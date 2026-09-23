from __future__ import annotations

from math import isfinite, trunc


BROWSER_WHEEL_SCROLL_MODES = frozenset(
    {"system", "small", "medium", "large", "custom", "pixels", "viewport"}
)
BROWSER_WHEEL_SCROLL_PRESET_ROWS: dict[str, int] = {
    "small": 1,
    "medium": 2,
    "large": 3,
}
BROWSER_WHEEL_SCROLL_CUSTOM_MIN_ROWS = 1
BROWSER_WHEEL_SCROLL_CUSTOM_MAX_ROWS = 12
BROWSER_WHEEL_SCROLL_DEFAULT_PIXELS = 96.0
BROWSER_WHEEL_SCROLL_DEFAULT_VIEWPORT_PERCENT = 50.0
BROWSER_WHEEL_SCROLL_MAX_PIXELS = 4000.0
BROWSER_WHEEL_SCROLL_MAX_VIEWPORT_PERCENT = 200.0


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


def normalize_browser_wheel_amount(
    value: object,
    *,
    default: float,
    minimum: float = 1.0,
    maximum: float = BROWSER_WHEEL_SCROLL_MAX_PIXELS,
) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        amount = float(default)
    if not isfinite(amount):
        amount = float(default)
    return max(float(minimum), min(float(maximum), amount))


class BrowserWheelScrollAccumulator:
    """Convert custom vertical angle-wheel deltas to logical-pixel movement."""

    def __init__(
        self,
        mode: object = "system",
        custom_rows: object = 3,
        fixed_pixels: object = BROWSER_WHEEL_SCROLL_DEFAULT_PIXELS,
        viewport_percent: object = BROWSER_WHEEL_SCROLL_DEFAULT_VIEWPORT_PERCENT,
    ) -> None:
        self._mode = "system"
        self._custom_rows = 3
        self._fixed_pixels = BROWSER_WHEEL_SCROLL_DEFAULT_PIXELS
        self._viewport_percent = BROWSER_WHEEL_SCROLL_DEFAULT_VIEWPORT_PERCENT
        self._pixel_remainder = 0.0
        self._last_geometry: tuple[str, int] | None = None
        self.configure(mode, custom_rows, fixed_pixels, viewport_percent)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def custom_rows(self) -> int:
        return self._custom_rows

    @property
    def fixed_pixels(self) -> float:
        return self._fixed_pixels

    @property
    def viewport_percent(self) -> float:
        return self._viewport_percent

    @property
    def rows_per_notch(self) -> int | None:
        if self._mode == "system":
            return None
        if self._mode == "custom":
            return self._custom_rows
        return BROWSER_WHEEL_SCROLL_PRESET_ROWS.get(self._mode)

    @property
    def pixel_remainder(self) -> float:
        return self._pixel_remainder

    def configure(
        self,
        mode: object,
        custom_rows: object,
        fixed_pixels: object = BROWSER_WHEEL_SCROLL_DEFAULT_PIXELS,
        viewport_percent: object = BROWSER_WHEEL_SCROLL_DEFAULT_VIEWPORT_PERCENT,
    ) -> None:
        normalized_mode = normalize_browser_wheel_scroll_mode(mode)
        normalized_rows = normalize_browser_wheel_custom_rows(custom_rows)
        normalized_pixels = normalize_browser_wheel_amount(
            fixed_pixels,
            default=BROWSER_WHEEL_SCROLL_DEFAULT_PIXELS,
        )
        normalized_percent = normalize_browser_wheel_amount(
            viewport_percent,
            default=BROWSER_WHEEL_SCROLL_DEFAULT_VIEWPORT_PERCENT,
            maximum=BROWSER_WHEEL_SCROLL_MAX_VIEWPORT_PERCENT,
        )
        if (
            normalized_mode != self._mode
            or normalized_rows != self._custom_rows
            or normalized_pixels != self._fixed_pixels
            or normalized_percent != self._viewport_percent
        ):
            self.reset()
        self._mode = normalized_mode
        self._custom_rows = normalized_rows
        self._fixed_pixels = normalized_pixels
        self._viewport_percent = normalized_percent

    def reset(self) -> None:
        """Drop fractional movement when the input or navigation context changes."""
        self._pixel_remainder = 0.0
        self._last_geometry = None

    def consume_angle_delta(
        self,
        angle_delta_y: int,
        row_height: int,
        viewport_height: int = 0,
    ) -> int:
        if self._mode == "system" or not angle_delta_y:
            return 0
        logical_row_height = max(1, int(row_height))
        logical_viewport_height = max(1, int(viewport_height or row_height))
        if self._mode == "pixels":
            distance = self._fixed_pixels
            geometry = None
        elif self._mode == "viewport":
            distance = logical_viewport_height * self._viewport_percent / 100.0
            geometry = ("viewport", logical_viewport_height)
        else:
            distance = float(self.rows_per_notch or 0) * logical_row_height
            geometry = ("row", logical_row_height)
        if geometry != self._last_geometry:
            self._pixel_remainder = 0.0
            self._last_geometry = geometry
        movement = self._pixel_remainder - float(angle_delta_y) * distance / 120.0
        whole_pixels = trunc(movement)
        self._pixel_remainder = movement - whole_pixels
        return whole_pixels
