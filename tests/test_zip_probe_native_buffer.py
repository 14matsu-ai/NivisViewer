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

    def checked_header(stream, **kwargs):
        inspected.append((kwargs.get('formats'), source._lock._is_owned()))
        return open_image(stream, **kwargs)

    def reject_python_device(*args, **kwargs):
        pytest.fail("ZIP geometry reintroduced Python QIODevice callbacks")

    monkeypatch.setattr(Image, 'open', checked_header)
    monkeypatch.setattr(Image.Image, 'load', reject_python_device)
    monkeypatch.setattr(image_source, "QImageReader", reject_python_device)
    monkeypatch.setattr(image_source, "_ZipEntrySequentialDevice", reject_python_device)
    try:
        assert source.probe_image_size(name) == (91, 137)
        assert inspected == [((format,), False)]
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
