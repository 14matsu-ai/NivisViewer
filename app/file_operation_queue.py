from __future__ import annotations

from .i18n import tr


import os
from collections import deque
from dataclasses import dataclass, replace
from enum import StrEnum
from threading import Event, Lock

from PySide6.QtCore import (
    QEventLoop,
    QObject,
    QRunnable,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)

from .file_operation_plan import (
    ConflictResolution,
    FileConflictKind,
    FileOperationPlan,
    FileOperationPlanner,
    FileOperationState,
)
from .file_operation_service import (
    FileOperationErrorCode,
    FileOperationItemResult,
    FileOperationRequest,
    FileOperationResult,
    FileOperationService,
)


class FileOperationQueueState(StrEnum):
    RUNNING = "running"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


@dataclass
class _QueueEntry:
    request: FileOperationRequest
    operation_id: str
    state: FileOperationState = FileOperationState.PREPARING
    plan: FileOperationPlan | None = None
    cancelled: Event | None = None


class _PreflightSignals(QObject):
    completed = Signal(object, object)


class _PreflightWorker(QRunnable):
    def __init__(
        self,
        planner: FileOperationPlanner,
        request: FileOperationRequest,
        cancelled: Event,
    ) -> None:
        super().__init__()
        self.planner = planner
        self.request = request
        self.cancelled = cancelled
        self.signals = _PreflightSignals()

    @Slot()
    def run(self) -> None:
        try:
            plan = self.planner.prepare(self.request, cancelled=self.cancelled)
        except BaseException as exc:
            plan = FileOperationPlan(
                operation_id=self.request.operation_id,
                request_id=self.request.request_id,
                operation=self.request.operation,
                source_paths=self.request.source_paths,
                destination_directory=self.request.destination_directory,
                errors=(tr('事前確認に失敗しました: {p0}', p0=exc),),
                state=FileOperationState.FAILED,
            )
        self.signals.completed.emit(self.request, plan)


class _ExecutionSignals(QObject):
    progress = Signal(object)
    completed = Signal(object)


class _ExecutionWorker(QRunnable):
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
        self.signals = _ExecutionSignals()

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


