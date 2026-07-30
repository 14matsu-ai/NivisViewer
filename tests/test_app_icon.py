from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QWidget

from app.app_icon import (
    application_icon_path,
    install_application_icon,
    install_window_icon,
)


ROOT = Path(__file__).resolve().parents[1]


def test_application_icon_path_uses_resource_directory():
    assert application_icon_path(ROOT) == (
        ROOT / "assets" / "icons" / "nivisviewer.ico"
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "app/browser_window.py",
        "app/viewer_window.py",
        "app/settings_dialog.py",
        "app/diagnostics_dialog.py",
        "app/file_conflict_dialog.py",
    ],
)
def test_top_level_window_installs_application_icon(relative_path):
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    assert "install_window_icon(self)" in source


def test_application_bootstrap_installs_official_icon():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "install_application_icon(application, paths.resource_dir)" in source


def test_application_and_top_level_window_receive_official_icon(qapp):
    previous = qapp.windowIcon()
    window = QWidget()
    try:
        assert install_application_icon(qapp, ROOT)
        assert not qapp.windowIcon().isNull()
        assert install_window_icon(window)
        assert not window.windowIcon().isNull()
        assert window.windowIcon().cacheKey() == qapp.windowIcon().cacheKey()
    finally:
        window.close()
        qapp.setWindowIcon(previous)
