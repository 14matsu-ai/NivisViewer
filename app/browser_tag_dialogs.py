"""Draft-only tag dialogs; physical changes belong to Browser's Apply path."""
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QMenu,
)

from .browser_tags import filename_tags, normalize_tag_registry, valid_tag_name
from .i18n import tr


def next_tag_state(state, initial):
    return (Qt.CheckState.Checked if state == Qt.CheckState.Unchecked else
            Qt.CheckState.Unchecked if state == Qt.CheckState.PartiallyChecked else
            initial if initial == Qt.CheckState.PartiallyChecked else Qt.CheckState.Unchecked)


class TagCheckBox(QCheckBox):
    """ZipPlaFork Program.PrefixEscapedToolStripMenuItem.ToggleCheck adaptation.

    AGPL-3.0-or-later, fixed 07955f5267e2fb92d6fc6e40fde2507d8fb07b3b.
    Only an initially mixed selection can return to the preserve state.
    """
    def __init__(self, text, state, parent=None):
        super().__init__(text, parent)
        self.initial_state = state
        self.setTristate(state == Qt.CheckState.PartiallyChecked)
        self.setCheckState(state)

    def nextCheckState(self):
        self.setCheckState(next_tag_state(self.checkState(), self.initial_state))


class TagSelectionMenu(QMenu):
    """ZipPla-style draft toggles: left closes, right stays, Escape discards.

    Adapted from Program.SetTagsToToolStripMenuItems and CatalogForm context
    menu handling, AGPL-3.0-or-later at 07955f5267e2fb92d6fc6e40fde2507d8fb07b3b.
    Physical rename remains the caller's responsibility after root exec returns.
    """
    def __init__(self, paths, registry, root):
        super().__init__(tr('タグ'), root)
        self.root = root
        root.aboutToHide.connect(self.hide)
        self.cancelled = False
        self.initial, self.states, self.tag_actions = {}, {}, {}
        self._other_actions = {}
        membership = [set(filename_tags(str(path))) for path in paths]
        registered = {tag['name']: tag for tag in registry}
        self.unknown = sorted((set().union(*membership) if membership else set()) - registered.keys())
        self.clear_action = self.addAction(tr('すべて外す'))
        self.addSeparator()
        for name in list(registered) + self.unknown:
            count = sum(name in tags for tags in membership)
            state = (Qt.CheckState.Unchecked if not count else Qt.CheckState.Checked
                     if count == len(membership) else Qt.CheckState.PartiallyChecked)
            action = self.addAction(name)
            action.setCheckable(True)
            action.setData(name)
            if name in registered:
                swatch = QPixmap(12, 12)
                swatch.fill(QColor(registered[name]['color']))
                action.setIcon(QIcon(swatch))
            self.tag_actions[action] = name
            self.initial[name] = self.states[name] = state
        self.addSeparator()
        self.editor_action = self.addAction(tr('タグの管理'))
        self.import_action = self.addAction(tr('ファイル名の未登録タグを登録…'))
        self.import_action.setEnabled(bool(self.unknown))
        self.setToolTipsVisible(True)
        root.installEventFilter(self)
        self._refresh()

    def changes(self):
        if self.cancelled:
            return {}
        return {name: True if state == Qt.CheckState.Checked else False
                if state == Qt.CheckState.Unchecked else None
                for name, state in self.states.items() if state != self.initial[name]}

    def _refresh(self):
        for action, name in self.tag_actions.items():
            state = self.states[name]
            label = tr('{p0}（未登録）', p0=name) if name in self.unknown else name
            action.setText(('− ' if state == Qt.CheckState.PartiallyChecked else '') + label.replace('&', '&&'))
            action.setChecked(state == Qt.CheckState.Checked)
            action.setToolTip(tr('チェックは全項目に付ける、空欄は外す、－は混在を保ちます。右クリックは開いたまま変更、閉じると適用、Escで取り消します。'))
        self.clear_action.setEnabled(any(s != Qt.CheckState.Unchecked for s in self.states.values()))
        # Pending renames cannot be combined with a stale-path file operation.
        if self.changes():
            for action in self.root.actions():
                if action.menu() is not self and not action.isSeparator():
                    self._other_actions.setdefault(action, action.isEnabled())
                    action.setEnabled(False)
            self.editor_action.setEnabled(False)
            self.import_action.setEnabled(False)
        else:
            for action, enabled in self._other_actions.items():
                action.setEnabled(enabled)
            self.editor_action.setEnabled(True)
            self.import_action.setEnabled(bool(self.unknown))

    def _toggle(self, action, keep_open):
        if action is None or not action.isEnabled():
            return False
        if action == self.clear_action:
            self.states = {name: Qt.CheckState.Unchecked for name in self.states}
        elif action in self.tag_actions:
            name = self.tag_actions[action]
            self.states[name] = next_tag_state(self.states[name], self.initial[name])
        else:
            return False
        self._refresh()
        if not keep_open:
            self.root.close()
        return True

    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            if self._toggle(self.actionAt(event.position().toPoint()), event.button() == Qt.MouseButton.RightButton):
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled = True
            self.root.close()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._toggle(self.activeAction(), False):
                event.accept()
                return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.root and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.cancelled = True
        return super().eventFilter(watched, event)


