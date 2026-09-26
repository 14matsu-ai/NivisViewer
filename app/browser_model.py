from __future__ import annotations

from .i18n import tr


import os
from dataclasses import dataclass, replace
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
from .browser_filter import BrowserFilterState
from .file_operation_artifact import FileOperationArtifactPolicy
from .browser_scanner import BrowserScanEntry, scan_entry_from_dir_entry
from .image_source import ARCHIVE_EXTENSIONS, PDF_EXTENSIONS, SUPPORTED_EXTENSIONS
from .zippla_filename_metadata import zippla_display_name


BROWSER_IMAGE_EXTENSIONS = SUPPORTED_EXTENSIONS
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
    rating: int | None = None
    page_count: int | None = None
    created_time_ns: int | None = None
    accessed_time_ns: int | None = None
    online_only: bool = False

    @property
    def thumbnail_revision(self) -> tuple[object, ...]:
        """The scanned content identity, excluding read/access metadata."""
        return (
            self.kind,
            self.file_size,
            self.modified_time_ns if self.modified_time_ns is not None else self.modified_at,
            self.created_time_ns,
        )

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
                error=tr('フォルダを読み込めません: {p0} ({p1})', p0=target, p1=exc),
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
        rating=entry.rating,
        page_count=entry.page_count,
        created_time_ns=entry.created_time_ns,
        accessed_time_ns=entry.accessed_time_ns,
        online_only=entry.online_only,
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
    RatingRole = PathRole + 15
    RatingPreviewRole = PathRole + 16
    ImageDimensionsRole = PathRole + 17
    PageCountRole = PathRole + 18

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
        self._filter_state = BrowserFilterState()
        self._scan_generation: int | None = None
        self._source_positions: dict[str, list[int]] = {}
        self._icons: dict[str, QIcon] = {}
        self._thumbnail_images: dict[str, QImage] = {}
        self._thumbnail_signatures: dict[str, tuple[int, tuple[object, ...]]] = {}
        self._low_resolution_thumbnails: set[str] = set()
        self._thumbnail_errors: dict[str, str] = {}
        self._preview_statuses: dict[str, str] = {}
        self._cut_keys: frozenset[str] = frozenset()
        self._rating_previews: dict[str, int] = {}
        self._image_dimensions: dict[str, tuple[int, int]] = {}
        self._fallback_icons: dict[BrowserItemKind, QIcon] = {}
        self._row_by_key: dict[str, int] = {}
        self._filter_restore_limit = 0
        self._unfiltered_view: tuple[list[BrowserItem], dict[str, int]] | None = None

    def configure_filter_restore_cache(self, *, enabled: bool, max_entries: int) -> None:
        """Keep at most one current-folder view, sharing immutable items."""
        self._filter_restore_limit = max(0, int(max_entries)) if enabled else 0
        if (
            self._unfiltered_view is not None
            and len(self._unfiltered_view[0]) > self._filter_restore_limit
        ) or not enabled:
            self._unfiltered_view = None

    @property
    def items(self) -> tuple[BrowserItem, ...]:
        return tuple(self._items)

    @property
    def filter_state(self) -> BrowserFilterState:
        return self._filter_state

    @property
    def source_items(self) -> tuple[BrowserItem, ...]:
        return tuple(self._source_items)

    @property
    def source_count(self) -> int:
        return len(self._source_items)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == int(Qt.ItemDataRole.DisplayRole):
            return f"{item.display_name}\n{tr(self._KIND_LABELS[item.kind])}"
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
        if role == self.RatingRole:
            return item.rating
        if role == self.RatingPreviewRole:
            return self._rating_previews.get(self._key(item.path))
        if role == self.ImageDimensionsRole:
            return self._image_dimensions.get(self._key(item.path))
        if role == self.PageCountRole:
            return item.page_count
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
            if item.online_only:
                from .cloud_files import online_only_message
                return f"{item.path}\n{online_only_message()}"
            error = self._thumbnail_errors.get(self._key(item.path))
            return str(item.path) if not error else f"{item.path}\n{error}"
        return None

    def set_items(
        self,
        items: tuple[BrowserItem, ...] | list[BrowserItem],
        *,
        preserve_thumbnails: bool = False,
    ) -> None:
        self.set_sorted_items(
            self.sort_items(items),
            preserve_thumbnails=preserve_thumbnails,
        )

    def sort_items(
        self,
        items: tuple[BrowserItem, ...] | list[BrowserItem],
    ) -> tuple[BrowserItem, ...]:
        source_items = [
            item
            for item in items
            if not FileOperationArtifactPolicy.is_internal_operation_artifact(
                item.path
            )
        ]
        return tuple(self._sort_policy.sorted_items(source_items))

    def visible_items(
        self,
        items: tuple[BrowserItem, ...] | list[BrowserItem],
    ) -> tuple[BrowserItem, ...]:
        source_items = [
            item
            for item in items
            if (
                not FileOperationArtifactPolicy.is_internal_operation_artifact(
                    item.path
                )
                and self._filter_state.matches(item)
            )
        ]
        return tuple(self._sort_policy.sorted_items(source_items))

    def set_sorted_items(
        self,
        items: tuple[BrowserItem, ...] | list[BrowserItem],
        *,
        preserve_thumbnails: bool = False,
    ) -> None:
        self.beginResetModel()
        self._source_items = list(items)
        self._rebuild_source_index()
        # Scanner results supplied here are already in current sort order.
        # Predicates preserve that order and avoid a redundant full sort.
        self._items = [
            item for item in self._source_items if self._filter_state.matches(item)
        ]
        self._icons.clear()
        if preserve_thumbnails:
            self._retain_compatible_thumbnails()
        else:
            self._thumbnail_images.clear()
            self._thumbnail_signatures.clear()
            self._low_resolution_thumbnails.clear()
        self._thumbnail_errors.clear()
        self._preview_statuses.clear()
        self._rating_previews.clear()
        if not preserve_thumbnails:
            self._image_dimensions.clear()
        self._scan_generation = None
        self._rebuild_row_index()
        self.endResetModel()

    def reuse_known_page_counts(
        self,
        items: tuple[BrowserItem, ...] | list[BrowserItem],
    ) -> tuple[BrowserItem, ...]:
        """Carry valid per-item counts across a same-folder refresh.

        Scanner results intentionally stay cheap and therefore start with an
        unknown count.  A count learned by the thumbnail metadata path remains
        valid only while the item's kind, size, and modification fingerprint
        still match the refreshed directory entry.
        """

        known = {
            self._key(item.path): item
            for item in self._source_items
            if item.page_count is not None
        }
        reused: list[BrowserItem] = []
        for item in items:
            previous = known.get(self._key(item.path))
            if (
                item.page_count is None
                and previous is not None
                and previous.thumbnail_revision == item.thumbnail_revision
            ):
                item = replace(item, page_count=previous.page_count)
            reused.append(item)
        return tuple(reused)

    def begin_final_directory_scan(
        self,
        items: tuple[BrowserItem, ...] | list[BrowserItem],
        *,
        generation: int,
    ) -> None:
        self.beginResetModel()
        self._source_items = list(items)
        self._rebuild_source_index()
        self._items = [
            item for item in self._source_items if self._filter_state.matches(item)
        ]
        self._icons.clear()
        self._thumbnail_images.clear()
        self._thumbnail_signatures.clear()
        self._low_resolution_thumbnails.clear()
        self._thumbnail_errors.clear()
        self._preview_statuses.clear()
        self._rating_previews.clear()
        self._image_dimensions.clear()
        self._scan_generation = int(generation)
        self._rebuild_row_index()
        self.endResetModel()

    def append_final_directory_scan(
        self,
        entries: tuple[BrowserItem, ...] | list[BrowserItem],
        *,
        generation: int,
    ) -> int:
        if generation != self._scan_generation:
            return 0
        additions: list[BrowserItem] = []
        addition_keys: list[str] = []
        for entry in entries:
            key = self._key(entry.path)
            if key in self._source_positions:
                continue
            self._source_positions[key] = [len(self._source_items) + len(additions)]
            additions.append(entry)
            addition_keys.append(key)
        if not additions:
            return 0
        self._unfiltered_view = None
        self._source_items.extend(additions)
        visible_pairs = [
            (item, key)
            for item, key in zip(additions, addition_keys)
            if self._filter_state.matches(item)
        ]
        visible_additions = [item for item, _key in visible_pairs]
        if visible_additions:
            first = len(self._items)
            last = first + len(visible_additions) - 1
            self.beginInsertRows(QModelIndex(), first, last)
            self._items.extend(visible_additions)
            for row, (_item, key) in enumerate(visible_pairs, start=first):
                self._row_by_key[key] = row
            self.endInsertRows()
        return len(additions)

    def begin_directory_scan(self, *, generation: int) -> None:
        self._unfiltered_view = None
        self.beginResetModel()
        self._source_items = []
        self._source_positions.clear()
        self._items = []
        self._icons.clear()
        self._thumbnail_images.clear()
        self._thumbnail_signatures.clear()
        self._low_resolution_thumbnails.clear()
        self._thumbnail_errors.clear()
        self._preview_statuses.clear()
        self._rating_previews.clear()
        self._image_dimensions.clear()
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
            if key in self._source_positions:
                continue
            self._source_positions[key] = [len(self._source_items) + len(additions)]
            additions.append(entry)
        if not additions:
            return 0
        self.beginResetModel()
        self._source_items.extend(additions)
        self._items = list(self.visible_items(self._source_items))
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
        random_seed: int = 0,
    ) -> bool:
        policy = BrowserSortPolicy(
            sort_key=normalize_browser_sort_key(sort_key),
            sort_order=normalize_browser_sort_order(sort_order),
            folders_first=bool(folders_first),
            random_seed=random_seed,
        )
        if policy == self._sort_policy:
            return False
        self.beginResetModel()
        self._sort_policy = policy
        self._items = list(self.visible_items(self._source_items))
        self._rebuild_row_index()
        self.endResetModel()
        return True

    def configure_filter(self, state: BrowserFilterState) -> bool:
        normalized = BrowserFilterState.normalized(
            search_text=state.search_text,
            rating_mode=state.rating_mode,
            rating_reference=state.rating_reference,
            include_tags=state.include_tags,
            exclude_tags=state.exclude_tags,
            tag_match=state.tag_match,
        )
        if normalized == self._filter_state:
            return False
        empty = BrowserFilterState.normalized()
        if (
            self._filter_state == empty
            and self._scan_generation is None
            and 0 < len(self._items) <= self._filter_restore_limit
        ):
            self._unfiltered_view = (self._items, self._row_by_key)
        self.beginResetModel()
        self._filter_state = normalized
        if normalized == empty and self._unfiltered_view is not None:
            self._items, self._row_by_key = self._unfiltered_view
            self._unfiltered_view = None
        elif self._unfiltered_view is not None:
            # Only filter transitions may reuse this view. Mutations/sort changes
            # use visible_items and invalidate it, so they cannot read stale rows.
            self._items = [
                item for item in self._unfiltered_view[0] if normalized.matches(item)
            ]
            self._rebuild_row_index(invalidate_filter_cache=False)
        else:
            self._items = list(self.visible_items(self._source_items))
            self._rebuild_row_index(invalidate_filter_cache=False)
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
            (int(request_token), item.thumbnail_revision)
            if request_token is not None
            else None
        )
        # A Shell/disk placeholder is only a bridge until the authoritative
        # thumbnail is ready. Never let a late provisional notification
        # downgrade a final thumbnail already painted for this row.
        if (
            low_resolution
            and key in self._thumbnail_images
            and key not in self._low_resolution_thumbnails
        ):
            return False
        if (
            not low_resolution
            and signature is not None
            and key in self._thumbnail_images
            and key not in self._low_resolution_thumbnails
            and self._thumbnail_signatures.get(key) == signature
        ):
            return False
        self._thumbnail_images[key] = QImage(image)
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

    @property
    def thumbnail_image_bytes(self) -> int:
        """Bytes held by model-owned thumbnail QImages (implicit sharing included)."""
        return sum(int(image.sizeInBytes()) for image in self._thumbnail_images.values())

    def retain_thumbnail_images(self, paths) -> int:
        """Release model QImage references outside the current viewport.

        Provider RAM remains the reusable cache; keeping every old model image
        would pin evicted QImages as the user scrolls through a large folder.
        """
        retained = {self._key(Path(path)) for path in paths}
        removed = 0
        for key in tuple(self._thumbnail_images):
            if key in retained:
                continue
            image = self._thumbnail_images.pop(key)
            removed += int(image.sizeInBytes())
            self._thumbnail_signatures.pop(key, None)
            self._low_resolution_thumbnails.discard(key)
        return removed

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

    def set_rating_preview(
        self,
        path: str | Path | None,
        rating: int | None,
    ) -> bool:
        changed_rows: set[int] = set()
        if path is None:
            keys = tuple(self._rating_previews)
            self._rating_previews.clear()
            changed_rows.update(
                row
                for key in keys
                if (row := self._row_by_key.get(key, -1)) >= 0
            )
        else:
            key = self._key(Path(path))
            row = self._row_by_key.get(key, -1)
            if row < 0:
                return False
            normalized = (
                int(rating)
                if rating is not None and 1 <= int(rating) <= 5
                else None
            )
            previous = self._rating_previews.get(key)
            if normalized is None:
                if key not in self._rating_previews:
                    return False
                self._rating_previews.pop(key, None)
            elif previous == normalized:
                return False
            else:
                self._rating_previews[key] = normalized
            changed_rows.add(row)
        for row in changed_rows:
            index = self.index(row, 0)
            self.dataChanged.emit(index, index, [self.RatingPreviewRole])
        return bool(changed_rows)

    def set_image_dimensions(
        self,
        path: str | Path,
        dimensions: tuple[int, int],
    ) -> bool:
        row = self.row_for_path(path)
        width, height = (max(1, int(value)) for value in dimensions)
        if row < 0:
            return False
        key = self._key(Path(path))
        normalized = (width, height)
        if self._image_dimensions.get(key) == normalized:
            return False
        self._image_dimensions[key] = normalized
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [self.ImageDimensionsRole])
        return True

    def image_dimensions(self, path: str | Path) -> tuple[int, int] | None:
        return self._image_dimensions.get(self._key(Path(path)))

    def set_page_count(self, path: str | Path, page_count: int) -> bool:
        key = self._key(Path(path))
        normalized = max(0, int(page_count))
        changed = False
        # Thumbnail memory hits can publish many counts in one GUI turn.
        # Resolve only this source instead of walking the entire folder per hit.
        for position in self._source_positions.get(key, ()):
            item = self._source_items[position]
            if item.page_count == normalized:
                continue
            self._source_items[position] = replace(
                item,
                page_count=normalized,
            )
            changed = True
            # Counts do not affect filtering or sorting. Patch the retained row
            # directly instead of dropping a large view on every thumbnail hit.
            if self._unfiltered_view is not None:
                saved_items, saved_rows = self._unfiltered_view
                saved_row = saved_rows.get(key)
                if saved_row is not None:
                    saved_items[saved_row] = self._source_items[position]
        row = self._row_by_key.get(key, -1)
        if row >= 0 and self._items[row].page_count != normalized:
            self._items[row] = replace(
                self._items[row],
                page_count=normalized,
            )
            changed = True
            index = self.index(row, 0)
            self.dataChanged.emit(
                index,
                index,
                [self.PageCountRole, self.ItemRole],
            )
        return changed

    def page_count(self, path: str | Path) -> int | None:
        key = self._key(Path(path))
        row = self._row_by_key.get(key, -1)
        if row >= 0:
            return self._items[row].page_count
        positions = self._source_positions.get(key)
        return self._source_items[positions[0]].page_count if positions else None

    def apply_rating_renames(
        self,
        replacements: tuple[tuple[str | Path, str | Path, int | None], ...],
    ) -> bool:
        """Relocate model identities while retaining decoded thumbnails."""

        if not replacements:
            return False
        replacement_by_key = {
            self._key(Path(old)): (Path(new), rating)
            for old, new, rating in replacements
        }
        if len(replacements) > 1:
            stable_rows = self._apply_stable_rating_batch(replacement_by_key)
            if stable_rows is not None:
                self._relocate_rating_caches(replacements)
                for row in stable_rows:
                    index = self.index(row, 0)
                    self.dataChanged.emit(index, index)
                return True
        patched_row: int | None = None
        if len(replacements) == 1:
            old, new, rating = replacements[0]
            old_key = self._key(Path(old))
            new_key = self._key(Path(new))
            positions = self._source_positions.get(old_key, ())
            if len(positions) == 1 and (
                new_key == old_key or new_key not in self._source_positions
            ):
                position = positions[0]
                old_item = self._source_items[position]
                new_item = replace(
                    old_item, path=Path(new),
                    display_name=zippla_display_name(Path(new)), rating=rating,
                )
                row = self._row_by_key.get(old_key)
                if (
                    self._filter_state.matches(new_item) == (row is not None)
                    and self._rename_keeps_order(self._source_items, position, new_item)
                    and (row is None or self._rename_keeps_order(self._items, row, new_item))
                ):
                    self._source_items[position] = new_item
                    self._source_positions.pop(old_key)
                    self._source_positions[new_key] = [position]
                    if row is not None:
                        self._items[row] = new_item
                        self._row_by_key.pop(old_key)
                        self._row_by_key[new_key] = row
                        patched_row = row
                    if self._unfiltered_view is not None:
                        saved_items, saved_rows = self._unfiltered_view
                        saved_row = saved_rows.pop(old_key, None)
                        if saved_row is not None:
                            saved_items[saved_row] = new_item
                            saved_rows[new_key] = saved_row
                    replacement_by_key = {}
        changed = False
        new_source: list[BrowserItem] = []
        new_visible: list[BrowserItem] | None = None
        single_reindex: tuple[str, str, int, int, int | None, int | None] | None = None
        if replacement_by_key and len(replacements) == 1:
            old, new, rating = replacements[0]
            old_key = self._key(Path(old))
            positions = self._source_positions.get(old_key, ())
            new_key = self._key(Path(new))
            if len(positions) == 1 and (
                new_key == old_key or new_key not in self._source_positions
            ):
                new_item = replace(
                    self._source_items[positions[0]],
                    path=Path(new),
                    display_name=zippla_display_name(Path(new)),
                    rating=rating,
                )
                new_source = list(self._source_items)
                new_source.pop(positions[0])
                new_source_position = self._rename_insertion_index(new_source, new_item)
                new_source.insert(new_source_position, new_item)
                new_visible = list(self._items)
                row = self._row_by_key.get(old_key)
                if row is not None:
                    new_visible.pop(row)
                new_row = None
                if self._filter_state.matches(new_item):
                    new_row = self._rename_insertion_index(new_visible, new_item)
                    new_visible.insert(new_row, new_item)
                single_reindex = (
                    old_key, self._key(Path(new)), positions[0],
                    new_source_position, row, new_row,
                )
                changed = True
        if replacement_by_key and not changed:
            for item in self._source_items:
                replacement_value = replacement_by_key.get(self._key(item.path))
                if replacement_value is None:
                    new_source.append(item)
                    continue
                new_path, rating = replacement_value
                new_source.append(
                    replace(
                        item,
                        path=new_path,
                        display_name=zippla_display_name(new_path),
                        rating=rating,
                    )
                )
                changed = True
            if changed:
                new_source = self._sort_policy.sorted_items(new_source)
                new_visible = [
                    item for item in new_source if self._filter_state.matches(item)
                ]
        if not changed and replacement_by_key:
            return False

        self._relocate_rating_caches(replacements)

        if not replacement_by_key:
            if patched_row is not None:
                index = self.index(patched_row, 0)
                self.dataChanged.emit(index, index)
            return True

        # A rating edit can both reorder rows and add/remove a row from the
        # active predicate result.  Publish one reset rather than claiming a
        # layout-only change while the row count changes.  Path-keyed image
        # artifacts above remain intact across this visible-list reset.
        self.beginResetModel()
        self._source_items = new_source
        self._items = new_visible if new_visible is not None else list(self.visible_items(new_source))
        if single_reindex is None:
            self._rebuild_source_index()
            self._rebuild_row_index()
        else:
            old_key, new_key, old_source, new_source_row, old_row, new_row = single_reindex
            self._source_positions = self._renamed_index_positions(
                self._source_positions, old_key, new_key, old_source,
                new_source_row, source=True,
            )
            self._row_by_key = self._renamed_index_positions(
                self._row_by_key, old_key, new_key, old_row, new_row,
                source=False,
            )
            self._unfiltered_view = None
        self.endResetModel()
        return True

    @staticmethod
    def _renamed_index_positions(
        index: dict, old_key: str, new_key: str,
        old_position: int | None, new_position: int | None,
        *, source: bool,
    ) -> dict:
        shifted = {}
        for key, value in index.items():
            if key == old_key:
                continue
            position = value[0] if source else value
            if old_position is not None and position > old_position:
                position -= 1
            if new_position is not None and position >= new_position:
                position += 1
            shifted[key] = [position] if source else position
        if new_position is not None:
            shifted[new_key] = [new_position] if source else new_position
        return shifted

    def _relocate_rating_caches(
        self, replacements: tuple[tuple[str | Path, str | Path, int | None], ...],
    ) -> None:
        for old, new, _rating in replacements:
            old_key = self._key(Path(old))
            new_key = self._key(Path(new))
            self._move_cache_key(self._icons, old_key, new_key)
            self._move_cache_key(self._thumbnail_images, old_key, new_key)
            self._move_cache_key(self._thumbnail_signatures, old_key, new_key)
            self._move_cache_key(self._thumbnail_errors, old_key, new_key)
            self._move_cache_key(self._preview_statuses, old_key, new_key)
            self._move_cache_key(self._image_dimensions, old_key, new_key)
            self._rating_previews.pop(old_key, None)
            if old_key in self._low_resolution_thumbnails:
                self._low_resolution_thumbnails.discard(old_key)
                self._low_resolution_thumbnails.add(new_key)
            if old_key in self._cut_keys:
                self._cut_keys = frozenset(
                    new_key if key == old_key else key
                    for key in self._cut_keys
                )

    def _apply_stable_rating_batch(
        self, replacement_by_key: dict[str, tuple[Path, int | None]],
    ) -> list[int] | None:
        updates: dict[str, BrowserItem] = {}
        for old_key, (new_path, rating) in replacement_by_key.items():
            positions = self._source_positions.get(old_key, ())
            new_key = self._key(new_path)
            if len(positions) != 1 or (new_key != old_key and new_key in self._source_positions):
                return None
            updates[old_key] = replace(
                self._source_items[positions[0]], path=new_path,
                display_name=zippla_display_name(new_path), rating=rating,
            )
        if len({self._key(item.path) for item in updates.values()}) != len(updates):
            return None

        def updated(item: BrowserItem) -> BrowserItem:
            return updates.get(self._key(item.path), item)

        for old_key, item in updates.items():
            position = self._source_positions[old_key][0]
            neighbors = [updated(value) for value in self._source_items[
                max(0, position - 1):position + 2
            ]]
            if self._sort_policy.sorted_items(neighbors) != neighbors:
                return None
            row = self._row_by_key.get(old_key)
            if self._filter_state.matches(item) != (row is not None):
                return None
            if row is not None:
                neighbors = [updated(value) for value in self._items[
                    max(0, row - 1):row + 2
                ]]
                if self._sort_policy.sorted_items(neighbors) != neighbors:
                    return None

        changed_rows: list[int] = []
        for old_key, item in updates.items():
            position = self._source_positions.pop(old_key)[0]
            new_key = self._key(item.path)
            self._source_items[position] = item
            self._source_positions[new_key] = [position]
            row = self._row_by_key.pop(old_key, None)
            if row is not None:
                self._items[row] = item
                self._row_by_key[new_key] = row
                changed_rows.append(row)
            if self._unfiltered_view is not None:
                saved_items, saved_rows = self._unfiltered_view
                saved_row = saved_rows.pop(old_key, None)
                if saved_row is not None:
                    saved_items[saved_row] = item
                    saved_rows[new_key] = saved_row
        return changed_rows

    def _rename_keeps_order(
        self, items: list[BrowserItem], position: int, replacement: BrowserItem,
    ) -> bool:
        neighbors = list(items[max(0, position - 1):position])
        neighbors.append(replacement)
        neighbors.extend(items[position + 1:position + 2])
        return self._sort_policy.sorted_items(neighbors) == neighbors

    def _rename_insertion_index(
        self, items: list[BrowserItem], item: BrowserItem,
    ) -> int:
        low, high = 0, len(items)
        while low < high:
            middle = (low + high) // 2
            if self._sort_policy.sorted_items((items[middle], item))[0] is items[middle]:
                low = middle + 1
            else:
                high = middle
        return low

    @staticmethod
    def _move_cache_key(cache: dict, old_key: str, new_key: str) -> None:
        if old_key == new_key:
            return
        if old_key in cache:
            cache[new_key] = cache.pop(old_key)

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

    def clear_thumbnails_for_preview_kind(self, preview_kind: str) -> int:
        """Drop rendered thumbnails for one provider-owned preview family."""
        wanted = str(preview_kind or "").casefold()
        if not wanted:
            return 0
        keys = {
            self._key(item.path)
            for item in self._source_items
            if str(item.preview_kind or "").casefold() == wanted
        }
        changed = [
            key
            for key in keys
            if (
                key in self._thumbnail_images
                or key in self._thumbnail_signatures
                or key in self._low_resolution_thumbnails
                or key in self._thumbnail_errors
                or key in self._preview_statuses
            )
        ]
        if not changed:
            return 0
        for key in changed:
            self._thumbnail_images.pop(key, None)
            self._thumbnail_signatures.pop(key, None)
            self._low_resolution_thumbnails.discard(key)
            self._thumbnail_errors.pop(key, None)
            self._preview_statuses.pop(key, None)
        if self._items:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._items) - 1, 0),
                [
                    self.ThumbnailImageRole,
                    self.ThumbnailLowResolutionRole,
                    self.ThumbnailErrorRole,
                    self.PreviewStatusRole,
                ],
            )
        return len(changed)

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
            == (int(request_token), item.thumbnail_revision)
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

    def _rebuild_row_index(self, *, invalidate_filter_cache: bool = True) -> None:
        if invalidate_filter_cache:
            self._unfiltered_view = None
        self._row_by_key = {
            self._key(item.path): row for row, item in enumerate(self._items)
        }

    def _rebuild_source_index(self) -> None:
        self._source_positions = {}
        for position, item in enumerate(self._source_items):
            self._source_positions.setdefault(self._key(item.path), []).append(position)

    def _retain_compatible_thumbnails(self) -> None:
        items_by_key = {self._key(item.path): item for item in self._source_items}
        retained_signatures = {
            key: signature
            for key, signature in self._thumbnail_signatures.items()
            if (
                key in self._thumbnail_images
                and key not in self._low_resolution_thumbnails
                and (item := items_by_key.get(key)) is not None
                and signature[1] == item.thumbnail_revision
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
