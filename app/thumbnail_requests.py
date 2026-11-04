"""Request bookkeeping, distinct from native worker and pixel ownership."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Callable

from .browser_thumbnail_scheduler import ThumbnailPriority

if TYPE_CHECKING:
    from .thumbnail_provider import _ThumbnailWorker

RequestKey = tuple[str, int, int]


@dataclass(frozen=True)
class PendingThumbnail:
    worker: _ThumbnailWorker
    priority: ThumbnailPriority


@dataclass(frozen=True, slots=True)
class CancelledRequests:
    matched: int
    taken: tuple[tuple[RequestKey, _ThumbnailWorker], ...]
    retained: tuple[_ThumbnailWorker, ...]


class ThumbnailRequests:
    def __init__(self) -> None:
        self.pending: dict[RequestKey, PendingThumbnail] = {}
        self.lock = Lock()
        # Retiring a generation drops logical requests, not native ownership.
        self.owned_workers: set[_ThumbnailWorker] = set()
        self.waiting_generation: dict[_ThumbnailWorker, RequestKey] = {}

    def _cancel_locked(self, key, pending, try_take, *, interrupt_running, forget_running):
        worker = pending.worker
        if interrupt_running:
            worker.cancelled.set()
        taken = try_take(worker)
        if taken:
            if not interrupt_running:
                worker.cancelled.set()
            self.pending.pop(key, None)
        elif forget_running:
            self.pending.pop(key, None)
        return taken

    def cancel_where(
        self,
        matches: Callable[[RequestKey, PendingThumbnail], bool],
        try_take: Callable[[_ThumbnailWorker], bool],
        *,
        interrupt_running: bool = False,
        forget_running: bool = False,
    ) -> CancelledRequests:
        """Queued-only cancellation must take BEFORE setting the cancel event.

        Invalidation/close can interrupt runners, retaining their pending record
        until completion. Generation retirement alone may forget those records;
        native ownership still fences source-handle release and pool shutdown.
        Callers emit workflow/capacity signals after this lock is released.
        """
        taken = []
        retained = []
        matched = 0
        with self.lock:
            for key, pending in tuple(self.pending.items()):
                if not matches(key, pending):
                    continue
                matched += 1
                if self._cancel_locked(key, pending, try_take,
                                       interrupt_running=interrupt_running,
                                       forget_running=forget_running):
                    taken.append((key, pending.worker))
                else:
                    retained.append(pending.worker)
        return CancelledRequests(matched, tuple(taken), tuple(retained))

    def cancel_one(self, key: RequestKey, try_take, *, interrupt_running: bool) -> bool:
        with self.lock:
            pending = self.pending.get(key)
            return pending is not None and self._cancel_locked(
                key, pending, try_take, interrupt_running=interrupt_running,
                forget_running=False,
            )

    def release_source_handles(self, affected, try_take, *, wait_msecs: int) -> bool:
        batch = self.cancel_where(lambda _key, p: affected(p.worker), try_take,
                                  interrupt_running=True)
        waiting = set(batch.retained)
        # Old-generation runners no longer have logical pending records.
        with self.lock:
            for worker in self.owned_workers:
                if affected(worker) and worker.run_started.is_set() and not worker.io_finished.is_set():
                    worker.cancelled.set()
                    waiting.add(worker)
        deadline = monotonic() + max(0, wait_msecs) / 1000
        return all(worker.io_finished.wait(max(0, deadline - monotonic())) for worker in waiting)

    def handoff_worker(self, key: RequestKey, nonce):
        with self.lock:
            pending = self.pending.get(key)
            if pending is not None and pending.worker.request_nonce is nonce:
                return pending.worker
        return None

    def remove_owned_request(self, key: RequestKey, worker):
        with self.lock:
            pending = self.pending.get(key)
            if pending is not None and pending.worker is worker:
                return self.pending.pop(key)
        return None
