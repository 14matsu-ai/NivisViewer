import ast
from pathlib import Path
import re
from string import Formatter

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QMenu,
    QMenuBar, QStyle, QStyleOptionMenuItem, QTabWidget, QWidgetAction,
)

from app.application_controller import ApplicationController
from app.archive_backend import ArchiveBackendError, ArchiveErrorCode, _USER_MESSAGES as ARCHIVE_MESSAGES
from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_sort import BROWSER_SORT_CHOICES, BROWSER_DISPLAY_DENSITY_LABELS
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.file_conflict_dialog import FileConflictTableModel
from app.file_operation_panel import FileOperationPanel
from app.file_operation_service import FileOperationKind, FileOperationProgress
from app.file_properties_dialog import FilePropertiesDialog
from app.i18n import initialize_ui_language, install_ui_language, tr
from app.menu_icons import _GearIconEngine, _menu_icon_metrics, install_text_icon_menu_style, settings_icon
from app.pdf_backend import PdfBackendError, PdfErrorCode, _USER_MESSAGES as PDF_MESSAGES
from app.settings_dialog import SettingsDialog
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import FRAME_RATIOS, CROP_MODES, BROWSER_THUMBNAIL_DISPLAY_MODES
from app.translations_en import ENGLISH
from app.viewer_commands import COMMAND_CHOICES
from app.viewer_memory_policy import VIEWER_MEMORY_MODE_LABELS
from app.viewer_render import DOWNSCALE_ALGORITHM_LABELS, UPSCALE_ALGORITHM_LABELS
from app.viewer_window import ViewerWindow
from app.windows_filename import generate_copy_name


JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


@pytest.fixture(autouse=True)
def language_lifetime(qapp):
    install_ui_language("ja")
    yield
    install_ui_language("ja")
    qapp.clipboard().clear()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def config_at(tmp_path, language="ja"):
    config = ConfigManager(tmp_path / "language-config.json")
    config.load()
    config.apply({"ui_language": language}, save=True)
    return config


def dispose(window, qapp):
    window.close()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_catalog_covers_explicit_calls_and_preserves_format_contracts():
    root = Path(__file__).resolve().parents[1]
    formatter = Formatter()
    sources = set()
    for path in [*root.joinpath("app").glob("*.py"), root / "main.py"]:
        if path.name == "translations_en.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "tr" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                continue
            source = node.args[0].value
            if not isinstance(source, str):
                continue
            sources.add(source)
            assert source in ENGLISH, (path.name, node.lineno, source)
            if node.keywords:
                expected = [(field, spec, conversion) for _, field, spec, conversion in formatter.parse(source) if field]
                translated = [(field, spec, conversion) for _, field, spec, conversion in formatter.parse(ENGLISH[source]) if field]
                assert sorted(expected) == sorted(translated), source
    assert len(sources) > 700
    assert all(value and not JAPANESE.search(value) for value in ENGLISH.values())
    static_labels = [label for label, *_ in BROWSER_SORT_CHOICES]
    static_labels += list(BROWSER_DISPLAY_DENSITY_LABELS.values())
    static_labels += [label for _, label in FRAME_RATIOS.values()]
    static_labels += list(CROP_MODES.values()) + list(BROWSER_THUMBNAIL_DISPLAY_MODES.values())
    static_labels += [label for label, _ in COMMAND_CHOICES + VIEWER_MEMORY_MODE_LABELS]
    static_labels += list(DOWNSCALE_ALGORITHM_LABELS.values()) + list(UPSCALE_ALGORITHM_LABELS.values())
    static_labels += list(ARCHIVE_MESSAGES.values()) + list(PDF_MESSAGES.values())
    assert all(not JAPANESE.search(label) or label in ENGLISH for label in static_labels)


@pytest.mark.parametrize("value,expected", [(None, "ja"), ("en", "en"), ("ja", "ja"),
                                         ("auto", "ja"), ("fr", "ja"), (1, "ja"), ({}, "ja")])
def test_language_default_validation_and_persistence(tmp_path, value, expected):
    config = ConfigManager(tmp_path / "config.json")
    assert config.load()["ui_language"] == "ja"
    config.apply({"ui_language": value}, save=True)
    assert ConfigManager(config.path).load()["ui_language"] == expected


