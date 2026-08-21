from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config_manager import ConfigManager


def test_missing_config_uses_defaults(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "config.json")

    assert manager.load() == ConfigManager.DEFAULTS
    assert manager.data is not ConfigManager.DEFAULTS
    assert manager.data["open_viewer_behavior"] == "reuse_or_create"
    assert manager.data["loop_book_navigation"] is False
    assert manager.data["bring_viewer_to_front_on_open"] is True
    assert manager.data["restore_last_reading_position"] is True
    assert manager.data["book_open_position"] == "first_page"
    assert manager.data["metadata_migration_v1_completed"] is False
    assert manager.metadata_database_path == tmp_path / "data" / "metadata.sqlite3"
    assert manager.data["last_browser_path"] == ""
    assert manager.data["browser_sidebar_visible"] is True
    assert manager.data["browser_sidebar_width"] == 280
    assert manager.data["thumbnail_size"] == 180
    assert manager.data["thumbnail_quality_mode"] == "auto"
    assert manager.data["thumbnail_cache_max_edge"] == 1024
    assert manager.data["browser_sort_key"] == "name"
    assert manager.data["browser_sort_order"] == "ascending"
    assert manager.data["browser_folders_first"] is True
    assert manager.data["browser_display_density"] == "standard"
    assert manager.data["join_spread_pages"] is False
    assert manager.data["thumbnail_disk_cache_enabled"] is True
    assert manager.data["thumbnail_cache_limit_mb"] == 512
    assert manager.data["gap"] == 12
    assert manager.data["viewer_downscale_algorithm"] == "auto"
    assert manager.data["viewer_upscale_algorithm"] == "auto"
    assert manager.data["magnifier_downscale_algorithm"] == "sharp"
    assert manager.data["magnifier_upscale_algorithm"] == "lanczos"
    assert "viewer_resampling_mode" not in manager.data
    assert "magnifier_resampling_mode" not in manager.data
    assert "smooth_scaling" not in manager.data
    assert manager.data["mouse_gestures_enabled"] is True
    assert manager.data["mouse_gesture_show_trail"] is True
    assert manager.data["mouse_gesture_min_distance"] == 36
    assert manager.data["mouse_gesture_bindings"] == {
        "D": "close_viewer",
        "U": "toggle_fullscreen",
    }
    assert manager.data["browser_folder_gestures_enabled"] is True
    assert manager.data["mouse_back_button_action"] == "previous_book"
    assert manager.data["mouse_forward_button_action"] == "next_book"
    assert manager.viewer_memory_mode() == "auto"
    assert manager.viewer_prefetch_settings() == {
        "preset": "standard",
        "direction_priority_enabled": True,
        "image_forward_units": 3,
        "image_backward_units": 3,
        "pdf_forward_units": 3,
        "pdf_backward_units": 3,
    }


def test_partial_config_is_merged_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"view_mode": "single"}', encoding="utf-8")

    loaded = ConfigManager(path).load()

    assert loaded["view_mode"] == "single"
    assert loaded["reading_direction"] == ConfigManager.DEFAULTS["reading_direction"]
    assert loaded["cache_size"] == ConfigManager.DEFAULTS["cache_size"]
    assert loaded["viewer_prefetch_preset"] == "standard"
    assert loaded["book_open_position"] == "first_page"


@pytest.mark.parametrize("value", ["resume_last", "first_page"])
def test_book_open_position_round_trips_and_unknown_values_fall_back(
    tmp_path: Path,
    value: str,
) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    manager.load()
    manager.apply({"book_open_position": value}, save=True)

    assert ConfigManager(path).load()["book_open_position"] == value

    path.write_text('{"book_open_position": "unknown"}', encoding="utf-8")
    assert ConfigManager(path).load()["book_open_position"] == "first_page"


@pytest.mark.parametrize(
    ("preset", "expected"),
    tuple(ConfigManager.VIEWER_PREFETCH_PRESETS.items()),
)
def test_viewer_prefetch_presets_have_requested_values(
    tmp_path: Path,
    preset: str,
    expected: dict[str, int],
) -> None:
    manager = ConfigManager(tmp_path / "config.json")
    manager.load()

    manager.apply({"viewer_prefetch_preset": preset})

    resolved = manager.viewer_prefetch_settings()
    assert resolved["preset"] == preset
    assert {
        key: resolved[key] for key in expected
    } == expected


