"""Cold-start wheel diagnostics for the production ZIP Viewer path."""
from __future__ import annotations

from pathlib import Path
from threading import Lock
from threading import Event
from time import monotonic, sleep
import io
import zipfile

from PIL import Image, ImageOps
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.image_source import ZipImageSource
from app.viewer_window import ViewerWindow


def _jpeg_bytes(size: tuple[int, int], seed: int) -> bytes:
    pixels = bytes((index * 17 + seed * 31) % 256 for index in range(size[0] * size[1]))
    output = io.BytesIO()
    with Image.frombytes("L", size, pixels) as luminance:
        with ImageOps.colorize(luminance, black=(10, 20, 40), white=(245, 235, 215)) as image:
            image.save(output, "JPEG", quality=88, subsampling=2)
    return output.getvalue()


def _write_zip(path: Path, pages: int = 8) -> None:
    payload = _jpeg_bytes((1200, 1800), 7)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for index in range(pages):
            archive.writestr(f"{index:03d}.jpg", payload)


class SlowZipSource(ZipImageSource):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.started: list[str] = []
        self.finished: list[str] = []
        self.started_times: list[tuple[str, float]] = []
        self.finished_times: list[tuple[str, float]] = []
        self.lock = Lock()

    def open_streamed_jpeg_at_most(self, image_id, maximum_size):
        return self._slow_decode(image_id, maximum_size, super().open_streamed_jpeg_at_most)

    def open_compatible_jpeg_at_most(self, image_id, maximum_size):
        return self._slow_decode(image_id, maximum_size, super().open_compatible_jpeg_at_most)

    def _slow_decode(self, image_id, maximum_size, decoder):
        with self.lock:
            self.started.append(str(image_id))
            self.started_times.append((str(image_id), monotonic()))
        if str(image_id) != "000.jpg":
            sleep(0.18)
        result = decoder(image_id, maximum_size)
        with self.lock:
            self.finished.append(str(image_id))
            self.finished_times.append((str(image_id), monotonic()))
        return result


class BlockingWarmupSource(ZipImageSource):
    def __init__(self, path: Path, blocked_image_id: str = "001.jpg") -> None:
        super().__init__(path)
        self.blocked_image_id = blocked_image_id
        self.blocked_image_started = Event()
        self.release_blocked_image = Event()

    def open_streamed_jpeg_at_most(self, image_id, maximum_size):
        self._wait_for_page_one(image_id)
        return super().open_streamed_jpeg_at_most(image_id, maximum_size)

    def open_compatible_jpeg_at_most(self, image_id, maximum_size):
        self._wait_for_page_one(image_id)
        return super().open_compatible_jpeg_at_most(image_id, maximum_size)

    def _wait_for_page_one(self, image_id):
        if str(image_id) == self.blocked_image_id:
            self.blocked_image_started.set()
            self.release_blocked_image.wait(3.0)


def _wait_until(qapp: QApplication, predicate, timeout: float = 8.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        sleep(0.001)
    assert predicate()


def _wheel_event(index: int) -> QWheelEvent:
    event = QWheelEvent(
        QPointF(100, 100),
        QPointF(100, 100),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    event.setTimestamp(1_000_000 + index * 8)
    return event


def test_cold_start_rapid_wheel_reports_runtime_frontier(tmp_path, qapp):
    archive = tmp_path / "large-cold.zip"
    _write_zip(archive)
    source = SlowZipSource(archive)
    session = BookSession(source_factory=lambda *_args, **_kwargs: (source, None))
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(900, 700)
    window.set_view_mode("single")
    window.show()
    qapp.processEvents()
    try:
        assert window._finish_opened_book(session.open_book(archive), modal_on_empty=False)
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        first_displayed_at = monotonic()
        runtime = session.viewer_runtime
        assert runtime is not None
        started_at = monotonic()
        requested = []
        for index in range(6):
            window.viewer.wheelEvent(_wheel_event(index))
            requested.append(window.presentation_state.requested_page)
        assert requested and requested[0] == 1
        assert all(page == 1 for page in requested)
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 1)
        elapsed = monotonic() - started_at
        assert elapsed < 2.0
        assert source.started[:1] == ["000.jpg"]
        assert "001.jpg" in source.started
        assert runtime.metrics.stale_results == 0
        print(
            {
                "requested": requested,
                "elapsed_s": round(elapsed, 3),
                "started": tuple(source.started),
                "finished": tuple(source.finished),
                "started_times": tuple((name, round(at - first_displayed_at, 3)) for name, at in source.started_times),
                "finished_times": tuple((name, round(at - first_displayed_at, 3)) for name, at in source.finished_times),
                "metrics": runtime.metrics,
                "debug": runtime.cache_debug_values(),
            },
            flush=True,
        )
    finally:
        window.close()
        qapp.processEvents()
        source.close()


def test_cold_wheel_target_preempts_unrelated_started_startup_warmup(
    tmp_path,
    qapp,
):
    archive = tmp_path / "cold-wheel-preemption.zip"
    _write_zip(archive, pages=8)
    source = BlockingWarmupSource(archive, blocked_image_id="002.jpg")
    session = BookSession(source_factory=lambda *_args, **_kwargs: (source, None))
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(900, 700)
    window.set_view_mode("single")
    window.show()
    qapp.processEvents()
    try:
        assert window._finish_opened_book(session.open_book(archive), modal_on_empty=False)
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        runtime = session.viewer_runtime
        assert runtime is not None
        _wait_until(qapp, source.blocked_image_started.is_set, timeout=3.0)
        # Page one completed as startup warmup before the unrelated page two
        # warmup began. Evict only that neighbor so the first real wheel
        # target is cold while page two remains the running startup job.
        for frame in tuple(runtime._frame_store.values()):
            if frame.unit.pages[0].page_index == 1:
                runtime._frame_store.take(frame.key)
        runtime._source_store.clear()
        started_job = runtime._active_job
        assert started_job is not None and started_job.started.is_set()
        assert started_job.unit.pages[0].page_index == 2

        # Feed the production wheel event path repeatedly. The first cold
        # target is page one; later notches are consumed by the existing
        # hold-unready contract instead of replayed as a hidden seek.
        for index in range(6):
            window.viewer.wheelEvent(_wheel_event(index))
        assert window.presentation_state.requested_page == 1
        assert started_job.cancelled.is_set()
        assert runtime.metrics.cancel_requests == 1
        assert runtime.metrics.running_job_adoptions == 0

        source.release_blocked_image.set()
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 1)
        assert runtime._current_request is not None
        assert runtime._current_request.current.pages[0].page_index == 1
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
    finally:
        source.release_blocked_image.set()
        window.close()
        qapp.processEvents()
        source.close()