class TagManagerDialog(QDialog):
    def __init__(self, registry, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('タグの管理'))
        self.resize(540, 420)
        layout = QVBoxLayout(self)
        explanation = QLabel(tr('登録名の変更・削除ではファイルのタグは変わりません。同じ名前を登録すると、既存の一致するタグも表示されます。'), self)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels([tr('タグ名'), tr('色')])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.table)
        for entry in normalize_tag_registry(registry):
            self.add_row(entry['name'], entry['color'], edit=False)
        actions = QHBoxLayout()
        for text, callback in [(tr('追加'), lambda: self.add_row(edit=True)), (tr('削除'), self.remove_row),
                               (tr('上へ'), lambda: self.move_row(-1)), (tr('下へ'), lambda: self.move_row(1))]:
            button = QPushButton(text, self)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.error_label = QLabel(self)
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=self)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def add_row(self, name='', color='#80bfff', *, edit=False):
        row = self.table.rowCount()
        self.table.insertRow(row)
        name_item = QTableWidgetItem(name)
        self.table.setItem(row, 0, name_item)
        button = QPushButton(color, self.table)
        self.set_button_color(button, color)
        button.clicked.connect(lambda: self.choose_color(button))
        self.table.setCellWidget(row, 1, button)
        self.table.setCurrentCell(row, 0)
        if edit:
            self.table.editItem(name_item)

    def choose_color(self, button):
        color = QColorDialog.getColor(QColor(button.text()), self, tr('色'))
        if color.isValid():
            self.set_button_color(button, color.name())

    @staticmethod
    def set_button_color(button, value):
        color = QColor(value)
        foreground = '#000000' if color.lightness() > 150 else '#ffffff'
        button.setText(color.name())
        button.setStyleSheet(f'background-color: {color.name()}; color: {foreground};')

    def remove_row(self):
        if self.table.currentRow() >= 0:
            self.table.removeRow(self.table.currentRow())

    def move_row(self, offset):
        row, target = self.table.currentRow(), self.table.currentRow() + offset
        if 0 <= row < self.table.rowCount() and 0 <= target < self.table.rowCount():
            first, second = self.table.item(row, 0).text(), self.table.item(target, 0).text()
            color1, color2 = self.table.cellWidget(row, 1).text(), self.table.cellWidget(target, 1).text()
            self.table.item(row, 0).setText(second)
            self.table.item(target, 0).setText(first)
            self.set_button_color(self.table.cellWidget(row, 1), color2)
            self.set_button_color(self.table.cellWidget(target, 1), color1)
            self.table.setCurrentCell(target, 0)

    def registry(self):
        return [{'name': self.table.item(row, 0).text().strip(),
                 'color': self.table.cellWidget(row, 1).text()} for row in range(self.table.rowCount())]

    def accept(self):
        values = self.registry()
        if (any(not valid_tag_name(entry['name']) for entry in values)
                or len({entry['name'].casefold() for entry in values}) != len(values)):
            self.error_label.setText(tr('タグ名が空、使用できない文字を含む、または重複しています。'))
            return
        super().accept()


