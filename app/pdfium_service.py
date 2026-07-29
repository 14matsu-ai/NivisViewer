from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import StrEnum
import logging
from queue import PriorityQueue
from threading import Lock, Thread
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


_PDFIUM_LOG = logging.getLogger("nivisviewer.pdfium")


class PdfiumServiceState(StrEnum):
    RUNNING = "running"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


class PdfAvailabilityState(StrEnum):
    UNKNOWN = "unknown"
    CHECKING = "checking"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass(frozen=True)
class PdfAvailabilitySnapshot:
    state: PdfAvailabilityState
    backend_name: str | None = None
    library_version: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    checked_at: float | None = None


_CONTROL_CLOSE_PRIORITY = -10_000
_CONTROL_AVAILABILITY_PRIORITY = -20_000


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
    control: str | None = None


class PdfiumService:
    """Application-wide serialized gateway for every PDFium call."""

    def __init__(
        self,
        backend: PdfBackend | None = None,
        *,
        auto_probe: bool = True,
    ) -> None:
        self.backend = backend or PdfiumBackend()
        self._queue: PriorityQueue[_QueueItem] = PriorityQueue()
        self._lock = Lock()
        self._pending: dict[tuple[object, ...], _PendingJob] = {}
        self._sequence = 0
        self._state = PdfiumServiceState.RUNNING
        self._active_job: _PendingJob | None = None
        self._closing_documents: set[str] = set()
        self._document_close_errors: dict[str, BaseException] = {}
        self._shutdown_future: Future | None = None
        self.last_shutdown_error: str | None = None
        self._active_calls = 0
        self.maximum_concurrent_calls = 0
        self._availability = PdfAvailabilitySnapshot(
            PdfAvailabilityState.UNKNOWN,
            backend_name=type(self.backend).__name__,
        )
        self._availability_future: Future | None = None
        self._worker = Thread(
            target=self._run,
            name="NivisViewer-Pdfium",
            daemon=False,
        )
        self._worker.start()
        if auto_probe:
            self.request_availability_probe()

    @property
    def state(self) -> PdfiumServiceState:
        with self._lock:
            return self._state

    @property
    def is_available(self) -> bool:
        return (
            self.availability_snapshot.state
            is PdfAvailabilityState.AVAILABLE
        )

    @property
    def availability_snapshot(self) -> PdfAvailabilitySnapshot:
        with self._lock:
            return self._availability

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def request_availability_probe(self, *, force: bool = False) -> bool:
        with self._lock:
            if self._state is not PdfiumServiceState.RUNNING:
                return False
            if (
                not force
                and self._availability.state
                in {
                    PdfAvailabilityState.CHECKING,
                    PdfAvailabilityState.AVAILABLE,
                    PdfAvailabilityState.UNAVAILABLE,
                }
            ):
                return False
            self._availability = PdfAvailabilitySnapshot(
                PdfAvailabilityState.CHECKING,
                backend_name=type(self.backend).__name__,
            )

        def check() -> bool:
            checker = getattr(self.backend, "is_available", None)
            try:
                available = (
                    bool(checker())
                    if callable(checker)
                    else True
                )
            except PdfBackendError as exc:
                snapshot = PdfAvailabilitySnapshot(
                    PdfAvailabilityState.UNAVAILABLE
                    if exc.code is PdfErrorCode.BACKEND_UNAVAILABLE
                    else PdfAvailabilityState.ERROR,
                    backend_name=type(self.backend).__name__,
                    error_code=exc.code.value,
                    error_message=exc.debug_message or exc.user_message,
                    checked_at=time.monotonic(),
                )
            except Exception as exc:
                snapshot = PdfAvailabilitySnapshot(
                    PdfAvailabilityState.ERROR,
                    backend_name=type(self.backend).__name__,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                    checked_at=time.monotonic(),
                )
            else:
                module = getattr(self.backend, "_pdfium_module", None)
                version = getattr(module, "__version__", None)
                snapshot = PdfAvailabilitySnapshot(
                    PdfAvailabilityState.AVAILABLE
                    if available
                    else PdfAvailabilityState.UNAVAILABLE,
                    backend_name=type(self.backend).__name__,
                    library_version=str(version) if version is not None else None,
                    checked_at=time.monotonic(),
                )
            with self._lock:
                if self._state is PdfiumServiceState.RUNNING:
                    self._availability = snapshot
            return snapshot.state is PdfAvailabilityState.AVAILABLE

        future = self._submit(
            ("availability", self._next_sequence()),
            _CONTROL_AVAILABILITY_PRIORITY,
            check,
            deduplicate=False,
        )
        with self._lock:
            self._availability_future = future
        return True

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
        cancelled: list[_PendingJob] = []
        with self._lock:
            if self._state is not PdfiumServiceState.RUNNING:
                future = self._cancelled_future("PDF service is stopping")
            else:
                key = ("close", str(document_id))
                existing = self._pending.get(key)
                if existing is not None:
                    future = existing.future
                else:
                    self._closing_documents.add(str(document_id))
                    active_key = (
                        self._active_job.key
                        if self._active_job is not None
                        else None
                    )
                    for pending_key, job in tuple(self._pending.items()):
                        if (
                            pending_key != active_key
                            and self._job_targets_document(job, document_id)
                        ):
                            self._pending.pop(pending_key, None)
                            cancelled.append(job)
                    sequence = self._next_sequence_locked()
                    future = Future()
                    job = _PendingJob(
                        key,
                        lambda: self.backend.close_document(document_id),
                        future,
                        _CONTROL_CLOSE_PRIORITY,
                        sequence,
                        control="close_document",
                    )
                    self._pending[key] = job
                    self._queue.put(
                        _QueueItem(job.priority, sequence, 0, key)
                    )
        self._cancel_jobs(cancelled, "PDF document is closing")
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
        with self._lock:
            return not self._document_close_errors

    def shutdown(self, *, wait_seconds: float = 0.5) -> bool:
        cancelled: list[_PendingJob] = []
        active_job: _PendingJob | None = None
        already_stopped = False
        with self._lock:
            if self._state is PdfiumServiceState.STOPPED:
                close_future = self._shutdown_future
                already_stopped = True
            elif self._state is PdfiumServiceState.SHUTTING_DOWN:
                close_future = self._shutdown_future
                active_job = self._active_job
            else:
                self._state = PdfiumServiceState.SHUTTING_DOWN
                self._availability = PdfAvailabilitySnapshot(
                    PdfAvailabilityState.STOPPED,
                    backend_name=type(self.backend).__name__,
                    checked_at=time.monotonic(),
                )
                active_key = (
                    self._active_job.key
                    if self._active_job is not None
                    else None
                )
                active_job = self._active_job
                for key, job in tuple(self._pending.items()):
                    if key != active_key:
                        self._pending.pop(key, None)
                        cancelled.append(job)
                sequence = self._next_sequence_locked()
                close_future = Future()
                close_job = _PendingJob(
                    ("shutdown-close-all", sequence),
                    self.backend.close_all,
                    close_future,
                    _CONTROL_CLOSE_PRIORITY,
                    sequence,
                    control="shutdown_close_all",
                )
                self._shutdown_future = close_future
                self._pending[close_job.key] = close_job
                self._queue.put(
                    _QueueItem(
                        close_job.priority,
                        close_job.sequence,
                        0,
                        close_job.key,
                    )
                )
        if not already_stopped:
            self._cancel_jobs(cancelled, "PDF service is stopping")
            if active_job is not None:
                self._request_job_cancel(active_job)
        timeout = max(0.0, float(wait_seconds))
        deadline = time.monotonic() + timeout
        if close_future is not None:
            try:
                close_future.result(timeout=timeout)
            except Exception:
                pass
        remaining = max(0.0, deadline - time.monotonic())
        self._worker.join(timeout=remaining)
        if self._worker.is_alive():
            self._record_shutdown_error(
                "PDFium worker did not stop before the shutdown timeout.",
                log=True,
            )
            return False
        if close_future is not None:
            try:
                close_future.result(timeout=0)
            except BaseException as exc:
                detail = getattr(exc, "debug_message", None) or str(exc)
                self._record_shutdown_error(
                    (
                        "PDFium document close failed: "
                        f"{type(exc).__name__}: {detail}"
                    ),
                    log=False,
                )
                return False
        with self._lock:
            stopped = self._state is PdfiumServiceState.STOPPED
            if stopped:
                self.last_shutdown_error = None
        if not stopped:
            self._record_shutdown_error(
                "PDFium worker exited without completing shutdown.",
                log=True,
            )
            return False
        return True

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
            return self._cancelled_future("PDF request was cancelled")
        with self._lock:
            if self._state is not PdfiumServiceState.RUNNING:
                return self._cancelled_future("PDF service is stopping")
            if (
                len(key) > 1
                and key[0] == "render"
                and str(key[1]) in self._closing_documents
            ):
                return self._cancelled_future("PDF document is closing")
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
            job: _PendingJob | None = None
            try:
                with self._lock:
                    job = self._pending.get(item.key)
                    if (
                        job is None
                        or job.version != item.version
                        or job.future.done()
                    ):
                        continue
                    self._active_job = job
                if is_cancelled(job.cancel_token):
                    self._finish_error(
                        job,
                        PdfBackendError(PdfErrorCode.CANCELLED),
                    )
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
                if job.control == "shutdown_close_all":
                    with self._lock:
                        self._state = PdfiumServiceState.STOPPED
                        self._availability = PdfAvailabilitySnapshot(
                            PdfAvailabilityState.STOPPED,
                            backend_name=type(self.backend).__name__,
                            checked_at=time.monotonic(),
                        )
                        self._closing_documents.clear()
                        self._active_job = None
                    return
            finally:
                with self._lock:
                    if self._active_job is job:
                        self._active_job = None
                self._queue.task_done()

    def _finish_result(self, job: _PendingJob, result: object) -> None:
        with self._lock:
            self._pending.pop(job.key, None)
            if job.control == "close_document" and len(job.key) > 1:
                document_id = str(job.key[1])
                self._closing_documents.discard(document_id)
                self._document_close_errors.pop(document_id, None)
            elif job.control == "shutdown_close_all":
                self._document_close_errors.clear()
        if not job.future.done():
            job.future.set_result(result)

    def _finish_error(self, job: _PendingJob, exc: BaseException) -> None:
        with self._lock:
            self._pending.pop(job.key, None)
            if job.control == "close_document" and len(job.key) > 1:
                document_id = str(job.key[1])
                self._closing_documents.discard(document_id)
                self._document_close_errors[document_id] = exc
        if not job.future.done():
            job.future.set_exception(exc)

    @staticmethod
    def _job_targets_document(job: _PendingJob, document_id: str) -> bool:
        return (
            len(job.key) > 1
            and job.key[0] == "render"
            and str(job.key[1]) == str(document_id)
        )

    @classmethod
    def _cancel_jobs(cls, jobs: list[_PendingJob], message: str) -> None:
        for job in jobs:
            cls._request_job_cancel(job)
            if not job.future.done():
                job.future.set_exception(
                    PdfBackendError(
                        PdfErrorCode.CANCELLED,
                        debug_message=message,
                    )
                )

    @staticmethod
    def _request_job_cancel(job: _PendingJob) -> None:
        token = job.cancel_token
        for method_name in ("set", "cancel"):
            method = getattr(token, method_name, None)
            if callable(method):
                method()
                return

    def _record_shutdown_error(self, message: str, *, log: bool) -> None:
        with self._lock:
            repeated = self.last_shutdown_error == message
            self.last_shutdown_error = message
        if log and not repeated:
            _PDFIUM_LOG.error(message)

    @staticmethod
    def _cancelled_future(message: str) -> Future:
        future: Future = Future()
        future.set_exception(
            PdfBackendError(
                PdfErrorCode.CANCELLED,
                debug_message=message,
            )
        )
        return future

    @staticmethod
    def _wait(future: Future, cancel_token):
        while not future.done():
            time.sleep(0.01)
        result = future.result()
        if is_cancelled(cancel_token):
            raise PdfBackendError(PdfErrorCode.CANCELLED)
        return result

    def _next_sequence(self) -> int:
        with self._lock:
            return self._next_sequence_locked()

    def _next_sequence_locked(self) -> int:
        self._sequence += 1
        return self._sequence
