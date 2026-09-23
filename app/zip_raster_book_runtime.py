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

from .i18n import tr


from collections import OrderedDict
from dataclasses import dataclass, field, replace
from heapq import heapify, heappop, heappush
from math import ceil, isfinite
from pathlib import Path
from threading import Event, Lock
from time import monotonic

from PIL import Image, ImageEnhance
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QImage, QPixmap

from .archive_backend import ArchiveErrorCode
from .image_source import ImageSource, ImageSourceError, ZipImageSource
from .image_work_coordinator import ImageWorkCoordinator, ImageWorkPriority
from .raster_layout_metadata import (
    LAYOUT_METADATA_BATCH_PAGES,
    RasterLayoutMetadata,
    probe_layout_metadata,
    select_layout_metadata_pages,
)
from .raster_admission_policy import (
    RasterAdmissionAction,
    RasterAdmissionDecision,
    RasterAdmissionPolicy,
)
from .raster_warmup_planner import (
    RasterWarmupPlan,
    RasterWarmupPlanner,
    WarmupStopReason,
)
from .thumbnail_render import pil_to_qimage
from .viewer_render import (
    ViewerRenderKey,
    normalize_downscale_algorithm,
    normalize_resampling_mode,
    normalize_upscale_algorithm,
    qimage_to_pillow,
    render_qimage,
    resampling_policy_for_legacy_mode,
)
from .viewer_widget import calculate_spread_layout


_DEFAULT_CACHE_BYTES = 256 * 1024 * 1024
_JPEG_SUFFIXES = frozenset({".jpg", ".jpeg", ".jpe"})
_FIT_PREVIEW_MODES = frozenset(
    {"fit_window", "fit_no_upscale", "fit_width", "fit_height"}
)
_UNKNOWN_JPEG_ASPECT_LIMIT = 4
_MAX_EXACT_RENDER_PIXELS = 64 * 1024 * 1024
DecoderMaximumSize = tuple[int | None, int | None]


def _bounded_physical_render_size(
    render_spec: "ZipRasterRenderSpec",
    target_size: tuple[int, int],
) -> tuple[int, int]:
    """Keep ordinary frames exact while bounding pathological manual zoom.

    Fit/actual-size frames always retain their exact physical dimensions.
    Manual zoom can request a multi-gigabyte monolithic artifact; until the
    Viewer gains tiled zoom rendering, cap only that exceptional artifact and
    let QPainter enlarge it.  This safety valve is unrelated to cache page
    count and does not affect normal-display quality.
    """

    width = max(1, int(target_size[0]))
    height = max(1, int(target_size[1]))
    pixels = width * height
    if render_spec.fit_mode != "manual_zoom" or pixels <= _MAX_EXACT_RENDER_PIXELS:
        return width, height
    scale = (_MAX_EXACT_RENDER_PIXELS / pixels) ** 0.5
    return max(1, round(width * scale)), max(1, round(height * scale))


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
    # The combined mode and smooth flag are constructor-only compatibility
    # inputs.  Cache equality is owned by the normalized algorithms below.
    resampling_mode: str = field(default="standard", compare=False)
    smooth_scaling: bool = field(default=True, compare=False)
    downscale_algorithm: str | None = None
    upscale_algorithm: str | None = None
    split_wide_image: bool = False
    reading_direction: str = "ltr"
    brightness: float = 1.0
    contrast: float = 1.0
    gamma: float = 1.0
    decoder_maximum_size: DecoderMaximumSize | None = None
    decoder_headroom: float = 1.0
    decoder_layout_sized: bool = False

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
            decoder_size = tuple(
                None if value is None else max(1, int(value))
                for value in decoder_size
            )
            if decoder_size == (None, None):
                decoder_size = None
        decoder_headroom = float(self.decoder_headroom)
        if not isfinite(decoder_headroom):
            raise ValueError("decoder_headroom must be finite")
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
        legacy_policy = resampling_policy_for_legacy_mode(self.resampling_mode)
        legacy_downscale = legacy_policy.downscale_algorithm
        legacy_upscale = legacy_policy.upscale_algorithm
        if self.resampling_mode == "standard" and not self.smooth_scaling:
            legacy_downscale, legacy_upscale = "fast", "nearest"
        object.__setattr__(
            self,
            "downscale_algorithm",
            normalize_downscale_algorithm(
                self.downscale_algorithm
                if self.downscale_algorithm is not None
                else legacy_downscale
            ),
        )
        object.__setattr__(
            self,
            "upscale_algorithm",
            normalize_upscale_algorithm(
                self.upscale_algorithm
                if self.upscale_algorithm is not None
                else legacy_upscale
            ),
        )
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
        object.__setattr__(
            self,
            "decoder_headroom",
            min(2.0, max(1.0, decoder_headroom)),
        )
        object.__setattr__(
            self,
            "decoder_layout_sized",
            bool(self.decoder_layout_sized),
        )

    @property
    def adjustments(self) -> tuple[float, float, float]:
        return self.brightness, self.contrast, self.gamma


def _size_within_bounds(
    logical_size: tuple[int, int],
    maximum_size: DecoderMaximumSize,
) -> tuple[int, int]:
    """Return the aspect-preserving raster size required by axis bounds."""

    width = max(1, int(logical_size[0]))
    height = max(1, int(logical_size[1]))
    scales = [1.0]
    if maximum_size[0] is not None:
        scales.append(max(1, int(maximum_size[0])) / width)
    if maximum_size[1] is not None:
        scales.append(max(1, int(maximum_size[1])) / height)
    scale = min(scales)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _qimage_satisfies_source_requirement(
    qimage: QImage,
    original_size: tuple[int, int],
    maximum_size: DecoderMaximumSize | None,
) -> bool:
    if maximum_size is None:
        return (qimage.width(), qimage.height()) == original_size
    required_width, required_height = _size_within_bounds(
        original_size,
        maximum_size,
    )
    return (
        qimage.width() >= required_width
        and qimage.height() >= required_height
    )


def _add_decoder_headroom(
    maximum_size: DecoderMaximumSize,
    headroom: float,
) -> DecoderMaximumSize:
    return tuple(
        None if value is None else max(1, ceil(value * headroom))
        for value in maximum_size
    )


def _display_frame_bytes_for_sizes(
    unit: ZipRasterDisplayUnit,
    render_spec: ZipRasterRenderSpec,
    logical_sizes: tuple[tuple[int, int], ...],
    *,
    source_sizes: tuple[tuple[int, int], ...] | None = None,
) -> int:
    """Estimate the complete display artifact from final layout geometry."""

    expanded: list[tuple[tuple[int, int], tuple[int, int] | None]] = []
    for index, size in enumerate(logical_sizes):
        width, height = size
        source_size = None if source_sizes is None else source_sizes[index]
        if (
            render_spec.split_wide_image
            and unit.is_single
            and width >= 2
            and width / max(1, height) >= 1.25
        ):
            left = width // 2
            if source_size is not None and source_size[0] < 2:
                expanded.append(((width, height), source_size))
            elif source_size is None:
                expanded.extend(
                    (
                        ((left, height), source_size),
                        ((width - left, height), source_size),
                    )
                )
            else:
                source_width, source_height = source_size
                left_pixels = max(
                    1,
                    min(
                        source_width - 1,
                        round(source_width * left / width),
                    ),
                )
                expanded.extend(
                    (
                        ((left, height), (left_pixels, source_height)),
                        (
                            (width - left, height),
                            (source_width - left_pixels, source_height),
                        ),
                    )
                )
        else:
            expanded.append(((width, height), source_size))
    rotation = render_spec.rotation
    layout_sizes = [
        (height, width) if rotation in {90, 270} else (width, height)
        for (width, height), _source_size in expanded
    ]
    layout = calculate_spread_layout(
        layout_sizes,
        render_spec.viewport_size,
        fit_mode=render_spec.fit_mode,
        manual_zoom=render_spec.manual_zoom,
        gap=render_spec.gap,
        join_spread_pages=render_spec.join_spread_pages,
        spread_is_single=unit.is_single,
        horizontal_alignment=render_spec.horizontal_alignment,
    )
    dpr = render_spec.device_pixel_ratio
    frame_bytes = 0
    for (_logical_size, source_size), rect in zip(expanded, layout.rects):
        target_width = max(1, round(rect.width() * dpr))
        target_height = max(1, round(rect.height() * dpr))
        target_width, target_height = _bounded_physical_render_size(
            render_spec,
            (target_width, target_height),
        )
        frame_bytes += target_width * target_height * 4
    return max(1, frame_bytes)


