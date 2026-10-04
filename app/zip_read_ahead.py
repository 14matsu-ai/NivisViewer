"""Bounded encoded ZIP read-ahead ring.

This worker only reads archive bytes.  It never decodes images or publishes
GUI state.  A single background reader fills a small completed-payload ring so
near pages can skip ZIP entry I/O without paying decoded-QImage memory cost.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Condition, Event, RLock
from typing import Callable

from PySide6.QtCore import QByteArray, QObject, Signal


class _ReadSignals(QObject):
    completed = Signal()


class ZipReadAhead:
    """Serial encoded-payload ring with bounded item count and byte ownership."""

    def __init__(
        self,
        read: Callable[[str, Event], tuple[QByteArray, int]],
        *,
        max_items: int = 4,
    ) -> None:
        self._read = read
        self.signals = _ReadSignals()
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._pool = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="nivis-zip-read",
        )
        self._next: dict[str, tuple[str, int]] = {}
        self._keep: set[str] = set()
        self._cache: OrderedDict[str, tuple[QByteArray, int]] = OrderedDict()
        self._cache_bytes = 0
        self._pending_image_id: str | None = None
        self._pending_reserved_bytes = 0
        self._pending_cancel: Event | None = None
        self._future: Future | None = None
        self._max_items = max(1, min(16, int(max_items)))
        self._byte_budget: int | None = None
        self._closed = False
        self.hits = 0
        self.starts = 0

    @property
    def reserved_bytes(self) -> int:
        with self._lock:
            return self._cache_bytes + self._pending_reserved_bytes

    @property
    def cached_count(self) -> int:
        with self._lock:
            return len(self._cache)

    @property
    def busy(self) -> bool:
        with self._lock:
            future = self._future
            return bool(future is not None and not future.done())

    def wait(self, timeout: float) -> bool:
        with self._lock:
            future = self._future
        if future is None:
            return True
        try:
            future.result(timeout=max(0.0, timeout))
        except TimeoutError:
            return future.done()
        except Exception:
            pass
        return True

    def configure(
        self,
        next_reads: dict[str, tuple[str, int]],
        keep: set[str],
        *,
        byte_budget: int | None = None,
    ) -> None:
        """Replace the desired near-page chain without blocking the GUI.

        Completed payloads outside the new near set are released immediately.
        A running read is cooperatively cancelled only when it is no longer
        useful to the new current-centered chain.
        """

        with self._condition:
            if self._closed:
                return
            self._next = dict(next_reads)
            self._keep = set(keep)
            self._byte_budget = (
                None
                if byte_budget is None
                else max(0, int(byte_budget))
            )
            for image_id in tuple(self._cache):
                if image_id not in self._keep:
                    payload, _read_calls = self._cache.pop(image_id)
                    self._cache_bytes -= payload.size()
            if self._byte_budget is not None:
                # Ordered insertion follows page distance. Drop farthest
                # completed payloads first if a live memory limit shrinks.
                while (
                    self._cache
                    and self._cache_bytes > self._byte_budget
                ):
                    _image_id, (payload, _calls) = self._cache.popitem(
                        last=True
                    )
                    self._cache_bytes -= payload.size()
            if (
                self._pending_image_id is not None
                and self._pending_cancel is not None
                and (
                    self._pending_image_id not in self._keep
                    or (
                        self._byte_budget is not None
                        and self._cache_bytes + self._pending_reserved_bytes
                        > self._byte_budget
                    )
                )
            ):
                # Keep the nearer completed payloads. The running read still
                # owns its reservation until cooperative cancellation settles.
                self._pending_cancel.set()
            self._condition.notify_all()

    def cancel(self) -> None:
        with self._condition:
            self._next.clear()
            self._keep.clear()
            self._cache.clear()
            self._cache_bytes = 0
            if self._pending_cancel is not None:
                self._pending_cancel.set()
            self._condition.notify_all()

    def take(self, image_id: str) -> tuple[QByteArray, int] | None:
        """Transfer one prepared payload to the foreground decoder.

        If this exact entry is currently being read, wait only until *that*
        payload becomes available.  If another entry owns the ZIP read lane,
        cancel that speculative read and let the foreground path take over.
        """

        while True:
            with self._condition:
                cached = self._cache.pop(image_id, None)
                if cached is not None:
                    payload, read_calls = cached
                    self._cache_bytes -= payload.size()
                    self.hits += 1
                    self._condition.notify_all()
                    return payload, read_calls

                future = self._future
                if future is None or future.done():
                    return None

                if self._pending_image_id != image_id:
                    if self._pending_cancel is not None:
                        self._pending_cancel.set()
                    return None

                self._condition.wait(timeout=0.05)

    def kick(self, image_id: str) -> None:
        """Fill the ring forward from ``image_id`` on the single ZIP read lane."""

        with self._condition:
            if self._closed:
                return
            future = self._future
            if future is not None and not future.done():
                return
            if image_id not in self._next:
                return
            future = self._pool.submit(self._fill, image_id)
            self._future = future
            future.add_done_callback(self._completed)

    def _fill(self, trigger: str) -> None:
        current_trigger = trigger
        while True:
            with self._condition:
                if self._closed or len(self._cache) >= self._max_items:
                    return
                candidate = self._next.get(current_trigger)
                if candidate is None:
                    return
                next_id, reserved_bytes = candidate
                if next_id not in self._keep:
                    return
                if next_id in self._cache:
                    current_trigger = next_id
                    continue

                reserved = max(0, int(reserved_bytes))
                if (
                    self._byte_budget is not None
                    and self._cache_bytes + reserved > self._byte_budget
                ):
                    return
                cancelled = Event()
                self._pending_image_id = next_id
                self._pending_reserved_bytes = reserved
                self._pending_cancel = cancelled
                self.starts += 1

            try:
                payload, read_calls = self._read(next_id, cancelled)
            except Exception:
                return
            finally:
                # The state is cleared after the payload handoff below so a
                # foreground take() can distinguish "still reading this id".
                pass

            if cancelled.is_set():
                return

            emit_completed = False
            with self._condition:
                if (
                    self._closed
                    or cancelled.is_set()
                    or next_id not in self._keep
                    or (
                        self._byte_budget is not None
                        and self._cache_bytes + payload.size()
                        > self._byte_budget
                    )
                ):
                    # Recheck after I/O: the budget or actual encoded size may
                    # have changed while this entry was being read.
                    return
                previous = self._cache.pop(next_id, None)
                if previous is not None:
                    self._cache_bytes -= previous[0].size()
                self._cache[next_id] = (payload, int(read_calls))
                self._cache_bytes += payload.size()
                self._pending_image_id = None
                self._pending_reserved_bytes = 0
                self._pending_cancel = None
                current_trigger = next_id
                emit_completed = True
                self._condition.notify_all()
            if emit_completed:
                self.signals.completed.emit()

    def _completed(self, future: Future) -> None:
        with self._condition:
            if self._future is future:
                self._future = None
            self._pending_image_id = None
            self._pending_reserved_bytes = 0
            self._pending_cancel = None
            self._condition.notify_all()
        self.signals.completed.emit()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._next.clear()
            self._keep.clear()
            self._cache.clear()
            self._cache_bytes = 0
            if self._pending_cancel is not None:
                self._pending_cancel.set()
            self._condition.notify_all()
        self._pool.shutdown(wait=False, cancel_futures=True)
