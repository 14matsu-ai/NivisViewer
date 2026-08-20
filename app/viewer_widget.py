from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
import math
from time import monotonic

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QContextMenuEvent,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from .image_work_coordinator import ImageWorkCoordinator, ImageWorkPriority
from .mouse_gesture import MouseGestureRecognizer
from .page_model import DisplaySpread
from .viewer_canvas_pointer import (
    ViewerCanvasPointerController,
    ViewerCanvasPointerState,
)
from .viewer_render import (
    ViewerRenderKey,
    ViewerRenderResult,
    ViewerRenderTask,
    normalize_resampling_mode,
)


@dataclass
class ViewerImage:
    page_index: int
    image_id: str
    pixmap: QPixmap | None
    original_size: tuple[int, int] | None
    error: str | None = None
    loading: bool = False
    rendered_size: tuple[int, int] | None = None
    pre_rotated: bool = False
    qimage: QImage | None = None
    source_generation: int = 0
    source_identity: str = ""
    split_range: tuple[int, int, int, int] | None = None
    display_prepared: bool = False
    source_is_preview: bool = False


@dataclass(frozen=True)
class ViewerFrameCommit:
    """A complete Widget frame swap and its originating request token.

    The token is deliberately opaque to ``ViewerWidget``.  Presentation
    semantics belong to ``ViewerPresentationState``; the Widget only reports
    that every logical slot was replaced in one canvas transaction.
    """

    frame_token: object | None
    widget_frame_serial: int
    spread_identity: tuple[tuple[int, str], ...]
    page_indexes: tuple[int, ...]
    image_ids: tuple[str, ...]
    failed_page_indexes: tuple[int, ...]


@dataclass(frozen=True)
class SpreadLayout:
    scale: float
    scales: tuple[float, ...]
    content_size: QSize
    rects: tuple[QRect, ...]
    effective_gap: int


@dataclass
class _PendingDisplay:
    spread: DisplaySpread
    images: tuple[ViewerImage, ...]
    keys: tuple[ViewerRenderKey | None, ...]
    generation: int
    request_generation: int
    failed_keys: dict[ViewerRenderKey, str]
    frame_token: object | None = None


@dataclass(frozen=True)
class PreparedDisplayUnitKey:
    spread_identity: tuple[tuple[int, str], ...]
    image_ids: tuple[str, ...]
    render_keys: tuple[ViewerRenderKey | None, ...]
    layout_generation: int


@dataclass(frozen=True)
class _PreparedDisplayUnit:
    spread: DisplaySpread
    images: tuple[ViewerImage, ...]
    render_keys: tuple[ViewerRenderKey | None, ...]


@dataclass
class _PreparedUnitRequest:
    key: PreparedDisplayUnitKey
    spread: DisplaySpread
    images: tuple[ViewerImage, ...]
    sources: tuple[QImage | None, ...]
    priority: int
    protected: bool
    failed_keys: set[ViewerRenderKey]


def calculate_spread_layout(
    image_sizes: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    viewport_size: tuple[int, int],
    *,
    fit_mode: str = "fit_window",
    manual_zoom: float = 1.0,
    gap: int = 0,
    join_spread_pages: bool = False,
    spread_is_single: bool = False,
    horizontal_alignment: str = "center",
    pan: tuple[int, int] = (0, 0),
) -> SpreadLayout:
    """Calculate spread geometry without requiring a QWidget."""
    if not image_sizes:
        return SpreadLayout(1.0, (), QSize(0, 0), (), 0)

    widths = [max(1, int(width)) for width, _height in image_sizes]
    heights = [max(1, int(height)) for _width, height in image_sizes]
    viewport_width = max(1, int(viewport_size[0]))
    viewport_height = max(1, int(viewport_size[1]))
    effective_gap = (
        0
        if join_spread_pages and len(image_sizes) == 2 and not spread_is_single
        else max(0, int(gap)) if len(image_sizes) > 1 else 0
    )
    width_for_images = max(1, viewport_width - effective_gap * (len(widths) - 1))
    total_width = sum(widths)
    max_height = max(heights)
    fit_width_scale = max(0.01, width_for_images / total_width)
    fit_height_scale = max(0.01, viewport_height / max_height)

    independent_fit = len(image_sizes) == 2 and fit_mode in {
        "fit_window",
        "fit_no_upscale",
        "fit_width",
        "fit_height",
    }
    if independent_fit:
        left_slot_width = width_for_images // 2
        slot_widths = (
            max(1, left_slot_width),
            max(1, width_for_images - left_slot_width),
        )
        scales_list: list[float] = []
        for width, height, slot_width in zip(widths, heights, slot_widths):
            fit_slot_width = max(0.01, slot_width / width)
            fit_slot_height = max(0.01, viewport_height / height)
            page_scale = min(fit_slot_width, fit_slot_height)
            if fit_mode == "fit_no_upscale":
                page_scale = min(1.0, page_scale)
            scales_list.append(page_scale)
        scales = tuple(scales_list)
        target_widths = [
            max(1, round(width * page_scale))
            for width, page_scale in zip(widths, scales)
        ]
        target_heights = [
            max(1, round(height * page_scale))
            for height, page_scale in zip(heights, scales)
        ]
        pan_x, pan_y = int(pan[0]), int(pan[1])
        rects: list[QRect] = []
        slot_x = pan_x
        for index, (target_width, target_height, slot_width) in enumerate(zip(
            target_widths,
            target_heights,
            slot_widths,
        )):
            x = (
                slot_x + slot_width - target_width
                if index == 0
                else slot_x
            )
            y = (viewport_height - target_height) // 2 + pan_y
            rects.append(QRect(x, y, target_width, target_height))
            slot_x += slot_width + effective_gap
        return SpreadLayout(
            scale=scales[0],
            scales=scales,
            content_size=QSize(viewport_width, max(target_heights)),
            rects=tuple(rects),
            effective_gap=effective_gap,
        )

    if fit_mode == "actual_size":
        scale = 1.0
    elif fit_mode == "manual_zoom":
        scale = min(8.0, max(0.05, float(manual_zoom)))
    elif fit_mode == "fit_no_upscale":
        scale = min(1.0, fit_width_scale, fit_height_scale)
    elif fit_mode == "fit_width":
        scale = fit_width_scale
    elif fit_mode == "fit_height":
        scale = fit_height_scale
    else:
        scale = min(fit_width_scale, fit_height_scale)

    target_widths = [max(1, round(width * scale)) for width in widths]
    target_heights = [max(1, round(height * scale)) for height in heights]
    content_width = sum(target_widths) + effective_gap * (len(target_widths) - 1)
    content_height = max(target_heights)
    pan_x, pan_y = int(pan[0]), int(pan[1])
    if horizontal_alignment == "left":
        origin_x = pan_x
    elif horizontal_alignment == "right":
        origin_x = viewport_width - content_width + pan_x
    else:
        origin_x = (viewport_width - content_width) // 2 + pan_x
    origin_y = (viewport_height - content_height) // 2 + pan_y

    rects: list[QRect] = []
    x = origin_x
    for width, height in zip(target_widths, target_heights):
        y = origin_y + (content_height - height) // 2
        rects.append(QRect(x, y, width, height))
        x += width + effective_gap
    return SpreadLayout(
        scale=scale,
        scales=tuple(scale for _size in image_sizes),
        content_size=QSize(content_width, content_height),
        rects=tuple(rects),
        effective_gap=effective_gap,
    )


