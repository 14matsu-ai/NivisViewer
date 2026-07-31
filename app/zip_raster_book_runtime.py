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
from .image_source import ImageSourceError, ZipImageSource
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
    prefetch_admission_stops: int = 0


@dataclass(frozen=True)
class _UnitKey:
    source_epoch: int
    source_identity: int
    unit_identity: tuple[tuple[int, str], ...]
    is_single: bool
    render_spec: ZipRasterRenderSpec


@dataclass(frozen=True)
class _DecodedPage:
    page: ZipRasterPage
    qimage: QImage | None
    original_size: tuple[int, int]
    source_is_preview: bool
    error: str | None = None


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
    worker_completed_at: float
    gui_ready_at: float


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

    def get(self, key: _UnitKey, *, touch: bool = False) -> _CachedFrame | None:
        frame = self._frames.get(key)
        if frame is not None and touch:
            self._frames.move_to_end(key)
        return frame

    def values(self) -> tuple[_CachedFrame, ...]:
        return tuple(self._frames.values())

    def clear(self) -> None:
        self._frames.clear()

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
            key for key in self._frames if key != self._current_key
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
        pixmap_bytes = sum(
            page.pixmap.width() * page.pixmap.height() * 4
            for page in frame.pages
            if page.pixmap is not None
        )
        qimages: dict[int, QImage] = {}
        for page in frame.pages:
            if page.source_qimage is not None:
                qimages[int(page.source_qimage.cacheKey())] = page.source_qimage
        source_bytes = sum(image.sizeInBytes() for image in qimages.values())
        return pixmap_bytes + source_bytes


class _JobSignals(QObject):
    completed = Signal(object)


