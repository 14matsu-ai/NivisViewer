from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.file_operation_service import (
    FileOperationKind,
    FileOperationRequest,
    FileOperationResult,
    FileOperationService,
)
from app.file_operation_worker import FileOperationExecutor


class BlockingService(FileOperationService):
    def __init__(self, started: Event, release: Event) -> None:
        self.started = started
        self.release = release

    def execute(self, request, *, cancelled=None, progress=None):
        self.started.set()
        self.release.wait(2)
        return FileOperationResult(
            request.operation,
            (),
            cancelled=bool(cancelled and cancelled.is_set()),
            request_id=request.request_id,
        )


def request(tmp_path: Path, request_id: int = 1) -> FileOperationRequest:
    return FileOperationRequest(
        request_id,
        FileOperationKind.COPY,
        (str(tmp_path / "source"),),
        str(tmp_path / "destination"),
    )


def test_execute_returns_before_slow_copy_and_qtimer_remains_responsive(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    started = Event()
    release = Event()
    executor = FileOperationExecutor(BlockingService(started, release))
    try:
        before = monotonic()
        assert executor.execute(request(tmp_path))
        assert monotonic() - before < 0.5
        assert started.wait(1)

        ticks: list[bool] = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        qapp.processEvents()
        assert ticks == [True]
    finally:
        release.set()
        executor.wait_for_done(2000)
        qapp.processEvents()
        executor.close()


def test_executor_serializes_operations_and_reports_cancellation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    started = Event()
    release = Event()
    executor = FileOperationExecutor(BlockingService(started, release))
    completed = []
    executor.operation_completed.connect(completed.append)

    assert executor.execute(request(tmp_path, 1))
    assert started.wait(1)
    assert not executor.execute(request(tmp_path, 2))
    executor.cancel()
    release.set()
    assert executor.wait_for_done(2000)
    qapp.processEvents()

    assert completed and completed[0].cancelled
    executor.close()


def test_close_does_not_wait_and_ignores_late_worker_result(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    started = Event()
    release = Event()
    executor = FileOperationExecutor(BlockingService(started, release))
    completed = []
    executor.operation_completed.connect(completed.append)
    assert executor.execute(request(tmp_path))
    assert started.wait(1)

    before = monotonic()
    executor.close()
    elapsed = monotonic() - before
    release.set()
    assert executor.wait_for_done(2000)
    qapp.processEvents()

    assert elapsed < 0.5
    assert completed == []
