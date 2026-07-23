from __future__ import annotations

from collections import OrderedDict
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


class _ThumbnailWorkerSignals(QObject):
    finished = Signal(str, int, int, object, object)


class _ThumbnailWorker(QRunnable):
    def __init__(
        self,
        item: BrowserItem,
        size: int,
        generation: int,
        loader: Callable[[BrowserItem, int], QImage | None],
    ) -> None:
        super().__init__()
        self.item = item
        self.size = size
        self.generation = generation
        self.loader = loader
        self.signals = _ThumbnailWorkerSignals()

    @Slot()
    def run(self) -> None:
        image = self.loader(self.item, self.size)
        self.signals.finished.emit(
            str(self.item.path),
            self.generation,
            self.size,
            self.item.modified_at,
            image,
        )


class BrowserThumbnailProvider(QObject):
    thumbnail_ready = Signal(str, int, object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        max_workers: int = 2,
        cache_capacity: int = 128,
        loader: Callable[[BrowserItem, int], QImage | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, min(2, max_workers)))
        self._cache_capacity = max(1, cache_capacity)
        self._cache: OrderedDict[tuple[str, int, float | None], QImage] = OrderedDict()
        self._pending: set[tuple[str, int, int]] = set()
        self._pending_lock = Lock()
        self._generation = 0
        self._closed = False
        self._loader = loader or self.load_thumbnail

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

    def close(self, wait_msecs: int = 2000) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._pool.clear()
        self._pool.waitForDone(wait_msecs)
        with self._pending_lock:
            self._pending.clear()

    @staticmethod
    def load_thumbnail(item: BrowserItem, size: int) -> QImage | None:
        try:
            if item.kind == BrowserItemKind.IMAGE:
                return BrowserThumbnailProvider._load_image_path(item.path, size)
            if item.kind == BrowserItemKind.FOLDER:
                return BrowserThumbnailProvider._load_folder(item.path, size)
            if item.kind == BrowserItemKind.ARCHIVE:
                return BrowserThumbnailProvider._load_archive(item.path, size)
        except Exception:
            return None
        return None

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
            return None
        for path in candidates:
            image = BrowserThumbnailProvider._load_image_path(path, size)
            if image is not None:
                return image
        return None

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
                        from io import BytesIO

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
