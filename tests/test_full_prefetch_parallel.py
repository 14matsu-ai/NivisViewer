import pytest
from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from app.raster_execution_budget import RASTER_EXECUTION_BUDGET
from app.zip_raster_book_runtime import ZipRasterBookRuntime
from app.image_work_coordinator import ImageWorkCoordinator
from tests.test_viewer_parallel_trials import GateSource, close_runtime
from tests.test_near_prefetch_parallel import known_unit
from tests.test_zip_raster_book_runtime import _request, _write_zip, _wait_until


@pytest.mark.parametrize('workers', [1,2])
def test_full_single_prefetch_uses_four_global_slots_and_latest_cold_waits_for_native(tmp_path,qapp,workers):
    RASTER_EXECUTION_BUDGET.configure(4)
    source=GateSource(_write_zip(tmp_path,pages=8))
    runtime=ZipRasterBookRuntime(source,1,max_active_jobs=workers,spread_parallel=True,current_extra=True,
        near_prefetch_parallel=True,prefetch_execution_mode='full')
    units=[known_unit(i) for i in range(8)]
    frames=[]
    runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1,units[0],*units))
        _wait_until(qapp,lambda:bool(frames))
        source.block={f'{i}.png' for i in (1,2,3,4)}
        runtime.release_continuous_warmup(request_id=1)
        _wait_until(qapp,lambda:all(source.entered[f'{i}.png'].is_set() for i in (1,2,3,4)))
        assert RASTER_EXECUTION_BUDGET.debug_values()['owned_permits']==4
        assert RASTER_EXECUTION_BUDGET.debug_values()['active_native_jobs']==4
        # A cached reversal never waits for any of the four native workers.
        runtime.request(_request(10,units[0],*units))
        assert frames[-1].request_id==10 and frames[-1].cache_hit
        assert source.calls['0.png']==1
        old=tuple(runtime._active_slots())
        runtime.request(_request(2,units[6]))
        runtime.request(_request(3,units[7]))
        assert not source.entered['6.png'].is_set() and not source.entered['7.png'].is_set()
        assert all(job in runtime._inflight_reservations for job in old)
        source.release['1.png'].set()
        _wait_until(qapp,lambda:any(frame.request_id==3 for frame in frames))
        assert source.calls['7.png']==1 and source.calls['6.png']==0
        assert RASTER_EXECUTION_BUDGET.debug_values()['owned_permits']<=4
    finally:
        close_runtime(runtime,source,qapp)
        RASTER_EXECUTION_BUDGET.configure(3)


def test_existing_reserve_owner_prevents_full_lane_borrowing(tmp_path,qapp):
    RASTER_EXECUTION_BUDGET.configure(4)
    archive=_write_zip(tmp_path,pages=8)
    a_source,b_source=GateSource(archive),GateSource(archive)
    a=ZipRasterBookRuntime(a_source,1,spread_parallel=True,near_prefetch_parallel=True)
    b=ZipRasterBookRuntime(b_source,1,max_active_jobs=1,current_extra=True,spread_parallel=True,
        near_prefetch_parallel=True,prefetch_execution_mode='full')
    units=[known_unit(i) for i in range(8)]
    frames=[]
    b.frameReady.connect(frames.append)
    try:
        b.request(_request(1,units[0],*units))
        _wait_until(qapp,lambda:bool(frames))
        b_source.block={'1.png','2.png'}
        b.release_continuous_warmup(request_id=1)
        assert b_source.entered['1.png'].wait(1)
        assert not b_source.entered['2.png'].is_set()
        assert b._extra_job is None and not b._auxiliary_jobs
    finally:
        close_runtime(a,a_source,qapp)
        close_runtime(b,b_source,qapp)
        RASTER_EXECUTION_BUDGET.configure(3)


def test_execution_mode_profile_save_cancel_reset_invalid(tmp_path,qapp):
    config=ConfigManager(tmp_path/'config.json')
    config.load()
    assert config.get('viewer_prefetch_execution_mode')=='reserve'
    for value in (None,True,4,[], 'other'):
        config.apply({'viewer_prefetch_execution_mode':value})
        assert config.get('viewer_prefetch_execution_mode')=='reserve'
    dialog=SettingsDialog(config)
    try:
        assert [dialog.viewer_prefetch_execution_mode_combo.itemData(i)
                for i in range(dialog.viewer_prefetch_execution_mode_combo.count())] == ['reserve','burst','auto']
        dialog._select_data(dialog.viewer_prefetch_execution_mode_combo,'burst')
        dialog.apply_settings()
        assert ConfigManager(config.path).load()['viewer_prefetch_execution_mode']=='burst'
        dialog._reset_viewer_scope()
        assert dialog.viewer_prefetch_execution_mode_combo.currentData()=='reserve'
        dialog.reject()
        assert config.get('viewer_prefetch_execution_mode')=='burst'
        assert config.get('viewer_near_prefetch_parallel') is False
        assert config.get('viewer_raster_execution_limit')==4
    finally:
        dialog.close();qapp.processEvents()


