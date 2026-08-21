from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic
import zipfile

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.image_source import ImageSourceError, ZipImageSource
from app.viewer_navigation_policy import NavigationInputKind
from app.viewer_window import ViewerWindow
from app.zip_raster_book_runtime import (
    ZipRasterDisplayUnit,
    ZipRasterPage,
    ZipRasterRenderSpec,
    ZipRasterRequest,
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
        assert [
            unit.pages[0].page_index for unit in request.work_order
        ] == list(range(12))
        assert window.viewer_cache_budget_bytes == 4096 * 1024 * 1024
        assert runtime.cache_byte_budget == window.viewer_cache_budget_bytes
        assert runtime.cache_unit_limit >= 12

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
        assert [
            unit.pages[0].page_index
            for unit in reversed_request.work_order[:5]
        ] == [6, 5, 7, 4, 8]

        window.prefetch_preset = "disabled"
        disabled_request = window._zip_runtime_request(
            window.model.spread_at()
        )
        assert disabled_request is not None
        assert disabled_request.work_order == (disabled_request.current,)

        window.prefetch_preset = "standard"
        window.fit_mode = "actual_size"
        full_source_request = window._zip_runtime_request(
            window.model.spread_at()
        )
        assert full_source_request is not None
        assert full_source_request.render_spec.decoder_maximum_size is None
        assert full_source_request.work_order == (
            full_source_request.current,
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
            lambda request: staged.append(request) or True,
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
        assert requested[-1] is staged[-1]
        assert window._pending_zip_runtime_request is None

        # Even inside the same wheel cadence a ready frame bypasses stage,
        # worker creation, and the timer completely.
        busy[0] = True
        cached[0] = True
        staged_count = len(staged)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        assert requested[-1].current.pages[0].page_index == 5
        assert len(staged) == staged_count

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
        assert requested[-1] is staged[-1]
    finally:
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
        window.set_viewer_resampling_mode("high_quality")
        window._set_image_adjustments(brightness=1.2)
        window.page_list_dock.show()
        window._refresh_view()

        assert captured
        assert all(request.source_epoch == session.generation for request in captured)
        assert captured[-1].render_spec.rotation == 90
        assert captured[-1].render_spec.resampling_mode == "high_quality"
        assert captured[-1].render_spec.brightness == 1.2
        assert captured[-1].render_spec.decoder_maximum_size is not None
        assert captured[-1].render_spec.decoder_headroom == 2.0
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
        assert magnifier_request.work_order == (magnifier_request.current,)
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
        assert runtime.request(
            ZipRasterRequest(
                session.generation,
                1,
                unit,
                (unit,),
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


def test_failed_replacement_open_keeps_active_zip_runtime_epoch(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = _write_zip(tmp_path)
    source = ZipImageSource(archive)

    def source_factory(path: Path, **_kwargs):
        if path.name == "broken.zip":
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
        _wait_until(qapp, lambda: window._zip_runtime_current_frame_serial > 0)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        jobs_before_failed_open = runtime.metrics.jobs_submitted
        old_displayed_page = window.presentation_state.displayed_page
        old_slider = window.slider.value()
        old_status = window.status.currentMessage()
        old_progress = window.presentation_state.progress_page
        old_back_history = window.presentation_state.back_history

        window.open_path(tmp_path / "broken.zip")
        assert window.presentation_state.replacement_open_pending
        assert window.presentation_state.displayed_page == old_displayed_page
        assert window.slider.value() == old_slider
        assert runtime.metrics.jobs_submitted == jobs_before_failed_open
        assert session.wait_for_async(3000)
        _wait_until(
            qapp,
            lambda: window._zip_runtime_active
            and window._zip_runtime_current_frame_serial > 0,
        )

        assert session.generation == active_epoch
        assert session.viewer_runtime is runtime
        assert window._zip_runtime is runtime
        assert window.viewer.displayed_page_indexes == (0,)
        assert window.presentation_state.current_book_epoch == active_epoch
        assert window.presentation_state.displayed_page == old_displayed_page
        assert window.presentation_state.progress_page == old_progress
        assert window.presentation_state.back_history == old_back_history
        assert window.slider.value() == old_slider
        # The temporary open-error override may still be visible. Once it is
        # released, the committed presentation status remains the old book.
        window._clear_status_override()
        window._update_status()
        assert window.status.currentMessage() == old_status
    finally:
        window.close()
        qapp.processEvents()
