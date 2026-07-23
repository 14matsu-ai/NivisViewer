from __future__ import annotations

import zipfile
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from app.browser_model import BrowserItemKind
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager


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
