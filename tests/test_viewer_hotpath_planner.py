"""Pure planner regressions: real production topology, plan and planner."""
from __future__ import annotations
from dataclasses import dataclass
import pytest
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan, RasterWarmupPlanner, WarmupStopReason

@dataclass(frozen=True)
class Unit:
    identity: tuple[int,...]


def make_plan(count=4097,current=0,direction=1,nearby=()):
    units=tuple(Unit((i,)) for i in range(count))
    topology=RasterBookTopology(units,identity_of=lambda u:u.identity,
        page_indexes_of=lambda u:u.identity,page_count=count)
    return RasterWarmupPlan(topology,current=units[current],identity_of=lambda u:u.identity,
        page_indexes_of=lambda u:u.identity,direction=direction,background_enabled=True,nearby_units=nearby)


def next_unit(planner,ready,limit=64):
    return planner.next_candidate(identity_of=lambda u:u.identity,is_ready=ready,
        is_terminal_failure=lambda u:False,scan_limit=limit)

@pytest.mark.parametrize('count',[2,65,66,4097,16385])
def test_full_ready_book_yields_until_really_complete(count):
    planner=RasterWarmupPlanner(make_plan(count));planner.release_after_first_commit()
    calls=[];turns=0
    while True:
        assert next_unit(planner,lambda u:calls.append(u.identity) or True) is None
        assert planner.last_scan_count<=64
        turns+=1
        if planner.book_complete:break
        assert planner.stop_reason is WarmupStopReason.YIELDED
        assert turns<=count//64+2
    assert len(calls)==count-1 and len(set(calls))==count-1
    assert planner.stop_reason is WarmupStopReason.COMPLETE


def test_later_miss_is_not_mistaken_for_complete():
    p=RasterWarmupPlanner(make_plan(4098));p.release_after_first_commit()
    found=None
    for _ in range(66):
        found=next_unit(p,lambda u:u.identity!=(4097,))
        if found is not None:break
        assert not p.book_complete
    assert found is not None and found.identity==(4097,)

@pytest.mark.parametrize('direction',[-1,0,1])
def test_slicing_keeps_exact_order_for_mixed_ready_and_sliding_units(direction):
    plan=make_plan(100,50,direction,nearby=(Unit((50,51)),Unit((49,50))))
    full=RasterWarmupPlanner(plan);sliced=RasterWarmupPlanner(plan)
    for p in (full,sliced):p.release_after_first_commit()
    ready=lambda u:sum(u.identity)%7!=0
    reference=[]
    while (u:=next_unit(full,ready,None)) is not None:reference.append(u.identity)
    actual=[]
    for _ in range(200):
        u=next_unit(sliced,ready,3)
        if u is not None:actual.append(u.identity)
        elif sliced.book_complete:break
    assert actual==reference


def test_recenter_refills_evicted_data_not_permanent_visited_state():
    plan=make_plan(100);p=RasterWarmupPlanner(plan);p.release_after_first_commit()
    assert next_unit(p,lambda _:True,None) is None
    p.recenter(plan)
    assert next_unit(p,lambda u:u.identity!=(4,)).identity==(4,)


def test_pending_yield_recenter_uses_new_current_direction():
    p=RasterWarmupPlanner(make_plan(1000));p.release_after_first_commit()
    assert next_unit(p,lambda _:True,8) is None
    assert p.stop_reason is WarmupStopReason.YIELDED
    p.recenter(make_plan(1000,700,-1))
    assert next_unit(p,lambda _:False).identity==(699,)


def test_capacity_skips_and_reset_remain_retriable():
    p=RasterWarmupPlanner(make_plan(100));p.release_after_first_commit()
    for i in range(1,100):p.mark_capacity_skip((i,))
    assert next_unit(p,lambda _:pytest.fail('skip should precede lookup'),8) is None
    assert p.last_scan_count==8 and not p.book_complete
    while not p.book_complete:next_unit(p,lambda _:True,8)
    assert p.stop_reason is WarmupStopReason.COMPLETE_WITH_SKIPS
    p.reset_capacity()
    assert next_unit(p,lambda _:False).identity==(1,)

@pytest.mark.parametrize('stop', ['suspend','stop_for_soft_target','stop_for_hard_limit'])
def test_real_planner_stop_does_not_become_yield(stop):
    p=RasterWarmupPlanner(make_plan(100));p.release_after_first_commit()
    getattr(p,stop)();reason=p.stop_reason
    assert next_unit(p,lambda _:pytest.fail('must stay stopped')) is None
    assert p.stop_reason is reason and p.last_scan_count==0
