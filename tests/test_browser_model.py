from __future__ import annotations

import os
from pathlib import Path

from app.browser_model import BrowserItemDiscovery, BrowserItemKind


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
