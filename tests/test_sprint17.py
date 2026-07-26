from __future__ import annotations

from pathlib import Path
from threading import Event
import time

from PIL import Image
from PySide6.QtCore import QModelIndex, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage, QStandardItem, QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QMainWindow,
    QScrollArea,
    QSlider,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.browser_model import (
    BrowserItem,
    BrowserItemKind,
    BrowserItemModel,
    browser_item_from_scan_entry,
)
from app.browser_window import BrowserWindow
from app.browser_scanner import BrowserScanRequest, scan_directory
from app.browser_thumbnail_scheduler import (
    ThumbnailPriority,
    build_thumbnail_request_plan,
)
from app.browser_visibility import (
    BrowserVisibilityPolicy,
    filesystem_visibility_flags,
)
from app.config_manager import ConfigManager
from app.drag_drop import build_path_mime_data
from app.external_drop_open import ExternalDropOpenController
from app.folder_tree_sync import FolderTreeSyncController
from app.fullscreen_chrome import FullscreenChromeController
from app.settings_dialog import SettingsDialog
from app.metadata_store import MetadataStore
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec
from app.viewer_window import ViewerWindow


def make_config(tmp_path: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return config


def make_thumbnail_item(path: Path) -> BrowserItem:
    return BrowserItem(
        path.name,
        path,
        BrowserItemKind.IMAGE,
        path.stat().st_mtime,
        file_size=path.stat().st_size,
        modified_time_ns=path.stat().st_mtime_ns,
    )


def test_sprint17_config_defaults_and_normalization(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert config.get("browser_show_hidden_items") is True
    assert config.get("browser_show_unsupported_files") is True
    assert config.get("browser_show_system_items") is False
    assert config.get("folder_tree_focus_rebase") is True
    assert config.get("folder_tree_context_ancestor_levels") == 3

    changed = config.apply(
        {
            "hide_ui_in_fullscreen": "yes",
            "hide_cursor_in_fullscreen": 1,
            "folder_tree_context_ancestor_levels": 99,
            "thumbnail_cache_max_edge": 5000,
        }
    )
    assert config.get("hide_ui_in_fullscreen") is False
    assert config.get("hide_cursor_in_fullscreen") is False
    assert changed["folder_tree_context_ancestor_levels"] == 12
    assert changed["thumbnail_cache_max_edge"] == 2048


def test_settings_dialog_is_scrollable_and_round_trips_sprint17_values(
    tmp_path: Path,
    qapp,
) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)
    assert len(dialog.findChildren(QScrollArea)) == 5
    assert dialog.button_box.parent() is dialog
    assert dialog.button_box.button(QDialogButtonBox.StandardButton.Ok) is not None

    dialog.fullscreen_hide_ui_checkbox.setChecked(True)
    dialog.fullscreen_hide_cursor_checkbox.setChecked(True)
    dialog.browser_show_hidden_checkbox.setChecked(False)
    dialog.browser_show_unsupported_checkbox.setChecked(False)
    dialog.browser_show_system_checkbox.setChecked(True)
    dialog.folder_tree_focus_rebase_checkbox.setChecked(True)
    dialog.folder_tree_ancestor_levels_spin.setValue(7)
    dialog.thumbnail_cache_max_edge_spin.setValue(2048)
    changed = dialog.apply_settings()

    assert changed["hide_ui_in_fullscreen"] is True
    assert changed["hide_cursor_in_fullscreen"] is True
    assert changed["browser_show_hidden_items"] is False
    assert changed["browser_show_unsupported_files"] is False
    assert changed["browser_show_system_items"] is True
    assert changed["folder_tree_context_ancestor_levels"] == 7
    assert changed["thumbnail_cache_max_edge"] == 2048

    reopened = SettingsDialog(ConfigManager(config.path))
    reopened.config.load()
    reopened.load_current_values()
    assert reopened.fullscreen_hide_cursor_checkbox.isChecked()
    assert reopened.folder_tree_ancestor_levels_spin.value() == 7
    assert reopened.thumbnail_cache_max_edge_spin.value() == 2048
    dialog.reject()
    reopened.reject()


def test_settings_dialog_clamps_to_short_screen_but_keeps_buttons(
    tmp_path: Path,
    qapp,
) -> None:
    dialog = SettingsDialog(make_config(tmp_path))
    dialog.resize(800, 2000)
    dialog.show()
    qapp.processEvents()
    available = dialog.screen().availableGeometry()
    assert dialog.height() <= max(420, int(available.height() * 0.88))
    assert dialog.button_box.isVisible()
    dialog.reject()


def test_visibility_policy_lists_hidden_and_unsupported_without_decoding(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "一覧"
    folder.mkdir()
    Image.new("RGB", (8, 8), "white").save(folder / ".hidden.jpg")
    (folder / "notes.txt").write_text("日本語", encoding="utf-8")
    (folder / "download.part").write_bytes(b"temporary")
    batches = []
    request = BrowserScanRequest(
        str(folder),
        generation=1,
        visibility_policy=BrowserVisibilityPolicy(
            show_hidden_items=True,
            show_unsupported_files=True,
            show_system_items=False,
        ),
    )
    result = scan_directory(request, Event(), batches.append)
    entries = [entry for batch in batches for entry in batch.entries]
    by_name = {entry.display_name: entry for entry in entries}

    assert result.total_count == 2
    assert by_name[".hidden.jpg"].hidden
    assert by_name[".hidden.jpg"].openable_by_nivisviewer
    assert by_name["notes.txt"].item_kind == "other"
    assert not by_name["notes.txt"].openable_by_nivisviewer
    assert "download.part" not in by_name


def test_visibility_policy_filters_hidden_unsupported_and_system(
    tmp_path: Path,
) -> None:
    assert filesystem_visibility_flags(".secret", 0)[0]
    hidden, system = filesystem_visibility_flags("system.dat", 0x2 | 0x4)
    assert hidden and system
    policy = BrowserVisibilityPolicy(
        show_hidden_items=False,
        show_unsupported_files=False,
        show_system_items=False,
    )
    assert not policy.allows(
        hidden=True,
        system=False,
        supported=True,
        is_directory=False,
    )
    assert not policy.allows(
        hidden=False,
        system=True,
        supported=True,
        is_directory=False,
    )
    assert not policy.allows(
        hidden=False,
        system=False,
        supported=False,
        is_directory=False,
    )


def test_unsupported_model_item_uses_flags_and_keeps_file_unchanged(
    tmp_path: Path,
) -> None:
    source = tmp_path / "資料.txt"
    source.write_text("変更禁止", encoding="utf-8")
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    request = BrowserScanRequest(
        str(tmp_path),
        generation=2,
        visibility_policy=BrowserVisibilityPolicy(),
    )
    batches = []
    scan_directory(request, Event(), batches.append)
    entry = next(
        entry
        for batch in batches
        for entry in batch.entries
        if entry.display_name == source.name
    )
    item = browser_item_from_scan_entry(entry)
    model = BrowserItemModel()
    model.set_items([item])

    assert item.kind is BrowserItemKind.OTHER
    assert model.data(model.index(0), model.OpenableRole) is False
    assert source.read_bytes() == before[0]
    assert source.stat().st_mtime_ns == before[1]


def test_prefetch_miss_skips_decode_and_disk_persistence(
    tmp_path: Path,
    qapp,
) -> None:
    source = tmp_path / "cover.png"
    Image.new("RGB", (100, 140), "white").save(source)
    item = make_thumbnail_item(source)
    calls: list[int] = []

    def loader(_item: BrowserItem, edge: int) -> QImage:
        calls.append(edge)
        return QImage(edge, edge, QImage.Format.Format_RGB888)

    cache = ThumbnailDiskCache(tmp_path / "cache")
    provider = BrowserThumbnailProvider(
        loader=loader,
        disk_cache=cache,
        disk_cache_enabled=True,
    )
    spec = ThumbnailRenderSpec.from_settings(
        180,
        "portrait_1_sqrt2",
        "smart_crop",
        quality_mode="auto",
        max_edge=1024,
    )
    generation = provider.begin_generation()
    assert provider.request(
        item,
        spec,
        generation=generation,
        priority=ThumbnailPriority.PREFETCH,
    )
    assert provider.wait_for_done(2000)
    qapp.processEvents()
    assert calls == []
    assert cache.statistics()["entry_count"] == 0

    assert provider.request(
        item,
        spec,
        generation=generation,
        priority=ThumbnailPriority.VISIBLE,
    )
    assert provider.wait_for_done(2000)
    qapp.processEvents()
    stats = provider.cache_statistics()
    assert len(calls) == 1
    assert stats["generated_visible"] == 1
    assert stats["generated_prefetch"] == 0
    assert stats["disk_saved"] == 1
    assert stats["entry_count"] == 1
    provider.close()


def test_two_hundred_item_plan_does_not_request_all_items() -> None:
    plan = build_thumbnail_request_plan(
        row_count=200,
        first_visible=40,
        last_visible=59,
        prefetch_screens=1,
    )
    assert len(plan.visible_rows) == 20
    assert len(plan.prefetch_rows) == 40
    assert len(plan.requested_rows) == 60
    assert 0 not in plan.requested_rows
    assert 199 not in plan.requested_rows

    fast = build_thumbnail_request_plan(
        row_count=200,
        first_visible=80,
        last_visible=99,
        prefetch_screens=1,
        fast_scrolling=True,
    )
    assert fast.prefetch_rows == ()


def make_fullscreen_controller() -> tuple[
    QApplication,
    QMainWindow,
    QWidget,
    QWidget,
    FullscreenChromeController,
]:
    app = QApplication.instance()
    assert app is not None
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
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
        hide_delay_ms=0,
    )
    window.resize(640, 480)
    window.show()
    app.processEvents()
    return app, window, central, viewer, controller


def test_fullscreen_controller_owns_normal_chrome_and_cursor(qapp) -> None:
    _app, window, central, viewer, controller = make_fullscreen_controller()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=True)
    controller._pointer_in_reveal_area = lambda _position: False
    controller.hide_overlays()
    controller._schedule_cursor_hide()
    controller._hide_cursor_if_idle()

    assert controller.menu_bar.parent() is window
    assert controller.status_bar.parent() is window
    assert not controller.menu_bar.isVisible()
    assert not controller.status_bar.isVisible()
    assert controller.fullscreen_menu_bar.parent() is controller.top_overlay
    assert controller.fullscreen_status_bar.parent() is controller.bottom_overlay
    assert not controller.top_overlay.isVisible()
    assert not controller.bottom_overlay.isVisible()
    assert controller.cursor_hidden
    assert viewer.cursor().shape() == Qt.CursorShape.BlankCursor

    controller.process_pointer(
        central.mapToGlobal(QPoint(central.width() // 2, 0))
    )
    assert controller.top_overlay.isVisible()
    assert not controller.cursor_hidden

    controller.set_fullscreen_state(False, hide_ui=True, hide_cursor=True)
    assert controller.menu_bar.parent() is window
    assert controller.status_bar.parent() is window
    assert not controller.cursor_hidden
    assert QApplication.overrideCursor() is None
    window.close()


def test_cursor_blank_state_does_not_leak_between_viewers(qapp) -> None:
    _app, first_window, _central, _viewer, first = make_fullscreen_controller()
    _app, second_window, _central2, viewer2, second = make_fullscreen_controller()
    first.set_fullscreen_state(True, hide_ui=True, hide_cursor=True)
    first.hide_overlays()
    first._schedule_cursor_hide()
    first._hide_cursor_if_idle()
    assert first.cursor_hidden
    assert viewer2.cursor().shape() != Qt.CursorShape.BlankCursor
    second.set_fullscreen_state(False, hide_ui=True, hide_cursor=False)
    first.shutdown()
    assert QApplication.overrideCursor() is None
    first_window.close()
    second_window.close()


def test_focused_root_index_context_depths() -> None:
    model = QStandardItemModel()
    root = model.invisibleRootItem()
    chain: list[QStandardItem] = []
    parent = root
    for name in ("A", "B", "C", "D", "E"):
        item = QStandardItem(name)
        parent.appendRow(item)
        chain.append(item)
        parent = item
    current = chain[-1].index()
    ancestors = [
        chain[-2].index(),
        chain[-3].index(),
        chain[-4].index(),
        chain[-5].index(),
    ]
    assert (
        FolderTreeSyncController._focused_root_index(current, ancestors, 0)
        == current
    )
    assert (
        FolderTreeSyncController._focused_root_index(current, ancestors, 1)
        == current
    )
    assert (
        FolderTreeSyncController._focused_root_index(current, ancestors, 3)
        == chain[-3].index()
    )
    assert (
        FolderTreeSyncController._focused_root_index(current, ancestors, 12)
        == chain[0].index()
    )


def test_viewer_child_and_fullscreen_overlay_receive_native_drop(
    tmp_path: Path,
    qapp,
) -> None:
    opened: list[tuple[str, bool | None]] = []
    window = ViewerWindow(
        config_manager=make_config(tmp_path),
        open_path_handler=lambda path, new, _source: opened.append((path, new)),
    )
    first = str((tmp_path / "画像.jpg").absolute())
    second = str((tmp_path / "本.cbz").absolute())
    mime = build_path_mime_data((first, second))
    enter = QDragEnterEvent(
        QPoint(2, 2),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window.viewer, enter)
    assert enter.isAccepted()

    drop = QDropEvent(
        QPointF(2, 2),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window.viewer, drop)
    assert opened == [(first, False), (second, True)]

    third = str((tmp_path / "資料.pdf").absolute())
    window.show()
    window.fullscreen_chrome.set_fullscreen_state(
        True,
        hide_ui=False,
        hide_cursor=False,
    )
    qapp.processEvents()
    overlay_mime = build_path_mime_data((third,))
    overlay_enter = QDragEnterEvent(
        QPoint(2, 2),
        Qt.DropAction.CopyAction,
        overlay_mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window.fullscreen_chrome.top_overlay, overlay_enter)
    assert overlay_enter.isAccepted()
    overlay_drop = QDropEvent(
        QPointF(2, 2),
        Qt.DropAction.CopyAction,
        overlay_mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window.fullscreen_chrome.top_overlay, overlay_drop)
    assert opened[-1] == (third, False)
    window.close()
    qapp.processEvents()


def test_external_drop_controller_rejects_http_and_text_commands() -> None:
    from PySide6.QtCore import QMimeData, QUrl

    mime = QMimeData()
    mime.setUrls([QUrl("https://example.invalid/book.zip")])
    mime.setText("cmd.exe /c erase")
    assert ExternalDropOpenController.local_paths(mime) == ()
    assert ExternalDropOpenController.paths(mime) == ()


def test_browser_window_shows_all_items_and_requests_generic_preview(
    tmp_path: Path,
    qapp,
) -> None:
    folder = tmp_path / "全ファイル"
    folder.mkdir()
    Image.new("RGB", (16, 24), "white").save(folder / "cover.jpg")
    Image.new("RGB", (16, 24), "white").save(folder / ".hidden.png")
    (folder / "notes.txt").write_text("text", encoding="utf-8")
    config = make_config(tmp_path)
    config.set("last_browser_path", str(folder))
    window = BrowserWindow(config_manager=config)
    window.resize(700, 500)
    window.show()
    assert window.wait_for_scan()
    qapp.processEvents()

    assert {item.display_name for item in window.items} == {
        "cover.jpg",
        ".hidden.png",
        "notes.txt",
    }
    other = next(item for item in window.items if item.kind is BrowserItemKind.OTHER)
    assert not other.openable_by_nivisviewer
    requested: list[BrowserItem] = []
    original_request = window.thumbnail_provider.request
    window.thumbnail_provider.request = lambda item, *_args, **_kwargs: (
        requested.append(item) or False
    )
    try:
        window._request_visible_thumbnails()
    finally:
        window.thumbnail_provider.request = original_request
    assert requested
    assert any(item.path == other.path for item in requested)
    assert not other.openable_by_nivisviewer
    window.open_item(window.item_model.index(window.item_model.row_for_path(other.path), 0))
    assert "表示できません" in window.statusBar().currentMessage()
    window.close()
    qapp.processEvents()


def test_visibility_setting_refreshes_without_growing_history(
    tmp_path: Path,
    qapp,
) -> None:
    folder = tmp_path / "表示更新"
    folder.mkdir()
    config = make_config(tmp_path)
    config.set("last_browser_path", str(folder))
    window = BrowserWindow(config_manager=config)
    assert window.wait_for_scan()
    qapp.processEvents()
    history_count = len(window.navigation_history)
    refreshes: list[bool] = []
    original = window.refresh_current_folder
    window.refresh_current_folder = lambda: refreshes.append(True) or True
    try:
        config.apply({"browser_show_unsupported_files": False})
    finally:
        window.refresh_current_folder = original
    assert refreshes == [True]
    assert len(window.navigation_history) == history_count
    window.close()
    qapp.processEvents()


def test_favorite_single_click_and_double_click_navigate_once(
    tmp_path: Path,
    qapp,
) -> None:
    folder = tmp_path / "favorite"
    folder.mkdir()
    config = make_config(tmp_path)
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(folder))
    window = BrowserWindow(
        config_manager=config,
        metadata_store=store,
        restore_initial_location=False,
    )
    qapp.processEvents()
    index = window.folder_bookmark_model.index(0, 0)
    calls: list[int] = []
    original = window.open_folder_bookmark
    window.open_folder_bookmark = lambda model_index: calls.append(model_index.row())
    window._favorite_click_timer.setInterval(1)
    try:
        window._on_favorite_clicked(index)
        QTest.qWait(30)
        qapp.processEvents()
        assert calls == [0]

        calls.clear()
        window._on_favorite_clicked(index)
        window._on_favorite_double_clicked(index)
        QTest.qWait(30)
        qapp.processEvents()
        assert calls == [0]
    finally:
        window.open_folder_bookmark = original
        window.close()
        qapp.processEvents()
        store.close()


def test_unsupported_viewer_drop_is_ignored_and_current_state_is_kept(
    tmp_path: Path,
    qapp,
) -> None:
    opened: list[str] = []
    window = ViewerWindow(
        config_manager=make_config(tmp_path),
        open_path_handler=lambda path, _new, _source: opened.append(path),
    )
    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("text", encoding="utf-8")
    mime = build_path_mime_data((str(unsupported),))
    enter = QDragEnterEvent(
        QPoint(1, 1),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window.viewer, enter)
    drop = QDropEvent(
        QPointF(1, 1),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window.viewer, drop)
    deadline = time.monotonic() + 1
    while window._drop_probe_workers and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.002)
    assert opened == []
    assert window.model.total_pages == 0
    window.close()
    qapp.processEvents()


