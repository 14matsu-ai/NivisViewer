from __future__ import annotations

import os
import sqlite3
import time

from PIL import Image
from PySide6.QtWidgets import QMenu

from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_scanner import scan_entry_from_dir_entry
from app.browser_visibility import BrowserVisibilityPolicy
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.folder_cover import (
    NIVIS_COVER_NAME, ZIPPLA_COVER_NAME, clear_folder_cover,
    has_nivis_cover, resolve_folder_cover, set_folder_cover,
)
from app.image_source import FolderImageSource, create_image_source
from app.metadata_store import MetadataStore
from app.settings_dialog import SettingsDialog
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec


def _image(path, color):
    Image.new('RGB', (24, 24), color).save(path)


def test_folder_local_cover_survives_external_move_and_falls_back(tmp_path):
    folder = tmp_path / '日本語フォルダー'
    folder.mkdir()
    first = folder / '01.png'
    second = folder / '02.png'
    zippla = folder / ZIPPLA_COVER_NAME
    _image(first, (220, 20, 20))
    _image(second, (20, 20, 220))
    _image(zippla, (20, 220, 20))
    original_bytes = {path.name: path.read_bytes() for path in (first, second, zippla)}
    provider = BrowserThumbnailProvider(
        disk_cache_enabled=False,
        preview_settings={'browser_use_zippla_cover': True},
    )
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    try:
        item = BrowserItem(folder.name, folder, BrowserItemKind.FOLDER, None)
        assert provider._load_pipeline(item, spec).cover_path == zippla
        cover = set_folder_cover(second)
        assert cover == folder / NIVIS_COVER_NAME
        assert has_nivis_cover(folder)
        selected = provider._load_pipeline(item, spec)
        assert selected.cover_path == cover
        assert selected.image.pixelColor(12, 12).blue() > 150

        moved = tmp_path / '移動後'
        folder.rename(moved)
        moved_item = BrowserItem(moved.name, moved, BrowserItemKind.FOLDER, None)
        assert resolve_folder_cover(moved, use_zippla_cover=True) == moved / NIVIS_COVER_NAME
        assert provider._load_pipeline(moved_item, spec).cover_path == moved / NIVIS_COVER_NAME
        assert clear_folder_cover(moved)
        assert not clear_folder_cover(moved)
        assert provider._load_pipeline(moved_item, spec).cover_path == moved / ZIPPLA_COVER_NAME
        (moved / ZIPPLA_COVER_NAME).unlink()
        assert provider._load_pipeline(moved_item, spec).cover_path == moved / first.name
        for name, data in original_bytes.items():
            if name != ZIPPLA_COVER_NAME:
                assert (moved / name).read_bytes() == data
    finally:
        provider.close()


def test_zippla_cover_is_ignored_by_default(tmp_path):
    folder = tmp_path / '本'
    folder.mkdir()
    page = folder / '01.png'
    zippla = folder / ZIPPLA_COVER_NAME
    _image(page, 'red')
    _image(zippla, 'blue')
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    item = BrowserItem(folder.name, folder, BrowserItemKind.FOLDER, None)
    try:
        assert resolve_folder_cover(folder) is None
        off = provider._load_pipeline(item, 64)
        assert off.cover_path == page
        assert off.page_count == 2
        assert provider._load_page_count_pipeline(item, 64).page_count == 2
        provider.set_zippla_cover_enabled(True)
        on = provider._load_pipeline(item, 64)
        assert on.cover_path == zippla
        assert on.page_count == 1
        assert provider._load_page_count_pipeline(item, 64).page_count == 1
        provider.set_zippla_cover_enabled(False)
        assert provider._load_pipeline(item, 64).cover_path == page
    finally:
        provider.close()


def test_turning_off_zippla_cover_does_not_reuse_its_disk_thumbnail(tmp_path):
    folder = tmp_path / '本'
    folder.mkdir()
    page = folder / '01.png'
    zippla = folder / ZIPPLA_COVER_NAME
    _image(page, 'red')
    _image(zippla, 'blue')
    item = BrowserItem(folder.name, folder, BrowserItemKind.FOLDER, None)
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    provider = BrowserThumbnailProvider(disk_cache=cache)
    try:
        zippla_image = BrowserThumbnailProvider._load_image_path(zippla, spec)
        assert zippla_image is not None
        assert cache.put(item, spec, zippla_image, cover_path=zippla, page_count=1)
        assert cache.get_page_count(item) == 1
        assert cache.get_suitable(item, spec, forbid_zippla_cover=True) is None
        result = provider._load_pipeline(item, spec)
        assert result.cover_path == page
        provider.set_zippla_cover_enabled(True)
        assert cache.get_page_count(item) is None
    finally:
        provider.close()


