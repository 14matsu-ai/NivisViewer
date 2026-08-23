from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QListView, QStyle, QStyleOptionViewItem

from app.browser_item_delegate import (
    BrowserItemDelegate,
    browser_item_type_key,
    quantize_thumbnail_size,
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


class FixedShellIconProvider:
    def __init__(self, color: str = "#d03030") -> None:
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(color))
        self.icon = QIcon(pixmap)

    def icon_for(self, _item):
        return self.icon


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
def test_folder_without_preview_paints_square_canvas_until_preview_arrives(
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
    probe = thumbnail.topLeft() + QPoint(12, 12)

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

    pending = render(0)
    assert pending.pixelColor(probe) != palette.base().color()
    assert pending.pixelColor(thumbnail.topLeft() + QPoint(7, 7)) != (
        palette.base().color()
    )
    assert model.data(model.index(0, 0), model.ThumbnailImageRole) is None
    assert delegate._uses_folder_fallback_canvas(folder, None)
    assert not delegate._uses_folder_fallback_canvas(image_file, None)

    model.set_preview_status(folder.path, "loading")
    loading = render(0, selected=True)
    assert loading.pixelColor(probe) != palette.base().color()

    preview = QImage(
        delegate.frame_size,
        QImage.Format.Format_RGB32,
    )
    preview.fill(QColor("#20a050"))
    assert not delegate._uses_folder_fallback_canvas(folder, preview)
    model.set_thumbnail_image(folder.path, preview)
    completed = render(0)
    assert completed.pixelColor(probe) == QColor("#20a050")


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
    assert provider.cancelled_prefetch == 1
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
    assert len(second_paths) == 30
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
