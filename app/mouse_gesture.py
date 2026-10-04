from __future__ import annotations

from math import hypot
from typing import TypeAlias

Point: TypeAlias = tuple[float, float]


class MouseGestureRecognizer:
    """Recognize a compact U/D/L/R sequence without depending on Qt."""

    def __init__(
        self,
        min_distance: int = 36,
        *,
        max_directions: int = 8,
        axis_dominance_ratio: float | None = None,
    ) -> None:
        self.min_distance = max(1, int(min_distance))
        self.max_directions = max(1, int(max_directions))
        self.axis_dominance_ratio = (
            None
            if axis_dominance_ratio is None
            else max(1.0, float(axis_dominance_ratio))
        )
        self._active = False
        self._anchor: Point | None = None
        self._directions: list[str] = []

    @property
    def active(self) -> bool:
        return self._active

    @property
    def pattern(self) -> str:
        return "".join(self._directions)

    def begin(self, point: Point) -> None:
        self._active = True
        self._anchor = self._coerce_point(point)
        self._directions.clear()

    def update(self, point: Point) -> str:
        if not self._active or self._anchor is None:
            return self.pattern

        current = self._coerce_point(point)
        dx = current[0] - self._anchor[0]
        dy = current[1] - self._anchor[1]
        if hypot(dx, dy) < self.min_distance:
            return self.pattern

        horizontal = abs(dx)
        vertical = abs(dy)
        if (
            self.axis_dominance_ratio is not None
            and min(horizontal, vertical) > 0
            and max(horizontal, vertical)
            < min(horizontal, vertical) * self.axis_dominance_ratio
        ):
            return self.pattern

        if horizontal >= vertical:
            direction = "R" if dx >= 0 else "L"
        else:
            direction = "D" if dy >= 0 else "U"
        if (not self._directions or self._directions[-1] != direction) and (
            len(self._directions) < self.max_directions
        ):
            self._directions.append(direction)
        self._anchor = current
        return self.pattern

    def finish(self, point: Point | None = None) -> str:
        if not self._active:
            return ""
        if point is not None:
            self.update(point)
        pattern = self.pattern
        self._reset()
        return pattern

    def cancel(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self._active = False
        self._anchor = None
        self._directions.clear()

    @staticmethod
    def _coerce_point(point: Point) -> Point:
        return float(point[0]), float(point[1])
