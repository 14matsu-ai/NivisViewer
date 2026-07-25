from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
import inspect
import logging
import os
from pathlib import Path
from threading import Event, Lock
from typing import Callable
import zipfile

from natsort import natsorted
from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QIcon, QImage, QPixmap

from .browser_model import (
    BROWSER_IMAGE_EXTENSIONS,
    BrowserItem,
    BrowserItemKind,
)
from .browser_thumbnail_scheduler import ThumbnailPriority
from .image_work_coordinator import ImageWorkCoordinator, ImageWorkPriority
from .image_source import EXTERNAL_ARCHIVE_EXTENSIONS, SUPPORTED_EXTENSIONS, SevenZipImageSource
from .pdf_backend import PageRenderSpec, PdfRenderPriority
from .thumbnail_disk_cache import ThumbnailDiskCache
from .thumbnail_render import (
    SmartCropCache,
    ThumbnailRenderSpec,
    pil_to_qimage,
    render_pil_thumbnail,
    smart_crop_cache_key,
)


_RETIRED_THUMBNAIL_PROVIDERS: set[BrowserThumbnailProvider] = set()
_THUMBNAIL_LOG = logging.getLogger("nivisviewer.thumbnail")


class PageThumbnailProvider:
    @staticmethod
    def create_icon(qimage: QImage, size: int) -> QIcon:
        thumbnail = qimage.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        return QIcon(QPixmap.fromImage(thumbnail))


@dataclass(frozen=True)
class ThumbnailLoadResult:
    image: QImage | None
    cover_path: Path | None = None
    provisional_image: QImage | None = None
    entry_path: str = ""


@dataclass(frozen=True)
class _PendingThumbnail:
    worker: _ThumbnailWorker
    priority: ThumbnailPriority


class _ThumbnailWorkerSignals(QObject):
    finished = Signal(str, int, object, object, object)
    provisional = Signal(str, int, object, object, object)


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
        self.signals = _ThumbnailWorkerSignals()

    @Slot()
    def run(self) -> None:
        def report_provisional(image: QImage) -> None:
            self.signals.provisional.emit(
                str(self.item.path),
                self.generation,
                (
                    self.size.cache_token
                    if isinstance(self.size, ThumbnailRenderSpec)
                    else self.size
                ),
                self.item.modified_at,
                image,
            )

        result = _invoke_thumbnail_loader(
            self.loader,
            self.item,
            self.size,
            self.cancelled,
            self.priority,
            report_provisional,
        )
        self.signals.finished.emit(
            str(self.item.path),
            self.generation,
            (
                self.size.cache_token
                if isinstance(self.size, ThumbnailRenderSpec)
                else self.size
            ),
            self.item.modified_at,
            result,
        )


