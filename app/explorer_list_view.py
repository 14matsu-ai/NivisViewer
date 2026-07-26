from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QListView, QTreeView

from .drag_drop import ExplorerSelectionController, FileDragController, paths_from_mime_data


class ExplorerListView(QListView):
    """Explorer-like item drag; Shift+blank drag is the only rubber band path."""

    paths_dropped = Signal(object, object, object, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.selection_controller = ExplorerSelectionController()
        self._press_position = QPoint()
        self._press_timer = QElapsedTimer()
        self._drag_started = False
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragEnabled(False)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        index = self.indexAt(event.position().toPoint())
        row = index.row() if index.isValid() else -1
        self.selection_controller.begin(row, event.modifiers())
        self._press_position = event.position().toPoint()
        self._press_timer.start()
        self._drag_started = False
        if (
            event.button() == Qt.MouseButton.LeftButton
            and row < 0
            and not self.selection_controller.shift_rubber_band
        ):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if (
            not self._drag_started
            and self.selection_controller.press_row >= 0
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._press_position).manhattanLength()
            >= QApplication.startDragDistance()
            and self._press_timer.isValid()
            and self._press_timer.elapsed() >= QApplication.startDragTime()
        ):
            self._drag_started = True
            self.start_path_drag()
            return
        if self.selection_controller.shift_rubber_band:
            super().mouseMoveEvent(event)
            return
        if self.selection_controller.blank_press:
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(event)
        self.selection_controller = ExplorerSelectionController()
        self._drag_started = False

    def start_path_drag(self) -> bool:
        model = self.model()
        if model is None:
            return False
        paths: list[str] = []
        for index in sorted(self.selectedIndexes(), key=lambda value: value.row()):
            path = index.data(getattr(model, "PathRole", Qt.ItemDataRole.UserRole))
            if path:
                paths.append(str(path))
        if not paths:
            return False
        drag = QDrag(self)
        drag.setMimeData(FileDragController.mime_data(tuple(paths)))
        drag.exec(
            Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
            Qt.DropAction.CopyAction,
        )
        return True

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        paths = paths_from_mime_data(event.mimeData())
        if not paths:
            event.ignore()
            return
        index: QModelIndex = self.indexAt(event.position().toPoint())
        self.paths_dropped.emit(paths, index, event.modifiers(), event.source())
        event.acceptProposedAction()


class PathDropTreeView(QTreeView):
    paths_dropped = Signal(object, object, object, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        paths = paths_from_mime_data(event.mimeData())
        index = self.indexAt(event.position().toPoint())
        if not paths or not index.isValid():
            event.ignore()
            return
        self.paths_dropped.emit(paths, index, event.modifiers(), event.source())
        event.acceptProposedAction()
