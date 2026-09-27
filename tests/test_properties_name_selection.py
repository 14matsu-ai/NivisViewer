import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QLineEdit, QWidget

from app.browser_window import BrowserWindow
from app.file_properties_dialog import FilePropertiesDialog


@pytest.mark.parametrize('name, folder, expected', [
    ('日本語の書庫.zip', False, '日本語の書庫'),
    ('📚本.tar.zip', False, '📚本.tar'),
    ('README', False, 'README'),
    ('.hidden', False, '.hidden'),
    ('資料.zip', True, '資料.zip'),
])
def test_initial_click_and_focus_reentry_select_basename(tmp_path, qapp, name, folder, expected):
    path = tmp_path / name
    path.mkdir() if folder else path.write_bytes(b'unchanged')
    dialog = FilePropertiesDialog(path)
    dialog.show()
    qapp.processEvents()
    edit = dialog.name_edit
    try:
        QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
        assert edit.selectedText() == expected
        QTest.mouseClick(edit, Qt.MouseButton.LeftButton, pos=QPoint(8, edit.height() // 2))
        assert not edit.hasSelectedText()
        dialog.cancel_button.setFocus()
        qapp.processEvents()
        QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
        assert edit.selectedText() == expected
        assert edit.text() == name
        assert path.exists()
    finally:
        dialog.reject()


def test_properties_left_from_selected_name_moves_to_selection_start(tmp_path, qapp):
    path = tmp_path / '日本語📚.zip'
    path.write_bytes(b'')
    dialog = FilePropertiesDialog(path)
    dialog.show()
    qapp.processEvents()
    edit = dialog.name_edit
    try:
        assert edit.selectedText() == '日本語📚'
        QTest.keyClick(edit, Qt.Key.Key_Left)
        assert not edit.hasSelectedText() and edit.cursorPosition() == 0
        edit.selectAll()
        QTest.keyClick(edit, Qt.Key.Key_Left)
        assert not edit.hasSelectedText() and edit.cursorPosition() == 0
        edit.setSelection(2, 2)
        QTest.keyClick(edit, Qt.Key.Key_Left)
        assert edit.cursorPosition() == 2
        edit.setSelection(2, 2)
        QTest.keyClick(edit, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        assert edit.hasSelectedText()
    finally:
        dialog.reject()


def test_f2_prompt_left_from_selected_name_moves_to_start(qapp, monkeypatch):
    observed = []

    def inspect(dialog):
        qapp.processEvents()
        edit = dialog.findChild(QLineEdit)
        assert edit is not None and edit.selectedText() == '日本語📚'
        QTest.keyClick(edit, Qt.Key.Key_Left)
        observed.append((edit.hasSelectedText(), edit.cursorPosition()))
        edit.selectAll()
        QTest.keyClick(edit, Qt.Key.Key_Left)
        observed.append((edit.hasSelectedText(), edit.cursorPosition()))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QInputDialog, 'exec', inspect)
    parent = QWidget()
    try:
        assert BrowserWindow._prompt_for_filename(
            parent, '名前の変更', '新しい名前:', '日本語📚.zip'
        ) is None
        assert observed == [(False, 0), (False, 0)]
    finally:
        parent.close()


def test_draft_reentry_apply_and_cancel_preserve_existing_contract(tmp_path, qapp):
    path = tmp_path / '元の名前.zip'
    path.write_bytes(b'original')
    dialog = FilePropertiesDialog(path)
    emitted = []
    dialog.rename_requested.connect(lambda name, close: emitted.append((name, close)))
    dialog.show()
    qapp.processEvents()
    try:
        edit = dialog.name_edit
        edit.setText('編集中.tar.cbz')
        dialog.cancel_button.setFocus()
        edit.setFocus(Qt.FocusReason.TabFocusReason)
        qapp.processEvents()
        assert edit.selectedText() == '編集中.tar'
        assert edit.text() == '編集中.tar.cbz'
        assert not emitted
        dialog.apply_button.click()
        assert emitted == [('編集中.tar.cbz', False)]
        assert path.read_bytes() == b'original'
        dialog.reject()
        assert path.exists()
        assert not (tmp_path / '編集中.tar.cbz').exists()
    finally:
        dialog.reject()


def test_drag_and_keyboard_selection_remain_normal(tmp_path, qapp):
    path = tmp_path / 'long-filename.zip'
    path.write_bytes(b'')
    dialog = FilePropertiesDialog(path)
    dialog.show()
    qapp.processEvents()
    edit = dialog.name_edit
    try:
        start = QPoint(5, edit.height() // 2)
        end = QPoint(55, edit.height() // 2)
        QTest.mousePress(edit, Qt.MouseButton.LeftButton, pos=start)
        QApplication.sendEvent(edit, QMouseEvent(
            QEvent.Type.MouseMove, QPointF(end), QPointF(end),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        ))
        QTest.mouseRelease(edit, Qt.MouseButton.LeftButton, pos=end)
        assert edit.hasSelectedText()
        assert edit.selectedText() != 'long-filename'
        QTest.keyClick(edit, Qt.Key.Key_End)
        assert not edit.hasSelectedText()
        QTest.keyClick(edit, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        assert edit.selectedText() == 'p'
        assert edit.text() == path.name
    finally:
        dialog.reject()
