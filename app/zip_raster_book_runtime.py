"""Book-scoped ZIP raster runtime derived from ZipPlaFork's Viewer structure.

The one-active-job, replaceable page work order, end-to-end page job, and
priority-coupled cache lifecycle are structural ports from ZipPlaFork revision
07955f5267e2fb92d6fc6e40fde2507d8fb07b3b (AGPL-3.0-or-later):

* ``ViewerForm.bmwLoadEachPage_DoWork`` / ``SetNewResizedImage``
* ``ViewerForm.SetBackgroundMode`` / ``priorityLevel``
* ``ViewerForm.ReduceUsingMemory``
* ``BackgroundMultiWorker.SetWorksOrder``

Qt thread-affinity, request/source generations, atomic frame publication, and
the concrete Pillow/QImage implementation are NivisViewer adaptations.  The
complete source/method/license map is in docs/ZIPPLAFORK_COMPARISON.md.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from threading import Event, Lock
from time import monotonic

from PIL import Image, ImageEnhance
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QImage, QPixmap

from .archive_backend import ArchiveErrorCode
from .image_source import ImageSource, ImageSourceError, ZipImageSource
from .image_work_coordinator import ImageWorkCoordinator, ImageWorkPriority
from .thumbnail_render import pil_to_qimage
from .viewer_render import ViewerRenderKey, normalize_resampling_mode, render_qimage
from .viewer_widget import calculate_spread_layout


_DEFAULT_CACHE_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class ZipRasterPage:
    page_index: int
    image_id: str
    known_size: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        page_index = int(self.page_index)
        image_id = str(self.image_id)
        if page_index < 0:
            raise ValueError("page_index must be non-negative")
        if not image_id:
            raise ValueError("image_id must not be empty")
        known_size = self.known_size
        if known_size is not None:
            known_size = (
                max(1, int(known_size[0])),
                max(1, int(known_size[1])),
            )
        object.__setattr__(self, "page_index", page_index)
        object.__setattr__(self, "image_id", image_id)
        object.__setattr__(self, "known_size", known_size)


@dataclass(frozen=True)
class ZipRasterDisplayUnit:
    start_index: int
    pages: tuple[ZipRasterPage, ...]
    is_single: bool

    def __post_init__(self) -> None:
        pages = tuple(self.pages)
        if not pages or len(pages) > 2:
            raise ValueError("a display unit must contain one or two pages")
        if any(not isinstance(page, ZipRasterPage) for page in pages):
            raise TypeError("pages must contain ZipRasterPage values")
        object.__setattr__(self, "start_index", int(self.start_index))
        object.__setattr__(self, "pages", pages)
        object.__setattr__(self, "is_single", bool(self.is_single))

    @property
    def identity(self) -> tuple[tuple[int, str], ...]:
        return tuple((page.page_index, page.image_id) for page in self.pages)


@dataclass(frozen=True)
class ZipRasterRenderSpec:
    viewport_size: tuple[int, int]
    device_pixel_ratio: float = 1.0
    fit_mode: str = "fit_window"
    manual_zoom: float = 1.0
    gap: int = 0
    join_spread_pages: bool = False
    horizontal_alignment: str = "center"
    rotation: int = 0
    resampling_mode: str = "standard"
    smooth_scaling: bool = True
    split_wide_image: bool = False
    reading_direction: str = "ltr"
    brightness: float = 1.0
    contrast: float = 1.0
    gamma: float = 1.0
    decoder_maximum_size: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        viewport = (
            max(1, int(self.viewport_size[0])),
            max(1, int(self.viewport_size[1])),
        )
        dpr = float(self.device_pixel_ratio)
        if not isfinite(dpr) or dpr <= 0:
            raise ValueError("device_pixel_ratio must be positive and finite")
        alignment = str(self.horizontal_alignment)
        if alignment not in {"left", "center", "right"}:
            alignment = "center"
        direction = str(self.reading_direction)
        if direction not in {"ltr", "rtl"}:
            direction = "ltr"
        decoder_size = self.decoder_maximum_size
        if decoder_size is not None:
            decoder_size = (
                max(1, int(decoder_size[0])),
                max(1, int(decoder_size[1])),
            )
        object.__setattr__(self, "viewport_size", viewport)
        object.__setattr__(self, "device_pixel_ratio", dpr)
        object.__setattr__(self, "fit_mode", str(self.fit_mode))
        object.__setattr__(
            self,
            "manual_zoom",
            min(8.0, max(0.05, float(self.manual_zoom))),
        )
        object.__setattr__(self, "gap", max(0, int(self.gap)))
        object.__setattr__(
            self,
            "join_spread_pages",
            bool(self.join_spread_pages),
        )
        object.__setattr__(self, "horizontal_alignment", alignment)
        object.__setattr__(self, "rotation", int(self.rotation) % 360)
        object.__setattr__(
            self,
            "resampling_mode",
            normalize_resampling_mode(self.resampling_mode),
        )
        object.__setattr__(self, "smooth_scaling", bool(self.smooth_scaling))
        object.__setattr__(
            self,
            "split_wide_image",
            bool(self.split_wide_image),
        )
        object.__setattr__(self, "reading_direction", direction)
        object.__setattr__(
            self,
            "brightness",
            min(3.0, max(0.1, float(self.brightness))),
        )
        object.__setattr__(
            self,
            "contrast",
            min(3.0, max(0.1, float(self.contrast))),
        )
        object.__setattr__(
            self,
            "gamma",
            min(5.0, max(0.1, float(self.gamma))),
        )
        object.__setattr__(self, "decoder_maximum_size", decoder_size)

    @property
    def adjustments(self) -> tuple[float, float, float]:
        return self.brightness, self.contrast, self.gamma


@dataclass(frozen=True)
class ZipRasterRequest:
    source_epoch: int
    request_id: int
    current: ZipRasterDisplayUnit
    work_order: tuple[ZipRasterDisplayUnit, ...]
    render_spec: ZipRasterRenderSpec
    navigation_direction: int = 0

    def __post_init__(self) -> None:
        order = tuple(self.work_order)
        if not order:
            order = (self.current,)
        if order[0].identity != self.current.identity:
            order = (self.current,) + tuple(
                unit for unit in order if unit.identity != self.current.identity
            )
        seen: set[tuple[tuple[int, str], ...]] = set()
        normalized: list[ZipRasterDisplayUnit] = []
        for unit in order:
            if unit.identity in seen:
                continue
            seen.add(unit.identity)
            normalized.append(unit)
        object.__setattr__(self, "source_epoch", int(self.source_epoch))
        object.__setattr__(self, "request_id", int(self.request_id))
        object.__setattr__(self, "work_order", tuple(normalized))
        direction = int(self.navigation_direction)
        object.__setattr__(
            self,
            "navigation_direction",
            -1 if direction < 0 else 1 if direction > 0 else 0,
        )


@dataclass(frozen=True)
class ZipRasterFramePage:
    page_index: int
    image_id: str
    logical_image_id: str
    original_size: tuple[int, int]
    pixmap: QPixmap | None
    source_qimage: QImage | None
    error: str | None = None
    split_range: tuple[int, int, int, int] | None = None
    source_is_preview: bool = False


@dataclass(frozen=True)
class ZipRasterFrame:
    source_epoch: int
    source_identity: int
    request_id: int
    unit: ZipRasterDisplayUnit
    pages: tuple[ZipRasterFramePage, ...]
    cache_hit: bool
    worker_completed_at: float
    gui_ready_at: float


@dataclass(frozen=True)
class ZipRasterRuntimeMetrics:
    jobs_submitted: int = 0
    queued_callbacks: int = 0
    qpixmap_creations: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cancel_requests: int = 0
    stale_results: int = 0
    terminal_errors: int = 0
    cache_evictions: int = 0
    source_cache_hits: int = 0
    source_cache_misses: int = 0
    source_cache_evictions: int = 0
    prefetch_admission_stops: int = 0


@dataclass(frozen=True)
class _UnitKey:
    source_epoch: int
    source_identity: int
    unit_identity: tuple[tuple[int, str], ...]
    is_single: bool
    render_spec: ZipRasterRenderSpec


@dataclass(frozen=True)
class _SourceKey:
    source_epoch: int
    source_identity: int
    image_id: str
    adjustments: tuple[float, float, float]
    pixel_size: tuple[int, int]
    full_resolution: bool


@dataclass(frozen=True)
class _CachedSource:
    key: _SourceKey
    page_index: int
    qimage: QImage
    original_size: tuple[int, int]
    source_is_preview: bool


@dataclass(frozen=True)
class _DecodedPage:
    page: ZipRasterPage
    qimage: QImage | None
    original_size: tuple[int, int]
    source_is_preview: bool
    error: str | None = None
    source_key: _SourceKey | None = None


@dataclass(frozen=True)
class _RenderedPage:
    page_index: int
    image_id: str
    logical_image_id: str
    original_size: tuple[int, int]
    display_qimage: QImage | None
    source_qimage: QImage | None
    error: str | None
    split_range: tuple[int, int, int, int] | None
    source_is_preview: bool
    source_key: _SourceKey | None
    source_original_size: tuple[int, int]


@dataclass(frozen=True)
class _JobResult:
    serial: int
    key: _UnitKey
    request_id: int
    unit: ZipRasterDisplayUnit
    pages: tuple[_RenderedPage, ...]
    cancelled: bool
    completed_at: float


@dataclass(frozen=True)
class _CachedFrame:
    key: _UnitKey
    unit: ZipRasterDisplayUnit
    pages: tuple[ZipRasterFramePage, ...]
    source_keys: tuple[_SourceKey | None, ...]
    worker_completed_at: float
    gui_ready_at: float


class _ZipRasterSourceStore:
    """Keep decoded raster sources independent from layout-specific frames.

    ZipPlaFork keeps the source bitmap and its resized display bitmap in the
    same page lifetime.  This Qt adaptation preserves that useful lifetime
    split without importing WinForms/GDI ownership: immutable ``QImage``
    sources remain book-scoped, while ``QPixmap`` frames remain GUI/layout
    scoped.  A smaller preview can satisfy an equal-or-smaller layout; a full
    source can satisfy every later layout, rotation, DPI, or magnifier request.
    """

    def __init__(self, *, page_limit: int) -> None:
        self._sources: OrderedDict[_SourceKey, _CachedSource] = OrderedDict()
        self._page_limit = max(1, int(page_limit))
        self._active_order: tuple[tuple[int, str], ...] = ()
        self._current_pages: tuple[ZipRasterPage, ...] = ()
        self._current_render_spec: ZipRasterRenderSpec | None = None
        self._direction = 0

    @property
    def page_count(self) -> int:
        return len(self._sources)

    @property
    def byte_size(self) -> int:
        return sum(source.qimage.sizeInBytes() for source in self._sources.values())

    @property
    def largest_source_bytes(self) -> int:
        return max(
            (source.qimage.sizeInBytes() for source in self._sources.values()),
            default=0,
        )

    @property
    def page_indexes(self) -> tuple[int, ...]:
        return tuple(source.page_index for source in self._sources.values())

    def clear(self) -> None:
        self._sources.clear()

    def set_page_limit(self, page_limit: int) -> int:
        self._page_limit = max(1, int(page_limit))
        return self._prune_count()

    def set_retention_order(
        self,
        work_order: tuple[ZipRasterDisplayUnit, ...],
        current: ZipRasterDisplayUnit | None,
        render_spec: ZipRasterRenderSpec | None,
        direction: int,
    ) -> int:
        seen: set[str] = set()
        order: list[tuple[int, str]] = []
        for unit in work_order:
            for page in unit.pages:
                if page.image_id in seen:
                    continue
                seen.add(page.image_id)
                order.append((page.page_index, page.image_id))
        self._active_order = tuple(order)
        self._current_pages = current.pages if current is not None else ()
        self._current_render_spec = render_spec
        normalized = int(direction)
        self._direction = -1 if normalized < 0 else 1 if normalized > 0 else 0
        return self._prune_count()

    def find(
        self,
        page: ZipRasterPage,
        render_spec: ZipRasterRenderSpec,
        *,
        touch: bool = False,
    ) -> _CachedSource | None:
        candidates = tuple(
            source
            for source in self._sources.values()
            if source.key.image_id == page.image_id
            and source.key.adjustments == render_spec.adjustments
            and self._satisfies(source, render_spec.decoder_maximum_size)
        )
        if not candidates:
            return None
        # Prefer the smallest sufficient source.  This avoids making normal
        # fit-window rendering walk a full-resolution image while a retained
        # decoder-sized source is still sufficient.
        selected = min(candidates, key=lambda source: source.qimage.sizeInBytes())
        if touch:
            self._sources.move_to_end(selected.key)
        return selected

    def get(
        self,
        key: _SourceKey | None,
        *,
        touch: bool = False,
    ) -> _CachedSource | None:
        if key is None:
            return None
        source = self._sources.get(key)
        if source is not None and touch:
            self._sources.move_to_end(key)
        return source

    def put(self, source: _CachedSource) -> int:
        evicted = 0
        dominated = tuple(
            key
            for key, existing in self._sources.items()
            if key != source.key
            and key.image_id == source.key.image_id
            and key.adjustments == source.key.adjustments
            and self._dominates(source, existing)
        )
        for key in dominated:
            self._sources.pop(key, None)
            evicted += 1
        self._sources[source.key] = source
        self._sources.move_to_end(source.key)
        return evicted + self._prune_count()

    def evict_one(self) -> bool:
        candidate = self._eviction_candidate()
        if candidate is None:
            return False
        self._sources.pop(candidate, None)
        return True

    def _prune_count(self) -> int:
        evicted = 0
        while len(self._sources) > self._page_limit:
            if not self.evict_one():
                break
            evicted += 1
        return evicted

    def _eviction_candidate(self) -> _SourceKey | None:
        protected = self._protected_keys()
        candidates = tuple(
            key
            for key in self._sources
            if key not in protected
        )
        if not candidates:
            return None
        positions = {key: index for index, key in enumerate(self._sources)}
        return max(
            candidates,
            key=lambda key: (
                self._retention_rank(self._sources[key]),
                -positions[key],
            ),
        )

    def _protected_keys(self) -> frozenset[_SourceKey]:
        render_spec = self._current_render_spec
        if render_spec is None:
            return frozenset()
        protected: set[_SourceKey] = set()
        for page in self._current_pages:
            source = self.find(page, render_spec)
            if source is not None:
                protected.add(source.key)
        return frozenset(protected)

    def _retention_rank(self, source: _CachedSource) -> tuple[int, int, int]:
        for index, (_page_index, image_id) in enumerate(self._active_order):
            if image_id == source.key.image_id:
                return (0, index, 0)
        if not self._active_order:
            return (1, 0, 0)
        current_anchor = self._active_order[0][0]
        delta = source.page_index - current_anchor
        direction_penalty = int(
            self._direction != 0
            and delta != 0
            and (1 if delta > 0 else -1) != self._direction
        )
        return (1, abs(delta), direction_penalty)

    @staticmethod
    def _satisfies(
        source: _CachedSource,
        maximum_size: tuple[int, int] | None,
    ) -> bool:
        if maximum_size is None:
            return not source.source_is_preview
        if not source.source_is_preview:
            return True
        original_width, original_height = source.original_size
        scale = min(
            1.0,
            max(1, int(maximum_size[0])) / max(1, original_width),
            max(1, int(maximum_size[1])) / max(1, original_height),
        )
        required_width = max(1, round(original_width * scale))
        required_height = max(1, round(original_height * scale))
        return (
            source.qimage.width() >= required_width
            and source.qimage.height() >= required_height
        )

    @staticmethod
    def _dominates(candidate: _CachedSource, existing: _CachedSource) -> bool:
        if not candidate.source_is_preview:
            return True
        if not existing.source_is_preview:
            return False
        return (
            candidate.qimage.width() >= existing.qimage.width()
            and candidate.qimage.height() >= existing.qimage.height()
        )


class _ZipRasterFrameStore:
    """Own completed frames independently from the active worker frontier.

    ZipPlaFork's ``BackgroundMultiWorker`` order also supplies the eviction
    priority used by ``ViewerForm.ReduceUsingMemory``.  The previous runtime
    incorrectly treated the three currently scheduled units as the complete
    cache membership and discarded every other completed frame on each page
    request.  This store keeps completed work until the configured unit/byte
    budget actually requires eviction, while the current -> next -> previous
    order ranks the protected neighborhood.

    The full-book order is represented as a distance/direction rank instead of
    allocating a Python object for every page on every wheel event.  Only the
    small active frontier is materialized by ``ZipRasterRequest``.
    """

    def __init__(self, *, unit_limit: int, byte_budget: int) -> None:
        self._frames: OrderedDict[_UnitKey, _CachedFrame] = OrderedDict()
        self._unit_limit = max(1, int(unit_limit))
        self._byte_budget = max(1, int(byte_budget))
        self._active_order: tuple[_UnitKey, ...] = ()
        self._current_key: _UnitKey | None = None
        self._displayed_key: _UnitKey | None = None
        self._direction = 0

    def __contains__(self, key: _UnitKey) -> bool:
        return key in self._frames

    @property
    def unit_count(self) -> int:
        return len(self._frames)

    @property
    def page_indexes(self) -> tuple[int, ...]:
        return tuple(
            page.page_index
            for frame in self._frames.values()
            for page in frame.unit.pages
        )

    @property
    def byte_size(self) -> int:
        return sum(self._frame_bytes(frame) for frame in self._frames.values())

    @property
    def largest_frame_bytes(self) -> int:
        return max(
            (self._frame_bytes(frame) for frame in self._frames.values()),
            default=0,
        )

    def get(self, key: _UnitKey, *, touch: bool = False) -> _CachedFrame | None:
        frame = self._frames.get(key)
        if frame is not None and touch:
            self._frames.move_to_end(key)
        return frame

    def values(self) -> tuple[_CachedFrame, ...]:
        return tuple(self._frames.values())

    def clear(self) -> None:
        self._frames.clear()

    def take(self, key: _UnitKey) -> _CachedFrame | None:
        return self._frames.pop(key, None)

    def set_limits(
        self,
        *,
        unit_limit: int | None = None,
        byte_budget: int | None = None,
    ) -> int:
        if unit_limit is not None:
            self._unit_limit = max(1, int(unit_limit))
        if byte_budget is not None:
            self._byte_budget = max(1, int(byte_budget))
        return self._prune()

    def set_retention_order(
        self,
        active_order: tuple[_UnitKey, ...],
        current_key: _UnitKey | None,
        direction: int,
    ) -> int:
        self._active_order = tuple(active_order)
        self._current_key = current_key
        normalized_direction = int(direction)
        if normalized_direction < 0:
            self._direction = -1
        elif normalized_direction > 0:
            self._direction = 1
        else:
            self._direction = 0
        return self._prune()

    def set_displayed_key(self, key: _UnitKey | None) -> int:
        """Protect the last painted frame until its replacement paints."""

        self._displayed_key = key
        return self._prune()

    def put(self, frame: _CachedFrame) -> tuple[bool, int]:
        self._frames[frame.key] = frame
        self._frames.move_to_end(frame.key)
        evicted = self._prune()
        return frame.key in self._frames, evicted

    def can_admit_prefetch(self, key: _UnitKey) -> bool:
        if key in self._frames:
            return False
        if (
            len(self._frames) < self._unit_limit
            and self.byte_size < self._byte_budget
        ):
            return True
        victim = self._eviction_candidate()
        if victim is None:
            return False
        # A newly relevant neighbor may replace an older/farther frame, but a
        # background request never churns an equal-or-better retained frame.
        return self._retention_rank(key) < self._retention_rank(victim)

    def evict_one(self) -> bool:
        candidate = self._eviction_candidate()
        if candidate is None:
            return False
        self._frames.pop(candidate, None)
        return True

    def _prune(self) -> int:
        evicted = 0
        while (
            len(self._frames) > self._unit_limit
            or self.byte_size > self._byte_budget
        ):
            removable = self._eviction_candidate()
            if removable is None:
                # A single current frame may legitimately exceed the byte
                # budget. Keeping it is required by the old-frame/atomic
                # commit contract; no background frame is admitted beside it.
                break
            self._frames.pop(removable, None)
            evicted += 1
        return evicted

    def _eviction_candidate(self) -> _UnitKey | None:
        candidates = tuple(
            key
            for key in self._frames
            if key not in {self._current_key, self._displayed_key}
        )
        if not candidates:
            return None
        positions = {key: index for index, key in enumerate(self._frames)}
        return max(
            candidates,
            key=lambda key: (
                self._retention_rank(key),
                -positions[key],
            ),
        )

    def _retention_rank(self, key: _UnitKey) -> tuple[int, int, int]:
        try:
            return (0, self._active_order.index(key), 0)
        except ValueError:
            pass
        current = self._current_key
        if current is None:
            return (1, 0, 0)
        current_anchor = self._unit_anchor(current)
        candidate_anchor = self._unit_anchor(key)
        delta = candidate_anchor - current_anchor
        direction_penalty = int(
            self._direction != 0
            and delta != 0
            and (1 if delta > 0 else -1) != self._direction
        )
        return (1, abs(delta), direction_penalty)

    @staticmethod
    def _unit_anchor(key: _UnitKey) -> int:
        indexes = tuple(page_index for page_index, _image_id in key.unit_identity)
        return min(indexes) if indexes else 0

    @staticmethod
    def _frame_bytes(frame: _CachedFrame) -> int:
        return sum(
            page.pixmap.width() * page.pixmap.height() * 4
            for page in frame.pages
            if page.pixmap is not None
        )


class _JobSignals(QObject):
    completed = Signal(object)


class _ZipRasterUnitJob(QRunnable):
    def __init__(
        self,
        *,
        serial: int,
        key: _UnitKey,
        request_id: int,
        source: ImageSource,
        unit: ZipRasterDisplayUnit,
        cached_sources: dict[str, _CachedSource] | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.serial = int(serial)
        self.key = key
        self.source = source
        self.unit = unit
        self.cached_sources = dict(cached_sources or {})
        self.signals = _JobSignals()
        self.cancelled = Event()
        self.started = Event()
        self.finished = Event()
        self._token_lock = Lock()
        self._request_id = int(request_id)

    def adopt_request(self, request_id: int) -> None:
        with self._token_lock:
            self._request_id = int(request_id)

    def cancel(self) -> bool:
        if self.cancelled.is_set():
            return False
        self.cancelled.set()
        cancel_image_request = getattr(self.source, "cancel_image_request", None)
        if callable(cancel_image_request):
            for page in self.unit.pages:
                try:
                    cancel_image_request(page.image_id)
                except Exception:
                    pass
        return True

    def mark_removed_before_start(self) -> None:
        self.cancelled.set()
        self.finished.set()

    @Slot()
    def run(self) -> None:
        self.started.set()
        pages: tuple[_RenderedPage, ...] = ()
        try:
            if not self.cancelled.is_set():
                pages = self._render_unit()
        except Exception as exc:  # pragma: no cover - defensive worker wall
            if not self.cancelled.is_set():
                message = str(exc) or exc.__class__.__name__
                pages = tuple(
                    _RenderedPage(
                        page.page_index,
                        page.image_id,
                        page.image_id,
                        page.known_size or (360, 520),
                        None,
                        None,
                        message,
                        None,
                        False,
                        None,
                        page.known_size or (360, 520),
                    )
                    for page in self.unit.pages
                )
        with self._token_lock:
            request_id = self._request_id
        result = _JobResult(
            self.serial,
            self.key,
            request_id,
            self.unit,
            pages,
            self.cancelled.is_set(),
            monotonic(),
        )
        # Worker completion and GUI-result drainage are separate lifetime
        # phases.  Mark native/Pillow/source access finished before queuing the
        # GUI callback; the runtime keeps the job in ``_jobs`` until that
        # callback has consumed the result.
        self.finished.set()
        self.signals.completed.emit(result)

    def _render_unit(self) -> tuple[_RenderedPage, ...]:
        decoded = tuple(self._decode_page(page) for page in self.unit.pages)
        if self.cancelled.is_set():
            return ()
        expanded: list[
            tuple[
                _DecodedPage,
                str,
                tuple[int, int],
                tuple[int, int, int, int] | None,
            ]
        ] = []
        for page in decoded:
            split = self._split_ranges(page)
            if split is None:
                expanded.append(
                    (page, page.page.image_id, page.original_size, None)
                )
                continue
            left_range, right_range, left_size, right_size = split
            halves = [
                (f"{page.page.image_id}#left", left_size, left_range),
                (f"{page.page.image_id}#right", right_size, right_range),
            ]
            if self.key.render_spec.reading_direction == "rtl":
                halves.reverse()
            expanded.extend(
                (page, image_id, original_size, split_range)
                for image_id, original_size, split_range in halves
            )

        rotation = self.key.render_spec.rotation
        layout_sizes = [
            (
                (height, width)
                if rotation in {90, 270}
                else (width, height)
            )
            for _page, _image_id, (width, height), _split in expanded
        ]
        layout = calculate_spread_layout(
            layout_sizes,
            self.key.render_spec.viewport_size,
            fit_mode=self.key.render_spec.fit_mode,
            manual_zoom=self.key.render_spec.manual_zoom,
            gap=self.key.render_spec.gap,
            join_spread_pages=self.key.render_spec.join_spread_pages,
            spread_is_single=self.unit.is_single,
            horizontal_alignment=self.key.render_spec.horizontal_alignment,
        )

        rendered: list[_RenderedPage] = []
        for expanded_page, target_rect in zip(expanded, layout.rects):
            if self.cancelled.is_set():
                return ()
            page, image_id, original_size, split_range = expanded_page
            if page.error is not None or page.qimage is None:
                rendered.append(
                    _RenderedPage(
                        page.page.page_index,
                        image_id,
                        page.page.image_id,
                        original_size,
                        None,
                        page.qimage,
                        page.error or "画像を表示できません。",
                        split_range,
                        page.source_is_preview,
                        page.source_key,
                        page.original_size,
                    )
                )
                continue
            target_width = max(
                1,
                round(target_rect.width() * self.key.render_spec.device_pixel_ratio),
            )
            target_height = max(
                1,
                round(target_rect.height() * self.key.render_spec.device_pixel_ratio),
            )
            source_width, source_height = (
                (split_range[2], split_range[3])
                if split_range is not None
                else (page.qimage.width(), page.qimage.height())
            )
            if rotation in {90, 270}:
                source_width, source_height = source_height, source_width
            if self.key.render_spec.resampling_mode == "standard" and (
                target_width > source_width or target_height > source_height
            ):
                scale = min(
                    1.0,
                    target_width / max(1, source_width),
                    target_height / max(1, source_height),
                )
                target_width = max(1, round(source_width * scale))
                target_height = max(1, round(source_height * scale))
            render_key = ViewerRenderKey(
                image_id=image_id,
                source_cache_key=int(page.qimage.cacheKey()),
                target_width=target_width,
                target_height=target_height,
                mode=self.key.render_spec.resampling_mode,
                rotation=rotation,
                device_pixel_ratio_milli=round(
                    self.key.render_spec.device_pixel_ratio * 1000
                ),
                split_range=split_range,
                smooth_transform=self.key.render_spec.smooth_scaling,
            )
            try:
                display_qimage, _resized = render_qimage(page.qimage, render_key)
                error = None if not display_qimage.isNull() else "画像を表示できません。"
                if error is not None:
                    display_qimage = None
            except Exception as exc:
                display_qimage = None
                error = str(exc) or exc.__class__.__name__
            rendered.append(
                _RenderedPage(
                    page.page.page_index,
                    image_id,
                    page.page.image_id,
                    original_size,
                    display_qimage,
                    page.qimage,
                    error,
                    split_range,
                    page.source_is_preview,
                    page.source_key,
                    page.original_size,
                )
            )
        return tuple(rendered)

    def _decode_page(self, page: ZipRasterPage) -> _DecodedPage:
        if self.cancelled.is_set():
            return _DecodedPage(page, None, page.known_size or (360, 520), False)
        spec = self.key.render_spec
        cached = self.cached_sources.get(page.image_id)
        if cached is not None:
            return _DecodedPage(
                page,
                QImage(cached.qimage),
                cached.original_size,
                cached.source_is_preview,
                source_key=cached.key,
            )
        try:
            qimage: QImage | None = None
            original_size: tuple[int, int] | None = None
            if (
                spec.decoder_maximum_size is not None
                and spec.adjustments == (1.0, 1.0, 1.0)
                and Path(page.image_id).suffix.casefold()
                in {".jpg", ".jpeg", ".jpe"}
            ):
                decoded = self.source.open_qimage_at_most(
                    page.image_id,
                    spec.decoder_maximum_size,
                )
                if decoded is not None:
                    qimage, original_size = decoded
            if (
                (qimage is None or qimage.isNull())
                and spec.adjustments == (1.0, 1.0, 1.0)
                and Path(page.image_id).suffix.casefold() == ".webp"
            ):
                qimage = self.source.open_qimage(page.image_id)
                if qimage is not None and not qimage.isNull():
                    original_size = (qimage.width(), qimage.height())
            if qimage is None or qimage.isNull():
                image = self.source.open_image(page.image_id)
                adjusted: Image.Image | None = None
                try:
                    adjusted = self._apply_adjustments(image, spec.adjustments)
                    qimage = pil_to_qimage(adjusted)
                    original_size = adjusted.size
                finally:
                    if adjusted is not None and adjusted is not image:
                        adjusted.close()
                    image.close()
            if self.cancelled.is_set():
                return _DecodedPage(
                    page,
                    None,
                    original_size or page.known_size or (360, 520),
                    False,
                )
            if qimage is None or qimage.isNull():
                raise ImageSourceError("画像decoderが結果を返しませんでした。")
            logical = original_size or (qimage.width(), qimage.height())
            logical = (max(1, int(logical[0])), max(1, int(logical[1])))
            source_is_preview = (qimage.width(), qimage.height()) != logical
            source_key = _SourceKey(
                self.key.source_epoch,
                self.key.source_identity,
                page.image_id,
                spec.adjustments,
                (qimage.width(), qimage.height()),
                not source_is_preview,
            )
            return _DecodedPage(
                page,
                qimage,
                logical,
                source_is_preview,
                source_key=source_key,
            )
        except ImageSourceError as exc:
            if exc.code in {
                ArchiveErrorCode.PROCESS_CANCELLED.value,
                "process_cancelled",
            } and self.cancelled.is_set():
                return _DecodedPage(
                    page,
                    None,
                    page.known_size or (360, 520),
                    False,
                )
            return _DecodedPage(
                page,
                None,
                page.known_size or (360, 520),
                False,
                str(exc),
            )
        except Exception as exc:
            return _DecodedPage(
                page,
                None,
                page.known_size or (360, 520),
                False,
                str(exc) or exc.__class__.__name__,
            )

    def _split_ranges(
        self,
        page: _DecodedPage,
    ) -> tuple[
        tuple[int, int, int, int],
        tuple[int, int, int, int],
        tuple[int, int],
        tuple[int, int],
    ] | None:
        if (
            not self.key.render_spec.split_wide_image
            or not self.unit.is_single
            or page.qimage is None
        ):
            return None
        width, height = page.original_size
        if height <= 0 or width < 2 or width / height < 1.25:
            return None
        pixel_width = page.qimage.width()
        if pixel_width < 2:
            return None
        left_width = width // 2
        right_width = width - left_width
        left_pixels = max(
            1,
            min(pixel_width - 1, round(pixel_width * left_width / width)),
        )
        return (
            (0, 0, left_pixels, page.qimage.height()),
            (
                left_pixels,
                0,
                page.qimage.width() - left_pixels,
                page.qimage.height(),
            ),
            (left_width, height),
            (right_width, height),
        )

    @staticmethod
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
                min(
                    255,
                    max(
                        0,
                        int(((value / 255.0) ** inverse_gamma) * 255.0 + 0.5),
                    ),
                )
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


class RasterBookRuntime(QObject):
    """Own one complete raster Viewer execution path for one book.

    ZIP and folder books share this scheduler, source/frame artifact split,
    cache policy, and callback-drain lifetime.  Concrete subclasses constrain
    the accepted source type without forking the execution pipeline.
    """

    _source_type: type[ImageSource] = ImageSource
    _runtime_display_name = "Raster"

    frameReady = Signal(object)
    artifactReady = Signal(object)
    idle = Signal(object)

    def __init__(
        self,
        source: ImageSource,
        source_epoch: int,
        parent: QObject | None = None,
        *,
        image_work_coordinator: ImageWorkCoordinator | None = None,
        cache_unit_limit: int = 3,
        cache_byte_budget: int = _DEFAULT_CACHE_BYTES,
    ) -> None:
        if not isinstance(source, self._source_type):
            raise TypeError(
                f"{type(self).__name__} requires {self._source_type.__name__}"
            )
        super().__init__(parent)
        self.source = source
        self.source_epoch = int(source_epoch)
        self._source_identity = id(source)
        self._coordinator = image_work_coordinator
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._accepting_requests = True
        self._shutdown_complete = False
        self._serial = 0
        self._current_request: ZipRasterRequest | None = None
        self._current_key: _UnitKey | None = None
        self._work_keys: tuple[_UnitKey, ...] = ()
        self._unit_by_key: dict[_UnitKey, ZipRasterDisplayUnit] = {}
        self._prefetch_released_request_id: int | None = None
        self._prefetch_admission_stopped_request_id: int | None = None
        self._dispatch_suspended = False
        self._painted_key: _UnitKey | None = None
        self._source_hydration_key: _UnitKey | None = None
        self._source_hydration_frame: _CachedFrame | None = None
        self._active_job: _ZipRasterUnitJob | None = None
        self._jobs: set[_ZipRasterUnitJob] = set()
        self._failed_prefetch: set[_UnitKey] = set()
        self._cache_unit_limit = max(1, int(cache_unit_limit))
        self._cache_byte_budget = max(1, int(cache_byte_budget))
        self._frame_store = _ZipRasterFrameStore(
            unit_limit=self._cache_unit_limit,
            byte_budget=self._cache_byte_budget,
        )
        self._source_store = _ZipRasterSourceStore(
            page_limit=max(2, self._cache_unit_limit * 2),
        )
        self._metrics = ZipRasterRuntimeMetrics()

    @property
    def metrics(self) -> ZipRasterRuntimeMetrics:
        return replace(self._metrics)

    @property
    def active_job_count(self) -> int:
        return int(
            self._active_job is not None
            and not self._active_job.finished.is_set()
        )

    @property
    def cached_page_indexes(self) -> tuple[int, ...]:
        indexes = list(self._frame_store.page_indexes)
        if self._source_hydration_frame is not None:
            indexes.extend(
                page.page_index
                for page in self._source_hydration_frame.unit.pages
            )
        return tuple(indexes)

    @property
    def cached_unit_count(self) -> int:
        return self._frame_store.unit_count + int(
            self._source_hydration_frame is not None
        )

    @property
    def cache_bytes(self) -> int:
        hydration_bytes = (
            _ZipRasterFrameStore._frame_bytes(self._source_hydration_frame)
            if self._source_hydration_frame is not None
            else 0
        )
        return (
            self._frame_store.byte_size
            + self._source_store.byte_size
            + hydration_bytes
        )

    @property
    def decoded_source_count(self) -> int:
        return self._source_store.page_count

    @property
    def decoded_source_bytes(self) -> int:
        return self._source_store.byte_size

    def set_cache_limits(
        self,
        *,
        unit_limit: int | None = None,
        byte_budget: int | None = None,
    ) -> None:
        if unit_limit is not None:
            self._cache_unit_limit = max(1, int(unit_limit))
        if byte_budget is not None:
            self._cache_byte_budget = max(1, int(byte_budget))
        evicted = self._frame_store.set_limits(
            unit_limit=self._cache_unit_limit,
            byte_budget=self._cache_byte_budget,
        )
        if evicted:
            self._bump("cache_evictions", evicted)
        source_evicted = self._source_store.set_page_limit(
            max(2, self._cache_unit_limit * 2)
        )
        self._record_source_evictions(source_evicted)
        self._enforce_combined_budget()

    def has_cached_current(self, request: ZipRasterRequest) -> bool:
        return (
            self._key_for(request.current, request.render_spec)
            in self._frame_store
        )

    def require_cached_current_source(
        self,
        request: ZipRasterRequest,
    ) -> bool:
        """Withhold one source-less frame while its source is hydrated.

        Main display QPixmaps intentionally survive decoded-source eviction.
        Interactive source consumers such as the magnifier can therefore ask
        for the same full-spec unit again.  The frame remains owned as a
        rollback artifact: navigation/cancellation restores the ready hit,
        while the same-current request treats only this unit as cold.
        """

        if (
            not self._accepting_requests
            or request.source_epoch != self.source_epoch
        ):
            return False
        key = self._key_for(request.current, request.render_spec)
        if self._source_hydration_key == key:
            return True
        if self._source_hydration_frame is not None:
            self._restore_source_hydration_frame()
        cached = self._frame_store.get(key, touch=False)
        if cached is None:
            return False
        for index, page in enumerate(cached.pages):
            if page.error is not None:
                continue
            if self._source_for_frame_page(
                cached,
                index,
                page,
                touch=False,
            ) is None:
                self._source_hydration_frame = self._frame_store.take(key)
                self._source_hydration_key = key
                self._failed_prefetch.discard(key)
                return self._source_hydration_frame is not None
        return False

    def stage(self, request: ZipRasterRequest) -> bool:
        """Adopt a navigation intent without starting its cold current job.

        The Viewer uses this before its replaceable cold-demand timer.  Work
        order, retention and cancellation therefore follow every input
        immediately, while a paced wheel/key burst can still replace the
        eventual decode target.  A completed staged artifact is retained but
        never published until :meth:`request` admits that exact serial.
        """

        if (
            not self._accepting_requests
            or request.source_epoch != self.source_epoch
        ):
            return False
        self._adopt_request(request, suspend_dispatch=True)
        return True

    def request(self, request: ZipRasterRequest) -> bool:
        if (
            not self._accepting_requests
            or request.source_epoch != self.source_epoch
        ):
            return False
        if self._dispatch_suspended and self._current_request is request:
            # Window dispatches the exact object it staged.  Retention,
            # cancellation and work-order replacement already happened at
            # input time, so admission only opens the current-job gate.
            self._dispatch_suspended = False
        else:
            self._adopt_request(request, suspend_dispatch=False)
        current_key = self._current_key
        if current_key is None:
            return False
        frame = self._frame_store.get(current_key, touch=True)
        if frame is not None:
            self._bump("cache_hits")
            self.frameReady.emit(self._public_frame(request, frame, True))
        else:
            self._bump("cache_misses")
        self._drive()
        return True

    def _adopt_request(
        self,
        request: ZipRasterRequest,
        *,
        suspend_dispatch: bool,
    ) -> None:
        current_key = self._key_for(request.current, request.render_spec)
        if (
            self._source_hydration_key is not None
            and self._source_hydration_key != current_key
        ):
            self._restore_source_hydration_frame()
        work_keys = tuple(
            self._key_for(unit, request.render_spec)
            for unit in request.work_order
        )
        self._current_request = request
        self._current_key = current_key
        self._work_keys = work_keys
        self._unit_by_key = dict(zip(work_keys, request.work_order))
        self._prefetch_released_request_id = None
        self._prefetch_admission_stopped_request_id = None
        self._dispatch_suspended = bool(suspend_dispatch)
        evicted = self._frame_store.set_retention_order(
            work_keys,
            current_key,
            request.navigation_direction,
        )
        if evicted:
            self._bump("cache_evictions", evicted)
        source_evicted = self._source_store.set_retention_order(
            request.work_order,
            request.current,
            request.render_spec,
            request.navigation_direction,
        )
        self._record_source_evictions(source_evicted)
        self._enforce_combined_budget()
        # Admission/failure suppression belongs to one replaceable work order.
        # A later navigation may make a previously rejected neighbor current,
        # and must get a fresh attempt.
        self._failed_prefetch.clear()

        active = self._active_job
        if active is not None:
            active_cancelled = active.cancelled.is_set()
            if active.key == current_key and not active_cancelled:
                active.adopt_request(request.request_id)
            elif (
                not suspend_dispatch
                and not active_cancelled
                and current_key in self._frame_store
                and active.key in work_keys
            ):
                # A same-current refresh or a compatible work-order change can
                # adopt an already useful neighbor instead of restarting it.
                active.adopt_request(request.request_id)
            else:
                self._cancel_active_job()

    def release_prefetch(self, *, request_id: int) -> bool:
        request = self._current_request
        if (
            request is None
            or self._dispatch_suspended
            or int(request_id) != request.request_id
            or self._current_key not in self._frame_store
        ):
            return False
        self._painted_key = self._current_key
        evicted = self._frame_store.set_displayed_key(self._painted_key)
        if evicted:
            self._bump("cache_evictions", evicted)
        self._prefetch_released_request_id = request.request_id
        self._drive()
        return True

    def cancel(self, *, clear_artifacts: bool = False) -> None:
        if clear_artifacts:
            self._source_hydration_key = None
            self._source_hydration_frame = None
        else:
            self._restore_source_hydration_frame()
        self._current_request = None
        self._current_key = None
        self._work_keys = ()
        self._unit_by_key.clear()
        self._prefetch_released_request_id = None
        self._prefetch_admission_stopped_request_id = None
        self._dispatch_suspended = False
        self._painted_key = None
        self._failed_prefetch.clear()
        self._cancel_active_job()
        if clear_artifacts:
            self._frame_store.clear()
            self._source_store.clear()
        self._frame_store.set_retention_order((), None, 0)
        self._frame_store.set_displayed_key(None)
        self._source_store.set_retention_order((), None, None, 0)

    def invalidate_layout(self) -> None:
        # Layout-dependent QPixmaps are invalid, but decoded archive content
        # remains valid.  Keeping this boundary is what prevents resize, DPI,
        # rotation, and magnifier transitions from reopening the same entry.
        self.cancel(clear_artifacts=False)
        self._frame_store.clear()

    def has_unfinished_tasks(self) -> bool:
        # Jobs remain owned until their queued GUI completion is consumed.
        return bool(self._jobs)

    def wait_for_done(self, msecs: int = 5000) -> bool:
        deadline = monotonic() + max(0, int(msecs)) / 1000
        for job in tuple(self._jobs):
            remaining = max(0.0, deadline - monotonic())
            if not job.finished.wait(remaining):
                return False
        return True

    def shutdown(self, *, wait_msecs: int = 5000) -> bool:
        if self._shutdown_complete:
            return True
        self._accepting_requests = False
        self.cancel(clear_artifacts=True)
        if self._coordinator is None:
            self._thread_pool.clear()
        workers_completed = self.wait_for_done(wait_msecs)
        completed = workers_completed and not self._jobs
        if completed:
            self._shutdown_complete = True
        return completed

    def _drive(self) -> None:
        request = self._current_request
        if (
            not self._accepting_requests
            or request is None
            or self._current_key is None
            or self._dispatch_suspended
            or self._active_job is not None
        ):
            return
        if self._current_key not in self._frame_store:
            self._submit(self._current_key, ImageWorkPriority.VIEWER_CURRENT)
            return
        if self._prefetch_released_request_id != request.request_id:
            return
        for rank, key in enumerate(self._work_keys[1:]):
            if key in self._frame_store or key in self._failed_prefetch:
                continue
            if not self._can_admit_prefetch(key):
                if (
                    self._prefetch_admission_stopped_request_id
                    != request.request_id
                ):
                    self._prefetch_admission_stopped_request_id = request.request_id
                    self._bump("prefetch_admission_stops")
                return
            priority = (
                ImageWorkPriority.VIEWER_NEXT
                if rank == 0
                else ImageWorkPriority.VIEWER_PREVIOUS
            )
            self._submit(key, priority)
            return

    def _can_admit_prefetch(self, key: _UnitKey) -> bool:
        """Reject work that cannot coexist with the protected current unit.

        The frame store alone cannot see the usually larger decoded source
        allocation.  Admission therefore uses the combined book budget and a
        conservative estimate of both missing sources and the display frame.
        Admission does not pre-evict completed artifacts or assume that a
        running/stale job will reach commit.  Replacement under a full budget
        is deferred until that page becomes current, where atomic-frame
        semantics deliberately allow one soft overflow before eviction.
        """
        if not self._frame_store.can_admit_prefetch(key):
            return False
        unit = self._unit_by_key.get(key)
        if unit is None:
            return False
        source_bytes = self._estimated_missing_source_bytes(
            unit,
            key.render_spec,
        )
        frame_bytes = self._estimated_frame_bytes(
            unit,
            key.render_spec,
            source_bytes=source_bytes,
        )
        free = max(0, self._cache_byte_budget - self.cache_bytes)
        return source_bytes + frame_bytes <= free

    def _estimated_missing_source_bytes(
        self,
        unit: ZipRasterDisplayUnit,
        render_spec: ZipRasterRenderSpec,
    ) -> int:
        observed = self._source_store.largest_source_bytes
        estimate = 0
        for page in unit.pages:
            if self._source_store.find(page, render_spec) is not None:
                continue
            known_size = page.known_size
            suffix = Path(page.image_id).suffix.casefold()
            decoder_size = render_spec.decoder_maximum_size
            if known_size is not None:
                width, height = known_size
                if (
                    suffix in {".jpg", ".jpeg", ".jpe"}
                    and decoder_size is not None
                    and render_spec.adjustments == (1.0, 1.0, 1.0)
                ):
                    scale = min(
                        1.0,
                        decoder_size[0] / max(1, width),
                        decoder_size[1] / max(1, height),
                    )
                    width = max(1, round(width * scale))
                    height = max(1, round(height * scale))
                estimate += width * height * 4
                continue
            if (
                suffix in {".jpg", ".jpeg", ".jpe"}
                and decoder_size is not None
                and render_spec.adjustments == (1.0, 1.0, 1.0)
            ):
                unknown = decoder_size[0] * decoder_size[1] * 4
            else:
                unknown = observed
            if unknown <= 0:
                viewport_width, viewport_height = render_spec.viewport_size
                dpr = render_spec.device_pixel_ratio
                unknown = round(viewport_width * viewport_height * dpr * dpr * 4)
            estimate += max(1, int(unknown))
        return estimate

    def _estimated_frame_bytes(
        self,
        unit: ZipRasterDisplayUnit,
        render_spec: ZipRasterRenderSpec,
        *,
        source_bytes: int,
    ) -> int:
        known_sizes = tuple(page.known_size for page in unit.pages)
        if all(size is not None for size in known_sizes):
            expanded: list[tuple[int, int]] = []
            for size in known_sizes:
                assert size is not None
                width, height = size
                if (
                    render_spec.split_wide_image
                    and unit.is_single
                    and width >= 2
                    and width / max(1, height) >= 1.25
                ):
                    left = width // 2
                    expanded.extend(((left, height), (width - left, height)))
                else:
                    expanded.append((width, height))
            if render_spec.rotation in {90, 270}:
                expanded = [(height, width) for width, height in expanded]
            layout = calculate_spread_layout(
                expanded,
                render_spec.viewport_size,
                fit_mode=render_spec.fit_mode,
                manual_zoom=render_spec.manual_zoom,
                gap=render_spec.gap,
                join_spread_pages=render_spec.join_spread_pages,
                spread_is_single=unit.is_single,
                horizontal_alignment=render_spec.horizontal_alignment,
            )
            dpr = render_spec.device_pixel_ratio
            return max(
                1,
                sum(
                    max(1, round(rect.width() * dpr))
                    * max(1, round(rect.height() * dpr))
                    * 4
                    for rect in layout.rects
                ),
            )

        viewport_width, viewport_height = render_spec.viewport_size
        dpr = render_spec.device_pixel_ratio
        viewport_bytes = round(
            viewport_width * viewport_height * dpr * dpr * 4
        )
        observed = self._frame_store.largest_frame_bytes
        source_floor = source_bytes
        if render_spec.fit_mode == "manual_zoom":
            source_floor = round(
                source_floor * max(1.0, render_spec.manual_zoom) ** 2
            )
        return max(1, viewport_bytes, observed, source_floor)

    def _submit(self, key: _UnitKey, priority: ImageWorkPriority) -> None:
        request = self._current_request
        unit = self._unit_by_key.get(key)
        if request is None or unit is None:
            return
        cached_sources: dict[str, _CachedSource] = {}
        for page in unit.pages:
            cached = self._source_store.find(
                page,
                key.render_spec,
                touch=True,
            )
            if cached is None:
                self._bump("source_cache_misses")
                continue
            cached_sources[page.image_id] = cached
            self._bump("source_cache_hits")
        self._serial += 1
        job = _ZipRasterUnitJob(
            serial=self._serial,
            key=key,
            request_id=request.request_id,
            source=self.source,
            unit=unit,
            cached_sources=cached_sources,
        )
        job.signals.completed.connect(
            self._on_job_completed,
            Qt.ConnectionType.QueuedConnection,
        )
        self._active_job = job
        self._jobs.add(job)
        if self._coordinator is not None:
            started = self._coordinator.start_viewer(job, int(priority))
        else:
            self._thread_pool.start(job, int(priority))
            started = True
        if started:
            self._bump("jobs_submitted")
            return
        job.mark_removed_before_start()
        self._jobs.discard(job)
        self._active_job = None
        if key == self._current_key:
            self._publish_start_failure(
                request,
                key,
                unit,
                f"{self._runtime_display_name} Viewer workerを開始できませんでした。",
            )
        else:
            self._failed_prefetch.add(key)
        self._emit_idle_if_needed()

    def _publish_start_failure(
        self,
        request: ZipRasterRequest,
        key: _UnitKey,
        unit: ZipRasterDisplayUnit,
        message: str,
    ) -> None:
        hydration_frame = (
            self._source_hydration_frame
            if self._source_hydration_key == key
            else None
        )
        if hydration_frame is not None:
            self._restore_source_hydration_frame()
            self._bump("terminal_errors", len(hydration_frame.pages))
            if (
                self._accepting_requests
                and not self._dispatch_suspended
                and self._current_request is request
                and self._current_key == key
            ):
                self.frameReady.emit(
                    self._public_frame(request, hydration_frame, True)
                )
            return
        pages = tuple(
            ZipRasterFramePage(
                page.page_index,
                page.image_id,
                page.image_id,
                page.known_size or (360, 520),
                None,
                None,
                message,
            )
            for page in unit.pages
        )
        ready_at = monotonic()
        cached = _CachedFrame(
            key,
            unit,
            pages,
            (None,) * len(pages),
            ready_at,
            ready_at,
        )
        _retained, evicted = self._frame_store.put(cached)
        if evicted:
            self._bump("cache_evictions", evicted)
        self._bump("terminal_errors", len(pages))
        if (
            self._accepting_requests
            and not self._dispatch_suspended
            and self._current_request is request
            and self._current_key == key
        ):
            self.frameReady.emit(self._public_frame(request, cached, False))

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
        self._emit_idle_if_needed()

    def _try_take(self, job: _ZipRasterUnitJob) -> bool:
        if self._coordinator is not None:
            return self._coordinator.try_take_viewer(job)
        try:
            return self._thread_pool.tryTake(job)
        except RuntimeError:
            return False

    @Slot(object)
    def _on_job_completed(self, result: _JobResult) -> None:
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
            self._drive()
            self._emit_idle_if_needed()
            return
        if result.cancelled:
            self._drive()
            self._emit_idle_if_needed()
            return

        existing_before_result = self._frame_store.get(
            result.key,
            touch=False,
        )
        existing_before_result_is_ready = bool(
            existing_before_result is not None
            and all(page.error is None for page in existing_before_result.pages)
        )

        stored_sources: set[_SourceKey] = set()
        for rendered in result.pages:
            if (
                rendered.source_key is None
                or rendered.source_qimage is None
                or rendered.source_key in stored_sources
            ):
                continue
            stored_sources.add(rendered.source_key)
            source_evicted = self._source_store.put(
                _CachedSource(
                    rendered.source_key,
                    rendered.page_index,
                    QImage(rendered.source_qimage),
                    rendered.source_original_size,
                    rendered.source_is_preview,
                )
            )
            self._record_source_evictions(source_evicted)

        frame_pages: list[ZipRasterFramePage] = []
        source_keys: list[_SourceKey | None] = []
        terminal_errors = 0
        for rendered in result.pages:
            pixmap: QPixmap | None = None
            error = rendered.error
            if rendered.display_qimage is not None and error is None:
                pixmap = QPixmap.fromImage(rendered.display_qimage)
                if pixmap.isNull():
                    pixmap = None
                    error = "QPixmapを作成できませんでした。"
                else:
                    pixmap.setDevicePixelRatio(
                        result.key.render_spec.device_pixel_ratio
                    )
                    self._bump("qpixmap_creations")
            if error is not None:
                terminal_errors += 1
            frame_pages.append(
                ZipRasterFramePage(
                    rendered.page_index,
                    rendered.image_id,
                    rendered.logical_image_id,
                    rendered.original_size,
                    pixmap,
                    None,
                    error,
                    rendered.split_range,
                    rendered.source_is_preview,
                )
            )
            source_keys.append(rendered.source_key)
        if not frame_pages:
            if self._source_hydration_key == result.key:
                self._restore_source_hydration_frame()
            self._drive()
            self._emit_idle_if_needed()
            return
        if terminal_errors:
            self._bump("terminal_errors", terminal_errors)
            if self._source_hydration_key == result.key:
                hydration_frame = self._source_hydration_frame
                self._restore_source_hydration_frame()
                self._enforce_combined_budget()
                request = self._current_request
                if (
                    hydration_frame is not None
                    and request is not None
                    and not self._dispatch_suspended
                    and result.key == self._current_key
                ):
                    self.frameReady.emit(
                        self._public_frame(request, hydration_frame, True)
                    )
                self._drive()
                self._emit_idle_if_needed()
                return
            if existing_before_result_is_ready:
                # A source-hydration job may finish and queue its callback just
                # before navigation restores the withheld ready QPixmap.  The
                # old key can still be a relevant neighbor of the new request,
                # but a late terminal result must never replace that known-good
                # frame with an error artifact.  Successful late work remains
                # eligible to refresh the cached source below.
                self._enforce_combined_budget()
                self._drive()
                self._emit_idle_if_needed()
                return
        gui_ready = monotonic()
        cached = _CachedFrame(
            result.key,
            result.unit,
            tuple(frame_pages),
            tuple(source_keys),
            result.completed_at,
            gui_ready,
        )
        if self._source_hydration_key == result.key:
            self._source_hydration_key = None
            self._source_hydration_frame = None
        retained, evicted = self._frame_store.put(cached)
        if evicted:
            self._bump("cache_evictions", evicted)
        self._enforce_combined_budget()
        retained = result.key in self._frame_store
        if result.key != self._current_key and not retained:
            # The artifact could not coexist with the protected current frame
            # under the byte/unit budget.  Do not immediately decode the same
            # prefetch forever; a new request/work order may retry it.
            self._failed_prefetch.add(result.key)
        self.artifactReady.emit(cached)
        request = self._current_request
        if (
            request is not None
            and not self._dispatch_suspended
            and result.key == self._current_key
            and not (
                existing_before_result_is_ready
                and result.request_id != request.request_id
            )
        ):
            self.frameReady.emit(self._public_frame(request, cached, False))
        self._drive()
        self._emit_idle_if_needed()

    def _result_is_relevant(self, result: _JobResult) -> bool:
        request = self._current_request
        return bool(
            self._accepting_requests
            and request is not None
            and result.key.source_epoch == self.source_epoch
            and result.key.source_identity == self._source_identity
            and result.key in self._work_keys
        )

    def _key_for(
        self,
        unit: ZipRasterDisplayUnit,
        render_spec: ZipRasterRenderSpec,
    ) -> _UnitKey:
        return _UnitKey(
            self.source_epoch,
            self._source_identity,
            unit.identity,
            unit.is_single,
            render_spec,
        )

    def _public_frame(
        self,
        request: ZipRasterRequest,
        cached: _CachedFrame,
        cache_hit: bool,
    ) -> ZipRasterFrame:
        pages: list[ZipRasterFramePage] = []
        for index, page in enumerate(cached.pages):
            source = self._source_for_frame_page(
                cached,
                index,
                page,
                touch=True,
            )
            pages.append(
                replace(
                    page,
                    source_qimage=(
                        QImage(source.qimage) if source is not None else None
                    ),
                    source_is_preview=(
                        source.source_is_preview
                        if source is not None
                        else page.source_is_preview
                    ),
                )
            )
        return ZipRasterFrame(
            self.source_epoch,
            self._source_identity,
            request.request_id,
            cached.unit,
            tuple(pages),
            bool(cache_hit),
            cached.worker_completed_at,
            cached.gui_ready_at,
        )

    def _source_for_frame_page(
        self,
        cached: _CachedFrame,
        index: int,
        page: ZipRasterFramePage,
        *,
        touch: bool,
    ) -> _CachedSource | None:
        source_key = (
            cached.source_keys[index]
            if index < len(cached.source_keys)
            else None
        )
        source = self._source_store.get(source_key, touch=touch)
        if source is not None:
            return source
        logical_page = next(
            (
                candidate
                for candidate in cached.unit.pages
                if candidate.page_index == page.page_index
                and candidate.image_id == page.logical_image_id
            ),
            None,
        )
        if logical_page is None:
            return None
        # A later full-resolution decode may have dominance-replaced the exact
        # preview key recorded by this frame.  Reattach that sufficient source
        # without a worker job.
        return self._source_store.find(
            logical_page,
            cached.key.render_spec,
            touch=touch,
        )

    def _restore_source_hydration_frame(self) -> bool:
        frame = self._source_hydration_frame
        self._source_hydration_key = None
        self._source_hydration_frame = None
        if frame is None:
            return False
        retained, evicted = self._frame_store.put(frame)
        if evicted:
            self._bump("cache_evictions", evicted)
        return retained

    def _record_source_evictions(self, amount: int) -> None:
        if amount <= 0:
            return
        self._bump("source_cache_evictions", amount)
        self._bump("cache_evictions", amount)

    def _enforce_combined_budget(self) -> None:
        while self.cache_bytes > self._cache_byte_budget:
            if self._source_store.evict_one():
                self._record_source_evictions(1)
                continue
            if self._frame_store.evict_one():
                self._bump("cache_evictions")
                continue
            # The complete current frame and its current source are protected.
            # One exceptionally large page may therefore exceed the soft book
            # budget, matching the old-frame/atomic-commit guarantee.
            break

    def _emit_idle_if_needed(self) -> None:
        if self._active_job is None and not self.has_unfinished_tasks():
            self.idle.emit(self)

    def _bump(self, field: str, amount: int = 1) -> None:
        self._metrics = replace(
            self._metrics,
            **{
                field: int(getattr(self._metrics, field)) + int(amount),
            },
        )


class ZipRasterBookRuntime(RasterBookRuntime):
    """Book-scoped raster runtime constrained to a ZIP source."""

    _source_type = ZipImageSource
    _runtime_display_name = "ZIP"
