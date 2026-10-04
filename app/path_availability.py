from __future__ import annotations

import os
from .cloud_files import require_local, OnlineOnlyError
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Event, Lock
import time

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot


class PathAvailability(StrEnum):
    UNKNOWN = "unknown"
    CHECKING = "checking"
    AVAILABLE = "available"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    ONLINE_ONLY = "online_only"


class PathAvailabilityServiceState(StrEnum):
    RUNNING = "running"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


@dataclass(frozen=True)
class PathAvailabilityResult:
    request_id: int
    path: str
    path_key: str
    state: PathAvailability
    checked_at: float


class PathAvailabilityFileSystem:
    """Path probe adapter. Every method is called from a worker."""

    def probe(self, path: str) -> PathAvailability:
        try:
            require_local(path)
            os.stat(path)
            return PathAvailability.AVAILABLE
        except OnlineOnlyError:
            return PathAvailability.ONLINE_ONLY
        except FileNotFoundError:
            return (
                PathAvailability.UNAVAILABLE
                if path.startswith("\\\\")
                else PathAvailability.MISSING
            )
        except PermissionError:
            return PathAvailability.ERROR
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            if winerror in {21, 53, 64, 67, 121, 1231, 1232}:
                return PathAvailability.UNAVAILABLE
            return PathAvailability.ERROR


class _ProbeSignals(QObject):
    finished = Signal(object)


class _ProbeWorker(QRunnable):
    def __init__(
        self,
        request_id: int,
        path: str,
        key: str,
        filesystem: PathAvailabilityFileSystem,
        cancelled: Event,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.path = path
        self.key = key
        self.filesystem = filesystem
        self.cancelled = cancelled
        self.signals = _ProbeSignals()

    @Slot()
    def run(self) -> None:
        if self.cancelled.is_set():
            return
        state = self.filesystem.probe(self.path)
        if self.cancelled.is_set():
            return
        self.signals.finished.emit(
            PathAvailabilityResult(
                self.request_id,
                self.path,
                self.key,
                state,
                time.monotonic(),
            )
        )


class PathAvailabilityService(QObject):
    result_ready = Signal(object)
    availability_changed = Signal(object)
    shutdown_finished = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        filesystem: PathAvailabilityFileSystem | None = None,
        max_workers: int = 2,
    ) -> None:
        super().__init__(parent)
        self.filesystem = filesystem or PathAvailabilityFileSystem()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max(1, min(4, int(max_workers))))
        self._lock = Lock()
        self._next_request_id = 0
        self._pending_by_key: dict[str, int] = {}
        self._cancel_by_request: dict[int, Event] = {}
        self._cache: dict[str, PathAvailabilityResult] = {}
        self._state = PathAvailabilityServiceState.RUNNING
        self._shutdown_poll_timer = QTimer(self)
        self._shutdown_poll_timer.setInterval(25)
        self._shutdown_poll_timer.timeout.connect(self._poll_shutdown)

    @property
    def state(self) -> PathAvailabilityServiceState:
        with self._lock:
            return self._state

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending_by_key)

    def cached_state(self, path: str | Path) -> PathAvailability:
        with self._lock:
            result = self._cache.get(path_key(path))
            if result is not None:
                return result.state
            if path_key(path) in self._pending_by_key:
                return PathAvailability.CHECKING
        return PathAvailability.UNKNOWN

    def probe(self, path: str | Path, *, force: bool = False) -> int:
        display = lexical_absolute(path)
        key = path_key(display)
        with self._lock:
            if self._state is not PathAvailabilityServiceState.RUNNING:
                return 0
            pending = self._pending_by_key.get(key)
            if pending is not None:
                return pending
            self._next_request_id += 1
            request_id = self._next_request_id
            cached = None if force else self._cache.get(key)
            if cached is None:
                cancelled = Event()
                self._pending_by_key[key] = request_id
                self._cancel_by_request[request_id] = cancelled
            else:
                cancelled = None
        if cached is not None:
            result = PathAvailabilityResult(
                request_id,
                display,
                key,
                cached.state,
                cached.checked_at,
            )
            QTimer.singleShot(0, self, lambda value=result: self.result_ready.emit(value))
            return request_id
        assert cancelled is not None
        worker = _ProbeWorker(
            request_id,
            display,
            key,
            self.filesystem,
            cancelled,
        )
        worker.signals.finished.connect(self._on_finished)
        self._pool.start(worker)
        return request_id

    def request_probe(
        self,
        path: str | Path,
        purpose: str = "generic",
        request_id: int | None = None,
        *,
        force: bool = False,
    ) -> int:
        """High-level probe entry point.

        ``purpose`` and caller request IDs are intentionally coordination
        metadata only. The service still deduplicates actual filesystem work
        by lexical path, and consumers keep their own latest-request context.
        """

        del purpose, request_id
        return self.probe(path, force=force)

    def cancel_request(self, request_id: int) -> bool:
        with self._lock:
            # A worker may be shared by several models/coordinators because
            # probes are deduplicated by path. Cancellation is therefore a
            # consumer-side generation change; only service shutdown cancels
            # the shared physical probe.
            return int(request_id) in self._cancel_by_request

    def invalidate(self, path: str | Path | None = None) -> None:
        with self._lock:
            if path is None:
                self._cache.clear()
            else:
                self._cache.pop(path_key(path), None)

    def begin_shutdown(self) -> None:
        with self._lock:
            if self._state is PathAvailabilityServiceState.STOPPED:
                return
            if self._state is PathAvailabilityServiceState.SHUTTING_DOWN:
                return
            self._state = PathAvailabilityServiceState.SHUTTING_DOWN
            pending = tuple(self._cancel_by_request.values())
            self._pending_by_key.clear()
            self._cancel_by_request.clear()
            self._cache.clear()
        for event in pending:
            event.set()
        try:
            self._pool.clear()
            active_count = self._pool.activeThreadCount()
        except RuntimeError:
            # During QObject tree teardown Qt may delete the pool before a
            # model's destroyed hook reaches this idempotent close method.
            active_count = 0
        if active_count > 0:
            self._shutdown_poll_timer.start()
            return
        self._finish_shutdown()

    def _poll_shutdown(self) -> None:
        try:
            active_count = self._pool.activeThreadCount()
        except RuntimeError:
            active_count = 0
        if active_count <= 0:
            self._finish_shutdown()

    def _finish_shutdown(self) -> None:
        with self._lock:
            if self._state is PathAvailabilityServiceState.STOPPED:
                return
            self._state = PathAvailabilityServiceState.STOPPED
        try:
            self._shutdown_poll_timer.stop()
            self.shutdown_finished.emit()
        except RuntimeError:
            # The containing QObject tree is already being destroyed.
            pass

    def close(self) -> None:
        self.begin_shutdown()

    @Slot(object)
    def _on_finished(self, result: PathAvailabilityResult) -> None:
        with self._lock:
            self._pending_by_key.pop(result.path_key, None)
            self._cancel_by_request.pop(result.request_id, None)
            if self._state is not PathAvailabilityServiceState.RUNNING:
                return
            self._cache[result.path_key] = result
        self.result_ready.emit(result)
        self.availability_changed.emit(result)


def lexical_absolute(path: str | Path) -> str:
    return os.path.abspath(os.path.normpath(os.fspath(path)))


def path_key(path: str | Path) -> str:
    return os.path.normcase(lexical_absolute(path)).casefold()
