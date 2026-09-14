from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from app.browser_model import BrowserItemDiscovery, BrowserItemKind
from app.browser_directory_watcher import BrowserDirectoryChange
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider


def write_png(path: Path, color: str, width: int = 32) -> None:
    with Image.new("RGB", (width, 40), color) as image:
        image.save(path, format="PNG")


def wait_until(qapp, predicate, timeout: int = 4000) -> None:
    for _ in range(timeout // 10):
        qapp.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    assert predicate()


def item_for(path: Path):
    return next(item for item in BrowserItemDiscovery().discover(path.parent).items if item.path == path)


def displayed_blue(window: BrowserWindow, path: Path) -> bool:
    row = window.item_model.row_for_path(path)
    image = window.item_model.data(window.item_model.index(row), window.item_model.ThumbnailImageRole)
    return isinstance(image, QImage) and image.pixelColor(image.width() // 2, image.height() // 2).blue() > 200


@pytest.mark.parametrize("change", ["rename", "grow_same_mtime", "rewrite", "replace", "incomplete"])
def test_real_watcher_download_converges_to_visible_pixels(tmp_path, qapp, change):
    folder = tmp_path / "ダウンロード"
    folder.mkdir()
    final = folder / "完成.png"
    original = folder / "完成.png.crdownload" if change == "rename" else final
    if change == "incomplete":
        original.write_bytes(b"incomplete")
    else:
        write_png(original, "red")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    calls = []

    def decode(item, size):
        calls.append((item.path, item.thumbnail_revision))
        if item.kind is not BrowserItemKind.IMAGE:
            return None
        return BrowserThumbnailProvider.load_thumbnail(item, size)

    provider = BrowserThumbnailProvider(loader=decode, disk_cache_enabled=False)
    window = BrowserWindow(config_manager=config, thumbnail_provider=provider)
    window.resize(500, 360)
    window.show()
    try:
        assert window.wait_for_scan()
        if change != "rename":
            wait_until(qapp, lambda: bool(calls) and provider.pending_count == 0)
        assert window.item_model.row_for_path(original) >= 0
        before = original.stat()
        if change == "rename":
            assert window.items[0].kind is BrowserItemKind.OTHER
            write_png(original, "blue")
            original.rename(final)
        elif change == "replace":
            replacement = folder / "replacement.tmp"
            write_png(replacement, "blue", 80)
            os.replace(replacement, final)
        else:
            write_png(final, "blue", 80 if change == "grow_same_mtime" else 32)
            if change == "grow_same_mtime":
                # Model and failure-cache identity must include size.
                with final.open("ab") as stream:
                    stream.write(b"padding")
                os.utime(final, ns=(before.st_atime_ns, before.st_mtime_ns))
        wait_until(qapp, lambda: displayed_blue(window, final))
        assert window.item_model.row_for_path(original) == (-1 if change == "rename" else 0)
        assert window.items[0].kind is BrowserItemKind.IMAGE
        assert window.items[0].file_size == final.stat().st_size
    finally:
        window.close()
        provider.close()
        qapp.processEvents()


def test_failed_decode_retries_after_size_change_with_same_mtime(tmp_path, qapp):
    path = tmp_path / "途中.png"
    path.write_bytes(b"incomplete")
    old = item_for(path)
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    try:
        generation = provider.begin_generation()
        provider.request(old, 80, generation=generation)
        assert provider.wait_for_done(2000)
        qapp.processEvents()
        assert provider._failed
        write_png(path, "blue")
        os.utime(path, ns=(old.modified_time_ns, old.modified_time_ns))
        current = item_for(path)
        generation = provider.begin_generation()
        provider.request(current, 80, generation=generation)
        assert provider.wait_for_done(2000)
        qapp.processEvents()
        key = (provider._path_key(path), 80, current.thumbnail_revision)
        assert provider._cache[key].pixelColor(0, 0).blue() > 200
    finally:
        provider.close()


@pytest.mark.parametrize("success", [False, True])
def test_cancelled_old_decode_cannot_poison_failure_or_disk_cache(tmp_path, qapp, success):
    path = tmp_path / "changing.png"
    write_png(path, "red")
    old = item_for(path)
    started, release = Event(), Event()
    disk = ThumbnailDiskCache(tmp_path / "cache")

    def decode(item, size):
        image = BrowserThumbnailProvider.load_thumbnail(item, size)
        started.set()
        assert release.wait(3)
        return image if success else None

    provider = BrowserThumbnailProvider(loader=decode, disk_cache=disk)
    try:
        provider.request(old, 80, generation=provider.begin_generation())
        assert started.wait(2)
        write_png(path, "blue", 80)
        provider.begin_generation(retry_failed=True)
        release.set()
        assert provider.wait_for_done(2000)
        qapp.processEvents()
        assert not provider._failed
        assert not provider._cache
        assert disk.get(item_for(path), 80) is None
    finally:
        release.set()
        provider.close()


def test_disk_refuses_old_decode_under_new_source_fingerprint(tmp_path):
    path = tmp_path / "changing.png"
    write_png(path, "red")
    old = item_for(path)
    image = BrowserThumbnailProvider.load_thumbnail(old, 80)
    write_png(path, "blue", 80)
    disk = ThumbnailDiskCache(tmp_path / "cache")
    try:
        assert not disk.put(old, 80, image)
        disk.update_page_count(old, 99)
        assert disk.get(item_for(path), 80) is None
        assert disk.get_page_count(item_for(path)) is None
    finally:
        disk.close()


def test_coalesced_writes_and_metadata_unchanged_failure_recovery(tmp_path, qapp):
    folder = tmp_path / "download"
    folder.mkdir()
    path = folder / "完成.png"
    write_png(path, "blue")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    readable = False
    calls = []

    def decode(item, size):
        calls.append(item.path)
        return BrowserThumbnailProvider.load_thumbnail(item, size) if readable else None

    provider = BrowserThumbnailProvider(loader=decode, disk_cache_enabled=False)
    window = BrowserWindow(config_manager=config, thumbnail_provider=provider)
    window.resize(500, 360)
    window.show()
    try:
        assert window.wait_for_scan()
        wait_until(qapp, lambda: bool(provider._failed) and provider.pending_count == 0)
        calls_before = len(calls)
        # Simulate release of a sharing lock: metadata and contents are unchanged.
        readable = True
        with patch.object(window.scanner, "start", wraps=window.scanner.start) as scan:
            for _ in range(8):
                window.directory_watcher.directory_changed.emit(BrowserDirectoryChange(
                    str(folder), window.directory_watcher.generation
                ))
                QTest.qWait(100)
            assert scan.call_count == 0
            assert len(calls) == calls_before
            wait_until(qapp, lambda: displayed_blue(window, path))
            assert scan.call_count == 1
            assert len(calls) == calls_before + 1
            QTest.qWait(450)
            assert scan.call_count == 1
    finally:
        window.close()
        provider.close()
        qapp.processEvents()
