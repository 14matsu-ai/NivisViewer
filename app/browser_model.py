from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt
from PySide6.QtGui import QIcon, QImage

from .browser_sort import (
    BrowserSortKey,
    BrowserSortOrder,
    BrowserSortPolicy,
    normalize_browser_sort_key,
    normalize_browser_sort_order,
)
from .file_operation_artifact import FileOperationArtifactPolicy
from .browser_scanner import BrowserScanEntry, scan_entry_from_dir_entry
from .image_source import ARCHIVE_EXTENSIONS, PDF_EXTENSIONS, SUPPORTED_EXTENSIONS


BROWSER_IMAGE_EXTENSIONS = set(SUPPORTED_EXTENSIONS)
BROWSER_ARCHIVE_EXTENSIONS = set(ARCHIVE_EXTENSIONS)
BROWSER_PDF_EXTENSIONS = set(PDF_EXTENSIONS)


class BrowserItemKind(str, Enum):
    FOLDER = "folder"
    ARCHIVE = "archive"
    IMAGE = "image"
    PDF = "pdf"
    OTHER = "other"


@dataclass(frozen=True)
class BrowserItem:
    display_name: str
    path: Path
    kind: BrowserItemKind
    modified_at: float | None
    file_size: int | None = None
    modified_time_ns: int | None = None
    extension: str = ""
    hidden: bool = False
    system: bool = False
    openable_by_nivisviewer: bool = True
    can_generate_preview: bool = True
    preview_kind: str = ""
    preview_status: str = "pending"

    @property
    def can_open(self) -> bool:
        return self.openable_by_nivisviewer

    @property
    def is_folder(self) -> bool:
        return self.kind is BrowserItemKind.FOLDER

    @property
    def is_supported(self) -> bool:
        return self.openable_by_nivisviewer


@dataclass(frozen=True)
class BrowserDiscoveryResult:
    folder: Path
    items: tuple[BrowserItem, ...]
    error: str | None = None


class BrowserItemDiscovery:
    """Lists browser entries without decoding images or opening archives."""

    def discover(self, folder: str | Path) -> BrowserDiscoveryResult:
        target = Path(folder).expanduser()
        target = Path(os.path.abspath(os.path.normpath(os.fspath(target))))

        items: list[BrowserItem] = []
        try:
            with os.scandir(target) as entries:
                for entry in entries:
                    item = self._item_from_entry(entry)
                    if item is not None:
                        items.append(item)
        except OSError as exc:
            return BrowserDiscoveryResult(
                folder=target,
                items=(),
                error=f"フォルダを読み込めません: {target} ({exc})",
            )

        ordered = BrowserSortPolicy().sorted_items(items)
        return BrowserDiscoveryResult(folder=target, items=tuple(ordered))

    @staticmethod
    def _item_from_entry(entry: os.DirEntry[str]) -> BrowserItem | None:
        scanned = scan_entry_from_dir_entry(entry)
        if scanned is None:
            return None
        try:
            return browser_item_from_scan_entry(scanned)
        except ValueError:
            return None


def browser_item_from_scan_entry(entry: BrowserScanEntry) -> BrowserItem:
    modified_at = (
        entry.modified_time_ns / 1_000_000_000
        if entry.modified_time_ns is not None
        else None
    )
    return BrowserItem(
        display_name=entry.display_name,
        path=Path(entry.path).absolute(),
        kind=BrowserItemKind(entry.item_kind),
        modified_at=modified_at,
        file_size=entry.file_size,
        modified_time_ns=entry.modified_time_ns,
        extension=entry.extension,
        hidden=entry.hidden,
        system=entry.system,
        openable_by_nivisviewer=entry.openable_by_nivisviewer,
        can_generate_preview=entry.can_generate_preview,
        preview_kind=entry.preview_kind,
    )


