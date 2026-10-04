"""Real Qt-offscreen tests; run in the full repository, not the fragment harness."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import monotonic
import zipfile

import pytest
pytest.importorskip("PySide6", reason="real Qt runtime verification requires PySide6")
from PIL import Image
from PySide6.QtTest import QTest

from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.image_source import FolderImageSource, ZipImageSource
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
from app.zip_raster_book_runtime import (
    ZipRasterBookRuntime, ZipRasterDisplayUnit, ZipRasterPage,
    ZipRasterRenderSpec, ZipRasterRequest,
)


def _wait(qapp, predicate):
    deadline = monotonic() + 5.0
    while not predicate() and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(5)
    assert predicate()


def _book(tmp_path, folder):
    images = tmp_path / "images"
    images.mkdir()
    for i in range(3):
        with Image.new("RGB", (120, 180)) as image:
            image.save(images / f"{i}.jpg")
    if folder:
        return images
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in sorted(images.glob("*.jpg")):
            output.write(path, path.name)
    return archive


def _request(source, mode, *, current_index=0, request_id=1):
    units = tuple(ZipRasterDisplayUnit(i, (ZipRasterPage(i, name),), True)
                  for i, name in enumerate(source.list_images()))
    topology = RasterBookTopology(
        units, identity_of=lambda unit:unit.identity,
        page_indexes_of=lambda unit:(page.page_index for page in unit.pages),
        page_count=len(units),
    )
    current = units[current_index]
    plan = RasterWarmupPlan(
        topology, current=current, identity_of=lambda unit:unit.identity,
        page_indexes_of=lambda unit:(page.page_index for page in unit.pages),
        direction=1, background_enabled=True,
    )
    spec = ZipRasterRenderSpec(
        (320, 240), fit_mode="fit_window" if mode=="nearest" else mode,
        manual_zoom=1.25, downscale_algorithm="nearest" if mode=="nearest" else "auto",
        decoder_maximum_size=None, decoder_layout_sized=True,
    )
    return ZipRasterRequest(1, request_id, current, plan, spec, navigation_direction=1)


@pytest.mark.parametrize("folder", [False, True], ids=["zip", "folder"])
@pytest.mark.parametrize("mode", ["actual_size", "manual_zoom", "nearest"])
def test_full_source_background_builds_ready_neighbor_without_navigation_redecode(tmp_path, qapp, folder, mode):
    source = (FolderImageSource if folder else ZipImageSource)(_book(tmp_path, folder))
    runtime = (FolderRasterBookRuntime if folder else ZipRasterBookRuntime)(
        source, 1, cache_byte_budget=8 * 1024 * 1024,
    )
    frames = []
    runtime.frameReady.connect(frames.append)
    try:
        req = _request(source, mode)
        assert runtime.request(req)
        _wait(qapp, lambda:len(frames)==1)
        assert runtime.release_continuous_warmup(request_id=1)
        _wait(qapp, lambda:runtime.cached_unit_count==3 and not runtime.has_unfinished_tasks())
        assert not runtime.auto_cache_growth_requested
        assert runtime.matches_render_spec(req.render_spec)
        assert not runtime.matches_render_spec(replace(req.render_spec, device_pixel_ratio=2.0))
        before = runtime.metrics
        next_req = _request(source, mode, current_index=1, request_id=2)
        assert runtime.has_cached_current(next_req)
        assert runtime.request(next_req)
        assert frames[-1].cache_hit
        assert runtime.metrics.qpixmap_creations == before.qpixmap_creations
        assert runtime.metrics.jobs_submitted == before.jobs_submitted
        assert runtime.cache_bytes <= runtime.cache_byte_budget
    finally:
        _wait(qapp, lambda:not runtime.has_unfinished_tasks())
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_full_source_oversized_neighbor_is_declined_before_pixels(tmp_path, qapp):
    path = _book(tmp_path, True)

    forbidden_decode_attempts = []

    class GuardedSource(FolderImageSource):
        def probe_jpeg_size(self, image_id):
            if Path(image_id).name == "1.jpg":
                return 50_000, 50_000
            return super().probe_jpeg_size(image_id)

        def open_compatible_jpeg_at_most(self, image_id, maximum_size):
            if Path(image_id).name == "1.jpg":
                forbidden_decode_attempts.append(image_id)
            assert Path(image_id).name != "1.jpg", "oversized full-source prefetch reached pixels"
            return super().open_compatible_jpeg_at_most(image_id, maximum_size)

        def open_image(self, image_id):
            if Path(image_id).name == "1.jpg":
                forbidden_decode_attempts.append(image_id)
            assert Path(image_id).name != "1.jpg", "oversized source used generic fallback"
            return super().open_image(image_id)

    source = GuardedSource(path)
    runtime = FolderRasterBookRuntime(source, 1, cache_byte_budget=1024 * 1024)
    frames=[]
    runtime.frameReady.connect(frames.append)
    try:
        req=_request(source,"actual_size")
        assert runtime.request(req)
        _wait(qapp,lambda:len(frames)==1)
        assert runtime.release_continuous_warmup(request_id=1)
        _wait(qapp, lambda:runtime.warmup_stop_reason in {"complete", "complete_with_skips"} and not runtime.has_unfinished_tasks())
        assert not runtime.has_cached_current(_request(source,"actual_size",current_index=1,request_id=2))
        assert runtime.has_cached_current(_request(source,"actual_size",current_index=2,request_id=2))
        assert runtime.metrics.oversized_prefetch_skips > 0
        assert not forbidden_decode_attempts
        assert runtime.cache_bytes <= runtime.cache_byte_budget
    finally:
        _wait(qapp,lambda:not runtime.has_unfinished_tasks())
        assert runtime.shutdown(wait_msecs=3000)
        source.close()
