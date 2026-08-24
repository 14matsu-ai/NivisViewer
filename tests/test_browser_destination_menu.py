from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication, QMenu

from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.metadata_store import MetadataStore


def _submenu(menu: QMenu, title: str) -> QMenu:
    for action in menu.actions():
        candidate = action.menu()
        if action.text() == title and candidate is not None:
            return candidate
    raise AssertionError(f"submenu not found: {title}")


def test_move_destination_submenu_uses_folder_bookmark_labels_and_paths(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    explicit = tmp_path / "明示ラベルのフォルダ"
    fallback = tmp_path / "パス名を使うフォルダ"
    explicit.mkdir()
    fallback.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    assert store.add_folder_bookmark(str(explicit), label="移動先 A")
    assert store.add_folder_bookmark(str(fallback))
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = BrowserWindow(
        config_manager=config,
        metadata_store=store,
        pdfium_service=object(),
        restore_initial_location=False,
    )

    try:
        # Exercise the same aboutToShow connection used when File -> Move is
        # opened, without native input or a real on-screen menu.
        window.move_destination_menu.aboutToShow.emit()
        favorites = _submenu(window.move_destination_menu, "お気に入り")
        actions = [action for action in favorites.actions() if not action.isSeparator()]

        assert [action.text() for action in actions] == [
            "移動先 A",
            fallback.name,
        ]
        assert [action.toolTip() for action in actions] == [
            str(explicit.absolute()),
            str(fallback.absolute()),
        ]
    finally:
        window.close()
        qapp.processEvents()
        store.close()
