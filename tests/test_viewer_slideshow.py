from unittest.mock import Mock

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QInputDialog,
    QLineEdit,
    QTextEdit,
    QSpinBox,
)

from app.config_manager import ConfigManager
from app.viewer_window import ViewerWindow


@pytest.fixture
def window(tmp_path, qapp):
    folder = tmp_path / 'pages'
    folder.mkdir()
    for index in range(3):
        Image.new('RGB', (24, 32), 'white').save(folder / f'{index}.png')
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    viewer = ViewerWindow(config_manager=config)
    viewer.show()
    viewer._finish_opened_book(viewer.book_session.open_book(folder), modal_on_empty=False)
    viewer.viewer.setFocus()
    qapp.processEvents()
    try:
        yield viewer
    finally:
        viewer.close()
        qapp.processEvents()


def press(widget, key, modifiers=Qt.KeyboardModifier.NoModifier, repeat=False):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, modifiers, '', repeat))


def release(widget, key, repeat=False):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyRelease, key, Qt.KeyboardModifier.NoModifier, '', repeat))


@pytest.mark.parametrize('digit', range(1, 10))
@pytest.mark.parametrize('s_first', [False, True])
def test_digit_chord_both_orders_and_repeat(window, monkeypatch, digit, s_first):
    starts = Mock(wraps=window.start_slideshow)
    monkeypatch.setattr(window, 'start_slideshow', starts)
    keys = [int(Qt.Key.Key_0) + digit, Qt.Key.Key_S]
    if s_first:
        keys.reverse()
    for key in keys:
        press(window.viewer, key)
    for key in keys:
        release(window.viewer, key, repeat=True)
        press(window.viewer, key, repeat=True)
    for key in reversed(keys):
        release(window.viewer, key)
    starts.assert_called_once_with(digit)
    assert window.slideshow_timer.isActive()
    assert window.slideshow_timer.interval() == digit * 1000


def test_released_digit_is_not_a_sequence_and_bare_s_toggles(window):
    window.set_slideshow_interval(20)
    QTest.keyClick(window.viewer, Qt.Key.Key_4)
    assert not window.slideshow_timer.isActive()
    QTest.keyClick(window.viewer, Qt.Key.Key_S)
    assert window.slideshow_timer.interval() == 20000
    assert window.slideshow_timer.isActive()
    QTest.keyClick(window.viewer, Qt.Key.Key_S)
    assert not window.slideshow_timer.isActive()


@pytest.mark.parametrize('kind', [QEvent.Type.WindowDeactivate, QEvent.Type.FocusOut])
def test_focus_loss_clears_held_state(window, kind):
    press(window.viewer, Qt.Key.Key_2)
    press(window.viewer, Qt.Key.Key_S)
    window.slideshow_timer.stop()
    target = window if kind == QEvent.Type.WindowDeactivate else window.viewer
    QApplication.sendEvent(target, QEvent(kind))
    assert not window.slideshow_keys.digits
    assert not window.slideshow_keys.s_held
    release(window.viewer, Qt.Key.Key_S)
    assert not window.slideshow_timer.isActive()
    window.set_slideshow_interval(20)
    QTest.keyClick(window.viewer, Qt.Key.Key_S)
    assert window.slideshow_timer.interval() == 20000


@pytest.mark.parametrize('editor_type', [QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox])
def test_editable_fields_do_not_trigger(window, qapp, editor_type):
    editor = editor_type(window)
    editor.show()
    editor.setFocus()
    qapp.processEvents()
    press(editor, Qt.Key.Key_3)
    QTest.keyClick(editor, Qt.Key.Key_S)
    release(editor, Qt.Key.Key_3)
    QTest.keyClick(editor, Qt.Key.Key_S, Qt.KeyboardModifier.ShiftModifier)
    assert not window.slideshow_timer.isActive()
    assert not window.slideshow_keys.digits
    editor.deleteLater()


def test_shift_s_enter_starts_and_cancel_preserves_interval(window, qapp):
    def accept_dialog():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QInputDialog)
        dialog.setDoubleValue(7)
        QTest.keyClick(dialog, Qt.Key.Key_Return)

    QTimer.singleShot(0, accept_dialog)
    QTest.keyClick(window.viewer, Qt.Key.Key_S, Qt.KeyboardModifier.ShiftModifier)
    assert window.slideshow_timer.isActive()
    assert window.slideshow_timer.interval() == 7000
    window.slideshow_timer.stop()
    window.viewer.setFocus()
    qapp.processEvents()
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
    QTest.keyClick(window.viewer, Qt.Key.Key_S, Qt.KeyboardModifier.ShiftModifier)
    assert window.slideshow_timer.interval() == 7000
    assert not window.slideshow_timer.isActive()


def test_custom_slideshow_interval_accepts_multidigit_typing_and_persists(window, qapp):
    def accept_dialog():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QInputDialog)
        spin = dialog.findChild(QDoubleSpinBox)
        assert spin is not None
        editor = spin.lineEdit()
        editor.selectAll()
        QTest.keyClicks(editor, '12')
        assert spin.value() == 12.0
        editor.selectAll()
        QTest.keyClicks(editor, '180')
        assert spin.value() == 180.0
        spin.stepDown()
        spin.stepUp()
        assert spin.value() == 180.0
        QTest.keyClick(dialog, Qt.Key.Key_Return)

    QTimer.singleShot(0, accept_dialog)
    QTest.keyClick(window.viewer, Qt.Key.Key_S, Qt.KeyboardModifier.ShiftModifier)
    assert window.slideshow_timer.interval() == 180000
    assert window.config.get('slideshow_interval_ms') == 180000


