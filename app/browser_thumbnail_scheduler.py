from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from math import ceil


class ThumbnailPriority(IntEnum):
    PREFETCH = 0
    READ_AHEAD = 1
    SELECTED = 2
    VISIBLE = 3


@dataclass(frozen=True)
class ThumbnailRequestPlan:
    visible_rows: tuple[int, ...]
    selected_rows: tuple[int, ...]
    directional_rows: tuple[int, ...]
    safety_rows: tuple[int, ...]

    @property
    def prefetch_rows(self) -> tuple[int, ...]:
        """Compatibility view of all bounded speculative rows."""

        return self.directional_rows + self.safety_rows

    @property
    def requested_rows(self) -> tuple[int, ...]:
        return (
            self.visible_rows
            + self.selected_rows
            + self.directional_rows
            + self.safety_rows
        )


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
    scroll_direction: int = 1,
    opposite_safety_fraction: float = 0.25,
    fast_scrolling: bool = False,
) -> ThumbnailRequestPlan:
    count = max(0, int(row_count))
    if count == 0:
        return ThumbnailRequestPlan((), (), (), ())
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
        return ThumbnailRequestPlan(visible, selected, (), ())

    visible_count = max(1, len(visible))
    directional_count = visible_count * max(0, min(3, int(prefetch_screens)))
    safety_count = ceil(
        visible_count * max(0.0, min(1.0, float(opposite_safety_fraction)))
    )
    excluded = visible_set | set(selected)
    direction = -1 if int(scroll_direction) < 0 else 1
    if direction > 0:
        directional_candidates = range(
            last + 1,
            min(count, last + 1 + directional_count),
        )
        safety_candidates = range(
            first - 1,
            max(-1, first - 1 - safety_count),
            -1,
        )
    else:
        directional_candidates = range(
            first - 1,
            max(-1, first - 1 - directional_count),
            -1,
        )
        safety_candidates = range(
            last + 1,
            min(count, last + 1 + safety_count),
        )
    directional = tuple(
        row for row in directional_candidates if row not in excluded
    )
    directional_set = set(directional)
    safety = tuple(
        row
        for row in safety_candidates
        if row not in excluded and row not in directional_set
    )
    return ThumbnailRequestPlan(visible, selected, directional, safety)
