"""Compatibility, immutable read ownership and read/generation isolation."""
from dataclasses import replace
from threading import Event, Thread
from time import monotonic, sleep

import pytest
from PIL import Image
from PySide6.QtGui import QImage

from app.browser_model import BrowserItemKind
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec
from app.browser_generation_budget import BrowserGenerationBudget
from app.image_work_coordinator import ImageWorkCoordinator, ImageWorkPriority
from PySide6.QtCore import QRunnable, QTimer
from types import SimpleNamespace
from tests.test_thumbnail_provider import make_item


def source(path):
    with Image.new('RGB', (512, 768), 'navy') as image:
        image.save(path)
    return make_item(path, BrowserItemKind.IMAGE)


def pixels():
    image = QImage(96, 96, QImage.Format.Format_RGBA8888)
    image.fill(0xFF2468AC)
    return image


@pytest.mark.parametrize('version', [3, 4])
def test_compatible_legacy_png_reuses_without_generation_or_copy(tmp_path, qapp, version):
    item = source(tmp_path / 'image.png')
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    assert cache.put(item, replace(spec, implementation_version=version), pixels())
    saved_files = set(cache.files_dir.iterdir())
    provider = BrowserThumbnailProvider(disk_cache=cache,
        loader=lambda *_: pytest.fail('Compatible old raster should not regenerate'))
    try:
        generation = provider.begin_generation()
        assert provider.request(item, spec, generation=generation)
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        assert provider.cache_statistics()['disk_hit'] == 1
        assert provider.cache_statistics()['generated'] == 0
        assert set(cache.files_dir.iterdir()) == saved_files
        assert cache.statistics()['entry_count'] == 1
        wrong_crop = replace(spec, crop_mode='center_crop')
        wrong_alpha = replace(spec, preserve_alpha=True)
        wrong_quality = replace(spec, encoder_quality=80)
        wrong_matte = replace(spec, matte_color='#000000')
        assert cache.get_suitable(item, wrong_crop) is None
        assert cache.get_suitable(item, wrong_alpha) is None
        assert cache.get_suitable(item, wrong_quality) is None
        assert cache.get_suitable(item, wrong_matte) is None
        assert cache.get_suitable(item, replace(spec, implementation_version=6)) is None
    finally:
        provider.close()
        cache.close()


@pytest.mark.parametrize('clear', [False, True])
def test_decode_lease_does_not_hold_lock_or_resurrect_deleted_entry(tmp_path, qapp, clear):
    item = source(tmp_path / 'image.png')
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    assert cache.put(item, spec, pixels())
    other = ThumbnailDiskCache(tmp_path / 'cache')
    entered, release = Event(), Event()
    original = cache._read_qimage
    results = []
    def decode(handle):
        entered.set()
        assert release.wait(5)
        return original(handle)
    cache._read_qimage = decode
    thread = Thread(target=lambda: results.append(cache.get_suitable(item, spec)))
    thread.start()
    try:
        assert entered.wait(2)
        # A second cache object shares file-lifetime state, not the codec lock.
        assert other._lock.acquire(timeout=.5)
        other._lock.release()
        if clear:
            assert other.clear_all()
        else:
            item.path.unlink()
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert results == [None]
        assert not cache._read_leases
    finally:
        release.set()
        thread.join(5)
        cache.close()
        other.close()


