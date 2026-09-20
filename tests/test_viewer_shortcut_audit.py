from io import BytesIO
from zipfile import ZipFile

from PIL import Image
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QPixmap, QShortcut, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QInputDialog,
    QLineEdit,
    QMessageBox,
)

from tests.test_application_controller import make_controller, close_controller, finish_viewer_open, wait_until


@pytest.fixture
def book(tmp_path, qapp):
    paths = []
    for n in range(3):
        path = tmp_path / f'{n:02d}.cbz'
        with ZipFile(path, 'w') as archive:
            for page in range(6):
                buffer = BytesIO()
                Image.new('RGB', (800, 1200), (20 + page * 30, 40 + n * 50, 90)).save(buffer, 'PNG')
                archive.writestr(f'{page:02d}.png', buffer.getvalue())
        paths.append(path)
    controller = make_controller(tmp_path / 'profile', qapp)
    controller.config.apply({'view_mode': 'single', 'fit_mode': 'fit_window',
                             'single_first_page': False, 'reading_direction': 'ltr'})
    window = controller.open_path(paths[1])
    window.resize(800, 600)
    window.activateWindow()
    window.viewer.setFocus()
    finish_viewer_open(qapp, window)
    settle(qapp, window, 0)
    assert QApplication.focusWidget() is window.viewer
    try:
        yield window, paths
    finally:
        close_controller(controller, qapp)


def settle(app, window, page):
    def ready():
        window.viewer.render(QPixmap(window.viewer.size()))
        app.processEvents()  # Commit queued post-paint history/action state.
        return (window.model.current_index == page and page in window.viewer.displayed_page_indexes
                and window.history_back_action.isEnabled() == bool(window.presentation_state.back_history)
                and window.history_forward_action.isEnabled() == bool(window.presentation_state.forward_history))
    assert wait_until(app, ready, timeout=4), {
        'model': window.model.current_index,
        'displayed': window.viewer.displayed_page_indexes,
        'back': [entry.values.page_index for entry in window.presentation_state.back_history],
        'forward': [entry.values.page_index for entry in window.presentation_state.forward_history],
        'actions': (window.history_back_action.isEnabled(), window.history_forward_action.isEnabled()),
        'focus': QApplication.focusWidget(),
    }


def key(window, keycode, modifiers=Qt.KeyboardModifier.NoModifier, target=None):
    QTest.keyClick(target or window.viewer, keycode, modifiers)


@pytest.mark.parametrize('next_key,previous_key', [(Qt.Key.Key_Right, Qt.Key.Key_Left),
                                                 (Qt.Key.Key_Space, Qt.Key.Key_Backspace),
                                                 (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp)])
def test_listed_page_navigation(book, qapp, next_key, previous_key):
    window, _ = book
    key(window, next_key)
    settle(qapp, window, 1)
    key(window, previous_key)
    settle(qapp, window, 0)
    key(window, Qt.Key.Key_End)
    settle(qapp, window, 5)
    key(window, Qt.Key.Key_Home)
    settle(qapp, window, 0)