class BrowserThumbnailProvider(QObject):
    thumbnail_ready = Signal(str, int, object)
    thumbnail_provisional = Signal(str, int, object)
    cache_cleared = Signal()
    scheduling_resumed = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        max_workers: int = 2,
        cache_capacity: int = 128,
        loader: Callable[[BrowserItem, int], QImage | None] | None = None,
        disk_cache: ThumbnailDiskCache | None = None,
        disk_cache_enabled: bool = True,
        archive_backend_registry=None,
        pdfium_service=None,
        image_work_coordinator: ImageWorkCoordinator | None = None,
    ) -> None:
        super().__init__(parent)
        self._coordinator = image_work_coordinator
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._cache_capacity = max(1, cache_capacity)
        self._cache: OrderedDict[tuple[str, int, float | None], QImage] = OrderedDict()
        self._cache_specs: dict[int, ThumbnailRenderSpec] = {}
        self._pending: dict[tuple[str, int, int], _PendingThumbnail] = {}
        self._pending_lock = Lock()
        self._failure_lock = Lock()
        self._generation = 0
        self._closed = False
        self._decode_loader = loader
        self._loader = self._load_pipeline
        self._disk_cache = disk_cache
        self._disk_cache_enabled = bool(disk_cache_enabled)
        self._failed: set[tuple[str, int, float | None]] = set()
        self._maintenance_started = False
        self._archive_backend_registry = archive_backend_registry
        self._pdfium_service = pdfium_service
        self._smart_crop_cache = SmartCropCache()
        self._paused = bool(
            image_work_coordinator is not None
            and image_work_coordinator.browser_paused
        )
        if image_work_coordinator is not None:
            image_work_coordinator.browser_pause_changed.connect(self.set_paused)

    @property
    def generation(self) -> int:
        return self._generation

    def begin_generation(self) -> int:
        self._generation += 1
        with self._pending_lock:
            for pending in self._pending.values():
                pending.worker.cancelled.set()
                self._try_take(pending.worker)
            self._pending.clear()
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
        cache_key = (path_key, cache_token, item.modified_at)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            image = cached.copy()
            QTimer.singleShot(
                0,
                lambda path=str(item.path), current=requested_generation, result=image: (
                    self.thumbnail_ready.emit(path, current, result)
                    if not self._closed and current == self._generation
                    else None
                ),
            )
            return False

        if isinstance(normalized_size, ThumbnailRenderSpec):
            candidate = self._memory_candidate(
                path_key,
                item.modified_at,
                normalized_size,
            )
            if candidate is not None:
                candidate_image, candidate_spec = candidate
                image = candidate_image.copy()
                if candidate_spec.long_edge >= normalized_size.long_edge * 0.95:
                    QTimer.singleShot(
                        0,
                        lambda path=str(item.path), current=requested_generation, result=image: (
                            self.thumbnail_ready.emit(path, current, result)
                            if not self._closed and current == self._generation
                            else None
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
        normalized_priority = ThumbnailPriority(priority)
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
            self._pending[pending_key] = _PendingThumbnail(
                worker,
                normalized_priority,
            )
        if self._start_worker(worker, normalized_priority):
            return True
        with self._pending_lock:
            self._pending.pop(pending_key, None)
        return False

    def cancel_prefetch_except(
        self,
        paths: set[str],
        *,
        size: int | ThumbnailRenderSpec,
        generation: int,
    ) -> int:
        keep = {self._path_key(Path(path)) for path in paths}
        size_token = size.cache_token if isinstance(size, ThumbnailRenderSpec) else int(size)
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
                pending.worker.cancelled.set()
                if self._try_take(pending.worker):
                    self._pending.pop(key, None)
                    cancelled += 1
        return cancelled

    @Slot(bool)
    def set_paused(self, paused: bool) -> None:
        normalized = bool(paused)
        if normalized == self._paused:
            return
        self._paused = normalized
        if normalized:
            with self._pending_lock:
                for key, pending in tuple(self._pending.items()):
                    if self._try_take(pending.worker):
                        pending.worker.cancelled.set()
                        self._pending.pop(key, None)
        else:
            self.scheduling_resumed.emit()

    @property
    def pending_count(self) -> int:
        with self._pending_lock:
            return len(self._pending)

    def wait_for_done(self, msecs: int = 5000) -> bool:
        if self._coordinator is not None:
            return self._coordinator.wait_for_browser(msecs)
        return self._pool.waitForDone(msecs)

    @property
    def disk_cache(self) -> ThumbnailDiskCache | None:
        return self._disk_cache

    def disk_cache_usage_bytes(self) -> int:
        if not self._disk_cache_enabled or self._disk_cache is None:
            return 0
        return self._disk_cache.usage_bytes()

    def set_disk_cache_enabled(self, enabled: bool) -> None:
        self._disk_cache_enabled = bool(enabled)

    def set_disk_cache_limit_mb(self, limit_mb: int) -> None:
        disk_cache = self._disk_cache
        if disk_cache is None:
            return
        disk_cache.set_limit_mb(limit_mb)
        if not self._disk_cache_enabled:
            return

        def prune_disk() -> ThumbnailLoadResult:
            disk_cache.set_enabled(True)
            disk_cache.prune()
            return ThumbnailLoadResult(None)

        item = BrowserItem(
            display_name="cache-prune",
            path=disk_cache.cache_dir,
            kind=BrowserItemKind.FOLDER,
            modified_at=None,
        )
        worker = _ThumbnailWorker(
            item,
            16,
            self._generation,
            lambda _item, _size: prune_disk(),
        )
        self._start_worker(worker, ThumbnailPriority.PREFETCH)

    def clear_memory_cache(self) -> None:
        self._cache.clear()
        self._cache_specs.clear()
        with self._failure_lock:
            self._failed.clear()

    def clear_all_caches_async(self) -> None:
        self.clear_memory_cache()
        disk_cache = self._disk_cache
        if disk_cache is None:
            QTimer.singleShot(0, self.cache_cleared.emit)
            return

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
        with self._pending_lock:
            for pending in self._pending.values():
                pending.worker.cancelled.set()
                self._try_take(pending.worker)
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
    ) -> ThumbnailLoadResult:
        spec = (
            size
            if isinstance(size, ThumbnailRenderSpec)
            else ThumbnailRenderSpec.from_settings(size, "square_1_1", "letterbox")
        )
        try:
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
                )
            if item.kind == BrowserItemKind.ARCHIVE:
                if item.path.suffix.lower() in EXTERNAL_ARCHIVE_EXTENSIONS:
                    return BrowserThumbnailProvider._load_external_archive_result(
                        item.path,
                        spec,
                        archive_backend_registry,
                        cancel_token,
                        smart_crop_cache,
                    )
                return BrowserThumbnailProvider._load_archive_result(
                    item.path,
                    spec,
                    smart_crop_cache,
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
        except Exception:
            return ThumbnailLoadResult(None)
        return ThumbnailLoadResult(None)

    def _load_pipeline(
        self,
        item: BrowserItem,
        size: int | ThumbnailRenderSpec,
        cancel_token=None,
        thumbnail_priority: ThumbnailPriority = ThumbnailPriority.VISIBLE,
        provisional_callback: Callable[[QImage], None] | None = None,
    ) -> ThumbnailLoadResult:
        cache_token = size.cache_token if isinstance(size, ThumbnailRenderSpec) else int(size)
        failure_key = (self._path_key(item.path), cache_token, item.modified_at)
        disk_cache = self._disk_cache
        provisional: QImage | None = None
        if self._disk_cache_enabled and disk_cache is not None:
            disk_cache.set_enabled(True)
            self._run_initial_maintenance(disk_cache)
            if isinstance(size, ThumbnailRenderSpec) and hasattr(
                disk_cache, "get_suitable"
            ):
                cached_result = disk_cache.get_suitable(item, size)
                if cached_result is not None:
                    if not cached_result.low_resolution_placeholder:
                        return ThumbnailLoadResult(cached_result.image)
                    provisional = cached_result.image
                    if provisional_callback is not None:
                        provisional_callback(provisional.copy())
                        provisional = None
            else:
                cached = disk_cache.get(item, cache_token)
                if cached is not None:
                    return ThumbnailLoadResult(cached)

        with self._failure_lock:
            if failure_key in self._failed:
                return ThumbnailLoadResult(None, provisional_image=provisional)

        if self._decode_loader is None:
            result = self.load_thumbnail_result(
                item,
                size,
                self._archive_backend_registry,
                cancel_token,
                self._pdfium_service,
                self._pdf_render_priority(thumbnail_priority),
                self._smart_crop_cache,
            )
        else:
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
        if result.image is None or result.image.isNull():
            with self._failure_lock:
                self._failed.add(failure_key)
            return ThumbnailLoadResult(None, provisional_image=provisional)

        if self._disk_cache_enabled and disk_cache is not None:
            disk_cache.put(
                item,
                size if isinstance(size, ThumbnailRenderSpec) else cache_token,
                result.image,
                cover_path=result.cover_path,
                entry_path=result.entry_path,
            )
        return ThumbnailLoadResult(
            result.image,
            result.cover_path,
            provisional_image=provisional,
            entry_path=result.entry_path,
        )

    @Slot(str, int, object, object, object)
    def _on_provisional(
        self,
        path: str,
        generation: int,
        _size_token: int,
        _modified_at: float | None,
        image: QImage | None,
    ) -> None:
        if (
            self._closed
            or generation != self._generation
            or image is None
            or image.isNull()
        ):
            return
        self.thumbnail_provisional.emit(path, generation, image)

    def _run_initial_maintenance(self, disk_cache: ThumbnailDiskCache) -> None:
        with self._pending_lock:
            if self._maintenance_started:
                return
            self._maintenance_started = True
        disk_cache.prune()

    @staticmethod
    def _pdf_render_priority(priority: ThumbnailPriority) -> int:
        return {
            ThumbnailPriority.VISIBLE: int(PdfRenderPriority.THUMBNAIL_VISIBLE),
            ThumbnailPriority.SELECTED: int(PdfRenderPriority.THUMBNAIL_SELECTED),
            ThumbnailPriority.PREFETCH: int(PdfRenderPriority.THUMBNAIL_PREFETCH),
        }[ThumbnailPriority(priority)]

    @Slot(str, int, object, object, object)
    def _on_finished(
        self,
        path: str,
        generation: int,
        size_token: int,
        modified_at: float | None,
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
        if provisional is not None and not provisional.isNull():
            self.thumbnail_provisional.emit(path, generation, provisional)
        image = loaded.image
        if image is None or image.isNull():
            return
        self._cache[cache_key] = image.copy()
        if pending is not None and isinstance(
            pending.worker.size, ThumbnailRenderSpec
        ):
            self._cache_specs[int(size_token)] = pending.worker.size
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self._cache_capacity:
            self._cache.popitem(last=False)
        self.thumbnail_ready.emit(path, generation, image)

    def _memory_candidate(
        self,
        path_key: str,
        modified_at: float | None,
        requested: ThumbnailRenderSpec,
    ) -> tuple[QImage, ThumbnailRenderSpec] | None:
        candidates: list[tuple[QImage, ThumbnailRenderSpec]] = []
        for (cached_path, token, cached_modified), image in self._cache.items():
            if cached_path != path_key or cached_modified != modified_at:
                continue
            spec = self._cache_specs.get(token)
            if spec is None or spec.family_token != requested.family_token:
                continue
            candidates.append((image, spec))
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
            stat = path.stat()
            with Image.open(path) as image:
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
    def _load_folder_result(
        folder: Path,
        spec: ThumbnailRenderSpec,
        smart_crop_cache: SmartCropCache | None = None,
    ) -> ThumbnailLoadResult:
        try:
            candidates = natsorted(
                (
                    path
                    for path in folder.iterdir()
                    if path.is_file() and path.suffix.lower() in BROWSER_IMAGE_EXTENSIONS
                ),
                key=lambda path: path.name,
            )
        except OSError:
            return ThumbnailLoadResult(None)
        for path in candidates:
            image = BrowserThumbnailProvider._load_image_path(
                path,
                spec,
                smart_crop_cache,
            )
            if image is not None:
                return ThumbnailLoadResult(image, path)
        return ThumbnailLoadResult(None)

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
    ) -> ThumbnailLoadResult:
        try:
            stat = archive.stat()
            with zipfile.ZipFile(archive, "r") as source:
                names = natsorted(
                    (
                        info.filename
                        for info in source.infolist()
                        if not info.is_dir()
                        and Path(info.filename).suffix.lower() in BROWSER_IMAGE_EXTENSIONS
                    )
                )
                for name in names:
                    try:
                        with source.open(name, "r") as file:
                            data = file.read()
                        with Image.open(BytesIO(data)) as image:
                            qimage, _crop = BrowserThumbnailProvider._render_image(
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
                        if qimage is not None:
                            return ThumbnailLoadResult(qimage, entry_path=name)
                    except Exception:
                        continue
        except (OSError, zipfile.BadZipFile):
            return ThumbnailLoadResult(None)
        return ThumbnailLoadResult(None)

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
    ) -> ThumbnailLoadResult:
        if archive_backend_registry is None:
            return ThumbnailLoadResult(None)
        backend = archive_backend_registry.backend_for_path(archive)
        if backend is None:
            return ThumbnailLoadResult(None)
        source: SevenZipImageSource | None = None
        try:
            source = SevenZipImageSource(
                archive,
                backend=backend,
                cancel_token=cancel_token,
            )
            first = source.list_images()[0]
            with source.open_image(first) as image:
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
                return ThumbnailLoadResult(rendered, entry_path=first)
        except Exception:
            return ThumbnailLoadResult(None)
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

    def _try_take(self, worker: _ThumbnailWorker) -> bool:
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
        if self._coordinator is not None:
            mapped = {
                ThumbnailPriority.VISIBLE: ImageWorkPriority.BROWSER_VISIBLE,
                ThumbnailPriority.SELECTED: ImageWorkPriority.BROWSER_SELECTED,
                ThumbnailPriority.PREFETCH: ImageWorkPriority.BROWSER_PREFETCH,
            }[normalized]
            return self._coordinator.start_browser(worker, mapped)
        self._pool.start(worker, int(normalized))
        return True

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
        if self._disk_cache is not None:
            self._disk_cache.close()
        _RETIRED_THUMBNAIL_PROVIDERS.discard(self)


def _invoke_thumbnail_loader(
    loader,
    item: BrowserItem,
    size: int,
    cancel_token,
    thumbnail_priority: ThumbnailPriority = ThumbnailPriority.VISIBLE,
    provisional_callback: Callable[[QImage], None] | None = None,
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
