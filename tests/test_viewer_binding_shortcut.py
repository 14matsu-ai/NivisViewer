import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton, QTextBrowser

from app.config_manager import ConfigManager
from app.i18n import install_ui_language, tr
from app.viewer_window import ViewerWindow


@pytest.fixture
def window(tmp_path, qapp):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    window = ViewerWindow(config_manager=config)
    window.show()
    window.activateWindow()
    window.viewer.setFocus()
    qapp.processEvents()
    try:
        assert QApplication.focusWidget() is window.viewer
        yield window
    finally:
        window.close()
        qapp.processEvents()


def test_open_with_shortcut_defaults_to_ctrl_t_and_works_without_menu(window, monkeypatch):
    from unittest.mock import Mock
    probe = Mock()
    monkeypatch.setattr(window, 'open_current_with_application_picker', probe)
    assert window.shortcut_bindings['viewer_open_with'] == ['Ctrl+T']
    assert window.viewer_open_with_action.shortcuts() == []
    window._rebuild_viewer_shortcuts()
    window.menuBar().hide()
    QTest.keyClick(window.viewer, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier)
    probe.assert_called_once_with()
    # Do not open a previously displayed file after the user has changed pages.
    window._open_with_after_probe('C:/images/old.png')


def test_shift_r_uses_actual_viewer_focus_route_and_persists(window, qapp):
    original = window.reading_direction
    QTest.keyClick(window.viewer, Qt.Key.Key_R)
    assert window.reading_direction == original
    QTest.keyClick(window.viewer, Qt.Key.Key_R, Qt.KeyboardModifier.ShiftModifier)
    changed = 'ltr' if original == 'rtl' else 'rtl'
    assert window.reading_direction == changed
    assert window.config.get('reading_direction') == changed
    assert window.ltr_action.isChecked() == (changed == 'ltr')
    assert window.rtl_action.isChecked() == (changed == 'rtl')
    QTest.keyClick(window.viewer, Qt.Key.Key_R, Qt.KeyboardModifier.ShiftModifier)
    assert window.reading_direction == original


@pytest.mark.parametrize('modifiers', [Qt.KeyboardModifier.ControlModifier,
                                      Qt.KeyboardModifier.AltModifier,
                                      Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier])
def test_other_r_chords_do_not_toggle(window, modifiers):
    original = window.reading_direction
    QTest.keyClick(window.viewer, Qt.Key.Key_R, modifiers)
    assert window.reading_direction == original


def test_shortcut_is_unique_and_keeps_context_and_repeat_behavior(window):
    shortcuts = window.findChildren(QShortcut)
    matching = [shortcut for shortcut in shortcuts if shortcut.key().toString() == 'Shift+R']
    assert len(matching) == 1
    assert not any(shortcut.key().toString() == 'R' for shortcut in shortcuts)
    assert matching[0].context() == Qt.ShortcutContext.WindowShortcut
    assert matching[0].autoRepeat()  # Same behavior as the old R QShortcut.
    original = window.reading_direction
    for expected in ('ltr' if original == 'rtl' else 'rtl', original):
        QApplication.sendEvent(window.viewer, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_R,
                              Qt.KeyboardModifier.ShiftModifier, 'R', True))
        assert window.reading_direction == expected


@pytest.mark.parametrize('modal', [False, True])
def test_text_or_modal_focus_does_not_change_binding(window, qapp, modal):
    dialog = QDialog(window) if modal else None
    if dialog:
        dialog.setModal(True)
        dialog.show()
        dialog.activateWindow()
    edit = QLineEdit(dialog or window)
    edit.show()
    edit.setFocus()
    qapp.processEvents()
    assert QApplication.focusWidget() is edit
    original = window.reading_direction
    try:
        QTest.keyClick(edit, Qt.Key.Key_R)
        QTest.keyClick(edit, Qt.Key.Key_R, Qt.KeyboardModifier.ShiftModifier)
        # QTest's key enum path does not synthesize layout-dependent uppercase
        # text on offscreen; both key events must nevertheless reach the edit.
        assert edit.text().casefold() == 'rr'
        assert window.reading_direction == original
    finally:
        if dialog:
            dialog.close()
        edit.close()


def test_modal_non_text_focus_blocks_parent_shortcut(window, qapp):
    dialog = QDialog(window)
    dialog.setModal(True)
    button = QPushButton('OK', dialog)
    dialog.show()
    dialog.activateWindow()
    button.setFocus()
    qapp.processEvents()
    assert QApplication.focusWidget() is button
    original = window.reading_direction
    try:
        QTest.keyClick(button, Qt.Key.Key_R, Qt.KeyboardModifier.ShiftModifier)
        assert window.reading_direction == original
    finally:
        dialog.close()


@pytest.mark.parametrize('language', ['ja', 'en'])
def test_help_matches_new_chord(window, monkeypatch, language):
    captured = []

    def capture_help(dialog):
        captured.append(dialog.findChild(QTextBrowser).toPlainText())
        dialog.reject()
        return 0

    monkeypatch.setattr(QDialog, 'exec', capture_help)
    install_ui_language(language)
    try:
        window.show_shortcuts_help()
        lines = captured[0].splitlines()
        assert any(
            line.startswith(f'{tr("読み方向切替")}:')
            and 'Shift+R' in line
            for line in lines
        )
        assert not any(line.startswith('R:') for line in lines)
    finally:
        install_ui_language('ja')
