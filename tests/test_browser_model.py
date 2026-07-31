from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QImage

from app.browser_model import (
    BrowserItem,
    BrowserItemDiscovery,
    BrowserItemKind,
    BrowserItemModel,
)


def test_discovery_lists_supported_items_in_stable_natural_order(tmp_path: Path) -> None:
    (tmp_path / "章2").mkdir()
    (tmp_path / "章10").mkdir()
    for name in (
        "10.jpg",
        "2.jpg",
        "1.png",
        "日本語.webp",
        "scan.tiff",
        "icon.ico",
        "book10.zip",
        "book2.cbz",
    ):
        (tmp_path / name).write_bytes(b"test")
    for name in ("notes.txt", ".hidden.jpg", "download.part"):
        (tmp_path / name).write_bytes(b"ignore")

    result = BrowserItemDiscovery().discover(tmp_path)

    assert result.error is None
    assert [item.display_name for item in result.items] == [
        "章2",
        "章10",
        "1.png",
        "2.jpg",
        "10.jpg",
        "book2.cbz",
        "book10.zip",
        "icon.ico",
        "scan.tiff",
        "日本語.webp",
    ]
    assert [item.kind for item in result.items] == [
        BrowserItemKind.FOLDER,
        BrowserItemKind.FOLDER,
        BrowserItemKind.IMAGE,
        BrowserItemKind.IMAGE,
        BrowserItemKind.IMAGE,
        BrowserItemKind.ARCHIVE,
        BrowserItemKind.ARCHIVE,
        BrowserItemKind.IMAGE,
        BrowserItemKind.IMAGE,
        BrowserItemKind.IMAGE,
    ]
    assert all(item.path.is_absolute() for item in result.items)
    assert all(item.modified_time_ns is not None for item in result.items)
    assert all(
        item.file_size == 4
        for item in result.items
        if item.kind is not BrowserItemKind.FOLDER
    )
    assert all(
        item.file_size is None
        for item in result.items
        if item.kind is BrowserItemKind.FOLDER
    )
    assert result.items[-1].display_name == "日本語.webp"


