from dataclasses import replace
from threading import Event
from time import monotonic, sleep

import pytest
from PySide6.QtTest import QTest

from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.image_source import FolderImageSource
from app.raster_execution_budget import RASTER_EXECUTION_BUDGET
from tests.test_folder_raster_book_runtime import (
    _request, _unit, _wait_until, _write_folder,
)


def test_capacity_burst_coalesces_and_cancel_discards_pending_dispatch(tmp_path, qapp):
    class CountingRuntime(FolderRasterBookRuntime):
        drives = 0

        def _drive(self):
            self.drives += 1
            super()._drive()

    source = FolderImageSource(_write_folder(tmp_path / 'book', pages=1))
    runtime = CountingRuntime(source, 1, current_extra=True)
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(_request(1, _unit(source, 0)))
        _wait_until(qapp, lambda: bool(frames) and not runtime.has_unfinished_tasks())
        QTest.qWait(20)
        baseline = runtime.drives
        for _ in range(100):
            RASTER_EXECUTION_BUDGET.signals.capacity_available.emit()
        QTest.qWait(20)
        assert 1 <= runtime.drives - baseline <= 2

        baseline = runtime.drives
        for _ in range(100):
            RASTER_EXECUTION_BUDGET.signals.capacity_available.emit()
        runtime.cancel(clear_artifacts=True)
        QTest.qWait(20)
        assert runtime.drives == baseline
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


@pytest.mark.parametrize('finished_before_callback', [False, True])
def test_background_headers_wait_for_observations_without_blocking_cold_current(
    tmp_path, qapp, finished_before_callback,
):
    source = FolderImageSource(_write_folder(tmp_path / 'book', pages=48))
    known = tuple(_unit(source, i) for i in range(48))
    units = (known[0], *(replace(unit, pages=tuple(
        replace(page, known_size=None) for page in unit.pages
    )) for unit in known[1:]))
    original_probe = source.probe_image_size
    header_started = Event()
    release_header = Event()

    def blocked_probe(image_id):
        header_started.set()
        assert release_header.wait(5)
        return original_probe(image_id)

    source.probe_image_size = blocked_probe
    runtime = FolderRasterBookRuntime(
        source, 1, max_active_jobs=2, current_extra=True,
        prefetch_execution_mode='burst', cache_byte_budget=256 << 20,
    )
    request = replace(_request(1, units[0], *units, direction=1),
                      resolve_layout_metadata=True)
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(request)
        _wait_until(qapp, lambda: bool(frames))
        assert runtime.release_continuous_warmup(request_id=1)
        _wait_until(qapp, header_started.is_set)
        if finished_before_callback:
            header_job = next(job for job in runtime._jobs if job.layout_metadata_pages)
            release_header.set()
            deadline = monotonic() + 3
            while not header_job.finished.is_set() and monotonic() < deadline:
                # Deliberately leave its result queued on the GUI thread.
                sleep(.001)
            assert header_job.finished.is_set()
        for _ in range(8):
            runtime._drive()
        assert sum(bool(job.layout_metadata_pages) for job in runtime._jobs) == 1

        # A distant cold current must still start while the old header worker
        # owns its lane. Generation/request checks discard the cancelled batch.
        target = known[-1]
        assert runtime.request(_request(2, target))
        _wait_until(qapp, lambda: any(frame.request_id == 2 for frame in frames))
        assert release_header.is_set() == finished_before_callback
        assert frames[-1].request_id == 2
    finally:
        release_header.set()
        runtime.cancel(clear_artifacts=True)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert runtime.shutdown(wait_msecs=5000)
        source.close()
