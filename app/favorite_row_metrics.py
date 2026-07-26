from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FavoriteRowMetrics:
    padding_y: int = 1
    spacing: int = 0
    icon_size: int = 16

    @classmethod
    def normalized(
        cls,
        *,
        padding_y: int = 1,
        spacing: int = 0,
        icon_size: int = 16,
    ) -> FavoriteRowMetrics:
        return cls(
            padding_y=max(0, min(8, int(padding_y))),
            spacing=max(0, min(8, int(spacing))),
            icon_size=max(14, min(24, int(icon_size))),
        )

    def row_height(self, font_height: int) -> int:
        return max(max(1, int(font_height)), self.icon_size) + self.padding_y * 2
