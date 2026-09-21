from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtTest import QTest

from app.config_manager import ConfigManager
from app.i18n import install_ui_language, tr
from app.settings_dialog import SettingsDialog
from app.shortcut_catalog import canonical_key, normalize_shortcut_bindings
from tests.test_application_controller import (
    close_controller,
    finish_viewer_open,
    make_controller,
    write_image,
)


def make_config(tmp_path: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return config


def test_partial_legacy_migration_preserves_close_and_browser_filter_bindings(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"viewer_close_shortcut": "Ctrl+K", '
        '"shortcut_bindings": {"browser": {"browser_clear_filters": ["T"]}}}',
        encoding="utf-8",
    )

    restored = ConfigManager(config_path).load()
    assert restored["shortcut_bindings"]["viewer"]["viewer_close"] == ["Ctrl+K"]
    assert restored["shortcut_bindings"]["browser"]["browser_cancel"] == ["Esc"]
    assert restored["shortcut_bindings"]["browser"]["browser_clear_filters"] == ["T"]

    normalized = normalize_shortcut_bindings(
        {"viewer": {"viewer_close": []}, "browser": {"browser_cancel": []}}
    )
    assert normalized["viewer"]["viewer_close"] == []
    assert normalized["browser"]["browser_cancel"] == []
    assert normalized["viewer"]["viewer_zoom_in"] == ["+", "Shift++", "="]
    imported_invalid = normalize_shortcut_bindings(
        {"viewer": {"viewer_close": ["Ctrl+K, Ctrl+C"]}}
    )
    assert imported_invalid["viewer"]["viewer_close"] == []

    config = ConfigManager(tmp_path / "apply.json")
    config.load()
    config.apply(
        {
            "viewer_close_shortcut": "Ctrl+K",
            "shortcut_bindings": {"viewer": {"viewer_zoom_in": ["X"]}},
        }
    )
    assert config.get("shortcut_bindings")["viewer"]["viewer_close"] == ["Ctrl+K"]


def test_legacy_viewer_quit_binding_migrates_to_the_single_close_action(tmp_path: Path) -> None:
    restored = normalize_shortcut_bindings(
        {"viewer": {"viewer_quit": ["Ctrl+Alt+Q"]}}
    )
    assert restored["viewer"]["viewer_close"] == ["Ctrl+Alt+Q"]

    preserved = normalize_shortcut_bindings(
        {"viewer": {"viewer_close": ["Ctrl+W"], "viewer_quit": ["Ctrl+Q"]}}
    )
    assert preserved["viewer"]["viewer_close"] == ["Ctrl+W", "Ctrl+Q"]
    close_disabled_but_quit_assigned = normalize_shortcut_bindings(
        {"viewer": {"viewer_close": [], "viewer_quit": ["Ctrl+Q"]}}
    )
    assert close_disabled_but_quit_assigned["viewer"]["viewer_close"] == ["Ctrl+Q"]
    both_disabled = normalize_shortcut_bindings(
        {"viewer": {"viewer_close": [], "viewer_quit": []}}
    )
    assert both_disabled["viewer"]["viewer_close"] == []

    path = tmp_path / "legacy.json"
    path.write_text(
        '{"shortcut_bindings": {"viewer": {"viewer_close": [], "viewer_quit": ["Ctrl+Q"]}}}',
        encoding="utf-8",
    )
    migrated = ConfigManager(path)
    migrated.load()
    assert migrated.get("shortcut_bindings")["viewer"]["viewer_close"] == ["Ctrl+Q"]
    migrated.save()
    reloaded = ConfigManager(path)
    reloaded.load()
    assert reloaded.get("shortcut_bindings")["viewer"]["viewer_close"] == ["Ctrl+Q"]


def test_shortcut_aliases_beyond_three_fields_survive_save_reload(tmp_path: Path, qapp) -> None:
    path = tmp_path / "config.json"
    config = ConfigManager(path)
    config.load()
    aliases = ["Ctrl+A", "Ctrl+B", "Ctrl+C", "Ctrl+D"]
    config.apply(
        {"shortcut_bindings": {"viewer": {"viewer_close": aliases}}},
        save=True,
    )

    restored = ConfigManager(path)
    restored.load()
    assert restored.get("shortcut_bindings")["viewer"]["viewer_close"] == aliases

    dialog = SettingsDialog(restored)
    try:
        assert dialog.values()["shortcut_bindings"]["viewer"]["viewer_close"] == aliases
        assert not dialog.shortcut_status.isHidden()
    finally:
        dialog.reject()


def test_shortcut_scope_reset_keeps_other_scope_and_draft(tmp_path: Path, qapp) -> None:
    config = make_config(tmp_path)
    saved = deepcopy(config.data)
    dialog = SettingsDialog(config)
    close_editor = dialog.shortcut_editors[("viewer", "viewer_close")][0]
    browser_editor = dialog.shortcut_editors[("browser", "browser_back")][0]
    close_editor.setKeySequence(QKeySequence("Ctrl+K"))
    browser_editor.setKeySequence(QKeySequence("T"))

    dialog._reset_tab_draft("shortcuts_browser")

    assert close_editor.keySequence().toString() == "Ctrl+K"
    assert browser_editor.keySequence().toString() == "Alt+Left"
    assert config.get("shortcut_bindings")["viewer"]["viewer_close"] == ["Ctrl+W", "Ctrl+Q"]

    dialog.viewer_slideshow_chord_checkbox.setChecked(False)
    dialog._reset_tab_draft("shortcuts_browser")
    assert not dialog.viewer_slideshow_chord_checkbox.isChecked()
    dialog._reset_tab_draft("shortcuts_viewer")
    assert dialog.viewer_slideshow_chord_checkbox.isChecked()
    assert config.data == saved
    dialog.reject()
    assert config.data == saved


def test_browser_cancel_filter_option_disables_binding_without_losing_draft(
    tmp_path: Path, qapp,
) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)
    clear_editor = dialog.shortcut_editors[("browser", "browser_clear_filters")][0]
    cancel_editor = dialog.shortcut_editors[("browser", "browser_cancel")][0]
    cancel_row = dialog.shortcut_rows[("browser", "browser_cancel")]
    clear_label = dialog.shortcut_labels[("browser", "browser_clear_filters")]
    try:
        assert cancel_row.isAncestorOf(dialog.browser_cancel_filter_option_row)
        assert dialog.browser_cancel_filter_option_row.isAncestorOf(
            dialog.browser_cancel_clears_filters_checkbox
        )
        assert dialog.browser_cancel_clears_filters_checkbox.isChecked()
        assert not clear_editor.isEnabled()
        assert not clear_label.isEnabled()
        assert not dialog.shortcut_reset_buttons[("browser", "browser_clear_filters")].isEnabled()
        clear_editor.setKeySequence(QKeySequence("Esc"))
        assert dialog._shortcut_conflicts() == []

        dialog.browser_cancel_clears_filters_checkbox.setChecked(False)
        assert clear_editor.isEnabled()
        assert clear_label.isEnabled()
        assert dialog.shortcut_reset_buttons[("browser", "browser_clear_filters")].isEnabled()
        assert any(
            item[1:] == ("browser_cancel", "browser_clear_filters", "Esc")
            or item[1:] == ("browser_clear_filters", "browser_cancel", "Esc")
            for item in dialog._shortcut_conflicts()
        )
        dialog.browser_cancel_clears_filters_checkbox.setChecked(True)
        assert not clear_editor.isEnabled()
        assert clear_editor.keySequence().toString() == "Esc"
        assert dialog.values()["browser_cancel_clears_filters"] is True

        dialog.browser_cancel_clears_filters_checkbox.setChecked(False)
        assert clear_editor.isEnabled()
        assert dialog.values()["shortcut_bindings"]["browser"]["browser_clear_filters"] == ["Esc"]
        cancel_editor.clear()
        changed = dialog.apply_settings()
        assert changed["browser_cancel_clears_filters"] is False
        assert config.get("browser_cancel_clears_filters") is False
        restored = ConfigManager(config.path)
        restored.load()
        assert restored.get("browser_cancel_clears_filters") is False
        assert restored.get("shortcut_bindings")["browser"]["browser_clear_filters"] == ["Esc"]
    finally:
        dialog.reject()


