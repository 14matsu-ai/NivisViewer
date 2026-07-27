from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QModelIndex, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QImage, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from .browser_grid_metrics import build_browser_grid_metrics
from .browser_sort import BrowserDisplayDensity
from .shell_icon_provider import ShellAssociatedIconProvider
from .thumbnail_render import (
    THUMBNAIL_SIZE_BUCKETS,
    frame_size_from_long_edge,
    quantize_thumbnail_size,
    snap_logical_rect_to_physical_pixels,
)


@dataclass(frozen=True)
class BrowserGridProfile:
    horizontal_margin: int
    vertical_margin: int
    spacing: int
    font_size: int
    title_lines: int


GRID_PROFILES = {
    BrowserDisplayDensity.EXTRA_COMPACT: BrowserGridProfile(4, 22, 0, 7, 1),
    BrowserDisplayDensity.COMPACT: BrowserGridProfile(20, 32, 2, 8, 1),
    BrowserDisplayDensity.STANDARD: BrowserGridProfile(44, 58, 6, 9, 2),
    BrowserDisplayDensity.COMFORTABLE: BrowserGridProfile(72, 88, 12, 10, 2),
    BrowserDisplayDensity.LARGE: BrowserGridProfile(104, 118, 16, 11, 2),
}

GRID_PRESET_THUMBNAIL_SIZES = {
    BrowserDisplayDensity.EXTRA_COMPACT: 96,
    BrowserDisplayDensity.COMPACT: 128,
    BrowserDisplayDensity.STANDARD: 180,
    BrowserDisplayDensity.COMFORTABLE: 240,
    BrowserDisplayDensity.LARGE: 320,
}


def browser_item_type_key(item: BrowserItem) -> str:
    if item.kind is BrowserItemKind.FOLDER:
        return "folder"
    if item.kind is BrowserItemKind.IMAGE:
        return "image"
    if item.kind is BrowserItemKind.PDF:
        return "pdf"
    suffix = item.path.suffix.casefold()
    if suffix in {".zip", ".cbz"}:
        return "zip"
    if suffix in {".rar", ".cbr"}:
        return "rar"
    if suffix in {".7z", ".cb7"}:
        return "7z"
    return "archive"


def type_badge_rect(thumbnail_rect: QRect, badge_size: int = 16) -> QRect:
    size = max(12, int(badge_size))
    return QRect(
        thumbnail_rect.left() + 4,
        thumbnail_rect.bottom() - size - 3,
        size,
        size,
    )


def thumbnail_rect_for_cell(
    cell_rect: QRect,
    thumbnail_size: int | QSize,
    cell_padding: int,
) -> QRect:
    if isinstance(thumbnail_size, QSize):
        size = QSize(
            max(1, thumbnail_size.width()),
            max(1, thumbnail_size.height()),
        )
    else:
        edge = max(1, int(thumbnail_size))
        size = QSize(edge, edge)
    return QRect(
        cell_rect.left() + (cell_rect.width() - size.width()) // 2,
        cell_rect.top() + max(0, int(cell_padding)),
        size.width(),
        size.height(),
    )


def elided_title_lines(
    metrics: QFontMetrics,
    text: str,
    width: int,
    maximum_lines: int,
) -> tuple[str, ...]:
    remaining = str(text).strip()
    if not remaining:
        return ("",)
    lines: list[str] = []
    for line_number in range(max(1, int(maximum_lines))):
        if line_number == maximum_lines - 1:
            lines.append(
                metrics.elidedText(
                    remaining,
                    Qt.TextElideMode.ElideRight,
                    max(1, width),
                )
            )
            break
        fit = 0
        for index in range(1, len(remaining) + 1):
            if metrics.horizontalAdvance(remaining[:index]) > width:
                break
            fit = index
        if fit >= len(remaining):
            lines.append(remaining)
            break
        if fit <= 0:
            lines.append(metrics.elidedText(remaining, Qt.TextElideMode.ElideRight, width))
            break
        split = max(remaining.rfind(" ", 0, fit + 1), remaining.rfind("_", 0, fit + 1))
        if split <= 0:
            split = fit
        lines.append(remaining[:split].rstrip())
        remaining = remaining[split:].lstrip(" _")
    return tuple(lines)