def test_apply_cancel_ok_and_restart_boundary(tmp_path, qapp):
    config = config_at(tmp_path)
    dialog = SettingsDialog(config)
    assert dialog.ui_language_label.text() == "表示言語 / Language"
    dialog.ui_language_combo.setCurrentIndex(dialog.ui_language_combo.findData("en"))
    dialog.reject()
    assert config.get("ui_language") == "ja"
    dialog = SettingsDialog(config)
    assert [(dialog.ui_language_combo.itemText(i), dialog.ui_language_combo.itemData(i))
            for i in range(dialog.ui_language_combo.count())] == [("日本語", "ja"), ("English", "en")]
    dialog.ui_language_combo.setCurrentIndex(1)
    dialog.apply_settings()
    assert ConfigManager(config.path).load()["ui_language"] == "en"
    assert "再起動" in dialog.ui_language_restart_note.text()
    assert dialog.windowTitle() == "設定" and tr("設定") == "設定"
    initialize_ui_language("en")
    assert tr("設定") == "設定"  # A saved draft cannot partial-switch the process.
    dialog.reject()
    assert config.get("ui_language") == "en"  # Cancel does not undo an earlier Apply.
    install_ui_language(ConfigManager(config.path).load()["ui_language"])
    reopened = SettingsDialog(config)
    assert reopened.windowTitle() == "Settings"
    assert reopened.ui_language_label.text() == "表示言語 / Language"
    assert reopened.ui_language_label.buddy() is reopened.ui_language_combo
    assert "restart" in reopened.ui_language_restart_note.text()
    reopened.ui_language_combo.setCurrentIndex(0)
    reopened.accept()
    assert ConfigManager(config.path).load()["ui_language"] == "ja"
    assert tr("設定") == "Settings"


def test_controller_initializes_saved_language_before_creating_windows(tmp_path, qapp):
    config = config_at(tmp_path, "en")
    qapp.setProperty("nivis_ui_language", None)  # Simulate next process startup.
    controller = ApplicationController(qapp, config_manager=config)
    try:
        assert qapp.property("nivis_ui_language") == "en"
        assert tr("設定") == "Settings"
        assert controller.get_browser_window() is None
    finally:
        controller.shutdown()


def test_settings_all_tabs_are_english_and_values_remain_ids(tmp_path, qapp):
    config = config_at(tmp_path)
    japanese = SettingsDialog(config)
    original_values = japanese.values()
    japanese.reject()
    install_ui_language("en")
    dialog = SettingsDialog(config)
    dialog.show()
    # Keep the same logical window at each DPI. The offscreen plugin's tiny
    # default 800-physical-pixel screen otherwise auto-shrinks QDialog on show.
    dialog.resize(620, 680)
    qapp.processEvents()
    try:
        assert dialog.values() == original_values
        assert all(button.text() for button in dialog.button_box.buttons())
        strings = [dialog.windowTitle()]
        for widget in dialog.findChildren(QLabel) + dialog.findChildren(QAbstractButton):
            if widget is dialog.ui_language_label:
                assert widget.text() == "表示言語 / Language"
                continue  # The language caption is deliberately bilingual.
            strings += [widget.text(), widget.toolTip()]
        strings += [group.title() for group in dialog.findChildren(QGroupBox)]
        for combo in dialog.findChildren(QComboBox):
            if combo is not dialog.ui_language_combo:
                strings += [combo.itemText(i) for i in range(combo.count())]
        for tabs in dialog.findChildren(QTabWidget):
            strings += [tabs.tabText(i) for i in range(tabs.count())]
        assert not [text for text in strings if JAPANESE.search(text)]
        assert dialog.browser_sort_key_combo.itemData(0) == "item_type:ascending"
        assert dialog.browser_sort_key_combo.itemText(0) == "Type (Ascending)"
        assert dialog.browser_wheel_scroll_mode_combo.findData("custom") >= 0
        assert dialog.gesture_down_combo.findData("next_page") >= 0
        assert dialog.browser_folder_snapshot_cache_help_button.accessibleName() == (
            "Folder listing memory cache help"
        )
        assert dialog.thumbnail_webp_quality_help_button.accessibleName() == (
            "Saved thumbnail quality help"
        )
        assert dialog.thumbnail_cache_max_edge_help_button.accessibleName() == (
            "Maximum generated edge help"
        )
        assert dialog.thumbnail_preserve_alpha_help_button.accessibleName() == (
            "Saved thumbnail transparency help"
        )
        clipped = []
        for index in range(dialog.tabs.count()):
            dialog.tabs.setCurrentIndex(index)
            qapp.processEvents()
            for button in dialog.findChildren(QAbstractButton):
                if button.isVisible() and button.text():
                    if button.width() < button.minimumSizeHint().width():
                        clipped.append((button.text(), button.width(), button.minimumSizeHint().width()))
        assert not clipped, "\n".join(map(str, clipped))
    finally:
        dialog.reject()


