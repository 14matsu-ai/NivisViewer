"""Pure tests; all memory figures below are controlled inputs, not measurements."""
from collections import OrderedDict
from types import SimpleNamespace
import random

import pytest
from app.browser_thumbnail_memory_policy import (
    AUTO_MAX_BYTES, BrowserMemoryGovernor, GIB, MIB, MemoryDemand,
    cache_victims, desired_limit, fair_grants, frame_byte_estimate,
    hot_row_order, normalize_memory_mode, physical_group_capacity,
)


def memory(available=100 * GIB, total=128 * GIB):
    return SimpleNamespace(total_physical_bytes=total,
                           available_physical_bytes=available)


@pytest.mark.parametrize('value,expected', [(None,'auto'),(True,'auto'),('', 'auto'),
    ('AUTO','auto'),('512','512'),(1024,'1024'),('unlimited','auto'),('8192','auto')])
def test_mode_validation(value, expected):
    assert normalize_memory_mode(value) == expected


@pytest.mark.parametrize('need,expected',[(0,128*MIB),(100*MIB,128*MIB),
    (350*MIB,512*MIB),(700*MIB,GIB),(1400*MIB,2*GIB),(200*GIB,2*GIB)])
def test_auto_buckets(need,expected):
    assert desired_limit('auto', need) == expected


def test_manual_and_hidden_are_caps_not_allocations():
    assert desired_limit('1024',1) == GIB
    assert desired_limit('1024',GIB,visible=False) == 32*MIB
    assert desired_limit('auto',GIB,visible=False) == 32*MIB


def test_unknown_sample_is_one_group_fallback():
    assert physical_group_capacity(None,10*GIB) == 256*MIB
    assert physical_group_capacity(memory(total=0,available=0),0) == 256*MIB


def test_zero_headroom_has_no_forced_128_mib_minimum():
    assert physical_group_capacity(memory(available=0,total=8*GIB),128*MIB) == 0


def test_retained_browser_bytes_added_back_once_no_viewer_addback():
    snap=memory(available=3*GIB,total=8*GIB)
    assert physical_group_capacity(snap,100*MIB)-physical_group_capacity(snap,0) == 100*MIB


def test_allocator_shares_small_cap_without_overgrant():
    assert fair_grants({1:100,2:100,3:20},120) == {3:20,1:50,2:50}
    assert sum(fair_grants({1:GIB,2:GIB},256*MIB).values()) == 256*MIB


def test_allocator_property_random_demands():
    rng=random.Random(42)
    for _ in range(500):
        wants={i:rng.randrange(4*GIB) for i in range(rng.randrange(1,12))}
        cap=rng.randrange(16*GIB)
        grants=fair_grants(wants,cap)
        assert sum(grants.values()) <= cap
        assert all(0<=grants[k]<=wants[k] for k in grants)


def test_normal_demand_growth_immediate_when_headroom_already_sampled():
    governor=BrowserMemoryGovernor()
    governor.sample(memory(),0)
    assert governor.resolve({1:MemoryDemand('auto',0)},0)[1].limit_bytes == 128*MIB
    assert governor.resolve({1:MemoryDemand('auto',700*MIB)},1)[1].limit_bytes == GIB


def test_pressure_shrink_immediate_recovery_time_based_and_bounded():
    governor=BrowserMemoryGovernor()
    demand={1:MemoryDemand('auto',700*MIB)}
    governor.sample(memory(),0)
    assert governor.resolve(demand,0)[1].limit_bytes == GIB
    governor.sample(memory(available=0,total=8*GIB),GIB)
    assert governor.resolve(demand,5)[1].limit_bytes == 0
    governor.sample(memory(),0)
    assert governor.resolve(demand,10)[1].limit_bytes == 0
    for _ in range(100):
        assert governor.resolve(demand,10.01)[1].limit_bytes == 0
    assert governor.resolve(demand,19)[1].limit_bytes == 0
    assert governor.resolve(demand,20)[1].limit_bytes == 256*MIB
    assert governor.resolve(demand,25)[1].limit_bytes == 512*MIB
    assert governor.resolve(demand,30)[1].limit_bytes == 768*MIB
    assert governor.resolve(demand,35)[1].limit_bytes == GIB


def test_new_low_sample_resets_recovery():
    governor=BrowserMemoryGovernor()
    demand={1:MemoryDemand('auto',700*MIB)}
    governor.sample(None,0)
    governor.resolve(demand,0)
    governor.sample(memory(),0)
    governor.resolve(demand,5)
    governor.sample(None,0)
    governor.resolve(demand,10)
    governor.sample(memory(),0)
    assert governor.resolve(demand,15)[1].limit_bytes == 256*MIB
    assert governor.resolve(demand,24)[1].limit_bytes == 256*MIB


