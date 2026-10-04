from time import monotonic, sleep
import zipfile

import pytest
from PIL import Image

from app.config_manager import ConfigManager
from app.image_work_coordinator import ImageWorkCoordinator
from app.settings_dialog import SettingsDialog
from app.viewer_window import ViewerWindow


def test_parallel_settings_roundtrip_and_reset(tmp_path, qapp):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    dialog = SettingsDialog(config)
    try:
        assert dialog.viewer_decode_workers_combo.currentData() == 1
        assert dialog.viewer_zip_read_ahead_checkbox.isChecked()
        dialog.viewer_decode_workers_combo.setCurrentIndex(1)
        dialog.viewer_zip_read_ahead_checkbox.setChecked(False)
        values = dialog.values()
        config.apply({key: values[key] for key in ("viewer_decode_workers", "viewer_zip_read_ahead_enabled")}, save=True)
        reopened = ConfigManager(config.path)
        reopened.load()
        assert reopened.get("viewer_decode_workers") == 2
        assert reopened.get("viewer_zip_read_ahead_enabled") is False
        dialog._reset_viewer_scope()
        assert dialog.values()["viewer_decode_workers"] == 1
        assert dialog.values()["viewer_zip_read_ahead_enabled"] is True
    finally:
        dialog.close()


@pytest.mark.parametrize("workers,expected", [(0, 1), (8, 2), ("invalid", 1)])
def test_parallel_settings_normalize_invalid_config(tmp_path, workers, expected):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"viewer_decode_workers": workers, "viewer_zip_read_ahead_enabled": "false"})
    assert config.get("viewer_decode_workers") == expected
    assert config.get("viewer_zip_read_ahead_enabled") is True


@pytest.mark.parametrize("archive", [False, True])
def test_window_parallel_settings_apply_to_next_book_only(tmp_path, qapp, archive):
    image_path = tmp_path / "日本語.jpg"
    with Image.new("RGB", (80, 120)) as image:
        image.save(image_path)
    path = image_path
    if archive:
        path = tmp_path / "日本語.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.write(image_path, image_path.name)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    coordinator = ImageWorkCoordinator()
    window = ViewerWindow(config_manager=config, image_work_coordinator=coordinator)
    def opened_after(previous):
        deadline = monotonic() + 5
        while window.book_session.viewer_runtime is previous and monotonic() < deadline:
            qapp.processEvents()
            sleep(0.001)
        assert window.book_session.viewer_runtime is not previous
    try:
        assert window.open_path(path)
        opened_after(None)
        original = window.book_session.viewer_runtime
        assert original._max_active_jobs == 1
        config.apply({"viewer_decode_workers": 2, "viewer_zip_read_ahead_enabled": False})
        window._load_prefetch_settings()
        assert window.book_session.viewer_runtime is original
        assert original._max_active_jobs == 1
        assert coordinator.folder_supplemental_workers == 0
        assert window.open_path(path)
        opened_after(original)
        current = window.book_session.viewer_runtime
        assert current is not original
        assert current._max_active_jobs == 2
        assert not current._zip_read_ahead_enabled
        assert coordinator.folder_supplemental_workers == 1
        config.apply({"viewer_decode_workers": 1, "viewer_zip_read_ahead_enabled": True})
        window._load_prefetch_settings()
        assert current._max_active_jobs == 2
        assert window.open_path(path)
        opened_after(current)
        assert window.book_session.viewer_runtime._max_active_jobs == 1
        assert window.book_session.viewer_runtime._zip_read_ahead_enabled == archive
        assert coordinator.folder_supplemental_workers == 0
    finally:
        window.prepare_shutdown(wait_msecs=10000)
        window.close()
        qapp.processEvents()
        assert coordinator.shutdown(wait_msecs=10000)
