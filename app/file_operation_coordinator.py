from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from .file_operation_service import (
    FileOperationKind,
    FileOperationRequest,
    FileOperationResult,
    FileOperationService,
)
from .file_operation_worker import FileOperationExecutor
from .file_operation_queue import FileOperationQueue
from .metadata_store import MetadataStore


class FileOperationCoordinator(QObject):
    operation_started = Signal(object)
    operation_progress = Signal(object)
    operation_completed = Signal(object)
    plan_ready = Signal(object)
    conflicts_required = Signal(object)
    state_changed = Signal(str, object)
    queue_changed = Signal()

    def __init__(
        self,
        metadata_store: MetadataStore | None,
        parent: QObject | None = None,
        *,
        service: FileOperationService | None = None,
        executor: FileOperationExecutor | None = None,
        queue: FileOperationQueue | None = None,
    ) -> None:
        super().__init__(parent)
        self.metadata_store = metadata_store
        self.queue = queue
        self.executor = (
            None if queue is not None else (executor or FileOperationExecutor(service))
        )
        backend = self.queue or self.executor
        assert backend is not None
        if self.queue is not None:
            self.queue.operation_preparing.connect(self.operation_started)
        else:
            backend.operation_started.connect(self.operation_started)
        backend.operation_progress.connect(self.operation_progress)
        backend.operation_completed.connect(self._on_completed)
        if self.queue is not None:
            self.queue.plan_ready.connect(self.plan_ready)
            self.queue.conflicts_required.connect(self.conflicts_required)
            self.queue.state_changed.connect(self.state_changed)
            self.queue.queue_changed.connect(self.queue_changed)

    @property
    def busy(self) -> bool:
        backend = self.queue or self.executor
        return bool(backend and backend.busy)

    def execute(self, request: FileOperationRequest) -> bool:
        backend = self.queue or self.executor
        return bool(backend and backend.execute(request))

    def cancel(self) -> None:
        backend = self.queue or self.executor
        if backend is not None:
            backend.cancel()

    def close(self) -> None:
        backend = self.queue or self.executor
        if backend is not None:
            backend.close()

    def wait_for_done(self, msecs: int = 5000) -> bool:
        backend = self.queue or self.executor
        return True if backend is None else backend.wait_for_done(msecs)

    @Slot(object)
    def _on_completed(self, result: FileOperationResult) -> None:
        if (
            self.metadata_store is not None
            and result.operation in {FileOperationKind.RENAME, FileOperationKind.MOVE}
        ):
            for item in result.successes:
                if item.source_path and item.destination_path:
                    self.metadata_store.relocate_tree(
                        item.source_path,
                        item.destination_path,
                    )
        self.operation_completed.emit(result)
