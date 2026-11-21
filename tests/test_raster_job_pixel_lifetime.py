"""Worker pixels end before native fences; queued GUI results keep their own data."""
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import replace
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QThreadPool, Qt, Slot
from PySide6.QtGui import QImage

from app import zip_raster_book_runtime as raster
from app.raster_execution_budget import RASTER_EXECUTION_BUDGET as budget
from app.zip_raster_book_runtime import _CachedSource, _SourceKey, _UnitKey, _ZipRasterUnitJob
from tests.test_near_prefetch_parallel import known_unit
from tests.test_viewer_parallel_trials import GateSource, close_runtime
from tests.test_zip_raster_book_runtime import _request, _write_zip, _wait_until


class Receiver(QObject):
    def __init__(self):
        super().__init__()
        self.results = []

    @Slot(object)
    def completed(self, result):
        self.results.append(result)


def make_job(source, *, cached=False, emergency=True):
    unit = known_unit(0, 1)
    spec = raster.ZipRasterRenderSpec((640, 480))
    sources = {}
    if cached:
        image = QImage(120, 180, QImage.Format.Format_ARGB32)
        image.fill(0xff112233)
        key = _SourceKey(1, id(source), '0.png', spec.adjustments, (120, 180), True)
        sources['0.png'] = _CachedSource(key, 0, image, (120, 180), False)
    job = _ZipRasterUnitJob(serial=1, key=_UnitKey(1, id(source), unit.identity, False, spec),
                            request_id=1, source=source, unit=unit, cached_sources=sources,
                            spread_parallel=True, emergency_allowed=emergency)
    return job, sources


def assert_released(job):
    assert job._helper_promotion_closed
    assert job._helper_future is None
    assert job._helper_operation is None
    assert job._helper_page is None
    assert job.cached_sources == {}
    assert not job.autoDelete()


@pytest.fixture
def source(tmp_path, qapp):
    budget.configure(4)
    value = GateSource(_write_zip(tmp_path, pages=4))
    yield value
    value.unblock()
    value.close()
    budget.configure(3)


@pytest.mark.parametrize('cached', [False, True])
def test_completed_job_releases_pixels_before_queued_result_is_delivered(source, qapp, cached):
    job, originals = make_job(source, cached=cached)
    receiver = Receiver()
    job.signals.completed.connect(receiver.completed, Qt.ConnectionType.QueuedConnection)
    pool = QThreadPool()
    pool.start(job)
    assert pool.waitForDone(3000)
    assert job.finished.is_set() and not receiver.results
    assert_released(job)
    if cached:
        assert not originals['0.png'].qimage.isNull()  # Do not clear the cache owner's copy.
    qapp.processEvents()
    assert len(receiver.results) == 1
    result = receiver.results[0]
    assert len(result.pages) == 2 and not result.cancelled
    assert all(page.error is None and not page.display_qimage.isNull() for page in result.pages)
    assert budget.debug_values()['owned_permits'] == 0


@pytest.mark.parametrize('cancel', [False, True])
def test_parent_failure_or_cancel_drains_running_helper_before_fences(source, qapp, monkeypatch, cancel):
    source.block = {'0.png', '1.png'}
    job, _ = make_job(source)
    decode = job._decode_page

    def decode_page(page):
        if page.page_index == 0 and not cancel:
            assert source.entered['1.png'].wait(2)
            raise RuntimeError('parent decode failed')
        return decode(page)

    monkeypatch.setattr(job, '_decode_page', decode_page)
    released = []
    job.native_lane_release = lambda: released.append(True)
    receiver = Receiver()
    job.signals.completed.connect(receiver.completed, Qt.ConnectionType.QueuedConnection)
    pool = QThreadPool()
    try:
        pool.start(job)
        assert source.entered['1.png'].wait(2)
        if cancel:
            assert source.entered['0.png'].wait(2)
            job.cancel()
            source.release['0.png'].set()
        _wait_until(qapp, lambda: job._helper_promotion_closed)
        assert not job.finished.is_set() and not released and not receiver.results
        assert job._helper_future is not None and not job._helper_future.done()
        assert budget.debug_values()['owned_permits'] == 2
        source.release['1.png'].set()
        assert pool.waitForDone(3000)
        assert_released(job)
        assert job.finished.is_set() and released == [True]
        qapp.processEvents()
        assert len(receiver.results) == 1 and receiver.results[0].cancelled is cancel
        if not cancel:
            assert all(page.error == 'parent decode failed' for page in receiver.results[0].pages)
        assert budget.debug_values()['owned_permits'] == 0
    finally:
        source.unblock()
        assert pool.waitForDone(3000)


