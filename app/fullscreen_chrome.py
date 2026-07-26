from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QMainWindow,
    QMenuBar,
    QSlider,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)


CURSOR_IDLE_HIDE_MS = 800


class FullscreenChromeController(QObject):
    """Single reconciler for fullscreen chrome and window-local cursor state."""

    def __init__(
        self,
        window: QMainWindow,
        *,
        viewer: QWidget,
        menu_bar: QMenuBar,
        slider: QSlider,
        status_bar: QStatusBar,
        edge_trigger_px: int = 8,
        hide_delay_ms: int = 900,
        auto_reveal: bool = True,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.viewer = viewer
        self.menu_bar = menu_bar
        self.slider = slider
        self.status_bar = status_bar

        self.fullscreen = False
        self.hide_ui_enabled = True
        self.hide_cursor_enabled = False
        self.active = False  # Compatibility: fullscreen overlay ownership is active.
        self.auto_reveal = True
        self.edge_trigger_px = 8
        self.hide_delay_ms = 900
        self.top_overlay_visible = False
        self.bottom_overlay_visible = False
        self.pointer_in_top_trigger = False
        self.pointer_in_bottom_trigger = False
        self.pointer_in_top_overlay = False
        self.pointer_in_bottom_overlay = False
        self.menu_popup_or_modal_open = False
        self.slider_dragging = False
        self.mouse_button_down = False
        self.hide_timer_generation = 0
        self.cursor_hidden = False
        self._requested_overlay: str | None = None
        self._attached = False
        self._scheduled_hide_generation = 0
        self._cursor_timer_generation = 0
        self._scheduled_cursor_generation = 0

        parent = window.centralWidget()
        if parent is None:
            raise ValueError("FullscreenChromeController requires a central widget")
        self.overlay_parent = parent
        self.top_overlay = QFrame(parent)
        self.top_overlay.setObjectName("fullscreen_top_chrome")
        self.top_overlay.setFrameShape(QFrame.Shape.StyledPanel)
        self.top_overlay.setAcceptDrops(True)
        self._top_layout = QVBoxLayout(self.top_overlay)
        self._top_layout.setContentsMargins(0, 0, 0, 0)
        self._top_layout.setSpacing(0)
        self.fullscreen_menu_bar = QMenuBar(self.top_overlay)
        self._top_layout.addWidget(self.fullscreen_menu_bar)

        self.bottom_overlay = QFrame(parent)
        self.bottom_overlay.setObjectName("fullscreen_bottom_chrome")
        self.bottom_overlay.setFrameShape(QFrame.Shape.StyledPanel)
        self.bottom_overlay.setAcceptDrops(True)
        self._bottom_layout = QVBoxLayout(self.bottom_overlay)
        self._bottom_layout.setContentsMargins(4, 2, 4, 0)
        self._bottom_layout.setSpacing(0)
        self.fullscreen_status_bar = QStatusBar(self.bottom_overlay)
        self._bottom_layout.addWidget(self.fullscreen_status_bar)
        self.status_bar.messageChanged.connect(
            self.fullscreen_status_bar.showMessage
        )

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_if_idle)
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.setInterval(CURSOR_IDLE_HIDE_MS)
        self._cursor_timer.timeout.connect(self._hide_cursor_if_idle)

        self.slider.sliderPressed.connect(self._slider_pressed)
        self.slider.sliderReleased.connect(self._slider_released)
        self._filtered_widgets = (
            window,
            parent,
            viewer,
            self.top_overlay,
            self.bottom_overlay,
            menu_bar,
            slider,
            status_bar,
            self.fullscreen_menu_bar,
            self.fullscreen_status_bar,
        )
        for widget in self._filtered_widgets:
            widget.installEventFilter(self)
            widget.setMouseTracking(True)
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)

        self.configure(
            auto_reveal=auto_reveal,
            edge_trigger_px=edge_trigger_px,
            hide_delay_ms=hide_delay_ms,
        )
        self.hide_overlays()

    def configure(
        self,
        *,
        auto_reveal: bool,
        edge_trigger_px: int,
        hide_delay_ms: int,
    ) -> None:
        self.auto_reveal = bool(auto_reveal)
        self.edge_trigger_px = max(4, min(32, int(edge_trigger_px)))
        self.hide_delay_ms = max(0, min(3000, int(hide_delay_ms)))
        self._hide_timer.setInterval(self.hide_delay_ms)
        self.reconcile_state()

    def set_fullscreen_state(
        self,
        fullscreen: bool,
        *,
        hide_ui: bool,
        hide_cursor: bool,
    ) -> None:
        self.fullscreen = bool(fullscreen)
        self.active = self.fullscreen
        self.hide_ui_enabled = bool(hide_ui)
        self.hide_cursor_enabled = bool(hide_cursor)
        self._invalidate_hide_timer()
        self._invalidate_cursor_timer()
        if self.fullscreen:
            self._attach_chrome()
            self._update_geometry()
        else:
            self.hide_overlays()
            self._restore_chrome()
            self._set_cursor_hidden(False)
        self.reconcile_state()

    def set_active(self, active: bool) -> None:
        """Compatibility wrapper used by controller tests and older callers."""

        self.set_fullscreen_state(
            active,
            hide_ui=self.hide_ui_enabled,
            hide_cursor=self.hide_cursor_enabled,
        )

    def set_hide_ui_enabled(self, enabled: bool) -> None:
        self.hide_ui_enabled = bool(enabled)
        self._invalidate_hide_timer()
        self.reconcile_state()

    def set_hide_cursor_enabled(self, enabled: bool) -> None:
        self.hide_cursor_enabled = bool(enabled)
        self._invalidate_cursor_timer()
        self.reconcile_state()

    def process_pointer(self, global_position: QPoint) -> None:
        self._set_cursor_hidden(False)
        self._update_pointer_state(global_position)
        if (
            self.fullscreen
            and self.hide_ui_enabled
            and self.auto_reveal
        ):
            if self.pointer_in_top_trigger:
                self._requested_overlay = "top"
            elif self.pointer_in_bottom_trigger:
                self._requested_overlay = "bottom"
        self.reconcile_state()

    def reconcile_state(self) -> None:
        """Apply every fullscreen UI/cursor state transition through one path."""

        self._refresh_interaction_state()
        if not self.fullscreen:
            self.hide_overlays()
            self._set_cursor_hidden(False)
            return
        self._attach_chrome()
        self._update_geometry()

        if not self.hide_ui_enabled:
            self._show_both_overlays()
            self._invalidate_hide_timer()
        elif not self.auto_reveal:
            self.hide_overlays()
            self._invalidate_hide_timer()
        elif self.pointer_in_top_trigger or self.pointer_in_top_overlay:
            self._show_overlay("top")
        elif self.pointer_in_bottom_trigger or self.pointer_in_bottom_overlay:
            self._show_overlay("bottom")
        elif self._interaction_blocks_hide():
            self._invalidate_hide_timer()
        else:
            if self.top_overlay_visible or self.bottom_overlay_visible:
                self.schedule_hide()
            else:
                self._invalidate_hide_timer()

        if (
            not self.hide_cursor_enabled
            or self.top_overlay_visible
            or self.bottom_overlay_visible
            or self._interaction_blocks_hide()
        ):
            self._invalidate_cursor_timer()
            self._set_cursor_hidden(False)
        elif not self.cursor_hidden:
            self._schedule_cursor_hide()

    def show_top(self) -> None:
        if not self.fullscreen:
            return
        self._requested_overlay = "top"
        self._show_overlay("top")
        self._set_cursor_hidden(False)

    def show_bottom(self) -> None:
        if not self.fullscreen:
            return
        self._requested_overlay = "bottom"
        self._show_overlay("bottom")
        self._set_cursor_hidden(False)

    def hide_overlays(self) -> None:
        self.top_overlay.hide()
        self.bottom_overlay.hide()
        self.top_overlay_visible = False
        self.bottom_overlay_visible = False
        self._requested_overlay = None

    def schedule_hide(self) -> None:
        if not self.fullscreen or not self.hide_ui_enabled:
            return
        self.hide_timer_generation += 1
        self._scheduled_hide_generation = self.hide_timer_generation
        self._hide_timer.stop()
        self._hide_timer.start(self.hide_delay_ms)

    def reevaluate_visibility(self) -> None:
        self._invalidate_hide_timer()
        self._update_pointer_state(QCursor.pos())
        self.reconcile_state()

    def shutdown(self) -> None:
        self._invalidate_hide_timer()
        self._invalidate_cursor_timer()
        self._set_cursor_hidden(False)
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if not hasattr(self, "top_overlay"):
            return False
        event_type = event.type()
        if watched in self._filtered_widgets and event_type in {
            QEvent.Type.Resize,
            QEvent.Type.Move,
        }:
            self._update_geometry()
        if (
            self.fullscreen
            and event_type == QEvent.Type.MouseMove
            and isinstance(event, QMouseEvent)
        ):
            self.process_pointer(event.globalPosition().toPoint())
        elif watched in self._filtered_widgets:
            if event_type == QEvent.Type.MouseButtonPress:
                self.mouse_button_down = True
                self._set_cursor_hidden(False)
                self.reconcile_state()
            elif event_type == QEvent.Type.MouseButtonRelease:
                self.mouse_button_down = False
                self._update_pointer_state(QCursor.pos())
                self.reconcile_state()
        return False

    def _show_overlay(self, side: str) -> None:
        self._update_geometry()
        if side == "top":
            self.bottom_overlay.hide()
            self.top_overlay.show()
            self.top_overlay.raise_()
            self.top_overlay_visible = True
            self.bottom_overlay_visible = False
        else:
            self.top_overlay.hide()
            self.bottom_overlay.show()
            self.bottom_overlay.raise_()
            self.top_overlay_visible = False
            self.bottom_overlay_visible = True
        self._invalidate_hide_timer()

    def _show_both_overlays(self) -> None:
        self._update_geometry()
        self.top_overlay.show()
        self.bottom_overlay.show()
        self.top_overlay.raise_()
        self.bottom_overlay.raise_()
        self.top_overlay_visible = True
        self.bottom_overlay_visible = True

    def _attach_chrome(self) -> None:
        if self._attached:
            return
        for action in self.menu_bar.actions():
            if action not in self.fullscreen_menu_bar.actions():
                self.fullscreen_menu_bar.addAction(action)
        self.slider.setParent(self.bottom_overlay)
        self._bottom_layout.insertWidget(0, self.slider)
        self.menu_bar.hide()
        self.status_bar.hide()
        self.fullscreen_menu_bar.show()
        self.fullscreen_status_bar.showMessage(self.status_bar.currentMessage())
        self.fullscreen_status_bar.show()
        self.slider.show()
        self._attached = True

    def _restore_chrome(self) -> None:
        if not self._attached:
            return
        self._bottom_layout.removeWidget(self.slider)
        central_layout = self.overlay_parent.layout()
        if isinstance(central_layout, QVBoxLayout):
            central_layout.addWidget(self.slider, 0)
        self.fullscreen_menu_bar.hide()
        self.fullscreen_status_bar.hide()
        self.menu_bar.show()
        self.slider.show()
        self.status_bar.show()
        self._attached = False

    def _update_geometry(self) -> None:
        if not self.fullscreen:
            return
        width = max(1, self.overlay_parent.width())
        top_height = max(1, self.top_overlay.sizeHint().height())
        bottom_height = max(1, self.bottom_overlay.sizeHint().height())
        self.top_overlay.setGeometry(0, 0, width, top_height)
        self.bottom_overlay.setGeometry(
            0,
            max(0, self.overlay_parent.height() - bottom_height),
            width,
            bottom_height,
        )

    def _hide_if_idle(self) -> None:
        if self._scheduled_hide_generation != self.hide_timer_generation:
            return
        self._refresh_interaction_state()
        self._update_pointer_state(QCursor.pos())
        if (
            not self.fullscreen
            or not self.hide_ui_enabled
            or self._interaction_blocks_hide()
            or self._pointer_in_reveal_area(QCursor.pos())
        ):
            return
        self.hide_overlays()
        if self.hide_cursor_enabled and not self._interaction_blocks_hide():
            self._schedule_cursor_hide()

    def _schedule_cursor_hide(self) -> None:
        if not self.fullscreen or not self.hide_cursor_enabled:
            return
        self._cursor_timer_generation += 1
        self._scheduled_cursor_generation = self._cursor_timer_generation
        self._cursor_timer.stop()
        self._cursor_timer.start(CURSOR_IDLE_HIDE_MS)

    def _hide_cursor_if_idle(self) -> None:
        if self._scheduled_cursor_generation != self._cursor_timer_generation:
            return
        self._refresh_interaction_state()
        if (
            self.fullscreen
            and self.hide_cursor_enabled
            and not self.top_overlay_visible
            and not self.bottom_overlay_visible
            and not self._interaction_blocks_hide()
        ):
            self._set_cursor_hidden(True)

    def _set_cursor_hidden(self, hidden: bool) -> None:
        normalized = bool(hidden)
        if normalized == self.cursor_hidden:
            return
        for widget in (
            self.window,
            self.overlay_parent,
            self.viewer,
            self.top_overlay,
            self.bottom_overlay,
            self.fullscreen_menu_bar,
            self.fullscreen_status_bar,
        ):
            if normalized:
                widget.setCursor(QCursor(Qt.CursorShape.BlankCursor))
            else:
                widget.unsetCursor()
        self.cursor_hidden = normalized

    def _update_pointer_state(self, global_position: QPoint) -> None:
        local = self.overlay_parent.mapFromGlobal(global_position)
        inside = self.overlay_parent.rect().contains(local)
        self.pointer_in_top_trigger = bool(
            inside and 0 <= local.y() <= self.edge_trigger_px
        )
        self.pointer_in_bottom_trigger = bool(
            inside
            and self.overlay_parent.height() - self.edge_trigger_px - 1
            <= local.y()
            < self.overlay_parent.height()
        )
        self.pointer_in_top_overlay = bool(
            self.top_overlay.isVisible()
            and self.top_overlay.rect().contains(
                self.top_overlay.mapFromGlobal(global_position)
            )
        )
        self.pointer_in_bottom_overlay = bool(
            self.bottom_overlay.isVisible()
            and self.bottom_overlay.rect().contains(
                self.bottom_overlay.mapFromGlobal(global_position)
            )
        )

    def _pointer_in_reveal_area(self, global_position: QPoint) -> bool:
        self._update_pointer_state(global_position)
        return any(
            (
                self.pointer_in_top_trigger,
                self.pointer_in_bottom_trigger,
                self.pointer_in_top_overlay,
                self.pointer_in_bottom_overlay,
            )
        )

    def _refresh_interaction_state(self) -> None:
        self.slider_dragging = self.slider.isSliderDown()
        self.mouse_button_down = (
            QApplication.mouseButtons() != Qt.MouseButton.NoButton
        )
        popup = QApplication.activePopupWidget()
        modal = QApplication.activeModalWidget()
        self.menu_popup_or_modal_open = bool(
            popup is not None
            or (
                modal is not None
                and (modal is self.window or self.window.isAncestorOf(modal))
            )
        )

    def _interaction_blocks_hide(self) -> bool:
        return bool(
            self.mouse_button_down
            or self.slider_dragging
            or self.menu_popup_or_modal_open
        )

    def _invalidate_hide_timer(self) -> None:
        self.hide_timer_generation += 1
        self._scheduled_hide_generation = self.hide_timer_generation
        self._hide_timer.stop()

    def _invalidate_cursor_timer(self) -> None:
        self._cursor_timer_generation += 1
        self._scheduled_cursor_generation = self._cursor_timer_generation
        self._cursor_timer.stop()

    def _slider_pressed(self) -> None:
        self.slider_dragging = True
        self._set_cursor_hidden(False)
        self.reconcile_state()

    def _slider_released(self) -> None:
        self.slider_dragging = False
        self.mouse_button_down = False
        self.reconcile_state()

    def _cancel_hide(self) -> None:
        """Compatibility hook retained for older integrations."""

        self._invalidate_hide_timer()
