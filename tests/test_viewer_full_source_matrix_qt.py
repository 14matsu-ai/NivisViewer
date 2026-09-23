"""Matched current-only control versus admitted warmup; real JPEG/Qt artifacts."""
from threading import Lock
import zipfile

import pytest
from PIL import Image
from app.image_source import FolderImageSource, ZipImageSource
from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
from app.zip_raster_book_runtime import ZipRasterBookRuntime, ZipRasterDisplayUnit, ZipRasterPage, ZipRasterRenderSpec, ZipRasterRequest
import app.zip_raster_book_runtime as raster
from test_viewer_refinements_qt import _wait


@pytest.mark.parametrize("folder", [False, True], ids=["zip-1lane", "folder-2lane"])
@pytest.mark.parametrize("mode", ["actual_size", "manual_zoom", "nearest"])
@pytest.mark.parametrize("shape", ["portrait", "landscape", "mixed"])
@pytest.mark.parametrize("spread,known", [(False, False), (False, True), (True, False), (True, True)])
def test_matched_full_source_artifacts(tmp_path, qapp, monkeypatch, folder, mode, shape, spread, known):
    images = tmp_path / "images"
    images.mkdir()
    sizes = [(120, 180) if shape == "portrait" or (shape == "mixed" and i % 2 == 0)
             else (180, 120) for i in range(4)]
    for i, size in enumerate(sizes):
        with Image.new("RGB", size, (i * 40, 90, 130)) as image:
            image.save(images / f"{i}.jpg")
    path = images
    if not folder:
        path = tmp_path / "book.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for image in sorted(images.iterdir()):
                archive.write(image, image.name)
    source_type = FolderImageSource if folder else ZipImageSource
    counts = {"decode": 0, "render": 0, "resize": 0}
    lock = Lock()
    original_render = raster.render_qimage
    def render(*args, **kwargs):
        result = original_render(*args, **kwargs)
        with lock:
            counts["render"] += 1
            counts["resize"] += int(result[1])
        return result
    monkeypatch.setattr(raster, "render_qimage", render)
    class Source(source_type):
        def open_compatible_jpeg_at_most(self, *args, **kwargs):
            with lock:
                counts["decode"] += 1
            return super().open_compatible_jpeg_at_most(*args, **kwargs)
        def open_image(self, *args, **kwargs):
            with lock:
                counts["decode"] += 1
            return super().open_image(*args, **kwargs)
    deltas = []
    for enabled in (False, True):
        source = Source(path)
        runtime = (FolderRasterBookRuntime if folder else ZipRasterBookRuntime)(
            source, 1, cache_byte_budget=16 * 1024 * 1024, max_active_jobs=2 if folder else 1)
        pages = tuple(ZipRasterPage(i, name, sizes[i] if known else None) for i, name in enumerate(source.list_images()))
        width = 2 if spread else 1
        units = tuple(ZipRasterDisplayUnit(i, pages[i:i+width], not spread) for i in range(0, 4, width))
        topology = RasterBookTopology(units, identity_of=lambda u: u.identity,
            page_indexes_of=lambda u: (p.page_index for p in u.pages), page_count=4)
        def request(index, serial):
            plan = RasterWarmupPlan(topology, current=units[index], identity_of=lambda u: u.identity,
                page_indexes_of=lambda u: (p.page_index for p in u.pages), direction=1, background_enabled=enabled)
            spec = ZipRasterRenderSpec((320, 240), fit_mode="fit_window" if mode == "nearest" else mode,
                manual_zoom=1.25, downscale_algorithm="nearest" if mode == "nearest" else "auto",
                decoder_maximum_size=None, decoder_layout_sized=True)
            return ZipRasterRequest(1, serial, units[index], plan, spec, navigation_direction=1)
        frames = []
        runtime.frameReady.connect(frames.append)
        try:
            assert runtime.request(request(0, 1))
            _wait(qapp, lambda: len(frames) == 1)
            runtime.release_continuous_warmup(request_id=1)
            _wait(qapp, lambda: not runtime.has_unfinished_tasks())
            next_request = request(1, 2)
            assert runtime.has_cached_current(next_request) == enabled
            before = dict(counts)
            pixmaps = runtime.metrics.qpixmap_creations
            assert runtime.request(next_request)
            _wait(qapp, lambda: len(frames) == 2 and not runtime.has_unfinished_tasks())
            assert frames[-1].cache_hit == enabled
            delta = {key: counts[key] - before[key] for key in counts}
            delta["pixmap"] = runtime.metrics.qpixmap_creations - pixmaps
            deltas.append(delta)
            if enabled:
                assert all(value == 0 for value in delta.values()), delta
            else:
                assert delta["decode"] > 0 and delta["render"] > 0 and delta["pixmap"] > 0
        finally:
            assert runtime.shutdown(wait_msecs=3000)
            source.close()
    assert deltas[1]["decode"] < deltas[0]["decode"]
