from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import adjacent_book_search as navigation
from app.adjacent_book_search import (
    AdjacentBookBrowserSnapshot as Snapshot,
    AdjacentBookSnapshotEntry as Entry,
    AdjacentBookSearchStatus as Status,
)
from app.application_controller import ApplicationController


def _entry(path, kind="image", openable=True):
    return Entry(str(path), kind, ".jpg", "unused-sort-key", 123, 456, openable)


def test_captured_mixed_order_and_normalized_duplicates_use_first_match(tmp_path):
    first = tmp_path / "日本語.JPG"
    alias = tmp_path / "sub" / ".." / "日本語.jpg"
    folder = tmp_path / "Folder.Name"
    archive = tmp_path / "last.zip"
    snapshot = Snapshot(str(tmp_path), 7, (
        _entry(first), _entry(folder, "folder"), _entry(alias),
        _entry(tmp_path / "skip.txt", "other"),
        _entry(tmp_path / "disabled.jpg", openable=False),
        _entry(archive, "archive"),
    ), "rating:descending", "search=kept")
    paths = tuple(map(navigation.lexical_absolute, (first, folder, alias, archive)))
    assert snapshot.viewer_paths == paths
    assert snapshot.viewer_paths is snapshot.viewer_paths
    assert snapshot.adjacent_viewer_path(alias, 1) == (Status.FOUND, paths[1])
    assert snapshot.adjacent_viewer_path(alias, 0) == (Status.FOUND, paths[1])
    assert snapshot.adjacent_viewer_path(alias, -9) == (Status.BOUNDARY, None)
    assert snapshot.adjacent_viewer_path(alias, -1, loop=True) == (Status.FOUND, paths[-1])
    assert snapshot.adjacent_viewer_path(archive, 1) == (Status.BOUNDARY, None)
    assert snapshot.adjacent_viewer_path(archive, 1, loop=True) == (Status.FOUND, paths[0])
    assert snapshot.contains_viewer_path(alias)
    assert not snapshot.contains_viewer_path(tmp_path / "disabled.jpg")
    assert not snapshot.contains_viewer_path(tmp_path / "skip.txt")
    listing = ApplicationController._folder_snapshot_from_browser_navigation(snapshot, str(alias))
    assert listing.selected_index == 0
    assert listing.selected_image == paths[0]
    assert listing.image_ids == (paths[0], paths[2])
    assert listing.image_ids is snapshot.image_paths
    assert listing.fingerprints is snapshot.image_fingerprints
    assert listing.fingerprints == ((paths[0], 456, 123), (paths[2], 456, 123))
    assert (listing.generation, listing.sort_identity, listing.filter_identity) == (
        7, "rating:descending", "search=kept",
    )
    assert ApplicationController._folder_snapshot_from_browser_navigation(snapshot, str(archive)) is None


@pytest.mark.parametrize("count", [0, 1])
def test_empty_missing_and_single_item_loop(tmp_path, count):
    path = tmp_path / "single.jpg"
    snapshot = Snapshot(str(tmp_path), 1, (_entry(path),) * count)
    for direction in (-1, 0, 1):
        for loop in (False, True):
            assert snapshot.adjacent_viewer_path(tmp_path / "missing", direction, loop=loop) == (
                Status.UNAVAILABLE, None,
            )
        expected = (Status.FOUND, navigation.lexical_absolute(path)) if count else (Status.UNAVAILABLE, None)
        assert snapshot.adjacent_viewer_path(path, direction, loop=True) == expected
    assert snapshot.adjacent_viewer_path(path, 1) == (
        Status.BOUNDARY if count else Status.UNAVAILABLE, None,
    )


def test_image_index_is_first_match_in_its_own_filtered_domain(tmp_path):
    path = tmp_path / "same.jpg"
    snapshot = Snapshot(str(tmp_path), 1, (
        _entry(path, "archive"), _entry(tmp_path / "other.jpg"), _entry(path),
    ))
    assert snapshot.adjacent_viewer_path(path, 1) == (
        Status.FOUND, navigation.lexical_absolute(tmp_path / "other.jpg"),
    )
    assert snapshot.image_index_for_path(path) == 1


