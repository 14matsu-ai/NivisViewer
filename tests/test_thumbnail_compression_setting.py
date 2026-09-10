from dataclasses import replace
from io import BytesIO
from threading import Event, Thread

import pytest
from PIL import Image
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QSignalSpy

from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec


@pytest.mark.parametrize("value,expected", [
    (None, 60), (True, 60), (False, 60), (1.5, 60), ("70", 60),
    (-1, 60), (0, 60), (101, 60), (1, 1), (60, 60), (70, 70), (100, 100),
])
def test_compression_quality_normalizes_and_persists(tmp_path, value, expected):
    config = ConfigManager(tmp_path / "config.json")
    assert config.load()["thumbnail_webp_quality"] == 60
    config.apply({"thumbnail_webp_quality": value}, save=True)
    assert config.get("thumbnail_webp_quality") == expected
    assert ConfigManager(config.path).load()["thumbnail_webp_quality"] == expected


def test_real_settings_compression_control_cancel_apply_and_runtime(tmp_path, qapp, monkeypatch):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"thumbnail_size": 149, "thumbnail_quality_mode": "auto",
                  "thumbnail_frame_ratio": "portrait_1_sqrt2", "thumbnail_cache_max_edge": 512})
    window = BrowserWindow(config_manager=config, restore_initial_location=False)
    dialog = None
    try:
        window.show()
        old = window.thumbnail_render_spec
        generation = window._generation
        with monkeypatch.context() as guard:
            guard.setattr(window, "refresh_current_folder", lambda: pytest.fail("compression rescanned folder"))
            guard.setattr(window, "_apply_list_view_geometry", lambda: pytest.fail("compression changed geometry"))
            guard.setattr(window, "_schedule_thumbnail_requests", lambda *_: None)
            for apply in (False, True):
                dialog = SettingsDialog(config)
                dialog.tabs.setCurrentIndex(1)
                dialog.show()
                control = dialog.thumbnail_webp_quality_spin
                dialog.tabs.currentWidget().ensureWidgetVisible(control)
                qapp.processEvents()
                assert control.isVisible()
                viewport = dialog.tabs.currentWidget().viewport()
                assert viewport.rect().contains(control.mapTo(viewport, control.rect().center()))
                assert (control.minimum(), control.maximum(), control.value()) == (1, 100, 60)
                assert control.parentWidget().title() == "サムネイル"
                assert control.parentWidget().layout().labelForField(control).text() == "保存サムネイルの圧縮品質:"
                control.setValue(37)
                assert config.get("thumbnail_webp_quality") == 60
                assert window.thumbnail_render_spec == old
                if apply:
                    dialog.apply_settings()
                dialog.reject()
                qapp.processEvents()
            assert config.get("thumbnail_webp_quality") == 37
            new = window.thumbnail_render_spec
            assert new.encoder_quality == 37 and new.cache_token != old.cache_token
            assert replace(new, encoder_quality=old.encoder_quality) == old
            assert window._generation == generation + 1
            cache = window.thumbnail_provider._disk_cache
            assert cache.encoder_quality == 37
            assert cache.format_version == f"3-webp-q37-{new.encoding_policy.alpha_token}"
            stable_generation = window._generation
            config.apply({"thumbnail_webp_quality": 37, "slideshow_interval_ms": 4200})
            assert window._generation == stable_generation
            assert window.thumbnail_render_spec == new
        saved = ConfigManager(config.path)
        assert saved.load()["thumbnail_webp_quality"] == 37
        reopened = SettingsDialog(saved)
        try:
            assert reopened.thumbnail_webp_quality_spin.value() == 37
        finally:
            reopened.reject()
    finally:
        if dialog is not None:
            dialog.reject()
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize("quality", [1, 37, 60, 100])
@pytest.mark.parametrize("alpha", [255, 127])
def test_request_owned_quality_encodes_correct_bytes(tmp_path, quality, alpha):
    path = tmp_path / "source.png"
    path.write_bytes(b"fingerprint only")
    item = BrowserItem(path.name, path, BrowserItemKind.IMAGE, None)
    cache = ThumbnailDiskCache(tmp_path / "cache", encoder_quality=quality)
    image = QImage(24, 32, QImage.Format.Format_RGBA8888)
    image.fill(QColor(15, 120, 170, alpha))
    spec = ThumbnailRenderSpec.from_settings(149, "portrait_1_sqrt2", "letterbox", encoder_quality=quality)
    try:
        assert cache.put(item, spec, image)
        expected = BytesIO()
        pixels = spec.encoding_policy.prepare_pixels(cache._qimage_to_pil(image))
        if cache._encoder == "WEBP":
            pixels.save(expected, format="WEBP", **spec.encoding_policy.webp_options())
        else:
            pixels.save(expected, format="PNG", optimize=False)
        assert next(cache.files_dir.iterdir()).read_bytes() == expected.getvalue()
        cache.close()
        cache = ThumbnailDiskCache(tmp_path / "cache", encoder_quality=quality)
        assert cache.get_suitable(item, spec) is not None  # No restart invalidation.
        assert cache.get_suitable(item, replace(spec, encoder_quality=quality % 100 + 1)) is None
    finally:
        cache.close()


def test_quality_change_during_encode_keeps_old_spec_identity_and_fences_publish(tmp_path, qapp, monkeypatch):
    path = tmp_path / "source.png"
    path.write_bytes(b"fingerprint only")
    item = BrowserItem(path.name, path, BrowserItemKind.IMAGE, None)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    provider = BrowserThumbnailProvider(disk_cache=cache)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = BrowserWindow(config_manager=config, thumbnail_provider=provider, restore_initial_location=False)
    image = QImage(32, 48, QImage.Format.Format_RGB888)
    image.fill(QColor("green"))
    entered, release, done = Event(), Event(), Event()
    results, failures, encoded_options = [], [], []
    old_spec, old_generation = window.thumbnail_render_spec, window._generation
    original_save = Image.Image.save

    def gated_save(pixels, *args, **kwargs):
        encoded_options.append(kwargs.copy())
        entered.set()
        assert release.wait(5), "GUI change blocked behind the encoding lock"
        return original_save(pixels, *args, **kwargs)

    def encode():
        try:
            results.append(cache.put(item, old_spec, image))
        except BaseException as error:
            failures.append(error)
        finally:
            done.set()

    worker = Thread(target=encode)
    try:
        with monkeypatch.context() as guard:
            guard.setattr(Image.Image, "save", gated_save)
            guard.setattr(window, "_schedule_thumbnail_requests", lambda *_: None)
            worker.start()
            assert entered.wait(5)
            config.apply({"thumbnail_webp_quality": 23}, save=True)
            assert not done.is_set()  # Change did not wait for disk work.
            assert cache.encoder_quality == window.thumbnail_render_spec.encoder_quality == 23
            release.set()
            worker.join(5)
        assert not worker.is_alive() and not failures and results == [True]
        if cache._encoder == "WEBP":
            assert encoded_options[0]["quality"] == 60
        row = cache._connection.execute("SELECT format_version, thumbnail_size FROM entries").fetchone()
        assert "-q60-" in row[0] and row[1] == old_spec.cache_token
        new_spec = window.thumbnail_render_spec
        assert cache.get_suitable(item, new_spec) is None
        assert cache.put(item, new_spec, image)
        assert cache.get_suitable(item, new_spec) is not None
        spy = QSignalSpy(provider.thumbnail_ready)
        provider._on_finished(str(path), old_generation, old_spec.cache_token, None, image)
        assert spy.count() == 0
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(5)
        window.close()
        provider.close()
        qapp.processEvents()