def test_browser_scope_reset_restores_cancel_filter_option_but_viewer_reset_does_not(
    tmp_path: Path, qapp,
) -> None:
    dialog = SettingsDialog(make_config(tmp_path))
    try:
        dialog.browser_cancel_clears_filters_checkbox.setChecked(False)
        dialog._reset_tab_draft("shortcuts_viewer")
        assert not dialog.browser_cancel_clears_filters_checkbox.isChecked()
        dialog._reset_tab_draft("shortcuts_browser")
        assert dialog.browser_cancel_clears_filters_checkbox.isChecked()
    finally:
        dialog.reject()


def test_shortcut_editor_captures_one_combination_per_box(tmp_path: Path, qapp) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)
    editors = dialog.shortcut_editors[("viewer", "viewer_close")]

    editors[0].setKeySequence(QKeySequence("Ctrl+K, Ctrl+C"))
    assert editors[0].keySequence().toString() == "Ctrl+W"
    assert not dialog._sync_shortcut_status()
    editors[0].setFocus()
    QTest.keyClick(editors[0], Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(editors[0], Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert editors[0].keySequence().count() == 1
    assert "," not in editors[0].keySequence().toString()

    editors[1].setFocus()
    QTest.keyClick(
        editors[1],
        Qt.Key.Key_W,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    editors[2].setFocus()
    QTest.keyClick(editors[2], Qt.Key.Key_R)
    assert editors[1].keySequence().count() == 1
    assert editors[1].keySequence().toString() == "Ctrl+Shift+W"
    assert editors[2].keySequence().count() == 1
    assert editors[2].keySequence().toString() == "R"
    assert editors[1].keySequence() != editors[2].keySequence()
    dialog.reject()


def test_shortcut_settings_tab_order_and_independent_scope_scrolls(
    tmp_path: Path, qapp,
) -> None:
    install_ui_language("ja")
    dialog = SettingsDialog(make_config(tmp_path))
    dialog.resize(620, 680)
    dialog.show()
    dialog.tabs.setCurrentIndex(6)
    dialog.shortcut_tabs.setCurrentIndex(1)
    qapp.processEvents()
    try:
        assert [
            dialog.tabs.tabText(index)
            for index in range(dialog.tabs.count())
        ] == ["Viewer", "Browser", "ファイル", "書庫", "Mouse", "Windows連携", "ショートカット", "一般"]
        assert dialog.tabs.widget(6).objectName() == "shortcuts_tab"
        assert set(dialog.shortcut_scope_scrolls) == {"browser", "viewer"}
        assert all(scroll.widgetResizable() for scroll in dialog.shortcut_scope_scrolls.values())
        assert dialog.shortcut_scope_scrolls["browser"] is not dialog.shortcut_scope_scrolls["viewer"]
        for scroll in dialog.shortcut_scope_scrolls.values():
            assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            assert scroll.widget().layout().itemAt(
                scroll.widget().layout().count() - 2
            ).widget().objectName().startswith("reset_shortcuts_")
            for editors in dialog.shortcut_editors.values():
                for editor in editors:
                    assert editor.width() >= editor.minimumWidth()
    finally:
        dialog.reject()


def test_shortcut_search_uses_visible_labels_and_key_tokens(
    tmp_path: Path, qapp,
):
    install_ui_language("en")
    config = make_config(tmp_path)
    config.apply({
        "shortcut_bindings": {
            "browser": {"browser_back": ["T"]},
            "viewer": {"viewer_rotate_left": ["Ctrl+Alt+Shift+W"]},
        },
    })
    dialog = SettingsDialog(config)

    def visible(action_id: str) -> bool:
        return not dialog.shortcut_rows[(
            "browser" if action_id.startswith("browser_") else "viewer",
            action_id,
        )].isHidden()

    try:
        dialog.shortcut_search_edit.setText("browser_back")
        assert not visible("browser_back")
        dialog.shortcut_search_edit.setText("Back")
        assert visible("browser_back")
        dialog.shortcut_search_edit.setText("F")
        assert visible("viewer_toggle_fullscreen")
        assert not visible("viewer_toggle_reading_direction")
        dialog.shortcut_search_edit.setText("Ctrl")
        assert visible("viewer_page_info")
        assert visible("viewer_rotate_left")
        dialog.shortcut_search_edit.setText("Ctrl+Alt+Shift+W")
        assert visible("viewer_rotate_left")
        assert not visible("viewer_page_info")
        dialog.shortcut_search_edit.setText("PageDown")
        assert visible("viewer_next_page_or_scroll")
        dialog.shortcut_search_edit.setText("")
        assert all(not row.isHidden() for row in dialog.shortcut_rows.values())
        dialog.shortcut_search_edit.setText("no such shortcut")
        assert all(row.isHidden() for row in dialog.shortcut_rows.values())
    finally:
        dialog.reject()
        install_ui_language("ja")


@pytest.mark.parametrize("language", ["ja", "en", "zh-Hans", "zh-Hant"])
def test_captured_shortcut_key_search_ignores_action_labels(
    tmp_path: Path, qapp, language: str,
) -> None:
    install_ui_language(language)
    dialog = SettingsDialog(make_config(tmp_path))
    search = dialog.shortcut_search_edit
    try:
        for key, query in ((Qt.Key.Key_F, "F"), (Qt.Key.Key_S, "S")):
            QTest.keyClick(search, key)
            assert search.text() == query
            for scope_action, row in dialog.shortcut_rows.items():
                editors = dialog.shortcut_editors[scope_action]
                keys = [canonical_key(editor.keySequence()) for editor in editors]
                keys.extend(dialog._shortcut_extra_bindings.get(scope_action, ()))
                assert not row.isHidden() == dialog._shortcut_key_query_matches(query, keys), scope_action
        assert dialog.shortcut_rows[("viewer", "viewer_first_page")].isHidden()
    finally:
        dialog.reject()
        install_ui_language("ja")


def test_move_conflict_clears_preserved_extra_only_in_same_scope_and_saves(
    tmp_path: Path, qapp,
) -> None:
    config = make_config(tmp_path)
    config.apply({"shortcut_bindings": {
        "viewer": {"viewer_close": ["Ctrl+W", "Ctrl+Q", "F11", "F12"]},
        "browser": {"browser_back": ["Alt+Left", "F12"]},
    }})
    dialog = SettingsDialog(config)
    try:
        dialog.shortcut_editors[("viewer", "viewer_page_info")][0].setKeySequence(
            QKeySequence("F12")
        )
        assert dialog._shortcut_conflicts()
        dialog.shortcut_search_edit.setText("F12")
        assert not dialog.shortcut_rows[("viewer", "viewer_close")].isHidden()
        dialog._move_shortcut_conflicts("viewer", "viewer_page_info")
        assert not dialog._shortcut_conflicts()
        assert dialog.shortcut_rows[("viewer", "viewer_close")].isHidden()
        assert not dialog.shortcut_rows[("viewer", "viewer_page_info")].isHidden()
        values = dialog.values()["shortcut_bindings"]
        assert values["viewer"]["viewer_close"] == ["Ctrl+W", "Ctrl+Q", "F11"]
        assert values["viewer"]["viewer_page_info"] == ["F12"]
        assert values["browser"]["browser_back"] == ["Alt+Left", "F12"]
        assert "F12" not in dialog.shortcut_editors[("viewer", "viewer_close")][0].toolTip()
        dialog.apply_settings()
        reloaded = ConfigManager(config.path)
        reloaded.load()
        saved = reloaded.get("shortcut_bindings")
        assert saved["viewer"]["viewer_close"] == ["Ctrl+W", "Ctrl+Q", "F11"]
        assert saved["viewer"]["viewer_page_info"] == ["F12"]
        assert saved["browser"]["browser_back"] == ["Alt+Left", "F12"]
    finally:
        dialog.reject()


def test_file_reset_does_not_reset_browser_cache_draft(tmp_path: Path, qapp) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)
    dialog.disk_cache_checkbox.setChecked(False)
    dialog.text_preview_checkbox.setChecked(False)
    dialog.ffmpeg_path_edit.setText("C:/draft/ffmpeg.exe")
    dialog.delete_skip_confirmation_checkbox.setChecked(True)

    dialog._reset_tab_draft("file")

    assert not dialog.disk_cache_checkbox.isChecked()
    assert not dialog.text_preview_checkbox.isChecked()
    assert dialog.ffmpeg_path_edit.text() == "C:/draft/ffmpeg.exe"
    assert not dialog.delete_skip_confirmation_checkbox.isChecked()

    dialog._reset_tab_draft("browser")
    assert dialog.disk_cache_checkbox.isChecked()
    assert dialog.text_preview_checkbox.isChecked()
    assert dialog.ffmpeg_path_edit.text() == ""
    assert config.get("thumbnail_disk_cache_enabled") is True


def test_browser_cancel_label_is_short_with_priority_help(tmp_path: Path, qapp) -> None:
    dialog = SettingsDialog(make_config(tmp_path))
    label = dialog.findChild(
        type(dialog.shortcut_status),
        "shortcut_label_browser_browser_cancel",
    )
    assert label is not None
    assert label.text() == "操作をキャンセル"
    assert "選択操作" in label.toolTip()
    assert "絞り込み" in label.toolTip()
    dialog.reject()


def test_shortcut_search_captures_single_chords_and_clear_button(
    tmp_path: Path, qapp,
) -> None:
    dialog = SettingsDialog(make_config(tmp_path))
    dialog.show()
    qapp.processEvents()
    search = dialog.shortcut_search_edit
    try:
        search.setFocus()
        QTest.keyPress(search, Qt.Key.Key_Control)
        QTest.keyRelease(search, Qt.Key.Key_Control)
        assert search.text() == "Ctrl"
        assert not dialog.shortcut_rows[("viewer", "viewer_page_info")].isHidden()
        QTest.keyClick(search, Qt.Key.Key_Escape)
        assert search.text() == "Esc"
        assert dialog.isVisible()
        assert not dialog.shortcut_rows[("browser", "browser_cancel")].isHidden()
        assert not dialog.shortcut_rows[("viewer", "viewer_cancel_temporary")].isHidden()

        QTest.keyClick(search, Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier)
        assert search.text() == "Ctrl+Left"
        assert not dialog.shortcut_rows[("viewer", "viewer_rotate_left")].isHidden()
        assert dialog.shortcut_rows[("viewer", "viewer_rotate_right")].isHidden()

        QTest.keyClick(search, Qt.Key.Key_R, Qt.KeyboardModifier.ShiftModifier)
        assert search.text() == "Shift+R"
        assert not dialog.shortcut_rows[("viewer", "viewer_toggle_reading_direction")].isHidden()
        assert dialog.shortcut_rows[("viewer", "viewer_rotate_left")].isHidden()

        dialog.shortcut_search_clear_button.click()
        assert search.text() == ""
        assert all(not row.isHidden() for row in dialog.shortcut_rows.values())
        assert "1つのキー" in search.placeholderText()
        QTest.keyClick(search, Qt.Key.Key_Return)
        assert search.text() == "Return"
        assert dialog.isVisible()
    finally:
        dialog.reject()


def test_shortcut_search_modifier_subsets_and_staggered_release(
    tmp_path: Path, qapp,
) -> None:
    install_ui_language("en")
    config = make_config(tmp_path)
    config.apply(
        {
            "shortcut_bindings": {
                "viewer": {
                    "viewer_rotate_left": ["Ctrl+Shift+R"],
                    "viewer_rotate_right": ["Ctrl+Alt+R"],
                },
            },
        }
    )
    dialog = SettingsDialog(config)
    dialog.show()
    dialog.tabs.setCurrentIndex(6)
    dialog.shortcut_tabs.setCurrentIndex(1)
    qapp.processEvents()
    search = dialog.shortcut_search_edit

    def visible(action_id: str) -> bool:
        return not dialog.shortcut_rows[("viewer", action_id)].isHidden()

    try:
        search.setFocus()
        QTest.keyPress(search, Qt.Key.Key_Control)
        QTest.keyRelease(search, Qt.Key.Key_Control)
        assert search.text() == "Ctrl"
        assert visible("viewer_rotate_left")
        assert visible("viewer_rotate_right")

        QTest.keyPress(search, Qt.Key.Key_Control)
        QTest.keyPress(search, Qt.Key.Key_Shift)
        QTest.keyRelease(search, Qt.Key.Key_Shift)
        QTest.keyRelease(search, Qt.Key.Key_Control)
        assert search.text() == "Ctrl+Shift"
        assert visible("viewer_rotate_left")
        assert not visible("viewer_rotate_right")

        QTest.keyPress(search, Qt.Key.Key_Control)
        QTest.keyPress(search, Qt.Key.Key_Shift)
        QTest.keyPress(search, Qt.Key.Key_R)
        QTest.keyRelease(search, Qt.Key.Key_R)
        QTest.keyRelease(search, Qt.Key.Key_Shift)
        QTest.keyRelease(search, Qt.Key.Key_Control)
        assert search.text() == "Ctrl+Shift+R"
        assert visible("viewer_rotate_left")
        assert not visible("viewer_rotate_right")

        QTest.keyPress(search, Qt.Key.Key_Alt)
        QTest.keyRelease(search, Qt.Key.Key_Alt)
        assert search.text() == "Alt"
        assert not visible("viewer_rotate_left")
        assert visible("viewer_rotate_right")
    finally:
        dialog.reject()
        install_ui_language("ja")


def test_shortcut_assignment_rejects_modifier_only_and_restores_status(
    tmp_path: Path, qapp,
) -> None:
    install_ui_language("ja")
    dialog = SettingsDialog(make_config(tmp_path))
    dialog.show()
    dialog.tabs.setCurrentIndex(6)
    dialog.shortcut_tabs.setCurrentIndex(1)
    qapp.processEvents()
    warning = "Ctrl・Shift・Altだけでは登録できません。ほかのキーと組み合わせてください"
    editor = dialog.shortcut_editors[("viewer", "viewer_close")][0]
    old_value = editor.keySequence().toString()
    try:
        editor.setFocus()
        QTest.keyPress(editor, Qt.Key.Key_Control)
        QTest.keyRelease(editor, Qt.Key.Key_Control)
        assert editor.keySequence().toString() == old_value
        assert dialog.shortcut_status.text() == warning
        assert dialog._sync_shortcut_status()
    finally:
        dialog.reject()
        install_ui_language("ja")


def test_shortcut_assignment_modifier_warning_lifecycle_and_valid_chord(
    tmp_path: Path, qapp,
) -> None:
    install_ui_language("ja")
    dialog = SettingsDialog(make_config(tmp_path))
    dialog.show()
    dialog.tabs.setCurrentIndex(6)
    dialog.shortcut_tabs.setCurrentIndex(1)
    qapp.processEvents()
    warning = "Ctrl・Shift・Altだけでは登録できません。ほかのキーと組み合わせてください"
    editor = dialog.shortcut_editors[("viewer", "viewer_page_info")][0]
    other = dialog.shortcut_editors[("viewer", "viewer_page_info")][1]
    old_value = editor.keySequence().toString()
    try:
        editor.setFocus()
        QTest.keyPress(editor, Qt.Key.Key_Control)
        QTest.keyPress(editor, Qt.Key.Key_Shift)
        QTest.keyRelease(editor, Qt.Key.Key_Shift)
        QTest.keyRelease(editor, Qt.Key.Key_Control)
        assert editor.keySequence().toString() == old_value
        assert dialog.shortcut_status.text() == warning

        other.setFocus()
        qapp.processEvents()
        assert dialog.shortcut_status.isHidden()

        editor.setFocus()
        QTest.keyPress(editor, Qt.Key.Key_Control)
        QTest.keyPress(editor, Qt.Key.Key_Shift)
        QTest.keyPress(editor, Qt.Key.Key_R)
        QTest.keyRelease(editor, Qt.Key.Key_R)
        QTest.keyRelease(editor, Qt.Key.Key_Shift)
        QTest.keyRelease(editor, Qt.Key.Key_Control)
        assert editor.keySequence().toString() == "Ctrl+Shift+R"
        assert warning not in dialog.shortcut_status.text()
    finally:
        dialog.reject()
        install_ui_language("ja")


def test_shortcut_assignment_warning_restores_conflict_and_english_translation(
    tmp_path: Path, qapp,
) -> None:
    install_ui_language("en")
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)
    dialog.show()
    dialog.tabs.setCurrentIndex(6)
    dialog.shortcut_tabs.setCurrentIndex(1)
    qapp.processEvents()
    editor = dialog.shortcut_editors[("viewer", "viewer_page_info")][0]
    other = dialog.shortcut_editors[("viewer", "viewer_page_info")][1]
    try:
        editor.setFocus()
        editor.setKeySequence(QKeySequence("Right"))
        dialog._sync_shortcut_status("viewer", "viewer_page_info")
        conflict_text = dialog.shortcut_status.text()
        assert "assigned" in conflict_text

        QTest.keyPress(editor, Qt.Key.Key_Control)
        QTest.keyRelease(editor, Qt.Key.Key_Control)
        assert "alone cannot be assigned" in dialog.shortcut_status.text()
        assert not dialog._sync_shortcut_status()

        other.setFocus()
        qapp.processEvents()
        assert dialog.shortcut_status.text() == conflict_text
        assert not dialog._sync_shortcut_status()
        assert "#ff262a" in dialog.shortcut_status.styleSheet()
    finally:
        dialog.reject()
        install_ui_language("ja")


@pytest.mark.parametrize("language", ["ja", "en"])
@pytest.mark.parametrize("point_size", [9, 14])
def test_conflict_move_button_stays_visible_without_row_geometry_jump(
    tmp_path: Path, qapp, language: str, point_size: int,
) -> None:
    install_ui_language(language)
    dialog = SettingsDialog(make_config(tmp_path))
    font = QFont(dialog.font())
    font.setPointSize(point_size)
    dialog.setFont(font)
    dialog.resize(620, 680)
    dialog.show()
    dialog.tabs.setCurrentIndex(6)
    dialog.shortcut_tabs.setCurrentIndex(1)
    qapp.processEvents()
    try:
        for button in dialog.shortcut_move_buttons.values():
            assert not button.isHidden()
            assert not button.isEnabled()
        reset_xs = {
            dialog.shortcut_reset_buttons[key].mapTo(
                dialog.shortcut_scope_scrolls["viewer"].widget(),
                dialog.shortcut_reset_buttons[key].rect().topLeft(),
            ).x()
            for key in dialog.shortcut_reset_buttons
            if key[0] == "viewer"
        }
        assert len(reset_xs) == 1
        page = dialog.shortcut_scope_scrolls["viewer"].widget()
        viewer_rows = sorted(
            (
                dialog.shortcut_rows[key].geometry().top(),
                key,
            )
            for key in dialog.shortcut_rows
            if key[0] == "viewer"
        )
        for key, editors in dialog.shortcut_editors.items():
            if key[0] != "viewer":
                continue
            move_button = dialog.shortcut_move_buttons[key]
            reset_button = dialog.shortcut_reset_buttons[key]
            editor_rects = [
                editor.mapTo(page, editor.rect().topLeft())
                for editor in editors
            ]
            for left, right in zip(editor_rects, editor_rects[1:]):
                assert left.x() + editors[0].width() <= right.x()
            reset_point = reset_button.mapTo(page, reset_button.rect().topLeft())
            move_point = move_button.mapTo(page, move_button.rect().topLeft())
            assert editor_rects[-1].x() + editors[-1].width() <= reset_point.x()
            assert abs(
                editor_rects[0].y() + editors[0].height() // 2
                - reset_point.y() - reset_button.height() // 2
            ) <= 1
            assert move_point.x() == reset_point.x()
            assert move_point.y() >= reset_point.y() + reset_button.height() - 1
            assert move_button.size() == reset_button.size()
            assert move_button.isFlat() == reset_button.isFlat()
            assert move_button.font() == reset_button.font()
            row_index = [item_key for _, item_key in viewer_rows].index(key)
            if row_index + 1 < len(viewer_rows):
                next_key = viewer_rows[row_index + 1][1]
                next_edit = dialog.shortcut_editors[next_key][0]
                next_reset = dialog.shortcut_reset_buttons[next_key]
                next_key_top = next_edit.mapTo(page, next_edit.rect().topLeft()).y()
                next_reset_top = next_reset.mapTo(page, next_reset.rect().topLeft()).y()
                assert move_point.y() + move_button.height() <= min(
                    next_key_top, next_reset_top
                )
            elif key[0] == "viewer":
                next_control = dialog.viewer_slideshow_chord_checkbox
                next_top = next_control.mapTo(page, next_control.rect().topLeft()).y()
                assert move_point.y() + move_button.height() <= next_top
            else:
                footer = page.findChild(type(reset_button), "reset_shortcuts_browser")
                assert footer is not None
                footer_top = footer.mapTo(page, footer.rect().topLeft()).y()
                assert move_point.y() + move_button.height() <= footer_top
        row = dialog.shortcut_rows[("viewer", "viewer_page_info")]
        baseline_height = row.height()
        page_info = dialog.shortcut_editors[("viewer", "viewer_page_info")][0]
        page_info.setKeySequence(QKeySequence("Right"))
        dialog._sync_shortcut_status("viewer", "viewer_page_info")
        qapp.processEvents()
        assert dialog.shortcut_move_buttons[("viewer", "viewer_page_info")].isEnabled()
        assert not dialog.shortcut_move_buttons[("viewer", "viewer_page_info")].isHidden()
        assert row.height() == baseline_height

        dialog._reset_shortcut_action("viewer", "viewer_page_info")
        qapp.processEvents()
        assert not dialog.shortcut_move_buttons[("viewer", "viewer_page_info")].isEnabled()
        assert not dialog.shortcut_move_buttons[("viewer", "viewer_page_info")].isHidden()
        assert row.height() == baseline_height
    finally:
        dialog.reject()
        install_ui_language("ja")


def test_conflict_names_edited_owner_and_reservation_is_not_transferable(
    tmp_path: Path, qapp
) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)
    page_info = dialog.shortcut_editors[("viewer", "viewer_page_info")][0]
    page_info.setKeySequence(QKeySequence("Right"))
    dialog._sync_shortcut_status("viewer", "viewer_page_info")

    assert not dialog.shortcut_status.isHidden()
    assert "次ページ" in dialog.shortcut_status.text()
    assert "ページ情報" in dialog.shortcut_status.text()
    assert dialog.shortcut_move_buttons[("viewer", "viewer_page_info")].isEnabled()
    assert not dialog.shortcut_move_buttons[("viewer", "viewer_next_page")].isEnabled()

    dialog._reset_shortcut_action("viewer", "viewer_page_info")
    toggle = dialog.shortcut_editors[("viewer", "viewer_slideshow_toggle")][0]
    toggle.setKeySequence(QKeySequence("1"))
    dialog._sync_shortcut_status("viewer", "viewer_slideshow_toggle")
    assert "予約" in dialog.shortcut_status.text()
    assert not any(button.isEnabled() for button in dialog.shortcut_move_buttons.values())


