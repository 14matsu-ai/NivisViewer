from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
from queue import PriorityQueue
from threading import Event, Lock, Thread
import time
from typing import Callable

from .pdf_backend import (
    PdfBackend,
    PdfBackendError,
    PdfDocumentInfo,
    PdfErrorCode,
    PdfRenderPriority,
    PdfRenderRequest,
    PdfRenderResult,
    is_cancelled,
)
from .pdfium_backend import PdfiumBackend


@dataclass(order=True)
class _QueueItem:
    priority: int
    sequence: int
    version: int
    key: tuple[object, ...] = field(compare=False)


@dataclass
class _PendingJob:
    key: tuple[object, ...]
    operation: Callable[[], object]
    future: Future
    priority: int
    sequence: int
    version: int = 0
    cancel_token: object = None


class PdfiumService:
    """Application-wide serialized gateway for every PDFium call."""

    def __init__(self, backend: PdfBackend | None = None) -> None:
        self.backend = backend or PdfiumBackend()
        self._queue: PriorityQueue[_QueueItem] = PriorityQueue()
        self._lock = Lock()
        self._pending: dict[tuple[object, ...], _PendingJob] = {}
        self._sequence = 0
        self._closed = False
        self._stop = Event()
        self._active_calls = 0
        self.maximum_concurrent_calls = 0
        self._worker = Thread(
            target=self._run,
            name="NivisViewer-Pdfium",
            daemon=True,
        )
        self._worker.start()

    @property
    def is_available(self) -> bool:
        checker = getattr(self.backend, "is_available", None)
        if not callable(checker):
            return True
        future = self._submit(
            ("availability", self._next_sequence()),
            int(PdfRenderPriority.DOCUMENT_OPEN),
            checker,
            deduplicate=False,
        )
        try:
            return bool(self._wait(future, None))
        except PdfBackendError:
            return False

    def open_document(
        self,
        path: str,
        *,
        password: str | None = None,
        cancel_token=None,
    ) -> PdfDocumentInfo:
        key = ("open", str(path), password is not None, id(cancel_token))
        future = self._submit(
            key,
            int(PdfRenderPriority.DOCUMENT_OPEN),
            lambda: self.backend.open_document(
                path,
                password=password,
                cancel_token=cancel_token,
            ),
            cancel_token=cancel_token,
            deduplicate=False,
        )
        return self._wait(future, cancel_token)

    def render_page(
        self,
        request: PdfRenderRequest,
        *,
        cancel_token=None,
    ) -> PdfRenderResult:
        future = self._submit(
            ("render", *request.cache_key),
            int(request.priority),
            lambda: self.backend.render_page(
                request,
                cancel_token=cancel_token,
            ),
            cancel_token=cancel_token,
            deduplicate=True,
        )
        return self._wait(future, cancel_token)

    def close_document(self, document_id: str, *, wait: bool = False) -> None:
        future = self._submit(
            ("close", document_id),
            int(PdfRenderPriority.DOCUMENT_CLOSE),
            lambda: self.backend.close_document(document_id),
            deduplicate=True,
        )
        if wait:
            self._wait(future, None)

    def flush(self, *, wait_seconds: float = 2.0) -> bool:
        """Wait until requests queued before this barrier have completed."""
        key = ("flush", self._next_sequence())
        future = self._submit(
            key,
            int(PdfRenderPriority.DOCUMENT_CLOSE),
            lambda: None,
            deduplicate=False,
        )
        try:
            future.result(timeout=max(0.0, float(wait_seconds)))
        except Exception:
            return False
        return True

    def shutdown(self, *, wait_seconds: float = 0.5) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sequence = self._next_sequence_locked()
            close_future: Future = Future()
            close_job = _PendingJob(
                ("shutdown-close-all", sequence),
                self.backend.close_all,
                close_future,
                int(PdfRenderPriority.DOCUMENT_CLOSE),
                sequence,
            )
            self._pending[close_job.key] = close_job
            self._queue.put(
                _QueueItem(close_job.priority, sequence, 0, close_job.key)
            )
        try:
            close_future.result(timeout=max(0.0, float(wait_seconds)))
        except Exception:
            pass
        self._stop.set()
        self._queue.put(_QueueItem(10_000, self._next_sequence(), 0, ("stop",)))
        self._worker.join(timeout=max(0.0, float(wait_seconds)))

    def _submit(
        self,
        key: tuple[object, ...],
        priority: int,
        operation: Callable[[], object],
        *,
        cancel_token=None,
        deduplicate: bool,
    ) -> Future:
        if is_cancelled(cancel_token):
            future: Future = Future()
            future.set_exception(PdfBackendError(PdfErrorCode.CANCELLED))
            return future
        with self._lock:
            if self._closed:
                future = Future()
                future.set_exception(PdfBackendError(PdfErrorCode.CANCELLED))
                return future
            existing = self._pending.get(key) if deduplicate else None
            if existing is not None:
                if priority < existing.priority:
                    existing.priority = priority
                    existing.version += 1
                    self._queue.put(
                        _QueueItem(
                            priority,
                            existing.sequence,
                            existing.version,
                            key,
                        )
                    )
                return existing.future
            sequence = self._next_sequence_locked()
            future = Future()
            job = _PendingJob(
                key,
                operation,
                future,
                priority,
                sequence,
                cancel_token=cancel_token,
            )
            self._pending[key] = job
            self._queue.put(_QueueItem(priority, sequence, 0, key))
            return future

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item.key == ("stop",):
                return
            with self._lock:
                job = self._pending.get(item.key)
                if (
                    job is None
                    or job.version != item.version
                    or job.future.done()
                ):
                    continue
            if is_cancelled(job.cancel_token):
                self._finish_error(job, PdfBackendError(PdfErrorCode.CANCELLED))
                continue
            try:
                with self._lock:
                    self._active_calls += 1
                    self.maximum_concurrent_calls = max(
                        self.maximum_concurrent_calls,
                        self._active_calls,
                    )
                result = job.operation()
            except BaseException as exc:
                self._finish_error(job, exc)
            else:
                self._finish_result(job, result)
            finally:
                with self._lock:
                    self._active_calls -= 1

    def _finish_result(self, job: _PendingJob, result: object) -> None:
        with self._lock:
            self._pending.pop(job.key, None)
        if not job.future.done():
            job.future.set_result(result)

    def _finish_error(self, job: _PendingJob, exc: BaseException) -> None:
        with self._lock:
            self._pending.pop(job.key, None)
        if not job.future.done():
            job.future.set_exception(exc)

    @staticmethod
    def _wait(future: Future, cancel_token):
        while not future.done():
            if is_cancelled(cancel_token):
                raise PdfBackendError(PdfErrorCode.CANCELLED)
            time.sleep(0.01)
        return future.result()

    def _next_sequence(self) -> int:
        with self._lock:
            return self._next_sequence_locked()

    def _next_sequence_locked(self) -> int:
        self._sequence += 1
        return self._sequence
