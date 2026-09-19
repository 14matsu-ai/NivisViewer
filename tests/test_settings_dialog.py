from __future__ import annotations

from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QPushButton, QWidget

from app.browser_model import BrowserItem, BrowserItemKind
from app.config_manager import ConfigManager
from app.seven_zip_locator import SevenZipInfo
from app.settings_dialog import SettingsDialog, _RETIRED_SETTINGS_DIALOGS
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider
from app.viewer_render import (
    DOWNSCALE_ALGORITHM_LABELS,
    UPSCALE_ALGORITHM_LABELS,
)


def make_config(tmp_path: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return config


def flush_deferred_deletes(qapp: QApplication) -> None:
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


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
            "browser_thumbnail_display_mode": "center_crop",
            "browser_folder_fallback_background": "#31597d",
            "thumbnail_quality_mode": "high",
            "thumbnail_cache_max_edge": 1536,
            "browser_display_density": "comfortable",
            "browser_sort_key": "modified_time",
            "browser_sort_order": "descending",
            "browser_folders_first": False,
            "browser_location_history_limit": 137,
            "browser_search_history_limit": 333,
            "browser_preserve_search_for_viewer_roundtrip": False,
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
    assert (
        dialog.browser_thumbnail_display_mode_combo.currentData()
        == "center_crop"
    )
    assert dialog.browser_folder_fallback_background_combo.currentData() == "custom"
    assert dialog._folder_fallback_custom_color == "#31597d"
    assert dialog.thumbnail_quality_mode_combo.currentData() == "high"
    assert dialog.thumbnail_cache_max_edge_spin.value() == 1536
    assert dialog.browser_display_density_combo.currentData() == "comfortable"
    assert dialog.browser_sort_key_combo.currentData() == "modified_time:descending"
    assert not hasattr(dialog, "browser_sort_order_combo")
    assert not dialog.browser_folders_first_checkbox.isChecked()
    assert dialog.browser_location_history_limit_spin.value() == 137
    assert dialog.browser_search_history_limit_spin.value() == 333
    assert not (
        dialog.browser_preserve_search_for_viewer_roundtrip_checkbox.isChecked()
    )
    assert (
        dialog.browser_preserve_search_for_viewer_roundtrip_checkbox.text()
        == "検索結果からViewerを開いたとき、戻るまで検索を維持"
    )
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
    dialog.browser_thumbnail_display_mode_combo.setCurrentIndex(
        dialog.browser_thumbnail_display_mode_combo.findData("center_crop")
    )
    dialog.browser_folder_fallback_background_combo.setCurrentIndex(
        dialog.browser_folder_fallback_background_combo.findData("custom")
    )
    dialog._folder_fallback_custom_color = "#31597d"
    dialog._sync_folder_fallback_background_controls()
    dialog.thumbnail_quality_mode_combo.setCurrentIndex(
        dialog.thumbnail_quality_mode_combo.findData("economy")
    )
    dialog.thumbnail_cache_max_edge_spin.setValue(768)
    dialog.browser_display_density_combo.setCurrentIndex(
        dialog.browser_display_density_combo.findData("compact")
    )
    dialog.browser_sort_key_combo.setCurrentIndex(
        dialog.browser_sort_key_combo.findData("file_size:descending")
    )
    dialog.browser_folders_first_checkbox.setChecked(False)
    dialog.browser_location_history_limit_spin.setValue(217)
    dialog.browser_search_history_limit_spin.setValue(0)
    dialog.browser_preserve_search_for_viewer_roundtrip_checkbox.setChecked(False)

    changed = dialog.apply_settings()

    assert changed["open_viewer_behavior"] == "reuse_active"
    assert changed["gap"] == 45
    assert ConfigManager(config.path).load()["thumbnail_size"] == 260
    assert changed["thumbnail_frame_ratio"] == "portrait_2_3"
    assert changed["thumbnail_crop_mode"] == "letterbox"
    assert changed["browser_thumbnail_display_mode"] == "center_crop"
    assert changed["browser_folder_fallback_background"] == "#31597d"
    assert changed["thumbnail_quality_mode"] == "economy"
    assert changed["thumbnail_cache_max_edge"] == 768
    assert changed["browser_display_density"] == "compact"
    assert changed["browser_sort_key"] == "file_size"
    assert changed["browser_sort_order"] == "descending"
    assert changed["browser_folders_first"] is False
    assert changed["browser_location_history_limit"] == 217
    assert changed["browser_search_history_limit"] == 0
    assert changed["browser_preserve_search_for_viewer_roundtrip"] is False

    dialog.join_spread_checkbox.setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    restored = ConfigManager(config.path).load()
    assert restored["join_spread_pages"] is True
    assert restored["browser_thumbnail_display_mode"] == "center_crop"
    assert restored["browser_folder_fallback_background"] == "#31597d"
    assert restored["browser_preserve_search_for_viewer_roundtrip"] is False
    reopened_config = ConfigManager(config.path)
    reopened_config.load()
    reopened = SettingsDialog(reopened_config)
    assert (
        reopened.browser_thumbnail_display_mode_combo.currentData()
        == "center_crop"
    )
    assert reopened.browser_folder_fallback_background_combo.currentData() == "custom"
    reopened.reject()


def test_folder_fallback_background_restore_default_uses_auto(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    del qapp
    config = make_config(tmp_path)
    config.apply({"browser_folder_fallback_background": "#31597d"})
    dialog = SettingsDialog(config)

    assert dialog.browser_folder_fallback_background_combo.currentData() == "custom"
    assert dialog.browser_folder_fallback_color_button.isEnabled()
    dialog.browser_folder_fallback_restore_button.click()

    assert dialog.browser_folder_fallback_background_combo.currentData() == "auto"
    assert not dialog.browser_folder_fallback_color_button.isEnabled()
    assert dialog.values()["browser_folder_fallback_background"] == "auto"
    assert dialog.browser_folder_fallback_color_button.text() == "#FFFFE0"
    dialog.reject()


def test_folder_snapshot_cache_controls_persist_and_disable_cap(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    del qapp
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)

    assert dialog.browser_folder_snapshot_cache_checkbox.text() == (
        "フォルダ一覧をメモリに一時保存する"
    )
    expected_tooltip = (
        "ファイル数の多いフォルダの再表示を高速化します。\n"
        "前回の一覧をメモリから先に表示し、あとで変更を確認します。\n"
        "HDDや大量ファイルのフォルダで特に効果的です。"
    )
    assert dialog.browser_folder_snapshot_cache_checkbox.toolTip() == (
        expected_tooltip
    )
    assert dialog.browser_folder_snapshot_cache_help_button.toolTip() == (
        expected_tooltip
    )
    assert (
        dialog.browser_folder_snapshot_cache_help_button.parentWidget()
        is dialog.browser_folder_snapshot_cache_checkbox_row
    )
    assert dialog.browser_folder_snapshot_cache_max_entries_combo.currentData() == (
        60_000
    )
    assert dialog.browser_folder_snapshot_cache_max_entries_combo.isEnabled()
    assert dialog.browser_folder_snapshot_cache_max_entries_combo.itemText(
        dialog.browser_folder_snapshot_cache_max_entries_combo.findData(60_000)
    ) == "60,000項目（推定約46MiB）"
    popup_calls: list[tuple[str, str]] = []
    with patch(
        "app.settings_dialog.QMessageBox.information",
        side_effect=lambda _parent, title, text: popup_calls.append(
            (str(title), str(text))
        ),
    ):
        dialog.browser_folder_snapshot_cache_help_button.click()
    assert popup_calls
    assert "画像サムネイルではなく" in popup_calls[0][1]
    assert "再表示するときの待ち時間を短くする" in popup_calls[0][1]
    assert "HDDや大量ファイルのフォルダで特に効果的" in popup_calls[0][1]

    dialog.browser_folder_snapshot_cache_checkbox.setChecked(False)
    assert not dialog.browser_folder_snapshot_cache_max_entries_combo.isEnabled()
    dialog.browser_folder_snapshot_cache_max_entries_combo.setCurrentIndex(
        dialog.browser_folder_snapshot_cache_max_entries_combo.findData(120_000)
    )
    changed = dialog.apply_settings()

    assert changed["browser_folder_snapshot_cache_enabled"] is False
    assert changed["browser_folder_snapshot_cache_max_entries"] == 120_000
    restored = ConfigManager(config.path).load()
    assert restored["browser_folder_snapshot_cache_max_entries"] == 120_000
    dialog.reject()


def test_delete_confirmation_settings_are_in_file_operations_and_persist_live(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    del qapp
    config = make_config(tmp_path)
    published: list[dict[str, object]] = []
    config.settings_changed.connect(published.append)
    dialog = SettingsDialog(config)

    assert config.get("file_operation_delete_confirm_focus_yes") is False
    assert config.get("file_operation_delete_skip_confirmation") is False
    assert any(
        dialog.tabs.tabText(index) == "ファイル"
        for index in range(dialog.tabs.count())
    )
    assert dialog.file_operation_group.title() == "ファイル操作"
    assert dialog.delete_confirm_focus_yes_checkbox.text() == (
        "削除確認で「はい」を初期選択"
    )
    assert dialog.delete_skip_confirmation_checkbox.text() == (
        "削除確認を表示せずゴミ箱へ移動"
    )
    assert dialog.delete_confirm_focus_yes_checkbox.isEnabled()

    dialog.delete_confirm_focus_yes_checkbox.setChecked(True)
    dialog.delete_skip_confirmation_checkbox.setChecked(True)
    assert not dialog.delete_confirm_focus_yes_checkbox.isEnabled()
    assert dialog.delete_confirm_focus_yes_checkbox.isChecked()

    changed = dialog.apply_settings()
    assert changed["file_operation_delete_confirm_focus_yes"] is True
    assert changed["file_operation_delete_skip_confirmation"] is True
    assert published[-1] == changed
    assert config.get("file_operation_delete_confirm_focus_yes") is True
    assert config.get("file_operation_delete_skip_confirmation") is True
    dialog.reject()

    reopened_config = ConfigManager(config.path)
    reopened_config.load()
    reopened = SettingsDialog(reopened_config)
    assert reopened.delete_skip_confirmation_checkbox.isChecked()
    assert reopened.delete_confirm_focus_yes_checkbox.isChecked()
    assert not reopened.delete_confirm_focus_yes_checkbox.isEnabled()
    reopened.delete_skip_confirmation_checkbox.setChecked(False)
    assert reopened.delete_confirm_focus_yes_checkbox.isEnabled()
    assert reopened.delete_confirm_focus_yes_checkbox.isChecked()
    reopened.reject()


def test_resampling_controls_expose_backend_authority_and_apply(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    config.apply(
        {
            "viewer_downscale_algorithm": "smooth",
            "viewer_upscale_algorithm": "bilinear",
            "magnifier_downscale_algorithm": "area",
            "magnifier_upscale_algorithm": "nearest",
        }
    )
    published: list[dict[str, object]] = []
    config.settings_changed.connect(published.append)
    dialog = SettingsDialog(config)

    def combo_items(combo) -> dict[str, str]:
        return {
            str(combo.itemData(index)): combo.itemText(index)
            for index in range(combo.count())
        }

    assert combo_items(
        dialog.viewer_downscale_algorithm_combo
    ) == DOWNSCALE_ALGORITHM_LABELS
    assert combo_items(
        dialog.viewer_upscale_algorithm_combo
    ) == UPSCALE_ALGORITHM_LABELS
    assert dialog.viewer_downscale_algorithm_combo.currentData() == "smooth"
    assert dialog.viewer_upscale_algorithm_combo.currentData() == "bilinear"
    assert dialog.magnifier_downscale_algorithm_combo.currentData() == "area"
    assert dialog.magnifier_upscale_algorithm_combo.currentData() == "nearest"

    dialog.viewer_downscale_algorithm_combo.setCurrentIndex(
        dialog.viewer_downscale_algorithm_combo.findData("sharp")
    )
    dialog.viewer_upscale_algorithm_combo.setCurrentIndex(
        dialog.viewer_upscale_algorithm_combo.findData("lanczos")
    )
    dialog.magnifier_downscale_algorithm_combo.setCurrentIndex(
        dialog.magnifier_downscale_algorithm_combo.findData("fast")
    )
    dialog.magnifier_upscale_algorithm_combo.setCurrentIndex(
        dialog.magnifier_upscale_algorithm_combo.findData("bicubic")
    )
    changed = dialog.apply_settings()

    assert changed["viewer_downscale_algorithm"] == "sharp"
    assert changed["viewer_upscale_algorithm"] == "lanczos"
    assert changed["magnifier_downscale_algorithm"] == "fast"
    assert changed["magnifier_upscale_algorithm"] == "bicubic"
    assert published[-1] == changed
    restored = ConfigManager(config.path).load()
    assert restored["viewer_downscale_algorithm"] == "sharp"
    assert restored["viewer_upscale_algorithm"] == "lanczos"
    assert restored["magnifier_downscale_algorithm"] == "fast"
    assert restored["magnifier_upscale_algorithm"] == "bicubic"
    dialog.reject()


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


def test_viewer_prefetch_presets_and_custom_controls(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    config.apply(
        {
            "viewer_prefetch_image_forward_units": 9,
            "viewer_prefetch_image_backward_units": 8,
            "viewer_prefetch_pdf_forward_units": 7,
            "viewer_prefetch_pdf_backward_units": 6,
        }
    )
    dialog = SettingsDialog(config)

    assert [
        dialog.prefetch_preset_combo.itemText(index)
        for index in range(dialog.prefetch_preset_combo.count())
    ] == ["無効", "省メモリ", "標準", "多め", "カスタム"]
    assert dialog.prefetch_preset_combo.currentData() == "standard"
    assert dialog.prefetch_direction_priority_checkbox.isChecked()
    assert not dialog.prefetch_custom_group.isEnabled()
    assert dialog.prefetch_image_forward_spin.value() == 3
    assert dialog.prefetch_image_backward_spin.value() == 3
    assert dialog.prefetch_pdf_forward_spin.value() == 3
    assert dialog.prefetch_pdf_backward_spin.value() == 3
    assert [
        dialog.viewer_memory_mode_combo.itemText(index)
        for index in range(dialog.viewer_memory_mode_combo.count())
    ] == [
        "最小限",
        "256 MB前後",
        "512 MB前後",
        "1 GB前後",
        "2 GB前後",
        "4 GB前後",
        "8 GB前後",
        "16 GB前後",
        "32 GB前後",
        "自動",
    ]
    assert dialog.viewer_memory_mode_combo.currentData() == "auto"

    more_index = dialog.prefetch_preset_combo.findData("more")
    dialog.prefetch_preset_combo.setCurrentIndex(more_index)
    assert dialog.prefetch_image_forward_spin.value() == 6
    assert dialog.prefetch_image_backward_spin.value() == 2
    assert dialog.prefetch_pdf_forward_spin.value() == 4
    assert dialog.prefetch_pdf_backward_spin.value() == 1
    assert dialog.viewer_memory_mode_combo.currentData() == "auto"
    assert not dialog.prefetch_custom_group.isEnabled()

    custom_index = dialog.prefetch_preset_combo.findData("custom")
    dialog.prefetch_preset_combo.setCurrentIndex(custom_index)

    assert dialog.prefetch_custom_group.isEnabled()
    assert dialog.prefetch_image_forward_spin.value() == 9
    assert dialog.prefetch_image_backward_spin.value() == 8
    assert dialog.prefetch_pdf_forward_spin.value() == 7
    assert dialog.prefetch_pdf_backward_spin.value() == 6
    dialog.reject()


def test_book_open_position_combo_defaults_and_applies_resume(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)

    assert [
        dialog.book_open_position_combo.itemText(index)
        for index in range(dialog.book_open_position_combo.count())
    ] == ["常に先頭ページから開く", "前回閉じたページから再開"]
    assert dialog.book_open_position_combo.currentData() == "first_page"

    dialog.book_open_position_combo.setCurrentIndex(
        dialog.book_open_position_combo.findData("resume_last")
    )
    changed = dialog.apply_settings()
    assert changed["book_open_position"] == "resume_last"
    assert config.get("book_open_position") == "resume_last"
    dialog.reject()


def test_custom_viewer_prefetch_applies_and_cancel_does_not_save(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)
    dialog.prefetch_preset_combo.setCurrentIndex(
        dialog.prefetch_preset_combo.findData("custom")
    )
    dialog.prefetch_direction_priority_checkbox.setChecked(False)
    dialog.prefetch_image_forward_spin.setValue(4)
    dialog.prefetch_image_backward_spin.setValue(2)
    dialog.prefetch_pdf_forward_spin.setValue(5)
    dialog.prefetch_pdf_backward_spin.setValue(1)
    dialog.viewer_memory_mode_combo.setCurrentIndex(
        dialog.viewer_memory_mode_combo.findData("4096")
    )

    changed = dialog.apply_settings()

    assert changed["viewer_prefetch_preset"] == "custom"
    assert changed["viewer_prefetch_direction_priority_enabled"] is False
    assert config.viewer_prefetch_settings()["image_forward_units"] == 4
    assert config.viewer_prefetch_settings()["pdf_backward_units"] == 1
    assert config.viewer_memory_mode() == "4096"

    dialog.prefetch_image_forward_spin.setValue(20)
    dialog.reject()
    assert config.get("viewer_prefetch_image_forward_units") == 4


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
    medium_index = dialog.browser_grid_preset_combo.findData("medium")

    assert [
        dialog.browser_grid_preset_combo.itemData(index)
        for index in range(dialog.browser_grid_preset_combo.count())
    ] == [
        "extra_compact",
        "compact",
        "medium",
        "standard",
        "comfortable",
        "large",
    ]
    assert dialog.browser_grid_preset_combo.itemText(medium_index) == "中 (149px)"
    dialog.browser_grid_preset_combo.setCurrentIndex(medium_index)
    dialog.browser_grid_preset_combo.activated.emit(medium_index)
    changed = dialog.apply_settings()

    assert dialog.browser_display_density_combo.currentData() == "medium"
    assert dialog.thumbnail_size_spin.value() == 149
    assert changed["browser_display_density"] == "medium"
    restored = ConfigManager(config.path).load()
    assert restored["browser_display_density"] == "medium"
    assert restored["thumbnail_size"] == 149
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


def test_cache_usage_layout_keeps_one_row_and_has_no_manual_cleanup(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    dialog = SettingsDialog(
        make_config(tmp_path),
        cache_statistics_getter=lambda: {
            "usage_bytes": 123456789,
            "entry_count": 123456,
            "session_growth_bytes": 9876543,
            "last_cleanup_display": "2026-09-20 12:34",
        },
    )
    dialog.resize(420, 680)
    dialog.show()
    dialog.tabs.setCurrentIndex(1)
    browser_tab = dialog.tabs.currentWidget()
    browser_tab.ensureWidgetVisible(dialog.clear_cache_button)
    qapp.processEvents()

    assert not hasattr(dialog, "cleanup_cache_button")
    assert not hasattr(SettingsDialog, "cache_cleanup_requested")
    assert all(
        button.text() != "今すぐ整理"
        for button in dialog.findChildren(QPushButton)
    )
    assert dialog.cache_usage_label.wordWrap()
    usage_row = dialog.cache_usage_label.parentWidget()
    assert usage_row is not None
    assert usage_row.layout().count() == 2
    assert not dialog.cache_usage_label.geometry().intersects(
        dialog.clear_cache_button.geometry()
    )
    assert (
        dialog.cache_usage_label.geometry().right()
        < dialog.clear_cache_button.geometry().left()
    )
    assert dialog.clear_cache_button.isVisible()
    assert "最後の整理:" in dialog.cache_usage_label.toolTip()
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
    assert (
        dialog.mouse_gestures_checkbox.text()
        == "Viewer画像表示領域でマウスジェスチャーを使用する"
    )
    assert dialog.browser_folder_gestures_checkbox.isChecked()
    assert dialog.mouse_gesture_trail_checkbox.isChecked()
    assert dialog.mouse_gesture_distance_spin.value() == 36
    assert dialog.gesture_down_combo.currentData() == "close_viewer"
    assert dialog.gesture_up_combo.currentData() == "toggle_fullscreen"
    assert dialog.mouse_back_action_combo.currentData() == "previous_book"
    assert dialog.mouse_forward_action_combo.currentData() == "next_book"

    dialog.mouse_gestures_checkbox.setChecked(False)
    dialog.browser_folder_gestures_checkbox.setChecked(False)
    dialog.mouse_gesture_distance_spin.setValue(88)
    dialog.gesture_down_combo.setCurrentIndex(
        dialog.gesture_down_combo.findData("next_page")
    )
    dialog.mouse_back_action_combo.setCurrentIndex(0)
    changed = dialog.apply_settings()

    assert changed["mouse_gestures_enabled"] is False
    assert changed["browser_folder_gestures_enabled"] is False
    assert changed["mouse_gesture_min_distance"] == 88
    assert changed["mouse_gesture_bindings"]["D"] == "next_page"
    assert changed["mouse_back_button_action"] == ""
    assert not dialog.gesture_down_combo.isEnabled()
    restored = ConfigManager(config.path).load()
    assert restored["mouse_gesture_bindings"]["D"] == "next_page"
    assert restored["browser_folder_gestures_enabled"] is False
    dialog.reject()


def test_mouse_settings_preserve_saved_partial_and_multistroke_bindings(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    config.apply(
        {
            "mouse_gesture_bindings": {
                "U": "next_page",
                "DR": "last_page",
            }
        }
    )
    dialog = SettingsDialog(config)

    assert dialog.gesture_up_combo.currentData() == "next_page"
    assert dialog.gesture_down_combo.currentData() == ""

    changed = dialog.apply_settings()

    assert "mouse_gesture_bindings" not in changed
    assert config.get("mouse_gesture_bindings") == {
        "U": "next_page",
        "DR": "last_page",
    }
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


def test_viewer_canvas_click_controls_use_requested_labels(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)

    assert dialog.viewer_canvas_click_direction_combo.currentData() == "auto"
    assert dialog.viewer_canvas_left_click_combo.currentData() == "next_single_page"
    assert not dialog.viewer_slider_wheel_single_page_checkbox.isChecked()
    assert [
        dialog.viewer_canvas_left_click_combo.itemText(index)
        for index in range(dialog.viewer_canvas_left_click_combo.count())
    ] == [
        "クリックでページ移動しない",
        "1ページずつ移動",
        "現在のページ送り単位で移動",
    ]
    assert (
        dialog.viewer_slider_wheel_single_page_checkbox.text()
        == "下部UI上のマウスホイールで1ページずつ移動する"
    )
    dialog.reject()


def test_repeated_close_deletes_each_dialog_from_parent(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    parent = QWidget()

    for _ in range(5):
        dialog = SettingsDialog(config, parent)
        assert len(parent.findChildren(SettingsDialog)) == 1

        dialog.reject()
        flush_deferred_deletes(qapp)

        assert parent.findChildren(SettingsDialog) == []

    parent.deleteLater()
    flush_deferred_deletes(qapp)


def test_close_takes_queued_probe_and_retires_running_probe(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    started = Event()
    release = Event()

    class BlockingLocator:
        def __init__(self) -> None:
            self.calls = 0

        def locate(self, path: str, *, force: bool) -> SevenZipInfo:
            self.calls += 1
            started.set()
            assert release.wait(5.0)
            return SevenZipInfo(path, True, "old", None)

    old_locator = BlockingLocator()
    old_dialog = SettingsDialog(
        make_config(tmp_path / "old"),
        seven_zip_locator=old_locator,  # type: ignore[arg-type]
    )
    old_dialog._probe_pool.setMaxThreadCount(1)
    old_dialog._start_seven_zip_probe("running.exe")
    assert started.wait(2.0)
    old_dialog._start_seven_zip_probe("queued.exe")
    old_tracking = old_dialog._probe_workers
    old_status_setter = Mock(wraps=old_dialog.seven_zip_status_label.setText)
    old_dialog.seven_zip_status_label.setText = old_status_setter

    old_dialog.reject()
    old_dialog.reject()

    assert old_locator.calls == 1
    assert len(old_tracking) == 1
    assert old_dialog.parent() is None
    assert old_dialog in _RETIRED_SETTINGS_DIALOGS

    class ReadyLocator:
        def locate(self, path: str, *, force: bool) -> SevenZipInfo:
            return SevenZipInfo(path, True, "new", None)

    new_dialog = SettingsDialog(
        make_config(tmp_path / "new"),
        seven_zip_locator=ReadyLocator(),  # type: ignore[arg-type]
    )
    new_dialog._start_seven_zip_probe("new.exe")
    assert new_dialog._probe_pool.waitForDone(2000)
    qapp.processEvents()
    assert new_dialog._probe_workers == {}
    new_status = new_dialog.seven_zip_status_label.text()
    assert "new.exe" in new_status

    release.set()
    assert old_dialog._probe_pool.waitForDone(2000)
    qapp.processEvents()

    assert old_tracking == {}
    old_status_setter.assert_not_called()
    assert new_dialog.seven_zip_status_label.text() == new_status
    assert old_dialog not in _RETIRED_SETTINGS_DIALOGS

    new_dialog.reject()
    flush_deferred_deletes(qapp)


def test_probe_exceptions_finish_and_clear_all_tracking(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class RaisingLocator:
        def locate(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("probe failed")

    locator = RaisingLocator()
    dialog = SettingsDialog(
        make_config(tmp_path),
        seven_zip_locator=locator,  # type: ignore[arg-type]
        winrar_locator=locator,  # type: ignore[arg-type]
        ffmpeg_locator=locator,  # type: ignore[arg-type]
    )
    dialog._start_seven_zip_probe("7z.exe")
    dialog._start_winrar_probe("WinRAR.exe")
    dialog.redetect_ffmpeg()

    assert dialog._probe_pool.waitForDone(2000)
    qapp.processEvents()

    assert dialog._probe_workers == {}
    assert dialog._winrar_probe_workers == {}
    assert dialog._ffmpeg_probe_workers == {}
    assert "probe failed" in dialog.seven_zip_status_label.text()
    assert "probe failed" in dialog.winrar_status_label.text()
    assert "見つかりません" in dialog.ffmpeg_status_label.text()

    dialog.reject()
    flush_deferred_deletes(qapp)


def test_application_shutdown_waits_for_running_probe_and_finalizes_tracking(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    started = Event()
    release = Event()

    class BlockingLocator:
        def locate(self, path: str, *, force: bool) -> SevenZipInfo:
            started.set()
            assert release.wait(5.0)
            return SevenZipInfo(path, True, "ready", None)

    dialog = SettingsDialog(
        make_config(tmp_path),
        seven_zip_locator=BlockingLocator(),  # type: ignore[arg-type]
    )
    dialog._start_seven_zip_probe("running.exe")
    assert started.wait(2.0)
    tracking = dialog._probe_workers
    original_delete_later = dialog.deleteLater
    delete_later = Mock()
    dialog.deleteLater = delete_later

    assert not dialog._prepare_application_shutdown(wait_msecs=0)
    assert dialog._probes_closed
    assert len(tracking) == 1
    assert dialog in _RETIRED_SETTINGS_DIALOGS
    assert not dialog._delete_scheduled

    assert not dialog._prepare_application_shutdown(wait_msecs=0)
    assert len(tracking) == 1
    assert dialog in _RETIRED_SETTINGS_DIALOGS
    delete_later.assert_not_called()

    release.set()
    assert dialog._probe_pool.waitForDone(2000)
    assert dialog._prepare_application_shutdown(wait_msecs=0)
    assert tracking == {}
    assert dialog not in _RETIRED_SETTINGS_DIALOGS
    delete_later.assert_called_once_with()

    qapp.processEvents()
    assert tracking == {}
    delete_later.assert_called_once_with()

    dialog.deleteLater = original_delete_later
    original_delete_later()
    flush_deferred_deletes(qapp)


def test_shutdown_preparation_blocks_new_probes_and_file_pickers(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    dialog = SettingsDialog(make_config(tmp_path))
    picker = Mock(return_value=("", ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", picker)

    dialog.browse_ffmpeg()
    assert picker.call_count == 1
    assert dialog._prepare_application_shutdown(wait_msecs=0)

    dialog.browse_ffmpeg()
    dialog.browse_winrar()
    dialog.browse_seven_zip()
    dialog._start_seven_zip_probe("late.exe")
    dialog._start_winrar_probe("late.exe")
    dialog.redetect_ffmpeg()

    assert picker.call_count == 1
    assert dialog._probe_workers == {}
    assert dialog._winrar_probe_workers == {}
    assert dialog._ffmpeg_probe_workers == {}
    flush_deferred_deletes(qapp)
