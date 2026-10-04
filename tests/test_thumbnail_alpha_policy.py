from dataclasses import replace
from io import BytesIO
from threading import Event, Thread

from PIL import Image
import pytest
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QSignalSpy

from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec, pil_to_qimage


def source_item(tmp_path):
    source = tmp_path / "synthetic.png"
    source.write_bytes(b"temporary fingerprint")
    return BrowserItem(source.name, source, BrowserItemKind.IMAGE, None)


def alpha_pixels():
    image = Image.new("RGBA", (256, 8))
    image.putdata([(x, (x * 19 + y * 71) % 256, (x * 53) % 256, x)
                   for y in range(8) for x in range(256)])
    return image


@pytest.mark.parametrize("value,expected", [(None, False), (1, False), ("true", False), (False, False), (True, True)])
def test_alpha_setting_normalization_preserves_quality40(tmp_path, value, expected):
    config = ConfigManager(tmp_path / "config.json")
    assert config.load()["thumbnail_preserve_alpha"] is False
    config.apply({"thumbnail_webp_quality": 40, "thumbnail_preserve_alpha": value}, save=True)
    loaded = ConfigManager(config.path).load()
    assert loaded["thumbnail_webp_quality"] == 40
    assert loaded["thumbnail_preserve_alpha"] is expected


@pytest.mark.parametrize("preserve", [False, True])
@pytest.mark.parametrize("png", [False, True])
def test_saved_alpha_policy_and_exact_alpha_plane(tmp_path, monkeypatch, preserve, png):
    if png:
        monkeypatch.setattr(ThumbnailDiskCache, "_select_encoder", staticmethod(lambda: ("PNG", "png")))
    item = source_item(tmp_path)
    pixels = alpha_pixels()
    spec = ThumbnailRenderSpec(256, 256, "square_1_1", "letterbox",
                               encoder_quality=40, preserve_alpha=preserve, matte_color="#204060")
    cache = ThumbnailDiskCache(tmp_path / "cache", encoder_quality=40, preserve_alpha=preserve, matte_color="#204060")
    try:
        qimage = pil_to_qimage(pixels)
        original = qimage.copy()
        assert cache.put(item, spec, qimage)
        assert qimage == original  # Caller-owned pixels never mutated.
        data = next(cache.files_dir.iterdir()).read_bytes()
        with Image.open(BytesIO(data)) as decoded:
            actual = decoded.convert("RGBA")
        if preserve:
            assert actual.getchannel("A").tobytes() == pixels.getchannel("A").tobytes()
            if not png:
                assert b"VP8 " in data and b"ALPH" in data
                assert actual.convert("RGB").tobytes() != pixels.convert("RGB").tobytes()
        else:
            assert actual.getchannel("A").getextrema() == (255, 255)
        prepared = pixels if preserve else Image.alpha_composite(
            Image.new("RGBA", pixels.size, "#204060"), pixels).convert("RGB")
        expected = BytesIO()
        if png:
            prepared.save(expected, format="PNG", optimize=False)
        else:
            prepared.save(expected, format="WEBP", lossless=False, quality=40,
                          alpha_quality=100, exact=True, method=4)
        assert data == expected.getvalue()
        assert cache.get_suitable(item, replace(spec, preserve_alpha=not preserve)) is None
        changed_matte = replace(spec, matte_color="#aa9966")
        assert (changed_matte.cache_token == spec.cache_token) is preserve
        assert (cache.get_suitable(item, changed_matte) is not None) is preserve
    finally:
        cache.close()


