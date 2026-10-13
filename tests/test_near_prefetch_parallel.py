from dataclasses import replace

import pytest
from app.config_manager import ConfigManager
from app.raster_execution_budget import RASTER_EXECUTION_BUDGET
from app.settings_dialog import SettingsDialog
from app.viewer_window import ViewerWindow
from app.image_source import ImageSourceError
from app.zip_raster_book_runtime import ZipRasterBookRuntime
from tests.test_viewer_parallel_trials import GateSource, close_runtime
from tests.test_zip_raster_book_runtime import _request, _unit, _write_zip, _wait_until


def known_unit(*indexes):
    unit = _unit(*indexes)
    return replace(unit, pages=tuple(replace(page, known_size=(120, 180), is_animated=False) for page in unit.pages))


@pytest.mark.parametrize('enabled', [False, True])
def test_closest_prefetch_parallel_and_current_has_its_own_helper(tmp_path, qapp, enabled):
    RASTER_EXECUTION_BUDGET.configure(4)
    source = GateSource(_write_zip(tmp_path, pages=8))
    runtime = ZipRasterBookRuntime(source, 1, max_active_jobs=1, current_extra=True,
                                  spread_parallel=True, near_prefetch_parallel=enabled)
    units = [known_unit(i, i + 1) for i in range(0, 8, 2)]
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1, units[0], *units))
        _wait_until(qapp, lambda: bool(frames))
        source.block = {'2.png', '3.png', '6.png', '7.png'}
        runtime.release_continuous_warmup(request_id=1)
        assert source.entered['2.png'].wait(1)
        assert source.entered['3.png'].wait(.1) is enabled
        assert not source.entered['4.png'].is_set()
        assert RASTER_EXECUTION_BUDGET.debug_values()['background_permits'] <= 2
        old = runtime._active_job
        runtime.request(_request(2, units[3]))
        assert source.entered['6.png'].wait(1) and source.entered['7.png'].wait(1)
        assert old in runtime._inflight_reservations and not old.finished.is_set()
        assert RASTER_EXECUTION_BUDGET.debug_values()['owned_permits'] == (4 if enabled else 3)
        source.release['6.png'].set(); source.release['7.png'].set()
        _wait_until(qapp, lambda: any(frame.request_id == 2 for frame in frames))
        assert old in runtime._inflight_reservations
        assert source.calls['6.png'] == source.calls['7.png'] == 1
        assert len(frames[-1].pages) == 2
    finally:
        close_runtime(runtime, source, qapp)
        RASTER_EXECUTION_BUDGET.configure(3)


@pytest.mark.parametrize('workers', [1, 2])
@pytest.mark.parametrize('limit', [3, 4])
def test_next_one_finishes_before_next_two_and_global_headroom_is_not_per_window(tmp_path, qapp, workers, limit):
    RASTER_EXECUTION_BUDGET.configure(limit)
    archive = _write_zip(tmp_path, pages=8)
    sources = [GateSource(archive) for _ in range(2)]
    runtimes = [ZipRasterBookRuntime(source, 1, max_active_jobs=workers, current_extra=True,
                spread_parallel=True, near_prefetch_parallel=True) for source in sources]
    units = [known_unit(i, i + 1) for i in range(0, 8, 2)]
    frames = [[], []]
    prepared = [[], []]
    try:
        for index, runtime in enumerate(runtimes):
            runtime.frameReady.connect(frames[index].append)
            runtime.artifactReady.connect(lambda frame, owner=index: prepared[owner].append(frame.unit.start_index))
            runtime.request(_request(1, units[0], *units))
        _wait_until(qapp, lambda: all(frames))
        for source, runtime in zip(sources, runtimes):
            source.block = {'2.png', '3.png'}
            runtime.release_continuous_warmup(request_id=1)
        _wait_until(qapp, lambda: RASTER_EXECUTION_BUDGET.debug_values()['background_permits'] == 2)
        assert all(not source.entered['4.png'].is_set() for source in sources)
        assert RASTER_EXECUTION_BUDGET.debug_values()['owned_permits'] <= 2
        for source in sources:
            source.unblock()
        _wait_until(qapp, lambda: all(runtime.cached_unit_count == 4 for runtime in runtimes), timeout_ms=5000)
        assert all(source.calls == {f'{i}.png': 1 for i in range(8)} for source in sources)
        assert all(order.index(2) < order.index(4) for order in prepared)
    finally:
        for source in sources:
            source.unblock()
        for runtime, source in zip(runtimes, sources):
            close_runtime(runtime, source, qapp)
        RASTER_EXECUTION_BUDGET.configure(3)


def test_near_option_save_reset_cancel_and_existing_spread_off(tmp_path, qapp):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    assert config.get('viewer_near_prefetch_parallel') is False
    dialog = SettingsDialog(config)
    try:
        dialog.viewer_near_prefetch_parallel_checkbox.setChecked(True)
        dialog.apply_settings()
        assert ConfigManager(config.path).load()['viewer_near_prefetch_parallel'] is True
        dialog._reset_viewer_scope()
        assert not dialog.viewer_near_prefetch_parallel_checkbox.isChecked()
        dialog.reject()
        assert config.get('viewer_near_prefetch_parallel') is True
    finally:
        dialog.close()
    source = GateSource(_write_zip(tmp_path, pages=2))
    runtime = ZipRasterBookRuntime(source, 1, spread_parallel=False, near_prefetch_parallel=True)
    try:
        assert not runtime._near_prefetch_parallel
    finally:
        close_runtime(runtime, source, qapp)


