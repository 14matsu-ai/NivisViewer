from __future__ import annotations

from pathlib import Path
from threading import Event

from PIL import Image
from PySide6.QtWidgets import QApplication

from app.browser_model import BrowserItemDiscovery, BrowserItemKind
from app.browser_scanner import BrowserScanCompleted, BrowserScanRequest, scan_directory


def test_scanner_lists_all_external_archive_extensions_and_uppercase(
    tmp_path: Path,
) -> None:
    names = ("a.rar", "b.CBR", "c.7z", "d.CB7")
    for name in names:
        (tmp_path / name).write_bytes(b"archive")
    batches = []

    result = scan_directory(
        BrowserScanRequest(str(tmp_path), generation=1),
        Event(),
        batches.append,
    )

    assert isinstance(result, BrowserScanCompleted)
    entries = [entry for batch in batches for entry in batch.entries]
    assert {entry.display_name for entry in entries} == set(names)
    assert {entry.item_kind for entry in entries} == {"archive"}


def test_scanner_hides_later_rar_volumes_but_keeps_first_and_normal(
    tmp_path: Path,
) -> None:
    for name in (
        "normal.rar",
        "book.part1.rar",
        "book.part2.rar",
        "book.part03.rar",
        "book.r00",
    ):
        (tmp_path / name).write_bytes(b"archive")
    batches = []

    scan_directory(
        BrowserScanRequest(str(tmp_path), generation=2),
        Event(),
        batches.append,
    )

    assert {
        entry.display_name for batch in batches for entry in batch.entries
    } == {"normal.rar", "book.part1.rar"}


def test_discovery_classifies_external_archives_as_archive_items(
    tmp_path: Path,
) -> None:
    for name in ("a.rar", "b.cbr", "c.7z", "d.cb7"):
        (tmp_path / name).write_bytes(b"x")

    result = BrowserItemDiscovery().discover(tmp_path)

    assert len(result.items) == 4
    assert all(item.kind is BrowserItemKind.ARCHIVE for item in result.items)
