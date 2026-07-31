"""Restricted ZIP/JPEG display path modeled on ZipPlaFork's page worker.

The current -> next -> previous, one-active-job structure follows ZipPlaFork
revision 07955f5267e2fb92d6fc6e40fde2507d8fb07b3b (AGPL-3.0-or-later).
The concrete PySide6 implementation is local to NivisViewer; provenance and
the upstream method map are recorded in docs/ZIPPLAFORK_COMPARISON.md.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from math import isfinite
from threading import Event, Lock
from time import monotonic
from typing import Protocol

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QImage, QPixmap

from .image_work_coordinator import ImageWorkCoordinator, ImageWorkPriority


class _StreamedJpegDecode(Protocol):
    qimage: QImage
    original_size: tuple[int, int]
    bytes_read: int
    read_calls: int


class _StreamedJpegSource(Protocol):
    def open_streamed_jpeg_at_most(
        self,
        image_id: str,
        maximum_size: tuple[int, int],
    ) -> _StreamedJpegDecode | None: ...

    def cancel_image_request(self, image_id: str) -> None: ...


@dataclass(frozen=True)
class ZipPlaCompatibleRasterPage:
    page_index: int
    image_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_index", int(self.page_index))
        object.__setattr__(self, "image_id", str(self.image_id))
        if self.page_index < 0:
            raise ValueError("page_index must be non-negative")
        if not self.image_id:
            raise ValueError("image_id must not be empty")


@dataclass(frozen=True)
class ZipPlaCompatibleRasterRequest:
    """One single-page display request and its ordered prefetch frontier.

    ``maximum_size`` is the physical-pixel decoder bound.  ``prefetch`` is
    normally ``(next, previous)``.  The path admits at most one worker at a
    time and does not start the first prefetch page until the current page has
    become a display-ready ``QPixmap``.
    """

    source: _StreamedJpegSource
    source_epoch: int
    request_id: int
    page_index: int
    image_id: str
    maximum_size: tuple[int, int]
    device_pixel_ratio: float = 1.0
    prefetch: tuple[ZipPlaCompatibleRasterPage, ...] = ()

    def __post_init__(self) -> None:
        width, height = self.maximum_size
        normalized_size = (max(1, int(width)), max(1, int(height)))
        normalized_prefetch = tuple(self.prefetch)
        dpr = float(self.device_pixel_ratio)
        if self.source is None:
            raise ValueError("source must not be None")
        if int(self.page_index) < 0:
            raise ValueError("page_index must be non-negative")
        if not str(self.image_id):
            raise ValueError("image_id must not be empty")
        if not isfinite(dpr) or dpr <= 0:
            raise ValueError("device_pixel_ratio must be positive and finite")
        if any(
            not isinstance(page, ZipPlaCompatibleRasterPage)
            for page in normalized_prefetch
        ):
            raise TypeError(
                "prefetch entries must be ZipPlaCompatibleRasterPage"
            )
        object.__setattr__(self, "source_epoch", int(self.source_epoch))
        object.__setattr__(self, "request_id", int(self.request_id))
        object.__setattr__(self, "page_index", int(self.page_index))
        object.__setattr__(self, "image_id", str(self.image_id))
        object.__setattr__(self, "maximum_size", normalized_size)
        object.__setattr__(self, "device_pixel_ratio", dpr)
        object.__setattr__(self, "prefetch", normalized_prefetch)


@dataclass(frozen=True)
class ZipPlaCompatibleRasterMetrics:
    jobs_submitted: int = 0
    queued_callbacks: int = 0
    qpixmap_creations: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cancel_requests: int = 0
    bytes_read: int = 0
    read_calls: int = 0
    stale_results: int = 0
    job_failures: int = 0


@dataclass(frozen=True)
class _ArtifactKey:
    source_epoch: int
    source_identity: int
    page_index: int
    image_id: str
    maximum_size: tuple[int, int]
    device_pixel_ratio_milli: int


@dataclass(frozen=True)
class ZipPlaCompatibleRasterArtifact:
    source_epoch: int
    source_identity: int
    page_index: int
    image_id: str
    original_size: tuple[int, int]
    pixmap: QPixmap
    maximum_size: tuple[int, int]
    device_pixel_ratio: float
    worker_completed_at: float
    gui_ready_at: float
    gui_callback_ms: float
    bytes_read: int
    read_calls: int


@dataclass(frozen=True)
class ZipPlaCompatibleRasterFrame:
    source_epoch: int
    source_identity: int
    request_id: int
    page_index: int
    image_id: str
    original_size: tuple[int, int]
    pixmap: QPixmap
    maximum_size: tuple[int, int]
    device_pixel_ratio: float
    cache_hit: bool
    worker_completed_at: float
    gui_ready_at: float
    gui_callback_ms: float
    bytes_read: int
    read_calls: int


@dataclass(frozen=True)
class ZipPlaCompatibleRasterFailure:
    source_epoch: int
    source_identity: int
    request_id: int
    page_index: int
    image_id: str
    error: str
    cancelled: bool = False


@dataclass(frozen=True)
class _JobResult:
    serial: int
    key: _ArtifactKey
    request_id: int
    decode: _StreamedJpegDecode | None
    cancelled: bool
    error: str | None
    completed_at: float


class _JobSignals(QObject):
    completed = Signal(object)


class _ZipPlaCompatibleRasterJob(QRunnable):
    def __init__(
        self,
        *,
        serial: int,
        key: _ArtifactKey,
        request_id: int,
        source: _StreamedJpegSource,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.serial = int(serial)
        self.key = key
        self.source = source
        self.signals = _JobSignals()
        self.cancelled = Event()
        self.started = Event()
        self.finished = Event()
        self._token_lock = Lock()
        self._request_id = int(request_id)

    def adopt_request(self, request_id: int) -> None:
        """Let an in-flight page become the latest request for the same key."""
        with self._token_lock:
            self._request_id = int(request_id)

    def cancel(self) -> bool:
        if self.cancelled.is_set():
            return False
        self.cancelled.set()
        cancel = getattr(self.source, "cancel_image_request", None)
        if callable(cancel):
            try:
                cancel(self.key.image_id)
            except Exception:
                # Cancellation is advisory. Generation checks still reject a
                # result if a backend cannot interrupt its current read.
                pass
        return True

    def mark_removed_before_start(self) -> None:
        self.cancelled.set()
        self.finished.set()

    @Slot()
    def run(self) -> None:
        self.started.set()
        decode: _StreamedJpegDecode | None = None
        error: str | None = None
        try:
            if not self.cancelled.is_set():
                decode_method = getattr(
                    self.source,
                    "open_compatible_jpeg_at_most",
                    None,
                )
                if not callable(decode_method):
                    decode_method = self.source.open_streamed_jpeg_at_most
                decode = decode_method(
                    self.key.image_id,
                    self.key.maximum_size,
                )
            if self.cancelled.is_set():
                decode = None
            elif (
                decode is None
                or decode.qimage is None
                or decode.qimage.isNull()
            ):
                error = "JPEG decoder did not produce an image."
        except Exception as exc:  # pragma: no cover - worker safety boundary
            if not self.cancelled.is_set():
                error = str(exc) or exc.__class__.__name__
        with self._token_lock:
            request_id = self._request_id
        result = _JobResult(
            serial=self.serial,
            key=self.key,
            request_id=request_id,
            decode=decode,
            cancelled=self.cancelled.is_set(),
            error=error,
            completed_at=monotonic(),
        )
        try:
            self.signals.completed.emit(result)
        finally:
            self.finished.set()


class ZipPlaCompatibleRasterPath(QObject):
    """A single-job ZIP/JPEG path that publishes display-only artifacts.

    The path deliberately does not know about ``ImageCache``,
    ``ViewerRenderTask``, or ``ViewerWidget`` prepared-display caches.  A
    successful worker result crosses one queued signal boundary.  That GUI
    slot creates one ``QPixmap`` and either publishes the current frame or
    keeps the artifact in the bounded current/next/previous cache.
    """

    frameReady = Signal(object)
    frameFailed = Signal(object)
    artifactReady = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        image_work_coordinator: ImageWorkCoordinator | None = None,
    ) -> None:
        super().__init__(parent)
        self._coordinator = image_work_coordinator
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._accepting_requests = True
        self._shutdown_complete = False
        self._serial = 0
        self._current_request: ZipPlaCompatibleRasterRequest | None = None
        self._current_key: _ArtifactKey | None = None
        self._prefetch_keys: tuple[_ArtifactKey, ...] = ()
        self._prefetch_released_request_id: int | None = None
        self._source_by_key: dict[_ArtifactKey, _StreamedJpegSource] = {}
        self._page_by_key: dict[
            _ArtifactKey,
            ZipPlaCompatibleRasterPage,
        ] = {}
        self._active_job: _ZipPlaCompatibleRasterJob | None = None
        self._jobs: set[_ZipPlaCompatibleRasterJob] = set()
        self._artifacts: OrderedDict[
            _ArtifactKey,
            ZipPlaCompatibleRasterArtifact,
        ] = OrderedDict()
        self._failed_prefetch: set[_ArtifactKey] = set()
        self._metrics = ZipPlaCompatibleRasterMetrics()

    @property
    def metrics(self) -> ZipPlaCompatibleRasterMetrics:
        return replace(self._metrics)

    @property
    def active_job_count(self) -> int:
        return int(
            self._active_job is not None
            and not self._active_job.finished.is_set()
        )

    @property
    def queued_job_count(self) -> int:
        job = self._active_job
        return int(
            job is not None
            and not job.started.is_set()
            and not job.finished.is_set()
        )

    @property
    def artifact_page_indexes(self) -> tuple[int, ...]:
        return tuple(
            artifact.page_index for artifact in self._artifacts.values()
        )

    @property
    def artifact_bytes(self) -> int:
        return sum(
            max(0, artifact.pixmap.width())
            * max(0, artifact.pixmap.height())
            * 4
            for artifact in self._artifacts.values()
        )

    def has_cached_current(
        self,
        request: ZipPlaCompatibleRasterRequest,
    ) -> bool:
        """Return whether ``request`` can publish without worker activity."""
        return self._key_for(
            request,
            ZipPlaCompatibleRasterPage(
                request.page_index,
                request.image_id,
            ),
        ) in self._artifacts

    def request(self, request: ZipPlaCompatibleRasterRequest) -> bool:
        if not self._accepting_requests:
            return False

        current_key = self._key_for(
            request,
            ZipPlaCompatibleRasterPage(
                request.page_index,
                request.image_id,
            ),
        )
        prefetch_pages = self._normalized_prefetch(request)
        prefetch_keys = tuple(
            self._key_for(request, page) for page in prefetch_pages
        )
        self._current_request = request
        self._current_key = current_key
        self._prefetch_keys = prefetch_keys
        self._prefetch_released_request_id = None
        self._source_by_key = {
            key: request.source
            for key in (current_key, *prefetch_keys)
        }
        self._page_by_key = {
            current_key: ZipPlaCompatibleRasterPage(
                request.page_index,
                request.image_id,
            ),
            **{
                key: page
                for key, page in zip(prefetch_keys, prefetch_pages)
            },
        }
        self._failed_prefetch.discard(current_key)
        self._prune_artifacts()

        active = self._active_job
        if active is not None:
            if active.key == current_key:
                active.adopt_request(request.request_id)
            else:
                self._cancel_active_job()

        artifact = self._artifacts.get(current_key)
        if artifact is not None:
            self._artifacts.move_to_end(current_key)
            self._bump("cache_hits")
            self.frameReady.emit(
                self._frame_from_artifact(
                    request,
                    artifact,
                    cache_hit=True,
                )
            )
        else:
            self._bump("cache_misses")

        self._drive()
        return True

    def release_prefetch(
        self,
        *,
        request_id: int,
        page_index: int,
        image_id: str,
    ) -> bool:
        """Allow nearby work only after the accepted current frame painted."""
        request = self._current_request
        if (
            request is None
            or int(request_id) != request.request_id
            or int(page_index) != request.page_index
            or str(image_id) != request.image_id
            or self._current_key not in self._artifacts
        ):
            return False
        self._prefetch_released_request_id = request.request_id
        self._drive()
        return True

    def cancel(self, *, clear_artifacts: bool = False) -> None:
        self._current_request = None
        self._current_key = None
        self._prefetch_keys = ()
        self._prefetch_released_request_id = None
        self._source_by_key.clear()
        self._page_by_key.clear()
        self._failed_prefetch.clear()
        self._cancel_active_job()
        if clear_artifacts:
            self._artifacts.clear()

    def invalidate_layout(self) -> None:
        self.cancel(clear_artifacts=True)

    def reset_metrics(self) -> None:
        self._metrics = ZipPlaCompatibleRasterMetrics()

    def has_unfinished_tasks(self) -> bool:
        return any(not job.finished.is_set() for job in self._jobs)

    def wait_for_done(self, msecs: int = 5000) -> bool:
        deadline = monotonic() + max(0, int(msecs)) / 1000
        for job in tuple(self._jobs):
            remaining = max(0.0, deadline - monotonic())
            if not job.finished.wait(remaining):
                return False
        return True

    def shutdown(self, *, wait_msecs: int = 5000) -> bool:
        if self._shutdown_complete:
            return True
        self._accepting_requests = False
        self.cancel(clear_artifacts=True)
        if self._coordinator is None:
            self._thread_pool.clear()
        completed = self.wait_for_done(wait_msecs)
        if completed:
            self._shutdown_complete = True
        return completed

    def _drive(self) -> None:
        if (
            not self._accepting_requests
            or self._current_request is None
            or self._current_key is None
            or self._active_job is not None
        ):
            return

        current_key = self._current_key
        if current_key not in self._artifacts:
            self._submit(current_key, ImageWorkPriority.VIEWER_CURRENT)
            return
        if (
            self._prefetch_released_request_id
            != self._current_request.request_id
        ):
            return

        for rank, key in enumerate(self._prefetch_keys):
            if key in self._artifacts or key in self._failed_prefetch:
                continue
            priority = (
                ImageWorkPriority.VIEWER_NEXT
                if rank == 0
                else ImageWorkPriority.VIEWER_PREVIOUS
            )
            self._submit(key, priority)
            return

    def _submit(
        self,
        key: _ArtifactKey,
        priority: ImageWorkPriority,
    ) -> None:
        request = self._current_request
        source = self._source_by_key.get(key)
        if request is None or source is None:
            return
        self._serial += 1
        job = _ZipPlaCompatibleRasterJob(
            serial=self._serial,
            key=key,
            request_id=request.request_id,
            source=source,
        )
        job.signals.completed.connect(
            self._on_job_completed,
            Qt.ConnectionType.QueuedConnection,
        )
        self._active_job = job
        self._jobs.add(job)
        if self._coordinator is not None:
            started = self._coordinator.start_viewer(job, int(priority))
        else:
            self._thread_pool.start(job, int(priority))
            started = True
        if started:
            self._bump("jobs_submitted")
            return

        job.mark_removed_before_start()
        self._jobs.discard(job)
        self._active_job = None
        self._record_start_failure(key)

    def _record_start_failure(self, key: _ArtifactKey) -> None:
        self._bump("job_failures")
        if key != self._current_key or self._current_request is None:
            self._failed_prefetch.add(key)
            self._drive()
            return
        request = self._current_request
        self.frameFailed.emit(
            ZipPlaCompatibleRasterFailure(
                source_epoch=request.source_epoch,
                source_identity=id(request.source),
                request_id=request.request_id,
                page_index=request.page_index,
                image_id=request.image_id,
                error="The image worker is not accepting requests.",
            )
        )

    def _cancel_active_job(self) -> None:
        job = self._active_job
        if job is None:
            return
        if job.cancel():
            self._bump("cancel_requests")
        removed = self._try_take(job)
        if not removed:
            return
        job.mark_removed_before_start()
        self._jobs.discard(job)
        self._active_job = None

    def _try_take(self, job: _ZipPlaCompatibleRasterJob) -> bool:
        if self._coordinator is not None:
            return self._coordinator.try_take_viewer(job)
        try:
            return self._thread_pool.tryTake(job)
        except RuntimeError:
            return False

    @Slot(object)
    def _on_job_completed(self, result: _JobResult) -> None:
        gui_callback_started = monotonic()
        self._bump("queued_callbacks")
        job = self._active_job
        if job is not None and job.serial == result.serial:
            self._active_job = None
        for owned in tuple(self._jobs):
            if owned.serial == result.serial:
                self._jobs.discard(owned)
                break

        decode = result.decode
        if decode is not None:
            self._bump(
                "bytes_read",
                max(0, int(getattr(decode, "bytes_read", 0))),
            )
            self._bump(
                "read_calls",
                max(0, int(getattr(decode, "read_calls", 0))),
            )

        if not self._result_is_current(result):
            self._bump("stale_results")
            self._drive()
            return
        if result.cancelled:
            self._drive()
            return
        if result.error is not None or decode is None:
            self._bump("job_failures")
            if result.key == self._current_key:
                request = self._current_request
                if request is not None:
                    self.frameFailed.emit(
                        ZipPlaCompatibleRasterFailure(
                            source_epoch=request.source_epoch,
                            source_identity=id(request.source),
                            request_id=request.request_id,
                            page_index=request.page_index,
                            image_id=request.image_id,
                            error=(
                                result.error
                                or "JPEG decoder did not produce an image."
                            ),
                        )
                    )
                return
            self._failed_prefetch.add(result.key)
            self._drive()
            return

        pixmap = QPixmap.fromImage(decode.qimage)
        if pixmap.isNull():
            self._bump("job_failures")
            if result.key == self._current_key:
                request = self._current_request
                if request is not None:
                    self.frameFailed.emit(
                        ZipPlaCompatibleRasterFailure(
                            source_epoch=request.source_epoch,
                            source_identity=id(request.source),
                            request_id=request.request_id,
                            page_index=request.page_index,
                            image_id=request.image_id,
                            error="QPixmap creation failed.",
                        )
                    )
                return
            self._failed_prefetch.add(result.key)
            self._drive()
            return

        request = self._current_request
        if request is None:
            self._bump("stale_results")
            return
        pixmap.setDevicePixelRatio(request.device_pixel_ratio)
        self._bump("qpixmap_creations")
        gui_ready_at = monotonic()
        artifact = ZipPlaCompatibleRasterArtifact(
            source_epoch=result.key.source_epoch,
            source_identity=result.key.source_identity,
            page_index=result.key.page_index,
            image_id=result.key.image_id,
            original_size=(
                max(1, int(decode.original_size[0])),
                max(1, int(decode.original_size[1])),
            ),
            pixmap=pixmap,
            maximum_size=result.key.maximum_size,
            device_pixel_ratio=request.device_pixel_ratio,
            worker_completed_at=result.completed_at,
            gui_ready_at=gui_ready_at,
            gui_callback_ms=max(
                0.0,
                (gui_ready_at - gui_callback_started) * 1000,
            ),
            bytes_read=max(0, int(decode.bytes_read)),
            read_calls=max(0, int(decode.read_calls)),
        )
        self._artifacts[result.key] = artifact
        self._artifacts.move_to_end(result.key)
        self._prune_artifacts()
        self.artifactReady.emit(artifact)
        if result.key == self._current_key:
            self.frameReady.emit(
                self._frame_from_artifact(
                    request,
                    artifact,
                    cache_hit=False,
                )
            )
        self._drive()

    def _result_is_current(self, result: _JobResult) -> bool:
        request = self._current_request
        if (
            not self._accepting_requests
            or request is None
            or self._current_key is None
            or result.request_id != request.request_id
            or result.key.source_epoch != request.source_epoch
            or result.key.source_identity != id(request.source)
            or result.key.maximum_size != request.maximum_size
            or result.key.device_pixel_ratio_milli
            != self._dpr_milli(request.device_pixel_ratio)
        ):
            return False
        return result.key in (self._current_key, *self._prefetch_keys)

    def _prune_artifacts(self) -> None:
        desired = set(
            key
            for key in (self._current_key, *self._prefetch_keys)
            if key is not None
        )
        for key in tuple(self._artifacts):
            if key not in desired:
                self._artifacts.pop(key, None)
        while len(self._artifacts) > 3:
            removable = next(
                (
                    key
                    for key in self._artifacts
                    if key != self._current_key
                ),
                None,
            )
            if removable is None:
                break
            self._artifacts.pop(removable, None)

    @staticmethod
    def _normalized_prefetch(
        request: ZipPlaCompatibleRasterRequest,
    ) -> tuple[ZipPlaCompatibleRasterPage, ...]:
        current = (request.page_index, request.image_id)
        seen = {current}
        ordered: list[ZipPlaCompatibleRasterPage] = []
        for page in request.prefetch:
            identity = (page.page_index, page.image_id)
            if identity in seen:
                continue
            seen.add(identity)
            ordered.append(page)
            if len(ordered) == 2:
                break
        return tuple(ordered)

    @classmethod
    def _key_for(
        cls,
        request: ZipPlaCompatibleRasterRequest,
        page: ZipPlaCompatibleRasterPage,
    ) -> _ArtifactKey:
        return _ArtifactKey(
            source_epoch=request.source_epoch,
            source_identity=id(request.source),
            page_index=page.page_index,
            image_id=page.image_id,
            maximum_size=request.maximum_size,
            device_pixel_ratio_milli=cls._dpr_milli(
                request.device_pixel_ratio
            ),
        )

    @staticmethod
    def _dpr_milli(value: float) -> int:
        return max(1, round(float(value) * 1000))

    @staticmethod
    def _frame_from_artifact(
        request: ZipPlaCompatibleRasterRequest,
        artifact: ZipPlaCompatibleRasterArtifact,
        *,
        cache_hit: bool,
    ) -> ZipPlaCompatibleRasterFrame:
        return ZipPlaCompatibleRasterFrame(
            source_epoch=request.source_epoch,
            source_identity=id(request.source),
            request_id=request.request_id,
            page_index=request.page_index,
            image_id=request.image_id,
            original_size=artifact.original_size,
            pixmap=artifact.pixmap,
            maximum_size=request.maximum_size,
            device_pixel_ratio=request.device_pixel_ratio,
            cache_hit=bool(cache_hit),
            worker_completed_at=artifact.worker_completed_at,
            gui_ready_at=artifact.gui_ready_at,
            gui_callback_ms=artifact.gui_callback_ms,
            bytes_read=artifact.bytes_read,
            read_calls=artifact.read_calls,
        )

    def _bump(self, field: str, amount: int = 1) -> None:
        self._metrics = replace(
            self._metrics,
            **{
                field: int(getattr(self._metrics, field)) + int(amount),
            },
        )
