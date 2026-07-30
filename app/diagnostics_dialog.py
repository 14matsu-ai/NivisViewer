from __future__ import annotations

import platform
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
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

from .app_icon import install_window_icon
from .logging_setup import dependency_versions
from .pdfium_service import (
    PdfAvailabilitySnapshot,
    PdfAvailabilityState,
    PdfiumService,
)
from .version import __version__


def diagnostic_text(
    profile_dir: str | Path,
    *,
    pdf_available: bool | None = None,
    archive_status: str = "起動時検出を使用",
    registration_status: str = "設定画面で確認",
    pdf_snapshot: PdfAvailabilitySnapshot | None = None,
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
        f"PDF: {_pdf_status_text(pdf_snapshot, pdf_available)}",
        f"Archive backends: {archive_status}",
        f"Windows registration: {registration_status}",
        "Telemetry: disabled",
    ]
    return "\n".join(lines)


def _pdf_status_text(
    snapshot: PdfAvailabilitySnapshot | None,
    legacy_available: bool | None,
) -> str:
    if snapshot is None:
        if legacy_available is None:
            return "未確認"
        return "利用可能" if legacy_available else "利用不可"
    return {
        PdfAvailabilityState.UNKNOWN: "未確認",
        PdfAvailabilityState.CHECKING: "確認中",
        PdfAvailabilityState.AVAILABLE: "利用可能",
        PdfAvailabilityState.UNAVAILABLE: "利用不可",
        PdfAvailabilityState.ERROR: "エラー",
        PdfAvailabilityState.STOPPED: "停止済み",
    }[snapshot.state]


class DiagnosticsDialog(QDialog):
    def __init__(
        self,
        profile_dir: str | Path,
        parent: QWidget | None = None,
        *,
        text: str | None = None,
        licenses_dir: str | Path | None = None,
        pdfium_service: PdfiumService | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("NivisViewerについて／診断情報")
        install_window_icon(self)
        self.resize(680, 480)
        self.profile_dir = Path(profile_dir)
        self.pdfium_service = pdfium_service
        self._static_text = text
        self._last_pdf_snapshot = (
            self.pdfium_service.availability_snapshot
            if self.pdfium_service is not None
            else None
        )
        self.licenses_dir = (
            Path(licenses_dir)
            if licenses_dir is not None
            else Path(os.path.abspath(__file__)).parents[1] / "licenses"
        )
        self.text_edit = QTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(self._current_text())
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
        self._availability_timer = QTimer(self)
        self._availability_timer.setInterval(100)
        self._availability_timer.timeout.connect(self._refresh_cached_pdf_state)
        if self.pdfium_service is not None:
            self._availability_timer.start()

    def copy_to_clipboard(self) -> None:
        application = QApplication.instance()
        if application is not None:
            application.clipboard().setText(self.text_edit.toPlainText())

    def _current_text(self) -> str:
        if self._static_text is not None:
            return self._static_text
        snapshot = (
            self.pdfium_service.availability_snapshot
            if self.pdfium_service is not None
            else None
        )
        return diagnostic_text(self.profile_dir, pdf_snapshot=snapshot)

    def _refresh_cached_pdf_state(self) -> None:
        if self.pdfium_service is None:
            return
        snapshot = self.pdfium_service.availability_snapshot
        if snapshot == self._last_pdf_snapshot:
            return
        self._last_pdf_snapshot = snapshot
        value = self._current_text()
        if value != self.text_edit.toPlainText():
            self.text_edit.setPlainText(value)

    def done(self, result: int) -> None:  # type: ignore[override]
        self._availability_timer.stop()
        super().done(result)

    @staticmethod
    def _open_path(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
