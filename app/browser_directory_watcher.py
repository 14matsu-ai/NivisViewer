from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from .windows_directory_notifications import WindowsContentNotification


logger = logging.getLogger(__name__)


def _close_notification_holder(holder: list[Any | None]) -> None:
    notification, holder[0] = holder[0], None
    if notification is not None:
        try:
            notification.close()
        except OSError as exc:
            logger.warning("Browser Windows content notification close failed: %s", exc)


@dataclass(frozen=True)
class BrowserDirectoryChange:
    path: str
    generation: int


class BrowserDirectoryWatcher(QObject):
    """Event source for one non-recursive Browser directory.

    A fresh QFileSystemWatcher is created for each navigation generation so a
    queued signal from a detached directory keeps its original generation.
    BrowserWindow owns all reconciliation and model semantics.
    """

    directory_changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._watcher: QFileSystemWatcher | None = None
        self._watcher_connection = None
        self._path: str | None = None
        self._generation = 0
        self._content_holder: list[Any | None] = [None]
        self._last_content_error: str | None = None
        self._content_timer = QTimer(self)
        self._content_timer.setInterval(100)
        self._content_timer.timeout.connect(self._poll_content_notification)
        self.destroyed.connect(
            lambda *_args, holder=self._content_holder: _close_notification_holder(holder)
        )

    @property
    def path(self) -> str | None:
        return self._path

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def content_notifications_active(self) -> bool:
        return self._content_holder[0] is not None

    @property
    def last_content_error(self) -> str | None:
        return self._last_content_error

    def watch(self, path: str | Path, generation: int) -> bool:
        self.clear()
        normalized = os.path.abspath(os.path.normpath(os.fspath(path)))
        watcher = QFileSystemWatcher(self)
        token = int(generation)
        self._watcher_connection = watcher.directoryChanged.connect(
            lambda changed, source=watcher, expected=normalized, current=token: (
                self._on_directory_changed(
                    source,
                    changed,
                    expected,
                    current,
                )
            )
        )
        if not watcher.addPath(normalized):
            try:
                watcher.directoryChanged.disconnect(self._watcher_connection)
            except (RuntimeError, TypeError):
                pass
            self._watcher_connection = None
            watcher.deleteLater()
            return False
        self._watcher = watcher
        self._path = normalized
        self._generation = token
        self._last_content_error = None
        if os.name == "nt":
            try:
                self._content_holder[0] = WindowsContentNotification(normalized)
                self._content_timer.start()
            except (OSError, ValueError) as exc:
                self._last_content_error = str(exc)
                logger.warning(
                    "Browser Windows content notification unavailable for %s: %s",
                    normalized,
                    exc,
                )
        return True

    def clear(self) -> None:
        self._content_timer.stop()
        notification, self._content_holder[0] = self._content_holder[0], None
        if notification is not None:
            try:
                notification.close()
            except OSError as exc:
                self._last_content_error = str(exc)
                logger.warning("Browser Windows content notification close failed: %s", exc)
        watcher = self._watcher
        connection, self._watcher_connection = self._watcher_connection, None
        self._watcher = None
        self._path = None
        self._generation = 0
        if watcher is None:
            return
        if connection is not None:
            try:
                watcher.directoryChanged.disconnect(connection)
            except (RuntimeError, TypeError):
                pass
        paths = watcher.directories()
        if paths:
            watcher.removePaths(paths)
        watcher.deleteLater()

    def _poll_content_notification(self) -> None:
        notification = self._content_holder[0]
        path = self._path
        generation = self._generation
        if notification is None or path is None:
            self._content_timer.stop()
            return
        try:
            changed = notification.poll()
        except OSError as exc:
            self._last_content_error = str(exc)
            logger.warning(
                "Browser Windows content notification failed for %s: %s",
                path,
                exc,
            )
            self._content_timer.stop()
            failed, self._content_holder[0] = self._content_holder[0], None
            if failed is not None:
                try:
                    failed.close()
                except OSError:
                    pass
            changed = True  # One catch-up; QFileSystemWatcher remains active.
        if changed and self._path == path and self._generation == generation:
            self.directory_changed.emit(BrowserDirectoryChange(path, generation))

    def close(self) -> None:
        self.clear()

    def _on_directory_changed(
        self,
        source: QFileSystemWatcher,
        changed_path: str,
        expected_path: str,
        generation: int,
    ) -> None:
        if source is not self._watcher:
            return
        if self._path != expected_path or self._generation != generation:
            return
        changed = os.path.abspath(os.path.normpath(changed_path))
        if os.path.normcase(changed).casefold() != os.path.normcase(
            expected_path
        ).casefold():
            return
        self.directory_changed.emit(
            BrowserDirectoryChange(expected_path, generation)
        )
