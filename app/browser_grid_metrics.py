from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize

from .thumbnail_render import frame_size_from_long_edge


FILENAME_DISPLAY_LINES = {
    "hidden": 0,
    "one_line": 1,
    "two_lines": 2,
}


@dataclass(frozen=True)
class BrowserGridMetrics:
    """Single source of truth for Browser list geometry in logical pixels."""

    frame_size: QSize
    font_height: int
    filename_display: str = "one_line"
    filename_gap: int = 0
    filename_padding_y: int = 0
    horizontal_margin: int = 0
    cell_padding: int = 0
    item_spacing: int = 0

    @property
    def title_lines(self) -> int:
        return FILENAME_DISPLAY_LINES.get(self.filename_display, 1)

    @property
    def title_height(self) -> int:
        if self.title_lines == 0:
            return 0
        return self.font_height * self.title_lines + self.filename_padding_y * 2

    @property
    def thumbnail_title_gap(self) -> int:
        return self.filename_gap if self.title_height else 0

    @property
    def cell_size(self) -> QSize:
        return QSize(
            self.frame_size.width()
            + max(0, self.horizontal_margin)
            + self.cell_padding * 2,
            self.frame_size.height()
            + self.thumbnail_title_gap
            + self.title_height
            + self.cell_padding * 2,
        )

    @property
    def grid_size(self) -> QSize:
        return self.cell_size

    def cell_rect(self, origin_x: int = 0, origin_y: int = 0) -> QRect:
        return QRect(origin_x, origin_y, self.cell_size.width(), self.cell_size.height())

    def thumbnail_frame_rect(self, cell_rect: QRect) -> QRect:
        return QRect(
            cell_rect.left() + (cell_rect.width() - self.frame_size.width()) // 2,
            cell_rect.top() + self.cell_padding,
            self.frame_size.width(),
            self.frame_size.height(),
        )

    def title_rect(self, cell_rect: QRect) -> QRect:
        if self.title_height == 0:
            return QRect()
        frame = self.thumbnail_frame_rect(cell_rect)
        return QRect(
            cell_rect.left(),
            frame.bottom() + 1 + self.thumbnail_title_gap,
            cell_rect.width(),
            self.title_height,
        )

    def selection_rect(self, cell_rect: QRect) -> QRect:
        return self.thumbnail_frame_rect(cell_rect).adjusted(1, 1, -2, -2)


def build_browser_grid_metrics(
    *,
    thumbnail_size: int,
    frame_ratio_id: str,
    font_height: int,
    filename_display: str,
    filename_gap: int,
    filename_padding_y: int,
    horizontal_margin: int,
    cell_padding: int,
    item_spacing: int,
) -> BrowserGridMetrics:
    return BrowserGridMetrics(
        frame_size_from_long_edge(thumbnail_size, frame_ratio_id),
        max(1, int(font_height)),
        filename_display=(
            filename_display
            if filename_display in FILENAME_DISPLAY_LINES
            else "one_line"
        ),
        filename_gap=max(0, min(32, int(filename_gap))),
        filename_padding_y=max(0, min(16, int(filename_padding_y))),
        horizontal_margin=max(0, int(horizontal_margin)),
        cell_padding=max(0, min(12, int(cell_padding))),
        item_spacing=max(0, min(32, int(item_spacing))),
    )