@pytest.mark.parametrize("language", ["ja", "en"])
@pytest.mark.parametrize("operation", ["copy", "search"])
def test_browser_late_filename_menu_and_status_preserve_user_data(tmp_path, qapp, monkeypatch, language, operation):
    install_ui_language(language)
    config = config_at(tmp_path, language)
    provider = BrowserThumbnailProvider(loader=lambda *_: None, disk_cache_enabled=False)
    browser = BrowserWindow(config_manager=config, thumbnail_provider=provider, restore_initial_location=False)
    browser.image_detail_probe.request = lambda *_: None
    name = "設定 Folder.Name"
    item = BrowserItem(name, tmp_path / name, BrowserItemKind.FOLDER, None, page_count=42, file_size=1234)
    browser.resize(900, 600)
    browser.show()
    browser.item_model.set_items([item])
    browser.list_view.doItemsLayout()
    browser.list_view.setCurrentIndex(browser.item_model.index(0, 0))
    browser._update_status(force=True)
    assert browser.browser_item_count_label.text() == ("1 items" if language == "en" else "1 個の項目")
    assert browser.file_detail_label.text() == ("42 pages" if language == "en" else "42 ページ")
    assert browser.browser_selected_path_edit.text() == str(item.path)
    assert browser.settings_action.text() == ("Settings" if language == "en" else "設定")
    assert not browser.settings_action.icon().isNull()
    errors = []
    qapp.clipboard().setText("unchanged")
    def menu_factory(parent):
        menu = QMenu(parent)
        def interact():
            try:
                editor = menu.findChild(QLineEdit, "browser_context_filename")
                assert editor.text() == name and editor.isReadOnly()
                editor.setFocus()
                editor.setSelection(0, 2)
                assert editor.selectedText() == "設定"
                source = "選択文字をコピー" if operation == "copy" else "選択文字で検索"
                choices = {a.text(): a for a in menu.actions() if not isinstance(a, QWidgetAction)}
                assert tr("コピー") in choices and tr("名前をコピー") in choices
                action = choices[tr(source)]
                QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
            except Exception as error:
                errors.append(error)
            finally:
                menu.close()
        QTimer.singleShot(0, interact)
        return menu
    monkeypatch.setattr("app.browser_window.QMenu", menu_factory)
    try:
        browser._show_context_menu(browser.list_view.visualRect(browser.item_model.index(0, 0)).center())
        assert not errors, errors
        if operation == "copy":
            assert qapp.clipboard().text() == "設定"
            assert qapp.clipboard().mimeData().formats() == ["text/plain"]
        else:
            assert browser.active_search_query == "設定"
            assert browser.items == (item,)
            assert config.get("browser_search_history")[0] == "設定"
            assert qapp.clipboard().text() == "unchanged"
    finally:
        dispose(browser, qapp)


def test_viewer_menus_and_magnifier_values_in_english(tmp_path, qapp, monkeypatch):
    install_ui_language("en")
    window = ViewerWindow(config_manager=config_at(tmp_path, "en"))
    try:
        texts = [action.text() for action in window.menuBar().actions()]
        assert {"File", "View", "Settings", "Go", "Help"} <= set(texts)
        assert [a.text() for a in window.normal_resampling_menu.actions()] == ["Downscaling", "Upscaling"]
        menu = window.findChild(QMenu, "viewer_settings_menu")
        assert menu is not None and not menu.icon().isNull()
        assert not [a.text() for m in window.findChildren(QMenu) for a in m.actions() if JAPANESE.search(a.text())]
        def choose(*args):
            assert args[3] == ("1.5×", "2×", "3×", "4×")
            return args[3][2], True
        monkeypatch.setattr("app.viewer_window.QInputDialog.getItem", choose)
        window.set_magnifier_options_dialog()
        assert window.magnifier_zoom == 3.0
    finally:
        dispose(window, qapp)


@pytest.mark.parametrize("language", ["ja", "en"])
@pytest.mark.parametrize("dark", [False, True])
@pytest.mark.parametrize("compact", [False, True])
def test_settings_gear_keeps_menu_text_and_palette_contrast(qapp, language, dark, compact):
    install_ui_language(language)
    previous_palette = QPalette(qapp.palette())
    palette = QPalette(previous_palette)
    foreground = QColor("#eeeeee" if dark else "#151515")
    palette.setColor(QPalette.ColorRole.WindowText, foreground)
    qapp.setPalette(palette)
    bar = QMenuBar()
    install_text_icon_menu_style(bar, compact=compact)
    action = bar.addAction(tr("設定"))
    action.setIcon(settings_icon())
    bar.show()
    qapp.processEvents()
    try:
        assert action.text() == ("Settings" if language == "en" else "設定")
        edge, gap = _menu_icon_metrics(bar.fontMetrics())
        assert edge == round(bar.fontMetrics().height() * 0.8)
        assert edge < 16 and edge + gap < 20  # Smaller than the previous menu icon/slot.
        assert bar.actionGeometry(action).width() >= bar.fontMetrics().horizontalAdvance(action.text()) + 12 + edge + gap
        assert not bar.grab().isNull()
        for scale in (1.0, 1.25, 1.5, 2.0):
            image = action.icon().pixmap(QSize(edge, edge), scale).toImage()
            colored = [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())
                       if image.pixelColor(x, y).alpha() > 240]
            assert colored and any(color.rgb() == foreground.rgb() for color in colored)
            def ink_bounds(rendered):
                points = [(x, y) for y in range(rendered.height()) for x in range(rendered.width())
                          if rendered.pixelColor(x, y).alpha() > 32]
                return (min(x for x, y in points), min(y for x, y in points),
                        max(x for x, y in points), max(y for x, y in points))
            left, top, right, bottom = ink_bounds(image)
            old = ink_bounds(action.icon().pixmap(QSize(16, 16), scale).toImage())
            assert right - left < old[2] - old[0]
            assert bottom - top < old[3] - old[1]
            assert abs((left + right) / 2 - (image.width() - 1) / 2) <= 0.5
            assert abs((top + bottom) / 2 - (image.height() - 1) / 2) <= 0.5
    finally:
        bar.close()
        qapp.setPalette(previous_palette)


