from pathlib import Path

from PySide6.QtCore import Qt

from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.favorite_editor_dialog import FavoriteEditorDialog
from app.metadata_store import MetadataStore


def test_favorite_color_defaults_and_separator_editor(tmp_path: Path, qapp) -> None:
    config = ConfigManager(tmp_path / "config.json")
    values = config.load()
    assert values["favorite_color_show_icon"] is True
    assert values["favorite_color_show_left_bar"] is True
    assert values["favorite_color_show_background"] is False
    assert values["favorite_color_show_text"] is False

    folder = tmp_path / "folder"
    folder.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    try:
        assert store.add_group_favorite(1, str(folder))
        dialog = FavoriteEditorDialog(store.list_group_favorites(1), {}, [])
        try:
            dialog._add_separator()
            separators = dialog.separators()
            assert len(separators) == 1
            assert separators[0]["alignment"] == "center"
        finally:
            dialog.reject()
    finally:
        store.close()


def test_favorite_reorder_and_arrow_move_skip_separator_rows(tmp_path: Path, qapp) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    store.add_folder_bookmark(str(first))
    store.add_folder_bookmark(str(second))
    config.apply({"favorite_separators": {"1": [
        {"id": "divider", "label": "区切り", "alignment": "center", "before_path": str(second)},
    ]}})
    window = BrowserWindow(config_manager=config, metadata_store=store, restore_initial_location=False)
    try:
        assert [item.kind for item in window.folder_bookmark_model.entries] == ["folder", "separator", "folder"]
        second_index = window.folder_bookmark_model.index(2, 0)
        window._on_favorite_paths_dropped(
            (str(first),), second_index, Qt.KeyboardModifier.NoModifier,
            window.favorite_view, Qt.DropAction.MoveAction,
        )
        assert [item.path for item in store.list_folder_bookmarks()] == [str(second), str(first)]
        assert not window.move_folder_bookmark(window.folder_bookmark_model.index(0, 0), 1)
        assert window.move_folder_bookmark(window.folder_bookmark_model.index(1, 0), 1)
        assert [item.path for item in store.list_folder_bookmarks()] == [str(first), str(second)]
    finally:
        window.close()
        store.close()