def test_cover_files_stay_out_of_browser_and_folder_pages(tmp_path):
    folder = tmp_path / '本'
    folder.mkdir()
    page = folder / 'あ.png'
    _image(page, 'red')
    zippla = folder / ZIPPLA_COVER_NAME
    _image(zippla, 'blue')
    nivis = set_folder_cover(page)
    visible = BrowserVisibilityPolicy(
        show_hidden_items=True, show_unsupported_files=True,
    )
    zippla_cover_mode = BrowserVisibilityPolicy(
        show_hidden_items=True, show_unsupported_files=True,
        use_zippla_cover=True,
    )
    with os.scandir(folder) as entries:
        scanned = {
            entry.name: scan_entry_from_dir_entry(entry, visible)
            for entry in entries
        }
    assert scanned[page.name] is not None
    assert scanned[zippla.name] is not None
    assert scanned[zippla.name].hidden
    assert scanned[nivis.name] is None
    with os.scandir(folder) as entries:
        assert all(
            scan_entry_from_dir_entry(entry, zippla_cover_mode) is None
            for entry in entries if entry.name in {nivis.name, zippla.name}
        )
    assert set(FolderImageSource(folder).list_images()) == {str(page), str(zippla)}
    assert set(FolderImageSource(folder, recursive=True).list_images()) == {str(page), str(zippla)}
    assert FolderImageSource(folder, use_zippla_cover=True).list_images() == [str(page)]
    assert FolderImageSource(
        folder, image_snapshot=(str(nivis), str(page), str(zippla)),
        use_zippla_cover=True,
    ).list_images() == [str(page)]
    assert set(FolderImageSource(
        folder, image_snapshot=(str(nivis), str(page), str(zippla)),
    ).list_images()) == {str(page), str(zippla)}
    off_source, _ = create_image_source(folder)
    on_source, _ = create_image_source(folder, use_zippla_cover=True)
    try:
        assert set(off_source.list_images()) == {str(page), str(zippla)}
        assert on_source.list_images() == [str(page)]
    finally:
        off_source.close()
        on_source.close()
    assert BrowserThumbnailProvider._folder_image_candidates(folder) == [page]


def test_reselecting_cover_replaces_only_nivis_snapshot(tmp_path):
    folder = tmp_path / '本'
    folder.mkdir()
    first = folder / '一枚目.png'
    second = folder / '二枚目.png'
    zippla = folder / ZIPPLA_COVER_NAME
    _image(first, 'red')
    _image(second, 'blue')
    _image(zippla, 'green')
    zippla_bytes = zippla.read_bytes()
    cover = set_folder_cover(first)
    first_bytes = cover.read_bytes()
    assert set_folder_cover(second) == cover
    assert cover.read_bytes() != first_bytes
    assert zippla.read_bytes() == zippla_bytes
    assert {path.name for path in folder.iterdir()} == {
        first.name, second.name, zippla.name, cover.name,
    }


def test_existing_same_named_file_is_never_overwritten_or_deleted(tmp_path):
    folder = tmp_path / '本'
    folder.mkdir()
    page = folder / '表紙.png'
    _image(page, 'red')
    reserved = folder / NIVIS_COVER_NAME
    _image(reserved, 'blue')
    before = reserved.read_bytes()
    try:
        set_folder_cover(page)
    except FileExistsError:
        pass
    else:
        assert False, 'Expected an unowned filename collision'
    assert reserved.read_bytes() == before
    assert not clear_folder_cover(folder)
    assert resolve_folder_cover(folder) is None
    zippla = folder / ZIPPLA_COVER_NAME
    _image(zippla, 'green')
    assert resolve_folder_cover(folder) is None
    assert resolve_folder_cover(folder, use_zippla_cover=True) == zippla
    assert reserved.read_bytes() == before


