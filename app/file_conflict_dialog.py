from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from .file_operation_plan import (
    ConflictResolution,
    FileConflict,
    FileOperationPlan,
)


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
        self.table.horizontalHeader().setStretchLastSection(True)
        self.same_kind_checkbox = QCheckBox("同じ種類の衝突へ適用", self)

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
        layout.addLayout(action_layout)
        layout.addWidget(self.buttons)
        self.table.selectionModel().selectionChanged.connect(
            lambda *_args: self._update_resolution_buttons()
        )
        self._update_resolution_buttons()

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
