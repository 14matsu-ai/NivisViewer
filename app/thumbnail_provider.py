from __future__ import annotations
from .cloud_files import require_local, local_image_candidate

from .i18n import tr


from collections import OrderedDict, deque
from dataclasses import dataclass, replace
import heapq
import inspect
import logging
import os
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import Callable
import zipfile

from natsort import natsorted
from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QImage

from .browser_model import (
    BROWSER_IMAGE_EXTENSIONS,
    BrowserItem,
    BrowserItemKind,
)
from .browser_thumbnail_scheduler import ThumbnailPriority
from .archive_backend import MAX_IMAGE_ENTRY_BYTES
from .gimp_xcf_backend import gimp_cancellation
from .xcf_raster_reader import DeferXcf, xcf_worker_scope
from .file_preview import PreviewResult, PreviewResultKind, PreviewSource
from .image_work_coordinator import ImageWorkCoordinator, ImageWorkPriority
from .image_source import EXTERNAL_ARCHIVE_EXTENSIONS, SUPPORTED_EXTENSIONS, SevenZipImageSource
from .pdf_backend import PageRenderSpec, PdfRenderPriority
from .creative_image_decoder import decode_creative_thumbnail
from .psd_decoder import decode_psd_thumbnail
from .preview_provider_registry import PreviewProviderRegistry
from .supported_formats import (
    CREATIVE_PROJECT_EXTENSIONS,
    PSD_EXTENSIONS,
    XCF_EXTENSIONS,
    xcf_loading_enabled,
)
from .thumbnail_disk_cache import ThumbnailDiskCache, ThumbnailSourceIdentity
from .thumbnail_render import (
    SmartCropCache,
    ThumbnailEncodingPolicy,
    ThumbnailRenderSpec,
    pil_to_qimage,
    render_pil_thumbnail,
    smart_crop_cache_key,
)


_RETIRED_THUMBNAIL_PROVIDERS: set[BrowserThumbnailProvider] = set()
_THUMBNAIL_LOG = logging.getLogger("nivisviewer.thumbnail")
_PAGE_COUNT_REQUEST_TOKEN = -1
_DEFAULT_BROWSER_MEMORY_CACHE_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class ThumbnailLoadResult:
    image: QImage | None
    cover_path: Path | None = None
    provisional_image: QImage | None = None
    entry_path: str = ""
    result_kind: PreviewResultKind | None = None
    preview_source: PreviewSource = PreviewSource.EXISTING
    persist_to_disk: bool = True
    page_count: int | None = None
    disk_cache_hit: bool = False
    source_identity: ThumbnailSourceIdentity | None = None
    cache_in_memory: bool = True

    @property
    def resolved_kind(self) -> PreviewResultKind:
        if self.result_kind is not None:
            return self.result_kind
        if self.image is not None and not self.image.isNull():
            return PreviewResultKind.READY
        return PreviewResultKind.FAILED

    @classmethod
    def from_preview(cls, result: PreviewResult) -> ThumbnailLoadResult:
        return cls(
            result.image,
            provisional_image=result.provisional_image,
            entry_path=result.entry_path,
            result_kind=result.kind,
            preview_source=result.source,
            persist_to_disk=result.persist_to_disk,
            cache_in_memory=result.cache_in_memory,
        )


@dataclass(frozen=True)
class _PendingThumbnail:
    worker: _ThumbnailWorker
    priority: ThumbnailPriority


@dataclass(frozen=True)
class _ThumbnailSaveRequest:
    item: BrowserItem
    size: int | ThumbnailRenderSpec
    image: QImage
    cover_path: Path | None
    entry_path: str
    page_count: int | None
    priority: ThumbnailPriority
    encoding_policy: ThumbnailEncodingPolicy
    source_identity: ThumbnailSourceIdentity
    write_epoch: int
    protected_thumbnail_sizes: frozenset[int]

    @property
    def byte_cost(self) -> int:
        return max(0, int(self.image.sizeInBytes()))


class _ThumbnailSaveDrain(QRunnable):
    def __init__(self, provider: BrowserThumbnailProvider) -> None:
        super().__init__()
        self.provider = provider

    @Slot()
    def run(self) -> None:
        self.provider._drain_thumbnail_saves()


class _ThumbnailWorkerSignals(QObject):
    xcf_deferred = Signal(object)
    finished = Signal(str, int, object, object, object)
    provisional = Signal(str, int, object, object, object)
    page_count_discovered = Signal(str, int, int)


class _ThumbnailWorker(QRunnable):
    def __init__(
        self,
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
        generation: int,
        loader: Callable[..., ThumbnailLoadResult],
        priority: ThumbnailPriority = ThumbnailPriority.VISIBLE,
    ) -> None:
        super().__init__()
        self.item = item
        self.size = size
        self.generation = generation
        self.loader = loader
        self.priority = ThumbnailPriority(priority)
        self.cancelled = Event()
        self.run_started = Event()
        self.page_count_reported = False
        self.xcf_lane = item.path.suffix.lower() in XCF_EXTENSIONS
        self.xcf_handoff_connected = False
        self.signals = _ThumbnailWorkerSignals()

    @Slot()
    def run(self) -> None:
        self.run_started.set()

        def report_provisional(image: QImage) -> None:
            self.signals.provisional.emit(
                str(self.item.path),
                self.generation,
                (
                    self.size.cache_token
                    if isinstance(self.size, ThumbnailRenderSpec)
                    else self.size
                ),
                self.item.thumbnail_revision,
                image,
            )

        def report_page_count(page_count: int) -> None:
            if self.cancelled.is_set():
                return
            self.page_count_reported = True
            self.signals.page_count_discovered.emit(
                str(self.item.path),
                self.generation,
                max(0, int(page_count)),
            )

        try:
            with xcf_worker_scope(defer=not self.xcf_lane):
                result = _invoke_thumbnail_loader(
                    self.loader,
                    self.item,
                    self.size,
                    self.cancelled,
                    self.priority,
                    report_provisional,
                    report_page_count,
                )
        except DeferXcf:
            # An archive/folder cover was discovered to be XCF. Release the
            # ordinary lane before retrying under the same request identity.
            self.signals.xcf_deferred.emit(self)
            return
        except Exception:
            _THUMBNAIL_LOG.exception(
                "thumbnail worker failed path=%s generation=%s",
                self.item.path,
                self.generation,
            )
            result = ThumbnailLoadResult(
                None,
                result_kind=PreviewResultKind.FAILED,
                persist_to_disk=False,
            )
        self.signals.finished.emit(
            str(self.item.path),
            self.generation,
            (
                self.size.cache_token
                if isinstance(self.size, ThumbnailRenderSpec)
                else self.size
            ),
            self.item.thumbnail_revision,
            result,
        )


