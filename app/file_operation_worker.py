from __future__ import annotations

from threading import Event, Lock

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from .file_operation_service import (
    FileOperationErrorCode,
    FileOperationItemResult,
    FileOperationRequest,
    FileOperationResult,
    FileOperationService,
)


_RETIRED_EXECUTORS: set[FileOperationExecutor] = set()


class _FileOperationWorkerSignals(QObject):
    progress = Signal(object)
    completed = Signal(object)


class _FileOperationWorker(QRunnable):
    def __init__(
        self,
        service: FileOperationService,
        request: FileOperationRequest,
        cancelled: Event,
    ) -> None:
        super().__init__()
        self.service = service
        self.request = request
        self.cancelled = cancelled
        self.signals = _FileOperationWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.service.execute(
                self.request,
                cancelled=self.cancelled,
                progress=self.signals.progress.emit,
            )
        except BaseException as exc:
            result = FileOperationResult(
                self.request.operation,
                tuple(
                    FileOperationItemResult(
                        source,
                        self.request.destination_directory,
                        False,
                        FileOperationErrorCode.IO_ERROR.value,
                        str(exc),
                    )
                    for source in (self.request.source_paths or (None,))
                ),
                request_id=self.request.request_id,
                operation_id=self.request.operation_id,
            )
        self.signals.completed.emit(result)


class FileOperationExecutor(QObject):
    operation_started = Signal(object)
    operation_progress = Signal(object)
    operation_completed = Signal(object)

    def __init__(
        self,
        service: FileOperationService | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or FileOperationService()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._worker: _FileOperationWorker | None = None
        self._cancelled: Event | None = None
        self._active_request: FileOperationRequest | None = None
        self._lock = Lock()
        self._closed = False

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._worker is not None

    @property
    def active_request(self) -> FileOperationRequest | None:
        with self._lock:
            return self._active_request

    def execute(self, request: FileOperationRequest) -> bool:
        with self._lock:
            if self._closed or self._worker is not None:
                return False
            cancelled = Event()
            worker = _FileOperationWorker(self.service, request, cancelled)
            self._worker = worker
            self._cancelled = cancelled
            self._active_request = request
        worker.signals.progress.connect(self._relay_progress)
        worker.signals.completed.connect(self._relay_completed)
        self.operation_started.emit(request)
        self._pool.start(worker)
        return True

    def cancel(self) -> None:
        with self._lock:
            cancelled = self._cancelled
            worker = self._worker
        if cancelled is not None:
            cancelled.set()
        if worker is not None:
            try:
                removed = self._pool.tryTake(worker)
            except RuntimeError:
                removed = False
            if removed:
                request = worker.request
                self._clear_active()
                if not self._closed:
                    self.operation_completed.emit(
                        FileOperationResult(
                            request.operation,
                            (),
                            cancelled=True,
                            request_id=request.request_id,
                        )
                    )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.cancel()
        if self.busy:
            _RETIRED_EXECUTORS.add(self)

    def wait_for_done(self, msecs: int = 5000) -> bool:
        return self._pool.waitForDone(max(0, int(msecs)))

    @Slot(object)
    def _relay_progress(self, progress: object) -> None:
        if not self._closed:
            self.operation_progress.emit(progress)

    @Slot(object)
    def _relay_completed(self, result: FileOperationResult) -> None:
        self._clear_active()
        _RETIRED_EXECUTORS.discard(self)
        if not self._closed:
            self.operation_completed.emit(result)

    def _clear_active(self) -> None:
        with self._lock:
            self._worker = None
            self._cancelled = None
            self._active_request = None
