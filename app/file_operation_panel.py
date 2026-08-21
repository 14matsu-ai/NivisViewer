from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
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
from .file_operation_service import (
    FileOperationItemState,
    FileOperationProgress,
    FileOperationResult,
)


class FileOperationPanel(QWidget):
    cancel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("file_operation_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._queue = None
        self._active_operation_id: str | None = None
        self._active_operation = ""
        self._cancel_requested_for_operation = False
        self._idle_close_queued = False
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
        self.cancel_button.clicked.connect(self._request_cancel)
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
        if self._queue is queue:
            return
        if self._queue is not None:
            raise RuntimeError("FileOperationPanel is already bound")
        self._queue = queue
        queue.operation_preparing.connect(self._on_operation_preparing)
        queue.operation_started.connect(self._on_operation_started)
        queue.operation_progress.connect(self.show_progress)
        queue.operation_completed.connect(self.show_result)
        queue.state_changed.connect(self._on_state_changed)
        queue.queue_changed.connect(self._on_queue_changed)

    def _on_operation_preparing(self, request: FileOperationRequest) -> None:
        self._begin_operation(request)
        self.show_state(request.operation.value, FileOperationState.PREPARING)

    def _on_operation_started(self, request: FileOperationRequest) -> None:
        self._begin_operation(request)
        self.show_state(request.operation.value, FileOperationState.RUNNING)

    def show_operation(
        self,
        request: FileOperationRequest,
        state: FileOperationState,
    ) -> None:
        operation_id = str(request.operation_id or request.request_id)
        if operation_id != self._active_operation_id:
            self._begin_operation(request)
        elif self._cancel_requested_for_operation:
            state = FileOperationState.CANCELLING
        self.show_state(request.operation.value, state)

    def _on_queue_changed(self) -> None:
        self.queue_label.setText(f"待機 {len(self._queue.queued_requests)}")
        self.close_if_idle()

    def _begin_operation(self, request: FileOperationRequest) -> None:
        self._active_operation_id = str(
            request.operation_id or request.request_id
        )
        self._active_operation = request.operation.value
        self._cancel_requested_for_operation = False
        self._idle_close_queued = False

    def _on_state_changed(
        self,
        operation_id: str,
        state: FileOperationState,
    ) -> None:
        if str(operation_id) != self._active_operation_id:
            return
        if state is FileOperationState.CANCELLING:
            self.show_state(
                self._active_operation,
                FileOperationState.CANCELLING,
            )

    def _request_cancel(self) -> None:
        if (
            self._queue is None
            or self._active_operation_id is None
            or self._cancel_requested_for_operation
        ):
            return
        self._cancel_requested_for_operation = True
        self.cancel_button.setEnabled(False)
        self.summary_label.setText(
            f"{self._active_operation}: {FileOperationState.CANCELLING.value}"
        )
        self.cancel_requested.emit()
        self._queue.cancel(self._active_operation_id)

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
                FileOperationState.CANCELLING,
            }
        )
        self.show()

    def show_progress(self, progress: FileOperationProgress) -> None:
        if (
            self._active_operation_id is not None
            and progress.operation_id
            and str(progress.operation_id) != self._active_operation_id
        ):
            return
        if self._cancel_requested_for_operation:
            return
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
        if (
            self._active_operation_id is not None
            and result.operation_id
            and str(result.operation_id) != self._active_operation_id
        ):
            return
        items = result.effective_items
        failures = sum(not item.success for item in items)
        skipped = sum(
            item.state is FileOperationItemState.SKIPPED for item in items
        )
        source_remaining = sum(
            item.state
            in {
                FileOperationItemState.COPIED_SOURCE_REMAINS,
                FileOperationItemState.SOURCE_REMOVAL_FAILED,
                FileOperationItemState.DESTINATION_PUBLISHED_SOURCE_REMAINS,
            }
            for item in items
        )
        self.details_view.setPlainText(
            "\n".join(
                f"{item.source_path or item.destination_path or '(不明)'}: "
                f"{item.error_message or item.error_code or '失敗'}"
                + (
                    f"\n  未回収artifact: {', '.join(item.artifact_paths)}"
                    if item.artifact_paths
                    else ""
                )
                + (
                    f"\n  cleanup: {'; '.join(item.cleanup_errors)}"
                    if item.cleanup_errors
                    else ""
                )
                for item in items
                if not item.success
            )
        )
        self.details_button.setVisible(bool(failures))
        self.cancel_button.setEnabled(False)
        if result.cancelled:
            self.summary_label.setText("キャンセルしました")
            self._hide_timer.start(3000)
        elif failures:
            summary = (
                f"完了: 成功 {sum(item.success for item in items)} / "
                f"スキップ {skipped} / 失敗 {max(0, failures - skipped)}"
            )
            if source_remaining:
                summary += f"（元項目残留 {source_remaining}）"
            self.summary_label.setText(summary)
        else:
            self.summary_label.setText("完了")
            self._hide_timer.start(2500)
        self.byte_progress.setRange(0, 1000)
        self.byte_progress.setValue(1000 if not result.cancelled else 0)
        self._active_operation_id = None
        self._active_operation = ""
        self._cancel_requested_for_operation = False
        self.close_if_idle()

    def close_if_idle(self) -> None:
        if (
            self._queue is None
            or self._queue.busy
            or self._active_operation_id is not None
            or self._idle_close_queued
        ):
            return
        self._idle_close_queued = True
        # Bind the callback to this QWidget so Qt drops it when
        # WA_DeleteOnClose destroys the panel before the next event-loop turn.
        QTimer.singleShot(0, self, self._close_if_still_idle)

    def _close_if_still_idle(self) -> None:
        self._idle_close_queued = False
        if (
            self._queue is not None
            and not self._queue.busy
            and self._active_operation_id is None
        ):
            self.close()

    @staticmethod
    def _format_bytes(value: float) -> str:
        size = max(0.0, float(value))
        for unit in ("B", "KiB", "MiB", "GiB"):
            if size < 1024.0 or unit == "GiB":
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} GiB"
