"""Draft-only tag dialogs; physical changes belong to Browser's Apply path."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .browser_tags import (
    filename_tags,
    new_tag_entry,
    normalize_tag_registry,
    registry_by_token,
    valid_tag_display_name,
    valid_tag_token,
)
from .i18n import tr


def next_tag_state(state, initial):
    return (
        Qt.CheckState.Checked
        if state == Qt.CheckState.Unchecked
        else Qt.CheckState.Unchecked
        if state == Qt.CheckState.PartiallyChecked
        else initial
        if initial == Qt.CheckState.PartiallyChecked
        else Qt.CheckState.Unchecked
    )


class TagCheckBox(QCheckBox):
    """ZipPlaFork toggle-check adaptation for mixed selections.

    Based on Program.PrefixEscapedToolStripMenuItem.ToggleCheck at revision
    07955f5267e2fb92d6fc6e40fde2507d8fb07b3b (AGPL-3.0-or-later).
    """

    def __init__(self, text, state, parent=None):
        super().__init__(text, parent)
        self.initial_state = state
        self.setTristate(state == Qt.CheckState.PartiallyChecked)
        self.setCheckState(state)

    def nextCheckState(self):
        self.setCheckState(
            next_tag_state(self.checkState(), self.initial_state)
        )


class TagSelectionMenu(QMenu):
    """Draft multiple tag changes; close to apply, Escape to cancel.

    The draft/mixed-selection basis remains derived from ZipPlaFork
    Program.SetTagsToToolStripMenuItems/CatalogForm at revision
    07955f5267e2fb92d6fc6e40fde2507d8fb07b3b (AGPL-3.0-or-later).
    Keeping ordinary left-click toggles open is a NivisViewer extension.
    """

    def __init__(self, paths, registry, root):
        super().__init__(tr("タグ"), root)
        self.root = root
        root.aboutToHide.connect(self.hide)
        self.cancelled = False
        self.initial, self.states, self.tag_actions = {}, {}, {}
        self._other_actions = {}

        membership = [set(filename_tags(str(path))) for path in paths]
        normalized = normalize_tag_registry(registry)
        registered_tokens = set(registry_by_token(normalized))
        self.unknown = sorted(
            (set().union(*membership) if membership else set())
            - registered_tokens
        )
        self.registered = {
            str(entry["write_token"]): entry
            for entry in normalized
        }

        self.clear_action = self.addAction(tr("すべて外す"))
        self.remove_unknown_action = self.addAction(
            tr("ファイル内の未登録タグをすべて外す")
        )
        self.remove_unknown_action.setEnabled(bool(self.unknown))
        self.addSeparator()

        for key, entry in self.registered.items():
            tokens = tuple(str(token) for token in entry["tokens"])
            count = sum(
                any(token in tags for token in tokens)
                for tags in membership
            )
            self._add_toggle_action(
                key,
                str(entry["display_name"]),
                count,
                len(membership),
                color=str(entry["color"]),
            )

        for token in self.unknown:
            count = sum(token in tags for tags in membership)
            self._add_toggle_action(
                token,
                tr("{p0}（未登録）", p0=token),
                count,
                len(membership),
            )

        self.addSeparator()
        self.editor_action = self.addAction(tr("タグの管理"))
        self.import_action = self.addAction(
            tr("ファイル名の未登録タグを登録…")
        )
        self.import_action.setEnabled(bool(self.unknown))
        self.setToolTipsVisible(True)
        root.installEventFilter(self)
        self._refresh()

    def _add_toggle_action(
        self,
        key: str,
        label: str,
        count: int,
        total: int,
        *,
        color: str | None = None,
    ) -> None:
        state = (
            Qt.CheckState.Unchecked
            if not count
            else Qt.CheckState.Checked
            if count == total
            else Qt.CheckState.PartiallyChecked
        )
        action = self.addAction(label)
        action.setCheckable(True)
        action.setData(key)
        if color:
            swatch = QPixmap(12, 12)
            swatch.fill(QColor(color))
            action.setIcon(QIcon(swatch))
        self.tag_actions[action] = key
        self.initial[key] = self.states[key] = state

    def changes(self):
        if self.cancelled:
            return {}
        return {
            key: (
                True
                if state == Qt.CheckState.Checked
                else False
                if state == Qt.CheckState.Unchecked
                else None
            )
            for key, state in self.states.items()
            if state != self.initial[key]
        }

    def _display_label(self, key: str) -> str:
        entry = self.registered.get(key)
        if entry is not None:
            return str(entry["display_name"])
        return tr("{p0}（未登録）", p0=key)

    def _refresh(self):
        for action, key in self.tag_actions.items():
            state = self.states[key]
            label = self._display_label(key)
            action.setText(
                ("− " if state == Qt.CheckState.PartiallyChecked else "")
                + label.replace("&", "&&")
            )
            action.setChecked(state == Qt.CheckState.Checked)
            action.setToolTip(
                tr(
                    "複数のタグを続けて変更できます。"
                    "閉じると適用、Escで取り消します。"
                )
            )

        self.clear_action.setEnabled(
            any(
                state != Qt.CheckState.Unchecked
                for state in self.states.values()
            )
        )
        if self.changes():
            for action in self.root.actions():
                if action.menu() is not self and not action.isSeparator():
                    self._other_actions.setdefault(
                        action,
                        action.isEnabled(),
                    )
                    action.setEnabled(False)
            self.editor_action.setEnabled(False)
            self.import_action.setEnabled(False)
        else:
            for action, enabled in self._other_actions.items():
                action.setEnabled(enabled)
            self.editor_action.setEnabled(True)
            self.import_action.setEnabled(bool(self.unknown))

    def _toggle(self, action):
        if action is None or not action.isEnabled():
            return False
        if action == self.clear_action:
            self.states = {
                key: Qt.CheckState.Unchecked
                for key in self.states
            }
        elif action == self.remove_unknown_action:
            for token in self.unknown:
                self.states[token] = Qt.CheckState.Unchecked
        elif action in self.tag_actions:
            key = self.tag_actions[action]
            self.states[key] = next_tag_state(
                self.states[key],
                self.initial[key],
            )
        else:
            return False
        self._refresh()
        return True

    def mouseReleaseEvent(self, event):
        if event.button() in {
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.RightButton,
        }:
            if self._toggle(
                self.actionAt(event.position().toPoint())
            ):
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled = True
            self.root.close()
            event.accept()
            return
        if event.key() in {
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        }:
            if self._toggle(self.activeAction()):
                event.accept()
                return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if (
            watched is self.root
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self.cancelled = True
        return super().eventFilter(watched, event)


class TagTokenEditorDialog(QDialog):
    """Edit physical strings without renaming files."""

    def __init__(
        self,
        entry,
        *,
        reserved_tokens=(),
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("タグ文字列の管理"))
        self.resize(500, 420)
        self._entry = deepcopy(entry)
        self._tokens = [str(token) for token in self._entry["tokens"]]
        self._write_token = str(self._entry["write_token"])
        self._reserved = {
            str(token)
            for token in reserved_tokens
        }

        layout = QVBoxLayout(self)
        note = QLabel(
            tr(
                "表示名の変更ではファイル名は変わりません。"
                "ここで文字列を削除すると、その文字列が残るファイルは"
                "未登録タグになります。物理変換は未登録タグ画面から行えます。"
            ),
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.list = QListWidget(self)
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        layout.addWidget(self.list)

        buttons = QHBoxLayout()
        self.add_button = QPushButton(tr("文字列を追加"), self)
        self.remove_button = QPushButton(tr("削除"), self)
        self.standard_button = QPushButton(
            tr("標準文字列にする"),
            self,
        )
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.standard_button)
        layout.addLayout(buttons)

        self.error_label = QLabel(self)
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.add_button.clicked.connect(self._add_token)
        self.remove_button.clicked.connect(self._remove_token)
        self.standard_button.clicked.connect(self._make_standard)
        self._refresh()

    def _refresh(self):
        current = self.list.currentRow()
        self.list.clear()
        for token in self._tokens:
            label = (
                tr("{p0}  （標準）", p0=token)
                if token == self._write_token
                else token
            )
            self.list.addItem(label)
        if self._tokens:
            self.list.setCurrentRow(
                min(max(0, current), len(self._tokens) - 1)
            )

    def _add_token(self):
        token, accepted = QInputDialog.getText(
            self,
            tr("タグ文字列を追加"),
            tr("ファイル名で使用するタグ文字列:"),
        )
        token = token.strip()
        if not accepted:
            return
        if not valid_tag_token(token):
            self.error_label.setText(
                tr("タグ文字列が空、または使用できない文字を含みます。")
            )
            return
        if token in self._reserved:
            self.error_label.setText(
                tr("その文字列は別の登録タグで使用されています。")
            )
            return
        if token in self._tokens:
            self.error_label.setText(
                tr("その文字列はこのタグに既に登録されています。")
            )
            return
        self.error_label.clear()
        self._tokens.append(token)
        self._refresh()
        self.list.setCurrentRow(len(self._tokens) - 1)

    def _remove_token(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self._tokens):
            return
        if len(self._tokens) <= 1:
            self.error_label.setText(
                tr("登録タグには最低1つのタグ文字列が必要です。")
            )
            return
        token = self._tokens[row]
        answer = QMessageBox.question(
            self,
            tr("タグ文字列を削除"),
            tr(
                "「{p0}」が残っているファイルは未登録タグになります。"
                "削除しますか？",
                p0=token,
            ),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._tokens.pop(row)
        if token == self._write_token:
            self._write_token = self._tokens[0]
        self.error_label.clear()
        self._refresh()

    def _make_standard(self):
        row = self.list.currentRow()
        if 0 <= row < len(self._tokens):
            self._write_token = self._tokens[row]
            self._refresh()

    def entry(self):
        result = deepcopy(self._entry)
        result["write_token"] = self._write_token
        result["tokens"] = list(self._tokens)
        return result


class UnmanagedTagDialog(QDialog):
    def __init__(self, counts, registry, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("フォルダ内の未登録タグ"))
        self.resize(520, 260)
        self.registry = normalize_tag_registry(registry)

        layout = QVBoxLayout(self)
        note = QLabel(
            tr(
                "現在のフォルダで見つかった未登録タグを"
                "絞り込み・登録・統合・削除できます。"
            ),
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.unknown_combo = QComboBox(self)
        for token, count in counts.items():
            self.unknown_combo.addItem(
                tr("{p0}  ({p1}件)", p0=token, p1=count),
                token,
            )
        layout.addWidget(self.unknown_combo)

        self.operation_combo = QComboBox(self)
        self.operation_combo.addItem(
            tr("この未登録タグで絞り込み"),
            "filter",
        )
        self.operation_combo.addItem(
            tr("新しい登録タグとして追加"),
            "register",
        )
        if self.registry:
            self.operation_combo.addItem(
                tr("登録タグの対応文字列として追加（ファイル名変更なし）"),
                "attach",
            )
            self.operation_combo.addItem(
                tr("登録タグの標準文字列へ変換（ファイル名を変更）"),
                "convert",
            )
        self.operation_combo.addItem(
            tr("ファイルからこの未登録タグを削除"),
            "delete",
        )
        layout.addWidget(self.operation_combo)

        self.target_combo = QComboBox(self)
        for entry in self.registry:
            self.target_combo.addItem(
                str(entry["display_name"]),
                str(entry["id"]),
            )
        layout.addWidget(self.target_combo)

        self.warning = QLabel(self)
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.operation_combo.currentIndexChanged.connect(
            self._sync_operation
        )
        self._sync_operation()

    def _sync_operation(self):
        kind = self.operation_combo.currentData()
        self.target_combo.setEnabled(kind in {"attach", "convert"})
        if kind == "convert":
            self.warning.setText(
                tr(
                    "対象ファイル名を書き換えます。"
                    "同名衝突や使用中ファイルは既存の安全処理で停止します。"
                )
            )
        elif kind == "delete":
            self.warning.setText(
                tr("対象ファイル名からこのタグ文字列を削除します。")
            )
        elif kind == "attach":
            self.warning.setText(
                tr(
                    "ファイル名は変更せず、同じ論理タグとして認識します。"
                )
            )
        else:
            self.warning.clear()

    def operation(self) -> dict[str, object]:
        return {
            "kind": str(self.operation_combo.currentData()),
            "token": str(self.unknown_combo.currentData() or ""),
            "target_id": str(self.target_combo.currentData() or ""),
        }


class TagManagerDialog(QDialog):
    def __init__(
        self,
        registry,
        parent=None,
        *,
        unmanaged_counts=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("タグの管理"))
        self.resize(720, 460)
        self.unmanaged_operation = None
        self._unmanaged_counts = dict(unmanaged_counts or {})

        layout = QVBoxLayout(self)
        explanation = QLabel(
            tr(
                "表示名は自由に変更でき、ファイル名のタグ文字列は変わりません。"
                "実タグ文字列の変更・統合は専用操作で行います。"
            ),
            self,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(
            [
                tr("表示名"),
                tr("標準タグ文字列"),
                tr("対応文字列"),
                tr("色"),
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        layout.addWidget(self.table)
        self._populate(normalize_tag_registry(registry))

        actions = QHBoxLayout()
        for text, callback in (
            (tr("追加"), self._add_tag),
            (tr("削除"), self.remove_row),
            (tr("上へ"), lambda: self.move_row(-1)),
            (tr("下へ"), lambda: self.move_row(1)),
        ):
            button = QPushButton(text, self)
            button.clicked.connect(callback)
            actions.addWidget(button)

        self.unmanaged_button = QPushButton(
            tr("フォルダ内の未登録タグ…"),
            self,
        )
        self.unmanaged_button.setEnabled(bool(self._unmanaged_counts))
        self.unmanaged_button.clicked.connect(self._open_unmanaged)
        actions.addWidget(self.unmanaged_button)
        layout.addLayout(actions)

        self.error_label = QLabel(self)
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _populate(self, entries):
        self.table.setRowCount(0)
        for entry in entries:
            self.add_row(entry)

    def add_row(
        self,
        entry_or_name="",
        color="#80bfff",
        *,
        edit=False,
    ):
        if isinstance(entry_or_name, dict):
            entry = deepcopy(entry_or_name)
        else:
            name = str(entry_or_name)
            if name and valid_tag_token(name):
                entry = new_tag_entry(
                    name,
                    display_name=name,
                    color=color,
                )
            else:
                entry = {
                    "id": f"draft-{self.table.rowCount()}",
                    "name": name,
                    "display_name": name,
                    "write_token": "",
                    "tokens": [],
                    "color": color,
                }

        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(str(entry["display_name"]))
        name_item.setData(Qt.ItemDataRole.UserRole, deepcopy(entry))
        self.table.setItem(row, 0, name_item)

        token_item = QTableWidgetItem(str(entry["write_token"]))
        token_item.setFlags(
            token_item.flags() & ~Qt.ItemFlag.ItemIsEditable
        )
        self.table.setItem(row, 1, token_item)

        token_button = QPushButton(
            tr("{p0}件…", p0=len(entry["tokens"])),
            self.table,
        )
        token_button.clicked.connect(
            lambda _checked=False, item=name_item: self._edit_tokens(item)
        )
        self.table.setCellWidget(row, 2, token_button)

        color_button = QPushButton(str(entry["color"]), self.table)
        self.set_button_color(color_button, str(entry["color"]))
        color_button.clicked.connect(
            lambda _checked=False, button=color_button: self.choose_color(button)
        )
        self.table.setCellWidget(row, 3, color_button)
        self.table.setCurrentCell(row, 0)
        if edit:
            self.table.editItem(name_item)

    def _add_tag(self):
        token, accepted = QInputDialog.getText(
            self,
            tr("タグを追加"),
            tr("新しいタグの標準文字列:"),
        )
        token = token.strip()
        if not accepted:
            return
        if not valid_tag_token(token):
            self.error_label.setText(
                tr("タグ文字列が空、または使用できない文字を含みます。")
            )
            return
        if token in {
            str(value)
            for entry in self.registry()
            for value in entry["tokens"]
        }:
            self.error_label.setText(
                tr("そのタグ文字列は既に使用されています。")
            )
            return
        self.error_label.clear()
        self.add_row(new_tag_entry(token))
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)
        self.table.editItem(self.table.currentItem())

    def _entry_for_row(self, row):
        item = self.table.item(row, 0)
        entry = deepcopy(item.data(Qt.ItemDataRole.UserRole))
        display = item.text().strip()
        entry["display_name"] = display
        entry["name"] = display
        entry["color"] = self.table.cellWidget(row, 3).text()
        if not entry.get("tokens") and valid_tag_token(display):
            entry["tokens"] = [display]
            entry["write_token"] = display
        return entry

    def _edit_tokens(self, item):
        row = self.table.row(item)
        entry = self._entry_for_row(row)
        if not entry.get("tokens"):
            self.error_label.setText(
                tr("先に表示名へ有効な初期タグ文字列を入力してください。")
            )
            return
        reserved = {
            str(token)
            for index in range(self.table.rowCount())
            if index != row
            for token in self._entry_for_row(index)["tokens"]
        }
        dialog = TagTokenEditorDialog(
            entry,
            reserved_tokens=reserved,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.entry()
        item.setData(Qt.ItemDataRole.UserRole, updated)
        self.table.item(row, 1).setText(str(updated["write_token"]))
        self.table.cellWidget(row, 2).setText(
            tr("{p0}件…", p0=len(updated["tokens"]))
        )

    def choose_color(self, button):
        color = QColorDialog.getColor(
            QColor(button.text()),
            self,
            tr("色"),
        )
        if color.isValid():
            self.set_button_color(button, color.name())

    @staticmethod
    def set_button_color(button, value):
        color = QColor(value)
        foreground = "#000000" if color.lightness() > 150 else "#ffffff"
        button.setText(color.name())
        button.setStyleSheet(
            f"background-color: {color.name()}; color: {foreground};"
        )

    def remove_row(self):
        if self.table.currentRow() >= 0:
            self.table.removeRow(self.table.currentRow())

    def move_row(self, offset):
        row = self.table.currentRow()
        target = row + offset
        if not (
            0 <= row < self.table.rowCount()
            and 0 <= target < self.table.rowCount()
        ):
            return
        entries = self.registry()
        entries[row], entries[target] = entries[target], entries[row]
        self._populate(entries)
        self.table.setCurrentCell(target, 0)

    def registry(self):
        return [
            self._entry_for_row(row)
            for row in range(self.table.rowCount())
        ]

    def _validate_registry(self) -> bool:
        values = self.registry()
        displays = [str(entry["display_name"]) for entry in values]
        if any(not valid_tag_display_name(name) for name in displays):
            self.error_label.setText(
                tr("表示名が空、長すぎる、または制御文字を含みます。")
            )
            return False
        if len({name.casefold() for name in displays}) != len(displays):
            self.error_label.setText(
                tr("同じ表示名の登録タグが重複しています。")
            )
            return False

        if any(not entry.get("tokens") for entry in values):
            self.error_label.setText(
                tr("各登録タグには最低1つのタグ文字列が必要です。")
            )
            return False
        tokens = [
            str(token)
            for entry in values
            for token in entry["tokens"]
        ]
        if (
            any(not valid_tag_token(token) for token in tokens)
            or len(set(tokens)) != len(tokens)
            or any(
                str(entry.get("write_token", "")) not in entry["tokens"]
                for entry in values
            )
        ):
            self.error_label.setText(
                tr("タグ文字列が不正、または別の登録タグと競合しています。")
            )
            return False
        self.error_label.clear()
        return True

    def _open_unmanaged(self):
        if not self._validate_registry():
            return
        dialog = UnmanagedTagDialog(
            self._unmanaged_counts,
            self.registry(),
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.unmanaged_operation = dialog.operation()
            super().accept()

    def accept(self):
        if self._validate_registry():
            super().accept()


class ItemTagsDialog(QDialog):
    def __init__(self, paths, registry, parent=None):
        super().__init__(parent)
        self._initial_focus_pending = True
        self.setWindowTitle(tr("選択項目のタグ"))
        self.resize(500, 420)

        layout = QVBoxLayout(self)
        note = QLabel(
            tr(
                "適用すると選択項目のファイル名が変わります。"
                "チェックは全項目に付ける、空欄は全項目から外す、"
                "－は混在した状態を保ちます。"
            ),
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        membership = [set(filename_tags(str(path))) for path in paths]
        normalized = normalize_tag_registry(registry)
        registered_tokens = set(registry_by_token(normalized))

        rows = []
        for entry in normalized:
            rows.append(
                (
                    str(entry["write_token"]),
                    str(entry["display_name"]),
                    tuple(str(token) for token in entry["tokens"]),
                )
            )
        unknown = sorted(
            (set().union(*membership) if membership else set())
            - registered_tokens
        )
        rows.extend(
            (
                token,
                tr("{p0}（未登録）", p0=token),
                (token,),
            )
            for token in unknown
        )

        self.table = QTableWidget(len(rows), 1, self)
        self.table.setHorizontalHeaderLabels([tr("タグ")])
        self.table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )
        self.checks = {}

        for row, (key, label, tokens) in enumerate(rows):
            count = sum(
                any(token in tags for token in tokens)
                for tags in membership
            )
            state = (
                Qt.CheckState.Checked
                if membership and count == len(membership)
                else Qt.CheckState.Unchecked
                if count == 0
                else Qt.CheckState.PartiallyChecked
            )
            check = TagCheckBox(label, state, self.table)
            self.table.setCellWidget(row, 0, check)
            self.checks[key] = check

        layout.addWidget(self.table)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        apply_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Apply
        )
        cancel_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )
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
        return {
            name: (
                True
                if check.checkState() == Qt.CheckState.Checked
                else False
                if check.checkState() == Qt.CheckState.Unchecked
                else None
            )
            for name, check in self.checks.items()
        }


class TagFilterDialog(QDialog):
    def __init__(self, state, registry, parent=None):
        super().__init__(parent)
        self._initial_focus_pending = True
        self.setWindowTitle(tr("タグで絞り込み"))
        self.resize(500, 400)

        layout = QVBoxLayout(self)
        self.match_combo = QComboBox(self)
        self.match_combo.addItem(tr("含むタグすべてに一致（AND）"), "all")
        self.match_combo.addItem(tr("含むタグのいずれかに一致（OR）"), "any")
        self.match_combo.setCurrentIndex(
            self.match_combo.findData(state.tag_match)
        )
        layout.addWidget(self.match_combo)

        normalized = normalize_tag_registry(registry)
        rows = [
            (
                str(entry["write_token"]),
                str(entry["display_name"]),
            )
            for entry in normalized
        ]
        known = {key for key, _label in rows}
        for key in tuple(state.include_tags) + tuple(state.exclude_tags):
            if key not in known:
                rows.append((key, tr("{p0}（未登録）", p0=key)))
                known.add(key)

        self.table = QTableWidget(len(rows), 2, self)
        self.table.setHorizontalHeaderLabels([tr("タグ"), tr("条件")])
        self.table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )
        self.controls = {}

        for row, (key, label) in enumerate(rows):
            item = QTableWidgetItem(label)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item)
            combo = QComboBox(self.table)
            for text, data in (
                (tr("指定なし"), ""),
                (tr("含む"), "include"),
                (tr("除外"), "exclude"),
            ):
                combo.addItem(text, data)
            combo.setCurrentIndex(
                2
                if key in state.exclude_tags
                else 1
                if key in state.include_tags
                else 0
            )
            self.table.setCellWidget(row, 1, combo)
            self.controls[key] = combo

        layout.addWidget(self.table)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        apply_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Apply
        )
        cancel_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )
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
        return dict(
            include_tags=tuple(
                name
                for name, control in self.controls.items()
                if control.currentData() == "include"
            ),
            exclude_tags=tuple(
                name
                for name, control in self.controls.items()
                if control.currentData() == "exclude"
            ),
            tag_match=self.match_combo.currentData(),
        )