def test_snapshot_dataclass_contract_and_derived_immutability(tmp_path):
    args = (str(tmp_path), 1, (_entry(tmp_path / "image.jpg"),), "sort", "filter")
    snapshot = Snapshot(*args)
    assert snapshot == Snapshot(*args)
    assert hash(snapshot) == hash(args)
    assert repr(snapshot) == (
        f"AdjacentBookBrowserSnapshot(parent_folder={args[0]!r}, scan_generation=1, "
        f"entries={args[2]!r}, sort_identity='sort', filter_identity='filter')"
    )
    for field in fields(snapshot):
        if field.name.startswith("_"):
            assert not any((field.init, field.compare, field.hash, field.repr))
    with pytest.raises(FrozenInstanceError):
        snapshot.scan_generation = 2
    for index in (snapshot._viewer_indexes, snapshot._image_indexes):
        with pytest.raises(TypeError):
            index["invented"] = 3
    newer = replace(snapshot, entries=(_entry(tmp_path / "new.jpg"),))
    assert newer != snapshot
    assert not newer.contains_viewer_path(tmp_path / "image.jpg")
    assert snapshot.contains_viewer_path(tmp_path / "image.jpg")


@pytest.mark.parametrize("count", [1, 1000, 50000])
def test_repeated_navigation_membership_and_image_projection_are_constant_work(monkeypatch, tmp_path, count):
    class CountedEntries(tuple):
        traversals = 0

        def __iter__(self):
            self.traversals += 1
            return super().__iter__()

    entries = CountedEntries(_entry(tmp_path / f"{i}.jpg") for i in range(count))
    normalize = navigation.lexical_absolute
    calls = []

    def counted(path):
        calls.append(path)
        return normalize(path)

    def forbid_filesystem(*args, **kwargs):
        raise AssertionError("snapshot navigation must be lexical only")

    with monkeypatch.context() as patch:
        patch.setattr(navigation, "lexical_absolute", counted)
        patch.setattr(Path, "stat", forbid_filesystem)
        patch.setattr(Path, "resolve", forbid_filesystem)
        patch.setattr(navigation.os, "scandir", forbid_filesystem)
        snapshot = Snapshot(str(tmp_path), 1, entries)
        assert len(calls) == 2 * count
        assert entries.traversals == 1
        calls.clear()
        entries.traversals = 0
        target = entries[count // 2].absolute_path
        for _ in range(25):
            snapshot.adjacent_viewer_path(target, 1, loop=True)
            assert snapshot.contains_viewer_path(target)
            listing = ApplicationController._folder_snapshot_from_browser_navigation(snapshot, target)
            assert listing.image_ids is snapshot.image_paths
            assert listing.fingerprints is snapshot.image_fingerprints
        assert len(calls) == 75  # One target normalization per operation, for every size.
        assert entries.traversals == 0


@pytest.mark.parametrize("location", [None, "empty", "elsewhere"])
def test_browser_location_guard_precedes_membership(location):
    def forbidden(_path):
        raise AssertionError("moved-away/absent Browser must not consult membership")

    snapshot = SimpleNamespace(parent_folder="C:/Books", contains_viewer_path=forbidden)
    browser = None if location is None else SimpleNamespace(
        current_path=None if location == "empty" else "C:/Elsewhere",
    )
    controller = SimpleNamespace(get_browser_window=lambda: browser)
    ApplicationController._synchronize_browser_to_viewer_item(
        controller, SimpleNamespace(browser_navigation_snapshot=snapshot), "C:/Books/one.zip",
    )


def test_browser_sync_no_snapshot_fallback_and_index_membership(tmp_path):
    target = tmp_path / "one.zip"
    fallback = []
    synchronized = []
    browser = SimpleNamespace(
        current_path=tmp_path,
        synchronize_viewer_item=lambda *args, **kwargs: synchronized.append((args, kwargs)),
    )
    controller = SimpleNamespace(
        get_browser_window=lambda: browser, select_path_in_browser=fallback.append,
    )
    window = SimpleNamespace(browser_navigation_snapshot=None)
    sync = ApplicationController._synchronize_browser_to_viewer_item
    sync(controller, window, target)
    assert fallback == [target]
    window.browser_navigation_snapshot = Snapshot(str(tmp_path), 1, (_entry(target, "archive"),))
    sync(controller, window, tmp_path / "not-captured.zip")
    assert not synchronized
    sync(controller, window, target)
    assert synchronized == [((target,), {"expected_parent": str(tmp_path)})]
    assert fallback == [target]
