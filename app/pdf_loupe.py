"""Bounded PDF loupe artifacts, independent of normal Viewer cache/specs.

Only the final resampled image survives a job. PDFium objects remain inside the
existing serialized service; this worker owns pixel conversion/resampling.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import math
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage, QPixmap

from .image_adjustments import apply_image_adjustments
from .pdf_backend import PageRenderSpec
from .pdf_image_source import PdfImageSource
from .thumbnail_render import pil_to_qimage
from .viewer_render import ViewerRenderKey, render_qimage


PDF_LOUPE_CACHE_BYTES = 160 * 1024 * 1024


@dataclass(frozen=True)
class PdfLoupeKey:
    document_id: str
    page_index: int
    render: ViewerRenderKey
    adjustments: tuple[float, float, float]


class _Signals(QObject):
    finished = Signal(object, object, object)


class _Task(QRunnable):
    def __init__(self, source: PdfImageSource, key: PdfLoupeKey):
        super().__init__()
        self.setAutoDelete(False)
        self.source, self.key = source, key
        self.cancelled = Event()
        self.signals = _Signals()

    @Slot()
    def run(self):
        output, error = None, None
        try:
            if not self.cancelled.is_set():
                output = self._render()
        except Exception as exc:
            error = str(exc)
        self.signals.finished.emit(self, output, error)

    def _render(self):
        key = self.key.render
        image_id = f"pdf-page:{self.key.page_index}"
        logical_width, _ = self.source.logical_size(image_id)
        left_fraction = (logical_width // 2) / logical_width
        side = key.image_id.rpartition("#")[2]
        split = side in {"left", "right"}
        fraction = left_fraction if side == "left" else 1 - left_fraction
        rotated = key.rotation in {90, 270}
        width, height = key.target_width, key.target_height
        if split:
            if rotated:
                height = math.ceil(height / fraction)
            else:
                width = math.ceil(width / fraction)
        spec = PageRenderSpec(width, height, rotation_degrees=key.rotation)
        with self.source.open_image_for_render(
            image_id, spec, purpose="magnifier", cancel_token=self.cancelled,
        ) as raw:
            if self.cancelled.is_set():
                return None
            adjusted = apply_image_adjustments(raw, self.key.adjustments)
            try:
                source = pil_to_qimage(adjusted)
            finally:
                if adjusted is not raw:
                    adjusted.close()
        if self.cancelled.is_set():
            return None
        crop = None
        if split:
            axis = source.height() if rotated else source.width()
            span = max(1, min(axis - 1, round(axis * left_fraction)))
            first = (side == "left") != (key.rotation in {180, 270})
            cut = axis - span if key.rotation in {180, 270} else span
            start, extent = (0, cut) if first else (cut, axis - cut)
            crop = ((0, start, source.width(), extent) if rotated
                    else (start, 0, extent, source.height()))
        output, _ = render_qimage(source, replace(key, rotation=0, split_range=crop))
        return None if self.cancelled.is_set() else output


class PdfLoupeCache(QObject):
    ready = Signal(object, object)
    failed = Signal(object, str)

    def __init__(self, parent=None, *, byte_limit=PDF_LOUPE_CACHE_BYTES):
        super().__init__(parent)
        self.source: PdfImageSource | None = None
        self.byte_limit = max(0, min(PDF_LOUPE_CACHE_BYTES, int(byte_limit)))
        self._cache: OrderedDict[PdfLoupeKey, QPixmap] = OrderedDict()
        self._tasks: dict[PdfLoupeKey, _Task] = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._closed = False

    @property
    def cache_bytes(self):
        return sum(p.width() * p.height() * 4 for p in self._cache.values())

    @property
    def pending_count(self):
        return len(self._tasks)

    def set_byte_limit(self, limit):
        self.byte_limit = max(0, min(PDF_LOUPE_CACHE_BYTES, int(limit)))
        while self._cache and self.cache_bytes > self.byte_limit:
            self._cache.popitem(last=False)

    def set_source(self, source):
        if source is self.source:
            return
        for task in tuple(self._tasks.values()):
            self._cancel(task)
        self._cache.clear()
        self.source = source

    def retain_pages(self, indexes):
        for key, task in tuple(self._tasks.items()):
            if key.page_index not in indexes:
                self._cancel(task)

    def _cancel(self, task):
        task.cancelled.set()
        if self._pool.tryTake(task):
            self._tasks.pop(task.key, None)

    def request(self, page_index, render, adjustments):
        if self._closed or self.source is None:
            return
        key = PdfLoupeKey(self.source.document_id, page_index, render, adjustments)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self.ready.emit(key, cached)
            return
        existing = self._tasks.get(key)
        if existing is not None and not existing.cancelled.is_set():
            return
        # One running obsolete job may finish; at most the current one/two
        # slots are queued. Pointer motion never submits a different key.
        for old_key, task in tuple(self._tasks.items()):
            if old_key.render.image_id == render.image_id:
                self._cancel(task)
        task = _Task(self.source, key)
        self._tasks[key] = task
        task.signals.finished.connect(self._finished)
        self._pool.start(task)

    @Slot(object, object, object)
    def _finished(self, task, image: QImage | None, error):
        if self._tasks.get(task.key) is task:
            self._tasks.pop(task.key, None)
        if self._closed or task.cancelled.is_set() or task.source is not self.source:
            return
        if image is None or image.isNull():
            self.failed.emit(task.key, error or "PDF loupe rendering failed")
            return
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(task.key.render.device_pixel_ratio_milli / 1000)
        if pixmap.width() * pixmap.height() * 4 <= self.byte_limit:
            self._cache[task.key] = pixmap
            while self.cache_bytes > self.byte_limit or len(self._cache) > 8:
                self._cache.popitem(last=False)
        self.ready.emit(task.key, pixmap)

    def shutdown(self, msecs=5000):
        self._closed = True
        self.set_source(None)
        return self._pool.waitForDone(msecs)
