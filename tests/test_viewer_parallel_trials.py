from collections import Counter
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock

import pytest
from PySide6.QtCore import QRunnable
from PIL import Image

from app.image_source import ImageSourceError, ZipImageSource
from app.image_work_coordinator import ImageWorkCoordinator, ImageWorkPriority
from app.raster_execution_budget import RASTER_EXECUTION_BUDGET, SourceRequestToken, source_request_scope
from app.settings_dialog import SettingsDialog
from app.config_manager import ConfigManager
from app.zip_raster_book_runtime import ZipRasterBookRuntime, ZipRasterRenderSpec
from app.viewer_window import ViewerWindow
from tests.test_zip_raster_book_runtime import _request, _unit, _write_zip, _wait_until


@pytest.fixture(params=(3, 4))
def execution_limit(request):
    RASTER_EXECUTION_BUDGET.configure(request.param)
    yield request.param
    RASTER_EXECUTION_BUDGET.configure(3)


class GateSource(ZipImageSource):
    def __init__(self, path):
        super().__init__(path)
        self.calls = Counter()
        self.lock = Lock()
        self.block = set()
        self.entered = {f'{i}.png': Event() for i in range(8)}
        self.release = {f'{i}.png': Event() for i in range(8)}

    def open_image(self, image_id):
        with self.lock:
            self.calls[image_id] += 1
        self.entered[image_id].set()
        if image_id in self.block:
            assert self.release[image_id].wait(5)
        return super().open_image(image_id)

    def unblock(self):
        for event in self.release.values():
            event.set()


def close_runtime(runtime, source, qapp):
    source.unblock()
    _wait_until(qapp, lambda: not runtime.has_unfinished_tasks(), timeout_ms=5000)
    assert runtime.shutdown()
    source.close()
    qapp.processEvents()


@pytest.mark.parametrize('parallel', [False, True])
def test_spread_halves_parallelize_only_when_enabled_and_publish_atomically(tmp_path, qapp, parallel):
    source = GateSource(_write_zip(tmp_path, pages=2))
    source.block = {'0.png', '1.png'}
    runtime = ZipRasterBookRuntime(source, 1, spread_parallel=parallel)
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1, _unit(0, 1)))
        assert source.entered['0.png'].wait(1)
        if parallel:
            assert source.entered['1.png'].wait(1)
        else:
            assert not source.entered['1.png'].is_set()
        qapp.processEvents()
        assert frames == []
        source.release['0.png'].set()
        assert source.entered['1.png'].wait(1)
        qapp.processEvents()
        assert frames == []
        source.release['1.png'].set()
        _wait_until(qapp, lambda: bool(frames))
        assert len(frames) == 1
        assert len(frames[0].pages) == 2
        assert source.calls == {'0.png': 1, '1.png': 1}
    finally:
        close_runtime(runtime, source, qapp)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('direction', ['ltr', 'rtl'])
def test_parallel_spread_pixels_match_serial_with_dpr_zoom_and_errors(tmp_path, qapp, rotation, direction):
    archive = _write_zip(tmp_path, pages=2)
    snapshots = []
    for parallel in (False, True):
        source = GateSource(archive)
        original = source.open_image
        def open_image(image_id):
            if image_id == '1.png' and rotation == 180:
                raise ImageSourceError('one side failed')
            return original(image_id)
        source.open_image = open_image
        runtime = ZipRasterBookRuntime(source, 1, spread_parallel=parallel)
        frames = []
        runtime.frameReady.connect(frames.append)
        try:
            spec = ZipRasterRenderSpec((640, 480), rotation=rotation, reading_direction=direction,
                                       device_pixel_ratio=2, manual_zoom=1.3, fit_mode='manual')
            runtime.request(_request(1, _unit(0, 1), spec=spec))
            _wait_until(qapp, lambda: bool(frames))
            snapshots.append(tuple((page.page_index, page.error,
                None if page.pixmap is None else (page.pixmap.size().toTuple(),
                    sha256(bytes(page.pixmap.toImage().constBits())).hexdigest())) for page in frames[0].pages))
        finally:
            close_runtime(runtime, source, qapp)
    assert snapshots[0] == snapshots[1]