@pytest.mark.parametrize('failure', ['helper', 'submission', 'dispatch', 'execution'])
def test_worker_exception_paths_drop_pixel_owners_and_complete(source, qapp, monkeypatch, failure):
    job, originals = make_job(source, cached=failure == 'execution')
    signal_owner = job.signals
    receiver = Receiver()
    job.signals.completed.connect(receiver.completed, Qt.ConnectionType.QueuedConnection)
    released = []
    job.native_lane_release = lambda: released.append(True)
    decode = job._decode_page

    def fail(*args, **kwargs):
        raise RuntimeError(failure + ' failed')

    if failure == 'helper':
        monkeypatch.setattr(job, '_decode_page', lambda page: fail() if page.page_index == 1 else decode(page))
    elif failure == 'submission':
        monkeypatch.setattr(budget, 'helper', fail)
    elif failure == 'dispatch':
        completed = job.signals.completed
        job.signals = SimpleNamespace(dispatchReady=SimpleNamespace(emit=fail), completed=completed)
    else:
        class FailedLease:
            def __enter__(self):
                fail()

            def __exit__(self, *args):
                pass
        job.execution_lease = FailedLease()
    pool = QThreadPool()
    pool.start(job)
    assert pool.waitForDone(3000)
    assert_released(job)
    assert job.finished.is_set() and released == [True]
    qapp.processEvents()
    assert len(receiver.results) == 1
    assert all(page.error == failure + ' failed' for page in receiver.results[0].pages)
    assert budget.debug_values()['owned_permits'] == 0


@pytest.mark.parametrize('failure', ['execution', 'context', 'metadata'])
def test_header_job_exception_keeps_empty_metadata_contract(source, qapp, monkeypatch, failure):
    job, _ = make_job(source, cached=True)
    job.layout_metadata_pages = job.unit.pages
    receiver = Receiver()
    job.signals.completed.connect(receiver.completed, Qt.ConnectionType.QueuedConnection)

    @contextmanager
    def fail(*args, **kwargs):
        raise RuntimeError('header context failed')
        yield

    if failure == 'execution':
        job.execution_lease = fail()
    elif failure == 'context':
        from app import vector_image_decoder
        monkeypatch.setattr(vector_image_decoder, 'vector_context', fail)
    else:
        monkeypatch.setattr(budget, 'native_operation', fail)
    job.run()
    qapp.processEvents()
    assert len(receiver.results) == 1
    result = receiver.results[0]
    assert result.pages == ()
    assert result.layout_metadata is not None and result.layout_metadata.observations == ()
    assert result.layout_metadata.source_epoch == job.key.source_epoch
    assert result.layout_metadata.source_identity == job.key.source_identity
    assert_released(job)
    assert budget.debug_values()['owned_permits'] == 0


def test_dispatch_failure_waits_for_helper_with_parent_lease_still_owned(source, monkeypatch):
    source.block = {'1.png'}
    job, _ = make_job(source)
    results = []

    def fail():
        assert source.entered['1.png'].wait(2)
        raise RuntimeError('dispatch failed')

    job.signals = SimpleNamespace(dispatchReady=SimpleNamespace(emit=fail),
                                  completed=SimpleNamespace(emit=results.append))
    worker = Thread(target=job.run)
    try:
        worker.start()
        assert source.entered['1.png'].wait(2)
        # Inspect the real permits while the helper's source operation is blocked.
        assert not job.finished.wait(.05)
        assert budget.debug_values()['owned_permits'] == 2
        assert not results
    finally:
        source.unblock()
        worker.join(3)
    assert not worker.is_alive()
    assert_released(job)
    assert results and job.finished.is_set()


@pytest.mark.parametrize('telemetry_fails', [False, True])
def test_cached_sources_survive_through_adaptive_telemetry_only(source, monkeypatch, telemetry_fails):
    job, originals = make_job(source, cached=True)
    job.auto_memory_reserved = True
    observed, released = [], []

    def observe(*args):
        assert job.cached_sources == originals
        observed.append(args)
        if telemetry_fails:
            raise RuntimeError('optional telemetry failed')

    def release(owner):
        assert owner is job and job.cached_sources == {}
        released.append(True)

    monkeypatch.setattr(budget, 'observe_auto_job', observe)
    monkeypatch.setattr(budget, 'release_auto_memory', release)
    job.run()
    assert observed and released == [True]
    assert not job.auto_memory_reserved
    assert_released(job)


