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
        "fullscreen_auto_reveal_ui": True,
        "fullscreen_edge_trigger_px": 8,
        "fullscreen_ui_hide_delay_ms": 0,
        "show_page_list": False,
        "thumbnail_size": 180,
        "thumbnail_frame_ratio": "portrait_1_sqrt2",
        "thumbnail_crop_mode": "smart_crop",
        "thumbnail_quality_mode": "auto",
        "thumbnail_cache_max_edge": 1024,
        "browser_sort_key": "name",
        "browser_sort_order": "ascending",
        "browser_folders_first": True,
        "browser_display_density": "standard",
        "browser_item_spacing_mode": "preset",
        "browser_item_spacing": 2,
        "browser_cell_padding": 0,
        "browser_filename_display": "one_line",
        "browser_filename_gap": 0,
        "browser_filename_padding_y": 0,
        "browser_show_hidden_items": True,
        "browser_show_unsupported_files": True,
        "browser_show_system_items": False,
        "last_browser_path": "",
        "browser_sidebar_visible": True,
        "browser_sidebar_width": 280,
        "browser_sidebar_layout": "favorites_top_tree_bottom",
        "browser_sidebar_splitter_sizes": [220, 420],
        "browser_show_favorites": True,
        "browser_show_folder_tree": True,
        "browser_show_history": True,
        "favorite_row_padding_y": 1,
        "favorite_row_spacing": 0,
        "favorite_icon_size": 16,
        "folder_tree_sync_mode": "focus_current",
        "folder_tree_collapse_unrelated": True,
        "folder_tree_focus_rebase": True,
        "folder_tree_context_ancestor_levels": 3,
        "clear_browser_filter_on_navigation": False,
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
        "thumbnail_cache_max_unused_days": 0,
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
        for key in (
            "browser_show_favorites",
            "browser_show_folder_tree",
            "browser_show_history",
            "folder_tree_collapse_unrelated",
            "folder_tree_focus_rebase",
            "clear_browser_filter_on_navigation",
            "fullscreen_auto_reveal_ui",
            "hide_ui_in_fullscreen",
            "hide_cursor_in_fullscreen",
            "browser_show_hidden_items",
            "browser_show_unsupported_files",
            "browser_show_system_items",
        ):
            if not isinstance(normalized.get(key), bool):
                normalized[key] = cls.DEFAULTS[key]
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
        if normalized.get("browser_filename_display") not in {
            "hidden",
            "one_line",
            "two_lines",
        }:
            normalized["browser_filename_display"] = cls.DEFAULTS[
                "browser_filename_display"
            ]
        normalized["browser_filename_gap"] = cls._clamped_int(
            normalized.get("browser_filename_gap"),
            default=0,
            minimum=0,
            maximum=32,
        )
        normalized["browser_filename_padding_y"] = cls._clamped_int(
            normalized.get("browser_filename_padding_y"),
            default=0,
            minimum=0,
            maximum=16,
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
        if normalized.get("thumbnail_quality_mode") not in {
            "economy",
            "auto",
            "high",
        }:
            normalized["thumbnail_quality_mode"] = cls.DEFAULTS[
                "thumbnail_quality_mode"
            ]
        normalized["thumbnail_cache_max_edge"] = cls._clamped_int(
            normalized.get("thumbnail_cache_max_edge"),
            default=int(cls.DEFAULTS["thumbnail_cache_max_edge"]),
            minimum=256,
            maximum=2048,
        )
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
            "extra_compact",
            "compact",
            "standard",
            "comfortable",
            "large",
        }:
            normalized["browser_display_density"] = cls.DEFAULTS[
                "browser_display_density"
            ]
        if normalized.get("browser_item_spacing_mode") not in {"preset", "custom"}:
            normalized["browser_item_spacing_mode"] = cls.DEFAULTS[
                "browser_item_spacing_mode"
            ]
        normalized["browser_item_spacing"] = cls._clamped_int(
            normalized.get("browser_item_spacing"),
            default=int(cls.DEFAULTS["browser_item_spacing"]),
            minimum=0,
            maximum=32,
        )
        normalized["browser_cell_padding"] = cls._clamped_int(
            normalized.get("browser_cell_padding"),
            default=int(cls.DEFAULTS["browser_cell_padding"]),
            minimum=0,
            maximum=12,
        )
        if normalized.get("browser_sidebar_layout") not in {
            "favorites_top_tree_bottom",
            "tree_top_favorites_bottom",
            "tabs",
            "favorites_only",
            "tree_only",
        }:
            normalized["browser_sidebar_layout"] = cls.DEFAULTS[
                "browser_sidebar_layout"
            ]
        raw_splitter_sizes = normalized.get("browser_sidebar_splitter_sizes")
        if not (
            isinstance(raw_splitter_sizes, list)
            and len(raw_splitter_sizes) == 2
        ):
            raw_splitter_sizes = cls.DEFAULTS["browser_sidebar_splitter_sizes"]
        normalized["browser_sidebar_splitter_sizes"] = [
            cls._clamped_int(
                value,
                default=220 if index == 0 else 420,
                minimum=40,
                maximum=4000,
            )
            for index, value in enumerate(raw_splitter_sizes)
        ]
        if normalized.get("folder_tree_sync_mode") not in {
            "off",
            "select_current",
            "focus_current",
        }:
            normalized["folder_tree_sync_mode"] = cls.DEFAULTS[
                "folder_tree_sync_mode"
            ]
        normalized["folder_tree_context_ancestor_levels"] = cls._clamped_int(
            normalized.get("folder_tree_context_ancestor_levels"),
            default=int(cls.DEFAULTS["folder_tree_context_ancestor_levels"]),
            minimum=0,
            maximum=12,
        )
        normalized["favorite_row_padding_y"] = cls._clamped_int(
            normalized.get("favorite_row_padding_y"),
            default=int(cls.DEFAULTS["favorite_row_padding_y"]),
            minimum=0,
            maximum=8,
        )
        normalized["favorite_row_spacing"] = cls._clamped_int(
            normalized.get("favorite_row_spacing"),
            default=int(cls.DEFAULTS["favorite_row_spacing"]),
            minimum=0,
            maximum=8,
        )
        normalized["favorite_icon_size"] = cls._clamped_int(
            normalized.get("favorite_icon_size"),
            default=int(cls.DEFAULTS["favorite_icon_size"]),
            minimum=14,
            maximum=24,
        )
        normalized["fullscreen_edge_trigger_px"] = cls._clamped_int(
            normalized.get("fullscreen_edge_trigger_px"),
            default=int(cls.DEFAULTS["fullscreen_edge_trigger_px"]),
            minimum=4,
            maximum=32,
        )
        normalized["fullscreen_ui_hide_delay_ms"] = cls._clamped_int(
            normalized.get("fullscreen_ui_hide_delay_ms"),
            default=int(cls.DEFAULTS["fullscreen_ui_hide_delay_ms"]),
            minimum=0,
            maximum=3000,
        )
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
        unused_days = cls._clamped_int(
            normalized.get("thumbnail_cache_max_unused_days"),
            default=0,
            minimum=0,
            maximum=3650,
        )
        normalized["thumbnail_cache_max_unused_days"] = (
            unused_days if unused_days == 0 or unused_days >= 7 else 7
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
