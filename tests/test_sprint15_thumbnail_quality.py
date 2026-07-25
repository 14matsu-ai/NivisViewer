from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from threading import Event
import time

import pytest
from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage

from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import (
    THUMBNAIL_QUALITY_MARGINS,
    THUMBNAIL_SIZE_BUCKETS,
    ThumbnailRenderPolicy,
    ThumbnailRenderSpec,
    render_pil_thumbnail,
    snap_logical_rect_to_physical_pixels,
)
from scripts.thumbnail_quality_audit import run_audit


@pytest.mark.parametrize("display_size", [96, 128, 180, 240, 320])
@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 1.75, 2.0])
@pytest.mark.parametrize("quality_mode", ["economy", "auto", "high"])
def test_physical_cache_bucket_covers_logical_dpi_request(
    display_size: int,
    dpr: float,
    quality_mode: str,
) -> None:
    policy = ThumbnailRenderPolicy(
        display_size,
        "portrait_1_sqrt2",
        "smart_crop",
        dpr,
        quality_mode,
        2048,
    )
    spec = policy.render_spec()
    required = math.ceil(
        display_size * dpr * THUMBNAIL_QUALITY_MARGINS[quality_mode]
    )

    assert spec.long_edge >= required
    assert spec.long_edge in THUMBNAIL_SIZE_BUCKETS
    assert policy.logical_frame_size.height() == display_size


@pytest.mark.parametrize(
    ("display_size", "dpr", "minimum_bucket"),
    [(180, 2.0, 512), (320, 2.0, 1024), (180, 1.5, 384)],
)
def test_auto_quality_high_dpi_examples(
    display_size: int,
    dpr: float,
    minimum_bucket: int,
) -> None:
    policy = ThumbnailRenderPolicy(
        display_size,
        "portrait_1_sqrt2",
        "smart_crop",
        dpr,
        "auto",
        1024,
    )
    assert policy.render_spec().long_edge >= minimum_bucket


def test_max_edge_caps_cache_without_changing_logical_layout() -> None:
    policy = ThumbnailRenderPolicy(
        320,
        "portrait_1_sqrt2",
        "smart_crop",
        2.0,
        "high",
        768,
    )
    assert policy.logical_frame_size.height() == 320
    assert policy.render_spec().long_edge == 768


@pytest.mark.parametrize("dpr", [1.25, 1.5, 1.75, 2.0])
def test_fractional_dpi_rect_edges_are_physical_pixel_aligned(dpr: float) -> None:
    snapped = snap_logical_rect_to_physical_pixels(
        QRectF(3.3, 7.7, 180.2, 127.4),
        dpr,
    )
    for value in (
        snapped.left(),
        snapped.top(),
        snapped.right(),
        snapped.bottom(),
    ):
        assert value * dpr == pytest.approx(round(value * dpr))


def test_small_source_is_not_upscaled_into_physical_cache() -> None:
    source = Image.new("RGB", (48, 64), "white")
    spec = ThumbnailRenderSpec.from_settings(
        320,
        "portrait_1_sqrt2",
        "center_crop",
        device_pixel_ratio=2.0,
        quality_mode="auto",
    )
    rendered, _crop = render_pil_thumbnail(source, spec)
    assert rendered.width() <= source.width
    assert rendered.height() <= source.height


def test_render_diagnostics_do_not_double_apply_dpr() -> None:
    metrics = ThumbnailRenderPolicy(
        180,
        "portrait_1_sqrt2",
        "smart_crop",
        2.0,
        "auto",
        1024,
    ).diagnostics()
    assert metrics.logical_frame == (127, 180)
    assert metrics.display_pixels == (254, 360)
    assert metrics.cache_pixels == (362, 512)
    assert metrics.qimage_dpr == 1.0
    assert metrics.upscale_factor < 1.0
    assert metrics.resize_count == 1
    assert metrics.smooth_pixmap_transform


def _item(path: Path) -> BrowserItem:
    return BrowserItem(
        path.name,
        path,
        BrowserItemKind.IMAGE,
        path.stat().st_mtime,
    )