def test_persisted_visible_requests_finish_while_generation_is_blocked(tmp_path, qapp):
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    heavy = source(tmp_path / 'heavy.png')
    saved = [source(tmp_path / f'saved-{i}.png') for i in range(24)]
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    for item in saved:
        assert cache.put(item, spec, pixels())
    entered, release = Event(), Event()
    generated = []
    def loader(item, *_):
        assert item.path == heavy.path
        generated.append(item.path)
        entered.set()
        release.wait(5)
        return pixels()
    provider = BrowserThumbnailProvider(disk_cache=cache, loader=loader, max_workers=1)
    ready = []
    provider.thumbnail_ready.connect(lambda path, *_: ready.append(path))
    try:
        generation = provider.begin_generation()
        assert provider.request(heavy, spec, generation=generation)
        deadline = monotonic()+3
        while not entered.is_set() and monotonic()<deadline:
            qapp.processEvents()
            sleep(.001)
        assert entered.is_set()
        for item in saved:
            assert provider.request(item, spec, generation=generation)
        deadline = monotonic()+3
        while len(ready)<24 and monotonic()<deadline:
            qapp.processEvents()
            sleep(.001)
        assert set(ready) == {str(item.path) for item in saved}
        assert not release.is_set()
        assert provider.cache_statistics()['disk_hit'] == 24
        assert generated == [heavy.path]
    finally:
        release.set()
        provider.wait_for_done(5000)
        provider.close()
        cache.close()


def test_uncertain_old_crop_is_provisional_then_updated_without_duplicate_entry(tmp_path, qapp):
    item = source(tmp_path / 'image.jpg')
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'smart_crop')
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    assert cache.put(item, replace(spec, implementation_version=3), pixels())
    entered, release = Event(), Event()
    def loader(*args):
        entered.set()
        release.wait(3)
        return pixels()
    provider = BrowserThumbnailProvider(disk_cache=cache, loader=loader)
    provisional = []
    final = []
    provider.thumbnail_provisional.connect(lambda *args: provisional.append(args))
    provider.thumbnail_ready.connect(lambda *args: final.append(args))
    try:
        provider.request(item, spec, generation=provider.begin_generation())
        deadline = monotonic()+3
        while not entered.is_set() and monotonic()<deadline:
            qapp.processEvents()
            sleep(.001)
        assert provisional
        assert not final
        release.set()
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        assert final
        assert provider.wait_for_done(5000)
        assert cache.statistics()['entry_count'] == 1
        assert not cache.get_suitable(item, spec).low_resolution_placeholder
    finally:
        release.set()
        provider.close(5000)
        cache.close()


def test_working_budget_serializes_under_pressure_and_cancels_waiter(tmp_path, monkeypatch):
    import app.browser_generation_budget as module
    budget = BrowserGenerationBudget()
    monkeypatch.setattr(budget, 'estimate', lambda _item: 150*1024*1024)
    monkeypatch.setattr(module, 'read_physical_memory_snapshot', lambda: SimpleNamespace(
        available_physical_bytes=2*1024**3+400*1024*1024))
    release, entered, cancelled = Event(), Event(), Event()
    results = []
    def first():
        with budget.hold(None, Event()):
            entered.set()
            release.wait(3)
    def second():
        try:
            with budget.hold(None, cancelled):
                results.append('admitted')
        except InterruptedError:
            results.append('cancelled')
    a = Thread(target=first); a.start()
    assert entered.wait(1)
    b = Thread(target=second); b.start()
    sleep(.05)
    cancelled.set()
    b.join(1)
    release.set(); a.join(1)
    assert results == ['cancelled']
    assert budget.peak_active == 1
    assert budget.bytes == budget.active == 0


def test_viewer_reserved_lane_runs_during_browser_generation_and_pause_fences_reads(qapp):
    coordinator = ImageWorkCoordinator(max_workers=2)
    first, second, viewer, release = Event(), Event(), Event(), Event()
    class Job(QRunnable):
        def __init__(self, entered, block):
            super().__init__(); self.entered = entered; self.block = block
        def run(self):
            self.entered.set()
            if self.block: release.wait(3)
    try:
        assert coordinator.start_browser_generation(Job(first, True), ImageWorkPriority.BROWSER_PREFETCH)
        assert coordinator.start_browser_generation(Job(second, True), ImageWorkPriority.BROWSER_PREFETCH)
        assert first.wait(1) and second.wait(1)
        assert coordinator.start_viewer(Job(viewer, False), ImageWorkPriority.VIEWER_CURRENT)
        assert viewer.wait(1)
        coordinator.begin_viewer_interactive()
        assert not coordinator.start_browser_read(Job(Event(), False), ImageWorkPriority.BROWSER_VISIBLE)
        assert not coordinator.start_browser_generation(Job(Event(), False), ImageWorkPriority.BROWSER_PREFETCH)
        coordinator.end_viewer_interactive()
    finally:
        release.set()
        assert coordinator.shutdown(wait_msecs=5000)