class BrowserThumbnailProvider(QObject):
    thumbnail_ready = Signal(str, int, object)
    thumbnail_provisional = Signal(str, int, object)
    thumbnail_failed = Signal(str, int, str)
    preview_state_changed = Signal(str, int, str)
    page_count_ready = Signal(str, int, int)
    cache_cleared = Signal()
    scheduling_resumed = Signal()
    capacity_released = Signal()
    submission_rejected = Signal(str, int, object, object)
    work_settled = Signal(str, int, object, object, str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        max_workers: int = 2,
        cache_capacity: int | None = None,
        cache_capacity_bytes: int = _DEFAULT_BROWSER_MEMORY_CACHE_BYTES,
        loader: Callable[[BrowserItem, int], QImage | None] | None = None,
        disk_cache: ThumbnailDiskCache | None = None,
        disk_cache_enabled: bool = True,
        archive_backend_registry=None,
        pdfium_service=None,
        image_work_coordinator: ImageWorkCoordinator | None = None,
        preview_registry: PreviewProviderRegistry | None = None,
        preview_settings: dict[str, object] | None = None,
    ) -> None:
        super().__init__(parent)
        self._coordinator = image_work_coordinator
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._xcf_pool = QThreadPool(self)
        self._xcf_pool.setMaxThreadCount(1)
        self._xcf_pool.setExpiryTimeout(1000)
        self._save_pool = QThreadPool(self)
        self._save_pool.setMaxThreadCount(1)
        # This lane is short-lived between bursts; let idle provider threads
        # retire promptly when Browser windows are opened and closed repeatedly.
        self._save_pool.setExpiryTimeout(1000)
        self._save_lock = Lock()
        self._save_queue: deque[_ThumbnailSaveRequest] = deque()
        self._save_queue_bytes = 0
        self._deferred_save_queue: OrderedDict[
            tuple[object, ...], _ThumbnailSaveRequest
        ] = OrderedDict()
        self._deferred_save_bytes = 0
        self._deferred_save_max_items = 64
        self._deferred_save_max_bytes = 32 * 1024 * 1024
        self._retry_save_queue: OrderedDict[
            tuple[object, ...], _ThumbnailSaveRequest
        ] = OrderedDict()
        self._retry_save_bytes = 0
        self._retry_save_max_items = 64
        self._retry_save_max_bytes = 32 * 1024 * 1024
        self._save_enqueue_backpressured = False
        self._save_drain_active = False
        self._active_save_key: tuple[object, ...] | None = None
        self._save_queue_max_items = 48
        self._save_queue_max_bytes = 24 * 1024 * 1024
        self._maintenance_running = False
        self._maintenance_worker: _ThumbnailWorker | None = None
        self._browser_entry_limit_managed = cache_capacity is None
        self._cache_capacity = max(1, int(128 if cache_capacity is None else cache_capacity))
        self._cache_capacity_bytes = max(1, int(cache_capacity_bytes))
        self._browser_memory_managed = False
        self._browser_memory_mode = "auto"
        self._browser_memory_desired_bytes = self._cache_capacity_bytes
        self._browser_memory_group_capacity_bytes = self._cache_capacity_bytes
        self._browser_memory_reason = "unmanaged"
        self._cache_bytes = 0
        self._cache: OrderedDict[tuple[str, int, tuple[object, ...]], QImage] = OrderedDict()
        self._cache_page_counts: dict[
            tuple[str, int, tuple[object, ...]], int
        ] = {}
        self._cache_specs: dict[int, ThumbnailRenderSpec] = {}
        # Keys in this map are the content identities currently useful to the
        # Browser viewport. Lower rank means more important; old/far entries
        # remain cached but are the first eviction candidates.
        self._cache_retention_rank: dict[
            tuple[str, tuple[object, ...]], int
        ] = {}
        self._cache_entry_priority: dict[
            tuple[str, int, tuple[object, ...]], int
        ] = {}
        # Key-only indexes; QImages remain in the single authoritative cache.
        self._cache_keys_by_path: dict[
            str, OrderedDict[tuple[str, int, tuple[object, ...]], None]
        ] = {}
        self._cache_rank_buckets: dict[
            int | None, OrderedDict[tuple[str, int, tuple[object, ...]], None]
        ] = {}
        self._cache_rank_values: list[int] = []
        self._cache_rank_value_set: set[int] = set()
        self._cache_key_indexed_rank: dict[
            tuple[str, int, tuple[object, ...]], int | None
        ] = {}
        self._active_request_tokens: dict[str, set[int]] = {}
        self._pending: dict[tuple[str, int, int], _PendingThumbnail] = {}
        self._pending_lock = Lock()
        self._failure_lock = Lock()
        self._stats_lock = Lock()
        self._stats: dict[str, int] = {
            "requested_visible": 0,
            "requested_selected": 0,
            "requested_read_ahead": 0,
            "requested_prefetch": 0,
            "requested_background": 0,
            "memory_hit": 0,
            "disk_hit": 0,
            "generated": 0,
            "generated_visible": 0,
            "generated_selected": 0,
            "generated_read_ahead": 0,
            "generated_prefetch": 0,
            "generated_background": 0,
            "disk_saved": 0,
            "disk_saved_visible": 0,
            "disk_saved_selected": 0,
            "disk_saved_read_ahead": 0,
            "disk_saved_background": 0,
            "memory_only": 0,
            "prefetch_skipped": 0,
            "memory_cache_evictions": 0,
            "background_self_evictions": 0,
            "generated_background_nonresident": 0,
            "background_disk_hit": 0,
        }
        self._generated_buckets: dict[str, int] = {}
        self._generated_variants: dict[str, int] = {}
        self._generation = 0
        self._closed = False
        self._decode_loader = loader
        self._loader = self._load_pipeline
        self._disk_cache = disk_cache
        self._disk_cache_enabled = bool(disk_cache_enabled)
        self._disk_cache_health_lock = Lock()
        # None means that the configured route has not been exercised yet.
        # An observed failure blocks unbounded far generation; a later
        # successful write can restore the route if it becomes writable.
        self._disk_cache_write_available: bool | None = (
            None if self._disk_cache_enabled and disk_cache is not None else False
        )
        self._session_start_disk_bytes = (
            disk_cache.usage_bytes()
            if disk_cache_enabled and disk_cache is not None
            else 0
        )
        self._session_disk_baseline_initialized = bool(
            disk_cache_enabled
            and disk_cache is not None
            and disk_cache.enabled
        )
        self._failed: set[tuple[str, int, tuple[object, ...]]] = set()
        self._quiet_results: dict[
            tuple[str, int, tuple[object, ...]],
            PreviewResultKind,
        ] = {}
        self._archive_backend_registry = archive_backend_registry
        self._pdfium_service = pdfium_service
        self._owns_preview_registry = preview_registry is None
        self._preview_registry = (
            preview_registry
            or PreviewProviderRegistry(settings=preview_settings or {})
        )
        self._smart_crop_cache = SmartCropCache()
        self._paused = bool(
            image_work_coordinator is not None
            and image_work_coordinator.browser_paused
        )
        self._fast_scroll_suppressed = False
        if image_work_coordinator is not None:
            image_work_coordinator.browser_pause_changed.connect(self.set_paused)

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def has_failed_requests(self) -> bool:
        with self._failure_lock:
            return bool(self._failed)

    def clear_failed_thumbnail(
        self,
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
        *,
        generation: int,
    ) -> bool:
        """Release one failed identity for an ordinary visible request."""
        if self._closed or int(generation) != self._generation:
            return False
        token = size.cache_token if isinstance(size, ThumbnailRenderSpec) else int(size)
        failure_key = (self._path_key(item.path), token, item.thumbnail_revision)
        with self._failure_lock:
            # Clear only the FAILED memo. Quiet non-applicable/unavailable
            # results have a separate retry policy and stay untouched.
            self._failed.discard(failure_key)
        return True

    def begin_generation(self, *, retry_failed: bool = False) -> int:
        self._generation += 1
        self._active_request_tokens.clear()
        with self._failure_lock:
            self._quiet_results.clear()
        with self._pending_lock:
            had_pending = bool(self._pending)
            for pending in self._pending.values():
                pending.worker.cancelled.set()
                self._try_take(pending.worker)
            self._pending.clear()
        if had_pending:
            self.capacity_released.emit()
        if retry_failed:
            with self._failure_lock:
                self._failed.clear()
        self._cancel_queued_maintenance()
        if self._coordinator is None:
            self._pool.clear()
        return self._generation

    def request(
        self,
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
        *,
        generation: int | None = None,
        priority: ThumbnailPriority = ThumbnailPriority.VISIBLE,
    ) -> bool:
        from .supported_formats import VECTOR_IMAGE_EXTENSIONS, ENABLED_IMAGE_EXTENSIONS
        if item.path.suffix.casefold() in VECTOR_IMAGE_EXTENSIONS - ENABLED_IMAGE_EXTENSIONS:
            return False
        if item.path.suffix.casefold() in XCF_EXTENSIONS and not xcf_loading_enabled():
            return False
        if self._closed:
            return False
        requested_generation = self._generation if generation is None else generation
        if requested_generation != self._generation:
            return False

        normalized_size: int | ThumbnailRenderSpec
        if isinstance(size, ThumbnailRenderSpec):
            normalized_size = size
            cache_token = size.cache_token
        else:
            normalized_size = max(16, min(1024, int(size)))
            cache_token = normalized_size
        path_key = self._path_key(item.path)
        normalized_priority = ThumbnailPriority(priority)
        if normalized_priority is ThumbnailPriority.VISIBLE:
            # Visible image work owns the lane.  Its existing folder/archive
            # listing publishes the same metadata before decode, so obsolete
            # selected-only count work should yield cooperatively.
            self.cancel_page_count_requests_except(
                None,
                generation=requested_generation,
            )
        if (
            self._fast_scroll_suppressed
            and normalized_priority is ThumbnailPriority.VISIBLE
        ):
            normalized_priority = ThumbnailPriority.PREFETCH
        self._increment_stat(
            {
                ThumbnailPriority.VISIBLE: "requested_visible",
                ThumbnailPriority.SELECTED: "requested_selected",
                ThumbnailPriority.READ_AHEAD: "requested_read_ahead",
                ThumbnailPriority.PREFETCH: "requested_prefetch",
                ThumbnailPriority.BACKGROUND: "requested_background",
            }[normalized_priority]
        )
        self._active_request_tokens.setdefault(path_key, set()).add(cache_token)
        cache_key = (path_key, cache_token, item.thumbnail_revision)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._increment_stat("memory_hit")
            self._cache_entry_priority[cache_key] = max(
                int(normalized_priority),
                self._cache_entry_priority.get(cache_key, int(normalized_priority)),
            )
            self._cache.move_to_end(cache_key)
            self._cache_index_touch(cache_key)
            if not self._cache_retention_rank:
                self._reorder_memory_cache()
            image = QImage(cached)
            page_count = self._cache_page_counts.get(cache_key)
            QTimer.singleShot(
                0,
                lambda path=str(item.path), current=requested_generation,
                result=image, count=page_count: self._emit_memory_hit(
                    path,
                    current,
                    result,
                    count,
                ),
            )
            return False

        if isinstance(normalized_size, ThumbnailRenderSpec):
            candidate = self._memory_candidate(
                path_key,
                item.thumbnail_revision,
                normalized_size,
            )
            if candidate is not None:
                candidate_image, candidate_spec, page_count = candidate
                image = QImage(candidate_image)
                if candidate_spec.long_edge >= normalized_size.long_edge * 0.95:
                    QTimer.singleShot(
                        0,
                        lambda path=str(item.path), current=requested_generation,
                        result=image, count=page_count: self._emit_memory_hit(
                            path,
                            current,
                            result,
                            count,
                        ),
                    )
                    return False
                QTimer.singleShot(
                    0,
                    lambda path=str(item.path), current=requested_generation, result=image: (
                        self.thumbnail_provisional.emit(path, current, result)
                        if not self._closed and current == self._generation
                        else None
                    ),
                )

        if self._paused:
            return False

        pending_key = (path_key, cache_token, requested_generation)
        with self._pending_lock:
            existing = self._pending.get(pending_key)
            if existing is not None:
                if (
                    normalized_priority > existing.priority
                    and self._try_take(existing.worker)
                ):
                    self._pending[pending_key] = _PendingThumbnail(
                        existing.worker,
                        normalized_priority,
                    )
                    existing.worker.priority = normalized_priority
                    self._start_worker(existing.worker, normalized_priority)
                return False

            worker = _ThumbnailWorker(
                item,
                normalized_size,
                requested_generation,
                self._loader,
                normalized_priority,
            )
            worker.signals.finished.connect(self._on_finished)
            worker.signals.provisional.connect(self._on_provisional)
            worker.signals.page_count_discovered.connect(
                self._on_page_count_discovered
            )
            self._pending[pending_key] = _PendingThumbnail(
                worker,
                normalized_priority,
            )
        if self._start_worker(worker, normalized_priority):
            return True
        with self._pending_lock:
            removed = self._pending.pop(pending_key, None)
        if removed is not None:
            self.submission_rejected.emit(
                str(item.path), requested_generation, cache_token,
                item.thumbnail_revision,
            )
        return False

    def cancel_page_count_requests_except(
        self,
        keep_path: str | Path | None,
        *,
        generation: int | None = None,
    ) -> int:
        """Cancel obsolete selected metadata work on the existing lane."""

        requested_generation = self._generation if generation is None else generation
        keep_key = (
            None
            if keep_path is None
            else self._path_key(Path(keep_path))
        )
        cancelled = 0
        with self._pending_lock:
            for key, pending in tuple(self._pending.items()):
                if (
                    key[1] != _PAGE_COUNT_REQUEST_TOKEN
                    or key[2] != requested_generation
                    or key[0] == keep_key
                ):
                    continue
                pending.worker.cancelled.set()
                cancelled += 1
                if self._try_take(pending.worker):
                    self._pending.pop(key, None)
        if cancelled:
            self.capacity_released.emit()
        return cancelled

    def request_page_count(
        self,
        item: BrowserItem,
        *,
        generation: int | None = None,
        priority: ThumbnailPriority = ThumbnailPriority.SELECTED,
    ) -> bool:
        """Queue lightweight count metadata on the thumbnail worker authority.

        This deliberately uses the provider's existing bounded pool.  A
        selected-item request stays below visible thumbnails but above
        speculative read-ahead.  It lists direct folder entries or archive
        headers only; it never opens an image payload.
        """

        if self._closed or self._paused:
            return False
        requested_generation = self._generation if generation is None else generation
        if item.online_only or requested_generation != self._generation or item.kind not in {
            BrowserItemKind.FOLDER,
            BrowserItemKind.ARCHIVE,
        }:
            return False
        if item.page_count is not None:
            QTimer.singleShot(
                0,
                lambda path=str(item.path), current=requested_generation,
                count=max(0, int(item.page_count)): (
                    self.page_count_ready.emit(path, current, count)
                    if not self._closed and current == self._generation
                    else None
                ),
            )
            return False

        path_key = self._path_key(item.path)
        normalized_priority = ThumbnailPriority(priority)
        self.cancel_page_count_requests_except(
            item.path,
            generation=requested_generation,
        )
        pending_key = (
            path_key,
            _PAGE_COUNT_REQUEST_TOKEN,
            requested_generation,
        )
        with self._pending_lock:
            # A current-generation thumbnail worker for the same item already
            # performs the required folder/header listing.  Adopt it when it
            # is visible/selected, or promote it if it is still queued at a
            # speculative priority, instead of adding duplicate count work.
            for key, pending in tuple(self._pending.items()):
                if (
                    key[0] != path_key
                    or key[1] == _PAGE_COUNT_REQUEST_TOKEN
                    or key[2] != requested_generation
                    or pending.worker.cancelled.is_set()
                ):
                    continue
                if pending.priority >= normalized_priority:
                    return False
                if self._try_take(pending.worker):
                    pending.worker.priority = normalized_priority
                    self._pending[key] = _PendingThumbnail(
                        pending.worker,
                        normalized_priority,
                    )
                    self._start_worker(
                        pending.worker,
                        normalized_priority,
                    )
                    return False
            existing = self._pending.get(pending_key)
            if existing is not None:
                if (
                    normalized_priority > existing.priority
                    and self._try_take(existing.worker)
                ):
                    existing.worker.priority = normalized_priority
                    self._pending[pending_key] = _PendingThumbnail(
                        existing.worker,
                        normalized_priority,
                    )
                    self._start_worker(
                        existing.worker,
                        normalized_priority,
                    )
                return False
            worker = _ThumbnailWorker(
                item,
                _PAGE_COUNT_REQUEST_TOKEN,
                requested_generation,
                self._load_page_count_pipeline,
                normalized_priority,
            )
            worker.signals.finished.connect(self._on_page_count_finished)
            self._pending[pending_key] = _PendingThumbnail(
                worker,
                normalized_priority,
            )
        if self._start_worker(worker, normalized_priority):
            return True
        with self._pending_lock:
            removed = self._pending.pop(pending_key, None)
        if removed is not None:
            self.submission_rejected.emit(
                str(item.path), requested_generation, _PAGE_COUNT_REQUEST_TOKEN,
                item.thumbnail_revision,
            )
        return False

    def retry(
        self,
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
        *,
        generation: int | None = None,
    ) -> bool:
        token = size.cache_token if isinstance(size, ThumbnailRenderSpec) else int(size)
        failure_key = (self._path_key(item.path), token, item.thumbnail_revision)
        with self._failure_lock:
            self._failed.discard(failure_key)
            self._quiet_results.pop(failure_key, None)
        return self.request(
            item,
            size,
            generation=generation,
            priority=ThumbnailPriority.VISIBLE,
        )

    def request_disk_only(
        self,
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
        *,
        generation: int | None = None,
    ) -> bool:
        """Use an existing disk entry, but do not decode a cache miss."""

        return self.request(
            item,
            size,
            generation=generation,
            priority=ThumbnailPriority.PREFETCH,
        )

    def set_fast_scroll_suppressed(self, suppressed: bool) -> None:
        self._fast_scroll_suppressed = bool(suppressed)

    def cancel_prefetch_except(
        self,
        paths: set[str],
        *,
        size: int | ThumbnailRenderSpec,
        generation: int,
    ) -> int:
        keep = {self._path_key(Path(path)) for path in paths}
        size_token = (
            size.cache_token
            if isinstance(size, ThumbnailRenderSpec)
            else int(size)
        )
        cancelled = 0
        with self._pending_lock:
            candidates = tuple(self._pending.items())
            for key, pending in candidates:
                path_key, pending_size, pending_generation = key
                if (
                    pending_generation != generation
                    or pending_size != size_token
                    or pending.priority is not ThumbnailPriority.PREFETCH
                    or path_key in keep
                ):
                    continue
                if self._try_take(pending.worker):
                    pending.worker.cancelled.set()
                    self._pending.pop(key, None)
                    cancelled += 1
        if cancelled:
            self.capacity_released.emit()
        return cancelled

    def cancel_requests_except(
        self,
        paths: set[str],
        *,
        size: int | ThumbnailRenderSpec,
        generation: int,
    ) -> int:
        """Drop queued work that moved outside the current viewport plan.

        A worker that has already started is allowed to finish; at most the
        bounded worker count can therefore remain outside the new plan.
        """

        keep = {self._path_key(Path(path)) for path in paths}
        size_token = (
            size.cache_token
            if isinstance(size, ThumbnailRenderSpec)
            else int(size)
        )
        cancelled = 0
        with self._pending_lock:
            candidates = tuple(self._pending.items())
            for key, pending in candidates:
                path_key, pending_size, pending_generation = key
                if (
                    pending_generation != generation
                    or pending_size != size_token
                    or path_key in keep
                ):
                    continue
                if self._try_take(pending.worker):
                    pending.worker.cancelled.set()
                    self._pending.pop(key, None)
                    cancelled += 1
        if cancelled:
            self.capacity_released.emit()
        return cancelled

    @Slot(bool)
    def set_paused(self, paused: bool) -> None:
        normalized = bool(paused)
        if normalized == self._paused:
            return
        self._paused = normalized
        if normalized:
            cancelled = 0
            with self._pending_lock:
                for key, pending in tuple(self._pending.items()):
                    if self._try_take(pending.worker):
                        pending.worker.cancelled.set()
                        self._pending.pop(key, None)
                        cancelled += 1
            if cancelled:
                self.capacity_released.emit()
        else:
            self.scheduling_resumed.emit()

    @property
    def pending_count(self) -> int:
        with self._pending_lock:
            return len(self._pending)

    @property
    def shell_preview_pending_count(self) -> int:
        return self._preview_registry.shell_service.pending_count

    def wait_for_done(self, msecs: int = 5000) -> bool:
        deadline = monotonic() + max(0, int(msecs)) / 1000.0
        if self._coordinator is not None:
            decode_done = self._coordinator.wait_for_browser(msecs)
        else:
            decode_done = self._pool.waitForDone(msecs)
        remaining = max(0, int((deadline - monotonic()) * 1000))
        xcf_done = self._xcf_pool.waitForDone(remaining)
        remaining = max(0, int((deadline - monotonic()) * 1000))
        save_done = self._save_pool.waitForDone(remaining)
        return decode_done and xcf_done and save_done

    @property
    def disk_cache(self) -> ThumbnailDiskCache | None:
        return self._disk_cache

    def disk_cache_usage_bytes(self) -> int:
        if not self._disk_cache_enabled or self._disk_cache is None:
            return 0
        return self._disk_cache.usage_bytes()

    @property
    def memory_cache_usage_bytes(self) -> int:
        """Bytes retained by Browser thumbnails, excluding Viewer caches."""
        return self._cache_bytes

    @property
    def memory_cache_bytes(self) -> int:
        """Application broker interface: Browser provider-owned QImage bytes."""
        return self._cache_bytes

    @property
    def memory_cache_limit_bytes(self) -> int:
        return self._cache_capacity_bytes

    @property
    def background_persistence_available(self) -> bool:
        """Whether generated far thumbnails have a usable persistent route."""
        return self.background_disk_cache_enabled

    def configure_browser_memory(
        self, *, mode: str, limit_bytes: int, desired_bytes: int,
        group_capacity_bytes: int, reason: str,
    ) -> None:
        """Apply one broker grant and promptly evict to its byte allowance."""
        self._browser_memory_managed = True
        self._browser_memory_mode = str(mode)
        self._browser_memory_desired_bytes = max(0, int(desired_bytes))
        self._browser_memory_group_capacity_bytes = max(0, int(group_capacity_bytes))
        self._browser_memory_reason = str(reason)
        self._cache_capacity = (
            65536 if self._browser_entry_limit_managed
            else min(self._cache_capacity, 65536)
        )
        self._cache_capacity_bytes = max(0, int(limit_bytes))
        self._rebuild_cache_rank_index()
        self._trim_browser_memory()

    def memory_has_thumbnail(self, item: BrowserItem, size) -> bool:
        """Broker/controller spelling for the existing I/O-free RAM lookup."""
        return self.has_memory_thumbnail(item, size)

    def browser_memory_diagnostics(self) -> dict[str, object]:
        """Return provider-only RAM accounting; performs no cache or disk I/O."""
        return {
            "bytes": int(self._cache_bytes),
            "entries": len(self._cache),
            "limit_bytes": int(self._cache_capacity_bytes),
            "desired_bytes": int(self._browser_memory_desired_bytes),
            "group_capacity_bytes": int(self._browser_memory_group_capacity_bytes),
            "mode": self._browser_memory_mode,
            "reason": self._browser_memory_reason,
            "managed": bool(self._browser_memory_managed),
        }

    def has_memory_thumbnail(
        self,
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
    ) -> bool:
        """Return whether a suitable current-revision image is already in RAM.

        This deliberately performs no filesystem or disk-cache I/O, so the
        Browser workflow can reconcile its near range on the GUI thread.
        """
        token = size.cache_token if isinstance(size, ThumbnailRenderSpec) else int(size)
        path_key = self._path_key(item.path)
        if (path_key, token, item.thumbnail_revision) in self._cache:
            return True
        if not isinstance(size, ThumbnailRenderSpec):
            return False
        candidate = self._memory_candidate(path_key, item.thumbnail_revision, size)
        return bool(
            candidate is not None
            and candidate[1].long_edge >= size.long_edge * 0.95
        )

    @property
    def memory_cache_capacity_reached(self) -> bool:
        """Cheap RAM-only capacity check; safe for GUI scheduling decisions."""
        return (
            len(self._cache) >= self._cache_capacity
            or self._cache_bytes >= self._cache_capacity_bytes
        )

    @property
    def memory_cache_capacity_entries(self) -> int:
        return self._cache_capacity

    @property
    def memory_cache_has_evictable_entries(self) -> bool:
        """Whether old/distant RAM entries can make room for more work."""
        return any(
            self._retention_rank_for_cache_key(key) is None
            or (self._browser_memory_managed
                and self._retention_rank_for_cache_key(key) >= 3)
            for key in self._cache
        )

    @property
    def background_disk_cache_enabled(self) -> bool:
        """Whether disk persistence is configured and has not failed lately.

        This memory-only scheduling hint allows one attempt for an untested
        route. An observed failed save prevents far work from decoding rows
        that cannot be retained.
        """
        with self._disk_cache_health_lock:
            return bool(
                self._disk_cache_enabled
                and self._disk_cache is not None
                and self._disk_cache_write_available is not False
            )

    def _record_disk_cache_write_availability(self, available: bool) -> None:
        with self._disk_cache_health_lock:
            self._disk_cache_write_available = bool(available)

    def _has_persisted_thumbnail(
        self,
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
        cache_token: int,
        entry_path: str | None,
    ) -> bool:
        """Check an equivalent artifact on the worker lane after put() fails."""
        disk_cache = self._disk_cache
        if not self._disk_cache_enabled or disk_cache is None:
            return False
        try:
            if isinstance(size, ThumbnailRenderSpec) and hasattr(
                disk_cache, "get_suitable"
            ):
                try:
                    existing = disk_cache.get_suitable(
                        item, size, entry_path=entry_path
                    )
                except TypeError:
                    existing = disk_cache.get_suitable(item, size)
                return bool(
                    existing is not None
                    and not getattr(existing, "low_resolution_placeholder", False)
                )
            try:
                existing = disk_cache.get(
                    item, cache_token, entry_path=entry_path
                )
            except TypeError:
                existing = disk_cache.get(item, cache_token)
            return existing is not None
        except Exception:
            return False

    def set_cache_retention_priorities(
        self,
        entries: list[tuple[str | Path, tuple[object, ...], int]],
    ) -> None:
        """Recenter RAM retention without deleting reusable cache artifacts.

        The caller supplies only the current/selected/near rows, not the whole
        listing. Eviction order is updated in place so distant cached images
        remain available until memory pressure actually requires their removal.
        """
        priorities: dict[tuple[str, tuple[object, ...]], int] = {}
        for path, revision, rank in entries:
            key = (self._path_key(Path(path)), tuple(revision))
            priorities[key] = min(int(rank), priorities.get(key, int(rank)))
        self._cache_retention_rank = priorities
        if self._browser_memory_managed:
            self._rebuild_cache_rank_index()
            self._trim_browser_memory()
        else:
            self._reorder_memory_cache()

    def set_browser_memory_order(
        self,
        entries: list[tuple[str | Path, tuple[object, ...], int]],
    ) -> None:
        """Set Browser hot-row eviction order using the provider's shared path."""
        self.set_cache_retention_priorities(entries)

    def _trim_browser_memory(self) -> None:
        if not self._browser_memory_managed:
            return
        if (len(self._cache) <= self._cache_capacity
                and self._cache_bytes <= self._cache_capacity_bytes):
            return
        # Unranked entries leave first; then evict oldest entries in the
        # least-valuable rank. Completion-time pressure trims need no full-cache
        # walk, cost dictionary, or sort.
        while (len(self._cache) > self._cache_capacity
               or self._cache_bytes > self._cache_capacity_bytes):
            bucket = self._cache_rank_buckets.get(None)
            if bucket:
                key = next(iter(bucket))
            else:
                while (
                    self._cache_rank_values
                    and -self._cache_rank_values[0] not in self._cache_rank_value_set
                ):
                    heapq.heappop(self._cache_rank_values)
            if not bucket and self._cache_rank_values:
                least_value_rank = -self._cache_rank_values[0]
                bucket = self._cache_rank_buckets.get(least_value_rank)
                if not bucket:
                    self._rebuild_cache_rank_index()
                    if not self._cache_rank_values:
                        break
                    least_value_rank = -self._cache_rank_values[0]
                    bucket = self._cache_rank_buckets.get(least_value_rank)
                if not bucket:
                    break
                key = next(iter(bucket))
            elif not bucket:
                self._rebuild_cache_rank_index()
                bucket = self._cache_rank_buckets.get(None)
                if not bucket:
                    break
                key = next(iter(bucket))
            image = self._cache.pop(key, None)
            self._cache_index_remove(key)
            if image is None:
                continue
            self._cache_bytes -= int(image.sizeInBytes())
            self._cache_page_counts.pop(key, None)
            self._cache_entry_priority.pop(key, None)
            self._increment_stat("memory_cache_evictions")

    def _cache_index_rank_add(
        self, key: tuple[str, int, tuple[object, ...]]
    ) -> None:
        rank = self._retention_rank_for_cache_key(key)
        self._cache_rank_buckets.setdefault(rank, OrderedDict())[key] = None
        self._cache_key_indexed_rank[key] = rank
        if rank is not None and rank not in self._cache_rank_value_set:
            heapq.heappush(self._cache_rank_values, -rank)
            self._cache_rank_value_set.add(rank)
            if len(self._cache_rank_values) > max(
                64, 2 * len(self._cache_rank_value_set)
            ):
                self._cache_rank_values = [
                    -value for value in self._cache_rank_value_set
                ]
                heapq.heapify(self._cache_rank_values)

    def _cache_index_add(
        self, key: tuple[str, int, tuple[object, ...]]
    ) -> None:
        self._cache_keys_by_path.setdefault(key[0], OrderedDict())[key] = None
        if self._browser_memory_managed:
            self._cache_index_rank_add(key)

    def _cache_index_remove(
        self, key: tuple[str, int, tuple[object, ...]]
    ) -> None:
        path_bucket = self._cache_keys_by_path.get(key[0])
        if path_bucket is not None:
            path_bucket.pop(key, None)
            if not path_bucket:
                self._cache_keys_by_path.pop(key[0], None)
        rank = self._cache_key_indexed_rank.pop(key, None)
        rank_bucket = self._cache_rank_buckets.get(rank)
        if rank_bucket is not None:
            rank_bucket.pop(key, None)
            if not rank_bucket:
                self._cache_rank_buckets.pop(rank, None)
                if rank is not None:
                    self._cache_rank_value_set.discard(rank)

    def _cache_index_touch(
        self, key: tuple[str, int, tuple[object, ...]]
    ) -> None:
        path_bucket = self._cache_keys_by_path.get(key[0])
        if path_bucket is not None and key in path_bucket:
            path_bucket.move_to_end(key)
        rank = self._cache_key_indexed_rank.get(key)
        rank_bucket = self._cache_rank_buckets.get(rank)
        if rank_bucket is not None and key in rank_bucket:
            rank_bucket.move_to_end(key)

    def _rebuild_cache_rank_index(self) -> None:
        self._cache_rank_buckets.clear()
        self._cache_rank_values.clear()
        self._cache_rank_value_set.clear()
        self._cache_key_indexed_rank.clear()
        if not self._browser_memory_managed:
            return
        for key in self._cache:
            self._cache_index_rank_add(key)

    def _retention_rank_for_cache_key(
        self,
        key: tuple[str, int, tuple[object, ...]],
    ) -> int | None:
        return self._cache_retention_rank.get((key[0], key[2]))

    def _reorder_memory_cache(self) -> None:
        if len(self._cache) < 2:
            return
        original = list(self._cache.items())
        original_order = {key: index for index, (key, _image) in enumerate(original)}

        def eviction_order(entry):
            key = entry[0]
            rank = self._retention_rank_for_cache_key(key)
            if not self._cache_retention_rank:
                source_priority = self._cache_entry_priority.get(
                    key, int(ThumbnailPriority.BACKGROUND)
                )
                return (0, source_priority, original_order[key])
            if rank is None:
                return (0, 0, original_order[key])
            # The highest numeric rank is least valuable inside the retained
            # window and therefore stays closest to the eviction end.
            return (1, -rank, original_order[key])

        original.sort(key=eviction_order)
        self._cache.clear()
        self._cache.update(original)
        self._cache_keys_by_path.clear()
        for key, _image in original:
            self._cache_keys_by_path.setdefault(key[0], OrderedDict())[key] = None

    def cache_statistics(self) -> dict[str, object]:
        disk_stats: dict[str, object] = {}
        if self._disk_cache_enabled and self._disk_cache is not None:
            disk_stats = self._disk_cache.statistics()
        with self._stats_lock:
            stats: dict[str, object] = dict(self._stats)
            stats["generated_buckets"] = dict(self._generated_buckets)
            stats["generated_variants"] = dict(self._generated_variants)
        stats["memory_cache_usage_bytes"] = self._cache_bytes
        stats["memory_cache_capacity_bytes"] = self._cache_capacity_bytes
        stats["memory_cache_entries"] = len(self._cache)
        stats["memory_cache_capacity_entries"] = self._cache_capacity
        stats["memory_cache_retained_entries"] = sum(
            self._retention_rank_for_cache_key(key) is not None
            for key in self._cache
        )
        usage = int(disk_stats.get("usage_bytes", 0))
        stats.update(disk_stats)
        stats["session_growth_bytes"] = max(
            0,
            usage - self._session_start_disk_bytes,
        )
        return stats

    def set_disk_cache_enabled(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if normalized != self._disk_cache_enabled and self._disk_cache is not None:
            invalidate = getattr(self._disk_cache, "invalidate_pending_writes", None)
            if callable(invalidate):
                invalidate()
        self._disk_cache_enabled = normalized
        with self._disk_cache_health_lock:
            self._disk_cache_write_available = (
                None if self._disk_cache_enabled and self._disk_cache is not None
                else False
            )

    def set_disk_cache_limit_mb(self, limit_mb: int) -> None:
        disk_cache = self._disk_cache
        if disk_cache is None:
            return
        disk_cache.set_limit_mb(limit_mb)
        self.cleanup_caches_async(force=True)

    def set_disk_cache_encoder_quality(self, quality: int) -> None:
        if self._disk_cache is not None:
            self._disk_cache.set_encoder_quality(quality)

    def set_disk_cache_encoding_policy(self, policy: ThumbnailEncodingPolicy) -> None:
        if self._disk_cache is not None:
            self._disk_cache.set_encoding_policy(policy)

    def set_disk_cache_max_unused_days(self, days: int) -> None:
        if self._disk_cache is not None:
            self._disk_cache.set_max_unused_days(days)
            self.cleanup_caches_async(force=True)

    def cleanup_caches_async(self, *, force: bool = True) -> bool:
        disk_cache = self._disk_cache
        if disk_cache is None or not self._disk_cache_enabled or self._closed:
            return True
        if self._maintenance_running:
            return False

        item = BrowserItem(
            display_name="cache-cleanup",
            path=disk_cache.cache_dir,
            kind=BrowserItemKind.FOLDER,
            modified_at=None,
        )
        def cleanup() -> ThumbnailLoadResult:
            protected = tuple(
                (path, token)
                for path, tokens in self._active_request_tokens.items()
                for token in tokens
            )
            disk_cache.touch_source_tokens(protected)
            disk_cache.cleanup_if_due(force=force)
            return ThumbnailLoadResult(None)

        worker = _ThumbnailWorker(
            item,
            16,
            self._generation,
            lambda _item, _size: cleanup(),
        )
        worker.signals.finished.connect(self._on_maintenance_finished)
        self._maintenance_running = True
        self._maintenance_worker = worker
        started = self._start_worker(worker, ThumbnailPriority.PREFETCH)
        if not started:
            if self._maintenance_worker is worker:
                self._maintenance_worker = None
                self._maintenance_running = False
        return started

    def _cancel_queued_maintenance(self) -> bool:
        """Release maintenance state only when its queued worker is removed."""
        worker = self._maintenance_worker
        if worker is None or worker.run_started.is_set():
            return False
        if not self._try_take(worker):
            # It may have started between the event check and tryTake(). Keep
            # the running state until its finished signal arrives.
            return False
        worker.cancelled.set()
        if self._maintenance_worker is worker:
            self._maintenance_worker = None
            self._maintenance_running = False
        return True

    @Slot(str, int, object, object, object)
    def _on_maintenance_finished(
        self,
        _path: str,
        _generation: int,
        _size_token: int,
        _modified_at: object,
        _result: object,
    ) -> None:
        self._maintenance_running = False
        self._maintenance_worker = None
        disk_cache = self._disk_cache
        if (
            self._closed
            or disk_cache is None
            or not self._disk_cache_enabled
            or not disk_cache.maintenance_pending
        ):
            return
        self._continue_cache_maintenance()

    def _continue_cache_maintenance(self) -> None:
        disk_cache = self._disk_cache
        if (
            self._closed
            or disk_cache is None
            or not self._disk_cache_enabled
            or not disk_cache.maintenance_pending
        ):
            return
        if not self.cleanup_caches_async(force=False):
            QTimer.singleShot(500, self, self._continue_cache_maintenance)

    def clear_memory_cache(self) -> None:
        self._cache.clear()
        self._cache_bytes = 0
        self._cache_page_counts.clear()
        self._cache_specs.clear()
        self._cache_entry_priority.clear()
        self._cache_keys_by_path.clear()
        self._cache_rank_buckets.clear()
        self._cache_rank_values.clear()
        self._cache_rank_value_set.clear()
        self._cache_key_indexed_rank.clear()
        self._preview_registry.shell_service.clear_memory_cache()
        with self._failure_lock:
            self._failed.clear()
            self._quiet_results.clear()

    def invalidate_windows_shell_previews(
        self,
        *,
        begin_generation: bool = True,
    ) -> int:
        """Invalidate Shell-backed preview work without touching disk cache."""
        self._preview_registry.shell_service.clear_memory_cache()
        return self.begin_generation() if begin_generation else self._generation

    def update_preview_settings(self, settings: dict[str, object]) -> int:
        if self._disk_cache is not None:
            invalidate = getattr(self._disk_cache, "invalidate_pending_writes", None)
            if callable(invalidate):
                invalidate()
        self._preview_registry.update_settings(settings)
        return self.begin_generation()

    def clear_all_caches_async(self) -> None:
        self.clear_memory_cache()
        disk_cache = self._disk_cache
        if disk_cache is None:
            QTimer.singleShot(0, self.cache_cleared.emit)
            return
        disk_cache.invalidate_pending_writes()
        with self._save_lock:
            self._save_queue.clear()
            self._save_queue_bytes = 0
            self._deferred_save_queue.clear()
            self._deferred_save_bytes = 0
            self._retry_save_queue.clear()
            self._retry_save_bytes = 0

        def clear_disk() -> QImage | None:
            disk_cache.set_enabled(True)
            disk_cache.clear_all()
            return None

        item = BrowserItem(
            display_name="cache-maintenance",
            path=disk_cache.cache_dir,
            kind=BrowserItemKind.FOLDER,
            modified_at=None,
        )
        worker = _ThumbnailWorker(
            item,
            16,
            self._generation,
            lambda _item, _size: ThumbnailLoadResult(clear_disk()),
        )
        worker.signals.finished.connect(self._on_cache_clear_finished)
        self._start_worker(worker, ThumbnailPriority.PREFETCH)

    def close(self, wait_msecs: int = 250) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        with self._save_lock:
            self._deferred_save_queue.clear()
            self._deferred_save_bytes = 0
            self._retry_save_queue.clear()
            self._retry_save_bytes = 0
        with self._pending_lock:
            for pending in self._pending.values():
                pending.worker.cancelled.set()
                self._try_take(pending.worker)
        self._cancel_queued_maintenance()
        if self._coordinator is None:
            self._pool.clear()
        if self.wait_for_done(wait_msecs):
            self._finalize_close()
            return
        self.setParent(None)
        _RETIRED_THUMBNAIL_PROVIDERS.add(self)
        QTimer.singleShot(100, self, self._release_retired_if_idle)

    @staticmethod
    def load_thumbnail(
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
    ) -> QImage | None:
        return BrowserThumbnailProvider.load_thumbnail_result(item, size).image

    @staticmethod
    def load_thumbnail_result(
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
        archive_backend_registry=None,
        cancel_token=None,
        pdfium_service=None,
        pdf_render_priority: int = int(PdfRenderPriority.THUMBNAIL_VISIBLE),
        smart_crop_cache: SmartCropCache | None = None,
        page_count_callback: Callable[[int], None] | None = None,
        capture_source_identity: bool = False,
    ) -> ThumbnailLoadResult:
        from .vector_image_decoder import vector_context
        with vector_context(pdfium_service, cancel_token, pdf_render_priority):
            return BrowserThumbnailProvider._load_thumbnail_result_bound(
                item, size, archive_backend_registry, cancel_token, pdfium_service,
                pdf_render_priority, smart_crop_cache, page_count_callback,
                capture_source_identity,
            )

    @staticmethod
    def _load_thumbnail_result_bound(
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
        archive_backend_registry=None,
        cancel_token=None,
        pdfium_service=None,
        pdf_render_priority: int = int(PdfRenderPriority.THUMBNAIL_VISIBLE),
        smart_crop_cache: SmartCropCache | None = None,
        page_count_callback: Callable[[int], None] | None = None,
        capture_source_identity: bool = False,
    ) -> ThumbnailLoadResult:
        spec = (
            size
            if isinstance(size, ThumbnailRenderSpec)
            else ThumbnailRenderSpec.from_settings(size, "square_1_1", "letterbox")
        )
        try:
            require_local(item.path)
            if item.kind == BrowserItemKind.IMAGE:
                return ThumbnailLoadResult(
                    BrowserThumbnailProvider._load_image_path(
                        item.path,
                        spec,
                        smart_crop_cache,
                    )
                )
            if item.kind == BrowserItemKind.FOLDER:
                return BrowserThumbnailProvider._load_folder_result(
                    item.path,
                    spec,
                    smart_crop_cache,
                    page_count_callback,
                    cancel_token,
                    source_item=item if capture_source_identity else None,
                )
            if item.kind == BrowserItemKind.ARCHIVE:
                if item.path.suffix.lower() in EXTERNAL_ARCHIVE_EXTENSIONS:
                    return BrowserThumbnailProvider._load_external_archive_result(
                        item.path,
                        spec,
                        archive_backend_registry,
                        cancel_token,
                        smart_crop_cache,
                        page_count_callback,
                    )
                return BrowserThumbnailProvider._load_archive_result(
                    item.path,
                    spec,
                    smart_crop_cache,
                    page_count_callback,
                    cancel_token,
                )
            if item.kind == BrowserItemKind.PDF and pdfium_service is not None:
                from .pdf_image_source import PdfImageSource

                source = PdfImageSource(
                    item.path,
                    pdfium_service=pdfium_service,
                    cancel_token=cancel_token,
                )
                try:
                    image_id = source.list_images()[0]
                    with source.open_image_for_render(
                        image_id,
                        PageRenderSpec(spec.long_edge, spec.long_edge),
                        priority=int(pdf_render_priority),
                        purpose="thumbnail",
                    ) as image:
                        rendered, _crop = BrowserThumbnailProvider._render_image(
                            image,
                            spec,
                            smart_crop_cache,
                            smart_crop_cache_key(
                                item.path,
                                source_size=item.file_size,
                                source_mtime_ns=(
                                    int(item.modified_at * 1_000_000_000)
                                    if item.modified_at is not None
                                    else None
                                ),
                                entry_path="0",
                                ratio_id=spec.frame_ratio_id,
                            ),
                        )
                        return ThumbnailLoadResult(rendered, entry_path="0")
                finally:
                    source.close()
        except IndexError:
            return ThumbnailLoadResult(
                None,
                result_kind=PreviewResultKind.NO_CONTENT,
                persist_to_disk=False,
            )
        except Exception:
            cancelled = bool(
                cancel_token is not None
                and getattr(cancel_token, "is_set", lambda: False)()
            )
            return ThumbnailLoadResult(
                None,
                result_kind=(
                    PreviewResultKind.CANCELLED
                    if cancelled
                    else PreviewResultKind.FAILED
                ),
                persist_to_disk=False,
            )
        return ThumbnailLoadResult(
            None,
            result_kind=PreviewResultKind.UNAVAILABLE,
            persist_to_disk=False,
        )

    def _load_pipeline(
        self,
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
        cancel_token=None,
        thumbnail_priority: ThumbnailPriority = ThumbnailPriority.VISIBLE,
        provisional_callback: Callable[[QImage], None] | None = None,
        page_count_callback: Callable[[int], None] | None = None,
    ) -> ThumbnailLoadResult:
        cache_token = size.cache_token if isinstance(size, ThumbnailRenderSpec) else int(size)
        failure_key = (self._path_key(item.path), cache_token, item.thumbnail_revision)
        disk_cache = self._disk_cache
        disk_entry_path = (
            self._preview_registry.disk_cache_variant(item)
            if item.kind is BrowserItemKind.OTHER
            else None
        )

        try:
            require_local(item.path)
        except OSError:
            cached = None
            if self._disk_cache_enabled and disk_cache is not None:
                get_saved = getattr(disk_cache, 'get_offline_preview', None)
                if callable(get_saved):
                    cached = get_saved(item)
            with self._failure_lock:
                self._quiet_results[failure_key] = PreviewResultKind.UNAVAILABLE
            return ThumbnailLoadResult(
                cached, disk_cache_hit=cached is not None,
                result_kind=PreviewResultKind.UNAVAILABLE if cached is None else None,
                persist_to_disk=False,
            )

        def publish_page_count(page_count: int) -> None:
            if cancel_token is not None and cancel_token.is_set():
                return
            normalized = max(0, int(page_count))
            if self._disk_cache_enabled and disk_cache is not None:
                disk_cache.update_page_count(item, normalized)
            if page_count_callback is not None:
                page_count_callback(normalized)

        provisional: QImage | None = None
        disk_cache_available = bool(self._disk_cache_enabled and disk_cache is not None)
        if disk_cache_available:
            try:
                disk_cache.set_enabled(True)
                disk_cache_available = bool(getattr(disk_cache, "enabled", True))
            except Exception:
                disk_cache_available = False
            if not disk_cache_available:
                self._record_disk_cache_write_availability(False)
        if disk_cache_available:
            with self._stats_lock:
                if not self._session_disk_baseline_initialized:
                    self._session_start_disk_bytes = disk_cache.usage_bytes()
                    self._session_disk_baseline_initialized = True
            # Validation below is per requested item. Whole-cache maintenance
            # belongs to Browser's idle cleanup, never the first visible read.
            if isinstance(size, ThumbnailRenderSpec) and hasattr(
                disk_cache, "get_suitable"
            ):
                try:
                    cached_result = disk_cache.get_suitable(
                        item,
                        size,
                        entry_path=disk_entry_path,
                    )
                except TypeError:
                    cached_result = disk_cache.get_suitable(item, size)
                if cached_result is not None:
                    self._increment_stat("disk_hit")
                    if ThumbnailPriority(thumbnail_priority) is ThumbnailPriority.BACKGROUND:
                        self._increment_stat("background_disk_hit")
                    if (
                        cached_result.page_count is not None
                        and page_count_callback is not None
                    ):
                        page_count_callback(cached_result.page_count)
                    if not cached_result.low_resolution_placeholder:
                        return ThumbnailLoadResult(
                            cached_result.image,
                            page_count=cached_result.page_count,
                            disk_cache_hit=True,
                        )
                    provisional = cached_result.image
                    if provisional_callback is not None:
                        provisional_callback(provisional.copy())
                        provisional = None
            else:
                try:
                    cached = disk_cache.get(
                        item,
                        cache_token,
                        entry_path=disk_entry_path,
                    )
                except TypeError:
                    cached = disk_cache.get(item, cache_token)
                if cached is not None:
                    self._increment_stat("disk_hit")
                    if ThumbnailPriority(thumbnail_priority) is ThumbnailPriority.BACKGROUND:
                        self._increment_stat("background_disk_hit")
                    return ThumbnailLoadResult(cached, disk_cache_hit=True)

        try:
            require_local(item.path)
        except OSError:
            # Terminal for this generation; refreshing the folder permits retry.
            with self._failure_lock:
                self._quiet_results[failure_key] = PreviewResultKind.UNAVAILABLE
            return ThumbnailLoadResult(
                provisional, result_kind=PreviewResultKind.UNAVAILABLE,
                persist_to_disk=False,
            )

        normalized_priority = ThumbnailPriority(thumbnail_priority)
        if (
            item.kind is BrowserItemKind.IMAGE
            and item.path.suffix.casefold() in XCF_EXTENSIONS
            and normalized_priority in {
                ThumbnailPriority.READ_AHEAD,
                ThumbnailPriority.PREFETCH,
                ThumbnailPriority.BACKGROUND,
            }
        ):
            # XCF has no cheap saved composite. Avoid speculative full-layer
            # flattening; visible/selected requests may still render it.
            self._increment_stat("prefetch_skipped")
            return ThumbnailLoadResult(
                None,
                provisional_image=provisional,
                result_kind=PreviewResultKind.NOT_APPLICABLE,
                persist_to_disk=False,
            )
        if (
            normalized_priority is ThumbnailPriority.PREFETCH
            and item.kind in {
                BrowserItemKind.FOLDER,
                BrowserItemKind.IMAGE,
                BrowserItemKind.ARCHIVE,
            }
        ) or (
            normalized_priority is ThumbnailPriority.READ_AHEAD
            and item.kind is not BrowserItemKind.IMAGE
        ):
            self._increment_stat("prefetch_skipped")
            return ThumbnailLoadResult(
                None,
                provisional_image=provisional,
                result_kind=PreviewResultKind.NOT_APPLICABLE,
                persist_to_disk=False,
            )

        with self._failure_lock:
            quiet_kind = self._quiet_results.get(failure_key)
            if quiet_kind is not None:
                return ThumbnailLoadResult(
                    None,
                    provisional_image=provisional,
                    result_kind=quiet_kind,
                    persist_to_disk=False,
                )
            if failure_key in self._failed:
                return ThumbnailLoadResult(
                    None,
                    provisional_image=provisional,
                    result_kind=PreviewResultKind.FAILED,
                    persist_to_disk=False,
                )

        capture_save_identity = bool(
            disk_cache_available
            and ThumbnailPriority(thumbnail_priority)
            is not ThumbnailPriority.PREFETCH
        )
        if self._decode_loader is None:
            source_identity = (
                ThumbnailSourceIdentity.capture(item)
                if capture_save_identity
                else None
            )
            if item.kind is BrowserItemKind.OTHER:
                result = ThumbnailLoadResult.from_preview(
                    self._preview_registry.generate(
                        item,
                        (
                            size
                            if isinstance(size, ThumbnailRenderSpec)
                            else ThumbnailRenderSpec.from_settings(
                                size,
                                "square_1_1",
                                "letterbox",
                            )
                        ),
                        priority=ThumbnailPriority(thumbnail_priority),
                        cancel_token=cancel_token,
                    )
                )
            else:
                result = self.load_thumbnail_result(
                    item,
                    size,
                    self._archive_backend_registry,
                    cancel_token,
                    self._pdfium_service,
                    self._pdf_render_priority(thumbnail_priority),
                    self._smart_crop_cache,
                    publish_page_count,
                    capture_source_identity=capture_save_identity,
                )
        else:
            source_identity = (
                ThumbnailSourceIdentity.capture(item)
                if capture_save_identity
                else None
            )
            loaded = _invoke_thumbnail_loader(
                self._decode_loader,
                item,
                size.long_edge if isinstance(size, ThumbnailRenderSpec) else size,
                cancel_token,
            )
            result = (
                loaded
                if isinstance(loaded, ThumbnailLoadResult)
                else ThumbnailLoadResult(loaded)
            )
        if result.source_identity is None and source_identity is not None:
            result = replace(result, source_identity=source_identity)
        if cancel_token is not None and cancel_token.is_set():
            return ThumbnailLoadResult(
                None, result_kind=PreviewResultKind.CANCELLED, persist_to_disk=False
            )
        if result.image is None or result.image.isNull():
            if result.resolved_kind is PreviewResultKind.FAILED:
                with self._failure_lock:
                    if cancel_token is None or not cancel_token.is_set():
                        self._failed.add(failure_key)
            elif result.resolved_kind not in {
                PreviewResultKind.CANCELLED,
                PreviewResultKind.PENDING,
            } and not (
                normalized_priority is ThumbnailPriority.PREFETCH
                and result.resolved_kind is PreviewResultKind.UNAVAILABLE
            ):
                with self._failure_lock:
                    if cancel_token is None or not cancel_token.is_set():
                        self._quiet_results[failure_key] = result.resolved_kind
            return ThumbnailLoadResult(
                None,
                provisional_image=(
                    provisional
                    if provisional is not None
                    else result.provisional_image
                ),
                entry_path=result.entry_path,
                result_kind=result.resolved_kind,
                preview_source=result.preview_source,
                persist_to_disk=False,
                page_count=result.page_count,
                cache_in_memory=result.cache_in_memory,
            )

        self._increment_stat("generated")
        normalized_priority = ThumbnailPriority(thumbnail_priority)
        self._increment_stat(
            {
                ThumbnailPriority.VISIBLE: "generated_visible",
                ThumbnailPriority.SELECTED: "generated_selected",
                ThumbnailPriority.READ_AHEAD: "generated_read_ahead",
                ThumbnailPriority.PREFETCH: "generated_prefetch",
                ThumbnailPriority.BACKGROUND: "generated_background",
            }[normalized_priority]
        )
        bucket = (
            f"{size.frame_width}x{size.frame_height}"
            if isinstance(size, ThumbnailRenderSpec)
            else str(cache_token)
        )
        with self._stats_lock:
            self._generated_buckets[bucket] = (
                self._generated_buckets.get(bucket, 0) + 1
            )
            if isinstance(size, ThumbnailRenderSpec):
                variant = (
                    f"{size.frame_ratio_id}/{size.crop_mode}/"
                    f"{size.browser_display_mode}/{size.encoder_format}"
                )
                self._generated_variants[variant] = (
                    self._generated_variants.get(variant, 0) + 1
                )

        if (
            ThumbnailPriority(thumbnail_priority)
            is not ThumbnailPriority.PREFETCH
            and result.persist_to_disk
            and disk_cache_available
            and disk_cache is not None
        ):
            # Publish the same matte-composited pixels that we save. Otherwise
            # a fresh transparent preview and its later opaque disk hit differ.
            policy = size.encoding_policy if isinstance(size, ThumbnailRenderSpec) else getattr(disk_cache, "encoding_policy", ThumbnailEncodingPolicy())
            if not policy.preserve_alpha and result.image.hasAlphaChannel():
                result = replace(result, image=pil_to_qimage(
                    policy.prepare_pixels(ThumbnailDiskCache._qimage_to_pil(result.image))
                ))
        else:
            self._increment_stat("memory_only")
        return ThumbnailLoadResult(
            result.image,
            result.cover_path,
            provisional_image=(
                provisional if provisional is not None else result.provisional_image
            ),
            entry_path=result.entry_path,
            result_kind=result.resolved_kind,
            preview_source=result.preview_source,
            persist_to_disk=result.persist_to_disk,
            page_count=result.page_count,
            disk_cache_hit=result.disk_cache_hit,
            source_identity=result.source_identity,
            cache_in_memory=result.cache_in_memory,
        )

    @Slot(str, int, object, object, object)
    def _on_provisional(
        self,
        path: str,
        generation: int,
        _size_token: int,
        _modified_at: tuple[object, ...],
        image: QImage | None,
    ) -> None:
        with self._pending_lock:
            pending = self._pending.get((self._path_key(Path(path)), int(_size_token), generation))
        if pending is not None and pending.priority is ThumbnailPriority.BACKGROUND:
            return
        if (
            self._closed
            or generation != self._generation
            or image is None
            or image.isNull()
        ):
            return
        self.thumbnail_provisional.emit(path, generation, image)

    @Slot(str, int, int)
    def _on_page_count_discovered(
        self,
        path: str,
        generation: int,
        page_count: int,
    ) -> None:
        if self._closed or generation != self._generation:
            return
        path_key = self._path_key(Path(path))
        self._cancel_page_count_request(path_key, generation)
        self.page_count_ready.emit(
            path,
            generation,
            max(0, int(page_count)),
        )

    def _load_page_count_pipeline(
        self,
        item: BrowserItem,
        _size: int,
        cancel_token=None,
        _thumbnail_priority: ThumbnailPriority = ThumbnailPriority.PREFETCH,
    ) -> ThumbnailLoadResult:
        try:
            require_local(item.path)
        except OSError:
            return ThumbnailLoadResult(None, result_kind=PreviewResultKind.UNAVAILABLE,
                                       persist_to_disk=False)
        disk_cache = self._disk_cache
        if self._disk_cache_enabled and disk_cache is not None:
            disk_cache.set_enabled(True)
            cached = disk_cache.get_page_count(item)
            # A quality change alone must not force source enumeration. Full
            # cache maintenance is scheduled separately after Browser work.
            if cached is not None:
                return ThumbnailLoadResult(
                    None,
                    result_kind=PreviewResultKind.NOT_APPLICABLE,
                    persist_to_disk=False,
                    page_count=cached,
                )
        try:
            if self._is_cancelled(cancel_token):
                raise InterruptedError
            require_local(item.path)
            if item.kind is BrowserItemKind.FOLDER:
                page_count = len(
                    self._folder_image_candidates(item.path, cancel_token)
                )
            elif item.path.suffix.lower() in EXTERNAL_ARCHIVE_EXTENSIONS:
                if self._archive_backend_registry is None:
                    return ThumbnailLoadResult(
                        None,
                        result_kind=PreviewResultKind.UNAVAILABLE,
                        persist_to_disk=False,
                    )
                backend = self._archive_backend_registry.backend_for_path(
                    item.path
                )
                if backend is None:
                    return ThumbnailLoadResult(
                        None,
                        result_kind=PreviewResultKind.UNAVAILABLE,
                        persist_to_disk=False,
                    )
                source = SevenZipImageSource(
                    item.path,
                    backend=backend,
                    cancel_token=cancel_token,
                )
                try:
                    page_count = len(source.list_images())
                finally:
                    source.close()
            else:
                with zipfile.ZipFile(item.path, "r") as source:
                    page_count = len(
                        self._zip_image_names(source, cancel_token)
                    )
            if disk_cache is not None and self._disk_cache_enabled:
                disk_cache.update_page_count(item, page_count)
            return ThumbnailLoadResult(
                None,
                result_kind=PreviewResultKind.NOT_APPLICABLE,
                persist_to_disk=False,
                page_count=page_count,
            )
        except InterruptedError:
            return ThumbnailLoadResult(
                None,
                result_kind=PreviewResultKind.CANCELLED,
                persist_to_disk=False,
            )
        except (OSError, zipfile.BadZipFile):
            return ThumbnailLoadResult(
                None,
                result_kind=PreviewResultKind.FAILED,
                persist_to_disk=False,
            )

    def _protected_thumbnail_sizes(self, item: BrowserItem) -> set[int]:
        path_key = self._path_key(item.path)
        protected = set(self._active_request_tokens.get(path_key, set()))
        with self._pending_lock:
            protected.update(
                token
                for cached_path, token, _generation in self._pending
                if cached_path == path_key
            )
        return protected

    @staticmethod
    def _pdf_render_priority(priority: ThumbnailPriority) -> int:
        return {
            ThumbnailPriority.VISIBLE: int(PdfRenderPriority.THUMBNAIL_VISIBLE),
            ThumbnailPriority.SELECTED: int(PdfRenderPriority.THUMBNAIL_SELECTED),
            ThumbnailPriority.READ_AHEAD: int(PdfRenderPriority.THUMBNAIL_PREFETCH),
            ThumbnailPriority.PREFETCH: int(PdfRenderPriority.THUMBNAIL_PREFETCH),
                ThumbnailPriority.BACKGROUND: int(PdfRenderPriority.THUMBNAIL_PREFETCH),
        }[ThumbnailPriority(priority)]

    @Slot(str, int, object, object, object)
    def _consume_thumbnail_finished(
        self,
        path: str,
        generation: int,
        size_token: int,
        modified_at: tuple[object, ...],
        result: ThumbnailLoadResult | QImage | None,
    ) -> None:
        path_key = self._path_key(Path(path))
        pending_key = (path_key, int(size_token), generation)
        cache_key = (path_key, int(size_token), modified_at)
        with self._pending_lock:
            pending = self._pending.pop(pending_key, None)
        if self._closed:
            self._release_retired_if_idle()
            return
        if generation != self._generation:
            return
        loaded = (
            result
            if isinstance(result, ThumbnailLoadResult)
            else ThumbnailLoadResult(result)
        )
        provisional = loaded.provisional_image
        if (provisional is not None and not provisional.isNull()
                and (pending is None or pending.priority is not ThumbnailPriority.BACKGROUND)):
            self.thumbnail_provisional.emit(path, generation, provisional)
        if (
            loaded.page_count is not None
            and (
                pending is None
                or not pending.worker.page_count_reported
            )
        ):
            self._cancel_page_count_request(path_key, generation)
            self.page_count_ready.emit(
                path,
                generation,
                max(0, int(loaded.page_count)),
            )
        kind = loaded.resolved_kind
        self.preview_state_changed.emit(path, generation, kind.value)
        image = loaded.image
        if image is None or image.isNull():
            if (
                pending is not None
                and pending.priority >= ThumbnailPriority.SELECTED
                and kind is PreviewResultKind.FAILED
            ):
                self.thumbnail_failed.emit(
                    path,
                    generation,
                    tr('サムネイルを生成できませんでした'),
                )
            return
        cached_image = QImage(image)
        existing_image = self._cache.pop(cache_key, None)
        if existing_image is not None:
            self._cache_bytes -= int(existing_image.sizeInBytes())
            self._cache_index_remove(cache_key)
        self._cache_entry_priority.pop(cache_key, None)
        self._cache_page_counts.pop(cache_key, None)
        image_bytes = int(cached_image.sizeInBytes())
        if loaded.cache_in_memory and image_bytes <= self._cache_capacity_bytes:
            self._cache[cache_key] = cached_image
            self._cache_bytes += image_bytes
            self._cache_index_add(cache_key)
            self._cache_entry_priority[cache_key] = int(
                pending.priority if pending is not None else ThumbnailPriority.VISIBLE
            )
            if loaded.page_count is not None:
                self._cache_page_counts[cache_key] = max(
                    0,
                    int(loaded.page_count),
                )
        if pending is not None and isinstance(
            pending.worker.size, ThumbnailRenderSpec
        ):
            self._cache_specs[int(size_token)] = pending.worker.size
        if cache_key in self._cache:
            # Keep this as the newest LRU item. The trim path consults its
            # current viewport rank and preserves LRU order within each rank.
            self._cache.move_to_end(cache_key, last=True)
            self._cache_index_touch(cache_key)
            if not self._browser_memory_managed:
                self._reorder_memory_cache()
        if self._browser_memory_managed:
            self._trim_browser_memory()
            if (cache_key not in self._cache and pending is not None
                    and pending.priority is ThumbnailPriority.BACKGROUND):
                self._increment_stat("background_self_evictions")
        else:
            while (
                len(self._cache) > self._cache_capacity
                or self._cache_bytes > self._cache_capacity_bytes
            ):
                evicted_key, _evicted_image = self._cache.popitem(last=False)
                self._cache_index_remove(evicted_key)
                self._cache_bytes -= int(_evicted_image.sizeInBytes())
                self._cache_page_counts.pop(evicted_key, None)
                self._cache_entry_priority.pop(evicted_key, None)
                self._increment_stat("memory_cache_evictions")
                if (
                    evicted_key == cache_key
                    and pending is not None
                    and pending.priority is ThumbnailPriority.BACKGROUND
                ):
                    self._increment_stat("background_self_evictions")
        if (
            pending is not None
            and pending.priority is ThumbnailPriority.BACKGROUND
            and not loaded.disk_cache_hit
            and cache_key not in self._cache
        ):
            self._increment_stat("generated_background_nonresident")
        if pending is None or pending.priority is not ThumbnailPriority.BACKGROUND:
            self.thumbnail_ready.emit(path, generation, image)

    @Slot(str, int, object, object, object)
    def _on_page_count_finished(
        self,
        path: str,
        generation: int,
        _size_token: int,
        _modified_at: tuple[object, ...],
        result: ThumbnailLoadResult | QImage | None,
    ) -> None:
        path_key = self._path_key(Path(path))
        pending_key = (
            path_key,
            _PAGE_COUNT_REQUEST_TOKEN,
            generation,
        )
        with self._pending_lock:
            pending = self._pending.pop(pending_key, None)
        if pending is not None:
            self.capacity_released.emit()
        if self._closed:
            self._release_retired_if_idle()
            return
        if generation != self._generation:
            return
        if pending is None or pending.worker.cancelled.is_set():
            return
        loaded = (
            result
            if isinstance(result, ThumbnailLoadResult)
            else ThumbnailLoadResult(result)
        )
        if loaded.page_count is not None:
            self.page_count_ready.emit(
                path,
                generation,
                max(0, int(loaded.page_count)),
            )

    def _cancel_page_count_request(
        self,
        path_key: str,
        generation: int,
    ) -> bool:
        pending_key = (
            path_key,
            _PAGE_COUNT_REQUEST_TOKEN,
            generation,
        )
        with self._pending_lock:
            pending = self._pending.get(pending_key)
            if pending is None:
                return False
            if self._try_take(pending.worker):
                pending.worker.cancelled.set()
                self._pending.pop(pending_key, None)
                cancelled = True
            else:
                pending.worker.cancelled.set()
                cancelled = False
        if cancelled:
            self.capacity_released.emit()
        return cancelled

    def _increment_stat(self, key: str, amount: int = 1) -> None:
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + int(amount)

    def _queue_thumbnail_save(
        self,
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
        result: ThumbnailLoadResult,
        priority: ThumbnailPriority,
    ) -> bool:
        disk_cache = self._disk_cache
        image = result.image
        if (
            self._closed
            or not self._disk_cache_enabled
            or disk_cache is None
            or image is None
            or image.isNull()
            or not result.persist_to_disk
            or result.disk_cache_hit
            or result.source_identity is None
            or priority is ThumbnailPriority.PREFETCH
        ):
            return False
        with self._save_lock:
            self._save_enqueue_backpressured = False
        policy = (
            size.encoding_policy
            if isinstance(size, ThumbnailRenderSpec)
            else getattr(disk_cache, "encoding_policy", ThumbnailEncodingPolicy())
        )
        write_epoch = int(getattr(disk_cache, "write_epoch", 0))
        queued = _ThumbnailSaveRequest(
            item=item,
            size=size,
            image=QImage(image),
            cover_path=result.cover_path,
            entry_path=result.entry_path,
            page_count=result.page_count,
            priority=priority,
            encoding_policy=policy,
            source_identity=result.source_identity,
            write_epoch=write_epoch,
            protected_thumbnail_sizes=frozenset(
                self._protected_thumbnail_sizes(item)
            ),
        )
        byte_cost = queued.byte_cost
        save_key = self._thumbnail_save_key(queued)
        start_drain = False
        with self._save_lock:
            if self._active_save_key == save_key:
                return True
            for index, existing in enumerate(self._save_queue):
                if self._thumbnail_save_key(existing) != save_key:
                    continue
                if queued.priority > existing.priority:
                    replacement_cost = queued.byte_cost
                    byte_delta = replacement_cost - existing.byte_cost
                    if self._save_queue_bytes + byte_delta <= self._save_queue_max_bytes:
                        self._save_queue[index] = queued
                        self._save_queue_bytes += byte_delta
                return True
            if self._closed:
                return False
            self._remove_deferred_save_locked(save_key)
            self._remove_retry_save_locked(save_key)
            if (
                len(self._save_queue) + int(self._save_drain_active)
                >= self._save_queue_max_items
                or self._save_queue_bytes + byte_cost > self._save_queue_max_bytes
            ):
                self._save_enqueue_backpressured = True
                previous = self._deferred_save_queue.pop(save_key, None)
                if previous is not None:
                    self._deferred_save_bytes = max(
                        0, self._deferred_save_bytes - previous.byte_cost
                    )
                if (
                    len(self._deferred_save_queue)
                    < self._deferred_save_max_items
                    and self._deferred_save_bytes + byte_cost
                    <= self._deferred_save_max_bytes
                ):
                    self._deferred_save_queue[save_key] = queued
                    self._deferred_save_bytes += byte_cost
                else:
                    previous = self._retry_save_queue.pop(save_key, None)
                    if previous is not None:
                        self._retry_save_bytes = max(
                            0, self._retry_save_bytes - previous.byte_cost
                        )
                    if (
                        len(self._retry_save_queue) < self._retry_save_max_items
                        and self._retry_save_bytes + byte_cost
                        <= self._retry_save_max_bytes
                    ):
                        self._retry_save_queue[save_key] = queued
                        self._retry_save_bytes += byte_cost
                    # Do not add another unbounded persistence tier.  The
                    # completed RAM entry is evicted so the owning workflow
                    # remains backpressured and will regenerate/re-save it
                    # after capacity_released instead of reporting a RAM hit
                    # as settled.
                    self._evict_cache_entry_for_save(item, size)
                return False
            self._save_queue.append(queued)
            self._save_queue_bytes += byte_cost
            if not self._save_drain_active:
                self._save_drain_active = True
                start_drain = True
        if start_drain:
            self._save_pool.start(_ThumbnailSaveDrain(self))
        return True

    @staticmethod
    def _thumbnail_save_key(
        request: _ThumbnailSaveRequest,
    ) -> tuple[object, ...]:
        token = (
            request.size.cache_token
            if isinstance(request.size, ThumbnailRenderSpec)
            else int(request.size)
        )
        return (
            request.source_identity,
            token,
            request.entry_path,
            request.encoding_policy,
            request.write_epoch,
        )

    def _drain_thumbnail_saves(self) -> None:
        pending_was_released = False
        while True:
            release_capacity = False
            request = None
            with self._save_lock:
                if not self._save_queue:
                    had_pending = bool(
                        self._deferred_save_queue
                        or self._retry_save_queue
                    )
                    had_backpressure = self._save_enqueue_backpressured
                    pending_was_released = pending_was_released or had_pending
                    self._save_drain_active = False
                    self._promote_retry_saves_locked()
                    self._promote_deferred_saves_locked()
                    if self._save_queue:
                        self._save_drain_active = True
                        continue
                    self._active_save_key = None
                    self._save_enqueue_backpressured = False
                    release_capacity = had_pending or had_backpressure
                else:
                    request = self._save_queue.popleft()
                    self._active_save_key = self._thumbnail_save_key(request)
            if request is None:
                if release_capacity or pending_was_released:
                    self.capacity_released.emit()
                return
            disk_cache = self._disk_cache
            saved = False
            persisted = False
            if (
                disk_cache is not None
                and self._disk_cache_enabled
                and request.write_epoch == int(getattr(disk_cache, "write_epoch", 0))
            ):
                try:
                    saved = disk_cache.put(
                        request.item,
                        request.size,
                        request.image,
                        cover_path=request.cover_path,
                        entry_path=request.entry_path,
                        page_count=request.page_count,
                        protected_thumbnail_sizes=set(
                            request.protected_thumbnail_sizes
                        ),
                        encoding_policy=request.encoding_policy,
                        expected_write_epoch=request.write_epoch,
                        expected_source_identity=request.source_identity,
                    )
                    persisted = bool(saved) or self._has_persisted_thumbnail(
                        request.item,
                        request.size,
                        (
                            request.size.cache_token
                            if isinstance(request.size, ThumbnailRenderSpec)
                            else int(request.size)
                        ),
                        (
                            self._preview_registry.disk_cache_variant(request.item)
                            if request.item.kind is BrowserItemKind.OTHER
                            else None
                        ),
                    )
                except Exception:
                    saved = False
                    persisted = False
                outcome = str(getattr(disk_cache, "last_put_status", "io_error"))
                if persisted or outcome == "already_present":
                    self._record_disk_cache_write_availability(True)
                elif outcome in {"io_error", "disabled"}:
                    # Source revision/cancellation is an expected refusal,
                    # not evidence that the persistence route is unhealthy.
                    self._record_disk_cache_write_availability(False)
            if saved:
                self._increment_stat("disk_saved")
                self._increment_stat(
                    {
                        ThumbnailPriority.VISIBLE: "disk_saved_visible",
                        ThumbnailPriority.SELECTED: "disk_saved_selected",
                        ThumbnailPriority.READ_AHEAD: "disk_saved_read_ahead",
                        ThumbnailPriority.BACKGROUND: "disk_saved_background",
                    }.get(request.priority, "disk_saved")
                )
            elif not persisted:
                self._increment_stat("memory_only")
            with self._save_lock:
                self._save_queue_bytes = max(
                    0,
                    self._save_queue_bytes - request.byte_cost,
                )
                if self._active_save_key == self._thumbnail_save_key(request):
                    self._active_save_key = None
                self._promote_deferred_saves_locked()
                self._promote_retry_saves_locked()
            if disk_cache is not None and getattr(
                disk_cache, "maintenance_pending", False
            ):
                QTimer.singleShot(0, self, self._continue_cache_maintenance)

    def _promote_deferred_saves_locked(self) -> None:
        while self._deferred_save_queue:
            if (
                len(self._save_queue) + int(self._save_drain_active)
                >= self._save_queue_max_items
                or self._save_queue_bytes >= self._save_queue_max_bytes
            ):
                return
            save_key, request = self._deferred_save_queue.popitem(last=False)
            self._deferred_save_bytes = max(
                0, self._deferred_save_bytes - request.byte_cost
            )
            if self._active_save_key == save_key:
                continue
            if any(
                self._thumbnail_save_key(existing) == save_key
                for existing in self._save_queue
            ):
                continue
            if self._save_queue_bytes + request.byte_cost > self._save_queue_max_bytes:
                self._deferred_save_queue[save_key] = request
                self._deferred_save_bytes += request.byte_cost
                return
            self._save_queue.append(request)
            self._save_queue_bytes += request.byte_cost

    def _promote_retry_saves_locked(self) -> None:
        while self._retry_save_queue:
            if (
                len(self._deferred_save_queue) >= self._deferred_save_max_items
                or self._deferred_save_bytes
                >= self._deferred_save_max_bytes
            ):
                return
            save_key, request = next(iter(self._retry_save_queue.items()))
            if (
                self._deferred_save_bytes + request.byte_cost
                > self._deferred_save_max_bytes
            ):
                return
            self._retry_save_queue.pop(save_key, None)
            self._retry_save_bytes = max(
                0, self._retry_save_bytes - request.byte_cost
            )
            if self._active_save_key == save_key:
                continue
            previous = self._deferred_save_queue.pop(save_key, None)
            if previous is not None:
                self._deferred_save_bytes = max(
                    0, self._deferred_save_bytes - previous.byte_cost
                )
            self._deferred_save_queue[save_key] = request
            self._deferred_save_bytes += request.byte_cost

    def _remove_deferred_save_locked(self, save_key) -> None:
        previous = self._deferred_save_queue.pop(save_key, None)
        if previous is not None:
            self._deferred_save_bytes = max(
                0, self._deferred_save_bytes - previous.byte_cost
            )

    def _remove_retry_save_locked(self, save_key) -> None:
        previous = self._retry_save_queue.pop(save_key, None)
        if previous is not None:
            self._retry_save_bytes = max(
                0, self._retry_save_bytes - previous.byte_cost
            )

    def _evict_cache_entry_for_save(
        self,
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
    ) -> None:
        token = size.cache_token if isinstance(size, ThumbnailRenderSpec) else int(size)
        cache_key = (self._path_key(item.path), token, item.thumbnail_revision)
        image = self._cache.pop(cache_key, None)
        if image is None:
            return
        self._cache_bytes = max(0, self._cache_bytes - int(image.sizeInBytes()))
        self._cache_index_remove(cache_key)
        self._cache_page_counts.pop(cache_key, None)
        self._cache_entry_priority.pop(cache_key, None)

    def _memory_candidate(
        self,
        path_key: str,
        modified_at: tuple[object, ...],
        requested: ThumbnailRenderSpec,
    ) -> tuple[QImage, ThumbnailRenderSpec, int | None] | None:
        candidates: list[
            tuple[QImage, ThumbnailRenderSpec, int | None]
        ] = []
        for cache_key in self._cache_keys_by_path.get(path_key, ()):
            image = self._cache.get(cache_key)
            if image is None:
                continue
            cached_path, token, cached_modified = cache_key
            if cached_modified != modified_at:
                continue
            spec = self._cache_specs.get(token)
            if spec is None or spec.family_token != requested.family_token:
                continue
            candidates.append(
                (image, spec, self._cache_page_counts.get(cache_key))
            )
        if not candidates:
            return None
        adequate = [
            candidate
            for candidate in candidates
            if candidate[1].long_edge >= requested.long_edge * 0.95
        ]
        if adequate:
            return min(adequate, key=lambda candidate: candidate[1].long_edge)
        return max(candidates, key=lambda candidate: candidate[1].long_edge)

    def _emit_memory_hit(
        self,
        path: str,
        generation: int,
        image: QImage,
        page_count: int | None,
    ) -> None:
        if self._closed or generation != self._generation:
            return
        if page_count is not None:
            self.page_count_ready.emit(path, generation, page_count)
        self.thumbnail_ready.emit(path, generation, image)

    @Slot(str, int, object, object, object)
    def _on_cache_clear_finished(
        self,
        _path: str,
        _generation: int,
        _size: int,
        _modified_at: object,
        _image: object,
    ) -> None:
        self.cache_cleared.emit()

    @staticmethod
    def _load_image_path(
        path: Path,
        spec: ThumbnailRenderSpec,
        smart_crop_cache: SmartCropCache | None = None,
    ) -> QImage | None:
        try:
            require_local(path)
            stat = path.stat()
            if path.suffix.casefold() in {'.svg', '.ai'}:
                from .vector_image_decoder import vector_pil
                image_context = vector_pil(path, path.suffix.casefold(), (spec.long_edge, spec.long_edge))
            elif path.suffix.casefold() in PSD_EXTENSIONS:
                image_context = decode_psd_thumbnail(
                    path,
                    minimum_long_edge=spec.long_edge,
                )
            elif path.suffix.casefold() in CREATIVE_PROJECT_EXTENSIONS:
                image_context = decode_creative_thumbnail(
                    path,
                    suffix=path.suffix.casefold(),
                    minimum_long_edge=spec.long_edge,
                )
            else:
                image_context = Image.open(path)

            with image_context as image:
                rendered, _crop = BrowserThumbnailProvider._render_image(
                    image,
                    spec,
                    smart_crop_cache,
                    smart_crop_cache_key(
                        path,
                        source_size=stat.st_size,
                        source_mtime_ns=stat.st_mtime_ns,
                        ratio_id=spec.frame_ratio_id,
                    ),
                )
                return rendered
        except Exception:
            return None

    @staticmethod
    def _load_folder(
        folder: Path,
        size: int | ThumbnailRenderSpec,
    ) -> QImage | None:
        spec = (
            size
            if isinstance(size, ThumbnailRenderSpec)
            else ThumbnailRenderSpec.from_settings(size, "square_1_1", "letterbox")
        )
        return BrowserThumbnailProvider._load_folder_result(folder, spec).image

    @staticmethod
    def _folder_image_candidates(
        folder: Path,
        cancel_token=None,
    ) -> list[Path]:
        candidates: list[Path] = []
        require_local(folder)
        with os.scandir(folder) as entries:
            for entry in entries:
                if BrowserThumbnailProvider._is_cancelled(cancel_token):
                    raise InterruptedError
                if (
                    Path(entry.name).suffix.lower()
                    not in BROWSER_IMAGE_EXTENSIONS
                ):
                    continue
                try:
                    if local_image_candidate(entry.path) and entry.is_file(follow_symlinks=True):
                        candidates.append(Path(entry.path))
                except OSError:
                    continue
        return natsorted(candidates, key=lambda path: path.name)

    @staticmethod
    def _zip_image_names(
        source: zipfile.ZipFile,
        cancel_token=None,
    ) -> list[str]:
        names: list[str] = []
        for info in source.infolist():
            if BrowserThumbnailProvider._is_cancelled(cancel_token):
                raise InterruptedError
            if (
                not info.is_dir()
                and Path(info.filename).suffix.lower()
                in BROWSER_IMAGE_EXTENSIONS
            ):
                names.append(info.filename)
        return natsorted(names)

    @staticmethod
    def _load_folder_result(
        folder: Path,
        spec: ThumbnailRenderSpec,
        smart_crop_cache: SmartCropCache | None = None,
        page_count_callback: Callable[[int], None] | None = None,
        cancel_token=None,
        source_item: BrowserItem | None = None,
    ) -> ThumbnailLoadResult:
        try:
            candidates = BrowserThumbnailProvider._folder_image_candidates(
                folder,
                cancel_token,
            )
        except InterruptedError:
            return ThumbnailLoadResult(
                None,
                result_kind=PreviewResultKind.CANCELLED,
                persist_to_disk=False,
            )
        except OSError:
            return ThumbnailLoadResult(
                None,
                result_kind=PreviewResultKind.FAILED,
                persist_to_disk=False,
            )
        if page_count_callback is not None:
            page_count_callback(len(candidates))
        for path in candidates:
            if BrowserThumbnailProvider._is_cancelled(cancel_token):
                return ThumbnailLoadResult(
                    None,
                    result_kind=PreviewResultKind.CANCELLED,
                    persist_to_disk=False,
                    page_count=len(candidates),
                )
            source_identity = (
                ThumbnailSourceIdentity.capture(source_item, cover_path=path)
                if source_item is not None
                else None
            )
            image = BrowserThumbnailProvider._load_image_path(
                path,
                spec,
                smart_crop_cache,
            )
            if image is not None:
                return ThumbnailLoadResult(
                    image,
                    path,
                    page_count=len(candidates),
                    source_identity=source_identity,
                )
        return ThumbnailLoadResult(
            None,
            result_kind=PreviewResultKind.NO_CONTENT,
            persist_to_disk=False,
            page_count=len(candidates),
        )

    @staticmethod
    def _load_archive(
        archive: Path,
        spec: ThumbnailRenderSpec,
        smart_crop_cache: SmartCropCache | None = None,
    ) -> QImage | None:
        return BrowserThumbnailProvider._load_archive_result(
            archive,
            spec,
            smart_crop_cache,
        ).image

    @staticmethod
    def _load_archive_result(
        archive: Path,
        spec: ThumbnailRenderSpec,
        smart_crop_cache: SmartCropCache | None = None,
        page_count_callback: Callable[[int], None] | None = None,
        cancel_token=None,
    ) -> ThumbnailLoadResult:
        try:
            require_local(archive)
            stat = archive.stat()
            with zipfile.ZipFile(archive, "r") as source:
                names = BrowserThumbnailProvider._zip_image_names(
                    source,
                    cancel_token,
                )
                if page_count_callback is not None:
                    page_count_callback(len(names))
                for name in names:
                    if BrowserThumbnailProvider._is_cancelled(cancel_token):
                        raise InterruptedError
                    try:
                        with source.open(name, "r") as file:
                            if Path(name).suffix.casefold() in {'.svg', '.ai'}:
                                from .vector_image_decoder import vector_pil, MAX_SVG_BYTES, MAX_INPUT_BYTES
                                suffix = Path(name).suffix.casefold()
                                limit = MAX_SVG_BYTES if suffix == '.svg' else MAX_INPUT_BYTES
                                if source.getinfo(name).file_size > limit:
                                    continue
                                image_context = vector_pil(file.read(limit + 1), suffix, (spec.long_edge, spec.long_edge))
                            elif Path(name).suffix.casefold() in PSD_EXTENSIONS:
                                if source.getinfo(name).file_size > MAX_IMAGE_ENTRY_BYTES:
                                    continue
                                image_context = decode_psd_thumbnail(
                                    file.read(),
                                    minimum_long_edge=spec.long_edge,
                                )
                            elif (
                                Path(name).suffix.casefold()
                                in CREATIVE_PROJECT_EXTENSIONS
                            ):
                                if source.getinfo(name).file_size > MAX_IMAGE_ENTRY_BYTES:
                                    continue
                                suffix = Path(name).suffix.casefold()
                                image_context = decode_creative_thumbnail(
                                    file.read(),
                                    suffix=suffix,
                                    minimum_long_edge=spec.long_edge,
                                )
                            else:
                                image_context = Image.open(file)

                            with image_context as image:
                                if BrowserThumbnailProvider._is_cancelled(
                                    cancel_token
                                ):
                                    raise InterruptedError
                                qimage, _crop = (
                                    BrowserThumbnailProvider._render_image(
                                        image,
                                        spec,
                                        smart_crop_cache,
                                        smart_crop_cache_key(
                                            archive,
                                            source_size=stat.st_size,
                                            source_mtime_ns=stat.st_mtime_ns,
                                            entry_path=name,
                                            ratio_id=spec.frame_ratio_id,
                                        ),
                                    )
                                )
                        if BrowserThumbnailProvider._is_cancelled(cancel_token):
                            raise InterruptedError
                        if qimage is not None:
                            return ThumbnailLoadResult(
                                qimage,
                                entry_path=name,
                                page_count=len(names),
                            )
                    except InterruptedError:
                        raise
                    except Exception:
                        continue
        except InterruptedError:
            return ThumbnailLoadResult(
                None,
                result_kind=PreviewResultKind.CANCELLED,
                persist_to_disk=False,
            )
        except (OSError, zipfile.BadZipFile):
            return ThumbnailLoadResult(
                None,
                result_kind=PreviewResultKind.FAILED,
                persist_to_disk=False,
            )
        return ThumbnailLoadResult(
            None,
            result_kind=PreviewResultKind.NO_CONTENT,
            persist_to_disk=False,
            page_count=len(names),
        )

    @staticmethod
    def _load_external_archive(
        archive: Path,
        spec: ThumbnailRenderSpec,
        archive_backend_registry,
        cancel_token,
        smart_crop_cache: SmartCropCache | None = None,
    ) -> QImage | None:
        return BrowserThumbnailProvider._load_external_archive_result(
            archive,
            spec,
            archive_backend_registry,
            cancel_token,
            smart_crop_cache,
        ).image

    @staticmethod
    def _load_external_archive_result(
        archive: Path,
        spec: ThumbnailRenderSpec,
        archive_backend_registry,
        cancel_token,
        smart_crop_cache: SmartCropCache | None = None,
        page_count_callback: Callable[[int], None] | None = None,
    ) -> ThumbnailLoadResult:
        if archive_backend_registry is None:
            return ThumbnailLoadResult(
                None,
                result_kind=PreviewResultKind.UNAVAILABLE,
                persist_to_disk=False,
            )
        backend = archive_backend_registry.backend_for_path(archive)
        if backend is None:
            return ThumbnailLoadResult(
                None,
                result_kind=PreviewResultKind.UNAVAILABLE,
                persist_to_disk=False,
            )
        source: SevenZipImageSource | None = None
        try:
            source = SevenZipImageSource(
                archive,
                backend=backend,
                cancel_token=cancel_token,
            )
            images = source.list_images()
            if page_count_callback is not None:
                page_count_callback(len(images))
            first = images[0]
            with source.open_thumbnail_image(first, minimum_long_edge=spec.long_edge) as image:
                stat = archive.stat()
                rendered, _crop = BrowserThumbnailProvider._render_image(
                    image,
                    spec,
                    smart_crop_cache,
                    smart_crop_cache_key(
                        archive,
                        source_size=stat.st_size,
                        source_mtime_ns=stat.st_mtime_ns,
                        entry_path=first,
                        ratio_id=spec.frame_ratio_id,
                    ),
                )
                return ThumbnailLoadResult(
                    rendered,
                    entry_path=first,
                    page_count=len(images),
                )
        except IndexError:
            return ThumbnailLoadResult(
                None,
                result_kind=PreviewResultKind.NO_CONTENT,
                persist_to_disk=False,
                page_count=0,
            )
        except Exception:
            cancelled = bool(
                cancel_token is not None
                and getattr(cancel_token, "is_set", lambda: False)()
            )
            return ThumbnailLoadResult(
                None,
                result_kind=(
                    PreviewResultKind.CANCELLED
                    if cancelled
                    else PreviewResultKind.FAILED
                ),
                persist_to_disk=False,
            )
        finally:
            if source is not None:
                source.close()

    @staticmethod
    def _pil_to_qimage(image: Image.Image, size: int) -> QImage:
        prepared = image.copy()
        prepared.thumbnail((size, size), Image.Resampling.LANCZOS)
        return pil_to_qimage(prepared)

    @staticmethod
    def _render_image(
        image: Image.Image,
        spec: ThumbnailRenderSpec,
        smart_crop_cache: SmartCropCache | None,
        crop_key: tuple[object, ...],
    ) -> tuple[QImage, tuple[float, float, float, float] | None]:
        source_size = image.size
        cached_crop = (
            smart_crop_cache.get(crop_key)
            if spec.crop_mode == "smart_crop" and smart_crop_cache is not None
            else None
        )
        rendered, crop = render_pil_thumbnail(
            image,
            spec,
            normalized_crop=cached_crop,
        )
        if (
            spec.crop_mode == "smart_crop"
            and cached_crop is None
            and crop is not None
            and smart_crop_cache is not None
        ):
            smart_crop_cache.put(crop_key, crop)
        if _THUMBNAIL_LOG.isEnabledFor(logging.DEBUG):
            _THUMBNAIL_LOG.debug(
                "render source=%s proxy_max=%s cache=%sx%s crop=%s "
                "high_quality_resize_count=1 encoder_policy=%s-q%d",
                source_size,
                256 if spec.crop_mode == "smart_crop" else None,
                rendered.width(),
                rendered.height(),
                spec.crop_mode,
                spec.encoder_format,
                spec.encoder_quality,
            )
        return rendered, crop

    @staticmethod
    def _path_key(path: Path) -> str:
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path)))
        ).casefold()

    @staticmethod
    def _is_cancelled(cancel_token) -> bool:
        return bool(
            cancel_token is not None
            and getattr(cancel_token, "is_set", lambda: False)()
        )

    def _try_take(self, worker: _ThumbnailWorker) -> bool:
        if worker.xcf_lane:
            try:
                return self._xcf_pool.tryTake(worker)
            except RuntimeError:
                return False
        if self._coordinator is not None:
            return self._coordinator.try_take_browser(worker)
        try:
            return self._pool.tryTake(worker)
        except RuntimeError:
            # The worker completed and Qt auto-deleted its QRunnable before
            # the queued finished signal removed the Python pending record.
            return False

    def _start_worker(
        self,
        worker: _ThumbnailWorker,
        priority: ThumbnailPriority,
    ) -> bool:
        normalized = ThumbnailPriority(priority)
        if not worker.xcf_handoff_connected:
            worker.signals.xcf_deferred.connect(self._on_xcf_deferred)
            worker.xcf_handoff_connected = True
        if worker.xcf_lane:
            if self._closed or self._paused or (
                self._coordinator is not None and self._coordinator.browser_paused
            ):
                return False
            self._xcf_pool.start(worker, int(normalized))
            return True
        if self._coordinator is not None:
            mapped = {
                ThumbnailPriority.VISIBLE: ImageWorkPriority.BROWSER_VISIBLE,
                ThumbnailPriority.SELECTED: ImageWorkPriority.BROWSER_SELECTED,
                ThumbnailPriority.READ_AHEAD: ImageWorkPriority.BROWSER_READ_AHEAD,
                ThumbnailPriority.PREFETCH: ImageWorkPriority.BROWSER_PREFETCH,
                ThumbnailPriority.BACKGROUND: ImageWorkPriority.BROWSER_PREFETCH,
            }[normalized]
            return self._coordinator.start_browser(worker, mapped)
        self._pool.start(worker, int(normalized))
        return True

    def _on_xcf_deferred(self, old: _ThumbnailWorker) -> None:
        token = old.size.cache_token if isinstance(old.size, ThumbnailRenderSpec) else old.size
        key = (self._path_key(old.item.path), token, old.generation)
        with self._pending_lock:
            pending = self._pending.get(key)
            if pending is None or pending.worker is not old:
                return
            worker = _ThumbnailWorker(old.item, old.size, old.generation, old.loader, old.priority)
            worker.cancelled = old.cancelled
            worker.signals = old.signals
            worker.xcf_lane = True
            worker.xcf_handoff_connected = True
            self._pending[key] = _PendingThumbnail(worker, pending.priority)
        if (not worker.cancelled.is_set() and old.generation == self._generation
                and self._start_worker(worker, worker.priority)):
            return
        worker.signals.finished.emit(str(worker.item.path), worker.generation, token,
                                     worker.item.thumbnail_revision, ThumbnailLoadResult(
                                         None, result_kind=PreviewResultKind.CANCELLED,
                                         persist_to_disk=False))

    def _release_retired_if_idle(self) -> None:
        if not self._closed:
            return
        if not self.wait_for_done(0):
            QTimer.singleShot(100, self, self._release_retired_if_idle)
            return
        self._finalize_close()

    def _finalize_close(self) -> None:
        with self._pending_lock:
            self._pending.clear()
        if self._owns_preview_registry:
            self._preview_registry.shutdown()
        if self._disk_cache is not None:
            self._disk_cache.close()
        _RETIRED_THUMBNAIL_PROVIDERS.discard(self)

    def request_background(self, item, size, *, generation):
        """Generate through the existing lane; never enqueue the entire listing."""
        if self._closed or self._paused or generation != self._generation:
            return "blocked"
        with self._save_lock:
            if self._save_enqueue_backpressured:
                return "blocked"
        token = size.cache_token
        cache_key = (self._path_key(item.path), token, item.thumbnail_revision)
        if self._has_pending_save_for(item, size):
            return "blocked"
        if cache_key in self._cache:
            return "settled"
        candidate = self._memory_candidate(cache_key[0], item.thumbnail_revision, size)
        adequate = candidate is not None and candidate[1].long_edge >= size.long_edge * .95
        if adequate:
            return "settled"
        with self._failure_lock:
            if cache_key in self._failed or cache_key in self._quiet_results:
                return "settled"
        if self.pending_count:
            return "blocked"
        if self.request(item, size, generation=generation, priority=ThumbnailPriority.BACKGROUND):
            return "queued"
        with self._pending_lock:
            if (cache_key[0], token, generation) in self._pending:
                return "queued"
        candidate = self._memory_candidate(cache_key[0], item.thumbnail_revision, size)
        adequate = candidate is not None and candidate[1].long_edge >= size.long_edge * .95
        if cache_key in self._cache or adequate:
            return "settled"
        return "blocked"

    def _has_pending_save_for(
        self,
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
    ) -> bool:
        path_key = self._path_key(item.path)
        token = size.cache_token if isinstance(size, ThumbnailRenderSpec) else int(size)

        def matches(request: _ThumbnailSaveRequest) -> bool:
            return (
                self._path_key(Path(request.item.path)) == path_key
                and (
                    request.size.cache_token
                    if isinstance(request.size, ThumbnailRenderSpec)
                    else int(request.size)
                ) == token
            )

        def key_matches(key: tuple[object, ...] | None) -> bool:
            if key is None or len(key) < 2:
                return False
            identity = key[0]
            source_path = getattr(identity, "source_path", None)
            return (
                source_path is not None
                and self._path_key(Path(str(source_path))) == path_key
                and int(key[1]) == token
            )

        with self._save_lock:
            return bool(
                key_matches(self._active_save_key)
                or any(matches(request) for request in self._save_queue)
                or any(
                    matches(request)
                    for request in self._deferred_save_queue.values()
                )
                or any(
                    matches(request)
                    for request in self._retry_save_queue.values()
                )
            )

    @Slot(str, int, object, object, object)
    def _on_finished(self, path, generation, size_token, modified_at, result):
        loaded = result if isinstance(result, ThumbnailLoadResult) else ThumbnailLoadResult(result)
        state = loaded.resolved_kind.value
        with self._save_lock:
            self._save_enqueue_backpressured = False
        key = (self._path_key(Path(path)), int(size_token), generation)
        with self._pending_lock:
            pending = self._pending.get(key)
            if pending is not None and pending.worker.cancelled.is_set():
                state = "cancelled"
        try:
            self._consume_thumbnail_finished(path, generation, size_token, modified_at, result)
        finally:
            if (
                pending is not None
                and not pending.worker.cancelled.is_set()
                and loaded.image is not None
                and not loaded.image.isNull()
                and loaded.persist_to_disk
                and not loaded.disk_cache_hit
                and pending.priority is not ThumbnailPriority.PREFETCH
                and not self._queue_thumbnail_save(
                    pending.worker.item,
                    pending.worker.size,
                    loaded,
                    pending.priority,
                )
            ):
                self._increment_stat("memory_only")
            if not self._closed and not self._save_enqueue_backpressured:
                self.work_settled.emit(path, generation, size_token, modified_at, state)


