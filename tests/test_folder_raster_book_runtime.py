from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event, Lock
from time import monotonic, sleep

from PIL import Image
import pytest
from PySide6.QtCore import QRunnable
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.image_work_coordinator import ImageWorkCoordinator
from app.image_source import FolderImageSource
from app.image_source import ZipImageSource
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
from app.raster_book_runtime import (
    RasterDisplayUnit,
    RasterFrame,
    RasterPage,
    RasterRenderSpec,
    RasterRequest,
)
from app.viewer_window import ViewerWindow
from app.viewer_navigation_policy import NavigationInputKind
from app.zip_raster_book_runtime import ZipRasterBookRuntime, _ZipRasterUnitJob
from tests.test_zip_raster_book_runtime import (
    _request as _zip_request,
    _unit as _zip_unit,
    _write_zip,
)


def _write_folder(root: Path, *, pages: int = 3, suffix: str = ".png") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(pages):
        with Image.new(
            "RGB",
            (120 + index * 10, 180 + index * 10),
            (40 + index * 30, 80, 120),
        ) as image:
            image.save(root / f"{index}{suffix}")
    return root


def _unit(source: FolderImageSource, *indexes: int) -> RasterDisplayUnit:
    image_ids = source.list_images()
    return RasterDisplayUnit(
        indexes[0],
        tuple(
            RasterPage(index, image_ids[index], source.logical_size(image_ids[index]))
            for index in indexes
        ),
        len(indexes) == 1,
    )


def _request(
    request_id: int,
    current: RasterDisplayUnit,
    *work_order: RasterDisplayUnit,
    spec: RasterRenderSpec | None = None,
    direction: int = 0,
) -> RasterRequest:
    units = tuple(
        sorted(
            dict.fromkeys(work_order or (current,)),
            key=lambda unit: min(page.page_index for page in unit.pages),
        )
    )
    topology = RasterBookTopology(
        units,
        identity_of=lambda unit: unit.identity,
        page_indexes_of=lambda unit: (
            page.page_index for page in unit.pages
        ),
        page_count=max(
            (page.page_index for unit in units for page in unit.pages),
            default=-1,
        )
        + 1,
    )
    return RasterRequest(
        1,
        request_id,
        current,
        RasterWarmupPlan(
            topology,
            current=current,
            identity_of=lambda unit: unit.identity,
            page_indexes_of=lambda unit: (
                page.page_index for page in unit.pages
            ),
            direction=direction,
            background_enabled=len(units) > 1,
        ),
        spec or RasterRenderSpec((640, 480)),
        navigation_direction=direction,
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


def test_ready_wheel_keeps_folder_prefetch_running_and_latest_cold_wins(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class ControlledFolderSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.block = False
            self.started_six = Event()
            self.started_seven = Event()
            self.release_six = Event()
            self.release_seven = Event()

        def open_image(self, image_id: str) -> Image.Image:
            if self.block:
                index = int(Path(image_id).stem)
                if index == 6:
                    self.started_six.set()
                    self.release_six.wait(3)
                elif index == 7:
                    self.started_seven.set()
                    self.release_seven.wait(3)
            return super().open_image(image_id)

    folder = _write_folder(tmp_path / "book", pages=10)
    source = ControlledFolderSource(folder)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    session = BookSession(
        source_factory=lambda *_args, **_kwargs: (source, None),
        folder_worker_limit=1,
    )
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    window.set_view_mode("single")
    try:
        window.show()
        qapp.processEvents()
        assert window._finish_opened_book(session.open_book(folder), modal_on_empty=False)
        runtime = session.viewer_runtime
        assert runtime is not None
        _wait_until(qapp, lambda: runtime.cached_unit_count == 10 and not runtime.has_unfinished_tasks())
        for frame in tuple(runtime._frame_store.values()):
            if frame.unit.pages[0].page_index >= 6:
                runtime._frame_store.take(frame.key)
        runtime._source_store.clear()
        assert set(runtime.cached_page_indexes) == set(range(6))
        source.block = True
        commits: list[int] = []
        window.presentationCommitted.connect(
            lambda commit: commits.append(commit.frame.unit.focused_index)
        )

        window.viewer.wheelInputObserved.emit(1_000_000)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        assert window.presentation_state.displayed_page == 1
        assert source.started_six.wait(1)
        assert runtime._active_job is not None
        assert not runtime._dispatch_suspended
        assert not window._zip_runtime_request_timer.isActive()

        window.viewer.wheelInputObserved.emit(1_000_008)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        window.viewer.wheelInputObserved.emit(1_000_016)
        window.previous_page(input_kind=NavigationInputKind.WHEEL)
        assert window.presentation_state.displayed_page == 1
        assert len(runtime._jobs) <= 1
        source.release_six.set()
        _wait_until(qapp, source.started_seven.is_set)
        assert 6 in runtime.cached_page_indexes
        assert runtime._active_job is not None
        assert not runtime._dispatch_suspended

        window.viewer.wheelInputObserved.emit(1_000_024)
        window._go_to_index_with_history(8, input_kind=NavigationInputKind.WHEEL)
        window.viewer.wheelInputObserved.emit(1_000_032)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        # A pending direct seek remains the destination; wheel input cannot
        # race one more page ahead of its missing image.
        assert window.presentation_state.requested_page == 8
        assert window._pending_zip_runtime_request is None
        assert len(runtime._jobs) <= 1
        source.release_seven.set()
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 8)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 9)
        assert 7 not in commits
        assert commits[-1] == 9
        assert 7 in runtime.cached_page_indexes
        assert runtime.metrics.cancel_requests == 0
        assert not runtime._dispatch_suspended
    finally:
        source.release_six.set()
        source.release_seven.set()
        window.close()
        qapp.processEvents()