class BrowserItemModel(QAbstractListModel):
    PathRole = int(Qt.ItemDataRole.UserRole) + 1
    KindRole = PathRole + 1
    ItemRole = PathRole + 2
    FileSizeRole = PathRole + 3
    ModifiedTimeRole = PathRole + 4
    ThumbnailImageRole = PathRole + 5
    ThumbnailLowResolutionRole = PathRole + 6
    HiddenRole = PathRole + 7
    SystemRole = PathRole + 8
    OpenableRole = PathRole + 9
    ThumbnailErrorRole = PathRole + 10
    PreviewKindRole = PathRole + 11
    CanGeneratePreviewRole = PathRole + 12
    PreviewStatusRole = PathRole + 13
    CutRole = PathRole + 14

    _KIND_LABELS = {
        BrowserItemKind.FOLDER: "フォルダ",
        BrowserItemKind.ARCHIVE: "書庫",
        BrowserItemKind.IMAGE: "画像",
        BrowserItemKind.PDF: "PDF",
        BrowserItemKind.OTHER: "その他",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._source_items: list[BrowserItem] = []
        self._items: list[BrowserItem] = []
        self._sort_policy = BrowserSortPolicy()
        self._scan_generation: int | None = None
        self._source_keys: set[str] = set()
        self._icons: dict[str, QIcon] = {}
        self._thumbnail_images: dict[str, QImage] = {}
        self._thumbnail_signatures: dict[str, tuple[int, float | None]] = {}
        self._low_resolution_thumbnails: set[str] = set()
        self._thumbnail_errors: dict[str, str] = {}
        self._preview_statuses: dict[str, str] = {}
        self._cut_keys: frozenset[str] = frozenset()
        self._fallback_icons: dict[BrowserItemKind, QIcon] = {}
        self._row_by_key: dict[str, int] = {}

    @property
    def items(self) -> tuple[BrowserItem, ...]:
        return tuple(self._items)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == int(Qt.ItemDataRole.DisplayRole):
            return f"{item.display_name}\n{self._KIND_LABELS[item.kind]}"
        if role == int(Qt.ItemDataRole.DecorationRole):
            return self._icons.get(self._key(item.path), self._fallback_icons.get(item.kind))
        if role == self.ThumbnailImageRole:
            image = self._thumbnail_images.get(self._key(item.path))
            return image
        if role == self.ThumbnailLowResolutionRole:
            return self._key(item.path) in self._low_resolution_thumbnails
        if role == self.HiddenRole:
            return item.hidden
        if role == self.SystemRole:
            return item.system
        if role == self.OpenableRole:
            return item.openable_by_nivisviewer
        if role == self.ThumbnailErrorRole:
            return self._thumbnail_errors.get(self._key(item.path))
        if role == self.PreviewKindRole:
            return item.preview_kind
        if role == self.CanGeneratePreviewRole:
            return item.can_generate_preview
        if role == self.PreviewStatusRole:
            return self._preview_statuses.get(
                self._key(item.path),
                item.preview_status,
            )
        if role == self.CutRole:
            return self._key(item.path) in self._cut_keys
        if role == self.PathRole:
            return str(item.path)
        if role == self.KindRole:
            return item.kind.value
        if role == self.ItemRole:
            return item
        if role == self.FileSizeRole:
            return item.file_size
        if role == self.ModifiedTimeRole:
            return item.modified_time_ns
        if role == int(Qt.ItemDataRole.ToolTipRole):
            error = self._thumbnail_errors.get(self._key(item.path))
            return str(item.path) if not error else f"{item.path}\n{error}"
        return None

    def set_items(
        self,
        items: tuple[BrowserItem, ...] | list[BrowserItem],
        *,
        preserve_thumbnails: bool = False,
    ) -> None:
        self.beginResetModel()
        self._source_items = [
            item
            for item in items
            if not FileOperationArtifactPolicy.is_internal_operation_artifact(
                item.path
            )
        ]
        self._source_keys = {self._key(item.path) for item in self._source_items}
        self._items = self._sort_policy.sorted_items(self._source_items)
        self._icons.clear()
        if preserve_thumbnails:
            self._retain_compatible_thumbnails()
        else:
            self._thumbnail_images.clear()
            self._thumbnail_signatures.clear()
            self._low_resolution_thumbnails.clear()
        self._thumbnail_errors.clear()
        self._preview_statuses.clear()
        self._scan_generation = None
        self._rebuild_row_index()
        self.endResetModel()

    def begin_directory_scan(self, *, generation: int) -> None:
        self.beginResetModel()
        self._source_items = []
        self._source_keys.clear()
        self._items = []
        self._icons.clear()
        self._thumbnail_images.clear()
        self._thumbnail_signatures.clear()
        self._low_resolution_thumbnails.clear()
        self._thumbnail_errors.clear()
        self._preview_statuses.clear()
        self._row_by_key.clear()
        self._scan_generation = int(generation)
        self.endResetModel()

    def append_scan_batch(
        self,
        entries: tuple[BrowserItem, ...] | list[BrowserItem],
        *,
        generation: int,
    ) -> int:
        if generation != self._scan_generation:
            return 0
        additions: list[BrowserItem] = []
        for entry in entries:
            if FileOperationArtifactPolicy.is_internal_operation_artifact(
                entry.path
            ):
                continue
            key = self._key(entry.path)
            if key in self._source_keys:
                continue
            self._source_keys.add(key)
            additions.append(entry)
        if not additions:
            return 0
        self.beginResetModel()
        self._source_items.extend(additions)
        self._items = self._sort_policy.sorted_items(self._source_items)
        self._rebuild_row_index()
        self.endResetModel()
        return len(additions)

    def finish_directory_scan(self, *, generation: int) -> bool:
        if generation != self._scan_generation:
            return False
        self._scan_generation = None
        return True

    def cancel_directory_scan(self, *, generation: int) -> bool:
        if generation != self._scan_generation:
            return False
        self._scan_generation = None
        return True

    def configure_sort(
        self,
        sort_key: BrowserSortKey | str,
        sort_order: BrowserSortOrder | str,
        folders_first: bool,
    ) -> bool:
        policy = BrowserSortPolicy(
            sort_key=normalize_browser_sort_key(sort_key),
            sort_order=normalize_browser_sort_order(sort_order),
            folders_first=bool(folders_first),
        )
        if policy == self._sort_policy:
            return False
        self.beginResetModel()
        self._sort_policy = policy
        self._items = policy.sorted_items(self._source_items)
        self._rebuild_row_index()
        self.endResetModel()
        return True

    def set_fallback_icons(self, icons: dict[BrowserItemKind, QIcon]) -> None:
        self._fallback_icons = dict(icons)
        if self._items:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._items) - 1, 0),
                [int(Qt.ItemDataRole.DecorationRole)],
            )

    def set_thumbnail(self, path: str | Path, icon: QIcon) -> bool:
        row = self.row_for_path(path)
        if row < 0:
            return False
        item = self._items[row]
        self._icons[self._key(item.path)] = icon
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [int(Qt.ItemDataRole.DecorationRole)])
        return True

    def set_thumbnail_image(
        self,
        path: str | Path,
        image: QImage,
        *,
        low_resolution: bool = False,
        request_token: int | None = None,
    ) -> bool:
        row = self.row_for_path(path)
        if row < 0 or image is None or image.isNull():
            return False
        item = self._items[row]
        key = self._key(item.path)
        signature = (
            (int(request_token), item.modified_at)
            if request_token is not None
            else None
        )
        if (
            not low_resolution
            and signature is not None
            and key in self._thumbnail_images
            and key not in self._low_resolution_thumbnails
            and self._thumbnail_signatures.get(key) == signature
        ):
            return False
        self._thumbnail_images[key] = image.copy()
        if low_resolution:
            self._low_resolution_thumbnails.add(key)
            self._thumbnail_signatures.pop(key, None)
        else:
            self._low_resolution_thumbnails.discard(key)
            if signature is None:
                self._thumbnail_signatures.pop(key, None)
            else:
                self._thumbnail_signatures[key] = signature
        self._thumbnail_errors.pop(key, None)
        index = self.index(row, 0)
        self.dataChanged.emit(
            index,
            index,
            [self.ThumbnailImageRole, self.ThumbnailLowResolutionRole],
        )
        return True

    def set_thumbnail_error(self, path: str | Path, message: str) -> bool:
        row = self.row_for_path(path)
        if row < 0:
            return False
        item = self._items[row]
        key = self._key(item.path)
        self._thumbnail_signatures.pop(key, None)
        self._thumbnail_errors[key] = str(message)
        index = self.index(row, 0)
        self.dataChanged.emit(
            index,
            index,
            [self.ThumbnailErrorRole, int(Qt.ItemDataRole.ToolTipRole)],
        )
        return True

    def clear_thumbnail_error(self, path: str | Path) -> bool:
        row = self.row_for_path(path)
        if row < 0:
            return False
        key = self._key(self._items[row].path)
        if key not in self._thumbnail_errors:
            return False
        self._thumbnail_errors.pop(key, None)
        index = self.index(row, 0)
        self.dataChanged.emit(
            index,
            index,
            [self.ThumbnailErrorRole, int(Qt.ItemDataRole.ToolTipRole)],
        )
        return True

    def set_preview_status(self, path: str | Path, status: str) -> bool:
        row = self.row_for_path(path)
        if row < 0:
            return False
        key = self._key(self._items[row].path)
        normalized = str(status)
        if self._preview_statuses.get(key) == normalized:
            return False
        self._preview_statuses[key] = normalized
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [self.PreviewStatusRole])
        return True

    def clear_thumbnails(self) -> None:
        if (
            not self._icons
            and not self._thumbnail_images
            and not self._thumbnail_signatures
            and not self._thumbnail_errors
        ):
            return
        self._icons.clear()
        self._thumbnail_images.clear()
        self._thumbnail_signatures.clear()
        self._low_resolution_thumbnails.clear()
        self._thumbnail_errors.clear()
        self._preview_statuses.clear()
        if self._items:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._items) - 1, 0),
                [
                    int(Qt.ItemDataRole.DecorationRole),
                    self.ThumbnailImageRole,
                    self.ThumbnailLowResolutionRole,
                    self.ThumbnailErrorRole,
                ],
            )

    def _has_compatible_thumbnail(
        self,
        item: BrowserItem,
        request_token: int,
    ) -> bool:
        key = self._key(item.path)
        return (
            key in self._thumbnail_images
            and key not in self._low_resolution_thumbnails
            and self._thumbnail_signatures.get(key)
            == (int(request_token), item.modified_at)
        )

    def set_cut_paths(self, paths: tuple[str | Path, ...] | list[str | Path]) -> bool:
        cut_keys = frozenset(self._key(Path(path)) for path in paths)
        changed_keys = self._cut_keys.symmetric_difference(cut_keys)
        if not changed_keys:
            return False
        self._cut_keys = cut_keys
        for key in changed_keys:
            row = self._row_by_key.get(key, -1)
            if row >= 0:
                index = self.index(row, 0)
                self.dataChanged.emit(index, index, [self.CutRole])
        return True

    def item_at(self, index_or_row: QModelIndex | int) -> BrowserItem | None:
        row = index_or_row.row() if isinstance(index_or_row, QModelIndex) else index_or_row
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def row_for_path(self, path: str | Path) -> int:
        return self._row_by_key.get(self._key(Path(path)), -1)

    def _rebuild_row_index(self) -> None:
        self._row_by_key = {
            self._key(item.path): row for row, item in enumerate(self._items)
        }

    def _retain_compatible_thumbnails(self) -> None:
        items_by_key = {self._key(item.path): item for item in self._source_items}
        retained_signatures = {
            key: signature
            for key, signature in self._thumbnail_signatures.items()
            if (
                key in self._thumbnail_images
                and key not in self._low_resolution_thumbnails
                and (item := items_by_key.get(key)) is not None
                and signature[1] == item.modified_at
            )
        }
        self._thumbnail_images = {
            key: self._thumbnail_images[key] for key in retained_signatures
        }
        self._thumbnail_signatures = retained_signatures
        self._low_resolution_thumbnails.clear()

    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path)))
        ).casefold()