def test_legacy_profile_cover_table_is_removed(tmp_path):
    database = tmp_path / 'metadata.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.execute('PRAGMA user_version=1')
        connection.execute('CREATE TABLE folder_covers (folder_path TEXT PRIMARY KEY, image_name TEXT)')
        connection.execute('INSERT INTO folder_covers VALUES (?, ?)', ('old', 'old.png'))
    store = MetadataStore(database)
    store.close()
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='folder_covers'"
        ).fetchone() is None


def test_zippla_cover_option_defaults_off_in_browser_settings(tmp_path, qapp):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    assert config.get('browser_use_zippla_cover') is False
    dialog = SettingsDialog(config)
    try:
        assert not dialog.browser_use_zippla_cover_checkbox.isChecked()
        dialog.browser_use_zippla_cover_checkbox.setChecked(True)
        assert dialog.values()['browser_use_zippla_cover'] is True
    finally:
        dialog.reject()


def test_cover_invalidation_works_before_lazy_disk_cache_opens(tmp_path):
    folder = tmp_path / '本'
    folder.mkdir()
    cover = folder / '表紙.png'
    _image(cover, 'red')
    item = BrowserItem(folder.name, folder, BrowserItemKind.FOLDER, None)
    cache_dir = tmp_path / 'cache'
    cache = ThumbnailDiskCache(cache_dir)
    try:
        image = BrowserThumbnailProvider._load_folder(folder, 64)
        assert cache.put(item, 64, image, cover_path=cover)
    finally:
        cache.close()
    lazy = ThumbnailDiskCache(cache_dir, enabled=False)
    try:
        assert lazy.invalidate_source(folder, BrowserItemKind.FOLDER)
        assert not lazy.enabled
        lazy.set_enabled(True)
        assert lazy.get(item, 64) is None
    finally:
        lazy.close()


def test_browser_context_action_sets_and_clears_cover(tmp_path, qapp, monkeypatch):
    folder = tmp_path / '画像'
    folder.mkdir()
    image = folder / '表紙.png'
    _image(image, 'green')
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    store = MetadataStore(tmp_path / 'profile' / 'metadata.sqlite3')
    provider = BrowserThumbnailProvider(loader=lambda *_: None, disk_cache_enabled=False)
    window = BrowserWindow(
        config_manager=config, metadata_store=store,
        thumbnail_provider=provider, restore_initial_location=False,
    )
    try:
        window.item_model.set_items([
            BrowserItem(image.name, image, BrowserItemKind.IMAGE, None),
        ])
        window.show()
        qapp.processEvents()
        window.list_view.doItemsLayout()

        class SelectingMenu(QMenu):
            target = 'これを親フォルダーの表紙に設定'
            enabled = False

            def exec(self, _point):
                action = next(action for action in self.actions() if action.text() == self.target)
                type(self).enabled = action.isEnabled()
                return action if action.isEnabled() else None

        monkeypatch.setattr('app.browser_window.QMenu', SelectingMenu)
        point = window.list_view.visualRect(window.item_model.index(0, 0)).center()
        window._show_context_menu(point)
        deadline = time.monotonic() + 5
        while window._folder_cover_workers and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert not window._folder_cover_workers
        assert has_nivis_cover(folder)
        assert SelectingMenu.enabled

        window.item_model.set_items([
            BrowserItem(folder.name, folder, BrowserItemKind.FOLDER, None),
        ])
        window.list_view.doItemsLayout()
        SelectingMenu.target = '表紙設定を削除'
        point = window.list_view.visualRect(window.item_model.index(0, 0)).center()
        window._show_context_menu(point)
        deadline = time.monotonic() + 5
        while window._folder_cover_workers and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert not window._folder_cover_workers
        assert not has_nivis_cover(folder)
        assert image.exists()
        window._show_context_menu(point)
        assert not SelectingMenu.enabled
        config.apply({'browser_use_zippla_cover': True}, save=False)
        assert window.browser_use_zippla_cover
        assert provider._zippla_cover_enabled
        assert window._current_browser_visibility_policy().use_zippla_cover
    finally:
        window.close()
        provider.close()
        store.close()
        qapp.processEvents()
