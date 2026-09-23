"""Browser thumbnail RAM budgets and hot-window ordering. No I/O or Qt.

These are policy limits, not preallocation and not a process working-set cap.
One application broker must apply all Browser grants together; Viewer memory
is already reflected in the physical-memory sample and is not added back.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
from typing import Iterable, Mapping

MIB = 1024 * 1024
GIB = 1024 * MIB
MEMORY_MODES = ("auto", "128", "256", "512", "1024", "2048")
AUTO_BUCKETS = tuple(value * MIB for value in (128, 256, 512, 1024, 2048))
AUTO_MAX_BYTES = 2 * GIB
GROUP_MAX_BYTES = 8 * GIB
UNKNOWN_GROUP_BYTES = 256 * MIB
SAMPLE_INTERVAL_SECONDS = 5.0
RECOVERY_STABLE_SECONDS = 10.0
RECOVERY_STEP_BYTES = 256 * MIB
# A safety bound for metadata, NOT the historical 128-image performance cap.
MANAGED_ENTRY_GUARD = 65536
UNLIMITED_HOT_SCREENS = 3
MAX_SELECTED_HOT_ROWS = 128
MAX_SELECTED_SCAN_ROWS = 512


def normalize_memory_mode(value: object) -> str:
    value = str(value).strip().lower()
    return value if value in MEMORY_MODES else "auto"


def desired_limit(mode: object, near_bytes: int, *, visible: bool = True) -> int:
    mode = normalize_memory_mode(mode)
    if mode != "auto":
        desired = int(mode) * MIB
    else:
        # 25% working-set margin; rounded up, capped at 2 GiB per Browser.
        need = (max(0, int(near_bytes)) * 5 + 3) // 4
        desired = next((bucket for bucket in AUTO_BUCKETS if bucket >= need), AUTO_MAX_BYTES)
    return desired if visible else min(desired, 32 * MIB)


def _snapshot_values(snapshot: object | None) -> tuple[int, int] | None:
    try:
        total = snapshot.total_physical_bytes
        available = snapshot.available_physical_bytes
    except (AttributeError, TypeError):
        return None
    if type(total) is not int or type(available) is not int:
        return None
    return (total, available) if total > 0 and 0 <= available <= total else None


def physical_group_capacity(snapshot: object | None, retained_bytes: int) -> int:
    """A conservative aggregate allowance, including existing managed pixels.

    Positive spare memory contributes only half to growth. Negative spare
    memory subtracts the complete shortfall. Add back only managed Browser
    provider caches, once per application sample. Model/native/Viewer images
    are *not* assumed reclaimable. The broker freezes this allowance until
    the next OS sample (never reuse stale availability with new usage).
    """
    values = _snapshot_values(snapshot)
    if values is None:
        return UNKNOWN_GROUP_BYTES
    total, available = values
    # Keep at least 2 GiB, scaling to 8 GiB on large-RAM machines. Avoid
    # declaring a 128-GiB machine "out of headroom" with 20 GiB still free.
    reserve = max(2 * GIB, min(8 * GIB, total // 8))
    # Extra safety beyond the OS reserve for decode/native allocations.
    spare = available - reserve - 256 * MIB
    contribution = spare // 2 if spare >= 0 else spare
    capacity = max(0, int(retained_bytes) + contribution)
    return min(GROUP_MAX_BYTES, total // 8, capacity)


def fair_grants(desired: Mapping[int, int], capacity: int) -> dict[int, int]:
    """Deterministic max-min allocation. Sum of grants never exceeds capacity."""
    remaining = max(0, int(capacity))
    pending = sorted((max(0, int(limit)), key) for key, limit in desired.items())
    result: dict[int, int] = {}
    for index, (limit, key) in enumerate(pending):
        grant = min(limit, remaining // (len(pending) - index))
        result[key] = grant
        remaining -= grant
    return result


@dataclass(frozen=True)
class MemoryDemand:
    mode: str
    near_bytes: int
    visible: bool = True

    @property
    def desired(self) -> int:
        return desired_limit(self.mode, self.near_bytes, visible=self.visible)


@dataclass(frozen=True)
class MemoryGrant:
    limit_bytes: int
    desired_bytes: int
    group_capacity_bytes: int
    reason: str


class BrowserMemoryGovernor:
    """One aggregate budget decision, fast shrink and time-based slow growth."""
    def __init__(self) -> None:
        self.capacity = UNKNOWN_GROUP_BYTES
        self._grants: dict[int, int] = {}
        self._growth_since: dict[int, float] = {}
        self._last_growth: dict[int, float] = {}
        self._sample_valid = False
        self._pressure_latched: set[int] = set()
        self._last_desired: dict[int, int] = {}

    def sample(self, snapshot: object | None, retained_bytes: int) -> None:
        self.capacity = physical_group_capacity(snapshot, retained_bytes)
        self._sample_valid = _snapshot_values(snapshot) is not None

    def resolve(self, demands: Mapping[int, MemoryDemand], now: float) -> dict[int, MemoryGrant]:
        desired = {key: demand.desired for key, demand in demands.items()}
        targets = fair_grants(desired, self.capacity)
        self._pressure_latched.intersection_update(demands)
        for mapping in (self._grants, self._growth_since, self._last_growth, self._last_desired):
            for key in tuple(mapping):
                if key not in demands:
                    del mapping[key]
        resolved: dict[int, MemoryGrant] = {}
        for key, target in targets.items():
            previous = self._grants.get(key)
            demand_grew = desired[key] > self._last_desired.get(key, 0)
            if target < desired[key]:
                self._pressure_latched.add(key)
            if previous is None or target < previous:
                grant = target
                self._growth_since.pop(key, None)
                self._last_growth.pop(key, None)
            elif (self._sample_valid and demand_grew and key not in self._pressure_latched):
                # A larger viewport/manual choice can use already granted OS
                # headroom immediately. Recovery after pressure is different.
                grant = target
                self._growth_since.pop(key, None)
            elif target == previous or not self._sample_valid:
                grant = min(previous, target)
                self._growth_since.pop(key, None)
            else:
                since = self._growth_since.setdefault(key, float(now))
                last = self._last_growth.get(key, since)
                stable = float(now) - since >= RECOVERY_STABLE_SECONDS
                interval = float(now) - last >= SAMPLE_INTERVAL_SECONDS
                grant = min(target, previous + RECOVERY_STEP_BYTES) if stable and interval else previous
                if grant > previous:
                    self._last_growth[key] = float(now)
            self._grants[key] = grant
            self._last_desired[key] = desired[key]
            if grant == desired[key] and self._sample_valid:
                self._pressure_latched.discard(key)
            reason = (
                "fallback" if not self._sample_valid else
                "recovering" if grant < target else
                "headroom_limited" if target < desired[key] else "ready"
            )
            resolved[key] = MemoryGrant(grant, desired[key], self.capacity, reason)
        return resolved


def hot_row_order(*, count: int, first: int, last: int, direction: int,
                  screens: int, selected: Iterable[int] = (),
                  selected_limit: int = MAX_SELECTED_HOT_ROWS) -> tuple[int, ...]:
    """Bounded near-window identities, independent of far generation progress.

    Within each distance band, prefer the scroll direction, then the reverse.
    Unlimited generation still has a finite three-screen RAM working set.
    """
    count = max(0, int(count))
    if count == 0:
        return ()
    first = max(0, min(count - 1, int(first)))
    last = max(first, min(count - 1, int(last)))
    screens = UNLIMITED_HOT_SCREENS if screens < 0 else min(100, max(0, int(screens)))
    span = last - first + 1
    result: list[int] = []
    seen: set[int] = set()

    def append(row: int) -> None:
        if 0 <= row < count and row not in seen and len(result) < MANAGED_ENTRY_GUARD:
            seen.add(row)
            result.append(row)

    def append_rows(rows) -> None:
        for row in rows:
            append(row)
            if len(result) >= MANAGED_ENTRY_GUARD:
                return

    for row in range(first, last + 1):
        append(row)
        if len(result) >= MANAGED_ENTRY_GUARD:
            return tuple(result)
    forward = range(last + 1, min(count, last + 1 + span))
    reverse = range(first - 1, max(-1, first - 1 - span), -1)
    # Always reserve the immediate next viewport, then a small reverse safety
    # band, before spending any of the bounded selection allowance.
    if direction < 0:
        next_rows, safety_rows = reverse, forward
    else:
        next_rows, safety_rows = forward, reverse
    if screens:
        append_rows(next_rows)
        if len(result) >= MANAGED_ENTRY_GUARD:
            return tuple(result)
        safety_count = min(span, max(1, (span + 3) // 4))
        append_rows(islice(safety_rows, safety_count))
        if len(result) >= MANAGED_ENTRY_GUARD:
            return tuple(result)

    selected_limit = min(MAX_SELECTED_HOT_ROWS, max(0, int(selected_limit)))
    if selected_limit:
        scan_limit = min(MAX_SELECTED_SCAN_ROWS, selected_limit * 4)
        selected_added = 0
        for row in islice(selected, scan_limit):
            previous_count = len(result)
            append(int(row))
            selected_added += len(result) > previous_count
            if len(result) >= MANAGED_ENTRY_GUARD:
                return tuple(result)
            if selected_added >= selected_limit:
                break
    for band in range(screens):
        forward = range(last + 1 + band * span, min(count, last + 1 + (band + 1) * span))
        reverse = range(first - 1 - band * span, max(-1, first - 1 - (band + 1) * span), -1)
        for group in ((reverse, forward) if direction < 0 else (forward, reverse)):
            for row in group:
                append(row)
                if len(result) >= MANAGED_ENTRY_GUARD:
                    return tuple(result)
    return tuple(result)


def frame_byte_estimate(spec: object) -> int:
    """Demand sizing hint only. Never a terminal image/admission error."""
    width = max(1, int(spec.frame_width))
    height = max(1, int(spec.frame_height))
    return width * height * 4


def cache_victims(costs: Mapping[object, int], ranks: Mapping[object, int],
                  *, byte_limit: int, entry_limit: int) -> tuple[object, ...]:
    """Return lower-value/older victims, charging actual QImage bytes.

    ``costs`` preserves provider LRU order (oldest first). Equal ranks evict
    oldest first. A hard budget still applies if even the visible set is too
    large; the UI's current painted images are not silently counted as freed.
    """
    total = sum(max(0, int(value)) for value in costs.values())
    count = len(costs)
    byte_limit = max(0, int(byte_limit))
    entry_limit = max(1, int(entry_limit))
    if total <= byte_limit and count <= entry_limit:
        return ()
    outside = max(ranks.values(), default=-1) + 1
    victims = []
    for key in sorted(costs, key=lambda key: ranks.get(key, outside), reverse=True):
        if total <= byte_limit and count <= entry_limit:
            break
        victims.append(key)
        total -= max(0, int(costs[key]))
        count -= 1
    return tuple(victims)