def test_menu_icon_geometry_uses_shared_metrics_without_changing_plain_items(qapp, monkeypatch):
    bar = QMenuBar()
    install_text_icon_menu_style(bar, compact=True)
    option = QStyleOptionMenuItem()
    option.initFrom(bar)
    option.text = "Settings"
    option.rect = QRect(0, 0, 160, 29)
    style = bar.style()
    plain = style.sizeFromContents(QStyle.ContentsType.CT_MenuBarItem, option, QSize(), bar)
    assert plain == QSize(option.fontMetrics.horizontalAdvance("Settings") + 12, option.fontMetrics.height() + 8)
    option.icon = settings_icon()
    sized = style.sizeFromContents(QStyle.ContentsType.CT_MenuBarItem, option, QSize(), bar)
    edge, gap = _menu_icon_metrics(option.fontMetrics)
    assert sized.height() == plain.height()
    assert sized.width() - plain.width() == edge + gap
    painted = []
    monkeypatch.setattr(_GearIconEngine, "paint", lambda self, painter, rect, mode, state: painted.append(QRect(rect)))
    surface = QPixmap(option.rect.size())
    painter = QPainter(surface)
    try:
        style.drawControl(QStyle.ControlElement.CE_MenuBarItem, option, painter, bar)
    finally:
        painter.end()
        bar.close()
    assert len(painted) == 1
    assert painted[0].size() == QSize(edge, edge)
    assert abs(painted[0].center().y() - option.rect.center().y()) <= 1


def test_safe_fallback_errors_and_generated_names_are_not_translated():
    install_ui_language("en")
    assert tr("設定") == "Settings"
    assert tr("missing catalog source") == "missing catalog source"
    assert "not found" in str(ArchiveBackendError(ArchiveErrorCode.ARCHIVE_NOT_FOUND))
    assert "not found" in str(PdfBackendError(PdfErrorCode.FILE_NOT_FOUND))
    assert generate_copy_name("設定.zip", []) == "設定 - コピー.zip"
    install_ui_language("invalid")
    assert tr("設定") == "設定"


@pytest.mark.parametrize("language", ["ja", "en"])
def test_late_dialogs_and_progress_keep_exact_user_data(tmp_path, qapp, language):
    install_ui_language(language)
    path = tmp_path / "設定 {name}.zip"
    path.write_bytes(b"temporary fixture")
    dialog = FilePropertiesDialog(path)
    panel = FileOperationPanel()
    try:
        assert dialog.windowTitle() == ("Properties" if language == "en" else "プロパティ")
        assert dialog.name_edit.text() == path.name
        assert dialog.location_label.text() == str(path.parent)
        assert all(button.text() for button in dialog.button_box.buttons())
        model = FileConflictTableModel(())
        assert [model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())] == [
            tr(source) for source in model.HEADERS
        ]
        total = 2 ** 45
        progress = FileOperationProgress(
            request_id=1, operation=FileOperationKind.COPY, completed=1, total=2,
            source_path=str(path), bytes_completed=total // 2, bytes_total=total,
            eta_seconds=12, current_file_bytes_completed=total // 2,
            current_file_bytes_total=total,
        )
        panel.show_progress(progress)
        assert panel.detail_label.text() == str(path) + tr('  残り約{p0}秒', p0=12)
        assert panel.byte_progress.value() * 2 == panel.byte_progress.maximum()
        assert abs(panel.current_file_progress.value() / panel.current_file_progress.maximum() - 0.5) < 1e-8
        assert progress.bytes_total == total and path.read_bytes() == b"temporary fixture"
    finally:
        dialog.reject()
        panel.close()
