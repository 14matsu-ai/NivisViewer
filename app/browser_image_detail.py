"""Worker-side image dimension probe for the Browser detail bar.

Some Pillow plugins (including JPEG XL) decode pixels during Image.open, so
dimension probing must stay off the GUI thread even without an explicit load.
"""

from __future__ import annotations

from . import pillow_plugins  # noqa: F401 - also supports standalone probes

from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


@dataclass(frozen=True)
class BrowserImageDetailResult:
    path: str
    generation: int
    dimensions: tuple[int, int] | None


class _ProbeSignals(QObject):
    completed = Signal(object)


class _ProbeWorker(QRunnable):
    def __init__(self, path: str, generation: int) -> None:
        super().__init__()
        self.path = path
        self.generation = generation
        self.signals = _ProbeSignals()

    @Slot()
    def run(self) -> None:
        dimensions: tuple[int, int] | None = None
        try:
            # Do not request a separate pixel load. Image.open itself may
            # decode the full payload in plugins such as pillow-jxl-plugin.
            with Image.open(self.path) as image:
                width, height = image.size
                try:
                    orientation = int(image.getexif().get(274, 1))
                except (AttributeError, TypeError, ValueError):
                    orientation = 1
                if orientation in {5, 6, 7, 8}:
                    width, height = height, width
                dimensions = (max(1, int(width)), max(1, int(height)))
        except Exception:
            # Third-party codecs can raise RuntimeError (or other ordinary
            # exceptions) for incomplete/corrupt payloads. Always complete
            # the probe so its owner can release this worker.
            dimensions = None
        self.signals.completed.emit(
            BrowserImageDetailResult(
                self.path,
                self.generation,
                dimensions,
            )
        )


class BrowserImageDetailProbe(QObject):
    completed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._workers: set[_ProbeWorker] = set()

    def request(self, path: str | Path, generation: int) -> None:
        # Selection scrubbing keeps at most the active probe plus the newest
        # pending identity. Stale results are still fenced by the Browser.
        for pending in tuple(self._workers):
            try:
                removed = self._pool.tryTake(pending)
            except RuntimeError:
                removed = False
            if removed:
                self._workers.discard(pending)
        worker = _ProbeWorker(str(path), int(generation))
        self._workers.add(worker)
        worker.signals.completed.connect(
            lambda result, owned=worker: self._finish(result, owned)
        )
        self._pool.start(worker)

    def _finish(
        self,
        result: BrowserImageDetailResult,
        worker: _ProbeWorker,
    ) -> None:
        self._workers.discard(worker)
        self.completed.emit(result)

    def close(self, msecs: int = -1) -> bool:
        self._pool.clear()
        completed = self._pool.waitForDone(int(msecs))
        if completed:
            self._workers.clear()
        return completed


__all__ = [
    "BrowserImageDetailProbe",
    "BrowserImageDetailResult",
]
