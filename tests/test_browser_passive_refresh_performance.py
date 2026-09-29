from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListView

from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_scanner import scan_entry_from_dir_entry
from app.browser_visibility import BrowserVisibilityPolicy
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager


def _write_png(path: Path, value: int = 80) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (48, 64), (value, 90, 120)) as image:
        image.save(path)


def _wait(qapp, predicate, timeout_ms: int = 5000) -> None:
    for _ in range(max(1, timeout_ms // 10)):
        qapp.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    assert predicate()


def test_passive_filesystem_refresh_keeps_viewport_not_offscreen_selection(
    tmp_path: Path,
    qapp,
) -> None:
    folder = tmp_path / "scroll-refresh"
    for index in range(90):
        _write_png(folder / f"{index:03}.png", 40 + index % 100)

    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(
        {
            "last_browser_path": str(folder),
            "browser_thumbnail_background_screens": 0,
        }
    )
    window = BrowserWindow(config_manager=config)
    window.resize(620, 430)
    window.show()
    assert window.wait_for_scan()

    selection = window.list_view.selectionModel()
    first = window.item_model.index(0, 0)
    selection.select(
        first,
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    selection.setCurrentIndex(
        first,
        QItemSelectionModel.SelectionFlag.NoUpdate,
    )

    target_row = 65
    target = window.item_model.index(target_row, 0)
    window.list_view.scrollTo(target, QListView.ScrollHint.PositionAtTop)
    qapp.processEvents()
    before = window.list_view.verticalScrollBar().value()
    assert before > 0

    # Force the same passive refresh path used by the directory watcher.  The
    # selected row is intentionally far outside the viewport.
    _write_png(folder / "zzz-new.png", 180)
    assert window._refresh_current_folder(
        navigation_source="filesystem_watch"
    )
    assert window.wait_for_scan()
    for _ in range(5):
        qapp.processEvents()
        QTest.qWait(5)

    current = window.item_model.item_at(window.list_view.currentIndex())
    assert current is not None
    assert current.path == folder / "000.png"
    after = window.list_view.verticalScrollBar().value()
    assert abs(after - before) <= max(2, window.list_view.gridSize().height())

    window.close()
    qapp.processEvents()


def test_metadata_only_refresh_can_update_same_rows_without_model_reset(
    tmp_path: Path,
    qapp,
) -> None:
    del qapp
    model = BrowserItemModel()
    first = BrowserItem(
        "a.png",
        tmp_path / "a.png",
        BrowserItemKind.IMAGE,
        1.0,
        file_size=100,
        modified_time_ns=1,
    )
    second = BrowserItem(
        "b.png",
        tmp_path / "b.png",
        BrowserItemKind.IMAGE,
        1.0,
        file_size=200,
        modified_time_ns=1,
    )
    model.set_items([first, second])
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(0xFF223344)
    assert model.set_thumbnail_image(first.path, image, request_token=101)

    resets = []
    changes = []
    model.modelReset.connect(lambda: resets.append(True))
    model.dataChanged.connect(
        lambda top, bottom, _roles=None:
        changes.append((top.row(), bottom.row()))
    )

    changed = replace(
        first,
        modified_at=2.0,
        modified_time_ns=2,
        file_size=140,
    )
    assert model.update_sorted_items_in_place(
        [changed, second],
        preserve_thumbnails=True,
    )

    assert resets == []
    assert model.item_at(0).file_size == 140
    assert changes
    # Revision changed, so the stale thumbnail must not survive just because
    # the row/path identity stayed stable.
    assert model.data(model.index(0, 0), model.ThumbnailImageRole) is None


def test_transient_download_files_are_visible_but_not_previewed(
    tmp_path: Path,
) -> None:
    names = (
        "book.cbz.crdownload",
        "movie.zip.part",
        "image.png.partial",
        "payload.tmp",
        "download.aria2",
    )
    for name in names:
        (tmp_path / name).write_bytes(b"growing")

    policy = BrowserVisibilityPolicy(
        show_hidden_items=True,
        show_unsupported_files=True,
    )
    with os.scandir(tmp_path) as entries:
        scanned = {
            entry.name: scan_entry_from_dir_entry(entry, policy)
            for entry in entries
        }

    for name in names:
        item = scanned[name]
        assert item is not None
        assert item.item_kind == "other"
        assert not item.openable_by_nivisviewer
        assert not item.can_generate_preview
        assert item.preview_kind == ""
