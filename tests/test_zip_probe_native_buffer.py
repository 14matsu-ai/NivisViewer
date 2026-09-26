from io import BytesIO
import zipfile

import pytest
from PIL import Image
from PySide6.QtCore import QBuffer

from app import image_source


@pytest.mark.parametrize("suffix,format", [(".jpg", "JPEG"), (".png", "PNG")])
def test_zip_geometry_uses_native_buffer_outside_source_lock(tmp_path, monkeypatch, suffix, format):
    payload = BytesIO()
    with Image.new("RGB", (91, 137), "navy") as image:
        image.save(payload, format=format)
    archive = tmp_path / "book.zip"
    name = "page" + suffix
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr(name, payload.getvalue())
    source = image_source.ZipImageSource(archive)
    reader = image_source.QImageReader
    inspected = []

    def checked_reader(device):
        inspected.append((isinstance(device, QBuffer), source._lock._is_owned()))
        return reader(device)

    def reject_python_device(*args, **kwargs):
        pytest.fail("ZIP geometry reintroduced Python QIODevice callbacks")

    monkeypatch.setattr(image_source, "QImageReader", checked_reader)
    monkeypatch.setattr(image_source, "_ZipEntrySequentialDevice", reject_python_device)
    try:
        assert source.probe_image_size(name) == (91, 137)
        assert inspected == [(True, False)]
    finally:
        source.close()
