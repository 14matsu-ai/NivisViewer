from __future__ import annotations

from io import BytesIO
from pathlib import Path
from threading import Event
from time import monotonic, perf_counter_ns
import zipfile

from PIL import Image
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.image_source import ImageSourceError, ZipImageSource
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
from app.viewer_navigation_policy import NavigationInputKind
from app.viewer_presentation_state import PresentationSurfaceMode
from app.viewer_window import ViewerWindow
from app.zip_raster_book_runtime import (
    ZipRasterDisplayUnit,
    ZipRasterPage,
    ZipRasterRenderSpec,
    ZipRasterRequest,
)


def _planned_units(request: ZipRasterRequest) -> tuple[ZipRasterDisplayUnit, ...]:
    return (
        request.current,
        *tuple(request.warmup_plan.iter_background_units()),
    )


def _write_zip(tmp_path: Path, *, pages: int = 3) -> Path:
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for index in range(pages):
            path = tmp_path / f"{index:03d}.png"
            with Image.new(
                "RGB",
                (120 + index * 10, 180 + index * 10),
                (80, 100 + index * 20, 140),
            ) as image:
                image.save(path)
            output.write(path, path.name)
    return archive


def _window(
    tmp_path: Path,
    *,
    pages: int = 3,
    memory_mode: str | None = None,
) -> tuple[ViewerWindow, BookSession, ZipImageSource, Path]:
    archive = _write_zip(tmp_path, pages=pages)
    source = ZipImageSource(archive)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    if memory_mode is not None:
        config.apply({"viewer_memory_mode": memory_mode})
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
    )
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    return window, session, source, archive


def test_viewer_admits_leading_slider_target_before_release(tmp_path, qapp):
    archive = _write_zip(tmp_path, pages=2)

    class BlockedSource(ZipImageSource):
        started = Event()
        release = Event()

        def open_image(self, image_id):
            if image_id == "001.png":
                self.started.set()
                assert self.release.wait(3)
            return super().open_image(image_id)

    source = BlockedSource(archive)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    session = BookSession(source_factory=lambda *_args, **_kwargs: (source, None))
    window = ViewerWindow(config_manager=config, book_session=session)
    try:
        window.resize(640, 480)
        window.set_view_mode("single")
        window.show()
        qapp.processEvents()
        assert window._finish_opened_book(session.open_book(archive), modal_on_empty=False)
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        assert source.started.wait(1)
        runtime = session.viewer_runtime
        window.slider.setSliderDown(True)
        window._go_to_index_with_history(1, input_kind=NavigationInputKind.SLIDER_SCRUB)
        assert window._pending_zip_runtime_request is None
        source.release.set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 1)
        assert window.presentation_state.requested_page == 1
        assert window.slider.isSliderDown()
        window.slider.setSliderDown(False)
        assert window.presentation_state.displayed_page == 1
        assert window._pending_zip_runtime_request is None
    finally:
        source.release.set()
        window.close()
        qapp.processEvents()


