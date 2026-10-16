from io import BytesIO
from dataclasses import replace
import zipfile

from PIL import Image, ImageOps
from PySide6.QtGui import QPixmap
import pytest

from app.image_source import FolderImageSource, ZipImageSource, SevenZipImageSource
from app import zip_raster_book_runtime as raster
from app.thumbnail_render import pil_to_qimage
from app.viewer_render import ResamplingPolicy, pillow_resampling_for_policy
from tests.test_zip_raster_book_runtime import _request, _wait_until
from tests.test_external_archive_image_source import FakeBackend, archive_entry


@pytest.mark.parametrize("kind", ["folder", "zip", "external"])
@pytest.mark.parametrize("orientation", range(1, 9))
def test_png_transfers_pixels_with_orientation_and_closed_stream(tmp_path, monkeypatch, kind, orientation):
    page = tmp_path / "page.png"
    with Image.new("RGBA", (17, 23)) as image:
        image.putdata([(x * 11, y * 9, (x+y) * 5, (x * 17 + y) % 256)
                       for y in range(23) for x in range(17)])
        exif = Image.Exif()
        exif[274] = orientation
        image.save(page, exif=exif)
    with Image.open(page) as image, ImageOps.exif_transpose(image) as expected:
        expected_size, expected_pixels = expected.size, expected.tobytes()
    if kind == "zip":
        archive = tmp_path / "book.cbz"
        with zipfile.ZipFile(archive, "w") as output:
            output.write(page, page.name)
        source, image_id = ZipImageSource(archive), page.name
    elif kind == "external":
        archive = tmp_path / "book.cb7"
        archive.write_bytes(b"fake archive")
        data = page.read_bytes()
        source, image_id = SevenZipImageSource(
            archive, backend=FakeBackend((archive_entry(page.name, len(data)),), data=data)), page.name
    else:
        source, image_id = FolderImageSource(tmp_path), str(page)
    streams = []
    original = Image.open
    def open_image(*args, **kwargs):
        result = original(*args, **kwargs)
        streams.append(result.fp)
        return result
    monkeypatch.setattr(Image, "open", open_image)
    monkeypatch.setattr(Image.Image, "copy", lambda *_: pytest.fail("No full PNG copy"))
    try:
        with source.open_image(image_id) as image:
            assert image.size == expected_size
            assert image.tobytes() == expected_pixels
            assert image.getexif().get(274, 1) == 1
            assert all(stream.closed for stream in streams)
            assert image._nivis_source_is_animated is False
        image.close()
        assert not getattr(source, "_active_requests", {})
    finally:
        source.close()


def test_animated_png_keeps_detached_first_frame(tmp_path):
    page = tmp_path / "page.png"
    with Image.new("RGBA", (19, 29), (20, 30, 40, 120)) as first, Image.new("RGBA", (19, 29), "red") as second:
        first.save(page, save_all=True, append_images=[second], duration=50)
    source = FolderImageSource(tmp_path)
    try:
        with source.open_image(str(page)) as image:
            assert image.size == (19, 29)
            assert image.getpixel((0, 0)) == (20, 30, 40, 120)
            assert image._nivis_source_is_animated is True
        image.close()
    finally:
        source.close()


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.5])
@pytest.mark.parametrize("spread", [False, True])
@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_png_source_is_final_size_after_one_resample(tmp_path, qapp, monkeypatch, dpr, spread, mode):
    archive = tmp_path / "book.cbz"
    size = (801, 1203)
    with Image.new(mode, size, (30, 70, 150, 160) if mode == "RGBA" else (30, 70, 150)) as image:
        # A sharp feature and alpha edge make the pixel comparison meaningful.
        image.paste((220, 40, 80, 32) if mode == "RGBA" else (220, 40, 80), (13, 17, 419, 623))
        payload = BytesIO()
        image.save(payload, "PNG")
    count = 2 if spread else 1
    with zipfile.ZipFile(archive, "w") as output:
        for i in range(count): output.writestr(f"{i}.png", payload.getvalue())
    source = ZipImageSource(archive)
    runtime = raster.ZipRasterBookRuntime(source, 1)
    unit = raster.ZipRasterDisplayUnit(0, tuple(
        raster.ZipRasterPage(i, f"{i}.png", size, False) for i in range(count)), not spread)
    spec = raster.ZipRasterRenderSpec((317, 231), device_pixel_ratio=dpr,
        decoder_maximum_size=(round(317*dpr), round(231*dpr)), decoder_layout_sized=True,
        downscale_algorithm="sharp", upscale_algorithm="lanczos")
    frames, renders = [], []
    original = raster.render_qimage
    def render(image, key):
        result, resized = original(image, key)
        renders.append((image.size().toTuple(), (key.target_width, key.target_height), resized))
        return result, resized
    monkeypatch.setattr(raster, "render_qimage", render)
    runtime.frameReady.connect(frames.append)
    try:
        assert runtime.request(_request(1, unit, spec=spec))
        _wait_until(qapp, lambda: bool(frames))
        assert len(renders) == count
        assert all(actual == target and not resized for actual, target, resized in renders)
        for page in frames[-1].pages:
            assert not page.error
            target = page.source_qimage.size().toTuple()
            with Image.open(BytesIO(payload.getvalue())) as image:
                policy = ResamplingPolicy("sharp", "lanczos")
                with image.resize(target, pillow_resampling_for_policy(policy, size, target)) as expected:
                    assert page.source_qimage == pil_to_qimage(expected)
                    assert page.pixmap.toImage() == QPixmap.fromImage(pil_to_qimage(expected)).toImage()
        # Invalidate only frames: exact-size sources must still satisfy the
        # source cache on the next request, despite ceil-based decoder bounds.
        submitted = runtime.metrics.source_cache_misses
        runtime.invalidate_layout()
        assert runtime.request(_request(2, unit, spec=spec))
        _wait_until(qapp, lambda: len(frames) == 2)
        assert runtime.metrics.source_cache_misses == submitted
        assert runtime.metrics.source_cache_hits >= count
        # A slightly larger frame must not mistake the exact-size source for
        # a sufficient one. Full-pixel requests (for example the lens) still
        # promote it rather than enlarging a retained preview.
        larger = replace(spec, viewport_size=(325, 239))
        runtime.invalidate_layout()
        assert runtime.request(_request(3, unit, spec=larger))
        _wait_until(qapp, lambda: len(frames) == 3)
        assert runtime.metrics.source_cache_misses == submitted + count
        full = replace(spec, decoder_maximum_size=None, fit_mode="actual_size")
        assert runtime.request(_request(4, unit, spec=full))
        _wait_until(qapp, lambda: len(frames) == 4)
        assert all(page.source_qimage.size().toTuple() == size
                   and not page.source_is_preview for page in frames[-1].pages)
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()
