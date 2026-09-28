from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtWidgets import QListView


class SidebarHistoryView(QListView):
    open_requested = Signal(QModelIndex)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.double_click_to_open = False
        self._ignore_release = False
        self.clicked.connect(self._clicked)
        self.doubleClicked.connect(self._double_clicked)

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
