from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from time import monotonic

from PIL import Image, ImageEnhance
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage

from .archive_backend import ArchiveErrorCode
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
    source_is_preview: bool = False
    raster_decode_revision: int = 0


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
        raster_decode_bounds: tuple[int, int] | None = None,
        raster_decode_revision: int = 0,
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
        self.raster_decode_bounds = raster_decode_bounds
        self.raster_decode_revision = int(raster_decode_revision)
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
            source_is_preview = False
            target_decoder = getattr(
                self.source,
                "open_qimage_at_most",
                None,
            )
            if (
                self.raster_decode_bounds is not None
                and callable(target_decoder)
                and self.adjustments == (1.0, 1.0, 1.0)
                and self.render_spec is None
            ):
                performance_trace.mark(
                    self.trace_id,
                    "source.target_decode.started",
                    (
                        f"{self.image_id} "
                        f"max={self.raster_decode_bounds[0]}x"
                        f"{self.raster_decode_bounds[1]}"
                    ),
                )
                decoded = target_decoder(
                    self.image_id,
                    self.raster_decode_bounds,
                )
                if decoded is not None:
                    qimage, original_size = decoded
                    source_is_preview = (
                        qimage.width(),
                        qimage.height(),
                    ) != original_size
                performance_trace.mark(
                    self.trace_id,
                    "source.target_decode.completed",
                    (
                        "fallback"
                        if qimage is None or qimage.isNull()
                        else (
                            f"{qimage.width()}x{qimage.height()} "
                            f"preview={source_is_preview}"
                        )
                    ),
                )
            open_qimage = getattr(self.source, "open_qimage", None)
            if (
                (qimage is None or qimage.isNull())
                and callable(open_qimage)
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
                source_is_preview=source_is_preview,
                raster_decode_revision=self.raster_decode_revision,
            )
        except ImageSourceError as exc:
            cancelled = exc.code in {
                PdfErrorCode.CANCELLED.value,
                ArchiveErrorCode.PROCESS_CANCELLED.value,
            }
            result = CachedImage(
                page_index=self.page_index,
                image_id=self.image_id,
                qimage=None,
                original_size=None,
                error=str(exc),
                generation=self.generation,
                render_spec_signature=self.render_spec_signature,
                raster_decode_revision=self.raster_decode_revision,
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
                raster_decode_revision=self.raster_decode_revision,
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
            tuple[_ImageLoadTask, int],
        ] = {}
        self._cancel_requested_tasks: set[tuple[int, int]] = set()
        self._wanted_indexes: set[int] = set()
        self._protected_indexes: set[int] = set()
        self._center_index = 0
        self._preferred_direction = 0
        self._configured_prefetch_order = False
        self._configured_prefetch_ranks: dict[int, int] = {}
        self._configured_prefetch_units: tuple[tuple[int, ...], ...] = ()
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._coordinator = image_work_coordinator
        self._adjustments = (1.0, 1.0, 1.0)
        self._render_spec: PageRenderSpec | dict[int, PageRenderSpec] | None = None
        self._raster_decode_bounds: tuple[int, int] | None = None
        self._raster_decode_revision = 0
        self._force_full_resolution: set[int] = set()
        self._trace_id = 0

    def set_cache_size(self, cache_size: int) -> None:
        self.cache_size = max(1, int(cache_size))
        self._enforce_limit()

    @property
    def cache_byte_budget_mib(self) -> int:
        return self._cache_byte_budget // (1024 * 1024)

    @property
    def cache_byte_budget_bytes(self) -> int:
        return self._cache_byte_budget

    @property
    def cache_bytes(self) -> int:
        return self._cache_bytes

    def set_cache_byte_budget_mib(self, memory_mib: int) -> None:
        self.set_cache_byte_budget_bytes(
            max(
                64,
                min(32768, int(memory_mib)),
            )
            * 1024
            * 1024
        )

    def set_cache_byte_budget_bytes(self, memory_bytes: int) -> None:
        self._cache_byte_budget = max(
            1,
            int(memory_bytes),
        )
        limited_wanted = self._limit_wanted_to_capacity(
            self._wanted_indexes
        )
        if limited_wanted != self._wanted_indexes:
            self._wanted_indexes = limited_wanted
            self._cancel_unwanted_in_flight()
        self._enforce_limit()

    def set_raster_decode_bounds(
        self,
        maximum_size: tuple[int, int] | None,
    ) -> bool:
        normalized = (
            None
            if maximum_size is None
            else (
                max(1, int(maximum_size[0])),
                max(1, int(maximum_size[1])),
            )
        )
        if normalized == self._raster_decode_bounds:
            return False
        self._raster_decode_bounds = normalized
        self._raster_decode_revision += 1
        self._cancel_in_flight()
        for index, cached in tuple(self._cache.items()):
            if cached.source_is_preview:
                self._evict_cached(index)
        return True

    def ensure_full_resolution(self, page_index: int) -> bool:
        cached = self._cache.get(int(page_index))
        if cached is None or not cached.source_is_preview:
            return False
        self._force_full_resolution.add(int(page_index))
        self.ensure_loaded(int(page_index))
        return True

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
        self._preferred_direction = 0
        self._configured_prefetch_order = False
        self._configured_prefetch_ranks.clear()
        self._configured_prefetch_units = ()
        self._force_full_resolution.clear()
        self._trace_id = int(trace_id)

    def suspend_for_book_runtime(self) -> None:
        """Stop the legacy raster pipeline while a book runtime owns ZIP work.

        The source remains attached for model metadata only. Advancing the
        generation rejects already-queued legacy results; ZIP display work
        must not return to this cache because of a feature or decoder error.
        """
        self._cancel_in_flight()
        self.generation += 1
        self._clear_cache()
        self._wanted_indexes.clear()
        self._protected_indexes.clear()
        self._center_index = 0
        self._preferred_direction = 0
        self._configured_prefetch_order = False
        self._configured_prefetch_ranks.clear()
        self._configured_prefetch_units = ()
        self._force_full_resolution.clear()

    def suspend_raster_work(self) -> None:
        """Backward-compatible alias for older tests and historical tooling."""
        self.suspend_for_book_runtime()

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
        self._preferred_direction = 0
        self._configured_prefetch_order = False
        self._configured_prefetch_ranks.clear()
        self._configured_prefetch_units = ()
        self._force_full_resolution.clear()

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

    def contains(self, page_index: int) -> bool:
        return int(page_index) in self._cache

    def has_pending_page(self, page_index: int) -> bool:
        key = (self.generation, int(page_index))
        return key in self._tasks or key in self._in_flight

    def retain_visible_only(
        self,
        center_index: int,
        visible_indexes: tuple[int, ...],
    ) -> None:
        """Release temporary prefetch protection without starting new work."""
        if not self.image_ids:
            self._protected_indexes.clear()
            self._wanted_indexes.clear()
            return
        center = max(0, min(int(center_index), len(self.image_ids) - 1))
        visible = {
            int(index)
            for index in visible_indexes
            if 0 <= int(index) < len(self.image_ids)
        }
        visible.add(center)
        self._center_index = center
        self._protected_indexes = visible
        self._wanted_indexes = set(visible)
        self._configured_prefetch_order = False
        self._configured_prefetch_ranks.clear()
        self._configured_prefetch_units = tuple()
        self._cancel_unwanted_in_flight()
        self._enforce_limit()

    def preload_around(
        self,
        center_index: int,
        *,
        radius: int = PRELOAD_RADIUS,
        visible_indexes: tuple[int, ...] = tuple(),
        preferred_direction: int = 0,
        rolling_indexes: tuple[int, ...] = tuple(),
        prefetch_indexes: tuple[int, ...] | None = None,
        prefetch_units: tuple[tuple[int, ...], ...] = tuple(),
    ) -> None:
        if not self.image_ids:
            return

        start = max(0, center_index - radius)
        end = min(len(self.image_ids) - 1, center_index + radius)
        candidates = (
            tuple(range(start, end + 1))
            if prefetch_indexes is None
            else tuple(
                index
                for index in prefetch_indexes
                if 0 <= index < len(self.image_ids)
            )
        )
        wanted = set(candidates)
        wanted.add(center_index)
        wanted.update(index for index in visible_indexes if 0 <= index < len(self.image_ids))
        wanted.update(
            index
            for index in rolling_indexes
            if 0 <= index < len(self.image_ids)
        )
        self._protected_indexes = set(visible_indexes)
        self._center_index = center_index
        self._preferred_direction = max(
            -1,
            min(1, int(preferred_direction)),
        )
        self._configured_prefetch_order = prefetch_indexes is not None
        self._configured_prefetch_ranks.clear()
        if prefetch_indexes is not None:
            for rank, index in enumerate(candidates):
                self._configured_prefetch_ranks.setdefault(index, rank)
        self._configured_prefetch_units = tuple(
            tuple(
                dict.fromkeys(
                    index
                    for index in unit
                    if 0 <= index < len(self.image_ids)
                )
            )
            for unit in prefetch_units
            if unit
        )
        self._wanted_indexes = self._limit_wanted_to_capacity(wanted)
        self._cancel_unwanted_in_flight()

        self.ensure_loaded(center_index)
        limited_after_current = self._limit_wanted_to_capacity(
            self._wanted_indexes
        )
        if limited_after_current != self._wanted_indexes:
            self._wanted_indexes = limited_after_current
            self._cancel_unwanted_in_flight()
        for index in visible_indexes:
            if index != center_index and index in self._wanted_indexes:
                self.ensure_loaded(index)
        for index in rolling_indexes:
            if (
                index != center_index
                and index not in self._protected_indexes
                and index in self._wanted_indexes
            ):
                self.ensure_loaded(index)
        ordered_candidates: tuple[int, ...] | list[int] = candidates
        if (
            prefetch_indexes is None
            and bool(getattr(self.source, "supports_target_rendering", False))
        ):
            ordered_candidates = sorted(
                candidates,
                key=self._pdf_prefetch_rank,
            )
        for index in ordered_candidates:
            if index in self._wanted_indexes:
                self.ensure_loaded(index)

        self._enforce_limit()

    def _limit_wanted_to_capacity(self, wanted: set[int]) -> set[int]:
        known_costs = [
            byte_cost
            for byte_cost in self._cache_entry_bytes.values()
            if byte_cost > 0
        ]
        if not known_costs:
            return set(wanted)
        # ``cache_size`` is the legacy manual count limit.  A configured
        # forward/backward plan can legitimately contain more entries (for
        # example current + 6 forward + 4 backward = 11).  In that case the
        # byte budget remains the hard bound; silently clipping the plan back
        # to the legacy count makes the settings ineffective even for small
        # images.
        requested_count_limit = max(self.cache_size, len(wanted))
        entry_capacity = max(
            1,
            min(
                requested_count_limit,
                self._cache_byte_budget // max(known_costs),
            ),
        )
        required = {
            index
            for index in self._protected_indexes | {self._center_index}
            if 0 <= index < len(self.image_ids)
        }
        entry_capacity = max(entry_capacity, len(required))
        optional = sorted(
            wanted - required,
            key=self._pdf_prefetch_rank,
        )
        remaining = max(0, entry_capacity - len(required))
        selected: set[int] = set()
        grouped: set[int] = set()
        optional_set = set(optional)
        for unit in self._configured_prefetch_units:
            candidates = (set(unit) & optional_set) - selected
            grouped.update(set(unit) & optional_set)
            if candidates and len(candidates) <= remaining:
                selected.update(candidates)
                remaining -= len(candidates)
        if remaining:
            for index in optional:
                if index in grouped:
                    continue
                selected.add(index)
                remaining -= 1
                if remaining <= 0:
                    break
        return required | selected

    def _cancel_unwanted_in_flight(self) -> None:
        for (generation, index), source in tuple(self._in_flight.items()):
            if generation != self.generation or index in self._wanted_indexes:
                continue
            cancel = getattr(source, "cancel_image_request", None)
            if callable(cancel) and 0 <= index < len(self.image_ids):
                cancel(self.image_ids[index])
            task_entry = self._tasks.get((generation, index))
            if task_entry is None:
                continue
            task, _priority = task_entry
            if self._try_take_task(task):
                self._tasks.pop((generation, index), None)
                self._in_flight.pop((generation, index), None)
            elif callable(cancel):
                # The same index can become wanted again before a running
                # archive/PDF request reports cancellation.
                self._cancel_requested_tasks.add((generation, index))

    def ensure_loaded(self, page_index: int) -> None:
        if self.source is None or not (0 <= page_index < len(self.image_ids)):
            return
        in_flight_key = (self.generation, page_index)
        cached = self._cache.get(page_index)
        force_full_resolution = (
            page_index in self._force_full_resolution
            and cached is not None
            and cached.source_is_preview
        )
        if cached is not None and not force_full_resolution:
            return

        if page_index == self._center_index:
            priority = int(PdfRenderPriority.VIEWER_CURRENT)
            work_priority = ImageWorkPriority.VIEWER_CURRENT
        elif page_index in self._protected_indexes:
            priority = int(PdfRenderPriority.VIEWER_SPREAD_PARTNER)
            work_priority = int(ImageWorkPriority.VIEWER_SPREAD_PARTNER)
        elif (
            bool(getattr(self.source, "supports_target_rendering", False))
            or self._configured_prefetch_order
        ):
            priority, work_priority = self._pdf_prefetch_priority(page_index)
        elif page_index > self._center_index:
            priority = int(PdfRenderPriority.VIEWER_NEXT)
            work_priority = int(ImageWorkPriority.VIEWER_NEXT)
        else:
            priority = int(PdfRenderPriority.VIEWER_PREVIOUS)
            work_priority = int(ImageWorkPriority.VIEWER_PREVIOUS)
        existing = self._tasks.get(in_flight_key)
        if existing is not None:
            task, old_priority = existing
            if (
                work_priority != old_priority
                and self._try_take_task(task)
            ):
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
            (
                None
                if force_full_resolution
                else self._raster_decode_bounds
            ),
            self._raster_decode_revision,
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

    def _pdf_prefetch_priority(self, page_index: int) -> tuple[int, int]:
        queue_rank = self._pdf_prefetch_rank(page_index)
        render_rank = self._directional_prefetch_rank(page_index)
        return (
            int(PdfRenderPriority.VIEWER_NEXT) + render_rank,
            int(ImageWorkPriority.VIEWER_NEXT) - queue_rank,
        )

    def _pdf_prefetch_rank(self, page_index: int) -> int:
        configured_rank = self._configured_prefetch_ranks.get(page_index)
        if configured_rank is not None:
            return configured_rank
        return self._directional_prefetch_rank(page_index)

    def _directional_prefetch_rank(self, page_index: int) -> int:
        distance = max(1, abs(page_index - self._center_index))
        if self._preferred_direction:
            in_preferred_direction = (
                page_index - self._center_index
            ) * self._preferred_direction > 0
            return (
                distance - 1
                if in_preferred_direction
                else len(self.image_ids) + distance - 1
            )
        return (distance - 1) * 2 + int(page_index < self._center_index)

    def _start_task(
        self,
        task: _ImageLoadTask,
        work_priority: int,
    ) -> None:
        if self._coordinator is not None:
            if self._coordinator.start_viewer(task, work_priority):
                return
            task_key = (task.generation, task.page_index)
            task_entry = self._tasks.get(task_key)
            if task_entry is not None and task_entry[0] is task:
                self._tasks.pop(task_key, None)
                self._in_flight.pop(task_key, None)
                self._cancel_requested_tasks.discard(task_key)
            task.finished.set()
            return
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
        task_key = (cached.generation, cached.page_index)
        retry_cancelled = task_key in self._cancel_requested_tasks
        self._cancel_requested_tasks.discard(task_key)
        self._in_flight.pop(task_key, None)
        self._tasks.pop(task_key, None)
        if not cached.source_is_preview:
            self._force_full_resolution.discard(cached.page_index)
        if result.cancelled:
            if (
                retry_cancelled
                and cached.generation == self.generation
                and result.source is self.source
                and 0 <= cached.page_index < len(self.image_ids)
                and self.image_ids[cached.page_index] == cached.image_id
                and cached.page_index in self._wanted_indexes
            ):
                self.ensure_loaded(cached.page_index)
            if not self.has_in_flight_for_source(result.source):
                self.sourceIdle.emit(result.source)
            return
        if not self.has_in_flight_for_source(result.source):
            self.sourceIdle.emit(result.source)
        if cached.generation != self.generation:
            return
        if (
            cached.source_is_preview
            and cached.raster_decode_revision
            != self._raster_decode_revision
        ):
            if cached.page_index in self._wanted_indexes:
                self.ensure_loaded(cached.page_index)
            return
        if not (0 <= cached.page_index < len(self.image_ids)):
            return
        if self.image_ids[cached.page_index] != cached.image_id:
            return
        if self._wanted_indexes and cached.page_index not in self._wanted_indexes:
            return

        self._store_cached(cached)
        limited_wanted = self._limit_wanted_to_capacity(
            self._wanted_indexes
        )
        if limited_wanted != self._wanted_indexes:
            self._wanted_indexes = limited_wanted
            self._cancel_unwanted_in_flight()
        performance_trace.mark(
            self._trace_id,
            "image_cache.stored",
            f"page={cached.page_index}",
        )
        self._enforce_limit()
        self.pageLoaded.emit(cached)

    def _enforce_limit(self) -> None:
        active_count_limit = max(
            self.cache_size,
            len(self._wanted_indexes),
        )
        while len(self._cache) > active_count_limit:
            candidate = self._oldest_evictable_index()
            if candidate is None:
                break
            self._evict_cached(candidate)
        while (
            self._cache_bytes > self._cache_byte_budget
            and self._cache
        ):
            candidate = self._oldest_evictable_index()
            if candidate is None:
                break
            self._evict_cached(candidate)

    def _oldest_evictable_index(self) -> int | None:
        candidates = [
            index
            for index in self._cache
            if (
                index != self._center_index
                and index not in self._protected_indexes
            )
        ]
        if candidates:
            if not self._wanted_indexes:
                return candidates[0]
            for index in candidates:
                if index not in self._wanted_indexes:
                    return index
            # Configured prefetch is submitted from nearest to farthest.  A
            # byte-constrained cache must retain that priority instead of
            # letting later, distant completions evict the next display unit.
            return max(candidates, key=self._pdf_prefetch_rank)
        # Every remaining entry belongs to the current display unit.  Keep the
        # unit complete even when one unusually large spread exceeds budget.
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
            elif callable(cancel):
                self._cancel_requested_tasks.add((generation, index))
