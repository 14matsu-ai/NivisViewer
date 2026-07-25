from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from .file_operation_service import (
    FileOperationKind,
    FileOperationRequest,
    FileOperationResult,
    FileOperationService,
)
from .file_operation_worker import FileOperationExecutor
from .metadata_store import MetadataStore


class FileOperationCoordinator(QObject):
    operation_started = Signal(object)
    operation_progress = Signal(object)
    operation_completed = Signal(object)

    def __init__(
        self,
        metadata_store: MetadataStore | None,
        parent: QObject | None = None,
        *,
        service: FileOperationService | None = None,
        executor: FileOperationExecutor | None = None,
    ) -> None:
        super().__init__(parent)
        self.metadata_store = metadata_store
        self.executor = executor or FileOperationExecutor(service)
        self.executor.operation_started.connect(self.operation_started)
        self.executor.operation_progress.connect(self.operation_progress)
        self.executor.operation_completed.connect(self._on_completed)

    @property
    def busy(self) -> bool:
        return self.executor.busy

    def execute(self, request: FileOperationRequest) -> bool:
        return self.executor.execute(request)

    def cancel(self) -> None:
        self.executor.cancel()

    def close(self) -> None:
        self.executor.close()

    def wait_for_done(self, msecs: int = 5000) -> bool:
        return self.executor.wait_for_done(msecs)

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