def test_near_prefetch_refuses_oversized_work_and_keeps_displayed_frame(tmp_path, qapp):
    RASTER_EXECUTION_BUDGET.configure(4)
    source = GateSource(_write_zip(tmp_path, pages=4))
    runtime = ZipRasterBookRuntime(source, 1, spread_parallel=True, current_extra=True,
                                  near_prefetch_parallel=True, cache_byte_budget=8*1024*1024)
    current = known_unit(0, 1)
    next_unit = known_unit(2, 3)
    next_unit = replace(next_unit, pages=tuple(replace(page, known_size=(4096,6500)) for page in next_unit.pages))
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1, current, current, next_unit))
        _wait_until(qapp, lambda: bool(frames))
        runtime.release_continuous_warmup(request_id=1)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert source.calls == {'0.png': 1, '1.png': 1}
        assert frames[-1].unit == current
        assert runtime.cache_debug_values()['inflight_reservation_bytes'] == 0
    finally:
        close_runtime(runtime, source, qapp)
        RASTER_EXECUTION_BUDGET.configure(3)


def test_near_helper_error_releases_slots_and_keeps_displayed_artifact(tmp_path, qapp):
    class FailingSource(GateSource):
        def open_image(self, image_id):
            if image_id == '3.png':
                raise ImageSourceError('decode failure')
            return super().open_image(image_id)
    RASTER_EXECUTION_BUDGET.configure(4)
    source = FailingSource(_write_zip(tmp_path, pages=4))
    runtime = ZipRasterBookRuntime(source, 1, spread_parallel=True, near_prefetch_parallel=True)
    units = [known_unit(0,1), known_unit(2,3)]
    artifacts = []
    runtime.artifactReady.connect(artifacts.append)
    try:
        runtime.request(_request(1, units[0], *units))
        _wait_until(qapp, lambda: bool(artifacts))
        runtime.release_continuous_warmup(request_id=1)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert len(artifacts) == 1 and artifacts[0].unit == units[0]
        assert runtime._key_for(units[1], runtime._current_request.render_spec) in runtime._failed_prefetch
        assert RASTER_EXECUTION_BUDGET.debug_values()['owned_permits'] == 0
    finally:
        close_runtime(runtime, source, qapp)
        RASTER_EXECUTION_BUDGET.configure(3)


def test_near_option_changes_next_book_without_mutating_owned_runtime(tmp_path, qapp):
    archive = _write_zip(tmp_path, pages=4)
    config = ConfigManager(tmp_path/'config.json')
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        assert window.open_path(archive)
        _wait_until(qapp, lambda: window.book_session.viewer_runtime is not None)
        old = window.book_session.viewer_runtime
        assert not old._near_prefetch_parallel
        config.apply({'viewer_near_prefetch_parallel': True})
        assert window.book_session.viewer_runtime is old and not old._near_prefetch_parallel
        assert window.open_path(archive)
        _wait_until(qapp, lambda: window.book_session.viewer_runtime is not old)
        assert window.book_session.viewer_runtime._near_prefetch_parallel
    finally:
        window.prepare_shutdown(wait_msecs=5000)
        window.close()
        qapp.processEvents()


def test_former_current_is_demoted_and_idle_near_helper_serves_latest_current(tmp_path, qapp):
    RASTER_EXECUTION_BUDGET.configure(4)
    source = GateSource(_write_zip(tmp_path, pages=8))
    runtime = ZipRasterBookRuntime(source, 1, max_active_jobs=1, current_extra=True,
                                  spread_parallel=True, near_prefetch_parallel=True)
    units = [known_unit(i,i+1) for i in range(0,8,2)]
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1,units[0]))
        _wait_until(qapp, lambda:bool(frames))
        source.block={'2.png','3.png','6.png','7.png'}
        runtime.request(_request(2,units[1],*units))
        assert source.entered['2.png'].wait(1) and source.entered['3.png'].wait(1)
        old=runtime._active_job
        runtime.request(_request(3,units[0],*units))
        assert not old.current_priority and not old.cancelled.is_set()
        assert RASTER_EXECUTION_BUDGET.debug_values()['background_permits']==2
        runtime.request(_request(4,units[3]))
        assert source.entered['6.png'].wait(1) and source.entered['7.png'].wait(1)
        assert not old.finished.is_set()
        source.release['6.png'].set(); source.release['7.png'].set()
        _wait_until(qapp, lambda:any(frame.request_id==4 for frame in frames))
        assert old in runtime._inflight_reservations
        assert source.calls['6.png']==source.calls['7.png']==1
    finally:
        close_runtime(runtime,source,qapp)
        RASTER_EXECUTION_BUDGET.configure(3)
