from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QObject, Signal, Slot

from .file_operation_service import (
    FileOperationKind,
    FileOperationItemState,
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
        self._undo_entries = ()
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
        if request.operation is FileOperationKind.UNDO:
            entries = self._undo_entries
            if (self.busy or not entries
                    or request.source_paths != tuple(entry.path for entry in entries)):
                return False
            request = replace(request, undo_entries=entries)
            self._undo_entries = ()
            accepted = bool(backend and backend.execute(request))
            if not accepted:
                self._undo_entries = entries
            return accepted
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
        # Unsupported and undo operations deliberately clear the one-step journal.
        self._undo_entries = result.undo_entries if result.operation is not FileOperationKind.UNDO else ()
        metadata_warnings: list[str] = []

        def sync_metadata(method_name: str, *paths: str) -> None:
            if self.metadata_store is None:
                return
            method = getattr(self.metadata_store, method_name, None)
            try:
                completed = bool(callable(method) and method(*paths))
            except Exception as exc:
                completed = False
                error = str(exc)
            else:
                error = str(getattr(self.metadata_store, "last_error", "") or "")
            if not completed:
                label = ", ".join(paths)
                detail = f": {error}" if error else ""
                metadata_warnings.append(
                    f"{method_name} ({label}){detail}"
                )

        if self.metadata_store is not None:
            for item in result.effective_items:
                operation = item.operation or result.operation
                if not item.destination_path:
                    continue
                if item.replaced_existing and item.destination_published:
                    if operation is FileOperationKind.COPY:
                        sync_metadata(
                            "apply_copy_replace_metadata", item.destination_path
                        )
                    elif (
                        operation is FileOperationKind.MOVE
                        and item.success
                        and item.source_removed
                        and item.state is FileOperationItemState.MOVED
                        and item.source_path
                    ):
                        sync_metadata(
                            "apply_move_replace_metadata",
                            item.source_path,
                            item.destination_path,
                        )
                    elif operation is FileOperationKind.MOVE and item.source_path:
                        sync_metadata(
                            "apply_partial_move_replace_metadata",
                            item.source_path,
                            item.destination_path,
                        )
                    continue
                if (
                    operation in {FileOperationKind.RENAME, FileOperationKind.MOVE}
                    and item.success
                    and item.source_path
                ):
                    sync_metadata(
                        "relocate_tree",
                        item.source_path,
                        item.destination_path,
                    )
        if metadata_warnings:
            result = replace(
                result,
                metadata_sync_warnings=tuple(metadata_warnings),
            )
        self.operation_completed.emit(result)

    @property
    def undo_entries(self):
        return self._undo_entries

    def invalidate_undo(self) -> None:
        self._undo_entries = ()
