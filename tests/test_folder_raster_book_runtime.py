from __future__ import annotations

from pathlib import Path
from threading import Event, Lock
from time import monotonic, sleep

from PIL import Image
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.image_source import FolderImageSource
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
from app.raster_book_runtime import (
    RasterDisplayUnit,
    RasterFrame,
    RasterPage,
    RasterRenderSpec,
    RasterRequest,
)
from app.viewer_window import ViewerWindow


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


def test_folder_runtime_orders_one_lane_bypasses_ready_hits_and_skips_single_prefetch(
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

    source = CountingFolderSource(_write_folder(tmp_path / "book", pages=3))
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
        direction=1,
    )
    try:
        assert runtime.request(request)
        _wait_until(qapp, lambda: len(frames) == 1)
        assert source.order == ["1.png"]
        assert runtime.metrics.jobs_submitted == 1

        assert runtime.release_prefetch(request_id=1)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert source.order == ["1.png", "2.png", "0.png"]
        assert source.max_active == 1

        jobs_before_hit = runtime.metrics.jobs_submitted
        assert runtime.request(_request(2, _unit(source, 2), _unit(source, 2)))
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
            ),
            RasterRenderSpec(
                (900, 650),
                rotation=90,
                decoder_maximum_size=(900, 650),
                decoder_layout_sized=True,
            ),
            RasterRenderSpec(
                (720, 520),
                device_pixel_ratio=2.0,
                decoder_maximum_size=(100, 150),
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
        assert runtime.release_prefetch(request_id=1)
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
        assert runtime.release_prefetch(request_id=2)
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
        jobs_before_probe = runtime.metrics.jobs_submitted
        assert runtime.release_prefetch(request_id=3)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        assert source.header_probes[-1] == "2.jpg"
        assert source.preview_opens.count("2.jpg") == 1
        assert runtime.metrics.jobs_submitted == jobs_before_probe + 1
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
            assert runtime.release_prefetch(request_id=1)
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

    # Standard rendering clamps the apparent 200x2000 fit-width target to its
    # retained 10x100 source.  The worker must admit that real 10x100 frame.
    exercise(
        tmp_path / "standard-no-upscale",
        sizes=((10, 100), (10, 100)),
        viewport=(200, 200),
        decoder_maximum=(10, None),
        free_bytes=200_000,
        expect_admitted=True,
    )

    # High-quality rendering does create the 200x2000 target, so the same
    # source and budget remain correctly rejected before pixel decode.
    exercise(
        tmp_path / "high-quality-upscale",
        sizes=((200, 200), (10, 100)),
        viewport=(200, 200),
        decoder_maximum=(10, None),
        free_bytes=200_000,
        resampling_mode="high_quality",
        expect_admitted=False,
    )

    # Each tall page alone fit the same 2.401 MB snapshot used by the former
    # page-local check.  Both missing native JPEG tiers plus their complete
    # spread do not, so neither page may begin pixel decode.
    exercise(
        tmp_path / "spread-total",
        sizes=((200, 6000), (200, 6000), (200, 6000)),
        viewport=(100, 100),
        decoder_maximum=(100, None),
        free_bytes=2_401_000,
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
        assert window._zip_runtime is runtime
        assert window._zip_runtime_active
        assert window.viewer._direct_display_mode
        assert window.image_cache.cache_bytes == 0
        assert window.book_session.page_list_runtime is not None
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
