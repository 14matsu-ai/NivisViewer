"""Viewer-local held-key chords, using Qt events rather than global input."""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QDialog, QLineEdit,
    QPlainTextEdit, QTextEdit, QWidget,
)


class SlideshowKeys(QObject):
    def __init__(
        self,
        window,
        *,
        start,
        toggle,
        choose,
        chord_enabled=lambda: True,
        toggle_enabled=lambda: True,
        choose_enabled=lambda: True,
    ):
        super().__init__(window)
        self.window = window
        self.start = start
        self.toggle = toggle
        self.choose = choose
        self.chord_enabled = chord_enabled
        self.toggle_enabled = toggle_enabled
        self.choose_enabled = choose_enabled
        self.digits = []
        self.s_held = False
        self.used = False
        QApplication.instance().installEventFilter(self)

    def reset(self):
        self.digits.clear()
        self.s_held = False
        self.used = False

    def _eligible(self, watched):
        if not isinstance(watched, QWidget) or watched.window() is not self.window:
            return False
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return False
        focus = QApplication.focusWidget()
        if focus is None or focus.window() is not self.window:
            return False
        current = focus
        while current is not self.window:
            if isinstance(current, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QDialog)):
                return False
            if isinstance(current, QComboBox) and current.isEditable():
                return False
            current = current.parentWidget()
            if current is None:
                return False
        return True

    def eventFilter(self, watched, event):
        kind = event.type()
        if (
            (watched is self.window and kind == QEvent.Type.WindowDeactivate)
            or (isinstance(watched, QWidget) and watched.window() is self.window
                and kind == QEvent.Type.FocusOut)
        ):
            self.reset()
        if not isinstance(event, QKeyEvent) or kind not in {
            QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress, QEvent.Type.KeyRelease,
        }:
            return False
        if not self._eligible(watched):
            return False
        key = event.key()
        is_s = key == Qt.Key.Key_S
        digit = int(key) - int(Qt.Key.Key_0) if Qt.Key.Key_1 <= key <= Qt.Key.Key_9 else None
        if not is_s and digit is None:
            return False
        if not bool(self.chord_enabled()):
            return False
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if digit is not None and modifiers != Qt.KeyboardModifier.NoModifier:
            # Only unmodified digits belong to the held-key chord. Leave
            # Shift/Ctrl/Alt digit shortcuts to Qt, including their override.
            self.reset()
            return False
        is_shift_s = is_s and modifiers == Qt.KeyboardModifier.ShiftModifier
        if is_shift_s and not bool(self.choose_enabled()):
            # Shift+S belongs to the normal shortcut dispatcher once the
            # interval action has been moved away from it.
            return False
        if modifiers not in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier):
            self.reset()
            return False
        if kind == QEvent.Type.ShortcutOverride:
            if is_shift_s and bool(self.choose_enabled()):
                event.accept()
                return True
            if is_s and modifiers == Qt.KeyboardModifier.NoModifier and (
                self.s_held or self.digits or bool(self.toggle_enabled())
            ):
                event.accept()
                return True
            if digit is not None:
                event.accept()
                return True
            return False
        if event.isAutoRepeat():
            return (
                is_shift_s
                or bool(self.digits)
                or (is_s and (self.s_held or bool(self.toggle_enabled())))
            )
        if kind == QEvent.Type.KeyRelease:
            if digit is not None:
                if digit in self.digits:
                    self.digits.remove(digit)
                return False
            toggle = self.s_held and not self.used
            self.s_held = False
            self.used = False
            if toggle and bool(self.toggle_enabled()):
                self.toggle()
            return True
        if is_s:
            if self.s_held:
                return True
            self.s_held = True
            if modifiers == Qt.KeyboardModifier.ShiftModifier:
                self.reset()  # A modal dialog owns subsequent releases.
                self.choose()
                return True
        elif modifiers == Qt.KeyboardModifier.NoModifier:
            if digit not in self.digits:
                self.digits.append(digit)
        if self.s_held and self.digits and not self.used:
            self.used = True
            self.start(self.digits[-1])
        # Bare S is committed on release, so S-first chords never briefly
        # toggle the old interval before starting the requested interval.
        return is_s or self.s_held