def test_two_browsers_and_removed_client_do_not_duplicate_headroom():
    governor=BrowserMemoryGovernor()
    governor.sample(None,0)
    result=governor.resolve({1:MemoryDemand('auto',700*MIB),2:MemoryDemand('auto',700*MIB)},0)
    assert sum(value.limit_bytes for value in result.values()) == 256*MIB
    assert 2 not in governor.resolve({1:MemoryDemand('auto',700*MIB)},5)


def test_capacity_frozen_between_os_samples():
    governor=BrowserMemoryGovernor()
    governor.sample(memory(available=3*GIB,total=8*GIB),0)
    cap=governor.capacity
    for t in range(10):
        governor.resolve({1:MemoryDemand('2048',0)},t)
        assert governor.capacity==cap


@pytest.mark.parametrize('direction',[-1,1])
def test_hot_scope_order_and_recenter(direction):
    rows=hot_row_order(count=1000,first=160,last=199,direction=direction,screens=3)
    assert rows[:40] == tuple(range(160,200))
    assert len(rows)==280
    expected=tuple(range(200,240)) if direction>0 else tuple(range(159,119,-1))
    assert rows[40:80]==expected
    reverse_safety = tuple(range(159,149,-1)) if direction>0 else tuple(range(200,210))
    assert rows[80:90] == reverse_safety
    assert min(rows)==40 and max(rows)==319


def test_unlimited_generation_does_not_mean_unlimited_ram():
    assert hot_row_order(count=1000000,first=160,last=199,direction=1,screens=-1)==hot_row_order(
        count=1000000,first=160,last=199,direction=1,screens=3)


def test_zero_scope_and_short_listing_and_selected():
    assert hot_row_order(count=0,first=0,last=0,direction=1,screens=3)==()
    rows=hot_row_order(count=20,first=0,last=4,direction=1,screens=0,selected=[19,19,1])
    assert rows==(0,1,2,3,4,19)
    assert frame_byte_estimate(SimpleNamespace(frame_width=512,frame_height=768))==1572864


def test_huge_selection_cannot_displace_the_immediate_next_viewport():
    rows = hot_row_order(
        count=100000, first=0, last=39, direction=1, screens=3,
        selected=range(1000, 71000),
    )
    assert rows[:40] == tuple(range(40))
    assert rows[40:80] == tuple(range(40, 80))
    assert sum(1000 <= row < 71000 for row in rows) == 128
    assert len(rows) <= 40 + 40 * 6 + 128
    assert len(rows) <= 65536


def test_ranked_cache_evicts_old_position_not_new_next_screen():
    # A full cache whose old viewport is behind us; new hot result must stay.
    costs=OrderedDict((i,1) for i in range(128))
    costs[200]=1
    ranks={i:i-160 for i in range(160,280)}
    victims=cache_victims(costs,ranks,byte_limit=128,entry_limit=65536)
    assert victims==(0,)
    assert 200 not in victims


def test_far_result_cannot_evict_more_valuable_near_results():
    costs=OrderedDict((i,1) for i in range(128))
    costs[500]=1
    assert cache_victims(costs,{i:i for i in range(128)},byte_limit=128,entry_limit=65536)==(500,)


def test_no_historical_128_item_cap_and_hard_bytes_remain():
    costs=OrderedDict((i,256*1024) for i in range(1200))
    assert cache_victims(costs,{},byte_limit=512*MIB,entry_limit=65536)==()
    victims=cache_victims(costs,{},byte_limit=128*MIB,entry_limit=65536)
    assert len(victims)==688
    assert sum(value for key,value in costs.items() if key not in victims)<=128*MIB


def test_zero_and_oversized_budgets_do_not_overflow():
    assert cache_victims({1:20},{1:0},byte_limit=19,entry_limit=65536)==(1,)
    assert cache_victims({1:20},{1:0},byte_limit=0,entry_limit=65536)==(1,)


@pytest.mark.parametrize('total,available',[(True,1),('128',1),(GIB,True),(GIB,2*GIB),(GIB,-1)])
def test_invalid_snapshot_never_offers_large_unknown_capacity(total,available):
    governor=BrowserMemoryGovernor()
    governor.sample(memory(total=total,available=available),0)
    grants=governor.resolve({1:MemoryDemand('2048',0)},0)
    assert grants[1].limit_bytes<=256*MIB
    assert grants[1].reason=='fallback'
