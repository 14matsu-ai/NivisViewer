"""Pure policies for Browser actions; no filesystem I/O or Qt ownership."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import chain
import re
from typing import Iterator, Mapping

WORKFLOW_DEFAULTS = {
    "browser_thumbnail_background_screens": 3,
    "browser_selection_filename_opacity": 38,
    "browser_selection_border_width": 2,
    "browser_selection_color": "auto",
    "browser_selection_text_color_auto_adjust": True,
    "browser_selection_frame_rounded": False,
}


def bounded_int(value: object, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value) if not isinstance(value, bool) else default
    except (ValueError, TypeError, OverflowError):
        parsed = default
    return min(high, max(low, parsed))


def normalize_workflow_settings(values: Mapping[str, object]) -> dict[str, object]:
    color = str(values.get("browser_selection_color", "auto")).strip().lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", color):
        color = "auto"
    return {
        "browser_thumbnail_background_screens": bounded_int(
            values.get("browser_thumbnail_background_screens", 3), 3, -1, 100),
        "browser_selection_filename_opacity": bounded_int(
            values.get("browser_selection_filename_opacity", 38), 38, 0, 100),
        "browser_selection_border_width": bounded_int(
            values.get("browser_selection_border_width", 2), 2, 1, 12),
        "browser_selection_color": color,
        "browser_selection_text_color_auto_adjust": (
            values.get("browser_selection_text_color_auto_adjust", True)
            if isinstance(values.get("browser_selection_text_color_auto_adjust", True), bool)
            else True
        ),
        "browser_selection_frame_rounded": (
            values.get("browser_selection_frame_rounded", False)
            if isinstance(values.get("browser_selection_frame_rounded", False), bool)
            else False
        ),
    }


def decode_preferred_drop_effect(raw: bytes) -> str:
    """Read the leading little-endian DWORD; tolerate allocator padding."""
    if len(raw) < 4:
        return "invalid"
    mask = int.from_bytes(raw[:4], "little")
    # Only an unambiguous preferred effect may request MOVE. Bitwise
    # combinations are allowed-effect masks, not proof that the user chose
    # to move these files.
    if mask == 2:
        return "move"
    if mask == 1:
        return "copy"
    return f"unknown({mask})"


def paste_is_move(*, internal_matches: bool, internal_cut: bool,
                  preferred_effect: str) -> bool:
    """Trust private intent only while it still identifies this clipboard."""
    if internal_matches:
        return bool(internal_cut)
    return preferred_effect == "move"


@dataclass(frozen=True)
class SelectionAppearance:
    opacity_percent: int = 38
    border_width: int = 2
    color: str = "auto"
    auto_adjust_text_color: bool = True
    rounded_frame: bool = False

    @classmethod
    def from_settings(cls, values: Mapping[str, object]) -> SelectionAppearance:
        values = normalize_workflow_settings(values)
        return cls(int(values["browser_selection_filename_opacity"]),
                   int(values["browser_selection_border_width"]),
                   str(values["browser_selection_color"]),
                   bool(values["browser_selection_text_color_auto_adjust"]),
                   bool(values["browser_selection_frame_rounded"]))

    @property
    def alpha(self) -> int:
        # Retain the old default 96/255 exactly.
        return 96 if self.opacity_percent == 38 else round(self.opacity_percent * 255 / 100)


class ThumbnailWarmupCursor:
    """One byte per row, no images, no eagerly materialized all-book queue.

    Only a terminal completion marks a row done. Rejected/cancelled requests
    remain retryable. A viewport recenter preserves completions in this exact
    model/generation/render revision and changes only the unstarted order.
    """
    def __init__(self, count: int) -> None:
        self.count = max(0, int(count))
        self._done = bytearray(self.count)
        self._retry: deque[int] = deque()
        self._order: Iterator[int] = iter(())
        self.first = 0
        self.last = -1
        self.low = 0
        self.high = -1
        self.exhausted = True

    def recenter(self, first: int, last: int, direction: int, screens: int) -> None:
        if not self.count:
            return
        self.first = max(0, min(self.count - 1, int(first)))
        self.last = max(self.first, min(self.count - 1, int(last)))
        span = self.last - self.first + 1
        if screens < 0:
            self.low, self.high = 0, self.count - 1
        else:
            self.low = max(0, self.first - span * max(0, screens))
            self.high = min(self.count - 1, self.last + span * max(0, screens))
        forward = range(self.last + 1, self.high + 1)
        reverse = range(self.first - 1, self.low - 1, -1)
        self._order = chain(reverse, forward) if direction < 0 else chain(forward, reverse)
        self._retry.clear()
        self.exhausted = False

    def eligible(self, row: int) -> bool:
        return self.low <= row <= self.high and not self.first <= row <= self.last

    def take(self, *, scan_limit: int = 128) -> int | None:
        for _ in range(max(1, scan_limit)):
            if self._retry:
                row = self._retry.popleft()
            else:
                row = next(self._order, None)
                if row is None:
                    self.exhausted = True
                    return None
            if self.eligible(row) and not self._done[row]:
                return row
        return None  # Caller yields to the event loop before continuing.

    def complete(self, row: int) -> None:
        if 0 <= row < self.count:
            self._done[row] = 1

    def invalidate(self, row: int) -> None:
        if not 0 <= row < self.count:
            return
        self._done[row] = 0
        self._retry = deque(candidate for candidate in self._retry if candidate != row)
        if self.eligible(row):
            self._retry.appendleft(row)
        self.exhausted = False

    def retry(self, row: int) -> None:
        if 0 <= row < self.count and not self._done[row] and row not in self._retry:
            self._retry.appendleft(row)
            self.exhausted = False
