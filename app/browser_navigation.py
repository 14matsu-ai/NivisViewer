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
    def __init__(self, max_entries: int = 150, recent_limit: int = 50) -> None:
        self.max_entries = max(1, min(1000, int(max_entries)))
        self._recent_limit = self._normalize_recent_limit(recent_limit)
        self._locations: list[BrowserLocation] = []
        self._recent_locations: list[BrowserLocation] = []
        self._current_index = -1

    def __len__(self) -> int:
        return len(self._locations)

    @property
    def entries(self) -> tuple[BrowserLocation, ...]:
        return tuple(self._locations)

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def recent_limit(self) -> int:
        return self._recent_limit

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
        self._record_recent(location)

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
        updated = replace(
            current,
            selected_path=selected_path,
            vertical_scroll=max(0, int(vertical_scroll)),
            horizontal_scroll=max(0, int(horizontal_scroll)),
        )
        self._locations[self._current_index] = updated
        current_key = self._path_key(current.path)
        for index, location in enumerate(self._recent_locations):
            if self._path_key(location.path) == current_key:
                self._recent_locations[index] = updated
                break

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

    def go_to(self, index: int) -> BrowserLocation | None:
        target = int(index)
        if not 0 <= target < len(self._locations):
            return None
        self._current_index = target
        return self.current()

    def recent_unique(
        self,
        limit: int | None = None,
    ) -> tuple[tuple[int, BrowserLocation], ...]:
        """Project the bounded MRU while retaining a matching timeline index."""

        count = self._recent_limit if limit is None else max(1, int(limit))
        timeline_indexes: dict[str, int] = {}
        for index, location in enumerate(self._locations):
            timeline_indexes[self._path_key(location.path)] = index
        return tuple(
            (timeline_indexes.get(self._path_key(location.path), -1), location)
            for location in self._recent_locations[:count]
        )

    def set_recent_limit(self, limit: int) -> bool:
        normalized = self._normalize_recent_limit(limit)
        changed = normalized != self._recent_limit
        self._recent_limit = normalized
        if len(self._recent_locations) > normalized:
            del self._recent_locations[normalized:]
            changed = True
        return changed

    def mark_recent(self, location: BrowserLocation) -> None:
        self._record_recent(location)

    def remove_recent(self, path: str) -> bool:
        key = self._path_key(path)
        retained = [
            location
            for location in self._recent_locations
            if self._path_key(location.path) != key
        ]
        if len(retained) == len(self._recent_locations):
            return False
        self._recent_locations = retained
        return True

    def current(self) -> BrowserLocation | None:
        if not 0 <= self._current_index < len(self._locations):
            return None
        return self._locations[self._current_index]

    def relocate_path(self, old_path: str, new_path: str) -> bool:
        return self.relocate_tree(old_path, new_path)

    def relocate_tree(self, old_root: str, new_root: str) -> bool:
        old_key = self._path_key(old_root)
        changed = False
        relocated: list[BrowserLocation] = []
        relocated_current = -1
        for index, location in enumerate(self._locations):
            new_location = replace(
                location,
                path=self._relocated_value(
                    location.path,
                    old_root,
                    new_root,
                    old_key,
                ),
                selected_path=(
                    self._relocated_value(
                        location.selected_path,
                        old_root,
                        new_root,
                        old_key,
                    )
                    if location.selected_path is not None
                    else None
                ),
            )
            changed = changed or new_location != location
            if (
                relocated
                and self._path_key(relocated[-1].path)
                == self._path_key(new_location.path)
            ):
                relocated[-1] = new_location
                if index <= self._current_index:
                    relocated_current = len(relocated) - 1
                continue
            relocated.append(new_location)
            if index <= self._current_index:
                relocated_current = len(relocated) - 1
        relocated_recent = self._relocated_recent(
            old_root,
            new_root,
            old_key,
        )
        recent_changed = relocated_recent != self._recent_locations
        if changed:
            self._locations = relocated
            self._current_index = relocated_current
        if recent_changed:
            self._recent_locations = relocated_recent
        return changed or recent_changed

    def _record_recent(self, location: BrowserLocation) -> None:
        key = self._path_key(location.path)
        self._recent_locations = [
            existing
            for existing in self._recent_locations
            if self._path_key(existing.path) != key
        ]
        self._recent_locations.insert(0, location)
        del self._recent_locations[self._recent_limit :]

    def _relocated_recent(
        self,
        old_root: str,
        new_root: str,
        old_key: str,
    ) -> list[BrowserLocation]:
        result: list[BrowserLocation] = []
        seen: set[str] = set()
        for location in self._recent_locations:
            relocated = replace(
                location,
                path=self._relocated_value(
                    location.path,
                    old_root,
                    new_root,
                    old_key,
                ),
                selected_path=(
                    self._relocated_value(
                        location.selected_path,
                        old_root,
                        new_root,
                        old_key,
                    )
                    if location.selected_path is not None
                    else None
                ),
            )
            key = self._path_key(relocated.path)
            if key in seen:
                continue
            seen.add(key)
            result.append(relocated)
        return result[: self._recent_limit]

    @staticmethod
    def _normalize_recent_limit(limit: int) -> int:
        try:
            value = int(limit)
        except (TypeError, ValueError):
            value = 50
        return value if value in {10, 20, 30, 50, 100, 200} else 50

    @classmethod
    def _relocated_value(
        cls,
        path: str,
        old_root: str,
        new_root: str,
        old_key: str,
    ) -> str:
        key = cls._path_key(path)
        if key == old_key:
            return os.path.abspath(os.path.normpath(new_root))
        prefix = old_key.rstrip("\\/") + os.sep.casefold()
        if not key.startswith(prefix):
            return path
        relative = os.path.relpath(
            os.path.abspath(os.path.normpath(path)),
            os.path.abspath(os.path.normpath(old_root)),
        )
        return os.path.abspath(os.path.normpath(os.path.join(new_root, relative)))

    @staticmethod
    def _path_key(path: str) -> str:
        absolute = os.path.abspath(os.path.normpath(os.path.expanduser(path)))
        return os.path.normcase(absolute).casefold()
