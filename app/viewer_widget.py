from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QMouseEvent, QPainter, QPixmap, QResizeEvent, QTransform, QWheelEvent
from PySide6.QtWidgets import QWidget

from .page_model import DisplaySpread


@dataclass
class ViewerImage:
    page_index: int
    image_id: str
    pixmap: QPixmap | None
    original_size: tuple[int, int] | None
    error: str | None = None
    loading: bool = False


class ViewerWidget(QWidget):
    nextRequested = Signal()
    previousRequested = Signal()
    zoomChanged = Signal(float)
    fullscreenToggleRequested = Signal()
    leftSideClicked = Signal()
    rightSideClicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self.background_color = QColor("#000000")
        self.fit_mode = "fit_window"
        self.manual_zoom = 1.0
        self.gap = 24
        self.rotation_angle = 0
        self.smooth_scaling = True
        self.horizontal_alignment = "center"
        self.magnifier_enabled = False
        self.magnifier_zoom = 2.0
        self.magnifier_size = 220
        self.auto_hide_cursor = False
        self._cursor_hidden = False
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.timeout.connect(self._hide_cursor)

        self._spread = DisplaySpread(0, tuple(), True)
        self._images: list[ViewerImage] = []
        self._pan = QPoint(0, 0)
        self._drag_start: QPoint | None = None
        self._press_position: QPoint | None = None
        self._drag_origin = QPoint(0, 0)
        self._mouse_pos: QPoint | None = None
        self._last_draw_layout: list[tuple[QRect, QPixmap]] = []

    def set_background_color(self, color: str) -> None:
        self.background_color = QColor(color)
        self.update()

    def set_gap(self, gap: int) -> None:
        self.gap = max(0, int(gap))
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
        self.auto_hide_cursor = enabled
        if enabled:
            self._show_cursor_temporarily()
        else:
            self._cursor_timer.stop()
            self.unsetCursor()
            self._cursor_hidden = False

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
        self._spread = spread
        self._images = pages
        self._pan = QPoint(0, 0)
        self.update()

    @staticmethod
    def from_qimage(page_index: int, image_id: str, qimage: QImage, original_size: tuple[int, int]) -> ViewerImage:
        return ViewerImage(
            page_index=page_index,
            image_id=image_id,
            pixmap=QPixmap.fromImage(qimage),
            original_size=original_size,
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
        self._pan = QPoint(0, 0)
        self.update()

    def current_resolution_text(self) -> str:
        if not self._images:
            return ""
        parts = []
        for image in self._images:
            if image.original_size is not None:
                parts.append(f"{image.original_size[0]}x{image.original_size[1]}")
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
            return

        scale = self._scale_for_current_mode()
        content_size = self._content_size(scale)
        if self.horizontal_alignment == "left":
            origin_x = self._pan.x()
        elif self.horizontal_alignment == "right":
            origin_x = self.width() - content_size.width() + self._pan.x()
        else:
            origin_x = (self.width() - content_size.width()) // 2 + self._pan.x()
        origin_y = (self.height() - content_size.height()) // 2 + self._pan.y()

        x = origin_x
        self._last_draw_layout = []
        for image in self._images:
            pixmap = self._display_pixmap(image)
            base = pixmap.size() if pixmap is not None else self._base_size(image)
            target = QSize(max(1, round(base.width() * scale)), max(1, round(base.height() * scale)))
            rect = QRect(x, origin_y + (content_size.height() - target.height()) // 2, target.width(), target.height())
            if pixmap is not None:
                painter.drawPixmap(rect, pixmap)
                self._last_draw_layout.append((rect, pixmap))
            else:
                self._draw_placeholder(painter, rect, image)
            x += target.width() + self.gap
        self._draw_magnifier(painter)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        if self.fit_mode in {"fit_window", "fit_no_upscale", "fit_width", "fit_height"}:
            self._pan = QPoint(0, 0)
        super().resizeEvent(event)

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
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton and self._can_pan():
            self._drag_start = event.position().toPoint()
            self._drag_origin = QPoint(self._pan)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._show_cursor_temporarily()
        self._mouse_pos = event.position().toPoint()
        if self._drag_start is not None:
            delta = event.position().toPoint() - self._drag_start
            self._pan = self._drag_origin + delta
            self.update()
            event.accept()
            return
        if self.magnifier_enabled:
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._mouse_pos = None
        self._cursor_timer.stop()
        self.unsetCursor()
        self._cursor_hidden = False
        if self.magnifier_enabled:
            self.update()
        super().leaveEvent(event)

    def _show_cursor_temporarily(self) -> None:
        if not self.auto_hide_cursor:
            return
        if self._cursor_hidden:
            self.unsetCursor()
            self._cursor_hidden = False
        self._cursor_timer.start(1500)

    def _hide_cursor(self) -> None:
        if not self.auto_hide_cursor:
            return
        self.setCursor(QCursor(Qt.CursorShape.BlankCursor))
        self._cursor_hidden = True

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            self._drag_start = None
            self.unsetCursor()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._press_position is not None:
            release_position = event.position().toPoint()
            moved = (release_position - self._press_position).manhattanLength()
            self._press_position = None
            if moved < 6:
                if release_position.x() >= self.width() / 2:
                    self.rightSideClicked.emit()
                else:
                    self.leftSideClicked.emit()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.fullscreenToggleRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _can_pan(self) -> bool:
        scale = self._scale_for_current_mode()
        content_size = self._content_size(scale)
        return content_size.width() > self.width() or content_size.height() > self.height()

    def _scroll_vertical(self, direction: int) -> bool:
        if not self._images:
            return False
        scale = self._scale_for_current_mode()
        content_size = self._content_size(scale)
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
        if not self._images:
            return 1.0
        if self.fit_mode == "actual_size":
            return 1.0
        if self.fit_mode == "manual_zoom":
            return self.manual_zoom
        if self.fit_mode == "fit_no_upscale":
            return min(1.0, self._fit_window_scale())
        if self.fit_mode == "fit_width":
            return self._fit_width_scale()
        if self.fit_mode == "fit_height":
            return self._fit_height_scale()
        return self._fit_window_scale()

    def _fit_window_scale(self) -> float:
        return min(self._fit_width_scale(), self._fit_height_scale())

    def _fit_width_scale(self) -> float:
        content_width = sum(self._base_size(image).width() for image in self._images)
        if len(self._images) > 1:
            content_width += self.gap * (len(self._images) - 1)
        if content_width <= 0:
            return 1.0
        return max(0.01, self.width() / content_width)

    def _fit_height_scale(self) -> float:
        content_height = max(self._base_size(image).height() for image in self._images)
        if content_height <= 0:
            return 1.0
        return max(0.01, self.height() / content_height)

    def _content_size(self, scale: float) -> QSize:
        if not self._images:
            return QSize(0, 0)

        width = sum(max(1, round(self._base_size(image).width() * scale)) for image in self._images)
        if len(self._images) > 1:
            width += self.gap * (len(self._images) - 1)
        height = max(max(1, round(self._base_size(image).height() * scale)) for image in self._images)
        return QSize(width, height)

    def _base_size(self, image: ViewerImage) -> QSize:
        if image.pixmap is not None:
            size = image.pixmap.size()
        else:
            size = QSize(360, 520)
        if self.rotation_angle in (90, 270):
            return QSize(size.height(), size.width())
        return size

    def _display_pixmap(self, image: ViewerImage) -> QPixmap | None:
        if image.pixmap is None:
            return None
        if self.rotation_angle == 0:
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