def test_parallel_single_cached_half_and_layout_reuse_do_not_decode_again(tmp_path, qapp):
    source = GateSource(_write_zip(tmp_path, pages=2))
    runtime = ZipRasterBookRuntime(source, 1, spread_parallel=True)
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1, _unit(0)))
        _wait_until(qapp, lambda: len(frames) == 1)
        runtime.request(_request(2, _unit(0, 1)))
        _wait_until(qapp, lambda: len(frames) == 2)
        runtime.request(_request(3, _unit(0, 1), spec=ZipRasterRenderSpec((800, 600), rotation=90)))
        _wait_until(qapp, lambda: len(frames) == 3)
        assert source.calls == {'0.png': 1, '1.png': 1}
        assert runtime.metrics.source_cache_hits >= 3
    finally:
        close_runtime(runtime, source, qapp)


@pytest.mark.parametrize('workers', [1, 2])
@pytest.mark.parametrize('extra', [False, True])
def test_additional_current_slot_bypasses_unrelated_prefetch_and_preserves_reservations(tmp_path, qapp, workers, extra):
    source = GateSource(_write_zip(tmp_path, pages=6))
    source.block = {'1.png', '2.png'}
    runtime = ZipRasterBookRuntime(source, 1, max_active_jobs=workers, current_extra=extra)
    frames = []
    runtime.frameReady.connect(frames.append)
    units = [_unit(i) for i in range(6)]
    try:
        runtime.request(_request(1, units[0], *units))
        _wait_until(qapp, lambda: bool(frames))
        runtime.release_continuous_warmup(request_id=1)
        assert source.entered['1.png'].wait(1)
        if workers == 2:
            assert source.entered['2.png'].wait(1)
        old = tuple(runtime._active_slots())
        runtime.request(_request(2, units[4], *units))
        if extra:
            _wait_until(qapp, lambda: any(frame.request_id == 2 for frame in frames))
            assert source.entered['4.png'].is_set()
        else:
            qapp.processEvents()
            assert not source.entered['4.png'].is_set()
        assert all(job in runtime._inflight_reservations for job in old)
        assert all(not job.finished.is_set() for job in old)
        source.unblock()
        _wait_until(qapp, lambda: any(frame.request_id == 2 for frame in frames))
        assert source.calls['4.png'] == 1
    finally:
        close_runtime(runtime, source, qapp)


def test_current_prefetch_is_adopted_instead_of_using_an_extra_slot(tmp_path, qapp):
    source = GateSource(_write_zip(tmp_path, pages=3))
    source.block = {'1.png'}
    runtime = ZipRasterBookRuntime(source, 1, current_extra=True)
    frames = []
    runtime.frameReady.connect(frames.append)
    units = [_unit(i) for i in range(3)]
    try:
        runtime.request(_request(1, units[0], *units))
        _wait_until(qapp, lambda: bool(frames))
        runtime.release_continuous_warmup(request_id=1)
        assert source.entered['1.png'].wait(1)
        owned = runtime._active_job
        runtime.request(_request(2, units[1], *units))
        assert runtime._active_job is owned
        assert runtime._extra_job is None
        assert not owned.cancelled.is_set()
        source.unblock()
        _wait_until(qapp, lambda: any(frame.request_id == 2 for frame in frames))
        assert source.calls['1.png'] == 1
    finally:
        close_runtime(runtime, source, qapp)


def test_cancelled_extra_keeps_its_slot_until_native_end_and_only_latest_runs(tmp_path, qapp, execution_limit):
    source = GateSource(_write_zip(tmp_path, pages=6))
    source.block = {'1.png', '3.png'}
    runtime = ZipRasterBookRuntime(source, 1, current_extra=True)
    frames = []
    runtime.frameReady.connect(frames.append)
    units = [_unit(i) for i in range(6)]
    try:
        runtime.request(_request(1, units[0], *units))
        _wait_until(qapp, lambda: bool(frames))
        runtime.release_continuous_warmup(request_id=1)
        assert source.entered['1.png'].wait(1)
        runtime.request(_request(2, units[3], *units))
        assert source.entered['3.png'].wait(1)
        extra = runtime._extra_job
        for request_id, target in ((3, 4), (4, 5)):
            runtime.request(_request(request_id, units[target], *units))
        assert extra.cancelled.is_set() and not extra.finished.is_set()
        assert extra in runtime._inflight_reservations
        assert runtime._extra_job is extra
        assert not source.entered['4.png'].is_set()
        assert not source.entered['5.png'].is_set()
        source.release['3.png'].set()
        _wait_until(qapp, lambda: any(frame.request_id == 4 for frame in frames))
        assert not any(frame.request_id in (2, 3) for frame in frames)
        assert not source.entered['4.png'].is_set()
    finally:
        close_runtime(runtime, source, qapp)


