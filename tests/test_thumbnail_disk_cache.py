from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtGui import QColor, QImage

from app.browser_model import BrowserItem, BrowserItemKind
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_render import (
    THUMBNAIL_ENCODER_QUALITY, THUMBNAIL_LOSSLESS_ENCODER_EFFORT,
    ThumbnailRenderSpec,
)


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


def test_foreground_put_does_not_prune_or_enumerate_unrelated_cache_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cache = ThumbnailDiskCache(tmp_path / "cache")
    sources = []
    for index in range(40):
        source = tmp_path / f"source-{index}.jpg"
        write_image(source)
        sources.append(source)
        assert cache.put(make_item(source), 120, thumbnail(size=16))

    unrelated = tmp_path / "new-visible.jpg"
    write_image(unrelated)
    calls = 0
    original_is_file = Path.is_file

    def counted_is_file(path: Path) -> bool:
        nonlocal calls
        calls += 1
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", counted_is_file)
    monkeypatch.setattr(
        cache,
        "prune",
        lambda **_kwargs: pytest.fail("foreground put invoked global cleanup"),
    )
    assert cache.put(make_item(unrelated), 120, thumbnail(size=16))
    assert calls <= 1  # At most the requested cache key is checked for an existing file.
    assert cache.statistics()["entry_count"] == 41
    cache.close()


def test_incremental_statistics_triggers_survive_reopen_and_delete(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache"
    cache = ThumbnailDiskCache(cache_path)
    sources = []
    for index in range(3):
        source = tmp_path / f"stats-{index}.jpg"
        write_image(source)
        sources.append(source)
        assert cache.put(make_item(source), 120, thumbnail(size=20))
    with sqlite3.connect(cache.index_path) as connection:
        count, total = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(byte_size), 0) FROM entries"
        ).fetchone()
    assert cache.statistics()["entry_count"] == count == 3
    assert cache.usage_bytes() == total
    cache.close()

    reopened = ThumbnailDiskCache(cache_path)
    assert reopened.statistics()["entry_count"] == 3
    assert reopened.usage_bytes() == total
    assert reopened.clear_all()
    assert reopened.statistics()["entry_count"] == 0
    assert reopened.usage_bytes() == 0
    reopened.close()


def test_automatic_maintenance_scans_a_bounded_resumable_file_batch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import app.thumbnail_disk_cache as module

    cache = ThumbnailDiskCache(tmp_path / "cache")
    for index in range(13):
        source = tmp_path / f"maintenance-{index}.jpg"
        write_image(source)
        assert cache.put(make_item(source), 120, thumbnail(size=16))
    monkeypatch.setattr(module, "CACHE_MAINTENANCE_BATCH", 4)
    calls = 0
    original_is_file = Path.is_file

    def counted_is_file(path: Path) -> bool:
        nonlocal calls
        calls += 1
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", counted_is_file)
    cache.cleanup_if_due(force=True)
    assert calls <= 4
    assert cache.maintenance_pending
    cache.cleanup_if_due()
    assert calls <= 8
    assert cache.maintenance_pending
    cache.close()


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


def test_browser_display_modes_do_not_share_disk_cache_variants(tmp_path: Path) -> None:
    source = tmp_path / "日本語.webp"
    write_image(source)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    item = make_item(source)
    fit = ThumbnailRenderSpec.from_settings(
        180,
        "square_1_1",
        "letterbox",
        browser_display_mode="fit",
    )
    crop = ThumbnailRenderSpec.from_settings(
        180,
        "square_1_1",
        "letterbox",
        browser_display_mode="center_crop",
    )

    assert fit.cache_token != crop.cache_token
    assert fit.family_token != crop.family_token
    legacy_fit = ThumbnailRenderSpec(
        fit.frame_width,
        fit.frame_height,
        fit.frame_ratio_id,
        fit.crop_mode,
        quality_mode=fit.quality_mode,
    )
    assert fit.cache_token == legacy_fit.cache_token
    assert fit.family_token == legacy_fit.family_token
    assert cache.put(item, fit, thumbnail("red"))
    assert cache.get_suitable(item, fit) is not None
    assert cache.get_suitable(item, crop) is None
    assert cache.put(item, crop, thumbnail("blue"))
    assert cache.get_suitable(item, crop) is not None
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