@pytest.mark.parametrize('cancel_first', [False, True])
def test_removed_before_start_clears_only_worker_cache_and_releases_once(source, cancel_first):
    job, originals = make_job(source, cached=True)
    released = []
    job.native_lane_release = lambda: released.append(True)
    if cancel_first:
        job.cancel()
    job.mark_removed_before_start()
    job.mark_removed_before_start()
    assert not job.started.is_set() and job.finished.is_set()
    assert_released(job)
    assert originals and released == [True]
    assert not source.calls


def test_promotion_after_drain_snapshot_cannot_submit_and_wait_does_not_hold_token_lock(source, monkeypatch):
    job, _ = make_job(source)
    waiting, release = Event(), Event()

    class SlowFuture:
        def result(self):
            assert job._token_lock.acquire(blocking=False)
            job._token_lock.release()
            waiting.set()
            assert release.wait(3)

    job._helper_future = SlowFuture()
    job._helper_operation = lambda: None
    job._helper_page = job.unit.pages[1]
    job.helper_decided.set()
    monkeypatch.setattr(budget, 'helper', lambda *a, **kw: pytest.fail('late promotion submitted'))
    errors = []

    def drain():
        try:
            job._drain_helper(raise_error=True)
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=drain)
    try:
        thread.start()
        assert waiting.wait(2)
        job.adopt_request(9, as_current=True)
        job.cancel()
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and not errors
    assert_released(job)


@pytest.mark.parametrize('helper_fails', [False, True])
def test_promotion_wins_lock_then_drain_joins_that_exact_future(source, monkeypatch, helper_fails):
    job, _ = make_job(source)
    entered, publish_future, waiting, finish = Event(), Event(), Event(), Event()
    job._helper_operation = lambda: None
    job._helper_page = job.unit.pages[1]
    job.helper_decided.set()
    future = Future()
    calls = []

    def submit(operation, **kwargs):
        calls.append(operation)
        entered.set()
        assert publish_future.wait(3)
        return future

    monkeypatch.setattr(budget, 'helper', submit)
    promote = Thread(target=lambda: job.adopt_request(2, as_current=True))

    def drain():
        waiting.set()
        job._drain_helper()
        finish.set()

    closer = Thread(target=drain)
    try:
        promote.start()
        assert entered.wait(2)
        closer.start()
        assert waiting.wait(2)
        publish_future.set()
        promote.join(2)
        assert not finish.wait(.05)
        job.adopt_request(3, as_current=True)
        assert len(calls) == 1
        if helper_fails:
            future.set_exception(RuntimeError('helper failed'))
        else:
            future.set_result(None)
    finally:
        publish_future.set()
        if not future.done():
            future.set_result(None)
        promote.join(3)
        closer.join(3)
    assert not promote.is_alive() and not closer.is_alive() and finish.is_set()
    assert_released(job)


@pytest.mark.parametrize('outcome', ['refused', 'queued_cancel'])
def test_runtime_start_refusal_and_unstarted_cancel_release_cached_payload(source, qapp, monkeypatch, outcome):
    runtime = raster.ZipRasterBookRuntime(source, 1)
    frames, made = [], []
    runtime.frameReady.connect(frames.append)
    constructor = raster._ZipRasterUnitJob

    def create(**kwargs):
        job = constructor(**kwargs)
        made.append((job, bool(job.cached_sources)))
        return job

    def start(*args):
        if outcome == 'refused':
            raise RuntimeError('pool refused job')

    try:
        runtime.request(_request(1, known_unit(0)))
        _wait_until(qapp, lambda: bool(frames))
        monkeypatch.setattr(raster, '_ZipRasterUnitJob', create)
        monkeypatch.setattr(runtime._thread_pool, 'start', start)
        monkeypatch.setattr(runtime._thread_pool, 'tryTake', lambda job: job is made[-1][0])
        runtime.request(_request(2, known_unit(0), spec=replace(_request(1, known_unit(0)).render_spec, rotation=90)))
        job, was_cached = made[-1]
        assert was_cached
        if outcome == 'queued_cancel':
            runtime.cancel()
        assert_released(job)
        assert job.finished.is_set() and not job.started.is_set()
        assert job not in runtime._jobs and job not in runtime._inflight_reservations
    finally:
        close_runtime(runtime, source, qapp)