def _invoke_thumbnail_loader(
    loader, item, size, cancel_token,
    thumbnail_priority=ThumbnailPriority.VISIBLE,
    provisional_callback=None, page_count_callback=None,
):
    with gimp_cancellation(cancel_token):
        return _invoke_thumbnail_loader_bound(
            loader, item, size, cancel_token, thumbnail_priority,
            provisional_callback, page_count_callback,
        )


def _invoke_thumbnail_loader_bound(
    loader,
    item: BrowserItem,
    size: int,
    cancel_token,
    thumbnail_priority: ThumbnailPriority = ThumbnailPriority.VISIBLE,
    provisional_callback: Callable[[QImage], None] | None = None,
    page_count_callback: Callable[[int], None] | None = None,
):
    try:
        signature = inspect.signature(loader)
        positional_count = len(
            [
                parameter
                for parameter in signature.parameters.values()
                if parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                }
            ]
        )
        accepts_priority = (
            "thumbnail_priority" in signature.parameters
            or positional_count >= 4
            or any(
                parameter.kind is inspect.Parameter.VAR_POSITIONAL
                for parameter in signature.parameters.values()
            )
        )
        accepts_provisional = "provisional_callback" in signature.parameters
        accepts_page_count = "page_count_callback" in signature.parameters
        accepts_cancel = (
            "cancel_token" in signature.parameters
            or positional_count >= 3
            or any(
                parameter.kind is inspect.Parameter.VAR_POSITIONAL
                for parameter in signature.parameters.values()
            )
        )
    except (TypeError, ValueError):
        accepts_cancel = False
        accepts_priority = False
        accepts_provisional = False
        accepts_page_count = False
    if accepts_provisional and accepts_page_count:
        return loader(
            item,
            size,
            cancel_token,
            thumbnail_priority,
            provisional_callback=provisional_callback,
            page_count_callback=page_count_callback,
        )
    if accepts_provisional:
        return loader(
            item,
            size,
            cancel_token,
            thumbnail_priority,
            provisional_callback=provisional_callback,
        )
    if accepts_priority:
        return loader(item, size, cancel_token, thumbnail_priority)
    if accepts_cancel:
        return loader(item, size, cancel_token)
    return loader(item, size)
