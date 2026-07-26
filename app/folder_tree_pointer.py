from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from PySide6.QtCore import QPoint


class FolderTreePointerState(Enum):
    IDLE = auto()
    PRESSED = auto()
    DRAG_HOVER = auto()
    PROGRAMMATIC_SYNC = auto()


@dataclass(frozen=True)
class FolderTreePress:
    path: str
    position: QPoint
    disclosure: bool


class FolderTreePointerController:
    """Confirms navigation only after a click completes on one path."""

    def __init__(self) -> None:
        self.state = FolderTreePointerState.IDLE
        self.press: FolderTreePress | None = None

    def begin(self, path: str, position: QPoint, *, disclosure: bool) -> bool:
        if self.state is FolderTreePointerState.PROGRAMMATIC_SYNC or not path:
            return False
        self.press = FolderTreePress(path, QPoint(position), bool(disclosure))
        self.state = FolderTreePointerState.PRESSED
        return True

    def moved(self, position: QPoint, threshold: int) -> bool:
        if (
            self.state is FolderTreePointerState.PRESSED
            and self.press is not None
            and (position - self.press.position).manhattanLength()
            >= max(1, int(threshold))
        ):
            self.state = FolderTreePointerState.DRAG_HOVER
            return True
        return self.state is FolderTreePointerState.DRAG_HOVER

    def confirm_release(self, path: str, position: QPoint, threshold: int) -> str | None:
        press = self.press
        confirmed = (
            self.state is FolderTreePointerState.PRESSED
            and press is not None
            and not press.disclosure
            and press.path == path
            and (position - press.position).manhattanLength()
            < max(1, int(threshold))
        )
        self.reset()
        return path if confirmed else None

    def begin_programmatic_sync(self) -> None:
        self.press = None
        self.state = FolderTreePointerState.PROGRAMMATIC_SYNC

    def end_programmatic_sync(self) -> None:
        if self.state is FolderTreePointerState.PROGRAMMATIC_SYNC:
            self.reset()

    def reset(self) -> None:
        self.press = None
        self.state = FolderTreePointerState.IDLE
