from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic
import zipfile

from PIL import Image
import pytest
from PySide6.QtCore import QByteArray
from PySide6.QtTest import QTest

from app.image_source import ImageSource, ZipImageSource
from app.raster_layout_metadata import select_layout_metadata_pages
from app.raster_warmup_planner import (
    RasterBookTopology,
    RasterWarmupPlan,
    RasterWarmupPlanner,
    WarmupStopReason,
)
from app.zip_raster_book_runtime import (
    ZipRasterBookRuntime,
    ZipRasterDisplayUnit,
    ZipRasterPage,
    ZipRasterRenderSpec,
    ZipRasterRequest,
)
from app.zip_read_ahead import ZipReadAhead


def _unit(index: int, size=None) -> ZipRasterDisplayUnit:
    return ZipRasterDisplayUnit(
        index,
        (ZipRasterPage(index, f"{index}.png", size),),
        True,
    )


def _plan(units, current_index=0, direction=1):
    units = tuple(units)
    topology = RasterBookTopology(
        units,
        identity_of=lambda unit: unit.identity,
        page_indexes_of=lambda unit: (
            page.page_index for page in unit.pages
        ),
        page_count=len(units),
    )
    current = units[current_index]
    return RasterWarmupPlan(
        topology,
        current=current,
        identity_of=lambda unit: unit.identity,
        page_indexes_of=lambda unit: (
            page.page_index for page in unit.pages
        ),
        direction=direction,
        background_enabled=True,
    )


def _wait(qapp, predicate, timeout_ms=5000):
    deadline = monotonic() + timeout_ms / 1000.0
    while not predicate() and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(5)
    assert predicate()


def test_header_corridor_can_bias_forward_without_reordering_decode_plan():
    units = tuple(_unit(index) for index in range(96))
    plan = _plan(units, current_index=48, direction=1)

    pages = select_layout_metadata_pages(
        units[48],
        plan,
        set(),
        maximum_pages=32,
        include_nearby=True,
        directional_bias=0.75,
    )
    indexes = [page.page_index for page in pages]

    assert indexes[0] == 48
    assert indexes[1:6] == [49, 50, 51, 52, 53]
    assert len(indexes) == 32
    assert sum(index > 48 for index in indexes) > sum(
        index < 48 for index in indexes
    )

    # The real pixel work order remains distance/priority based and is not
    # replaced by the metadata reconnaissance order.
    decode_order = [
        unit.start_index
        for unit in plan.iter_continuous_units(
            preferred_units=4,
            opposite_units=1,
        )
    ]
    assert decode_order[:5] == [49, 47, 50, 51, 52]
    assert decode_order.index(50) < decode_order.index(51)


def test_planner_can_expand_ready_band_after_metadata_discovers_heavy_page():
    units = tuple(_unit(index) for index in range(16))
    plan = _plan(units, current_index=2, direction=1)
    planner = RasterWarmupPlanner(plan)
    assert planner.release_after_first_commit(
        preferred_units=4,
        opposite_units=1,
    )
    planner.stop_for_soft_target()
    assert planner.stop_reason is WarmupStopReason.SOFT_TARGET

    assert planner.set_priority_band(
        preferred_units=8,
        opposite_units=1,
    )
    assert planner.stop_reason is WarmupStopReason.RUNNING
    assert planner.startup_target_count >= 8


def test_encoded_read_ahead_ring_fills_multiple_near_payloads():
    payloads = {
        f"p{index}": QByteArray(bytes([index]) * (100 + index))
        for index in range(1, 5)
    }
    reads = []

    def read(image_id, cancelled):
        assert not cancelled.is_set()
        reads.append(image_id)
        return QByteArray(payloads[image_id]), 1

    ring = ZipReadAhead(read, max_items=4)
    ring.configure(
        {
            "p0": ("p1", 101),
            "p1": ("p2", 102),
            "p2": ("p3", 103),
            "p3": ("p4", 104),
        },
        {"p1", "p2", "p3", "p4"},
    )
    try:
        ring.kick("p0")
        assert ring.wait(2.0)
        assert reads == ["p1", "p2", "p3", "p4"]
        assert ring.cached_count == 4
        expected = sum(payload.size() for payload in payloads.values())
        assert ring.reserved_bytes == expected

        for image_id in ("p1", "p2", "p3", "p4"):
            prepared = ring.take(image_id)
            assert prepared is not None
            assert prepared[0] == payloads[image_id]
        assert ring.hits == 4
        assert ring.reserved_bytes == 0
    finally:
        ring.close()


def test_zip_source_read_ahead_budget_is_cumulative(tmp_path: Path):
    archive = tmp_path / "ring.zip"
    names = []
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as output:
        for index in range(5):
            path = tmp_path / f"{index}.jpg"
            with Image.new(
                "RGB",
                (320, 480),
                (40 + index * 20, 80, 120),
            ) as image:
                image.save(path, format="JPEG", quality=90)
            output.write(path, path.name)
            names.append(path.name)

    source = ZipImageSource(archive)
    try:
        first = source._zip.getinfo(names[1]).file_size
        second = source._zip.getinfo(names[2]).file_size
        third = source._zip.getinfo(names[3]).file_size
        budget = first + second
        assert third > 0

        source.configure_read_ahead(tuple(names), budget)
        assert source._read_ahead is not None
        source._read_ahead.kick(names[0])
        assert source.wait_read_ahead(2.0)
        # Exactly the nearest two completed payloads fit this byte budget.
        assert source._read_ahead.cached_count == 2
        assert source.read_ahead_reserved_bytes <= budget
    finally:
        source.close()


