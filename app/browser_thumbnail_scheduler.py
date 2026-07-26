from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from math import ceil


class ThumbnailPriority(IntEnum):
    PREFETCH = 0
    SELECTED = 1
    VISIBLE = 2


@dataclass(frozen=True)
class ThumbnailRequestPlan:
    visible_rows: tuple[int, ...]
    selected_rows: tuple[int, ...]
    prefetch_rows: tuple[int, ...]

    @property
    def requested_rows(self) -> tuple[int, ...]:
        return self.visible_rows + self.selected_rows + self.prefetch_rows


def calculate_grid_visible_range(
    *,
    row_count: int,
    viewport_width: int,
    viewport_height: int,
    grid_width: int,
    grid_height: int,
    vertical_offset: int,
) -> tuple[int, int] | None:
    count = max(0, int(row_count))
    if count == 0:
        return None
    cell_width = max(1, int(grid_width))
    cell_height = max(1, int(grid_height))
    columns = max(1, int(viewport_width) // cell_width)
    first_line = max(0, int(vertical_offset)) // cell_height
    visible_lines = max(1, ceil(max(1, int(viewport_height)) / cell_height) + 1)
    first = min(count - 1, first_line * columns)
    last = min(count - 1, first + visible_lines * columns - 1)
    return first, last


def build_thumbnail_request_plan(
    *,
    row_count: int,
    first_visible: int,
    last_visible: int,
    selected_rows: tuple[int, ...] = (),
    prefetch_screens: int = 1,
    fast_scrolling: bool = False,
) -> ThumbnailRequestPlan:
    count = max(0, int(row_count))
    if count == 0:
        return ThumbnailRequestPlan((), (), ())
    first = max(0, min(int(first_visible), count - 1))
    last = max(first, min(int(last_visible), count - 1))
    visible = tuple(range(first, last + 1))
    visible_set = set(visible)
    selected = tuple(
        row
        for row in dict.fromkeys(int(value) for value in selected_rows)
        if 0 <= row < count and row not in visible_set
    )
    if fast_scrolling:
        return ThumbnailRequestPlan(visible, selected, ())

    visible_count = max(1, len(visible))
    margin = visible_count * max(0, min(3, int(prefetch_screens)))
    prefetch_first = max(0, first - margin)
    prefetch_last = min(count - 1, last + margin)
    excluded = visible_set | set(selected)
    prefetch = tuple(
        row
        for row in range(prefetch_first, prefetch_last + 1)
        if row not in excluded
    )
    return ThumbnailRequestPlan(visible, selected, prefetch)
