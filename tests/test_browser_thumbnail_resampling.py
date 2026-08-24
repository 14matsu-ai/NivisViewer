from __future__ import annotations

from collections.abc import Callable

import pytest
from PIL import Image, ImageDraw, ImageOps
from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from app.browser_item_delegate import (
    BrowserDisplaySurfaceCache,
    BrowserItemDelegate,
    GRID_PRESET_THUMBNAIL_SIZES,
    prepare_display_thumbnail_surface,
    thumbnail_image_rects,
)
from app.browser_sort import BrowserDisplayDensity
from app.thumbnail_render import ThumbnailRenderSpec, render_pil_thumbnail


REQUIRED_PRESETS = (
    BrowserDisplayDensity.EXTRA_COMPACT,
    BrowserDisplayDensity.COMPACT,
    BrowserDisplayDensity.MEDIUM,
    BrowserDisplayDensity.STANDARD,
    BrowserDisplayDensity.COMFORTABLE,
    BrowserDisplayDensity.LARGE,
)
REQUIRED_DPRS = (1.0, 1.25, 1.5, 2.0)


@pytest.mark.parametrize("density", REQUIRED_PRESETS)
@pytest.mark.parametrize("device_pixel_ratio", REQUIRED_DPRS)
@pytest.mark.parametrize("display_mode", ("fit", "center_crop"))
def test_display_surface_is_exact_physical_target_and_reused(
    qapp,
    density: BrowserDisplayDensity,
    device_pixel_ratio: float,
    display_mode: str,
) -> None:
    logical_edge = GRID_PRESET_THUMBNAIL_SIZES[density]
    delegate = BrowserItemDelegate(
        thumbnail_size=logical_edge,
        density=density,
        frame_ratio_id="portrait_1_sqrt2",
        thumbnail_display_mode=display_mode,
    )
    frame = delegate.grid_metrics.thumbnail_frame_rect(
        delegate.grid_metrics.cell_rect()
    )
    spec = ThumbnailRenderSpec.from_settings(
        logical_edge,
        "portrait_1_sqrt2",
        "letterbox",
        device_pixel_ratio=device_pixel_ratio,
        quality_mode="auto",
        browser_display_mode=display_mode,
    )
    cached = QImage(
        spec.frame_width,
        spec.frame_height,
        QImage.Format.Format_RGB32,
    )
    cached.fill(QColor("white"))
    target, source = thumbnail_image_rects(
        frame,
        cached.size(),
        display_mode,
        device_pixel_ratio=device_pixel_ratio,
    )

    first = delegate._display_surface_cache.prepare(
        cached,
        source,
        target,
        device_pixel_ratio,
    )
    second = delegate._display_surface_cache.prepare(
        cached,
        source,
        target,
        device_pixel_ratio,
    )

    assert first.image.width() == round(target.width() * device_pixel_ratio)
    assert first.image.height() == round(target.height() * device_pixel_ratio)
    assert first.image.devicePixelRatio() == pytest.approx(device_pixel_ratio)
    assert first.prepared_with_resampling
    assert not first.cache_hit
    assert second.cache_hit
    assert not second.prepared_with_resampling
    assert delegate._display_surface_cache.item_count == 1


@pytest.mark.parametrize("device_pixel_ratio", REQUIRED_DPRS)
def test_exact_surface_skips_filtered_preparation_and_final_paint_is_one_to_one(
    qapp,
    device_pixel_ratio: float,
) -> None:
    thumbnail_rect = QRect(8, 8, 128, 128)
    physical_edge = round(120 * device_pixel_ratio)
    cached = QImage(
        physical_edge,
        physical_edge,
        QImage.Format.Format_RGB32,
    )
    for y in range(physical_edge):
        for x in range(physical_edge):
            cached.setPixelColor(x, y, QColor("black" if (x + y) % 2 else "white"))
    target, source = thumbnail_image_rects(
        thumbnail_rect,
        cached.size(),
        "fit",
        device_pixel_ratio=device_pixel_ratio,
    )
    surface = prepare_display_thumbnail_surface(
        cached,
        source,
        target,
        device_pixel_ratio,
    )
    assert not surface.prepared_with_resampling
    assert surface.image.size() == cached.size()

    logical_canvas_edge = 144
    canvas = QImage(
        round(logical_canvas_edge * device_pixel_ratio),
        round(logical_canvas_edge * device_pixel_ratio),
        QImage.Format.Format_RGB32,
    )
    canvas.setDevicePixelRatio(device_pixel_ratio)
    canvas.fill(QColor("red"))
    painter = QPainter(canvas)
    painter.drawImage(target.topLeft(), surface.image)
    painter.end()

    left = round(target.left() * device_pixel_ratio)
    top = round(target.top() * device_pixel_ratio)
    for y in range(physical_edge):
        for x in range(physical_edge):
            assert canvas.pixelColor(left + x, top + y) == cached.pixelColor(x, y)