def test_entry_cancellation_does_not_cancel_another_scoped_consumer(tmp_path):
    source = ZipImageSource(_write_zip(tmp_path, pages=1))
    first, second = SourceRequestToken(), SourceRequestToken()
    with source_request_scope(first):
        a = source._begin_request('0.png')
    with source_request_scope(second):
        b = source._begin_request('0.png')
    legacy = source._begin_request('0.png')
    try:
        source.cancel_image_request('0.png')
        assert legacy.is_set()
        assert not a.is_set() and not b.is_set()
        first.set()
        assert not second.is_set()
        source.close()
        assert second.is_set()
    finally:
        for token in (a, b, legacy):
            source._finish_request('0.png', token)
        source.close()


def test_parallel_options_save_reset_cancel_and_default_on(tmp_path, qapp):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    keys = ('viewer_spread_parallel_trial', 'viewer_current_extra_trial')
    assert [config.get(key) for key in keys] == [True, True]
    dialog = SettingsDialog(config)
    try:
        dialog.viewer_spread_parallel_checkbox.setChecked(False)
        dialog.viewer_current_extra_checkbox.setChecked(False)
        dialog.apply_settings()
        assert all(ConfigManager(config.path).load()[key] is False for key in keys)
        dialog._reset_viewer_scope()
        assert dialog.viewer_spread_parallel_checkbox.isChecked()
        assert dialog.viewer_current_extra_checkbox.isChecked()
        dialog.reject()
        assert all(config.get(key) is False for key in keys)
        assert config.get('viewer_prefetch_forward_screens') == 5
    finally:
        dialog.close()
        qapp.processEvents()


@pytest.mark.parametrize('shared_coordinator', [False, True])
@pytest.mark.parametrize('limit', [3, 4])
def test_native_limit_across_windows_and_browser_lane_stays_independent(tmp_path, qapp, shared_coordinator, limit):
    RASTER_EXECUTION_BUDGET.configure(limit)
    archive = _write_zip(tmp_path, pages=1)
    coordinator = ImageWorkCoordinator(folder_supplemental_workers=1) if shared_coordinator else None
    # A shared coordinator keeps its two normal lanes and single extra lane.
    # Independent runtime pools share permits but need not start in list order.
    expected_active = min(limit, 3) if shared_coordinator else limit
    sources = [GateSource(archive) for _ in range(expected_active + 1)]
    runtimes = [ZipRasterBookRuntime(source, 1, max_active_jobs=2,
                current_extra=True, image_work_coordinator=coordinator) for source in sources]
    class BrowserJob(QRunnable):
        def run(self):
            browser_entered.set()
    browser_entered = Event()
    browser_job = BrowserJob()
    try:
        for source, runtime in zip(sources, runtimes):
            source.block = {'0.png'}
            runtime.request(_request(1, _unit(0)))
        _wait_until(qapp, lambda: sum(source.entered['0.png'].is_set() for source in sources) == expected_active)
        waiting = next(source for source in sources if not source.entered['0.png'].is_set())
        assert RASTER_EXECUTION_BUDGET.debug_values()['active_native_jobs'] == expected_active
        if coordinator is not None:
            assert sum(lane == 'current' for lane in coordinator._viewer_lane.values()) == 1
            assert coordinator.start_browser(browser_job, ImageWorkPriority.BROWSER_VISIBLE)
            assert browser_entered.wait(1)
        next(source for source in sources if source.entered['0.png'].is_set()).unblock()
        _wait_until(qapp, waiting.entered['0.png'].is_set)
        assert RASTER_EXECUTION_BUDGET.debug_values()['active_native_jobs'] <= limit
    finally:
        for source in sources:
            source.unblock()
        for runtime, source in zip(runtimes, sources):
            close_runtime(runtime, source, qapp)
        if coordinator is not None:
            assert coordinator.shutdown(wait_msecs=3000)
        assert RASTER_EXECUTION_BUDGET.debug_values()['owned_permits'] == 0
        RASTER_EXECUTION_BUDGET.configure(3)


