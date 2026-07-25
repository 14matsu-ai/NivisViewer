from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from .browser_sort import BrowserDisplayDensity


THUMBNAIL_SIZE_BUCKETS = (96, 128, 160, 192, 256, 320, 384)


@dataclass(frozen=True)
class BrowserGridProfile:
    horizontal_margin: int
    vertical_margin: int
    spacing: int
    font_size: int
    title_lines: int


GRID_PROFILES = {
    BrowserDisplayDensity.COMPACT: BrowserGridProfile(20, 32, 2, 8, 1),
    BrowserDisplayDensity.STANDARD: BrowserGridProfile(44, 58, 6, 9, 2),
    BrowserDisplayDensity.COMFORTABLE: BrowserGridProfile(72, 88, 12, 10, 2),
    BrowserDisplayDensity.LARGE: BrowserGridProfile(104, 118, 16, 11, 2),
}

GRID_PRESET_THUMBNAIL_SIZES = {
    BrowserDisplayDensity.COMPACT: 128,
    BrowserDisplayDensity.STANDARD: 180,
    BrowserDisplayDensity.COMFORTABLE: 240,
    BrowserDisplayDensity.LARGE: 320,
}


def quantize_thumbnail_size(size: int) -> int:
    value = max(THUMBNAIL_SIZE_BUCKETS[0], min(THUMBNAIL_SIZE_BUCKETS[-1], int(size)))
    return min(THUMBNAIL_SIZE_BUCKETS, key=lambda bucket: (abs(bucket - value), bucket))


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
    thumbnail_size: int,
    spacing: int,
) -> QRect:
    size = max(1, int(thumbnail_size))
    return QRect(
        cell_rect.left() + (cell_rect.width() - size) // 2,
        cell_rect.top() + max(2, int(spacing) // 2),
        size,
        size,
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
    ) -> None:
        super().__init__(parent)
        self.thumbnail_size = int(thumbnail_size)
        self.density = density

    @property
    def profile(self) -> BrowserGridProfile:
        return GRID_PROFILES[self.density]

    @property
    def cell_size(self) -> QSize:
        profile = self.profile
        return QSize(
            self.thumbnail_size + profile.horizontal_margin,
            self.thumbnail_size + profile.vertical_margin,
        )

    def configure(
        self,
        *,
        thumbnail_size: int,
        density: BrowserDisplayDensity,
    ) -> None:
        self.thumbnail_size = int(thumbnail_size)
        self.density = density

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        del option, index
        return self.cell_size

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
            profile = self.profile
            cell = option.rect
            thumbnail_rect = thumbnail_rect_for_cell(
                cell,
                self.thumbnail_size,
                profile.spacing,
            )
            painter.fillRect(thumbnail_rect, option.palette.base())
            painter.setPen(QPen(option.palette.mid().color(), 1))
            painter.drawRect(thumbnail_rect.adjusted(0, 0, -1, -1))
            icon = index.data(Qt.ItemDataRole.DecorationRole)
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
            self._paint_type_badge(painter, thumbnail_rect, item)
            self._paint_title(painter, option, thumbnail_rect, item.display_name)
            if option.state & QStyle.StateFlag.State_HasFocus:
                painter.setPen(QPen(option.palette.highlight().color(), 1))
                painter.drawRect(cell.adjusted(1, 1, -2, -2))
        finally:
            painter.restore()

    @staticmethod
    def _paint_background(
        painter: QPainter,
        option: QStyleOptionViewItem,
    ) -> None:
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        elif option.state & QStyle.StateFlag.State_MouseOver:
            color = option.palette.highlight().color()
            color.setAlpha(42)
            painter.fillRect(option.rect, color)

    def _paint_title(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        thumbnail_rect: QRect,
        title: str,
    ) -> None:
        profile = self.profile
        font = QFont(option.font)
        font.setPointSize(profile.font_size)
        painter.setFont(font)
        painter.setPen(
            option.palette.highlightedText().color()
            if option.state & QStyle.StateFlag.State_Selected
            else option.palette.text().color()
        )
        metrics = QFontMetrics(font)
        title_top = thumbnail_rect.bottom() + max(4, profile.spacing // 2)
        title_rect = QRect(
            option.rect.left() + max(3, profile.spacing // 2),
            title_top,
            option.rect.width() - max(6, profile.spacing),
            max(1, option.rect.bottom() - title_top),
        )
        lines = elided_title_lines(
            metrics,
            title,
            title_rect.width(),
            profile.title_lines,
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

    def _paint_type_badge(
        self,
        painter: QPainter,
        thumbnail_rect: QRect,
        item: BrowserItem,
    ) -> None:
        key = browser_item_type_key(item)
        colors = {
            "folder": QColor("#D89B28"),
            "image": QColor("#3787D3"),
            "zip": QColor("#4E9C62"),
            "rar": QColor("#A65CC5"),
            "7z": QColor("#65737E"),
            "pdf": QColor("#CF4B43"),
            "archive": QColor("#65737E"),
        }
        labels = {
            "folder": "F",
            "image": "I",
            "zip": "Z",
            "rar": "R",
            "7z": "7",
            "pdf": "P",
            "archive": "A",
        }
        badge_size = 18 if self.density is BrowserDisplayDensity.LARGE else 16
        badge = type_badge_rect(thumbnail_rect, badge_size)
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1))
        painter.setBrush(colors[key])
        painter.drawRoundedRect(badge, 3, 3)
        font = QFont(painter.font())
        font.setBold(True)
        font.setPixelSize(max(9, badge.height() - 6))
        painter.setFont(font)
        painter.setPen(QColor("white"))
        painter.drawText(
            badge,
            int(Qt.AlignmentFlag.AlignCenter),
            labels[key],
        )
