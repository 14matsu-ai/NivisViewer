from __future__ import annotations

from .i18n import tr


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


QT_PROGRESS_MAXIMUM = 2_147_483_647


def _project_qt_progress(
    completed: int,
    total: int,
    *,
    maximum: int = QT_PROGRESS_MAXIMUM,
    always_scale: bool = False,
) -> tuple[int, int]:
    """Project exact Python counters into QProgressBar's 32-bit range."""

    exact_total = max(0, int(total))
    exact_completed = max(0, int(completed))
    qt_limit = max(1, min(int(maximum), QT_PROGRESS_MAXIMUM))
    if exact_total <= 0:
        return 0, 0

    exact_completed = min(exact_completed, exact_total)
    qt_maximum = (
        qt_limit
        if always_scale or exact_total > qt_limit
        else exact_total
    )
    if qt_maximum == exact_total:
        return qt_maximum, exact_completed
    if exact_completed <= 0:
        return qt_maximum, 0
    if exact_completed >= exact_total:
        return qt_maximum, qt_maximum

    projected = (
        exact_completed * qt_maximum + exact_total // 2
    ) // exact_total
    if qt_maximum > 1:
        projected = min(qt_maximum - 1, max(1, projected))
    return qt_maximum, projected


class FileOperationPanel(QWidget):
    cancel_requested = Signal()
    result_acknowledged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("file_operation_panel")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._queue = None
        self._active_operation_id: str | None = None
        self._active_operation = ""
        self._cancel_requested_for_operation = False
        self._idle_close_queued = False
        self._result_requires_acknowledgement = False
        self.summary_label = QLabel("", self)
        self.detail_label = QLabel("", self)
        self.queue_label = QLabel("", self)
        self.item_progress = QProgressBar(self)
        self.item_progress.setTextVisible(True)
        self.byte_progress = QProgressBar(self)
        self.byte_progress.setTextVisible(True)
        self.current_file_progress = QProgressBar(self)
        self.current_file_progress.setTextVisible(True)
        self.cancel_button = QPushButton(tr('キャンセル'), self)
        self.cancel_button.clicked.connect(self._request_cancel)
        self.details_button = QPushButton(tr('詳細'), self)
        self.details_button.setVisible(False)
        self.details_view = QTextEdit(self)
        self.details_view.setReadOnly(True)
        self.details_view.setVisible(False)
        self.details_button.clicked.connect(self._toggle_details)
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

    def bind(self, queue, *, completion_source=None) -> None:
        """Bind queue activity and optionally post-processed results."""
        if self._queue is queue:
            return
        if self._queue is not None:
            raise RuntimeError("FileOperationPanel is already bound")
        self._queue = queue
        queue.operation_preparing.connect(self._on_operation_preparing)
        queue.operation_started.connect(self._on_operation_started)
        queue.operation_progress.connect(self.show_progress)
        result_source = (
            completion_source if completion_source is not None else queue
        )
        result_source.operation_completed.connect(self.show_result)
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
        self.queue_label.setText(tr('待機 {p0}', p0=len(self._queue.queued_requests)))
        self.close_if_idle()

    def _begin_operation(self, request: FileOperationRequest) -> None:
        self._active_operation_id = str(
            request.operation_id or request.request_id
        )
        self._active_operation = request.operation.value
        self._cancel_requested_for_operation = False
        self._idle_close_queued = False
        self._result_requires_acknowledgement = False
        self.details_view.setVisible(False)
        self.details_button.setText(tr('詳細'))
        self.cancel_button.setText(tr('キャンセル'))

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
        if self._result_requires_acknowledgement:
            self._result_requires_acknowledgement = False
            self.close()
            self.result_acknowledged.emit()
            return
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
            details += tr('  残り約{p0}秒', p0=max(0, round(progress.eta_seconds)))
        self.detail_label.setText(details)
        item_maximum, item_value = _project_qt_progress(
            progress.completed,
            max(1, progress.total),
        )
        self.item_progress.setRange(0, item_maximum)
        self.item_progress.setValue(item_value)
        if progress.bytes_total > 0:
            byte_maximum, byte_value = _project_qt_progress(
                progress.bytes_completed,
                progress.bytes_total,
                maximum=1000,
                always_scale=True,
            )
            self.byte_progress.setRange(0, byte_maximum)
            self.byte_progress.setValue(byte_value)
            self.byte_progress.setFormat(
                f"{progress.bytes_completed} / {progress.bytes_total} bytes"
            )
        else:
            self.byte_progress.setRange(0, 0)
        if (
            progress.current_file_bytes_total is not None
            and progress.current_file_bytes_total > 0
        ):
            current_maximum, current_value = _project_qt_progress(
                progress.current_file_bytes_completed,
                progress.current_file_bytes_total,
            )
            self.current_file_progress.setRange(0, current_maximum)
            self.current_file_progress.setValue(current_value)
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
        details = [
                f"{item.source_path or item.destination_path or tr('(不明)')}: "
                f"{item.error_message or item.error_code or tr('失敗')}"
                + (
                    tr('\n  未回収artifact: {p0}', p0=', '.join(item.artifact_paths))
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
            ]
        details.extend(
            tr('メタデータ同期の確認が必要です: {p0}', p0=warning)
            for warning in result.metadata_sync_warnings
        )
        self.details_view.setPlainText("\n".join(details))
        self.details_button.setVisible(
            bool(failures or result.metadata_sync_warnings)
        )
        self.cancel_button.setEnabled(False)
        self._result_requires_acknowledgement = bool(
            result.metadata_sync_warnings
        )
        if self._result_requires_acknowledgement:
            self._hide_timer.stop()
            self.details_view.setVisible(True)
            self.details_button.setText(tr('詳細を隠す'))
            self.cancel_button.setText(tr('閉じる'))
            self.cancel_button.setEnabled(True)
        else:
            self.details_view.setVisible(False)
            self.details_button.setText(tr('詳細'))
            self.cancel_button.setText(tr('キャンセル'))
        if result.cancelled:
            summary = tr('キャンセルしました')
            if result.metadata_sync_warnings:
                summary += tr('（メタデータ同期の確認が必要です）')
            self.summary_label.setText(summary)
            if not self._result_requires_acknowledgement:
                self._hide_timer.start(3000)
        elif failures:
            summary = (
                tr('完了: 成功 {p0} / スキップ {p1} / 失敗 {p2}', p0=sum(item.success for item in items), p1=skipped, p2=max(0, failures - skipped))
            )
            if source_remaining:
                summary += tr('（元項目残留 {p0}）', p0=source_remaining)
            self.summary_label.setText(summary)
        else:
            self.summary_label.setText(
                tr('完了（メタデータ同期の確認が必要です）')
                if result.metadata_sync_warnings
                else tr('完了')
            )
            if not self._result_requires_acknowledgement:
                self._hide_timer.start(2500)
        self.byte_progress.setRange(0, 1000)
        self.byte_progress.setValue(1000 if not result.cancelled else 0)
        self._active_operation_id = None
        self._active_operation = ""
        self._cancel_requested_for_operation = False
        self.close_if_idle()

    def _toggle_details(self) -> None:
        visible = not self.details_view.isVisible()
        self.details_view.setVisible(visible)
        self.details_button.setText(
            tr('詳細を隠す') if visible else tr('詳細を表示')
        )

    @property
    def awaiting_result_acknowledgement(self) -> bool:
        return self._result_requires_acknowledgement

    def close_if_idle(self) -> None:
        if (
            self._queue is None
            or self._queue.busy
            or self._active_operation_id is not None
            or self._result_requires_acknowledgement
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
            and not self._result_requires_acknowledgement
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