def test_fullscreen_cursor_setting_applies_immediately_to_existing_viewer(
    tmp_path: Path,
    qapp,
) -> None:
    config = make_config(tmp_path)
    window = ViewerWindow(config_manager=config)
    window.showFullScreen()
    qapp.processEvents()
    config.apply(
        {
            "hide_ui_in_fullscreen": True,
            "hide_cursor_in_fullscreen": True,
        }
    )
    qapp.processEvents()
    assert window.fullscreen_chrome.fullscreen
    assert window.fullscreen_chrome.hide_ui_enabled
    assert window.fullscreen_chrome.hide_cursor_enabled
    assert window.menuBar().parent() is window
    assert not window.menuBar().isVisible()
    assert (
        window.fullscreen_chrome.fullscreen_menu_bar.parent()
        is window.fullscreen_chrome.top_overlay
    )

    config.apply({"hide_cursor_in_fullscreen": False})
    assert not window.fullscreen_chrome.hide_cursor_enabled
    assert not window.fullscreen_chrome.cursor_hidden
    window.showNormal()
    window.close()
    qapp.processEvents()


def test_deep_tree_rebases_to_three_level_context_without_focus_or_history_change(
    tmp_path: Path,
    qapp,
) -> None:
    target = tmp_path / "A" / "B" / "C" / "D" / "E"
    target.mkdir(parents=True)
    config = make_config(tmp_path)
    config.apply(
        {
            "last_browser_path": str(target),
            "folder_tree_sync_mode": "focus_current",
            "folder_tree_focus_rebase": True,
            "folder_tree_context_ancestor_levels": 3,
        }
    )
    window = BrowserWindow(config_manager=config)
    window.resize(760, 520)
    window.show()
    assert window.wait_for_scan()
    window.list_view.setFocus()
    history_count = len(window.navigation_history)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        qapp.processEvents()
        current = window.folder_tree.currentIndex()
        root = window.folder_tree.rootIndex()
        if current.isValid() and root.isValid():
            break
        QTest.qWait(10)

    current_path = Path(window.file_system_model.filePath(window.folder_tree.currentIndex()))
    root_path = Path(window.file_system_model.filePath(window.folder_tree.rootIndex()))
    assert current_path == target
    assert root_path == target.parents[1]
    assert window.list_view.hasFocus()
    assert len(window.navigation_history) == history_count

    window.folder_tree_sync.show_full_tree()
    assert not window.folder_tree.rootIndex().isValid()
    window._sync_tree_to_path(target)
    deadline = time.monotonic() + 1
    while not window.folder_tree.rootIndex().isValid() and time.monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)
    assert Path(window.file_system_model.filePath(window.folder_tree.rootIndex())) == target.parents[1]
    window.close()
    qapp.processEvents()