def test_viewer_shortcut_bindings_are_isolated_between_windows(tmp_path: Path, qapp) -> None:
    first_path = tmp_path / "first" / "page.png"
    second_path = tmp_path / "second" / "page.png"
    write_image(first_path)
    write_image(second_path)
    controller = make_controller(tmp_path / "profile", qapp)
    controller.settings["open_viewer_behavior"] = "always_new"
    first = controller.open_path(first_path)
    second = controller.open_path(second_path)
    finish_viewer_open(qapp, first)
    finish_viewer_open(qapp, second)
    try:
        first.apply_settings(
            {"shortcut_bindings": {"viewer": {"viewer_toggle_spread": ["T"]}}}
        )
        first.view_mode = "single"
        second.view_mode = "single"
        first_keys = {
            shortcut.key().toString()
            for shortcut in first._viewer_dynamic_shortcuts
        }
        second_keys = {
            shortcut.key().toString()
            for shortcut in second._viewer_dynamic_shortcuts
        }
        assert "T" in first_keys
        assert "T" not in second_keys
        next(shortcut for shortcut in first._viewer_dynamic_shortcuts if shortcut.key().toString() == "T").activated.emit()
        assert first.view_mode == "spread"
        second.viewer.setFocus()
        assert second.view_mode == "single"
    finally:
        close_controller(controller, qapp)
