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
    assert manager.data["last_browser_path"] == ""
    assert manager.data["browser_sidebar_visible"] is True
    assert manager.data["browser_sidebar_width"] == 280
    assert manager.data["thumbnail_size"] == 180


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
        '"browser_sidebar_width": -5, "thumbnail_size": 9999}',
        encoding="utf-8",
    )

    restored = ConfigManager(path).load()

    assert restored["last_browser_path"] == ""
    assert restored["browser_sidebar_visible"] is True
    assert restored["browser_sidebar_width"] == 120
    assert restored["thumbnail_size"] == 500
