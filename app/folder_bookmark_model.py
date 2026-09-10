from __future__ import annotations

from .i18n import tr


from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Qt, Slot
from PySide6.QtGui import QColor

from .metadata_store import MetadataStore
from .path_availability import (
    PathAvailability,
    PathAvailabilityResult,
    PathAvailabilityService,
    path_key,
)
from .shell_icon_provider import ShellAssociatedIconProvider


@dataclass(frozen=True)
class FolderBookmarkItem:
    label: str
    path: str
    exists: bool | None
    sort_order: int
    availability: PathAvailability = PathAvailability.UNKNOWN


class FolderBookmarkModel(QAbstractListModel):
    EntryRole = int(Qt.ItemDataRole.UserRole) + 1
    PathRole = EntryRole + 1
    ExistsRole = PathRole + 1
    AvailabilityRole = ExistsRole + 1

    def __init__(
        self,
        metadata_store: MetadataStore | None,
        parent: QObject | None = None,
        *,
        shell_icon_provider: ShellAssociatedIconProvider | None = None,
        availability_service: PathAvailabilityService | None = None,
    ) -> None:
        super().__init__(parent)
        self.metadata_store = metadata_store
        self.shell_icon_provider = (
            shell_icon_provider or ShellAssociatedIconProvider()
        )
        self._owns_availability_service = availability_service is None
        self.availability_service = (
            availability_service or PathAvailabilityService(self)
        )
        self._entries: list[FolderBookmarkItem] = []
        self._row_by_path: dict[str, int] = {}
        self._probe_generation = 0
        self._pending_requests: dict[int, tuple[int, str]] = {}
        self.availability_service.result_ready.connect(self._on_probe_finished)
        if self._owns_availability_service:
            self.destroyed.connect(lambda *_args: self.availability_service.close())
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
            if entry.availability is PathAvailability.CHECKING:
                suffix = tr(' — 確認中')
            elif entry.availability is PathAvailability.MISSING:
                suffix = tr(' — 見つかりません')
            elif entry.availability in {
                PathAvailability.UNAVAILABLE,
                PathAvailability.ERROR,
            }:
                suffix = tr(' — 現在確認できません')
            else:
                suffix = ""
            return f"{entry.label}{suffix}"
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return entry.path
        if role == int(Qt.ItemDataRole.DecorationRole):
            return self.shell_icon_provider.icon_for_extension("", folder=True)
        if (
            role == int(Qt.ItemDataRole.ForegroundRole)
            and entry.availability
            in {
                PathAvailability.MISSING,
                PathAvailability.UNAVAILABLE,
                PathAvailability.ERROR,
            }
        ):
            return QColor("#888888")
        if role == self.EntryRole:
            return entry
        if role == self.PathRole:
            return entry.path
        if role == self.ExistsRole:
            return entry.exists
        if role == self.AvailabilityRole:
            return entry.availability.value
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
        return self._row_by_path.get(path_key(path), -1)

    def refresh(self) -> None:
        bookmarks = (
            self.metadata_store.list_folder_bookmarks()
            if self.metadata_store is not None
            else []
        )
        self._probe_generation += 1
        generation = self._probe_generation
        self._pending_requests.clear()
        self.beginResetModel()
        self._entries = [
            FolderBookmarkItem(
                label=entry.display_name,
                path=entry.path,
                exists=None,
                sort_order=entry.sort_order,
                availability=PathAvailability.UNKNOWN,
            )
            for entry in bookmarks
        ]
        self._row_by_path = {
            path_key(entry.path): row
            for row, entry in enumerate(self._entries)
        }
        self.endResetModel()
        for row, entry in enumerate(self._entries):
            request_id = self.availability_service.probe(entry.path)
            if request_id:
                self._entries[row] = replace(
                    entry,
                    availability=PathAvailability.CHECKING,
                )
                self._pending_requests[request_id] = (
                    generation,
                    path_key(entry.path),
                )

    def refresh_availability(self) -> None:
        self.availability_service.invalidate()
        self._probe_generation += 1
        generation = self._probe_generation
        self._pending_requests.clear()
        for row, entry in enumerate(self._entries):
            self._entries[row] = replace(
                entry,
                exists=None,
                availability=PathAvailability.CHECKING,
            )
            request_id = self.availability_service.probe(entry.path, force=True)
            if request_id:
                self._pending_requests[request_id] = (
                    generation,
                    path_key(entry.path),
                )
        if self._entries:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._entries) - 1, 0),
            )

    @Slot(object)
    def _on_probe_finished(self, result: PathAvailabilityResult) -> None:
        pending = self._pending_requests.pop(result.request_id, None)
        if pending is None:
            return
        generation, key = pending
        if generation != self._probe_generation or key != result.path_key:
            return
        row = self._row_by_path.get(key, -1)
        if row < 0:
            return
        entry = self._entries[row]
        exists = (
            True
            if result.state is PathAvailability.AVAILABLE
            else False if result.state is PathAvailability.MISSING else None
        )
        self._entries[row] = replace(
            entry,
            exists=exists,
            availability=result.state,
        )
        index = self.index(row, 0)
        self.dataChanged.emit(index, index)
