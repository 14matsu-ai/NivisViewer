"""Compact registered-tag filter buttons for the Browser menu bar."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeyEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMenu,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from .browser_filter import BrowserFilterState
from .browser_tags import normalize_tag_registry
from .i18n import tr


class _TagQuickFilterButton(QToolButton):
    activated = Signal(str, object, object)

    def __init__(self, name: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.name = name
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pressed_button = Qt.MouseButton.NoButton
        self._pressed_modifiers = Qt.KeyboardModifier.NoModifier

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() in {
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.RightButton,
        }:
            self._pressed_button = event.button()
            self._pressed_modifiers = event.modifiers()
        else:
            self._pressed_button = Qt.MouseButton.NoButton
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pressed_button = self._pressed_button
        pressed_modifiers = self._pressed_modifiers
        self._pressed_button = Qt.MouseButton.NoButton
        self._pressed_modifiers = Qt.KeyboardModifier.NoModifier
        inside = self.rect().contains(event.position().toPoint())
        super().mouseReleaseEvent(event)
        if (
            inside
            and pressed_button == event.button()
            and event.button() in {
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.MiddleButton,
                Qt.MouseButton.RightButton,
            }
        ):
            self.activated.emit(self.name, pressed_modifiers, event.button())

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in {
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        }:
            self.activated.emit(
                self.name,
                event.modifiers(),
                Qt.MouseButton.LeftButton,
            )
            event.accept()
            return
        super().keyPressEvent(event)


class BrowserTagQuickFilterStrip(QWidget):
    """Show registered tags as compact buttons with a complete overflow menu.

    Registered tags follow the management order from left to right. When the
    menu-bar corner has less room, later entries move to the overflow menu;
    every registered tag remains reachable there.
    """

    activated = Signal(str, object, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._registry: list[dict[str, str]] = []
        self._buttons: dict[str, _TagQuickFilterButton] = {}
        self._state = BrowserFilterState()
        self._available_width = 0
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._overflow = QToolButton(self)
        self._overflow.setText("…")
        self._overflow.setToolTip(tr("登録タグをすべて表示"))
        self._overflow.setAutoRaise(True)
        self._overflow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._overflow.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Preferred,
        )
        self._overflow_menu = QMenu(self._overflow)
        self._overflow.setMenu(self._overflow_menu)
        self._layout.addWidget(self._overflow)
        self._overflow.hide()
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect)

    @property
    def registry(self) -> tuple[dict[str, str], ...]:
        return tuple(dict(entry) for entry in self._registry)

    def set_registry(self, registry: object) -> None:
        normalized = normalize_tag_registry(registry)
        if normalized == self._registry:
            self._sync_buttons()
            self._reflow()
            return
        for button in self._buttons.values():
            self._layout.removeWidget(button)
            button.deleteLater()
        self._buttons.clear()
        self._registry = normalized
        for entry in normalized:
            button = _TagQuickFilterButton(entry["name"], self)
            button.activated.connect(self.activated)
            button.setText(entry["name"])
            button.setToolTip(tr("クリックでタグ絞り込み: {p0}", p0=entry["name"]))
            self._buttons[entry["name"]] = button
        self._sync_buttons()
        self._reflow()

    def set_filter_state(self, state: BrowserFilterState) -> None:
        self._state = state
        self._sync_buttons()
        self._sync_overflow_menu()

    def set_available_width(self, width: int) -> None:
        self._available_width = max(0, int(width))
        total = self._full_width()
        actual = min(total, self._available_width) if self._available_width else 0
        if actual < self._overflow_width() and total > self._overflow_width():
            actual = 0
        self.setFixedWidth(actual)
        self._reflow()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._full_width(), super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().minimumSizeHint().height())

    def _full_width(self) -> int:
        widths = [button.sizeHint().width() for button in self._buttons.values()]
        if not widths:
            return 0
        return sum(widths) + 2 * (len(widths) - 1)

    def _overflow_width(self) -> int:
        return self._overflow.sizeHint().width() + 2

    def _reflow(self) -> None:
        if not self._buttons:
            self.hide()
            self._overflow.hide()
            return
        self.show()
        available = self.width()
        if available <= 0:
            for button in self._buttons.values():
                button.hide()
            self._overflow.hide()
            self._sync_overflow_menu()
            return

        names = [entry["name"] for entry in self._registry]
        visible: list[str] = []
        used = 0
        overflow_width = self._overflow_width()
        # Keep the management order visible from left to right.
        for name in names:
            width = self._buttons[name].sizeHint().width()
            proposed = used + width + (2 if visible else 0)
            remaining = len(names) - len(visible) - 1
            needs_overflow = remaining > 0
            reserve = overflow_width + (2 if proposed else 0) if needs_overflow else 0
            if proposed + reserve > available:
                break
            visible.append(name)
            used = proposed
        hidden = names[len(visible):]
        if hidden and not visible and available < overflow_width:
            visible = []
        for button in self._buttons.values():
            button.hide()
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._overflow:
                widget.hide()
        if hidden:
            self._overflow.show()
        else:
            self._overflow.hide()
        for name in visible:
            button = self._buttons[name]
            self._layout.addWidget(button)
            button.show()
        if hidden:
            self._layout.addWidget(self._overflow)
        self._sync_overflow_menu(hidden)

    def _sync_buttons(self) -> None:
        include = set(self._state.include_tags)
        exclude = set(self._state.exclude_tags)
        for entry in self._registry:
            name = entry["name"]
            button = self._buttons.get(name)
            if button is None:
                continue
            is_include = name in include
            is_exclude = name in exclude
            button.setChecked(is_include)
            state = "include" if is_include else "exclude" if is_exclude else "none"
            button.setProperty("quickTagState", state)
            color = QColor(entry["color"])
            foreground = "#000000" if color.lightness() > 150 else "#ffffff"
            background = color.name() if is_include else color.lighter(185).name()
            border = color.name()
            button.setStyleSheet(
                "QToolButton { padding: 1px 5px; border: 1px solid "
                f"{border}; border-radius: 2px; }}"
                "QToolButton:checked { background-color: "
                f"{background}; color: {foreground}; }}"
                "QToolButton[quickTagState=\"exclude\"] { border: 2px dashed "
                f"{border}; }}"
            )
            if is_exclude:
                button.setToolTip(tr("タグを除外中: {p0}", p0=name))
            else:
                button.setToolTip(tr("クリックでタグ絞り込み: {p0}", p0=name))
        self._sync_overflow_menu()

    def _sync_overflow_menu(self, hidden: Iterable[str] | None = None) -> None:
        if hidden is None:
            hidden = self._registry_names_not_visible()
        hidden_names = tuple(hidden)
        self._overflow_menu.clear()
        include = set(self._state.include_tags)
        exclude = set(self._state.exclude_tags)
        entries = {entry["name"]: entry for entry in self._registry}
        for name in hidden_names:
            action = self._overflow_menu.addAction(name.replace("&", "&&"))
            action.setCheckable(True)
            action.setChecked(name in include)
            entry = entries[name]
            pixmap = QPixmap(10, 10)
            pixmap.fill(QColor(entry["color"]))
            action.setIcon(QIcon(pixmap))
            action.setToolTip(
                tr("タグを除外中: {p0}", p0=name)
                if name in exclude
                else tr("クリックでタグ絞り込み: {p0}", p0=name)
            )
            action.triggered.connect(
                lambda _checked=False, tag=name: self._activate_overflow_tag(tag)
            )

    def _activate_overflow_tag(self, name: str) -> None:
        self.activated.emit(
            name,
            QApplication.keyboardModifiers(),
            Qt.MouseButton.LeftButton,
        )

    def _registry_names_not_visible(self) -> tuple[str, ...]:
        visible = {
            name for name, button in self._buttons.items() if button.isVisible()
        }
        return tuple(
            entry["name"] for entry in self._registry if entry["name"] not in visible
        )
