from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFocusEvent, QMouseEvent
from PySide6.QtWidgets import QLineEdit


class BrowserAddressBar(QLineEdit):
    """Select once on focus-by-click while preserving normal text dragging."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mouse_focus_pending = False
        self._select_all_on_release = False
        self._press_position: QPointF | None = None

    def focusInEvent(self, event: QFocusEvent) -> None:  # type: ignore[override]
        self._mouse_focus_pending = (
            event.reason() == Qt.FocusReason.MouseFocusReason
        )
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # type: ignore[override]
        self._mouse_focus_pending = False
        self._select_all_on_release = False
        self._press_position = None
        super().focusOutEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._select_all_on_release = bool(
            event.button() == Qt.MouseButton.LeftButton
            and (self._mouse_focus_pending or not self.hasFocus())
        )
        self._mouse_focus_pending = False
        self._press_position = (
            event.position() if self._select_all_on_release else None
        )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if (
            self._select_all_on_release
            and self._press_position is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and event.position() != self._press_position
        ):
            self._select_all_on_release = False
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        select_all = bool(
            self._select_all_on_release
            and event.button() == Qt.MouseButton.LeftButton
        )
        self._select_all_on_release = False
        self._press_position = None
        super().mouseReleaseEvent(event)
        if select_all:
            self.selectAll()