def test_duplicate_reuse_updates_page_count_without_writing_image(tmp_path, qapp):
    item = source(tmp_path / 'image.png')
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    try:
        assert cache.put(item, replace(spec, implementation_version=4), pixels())
        files = set(cache.files_dir.iterdir())
        assert not cache.put(item, spec, pixels(), page_count=7)
        assert cache.get_page_count(item) == 7
        assert set(cache.files_dir.iterdir()) == files
    finally:
        cache.close()


def test_waiting_admission_rechecks_available_memory(monkeypatch):
    import app.browser_generation_budget as module
    budget = BrowserGenerationBudget()
    monkeypatch.setattr(budget, 'estimate', lambda _: 150*1024*1024)
    free = [2*1024**3+400*1024*1024]
    monkeypatch.setattr(module, 'read_physical_memory_snapshot', lambda: SimpleNamespace(
        available_physical_bytes=free[0]))
    release, entered = Event(), Event()
    result = []
    def first():
        with budget.hold(None, Event()):
            entered.set()
            release.wait(3)
    def second():
        with budget.hold(None, Event()):
            result.append('admitted')
    a = Thread(target=first); a.start()
    assert entered.wait(1)
    b = Thread(target=second); b.start()
    try:
        sleep(.05)
        free[0] = 2*1024**3+100*1024*1024
        sleep(.05)
        assert not result and b.is_alive()  # Temporary pressure is not failure.
        free[0] = 8*1024**3
        b.join(1)
        assert result == ['admitted']
    finally:
        release.set(); a.join(3); b.join(3)
    assert budget.bytes == budget.active == 0


@pytest.mark.parametrize('action', ['generation', 'source', 'close'])
def test_saved_read_cannot_publish_after_invalidation(tmp_path, qapp, action):
    item = source(tmp_path / 'image.png')
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    assert cache.put(item, spec, pixels())
    entered, release = Event(), Event()
    original = cache._read_qimage
    def decode(handle):
        entered.set()
        release.wait(5)
        return original(handle)
    cache._read_qimage = decode
    provider = BrowserThumbnailProvider(disk_cache=cache,
        loader=lambda *_: None)
    ready = []
    provider.thumbnail_ready.connect(lambda *args: ready.append(args))
    try:
        generation = provider.begin_generation()
        assert provider.request(item, spec, generation=generation)
        assert entered.wait(2)
        if action == 'generation':
            provider.begin_generation()
        elif action == 'source':
            item.path.unlink()
        else:
            provider.close(0)
        release.set()
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        assert not ready
        assert not cache._read_leases
    finally:
        release.set()
        provider.close(5000)
        cache.close()


def test_offline_saved_decode_releases_lock_and_does_not_survive_clear(tmp_path, qapp):
    item = source(tmp_path / 'image.png')
    info = item.path.stat()
    item = replace(item, file_size=info.st_size, modified_time_ns=info.st_mtime_ns)
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    assert cache.put(item, 64, pixels())
    entered, release = Event(), Event()
    original = cache._read_qimage
    def decode(handle):
        entered.set()
        release.wait(5)
        return original(handle)
    cache._read_qimage = decode
    results = []
    reader = Thread(target=lambda: results.append(cache.get_offline_preview(item)))
    reader.start()
    try:
        assert entered.wait(2)
        assert cache._lock.acquire(timeout=.5)
        cache._lock.release()
        assert cache.clear_all()
        release.set(); reader.join(5)
        assert results == [None]
        assert not cache._read_leases
    finally:
        release.set(); reader.join(5); cache.close()


