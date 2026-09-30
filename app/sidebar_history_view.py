from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QListView

from .drag_drop import paths_from_mime_data


class SidebarHistoryView(QListView):
    open_requested = Signal(QModelIndex)
    paths_dropped = Signal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.double_click_to_open = False
        self._ignore_release = False
        self._folder_drop_enabled = False
        self._shift_favorite_drop_enabled = False
        self._sidebar_file_operations_disabled = False
        self.shift_favorite_candidate_resolver = None
        self.sidebar_add_candidate_resolver = None
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.clicked.connect(self._clicked)
        self.doubleClicked.connect(self._double_clicked)

    def set_folder_drop_enabled(self, enabled: bool) -> None:
        self._folder_drop_enabled = bool(enabled)
        self._sync_accept_drops()

    def set_shift_favorite_drop_enabled(self, enabled: bool) -> None:
        self._shift_favorite_drop_enabled = bool(enabled)
        self._sync_accept_drops()

    def set_sidebar_file_operations_disabled(self, disabled: bool) -> None:
        self._sidebar_file_operations_disabled = bool(disabled)

    def _sync_accept_drops(self) -> None:
        enabled = self._folder_drop_enabled or self._shift_favorite_drop_enabled
        self.setAcceptDrops(enabled)
        self.viewport().setAcceptDrops(enabled)

    def _accept_history_drop(self, event) -> bool:
        if self._sidebar_file_operations_disabled:
            resolver = self.sidebar_add_candidate_resolver
            if not (callable(resolver) and resolver(
                paths_from_mime_data(event.mimeData()), event.modifiers(), event.source(),
            )):
                event.ignore()
                return False
        if self._sidebar_file_operations_disabled or self._shift_favorite_candidate(event):
            if not event.possibleActions() & Qt.DropAction.CopyAction:
                event.ignore()
                return False
            event.setDropAction(Qt.DropAction.CopyAction)
            if event.dropAction() != Qt.DropAction.CopyAction:
                event.ignore()
                return False
            event.accept()
            return True
        if not self._folder_drop_enabled:
            event.ignore()
            return False
        event.acceptProposedAction()
        return event.isAccepted()

    def _shift_favorite_candidate(self, event) -> bool:
        if not (self._shift_favorite_drop_enabled
                and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            return False
        resolver = self.shift_favorite_candidate_resolver
        return not callable(resolver) or bool(
            resolver(paths_from_mime_data(event.mimeData()), event.source())
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if (self._folder_drop_enabled or self._shift_favorite_drop_enabled) and paths_from_mime_data(event.mimeData()):
            self._accept_history_drop(event)
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if (self._folder_drop_enabled or self._shift_favorite_drop_enabled) and paths_from_mime_data(event.mimeData()):
            self._accept_history_drop(event)
            return
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = paths_from_mime_data(event.mimeData())
        if not (self._folder_drop_enabled or self._shift_favorite_drop_enabled) or not paths:
            event.ignore()
            return
        if not self._accept_history_drop(event):
            return
        self.paths_dropped.emit(paths, event.modifiers(), event.source())

    def _clicked(self, index):
        if not self.double_click_to_open:
            self.open_requested.emit(index)

    def _double_clicked(self, index):
        if self.double_click_to_open:
            self.open_requested.emit(index)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.double_click_to_open:
            self._ignore_release = True
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
        if self._ignore_release and event.button() == Qt.MouseButton.LeftButton:
            self._ignore_release = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.open_requested.emit(self.currentIndex())
            event.accept()
        else:
            super().keyPressEvent(event)
