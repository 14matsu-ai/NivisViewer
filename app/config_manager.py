from __future__ import annotations

from .i18n import tr


import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal
from .browser_sort import normalize_browser_random_seed, normalize_browser_sort_key
from .thumbnail_render import THUMBNAIL_ENCODER_QUALITY, normalize_thumbnail_webp_quality
from .i18n import normalize_ui_language

from .browser_wheel_scroll import (
    normalize_browser_wheel_custom_rows,
    normalize_browser_wheel_scroll_mode,
)
from .browser_folder_snapshot_cache import (
    DEFAULT_MAX_ENTRIES as DEFAULT_BROWSER_FOLDER_SNAPSHOT_CACHE_MAX_ENTRIES,
    normalize_browser_folder_snapshot_cache_max_entries,
)
from .browser_icon_size import (
    BROWSER_ICON_SIZE_DEFAULT_CUSTOM_PERCENT,
    BROWSER_ICON_SIZE_DEFAULT_PRESET,
    ICON_SIZE_SETTING_SPECS,
    normalize_browser_icon_size_custom_percent,
    normalize_browser_icon_size_preset,
)
from .viewer_commands import normalize_viewer_command
from .viewer_close_shortcut import normalize_viewer_close_shortcut
from .viewer_memory_policy import (
    normalize_viewer_memory_mode,
    viewer_memory_mode_from_legacy_mib,
)


