from __future__ import annotations

import zipfile
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

import pytest
from PIL import Image
from PySide6.QtCore import QItemSelectionModel, QSize
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication,
    QListView,
    QMessageBox,
    QTabWidget,
)

from app.browser_model import BrowserItemKind, BrowserItemModel
from app.browser_window import BrowserWindow
from app.browser_image_detail import BrowserImageDetailResult
from app.config_manager import ConfigManager
from app.metadata_store import MetadataStore


def write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (16, 24), "white") as image:
        image.save(path)


def make_config(tmp_path: Path, folder: Path, *, thumbnail_size: int = 180) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    config.set("thumbnail_size", thumbnail_size)
    return config


def finish_scan(window: BrowserWindow, qapp: QApplication) -> None:
    assert window.wait_for_scan()
    qapp.processEvents()


def test_file_detail_bar_shows_size_and_header_dimensions_and_rejects_stale(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "detail"
    first = folder / "first.jpg"
    second = folder / "second.jpg"
    write_image(first)
    with Image.new("RGB", (40, 30), "white") as image:
        image.save(second)
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)

    first_index = window.item_model.index(window.item_model.row_for_path(first), 0)
    window.list_view.selectionModel().select(
        first_index,
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    window.list_view.setCurrentIndex(first_index)
    stale_generation = window._detail_generation

    second_index = window.item_model.index(window.item_model.row_for_path(second), 0)
    window.list_view.selectionModel().select(
        second_index,
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    window.list_view.setCurrentIndex(second_index)
    window._on_image_detail_completed(
        BrowserImageDetailResult(str(first), stale_generation, (999, 999))
    )
    window.image_detail_probe._pool.waitForDone()
    qapp.processEvents()

    detail = window.file_detail_label.text()
    assert "40 × 30" in detail
    assert "999" not in detail
    assert "★" not in detail
    window.close()
    qapp.processEvents()


def test_direct_rating_target_preserves_multiselection_and_thumbnail_cache(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "rating-direct"
    first = folder / "first.jpg"
    second = folder / "second.jpg"
    write_image(first)
    write_image(second)
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)
    assert window.thumbnail_provider.wait_for_done()
    qapp.processEvents()
    selection = window.list_view.selectionModel()
    first_index = window.item_model.index(window.item_model.row_for_path(first), 0)
    second_index = window.item_model.index(window.item_model.row_for_path(second), 0)
    selection.select(first_index, QItemSelectionModel.SelectionFlag.Select)
    selection.select(second_index, QItemSelectionModel.SelectionFlag.Select)
    selection.setCurrentIndex(first_index, QItemSelectionModel.SelectionFlag.NoUpdate)
    seeded_thumbnail = QImage(12, 8, QImage.Format.Format_ARGB32)
    seeded_thumbnail.fill(0xFF224466)
    window.item_model.set_thumbnail_image(first, seeded_thumbnail)
    thumbnail = window.item_model.data(
        first_index,
        BrowserItemModel.ThumbnailImageRole,
    )
    thumbnail_key = thumbnail.cacheKey()

    try:
        with patch.object(
            window.thumbnail_provider,
            "request",
            wraps=window.thumbnail_provider.request,
        ) as thumbnail_request:
            assert window.set_rating_for_paths((str(first),), 4) is True
            qapp.processEvents()
            assert thumbnail_request.call_count == 0

        renamed = folder / "first {zpi$r=4}.jpg"
        assert renamed.exists()
        assert second.exists()
        assert len(selection.selectedIndexes()) == 2
        renamed_index = window.item_model.index(
            window.item_model.row_for_path(renamed),
            0,
        )
        assert window.item_model.data(
            renamed_index,
            BrowserItemModel.ThumbnailImageRole,
        ).cacheKey() == thumbnail_key
    finally:
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize("item_count", [0, 1, 25])
def test_status_count_uses_model_row_count_without_materializing_items(
    tmp_path: Path,
    qapp: QApplication,
    item_count: int,
) -> None:
    folder = tmp_path / f"件数-{item_count}"
    folder.mkdir()
    for index in range(item_count):
        write_image(folder / f"{index:03}.jpg")
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)
    if item_count > 1:
        selection_model = window.list_view.selectionModel()
        first = window.item_model.index(0, 0)
        second = window.item_model.index(1, 0)
        selection_model.setCurrentIndex(
            first,
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )
        selection_model.select(
            first,
            QItemSelectionModel.SelectionFlag.Select,
        )
        selection_model.select(
            second,
            QItemSelectionModel.SelectionFlag.Select,
        )

    with patch.object(
        BrowserItemModel,
        "items",
        new_callable=PropertyMock,
        side_effect=AssertionError("status must not materialize all model items"),
    ):
        window._update_status(force=True)

    assert f" — {item_count}件 — " in window.statusBar().currentMessage()
    if item_count > 1:
        assert "ほか1件" in window.statusBar().currentMessage()
    window.close()
    qapp.processEvents()


def test_loading_status_uses_committed_row_count_without_materializing_items(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "読み込み中件数"
    for index in range(3):
        write_image(folder / f"{index}.jpg")
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)
    window._pending_scan = SimpleNamespace(
        path=folder,
        refresh=False,
        committed=True,
        buffered_entries=[object(), object()],
    )

    try:
        with patch.object(
            BrowserItemModel,
            "items",
            new_callable=PropertyMock,
            side_effect=AssertionError(
                "loading status must not materialize all model items"
            ),
        ):
            window._update_status(force=True)
        assert window.statusBar().currentMessage().endswith(
            "読み込み中… 5項目"
        )
    finally:
        window._pending_scan = None
        window.close()
        qapp.processEvents()


def test_show_folder_sidebar_and_settings_round_trip(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "漫画"
    write_image(folder / "1.jpg")
    (folder / "次の本").mkdir()
    config = make_config(tmp_path, folder, thumbnail_size=220)
    window = BrowserWindow(config_manager=config)

    window.show_initial()
    finish_scan(window, qapp)

    assert window.isVisible()
    assert window.current_path == folder.resolve()
    assert {item.display_name for item in window.items} == {"1.jpg", "次の本"}
    assert window.list_view.iconSize() == QSize(220, 220)

    window.splitter.setSizes([340, 700])
    qapp.processEvents()
    window.set_sidebar_visible(False)
    assert not window.sidebar.isVisible()
    assert not window.sidebar_action.isChecked()
    window.set_sidebar_visible(True)
    assert window.sidebar.isVisible()
    assert window.sidebar_action.isChecked()

    window.close()
    qapp.processEvents()

    assert config.get("browser_sidebar_visible") is True
    assert int(config.get("browser_sidebar_width")) >= 300
    assert config.get("browser_window_geometry")


def test_browser_defers_thumbnail_disk_cache_open_until_a_worker_requests_it(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "先行表示"
    folder.mkdir()
    config = make_config(tmp_path, folder)

    with patch("app.browser_window.ThumbnailDiskCache") as cache_type:
        cache = cache_type.return_value
        cache.enabled = False
        window = BrowserWindow(config_manager=config)

        assert cache_type.call_args.kwargs["enabled"] is False
        assert window.thumbnail_provider.disk_cache is cache
        assert not cache.set_enabled.called

        window.close()
        qapp.processEvents()


def test_settings_action_is_direct_and_triggers_existing_dialog_path_once(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "本棚"
    folder.mkdir()
    opened: list[BrowserWindow] = []
    monkeypatch.setattr(
        BrowserWindow,
        "open_settings_dialog",
        lambda window: opened.append(window),
    )
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)

    actions = window.menuBar().actions()
    settings_actions = [
        action for action in actions if action.text() == "環境設定…"
    ]
    assert settings_actions == [window.settings_action]
    assert window.settings_action.menu() is None
    assert not any(
        action.text() == "設定" and action.menu() is not None
        for action in actions
    )

    window.settings_action.trigger()

    assert opened == [window]
    window.close()
    qapp.processEvents()


def test_settings_dialog_is_not_created_after_application_shutdown_starts(
    monkeypatch,
) -> None:
    dialog_factory = Mock()
    monkeypatch.setattr("app.browser_window.SettingsDialog", dialog_factory)
    window = SimpleNamespace(
        _shutdown_prepared=False,
        _settings_dialog_open_guard=Mock(return_value=False),
    )

    BrowserWindow.open_settings_dialog(window)

    dialog_factory.assert_not_called()


def test_item_activation_passes_image_and_archive_but_folder_navigates(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "本棚"
    image = folder / "選択画像.jpg"
    write_image(image)
    archive = folder / "書庫.cbz"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(image, "1.jpg")
    child = folder / "子フォルダ"
    child.mkdir()
    opened: list[tuple[str, bool]] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        open_path_handler=lambda path, new: opened.append((path, new)),
    )
    finish_scan(window, qapp)

    image_row = next(
        row for row, item in enumerate(window.items) if item.kind == BrowserItemKind.IMAGE
    )
    archive_row = next(
        row for row, item in enumerate(window.items) if item.kind == BrowserItemKind.ARCHIVE
    )
    folder_row = next(
        row for row, item in enumerate(window.items) if item.kind == BrowserItemKind.FOLDER
    )
    window.open_item(window.item_model.index(image_row, 0))
    window.open_item(
        window.item_model.index(archive_row, 0),
        open_in_new_window=True,
    )

    assert opened == [(str(image.absolute()), False), (str(archive.absolute()), True)]

    window.set_current_folder(folder)
    window.open_item(window.item_model.index(folder_row, 0))
    finish_scan(window, qapp)
    assert window.current_path == child.resolve()
    assert len(opened) == 2
    window.close()
    qapp.processEvents()


def test_controller_selection_sync_does_not_reopen_item(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "同期"
    image = folder / "対象.jpg"
    write_image(image)
    opened: list[str] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        open_path_handler=lambda path, _new: opened.append(path),
    )
    finish_scan(window, qapp)

    window.select_path(image)
    finish_scan(window, qapp)

    assert window.list_view.currentIndex().isValid()
    assert window.item_model.item_at(window.list_view.currentIndex()).path == image.absolute()
    assert opened == []
    window.close()
    qapp.processEvents()


def test_sort_controls_apply_without_opening_viewer_or_changing_history(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "並び替え"
    first = folder / "book2.jpg"
    second = folder / "book10.jpg"
    write_image(first)
    write_image(second)
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    os.utime(second, ns=(2_000_000_000, 2_000_000_000))
    opened: list[str] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        open_path_handler=lambda path, _new: opened.append(path),
    )
    finish_scan(window, qapp)
    initial_history_length = len(window.navigation_history)
    initial_generation = window.thumbnail_provider.generation

    window.browser_sort_key_combo.setCurrentIndex(
        window.browser_sort_key_combo.findData("modified_time")
    )
    window.browser_sort_order_combo.setCurrentIndex(
        window.browser_sort_order_combo.findData("descending")
    )
    qapp.processEvents()

    assert [entry.display_name for entry in window.items] == [
        "book10.jpg",
        "book2.jpg",
    ]
    assert window.config.get("browser_sort_key") == "modified_time"
    assert window.config.get("browser_sort_order") == "descending"
    assert len(window.navigation_history) == initial_history_length
    assert window.thumbnail_provider.generation == initial_generation
    assert opened == []
    assert "更新日時・降順" in window.statusBar().currentMessage()
    window.close()
    qapp.processEvents()


def test_sort_and_density_preserve_multiple_selection_and_thumbnail_cache(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "複数選択"
    for index in range(1, 13):
        write_image(folder / f"book{index}.jpg")
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)
    selection_model = window.list_view.selectionModel()
    selected_names = {"book2.jpg", "book10.jpg"}
    for name in selected_names:
        row = next(
            row
            for row, entry in enumerate(window.items)
            if entry.display_name == name
        )
        index = window.item_model.index(row, 0)
        selection_model.select(index, QItemSelectionModel.SelectionFlag.Select)
        if name == "book10.jpg":
            selection_model.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
    initial_generation = window.thumbnail_provider.generation
    initial_history_length = len(window.navigation_history)

    window.config.apply(
        {
            "browser_sort_order": "descending",
            "browser_display_density": "comfortable",
        }
    )
    qapp.processEvents()

    restored_names = {
        window.item_model.item_at(index).display_name
        for index in window.list_view.selectionModel().selectedIndexes()
    }
    current = window.item_model.item_at(window.list_view.currentIndex())
    assert restored_names == selected_names
    assert current is not None and current.display_name == "book10.jpg"
    assert window.list_view.gridSize() == QSize(199, 193)
    assert window.thumbnail_provider.generation == initial_generation
    assert len(window.navigation_history) == initial_history_length
    window.close()
    qapp.processEvents()


def test_thumbnail_size_change_preserves_selection_and_uses_new_generation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "サイズ変更"
    image = folder / "選択.jpg"
    write_image(image)
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)
    window.select_path(image)
    finish_scan(window, qapp)
    initial_generation = window.thumbnail_provider.generation

    window.config.apply({"thumbnail_size": 260})
    qapp.processEvents()

    selected = window.item_model.item_at(window.list_view.currentIndex())
    assert selected is not None and selected.path == image.absolute()
    assert window.list_view.iconSize() == QSize(260, 260)
    assert window.thumbnail_provider.generation == initial_generation + 1

    window.config.apply({"thumbnail_size": 260})
    qapp.processEvents()
    assert window.thumbnail_provider.generation == initial_generation + 1
    window.close()
    qapp.processEvents()


def test_display_density_changes_layout_without_changing_thumbnail_size(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "密度"
    write_image(folder / "1.jpg")
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder, thumbnail_size=180)
    )
    finish_scan(window, qapp)

    expectations = {
        "compact": QSize(147, 191),
        "standard": QSize(171, 192),
        "comfortable": QSize(199, 193),
        "large": QSize(231, 195),
    }
    for density, grid_size in expectations.items():
        window.browser_display_density_combo.setCurrentIndex(
            window.browser_display_density_combo.findData(density)
        )
        qapp.processEvents()
        assert window.list_view.gridSize() == grid_size
        assert window.list_view.iconSize() == QSize(180, 180)

    window.close()
    qapp.processEvents()


def test_folders_first_control_keeps_folder_group_at_front(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "フォルダ優先"
    write_image(folder / "a.jpg")
    (folder / "z-folder").mkdir()
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)

    assert [entry.display_name for entry in window.items] == [
        "z-folder",
        "a.jpg",
    ]
    window.browser_folders_first_checkbox.setChecked(False)
    qapp.processEvents()

    assert [entry.display_name for entry in window.items] == [
        "a.jpg",
        "z-folder",
    ]
    assert window.config.get("browser_folders_first") is False
    window.close()
    qapp.processEvents()


def test_sort_keeps_visible_anchor_item_on_screen(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "スクロール"
    for index in range(1, 61):
        write_image(folder / f"book{index}.jpg")
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    window.resize(640, 420)
    window.show()
    finish_scan(window, qapp)
    anchor_row = window.item_model.row_for_path(folder / "book40.jpg")
    anchor = window.item_model.index(anchor_row, 0)
    window.list_view.scrollTo(anchor, QListView.ScrollHint.PositionAtCenter)
    qapp.processEvents()
    visible_anchor = window.item_model.item_at(window._visible_anchor_index())
    assert visible_anchor is not None

    window.browser_sort_order_combo.setCurrentIndex(
        window.browser_sort_order_combo.findData("descending")
    )
    qapp.processEvents()

    restored_row = window.item_model.row_for_path(visible_anchor.path)
    restored = window.item_model.index(restored_row, 0)
    assert window.list_view.visualRect(restored).intersects(
        window.list_view.viewport().rect()
    )
    window.close()
    qapp.processEvents()


def test_metadata_update_does_not_change_filesystem_mtime_sort(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "メタデータ分離"
    older = folder / "older.jpg"
    newer = folder / "newer.jpg"
    write_image(older)
    write_image(newer)
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
    source_mtimes = (older.stat().st_mtime_ns, newer.stat().st_mtime_ns)
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        metadata_store=store,
    )
    finish_scan(window, qapp)
    window.config.apply(
        {
            "browser_sort_key": "modified_time",
            "browser_sort_order": "descending",
        }
    )
    before = [entry.display_name for entry in window.items]

    store.set_rating(str(older), 5)
    qapp.processEvents()

    assert [entry.display_name for entry in window.items] == before == [
        "newer.jpg",
        "older.jpg",
    ]
    assert (older.stat().st_mtime_ns, newer.stat().st_mtime_ns) == source_mtimes
    window.close()
    qapp.processEvents()
    store.close()


def test_sidebar_has_folder_bookmark_and_history_tabs(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "three-tabs"
    folder.mkdir()
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    config = make_config(tmp_path, folder)
    config.set("browser_sidebar_layout", "tabs")
    window = BrowserWindow(
        config_manager=config,
        metadata_store=store,
    )
    finish_scan(window, qapp)

    tabs = window.sidebar_layout_controller.tabs
    assert tabs is not None
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        "フォルダ",
        "お気に入り",
        "履歴",
    ]
    favorites_panel = tabs.widget(1)
    assert isinstance(favorites_panel, QTabWidget)
    assert [
        favorites_panel.tabText(index)
        for index in range(favorites_panel.count())
    ] == ["フォルダ", "本"]
    window.close()
    store.close()
    qapp.processEvents()


def test_bookmark_model_updates_and_bookmarks_can_be_opened_or_removed(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    shelf = tmp_path / "shelf"
    book = shelf / "book"
    archive = shelf / "book.cbz"
    write_image(book / "1.jpg")
    archive.write_bytes(b"test")
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    opened: list[tuple[str, bool]] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, shelf),
        metadata_store=store,
        open_path_handler=lambda path, new: opened.append((path, new)),
    )
    finish_scan(window, qapp)

    window.add_browser_bookmark(book, item_type="book_folder", label="本")
    window.add_browser_bookmark(archive, item_type="archive")
    assert window.bookmark_model.rowCount() == 2

    book_index = window.bookmark_model.index(0, 0)
    archive_index = window.bookmark_model.index(1, 0)
    window.open_bookmark(book_index, open_in_new_window=True)
    window.open_bookmark(archive_index, open_in_new_window=True)

    assert opened == [(str(book.absolute()), True), (str(archive.absolute()), True)]
    window.remove_browser_bookmark(book)
    assert window.bookmark_model.rowCount() == 1
    window.close()
    store.close()
    qapp.processEvents()


def test_folder_bookmark_navigates_browser_without_opening_viewer(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    shelf = tmp_path / "shelf"
    target = shelf / "target"
    target.mkdir(parents=True)
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    opened: list[str] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, shelf),
        metadata_store=store,
        open_path_handler=lambda path, _new: opened.append(path),
    )
    finish_scan(window, qapp)
    window.add_browser_bookmark(target, item_type="folder")

    window.open_bookmark(window.bookmark_model.index(0, 0))
    finish_scan(window, qapp)

    assert window.current_path == target.absolute()
    assert opened == []
    window.close()
    store.close()
    qapp.processEvents()


