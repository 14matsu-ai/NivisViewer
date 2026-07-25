from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
import inspect
import os
from pathlib import Path
from threading import Event, Lock
from typing import Callable
import zipfile

from natsort import natsorted
from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QIcon, QImage, QPixmap

from .browser_model import (
    BROWSER_IMAGE_EXTENSIONS,
    BrowserItem,
    BrowserItemKind,
)
from .browser_thumbnail_scheduler import ThumbnailPriority
from .image_source import EXTERNAL_ARCHIVE_EXTENSIONS, SUPPORTED_EXTENSIONS, SevenZipImageSource
from .thumbnail_disk_cache import ThumbnailDiskCache


_RETIRED_THUMBNAIL_PROVIDERS: set[BrowserThumbnailProvider] = set()


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


@dataclass(frozen=True)
class _PendingThumbnail:
    worker: _ThumbnailWorker
    priority: ThumbnailPriority


class _ThumbnailWorkerSignals(QObject):
    finished = Signal(str, int, int, object, object)


class _ThumbnailWorker(QRunnable):
    def __init__(
        self,
        item: BrowserItem,
        size: int,
        generation: int,
        loader: Callable[[BrowserItem, int], ThumbnailLoadResult],
    ) -> None:
        super().__init__()
        self.item = item
        self.size = size
        self.generation = generation
        self.loader = loader
        self.cancelled = Event()
        self.signals = _ThumbnailWorkerSignals()

    @Slot()
    def run(self) -> None:
        result = _invoke_thumbnail_loader(
            self.loader,
            self.item,
            self.size,
            self.cancelled,
        )
        self.signals.finished.emit(
            str(self.item.path),
            self.generation,
            self.size,
            self.item.modified_at,
            result.image,
        )


