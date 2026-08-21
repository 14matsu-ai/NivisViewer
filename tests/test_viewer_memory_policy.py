from __future__ import annotations

import pytest

from app.viewer_memory_policy import (
    GIB,
    MIB,
    PhysicalMemorySnapshot,
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
