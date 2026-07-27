from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Iterable

from PySide6.QtCore import QObject, Signal


class ApplicationShutdownState(StrEnum):
    RUNNING = "running"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class ApplicationShutdownSnapshot:
    running_file_operations: int = 0
    queued_file_operations: int = 0
    pending_pdf_jobs: int = 0
    shell_preview_pending: int = 0
    path_probe_pending: int = 0
    thumbnail_pending: int = 0


class ApplicationShutdownCoordinator(QObject):
    """Idempotent ordered shutdown runner without a nested event loop."""

    phase_changed = Signal(str)
    shutdown_finished = Signal()
    shutdown_failed = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        steps: Iterable[tuple[str, Callable[[], object]]] = (),
    ) -> None:
        super().__init__(parent)
        self._steps = list(steps)
        self._state = ApplicationShutdownState.RUNNING
        self._snapshot = ApplicationShutdownSnapshot()

    @property
    def state(self) -> ApplicationShutdownState:
        return self._state

    @property
    def snapshot(self) -> ApplicationShutdownSnapshot:
        return self._snapshot

    def configure(
        self,
        steps: Iterable[tuple[str, Callable[[], object]]],
        *,
        snapshot: ApplicationShutdownSnapshot | None = None,
    ) -> None:
        if self._state is not ApplicationShutdownState.RUNNING:
            return
        self._steps = list(steps)
        if snapshot is not None:
            self._snapshot = snapshot

    def begin_shutdown(self) -> bool:
        if self._state is ApplicationShutdownState.STOPPED:
            return False
        if self._state is ApplicationShutdownState.SHUTTING_DOWN:
            return False
        self._state = ApplicationShutdownState.SHUTTING_DOWN
        try:
            for name, callback in self._steps:
                self.phase_changed.emit(name)
                callback()
        except Exception as exc:
            self._state = ApplicationShutdownState.TIMED_OUT
            self.shutdown_failed.emit(str(exc))
            return False
        self._state = ApplicationShutdownState.STOPPED
        self.shutdown_finished.emit()
        return True