class BrowserItemDelegate(QStyledItemDelegate):
    def __init__(
        self,
        parent=None,
        *,
        thumbnail_size: int = 180,
        density: BrowserDisplayDensity = BrowserDisplayDensity.STANDARD,
        frame_ratio_id: str = "portrait_1_sqrt2",
        cell_padding: int = 0,
        filename_display: str = "one_line",
        filename_gap: int = 0,
        filename_padding_y: int = 0,
        item_spacing: int = 0,
        shell_icon_provider: ShellAssociatedIconProvider | None = None,
    ) -> None:
        super().__init__(parent)
        self.thumbnail_size = int(thumbnail_size)
        self.density = density
        self.frame_ratio_id = frame_ratio_id
        self.cell_padding = max(0, min(12, int(cell_padding)))
        self.filename_display = filename_display
        self.filename_gap = max(0, min(32, int(filename_gap)))
        self.filename_padding_y = max(0, min(16, int(filename_padding_y)))
        self.item_spacing = max(0, min(32, int(item_spacing)))
        self.shell_icon_provider = (
            shell_icon_provider or ShellAssociatedIconProvider()
        )

    @property
    def profile(self) -> BrowserGridProfile:
        return GRID_PROFILES[self.density]

    @property
    def cell_size(self) -> QSize:
        return self.grid_metrics.cell_size

    @property
    def frame_size(self) -> QSize:
        return frame_size_from_long_edge(
            self.thumbnail_size,
            self.frame_ratio_id,
        )

    @property
    def grid_metrics(self):
        font = QFont()
        font.setPointSize(self.profile.font_size)
        return build_browser_grid_metrics(
            thumbnail_size=self.thumbnail_size,
            frame_ratio_id=self.frame_ratio_id,
            font_height=QFontMetrics(font).height(),
            filename_display=self.filename_display,
            filename_gap=self.filename_gap,
            filename_padding_y=self.filename_padding_y,
            horizontal_margin=self.profile.horizontal_margin,
            cell_padding=self.cell_padding,
            item_spacing=self.item_spacing,
        )

    def configure(
        self,
        *,
        thumbnail_size: int,
        density: BrowserDisplayDensity,
        frame_ratio_id: str | None = None,
        cell_padding: int | None = None,
        filename_display: str | None = None,
        filename_gap: int | None = None,
        filename_padding_y: int | None = None,
        item_spacing: int | None = None,
    ) -> None:
        self.thumbnail_size = int(thumbnail_size)
        self.density = density
        if frame_ratio_id is not None:
            self.frame_ratio_id = frame_ratio_id
        if cell_padding is not None:
            self.cell_padding = max(0, min(12, int(cell_padding)))
        if filename_display is not None:
            self.filename_display = filename_display
        if filename_gap is not None:
            self.filename_gap = max(0, min(32, int(filename_gap)))
        if filename_padding_y is not None:
            self.filename_padding_y = max(0, min(16, int(filename_padding_y)))
        if item_spacing is not None:
            self.item_spacing = max(0, min(32, int(item_spacing)))

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        del option, index
        return self.cell_size

    @staticmethod
    def content_opacity(index: QModelIndex, item: BrowserItem) -> float:
        if bool(index.data(BrowserItemModel.CutRole)):
            return 0.52
        if item.hidden:
            return 0.62
        return 1.0

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        painter.save()
        try:
            self._paint_background(painter, option)
            item = index.data(BrowserItemModel.ItemRole)
            if not isinstance(item, BrowserItem):
                return
            painter.setOpacity(self.content_opacity(index, item))
            cell = option.rect
            thumbnail_rect = self.grid_metrics.thumbnail_frame_rect(cell)
            dpr = max(0.5, painter.device().devicePixelRatioF())
            snapped_frame = snap_logical_rect_to_physical_pixels(
                QRectF(thumbnail_rect),
                dpr,
            )
            painter.fillRect(snapped_frame, option.palette.base())
            painter.setPen(QPen(option.palette.mid().color(), 1))
            painter.drawRect(snapped_frame.adjusted(0, 0, -1 / dpr, -1 / dpr))
            thumbnail_image = index.data(BrowserItemModel.ThumbnailImageRole)
            if isinstance(thumbnail_image, QImage) and not thumbnail_image.isNull():
                self._paint_thumbnail_image(
                    painter,
                    thumbnail_rect,
                    thumbnail_image,
                    enabled=bool(option.state & QStyle.StateFlag.State_Enabled),
                )
            else:
                icon = index.data(Qt.ItemDataRole.DecorationRole)
                if item.kind is BrowserItemKind.OTHER or not isinstance(icon, QIcon):
                    icon_size = max(
                        16,
                        min(
                            96,
                            min(thumbnail_rect.width(), thumbnail_rect.height()) - 8,
                        ),
                    )
                    icon = self._association_image(
                        item,
                        icon_size,
                        dpr,
                    )
                self._paint_fallback_icon(painter, thumbnail_rect, icon, option)
            if bool(index.data(BrowserItemModel.ThumbnailLowResolutionRole)):
                color = option.palette.highlight().color()
                color.setAlpha(190)
                painter.setPen(QPen(color, 1, Qt.PenStyle.DotLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(
                    snap_logical_rect_to_physical_pixels(
                        QRectF(thumbnail_rect.adjusted(2, 2, -3, -3)),
                        dpr,
                    )
                )
            self._paint_type_icon(painter, thumbnail_rect, item)
            if index.data(BrowserItemModel.ThumbnailErrorRole):
                self._paint_error_badge(painter, thumbnail_rect)
            self._paint_title(painter, option, thumbnail_rect, item.display_name)
            painter.setOpacity(1.0)
            self._paint_interaction_frame(
                painter,
                option,
                self.grid_metrics.selection_rect(cell),
            )
        finally:
            painter.restore()

    @staticmethod
    def _paint_fallback_icon(
        painter: QPainter,
        thumbnail_rect: QRect,
        icon: object,
        option: QStyleOptionViewItem,
    ) -> None:
        if isinstance(icon, QImage) and not icon.isNull():
            dpr = max(0.5, painter.device().devicePixelRatioF())
            logical_width = icon.width() / dpr
            logical_height = icon.height() / dpr
            scale = min(
                1.0,
                max(1.0, thumbnail_rect.width() - 8) / max(1.0, logical_width),
                max(1.0, thumbnail_rect.height() - 8) / max(1.0, logical_height),
            )
            width = logical_width * scale
            height = logical_height * scale
            target = QRectF(
                thumbnail_rect.center().x() - width / 2,
                thumbnail_rect.center().y() - height / 2,
                width,
                height,
            )
            painter.drawImage(
                target,
                icon,
                QRectF(0, 0, icon.width(), icon.height()),
            )
            return
        if isinstance(icon, QIcon) and not icon.isNull():
                mode = (
                    QIcon.Mode.Disabled
                    if not (option.state & QStyle.StateFlag.State_Enabled)
                    else QIcon.Mode.Normal
                )
                pixmap = icon.pixmap(thumbnail_rect.size(), mode)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        thumbnail_rect.size() - QSize(8, 8),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    point = thumbnail_rect.center() - scaled.rect().center()
                    painter.drawPixmap(point, scaled)

    @staticmethod
    def _paint_thumbnail_image(
        painter: QPainter,
        thumbnail_rect: QRect,
        image: QImage,
        *,
        enabled: bool,
    ) -> None:
        available = QRectF(thumbnail_rect.adjusted(4, 4, -4, -4))
        source_ratio = image.width() / max(1, image.height())
        target_ratio = available.width() / max(1.0, available.height())
        if source_ratio > target_ratio:
            width = available.width()
            height = width / source_ratio
        else:
            height = available.height()
            width = height * source_ratio
        dpr = max(0.5, painter.device().devicePixelRatioF())
        no_upscale = min(
            1.0,
            image.width() / max(1.0, width * dpr),
            image.height() / max(1.0, height * dpr),
        )
        width *= no_upscale
        height *= no_upscale
        target = QRectF(
            available.center().x() - width / 2,
            available.center().y() - height / 2,
            width,
            height,
        )
        target = snap_logical_rect_to_physical_pixels(target, dpr)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if not enabled:
            painter.setOpacity(0.55)
        painter.drawImage(
            target,
            image,
            QRectF(0.0, 0.0, float(image.width()), float(image.height())),
        )
        if not enabled:
            painter.setOpacity(1.0)

    @staticmethod
    def _paint_background(
        painter: QPainter,
        option: QStyleOptionViewItem,
    ) -> None:
        if option.state & QStyle.StateFlag.State_MouseOver:
            color = option.palette.highlight().color()
            color.setAlpha(24)
            painter.fillRect(option.rect, color)

    def _paint_title(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        thumbnail_rect: QRect,
        title: str,
    ) -> None:
        profile = self.profile
        grid = self.grid_metrics
        if grid.title_lines == 0:
            return
        font = QFont(option.font)
        font.setPointSize(profile.font_size)
        painter.setFont(font)
        painter.setPen(
            option.palette.highlightedText().color()
            if option.state & QStyle.StateFlag.State_Selected
            else option.palette.text().color()
        )
        metrics = QFontMetrics(font)
        title_rect = grid.title_rect(option.rect)
        if option.state & QStyle.StateFlag.State_Selected:
            selected_background = option.palette.highlight().color()
            selected_background.setAlpha(96)
            painter.fillRect(title_rect.adjusted(-2, 0, 2, 0), selected_background)
        lines = (
            (
                metrics.elidedText(
                    title.replace("\n", " "),
                    Qt.TextElideMode.ElideMiddle,
                    title_rect.width(),
                ),
            )
            if grid.title_lines == 1
            else elided_title_lines(metrics, title, title_rect.width(), 2)
        )
        y = title_rect.top()
        for line in lines:
            line_rect = QRect(title_rect.left(), y, title_rect.width(), metrics.height())
            painter.drawText(
                line_rect,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                line,
            )
            y += metrics.lineSpacing()

    def _paint_type_icon(
        self,
        painter: QPainter,
        thumbnail_rect: QRect,
        item: BrowserItem,
    ) -> None:
        badge_sizes = {
            BrowserDisplayDensity.EXTRA_COMPACT: 14,
            BrowserDisplayDensity.COMPACT: 16,
            BrowserDisplayDensity.STANDARD: 18,
            BrowserDisplayDensity.COMFORTABLE: 20,
            BrowserDisplayDensity.LARGE: 22,
        }
        badge_size = badge_sizes[self.density]
        badge = type_badge_rect(thumbnail_rect, badge_size)
        dpr = max(0.5, painter.device().devicePixelRatioF())
        badge_target = snap_logical_rect_to_physical_pixels(QRectF(badge), dpr)
        shadow = badge_target.adjusted(-2, -2, 2, 2)
        shadow_color = QColor(0, 0, 0, 105)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow_color)
        painter.drawRoundedRect(shadow, 4, 4)
        image = self._association_image(item, badge_size, dpr)
        if image.isNull():
            return
        painter.drawImage(
            badge_target,
            image,
            QRectF(0, 0, image.width(), image.height()),
        )

    def _association_image(
        self,
        item: BrowserItem,
        logical_size: int,
        dpr: float,
    ) -> QImage:
        image_for = getattr(self.shell_icon_provider, "image_for", None)
        if callable(image_for):
            return image_for(
                item,
                logical_size=logical_size,
                device_pixel_ratio=dpr,
            )
        icon_for = getattr(self.shell_icon_provider, "icon_for", None)
        icon = icon_for(item) if callable(icon_for) else QIcon()
        if not isinstance(icon, QIcon) or icon.isNull():
            return QImage()
        pixel_size = max(1, round(logical_size * dpr))
        pixmap = icon.pixmap(QSize(pixel_size, pixel_size))
        return pixmap.toImage() if not pixmap.isNull() else QImage()

    @staticmethod
    def _paint_error_badge(painter: QPainter, thumbnail_rect: QRect) -> None:
        size = 16
        badge = QRect(
            thumbnail_rect.right() - size - 4,
            thumbnail_rect.top() + 4,
            size,
            size,
        )
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.setBrush(QColor(190, 40, 40, 225))
        painter.drawEllipse(badge)
        painter.drawText(
            badge,
            int(Qt.AlignmentFlag.AlignCenter),
            "!",
        )

    @staticmethod
    def _paint_interaction_frame(
        painter: QPainter,
        option: QStyleOptionViewItem,
        selection_rect: QRect,
    ) -> None:
        dpr = max(0.5, painter.device().devicePixelRatioF())

        def snapped(adjusted: QRect) -> QRectF:
            return snap_logical_rect_to_physical_pixels(QRectF(adjusted), dpr)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        if selected:
            painter.setPen(QPen(option.palette.highlight().color(), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(snapped(selection_rect))
        elif hovered:
            color = option.palette.highlight().color()
            color.setAlpha(170)
            painter.setPen(QPen(color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(snapped(selection_rect))
        if focused:
            painter.setPen(QPen(option.palette.highlightedText().color(), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(snapped(selection_rect.adjusted(3, 3, -3, -3)))