class ViewerWidget(QWidget):
    nextRequested = Signal()
    previousRequested = Signal()
    zoomChanged = Signal(float)
    fullscreenToggleRequested = Signal()
    leftSideClicked = Signal()
    rightSideClicked = Signal()
    imageLeftClicked = Signal()
    contextMenuRequested = Signal(QPoint)
    gestureRecognized = Signal(str)
    extraMouseButtonPressed = Signal(str)
    viewportChanged = Signal()
    contentPainted = Signal(object)
    magnifierPdfResolutionRequested = Signal(int, QSize)
    magnifierSourceResolutionRequested = Signal(int, QSize)
    magnifierCancelled = Signal()
    displayCommitted = Signal(object)
    frameCommitted = Signal(object)
    renderCacheChanged = Signal()
    renderWorkFinished = Signal(object, bool)
    framePainted = Signal(int, object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        image_work_coordinator: ImageWorkCoordinator | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self.background_color = QColor("#000000")
        self.fit_mode = "fit_window"
        self.manual_zoom = 1.0
        self.gap = 24
        self.join_spread_pages = False
        self.rotation_angle = 0
        self.smooth_scaling = True
        self.horizontal_alignment = "center"
        self.magnifier_zoom = 2.0
        self.magnifier_size = 220
        self.resampling_mode = "standard"
        self.magnifier_resampling_mode = "high_quality"

        self._spread = DisplaySpread(0, tuple(), True)
        self._images: list[ViewerImage] = []
        self._pan = QPoint(0, 0)
        self._drag_start: QPoint | None = None
        self._press_position: QPoint | None = None
        self._canvas_press_side: str | None = None
        self._pending_canvas_click_side: str | None = None
        self._pending_canvas_click_global = QPoint()
        self._canvas_side_click_enabled = True
        self._drag_origin = QPoint(0, 0)
        self._mouse_pos: QPoint | None = None
        self._last_draw_layout: list[tuple[QRect, QPixmap]] = []
        self._last_image_layout: list[tuple[QRect, ViewerImage, QPixmap]] = []
        self._render_generation = 0
        self._render_pool = QThreadPool(self)
        self._render_pool.setMaxThreadCount(1)
        self._render_coordinator = image_work_coordinator
        self._render_pending: dict[ViewerRenderKey, int] = {}
        self._render_tasks: set[ViewerRenderTask] = set()
        self._local_render_tasks: set[ViewerRenderTask] = set()
        self._render_task_by_key: dict[ViewerRenderKey, ViewerRenderTask] = {}
        self._render_priorities: dict[ViewerRenderKey, int] = {}
        self._render_cache: OrderedDict[ViewerRenderKey, QPixmap] = OrderedDict()
        self._last_rendered_by_image: dict[str, QPixmap] = {}
        self._render_cache_limit = 12
        self._render_cache_byte_limit = 256 * 1024 * 1024
        self._pending_display: _PendingDisplay | None = None
        self._display_request_generation = 0
        self._presentation_frame_serial = 0
        self._direct_display_mode = False
        self._direct_frame_serial = 0
        self._direct_current_frame_serial = 0
        self._direct_current_frame_token: object | None = None
        self._prepared_units: OrderedDict[
            PreparedDisplayUnitKey,
            _PreparedDisplayUnit,
        ] = OrderedDict()
        self._prepared_requests: dict[
            PreparedDisplayUnitKey,
            _PreparedUnitRequest,
        ] = {}
        self._prepared_unit_priorities: dict[PreparedDisplayUnitKey, int] = {}
        self._protected_prepared_units: set[PreparedDisplayUnitKey] = set()
        self._resize_render_timer = QTimer(self)
        self._resize_render_timer.setSingleShot(True)
        self._resize_render_timer.setInterval(120)
        self._resize_render_timer.timeout.connect(self._refresh_current_render)
        self._deferred_render_target: (
            tuple[
                DisplaySpread,
                tuple[ViewerImage, ...],
                object | None,
            ]
            | tuple[DisplaySpread, tuple[ViewerImage, ...]]
            | None
        ) = None
        self.magnifier_selecting = False
        self.magnifier_active = False
        self.magnifier_source_page: int | None = None
        self.magnifier_source_rect: QRectF | None = None
        self.magnifier_previous_view_state: dict[str, object] | None = None
        self.magnifier_request_generation = 0
        self._magnifier_source_image_id: str | None = None
        self._magnifier_source_normalized: QRectF | None = None
        self._magnifier_selection_rect: QRect | None = None
        self._magnifier_pixmap: QPixmap | None = None
        self._magnifier_key: ViewerRenderKey | None = None
        self._magnifier_waiting_for_pdf = False
        self._magnifier_pdf_source_key = 0
        self.mouse_gestures_enabled = True
        self.mouse_gesture_show_trail = True
        self.mouse_gesture_min_distance = 36
        self._gesture_recognizer = MouseGestureRecognizer(
            self.mouse_gesture_min_distance
        )
        self._gesture_trail: list[QPoint] = []
        self._right_button_down = False
        self._suppress_context_until_release = False
        self._canvas_context_provider = lambda: None
        self._canvas_click_allowed = lambda _position: True
        self._canvas_press_flags = lambda _position: {}
        self.canvas_pointer = ViewerCanvasPointerController(
            context_provider=lambda: self._canvas_context_provider(),
            click_allowed=lambda position: self._canvas_click_allowed(position),
            parent=self,
        )
        self.canvas_pointer.singleClickConfirmed.connect(
            self._confirm_canvas_single_click
        )
        self.canvas_pointer.doubleClickConfirmed.connect(
            self.fullscreenToggleRequested
        )

    def set_canvas_input_context(
        self,
        *,
        context_provider: Callable[[], object],
        click_allowed: Callable[[QPoint], bool],
        press_flags: Callable[[QPoint], dict[str, bool]] | None = None,
    ) -> None:
        self._canvas_context_provider = context_provider
        self._canvas_click_allowed = click_allowed
        self._canvas_press_flags = press_flags or (lambda _position: {})

    def cancel_pending_canvas_click(self) -> None:
        self.canvas_pointer.cancel()
        self._press_position = None
        self._drag_start = None
        self._canvas_press_side = None
        self._pending_canvas_click_side = None
        self.unsetCursor()

    def set_canvas_side_click_enabled(self, enabled: bool) -> None:
        self._canvas_side_click_enabled = bool(enabled)
        self.cancel_pending_canvas_click()

    def canvas_click_side_at(self, local_position: QPoint) -> str | None:
        if not self.rect().contains(local_position):
            return None
        x = local_position.x()
        center_x = self.rect().center().x()
        if x == center_x:
            return None

        layout = self._layout_for_current_images()
        if len(layout.rects) == 2:
            left_rect, right_rect = sorted(layout.rects, key=lambda rect: rect.x())
            gap_start = left_rect.right() + 1
            gap_end = right_rect.left() - 1
            if gap_start <= gap_end and gap_start <= x <= gap_end:
                return None
        return "left" if x < center_x else "right"

    def _confirm_canvas_single_click(self) -> None:
        side = self.canvas_click_side_at(
            self.mapFromGlobal(self._pending_canvas_click_global)
        )
        if side is None or side != self._pending_canvas_click_side:
            self._pending_canvas_click_side = None
            return
        self._pending_canvas_click_side = None
        self._emit_canvas_side_click(side)

    def _emit_canvas_side_click(self, side: str) -> None:
        if side == "left":
            self.leftSideClicked.emit()
        else:
            self.rightSideClicked.emit()
        self.imageLeftClicked.emit()

    def set_background_color(self, color: str) -> None:
        self.background_color = QColor(color)
        self.update()

    def set_gap(self, gap: int) -> None:
        normalized = max(0, int(gap))
        if normalized != self.gap:
            self.cancel_magnifier()
        self.gap = normalized
        self._refresh_current_render()

    def set_join_spread_pages(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if normalized != self.join_spread_pages:
            self.cancel_magnifier()
        self.join_spread_pages = normalized
        self._refresh_current_render()

    def set_smooth_scaling(self, enabled: bool) -> None:
        normalized = bool(enabled)
        changed = normalized != self.smooth_scaling
        self.smooth_scaling = normalized
        if changed and self.resampling_mode == "standard":
            self._refresh_current_render()
        else:
            self.update()

    def set_render_cache_byte_limit_mib(self, memory_mib: int) -> None:
        self.set_render_cache_byte_limit_bytes(
            max(
                64,
                min(4096, int(memory_mib)),
            )
            * 1024
            * 1024
        )

    def set_render_cache_byte_limit_bytes(
        self,
        memory_bytes: int,
    ) -> None:
        self._render_cache_byte_limit = max(1, int(memory_bytes))
        self._enforce_render_cache_limit()

    def render_cache_bytes(self) -> int:
        return sum(
            pixmap.width() * pixmap.height() * 4
            for pixmap in self._render_cache.values()
        )

    def set_resampling_modes(
        self,
        *,
        normal: str | None = None,
        magnifier: str | None = None,
    ) -> None:
        normal_changed = False
        magnifier_changed = False
        if normal is not None:
            normalized = normalize_resampling_mode(normal)
            if normalized != self.resampling_mode:
                self.resampling_mode = normalized
                normal_changed = True
        if magnifier is not None:
            normalized = normalize_resampling_mode(magnifier)
            if normalized != self.magnifier_resampling_mode:
                self.magnifier_resampling_mode = normalized
                magnifier_changed = True
                if self.magnifier_selecting or self.magnifier_active:
                    self.cancel_magnifier()
        if normal_changed:
            self._refresh_current_render()
        elif magnifier_changed:
            self.update()

    def event(self, event) -> bool:  # type: ignore[override]
        handled = super().event(event)
        if (
            event.type() == QEvent.Type.DevicePixelRatioChange
            and hasattr(self, "_render_pool")
        ):
            # A monitor transition can change physical target dimensions
            # without a logical QWidget resize.
            if self._direct_display_mode:
                # The compatible raster path owns decoder sizing.  Ask the
                # window to invalidate its physical-pixel artifact instead of
                # merely repainting the old-DPR QPixmap.
                self.viewportChanged.emit()
            else:
                self._refresh_current_render()
        return handled

    def set_horizontal_alignment(self, alignment: str) -> None:
        if alignment not in {"left", "center", "right"}:
            alignment = "center"
        if alignment != self.horizontal_alignment:
            self.cancel_magnifier()
        self.horizontal_alignment = alignment
        self._refresh_current_render()

    def set_magnifier_enabled(self, enabled: bool) -> None:
        # Kept as a compatibility shim for older settings callers. Magnification
        # is now an immediate pointer action and no longer has a pre-enable mode.
        if not enabled:
            self.cancel_magnifier()

    def set_magnifier_options(self, *, zoom: float | None = None, size: int | None = None) -> None:
        if zoom is not None:
            normalized_zoom = min(4.0, max(1.5, float(zoom)))
            if not math.isclose(normalized_zoom, self.magnifier_zoom):
                self.magnifier_zoom = normalized_zoom
                if self.magnifier_selecting or self.magnifier_active:
                    self.cancel_magnifier()
        if size is not None:
            self.magnifier_size = min(600, max(80, int(size)))
        self.update()

    def cancel_magnifier(self) -> bool:
        was_active = (
            self.magnifier_selecting
            or self.magnifier_active
            or self._magnifier_key is not None
            or self._magnifier_waiting_for_pdf
        )
        for task in tuple(self._render_tasks):
            if (
                task.key.purpose == "magnifier"
                and self._try_take_render_task(task)
            ):
                self._discard_render_task(task)
        self.magnifier_selecting = False
        self.magnifier_active = False
        self.magnifier_source_page = None
        self.magnifier_source_rect = None
        self.magnifier_previous_view_state = None
        self._magnifier_source_image_id = None
        self._magnifier_source_normalized = None
        self._magnifier_selection_rect = None
        self._magnifier_pixmap = None
        self._magnifier_key = None
        self._magnifier_waiting_for_pdf = False
        self._magnifier_pdf_source_key = 0
        self.magnifier_request_generation += 1
        if was_active:
            self.magnifierCancelled.emit()
            self.update()
        return was_active

    def toggle_magnifier(self, position: QPoint | None = None) -> bool:
        if (
            self.magnifier_selecting
            or self.magnifier_active
            or self._magnifier_key is not None
            or self._magnifier_waiting_for_pdf
        ):
            self.cancel_magnifier()
            return True
        target = QPoint(position) if position is not None else self._mouse_pos
        if target is None or not self._begin_magnifier_selection(target):
            return False
        self._request_magnifier_render()
        return True

    def set_auto_hide_cursor(self, enabled: bool) -> None:
        # FullscreenChromeController owns cursor idling. Keep this method only
        # as a source-compatible normal-cursor reset for older integrations.
        del enabled
        self.unsetCursor()

    def set_mouse_gesture_options(
        self,
        *,
        enabled: bool | None = None,
        show_trail: bool | None = None,
        min_distance: int | None = None,
    ) -> None:
        if enabled is not None:
            self.mouse_gestures_enabled = bool(enabled)
        if show_trail is not None:
            self.mouse_gesture_show_trail = bool(show_trail)
        if min_distance is not None:
            self.mouse_gesture_min_distance = max(12, min(200, int(min_distance)))
        if self._gesture_recognizer.active:
            self.cancel_mouse_gesture()
        self._gesture_recognizer = MouseGestureRecognizer(
            self.mouse_gesture_min_distance
        )
        if not self.mouse_gesture_show_trail:
            self._gesture_trail.clear()
            self.update()

    @property
    def gesture_in_progress(self) -> bool:
        return self._right_button_down and bool(self._gesture_recognizer.pattern)

    @property
    def displayed_page_indexes(self) -> tuple[int, ...]:
        return tuple(slot.page_index for slot in self._spread.slots)

    def displayed_source_snapshot(
        self,
        page_index: int,
    ) -> tuple[QImage | None, tuple[int, int] | None, str | None] | None:
        """Return source metadata from the atomically committed frame.

        Runtime-backed books intentionally do not mirror decoded sources into
        ``ImageCache``.  Clipboard/page-info actions therefore project from
        the same committed frame as paint instead of consulting requested-page
        state or a second cache owner.
        """

        for image in self._images:
            if image.page_index != int(page_index):
                continue
            source = (
                QImage(image.qimage)
                if image.qimage is not None and not image.qimage.isNull()
                else None
            )
            return source, image.original_size, image.error
        return None

    @property
    def gesture_trail(self) -> tuple[QPoint, ...]:
        return tuple(self._gesture_trail)

    def cancel_mouse_gesture(self) -> bool:
        was_active = (
            self._right_button_down
            or self._gesture_recognizer.active
            or bool(self._gesture_trail)
        )
        self._gesture_recognizer.cancel()
        self._gesture_trail.clear()
        if self._right_button_down:
            self._suppress_context_until_release = True
        self.update()
        return was_active

    def set_rotation_angle(self, angle: int) -> None:
        normalized = angle % 360
        if normalized != self.rotation_angle:
            self.cancel_magnifier()
        self.rotation_angle = normalized
        self._pan = QPoint(0, 0)
        self._refresh_current_render()

    def set_fit_mode(self, fit_mode: str) -> None:
        if fit_mode != self.fit_mode:
            self.cancel_magnifier()
        self.fit_mode = fit_mode
        if fit_mode in {"fit_window", "fit_no_upscale", "fit_width", "fit_height"}:
            self._pan = QPoint(0, 0)
        self._refresh_current_render()

    def set_manual_zoom(self, zoom: float) -> None:
        self.cancel_magnifier()
        self.manual_zoom = min(8.0, max(0.05, zoom))
        self.fit_mode = "manual_zoom"
        self.zoomChanged.emit(self.manual_zoom)
        self._refresh_current_render()

    def reset_zoom(self) -> None:
        self.cancel_magnifier()
        self.fit_mode = "fit_window"
        self.manual_zoom = 1.0
        self._pan = QPoint(0, 0)
        self._refresh_current_render()

    def scroll_forward(self) -> bool:
        return self._scroll_vertical(1)

    def scroll_backward(self) -> bool:
        return self._scroll_vertical(-1)

    def set_pages(
        self,
        spread: DisplaySpread,
        pages: list[ViewerImage],
        *,
        frame_token: object | None = None,
    ) -> None:
        if self._direct_display_mode:
            return
        same_display_unit = (
            spread.start_index == self._spread.start_index
            and tuple(slot.page_index for slot in spread.slots)
            == tuple(slot.page_index for slot in self._spread.slots)
        )
        if not same_display_unit:
            self.cancel_magnifier()
            self._pan = QPoint(0, 0)
        if same_display_unit and self._magnifier_waiting_for_pdf:
            self._resume_magnifier_after_pdf_render()
        self._prepare_display(spread, pages, frame_token=frame_token)

    def set_direct_display_mode(self, active: bool) -> None:
        """Enable the QPixmap-only display-ready commit boundary.

        Entering the mode invalidates every production resize/render/prepared
        request, but snapshots the last complete pixmap first so switching
        pipelines cannot blank an already painted frame.
        """

        normalized = bool(active)
        if normalized == self._direct_display_mode:
            return
        if normalized:
            layout = self._layout_for_current_images()
            preserved_images: list[ViewerImage] = []
            for image, rect in zip(self._images, layout.rects):
                pixmap = self._pixmap_for_paint(image, rect)
                preserved_images.append(
                    replace(
                        image,
                        pixmap=(
                            QPixmap(pixmap)
                            if pixmap is not None and not pixmap.isNull()
                            else None
                        ),
                        qimage=None,
                        pre_rotated=True,
                        display_prepared=(
                            pixmap is not None and not pixmap.isNull()
                        ),
                    )
                )
            self.cancel_magnifier()
            self._resize_render_timer.stop()
            self._deferred_render_target = None
            self._invalidate_render_requests(clear_cache=True)
            self._images = preserved_images
            self._direct_current_frame_serial = 0
            self._direct_current_frame_token = None
        else:
            self._direct_current_frame_serial = 0
            self._direct_current_frame_token = None
        self._direct_display_mode = normalized

    def commit_display_ready_single(
        self,
        spread: DisplaySpread,
        page_index: int,
        image_id: str,
        original_size: tuple[int, int],
        pixmap: QPixmap,
        frame_token: object,
    ) -> int:
        """Atomically publish one already prepared QPixmap-only frame."""

        if (
            not spread.is_single
            or len(spread.slots) != 1
            or spread.slots[0].page_index != int(page_index)
            or spread.slots[0].image_id != str(image_id)
        ):
            raise ValueError("direct display commit requires one matching page")
        width, height = (int(original_size[0]), int(original_size[1]))
        if width <= 0 or height <= 0:
            raise ValueError("original_size must contain positive dimensions")
        if pixmap.isNull():
            raise ValueError("direct display pixmap must not be null")

        return self.commit_display_ready_frame(
            spread,
            (
                ViewerImage(
                    page_index=int(page_index),
                    image_id=str(image_id),
                    pixmap=QPixmap(pixmap),
                    original_size=(width, height),
                    pre_rotated=True,
                    qimage=None,
                    display_prepared=True,
                ),
            ),
            frame_token,
        )

    def commit_display_ready_frame(
        self,
        spread: DisplaySpread,
        images: Iterable[ViewerImage],
        frame_token: object,
    ) -> int:
        """Publish one complete runtime-prepared frame in a single swap.

        A book runtime may provide a single page, a normal two-page spread, or
        two split artifacts derived from one wide logical page.  Every image
        must already be terminal: it either owns a non-null display pixmap or
        carries an error placeholder.  Source ``QImage`` handles may remain
        attached for magnifier work; they are not used for the main paint.
        """

        if not self._direct_display_mode:
            raise RuntimeError("display-ready runtime mode is not active")
        prepared = tuple(images)
        if not prepared:
            raise ValueError("display-ready frame must contain an image")
        spread_pages = {slot.page_index for slot in spread.slots}
        prepared_pages = {image.page_index for image in prepared}
        if prepared_pages != spread_pages:
            raise ValueError("display-ready frame does not match its spread")
        if not spread.is_single and len(prepared) != len(spread.slots):
            raise ValueError("display-ready spread must contain every slot once")
        for image in prepared:
            has_pixmap = image.pixmap is not None and not image.pixmap.isNull()
            if not has_pixmap and not image.error:
                raise ValueError(
                    "display-ready image must contain a pixmap or error"
                )

        same_display_unit = (
            spread.start_index == self._spread.start_index
            and tuple(slot.page_index for slot in spread.slots)
            == tuple(slot.page_index for slot in self._spread.slots)
        )
        if not same_display_unit:
            self._pan = QPoint(0, 0)

        self._direct_frame_serial += 1
        frame_serial = self._direct_frame_serial
        committed_images = tuple(
            replace(
                image,
                pixmap=(
                    QPixmap(image.pixmap)
                    if image.pixmap is not None and not image.pixmap.isNull()
                    else None
                ),
                loading=False,
                display_prepared=(
                    image.pixmap is not None and not image.pixmap.isNull()
                ),
            )
            for image in prepared
        )
        self._direct_current_frame_serial = frame_serial
        self._direct_current_frame_token = frame_token
        self._spread = spread
        self._images = list(committed_images)
        self.update()
        self._emit_frame_committed(
            spread,
            committed_images,
            frame_token=frame_token,
        )
        self.displayCommitted.emit(
            tuple(image.image_id for image in committed_images)
        )
        return frame_serial

    @staticmethod
    def from_qimage(
        page_index: int,
        image_id: str,
        qimage: QImage,
        original_size: tuple[int, int],
        rendered_size: tuple[int, int] | None = None,
        pre_rotated: bool = False,
        create_pixmap: bool = True,
        source_generation: int = 0,
        source_identity: str = "",
        split_range: tuple[int, int, int, int] | None = None,
        source_is_preview: bool = False,
    ) -> ViewerImage:
        return ViewerImage(
            page_index=page_index,
            image_id=image_id,
            pixmap=QPixmap.fromImage(qimage) if create_pixmap else None,
            original_size=original_size,
            rendered_size=rendered_size,
            pre_rotated=pre_rotated,
            qimage=QImage(qimage),
            source_generation=int(source_generation),
            source_identity=str(source_identity),
            split_range=split_range,
            source_is_preview=bool(source_is_preview),
        )

    @staticmethod
    def loading_page(page_index: int, image_id: str) -> ViewerImage:
        return ViewerImage(page_index=page_index, image_id=image_id, pixmap=None, original_size=None, loading=True)

    @staticmethod
    def error_page(page_index: int, image_id: str, error: str) -> ViewerImage:
        return ViewerImage(page_index=page_index, image_id=image_id, pixmap=None, original_size=None, error=error)

    def clear(self) -> None:
        self.cancel_magnifier()
        self._invalidate_render_requests(clear_cache=True)
        self._direct_current_frame_serial = 0
        self._direct_current_frame_token = None
        self._spread = DisplaySpread(0, tuple(), True)
        self._images = []
        self._last_draw_layout.clear()
        self._last_image_layout.clear()
        self._pan = QPoint(0, 0)
        self.update()

    def current_resolution_text(self) -> str:
        if not self._images:
            return ""
        parts = []
        for image in self._images:
            size = image.rendered_size or image.original_size
            if size is not None:
                parts.append(f"{size[0]}x{size[1]}")
            elif image.error:
                parts.append("error")
            else:
                parts.append("loading")
        return " + ".join(parts)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.background_color)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth_scaling)

        if not self._images:
            painter.setPen(QColor("#777777"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "画像を開いてください")
            self._draw_gesture_trail(painter)
            return

        layout = self._layout_for_current_images()
        self._last_draw_layout = []
        self._last_image_layout = []
        painted_image_ids: list[str] = []
        for image, rect in zip(self._images, layout.rects):
            pixmap = self._pixmap_for_paint(image, rect)
            if pixmap is not None:
                painter.drawPixmap(rect, pixmap)
                self._last_draw_layout.append((rect, pixmap))
                self._last_image_layout.append((rect, image, pixmap))
                painted_image_ids.append(image.image_id)
            else:
                self._draw_placeholder(painter, rect, image)
                if image.error:
                    # A terminal error placeholder is still a completed frame.
                    # Report it so after-paint scheduling and Browser gating
                    # cannot wait forever for a pixmap that will never exist.
                    painted_image_ids.append(image.image_id)
        self._draw_magnifier(painter)
        self._draw_gesture_trail(painter)
        if painted_image_ids:
            painted_ids = tuple(painted_image_ids)
            self.contentPainted.emit(painted_ids)
            if (
                self._direct_display_mode
                and self._direct_current_frame_serial > 0
            ):
                self.framePainted.emit(
                    self._direct_current_frame_serial,
                    painted_ids,
                )

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        if event.size() != event.oldSize():
            if self.magnifier_selecting or self.magnifier_active:
                self.cancel_magnifier()
            if not self._direct_display_mode:
                pending = self._pending_display
                if pending is not None:
                    if pending.frame_token is not None:
                        # A page request belongs to the old viewport/DPR
                        # identity. Keep the last committed frame; Window will
                        # issue a new presentation token for the resized view.
                        deferred_render_target = (
                            self._spread,
                            tuple(self._images),
                            None,
                        )
                    else:
                        deferred_render_target = (
                            pending.spread,
                            pending.images,
                            None,
                        )
                elif self._deferred_render_target is not None:
                    deferred_render_target = self._deferred_render_target
                else:
                    deferred_render_target = (
                        self._spread,
                        tuple(self._images),
                        None,
                    )
                # Reject old-size work immediately, but retain the last complete
                # frame until the debounced replacement is ready.
                self._invalidate_render_requests(clear_cache=False)
                self._deferred_render_target = deferred_render_target
                self._resize_render_timer.start()
        if self.fit_mode in {"fit_window", "fit_no_upscale", "fit_width", "fit_height"}:
            self._pan = QPoint(0, 0)
        super().resizeEvent(event)
        self.viewportChanged.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if delta > 0 else 1 / 1.15
            base = self._scale_for_current_mode() if self.fit_mode != "manual_zoom" else self.manual_zoom
            self.set_manual_zoom(base * factor)
            event.accept()
            return

        if delta < 0:
            self.nextRequested.emit()
        else:
            self.previousRequested.emit()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._show_cursor_temporarily()
        if event.button() == Qt.MouseButton.MiddleButton:
            if self.toggle_magnifier(event.position().toPoint()):
                event.accept()
                return
        if event.button() == Qt.MouseButton.BackButton:
            self.extraMouseButtonPressed.emit("back")
            event.accept()
            return
        if event.button() == Qt.MouseButton.ForwardButton:
            self.extraMouseButtonPressed.emit("forward")
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.cancel_pending_canvas_click()
            self._right_button_down = True
            self._suppress_context_until_release = False
            if self.mouse_gestures_enabled:
                self._gesture_trail = [event.position().toPoint()]
                point = event.position()
                self._gesture_recognizer.begin((point.x(), point.y()))
            else:
                self._gesture_trail.clear()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            position = event.position().toPoint()
            global_position = event.globalPosition().toPoint()
            flags = self._canvas_press_flags(global_position)
            self._press_position = position
            self._drag_start = position
            self._drag_origin = QPoint(self._pan)
            allowed = self.canvas_pointer.begin(
                local_position=position,
                global_position=global_position,
                button=event.button(),
                modifiers=event.modifiers(),
                fullscreen=bool(flags.get("fullscreen", False)),
                overlay=bool(flags.get("overlay", False)),
                edge_trigger=bool(flags.get("edge_trigger", False)),
                mouse_gesture=bool(flags.get("mouse_gesture", False)),
                drop_active=bool(flags.get("drop_active", False)),
            )
            self._canvas_press_side = (
                self.canvas_click_side_at(position) if allowed else None
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._show_cursor_temporarily()
        self._mouse_pos = event.position().toPoint()
        if (
            (self.magnifier_selecting or self.magnifier_active)
            and not (
                event.buttons()
                & (
                    Qt.MouseButton.LeftButton
                    | Qt.MouseButton.RightButton
                )
            )
        ):
            self._update_magnifier_selection(self._mouse_pos)
            self._request_magnifier_render()
            event.accept()
            return
        if self._right_button_down:
            point = event.position()
            if self._gesture_recognizer.active:
                self._gesture_trail.append(point.toPoint())
                self._gesture_recognizer.update((point.x(), point.y()))
            if self.mouse_gesture_show_trail:
                self.update()
            event.accept()
            return
        if (
            self._drag_start is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            state = self.canvas_pointer.move(event.position().toPoint())
            delta = event.position().toPoint() - self._drag_start
            if state is ViewerCanvasPointerState.PANNING:
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                if self._can_pan():
                    self._pan = self._drag_origin + delta
                    self.update()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._mouse_pos = None
        super().leaveEvent(event)

    def _show_cursor_temporarily(self) -> None:
        return

    def _hide_cursor(self) -> None:
        return

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.MiddleButton and (
            self.magnifier_selecting or self.magnifier_active
        ):
            event.accept()
            return
        if event.button() in {
            Qt.MouseButton.BackButton,
            Qt.MouseButton.ForwardButton,
        }:
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            release_position = event.position().toPoint()
            suppressed = self._suppress_context_until_release
            pattern = ""
            if self._gesture_recognizer.active:
                point = event.position()
                pattern = self._gesture_recognizer.finish((point.x(), point.y()))
            self._right_button_down = False
            self._suppress_context_until_release = False
            self._gesture_trail.clear()
            self.update()
            if not suppressed:
                if pattern:
                    self.gestureRecognized.emit(pattern)
                else:
                    self.contextMenuRequested.emit(release_position)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            local_position = event.position().toPoint()
            global_position = event.globalPosition().toPoint()
            release_side = self.canvas_click_side_at(local_position)
            if (
                self._canvas_press_side is not None
                and release_side == self._canvas_press_side
            ):
                self._pending_canvas_click_side = release_side
                self._pending_canvas_click_global = QPoint(global_position)
                self.canvas_pointer.release(
                    local_position=local_position,
                    global_position=global_position,
                    immediate=self._canvas_side_click_enabled,
                )
            else:
                self.canvas_pointer.cancel()
                self._pending_canvas_click_side = None
            self._drag_start = None
            self._press_position = None
            self._canvas_press_side = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        side = self.canvas_click_side_at(event.position().toPoint())
        if (
            self._canvas_side_click_enabled
            and side is not None
            and event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and self._canvas_click_allowed(event.globalPosition().toPoint())
        ):
            self.canvas_pointer.cancel()
            self._drag_start = None
            self._press_position = None
            self._canvas_press_side = None
            self._pending_canvas_click_side = None
            self.unsetCursor()
            self._emit_canvas_side_click(side)
            event.accept()
            return
        if self.canvas_pointer.double_click(
            button=event.button(),
            modifiers=event.modifiers(),
            global_position=event.globalPosition().toPoint(),
        ):
            self._drag_start = None
            self._press_position = None
            self._canvas_press_side = None
            self._pending_canvas_click_side = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        self.cancel_pending_canvas_click()
        super().focusOutEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # type: ignore[override]
        event.accept()

    def _can_pan(self) -> bool:
        content_size = self._layout_for_current_images().content_size
        return content_size.width() > self.width() or content_size.height() > self.height()

    def _scroll_vertical(self, direction: int) -> bool:
        if not self._images:
            return False
        content_size = self._layout_for_current_images().content_size
        overflow = content_size.height() - self.height()
        if overflow <= 4:
            return False

        min_pan = -((overflow + 1) // 2)
        max_pan = overflow // 2
        step = max(60, int(self.height() * 0.82))
        old_y = self._pan.y()
        new_y = old_y - step if direction > 0 else old_y + step
        new_y = max(min_pan, min(max_pan, new_y))
        if new_y == old_y:
            return False
        self._pan.setY(new_y)
        self.update()
        return True

    def _scale_for_current_mode(self) -> float:
        return self._layout_for_current_images().scale

    def _content_size(self, scale: float) -> QSize:
        if not self._images:
            return QSize(0, 0)

        width = sum(max(1, round(self._base_size(image).width() * scale)) for image in self._images)
        if len(self._images) > 1:
            width += self._effective_gap() * (len(self._images) - 1)
        height = max(max(1, round(self._base_size(image).height() * scale)) for image in self._images)
        return QSize(width, height)

    def _layout_for_current_images(self) -> SpreadLayout:
        return self._layout_for_images(self._spread, self._images)

    def _layout_for_images(
        self,
        spread: DisplaySpread,
        images: list[ViewerImage] | tuple[ViewerImage, ...],
    ) -> SpreadLayout:
        sizes = [
            (self._base_size(image).width(), self._base_size(image).height())
            for image in images
        ]
        return calculate_spread_layout(
            sizes,
            (self.width(), self.height()),
            fit_mode=self.fit_mode,
            manual_zoom=self.manual_zoom,
            gap=self.gap,
            join_spread_pages=self.join_spread_pages,
            spread_is_single=spread.is_single,
            horizontal_alignment=self.horizontal_alignment,
            pan=(self._pan.x(), self._pan.y()),
        )

    def _effective_gap(self) -> int:
        if (
            self.join_spread_pages
            and len(self._images) == 2
            and not self._spread.is_single
        ):
            return 0
        return self.gap if len(self._images) > 1 else 0

    def _base_size(self, image: ViewerImage) -> QSize:
        if image.original_size is not None:
            size = QSize(*image.original_size)
        elif image.pixmap is not None:
            size = image.pixmap.size()
        else:
            size = QSize(360, 520)
        if self.rotation_angle in (90, 270):
            return QSize(size.height(), size.width())
        return size

    def _pixmap_for_paint(
        self,
        image: ViewerImage,
        target_rect: QRect,
    ) -> QPixmap | None:
        key = self._viewer_render_key(image, target_rect)
        if key is None:
            return self._display_pixmap(image)
        cached = self._render_cache.get(key)
        if cached is not None:
            self._render_cache.move_to_end(key)
            return cached
        return self._last_rendered_by_image.get(
            image.image_id,
            self._display_pixmap(image),
        )

    def _viewer_render_key(
        self,
        image: ViewerImage,
        target_rect: QRect,
    ) -> ViewerRenderKey | None:
        if (
            image.qimage is None
            or (
                self.resampling_mode != "standard"
                and target_rect.size() == self._base_size(image)
                and (self.rotation_angle == 0 or image.pre_rotated)
                and image.pixmap is not None
            )
        ):
            return None
        dpr = max(1.0, float(self.devicePixelRatioF()))
        rotation = 0 if image.pre_rotated else self.rotation_angle
        target_width = max(1, round(target_rect.width() * dpr))
        target_height = max(1, round(target_rect.height() * dpr))
        source_sized = False
        if self.resampling_mode == "standard":
            source_size = self._rotated_source_size(image)
            if (
                source_size.isValid()
                and (
                    target_width > source_size.width()
                    or target_height > source_size.height()
                )
            ):
                # Standard keeps the historical QPainter upscale path.  A
                # source-sized artifact is enough above 100%; allocating an
                # 8x target pixmap for manual zoom can otherwise require
                # several GiB for one large page.
                scale = min(
                    1.0,
                    target_width / source_size.width(),
                    target_height / source_size.height(),
                )
                target_width = max(1, round(source_size.width() * scale))
                target_height = max(1, round(source_size.height() * scale))
            source_sized = (
                source_size.isValid()
                and target_width == source_size.width()
                and target_height == source_size.height()
            )
        return ViewerRenderKey(
            image_id=image.image_id,
            source_cache_key=int(image.qimage.cacheKey()),
            target_width=target_width,
            target_height=target_height,
            mode=self.resampling_mode,
            rotation=rotation,
            device_pixel_ratio_milli=round(dpr * 1000),
            layout_generation=0 if source_sized else self._render_generation,
            source_generation=image.source_generation,
            source_identity=image.source_identity,
            split_range=image.split_range,
            smooth_transform=(
                self.smooth_scaling
                if (
                    self.resampling_mode == "standard"
                    and not source_sized
                )
                else True
            ),
            source_sized=source_sized,
        )

    def _refresh_current_render(self) -> None:
        if self._direct_display_mode:
            self._resize_render_timer.stop()
            self._deferred_render_target = None
            self.update()
            return
        deferred = self._deferred_render_target
        self._deferred_render_target = None
        pending = self._pending_display
        spread = (
            deferred[0]
            if deferred is not None
            else pending.spread if pending is not None else self._spread
        )
        images = list(
            deferred[1]
            if deferred is not None
            else pending.images if pending is not None else self._images
        )
        frame_token = (
            deferred[2]
            if deferred is not None and len(deferred) >= 3
            else pending.frame_token if pending is not None else None
        )
        self._invalidate_render_requests(clear_cache=False)
        if not images:
            self.update()
            return
        self._prepare_display(
            spread,
            images,
            frame_token=frame_token,
        )

    def _prepared_unit_key(
        self,
        spread: DisplaySpread,
        pages: list[ViewerImage] | tuple[ViewerImage, ...],
    ) -> PreparedDisplayUnitKey:
        layout = self._layout_for_images(spread, pages)
        render_keys = tuple(
            self._viewer_render_key(image, rect)
            for image, rect in zip(pages, layout.rects)
        )
        return PreparedDisplayUnitKey(
            spread_identity=tuple(
                (slot.page_index, slot.image_id) for slot in spread.slots
            ),
            image_ids=tuple(image.image_id for image in pages),
            render_keys=render_keys,
            layout_generation=self._render_generation,
        )

    def prepare_display_units(
        self,
        units: Iterable[
            tuple[
                int,
                DisplaySpread,
                list[ViewerImage] | None,
                bool,
            ]
        ],
    ) -> None:
        if self._direct_display_mode:
            return
        desired_by_identity: dict[
            tuple[tuple[int, str], ...],
            tuple[int, bool],
        ] = {}
        planned: dict[PreparedDisplayUnitKey, _PreparedUnitRequest] = {}
        for priority, spread, pages, protected in units:
            spread_identity = tuple(
                (slot.page_index, slot.image_id) for slot in spread.slots
            )
            normalized_priority = max(0, int(priority))
            previous_desired = desired_by_identity.get(spread_identity)
            if (
                previous_desired is None
                or normalized_priority < previous_desired[0]
            ):
                desired_by_identity[spread_identity] = (
                    normalized_priority,
                    bool(protected),
                )
            if pages is None:
                continue
            unit_key = self._prepared_unit_key(spread, pages)
            request = _PreparedUnitRequest(
                key=unit_key,
                spread=spread,
                images=tuple(pages),
                sources=tuple(image.qimage for image in pages),
                priority=normalized_priority,
                protected=bool(protected),
                failed_keys=set(),
            )
            existing = planned.get(unit_key)
            if existing is None or request.priority < existing.priority:
                planned[unit_key] = request

        planned_by_identity = {
            key.spread_identity: key for key in planned
        }
        self._prepared_units = OrderedDict(
            (key, unit)
            for key, unit in self._prepared_units.items()
            if (
                key.spread_identity in desired_by_identity
                and (
                    key.spread_identity not in planned_by_identity
                    or planned_by_identity[key.spread_identity] == key
                )
            )
        )
        retained_requests: dict[
            PreparedDisplayUnitKey,
            _PreparedUnitRequest,
        ] = {}
        for key, request in self._prepared_requests.items():
            desired = desired_by_identity.get(key.spread_identity)
            if (
                desired is None
                or (
                    key.spread_identity in planned_by_identity
                    and planned_by_identity[key.spread_identity] != key
                )
            ):
                continue
            request.priority, request.protected = desired
            retained_requests[key] = request
        self._prepared_requests = retained_requests

        for unit_key, request in sorted(
            planned.items(),
            key=lambda item: item[1].priority,
        ):
            if self._unit_render_keys_ready(unit_key.render_keys):
                self._store_prepared_unit(request)
                self._prepared_requests.pop(unit_key, None)
                continue
            self._prepared_requests[unit_key] = request

        pending_render_keys = (
            {
                key
                for key in self._pending_display.keys
                if key is not None
            }
            if self._pending_display is not None
            else set()
        )
        for request in sorted(
            self._prepared_requests.values(),
            key=lambda value: value.priority,
        ):
            # Decode and display preparation share the same one-worker Viewer
            # lane. Keep both stages in the coordinator's priority domain so a
            # newly requested display artifact cannot sit behind queued source
            # prefetch work merely because the old render pool used 0-100.
            queue_priority = (
                int(ImageWorkPriority.VIEWER_SPREAD_PARTNER)
                - request.priority
            )
            for source, render_key in zip(
                request.sources,
                request.key.render_keys,
            ):
                if (
                    render_key is not None
                    and render_key not in self._render_cache
                    and source is not None
                ):
                    self._queue_render(
                        source,
                        render_key,
                        priority=queue_priority,
                        allow_priority_decrease=(
                            render_key not in pending_render_keys
                        ),
                    )

        tracked_keys = (
            set(self._prepared_units)
            | set(self._prepared_requests)
        )
        self._prepared_unit_priorities = {
            key: desired_by_identity[key.spread_identity][0]
            for key in tracked_keys
            if key.spread_identity in desired_by_identity
        }
        self._protected_prepared_units = {
            key
            for key in tracked_keys
            if desired_by_identity.get(key.spread_identity, (0, False))[1]
        }
        allowed_render_keys = {
            key
            for request in self._prepared_requests.values()
            for key in request.key.render_keys
            if key is not None
        }
        pending = self._pending_display
        if pending is not None:
            allowed_render_keys.update(pending_render_keys)
        for task in tuple(self._render_tasks):
            if (
                task.key.purpose == "viewer"
                and task.key not in allowed_render_keys
                and self._try_take_render_task(task)
            ):
                self._discard_render_task(task)
        self._enforce_render_cache_limit()

    def _store_prepared_unit(
        self,
        request: _PreparedUnitRequest,
    ) -> None:
        images = tuple(
            replace(image, pixmap=None, qimage=None)
            for image in request.images
        )
        self._prepared_units[request.key] = _PreparedDisplayUnit(
            request.spread,
            images,
            request.key.render_keys,
        )
        self._prepared_units.move_to_end(request.key)

    def tracks_prepared_display_unit(
        self,
        spread: DisplaySpread,
        *,
        source_generation: int,
        source_identity: str,
    ) -> bool:
        if self._direct_display_mode:
            return False
        identity = tuple(
            (slot.page_index, slot.image_id) for slot in spread.slots
        )

        def matches(
            key: PreparedDisplayUnitKey,
            images: tuple[ViewerImage, ...],
        ) -> bool:
            return (
                key.spread_identity == identity
                and key.layout_generation == self._render_generation
                and all(
                    image.source_generation == int(source_generation)
                    and image.source_identity == str(source_identity)
                    for image in images
                )
                and all(
                    render_key is None
                    or render_key.mode == self.resampling_mode
                    for render_key in key.render_keys
                )
            )

        return any(
            matches(key, unit.images)
            for key, unit in self._prepared_units.items()
        ) or any(
            matches(key, request.images)
            for key, request in self._prepared_requests.items()
        )

    def prepared_display_is_ready(
        self,
        spread: DisplaySpread,
        *,
        source_generation: int,
        source_identity: str,
    ) -> bool:
        if self._direct_display_mode:
            return False
        identity = tuple(
            (slot.page_index, slot.image_id) for slot in spread.slots
        )
        return any(
            (
                key.spread_identity == identity
                and key.layout_generation == self._render_generation
                and all(
                    image.source_generation == int(source_generation)
                    and image.source_identity == str(source_identity)
                    for image in unit.images
                )
                and all(
                    render_key is None
                    or (
                        render_key.mode == self.resampling_mode
                        and render_key in self._render_cache
                    )
                    for render_key in key.render_keys
                )
            )
            for key, unit in self._prepared_units.items()
        )

    def has_pending_prepared_rendering(self) -> bool:
        if self._direct_display_mode:
            return False
        return bool(self._prepared_requests) or any(
            task.key.purpose == "viewer"
            for task in self._render_tasks
        )

    def apply_prepared_display(
        self,
        spread: DisplaySpread,
        *,
        source_generation: int,
        source_identity: str,
        frame_token: object | None = None,
    ) -> bool:
        if self._direct_display_mode:
            return False
        identity = tuple(
            (slot.page_index, slot.image_id) for slot in spread.slots
        )
        for unit_key, unit in reversed(self._prepared_units.items()):
            if (
                unit_key.spread_identity != identity
                or unit_key.layout_generation != self._render_generation
            ):
                continue
            render_keys = unit.render_keys
            concrete_keys = tuple(
                key for key in render_keys if key is not None
            )
            if (
                len(concrete_keys) != len(render_keys)
                or any(
                    key.source_generation != int(source_generation)
                    or key.source_identity != str(source_identity)
                    or key.mode != self.resampling_mode
                    for key in concrete_keys
                )
                or any(key not in self._render_cache for key in concrete_keys)
            ):
                continue
            self.supersede_pending_display()
            images = tuple(
                replace(
                    image,
                    pixmap=self._render_cache[key],
                    qimage=None,
                    display_prepared=True,
                )
                for image, key in zip(unit.images, concrete_keys)
            )
            self.cancel_magnifier()
            self._display_request_generation += 1
            self._pending_display = None
            self._prepared_units.move_to_end(unit_key)
            self._commit_display(
                spread,
                images,
                frame_token=frame_token,
            )
            return True
        return False

    def supersede_pending_display(self) -> None:
        self._resize_render_timer.stop()
        self._deferred_render_target = None
        pending = self._pending_display
        if pending is None:
            return
        self._pending_display = None
        self._display_request_generation += 1
        pending_keys = {
            key for key in pending.keys if key is not None
        }
        prepared_keys = {
            key
            for request in self._prepared_requests.values()
            for key in request.key.render_keys
            if key is not None
        }
        for task in tuple(self._render_tasks):
            if (
                task.key in pending_keys
                and task.key not in prepared_keys
                and self._try_take_render_task(task)
            ):
                self._discard_render_task(task)

    def invalidate_prepared_displays(self) -> None:
        self._resize_render_timer.stop()
        self._deferred_render_target = None
        self._invalidate_render_requests(clear_cache=False)

    def attach_current_sources(
        self,
        pages: Iterable[ViewerImage],
    ) -> None:
        if self._direct_display_mode:
            return
        sources = {image.image_id: image for image in pages}
        if not sources:
            return
        attached: list[ViewerImage] = []
        for image in self._images:
            source = sources.get(image.image_id)
            if source is None or source.qimage is None:
                attached.append(image)
                continue
            attached.append(
                replace(
                    image,
                    qimage=QImage(source.qimage),
                    original_size=source.original_size,
                    rendered_size=source.rendered_size,
                    pre_rotated=source.pre_rotated,
                    source_generation=source.source_generation,
                    source_identity=source.source_identity,
                    split_range=source.split_range,
                    source_is_preview=source.source_is_preview,
                )
            )
        self._images = attached

    def _prepare_display(
        self,
        spread: DisplaySpread,
        pages: list[ViewerImage],
        *,
        frame_token: object | None = None,
    ) -> None:
        if self._direct_display_mode:
            return
        if self._deferred_render_target is not None:
            # A page request arriving during resize debounce supersedes the
            # older viewport snapshot.  Otherwise the timer could revive an
            # empty or previous display after the new request was queued.
            self._deferred_render_target = (
                spread,
                tuple(pages),
                frame_token,
            )
        self._display_request_generation += 1
        request_generation = self._display_request_generation
        unit_key = self._prepared_unit_key(spread, pages)
        keys = unit_key.render_keys
        pending = _PendingDisplay(
            spread=spread,
            images=tuple(pages),
            keys=keys,
            generation=self._render_generation,
            request_generation=request_generation,
            failed_keys={},
            frame_token=frame_token,
        )
        self._pending_display = pending
        for task in tuple(self._render_tasks):
            if (
                task.key.purpose == "magnifier"
                and self._try_take_render_task(task)
            ):
                self._discard_render_task(task)
        missing = [
            (image.qimage, key)
            for image, key in zip(pages, keys)
            if key is not None
            and key not in self._render_cache
            and image.qimage is not None
        ]
        if not missing:
            self._commit_pending_display()
            return
        for source, key in missing:
            self._queue_render(
                source,
                key,
                priority=int(ImageWorkPriority.VIEWER_CURRENT),
            )

    def _pending_display_is_ready(self, pending: _PendingDisplay) -> bool:
        return all(
            key is None
            or key in self._render_cache
            or key in pending.failed_keys
            for key in pending.keys
        )

    def _commit_pending_display(self) -> None:
        pending = self._pending_display
        if (
            pending is None
            or pending.generation != self._render_generation
            or pending.request_generation != self._display_request_generation
            or not self._pending_display_is_ready(pending)
        ):
            return
        self._pending_display = None
        images = tuple(
            (
                replace(
                    image,
                    pixmap=None,
                    qimage=None,
                    error=pending.failed_keys[key],
                    loading=False,
                    display_prepared=False,
                )
                if key is not None and key in pending.failed_keys
                else image
            )
            for image, key in zip(pending.images, pending.keys)
        )
        self._commit_display(
            pending.spread,
            images,
            frame_token=pending.frame_token,
        )

    def _commit_display(
        self,
        spread: DisplaySpread,
        images: tuple[ViewerImage, ...],
        *,
        frame_token: object | None = None,
    ) -> None:
        self._spread = spread
        self._images = list(images)
        self._discard_stale_cached_generations()
        self._enforce_render_cache_limit()
        self.update()
        self._emit_frame_committed(
            spread,
            images,
            frame_token=frame_token,
        )
        self.displayCommitted.emit(
            tuple(image.image_id for image in images)
        )

    def _emit_frame_committed(
        self,
        spread: DisplaySpread,
        images: tuple[ViewerImage, ...],
        *,
        frame_token: object | None,
    ) -> None:
        self._presentation_frame_serial += 1
        self.frameCommitted.emit(
            ViewerFrameCommit(
                frame_token=frame_token,
                widget_frame_serial=self._presentation_frame_serial,
                spread_identity=tuple(
                    (slot.page_index, slot.image_id)
                    for slot in spread.slots
                ),
                page_indexes=tuple(
                    slot.page_index for slot in spread.slots
                ),
                image_ids=tuple(image.image_id for image in images),
                failed_page_indexes=tuple(
                    sorted(
                        {
                            image.page_index
                            for image in images
                            if image.error
                        }
                    )
                ),
            )
        )

    def _display_pixmap(self, image: ViewerImage) -> QPixmap | None:
        if image.pixmap is None:
            return None
        if (
            self.rotation_angle == 0
            or image.pre_rotated
            or image.display_prepared
        ):
            return image.pixmap
        transform = QTransform().rotate(self.rotation_angle)
        return image.pixmap.transformed(transform, Qt.TransformationMode.SmoothTransformation)

    def _queue_render(
        self,
        source: QImage,
        key: ViewerRenderKey,
        *,
        priority: int = 0,
        allow_priority_decrease: bool = False,
    ) -> None:
        # Book runtimes own normal display artifacts in direct mode.  The
        # magnifier is a separate NivisViewer UX projection over the committed
        # source QImage, so it still needs one cropped render job.  Blocking it
        # here made the ZIP magnifier wait for full resolution and then never
        # produce a lens frame.
        if self._direct_display_mode and key.purpose != "magnifier":
            return
        if self._render_pending.get(key) == self._render_generation:
            previous_priority = self._render_priorities.get(key, priority)
            task = self._render_task_by_key.get(key)
            if (
                priority == previous_priority
                or (
                    priority < previous_priority
                    and not allow_priority_decrease
                )
                or task is None
                or not self._try_take_render_task(task)
            ):
                return
            self._discard_render_task(task)
        self._render_pending[key] = self._render_generation
        self._render_priorities[key] = int(priority)
        task = ViewerRenderTask(source, key, self._render_generation)
        self._render_tasks.add(task)
        self._render_task_by_key[key] = task
        task.signals.completed.connect(
            lambda result, owned=task: self._on_render_completed(result, owned)
        )
        self._start_render_task(task, int(priority))

    def _start_render_task(
        self,
        task: ViewerRenderTask,
        priority: int,
    ) -> None:
        if self._direct_display_mode and task.key.purpose == "magnifier":
            # A lens crop is an optional projection of an already committed
            # source.  Keep it off the single book-runtime Viewer lane so a
            # non-interruptible Pillow/Lanczos crop cannot delay the next page.
            self._local_render_tasks.add(task)
            self._render_pool.start(task, int(priority))
        elif self._render_coordinator is not None:
            self._render_coordinator.start_viewer(task, int(priority))
        else:
            self._local_render_tasks.add(task)
            self._render_pool.start(task, int(priority))

    def _try_take_render_task(self, task: ViewerRenderTask) -> bool:
        if task not in self._local_render_tasks:
            if self._render_coordinator is None:
                return False
            return self._render_coordinator.try_take_viewer(task)
        try:
            return self._render_pool.tryTake(task)
        except RuntimeError:
            return False

    def _discard_render_task(self, task: ViewerRenderTask) -> None:
        self._render_tasks.discard(task)
        self._local_render_tasks.discard(task)
        key = getattr(task, "key", None)
        if key is not None and self._render_task_by_key.get(key) is task:
            self._render_task_by_key.pop(key, None)
            self._render_pending.pop(key, None)
            self._render_priorities.pop(key, None)

    def _on_render_completed(
        self,
        result: ViewerRenderResult,
        task: ViewerRenderTask,
    ) -> None:
        self._discard_render_task(task)
        if self._render_pending.get(result.key) == result.generation:
            self._render_pending.pop(result.key, None)
        if (
            result.generation != self._render_generation
        ):
            return
        pending = self._pending_display
        pending_accepts = (
            pending is not None
            and pending.generation == result.generation
            and result.key in pending.keys
        )
        prepared_accepts = tuple(
            request
            for request in self._prepared_requests.values()
            if result.key in request.key.render_keys
        )
        if result.image is None or result.error is not None:
            if (
                result.key.purpose == "magnifier"
                and result.key == self._magnifier_key
            ):
                self.cancel_magnifier()
                return
            if pending_accepts:
                pending.failed_keys[result.key] = (
                    result.error or "画像の表示準備に失敗しました。"
                )
                self._commit_pending_display()
            for request in prepared_accepts:
                request.failed_keys.add(result.key)
                self._prepared_requests.pop(request.key, None)
            if result.key.purpose == "viewer":
                self.renderWorkFinished.emit(result.key, False)
            return
        if result.key.purpose == "magnifier":
            if result.key != self._magnifier_key:
                return
            pixmap = QPixmap.fromImage(result.image)
            pixmap.setDevicePixelRatio(
                max(1.0, result.key.device_pixel_ratio_milli / 1000.0)
            )
            self._magnifier_pixmap = pixmap
            self.magnifier_selecting = False
            self.magnifier_active = True
            self._magnifier_waiting_for_pdf = False
            self.update()
            return

        if not pending_accepts and not prepared_accepts:
            return
        pixmap = QPixmap.fromImage(result.image)
        pixmap.setDevicePixelRatio(
            max(1.0, result.key.device_pixel_ratio_milli / 1000.0)
        )
        self._render_cache[result.key] = pixmap
        self._render_cache.move_to_end(result.key)
        self._last_rendered_by_image[result.key.image_id] = pixmap
        for request in prepared_accepts:
            if self._unit_render_keys_ready(request.key.render_keys):
                self._store_prepared_unit(request)
                self._prepared_requests.pop(request.key, None)
        if pending_accepts:
            self._commit_pending_display()
        self._enforce_render_cache_limit()
        self.renderCacheChanged.emit()
        self.renderWorkFinished.emit(result.key, True)

    def _unit_render_keys_ready(
        self,
        keys: tuple[ViewerRenderKey | None, ...],
    ) -> bool:
        return all(key is None or key in self._render_cache for key in keys)

    def _current_render_keys(self) -> set[ViewerRenderKey]:
        layout = self._layout_for_current_images()
        return {
            key
            for image, rect in zip(self._images, layout.rects)
            if (key := self._viewer_render_key(image, rect)) is not None
        }

    def _discard_stale_cached_generations(self) -> None:
        stale = tuple(
            key
            for key in self._render_cache
            if (
                not key.source_sized
                and key.layout_generation != self._render_generation
            )
        )
        for key in stale:
            pixmap = self._render_cache.pop(key)
            if self._last_rendered_by_image.get(key.image_id) is pixmap:
                self._last_rendered_by_image.pop(key.image_id, None)

    def _enforce_render_cache_limit(
        self,
        protected_keys: Iterable[ViewerRenderKey] = (),
    ) -> None:
        protected = set(protected_keys) | self._current_render_keys()
        pending = self._pending_display
        if pending is not None:
            protected.update(
                key for key in pending.keys if key is not None
            )
        for unit_key, request in self._prepared_requests.items():
            if unit_key not in self._protected_prepared_units:
                continue
            # A spread becomes reusable only after every page is ready. Keep
            # completed halves of current/near in-flight units until that
            # atomic request can either finish or be superseded.
            protected.update(
                key for key in request.key.render_keys if key is not None
            )
        for unit_key in self._protected_prepared_units:
            protected.update(
                key for key in unit_key.render_keys if key is not None
            )

        def cache_bytes() -> int:
            return sum(
                pixmap.width() * pixmap.height() * 4
                for pixmap in self._render_cache.values()
            )

        planned_render_keys = {
            key
            for unit_key in (
                set(self._prepared_units)
                | set(self._prepared_requests)
            )
            for key in unit_key.render_keys
            if key is not None
        }
        active_count_limit = max(
            self._render_cache_limit,
            len(planned_render_keys),
        )
        while (
            len(self._render_cache) > active_count_limit
            or cache_bytes() > self._render_cache_byte_limit
        ):
            far_unit = next(
                (
                    key
                    for key in sorted(
                        self._prepared_units,
                        key=lambda value: self._prepared_unit_priorities.get(
                            value,
                            99,
                        ),
                        reverse=True,
                    )
                    if key not in self._protected_prepared_units
                ),
                None,
            )
            removable_keys = (
                [
                    key
                    for key in far_unit.render_keys
                    if (
                        key is not None
                        and key in self._render_cache
                        and key not in protected
                    )
                ]
                if far_unit is not None
                else []
            )
            if far_unit is not None:
                # A spread is useful only while every page is ready.  Evict
                # the whole far display unit together instead of leaving one
                # orphaned half in the pixmap cache.
                self._prepared_units.pop(far_unit, None)
            if not removable_keys:
                far_request = next(
                    (
                        key
                        for key in sorted(
                            self._prepared_requests,
                            key=lambda value: self._prepared_unit_priorities.get(
                                value,
                                99,
                            ),
                            reverse=True,
                        )
                        if key not in self._protected_prepared_units
                        and {
                            render_key
                            for render_key in key.render_keys
                            if render_key is not None
                        }.isdisjoint(protected)
                    ),
                    None,
                )
                if far_request is not None:
                    # Do not leave an incomplete far spread waiting forever
                    # after one cached half is evicted. Drop its queued work
                    # and any completed halves as one unit; a later plan may
                    # request it again when it is near the current page.
                    self._prepared_requests.pop(far_request, None)
                    self._prepared_unit_priorities.pop(
                        far_request,
                        None,
                    )
                    request_keys = {
                        key
                        for key in far_request.render_keys
                        if key is not None
                    }
                    retained_keys = {
                        key
                        for unit_key in (
                            set(self._prepared_units)
                            | set(self._prepared_requests)
                        )
                        for key in unit_key.render_keys
                        if key is not None
                    }
                    cancel_only_keys = request_keys - retained_keys
                    for task in tuple(self._render_tasks):
                        if (
                            task.key in cancel_only_keys
                            and self._try_take_render_task(task)
                        ):
                            self._discard_render_task(task)
                    for key in cancel_only_keys:
                        pixmap = self._render_cache.pop(key, None)
                        if (
                            pixmap is not None
                            and self._last_rendered_by_image.get(
                                key.image_id
                            ) is pixmap
                        ):
                            self._last_rendered_by_image.pop(
                                key.image_id,
                                None,
                            )
                    continue
                removable = next(
                    (
                        key
                        for key in self._render_cache
                        if key not in protected
                    ),
                    None,
                )
                removable_keys = [] if removable is None else [removable]
            if not removable_keys:
                break
            for key in removable_keys:
                pixmap = self._render_cache.pop(key)
                if self._last_rendered_by_image.get(key.image_id) is pixmap:
                    self._last_rendered_by_image.pop(key.image_id, None)

    def _invalidate_render_requests(self, *, clear_cache: bool) -> None:
        self._render_generation += 1
        self._pending_display = None
        self._display_request_generation += 1
        self._prepared_requests.clear()
        self._prepared_units.clear()
        self._prepared_unit_priorities.clear()
        self._protected_prepared_units.clear()
        self._deferred_render_target = None
        self._render_pending.clear()
        self._render_priorities.clear()
        self._render_task_by_key.clear()
        for task in tuple(self._render_tasks):
            if self._try_take_render_task(task):
                self._discard_render_task(task)
        if clear_cache:
            self._render_cache.clear()
            self._last_rendered_by_image.clear()

    def wait_for_rendering(self, msecs: int = 5000) -> bool:
        if self._render_coordinator is not None:
            return self._wait_for_owned_render_tasks(msecs)
        return self._render_pool.waitForDone(max(0, int(msecs)))

    def _wait_for_owned_render_tasks(self, msecs: int) -> bool:
        deadline = monotonic() + max(0, int(msecs)) / 1000
        for task in tuple(self._render_tasks):
            remaining = max(0.0, deadline - monotonic())
            if not task.finished.wait(remaining):
                return False
        return True

    def shutdown_rendering(self, msecs: int = 5000) -> bool:
        self._resize_render_timer.stop()
        self.cancel_magnifier()
        self._invalidate_render_requests(clear_cache=True)
        if self._render_coordinator is not None:
            return self._wait_for_owned_render_tasks(msecs)
        self._render_pool.clear()
        return self._render_pool.waitForDone(max(0, int(msecs)))

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        self.shutdown_rendering()
        super().closeEvent(event)

    def _draw_placeholder(self, painter: QPainter, rect: QRect, image: ViewerImage) -> None:
        painter.fillRect(rect, QColor("#151515"))
        if image.error:
            painter.setPen(QColor("#ff6b6b"))
            text = f"読み込みエラー\nPage {image.page_index + 1}"
        else:
            painter.setPen(QColor("#888888"))
            text = f"読み込み中...\nPage {image.page_index + 1}"
        inner = rect.adjusted(16, 16, -16, -16)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.drawText(inner, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

    def _begin_magnifier_selection(self, position: QPoint) -> bool:
        hit = next(
            (
                (rect, image)
                for rect, image, _pixmap in reversed(self._last_image_layout)
                if rect.contains(position) and image.qimage is not None
            ),
            None,
        )
        if hit is None:
            return False
        rect, image = hit
        self.magnifier_selecting = True
        self.magnifier_active = False
        self.magnifier_source_page = image.page_index
        self._magnifier_source_image_id = image.image_id
        self.magnifier_previous_view_state = {
            "fit_mode": self.fit_mode,
            "manual_zoom": self.manual_zoom,
            "pan": QPoint(self._pan),
        }
        self.magnifier_request_generation += 1
        self._magnifier_pixmap = None
        self._magnifier_key = None
        self._magnifier_waiting_for_pdf = False
        self._update_magnifier_selection(position, hit=(rect, image))
        return True

    def _update_magnifier_selection(
        self,
        position: QPoint,
        *,
        hit: tuple[QRect, ViewerImage] | None = None,
    ) -> None:
        if not (self.magnifier_selecting or self.magnifier_active):
            return
        if hit is None:
            hit = next(
                (
                    (rect, image)
                    for rect, image, _pixmap in self._last_image_layout
                    if image.image_id == self._magnifier_source_image_id
                ),
                None,
            )
        if hit is None:
            self.cancel_magnifier()
            return
        image_rect, image = hit
        source_size = self._rotated_source_size(image)
        if source_size.isEmpty():
            self.cancel_magnifier()
            return

        screen_scale_x = image_rect.width() / max(1, source_size.width())
        screen_scale_y = image_rect.height() / max(1, source_size.height())
        crop_width = min(
            source_size.width(),
            max(
                1.0,
                self.width()
                / max(0.0001, screen_scale_x * self.magnifier_zoom),
            ),
        )
        crop_height = min(
            source_size.height(),
            max(
                1.0,
                self.height()
                / max(0.0001, screen_scale_y * self.magnifier_zoom),
            ),
        )
        target_aspect = self.width() / max(1, self.height())
        if crop_width / max(1.0, crop_height) > target_aspect:
            crop_width = crop_height * target_aspect
        else:
            crop_height = crop_width / max(0.0001, target_aspect)

        relative_x = (
            position.x() - image_rect.left()
        ) / max(1, image_rect.width())
        relative_y = (
            position.y() - image_rect.top()
        ) / max(1, image_rect.height())
        center_x = min(1.0, max(0.0, relative_x)) * source_size.width()
        center_y = min(1.0, max(0.0, relative_y)) * source_size.height()
        left = min(
            max(0.0, center_x - crop_width / 2),
            max(0.0, source_size.width() - crop_width),
        )
        top = min(
            max(0.0, center_y - crop_height / 2),
            max(0.0, source_size.height() - crop_height),
        )
        source_rect = QRectF(left, top, crop_width, crop_height)
        self.magnifier_source_rect = source_rect
        self._magnifier_source_normalized = QRectF(
            source_rect.left() / source_size.width(),
            source_rect.top() / source_size.height(),
            source_rect.width() / source_size.width(),
            source_rect.height() / source_size.height(),
        )
        self._magnifier_selection_rect = QRect(
            round(image_rect.left() + source_rect.left() * screen_scale_x),
            round(image_rect.top() + source_rect.top() * screen_scale_y),
            max(1, round(source_rect.width() * screen_scale_x)),
            max(1, round(source_rect.height() * screen_scale_y)),
        ).intersected(image_rect)
        self.update()

    def _rotated_source_size(self, image: ViewerImage) -> QSize:
        if image.qimage is None:
            return QSize()
        size = (
            QSize(image.split_range[2], image.split_range[3])
            if image.split_range is not None
            else image.qimage.size()
        )
        if not image.pre_rotated and self.rotation_angle in {90, 270}:
            return QSize(size.height(), size.width())
        return size

    def _magnifier_image(self) -> ViewerImage | None:
        return next(
            (
                image
                for image in self._images
                if image.page_index == self.magnifier_source_page
                and image.image_id == self._magnifier_source_image_id
            ),
            None,
        )

    def _request_magnifier_render(self, *, allow_pdf_request: bool = True) -> None:
        image = self._magnifier_image()
        source_rect = self.magnifier_source_rect
        if image is None or image.qimage is None or source_rect is None:
            self.cancel_magnifier()
            return

        source_size = self._rotated_source_size(image)
        left = max(0, min(source_size.width() - 1, math.floor(source_rect.left())))
        top = max(0, min(source_size.height() - 1, math.floor(source_rect.top())))
        right = max(
            left + 1,
            min(source_size.width(), math.ceil(source_rect.right())),
        )
        bottom = max(
            top + 1,
            min(source_size.height(), math.ceil(source_rect.bottom())),
        )
        dpr = max(1.0, float(self.devicePixelRatioF()))
        target_width = max(1, round(self.width() * dpr))
        target_height = max(1, round(self.height() * dpr))

        if allow_pdf_request and image.source_is_preview:
            self._magnifier_waiting_for_pdf = True
            self._magnifier_pdf_source_key = int(image.qimage.cacheKey())
            self.magnifierSourceResolutionRequested.emit(
                image.page_index,
                QSize(target_width, target_height),
            )
            self.update()
            return

        if allow_pdf_request and image.rendered_size is not None:
            required_width = max(
                source_size.width(),
                math.ceil(target_width * source_size.width() / (right - left)),
            )
            required_height = max(
                source_size.height(),
                math.ceil(target_height * source_size.height() / (bottom - top)),
            )
            if (
                required_width > source_size.width()
                or required_height > source_size.height()
            ):
                self._magnifier_waiting_for_pdf = True
                self._magnifier_pdf_source_key = int(image.qimage.cacheKey())
                self.magnifierPdfResolutionRequested.emit(
                    image.page_index,
                    QSize(required_width, required_height),
                )
                self.update()
                return

        self.magnifier_request_generation += 1
        for task in tuple(self._render_tasks):
            if task.key.purpose != "magnifier":
                continue
            if self._try_take_render_task(task):
                self._discard_render_task(task)

        rotation = 0 if image.pre_rotated else self.rotation_angle
        key = ViewerRenderKey(
            image_id=image.image_id,
            source_cache_key=int(image.qimage.cacheKey()),
            target_width=target_width,
            target_height=target_height,
            mode=self.magnifier_resampling_mode,
            rotation=rotation,
            device_pixel_ratio_milli=round(dpr * 1000),
            crop=(left, top, right, bottom),
            purpose="magnifier",
            request_generation=self.magnifier_request_generation,
            split_range=image.split_range,
        )
        self._magnifier_key = key
        self._queue_render(
            image.qimage,
            key,
            priority=int(ImageWorkPriority.VIEWER_INTERACTIVE_RERENDER),
        )

    def resume_magnifier_after_source_render(self) -> None:
        self._resume_magnifier_after_pdf_render()

    def _resume_magnifier_after_pdf_render(self) -> None:
        image = self._magnifier_image()
        normalized = self._magnifier_source_normalized
        if (
            image is None
            or image.qimage is None
            or normalized is None
            or int(image.qimage.cacheKey()) == self._magnifier_pdf_source_key
        ):
            return
        source_size = self._rotated_source_size(image)
        self.magnifier_source_rect = QRectF(
            normalized.left() * source_size.width(),
            normalized.top() * source_size.height(),
            normalized.width() * source_size.width(),
            normalized.height() * source_size.height(),
        )
        self._magnifier_waiting_for_pdf = False
        self._request_magnifier_render(allow_pdf_request=False)

    def _draw_magnifier(self, painter: QPainter) -> None:
        if self.magnifier_active and self._magnifier_pixmap is not None:
            painter.drawPixmap(self.rect(), self._magnifier_pixmap)
            return
        if self.magnifier_selecting and self._magnifier_selection_rect is not None:
            painter.fillRect(
                self._magnifier_selection_rect,
                QColor(80, 170, 255, 48),
            )
            pen = QPen(QColor("#f0f7ff"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(self._magnifier_selection_rect.adjusted(0, 0, -1, -1))

    def _draw_gesture_trail(self, painter: QPainter) -> None:
        if (
            not self.mouse_gesture_show_trail
            or not self.mouse_gestures_enabled
            or not self._right_button_down
            or not self._gesture_recognizer.pattern
            or len(self._gesture_trail) < 2
        ):
            return
        pen = QPen(QColor(120, 205, 255, 150))
        pen.setWidthF(max(2.0, 3.0 * self.devicePixelRatioF()))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for start, end in zip(self._gesture_trail, self._gesture_trail[1:]):
            painter.drawLine(start, end)
