"""Deterministic widget fixtures, optionally captured across Python runtimes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPalette, QRawFont
from PySide6.QtWidgets import QWidget

from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.favorite_editor_dialog import FavoriteEditorDialog
from app.settings_dialog import SettingsDialog
from app.viewer_window import ViewerWindow


def _snapshot(widget):
    records = []

    def walk(current, key):
        rectangle = current.geometry()
        hint = current.sizeHint()
        minimum = current.minimumSizeHint()
        records.append(dict(key=key, class_name=type(current).__name__,
                            geometry=[rectangle.x(), rectangle.y(), rectangle.width(), rectangle.height()],
                            hint=[hint.width(), hint.height()], minimum=[minimum.width(), minimum.height()],
                            visible=current.isVisibleTo(widget), enabled=current.isEnabled()))
        for index, child in enumerate(current.children()):
            if isinstance(child, QWidget):
                walk(child, f"{key}/{index}:{child.objectName()}")

    walk(widget, "root")
    # Top-level placement belongs to the offscreen platform, not this layout.
    records[0]["geometry"][:2] = [0, 0]
    return records


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("size", [(800, 600), (1100, 760)])
def test_major_widgets_have_stable_geometry_and_japanese_text(qapp, tmp_path, theme, size):
    previous_palette = qapp.palette()
    previous_font = qapp.font()
    previous_style = qapp.style().objectName()
    # Windows offscreen Qt does not enumerate the system font collection.
    # Load the same installed Japanese font for both interpreter comparisons.
    font_path = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts/meiryo.ttc"
    if not font_path.is_file():
        pytest.skip("Windows Meiryo font required for the Japanese layout probe")
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    assert font_id >= 0
    font = QFont("Meiryo UI", 9)
    raw_font = QRawFont.fromFont(font)
    assert raw_font.isValid() and all(raw_font.supportsCharacter(char) for char in "日本語ABC")
    qapp.setStyle("Fusion")
    qapp.setFont(font)
    palette = QPalette(qapp.style().standardPalette())
    if theme == "dark":
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
            palette.setColor(role, QColor("#202020"))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
            palette.setColor(role, QColor("#eeeeee"))
    qapp.setPalette(palette)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"ui_language": "ja", "browser_show_favorites": True,
                  "browser_show_history": True, "browser_show_folder_tree": True})
    browser = None
    viewer = None
    settings = None
    favorites = None
    captures = {"font": {"family": raw_font.familyName(),
                         "sha256": hashlib.sha256(font_path.read_bytes()).hexdigest()}}
    destination = os.environ.get("NIVIS_LAYOUT_CAPTURE_DIR")
    if destination:
        Path(destination).mkdir(parents=True, exist_ok=True)

    def capture(name, widget):
        qapp.processEvents()
        assert widget.width() > 0 and widget.height() > 0
        records = _snapshot(widget)
        assert all(entry["geometry"][2] >= 0 and entry["geometry"][3] >= 0 for entry in records)
        image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        assert not image.isNull()
        captures[name] = dict(widgets=records, dpr=image.devicePixelRatio(),
                              pixels=[image.width(), image.height()],
                              rgba_sha256=hashlib.sha256(bytes(image.constBits())).hexdigest())
        if destination:
            assert image.save(str(Path(destination) / f"{theme}-{size[0]}-{name}.png"))

    try:
        browser = BrowserWindow(config_manager=config, restore_initial_location=False)
        items = [BrowserItem(f"日本語の画像 {i}.png", tmp_path / f"{i}.png", BrowserItemKind.IMAGE, None) for i in range(3)]
        browser.item_model.set_items(items)
        thumbnail = QImage(90, 140, QImage.Format.Format_RGB32)
        thumbnail.fill(QColor("#4088cc"))
        for item in items:
            browser.item_model.set_thumbnail_image(item.path, thumbnail)
        browser.resize(*size)
        browser.show()
        browser.list_view.doItemsLayout()
        capture("browser", browser)
        # Include each real sidebar view's geometry and font metrics even if
        # the configured tab currently hides it.
        captures["sidebar"] = {name: _snapshot(getattr(browser, name)) for name in ("favorite_view", "history_view", "folder_tree", "list_view")}

        viewer = ViewerWindow(config_manager=config)
        viewer.resize(*size)
        viewer.statusBar().showMessage("日本語のページ情報：1 / 8")
        viewer.show()
        capture("viewer", viewer)
        viewer.fullscreen_chrome.set_fullscreen_state(True, hide_ui=False, hide_cursor=False)
        capture("viewer-chrome", viewer)
        chrome = viewer.fullscreen_chrome
        assert chrome.bottom_overlay.geometry().bottom() == chrome.overlay_parent.height() - 1
        assert chrome.bottom_overlay.isVisible()

        settings = SettingsDialog(config)
        settings._initial_probe_started = True  # No external tool detection.
        settings.show()
        for index in range(settings.tabs.count()):
            settings.tabs.setCurrentIndex(index)
            capture(f"settings-{index}", settings)

        favorites = FavoriteEditorDialog(
            [SimpleNamespace(path="C:/fixture/日本語", display_name="お気に入りの日本語フォルダー")],
            {"C:/fixture/日本語": "#4088cc"}, [],
        )
        favorites._add_separator()
        favorites.show()
        capture("favorites", favorites)
        if destination:
            (Path(destination) / f"{theme}-{size[0]}.json").write_text(json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        for dialog in (favorites, settings):
            if dialog is not None:
                dialog.reject()
                dialog.deleteLater()
        if viewer is not None:
            viewer.prepare_shutdown(wait_msecs=5000)
            viewer.close()
        if browser is not None:
            browser.prepare_shutdown()
            browser.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()
        qapp.setStyle(previous_style)
        qapp.setPalette(previous_palette)
        qapp.setFont(previous_font)
        QFontDatabase.removeApplicationFont(font_id)
