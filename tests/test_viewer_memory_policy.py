from __future__ import annotations

import pytest

from app.viewer_memory_policy import (
    GIB,
    MIB,
    PhysicalMemorySnapshot,
    ResolvedViewerMemoryPolicy,
    resolve_viewer_memory_budget,
    viewer_memory_mode_from_legacy_mib,
)


@pytest.mark.parametrize(
    ("mode", "expected_mib"),
    (
        ("minimal", 128),
        ("256", 256),
        ("512", 512),
        ("1024", 1024),
        ("2048", 2048),
        ("4096", 4096),
        ("8192", 8192),
        ("16384", 16384),
        ("32768", 32768),
    ),
)
def test_fixed_memory_modes_resolve_to_exact_bytes(
    mode: str,
    expected_mib: int,
) -> None:
    resolved = resolve_viewer_memory_budget(mode)

    assert resolved.mode == mode
    assert resolved.bytes == expected_mib * MIB
    assert resolved.hard_limit_bytes == expected_mib * MIB
    assert resolved.active_soft_target_bytes == expected_mib * MIB * 7 // 8
    assert resolved.inactive_soft_target_bytes == expected_mib * MIB // 2


def test_auto_uses_one_injected_snapshot_and_stable_bucket() -> None:
    snapshot = PhysicalMemorySnapshot(
        total_physical_bytes=16 * GIB,
        available_physical_bytes=8 * GIB,
        process_working_set_bytes=1 * GIB,
    )

    empty = resolve_viewer_memory_budget(
        "auto",
        snapshot=snapshot,
        current_cache_bytes=0,
    )
    populated = resolve_viewer_memory_budget(
        "auto",
        snapshot=snapshot,
        current_cache_bytes=512 * MIB,
    )

    assert empty.snapshot is snapshot
    assert empty.os_reserve_bytes == 16 * GIB // 5
    assert empty.mib == 4096
    # Cache already resident in the process is not charged a second time.  A
    # small change stays in the same bucket instead of making the limit flap.
    assert populated.mib == 4096


def test_auto_preserves_os_reserve_and_has_safe_floor() -> None:
    constrained = resolve_viewer_memory_budget(
        "auto",
        snapshot=PhysicalMemorySnapshot(
            total_physical_bytes=8 * GIB,
            available_physical_bytes=1 * GIB,
            process_working_set_bytes=768 * MIB,
        ),
    )

    assert constrained.os_reserve_bytes == 2 * GIB
    assert constrained.mib == 128

    pressured_existing_cache = resolve_viewer_memory_budget(
        "auto",
        snapshot=PhysicalMemorySnapshot(
            total_physical_bytes=16 * GIB,
            available_physical_bytes=1 * GIB,
            process_working_set_bytes=8 * GIB,
        ),
        current_cache_bytes=8 * GIB,
    )

    # Available memory below the reserve must shrink an existing cache rather
    # than treating every already-resident byte as permanently protected.
    assert pressured_existing_cache.mib == 4096


@pytest.mark.parametrize("legacy", (None, 0, -1, "auto"))
def test_legacy_auto_sentinels_do_not_migrate_to_minimal(legacy: object) -> None:
    assert viewer_memory_mode_from_legacy_mib(legacy) == "auto"


def test_active_and_inactive_targets_share_one_stable_hard_limit() -> None:
    policy = ResolvedViewerMemoryPolicy("4096")

    assert policy.hard_limit_bytes == 4 * GIB
    assert policy.target_bytes == 3584 * MIB
    assert policy.set_active(False) == 2 * GIB
    assert policy.hard_limit_bytes == 4 * GIB
    assert policy.set_active(True) == 3584 * MIB


def test_live_pressure_shrinks_immediately_and_recovers_incrementally() -> None:
    policy = ResolvedViewerMemoryPolicy("4096")
    pressured = PhysicalMemorySnapshot(
        total_physical_bytes=16 * GIB,
        available_physical_bytes=1 * GIB,
        process_working_set_bytes=4 * GIB,
    )

    shrunk = policy.observe_memory_pressure(
        pressured,
        current_cache_bytes=3 * GIB,
    )

    assert shrunk.hard_limit_bytes == 4 * GIB
    assert shrunk.pressure_ceiling_bytes == 816 * MIB
    assert shrunk.active_soft_target_bytes == 816 * MIB
    assert shrunk.inactive_soft_target_bytes == 816 * MIB

    recovered = policy.observe_memory_pressure(
        PhysicalMemorySnapshot(
            total_physical_bytes=16 * GIB,
            available_physical_bytes=12 * GIB,
            process_working_set_bytes=2 * GIB,
        ),
        current_cache_bytes=816 * MIB,
    )

    assert recovered.hard_limit_bytes == 4 * GIB
    assert recovered.pressure_ceiling_bytes == 1328 * MIB
    assert recovered.active_soft_target_bytes == 1328 * MIB


def test_pressure_recovery_ignores_small_available_memory_jitter() -> None:
    policy = ResolvedViewerMemoryPolicy("1024")
    first = policy.observe_memory_pressure(
        PhysicalMemorySnapshot(
            total_physical_bytes=8 * GIB,
            available_physical_bytes=2 * GIB,
            process_working_set_bytes=2 * GIB,
        ),
        current_cache_bytes=512 * MIB,
    )
    first_ceiling = first.pressure_ceiling_bytes

    jittered = policy.observe_memory_pressure(
        PhysicalMemorySnapshot(
            total_physical_bytes=8 * GIB,
            available_physical_bytes=2 * GIB + 8 * MIB,
            process_working_set_bytes=2 * GIB,
        ),
        current_cache_bytes=512 * MIB,
    )

    assert jittered.pressure_ceiling_bytes == first_ceiling


def test_auto_hard_limit_stays_stable_until_explicit_reconfigure() -> None:
    initial = PhysicalMemorySnapshot(
        total_physical_bytes=128 * GIB,
        available_physical_bytes=80 * GIB,
        process_working_set_bytes=2 * GIB,
    )
    policy = ResolvedViewerMemoryPolicy("auto", snapshot=initial)

    assert policy.hard_limit_bytes == 32 * GIB

    pressure_update = policy.observe_memory_pressure(
        PhysicalMemorySnapshot(
            total_physical_bytes=128 * GIB,
            available_physical_bytes=10 * GIB,
            process_working_set_bytes=20 * GIB,
        ),
        current_cache_bytes=16 * GIB,
    )

    assert pressure_update.hard_limit_bytes == 32 * GIB
    assert pressure_update.active_soft_target_bytes < 28 * GIB

    reconfigured = policy.reconfigure(
        "auto",
        snapshot=PhysicalMemorySnapshot(
            total_physical_bytes=8 * GIB,
            available_physical_bytes=3 * GIB,
            process_working_set_bytes=1 * GIB,
        ),
    )

    assert reconfigured.hard_limit_bytes == 1 * GIB


def test_debug_values_expose_resolution_without_side_effects() -> None:
    policy = ResolvedViewerMemoryPolicy("256", active=False)

    values = policy.debug_values()

    assert values["mode"] == "256"
    assert values["hard_limit_bytes"] == 256 * MIB
    assert values["selected_soft_target_bytes"] == 128 * MIB
    assert values["active"] is False
