from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from app.seven_zip_locator import SevenZipInfo
from app.winrar_locator import WinRARInfo


class FakeSevenZipLocator:
    def locate(self, path="", *, force=False):
        value = str(path or "")
        return SevenZipInfo(value, bool(value), "7-Zip fake" if value else None, None)


class FakeWinRARLocator:
    def __init__(self, valid: set[str]) -> None:
        self.valid = valid

    def locate(self, path="", *, extension=".rar", force=False):
        value = str(path or "")
        if value in self.valid:
            return WinRARInfo(
                value,
                True,
                "7.13 x64",
                None,
                "explicit",
                value,
            )
        return WinRARInfo(value, False, None, "invalid")


def make_config(tmp_path: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return config


def test_new_archive_settings_defaults_and_normalization(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    assert config.get("archive_backend_preference") == "auto"
    assert config.get("winrar_executable") == ""
    config.apply(
        {
            "archive_backend_preference": "unknown",
            "winrar_executable": 123,
        }
    )
    assert config.get("archive_backend_preference") == "auto"
    assert config.get("winrar_executable") == ""


def test_preference_and_valid_winrar_path_apply_after_async_probe(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    executable = str(tmp_path / "WinRAR.exe")
    config = make_config(tmp_path)
    dialog = SettingsDialog(
        config,
        seven_zip_locator=FakeSevenZipLocator(),  # type: ignore[arg-type]
        winrar_locator=FakeWinRARLocator({executable}),  # type: ignore[arg-type]
    )
    dialog._select_data(dialog.archive_backend_combo, "winrar")
    dialog.winrar_path_edit.setText(executable)

    changed = dialog.apply_settings()
    assert changed["archive_backend_preference"] == "winrar"
    assert config.get("winrar_executable") == ""
    assert dialog._probe_pool.waitForDone(2000)
    qapp.processEvents()

    assert config.get("winrar_executable") == executable
    assert "検出済み" in dialog.winrar_status_label.text()
    dialog.reject()


def test_invalid_winrar_path_does_not_replace_existing_setting(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    current = str(tmp_path / "good" / "WinRAR.exe")
    invalid = str(tmp_path / "bad" / "WinRAR.exe")
    config = make_config(tmp_path)
    config.apply({"winrar_executable": current}, save=True)
    dialog = SettingsDialog(
        config,
        seven_zip_locator=FakeSevenZipLocator(),  # type: ignore[arg-type]
        winrar_locator=FakeWinRARLocator({current}),  # type: ignore[arg-type]
    )
    dialog.winrar_path_edit.setText(invalid)

    dialog.apply_settings()
    assert dialog._probe_pool.waitForDone(2000)
    qapp.processEvents()

    assert config.get("winrar_executable") == current
    assert "見つかりません" in dialog.winrar_status_label.text()
    dialog.reject()


def test_cancel_does_not_save_winrar_or_preference_edits(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    dialog = SettingsDialog(
        config,
        seven_zip_locator=FakeSevenZipLocator(),  # type: ignore[arg-type]
        winrar_locator=FakeWinRARLocator(set()),  # type: ignore[arg-type]
    )
    dialog._select_data(dialog.archive_backend_combo, "winrar")
    dialog.winrar_path_edit.setText(str(tmp_path / "WinRAR.exe"))

    dialog.reject()
    qapp.processEvents()

    assert config.get("archive_backend_preference") == "auto"
    assert config.get("winrar_executable") == ""
