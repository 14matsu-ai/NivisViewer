from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .favorite_row_metrics import FavoriteRowMetrics


class FavoriteItemDelegate(QStyledItemDelegate):
    """One-line favorite rows with no style-dependent vertical padding."""

    def __init__(
        self,
        parent=None,
        *,
        metrics: FavoriteRowMetrics | None = None,
        show_color_icon: bool = True,
        show_color_left_bar: bool = True,
        show_color_background: bool = False,
        show_color_text: bool = False,
    ) -> None:
        super().__init__(parent)
        self.metrics = metrics or FavoriteRowMetrics()
        self.show_color_icon = bool(show_color_icon)
        self.show_color_left_bar = bool(show_color_left_bar)
        self.show_color_background = bool(show_color_background)
        self.show_color_text = bool(show_color_text)

    def configure_color_display(self, *, icon: bool, left_bar: bool, background: bool, text: bool) -> None:
        self.show_color_icon = bool(icon)
        self.show_color_left_bar = bool(left_bar)
        self.show_color_background = bool(background)
        self.show_color_text = bool(text)

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        return QSize(
            max(1, option.rect.width()),
            self.metrics.row_height(option.fontMetrics.height()),
        )

    def paint(self, painter, option: QStyleOptionViewItem, index) -> None:
        model = index.model()
        kind_role = getattr(model, "KindRole", -1)
        color_role = getattr(model, "AccentColorRole", -1)
        alignment_role = getattr(model, "SeparatorAlignmentRole", -1)
        kind = index.data(kind_role) if kind_role >= 0 else "folder"
        if kind == "separator":
            self._paint_separator(
                painter,
                option,
                str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
                str(index.data(alignment_role) or "center"),
            )
            return

        prepared = QStyleOptionViewItem(option)
        self.initStyleOption(prepared, index)
        prepared.decorationSize = QSize(
            self.metrics.icon_size,
            self.metrics.icon_size,
        )
        prepared.textElideMode = Qt.TextElideMode.ElideRight
        prepared.features &= ~QStyleOptionViewItem.ViewItemFeature.WrapText
        accent = QColor(str(index.data(color_role) or "")) if color_role >= 0 else QColor()
        selected = bool(prepared.state & QStyle.StateFlag.State_Selected)
        if accent.isValid() and self.show_color_background and not selected:
            background = QColor(accent)
            background.setAlpha(36)
            prepared.backgroundBrush = background
        if accent.isValid() and self.show_color_text and not selected:
            prepared.palette.setColor(QPalette.ColorRole.Text, accent)
        if accent.isValid() and self.show_color_icon and not prepared.icon.isNull():
            prepared.icon = self._tinted_icon(prepared.icon, prepared.decorationSize, accent)

        view = self.parent()
        is_drop_hover = getattr(view, "is_drop_hover_index", None)
        if callable(is_drop_hover) and is_drop_hover(index):
            prepared.state |= QStyle.StateFlag.State_MouseOver
            emphasized = QFont(prepared.font)
            emphasized.setBold(True)
            prepared.font = emphasized
        super().paint(painter, prepared, index)

        if accent.isValid() and self.show_color_left_bar:
            painter.save()
            painter.fillRect(
                QRect(option.rect.left() + 1, option.rect.top() + 1, 3, max(1, option.rect.height() - 2)),
                accent,
            )
            painter.restore()

    @staticmethod
    def _tinted_icon(icon: QIcon, size: QSize, color: QColor) -> QIcon:
        pixmap = icon.pixmap(size)
        if pixmap.isNull():
            return icon
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), color)
        painter.end()
        return QIcon(pixmap)

    def _paint_separator(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        label: str,
        alignment: str,
    ) -> None:
        rect = option.rect.adjusted(4, 0, -4, 0)
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect, option.palette.highlight())
            color = option.palette.highlightedText().color()
        else:
            color = option.palette.mid().color()
        painter.setPen(QPen(color, 1))
        y = rect.center().y()
        text = label.strip()
        if not text:
            painter.drawLine(rect.left(), y, rect.right(), y)
            painter.restore()
            return
        metrics = option.fontMetrics
        width = min(metrics.horizontalAdvance(text), max(1, rect.width() - 16))
        gap = 7
        if alignment == "left":
            text_rect = QRect(rect.left(), rect.top(), width, rect.height())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            start = min(rect.right(), text_rect.right() + gap)
            painter.drawLine(start, y, rect.right(), y)
        elif alignment == "right":
            text_rect = QRect(rect.right() - width + 1, rect.top(), width, rect.height())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
            end = max(rect.left(), text_rect.left() - gap)
            painter.drawLine(rect.left(), y, end, y)
        else:
            left = rect.center().x() - width // 2
            text_rect = QRect(left, rect.top(), width, rect.height())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)
            painter.drawLine(rect.left(), y, max(rect.left(), text_rect.left() - gap), y)
            painter.drawLine(min(rect.right(), text_rect.right() + gap), y, rect.right(), y)
        painter.restore()


class HistoryItemDelegate(FavoriteItemDelegate):
    """Keep compact rows and use the model's Windows association icons."""