def test_current_folder_can_be_added_to_bookmarks(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "current"
    folder.mkdir()
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        metadata_store=store,
    )
    finish_scan(window, qapp)

    window.add_current_folder_bookmark()

    assert window.bookmark_model.rowCount() == 1
    entry = window.bookmark_model.entries[0]
    assert Path(entry.path) == folder.absolute()
    assert entry.item_type == "folder"
    window.close()
    store.close()
    qapp.processEvents()


def test_history_is_recent_first_opens_items_and_selection_does_not(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "shelf"
    first = folder / "first"
    second = folder / "second"
    first.mkdir(parents=True)
    second.mkdir()
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    store.record_book_opened(
        str(first),
        item_type="folder",
        start_page_index=1,
        total_pages=3,
    )
    store.record_book_opened(
        str(second),
        item_type="folder",
        start_page_index=2,
        total_pages=4,
    )
    opened: list[tuple[str, bool]] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        metadata_store=store,
        open_path_handler=lambda path, new: opened.append((path, new)),
    )
    finish_scan(window, qapp)

    assert [Path(entry.path) for entry in window.history_model.entries] == [
        second.absolute(),
        first.absolute(),
    ]
    window.history_view.setCurrentIndex(window.history_model.index(0, 0))
    assert opened == []

    window.open_history(
        window.history_model.index(0, 0),
        open_in_new_window=True,
    )
    assert opened == [(str(second.absolute()), True)]
    window.close()
    store.close()
    qapp.processEvents()


