from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QListView, QStyle, QStyleOptionViewItem

from app.browser_item_delegate import (
    BROWSER_FOLDER_FALLBACK_DEFAULT_COLOR,
    BROWSER_PLACEHOLDER_ICON_MAX_RATIO,
    BrowserItemDelegate,
    browser_item_type_key,
    quantize_thumbnail_size,
    thumbnail_content_rect,
    thumbnail_image_rects,
    thumbnail_rect_for_cell,
    type_badge_rect,
)
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_sort import BrowserDisplayDensity
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec


class RecordingThumbnailProvider(BrowserThumbnailProvider):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[tuple[str, int, ThumbnailPriority]] = []
        self.cancelled_prefetch = 0

    def request(
        self,
        item,
        size: int,
        *,
        generation: int | None = None,
        priority: ThumbnailPriority = ThumbnailPriority.VISIBLE,
    ) -> bool:
        del generation
        self.requests.append((str(item.path), size, priority))
        return True

    def cancel_prefetch_except(
        self,
        paths: set[str],
        *,
        size: int,
        generation: int,
    ) -> int:
        del paths, size, generation
        self.cancelled_prefetch += 1
        return 0

    def cancel_requests_except(
        self,
        paths: set[str],
        *,
        size: int,
        generation: int,
    ) -> int:
        return self.cancel_prefetch_except(
            paths,
            size=size,
            generation=generation,
        )


class FixedShellIconProvider:
    def __init__(self, color: str = "#d03030") -> None:
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(color))
        self.icon = QIcon(pixmap)

    def icon_for(self, _item):
        return self.icon


