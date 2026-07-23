from __future__ import annotations

from pathlib import Path

from app.config_manager import ConfigManager


def test_missing_config_uses_defaults(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "config.json")

    assert manager.load() == ConfigManager.DEFAULTS
    assert manager.data is not ConfigManager.DEFAULTS


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
