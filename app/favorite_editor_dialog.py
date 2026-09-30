from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRubberBand,
    QVBoxLayout,
)

from .i18n import tr


class FavoriteEditorList(QListWidget):
    """Explorer-like multi-selection plus blank-area marquee selection."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self._rubber = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._rubber_origin: QPoint | None = None
        self._rubber_base: set[int] = set()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        point = event.position().toPoint()
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self.indexAt(point).isValid()
        ):
            additive = bool(
                event.modifiers()
                & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.ShiftModifier
                )
            )
            self._rubber_base = {
                row
                for row in range(self.count())
                if self.item(row).isSelected()
            } if additive else set()
            if not additive:
                self.clearSelection()
            self._rubber_origin = point
            self._rubber.setGeometry(QRect(point, point))
            self._rubber.show()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._rubber_origin is None:
            super().mouseMoveEvent(event)
            return
        rect = QRect(self._rubber_origin, event.position().toPoint()).normalized()
        self._rubber.setGeometry(rect)
        for row in range(self.count()):
            item = self.item(row)
            hit = rect.intersects(self.visualItemRect(item))
            item.setSelected(row in self._rubber_base or hit)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._rubber_origin is not None:
            self._rubber_origin = None
            self._rubber.hide()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class FavoriteEditorDialog(QDialog):
    def __init__(
        self,
        favorites,
        colors,
        separators,
        parent=None,
        *,
        color_display=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("お気に入りを編集"))
        self.resize(620, 620)
        self._initial_paths = {str(entry.path) for entry in favorites}
        colors = colors if isinstance(colors, dict) else {}
        separators = separators if isinstance(separators, list) else []
        color_by_key = {str(path).casefold(): str(value) for path, value in colors.items()}

        layout = QVBoxLayout(self)
        explanation = QLabel(
            tr(
                "Ctrl/Shiftクリックまたは空白部分のドラッグで複数選択できます。"
                "項目をドラッグすると並び替えできます。"
            ),
            self,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.list = FavoriteEditorList(self)
        folder_rows = []
        for entry in favorites:
            folder_rows.append(
                {
                    "kind": "folder",
                    "path": str(entry.path),
                    "label": str(entry.display_name),
                    "color": color_by_key.get(str(entry.path).casefold(), ""),
                }
            )
        by_anchor: dict[str, list[dict[str, object]]] = {}
        trailing: list[dict[str, object]] = []
        folder_keys = {row["path"].casefold() for row in folder_rows}
        for raw in separators:
            if not isinstance(raw, dict):
                continue
            alignment = str(raw.get("alignment", "center"))
            if alignment not in {"left", "center", "right"}:
                alignment = "center"
            row = {
                "kind": "separator",
                "id": str(raw.get("id", "")) or f"sep-{uuid4().hex[:12]}",
                "label": str(raw.get("label", "")),
                "alignment": alignment,
            }
            anchor = str(raw.get("before_path", "")).casefold()
            if anchor and anchor in folder_keys:
                by_anchor.setdefault(anchor, []).append(row)
            else:
                trailing.append(row)
        for row in folder_rows:
            for separator in by_anchor.get(row["path"].casefold(), ()):
                self._append_row(separator)
            self._append_row(row)
        for separator in trailing:
            self._append_row(separator)
        layout.addWidget(self.list, 1)

        actions = QHBoxLayout()
        for text, callback in (
            (tr("色を設定…"), self._choose_color),
            (tr("色を解除"), self._clear_color),
            (tr("区切りを追加"), self._add_separator),
            (tr("名前を変更"), self._rename_selected),
            (tr("選択項目を削除"), self._remove_selected),
        ):
            button = QPushButton(text, self)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

        separator_group = QGroupBox(tr("区切り"), self)
        separator_form = QFormLayout(separator_group)
        self.separator_label = QLineEdit(separator_group)
        self.separator_alignment = QComboBox(separator_group)
        self.separator_alignment.addItem(tr("左端"), "left")
        self.separator_alignment.addItem(tr("中央"), "center")
        self.separator_alignment.addItem(tr("右端"), "right")
        separator_form.addRow(tr("区切り名:"), self.separator_label)
        separator_form.addRow(tr("位置:"), self.separator_alignment)
        layout.addWidget(separator_group)

        display_group = QGroupBox(tr("色アクセントの表示"), self)
        display_layout = QHBoxLayout(display_group)
        display = dict(color_display or {})
        self.show_icon = QCheckBox(tr("アイコン"), display_group)
        self.show_bar = QCheckBox(tr("左端バー"), display_group)
        self.show_background = QCheckBox(tr("背景"), display_group)
        self.show_text = QCheckBox(tr("文字"), display_group)
        self.show_icon.setChecked(bool(display.get("icon", True)))
        self.show_bar.setChecked(bool(display.get("left_bar", True)))
        self.show_background.setChecked(bool(display.get("background", False)))
        self.show_text.setChecked(bool(display.get("text", False)))
        for widget in (self.show_icon, self.show_bar, self.show_background, self.show_text):
            display_layout.addWidget(widget)
        display_layout.addStretch(1)
        layout.addWidget(display_group)

        self.list.itemSelectionChanged.connect(self._sync_separator_controls)
        self.separator_label.textEdited.connect(self._separator_label_changed)
        self.separator_alignment.currentIndexChanged.connect(self._separator_alignment_changed)
        self._sync_separator_controls()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @staticmethod
    def _display_text(data: dict[str, object]) -> str:
        if data.get("kind") == "separator":
            label = str(data.get("label", "")).strip()
            return f"── {label} ──" if label else "────────"
        return str(data.get("label", ""))

    def _append_row(self, data: dict[str, object]) -> QListWidgetItem:
        item = QListWidgetItem(self._display_text(data), self.list)
        item.setData(Qt.ItemDataRole.UserRole, dict(data))
        return item

    def _selected_items(self) -> list[QListWidgetItem]:
        return list(self.list.selectedItems())

    def _choose_color(self) -> None:
        folders = [
            item for item in self._selected_items()
            if dict(item.data(Qt.ItemDataRole.UserRole) or {}).get("kind") == "folder"
        ]
        if not folders:
            return
        initial = QColor(
            str(dict(folders[0].data(Qt.ItemDataRole.UserRole) or {}).get("color", "#80bfff"))
        )
        color = QColorDialog.getColor(initial, self, tr("お気に入りの色"))
        if not color.isValid():
            return
        for item in folders:
            data = dict(item.data(Qt.ItemDataRole.UserRole))
            data["color"] = color.name()
            item.setData(Qt.ItemDataRole.UserRole, data)

    def _clear_color(self) -> None:
        for item in self._selected_items():
            data = dict(item.data(Qt.ItemDataRole.UserRole) or {})
            if data.get("kind") == "folder":
                data["color"] = ""
                item.setData(Qt.ItemDataRole.UserRole, data)

    def _add_separator(self) -> None:
        selected_rows = sorted(self.list.row(item) for item in self._selected_items())
        row = selected_rows[-1] + 1 if selected_rows else self.list.count()
        data = {
            "kind": "separator",
            "id": f"sep-{uuid4().hex[:12]}",
            "label": "",
            "alignment": "center",
        }
        item = QListWidgetItem(self._display_text(data))
        item.setData(Qt.ItemDataRole.UserRole, data)
        self.list.insertItem(row, item)
        self.list.clearSelection()
        item.setSelected(True)
        self.list.setCurrentItem(item)
        self._sync_separator_controls()
        self.separator_label.setFocus()

    def _rename_selected(self) -> None:
        selected = self._selected_items()
        if len(selected) != 1:
            return
        item = selected[0]
        data = dict(item.data(Qt.ItemDataRole.UserRole) or {})
        current = str(data.get("label", ""))
        title = tr("区切り名") if data.get("kind") == "separator" else tr("お気に入りの表示名")
        value, accepted = QInputDialog.getText(self, title, tr("表示名:"), text=current)
        if not accepted:
            return
        value = value.strip()
        if data.get("kind") == "folder" and not value:
            return
        data["label"] = value
        item.setData(Qt.ItemDataRole.UserRole, data)
        item.setText(self._display_text(data))
        self._sync_separator_controls()

    def _remove_selected(self) -> None:
        for row in sorted((self.list.row(item) for item in self._selected_items()), reverse=True):
            self.list.takeItem(row)
        self._sync_separator_controls()

    def _current_separator(self):
        selected = self._selected_items()
        if len(selected) != 1:
            return None, None
        item = selected[0]
        data = dict(item.data(Qt.ItemDataRole.UserRole) or {})
        if data.get("kind") != "separator":
            return None, None
        return item, data

    def _sync_separator_controls(self) -> None:
        item, data = self._current_separator()
        enabled = item is not None
        self.separator_label.setEnabled(enabled)
        self.separator_alignment.setEnabled(enabled)
        self.separator_label.blockSignals(True)
        self.separator_alignment.blockSignals(True)
        try:
            self.separator_label.setText(str(data.get("label", "")) if data else "")
            alignment = str(data.get("alignment", "center")) if data else "center"
            index = self.separator_alignment.findData(alignment)
            self.separator_alignment.setCurrentIndex(max(0, index))
        finally:
            self.separator_label.blockSignals(False)
            self.separator_alignment.blockSignals(False)

    def _separator_label_changed(self, value: str) -> None:
        item, data = self._current_separator()
        if item is None:
            return
        data["label"] = value
        item.setData(Qt.ItemDataRole.UserRole, data)
        item.setText(self._display_text(data))

    def _separator_alignment_changed(self, _index: int) -> None:
        item, data = self._current_separator()
        if item is None:
            return
        data["alignment"] = str(self.separator_alignment.currentData() or "center")
        item.setData(Qt.ItemDataRole.UserRole, data)

    def favorite_rows(self) -> list[dict[str, str]]:
        result = []
        for row in range(self.list.count()):
            data = dict(self.list.item(row).data(Qt.ItemDataRole.UserRole) or {})
            if data.get("kind") == "folder":
                result.append(
                    {
                        "path": str(data.get("path", "")),
                        "label": str(data.get("label", "")),
                        "color": str(data.get("color", "")),
                    }
                )
        return result

    def separators(self) -> list[dict[str, str]]:
        rows = [
            dict(self.list.item(row).data(Qt.ItemDataRole.UserRole) or {})
            for row in range(self.list.count())
        ]
        result = []
        for index, data in enumerate(rows):
            if data.get("kind") != "separator":
                continue
            before_path = ""
            for candidate in rows[index + 1 :]:
                if candidate.get("kind") == "folder":
                    before_path = str(candidate.get("path", ""))
                    break
            alignment = str(data.get("alignment", "center"))
            if alignment not in {"left", "center", "right"}:
                alignment = "center"
            result.append(
                {
                    "id": str(data.get("id", "")) or f"sep-{uuid4().hex[:12]}",
                    "label": str(data.get("label", "")),
                    "alignment": alignment,
                    "before_path": before_path,
                }
            )
        return result

    def color_display(self) -> dict[str, bool]:
        return {
            "icon": self.show_icon.isChecked(),
            "left_bar": self.show_bar.isChecked(),
            "background": self.show_background.isChecked(),
            "text": self.show_text.isChecked(),
        }