class AlphaShellIconProvider:
    def image_for(
        self,
        _item,
        *,
        logical_size: int,
        device_pixel_ratio: float,
    ) -> QImage:
        size = max(1, round(logical_size * device_pixel_ratio))
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        inset = max(1, size // 4)
        painter = QPainter(image)
        painter.fillRect(
            QRect(inset, inset, size - inset * 2, size - inset * 2),
            QColor("#e03030"),
        )
        painter.end()
        return image


def make_item(path: Path, kind: BrowserItemKind) -> BrowserItem:
    return BrowserItem(path.name, path, kind, None)


def make_window(
    tmp_path: Path,
    qapp,
    *,
    provider: BrowserThumbnailProvider | None = None,
) -> BrowserWindow:
    folder = tmp_path / "一覧"
    folder.mkdir()
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    window = BrowserWindow(
        config_manager=config,
        thumbnail_provider=provider,
    )
    window.resize(760, 520)
    window.show()
    assert window.wait_for_scan()
    qapp.processEvents()
    return window


def test_delegate_uses_fixed_cell_and_uniform_thumbnail_rect(qapp):
    delegate = BrowserItemDelegate(
        thumbnail_size=180,
        density=BrowserDisplayDensity.STANDARD,
    )
    option = QStyleOptionViewItem()
    first = delegate.sizeHint(option, BrowserItemModel().index(0, 0))
    second = delegate.sizeHint(option, BrowserItemModel().index(99, 0))
    assert first == second == QSize(171, 192)

    cell = QRect(0, 0, first.width(), first.height())
    thumbnail = thumbnail_rect_for_cell(
        cell,
        delegate.frame_size,
        delegate.cell_padding,
    )
    assert thumbnail.size() == QSize(127, 180)
    assert thumbnail.left() == (first.width() - 127) // 2


def test_thumbnail_display_modes_keep_ratio_for_wide_tall_and_square_images(qapp):
    frame = QRect(0, 0, 100, 100)
    fit_wide_target, fit_wide_source = thumbnail_image_rects(
        frame,
        QSize(200, 100),
        "fit",
    )
    crop_wide_target, crop_wide_source = thumbnail_image_rects(
        frame,
        QSize(200, 100),
        "center_crop",
    )
    crop_tall_target, crop_tall_source = thumbnail_image_rects(
        frame,
        QSize(100, 200),
        "center_crop",
    )
    crop_square_target, crop_square_source = thumbnail_image_rects(
        frame,
        QSize(100, 100),
        "center_crop",
    )

    assert fit_wide_target.width() == 92
    assert fit_wide_target.height() == 46
    assert fit_wide_source == QRect(0, 0, 200, 100)
    assert crop_wide_target.size() == QSize(98, 98)
    assert crop_wide_source.toRect() == QRect(50, 0, 100, 100)
    assert crop_tall_target.size() == QSize(98, 98)
    assert crop_tall_source.toRect() == QRect(0, 50, 100, 100)
    assert crop_square_target.size() == QSize(98, 98)
    assert crop_square_source.toRect() == QRect(0, 0, 100, 100)


def test_center_crop_delegate_fills_frame_from_image_center(qapp):
    delegate = BrowserItemDelegate(thumbnail_display_mode="center_crop")
    source = QImage(200, 100, QImage.Format.Format_RGB32)
    source.fill(QColor("green"))
    painter_image = QImage(100, 100, QImage.Format.Format_RGB32)
    painter_image.fill(QColor("white"))
    painter = QPainter(painter_image)
    delegate._paint_thumbnail_image(
        painter,
        QRect(0, 0, 100, 100),
        source,
        enabled=True,
    )
    painter.end()

    assert painter_image.pixelColor(2, 2) == QColor("green")
    assert painter_image.pixelColor(97, 97) == QColor("green")


@pytest.mark.parametrize("dark", [False, True])
def test_folder_without_preview_uses_exact_black_thumbnail_rectangle(
    qapp,
    tmp_path,
    dark,
):
    palette = QPalette(qapp.palette())
    if dark:
        palette.setColor(QPalette.ColorRole.Window, QColor("#202124"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#292a2d"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#34363a"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#3c4043"))
        palette.setColor(QPalette.ColorRole.Mid, QColor("#666a70"))
        palette.setColor(QPalette.ColorRole.Midlight, QColor("#4b4f54"))

    delegate = BrowserItemDelegate(
        thumbnail_size=180,
        density=BrowserDisplayDensity.STANDARD,
        shell_icon_provider=FixedShellIconProvider("#d6a928"),
    )
    folder = make_item(tmp_path / "folder", BrowserItemKind.FOLDER)
    image_file = make_item(tmp_path / "page.jpg", BrowserItemKind.IMAGE)
    model = BrowserItemModel()
    model.set_items([folder, image_file])
    fallback = QPixmap(32, 32)
    fallback.fill(QColor("#d6a928"))
    model.set_fallback_icons(
        {
            BrowserItemKind.FOLDER: QIcon(fallback),
            BrowserItemKind.IMAGE: QIcon(fallback),
        }
    )

    option = QStyleOptionViewItem()
    option.rect = QRect(QPoint(), delegate.cell_size)
    option.palette = palette
    option.state = QStyle.StateFlag.State_Enabled
    thumbnail = delegate.grid_metrics.thumbnail_frame_rect(option.rect)
    content = thumbnail_content_rect(thumbnail).toRect()
    # Probe unobscured edges: rating stars occupy the upper left, and the
    # associated icon occupies the lower left of both preview states.
    probe = content.topRight() + QPoint(-1, 1)
    padding_probe = QPoint(content.left() - 1, content.center().y())

    def render(row: int, *, selected: bool = False) -> QImage:
        canvas = QImage(
            delegate.cell_size,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        canvas.fill(palette.window().color())
        option.state = QStyle.StateFlag.State_Enabled
        if selected:
            option.state |= (
                QStyle.StateFlag.State_Selected
                | QStyle.StateFlag.State_MouseOver
                | QStyle.StateFlag.State_HasFocus
            )
        painter = QPainter(canvas)
        delegate.paint(painter, option, model.index(row, 0))
        painter.end()
        return canvas

    def assert_placeholder(row: int, color: str) -> None:
        pending = render(row)
        # Both kinds fill the same square-cornered content rect, not a smaller
        # card. The visible corners/left edge also constrain its full extent.
        for point in (content.topRight(), content.bottomRight(),
                      QPoint(content.left(), content.center().y()), probe):
            assert pending.pixelColor(point) == QColor(color)
        assert pending.pixelColor(padding_probe) == palette.base().color()
        assert pending.pixelColor(thumbnail.topLeft() - QPoint(1, 0)) == palette.window().color()
        assert model.data(model.index(row, 0), model.ThumbnailImageRole) is None
        assert render(row, selected=True).pixelColor(probe) == QColor(color)

    for row, item in enumerate((folder, image_file)):
        assert_placeholder(row, BROWSER_FOLDER_FALLBACK_DEFAULT_COLOR)
        model.set_preview_status(item.path, "loading")
        assert render(row, selected=True).pixelColor(probe) == QColor(
            BROWSER_FOLDER_FALLBACK_DEFAULT_COLOR
        )

    delegate.configure(
        thumbnail_size=180,
        density=BrowserDisplayDensity.STANDARD,
        folder_fallback_background="#804020",
        file_fallback_background="#31597d",
    )
    assert_placeholder(0, "#804020")
    assert_placeholder(1, "#31597d")
    # Reset one field without overwriting the other kind's independent color.
    delegate.configure(
        thumbnail_size=180, density=BrowserDisplayDensity.STANDARD,
        file_fallback_background="auto",
    )
    assert_placeholder(0, "#804020")
    assert_placeholder(1, BROWSER_FOLDER_FALLBACK_DEFAULT_COLOR)
    delegate.configure(
        thumbnail_size=180, density=BrowserDisplayDensity.STANDARD,
        folder_fallback_background="auto",
    )
    assert_placeholder(0, BROWSER_FOLDER_FALLBACK_DEFAULT_COLOR)
    assert_placeholder(1, BROWSER_FOLDER_FALLBACK_DEFAULT_COLOR)

    preview = QImage(
        content.size() * 2,
        QImage.Format.Format_RGB32,
    )
    preview.fill(QColor("#20a050"))
    for row, item in enumerate((folder, image_file)):
        model.set_thumbnail_image(item.path, preview)
        completed = render(row)
        assert completed.pixelColor(thumbnail.center()) == QColor("#20a050")
        assert completed.pixelColor(probe) == QColor("#20a050")
        # Real pixels replace the fallback but cannot spill into frame padding.
        assert completed.pixelColor(padding_probe) == palette.base().color()
        assert render(row, selected=True).pixelColor(thumbnail.center()) == QColor("#20a050")


@pytest.mark.parametrize("device_pixel_ratio", [1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize("background", ["auto", "#31597d"])
@pytest.mark.parametrize("display_mode", ["fit", "center_crop"])
def test_folder_fallback_fill_matches_normal_thumbnail_rect_at_all_dpi(
    qapp,
    device_pixel_ratio: float,
    background: str,
    display_mode: str,
) -> None:
    del qapp
    delegate = BrowserItemDelegate(
        folder_fallback_background=background,
        thumbnail_display_mode=display_mode,
    )
    logical_size = QSize(120, 96)
    physical_size = QSize(
        round(logical_size.width() * device_pixel_ratio),
        round(logical_size.height() * device_pixel_ratio),
    )
    actual = QImage(physical_size, QImage.Format.Format_ARGB32_Premultiplied)
    expected = QImage(physical_size, QImage.Format.Format_ARGB32_Premultiplied)
    actual.setDevicePixelRatio(device_pixel_ratio)
    expected.setDevicePixelRatio(device_pixel_ratio)
    actual.fill(QColor("#d020e0"))
    expected.fill(QColor("#d020e0"))
    thumbnail = QRect(17, 13, 73, 61)

    content = thumbnail_content_rect(
        thumbnail,
        display_mode=display_mode,
        device_pixel_ratio=device_pixel_ratio,
    )
    matching_image_size = QSize(
        round(content.width() * device_pixel_ratio),
        round(content.height() * device_pixel_ratio),
    )
    real_target, _source = thumbnail_image_rects(
        thumbnail,
        matching_image_size,
        display_mode,
        device_pixel_ratio=device_pixel_ratio,
    )
    assert content == real_target
    painter = QPainter(actual)
    delegate._paint_placeholder_canvas(painter, content)
    painter.end()
    painter = QPainter(expected)
    painter.fillRect(
        content,
        QColor(
            BROWSER_FOLDER_FALLBACK_DEFAULT_COLOR
            if background == "auto"
            else background
        ),
    )
    painter.end()

    assert actual == expected


@pytest.mark.parametrize("background", ["auto", "#31597d"])
def test_broken_archive_uses_shared_placeholder_canvas(
    qapp,
    tmp_path: Path,
    background: str,
) -> None:
    delegate = BrowserItemDelegate(
        thumbnail_size=180,
        density=BrowserDisplayDensity.STANDARD,
        file_fallback_background=background,
        shell_icon_provider=AlphaShellIconProvider(),
    )
    archive = make_item(tmp_path / "broken.cbz", BrowserItemKind.ARCHIVE)
    model = BrowserItemModel()
    model.set_items([archive])
    fallback = QPixmap(30, 20)
    fallback.fill(QColor("#d6a928"))
    model.set_fallback_icons({BrowserItemKind.ARCHIVE: QIcon(fallback)})
    assert model.set_thumbnail_error(archive.path, "unreadable archive")
    option = QStyleOptionViewItem()
    option.rect = QRect(QPoint(), delegate.cell_size)
    option.palette = qapp.palette()
    option.state = QStyle.StateFlag.State_Enabled
    thumbnail = delegate.grid_metrics.thumbnail_frame_rect(option.rect)
    content = thumbnail_content_rect(
        thumbnail,
        display_mode=delegate.thumbnail_display_mode,
    ).toRect()
    expected = QColor(
        BROWSER_FOLDER_FALLBACK_DEFAULT_COLOR
        if background == "auto"
        else background
    )
    canvas = QImage(
        delegate.cell_size,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    canvas.fill(option.palette.window().color())

    painter = QPainter(canvas)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()

    assert delegate._uses_placeholder_canvas(
        archive,
        None,
        "unreadable archive",
    )
    assert canvas.pixelColor(content.bottomRight() - QPoint(2, 2)) == expected
    assert canvas.pixelColor(
        QPoint(thumbnail.left(), thumbnail.center().y())
    ) != expected


@pytest.mark.parametrize("device_pixel_ratio", [1.0, 1.25, 1.5, 2.0])
def test_large_placeholder_icon_is_modestly_sized_and_centered_at_all_dpi(
    qapp,
    tmp_path: Path,
    device_pixel_ratio: float,
) -> None:
    del tmp_path
    delegate = BrowserItemDelegate()
    thumbnail = QRect(11, 7, 81, 67)
    content = thumbnail_content_rect(
        thumbnail,
        display_mode=delegate.thumbnail_display_mode,
        device_pixel_ratio=device_pixel_ratio,
    )
    physical_size = QSize(
        round(110 * device_pixel_ratio),
        round(90 * device_pixel_ratio),
    )
    canvas = QImage(physical_size, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.setDevicePixelRatio(device_pixel_ratio)
    canvas.fill(Qt.GlobalColor.transparent)
    option = QStyleOptionViewItem()
    option.state = QStyle.StateFlag.State_Enabled
    pixmap = QPixmap(30, 18)
    pixmap.fill(QColor("#e0b020"))
    icon = QIcon(pixmap)

    painter = QPainter(canvas)
    delegate._paint_fallback_icon(painter, content, icon, option)
    painter.end()

    opaque = [
        QPoint(x, y)
        for y in range(canvas.height())
        for x in range(canvas.width())
        if canvas.pixelColor(x, y).alpha() > 0
    ]
    assert opaque
    left = min(point.x() for point in opaque) / device_pixel_ratio
    right = (max(point.x() for point in opaque) + 1) / device_pixel_ratio
    top = min(point.y() for point in opaque) / device_pixel_ratio
    bottom = (max(point.y() for point in opaque) + 1) / device_pixel_ratio
    painted_width = right - left
    painted_height = bottom - top
    assert (left + right) / 2 == pytest.approx(content.center().x(), abs=1.0)
    assert (top + bottom) / 2 == pytest.approx(content.center().y(), abs=1.0)
    assert painted_width == pytest.approx(
        content.width() * BROWSER_PLACEHOLDER_ICON_MAX_RATIO,
        abs=1.0,
    )
    assert painted_height <= (
        content.height() * BROWSER_PLACEHOLDER_ICON_MAX_RATIO
        + 1.0 / device_pixel_ratio
    )
    assert painted_width < content.width() - 8
    assert painted_height < content.height() - 8


@pytest.mark.parametrize("device_pixel_ratio", [1.0, 1.25, 1.5, 2.0])
def test_type_icon_keeps_alpha_without_translucent_backing_plate(
    qapp,
    tmp_path: Path,
    device_pixel_ratio: float,
) -> None:
    del qapp
    item = make_item(tmp_path / "book.pdf", BrowserItemKind.PDF)
    delegate = BrowserItemDelegate(shell_icon_provider=AlphaShellIconProvider())
    thumbnail = QRect(12, 9, 80, 80)
    logical_size = QSize(110, 105)
    canvas = QImage(
        QSize(
            round(logical_size.width() * device_pixel_ratio),
            round(logical_size.height() * device_pixel_ratio),
        ),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    canvas.setDevicePixelRatio(device_pixel_ratio)
    canvas.fill(QColor("white"))

    painter = QPainter(canvas)
    delegate._paint_type_icon(painter, thumbnail, item)
    painter.end()

    badge = type_badge_rect(thumbnail, 18)
    outside_icon = QPoint(
        round((badge.left() + 1) * device_pixel_ratio),
        round((badge.top() + 1) * device_pixel_ratio),
    )
    icon_center = QPoint(
        round(badge.center().x() * device_pixel_ratio),
        round(badge.center().y() * device_pixel_ratio),
    )
    assert canvas.pixelColor(outside_icon) == QColor("white")
    assert canvas.pixelColor(icon_center).red() > 180


def test_type_badges_distinguish_supported_item_types_and_are_bottom_left(tmp_path):
    cases = {
        "folder": make_item(tmp_path / "folder", BrowserItemKind.FOLDER),
        "image": make_item(tmp_path / "page.jpg", BrowserItemKind.IMAGE),
        "zip": make_item(tmp_path / "book.cbz", BrowserItemKind.ARCHIVE),
        "rar": make_item(tmp_path / "book.cbr", BrowserItemKind.ARCHIVE),
        "7z": make_item(tmp_path / "book.cb7", BrowserItemKind.ARCHIVE),
        "pdf": make_item(tmp_path / "book.pdf", BrowserItemKind.PDF),
    }
    assert {browser_item_type_key(item) for item in cases.values()} == set(cases)
    thumbnail = QRect(20, 10, 180, 180)
    badge = type_badge_rect(thumbnail)
    assert badge.size() == QSize(16, 16)
    assert badge.left() < thumbnail.center().x()
    assert badge.bottom() > thumbnail.center().y()


def test_badge_paints_at_high_dpi(qapp, tmp_path):
    model = BrowserItemModel()
    item = make_item(tmp_path / "book.pdf", BrowserItemKind.PDF)
    model.set_items([item])
    pixmap = QPixmap(80, 120)
    pixmap.fill(QColor("#eeeeee"))
    model.set_thumbnail(item.path, QIcon(pixmap))
    delegate = BrowserItemDelegate(
        thumbnail_size=180,
        density=BrowserDisplayDensity.STANDARD,
        shell_icon_provider=FixedShellIconProvider(),
    )
    canvas = QImage(448, 476, QImage.Format.Format_ARGB32)
    canvas.setDevicePixelRatio(2.0)
    canvas.fill(QColor("white"))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 224, 238)
    option.palette = qapp.palette()
    option.state = QStyle.StateFlag.State_Enabled
    painter = QPainter(canvas)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()

    thumbnail = thumbnail_rect_for_cell(
        option.rect,
        delegate.frame_size,
        delegate.cell_padding,
    )
    badge = type_badge_rect(thumbnail, 18)
    physical_center = badge.center() * 2
    color = canvas.pixelColor(physical_center)
    assert color.red() > color.blue()


def test_four_density_profiles_keep_selection_with_minimal_scroll(
    tmp_path, qapp
):
    window = make_window(tmp_path, qapp)
    items = [
        make_item(tmp_path / "一覧" / f"book {index:03d}.jpg", BrowserItemKind.IMAGE)
        for index in range(100)
    ]
    window.item_model.set_items(items)
    current = window.item_model.index(55, 0)
    window.list_view.selectionModel().setCurrentIndex(
        current,
        QItemSelectionModel.SelectionFlag.SelectCurrent,
    )
    window.list_view.scrollTo(current, QListView.ScrollHint.PositionAtCenter)
    qapp.processEvents()
    window.config.apply({"browser_display_density": "large"})
    for _ in range(3):
        qapp.processEvents()

    selected = window.item_model.item_at(window.list_view.currentIndex())
    assert selected is not None and selected.path == items[55].path
    assert window.list_view.gridSize() == QSize(231, 195)
    restored_selected = window.item_model.index(
        window.item_model.row_for_path(items[55].path),
        0,
    )
    selected_rect = window.list_view.visualRect(restored_selected)
    viewport_rect = window.list_view.viewport().rect()
    assert viewport_rect.contains(selected_rect)
    assert min(
        abs(selected_rect.top() - viewport_rect.top()),
        abs(selected_rect.bottom() - viewport_rect.bottom()),
    ) <= window.list_view.gridSize().height()
    window.close()
    qapp.processEvents()


def test_same_thumbnail_bucket_reuses_generation(tmp_path, qapp):
    window = make_window(tmp_path, qapp)
    initial = window.thumbnail_provider.generation
    window.config.apply({"thumbnail_size": 250})
    first = window.thumbnail_provider.generation
    window.config.apply({"thumbnail_size": 255})
    second = window.thumbnail_provider.generation

    assert quantize_thumbnail_size(250) == quantize_thumbnail_size(255) == 256
    assert first == initial + 1
    assert second == first
    assert window.list_view.iconSize() == QSize(255, 255)
    window.close()
    qapp.processEvents()


def test_ratio_and_crop_changes_use_2d_cache_generation(tmp_path, qapp):
    window = make_window(tmp_path, qapp)
    initial_generation = window.thumbnail_provider.generation
    initial_height = window.list_view.gridSize().height()

    window.config.apply({"thumbnail_frame_ratio": "landscape_16_9"})
    ratio_generation = window.thumbnail_provider.generation
    landscape_grid = window.list_view.gridSize()
    window.config.apply({"thumbnail_crop_mode": "letterbox"})
    crop_generation = window.thumbnail_provider.generation

    assert ratio_generation == initial_generation + 1
    assert crop_generation == ratio_generation + 1
    assert landscape_grid.width() == 224
    assert landscape_grid.height() < initial_height
    assert window.config.get("thumbnail_frame_ratio") == "landscape_16_9"
    assert window.config.get("thumbnail_crop_mode") == "letterbox"
    window.close()
    qapp.processEvents()


def test_browser_display_mode_repaints_current_images_and_uses_new_cache_variant(
    tmp_path,
    qapp,
):
    window = make_window(tmp_path, qapp)
    item = make_item(tmp_path / "一覧" / "日本語.webp", BrowserItemKind.IMAGE)
    window.item_model.set_items([item])
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(QColor("green"))
    assert window.item_model.set_thumbnail_image(item.path, image)
    initial_generation = window.thumbnail_provider.generation
    initial_token = window.thumbnail_render_spec.cache_token

    window.config.apply({"browser_thumbnail_display_mode": "center_crop"})
    qapp.processEvents()

    assert window.item_delegate.thumbnail_display_mode == "center_crop"
    assert window.thumbnail_render_spec.browser_display_mode == "center_crop"
    assert window.thumbnail_render_spec.cache_token != initial_token
    assert window.thumbnail_provider.generation == initial_generation + 1
    assert isinstance(
        window.item_model.index(0, 0).data(BrowserItemModel.ThumbnailImageRole),
        QImage,
    )
    window.close()
    qapp.processEvents()


def test_folder_fallback_color_change_is_repaint_only(
    tmp_path: Path,
    qapp,
) -> None:
    provider = RecordingThumbnailProvider()
    window = make_window(tmp_path, qapp, provider=provider)
    folder = make_item(tmp_path / "一覧" / "folder", BrowserItemKind.FOLDER)
    window.item_model.set_items([folder])
    window._thumbnail_request_timer.stop()
    provider.requests.clear()
    thumbnail_generation = provider.generation
    scan_generation = window._scan_generation

    window.config.apply({"browser_folder_fallback_background": "#31597d"})
    qapp.processEvents()

    assert window.item_delegate.folder_fallback_background_color() == QColor(
        "#31597d"
    )
    assert provider.generation == thumbnail_generation
    assert window._scan_generation == scan_generation
    assert provider.requests == []
    assert window.item_model.data(
        window.item_model.index(0, 0),
        BrowserItemModel.ThumbnailImageRole,
    ) is None
    window.close()
    qapp.processEvents()


def test_ten_thousand_items_request_only_visible_and_prefetch_ranges(
    tmp_path, qapp
):
    provider = RecordingThumbnailProvider()
    window = make_window(tmp_path, qapp, provider=provider)
    window.item_model.set_items(
        [
            make_item(
                tmp_path / "一覧" / f"book {index:05d}.jpg",
                BrowserItemKind.IMAGE,
            )
            for index in range(10_000)
        ]
    )
    window._request_visible_thumbnails()

    assert provider.requests
    assert len(provider.requests) < 500
    assert len(provider.requests) < window.item_model.rowCount() // 10
    assert any(priority is ThumbnailPriority.VISIBLE for _, _, priority in provider.requests)
    assert {size for _, size, _ in provider.requests} == {
        window.thumbnail_render_spec
    }

    provider.requests.clear()
    cancellations_before = provider.cancelled_prefetch
    window.config.apply({"browser_thumbnail_display_mode": "center_crop"})
    window._fast_scrolling = True
    window._request_visible_thumbnails()
    assert provider.requests
    assert {
        request_size.browser_display_mode
        for _, request_size, _ in provider.requests
    } == {"center_crop"}
    assert all(
        priority is not ThumbnailPriority.PREFETCH
        for _, _, priority in provider.requests
    )
    assert provider.cancelled_prefetch == cancellations_before + 1
    window.close()
    qapp.processEvents()


@pytest.mark.parametrize(
    ("item_count", "expected_requests"),
    [(20, 20), (200, 40), (1000, 40)],
)
def test_ready_thumbnails_are_not_requested_again(
    tmp_path,
    qapp,
    monkeypatch,
    item_count: int,
    expected_requests: int,
) -> None:
    provider = RecordingThumbnailProvider()
    window = make_window(tmp_path, qapp, provider=provider)
    items = [
        BrowserItem(
            f"{index}.jpg",
            tmp_path / "一覧" / f"{index}.jpg",
            BrowserItemKind.IMAGE,
            float(index),
            file_size=index + 1,
            modified_time_ns=index,
        )
        for index in range(item_count)
    ]
    window.item_model.set_items(items)
    monkeypatch.setattr(
        window,
        "_visible_row_range",
        lambda: (0, min(item_count - 1, 19)),
    )
    window._thumbnail_request_timer.stop()
    provider.requests.clear()
    changes: list[bool] = []
    window.item_model.dataChanged.connect(lambda *_args: changes.append(True))

    window._request_visible_thumbnails()
    initial_paths = tuple(request[0] for request in provider.requests)
    assert len(initial_paths) == expected_requests
    assert len(set(initial_paths)) == expected_requests

    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(QColor("green"))
    for path in initial_paths:
        window._on_thumbnail_ready(path, window._generation, image)
    assert len(changes) == expected_requests

    provider.requests.clear()
    for _ in range(5):
        window._request_visible_thumbnails()

    assert provider.requests == []
    assert len(changes) == expected_requests
    window.close()
    qapp.processEvents()


def test_scroll_requests_only_new_items_and_does_not_request_ready_items_again(
    tmp_path,
    qapp,
    monkeypatch,
) -> None:
    provider = RecordingThumbnailProvider()
    window = make_window(tmp_path, qapp, provider=provider)
    items = [
        BrowserItem(
            f"{index}.jpg",
            tmp_path / "一覧" / f"{index}.jpg",
            BrowserItemKind.IMAGE,
            float(index),
        )
        for index in range(100)
    ]
    window.item_model.set_items(items)
    visible_range = [0, 9]
    monkeypatch.setattr(
        window,
        "_visible_row_range",
        lambda: (visible_range[0], visible_range[1]),
    )
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(QColor("blue"))

    def request_and_apply() -> set[str]:
        provider.requests.clear()
        window._request_visible_thumbnails()
        paths = {request[0] for request in provider.requests}
        for path in paths:
            window._on_thumbnail_ready(path, window._generation, image)
        return paths

    first_paths = request_and_apply()
    assert len(first_paths) == 20

    visible_range[:] = [40, 49]
    second_paths = request_and_apply()
    assert len(second_paths) == 23
    assert first_paths.isdisjoint(second_paths)

    visible_range[:] = [0, 9]
    assert request_and_apply() == set()
    window.close()
    qapp.processEvents()


def test_thumbnail_spec_fingerprint_and_unapplied_cache_require_requests(
    tmp_path,
    qapp,
    monkeypatch,
) -> None:
    provider = RecordingThumbnailProvider()
    window = make_window(tmp_path, qapp, provider=provider)
    path = tmp_path / "一覧" / "page.jpg"
    item = BrowserItem(
        path.name,
        path,
        BrowserItemKind.IMAGE,
        1.0,
        file_size=10,
        modified_time_ns=1,
    )
    window.item_model.set_items([item])
    monkeypatch.setattr(window, "_visible_row_range", lambda: (0, 0))
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(QColor("green"))
    window._on_thumbnail_ready(str(path), window._generation, image)

    provider.requests.clear()
    window._request_visible_thumbnails()
    assert provider.requests == []

    window.thumbnail_render_spec = ThumbnailRenderSpec.from_settings(
        512,
        window.thumbnail_frame_ratio,
        window.thumbnail_crop_mode,
        device_pixel_ratio=2.0,
        quality_mode=window.thumbnail_quality_mode,
        max_edge=window.thumbnail_cache_max_edge,
        browser_display_mode=window.browser_thumbnail_display_mode,
    )
    window._generation = provider.begin_generation()
    window._request_visible_thumbnails()
    assert [request[0] for request in provider.requests] == [str(path)]
    window._on_thumbnail_ready(str(path), window._generation, image)
    provider.requests.clear()
    window._request_visible_thumbnails()
    assert provider.requests == []

    updated_item = BrowserItem(
        path.name,
        path,
        BrowserItemKind.IMAGE,
        2.0,
        file_size=20,
        modified_time_ns=2,
    )
    window.item_model.set_items(
        [updated_item],
        preserve_thumbnails=True,
    )
    window._request_visible_thumbnails()
    assert [request[0] for request in provider.requests] == [str(path)]

    window.item_model.set_items([updated_item])
    provider.requests.clear()
    window._request_visible_thumbnails()
    assert [request[0] for request in provider.requests] == [str(path)]
    window.close()
    qapp.processEvents()


def test_model_path_lookup_remains_correct_after_large_sort(tmp_path):
    model = BrowserItemModel()
    items = [
        make_item(tmp_path / f"book {index:05d}.jpg", BrowserItemKind.IMAGE)
        for index in range(10_000)
    ]
    model.set_items(items)
    target = items[7_654]
    row = model.row_for_path(target.path)
    assert model.item_at(row) == target
    model.configure_sort("name", "descending", True)
    row = model.row_for_path(target.path)
    assert model.item_at(row) == target