@pytest.mark.parametrize("preserve", [False, True])
@pytest.mark.parametrize("premultiplied", [False, True])
def test_fresh_and_reloaded_compositing_matches_once(tmp_path, monkeypatch, preserve, premultiplied):
    # PNG isolates exact compositing from expected lossy RGB error.
    monkeypatch.setattr(ThumbnailDiskCache, "_select_encoder", staticmethod(lambda: ("PNG", "png")))
    item = source_item(tmp_path)
    qimage = QImage(16, 16, QImage.Format.Format_ARGB32_Premultiplied if premultiplied else QImage.Format.Format_RGBA8888)
    qimage.fill(QColor(240, 60, 30, 128))
    straight = ThumbnailDiskCache._qimage_to_pil(qimage)
    spec = ThumbnailRenderSpec(16, 16, "square_1_1", "letterbox", encoder_quality=40,
                               preserve_alpha=preserve, matte_color="#204060")
    cache = ThumbnailDiskCache(tmp_path / "cache")
    provider = BrowserThumbnailProvider(disk_cache=cache, loader=lambda *_: qimage)
    try:
        first = provider._load_pipeline(item, spec).image
        reloaded = provider._load_pipeline(item, spec).image
        assert first is not None and reloaded is not None
        assert ThumbnailDiskCache._qimage_to_pil(first).tobytes() == ThumbnailDiskCache._qimage_to_pil(reloaded).tobytes()
        expected = straight if preserve else Image.alpha_composite(Image.new("RGBA", straight.size, "#204060"), straight)
        assert ThumbnailDiskCache._qimage_to_pil(first).tobytes() == expected.tobytes()
        assert qimage.pixelColor(0, 0).alpha() == 128
    finally:
        provider.close()


def test_real_settings_alpha_cancel_apply_and_palette_identity(tmp_path, qapp, monkeypatch):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"thumbnail_webp_quality": 40}, save=True)
    window = BrowserWindow(config_manager=config, restore_initial_location=False)
    try:
        monkeypatch.setattr(window, "_schedule_thumbnail_requests", lambda *_: None)
        monkeypatch.setattr(window, "refresh_current_folder", lambda: pytest.fail("alpha setting rescanned"))
        monkeypatch.setattr(window, "_apply_list_view_geometry", lambda: pytest.fail("alpha setting changed geometry"))
        original = window.thumbnail_render_spec
        assert original.encoder_quality == 40 and not original.preserve_alpha
        assert original.matte_color == window.list_view.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Base).name()
        for apply in (False, True):
            dialog = SettingsDialog(config)
            dialog.tabs.setCurrentIndex(1)
            dialog.show()
            control = dialog.thumbnail_preserve_alpha_checkbox
            dialog.tabs.currentWidget().ensureWidgetVisible(control)
            qapp.processEvents()
            viewport = dialog.tabs.currentWidget().viewport()
            assert control.text() == "保存サムネイルの透明度を保持"
            assert viewport.rect().contains(control.mapTo(viewport, control.rect().center()))
            assert not control.isChecked()
            assert dialog.thumbnail_webp_quality_spin.value() == 40
            assert "\n" in dialog.thumbnail_preserve_alpha_help_button.toolTip()
            assert dialog.thumbnail_preserve_alpha_help_button.accessibleName() == (
                "保存サムネイルの透明度の説明"
            )
            control.setChecked(True)
            assert config.get("thumbnail_preserve_alpha") is False
            if apply: dialog.apply_settings()
            dialog.reject()
        assert window.thumbnail_render_spec.preserve_alpha
        assert config.get("thumbnail_webp_quality") == 40
        saved = ConfigManager(config.path)
        saved.load()
        reopened = SettingsDialog(saved)
        assert reopened.thumbnail_preserve_alpha_checkbox.isChecked()
        reopened.reject()
        generation = window._generation
        token = window.thumbnail_render_spec.cache_token
        palette = window.list_view.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor("#123456"))
        window.list_view.setPalette(palette)
        assert window._generation == generation and window.thumbnail_render_spec.cache_token == token
        config.apply({"thumbnail_preserve_alpha": False}, save=True)
        assert window.thumbnail_render_spec.matte_color == "#123456"
        generation = window._generation
        token = window.thumbnail_render_spec.cache_token
        palette.setColor(QPalette.ColorRole.Base, QColor("#654321"))
        window.list_view.setPalette(palette)
        assert window._generation == generation + 1
        assert window.thumbnail_render_spec.cache_token != token
        assert window.thumbnail_provider._disk_cache.encoding_policy == window.thumbnail_render_spec.encoding_policy
        generation = window._generation
        config.apply({"browser_folder_fallback_background": "#112233", "browser_file_fallback_background": "#445566"})
        assert window._generation == generation
    finally:
        window.close()
        qapp.processEvents()


