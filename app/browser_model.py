from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from natsort import natsorted
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt
from PySide6.QtGui import QIcon


BROWSER_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
BROWSER_ARCHIVE_EXTENSIONS = {".zip", ".cbz"}


class BrowserItemKind(str, Enum):
    FOLDER = "folder"
    ARCHIVE = "archive"
    IMAGE = "image"


@dataclass(frozen=True)
class BrowserItem:
    display_name: str
    path: Path
    kind: BrowserItemKind
    modified_at: float | None


@dataclass(frozen=True)
class BrowserDiscoveryResult:
    folder: Path
    items: tuple[BrowserItem, ...]
    error: str | None = None


class BrowserItemDiscovery:
    """Lists browser entries without decoding images or opening archives."""

    _KIND_ORDER = {
        BrowserItemKind.FOLDER: 0,
        BrowserItemKind.ARCHIVE: 1,
        BrowserItemKind.IMAGE: 2,
    }

    def discover(self, folder: str | Path) -> BrowserDiscoveryResult:
        target = Path(folder).expanduser()
        try:
            target = target.resolve()
        except OSError:
            target = target.absolute()

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

        ordered = natsorted(
            items,
            key=lambda item: (
                self._KIND_ORDER[item.kind],
                item.display_name.casefold(),
            ),
        )
        return BrowserDiscoveryResult(folder=target, items=tuple(ordered))

    @staticmethod
    def _item_from_entry(entry: os.DirEntry[str]) -> BrowserItem | None:
        name = entry.name
        if BrowserItemDiscovery._is_hidden_or_temporary(entry, name):
            return None

        try:
            if entry.is_dir(follow_symlinks=False):
                kind = BrowserItemKind.FOLDER
            elif entry.is_file(follow_symlinks=False):
                suffix = Path(name).suffix.lower()
                if suffix in BROWSER_ARCHIVE_EXTENSIONS:
                    kind = BrowserItemKind.ARCHIVE
                elif suffix in BROWSER_IMAGE_EXTENSIONS:
                    kind = BrowserItemKind.IMAGE
                else:
                    return None
            else:
                return None
        except OSError:
            return None

        try:
            modified_at = entry.stat(follow_symlinks=False).st_mtime
        except OSError:
            modified_at = None
        return BrowserItem(
            display_name=name,
            path=Path(entry.path).absolute(),
            kind=kind,
            modified_at=modified_at,
        )

    @staticmethod
    def _is_hidden_or_temporary(entry: os.DirEntry[str], name: str) -> bool:
        if name.startswith((".", "~$")) or name.endswith((".tmp", ".part", ".crdownload")):
            return True
        try:
            attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        except OSError:
            return False
        hidden_attribute = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
        return bool(attributes & hidden_attribute)


class BrowserItemModel(QAbstractListModel):
    PathRole = int(Qt.ItemDataRole.UserRole) + 1
    KindRole = PathRole + 1
    ItemRole = PathRole + 2

    _KIND_LABELS = {
        BrowserItemKind.FOLDER: "フォルダ",
        BrowserItemKind.ARCHIVE: "ZIP / CBZ",
        BrowserItemKind.IMAGE: "画像",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[BrowserItem] = []
        self._icons: dict[str, QIcon] = {}
        self._fallback_icons: dict[BrowserItemKind, QIcon] = {}

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
        if role == self.PathRole:
            return str(item.path)
        if role == self.KindRole:
            return item.kind.value
        if role == self.ItemRole:
            return item
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return str(item.path)
        return None

    def set_items(self, items: tuple[BrowserItem, ...] | list[BrowserItem]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self._icons.clear()
        self.endResetModel()

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

    def clear_thumbnails(self) -> None:
        if not self._icons:
            return
        self._icons.clear()
        if self._items:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._items) - 1, 0),
                [int(Qt.ItemDataRole.DecorationRole)],
            )

    def item_at(self, index_or_row: QModelIndex | int) -> BrowserItem | None:
        row = index_or_row.row() if isinstance(index_or_row, QModelIndex) else index_or_row
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def row_for_path(self, path: str | Path) -> int:
        key = self._key(Path(path))
        for row, item in enumerate(self._items):
            if self._key(item.path) == key:
                return row
        return -1

    @staticmethod
    def _key(path: Path) -> str:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        return str(resolved).casefold()