def test_shutdown_keeps_native_reservation_until_loader_returns(tmp_path, qapp):
    from app.browser_generation_budget import BROWSER_GENERATION_BUDGET as budget
    item = source(tmp_path / 'image.png')
    entered, release = Event(), Event()
    def loader(*_):
        entered.set()
        release.wait(5)
        return pixels()
    provider = BrowserThumbnailProvider(loader=loader, disk_cache_enabled=False)
    ready = []
    provider.thumbnail_ready.connect(lambda *args: ready.append(args))
    try:
        assert provider.request(item, 64, generation=provider.begin_generation())
        assert entered.wait(2)
        provider.close(0)
        assert budget.active >= 1 and budget.bytes >= 16*1024*1024
        release.set()
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        assert not ready
        assert budget.active == budget.bytes == 0
    finally:
        release.set(); provider.wait_for_done(5000); provider.close()


def test_completion_wait_delivers_handoff_without_unrelated_gui_timer(tmp_path, qapp):
    item = source(tmp_path / 'image.png')
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    provider = BrowserThumbnailProvider(disk_cache=cache, loader=lambda *_: pixels())
    unrelated = []
    ready = []
    provider.thumbnail_ready.connect(lambda *args: ready.append(args))
    try:
        QTimer.singleShot(0, lambda: unrelated.append('timer'))
        assert provider.request(item, 64, generation=provider.begin_generation())
        assert provider.wait_for_done(5000)
        assert ready
        assert not unrelated
        qapp.processEvents()
        assert unrelated == ['timer']
    finally:
        provider.close(5000); cache.close()


def test_integer_token_lookup_retains_exact_spec_token_contract(tmp_path, qapp):
    item = source(tmp_path / 'image.png')
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    try:
        assert cache.put(item, spec, pixels())
        assert cache.get(item, spec.cache_token) is not None
        assert cache.get(item, spec.cache_token+1) is None
    finally:
        cache.close()


@pytest.mark.parametrize('suffix', ['.jxl', '.xcf'])
def test_admission_estimate_does_not_enter_eager_optional_codec(tmp_path, monkeypatch, suffix):
    item = SimpleNamespace(kind=BrowserItemKind.IMAGE, path=tmp_path / ('image'+suffix))
    def forbidden(*args, **kwargs):
        pytest.fail('Optional pixel decoder cannot run before working-memory admission')
    monkeypatch.setattr(Image, 'open', forbidden)
    assert BrowserGenerationBudget.estimate(item) == 256*1024*1024


@pytest.mark.parametrize('legacy,needs_refresh', [(4, False), (3, True)])
def test_adequate_proven_legacy_beats_undersized_current_only_when_reusable(tmp_path, qapp, legacy, needs_refresh):
    item = source(tmp_path / ('image.jpg' if needs_refresh else 'image.png'))
    low = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'smart_crop')
    high = ThumbnailRenderSpec.from_settings(256, 'square_1_1', 'smart_crop')
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    def image(edge):
        result = QImage(edge,edge,QImage.Format.Format_RGB32)
        result.fill(0xff123456)
        return result
    try:
        assert cache.put(item, low, image(low.long_edge))
        old = replace(high,implementation_version=legacy)
        assert cache.put(item,old,image(high.long_edge))
        result = cache.get_suitable(item,high)
        assert result.low_resolution_placeholder is needs_refresh
        assert result.frame_width == (low.long_edge if needs_refresh else high.long_edge)
    finally:
        cache.close()


def test_completed_notification_does_not_release_native_worker_before_pool_return(tmp_path, qapp, monkeypatch):
    import app.thumbnail_provider as module
    from shiboken6 import isValid
    original = module._ThumbnailWorker
    reported, release = Event(), Event()
    class DelayedReturn(original):
        def run(self):
            try:
                super().run()
            finally:
                reported.set()
                release.wait(5)
    monkeypatch.setattr(module,'_ThumbnailWorker',DelayedReturn)
    item = source(tmp_path / 'image.png')
    provider = BrowserThumbnailProvider(loader=lambda *_: pixels(),disk_cache_enabled=False)
    try:
        provider.request(item,64,generation=provider.begin_generation())
        worker = next(iter(provider._pending.values())).worker
        assert reported.wait(2)
        qapp.processEvents()
        assert provider.pending_count == 0
        provider._collect_owned_workers()
        assert worker in provider._owned_workers
        assert isValid(worker)
        release.set()
        assert provider.wait_for_done(5000)
        provider._collect_owned_workers()
        assert not provider._owned_workers
    finally:
        release.set(); provider.close(5000)


