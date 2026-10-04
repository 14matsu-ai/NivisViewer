from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialog

from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from app.seven_zip_locator import SevenZipInfo


class FakeLocator:
    def __init__(self, valid_paths: set[str]) -> None:
        self.valid_paths = valid_paths
        self.calls: list[tuple[str, bool]] = []

    def locate(self, path="", *, force=False) -> SevenZipInfo:
        value = str(path or "")
        self.calls.append((value, force))
        if value in self.valid_paths or (not value and "" in self.valid_paths):
            detected = value or r"C:\Program Files\7-Zip\7z.exe"
            return SevenZipInfo(detected, True, "7-Zip 26.00", None)
        return SevenZipInfo(value, False, None, "invalid")


def make_config(tmp_path: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return config


def test_config_default_and_invalid_type_normalization(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert config.get("seven_zip_executable") == ""

    config.apply({"seven_zip_executable": 123})
    assert config.get("seven_zip_executable") == ""


def test_explicit_path_is_saved_only_after_async_validation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    executable = str(tmp_path / "7z.exe")
    locator = FakeLocator({executable})
    config = make_config(tmp_path)
    dialog = SettingsDialog(config, seven_zip_locator=locator)  # type: ignore[arg-type]
    dialog.seven_zip_path_edit.setText(executable)

    changed = dialog.apply_settings()
    assert "seven_zip_executable" not in changed
    assert config.get("seven_zip_executable") == ""
    assert dialog._probe_pool.waitForDone(2000)
    qapp.processEvents()

    assert config.get("seven_zip_executable") == executable
    assert "検出済み" in dialog.seven_zip_status_label.text()
    dialog.reject()


def test_invalid_path_does_not_replace_existing_valid_setting(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    valid = str(tmp_path / "good.exe")
    invalid = str(tmp_path / "bad.exe")
    config = make_config(tmp_path)
    config.apply({"seven_zip_executable": valid}, save=True)
    dialog = SettingsDialog(
        config,
        seven_zip_locator=FakeLocator({valid}),  # type: ignore[arg-type]
    )
    dialog.seven_zip_path_edit.setText(invalid)

    dialog.apply_settings()
    assert dialog._probe_pool.waitForDone(2000)
    qapp.processEvents()

    assert config.get("seven_zip_executable") == valid
    assert "見つかりません" in dialog.seven_zip_status_label.text()
    dialog.reject()


def test_automatic_mode_can_be_saved_even_when_not_detected(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    config.apply({"seven_zip_executable": r"C:\old\7z.exe"}, save=True)
    dialog = SettingsDialog(
        config,
        seven_zip_locator=FakeLocator(set()),  # type: ignore[arg-type]
    )

    dialog.use_automatic_seven_zip()
    changed = dialog.apply_settings()
    assert dialog._probe_pool.waitForDone(2000)
    qapp.processEvents()

    assert changed["seven_zip_executable"] == ""
    assert config.get("seven_zip_executable") == ""
    assert "見つかりません" in dialog.seven_zip_status_label.text()
    dialog.reject()


def test_cancel_does_not_save_unvalidated_explicit_path(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    executable = str(tmp_path / "7z.exe")
    config = make_config(tmp_path)
    dialog = SettingsDialog(
        config,
        seven_zip_locator=FakeLocator({executable}),  # type: ignore[arg-type]
    )
    dialog.seven_zip_path_edit.setText(executable)

    dialog.reject()
    qapp.processEvents()

    assert config.get("seven_zip_executable") == ""


def test_ok_waits_for_valid_explicit_path_before_accepting(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    executable = str(tmp_path / "7zz.exe")
    config = make_config(tmp_path)
    dialog = SettingsDialog(
        config,
        seven_zip_locator=FakeLocator({executable}),  # type: ignore[arg-type]
    )
    dialog.seven_zip_path_edit.setText(executable)

    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog._probe_pool.waitForDone(2000)
    qapp.processEvents()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert config.get("seven_zip_executable") == executable
