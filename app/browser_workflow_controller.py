"""Browser-only glue; the existing operation and thumbnail lanes do the work."""
from __future__ import annotations

from PySide6.QtCore import QObject, QEvent, QTimer, Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QWidget

from .browser_model import BrowserItemKind
from .browser_workflow_policy import (
    SelectionAppearance, ThumbnailWarmupCursor, normalize_workflow_settings,
)
from .file_operation_service import FileOperationKind
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
        self._ime_composing = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._pump)
        model = window.item_model
        for signal in (model.modelReset, model.layoutChanged,
                       model.rowsInserted, model.rowsRemoved):
            signal.connect(self._model_changed)
        window.thumbnail_provider.work_settled.connect(self._settled)
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
        return True

    def _pump(self) -> None:
        window = self.window
        if (window._shutdown_prepared or window._fast_scrolling
                or not window.isVisible()
                or self.options["browser_thumbnail_background_screens"] == 0
                or not self._synchronize_cursor()):
            return
        provider = window.thumbnail_provider
        foreground_timer = getattr(window, "_thumbnail_request_timer", None)
        if (getattr(window, "_first_paint_pending_generation", None) is not None
                or (foreground_timer is not None and foreground_timer.isActive())):
            return
        if provider.pending_count:
            return  # Completion or resume, not a polling timer, continues.
        if self._inflight is not None:
            # A queued speculative request can be removed by visible-only
            # reprioritization without a worker callback. Do not strand it.
            self._cursor.retry(self._inflight[0])
            self._inflight = None
        for _ in range(32):
            row = self._cursor.take()
            if row is None:
                if not self._cursor.exhausted:
                    self.schedule_background()
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
                return
            self._cursor.complete(row)
        self.schedule_background()  # Bounded cached/unsupported scan per tick.

    def _settled(self, path, generation, token, revision, state) -> None:
        current = self._inflight
        if current is not None and current[1:5] == (path, generation, token, revision):
            row, _path, _generation, _token, _revision, context = current
            self._inflight = None
            if context == self._context and self._cursor is not None:
                if state == "cancelled":
                    self._cursor.retry(row)
                else:
                    self._cursor.complete(row)
            # A running low-priority request may become visible without Qt
            # allowing queue promotion. Reuse its now-complete memory entry.
            region = self.window._visible_row_range()
            if region is not None and region[0] <= row <= region[1]:
                self.window._schedule_thumbnail_requests(0)
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
        # Viewer-in-use confirmation and the existing operation queue remain
        # authoritative; a new executor/thread pool is deliberately not added.
        return window._start_file_operation(
            FileOperationKind.UNDO, sources=tuple(entry.path for entry in entries))
