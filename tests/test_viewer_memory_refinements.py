"""Pressure/Auto/grace regressions: deterministic and no Qt or filesystem I/O."""
from __future__ import annotations

import pytest

from app.viewer_memory_policy import GIB, MIB, PhysicalMemorySnapshot, ResolvedViewerMemoryPolicy


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def snapshot(*, total=16 * GIB, available=12 * GIB, used=3 * GIB):
    return PhysicalMemorySnapshot(total, available, used)


def auto_policy():
    clock = Clock()
    policy = ResolvedViewerMemoryPolicy(
        "auto", snapshot=snapshot(available=6 * GIB), clock=clock,
    )
    assert policy.hard_limit_bytes == 2 * GIB
    return clock, policy


def sample(clock, policy, at, *, resident=1800 * MIB, requested=True, snap=None, cap=None):
    clock.now = at
    return policy.observe_memory_pressure(
        snapshot() if snap is None else snap,
        current_cache_bytes=resident,
        auto_growth_requested=requested,
        auto_growth_cap_bytes=cap,
    )


def test_auto_recovers_above_initial_hard_limit_without_reconfigure():
    clock, policy = auto_policy()
    assert sample(clock, policy, 0).hard_limit_bytes == 2 * GIB
    assert sample(clock, policy, 5).hard_limit_bytes == 2 * GIB
    assert sample(clock, policy, 10).hard_limit_bytes == 2 * GIB + 256 * MIB
    assert policy.resolution.mode == "auto"


def test_auto_growth_is_step_and_time_bounded():
    clock, policy = auto_policy()
    sample(clock, policy, 0)
    sample(clock, policy, 5)
    first = sample(clock, policy, 10).hard_limit_bytes
    for at in (10, 10.1, 11, 14.9):
        assert sample(clock, policy, at, resident=2 * GIB).hard_limit_bytes == first
    assert sample(clock, policy, 15, resident=2 * GIB).hard_limit_bytes == first + 256 * MIB


@pytest.mark.parametrize("mode", ["minimal", "256", "512", "1024", "2048", "4096", "8192", "16384", "32768"])
def test_fixed_modes_do_not_expand_even_when_requested(mode):
    clock = Clock()
    policy = ResolvedViewerMemoryPolicy(mode, clock=clock)
    initial = policy.hard_limit_bytes
    for at in range(0, 65, 5):
        sample(clock, policy, at, resident=initial, snap=snapshot(total=128 * GIB, available=80 * GIB, used=initial))
    assert policy.hard_limit_bytes == initial


@pytest.mark.parametrize("requested,resident", [(False, 1800 * MIB), (True, 10 * MIB), (False, 10 * MIB)])
def test_no_growth_without_live_demand_and_utilization(requested, resident):
    clock, policy = auto_policy()
    for at in range(0, 45, 5):
        sample(clock, policy, at, requested=requested, resident=resident)
    assert policy.hard_limit_bytes == 2 * GIB


def test_inactive_reader_does_not_grow_even_during_retention_grace():
    clock, policy = auto_policy()
    policy.set_active(False)
    for at in range(0, 45, 5):
        sample(clock, policy, at)
    assert policy.hard_limit_bytes == 2 * GIB


def test_missing_sample_resets_sustained_headroom():
    clock, policy = auto_policy()
    sample(clock, policy, 0)
    sample(clock, policy, 5)
    policy.reset_growth_observation()
    assert sample(clock, policy, 10).hard_limit_bytes == 2 * GIB
    sample(clock, policy, 15)
    assert sample(clock, policy, 20).hard_limit_bytes > 2 * GIB


@pytest.mark.parametrize("gap", [16, 300, -1])
def test_sample_gap_or_clock_reversal_cannot_prove_sustained_recovery(gap):
    clock, policy = auto_policy()
    sample(clock, policy, 0)
    assert sample(clock, policy, gap).hard_limit_bytes == 2 * GIB


def test_reactivation_restarts_growth_observation():
    clock, policy = auto_policy()
    sample(clock, policy, 0)
    sample(clock, policy, 5)
    policy.set_active(False)
    clock.now = 9
    policy.set_active(True)
    assert sample(clock, policy, 10).hard_limit_bytes == 2 * GIB


@pytest.mark.parametrize("total", [0, -1])
def test_invalid_snapshot_never_grows_and_keeps_last_good_resolution(total):
    clock, policy = auto_policy()
    sample(clock, policy, 0)
    previous = policy.resolution
    assert sample(clock, policy, 10, snap=snapshot(total=total)) is previous
    assert policy.debug_values()["auto_growth_waiting"] is False