class BrowserThumbnailProvider(QObject):
    thumbnail_ready = Signal(str, int, object)
    cache_cleared = Signal()

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
    ) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, min(2, max_workers)))
        self._cache_capacity = max(1, cache_capacity)
        self._cache: OrderedDict[tuple[str, int, float | None], QImage] = OrderedDict()
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

    @property
    def generation(self) -> int:
        return self._generation

    def begin_generation(self) -> int:
        self._generation += 1
        with self._pending_lock:
            for pending in self._pending.values():
                pending.worker.cancelled.set()
            self._pending.clear()
        self._pool.clear()
        return self._generation

    def request(
        self,
        item: BrowserItem,
        size: int,
        *,
        generation: int | None = None,
        priority: ThumbnailPriority = ThumbnailPriority.VISIBLE,
    ) -> bool:
        if self._closed:
            return False
        requested_generation = self._generation if generation is None else generation
        if requested_generation != self._generation:
            return False

        normalized_size = max(16, min(1024, int(size)))
        path_key = self._path_key(item.path)
        cache_key = (path_key, normalized_size, item.modified_at)
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

        pending_key = (path_key, normalized_size, requested_generation)
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
                    self._pool.start(existing.worker, int(normalized_priority))
                return False

            worker = _ThumbnailWorker(
                item,
                normalized_size,
                requested_generation,
                self._loader,
            )
            worker.signals.finished.connect(self._on_finished)
            self._pending[pending_key] = _PendingThumbnail(
                worker,
                normalized_priority,
            )
        self._pool.start(worker, int(normalized_priority))
        return True

    def cancel_prefetch_except(
        self,
        paths: set[str],
        *,
        size: int,
        generation: int,
    ) -> int:
        keep = {self._path_key(Path(path)) for path in paths}
        cancelled = 0
        with self._pending_lock:
            candidates = tuple(self._pending.items())
            for key, pending in candidates:
                path_key, pending_size, pending_generation = key
                if (
                    pending_generation != generation
                    or pending_size != int(size)
                    or pending.priority is not ThumbnailPriority.PREFETCH
                    or path_key in keep
                ):
                    continue
                pending.worker.cancelled.set()
                if self._try_take(pending.worker):
                    self._pending.pop(key, None)
                    cancelled += 1
        return cancelled

    @property
    def pending_count(self) -> int:
        with self._pending_lock:
            return len(self._pending)

    def wait_for_done(self, msecs: int = 5000) -> bool:
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
        self._pool.start(
            _ThumbnailWorker(
                item,
                16,
                self._generation,
                lambda _item, _size: prune_disk(),
            )
        )

    def clear_memory_cache(self) -> None:
        self._cache.clear()
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
        self._pool.start(worker)

    def close(self, wait_msecs: int = 250) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        with self._pending_lock:
            for pending in self._pending.values():
                pending.worker.cancelled.set()
        self._pool.clear()
        if self._pool.waitForDone(wait_msecs):
            self._finalize_close()
            return
        self.setParent(None)
        _RETIRED_THUMBNAIL_PROVIDERS.add(self)
        QTimer.singleShot(100, self, self._release_retired_if_idle)

    @staticmethod
    def load_thumbnail(item: BrowserItem, size: int) -> QImage | None:
        return BrowserThumbnailProvider.load_thumbnail_result(item, size).image

    @staticmethod
    def load_thumbnail_result(
        item: BrowserItem,
        size: int,
        archive_backend_registry=None,
        cancel_token=None,
    ) -> ThumbnailLoadResult:
        try:
            if item.kind == BrowserItemKind.IMAGE:
                return ThumbnailLoadResult(
                    BrowserThumbnailProvider._load_image_path(item.path, size)
                )
            if item.kind == BrowserItemKind.FOLDER:
                return BrowserThumbnailProvider._load_folder_result(item.path, size)
            if item.kind == BrowserItemKind.ARCHIVE:
                if item.path.suffix.lower() in EXTERNAL_ARCHIVE_EXTENSIONS:
                    return ThumbnailLoadResult(
                        BrowserThumbnailProvider._load_external_archive(
                            item.path,
                            size,
                            archive_backend_registry,
                            cancel_token,
                        )
                    )
                return ThumbnailLoadResult(
                    BrowserThumbnailProvider._load_archive(item.path, size)
                )
        except Exception:
            return ThumbnailLoadResult(None)
        return ThumbnailLoadResult(None)

    def _load_pipeline(
        self,
        item: BrowserItem,
        size: int,
        cancel_token=None,
    ) -> ThumbnailLoadResult:
        failure_key = (self._path_key(item.path), size, item.modified_at)
        disk_cache = self._disk_cache
        if self._disk_cache_enabled and disk_cache is not None:
            disk_cache.set_enabled(True)
            self._run_initial_maintenance(disk_cache)
            cached = disk_cache.get(item, size)
            if cached is not None:
                return ThumbnailLoadResult(cached)

        with self._failure_lock:
            if failure_key in self._failed:
                return ThumbnailLoadResult(None)

        if self._decode_loader is None:
            result = self.load_thumbnail_result(
                item,
                size,
                self._archive_backend_registry,
                cancel_token,
            )
        else:
            loaded = _invoke_thumbnail_loader(
                self._decode_loader,
                item,
                size,
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
            return ThumbnailLoadResult(None)

        if self._disk_cache_enabled and disk_cache is not None:
            disk_cache.put(
                item,
                size,
                result.image,
                cover_path=result.cover_path,
            )
        return result

    def _run_initial_maintenance(self, disk_cache: ThumbnailDiskCache) -> None:
        with self._pending_lock:
            if self._maintenance_started:
                return
            self._maintenance_started = True
        disk_cache.prune()

    @Slot(str, int, int, object, object)
    def _on_finished(
        self,
        path: str,
        generation: int,
        size: int,
        modified_at: float | None,
        image: QImage | None,
    ) -> None:
        path_key = self._path_key(Path(path))
        pending_key = (path_key, size, generation)
        cache_key = (path_key, size, modified_at)
        with self._pending_lock:
            self._pending.pop(pending_key, None)
        if self._closed:
            self._release_retired_if_idle()
            return
        if generation != self._generation or image is None or image.isNull():
            return
        self._cache[cache_key] = image.copy()
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self._cache_capacity:
            self._cache.popitem(last=False)
        self.thumbnail_ready.emit(path, generation, image)

    @Slot(str, int, int, object, object)
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
    def _load_image_path(path: Path, size: int) -> QImage | None:
        try:
            with Image.open(path) as image:
                prepared = ImageOps.exif_transpose(image)
                return BrowserThumbnailProvider._pil_to_qimage(prepared, size)
        except Exception:
            return None

    @staticmethod
    def _load_folder(folder: Path, size: int) -> QImage | None:
        return BrowserThumbnailProvider._load_folder_result(folder, size).image

    @staticmethod
    def _load_folder_result(folder: Path, size: int) -> ThumbnailLoadResult:
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
            image = BrowserThumbnailProvider._load_image_path(path, size)
            if image is not None:
                return ThumbnailLoadResult(image, path)
        return ThumbnailLoadResult(None)

    @staticmethod
    def _load_archive(archive: Path, size: int) -> QImage | None:
        try:
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
                            prepared = ImageOps.exif_transpose(image)
                            qimage = BrowserThumbnailProvider._pil_to_qimage(prepared, size)
                        if qimage is not None:
                            return qimage
                    except Exception:
                        continue
        except (OSError, zipfile.BadZipFile):
            return None
        return None

    @staticmethod
    def _load_external_archive(
        archive: Path,
        size: int,
        archive_backend_registry,
        cancel_token,
    ) -> QImage | None:
        if archive_backend_registry is None:
            return None
        backend = archive_backend_registry.backend_for_path(archive)
        if backend is None:
            return None
        source: SevenZipImageSource | None = None
        try:
            source = SevenZipImageSource(
                archive,
                backend=backend,
                cancel_token=cancel_token,
            )
            first = source.list_images()[0]
            with source.open_image(first) as image:
                return BrowserThumbnailProvider._pil_to_qimage(image, size)
        except Exception:
            return None
        finally:
            if source is not None:
                source.close()

    @staticmethod
    def _pil_to_qimage(image: Image.Image, size: int) -> QImage:
        prepared = image.convert("RGBA")
        prepared.thumbnail((size, size), Image.Resampling.LANCZOS)
        data = prepared.tobytes("raw", "RGBA")
        return QImage(
            data,
            prepared.width,
            prepared.height,
            prepared.width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()

    @staticmethod
    def _path_key(path: Path) -> str:
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path)))
        ).casefold()

    def _try_take(self, worker: _ThumbnailWorker) -> bool:
        try:
            return self._pool.tryTake(worker)
        except RuntimeError:
            # The worker completed and Qt auto-deleted its QRunnable before
            # the queued finished signal removed the Python pending record.
            return False

    def _release_retired_if_idle(self) -> None:
        if not self._closed:
            return
        if not self._pool.waitForDone(0):
            QTimer.singleShot(100, self, self._release_retired_if_idle)
            return
        self._finalize_close()

    def _finalize_close(self) -> None:
        with self._pending_lock:
            self._pending.clear()
        if self._disk_cache is not None:
            self._disk_cache.close()
        _RETIRED_THUMBNAIL_PROVIDERS.discard(self)


def _invoke_thumbnail_loader(loader, item: BrowserItem, size: int, cancel_token):
    try:
        signature = inspect.signature(loader)
        accepts_cancel = (
            "cancel_token" in signature.parameters
            or len(
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
            >= 3
            or any(
                parameter.kind is inspect.Parameter.VAR_POSITIONAL
                for parameter in signature.parameters.values()
            )
        )
    except (TypeError, ValueError):
        accepts_cancel = False
    if accepts_cancel:
        return loader(item, size, cancel_token)
    return loader(item, size)
