from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


class ConfigManager:
    DEFAULTS: dict[str, Any] = {
        "last_open_path": "",
        "recent_paths": [],
        "reading_positions": {},
        "bookmarks": {},
        "view_mode": "spread",
        "reading_direction": "rtl",
        "fit_mode": "fit_window",
        "fullscreen": False,
        "reopen_last_on_start": False,
        "recursive_folder": False,
        "sort_descending": False,
        "hide_ui_in_fullscreen": False,
        "hide_cursor_in_fullscreen": False,
        "show_page_list": False,
        "thumbnail_size": 96,
        "auto_open_adjacent_book": False,
        "open_viewer_behavior": "reuse_or_create",
        "loop_book_navigation": False,
        "bring_viewer_to_front_on_open": True,
        "magnifier_enabled": False,
        "magnifier_zoom": 2.0,
        "magnifier_size": 220,
        "gap": 24,
        "single_first_page": True,
        "treat_wide_image_as_single": True,
        "split_wide_image": False,
        "smooth_scaling": True,
        "horizontal_alignment": "center",
        "brightness": 1.0,
        "contrast": 1.0,
        "gamma": 1.0,
        "cache_size": 10,
        "rotation_angle": 0,
        "slideshow_interval_ms": 3000,
        "background_color": "#000000",
        "window_geometry": "",
        "window_state": "",
    }

    def __init__(self, path: str | Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parents[1]
        self.path = Path(path) if path else base_dir / "config.json"
        self.data: dict[str, Any] = deepcopy(self.DEFAULTS)

    def load(self) -> dict[str, Any]:
        defaults = deepcopy(self.DEFAULTS)
        if not self.path.exists():
            self.data = defaults
            return self.data

        try:
            with self.path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
        except (OSError, json.JSONDecodeError):
            self.data = defaults
            return self.data

        if isinstance(loaded, dict):
            merged = defaults
            merged.update(loaded)
            self.data = merged
        else:
            self.data = defaults
        return self.data

    def save(self, updates: dict[str, Any] | None = None) -> None:
        if updates:
            self.data.update(updates)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
