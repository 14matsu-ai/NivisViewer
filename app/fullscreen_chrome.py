from __future__ import annotations

import ctypes
import logging
import sys
from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QEvent, QObject, QPoint, QRect, QTimer, Qt
from PySide6.QtGui import (
    QCursor,
    QGuiApplication,
    QMouseEvent,
    QPalette,
    QScreen,
    QWheelEvent,
    QWindow,
)
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

from app.viewer_page_slider import ViewerPageSlider
from app.windows_fullscreen import WindowsFullscreenAdapter
from app.menu_icons import install_text_icon_menu_style


CURSOR_IDLE_HIDE_MS = 800
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DEFAULT = 0
_DWMWCP_DONOTROUND = 1
_DWMWA_BORDER_COLOR = 34
_DWM_COLOR_DEFAULT = 0xFFFFFFFF


def _is_native_windows_platform() -> bool:
    return bool(
        sys.platform == "win32"
        and QGuiApplication.platformName().lower() == "windows"
    )


@dataclass(frozen=True)
class _FullscreenRestoreState:
    window_flags: Qt.WindowType
    normal_geometry: QRect
    saved_geometry: QByteArray
    screen: QScreen | None
    was_maximized: bool


class FullscreenChromeController(QObject):
    """Single reconciler for true-fullscreen state, chrome and cursor state."""

    def __init__(
        self,
        window: QMainWindow,
        *,
        viewer: QWidget,
        menu_bar: QMenuBar,
        slider: QSlider,
        status_bar: QStatusBar,
        edge_trigger_px: int = 8,
        top_edge_trigger_px: int | None = None,
        bottom_edge_trigger_px: int = 28,
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
        self.top_edge_trigger_px = 8
        self.bottom_edge_trigger_px = 28
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
        self._last_bottom_overlay_height = 1
        self._fullscreen_restore_state: _FullscreenRestoreState | None = None

        parent = window.centralWidget()
        if parent is None:
            raise ValueError("FullscreenChromeController requires a central widget")
        self.overlay_parent = parent
        self.overlay_parent.setAutoFillBackground(True)
        self.top_overlay = QFrame(parent)
        self.top_overlay.setObjectName("fullscreen_top_chrome")
        self.top_overlay.setFrameShape(QFrame.Shape.NoFrame)
        self.top_overlay.setAutoFillBackground(True)
        self.top_overlay.setAcceptDrops(True)
        self._top_layout = QVBoxLayout(self.top_overlay)
        self._top_layout.setContentsMargins(0, 0, 0, 0)
        self._top_layout.setSpacing(0)
        self.fullscreen_menu_bar = QMenuBar(self.top_overlay)
        install_text_icon_menu_style(self.fullscreen_menu_bar)
        self._top_layout.addWidget(self.fullscreen_menu_bar)

        self.bottom_overlay = QFrame(parent)
        self.bottom_overlay.setObjectName("fullscreen_bottom_chrome")
        self.bottom_overlay.setFrameShape(QFrame.Shape.NoFrame)
        self.bottom_overlay.setAutoFillBackground(True)
        self.bottom_overlay.setAcceptDrops(True)
        self._bottom_layout = QVBoxLayout(self.bottom_overlay)
        self._bottom_layout.setContentsMargins(4, 2, 4, 0)
        self._bottom_layout.setSpacing(0)
        self.fullscreen_status_bar = QStatusBar(self.bottom_overlay)
        self._bottom_layout.addWidget(self.fullscreen_status_bar)
        self.bottom_reveal_strip = QWidget(parent)
        self.bottom_reveal_strip.setObjectName("fullscreen_bottom_reveal_strip")
        self.bottom_reveal_strip.setMouseTracking(True)
        self.bottom_reveal_strip.setAttribute(
            Qt.WidgetAttribute.WA_NoSystemBackground,
            True,
        )
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
            self.bottom_reveal_strip,
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
            top_edge_trigger_px=top_edge_trigger_px,
            bottom_edge_trigger_px=bottom_edge_trigger_px,
            hide_delay_ms=hide_delay_ms,
        )
        self.hide_overlays()
        self.bottom_reveal_strip.hide()

    def enter_true_fullscreen(self) -> None:
        """Enter a full-monitor borderless state through a neutral state.

        On Windows, changing a maximized decorated HWND directly to fullscreen
        can leave the shell's working-area maximization/taskbar relationship
        attached to the window.  Capture restoration state, hide that
        intermediate transition, remove maximization, then create the
        frameless fullscreen surface on the same monitor.
        """

        if (
            self.window.isFullScreen()
            or self._fullscreen_restore_state is not None
        ):
            return
        screen = self.window.screen()
        normal_geometry = QRect(self.window.normalGeometry())
        if not normal_geometry.isValid() or normal_geometry.isEmpty():
            normal_geometry = QRect(self.window.geometry())
        self._fullscreen_restore_state = _FullscreenRestoreState(
            window_flags=self.window.windowFlags(),
            normal_geometry=normal_geometry,
            saved_geometry=QByteArray(self.window.saveGeometry()),
            screen=screen,
            was_maximized=self.window.isMaximized(),
        )
        target_geometry = (
            QRect(screen.geometry())
            if screen is not None
            else QRect(self.window.geometry())
        )
        native_monitor = self._capture_native_fullscreen_monitor()

        # setWindowFlags recreates the native surface and hides it.  Hide an
        # already visible maximized window first so neither the neutral state
        # nor the working-area bounds are presented to the user.
        if self.window.isVisible() and self.window.isMaximized():
            self.window.hide()
        self.window.setWindowState(Qt.WindowState.WindowNoState)
        self.window.setWindowFlags(
            self._fullscreen_restore_state.window_flags
            | Qt.WindowType.FramelessWindowHint
        )
        self._set_target_screen(screen)
        self.window.setGeometry(target_geometry)
        if _is_native_windows_platform():
            # Keep Win32's maximized state on a borderless HWND. This matches
            # the shell-recognized transition used by ZipPla; Qt's separate
            # WindowFullScreen state does not set WS_MAXIMIZE.
            self.window.showMaximized()
        else:
            self.window.showFullScreen()
        # The explicit full-monitor projection prevents stale Qt work-area
        # bounds from leaving the native taskbar edge uncovered.
        self.window.setGeometry(target_geometry)
        self._apply_native_fullscreen_bounds(native_monitor)

    @property
    def owns_true_fullscreen_transition(self) -> bool:
        return self._fullscreen_restore_state is not None

    def leave_true_fullscreen(self) -> None:
        """Restore the exact pre-fullscreen flags, monitor and normal state."""

        restore = self._fullscreen_restore_state
        if restore is None:
            if self.window.isFullScreen():
                self.window.showNormal()
            return

        self.window.hide()
        self.window.setWindowState(Qt.WindowState.WindowNoState)
        self.window.setWindowFlags(restore.window_flags)
        self._set_target_screen(restore.screen)
        self.window.setGeometry(restore.normal_geometry)
        if restore.was_maximized:
            self.window.showMaximized()
        else:
            self.window.showNormal()
            self.window.setGeometry(restore.normal_geometry)
        self._fullscreen_restore_state = None

    def standard_window_geometry(self) -> QByteArray:
        """Return geometry for persistence without saving fullscreen bounds."""

        restore = self._fullscreen_restore_state
        if restore is not None:
            return QByteArray(restore.saved_geometry)
        return QByteArray(self.window.saveGeometry())

    def _set_target_screen(self, screen: QScreen | None) -> None:
        if screen is None:
            return
        window_handle = self.window.windowHandle()
        if window_handle is not None and window_handle.screen() is not screen:
            window_handle.setScreen(screen)

    def _capture_native_fullscreen_monitor(self) -> int | None:
        if not _is_native_windows_platform():
            return None
        try:
            return WindowsFullscreenAdapter().monitor_for_window(self._native_window_id())
        except (AttributeError, OSError):
            return None

    def _apply_native_fullscreen_bounds(self, monitor: int | None) -> None:
        """Project full-monitor bounds onto the borderless native window.

        On Windows the caller has already entered Qt's maximized state. Keep
        that OS-visible state while correcting the bounds for mixed-DPI
        monitors; z-order is left to the foreground window and Windows shell.
        """

        if not _is_native_windows_platform() or not self.window.isVisible():
            return
        if monitor is None:
            return
        try:
            applied = WindowsFullscreenAdapter().apply(
                self._native_window_id(), monitor,
            )
        except (AttributeError, OSError):
            applied = False
        if not applied:
            logging.getLogger(__name__).warning(
                "Native fullscreen monitor correction unavailable; retaining Qt bounds"
            )

    def _native_window_id(self) -> int:
        return int(self.window.winId())

    def configure(
        self,
        *,
        auto_reveal: bool,
        edge_trigger_px: int | None = None,
        top_edge_trigger_px: int | None = None,
        bottom_edge_trigger_px: int | None = None,
        hide_delay_ms: int,
    ) -> None:
        self.auto_reveal = bool(auto_reveal)
        top_value = (
            top_edge_trigger_px
            if top_edge_trigger_px is not None
            else edge_trigger_px
        )
        if top_value is None:
            top_value = self.top_edge_trigger_px
        bottom_value = (
            bottom_edge_trigger_px
            if bottom_edge_trigger_px is not None
            else self.bottom_edge_trigger_px
        )
        self.top_edge_trigger_px = max(4, min(32, int(top_value)))
        self.bottom_edge_trigger_px = max(12, min(64, int(bottom_value)))
        self.edge_trigger_px = self.top_edge_trigger_px
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
        self._apply_native_fullscreen_frame(self.fullscreen)
        if self.fullscreen:
            self._attach_chrome()
            self._update_geometry()
            self._update_reveal_strip_visibility()
        else:
            self._reset_bottom_wheel_accumulator()
            self.bottom_reveal_strip.hide()
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
        self._update_reveal_strip_visibility()

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

    def is_edge_trigger(self, global_position: QPoint) -> bool:
        self._update_pointer_state(global_position)
        return bool(
            self.pointer_in_top_trigger
            or self.pointer_in_bottom_trigger
            or self.pointer_in_bottom_overlay
        )

    def schedule_hide(self) -> None:
        if not self.fullscreen or not self.hide_ui_enabled:
            return
        self.hide_timer_generation += 1
        self._scheduled_hide_generation = self.hide_timer_generation
        self._hide_timer.stop()
        self._hide_timer.start(self.hide_delay_ms)

    def reevaluate_visibility(self) -> None:
        self._invalidate_hide_timer()
        self._apply_native_fullscreen_frame(self.fullscreen)
        cursor_position = QCursor.pos()
        if (
            (self.top_overlay_visible or self.bottom_overlay_visible)
            and not self._pointer_in_reveal_area(cursor_position)
        ):
            self.schedule_hide()
            return
        self._update_pointer_state(cursor_position)
        self.reconcile_state()

    def shutdown(self) -> None:
        self._invalidate_hide_timer()
        self._invalidate_cursor_timer()
        self._reset_bottom_wheel_accumulator()
        self._set_cursor_hidden(False)
        self._apply_native_fullscreen_frame(False)
        self.bottom_reveal_strip.hide()
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
            global_position = event.globalPosition().toPoint()
            self.process_pointer(global_position)
            if not self._pointer_in_bottom_hover_region(global_position):
                self._reset_bottom_wheel_accumulator()
        elif (
            self.fullscreen
            and event_type == QEvent.Type.Wheel
            and isinstance(event, QWheelEvent)
        ):
            if (
                self._is_bottom_wheel_target(watched, event)
                and isinstance(self.slider, ViewerPageSlider)
            ):
                self.slider.wheelInputObserved.emit(int(event.timestamp()))
                if event.isEndEvent():
                    handled = self.slider.process_wheel_delta(
                        event.angleDelta(),
                        event.pixelDelta(),
                        sequence_finished=True,
                    )
                else:
                    handled = self.slider.process_wheel_delta(
                        event.angleDelta(),
                        event.pixelDelta(),
                    )
                if handled:
                    event.accept()
                return handled
            if not self._is_slider_wheel_target(watched):
                self._reset_bottom_wheel_accumulator()
            if watched is self.bottom_reveal_strip:
                self.show_bottom()
                return True
        elif watched in self._filtered_widgets:
            if event_type == QEvent.Type.MouseButtonPress:
                self.mouse_button_down = True
                self._set_cursor_hidden(False)
                self.reconcile_state()
            elif event_type == QEvent.Type.MouseButtonRelease:
                self.mouse_button_down = False
                self._update_pointer_state(QCursor.pos())
                self.reconcile_state()
            elif (
                watched is self.bottom_reveal_strip
                and event_type == QEvent.Type.Wheel
            ):
                self.show_bottom()
                return True
        if (
            watched is self.bottom_reveal_strip
            and event_type
            in {
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick,
            }
        ):
            return True
        return False

    def _reset_bottom_wheel_accumulator(self) -> None:
        if isinstance(self.slider, ViewerPageSlider):
            self.slider.reset_wheel_accumulator()

    def _is_slider_wheel_target(self, watched: QObject) -> bool:
        return bool(
            isinstance(watched, QWidget)
            and (
                watched is self.slider
                or self.slider.isAncestorOf(watched)
            )
        )

    def _is_bottom_wheel_target(
        self,
        watched: QObject,
        event: QWheelEvent,
    ) -> bool:
        if not self.bottom_overlay.isVisible():
            return False
        if isinstance(watched, QWidget):
            if self._is_slider_wheel_target(watched):
                return False
            return bool(
                watched is self.bottom_overlay
                or self.bottom_overlay.isAncestorOf(watched)
            )
        if isinstance(watched, QWindow):
            window_handle = self.window.windowHandle()
            return bool(
                watched is window_handle
                and self._bottom_wheel_region_global().contains(
                    event.globalPosition().toPoint()
                )
            )
        return False

    def _apply_native_fullscreen_frame(self, fullscreen: bool) -> None:
        if sys.platform != "win32" or not self.window.isVisible():
            return
        try:
            dwmapi = ctypes.windll.dwmapi
        except (AttributeError, OSError):
            return

        window_id = int(self.window.winId())
        corner_preference = ctypes.c_int(
            _DWMWCP_DONOTROUND if fullscreen else _DWMWCP_DEFAULT
        )
        if fullscreen:
            color = self.top_overlay.palette().color(
                QPalette.ColorRole.Window
            )
            border_value = (
                color.red()
                | (color.green() << 8)
                | (color.blue() << 16)
            )
        else:
            border_value = _DWM_COLOR_DEFAULT
        border_color = ctypes.c_uint32(border_value)
        dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(window_id),
            _DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(corner_preference),
            ctypes.sizeof(corner_preference),
        )
        dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(window_id),
            _DWMWA_BORDER_COLOR,
            ctypes.byref(border_color),
            ctypes.sizeof(border_color),
        )

    def _show_overlay(self, side: str) -> None:
        self._update_geometry()
        if side == "top":
            if (
                self.top_overlay_visible
                and self.top_overlay.isVisible()
                and not self.bottom_overlay_visible
            ):
                self._requested_overlay = "top"
                self._invalidate_hide_timer()
                return
            self.bottom_overlay.hide()
            self.top_overlay.show()
            self.top_overlay.raise_()
            self.top_overlay_visible = True
            self.bottom_overlay_visible = False
        else:
            if (
                self.bottom_overlay_visible
                and self.bottom_overlay.isVisible()
                and not self.top_overlay_visible
            ):
                self._requested_overlay = "bottom"
                self._invalidate_hide_timer()
                return
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
        parent_rect = self.overlay_parent.rect()
        width = max(1, parent_rect.width())
        top_height = min(
            max(1, self.top_overlay.sizeHint().height()),
            max(1, parent_rect.height()),
        )
        bottom_height = min(
            max(1, self.bottom_overlay.sizeHint().height()),
            max(1, parent_rect.height()),
        )
        self._last_bottom_overlay_height = bottom_height
        top_geometry = QRect(
            parent_rect.left(),
            parent_rect.top(),
            width,
            top_height,
        )
        bottom_geometry = QRect(
            parent_rect.left(),
            parent_rect.top(),
            width,
            bottom_height,
        )
        bottom_geometry.moveBottom(parent_rect.bottom())
        reveal_geometry = QRect(
            parent_rect.left(),
            parent_rect.top(),
            width,
            min(self.bottom_edge_trigger_px, max(1, parent_rect.height())),
        )
        reveal_geometry.moveBottom(parent_rect.bottom())
        self.top_overlay.setGeometry(top_geometry)
        self.bottom_overlay.setGeometry(bottom_geometry)
        self.bottom_reveal_strip.setGeometry(reveal_geometry)
        if self.bottom_reveal_strip.isVisible():
            self.bottom_reveal_strip.raise_()
            if self.bottom_overlay.isVisible():
                self.bottom_overlay.raise_()

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
            self.bottom_reveal_strip,
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
            inside and 0 <= local.y() <= self.top_edge_trigger_px
        )
        self.pointer_in_bottom_trigger = bool(
            inside
            and self.overlay_parent.height() - self.bottom_edge_trigger_px
            <= local.y()
            < self.overlay_parent.height()
        )
        self.pointer_in_top_overlay = bool(
            self.top_overlay.isVisible()
            and self.top_overlay.rect().contains(
                self.top_overlay.mapFromGlobal(global_position)
            )
        )
        self.pointer_in_bottom_overlay = self._pointer_in_bottom_hover_region(
            global_position
        )

    def _pointer_in_bottom_hover_region(
        self,
        global_position: QPoint,
    ) -> bool:
        return self._bottom_hover_region_global().contains(global_position)

    def _bottom_hover_region_global(self) -> QRect:
        bottom_height = max(1, self._last_bottom_overlay_height)
        local_top = max(0, self.overlay_parent.height() - bottom_height)
        top_global = self.overlay_parent.mapToGlobal(QPoint(0, local_top)).y()
        frame = self.window.frameGeometry()
        bottom_bounds = QRect(frame)
        if self.fullscreen or self.window.isFullScreen():
            screen = self.window.screen()
            if screen is not None:
                # QRect uses inclusive right/bottom coordinates, while
                # Windows can round a high-DPI cursor at the physical edge
                # to x + width / y + height. Include only that boundary pixel.
                screen_edge = screen.geometry().adjusted(0, 0, 1, 1)
                bottom_bounds = bottom_bounds.united(screen_edge)
        top_global = min(top_global, bottom_bounds.bottom())
        return QRect(
            QPoint(bottom_bounds.left(), top_global),
            QPoint(bottom_bounds.right(), bottom_bounds.bottom()),
        )

    def _bottom_wheel_region_global(self) -> QRect:
        if not self.bottom_overlay.isVisible():
            return QRect()
        overlay_region = QRect(
            self.bottom_overlay.mapToGlobal(QPoint(0, 0)),
            self.bottom_overlay.size(),
        )
        bottom = max(
            overlay_region.bottom(),
            self.window.frameGeometry().bottom(),
        )
        left = overlay_region.left()
        right = overlay_region.right()
        if self.fullscreen or self.window.isFullScreen():
            screen = self.window.screen()
            if screen is not None:
                # Preserve the existing high-DPI boundary allowance only
                # while the visible overlay owns Wheel input.
                screen_edge = screen.geometry().adjusted(0, 0, 1, 1)
                left = min(left, screen_edge.left())
                right = max(right, screen_edge.right())
                bottom = max(bottom, screen_edge.bottom())
        return QRect(
            QPoint(left, overlay_region.top()),
            QPoint(right, bottom),
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

    def _update_reveal_strip_visibility(self) -> None:
        visible = bool(
            self.fullscreen
            and self.hide_ui_enabled
            and self.auto_reveal
        )
        self.bottom_reveal_strip.setVisible(visible)
        if visible:
            self.bottom_reveal_strip.raise_()
            if self.bottom_overlay.isVisible():
                self.bottom_overlay.raise_()

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
