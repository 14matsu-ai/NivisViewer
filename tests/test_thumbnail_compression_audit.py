"""Safe synthetic evidence for the oversized-cache investigation; no user files."""

from contextlib import contextmanager
from io import BytesIO
import random
import struct
from types import SimpleNamespace
from zipfile import ZipFile

from PIL import Image, features
import pytest

from app.archive_backend import ArchiveEntry, ArchiveListing
from app.browser_model import BrowserItem, BrowserItemKind
from app.file_preview import PreviewResult, PreviewSource
from app.preview_provider_registry import PreviewProviderRegistry
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec, pil_to_qimage, render_pil_thumbnail


def chunks(data):
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    result, offset = [], 12
    while offset + 8 <= len(data):
        kind, size = struct.unpack_from("<4sI", data, offset)
        result.append(kind.decode("ascii"))
        offset += 8 + size + size % 2
    return result


def item_for(path, kind=BrowserItemKind.IMAGE):
    stat = path.stat()
    return BrowserItem(path.name, path, kind, stat.st_mtime, stat.st_size, stat.st_mtime_ns)


def noise(size=(272, 384)):
    return Image.frombytes("RGB", size, random.Random(104448).randbytes(size[0] * size[1] * 3))


def test_272x384_lossy_quality_and_one_nonopaque_pixel(tmp_path):
    assert features.check("webp")
    source = tmp_path / "synthetic.png"
    source.write_bytes(b"fingerprint")
    opaque = noise().convert("RGBA")
    nearly_opaque = opaque.copy()
    nearly_opaque.putpixel((100, 100), (*nearly_opaque.getpixel((100, 100))[:3], 254))
    sizes = {}
    for label, pixels in (("opaque", opaque), ("one_alpha254", nearly_opaque)):
        encoded = []
        for quality in (1, 40, 60, 100):
            spec = ThumbnailRenderSpec(272, 384, "portrait_1_sqrt2", "smart_crop", encoder_quality=quality)
            cache = ThumbnailDiskCache(tmp_path / f"{label}-{quality}", encoder_quality=quality)
            try:
                assert cache.put(item_for(source), spec, pil_to_qimage(pixels))
                data = next(cache.files_dir.iterdir()).read_bytes()
                encoded.append(data)
                sizes[f"{label}-q{quality}"] = len(data)
                assert "VP8 " in chunks(data) and "VP8L" not in chunks(data)
                with Image.open(BytesIO(data)) as decoded:
                    assert decoded.convert("RGBA").getchannel("A").getextrema() == (255, 255)
            finally:
                cache.close()
        assert len(encoded[0]) < len(encoded[1]) < len(encoded[2]) < len(encoded[3])
    print("synthetic noise encoded bytes:", sizes)


@pytest.mark.parametrize("crop", ["letterbox", "center_crop", "smart_crop"])
@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_opaque_rendering_does_not_add_transparency_or_letterbox_padding(crop, mode):
    spec = ThumbnailRenderSpec(272, 384, "portrait_1_sqrt2", crop, encoder_quality=40)
    pixels = noise((800, 600)).convert(mode)
    rendered, _ = render_pil_thumbnail(pixels, spec)
    actual = ThumbnailDiskCache._qimage_to_pil(rendered)
    assert actual.getchannel("A").getextrema() == (255, 255)
    assert actual.size == ((272, 204) if crop == "letterbox" else (272, 384))


def test_hidden_rgb_and_lossless_exact_are_separate_from_lossy_quality():
    pixels = noise().convert("RGBA")
    pixels.putalpha(0)
    sizes = {}
    for exact in (False, True):
        output = BytesIO()
        pixels.save(output, format="WEBP", lossless=True, quality=90, method=4, exact=exact)
        sizes[exact] = len(output.getvalue())
    assert sizes[True] > 102400 and sizes[False] < 1024
    print("fully hidden synthetic RGB, exact false/true bytes:", sizes)


def test_separate_lossy_rgb_lossless_alpha_is_numerically_safe_for_alpha():
    pixels = noise().convert("RGBA")
    pixels.putpixel((100, 100), (*pixels.getpixel((100, 100))[:3], 254))
    output = BytesIO()
    pixels.save(output, format="WEBP", lossless=False, quality=40,
                alpha_quality=100, method=4, exact=True)
    assert "VP8 " in chunks(output.getvalue()) and "ALPH" in chunks(output.getvalue())
    with Image.open(BytesIO(output.getvalue())) as decoded:
        assert decoded.convert("RGBA").getchannel("A").tobytes() == pixels.getchannel("A").tobytes()
    print("synthetic proposal q40 RGB + exact alpha bytes:", len(output.getvalue()))