class ConfigManager(QObject):
    settings_changed = Signal(object)

    _LEGACY_VIEWER_CACHE_MEMORY_KEY = "viewer_cache_max_memory_mib"
    _LEGACY_RESAMPLING_KEYS = (
        "viewer_resampling_mode",
        "magnifier_resampling_mode",
        "smooth_scaling",
    )
    _LEGACY_RESAMPLING_ALGORITHMS: dict[str, tuple[str, str]] = {
        "moire_reduction": ("area", "bicubic"),
        "high_quality": ("sharp", "lanczos"),
        "smooth": ("smooth", "bilinear"),
        "pixel": ("nearest", "nearest"),
    }
    VIEWER_DOWNSCALE_ALGORITHMS = frozenset(
        {"auto", "fast", "smooth", "sharp", "area", "nearest"}
    )
    VIEWER_UPSCALE_ALGORITHMS = frozenset(
        {"auto", "bilinear", "bicubic", "lanczos", "nearest"}
    )
    _LEGACY_VIEWER_MEMORY_MIB_BY_PREFETCH_PRESET: dict[str, int] = {
        "disabled": 128,
        "memory_saver": 128,
        "standard": 256,
        "more": 512,
    }

    VIEWER_PREFETCH_PRESETS: dict[str, dict[str, int]] = {
        "disabled": {
            "image_forward_units": 0,
            "image_backward_units": 0,
            "pdf_forward_units": 0,
            "pdf_backward_units": 0,
        },
        "memory_saver": {
            "image_forward_units": 2,
            "image_backward_units": 1,
            "pdf_forward_units": 1,
            "pdf_backward_units": 0,
        },
        "standard": {
            "image_forward_units": 3,
            "image_backward_units": 3,
            "pdf_forward_units": 3,
            "pdf_backward_units": 3,
        },
        "more": {
            "image_forward_units": 6,
            "image_backward_units": 2,
            "pdf_forward_units": 4,
            "pdf_backward_units": 1,
        },
    }

    DEFAULTS: dict[str, Any] = {
        "ui_language": "ja",
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
        "fullscreen_top_edge_trigger_px": 8,
        "fullscreen_bottom_edge_trigger_px": 28,
        "fullscreen_ui_hide_delay_ms": 0,
        "viewer_canvas_click_direction": "auto",
        "viewer_canvas_left_click_action": "next_single_page",
        "viewer_slider_wheel_single_page_enabled": False,
        "show_page_list": False,
        "thumbnail_size": 180,
        "thumbnail_frame_ratio": "portrait_1_sqrt2",
        "thumbnail_crop_mode": "smart_crop",
        "browser_thumbnail_display_mode": "fit",
        "browser_folder_fallback_background": "auto",
        "browser_file_fallback_background": "auto",
        **{
            key: BROWSER_ICON_SIZE_DEFAULT_PRESET
            for key, _custom_key, _label in ICON_SIZE_SETTING_SPECS
        },
        **{
            custom_key: BROWSER_ICON_SIZE_DEFAULT_CUSTOM_PERCENT
            for _key, custom_key, _label in ICON_SIZE_SETTING_SPECS
        },
        "browser_wheel_scroll_mode": "system",
        "browser_wheel_scroll_custom_rows": 3,
        "thumbnail_quality_mode": "auto",
        "thumbnail_webp_quality": THUMBNAIL_ENCODER_QUALITY,
        "thumbnail_preserve_alpha": False,
        "thumbnail_cache_max_edge": 1024,
        "text_preview_enabled": True,
        "video_thumbnail_enabled": True,
        "video_thumbnail_backend": "auto",
        "video_thumbnail_frame_mode": "smart",
        "video_thumbnail_shell_placeholder": True,
        "ffmpeg_executable": "",
        "browser_external_drop_behavior": "focus_only",
        "file_operation_destinations": [],
        "file_operation_delete_confirm_focus_yes": False,
        "file_operation_delete_skip_confirmation": False,
        "browser_sort_key": "name",
        "browser_sort_order": "ascending",
        "browser_random_seed": 0,
        "browser_folders_first": True,
        "browser_location_history_limit": 50,
        "browser_search_history_limit": 50,
        "browser_search_history": [],
        "browser_tag_registry": [],
        "browser_tag_grouped": False,
        "browser_preserve_search_for_viewer_roundtrip": True,
        "browser_display_density": "standard",
        "browser_item_spacing_x": 0,
        "browser_item_spacing_y": 0,
        "browser_cell_padding": 0,
        "browser_filename_display": "one_line",
        "browser_filename_elide_mode": "right",
        "browser_filename_font_size": 0,
        "browser_filename_show_extension": True,
        "browser_filename_gap": 0,
        "browser_filename_padding_y": 0,
        "browser_show_hidden_items": True,
        "browser_show_unsupported_files": True,
        "browser_show_system_items": False,
        "browser_folder_snapshot_cache_enabled": True,
        "browser_folder_snapshot_cache_max_entries": (
            DEFAULT_BROWSER_FOLDER_SNAPSHOT_CACHE_MAX_ENTRIES
        ),
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
        "book_open_position": "first_page",
        "metadata_migration_v1_completed": False,
        "mouse_gestures_enabled": True,
        "mouse_gesture_show_trail": True,
        "mouse_gesture_min_distance": 36,
        "mouse_gesture_bindings": {
            "D": "close_viewer",
            "U": "toggle_fullscreen",
        },
        "viewer_close_shortcut": "Ctrl+W",
        "browser_folder_gestures_enabled": True,
        "mouse_back_button_action": "previous_book",
        "mouse_forward_button_action": "next_book",
        "mouse_side_buttons_folder_navigation": False,
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
        "magnifier_allow_outside_image": True,
        "magnifier_size": 220,
        "viewer_downscale_algorithm": "auto",
        "viewer_upscale_algorithm": "auto",
        "magnifier_downscale_algorithm": "sharp",
        "magnifier_upscale_algorithm": "lanczos",
        "gap": 12,
        "single_first_page": True,
        "treat_wide_image_as_single": True,
        "split_wide_image": False,
        "horizontal_alignment": "center",
        "brightness": 1.0,
        "contrast": 1.0,
        "gamma": 1.0,
        "cache_size": 10,
        "viewer_prefetch_preset": "standard",
        "viewer_prefetch_direction_priority_enabled": True,
        "viewer_prefetch_image_forward_units": 3,
        "viewer_prefetch_image_backward_units": 3,
        "viewer_prefetch_pdf_forward_units": 3,
        "viewer_prefetch_pdf_backward_units": 3,
        "viewer_memory_mode": "auto",
        "rotation_angle": 0,
        "slideshow_interval_ms": 3000,
        "slideshow_repeat": False,
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
            loaded = self._migrate_browser_item_spacing(loaded)
            loaded = self._migrate_legacy_resampling_settings(loaded)
            if "viewer_memory_mode" not in loaded:
                preset = str(loaded.get("viewer_prefetch_preset", "standard"))
                if self._LEGACY_VIEWER_CACHE_MEMORY_KEY in loaded:
                    legacy_mib = loaded[self._LEGACY_VIEWER_CACHE_MEMORY_KEY]
                else:
                    legacy_mib = self._LEGACY_VIEWER_MEMORY_MIB_BY_PREFETCH_PRESET.get(
                        preset,
                        self._LEGACY_VIEWER_MEMORY_MIB_BY_PREFETCH_PRESET[
                            "standard"
                        ],
                    )
                loaded["viewer_memory_mode"] = viewer_memory_mode_from_legacy_mib(
                    legacy_mib
                )
            loaded.pop(self._LEGACY_VIEWER_CACHE_MEMORY_KEY, None)
            merged = defaults
            merged.update(loaded)
            legacy_edge = loaded.get("fullscreen_edge_trigger_px")
            if (
                "fullscreen_top_edge_trigger_px" not in loaded
                and legacy_edge is not None
            ):
                merged["fullscreen_top_edge_trigger_px"] = legacy_edge
            if (
                "fullscreen_bottom_edge_trigger_px" not in loaded
                and legacy_edge is not None
            ):
                merged["fullscreen_bottom_edge_trigger_px"] = legacy_edge
            self._replace_data(self._normalize(merged))
        else:
            self._replace_data(defaults)
        return self.data

    def save(self, updates: dict[str, Any] | None = None) -> None:
        if updates:
            self.apply(updates)

        self.data.pop(self._LEGACY_VIEWER_CACHE_MEMORY_KEY, None)
        for key in self._LEGACY_RESAMPLING_KEYS:
            self.data.pop(key, None)

        if not self.writable:
            self.last_error = tr('プロファイルは読み取り専用です。')
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
        merged.update(self._migrate_browser_item_spacing(updates))
        merged.pop(self._LEGACY_VIEWER_CACHE_MEMORY_KEY, None)
        for key in self._LEGACY_RESAMPLING_KEYS:
            merged.pop(key, None)
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
    def _migrate_browser_item_spacing(cls, values: dict[str, Any]) -> dict[str, Any]:
        migrated = dict(values)
        if "browser_item_spacing_mode" in migrated or "browser_item_spacing" in migrated:
            # Preset spacing was ignored by Qt's fixed grid. Preserve its
            # compact appearance; retain an explicitly chosen custom amount
            # as an effective gap on both axes. New axis values take priority.
            gap = (
                cls._clamped_int(
                    migrated.get("browser_item_spacing"),
                    default=2, minimum=0, maximum=32,
                )
                if migrated.get("browser_item_spacing_mode") == "custom" else 0
            )
            migrated.setdefault("browser_item_spacing_x", gap)
            migrated.setdefault("browser_item_spacing_y", gap)
            migrated.pop("browser_item_spacing_mode", None)
            migrated.pop("browser_item_spacing", None)
        return migrated

    @classmethod
    def _normalize(cls, values: dict[str, Any]) -> dict[str, Any]:
        normalized = values
        from .browser_tags import normalize_tag_registry
        normalized['browser_tag_registry'] = normalize_tag_registry(normalized.get('browser_tag_registry'))
        normalized["ui_language"] = normalize_ui_language(normalized.get("ui_language"))
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
            "browser_preserve_search_for_viewer_roundtrip",
            "fullscreen_auto_reveal_ui",
            "hide_ui_in_fullscreen",
            "hide_cursor_in_fullscreen",
            "browser_show_hidden_items",
            "browser_filename_show_extension",
            "browser_tag_grouped",
            "browser_show_unsupported_files",
            "browser_show_system_items",
            "browser_folder_snapshot_cache_enabled",
            "text_preview_enabled",
            "video_thumbnail_enabled",
            "video_thumbnail_shell_placeholder",
            "file_operation_delete_confirm_focus_yes",
            "file_operation_delete_skip_confirmation",
        ):
            if not isinstance(normalized.get(key), bool):
                normalized[key] = cls.DEFAULTS[key]
        normalized[
            "browser_folder_snapshot_cache_max_entries"
        ] = normalize_browser_folder_snapshot_cache_max_entries(
            normalized.get("browser_folder_snapshot_cache_max_entries")
        )
        if not isinstance(normalized.get("browser_window_geometry"), str):
            normalized["browser_window_geometry"] = cls.DEFAULTS["browser_window_geometry"]
        if normalized.get("video_thumbnail_backend") not in {
            "auto",
            "windows_shell",
            "ffmpeg",
            "disabled",
        }:
            normalized["video_thumbnail_backend"] = cls.DEFAULTS[
                "video_thumbnail_backend"
            ]
        if normalized.get("video_thumbnail_frame_mode") not in {
            "smart",
            "one_third",
            "windows_shell",
        }:
            normalized["video_thumbnail_frame_mode"] = cls.DEFAULTS[
                "video_thumbnail_frame_mode"
            ]
        if not isinstance(normalized.get("ffmpeg_executable"), str):
            normalized["ffmpeg_executable"] = ""
        if normalized.get("browser_external_drop_behavior") not in {
            "focus_only",
            "focus_and_open",
        }:
            normalized["browser_external_drop_behavior"] = cls.DEFAULTS[
                "browser_external_drop_behavior"
            ]
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
        normalized["browser_wheel_scroll_mode"] = (
            normalize_browser_wheel_scroll_mode(
                normalized.get("browser_wheel_scroll_mode")
            )
        )
        normalized["browser_wheel_scroll_custom_rows"] = (
            normalize_browser_wheel_custom_rows(
                normalized.get("browser_wheel_scroll_custom_rows")
            )
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
        filename_elide_mode = normalized.get("browser_filename_elide_mode")
        if not isinstance(filename_elide_mode, str) or filename_elide_mode not in {
            "right",
            "middle",
        }:
            normalized["browser_filename_elide_mode"] = "right"
        normalized["browser_filename_font_size"] = cls._clamped_int(
            normalized.get("browser_filename_font_size"),
            default=0,
            minimum=0,
            maximum=24,
        )
        if 0 < normalized["browser_filename_font_size"] < 6:
            normalized["browser_filename_font_size"] = 0
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
        if normalized.get("browser_thumbnail_display_mode") not in {
            "fit",
            "center_crop",
        }:
            normalized["browser_thumbnail_display_mode"] = cls.DEFAULTS[
                "browser_thumbnail_display_mode"
            ]
        if normalized.get("thumbnail_quality_mode") not in {
            "economy",
            "auto",
            "high",
        }:
            normalized["thumbnail_quality_mode"] = cls.DEFAULTS[
                "thumbnail_quality_mode"
            ]
        normalized["thumbnail_webp_quality"] = normalize_thumbnail_webp_quality(
            normalized.get("thumbnail_webp_quality")
        )
        normalized["thumbnail_preserve_alpha"] = normalized.get("thumbnail_preserve_alpha") is True
        normalized["thumbnail_cache_max_edge"] = cls._clamped_int(
            normalized.get("thumbnail_cache_max_edge"),
            default=int(cls.DEFAULTS["thumbnail_cache_max_edge"]),
            minimum=256,
            maximum=2048,
        )
        normalized["browser_sort_key"] = normalize_browser_sort_key(normalized.get("browser_sort_key")).value
        normalized["browser_random_seed"] = normalize_browser_random_seed(normalized.get("browser_random_seed"))
        if normalized.get("browser_sort_order") not in {"ascending", "descending"}:
            normalized["browser_sort_order"] = cls.DEFAULTS["browser_sort_order"]
        if not isinstance(normalized.get("browser_folders_first"), bool):
            normalized["browser_folders_first"] = cls.DEFAULTS["browser_folders_first"]
        location_history_limit = normalized.get("browser_location_history_limit")
        if (
            isinstance(location_history_limit, bool)
            or not isinstance(location_history_limit, int)
            or not 1 <= location_history_limit <= 1000
        ):
            normalized["browser_location_history_limit"] = cls.DEFAULTS[
                "browser_location_history_limit"
            ]
        search_history_limit = normalized.get("browser_search_history_limit")
        if (
            isinstance(search_history_limit, bool)
            or not isinstance(search_history_limit, int)
            or not 0 <= search_history_limit <= 1000
        ):
            search_history_limit = cls.DEFAULTS["browser_search_history_limit"]
            normalized["browser_search_history_limit"] = search_history_limit
        raw_search_history = normalized.get("browser_search_history")
        if not isinstance(raw_search_history, list):
            raw_search_history = []
        search_history: list[str] = []
        seen_searches: set[str] = set()
        if search_history_limit > 0:
            for raw_query in raw_search_history:
                if not isinstance(raw_query, str):
                    continue
                query = raw_query.strip()
                key = query.casefold()
                if not query or key in seen_searches:
                    continue
                seen_searches.add(key)
                search_history.append(query)
                if len(search_history) >= search_history_limit:
                    break
        normalized["browser_search_history"] = search_history
        if normalized.get("browser_display_density") not in {
            "extra_compact",
            "compact",
            "medium",
            "standard",
            "comfortable",
            "large",
        }:
            normalized["browser_display_density"] = cls.DEFAULTS[
                "browser_display_density"
            ]
        for key in ("browser_item_spacing_x", "browser_item_spacing_y"):
            normalized[key] = cls._clamped_int(
                normalized.get(key), default=0, minimum=0, maximum=32,
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
        normalized["fullscreen_top_edge_trigger_px"] = cls._clamped_int(
            normalized.get("fullscreen_top_edge_trigger_px"),
            default=int(cls.DEFAULTS["fullscreen_top_edge_trigger_px"]),
            minimum=4,
            maximum=32,
        )
        normalized["fullscreen_bottom_edge_trigger_px"] = cls._clamped_int(
            normalized.get("fullscreen_bottom_edge_trigger_px"),
            default=int(cls.DEFAULTS["fullscreen_bottom_edge_trigger_px"]),
            minimum=12,
            maximum=64,
        )
        if normalized.get("viewer_canvas_left_click_action") not in {
            "next_single_page",
            "next_display_unit",
            "none",
        }:
            normalized["viewer_canvas_left_click_action"] = cls.DEFAULTS[
                "viewer_canvas_left_click_action"
            ]
        if normalized.get("viewer_canvas_click_direction") not in {
            "right_next",
            "left_next",
            "auto",
        }:
            normalized["viewer_canvas_click_direction"] = cls.DEFAULTS[
                "viewer_canvas_click_direction"
            ]
        normalized["fullscreen_ui_hide_delay_ms"] = cls._clamped_int(
            normalized.get("fullscreen_ui_hide_delay_ms"),
            default=int(cls.DEFAULTS["fullscreen_ui_hide_delay_ms"]),
            minimum=0,
            maximum=3000,
        )
        behavior = normalized.get("open_viewer_behavior")
        if behavior not in {"reuse_active", "always_new", "reuse_or_create"}:
            normalized["open_viewer_behavior"] = "reuse_or_create"
        if normalized.get("viewer_prefetch_preset") not in {
            *cls.VIEWER_PREFETCH_PRESETS,
            "custom",
        }:
            normalized["viewer_prefetch_preset"] = cls.DEFAULTS[
                "viewer_prefetch_preset"
            ]
        if normalized.get("book_open_position") not in {
            "first_page",
            "resume_last",
        }:
            normalized["book_open_position"] = cls.DEFAULTS[
                "book_open_position"
            ]
        for key in (
            "viewer_downscale_algorithm",
            "magnifier_downscale_algorithm",
        ):
            if normalized.get(key) not in cls.VIEWER_DOWNSCALE_ALGORITHMS:
                normalized[key] = cls.DEFAULTS[key]
        for key in (
            "viewer_upscale_algorithm",
            "magnifier_upscale_algorithm",
        ):
            if normalized.get(key) not in cls.VIEWER_UPSCALE_ALGORITHMS:
                normalized[key] = cls.DEFAULTS[key]
        try:
            magnifier_zoom = float(normalized.get("magnifier_zoom", 2.0))
        except (TypeError, ValueError):
            magnifier_zoom = 2.0
        normalized["magnifier_zoom"] = (
            magnifier_zoom
            if magnifier_zoom in {1.5, 2.0, 3.0, 4.0}
            else 2.0
        )
        normalized["slideshow_interval_ms"] = cls._clamped_int(
            normalized.get("slideshow_interval_ms"), default=3000, minimum=500, maximum=60000,
        )
        for key in (
            "bring_viewer_to_front_on_open",
            "loop_book_navigation",
            "slideshow_repeat",
            "auto_open_adjacent_book",
            "restore_last_reading_position",
            "metadata_migration_v1_completed",
            "mouse_gestures_enabled",
            "mouse_side_buttons_folder_navigation",
            "mouse_gesture_show_trail",
            "browser_folder_gestures_enabled",
            "viewer_slider_wheel_single_page_enabled",
            "magnifier_allow_outside_image",
            "join_spread_pages",
            "single_first_page",
            "treat_wide_image_as_single",
            "thumbnail_disk_cache_enabled",
            "pdf_render_annotations",
            "viewer_prefetch_direction_priority_enabled",
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
        normalized["viewer_close_shortcut"] = normalize_viewer_close_shortcut(
            normalized.get("viewer_close_shortcut")
        )
        normalized["gap"] = cls._clamped_int(
            normalized.get("gap"),
            default=int(cls.DEFAULTS["gap"]),
            minimum=0,
            maximum=100,
        )
        for key in (
            "viewer_prefetch_image_forward_units",
            "viewer_prefetch_image_backward_units",
            "viewer_prefetch_pdf_forward_units",
            "viewer_prefetch_pdf_backward_units",
        ):
            normalized[key] = cls._clamped_int(
                normalized.get(key),
                default=int(cls.DEFAULTS[key]),
                minimum=0,
                maximum=20,
            )
        normalized["viewer_memory_mode"] = normalize_viewer_memory_mode(
            normalized.get("viewer_memory_mode")
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
        for key in ("browser_folder_fallback_background", "browser_file_fallback_background"):
            fallback_background = str(normalized.get(key, "auto")).strip().casefold()
            normalized[key] = (
                fallback_background
                if fallback_background == "auto"
                or re.fullmatch(r"#[0-9a-f]{6}", fallback_background)
                else cls.DEFAULTS[key]
            )
        for key, custom_key, _label in ICON_SIZE_SETTING_SPECS:
            normalized[key] = normalize_browser_icon_size_preset(normalized.get(key))
            normalized[custom_key] = normalize_browser_icon_size_custom_percent(
                normalized.get(custom_key)
            )
        return normalized

    @classmethod
    def _migrate_legacy_resampling_settings(
        cls,
        loaded: dict[str, Any],
    ) -> dict[str, Any]:
        """Translate the old combined modes once, without retaining two authorities."""

        migrated = dict(loaded)
        legacy_smooth = migrated.get("smooth_scaling", True)
        smooth_enabled = legacy_smooth if isinstance(legacy_smooth, bool) else True
        for prefix, legacy_key, legacy_default in (
            ("viewer", "viewer_resampling_mode", "standard"),
            ("magnifier", "magnifier_resampling_mode", "high_quality"),
        ):
            down_key = f"{prefix}_downscale_algorithm"
            up_key = f"{prefix}_upscale_algorithm"
            if down_key in migrated and up_key in migrated:
                continue
            legacy_mode = migrated.get(legacy_key, legacy_default)
            if legacy_mode == "standard":
                algorithms = (
                    ("auto", "auto")
                    if smooth_enabled
                    else ("fast", "nearest")
                )
            else:
                algorithms = cls._LEGACY_RESAMPLING_ALGORITHMS.get(
                    str(legacy_mode),
                    ("auto", "auto") if smooth_enabled else ("fast", "nearest"),
                )
            migrated.setdefault(down_key, algorithms[0])
            migrated.setdefault(up_key, algorithms[1])
        for key in cls._LEGACY_RESAMPLING_KEYS:
            migrated.pop(key, None)
        return migrated

    def viewer_prefetch_settings(self) -> dict[str, int | bool | str]:
        preset = str(self.get("viewer_prefetch_preset", "standard"))
        if preset == "custom":
            values = {
                "image_forward_units": int(
                    self.get("viewer_prefetch_image_forward_units", 3)
                ),
                "image_backward_units": int(
                    self.get("viewer_prefetch_image_backward_units", 3)
                ),
                "pdf_forward_units": int(
                    self.get("viewer_prefetch_pdf_forward_units", 3)
                ),
                "pdf_backward_units": int(
                    self.get("viewer_prefetch_pdf_backward_units", 3)
                ),
            }
        else:
            values = dict(
                self.VIEWER_PREFETCH_PRESETS.get(
                    preset,
                    self.VIEWER_PREFETCH_PRESETS["standard"],
                )
            )
        return {
            "preset": preset,
            "direction_priority_enabled": bool(
                self.get("viewer_prefetch_direction_priority_enabled", True)
            ),
            **values,
        }

    def viewer_memory_mode(self) -> str:
        return normalize_viewer_memory_mode(
            self.get("viewer_memory_mode", "auto")
        )

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
