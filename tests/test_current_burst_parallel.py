import pytest
from app.raster_execution_budget import RASTER_EXECUTION_BUDGET
from app.zip_raster_book_runtime import ZipRasterBookRuntime
from tests.test_full_prefetch_parallel import known_unit
from tests.test_viewer_parallel_trials import GateSource,close_runtime
from tests.test_zip_raster_book_runtime import _request,_write_zip,_wait_until


def test_four_background_plus_current_pair_is_six_and_cancel_never_invents_more(tmp_path,qapp):
    RASTER_EXECUTION_BUDGET.configure(4)
    source=GateSource(_write_zip(tmp_path,pages=8))
    runtime=ZipRasterBookRuntime(source,1,max_active_jobs=1,current_extra=True,spread_parallel=True,
        near_prefetch_parallel=True,prefetch_execution_mode='burst')
    units=[known_unit(i) for i in range(8)]
    frames=[];runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1,units[0],*units))
        _wait_until(qapp,lambda:bool(frames))
        source.block={f'{i}.png' for i in (1,2,3,4,6,7)}
        runtime.release_continuous_warmup(request_id=1)
        _wait_until(qapp,lambda:all(source.entered[f'{i}.png'].is_set() for i in (1,2,3,4)))
        assert RASTER_EXECUTION_BUDGET.debug_values()['emergency_permits']==0
        runtime.request(_request(2,known_unit(6,7)))
        assert source.entered['6.png'].wait(1) and source.entered['7.png'].wait(1)
        values=RASTER_EXECUTION_BUDGET.debug_values()
        assert values['active_native_jobs']==6 and values['emergency_permits']==2 and values['normal_permits']==4
        old=runtime._emergency_job
        runtime.request(_request(3,units[5]))
        assert not source.entered['5.png'].is_set()
        assert RASTER_EXECUTION_BUDGET.debug_values()['owned_permits']==6
        assert old in runtime._inflight_reservations
        source.release['6.png'].set();source.release['7.png'].set()
        _wait_until(qapp,lambda:any(frame.request_id==3 for frame in frames))
        assert all(frame.request_id!=2 for frame in frames)
        assert source.calls['5.png']==1
        assert RASTER_EXECUTION_BUDGET.debug_values()['emergency_permits']<=2
    finally:
        close_runtime(runtime,source,qapp)
        RASTER_EXECUTION_BUDGET.configure(3)


@pytest.mark.parametrize('strict',['reserve','full','limit3'])
def test_stricter_owner_or_three_limit_disables_emergency(tmp_path,qapp,strict):
    RASTER_EXECUTION_BUDGET.configure(3 if strict=='limit3' else 4)
    archive=_write_zip(tmp_path,pages=8)
    source=GateSource(archive)
    runtime=ZipRasterBookRuntime(source,1,current_extra=True,spread_parallel=True,near_prefetch_parallel=True,prefetch_execution_mode='burst')
    other_source=GateSource(archive)
    other=ZipRasterBookRuntime(other_source,1,prefetch_execution_mode=strict if strict!='limit3' else 'burst')
    try:
        assert RASTER_EXECUTION_BUDGET.debug_values()['effective_native_limit']==(3 if strict=='limit3' else 4)
    finally:
        close_runtime(runtime,source,qapp);close_runtime(other,other_source,qapp)
        RASTER_EXECUTION_BUDGET.configure(3)


def test_existing_prefetch_is_promoted_and_second_half_starts_without_duplicate_first(tmp_path,qapp):
    RASTER_EXECUTION_BUDGET.configure(4)
    source=GateSource(_write_zip(tmp_path,pages=8))
    runtime=ZipRasterBookRuntime(source,1,max_active_jobs=2,current_extra=True,spread_parallel=True,
        near_prefetch_parallel=True,prefetch_execution_mode='burst')
    units=[known_unit(i,i+1) for i in range(0,8,2)]
    frames=[];runtime.frameReady.connect(frames.append)
    try:
        runtime.request(_request(1,units[0],*units))
        _wait_until(qapp,lambda:bool(frames))
        source.block={'2.png','3.png','4.png','6.png','7.png'}
        runtime.release_continuous_warmup(request_id=1)
        _wait_until(qapp,lambda:all(source.entered[f'{i}.png'].is_set() for i in (2,3,4,6)))
        assert not source.entered['7.png'].is_set()
        old=next(job for job in runtime._active_slots() if job.unit.identity==units[3].identity)
        runtime.request(_request(2,units[3],*units),preserve_started_compatible=True)
        assert source.entered['7.png'].wait(1)
        assert any(job is old for job in runtime._active_slots())
        assert source.calls['6.png']==1
        source.release['6.png'].set();source.release['7.png'].set()
        _wait_until(qapp,lambda:any(frame.request_id==2 for frame in frames))
        assert source.calls['6.png']==source.calls['7.png']==1
    finally:
        close_runtime(runtime,source,qapp)
        RASTER_EXECUTION_BUDGET.configure(3)