def test_menu_presets_persist_and_custom_cancel_restores_check(window, monkeypatch):
    window.slideshow_repeat_action.trigger()
    assert window.config.get('slideshow_repeat') is True
    assert window.slideshow_repeat
    window.auto_open_adjacent_book_action.trigger()
    assert window.config.get('auto_open_adjacent_book') is True
    assert tuple(window.slideshow_interval_actions) == (1, 3, 5, 10, 20, 30, 60)
    for seconds, action in window.slideshow_interval_actions.items():
        action.trigger()
        assert action.isChecked()
        assert window.config.get('slideshow_interval_ms') == seconds * 1000
        assert not window.slideshow_timer.isActive()
    monkeypatch.setattr(QInputDialog, 'getDouble', lambda *args: (2.5, True))
    window.slideshow_custom_action.trigger()
    assert window.slideshow_timer.interval() == 2500
    assert window.slideshow_custom_action.isChecked()
    window.slideshow_interval_actions[5].trigger()
    monkeypatch.setattr(QInputDialog, 'getDouble', lambda *args: (2.5, False))
    window.slideshow_custom_action.trigger()
    assert window.slideshow_interval_actions[5].isChecked()
    assert not window.slideshow_custom_action.isChecked()


def test_shared_interval_update(window):
    window.config.apply({'slideshow_interval_ms': 10000})
    assert window.slideshow_timer.interval() == 10000
    assert window.slideshow_interval_actions[10].isChecked()


@pytest.mark.parametrize('interval, expected', [(None, 3000), (-1, 500), (700000, 600000), (2500, 2500)])
def test_slideshow_config_defaults_normalization_and_roundtrip(tmp_path, interval, expected):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    assert config.get('slideshow_repeat') is False
    config.apply({'slideshow_interval_ms': interval, 'slideshow_repeat': 'invalid'})
    config.save()
    restored = ConfigManager(config.path).load()
    assert restored['slideshow_interval_ms'] == expected
    assert restored['slideshow_repeat'] is False


@pytest.mark.parametrize('repeat', [False, True])
def test_end_repeat_or_stop_and_independent_loop_setting(window, repeat):
    window.config.apply({'slideshow_repeat': repeat, 'loop_book_navigation': True})
    assert window.slideshow_repeat_action.isChecked() is repeat
    window.last_page()
    last = window.model.current_index
    window.start_slideshow(60)
    window._advance_slideshow()
    assert window.model.current_index == (0 if repeat else last)
    assert window.slideshow_timer.isActive() is repeat
    assert window.config.get('loop_book_navigation') is True


@pytest.mark.parametrize('repeat', [False, True])
def test_next_search_precedes_repeat_and_boundary_is_not_retried(window, repeat):
    window.config.apply({'slideshow_repeat': repeat, 'auto_open_adjacent_book': True})
    handler = Mock(return_value='searching')
    window._adjacent_book_handler = handler
    window.last_page()
    window.start_slideshow(60)
    window._advance_slideshow()
    assert window._slideshow_waiting_for_next
    assert not window.slideshow_timer.isActive()
    assert window.model.current_index > 0
    window._advance_slideshow()
    handler.assert_called_once()
    window.complete_adjacent_book_search(1, 'boundary')
    assert window.slideshow_timer.isActive() is repeat
    if repeat:
        assert window.model.current_index == 0
        window.model.go_to_index(window.model.total_pages - 1)
        window._advance_slideshow()
        handler.assert_called_once()


def test_stop_during_search_does_not_resume_on_late_failure(window):
    window.config.apply({'slideshow_repeat': True, 'auto_open_adjacent_book': True})
    window._adjacent_book_handler = Mock(return_value='searching')
    window.last_page()
    window.start_slideshow(60)
    window._advance_slideshow()
    window.toggle_slideshow()
    window.complete_adjacent_book_search(1, 'error')
    assert not window.slideshow_timer.isActive()
    assert not window._slideshow_waiting_for_next


@pytest.mark.parametrize('broken', [False, True])
def test_real_next_book_open_resumes_or_falls_back(tmp_path, qapp, broken):
    from tests.test_application_controller import (
        make_controller, write_archive, finish_viewer_open, close_controller, wait_until,
    )
    first, second = tmp_path / '01.cbz', tmp_path / '02.cbz'
    write_archive(first)
    if broken:
        second.write_bytes(b'broken zip')
    else:
        write_archive(second)
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'slideshow_repeat': True, 'auto_open_adjacent_book': True})
    try:
        viewer = controller.open_path(first)
        finish_viewer_open(qapp, viewer)
        viewer.last_page()
        viewer.start_slideshow(60)
        viewer._advance_slideshow()
        assert wait_until(qapp, lambda: not viewer._slideshow_waiting_for_next, timeout=5)
        assert viewer.book_session.current_path == (first if broken else second)
        assert viewer.slideshow_timer.isActive()
        assert viewer.model.current_index == 0
        if broken:
            handler = Mock(wraps=viewer._adjacent_book_handler)
            viewer._adjacent_book_handler = handler
            viewer._advance_slideshow()
            handler.assert_not_called()
    finally:
        close_controller(controller, qapp)
