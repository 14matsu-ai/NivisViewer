from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Qt, Slot
from PySide6.QtGui import QColor

from .metadata_store import BrowserBookmark, MetadataStore
from .path_availability import (
    PathAvailability,
    PathAvailabilityResult,
    PathAvailabilityService,
    path_key,
)


class BookmarkModel(QAbstractListModel):
    EntryRole = int(Qt.ItemDataRole.UserRole) + 1
    PathRole = EntryRole + 1
    AvailabilityRole = PathRole + 1

    _TYPE_LABELS = {
        "folder": "フォルダ",
        "book_folder": "画像フォルダ",
        "archive": "ZIP / CBZ",
        "image": "画像",
    }

    def __init__(
        self,
        metadata_store: MetadataStore | None,
        parent: QObject | None = None,
        *,
        availability_service: PathAvailabilityService | None = None,
    ) -> None:
        super().__init__(parent)
        self.metadata_store = metadata_store
        self._owns_availability_service = availability_service is None
        self.availability_service = (
            availability_service or PathAvailabilityService(self)
        )
        self._entries: list[BrowserBookmark] = []
        self._availability: dict[str, PathAvailability] = {}
        self._pending_requests: dict[int, tuple[int, str]] = {}
        self._probe_generation = 0
        self.availability_service.result_ready.connect(self._on_probe_finished)
        if self._owns_availability_service:
            self.destroyed.connect(lambda *_args: self.availability_service.close())
        if metadata_store is not None:
            metadata_store.bookmarks_changed.connect(self.refresh)
        self.refresh()

    @property
    def entries(self) -> tuple[BrowserBookmark, ...]:
        return tuple(self._entries)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        entry = self.entry_at(index)
        if entry is None:
            return None
        if role == int(Qt.ItemDataRole.DisplayRole):
            type_label = self._TYPE_LABELS.get(entry.item_type, entry.item_type)
            suffix = self._availability_suffix(self._state(entry.path))
            return f"{entry.display_name}\n{type_label}{suffix}"
        if role == int(Qt.ItemDataRole.ToolTipRole):
            suffix = self._availability_tooltip(self._state(entry.path))
            return entry.path if not suffix else f"{entry.path}\n{suffix}"
        if (
            role == int(Qt.ItemDataRole.ForegroundRole)
            and self._state(entry.path)
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
        if role == self.AvailabilityRole:
            return self._state(entry.path).value
        return None

    def entry_at(self, index_or_row: QModelIndex | int) -> BrowserBookmark | None:
        row = index_or_row.row() if isinstance(index_or_row, QModelIndex) else index_or_row
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def refresh(self) -> None:
        entries = (
            self.metadata_store.list_browser_bookmarks()
            if self.metadata_store is not None
            else []
        )
        self._probe_generation += 1
        generation = self._probe_generation
        self._pending_requests.clear()
        self.beginResetModel()
        self._entries = list(entries)
        self._availability = {
            path_key(entry.path): PathAvailability.UNKNOWN
            for entry in self._entries
        }
        self.endResetModel()
        for entry in self._entries:
            key = path_key(entry.path)
            request_id = self.availability_service.probe(entry.path)
            if request_id:
                self._availability[key] = PathAvailability.CHECKING
                self._pending_requests[request_id] = (generation, key)

    def refresh_availability(self) -> None:
        self.availability_service.invalidate()
        self._probe_generation += 1
        generation = self._probe_generation
        self._pending_requests.clear()
        for entry in self._entries:
            key = path_key(entry.path)
            self._availability[key] = PathAvailability.CHECKING
            request_id = self.availability_service.probe(entry.path, force=True)
            if request_id:
                self._pending_requests[request_id] = (generation, key)
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
        self._availability[key] = result.state
        for row, entry in enumerate(self._entries):
            if path_key(entry.path) == key:
                index = self.index(row, 0)
                self.dataChanged.emit(index, index)
                break

    def _state(self, path: str) -> PathAvailability:
        return self._availability.get(path_key(path), PathAvailability.UNKNOWN)

    @staticmethod
    def _availability_suffix(state: PathAvailability) -> str:
        if state is PathAvailability.CHECKING:
            return " — 確認中"
        if state is PathAvailability.MISSING:
            return " — 見つかりません"
        if state in {PathAvailability.UNAVAILABLE, PathAvailability.ERROR}:
            return " — 現在確認できません"
        return ""

    @staticmethod
    def _availability_tooltip(state: PathAvailability) -> str:
        if state is PathAvailability.CHECKING:
            return "確認中"
        if state is PathAvailability.MISSING:
            return "見つかりません"
        if state in {PathAvailability.UNAVAILABLE, PathAvailability.ERROR}:
            return "現在確認できません"
        return ""
