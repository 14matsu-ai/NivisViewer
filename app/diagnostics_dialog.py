from __future__ import annotations

import platform
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .logging_setup import dependency_versions
from .version import __version__


def diagnostic_text(
    profile_dir: str | Path,
    *,
    pdf_available: bool | None = None,
    archive_status: str = "起動時検出を使用",
    registration_status: str = "設定画面で確認",
) -> str:
    versions = dependency_versions()
    lines = [
        f"NivisViewer {__version__}",
        "License: MIT",
        f"Mode: {'frozen' if getattr(sys, 'frozen', False) else 'source'}",
        f"Executable: {sys.executable}",
        f"Profile: {Path(profile_dir)}",
        f"Python: {platform.python_version()}",
        f"PySide6: {versions['PySide6']}",
        f"Pillow: {versions['Pillow']}",
        f"pypdfium2: {versions['pypdfium2']}",
        f"PDF: {pdf_available if pdf_available is not None else '未確認'}",
        f"Archive backends: {archive_status}",
        f"Windows registration: {registration_status}",
        "Telemetry: disabled",
    ]
    return "\n".join(lines)


class DiagnosticsDialog(QDialog):
    def __init__(
        self,
        profile_dir: str | Path,
        parent: QWidget | None = None,
        *,
        text: str | None = None,
        licenses_dir: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("NivisViewerについて／診断情報")
        self.resize(680, 480)
        self.profile_dir = Path(profile_dir)
        self.licenses_dir = (
            Path(licenses_dir)
            if licenses_dir is not None
            else Path(__file__).resolve().parents[1] / "licenses"
        )
        self.text_edit = QTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(text or diagnostic_text(self.profile_dir))
        copy_button = QPushButton("診断情報をコピー", self)
        logs_button = QPushButton("ログフォルダを開く", self)
        licenses_button = QPushButton("ライセンス情報を開く", self)
        copy_button.clicked.connect(self.copy_to_clipboard)
        logs_button.clicked.connect(
            lambda: self._open_path(self.profile_dir / "data" / "logs")
        )
        licenses_button.clicked.connect(
            lambda: self._open_path(self.licenses_dir)
        )
        actions = QHBoxLayout()
        actions.addWidget(copy_button)
        actions.addWidget(logs_button)
        actions.addWidget(licenses_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.text_edit)
        layout.addLayout(actions)
        layout.addWidget(buttons)

    def copy_to_clipboard(self) -> None:
        application = QApplication.instance()
        if application is not None:
            application.clipboard().setText(self.text_edit.toPlainText())

    @staticmethod
    def _open_path(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
