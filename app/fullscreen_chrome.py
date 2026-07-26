from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMenuBar,
    QSlider,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)


class FullscreenChromeController(QObject):
    """Shows fullscreen chrome as overlays without resizing the Viewer."""

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
        self.edge_trigger_px = 8
        self.hide_delay_ms = 900
        self.auto_reveal = True
        self.active = False
        self._attached = False
        self._ui_mouse_down = False

        parent = window.centralWidget()
        if parent is None:
            raise ValueError("FullscreenChromeController requires a central widget")
        self.overlay_parent = parent
        self.top_overlay = QFrame(parent)
        self.top_overlay.setObjectName("fullscreen_top_chrome")
        self.top_overlay.setFrameShape(QFrame.Shape.StyledPanel)
        self._top_layout = QVBoxLayout(self.top_overlay)
        self._top_layout.setContentsMargins(0, 0, 0, 0)
        self._top_layout.setSpacing(0)

        self.bottom_overlay = QFrame(parent)
        self.bottom_overlay.setObjectName("fullscreen_bottom_chrome")
        self.bottom_overlay.setFrameShape(QFrame.Shape.StyledPanel)
        self._bottom_layout = QVBoxLayout(self.bottom_overlay)
        self._bottom_layout.setContentsMargins(4, 2, 4, 0)
        self._bottom_layout.setSpacing(0)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_if_idle)
        self.slider.sliderPressed.connect(self._cancel_hide)
        self.slider.sliderReleased.connect(self.schedule_hide)

        for widget in (
            window,
            viewer,
            self.top_overlay,
            self.bottom_overlay,
            menu_bar,
            slider,
            status_bar,
        ):
            widget.installEventFilter(self)
            widget.setMouseTracking(True)
        self.configure(
            auto_reveal=auto_reveal,
            edge_trigger_px=edge_trigger_px,
            hide_delay_ms=hide_delay_ms,
        )
        self.top_overlay.hide()
        self.bottom_overlay.hide()

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
        if self.active and not self.auto_reveal:
            self.hide_overlays()

    def set_active(self, active: bool) -> None:
        normalized = bool(active)
        if normalized == self.active:
            self._update_geometry()
            return
        self.active = normalized
        self._hide_timer.stop()
        if normalized:
            self._attach_chrome()
            self.hide_overlays()
            self._update_geometry()
        else:
            self.hide_overlays()
            self._restore_chrome()

    def process_pointer(self, global_position: QPoint) -> None:
        if not self.active or not self.auto_reveal:
            return
        local = self.overlay_parent.mapFromGlobal(global_position)
        if not self.overlay_parent.rect().contains(local):
            self.schedule_hide()
            return
        if local.y() <= self.edge_trigger_px:
            self.show_top()
        if local.y() >= self.overlay_parent.height() - self.edge_trigger_px - 1:
            self.show_bottom()
        if not self._pointer_in_reveal_area(global_position):
            self.schedule_hide()

    def show_top(self) -> None:
        if not self.active:
            return
        self._update_geometry()
        self.bottom_overlay.hide()
        self.top_overlay.show()
        self.top_overlay.raise_()
        self._hide_timer.stop()

    def show_bottom(self) -> None:
        if not self.active:
            return
        self._update_geometry()
        self.top_overlay.hide()
        self.bottom_overlay.show()
        self.bottom_overlay.raise_()
        self._hide_timer.stop()

    def hide_overlays(self) -> None:
        self.top_overlay.hide()
        self.bottom_overlay.hide()

    def schedule_hide(self) -> None:
        if self.active:
            self._hide_timer.stop()
            self._hide_timer.start(self.hide_delay_ms)

    def reevaluate_visibility(self) -> None:
        """Re-check chrome after viewer state/layout changes."""
        self._hide_timer.stop()
        if not self.active:
            return
        if self._pointer_in_reveal_area(QCursor.pos()):
            return
        self.schedule_hide()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if not hasattr(self, "top_overlay"):
            return False
        event_type = event.type()
        if event_type in {QEvent.Type.Resize, QEvent.Type.Move}:
            self._update_geometry()
        if (
            getattr(self, "active", False)
            and event_type == QEvent.Type.MouseMove
            and isinstance(event, QMouseEvent)
        ):
            self.process_pointer(event.globalPosition().toPoint())
        if watched in {
            self.top_overlay,
            self.bottom_overlay,
            self.menu_bar,
            self.slider,
            self.status_bar,
        }:
            if event_type == QEvent.Type.Enter:
                self._hide_timer.stop()
            elif event_type == QEvent.Type.Leave:
                self.schedule_hide()
            elif event_type == QEvent.Type.MouseButtonPress:
                self._ui_mouse_down = True
                self._hide_timer.stop()
            elif event_type == QEvent.Type.MouseButtonRelease:
                self._ui_mouse_down = False
                self.schedule_hide()
        return False

    def _attach_chrome(self) -> None:
        if self._attached:
            return
        self.menu_bar.setParent(self.top_overlay)
        self._top_layout.addWidget(self.menu_bar)
        self.slider.setParent(self.bottom_overlay)
        self.status_bar.setParent(self.bottom_overlay)
        self._bottom_layout.addWidget(self.slider)
        self._bottom_layout.addWidget(self.status_bar)
        self.menu_bar.show()
        self.slider.show()
        self.status_bar.show()
        self._attached = True

    def _restore_chrome(self) -> None:
        if not self._attached:
            return
        self._top_layout.removeWidget(self.menu_bar)
        self._bottom_layout.removeWidget(self.slider)
        self._bottom_layout.removeWidget(self.status_bar)
        self.window.setMenuBar(self.menu_bar)
        central_layout = self.overlay_parent.layout()
        if isinstance(central_layout, QVBoxLayout):
            central_layout.addWidget(self.slider, 0)
        self.window.setStatusBar(self.status_bar)
        self.menu_bar.show()
        self.slider.show()
        self.status_bar.show()
        self._attached = False

    def _update_geometry(self) -> None:
        if not self.active:
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
        if not self.active or self._interaction_blocks_hide():
            if self.hide_delay_ms:
                self.schedule_hide()
            return
        global_position = QCursor.pos()
        if self._pointer_in_reveal_area(global_position):
            return
        self.hide_overlays()

    def _pointer_in_reveal_area(self, global_position: QPoint) -> bool:
        local = self.overlay_parent.mapFromGlobal(global_position)
        if 0 <= local.y() <= self.edge_trigger_px:
            return True
        if (
            self.overlay_parent.height() - self.edge_trigger_px - 1
            <= local.y()
            < self.overlay_parent.height()
        ):
            return True
        return (
            self.top_overlay.isVisible()
            and self.top_overlay.rect().contains(
                self.top_overlay.mapFromGlobal(global_position)
            )
        ) or (
            self.bottom_overlay.isVisible()
            and self.bottom_overlay.rect().contains(
                self.bottom_overlay.mapFromGlobal(global_position)
            )
        )

    def _interaction_blocks_hide(self) -> bool:
        application = QApplication.instance()
        if self._ui_mouse_down or self.slider.isSliderDown():
            return True
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            return True
        if QApplication.activePopupWidget() is not None:
            return True
        modal = QApplication.activeModalWidget()
        return bool(
            application is not None
            and modal is not None
            and (modal is self.window or self.window.isAncestorOf(modal))
        )

    def _cancel_hide(self) -> None:
        self._hide_timer.stop()