def test_old_lossless_q40_identity_excluded_and_lazily_retired(tmp_path, monkeypatch):
    item = source_item(tmp_path)
    spec = ThumbnailRenderSpec(16, 16, "square_1_1", "letterbox", encoder_quality=40)
    old = ThumbnailDiskCache(tmp_path / "cache", encoder_quality=40)
    with monkeypatch.context() as legacy:
        legacy.setattr(old, "_format_version_for_policy", lambda _: "3-webp-q40-alpha-lossless-e90")
        assert old.put(item, spec, pil_to_qimage(alpha_pixels()), page_count=42)
    old.close()
    cache = ThumbnailDiskCache(tmp_path / "cache", encoder_quality=40)
    try:
        assert cache.statistics()["entry_count"] == 1  # No startup wipe.
        assert cache.get_suitable(item, spec) is None
        assert cache.get_page_count(item) == 42
        assert cache.prune() == 1
    finally:
        cache.close()


def test_inflight_matte_and_alpha_change_is_atomic_and_nonblocking(tmp_path, qapp, monkeypatch):
    item = source_item(tmp_path)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"thumbnail_webp_quality": 40})
    cache = ThumbnailDiskCache(tmp_path / "cache", encoder_quality=40)
    provider = BrowserThumbnailProvider(disk_cache=cache)
    window = BrowserWindow(config_manager=config, thumbnail_provider=provider, restore_initial_location=False)
    entered, release = Event(), Event()
    failures, options = [], []
    old_spec, old_generation = window.thumbnail_render_spec, window._generation
    original_save = Image.Image.save
    image = pil_to_qimage(alpha_pixels())
    def save(pixels, *args, **kwargs):
        options.append((pixels.mode, kwargs.copy()))
        entered.set()
        assert release.wait(5), "GUI blocked on encoding"
        return original_save(pixels, *args, **kwargs)
    def encode():
        try: assert cache.put(item, old_spec, image)
        except BaseException as error: failures.append(error)
    worker = Thread(target=encode)
    try:
        with monkeypatch.context() as guard:
            guard.setattr(Image.Image, "save", save)
            guard.setattr(window, "_schedule_thumbnail_requests", lambda *_: None)
            worker.start()
            assert entered.wait(5)
            palette = window.list_view.palette()
            palette.setColor(QPalette.ColorRole.Base, QColor("#123456"))
            window.list_view.setPalette(palette)
            config.apply({"thumbnail_preserve_alpha": True}, save=True)
            assert worker.is_alive()
            assert window.thumbnail_render_spec.preserve_alpha and cache.encoding_policy.preserve_alpha
            release.set()
            worker.join(5)
        assert not worker.is_alive() and not failures
        assert options[0][0] == "RGB" and options[0][1]["quality"] == 40
        row = cache._connection.execute("SELECT format_version FROM entries").fetchone()
        assert row[0].endswith(old_spec.encoding_policy.alpha_token)
        assert cache.get_suitable(item, window.thumbnail_render_spec) is None
        spy = QSignalSpy(provider.thumbnail_ready)
        provider._on_finished(str(item.path), old_generation, old_spec.cache_token, None, image)
        assert spy.count() == 0
    finally:
        release.set()
        if worker.ident is not None: worker.join(5)
        window.close()
        provider.close()
        qapp.processEvents()
