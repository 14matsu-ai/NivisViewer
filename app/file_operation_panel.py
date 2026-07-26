from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .file_operation_plan import FileOperationState
from .file_operation_service import FileOperationProgress, FileOperationResult


class FileOperationPanel(QWidget):
    cancel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("file_operation_panel")
        self.summary_label = QLabel("", self)
        self.detail_label = QLabel("", self)
        self.queue_label = QLabel("", self)
        self.item_progress = QProgressBar(self)
        self.item_progress.setTextVisible(True)
        self.byte_progress = QProgressBar(self)
        self.byte_progress.setTextVisible(True)
        self.current_file_progress = QProgressBar(self)
        self.current_file_progress.setTextVisible(True)
        self.cancel_button = QPushButton("キャンセル", self)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.details_button = QPushButton("詳細", self)
        self.details_button.setVisible(False)
        self.details_view = QTextEdit(self)
        self.details_view.setReadOnly(True)
        self.details_view.setVisible(False)
        self.details_button.clicked.connect(
            lambda: self.details_view.setVisible(
                not self.details_view.isVisible()
            )
        )
        top_layout = QHBoxLayout()
        top_layout.addWidget(self.summary_label)
        top_layout.addWidget(self.detail_label, 1)
        top_layout.addWidget(self.queue_label)
        top_layout.addWidget(self.item_progress)
        top_layout.addWidget(self.byte_progress)
        top_layout.addWidget(self.current_file_progress)
        top_layout.addWidget(self.details_button)
        top_layout.addWidget(self.cancel_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.addLayout(top_layout)
        layout.addWidget(self.details_view)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.hide()

    def bind(self, queue) -> None:
        queue.operation_preparing.connect(
            lambda request: self.show_state(
                request.operation.value, FileOperationState.PREPARING
            )
        )
        queue.operation_started.connect(
            lambda request: self.show_state(
                request.operation.value, FileOperationState.RUNNING
            )
        )
        queue.operation_progress.connect(self.show_progress)
        queue.operation_completed.connect(self.show_result)
        queue.queue_changed.connect(
            lambda: self.queue_label.setText(
                f"待機 {len(queue.queued_requests)}"
            )
        )
        self.cancel_requested.connect(queue.cancel)

    def show_state(self, operation: str, state: FileOperationState) -> None:
        self._hide_timer.stop()
        self.summary_label.setText(f"{operation}: {state.value}")
        self.detail_label.clear()
        self.cancel_button.setEnabled(
            state
            not in {
                FileOperationState.COMPLETED,
                FileOperationState.FAILED,
                FileOperationState.CANCELLED,
            }
        )
        self.show()

    def show_progress(self, progress: FileOperationProgress) -> None:
        self.show()
        self.summary_label.setText(progress.operation.value)
        details = progress.source_path or ""
        if progress.bytes_per_second > 0:
            details += f"  {self._format_bytes(progress.bytes_per_second)}/s"
        if progress.eta_seconds is not None:
            details += f"  残り約{max(0, round(progress.eta_seconds))}秒"
        self.detail_label.setText(details)
        self.item_progress.setRange(0, max(1, progress.total))
        self.item_progress.setValue(progress.completed)
        if progress.bytes_total > 0:
            self.byte_progress.setRange(0, 1000)
            self.byte_progress.setValue(
                min(1000, int(progress.bytes_completed * 1000 / progress.bytes_total))
            )
            self.byte_progress.setFormat(
                f"{progress.bytes_completed} / {progress.bytes_total} bytes"
            )
        else:
            self.byte_progress.setRange(0, 0)
        if progress.current_file_bytes_total:
            self.current_file_progress.setRange(
                0,
                max(1, progress.current_file_bytes_total),
            )
            self.current_file_progress.setValue(
                min(
                    progress.current_file_bytes_completed,
                    progress.current_file_bytes_total,
                )
            )
            self.current_file_progress.setFormat(
                f"{progress.current_file_bytes_completed} / "
                f"{progress.current_file_bytes_total}"
            )
        else:
            self.current_file_progress.setRange(0, 0)

    def show_result(self, result: FileOperationResult) -> None:
        failures = len(result.failures)
        self.details_view.setPlainText(
            "\n".join(
                f"{item.source_path or item.destination_path or '(不明)'}: "
                f"{item.error_message or item.error_code or '失敗'}"
                for item in result.failures
            )
        )
        self.details_button.setVisible(bool(result.failures))
        self.cancel_button.setEnabled(False)
        if result.cancelled:
            self.summary_label.setText("キャンセルしました")
            self._hide_timer.start(3000)
        elif failures:
            self.summary_label.setText(
                f"完了: 成功 {len(result.successes)} / 失敗 {failures}"
            )
        else:
            self.summary_label.setText("完了")
            self._hide_timer.start(2500)
        self.byte_progress.setRange(0, 1000)
        self.byte_progress.setValue(1000 if not result.cancelled else 0)

    @staticmethod
    def _format_bytes(value: float) -> str:
        size = max(0.0, float(value))
        for unit in ("B", "KiB", "MiB", "GiB"):
            if size < 1024.0 or unit == "GiB":
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} GiB"
