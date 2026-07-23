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
        }
    )

    dialog = SettingsDialog(config)

    assert dialog.open_behavior_combo.currentData() == "always_new"
    assert dialog.gap_spin.value() == 33
    assert dialog.join_spread_checkbox.isChecked()
    assert not dialog.gap_spin.isEnabled()
    assert dialog.thumbnail_size_spin.value() == 240
    dialog.reject()


def test_apply_and_ok_persist_settings(tmp_path: Path, qapp: QApplication) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(config)
    dialog.open_behavior_combo.setCurrentIndex(
        dialog.open_behavior_combo.findData("reuse_active")
    )
    dialog.gap_spin.setValue(45)
    dialog.thumbnail_size_spin.setValue(260)

    changed = dialog.apply_settings()

    assert changed["open_viewer_behavior"] == "reuse_active"
    assert changed["gap"] == 45
    assert ConfigManager(config.path).load()["thumbnail_size"] == 260

    dialog.join_spread_checkbox.setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert ConfigManager(config.path).load()["join_spread_pages"] is True


def test_cancel_does_not_apply_changes(tmp_path: Path, qapp: QApplication) -> None:
    config = make_config(tmp_path)
    original_gap = config.get("gap")
    dialog = SettingsDialog(config)
    dialog.gap_spin.setValue(99)

    dialog.reject()

    assert config.get("gap") == original_gap
    assert not config.path.exists()


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