def test_custom_viewer_prefetch_round_trip_and_clamping(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    manager.load()
    manager.apply(
        {
            "viewer_prefetch_preset": "custom",
            "viewer_prefetch_direction_priority_enabled": False,
            "viewer_prefetch_image_forward_units": -1,
            "viewer_prefetch_image_backward_units": 21,
            "viewer_prefetch_pdf_forward_units": 7,
            "viewer_prefetch_pdf_backward_units": 8,
        },
        save=True,
    )

    restored = ConfigManager(path)
    restored.load()

    assert restored.viewer_prefetch_settings() == {
        "preset": "custom",
        "direction_priority_enabled": False,
        "image_forward_units": 0,
        "image_backward_units": 20,
        "pdf_forward_units": 7,
        "pdf_backward_units": 8,
    }


def test_viewer_memory_mode_migration_is_one_way_on_save(tmp_path: Path) -> None:
    legacy_key = "viewer_cache_max_memory_mib"

    fresh_path = tmp_path / "fresh.json"
    fresh = ConfigManager(fresh_path)
    fresh.load()
    fresh.save()
    assert legacy_key not in json.loads(fresh_path.read_text(encoding="utf-8"))

    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(
        '{"viewer_cache_max_memory_mib": 768}',
        encoding="utf-8",
    )
    legacy = ConfigManager(legacy_path)
    assert legacy.load()["viewer_memory_mode"] == "512"
    assert legacy_key not in legacy.data
    legacy.save()
    assert legacy_key not in json.loads(legacy_path.read_text(encoding="utf-8"))

    mixed_path = tmp_path / "mixed.json"
    mixed_path.write_text(
        '{"viewer_memory_mode": "8192", '
        '"viewer_cache_max_memory_mib": 128}',
        encoding="utf-8",
    )
    mixed = ConfigManager(mixed_path)
    assert mixed.load()["viewer_memory_mode"] == "8192"
    assert legacy_key not in mixed.data
    mixed.save()
    assert legacy_key not in json.loads(mixed_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("legacy_mode", "smooth_scaling", "expected"),
    (
        ("standard", True, ("auto", "auto")),
        ("standard", False, ("fast", "nearest")),
        ("moire_reduction", True, ("area", "bicubic")),
        ("high_quality", True, ("sharp", "lanczos")),
        ("smooth", True, ("smooth", "bilinear")),
        ("pixel", True, ("nearest", "nearest")),
    ),
)
def test_legacy_resampling_mode_is_migrated_to_explicit_algorithms(
    tmp_path: Path,
    legacy_mode: str,
    smooth_scaling: bool,
    expected: tuple[str, str],
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "viewer_resampling_mode": legacy_mode,
                "smooth_scaling": smooth_scaling,
            }
        ),
        encoding="utf-8",
    )

    restored = ConfigManager(path).load()

    assert (
        restored["viewer_downscale_algorithm"],
        restored["viewer_upscale_algorithm"],
    ) == expected
    assert restored["magnifier_downscale_algorithm"] == "sharp"
    assert restored["magnifier_upscale_algorithm"] == "lanczos"
    assert all(
        key not in restored
        for key in ConfigManager._LEGACY_RESAMPLING_KEYS
    )


def test_explicit_resampling_algorithms_win_and_legacy_keys_are_not_resaved(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "viewer_resampling_mode": "high_quality",
                "magnifier_resampling_mode": "pixel",
                "smooth_scaling": False,
                "viewer_downscale_algorithm": "area",
                "magnifier_downscale_algorithm": "smooth",
                "magnifier_upscale_algorithm": "bicubic",
            }
        ),
        encoding="utf-8",
    )
    manager = ConfigManager(path)

    restored = manager.load()

    assert restored["viewer_downscale_algorithm"] == "area"
    assert restored["viewer_upscale_algorithm"] == "lanczos"
    assert restored["magnifier_downscale_algorithm"] == "smooth"
    assert restored["magnifier_upscale_algorithm"] == "bicubic"
    manager.save()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert all(
        key not in persisted
        for key in ConfigManager._LEGACY_RESAMPLING_KEYS
    )


def test_resampling_algorithms_are_normalized_by_scale_direction(
    tmp_path: Path,
) -> None:
    manager = ConfigManager(tmp_path / "config.json")
    manager.load()

    manager.apply(
        {
            "viewer_downscale_algorithm": "bicubic",
            "viewer_upscale_algorithm": "area",
            "magnifier_downscale_algorithm": "nearest",
            "magnifier_upscale_algorithm": "nearest",
        }
    )

    assert manager.get("viewer_downscale_algorithm") == "auto"
    assert manager.get("viewer_upscale_algorithm") == "auto"
    assert manager.get("magnifier_downscale_algorithm") == "nearest"
    assert manager.get("magnifier_upscale_algorithm") == "nearest"


def test_unknown_viewer_memory_mode_falls_back_to_auto(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"viewer_memory_mode": "unknown"}', encoding="utf-8")

    assert ConfigManager(path).load()["viewer_memory_mode"] == "auto"