class _ZipRasterUnitJob(QRunnable):
    def __init__(
        self,
        *,
        serial: int,
        key: _UnitKey,
        request_id: int,
        source: ZipImageSource,
        unit: ZipRasterDisplayUnit,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.serial = int(serial)
        self.key = key
        self.source = source
        self.unit = unit
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
        for page in self.unit.pages:
            try:
                self.source.cancel_image_request(page.image_id)
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
                )
            )
        return tuple(rendered)

    def _decode_page(self, page: ZipRasterPage) -> _DecodedPage:
        if self.cancelled.is_set():
            return _DecodedPage(page, None, page.known_size or (360, 520), False)
        spec = self.key.render_spec
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
            return _DecodedPage(
                page,
                qimage,
                logical,
                (qimage.width(), qimage.height()) != logical,
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


class ZipRasterBookRuntime(QObject):
    """Own the complete ZIP raster Viewer execution path for one book."""

    frameReady = Signal(object)
    artifactReady = Signal(object)
    idle = Signal(object)

    def __init__(
        self,
        source: ZipImageSource,
        source_epoch: int,
        parent: QObject | None = None,
        *,
        image_work_coordinator: ImageWorkCoordinator | None = None,
        cache_unit_limit: int = 3,
        cache_byte_budget: int = _DEFAULT_CACHE_BYTES,
    ) -> None:
        if not isinstance(source, ZipImageSource):
            raise TypeError("ZipRasterBookRuntime requires ZipImageSource")
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
        self._active_job: _ZipRasterUnitJob | None = None
        self._jobs: set[_ZipRasterUnitJob] = set()
        self._failed_prefetch: set[_UnitKey] = set()
        self._frame_store = _ZipRasterFrameStore(
            unit_limit=cache_unit_limit,
            byte_budget=cache_byte_budget,
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
        return self._frame_store.page_indexes

    @property
    def cached_unit_count(self) -> int:
        return self._frame_store.unit_count

    @property
    def cache_bytes(self) -> int:
        return self._frame_store.byte_size

    def set_cache_limits(
        self,
        *,
        unit_limit: int | None = None,
        byte_budget: int | None = None,
    ) -> None:
        evicted = self._frame_store.set_limits(
            unit_limit=unit_limit,
            byte_budget=byte_budget,
        )
        if evicted:
            self._bump("cache_evictions", evicted)

    def has_cached_current(self, request: ZipRasterRequest) -> bool:
        return (
            self._key_for(request.current, request.render_spec)
            in self._frame_store
        )

    def request(self, request: ZipRasterRequest) -> bool:
        if (
            not self._accepting_requests
            or request.source_epoch != self.source_epoch
        ):
            return False
        current_key = self._key_for(request.current, request.render_spec)
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
        evicted = self._frame_store.set_retention_order(
            work_keys,
            current_key,
            request.navigation_direction,
        )
        if evicted:
            self._bump("cache_evictions", evicted)
        # Admission/failure suppression belongs to one replaceable work order.
        # A later navigation may make a previously rejected neighbor current,
        # and must get a fresh attempt.
        self._failed_prefetch.clear()

        active = self._active_job
        if active is not None:
            if active.key == current_key:
                active.adopt_request(request.request_id)
            elif current_key in self._frame_store and active.key in work_keys:
                # A same-current refresh or a compatible work-order change can
                # adopt an already useful neighbor instead of restarting it.
                active.adopt_request(request.request_id)
            else:
                self._cancel_active_job()

        frame = self._frame_store.get(current_key, touch=True)
        if frame is not None:
            self._bump("cache_hits")
            self.frameReady.emit(self._public_frame(request, frame, True))
        else:
            self._bump("cache_misses")
        self._drive()
        return True

    def release_prefetch(self, *, request_id: int) -> bool:
        request = self._current_request
        if (
            request is None
            or int(request_id) != request.request_id
            or self._current_key not in self._frame_store
        ):
            return False
        self._prefetch_released_request_id = request.request_id
        self._drive()
        return True

    def cancel(self, *, clear_artifacts: bool = False) -> None:
        self._current_request = None
        self._current_key = None
        self._work_keys = ()
        self._unit_by_key.clear()
        self._prefetch_released_request_id = None
        self._prefetch_admission_stopped_request_id = None
        self._failed_prefetch.clear()
        self._cancel_active_job()
        if clear_artifacts:
            self._frame_store.clear()
        self._frame_store.set_retention_order((), None, 0)

    def invalidate_layout(self) -> None:
        self.cancel(clear_artifacts=True)

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
            if not self._frame_store.can_admit_prefetch(key):
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

    def _submit(self, key: _UnitKey, priority: ImageWorkPriority) -> None:
        request = self._current_request
        unit = self._unit_by_key.get(key)
        if request is None or unit is None:
            return
        self._serial += 1
        job = _ZipRasterUnitJob(
            serial=self._serial,
            key=key,
            request_id=request.request_id,
            source=self.source,
            unit=unit,
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
                "ZIP Viewer workerを開始できませんでした。",
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
        cached = _CachedFrame(key, unit, pages, ready_at, ready_at)
        _retained, evicted = self._frame_store.put(cached)
        if evicted:
            self._bump("cache_evictions", evicted)
        self._bump("terminal_errors", len(pages))
        if (
            self._accepting_requests
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

        frame_pages: list[ZipRasterFramePage] = []
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
                    (
                        QImage(rendered.source_qimage)
                        if rendered.source_qimage is not None
                        else None
                    ),
                    error,
                    rendered.split_range,
                    rendered.source_is_preview,
                )
            )
        if not frame_pages:
            self._drive()
            self._emit_idle_if_needed()
            return
        if terminal_errors:
            self._bump("terminal_errors", terminal_errors)
        gui_ready = monotonic()
        cached = _CachedFrame(
            result.key,
            result.unit,
            tuple(frame_pages),
            result.completed_at,
            gui_ready,
        )
        retained, evicted = self._frame_store.put(cached)
        if evicted:
            self._bump("cache_evictions", evicted)
        if result.key != self._current_key and not retained:
            # The artifact could not coexist with the protected current frame
            # under the byte/unit budget.  Do not immediately decode the same
            # prefetch forever; a new request/work order may retry it.
            self._failed_prefetch.add(result.key)
        self.artifactReady.emit(cached)
        request = self._current_request
        if request is not None and result.key == self._current_key:
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
        return ZipRasterFrame(
            self.source_epoch,
            self._source_identity,
            request.request_id,
            cached.unit,
            cached.pages,
            bool(cache_hit),
            cached.worker_completed_at,
            cached.gui_ready_at,
        )

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
