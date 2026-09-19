from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from sys import getsizeof
from typing import TYPE_CHECKING

from .browser_sort import BrowserSortPolicy
from .browser_visibility import BrowserVisibilityPolicy

if TYPE_CHECKING:
    from .browser_model import BrowserItem


DEFAULT_MAX_ENTRIES = 60_000
DEFAULT_MAX_BYTES = 96 * 1024 * 1024
BROWSER_FOLDER_SNAPSHOT_CACHE_ENTRY_LIMITS = (
    15_000,
    30_000,
    60_000,
    120_000,
    240_000,
)
# A representative item is about 792 bytes with _item_estimated_bytes(); use
# the rounded-up value for readable settings labels, not as the safety limit.
_UI_ESTIMATED_ITEM_BYTES = 800


def normalize_browser_folder_snapshot_cache_max_entries(value: object) -> int:
    if (
        type(value) is int
        and value in BROWSER_FOLDER_SNAPSHOT_CACHE_ENTRY_LIMITS
    ):
        return value
    return DEFAULT_MAX_ENTRIES


def estimate_browser_folder_snapshot_cache_mib(max_entries: int) -> int:
    """Return a rounded UI estimate based on the conservative item estimate."""

    estimated_bytes = 1024 + max(0, int(max_entries)) * _UI_ESTIMATED_ITEM_BYTES
    return max(1, ceil(estimated_bytes / (1024 * 1024)))


def _path_key(path: str | Path) -> str:
    return os.path.normcase(
        os.path.abspath(os.path.normpath(os.fspath(path)))
    ).casefold()


def _item_estimated_bytes(item: BrowserItem) -> int:
    """Conservative shallow estimate used only for the session budget.

    The immutable item keeps references to the path and display name, so the
    estimate accounts for those strings and a fixed allowance for dataclass,
    enum, tuple and OrderedDict bookkeeping. It intentionally overestimates
    small entries to keep the cache bounded on large folders.
    """

    path = os.fspath(item.path)
    return (
        512
        + getsizeof(item)
        + getsizeof(item.path)
        + getsizeof(path)
        + getsizeof(item.display_name)
    )


@dataclass(frozen=True)
class BrowserFolderSnapshot:
    path: Path
    visibility_policy: BrowserVisibilityPolicy
    sort_policy: BrowserSortPolicy
    items: tuple[BrowserItem, ...]
    estimated_bytes: int


class BrowserFolderSnapshotCache:
    """Bounded, session-only cache of prepared and sorted folder items."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.enabled = bool(enabled)
        self.max_entries = max(1, int(max_entries))
        self.max_bytes = max(1, int(max_bytes))
        self._entries: OrderedDict[
            tuple[str, BrowserVisibilityPolicy, BrowserSortPolicy],
            BrowserFolderSnapshot,
        ] = OrderedDict()
        self._entry_count = 0
        self._estimated_bytes = 0

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def estimated_bytes(self) -> int:
        return self._estimated_bytes

    def clear(self) -> None:
        self._entries.clear()
        self._entry_count = 0
        self._estimated_bytes = 0

    def set_enabled(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if self.enabled and not normalized:
            self.clear()
        self.enabled = normalized

    def set_max_entries(self, max_entries: int) -> None:
        self.max_entries = max(1, int(max_entries))
        self._evict()

    def get(
        self,
        path: str | Path,
        visibility_policy: BrowserVisibilityPolicy,
        sort_policy: BrowserSortPolicy,
    ) -> BrowserFolderSnapshot | None:
        if not self.enabled:
            return None
        key = (_path_key(path), visibility_policy, sort_policy)
        snapshot = self._entries.get(key)
        if snapshot is None:
            return None
        self._entries.move_to_end(key)
        return snapshot

    def put(
        self,
        path: str | Path,
        visibility_policy: BrowserVisibilityPolicy,
        sort_policy: BrowserSortPolicy,
        items: tuple[BrowserItem, ...] | list[BrowserItem],
    ) -> BrowserFolderSnapshot | None:
        if not self.enabled:
            return None
        normalized_items = tuple(items)
        # Include one bounded allowance for the snapshot/key itself so that a
        # large number of empty folders cannot bypass the byte budget.
        estimated_bytes = 1024 + sum(
            _item_estimated_bytes(item) for item in normalized_items
        )
        key = (_path_key(path), visibility_policy, sort_policy)
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._entry_count -= len(previous.items)
            self._estimated_bytes -= previous.estimated_bytes
        if (
            not normalized_items
            or len(normalized_items) > self.max_entries
            or estimated_bytes > self.max_bytes
        ):
            return None
        snapshot = BrowserFolderSnapshot(
            path=Path(os.path.abspath(os.path.normpath(os.fspath(path)))),
            visibility_policy=visibility_policy,
            sort_policy=sort_policy,
            items=normalized_items,
            estimated_bytes=estimated_bytes,
        )
        self._entries[key] = snapshot
        self._entry_count += len(normalized_items)
        self._estimated_bytes += estimated_bytes
        self._evict()
        return snapshot

    def discard_path(self, path: str | Path) -> None:
        target_key = _path_key(path)
        retained: OrderedDict[
            tuple[str, BrowserVisibilityPolicy, BrowserSortPolicy],
            BrowserFolderSnapshot,
        ] = OrderedDict()
        for key, snapshot in self._entries.items():
            if key[0] == target_key:
                self._entry_count -= len(snapshot.items)
                self._estimated_bytes -= snapshot.estimated_bytes
            else:
                retained[key] = snapshot
        self._entries = retained

    def _evict(self) -> None:
        while (
            self._entries
            and (
                self._entry_count > self.max_entries
                or self._estimated_bytes > self.max_bytes
            )
        ):
            _key, snapshot = self._entries.popitem(last=False)
            self._entry_count -= len(snapshot.items)
            self._estimated_bytes -= snapshot.estimated_bytes
