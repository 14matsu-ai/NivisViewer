import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMessageBox, QPushButton

from app.config_manager import ConfigManager
from app.i18n import install_ui_language
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


@pytest.mark.parametrize('language,expected', [('ja', 'Shift+R: 左綴じ / 右綴じ切替'),
                                              ('en', 'Shift+R: Toggle left / right binding')])
def test_help_matches_new_chord(window, monkeypatch, language, expected):
    captured = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: captured.append(args[2]))
    install_ui_language(language)
    try:
        window.show_shortcuts_help()
        assert expected in captured[0].splitlines()
        assert not any(line.startswith('R:') for line in captured[0].splitlines())
    finally:
        install_ui_language('ja')
