from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QRunnable,
    QThreadPool,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor

from .metadata_store import MetadataStore
from .shell_icon_provider import ShellAssociatedIconProvider


_FOLDER_PROBE_POOL = QThreadPool()
_FOLDER_PROBE_POOL.setMaxThreadCount(1)


@dataclass(frozen=True)
class FolderBookmarkItem:
    label: str
    path: str
    exists: bool | None
    sort_order: int


class _FolderProbeSignals(QObject):
    finished = Signal(int, str, bool)


class _FolderProbe(QRunnable):
    def __init__(self, generation: int, path: str) -> None:
        super().__init__()
        self.generation = generation
        self.path = path
        self.signals = _FolderProbeSignals()

    @Slot()
    def run(self) -> None:
        try:
            exists = Path(self.path).is_dir()
        except OSError:
            exists = False
        self.signals.finished.emit(self.generation, self.path, exists)


class FolderBookmarkModel(QAbstractListModel):
    EntryRole = int(Qt.ItemDataRole.UserRole) + 1
    PathRole = EntryRole + 1
    ExistsRole = PathRole + 1

    def __init__(
        self,
        metadata_store: MetadataStore | None,
        parent: QObject | None = None,
        *,
        shell_icon_provider: ShellAssociatedIconProvider | None = None,
    ) -> None:
        super().__init__(parent)
        self.metadata_store = metadata_store
        self.shell_icon_provider = (
            shell_icon_provider or ShellAssociatedIconProvider()
        )
        self._entries: list[FolderBookmarkItem] = []
        self._row_by_path: dict[str, int] = {}
        self._probe_generation = 0
        if metadata_store is not None:
            metadata_store.bookmarks_changed.connect(self.refresh)
        self.refresh()

    @property
    def entries(self) -> tuple[FolderBookmarkItem, ...]:
        return tuple(self._entries)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        entry = self.entry_at(index)
        if entry is None:
            return None
        if role == int(Qt.ItemDataRole.DisplayRole):
            suffix = " — 見つかりません" if entry.exists is False else ""
            return f"{entry.label}{suffix}"
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return entry.path
        if role == int(Qt.ItemDataRole.DecorationRole):
            return self.shell_icon_provider.icon_for_extension("", folder=True)
        if role == int(Qt.ItemDataRole.ForegroundRole) and entry.exists is False:
            return QColor("#888888")
        if role == self.EntryRole:
            return entry
        if role == self.PathRole:
            return entry.path
        if role == self.ExistsRole:
            return entry.exists
        return None

    def entry_at(
        self,
        index_or_row: QModelIndex | int,
    ) -> FolderBookmarkItem | None:
        row = index_or_row.row() if isinstance(index_or_row, QModelIndex) else index_or_row
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def row_for_path(self, path: str | Path) -> int:
        return self._row_by_path.get(self._path_key(path), -1)

    def refresh(self) -> None:
        bookmarks = (
            self.metadata_store.list_folder_bookmarks()
            if self.metadata_store is not None
            else []
        )
        self._probe_generation += 1
        generation = self._probe_generation
        self.beginResetModel()
        self._entries = [
            FolderBookmarkItem(
                label=entry.display_name,
                path=entry.path,
                exists=None,
                sort_order=entry.sort_order,
            )
            for entry in bookmarks
        ]
        self._row_by_path = {
            self._path_key(entry.path): row
            for row, entry in enumerate(self._entries)
        }
        self.endResetModel()
        for entry in self._entries:
            worker = _FolderProbe(generation, entry.path)
            worker.signals.finished.connect(self._on_probe_finished)
            _FOLDER_PROBE_POOL.start(worker)

    @Slot(int, str, bool)
    def _on_probe_finished(self, generation: int, path: str, exists: bool) -> None:
        if generation != self._probe_generation:
            return
        row = self.row_for_path(path)
        if row < 0:
            return
        entry = self._entries[row]
        self._entries[row] = replace(entry, exists=bool(exists))
        index = self.index(row, 0)
        self.dataChanged.emit(
            index,
            index,
            [
                int(Qt.ItemDataRole.DisplayRole),
                int(Qt.ItemDataRole.ForegroundRole),
                self.ExistsRole,
            ],
        )

    @staticmethod
    def _path_key(path: str | Path) -> str:
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path)))
        ).casefold()