def test_memory_low_resolution_placeholder_is_replaced_from_source(
    qapp,
    tmp_path: Path,
) -> None:
    source = tmp_path / "cover.png"
    Image.new("RGB", (800, 1200), "white").save(source)
    decode_edges: list[int] = []

    def loader(_item: BrowserItem, edge: int) -> QImage:
        decode_edges.append(edge)
        return QImage(edge, edge, QImage.Format.Format_RGB888)

    provider = BrowserThumbnailProvider(loader=loader)
    provisional: list[int] = []
    completed: list[int] = []
    provider.thumbnail_provisional.connect(
        lambda _path, _generation, image: provisional.append(image.width())
    )
    provider.thumbnail_ready.connect(
        lambda _path, _generation, image: completed.append(image.width())
    )
    generation = provider.begin_generation()
    low = ThumbnailRenderSpec.from_settings(
        96, "square_1_1", "letterbox", quality_mode="economy"
    )
    high = ThumbnailRenderSpec.from_settings(
        320, "square_1_1", "letterbox", quality_mode="economy"
    )
    assert provider.request(_item(source), low, generation=generation)
    assert provider.wait_for_done(2000)
    qapp.processEvents()
    assert provider.request(_item(source), high, generation=generation)
    qapp.processEvents()
    assert provider.wait_for_done(2000)
    qapp.processEvents()

    assert decode_edges == [96, 320]
    assert provisional == [96]
    assert completed[-1] == 320
    provider.close()


def test_model_replaces_only_the_arriving_thumbnail(tmp_path: Path) -> None:
    first = tmp_path / "1.jpg"
    second = tmp_path / "2.jpg"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    model = BrowserItemModel()
    model.set_items([_item(first), _item(second)])
    low = QImage(96, 96, QImage.Format.Format_RGB888)
    high = QImage(512, 512, QImage.Format.Format_RGB888)

    assert model.set_thumbnail_image(first, low, low_resolution=True)
    assert model.data(model.index(0), model.ThumbnailLowResolutionRole)
    assert model.set_thumbnail_image(first, high, low_resolution=False)
    assert not model.data(model.index(0), model.ThumbnailLowResolutionRole)
    assert model.data(model.index(1), model.ThumbnailImageRole) is None