@pytest.mark.parametrize('workers', [1, 2])
@pytest.mark.parametrize('mode', ['thumbnail', 'metadata', 'adopt_thumbnail'])
def test_priority_promotion_after_native_pool_becomes_idle(tmp_path, qapp, monkeypatch, workers, mode):
    from app.browser_thumbnail_scheduler import ThumbnailPriority
    from app.thumbnail_provider import ThumbnailLoadResult
    from shiboken6 import isValid

    running = [source(tmp_path / f'running-{i}.png') for i in range(workers)]
    folder = tmp_path / 'queued'
    folder.mkdir()
    queued = (source(tmp_path / 'queued.png') if mode == 'thumbnail'
              else make_item(folder, BrowserItemKind.FOLDER))
    entered = {item.path: Event() for item in running}
    release = Event()
    generated = []

    def loader(item, *_):
        if item.path in entered:
            entered[item.path].set()
            assert release.wait(5)
        generated.append(item.path)
        return pixels()

    provider = BrowserThumbnailProvider(max_workers=workers, loader=loader, disk_cache_enabled=False)
    monkeypatch.setattr(provider, '_load_page_count_pipeline',
                        lambda *_args, **_kwargs: ThumbnailLoadResult(None, page_count=7))
    taken_workers = []
    notifications = []
    original_take = provider._try_take
    original_collect = provider._collect_owned_workers

    def take_and_finish_native_jobs(worker):
        taken = original_take(worker)
        if taken:
            taken_workers.append(worker)
            release.set()
            assert provider._pool.waitForDone(2000)
            assert isValid(worker)
        return taken

    def collect_outside_pending_lock():
        # Fail before blocking on a regression, while keeping the real Lock,
        # Qt pools and tryTake/finish race rather than substituting fake pools.
        assert provider._pending_lock.acquire(blocking=False)
        provider._pending_lock.release()
        original_collect()

    def capacity_callback():
        assert provider._pending_lock.acquire(blocking=False)
        provider._pending_lock.release()
        notifications.append(True)

    provider.capacity_released.connect(capacity_callback)
    try:
        generation = provider.begin_generation()
        for item in running:
            assert provider.request(item, 64, generation=generation)
        for event in entered.values():
            assert event.wait(2)
        if mode == 'metadata':
            assert provider.request_page_count(queued, generation=generation,
                                               priority=ThumbnailPriority.BACKGROUND)
        else:
            assert provider.request(queued, 64, generation=generation,
                                    priority=ThumbnailPriority.BACKGROUND)
        monkeypatch.setattr(provider, '_try_take', take_and_finish_native_jobs)
        monkeypatch.setattr(provider, '_collect_owned_workers', collect_outside_pending_lock)
        if mode == 'thumbnail':
            assert not provider.request(queued, 64, generation=generation,
                                        priority=ThumbnailPriority.VISIBLE)
        else:
            assert not provider.request_page_count(queued, generation=generation,
                                                   priority=ThumbnailPriority.SELECTED)
        assert len(taken_workers) == 1
        assert taken_workers[0].priority == (ThumbnailPriority.VISIBLE if mode == 'thumbnail'
                                             else ThumbnailPriority.SELECTED)
        assert taken_workers[0] in provider._owned_workers
        assert len(provider._owned_workers) <= 256
        assert not notifications  # No synchronous capacity callback during submission.
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        assert provider.pending_count == 0
        assert generated.count(queued.path) == (0 if mode == 'metadata' else 1)
        provider._collect_owned_workers()
        qapp.processEvents()
        assert notifications
        assert not provider._owned_workers
    finally:
        release.set()
        provider.close(5000)


