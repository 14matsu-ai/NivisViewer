"""Bounded local evidence for intermittent GUI stalls; no image contents."""
from __future__ import annotations

import faulthandler
from functools import wraps
import logging
import os
from time import perf_counter

from PySide6.QtCore import QObject, QTimer

from .app_paths import AppPaths


_LOG = logging.getLogger("nivisviewer.freeze")


def trace_gui_phase(function):
    """Log low-frequency open/retirement boundaries even outside DEBUG mode."""
    @wraps(function)
    def traced(self, *args, **kwargs):
        identity = getattr(self, "_active_open_trace_id",
                           getattr(self, "generation", getattr(self, "source_epoch", 0)))
        name = function.__qualname__
        seen = getattr(self, "_freeze_phase_operations", None)
        if seen is None:
            seen = self._freeze_phase_operations = {}
        first = seen.get(name) != identity
        seen[name] = identity
        started = perf_counter()
        if first:
            _LOG.info("BEGIN %s owner=%x operation=%s", name, id(self), identity)
            frame_store = getattr(self, "_frame_store", None)
            source_store = getattr(self, "_source_store", None)
            if frame_store is not None and source_store is not None:
                _LOG.info("CACHE owner=%x frames=%s sources=%s bytes=%s",
                          id(self), frame_store.unit_count, source_store.page_count,
                          frame_store.byte_size + source_store.byte_size)
        try:
            return function(self, *args, **kwargs)
        finally:
            elapsed = (perf_counter() - started) * 1000
            if first or elapsed >= 50:
                _LOG.info("END %s owner=%x operation=%s elapsed_ms=%.1f",
                          name, id(self), identity, elapsed)
    return traced


class FreezeDiagnostics(QObject):
    """A Qt heartbeat rearms CPython's native (GIL-independent) watchdog.

    Only one instance is installed by main. A hang produces one all-thread
    dump, not a repeating stream. Recovery rearms it; a 2 MiB cap bounds
    repeated intermittent stalls. No worker is polled and no source is read.
    """

    def __init__(self, paths: AppPaths, application: QObject) -> None:
        super().__init__(application)
        self._file = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._heartbeat)
        if not paths.writable:
            return
        try:
            paths.logs_dir.mkdir(parents=True, exist_ok=True)
            # Keep at most four earlier sessions, plus this one. Only our own
            # dedicated diagnostic files are eligible for removal.
            old = sorted(paths.logs_dir.glob("freeze-*.log"),
                         key=lambda path: path.stat().st_mtime, reverse=True)
            for path in old[4:]:
                path.unlink(missing_ok=True)
            path = paths.logs_dir / f"freeze-{os.getpid()}.log"
            self._file = path.open("a", encoding="utf-8")
            self._file.write(f"GUI watchdog pid={os.getpid()} timeout=5s\n")
            self._file.flush()
            application.aboutToQuit.connect(self.close)
            self._heartbeat()
            self._timer.start()
            _LOG.info("GUI watchdog enabled: %s", path)
        except (OSError, RuntimeError):
            self.close()
            _LOG.warning("GUI watchdog unavailable", exc_info=True)

    def _heartbeat(self) -> None:
        if self._file is None:
            return
        if os.fstat(self._file.fileno()).st_size >= 2 * 1024 * 1024:
            self.close()
            return
        # Implemented by CPython's native watchdog, so a decoder holding the
        # GIL cannot prevent the stack dump (unlike a Python daemon thread).
        faulthandler.dump_traceback_later(5, repeat=False, file=self._file)

    def close(self) -> None:
        self._timer.stop()
        if self._file is not None:
            faulthandler.cancel_dump_traceback_later()
            self._file.close()
            self._file = None
