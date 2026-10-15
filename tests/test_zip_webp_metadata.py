from io import BytesIO
import zipfile

from PIL import Image, WebPImagePlugin
import pytest

from app import image_source
from app.webp_metadata import probe_webp_size


def archive_source(tmp_path, payload, name="画像.webp"):
    path = tmp_path / "book.cbz"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, payload)
    return image_source.ZipImageSource(path), name


@pytest.mark.parametrize("orientation", range(1, 9))
@pytest.mark.parametrize("lossless", [False, True])
@pytest.mark.parametrize("size", [(128, 96), (96, 128)])
def test_zip_webp_geometry_preserves_exif_without_qt_or_pixel_decode(
    tmp_path, monkeypatch, orientation, lossless, size,
):
    payload = BytesIO()
    with Image.new("RGBA", size, (30, 60, 90, 120)) as image:
        image.getexif()[274] = orientation
        image.save(payload, "WEBP", lossless=lossless, exif=image.getexif())
    source, name = archive_source(tmp_path, payload.getvalue())
    reads = []
    original_read = zipfile.ZipExtFile.read
    def read(entry, count=-1):
        reads.append(count)
        return original_read(entry, count)
    def forbidden(*args, **kwargs):
        pytest.fail("ZIP WebP size probing must not enter an image decoder")
    monkeypatch.setattr(image_source, "QImageReader", forbidden)
    monkeypatch.setattr(Image, "open", forbidden)
    monkeypatch.setattr(WebPImagePlugin.WebPImageFile, "load", forbidden)
    monkeypatch.setattr(zipfile.ZipExtFile, "read", read)
    try:
        expected = size[::-1] if orientation in {5, 6, 7, 8} else size
        assert source.probe_image_size(name) == expected
        count = len(reads)
        assert source.probe_image_size(name) == expected
        assert len(reads) == count
        assert all(count >= 0 for count in reads)
        assert sum(reads) <= 1024 * 1024
        assert not source._active_requests
    finally:
        source.close()


@pytest.mark.parametrize("lossless", [False, True])
@pytest.mark.parametrize("name", ["画像.webp", "mislabelled.png"])
def test_simple_webp_dimensions_and_magic_detection(tmp_path, lossless, name):
    payload = BytesIO()
    with Image.new("RGB", (113, 157), "navy") as image:
        image.save(payload, "WEBP", lossless=lossless)
    source, name = archive_source(tmp_path, payload.getvalue(), name)
    try:
        assert source.probe_image_size(name) == (113, 157)
    finally:
        source.close()


def test_animated_webp_uses_canvas_size_without_allocating_frames(tmp_path, monkeypatch):
    payload = BytesIO()
    with Image.new("RGB", (127, 91), "navy") as first, Image.new("RGB", (127, 91), "red") as second:
        first.save(payload, "WEBP", save_all=True, append_images=[second], duration=50)
    source, name = archive_source(tmp_path, payload.getvalue())
    monkeypatch.setattr(image_source, "QImageReader", lambda *_: pytest.fail("No Qt metadata reader"))
    try:
        assert source.probe_image_size(name) == (127, 91)
    finally:
        source.close()


def chunk(kind, data):
    return kind + len(data).to_bytes(4, "little") + data + (b"\0" if len(data) & 1 else b"")


def riff(chunks):
    return b"RIFF" + (len(chunks) + 4).to_bytes(4, "little") + b"WEBP" + chunks


@pytest.mark.parametrize("exif_present", [False, True])
def test_large_payload_probe_stays_bounded_and_never_guesses_late_exif(tmp_path, monkeypatch, exif_present):
    flags = 0x08 if exif_present else 0
    header = bytes([flags, 0, 0, 0]) + (126).to_bytes(3, "little") + (90).to_bytes(3, "little")
    exif = Image.Exif()
    exif[274] = 6
    payload = riff(chunk(b"VP8X", header) + chunk(b"VP8 ", b"\0" * (2 * 1024 * 1024))
                   + (chunk(b"EXIF", exif.tobytes()) if exif_present else b""))
    source, name = archive_source(tmp_path, payload)
    reads = []
    original = zipfile.ZipExtFile.read
    def read(entry, count=-1):
        assert 0 <= count <= 1024 * 1024
        result = original(entry, count)
        reads.append(len(result))
        return result
    monkeypatch.setattr(zipfile.ZipExtFile, "read", read)
    monkeypatch.setattr(image_source, "QImageReader", lambda *_: pytest.fail("No Qt fallback"))
    try:
        assert source.probe_image_size(name) == (None if exif_present else (127, 91))
        assert sum(reads) <= (1024 * 1024 if exif_present else 64 * 1024)
        assert (name in source._size_cache) is not exif_present
        assert not source._active_requests
    finally:
        source.close()


@pytest.mark.parametrize("payload", [b"", b"not webp", riff(chunk(b"VP8L", b"\x2f\0\0\0\xe0")),
    riff(chunk(b"VP8 ", b"\0" * 10)), riff(chunk(b"VP8X", b"\xc1" + b"\0" * 9))])
def test_invalid_webp_is_unknown_without_qt_fallback(tmp_path, monkeypatch, payload):
    source, name = archive_source(tmp_path, payload)
    monkeypatch.setattr(image_source, "QImageReader", lambda *_: pytest.fail("No Qt fallback"))
    try:
        assert source.probe_image_size(name) is None
        assert name not in source._size_cache
        assert not source._active_requests
    finally:
        source.close()


def test_exif_chunk_padding_and_riff_bounds():
    exif = Image.Exif()
    exif[274] = 8
    header = b"\x08\0\0\0" + (126).to_bytes(3, "little") + (90).to_bytes(3, "little")
    data = riff(chunk(b"VP8X", header) + chunk(b"JUNK", b"abc") + chunk(b"EXIF", exif.tobytes()))
    assert probe_webp_size(data, file_size=len(data)) == (91, 127)
    assert probe_webp_size(data[:-1], file_size=len(data)) is None
    assert probe_webp_size(data, file_size=len(data) - 2) is None


@pytest.mark.parametrize("payload", [b"", b"Exif\0\0", b"not TIFF"])
def test_invalid_exif_does_not_guess_unrotated_geometry(payload):
    header = b"\x08\0\0\0" + (126).to_bytes(3, "little") + (90).to_bytes(3, "little")
    data = riff(chunk(b"VP8X", header) + chunk(b"EXIF", payload))
    assert probe_webp_size(data, file_size=len(data)) is None


def test_cancellation_during_zip_webp_header_read_releases_request(tmp_path, monkeypatch):
    payload = BytesIO()
    with Image.new("RGB", (128, 96), "navy") as image:
        image.save(payload, "WEBP")
    source, name = archive_source(tmp_path, payload.getvalue())
    original = zipfile.ZipExtFile.read
    def read(entry, count=-1):
        result = original(entry, count)
        for events in source._active_requests.values():
            for event in events:
                event.set()
        return result
    monkeypatch.setattr(zipfile.ZipExtFile, "read", read)
    try:
        with pytest.raises(image_source.ImageSourceError):
            source.probe_image_size(name)
        assert not source._active_requests
        assert name not in source._size_cache
    finally:
        source.close()
