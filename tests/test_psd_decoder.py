from io import BytesIO
from pathlib import Path
import zipfile

import pytest
from PIL import Image
from psd_tools import PSDImage
from psd_tools.constants import Compression, Resource
from psd_tools.psd.image_resources import ImageResource, ThumbnailResource, VersionInfo

from app import psd_decoder
from app.image_source import FolderImageSource, ZipImageSource, SevenZipImageSource
from app.archive_backend import ArchiveEntry, ArchiveListing
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec


def document(*, version=1, preview=True, thumbnail=False):
    with Image.new("RGB", (40, 60), "navy") as image:
        psd = PSDImage.frompil(image, compression=Compression.RAW)
    # RAW merged pixels have identical layout in PSD and PSB.
    psd._record.header.version = version
    psd.image_resources[Resource.VERSION_INFO] = ImageResource(
        key=Resource.VERSION_INFO, data=VersionInfo(has_composite=preview)
    )
    if thumbnail:
        output = BytesIO()
        with Image.new("RGB", (16, 24), "red") as image:
            image.save(output, format="JPEG")
        psd.image_resources[Resource.THUMBNAIL_RESOURCE] = ImageResource(
            key=Resource.THUMBNAIL_RESOURCE,
            data=ThumbnailResource(fmt=1, width=16, height=24, row=48,
                                   total_size=1152, bits=24, planes=1, data=output.getvalue()),
        )
    output = BytesIO()
    psd.save(output)
    return output.getvalue()


@pytest.mark.parametrize("version,suffix", [(1, ".psd"), (2, ".psb")])
def test_real_folder_zip_and_header(tmp_path, version, suffix):
    data = document(version=version)
    path = tmp_path / ("日本語" + suffix)
    path.write_bytes(data)
    folder = FolderImageSource(tmp_path)
    assert folder.list_images() == [str(path)]
    assert folder.probe_image_size(str(path)) == (40, 60)
    assert folder.probe_image_is_animated(str(path)) is False
    with folder.open_image(str(path)) as image:
        assert image.size == (40, 60)
        assert image.getpixel((0, 0)) == (0, 0, 128)
    folder.close()
    archive = tmp_path / "book.cbz"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr(path.name, data)
    source = ZipImageSource(archive)
    try:
        assert source.list_images() == [path.name]
        assert source.probe_image_size(path.name) == (40, 60)
        with source.open_image(path.name) as image:
            assert image.getpixel((0, 0)) == (0, 0, 128)
    finally:
        source.close()
    spec = ThumbnailRenderSpec.from_settings(32, "square_1_1", "letterbox")
    result = BrowserThumbnailProvider._load_archive_result(archive, spec)
    assert result.image is not None


def test_embedded_thumbnail_precedes_merged_preview():
    data = document(thumbnail=True)
    with psd_decoder.decode_psd_thumbnail(data, minimum_long_edge=16) as image:
        assert image.size == (16, 24)
        assert image.getpixel((0, 0))[0] > 240
    with psd_decoder.decode_psd_thumbnail(data, minimum_long_edge=32) as image:
        assert image.size == (40, 60)
        assert image.getpixel((0, 0)) == (0, 0, 128)


def test_missing_preview_never_composites_layers():
    data = document(preview=False, thumbnail=True)
    with pytest.raises(psd_decoder.PsdDecodeError, match="merged preview"):
        psd_decoder.decode_psd_image(data)
    with psd_decoder.decode_psd_thumbnail(data, minimum_long_edge=16) as image:
        assert image.size == (16, 24)
    with pytest.raises(psd_decoder.PsdDecodeError):
        psd_decoder.decode_psd_thumbnail(data, minimum_long_edge=32)


@pytest.mark.parametrize("limit,decoder", [
    ("PSD_VIEWER_MAX_ALLOC_BYTES", psd_decoder.decode_psd_image),
    ("PSD_THUMBNAIL_MAX_ALLOC_BYTES", psd_decoder.decode_psd_thumbnail),
])
def test_allocation_guard_rejects_before_rendering(monkeypatch, limit, decoder):
    data = document()
    monkeypatch.setattr(psd_decoder, limit, 1)
    with pytest.raises(psd_decoder.PsdDecodeError) as failure:
        decoder(data)
    assert isinstance(failure.value.__cause__, ValueError)


@pytest.mark.parametrize("header", [b"", b"8BPS", b"invalid" * 6])
def test_invalid_header_and_document(header):
    assert psd_decoder.probe_psd_size_from_header(header) is None
    with pytest.raises(psd_decoder.PsdDecodeError):
        psd_decoder.decode_psd_image(header)


def test_external_archive_preview_and_thumbnail(tmp_path):
    data = document(version=2, thumbnail=True)
    entry = ArchiveEntry("日本語.psb", len(data), len(data), False, False,
                         original_path="日本語.psb")

    class Backend:
        def list_entries(self, path, **kwargs):
            return ArchiveListing(path, (entry,), "7z", False, False)

        def read_entry(self, path, image_id, **kwargs):
            return data

    path = tmp_path / "book.7z"
    path.write_bytes(b"fake archive")
    source = SevenZipImageSource(path, backend=Backend())
    try:
        assert source.list_images() == [entry.path]
        assert source.probe_image_size(entry.path) == (40, 60)
        with source.open_image(entry.path) as image:
            assert image.size == (40, 60)
        with source.open_thumbnail_image(entry.path, minimum_long_edge=16) as image:
            assert image.size == (16, 24)
    finally:
        source.close()


def test_browser_detail_reads_only_psb_header(tmp_path, monkeypatch):
    from app.browser_image_detail import _ProbeWorker

    path = tmp_path / "canvas.psb"
    path.write_bytes(document(version=2)[:26])
    monkeypatch.setattr(Image, "open", lambda *_a, **_k: pytest.fail("pixel decoder called"))
    worker = _ProbeWorker(str(path), 7)
    results = []
    worker.signals.completed.connect(results.append)
    worker.run()
    assert len(results) == 1
    assert results[0].dimensions == (40, 60)
    assert results[0].generation == 7
