from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from threading import Lock
from time import perf_counter


_LOGGER = logging.getLogger("nivisviewer.performance")


@dataclass(frozen=True)
class PerformanceEvent:
    operation_id: int
    name: str
    timestamp: float
    detail: str = ""


class PerformanceTrace:
    """Small opt-in timeline used by DEBUG logs and deterministic tests."""

    def __init__(self, *, enabled: bool | None = None) -> None:
        self.enabled = (
            _LOGGER.isEnabledFor(logging.DEBUG)
            or os.environ.get("NIVISVIEWER_DEBUG_TIMING") == "1"
            if enabled is None
            else bool(enabled)
        )
        self._lock = Lock()
        self._sequence = 0
        self._events: list[PerformanceEvent] = []

    def begin(self, name: str, detail: str = "") -> int:
        with self._lock:
            self._sequence += 1
            operation_id = self._sequence
        self.mark(operation_id, name, detail)
        return operation_id

    def mark(self, operation_id: int, name: str, detail: str = "") -> None:
        if not self.enabled:
            return
        event = PerformanceEvent(
            int(operation_id),
            str(name),
            perf_counter(),
            str(detail),
        )
        with self._lock:
            self._events.append(event)
        _LOGGER.debug(
            "operation=%s event=%s detail=%s",
            event.operation_id,
            event.name,
            event.detail,
        )

    def events_for(self, operation_id: int) -> tuple[PerformanceEvent, ...]:
        with self._lock:
            return tuple(
                event
                for event in self._events
                if event.operation_id == int(operation_id)
            )

    @property
    def latest_operation_id(self) -> int:
        with self._lock:
            return self._sequence

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


performance_trace = PerformanceTrace()
