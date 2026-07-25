from __future__ import annotations

from pathlib import Path
from PIL import Image
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListView

from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.metadata_store import MetadataStore


def write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (12, 18), "white") as image:
        image.save(path)


def make_window(tmp_path: Path, qapp, *, count: int = 3):
    folder = tmp_path / "一覧"
    for number in range(count):
        write_image(folder / f"{number:03d}.jpg")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    window = BrowserWindow(
        config_manager=config,
        metadata_store=store,
    )
    window.resize(760, 520)
    window.show()
    assert window.wait_for_scan()
    qapp.processEvents()
    return window, store, folder


def close_window(window: BrowserWindow, store: MetadataStore, qapp) -> None:
    window.thumbnail_provider.wait_for_done(2000)
    window.close()
    qapp.processEvents()
    store.close()


def test_current_change_repaints_old_and_new_visual_rects(
    tmp_path: Path,
    qapp,
) -> None:
    window, store, _folder = make_window(tmp_path, qapp)
    previous = window.item_model.index(0, 0)
    current = window.item_model.index(1, 0)
    updated_rows: list[int] = []
    original = window._update_index_rect
    window._update_index_rect = lambda index: updated_rows.append(index.row())
    try:
        window._on_list_current_changed(current, previous)
    finally:
        window._update_index_rect = original

    assert updated_rows == [0, 1]
    close_window(window, store, qapp)