def test_display_surface_cache_is_bounded_and_dpr_change_discards_old_surfaces(
    qapp,
) -> None:
    cache = BrowserDisplaySurfaceCache(max_items=2, max_bytes=12_000)
    target = QRectF(0, 0, 40, 40)
    source = QRectF(0, 0, 80, 80)
    for color in ("red", "green", "blue"):
        image = QImage(80, 80, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        cache.prepare(image, source, target, 1.0)
    assert cache.item_count <= 2
    assert cache.byte_cost <= 12_000

    replacement = QImage(80, 80, QImage.Format.Format_RGB32)
    replacement.fill(QColor("white"))
    cache.prepare(replacement, source, target, 1.25)
    assert cache.item_count == 1
    assert cache.byte_cost <= 12_000


def test_delegate_repaint_prepares_display_pixels_only_once(
    qapp,
    monkeypatch,
) -> None:
    import app.browser_item_delegate as delegate_module

    calls = 0
    original = delegate_module.prepare_display_thumbnail_surface

    def record_prepare(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        delegate_module,
        "prepare_display_thumbnail_surface",
        record_prepare,
    )
    delegate = BrowserItemDelegate(
        thumbnail_size=149,
        density=BrowserDisplayDensity.MEDIUM,
        frame_ratio_id="portrait_1_sqrt2",
    )
    cached = QImage(226, 320, QImage.Format.Format_RGB32)
    cached.fill(QColor("white"))
    canvas = QImage(240, 240, QImage.Format.Format_RGB32)
    canvas.fill(QColor("black"))
    painter = QPainter(canvas)
    for _ in range(2):
        delegate._paint_thumbnail_image(
            painter,
            QRect(20, 20, 105, 149),
            cached,
            enabled=True,
            display_mode="fit",
        )
    painter.end()

    assert calls == 1
    assert delegate._display_surface_cache.item_count == 1


def _line_art() -> Image.Image:
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    for offset in range(20, 880, 17):
        draw.line((offset, 20, 900 - offset // 2, 1180), fill="black", width=2)
    return image


def _small_text() -> Image.Image:
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    for y in range(20, 1180, 24):
        draw.text((20 + (y % 37), y), f"NivisViewer thumbnail text {y:04d}", fill="black")
    return image


def _photographic_detail() -> Image.Image:
    noise = Image.effect_noise((300, 400), 48).convert("L")
    colored = ImageOps.colorize(noise, "#18304a", "#efc98d")
    return colored.resize((900, 1200), Image.Resampling.BICUBIC)


def _high_contrast_edges() -> Image.Image:
    image = Image.new("RGB", (900, 1200), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((90, 90, 810, 1110), fill="white")
    draw.polygon(((450, 90), (810, 600), (450, 1110), (90, 600)), fill="black")
    return image


def _portrait_illustration() -> Image.Image:
    image = Image.new("RGB", (900, 1200), "#7890b0")
    draw = ImageDraw.Draw(image)
    draw.ellipse((180, 120, 720, 760), fill="#f1c7a3", outline="#302820", width=12)
    draw.ellipse((300, 350, 370, 420), fill="#202020")
    draw.ellipse((530, 350, 600, 420), fill="#202020")
    draw.arc((320, 410, 580, 650), 20, 160, fill="#803030", width=12)
    draw.polygon(((120, 1200), (260, 700), (640, 700), (780, 1200)), fill="#344860")
    return image


QUALITY_FIXTURES: tuple[tuple[str, Callable[[], Image.Image]], ...] = (
    ("line_art", _line_art),
    ("small_text", _small_text),
    ("photographic_detail", _photographic_detail),
    ("high_contrast_edges", _high_contrast_edges),
    ("portrait_illustration", _portrait_illustration),
)


@pytest.mark.parametrize(("_name", "fixture_factory"), QUALITY_FIXTURES)
def test_display_ready_path_matches_previous_single_paint_pixels(
    qapp,
    _name: str,
    fixture_factory: Callable[[], Image.Image],
) -> None:
    dpr = 1.25
    logical_edge = GRID_PRESET_THUMBNAIL_SIZES[BrowserDisplayDensity.MEDIUM]
    delegate = BrowserItemDelegate(
        thumbnail_size=logical_edge,
        density=BrowserDisplayDensity.MEDIUM,
        frame_ratio_id="portrait_1_sqrt2",
        thumbnail_display_mode="fit",
    )
    spec = ThumbnailRenderSpec.from_settings(
        logical_edge,
        "portrait_1_sqrt2",
        "letterbox",
        device_pixel_ratio=dpr,
        quality_mode="auto",
    )
    cached, _crop = render_pil_thumbnail(fixture_factory(), spec)
    thumbnail_rect = delegate.grid_metrics.thumbnail_frame_rect(
        delegate.grid_metrics.cell_rect(12, 12)
    )
    target, source = thumbnail_image_rects(
        thumbnail_rect,
        cached.size(),
        "fit",
        device_pixel_ratio=dpr,
    )
    physical_width = round((thumbnail_rect.right() + 24) * dpr)
    physical_height = round((thumbnail_rect.bottom() + 24) * dpr)

    previous = QImage(physical_width, physical_height, QImage.Format.Format_RGB32)
    previous.setDevicePixelRatio(dpr)
    previous.fill(QColor("#334455"))
    painter = QPainter(previous)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawImage(target, cached, source)
    painter.end()

    prepared = prepare_display_thumbnail_surface(cached, source, target, dpr)
    assert prepared.prepared_with_resampling
    current = QImage(physical_width, physical_height, QImage.Format.Format_RGB32)
    current.setDevicePixelRatio(dpr)
    current.fill(QColor("#334455"))
    painter = QPainter(current)
    painter.drawImage(target.topLeft(), prepared.image)
    painter.end()

    left = round(target.left() * dpr)
    top = round(target.top() * dpr)
    width = round(target.width() * dpr)
    height = round(target.height() * dpr)
    maximum_channel_delta = 0
    for y in range(top, top + height):
        for x in range(left, left + width):
            before = previous.pixelColor(x, y)
            after = current.pixelColor(x, y)
            maximum_channel_delta = max(
                maximum_channel_delta,
                abs(before.red() - after.red()),
                abs(before.green() - after.green()),
                abs(before.blue() - after.blue()),
            )
    assert maximum_channel_delta <= 1