def test_disk_cache_keeps_multiple_sizes_and_selects_smallest_adequate(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cover.png"
    Image.new("RGB", (800, 1200), "white").save(source)
    item = _item(source)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    small = ThumbnailRenderSpec.from_settings(
        180, "square_1_1", "letterbox", quality_mode="economy"
    )
    medium = ThumbnailRenderSpec.from_settings(
        350, "square_1_1", "letterbox", quality_mode="economy"
    )
    large = ThumbnailRenderSpec.from_settings(
        500, "square_1_1", "letterbox", quality_mode="economy"
    )
    assert cache.put(
        item,
        small,
        QImage(small.long_edge, small.long_edge, QImage.Format.Format_RGB888),
    )
    assert cache.put(
        item,
        medium,
        QImage(medium.long_edge, medium.long_edge, QImage.Format.Format_RGB888),
    )
    assert cache.put(
        item,
        large,
        QImage(large.long_edge, large.long_edge, QImage.Format.Format_RGB888),
    )
    requested = ThumbnailRenderSpec.from_settings(
        300, "square_1_1", "letterbox", quality_mode="economy"
    )

    selected = cache.get_suitable(item, requested)
    assert selected is not None
    assert not selected.low_resolution_placeholder
    assert selected.frame_width == medium.long_edge
    cache.close()


def test_disk_cache_reports_lower_candidate_as_placeholder(tmp_path: Path) -> None:
    source = tmp_path / "cover.png"
    Image.new("RGB", (800, 1200), "white").save(source)
    item = _item(source)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    low = ThumbnailRenderSpec.from_settings(
        96, "square_1_1", "letterbox", quality_mode="economy"
    )
    high = ThumbnailRenderSpec.from_settings(
        500, "square_1_1", "letterbox", quality_mode="economy"
    )
    assert cache.put(
        item,
        low,
        QImage(low.long_edge, low.long_edge, QImage.Format.Format_RGB888),
    )
    selected = cache.get_suitable(item, high)
    assert selected is not None
    assert selected.low_resolution_placeholder
    cache.close()


def test_disk_placeholder_is_delivered_before_source_upgrade_finishes(
    qapp,
    tmp_path: Path,
) -> None:
    source = tmp_path / "cover.png"
    Image.new("RGB", (800, 1200), "white").save(source)
    item = _item(source)
    cache_path = tmp_path / "cache"
    cache = ThumbnailDiskCache(cache_path)
    low = ThumbnailRenderSpec.from_settings(
        96, "square_1_1", "letterbox", quality_mode="economy"
    )
    high = ThumbnailRenderSpec.from_settings(
        500, "square_1_1", "letterbox", quality_mode="economy"
    )
    assert cache.put(
        item,
        low,
        QImage(low.long_edge, low.long_edge, QImage.Format.Format_RGB888),
    )
    cache.close()
    started = Event()
    release = Event()

    def loader(_item: BrowserItem, edge: int) -> QImage:
        started.set()
        release.wait(2)
        return QImage(edge, edge, QImage.Format.Format_RGB888)

    provider = BrowserThumbnailProvider(
        loader=loader,
        disk_cache=ThumbnailDiskCache(cache_path, enabled=False),
        disk_cache_enabled=True,
    )
    provisional: list[int] = []
    completed: list[int] = []
    provider.thumbnail_provisional.connect(
        lambda _path, _generation, image: provisional.append(image.width())
    )
    provider.thumbnail_ready.connect(
        lambda _path, _generation, image: completed.append(image.width())
    )
    generation = provider.begin_generation()
    try:
        assert provider.request(item, high, generation=generation)
        assert started.wait(1)
        deadline = time.monotonic() + 1
        while not provisional and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert provisional == [low.long_edge]
        assert completed == []
    finally:
        release.set()
    assert provider.wait_for_done(2000)
    qapp.processEvents()
    assert completed == [high.long_edge]
    provider.close()


def test_old_cache_schema_is_a_miss_without_immediate_file_deletion(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"
    files_dir = cache_dir / "files"
    files_dir.mkdir(parents=True)
    legacy_file = files_dir / "legacy.webp"
    legacy_file.write_bytes(b"legacy")
    index = cache_dir / "index.sqlite3"
    with sqlite3.connect(index) as connection:
        connection.execute("CREATE TABLE entries (cache_key TEXT PRIMARY KEY)")
        connection.execute("PRAGMA user_version=1")
    cache = ThumbnailDiskCache(cache_dir)
    assert cache.enabled
    assert legacy_file.exists()
    cache.close()


def test_cache_token_separates_render_policy_components() -> None:
    base = ThumbnailRenderSpec.from_settings(
        180, "portrait_1_sqrt2", "smart_crop", quality_mode="auto"
    )
    assert base.cache_token != ThumbnailRenderSpec.from_settings(
        180, "square_1_1", "smart_crop", quality_mode="auto"
    ).cache_token
    assert base.cache_token != ThumbnailRenderSpec.from_settings(
        180, "portrait_1_sqrt2", "letterbox", quality_mode="auto"
    ).cache_token
    assert base.cache_token != ThumbnailRenderSpec.from_settings(
        180, "portrait_1_sqrt2", "smart_crop", quality_mode="high"
    ).cache_token


def test_browser_dpi_change_retargets_without_resetting_current_item(
    qapp,
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = BrowserWindow(
        config_manager=config,
        restore_initial_location=False,
    )
    source = tmp_path / "cover.png"
    Image.new("RGB", (800, 1200), "white").save(source)
    item = _item(source)
    window.item_model.set_items([item])
    window.item_model.set_thumbnail_image(
        source,
        QImage(256, 256, QImage.Format.Format_RGB888),
    )
    window.list_view.setCurrentIndex(window.item_model.index(0))
    window._thumbnail_dpr = 1.0
    monkeypatch.setattr(window, "_current_device_pixel_ratio", lambda: 2.0)

    window._reevaluate_thumbnail_dpr()

    assert window.thumbnail_render_spec.long_edge == 512
    assert window.list_view.currentIndex().row() == 0
    assert window.item_model.data(
        window.item_model.index(0),
        window.item_model.ThumbnailImageRole,
    ) is not None

    monkeypatch.setattr(window, "_current_device_pixel_ratio", lambda: 1.0)
    window._reevaluate_thumbnail_dpr()
    assert window.thumbnail_render_spec.long_edge == 256
    assert window.list_view.currentIndex().row() == 0
    window.close()
    qapp.processEvents()


def test_browser_quality_setting_is_applied_immediately(
    qapp,
    tmp_path: Path,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = BrowserWindow(
        config_manager=config,
        restore_initial_location=False,
    )
    window._thumbnail_dpr = 1.0
    window.apply_settings(
        {
            "thumbnail_quality_mode": "high",
            "thumbnail_cache_max_edge": 768,
        }
    )
    assert window.thumbnail_quality_mode == "high"
    assert window.thumbnail_cache_max_edge == 768
    assert window.thumbnail_render_spec.long_edge == 384
    window.close()
    qapp.processEvents()


def test_codec_audit_covers_line_photo_gradient_and_rgba() -> None:
    results = run_audit()
    by_key = {(result.sample, result.codec): result for result in results}
    assert {result.sample for result in results} == {
        "line_text",
        "gradient",
        "photo",
        "rgba",
    }
    for sample in ("line_text", "gradient", "photo"):
        assert by_key[(sample, "webp-q90")].psnr >= by_key[
            (sample, "webp-q80")
        ].psnr
    assert by_key[("rgba", "webp-lossless")].alpha_mse == 0
    assert by_key[("rgba", "png")].alpha_mse == 0