def test_two_hundred_item_session_persists_only_visible_misses(
    tmp_path: Path,
    qapp,
) -> None:
    items: list[BrowserItem] = []
    for index in range(200):
        path = tmp_path / f"{index:03d}.jpg"
        path.write_bytes(b"source")
        stat_result = path.stat()
        items.append(
            BrowserItem(
                path.name,
                path,
                BrowserItemKind.IMAGE,
                stat_result.st_mtime,
                file_size=stat_result.st_size,
                modified_time_ns=stat_result.st_mtime_ns,
            )
        )
    decoded: list[str] = []

    def loader(item: BrowserItem, edge: int) -> QImage:
        decoded.append(item.display_name)
        return QImage(edge, edge, QImage.Format.Format_RGB888)

    provider = BrowserThumbnailProvider(
        loader=loader,
        disk_cache=ThumbnailDiskCache(tmp_path / "cache"),
        disk_cache_enabled=True,
    )
    spec = ThumbnailRenderSpec.from_settings(
        180,
        "portrait_1_sqrt2",
        "smart_crop",
        quality_mode="economy",
        max_edge=1024,
    )
    plan = build_thumbnail_request_plan(
        row_count=200,
        first_visible=0,
        last_visible=19,
        prefetch_screens=1,
    )
    generation = provider.begin_generation()
    for row in plan.visible_rows:
        provider.request(
            items[row],
            spec,
            generation=generation,
            priority=ThumbnailPriority.VISIBLE,
        )
    for row in plan.prefetch_rows:
        provider.request(
            items[row],
            spec,
            generation=generation,
            priority=ThumbnailPriority.PREFETCH,
        )
    assert provider.wait_for_done(10000)
    qapp.processEvents()
    stats = provider.cache_statistics()
    assert len(decoded) == len(plan.visible_rows) == 20
    assert stats["generated_prefetch"] == 0
    assert stats["prefetch_skipped"] == len(plan.prefetch_rows)
    assert stats["entry_count"] == len(plan.visible_rows)
    assert int(stats["session_growth_bytes"]) > 0
    provider.close()
