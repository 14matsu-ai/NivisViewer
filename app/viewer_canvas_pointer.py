from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from time import monotonic
from typing import Callable

from PySide6.QtCore import QObject, QPoint, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication


class ViewerCanvasPointerState(Enum):
    IDLE = auto()
    PRESSED = auto()
    PANNING = auto()
    PENDING_SINGLE_CLICK = auto()
    DOUBLE_CLICK = auto()
    CANCELLED = auto()


@dataclass(frozen=True)
class CanvasPressSnapshot:
    local_position: QPoint
    global_position: QPoint
    pressed_at: float
    button: Qt.MouseButton
    modifiers: Qt.KeyboardModifier
    context_token: object
    fullscreen: bool
    overlay: bool
    edge_trigger: bool
    mouse_gesture: bool
    drop_active: bool


class ViewerCanvasPointerController(QObject):
    """Resolve click, pan, and double-click as mutually exclusive gestures."""

    singleClickConfirmed = Signal()
    doubleClickConfirmed = Signal()

    def __init__(
        self,
        *,
        context_provider: Callable[[], object],
        click_allowed: Callable[[QPoint], bool],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._context_provider = context_provider
        self._click_allowed = click_allowed
        self.state = ViewerCanvasPointerState.IDLE
        self.press: CanvasPressSnapshot | None = None
        self._pending_context: object | None = None
        self._pending_global_position = QPoint()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._confirm_single_click)

    @property
    def pending(self) -> bool:
        return self._timer.isActive()

    def begin(
        self,
        *,
        local_position: QPoint,
        global_position: QPoint,
        button: Qt.MouseButton,
        modifiers: Qt.KeyboardModifier,
        fullscreen: bool = False,
        overlay: bool = False,
        edge_trigger: bool = False,
        mouse_gesture: bool = False,
        drop_active: bool = False,
    ) -> bool:
        self.cancel()
        context = self._context_provider()
        allowed = bool(
            button == Qt.MouseButton.LeftButton
            and modifiers == Qt.KeyboardModifier.NoModifier
            and not overlay
            and not edge_trigger
            and not mouse_gesture
            and not drop_active
            and self._click_allowed(global_position)
        )
        self.press = CanvasPressSnapshot(
            QPoint(local_position),
            QPoint(global_position),
            monotonic(),
            button,
            modifiers,
            context,
            bool(fullscreen),
            bool(overlay),
            bool(edge_trigger),
            bool(mouse_gesture),
            bool(drop_active),
        )
        self.state = (
            ViewerCanvasPointerState.PRESSED
            if allowed
            else ViewerCanvasPointerState.CANCELLED
        )
        return allowed

    def move(self, local_position: QPoint) -> ViewerCanvasPointerState:
        if self.press is None or self.state not in {
            ViewerCanvasPointerState.PRESSED,
            ViewerCanvasPointerState.PANNING,
        }:
            return self.state
        distance = (
            QPoint(local_position) - self.press.local_position
        ).manhattanLength()
        if distance >= QApplication.startDragDistance():
            self.state = ViewerCanvasPointerState.PANNING
        return self.state

    def release(
        self,
        *,
        local_position: QPoint,
        global_position: QPoint,
        immediate: bool = False,
    ) -> bool:
        press = self.press
        if press is None or self.state != ViewerCanvasPointerState.PRESSED:
            self.press = None
            if self.state != ViewerCanvasPointerState.PENDING_SINGLE_CLICK:
                self.state = ViewerCanvasPointerState.IDLE
            return False
        moved = (
            QPoint(local_position) - press.local_position
        ).manhattanLength()
        allowed = bool(
            moved < QApplication.startDragDistance()
            and press.context_token == self._context_provider()
            and self._click_allowed(global_position)
        )
        self.press = None
        if not allowed:
            self.state = ViewerCanvasPointerState.CANCELLED
            return False
        self._pending_context = press.context_token
        self._pending_global_position = QPoint(global_position)
        if immediate:
            self._pending_context = None
            self.state = ViewerCanvasPointerState.IDLE
            self.singleClickConfirmed.emit()
            return True
        self.state = ViewerCanvasPointerState.PENDING_SINGLE_CLICK
        interval = max(1, QApplication.doubleClickInterval())
        scheduling_margin = min(10, max(0, interval // 4))
        self._timer.start(max(1, interval - scheduling_margin))
        return True

    def double_click(
        self,
        *,
        button: Qt.MouseButton,
        modifiers: Qt.KeyboardModifier,
        global_position: QPoint,
    ) -> bool:
        if (
            button != Qt.MouseButton.LeftButton
            or modifiers != Qt.KeyboardModifier.NoModifier
            or not self._click_allowed(global_position)
        ):
            self.cancel()
            return False
        self._timer.stop()
        self.press = None
        self._pending_context = None
        self.state = ViewerCanvasPointerState.DOUBLE_CLICK
        self.doubleClickConfirmed.emit()
        return True

    def cancel(self) -> None:
        self._timer.stop()
        self.press = None
        self._pending_context = None
        self.state = ViewerCanvasPointerState.CANCELLED

    def _confirm_single_click(self) -> None:
        context = self._pending_context
        position = QPoint(self._pending_global_position)
        self._pending_context = None
        if (
            context is None
            or context != self._context_provider()
            or not self._click_allowed(position)
        ):
            self.state = ViewerCanvasPointerState.CANCELLED
            return
        self.state = ViewerCanvasPointerState.IDLE
        self.singleClickConfirmed.emit()
