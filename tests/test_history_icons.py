from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QListView, QStyleOptionViewItem

from app.favorite_item_delegate import HistoryItemDelegate
from app.history_model import HistoryModel
from app.metadata_store import HistoryEntry


def test_history_delegate_preserves_extension_association_icons(qapp, monkeypatch):
    model = HistoryModel(None)
    view = QListView()
    try:
        model._entries = [
            HistoryEntry(path, kind, 0, 0, 1, 1)
            for path, kind in (
                ('C:/missing/book.zip', 'archive'),
                ('C:/missing/page.png', 'image'),
                ('C:/missing/book.pdf', 'pdf'),
                ('C:/missing/folder', 'folder'),
            )
        ]
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.red)
        association_icon = QIcon(pixmap)
        calls = []

        def icon_for_extension(extension, *, folder):
            calls.append((extension, folder))
            return association_icon

        monkeypatch.setattr(model.shell_icon_provider, 'icon_for_extension', icon_for_extension)
        view.setModel(model)
        delegate = HistoryItemDelegate(view)
        for row, expected in enumerate((('.zip', False), ('.png', False), ('.pdf', False), ('', True))):
            option = QStyleOptionViewItem()
            delegate.initStyleOption(option, model.index(row, 0))
            assert calls[-1] == expected
            assert option.icon.cacheKey() == association_icon.cacheKey()
    finally:
        model.availability_service.close()
        view.close()
