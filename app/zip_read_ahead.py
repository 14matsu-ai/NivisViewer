"""One bounded encoded ZIP payload, overlapped with a single raster decoder.

This worker only reads archive bytes. No image decode, GUI publication, disk
cache, unbounded queue, or book-global population is owned here.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event, RLock
from typing import Callable

from PySide6.QtCore import QByteArray, QObject, Signal


class _ReadSignals(QObject):
    completed = Signal()


@dataclass
class _PendingRead:
    image_id: str
    reserved_bytes: int
    cancelled: Event
    future: Future | None = None


class ZipReadAhead:
    def __init__(self, read: Callable[[str, Event], tuple[QByteArray, int]]) -> None:
        self._read = read
        self.signals = _ReadSignals()
        self._lock = RLock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nivis-zip-read")
        self._pending: _PendingRead | None = None
        self._next: dict[str, tuple[str, int]] = {}
        self._closed = False
        self.hits = 0
        self.starts = 0

    @property
    def reserved_bytes(self) -> int:
        with self._lock:
            return self._pending.reserved_bytes if self._pending else 0

    @property
    def busy(self) -> bool:
        with self._lock:
            pending = self._pending
            return bool(pending and pending.future is not None and not pending.future.done())

    def wait(self, timeout: float) -> bool:
        with self._lock:
            future = self._pending.future if self._pending else None
        if future is None:
            return True
        try:
            future.result(timeout=max(0.0, timeout))
        except TimeoutError:
            return future.done()
        except Exception:
            pass
        return True

    def configure(self, next_reads: dict[str, tuple[str, int]], keep: set[str]) -> None:
        with self._lock:
            if self._closed:
                return
            self._next = dict(next_reads)
            pending = self._pending
            if pending is not None and pending.image_id not in keep:
                self._cancel_locked(pending)

    def _cancel_locked(self, pending: _PendingRead) -> None:
        pending.cancelled.set()
        if pending.future is not None and pending.future.done():
            self._pending = None
            pending.future = None

    def cancel(self) -> None:
        self.configure({}, set())

    def take(self, image_id: str) -> tuple[QByteArray, int] | None:
        """Worker-only transfer; a matching in-progress read may finish first."""
        with self._lock:
            pending = self._pending
            if pending is None or pending.image_id != image_id or pending.cancelled.is_set():
                return None
            future = pending.future
        if future is None:
            return None
        try:
            payload = future.result()
            if pending.cancelled.is_set():
                return None
            self.hits += 1
            return payload
        except Exception:
            # Background failure is retried by the normal foreground source
            # path, which owns error reporting and its request cancellation.
            return None
        finally:
            with self._lock:
                if self._pending is pending:
                    self._pending = None
                # Future callbacks retain pending. Break that cycle so the
                # encoded payload is released immediately, without a GC pass.
                pending.future = None

    def kick(self, image_id: str) -> None:
        """Start the next encoded read only after this payload is available."""
        with self._lock:
            candidate = self._next.get(image_id)
            if self._closed or candidate is None or self._pending is not None:
                return
            next_id, size = candidate
            pending = _PendingRead(next_id, size, Event())
            self._pending = pending
            try:
                pending.future = self._pool.submit(self._read, next_id, pending.cancelled)
                self.starts += 1
                pending.future.add_done_callback(lambda _future: self._completed(pending))
            except RuntimeError:
                self._pending = None

    def _completed(self, pending: _PendingRead) -> None:
        with self._lock:
            if self._pending is pending and (self._closed or pending.cancelled.is_set()):
                self._pending = None
                pending.future = None
        self.signals.completed.emit()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._next.clear()
            if self._pending is not None:
                self._cancel_locked(self._pending)
        self._pool.shutdown(wait=False, cancel_futures=True)