def test_native_ownership_cap_survives_generation_cancel_until_pool_returns(tmp_path, qapp):
    from shiboken6 import isValid
    item = source(tmp_path / 'image.png')
    entered, release = Event(), Event()

    def loader(*_):
        entered.set()
        assert release.wait(5)
        return pixels()

    provider = BrowserThumbnailProvider(max_workers=1, loader=loader, disk_cache_enabled=False)
    rejected = []
    results = []
    provider.submission_rejected.connect(lambda *args: rejected.append(args))
    provider.thumbnail_ready.connect(lambda *args: results.append(args))
    try:
        generation = provider.begin_generation()
        assert provider.request(item, 16, generation=generation)
        assert entered.wait(2)
        for size in range(17, 272):
            assert provider.request(item, size, generation=generation)
        assert len(provider._owned_workers) == 256
        assert not provider.request(item, 272, generation=generation)
        assert len(rejected) == 1 and provider.pending_count == 256
        running_worker = next(p.worker for p in provider._pending.values()
                              if p.worker.run_started.is_set())
        current = provider.begin_generation()
        provider._collect_owned_workers()
        assert running_worker in provider._owned_workers and isValid(running_worker)
        assert len(provider._owned_workers) == 256
        assert provider.pending_count == 0
        release.set()
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        provider._collect_owned_workers()
        assert not provider._owned_workers and not results
        assert provider.request(item, 272, generation=current)
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        assert len(results) == 1 and results[0][1] == current
    finally:
        release.set()
        provider.close(5000)


def test_provider_wait_keeps_gui_thread_binding_after_provider_collection(qapp):
    import gc
    from PySide6.QtCore import QThread
    from shiboken6 import isValid
    from app.browser_generation_budget import refresh_generation_memory

    refresh_generation_memory()
    thread = QThread.currentThread()
    sampler = qapp._nivis_generation_memory_sampler
    assert sampler.owner_thread is thread
    for _ in range(3):
        provider = BrowserThumbnailProvider(disk_cache_enabled=False)
        assert provider.wait_for_done(0)
        provider.close()
        del provider
        gc.collect()
        assert isValid(thread)
        assert QThread.currentThread() is thread
        assert sampler.owner_thread is thread
        assert qapp.thread() is thread


def test_gui_thread_binding_survives_collection_before_native_codec_read(tmp_path, qapp):
    import gc
    import io
    import weakref
    from PySide6.QtCore import QThread
    from app.browser_generation_budget import refresh_generation_memory
    from app.image_source import _read_jpeg_qimage_at_most

    refresh_generation_memory()
    thread = QThread.currentThread()
    reference = weakref.ref(thread)
    del thread
    gc.collect()
    assert reference() is not None
    assert reference().isCurrentThread()
    assert reference() == qapp.thread()
    payload = io.BytesIO()
    with Image.new('RGB', (640, 960), 'navy') as image:
        image.save(payload, format='JPEG')
    for _ in range(3):
        gc.collect()
        decoded = _read_jpeg_qimage_at_most(payload.getvalue(), (160, 240))
        assert decoded is not None
        image, logical = decoded
        assert logical == (640, 960)
        assert image.size().width() == 160 and image.size().height() == 240


def test_large_jobs_use_roomy_memory_but_wait_for_commit_recovery(monkeypatch):
    import app.browser_generation_budget as module
    from contextlib import ExitStack
    budget = BrowserGenerationBudget()
    budget.max_active = 8
    sample = SimpleNamespace(available_physical_bytes=16*1024**3,
                             available_commit_bytes=16*1024**3)
    budget.snapshot_reader = lambda: sample
    cost = 4000*6000*20
    with ExitStack() as stack:
        for _ in range(8):
            stack.enter_context(budget.hold(None, Event(), cost=cost, wait=False))
        assert budget.active == 8 and budget.bytes == 8*cost
        assert not budget.can_admit(16*1024*1024)
    sample.available_commit_bytes = 2*1024**3 + cost
    with pytest.raises(module.GenerationAdmissionDeferred):
        with budget.hold(None, Event(), cost=cost, wait=False):
            pytest.fail('Commit pressure must defer before decoding')
    sample.available_commit_bytes = 16*1024**3
    with budget.hold(None, Event(), cost=cost, wait=False):
        assert budget.active == 1
    assert budget.bytes == budget.active == 0


