from __future__ import annotations

from math import trunc


BROWSER_WHEEL_SCROLL_MODES = frozenset(
    {"system", "small", "medium", "large", "custom"}
)
BROWSER_WHEEL_SCROLL_PRESET_ROWS: dict[str, int] = {
    "small": 1,
    "medium": 2,
    "large": 3,
}
BROWSER_WHEEL_SCROLL_CUSTOM_MIN_ROWS = 1
BROWSER_WHEEL_SCROLL_CUSTOM_MAX_ROWS = 12


def normalize_browser_wheel_scroll_mode(value: object) -> str:
    mode = str(value).strip().casefold()
    return mode if mode in BROWSER_WHEEL_SCROLL_MODES else "system"


def normalize_browser_wheel_custom_rows(value: object) -> int:
    try:
        rows = int(value)
    except (TypeError, ValueError, OverflowError):
        rows = 3
    return max(
        BROWSER_WHEEL_SCROLL_CUSTOM_MIN_ROWS,
        min(BROWSER_WHEEL_SCROLL_CUSTOM_MAX_ROWS, rows),
    )


class BrowserWheelScrollAccumulator:
    """Convert high-resolution angle deltas to adaptive grid-row movement."""

    def __init__(self, mode: object = "system", custom_rows: object = 3) -> None:
        self._mode = "system"
        self._custom_rows = 3
        self._pixel_remainder = 0.0
        self.configure(mode, custom_rows)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def custom_rows(self) -> int:
        return self._custom_rows

    @property
    def rows_per_notch(self) -> int | None:
        if self._mode == "system":
            return None
        if self._mode == "custom":
            return self._custom_rows
        return BROWSER_WHEEL_SCROLL_PRESET_ROWS[self._mode]

    @property
    def pixel_remainder(self) -> float:
        return self._pixel_remainder

    def configure(self, mode: object, custom_rows: object) -> None:
        normalized_mode = normalize_browser_wheel_scroll_mode(mode)
        normalized_rows = normalize_browser_wheel_custom_rows(custom_rows)
        if (
            normalized_mode != self._mode
            or normalized_rows != self._custom_rows
        ):
            self._pixel_remainder = 0.0
        self._mode = normalized_mode
        self._custom_rows = normalized_rows

    def consume_angle_delta(self, angle_delta_y: int, row_height: int) -> int:
        rows = self.rows_per_notch
        if rows is None or not angle_delta_y:
            return 0
        logical_row_height = max(1, int(row_height))
        movement = (
            self._pixel_remainder
            - float(angle_delta_y) * rows * logical_row_height / 120.0
        )
        whole_pixels = trunc(movement)
        self._pixel_remainder = movement - whole_pixels
        return whole_pixels
