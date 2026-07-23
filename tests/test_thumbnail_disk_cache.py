from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from PIL import Image
from PySide6.QtGui import QColor, QImage

from app.browser_model import BrowserItem, BrowserItemKind
from app.thumbnail_disk_cache import ThumbnailDiskCache


def write_image(path: Path, *, color: str = "white") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (32, 48), color) as image:
        image.save(path)


def make_item(path: Path, kind: BrowserItemKind = BrowserItemKind.IMAGE) -> BrowserItem:
    return BrowserItem(path.name, path, kind, path.stat().st_mtime)


def thumbnail(color: str = "red", size: int = 80) -> QImage:
    image = QImage(size, size, QImage.Format.Format_RGBA8888)
    image.fill(QColor(color))
    return image


def test_save_reload_no_duplicate_and_unicode_path(tmp_path: Path) -> None:
    source = tmp_path / "日本語画像.jpg"
    write_image(source)
    cache_path = tmp_path / "data" / "thumbnail_cache"
    cache = ThumbnailDiskCache(cache_path)
    item = make_item(source)

    assert cache.put(item, 180, thumbnail())
    assert not cache.put(item, 180, thumbnail())
    assert cache.get(item, 180) is not None
    cache.close()

    reopened = ThumbnailDiskCache(cache_path)
    loaded = reopened.get(item, 180)
    assert loaded is not None and not loaded.isNull()
    assert reopened.usage_bytes() > 0
    reopened.close()


def test_source_change_and_thumbnail_size_invalidate_entry(tmp_path: Path) -> None:
    source = tmp_path / "page.jpg"
    write_image(source)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    item = make_item(source)
    assert cache.put(item, 180, thumbnail())
    assert cache.get(item, 200) is None
    assert cache.put(item, 200, thumbnail("blue"))

    time.sleep(0.01)
    write_image(source, color="black")

    assert cache.get(make_item(source), 180) is None
    assert cache.get(make_item(source), 200) is None
    cache.close()


def test_folder_entry_tracks_actual_cover_file(tmp_path: Path) -> None:
    folder = tmp_path / "book"
    cover = folder / "1.jpg"
    write_image(cover)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    item = make_item(folder, BrowserItemKind.FOLDER)
    assert cache.put(item, 180, thumbnail(), cover_path=cover)
    assert cache.get(item, 180) is not None

    time.sleep(0.01)
    write_image(cover, color="blue")

    assert cache.get(make_item(folder, BrowserItemKind.FOLDER), 180) is None
    cache.close()


def test_prune_removes_old_entries_to_below_ninety_percent(tmp_path: Path) -> None:
    cache = ThumbnailDiskCache(
        tmp_path / "cache",
        limit_bytes=1,
        cleanup_interval=100,
    )
    first = tmp_path / "1.jpg"
    second = tmp_path / "2.jpg"
    write_image(first)
    write_image(second)
    assert cache.put(make_item(first), 180, thumbnail("red"))
    time.sleep(0.01)
    assert cache.put(make_item(second), 180, thumbnail("blue"))

    removed = cache.prune()

    assert removed >= 1
    assert cache.usage_bytes() <= int(cache.limit_bytes * 0.9)
    cache.close()


def test_clear_and_no_temporary_files_remain(tmp_path: Path) -> None:
    source = tmp_path / "page.jpg"
    write_image(source)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    assert cache.put(make_item(source), 180, thumbnail())

    assert cache.clear_all()
    assert cache.usage_bytes() == 0
    assert not list(cache.files_dir.glob("*.tmp"))
    assert not list(cache.files_dir.glob(".*.tmp"))
    cache.close()


def test_hit_access_time_is_flushed_in_batch(tmp_path: Path) -> None:
    source = tmp_path / "page.jpg"
    write_image(source)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    item = make_item(source)
    assert cache.put(item, 180, thumbnail())
    with sqlite3.connect(cache.index_path) as connection:
        before = connection.execute("SELECT last_used FROM entries").fetchone()[0]

    time.sleep(0.01)
    assert cache.get(item, 180) is not None
    with sqlite3.connect(cache.index_path) as connection:
        unflushed = connection.execute("SELECT last_used FROM entries").fetchone()[0]
    assert unflushed == before

    cache.flush_accesses()
    with sqlite3.connect(cache.index_path) as connection:
        flushed = connection.execute("SELECT last_used FROM entries").fetchone()[0]
    assert flushed > before
    cache.close()


def test_unwritable_location_and_corrupt_database_do_not_raise(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    unavailable = ThumbnailDiskCache(blocked)
    assert not unavailable.enabled

    cache_dir = tmp_path / "corrupt"
    cache_dir.mkdir()
    (cache_dir / "index.sqlite3").write_bytes(b"not a database")
    rebuilt = ThumbnailDiskCache(cache_dir)
    assert rebuilt.enabled
    assert rebuilt.usage_bytes() == 0
    rebuilt.close()
