from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from .viewer_commands import normalize_viewer_command


class ConfigManager(QObject):
    settings_changed = Signal(object)

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
        "thumbnail_size": 180,
        "thumbnail_frame_ratio": "portrait_1_sqrt2",
        "thumbnail_crop_mode": "smart_crop",
        "browser_sort_key": "name",
        "browser_sort_order": "ascending",
        "browser_folders_first": True,
        "browser_display_density": "standard",
        "last_browser_path": "",
        "browser_sidebar_visible": True,
        "browser_sidebar_width": 280,
        "browser_window_geometry": "",
        "auto_open_adjacent_book": False,
        "open_viewer_behavior": "reuse_or_create",
        "loop_book_navigation": False,
        "bring_viewer_to_front_on_open": True,
        "restore_last_reading_position": True,
        "metadata_migration_v1_completed": False,
        "mouse_gestures_enabled": True,
        "mouse_gesture_show_trail": True,
        "mouse_gesture_min_distance": 36,
        "mouse_gesture_bindings": {
            "D": "close_viewer",
            "U": "toggle_fullscreen",
        },
        "mouse_back_button_action": "previous_book",
        "mouse_forward_button_action": "next_book",
        "join_spread_pages": False,
        "thumbnail_disk_cache_enabled": True,
        "thumbnail_cache_limit_mb": 512,
        "pdf_render_base_dpi": 96,
        "pdf_render_annotations": True,
        "archive_backend_preference": "auto",
        "winrar_executable": "",
        "seven_zip_executable": "",
        "magnifier_enabled": False,
        "magnifier_zoom": 2.0,
        "magnifier_size": 220,
        "gap": 12,
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

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        writable: bool = True,
    ) -> None:
        super().__init__()
        base_dir = Path(__file__).resolve().parents[1]
        self.path = Path(path) if path else base_dir / "config.json"
        self.writable = bool(writable)
        self.last_error: str | None = None
        self.data: dict[str, Any] = deepcopy(self.DEFAULTS)

    @property
    def base_dir(self) -> Path:
        return self.path.parent.resolve()

    @property
    def thumbnail_cache_dir(self) -> Path:
        return self.base_dir / "data" / "thumbnail_cache"

    @property
    def metadata_database_path(self) -> Path:
        return self.base_dir / "data" / "metadata.sqlite3"

    def load(self) -> dict[str, Any]:
        defaults = deepcopy(self.DEFAULTS)
        if not self.path.exists():
            self._replace_data(defaults)
            return self.data

        try:
            with self.path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
        except (OSError, json.JSONDecodeError):
            self._replace_data(defaults)
            return self.data

        if isinstance(loaded, dict):
            merged = defaults
            merged.update(loaded)
            self._replace_data(self._normalize(merged))
        else:
            self._replace_data(defaults)
        return self.data

    def save(self, updates: dict[str, Any] | None = None) -> None:
        if updates:
            self.apply(updates)

        if not self.writable:
            self.last_error = "プロファイルは読み取り専用です。"
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.tmp")
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(self.data, file, ensure_ascii=False, indent=2)
            temporary.replace(self.path)
            self.last_error = None
        except OSError as exc:
            self.last_error = str(exc)
            try:
                temporary.unlink(missing_ok=True)
            except (OSError, UnboundLocalError):
                pass

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.apply({key: value})

    def apply(self, updates: dict[str, Any], *, save: bool = False) -> dict[str, Any]:
        merged = deepcopy(self.data)
        merged.update(updates)
        normalized = self._normalize(merged)
        changed = {
            key: value
            for key, value in normalized.items()
            if key not in self.data or self.data[key] != value
        }
        if changed:
            self.data.update(changed)
            self.settings_changed.emit(changed)
        if save:
            self.save()
        return changed

    @classmethod
    def _normalize(cls, values: dict[str, Any]) -> dict[str, Any]:
        normalized = values
        if normalized.get("archive_backend_preference") not in {
            "auto",
            "winrar",
            "seven_zip",
        }:
            normalized["archive_backend_preference"] = "auto"
        if not isinstance(normalized.get("winrar_executable"), str):
            normalized["winrar_executable"] = ""
        if not isinstance(normalized.get("seven_zip_executable"), str):
            normalized["seven_zip_executable"] = ""
        if not isinstance(normalized.get("last_browser_path"), str):
            normalized["last_browser_path"] = cls.DEFAULTS["last_browser_path"]
        if not isinstance(normalized.get("browser_sidebar_visible"), bool):
            normalized["browser_sidebar_visible"] = cls.DEFAULTS["browser_sidebar_visible"]
        if not isinstance(normalized.get("browser_window_geometry"), str):
            normalized["browser_window_geometry"] = cls.DEFAULTS["browser_window_geometry"]
        normalized["browser_sidebar_width"] = cls._clamped_int(
            normalized.get("browser_sidebar_width"),
            default=int(cls.DEFAULTS["browser_sidebar_width"]),
            minimum=120,
            maximum=1200,
        )
        normalized["thumbnail_size"] = cls._clamped_int(
            normalized.get("thumbnail_size"),
            default=int(cls.DEFAULTS["thumbnail_size"]),
            minimum=96,
            maximum=384,
        )
        if normalized.get("thumbnail_frame_ratio") not in {
            "square_1_1",
            "landscape_3_2",
            "portrait_2_3",
            "landscape_4_3",
            "portrait_3_4",
            "landscape_16_9",
            "portrait_9_16",
            "landscape_sqrt2_1",
            "portrait_1_sqrt2",
        }:
            normalized["thumbnail_frame_ratio"] = cls.DEFAULTS[
                "thumbnail_frame_ratio"
            ]
        if normalized.get("thumbnail_crop_mode") not in {
            "letterbox",
            "center_crop",
            "smart_crop",
        }:
            normalized["thumbnail_crop_mode"] = cls.DEFAULTS[
                "thumbnail_crop_mode"
            ]
        if normalized.get("browser_sort_key") not in {
            "name",
            "modified_time",
            "item_type",
            "file_size",
        }:
            normalized["browser_sort_key"] = cls.DEFAULTS["browser_sort_key"]
        if normalized.get("browser_sort_order") not in {"ascending", "descending"}:
            normalized["browser_sort_order"] = cls.DEFAULTS["browser_sort_order"]
        if not isinstance(normalized.get("browser_folders_first"), bool):
            normalized["browser_folders_first"] = cls.DEFAULTS["browser_folders_first"]
        if normalized.get("browser_display_density") not in {
            "compact",
            "standard",
            "comfortable",
            "large",
        }:
            normalized["browser_display_density"] = cls.DEFAULTS[
                "browser_display_density"
            ]
        behavior = normalized.get("open_viewer_behavior")
        if behavior not in {"reuse_active", "always_new", "reuse_or_create"}:
            normalized["open_viewer_behavior"] = "reuse_or_create"
        for key in (
            "bring_viewer_to_front_on_open",
            "loop_book_navigation",
            "restore_last_reading_position",
            "metadata_migration_v1_completed",
            "mouse_gestures_enabled",
            "mouse_gesture_show_trail",
            "join_spread_pages",
            "single_first_page",
            "treat_wide_image_as_single",
            "thumbnail_disk_cache_enabled",
            "pdf_render_annotations",
        ):
            if not isinstance(normalized.get(key), bool):
                normalized[key] = cls.DEFAULTS[key]
        normalized["mouse_gesture_min_distance"] = cls._clamped_int(
            normalized.get("mouse_gesture_min_distance"),
            default=int(cls.DEFAULTS["mouse_gesture_min_distance"]),
            minimum=12,
            maximum=200,
        )
        raw_bindings = normalized.get("mouse_gesture_bindings")
        bindings: dict[str, str] = {}
        if isinstance(raw_bindings, dict):
            for raw_pattern, raw_command in raw_bindings.items():
                if (
                    isinstance(raw_pattern, str)
                    and re.fullmatch(r"[UDLR]{1,8}", raw_pattern)
                ):
                    command = normalize_viewer_command(raw_command)
                    if command:
                        bindings[raw_pattern] = command
        normalized["mouse_gesture_bindings"] = bindings
        for key in ("mouse_back_button_action", "mouse_forward_button_action"):
            normalized[key] = normalize_viewer_command(normalized.get(key))
        normalized["gap"] = cls._clamped_int(
            normalized.get("gap"),
            default=int(cls.DEFAULTS["gap"]),
            minimum=0,
            maximum=100,
        )
        normalized["thumbnail_cache_limit_mb"] = cls._clamped_int(
            normalized.get("thumbnail_cache_limit_mb"),
            default=int(cls.DEFAULTS["thumbnail_cache_limit_mb"]),
            minimum=128,
            maximum=4096,
        )
        normalized["pdf_render_base_dpi"] = cls._clamped_int(
            normalized.get("pdf_render_base_dpi"),
            default=int(cls.DEFAULTS["pdf_render_base_dpi"]),
            minimum=72,
            maximum=300,
        )
        return normalized

    def _replace_data(self, values: dict[str, Any]) -> None:
        self.data.clear()
        self.data.update(values)

    @staticmethod
    def _clamped_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))
