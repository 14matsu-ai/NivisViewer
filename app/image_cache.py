from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from time import monotonic

from PIL import Image, ImageEnhance
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage

from .image_work_coordinator import ImageWorkCoordinator, ImageWorkPriority
from .image_source import ImageSource, ImageSourceError
from .pdf_backend import PageRenderSpec, PdfErrorCode, PdfRenderPriority
from .performance_trace import performance_trace
from .thumbnail_render import pil_to_qimage


PRELOAD_RADIUS = 3
_DEFAULT_CACHE_BYTE_BUDGET = 256 * 1024 * 1024


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
    render_spec_signature: tuple[object, ...] | None = None


@dataclass(frozen=True)
class _ImageLoadResult:
    cached: CachedImage
    source: ImageSource
    cancelled: bool = False


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
        render_spec_signature: tuple[object, ...] | None,
        priority: int,
        trace_id: int = 0,
    ) -> None:
        super().__init__()
        self.source = source
        self.page_index = page_index
        self.image_id = image_id
        self.generation = generation
        self.adjustments = adjustments
        self.render_spec = render_spec
        self.render_spec_signature = render_spec_signature
        self.priority = priority
        self.trace_id = int(trace_id)
        self.signals = _ImageLoadSignals()
        self.finished = Event()

    @Slot()
    def run(self) -> None:
        try:
            self._run_load()
        finally:
            self.finished.set()

    def _run_load(self) -> None:
        performance_trace.mark(
            self.trace_id,
            "viewer.worker.started",
            f"page={self.page_index}",
        )
        cancelled = False
        try:
            qimage: QImage | None = None
            original_size: tuple[int, int] | None = None
            rendered_size: tuple[int, int] | None = None
            open_qimage = getattr(self.source, "open_qimage", None)
            if (
                callable(open_qimage)
                and Path(self.image_id).suffix.casefold() == ".webp"
                and self.adjustments == (1.0, 1.0, 1.0)
                and self.render_spec is None
            ):
                performance_trace.mark(
                    self.trace_id,
                    "webp.qimagereader.started",
                    self.image_id,
                )
                qimage = open_qimage(self.image_id)
                performance_trace.mark(
                    self.trace_id,
                    "webp.qimagereader.completed",
                    (
                        "fallback"
                        if qimage is None or qimage.isNull()
                        else f"{qimage.width()}x{qimage.height()}"
                    ),
                )
                if qimage is not None and not qimage.isNull():
                    original_size = (qimage.width(), qimage.height())
            renderer = getattr(self.source, "open_image_for_render", None)
            if qimage is None or qimage.isNull():
                performance_trace.mark(
                    self.trace_id,
                    "source.bytes_decode.started",
                    self.image_id,
                )
                if callable(renderer):
                    image = renderer(
                        self.image_id,
                        self.render_spec,
                        priority=self.priority,
                        generation=self.generation,
                    )
                else:
                    image = self.source.open_image(self.image_id)
                performance_trace.mark(
                    self.trace_id,
                    "source.bytes_decode.completed",
                    f"mode={image.mode}",
                )
                adjusted: Image.Image | None = None
                try:
                    adjusted = self._apply_adjustments(image)
                    performance_trace.mark(
                        self.trace_id,
                        "pillow_to_qimage.started",
                        f"mode={adjusted.mode}",
                    )
                    qimage = self._pil_to_qimage(adjusted)
                    performance_trace.mark(
                        self.trace_id,
                        "pillow_to_qimage.completed",
                    )
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
                render_spec_signature=self.render_spec_signature,
            )
        except ImageSourceError as exc:
            cancelled = exc.code == PdfErrorCode.CANCELLED.value
            result = CachedImage(
                page_index=self.page_index,
                image_id=self.image_id,
                qimage=None,
                original_size=None,
                error=str(exc),
                generation=self.generation,
                render_spec_signature=self.render_spec_signature,
            )
        except Exception as exc:
            result = CachedImage(
                page_index=self.page_index,
                image_id=self.image_id,
                qimage=None,
                original_size=None,
                error=str(exc),
                generation=self.generation,
                render_spec_signature=self.render_spec_signature,
            )
        self.signals.loaded.emit(
            _ImageLoadResult(result, self.source, cancelled=cancelled)
        )

    @staticmethod
    def _pil_to_qimage(image: Image.Image) -> QImage:
        return pil_to_qimage(image)

    def _apply_adjustments(self, image: Image.Image) -> Image.Image:
        brightness, contrast, gamma = self.adjustments
        if (brightness, contrast, gamma) == (1.0, 1.0, 1.0):
            return image
        adjusted = image
        if brightness != 1.0:
            adjusted = ImageEnhance.Brightness(adjusted).enhance(brightness)
        if contrast != 1.0:
            adjusted = ImageEnhance.Contrast(adjusted).enhance(contrast)
        if gamma != 1.0:
            inverse_gamma = 1.0 / gamma
            lut = [min(255, max(0, int(((value / 255.0) ** inverse_gamma) * 255.0 + 0.5))) for value in range(256)]
            if adjusted.mode == "RGBA":
                red, green, blue, alpha = adjusted.split()
                adjusted = Image.merge(
                    "RGBA",
                    (
                        red.point(lut),
                        green.point(lut),
                        blue.point(lut),
                        alpha,
                    ),
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


class ImageCache(QObject):
    pageLoaded = Signal(object)
    sourceIdle = Signal(object)

    def __init__(
        self,
        cache_size: int = 10,
        parent: QObject | None = None,
        *,
        image_work_coordinator: ImageWorkCoordinator | None = None,
    ) -> None:
        super().__init__(parent)
        self.cache_size = max(1, int(cache_size))
        self.generation = 0
        self.source: ImageSource | None = None
        self.image_ids: list[str] = []
        self._cache: OrderedDict[int, CachedImage] = OrderedDict()
        self._cache_entry_bytes: dict[int, int] = {}
        self._cache_bytes = 0
        self._cache_byte_budget = _DEFAULT_CACHE_BYTE_BUDGET
        self._in_flight: dict[tuple[int, int], ImageSource] = {}
        self._tasks: dict[
            tuple[int, int],
            tuple[_ImageLoadTask, ImageWorkPriority],
        ] = {}
        self._wanted_indexes: set[int] = set()
        self._protected_indexes: set[int] = set()
        self._center_index = 0
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._coordinator = image_work_coordinator
        self._adjustments = (1.0, 1.0, 1.0)
        self._render_spec: PageRenderSpec | dict[int, PageRenderSpec] | None = None
        self._trace_id = 0

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
        self._cancel_in_flight()
        self._adjustments = adjustments
        self.generation += 1
        self._clear_cache()

    def set_source(
        self,
        source: ImageSource | None,
        image_ids: list[str],
        *,
        trace_id: int = 0,
    ) -> None:
        self._cancel_in_flight()
        self.generation += 1
        self.source = source
        self.image_ids = list(image_ids)
        self._clear_cache()
        self._wanted_indexes.clear()
        self._protected_indexes.clear()
        self._center_index = 0
        self._trace_id = int(trace_id)

    def set_render_spec(
        self,
        render_spec: PageRenderSpec | dict[int, PageRenderSpec] | None,
    ) -> bool:
        if self._render_spec_signature(render_spec) == self._render_spec_signature(
            self._render_spec
        ):
            return False
        requested_signatures = (
            {
                index: self._page_render_spec_signature(spec)
                for index, spec in render_spec.items()
            }
            if isinstance(render_spec, dict)
            else None
        )
        scalar_signature = (
            None
            if isinstance(render_spec, dict) or render_spec is None
            else self._page_render_spec_signature(render_spec)
        )
        missing = object()

        def artifact_spec_changed(
            index: int,
            artifact_signature: tuple[object, ...] | None,
        ) -> bool:
            requested_signature = (
                requested_signatures.get(index, missing)
                if requested_signatures is not None
                else scalar_signature
            )
            return (
                requested_signature is not missing
                and artifact_signature != requested_signature
            )

        shared_spec_changed = False
        if isinstance(render_spec, dict) and isinstance(self._render_spec, dict):
            for index in self._wanted_indexes:
                if index not in render_spec or index not in self._render_spec:
                    continue
                if (
                    self._page_render_spec_signature(render_spec[index])
                    != self._page_render_spec_signature(self._render_spec[index])
                ):
                    shared_spec_changed = True
                    break
        else:
            shared_spec_changed = True

        cached_spec_changed = any(
            artifact_spec_changed(index, cached.render_spec_signature)
            for index, cached in self._cache.items()
        )
        task_spec_changed = any(
            generation == self.generation
            and artifact_spec_changed(index, task.render_spec_signature)
            for (generation, index), (task, _priority) in self._tasks.items()
        )
        if not (
            shared_spec_changed
            or cached_spec_changed
            or task_spec_changed
        ):
            self._render_spec = render_spec
            return False
        self._cancel_in_flight()
        self._render_spec = render_spec
        self.generation += 1
        self._clear_cache()
        return True

    @staticmethod
    def _page_render_spec_signature(
        render_spec: PageRenderSpec,
    ) -> tuple[object, ...]:
        return (
            render_spec.target_pixel_size,
            render_spec.rotation_degrees % 360,
            render_spec.mode,
            round(max(0.1, render_spec.device_pixel_ratio) * 4) / 4,
        )

    @staticmethod
    def _render_spec_signature(
        render_spec: PageRenderSpec | dict[int, PageRenderSpec] | None,
    ):
        if isinstance(render_spec, dict):
            return tuple(
                (index, ImageCache._page_render_spec_signature(spec))
                for index, spec in sorted(render_spec.items())
            )
        return (
            None
            if render_spec is None
            else ImageCache._page_render_spec_signature(render_spec)
        )

    def clear(self) -> None:
        self._cancel_in_flight()
        self.generation += 1
        self.source = None
        self.image_ids = []
        self._clear_cache()
        self._wanted_indexes.clear()
        self._protected_indexes.clear()
        self._center_index = 0

    def has_in_flight_for_source(self, source: ImageSource) -> bool:
        return any(active_source is source for active_source in self._in_flight.values())

    def has_unfinished_tasks(self) -> bool:
        return any(
            not task.finished.is_set()
            for task, _priority in self._tasks.values()
        )

    def wait_for_owned_tasks(self, msecs: int = 5000) -> bool:
        tasks = tuple(task for task, _priority in self._tasks.values())
        deadline = monotonic() + max(0, int(msecs)) / 1000
        for task in tasks:
            remaining = max(0.0, deadline - monotonic())
            if not task.finished.wait(remaining):
                return False
        return True

    def wait_for_done(self, msecs: int = 5000) -> bool:
        if self._coordinator is not None:
            return self._coordinator.wait_for_viewer(msecs)
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
                task_entry = self._tasks.get((generation, index))
                if task_entry is not None:
                    task, _priority = task_entry
                    if self._try_take_task(task):
                        self._tasks.pop((generation, index), None)
                        self._in_flight.pop((generation, index), None)

        self.ensure_loaded(center_index)
        for index in visible_indexes:
            if index != center_index:
                self.ensure_loaded(index)
        for index in range(start, end + 1):
            self.ensure_loaded(index)

        self._enforce_limit()

    def ensure_loaded(self, page_index: int) -> None:
        if self.source is None or not (0 <= page_index < len(self.image_ids)):
            return
        in_flight_key = (self.generation, page_index)
        if page_index in self._cache:
            return

        if page_index == self._center_index:
            priority = int(PdfRenderPriority.VIEWER_CURRENT)
            work_priority = ImageWorkPriority.VIEWER_CURRENT
        elif page_index in self._protected_indexes:
            priority = int(PdfRenderPriority.VIEWER_SPREAD_PARTNER)
            work_priority = ImageWorkPriority.VIEWER_SPREAD_PARTNER
        elif page_index > self._center_index:
            priority = int(PdfRenderPriority.VIEWER_NEXT)
            work_priority = ImageWorkPriority.VIEWER_NEXT
        else:
            priority = int(PdfRenderPriority.VIEWER_PREVIOUS)
            work_priority = ImageWorkPriority.VIEWER_PREVIOUS
        existing = self._tasks.get(in_flight_key)
        if existing is not None:
            task, old_priority = existing
            if work_priority > old_priority and self._try_take_task(task):
                task.priority = priority
                self._tasks[in_flight_key] = (task, work_priority)
                self._start_task(task, work_priority)
            return

        self._in_flight[in_flight_key] = self.source
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
            (
                None
                if render_spec is None
                else self._page_render_spec_signature(render_spec)
            ),
            priority,
            self._trace_id,
        )
        performance_trace.mark(
            self._trace_id,
            "viewer.request.registered",
            f"page={page_index} priority={priority}",
        )
        task.signals.loaded.connect(self._on_loaded)
        self._tasks[in_flight_key] = (task, work_priority)
        self._start_task(task, work_priority)

    def _start_task(
        self,
        task: _ImageLoadTask,
        work_priority: ImageWorkPriority,
    ) -> None:
        if self._coordinator is not None:
            self._coordinator.start_viewer(task, work_priority)
        else:
            self._thread_pool.start(task, int(work_priority))

    def _try_take_task(self, task: _ImageLoadTask) -> bool:
        if self._coordinator is not None:
            return self._coordinator.try_take_viewer(task)
        try:
            return self._thread_pool.tryTake(task)
        except RuntimeError:
            return False

    @Slot(object)
    def _on_loaded(self, result: _ImageLoadResult) -> None:
        cached = result.cached
        self._in_flight.pop((cached.generation, cached.page_index), None)
        self._tasks.pop((cached.generation, cached.page_index), None)
        if not self.has_in_flight_for_source(result.source):
            self.sourceIdle.emit(result.source)
        if result.cancelled:
            return
        if cached.generation != self.generation:
            return
        if not (0 <= cached.page_index < len(self.image_ids)):
            return
        if self.image_ids[cached.page_index] != cached.image_id:
            return
        if self._wanted_indexes and cached.page_index not in self._wanted_indexes:
            return

        self._store_cached(cached)
        performance_trace.mark(
            self._trace_id,
            "image_cache.stored",
            f"page={cached.page_index}",
        )
        self._enforce_limit()
        self.pageLoaded.emit(cached)

    def _enforce_limit(self) -> None:
        while len(self._cache) > self.cache_size:
            candidate = self._oldest_evictable_index()
            if candidate is None:
                break
            self._evict_cached(candidate)
        while (
            self._cache_bytes > self._cache_byte_budget
            and len(self._cache) > 1
        ):
            candidate = self._oldest_evictable_index()
            if candidate is None:
                break
            self._evict_cached(candidate)

    def _oldest_evictable_index(self) -> int | None:
        for index in self._cache:
            if (
                index != self._center_index
                and index not in self._protected_indexes
            ):
                return index
        for index in self._cache:
            if index != self._center_index:
                return index
        return None

    def _store_cached(self, cached: CachedImage) -> None:
        index = cached.page_index
        if index in self._cache:
            self._cache.pop(index)
            self._cache_bytes -= self._cache_entry_bytes.pop(index, 0)
        estimated_bytes = self._estimate_cached_bytes(cached)
        self._cache[index] = cached
        self._cache_entry_bytes[index] = estimated_bytes
        self._cache_bytes += estimated_bytes
        self._cache.move_to_end(index)

    def _evict_cached(self, index: int) -> None:
        self._cache.pop(index, None)
        self._cache_bytes -= self._cache_entry_bytes.pop(index, 0)
        self._cache_bytes = max(0, self._cache_bytes)

    def _clear_cache(self) -> None:
        self._cache.clear()
        self._cache_entry_bytes.clear()
        self._cache_bytes = 0

    @staticmethod
    def _estimate_cached_bytes(cached: CachedImage) -> int:
        image = cached.qimage
        if image is None or image.isNull():
            return 0
        try:
            return max(0, int(image.sizeInBytes()))
        except (AttributeError, TypeError):
            return max(0, int(image.bytesPerLine()) * int(image.height()))

    def _cancel_in_flight(self) -> None:
        for (generation, index), source in tuple(self._in_flight.items()):
            if generation != self.generation or not (0 <= index < len(self.image_ids)):
                continue
            cancel = getattr(source, "cancel_image_request", None)
            if callable(cancel):
                cancel(self.image_ids[index])
            task_entry = self._tasks.get((generation, index))
            if task_entry is None:
                continue
            task, _priority = task_entry
            if self._try_take_task(task):
                self._tasks.pop((generation, index), None)
                self._in_flight.pop((generation, index), None)
