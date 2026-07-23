from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from .metadata_store import HistoryEntry, MetadataStore


class HistoryModel(QAbstractListModel):
    EntryRole = int(Qt.ItemDataRole.UserRole) + 1
    PathRole = EntryRole + 1

    _TYPE_LABELS = {
        "folder": "画像フォルダ",
        "book_folder": "画像フォルダ",
        "archive": "ZIP / CBZ",
        "image": "画像",
    }

    def __init__(
        self,
        metadata_store: MetadataStore | None,
        parent=None,
        *,
        limit: int = 500,
    ) -> None:
        super().__init__(parent)
        self.metadata_store = metadata_store
        self.limit = max(1, min(5000, int(limit)))
        self._entries: list[HistoryEntry] = []
        if metadata_store is not None:
            metadata_store.history_changed.connect(self.refresh)
        self.refresh()

    @property
    def entries(self) -> tuple[HistoryEntry, ...]:
        return tuple(self._entries)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        entry = self.entry_at(index)
        if entry is None:
            return None
        if role == int(Qt.ItemDataRole.DisplayRole):
            opened = datetime.fromtimestamp(entry.last_opened_at).strftime(
                "%Y-%m-%d %H:%M"
            )
            page = str(entry.page_index + 1)
            if entry.total_pages:
                page = f"{page} / {entry.total_pages}"
            type_label = self._TYPE_LABELS.get(entry.item_type, entry.item_type)
            missing = " — 見つかりません" if not entry.exists else ""
            return (
                f"{entry.display_name}\n"
                f"{opened} — {page} — {type_label}{missing}"
            )
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return entry.path
        if role == int(Qt.ItemDataRole.ForegroundRole) and not entry.exists:
            return QColor("#888888")
        if role == self.EntryRole:
            return entry
        if role == self.PathRole:
            return entry.path
        return None

    def entry_at(self, index_or_row: QModelIndex | int) -> HistoryEntry | None:
        row = index_or_row.row() if isinstance(index_or_row, QModelIndex) else index_or_row
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def refresh(self) -> None:
        entries = (
            self.metadata_store.list_history(limit=self.limit)
            if self.metadata_store is not None
            else []
        )
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()
