from io import BytesIO
import zipfile

import pytest
from PIL import Image

from app import image_source


@pytest.mark.parametrize("suffix,format", [(".jpg", "JPEG"), (".png", "PNG")])
def test_zip_geometry_reads_only_headers_outside_source_lock(tmp_path, monkeypatch, suffix, format):
    payload = BytesIO()
    with Image.new("RGB", (91, 137), "navy") as image:
        image.save(payload, format=format)
    archive = tmp_path / "book.zip"
    name = "page" + suffix
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr(name, payload.getvalue())
    source = image_source.ZipImageSource(archive)
    open_image = Image.open
    inspected = []
    scanned = []
    scan_geometry = image_source._jpeg_buffer_geometry

    def checked_header(stream, **kwargs):
        inspected.append((kwargs.get('formats'), source._lock._is_owned()))
        return open_image(stream, **kwargs)

    def reject_python_device(*args, **kwargs):
        pytest.fail("ZIP geometry reintroduced Python QIODevice callbacks")

    def checked_geometry(data):
        scanned.append(source._lock._is_owned())
        return scan_geometry(data)

    monkeypatch.setattr(Image, 'open', checked_header)
    monkeypatch.setattr(Image.Image, 'load', reject_python_device)
    monkeypatch.setattr(image_source, "QImageReader", reject_python_device)
    monkeypatch.setattr(image_source, "_ZipEntrySequentialDevice", reject_python_device)
    monkeypatch.setattr(image_source, '_jpeg_buffer_geometry', checked_geometry)
    try:
        assert source.probe_image_size(name) == (91, 137)
        if format == 'JPEG':
            assert scanned == [False]
            assert inspected == []
        else:
            assert inspected == [((format,), False)]
            assert scanned == []
    finally:
        source.close()


@pytest.mark.parametrize('orientation', range(1, 9))
@pytest.mark.parametrize('progressive', [False, True])
@pytest.mark.parametrize('segments', [0, 20])
def test_jpeg_app_segments_keep_native_decode(tmp_path, monkeypatch, orientation, progressive, segments):
    payload = BytesIO()
    exif = Image.Exif()
    exif[274] = orientation
    with Image.new('RGB', (1200, 800), 'navy') as image:
        image.save(payload, 'JPEG', exif=exif, progressive=progressive)
    jpeg = payload.getvalue()
    segment = b'\xff\xef' + (65535).to_bytes(2, 'big') + b'x' * 65533
    jpeg = jpeg[:2] + segment * segments + jpeg[2:]
    archive = tmp_path / '付加情報.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        output.writestr('画像.jpg', jpeg)
    source = image_source.ZipImageSource(archive)
    original_open = source._zip.open
    opened = []
    def counted_open(*args, **kwargs):
        opened.append(args[0])
        return original_open(*args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail('Geometry must not reopen pixels or enter Qt metadata')
    monkeypatch.setattr(source._zip, 'open', counted_open)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(image_source.QImageReader, 'size', forbidden)
    monkeypatch.setattr(image_source.QImageReader, 'transformation', forbidden)
    try:
        decoded = source.open_compatible_jpeg_at_most('画像.jpg', (300, 300))
        assert decoded is not None
        assert decoded.full_payload_materializations == 1
        assert decoded.bytes_read == len(jpeg)
        assert len(opened) == 1
        swapped = orientation in {5, 6, 7, 8}
        assert decoded.original_size == ((800, 1200) if swapped else (1200, 800))
        assert decoded.qimage.size().toTuple() == ((200, 300) if swapped else (300, 200))
    finally:
        source.close()


@pytest.mark.parametrize('data', [b'', b'\xff\xd8', b'\xff\xd8\xff',
    b'\xff\xd8\xff\xe1\0\1', b'\xff\xd8\xff\xe1\xff\xff',
    b'\xff\xd8\xff\xc0\0\4xx', b'\xff\xd8\xff\0xx',
    b'\xff\xd8\xff\xff'])
def test_truncated_jpeg_geometry_is_declined(data):
    from PySide6.QtCore import QByteArray
    assert image_source._jpeg_buffer_geometry(QByteArray(data)) is None


def test_large_jpeg_probe_releases_views_between_bounded_reads(tmp_path, monkeypatch):
    payload = BytesIO()
    with Image.new('RGB', (32, 48), 'navy') as image:
        image.save(payload, 'JPEG')
    jpeg = payload.getvalue()
    segment = b'\xff\xef' + (65535).to_bytes(2, 'big') + b'x' * 65533
    archive = tmp_path / 'large-header.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        output.writestr('page.jpg', jpeg[:2] + segment * 20 + jpeg[2:])
    source = image_source.ZipImageSource(archive)
    scan = image_source._jpeg_buffer_geometry
    inspected = []
    def checked(data):
        assert not source._lock._is_owned()
        inspected.append(len(data))
        return scan(data)
    monkeypatch.setattr(image_source, '_jpeg_buffer_geometry', checked)
    try:
        assert source.probe_image_size('page.jpg') is None
        assert inspected == [64 * 1024, 256 * 1024, 1024 * 1024]
    finally:
        source.close()


@pytest.mark.parametrize('orientation', range(1, 9))
@pytest.mark.parametrize('kind', ['zip', 'folder'])
def test_metadata_keeps_jpeg_exif_axes_without_qt_or_pixels(tmp_path, monkeypatch, orientation, kind):
    payload = BytesIO()
    exif = Image.Exif()
    exif[274] = orientation
    with Image.new('RGB', (91, 137), 'navy') as image:
        image.save(payload, 'JPEG', exif=exif)
    if kind == 'zip':
        path = tmp_path / 'book.zip'
        with zipfile.ZipFile(path, 'w') as output:
            output.writestr('page.jpg', payload.getvalue())
        source = image_source.ZipImageSource(path)
        name = 'page.jpg'
    else:
        path = tmp_path / 'page.jpg'
        path.write_bytes(payload.getvalue())
        source = image_source.FolderImageSource(tmp_path)
        name = str(path)
    def forbidden(*args, **kwargs):
        pytest.fail('Geometry must not enter Qt image handlers or decode pixels')
    monkeypatch.setattr(image_source, 'QImageReader', forbidden)
    monkeypatch.setattr(Image.Image, 'load', forbidden)
    try:
        assert source.probe_image_size(name) == ((137, 91) if orientation >= 5 else (91, 137))
    finally:
        source.close()


def test_native_jpeg_pixels_do_not_reenter_qt_metadata(tmp_path, monkeypatch):
    payload = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    with Image.new('RGB', (91, 137), 'navy') as image:
        image.save(payload, 'JPEG', exif=exif)
    archive = tmp_path / 'book.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('page.jpg', payload.getvalue())
    def forbidden(*args, **kwargs):
        pytest.fail('Native pixel reads must not invoke Qt metadata entry points')
    monkeypatch.setattr(image_source.QImageReader, 'size', forbidden)
    monkeypatch.setattr(image_source.QImageReader, 'transformation', forbidden)
    source = image_source.ZipImageSource(archive)
    try:
        decoded = source.open_compatible_jpeg_at_most('page.jpg', (None, None))
        assert decoded.original_size == (137, 91)
        assert decoded.qimage.size().toTuple() == (137, 91)
    finally:
        source.close()
