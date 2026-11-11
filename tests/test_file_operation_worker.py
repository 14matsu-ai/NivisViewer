from __future__ import annotations

import errno
import logging
from pathlib import Path
from threading import Event, current_thread, main_thread
from time import monotonic

import pytest
from PySide6.QtCore import QThreadPool, QTimer
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


@pytest.mark.parametrize("worker_kind", ["queue", "executor"])
@pytest.mark.parametrize("receipt_fails", [False, True])
def test_undo_receipt_wait_is_between_service_result_and_worker_notification(
    tmp_path, qapp, monkeypatch, caplog, worker_kind, receipt_fails,
):
    from app import file_operation_service as service_module
    from app.file_operation_queue import _ExecutionWorker
    from app.file_operation_worker import _FileOperationWorker

    source = tmp_path / "source.bin"
    source.write_bytes(b"fixture payload")
    destination = tmp_path / "target"
    destination.mkdir()
    entered = Event()
    release = Event()
    original_receipts = service_module.make_undo_entries

    def delayed_receipts(result):
        assert current_thread() is not main_thread()
        entered.set()
        assert release.wait(3)
        if receipt_fails:
            raise PermissionError(errno.EACCES, "private undo message", str(source), 5)
        return original_receipts(result)

    monkeypatch.setattr(service_module, "make_undo_entries", delayed_receipts)
    operation = FileOperationRequest(52, FileOperationKind.COPY, (str(source),), str(destination))
    worker_class = _ExecutionWorker if worker_kind == "queue" else _FileOperationWorker
    worker = worker_class(FileOperationService(), operation, Event())
    completed = []
    worker.signals.completed.connect(completed.append)
    pool = QThreadPool()
    try:
        with caplog.at_level(logging.INFO, logger="nivisviewer.file_operation"):
            pool.start(worker)
            assert entered.wait(2)
            messages = [record.getMessage() for record in caplog.records]
            assert any("file operation result request=52" in message for message in messages)
            assert any("undo receipt start request=52" in message for message in messages)
            assert not any("undo receipt complete" in message for message in messages)
            assert not any("worker result emitted" in message for message in messages)
            ticks = []
            QTimer.singleShot(0, lambda: ticks.append(True))
            qapp.processEvents()
            assert ticks == [True]
            assert not completed
            release.set()
            assert pool.waitForDone(3000)
            qapp.processEvents()
        messages = [record.getMessage() for record in caplog.records]
        receipt_end = next(i for i, message in enumerate(messages) if "undo receipt complete" in message)
        emitted = next(i for i, message in enumerate(messages) if "worker result emitted" in message)
        assert receipt_end < emitted
        assert f"failed={receipt_fails}" in messages[receipt_end]
        assert len(completed) == 1 and completed[0].items[0].success
        assert bool(completed[0].undo_entries) is not receipt_fails
        assert (destination / source.name).read_bytes() == source.read_bytes()
        assert str(tmp_path) not in caplog.text
        assert "private undo message" not in caplog.text
    finally:
        release.set()
        assert pool.waitForDone(3000)
        qapp.processEvents()


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
