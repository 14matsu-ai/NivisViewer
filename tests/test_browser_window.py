from __future__ import annotations

import zipfile
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QMessageBox

from app.browser_model import BrowserItemKind
from app.browser_window import BrowserWindow
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
    qapp.processEvents()

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

    window.select_path(image)

    assert window.list_view.currentIndex().isValid()
    assert window.item_model.item_at(window.list_view.currentIndex()).path == image.absolute()
    assert opened == []
    window.close()
    qapp.processEvents()


def test_sidebar_has_folder_bookmark_and_history_tabs(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "three-tabs"
    folder.mkdir()
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        metadata_store=store,
    )

    assert [window.sidebar.tabText(index) for index in range(window.sidebar.count())] == [
        "フォルダ",
        "ブックマーク",
        "履歴",
    ]
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
    window.add_browser_bookmark(target, item_type="folder")

    window.open_bookmark(window.bookmark_model.index(0, 0))

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
