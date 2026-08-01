"""Virtual Viewer page list and visible-thumbnail runtime.

The visible-window work order, one-at-a-time thumbnail lane, and immediate
release of thumbnails outside that window are structural translations of
ZipPlaFork ``source/ZipPla/CatalogForm.cs`` at fixed revision
``07955f5267e2fb92d6fc6e40fde2507d8fb07b3b``.  In particular this follows
``ThumbViewer.PaintPart``, ``ThumbViewerItem.LoadAsync`` / ``Clear``, and the
revision's single-permit thumbnail semaphore.  That source is
AGPL-3.0-or-later (Rio's Toolbox / ZipPlaFork); the complete source, method,
copyright, and license mapping is recorded in
``docs/ZIPPLAFORK_COMPARISON.md``.

Qt model virtualization, source cloning, generation rejection, a bounded
QImage cache, and GUI-only QPixmap upload are NivisViewer adaptations.  The
worker deliberately never creates a QPixmap or QIcon.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import logging
from math import isfinite
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import Iterable, Sequence

from PIL import Image, ImageEnhance, ImageOps
from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QRunnable,
    QSize,
    QThreadPool,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QIcon, QImage, QTransform

from .image_work_coordinator import ImageWorkCoordinator, ImageWorkPriority
from .pdf_backend import PageRenderSpec, PdfRenderPriority
from .thumbnail_render import pil_to_qimage
from .viewer_render import qimage_to_pillow


_LOG = logging.getLogger(__name__)
_DEFAULT_CACHE_BYTES = 32 * 1024 * 1024
_MAX_PHYSICAL_EDGE = 2048


class ViewerPageListModel(QAbstractListModel):
    """Virtual page rows; unfiltered books allocate no per-page row objects."""

    PageIndexRole = int(Qt.ItemDataRole.UserRole) + 1
    ImageIdRole = int(Qt.ItemDataRole.UserRole) + 2

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._book_epoch = -1
        self._image_ids: tuple[str, ...] = ()
        self._filter_text = ""
        self._filtered_pages: tuple[int, ...] | None = None
        self._row_by_page: dict[int, int] | None = None
        self._thumbnails: dict[int, QIcon] = {}

    @property
    def book_epoch(self) -> int:
        return self._book_epoch

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        if self._filtered_pages is None:
            return len(self._image_ids)
        return len(self._filtered_pages)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid() or index.column() != 0:
            return None
        page_index = self.page_index_at(index.row())
        if page_index is None:
            return None
        image_id = self._image_ids[page_index]
        if role == int(Qt.ItemDataRole.DisplayRole):
            # ZipPlaFork's Catalog items are materialized only around paint.
            # Here the equivalent label is generated lazily by Qt's data call.
            name = Path(image_id).name or image_id
            return f"{page_index + 1}: {name}"
        if role == int(Qt.ItemDataRole.DecorationRole):
            icon = self._thumbnails.get(page_index)
            return QIcon(icon) if icon is not None else None
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return image_id
        if role == self.PageIndexRole:
            return page_index
        if role == self.ImageIdRole:
            return image_id
        return None

    def roleNames(self) -> dict[int, bytes]:  # noqa: N802
        roles = dict(super().roleNames())
        roles[self.PageIndexRole] = b"pageIndex"
        roles[self.ImageIdRole] = b"imageId"
        return roles

    def set_book(
        self,
        epoch: int,
        image_ids: Sequence[str],
        filter_text: str = "",
    ) -> None:
        normalized_ids = tuple(str(image_id) for image_id in image_ids)
        normalized_filter = str(filter_text).strip()
        if (
            self._book_epoch == int(epoch)
            and self._image_ids == normalized_ids
        ):
            self.set_filter(normalized_filter)
            return
        self.beginResetModel()
        self._book_epoch = int(epoch)
        self._image_ids = normalized_ids
        self._filter_text = normalized_filter
        self._thumbnails.clear()
        self._rebuild_filter()
        self.endResetModel()

    def set_filter(self, filter_text: str) -> None:
        normalized = str(filter_text).strip()
        if normalized == self._filter_text:
            return
        self.beginResetModel()
        self._filter_text = normalized
        self._rebuild_filter()
        self.endResetModel()

    def clear(self) -> None:
        if self._book_epoch == -1 and not self._image_ids:
            self.clear_thumbnails()
            return
        self.beginResetModel()
        self._book_epoch = -1
        self._image_ids = ()
        self._filter_text = ""
        self._filtered_pages = None
        self._row_by_page = None
        self._thumbnails.clear()
        self.endResetModel()

    def page_index_at(self, row: int) -> int | None:
        normalized = int(row)
        if normalized < 0:
            return None
        if self._filtered_pages is None:
            return normalized if normalized < len(self._image_ids) else None
        if normalized >= len(self._filtered_pages):
            return None
        return self._filtered_pages[normalized]

    def image_id_for_page(self, page_index: int) -> str | None:
        normalized = int(page_index)
        if not 0 <= normalized < len(self._image_ids):
            return None
        return self._image_ids[normalized]

    def row_for_page(self, page_index: int) -> int:
        normalized = int(page_index)
        if not 0 <= normalized < len(self._image_ids):
            return -1
        if self._row_by_page is None:
            return normalized
        return self._row_by_page.get(normalized, -1)

    def set_thumbnail(self, page_index: int, icon: QIcon) -> bool:
        page = int(page_index)
        row = self.row_for_page(page)
        if row < 0 or icon is None or icon.isNull():
            return False
        self._thumbnails[page] = QIcon(icon)
        model_index = self.index(row, 0)
        self.dataChanged.emit(
            model_index,
            model_index,
            [int(Qt.ItemDataRole.DecorationRole)],
        )
        return True

    def retain_thumbnails(self, page_indexes: Iterable[int]) -> None:
        retained = {
            int(page)
            for page in page_indexes
            if 0 <= int(page) < len(self._image_ids)
        }
        removed = tuple(page for page in self._thumbnails if page not in retained)
        for page in removed:
            self._thumbnails.pop(page, None)
        self._emit_thumbnail_changes(removed)

    def clear_thumbnails(self) -> None:
        removed = tuple(self._thumbnails)
        if not removed:
            return
        self._thumbnails.clear()
        self._emit_thumbnail_changes(removed)

    def _rebuild_filter(self) -> None:
        if not self._filter_text:
            # This is the important large-book path: row == page and neither
            # a full index tuple nor a full reverse map is allocated.
            self._filtered_pages = None
            self._row_by_page = None
            return
        needle = self._filter_text.casefold()
        pages = tuple(
            page
            for page, image_id in enumerate(self._image_ids)
            if needle in image_id.casefold() or needle in str(page + 1)
        )
        self._filtered_pages = pages
        self._row_by_page = {page: row for row, page in enumerate(pages)}

    def _emit_thumbnail_changes(self, pages: Iterable[int]) -> None:
        for page in pages:
            row = self.row_for_page(page)
            if row < 0:
                continue
            model_index = self.index(row, 0)
            self.dataChanged.emit(
                model_index,
                model_index,
                [int(Qt.ItemDataRole.DecorationRole)],
            )


@dataclass(frozen=True)
class ViewerPageThumbnailSpec:
    logical_size: int
    device_pixel_ratio: float
    physical_edge: int
    rotation: int
    brightness: float
    contrast: float
    gamma: float

    @property
    def adjustments(self) -> tuple[float, float, float]:
        return self.brightness, self.contrast, self.gamma

    @classmethod
    def create(
        cls,
        logical_size: int | QSize | tuple[int, int],
        dpr: float,
        rotation: int,
        brightness: float,
        contrast: float,
        gamma: float,
    ) -> "ViewerPageThumbnailSpec":
        if isinstance(logical_size, QSize):
            logical_edge = max(logical_size.width(), logical_size.height())
        elif isinstance(logical_size, tuple):
            logical_edge = max(int(logical_size[0]), int(logical_size[1]))
        else:
            logical_edge = int(logical_size)
        logical_edge = max(1, logical_edge)
        dpr = float(dpr)
        if not isfinite(dpr) or dpr <= 0:
            dpr = 1.0
        return cls(
            logical_edge,
            dpr,
            min(_MAX_PHYSICAL_EDGE, max(1, round(logical_edge * dpr))),
            int(rotation) % 360,
            _bounded_factor(brightness, 0.1, 3.0),
            _bounded_factor(contrast, 0.1, 3.0),
            _bounded_factor(gamma, 0.1, 5.0),
        )


@dataclass(frozen=True)
class ViewerPageThumbnail:
    runtime_id: int
    source_epoch: int
    page_index: int
    image_id: str
    spec: ViewerPageThumbnailSpec
    qimage: QImage | None
    cache_hit: bool
    completed_at: float
    error: str | None = None


@dataclass(frozen=True)
class ViewerPageListRuntimeMetrics:
    jobs_submitted: int = 0
    queued_callbacks: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cancel_requests: int = 0
    stale_results: int = 0
    terminal_errors: int = 0
    qimage_creations: int = 0
    qpixmap_uploads: int = 0
    cache_evictions: int = 0


@dataclass(frozen=True)
class _ThumbnailKey:
    source_epoch: int
    page_index: int
    image_id: str
    spec: ViewerPageThumbnailSpec


@dataclass(frozen=True)
class _CachedThumbnail:
    key: _ThumbnailKey
    qimage: QImage
    completed_at: float


@dataclass(frozen=True)
class _ThumbnailJobResult:
    serial: int
    generation: int
    key: _ThumbnailKey
    qimage: QImage | None
    cancelled: bool
    completed_at: float
    error: str | None


class _ThumbnailSourceLease:
    """Lazily own only a distinct thumbnail clone, never the Viewer source."""

    def __init__(self, source) -> None:
        self._base_source = source
        self._lock = Lock()
        self._resolved = False
        self._source = None
        self._owned = False

    def acquire(self):
        with self._lock:
            if self._resolved:
                return self._source
        # Opening a large archive clone may be relatively expensive.  Do it
        # outside the lock so a GUI-thread cancellation never waits for ZIP
        # enumeration.  The runtime has only one active job, so acquisition
        # itself remains single-writer.
        candidate = None
        factory = getattr(self._base_source, "fork_for_thumbnail", None)
        if callable(factory):
            try:
                candidate = factory()
            except Exception as exc:
                # A built-in source advertises this hook precisely so PageList
                # work cannot contend with or cancel the current Viewer
                # session.  Falling back after a failed fork would silently
                # violate that ownership boundary; publish a thumbnail error
                # instead and leave the active book source untouched.
                raise RuntimeError("thumbnail source clone failed") from exc
        if candidate is None:
            candidate = self._base_source
        with self._lock:
            self._source = candidate
            self._owned = candidate is not self._base_source
            self._resolved = True
            return candidate

    def cancel_owned(self, image_id: str) -> None:
        with self._lock:
            source = self._source if self._owned else None
        if source is None:
            return
        cancel = getattr(source, "cancel_image_request", None)
        if callable(cancel):
            try:
                cancel(image_id)
            except Exception:
                pass

    def close_owned(self) -> None:
        with self._lock:
            source = self._source if self._owned else None
            self._source = None
            self._owned = False
            self._resolved = False
        if source is None:
            return
        close = getattr(source, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                _LOG.exception("thumbnail source clone close failed")


class _ThumbnailJobSignals(QObject):
    completed = Signal(object)


class _ViewerPageThumbnailJob(QRunnable):
    def __init__(
        self,
        *,
        serial: int,
        generation: int,
        key: _ThumbnailKey,
        lease: _ThumbnailSourceLease,
        collect_timing: bool,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.serial = int(serial)
        self.key = key
        self.lease = lease
        self.collect_timing = bool(collect_timing)
        self.signals = _ThumbnailJobSignals()
        self.cancelled = Event()
        self.started = Event()
        self.finished = Event()
        self._token_lock = Lock()
        self._generation = int(generation)

    def adopt_generation(self, generation: int) -> None:
        with self._token_lock:
            self._generation = int(generation)

    def cancel(self) -> bool:
        if self.cancelled.is_set():
            return False
        self.cancelled.set()
        # Only a distinct clone may be interrupted.  Shared archive sources
        # can also be serving the current Viewer page.
        self.lease.cancel_owned(self.key.image_id)
        return True

    def mark_removed_before_start(self) -> None:
        self.cancelled.set()
        self.finished.set()

    @Slot()
    def run(self) -> None:
        self.started.set()
        qimage: QImage | None = None
        error: str | None = None
        try:
            if not self.cancelled.is_set():
                source = self.lease.acquire()
                if not self.cancelled.is_set():
                    qimage = self._decode(source)
        except Exception as exc:  # worker boundary
            if not self.cancelled.is_set():
                error = str(exc) or exc.__class__.__name__
        with self._token_lock:
            generation = self._generation
        result = _ThumbnailJobResult(
            self.serial,
            generation,
            self.key,
            qimage,
            self.cancelled.is_set(),
            monotonic() if self.collect_timing else 0.0,
            error,
        )
        # Native/source access is finished, but the runtime keeps this job
        # alive until the queued GUI callback consumes the result.
        self.finished.set()
        self.signals.completed.emit(result)

    def _decode(self, source) -> QImage:
        edge = self.key.spec.physical_edge
        qimage = self._target_qimage(source, edge)
        if qimage is None or qimage.isNull():
            qimage = self._direct_qimage(source)
        if qimage is None or qimage.isNull():
            qimage = self._pdf_qimage(source, edge)
        if qimage is None or qimage.isNull():
            qimage = self._pillow_qimage(source.open_image(self.key.image_id))
        elif self.key.spec.adjustments != (1.0, 1.0, 1.0):
            pillow = qimage_to_pillow(qimage)
            qimage = self._pillow_qimage(pillow, exif_orientation=False)
        if self.cancelled.is_set():
            return QImage()
        if self.key.spec.rotation:
            qimage = qimage.transformed(
                QTransform().rotate(self.key.spec.rotation),
                Qt.TransformationMode.SmoothTransformation,
            )
        qimage = qimage.scaled(
            edge,
            edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if qimage.isNull():
            raise RuntimeError("thumbnail decoder returned an empty image")
        qimage.setDevicePixelRatio(self.key.spec.device_pixel_ratio)
        return qimage

    def _target_qimage(self, source, edge: int) -> QImage | None:
        decoder = getattr(source, "open_qimage_at_most", None)
        if not callable(decoder):
            return None
        decoded = decoder(self.key.image_id, (edge, edge))
        if decoded is None:
            return None
        qimage = decoded[0] if isinstance(decoded, tuple) else decoded
        return QImage(qimage) if isinstance(qimage, QImage) else None

    def _direct_qimage(self, source) -> QImage | None:
        decoder = getattr(source, "open_qimage", None)
        if not callable(decoder):
            return None
        qimage = decoder(self.key.image_id)
        return QImage(qimage) if isinstance(qimage, QImage) else None

    def _pdf_qimage(self, source, edge: int) -> QImage | None:
        render = getattr(source, "open_image_for_render", None)
        logical_size = getattr(source, "logical_size", None)
        if not callable(render) or not callable(logical_size):
            return None
        width, height = logical_size(self.key.image_id)
        target_width, target_height = _fit_size(width, height, edge)
        image = render(
            self.key.image_id,
            PageRenderSpec(
                target_width,
                target_height,
                size_bucket=(target_width, target_height),
                mode="thumbnail",
            ),
            priority=int(PdfRenderPriority.THUMBNAIL_VISIBLE),
            generation=self._generation_token(),
            purpose="thumbnail",
        )
        # The common QImage path below applies adjustments exactly once.
        return self._pillow_qimage(image, apply_adjustments=False)

    def _pillow_qimage(
        self,
        image: Image.Image,
        *,
        exif_orientation: bool = True,
        apply_adjustments: bool = True,
    ) -> QImage:
        owned: list[Image.Image] = [image]
        try:
            oriented = ImageOps.exif_transpose(image) if exif_orientation else image
            owned.append(oriented)
            adjusted = (
                _apply_adjustments(oriented, self.key.spec.adjustments)
                if apply_adjustments
                else oriented
            )
            owned.append(adjusted)
            return pil_to_qimage(adjusted)
        finally:
            seen: set[int] = set()
            for candidate in reversed(owned):
                if id(candidate) in seen:
                    continue
                seen.add(id(candidate))
                candidate.close()

    def _generation_token(self) -> int:
        with self._token_lock:
            return self._generation


class ViewerPageListRuntime(QObject):
    """Book-scoped visible-only thumbnail subsystem."""

    thumbnailReady = Signal(object)
    idle = Signal(object)

    def __init__(
        self,
        source,
        image_ids: Sequence[str],
        source_epoch: int,
        parent: QObject | None = None,
        *,
        image_work_coordinator: ImageWorkCoordinator | None = None,
        cache_byte_budget: int = _DEFAULT_CACHE_BYTES,
        collect_metrics: bool = False,
    ) -> None:
        super().__init__(parent)
        self._runtime_id = id(self)
        self.source = source
        self.source_epoch = int(source_epoch)
        self._image_ids = tuple(str(image_id) for image_id in image_ids)
        self._coordinator = image_work_coordinator
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._lease = _ThumbnailSourceLease(source)
        self._cache_budget = max(1, int(cache_byte_budget))
        self._collect_metrics = bool(collect_metrics)
        self._cache: OrderedDict[_ThumbnailKey, _CachedThumbnail] = OrderedDict()
        self._cache_bytes = 0
        self._desired_pages: tuple[int, ...] = ()
        self._desired_keys: tuple[_ThumbnailKey, ...] = ()
        self._spec: ViewerPageThumbnailSpec | None = None
        self._failed_keys: set[_ThumbnailKey] = set()
        self._completed_keys: set[_ThumbnailKey] = set()
        self._generation = 0
        self._serial = 0
        self._active_job: _ViewerPageThumbnailJob | None = None
        self._jobs: set[_ViewerPageThumbnailJob] = set()
        self._visible = False
        self._manual_paused = False
        self._coordinator_paused = bool(
            image_work_coordinator is not None
            and image_work_coordinator.browser_paused
        )
        self._accepting_requests = True
        self._shutdown_complete = False
        self._release_source_requested = False
        self._was_busy = False
        self._metrics = ViewerPageListRuntimeMetrics()
        if image_work_coordinator is not None:
            image_work_coordinator.browser_pause_changed.connect(
                self._on_browser_pause_changed
            )

    @property
    def runtime_id(self) -> int:
        return self._runtime_id

    @property
    def image_ids(self) -> tuple[str, ...]:
        return self._image_ids

    @property
    def metrics(self) -> ViewerPageListRuntimeMetrics:
        return replace(self._metrics)

    @property
    def cache_bytes(self) -> int:
        return self._cache_bytes

    @property
    def cached_pages(self) -> tuple[int, ...]:
        return tuple(cached.key.page_index for cached in self._cache.values())

    @property
    def desired_pages(self) -> tuple[int, ...]:
        return self._desired_pages

    @property
    def visible(self) -> bool:
        return self._visible

    def set_visible(self, visible: bool) -> None:
        normalized = bool(visible)
        if normalized == self._visible and normalized:
            return
        self._visible = normalized
        if not normalized:
            self._generation += 1
            self._desired_pages = ()
            self._desired_keys = ()
            self._spec = None
            self._failed_keys.clear()
            self._completed_keys.clear()
            self._cancel_active_job()
            self._clear_cache()
            self._request_owned_source_release()
            return
        self._drive()

    def request_visible_pages(
        self,
        order: Iterable[int],
        spec: ViewerPageThumbnailSpec,
    ) -> bool:
        if not self._accepting_requests or not isinstance(
            spec, ViewerPageThumbnailSpec
        ):
            return False
        seen: set[int] = set()
        pages: list[int] = []
        for value in order:
            page = int(value)
            if page in seen or not 0 <= page < len(self._image_ids):
                continue
            seen.add(page)
            pages.append(page)
        normalized_pages = tuple(pages)
        if normalized_pages == self._desired_pages and spec == self._spec:
            self._drive()
            return True

        self._generation += 1
        old_keys = set(self._desired_keys)
        self._desired_pages = normalized_pages
        self._spec = spec
        self._desired_keys = tuple(self._key_for(page, spec) for page in pages)
        desired_set = set(self._desired_keys)
        self._failed_keys.clear()
        self._retain_cache(desired_set)
        self._completed_keys = set(self._cache)

        active = self._active_job
        if active is not None:
            if active.key in desired_set and not active.cancelled.is_set():
                active.adopt_generation(self._generation)
            else:
                self._cancel_active_job()

        for key in self._desired_keys:
            cached = self._cache.get(key)
            if cached is None:
                self._bump("cache_misses")
                continue
            self._cache.move_to_end(key)
            if key not in old_keys:
                self._bump("cache_hits")
                self.thumbnailReady.emit(self._public_thumbnail(cached, True))
        self._drive()
        return True

    def set_paused(self, paused: bool) -> None:
        normalized = bool(paused)
        if normalized == self._manual_paused:
            return
        was_paused = self._is_paused
        self._manual_paused = normalized
        self._apply_pause_transition(was_paused)

    def record_pixmap_upload(self) -> None:
        self._bump("qpixmap_uploads")

    def cancel(self) -> None:
        self._generation += 1
        self._desired_pages = ()
        self._desired_keys = ()
        self._spec = None
        self._failed_keys.clear()
        self._completed_keys.clear()
        self._cancel_active_job()
        self._clear_cache()
        self._request_owned_source_release()

    def has_unfinished_tasks(self) -> bool:
        return bool(self._jobs)

    def wait_for_done(self, msecs: int = 5000) -> bool:
        deadline = monotonic() + max(0, int(msecs)) / 1000
        for job in tuple(self._jobs):
            if not job.finished.wait(max(0.0, deadline - monotonic())):
                return False
        return True

    def shutdown(self, *, wait_msecs: int = 5000) -> bool:
        if self._shutdown_complete:
            return True
        self._accepting_requests = False
        self._visible = False
        self.cancel()
        if self._coordinator is None:
            self._thread_pool.clear()
        workers_done = self.wait_for_done(wait_msecs)
        completed = workers_done and not self._jobs
        if completed:
            self._release_owned_source_if_drained()
            self._disconnect_coordinator()
            self._shutdown_complete = True
        return completed

    @property
    def _is_paused(self) -> bool:
        return self._manual_paused or self._coordinator_paused

    @Slot(bool)
    def _on_browser_pause_changed(self, paused: bool) -> None:
        was_paused = self._is_paused
        self._coordinator_paused = bool(paused)
        self._apply_pause_transition(was_paused)

    def _apply_pause_transition(self, was_paused: bool) -> None:
        if self._is_paused == was_paused:
            return
        if self._is_paused:
            self._generation += 1
            self._cancel_active_job()
            return
        self._drive()

    def _drive(self) -> None:
        # Structural port of CatalogForm's visible item scheduling: only the
        # replaceable visible order is eligible and exactly one item runs.
        if (
            not self._accepting_requests
            or not self._visible
            or self._is_paused
            or self._active_job is not None
        ):
            return
        for key in self._desired_keys:
            if (
                key in self._cache
                or key in self._failed_keys
                or key in self._completed_keys
            ):
                continue
            self._submit(key)
            return

    def _submit(self, key: _ThumbnailKey) -> None:
        self._serial += 1
        job = _ViewerPageThumbnailJob(
            serial=self._serial,
            generation=self._generation,
            key=key,
            lease=self._lease,
            collect_timing=self._collect_metrics,
        )
        job.signals.completed.connect(
            self._on_job_completed,
            Qt.ConnectionType.QueuedConnection,
        )
        self._active_job = job
        self._jobs.add(job)
        self._was_busy = True
        if self._coordinator is not None:
            started = self._coordinator.start_browser(
                job,
                ImageWorkPriority.BROWSER_VISIBLE,
            )
        else:
            self._thread_pool.start(job, int(ImageWorkPriority.BROWSER_VISIBLE))
            started = True
        if started:
            self._bump("jobs_submitted")
            return
        job.mark_removed_before_start()
        self._jobs.discard(job)
        self._active_job = None
        self._emit_idle_if_needed()

    def _cancel_active_job(self) -> None:
        job = self._active_job
        if job is None:
            return
        if job.cancel():
            self._bump("cancel_requests")
        if not self._try_take(job):
            return
        job.mark_removed_before_start()
        self._jobs.discard(job)
        self._active_job = None
        self._release_owned_source_if_drained()
        self._emit_idle_if_needed()

    def _try_take(self, job: _ViewerPageThumbnailJob) -> bool:
        if self._coordinator is not None:
            return self._coordinator.try_take_browser(job)
        try:
            return self._thread_pool.tryTake(job)
        except RuntimeError:
            return False

    @Slot(object)
    def _on_job_completed(self, result: _ThumbnailJobResult) -> None:
        self._bump("queued_callbacks")
        active = self._active_job
        if active is not None and active.serial == result.serial:
            self._active_job = None
        for job in tuple(self._jobs):
            if job.serial == result.serial:
                self._jobs.discard(job)
                break

        if not self._result_is_relevant(result):
            self._bump("stale_results")
        elif result.cancelled:
            pass
        elif result.error is not None or result.qimage is None or result.qimage.isNull():
            self._failed_keys.add(result.key)
            self._bump("terminal_errors")
            self.thumbnailReady.emit(
                ViewerPageThumbnail(
                    self.runtime_id,
                    self.source_epoch,
                    result.key.page_index,
                    result.key.image_id,
                    result.key.spec,
                    None,
                    False,
                    result.completed_at,
                    result.error or "thumbnail decoder returned an empty image",
                )
            )
        else:
            self._bump("qimage_creations")
            self._completed_keys.add(result.key)
            cached = _CachedThumbnail(
                result.key,
                QImage(result.qimage),
                result.completed_at,
            )
            self._put_cache(cached)
            self.thumbnailReady.emit(self._public_thumbnail(cached, False))

        self._release_owned_source_if_drained()
        self._drive()
        self._emit_idle_if_needed()

    def _result_is_relevant(self, result: _ThumbnailJobResult) -> bool:
        return bool(
            self._accepting_requests
            and self._visible
            and not self._is_paused
            and result.generation == self._generation
            and result.key in self._desired_keys
        )

    def _key_for(
        self,
        page_index: int,
        spec: ViewerPageThumbnailSpec,
    ) -> _ThumbnailKey:
        return _ThumbnailKey(
            self.source_epoch,
            page_index,
            self._image_ids[page_index],
            spec,
        )

    def _public_thumbnail(
        self,
        cached: _CachedThumbnail,
        cache_hit: bool,
    ) -> ViewerPageThumbnail:
        return ViewerPageThumbnail(
            self.runtime_id,
            self.source_epoch,
            cached.key.page_index,
            cached.key.image_id,
            cached.key.spec,
            QImage(cached.qimage),
            bool(cache_hit),
            cached.completed_at,
        )

    def _put_cache(self, cached: _CachedThumbnail) -> None:
        self._remove_cache(cached.key)
        self._cache[cached.key] = cached
        self._cache_bytes += _qimage_bytes(cached.qimage)
        evicted = 0
        while self._cache_bytes > self._cache_budget and len(self._cache) > 1:
            victim = next(
                (
                    key
                    for key in reversed(self._desired_keys)
                    if key in self._cache and key != self._desired_keys[0]
                ),
                next(iter(self._cache)),
            )
            self._remove_cache(victim)
            evicted += 1
        if evicted:
            self._bump("cache_evictions", evicted)

    def _retain_cache(self, desired: set[_ThumbnailKey]) -> None:
        removed = 0
        for key in tuple(self._cache):
            if key in desired:
                continue
            self._remove_cache(key)
            removed += 1
        if removed:
            self._bump("cache_evictions", removed)

    def _remove_cache(self, key: _ThumbnailKey) -> None:
        cached = self._cache.pop(key, None)
        if cached is not None:
            self._cache_bytes = max(
                0,
                self._cache_bytes - _qimage_bytes(cached.qimage),
            )

    def _clear_cache(self) -> None:
        self._cache.clear()
        self._cache_bytes = 0

    def _request_owned_source_release(self) -> None:
        self._release_source_requested = True
        self._release_owned_source_if_drained()

    def _release_owned_source_if_drained(self) -> None:
        if not self._release_source_requested or self._jobs:
            return
        self._lease.close_owned()
        self._release_source_requested = False

    def _disconnect_coordinator(self) -> None:
        if self._coordinator is None:
            return
        try:
            self._coordinator.browser_pause_changed.disconnect(
                self._on_browser_pause_changed
            )
        except (RuntimeError, TypeError):
            pass

    def _emit_idle_if_needed(self) -> None:
        if self._jobs or not self._was_busy:
            return
        self._was_busy = False
        if not self._accepting_requests:
            self._release_owned_source_if_drained()
            self._shutdown_complete = True
        self.idle.emit(self)

    def _bump(self, field: str, amount: int = 1) -> None:
        if not self._collect_metrics:
            return
        self._metrics = replace(
            self._metrics,
            **{field: int(getattr(self._metrics, field)) + int(amount)},
        )


def _bounded_factor(value: float, lower: float, upper: float) -> float:
    normalized = float(value)
    if not isfinite(normalized):
        return 1.0
    return min(upper, max(lower, normalized))


def _fit_size(width: int, height: int, edge: int) -> tuple[int, int]:
    width = max(1, int(width))
    height = max(1, int(height))
    scale = min(edge / width, edge / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _apply_adjustments(
    image: Image.Image,
    adjustments: tuple[float, float, float],
) -> Image.Image:
    brightness, contrast, gamma = adjustments
    adjusted = image
    if brightness != 1.0:
        adjusted = ImageEnhance.Brightness(adjusted).enhance(brightness)
    if contrast != 1.0:
        adjusted = ImageEnhance.Contrast(adjusted).enhance(contrast)
    if gamma != 1.0:
        inverse_gamma = 1.0 / gamma
        lut = [
            min(255, max(0, int(((value / 255.0) ** inverse_gamma) * 255 + 0.5)))
            for value in range(256)
        ]
        if adjusted.mode == "RGBA":
            red, green, blue, alpha = adjusted.split()
            adjusted = Image.merge(
                "RGBA",
                (red.point(lut), green.point(lut), blue.point(lut), alpha),
            )
        elif adjusted.mode == "RGB":
            adjusted = Image.merge(
                "RGB",
                tuple(channel.point(lut) for channel in adjusted.split()),
            )
        elif adjusted.mode == "L":
            adjusted = adjusted.point(lut)
        else:
            adjusted = adjusted.convert("RGBA")
    return adjusted


def _qimage_bytes(image: QImage) -> int:
    try:
        return max(0, int(image.sizeInBytes()))
    except (AttributeError, RuntimeError):
        return max(0, int(image.bytesPerLine()) * int(image.height()))


# Compatibility name for callers that describe these counters by artifact.
ViewerPageThumbnailMetrics = ViewerPageListRuntimeMetrics
