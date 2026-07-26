from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

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
        super().paint(painter, prepared, index)
