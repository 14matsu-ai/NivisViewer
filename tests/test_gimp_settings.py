from threading import Event

from app import gimp_xcf_backend as backend
from app import settings_dialog as settings_module
from app.application_controller import ApplicationController
from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog


def test_manual_gimp_setting_survives_restart_and_updates_runtime(tmp_path, qapp, monkeypatch):
    manual = tmp_path / "別の場所" / "gimp-console.exe"
    manual.parent.mkdir()
    manual.touch()
    automatic = tmp_path / "auto.exe"
    automatic.touch()
    monkeypatch.setattr(backend, "_candidate_paths", lambda: iter([automatic]))
    monkeypatch.setattr(backend, "_probe_version", lambda _: (3, 2, 4))
    monkeypatch.delenv("NIVISVIEWER_GIMP_EXE", raising=False)
    config = ConfigManager(tmp_path / "settings.json")
    controller = ApplicationController(qapp, config_manager=config)
    dialog = SettingsDialog(config)
    try:
        dialog.gimp_path_edit.setText(f'"{manual}"')
        dialog.apply_settings()
        assert backend.find_gimp3_executable() == str(manual.resolve())
        reloaded = ConfigManager(config.path)
        reloaded.load()
        assert reloaded.get("gimp_executable") == str(manual)
        config.apply({"gimp_executable": str(tmp_path / "missing.exe")})
        assert backend.find_gimp3_executable() is None
        dialog.gimp_auto_button.click()
        dialog.apply_settings()
        assert backend.find_gimp3_executable() == str(automatic.resolve())
    finally:
        dialog.reject()
        qapp.processEvents()
        controller.shutdown()
        backend.configure_gimp_executable("")


def test_editing_path_rejects_old_probe_result(tmp_path, qapp, monkeypatch):
    entered, release = Event(), Event()
    def probe(path):
        entered.set()
        assert release.wait(2)
        return path, "3.2.4"
    monkeypatch.setattr(settings_module, "probe_gimp_executable", probe)
    config = ConfigManager(tmp_path / "settings.json")
    config.load()
    dialog = SettingsDialog(config)
    try:
        dialog.gimp_path_edit.setText("old.exe")
        dialog.check_gimp()
        assert entered.wait(1)
        dialog.gimp_path_edit.setText("new.exe")
        release.set()
        assert dialog._probe_pool.waitForDone(2000)
        qapp.processEvents()
        assert "未確認" in dialog.gimp_status_label.text()
        dialog.check_gimp()
        assert dialog._probe_pool.waitForDone(2000)
        qapp.processEvents()
        assert "3.2.4" in dialog.gimp_status_label.text()
        assert "new.exe" in dialog.gimp_status_label.text()
        assert config.get("gimp_executable") == ""
    finally:
        release.set()
        dialog.reject()
        qapp.processEvents()


def test_browse_and_archive_reset(tmp_path, qapp, monkeypatch):
    config = ConfigManager(tmp_path / "settings.json")
    config.load()
    dialog = SettingsDialog(config)
    monkeypatch.setattr(settings_module.QFileDialog, "getOpenFileName",
                        lambda *args: ("D:/GIMP/gimp-console.exe", ""))
    try:
        dialog.gimp_browse_button.click()
        assert dialog.values()["gimp_executable"] == "D:/GIMP/gimp-console.exe"
        dialog._reset_tab_draft("archive")
        assert dialog.gimp_path_edit.text() == ""
    finally:
        dialog.reject()
        qapp.processEvents()
