from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .favorite_row_metrics import FavoriteRowMetrics


class FavoriteItemDelegate(QStyledItemDelegate):
    """One-line favorite rows with no style-dependent vertical padding."""

    def __init__(
        self,
        parent=None,
        *,
        metrics: FavoriteRowMetrics | None = None,
    ) -> None:
        super().__init__(parent)
        self.metrics = metrics or FavoriteRowMetrics()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        return QSize(
            max(1, option.rect.width()),
            self.metrics.row_height(option.fontMetrics.height()),
        )

    def paint(self, painter, option: QStyleOptionViewItem, index) -> None:
        prepared = QStyleOptionViewItem(option)
        self.initStyleOption(prepared, index)
        prepared.decorationSize = QSize(
            self.metrics.icon_size,
            self.metrics.icon_size,
        )
        prepared.textElideMode = Qt.TextElideMode.ElideRight
        prepared.features &= ~QStyleOptionViewItem.ViewItemFeature.WrapText
        view = self.parent()
        is_drop_hover = getattr(view, "is_drop_hover_index", None)
        if callable(is_drop_hover) and is_drop_hover(index):
            prepared.state |= QStyle.StateFlag.State_MouseOver
            emphasized = QFont(prepared.font)
            emphasized.setBold(True)
            prepared.font = emphasized
        super().paint(painter, prepared, index)


class HistoryItemDelegate(FavoriteItemDelegate):
    """Keep compact rows and use the model's Windows association icons."""
