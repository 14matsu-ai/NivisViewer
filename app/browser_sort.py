from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import os
from pathlib import Path
import secrets
from typing import TYPE_CHECKING, Any, Iterable

from natsort import natsort_keygen, ns

from .zippla_filename_metadata import ZipPlaFilenameMetadata

if TYPE_CHECKING:
    from .browser_model import BrowserItem


class BrowserSortKey(str, Enum):
    NAME = "name"
    MODIFIED_TIME = "modified_time"
    ITEM_TYPE = "item_type"
    FILE_SIZE = "file_size"
    RATING = "rating"
    CREATED_TIME = "created_time"
    ACCESSED_TIME = "accessed_time"
    RANDOM = "random"


class BrowserSortOrder(str, Enum):
    ASCENDING = "ascending"
    DESCENDING = "descending"


class BrowserDisplayDensity(str, Enum):
    EXTRA_COMPACT = "extra_compact"
    COMPACT = "compact"
    MEDIUM = "medium"
    STANDARD = "standard"
    COMFORTABLE = "comfortable"
    LARGE = "large"


BROWSER_SORT_KEY_LABELS = {
    BrowserSortKey.ITEM_TYPE: "種類",
    BrowserSortKey.NAME: "名前",
    BrowserSortKey.RATING: "レート",
    BrowserSortKey.CREATED_TIME: "作成日時",
    BrowserSortKey.ACCESSED_TIME: "アクセス日時",
    BrowserSortKey.MODIFIED_TIME: "更新日時",
    BrowserSortKey.FILE_SIZE: "サイズ",
    BrowserSortKey.RANDOM: "ランダム",
}

BROWSER_SORT_ORDER_LABELS = {
    BrowserSortOrder.ASCENDING: "昇順",
    BrowserSortOrder.DESCENDING: "降順",
}

# One catalog for both user-facing selectors; persisted key/order remain separate.
BROWSER_SORT_CHOICES = tuple(
    (f"{label}（{direction}）", key.value, order.value)
    for key, label in BROWSER_SORT_KEY_LABELS.items()
    if key is not BrowserSortKey.RANDOM
    for order, direction in BROWSER_SORT_ORDER_LABELS.items()
) + (("ランダム", BrowserSortKey.RANDOM.value, BrowserSortOrder.ASCENDING.value),)
MAX_BROWSER_RANDOM_SEED = (1 << 64) - 1


def normalize_browser_random_seed(value: object) -> int:
    # Zero is the stable migration/default seed until explicit Random activation.
    if type(value) is int and 0 <= value <= MAX_BROWSER_RANDOM_SEED:
        return value
    return 0


def new_browser_random_seed(previous: object) -> int:
    previous = normalize_browser_random_seed(previous)
    # A different seed is guaranteed, not a different permutation of a tiny list.
    offset = 1 + secrets.randbelow(MAX_BROWSER_RANDOM_SEED)
    return (previous + offset) & MAX_BROWSER_RANDOM_SEED


def browser_sort_choice_index(key: object, order: object) -> int:
    key = normalize_browser_sort_key(key).value
    order = normalize_browser_sort_order(order).value
    if key == BrowserSortKey.RANDOM.value:
        return len(BROWSER_SORT_CHOICES) - 1
    return next(
        i for i, (_, k, o) in enumerate(BROWSER_SORT_CHOICES)
        if (k, o) == (key, order)
    )


def creation_time_ns(stat: object, *, platform: str = os.name) -> int | None:
    """Use captured stat only; POSIX metadata-change time is not birth time."""
    birth = getattr(stat, "st_birthtime_ns", None)
    if birth is not None:
        return birth
    return getattr(stat, "st_ctime_ns", None) if platform == "nt" else None


BROWSER_DISPLAY_DENSITY_LABELS = {
    BrowserDisplayDensity.EXTRA_COMPACT: "極小",
    BrowserDisplayDensity.COMPACT: "コンパクト",
    BrowserDisplayDensity.MEDIUM: "中",
    BrowserDisplayDensity.STANDARD: "標準",
    BrowserDisplayDensity.COMFORTABLE: "ゆったり",
    BrowserDisplayDensity.LARGE: "大",
}

_natural_key = natsort_keygen(alg=ns.IGNORECASE)


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
    random_seed: int = 0

    def sorted_items(self, items: Iterable[BrowserItem]) -> list[BrowserItem]:
        source = list(items)
        if self.sort_key is BrowserSortKey.RANDOM:
            # ZipPlaFork GetRandomIndex principle (AGPL-3.0-or-later), adapted
            # to the canonical parser and deterministic collision tie-breaks.
            # See docs/ZIPPLAFORK_COMPARISON.md section 43. No filesystem I/O.
            seed = str(normalize_browser_random_seed(self.random_seed))

            def random_key(item: BrowserItem) -> tuple[bool, bytes, str, str, str]:
                path = Path(item.path)
                name = ZipPlaFilenameMetadata.parse(path).display_name
                identity = os.path.normcase(os.path.normpath(str(path.parent / name)))
                digest = hashlib.sha256(
                    (identity + "\t" + seed).encode("utf-8", "surrogatepass")
                ).digest()
                return (
                    self.folders_first and item.kind.value != "folder",
                    digest, identity, str(path).casefold(), str(path),
                )

            return sorted(source, key=random_key)
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
        if self.sort_key in {
            BrowserSortKey.CREATED_TIME, BrowserSortKey.ACCESSED_TIME,
        }:
            attribute = (
                "created_time_ns" if self.sort_key is BrowserSortKey.CREATED_TIME
                else "accessed_time_ns"
            )
            value = getattr(item, attribute, None)
            return value if value is not None else -1
        if self.sort_key is BrowserSortKey.MODIFIED_TIME:
            return item.modified_time_ns if item.modified_time_ns is not None else -1
        if self.sort_key is BrowserSortKey.ITEM_TYPE:
            if item.kind.value == "folder":
                return (0, "")
            return (1, (item.extension or Path(item.path).suffix).casefold())
        if self.sort_key is BrowserSortKey.FILE_SIZE:
            return item.file_size if item.file_size is not None else -1
        if self.sort_key is BrowserSortKey.RATING:
            if item.rating is not None:
                return item.rating
            # ZipPlaFork keeps unrated items behind rated items for both sort
            # directions. ``reverse`` is applied by ``sorted`` below.
            return (
                -1
                if self.sort_order is BrowserSortOrder.DESCENDING
                else 6
            )
        return _natural_key(item.display_name)

    @staticmethod
    def _natural_identity_key(item: BrowserItem) -> tuple[Any, str]:
        return _natural_key(item.display_name), str(item.path).casefold()

    @staticmethod
    def _path_identity_key(item: BrowserItem) -> str:
        return str(item.path).casefold()
