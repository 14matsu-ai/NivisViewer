"""Low-frequency process memory trace for leak investigation.

Only process-wide counters are recorded: commit charge, working set, system
commit headroom and the Python thread count. No paths, file names or image
contents are read or logged. One sample is a single GetProcessMemoryInfo /
GlobalMemoryStatusEx call on the GUI thread, and a log line is written only
when a counter moved by at least ``step_bytes`` or the heartbeat elapsed, so
the rotating log is not flooded.
"""
from __future__ import annotations

import logging
import threading
from time import monotonic
from typing import Callable

from PySide6.QtCore import QObject, QTimer

from .viewer_memory_policy import PhysicalMemorySnapshot, read_physical_memory_snapshot


_LOG = logging.getLogger("nivisviewer.memory")

GIB = 1024 ** 3
DEFAULT_SAMPLE_INTERVAL_MS = 10_000
DEFAULT_STEP_BYTES = GIB
DEFAULT_HEARTBEAT_SECONDS = 30 * 60.0


def _gib(value: int | None) -> str:
    return "n/a" if value is None else f"{value / GIB:.2f}"


def _signed_gib(value: int) -> str:
    return f"{value / GIB:+.2f}"


def describe_memory(snapshot: PhysicalMemorySnapshot | None = None) -> str:
    """One-line process counters; safe to call during interpreter teardown."""
    try:
        if snapshot is None:
            snapshot = read_physical_memory_snapshot()
    except Exception:
        snapshot = None
    if snapshot is None:
        return "commit_gib=n/a working_set_gib=n/a"
    return (f"commit_gib={_gib(snapshot.process_private_commit_bytes)} "
            f"working_set_gib={_gib(snapshot.process_working_set_bytes)}")


class MemoryTrace(QObject):
    """Logs process memory at start, on >= ``step_bytes`` moves and on exit."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        reader: Callable[[], PhysicalMemorySnapshot | None] = read_physical_memory_snapshot,
        interval_ms: int = DEFAULT_SAMPLE_INTERVAL_MS,
        step_bytes: int = DEFAULT_STEP_BYTES,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        super().__init__(parent)
        self._reader = reader
        self._step_bytes = max(1, int(step_bytes))
        self._heartbeat_seconds = max(1.0, float(heartbeat_seconds))
        self._clock = clock
        self._started_at = clock()
        self._last_logged_at: float | None = None
        self._last_commit: int | None = None
        self._last_working_set: int | None = None
        self._peak_commit = 0
        self._timer = QTimer(self)
        self._timer.setInterval(max(1000, int(interval_ms)))
        self._timer.timeout.connect(self.sample)

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        self._started_at = self._clock()
        self.record("start")
        self._timer.start()

    def stop(self, reason: str = "stop") -> None:
        self._timer.stop()
        self.record(reason)

    def sample(self) -> bool:
        """Poll once; log only if a counter moved enough or the heartbeat is due."""
        snapshot = self._read()
        if snapshot is None:
            return False
        commit = snapshot.process_private_commit_bytes or 0
        working_set = snapshot.process_working_set_bytes
        self._peak_commit = max(self._peak_commit, commit)
        now = self._clock()
        if self._last_logged_at is None:
            reason = "start"
        elif (abs(commit - (self._last_commit or 0)) >= self._step_bytes
              or abs(working_set - (self._last_working_set or 0)) >= self._step_bytes):
            reason = "growth" if commit >= (self._last_commit or 0) else "release"
        elif now - self._last_logged_at >= self._heartbeat_seconds:
            reason = "heartbeat"
        else:
            return False
        self._log(reason, snapshot, now)
        return True

    def record(self, reason: str) -> bool:
        """Log unconditionally, e.g. at start and shutdown boundaries."""
        snapshot = self._read()
        if snapshot is None:
            return False
        self._peak_commit = max(self._peak_commit, snapshot.process_private_commit_bytes or 0)
        self._log(reason, snapshot, self._clock())
        return True

    def _read(self) -> PhysicalMemorySnapshot | None:
        try:
            return self._reader()
        except Exception:
            return None

    def _log(self, reason: str, snapshot: PhysicalMemorySnapshot, now: float) -> None:
        commit = snapshot.process_private_commit_bytes or 0
        working_set = snapshot.process_working_set_bytes
        commit_delta = commit - (self._last_commit if self._last_commit is not None else commit)
        working_delta = working_set - (self._last_working_set
                                       if self._last_working_set is not None else working_set)
        _LOG.info(
            "Process memory reason=%s commit_gib=%s (%s) working_set_gib=%s (%s) "
            "peak_commit_gib=%s system_commit_available_gib=%s system_commit_total_gib=%s "
            "python_threads=%d uptime_s=%.0f",
            reason,
            _gib(commit), _signed_gib(commit_delta),
            _gib(working_set), _signed_gib(working_delta),
            _gib(self._peak_commit),
            _gib(snapshot.available_commit_bytes), _gib(snapshot.total_commit_bytes),
            threading.active_count(),
            now - self._started_at,
        )
        self._last_logged_at = now
        self._last_commit = commit
        self._last_working_set = working_set
