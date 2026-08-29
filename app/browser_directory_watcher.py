from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, Signal


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
        self._path: str | None = None
        self._generation = 0

    @property
    def path(self) -> str | None:
        return self._path

    @property
    def generation(self) -> int:
        return self._generation

    def watch(self, path: str | Path, generation: int) -> bool:
        self.clear()
        normalized = os.path.abspath(os.path.normpath(os.fspath(path)))
        watcher = QFileSystemWatcher(self)
        token = int(generation)
        watcher.directoryChanged.connect(
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
            watcher.deleteLater()
            return False
        self._watcher = watcher
        self._path = normalized
        self._generation = token
        return True

    def clear(self) -> None:
        watcher = self._watcher
        self._watcher = None
        self._path = None
        self._generation = 0
        if watcher is None:
            return
        try:
            watcher.directoryChanged.disconnect()
        except (RuntimeError, TypeError):
            pass
        paths = watcher.directories()
        if paths:
            watcher.removePaths(paths)
        watcher.deleteLater()

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
