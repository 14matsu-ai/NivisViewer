from pathlib import Path
from threading import Event

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
