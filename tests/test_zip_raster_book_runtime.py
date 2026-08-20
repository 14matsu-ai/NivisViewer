from __future__ import annotations

from pathlib import Path
from threading import Lock
from time import monotonic, sleep
import zipfile

from PIL import Image
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.image_source import ZipImageSource
from app.image_work_coordinator import ImageWorkCoordinator
from app.zip_raster_book_runtime import (
    ZipRasterBookRuntime,
    ZipRasterDisplayUnit,
    ZipRasterFrame,
    ZipRasterPage,
    ZipRasterRenderSpec,
    ZipRasterRequest,
)


def _write_zip(tmp_path: Path, *, pages: int = 3) -> Path:
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for index in range(pages):
            path = tmp_path / f"{index}.png"
            with Image.new(
                "RGB",
                (120 + index * 10, 180 + index * 10),
                (40 + index * 30, 80, 120),
            ) as image:
                image.save(path)
            output.write(path, path.name)
    return archive


def _unit(*indexes: int) -> ZipRasterDisplayUnit:
    return ZipRasterDisplayUnit(
        indexes[0],
        tuple(ZipRasterPage(index, f"{index}.png") for index in indexes),
        len(indexes) == 1,
    )


def _request(
    request_id: int,
    current: ZipRasterDisplayUnit,
    *work_order: ZipRasterDisplayUnit,
    spec: ZipRasterRenderSpec | None = None,
    direction: int = 0,
) -> ZipRasterRequest:
    return ZipRasterRequest(
        1,
        request_id,
        current,
        work_order or (current,),
        spec or ZipRasterRenderSpec((640, 480)),
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


def test_runtime_uses_one_ordered_job_lane_and_paint_gates_prefetch(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = _write_zip(tmp_path)

    class CountingSource(ZipImageSource):
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
                self.order.append(image_id)
            try:
                sleep(0.01)
                return super().open_image(image_id)
            finally:
                with self.guard:
                    self.active -= 1

    source = CountingSource(archive)
    runtime = ZipRasterBookRuntime(source, 1)
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    request = _request(1, _unit(1), _unit(1), _unit(2), _unit(0))
    try:
        assert runtime.request(request)
        _wait_until(qapp, lambda: len(frames) == 1)

        assert source.order == ["1.png"]
        assert runtime.metrics.jobs_submitted == 1
        assert runtime.release_prefetch(request_id=1)
        _wait_until(
            qapp,
            lambda: runtime.metrics.jobs_submitted == 3
            and not runtime.has_unfinished_tasks(),
        )

        assert source.order == ["1.png", "2.png", "0.png"]
        assert source.max_active == 1
        assert set(runtime.cached_page_indexes) == {0, 1, 2}

        jobs_before_hit = runtime.metrics.jobs_submitted
        assert runtime.request(_request(2, _unit(2), _unit(2), _unit(1)))
        qapp.processEvents()
        assert frames[-1].request_id == 2
        assert frames[-1].cache_hit
        assert runtime.metrics.jobs_submitted == jobs_before_hit
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_completed_frames_outlive_active_frontier_and_make_roundtrip_ready(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = ZipImageSource(_write_zip(tmp_path, pages=6))
    runtime = ZipRasterBookRuntime(source, 1, cache_unit_limit=5)
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        for request_id, page_index in enumerate(range(4), start=1):
            order = [_unit(page_index)]
            if page_index + 1 < 6:
                order.append(_unit(page_index + 1))
            if page_index > 0:
                order.append(_unit(page_index - 1))
            assert runtime.request(
                _request(
                    request_id,
                    order[0],
                    *order,
                    direction=1,
                )
            )
            _wait_until(
                qapp,
                lambda current=request_id: bool(frames)
                and frames[-1].request_id == current,
            )
            assert runtime.release_prefetch(request_id=request_id)
            _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        # Page 0 and 1 are outside the final (3, 4, 2) active frontier, but
        # remain valid completed artifacts because no cache limit is exceeded.
        assert set(runtime.cached_page_indexes) == {0, 1, 2, 3, 4}

        # At capacity, the newly adjacent page replaces the farthest retained
        # page instead of purging every frame outside the three active keys.
        assert runtime.request(
            _request(5, _unit(4), _unit(4), _unit(5), _unit(3), direction=1)
        )
        qapp.processEvents()
        assert frames[-1].request_id == 5
        assert frames[-1].cache_hit
        assert runtime.release_prefetch(request_id=5)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert set(runtime.cached_page_indexes) == {1, 2, 3, 4, 5}
        assert runtime.metrics.cache_evictions == 1

        submitted_before_roundtrip = runtime.metrics.jobs_submitted
        assert runtime.request(
            _request(6, _unit(1), _unit(1), _unit(0), _unit(2), direction=-1)
        )
        qapp.processEvents()

        assert frames[-1].request_id == 6
        assert frames[-1].cache_hit
        assert runtime.metrics.jobs_submitted == submitted_before_roundtrip
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_layout_changes_reuse_book_scoped_decoded_source(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "layout-book.zip"
    page_path = tmp_path / "0.jpg"
    with Image.new("RGB", (120, 180), (60, 90, 130)) as image:
        image.save(page_path, "JPEG", quality=90)
    with zipfile.ZipFile(archive, "w") as output:
        output.write(page_path, page_path.name)

    class CountingSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.decode_calls = 0

        def open_qimage_at_most(self, image_id, maximum_size):
            self.decode_calls += 1
            return super().open_qimage_at_most(image_id, maximum_size)

        def open_image(self, image_id: str) -> Image.Image:
            self.decode_calls += 1
            return super().open_image(image_id)

    source = CountingSource(archive)
    runtime = ZipRasterBookRuntime(source, 1)
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    current = ZipRasterDisplayUnit(
        0,
        (ZipRasterPage(0, "0.jpg"),),
        True,
    )
    try:
        assert runtime.request(
            _request(
                1,
                current,
                current,
                spec=ZipRasterRenderSpec(
                    (640, 480),
                    decoder_maximum_size=(80, 120),
                ),
            )
        )
        _wait_until(qapp, lambda: len(frames) == 1)
        assert source.decode_calls == 1
        assert runtime.decoded_source_count == 1

        # A smaller layout is satisfied by the retained decoder-sized source.
        runtime.invalidate_layout()
        assert runtime.request(
            _request(
                2,
                current,
                current,
                spec=ZipRasterRenderSpec(
                    (500, 360),
                    device_pixel_ratio=1.5,
                    decoder_maximum_size=(60, 90),
                ),
            )
        )
        _wait_until(qapp, lambda: len(frames) == 2)

        assert source.decode_calls == 1
        assert frames[-1].pages[0].source_is_preview

        # Rotation/magnifier-style full-resolution demand upgrades once.
        runtime.invalidate_layout()
        assert runtime.request(
            _request(
                3,
                current,
                current,
                spec=ZipRasterRenderSpec((900, 650), rotation=90),
            )
        )
        _wait_until(qapp, lambda: len(frames) == 3)
        assert source.decode_calls == 2
        assert not frames[-1].pages[0].source_is_preview

        # The full source then satisfies later DPI/layout variants.
        runtime.invalidate_layout()
        assert runtime.request(
            _request(
                4,
                current,
                current,
                spec=ZipRasterRenderSpec(
                    (720, 520),
                    device_pixel_ratio=2.0,
                    decoder_maximum_size=(100, 150),
                ),
            )
        )
        _wait_until(qapp, lambda: len(frames) == 4)

        assert source.decode_calls == 2
        assert runtime.metrics.jobs_submitted == 4
        assert runtime.metrics.source_cache_hits == 2
        assert runtime.metrics.source_cache_misses == 2
        assert runtime.decoded_source_count == 1
        assert frames[-1].request_id == 4
        assert frames[-1].pages[0].source_qimage is not None

        # Active adjustments define the protected source variant. Repeated
        # adjustment changes must not pin every old full-resolution QImage just
        # because they belong to the currently displayed image ID.
        one_variant_budget = runtime.cache_bytes + 1
        runtime.set_cache_limits(byte_budget=one_variant_budget)
        for request_id, brightness in enumerate((1.1, 1.2, 1.3), start=5):
            assert runtime.request(
                _request(
                    request_id,
                    current,
                    current,
                    spec=ZipRasterRenderSpec(
                        (720, 520),
                        device_pixel_ratio=2.0,
                        brightness=brightness,
                    ),
                )
            )
            _wait_until(
                qapp,
                lambda expected=request_id: frames[-1].request_id == expected,
            )
            assert runtime.decoded_source_count == 1
            assert runtime.cache_bytes <= one_variant_budget
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_runtime_completes_spread_rotation_and_adjustment_in_one_frame(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = ZipImageSource(_write_zip(tmp_path, pages=2))
    runtime = ZipRasterBookRuntime(source, 1)
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    spread = _unit(0, 1)
    spec = ZipRasterRenderSpec(
        (700, 500),
        device_pixel_ratio=1.5,
        rotation=90,
        resampling_mode="high_quality",
        brightness=1.1,
        contrast=1.1,
        gamma=1.2,
        gap=8,
    )
    try:
        assert runtime.request(_request(1, spread, spread, spec=spec))
        _wait_until(qapp, lambda: len(frames) == 1)

        frame = frames[0]
        assert frame.unit.identity == spread.identity
        assert len(frame.pages) == 2
        assert all(page.error is None for page in frame.pages)
        assert all(page.pixmap is not None for page in frame.pages)
        assert all(page.pixmap.devicePixelRatio() == 1.5 for page in frame.pages)
        assert runtime.metrics.jobs_submitted == 1
        assert runtime.metrics.queued_callbacks == 1
        assert runtime.metrics.qpixmap_creations == 2
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_runtime_reversal_publishes_only_latest_request(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = ZipImageSource(_write_zip(tmp_path))
    runtime = ZipRasterBookRuntime(source, 1)
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(_request(1, _unit(0), _unit(0), _unit(1)))
        assert runtime.request(_request(2, _unit(2), _unit(2), _unit(1)))
        _wait_until(qapp, lambda: any(frame.request_id == 2 for frame in frames))

        assert [frame.request_id for frame in frames] == [2]
        assert frames[0].unit.identity == ((2, "2.png"),)
        assert runtime.active_job_count <= 1
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_runtime_returns_terminal_error_frame_without_legacy_fallback(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("0.jpg", b"not a jpeg")
    source = ZipImageSource(archive)
    runtime = ZipRasterBookRuntime(source, 1)
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        current = ZipRasterDisplayUnit(
            0,
            (ZipRasterPage(0, "0.jpg"),),
            True,
        )
        assert runtime.request(_request(1, current, current))
        _wait_until(qapp, lambda: len(frames) == 1)

        assert len(frames[0].pages) == 1
        assert frames[0].pages[0].pixmap is None
        assert frames[0].pages[0].error
        assert runtime.metrics.terminal_errors == 1
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_memory_budget_stops_prefetch_before_decode_churn(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class CountingSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.opens: list[str] = []

        def open_image(self, image_id: str) -> Image.Image:
            self.opens.append(image_id)
            return super().open_image(image_id)

    source = CountingSource(_write_zip(tmp_path))
    runtime = ZipRasterBookRuntime(source, 1)
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        request = _request(1, _unit(1), _unit(1), _unit(2), _unit(0))
        assert runtime.request(request)
        _wait_until(qapp, lambda: len(frames) == 1)
        current_bytes = runtime.cache_bytes
        assert runtime.decoded_source_bytes > 0
        assert runtime.cached_unit_count == 1
        runtime.set_cache_limits(byte_budget=current_bytes + 1)
        assert runtime.release_prefetch(request_id=1)
        qapp.processEvents()

        # The QPixmap-only store still has ample apparent room, but the
        # protected decoded source consumes the combined budget.  Do not read
        # and decode a neighbor only to evict it immediately.
        assert runtime.metrics.jobs_submitted == 1
        assert runtime.metrics.prefetch_admission_stops == 1
        assert runtime.cached_page_indexes == (1,)
        assert source.opens == ["1.png"]
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_current_worker_start_rejection_publishes_terminal_frame(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = ZipImageSource(_write_zip(tmp_path, pages=1))
    coordinator = ImageWorkCoordinator()
    coordinator._accepting_requests = False
    runtime = ZipRasterBookRuntime(
        source,
        1,
        image_work_coordinator=coordinator,
    )
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        current = _unit(0)
        assert runtime.request(_request(1, current, current))
        qapp.processEvents()

        assert len(frames) == 1
        assert frames[0].pages[0].pixmap is None
        assert "worker" in (frames[0].pages[0].error or "")
        assert runtime.metrics.terminal_errors == 1
        assert not runtime.has_unfinished_tasks()
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()
