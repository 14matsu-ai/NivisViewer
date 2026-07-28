from __future__ import annotations

import os
import platform
import subprocess
import webbrowser
from collections.abc import Iterator
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from app import settings_dialog as settings_dialog_module
from app.seven_zip_locator import SevenZipInfo
from app.system_file_opener import WindowsSystemOpenAdapter
from app.winrar_locator import WinRARInfo


def _reject_external_launch(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise AssertionError(
        "External application launch is forbidden during automated tests; "
        "inject a fake adapter or opener instead."
    )


def _safe_seven_zip_locator() -> Mock:
    locator = Mock()
    locator.locate.return_value = SevenZipInfo(
        "",
        False,
        None,
        "Disabled during automated tests",
    )
    return locator


def _safe_winrar_locator() -> Mock:
    locator = Mock()
    locator.locate.return_value = WinRARInfo(
        "",
        False,
        None,
        "Disabled during automated tests",
    )
    return locator


def _safe_ffmpeg_locator() -> Mock:
    locator = Mock()
    locator.locate.return_value = None
    return locator


@pytest.fixture(autouse=True)
def prevent_external_application_launches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail tests that cross a real OS application-launch boundary."""

    popen_type = subprocess.Popen
    monkeypatch.setattr(
        WindowsSystemOpenAdapter,
        "open_default",
        _reject_external_launch,
    )
    monkeypatch.setattr(
        WindowsSystemOpenAdapter,
        "open_picker",
        _reject_external_launch,
    )
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        staticmethod(_reject_external_launch),
    )
    monkeypatch.setattr(popen_type, "__init__", _reject_external_launch)
    monkeypatch.setattr(subprocess, "run", _reject_external_launch)
    monkeypatch.setattr(webbrowser, "open", _reject_external_launch)
    monkeypatch.setattr(platform, "platform", lambda: "Windows-test")
    monkeypatch.setattr(
        settings_dialog_module,
        "SevenZipLocator",
        _safe_seven_zip_locator,
    )
    monkeypatch.setattr(
        settings_dialog_module,
        "WinRARLocator",
        _safe_winrar_locator,
    )
    monkeypatch.setattr(
        settings_dialog_module,
        "FFmpegLocator",
        _safe_ffmpeg_locator,
    )
    if hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", _reject_external_launch)


@pytest.fixture(scope="session")
def qapp() -> Iterator[QApplication]:
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()
