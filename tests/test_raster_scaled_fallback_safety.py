from __future__ import annotations

from pathlib import Path

from app.image_source import ImageSource
from app.zip_raster_book_runtime import (
    ZipRasterDisplayUnit,
    ZipRasterPage,
    ZipRasterRenderSpec,
    _UnitKey,
    _ZipRasterUnitJob,
)


class FailedScaledJpegSource(ImageSource):
    def __init__(self):
        super().__init__(Path("."))
        self.full_decodes = 0
        self.probes = 0

    def list_images(self):
        return ["very-large.jpg"]

    def display_path(self, image_id):
        return image_id

    def open_image(self, image_id):
        self.full_decodes += 1
        raise AssertionError("A large full decode must be refused")

    def probe_jpeg_size(self, image_id):
        self.probes += 1
        return (30000, 40000)


def test_failed_scaled_decode_does_not_fall_back_to_unbounded_full_raster():
    source = FailedScaledJpegSource()
    unit = ZipRasterDisplayUnit(
        0,
        (ZipRasterPage(0, "very-large.jpg"),),
        True,
    )
    spec = ZipRasterRenderSpec(
        (1280, 800),
        decoder_maximum_size=(1280, 800),
    )
    key = _UnitKey(1, id(source), unit.identity, True, spec)
    job = _ZipRasterUnitJob(
        serial=1,
        key=key,
        request_id=1,
        source=source,
        unit=unit,
    )

    result = job._decode_page(unit.pages[0])

    assert result.qimage is None
    assert result.error
    assert source.probes == 1
    assert source.full_decodes == 0