@pytest.mark.parametrize("alpha", [255, 127, 0])
def test_webp_encoding_matches_identity_and_flattens_alpha_by_default(tmp_path, alpha):
    source = tmp_path / "source.png"
    write_image(source)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    try:
        if cache.statistics()["encoder"] != "WEBP":
            pytest.skip("WebP encoder unavailable")
        assert THUMBNAIL_ENCODER_QUALITY == 60
        assert THUMBNAIL_LOSSLESS_ENCODER_EFFORT == 90
        assert cache.format_version == "3-webp-q60-rgb-lossy-v1-matteffffff"
        image = thumbnail()
        image.fill(QColor(23, 89, 177, alpha))
        assert cache.put(make_item(source), 149, image)
        encoded = next(cache.files_dir.glob("*.webp")).read_bytes()
        expected = BytesIO()
        pixels = cache.encoding_policy.prepare_pixels(cache._qimage_to_pil(image))
        pixels.save(expected, format="WEBP", **cache.encoding_policy.webp_options())
        assert encoded == expected.getvalue()
        loaded = cache.get(make_item(source), 149)
        assert loaded is not None
        assert loaded.pixelColor(0, 0).alpha() == 255
    finally:
        cache.close()


@pytest.mark.parametrize("dpr,edge", [(1, 256), (1.25, 320), (1.5, 320), (2, 512)])
@pytest.mark.parametrize("old_quality", [70, 90])
def test_q60_identity_preserves_user_resolution_policy(tmp_path, dpr, edge, old_quality):
    spec = ThumbnailRenderSpec.from_settings(
        149, "portrait_1_sqrt2", "letterbox", device_pixel_ratio=dpr,
        quality_mode="auto", max_edge=512,
    )
    old = replace(spec, encoder_quality=old_quality)
    assert spec.encoder_quality == 60
    assert spec.long_edge == old.long_edge == edge
    assert spec.frame_width == old.frame_width
    assert spec.frame_height == old.frame_height
    assert spec.cache_token != old.cache_token
    assert spec.family_token != old.family_token
    source = tmp_path / "image.png"
    write_image(source)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    try:
        assert cache.put(make_item(source), old, thumbnail())
        assert cache.get_suitable(make_item(source), spec) is None
        assert cache.put(make_item(source), spec, thumbnail())
        assert cache.get_suitable(make_item(source), spec) is not None
        assert cache.get_suitable(make_item(source), old) is not None
    finally:
        cache.close()