def _decoder_maximum_for_page(
    render_spec: ZipRasterRenderSpec,
    unit: ZipRasterDisplayUnit,
    page: ZipRasterPage,
) -> DecoderMaximumSize | None:
    """Plan one page's decoder-sized source from the final physical slot.

    The old path used the whole Viewer rectangle for every page and disabled
    scaled decode for rotation, split pages and one-axis fit modes.  Production
    requests opt into this layout-aware plan; explicit low-level specs keep the
    historical box semantics for compatibility.
    """

    maximum_size = render_spec.decoder_maximum_size
    if maximum_size is None:
        return None
    headroom = render_spec.decoder_headroom
    rotation = render_spec.rotation % 360
    if not render_spec.decoder_layout_sized:
        planned = _add_decoder_headroom(maximum_size, headroom)
        return (planned[1], planned[0]) if rotation in {90, 270} else planned

    all_sizes_known = all(candidate.known_size is not None for candidate in unit.pages)
    if all_sizes_known and render_spec.fit_mode in _FIT_PREVIEW_MODES:
        expanded: list[
            tuple[ZipRasterPage, tuple[int, int], tuple[int, int]]
        ] = []
        for candidate in unit.pages:
            assert candidate.known_size is not None
            logical_width, logical_height = candidate.known_size
            if (
                render_spec.split_wide_image
                and unit.is_single
                and logical_width >= 2
                and logical_width / max(1, logical_height) >= 1.25
            ):
                left_width = logical_width // 2
                for part_width in (left_width, logical_width - left_width):
                    expanded.append(
                        (
                            candidate,
                            (part_width, logical_height),
                            (logical_width, logical_height),
                        )
                    )
            else:
                expanded.append(
                    (
                        candidate,
                        (logical_width, logical_height),
                        (logical_width, logical_height),
                    )
                )
        layout_sizes = tuple(
            (part_height, part_width)
            if rotation in {90, 270}
            else (part_width, part_height)
            for _candidate, (part_width, part_height), _source_size in expanded
        )
        layout = calculate_spread_layout(
            layout_sizes,
            render_spec.viewport_size,
            fit_mode=render_spec.fit_mode,
            manual_zoom=render_spec.manual_zoom,
            gap=render_spec.gap,
            join_spread_pages=render_spec.join_spread_pages,
            spread_is_single=unit.is_single,
            horizontal_alignment=render_spec.horizontal_alignment,
        )
        required_scale = 0.0
        source_size = page.known_size
        assert source_size is not None
        for expanded_part, target_rect in zip(expanded, layout.rects):
            candidate, (part_width, part_height), _whole_size = expanded_part
            if candidate.image_id != page.image_id:
                continue
            target_width = max(
                1,
                ceil(target_rect.width() * render_spec.device_pixel_ratio),
            )
            target_height = max(
                1,
                ceil(target_rect.height() * render_spec.device_pixel_ratio),
            )
            if rotation in {90, 270}:
                target_width, target_height = target_height, target_width
            required_scale = max(
                required_scale,
                target_width / max(1, part_width),
                target_height / max(1, part_height),
            )
        required_scale = min(1.0, max(0.0, required_scale) * headroom)
        if required_scale > 0:
            return (
                max(1, ceil(source_size[0] * required_scale)),
                max(1, ceil(source_size[1] * required_scale)),
            )

    viewport_width, viewport_height = render_spec.viewport_size
    dpr = render_spec.device_pixel_ratio
    physical_width = max(1, ceil(viewport_width * dpr))
    physical_height = max(1, ceil(viewport_height * dpr))
    if len(unit.pages) == 2:
        effective_gap = 0 if render_spec.join_spread_pages else render_spec.gap
        slot_width = max(1, (viewport_width - effective_gap) // 2)
        planned: DecoderMaximumSize = (
            max(1, ceil(slot_width * dpr)),
            physical_height,
        )
    elif render_spec.split_wide_image and unit.is_single:
        if rotation in {90, 270}:
            planned = (physical_height * 2, physical_width)
        else:
            planned = (physical_width, physical_height)
        return _add_decoder_headroom(planned, headroom)
    else:
        planned = maximum_size
    planned = _add_decoder_headroom(planned, headroom)
    return (planned[1], planned[0]) if rotation in {90, 270} else planned


def _requires_exact_prefetch_admission(
    page: ZipRasterPage,
    decoder_maximum: DecoderMaximumSize | None,
    *,
    has_cached_source: bool,
) -> bool:
    """Route every unknown logical geometry through worker confirmation.

    Cached pixels can provide dimensions without I/O, but do not make an
    unknown page descriptor safe for GUI-side frame estimation. The worker
    resolves cached/known dimensions first and probes only when needed.
    """

    del decoder_maximum, has_cached_source
    return page.known_size is None


@dataclass(frozen=True)
class ZipRasterRequest:
    source_epoch: int
    request_id: int
    current: ZipRasterDisplayUnit
    warmup_plan: RasterWarmupPlan[
        ZipRasterDisplayUnit,
        tuple[tuple[int, str], ...],
    ]
    render_spec: ZipRasterRenderSpec
    navigation_direction: int = 0
    resolve_layout_metadata: bool = False

    def __post_init__(self) -> None:
        if self.warmup_plan.current_identity != self.current.identity:
            raise ValueError("warm-up plan current must match request current")
        object.__setattr__(self, "source_epoch", int(self.source_epoch))
        object.__setattr__(self, "request_id", int(self.request_id))
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
    startup_runway_releases: int = 0
    continuous_warmup_releases: int = 0
    warmup_planner_creations: int = 0
    warmup_planner_recenters: int = 0
    work_order_changes: int = 0
    running_job_adoptions: int = 0
    queued_job_replacements: int = 0
    finished_job_slot_releases: int = 0
    compatible_old_results: int = 0
    prefetch_admission_stops: int = 0
    oversized_prefetch_skips: int = 0
    layout_metadata_jobs: int = 0
    layout_metadata_pages: int = 0


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
    admission_declined: bool
    oversized_prefetch: bool
    completed_at: float
    layout_metadata: RasterLayoutMetadata | None = None


@dataclass(frozen=True)
class _CachedFrame:
    key: _UnitKey
    unit: ZipRasterDisplayUnit
    pages: tuple[ZipRasterFramePage, ...]
    source_keys: tuple[_SourceKey | None, ...]
    worker_completed_at: float
    gui_ready_at: float


# Lexicographic shared-cache value: layout scope, artifact class
# (display frame before rehydratable source), current-relative locality,
# source-variant penalty, direction penalty. Higher tuples are reclaimed first.
_ArtifactRetentionRank = tuple[int, int, int, int, int]


class _ByteLedger:
    """Track aggregate and largest artifact sizes without rescanning a store."""

    def __init__(self) -> None:
        self.total = 0
        self._largest_heap: list[int] = []
        self._removed_sizes: dict[int, int] = {}
        self._live_sizes: dict[int, int] = {}
        self._item_count = 0

    @property
    def largest(self) -> int:
        while self._largest_heap:
            size = -self._largest_heap[0]
            removed = self._removed_sizes.get(size, 0)
            if removed <= 0:
                return size
            heappop(self._largest_heap)
            if removed == 1:
                self._removed_sizes.pop(size, None)
            else:
                self._removed_sizes[size] = removed - 1
        return 0

    def add(self, size: int) -> None:
        normalized = max(0, int(size))
        self.total += normalized
        self._item_count += 1
        if normalized:
            self._live_sizes[normalized] = (
                self._live_sizes.get(normalized, 0) + 1
            )
            heappush(self._largest_heap, -normalized)

    def remove(self, size: int) -> None:
        normalized = max(0, int(size))
        self.total -= normalized
        self._item_count -= 1
        if normalized:
            live = self._live_sizes[normalized]
            if live == 1:
                self._live_sizes.pop(normalized)
            else:
                self._live_sizes[normalized] = live - 1
            self._removed_sizes[normalized] = (
                self._removed_sizes.get(normalized, 0) + 1
            )
        self._compact_if_needed()

    def _compact_if_needed(self) -> None:
        # A long-lived large artifact can otherwise keep tombstones for many
        # smaller replacements below the heap root indefinitely.  Rebuilding
        # at a bounded ratio keeps add/remove amortized O(log N) and memory O(N).
        if len(self._largest_heap) <= max(64, self._item_count * 2):
            return
        self._largest_heap = [
            -size
            for size, count in self._live_sizes.items()
            for _unused in range(count)
        ]
        heapify(self._largest_heap)
        self._removed_sizes.clear()

    def clear(self) -> None:
        self.total = 0
        self._largest_heap.clear()
        self._removed_sizes.clear()
        self._live_sizes.clear()
        self._item_count = 0


class _ZipRasterSourceStore:
    """Keep decoded raster sources independent from layout-specific frames.

    ZipPlaFork keeps the source bitmap and its resized display bitmap in the
    same page lifetime.  This Qt adaptation preserves that useful lifetime
    split without importing WinForms/GDI ownership: immutable ``QImage``
    sources remain book-scoped, while ``QPixmap`` frames remain GUI/layout
    scoped.  A smaller preview can satisfy an equal-or-smaller layout; a full
    source can satisfy every later layout, rotation, DPI, or magnifier request.
    """

    def __init__(self) -> None:
        self._sources: OrderedDict[_SourceKey, _CachedSource] = OrderedDict()
        self._sources_by_identity: dict[
            tuple[str, tuple[float, float, float]],
            OrderedDict[_SourceKey, None],
        ] = {}
        self._bytes = _ByteLedger()
        self._eviction_order: list[_SourceKey] | None = None
        self._retention_plan: RasterWarmupPlan[
            ZipRasterDisplayUnit,
            tuple[tuple[int, str], ...],
        ] | None = None
        self._active_anchor: int | None = None
        self._current_unit: ZipRasterDisplayUnit | None = None
        self._current_render_spec: ZipRasterRenderSpec | None = None
        self._displayed_unit: ZipRasterDisplayUnit | None = None
        self._displayed_render_spec: ZipRasterRenderSpec | None = None
        self._direction = 0

    @property
    def page_count(self) -> int:
        return len(self._sources)

    @property
    def byte_size(self) -> int:
        return self._bytes.total

    @property
    def largest_source_bytes(self) -> int:
        return self._bytes.largest

    @property
    def page_indexes(self) -> tuple[int, ...]:
        return tuple(source.page_index for source in self._sources.values())

    def clear(self) -> None:
        self._sources.clear()
        self._sources_by_identity.clear()
        self._bytes.clear()
        self._eviction_order = None
        self._displayed_unit = None
        self._displayed_render_spec = None

    def set_retention_plan(
        self,
        plan: RasterWarmupPlan[
            ZipRasterDisplayUnit,
            tuple[tuple[int, str], ...],
        ] | None,
        current: ZipRasterDisplayUnit | None,
        render_spec: ZipRasterRenderSpec | None,
        direction: int,
    ) -> int:
        self._retention_plan = plan
        self._active_anchor = (
            min((page.page_index for page in current.pages), default=0)
            if current is not None
            else None
        )
        self._current_unit = current
        self._current_render_spec = render_spec
        normalized = int(direction)
        self._direction = -1 if normalized < 0 else 1 if normalized > 0 else 0
        self._eviction_order = None
        return 0

    def set_displayed_unit(
        self,
        unit: ZipRasterDisplayUnit | None,
        render_spec: ZipRasterRenderSpec | None,
    ) -> None:
        """Protect source pixels still referenced by the painted frame."""

        self._displayed_unit = unit
        self._displayed_render_spec = render_spec
        self._eviction_order = None

    def find(
        self,
        page: ZipRasterPage,
        render_spec: ZipRasterRenderSpec,
        *,
        unit: ZipRasterDisplayUnit | None = None,
        touch: bool = False,
    ) -> _CachedSource | None:
        display_unit = unit or ZipRasterDisplayUnit(
            page.page_index,
            (page,),
            True,
        )
        required_size = _decoder_maximum_for_page(
            render_spec,
            display_unit,
            page,
        )
        group = self._sources_by_identity.get(
            (page.image_id, render_spec.adjustments)
        )
        candidates = tuple(
            self._sources[key]
            for key in group or ()
            if self._satisfies(self._sources[key], required_size)
        )
        if not candidates:
            return None
        if required_size is not None:
            preview_candidates = tuple(
                source for source in candidates if source.source_is_preview
            )
            if preview_candidates:
                candidates = preview_candidates
        # Prefer the smallest sufficient source.  This avoids making normal
        # fit-window rendering walk a full-resolution image while a retained
        # decoder-sized source is still sufficient.
        selected = min(candidates, key=lambda source: source.qimage.sizeInBytes())
        if touch:
            self._touch(selected.key)
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
            self._touch(key)
        return source

    def put(self, source: _CachedSource) -> int:
        evicted = 0
        group = self._sources_by_identity.get(self._identity_for(source.key))
        dominated = tuple(
            key
            for key in group or ()
            for existing in (self._sources[key],)
            if key != source.key
            and self._dominates(source, existing)
        )
        for key in dominated:
            self._remove(key)
            evicted += 1
        self._store(source)
        return evicted

    def evict_one(self) -> bool:
        self._ensure_eviction_order()
        candidate = (
            self._eviction_order.pop()
            if self._eviction_order
            else None
        )
        if candidate is None:
            return False
        self._remove(candidate, invalidate_eviction_order=False)
        return True

    def lower_rank_reclaim_candidates(
        self,
        unit: ZipRasterDisplayUnit,
        render_spec: ZipRasterRenderSpec,
        *,
        excluded_keys: frozenset[_SourceKey] = frozenset(),
        retention_boundary: _ArtifactRetentionRank | None = None,
        include_navigation_sources: bool = False,
    ) -> tuple[tuple[_SourceKey, int, _ArtifactRetentionRank], ...]:
        """Return worst-first sources below a pending artifact's value.

        Ordinary callers preserve current/displayed/near source ownership.
        Display-frame admission may include those rehydratable navigation
        sources and compare them with the pending *frame* rank. This lets a
        constrained cache form an immediately paintable runway instead of
        retaining one decoded source beside every already-independent QPixmap.
        The pending unit's own reusable source remains protected.
        """

        candidate_ranks: list[_ArtifactRetentionRank] = []
        protected = (
            set()
            if include_navigation_sources
            else set(self._protected_keys())
        )
        for page in unit.pages:
            plan = self._retention_plan
            active_rank = (
                plan.rank_for_page(page.page_index) if plan is not None else None
            )
            if active_rank is None:
                return ()
            decoder_maximum = _decoder_maximum_for_page(
                render_spec,
                unit,
                page,
            )
            candidate_ranks.append(
                self._rank_for_request(
                    page.page_index,
                    render_spec,
                    source_is_preview=decoder_maximum is not None,
                )
            )
            reusable = self.find(page, render_spec, unit=unit, touch=False)
            if reusable is not None:
                # Admission estimates this page as a source hit and _submit()
                # passes the same variant to the worker.  Never reclaim it
                # between the estimate and job construction.
                protected.add(reusable.key)
        if not candidate_ranks:
            return ()
        candidate_boundary = (
            max(candidate_ranks)
            if retention_boundary is None
            else retention_boundary
        )
        if include_navigation_sources:
            positions = {
                key: index for index, key in enumerate(self._sources)
            }
            eviction_order = sorted(
                self._sources,
                key=lambda key: (
                    self._retention_rank(self._sources[key]),
                    -positions[key],
                ),
            )
        else:
            self._ensure_eviction_order()
            eviction_order = self._eviction_order or ()
        return tuple(
            (
                key,
                self._sources[key].qimage.sizeInBytes(),
                self._retention_rank(self._sources[key]),
            )
            for key in reversed(eviction_order)
            if key not in protected
            and key not in excluded_keys
            and self._retention_rank(self._sources[key]) > candidate_boundary
        )

    def projected_put_delta(
        self,
        sources: tuple[_CachedSource, ...],
    ) -> tuple[int, frozenset[_SourceKey]]:
        """Return exact byte delta and keys superseded by ordered puts."""

        projected_groups: dict[
            tuple[str, tuple[float, float, float]],
            dict[_SourceKey, _CachedSource],
        ] = {}
        original_keys: set[_SourceKey] = set()
        for source in sources:
            identity = self._identity_for(source.key)
            projected = projected_groups.get(identity)
            if projected is None:
                group = self._sources_by_identity.get(identity)
                projected = {
                    key: self._sources[key] for key in group or ()
                }
                projected_groups[identity] = projected
                original_keys.update(projected)
            for key, existing in tuple(projected.items()):
                if key == source.key or self._dominates(source, existing):
                    projected.pop(key, None)
            projected[source.key] = source
        original_bytes = sum(
            self._sources[key].qimage.sizeInBytes()
            for key in original_keys
        )
        projected_bytes = sum(
            source.qimage.sizeInBytes()
            for projected in projected_groups.values()
            for source in projected.values()
        )
        projected_keys = {
            key
            for projected in projected_groups.values()
            for key in projected
        }
        return (
            projected_bytes - original_bytes,
            frozenset(original_keys - projected_keys),
        )

    def remove_reclaim_candidates(
        self,
        keys: tuple[_SourceKey, ...],
    ) -> tuple[int, int]:
        removed = 0
        freed = 0
        for key in keys:
            source = self._remove(key, invalidate_eviction_order=False)
            if source is None:
                continue
            removed += 1
            freed += source.qimage.sizeInBytes()
        self._eviction_order = None
        return removed, freed

    @staticmethod
    def _identity_for(
        key: _SourceKey,
    ) -> tuple[str, tuple[float, float, float]]:
        return (key.image_id, key.adjustments)

    def _store(self, source: _CachedSource) -> None:
        self._remove(source.key)
        self._sources[source.key] = source
        group = self._sources_by_identity.setdefault(
            self._identity_for(source.key),
            OrderedDict(),
        )
        group[source.key] = None
        self._bytes.add(source.qimage.sizeInBytes())
        self._eviction_order = None

    def _remove(
        self,
        key: _SourceKey,
        *,
        invalidate_eviction_order: bool = True,
    ) -> _CachedSource | None:
        source = self._sources.pop(key, None)
        if source is None:
            return None
        self._bytes.remove(source.qimage.sizeInBytes())
        identity = self._identity_for(key)
        group = self._sources_by_identity.get(identity)
        if group is not None:
            group.pop(key, None)
            if not group:
                self._sources_by_identity.pop(identity, None)
        if invalidate_eviction_order:
            self._eviction_order = None
        return source

    def _touch(self, key: _SourceKey) -> None:
        self._sources.move_to_end(key)
        group = self._sources_by_identity.get(self._identity_for(key))
        if group is not None:
            group.move_to_end(key)
        self._eviction_order = None

    def _eviction_candidate(self) -> _SourceKey | None:
        self._ensure_eviction_order()
        return self._eviction_order[-1] if self._eviction_order else None

    def worst_reclaim_candidate(
        self,
    ) -> tuple[_SourceKey, int, _ArtifactRetentionRank] | None:
        key = self._eviction_candidate()
        if key is None:
            return None
        source = self._sources[key]
        return (
            key,
            source.qimage.sizeInBytes(),
            self._retention_rank(source),
        )

    def _ensure_eviction_order(self) -> None:
        if self._eviction_order is not None:
            return
        protected = self._protected_keys()
        candidates = [
            key
            for key in self._sources
            if key not in protected
        ]
        positions = {key: index for index, key in enumerate(self._sources)}
        candidates.sort(
            key=lambda key: (
                self._retention_rank(self._sources[key]),
                -positions[key],
            ),
        )
        self._eviction_order = candidates

    def _protected_keys(self) -> frozenset[_SourceKey]:
        render_spec = self._current_render_spec
        current_unit = self._current_unit
        protected: set[_SourceKey] = set()
        if render_spec is not None and current_unit is not None:
            self._protect_unit_sources(
                protected,
                current_unit,
                render_spec,
                keep_navigation_preview=True,
            )
        displayed_unit = self._displayed_unit
        displayed_render_spec = self._displayed_render_spec
        if displayed_unit is not None and displayed_render_spec is not None:
            self._protect_unit_sources(
                protected,
                displayed_unit,
                displayed_render_spec,
                keep_navigation_preview=True,
            )
        plan = self._retention_plan
        if (
            plan is not None
            and plan.background_enabled
            and render_spec is not None
        ):
            for unit in plan.iter_background_units():
                rank = plan.rank_for_identity(unit.identity)
                if rank not in {1, 2}:
                    if rank is not None and rank > 2:
                        break
                    continue
                self._protect_unit_sources(
                    protected,
                    unit,
                    render_spec,
                    keep_navigation_preview=False,
                )
                if rank == 2:
                    break
        return frozenset(protected)

    def _protect_unit_sources(
        self,
        protected: set[_SourceKey],
        unit: ZipRasterDisplayUnit,
        render_spec: ZipRasterRenderSpec,
        *,
        keep_navigation_preview: bool,
    ) -> None:
        for page in unit.pages:
            source = self.find(page, render_spec, unit=unit)
            if source is not None:
                protected.add(source.key)
            if not keep_navigation_preview or render_spec.decoder_maximum_size is not None:
                continue
            group = self._sources_by_identity.get(
                (page.image_id, render_spec.adjustments)
            )
            previews = tuple(
                self._sources[key]
                for key in group or ()
                if self._sources[key].source_is_preview
            )
            if previews:
                protected.add(
                    min(
                        previews,
                        key=lambda candidate: candidate.qimage.sizeInBytes(),
                    ).key
                )

    def _rank_for_request(
        self,
        page_index: int,
        render_spec: ZipRasterRenderSpec,
        *,
        source_is_preview: bool,
    ) -> _ArtifactRetentionRank:
        plan = self._retention_plan
        active_rank = (
            plan.rank_for_page(page_index) if plan is not None else None
        )
        adjustments_match = (
            self._current_render_spec is not None
            and render_spec.adjustments == self._current_render_spec.adjustments
        )
        if active_rank is not None and adjustments_match:
            scope = 0
            locality = active_rank
        else:
            scope = 1 if adjustments_match else 2
            locality = self._fallback_distance(page_index)
        return (
            scope,
            1,
            locality,
            int(not source_is_preview),
            self._direction_penalty(page_index),
        )

    def _retention_rank(
        self,
        source: _CachedSource,
    ) -> _ArtifactRetentionRank:
        # Full sources are interactive promotion artifacts.  Outside an active
        # full-source request, evict them before the smaller navigation preview
        # at the same locality so a magnifier session cannot poison page turns.
        render_spec = self._current_render_spec
        adjustments_match = bool(
            render_spec is not None
            and source.key.adjustments == render_spec.adjustments
        )
        plan = self._retention_plan
        active_rank = (
            plan.rank_for_page(source.page_index)
            if plan is not None and adjustments_match
            else None
        )
        if active_rank is not None:
            scope = 0
            locality = active_rank
        else:
            scope = 1 if adjustments_match else 2
            locality = self._fallback_distance(source.page_index)
        return (
            scope,
            1,
            locality,
            int(not source.source_is_preview),
            self._direction_penalty(source.page_index),
        )

    def _fallback_distance(self, page_index: int) -> int:
        if self._active_anchor is None:
            return 0
        return abs(int(page_index) - self._active_anchor)

    def _direction_penalty(self, page_index: int) -> int:
        if self._active_anchor is None:
            return 0
        delta = int(page_index) - self._active_anchor
        return int(
            self._direction != 0
            and delta != 0
            and (1 if delta > 0 else -1) != self._direction
        )

    @staticmethod
    def _satisfies(
        source: _CachedSource,
        maximum_size: DecoderMaximumSize | None,
    ) -> bool:
        if not source.source_is_preview:
            return True
        return _qimage_satisfies_source_requirement(
            source.qimage,
            source.original_size,
            maximum_size,
        )

    @staticmethod
    def _dominates(candidate: _CachedSource, existing: _CachedSource) -> bool:
        if candidate.source_is_preview != existing.source_is_preview:
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
    request.  This store keeps completed work until the book-wide byte policy
    requires eviction, while the current -> next -> previous order ranks the
    protected neighborhood.  Artifact count is diagnostic only; it never
    limits residency.

    The full-book order is represented by one immutable topology plus a lazy
    distance/direction cursor instead of allocating and sorting a work object
    for every page on every wheel event.
    """

    def __init__(self) -> None:
        self._frames: OrderedDict[_UnitKey, _CachedFrame] = OrderedDict()
        self._bytes = _ByteLedger()
        self._eviction_order: list[_UnitKey] | None = None
        self._retention_plan: RasterWarmupPlan[
            ZipRasterDisplayUnit,
            tuple[tuple[int, str], ...],
        ] | None = None
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
        return self._bytes.total

    @property
    def largest_frame_bytes(self) -> int:
        return self._bytes.largest

    def get(self, key: _UnitKey, *, touch: bool = False) -> _CachedFrame | None:
        frame = self._frames.get(key)
        if frame is not None and touch:
            self._frames.move_to_end(key)
            self._eviction_order = None
        return frame

    def values(self) -> tuple[_CachedFrame, ...]:
        return tuple(self._frames.values())

    def clear(self) -> None:
        self._frames.clear()
        self._bytes.clear()
        self._eviction_order = None

    def take(self, key: _UnitKey) -> _CachedFrame | None:
        return self._remove(key)

    def set_retention_plan(
        self,
        plan: RasterWarmupPlan[
            ZipRasterDisplayUnit,
            tuple[tuple[int, str], ...],
        ] | None,
        current_key: _UnitKey | None,
        direction: int,
    ) -> int:
        self._retention_plan = plan
        self._eviction_order = None
        self._current_key = current_key
        normalized_direction = int(direction)
        if normalized_direction < 0:
            self._direction = -1
        elif normalized_direction > 0:
            self._direction = 1
        else:
            self._direction = 0
        return 0

    def set_displayed_key(self, key: _UnitKey | None) -> int:
        """Protect the last painted frame until its replacement paints."""

        self._displayed_key = key
        self._eviction_order = None
        return 0

    def put(self, frame: _CachedFrame) -> tuple[bool, int]:
        self._remove(frame.key)
        self._frames[frame.key] = frame
        self._frames.move_to_end(frame.key)
        self._bytes.add(self._frame_bytes(frame))
        self._eviction_order = None
        return True, 0

    def can_admit_prefetch(self, key: _UnitKey) -> bool:
        return key not in self._frames

    def evict_one(self) -> bool:
        self._ensure_eviction_order()
        candidate = (
            self._eviction_order.pop()
            if self._eviction_order
            else None
        )
        if candidate is None:
            return False
        self._remove(candidate, invalidate_eviction_order=False)
        return True

    def lower_rank_reclaim_candidates(
        self,
        key: _UnitKey,
    ) -> tuple[tuple[_UnitKey, int, _ArtifactRetentionRank], ...]:
        """Return complete frames strictly below a pending unit's rank."""

        candidate_rank = self._retention_rank(key)
        self._ensure_eviction_order()
        return tuple(
            (
                candidate,
                self._frame_bytes(self._frames[candidate]),
                self._retention_rank(candidate),
            )
            for candidate in reversed(self._eviction_order or ())
            if self._retention_rank(candidate) > candidate_rank
        )

    def retention_rank(self, key: _UnitKey) -> _ArtifactRetentionRank:
        """Return the shared source/frame value of a display artifact."""

        return self._retention_rank(key)

    def remove_reclaim_candidates(
        self,
        keys: tuple[_UnitKey, ...],
    ) -> tuple[int, int]:
        removed = 0
        freed = 0
        for key in keys:
            frame = self._remove(key, invalidate_eviction_order=False)
            if frame is None:
                continue
            removed += 1
            freed += self._frame_bytes(frame)
        self._eviction_order = None
        return removed, freed

    def _remove(
        self,
        key: _UnitKey,
        *,
        invalidate_eviction_order: bool = True,
    ) -> _CachedFrame | None:
        frame = self._frames.pop(key, None)
        if frame is not None:
            self._bytes.remove(self._frame_bytes(frame))
            if invalidate_eviction_order:
                self._eviction_order = None
        return frame

    def _eviction_candidate(self) -> _UnitKey | None:
        self._ensure_eviction_order()
        return self._eviction_order[-1] if self._eviction_order else None

    def worst_reclaim_candidate(
        self,
    ) -> tuple[_UnitKey, int, _ArtifactRetentionRank] | None:
        key = self._eviction_candidate()
        if key is None:
            return None
        frame = self._frames[key]
        return key, self._frame_bytes(frame), self._retention_rank(key)

    def _ensure_eviction_order(self) -> None:
        if self._eviction_order is not None:
            return
        candidates = [
            key
            for key in self._frames
            if not self._is_protected(key)
        ]
        positions = {key: index for index, key in enumerate(self._frames)}
        candidates.sort(
            key=lambda key: (
                self._retention_rank(key),
                -positions[key],
            ),
        )
        self._eviction_order = candidates

    def _is_protected(self, key: _UnitKey) -> bool:
        if key in {self._current_key, self._displayed_key}:
            return True
        current = self._current_key
        plan = self._retention_plan
        if (
            current is None
            or plan is None
            or key.render_spec != current.render_spec
        ):
            return False
        return plan.rank_for_identity(key.unit_identity) in {1, 2}

    def _retention_rank(self, key: _UnitKey) -> _ArtifactRetentionRank:
        plan = self._retention_plan
        current = self._current_key
        same_layout = bool(
            current is not None and key.render_spec == current.render_spec
        )
        active_rank = (
            plan.rank_for_identity(key.unit_identity)
            if plan is not None and same_layout
            else None
        )
        if active_rank is not None:
            return (0, 0, active_rank, 0, 0)
        if current is None:
            return (2, 0, 0, 0, 0)
        current_anchor = self._unit_anchor(current)
        candidate_anchor = self._unit_anchor(key)
        delta = candidate_anchor - current_anchor
        direction_penalty = int(
            self._direction != 0
            and delta != 0
            and (1 if delta > 0 else -1) != self._direction
        )
        return (
            1 if same_layout else 2,
            0,
            abs(delta),
            0,
            direction_penalty,
        )

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
        layout_metadata_pages: tuple[ZipRasterPage, ...] = (),
        prefetch_budget_bytes: int | None = None,
        prefetch_hard_limit_bytes: int | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.serial = int(serial)
        self.key = key
        self.source = source
        self.unit = unit
        self.layout_metadata_pages = tuple(layout_metadata_pages)
        self.cached_sources = dict(cached_sources or {})
        self.prefetch_budget_bytes = (
            None
            if prefetch_budget_bytes is None
            else max(0, int(prefetch_budget_bytes))
        )
        self.prefetch_hard_limit_bytes = (
            None
            if prefetch_hard_limit_bytes is None
            else max(1, int(prefetch_hard_limit_bytes))
        )
        self.confirmed_required_bytes: int | None = None
        self.signals = _JobSignals()
        self.cancelled = Event()
        self.admission_declined = False
        self.oversized_prefetch = False
        self.started = Event()
        self.finished = Event()
        self.source_decode_started = Event()
        self._token_lock = Lock()
        self._request_id = int(request_id)

    def adopt_request(
        self,
        request_id: int,
        *,
        as_current: bool = False,
    ) -> None:
        with self._token_lock:
            self._request_id = int(request_id)
            if as_current:
                # A neighbor prefetch can become the interactive target while
                # its header probe is running.  Current work is never rejected
                # by the prefetch budget snapshot.  Keep a decline that the
                # worker has already decided: its completion will drive a
                # fresh, unrestricted current request instead of publishing an
                # error frame for a valid image.
                self.prefetch_budget_bytes = None

    def expand_prefetch_budget(self, byte_budget: int) -> bool:
        """Expand a running background job's snapshot atomically.

        Return ``True`` when the worker had already decided to decline under
        the previous snapshot.  The runtime then retries that key once after
        consuming the empty declined result.
        """

        with self._token_lock:
            already_declined = self.admission_declined
            if self.prefetch_budget_bytes is not None:
                self.prefetch_budget_bytes = max(
                    self.prefetch_budget_bytes,
                    max(0, int(byte_budget)),
                )
            return already_declined

    def resize_prefetch_budget(self, byte_budget: int, hard_limit: int) -> int | None:
        """Apply a smaller live reservation and return confirmed artifact cost."""

        with self._token_lock:
            if self.prefetch_budget_bytes is not None:
                self.prefetch_budget_bytes = max(0, int(byte_budget))
            if self.prefetch_hard_limit_bytes is not None:
                self.prefetch_hard_limit_bytes = max(1, int(hard_limit))
            return self.confirmed_required_bytes

    def cancel(self) -> bool:
        if self.cancelled.is_set():
            return False
        self.cancelled.set()
        cancel_image_request = getattr(self.source, "cancel_image_request", None)
        if callable(cancel_image_request):
            for page in (*self.unit.pages, *self.layout_metadata_pages):
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
        metadata = (
            RasterLayoutMetadata(
                self.key.source_epoch,
                self.key.source_identity,
                (),
            )
            if self.layout_metadata_pages
            else None
        )
        try:
            if not self.cancelled.is_set():
                if self.layout_metadata_pages:
                    metadata = RasterLayoutMetadata(
                        self.key.source_epoch,
                        self.key.source_identity,
                        probe_layout_metadata(
                            self.layout_metadata_pages,
                            self.source.probe_image_size,
                            self.cancelled,
                        ),
                    )
                else:
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
            self.admission_declined,
            self.oversized_prefetch,
            monotonic(),
            layout_metadata=metadata,
        )
        # Worker completion and GUI-result drainage are separate lifetime
        # phases.  Mark native/Pillow/source access finished before queuing the
        # GUI callback; the runtime keeps the job in ``_jobs`` until that
        # callback has consumed the result.
        self.finished.set()
        self.signals.completed.emit(result)

    def _render_unit(self) -> tuple[_RenderedPage, ...]:
        if not self._admit_unknown_prefetch_unit():
            return ()
        decoded_pages: list[_DecodedPage] = []
        for page in self.unit.pages:
            decoded_pages.append(self._decode_page(page))
            if self.cancelled.is_set() or self.admission_declined:
                return ()
        decoded = tuple(decoded_pages)
        if self.cancelled.is_set() or self.admission_declined:
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
                        page.error or tr('画像を表示できません。'),
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
            target_width, target_height = _bounded_physical_render_size(
                self.key.render_spec,
                (target_width, target_height),
            )
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
                downscale_algorithm=(
                    self.key.render_spec.downscale_algorithm
                ),
                upscale_algorithm=self.key.render_spec.upscale_algorithm,
            )
            try:
                display_qimage, _resized = render_qimage(page.qimage, render_key)
                error = None if not display_qimage.isNull() else tr('画像を表示できません。')
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
            decoder_maximum = _decoder_maximum_for_page(spec, self.unit, page)
            suffix = Path(page.image_id).suffix.casefold()
            if self.cancelled.is_set():
                return _DecodedPage(
                    page,
                    None,
                    page.known_size or (360, 520),
                    False,
                )
            if suffix in _JPEG_SUFFIXES:
                # Keep JPEGs on the same bounded/native decoder path even when
                # the caller requests the full source.  Besides avoiding a
                # second payload materialization through Pillow, this lets an
                # archive source reject a malformed declared-JPEG before the
                # generic fallback reopens the same entry.
                self.source_decode_started.set()
                compatible = self.source.open_compatible_jpeg_at_most(
                    page.image_id,
                    decoder_maximum or (None, None),
                )
                decoded = None
                if compatible is not None:
                    qimage = compatible.qimage
                    original_size = compatible.original_size
                    if not _qimage_satisfies_source_requirement(
                        qimage,
                        original_size,
                        decoder_maximum,
                    ):
                        # A decoder backend is allowed to ignore or coarsen a
                        # scaled-size hint.  Never admit that undersized fresh
                        # result: fall through to the full-source path once so
                        # normal display cannot silently upscale a preview.
                        qimage = None
                        original_size = None
                elif decoder_maximum is not None:
                    decoded = self.source.open_qimage_at_most(
                        page.image_id,
                        decoder_maximum,
                    )
                if decoded is not None:
                    qimage, original_size = decoded
                if (
                    decoder_maximum is not None
                    and qimage is not None
                    and original_size is not None
                ):
                    qimage = self._contain_preview_source(
                        qimage,
                        original_size,
                        decoder_maximum,
                    )
            if (
                (qimage is None or qimage.isNull())
                and spec.adjustments == (1.0, 1.0, 1.0)
                and suffix == ".webp"
            ):
                self.source_decode_started.set()
                qimage = self.source.open_qimage(page.image_id)
                if qimage is not None and not qimage.isNull():
                    original_size = (qimage.width(), qimage.height())
            if (
                qimage is not None
                and not qimage.isNull()
                and spec.adjustments != (1.0, 1.0, 1.0)
            ):
                qimage = self._adjust_qimage(qimage, spec.adjustments)
            if qimage is None or qimage.isNull():
                if suffix in _JPEG_SUFFIXES and decoder_maximum is not None:
                    logical_size = original_size or page.known_size
                    if logical_size is None:
                        try:
                            logical_size = self.source.probe_jpeg_size(
                                page.image_id
                            )
                        except Exception:
                            logical_size = None
                    if logical_size is None or len(logical_size) != 2:
                        raise ImageSourceError(
                            tr('JPEGの縮小読み込みに失敗し、安全な元画像サイズを確認できません。'),
                            code="unsafe_scaled_decode_fallback",
                        )
                    logical_size = (
                        max(1, int(logical_size[0])),
                        max(1, int(logical_size[1])),
                    )
                    compatible_size = self.source.estimate_compatible_jpeg_size(
                        logical_size,
                        decoder_maximum,
                    )
                    # A native JPEG reduction tier can be up to twice the
                    # requested edge on a bounded axis. Do not turn a failed
                    # scaled decode into an arbitrarily larger full raster.
                    if any(
                        maximum is not None
                        and logical_size[axis]
                        > max(1, int(compatible_size[axis])) * 2
                        for axis, maximum in enumerate(decoder_maximum)
                    ):
                        raise ImageSourceError(
                            tr('画像が大きすぎるため、安全な縮小読み込みに失敗しました。'),
                            code="unsafe_scaled_decode_fallback",
                        )
                self.source_decode_started.set()
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
                raise ImageSourceError(tr('画像decoderが結果を返しませんでした。'))
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

    def _admit_unknown_prefetch_unit(self) -> bool:
        """Confirm geometry, then budget missing sources and the final frame.

        This retained-pixel estimate does not bound decoder RSS, compressed
        payloads, scratch buffers, or QPixmap allocations. Header reads use the
        existing worker/source APIs; the completed artifact is replanned
        against the live combined cache budget before publication.
        """

        with self._token_lock:
            if self.prefetch_budget_bytes is None:
                return True
        if self.cancelled.is_set():
            return False

        resolved_pages: list[ZipRasterPage] = []
        for page in self.unit.pages:
            if self.cancelled.is_set():
                return False
            cached = self.cached_sources.get(page.image_id)
            logical_size = (
                cached.original_size if cached is not None else page.known_size
            )
            if logical_size is None:
                try:
                    probe = (
                        self.source.probe_jpeg_size
                        if Path(page.image_id).suffix.casefold() in _JPEG_SUFFIXES
                        else self.source.probe_image_size
                    )
                    logical_size = probe(page.image_id)
                except Exception:
                    logical_size = None
            if self.cancelled.is_set():
                return False
            try:
                if logical_size is None or len(logical_size) != 2:
                    return self._decline_prefetch_if_still_bounded()
                logical_size = (int(logical_size[0]), int(logical_size[1]))
                if min(logical_size) <= 0:
                    return self._decline_prefetch_if_still_bounded()
            except (TypeError, ValueError, OverflowError):
                return self._decline_prefetch_if_still_bounded()
            resolved_pages.append(replace(page, known_size=logical_size))

        # Keep identity and slot semantics while giving admission and decode
        # the same resolved dimensions. PageModel remains the layout authority.
        resolved_unit = replace(self.unit, pages=tuple(resolved_pages))
        logical_sizes: list[tuple[int, int]] = []
        source_sizes: list[tuple[int, int]] = []
        missing_source_bytes = 0
        incompatible_cached_ids: list[str] = []
        for page in resolved_unit.pages:
            if self.cancelled.is_set():
                return False
            logical_size = page.known_size
            assert logical_size is not None
            logical_sizes.append(logical_size)
            decoder_maximum = _decoder_maximum_for_page(
                self.key.render_spec,
                resolved_unit,
                page,
            )
            cached = self.cached_sources.get(page.image_id)
            if cached is not None and _qimage_satisfies_source_requirement(
                cached.qimage,
                logical_size,
                decoder_maximum,
            ):
                source_sizes.append(
                    (cached.qimage.width(), cached.qimage.height())
                )
                continue
            if cached is not None:
                # A provisional bound can resolve to a different native JPEG
                # tier. Do not silently reuse the undersized source.
                incompatible_cached_ids.append(page.image_id)
            if (
                Path(page.image_id).suffix.casefold() in _JPEG_SUFFIXES
                and decoder_maximum is not None
            ):
                source_size = self.source.estimate_compatible_jpeg_size(
                    logical_size,
                    decoder_maximum,
                )
            else:
                source_size = logical_size
            source_width, source_height = (
                max(1, int(source_size[0])),
                max(1, int(source_size[1])),
            )
            missing_source_bytes += source_width * source_height * 4
            source_sizes.append((source_width, source_height))

        frame_bytes = _display_frame_bytes_for_sizes(
            resolved_unit,
            self.key.render_spec,
            tuple(logical_sizes),
            source_sizes=tuple(source_sizes),
        )
        required_bytes = missing_source_bytes + frame_bytes
        with self._token_lock:
            if self.cancelled.is_set():
                return False
            self.confirmed_required_bytes = required_bytes
            budget = self.prefetch_budget_bytes
            if budget is not None and required_bytes > budget:
                self.admission_declined = True
                hard_limit = self.prefetch_hard_limit_bytes
                self.oversized_prefetch = bool(
                    hard_limit is not None and required_bytes > hard_limit
                )
                return False
            self.unit = resolved_unit
            for image_id in incompatible_cached_ids:
                self.cached_sources.pop(image_id, None)
        return True

    def _decline_prefetch_if_still_bounded(self) -> bool:
        with self._token_lock:
            if self.prefetch_budget_bytes is None:
                return True
            if not self.cancelled.is_set():
                self.admission_declined = True
        return False

    @staticmethod
    def _contain_preview_source(
        qimage: QImage,
        original_size: tuple[int, int],
        maximum_size: DecoderMaximumSize,
    ) -> QImage:
        """Keep a decoder that ignored scaled output out of the source store.

        Native JPEG tiers may be up to just under twice the requested edge and
        are intentionally retained so the display performs the only exact
        resize.  A full/oversized fallback is reduced immediately on the worker
        before adjustment, rotation or format conversion can multiply it.
        """

        target_width, target_height = _size_within_bounds(
            original_size,
            maximum_size,
        )
        actual = (qimage.width(), qimage.height())
        must_reduce = (
            actual[0] > target_width * 2
            or actual[1] > target_height * 2
        )
        if not must_reduce:
            return qimage
        return qimage.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    @classmethod
    def _adjust_qimage(
        cls,
        qimage: QImage,
        adjustments: tuple[float, float, float],
    ) -> QImage:
        image = qimage_to_pillow(qimage)
        adjusted: Image.Image | None = None
        try:
            adjusted = cls._apply_adjustments(image, adjustments)
            return pil_to_qimage(adjusted)
        finally:
            if adjusted is not None and adjusted is not image:
                adjusted.close()
            image.close()

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

        def replace_adjusted(replacement: Image.Image) -> None:
            nonlocal adjusted
            previous = adjusted
            adjusted = replacement
            if previous is not image:
                previous.close()

        if brightness != 1.0:
            replace_adjusted(
                ImageEnhance.Brightness(adjusted).enhance(brightness)
            )
        if contrast != 1.0:
            replace_adjusted(
                ImageEnhance.Contrast(adjusted).enhance(contrast)
            )
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
                gamma_image = adjusted.point(lut * 3 + list(range(256)))
            elif adjusted.mode == "RGB":
                gamma_image = adjusted.point(lut * 3)
            elif adjusted.mode == "L":
                gamma_image = adjusted.point(lut)
            else:
                converted = adjusted.convert("RGBA")
                try:
                    gamma_image = converted.point(lut * 3 + list(range(256)))
                finally:
                    converted.close()
            replace_adjusted(gamma_image)
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
    layoutMetadataReady = Signal(object)
    idle = Signal(object)

    def __init__(
        self,
        source: ImageSource,
        source_epoch: int,
        parent: QObject | None = None,
        *,
        image_work_coordinator: ImageWorkCoordinator | None = None,
        cache_byte_budget: int = _DEFAULT_CACHE_BYTES,
        cache_soft_target_bytes: int | None = None,
        max_active_jobs: int = 1,
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
        self._max_active_jobs = max(1, min(2, int(max_active_jobs)))
        self._thread_pool.setMaxThreadCount(self._max_active_jobs)
        self._accepting_requests = True
        self._shutdown_complete = False
        self._serial = 0
        self._current_request: ZipRasterRequest | None = None
        self._current_key: _UnitKey | None = None
        self._warmup_planner: RasterWarmupPlanner[
            ZipRasterDisplayUnit,
            tuple[tuple[int, str], ...],
        ] | None = None
        self._planner_render_spec: ZipRasterRenderSpec | None = None
        self._prefetch_admission_stopped_request_id: int | None = None
        self._dispatch_suspended = False
        self._published_request: ZipRasterRequest | None = None
        self._painted_key: _UnitKey | None = None
        self._source_hydration_key: _UnitKey | None = None
        self._source_hydration_frame: _CachedFrame | None = None
        self._active_job: _ZipRasterUnitJob | None = None
        self._secondary_job: _ZipRasterUnitJob | None = None
        self._jobs: set[_ZipRasterUnitJob] = set()
        self._inflight_reservations: dict[_ZipRasterUnitJob, int] = {}
        self._processing_completion = False
        self._pending_completion_keys: set[_UnitKey] = set()
        self._failed_prefetch: set[_UnitKey] = set()
        # Failed header attempts are suppressed per book; successful dimensions
        # live in PageModel and the source metadata cache.
        self._layout_metadata_attempted: set[str] = set()
        # Admission rejection is capacity state, not a broken page.  Keep it
        # separate so a later budget increase can retry the same paint-
        # released work order without also looping terminal/start failures.
        self._admission_declined_prefetch: set[_UnitKey] = set()
        self._capacity_retry_after_completion: set[_UnitKey] = set()
        self._cache_byte_budget = max(1, int(cache_byte_budget))
        self._cache_soft_target_bytes = max(
            1,
            min(
                self._cache_byte_budget,
                int(cache_soft_target_bytes)
                if cache_soft_target_bytes is not None
                else self._cache_byte_budget * 7 // 8,
            ),
        )
        self._admission_policy = RasterAdmissionPolicy(
            hard_limit_bytes=self._cache_byte_budget,
            soft_target_bytes=self._cache_soft_target_bytes,
        )
        self._frame_store = _ZipRasterFrameStore()
        self._source_store = _ZipRasterSourceStore()
        self._metrics = ZipRasterRuntimeMetrics()
        if self._coordinator is not None and self._max_active_jobs > 1:
            self._coordinator.folder_viewer_capacity_available.connect(
                self._drive, Qt.ConnectionType.QueuedConnection
            )

    @property
    def metrics(self) -> ZipRasterRuntimeMetrics:
        return replace(self._metrics)

    @property
    def active_job_count(self) -> int:
        return sum(
            not job.finished.is_set()
            for job in self._active_slots()
        )

    def _active_slots(self) -> tuple[_ZipRasterUnitJob, ...]:
        return tuple(
            job for job in (self._active_job, self._secondary_job)
            if job is not None
        )

    def _free_slot_available(self) -> bool:
        return self._active_job is None or (
            self._max_active_jobs > 1 and self._secondary_job is None
        )

    def _remove_active_slot(self, job: _ZipRasterUnitJob) -> None:
        if self._active_job is job:
            self._active_job = None
        if self._secondary_job is job:
            self._secondary_job = None

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

    @property
    def cache_byte_budget(self) -> int:
        """Resolved combined source/frame budget for diagnostics/benchmarks."""

        return self._cache_byte_budget

    @property
    def cache_soft_target_bytes(self) -> int:
        """Background population target inside the hard byte limit."""

        return self._cache_soft_target_bytes

    @property
    def warmup_stop_reason(self) -> str:
        planner = self._warmup_planner
        return (
            planner.stop_reason.value
            if planner is not None
            else WarmupStopReason.SUSPENDED.value
        )

    @property
    def unprocessed_unit_count(self) -> int:
        planner = self._warmup_planner
        return planner.unprocessed_hint if planner is not None else 0

    def cache_debug_values(self) -> dict[str, int | str | bool]:
        """Expose population state to offscreen benchmarks without logging."""

        ready_pages = set(self._frame_store.page_indexes)
        source_pages = set(self._source_store.page_indexes)
        planner = self._warmup_planner
        request = self._current_request
        priority_identities = (
            request.warmup_plan.priority_band_identities(
                preferred_units=4,
                opposite_units=1,
            )
            if request is not None
            else ()
        )
        ready_priority_units = 0
        if request is not None:
            for identity in priority_identities:
                unit = request.warmup_plan.unit_for_identity(identity)
                if unit is not None and (
                    self._key_for(unit, request.render_spec)
                    in self._frame_store
                ):
                    ready_priority_units += 1
        return {
            "hard_limit_bytes": self._cache_byte_budget,
            "soft_target_bytes": self._cache_soft_target_bytes,
            "cache_used_bytes": self.cache_bytes,
            "inflight_reservation_bytes": sum(
                self._inflight_reservations.values()
            ),
            "active_job_count": self.active_job_count,
            "cache_over_hard_bytes": max(
                0,
                self.cache_bytes - self._cache_byte_budget,
            ),
            "cache_over_soft_bytes": max(
                0,
                self.cache_bytes - self._cache_soft_target_bytes,
            ),
            "ready_page_count": len(ready_pages),
            "ready_unit_count": self.cached_unit_count,
            "ready_ahead_unit_count": ready_priority_units,
            "source_page_count": len(source_pages),
            "source_only_page_count": len(source_pages - ready_pages),
            "unprocessed_unit_count": self.unprocessed_unit_count,
            "worker_running": bool(self.active_job_count),
            "warmup_stop_reason": self.warmup_stop_reason,
            "book_complete": bool(planner is not None and planner.book_complete),
            "capacity_skip_count": (
                len(planner.capacity_skips) if planner is not None else 0
            ),
            "startup_runway_target_units": (
                planner.startup_target_count if planner is not None else 0
            ),
            "priority_band_target_units": (
                planner.startup_target_count if planner is not None else 0
            ),
            "cache_hits": self._metrics.cache_hits,
            "cache_misses": self._metrics.cache_misses,
            "jobs_submitted": self._metrics.jobs_submitted,
            "startup_runway_releases": (
                self._metrics.startup_runway_releases
            ),
            "continuous_warmup_releases": (
                self._metrics.continuous_warmup_releases
            ),
            "warmup_planner_creations": (
                self._metrics.warmup_planner_creations
            ),
            "warmup_planner_recenters": (
                self._metrics.warmup_planner_recenters
            ),
            "work_order_changes": self._metrics.work_order_changes,
            "running_job_adoptions": (
                self._metrics.running_job_adoptions
            ),
            "queued_job_replacements": (
                self._metrics.queued_job_replacements
            ),
            "finished_job_slot_releases": (
                self._metrics.finished_job_slot_releases
            ),
            "compatible_old_results": (
                self._metrics.compatible_old_results
            ),
            "cancel_requests": self._metrics.cancel_requests,
            "stale_results": self._metrics.stale_results,
            "cache_evictions": self._metrics.cache_evictions,
            "oversized_prefetch_skips": (
                self._metrics.oversized_prefetch_skips
            ),
        }

    def set_cache_limits(
        self,
        *,
        byte_budget: int | None = None,
    ) -> None:
        if byte_budget is None:
            return
        # Compatibility API for old tests/adapters: one limit means its
        # background target is the same hard boundary. Production uses the
        # explicit soft/hard method below.
        self.set_memory_limits(
            hard_limit_bytes=byte_budget,
            soft_target_bytes=byte_budget,
        )

    def set_memory_limits(
        self,
        *,
        hard_limit_bytes: int,
        soft_target_bytes: int,
    ) -> None:
        previous_byte_budget = self._cache_byte_budget
        previous_soft_target = self._cache_soft_target_bytes
        self._cache_byte_budget = max(1, int(hard_limit_bytes))
        self._cache_soft_target_bytes = max(
            1,
            min(self._cache_byte_budget, int(soft_target_bytes)),
        )
        self._admission_policy.set_limits(
            hard_limit_bytes=self._cache_byte_budget,
            soft_target_bytes=self._cache_soft_target_bytes,
        )
        limits_shrunk = (
            self._cache_byte_budget < previous_byte_budget
            or self._cache_soft_target_bytes < previous_soft_target
        )
        if limits_shrunk:
            for active in self._active_slots():
                if active.key == self._current_key:
                    continue
                # A background worker carries a snapshot of its admission
                # budget.  Invalidate that snapshot immediately; otherwise a
                # live 32-GiB -> minimal change could still publish work that
                # the newly authoritative budget would never start.
                new_budget = self._prefetch_worker_budget(
                    active.key,
                    excluding_job=active,
                )
                confirmed_cost = active.resize_prefetch_budget(
                    new_budget,
                    self._cache_byte_budget,
                )
                if (
                    confirmed_cost is not None
                    and confirmed_cost <= new_budget
                ):
                    # A header-confirmed unit still fits the live budget,
                    # including lower-rank artifacts it may replace. Keep its
                    # started decode instead of throwing away useful pixels.
                    self._inflight_reservations[active] = max(1, new_budget)
                    continue
                if (
                    active.source_decode_started.is_set()
                    or confirmed_cost is not None
                ):
                    # A started decode that cannot fit the new reservation is
                    # suppressed until capacity expands or it becomes current;
                    # otherwise cancellation could make us decode it twice.
                    self._failed_prefetch.add(active.key)
                    self._admission_declined_prefetch.add(active.key)
                    planner = self._warmup_planner
                    if planner is not None:
                        planner.mark_capacity_skip(active.key.unit_identity)
                else:
                    # Header-only probes are cheap to repeat. If the shrink
                    # interrupts one before pixel decode, retry under the new
                    # reservation after the probe unwinds.
                    self._capacity_retry_after_completion.add(active.key)
                self._cancel_job(active)
        self._enforce_combined_budget(
            limit_bytes=(
                self._cache_soft_target_bytes
                if self._cache_soft_target_bytes < previous_soft_target
                else self._cache_byte_budget
            )
        )
        limits_expanded = (
            self._cache_byte_budget > previous_byte_budget
            or self._cache_soft_target_bytes > previous_soft_target
        )
        if not limits_expanded:
            if limits_shrunk:
                planner = self._warmup_planner
                if planner is not None:
                    planner.reset_capacity()
                self._prefetch_admission_stopped_request_id = None
                self._drive()
            return

        retry_keys: tuple[_UnitKey, ...] = ()
        if limits_expanded:
            # Only exact worker-side capacity declines are retriable here.
            # Broken images and worker-start failures stay suppressed for the
            # current work order, avoiding a background retry loop.
            retry_keys = tuple(self._admission_declined_prefetch)
            self._failed_prefetch.difference_update(
                self._admission_declined_prefetch
            )
            self._admission_declined_prefetch.clear()
        planner = self._warmup_planner
        if planner is not None:
            planner.reset_capacity()
        self._prefetch_admission_stopped_request_id = None

        # A newly affordable *earlier* unit must regain its work-order
        # priority.  Merely raising the pressure target must not throw away an
        # unrelated far decode already in flight: that converts gradual Auto
        # recovery into duplicate work every five-second pressure sample.
        retry_ranks = tuple(
            rank
            for key in retry_keys
            if (rank := self._background_rank(key)) is not None
        )
        earliest_retry_rank = min(retry_ranks, default=None)
        expandable_jobs = tuple(
            active for active in self._active_slots()
            if active.key != self._current_key
        )
        for index, active in enumerate(expandable_jobs):
            active_rank = self._background_rank(active.key)
            should_reprioritize = active_rank is None or (
                earliest_retry_rank is not None
                and earliest_retry_rank < active_rank
            )
            replaced_queued = bool(
                should_reprioritize and self._take_unstarted_job(active)
            )
            if replaced_queued:
                self._bump("queued_job_replacements")
                continue
            new_budget = self._prefetch_worker_budget(
                active.key, excluding_job=active
            )
            if len(expandable_jobs) > 1:
                rank = self._admission_rank(active.key)
                if rank is not None:
                    target = self._admission_policy.target_for_rank(rank)
                    unreserved = max(
                        0,
                        target - self.cache_bytes
                        - sum(self._inflight_reservations.values()),
                    )
                    remaining = len(expandable_jobs) - index
                    new_budget = min(
                        new_budget,
                        self._inflight_reservations.get(active, 0)
                        + unreserved // remaining,
                    )
            if active.expand_prefetch_budget(new_budget):
                self._capacity_retry_after_completion.add(active.key)
            if active.prefetch_budget_bytes is not None:
                self._inflight_reservations[active] = max(
                    self._inflight_reservations.get(active, 0),
                    active.prefetch_budget_bytes,
                )
        self._drive()

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

    def stage(
        self,
        request: ZipRasterRequest,
        *,
        publish_cached: bool = False,
    ) -> bool:
        """Adopt a navigation intent without starting its cold current job.

        The Viewer uses this before its replaceable cold-demand timer.  Work
        order, retention and cancellation therefore follow every input
        immediately, while a paced wheel/key burst can still replace the
        eventual decode target.  By default a completed staged artifact is
        retained but not published. ``publish_cached`` lets a ready transit
        frame publish without opening the decode/warmup dispatch gate.
        """

        if (
            not self._accepting_requests
            or request.source_epoch != self.source_epoch
        ):
            return False
        self._adopt_request(request, suspend_dispatch=True)
        if publish_cached:
            current_key = self._current_key
            frame = (
                self._frame_store.get(current_key, touch=True)
                if current_key is not None
                else None
            )
            if frame is None:
                return False
            self._bump("cache_hits")
            self._publish_frame(request, frame, True)
        return True

    def refresh_work_order(self, request: ZipRasterRequest) -> bool:
        """Refresh same-unit metadata without opening a staged input gate."""

        previous = self._current_request
        if (
            not self._accepting_requests
            or previous is None
            or request.source_epoch != self.source_epoch
            or request.request_id != previous.request_id
            or self._key_for(request.current, request.render_spec) != self._current_key
        ):
            return False
        was_published = self._published_request is previous
        self._adopt_request(
            request,
            suspend_dispatch=self._dispatch_suspended,
        )
        if was_published:
            self._published_request = request
        self._drive()
        return True

    def release_staged(self, request: ZipRasterRequest) -> bool:
        """Open the worker gate for the exact latest staged navigation.

        An adopted job can finish while this gate is closed: publish its ready
        final frame on release. A transit frame already published during stage
        has the same cache presence but must not publish a second time.
        """

        if (
            not self._accepting_requests
            or request.source_epoch != self.source_epoch
            or self._current_request is not request
            or not self._dispatch_suspended
        ):
            return False
        self._dispatch_suspended = False
        current_key = self._current_key
        frame = (
            self._frame_store.get(current_key, touch=True)
            if current_key is not None else None
        )
        if frame is not None and self._published_request is not request:
            self._bump("cache_hits")
            self._publish_frame(request, frame, True)
        self._drive()
        return True

    def request(
        self,
        request: ZipRasterRequest,
        *,
        preserve_started_compatible: bool = False,
    ) -> bool:
        if (
            not self._accepting_requests
            or request.source_epoch != self.source_epoch
        ):
            return False
        self._release_finished_active_slot()
        if self._dispatch_suspended and self._current_request is request:
            # Window dispatches the exact object it staged.  Retention,
            # cancellation and work-order replacement already happened at
            # input time, so admission only opens the current-job gate.
            self._dispatch_suspended = False
        else:
            self._adopt_request(
                request,
                suspend_dispatch=False,
                preserve_started_compatible=preserve_started_compatible,
            )
        current_key = self._current_key
        if current_key is None:
            return False
        frame = self._frame_store.get(current_key, touch=True)
        if frame is not None:
            self._bump("cache_hits")
            self._publish_frame(request, frame, True)
        else:
            self._bump("cache_misses")
        self._drive()
        return True

    def _adopt_request(
        self,
        request: ZipRasterRequest,
        *,
        suspend_dispatch: bool,
        preserve_started_compatible: bool = False,
    ) -> None:
        current_key = self._key_for(request.current, request.render_spec)
        if (
            self._source_hydration_key is not None
            and self._source_hydration_key != current_key
        ):
            self._restore_source_hydration_frame()
        self._current_request = request
        self._current_key = current_key
        planner = self._warmup_planner
        if planner is None:
            planner = RasterWarmupPlanner(request.warmup_plan)
            self._warmup_planner = planner
            self._bump("warmup_planner_creations")
        else:
            planner.recenter(request.warmup_plan)
            self._bump("warmup_planner_recenters")
            self._bump("work_order_changes")
            if self._planner_render_spec != request.render_spec:
                # Capacity observations belong to a physical render
                # signature.  Layout/DPR changes keep the book-scoped owner,
                # but reconsider every unit under the new byte cost.
                planner.reset_capacity()
        self._planner_render_spec = request.render_spec
        self._prefetch_admission_stopped_request_id = None
        self._dispatch_suspended = bool(suspend_dispatch)
        evicted = self._frame_store.set_retention_plan(
            request.warmup_plan,
            current_key,
            request.navigation_direction,
        )
        if evicted:
            self._bump("cache_evictions", evicted)
        source_evicted = self._source_store.set_retention_plan(
            request.warmup_plan,
            request.current,
            request.render_spec,
            request.navigation_direction,
        )
        self._record_source_evictions(source_evicted)
        self._enforce_combined_budget()
        # Broken/capacity state is book work state, not navigation state.  Only
        # the newly interactive current gets a targeted retry; other scanned
        # units retain their suppression across page turns.
        self._failed_prefetch.discard(current_key)
        self._admission_declined_prefetch.discard(current_key)
        self._capacity_retry_after_completion.discard(current_key)
        planner.discard_capacity_skip(request.current.identity)
        urgent_identities = set(
            request.warmup_plan.priority_band_identities(
                preferred_units=4,
                opposite_units=1,
            )
        )
        for declined_key in tuple(self._admission_declined_prefetch):
            if (
                declined_key.render_spec == request.render_spec
                and declined_key.unit_identity in urgent_identities
            ):
                # A prior soft-target/far decline is not terminal. Promotion
                # into the new current's ready-ahead band gives it hard-budget
                # urgency without resetting unrelated book scan state.
                self._admission_declined_prefetch.discard(declined_key)
                self._failed_prefetch.discard(declined_key)
                self._capacity_retry_after_completion.discard(declined_key)
                planner.discard_capacity_skip(declined_key.unit_identity)

        self._release_finished_active_slot()
        for active in self._active_slots():
            active_cancelled = active.cancelled.is_set()
            current_is_ready_or_pending = (
                current_key in self._frame_store
                or self._has_pending_completion(current_key)
            )
            if active.key == current_key and not active_cancelled:
                active.adopt_request(request.request_id, as_current=True)
            elif (
                not active_cancelled
                and self._active_job_is_artifact_compatible(active, request)
                and (current_is_ready_or_pending or preserve_started_compatible)
            ):
                # Ready navigation can keep compatible book work. Wheel
                # navigation also lets an already-started compatible decode
                # finish as a cache artifact: dropping that work on every
                # cold tick makes moderate scrolling appear to skip pages.
                # Unstarted work is still replaced by the newest current.
                if self._take_unstarted_job(active):
                    self._bump("queued_job_replacements")
                else:
                    # tryTake-before-cancel closes the dequeue race: if Qt has
                    # just started the compatible job, preserve/adopt it
                    # instead of setting a cancellation flag that would throw
                    # away the same work after it consumes the worker slot.
                    active.adopt_request(request.request_id)
                    self._bump("running_job_adoptions")
            else:
                self._cancel_job(active)

    def _active_job_is_artifact_compatible(
        self,
        active: _ZipRasterUnitJob,
        request: ZipRasterRequest,
    ) -> bool:
        if (
            active.key.source_epoch != self.source_epoch
            or active.key.source_identity != self._source_identity
            or active.key.render_spec != request.render_spec
        ):
            return False
        return request.warmup_plan.contains_identity(
            active.key.unit_identity
        )

    def _release_finished_active_slot(self) -> bool:
        released = False
        for active in self._active_slots():
            if not active.finished.is_set():
                continue
            # The worker finished before its queued GUI result was drained.
            # Free the execution slot while the key ledger prevents duplicates.
            self._remove_active_slot(active)
            self._pending_completion_keys.add(active.key)
            self._bump("finished_job_slot_releases")
            released = True
        return released

    def _take_unstarted_job(self, job: _ZipRasterUnitJob) -> bool:
        """Replace queued work without first poisoning a possible runner."""

        if job.started.is_set() or job.finished.is_set():
            return False
        if not self._try_take(job):
            return False
        job.mark_removed_before_start()
        self._jobs.discard(job)
        self._inflight_reservations.pop(job, None)
        self._remove_active_slot(job)
        self._emit_idle_if_needed()
        return True

    def release_continuous_warmup(self, *, request_id: int) -> bool:
        """Activate book-scoped population after the first complete commit.

        Four reading-direction units plus one reverse unit form only the
        urgent prefix of one continuous all-book order. Navigation recenters
        the same owner; physical paint never re-releases scheduling.
        """

        request = self._current_request
        if (
            request is None
            or self._dispatch_suspended
            or int(request_id) != request.request_id
            or self._current_key not in self._frame_store
        ):
            return False
        planner = self._warmup_planner
        if planner is not None:
            released = planner.release_after_first_commit(
                preferred_units=4,
                opposite_units=1,
            )
            if released:
                # Keep the legacy metric name for existing diagnostics while
                # making the new book-scoped meaning explicit.
                self._bump("startup_runway_releases")
                self._bump("continuous_warmup_releases")
        self._drive()
        return True

    def release_startup_runway(self, *, request_id: int) -> bool:
        """Compatibility adapter for the former request-scoped phase."""

        return self.release_continuous_warmup(request_id=request_id)

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
        self._source_store.set_displayed_unit(
            request.current,
            request.render_spec,
        )
        # A cold atomic commit temporarily protects both the last painted
        # frame and its replacement.  Once the replacement has painted, the
        # old frame is removable; enforce the combined source+frame budget at
        # that exact ownership boundary rather than waiting for another
        # successful prefetch (which may never exist at an end page or with
        # prefetch disabled).
        self._enforce_combined_budget()
        # Paint transfers displayed ownership and may free the preceding
        # frame.  It is deliberately not a raster scheduling release; the
        # continuous bounded planner was activated by the first accepted
        # complete commit.
        planner = self._warmup_planner
        if (
            planner is not None
            and planner.background_released
            and planner.stop_reason
            in {WarmupStopReason.SOFT_TARGET, WarmupStopReason.HARD_LIMIT}
            and self.cache_bytes < self._cache_soft_target_bytes
        ):
            # Ownership transfer can make bytes available again. Reconsider
            # the existing order without changing its released/book lifetime.
            planner.recenter(planner.plan)
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
        self._warmup_planner = None
        self._planner_render_spec = None
        self._prefetch_admission_stopped_request_id = None
        self._dispatch_suspended = False
        self._painted_key = None
        self._failed_prefetch.clear()
        self._admission_declined_prefetch.clear()
        self._capacity_retry_after_completion.clear()
        self._cancel_active_job()
        if clear_artifacts:
            self._frame_store.clear()
            self._source_store.clear()
        self._frame_store.set_retention_plan(None, None, 0)
        self._frame_store.set_displayed_key(None)
        self._source_store.set_retention_plan(None, None, None, 0)
        self._source_store.set_displayed_unit(None, None)

    def suspend(self) -> None:
        """Pause a provisional replacement without ending this book owner.

        A failed replacement resumes the same runtime.  Preserve its planner,
        completed artifacts, scan/admission ledger and displayed retention;
        only stop publication and active workers.  A successful
        replacement subsequently retires this runtime through the normal
        book-lifetime shutdown boundary.
        """

        if not self._accepting_requests:
            return
        self._restore_source_hydration_frame()
        self._dispatch_suspended = True
        planner = self._warmup_planner
        if planner is not None:
            planner.suspend()
        self._cancel_active_job()

    def invalidate_layout(self) -> None:
        # Layout-dependent QPixmaps are invalid, but decoded archive content
        # remains valid.  Keeping this boundary is what prevents resize, DPI,
        # rotation, and magnifier transitions from reopening the same entry.
        self._restore_source_hydration_frame()
        self._current_request = None
        self._current_key = None
        self._planner_render_spec = None
        self._prefetch_admission_stopped_request_id = None
        self._dispatch_suspended = False
        self._painted_key = None
        self._failed_prefetch.clear()
        self._admission_declined_prefetch.clear()
        self._capacity_retry_after_completion.clear()
        planner = self._warmup_planner
        if planner is not None:
            planner.suspend()
        self._cancel_active_job()
        self._frame_store.set_retention_plan(None, None, 0)
        self._frame_store.set_displayed_key(None)
        self._source_store.set_retention_plan(None, None, None, 0)
        self._source_store.set_displayed_unit(None, None)
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

    def retire(self) -> bool:
        """Stop work without synchronously destroying a large ready cache.

        A replacement book can submit its current page first; the owner calls
        :meth:`shutdown` after that frame paints to reclaim retained QImages
        and QPixmaps outside the new book's open critical path.
        """

        self._accepting_requests = False
        self.cancel(clear_artifacts=False)
        if self._coordinator is None:
            self._thread_pool.clear()
        return not self._jobs

    def _drive(self) -> None:
        # Result handling may publish, evict and update the planner. Dispatch
        # after its reservation transfers into the cache, not midway through.
        if self._processing_completion:
            return
        request = self._current_request
        planner = self._warmup_planner
        if (
            not self._accepting_requests
            or request is None
            or self._current_key is None
            or planner is None
            or self._dispatch_suspended
        ):
            return
        self._release_finished_active_slot()
        if self._current_key not in self._frame_store:
            if (
                self._has_pending_completion(self._current_key)
                or any(job.key == self._current_key for job in self._active_slots())
            ):
                return
            if self._free_slot_available():
                self._submit(self._current_key, ImageWorkPriority.VIEWER_CURRENT)
            return
        while self._free_slot_available():
            unit = planner.next_candidate(
                identity_of=lambda candidate: candidate.identity,
                is_ready=lambda candidate: (
                    (
                        key := self._key_for(candidate, request.render_spec)
                    )
                    in self._frame_store
                    or self._has_pending_completion(key)
                    or any(job.key == key for job in self._active_slots())
                ),
                is_terminal_failure=lambda candidate: (
                    self._key_for(candidate, request.render_spec)
                    in self._failed_prefetch
                ),
            )
            if unit is None:
                return
            key = self._key_for(unit, request.render_spec)
            metadata_pages = self._layout_metadata_pages_for(key)
            decision = (
                None
                if metadata_pages
                else self._prefetch_admission_decision(key)
            )
            if decision is not None and not decision.admitted:
                if self._inflight_reservations:
                    planner.recenter(request.warmup_plan)
                    return
                if (
                    decision.action is RasterAdmissionAction.SOFT_TARGET_REACHED
                    and self.cache_bytes + sum(self._inflight_reservations.values())
                    >= decision.target_bytes
                ):
                    planner.stop_for_soft_target()
                    return
                # Capacity rejection belongs to this replaceable work order,
                # not to the whole warm-up frontier.  A single exceptionally
                # large page must not prevent later small pages from using the
                # remaining byte budget.  A new request or larger limit clears
                # only these capacity entries and makes them eligible again.
                self._failed_prefetch.add(key)
                self._admission_declined_prefetch.add(key)
                planner.mark_capacity_skip(unit.identity)
                if decision.action is RasterAdmissionAction.SKIP_OVERSIZED:
                    self._bump("oversized_prefetch_skips")
                if (
                    self._prefetch_admission_stopped_request_id
                    != request.request_id
                ):
                    self._prefetch_admission_stopped_request_id = request.request_id
                    self._bump("prefetch_admission_stops")
                continue
            priority = (
                ImageWorkPriority.VIEWER_NEXT
                if unit.identity
                in request.warmup_plan.priority_band_identities(
                    preferred_units=4,
                    opposite_units=1,
                )
                else ImageWorkPriority.VIEWER_PREVIOUS
            )
            if not self._submit(
                key,
                priority,
                layout_metadata_pages=metadata_pages,
            ):
                planner.recenter(request.warmup_plan)
                return

    def _has_pending_completion(self, key: _UnitKey) -> bool:
        if key in self._pending_completion_keys:
            return True
        # A worker can set ``finished`` between GUI-side observations. Scanning
        # the small lifetime set keeps duplicate prevention correct even before
        # navigation has explicitly detached that finished active slot.
        return any(
            job.key == key and job.finished.is_set()
            for job in self._jobs
        )

    def _background_rank(self, key: _UnitKey) -> int | None:
        request = self._current_request
        if request is None or key.render_spec != request.render_spec:
            return None
        return request.warmup_plan.rank_for_identity(key.unit_identity)

    def _admission_rank(self, key: _UnitKey) -> int | None:
        """Map the continuously maintained ready-ahead band to hard budget.

        This is scheduling urgency only.  Retention and eviction continue to
        use the full distance/direction rank and the combined byte budget.
        """

        rank = self._background_rank(key)
        request = self._current_request
        if rank is None or request is None:
            return rank
        if key.unit_identity in request.warmup_plan.priority_band_identities(
            preferred_units=4,
            opposite_units=1,
        ):
            return min(rank, self._admission_policy.minimum_protected_rank)
        return rank

    def _unit_for_key(self, key: _UnitKey) -> ZipRasterDisplayUnit | None:
        request = self._current_request
        if (
            request is None
            or key.source_epoch != self.source_epoch
            or key.source_identity != self._source_identity
            or key.render_spec != request.render_spec
        ):
            return None
        return request.warmup_plan.unit_for_identity(key.unit_identity)

    def _layout_metadata_pages_for(
        self,
        key: _UnitKey,
    ) -> tuple[ZipRasterPage, ...]:
        request = self._current_request
        unit = self._unit_for_key(key)
        if request is None or unit is None or not request.resolve_layout_metadata:
            return ()
        return select_layout_metadata_pages(
            unit,
            request.warmup_plan,
            self._layout_metadata_attempted,
            maximum_pages=(
                len(unit.pages)
                if key == self._current_key
                else LAYOUT_METADATA_BATCH_PAGES
            ),
            # Resolve just the visible spread before painting. Neighbor
            # headers are useful for admission, but must not delay the first
            # current-unit decode on a cold book.
            include_nearby=key != self._current_key,
        )

    def _prefetch_admission_decision(
        self,
        key: _UnitKey,
    ) -> RasterAdmissionDecision:
        """Reject work that cannot coexist with the protected current unit.

        The frame store alone cannot see the usually larger decoded source
        allocation.  Admission therefore uses the combined book budget and a
        conservative estimate of both missing sources and the display frame. A
        full store may reserve only whole artifacts that rank below this
        candidate.  This phase is deliberately non-mutating: cancellation or
        stale completion must leave every prior ready artifact intact.  Actual
        bytes are replanned and reclaimed only for a relevant successful GUI
        result immediately before it enters the stores.
        """
        rank = self._background_rank(key)
        admission_rank = self._admission_rank(key)
        reserved_bytes = sum(self._inflight_reservations.values())
        if (
            not self._frame_store.can_admit_prefetch(key)
            or rank is None
            or admission_rank is None
        ):
            return self._admission_policy.decide_background(
                retained_bytes=self.cache_bytes + reserved_bytes,
                estimated_bytes=self._cache_byte_budget + 1,
                reclaimable_lower_rank_bytes=0,
                rank=self._admission_policy.minimum_protected_rank + 1,
            )
        unit = self._unit_for_key(key)
        if unit is None:
            return self._admission_policy.decide_background(
                retained_bytes=self.cache_bytes + reserved_bytes,
                estimated_bytes=self._cache_byte_budget + 1,
                reclaimable_lower_rank_bytes=0,
                rank=admission_rank,
            )
        source_candidates = self._source_store.lower_rank_reclaim_candidates(
            unit,
            key.render_spec,
            retention_boundary=self._frame_store.retention_rank(key),
            include_navigation_sources=True,
        )
        frame_candidates = self._frame_store.lower_rank_reclaim_candidates(key)
        reclaimable_bytes = sum(
            size for _candidate, size, _retention in source_candidates
        ) + sum(
            size for _candidate, size, _retention in frame_candidates
        )
        if reserved_bytes:
            # Concurrent jobs cannot both reserve the same reclaim candidate.
            reclaimable_bytes = 0
        requires_exact_worker_admission = any(
            _requires_exact_prefetch_admission(
                page,
                _decoder_maximum_for_page(key.render_spec, unit, page),
                has_cached_source=self._source_store.find(
                    page,
                    key.render_spec,
                    unit=unit,
                    touch=False,
                )
                is not None,
            )
            for page in unit.pages
        )
        if requires_exact_worker_admission:
            # A prior giant lazy PNG must not make every later unknown page
            # inherit its observed provisional cost and skip the header probe.
            # Prove only rank/unit replaceability here; the worker compares its
            # exact header cost against free + strictly lower-rank allowance.
            return self._admission_policy.decide_background(
                retained_bytes=self.cache_bytes + reserved_bytes,
                estimated_bytes=None,
                reclaimable_lower_rank_bytes=reclaimable_bytes,
                rank=admission_rank,
            )
        source_bytes = self._estimated_missing_source_bytes(
            unit,
            key.render_spec,
        )
        frame_bytes = self._estimated_frame_bytes(
            unit,
            key.render_spec,
            source_bytes=source_bytes,
        )
        required_bytes = source_bytes + frame_bytes
        return self._admission_policy.decide_background(
            retained_bytes=self.cache_bytes + reserved_bytes,
            estimated_bytes=required_bytes,
            reclaimable_lower_rank_bytes=reclaimable_bytes,
            rank=admission_rank,
        )

    def _lower_rank_reclaim_plan(
        self,
        key: _UnitKey,
        unit: ZipRasterDisplayUnit,
        *,
        bytes_needed: int,
        excluded_source_keys: frozenset[_SourceKey] = frozenset(),
    ) -> tuple[tuple[_SourceKey, ...], tuple[_UnitKey, ...]] | None:
        frame_candidates = self._frame_store.lower_rank_reclaim_candidates(key)
        source_candidates = self._source_store.lower_rank_reclaim_candidates(
            unit,
            key.render_spec,
            excluded_keys=excluded_source_keys,
            retention_boundary=self._frame_store.retention_rank(key),
            include_navigation_sources=True,
        )
        # Source and frame residency share one byte authority.  Pick the
        # globally lowest-value artifacts regardless of which store owns them
        # instead of exhausting decoded sources before considering frames.
        combined: list[
            tuple[
                _ArtifactRetentionRank,
                str,
                _SourceKey | _UnitKey,
                int,
            ]
        ] = [
            (rank, "source", candidate, size)
            for candidate, size, rank in source_candidates
        ]
        combined.extend(
            (rank, "frame", candidate, size)
            for candidate, size, rank in frame_candidates
        )
        combined.sort(key=lambda candidate: candidate[0], reverse=True)

        selected_sources: list[_SourceKey] = []
        selected_frames: list[_UnitKey] = []
        reclaimed_bytes = 0
        for _rank, kind, candidate, size in combined:
            if reclaimed_bytes >= bytes_needed:
                break
            if kind == "source":
                selected_sources.append(candidate)  # type: ignore[arg-type]
            else:
                selected_frames.append(candidate)  # type: ignore[arg-type]
            reclaimed_bytes += size
        if reclaimed_bytes < bytes_needed:
            return None
        return tuple(selected_sources), tuple(selected_frames)

    def _prefetch_worker_budget(
        self,
        key: _UnitKey,
        *,
        excluding_job: _ZipRasterUnitJob | None = None,
    ) -> int:
        """Return free bytes plus every strictly lower-rank reservation."""

        unit = self._unit_for_key(key)
        rank = self._admission_rank(key)
        if unit is None or rank is None:
            return 0
        target = self._admission_policy.target_for_rank(rank)
        reserved_bytes = sum(
            size for job, size in self._inflight_reservations.items()
            if job is not excluding_job
        )
        free = max(0, target - self.cache_bytes - reserved_bytes)
        if reserved_bytes:
            return free
        source_allowance = sum(
            size
            for _candidate, size, _rank in (
                self._source_store.lower_rank_reclaim_candidates(
                    unit,
                    key.render_spec,
                    retention_boundary=self._frame_store.retention_rank(key),
                    include_navigation_sources=True,
                )
            )
        )
        frame_allowance = sum(
            size
            for _candidate, size, _rank in (
                self._frame_store.lower_rank_reclaim_candidates(key)
            )
        )
        return free + source_allowance + frame_allowance

    def _apply_reclaim_plan(
        self,
        plan: tuple[tuple[_SourceKey, ...], tuple[_UnitKey, ...]],
    ) -> None:
        source_keys, frame_keys = plan
        source_evicted, _source_freed = (
            self._source_store.remove_reclaim_candidates(source_keys)
        )
        self._record_source_evictions(source_evicted)
        frame_evicted, _frame_freed = (
            self._frame_store.remove_reclaim_candidates(frame_keys)
        )
        if frame_evicted:
            self._bump("cache_evictions", frame_evicted)

    def _decline_completed_prefetch(self, key: _UnitKey) -> None:
        self._failed_prefetch.add(key)
        self._admission_declined_prefetch.add(key)
        planner = self._warmup_planner
        if planner is not None:
            planner.mark_capacity_skip(key.unit_identity)
        request = self._current_request
        if (
            request is not None
            and self._prefetch_admission_stopped_request_id
            != request.request_id
        ):
            self._prefetch_admission_stopped_request_id = request.request_id
            self._bump("prefetch_admission_stops")
        self._drive()
        self._emit_idle_if_needed()

    def _estimated_missing_source_bytes(
        self,
        unit: ZipRasterDisplayUnit,
        render_spec: ZipRasterRenderSpec,
    ) -> int:
        observed = self._source_store.largest_source_bytes
        estimate = 0
        for page in unit.pages:
            if self._source_store.find(page, render_spec, unit=unit) is not None:
                continue
            known_size = page.known_size
            suffix = Path(page.image_id).suffix.casefold()
            decoder_size = _decoder_maximum_for_page(
                render_spec,
                unit,
                page,
            )
            if known_size is not None:
                width, height = known_size
                if (
                    suffix in _JPEG_SUFFIXES
                    and decoder_size is not None
                ):
                    width, height = self.source.estimate_compatible_jpeg_size(
                        (width, height),
                        decoder_size,
                    )
                estimate += width * height * 4
                continue
            if (
                suffix in _JPEG_SUFFIXES
                and decoder_size is not None
            ):
                maximum_width, maximum_height = decoder_size
                if maximum_width is None and maximum_height is None:
                    unknown = self._cache_byte_budget + 1
                else:
                    # Lazy ZIP/folder indexes do not read every JPEG header on
                    # the GUI thread.  For a one-axis fit, admit a bounded
                    # provisional page rather than disabling all neighbor
                    # prefetch.  A 4:1 decoded aspect cap covers ordinary
                    # comic pages; more extreme sources remain safe because
                    # the completed artifact is charged at its actual bytes
                    # and immediately passes through combined-budget eviction.
                    if maximum_width is None:
                        maximum_height = max(1, int(maximum_height))
                        maximum_width = (
                            maximum_height * _UNKNOWN_JPEG_ASPECT_LIMIT
                        )
                    elif maximum_height is None:
                        maximum_width = max(1, int(maximum_width))
                        maximum_height = (
                            maximum_width * _UNKNOWN_JPEG_ASPECT_LIMIT
                        )
                    unknown = (
                        max(1, int(maximum_width))
                        * max(1, int(maximum_height))
                        * 4
                        * max(
                            1,
                            int(
                                self.source.compatible_jpeg_unknown_area_multiplier
                            ),
                        )
                    )
            elif suffix not in _JPEG_SUFFIXES:
                # Unknown PNG/WebP/etc. sources are admitted provisionally on
                # the GUI thread, then header-probed and charged exactly by the
                # worker before any pixel raster is allocated.  A viewport-
                # sized floor lets the first such neighbor reach that safety
                # boundary without using compressed entry bytes as a decode
                # estimate; a prior retained source remains the better local
                # estimate when available.
                viewport_width, viewport_height = render_spec.viewport_size
                dpr = render_spec.device_pixel_ratio
                viewport_source_bytes = round(
                    viewport_width * viewport_height * dpr * dpr * 4
                )
                unknown = max(1, observed, viewport_source_bytes)
            else:
                # Unknown full decodes have no trustworthy upper bound.  Do
                # not let a compressed-byte or previous-page guess admit a
                # potentially 100+ MiB neighbor before it is current.
                unknown = self._cache_byte_budget + 1
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
            logical_sizes = tuple(
                size for size in known_sizes if size is not None
            )
            source_sizes: list[tuple[int, int]] = []
            for page, logical_size in zip(unit.pages, logical_sizes):
                cached = self._source_store.find(
                    page,
                    render_spec,
                    unit=unit,
                    touch=False,
                )
                if cached is not None:
                    source_sizes.append(
                        (cached.qimage.width(), cached.qimage.height())
                    )
                    continue
                decoder_size = _decoder_maximum_for_page(
                    render_spec,
                    unit,
                    page,
                )
                if (
                    Path(page.image_id).suffix.casefold() in _JPEG_SUFFIXES
                    and decoder_size is not None
                ):
                    source_sizes.append(
                        self.source.estimate_compatible_jpeg_size(
                            logical_size,
                            decoder_size,
                        )
                    )
                else:
                    # Missing non-JPEG sources decode to their logical raster;
                    # standard resampling then clamps the display artifact to
                    # that source instead of charging a fictitious viewport
                    # upscale that QPixmap will never receive.
                    source_sizes.append(logical_size)
            return _display_frame_bytes_for_sizes(
                unit,
                render_spec,
                logical_sizes,
                source_sizes=tuple(source_sizes),
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

    def _submit(
        self,
        key: _UnitKey,
        priority: ImageWorkPriority,
        *,
        layout_metadata_pages: tuple[ZipRasterPage, ...] | None = None,
    ) -> bool:
        request = self._current_request
        unit = self._unit_for_key(key)
        if request is None or unit is None or not self._free_slot_available():
            return False
        metadata_pages = (
            self._layout_metadata_pages_for(key)
            if layout_metadata_pages is None
            else tuple(layout_metadata_pages)
        )
        cached_sources: dict[str, _CachedSource] = {}
        for page in (() if metadata_pages else unit.pages):
            cached = self._source_store.find(
                page,
                key.render_spec,
                unit=unit,
                touch=True,
            )
            if cached is None:
                self._bump("source_cache_misses")
                continue
            cached_sources[page.image_id] = cached
            self._bump("source_cache_hits")
        source_estimate = (
            0
            if metadata_pages
            else self._estimated_missing_source_bytes(unit, key.render_spec)
        )
        reservation = (
            0
            if metadata_pages
            else source_estimate + self._estimated_frame_bytes(
                unit, key.render_spec, source_bytes=source_estimate
            )
        )
        unknown_cost = not metadata_pages and any(
            page.known_size is None for page in unit.pages
        )
        if unknown_cost and self._max_active_jobs > 1:
            available = max(
                1,
                self._cache_soft_target_bytes - self.cache_bytes
                - sum(self._inflight_reservations.values()),
            )
            # Allow ordinary lazy Folder pages to run concurrently without
            # granting either unknown page the other's whole free budget.
            reservation = max(reservation, available // 3)
        worker_budget = (
            None if key == self._current_key or metadata_pages
            else self._prefetch_worker_budget(key)
        )
        if unknown_cost and self._max_active_jobs > 1 and worker_budget is not None:
            worker_budget = min(worker_budget, reservation)
        self._serial += 1
        job = _ZipRasterUnitJob(
            serial=self._serial,
            key=key,
            request_id=request.request_id,
            source=self.source,
            unit=unit,
            cached_sources=cached_sources,
            layout_metadata_pages=metadata_pages,
            prefetch_budget_bytes=worker_budget,
            prefetch_hard_limit_bytes=(
                None
                if key == self._current_key or metadata_pages
                else self._cache_byte_budget
            ),
        )
        job.signals.completed.connect(
            self._on_job_completed,
            Qt.ConnectionType.QueuedConnection,
        )
        if self._active_job is None:
            self._active_job = job
        else:
            self._secondary_job = job
        self._jobs.add(job)
        self._inflight_reservations[job] = max(1, reservation)
        if self._coordinator is not None:
            started = (
                self._coordinator.start_folder_viewer(job, int(priority))
                if self._max_active_jobs > 1
                else self._coordinator.start_viewer(job, int(priority))
            )
        else:
            self._thread_pool.start(job, int(priority))
            started = True
        if started:
            self._bump("jobs_submitted")
            if metadata_pages:
                self._bump("layout_metadata_jobs")
            return True
        job.mark_removed_before_start()
        self._jobs.discard(job)
        self._inflight_reservations.pop(job, None)
        self._remove_active_slot(job)
        if started is None:
            return False
        if key == self._current_key:
            self._publish_start_failure(
                request,
                key,
                unit,
                tr('{p0} Viewer workerを開始できませんでした。', p0=self._runtime_display_name),
            )
        else:
            self._failed_prefetch.add(key)
        self._emit_idle_if_needed()
        return False

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
                self._publish_frame(request, hydration_frame, True)
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
            self._publish_frame(request, cached, False)

    def _cancel_active_job(self) -> None:
        for job in self._active_slots():
            self._cancel_job(job)

    def _cancel_job(self, job: _ZipRasterUnitJob) -> None:
        if job.cancel():
            self._bump("cancel_requests")
        if not self._try_take(job):
            return
        job.mark_removed_before_start()
        self._jobs.discard(job)
        self._inflight_reservations.pop(job, None)
        self._remove_active_slot(job)
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
        owned = next(
            (job for job in self._jobs if job.serial == result.serial), None
        )
        try:
            self._processing_completion = True
            self._process_job_completed(result)
        finally:
            if owned is not None:
                self._inflight_reservations.pop(owned, None)
            self._processing_completion = False
            self._drive()

    def _process_job_completed(self, result: _JobResult) -> None:
        self._bump("queued_callbacks")
        self._pending_completion_keys.discard(result.key)
        for active in self._active_slots():
            if active.serial == result.serial:
                self._remove_active_slot(active)
                break
        for job in tuple(self._jobs):
            if job.serial == result.serial:
                self._jobs.discard(job)
                break
        metadata = result.layout_metadata
        if metadata is not None:
            self._capacity_retry_after_completion.discard(result.key)
            if (
                self._accepting_requests
                and self._current_request is not None
                and metadata.source_epoch == self.source_epoch
                and metadata.source_identity == self._source_identity
            ):
                self._layout_metadata_attempted.update(
                    image_id
                    for _index, image_id, _size in metadata.observations
                )
                self._bump(
                    "layout_metadata_pages",
                    len(metadata.observations),
                )
                planner = self._warmup_planner
                if planner is not None:
                    planner.recenter(planner.plan)
                self.layoutMetadataReady.emit(metadata)
            self._emit_idle_if_needed()
            return
        if not self._result_is_artifact_compatible(result):
            self._capacity_retry_after_completion.discard(result.key)
            self._bump("stale_results")
            self._drive()
            self._emit_idle_if_needed()
            return
        if result.admission_declined:
            request = self._current_request
            if result.oversized_prefetch:
                self._bump("oversized_prefetch_skips")
            retry_after_capacity_change = (
                result.key in self._capacity_retry_after_completion
            )
            self._capacity_retry_after_completion.discard(result.key)
            if (
                result.key != self._current_key
                and not retry_after_capacity_change
            ):
                self._failed_prefetch.add(result.key)
                self._admission_declined_prefetch.add(result.key)
                planner = self._warmup_planner
                if planner is not None:
                    planner.mark_capacity_skip(result.key.unit_identity)
                if (
                    request is not None
                    and self._prefetch_admission_stopped_request_id
                    != request.request_id
                ):
                    self._prefetch_admission_stopped_request_id = request.request_id
                    self._bump("prefetch_admission_stops")
            self._drive()
            self._emit_idle_if_needed()
            return
        if result.cancelled:
            self._capacity_retry_after_completion.discard(result.key)
            self._drive()
            self._emit_idle_if_needed()
            return

        request_at_completion = self._current_request
        if (
            request_at_completion is not None
            and result.request_id != request_at_completion.request_id
        ):
            # The immutable unit/render key is still useful book cache data.
            # Only frame publication is tied to the latest request serial.
            self._bump("compatible_old_results")

        self._capacity_retry_after_completion.discard(result.key)
        existing_before_result = self._frame_store.get(
            result.key,
            touch=False,
        )
        existing_before_result_is_ready = bool(
            existing_before_result is not None
            and all(page.error is None for page in existing_before_result.pages)
        )

        stored_sources: set[_SourceKey] = set()
        pending_sources: list[_CachedSource] = []
        for rendered in result.pages:
            if (
                rendered.source_key is None
                or rendered.source_qimage is None
                or rendered.source_key in stored_sources
            ):
                continue
            stored_sources.add(rendered.source_key)
            pending_sources.append(
                _CachedSource(
                    rendered.source_key,
                    rendered.page_index,
                    QImage(rendered.source_qimage),
                    rendered.source_original_size,
                    rendered.source_is_preview,
                )
            )

        result_succeeded = bool(result.pages) and all(
            rendered.error is None
            and rendered.display_qimage is not None
            and not rendered.display_qimage.isNull()
            for rendered in result.pages
        )
        reclaim_plan: (
            tuple[tuple[_SourceKey, ...], tuple[_UnitKey, ...]] | None
        ) = None
        if result.key != self._current_key and result_succeeded:
            source_delta, superseded_source_keys = (
                self._source_store.projected_put_delta(tuple(pending_sources))
            )
            display_bytes = sum(
                rendered.display_qimage.width()
                * rendered.display_qimage.height()
                * 4
                for rendered in result.pages
                if rendered.display_qimage is not None
            )
            existing_frame_bytes = (
                self._frame_store._frame_bytes(existing_before_result)
                if existing_before_result is not None
                else 0
            )
            projected_bytes = (
                self.cache_bytes
                + source_delta
                + display_bytes
                - existing_frame_bytes
            )
            result_rank = self._admission_rank(result.key)
            result_target = self._admission_policy.target_for_rank(
                result_rank
                if result_rank is not None
                else self._admission_policy.minimum_protected_rank + 1
            )
            bytes_needed = max(
                0,
                projected_bytes - result_target,
            )
            reclaim_plan = self._lower_rank_reclaim_plan(
                result.key,
                result.unit,
                bytes_needed=bytes_needed,
                excluded_source_keys=superseded_source_keys,
            )
            if reclaim_plan is None:
                self._decline_completed_prefetch(result.key)
                return

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
                    error = tr('QPixmapを作成できませんでした。')
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
            if result.key != self._current_key:
                # A failed background result never displaces a ready frame.
                # The next work order may retry it as current without carrying
                # a cached error artifact through the navigation window.
                self._failed_prefetch.add(result.key)
                self._drive()
                self._emit_idle_if_needed()
                return
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
                    self._publish_frame(request, hydration_frame, True)
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
        if reclaim_plan is not None:
            # Commit reclaim only after every QPixmap upload succeeded.  A
            # GUI-backend failure must not destroy previously ready artifacts.
            self._apply_reclaim_plan(reclaim_plan)
        for source in pending_sources:
            source_evicted = self._source_store.put(source)
            self._record_source_evictions(source_evicted)
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
            # under the byte budget.  Do not immediately decode the same
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
            self._publish_frame(request, cached, False)
        self._drive()
        self._emit_idle_if_needed()

    def _publish_frame(
        self, request: ZipRasterRequest, cached: _CachedFrame, cache_hit: bool,
    ) -> None:
        # Record before emitting: GUI slots can synchronously navigate/release.
        # Cache ownership alone does not imply this request was presented.
        self._published_request = request
        self.frameReady.emit(self._public_frame(request, cached, cache_hit))

    def _result_is_artifact_compatible(self, result: _JobResult) -> bool:
        request = self._current_request
        return bool(
            self._accepting_requests
            and request is not None
            and result.key.source_epoch == self.source_epoch
            and result.key.source_identity == self._source_identity
            and result.key.render_spec == request.render_spec
            and request.warmup_plan.contains_identity(
                result.key.unit_identity
            )
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
            unit=cached.unit,
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

    def _enforce_combined_budget(self, *, limit_bytes: int | None = None) -> None:
        limit = (
            self._cache_byte_budget
            if limit_bytes is None
            else max(1, min(self._cache_byte_budget, int(limit_bytes)))
        )
        while self.cache_bytes > limit:
            source_candidate = self._source_store.worst_reclaim_candidate()
            frame_candidate = self._frame_store.worst_reclaim_candidate()
            if source_candidate is None and frame_candidate is None:
                # Current, displayed and rank-1/rank-2 neighborhood artifacts
                # are the minimum navigation working set.  Like ZipPlaFork's
                # minimum count, that set may exceed a very small byte target.
                break
            if frame_candidate is None or (
                source_candidate is not None
                and source_candidate[2] >= frame_candidate[2]
            ):
                source_evicted, _freed = (
                    self._source_store.remove_reclaim_candidates(
                        (source_candidate[0],)
                    )
                )
                if not source_evicted:
                    break
                self._record_source_evictions(source_evicted)
                continue
            frame_evicted, _freed = (
                self._frame_store.remove_reclaim_candidates(
                    (frame_candidate[0],)
                )
            )
            if not frame_evicted:
                break
            self._bump("cache_evictions", frame_evicted)

    def _emit_idle_if_needed(self) -> None:
        if not self._active_slots() and not self.has_unfinished_tasks():
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
