"""Bounded header preflight for the existing raster worker lane.

This module owns no threads, PageModel, cache, or work order. Workers return
book-scoped observations; PageModel remains the geometry authority.
"""

from __future__ import annotations

from dataclasses import dataclass
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
) -> tuple[Any, ...]:
    """Pick bounded unknown headers around the candidate, never from book start."""

    limit = max(0, int(maximum_pages))
    if not limit:
        return ()

    def unknown(page: Any) -> bool:
        return page.known_size is None and page.image_id not in attempted_ids

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
    if not include_nearby:
        return tuple(selected)
    topology = plan.topology
    anchor = topology.ordinal_for_identity(unit.identity)
    if anchor is None:
        anchor = topology.ordinal_for_page(unit.pages[0].page_index)
    if anchor is None or len(selected) >= limit:
        return tuple(selected)
    step = plan.direction or 1
    for distance in range(1, limit + 1):
        for ordinal in (anchor + step * distance, anchor - step * distance):
            if 0 <= ordinal < len(topology):
                add(topology.unit_at(ordinal))
                if len(selected) >= limit:
                    return tuple(selected)
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