@pytest.mark.parametrize("old_quality", [70, 90])
def test_old_quality_transition_is_lazy_bounded_and_reuses_source_metadata(tmp_path, monkeypatch, old_quality):
    import app.thumbnail_disk_cache as module

    spec = ThumbnailRenderSpec.from_settings(149, "portrait_1_sqrt2", "letterbox")
    old_spec = replace(spec, encoder_quality=old_quality)
    items = []
    for number in range(5):
        path = tmp_path / f"book{number}.zip"
        path.write_bytes(b"not an archive: metadata must not enumerate this")
        items.append(make_item(path, BrowserItemKind.ARCHIVE))
    with monkeypatch.context() as legacy:
        old = ThumbnailDiskCache(tmp_path / "cache", cleanup_interval=1000, encoder_quality=old_quality)
        if old_quality == 90:
            legacy.setattr(old, "_format_version_for_policy",
                           lambda _q: f"3-{old._encoder.lower()}-q90-alpha-lossless")
        for item in items:
            assert old.put(item, old_spec, thumbnail(), page_count=42)
        old.close()
    old_files = set((tmp_path / "cache" / "files").iterdir())
    cache = ThumbnailDiskCache(tmp_path / "cache", cleanup_interval=1000)
    try:
        assert set(cache.files_dir.iterdir()) == old_files  # No startup wipe.
        assert cache.get(items[0], old_spec.cache_token) is None
        assert cache.get_suitable(items[0], spec) is None
        with monkeypatch.context() as metadata_only:
            metadata_only.setattr(Image, "open", lambda *_a, **_k: pytest.fail("metadata decoded pixels"))
            assert cache.get_page_count(items[0]) == 42
            assert cache.update_page_count(items[0], 43) == 1
        assert cache.put(items[0], spec, thumbnail())  # Carries count forward.
        assert cache.get_suitable(items[0], spec).page_count == 43
        # A changed source cannot reuse the previous format's metadata.
        items[1].path.write_bytes(b"changed source bytes")
        assert cache.get_page_count(items[1]) is None
        assert cache.update_page_count(items[1], 99) == 0
        assert module.OBSOLETE_FORMAT_PRUNE_BATCH == 128
        monkeypatch.setattr(module, "OBSOLETE_FORMAT_PRUNE_BATCH", 2)
        with monkeypatch.context() as maintenance:
            maintenance.setattr(cache, "_stat_path", lambda *_: pytest.fail("maintenance touched source"))
            maintenance.setattr(Image, "open", lambda *_a, **_k: pytest.fail("maintenance decoded pixels"))
            assert cache.cleanup_if_due(force=True) == 2
            assert len(set(cache.files_dir.iterdir()) & old_files) == 3
            assert cache.cleanup_if_due() == 2  # Resumes the bounded format batch.
            assert cache.prune() == 1
        assert not (set(cache.files_dir.iterdir()) & old_files)
        assert cache.get_page_count(items[0]) == 43
        assert cache.get_suitable(items[0], spec) is not None
    finally:
        cache.close()


def test_png_fallback_remains_lossless(tmp_path, monkeypatch):
    monkeypatch.setattr(ThumbnailDiskCache, "_select_encoder", staticmethod(lambda: ("PNG", "png")))
    source = tmp_path / "image.png"
    write_image(source)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    try:
        assert "-png-q60-" in cache.format_version
        assert cache.put(make_item(source), 149, thumbnail("blue"))
        assert cache.get(make_item(source), 149).pixelColor(0, 0) == QColor("blue")
    finally:
        cache.close()


def test_first_selected_count_reuses_q90_metadata_before_retirement(tmp_path, monkeypatch, qapp):
    import app.thumbnail_disk_cache as module
    from app.thumbnail_provider import BrowserThumbnailProvider

    source = tmp_path / "book.zip"
    source.write_bytes(b"deliberately not a readable archive")
    item = make_item(source, BrowserItemKind.ARCHIVE)
    with monkeypatch.context() as legacy:
        old = ThumbnailDiskCache(tmp_path / "cache", encoder_quality=90)
        legacy.setattr(old, "_format_version_for_policy",
                       lambda _q: f"3-{old._encoder.lower()}-q90-alpha-lossless")
        assert old.put(item, 149, thumbnail(), page_count=42)
        old.close()
    cache = ThumbnailDiskCache(tmp_path / "cache")
    provider = BrowserThumbnailProvider(disk_cache=cache)
    try:
        with monkeypatch.context() as no_source_read:
            no_source_read.setattr("zipfile.ZipFile", lambda *_a, **_k: pytest.fail("re-enumerated source"))
            result = provider._load_page_count_pipeline(item, 149)
        assert result.page_count == 42
        assert cache.statistics()["entry_count"] == 1  # Read is no longer a maintenance barrier.
        provider.cleanup_caches_async(force=False)
        assert provider.wait_for_done(3000)
        assert cache.statistics()["entry_count"] == 0  # Idle maintenance still retires old payloads.
    finally:
        provider.close()
        qapp.processEvents()