def test_parallel_child_is_owned_through_cancel_and_book_close(tmp_path, qapp, execution_limit):
    source = GateSource(_write_zip(tmp_path, pages=2))
    source.block = {'0.png', '1.png'}
    runtime = ZipRasterBookRuntime(source, 1, spread_parallel=True)
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1, _unit(0, 1)))
        assert source.entered['0.png'].wait(1) and source.entered['1.png'].wait(1)
        job = runtime._active_job
        assert not runtime.shutdown(wait_msecs=0)
        assert not job.finished.is_set()
        assert job in runtime._inflight_reservations
        source.release['0.png'].set()
        qapp.processEvents()
        assert not job.finished.is_set()
        assert job in runtime._inflight_reservations
        source.release['1.png'].set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert frames == []
        assert RASTER_EXECUTION_BUDGET.debug_values()['owned_permits'] == 0
    finally:
        close_runtime(runtime, source, qapp)


def test_split_wide_source_is_read_once_even_with_both_trials(tmp_path, qapp):
    import zipfile
    image_path = tmp_path / 'wide.png'
    with Image.new('RGB', (800, 200), 'red') as image:
        image.save(image_path)
    archive = tmp_path / 'wide.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.write(image_path, '0.png')
    source = GateSource(archive)
    runtime = ZipRasterBookRuntime(source, 1, spread_parallel=True, current_extra=True)
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1, _unit(0), spec=ZipRasterRenderSpec((640, 480), split_wide_image=True)))
        _wait_until(qapp, lambda: bool(frames))
        assert len(frames[0].pages) == 2
        assert source.calls['0.png'] == 1
    finally:
        close_runtime(runtime, source, qapp)


def test_memory_pressure_refuses_extra_without_discarding_displayed_frame(tmp_path, qapp, execution_limit):
    source = GateSource(_write_zip(tmp_path, pages=5))
    source.block = {'1.png'}
    runtime = ZipRasterBookRuntime(source, 1, current_extra=True, spread_parallel=True,
                                  cache_byte_budget=8 * 1024 * 1024)
    units = [_unit(i) for i in range(5)]
    units = [replace(unit, pages=tuple(replace(page, known_size=(120, 180)) for page in unit.pages)) for unit in units]
    large = replace(units[4], pages=(replace(units[4].pages[0], known_size=(4096, 6500)),))
    units[4] = large
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1, units[0], *units))
        _wait_until(qapp, lambda: bool(frames))
        runtime.release_continuous_warmup(request_id=1)
        assert source.entered['1.png'].wait(1)
        runtime.request(_request(2, large, *units))
        qapp.processEvents()
        assert not source.entered['4.png'].is_set()
        assert runtime._extra_job is None
        assert frames[-1].request_id == 1
        runtime.cancel(clear_artifacts=True)
    finally:
        close_runtime(runtime, source, qapp)


def test_trial_options_apply_to_next_book_and_preserve_owned_old_runtime(tmp_path, qapp):
    archive = _write_zip(tmp_path, pages=2)
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.apply({'viewer_spread_parallel_trial': False, 'viewer_current_extra_trial': False})
    coordinator = ImageWorkCoordinator()
    window = ViewerWindow(config_manager=config, image_work_coordinator=coordinator)
    try:
        assert window.open_path(archive)
        _wait_until(qapp, lambda: window.book_session.viewer_runtime is not None)
        old = window.book_session.viewer_runtime
        config.apply({'viewer_spread_parallel_trial': True, 'viewer_current_extra_trial': True})
        assert window.book_session.viewer_runtime is old
        assert not old._spread_parallel and not old._current_extra
        assert window.open_path(archive)
        _wait_until(qapp, lambda: window.book_session.viewer_runtime is not old)
        current = window.book_session.viewer_runtime
        assert current._spread_parallel and current._current_extra
        assert config.get('viewer_prefetch_forward_screens') == 5
    finally:
        window.prepare_shutdown(wait_msecs=5000)
        window.close()
        qapp.processEvents()
        assert coordinator.shutdown(wait_msecs=5000)


def test_rar_constraints_are_not_relaxed_by_trial_flags(tmp_path, qapp):
    from tests.test_rar_raster_pipeline import Backend, source_fixture
    from app.raster_book_runtime import RasterBookRuntime
    source = source_fixture(tmp_path, Backend())
    runtime = RasterBookRuntime(source, 1, spread_parallel=True, current_extra=True)
    try:
        assert not runtime._spread_parallel
        assert not runtime._current_extra
        assert runtime._max_active_jobs == 1
    finally:
        assert runtime.shutdown()
        source.close()


