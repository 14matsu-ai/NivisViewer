"""Releases transient failure memos; the existing Browser lanes do all work.

This object never reads source files, enumerates a directory, decodes images,
submits a worker, or publishes pixels. It only makes a bounded failed identity
eligible for the existing foreground/background request owners again.
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

    def reset(self) -> None:
        """Explicit Refresh or navigation opens a new bounded recovery window."""
        self._timer.stop()
        self._budget.clear()
        self._context = None

    def _sync_context(self) -> None:
        window = self.window
        context = (str(window.current_path), window.thumbnail_render_spec.cache_token)
        if context != self._context:
            self._timer.stop()
            self._budget.clear()
            self._context = context

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
        elif state == "failed" and self._eligible_item(*key) is not None:
            self._budget.failed(key, generation, self._clock())
        self.wake()

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
        delay = self._budget.next_delay_ms(self._clock())
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
        for key, generation in self._budget.take_due(self._clock()):
            if generation != window._generation:
                continue
            item = self._eligible_item(*key)
            if item is None:
                continue
            if provider.clear_failed_thumbnail(item, window.thumbnail_render_spec,
                                               generation=generation):
                workflow = getattr(window, "_browser_workflow", None)
                if workflow is not None:
                    workflow.retry_failed_thumbnail(*key)
                changed = True
        if changed:
            window._schedule_thumbnail_requests(0)
        self.wake()
