from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic
import zipfile

from PIL import Image
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
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


def test_viewer_releases_final_frame_completed_while_staged(tmp_path, qapp):
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
        assert window._pending_zip_runtime_request is not None
        source.release.set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert window.presentation_state.displayed_page == 0
        assert window.presentation_state.requested_page == 1
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
        ] == [6, 5, 7, 4, 8]

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
        ] == [6, 5, 7, 4, 8]

        window.prefetch_preset = "standard"
        window.fit_mode = "actual_size"
        full_source_request = window._zip_runtime_request(
            window.model.spread_at()
        )
        assert full_source_request is not None
        assert full_source_request.render_spec.decoder_maximum_size is None
        assert not full_source_request.warmup_plan.background_enabled
        assert tuple(full_source_request.warmup_plan.iter_background_units()) == ()

        window.set_view_mode("spread")
        layout_request = window._zip_runtime_request(window.model.spread_at())
        assert layout_request is not None
        assert layout_request.warmup_plan.topology is not initial_topology
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
        assert source.entered.wait(2.0)
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
            lambda request: requested.append(request) or True,
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

        # The first wheel packet remains immediate. Once the second packet
        # proves a rapid burst, following targets replace the staged target
        # even if a small-image worker happens to become idle between packets.
        busy[0] = True
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        assert requested[-1].current.pages[0].page_index == 2
        assert [request.current.pages[0].page_index for request in staged] == [3, 4]
        assert window._pending_zip_runtime_request is staged[-1]

        busy[0] = False
        wheel_end = _wheel_end_event()
        window.viewer.wheelEvent(wheel_end)
        assert wheel_end.isAccepted()
        assert released[-1] is staged[-1]
        assert window._pending_zip_runtime_request is None

        # A ready frame can publish during the same wheel cadence, but it now
        # remains staged so recentered warmup cannot start a cold transit page
        # before the final wheel target is known.
        busy[0] = True
        cached[0] = True
        staged_count = len(staged)
        requested_count = len(requested)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        assert len(requested) == requested_count
        assert len(staged) == staged_count + 1
        assert staged[-1].current.pages[0].page_index == 5
        assert window._pending_zip_runtime_request is staged[-1]
        window._finish_wheel_navigation()
        assert released[-1] is staged[-1]

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


def test_rapid_wheel_preempts_started_warmup_and_commits_only_final_target(
    tmp_path: Path,
    qapp: QApplication,
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
        started_job = runtime._active_job
        assert started_job is not None and started_job.started.is_set()

        for _index in range(10):
            window.next_page(input_kind=NavigationInputKind.WHEEL)
        assert window.model.focused_index == 10
        requested = window.presentation_state.requested
        displayed = window.presentation_state.displayed
        assert requested is not None and requested.unit.focused_index == 10
        assert displayed is not None and displayed.unit.focused_index == 0
        assert runtime._active_job is started_job
        assert started_job.cancelled.is_set()
        assert runtime.metrics.cancel_requests == 1
        assert runtime.metrics.running_job_adoptions == 0

        # Production's wheel boundary admits the exact final request. The
        # cancelled warmup never becomes a cache artifact; only page 10 can
        # atomically replace the displayed page 0 presentation.
        window._finish_wheel_navigation()
        source.release_page_one.set()
        _wait_until(
            qapp,
            lambda: window.presentation_state.displayed is not None
            and window.presentation_state.displayed.unit.focused_index == 10,
            timeout_ms=5000,
        )
        assert 1 not in runtime.cached_page_indexes
        assert runtime.metrics.cancel_requests == 1
        assert runtime.metrics.stale_results == 0
        assert runtime.metrics.warmup_planner_creations == 1
    finally:
        source.release_page_one.set()
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize(
    (
        "ready_pages",
        "final_page",
        "expected_intermediate_commits",
        "expected_intermediate_paint",
        "forbidden_transit_decodes",
    ),
    (
        ({1, 2}, 3, [1, 2], 2, set()),
        (set(), 3, [], None, {2}),
        ({1, 3}, 4, [1, 3], 3, {2}),
    ),
    ids=("ready", "cold", "mixed"),
)
def test_rapid_wheel_presents_only_ready_intermediate_frames(
    tmp_path: Path,
    qapp: QApplication,
    ready_pages: set[int],
    final_page: int,
    expected_intermediate_commits: list[int],
    expected_intermediate_paint: int | None,
    forbidden_transit_decodes: set[int],
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
        window._finish_wheel_navigation()
        if expected_intermediate_paint is None:
            assert all(page_index == 0 for page_index in paints)
        else:
            # The final cold job is admitted first. The latest legitimate
            # ready transit frame is then painted synchronously so a queued
            # run of native wheel messages cannot starve its update event.
            assert paints and paints[-1] == expected_intermediate_paint
        _wait_until(qapp, source.final_started.is_set)

        assert commits == expected_intermediate_commits
        assert window.slider.value() == final_page
        assert f"{final_page + 1} / 6" in window.status.currentMessage()
        assert window.presentation_state.requested_page == final_page
        assert window.presentation_state.displayed_page == (
            expected_intermediate_commits[-1]
            if expected_intermediate_commits
            else 0
        )
        assert forbidden_transit_decodes.isdisjoint(source.decode_starts)
        assert final_page in source.decode_starts

        source.release_final.set()
        _wait_until(
            qapp,
            lambda: window.presentation_state.displayed_page == final_page,
        )
        _wait_until(qapp, lambda: paints and paints[-1] == final_page)
        assert commits == [*expected_intermediate_commits, final_page]
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

        def accept_request(request: ZipRasterRequest) -> bool:
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
            lambda request: requested.append(request) or True,
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
