from __future__ import annotations

import os
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class BrowserLocation:
    path: str
    selected_path: str | None = None
    vertical_scroll: int = 0
    horizontal_scroll: int = 0


class BrowserNavigationHistory:
    def __init__(self, max_entries: int = 150) -> None:
        self.max_entries = max(1, min(1000, int(max_entries)))
        self._locations: list[BrowserLocation] = []
        self._current_index = -1

    def __len__(self) -> int:
        return len(self._locations)

    def visit(self, location: BrowserLocation) -> None:
        if (
            self._current_index >= 0
            and self._path_key(self._locations[self._current_index].path)
            == self._path_key(location.path)
        ):
            return

        if self._current_index + 1 < len(self._locations):
            del self._locations[self._current_index + 1 :]
        self._locations.append(location)
        if len(self._locations) > self.max_entries:
            overflow = len(self._locations) - self.max_entries
            del self._locations[:overflow]
        self._current_index = len(self._locations) - 1

    def update_current_view_state(
        self,
        *,
        selected_path: str | None,
        vertical_scroll: int,
        horizontal_scroll: int,
    ) -> None:
        if self._current_index < 0:
            return
        current = self._locations[self._current_index]
        self._locations[self._current_index] = replace(
            current,
            selected_path=selected_path,
            vertical_scroll=max(0, int(vertical_scroll)),
            horizontal_scroll=max(0, int(horizontal_scroll)),
        )

    def can_go_back(self) -> bool:
        return self._current_index > 0

    def can_go_forward(self) -> bool:
        return 0 <= self._current_index < len(self._locations) - 1

    def go_back(self) -> BrowserLocation | None:
        if not self.can_go_back():
            return None
        self._current_index -= 1
        return self.current()

    def go_forward(self) -> BrowserLocation | None:
        if not self.can_go_forward():
            return None
        self._current_index += 1
        return self.current()

    def current(self) -> BrowserLocation | None:
        if not 0 <= self._current_index < len(self._locations):
            return None
        return self._locations[self._current_index]

    @staticmethod
    def _path_key(path: str) -> str:
        absolute = os.path.abspath(os.path.normpath(os.path.expanduser(path)))
        return os.path.normcase(absolute).casefold()
