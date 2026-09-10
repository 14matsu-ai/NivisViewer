"""Compact five-star rating quick-filter control for the Browser menu row."""

from __future__ import annotations

from .i18n import tr


from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import QMenu, QWidget

from .browser_filter import RatingFilterMode, normalize_rating_filter_mode


class BrowserRatingFilterWidget(QWidget):
    filterChanged = Signal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("browser_rating_quick_filter")
        self.setMouseTracking(True)
        self._mode = RatingFilterMode.OFF
        self._reference = 0
        self._hover_reference = 0
        self._pressed_reference = 0
        self._context_menu: QMenu | None = None
        self.setFixedWidth(self.sizeHint().width())
        self._update_tool_tip()

    @property
    def mode(self) -> RatingFilterMode:
        return self._mode

    @property
    def reference(self) -> int:
        return self._reference

    def sizeHint(self) -> QSize:  # noqa: N802
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance("★★★★★") + 16, metrics.height() + 8)

    def _stars_rect(self) -> QRect:
        return self.rect().adjusted(8, 2, -8, -2)

    def rating_at(self, position) -> int | None:
        stars = self._stars_rect()
        if not stars.contains(position) or stars.width() <= 0:
            return None
        offset = max(0, position.x() - stars.left())
        return max(1, min(5, 5 * offset // max(1, stars.width()) + 1))

    def set_filter(self, mode: RatingFilterMode | str, reference: int = 0) -> bool:
        normalized_mode = normalize_rating_filter_mode(mode)
        normalized_reference = (
            max(1, min(5, int(reference)))
            if normalized_mode in {RatingFilterMode.AT_LEAST, RatingFilterMode.EQUAL}
            else 0
        )
        if (
            normalized_mode is self._mode
            and normalized_reference == self._reference
        ):
            return False
        self._mode = normalized_mode
        self._reference = normalized_reference
        self._hover_reference = 0
        self._update_tool_tip()
        self.update()
        self.filterChanged.emit(self._mode.value, self._reference)
        return True

    def clear_filter(self) -> bool:
        return self.set_filter(RatingFilterMode.OFF, 0)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        rating = self.rating_at(event.position().toPoint()) or 0
        if rating != self._hover_reference:
            self._hover_reference = rating
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_reference:
            self._hover_reference = 0
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_reference = (
                self.rating_at(event.position().toPoint()) or 0
            )
            if self._pressed_reference:
                event.accept()
                return
        elif event.button() == Qt.MouseButton.MiddleButton:
            self._pressed_reference = 0
            self.clear_filter()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pressed = self._pressed_reference
            self._pressed_reference = 0
            if pressed and self.rating_at(event.position().toPoint()) is not None:
                if (
                    self._mode is RatingFilterMode.AT_LEAST
                    and self._reference == pressed
                ):
                    self.clear_filter()
                else:
                    self.set_filter(RatingFilterMode.AT_LEAST, pressed)
                event.accept()
                return
        elif event.button() == Qt.MouseButton.MiddleButton:
            self._pressed_reference = 0
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        if self._context_menu is not None:
            if self._context_menu.isVisible():
                self._context_menu.close()
                event.accept()
                return
            self._context_menu.deleteLater()
        reference = self.rating_at(event.pos()) or self._reference or 3
        menu = QMenu(self)
        self._context_menu = menu
        at_least = menu.addAction(tr('★{p0}以上', p0=reference))
        equal = menu.addAction(tr('★{p0}のみ', p0=reference))
        unrated = menu.addAction(tr('未評価'))
        menu.addSeparator()
        clear = menu.addAction(tr('フィルタ解除'))
        at_least.triggered.connect(
            lambda _checked=False: self.set_filter(
                RatingFilterMode.AT_LEAST,
                reference,
            )
        )
        equal.triggered.connect(
            lambda _checked=False: self.set_filter(
                RatingFilterMode.EQUAL,
                reference,
            )
        )
        unrated.triggered.connect(
            lambda _checked=False: self.set_filter(RatingFilterMode.UNRATED, 0)
        )
        clear.triggered.connect(lambda _checked=False: self.clear_filter())
        menu.aboutToHide.connect(
            lambda value=menu: self._release_context_menu(value)
        )
        menu.popup(event.globalPos())
        event.accept()

    def _release_context_menu(self, menu: QMenu) -> None:
        if self._context_menu is menu:
            self._context_menu = None
        menu.deleteLater()

    def _update_tool_tip(self) -> None:
        if self._mode is RatingFilterMode.AT_LEAST:
            state = tr('現在: ★{p0}以上', p0=self._reference)
        elif self._mode is RatingFilterMode.EQUAL:
            state = tr('現在: ★{p0}のみ', p0=self._reference)
        elif self._mode is RatingFilterMode.UNRATED:
            state = tr('現在: 未評価')
        else:
            state = tr('現在: 絞り込みなし')
        self.setToolTip(
            tr('{p0}\nクリック: ★X以上で絞り込み（同じ条件で解除）\n右クリック: ★Xのみ／未評価／解除', p0=state)
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        stars = self._stars_rect()
        preview = self._hover_reference
        filled = preview or (
            self._reference
            if self._mode in {RatingFilterMode.AT_LEAST, RatingFilterMode.EQUAL}
            else 0
        )
        painter.setPen(QColor("#778899"))
        painter.drawText(
            stars,
            int(Qt.AlignmentFlag.AlignCenter),
            "☆☆☆☆☆",
        )
        if filled:
            painter.setPen(QColor("#ffd700"))
            painter.drawText(
                stars,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                "★★★★★"[:filled],
            )


__all__ = ["BrowserRatingFilterWidget"]
