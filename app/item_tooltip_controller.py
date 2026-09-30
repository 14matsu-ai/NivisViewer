from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtWidgets import QToolTip


class ItemTooltipController(QObject):
    """Own item tooltip timing so Qt cannot immediately revive a dismissed tip."""

    def __init__(self, view, *, delay_ms: int = 700) -> None:
        super().__init__(view)
        self.view = view
        self._position: QPoint | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._show_tooltip)
        view.setMouseTracking(True)
        view.viewport().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.ToolTip:
            # QApplication can send this almost immediately after a visible tip.
            # Our movement timer alone decides when another tip may appear.
            event.accept()
            return True
        if kind == QEvent.Type.MouseMove:
            self._dismiss()
            if event.buttons() == Qt.MouseButton.NoButton:
                self._position = event.position().toPoint()
                self._timer.start()
        elif kind in {
            QEvent.Type.Leave, QEvent.Type.Hide, QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel, QEvent.Type.DragEnter,
        }:
            self._dismiss()
        return False

    def _dismiss(self) -> None:
        self._timer.stop()
        self._position = None
        QToolTip.hideText()

    def _show_tooltip(self) -> None:
        viewport = self.view.viewport()
        if self._position is None or not viewport.isVisible():
            return
        index = self.view.indexAt(self._position)
        text = index.data(Qt.ItemDataRole.ToolTipRole) if index.isValid() else None
        if text:
            QToolTip.showText(
                viewport.mapToGlobal(self._position), str(text), viewport,
                self.view.visualRect(index),
            )
