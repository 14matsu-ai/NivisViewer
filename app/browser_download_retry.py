"""Debounces changed sources and releases finite transient failure retries.

This object never reads source files, enumerates a directory, decodes images,
submits a worker, or publishes pixels. It gates changing sources and makes bounded failed identities eligible for the
existing foreground/background request owners again.
"""
from __future__ import annotations

from time import monotonic
from PySide6.QtCore import QObject, QTimer

from .browser_download_policy import DownloadRetryBudget
from .browser_model import BrowserItemKind


class BrowserDownloadRetry(QObject):
    def __init__(self, window, *, clock=monotonic) -> None:
        super().__init__(window)
        self.window = window
        self._clock = clock
        self._budget = DownloadRetryBudget()
        self._context = None
        self._generation = None
        self._changing = {}
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._retry_due)
        provider = window.thumbnail_provider
        provider.work_settled.connect(self._settled)
        provider.scheduling_resumed.connect(self.wake)
        provider.capacity_released.connect(self.wake)
        window.directory_scan_committed.connect(self.wake)
        window.file_operation_coordinator.operation_completed.connect(self.wake)

    def close(self) -> None:
        self._closed = True
        self._timer.stop()
        self._budget.clear()
        self._clear_changing()

    def reset(self) -> None:
        """Explicit Refresh or navigation opens a new bounded recovery window."""
        self._timer.stop()
        self._budget.clear()
        self._clear_changing()
        self._context = None

    def _sync_context(self) -> None:
        window = self.window
        context = (str(window.current_path), window.thumbnail_render_spec.cache_token)
        if context != self._context:
            self._timer.stop()
            self._budget.clear()
            self._clear_changing()
            self._context = context
        if self._generation != window._generation:
            self._generation = window._generation
            def eligible(key):
                item = self._eligible_item(*key)
                return item is not None and window.thumbnail_provider.can_retry_failed_thumbnail(item, window.thumbnail_render_spec)
            self._budget.resume_interrupted(window._generation, self._clock(), eligible)

    def _eligible_item(self, path, token, revision):
        window = self.window
        if int(token) != window.thumbnail_render_spec.cache_token:
            return None
        row = window.item_model.row_for_path(path)
        if row < 0:
            return None
        item = window.item_model.item_at(row)
        if (item is None or item.thumbnail_revision != tuple(revision)
                or not item.can_generate_preview
                or item.kind not in {BrowserItemKind.IMAGE, BrowserItemKind.ARCHIVE,
                                     BrowserItemKind.PDF, BrowserItemKind.FOLDER}):
            return None
        region = window._visible_row_range()
        if region is None:
            return None
        first, last = region
        span = max(1, last - first + 1)
        # Visible + one neighbouring viewport each way. This is a retry band,
        # not a cap on ordinary configured thumbnail generation/read-ahead.
        if not first - span <= row <= last + span:
            return None
        return item

    def _settled(self, path, generation, token, revision, state) -> None:
        window = self.window
        if self._closed or window._shutdown_prepared:
            return
        self._sync_context()
        if generation != window._generation:
            return
        key = (str(path), int(token), tuple(revision))
        if state == "ready":
            self._budget.resolved(key)
        elif state == "failed":
            item = self._eligible_item(*key)
            if item is not None and window.thumbnail_provider.can_retry_failed_thumbnail(item, window.thumbnail_render_spec):
                self._budget.failed(key, generation, self._clock())
            else:
                self._budget.discard_pending(key)
        self.wake()

    def _clear_changing(self):
        for item, _due in self._changing.values():
            self.window.thumbnail_provider.release_changing_item(item)
        self._changing.clear()

    def reconcile_changes(self, previous_items, items):
        """Debounce source changes regardless of previous decode success."""
        self._sync_context()
        window = self.window
        provider = window.thumbnail_provider
        previous = {provider._path_key(item.path): item for item in previous_items}
        current = {provider._path_key(item.path): item for item in items}
        token = window.thumbnail_render_spec.cache_token
        for path in tuple(self._changing):
            if path not in current:
                item, _due = self._changing.pop(path)
                provider.release_changing_item(item)
        for path, item in current.items():
            old = previous.get(path)
            if old is None or item.thumbnail_revision == old.thumbnail_revision:
                continue
            self._budget.resolved((str(old.path), token, old.thumbnail_revision))
            provider.defer_changing_item(item)
            self._changing[path] = (item, self._clock() + 0.5)

    def _deferred(self) -> bool:
        window = self.window
        provider = window.thumbnail_provider
        return bool(
            window._fast_scrolling
            or not window.isVisible()
            or window.isMinimized()
            or window._pending_scan is not None
            or window._directory_change_pending
            or window.file_operation_coordinator.busy
            or getattr(provider, "_paused", False)
        )

    def wake(self, *_args) -> None:
        if self._closed or self.window._shutdown_prepared:
            self._timer.stop()
            return
        self._sync_context()
        if self._deferred():
            self._timer.stop()
            return
        now = self._clock()
        delay = self._budget.next_delay_ms(now)
        if self._changing:
            quiet_delay = max(0, int((min(due for _, due in self._changing.values()) - now) * 1000) + 1)
            delay = quiet_delay if delay is None else min(delay, quiet_delay)
        if delay is None:
            self._timer.stop()
        elif not self._timer.isActive() or self._timer.remainingTime() > delay:
            self._timer.start(delay)

    def _retry_due(self) -> None:
        window = self.window
        if self._closed or window._shutdown_prepared:
            self.close()
            return
        self._sync_context()
        provider = window.thumbnail_provider
        if self._deferred():
            # No timer polling while hidden/busy. Existing scroll, scan,
            # operation and scheduling-resumed signals wake us. Time still
            # expires, so a permanently busy/hidden Browser cannot loop.
            return
        changed = False
        for path, (item, due) in tuple(self._changing.items()):
            if due > self._clock():
                continue
            self._changing.pop(path)
            provider.release_changing_item(item)
            self.window._browser_workflow.retry_failed_thumbnail(
                str(item.path), window.thumbnail_render_spec.cache_token, item.thumbnail_revision)
            changed = True
        for key, _generation in self._budget.take_due(self._clock()):
            # Directory refresh can advance generation without changing this file.
            generation = window._generation
            item = self._eligible_item(*key)
            if item is None or not provider.can_retry_failed_thumbnail(item, window.thumbnail_render_spec):
                continue
            if provider.clear_failed_thumbnail(item, window.thumbnail_render_spec,
                                               generation=generation, automatic=True):
                workflow = getattr(window, "_browser_workflow", None)
                if workflow is not None:
                    workflow.retry_failed_thumbnail(*key)
                changed = True
        if changed:
            window._schedule_thumbnail_requests(0)
        self.wake()
