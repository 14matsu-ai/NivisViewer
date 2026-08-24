from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, QRectF, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from app.browser_item_delegate import (
    BROWSER_PLACEHOLDER_ICON_MAX_RATIO,
    GRID_PRESET_THUMBNAIL_SIZES,
    BrowserItemDelegate,
    thumbnail_content_rect,
    thumbnail_image_rects,
    type_badge_rect,
)
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_sort import BrowserDisplayDensity
from app.browser_thumbnail_scheduler import (
    build_thumbnail_request_plan,
    calculate_grid_visible_range,
)
from app.browser_wheel_scroll import BrowserWheelScrollAccumulator
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderPolicy


def _medium_delegate() -> BrowserItemDelegate:
    return BrowserItemDelegate(
        thumbnail_size=149,
        density=BrowserDisplayDensity.MEDIUM,
        frame_ratio_id="portrait_1_sqrt2",
        filename_display="one_line",
    )


@pytest.mark.parametrize(
    ("dpr", "expected_cache_edge"),
    ((1.0, 256), (1.25, 320), (1.5, 320), (2.0, 512)),
)
def test_medium_preset_geometry_and_cache_identity_at_dpi(
    dpr: float,
    expected_cache_edge: int,
) -> None:
    medium_policy = ThumbnailRenderPolicy(
        logical_thumbnail_size=149,
        frame_ratio_id="portrait_1_sqrt2",
        crop_mode="smart_crop",
        device_pixel_ratio=dpr,
        quality_mode="auto",
    )
    medium = medium_policy.render_spec()
    standard = ThumbnailRenderPolicy(
        logical_thumbnail_size=180,
        frame_ratio_id="portrait_1_sqrt2",
        crop_mode="smart_crop",
        device_pixel_ratio=dpr,
        quality_mode="auto",
    ).render_spec()

    assert medium_policy.logical_frame_size == QSize(105, 149)
    assert medium.long_edge == expected_cache_edge
    assert medium.logical_thumbnail_size == 149
    assert medium.cache_token != standard.cache_token
    assert medium.family_token == standard.family_token


