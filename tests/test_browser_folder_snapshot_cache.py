from __future__ import annotations

from pathlib import Path

from app.browser_folder_snapshot_cache import (
    BrowserFolderSnapshotCache,
    estimate_browser_folder_snapshot_cache_mib,
)
from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_sort import BrowserSortPolicy
from app.browser_visibility import BrowserVisibilityPolicy


def make_item(path: Path) -> BrowserItem:
    return BrowserItem(
        display_name=path.name,
        path=path,
        kind=BrowserItemKind.IMAGE,
        modified_at=1.0,
        file_size=8,
        modified_time_ns=1,
        extension=path.suffix.lower(),
    )


def test_cache_is_bounded_by_total_entries_and_lru() -> None:
    visibility = BrowserVisibilityPolicy()
    sorting = BrowserSortPolicy()
    cache = BrowserFolderSnapshotCache(max_entries=3, max_bytes=10_000_000)
    first = Path("C:/folders/first")
    second = Path("C:/folders/second")

    cache.put(first, visibility, sorting, [make_item(first / "a.jpg")])
    cache.put(
        second,
        visibility,
        sorting,
        [
            make_item(second / "a.jpg"),
            make_item(second / "b.jpg"),
        ],
    )
    assert cache.entry_count == 3
    assert cache.get(first, visibility, sorting) is not None

    third = Path("C:/folders/third")
    cache.put(third, visibility, sorting, [make_item(third / "a.jpg")])

    assert cache.entry_count == 2
    assert cache.get(first, visibility, sorting) is not None
    assert cache.get(second, visibility, sorting) is None
    assert cache.get(third, visibility, sorting) is not None


def test_default_budget_retains_multiple_15k_item_folders() -> None:
    visibility = BrowserVisibilityPolicy()
    sorting = BrowserSortPolicy()
    cache = BrowserFolderSnapshotCache()
    folders = [Path(f"C:/folders/large-{index}") for index in range(4)]

    for folder in folders:
        items = [
            make_item(folder / f"{index:05}.jpg")
            for index in range(15_000)
        ]
        assert cache.put(folder, visibility, sorting, items) is not None

    assert cache.entry_count == 60_000
    assert all(
        cache.get(folder, visibility, sorting) is not None
        for folder in folders
    )


def test_ui_memory_estimates_are_rounded_from_conservative_item_budget() -> None:
    assert [
        estimate_browser_folder_snapshot_cache_mib(limit)
        for limit in (15_000, 30_000, 60_000, 120_000, 240_000)
    ] == [12, 23, 46, 92, 184]


def test_cache_enforces_byte_budget_and_policy_key() -> None:
    path = Path("C:/folders/items")
    sorting = BrowserSortPolicy()
    default_visibility = BrowserVisibilityPolicy()
    hidden_visibility = BrowserVisibilityPolicy(show_hidden_items=False)
    item = make_item(path / "a.jpg")

    too_small = BrowserFolderSnapshotCache(max_entries=10, max_bytes=1)
    assert too_small.put(path, default_visibility, sorting, [item]) is None
    assert too_small.entry_count == 0

    cache = BrowserFolderSnapshotCache(max_entries=10, max_bytes=10_000_000)
    cache.put(path, default_visibility, sorting, [item])
    cache.put(path, hidden_visibility, sorting, [item])
    assert cache.get(path, default_visibility, sorting) is not None
    assert cache.get(path, hidden_visibility, sorting) is not None


def test_cache_is_session_only_and_can_be_disabled() -> None:
    path = Path("C:/folders/items")
    visibility = BrowserVisibilityPolicy()
    sorting = BrowserSortPolicy()
    cache = BrowserFolderSnapshotCache()
    cache.put(path, visibility, sorting, [make_item(path / "a.jpg")])

    cache.set_enabled(False)
    assert cache.get(path, visibility, sorting) is None
    assert cache.entry_count == 0

    cache.set_enabled(True)
    cache.put(path, visibility, sorting, [make_item(path / "a.jpg")])
    cache.discard_path(path)
    assert cache.get(path, visibility, sorting) is None


def test_runtime_limit_update_evicts_oldest_snapshot() -> None:
    visibility = BrowserVisibilityPolicy()
    sorting = BrowserSortPolicy()
    cache = BrowserFolderSnapshotCache(max_entries=3, max_bytes=10_000_000)
    folders = [Path(f"C:/folders/runtime-{index}") for index in range(3)]
    for folder in folders:
        cache.put(folder, visibility, sorting, [make_item(folder / "a.jpg")])

    cache.get(folders[0], visibility, sorting)
    cache.set_max_entries(2)

    assert cache.entry_count == 2
    assert cache.get(folders[0], visibility, sorting) is not None
    assert cache.get(folders[1], visibility, sorting) is None
    assert cache.get(folders[2], visibility, sorting) is not None
