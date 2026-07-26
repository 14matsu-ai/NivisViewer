from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum, auto

from PySide6.QtCore import QPoint, Qt


class BrowserPointerState(Enum):
    IDLE = auto()
    PRESSED_ON_ITEM = auto()
    PRESSED_ON_EMPTY = auto()
    FILE_DRAGGING = auto()
    RUBBER_BAND_SELECTING = auto()
    CANCELLED = auto()


@dataclass(frozen=True)
class BrowserPointerPress:
    position: QPoint
    global_position: QPoint
    timestamp_ns: int
    modifiers: Qt.KeyboardModifier
    row: int
    path: str | None
    was_selected: bool
    selection_snapshot: tuple[str, ...]
    current_path: str | None
    anchor_path: str | None


class BrowserPointerController:
    """Owns one mutually-exclusive Browser pointer input sequence."""

    def __init__(self) -> None:
        self.state = BrowserPointerState.IDLE
        self.press: BrowserPointerPress | None = None

    @property
    def press_row(self) -> int:
        return self.press.row if self.press is not None else -1

    @property
    def press_modifiers(self) -> Qt.KeyboardModifier:
        if self.press is None:
            return Qt.KeyboardModifier.NoModifier
        return self.press.modifiers

    @property
    def blank_press(self) -> bool:
        return self.state in {
            BrowserPointerState.PRESSED_ON_EMPTY,
            BrowserPointerState.RUBBER_BAND_SELECTING,
        }

    @property
    def shift_rubber_band(self) -> bool:
        return (
            self.press is not None
            and self.press.path is None
            and bool(self.press.modifiers & Qt.KeyboardModifier.ShiftModifier)
        )

    def begin(
        self,
        *,
        position: QPoint,
        global_position: QPoint,
        modifiers: Qt.KeyboardModifier,
        row: int,
        path: str | None,
        was_selected: bool,
        selection_snapshot: tuple[str, ...],
        current_path: str | None,
        anchor_path: str | None,
    ) -> None:
        self.press = BrowserPointerPress(
            QPoint(position),
            QPoint(global_position),
            time.monotonic_ns(),
            modifiers,
            int(row),
            path,
            bool(was_selected),
            tuple(selection_snapshot),
            current_path,
            anchor_path,
        )
        self.state = (
            BrowserPointerState.PRESSED_ON_ITEM
            if path is not None
            else BrowserPointerState.PRESSED_ON_EMPTY
        )

    def exceeded_drag_distance(self, position: QPoint, distance: int) -> bool:
        return (
            self.press is not None
            and (position - self.press.position).manhattanLength()
            >= max(1, int(distance))
        )

    def begin_file_drag(self) -> bool:
        if self.state is not BrowserPointerState.PRESSED_ON_ITEM:
            return False
        if self.press is None or self.press.modifiers != Qt.KeyboardModifier.NoModifier:
            return False
        self.state = BrowserPointerState.FILE_DRAGGING
        return True

    def begin_rubber_band(self) -> bool:
        if (
            self.state is not BrowserPointerState.PRESSED_ON_EMPTY
            or not self.shift_rubber_band
        ):
            return False
        self.state = BrowserPointerState.RUBBER_BAND_SELECTING
        return True

    def cancel(self) -> None:
        if self.state is not BrowserPointerState.IDLE:
            self.state = BrowserPointerState.CANCELLED

    def reset(self) -> None:
        self.state = BrowserPointerState.IDLE
        self.press = None
