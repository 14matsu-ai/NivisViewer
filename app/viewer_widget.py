from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
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

from .mouse_gesture import MouseGestureRecognizer
from .page_model import DisplaySpread
from .viewer_canvas_pointer import (
    ViewerCanvasPointerController,
    ViewerCanvasPointerState,
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


@dataclass(frozen=True)
class SpreadLayout:
    scale: float
    content_size: QSize
    rects: tuple[QRect, ...]
    effective_gap: int


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
    """Calculate a shared-scale spread layout without requiring a QWidget."""
    if not image_sizes:
        return SpreadLayout(1.0, QSize(0, 0), (), 0)

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

    def __init__(self, parent: QWidget | None = None) -> None:
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
        self.magnifier_enabled = False
        self.magnifier_zoom = 2.0
        self.magnifier_size = 220

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
        self.gap = max(0, int(gap))
        self.update()

    def set_join_spread_pages(self, enabled: bool) -> None:
        self.join_spread_pages = bool(enabled)
        self.update()

    def set_smooth_scaling(self, enabled: bool) -> None:
        self.smooth_scaling = enabled
        self.update()

    def set_horizontal_alignment(self, alignment: str) -> None:
        if alignment not in {"left", "center", "right"}:
            alignment = "center"
        self.horizontal_alignment = alignment
        self.update()

    def set_magnifier_enabled(self, enabled: bool) -> None:
        self.magnifier_enabled = enabled
        self.update()

    def set_magnifier_options(self, *, zoom: float | None = None, size: int | None = None) -> None:
        if zoom is not None:
            self.magnifier_zoom = min(8.0, max(1.1, zoom))
        if size is not None:
            self.magnifier_size = min(600, max(80, int(size)))
        self.update()

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
        self.rotation_angle = angle % 360
        self._pan = QPoint(0, 0)
        self.update()

    def set_fit_mode(self, fit_mode: str) -> None:
        self.fit_mode = fit_mode
        if fit_mode in {"fit_window", "fit_no_upscale", "fit_width", "fit_height"}:
            self._pan = QPoint(0, 0)
        self.update()

    def set_manual_zoom(self, zoom: float) -> None:
        self.manual_zoom = min(8.0, max(0.05, zoom))
        self.fit_mode = "manual_zoom"
        self.zoomChanged.emit(self.manual_zoom)
        self.update()

    def reset_zoom(self) -> None:
        self.fit_mode = "fit_window"
        self.manual_zoom = 1.0
        self._pan = QPoint(0, 0)
        self.update()

    def scroll_forward(self) -> bool:
        return self._scroll_vertical(1)

    def scroll_backward(self) -> bool:
        return self._scroll_vertical(-1)

    def set_pages(self, spread: DisplaySpread, pages: list[ViewerImage]) -> None:
        same_display_unit = (
            spread.start_index == self._spread.start_index
            and tuple(slot.page_index for slot in spread.slots)
            == tuple(slot.page_index for slot in self._spread.slots)
        )
        self._spread = spread
        self._images = pages
        if not same_display_unit:
            self._pan = QPoint(0, 0)
        self.update()

    @staticmethod
    def from_qimage(
        page_index: int,
        image_id: str,
        qimage: QImage,
        original_size: tuple[int, int],
        rendered_size: tuple[int, int] | None = None,
        pre_rotated: bool = False,
    ) -> ViewerImage:
        return ViewerImage(
            page_index=page_index,
            image_id=image_id,
            pixmap=QPixmap.fromImage(qimage),
            original_size=original_size,
            rendered_size=rendered_size,
            pre_rotated=pre_rotated,
        )

    @staticmethod
    def loading_page(page_index: int, image_id: str) -> ViewerImage:
        return ViewerImage(page_index=page_index, image_id=image_id, pixmap=None, original_size=None, loading=True)

    @staticmethod
    def error_page(page_index: int, image_id: str, error: str) -> ViewerImage:
        return ViewerImage(page_index=page_index, image_id=image_id, pixmap=None, original_size=None, error=error)

    def clear(self) -> None:
        self._spread = DisplaySpread(0, tuple(), True)
        self._images = []
        self._last_draw_layout.clear()
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
        painted_image_ids: list[str] = []
        for image, rect in zip(self._images, layout.rects):
            pixmap = self._display_pixmap(image)
            if pixmap is not None:
                painter.drawPixmap(rect, pixmap)
                self._last_draw_layout.append((rect, pixmap))
                painted_image_ids.append(image.image_id)
            else:
                self._draw_placeholder(painter, rect, image)
        self._draw_magnifier(painter)
        self._draw_gesture_trail(painter)
        if painted_image_ids:
            self.contentPainted.emit(tuple(painted_image_ids))

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        if self.fit_mode in {"fit_window", "fit_no_upscale", "fit_width", "fit_height"}:
            self._pan = QPoint(0, 0)
        super().resizeEvent(event)
        self.viewportChanged.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            factor = 1.15 if delta > 0 else 1 / 1.15
            base = self._scale_for_current_mode() if self.fit_mode != "manual_zoom" else self.manual_zoom
            self.set_manual_zoom(base * factor)
            event.accept()
            return

        if event.angleDelta().y() < 0:
            self.nextRequested.emit()
        elif event.angleDelta().y() > 0:
            self.previousRequested.emit()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._show_cursor_temporarily()
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
        if self.magnifier_enabled:
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._mouse_pos = None
        if self.magnifier_enabled:
            self.update()
        super().leaveEvent(event)

    def _show_cursor_temporarily(self) -> None:
        return

    def _hide_cursor(self) -> None:
        return

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
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
        sizes = [
            (self._base_size(image).width(), self._base_size(image).height())
            for image in self._images
        ]
        return calculate_spread_layout(
            sizes,
            (self.width(), self.height()),
            fit_mode=self.fit_mode,
            manual_zoom=self.manual_zoom,
            gap=self.gap,
            join_spread_pages=self.join_spread_pages,
            spread_is_single=self._spread.is_single,
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

    def _display_pixmap(self, image: ViewerImage) -> QPixmap | None:
        if image.pixmap is None:
            return None
        if self.rotation_angle == 0 or image.pre_rotated:
            return image.pixmap
        transform = QTransform().rotate(self.rotation_angle)
        return image.pixmap.transformed(transform, Qt.TransformationMode.SmoothTransformation)

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

    def _draw_magnifier(self, painter: QPainter) -> None:
        if not self.magnifier_enabled or self._mouse_pos is None:
            return
        for image_rect, pixmap in reversed(self._last_draw_layout):
            if not image_rect.contains(self._mouse_pos):
                continue

            lens_size = self.magnifier_size
            zoom = self.magnifier_zoom
            source_w = max(1, round(lens_size / zoom))
            source_h = max(1, round(lens_size / zoom))
            rel_x = (self._mouse_pos.x() - image_rect.x()) / max(1, image_rect.width())
            rel_y = (self._mouse_pos.y() - image_rect.y()) / max(1, image_rect.height())
            src_x = round(rel_x * pixmap.width() - source_w / 2)
            src_y = round(rel_y * pixmap.height() - source_h / 2)
            src_x = max(0, min(src_x, max(0, pixmap.width() - source_w)))
            src_y = max(0, min(src_y, max(0, pixmap.height() - source_h)))

            target_x = self._mouse_pos.x() - lens_size // 2
            target_y = self._mouse_pos.y() - lens_size // 2
            target_x = max(8, min(target_x, max(8, self.width() - lens_size - 8)))
            target_y = max(8, min(target_y, max(8, self.height() - lens_size - 8)))
            target = QRect(target_x, target_y, lens_size, lens_size)
            source = QRect(src_x, src_y, source_w, source_h)

            painter.fillRect(target.adjusted(-3, -3, 3, 3), QColor("#111111"))
            painter.drawPixmap(target, pixmap, source)
            painter.setPen(QColor("#eeeeee"))
            painter.drawRect(target.adjusted(0, 0, -1, -1))
            return

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
