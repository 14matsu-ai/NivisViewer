from __future__ import annotations

from .i18n import tr


from enum import StrEnum
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal, Slot

from .path_availability import (
    PathAvailability,
    PathAvailabilityResult,
    PathAvailabilityService,
    lexical_absolute,
)


class StartupRestoreState(StrEnum):
    IDLE = "idle"
    PROBING = "probing"
    OPENING = "opening"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class StartupRestoreCoordinator(QObject):
    """Restore the configured last path without probing it on the GUI thread."""

    state_changed = Signal(str)
    notification_requested = Signal(str)
    finished = Signal(str)

    def __init__(
        self,
        availability_service: PathAvailabilityService,
        open_path: Callable[[str], object],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.availability_service = availability_service
        self._open_path = open_path
        self._state = StartupRestoreState.IDLE
        self._generation = 0
        self._request_id = 0
        self._path = ""
        self.availability_service.result_ready.connect(self._on_probe_result)

    @property
    def state(self) -> StartupRestoreState:
        return self._state

    @property
    def active_path(self) -> str:
        return self._path

    def start(self, path: str | Path) -> bool:
        display = lexical_absolute(path)
        self.cancel()
        self._generation += 1
        self._path = display
        self._set_state(StartupRestoreState.PROBING)
        self._request_id = self.availability_service.request_probe(
            display,
            "startup_restore",
            self._generation,
        )
        if not self._request_id:
            self._set_state(StartupRestoreState.FAILED)
            self.finished.emit(self._state.value)
            return False
        return True

    def cancel(self) -> None:
        if self._state not in {
            StartupRestoreState.PROBING,
            StartupRestoreState.OPENING,
        }:
            return
        request_id = self._request_id
        self._generation += 1
        self._request_id = 0
        self._set_state(StartupRestoreState.CANCELLED)
        if request_id:
            self.availability_service.cancel_request(request_id)
        self.finished.emit(self._state.value)

    @Slot(object)
    def _on_probe_result(self, result: PathAvailabilityResult) -> None:
        if (
            self._state is not StartupRestoreState.PROBING
            or result.request_id != self._request_id
            or result.path != self._path
        ):
            return
        self._request_id = 0
        if result.state is PathAvailability.AVAILABLE:
            self._set_state(StartupRestoreState.OPENING)
            try:
                opened = self._open_path(self._path)
            except Exception:
                opened = False
            self._set_state(
                StartupRestoreState.COMPLETED
                if opened is not False
                else StartupRestoreState.FAILED
            )
        else:
            self._set_state(StartupRestoreState.FAILED)
            self.notification_requested.emit(
                {
                    PathAvailability.MISSING: tr('前回開いていた項目が見つかりません'),
                    PathAvailability.UNAVAILABLE: tr('前回開いていた項目へ現在アクセスできません'),
                    PathAvailability.ERROR: tr('前回開いていた項目を確認できません'),
                }.get(result.state, tr('前回開いていた項目を復元できません'))
            )
        self.finished.emit(self._state.value)

    def _set_state(self, state: StartupRestoreState) -> None:
        if self._state is state:
            return
        self._state = state
        self.state_changed.emit(state.value)
