from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QListView, QStyle, QStyleOptionViewItem

from app.browser_item_delegate import (
    BrowserItemDelegate,
    browser_item_type_key,
    quantize_thumbnail_size,
    thumbnail_rect_for_cell,
    type_badge_rect,
)
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_sort import BrowserDisplayDensity
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.thumbnail_provider import BrowserThumbnailProvider


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
    assert first == second == QSize(171, 238)

    cell = QRect(0, 0, first.width(), first.height())
    thumbnail = thumbnail_rect_for_cell(
        cell,
        delegate.frame_size,
        delegate.profile.spacing,
    )
    assert thumbnail.size() == QSize(127, 180)
    assert thumbnail.left() == (first.width() - 127) // 2


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

    thumbnail = thumbnail_rect_for_cell(option.rect, delegate.frame_size, 6)
    badge = type_badge_rect(thumbnail, 18)
    physical_center = badge.center() * 2
    color = canvas.pixelColor(physical_center)
    assert color.red() > color.blue()


def test_four_density_profiles_keep_selection_and_visible_anchor(
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
    anchor = window.item_model.item_at(window._visible_anchor_index())
    assert anchor is not None

    window.config.apply({"browser_display_density": "large"})
    for _ in range(3):
        qapp.processEvents()

    selected = window.item_model.item_at(window.list_view.currentIndex())
    assert selected is not None and selected.path == items[55].path
    assert window.list_view.gridSize() == QSize(231, 298)
    restored_anchor = window.item_model.index(
        window.item_model.row_for_path(anchor.path),
        0,
    )
    assert window.list_view.visualRect(restored_anchor).intersects(
        window.list_view.viewport().rect()
    )
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
    window._fast_scrolling = True
    window._request_visible_thumbnails()
    assert provider.requests
    assert all(
        priority is not ThumbnailPriority.PREFETCH
        for _, _, priority in provider.requests
    )
    assert provider.cancelled_prefetch == 1
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
