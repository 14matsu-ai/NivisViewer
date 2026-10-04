import zipfile
from contextlib import closing
from threading import Event

import pytest
from PIL import Image

from app.application_controller import ApplicationController
from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from app.supported_formats import configure_xcf_loading, xcf_loading_enabled
from app.creative_image_decoder import decode_creative_image, CreativeImageUnsupportedError
from app.image_source import FolderImageSource, ZipImageSource
from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_scanner import scan_directory, BrowserScanRequest
from app.thumbnail_provider import BrowserThumbnailProvider


def test_default_off_blocks_real_entry_paths_before_any_decoder(tmp_path, qapp, monkeypatch):
    from app import creative_image_decoder as decoder
    previous = xcf_loading_enabled()
    config = ConfigManager(tmp_path / "settings.json")
    controller = ApplicationController(qapp, config_manager=config)
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    try:
        assert config.get("xcf_loading_enabled") is False
        assert not xcf_loading_enabled()
        def forbidden(*args, **kwargs):
            pytest.fail("Disabled XCF reached an image decoder")
        monkeypatch.setattr(decoder, "_decode_xcf_native", forbidden)
        monkeypatch.setattr(decoder, "render_xcf_with_gimp", forbidden)
        folder = tmp_path / "画像"
        folder.mkdir()
        xcf = folder / "先頭.xcf"
        xcf.write_bytes(b"placeholder")
        png = folder / "次.png"
        Image.new("RGB", (2,2)).save(png)
        with pytest.raises(CreativeImageUnsupportedError, match="disabled"):
            decode_creative_image(xcf, suffix=".xcf")
        with closing(FolderImageSource(folder)) as source:
            assert all(not name.endswith(".xcf") for name in source.list_images())
        archive = tmp_path / "画像.zip"
        with zipfile.ZipFile(archive,"w") as z:
            z.write(xcf, xcf.name)
            z.write(png, png.name)
        with closing(ZipImageSource(archive)) as source:
            assert source.list_images() == [png.name]
        batches = []
        scan_directory(BrowserScanRequest(str(folder), generation=1), Event(), batches.append)
        assert not any(e.item_kind == "image" and e.path.endswith(".xcf")
                       for b in batches for e in b.entries)
        generation = provider.begin_generation()
        assert not provider.request(BrowserItem(xcf.name,xcf,BrowserItemKind.IMAGE,None),64,generation=generation)
        assert provider.pending_count == 0
    finally:
        provider.close()
        controller.shutdown()
        configure_xcf_loading(previous)


def test_xcf_opt_in_is_saved_and_applied_on_next_start(tmp_path, qapp):
    previous = xcf_loading_enabled()
    config = ConfigManager(tmp_path / "settings.json")
    controller = ApplicationController(qapp, config_manager=config)
    dialog = SettingsDialog(config)
    try:
        assert not dialog.xcf_loading_checkbox.isChecked()
        dialog.xcf_loading_checkbox.setChecked(True)
        dialog.apply_settings()
        assert not xcf_loading_enabled()  # Explicit restart contract
    finally:
        dialog.reject()
        controller.shutdown()
    controller = ApplicationController(qapp, config_manager=ConfigManager(config.path))
    dialog = SettingsDialog(controller.config)
    try:
        assert xcf_loading_enabled()
        assert dialog.xcf_loading_checkbox.isChecked()
        dialog._reset_tab_draft("archive")
        assert not dialog.xcf_loading_checkbox.isChecked()
        dialog.apply_settings()
        restored = ConfigManager(config.path)
        restored.load()
        assert restored.get("xcf_loading_enabled") is False
    finally:
        dialog.reject()
        controller.shutdown()
        configure_xcf_loading(previous)
