from __future__ import annotations

import weakref
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from .config_manager import ConfigManager
from .main_window import MainWindow


WindowFactory = Callable[..., MainWindow]


class ApplicationController(QObject):
    def __init__(
        self,
        application: QApplication,
        parent: QObject | None = None,
        *,
        config_manager: ConfigManager | None = None,
        window_factory: WindowFactory = MainWindow,
    ) -> None:
        super().__init__(parent or application)
        self.application = application
        self.config = config_manager or ConfigManager()
        self.settings = self.config.load()
        self._window_factory = window_factory
        self._viewer_windows: list[MainWindow] = []
        self._shutdown = False
        self.application.aboutToQuit.connect(self.shutdown)

    @property
    def main_window(self) -> MainWindow | None:
        return self._viewer_windows[0] if self._viewer_windows else None

    @property
    def viewer_windows(self) -> tuple[MainWindow, ...]:
        return tuple(self._viewer_windows)

    def start(self, initial_path: str | None = None) -> MainWindow:
        self._shutdown = False
        window = self.main_window or self._create_window()
        window.show_initial()
        if initial_path:
            self.open_path(initial_path, open_in_new_window=False)
        else:
            last_path = self.settings.get("last_open_path", "")
            if (
                bool(self.settings.get("reopen_last_on_start", False))
                and isinstance(last_path, str)
                and last_path
                and Path(last_path).exists()
            ):
                self.open_path(last_path, open_in_new_window=False)
        return window

    def open_path(self, path: str | Path, *, open_in_new_window: bool | None = None) -> bool:
        window = self._window_for_open(open_in_new_window)
        opened = window.open_path(path)
        if opened and bool(self.settings.get("bring_viewer_to_front_on_open", True)):
            self.bring_window_to_front_once(window)
        return opened

    def bring_window_to_front_once(self, window: QWidget) -> None:
        window.show()
        if window.isActiveWindow():
            return
        window.raise_()
        window.activateWindow()

        window_ref = weakref.ref(window)

        def activate_after_show() -> None:
            target = window_ref()
            if target is None or target.isActiveWindow():
                return
            target.raise_()
            target.activateWindow()

        QTimer.singleShot(0, activate_after_show)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        for window in tuple(self._viewer_windows):
            window.prepare_shutdown()

    def _create_window(self) -> MainWindow:
        window = self._window_factory(
            config_manager=self.config,
            open_path_handler=self.open_path,
        )
        self._viewer_windows.append(window)
        return window

    def _window_for_open(self, open_in_new_window: bool | None) -> MainWindow:
        behavior = str(self.settings.get("open_viewer_behavior", "reuse_or_create"))
        create_new = open_in_new_window is True or (
            open_in_new_window is None and behavior == "always_new" and self.main_window is not None
        )
        if create_new or self.main_window is None:
            window = self._create_window()
            window.show_initial()
            return window
        return self.main_window
