from __future__ import annotations

from .i18n import tr


from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from .app_icon import install_window_icon


class _PropertiesNameEdit(QLineEdit):
    """Select the editable basename once on entry, then allow normal editing."""

    def __init__(self, path: Path, parent) -> None:
        super().__init__(path.name, parent)
        self._is_directory = path.is_dir()
        self._first_click = True
        self._entry_press = None

    def _select_basename(self) -> None:
        name = self.text()
        suffix = "" if self._is_directory else Path(name).suffix
        basename = name[:-len(suffix)] if suffix else name
        # Qt selection offsets are UTF-16 code units, including emoji pairs.
        self.setSelection(0, len(basename.encode("utf-16-le")) // 2)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._first_click = True
        self._select_basename()

    def focusOutEvent(self, event) -> None:
        self._entry_press = None
        super().focusOutEvent(event)

    def mousePressEvent(self, event) -> None:
        self._entry_press = (
            event.position().toPoint()
            if self._first_click and event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() == Qt.KeyboardModifier.NoModifier else None
        )
        self._first_click = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._entry_press is not None
            and (event.position().toPoint() - self._entry_press).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._entry_press = None
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        select = self._entry_press is not None and event.button() == Qt.MouseButton.LeftButton
        self._entry_press = None
        super().mouseReleaseEvent(event)
        if select:
            self._select_basename()

    def keyPressEvent(self, event) -> None:
        self._first_click = False
        self._entry_press = None
        super().keyPressEvent(event)


class FilePropertiesDialog(QDialog):
    rename_requested = Signal(str, bool)

    def __init__(self, path: str | Path, parent=None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self.setWindowTitle(tr('プロパティ'))
        install_window_icon(self)
        self.setModal(True)
        self.resize(520, 240)

        self.name_edit = _PropertiesNameEdit(self._path, self)
        self.name_edit.setObjectName("properties_name_edit")
        self.type_label = QLabel(self)
        self.location_label = QLabel(self)
        self.size_label = QLabel(self)
        self.modified_label = QLabel(self)
        for label in (
            self.location_label,
            self.size_label,
            self.modified_label,
        ):
            label.setTextInteractionFlags(
                label.textInteractionFlags()
                | Qt.TextInteractionFlag.TextSelectableByMouse
            )

        self.error_label = QLabel(self)
        self.error_label.setObjectName("properties_error_label")
        self.error_label.setStyleSheet("color: #c62828;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()

        form = QFormLayout()
        form.addRow(tr('名前:'), self.name_edit)
        form.addRow(tr('種類:'), self.type_label)
        form.addRow(tr('場所:'), self.location_label)
        form.addRow(tr('サイズ:'), self.size_label)
        form.addRow(tr('更新日時:'), self.modified_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply,
            parent=self,
        )
        self.ok_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Ok
        )
        self.cancel_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.apply_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Apply
        )
        self.ok_button.clicked.connect(
            lambda _checked=False: self.rename_requested.emit(
                self.name_edit.text(),
                True,
            )
        )
        self.apply_button.clicked.connect(
            lambda _checked=False: self.rename_requested.emit(
                self.name_edit.text(),
                False,
            )
        )
        self.cancel_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error_label)
        layout.addStretch(1)
        layout.addWidget(self.button_box)
        self._refresh_information()

    @property
    def path(self) -> Path:
        return self._path

    def set_busy(self, busy: bool) -> None:
        enabled = not busy
        self.name_edit.setEnabled(enabled)
        self.ok_button.setEnabled(enabled)
        self.apply_button.setEnabled(enabled)
        self.cancel_button.setEnabled(enabled)

    def show_error(self, message: str) -> None:
        self.error_label.setText(message or tr('名前を変更できませんでした'))
        self.error_label.show()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def clear_error(self) -> None:
        self.error_label.clear()
        self.error_label.hide()

    def mark_renamed(self, path: str | Path) -> None:
        self._path = Path(path)
        self.name_edit.setText(self._path.name)
        self.clear_error()
        self._refresh_information()

    def _refresh_information(self) -> None:
        path = self._path
        self.type_label.setText(tr('フォルダー') if path.is_dir() else tr('ファイル'))
        self.location_label.setText(str(path.parent))
        try:
            stat_result = path.stat()
        except OSError:
            self.size_label.setText("-")
            self.modified_label.setText("-")
            return
        self.size_label.setText(
            "-" if path.is_dir() else tr('{p0:,} バイト', p0=stat_result.st_size)
        )
        self.modified_label.setText(
            datetime.fromtimestamp(stat_result.st_mtime).strftime(
                "%Y/%m/%d %H:%M:%S"
            )
        )