def test_legacy_full_profile_migrates_to_burst_on_load_and_save(tmp_path,qapp):
    import json
    path=tmp_path/'config.json'
    path.write_text(json.dumps({'viewer_prefetch_execution_mode':'full'}),encoding='utf-8')
    config=ConfigManager(path)
    assert config.load()['viewer_prefetch_execution_mode']=='burst'
    dialog=SettingsDialog(config)
    try:
        combo=dialog.viewer_prefetch_execution_mode_combo
        assert combo.currentData()=='burst'
        assert combo.currentText()=='通常4枠＋現在画面2枠（B）'
        assert 'B：' in combo.toolTip() and 'C：' not in combo.toolTip()
        assert all('試行' not in combo.itemText(i) for i in range(combo.count()))
        assert '試行' not in dialog.viewer_near_prefetch_parallel_checkbox.text()
        dialog.apply_settings()
        assert json.loads(path.read_text(encoding='utf-8'))['viewer_prefetch_execution_mode']=='burst'
    finally:
        dialog.close();qapp.processEvents()


def test_full_prefetch_aux_start_failure_releases_preclaimed_permit(tmp_path,qapp,monkeypatch):
    RASTER_EXECUTION_BUDGET.configure(4)
    source=GateSource(_write_zip(tmp_path,pages=8))
    runtime=ZipRasterBookRuntime(source,1,current_extra=True,spread_parallel=True,
        near_prefetch_parallel=True,prefetch_execution_mode='full')
    frames=[];runtime.frameReady.connect(frames.append)
    units=[known_unit(i) for i in range(8)]
    monkeypatch.setattr(RASTER_EXECUTION_BUDGET,'start_background',lambda *_args:False)
    try:
        runtime.request(_request(1,units[0],*units))
        _wait_until(qapp,lambda:bool(frames))
        source.block={'1.png','2.png'}
        runtime.release_continuous_warmup(request_id=1)
        _wait_until(qapp,lambda:source.entered['1.png'].is_set() and source.entered['2.png'].is_set())
        assert RASTER_EXECUTION_BUDGET.debug_values()['owned_permits']==2
        assert not runtime._auxiliary_jobs
        assert len(runtime._inflight_reservations)==2
    finally:
        close_runtime(runtime,source,qapp)
        RASTER_EXECUTION_BUDGET.configure(3)


def test_three_full_viewers_share_coordinator_and_complete_without_retry_storm(tmp_path,qapp):
    RASTER_EXECUTION_BUDGET.configure(4)
    archive=_write_zip(tmp_path,pages=8)
    coordinator=ImageWorkCoordinator(folder_supplemental_workers=1)
    sources=[GateSource(archive) for _ in range(3)]
    runtimes=[ZipRasterBookRuntime(source,1,max_active_jobs=2,current_extra=True,spread_parallel=True,
        near_prefetch_parallel=True,prefetch_execution_mode='full',image_work_coordinator=coordinator) for source in sources]
    units=[known_unit(i) for i in range(8)]
    frames=[[],[],[]]
    try:
        for index,runtime in enumerate(runtimes):
            runtime.frameReady.connect(frames[index].append)
            runtime.request(_request(1,units[0],*units))
        _wait_until(qapp,lambda:all(frames))
        for runtime in runtimes:runtime.release_continuous_warmup(request_id=1)
        _wait_until(qapp,lambda:all(runtime.cached_unit_count==8 for runtime in runtimes),timeout_ms=5000)
        assert all(source.calls=={f'{i}.png':1 for i in range(8)} for source in sources)
        assert RASTER_EXECUTION_BUDGET.debug_values()['owned_permits']<=4
    finally:
        for runtime,source in zip(runtimes,sources):close_runtime(runtime,source,qapp)
        assert coordinator.shutdown(wait_msecs=5000)
        RASTER_EXECUTION_BUDGET.configure(3)
