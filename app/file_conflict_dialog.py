from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
)

from .file_operation_plan import (
    ConflictResolution,
    FileConflict,
    FileOperationPlan,
)


@dataclass(frozen=True)
class ConflictPresentationModel:
    source_name: str
    source_path: str
    source_size: str
    source_modified_time: str
    destination_name: str
    destination_path: str
    destination_size: str
    destination_modified_time: str
    item_position: str
    item_kind: str
    selected_action: str

    @classmethod
    def from_conflict(
        cls,
        conflict: FileConflict,
        *,
        item_index: int,
        total_count: int,
        selected_action: ConflictResolution,
    ) -> ConflictPresentationModel:
        source_path = conflict.source_path or ""
        destination_path = conflict.destination_path or ""
        return cls(
            source_name=os.path.basename(source_path) or "(不明)",
            source_path=source_path or "(不明)",
            source_size=cls._size_text(
                conflict.source_size,
                conflict.source_kind,
            ),
            source_modified_time=FileConflictTableModel._format_mtime(
                conflict.source_mtime_ns
            )
            or "(不明)",
            destination_name=os.path.basename(destination_path) or "(不明)",
            destination_path=destination_path or "(不明)",
            destination_size=cls._size_text(
                conflict.destination_size,
                conflict.destination_kind,
            ),
            destination_modified_time=FileConflictTableModel._format_mtime(
                conflict.destination_mtime_ns
            )
            or "(不明)",
            item_position=f"{max(1, item_index)} / {max(1, total_count)}",
            item_kind=(
                f"{conflict.source_kind or '不明'} → "
                f"{conflict.destination_kind or '不明'}"
            ),
            selected_action=selected_action.value,
        )

    @staticmethod
    def _size_text(size: int | None, kind: str | None) -> str:
        if kind == "directory":
            return "フォルダ（サイズ不明）"
        if size is None:
            return "(不明)"
        return f"{size:,} bytes"


class FileConflictTableModel(QAbstractTableModel):
    HEADERS = (
        "コピー元",
        "移動先",
        "元種別",
        "先種別",
        "サイズ",
        "更新日時",
        "衝突",
        "処理",
    )

    def __init__(self, conflicts: tuple[FileConflict, ...], parent=None) -> None:
        super().__init__(parent)
        self.conflicts = conflicts
        self._resolutions = {
            conflict.conflict_id: conflict.default_resolution for conflict in conflicts
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.conflicts)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if (
            role is Qt.ItemDataRole.DisplayRole
            and orientation is Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.conflicts)):
            return None
        conflict = self.conflicts[index.row()]
        if role is Qt.ItemDataRole.UserRole:
            return conflict
        if role is Qt.ItemDataRole.ToolTipRole:
            return (
                f"{conflict.message}\n"
                f"元: {conflict.source_path or ''}\n"
                f"先: {conflict.destination_path or ''}"
            )
        if (
            role is Qt.ItemDataRole.ForegroundRole
            and index.column() == 7
            and self._resolutions[conflict.conflict_id]
            is ConflictResolution.REPLACE
        ):
            return QColor("#c62828")
        if role is not Qt.ItemDataRole.DisplayRole:
            return None
        if index.column() == 0:
            return conflict.source_path or ""
        if index.column() == 1:
            return conflict.destination_path or ""
        if index.column() == 2:
            return conflict.source_kind or ""
        if index.column() == 3:
            return conflict.destination_kind or ""
        if index.column() == 4:
            return self._format_sizes(conflict)
        if index.column() == 5:
            return self._format_mtime(conflict.source_mtime_ns)
        if index.column() == 6:
            return conflict.kind.value
        if index.column() == 7:
            return self._resolutions[conflict.conflict_id].value
        return None

    @property
    def resolutions(self) -> dict[str, ConflictResolution]:
        return dict(self._resolutions)

    def set_resolution(
        self,
        rows: tuple[int, ...],
        resolution: ConflictResolution,
        *,
        same_kind: bool = False,
    ) -> None:
        selected_kinds = {
            self.conflicts[row].kind
            for row in rows
            if 0 <= row < len(self.conflicts)
        }
        changed_rows: list[int] = []
        for row, conflict in enumerate(self.conflicts):
            selected = row in rows or (same_kind and conflict.kind in selected_kinds)
            if not selected or resolution not in conflict.allowed_resolutions:
                continue
            self._resolutions[conflict.conflict_id] = resolution
            changed_rows.append(row)
        for row in changed_rows:
            self.dataChanged.emit(
                self.index(row, 7),
                self.index(row, 7),
                [
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.ForegroundRole,
                ],
            )

    @staticmethod
    def _format_sizes(conflict: FileConflict) -> str:
        source = (
            "?"
            if conflict.source_size is None
            else str(conflict.source_size)
        )
        destination = (
            "?"
            if conflict.destination_size is None
            else str(conflict.destination_size)
        )
        return f"{source} → {destination}"

    @staticmethod
    def _format_mtime(value: int | None) -> str:
        if value is None:
            return ""
        try:
            return datetime.fromtimestamp(value / 1_000_000_000).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except (OSError, OverflowError, ValueError):
            return ""