@pytest.mark.parametrize('action', ['replace', 'disable', 'exception'])
def test_saved_read_releases_lease_and_fences_concurrent_replacement(tmp_path, qapp, action):
    item = source(tmp_path / 'image.png')
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    other = ThumbnailDiskCache(tmp_path / 'cache')
    assert cache.put(item, spec, pixels())
    entered, release = Event(), Event()
    original = cache._read_qimage
    results, errors = [], []
    def decode(handle):
        entered.set(); assert release.wait(5)
        return original(handle)
    cache._read_qimage = decode
    if action == 'exception':
        def fail(*_): raise RuntimeError('synthetic post-decode validation failure')
        cache._read_source_matches = fail
    def read():
        try: results.append(cache.get_suitable(item, spec))
        except RuntimeError: errors.append('validation')
    thread = Thread(target=read); thread.start()
    try:
        assert entered.wait(2)
        if action == 'replace':
            with other._lock:
                rows = other._connection.execute('SELECT cache_key,file_name FROM entries').fetchall()
                other._remove_entries(rows)
            assert other.put(item, spec, pixels())
        elif action == 'disable':
            cache.set_enabled(False)
        release.set(); thread.join(5)
        assert not thread.is_alive() and not cache._read_leases
        if action == 'exception':
            assert errors == ['validation'] and results == []
        else:
            assert not errors and results == [None]
        if action == 'replace':
            assert other.get_suitable(item, spec) is not None
            assert len(list(other.files_dir.iterdir())) == 1
    finally:
        release.set(); thread.join(5); cache.close(); other.close()


@pytest.mark.parametrize('action', ['generation', 'close', 'pause'])
def test_cancel_reserved_requeue_releases_only_queued_work(tmp_path, qapp, monkeypatch, action):
    from app.browser_generation_budget import BROWSER_GENERATION_BUDGET as budget
    item = source(tmp_path / 'large.png')
    cost = 300*1024*1024
    sample = SimpleNamespace(available_physical_bytes=2*1024**3+128*1024*1024)
    monkeypatch.setattr(budget, 'snapshot_reader', lambda: sample)
    monkeypatch.setattr(budget, 'estimate', lambda *_: cost)
    provider = BrowserThumbnailProvider(max_workers=1, disk_cache_enabled=False,
        loader=lambda *_: pytest.fail('Cancelled queued work must not decode'))
    entered, release = Event(), Event()
    class OccupiedNativeLane(QRunnable):
        def __init__(self):
            super().__init__(); self.setAutoDelete(False)
        def run(self):
            entered.set(); assert release.wait(5)
    occupied = OccupiedNativeLane()
    try:
        provider.request(item, 64, generation=provider.begin_generation())
        deadline = monotonic()+2
        while not provider._waiting_generation and monotonic()<deadline:
            qapp.processEvents(); sleep(.001)
        assert provider._waiting_generation
        provider._pool.start(occupied)
        assert entered.wait(1)
        sample.available_physical_bytes = 16*1024**3
        provider._retry_generation_admission()
        assert not provider._waiting_generation
        assert budget.active == 1 and budget.bytes == cost
        if action == 'generation': provider.begin_generation()
        elif action == 'close': provider.close(0)
        else: provider.set_paused(True)
        assert budget.active == budget.bytes == 0
        assert not provider._pool.waitForDone(0)  # Unrelated native work still runs.
        release.set(); assert provider.wait_for_done(5000)
        qapp.processEvents()
        assert not provider._cache
    finally:
        release.set(); provider.close(5000); provider._pool.waitForDone(5000)


