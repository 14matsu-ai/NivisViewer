from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import stat
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


@dataclass(frozen=True)
class PendingBrowserFocusRequest:
    folder: Path
    paths: tuple[Path, ...]
    primary: Path | None
    scan_generation: int
    open_after: bool
    request_id: int
    ignored_count: int = 0

    def with_generation(self, generation: int) -> PendingBrowserFocusRequest:
        return replace(self, scan_generation=int(generation))


class _BrowserDropProbeSignals(QObject):
    completed = Signal(object)


class _BrowserDropProbe(QRunnable):
    def __init__(
        self,
        paths: tuple[str, ...],
        request_id: int,
        open_after: bool,
        cancelled: Event,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.request_id = request_id
        self.open_after = open_after
        self.cancelled = cancelled
        self.signals = _BrowserDropProbeSignals()

    @Slot()
    def run(self) -> None:
        result = self._probe()
        self.signals.completed.emit(result)

    def _probe(self) -> PendingBrowserFocusRequest | None:
        resolved: list[tuple[Path, bool]] = []
        for raw_path in self.paths:
            if self.cancelled.is_set():
                return None
            path = Path(
                os.path.abspath(
                    os.path.normpath(os.fspath(Path(raw_path).expanduser()))
                )
            )
            try:
                mode = path.stat().st_mode
            except OSError:
                continue
            is_directory = stat.S_ISDIR(mode)
            if is_directory or stat.S_ISREG(mode):
                resolved.append((path, is_directory))
        if self.cancelled.is_set() or not resolved:
            return None
        first_path, first_is_directory = resolved[0]
        if first_is_directory:
            return PendingBrowserFocusRequest(
                folder=first_path,
                paths=(),
                primary=None,
                scan_generation=-1,
                open_after=False,
                request_id=self.request_id,
                ignored_count=max(0, len(resolved) - 1),
            )
        folder = first_path.parent
        same_parent = tuple(
            path
            for path, is_directory in resolved
            if not is_directory and _same_path(path.parent, folder)
        )
        ignored = len(resolved) - len(same_parent)
        return PendingBrowserFocusRequest(
            folder=folder,
            paths=same_parent,
            primary=first_path,
            scan_generation=-1,
            open_after=self.open_after,
            request_id=self.request_id,
            ignored_count=max(0, ignored),
        )


class BrowserMainDropController(QObject):
    """Asynchronously turns Explorer drops into path-based Browser focus."""

    focus_request_ready = Signal(object)
    request_cancelled = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._request_id = 0
        self._active_cancel: Event | None = None
        self._workers: set[_BrowserDropProbe] = set()
        self._closed = False

    @property
    def active_request_id(self) -> int:
        return self._request_id

    def handle_external_paths(
        self,
        paths: tuple[str, ...] | list[str],
        *,
        behavior: str = "focus_only",
    ) -> int:
        if self._closed:
            return -1
        self.cancel()
        normalized = tuple(dict.fromkeys(str(path) for path in paths if str(path)))
        if not normalized:
            return -1
        self._request_id += 1
        request_id = self._request_id
        cancelled = Event()
        self._active_cancel = cancelled
        worker = _BrowserDropProbe(
            normalized,
            request_id,
            behavior == "focus_and_open",
            cancelled,
        )
        worker.signals.completed.connect(
            lambda result, current=worker: self._on_completed(current, result)
        )
        self._workers.add(worker)
        QThreadPool.globalInstance().start(worker)
        return request_id

    def cancel(self) -> None:
        cancelled = self._active_cancel
        if cancelled is not None:
            cancelled.set()
            self.request_cancelled.emit(self._request_id)
        self._active_cancel = None

    def close(self) -> None:
        self._closed = True
        self.cancel()

    @Slot(object)
    def _on_completed(
        self,
        worker: _BrowserDropProbe,
        result: PendingBrowserFocusRequest | None,
    ) -> None:
        self._workers.discard(worker)
        if (
            self._closed
            or result is None
            or result.request_id != self._request_id
            or (
                self._active_cancel is not None
                and self._active_cancel.is_set()
            )
        ):
            return
        self._active_cancel = None
        self.focus_request_ready.emit(result)


def _same_path(first: Path, second: Path) -> bool:
    return (
        os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(first))))
        .casefold()
        == os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(second))))
        .casefold()
    )
