from __future__ import annotations

from pathlib import Path
from threading import Event, Lock
from time import monotonic, sleep
import zipfile
import pytest
from dataclasses import replace

from PIL import Image
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.image_source import ImageSourceError, StreamedJpegDecode, ZipImageSource
from app.image_source import FolderImageSource
from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.image_work_coordinator import ImageWorkCoordinator
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
from app.zip_raster_book_runtime import (
    ZipRasterBookRuntime,
    ZipRasterDisplayUnit,
    ZipRasterFrame,
    ZipRasterFramePage,
    ZipRasterPage,
    ZipRasterRenderSpec,
    ZipRasterRequest,
    _CachedFrame,
    _CachedSource,
    _SourceKey,
    _UnitKey,
    _ZipRasterFrameStore,
    _ZipRasterSourceStore,
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
    return ZipRasterRequest(
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


@pytest.mark.parametrize("folder", [False, True], ids=["zip", "folder"])
@pytest.mark.parametrize("complete_before_release", [False, True])
def test_staged_adopted_completion_publishes_once(
    tmp_path, qapp, folder, complete_before_release,
):
    archive = _write_zip(tmp_path, pages=2)
    base = FolderImageSource if folder else ZipImageSource

    class BlockedSource(base):
        started = Event()
        release = Event()

        def open_image(self, image_id):
            if Path(image_id).name == "1.png":
                self.started.set()
                assert self.release.wait(3)
            return super().open_image(image_id)

    source = BlockedSource(tmp_path if folder else archive)
    runtime = (FolderRasterBookRuntime if folder else ZipRasterBookRuntime)(source, 1)
    frames = []
    runtime.frameReady.connect(frames.append)
    units = tuple(
        ZipRasterDisplayUnit(i, (ZipRasterPage(i, name),), True)
        for i, name in enumerate(source.list_images())
    )
    try:
        assert runtime.request(_request(1, units[0], *units, direction=1))
        _wait_until(qapp, lambda: len(frames) == 1)
        runtime.release_continuous_warmup(request_id=1)
        assert source.started.wait(1)
        final = _request(2, units[1], *units, direction=1)
        assert runtime.stage(final)
        if complete_before_release:
            source.release.set()
            _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
            assert runtime.has_cached_current(final)
            assert [frame.request_id for frame in frames] == [1]
        assert not runtime.release_staged(replace(final, source_epoch=99))
        assert not runtime.release_staged(replace(final, request_id=99))
        assert runtime.release_staged(final)
        source.release.set()
        _wait_until(qapp, lambda: len(frames) == 2)
        assert [frame.request_id for frame in frames] == [1, 2]
        assert not runtime.release_staged(final)
        assert len(frames) == 2

        # A cached transit published during stage must not publish twice.
        ready = _request(3, units[1], *units)
        assert runtime.stage(ready, publish_cached=True)
        assert [frame.request_id for frame in frames] == [1, 2, 3]
        assert runtime.release_staged(ready)
        assert not runtime.release_staged(ready)
        assert [frame.request_id for frame in frames] == [1, 2, 3]
        old = _request(4, units[0], *units)
        newer = _request(5, units[1], *units)
        assert runtime.stage(old)
        assert runtime.stage(newer)
        assert not runtime.release_staged(old)
        assert runtime.release_staged(newer)
        assert [frame.request_id for frame in frames] == [1, 2, 3, 5]
    finally:
        source.release.set()
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_book_wide_artifact_store_ledgers_have_no_count_ceiling(
    qapp: QApplication,
) -> None:
    del qapp  # QPixmap construction only requires the shared application.
    artifact_count = 128
    spec = ZipRasterRenderSpec((640, 480))
    units = tuple(_unit(index) for index in range(artifact_count))

    plan = _request(1, units[0], *units, spec=spec, direction=-1).warmup_plan
    source_store = _ZipRasterSourceStore()
    source_keys: list[_SourceKey] = []
    for index in range(artifact_count):
        width = 24 + index % 11
        height = 32 + index % 13
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(index)
        key = _SourceKey(
            1,
            42,
            f"{index}.png",
            spec.adjustments,
            (width, height),
            True,
        )
        source_keys.append(key)
        source_store.put(
            _CachedSource(key, index, image, (width, height), False)
        )

    def assert_source_ledger() -> None:
        sizes = tuple(
            source.qimage.sizeInBytes()
            for source in source_store._sources.values()
        )
        assert source_store.byte_size == sum(sizes)
        assert source_store.largest_source_bytes == max(sizes, default=0)
        assert sum(
            len(group)
            for group in source_store._sources_by_identity.values()
        ) == source_store.page_count

    assert_source_ledger()
    source_store.set_retention_plan(
        plan,
        units[0],
        spec,
        -1,
    )
    replacement_image = QImage(80, 96, QImage.Format.Format_ARGB32)
    replacement_image.fill(0)
    source_store.put(
        _CachedSource(
            source_keys[0],
            0,
            replacement_image,
            (80, 96),
            False,
        )
    )
    # Count is observational only. Even a book much larger than the old
    # 96-page store limit remains resident until the shared byte policy asks
    # the runtime to reclaim a ranked artifact.
    assert source_store.page_count == artifact_count
    assert source_store.evict_one()
    assert source_store.page_count == artifact_count - 1
    assert_source_ledger()
    source_store.clear()
    assert_source_ledger()

    frame_store = _ZipRasterFrameStore()
    frame_keys: list[_UnitKey] = []
    for index, unit in enumerate(units):
        width = 30 + index % 17
        height = 40 + index % 19
        pixmap = QPixmap(width, height)
        pixmap.fill()
        key = _UnitKey(1, 42, unit.identity, unit.is_single, spec)
        frame_keys.append(key)
        retained, evicted = frame_store.put(
            _CachedFrame(
                key,
                unit,
                (
                    ZipRasterFramePage(
                        index,
                        f"{index}.png",
                        f"{index}.png",
                        (width, height),
                        pixmap,
                        None,
                    ),
                ),
                (None,),
                0.0,
                0.0,
            )
        )
        assert retained
        assert evicted == 0

    def assert_frame_ledger() -> None:
        sizes = tuple(
            frame_store._frame_bytes(frame)
            for frame in frame_store.values()
        )
        assert frame_store.byte_size == sum(sizes)
        assert frame_store.largest_frame_bytes == max(sizes, default=0)

    assert_frame_ledger()
    frame_store.set_retention_plan(plan, frame_keys[0], -1)
    frame_store.set_displayed_key(frame_keys[1])
    taken = frame_store.take(frame_keys[5])
    assert taken is not None
    assert frame_store.put(taken) == (True, 0)

    replacement_pixmap = QPixmap(90, 110)
    replacement_pixmap.fill()
    replacement_page = ZipRasterFramePage(
        0,
        "0.png",
        "0.png",
        (90, 110),
        replacement_pixmap,
        None,
    )
    retained, evicted = frame_store.put(
        _CachedFrame(
            frame_keys[0],
            units[0],
            (replacement_page,),
            (None,),
            0.0,
            0.0,
        )
    )
    assert retained
    assert evicted == 0
    assert frame_store.unit_count == artifact_count
    assert frame_store.evict_one()
    assert frame_store.unit_count == artifact_count - 1
    assert_frame_ledger()
    frame_store.clear()
    assert_frame_ledger()


def test_combined_byte_eviction_prefers_old_layout_and_protects_near_plan(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    del qapp  # QPixmap construction only requires the shared application.
    source = ZipImageSource(_write_zip(tmp_path, pages=5))
    runtime = ZipRasterBookRuntime(
        source,
        1,
        cache_byte_budget=1 << 30,
    )
    units = tuple(
        ZipRasterDisplayUnit(
            index,
            (ZipRasterPage(index, f"{index}.png", (16, 16)),),
            True,
        )
        for index in range(5)
    )
    spec = ZipRasterRenderSpec(
        (16, 16),
        decoder_maximum_size=(16, 16),
    )
    old_layout_spec = ZipRasterRenderSpec(
        (32, 32),
        decoder_maximum_size=(16, 16),
    )
    request = _request(
        1,
        units[2],
        *units,
        spec=spec,
        direction=1,
    )
    current_key = runtime._key_for(units[2], spec)
    runtime._current_request = request
    runtime._current_key = current_key
    runtime._frame_store.set_retention_plan(
        request.warmup_plan,
        current_key,
        1,
    )
    runtime._frame_store.set_displayed_key(current_key)
    runtime._source_store.set_retention_plan(
        request.warmup_plan,
        units[2],
        spec,
        1,
    )
    runtime._source_store.set_displayed_unit(units[2], spec)

    source_keys: dict[int, _SourceKey] = {}
    for index in (2, 3, 1, 0):
        image = QImage(16, 16, QImage.Format.Format_ARGB32)
        image.fill(index)
        key = _SourceKey(
            1,
            runtime._source_identity,
            f"{index}.png",
            spec.adjustments,
            (16, 16),
            False,
        )
        source_keys[index] = key
        runtime._source_store.put(
            _CachedSource(key, index, image, (16, 16), True)
        )

    def put_frame(index: int, render_spec: ZipRasterRenderSpec) -> _UnitKey:
        key = runtime._key_for(units[index], render_spec)
        pixmap = QPixmap(16, 16)
        pixmap.fill()
        retained, evicted = runtime._frame_store.put(
            _CachedFrame(
                key,
                units[index],
                (
                    ZipRasterFramePage(
                        index,
                        f"{index}.png",
                        f"{index}.png",
                        (16, 16),
                        pixmap,
                        None,
                    ),
                ),
                (source_keys.get(index),),
                0.0,
                0.0,
            )
        )
        assert retained
        assert evicted == 0
        return key

    try:
        protected_keys = {
            put_frame(2, spec),
            put_frame(3, spec),
            put_frame(1, spec),
        }
        far_frame_key = put_frame(0, spec)
        old_layout_key = put_frame(4, old_layout_spec)
        old_layout_bytes = runtime._frame_store._frame_bytes(
            runtime._frame_store.get(old_layout_key, touch=False)
        )

        # The stores together exceed the new byte target by exactly one old
        # layout frame. Unified ranking must evict that frame, not blindly
        # discard a reusable decoded source first.
        runtime.set_cache_limits(
            byte_budget=runtime.cache_bytes - old_layout_bytes
        )
        assert old_layout_key not in runtime._frame_store
        assert far_frame_key in runtime._frame_store
        assert runtime._source_store.get(source_keys[0]) is not None

        # Under an impossible byte target, current/next/previous frame and
        # source artifacts form the protected minimum; only farther artifacts
        # are reclaimed.
        runtime.set_cache_limits(byte_budget=1)
        assert all(key in runtime._frame_store for key in protected_keys)
        assert far_frame_key not in runtime._frame_store
        assert all(
            runtime._source_store.get(source_keys[index]) is not None
            for index in (2, 3, 1)
        )
        assert runtime._source_store.get(source_keys[0]) is None
        assert runtime.cache_bytes > runtime.cache_byte_budget
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_runtime_builds_startup_runway_then_continues_book_warmup(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = _write_zip(tmp_path, pages=8)

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
    units = tuple(_unit(index) for index in range(8))
    request = _request(1, units[2], *units, direction=1)
    try:
        assert runtime.request(request)
        _wait_until(qapp, lambda: len(frames) == 1)
        planner = runtime._warmup_planner
        assert planner is not None

        assert source.order == ["2.png"]
        assert runtime.metrics.jobs_submitted == 1
        assert runtime.release_startup_runway(request_id=1)
        # A duplicate GUI callback for the same accepted commit is idempotent;
        # it must not restart the runway or duplicate any decode.
        assert runtime.release_startup_runway(request_id=1)
        _wait_until(
            qapp,
            lambda: runtime.metrics.jobs_submitted == 8
            and not runtime.has_unfinished_tasks(),
        )
        assert source.order == [
            "2.png",
            "3.png",
            "1.png",
            "4.png",
            "5.png",
            "6.png",
            "0.png",
            "7.png",
        ]
        assert runtime.warmup_stop_reason == "complete"
        assert runtime.metrics.startup_runway_releases == 1
        assert runtime.metrics.continuous_warmup_releases == 1
        assert runtime.metrics.warmup_planner_creations == 1
        assert source.max_active == 1
        assert set(runtime.cached_page_indexes) == set(range(8))

        # Paint is still a valid ownership/reclamation acknowledgement, but it
        # is no longer a scheduler gate for the runway or book-wide iterator.
        jobs_before_paint = runtime.metrics.jobs_submitted
        assert runtime.release_prefetch(request_id=1)
        qapp.processEvents()
        assert runtime.metrics.jobs_submitted == jobs_before_paint

        jobs_before_hit = runtime.metrics.jobs_submitted
        assert runtime.request(_request(2, units[3], *units, direction=1))
        qapp.processEvents()
        assert frames[-1].request_id == 2
        assert frames[-1].cache_hit
        assert runtime.metrics.jobs_submitted == jobs_before_hit
        assert runtime._warmup_planner is planner
        assert runtime.metrics.warmup_planner_creations == 1
        assert runtime.metrics.warmup_planner_recenters == 1
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_paint_ack_reclaims_old_frame_from_combined_soft_overflow(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = ZipImageSource(_write_zip(tmp_path, pages=2))
    runtime = ZipRasterBookRuntime(
        source,
        1,
        cache_byte_budget=64 * 1024 * 1024,
    )
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    first = _request(1, _unit(0), _unit(0))
    second = _request(2, _unit(1), _unit(1))
    try:
        assert runtime.request(first)
        _wait_until(qapp, lambda: len(frames) == 1)
        assert runtime.release_prefetch(request_id=1)

        # Leave enough room for page 1 by itself, but not for both complete
        # frames.  The old painted frame is intentionally protected while the
        # cold replacement is decoded and atomically committed.
        budget = runtime.cache_bytes + 40_000
        runtime.set_cache_limits(byte_budget=budget)
        assert runtime.request(second)
        _wait_until(qapp, lambda: frames[-1].request_id == 2)
        assert runtime.has_cached_current(second)
        assert set(runtime.cached_page_indexes) == {0, 1}
        assert runtime.cache_bytes > budget

        # Paint acknowledgement transfers displayed ownership to page 1.  It
        # must also enforce the combined source+frame budget immediately; an
        # end page or disabled-prefetch work order has no later success to do
        # that cleanup on its behalf.
        assert runtime.release_prefetch(request_id=2)
        assert runtime.has_cached_current(second)
        assert runtime.cache_bytes <= budget
        assert runtime.cached_page_indexes == (1,)
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_painted_book_warmup_resumes_on_budget_increase_and_fills_broad_order(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = ZipImageSource(_write_zip(tmp_path, pages=8))
    runtime = ZipRasterBookRuntime(
        source,
        1,
    )
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    units = tuple(
        ZipRasterDisplayUnit(
            index,
            (
                ZipRasterPage(
                    index,
                    f"{index}.png",
                    (120 + index * 10, 180 + index * 10),
                ),
            ),
            True,
        )
        for index in range(8)
    )
    request = _request(1, units[0], *units, direction=1)
    try:
        assert runtime.request(request)
        _wait_until(qapp, lambda: len(frames) == 1)

        runtime.set_cache_limits(byte_budget=runtime.cache_bytes + 1)
        assert runtime.release_startup_runway(request_id=1)
        assert runtime.release_prefetch(request_id=1)
        qapp.processEvents()
        assert runtime.cached_page_indexes == (0,)
        assert runtime.metrics.prefetch_admission_stops == 1

        # Increasing the authoritative byte budget resumes the already
        # paint-released frontier immediately.  No timer, new request, or
        # second paint acknowledgement is needed, and the broad unit limit no
        # longer constrains warm-up to current/next/previous.
        runtime.set_cache_limits(byte_budget=64 * 1024 * 1024)
        assert runtime.cache_byte_budget == 64 * 1024 * 1024
        _wait_until(
            qapp,
            lambda: set(runtime.cached_page_indexes) == set(range(8))
            and not runtime.has_unfinished_tasks(),
        )

        assert runtime.metrics.jobs_submitted == 8
        assert runtime.cached_unit_count == 8
        assert runtime.cache_bytes <= 64 * 1024 * 1024
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_soft_target_keeps_near_minimum_then_expansion_resumes_book(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = ZipImageSource(_write_zip(tmp_path, pages=8))
    runtime = ZipRasterBookRuntime(
        source,
        1,
        cache_byte_budget=64 * 1024 * 1024,
        cache_soft_target_bytes=1,
    )
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    units = tuple(_unit(index) for index in range(8))
    request = _request(1, units[3], *units, direction=1)
    try:
        assert runtime.request(request)
        _wait_until(qapp, lambda: len(frames) == 1)
        assert runtime.release_startup_runway(request_id=1)
        assert runtime.release_prefetch(request_id=1)
        _wait_until(
            qapp,
            lambda: not runtime.has_unfinished_tasks()
            and runtime.warmup_stop_reason == "complete_with_skips",
        )

        # The soft target is intentionally below one image. Current plus the
        # four-forward/one-reverse priority band may populate up to the hard
        # byte budget; it is scheduling urgency, never a page retention cap.
        assert set(runtime.cached_page_indexes) == {2, 3, 4, 5, 6, 7}
        assert runtime.cache_bytes > runtime.cache_soft_target_bytes
        assert runtime.cache_debug_values()["capacity_skip_count"] == 2

        runtime.set_memory_limits(
            hard_limit_bytes=64 * 1024 * 1024,
            soft_target_bytes=64 * 1024 * 1024,
        )
        _wait_until(
            qapp,
            lambda: set(runtime.cached_page_indexes) == set(range(8))
            and not runtime.has_unfinished_tasks(),
        )
        assert runtime.warmup_stop_reason == "complete"
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_budget_shrink_cancels_running_background_snapshot(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = _write_zip(tmp_path, pages=3)

    class BlockingSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.background_started = Event()
            self.release_background = Event()

        def open_image(self, image_id: str) -> Image.Image:
            if image_id == "1.png":
                self.background_started.set()
                self.release_background.wait(2.0)
            return super().open_image(image_id)

    source = BlockingSource(archive)
    runtime = ZipRasterBookRuntime(
        source,
        1,
        cache_byte_budget=64 * 1024 * 1024,
    )
    frames: list[ZipRasterFrame] = []
    artifacts: list[int] = []
    runtime.frameReady.connect(frames.append)
    runtime.artifactReady.connect(
        lambda frame: artifacts.append(frame.unit.start_index)
    )
    request = _request(
        1,
        _unit(0),
        _unit(0),
        _unit(1),
        _unit(2),
        direction=1,
    )
    try:
        assert runtime.request(request)
        _wait_until(qapp, lambda: len(frames) == 1)
        reduced_budget = runtime.cache_bytes
        assert runtime.release_startup_runway(request_id=1)
        assert runtime.release_prefetch(request_id=1)
        _wait_until(qapp, source.background_started.is_set)

        runtime.set_cache_limits(byte_budget=reduced_budget)
        assert runtime.cache_byte_budget == reduced_budget
        assert runtime.metrics.cancel_requests >= 1
        source.release_background.set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        # The background job's old 64-MiB admission snapshot is obsolete.  Its
        # completion drains without publishing or retrying under the reduced
        # budget, while the already painted current artifact stays atomic.
        assert artifacts == [0]
        assert len(frames) == 1
        # The cancelled job plus bounded header-only capacity probes may
        # drain after the shrink, but no additional display artifact is
        # decoded/uploaded under the obsolete admission snapshot.
        assert runtime.metrics.jobs_submitted >= 2
        assert runtime.metrics.qpixmap_creations == 1
        assert runtime.cache_bytes <= reduced_budget
        jobs_after_drain = runtime.metrics.jobs_submitted
        QTest.qWait(25)
        qapp.processEvents()
        assert runtime.metrics.jobs_submitted == jobs_after_drain
        assert not runtime.has_unfinished_tasks()
    finally:
        source.release_background.set()
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_soft_target_recovery_keeps_unrelated_background_decode(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = _write_zip(tmp_path, pages=4)

    class BlockingSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.far_started = Event()
            self.release_far = Event()

        def open_image(self, image_id: str) -> Image.Image:
            if image_id == "2.png":
                self.far_started.set()
                self.release_far.wait(2.0)
            return super().open_image(image_id)

    source = BlockingSource(archive)
    runtime = ZipRasterBookRuntime(
        source,
        1,
        cache_byte_budget=64 * 1024 * 1024,
        cache_soft_target_bytes=32 * 1024 * 1024,
    )
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    units = tuple(_unit(index) for index in range(4))
    request = _request(1, units[0], *units, direction=1)
    try:
        assert runtime.request(request)
        _wait_until(qapp, lambda: len(frames) == 1)
        assert runtime.release_startup_runway(request_id=1)
        assert runtime.release_prefetch(request_id=1)
        _wait_until(qapp, source.far_started.is_set)

        cancels_before = runtime.metrics.cancel_requests
        runtime.set_memory_limits(
            hard_limit_bytes=64 * 1024 * 1024,
            soft_target_bytes=48 * 1024 * 1024,
        )
        assert runtime.metrics.cancel_requests == cancels_before

        source.release_far.set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert set(runtime.cached_page_indexes) == set(range(4))
        assert runtime.metrics.jobs_submitted == 4
    finally:
        source.release_far.set()
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_full_cache_navigation_reclaims_sources_before_ready_frames(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class BlockingSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.background_started = Event()
            self.release_background = Event()
            self.block_page_three_once = True

        def open_image(self, image_id: str) -> Image.Image:
            if image_id == "3.png" and self.block_page_three_once:
                self.block_page_three_once = False
                self.background_started.set()
                self.release_background.wait(2.0)
            return super().open_image(image_id)

    source = BlockingSource(_write_zip(tmp_path, pages=5))
    runtime = ZipRasterBookRuntime(
        source,
        1,
        cache_byte_budget=64 * 1024 * 1024,
    )
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    spec = ZipRasterRenderSpec((640, 480))
    units = tuple(
        ZipRasterDisplayUnit(
            index,
            (
                ZipRasterPage(
                    index,
                    f"{index}.png",
                    (120 + index * 10, 180 + index * 10),
                ),
            ),
            True,
        )
        for index in range(5)
    )
    initial = _request(1, units[0], *units, spec=spec, direction=1)
    try:
        assert runtime.request(initial)
        _wait_until(qapp, lambda: len(frames) == 1)
        # Fit the current plus its first two progressively larger neighbors,
        # but not a fourth page. The deprecated unit limit of one must not
        # truncate this byte-driven warm-up.
        runtime.set_cache_limits(byte_budget=runtime.cache_bytes * 3)
        assert runtime.release_startup_runway(request_id=1)
        assert runtime.release_prefetch(request_id=1)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert runtime.cached_unit_count == 3
        assert set(runtime.cached_page_indexes) == {0, 1, 2}

        # Freeze the combined store at its exact full size, then move the
        # ready current to page 2. Page 3 outranks far page 0 in the new order.
        # A small byte-only expansion makes that replacement affordable; the
        # deprecated unit limit remains irrelevant.
        full_budget = runtime.cache_bytes
        runtime.set_cache_limits(byte_budget=full_budget)
        evictions_before = runtime.metrics.cache_evictions
        jobs_before = runtime.metrics.jobs_submitted
        shifted = _request(
            2,
            units[2],
            units[2],
            units[3],
            units[1],
            units[4],
            units[0],
            spec=spec,
            direction=1,
        )
        assert runtime.request(shifted)
        assert frames[-1].request_id == 2
        assert frames[-1].cache_hit
        assert runtime.release_prefetch(request_id=2)
        shifted_budget = (
            full_budget + runtime._frame_store.largest_frame_bytes
        )
        runtime.set_cache_limits(byte_budget=shifted_budget)
        _wait_until(qapp, source.background_started.is_set)

        # Reversal cancels the decoded-but-uncommitted candidate.  Admission
        # was only a reservation, so no far source/frame has been reclaimed.
        rollback = _request(3, units[0], *units, spec=spec, direction=-1)
        assert runtime.request(rollback)
        assert frames[-1].request_id == 3
        source.release_background.set()
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert set(runtime.cached_page_indexes) == {0, 1, 2}
        assert runtime.cache_bytes == full_budget

        shifted = _request(
            4,
            units[2],
            units[2],
            units[3],
            units[1],
            units[4],
            units[0],
            spec=spec,
            direction=1,
        )
        assert runtime.request(shifted)
        assert runtime.release_prefetch(request_id=4)
        _wait_until(
            qapp,
            lambda: 3 in runtime.cached_page_indexes
            and not runtime.has_unfinished_tasks(),
        )

        assert 0 not in runtime.cached_page_indexes
        assert 2 in runtime.cached_page_indexes
        assert runtime._source_store.find(
            units[2].pages[0],
            spec,
            unit=units[2],
        ) is None
        # The current-relative frame runway has higher navigation value than
        # a rehydratable decoded source. Page 2 remains an immediate QPixmap
        # hit even though a later magnifier/source request would hydrate it.
        assert runtime._frame_store.get(
            runtime._key_for(units[2], spec),
            touch=False,
        ) is not None
        assert runtime.cache_bytes <= shifted_budget
        assert runtime.metrics.jobs_submitted == jobs_before + 2
        assert runtime.metrics.cache_evictions > evictions_before
    finally:
        source.release_background.set()
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_direction_reversal_retains_queued_compatible_artifact_and_reorders(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = _write_zip(tmp_path, pages=4)

    class BlockingSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.background_started = Event()
            self.release_background = Event()

        def open_image(self, image_id: str) -> Image.Image:
            if image_id == "2.png":
                self.background_started.set()
                self.release_background.wait(2.0)
            return super().open_image(image_id)

    source = BlockingSource(archive)
    runtime = ZipRasterBookRuntime(
        source,
        1,
    )
    frames: list[ZipRasterFrame] = []
    artifacts: list[int] = []
    runtime.frameReady.connect(frames.append)
    runtime.artifactReady.connect(
        lambda frame: artifacts.append(frame.unit.start_index)
    )
    try:
        first = _request(
            1,
            _unit(1),
            _unit(0),
            _unit(1),
            _unit(2),
            _unit(3),
            direction=1,
        )
        assert runtime.request(first)
        _wait_until(qapp, lambda: len(frames) == 1)
        assert runtime.release_startup_runway(request_id=1)
        assert source.background_started.wait(1.0)
        source.release_background.set()
        # Finish the old decode without draining its queued GUI result.  This
        # is the narrow full-book race: page 1 remains in the replacement
        # order, but its immutable result still carries request 1.
        assert runtime.wait_for_done(3000)

        # Reverse around the same ready current. The finished page-2 result is
        # still a useful complete artifact in the new priority band, even
        # though its immutable callback carries request 1. Retain it without
        # publishing stale presentation state, then continue with page 0 from
        # the recentered unstarted order.
        second = _request(
            2,
            _unit(1),
            _unit(0),
            _unit(3),
            _unit(2),
            _unit(1),
            direction=-1,
        )
        assert runtime.request(second)
        assert runtime.release_startup_runway(request_id=2)
        assert runtime.metrics.cancel_requests == 0
        _wait_until(qapp, lambda: 0 in artifacts)

        assert artifacts[:3] == [1, 2, 0]
        assert runtime.metrics.compatible_old_results == 1
        assert runtime.metrics.stale_results == 0
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
    finally:
        source.release_background.set()
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_rapid_navigation_preempts_unrelated_started_warmup_for_cold_target(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = _write_zip(tmp_path, pages=12)

    class BlockingSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.page_one_started = Event()
            self.release_page_one = Event()
            self.decode_order: list[str] = []

        def open_image(self, image_id: str) -> Image.Image:
            self.decode_order.append(image_id)
            if image_id == "1.png":
                self.page_one_started.set()
                self.release_page_one.wait(2.0)
            return super().open_image(image_id)

    source = BlockingSource(archive)
    runtime = ZipRasterBookRuntime(source, 1)
    units = tuple(_unit(index) for index in range(12))
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    final_request: ZipRasterRequest | None = None
    try:
        initial = _request(1, units[0], *units, direction=1)
        assert runtime.request(initial)
        _wait_until(qapp, lambda: bool(frames))
        assert runtime.release_continuous_warmup(request_id=1)
        assert source.page_one_started.wait(1.0)
        started_job = runtime._active_job
        assert started_job is not None
        assert started_job.started.is_set()

        # Model the replaceable portion of a rapid wheel sequence. The first
        # cold target that differs from the speculative page cooperatively
        # cancels that page; subsequent inputs keep replacing only the pending
        # current target while the occupied worker unwinds.
        for request_id, page_index in enumerate(range(2, 11), start=2):
            final_request = _request(
                request_id,
                units[page_index],
                *units,
                direction=1,
            )
            assert runtime.stage(final_request)
            assert runtime._active_job is started_job
            assert started_job.cancelled.is_set()

        assert final_request is not None
        assert runtime.metrics.cancel_requests == 1
        assert runtime.metrics.running_job_adoptions == 0
        assert runtime.metrics.warmup_planner_creations == 1
        assert runtime.metrics.work_order_changes == 9

        source.release_page_one.set()
        # Finish the cancelled native worker without draining its queued GUI
        # result. The final request must release that finished scheduling slot
        # immediately and start the final cold target.
        assert runtime.wait_for_done(3000)
        assert started_job.finished.is_set()
        assert 1 not in runtime.cached_page_indexes
        assert [frame.request_id for frame in frames] == [1]

        jobs_before_final = runtime.metrics.jobs_submitted
        assert runtime.request(final_request)
        final_job = runtime._active_job
        assert final_job is not None and final_job.key.unit_identity == (
            final_request.current.identity
        )
        assert runtime.metrics.jobs_submitted == jobs_before_final + 1
        assert runtime.metrics.finished_job_slot_releases == 1
        _wait_until(
            qapp,
            lambda: frames[-1].request_id == final_request.request_id,
        )
        assert frames[-1].unit.identity == final_request.current.identity
        assert 1 not in runtime.cached_page_indexes
        assert source.decode_order[:3] == ["0.png", "1.png", "10.png"]
        assert runtime.metrics.cancel_requests == 1
        assert runtime.metrics.stale_results == 0
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
    finally:
        source.release_page_one.set()
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_navigation_adopts_started_warmup_when_it_is_the_cold_target(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = _write_zip(tmp_path, pages=4)

    class BlockingSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.page_one_started = Event()
            self.release_page_one = Event()

        def open_image(self, image_id: str) -> Image.Image:
            if image_id == "1.png":
                self.page_one_started.set()
                self.release_page_one.wait(2.0)
            return super().open_image(image_id)

    source = BlockingSource(archive)
    runtime = ZipRasterBookRuntime(source, 1)
    units = tuple(_unit(index) for index in range(4))
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(_request(1, units[0], *units, direction=1))
        _wait_until(qapp, lambda: bool(frames))
        assert runtime.release_continuous_warmup(request_id=1)
        assert source.page_one_started.wait(1.0)
        started_job = runtime._active_job
        assert started_job is not None and started_job.started.is_set()

        page_one = _request(2, units[1], *units, direction=1)
        assert runtime.stage(page_one)
        assert runtime._active_job is started_job
        assert not started_job.cancelled.is_set()
        assert runtime.metrics.cancel_requests == 0

        assert runtime.request(page_one)
        source.release_page_one.set()
        _wait_until(qapp, lambda: frames[-1].request_id == 2)
        assert frames[-1].unit.start_index == 1
        assert runtime.metrics.cancel_requests == 0
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
    finally:
        source.release_page_one.set()
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_completed_frames_outlive_active_frontier_and_make_roundtrip_ready(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = ZipImageSource(_write_zip(tmp_path, pages=6))
    runtime = ZipRasterBookRuntime(source, 1)
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
            assert runtime.release_startup_runway(request_id=request_id)
            assert runtime.release_prefetch(request_id=request_id)
            _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        # Page 0 and 1 are outside the final (3, 4, 2) active frontier, but
        # remain valid completed artifacts because no cache limit is exceeded.
        assert set(runtime.cached_page_indexes) == {0, 1, 2, 3, 4}

        # Give the byte store enough headroom for most, but not all, of page 5.
        # The newly adjacent frame then replaces the lowest-value far frame;
        # no page-count ceiling participates in the decision.
        capacity_budget = (
            runtime.cache_bytes
            + runtime._frame_store.largest_frame_bytes // 2
        )
        runtime.set_cache_limits(byte_budget=capacity_budget)
        assert runtime.request(
            _request(5, _unit(4), _unit(4), _unit(5), _unit(3), direction=1)
        )
        qapp.processEvents()
        assert frames[-1].request_id == 5
        assert frames[-1].cache_hit
        assert runtime.release_prefetch(request_id=5)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert set(runtime.cached_page_indexes) == {1, 2, 3, 4, 5}
        assert runtime.metrics.cache_evictions >= 1
        assert runtime.cache_bytes <= capacity_budget

        submitted_before_roundtrip = runtime.metrics.jobs_submitted
        assert runtime.request(
            _request(6, _unit(1), _unit(1), _unit(0), _unit(2), direction=-1)
        )
        qapp.processEvents()

        assert frames[-1].request_id == 6
        assert frames[-1].cache_hit
        # The current cache-hit commit is synchronous and creates no current
        # job. The already-released planner may immediately replenish missing
        # page 0 as background ready-ahead after that commit returns.
        assert runtime.metrics.jobs_submitted == submitted_before_roundtrip + 1
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        assert 0 in runtime.cached_page_indexes
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

        def open_compatible_jpeg_at_most(self, image_id, maximum_size):
            self.decode_calls += 1
            return super().open_compatible_jpeg_at_most(image_id, maximum_size)

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
        assert frames[-1].pages[0].pixmap is not None
        assert frames[-1].pages[0].pixmap.size().toTuple() == (320, 480)

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
        assert frames[-1].pages[0].pixmap is not None
        assert frames[-1].pages[0].pixmap.size().toTuple() == (360, 540)
        assert frames[-1].pages[0].pixmap.devicePixelRatio() == 1.5
        # 80x120 from a 120x180 JPEG requires the native 1/1 tier; retaining
        # the full source avoids an arbitrary decoder resize and still lets the
        # smaller layout reuse the same source without another decode.
        assert not frames[-1].pages[0].source_is_preview

        # An explicit full-resolution demand reuses that already-sufficient
        # native 1/1 source instead of decoding the same JPEG again.
        assert runtime.request(
            _request(
                3,
                current,
                current,
                spec=ZipRasterRenderSpec((900, 650), rotation=90),
            )
        )
        _wait_until(qapp, lambda: len(frames) == 3)
        assert source.decode_calls == 1
        assert not frames[-1].pages[0].source_is_preview
        assert runtime.decoded_source_count == 1

        jobs_before_ready_return = runtime.metrics.jobs_submitted
        assert runtime.request(
            _request(
                4,
                current,
                current,
                spec=ZipRasterRenderSpec(
                    (500, 360),
                    device_pixel_ratio=1.5,
                    decoder_maximum_size=(60, 90),
                ),
            )
        )
        qapp.processEvents()
        assert frames[-1].request_id == 4
        assert frames[-1].cache_hit
        assert frames[-1].pages[0].source_qimage is not None
        assert not frames[-1].pages[0].source_is_preview
        assert runtime.metrics.jobs_submitted == jobs_before_ready_return

        # The same full source also satisfies a later larger layout.
        runtime.invalidate_layout()
        assert runtime.request(
            _request(
                5,
                current,
                current,
                spec=ZipRasterRenderSpec(
                    (720, 520),
                    device_pixel_ratio=2.0,
                    decoder_maximum_size=(100, 150),
                ),
            )
        )
        _wait_until(qapp, lambda: len(frames) == 5)

        assert source.decode_calls == 1
        assert runtime.metrics.jobs_submitted == 4
        assert runtime.metrics.source_cache_hits == 3
        assert runtime.metrics.source_cache_misses == 1
        assert runtime.decoded_source_count == 1
        assert frames[-1].request_id == 5
        assert frames[-1].pages[0].source_qimage is not None

        # Active adjustments define the protected source variant. Repeated
        # adjustment changes must not pin every old full-resolution QImage just
        # because they belong to the currently displayed image ID.
        one_variant_budget = runtime.cache_bytes + 1
        runtime.set_cache_limits(byte_budget=one_variant_budget)
        for request_id, brightness in enumerate((1.1, 1.2, 1.3), start=6):
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
            assert runtime.release_prefetch(request_id=request_id)
            # Count is not a policy boundary: the new current variant may
            # coexist with one reusable preview while frames/older variants
            # are reclaimed to the byte budget.
            assert runtime.decoded_source_count <= 2
            assert runtime.cache_bytes <= one_variant_budget
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_runtime_completes_spread_rotation_and_adjustment_in_one_frame(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "large-preview-spread.zip"
    page_paths: list[Path] = []
    for index in range(2):
        page_path = tmp_path / f"preview-{index}.jpg"
        with Image.new("RGB", (1600, 2400), (40 + index * 30, 80, 120)) as image:
            image.save(page_path, "JPEG", quality=88)
        page_paths.append(page_path)
    with zipfile.ZipFile(archive, "w") as output:
        for page_path in page_paths:
            output.write(page_path, page_path.name)

    class CountingSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.preview_sizes: list[tuple[int, int]] = []
            self.full_decodes = 0

        def open_compatible_jpeg_at_most(self, image_id, maximum_size):
            decoded = super().open_compatible_jpeg_at_most(image_id, maximum_size)
            if decoded is not None:
                self.preview_sizes.append(decoded.qimage.size().toTuple())
            return decoded

        def open_image(self, image_id: str) -> Image.Image:
            self.full_decodes += 1
            return super().open_image(image_id)

    source = CountingSource(archive)
    runtime = ZipRasterBookRuntime(source, 1)
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    spread = ZipRasterDisplayUnit(
        0,
        tuple(
            ZipRasterPage(index, path.name)
            for index, path in enumerate(page_paths)
        ),
        False,
    )
    spec = ZipRasterRenderSpec(
        (700, 500),
        device_pixel_ratio=1.5,
        rotation=90,
        resampling_mode="high_quality",
        brightness=1.1,
        contrast=1.1,
        gamma=1.2,
        gap=8,
        decoder_maximum_size=(1050, 750),
        decoder_headroom=2.0,
        decoder_layout_sized=True,
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
        assert source.full_decodes == 0
        assert len(source.preview_sizes) == 2
        assert all(width < 1600 and height < 2400 for width, height in source.preview_sizes)
        assert all(page.source_is_preview for page in frame.pages)
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_runtime_rejects_fresh_jpeg_preview_below_required_size(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "undersized-preview.zip"
    page_path = tmp_path / "0.jpg"
    with Image.new("RGB", (160, 240), (50, 80, 120)) as image:
        image.save(page_path, "JPEG", quality=90)
    with zipfile.ZipFile(archive, "w") as output:
        output.write(page_path, page_path.name)

    class UndersizedPreviewSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.full_decodes = 0

        def open_compatible_jpeg_at_most(self, image_id, maximum_size):
            del image_id, maximum_size
            preview = QImage(79, 120, QImage.Format.Format_RGB32)
            preview.fill(0)
            return StreamedJpegDecode(
                qimage=preview,
                original_size=(160, 240),
                bytes_read=1,
                read_calls=1,
                backend="fake-undersized",
            )

        def open_image(self, image_id: str) -> Image.Image:
            self.full_decodes += 1
            return super().open_image(image_id)

    source = UndersizedPreviewSource(archive)
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
                    (80, 120),
                    decoder_maximum_size=(80, 120),
                ),
            )
        )
        _wait_until(qapp, lambda: len(frames) == 1)

        assert source.full_decodes == 1
        assert frames[0].pages[0].error is None
        assert not frames[0].pages[0].source_is_preview
        assert frames[0].pages[0].source_qimage is not None
        assert frames[0].pages[0].source_qimage.size().toTuple() == (160, 240)
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


def test_memory_budget_prioritizes_a_display_ready_runway_over_sources(
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

    archive = tmp_path / "display-runway.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for index in range(6):
            path = tmp_path / f"{index}.png"
            with Image.new("RGB", (120, 180), (40 + index * 20, 80, 120)) as image:
                image.save(path)
            output.write(path, path.name)
    source = CountingSource(archive)
    runtime = ZipRasterBookRuntime(source, 1)
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        units = tuple(
            ZipRasterDisplayUnit(
                index,
                (ZipRasterPage(index, f"{index}.png", (120, 180)),),
                True,
            )
            for index in range(6)
        )
        spec = ZipRasterRenderSpec(
            (100, 100),
            decoder_maximum_size=(100, 100),
        )
        request = _request(
            1,
            units[2],
            *units,
            spec=spec,
            direction=1,
        )
        assert runtime.request(request)
        _wait_until(qapp, lambda: len(frames) == 1)
        source_bytes = runtime.decoded_source_bytes
        frame_bytes = runtime._frame_store.byte_size
        assert source_bytes > frame_bytes * 2
        assert runtime.cached_unit_count == 1
        # Several display frames plus two decoder-sized sources fit. Keeping
        # a decoded source beside every QPixmap does not. The runtime should
        # spend this constrained budget on a useful current-relative visual
        # runway and let source hydration remain an on-demand fallback.
        runtime.set_cache_limits(
            byte_budget=source_bytes * 2 + frame_bytes * 4
        )
        assert runtime.release_startup_runway(request_id=1)
        assert runtime.release_prefetch(request_id=1)
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        assert {1, 2, 3, 4}.issubset(runtime.cached_page_indexes)
        assert runtime.cached_unit_count >= 4
        assert runtime.decoded_source_count <= 2
        assert runtime.cache_bytes <= runtime.cache_byte_budget
        assert source.opens[:4] == ["2.png", "3.png", "1.png", "4.png"]
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_unknown_non_jpeg_prefetch_is_header_budgeted_before_pixel_decode(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class HeaderBudgetSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.opens: list[str] = []
            self.probes: list[str] = []

        def open_image(self, image_id: str) -> Image.Image:
            self.opens.append(image_id)
            return super().open_image(image_id)

        def probe_image_size(self, image_id: str) -> tuple[int, int] | None:
            self.probes.append(image_id)
            if image_id == "1.png":
                return (40_000, 60_000)
            return super().probe_image_size(image_id)

    archive = tmp_path / "lazy-mixed-size.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for index, size in enumerate(((2000, 2000), (130, 190), (140, 200))):
            path = tmp_path / f"{index}.png"
            with Image.new("RGB", size, (40 + index * 30, 80, 120)) as image:
                image.save(path)
            output.write(path, path.name)

    source = HeaderBudgetSource(archive)
    runtime = ZipRasterBookRuntime(source, 1, cache_byte_budget=64 << 20)
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    try:
        request = _request(1, _unit(0), _unit(0), _unit(1), _unit(2))
        assert runtime.request(request)
        _wait_until(qapp, lambda: len(frames) == 1)
        current_bytes = runtime.cache_bytes
        assert runtime.decoded_source_bytes > 4 << 20
        runtime.set_cache_limits(byte_budget=current_bytes + (4 << 20))
        assert runtime.release_startup_runway(request_id=1)
        assert runtime.release_prefetch(request_id=1)
        _wait_until(qapp, lambda: 2 in runtime.cached_page_indexes)

        # The lazy PNG reaches the worker for a header-only exact cost check,
        # but its multi-gigabyte raster never reaches Pillow/QImage decode.
        # Page 2 still probes and warms even though the retained 2000x2000
        # current source would make a GUI-side observed-size estimate too large.
        assert source.probes == ["1.png", "2.png"]
        assert source.opens == ["0.png", "2.png"]
        assert runtime.metrics.jobs_submitted == 3
        assert runtime.metrics.prefetch_admission_stops == 1
        assert runtime.metrics.oversized_prefetch_skips == 1
        assert runtime.cached_page_indexes == (0, 2)
        jobs_after_decline = runtime.metrics.jobs_submitted
        QTest.qWait(25)
        qapp.processEvents()
        assert runtime.metrics.jobs_submitted == jobs_after_decline
        assert runtime.active_job_count == 0
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_source_eviction_keeps_last_painted_frame_ready_for_reversal(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class FailingHydrationSource(ZipImageSource):
        fail_image_id: str | None = None

        def open_image(self, image_id: str) -> Image.Image:
            if image_id == self.fail_image_id:
                raise ImageSourceError("forced hydration failure")
            return super().open_image(image_id)

    source = FailingHydrationSource(_write_zip(tmp_path, pages=2))
    runtime = ZipRasterBookRuntime(
        source,
        1,
        cache_byte_budget=64 * 1024 * 1024,
    )
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    spec = ZipRasterRenderSpec((100, 100))
    first = _request(1, _unit(0), _unit(0), spec=spec)
    second = _request(2, _unit(1), _unit(1), spec=spec)
    try:
        assert runtime.request(first)
        _wait_until(qapp, lambda: bool(frames) and frames[-1].request_id == 1)
        assert runtime.release_prefetch(request_id=1)

        assert runtime.request(second)
        _wait_until(qapp, lambda: bool(frames) and frames[-1].request_id == 2)
        assert 0 in runtime.cached_page_indexes
        old_source = runtime._source_store.find(
            first.current.pages[0],
            spec,
            unit=first.current,
        )
        assert old_source is not None
        target_budget = runtime.cache_bytes - old_source.qimage.sizeInBytes()
        runtime.set_cache_limits(byte_budget=target_budget)
        # The prior painted source remains protected until the replacement
        # paint acknowledgement transfers displayed ownership.
        assert runtime._source_store.get(old_source.key) is not None
        source_evictions_before = runtime.metrics.source_cache_evictions
        assert runtime.release_prefetch(request_id=2)
        assert (
            runtime.metrics.source_cache_evictions
            == source_evictions_before + 1
        )
        assert runtime.cache_bytes <= target_budget

        jobs_before_reversal = runtime.metrics.jobs_submitted
        assert runtime.request(_request(3, _unit(0), _unit(0), spec=spec))
        qapp.processEvents()
        assert frames[-1].request_id == 3
        assert frames[-1].cache_hit
        assert runtime.metrics.jobs_submitted == jobs_before_reversal

        # A full-spec QPixmap hit with an evicted source remains instant for
        # display.  A later source consumer makes only this unit cold and
        # hydrates it without clearing neighboring ready frames.
        assert frames[-1].pages[0].source_qimage is None
        hydrate = _request(4, _unit(0), _unit(0), spec=spec)
        assert runtime.require_cached_current_source(hydrate)
        # A superseding page restores the withheld QPixmap. Reversing before
        # hydration dispatch is still a pure ready hit with no worker job.
        assert runtime.stage(_request(5, _unit(1), _unit(1), spec=spec))
        assert runtime.request(_request(6, _unit(0), _unit(0), spec=spec))
        qapp.processEvents()
        assert frames[-1].request_id == 6
        assert frames[-1].cache_hit
        assert runtime.metrics.jobs_submitted == jobs_before_reversal

        # Cover the narrower race where a failed hydration result is already
        # queued for the GUI thread when navigation restores the ready frame.
        # Page 0 remains in page 1's work order, so the late result is relevant
        # but must not overwrite the known-good QPixmap with an error frame.
        source.fail_image_id = "0.png"
        failed_hydrate = _request(7, _unit(0), _unit(0), spec=spec)
        assert runtime.require_cached_current_source(failed_hydrate)
        assert runtime.request(failed_hydrate)
        assert runtime.wait_for_done(3000)
        assert runtime.stage(
            _request(8, _unit(1), _unit(1), _unit(0), spec=spec)
        )
        jobs_after_failed_hydration = runtime.metrics.jobs_submitted
        frames_before_reversal = len(frames)
        assert runtime.request(_request(9, _unit(0), _unit(0), spec=spec))
        qapp.processEvents()
        assert frames[-1].request_id == 9
        assert frames[-1].cache_hit
        assert frames[-1].pages[0].error is None
        assert len(frames) == frames_before_reversal + 1
        assert runtime.metrics.jobs_submitted == jobs_after_failed_hydration
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        source.fail_image_id = None
        # Give the compatible successful result room to remain resident; the
        # earlier deliberately source-excluding budget already covered the
        # eviction/ready-frame contract above.
        runtime.set_cache_limits(
            byte_budget=target_budget + old_source.qimage.sizeInBytes()
        )
        hydrate = _request(10, _unit(0), _unit(0), spec=spec)
        assert runtime.require_cached_current_source(hydrate)
        assert runtime.request(hydrate)
        assert runtime.wait_for_done(3000)
        assert runtime.stage(
            _request(11, _unit(1), _unit(1), _unit(0), spec=spec)
        )
        frames_before_successful_reversal = len(frames)
        assert runtime.request(_request(12, _unit(0), _unit(0), spec=spec))
        qapp.processEvents()
        assert frames[-1].request_id == 12
        assert frames[-1].cache_hit
        assert len(frames) == frames_before_successful_reversal + 1
        # Presentation serials remain fenced, but the completed immutable
        # source artifact from request 10 is compatible with request 12's
        # exact book/render key. Retain it and avoid a duplicate hydration.
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        retained_source = runtime._source_store.find(
            _unit(0).pages[0],
            spec,
            unit=_unit(0),
        )
        assert retained_source is not None, (
            runtime.cache_debug_values(),
            runtime.metrics,
        )
        assert runtime.metrics.compatible_old_results >= 1
        fresh_hydration = _request(13, _unit(0), _unit(0), spec=spec)
        assert not runtime.require_cached_current_source(fresh_hydration)
        jobs_before_fresh_hydration = runtime.metrics.jobs_submitted
        assert runtime.request(fresh_hydration)
        qapp.processEvents()
        assert runtime.metrics.jobs_submitted == jobs_before_fresh_hydration
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
