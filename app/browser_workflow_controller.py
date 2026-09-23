"""Browser-only glue; the existing operation and thumbnail lanes do the work."""
from __future__ import annotations

from math import ceil

from PySide6.QtCore import QObject, QEvent, QTimer, Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from .browser_model import BrowserItemKind
from .browser_workflow_policy import (
    SelectionAppearance, ThumbnailWarmupCursor, normalize_workflow_settings,
)
from .file_operation_service import FileOperationKind
from .i18n import tr
from .shortcut_catalog import canonical_key, normalize_shortcut_bindings


class BrowserWorkflowController(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        # Route the edit through the existing BrowserWindow keyboard owner.
        # No application-global or second controller event filter is added.
        window.browser_search_edit.installEventFilter(window)
        self.options = normalize_workflow_settings(window.config.data)
        self._model_revision = 0
        self._context = None
        self._viewport = None
        self._cursor = None
        self._inflight = None
        self._near_rows: set[int] = set()
        self._next_rows: set[int] = set()
        self._near_cache_blocked = False
        self._stop_reason = "idle"
        self._ime_composing = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._pump)
        model = window.item_model
        for signal in (model.modelReset, model.layoutChanged,
                       model.rowsInserted, model.rowsRemoved):
            signal.connect(self._model_changed)
        model.dataChanged.connect(self._item_data_changed)
        window.thumbnail_provider.work_settled.connect(self._settled)
        window.thumbnail_provider.capacity_released.connect(self.schedule_background)
        window.thumbnail_provider.submission_rejected.connect(self._submission_rejected)
        window.thumbnail_provider.scheduling_resumed.connect(self._resumed)
        window.thumbnail_provider.cache_cleared.connect(self._model_changed)
        window.config.settings_changed.connect(self._settings_changed)
        self._apply_appearance()

    def _settings_changed(self, _changes=None) -> None:
        options = normalize_workflow_settings(self.window.config.data)
        if options["browser_thumbnail_background_screens"] != self.options["browser_thumbnail_background_screens"]:
            self._model_changed()
        self.options = options
        self._apply_appearance()

    def _apply_appearance(self) -> None:
        self.window.item_delegate.selection_appearance = SelectionAppearance.from_settings(self.options)
        self.window.list_view.viewport().update()

    def _model_changed(self, *_args) -> None:
        self._model_revision += 1
        self._context = None
        self._cursor = None
        self._inflight = None
        self._near_rows.clear()
        self._next_rows.clear()
        self._near_cache_blocked = False
        self._stop_reason = "model-changed"
        self.schedule_background()

    def _item_data_changed(self, top_left, bottom_right, roles=()) -> None:
        model = self.window.item_model
        item_role = getattr(model, "ItemRole", None)
        if roles and item_role is not None and item_role not in roles:
            return
        if self._cursor is None:
            self.schedule_background()
            return
        first = max(0, int(top_left.row()))
        last = min(self._cursor.count - 1, int(bottom_right.row()))
        for row in range(first, last + 1):
            self._cursor.invalidate(row)
        self._viewport = None
        self._near_cache_blocked = False
        self.schedule_background()

    def _resumed(self) -> None:
        if self._inflight is not None and self._cursor is not None:
            self._cursor.retry(self._inflight[0])
        self._inflight = None
        self.schedule_background()

    def schedule_background(self) -> None:
        if not self.window._shutdown_prepared:
            # Yield until the ordinary visible-request pass has populated its
            # higher-priority queue. This is a one-shot, not an idle poller.
            self._timer.start(0)

    def _synchronize_cursor(self) -> bool:
        window = self.window
        count = window.item_model.rowCount()
        region = window._visible_row_range()
        if count <= 0 or region is None:
            return False
        token = window.thumbnail_render_spec.cache_token
        context = (window._generation, token, self._model_revision, count)
        first, last = region
        viewport = (first, last, window._thumbnail_scroll_direction,
                    self.options["browser_thumbnail_background_screens"])
        if context != self._context:
            self._context = context
            self._cursor = ThumbnailWarmupCursor(count)
            self._viewport = None
            self._inflight = None
        if viewport != self._viewport:
            self._cursor.recenter(*viewport)
            self._viewport = viewport
            self._near_cache_blocked = False
            self._update_cache_retention(first, last, viewport[2], viewport[3])
            self._stop_reason = "recentered"
        return True

    def recenter_cache_retention(self) -> None:
        """Apply the latest viewport priority before visible requests enter LRU."""
        if not self.window._shutdown_prepared:
            previous = (self._context, self._viewport)
            if not self._synchronize_cursor():
                return
            if previous == (self._context, self._viewport) and self._viewport is not None:
                first, last, direction, screens = self._viewport
                self._update_cache_retention(
                    first, last, direction, screens, reopen_missing=False
                )

    def _update_cache_retention(
        self,
        first: int,
        last: int,
        direction: int,
        screens: int,
        *,
        reopen_missing: bool = True,
    ) -> None:
        window = self.window
        count = window.item_model.rowCount()
        span = max(1, last - first + 1)
        direction = -1 if int(direction) < 0 else 1
        try:
            selection_model = window.list_view.selectionModel()
            selected_rows = sorted(
                {index.row() for index in selection_model.selectedIndexes()}
            )
        except (AttributeError, RuntimeError):
            selected_rows = []

        retained: dict[int, int] = {}

        def add_rows(rows, rank: int) -> None:
            for row in rows:
                if 0 <= row < count:
                    retained[row] = min(rank, retained.get(row, rank))

        near_rows: list[int] = []
        next_rows: list[int] = []
        safety_rows: list[int] = []
        if screens != 0:
            next_count = span
            safety_count = ceil(span * 0.25)
            if direction > 0:
                next_rows = list(range(last + 1, min(count, last + 1 + next_count)))
                safety_rows = list(range(first - 1, max(-1, first - 1 - safety_count), -1))
            else:
                next_rows = list(range(first - 1, max(-1, first - 1 - next_count), -1))
                safety_rows = list(range(last + 1, min(count, last + 1 + safety_count)))
            near_rows = next_rows + safety_rows
        self._next_rows = set(next_rows)
        self._near_rows = set(near_rows)

        provider = window.thumbnail_provider
        base_rows = set(range(first, last + 1)) | set(next_rows) | set(safety_rows)
        selected_capacity = max(
            0,
            int(getattr(provider, "memory_cache_capacity_entries", 128))
            - len(base_rows),
        )
        selected_outside = [row for row in selected_rows if row not in base_rows]
        add_rows(selected_outside[:selected_capacity], 0)
        add_rows(range(first, last + 1), 0)
        add_rows(next_rows, 1)
        add_rows(safety_rows, 2)

        prioritize = getattr(provider, "set_cache_retention_priorities", None)
        if callable(prioritize):
            entries = []
            for row, rank in retained.items():
                item = window.item_model.item_at(row)
                if item is not None and item.can_generate_preview:
                    entries.append((item.path, item.thumbnail_revision, rank))
            prioritize(entries)

        has_memory = getattr(provider, "has_memory_thumbnail", None)
        if reopen_missing and self._cursor is not None and callable(has_memory):
            missing_near = []
            for row in near_rows:
                if not self._cursor.eligible(row):
                    continue
                item = window.item_model.item_at(row)
                if (item is not None and item.can_generate_preview
                        and item.kind in {BrowserItemKind.IMAGE, BrowserItemKind.FOLDER,
                                          BrowserItemKind.ARCHIVE, BrowserItemKind.PDF}
                        and not has_memory(item, window.thumbnail_render_spec)):
                    missing_near.append(row)
            self._cursor.reopen(missing_near)

    def _next_band_ready_at_capacity(self) -> bool:
        if not self._next_rows:
            return False
        window = self.window
        provider = window.thumbnail_provider
        has_memory = getattr(provider, "has_memory_thumbnail", None)
        at_capacity = getattr(provider, "memory_cache_capacity_reached", None)
        if not callable(has_memory) or at_capacity is None:
            return False
        evictable = bool(
            getattr(provider, "memory_cache_has_evictable_entries", False)
        )
        disk_enabled = bool(
            getattr(provider, "background_disk_cache_enabled", False)
        )
        required = []
        for row in self._next_rows:
            item = window.item_model.item_at(row)
            if (item is not None and item.can_generate_preview
                    and item.kind in {BrowserItemKind.IMAGE, BrowserItemKind.FOLDER,
                                      BrowserItemKind.ARCHIVE, BrowserItemKind.PDF}):
                required.append(item)
        if not required or not all(
            has_memory(item, window.thumbnail_render_spec) for item in required
        ):
            return False
        # A full cache is not a reason to truncate the configured horizon if
        # stale/distant entries can roll out, or if results can be persisted
        # and consumed from disk. Stop only when the current+next band occupies
        # all RAM and neither route can advance farther work.
        return bool(at_capacity) and not evictable and not disk_enabled

    @property
    def stop_reason(self) -> str:
        """Current one-shot scheduling state, useful for diagnostics/tests."""
        return self._stop_reason

    def _pump(self) -> None:
        window = self.window
        if window._shutdown_prepared:
            self._stop_reason = "shutdown"
            return
        if window._fast_scrolling:
            self._stop_reason = "fast-scroll-idle-boundary"
            return
        if not window.isVisible():
            self._stop_reason = "browser-hidden"
            return
        if not self._synchronize_cursor():
            self._stop_reason = "no-visible-rows"
            return
        if self.options["browser_thumbnail_background_screens"] == 0:
            self._stop_reason = "disabled"
            return
        if self._near_cache_blocked:
            self._stop_reason = "near-cache-capacity"
            return
        provider = window.thumbnail_provider
        foreground_timer = getattr(window, "_thumbnail_request_timer", None)
        if (getattr(window, "_first_paint_pending_generation", None) is not None
                or (foreground_timer is not None and foreground_timer.isActive())):
            self._stop_reason = "foreground-pending"
            return
        if provider.pending_count:
            self._stop_reason = "provider-busy"
            return  # Completion or resume, not a polling timer, continues.
        if getattr(provider, "_paused", False):
            self._stop_reason = "provider-paused"
            return
        if self._inflight is not None:
            # A queued speculative request can be removed by visible-only
            # reprioritization without a worker callback. Do not strand it.
            self._cursor.retry(self._inflight[0])
            self._inflight = None
        for _ in range(32):
            row = self._cursor.take()
            if row is None:
                if not self._cursor.exhausted:
                    self._stop_reason = "scan-yield"
                    self.schedule_background()
                else:
                    self._stop_reason = "range-complete"
                return
            if self._next_band_ready_at_capacity():
                self._cursor.retry(row)
                self._stop_reason = "far-cache-capacity"
                return
            item = window.item_model.item_at(row)
            if (item is None or not item.can_generate_preview
                    or item.kind not in {BrowserItemKind.IMAGE, BrowserItemKind.FOLDER,
                                         BrowserItemKind.ARCHIVE, BrowserItemKind.PDF}):
                self._cursor.complete(row)
                continue
            key = (row, str(item.path), window._generation,
                   window.thumbnail_render_spec.cache_token, item.thumbnail_revision,
                   self._context)
            self._inflight = key
            state = provider.request_background(item, window.thumbnail_render_spec,
                                                generation=window._generation)
            if state == "queued":
                return  # At most ONE speculative request beyond the near plan.
            self._inflight = None
            if state == "blocked":
                self._cursor.retry(row)
                self._stop_reason = (
                    "provider-paused" if getattr(provider, "_paused", False)
                    else "provider-busy"
                )
                return
            self._cursor.complete(row)
        self._stop_reason = "scan-yield"
        self.schedule_background()  # Bounded cached/unsupported scan per tick.

    def _settled(self, path, generation, token, revision, state) -> None:
        current = self._inflight
        if current is not None and current[1:5] == (path, generation, token, revision):
            row, _path, _generation, _token, _revision, context = current
            self._inflight = None
            if context == self._context and self._cursor is not None:
                item = self.window.item_model.item_at(row)
                item_changed = (
                    item is None
                    or str(item.path) != path
                    or item.thumbnail_revision != _revision
                )
                if state == "cancelled" or item_changed:
                    self._cursor.retry(row)
                else:
                    self._cursor.complete(row)
                    has_memory = getattr(
                        self.window.thumbnail_provider,
                        "has_memory_thumbnail",
                        None,
                    )
                    if (state == "ready" and row in self._next_rows
                            and item is not None and callable(has_memory)
                            and not has_memory(item, self.window.thumbnail_render_spec)):
                        # One terminal attempt is enough for this viewport. A
                        # too-small cache must stop normally, never resubmit an
                        # image that cannot fit and evict itself forever.
                        self._near_cache_blocked = True
                        self._stop_reason = "near-cache-capacity"
            # A running low-priority request may become visible without Qt
            # allowing queue promotion. Reuse its now-complete memory entry.
            region = self.window._visible_row_range()
            if region is not None and region[0] <= row <= region[1]:
                self.window._schedule_thumbnail_requests(0)
        self.schedule_background()

    def _submission_rejected(self, path, generation, token, revision) -> None:
        current = self._inflight
        if current is not None and current[1:5] == (
            path, generation, token, revision
        ):
            if self._cursor is not None:
                self._cursor.retry(current[0])
            self._inflight = None
            self._stop_reason = "submission-rejected"
            return
        if current is None:
            self.schedule_background()

    def handle_key(self, watched, event) -> bool:
        window = self.window
        if watched is window.browser_search_edit:
            if isinstance(event, QInputMethodEvent):
                self._ime_composing = bool(event.preeditString())
                return False
            if event.type() == QEvent.Type.FocusOut:
                self._ime_composing = False
        if self._ime_composing:
            return False
        if (window._shutdown_prepared or not isinstance(watched, QWidget)
                or watched.window() is not window or not isinstance(event, QKeyEvent)
                or event.type() not in {QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress}
                or QApplication.activeModalWidget() is not None
                or QApplication.activePopupWidget() is not None):
            return False
        key = canonical_key(QKeySequence(int(event.key()) | int(event.modifiers().value)))
        bindings = normalize_shortcut_bindings(window.config.data.get("shortcut_bindings"))["browser"]
        action = None
        if key in bindings["browser_focus_search"]:
            action = self.focus_search
        elif key in bindings["browser_undo"]:
            focus = QApplication.focusWidget()
            if (window._is_browser_editing_surface(watched)
                    or (focus is not None and window._is_browser_editing_surface(focus))):
                return False  # Native text-edit undo always keeps ownership.
            action = self.undo
        elif watched is window.browser_search_edit and key in {"Down", "Up", "Alt+Down", "F4"}:
            # Reuse the existing MRU popup and its selection/clear/persistence.
            action = window._show_search_history_popup
        if action is None:
            return False
        event.accept()
        if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
            action()
        return True

    def focus_search(self) -> None:
        self.window.browser_search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.window.browser_search_edit.selectAll()

    def undo(self) -> bool:
        window = self.window
        coordinator = window.file_operation_coordinator
        entries = coordinator.undo_entries
        if coordinator.busy or not entries or window._rating_batch is not None:
            return False
        if len(entries) > 512:
            answer = QMessageBox.question(
                window,
                tr("大量の操作を元に戻す"),
                tr(
                    "{count}項目を元に戻します。\n"
                    "操作後に変更された項目はスキップされる場合があります。"
                    "処理をキャンセルすると一部だけ元に戻ることがあります。\n"
                    "続行しますか？",
                    count=len(entries),
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        # Viewer-in-use confirmation and the existing operation queue remain
        # authoritative; a new executor/thread pool is deliberately not added.
        return window._start_file_operation(
            FileOperationKind.UNDO, sources=tuple(entry.path for entry in entries))
