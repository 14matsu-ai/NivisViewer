from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from PIL import Image, ImageEnhance
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage

from .image_source import ImageSource
from .pdf_backend import PageRenderSpec, PdfRenderPriority


PRELOAD_RADIUS = 3


@dataclass(frozen=True)
class CachedImage:
    page_index: int
    image_id: str
    qimage: QImage | None
    original_size: tuple[int, int] | None
    error: str | None
    generation: int
    rendered_size: tuple[int, int] | None = None
    rendered_rotation: int = 0


@dataclass(frozen=True)
class _ImageLoadResult:
    cached: CachedImage
    source: ImageSource


class _ImageLoadSignals(QObject):
    loaded = Signal(object)


class _ImageLoadTask(QRunnable):
    def __init__(
        self,
        source: ImageSource,
        page_index: int,
        image_id: str,
        generation: int,
        adjustments: tuple[float, float, float],
        render_spec: PageRenderSpec | None,
        priority: int,
    ) -> None:
        super().__init__()
        self.source = source
        self.page_index = page_index
        self.image_id = image_id
        self.generation = generation
        self.adjustments = adjustments
        self.render_spec = render_spec
        self.priority = priority
        self.signals = _ImageLoadSignals()

    @Slot()
    def run(self) -> None:
        try:
            renderer = getattr(self.source, "open_image_for_render", None)
            if callable(renderer):
                image = renderer(
                    self.image_id,
                    self.render_spec,
                    priority=self.priority,
                    generation=self.generation,
                )
            else:
                image = self.source.open_image(self.image_id)
            adjusted: Image.Image | None = None
            try:
                adjusted = self._apply_adjustments(image)
                qimage = self._pil_to_qimage(adjusted)
                original_size = image.info.get("logical_size", adjusted.size)
                rendered_size = image.info.get("pdf_render_size")
            finally:
                if adjusted is not None and adjusted is not image:
                    adjusted.close()
                image.close()
            result = CachedImage(
                page_index=self.page_index,
                image_id=self.image_id,
                qimage=qimage,
                original_size=original_size,
                error=None,
                generation=self.generation,
                rendered_size=rendered_size,
                rendered_rotation=(
                    self.render_spec.rotation_degrees % 360
                    if self.render_spec is not None
                    else 0
                ),
            )
        except Exception as exc:
            result = CachedImage(
                page_index=self.page_index,
                image_id=self.image_id,
                qimage=None,
                original_size=None,
                error=str(exc),
                generation=self.generation,
            )
        self.signals.loaded.emit(_ImageLoadResult(result, self.source))

    @staticmethod
    def _pil_to_qimage(image: Image.Image) -> QImage:
        rgba = image.convert("RGBA")
        data = rgba.tobytes("raw", "RGBA")
        return QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888).copy()

    def _apply_adjustments(self, image: Image.Image) -> Image.Image:
        brightness, contrast, gamma = self.adjustments
        adjusted = image.convert("RGBA")
        if brightness != 1.0:
            adjusted = ImageEnhance.Brightness(adjusted).enhance(brightness)
        if contrast != 1.0:
            adjusted = ImageEnhance.Contrast(adjusted).enhance(contrast)
        if gamma != 1.0:
            inverse_gamma = 1.0 / gamma
            lut = [min(255, max(0, int(((value / 255.0) ** inverse_gamma) * 255.0 + 0.5))) for value in range(256)]
            red, green, blue, alpha = adjusted.split()
            adjusted = Image.merge("RGBA", (red.point(lut), green.point(lut), blue.point(lut), alpha))
        return adjusted