class _CostSource(ImageSource):
    def __init__(self):
        super().__init__(Path("."))

    def list_images(self):
        return []

    def display_path(self, image_id):
        return image_id

    def open_image(self, image_id):
        raise AssertionError("cost planning must not decode")


def test_known_heavy_near_page_expands_runway_without_leapfrogging(qapp, tmp_path):
    del qapp
    archive = tmp_path / "cost.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("0.jpg", b"synthetic")
    source = ZipImageSource(archive)
    runtime = ZipRasterBookRuntime(
        source,
        1,
        cache_byte_budget=512 * 1024 * 1024,
    )
    sizes = [(1200, 1800)] * 12
    sizes[7] = (7000, 10000)
    units = tuple(_unit(index, sizes[index]) for index in range(12))
    plan = _plan(units, current_index=0, direction=1)
    request = ZipRasterRequest(
        1,
        1,
        units[0],
        plan,
        ZipRasterRenderSpec((1920, 1080)),
        navigation_direction=1,
    )
    try:
        preferred = runtime._ready_ahead_preferred_units(request)
        assert preferred >= 7
        identities = request.warmup_plan.priority_band_identities(
            preferred_units=preferred,
            opposite_units=1,
        )
        starts = [
            request.warmup_plan.unit_for_identity(identity).start_index
            for identity in identities
            if request.warmup_plan.unit_for_identity(identity) is not None
        ]
        # Heavy page 7 is included, but every nearer forward page remains
        # ahead of it. Cost changes *when* work starts, not distance ordering.
        assert starts.index(7) > starts.index(6)
        assert starts.index(7) > starts.index(1)
    finally:
        assert runtime.shutdown(wait_msecs=1000)
        source.close()


def test_repeated_mixed_resolution_zip_warmup_reaches_past_size_changes(
    tmp_path: Path,
    qapp,
) -> None:
    sizes = [
        (120, 180),
        (620, 930),
        (150, 220),
        (900, 1350),
        (130, 190),
        (700, 1050),
        (140, 200),
    ]
    archive = tmp_path / "mixed.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for index, size in enumerate(sizes):
            path = tmp_path / f"{index}.png"
            with Image.new(
                "RGB",
                size,
                (30 + index * 20, 90, 130),
            ) as image:
                image.save(path)
            output.write(path, path.name)

    source = ZipImageSource(archive)
    runtime = ZipRasterBookRuntime(
        source,
        1,
        cache_byte_budget=192 * 1024 * 1024,
    )
    names = source.list_images()
    units = tuple(
        ZipRasterDisplayUnit(
            index,
            (ZipRasterPage(index, name),),
            True,
        )
        for index, name in enumerate(names)
    )
    plan = _plan(units, current_index=0, direction=1)
    request = ZipRasterRequest(
        1,
        1,
        units[0],
        plan,
        ZipRasterRenderSpec((1280, 800)),
        navigation_direction=1,
        resolve_layout_metadata=True,
    )
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(request)
        _wait(qapp, lambda: bool(frames))
        assert runtime.release_continuous_warmup(request_id=1)
        _wait(
            qapp,
            lambda: not runtime.has_unfinished_tasks()
            and runtime.warmup_stop_reason
            in {"complete", "complete_with_skips"},
        )
        assert set(runtime.cached_page_indexes) == set(range(len(sizes)))
        assert runtime.metrics.layout_metadata_pages > 0
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


@pytest.mark.parametrize("direction,current_index,near_indexes", [
    (1, 0, (1, 2)),
    (-1, 4, (3, 2)),
])
def test_two_display_units_finish_before_distant_headers_or_pixels(
    tmp_path: Path, qapp, direction, current_index, near_indexes,
):
    archive = tmp_path / "ordered.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for index in range(7):
            path = tmp_path / f"{index}.png"
            with Image.new("RGB", (80, 120), (index * 25, 70, 90)) as image:
                image.save(path)
            output.write(path, path.name)

    source = ZipImageSource(archive)
    events = []
    original_probe = source.probe_image_size
    original_open = source.open_image

    def probe(image_id):
        events.append(("header", image_id))
        return original_probe(image_id)

    def open_image(image_id):
        events.append(("decode", image_id))
        return original_open(image_id)

    source.probe_image_size = probe
    source.open_image = open_image
    runtime = ZipRasterBookRuntime(source, 1, cache_byte_budget=64 << 20)
    names = source.list_images()
    units = tuple(ZipRasterDisplayUnit(
        index, (ZipRasterPage(index, name),), True,
    ) for index, name in enumerate(names))
    plan = _plan(units, current_index=current_index, direction=direction)
    request = ZipRasterRequest(
        1, 1, units[current_index], plan, ZipRasterRenderSpec((320, 240)),
        navigation_direction=direction, resolve_layout_metadata=True,
        next_display_units=tuple(units[index] for index in near_indexes),
    )
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(request)
        _wait(qapp, lambda: bool(frames))
        assert runtime.release_continuous_warmup(request_id=1)
        _wait(qapp, lambda: all(index in runtime.cached_page_indexes
                                for index in near_indexes))
        second_decode = events.index(("decode", names[near_indexes[1]]))
        distant = [position for position, (_kind, image_id) in enumerate(events)
                   if image_id not in {names[current_index],
                                       names[near_indexes[0]],
                                       names[near_indexes[1]]}]
        assert not distant or min(distant) > second_decode
        assert events.index(("decode", names[near_indexes[0]])) < second_decode
    finally:
        runtime.cancel(clear_artifacts=True)
        _wait(qapp, lambda: not runtime.has_unfinished_tasks())
        assert runtime.shutdown(wait_msecs=3000)
        source.close()
