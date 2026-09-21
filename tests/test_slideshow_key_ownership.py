from __future__ import annotations

import pytest

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from app.slideshow_keys import SlideshowKeys


def make_window(qapp: QApplication) -> QWidget:
    window = QWidget()
    window.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    window.show()
    window.activateWindow()
    window.setFocus()
    qapp.processEvents()
    return window


@pytest.mark.parametrize('digit', range(1, 10))
@pytest.mark.parametrize('enabled', [False, True])
def test_shift_digits_remain_available_to_normal_shortcuts(qapp, digit, enabled):
    window = make_window(qapp)
    called = []
    normal = QShortcut(QKeySequence(f'Shift+{digit}'), window)
    normal.activated.connect(lambda: called.append('normal'))
    keys = SlideshowKeys(
        window,
        start=lambda _: called.append('start'),
        toggle=lambda: called.append('toggle'),
        choose=lambda: called.append('choose'),
        chord_enabled=lambda: enabled,
    )
    try:
        QTest.keyClick(window, Qt.Key(int(Qt.Key.Key_0) + digit), Qt.KeyboardModifier.ShiftModifier)
        assert called == ['normal']
    finally:
        keys.deleteLater()
        window.close()
        qapp.processEvents()


def test_shift_digit_cancels_pending_chord_without_toggling_on_release(qapp):
    window = make_window(qapp)
    called = []
    normal = QShortcut(QKeySequence('Shift+1'), window)
    normal.activated.connect(lambda: called.append('normal'))
    keys = SlideshowKeys(
        window,
        start=lambda _: called.append('start'),
        toggle=lambda: called.append('toggle'),
        choose=lambda: called.append('choose'),
    )
    try:
        QTest.keyPress(window, Qt.Key.Key_S)
        QTest.keyClick(window, Qt.Key.Key_1, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyRelease(window, Qt.Key.Key_S)
        assert called == ['normal']
        QTest.keyClick(window, Qt.Key.Key_S)
        assert called == ['normal', 'toggle']
    finally:
        keys.deleteLater()
        window.close()
        qapp.processEvents()


def test_shift_s_is_left_to_normal_dispatch_when_interval_moved(qapp) -> None:
    window = make_window(qapp)
    called: list[str] = []
    normal = QShortcut(QKeySequence("Shift+S"), window)
    normal.activated.connect(lambda: called.append("normal"))
    keys = SlideshowKeys(
        window,
        start=lambda _seconds: called.append("start"),
        toggle=lambda: called.append("toggle"),
        choose=lambda: called.append("choose"),
        chord_enabled=lambda: True,
        toggle_enabled=lambda: False,
        choose_enabled=lambda: False,
    )
    try:
        QTest.keyClick(window, Qt.Key.Key_S, Qt.KeyboardModifier.ShiftModifier)
        assert called == ["normal"]
    finally:
        keys.deleteLater()
        normal.deleteLater()
        window.close()
        qapp.processEvents()


def test_number_s_chord_keeps_both_key_orders(qapp) -> None:
    window = make_window(qapp)
    starts: list[int] = []
    keys = SlideshowKeys(
        window,
        start=starts.append,
        toggle=lambda: None,
        choose=lambda: None,
        chord_enabled=lambda: True,
        toggle_enabled=lambda: False,
        choose_enabled=lambda: False,
    )
    try:
        QTest.keyPress(window, Qt.Key.Key_1)
        QTest.keyPress(window, Qt.Key.Key_S)
        QTest.keyRelease(window, Qt.Key.Key_S)
        QTest.keyRelease(window, Qt.Key.Key_1)
        QTest.keyPress(window, Qt.Key.Key_S)
        QTest.keyPress(window, Qt.Key.Key_2)
        QTest.keyRelease(window, Qt.Key.Key_2)
        QTest.keyRelease(window, Qt.Key.Key_S)
        assert starts == [1, 2]
    finally:
        keys.deleteLater()
        window.close()
        qapp.processEvents()