class ConflictResolutionDialog(QDialog):
    resolved = Signal(str, object, bool)

    def __init__(
        self,
        plan: FileOperationPlan,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle("ファイル名の衝突")
        self.setModal(True)
        self.resize(780, 440)
        self.model = FileConflictTableModel(plan.conflicts, self)
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.same_kind_checkbox = QCheckBox("同じ種類の衝突へ適用", self)
        self.detail_labels: dict[str, QLabel] = {}
        detail_layout = QGridLayout()
        detail_rows = (
            ("position", "現在の処理"),
            ("source_name", "同名ファイル名"),
            ("source_path", "コピー元／移動元"),
            ("source_meta", "元のサイズ／更新日時"),
            ("destination_path", "コピー先／移動先"),
            ("destination_meta", "先のサイズ／更新日時"),
            ("kind", "種別"),
            ("action", "選択中の処理"),
        )
        for row, (key, caption) in enumerate(detail_rows):
            caption_label = QLabel(caption, self)
            value_label = QLabel("(不明)", self)
            value_label.setObjectName(f"conflict_{key}_label")
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            value_label.setWordWrap(key in {"source_path", "destination_path"})
            value_label.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
            detail_layout.addWidget(caption_label, row, 0)
            detail_layout.addWidget(value_label, row, 1)
            self.detail_labels[key] = value_label
        detail_layout.setColumnStretch(1, 1)

        action_layout = QHBoxLayout()
        self.resolution_buttons: dict[ConflictResolution, QPushButton] = {}
        for text, resolution in (
            ("スキップ", ConflictResolution.SKIP),
            ("両方残す", ConflictResolution.KEEP_BOTH),
            ("置き換え", ConflictResolution.REPLACE),
            ("マージ", ConflictResolution.MERGE),
        ):
            button = QPushButton(text, self)
            button.clicked.connect(
                lambda _checked=False, value=resolution: self._apply(value)
            )
            self.resolution_buttons[resolution] = button
            action_layout.addWidget(button)
        action_layout.addStretch(1)
        action_layout.addWidget(self.same_kind_checkbox)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "処理を選択してください。既定では安全のためスキップします。",
                self,
            )
        )
        layout.addWidget(self.table, 1)
        layout.addLayout(detail_layout)
        layout.addLayout(action_layout)
        layout.addWidget(self.buttons)
        self.table.selectionModel().selectionChanged.connect(
            lambda *_args: self._update_current_conflict()
        )
        self.table.selectionModel().currentChanged.connect(
            lambda *_args: self._update_current_conflict()
        )
        if self.model.rowCount() > 0:
            first = self.model.index(0, 0)
            self.table.setCurrentIndex(first)
            self.table.selectRow(0)
        self._update_resolution_buttons()
        self._update_details()

    @property
    def resolutions(self) -> dict[str, ConflictResolution]:
        return self.model.resolutions

    def accept(self) -> None:  # type: ignore[override]
        self.resolved.emit(
            self.plan.operation_id,
            self.resolutions,
            self.same_kind_checkbox.isChecked(),
        )
        super().accept()

    def reject(self) -> None:  # type: ignore[override]
        resolutions = {
            conflict.conflict_id: ConflictResolution.CANCEL
            for conflict in self.plan.conflicts
        }
        self.resolved.emit(self.plan.operation_id, resolutions, False)
        super().reject()

    def _apply(self, resolution: ConflictResolution) -> None:
        rows = tuple(
            sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        )
        if not rows:
            rows = tuple(range(self.model.rowCount()))
        self.model.set_resolution(
            rows,
            resolution,
            same_kind=self.same_kind_checkbox.isChecked(),
        )
        self._update_details()

    def _update_current_conflict(self) -> None:
        self._update_resolution_buttons()
        self._update_details()

    def _update_details(self) -> None:
        if not self.plan.conflicts:
            return
        row = self.table.currentIndex().row()
        if not (0 <= row < len(self.plan.conflicts)):
            selected = self.table.selectionModel().selectedRows()
            row = selected[0].row() if selected else 0
        conflict = self.plan.conflicts[row]
        presentation = ConflictPresentationModel.from_conflict(
            conflict,
            item_index=row + 1,
            total_count=len(self.plan.conflicts),
            selected_action=self.model.resolutions[conflict.conflict_id],
        )
        values = {
            "position": presentation.item_position,
            "source_name": presentation.source_name,
            "source_path": presentation.source_path,
            "source_meta": (
                f"{presentation.source_size} / "
                f"{presentation.source_modified_time}"
            ),
            "destination_path": presentation.destination_path,
            "destination_meta": (
                f"{presentation.destination_size} / "
                f"{presentation.destination_modified_time}"
            ),
            "kind": presentation.item_kind,
            "action": presentation.selected_action,
        }
        for key, value in values.items():
            label = self.detail_labels[key]
            label.setText(value)
            label.setToolTip(value)

    def _update_resolution_buttons(self) -> None:
        selected = tuple(
            sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        )
        conflicts = (
            tuple(self.plan.conflicts[row] for row in selected)
            if selected
            else self.plan.conflicts
        )
        for resolution, button in self.resolution_buttons.items():
            button.setEnabled(
                bool(conflicts)
                and all(
                    resolution in conflict.allowed_resolutions
                    for conflict in conflicts
                )
            )