def test_zip_first_paint_populates_book_wide_display_ready_cache(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, session, _source, archive = _window(
        tmp_path,
        pages=12,
        memory_mode="4096",
    )
    try:
        window.set_view_mode("single")
        window.show()
        qapp.processEvents()
        opened = session.open_book(archive)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        runtime = session.viewer_runtime
        assert runtime is not None

        request = window._zip_runtime_request(window.model.spread_at())
        assert request is not None
        initial_topology = request.warmup_plan.topology
        assert [
            unit.pages[0].page_index for unit in _planned_units(request)
        ] == list(range(12))
        assert window.viewer_cache_budget_bytes == 4096 * 1024 * 1024
        assert runtime.cache_byte_budget == window.viewer_cache_budget_bytes
        assert runtime.cache_soft_target_bytes == (
            window.viewer_cache_soft_target_bytes
        )

        _wait_until(
            qapp,
            lambda: runtime.cached_unit_count == 12
            and not runtime.has_unfinished_tasks(),
            timeout_ms=5000,
        )
        assert set(runtime.cached_page_indexes) == set(range(12))
        assert runtime.decoded_source_count == 12

        # The independent memory mode is also authoritative for an already
        # open raster book; it is not only copied into the next BookSession.
        window.config.apply({"viewer_memory_mode": "256"})
        qapp.processEvents()
        assert window.viewer_cache_budget_bytes == 256 * 1024 * 1024
        assert runtime.cache_byte_budget == window.viewer_cache_budget_bytes
        window.config.apply({"viewer_memory_mode": "4096"})
        qapp.processEvents()
        assert runtime.cache_byte_budget == 4096 * 1024 * 1024

        window.model.go_to_index(6)
        window.presentation_state._direction = -1
        reversed_request = window._zip_runtime_request(
            window.model.spread_at()
        )
        assert reversed_request is not None
        assert reversed_request.warmup_plan.topology is initial_topology
        assert [
            unit.pages[0].page_index
            for unit in _planned_units(reversed_request)[:5]
        ] == [6, 5, 7, 4, 3]

        # Raster population is byte-budget driven.  The legacy preset remains
        # available to PDF/legacy paths but no longer narrows this plan.
        window.prefetch_preset = "disabled"
        disabled_request = window._zip_runtime_request(
            window.model.spread_at()
        )
        assert disabled_request is not None
        assert disabled_request.warmup_plan.background_enabled
        assert disabled_request.warmup_plan.topology is initial_topology
        assert [
            unit.pages[0].page_index
            for unit in _planned_units(disabled_request)[:5]
        ] == [6, 5, 7, 4, 3]

        window.prefetch_preset = "standard"
        window.fit_mode = "actual_size"
        full_source_request = window._zip_runtime_request(
            window.model.spread_at()
        )
        assert full_source_request is not None
        assert full_source_request.render_spec.decoder_maximum_size is None
        # Full-source fidelity no longer disables capacity-admitted warmup.
        assert full_source_request.warmup_plan.background_enabled
        assert tuple(full_source_request.warmup_plan.iter_background_units())

        window.set_view_mode("spread")
        layout_request = window._zip_runtime_request(window.model.spread_at())
        assert layout_request is not None
        assert layout_request.warmup_plan.topology is initial_topology
        repeated_layout_request = window._zip_runtime_request(
            window.model.spread_at()
        )
        assert repeated_layout_request is not None
        assert (
            repeated_layout_request.warmup_plan.topology
            is layout_request.warmup_plan.topology
        )
    finally:
        window.close()
        qapp.processEvents()


def test_cold_first_frame_resize_fences_old_layout_without_idle_prompt(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = _write_zip(tmp_path, pages=1)

    class BlockingZipSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.entered = Event()
            self.release = Event()

        def open_image(self, image_id: str) -> Image.Image:
            self.entered.set()
            self.release.wait(3.0)
            return super().open_image(image_id)

    source = BlockingZipSource(archive)
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
    )
    config = ConfigManager(tmp_path / "resize-first-frame-config.json")
    config.load()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    try:
        window.show()
        qapp.processEvents()
        opened = session.open_book(archive)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        # ZIP spread geometry may be header-probed first.  Keep the Qt event
        # loop running so the queued metadata result can release pixel decode.
        _wait_until(qapp, source.entered.is_set, timeout_ms=2000)
        old_request = window.presentation_state.requested
        assert old_request is not None

        window.resize(800, 600)
        qapp.processEvents()
        assert (
            window.presentation_state.requested is None
            or window.presentation_state.requested.token != old_request.token
        )
        assert (
            window.presentation_state.surface.mode
            is PresentationSurfaceMode.LOADING
        )
        assert not window.viewer._images

        _wait_until(
            qapp,
            lambda: window.presentation_state.requested is not None
            and window.presentation_state.requested.token
            != old_request.token,
        )
        replacement = window.presentation_state.requested
        assert replacement is not None
        assert replacement.token.layout_signature != old_request.token.layout_signature

        source.release.set()
        _wait_until(
            qapp,
            lambda: window.presentation_state.displayed is not None
            and window.presentation_state.displayed.token == replacement.token,
        )
        assert (
            window.presentation_state.surface.mode
            is PresentationSurfaceMode.DISPLAYED
        )
        assert window.viewer.displayed_page_indexes == (0,)

        window._on_viewport_changed()
        assert window._raster_viewport_timer.isActive()
        assert window._raster_viewport_timer.interval() == 120
        window._raster_viewport_timer.stop()
    finally:
        source.release.set()
        window.close()
        qapp.processEvents()


def test_zip_spread_wide_page_is_preflighted_before_viewer_decode(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    archive = tmp_path / "lazy-layout.zip"
    sizes = ((720, 1200), (1800, 900), (720, 1200))
    with zipfile.ZipFile(archive, "w") as output:
        for index, size in enumerate(sizes):
            path = tmp_path / f"{index}.jpg"
            with Image.new("RGB", size, (50 + index * 20, 90, 130)) as image:
                image.save(path, "JPEG", quality=88)
            output.write(path, path.name)

    class CountingZipSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.probes: list[str] = []
            self.entry_reads: list[str] = []
            self.decodes: list[str] = []

        def probe_image_size(self, image_id: str):
            self.probes.append(image_id)
            return super().probe_image_size(image_id)

        def _read_entry_qbytearray(self, image_id, cancelled):
            self.entry_reads.append(image_id)
            return super()._read_entry_qbytearray(image_id, cancelled)

        def open_compatible_jpeg_at_most(self, image_id, maximum_size):
            self.decodes.append(image_id)
            return super().open_compatible_jpeg_at_most(image_id, maximum_size)

    source = CountingZipSource(archive)
    session = BookSession(source_factory=lambda _path, **_kwargs: (source, None))
    config = ConfigManager(tmp_path / "lazy-layout-config.json")
    config.load()
    window = ViewerWindow(config_manager=config, book_session=session)
    monkeypatch.setattr(window, "_raster_background_allowed", lambda: False)
    layout_flags: list[bool] = []
    original_zip_request = window._zip_runtime_request

    def record_zip_request(spread):
        request = original_zip_request(spread)
        if request is not None:
            layout_flags.append(request.resolve_layout_metadata)
        return request

    monkeypatch.setattr(window, "_zip_runtime_request", record_zip_request)
    window.resize(640, 480)
    try:
        window.set_view_mode("spread")
        window.set_treat_wide_image_as_single(True)
        window.show()
        qapp.processEvents()
        assert window._finish_opened_book(
            session.open_book(archive),
            modal_on_empty=False,
        )
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        source.probes.clear()
        source.entry_reads.clear()
        source.decodes.clear()

        window._go_to_index_with_history(
            1,
            input_kind=NavigationInputKind.WHEEL,
        )
        _wait_until(
            qapp,
            lambda: (
                window.presentation_state.displayed_page == 1
                and window.presentation_state.displayed is not None
                and window.presentation_state.displayed.unit.page_indexes == (1,)
            ),
        )

        # The header probe determines the wide-page boundary before the
        # current unit is decoded.  The final single-page frame therefore has
        # one entry read/decode and does not re-decode after topology repair.
        assert True in layout_flags
        assert "1.jpg" in source.probes
        assert "2.jpg" in source.probes
        assert source.entry_reads.count("1.jpg") == 1
        assert source.decodes.count("1.jpg") == 1
    finally:
        window.close()
        qapp.processEvents()


def test_zip_spread_wheel_preflights_background_geometry_before_decode(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    """Unknown background spreads must not be decoded before wide-page repair."""

    archive = tmp_path / "mixed-cold-wheel.zip"
    sizes = (
        (900, 1400),
        (2016, 1152),
        (900, 1400),
        (1800, 1200),
        (900, 1400),
        (900, 1400),
    )
    with zipfile.ZipFile(archive, "w") as output:
        for index, size in enumerate(sizes):
            encoded = BytesIO()
            with Image.new(
                "RGB",
                size,
                (50 + index * 10, 80, 120),
            ) as image:
                image.save(encoded, format="JPEG", quality=86)
            output.writestr(f"{index}.jpg", encoded.getvalue())

    class CountingZipSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.decodes: list[str] = []
            self.entry_reads: list[str] = []
            self.probes: list[str] = []

        def probe_image_size(self, image_id: str):
            self.probes.append(image_id)
            return super().probe_image_size(image_id)

        def _read_entry_qbytearray(self, image_id, cancelled):
            self.entry_reads.append(image_id)
            return super()._read_entry_qbytearray(image_id, cancelled)

        def open_compatible_jpeg_at_most(self, image_id, maximum_size):
            self.decodes.append(image_id)
            return super().open_compatible_jpeg_at_most(image_id, maximum_size)

    source = CountingZipSource(archive)
    session = BookSession(source_factory=lambda _path, **_kwargs: (source, None))
    config = ConfigManager(tmp_path / "mixed-cold-wheel-config.json")
    config.load()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    background_enabled = [False]
    wheel_count = [0]
    monkeypatch.setattr(
        window,
        "_raster_background_allowed",
        lambda: background_enabled[0],
    )

    def wheel(timestamp: int) -> None:
        event = QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        event.setTimestamp(timestamp)
        assert QApplication.sendEvent(window.viewer, event)
        wheel_count[0] += 1

    try:
        window.set_view_mode("spread")
        window.set_single_first_page(False)
        window.set_treat_wide_image_as_single(True)
        window.show()
        qapp.processEvents()
        assert window._finish_opened_book(
            session.open_book(archive),
            modal_on_empty=False,
        )
        runtime = session.viewer_runtime
        assert runtime is not None
        _wait_until(
            qapp,
            lambda: window.presentation_state.displayed is not None,
        )
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        # Leave later page headers cold while admitting the real wheel path.
        source.decodes.clear()
        source.entry_reads.clear()
        source.probes.clear()
        background_enabled[0] = True
        for delay, timestamp in zip((0, 50, 100, 150, 200), range(5)):
            QTimer.singleShot(delay, lambda timestamp=timestamp: wheel(timestamp))

        _wait_until(
            qapp,
            lambda: wheel_count[0] == 5 and not runtime.has_unfinished_tasks(),
            timeout_ms=8000,
        )
        assert window.presentation_state.displayed is not None
        assert runtime.metrics.stale_results == 0
        assert runtime.metrics.layout_metadata_pages >= 4
        assert source.probes
        # Page 3 is the mixed-orientation trigger: an old provisional spread
        # would decode it once as (3, 2) and again as the repaired single page.
        assert source.decodes.count("3.jpg") == 1
        assert source.entry_reads.count("3.jpg") == 1
        assert all(
            source.decodes.count(image_id) <= 1
            for image_id in set(source.decodes)
        )
    finally:
        window.close()
        qapp.processEvents()


def _key_event(
    event_type: QEvent.Type,
    key: Qt.Key,
    *,
    auto_repeat: bool = False,
) -> QKeyEvent:
    return QKeyEvent(
        event_type,
        key,
        Qt.KeyboardModifier.NoModifier,
        "",
        auto_repeat,
        1,
    )


def _wheel_end_event() -> QWheelEvent:
    return QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(),
        QPoint(),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollEnd,
        False,
    )


def _wait_until(
    qapp: QApplication,
    predicate,
    *,
    timeout_ms: int = 3000,
) -> None:
    deadline = monotonic() + timeout_ms / 1000
    while not predicate() and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(5)
    assert predicate()


def test_input_kind_admission_is_immediate_except_rapid_bursts(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    window, session, _source, archive = _window(tmp_path, pages=10)
    try:
        window.set_view_mode("single")
        opened = session.open_book(archive)
        runtime = session.viewer_runtime
        assert runtime is not None
        busy = [False]
        cached = [False]
        requested: list[ZipRasterRequest] = []
        staged: list[ZipRasterRequest] = []
        released: list[ZipRasterRequest] = []
        monkeypatch.setattr(runtime, "has_unfinished_tasks", lambda: busy[0])
        monkeypatch.setattr(
            runtime,
            "has_cached_current",
            lambda _request: cached[0],
        )
        monkeypatch.setattr(
            runtime,
            "request",
            lambda request, **_kwargs: requested.append(request) or True,
        )
        monkeypatch.setattr(
            runtime,
            "stage",
            lambda request, **_kwargs: staged.append(request) or True,
        )
        monkeypatch.setattr(
            runtime,
            "release_staged",
            lambda request: released.append(request) or True,
        )

        assert window._finish_opened_book(opened, modal_on_empty=False)
        # This test isolates admission timing; real wheel-readiness behavior
        # is covered by the blocked-worker integration cases below.
        monkeypatch.setattr(window, "_hold_unready_wheel_advance", lambda *_args: False)
        requested.clear()

        # Qt input timestamps are quint64.  They must survive the signal path
        # beyond the signed-32-bit uptime boundary.
        window.viewer.wheelInputObserved.emit(3_000_000_000)
        assert window._navigation_wheel_timestamp_ns == 3_000_000_000_000_000
        window._navigation_wheel_timestamp_ns = None

        # A discrete cold turn never enters the pending timer/state machine.
        window._raster_viewport_timer.start()
        window.next_page()
        assert [request.current.pages[0].page_index for request in requested] == [1]
        assert not staged
        assert window._pending_zip_runtime_request is None
        assert not window._zip_runtime_request_timer.isActive()
        assert not window._raster_viewport_timer.isActive()

        # Cold targets reach the runtime immediately regardless of synthetic
        # busy state; the real runtime owns coalescing when decode is slow.
        busy[0] = True
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        assert [request.current.pages[0].page_index for request in requested[-3:]] == [2, 3, 4]
        assert not staged
        assert window._pending_zip_runtime_request is None

        busy[0] = False
        wheel_end = _wheel_end_event()
        window.viewer.wheelEvent(wheel_end)
        assert wheel_end.isAccepted()
        assert not released
        assert window._pending_zip_runtime_request is None

        # A ready wheel frame also reaches the runtime immediately, allowing
        # its recentered background order to use an available worker slot.
        busy[0] = True
        cached[0] = True
        staged_count = len(staged)
        requested_count = len(requested)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        assert len(requested) == requested_count + 1
        assert len(staged) == staged_count
        assert requested[-1].current.pages[0].page_index == 5
        assert window._pending_zip_runtime_request is None
        window._finish_wheel_navigation()
        assert not released

        # Raw key identity keeps the leading press immediate, coalesces only
        # auto-repeat, and flushes the exact final target on release.
        cached[0] = False
        busy[0] = False
        request_count = len(requested)
        shortcut_override = _key_event(
            QEvent.Type.ShortcutOverride,
            Qt.Key.Key_Right,
        )
        QApplication.sendEvent(window.viewer, shortcut_override)
        assert shortcut_override.isAccepted()
        assert len(requested) == request_count
        QApplication.sendEvent(
            window.viewer,
            _key_event(QEvent.Type.KeyPress, Qt.Key.Key_Right),
        )
        assert len(requested) == request_count + 1
        assert requested[-1].current.pages[0].page_index == 6
        busy[0] = False
        QApplication.sendEvent(
            window.viewer,
            _key_event(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Right,
                auto_repeat=True,
            ),
        )
        assert staged[-1].current.pages[0].page_index == 7
        QApplication.sendEvent(
            window.viewer,
            _key_event(QEvent.Type.KeyRelease, Qt.Key.Key_Right),
        )
        assert released[-1] is staged[-1]
    finally:
        window.close()
        qapp.processEvents()


def test_slider_admission_paces_cold_targets_and_coalesces_rapid_scrub(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    window, session, _source, archive = _window(tmp_path, pages=8)
    try:
        window.set_view_mode("single")
        opened = session.open_book(archive)
        runtime = session.viewer_runtime
        assert runtime is not None
        requested: list[ZipRasterRequest] = []
        staged: list[ZipRasterRequest] = []
        monkeypatch.setattr(runtime, "has_unfinished_tasks", lambda: True)
        monkeypatch.setattr(
            runtime,
            "has_cached_current",
            lambda _request: False,
        )
        monkeypatch.setattr(
            runtime,
            "request",
            lambda request, **_kwargs: requested.append(request) or True,
        )
        monkeypatch.setattr(
            runtime,
            "stage",
            lambda request, **_kwargs: staged.append(request) or True,
        )
        assert window._finish_opened_book(opened, modal_on_empty=False)
        requested.clear()
        window._navigation_admission.reset()
        clock_values = iter(
            (1_000_000_000, 1_080_000_000, 1_100_000_000, 1_170_000_000)
        )
        monkeypatch.setattr(
            window._navigation_admission,
            "_clock",
            lambda: next(clock_values),
        )

        window.slider.setSliderDown(True)
        window._go_to_index_with_history(
            1,
            input_kind=NavigationInputKind.SLIDER_SCRUB,
        )
        window._go_to_index_with_history(
            2,
            input_kind=NavigationInputKind.SLIDER_SCRUB,
        )
        window._go_to_index_with_history(
            3,
            input_kind=NavigationInputKind.SLIDER_SCRUB,
        )
        assert [request.current.pages[0].page_index for request in requested] == [1, 2]
        assert [request.current.pages[0].page_index for request in staged] == [3]
        assert window._pending_zip_runtime_request is staged[-1]

        # A paced target promotes the latest staged intent and clears the
        # replaceable rapid-scrub boundary instead of stacking another job.
        window._go_to_index_with_history(
            4,
            input_kind=NavigationInputKind.SLIDER_SCRUB,
        )
        assert [request.current.pages[0].page_index for request in requested] == [1, 2, 4]
        assert window._pending_zip_runtime_request is None
        assert len(staged) == 1
        window.slider.setSliderDown(False)
    finally:
        window.close()
        qapp.processEvents()


def test_continuous_slider_scrub_periodically_admits_and_releases_latest(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    window, session, _source, archive = _window(tmp_path, pages=50)
    try:
        window.set_view_mode("single")
        opened = session.open_book(archive)
        runtime = session.viewer_runtime
        assert runtime is not None
        requested: list[ZipRasterRequest] = []
        staged: list[ZipRasterRequest] = []
        released: list[ZipRasterRequest] = []
        monkeypatch.setattr(runtime, "has_unfinished_tasks", lambda: True)
        monkeypatch.setattr(
            runtime,
            "has_cached_current",
            lambda _request: False,
        )
        monkeypatch.setattr(
            runtime,
            "request",
            lambda request, **_kwargs: requested.append(request) or True,
        )
        monkeypatch.setattr(
            runtime,
            "stage",
            lambda request, **_kwargs: staged.append(request) or True,
        )
        monkeypatch.setattr(
            runtime,
            "release_staged",
            lambda request: released.append(request) or True,
        )
        assert window._finish_opened_book(opened, modal_on_empty=False)
        requested.clear()
        window._navigation_admission.reset()
        clock_values = iter(index * 12_000_000 for index in range(40))
        monkeypatch.setattr(
            window._navigation_admission,
            "_clock",
            lambda: next(clock_values),
        )

        window.slider.setSliderDown(True)
        for target in range(1, 41):
            window._go_to_index_with_history(
                target,
                input_kind=NavigationInputKind.SLIDER_SCRUB,
            )

        assert 7 <= len(requested) <= 10
        assert requested[0].current.pages[0].page_index == 1
        assert staged[-1].current.pages[0].page_index == 40
        assert window._pending_zip_runtime_request is staged[-1]

        window.slider.setSliderDown(False)
        window._finish_slider_navigation()
        assert released[-1] is staged[-1]
        assert window._pending_zip_runtime_request is None
    finally:
        window.close()
        qapp.processEvents()


def test_wheel_cold_requests_follow_capacity_lane_and_ready_hits_stay_synchronous(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    window, session, _source, archive = _window(tmp_path, pages=16)
    try:
        window.set_view_mode("single")
        opened = session.open_book(archive)
        runtime = session.viewer_runtime
        assert runtime is not None
        requested: list[ZipRasterRequest] = []
        staged: list[tuple[ZipRasterRequest, bool]] = []
        released: list[ZipRasterRequest] = []
        ready_pages = {2, 3, 7}
        monkeypatch.setattr(runtime, "has_unfinished_tasks", lambda: True)
        monkeypatch.setattr(
            runtime, "has_cached_current",
            lambda request: request.current.pages[0].page_index in ready_pages,
        )
        monkeypatch.setattr(
            runtime, "request",
            lambda request, **_kwargs: requested.append(request) or True,
        )
        monkeypatch.setattr(
            runtime, "stage",
            lambda request, *, publish_cached=False: (
                staged.append((request, publish_cached)) or True
            ),
        )
        monkeypatch.setattr(
            runtime, "release_staged",
            lambda request: released.append(request) or True,
        )
        assert window._finish_opened_book(opened, modal_on_empty=False)
        monkeypatch.setattr(window, "_hold_unready_wheel_advance", lambda *_args: False)
        requested.clear()

        for index in range(10):
            window.viewer.wheelInputObserved.emit(1_000_000 + index * 20)
            window.next_page(input_kind=NavigationInputKind.WHEEL)

        assert [request.current.pages[0].page_index for request in requested] == list(range(1, 11))
        assert not staged
        assert window._pending_zip_runtime_request is None

        # Reversal remains immediate after both ready and cold targets.
        window.viewer.wheelInputObserved.emit(1_000_200)
        window.previous_page(input_kind=NavigationInputKind.WHEEL)
        assert requested[-1].current.pages[0].page_index == 9
        assert window._pending_zip_runtime_request is None
        window._finish_wheel_navigation()

        requested.clear()
        staged.clear()
        for index in range(4):
            window.viewer.wheelInputObserved.emit(1_000_300 + index * 8)
            window.next_page(input_kind=NavigationInputKind.WHEEL)
        assert [request.current.pages[0].page_index for request in requested] == [
            10, 11, 12, 13,
        ]
        assert not staged
        window._finish_wheel_navigation()
        assert not released
    finally:
        window.close()
        qapp.processEvents()


def test_fast_cold_wheel_commits_each_page_before_next_rapid_packet(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    window, session, _source, archive = _window(tmp_path, pages=6)
    window.set_view_mode("single")
    try:
        window.show()
        qapp.processEvents()
        assert window._finish_opened_book(
            session.open_book(archive), modal_on_empty=False
        )
        runtime = session.viewer_runtime
        assert runtime is not None
        _wait_until(qapp, lambda: runtime.cached_unit_count == 6)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        # Hold only background warmup. Every target below is a genuine cold
        # request through the production runtime and decoder.
        planner = runtime._warmup_planner
        assert planner is not None
        monkeypatch.setattr(type(planner), "next_candidate", lambda *_args, **_kwargs: None)
        for frame in tuple(runtime._frame_store.values()):
            if frame.unit.pages[0].page_index in (1, 2, 3, 4):
                runtime._frame_store.take(frame.key)
        runtime._source_store.clear()
        commits: list[int] = []
        window.presentationCommitted.connect(
            lambda commit: commits.append(commit.frame.unit.focused_index)
        )

        for index in range(1, 5):
            assert index not in runtime.cached_page_indexes
            window.viewer.wheelInputObserved.emit(1_000_000 + index * 8)
            window.next_page(input_kind=NavigationInputKind.WHEEL)
            assert window._pending_zip_runtime_request is None
            assert not runtime._dispatch_suspended
            _wait_until(qapp, lambda index=index: (
                window.presentation_state.displayed_page == index
            ))
        assert commits == [1, 2, 3, 4]
    finally:
        window.close()
        qapp.processEvents()


def test_rapid_wheel_holds_unready_target_and_finishes_started_work(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    archive = _write_zip(tmp_path, pages=12)

    class BlockingZipSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.page_one_started = Event()
            self.release_page_one = Event()

        def open_image(self, image_id: str) -> Image.Image:
            if image_id == "001.png":
                self.page_one_started.set()
                self.release_page_one.wait(3.0)
            return super().open_image(image_id)

    source = BlockingZipSource(archive)
    config = ConfigManager(tmp_path / "rapid-wheel-config.json")
    config.load()
    config.apply({"viewer_memory_mode": "4096"})
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
    )
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    try:
        window.set_view_mode("single")
        window.show()
        qapp.processEvents()
        opened = session.open_book(archive)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        runtime = session.viewer_runtime
        assert runtime is not None
        _wait_until(
            qapp,
            lambda: window.presentation_state.displayed is not None
            and window.presentation_state.displayed.unit.focused_index == 0,
        )
        assert source.page_one_started.wait(1.0)
        planner = runtime._warmup_planner
        assert planner is not None
        monkeypatch.setattr(type(planner), "next_candidate", lambda *_args, **_kwargs: None)
        started_job = runtime._active_job
        assert started_job is not None and started_job.started.is_set()

        for _index in range(10):
            window.viewer.wheelInputObserved.emit(1_000_000 + _index * 8)
            window.next_page(input_kind=NavigationInputKind.WHEEL)
        assert window.model.focused_index == 1
        requested = window.presentation_state.requested
        displayed = window.presentation_state.displayed
        assert requested is not None and requested.unit.focused_index == 1
        assert displayed is not None and displayed.unit.focused_index == 0
        assert runtime._active_job is started_job
        assert not started_job.cancelled.is_set()
        assert runtime.metrics.cancel_requests == 0
        assert not runtime._dispatch_suspended
        assert window._pending_zip_runtime_request is None
        assert len(runtime._jobs) <= 1

        # Repeated same-direction notches do not create a backlog. The
        # already-started page completes and becomes the next displayed unit.
        source.release_page_one.set()
        _wait_until(
            qapp,
            lambda: window.presentation_state.displayed is not None
            and window.presentation_state.displayed.unit.focused_index == 1,
            timeout_ms=5000,
        )
        assert 1 in runtime.cached_page_indexes
        assert runtime.metrics.cancel_requests == 0
        assert runtime.metrics.stale_results == 0
        assert runtime.metrics.warmup_planner_creations == 1

        # A fresh notch after completion can advance again; no old notches
        # are replayed automatically.
        for frame in tuple(runtime._frame_store.values()):
            if frame.unit.pages[0].page_index == 2:
                runtime._frame_store.take(frame.key)
        runtime._source_store.clear()
        assert 2 not in runtime.cached_page_indexes
        window.viewer.wheelInputObserved.emit(1_000_080)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        assert window._pending_zip_runtime_request is None
        assert not runtime._dispatch_suspended
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 2)
    finally:
        source.release_page_one.set()
        window.close()
        qapp.processEvents()


def test_actual_wheel_route_preempts_unrelated_cold_archive_work(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "cold-wheel-priority.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for index in range(8):
            encoded = BytesIO()
            with Image.new(
                "RGB",
                (2016, 1152),
                (25 + index * 19, 90 + index * 9, 160 - index * 11),
            ) as image:
                image.save(encoded, format="JPEG", quality=88)
            output.writestr(f"{index:03d}.jpg", encoded.getvalue())

    class GatedZipSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.background_started = Event()
            self.background_cancelled = Event()
            self.release_background = Event()
            self.cold_target_started = Event()
            self.release_cold_target = Event()
            self.gate_cold_target = False
            self.background_gate_entries = 0
            self.completed_payload_reads: list[str] = []
            self.payload_read_started_ns: dict[str, int] = {}
            self.payload_read_finished_ns: dict[str, int] = {}

        def _read_entry_qbytearray(self, image_id, cancelled):
            page_index = int(Path(image_id).stem)
            if page_index >= 4:
                self.background_gate_entries += 1
                self.background_started.set()
                # Model a non-interruptible read already owned by the worker.
                # Cancellation must keep its worker slot and reservation until
                # this test releases the read.
                while not self.release_background.wait(0.002):
                    if cancelled.is_set():
                        self.background_cancelled.set()
                self._raise_if_cancelled(cancelled)
            elif page_index == 3 and self.gate_cold_target:
                self.cold_target_started.set()
                while not self.release_cold_target.wait(0.002):
                    self._raise_if_cancelled(cancelled)
            self.payload_read_started_ns[image_id] = perf_counter_ns()
            result = super()._read_entry_qbytearray(image_id, cancelled)
            self.payload_read_finished_ns[image_id] = perf_counter_ns()
            self.completed_payload_reads.append(image_id)
            return result

    source = GatedZipSource(archive)
    config = ConfigManager(tmp_path / "cold-wheel-config.json")
    config.load()
    config.apply({"viewer_memory_mode": "4096"})
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
    )
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    window.set_view_mode("single")
    commits: dict[int, int] = {}
    paints: dict[int, int] = {}

    def record_commit(commit) -> None:
        commits[commit.frame.unit.focused_index] = perf_counter_ns()

    def record_paint(_serial: int, image_ids: object) -> None:
        if isinstance(image_ids, tuple) and image_ids:
            paints[int(Path(str(image_ids[0])).stem)] = perf_counter_ns()

    window.presentationCommitted.connect(record_commit)
    window.viewer.framePainted.connect(record_paint)

    def wheel_next(timestamp: int) -> None:
        event = QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        event.setTimestamp(timestamp)
        assert QApplication.sendEvent(window.viewer, event)

    try:
        window.show()
        qapp.processEvents()
        assert window._finish_opened_book(
            session.open_book(archive),
            modal_on_empty=False,
        )
        runtime = session.viewer_runtime
        assert runtime is not None
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        _wait_until(qapp, source.background_started.is_set, timeout_ms=5000)
        _wait_until(
            qapp,
            lambda: {1, 2, 3}.issubset(set(runtime.cached_page_indexes)),
            timeout_ms=5000,
        )
        old_job = runtime._active_job
        assert old_job is not None and old_job.started.is_set()
        assert old_job in runtime._inflight_reservations

        warm_latencies_ms: list[float] = []
        warm_paint_latencies_ms: list[float] = []
        for page_index in (1, 2):
            reads_before = tuple(source.completed_payload_reads)
            started = perf_counter_ns()
            wheel_next(10_000 + page_index * 1_000)
            _wait_until(
                qapp,
                lambda page_index=page_index: (
                    window.presentation_state.displayed_page == page_index
                ),
            )
            _wait_until(
                qapp,
                lambda page_index=page_index: page_index in paints,
            )
            warm_latencies_ms.append(
                (commits[page_index] - started) / 1_000_000
            )
            warm_paint_latencies_ms.append(
                (paints[page_index] - started) / 1_000_000
            )
            assert tuple(source.completed_payload_reads) == reads_before
            assert runtime._active_job is old_job
            assert not old_job.cancelled.is_set()

        for frame in tuple(runtime._frame_store.values()):
            if any(page.page_index == 3 for page in frame.unit.pages):
                runtime._frame_store.take(frame.key)
        runtime._source_store.clear()
        assert 3 not in runtime.cached_page_indexes
        page3_reads_before = source.completed_payload_reads.count("003.jpg")
        source.gate_cold_target = True
        cold_started = perf_counter_ns()
        wheel_next(20_000)
        assert window.presentation_state.requested_page == 3
        _wait_until(qapp, source.background_cancelled.is_set)
        assert old_job.cancelled.is_set()
        assert runtime._active_job is old_job
        assert old_job in runtime._inflight_reservations
        assert not source.cold_target_started.is_set()

        source.release_background.set()
        _wait_until(qapp, source.cold_target_started.is_set, timeout_ms=5000)
        _wait_until(
            qapp,
            lambda: old_job not in runtime._inflight_reservations,
            timeout_ms=5000,
        )
        # Keep the post-target warmup from reading page 4 before assertions;
        # its worker may start only after the cold current has completed.
        source.release_background.clear()
        cold_target_stage_started = perf_counter_ns()

        # A repeated notch while page 3 is still cold must not queue page 4.
        wheel_next(21_000)
        assert window.model.focused_index == 3
        assert window.presentation_state.requested_page == 3
        assert window._pending_zip_runtime_request is None

        source.release_cold_target.set()
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 3)
        _wait_until(qapp, lambda: 3 in paints)
        assert window.model.focused_index == 3
        assert window.presentation_state.requested_page == 3
        assert (
            source.completed_payload_reads.count("003.jpg")
            == page3_reads_before + 1
        )
        assert not any(
            int(Path(image_id).stem) >= 4
            for image_id in source.completed_payload_reads
        )
        assert runtime.metrics.stale_results == 0

        # These local synthetic-archive timings compare the real viewer route;
        # the gated slot adds test synchronization and is not a Windows claim.
        print(
            "cold-wheel-ms "
            f"warm-commit={[round(value, 2) for value in warm_latencies_ms]} "
            f"warm-paint={[round(value, 2) for value in warm_paint_latencies_ms]} "
            f"cold-input-to-paint={round((paints[3] - cold_started) / 1_000_000, 2)} "
            "cold-payload-read="
            f"{round((source.payload_read_finished_ns['003.jpg'] - source.payload_read_started_ns['003.jpg']) / 1_000_000, 2)} "
            "cold-payload-read-to-paint="
            f"{round((paints[3] - source.payload_read_finished_ns['003.jpg']) / 1_000_000, 2)} "
            "cold-target-stage-to-paint="
            f"{round((paints[3] - cold_target_stage_started) / 1_000_000, 2)}"
        )
    finally:
        source.release_background.set()
        source.release_cold_target.set()
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize(
    (
        "ready_pages",
        "final_page",
    ),
    (
        ({1, 2}, 3),
        (set(), 3),
        ({1, 3}, 4),
    ),
    ids=("ready", "cold", "mixed"),
)
def test_rapid_wheel_presents_only_ready_intermediate_frames(
    tmp_path: Path,
    qapp: QApplication,
    ready_pages: set[int],
    final_page: int,
) -> None:
    archive = _write_zip(tmp_path, pages=6)

    class ObservedZipSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.decode_starts: list[int] = []
            self.block_image_id: str | None = None
            self.final_started = Event()
            self.release_final = Event()

        def open_image(self, image_id: str) -> Image.Image:
            page_index = int(Path(image_id).stem)
            self.decode_starts.append(page_index)
            if image_id == self.block_image_id:
                self.final_started.set()
                self.release_final.wait(3.0)
            return super().open_image(image_id)

    source = ObservedZipSource(archive)
    config = ConfigManager(tmp_path / "ready-transit-config.json")
    config.load()
    config.apply({"viewer_memory_mode": "4096"})
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
    )
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    try:
        window.set_view_mode("single")
        window.show()
        qapp.processEvents()
        opened = session.open_book(archive)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        runtime = session.viewer_runtime
        assert runtime is not None

        # Populate legitimate display-ready frames through the production
        # request/commit path, independent of background warmup capacity.
        for page_index in range(6):
            window._go_to_index_with_history(page_index)
            _wait_until(
                qapp,
                lambda page_index=page_index: (
                    window.presentation_state.displayed_page == page_index
                ),
            )
        window._go_to_index_with_history(0)
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        cold_pages = set(range(1, final_page + 1)) - ready_pages
        for frame in tuple(runtime._frame_store.values()):
            if any(
                page.page_index in cold_pages for page in frame.unit.pages
            ):
                runtime._frame_store.take(frame.key)
        runtime._source_store.clear()
        assert all(
            (page_index in runtime.cached_page_indexes) == (
                page_index in ready_pages
            )
            for page_index in range(1, final_page + 1)
        )

        source.decode_starts.clear()
        source.block_image_id = f"{final_page:03d}.png"
        commits: list[int] = []
        paints: list[int] = []
        window.presentationCommitted.connect(
            lambda commit: commits.append(commit.frame.unit.focused_index)
        )

        def record_paint(_serial: int, image_ids: object) -> None:
            if not isinstance(image_ids, tuple) or not image_ids:
                return
            paints.append(int(Path(str(image_ids[0])).stem))

        window.viewer.framePainted.connect(record_paint)

        for _page_index in range(1, final_page + 1):
            window.next_page(input_kind=NavigationInputKind.WHEEL)
            assert window.slider.value() == _page_index
            assert f"{_page_index + 1} / 6" in window.status.currentMessage()
            if _page_index == 1 and _page_index in ready_pages:
                # A ready hit commits now but waits for Qt's posted paint.
                assert paints == []
            if _page_index < final_page and _page_index not in ready_pages:
                _wait_until(
                    qapp,
                    lambda page_index=_page_index: (
                        window.presentation_state.displayed_page == page_index
                    ),
                )
        window._finish_wheel_navigation()
        _wait_until(qapp, source.final_started.is_set)

        assert commits == list(range(1, final_page))
        assert window.slider.value() == final_page
        assert f"{final_page + 1} / 6" in window.status.currentMessage()
        assert window.presentation_state.requested_page == final_page
        assert window.presentation_state.displayed_page == final_page - 1
        assert final_page in source.decode_starts

        source.release_final.set()
        _wait_until(
            qapp,
            lambda: window.presentation_state.displayed_page == final_page,
        )
        _wait_until(qapp, lambda: paints and paints[-1] == final_page)
        assert commits == list(range(1, final_page + 1))
        assert runtime.metrics.stale_results == 0
    finally:
        source.release_final.set()
        window.close()
        qapp.processEvents()


def test_zip_book_uses_one_runtime_across_spread_rotation_filter_and_page_list(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    window, session, _source, archive = _window(tmp_path)
    try:
        opened = session.open_book(archive)
        runtime = session.viewer_runtime
        assert runtime is not None
        captured: list[ZipRasterRequest] = []
        accepting = [True]
        cancelled: list[bool] = []
        cancel_runtime = runtime.cancel
        monkeypatch.setattr(runtime, "has_cached_current", lambda _request: True)

        def accept_request(request: ZipRasterRequest, **_kwargs) -> bool:
            captured.append(request)
            return accepting[0]

        def cancel_request(*, clear_artifacts: bool) -> None:
            cancelled.append(clear_artifacts)
            cancel_runtime(clear_artifacts=clear_artifacts)

        monkeypatch.setattr(
            runtime,
            "request",
            accept_request,
        )
        monkeypatch.setattr(
            runtime,
            "cancel",
            cancel_request,
        )
        monkeypatch.setattr(
            window,
            "_render_spread",
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("ZIP book entered legacy Viewer pipeline")
            ),
        )

        assert window._finish_opened_book(opened, modal_on_empty=False)
        assert window._zip_runtime_active
        assert window.viewer._direct_display_mode

        window.set_view_mode("spread")
        window.rotate_right()
        window.set_viewer_downscale_algorithm("sharp")
        window.set_viewer_upscale_algorithm("lanczos")
        window._set_image_adjustments(brightness=1.2)
        window.page_list_dock.show()
        window._refresh_view()

        assert captured
        assert all(request.source_epoch == session.generation for request in captured)
        assert captured[-1].render_spec.rotation == 90
        assert captured[-1].render_spec.downscale_algorithm == "sharp"
        assert captured[-1].render_spec.upscale_algorithm == "lanczos"
        assert captured[-1].render_spec.brightness == 1.2
        assert captured[-1].render_spec.decoder_maximum_size is not None
        assert captured[-1].render_spec.decoder_headroom == 1.0
        assert captured[-1].render_spec.decoder_layout_sized
        window.fit_mode = "fit_width"
        assert window._current_book_runtime_decode_bounds()[1] is None
        window.fit_mode = "fit_height"
        assert window._current_book_runtime_decode_bounds()[0] is None
        window.fit_mode = "fit_window"
        assert window._zip_runtime is runtime
        assert window._zip_runtime_active
        assert window.viewer._direct_display_mode

        # A ready-path admission rejection must not leave the PageList or the
        # shared interactive lane suspended by an earlier cold request.
        window._hold_raster_interactive_lane()
        accepting[0] = False
        window._refresh_view()
        assert cancelled and cancelled[-1] is False
        assert not window._raster_interactive_lane_held
    finally:
        window.close()
        qapp.processEvents()


def test_zip_runtime_commits_complete_spread_and_retains_source_for_magnifier(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    window, session, _source, archive = _window(tmp_path)
    events: list[str] = []
    notify_page_changed = session.notify_page_changed

    def track_page_changed() -> None:
        events.append("notify")
        notify_page_changed()

    monkeypatch.setattr(
        session,
        "notify_page_changed",
        track_page_changed,
    )
    window.viewer.frameCommitted.connect(
        lambda *_args: events.append("commit")
    )
    window.viewer.framePainted.connect(
        lambda *_args: events.append("paint")
    )
    window.view_mode = "spread"
    window.single_first_page = False
    window.reading_direction = "ltr"
    window.model.update_options(
        view_mode="spread",
        single_first_page=False,
        reading_direction="ltr",
    )
    try:
        window.show()
        qapp.processEvents()
        opened = session.open_book(archive)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        runtime = session.viewer_runtime
        assert runtime is not None
        _wait_until(
            qapp,
            lambda: len(window.viewer._images) == 2
            and all(
                image.display_prepared and image.pixmap is not None
                for image in window.viewer._images
            ),
        )

        assert window.viewer.displayed_page_indexes == (0, 1)
        assert all(image.qimage is not None for image in window.viewer._images)
        assert window._applied_display_request_id == window._active_request_id
        assert window._zip_runtime_current_frame_serial > 0
        _wait_until(qapp, lambda: "notify" in events)
        assert events.index("commit") < events.index("paint") < events.index("notify")

        # If A's post-paint zero timer is still armed, a synchronous ready-hit
        # B commit must disarm it until B itself paints.
        window._presentation_side_effect_timer.start()
        assert window._presentation_side_effect_timer.isActive()
        window._refresh_view()
        assert not window._presentation_side_effect_timer.isActive()

        # The ZIP runtime owns the main display, but the NivisViewer
        # magnifier remains an interactive projection of the committed source.
        source_image = window.viewer._images[0]
        assert source_image.qimage is not None
        window.viewer.magnifier_selecting = True
        window.viewer.magnifier_source_page = source_image.page_index
        window.viewer._magnifier_source_image_id = source_image.image_id
        window.viewer.magnifier_source_rect = QRectF(
            0,
            0,
            max(1, source_image.qimage.width() // 2),
            max(1, source_image.qimage.height() // 2),
        )
        magnifier_request = window._zip_runtime_request(
            window.model.spread_at()
        )
        assert magnifier_request is not None
        assert not magnifier_request.warmup_plan.background_enabled
        assert tuple(magnifier_request.warmup_plan.iter_background_units()) == ()
        assert magnifier_request.render_spec.decoder_maximum_size is None
        window.viewer._request_magnifier_render()
        _wait_until(qapp, lambda: window.viewer.magnifier_active)
        assert window.viewer._magnifier_pixmap is not None
    finally:
        window.close()
        qapp.processEvents()


def test_raster_magnifier_cancel_adopts_retained_preview_once(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    window, session, _source, archive = _window(tmp_path)
    staged: list[ZipRasterRequest] = []
    requested: list[ZipRasterRequest] = []
    cancelled: list[bool] = []
    try:
        opened = session.open_book(archive)
        runtime = session.viewer_runtime
        assert runtime is not None
        real_stage = runtime.stage
        real_cancel = runtime.cancel

        def stage(request: ZipRasterRequest) -> bool:
            staged.append(request)
            return real_stage(request)

        def cancel(*, clear_artifacts: bool) -> None:
            cancelled.append(clear_artifacts)
            real_cancel(clear_artifacts=clear_artifacts)

        monkeypatch.setattr(runtime, "stage", stage)
        monkeypatch.setattr(runtime, "has_cached_current", lambda _request: True)
        monkeypatch.setattr(
            runtime,
            "request",
            lambda request, **_kwargs: requested.append(request) or True,
        )
        monkeypatch.setattr(runtime, "cancel", cancel)

        assert window._finish_opened_book(opened, modal_on_empty=False)
        requested.clear()

        # Cancellation adopts the normal preview key synchronously.  Runtime
        # stage is the cancellation/stale boundary for an incompatible
        # full-source promotion; it does not clear either retained frame tier.
        window.viewer.magnifier_active = True
        assert window.viewer.cancel_magnifier()
        assert len(staged) == 1
        assert staged[-1].render_spec.decoder_maximum_size is not None
        assert window._raster_magnifier_cancel_timer.isActive()
        assert not cancelled

        _wait_until(qapp, lambda: len(requested) == 1)
        assert requested[-1].render_spec.decoder_maximum_size is not None
        assert not window._raster_magnifier_cancel_timer.isActive()
        assert not cancelled

        # A layout command already owns its refresh.  The cancel fallback is
        # coalesced instead of publishing/requesting the same unit twice.
        staged.clear()
        requested.clear()
        window.viewer.magnifier_active = True
        window.set_view_mode("spread")
        assert len(staged) == 1
        assert len(requested) == 1
        qapp.processEvents()
        assert len(requested) == 1
        assert not window._raster_magnifier_cancel_timer.isActive()
        assert not cancelled
    finally:
        window.close()
        qapp.processEvents()


def test_book_switch_keeps_archive_alive_until_runtime_job_stops(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = _write_zip(tmp_path)

    class BlockingZipSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.entered = Event()
            self.release = Event()

        def open_image(self, image_id: str) -> Image.Image:
            self.entered.set()
            self.release.wait(3.0)
            return super().open_image(image_id)

    source = BlockingZipSource(archive)
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
    )
    try:
        session.open_book(archive)
        runtime = session.viewer_runtime
        assert runtime is not None
        page = runtime.source.list_images()[0]
        unit = ZipRasterDisplayUnit(
            0,
            (ZipRasterPage(0, page),),
            True,
        )
        topology = RasterBookTopology(
            (unit,),
            identity_of=lambda item: item.identity,
            page_indexes_of=lambda item: (
                raster_page.page_index for raster_page in item.pages
            ),
            page_count=1,
        )
        warmup_plan = RasterWarmupPlan(
            topology,
            current=unit,
            identity_of=lambda item: item.identity,
            page_indexes_of=lambda item: (
                raster_page.page_index for raster_page in item.pages
            ),
            direction=0,
            background_enabled=True,
        )
        assert runtime.request(
            ZipRasterRequest(
                session.generation,
                1,
                unit,
                warmup_plan,
                ZipRasterRenderSpec((640, 480)),
            )
        )
        assert source.entered.wait(2.0)

        session.close_book()
        qapp.processEvents()

        assert session.viewer_runtime is None
        assert not source._closed.is_set()

        source.release.set()
        assert runtime.wait_for_done(3000)
        _wait_until(qapp, source._closed.is_set)
    finally:
        source.release.set()
        session.shutdown(3000)
        qapp.processEvents()


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_or_cancelled_replacement_keeps_active_zip_runtime_epoch(
    tmp_path: Path,
    qapp: QApplication,
    cancelled: bool,
) -> None:
    archive = _write_zip(tmp_path)
    source = ZipImageSource(archive)
    replacement_entered = Event()
    release_replacement = Event()

    def source_factory(path: Path, **_kwargs):
        if path.name == "broken.zip":
            replacement_entered.set()
            release_replacement.wait(3.0)
            raise ImageSourceError("broken replacement")
        return source, None

    session = BookSession(source_factory=source_factory)
    config = ConfigManager(tmp_path / "failed-open-config.json")
    config.load()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    try:
        opened = session.open_book(archive)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        runtime = session.viewer_runtime
        active_epoch = session.generation
        assert runtime is not None
        planner = runtime._warmup_planner
        assert planner is not None
        _wait_until(qapp, lambda: window._zip_runtime_current_frame_serial > 0)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        jobs_before_failed_open = runtime.metrics.jobs_submitted
        old_displayed_page = window.presentation_state.displayed_page
        old_slider = window.slider.value()
        old_status = window.status.currentMessage()
        old_progress = window.presentation_state.progress_page
        old_back_history = window.presentation_state.back_history
        old_pixmap_key = window.viewer._images[0].pixmap.cacheKey()

        window.open_path(tmp_path / "broken.zip")
        assert replacement_entered.wait(2.0)
        assert window.presentation_state.replacement_open_pending
        assert window.presentation_state.displayed_page == old_displayed_page
        assert window.slider.value() == old_slider
        assert runtime.metrics.jobs_submitted == jobs_before_failed_open
        assert not window._zip_runtime_active
        assert runtime._warmup_planner is planner

        window._on_viewport_changed()
        QTest.qWait(150)
        qapp.processEvents()
        assert window.presentation_state.replacement_open_pending
        assert not window._zip_runtime_active
        assert runtime.metrics.jobs_submitted == jobs_before_failed_open
        assert runtime._warmup_planner is planner
        assert window.viewer._images[0].pixmap.cacheKey() == old_pixmap_key

        if cancelled:
            session.cancel_pending_open()
        release_replacement.set()
        assert session.wait_for_async(3000)
        _wait_until(
            qapp,
            lambda: window._zip_runtime_active
            and window._zip_runtime_current_frame_serial > 0,
        )

        assert session.generation == active_epoch
        assert session.viewer_runtime is runtime
        assert window._zip_runtime is runtime
        assert runtime._warmup_planner is planner
        assert runtime.metrics.warmup_planner_creations == 1
        assert window.viewer.displayed_page_indexes == (0,)
        assert window.presentation_state.current_book_epoch == active_epoch
        assert window.presentation_state.displayed_page == old_displayed_page
        assert window.presentation_state.progress_page == old_progress
        assert window.presentation_state.back_history == old_back_history
        assert window.slider.value() == old_slider
        assert (
            window.presentation_state.surface.mode
            is PresentationSurfaceMode.DISPLAYED
        )
        assert window.viewer._images[0].pixmap.cacheKey() == old_pixmap_key
        # The temporary open-error override may still be visible. Once it is
        # released, the committed presentation status remains the old book.
        window._clear_status_override()
        window._update_status()
        assert window.status.currentMessage() == old_status
    finally:
        release_replacement.set()
        window.close()
        qapp.processEvents()