@pytest.mark.parametrize('action', ['recover', 'generation', 'close', 'source', 'viewer'])
def test_deferred_generation_frees_native_lane_and_preserves_invalidation(tmp_path, qapp, monkeypatch, action):
    from app.browser_generation_budget import BROWSER_GENERATION_BUDGET as budget
    from app.browser_thumbnail_scheduler import ThumbnailPriority
    large = source(tmp_path / 'large.png')
    small = source(tmp_path / 'small.png')
    cost = {large.path: 300*1024*1024, small.path: 16*1024*1024}
    sample = SimpleNamespace(available_physical_bytes=2*1024**3+128*1024*1024,
                             available_commit_bytes=16*1024**3)
    monkeypatch.setattr(budget, 'snapshot_reader', lambda: sample)
    monkeypatch.setattr(budget, 'estimate', lambda item: cost[item.path])
    coordinator = ImageWorkCoordinator(max_workers=2)
    provider = BrowserThumbnailProvider(loader=lambda *_: pixels(), disk_cache_enabled=False,
                                         image_work_coordinator=coordinator)
    ready = []
    provider.thumbnail_ready.connect(lambda path, *_: ready.append(path))
    def pump(predicate):
        deadline = monotonic()+3
        while not predicate() and monotonic()<deadline:
            qapp.processEvents(); sleep(.001)
        assert predicate()
    try:
        generation = provider.begin_generation()
        assert provider.request(large, 64, generation=generation, priority=ThumbnailPriority.READ_AHEAD)
        pump(lambda: bool(provider._waiting_generation))
        assert coordinator.wait_for_browser(500)
        assert budget.active == budget.bytes == 0
        assert not provider._failed
        assert provider.request(small, 64, generation=generation)
        pump(lambda: str(small.path) in ready)
        assert ready == [str(small.path)]
        if action == 'generation':
            provider.begin_generation()
        elif action == 'close':
            provider.close(500)
        elif action == 'source':
            provider.reconcile_items((large, small), (small,))
        elif action == 'viewer':
            coordinator.begin_viewer_interactive()
            assert not provider._waiting_generation
            coordinator.end_viewer_interactive()
        sample.available_physical_bytes = 16*1024**3
        if action == 'recover':
            pump(lambda: str(large.path) in ready)
            assert len(ready) == 2
        else:
            qapp.processEvents()
            provider._retry_generation_admission()
            assert provider.wait_for_done(500)
            qapp.processEvents()
            assert ready == [str(small.path)]
        assert not provider._waiting_generation and not provider._failed
    finally:
        provider.close(5000)
        assert coordinator.shutdown(wait_msecs=5000)


def test_saved_background_reads_continue_when_generation_fills_admission(tmp_path, qapp):
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    heavy = source(tmp_path / 'heavy.png')
    saved = [source(tmp_path / f'saved-{i}.png') for i in range(24)]
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    for item in saved:
        assert cache.put(item, spec, pixels())
    entered, release = Event(), Event()
    generated = []
    def loader(item, *_):
        assert item.path == heavy.path
        generated.append(item.path)
        entered.set()
        release.wait(5)
        return pixels()
    provider = BrowserThumbnailProvider(disk_cache=cache, loader=loader, max_workers=1)
    try:
        generation = provider.begin_generation()
        assert provider.request(heavy, spec, generation=generation)
        deadline = monotonic()+3
        while not entered.is_set() and monotonic()<deadline:
            qapp.processEvents()
            sleep(.001)
        assert entered.is_set()
        provider.background_read_request_limit = 1
        assert provider.pending_count == 1
        assert provider.pending_read_count == 0
        for item in saved:
            assert provider.request_background_read(item, spec, generation=generation) == 'queued'
            deadline = monotonic()+3
            while provider.pending_read_count and monotonic()<deadline:
                qapp.processEvents()
                sleep(.001)
            assert provider.has_memory_thumbnail(item, spec)
        assert all(provider.has_memory_thumbnail(item, spec) for item in saved)
        assert not release.is_set()
        assert provider.cache_statistics()['disk_hit'] == 24
        assert generated == [heavy.path]
    finally:
        release.set()
        provider.wait_for_done(5000)
        provider.close()
        cache.close()