def test_legacy_integer_write_uses_active_disk_quality(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"fingerprint")
    pixels = pil_to_qimage(noise())
    cache = ThumbnailDiskCache(tmp_path / "cache", encoder_quality=40)
    try:
        for quality in (40, 70):
            cache.set_encoder_quality(quality)
            assert cache.put(item_for(source), 384, pixels)
            row = cache._connection.execute(
                "SELECT file_name FROM entries WHERE format_version=?", (cache.format_version,)
            ).fetchone()
            expected = BytesIO()
            cache._qimage_to_pil(pixels).save(expected, format="WEBP", quality=quality, method=4, exact=True)
            assert (cache.files_dir / row[0]).read_bytes() == expected.getvalue()
    finally:
        cache.close()


@pytest.mark.parametrize("source_kind", ["image", "folder", "zip", "external", "pdf", "text", "video", "shell", "empty_folder", "broken_zip"])
def test_shared_pipeline_encodes_configured_quality_for_all_preview_routes(tmp_path, qapp, monkeypatch, source_kind):
    pixels = noise((80, 120))
    png = BytesIO()
    pixels.save(png, format="PNG")
    spec = ThumbnailRenderSpec(272, 384, "portrait_1_sqrt2", "smart_crop", encoder_quality=40)
    path, kind = tmp_path / "fixture.png", BrowserItemKind.IMAGE
    path.write_bytes(png.getvalue())

    class Backend:
        def list_entries(self, archive_path, **kwargs):
            return ArchiveListing(archive_path, (ArchiveEntry("page.png", len(png.getvalue()), 0, False, False),), "7z", False, False)

        def read_entry(self, *args, **kwargs):
            return png.getvalue()

    class PdfSource:
        def __init__(self, *args, **kwargs): pass
        def list_images(self): return ["0"]
        @contextmanager
        def open_image_for_render(self, *args, **kwargs): yield pixels.copy()
        def close(self): pass

    class Shell:
        def request_thumbnail(self, path, received_spec, **kwargs):
            assert received_spec is spec
            return PreviewResult.ready_image(pil_to_qimage(pixels), source=PreviewSource.WINDOWS_SHELL, persist_to_disk=False)
        def shutdown(self): pass

    class Video:
        def generate(self, path, received_spec, cancel_token):
            assert received_spec is spec
            return PreviewResult.ready_image(pil_to_qimage(pixels), source=PreviewSource.FFMPEG, persist_to_disk=True)

    if source_kind in {"folder", "empty_folder"}:
        path, kind = tmp_path / source_kind, BrowserItemKind.FOLDER
        path.mkdir()
        if source_kind == "folder": (path / "page.png").write_bytes(png.getvalue())
    elif source_kind in {"zip", "broken_zip", "external"}:
        path, kind = tmp_path / ("book.rar" if source_kind == "external" else "book.zip"), BrowserItemKind.ARCHIVE
        if source_kind == "zip":
            with ZipFile(path, "w") as archive: archive.writestr("page.png", png.getvalue())
        else: path.write_bytes(b"fake or broken archive")
    elif source_kind == "pdf":
        path, kind = tmp_path / "book.pdf", BrowserItemKind.PDF
        path.write_bytes(b"fake PDF backend")
        monkeypatch.setattr("app.pdf_image_source.PdfImageSource", PdfSource)
    elif source_kind in {"text", "video", "shell"}:
        path = tmp_path / {"text": "note.txt", "video": "clip.mp4", "shell": "drawing.unknown"}[source_kind]
        kind = BrowserItemKind.OTHER
        path.write_text("Synthetic text preview only", encoding="utf-8")
    registry = PreviewProviderRegistry(settings={"video_thumbnail_backend": "ffmpeg", "video_thumbnail_shell_placeholder": False}, shell_service=Shell(), ffmpeg_backend=Video())
    cache = ThumbnailDiskCache(tmp_path / "cache", encoder_quality=40)
    provider = BrowserThumbnailProvider(disk_cache=cache, preview_registry=registry, archive_backend_registry=SimpleNamespace(backend_for_path=lambda path: Backend()), pdfium_service=object())
    try:
        result = provider._load_pipeline(item_for(path, kind), spec)
        if source_kind in {"shell", "empty_folder", "broken_zip"}:
            assert not list(cache.files_dir.iterdir())
            return
        assert result.image is not None and not result.image.isNull()
        row = cache._connection.execute("SELECT file_name, format_version, thumbnail_size FROM entries").fetchone()
        assert row[1] == "3-webp-q40-rgb-lossy-v1-matteffffff" and row[2] == spec.cache_token
        data = (cache.files_dir / row[0]).read_bytes()
        assert "VP8 " in chunks(data)
        expected = BytesIO()
        cache._qimage_to_pil(result.image).save(expected, format="WEBP", quality=40, method=4, exact=True)
        assert data == expected.getvalue()
    finally:
        provider.close()
