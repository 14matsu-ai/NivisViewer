from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QModelIndex, QPointF, QRect, Qt
from PySide6.QtGui import QDropEvent, QImage
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMenuBar, QSlider, QStatusBar, QVBoxLayout, QWidget

from app.browser_grid_metrics import build_browser_grid_metrics
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.drag_drop import (
    NIVIS_PATHS_MIME,
    ExplorerSelectionController,
    build_path_mime_data,
    choose_drop_operation,
    paths_from_mime_data,
)
from app.fullscreen_chrome import FullscreenChromeController
from app.metadata_store import MetadataStore
from app.settings_dialog import SettingsDialog
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_render import ThumbnailRenderSpec
from app.viewer_window import ViewerWindow


def test_filename_modes_have_exact_height_and_no_bottom_margin() -> None:
    expected = {"hidden": 0, "one_line": 14, "two_lines": 28}
    for mode, title_height in expected.items():
        metrics = build_browser_grid_metrics(
            thumbnail_size=96,
            frame_ratio_id="square_1_1",
            font_height=14,
            filename_display=mode,
            filename_gap=0,
            filename_padding_y=0,
            horizontal_margin=0,
            cell_padding=0,
            item_spacing_x=0,
            item_spacing_y=0,
        )
        assert metrics.title_height == title_height
        assert metrics.cell_size.height() == 96 + title_height
        assert metrics.grid_size == metrics.cell_size
        assert metrics.title_rect(metrics.cell_rect()).bottom() == (
            metrics.cell_rect().bottom() if title_height else -1
        )


def test_filename_gap_and_padding_are_the_only_vertical_extras() -> None:
    metrics = build_browser_grid_metrics(
        thumbnail_size=128,
        frame_ratio_id="square_1_1",
        font_height=12,
        filename_display="two_lines",
        filename_gap=3,
        filename_padding_y=2,
        horizontal_margin=4,
        cell_padding=1,
        item_spacing_x=0,
        item_spacing_y=0,
    )
    assert metrics.cell_size.height() == 128 + 3 + 24 + 4 + 2
    assert metrics.selection_rect(metrics.cell_rect()).width() < metrics.cell_size.width()


def test_selected_filename_band_matches_frame_and_bridges_filename_gap() -> None:
    metrics = build_browser_grid_metrics(
        thumbnail_size=128,
        frame_ratio_id="square_1_1",
        font_height=12,
        filename_display="two_lines",
        filename_gap=6,
        filename_padding_y=2,
        horizontal_margin=8,
        cell_padding=3,
        item_spacing_x=5,
        item_spacing_y=7,
    )
    cell = metrics.cell_rect(20, 30)
    frame = metrics.thumbnail_frame_rect(cell)
    title = metrics.title_rect(cell)
    selected_title = metrics.selected_title_rect(cell)

    assert title.left() == frame.left()
    assert title.right() == frame.right()
    assert title.width() < cell.width()
    assert selected_title.left() == frame.left()
    assert selected_title.right() == frame.right()
    assert selected_title.top() == frame.bottom()
    assert selected_title.bottom() == title.bottom()
    assert selected_title.contains(title)


def test_config_defaults_and_cleanup_day_normalization(tmp_path: Path) -> None:
    config = ConfigManager(tmp_path / "config.json")
    settings = config.load()
    assert settings["browser_filename_display"] == "one_line"
    assert settings["browser_filename_gap"] == 0
    assert settings["browser_filename_padding_y"] == 0
    assert settings["fullscreen_ui_hide_delay_ms"] == 0
    assert settings["thumbnail_cache_max_unused_days"] == 0
    config.apply({"thumbnail_cache_max_unused_days": 1})
    assert config.get("thumbnail_cache_max_unused_days") == 7


def test_seven_day_cleanup_setting_shows_ssd_warning(qapp, tmp_path: Path) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    dialog = SettingsDialog(config)
    dialog.cache_unused_days_combo.setCurrentIndex(
        dialog.cache_unused_days_combo.findData(-1)
    )
    dialog.cache_unused_days_spin.setValue(7)
    changed = dialog.apply_settings()
    assert changed["thumbnail_cache_max_unused_days"] == 7
    assert any(
        "SSD" in label.text() and "再生成" in label.text()
        for label in dialog.findChildren(QLabel)
    )
    dialog.reject()


def test_fullscreen_zero_delay_hides_on_next_event_loop(qapp) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = QSlider(central)
    layout.addWidget(viewer)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=QMenuBar(window),
        slider=slider,
        status_bar=status,
        hide_delay_ms=0,
    )
    controller.set_active(True)
    controller.show_top()
    controller._pointer_in_reveal_area = lambda _point: False
    controller.reevaluate_visibility()
    assert not controller.top_overlay.isHidden()
    qapp.processEvents()
    assert controller.top_overlay.isHidden()


