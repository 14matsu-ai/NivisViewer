"""Unknown raster costs are confirmed on the worker before prefetch decode."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.image_source import ImageSource, jpeg_native_reduction_size
from app.zip_raster_book_runtime import (
    ZipRasterDisplayUnit,
    ZipRasterPage,
    ZipRasterRenderSpec,
    _CachedSource,
    _SourceKey,
    _UnitKey,
    _ZipRasterUnitJob,
    _requires_exact_prefetch_admission,
)


class HeaderSource(ImageSource):
    def __init__(self, sizes):
        super().__init__(Path("."))
        self.sizes = sizes
        self.probes = []

    def list_images(self):
        return list(self.sizes)

    def display_path(self, image_id):
        return image_id

    def open_image(self, image_id):
        raise AssertionError("Header admission must not decode pixels")

    def probe_image_size(self, image_id):
        self.probes.append(image_id)
        return self.sizes[image_id]

    def probe_jpeg_size(self, image_id):
        return self.probe_image_size(image_id)

    def estimate_compatible_jpeg_size(self, logical_size, maximum_size):
        return jpeg_native_reduction_size(logical_size, maximum_size)


@pytest.fixture
def app():
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        pytest.skip("offscreen is mandatory for this regression")
    instance = QApplication.instance() or QApplication([])
    yield instance


def make_job(sizes, budget, *, full=False, cached_sources=None):
    source = HeaderSource({f"{i}.jpg": size for i, size in enumerate(sizes)})
    unit = ZipRasterDisplayUnit(
        0,
        tuple(
            ZipRasterPage(i, image_id)
            for i, image_id in enumerate(source.list_images())
        ),
        len(sizes) == 1,
    )
    spec = ZipRasterRenderSpec(
        viewport_size=(3840, 2160),
        fit_mode="fit_window",
        gap=12,
        downscale_algorithm="auto",
        upscale_algorithm="auto",
        decoder_maximum_size=None if full else (3840, 2160),
        decoder_layout_sized=True,
    )
    key = _UnitKey(1, id(source), unit.identity, unit.is_single, spec)
    job = _ZipRasterUnitJob(
        serial=1,
        key=key,
        request_id=1,
        source=source,
        unit=unit,
        cached_sources=cached_sources,
        prefetch_budget_bytes=budget,
        prefetch_hard_limit_bytes=256 * 1024**2,
    )
    return source, unit, spec, job


@pytest.mark.parametrize(
    "size", [(1152, 2016), (4000, 6000), (6000, 9000), (2016, 1152)]
)
def test_unknown_jpeg_budget_uses_confirmed_dimensions(app, size):
    source, unit, _spec, job = make_job((size, size), 100 * 1024**2)
    assert _requires_exact_prefetch_admission(
        unit.pages[0], (1914, 2160), has_cached_source=False
    )
    assert _requires_exact_prefetch_admission(
        unit.pages[0], (1914, 2160), has_cached_source=True
    )
    identity = job.unit.identity
    assert job._admit_unknown_prefetch_unit()
    assert source.probes == ["0.jpg", "1.jpg"]
    assert job.unit.identity == identity
    assert [page.known_size for page in job.unit.pages] == [size, size]


def test_cached_source_supplies_unknown_geometry_without_header_io(app):
    source, _unit, _spec, job = make_job(((120, 180),), 12 * 1024**2)
    qimage = QImage(120, 180, QImage.Format.Format_ARGB32)
    cache_key = _SourceKey(1, id(source), "0.jpg", (1.0, 1.0, 1.0), (120, 180), True)
    cached = _CachedSource(cache_key, 0, qimage, (120, 180), False)
    job.cached_sources["0.jpg"] = cached
    assert job._admit_unknown_prefetch_unit()
    assert job.source.probes == []
    assert job.unit.pages[0].known_size == (120, 180)


def test_confirmed_oversized_full_source_does_not_reach_decode(app):
    source, _unit, _spec, job = make_job(
        ((6000, 9000), (6000, 9000)), 256 * 1024**2, full=True
    )
    assert job._render_unit() == ()
    assert source.probes == ["0.jpg", "1.jpg"]
    assert job.admission_declined and job.oversized_prefetch


def test_cancel_before_header_probe_is_not_a_capacity_decline(app):
    source, _unit, _spec, job = make_job(((1152, 2016),) * 2, 100 * 1024**2)
    job.cancelled.set()
    assert not job._admit_unknown_prefetch_unit()
    assert source.probes == []
    assert not job.admission_declined
