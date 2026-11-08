from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
from time import monotonic
from typing import Callable, Iterable

from PySide6.QtCore import QObject, Signal


_LOG = logging.getLogger('nivisviewer.application_shutdown')


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
        self._next_step_index = 0
        self._failed_step_index: int | None = None
        self._failed_step_name: str | None = None
        self._last_logged_failure: tuple[int, str] | None = None
        self._logged_step = -1
        self._phase_started = 0.0
        self._next_wait_log = 0.0
        self._diagnostics: Callable[[], object] | None = None

    @property
    def state(self) -> ApplicationShutdownState:
        return self._state

    @property
    def snapshot(self) -> ApplicationShutdownSnapshot:
        return self._snapshot

    @property
    def failed_step_index(self) -> int | None:
        return self._failed_step_index

    @property
    def failed_step_name(self) -> str | None:
        return self._failed_step_name

    def configure(
        self,
        steps: Iterable[tuple[str, Callable[[], object]]],
        *,
        snapshot: ApplicationShutdownSnapshot | None = None,
        diagnostics: Callable[[], object] | None = None,
    ) -> None:
        if self._state is not ApplicationShutdownState.RUNNING:
            return
        self._steps = list(steps)
        self._next_step_index = 0
        self._failed_step_index = None
        self._failed_step_name = None
        self._last_logged_failure = None
        self._logged_step = -1
        self._diagnostics = diagnostics
        if snapshot is not None:
            self._snapshot = snapshot

    def begin_shutdown(self) -> bool:
        if self._state is ApplicationShutdownState.STOPPED:
            return True
        if self._state is ApplicationShutdownState.SHUTTING_DOWN:
            return False
        self._state = ApplicationShutdownState.SHUTTING_DOWN
        while self._next_step_index < len(self._steps):
            step_index = self._next_step_index
            name, callback = self._steps[step_index]
            now = monotonic()
            if self._logged_step != step_index:
                self._logged_step = step_index
                self._phase_started = self._next_wait_log = now
                _LOG.info('Application shutdown phase started step=%s owner=%x', name, id(self))
            self.phase_changed.emit(name)
            try:
                result = callback()
            except Exception as exc:
                self._record_failure(step_index, name, str(exc), exc_info=True)
                return False
            if result is False:
                self._record_failure(
                    step_index,
                    name,
                    f"Shutdown step '{name}' did not complete.",
                )
                return False
            _LOG.info('Application shutdown phase complete step=%s elapsed_ms=%.1f',
                      name, (monotonic() - self._phase_started) * 1000)
            self._next_step_index += 1
            self._failed_step_index = None
            self._failed_step_name = None
        self._state = ApplicationShutdownState.STOPPED
        _LOG.info('Application shutdown all phases complete')
        self.shutdown_finished.emit()
        return True

    def _record_failure(
        self,
        step_index: int,
        step_name: str,
        message: str,
        *, exc_info: bool = False,
    ) -> None:
        self._state = ApplicationShutdownState.TIMED_OUT
        self._failed_step_index = step_index
        self._failed_step_name = step_name
        failure = (step_index, message)
        if failure != self._last_logged_failure:
            _LOG.error(
                "Application shutdown step failed step=%s index=%d error=%s",
                step_name,
                step_index,
                message,
                exc_info=exc_info,
            )
            self._last_logged_failure = failure
        now = monotonic()
        if self._diagnostics is not None and now >= self._next_wait_log:
            self._next_wait_log = now + 5.0
            try:
                state = self._diagnostics()
            except Exception:
                _LOG.exception('Unable to collect application shutdown counters')
            else:
                _LOG.info('Application shutdown waiting step=%s elapsed_ms=%.1f state=%s',
                          step_name, (now - self._phase_started) * 1000, state)
        self.shutdown_failed.emit(message)
