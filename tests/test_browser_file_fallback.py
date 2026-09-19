from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from app.browser_item_delegate import BrowserItemDelegate, thumbnail_content_rect
from app.browser_model import BrowserItemKind, BrowserItemModel
from app.browser_sort import BrowserDisplayDensity
from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from tests.test_browser_grid import AlphaShellIconProvider, RecordingThumbnailProvider, make_item, make_window


@pytest.mark.parametrize("kind", [BrowserItemKind.OTHER, BrowserItemKind.IMAGE, BrowserItemKind.PDF, BrowserItemKind.ARCHIVE])
@pytest.mark.parametrize("state", ["unsupported", "pending", "cancelled", "error"])
@pytest.mark.parametrize("dpr", [1., 1.25, 1.5, 2.])
@pytest.mark.parametrize("mode,density", [("fit", BrowserDisplayDensity.COMPACT), ("center_crop", BrowserDisplayDensity.STANDARD)])
def test_file_fallback_pixels_and_success_transition(qapp, tmp_path, kind, state, dpr, mode, density):
    delegate = BrowserItemDelegate(file_fallback_background="#31597d", folder_fallback_background="#804020",
                                   thumbnail_display_mode=mode, density=density,
                                   shell_icon_provider=AlphaShellIconProvider())
    item = replace(make_item(tmp_path / "name.ext", kind), preview_status=state,
                   can_generate_preview=state != "unsupported", rating=3)
    model = BrowserItemModel()
    model.set_items([item])
    icon = QPixmap(32, 32)
    icon.fill(QColor("#e03030"))
    model.set_fallback_icons({kind: QIcon(icon)})
    if state == "error":
        model.set_thumbnail_error(item.path, "failed")
    option = QStyleOptionViewItem()
    option.rect = QRect(QPoint(), delegate.cell_size)
    option.palette = qapp.palette()
    option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Selected
    frame = delegate.grid_metrics.thumbnail_frame_rect(option.rect)
    content = thumbnail_content_rect(frame, display_mode=mode, device_pixel_ratio=dpr)

    def render():
        canvas = QImage(QSize(round(option.rect.width() * dpr), round(option.rect.height() * dpr)), QImage.Format.Format_RGB32)
        canvas.setDevicePixelRatio(dpr)
        canvas.fill(Qt.GlobalColor.white)
        painter = QPainter(canvas)
        delegate.paint(painter, option, model.index(0, 0))
        painter.end()
        return canvas

    point = QPoint(round((content.right() - 4) * dpr), round((content.bottom() - 4) * dpr))
    canvas = render()
    assert canvas.pixelColor(point) == QColor("#31597d")
    assert canvas.pixelColor(round(content.center().x() * dpr), round(content.center().y() * dpr)) == QColor("#e03030")
    # Whole-canvas pixels show the fill is bounded by the shared content rect.
    for y in range(canvas.height()):
        for x in range(canvas.width()):
            if canvas.pixelColor(x, y) == QColor("#31597d"):
                assert content.adjusted(-1 / dpr, -1 / dpr, 1 / dpr, 1 / dpr).contains(x / dpr, y / dpr)
    assert delegate.folder_fallback_background_color() == QColor("#804020")
    image = QImage(1000, 1000, QImage.Format.Format_RGB32)
    image.fill(QColor("#20a050"))
    model.set_thumbnail_image(item.path, image)
    # Even stale error metadata must never put a fill on top of a valid image.
    model.set_thumbnail_error(item.path, "old error")
    assert not delegate._uses_placeholder_canvas(item, image, "old error")
    canvas = render()
    assert canvas.pixelColor(round(content.center().x() * dpr), round(content.center().y() * dpr)) == QColor("#20a050")


@pytest.mark.parametrize("value,expected", [(None, "auto"), ("#31597D", "#31597d"), ("#000000", "#000000"), ("bad", "auto"), ("auto", "auto")])
def test_file_color_config_roundtrip(tmp_path, value, expected):
    config = ConfigManager(tmp_path / "settings.json")
    # Legacy config predates the independent file-background key.
    config.path.write_text('{"browser_folder_fallback_background":"#804020"}', encoding="utf-8")
    config.load()
    assert config.get("browser_file_fallback_background") == "auto"
    config.apply({"browser_folder_fallback_background": "#804020"})
    if value is not None:
        config.apply({"browser_file_fallback_background": value})
    config.save()
    restored = ConfigManager(config.path).load()
    assert restored["browser_file_fallback_background"] == expected
    assert restored["browser_folder_fallback_background"] == "#804020"


