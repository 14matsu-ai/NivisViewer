from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import Lock
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
from .thumbnail_disk_cache import ThumbnailDiskCache


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
        self.signals = _ThumbnailWorkerSignals()

    @Slot()
    def run(self) -> None:
        result = self.loader(self.item, self.size)
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
    ) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, min(2, max_workers)))
        self._cache_capacity = max(1, cache_capacity)
        self._cache: OrderedDict[tuple[str, int, float | None], QImage] = OrderedDict()
        self._pending: set[tuple[str, int, int]] = set()
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

    @property
    def generation(self) -> int:
        return self._generation

    def begin_generation(self) -> int:
        self._generation += 1
        with self._pending_lock:
            self._pending.clear()
        self._pool.clear()
        return self._generation

    def request(self, item: BrowserItem, size: int, *, generation: int | None = None) -> bool:
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
        with self._pending_lock:
            if pending_key in self._pending:
                return False
            self._pending.add(pending_key)

        worker = _ThumbnailWorker(
            item,
            normalized_size,
            requested_generation,
            self._loader,
        )
        worker.signals.finished.connect(self._on_finished)
        self._pool.start(worker)
        return True

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

    def close(self, wait_msecs: int = 5000) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._pool.clear()
        self._pool.waitForDone(wait_msecs)
        with self._pending_lock:
            self._pending.clear()
        if self._disk_cache is not None:
            self._disk_cache.close()

    @staticmethod
    def load_thumbnail(item: BrowserItem, size: int) -> QImage | None:
        return BrowserThumbnailProvider.load_thumbnail_result(item, size).image

    @staticmethod
    def load_thumbnail_result(item: BrowserItem, size: int) -> ThumbnailLoadResult:
        try:
            if item.kind == BrowserItemKind.IMAGE:
                return ThumbnailLoadResult(
                    BrowserThumbnailProvider._load_image_path(item.path, size)
                )
            if item.kind == BrowserItemKind.FOLDER:
                return BrowserThumbnailProvider._load_folder_result(item.path, size)
            if item.kind == BrowserItemKind.ARCHIVE:
                return ThumbnailLoadResult(
                    BrowserThumbnailProvider._load_archive(item.path, size)
                )
        except Exception:
            return ThumbnailLoadResult(None)
        return ThumbnailLoadResult(None)

    def _load_pipeline(self, item: BrowserItem, size: int) -> ThumbnailLoadResult:
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
            result = self.load_thumbnail_result(item, size)
        else:
            loaded = self._decode_loader(item, size)
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
            self._pending.discard(pending_key)
        if self._closed or generation != self._generation or image is None or image.isNull():
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
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        return str(resolved).casefold()
