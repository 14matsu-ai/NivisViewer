"""Palette-aware vector menu icons and icon-plus-text menu-bar rendering."""

import math

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle, QStyleOptionMenuItem


class _GearIconEngine(QIconEngine):
    def clone(self):
        return _GearIconEngine()

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        edge = min(rect.width(), rect.height())
        painter.translate(QRectF(rect).center())
        painter.scale(edge / 24, edge / 24)
        shape = QPainterPath()
        for i in range(32):
            radius = 10 if i % 4 in (1, 2) else 7.7
            angle = i * math.tau / 32
            point = QPointF(math.cos(angle) * radius, math.sin(angle) * radius)
            if i == 0:
                shape.moveTo(point)
            else:
                shape.lineTo(point)
        shape.closeSubpath()
        hole = QPainterPath()
        hole.addEllipse(QRectF(-3.4, -3.4, 6.8, 6.8))
        group = QPalette.ColorGroup.Disabled if mode == QIcon.Mode.Disabled else QPalette.ColorGroup.Active
        role = QPalette.ColorRole.HighlightedText if mode == QIcon.Mode.Selected else QPalette.ColorRole.WindowText
        painter.fillPath(shape.subtracted(hole), QApplication.palette().color(group, role))
        painter.restore()

    def pixmap(self, size, mode, state):
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        pixmap = QPixmap(size)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, QRectF(0, 0, size.width() / scale, size.height() / scale), mode, state)
        painter.end()
        return pixmap


def settings_icon() -> QIcon:
    # No external assets or font/emoji dependency. Paint from the current palette.
    return QIcon(_GearIconEngine())


def _menu_icon_metrics(font_metrics) -> tuple[int, int]:
    # Use the line height, not a fixed toolbar-sized icon. The gear's ink
    # occupies about 5/6 of this box (roughly 2/3 of the menu text line).
    edge = max(1, round(font_metrics.height() * 0.8))
    gap = max(2, round(font_metrics.height() * 0.2))
    return edge, gap


class _TextIconMenuStyle(QProxyStyle):
    """QCommonStyle otherwise chooses either the icon or text on a menu bar."""

    def __init__(self, menu_bar, *, compact=False):
        super().__init__()
        self.setParent(menu_bar)
        self.compact = compact

    def pixelMetric(self, metric, option=None, widget=None):
        if self.compact and metric in {
            QStyle.PixelMetric.PM_MenuBarItemSpacing,
            QStyle.PixelMetric.PM_MenuBarHMargin,
            QStyle.PixelMetric.PM_MenuBarVMargin,
            QStyle.PixelMetric.PM_MenuBarPanelWidth,
        }:
            return 0
        return super().pixelMetric(metric, option, widget)

    def sizeFromContents(self, kind, option, size, widget=None):
        if kind == QStyle.ContentsType.CT_MenuBarItem and isinstance(option, QStyleOptionMenuItem):
            text_option = QStyleOptionMenuItem(option)
            text_option.icon = QIcon()
            if self.compact:
                result = QSize(option.fontMetrics.horizontalAdvance(option.text) + 12,
                               option.fontMetrics.height() + 8)
            else:
                result = super().sizeFromContents(kind, text_option, size, widget)
                result.setWidth(max(result.width(), option.fontMetrics.horizontalAdvance(option.text) + 12))
            if not option.icon.isNull():
                edge, gap = _menu_icon_metrics(option.fontMetrics)
                result.setWidth(result.width() + edge + gap)
            return result
        return super().sizeFromContents(kind, option, size, widget)

    def drawControl(self, element, option, painter, widget=None):
        if (element == QStyle.ControlElement.CE_MenuBarItem
                and isinstance(option, QStyleOptionMenuItem) and not option.icon.isNull()):
            background = QStyleOptionMenuItem(option)
            background.icon = QIcon()
            background.text = ""
            super().drawControl(element, background, painter, widget)
            text_option = QStyleOptionMenuItem(option)
            text_option.icon = QIcon()
            edge, gap = _menu_icon_metrics(option.fontMetrics)
            text_option.rect = QStyle.visualRect(option.direction, option.rect,
                                               option.rect.adjusted(edge + gap, 0, 0, 0))
            super().drawControl(element, text_option, painter, widget)
            icon_rect = QRect(option.rect.left() + 6,
                              option.rect.top() + (option.rect.height() - edge) // 2,
                              edge, edge)
            icon_rect = QStyle.visualRect(option.direction, option.rect, icon_rect)
            mode = QIcon.Mode.Normal if option.state & QStyle.StateFlag.State_Enabled else QIcon.Mode.Disabled
            if option.state & QStyle.StateFlag.State_Selected:
                mode = QIcon.Mode.Selected
            option.icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter, mode)
            return
        super().drawControl(element, option, painter, widget)


def install_text_icon_menu_style(menu_bar, *, compact=False) -> None:
    if compact:
        # Preserve the Browser's existing 4x6 logical-pixel item padding.
        menu_bar.setStyleSheet("")
    style = _TextIconMenuStyle(menu_bar, compact=compact)
    menu_bar.setStyle(style)
    menu_bar._text_icon_style = style