def test_folder_wheel_preserves_started_neighbor_then_promotes_reversed_current(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class BlockingFolderSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = Event()
            self.release = Event()
            self.opens: list[int] = []

        def open_image(self, image_id: str) -> Image.Image:
            index = int(Path(image_id).stem)
            self.opens.append(index)
            if index == 1:
                self.started.set()
                self.release.wait(3)
            return super().open_image(image_id)

    source = BlockingFolderSource(_write_folder(tmp_path / "book", pages=4))
    runtime = FolderRasterBookRuntime(source, 1)
    units = tuple(_unit(source, index) for index in range(4))
    frames: list[RasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(_request(1, units[0], *units, direction=1))
        _wait_until(qapp, lambda: frames and frames[-1].request_id == 1)
        assert runtime.release_continuous_warmup(request_id=1)
        assert source.started.wait(1)
        started_job = runtime._active_job
        assert started_job is not None and started_job.started.is_set()

        assert runtime.request(
            _request(2, units[2], *units, direction=1),
            preserve_started_compatible=True,
        )
        assert runtime._active_job is started_job
        assert not started_job.cancelled.is_set()
        assert len(runtime._jobs) <= 1

        assert runtime.request(
            _request(3, units[1], *units, direction=-1),
            preserve_started_compatible=True,
        )
        assert runtime._active_job is started_job
        assert not started_job.cancelled.is_set()
        source.release.set()
        _wait_until(qapp, lambda: frames[-1].request_id == 3)
        assert [frame.request_id for frame in frames] == [1, 3]
        assert 1 in runtime.cached_page_indexes
        assert runtime.metrics.cancel_requests == 0
        assert source.opens[:2] == [0, 1]
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
    finally:
        source.release.set()
        assert runtime.shutdown(wait_msecs=3000)


def test_ready_folder_wheel_queues_latest_committed_frame_paint(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = _write_folder(tmp_path / "book", pages=4)
    source = FolderImageSource(folder)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    session = BookSession(source_factory=lambda *_args, **_kwargs: (source, None))
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    window.set_view_mode("single")
    try:
        window.show()
        qapp.processEvents()
        assert window._finish_opened_book(session.open_book(folder), modal_on_empty=False)
        runtime = session.viewer_runtime
        assert runtime is not None
        _wait_until(qapp, lambda: runtime.cached_unit_count == 4 and not runtime.has_unfinished_tasks())
        commits: list[int] = []
        paints: list[int] = []
        window.presentationCommitted.connect(
            lambda commit: commits.append(commit.frame.unit.focused_index)
        )
        window.viewer.framePainted.connect(
            lambda _serial, ids: paints.append(int(Path(ids[0]).stem))
        )

        # Deliberately do not pump queued Qt paint events between inputs.
        for timestamp, advance in ((1_000_000, True), (1_000_008, True), (1_000_016, False)):
            window.viewer.wheelInputObserved.emit(timestamp)
            if advance:
                window.next_page(input_kind=NavigationInputKind.WHEEL)
            else:
                window.previous_page(input_kind=NavigationInputKind.WHEEL)
        assert commits == [1, 2, 1]
        assert paints == []
        _wait_until(qapp, lambda: bool(paints))
        assert paints[-1] == 1
        assert window.presentation_state.displayed_page == 1
        assert runtime.metrics.cancel_requests == 0
    finally:
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize("view_mode", ["single", "spread"])
@pytest.mark.parametrize("reading_direction", ["ltr", "rtl"])
def test_cold_wheel_holds_one_unit_and_reversal_returns_to_ready_frame(
    tmp_path: Path,
    qapp: QApplication,
    view_mode: str,
    reading_direction: str,
) -> None:
    class BlockingSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.block = False
            self.started = Event()
            self.release = Event()

        def open_image(self, image_id: str) -> Image.Image:
            if self.block and int(Path(image_id).stem) > 0:
                self.started.set()
                self.release.wait(3)
            return super().open_image(image_id)

    folder = _write_folder(tmp_path / "book", pages=6)
    source = BlockingSource(folder)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    session = BookSession(source_factory=lambda *_args, **_kwargs: (source, None))
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    window.set_view_mode(view_mode)
    window.set_reading_direction(reading_direction)
    if view_mode == "spread":
        window.set_single_first_page(True)
    try:
        window.show()
        qapp.processEvents()
        assert window._finish_opened_book(session.open_book(folder), modal_on_empty=False)
        runtime = session.viewer_runtime
        assert runtime is not None and runtime._max_active_jobs == 1
        _wait_until(
            qapp,
            lambda: (
                window.presentation_state.displayed_page == 0
                and window.presentation_state.displayed is not None
            ),
        )
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        first = window.presentation_state.displayed
        assert first is not None
        for frame in tuple(runtime._frame_store.values()):
            if frame.unit.identity != first.unit.identity:
                runtime._frame_store.take(frame.key)
        runtime._source_store.clear()
        source.block = True

        window.next_page(input_kind=NavigationInputKind.WHEEL)
        assert source.started.wait(1)
        pending = window.presentation_state.requested
        assert pending is not None and pending.unit.identity != first.unit.identity
        for _ in range(5):
            window.next_page(input_kind=NavigationInputKind.WHEEL)
        window.slider.nextDisplayUnitRequested.emit()
        assert window.presentation_state.requested is pending
        assert runtime.metrics.cancel_requests == 0

        window.previous_page(input_kind=NavigationInputKind.WHEEL)
        assert window.presentation_state.displayed is not None
        assert window.presentation_state.displayed.unit.identity == first.unit.identity
        source.release.set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert runtime.metrics.cancel_requests == 0
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        assert window.presentation_state.displayed is not None
        assert window.presentation_state.displayed.unit.identity == pending.unit.identity
    finally:
        source.release.set()
        window.close()
        qapp.processEvents()


def test_failed_folder_page_does_not_hold_later_wheel_navigation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = _write_folder(tmp_path / "book", pages=3)
    (folder / "1.png").write_bytes(b"broken image")
    source = FolderImageSource(folder)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    session = BookSession(source_factory=lambda *_args, **_kwargs: (source, None))
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    window.set_view_mode("single")
    try:
        window.show()
        qapp.processEvents()
        assert window._finish_opened_book(session.open_book(folder), modal_on_empty=False)
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 1)
        assert window.presentation_state.displayed is not None
        assert window.presentation_state.displayed.has_errors
        window.next_page(input_kind=NavigationInputKind.WHEEL)
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 2)
    finally:
        window.close()
        qapp.processEvents()


def test_folder_warmup_reads_without_waiting_for_a_paint_event(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class ObservedSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.neighbor_started = Event()

        def open_image(self, image_id: str) -> Image.Image:
            if Path(image_id).stem == "1":
                self.neighbor_started.set()
            return super().open_image(image_id)

    folder = _write_folder(tmp_path / "book", pages=3)
    source = ObservedSource(folder)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    session = BookSession(source_factory=lambda *_args, **_kwargs: (source, None))
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    window.set_view_mode("single")
    paints: list[object] = []
    window.viewer.framePainted.connect(lambda *_args: paints.append(_args))
    try:
        # A hidden widget has no physical paint acknowledgement. Its first
        # atomic commit must still release continuous one-worker warm-up.
        assert window._finish_opened_book(session.open_book(folder), modal_on_empty=False)
        _wait_until(qapp, source.neighbor_started.is_set)
        assert paints == []
        assert session.viewer_runtime is not None
        assert session.viewer_runtime.metrics.continuous_warmup_releases == 1
    finally:
        window.close()
        qapp.processEvents()


def test_folder_wheel_cancels_started_work_for_incompatible_render(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class BlockingFolderSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = Event()
            self.release = Event()

        def open_image(self, image_id: str) -> Image.Image:
            if Path(image_id).stem == "0":
                self.started.set()
                self.release.wait(3)
            return super().open_image(image_id)

    source = BlockingFolderSource(_write_folder(tmp_path / "book", pages=3))
    runtime = FolderRasterBookRuntime(source, 1)
    units = tuple(_unit(source, index) for index in range(3))
    frames: list[RasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(_request(1, units[0], *units))
        assert source.started.wait(1)
        started_job = runtime._active_job
        assert started_job is not None and started_job.started.is_set()
        assert runtime.request(
            _request(2, units[2], *units, spec=RasterRenderSpec((800, 600))),
            preserve_started_compatible=True,
        )
        assert started_job.cancelled.is_set()
        source.release.set()
        _wait_until(qapp, lambda: frames and frames[-1].request_id == 2)
        assert [frame.request_id for frame in frames] == [2]
        assert runtime.metrics.cancel_requests == 1
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
    finally:
        source.release.set()
        assert runtime.shutdown(wait_msecs=3000)


@pytest.mark.parametrize("release_first", (1, 2))
@pytest.mark.parametrize("coordinated", (False, True))
@pytest.mark.parametrize("targets", ((5,), (5, 4, 5, 3)))
def test_two_folder_workers_run_and_cold_current_takes_next_free_slot(
    tmp_path: Path,
    qapp: QApplication,
    release_first: int,
    coordinated: bool,
    targets: tuple[int, ...],
) -> None:
    class ControlledSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = {index: Event() for index in (1, 2, 3, 4, 5)}
            self.release = {index: Event() for index in (1, 2)}

        def open_image(self, image_id: str) -> Image.Image:
            index = int(Path(image_id).stem)
            if index in self.started:
                self.started[index].set()
            if index in self.release:
                self.release[index].wait(3)
            return super().open_image(image_id)

    source = ControlledSource(_write_folder(tmp_path / "book", pages=6))
    coordinator = (
        ImageWorkCoordinator(max_workers=2, folder_supplemental_workers=1)
        if coordinated else None
    )
    runtime = FolderRasterBookRuntime(
        source, 1, image_work_coordinator=coordinator, max_active_jobs=2,
    )
    units = tuple(_unit(source, index) for index in range(6))
    frames: list[RasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(_request(1, units[0], *units, direction=1))
        _wait_until(qapp, lambda: frames and frames[-1].request_id == 1)
        assert runtime.release_continuous_warmup(request_id=1)
        _wait_until(qapp, source.started[1].is_set)
        _wait_until(qapp, source.started[2].is_set)
        assert runtime.active_job_count == 2
        assert len(runtime._jobs) == 2

        for request_id, target in enumerate(targets, start=2):
            final = _request(request_id, units[target], *units, direction=1)
            assert runtime.request(final, preserve_started_compatible=True)
        latest = targets[-1]
        assert not source.started[latest].is_set()
        assert len(runtime._jobs) == 2
        source.release[release_first].set()
        _wait_until(qapp, source.started[latest].is_set)
        assert not source.release[3 - release_first].is_set()
        final_request_id = len(targets) + 1
        _wait_until(qapp, lambda: frames[-1].request_id == final_request_id)
        assert [frame.request_id for frame in frames] == [1, final_request_id]
        assert release_first in runtime.cached_page_indexes
        assert runtime.active_job_count <= 2
        source.release[3 - release_first].set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
    finally:
        for event in source.release.values():
            event.set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert runtime.shutdown(wait_msecs=3000)
        if coordinator is not None:
            assert coordinator.shutdown(wait_msecs=3000)


def test_two_folder_inflight_reservations_cancel_on_limit_shrink(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class BlockedSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = {1: Event(), 2: Event()}
            self.release = Event()

        def open_image(self, image_id: str) -> Image.Image:
            index = int(Path(image_id).stem)
            if index in self.started:
                self.started[index].set()
                self.release.wait(3)
            return super().open_image(image_id)

    source = BlockedSource(_write_folder(tmp_path / "book", pages=4))
    runtime = FolderRasterBookRuntime(source, 1, max_active_jobs=2)
    units = tuple(_unit(source, index) for index in range(4))
    frames: list[RasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(_request(1, units[0], *units, direction=1))
        _wait_until(qapp, lambda: bool(frames))
        assert runtime.release_continuous_warmup(request_id=1)
        assert source.started[1].wait(1)
        assert source.started[2].wait(1)
        before = runtime.cache_debug_values()
        assert before["active_job_count"] == 2
        assert before["inflight_reservation_bytes"] > 0

        hard = runtime.cache_bytes + 1
        runtime.set_memory_limits(hard_limit_bytes=hard, soft_target_bytes=hard)
        assert all(job.cancelled.is_set() for job in runtime._active_slots())
        source.release.set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        after = runtime.cache_debug_values()
        assert after["inflight_reservation_bytes"] == 0
        assert runtime.cache_bytes <= hard
        assert runtime.cached_page_indexes == (0,)
    finally:
        source.release.set()
        assert runtime.shutdown(wait_msecs=3000)


def test_two_folder_prefetch_jobs_do_not_spend_same_free_budget(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class BlockedSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = {1: Event(), 2: Event()}
            self.release = Event()

        def open_image(self, image_id: str) -> Image.Image:
            index = int(Path(image_id).stem)
            if index in self.started:
                self.started[index].set()
                self.release.wait(3)
            return super().open_image(image_id)

    source = BlockedSource(_write_folder(tmp_path / "book", pages=3))
    runtime = FolderRasterBookRuntime(source, 1, max_active_jobs=2)
    units = tuple(_unit(source, index) for index in range(3))
    frames: list[RasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        initial = _request(1, units[0], *units, direction=1)
        assert runtime.request(initial)
        _wait_until(qapp, lambda: bool(frames))
        def cost(unit):
            source_bytes = runtime._estimated_missing_source_bytes(unit, initial.render_spec)
            return source_bytes + runtime._estimated_frame_bytes(
                unit, initial.render_spec, source_bytes=source_bytes
            )
        first_cost, second_cost = cost(units[1]), cost(units[2])
        hard = runtime.cache_bytes + first_cost + max(1, second_cost // 2)
        runtime.set_memory_limits(hard_limit_bytes=hard, soft_target_bytes=hard)
        assert runtime.release_continuous_warmup(request_id=1)
        assert source.started[1].wait(1)
        qapp.processEvents()
        assert not source.started[2].is_set()
        assert runtime.active_job_count == 1
        debug = runtime.cache_debug_values()
        assert debug["cache_used_bytes"] + debug["inflight_reservation_bytes"] <= hard
        source.release.set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert runtime.cache_bytes <= hard
    finally:
        source.release.set()
        assert runtime.shutdown(wait_msecs=3000)


def test_repeated_queued_replacements_release_budget_for_later_prefetch(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FolderImageSource(_write_folder(tmp_path / "book", pages=2))
    runtime = FolderRasterBookRuntime(source, 1, max_active_jobs=2)
    units = tuple(_unit(source, index) for index in range(2))
    frames: list[RasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        request = _request(1, units[0], *units, direction=1)
        assert runtime.request(request)
        _wait_until(qapp, lambda: bool(frames))
        neighbor_key = runtime._key_for(units[1], request.render_spec)
        hard = runtime.cache_bytes + 2_000_000
        runtime.set_memory_limits(hard_limit_bytes=hard, soft_target_bytes=hard)
        queued: set[_ZipRasterUnitJob] = set()
        monkeypatch.setattr(runtime, "_try_take", lambda job: job in queued)
        for serial in range(3):
            job = _ZipRasterUnitJob(
                serial=10_000 + serial,
                key=neighbor_key,
                request_id=1,
                source=source,
                unit=units[1],
            )
            queued.add(job)
            runtime._secondary_job = job
            runtime._jobs.add(job)
            runtime._inflight_reservations[job] = hard
            assert runtime._take_unstarted_job(job)
            assert runtime.cache_debug_values()["inflight_reservation_bytes"] == 0
        assert runtime._prefetch_admission_decision(neighbor_key).admitted
        assert runtime.release_continuous_warmup(request_id=1)
        _wait_until(qapp, lambda: 1 in runtime.cached_page_indexes)
        assert runtime.cache_debug_values()["inflight_reservation_bytes"] == 0
    finally:
        assert runtime.shutdown(wait_msecs=3000)


def test_limit_expansion_updates_both_folder_job_reservations(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FolderImageSource(_write_folder(tmp_path / "book", pages=3))
    runtime = FolderRasterBookRuntime(source, 1, max_active_jobs=2)
    units = tuple(_unit(source, index) for index in range(3))
    frames: list[RasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        request = _request(1, units[0], *units, direction=1)
        assert runtime.request(request)
        _wait_until(qapp, lambda: bool(frames))
        jobs = []
        initial = runtime.cache_bytes + 1_000
        runtime.set_memory_limits(hard_limit_bytes=initial, soft_target_bytes=initial)
        for index in (1, 2):
            job = _ZipRasterUnitJob(
                serial=20_000 + index,
                key=runtime._key_for(units[index], request.render_spec),
                request_id=1,
                source=source,
                unit=units[index],
                prefetch_budget_bytes=100,
            )
            job.started.set()
            jobs.append(job)
            runtime._jobs.add(job)
            runtime._inflight_reservations[job] = 100
        runtime._active_job, runtime._secondary_job = jobs
        monkeypatch.setattr(runtime, "_drive", lambda: None)
        hard = runtime.cache_bytes + 4_000_000
        runtime.set_memory_limits(hard_limit_bytes=hard, soft_target_bytes=hard)
        assert all(job.prefetch_budget_bytes > 100 for job in jobs), (
            [job.prefetch_budget_bytes for job in jobs],
            [runtime._background_rank(job.key) for job in jobs],
        )
        assert all(
            runtime._inflight_reservations[job] >= job.prefetch_budget_bytes
            for job in jobs
        )
        assert runtime.cache_bytes + sum(runtime._inflight_reservations.values()) <= hard
    finally:
        runtime._active_job = None
        runtime._secondary_job = None
        runtime._jobs.difference_update(jobs if "jobs" in locals() else ())
        runtime._inflight_reservations.clear()
        assert runtime.shutdown(wait_msecs=3000)


def test_finished_folder_worker_keeps_reservation_until_gui_result(
    tmp_path: Path,
) -> None:
    source = FolderImageSource(_write_folder(tmp_path / "book", pages=1))
    runtime = FolderRasterBookRuntime(source, 1, max_active_jobs=2)
    unit = _unit(source, 0)
    job = _ZipRasterUnitJob(
        serial=30_001,
        key=runtime._key_for(unit, RasterRenderSpec((100, 100))),
        request_id=1,
        source=source,
        unit=unit,
    )
    runtime._active_job = job
    runtime._jobs.add(job)
    runtime._inflight_reservations[job] = 12_345
    try:
        job.finished.set()
        assert runtime._release_finished_active_slot()
        assert runtime.active_job_count == 0
        assert runtime.has_unfinished_tasks()
        assert runtime.cache_debug_values()["inflight_reservation_bytes"] == 12_345
    finally:
        runtime._jobs.discard(job)
        runtime._inflight_reservations.pop(job, None)
        assert runtime.shutdown(wait_msecs=3000)


def test_two_folder_jobs_cancel_at_source_epoch_change(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class BlockedSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = {1: Event(), 2: Event()}
            self.release = Event()

        def open_image(self, image_id: str) -> Image.Image:
            index = int(Path(image_id).stem)
            if index in self.started:
                self.started[index].set()
                self.release.wait(3)
            return super().open_image(image_id)

    old_source = BlockedSource(_write_folder(tmp_path / "old", pages=3))
    new_source = FolderImageSource(_write_folder(tmp_path / "new", pages=1))
    old = FolderRasterBookRuntime(old_source, 1, max_active_jobs=2)
    new = FolderRasterBookRuntime(new_source, 2, max_active_jobs=2)
    old_frames: list[RasterFrame] = []
    new_frames: list[RasterFrame] = []
    old.frameReady.connect(old_frames.append)
    new.frameReady.connect(new_frames.append)
    units = tuple(_unit(old_source, index) for index in range(3))
    try:
        assert old.request(_request(1, units[0], *units, direction=1))
        _wait_until(qapp, lambda: bool(old_frames))
        assert old.release_continuous_warmup(request_id=1)
        assert old_source.started[1].wait(1)
        assert old_source.started[2].wait(1)
        old.cancel(clear_artifacts=True)
        assert all(job.cancelled.is_set() for job in old._active_slots())
        assert new.request(replace(_request(1, _unit(new_source, 0)), source_epoch=2))
        old_source.release.set()
        _wait_until(qapp, lambda: bool(new_frames) and not old.has_unfinished_tasks())
        assert [frame.unit.start_index for frame in old_frames] == [0]
        assert [frame.unit.start_index for frame in new_frames] == [0]
        assert old.cached_unit_count == 0
        assert old.cache_debug_values()["inflight_reservation_bytes"] == 0
    finally:
        old_source.release.set()
        _wait_until(
            qapp, lambda: not old.has_unfinished_tasks() and not new.has_unfinished_tasks()
        )
        assert old.shutdown(wait_msecs=3000)
        assert new.shutdown(wait_msecs=3000)


def test_folder_supplemental_capacity_is_global_and_browser_stays_independent(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class BlockedSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = Event()
            self.release = Event()

        def open_image(self, image_id: str) -> Image.Image:
            self.started.set()
            self.release.wait(3)
            return super().open_image(image_id)

    class BrowserTask(QRunnable):
        def __init__(self) -> None:
            super().__init__()
            self.started = Event()
            self.release = Event()

        def run(self) -> None:
            self.started.set()
            self.release.wait(3)

    coordinator = ImageWorkCoordinator(max_workers=2, folder_supplemental_workers=1)
    sources = tuple(
        BlockedSource(_write_folder(tmp_path / f"book{index}", pages=1))
        for index in range(3)
    )
    runtimes = tuple(
        FolderRasterBookRuntime(
            source, 1, image_work_coordinator=coordinator, max_active_jobs=2
        )
        for source in sources
    )
    browser = BrowserTask()
    try:
        for index in (0, 1):
            unit = _unit(sources[index], 0)
            assert runtimes[index].request(_request(1, unit))
            assert sources[index].started.wait(1)
        third_unit = _unit(sources[2], 0)
        assert runtimes[2].request(_request(1, third_unit))
        assert not sources[2].started.is_set()
        assert sum(runtime.active_job_count for runtime in runtimes) == 2

        assert coordinator.start_browser(browser, 300)
        assert browser.started.wait(1)
        assert sum(runtime.active_job_count for runtime in runtimes) == 2
        sources[0].release.set()
        _wait_until(qapp, sources[2].started.is_set)
        assert not sources[1].release.is_set()
        assert sum(runtime.active_job_count for runtime in runtimes) <= 2
    finally:
        browser.release.set()
        for source in sources:
            source.release.set()
        _wait_until(
            qapp, lambda: all(not runtime.has_unfinished_tasks() for runtime in runtimes)
        )
        for runtime in runtimes:
            assert runtime.shutdown(wait_msecs=3000)
        assert coordinator.shutdown(wait_msecs=3000)


def test_folder_supplemental_lane_does_not_run_zip_work(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class BlockedFolder(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = Event()
            self.release = Event()

        def open_image(self, image_id: str) -> Image.Image:
            self.started.set()
            self.release.wait(3)
            return super().open_image(image_id)

    class ObservedZip(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = Event()

        def open_image(self, image_id: str) -> Image.Image:
            self.started.set()
            return super().open_image(image_id)

    coordinator = ImageWorkCoordinator(max_workers=2, folder_supplemental_workers=1)
    first = BlockedFolder(_write_folder(tmp_path / "first", pages=1))
    second = BlockedFolder(_write_folder(tmp_path / "second", pages=1))
    folder_a = FolderRasterBookRuntime(
        first, 1, image_work_coordinator=coordinator, max_active_jobs=2
    )
    folder_b = FolderRasterBookRuntime(
        second, 1, image_work_coordinator=coordinator, max_active_jobs=2
    )
    zip_source = ObservedZip(_write_zip(tmp_path, pages=1))
    archive = ZipRasterBookRuntime(zip_source, 1, image_work_coordinator=coordinator)
    try:
        assert folder_a.request(_request(1, _unit(first, 0)))
        assert first.started.wait(1)
        assert archive.request(_zip_request(1, _zip_unit(0)))
        assert not zip_source.started.is_set()
        assert folder_b.request(_request(1, _unit(second, 0)))
        assert second.started.wait(1)
        assert not zip_source.started.is_set()
        first.release.set()
        _wait_until(qapp, zip_source.started.is_set)
    finally:
        first.release.set()
        second.release.set()
        _wait_until(
            qapp,
            lambda: all(
                not runtime.has_unfinished_tasks()
                for runtime in (folder_a, folder_b, archive)
            ),
        )
        for runtime in (folder_a, folder_b, archive):
            assert runtime.shutdown(wait_msecs=3000)
        assert coordinator.shutdown(wait_msecs=3000)


def test_explicit_folder_two_worker_trial_with_coordinator(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class BlockedSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = {1: Event(), 2: Event()}
            self.release = Event()

        def open_image(self, image_id: str) -> Image.Image:
            index = int(Path(image_id).stem)
            if index in self.started:
                self.started[index].set()
                self.release.wait(3)
            return super().open_image(image_id)

    folder = _write_folder(tmp_path / "book", pages=4)
    source = BlockedSource(folder)
    coordinator = ImageWorkCoordinator(max_workers=2, folder_supplemental_workers=1)
    session = BookSession(
        source_factory=lambda *_args, **_kwargs: (source, None),
        image_work_coordinator=coordinator,
        folder_worker_limit=2,
    )
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(
        config_manager=config,
        book_session=session,
        image_work_coordinator=coordinator,
    )
    window.resize(640, 480)
    window.set_view_mode("single")
    try:
        window.show()
        qapp.processEvents()
        assert window._finish_opened_book(session.open_book(folder), modal_on_empty=False)
        runtime = session.viewer_runtime
        assert runtime is not None and runtime._max_active_jobs == 2
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        _wait_until(qapp, source.started[1].is_set)
        _wait_until(qapp, source.started[2].is_set)
        assert runtime.active_job_count == 2
    finally:
        source.release.set()
        window.close()
        qapp.processEvents()
        assert coordinator.shutdown(wait_msecs=3000)


def test_default_coordinator_runs_only_one_folder_viewer_job_globally(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class BlockingSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = Event()
            self.release = Event()

        def open_image(self, image_id: str) -> Image.Image:
            self.started.set()
            self.release.wait(3)
            return super().open_image(image_id)

    coordinator = ImageWorkCoordinator()
    sources = [
        BlockingSource(_write_folder(tmp_path / f"book{index}", pages=1))
        for index in range(2)
    ]
    runtimes = [
        FolderRasterBookRuntime(source, 1, image_work_coordinator=coordinator)
        for source in sources
    ]
    try:
        assert coordinator.folder_supplemental_workers == 0
        for index, runtime in enumerate(runtimes):
            unit = _unit(sources[index], 0)
            assert runtime.request(_request(1, unit))
            if index == 0:
                assert sources[0].started.wait(1)
        qapp.processEvents()
        assert not sources[1].started.is_set()
        sources[0].release.set()
        _wait_until(qapp, sources[1].started.is_set)
    finally:
        for source in sources:
            source.release.set()
        _wait_until(qapp, lambda: all(not runtime.has_unfinished_tasks() for runtime in runtimes))
        for runtime in runtimes:
            assert runtime.shutdown(wait_msecs=3000)
        assert coordinator.shutdown(wait_msecs=3000)


def test_folder_runtime_builds_startup_runway_bypasses_hits_and_skips_single_prefetch(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class CountingFolderSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.order: list[str] = []
            self.active = 0
            self.max_active = 0
            self.guard = Lock()

        def open_image(self, image_id: str) -> Image.Image:
            with self.guard:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.order.append(Path(image_id).name)
            try:
                sleep(0.01)
                return super().open_image(image_id)
            finally:
                with self.guard:
                    self.active -= 1

    source = CountingFolderSource(_write_folder(tmp_path / "book", pages=7))
    runtime = FolderRasterBookRuntime(source, 1)
    frames: list[RasterFrame] = []
    runtime.frameReady.connect(frames.append)
    current = _unit(source, 1)
    request = _request(
        1,
        current,
        current,
        _unit(source, 2),
        _unit(source, 0),
        _unit(source, 3),
        _unit(source, 4),
        _unit(source, 5),
        _unit(source, 6),
        direction=1,
    )
    try:
        assert runtime.request(request)
        _wait_until(qapp, lambda: len(frames) == 1)
        assert source.order == ["1.png"]
        assert runtime.metrics.jobs_submitted == 1

        assert runtime.release_startup_runway(request_id=1)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert source.order == [
            "1.png",
            "2.png",
            "0.png",
            "3.png",
            "4.png",
            "5.png",
            "6.png",
        ]
        assert runtime.metrics.jobs_submitted == 7
        assert runtime.metrics.startup_runway_releases == 1
        assert source.max_active == 1

        # Paint remains an ownership acknowledgement, not a decode gate.
        jobs_before_paint = runtime.metrics.jobs_submitted
        assert runtime.release_prefetch(request_id=1)
        qapp.processEvents()
        assert runtime.metrics.jobs_submitted == jobs_before_paint

        jobs_before_hit = runtime.metrics.jobs_submitted
        assert runtime.request(
            _request(
                2,
                _unit(source, 2),
                *(_unit(source, index) for index in range(7)),
                direction=1,
            )
        )
        qapp.processEvents()
        assert frames[-1].request_id == 2
        assert frames[-1].cache_hit
        assert runtime.metrics.jobs_submitted == jobs_before_hit
    finally:
        assert runtime.shutdown(wait_msecs=3000)

    single = CountingFolderSource(_write_folder(tmp_path / "single", pages=1))
    single_runtime = FolderRasterBookRuntime(single, 1)
    single_frames: list[RasterFrame] = []
    single_runtime.frameReady.connect(single_frames.append)
    try:
        only = _unit(single, 0)
        assert single_runtime.request(_request(1, only, only))
        _wait_until(qapp, lambda: len(single_frames) == 1)
        assert single_runtime.release_startup_runway(request_id=1)
        assert single_runtime.release_prefetch(request_id=1)
        qapp.processEvents()
        assert single.order == ["0.png"]
        assert single_runtime.metrics.jobs_submitted == 1
    finally:
        assert single_runtime.shutdown(wait_msecs=3000)


def test_folder_runtime_reuses_decoded_source_across_layout_variants(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class CountingFolderSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.decode_calls = 0
            self.full_decodes = 0

        def open_qimage_at_most(self, image_id, maximum_size):
            self.decode_calls += 1
            return super().open_qimage_at_most(image_id, maximum_size)

        def open_compatible_jpeg_at_most(self, image_id, maximum_size):
            self.decode_calls += 1
            return super().open_compatible_jpeg_at_most(image_id, maximum_size)

        def open_image(self, image_id: str) -> Image.Image:
            self.decode_calls += 1
            self.full_decodes += 1
            return super().open_image(image_id)

    folder = tmp_path / "layout"
    folder.mkdir()
    with Image.new("RGB", (1200, 1800), (60, 90, 130)) as image:
        image.save(folder / "0.jpg", "JPEG", quality=88)
    source = CountingFolderSource(folder)
    runtime = FolderRasterBookRuntime(source, 1)
    frames: list[RasterFrame] = []
    runtime.frameReady.connect(frames.append)
    current = _unit(source, 0)
    try:
        specs = (
            RasterRenderSpec((640, 480), decoder_maximum_size=(80, 120)),
            RasterRenderSpec(
                (500, 360),
                device_pixel_ratio=1.5,
                decoder_maximum_size=(60, 90),
                downscale_algorithm="nearest",
                upscale_algorithm="nearest",
            ),
            RasterRenderSpec(
                (900, 650),
                rotation=90,
                decoder_maximum_size=(900, 650),
                decoder_layout_sized=True,
                downscale_algorithm="sharp",
                upscale_algorithm="lanczos",
            ),
            RasterRenderSpec(
                (720, 520),
                device_pixel_ratio=2.0,
                decoder_maximum_size=(100, 150),
                downscale_algorithm="area",
                upscale_algorithm="bilinear",
            ),
        )
        expected_decodes = (1, 1, 2, 2)
        for request_id, (spec, expected) in enumerate(
            zip(specs, expected_decodes),
            start=1,
        ):
            if request_id > 1:
                runtime.invalidate_layout()
            assert runtime.request(
                _request(request_id, current, current, spec=spec)
            )
            _wait_until(
                qapp,
                lambda current_id=request_id: bool(frames)
                and frames[-1].request_id == current_id,
            )
            assert source.decode_calls == expected
        assert runtime.decoded_source_count == 1
        assert runtime.metrics.source_cache_hits == 2
        assert source.full_decodes == 0
    finally:
        assert runtime.shutdown(wait_msecs=3000)


def test_folder_prefetch_budgets_native_preview_and_allows_lazy_one_axis_fit(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class CountingFolderSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.preview_opens: list[str] = []
            self.header_probes: list[str] = []

        def open_compatible_jpeg_at_most(self, image_id, maximum_size):
            self.preview_opens.append(Path(image_id).name)
            return super().open_compatible_jpeg_at_most(image_id, maximum_size)

        def probe_jpeg_size(self, image_id):
            self.header_probes.append(Path(image_id).name)
            return super().probe_jpeg_size(image_id)

    folder = tmp_path / "prefetch-budget"
    folder.mkdir()
    for index in range(2):
        with Image.new("RGB", (1200, 1800), (50 + index * 20, 80, 120)) as image:
            image.save(folder / f"{index}.jpg", "JPEG", quality=88)
    with Image.new("RGB", (200, 6000), (90, 70, 120)) as image:
        image.save(folder / "2.jpg", "JPEG", quality=88)

    source = CountingFolderSource(folder)
    runtime = FolderRasterBookRuntime(source, 1, cache_byte_budget=4_000_000)
    frames: list[RasterFrame] = []
    runtime.frameReady.connect(frames.append)
    image_ids = source.list_images()
    current = RasterDisplayUnit(
        0,
        (RasterPage(0, image_ids[0], (1200, 1800)),),
        True,
    )
    known_neighbor = RasterDisplayUnit(
        1,
        (RasterPage(1, image_ids[1], (1200, 1800)),),
        True,
    )
    bounded = RasterRenderSpec(
        (100, 100),
        decoder_maximum_size=(100, 100),
        decoder_layout_sized=True,
    )
    try:
        first = _request(1, current, current, known_neighbor, spec=bounded)
        assert runtime.request(first)
        _wait_until(qapp, lambda: len(frames) == 1)
        current_bytes = runtime.cache_bytes
        runtime.set_cache_limits(byte_budget=current_bytes + 100_000)
        assert runtime.release_startup_runway(request_id=1)
        qapp.processEvents()

        # Pillow retains a 150x225 native JPEG tier here.  Budget that tier,
        # not the smaller 67x100 final target, and avoid decode-then-evict.
        assert source.preview_opens == ["0.jpg"]
        assert runtime.metrics.prefetch_admission_stops == 1

        runtime.set_cache_limits(byte_budget=256 * 1024 * 1024)
        runtime.invalidate_layout()
        lazy_neighbor = RasterDisplayUnit(
            1,
            (RasterPage(1, image_ids[1], None),),
            True,
        )
        one_axis = RasterRenderSpec(
            (1200, 800),
            fit_mode="fit_width",
            decoder_maximum_size=(1200, None),
            decoder_layout_sized=True,
        )
        second = _request(2, current, current, lazy_neighbor, spec=one_axis)
        assert runtime.request(second)
        _wait_until(qapp, lambda: frames[-1].request_id == 2)
        assert runtime.release_startup_runway(request_id=2)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        # A bounded provisional estimate keeps lazy one-axis neighbors useful;
        # it no longer disables fit-width/fit-height prefetch unconditionally.
        assert source.preview_opens[-1] == "1.jpg"
        assert source.preview_opens.count("1.jpg") == 1

        # An extreme unindexed one-axis neighbor does not inherit a misleading
        # book-local observed size.  The worker probes its real JPEG dimensions
        # and may use strictly lower-rank retained artifacts as a non-mutating
        # allowance.  Only after successful, relevant completion does page 2
        # replace the farther page 1 within the same combined budget.
        runtime.invalidate_layout()
        narrow = RasterRenderSpec(
            (100, 100),
            fit_mode="fit_width",
            decoder_maximum_size=(100, None),
            decoder_layout_sized=True,
        )
        extreme_neighbor = RasterDisplayUnit(
            2,
            (RasterPage(2, image_ids[2], None),),
            True,
        )
        third = _request(3, current, current, extreme_neighbor, spec=narrow)
        assert runtime.request(third)
        _wait_until(qapp, lambda: frames[-1].request_id == 3)
        runtime.set_cache_limits(byte_budget=runtime.cache_bytes + 1_500_000)
        assert runtime.release_startup_runway(request_id=3)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        assert source.header_probes[-1] == "2.jpg"
        assert source.preview_opens.count("2.jpg") == 1
        assert runtime.metrics.prefetch_admission_stops == 1
        assert 2 in runtime.cached_page_indexes
        assert 1 not in runtime.cached_page_indexes

        # Capacity was satisfied by sliding, so enlarging the budget does not
        # decode the same page again or manufacture a retry loop.
        jobs_after_slide = runtime.metrics.jobs_submitted
        runtime.set_cache_limits(byte_budget=256 * 1024 * 1024)
        qapp.processEvents()
        assert source.preview_opens.count("2.jpg") == 1
        assert runtime.metrics.jobs_submitted == jobs_after_slide

        # Making the same page current is now a pure ready hit.
        jobs_before_ready_hit = runtime.metrics.jobs_submitted
        assert runtime.request(
            _request(4, extreme_neighbor, extreme_neighbor, spec=narrow)
        )
        _wait_until(qapp, lambda: frames[-1].request_id == 4)
        assert source.preview_opens.count("2.jpg") == 1
        assert runtime.metrics.jobs_submitted == jobs_before_ready_hit
    finally:
        assert runtime.shutdown(wait_msecs=3000)


def test_folder_one_axis_prefetch_budgets_complete_display_unit(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class CountingFolderSource(FolderImageSource):
        # Keep the GUI-side estimate deliberately provisional.  The worker
        # must replace it with exact header dimensions before pixel decode.
        compatible_jpeg_unknown_area_multiplier = 1

        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.preview_opens: list[str] = []
            self.header_probes: list[str] = []

        def open_compatible_jpeg_at_most(self, image_id, maximum_size):
            self.preview_opens.append(Path(image_id).name)
            return super().open_compatible_jpeg_at_most(image_id, maximum_size)

        def probe_jpeg_size(self, image_id):
            self.header_probes.append(Path(image_id).name)
            return super().probe_jpeg_size(image_id)

    def exercise(
        folder: Path,
        *,
        sizes: tuple[tuple[int, int], ...],
        viewport: tuple[int, int],
        decoder_maximum: tuple[int | None, int | None],
        free_bytes: int,
        resampling_mode: str = "standard",
        expect_admitted: bool,
    ) -> CountingFolderSource:
        folder.mkdir()
        for index, size in enumerate(sizes):
            with Image.new("RGB", size, (60 + index * 20, 80, 120)) as image:
                image.save(folder / f"{index}.jpg", "JPEG", quality=88)
        source = CountingFolderSource(folder)
        runtime = FolderRasterBookRuntime(source, 1)
        frames: list[RasterFrame] = []
        runtime.frameReady.connect(frames.append)
        image_ids = source.list_images()
        current = RasterDisplayUnit(
            0,
            (RasterPage(0, image_ids[0], sizes[0]),),
            True,
        )
        neighbor = RasterDisplayUnit(
            1,
            tuple(
                RasterPage(index, image_ids[index], None)
                for index in range(1, len(image_ids))
            ),
            len(image_ids) == 2,
        )
        spec = RasterRenderSpec(
            viewport,
            fit_mode="fit_width",
            decoder_maximum_size=decoder_maximum,
            resampling_mode=resampling_mode,
        )
        try:
            assert runtime.request(
                _request(1, current, current, neighbor, spec=spec)
            )
            _wait_until(qapp, lambda: len(frames) == 1)
            runtime.set_cache_limits(
                byte_budget=runtime.cache_bytes + free_bytes,
            )
            jobs_before = runtime.metrics.jobs_submitted
            assert runtime.release_startup_runway(request_id=1)
            _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
            assert runtime.metrics.jobs_submitted == jobs_before + 1
            assert runtime.metrics.prefetch_admission_stops == int(
                not expect_admitted
            )
            expected_opens = ["0.jpg"]
            if expect_admitted:
                expected_opens.extend(
                    f"{index}.jpg" for index in range(1, len(image_ids))
                )
            assert source.preview_opens == expected_opens
            assert source.header_probes == [
                f"{index}.jpg" for index in range(1, len(image_ids))
            ]
            return source
        finally:
            assert runtime.shutdown(wait_msecs=3000)

    # Normal rendering now owns the exact 200x2000 physical artifact.  A tiny
    # retained source is deliberately enlarged once by the configured final
    # filter rather than hidden behind a later QPainter stretch, so admission
    # must account for the real frame and reject this small budget.
    exercise(
        tmp_path / "standard-no-upscale",
        sizes=((10, 100), (10, 100)),
        viewport=(200, 200),
        decoder_maximum=(10, None),
        free_bytes=200_000,
        expect_admitted=False,
    )

    # Legacy high-quality compatibility resolves to the same exact-size frame,
    # and therefore remains rejected under the same budget.
    exercise(
        tmp_path / "high-quality-upscale",
        sizes=((200, 200), (10, 100)),
        viewport=(200, 200),
        decoder_maximum=(10, None),
        free_bytes=200_000,
        resampling_mode="high_quality",
        expect_admitted=False,
    )

    # One tall page fits after including the safely reclaimable current source,
    # but the complete two-page unit does not. Admission deliberately budgets
    # free bytes plus lower-rank sources whose independent display-ready frame
    # remains paintable; 1.501 MB free + the current ~1.2 MB preview is still
    # below the complete spread's ~3.0 MB source-and-frame cost.
    exercise(
        tmp_path / "spread-total",
        sizes=((200, 6000), (200, 6000), (200, 6000)),
        viewport=(100, 100),
        decoder_maximum=(100, None),
        free_bytes=1_501_000,
        expect_admitted=False,
    )


def test_folder_runtime_reversal_rejects_running_obsolete_result(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class BlockingFolderSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = Event()
            self.release = Event()
            self.opens: list[str] = []

        def open_image(self, image_id: str) -> Image.Image:
            self.opens.append(Path(image_id).name)
            if Path(image_id).name == "0.png":
                self.started.set()
                assert self.release.wait(3)
            return super().open_image(image_id)

    source = BlockingFolderSource(_write_folder(tmp_path / "reverse", pages=3))
    runtime = FolderRasterBookRuntime(source, 1)
    frames: list[RasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(_request(1, _unit(source, 0), _unit(source, 0)))
        assert source.started.wait(1)
        assert runtime.request(
            _request(2, _unit(source, 2), _unit(source, 2), direction=-1)
        )
        source.release.set()
        _wait_until(qapp, lambda: any(frame.request_id == 2 for frame in frames))
        assert [frame.request_id for frame in frames] == [2]
        assert runtime.metrics.stale_results >= 1
        assert runtime.active_job_count <= 1
    finally:
        source.release.set()
        assert runtime.shutdown(wait_msecs=3000)


def test_folder_runtime_stages_rapid_navigation_and_decodes_only_final_target(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class BlockingFolderSource(FolderImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.started = Event()
            self.release = Event()
            self.opens: list[str] = []

        def open_image(self, image_id: str) -> Image.Image:
            name = Path(image_id).name
            self.opens.append(name)
            if name == "0.png":
                self.started.set()
                assert self.release.wait(3)
            return super().open_image(image_id)

    source = BlockingFolderSource(_write_folder(tmp_path / "rapid", pages=3))
    runtime = FolderRasterBookRuntime(source, 1)
    frames: list[RasterFrame] = []
    runtime.frameReady.connect(frames.append)
    final = _request(3, _unit(source, 2), _unit(source, 2), direction=1)
    try:
        assert runtime.request(_request(1, _unit(source, 0), _unit(source, 0)))
        assert source.started.wait(1)

        assert runtime.stage(
            _request(2, _unit(source, 1), _unit(source, 1), direction=1)
        )
        assert runtime.stage(final)
        assert runtime.metrics.jobs_submitted == 1
        assert source.opens == ["0.png"]

        source.release.set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert frames == []
        assert source.opens == ["0.png"]

        assert runtime.request(final)
        _wait_until(
            qapp,
            lambda: bool(frames) and frames[-1].request_id == 3,
        )
        assert source.opens == ["0.png", "2.png"]
    finally:
        source.release.set()
        assert runtime.shutdown(wait_msecs=3000)


def test_folder_production_uses_one_runtime_and_keeps_page_list_separate(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = _write_folder(tmp_path / "production", pages=2, suffix=".jpg")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)

    def legacy_path_used(*_args, **_kwargs):
        raise AssertionError("folder production returned to the legacy pipeline")

    monkeypatch.setattr(window, "_render_spread", legacy_path_used)
    monkeypatch.setattr(window, "_queue_decode_demand", legacy_path_used)
    try:
        assert window.open_path(folder)
        assert window.book_session.wait_for_async(3000)
        _wait_until(
            qapp,
            lambda: window.presentation_state.displayed_page == 0,
        )
        runtime = window.book_session.viewer_runtime
        assert isinstance(runtime, FolderRasterBookRuntime)
        assert runtime._max_active_jobs == 1
        assert ImageWorkCoordinator().folder_supplemental_workers == 0
        assert window._zip_runtime is runtime
        assert window._zip_runtime_active
        assert window.viewer._direct_display_mode
        assert window.image_cache.cache_bytes == 0
        # PageList ownership is intentionally outside the first-frame
        # critical path.  It is materialized only after that frame paints.
        assert window.book_session.page_list_runtime is None
        paint_boundaries: list[tuple[object, object]] = []
        window.viewer.framePainted.connect(
            lambda *_args: paint_boundaries.append(
                (
                    window.book_session.page_list_runtime,
                    window._pending_book_open_projection,
                )
            )
        )
        window.show()
        _wait_until(
            qapp,
            lambda: window.book_session.page_list_runtime is not None,
        )
        assert paint_boundaries
        assert paint_boundaries[0][0] is None
        assert paint_boundaries[0][1] is not None
        assert window._pending_book_open_projection is None
        assert window.book_session.page_list_runtime is not runtime

        committed_before = window.presentation_state.committed_frame_serial
        window.rotate_right()
        _wait_until(
            qapp,
            lambda: window.presentation_state.committed_frame_serial
            > committed_before,
        )
        assert window.book_session.viewer_runtime is runtime
        assert window.viewer.displayed_source_snapshot(0) is not None
    finally:
        window.close()
        qapp.processEvents()


def test_folder_book_switch_keeps_source_until_worker_callback_drains(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class TrackingFolderSource(FolderImageSource):
        def __init__(self, path: Path, *, blocking: bool = False) -> None:
            super().__init__(path)
            self.blocking = blocking
            self.started = Event()
            self.release = Event()
            self.closed = False

        def open_image(self, image_id: str) -> Image.Image:
            if self.blocking:
                self.started.set()
                assert self.release.wait(3)
            return super().open_image(image_id)

        def close(self) -> None:
            self.closed = True

    first_path = _write_folder(tmp_path / "first", pages=1)
    second_path = _write_folder(tmp_path / "second", pages=1)
    first = TrackingFolderSource(first_path, blocking=True)
    second = TrackingFolderSource(second_path)

    def source_factory(path, **_kwargs):
        return (first if Path(path) == first_path else second), None

    session = BookSession(source_factory=source_factory)
    session.open_book(first_path)
    runtime = session.viewer_runtime
    assert isinstance(runtime, FolderRasterBookRuntime)
    only = _unit(first, 0)
    assert runtime.request(_request(1, only, only))
    assert first.started.wait(1)

    try:
        session.open_book(second_path)
        assert session.source is second
        assert not first.closed

        first.release.set()
        assert runtime.wait_for_done(3000)
        _wait_until(qapp, lambda: first.closed)
        assert not runtime.has_unfinished_tasks()
    finally:
        first.release.set()
        session.shutdown(wait_msecs=3000)
        qapp.processEvents()