def test_file_color_settings_apply_reset_cancel_and_repaint_only(tmp_path, qapp):
    provider = RecordingThumbnailProvider()
    window = make_window(tmp_path, qapp, provider=provider)
    dialog = None
    patches = pytest.MonkeyPatch()
    try:
        window.item_delegate.shell_icon_provider = AlphaShellIconProvider()
        item = replace(make_item(tmp_path / "no-preview.bin", BrowserItemKind.OTHER),
                       can_generate_preview=False, preview_status="unsupported")
        window.item_model.set_items([item])
        qapp.processEvents()
        window._thumbnail_request_timer.stop()
        provider.requests.clear()
        generation, scan = provider.generation, window._scan_generation
        window.config.apply({"browser_folder_fallback_background": "#804020"})
        repaints = []
        viewport = window.list_view.viewport()
        update = viewport.update
        def repaint(*args):
            repaints.append(True)
            update(*args)
        patches.setattr(viewport, "update", repaint)
        dialog = SettingsDialog(window.config)
        assert dialog.browser_file_fallback_color_button.text() == "#C1C1C1"
        dialog.show()
        dialog.tabs.setCurrentIndex(1)
        browser_tab = dialog.tabs.currentWidget()
        browser_tab.ensureWidgetVisible(dialog.browser_file_fallback_restore_button)
        qapp.processEvents()
        assert dialog.tabs.tabText(1) == "Browser"
        assert dialog.browser_file_fallback_background_combo.isVisible()
        assert dialog.browser_file_fallback_restore_button.text() == "デフォルトに戻す"
        patches.setattr("app.settings_dialog.QColorDialog.getColor", lambda *_: QColor("#31597d"))
        dialog.browser_file_fallback_background_combo.setCurrentIndex(1)
        dialog.browser_file_fallback_color_button.click()
        assert window.config.get("browser_file_fallback_background") == "auto"
        dialog.apply_settings()
        qapp.processEvents()
        assert window.item_delegate.file_fallback_background_color() == QColor("#31597d")
        assert window.item_delegate.folder_fallback_background_color() == QColor("#804020")
        assert ConfigManager(window.config.path).load()["browser_file_fallback_background"] == "#31597d"
        dialog.browser_file_fallback_restore_button.click()
        assert dialog.values()["browser_file_fallback_background"] == "auto"
        assert dialog.browser_file_fallback_color_button.text() == "#C1C1C1"
        dialog.reject()  # Cancel unapplied reset.
        assert window.config.get("browser_file_fallback_background") == "#31597d"
        dialog = SettingsDialog(window.config)
        assert dialog.browser_file_fallback_background_combo.currentData() == "custom"
        for _ in range(2):
            dialog.browser_file_fallback_restore_button.click()
            dialog.apply_settings()
        qapp.processEvents()
        assert window.item_delegate.file_fallback_background_color() == QColor("#c1c1c1")
        assert ConfigManager(window.config.path).load()["browser_file_fallback_background"] == "auto"
        assert window.item_delegate.folder_fallback_background_color() == QColor("#804020")
        assert provider.generation == generation and window._scan_generation == scan
        assert not provider.requests
        assert repaints
    finally:
        # Browser owns a WA_DeleteOnClose QObject tree. Undo the bound-method
        # patch while the viewport is alive, before close/deferred deletion.
        patches.undo()
        if dialog is not None:
            dialog.reject()
        window.close()
        qapp.processEvents()


def test_fallback_settings_have_one_repaint_only_effect_boundary(tmp_path, qapp):
    provider = RecordingThumbnailProvider()
    window = make_window(tmp_path, qapp, provider=provider)
    try:
        window._thumbnail_request_timer.stop()
        provider.requests.clear()
        generation, scan = provider.generation, window._scan_generation
        calls, repaints = [], []
        configure = window.item_delegate.configure
        update = window.list_view.viewport().update
        def configured(**kwargs):
            calls.append(kwargs)
            configure(**kwargs)
        def repainted(*args):
            repaints.append(True)
            update(*args)
        with pytest.MonkeyPatch.context() as patches:
            patches.setattr(window.item_delegate, "configure", configured)
            patches.setattr(window.list_view.viewport(), "update", repainted)
            patches.setattr(window.item_delegate._display_surface_cache, "clear",
                            lambda: pytest.fail("color-only change cleared display cache"))
            window.config.apply({"browser_folder_fallback_background": "#804020",
                                 "browser_file_fallback_background": "#31597d"})
            assert len(calls) == len(repaints) == 1
            assert calls[-1]["folder_fallback_background"] == "#804020"
            assert calls[-1]["file_fallback_background"] == "#31597d"
            window.config.apply({"browser_file_fallback_background": "#31597d"})
            window._apply_fallback_background_settings({"unrelated": True})
            assert len(calls) == len(repaints) == 1
            window.config.apply({"browser_file_fallback_background": "auto"})
            assert len(calls) == len(repaints) == 2
            assert calls[-1]["folder_fallback_background"] == "#804020"
            assert calls[-1]["file_fallback_background"] == "auto"
        assert provider.generation == generation and window._scan_generation == scan
        assert provider.requests == []
    finally:
        window.close()
        qapp.processEvents()
