from __future__ import annotations

from pathlib import Path

from app.config_manager import ConfigManager


def test_missing_config_uses_defaults(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "config.json")

    assert manager.load() == ConfigManager.DEFAULTS
    assert manager.data is not ConfigManager.DEFAULTS
    assert manager.data["open_viewer_behavior"] == "reuse_or_create"
    assert manager.data["loop_book_navigation"] is False
    assert manager.data["bring_viewer_to_front_on_open"] is True
    assert manager.data["restore_last_reading_position"] is True
    assert manager.data["metadata_migration_v1_completed"] is False
    assert manager.metadata_database_path == tmp_path / "data" / "metadata.sqlite3"
    assert manager.data["last_browser_path"] == ""
    assert manager.data["browser_sidebar_visible"] is True
    assert manager.data["browser_sidebar_width"] == 280
    assert manager.data["thumbnail_size"] == 180
    assert manager.data["browser_sort_key"] == "name"
    assert manager.data["browser_sort_order"] == "ascending"
    assert manager.data["browser_folders_first"] is True
    assert manager.data["browser_display_density"] == "standard"
    assert manager.data["join_spread_pages"] is False
    assert manager.data["thumbnail_disk_cache_enabled"] is True
    assert manager.data["thumbnail_cache_limit_mb"] == 512
    assert manager.data["gap"] == 12
    assert manager.data["mouse_gestures_enabled"] is True
    assert manager.data["mouse_gesture_show_trail"] is True
    assert manager.data["mouse_gesture_min_distance"] == 36
    assert manager.data["mouse_gesture_bindings"] == {
        "D": "close_viewer",
        "U": "toggle_fullscreen",
    }
    assert manager.data["mouse_back_button_action"] == "previous_book"
    assert manager.data["mouse_forward_button_action"] == "next_book"


def test_partial_config_is_merged_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"view_mode": "single"}', encoding="utf-8")

    loaded = ConfigManager(path).load()

    assert loaded["view_mode"] == "single"
    assert loaded["reading_direction"] == ConfigManager.DEFAULTS["reading_direction"]
    assert loaded["cache_size"] == ConfigManager.DEFAULTS["cache_size"]


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