def test_corrupt_json_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{broken", encoding="utf-8")
    manager = ConfigManager(path)
    manager.set("view_mode", "single")

    assert manager.load() == ConfigManager.DEFAULTS


def test_utf8_japanese_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    value = "C:\\漫画\\雪の本"
    manager = ConfigManager(path)
    manager.save({"last_open_path": value, "bookmarks": {"日本語": [1, 3]}})

    raw = path.read_text(encoding="utf-8")
    restored = ConfigManager(path).load()

    assert "漫画" in raw
    assert "雪の本" in raw
    assert "\\u" not in raw
    assert restored["last_open_path"] == value
    assert restored["bookmarks"] == {"日本語": [1, 3]}


def test_invalid_browser_settings_are_normalized_and_clamped(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"last_browser_path": 12, "browser_sidebar_visible": "yes", '
        '"browser_sidebar_width": -5, "thumbnail_size": 9999, '
        '"browser_sort_key": "invalid", "browser_sort_order": "sideways", '
        '"browser_folders_first": "yes", "browser_display_density": "tiny"}',
        encoding="utf-8",
    )

    restored = ConfigManager(path).load()

    assert restored["last_browser_path"] == ""
    assert restored["browser_sidebar_visible"] is True
    assert restored["browser_sidebar_width"] == 120
    assert restored["thumbnail_size"] == 384
    assert restored["browser_sort_key"] == "name"
    assert restored["browser_sort_order"] == "ascending"
    assert restored["browser_folders_first"] is True
    assert restored["browser_display_density"] == "standard"


def test_thumbnail_size_has_safe_lower_bound(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "config.json")
    manager.load()

    manager.apply({"thumbnail_size": -20})

    assert manager.get("thumbnail_size") == 96


def test_thumbnail_quality_settings_are_normalized_and_persisted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    manager.load()
    manager.apply(
        {
            "thumbnail_quality_mode": "high",
            "thumbnail_cache_max_edge": 1536,
        },
        save=True,
    )
    restored = ConfigManager(path).load()
    assert restored["thumbnail_quality_mode"] == "high"
    assert restored["thumbnail_cache_max_edge"] == 1536

    manager.apply(
        {
            "thumbnail_quality_mode": "unknown",
            "thumbnail_cache_max_edge": 99999,
        }
    )
    assert manager.get("thumbnail_quality_mode") == "auto"
    assert manager.get("thumbnail_cache_max_edge") == 2048


def test_browser_setting_values_are_not_shared_between_instances(
    tmp_path: Path,
) -> None:
    first = ConfigManager(tmp_path / "first.json")
    second = ConfigManager(tmp_path / "second.json")
    first.load()
    second.load()

    first.apply(
        {
            "browser_sort_key": "file_size",
            "browser_display_density": "compact",
        }
    )

    assert second.get("browser_sort_key") == "name"
    assert second.get("browser_display_density") == "standard"


def test_sprint4_settings_are_normalized_and_changes_are_emitted(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"open_viewer_behavior": "bad", "gap": -20, '
        '"thumbnail_cache_limit_mb": 99999, "join_spread_pages": "yes"}',
        encoding="utf-8",
    )
    manager = ConfigManager(path)
    restored = manager.load()
    changes: list[dict[str, object]] = []
    manager.settings_changed.connect(changes.append)

    assert restored["open_viewer_behavior"] == "reuse_or_create"
    assert restored["gap"] == 0
    assert restored["thumbnail_cache_limit_mb"] == 4096
    assert restored["join_spread_pages"] is False

    manager.apply({"gap": 25, "join_spread_pages": True})

    assert changes == [{"gap": 25, "join_spread_pages": True}]


def test_mouse_settings_are_normalized_and_unknown_commands_are_disabled(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"mouse_gestures_enabled": "yes", '
        '"mouse_gesture_show_trail": 1, '
        '"mouse_gesture_min_distance": 999, '
        '"mouse_gesture_bindings": {'
        '"D": "close_viewer", "UX": "next_page", "L": "unknown"}, '
        '"mouse_back_button_action": "unknown", '
        '"mouse_forward_button_action": "next_book"}',
        encoding="utf-8",
    )

    restored = ConfigManager(path).load()

    assert restored["mouse_gestures_enabled"] is True
    assert restored["mouse_gesture_show_trail"] is True
    assert restored["mouse_gesture_min_distance"] == 200
    assert restored["mouse_gesture_bindings"] == {"D": "close_viewer"}
    assert restored["mouse_back_button_action"] == ""
    assert restored["mouse_forward_button_action"] == "next_book"


def test_mouse_binding_defaults_are_not_shared(tmp_path: Path) -> None:
    first = ConfigManager(tmp_path / "first.json")
    second = ConfigManager(tmp_path / "second.json")
    first.load()
    second.load()

    first.data["mouse_gesture_bindings"]["D"] = "next_page"

    assert second.data["mouse_gesture_bindings"]["D"] == "close_viewer"
    assert ConfigManager.DEFAULTS["mouse_gesture_bindings"]["D"] == "close_viewer"


def test_saved_partial_mouse_bindings_are_not_filled_with_new_defaults(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"mouse_gesture_bindings": {"U": "next_page", "DR": "last_page"}}',
        encoding="utf-8",
    )

    restored = ConfigManager(path).load()

    assert restored["mouse_gesture_bindings"] == {
        "U": "next_page",
        "DR": "last_page",
    }


def test_viewer_canvas_side_and_slider_wheel_settings_are_normalized(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"viewer_canvas_click_direction": "diagonal", '
        '"viewer_canvas_left_click_action": "invalid", '
        '"viewer_slider_wheel_single_page_enabled": "yes"}',
        encoding="utf-8",
    )

    restored = ConfigManager(path).load()

    assert restored["viewer_canvas_click_direction"] == "right_next"
    assert restored["viewer_canvas_left_click_action"] == "next_single_page"
    assert restored["viewer_slider_wheel_single_page_enabled"] is False
