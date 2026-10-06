from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from .browser_sort import BROWSER_SORT_CHOICES, browser_sort_choice_index
from .folder_sort_rules import SCOPE_LABELS, folder_key, normalize_folder_sort_rules
from .i18n import tr


class FolderSortRuleDialog(QDialog):
    def __init__(self, rule, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("並び指定を編集"))
        self.resize(580, 220)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(str(rule.get("path", "")), self)
        browse = QPushButton(tr("参照…"), self)
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        form.addRow(tr("対象フォルダ:"), path_row)
        self.sort_combo = QComboBox(self)
        self.sort_combo.addItem(tr("普段の並び順を使う"), "default")
        for label, key, order in BROWSER_SORT_CHOICES:
            self.sort_combo.addItem(tr(label), f"{key}:{order}")
        self.sort_combo.setCurrentIndex(
            0 if rule.get("mode") == "default" else 1 + browser_sort_choice_index(
                rule.get("sort_key"), rule.get("sort_order"),
            )
        )
        form.addRow(tr("並び替え:"), self.sort_combo)
        self.scope_combo = QComboBox(self)
        for scope, label in SCOPE_LABELS.items():
            self.scope_combo.addItem(tr(label), scope)
        self.scope_combo.setCurrentIndex(max(0, self.scope_combo.findData(rule.get("scope", "subtree"))))
        form.addRow(tr("適用範囲:"), self.scope_combo)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, tr("対象フォルダを選択"), self.path_edit.text())
        if path:
            self.path_edit.setText(path)

    def rule(self):
        data = self.sort_combo.currentData()
        key, order = ("name", "ascending") if data == "default" else data.split(":")
        return {
            "path": self.path_edit.text(), "scope": self.scope_combo.currentData(),
            "mode": "default" if data == "default" else "specified",
            "sort_key": key, "sort_order": order,
        }

    def accept(self):
        if not normalize_folder_sort_rules([self.rule()]):
            QMessageBox.warning(self, tr("並び指定を編集"), tr("対象フォルダを絶対パスで指定してください。"))
            return
        super().accept()


class FolderSortDialog(QDialog):
    apply_requested = Signal(object)

    def __init__(self, rules, current_path: str | Path | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("並び順変更指定"))
        self.resize(820, 540)
        self.current_path = str(current_path) if current_path is not None else ""
        layout = QVBoxLayout(self)
        explanation = QLabel(tr(
            "フォルダごとの並び順を指定します。指定が重なる場合は、近いフォルダの指定を優先します。お気に入りへの登録は不要です。"
        ), self)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        hint = QLabel(tr("指定が適用されるフォルダでは、一覧上部の並び替えは一時変更です。保存するにはこの画面で編集してください。"), self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.list = QTreeWidget(self)
        self.list.setHeaderLabels([tr("対象フォルダ"), tr("並び順"), tr("適用範囲")])
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.list.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for rule in normalize_folder_sort_rules(rules):
            self._append(rule)
        self.list.itemDoubleClicked.connect(lambda *_: self.edit_selected())
        layout.addWidget(self.list, 1)
        actions = QHBoxLayout()
        self.add_current_button = QPushButton(tr("現在のフォルダを追加…"), self)
        self.add_current_button.setEnabled(bool(self.current_path))
        self.add_current_button.clicked.connect(lambda: self.add_path(self.current_path))
        self.add_button = QPushButton(tr("フォルダを選んで追加…"), self)
        self.add_button.clicked.connect(self._choose_folder)
        self.edit_button = QPushButton(tr("編集…"), self)
        self.edit_button.clicked.connect(self.edit_selected)
        self.remove_button = QPushButton(tr("選択項目を削除"), self)
        self.remove_button.clicked.connect(self.remove_selected)
        for button in (self.add_current_button, self.add_button, self.edit_button, self.remove_button):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.list.itemSelectionChanged.connect(self._sync_buttons)
        self._sync_buttons()
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply, self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setText(tr("適用"))
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            lambda: self.apply_requested.emit(self.rules())
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _sync_buttons(self):
        count = len(self.list.selectedItems())
        self.edit_button.setEnabled(count == 1)
        self.remove_button.setEnabled(count > 0)

    def _append(self, rule):
        item = QTreeWidgetItem(self.list)
        self._set_item(item, rule)
        return item

    def _set_item(self, item, rule):
        if rule["mode"] == "default":
            label = tr("普段の並び順を使う")
        else:
            label = tr(BROWSER_SORT_CHOICES[browser_sort_choice_index(rule["sort_key"], rule["sort_order"])][0])
        item.setText(0, rule["path"])
        item.setToolTip(0, rule["path"])
        item.setText(1, label)
        item.setText(2, tr(SCOPE_LABELS[rule["scope"]]))
        item.setData(0, Qt.ItemDataRole.UserRole, dict(rule))

    def rules(self):
        return normalize_folder_sort_rules([
            self.list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(self.list.topLevelItemCount())
        ])

    def _find(self, path):
        return next((self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount())
                     if folder_key(self.list.topLevelItem(i).text(0)) == folder_key(path)), None)

    def add_path(self, path):
        existing = self._find(path)
        rule = existing.data(0, Qt.ItemDataRole.UserRole) if existing else {
            "path": os.path.normpath(path), "scope": "subtree", "sort_key": "name",
            "sort_order": "ascending", "mode": "specified",
        }
        self._edit_rule(rule, existing)

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, tr("対象フォルダを選択"), self.current_path)
        if path:
            self.add_path(path)

    def edit_selected(self):
        selected = self.list.selectedItems()
        if len(selected) == 1:
            self._edit_rule(selected[0].data(0, Qt.ItemDataRole.UserRole), selected[0])

    def _edit_rule(self, rule, item):
        dialog = FolderSortRuleDialog(rule, self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            updated = normalize_folder_sort_rules([dialog.rule()])[0]
            duplicate = self._find(updated["path"])
            if duplicate is not None and duplicate is not item:
                if item is not None:
                    self.list.takeTopLevelItem(self.list.indexOfTopLevelItem(item))
                item = duplicate
            if item is None:
                item = self._append(updated)
            else:
                self._set_item(item, updated)
            self.list.clearSelection()
            self.list.setCurrentItem(item)
        finally:
            dialog.deleteLater()

    def remove_selected(self):
        for item in self.list.selectedItems():
            self.list.takeTopLevelItem(self.list.indexOfTopLevelItem(item))

    def accept(self):
        self.apply_requested.emit(self.rules())
        super().accept()
