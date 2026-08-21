from __future__ import annotations

from pathlib import Path
from threading import Event, Lock
from time import monotonic, sleep
import zipfile

from PIL import Image
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.image_source import ImageSourceError, ZipImageSource
from app.image_work_coordinator import ImageWorkCoordinator
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


def test_book_wide_cache_store_ledgers_stay_exact_across_mutations(
    qapp: QApplication,
) -> None:
    del qapp  # QPixmap construction only requires the shared application.
    artifact_count = 128
    spec = ZipRasterRenderSpec((640, 480))
    units = tuple(_unit(index) for index in range(artifact_count))

    source_store = _ZipRasterSourceStore(page_limit=artifact_count * 2)
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
    reordered_units = (units[0],) + tuple(reversed(units[1:]))
    source_store.set_retention_order(
        reordered_units,
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
    assert source_store.set_page_limit(96) == artifact_count - 96
    assert source_store.evict_one()
    assert source_store.page_count == 95
    assert_source_ledger()
    source_store.clear()
    assert_source_ledger()

    frame_store = _ZipRasterFrameStore(
        unit_limit=artifact_count * 2,
        byte_budget=1 << 40,
    )
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
    reordered_keys = (frame_keys[0],) + tuple(reversed(frame_keys[1:]))
    frame_store.set_retention_order(reordered_keys, frame_keys[0], -1)
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
    assert frame_store.set_limits(unit_limit=96) == artifact_count - 96
    assert frame_store.evict_one()
    assert frame_store.unit_count == 95
    assert_frame_ledger()
    frame_store.clear()
    assert_frame_ledger()


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


def test_paint_ack_reclaims_old_frame_from_combined_soft_overflow(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = ZipImageSource(_write_zip(tmp_path, pages=2))
    runtime = ZipRasterBookRuntime(
        source,
        1,
        cache_unit_limit=2,
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
        cache_unit_limit=8,
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
        assert runtime.cache_unit_limit == 8
        assert runtime.request(request)
        _wait_until(qapp, lambda: len(frames) == 1)

        runtime.set_cache_limits(byte_budget=runtime.cache_bytes + 1)
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
        cache_unit_limit=3,
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
        assert runtime.metrics.jobs_submitted == 2
        assert runtime.cache_bytes <= reduced_budget
        QTest.qWait(25)
        qapp.processEvents()
        assert runtime.metrics.jobs_submitted == 2
        assert not runtime.has_unfinished_tasks()
    finally:
        source.release_background.set()
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_full_cache_navigation_reclaims_only_lower_rank_for_new_neighbor(
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
        cache_unit_limit=3,
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
        assert runtime.release_prefetch(request_id=1)
        _wait_until(
            qapp,
            lambda: runtime.cached_unit_count == 3
            and not runtime.has_unfinished_tasks(),
        )
        assert set(runtime.cached_page_indexes) == {0, 1, 2}

        # Freeze the combined store at its exact full size, then move the
        # ready current to page 2.  Page 3 outranks far page 0 in the new order
        # and must slide the retained window despite there being zero free
        # bytes at admission time.
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
        ) is not None
        assert runtime.cache_bytes <= full_budget
        assert runtime.metrics.jobs_submitted == jobs_before + 2
        assert runtime.metrics.cache_evictions > evictions_before
    finally:
        source.release_background.set()
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_reordered_warmup_cancels_background_that_is_no_longer_next(
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
            if image_id == "1.png":
                self.background_started.set()
                self.release_background.wait(2.0)
            return super().open_image(image_id)

    source = BlockingSource(archive)
    runtime = ZipRasterBookRuntime(
        source,
        1,
        cache_unit_limit=4,
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
            _unit(0),
            _unit(0),
            _unit(1),
            _unit(2),
            _unit(3),
            direction=1,
        )
        assert runtime.request(first)
        _wait_until(qapp, lambda: len(frames) == 1)
        assert runtime.release_prefetch(request_id=1)
        assert source.background_started.wait(1.0)
        source.release_background.set()
        # Finish the old decode without draining its queued GUI result.  This
        # is the narrow full-book race: page 1 remains in the replacement
        # order, but its immutable result still carries request 1.
        assert runtime.wait_for_done(3000)

        # Page 1 remains somewhere in the replacement full-book order, but
        # page 3 is now the highest-priority missing background unit.  The old
        # job must be cancelled rather than adopted under the new serial.
        second = _request(
            2,
            _unit(0),
            _unit(0),
            _unit(3),
            _unit(2),
            _unit(1),
            direction=-1,
        )
        assert runtime.request(second)
        assert runtime.release_prefetch(request_id=2)
        assert runtime.metrics.cancel_requests == 1
        _wait_until(qapp, lambda: 3 in artifacts)

        assert artifacts[:2] == [0, 3]
        assert runtime.metrics.stale_results >= 1
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
    finally:
        source.release_background.set()
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

        # An explicit full-resolution demand upgrades once while preserving the
        # navigation preview as a separate tier.
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
        assert runtime.decoded_source_count == 2

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
        assert frames[-1].pages[0].source_is_preview
        assert runtime.metrics.jobs_submitted == jobs_before_ready_return

        # The full source can satisfy a later preview larger than the retained
        # navigation tier without evicting that smaller tier.
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

        assert source.decode_calls == 2
        assert runtime.metrics.jobs_submitted == 4
        assert runtime.metrics.source_cache_hits == 2
        assert runtime.metrics.source_cache_misses == 2
        assert runtime.decoded_source_count == 2
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
            assert runtime.decoded_source_count == 1
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
            for index in range(3)
        )
        request = _request(1, units[1], units[1], units[2], units[0])
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
        cache_unit_limit=3,
        cache_byte_budget=100_000,
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
        assert runtime.metrics.source_cache_evictions >= 1

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

        source.fail_image_id = None
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
        # A result emitted under request 10 must not mutate source/frame cache
        # after request 12, even though full-book work orders can keep the same
        # key relevant by identity.  The next source consumer therefore asks
        # for a fresh hydration rather than accepting stale work.
        assert runtime.decoded_source_count == 0
        fresh_hydration = _request(13, _unit(0), _unit(0), spec=spec)
        assert runtime.require_cached_current_source(fresh_hydration)
        jobs_before_fresh_hydration = runtime.metrics.jobs_submitted
        assert runtime.request(fresh_hydration)
        _wait_until(qapp, lambda: runtime.decoded_source_count > 0)
        assert (
            runtime.metrics.jobs_submitted
            == jobs_before_fresh_hydration + 1
        )
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