def test_single_and_multiple_selection_do_not_leave_old_current_selected(
    tmp_path: Path,
    qapp,
) -> None:
    window, store, _folder = make_window(tmp_path, qapp)
    selection = window.list_view.selectionModel()
    first = window.item_model.index(0, 0)
    second = window.item_model.index(1, 0)

    selection.setCurrentIndex(
        first,
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    selection.setCurrentIndex(
        second,
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    assert not selection.isSelected(first)
    assert selection.isSelected(second)
    assert selection.currentIndex() == second

    selection.select(
        first,
        QItemSelectionModel.SelectionFlag.Select,
    )
    selection.setCurrentIndex(
        second,
        QItemSelectionModel.SelectionFlag.NoUpdate,
    )
    assert selection.isSelected(first)
    assert selection.currentIndex() == second
    close_window(window, store, qapp)


def test_custom_spacing_and_padding_preserve_selection_anchor_and_generation(
    tmp_path: Path,
    qapp,
) -> None:
    window, store, _folder = make_window(tmp_path, qapp, count=80)
    selection = window.list_view.selectionModel()
    first = window.item_model.index(28, 0)
    current = window.item_model.index(36, 0)
    selection.select(first, QItemSelectionModel.SelectionFlag.Select)
    selection.setCurrentIndex(
        current,
        QItemSelectionModel.SelectionFlag.Select,
    )
    window.list_view.scrollTo(
        current,
        QListView.ScrollHint.PositionAtCenter,
    )
    qapp.processEvents()
    before = window._capture_list_view_state()
    generation = window.thumbnail_provider.generation
    history_length = len(window.navigation_history)

    window.config.apply(
        {
            "browser_item_spacing_mode": "custom",
            "browser_item_spacing": 32,
            "browser_cell_padding": 12,
        }
    )
    for _ in range(3):
        qapp.processEvents()

    after = window._capture_list_view_state()
    assert set(after.selected_paths) == set(before.selected_paths)
    assert after.current_path == before.current_path
    anchor_row = window.item_model.row_for_path(before.anchor_path or "")
    assert anchor_row >= 0
    assert window.list_view.visualRect(
        window.item_model.index(anchor_row, 0)
    ).intersects(window.list_view.viewport().rect())
    assert window.list_view.spacing() == 32
    assert window.item_delegate.cell_padding == 12
    assert window.thumbnail_provider.generation == generation
    assert len(window.navigation_history) == history_length
    close_window(window, store, qapp)


def test_ctrl_b_toggles_current_folder_favorite_only_in_browser(
    tmp_path: Path,
    qapp,
) -> None:
    window, store, folder = make_window(tmp_path, qapp)
    window.activateWindow()
    window.setFocus()
    QTest.keyClick(
        window,
        Qt.Key.Key_B,
        Qt.KeyboardModifier.ControlModifier,
    )
    qapp.processEvents()

    assert [entry.path for entry in store.list_folder_bookmarks()] == [
        str(folder.absolute())
    ]

    QTest.keyClick(
        window,
        Qt.Key.Key_B,
        Qt.KeyboardModifier.ControlModifier,
    )
    qapp.processEvents()
    assert store.list_folder_bookmarks() == []
    close_window(window, store, qapp)


def test_sidebar_layout_change_preserves_favorite_selection(
    tmp_path: Path,
    qapp,
) -> None:
    window, store, folder = make_window(tmp_path, qapp)
    other = tmp_path / "別の場所"
    other.mkdir()
    store.add_folder_bookmark(str(folder), label="一覧")
    store.add_folder_bookmark(str(other), label="別")
    qapp.processEvents()
    row = window.folder_bookmark_model.row_for_path(other)
    index = window.folder_bookmark_model.index(row, 0)
    window.favorite_view.setCurrentIndex(index)

    window.set_sidebar_layout("tabs")
    qapp.processEvents()

    current = window.folder_bookmark_model.entry_at(
        window.favorite_view.currentIndex()
    )
    assert current is not None
    assert current.path == str(other.absolute())
    assert window.current_path == folder.absolute()
    close_window(window, store, qapp)


def test_tree_sync_selects_current_folder_without_history_or_focus_change(
    tmp_path: Path,
    qapp,
) -> None:
    window, store, folder = make_window(tmp_path, qapp)
    window.list_view.setFocus()
    history_length = len(window.navigation_history)
    window._sync_tree_to_path(folder)
    for _ in range(30):
        qapp.processEvents()
        current = window.folder_tree.currentIndex()
        if (
            current.isValid()
            and Path(window.file_system_model.filePath(current)) == folder
        ):
            break
        QTest.qWait(20)

    current = window.folder_tree.currentIndex()
    assert current.isValid()
    assert Path(window.file_system_model.filePath(current)) == folder
    assert len(window.navigation_history) == history_length
    assert window.list_view.hasFocus()

    generation = window.folder_tree_sync.generation
    window.folder_tree_sync.cancel()
    assert window.folder_tree_sync.generation == generation + 1
    close_window(window, store, qapp)


def test_same_folder_navigation_still_resynchronizes_tree(
    tmp_path: Path,
    qapp,
) -> None:
    window, store, folder = make_window(tmp_path, qapp)
    calls: list[Path] = []
    original = window._sync_tree_to_path
    window._sync_tree_to_path = calls.append
    try:
        assert window.navigate_to(folder)
    finally:
        window._sync_tree_to_path = original

    assert calls == [folder.absolute()]
    close_window(window, store, qapp)


def test_favorite_context_routes_copy_to_existing_coordinator_entrypoint(
    tmp_path: Path,
    qapp,
) -> None:
    window, store, _folder = make_window(tmp_path, qapp)
    destination = tmp_path / "コピー先"
    destination.mkdir()
    store.add_folder_bookmark(str(destination), label="コピー先")
    qapp.processEvents()
    favorite_row = window.folder_bookmark_model.row_for_path(destination)
    favorite_index = window.folder_bookmark_model.index(favorite_row, 0)
    window.list_view.selectionModel().setCurrentIndex(
        window.item_model.index(0, 0),
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    calls: list[str] = []
    window.copy_selected_to = lambda path=None: (
        calls.append(str(path)) or True
    )

    assert window.copy_selected_to_folder_bookmark(favorite_index)

    assert len(calls) == 1
    assert Path(calls[0]) == destination.absolute()
    close_window(window, store, qapp)


def test_tree_context_routes_move_to_existing_coordinator_entrypoint(
    tmp_path: Path,
    qapp,
) -> None:
    window, store, _folder = make_window(tmp_path, qapp)
    destination = tmp_path / "移動先"
    destination.mkdir()
    tree_index = window.file_system_model.index(str(destination))
    for _ in range(20):
        if tree_index.isValid():
            break
        qapp.processEvents()
        QTest.qWait(10)
        tree_index = window.file_system_model.index(str(destination))
    assert tree_index.isValid()
    window.list_view.selectionModel().setCurrentIndex(
        window.item_model.index(0, 0),
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    calls: list[str] = []
    window.move_selected_to = lambda path=None: (
        calls.append(str(path)) or True
    )

    assert window.move_selected_to_tree_index(tree_index)

    assert len(calls) == 1
    assert Path(calls[0]) == destination.absolute()
    close_window(window, store, qapp)