def test_missing_metadata_items_are_nonmodal_and_removable(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "shelf"
    folder.mkdir()
    missing_bookmark = tmp_path / "missing-book"
    missing_history = tmp_path / "missing-history.zip"
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    store.add_browser_bookmark(str(missing_bookmark), item_type="folder")
    store.record_book_opened(
        str(missing_history),
        item_type="archive",
        start_page_index=0,
        total_pages=None,
    )
    opened: list[str] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        metadata_store=store,
        open_path_handler=lambda path, _new: opened.append(path),
    )
    finish_scan(window, qapp)

    window.open_bookmark(window.bookmark_model.index(0, 0))
    assert window.statusBar().currentMessage() == "ブックマーク先が見つかりません"
    window.open_history(window.history_model.index(0, 0))
    assert window.statusBar().currentMessage() == "履歴の項目が見つかりません"
    assert opened == []

    window.remove_browser_bookmark(missing_bookmark)
    window.remove_history_entry(window.history_model.index(0, 0))
    assert window.bookmark_model.rowCount() == 0
    assert window.history_model.rowCount() == 0
    window.close()
    store.close()
    qapp.processEvents()


def test_clear_history_uses_confirmation_path(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "shelf"
    folder.mkdir()
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    store.record_book_opened(
        str(folder),
        item_type="folder",
        start_page_index=0,
        total_pages=1,
    )
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        metadata_store=store,
    )
    finish_scan(window, qapp)
    confirmations: list[bool] = []

    def answer_yes(*_args, **_kwargs):
        confirmations.append(True)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", answer_yes)

    assert window.clear_history() is True
    assert confirmations == [True]
    assert window.history_model.rowCount() == 0
    window.close()
    store.close()
    qapp.processEvents()