def test_path_mime_is_json_uri_list_deduplicated_and_local(tmp_path: Path) -> None:
    first = str((tmp_path / "日本語.cbz").absolute())
    second = str((tmp_path / "two.pdf").absolute())
    mime = build_path_mime_data((first, first, second))
    assert mime.hasUrls()
    assert mime.hasFormat(NIVIS_PATHS_MIME)
    assert paths_from_mime_data(mime) == (first, second)


def test_drop_action_matches_explorer_volume_and_modifiers() -> None:
    sources = (r"C:\Books\a.cbz",)
    assert choose_drop_operation(sources, r"C:\Target") == "move"
    assert choose_drop_operation(sources, r"D:\Target") == "copy"
    assert (
        choose_drop_operation(
            sources,
            r"C:\Target",
            Qt.KeyboardModifier.ControlModifier,
        )
        == "copy"
    )
    assert (
        choose_drop_operation(
            sources,
            r"D:\Target",
            Qt.KeyboardModifier.ShiftModifier,
        )
        == "move"
    )
    assert choose_drop_operation((r"\\srv\a\x",), r"\\srv\a\y") == "move"
    assert choose_drop_operation((r"\\srv\a\x",), r"\\srv\b\y") == "copy"


def test_explorer_selection_only_enables_blank_rubber_band_with_shift() -> None:
    controller = ExplorerSelectionController()
    controller.begin(-1, Qt.KeyboardModifier.NoModifier)
    assert controller.blank_press and not controller.shift_rubber_band
    controller.begin(-1, Qt.KeyboardModifier.ShiftModifier)
    assert controller.shift_rubber_band
    controller.begin(3, Qt.KeyboardModifier.ShiftModifier)
    assert not controller.shift_rubber_band