@pytest.mark.parametrize('limit', [3, 4])
def test_two_busy_parents_allow_spread_helper_only_at_four_and_lowering_drains(tmp_path, qapp, limit):
    RASTER_EXECUTION_BUDGET.configure(limit)
    archive = _write_zip(tmp_path, pages=8)
    source = GateSource(archive)
    runtime = ZipRasterBookRuntime(source, 1, max_active_jobs=2, current_extra=True, spread_parallel=True)
    other_source = GateSource(archive)
    other = ZipRasterBookRuntime(other_source, 1)
    frames = []
    runtime.frameReady.connect(frames.append)
    units = [_unit(i) for i in range(8)]
    try:
        runtime.request(_request(1, units[0], *units))
        _wait_until(qapp, lambda: bool(frames))
        source.block = {'1.png', '2.png', '4.png', '5.png'}
        runtime.release_continuous_warmup(request_id=1)
        assert source.entered['1.png'].wait(1) and source.entered['2.png'].wait(1)
        runtime.request(_request(2, _unit(4, 5)))
        assert source.entered['4.png'].wait(1)
        assert source.entered['5.png'].wait(.1) is (limit == 4)
        assert RASTER_EXECUTION_BUDGET.debug_values()['active_native_jobs'] == limit
        RASTER_EXECUTION_BUDGET.configure(3)
        other_source.block = {'0.png'}
        other.request(_request(1, _unit(0)))
        assert not other_source.entered['0.png'].wait(.05)
        source.release['1.png'].set()
        if limit == 4:
            _wait_until(qapp, lambda: RASTER_EXECUTION_BUDGET.debug_values()['owned_permits'] == 3)
            assert not other_source.entered['0.png'].is_set()
            source.release['2.png'].set()
        assert other_source.entered['0.png'].wait(1)
        assert RASTER_EXECUTION_BUDGET.debug_values()['active_native_jobs'] <= 3
        source.unblock()
        other_source.unblock()
        _wait_until(qapp, lambda: any(frame.request_id == 2 for frame in frames))
        assert source.calls['4.png'] == source.calls['5.png'] == 1
    finally:
        source.unblock(); other_source.unblock()
        close_runtime(runtime, source, qapp)
        close_runtime(other, other_source, qapp)
        RASTER_EXECUTION_BUDGET.configure(3)


def test_shared_cap_preferences_immediate_apply_save_reset_and_invalid_values(tmp_path, qapp):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    assert config.get('viewer_raster_execution_limit') == 4
    for value in (None, True, False, 0, 2, 5, '4', 4.0, []):
        config.apply({'viewer_raster_execution_limit': value})
        assert config.get('viewer_raster_execution_limit') == 4
    config.apply({'viewer_raster_execution_limit': 3})
    a = ViewerWindow(config_manager=config)
    independent = ConfigManager(tmp_path / 'independent.json')
    independent.load()
    independent.apply({'viewer_raster_execution_limit': 4})
    b = ViewerWindow(config_manager=independent)
    dialog = SettingsDialog(config)
    try:
        assert RASTER_EXECUTION_BUDGET.limit == 3
        dialog._select_data(dialog.viewer_raster_execution_limit_combo, 4)
        dialog.apply_settings()
        assert RASTER_EXECUTION_BUDGET.limit == 4
        assert ConfigManager(config.path).load()['viewer_raster_execution_limit'] == 4
        dialog._select_data(dialog.viewer_raster_execution_limit_combo, 3)
        dialog.apply_settings()
        dialog._reset_viewer_scope()
        assert dialog.viewer_raster_execution_limit_combo.currentData() == 4
        dialog.reject()
        assert config.get('viewer_raster_execution_limit') == 3
        config.apply({'viewer_raster_execution_limit': 4})
        independent.apply({'viewer_raster_execution_limit': 3})
        assert RASTER_EXECUTION_BUDGET.limit == 3
        b.prepare_shutdown(wait_msecs=3000)
        assert RASTER_EXECUTION_BUDGET.limit == 4
    finally:
        dialog.close()
        for window in (a, b):
            window.prepare_shutdown(wait_msecs=3000)
            window.close()
        qapp.processEvents()
        RASTER_EXECUTION_BUDGET.configure(3)
