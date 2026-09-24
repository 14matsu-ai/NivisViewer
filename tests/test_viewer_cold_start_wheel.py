"""Cold-start wheel diagnostics for the production ZIP Viewer path."""
from __future__ import annotations

from pathlib import Path
from threading import Lock
from threading import Event
from time import monotonic, sleep
import io
import os
import zipfile

from PIL import Image, ImageOps
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.image_source import ZipImageSource
from app.viewer_navigation_policy import NavigationInputKind
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


class SharedSpreadCountingSource(ZipImageSource):
    """Count one real ZIP payload/decode and gate the shared spread page."""

    def __init__(self, path: Path, blocked_image_id: str = "003.jpg") -> None:
        super().__init__(path)
        self.blocked_image_id = blocked_image_id
        self.block_enabled = False
        self.blocked_image_started = Event()
        self.release_blocked_image = Event()
        self.entry_reads: list[str] = []
        self.decode_calls: list[str] = []

    def _read_entry_qbytearray(self, image_id, cancelled):
        image_id = str(image_id)
        self.entry_reads.append(image_id)
        if (
            self.block_enabled
            and image_id == self.blocked_image_id
            and not self.release_blocked_image.is_set()
        ):
            self.blocked_image_started.set()
            while not self.release_blocked_image.wait(0.002):
                self._raise_if_cancelled(cancelled)
        return super()._read_entry_qbytearray(image_id, cancelled)

    def open_compatible_jpeg_at_most(self, image_id, maximum_size):
        self.decode_calls.append(str(image_id))
        return super().open_compatible_jpeg_at_most(image_id, maximum_size)


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


def test_cold_wheel_event_loop_intervals_are_recorded(tmp_path, qapp, monkeypatch):
    """Record real Qt-loop timing around 20/50/100 ms wheel intervals."""
    archive = tmp_path / "cold-wheel-event-loop.zip"
    _write_zip(archive, pages=8)
    source = SlowZipSource(archive)
    session = BookSession(source_factory=lambda *_args, **_kwargs: (source, None))
    config = ConfigManager(tmp_path / "cold-wheel-event-loop.json")
    config.load()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(900, 700)
    window.set_view_mode("single")
    window.show()
    qapp.processEvents()
    try:
        assert window._finish_opened_book(
            session.open_book(archive), modal_on_empty=False
        )
        runtime = session.viewer_runtime
        assert runtime is not None
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        planner = runtime._warmup_planner
        assert planner is not None
        monkeypatch.setattr(
            type(planner),
            "next_candidate",
            lambda *_args, **_kwargs: None,
        )
        for frame in tuple(runtime._frame_store.values()):
            if frame.unit.pages[0].page_index == 1:
                runtime._frame_store.take(frame.key)
        runtime._source_store.clear()
        with source.lock:
            source.started.clear()
            source.finished.clear()
            source.started_times.clear()
            source.finished_times.clear()

        paint_times: list[float] = []

        def record_paint(_serial, image_ids):
            if isinstance(image_ids, tuple) and any(
                str(image_id).startswith("001.jpg") for image_id in image_ids
            ):
                paint_times.append(monotonic())

        window.viewer.framePainted.connect(record_paint)
        input_times: list[float] = []
        intervals = (0.02, 0.05, 0.10)
        for index, interval in enumerate(intervals):
            qapp.processEvents()
            input_times.append(monotonic())
            assert QApplication.sendEvent(window.viewer, _wheel_event(index))
            sleep(interval)
            qapp.processEvents()
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 1)
        _wait_until(qapp, lambda: bool(paint_times))
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        page_started = next(
            at for name, at in source.started_times if name == "001.jpg"
        )
        page_finished = next(
            at for name, at in source.finished_times if name == "001.jpg"
        )
        print(
            "cold-wheel-event-loop-ms",
            {
                "intervals": tuple(round(value * 1000, 1) for value in intervals),
                "input_to_decode_start": round((page_started - input_times[0]) * 1000, 1),
                "decode": round((page_finished - page_started) * 1000, 1),
                "decode_to_paint": round((paint_times[0] - page_finished) * 1000, 1),
                "started": tuple(source.started),
                "finished": tuple(source.finished),
                "cancel_requests": runtime.metrics.cancel_requests,
                "stale_results": runtime.metrics.stale_results,
                "inflight": runtime.cache_debug_values()["inflight_reservation_bytes"],
            },
            flush=True,
        )
        assert source.started.count("001.jpg") == 1
        assert source.finished.count("001.jpg") == 1
        assert runtime.metrics.stale_results == 0
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