def test_medium_preset_keeps_images_placeholders_and_overlays_bounded(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    delegate = _medium_delegate()
    assert GRID_PRESET_THUMBNAIL_SIZES[BrowserDisplayDensity.MEDIUM] == 149
    assert delegate.frame_size == QSize(105, 149)
    assert delegate.profile == BrowserItemDelegate(
        thumbnail_size=128,
        density=BrowserDisplayDensity.COMPACT,
    ).profile

    cell = QRect(QPoint(), delegate.cell_size)
    frame = delegate.grid_metrics.thumbnail_frame_rect(cell)
    content = thumbnail_content_rect(frame, display_mode="fit")
    fit_target, fit_source = thumbnail_image_rects(
        frame,
        QSize(400, 200),
        "fit",
    )
    crop_target, crop_source = thumbnail_image_rects(
        frame,
        QSize(400, 200),
        "center_crop",
    )
    rating = delegate.rating_overlay_rect(cell)
    associated = type_badge_rect(frame, 16)
    title = delegate.grid_metrics.title_rect(cell)

    assert frame.size() == QSize(105, 149)
    assert QRectF(frame).contains(content)
    assert QRectF(frame).contains(fit_target)
    assert QRectF(frame).contains(crop_target)
    assert QRectF(QRect(0, 0, 400, 200)).contains(fit_source)
    assert QRectF(QRect(0, 0, 400, 200)).contains(crop_source)
    assert frame.contains(rating)
    assert rating.height() < frame.height() // 2
    assert frame.contains(associated)
    assert not frame.intersects(title)
    assert content.width() * BROWSER_PLACEHOLDER_ICON_MAX_RATIO < frame.width()
    assert content.height() * BROWSER_PLACEHOLDER_ICON_MAX_RATIO < frame.height()

    palette = QPalette(qapp.palette())
    palette.setColor(QPalette.ColorRole.Base, QColor("white"))
    option = QStyleOptionViewItem()
    option.rect = cell
    option.palette = palette
    option.state = QStyle.StateFlag.State_Enabled

    for kind, name, broken in (
        (BrowserItemKind.FOLDER, "folder", False),
        (BrowserItemKind.ARCHIVE, "broken.cbz", True),
    ):
        model = BrowserItemModel()
        item = BrowserItem(name, tmp_path / name, kind, None)
        model.set_items([item])
        if broken:
            model.set_thumbnail_error(item.path, "broken")
        canvas = QImage(delegate.cell_size, QImage.Format.Format_ARGB32)
        canvas.fill(QColor("white"))
        painter = QPainter(canvas)
        delegate.paint(painter, option, model.index(0, 0))
        painter.end()
        sample = QPoint(round(content.right()) - 2, round(content.top()) + 2)
        assert canvas.pixelColor(sample) == QColor("black")
        outside = QPoint(frame.right() - 2, frame.top() + 2)
        assert canvas.pixelColor(outside) == QColor("white")


def test_medium_grid_drives_wheel_rows_and_bounded_read_ahead(qapp) -> None:
    grid = _medium_delegate().grid_metrics.grid_size
    accumulator = BrowserWheelScrollAccumulator("medium", 3)

    assert accumulator.consume_angle_delta(-120, grid.height()) == 2 * grid.height()

    visible = calculate_grid_visible_range(
        row_count=50_000,
        viewport_width=760,
        viewport_height=520,
        grid_width=grid.width(),
        grid_height=grid.height(),
        vertical_offset=grid.height() * 20,
    )
    assert visible is not None
    plan = build_thumbnail_request_plan(
        row_count=50_000,
        first_visible=visible[0],
        last_visible=visible[1],
        prefetch_screens=1,
        scroll_direction=1,
    )
    visible_count = len(plan.visible_rows)
    assert len(plan.directional_rows) <= visible_count
    assert len(plan.safety_rows) <= (visible_count + 3) // 4
    assert len(plan.requested_rows) < 50_000


def test_medium_preset_applies_live_and_rejects_old_size_publication(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    window = BrowserWindow(
        config_manager=config,
        thumbnail_provider=provider,
        pdfium_service=object(),
        restore_initial_location=False,
    )
    calls = {"request": 0, "scan": 0}
    original_request = provider.request
    original_scan = window.scanner.start

    def record_request(*args, **kwargs):
        calls["request"] += 1
        return original_request(*args, **kwargs)

    def record_scan(*args, **kwargs):
        calls["scan"] += 1
        return original_scan(*args, **kwargs)

    provider.request = record_request  # type: ignore[method-assign]
    window.scanner.start = record_scan  # type: ignore[method-assign]
    old_generation = window._generation
    old_token = window.thumbnail_render_spec.cache_token

    changed = config.apply(
        {
            "browser_display_density": "medium",
            "thumbnail_size": 149,
        }
    )
    qapp.processEvents()

    assert changed["browser_display_density"] == "medium"
    assert changed["thumbnail_size"] == 149
    assert window.browser_display_density is BrowserDisplayDensity.MEDIUM
    assert window.thumbnail_size == 149
    assert window.item_delegate.frame_size == QSize(105, 149)
    assert window.thumbnail_render_spec.cache_token != old_token
    assert window._generation == old_generation + 1
    assert calls == {"request": 0, "scan": 0}

    path = tmp_path / "page.jpg"
    item = BrowserItem(path.name, path, BrowserItemKind.IMAGE, None)
    window.item_model.set_items([item])
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    index = window.item_model.index(0, 0)

    window._on_thumbnail_ready(str(path), old_generation, image)
    assert index.data(BrowserItemModel.ThumbnailImageRole) is None
    window._on_thumbnail_ready(str(path), window._generation, image)
    published = index.data(BrowserItemModel.ThumbnailImageRole)
    assert isinstance(published, QImage) and not published.isNull()

    config.save()
    restored = ConfigManager(config.path).load()
    assert restored["browser_display_density"] == "medium"
    assert restored["thumbnail_size"] == 149
    window.close()
    qapp.processEvents()
