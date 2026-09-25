from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QStyleOptionViewItem

from app.browser_item_delegate import BrowserItemDelegate
from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.file_operation_service import FileOperationKind
from app.i18n import install_ui_language
from app.settings_dialog import SettingsDialog


def make_config(tmp_path: Path, folder: Path | None = None) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    if folder is not None:
        config.set("last_browser_path", str(folder))
    return config


def test_overlay_config_defaults_clamp_and_reject_invalid_booleans(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    assert config.get("browser_show_rating_overlay") is True
    assert config.get("browser_show_tag_overlay") is True
    assert config.get("browser_rating_overlay_opacity") == 85
    assert config.get("browser_tag_overlay_opacity") == 100
    assert config.get("browser_tag_auto_text_color") is True
    assert config.get("browser_tag_text_luminance_threshold") == 150

    config.apply(
        {
            "browser_show_rating_overlay": "false",
            "browser_show_tag_overlay": None,
            "browser_rating_overlay_opacity": -20,
            "browser_tag_overlay_opacity": 120,
            "browser_tag_auto_text_color": 1,
            "browser_tag_text_luminance_threshold": 999,
        }
    )
    assert config.get("browser_show_rating_overlay") is True
    assert config.get("browser_show_tag_overlay") is True
    assert config.get("browser_rating_overlay_opacity") == 0
    assert config.get("browser_tag_overlay_opacity") == 100
    assert config.get("browser_tag_auto_text_color") is True
    assert config.get("browser_tag_text_luminance_threshold") == 255


def test_browser_startup_applies_persisted_overlay_options(
    tmp_path: Path,
    qapp,
) -> None:
    folder = tmp_path / "books"
    folder.mkdir()
    config = make_config(tmp_path, folder)
    config.apply(
        {
            "browser_show_rating_overlay": False,
            "browser_show_tag_overlay": False,
            "browser_rating_overlay_opacity": 17,
            "browser_tag_overlay_opacity": 29,
            "browser_tag_auto_text_color": False,
            "browser_tag_text_luminance_threshold": 203,
        }
    )
    window = BrowserWindow(config_manager=config)
    assert window.wait_for_scan()
    assert window.item_delegate.show_rating_overlay is False
    assert window.item_delegate.show_tag_overlay is False
    assert window.item_delegate.rating_overlay_opacity == 17
    assert window.item_delegate.tag_overlay_opacity == 29
    assert window.item_delegate.tag_auto_text_color is False
    assert window.item_delegate.tag_text_luminance_threshold == 203
    window.close()
    qapp.processEvents()


def test_overlay_delegate_visibility_hit_testing_and_tag_alpha(qapp) -> None:
    delegate = BrowserItemDelegate(thumbnail_size=180)
    cell = QRect(0, 0, 240, 280)
    overlay = delegate.rating_overlay_rect(cell)
    point = QPoint(overlay.left() + 8, overlay.center().y())
    assert delegate.rating_at_position(cell, point) == 1

    delegate.configure(
        thumbnail_size=180,
        density=delegate.density,
        show_rating_overlay=False,
    )
    assert delegate.rating_at_position(cell, point) is None
    delegate.configure(
        thumbnail_size=180,
        density=delegate.density,
        show_rating_overlay=True,
        rating_overlay_opacity=0,
    )
    assert delegate.rating_at_position(cell, point) is None

    item = BrowserItem(
        "book {zpi$t=Bright}.png",
        Path("book {zpi$t=Bright}.png"),
        BrowserItemKind.IMAGE,
        0.0,
    )
    option = QStyleOptionViewItem()
    rect = QRect(0, 0, 180, 200)

    class Painter:
        def __init__(self) -> None:
            self.pens: list[QColor] = []
            self.fills: list[QColor] = []

        def save(self) -> None:
            pass

        def restore(self) -> None:
            pass

        def setClipRect(self, _rect: QRect) -> None:
            pass

        def setFont(self, _font) -> None:
            pass

        def fillRect(self, _rect: QRect, color: QColor) -> None:
            self.fills.append(QColor(color))

        def setPen(self, color: QColor) -> None:
            self.pens.append(QColor(color))

        def drawText(self, *_args) -> None:
            pass

    delegate.tag_registry = [{"name": "Bright", "color": "#ffffff"}]
    delegate.configure(
        thumbnail_size=180,
        density=delegate.density,
        show_tag_overlay=True,
        tag_overlay_opacity=50,
        tag_auto_text_color=True,
        tag_text_luminance_threshold=150,
    )
    painter = Painter()
    delegate._paint_tags(painter, option, rect, item)
    assert painter.fills and painter.fills[0].alpha() in range(126, 129)
    assert painter.pens and painter.pens[-1].name() == "#000000"

    delegate.configure(
        thumbnail_size=180,
        density=delegate.density,
        tag_auto_text_color=False,
    )
    painter = Painter()
    delegate._paint_tags(painter, option, rect, item)
    assert painter.pens and painter.pens[-1].name() == "#ffffff"


def test_browser_live_overlay_settings_repaint_without_rescanning(
    tmp_path: Path,
    qapp,
) -> None:
    folder = tmp_path / "books"
    folder.mkdir()
    image = folder / "page.png"
    image.write_bytes(b"image")
    config = make_config(tmp_path, folder)
    window = BrowserWindow(config_manager=config)
    assert window.wait_for_scan()
    qapp.processEvents()
    scan_generation = window._scan_generation
    provider_generation = window.thumbnail_provider.generation
    window._rating_hover_path = str(image)

    config.apply(
        {
            "browser_show_rating_overlay": False,
            "browser_show_tag_overlay": False,
            "browser_rating_overlay_opacity": 12,
            "browser_tag_overlay_opacity": 34,
            "browser_tag_auto_text_color": False,
            "browser_tag_text_luminance_threshold": 201,
        }
    )
    qapp.processEvents()

    assert window._scan_generation == scan_generation
    assert window.thumbnail_provider.generation == provider_generation
    assert window.item_delegate.show_rating_overlay is False
    assert window.item_delegate.show_tag_overlay is False
    assert window.item_delegate.rating_overlay_opacity == 12
    assert window.item_delegate.tag_overlay_opacity == 34
    assert window.item_delegate.tag_auto_text_color is False
    assert window.item_delegate.tag_text_luminance_threshold == 201
    assert window._rating_hover_path is None
    window.close()
    qapp.processEvents()


def test_snapshot_reconcile_allows_committed_path_and_blocks_other_path(
    tmp_path: Path,
    qapp,
    monkeypatch,
) -> None:
    folder = tmp_path / "books"
    other = tmp_path / "other"
    folder.mkdir()
    other.mkdir()
    config = make_config(tmp_path, folder)
    window = BrowserWindow(config_manager=config)
    assert window.wait_for_scan()
    qapp.processEvents()
    window._snapshot_reconcile_pending = True
    window.current_path = folder.absolute()
    execute_calls: list[object] = []
    monkeypatch.setattr(
        window.file_operation_coordinator,
        "execute",
        lambda request: execute_calls.append(request) or True,
    )

    window._pending_scan = None
    assert window._start_file_operation(
        FileOperationKind.CREATE_DIRECTORY,
        destination=folder,
        new_name="created",
    )
    assert len(execute_calls) == 1

    window._file_operation_requests.clear()
    window._pending_scan = SimpleNamespace(path=folder)
    assert window._start_file_operation(
        FileOperationKind.CREATE_DIRECTORY,
        destination=folder,
        new_name="created-same-path",
    )
    assert len(execute_calls) == 2

    window._file_operation_requests.clear()
    window._pending_scan = SimpleNamespace(path=other)
    assert not window._start_file_operation(
        FileOperationKind.CREATE_DIRECTORY,
        destination=folder,
        new_name="blocked",
    )
    assert len(execute_calls) == 2
    window.close()
    qapp.processEvents()


def test_overlay_settings_dialog_roundtrip_and_resets(tmp_path: Path, qapp) -> None:
    config = make_config(tmp_path)
    config.apply(
        {
            "browser_show_rating_overlay": False,
            "browser_show_tag_overlay": False,
            "browser_rating_overlay_opacity": 23,
            "browser_tag_overlay_opacity": 45,
            "browser_tag_auto_text_color": False,
            "browser_tag_text_luminance_threshold": 211,
        }
    )
    dialog = SettingsDialog(config)
    assert not dialog.browser_rating_overlay_opacity_spin.isEnabled()
    assert not dialog.browser_tag_overlay_opacity_spin.isEnabled()
    assert not dialog.browser_tag_text_luminance_threshold_spin.isEnabled()
    values = dialog.values()
    assert values["browser_rating_overlay_opacity"] == 23
    assert values["browser_tag_overlay_opacity"] == 45
    assert values["browser_tag_text_luminance_threshold"] == 211

    dialog.browser_show_rating_overlay_checkbox.setChecked(True)
    assert dialog.browser_rating_overlay_opacity_spin.isEnabled()
    assert not dialog.browser_tag_overlay_opacity_spin.isEnabled()
    dialog.browser_show_tag_overlay_checkbox.setChecked(True)
    dialog.browser_tag_auto_text_color_checkbox.setChecked(True)
    assert dialog.browser_rating_overlay_opacity_spin.isEnabled()
    assert dialog.browser_tag_overlay_opacity_spin.isEnabled()
    assert dialog.browser_tag_text_luminance_threshold_spin.isEnabled()
    dialog.browser_overlay_restore_button.click()
    assert dialog.values()["browser_rating_overlay_opacity"] == 85
    assert dialog.values()["browser_tag_overlay_opacity"] == 100
    assert dialog.values()["browser_tag_text_luminance_threshold"] == 150

    dialog.browser_rating_overlay_opacity_spin.setValue(11)
    dialog.browser_tag_overlay_opacity_spin.setValue(22)
    dialog._reset_browser_scope()
    reset = dialog.values()
    assert reset["browser_show_rating_overlay"] is True
    assert reset["browser_show_tag_overlay"] is True
    assert reset["browser_rating_overlay_opacity"] == 85
    assert reset["browser_tag_overlay_opacity"] == 100
    assert reset["browser_tag_auto_text_color"] is True
    assert reset["browser_tag_text_luminance_threshold"] == 150
    dialog.reject()


@pytest.mark.parametrize(
    ("language", "group", "rating", "tag", "rating_opacity", "tag_opacity", "reset"),
    [
        ("en", "Thumbnail overlays", "Show ratings", "Show tags", "Rating background opacity:", "Tag background opacity:", "Reset rating/tag display to defaults"),
        ("zh_CN", "缩略图上的信息", "显示评分", "显示标签", "评分背景不透明度：", "标签背景不透明度：", "将评分/标签显示恢复为默认值"),
        ("zh_TW", "縮圖上的資訊", "顯示評分", "顯示標籤", "評分背景不透明度：", "標籤背景不透明度：", "將評分／標籤顯示還原為預設值"),
    ],
)
def test_overlay_settings_labels_are_translated(
    tmp_path: Path,
    qapp,
    language: str,
    group: str,
    rating: str,
    tag: str,
    rating_opacity: str,
    tag_opacity: str,
    reset: str,
) -> None:
    config = make_config(tmp_path)
    install_ui_language(language)
    try:
        dialog = SettingsDialog(config)
        assert dialog.findChildren(type(dialog.browser_show_rating_overlay_checkbox.parent()))
        assert dialog.browser_show_rating_overlay_checkbox.text() == rating
        assert dialog.browser_show_tag_overlay_checkbox.text() == tag
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        assert rating_opacity in labels
        assert tag_opacity in labels
        assert dialog.browser_overlay_restore_button.text() == reset
        groups = [group_box.title() for group_box in dialog.findChildren(type(dialog.browser_show_rating_overlay_checkbox.parent()))]
        assert group in groups
        dialog.reject()
    finally:
        install_ui_language("ja")