def test_discovery_returns_error_instead_of_raising(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_scandir = os.scandir

    def inaccessible(path):
        if Path(path) == tmp_path:
            raise PermissionError("denied")
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", inaccessible)

    result = BrowserItemDiscovery().discover(tmp_path)

    assert result.items == ()
    assert result.error is not None
    assert "フォルダを読み込めません" in result.error


def test_stat_failure_keeps_supported_item_with_safe_metadata(
    tmp_path: Path,
) -> None:
    class StatFailureEntry:
        name = "読めない.jpg"
        path = str(tmp_path / name)

        @staticmethod
        def stat(*, follow_symlinks: bool):
            raise PermissionError("denied")

        @staticmethod
        def is_dir(*, follow_symlinks: bool) -> bool:
            return False

        @staticmethod
        def is_file(*, follow_symlinks: bool) -> bool:
            return True

    entry = BrowserItemDiscovery._item_from_entry(StatFailureEntry())

    assert entry is not None
    assert entry.display_name == "読めない.jpg"
    assert entry.modified_at is None
    assert entry.modified_time_ns is None
    assert entry.file_size is None


def test_incremental_model_merges_batches_deduplicates_and_sorts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = BrowserItemModel()
    sort_calls = 0
    original_sorted_items = type(model._sort_policy).sorted_items

    def counted_sorted_items(policy, items):
        nonlocal sort_calls
        sort_calls += 1
        return original_sorted_items(policy, items)

    monkeypatch.setattr(
        type(model._sort_policy),
        "sorted_items",
        counted_sorted_items,
    )
    resets: list[bool] = []
    model.modelReset.connect(lambda: resets.append(True))
    model.begin_directory_scan(generation=4)
    first = BrowserItem(
        "book10.jpg",
        tmp_path / "book10.jpg",
        BrowserItemKind.IMAGE,
        None,
    )
    second = BrowserItem(
        "book2.jpg",
        tmp_path / "book2.jpg",
        BrowserItemKind.IMAGE,
        None,
    )
    folder = BrowserItem(
        "z-folder",
        tmp_path / "z-folder",
        BrowserItemKind.FOLDER,
        None,
    )

    assert model.append_scan_batch([first, second], generation=4) == 2
    assert model.append_scan_batch([first, folder], generation=4) == 1
    assert [entry.display_name for entry in model.items] == [
        "z-folder",
        "book2.jpg",
        "book10.jpg",
    ]
    resets_before_finish = len(resets)
    sorts_before_finish = sort_calls
    assert model.finish_directory_scan(generation=4)
    assert len(model.items) == 3
    assert len(resets) == resets_before_finish
    assert sort_calls == sorts_before_finish
    assert model.append_scan_batch([first], generation=4) == 0
    assert not model.finish_directory_scan(generation=4)


def test_incremental_model_ignores_old_generation_and_accepts_sort_change(
    tmp_path: Path,
) -> None:
    model = BrowserItemModel()
    model.begin_directory_scan(generation=8)
    values = [
        BrowserItem(
            "small.jpg",
            tmp_path / "small.jpg",
            BrowserItemKind.IMAGE,
            None,
            file_size=1,
        ),
        BrowserItem(
            "large.jpg",
            tmp_path / "large.jpg",
            BrowserItemKind.IMAGE,
            None,
            file_size=100,
        ),
    ]

    assert model.append_scan_batch(values[:1], generation=7) == 0
    assert model.append_scan_batch(values[:1], generation=8) == 1
    model.configure_sort("file_size", "descending", False)
    assert model.append_scan_batch(values[1:], generation=8) == 1

    assert [entry.display_name for entry in model.items] == [
        "large.jpg",
        "small.jpg",
    ]
    assert not model.finish_directory_scan(generation=7)


def test_final_scan_appends_pre_sorted_remainder_without_reset_or_resort(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = BrowserItemModel()
    items = [
        BrowserItem(
            f"{index:03}.jpg",
            tmp_path / f"{index:03}.jpg",
            BrowserItemKind.IMAGE,
            None,
        )
        for index in range(100)
    ]
    resets: list[bool] = []
    inserts: list[tuple[int, int]] = []
    model.modelReset.connect(lambda: resets.append(True))
    model.rowsInserted.connect(
        lambda _parent, first, last: inserts.append((first, last))
    )
    monkeypatch.setattr(
        type(model._sort_policy),
        "sorted_items",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("final append must not sort again")
        ),
    )

    model.begin_final_directory_scan(items[:40], generation=11)
    key_calls: list[Path] = []
    original_key = model._key

    def counted_key(path: Path) -> str:
        key_calls.append(path)
        return original_key(path)

    monkeypatch.setattr(model, "_key", counted_key)
    assert model.append_final_directory_scan(
        items[40:],
        generation=11,
    ) == 60

    assert len(key_calls) == 60
    assert resets == [True]
    assert inserts == [(40, 99)]
    assert model.items == tuple(items)
    assert model.finish_directory_scan(generation=11)
    assert model.append_final_directory_scan(
        items[:1],
        generation=11,
    ) == 0


def test_thumbnail_signature_tracks_ready_spec_and_avoids_duplicate_change(
    tmp_path: Path,
) -> None:
    model = BrowserItemModel()
    item = BrowserItem(
        "page.jpg",
        tmp_path / "page.jpg",
        BrowserItemKind.IMAGE,
        1.0,
    )
    model.set_items([item])
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(1)
    changes: list[bool] = []
    model.dataChanged.connect(lambda *_args: changes.append(True))

    assert model.set_thumbnail_image(item.path, image, request_token=101)
    assert model._has_compatible_thumbnail(item, 101)
    assert not model._has_compatible_thumbnail(item, 202)
    assert len(changes) == 1

    assert not model.set_thumbnail_image(item.path, image, request_token=101)
    assert len(changes) == 1

    assert model.set_thumbnail_image(
        item.path,
        image,
        low_resolution=True,
        request_token=101,
    )
    assert not model._has_compatible_thumbnail(item, 101)

    assert model.set_thumbnail_image(item.path, image, request_token=101)
    assert model._has_compatible_thumbnail(item, 101)
    assert model.set_thumbnail_error(item.path, "failed")
    assert not model._has_compatible_thumbnail(item, 101)


def test_thumbnail_model_keeps_cow_snapshot_isolated_from_input_mutation(
    tmp_path: Path,
) -> None:
    model = BrowserItemModel()
    item = BrowserItem(
        "page.jpg",
        tmp_path / "page.jpg",
        BrowserItemKind.IMAGE,
        1.0,
    )
    model.set_items([item])
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(0xFFFF0000)
    original_key = image.cacheKey()

    assert model.set_thumbnail_image(item.path, image, request_token=101)
    stored = model._thumbnail_images[model._key(item.path)]
    assert stored is not image
    assert stored.cacheKey() == original_key

    image.fill(0xFF0000FF)

    assert stored.pixelColor(0, 0).red() == 255
    assert stored.pixelColor(0, 0).blue() == 0
    assert stored.cacheKey() != image.cacheKey()


def test_refresh_retains_only_unchanged_ready_thumbnail_signatures(
    tmp_path: Path,
) -> None:
    model = BrowserItemModel()
    unchanged = BrowserItem(
        "unchanged.jpg",
        tmp_path / "unchanged.jpg",
        BrowserItemKind.IMAGE,
        1.0,
    )
    changed = BrowserItem(
        "changed.jpg",
        tmp_path / "changed.jpg",
        BrowserItemKind.IMAGE,
        1.0,
    )
    removed = BrowserItem(
        "removed.jpg",
        tmp_path / "removed.jpg",
        BrowserItemKind.IMAGE,
        1.0,
    )
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(1)
    model.set_items([unchanged, changed, removed])
    for item in (unchanged, changed, removed):
        assert model.set_thumbnail_image(item.path, image, request_token=101)

    changed_after_refresh = BrowserItem(
        changed.display_name,
        changed.path,
        changed.kind,
        2.0,
    )
    model.set_items(
        [unchanged, changed_after_refresh],
        preserve_thumbnails=True,
    )

    assert model._has_compatible_thumbnail(unchanged, 101)
    assert not model._has_compatible_thumbnail(changed_after_refresh, 101)
    assert model.data(
        model.index(model.row_for_path(changed.path), 0),
        model.ThumbnailImageRole,
    ) is None
    assert model._key(removed.path) not in model._thumbnail_signatures

    model.clear_thumbnails()
    assert not model._has_compatible_thumbnail(unchanged, 101)
