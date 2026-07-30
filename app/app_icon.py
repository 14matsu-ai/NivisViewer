from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget


APP_ICON_RELATIVE_PATH = Path("assets") / "icons" / "nivisviewer.ico"


def application_icon_path(resource_dir: str | Path) -> Path:
    return Path(resource_dir) / APP_ICON_RELATIVE_PATH


def load_application_icon(resource_dir: str | Path) -> QIcon:
    return QIcon(str(application_icon_path(resource_dir)))


def install_application_icon(
    application: QApplication,
    resource_dir: str | Path,
) -> bool:
    icon = load_application_icon(resource_dir)
    if icon.isNull():
        return False
    application.setWindowIcon(icon)
    return True


def install_window_icon(window: QWidget) -> bool:
    application = QApplication.instance()
    if application is None:
        return False
    icon = application.windowIcon()
    if icon.isNull():
        return False
    window.setWindowIcon(icon)
    return True