class ItemTagsDialog(QDialog):
    def __init__(self, paths, registry, parent=None):
        super().__init__(parent)
        self._initial_focus_pending = True
        self.setWindowTitle(tr('選択項目のタグ'))
        self.resize(500, 420)
        layout = QVBoxLayout(self)
        note = QLabel(tr('適用すると選択項目のファイル名が変わります。チェックは全項目に付ける、空欄は全項目から外す、－は混在した状態を保ちます。'), self)
        note.setWordWrap(True)
        layout.addWidget(note)
        membership = [set(filename_tags(str(path))) for path in paths]
        names = [entry['name'] for entry in registry]
        registered = set(names)
        names += sorted(set().union(*membership) - registered) if membership else []
        self.table = QTableWidget(len(names), 1, self)
        self.table.setHorizontalHeaderLabels([tr('タグ')])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.checks = {}
        for row, name in enumerate(names):
            label = name if name in registered else tr('{p0}（未登録）', p0=name)
            count = sum(name in tags for tags in membership)
            state = (Qt.CheckState.Checked if membership and count == len(membership) else
                     Qt.CheckState.Unchecked if count == 0 else Qt.CheckState.PartiallyChecked)
            check = TagCheckBox(label, state, self.table)
            self.table.setCellWidget(row, 0, check)
            self.checks[name] = check
        layout.addWidget(self.table)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel, parent=self)
        apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        button_layout = self.buttons.layout()
        button_layout.removeWidget(apply_button)
        button_layout.removeWidget(cancel_button)
        button_layout.addWidget(apply_button)
        button_layout.addWidget(cancel_button)
        apply_button.setDefault(True)
        cancel_button.setDefault(False)
        apply_button.clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def showEvent(self, event):
        super().showEvent(event)
        if self._initial_focus_pending:
            self._initial_focus_pending = False
            self.buttons.button(
                QDialogButtonBox.StandardButton.Apply
            ).setFocus(Qt.FocusReason.OtherFocusReason)

    def changes(self):
        return {name: True if check.checkState() == Qt.CheckState.Checked else
                False if check.checkState() == Qt.CheckState.Unchecked else None
                for name, check in self.checks.items()}


class TagFilterDialog(QDialog):
    def __init__(self, state, registry, parent=None):
        super().__init__(parent)
        self._initial_focus_pending = True
        self.setWindowTitle(tr('タグで絞り込み'))
        self.resize(500, 400)
        layout = QVBoxLayout(self)
        self.match_combo = QComboBox(self)
        self.match_combo.addItem(tr('含むタグすべてに一致（AND）'), 'all')
        self.match_combo.addItem(tr('含むタグのいずれかに一致（OR）'), 'any')
        self.match_combo.setCurrentIndex(self.match_combo.findData(state.tag_match))
        layout.addWidget(self.match_combo)
        names = list(dict.fromkeys([entry['name'] for entry in registry] + list(state.include_tags) + list(state.exclude_tags)))
        self.table = QTableWidget(len(names), 2, self)
        self.table.setHorizontalHeaderLabels([tr('タグ'), tr('条件')])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.controls = {}
        for row, name in enumerate(names):
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item)
            combo = QComboBox(self.table)
            for text, data in [(tr('指定なし'), ''), (tr('含む'), 'include'), (tr('除外'), 'exclude')]:
                combo.addItem(text, data)
            combo.setCurrentIndex(2 if name in state.exclude_tags else 1 if name in state.include_tags else 0)
            self.table.setCellWidget(row, 1, combo)
            self.controls[name] = combo
        layout.addWidget(self.table)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel, parent=self)
        apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        button_layout = self.buttons.layout()
        button_layout.removeWidget(apply_button)
        button_layout.removeWidget(cancel_button)
        button_layout.addWidget(apply_button)
        button_layout.addWidget(cancel_button)
        apply_button.setDefault(True)
        cancel_button.setDefault(False)
        apply_button.clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def showEvent(self, event):
        super().showEvent(event)
        if self._initial_focus_pending:
            self._initial_focus_pending = False
            self.buttons.button(
                QDialogButtonBox.StandardButton.Apply
            ).setFocus(Qt.FocusReason.OtherFocusReason)

    def filter_values(self):
        return dict(include_tags=tuple(name for name, c in self.controls.items() if c.currentData() == 'include'),
                    exclude_tags=tuple(name for name, c in self.controls.items() if c.currentData() == 'exclude'),
                    tag_match=self.match_combo.currentData())