@pytest.mark.parametrize("reading_direction", ["ltr", "rtl"])
def test_actual_viewer_wheel_reuses_started_shared_spread_source(
    tmp_path,
    qapp,
    monkeypatch,
    reading_direction,
):
    """The production wheel path keeps a started page shared by two spreads."""
    archive = tmp_path / f"shared-spread-{reading_direction}.zip"
    _write_zip(archive, pages=8)
    source = SharedSpreadCountingSource(archive)
    session = BookSession(source_factory=lambda *_args, **_kwargs: (source, None))
    config = ConfigManager(tmp_path / f"shared-spread-{reading_direction}.json")
    config.load()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(900, 700)
    window.set_view_mode("spread")
    window.set_single_first_page(False)
    window.set_reading_direction(reading_direction)
    window.show()
    qapp.processEvents()
    try:
        assert window._finish_opened_book(
            session.open_book(archive), modal_on_empty=False
        )
        runtime = session.viewer_runtime
        assert runtime is not None
        initial_indexes = (0, 1) if reading_direction == "ltr" else (1, 0)
        _wait_until(
            qapp,
            lambda: window.viewer.displayed_page_indexes == initial_indexes,
        )
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        # First let the real warmup lane build the shifted (3,4) artifact.
        # It is then ready and will be skipped while (2,3) is admitted.
        assert window.page_navigation.go_to_focused_page_index(
            1, input_kind=NavigationInputKind.DISCRETE
        )
        shifted_indexes = (1, 2) if reading_direction == "ltr" else (2, 1)
        _wait_until(
            qapp,
            lambda: window.viewer.displayed_page_indexes == shifted_indexes,
        )
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        # Controlled before/after switch: this is only for proving that the
        # old Viewer caller (which omitted the preserve hint) really fails.
        if os.environ.get("NIVISVIEWER_FORCE_PRESERVE_FALSE") == "1":
            real_request = runtime.request

            def request_without_started_preserve(request, **_kwargs):
                return real_request(request, preserve_started_compatible=False)

            monkeypatch.setattr(runtime, "request", request_without_started_preserve)

        # Keep the old paint visible, but make the current and shared source
        # cold. The next refresh therefore starts (1,2), then the planner's
        # ready (3,4) skip exposes the in-flight (2,3) neighbor.
        for frame in tuple(runtime._frame_store.values()):
            page_indexes = {page.page_index for page in frame.unit.pages}
            if page_indexes in ({1, 2}, {2, 3}):
                runtime._frame_store.take(frame.key)
        runtime._source_store.clear()
        source.entry_reads.clear()
        source.decode_calls.clear()
        source.block_enabled = True

        window._refresh_view()
        _wait_until(qapp, source.blocked_image_started.is_set)
        started_job = runtime._active_job
        assert started_job is not None and started_job.started.is_set()
        expected_active_identity = (
            ((2, "002.jpg"), (3, "003.jpg"))
            if reading_direction == "ltr"
            else ((3, "003.jpg"), (2, "002.jpg"))
        )
        assert started_job.key.unit_identity == expected_active_identity
        for frame in tuple(runtime._frame_store.values()):
            if {page.page_index for page in frame.unit.pages} == {3, 4}:
                runtime._frame_store.take(frame.key)

        # The canvas's real QWheelEvent advances the shifted spread to (3,4).
        window.viewer.wheelEvent(_wheel_event(1))
        assert window.model.current_index == 3
        requested = runtime._current_request
        assert requested is not None
        expected_target_identity = (
            ((3, "003.jpg"), (4, "004.jpg"))
            if reading_direction == "ltr"
            else ((4, "004.jpg"), (3, "003.jpg"))
        )
        assert requested.current.identity == expected_target_identity
        assert not started_job.cancelled.is_set()
        assert runtime.metrics.running_job_adoptions >= 1

        source.release_blocked_image.set()
        target_indexes = (3, 4) if reading_direction == "ltr" else (4, 3)
        _wait_until(qapp, lambda: window.viewer.displayed_page_indexes == target_indexes)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert source.entry_reads.count("003.jpg") == 1
        assert source.decode_calls.count("003.jpg") == 1
        assert runtime.metrics.stale_results == 0
    finally:
        source.release_blocked_image.set()
        window.close()
        qapp.processEvents()
        source.close()