@pytest.mark.parametrize("now", [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_clock_never_grows(now):
    clock, policy = auto_policy()
    sample(clock, policy, 0)
    assert sample(clock, policy, now).hard_limit_bytes == 2 * GIB


def test_small_headroom_is_not_an_auto_growth_trigger():
    clock, policy = auto_policy()
    # With 1800 MiB resident, safe capacity is only current hard + 8 MiB.
    reserve = 16 * GIB // 5
    limited = snapshot(available=2 * GIB + 8 * MIB - 1800 * MIB + reserve)
    for at in (0, 5, 10, 15):
        sample(clock, policy, at, snap=limited)
    assert policy.hard_limit_bytes == 2 * GIB


def test_optional_shared_grant_caps_growth_not_fixed_mode_selection():
    clock, policy = auto_policy()
    cap = 2 * GIB + 64 * MIB
    for at in (0, 5, 10, 15, 20):
        sample(clock, policy, at, cap=cap)
    assert policy.hard_limit_bytes == cap


def test_external_zero_grant_denies_growth():
    clock, policy = auto_policy()
    for at in (0, 5, 10, 15):
        sample(clock, policy, at, cap=0)
    assert policy.hard_limit_bytes == 2 * GIB


def test_auto_never_exceeds_existing_32_gib_ceiling():
    clock = Clock()
    policy = ResolvedViewerMemoryPolicy(
        "auto", snapshot=snapshot(total=128 * GIB, available=80 * GIB), clock=clock,
    )
    for at in (0, 5, 10, 15):
        sample(clock, policy, at, resident=30 * GIB, snap=snapshot(total=128 * GIB, available=80 * GIB, used=31 * GIB))
    assert policy.hard_limit_bytes == 32 * GIB


def test_short_focus_change_keeps_cache_target_then_expires():
    clock = Clock()
    policy = ResolvedViewerMemoryPolicy("4096", clock=clock)
    high = policy.target_bytes
    assert policy.set_active(False) == high
    clock.now = 9.99
    assert policy.target_bytes == high
    clock.now = 10
    assert policy.target_bytes == 2 * GIB
    assert policy.set_active(True) == high


def test_repeated_deactivate_does_not_extend_grace():
    clock = Clock()
    policy = ResolvedViewerMemoryPolicy("4096", clock=clock)
    policy.set_active(False)
    clock.now = 9
    policy.set_active(False)
    clock.now = 10
    assert policy.target_bytes == 2 * GIB


def test_constructed_inactive_has_no_grace():
    policy = ResolvedViewerMemoryPolicy("4096", active=False)
    assert policy.target_bytes == 2 * GIB
    assert not policy.inactive_grace_active


def test_pressure_bypasses_focus_grace():
    clock = Clock()
    policy = ResolvedViewerMemoryPolicy("4096", clock=clock)
    policy.set_active(False)
    sample(clock, policy, 1, resident=3 * GIB, snap=snapshot(available=1 * GIB, used=4 * GIB))
    assert policy.inactive_grace_active
    assert policy.target_bytes == 816 * MIB


def test_reconfigure_resets_growth_but_preserves_user_fixed_limit():
    clock, policy = auto_policy()
    sample(clock, policy, 0)
    sample(clock, policy, 5)
    policy.reconfigure("512")
    assert not policy.debug_values()["auto_growth_waiting"]
    for at in (10, 15, 20):
        sample(clock, policy, at, resident=512 * MIB)
    assert policy.hard_limit_bytes == 512 * MIB


def test_growth_stops_when_work_is_finished():
    clock, policy = auto_policy()
    for at in (0, 5, 10):
        sample(clock, policy, at)
    high = policy.hard_limit_bytes
    for at in (15, 20, 25, 30):
        sample(clock, policy, at, resident=high, requested=False)
    assert policy.hard_limit_bytes == high


def test_grace_debug_reports_effective_target_not_immediate_half():
    clock = Clock()
    policy = ResolvedViewerMemoryPolicy("4096", clock=clock)
    policy.set_active(False)
    values = policy.debug_values()
    assert values["active"] is False
    assert values["inactive_grace_active"] is True
    assert values["selected_soft_target_bytes"] == 3584 * MIB


@pytest.mark.parametrize("bad", [
    snapshot(available=-1), snapshot(available=17 * GIB),
    snapshot(used=-1), snapshot(total=float('nan')),
    snapshot(available=float('inf')), snapshot(used=None),
])
def test_invalid_byte_sample_resets_growth_without_changing_limits(bad):
    clock, policy = auto_policy()
    sample(clock, policy, 0)
    previous = sample(clock, policy, 5)
    assert sample(clock, policy, 10, snap=bad) is previous
    assert not policy.debug_values()["auto_growth_waiting"]
    assert sample(clock, policy, 15).hard_limit_bytes == 2 * GIB
