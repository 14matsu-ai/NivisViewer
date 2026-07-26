from __future__ import annotations

from pathlib import Path

from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from app.viewer_window import ViewerWindow


def make_config(tmp_path: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return config


def test_fullscreen_chrome_settings_apply_to_existing_viewer(
    tmp_path: Path,
    qapp,
) -> None:
    config = make_config(tmp_path)
    window = ViewerWindow(config_manager=config)

    config.apply(
        {
            "fullscreen_auto_reveal_ui": False,
            "fullscreen_edge_trigger_px": 20,
            "fullscreen_ui_hide_delay_ms": 1500,
        },
        save=True,
    )

    assert window.fullscreen_chrome.auto_reveal is False
    assert window.fullscreen_chrome.edge_trigger_px == 20
    assert window.fullscreen_chrome.hide_delay_ms == 1500
    window.close()
    qapp.processEvents()


def test_cancel_does_not_save_fullscreen_or_browser_layout_settings(
    tmp_path: Path,
    qapp,
) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)
    dialog.fullscreen_auto_reveal_checkbox.setChecked(False)
    dialog.fullscreen_edge_trigger_spin.setValue(24)
    dialog.fullscreen_hide_delay_spin.setValue(1800)
    dialog.browser_sidebar_layout_combo.setCurrentIndex(
        dialog.browser_sidebar_layout_combo.findData("tabs")
    )
    dialog.folder_tree_sync_mode_combo.setCurrentIndex(
        dialog.folder_tree_sync_mode_combo.findData("off")
    )

    dialog.reject()

    assert config.get("fullscreen_auto_reveal_ui") is True
    assert config.get("fullscreen_edge_trigger_px") == 8
    assert config.get("fullscreen_ui_hide_delay_ms") == 0
    assert config.get("browser_sidebar_layout") == (
        "favorites_top_tree_bottom"
    )
    assert config.get("folder_tree_sync_mode") == "focus_current"