def test_viewer_multiple_drop_reuses_first_and_opens_rest_in_new_windows(
    qapp,
    tmp_path: Path,
) -> None:
    opened: list[tuple[str, bool | None]] = []
    window = ViewerWindow(
        config_manager=ConfigManager(tmp_path / "config.json"),
        open_path_handler=lambda path, new, _source: opened.append((path, new)),
    )
    first = str((tmp_path / "1.jpg").absolute())
    second = str((tmp_path / "2.cbz").absolute())
    mime = build_path_mime_data((first, first, second))
    event = QDropEvent(
        QPointF(1, 1),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(event)
    assert opened == [(first, False), (second, True)]
    window.close()
    qapp.processEvents()


def test_view_mode_change_reevaluates_fullscreen_chrome(
    qapp,
    tmp_path: Path,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    calls: list[bool] = []
    window.fullscreen_chrome.reevaluate_visibility = lambda: calls.append(True)
    window.set_view_mode("single")
    window.set_reading_direction("ltr")
    window.set_fit_mode("fit_width")
    assert len(calls) >= 3
    window.close()
    qapp.processEvents()


def test_viewer_drop_accepts_folder_with_dot_after_async_probe(
    qapp,
    tmp_path: Path,
) -> None:
    opened: list[str] = []
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(
        config_manager=config,
        open_path_handler=lambda path, _new, _source: opened.append(path),
    )
    folder = tmp_path / "book.folder"
    folder.mkdir()
    mime = build_path_mime_data((str(folder),))
    event = QDropEvent(
        QPointF(1, 1),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(event)
    deadline = time.monotonic() + 2
    while not opened and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert opened == [str(folder)]
    window.close()
    qapp.processEvents()


def _cache_item(path: Path) -> BrowserItem:
    return BrowserItem(
        path.name,
        path,
        BrowserItemKind.IMAGE,
        path.stat().st_mtime,
    )


def _spec(edge: int, ratio: str = "square_1_1", crop: str = "letterbox"):
    return ThumbnailRenderSpec.from_settings(
        edge,
        ratio,
        crop,
        quality_mode="economy",
        max_edge=2048,
    )


def _entry_count(cache: ThumbnailDiskCache) -> int:
    with sqlite3.connect(cache.index_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0])


def test_cache_keeps_at_most_two_resolutions_per_variant(tmp_path: Path) -> None:
    source = tmp_path / "cover.png"
    Image.new("RGB", (1200, 1600), "white").save(source)
    cache = ThumbnailDiskCache(tmp_path / "cache", cleanup_interval=100)
    item = _cache_item(source)
    for edge in (128, 256, 512, 1024):
        spec = _spec(edge)
        assert cache.put(
            item,
            spec,
            QImage(spec.long_edge, spec.long_edge, QImage.Format.Format_RGB888),
        )
        assert _entry_count(cache) <= 2
    cache.close()


def test_cache_keeps_at_most_four_derivatives_per_source(tmp_path: Path) -> None:
    source = tmp_path / "cover.png"
    Image.new("RGB", (1200, 1600), "white").save(source)
    cache = ThumbnailDiskCache(tmp_path / "cache", cleanup_interval=100)
    item = _cache_item(source)
    variants = [
        ("square_1_1", "letterbox"),
        ("portrait_1_sqrt2", "letterbox"),
        ("landscape_16_9", "center_crop"),
        ("portrait_2_3", "center_crop"),
        ("landscape_4_3", "smart_crop"),
    ]
    for ratio, crop in variants:
        spec = _spec(256, ratio, crop)
        assert cache.put(
            item,
            spec,
            QImage(spec.frame_width, spec.frame_height, QImage.Format.Format_RGB888),
        )
    assert _entry_count(cache) == 4
    cache.close()


def test_cache_protects_visible_resolution_when_third_is_saved(tmp_path: Path) -> None:
    source = tmp_path / "cover.png"
    Image.new("RGB", (1200, 1600), "white").save(source)
    cache = ThumbnailDiskCache(tmp_path / "cache", cleanup_interval=100)
    item = _cache_item(source)
    first = _spec(128)
    second = _spec(256)
    third = _spec(512)
    for spec in (first, second):
        cache.put(
            item,
            spec,
            QImage(spec.long_edge, spec.long_edge, QImage.Format.Format_RGB888),
        )
    cache.put(
        item,
        third,
        QImage(third.long_edge, third.long_edge, QImage.Format.Format_RGB888),
        protected_thumbnail_sizes={first.cache_token},
    )
    assert cache.get(item, first.cache_token) is not None
    assert cache.get(item, third.cache_token) is not None
    assert cache.get(item, second.cache_token) is None
    cache.close()


def test_age_cleanup_disabled_and_thirty_days(tmp_path: Path) -> None:
    source = tmp_path / "cover.png"
    Image.new("RGB", (1200, 1600), "white").save(source)
    cache = ThumbnailDiskCache(tmp_path / "cache", cleanup_interval=100)
    item = _cache_item(source)
    spec = _spec(256)
    cache.put(
        item,
        spec,
        QImage(spec.long_edge, spec.long_edge, QImage.Format.Format_RGB888),
    )
    with sqlite3.connect(cache.index_path) as connection:
        connection.execute(
            "UPDATE entries SET last_used = ?",
            (time.time() - 40 * 86400,),
        )
    assert cache.prune(max_unused_days=0) == 0
    assert _entry_count(cache) == 1
    assert cache.prune(max_unused_days=30) == 1
    assert _entry_count(cache) == 0
    assert source.exists()
    cache.close()


def test_favorite_blank_drop_probes_folder_off_thread(
    qapp,
    tmp_path: Path,
) -> None:
    folder = tmp_path / "日本語お気に入り"
    folder.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    window = BrowserWindow(
        config_manager=ConfigManager(tmp_path / "config.json"),
        metadata_store=store,
        restore_initial_location=False,
    )
    window._on_favorite_paths_dropped(
        (str(folder),),
        QModelIndex(),
        Qt.KeyboardModifier.NoModifier,
        None,
    )
    deadline = time.monotonic() + 2
    while not store.list_folder_bookmarks() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert [entry.path for entry in store.list_folder_bookmarks()] == [str(folder)]
    assert len(window.navigation_history) == 0
    window.close()
    qapp.processEvents()
    store.close()


def test_folder_favorite_and_tree_drops_route_copy_move(
    qapp,
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = BrowserWindow(
        config_manager=config,
        metadata_store=store,
        restore_initial_location=False,
    )
    source = str((tmp_path / "source.cbz").absolute())
    target = tmp_path / "target"
    target.mkdir()
    item = BrowserItem("target", target, BrowserItemKind.FOLDER, None)
    window.item_model.set_items([item])
    calls: list[tuple[object, tuple[str, ...], object]] = []
    monkeypatch.setattr(
        window,
        "_start_file_operation",
        lambda operation, *, sources=(), destination=None, **_kwargs: (
            calls.append((operation, sources, destination)) or True
        ),
    )
    window._on_browser_paths_dropped(
        (source,),
        window.item_model.index(0),
        Qt.KeyboardModifier.ControlModifier,
        window.list_view,
    )
    assert calls[-1][0].value == "copy"

    store.add_folder_bookmark(str(target))
    favorite_index = window.folder_bookmark_model.index(0)
    window._on_favorite_paths_dropped(
        (source,),
        favorite_index,
        Qt.KeyboardModifier.ShiftModifier,
        None,
    )
    assert calls[-1][0].value == "move"

    monkeypatch.setattr(
        window.file_system_model,
        "filePath",
        lambda _index: str(target),
    )
    window._on_tree_paths_dropped(
        (source,),
        window.item_model.index(0),
        Qt.KeyboardModifier.ControlModifier,
        None,
    )
    assert calls[-1][0].value == "copy"
    window.close()
    qapp.processEvents()
    store.close()