def test_spread_and_single_page_chords(book, qapp):
    window, _ = book
    key(window, Qt.Key.Key_D)
    assert window.view_mode == 'spread'
    settle(qapp, window, 0)
    key(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    settle(qapp, window, 1)
    key(window, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    settle(qapp, window, 0)
    key(window, Qt.Key.Key_D)
    assert window.view_mode == 'single'


def test_history_chords(book, qapp):
    window, _ = book
    for page in (1, 2):
        key(window, Qt.Key.Key_Right)
        settle(qapp, window, page)
    key(window, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    settle(qapp, window, 1)
    key(window, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    settle(qapp, window, 2)


def test_g_opens_real_page_dialog_and_enter_navigates(book, qapp):
    window, _ = book
    seen = []
    def choose():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QInputDialog)
        seen.append(dialog)
        dialog.setIntValue(4)
        QTest.keyClick(dialog, Qt.Key.Key_Return)
    QTimer.singleShot(0, choose)
    key(window, Qt.Key.Key_G)
    assert seen
    settle(qapp, window, 3)


@pytest.mark.parametrize('modifiers', [Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier])
def test_bookmark_chords(book, modifiers):
    window, _ = book
    assert window._bookmark_pages() == []
    key(window, Qt.Key.Key_B, modifiers)
    assert window._bookmark_pages() == [0]
    key(window, Qt.Key.Key_B, modifiers)
    assert window._bookmark_pages() == []


def test_next_previous_book_chords(book, qapp):
    window, paths = book
    key(window, Qt.Key.Key_PageDown, Qt.KeyboardModifier.ControlModifier)
    assert wait_until(qapp, lambda: window.book_session.current_path == paths[2], timeout=4)
    finish_viewer_open(qapp, window)
    settle(qapp, window, 0)
    window.viewer.setFocus()
    key(window, Qt.Key.Key_PageUp, Qt.KeyboardModifier.ControlModifier)
    assert wait_until(qapp, lambda: window.book_session.current_path == paths[1], timeout=4)


def test_clipboard_and_page_info_chords(book, monkeypatch):
    window, _ = book
    clipboard = QApplication.clipboard()
    clipboard.clear()
    key(window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert not clipboard.image().isNull()
    assert clipboard.image().size().width() == 800
    clipboard.clear()
    key(window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert clipboard.text() == window.model.display_path_for_index(0)
    clipboard.clear()
    key(window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    assert clipboard.pixmap().size() == window.viewer.size()
    info = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: info.append(args))
    key(window, Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier)
    assert len(info) == 1 and '800 x 1200' in info[0][2]


def test_fullscreen_escape_magnifier_and_doubleclick(book, qapp):
    window, _ = book
    key(window, Qt.Key.Key_F)
    assert window.fullscreen_chrome.fullscreen
    key(window, Qt.Key.Key_F)
    assert not window.fullscreen_chrome.fullscreen
    key(window, Qt.Key.Key_F)
    assert window.fullscreen_chrome.fullscreen
    key(window, Qt.Key.Key_Escape)
    assert not window.fullscreen_chrome.fullscreen
    window.viewer.setFocus()
    QTest.mouseMove(window.viewer, window.viewer.rect().center())
    key(window, Qt.Key.Key_Z)
    assert window.viewer.magnifier_active or window.viewer.magnifier_selecting
    key(window, Qt.Key.Key_Z)
    assert not window.viewer.magnifier_active and not window.viewer.magnifier_selecting
    QTest.mouseDClick(window.viewer, Qt.MouseButton.LeftButton, pos=window.viewer.rect().center())
    assert window.fullscreen_chrome.fullscreen
    QTest.mouseDClick(window.viewer, Qt.MouseButton.LeftButton, pos=window.viewer.rect().center())
    assert not window.fullscreen_chrome.fullscreen


def test_escape_cancels_loupe_before_fullscreen(book):
    window, _ = book
    key(window, Qt.Key.Key_F)
    window.viewer.setFocus()
    QTest.mouseMove(window.viewer, window.viewer.rect().center())
    key(window, Qt.Key.Key_Z)
    assert window.viewer.magnifier_active or window.viewer.magnifier_selecting
    key(window, Qt.Key.Key_Escape)
    assert not window.viewer.magnifier_active and not window.viewer.magnifier_selecting
    assert window.fullscreen_chrome.fullscreen
    key(window, Qt.Key.Key_Escape)
    assert not window.fullscreen_chrome.fullscreen


def test_escape_cancels_gesture_before_fullscreen(book):
    window, _ = book
    key(window, Qt.Key.Key_F)
    window.viewer.setFocus()
    position = window.viewer.rect().center()
    QTest.mousePress(window.viewer, Qt.MouseButton.RightButton, pos=position)
    QTest.mouseMove(window.viewer, position + QPoint(100, 0))
    assert window.viewer.gesture_in_progress
    key(window, Qt.Key.Key_Escape)
    assert not window.viewer.gesture_in_progress
    assert window.fullscreen_chrome.fullscreen
    QTest.mouseRelease(window.viewer, Qt.MouseButton.RightButton, pos=position + QPoint(100, 0))
    key(window, Qt.Key.Key_Escape)
    assert not window.fullscreen_chrome.fullscreen


def test_overflow_arrows_pan_and_space_scrolls_before_page_turn(book):
    window, _ = book
    window.viewer.set_manual_zoom(2)
    origin = QPoint(window.viewer._pan)
    key(window, Qt.Key.Key_Right)
    small = abs(window.viewer._pan.x() - origin.x())
    assert small > 0 and window.model.current_index == 0
    previous = QPoint(window.viewer._pan)
    key(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert abs(window.viewer._pan.x() - previous.x()) > small
    assert window.model.current_index == 0
    key(window, Qt.Key.Key_Left)
    key(window, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    for forward, backward in [(Qt.Key.Key_Space, Qt.Key.Key_Backspace), (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp)]:
        previous = window.viewer._pan.y()
        key(window, forward)
        assert window.viewer._pan.y() < previous and window.model.current_index == 0
        key(window, backward)
        assert window.viewer._pan.y() == previous and window.model.current_index == 0


def test_shift_plus_and_equal_alias_zoom(book):
    window, _ = book
    before = window.viewer._scale_for_current_mode()
    key(window, Qt.Key.Key_Plus, Qt.KeyboardModifier.ShiftModifier)
    assert window.viewer.manual_zoom == pytest.approx(before * 1.15)
    key(window, Qt.Key.Key_Equal)
    assert window.viewer.manual_zoom == pytest.approx(before * 1.15**2)
    key(window, Qt.Key.Key_Plus, Qt.KeyboardModifier.KeypadModifier)
    assert window.viewer.manual_zoom == pytest.approx(before * 1.15**3)


@pytest.mark.parametrize('keycode,factor', [(Qt.Key.Key_Plus, 1.15), (Qt.Key.Key_Minus, 1/1.15)])
def test_zoom_chords_start_at_displayed_fit_scale(book, keycode, factor):
    window, _ = book
    before = window.viewer._scale_for_current_mode()
    assert before < 1
    key(window, keycode)
    assert window.viewer.fit_mode == 'manual_zoom'
    assert window.viewer.manual_zoom == pytest.approx(before * factor)
    key(window, keycode)
    assert window.viewer.manual_zoom == pytest.approx(before * factor * factor)
    key(window, Qt.Key.Key_0)
    assert window.viewer.fit_mode == 'fit_window'


def test_ctrl_wheel_delivered_to_canvas_zooms_without_turning_page(book):
    window, _ = book
    before = window.viewer._scale_for_current_mode()
    pos = window.viewer.rect().center()
    event = QWheelEvent(QPointF(pos), QPointF(window.viewer.mapToGlobal(pos)), QPoint(), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                        Qt.ScrollPhase.ScrollUpdate, False)
    QApplication.sendEvent(window.viewer, event)
    assert event.isAccepted()
    assert window.viewer.manual_zoom == pytest.approx(before * 1.15)
    assert window.model.current_index == 0


def test_registered_listed_chords_are_unambiguous(book):
    window, _ = book
    counts = {}
    for shortcut in window.findChildren(QShortcut):
        text = shortcut.key().toString(QKeySequence.SequenceFormat.PortableText)
        counts[text] = counts.get(text, 0) + 1
    for action in window.findChildren(QAction):
        for shortcut in action.shortcuts():
            text = shortcut.toString(QKeySequence.SequenceFormat.PortableText)
            if text:
                counts[text] = counts.get(text, 0) + 1
    assert all(count == 1 for count in counts.values()), counts


@pytest.mark.parametrize('name', ['slider', 'page_list'])
def test_child_control_focus_keeps_window_chords_and_home_end(book, qapp, name):
    window, _ = book
    control = getattr(window, name)
    if name == 'page_list':
        window.page_list_dock.show()
    control.setFocus()
    qapp.processEvents()
    assert QApplication.focusWidget() is control
    key(window, Qt.Key.Key_End, target=control)
    settle(qapp, window, 5)
    key(window, Qt.Key.Key_Home, target=control)
    settle(qapp, window, 0)
    direction = window.reading_direction
    key(window, Qt.Key.Key_R, Qt.KeyboardModifier.ShiftModifier, target=control)
    assert window.reading_direction != direction
    key(window, Qt.Key.Key_R, target=control)
    assert window.reading_direction != direction


def test_edit_field_owns_letters_and_copy(book, qapp):
    window, _ = book
    edit = QLineEdit(window)
    edit.show()
    edit.setFocus()
    qapp.processEvents()
    try:
        key(window, Qt.Key.Key_D, target=edit)
        key(window, Qt.Key.Key_F, target=edit)
        key(window, Qt.Key.Key_Z, target=edit)
        key(window, Qt.Key.Key_Plus, Qt.KeyboardModifier.ShiftModifier, target=edit)
        key(window, Qt.Key.Key_Minus, target=edit)
        assert edit.text() == 'dfz+-'
        assert window.view_mode == 'single' and not window.fullscreen_chrome.fullscreen
        assert window.viewer.fit_mode == 'fit_window'
        edit.selectAll()
        key(window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier, target=edit)
        assert QApplication.clipboard().text() == 'dfz+-'
    finally:
        edit.close()


def test_viewer_close_key_default_is_ctrl_w_and_escape_only_cancels_state(
    book,
    qapp,
):
    window, _ = book
    requests = []
    window.set_close_request_handler(lambda target: requests.append(target))
    window.viewer.setFocus()

    QTest.keyClick(
        window.viewer,
        Qt.Key.Key_W,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert requests == [window]
    QTest.keyClick(window.viewer, Qt.Key.Key_Escape)
    assert requests == [window]


def test_viewer_close_key_applies_immediately_and_can_be_disabled(book, qapp):
    window, _ = book
    requests = []
    window.set_close_request_handler(lambda target: requests.append(target))
    window.viewer.setFocus()

    window.config.apply({"viewer_close_shortcut": "Ctrl+Shift+W"})
    QTest.keyClick(
        window.viewer,
        Qt.Key.Key_W,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert requests == []
    QTest.keyClick(
        window.viewer,
        Qt.Key.Key_W,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert requests == [window]

    window.config.apply({"viewer_close_shortcut": ""})
    QTest.keyClick(
        window.viewer,
        Qt.Key.Key_W,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert requests == [window]


def test_viewer_close_key_does_not_fire_in_text_or_modal_input(book, qapp):
    window, _ = book
    requests = []
    window.set_close_request_handler(lambda target: requests.append(target))

    edit = QLineEdit(window)
    edit.show()
    edit.setFocus()
    qapp.processEvents()
    QTest.keyClick(
        edit,
        Qt.Key.Key_W,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert requests == []
    edit.close()

    dialog = QDialog(window)
    dialog.setModal(True)
    modal_edit = QLineEdit(dialog)
    modal_edit.show()
    dialog.show()
    modal_edit.setFocus()
    qapp.processEvents()
    QTest.keyClick(
        modal_edit,
        Qt.Key.Key_W,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert requests == []
    dialog.close()
