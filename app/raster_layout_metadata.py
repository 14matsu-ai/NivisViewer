"""Bounded header preflight for the existing raster worker lane.

This module owns no threads, PageModel, cache, or work order. Workers return
book-scoped observations; PageModel remains the geometry authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Callable, Iterable


LAYOUT_METADATA_BATCH_PAGES = 32
LayoutObservation = tuple[int, str, tuple[int, int] | None]


@dataclass(frozen=True)
class RasterLayoutMetadata:
    source_epoch: int
    source_identity: int
    observations: tuple[LayoutObservation, ...]


def select_layout_metadata_pages(
    unit: Any,
    plan: Any,
    attempted_ids: set[str],
    *,
    maximum_pages: int = LAYOUT_METADATA_BATCH_PAGES,
    include_nearby: bool = True,
    directional_bias: float | None = None,
) -> tuple[Any, ...]:
    """Pick bounded unknown headers around the candidate.

    ``directional_bias`` changes only metadata reconnaissance order.  It never
    reorders pixel decode/warm-up priority.  The runtime uses a forward-heavy
    corridor so an approaching expensive/wide page is known soon enough to
    start early while nearer display units remain first in the real work order.
    """

    limit = max(0, int(maximum_pages))
    if not limit:
        return ()

    def unknown(page: Any) -> bool:
        return (
            page.known_size is None
            and page.image_id not in attempted_ids
        )

    selected: list[Any] = []
    seen: set[str] = set()

    def add(candidate: Any) -> None:
        for page in candidate.pages:
            if len(selected) >= limit:
                return
            if unknown(page) and page.image_id not in seen:
                seen.add(page.image_id)
                selected.append(page)

    add(unit)
    if not include_nearby or len(selected) >= limit:
        return tuple(selected)

    topology = plan.topology
    anchor = topology.ordinal_for_identity(unit.identity)
    if anchor is None:
        anchor = topology.ordinal_for_page(unit.pages[0].page_index)
    if anchor is None:
        return tuple(selected)

    step = plan.direction or 1

    # Keep the historical alternating behavior for all existing callers/tests.
    if directional_bias is None:
        for distance in range(1, limit + 1):
            for ordinal in (
                anchor + step * distance,
                anchor - step * distance,
            ):
                if 0 <= ordinal < len(topology):
                    add(topology.unit_at(ordinal))
                    if len(selected) >= limit:
                        return tuple(selected)
        return tuple(selected)

    bias = max(0.5, min(1.0, float(directional_bias)))
    remaining = max(0, limit - len(selected))
    preferred_quota = ceil(remaining * bias)
    opposite_quota = remaining - preferred_quota

    # Header reconnaissance must remain local even when a huge book already
    # has known headers. At most four display units are inspected per requested
    # page on either side; later calls can move the corridor with navigation.
    scan_span = max(
        LAYOUT_METADATA_BATCH_PAGES,
        min(limit, LAYOUT_METADATA_BATCH_PAGES) * 4,
    )
    next_distance = {step: 1, -step: 1}

    def fill(direction: int, quota: int) -> None:
        before = len(selected)
        while (
            len(selected) - before < quota
            and len(selected) < limit
            and next_distance[direction] <= scan_span
        ):
            distance = next_distance[direction]
            next_distance[direction] = distance + 1
            ordinal = anchor + direction * distance
            if not 0 <= ordinal < len(topology):
                return
            add(topology.unit_at(ordinal))

    # Reconnaissance looks farther ahead first, then keeps a reverse safety
    # corridor.  Any unused quota at an edge is donated to the other side.
    fill(step, preferred_quota)
    fill(-step, opposite_quota)
    if len(selected) < limit:
        fill(step, limit - len(selected))
    if len(selected) < limit:
        fill(-step, limit - len(selected))
    return tuple(selected)


def probe_layout_metadata(
    pages: Iterable[Any],
    probe_size: Callable[[str], tuple[int, int] | None],
    cancelled: Any,
) -> tuple[LayoutObservation, ...]:
    """Read headers only, retaining successful observations across cancellation."""

    observations: list[LayoutObservation] = []
    for page in pages:
        if cancelled.is_set():
            break
        size = None
        try:
            candidate = probe_size(page.image_id)
            if candidate is not None:
                width, height = int(candidate[0]), int(candidate[1])
                if width > 0 and height > 0:
                    size = (width, height)
        except Exception:
            # Source-specific failures must not strand the shared worker lane.
            pass
        if cancelled.is_set() and size is None:
            break
        observations.append((page.page_index, page.image_id, size))
    return tuple(observations)