class FileOperationQueue(QObject):
    """Application-wide serial preflight/execution queue."""

    operation_queued = Signal(object)
    operation_preparing = Signal(object)
    plan_ready = Signal(object)
    conflicts_required = Signal(object)
    operation_started = Signal(object)
    operation_progress = Signal(object)
    operation_completed = Signal(object)
    state_changed = Signal(str, object)
    queue_changed = Signal()
    shutdown_progress = Signal(object)
    shutdown_finished = Signal()
    shutdown_failed = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        service: FileOperationService | None = None,
        planner: FileOperationPlanner | None = None,
        history_limit: int = 50,
    ) -> None:
        super().__init__(parent)
        self.service = service or FileOperationService()
        self.planner = planner or FileOperationPlanner()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._pending: deque[_QueueEntry] = deque()
        self._active: _QueueEntry | None = None
        self._history: deque[FileOperationResult] = deque(
            maxlen=max(1, int(history_limit))
        )
        self._lock = Lock()
        self._closed = False
        self._lifecycle = FileOperationQueueState.RUNNING
        self._shutdown_emitted = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.timeout.connect(self._on_shutdown_timeout)

    @property
    def lifecycle(self) -> FileOperationQueueState:
        with self._lock:
            return self._lifecycle

    @property
    def is_shutting_down(self) -> bool:
        return self.lifecycle is FileOperationQueueState.SHUTTING_DOWN

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active is not None or bool(self._pending)

    @property
    def active_operation(self) -> FileOperationRequest | None:
        with self._lock:
            return self._active.request if self._active is not None else None

    @property
    def active_state(self) -> FileOperationState | None:
        with self._lock:
            return self._active.state if self._active is not None else None

    @property
    def queued_requests(self) -> tuple[FileOperationRequest, ...]:
        with self._lock:
            return tuple(entry.request for entry in self._pending)

    @property
    def history(self) -> tuple[FileOperationResult, ...]:
        with self._lock:
            return tuple(self._history)

    def enqueue(self, request: FileOperationRequest) -> bool:
        operation_id = request.operation_id or str(request.request_id)
        request = replace(request, operation_id=operation_id)
        with self._lock:
            if (
                self._closed
                or self._contains_id_locked(operation_id)
                or self._request_conflicts_locked(request)
            ):
                return False
            entry = _QueueEntry(request, operation_id)
            self._pending.append(entry)
        self.operation_queued.emit(request)
        self.queue_changed.emit()
        self._start_next()
        return True

    execute = enqueue

    def resolve_conflicts(
        self,
        operation_id: str,
        resolutions: dict[str, ConflictResolution | str],
        *,
        apply_to_same_kind: bool = False,
    ) -> bool:
        with self._lock:
            entry = self._active
            if (
                entry is None
                or entry.operation_id != str(operation_id)
                or entry.state is not FileOperationState.WAITING_FOR_CONFLICTS
                or entry.plan is None
            ):
                return False
            plan = entry.plan
        normalized: dict[str, ConflictResolution] = {}
        kind_defaults: dict[FileConflictKind, ConflictResolution] = {}
        for conflict in plan.conflicts:
            raw = resolutions.get(conflict.conflict_id, conflict.default_resolution)
            try:
                resolution = (
                    raw if isinstance(raw, ConflictResolution) else ConflictResolution(str(raw))
                )
            except ValueError:
                resolution = ConflictResolution.SKIP
            if resolution not in conflict.allowed_resolutions:
                resolution = conflict.default_resolution
            normalized[conflict.conflict_id] = resolution
            if apply_to_same_kind and conflict.conflict_id in resolutions:
                kind_defaults[conflict.kind] = resolution
        if apply_to_same_kind:
            for conflict in plan.conflicts:
                resolution = kind_defaults.get(conflict.kind)
                if resolution in conflict.allowed_resolutions:
                    normalized[conflict.conflict_id] = resolution
        if any(value is ConflictResolution.CANCEL for value in normalized.values()):
            self.cancel(operation_id)
            return True
        destination_resolutions = tuple(
            (conflict.destination_path, normalized[conflict.conflict_id].value)
            for conflict in plan.conflicts
            if conflict.destination_path
        )
        collision_values = set(normalized.values())
        if collision_values == {ConflictResolution.KEEP_BOTH}:
            policy = "keep_both"
        elif collision_values == {ConflictResolution.REPLACE}:
            policy = "replace"
        elif collision_values == {ConflictResolution.MERGE}:
            policy = "merge"
        else:
            policy = "skip"
        from .file_operation_service import FileCollisionPolicy

        with self._lock:
            if self._active is not entry:
                return False
            entry.request = replace(
                entry.request,
                collision_policy=FileCollisionPolicy(policy),
                collision_resolutions=destination_resolutions,
                planned_total_bytes=plan.total_bytes,
                planned_item_bytes=tuple(
                    (item.source_path, item.size_bytes) for item in plan.items
                ),
            )
            entry.state = FileOperationState.READY
        self._emit_state(entry)
        self._start_execution(entry)
        return True

    def cancel(self, operation_id: str | None = None) -> bool:
        cancelled_result: FileOperationResult | None = None
        with self._lock:
            entry = self._active
            if entry is not None and (
                operation_id is None or entry.operation_id == str(operation_id)
            ):
                if entry.state is FileOperationState.CANCELLING:
                    return True
                if entry.cancelled is not None:
                    entry.cancelled.set()
                if entry.state is FileOperationState.WAITING_FOR_CONFLICTS:
                    entry.state = FileOperationState.CANCELLED
                    self._active = None
                    cancelled_result = FileOperationResult(
                        entry.request.operation,
                        (),
                        cancelled=True,
                        request_id=entry.request.request_id,
                        operation_id=entry.operation_id,
                    )
                else:
                    entry.state = FileOperationState.CANCELLING
                target = entry
            else:
                target = next(
                    (
                        pending
                        for pending in self._pending
                        if operation_id is not None
                        and pending.operation_id == str(operation_id)
                    ),
                    None,
                )
                if target is None:
                    return False
                self._pending.remove(target)
                target.state = FileOperationState.CANCELLED
                cancelled_result = FileOperationResult(
                    target.request.operation,
                    (),
                    cancelled=True,
                    request_id=target.request.request_id,
                    operation_id=target.operation_id,
                )
        self._emit_state(target)
        if cancelled_result is not None:
            self._finish_result(cancelled_result)
            self._start_next()
        self.queue_changed.emit()
        return True

    def has_path_conflict(self, request: FileOperationRequest) -> bool:
        with self._lock:
            return self._request_conflicts_locked(request)

    def close(self, *, cancel_running: bool = True) -> None:
        self.begin_shutdown(cancel_active=cancel_running)

    def begin_shutdown(
        self,
        *,
        cancel_active: bool = True,
        timeout_msecs: int = 10_000,
    ) -> bool:
        with self._lock:
            if self._lifecycle is FileOperationQueueState.STOPPED:
                return False
            if self._lifecycle is FileOperationQueueState.SHUTTING_DOWN:
                return False
            self._lifecycle = FileOperationQueueState.SHUTTING_DOWN
            self._closed = True
            pending = tuple(self._pending)
            self._pending.clear()
            active = self._active
            if (
                active is not None
                and active.state is FileOperationState.WAITING_FOR_CONFLICTS
            ):
                active.state = FileOperationState.CANCELLED
                self._active = None
                active = None
        if cancel_active and active is not None and active.cancelled is not None:
            active.cancelled.set()
        for entry in pending:
            entry.state = FileOperationState.CANCELLED
        self.queue_changed.emit()
        self.shutdown_progress.emit(
            {
                "active": active.operation_id if active is not None else None,
                "cancelled_waiting": len(pending),
            }
        )
        if active is None:
            self._finish_shutdown()
        elif timeout_msecs > 0:
            self._shutdown_timer.start(max(1, int(timeout_msecs)))
        return True

    def wait_for_done(self, msecs: int = 5000) -> bool:
        if not self.busy:
            return True
        timeout = max(0, int(msecs))
        if timeout <= 0:
            return False
        loop = QEventLoop()
        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)
        poll_timer = QTimer()
        poll_timer.setInterval(10)

        def check_done() -> None:
            if not self.busy:
                loop.quit()

        poll_timer.timeout.connect(check_done)
        timeout_timer.timeout.connect(loop.quit)
        poll_timer.start()
        timeout_timer.start(timeout)
        loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        poll_timer.stop()
        timeout_timer.stop()
        return not self.busy

    def _start_next(self) -> None:
        with self._lock:
            if self._closed or self._active is not None or not self._pending:
                return
            entry = self._pending.popleft()
            entry.cancelled = Event()
            entry.state = FileOperationState.PREPARING
            self._active = entry
        self.operation_preparing.emit(entry.request)
        self._emit_state(entry)
        self.queue_changed.emit()
        worker = _PreflightWorker(self.planner, entry.request, entry.cancelled)
        worker.signals.completed.connect(self._on_plan_ready)
        self._pool.start(worker)

    @Slot(object, object)
    def _on_plan_ready(
        self,
        request: FileOperationRequest,
        plan: FileOperationPlan,
    ) -> None:
        with self._lock:
            entry = self._active
            if entry is None or entry.operation_id != request.operation_id:
                return
            entry.plan = plan
            if plan.state is FileOperationState.CANCELLED:
                entry.state = FileOperationState.CANCELLED
            elif plan.errors:
                entry.state = FileOperationState.FAILED
            elif plan.conflicts:
                entry.state = FileOperationState.WAITING_FOR_CONFLICTS
            else:
                entry.state = FileOperationState.READY
                entry.request = replace(
                    entry.request,
                    planned_total_bytes=plan.total_bytes,
                    planned_item_bytes=tuple(
                        (item.source_path, item.size_bytes) for item in plan.items
                    ),
                )
        self.plan_ready.emit(plan)
        self._emit_state(entry)
        if entry.state is FileOperationState.WAITING_FOR_CONFLICTS:
            self.conflicts_required.emit(plan)
            return
        if entry.state in {FileOperationState.FAILED, FileOperationState.CANCELLED}:
            items = tuple(
                FileOperationItemResult(
                    source,
                    plan.destination_directory,
                    False,
                    (
                        FileOperationErrorCode.CANCELLED.value
                        if entry.state is FileOperationState.CANCELLED
                        else FileOperationErrorCode.IO_ERROR.value
                    ),
                    "; ".join(plan.errors) if plan.errors else tr('操作がキャンセルされました'),
                )
                for source in (plan.source_paths or (None,))
            )
            result = FileOperationResult(
                request.operation,
                items,
                cancelled=entry.state is FileOperationState.CANCELLED,
                request_id=request.request_id,
                operation_id=entry.operation_id,
            )
            with self._lock:
                if self._active is entry:
                    self._active = None
            self._finish_result(result)
            self._start_next()
            if self.is_shutting_down:
                self._finish_shutdown()
            return
        self._start_execution(entry)

    def _start_execution(self, entry: _QueueEntry) -> None:
        with self._lock:
            if self._closed or self._active is not entry:
                if self._active is entry:
                    entry.state = FileOperationState.CANCELLED
                    self._active = None
                stopped = self._closed
            else:
                stopped = False
                entry.state = FileOperationState.RUNNING
        if stopped:
            self._emit_state(entry)
            self._finish_shutdown()
            return
        self._emit_state(entry)
        self.operation_started.emit(entry.request)
        worker = _ExecutionWorker(self.service, entry.request, entry.cancelled or Event())
        worker.signals.progress.connect(self.operation_progress)
        worker.signals.completed.connect(self._on_execution_completed)
        self._pool.start(worker)

    @Slot(object)
    def _on_execution_completed(self, result: FileOperationResult) -> None:
        with self._lock:
            entry = self._active
            if entry is None or entry.operation_id != result.operation_id:
                return
            entry.state = (
                FileOperationState.CANCELLED
                if result.cancelled
                else (
                    FileOperationState.FAILED
                    if result.failures and not result.successes
                    else FileOperationState.COMPLETED
                )
            )
            self._active = None
        self._emit_state(entry)
        self._finish_result(result)
        self.queue_changed.emit()
        self._start_next()
        if self.is_shutting_down:
            self._finish_shutdown()

    def _finish_result(self, result: FileOperationResult) -> None:
        with self._lock:
            self._history.append(result)
        if not self._closed:
            self.operation_completed.emit(result)

    def _emit_state(self, entry: _QueueEntry) -> None:
        if not self._closed:
            self.state_changed.emit(entry.operation_id, entry.state)

    def _finish_shutdown(self) -> None:
        with self._lock:
            if (
                self._lifecycle is FileOperationQueueState.STOPPED
                or self._active is not None
            ):
                return
            self._lifecycle = FileOperationQueueState.STOPPED
            emit = not self._shutdown_emitted
            self._shutdown_emitted = True
        self._shutdown_timer.stop()
        if emit:
            self.shutdown_finished.emit()

    def _on_shutdown_timeout(self) -> None:
        with self._lock:
            if self._lifecycle is not FileOperationQueueState.SHUTTING_DOWN:
                return
            active = self._active
        operation_id = active.operation_id if active is not None else ""
        self.shutdown_failed.emit(
            tr('ファイル操作の終了を待機中です: {p0}', p0=operation_id)
        )

    def _contains_id_locked(self, operation_id: str) -> bool:
        return bool(
            (self._active is not None and self._active.operation_id == operation_id)
            or any(entry.operation_id == operation_id for entry in self._pending)
        )

    def _request_conflicts_locked(self, request: FileOperationRequest) -> bool:
        request_sources, request_targets = self._request_footprint(request)
        entries = ([self._active] if self._active is not None else []) + list(
            self._pending
        )
        for entry in entries:
            sources, targets = self._request_footprint(entry.request)
            if self._paths_overlap(request_sources, sources):
                return True
            if self._paths_overlap(request_sources, targets):
                return True
            if self._paths_overlap(request_targets, sources):
                return True
            if self._paths_overlap(request_targets, targets):
                return True
        return False

    @staticmethod
    def _request_footprint(
        request: FileOperationRequest,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        sources = tuple(
            os.path.normcase(os.path.abspath(os.path.normpath(path))).casefold()
            for path in request.source_paths
        )
        targets: list[str] = []
        if request.destination_directory:
            destination = os.path.abspath(
                os.path.normpath(request.destination_directory)
            )
            for source in request.source_paths:
                targets.append(
                    os.path.normcase(
                        os.path.join(destination, os.path.basename(source))
                    ).casefold()
                )
            if not request.source_paths and request.new_name:
                targets.append(
                    os.path.normcase(
                        os.path.join(destination, request.new_name)
                    ).casefold()
                )
        elif (
            request.new_name
            and len(request.source_paths) == 1
        ):
            targets.append(
                os.path.normcase(
                    os.path.join(
                        os.path.dirname(request.source_paths[0]),
                        request.new_name,
                    )
                ).casefold()
            )
        return sources, tuple(targets)

    @staticmethod
    def _paths_overlap(first: tuple[str, ...], second: tuple[str, ...]) -> bool:
        for left in first:
            for right in second:
                try:
                    common = os.path.commonpath((left, right))
                except ValueError:
                    continue
                if common in {left, right}:
                    return True
        return False
