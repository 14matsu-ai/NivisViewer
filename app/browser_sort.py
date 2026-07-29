from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable

from natsort import natsort_keygen, ns

if TYPE_CHECKING:
    from .browser_model import BrowserItem


class BrowserSortKey(str, Enum):
    NAME = "name"
    MODIFIED_TIME = "modified_time"
    ITEM_TYPE = "item_type"
    FILE_SIZE = "file_size"


class BrowserSortOrder(str, Enum):
    ASCENDING = "ascending"
    DESCENDING = "descending"


class BrowserDisplayDensity(str, Enum):
    EXTRA_COMPACT = "extra_compact"
    COMPACT = "compact"
    STANDARD = "standard"
    COMFORTABLE = "comfortable"
    LARGE = "large"


BROWSER_SORT_KEY_LABELS = {
    BrowserSortKey.NAME: "名前",
    BrowserSortKey.MODIFIED_TIME: "更新日時",
    BrowserSortKey.ITEM_TYPE: "種類",
    BrowserSortKey.FILE_SIZE: "サイズ",
}

BROWSER_SORT_ORDER_LABELS = {
    BrowserSortOrder.ASCENDING: "昇順",
    BrowserSortOrder.DESCENDING: "降順",
}

BROWSER_DISPLAY_DENSITY_LABELS = {
    BrowserDisplayDensity.EXTRA_COMPACT: "極小",
    BrowserDisplayDensity.COMPACT: "コンパクト",
    BrowserDisplayDensity.STANDARD: "標準",
    BrowserDisplayDensity.COMFORTABLE: "ゆったり",
    BrowserDisplayDensity.LARGE: "大",
}

_natural_key = natsort_keygen(alg=ns.IGNORECASE)
_ITEM_TYPE_ORDER = {
    "folder": 0,
    "archive": 1,
    "pdf": 2,
    "image": 3,
    "other": 4,
}


def normalize_browser_sort_key(value: object) -> BrowserSortKey:
    if isinstance(value, BrowserSortKey):
        return value
    try:
        return BrowserSortKey(str(value))
    except ValueError:
        return BrowserSortKey.NAME


def normalize_browser_sort_order(value: object) -> BrowserSortOrder:
    if isinstance(value, BrowserSortOrder):
        return value
    try:
        return BrowserSortOrder(str(value))
    except ValueError:
        return BrowserSortOrder.ASCENDING


def normalize_browser_display_density(value: object) -> BrowserDisplayDensity:
    if isinstance(value, BrowserDisplayDensity):
        return value
    try:
        return BrowserDisplayDensity(str(value))
    except ValueError:
        return BrowserDisplayDensity.STANDARD


@dataclass(frozen=True)
class BrowserSortPolicy:
    sort_key: BrowserSortKey = BrowserSortKey.NAME
    sort_order: BrowserSortOrder = BrowserSortOrder.ASCENDING
    folders_first: bool = True

    def sorted_items(self, items: Iterable[BrowserItem]) -> list[BrowserItem]:
        source = list(items)
        # Name sorting computes the natural key in the primary pass below.
        # Pre-order only by path so equal case-insensitive names retain the
        # existing deterministic path order without generating that expensive
        # key twice. Other primary keys still need natural-name secondary order.
        source.sort(
            key=(
                self._path_identity_key
                if self.sort_key is BrowserSortKey.NAME
                else self._natural_identity_key
            )
        )

        if self.folders_first:
            folders = [item for item in source if item.kind.value == "folder"]
            others = [item for item in source if item.kind.value != "folder"]
            return self._sort_group(folders) + self._sort_group(others)
        return self._sort_group(source)

    def _sort_group(self, items: list[BrowserItem]) -> list[BrowserItem]:
        return sorted(
            items,
            key=self._primary_key,
            reverse=self.sort_order is BrowserSortOrder.DESCENDING,
        )

    def _primary_key(self, item: BrowserItem) -> Any:
        if self.sort_key is BrowserSortKey.MODIFIED_TIME:
            return item.modified_time_ns if item.modified_time_ns is not None else -1
        if self.sort_key is BrowserSortKey.ITEM_TYPE:
            return _ITEM_TYPE_ORDER.get(item.kind.value, len(_ITEM_TYPE_ORDER))
        if self.sort_key is BrowserSortKey.FILE_SIZE:
            return item.file_size if item.file_size is not None else -1
        return _natural_key(item.display_name)

    @staticmethod
    def _natural_identity_key(item: BrowserItem) -> tuple[Any, str]:
        return _natural_key(item.display_name), str(item.path).casefold()

    @staticmethod
    def _path_identity_key(item: BrowserItem) -> str:
        return str(item.path).casefold()
