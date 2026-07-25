from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog

from app.browser_model import BrowserItem, BrowserItemKind
from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider


def make_config(tmp_path: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return config


def test_current_values_are_shown_and_join_disables_gap(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    config.apply(
        {
            "open_viewer_behavior": "always_new",
            "gap": 33,
            "join_spread_pages": True,
            "thumbnail_size": 240,
            "thumbnail_frame_ratio": "landscape_16_9",
            "thumbnail_crop_mode": "center_crop",
            "browser_display_density": "comfortable",
            "browser_sort_key": "modified_time",
            "browser_sort_order": "descending",
            "browser_folders_first": False,
        }
    )

    dialog = SettingsDialog(config)

    assert dialog.open_behavior_combo.currentData() == "always_new"
    assert dialog.gap_spin.value() == 33
    assert dialog.join_spread_checkbox.isChecked()
    assert not dialog.gap_spin.isEnabled()
    assert dialog.thumbnail_size_spin.value() == 240
    assert dialog.thumbnail_frame_ratio_combo.currentData() == "landscape_16_9"
    assert dialog.thumbnail_crop_mode_combo.currentData() == "center_crop"
    assert dialog.browser_display_density_combo.currentData() == "comfortable"
    assert dialog.browser_sort_key_combo.currentData() == "modified_time"
    assert dialog.browser_sort_order_combo.currentData() == "descending"
    assert not dialog.browser_folders_first_checkbox.isChecked()
    dialog.reject()


def test_apply_and_ok_persist_settings(tmp_path: Path, qapp: QApplication) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)
    dialog.open_behavior_combo.setCurrentIndex(
        dialog.open_behavior_combo.findData("reuse_active")
    )
    dialog.gap_spin.setValue(45)
    dialog.thumbnail_size_spin.setValue(260)
    dialog.thumbnail_frame_ratio_combo.setCurrentIndex(
        dialog.thumbnail_frame_ratio_combo.findData("portrait_2_3")
    )
    dialog.thumbnail_crop_mode_combo.setCurrentIndex(
        dialog.thumbnail_crop_mode_combo.findData("letterbox")
    )
    dialog.browser_display_density_combo.setCurrentIndex(
        dialog.browser_display_density_combo.findData("compact")
    )
    dialog.browser_sort_key_combo.setCurrentIndex(
        dialog.browser_sort_key_combo.findData("file_size")
    )
    dialog.browser_sort_order_combo.setCurrentIndex(
        dialog.browser_sort_order_combo.findData("descending")
    )
    dialog.browser_folders_first_checkbox.setChecked(False)

    changed = dialog.apply_settings()

    assert changed["open_viewer_behavior"] == "reuse_active"
    assert changed["gap"] == 45
    assert ConfigManager(config.path).load()["thumbnail_size"] == 260
    assert changed["thumbnail_frame_ratio"] == "portrait_2_3"
    assert changed["thumbnail_crop_mode"] == "letterbox"
    assert changed["browser_display_density"] == "compact"
    assert changed["browser_sort_key"] == "file_size"
    assert changed["browser_sort_order"] == "descending"
    assert changed["browser_folders_first"] is False

    dialog.join_spread_checkbox.setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert ConfigManager(config.path).load()["join_spread_pages"] is True


def test_cancel_does_not_apply_changes(tmp_path: Path, qapp: QApplication) -> None:
    config = make_config(tmp_path)
    original_gap = config.get("gap")
    dialog = SettingsDialog(config)
    dialog.gap_spin.setValue(99)
    dialog.browser_display_density_combo.setCurrentIndex(
        dialog.browser_display_density_combo.findData("comfortable")
    )

    dialog.reject()

    assert config.get("gap") == original_gap
    assert config.get("browser_display_density") == "standard"
    assert not config.path.exists()


def test_density_apply_does_not_request_cache_clear(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    dialog = SettingsDialog(make_config(tmp_path))
    cache_clear_requests: list[bool] = []
    dialog.cache_clear_requested.connect(
        lambda: cache_clear_requests.append(True)
    )
    dialog.browser_display_density_combo.setCurrentIndex(
        dialog.browser_display_density_combo.findData("comfortable")
    )

    dialog.apply_settings()

    assert cache_clear_requests == []
    assert dialog.config.get("browser_display_density") == "comfortable"
    dialog.reject()


def test_browser_grid_presets_apply_density_and_thumbnail_size(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)
    large_index = dialog.browser_grid_preset_combo.findData("large")

    assert dialog.browser_grid_preset_combo.count() == 5
    assert dialog.browser_grid_preset_combo.findData("extra_compact") >= 0
    assert large_index >= 0
    dialog.browser_grid_preset_combo.setCurrentIndex(large_index)
    dialog.browser_grid_preset_combo.activated.emit(large_index)
    changed = dialog.apply_settings()

    assert dialog.browser_display_density_combo.currentData() == "large"
    assert dialog.thumbnail_size_spin.value() == 320
    assert changed["browser_display_density"] == "large"
    restored = ConfigManager(config.path).load()
    assert restored["browser_display_density"] == "large"
    assert restored["thumbnail_size"] == 320
    dialog.reject()


def test_cache_clear_button_emits_request(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    dialog = SettingsDialog(make_config(tmp_path), cache_usage_getter=lambda: 2048)
    requests: list[bool] = []
    dialog.cache_clear_requested.connect(lambda: requests.append(True))

    dialog.request_cache_clear(confirm=False)

    assert requests == [True]
    assert "要求" in dialog.cache_usage_label.text()
    dialog.reject()


def test_cache_clear_request_reaches_disk_cache(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = tmp_path / "page.jpg"
    with Image.new("RGB", (16, 24), "white") as image:
        image.save(source)
    item = BrowserItem(
        source.name,
        source,
        BrowserItemKind.IMAGE,
        source.stat().st_mtime,
    )
    disk_cache = ThumbnailDiskCache(tmp_path / "cache")
    assert disk_cache.put(
        item,
        180,
        QImage(16, 16, QImage.Format.Format_RGBA8888),
    )
    provider = BrowserThumbnailProvider(
        disk_cache=disk_cache,
        disk_cache_enabled=True,
    )
    dialog = SettingsDialog(
        make_config(tmp_path),
        cache_usage_getter=provider.disk_cache_usage_bytes,
    )
    dialog.cache_clear_requested.connect(provider.clear_all_caches_async)

    dialog.request_cache_clear(confirm=False)
    assert provider.wait_for_done(2000)
    qapp.processEvents()

    assert provider.disk_cache_usage_bytes() == 0
    provider.close()
    dialog.reject()


def test_mouse_settings_are_shown_applied_and_disableable(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)

    assert dialog.mouse_gestures_checkbox.isChecked()
    assert dialog.mouse_gesture_trail_checkbox.isChecked()
    assert dialog.mouse_gesture_distance_spin.value() == 36
    assert dialog.gesture_down_combo.currentData() == "close_viewer"
    assert dialog.gesture_up_combo.currentData() == "toggle_fullscreen"
    assert dialog.mouse_back_action_combo.currentData() == "previous_book"
    assert dialog.mouse_forward_action_combo.currentData() == "next_book"

    dialog.mouse_gestures_checkbox.setChecked(False)
    dialog.mouse_gesture_distance_spin.setValue(88)
    dialog.gesture_down_combo.setCurrentIndex(
        dialog.gesture_down_combo.findData("next_page")
    )
    dialog.mouse_back_action_combo.setCurrentIndex(0)
    changed = dialog.apply_settings()

    assert changed["mouse_gestures_enabled"] is False
    assert changed["mouse_gesture_min_distance"] == 88
    assert changed["mouse_gesture_bindings"]["D"] == "next_page"
    assert changed["mouse_back_button_action"] == ""
    assert not dialog.gesture_down_combo.isEnabled()
    restored = ConfigManager(config.path).load()
    assert restored["mouse_gesture_bindings"]["D"] == "next_page"
    dialog.reject()


def test_cancel_does_not_save_mouse_settings(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)
    dialog.mouse_gestures_checkbox.setChecked(False)
    dialog.mouse_gesture_distance_spin.setValue(120)

    dialog.reject()

    assert config.get("mouse_gestures_enabled") is True
    assert config.get("mouse_gesture_min_distance") == 36
