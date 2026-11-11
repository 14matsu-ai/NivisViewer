from pathlib import Path
from threading import Event
import errno
import logging

import pytest

from app.file_operation_plan import FileOperationPlanner
from app.file_operation_queue import FileOperationQueue, FileOperationQueueState
from app.file_operation_service import FileOperationKind, FileOperationRequest


@pytest.mark.parametrize("action", ["cancel", "shutdown", "shutdown_wait"])
def test_cancelled_preflight_does_not_reenter_conflict_wait(tmp_path: Path, qapp, action):
    prepared = Event()
    release = Event()

    class DeferredPlanner(FileOperationPlanner):
        def prepare(self, request, *, cancelled=None):
            plan = super().prepare(request, cancelled=cancelled)
            assert plan.conflicts
            prepared.set()
            assert release.wait(3)
            return plan

    source = tmp_path / "本.cbz"
    source.write_bytes(b"new")
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / source.name).write_bytes(b"existing")
    queue = FileOperationQueue(planner=DeferredPlanner())
    conflicts, started, completed = [], [], []
    queue.conflicts_required.connect(conflicts.append)
    queue.operation_started.connect(started.append)
    queue.operation_completed.connect(completed.append)
    try:
        assert queue.enqueue(FileOperationRequest(
            1, FileOperationKind.COPY, (str(source),), str(destination),
        ))
        assert prepared.wait(2)
        if action == "cancel":
            assert queue.cancel()
        else:
            assert queue.begin_shutdown(cancel_active=action == "shutdown")
        release.set()
        assert queue._pool.waitForDone(3000)
        qapp.processEvents()

        assert conflicts == []
        assert started == []
        assert not queue.busy
        assert len(queue.history) == 1 and queue.history[0].cancelled
        if action == "cancel":
            assert len(completed) == 1 and completed[0].cancelled
        else:
            assert queue.lifecycle is FileOperationQueueState.STOPPED
        assert source.read_bytes() == b"new"
        assert (destination / source.name).read_bytes() == b"existing"
    finally:
        release.set()
        queue._pool.waitForDone(3000)
        queue.close()
        qapp.processEvents()


def test_preflight_failure_has_path_free_normal_diagnostics(qapp, caplog):
    class FailingPlanner(FileOperationPlanner):
        def prepare(self, request, *, cancelled=None):
            raise PermissionError(errno.EACCES, "private server message", request.source_paths[0], 5)

    queue = FileOperationQueue(planner=FailingPlanner())
    completed = []
    queue.operation_completed.connect(completed.append)
    try:
        with caplog.at_level(logging.INFO, logger="nivisviewer.file_operation"):
            assert queue.enqueue(FileOperationRequest(
                22, FileOperationKind.COPY, (r"\\private-server\share\book.zip",), r"C:\target",
            ))
            assert queue.wait_for_done(3000)
            qapp.processEvents()
        assert len(completed) == 1
        assert not completed[0].items[0].success
        assert "stage=preflight" in caplog.text
        assert "winerror=5" in caplog.text
        assert "state=failed" in caplog.text
        assert "private-server" not in caplog.text
        assert "private server message" not in caplog.text
    finally:
        queue.close()
        queue.wait_for_done(3000)
        qapp.processEvents()


@pytest.mark.parametrize("outcome", ["released", "cancelled", "shutdown", "shutdown_wait"])
def test_execution_gate_logs_wait_once_and_records_its_end(qapp, monkeypatch, caplog, outcome):
    from app import file_operation_queue as queue_module
    from app.file_operation_plan import FileOperationState

    now = 0.0
    monkeypatch.setattr(queue_module, "monotonic", lambda: now)
    ready = False
    queue = FileOperationQueue(execution_ready=lambda: ready)
    request = FileOperationRequest(
        45, FileOperationKind.COPY, (r"\\private-server\share\source",), r"C:\destination",
    )
    entry = queue_module._QueueEntry(
        request, "gate-45", state=FileOperationState.READY, cancelled=Event(),
    )
    queue._active = entry
    workers, started = [], []
    monkeypatch.setattr(queue._pool, "start", workers.append)
    queue.operation_started.connect(started.append)
    try:
        with caplog.at_level(logging.INFO, logger="nivisviewer.file_operation"):
            queue._start_execution(entry)
            for _ in range(20):
                now += .01
                queue._execution_gate_timer.stop()
                queue._retry_gated_execution()
            assert not workers
            assert entry.state is FileOperationState.READY
            waiting = [record.getMessage() for record in caplog.records if "handle gate" in record.getMessage()]
            assert len(waiting) == 1
            assert "state=waiting" in waiting[0]

            now += 2.0
            if outcome == "released":
                ready = True
            elif outcome == "cancelled":
                assert queue.cancel()
            else:
                assert queue.begin_shutdown(cancel_active=outcome == "shutdown")
            queue._execution_gate_timer.stop()
            queue._retry_gated_execution()
            queue._retry_gated_execution()

        messages = [record.getMessage() for record in caplog.records if "handle gate" in record.getMessage()]
        assert len(messages) == 2
        expected_outcome = "shutdown" if outcome == "shutdown_wait" else outcome
        assert f"state={expected_outcome}" in messages[1]
        assert "elapsed_ms=2200.0" in messages[1]
        assert "request=45" in messages[1]
        assert "private-server" not in caplog.text
        assert "C:\\destination" not in caplog.text
        assert entry.gate_wait_started_at is None
        if outcome.startswith("shutdown"):
            assert not workers
            assert queue.lifecycle is FileOperationQueueState.STOPPED
        else:
            assert len(workers) == len(started) == 1
            assert entry.cancelled.is_set() is (outcome == "cancelled")
    finally:
        queue._execution_gate_timer.stop()
        queue._active = None
        queue.close()
        qapp.processEvents()