class ImageCache(QObject):
    pageLoaded = Signal(object)
    sourceIdle = Signal(object)

    def __init__(self, cache_size: int = 10, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cache_size = max(1, int(cache_size))
        self.generation = 0
        self.source: ImageSource | None = None
        self.image_ids: list[str] = []
        self._cache: OrderedDict[int, CachedImage] = OrderedDict()
        self._in_flight: dict[tuple[int, int], ImageSource] = {}
        self._wanted_indexes: set[int] = set()
        self._protected_indexes: set[int] = set()
        self._center_index = 0
        self._thread_pool = QThreadPool(self)
        self._adjustments = (1.0, 1.0, 1.0)
        self._render_spec: PageRenderSpec | dict[int, PageRenderSpec] | None = None

    def set_cache_size(self, cache_size: int) -> None:
        self.cache_size = max(1, int(cache_size))
        self._enforce_limit()

    def set_adjustments(self, *, brightness: float, contrast: float, gamma: float) -> None:
        adjustments = (
            max(0.1, min(3.0, float(brightness))),
            max(0.1, min(3.0, float(contrast))),
            max(0.1, min(5.0, float(gamma))),
        )
        if adjustments == self._adjustments:
            return
        self._adjustments = adjustments
        self.generation += 1
        self._cache.clear()

    def set_source(self, source: ImageSource | None, image_ids: list[str]) -> None:
        self.generation += 1
        self.source = source
        self.image_ids = list(image_ids)
        self._cache.clear()
        self._wanted_indexes.clear()
        self._protected_indexes.clear()
        self._center_index = 0

    def set_render_spec(
        self,
        render_spec: PageRenderSpec | dict[int, PageRenderSpec] | None,
    ) -> bool:
        if self._render_spec_signature(render_spec) == self._render_spec_signature(
            self._render_spec
        ):
            return False
        self._cancel_in_flight()
        self._render_spec = render_spec
        self.generation += 1
        self._cache.clear()
        return True

    @staticmethod
    def _render_spec_signature(
        render_spec: PageRenderSpec | dict[int, PageRenderSpec] | None,
    ):
        def signature(spec: PageRenderSpec):
            return (
                spec.target_pixel_size,
                spec.rotation_degrees % 360,
                spec.mode,
                round(max(0.1, spec.device_pixel_ratio) * 4) / 4,
            )

        if isinstance(render_spec, dict):
            return tuple(
                (index, signature(spec))
                for index, spec in sorted(render_spec.items())
            )
        return None if render_spec is None else signature(render_spec)

    def clear(self) -> None:
        self._cancel_in_flight()
        self.generation += 1
        self.source = None
        self.image_ids = []
        self._cache.clear()
        self._wanted_indexes.clear()
        self._protected_indexes.clear()
        self._center_index = 0

    def has_in_flight_for_source(self, source: ImageSource) -> bool:
        return any(active_source is source for active_source in self._in_flight.values())

    def wait_for_done(self, msecs: int = 5000) -> bool:
        return self._thread_pool.waitForDone(msecs)

    def get(self, page_index: int) -> CachedImage | None:
        cached = self._cache.get(page_index)
        if cached is not None:
            self._cache.move_to_end(page_index)
        return cached

    def preload_around(
        self,
        center_index: int,
        *,
        radius: int = PRELOAD_RADIUS,
        visible_indexes: tuple[int, ...] = tuple(),
    ) -> None:
        if not self.image_ids:
            return

        start = max(0, center_index - radius)
        end = min(len(self.image_ids) - 1, center_index + radius)
        wanted = set(range(start, end + 1))
        wanted.update(index for index in visible_indexes if 0 <= index < len(self.image_ids))
        self._wanted_indexes = wanted
        self._protected_indexes = set(visible_indexes)
        self._center_index = center_index
        for (generation, index), source in tuple(self._in_flight.items()):
            if generation == self.generation and index not in wanted:
                cancel = getattr(source, "cancel_image_request", None)
                if callable(cancel) and 0 <= index < len(self.image_ids):
                    cancel(self.image_ids[index])

        for index in list(self._cache):
            if index not in wanted:
                del self._cache[index]

        for index in visible_indexes:
            self.ensure_loaded(index)
        for index in range(start, end + 1):
            self.ensure_loaded(index)

        self._enforce_limit()

    def ensure_loaded(self, page_index: int) -> None:
        if self.source is None or not (0 <= page_index < len(self.image_ids)):
            return
        in_flight_key = (self.generation, page_index)
        if page_index in self._cache or in_flight_key in self._in_flight:
            return

        self._in_flight[in_flight_key] = self.source
        if page_index == self._center_index:
            priority = int(PdfRenderPriority.VIEWER_CURRENT)
        elif page_index in self._protected_indexes:
            priority = int(PdfRenderPriority.VIEWER_SPREAD_PARTNER)
        elif page_index > self._center_index:
            priority = int(PdfRenderPriority.VIEWER_NEXT)
        else:
            priority = int(PdfRenderPriority.VIEWER_PREVIOUS)
        render_spec = (
            self._render_spec.get(page_index)
            if isinstance(self._render_spec, dict)
            else self._render_spec
        )
        task = _ImageLoadTask(
            self.source,
            page_index,
            self.image_ids[page_index],
            self.generation,
            self._adjustments,
            render_spec,
            priority,
        )
        task.signals.loaded.connect(self._on_loaded)
        self._thread_pool.start(task)

    @Slot(object)
    def _on_loaded(self, result: _ImageLoadResult) -> None:
        cached = result.cached
        self._in_flight.pop((cached.generation, cached.page_index), None)
        if not self.has_in_flight_for_source(result.source):
            self.sourceIdle.emit(result.source)
        if cached.generation != self.generation:
            return
        if not (0 <= cached.page_index < len(self.image_ids)):
            return
        if self.image_ids[cached.page_index] != cached.image_id:
            return
        if self._wanted_indexes and cached.page_index not in self._wanted_indexes:
            return

        self._cache[cached.page_index] = cached
        self._cache.move_to_end(cached.page_index)
        self._enforce_limit()
        self.pageLoaded.emit(cached)

    def _enforce_limit(self) -> None:
        while len(self._cache) > self.cache_size:
            evicted = False
            for index in list(self._cache):
                if index not in self._protected_indexes:
                    del self._cache[index]
                    evicted = True
                    break
            if not evicted:
                break

    def _cancel_in_flight(self) -> None:
        for (generation, index), source in tuple(self._in_flight.items()):
            if generation != self.generation or not (0 <= index < len(self.image_ids)):
                continue
            cancel = getattr(source, "cancel_image_request", None)
            if callable(cancel):
                cancel(self.image_ids[index])
