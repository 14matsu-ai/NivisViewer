from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace
import zipfile

import pytest
from PIL import Image, features
from PySide6.QtTest import QTest

from app.browser_model import BrowserItemDiscovery, BrowserItemKind
from app.archive_backend import ArchiveEntry, ArchiveListing
from app.config_manager import ConfigManager
from app.image_source import create_image_source, ImageSourceError
from app.supported_formats import IMAGE_EXTENSIONS, normalize_extensions
from app.thumbnail_provider import BrowserThumbnailProvider
from app.viewer_window import ViewerWindow


def make_book(tmp_path, kind, format_name="AVIF"):
    assert features.check("avif"), "Existing Pillow must provide AVIF"
    folder = tmp_path / "日本語"
    folder.mkdir()
    path = folder / ("画像." + format_name)
    with Image.new("RGB", (48, 64), "red") as image:
        image.save(path, format=format_name)
    if kind == "zip":
        archive = tmp_path / "書庫.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.write(path, "日本語/" + path.name)
        return archive, path
    return (folder if kind == "folder" else path), path


@pytest.mark.parametrize("kind", ["image", "folder", "zip"])
@pytest.mark.parametrize("format_name", ["AVIF", "JXL", "PNG", "JPEG"])
def test_format_source_and_browser_thumbnail(tmp_path, kind, format_name):
    target, path = make_book(tmp_path, kind, format_name)
    assert "." + format_name.lower() in IMAGE_EXTENSIONS
    assert normalize_extensions([format_name]) == ("." + format_name.lower(),)
    source, selected = create_image_source(target)
    try:
        pages = source.list_images()
        assert len(pages) == 1 and pages[0].endswith("." + format_name)
        if kind == "image":
            assert selected == str(path)
        with ThreadPoolExecutor(max_workers=1) as worker:
            decoded = worker.submit(source.open_image, pages[0]).result(timeout=5)
            with decoded:
                assert decoded.size == (48, 64)
                assert decoded.convert("RGB").getpixel((0, 0))[0] > 200
            item = next(item for item in BrowserItemDiscovery().discover(target.parent).items
                        if item.path == target)
            if kind == "image":
                assert item.kind is BrowserItemKind.IMAGE
            thumbnail = worker.submit(BrowserThumbnailProvider.load_thumbnail, item, 80).result(timeout=5)
            assert thumbnail is not None and not thumbnail.isNull()
            assert thumbnail.pixelColor(thumbnail.width() // 2, thumbnail.height() // 2).red() > 200
    finally:
        source.close()


@pytest.mark.parametrize("kind", ["image", "folder", "zip"])
@pytest.mark.parametrize("format_name", ["AVIF", "JXL"])
def test_format_reaches_viewer_pixels(tmp_path, qapp, monkeypatch, kind, format_name):
    target, _ = make_book(tmp_path, kind, format_name)
    if format_name == "JXL":
        from pillow_jxl.JpegXLImagePlugin import JXLImageFile
        gui_thread = get_ident()
        original = JXLImageFile._open
        def guarded_open(self):
            assert get_ident() != gui_thread, "JXL decoded on the GUI thread"
            return original(self)
        monkeypatch.setattr(JXLImageFile, "_open", guarded_open)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        window.show_initial()
        window.open_path(target)
        for _ in range(300):
            qapp.processEvents()
            if window.viewer._images:
                break
            QTest.qWait(10)
        assert window.viewer._images
        pixels = window.viewer._images[0].pixmap.toImage()
        assert pixels.pixelColor(pixels.width() // 2, pixels.height() // 2).red() > 200
    finally:
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize("format_name", ["AVIF", "JXL"])
@pytest.mark.parametrize("kind", ["image", "zip"])
def test_corrupt_format_reports_decode_failure(tmp_path, format_name, kind):
    path = tmp_path / ("壊れた." + format_name)
    path.write_bytes(b"\xff\x0a" if format_name == "JXL" else b"invalid avif")
    target = path
    if kind == "zip":
        target = tmp_path / "corrupt.zip"
        with zipfile.ZipFile(target, "w") as output:
            output.write(path, path.name)
    source, _ = create_image_source(target)
    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            with pytest.raises(ImageSourceError):
                worker.submit(source.open_image, source.list_images()[0]).result(timeout=5)
            item = next(item for item in BrowserItemDiscovery().discover(tmp_path).items
                        if item.path == target)
            assert worker.submit(BrowserThumbnailProvider.load_thumbnail, item, 80).result(timeout=5) is None
    finally:
        source.close()


@pytest.mark.parametrize("format_name", ["AVIF", "JXL"])
@pytest.mark.parametrize("suffix", [".rar", ".7z"])
def test_external_archive_formats_with_fake_extraction(tmp_path, format_name, suffix):
    _, image = make_book(tmp_path, "image", format_name)
    payload = image.read_bytes()
    archive = tmp_path / ("書庫" + suffix)
    archive.write_bytes(b"fake archive")
    entry_name = "日本語/" + image.name
    class Backend:
        def list_entries(self, path, **kwargs):
            return ArchiveListing(str(path), (
                ArchiveEntry(entry_name, len(payload), None, False, False),
            ), suffix[1:], None, False)
        def read_entry(self, path, entry, **kwargs):
            assert entry == entry_name
            return payload
    registry = SimpleNamespace(backend_for_path=lambda path: Backend())
    source, _ = create_image_source(archive, archive_backend_registry=registry)
    try:
        assert source.list_images() == [entry_name]
        with ThreadPoolExecutor(max_workers=1) as worker:
            decoded = worker.submit(source.open_image, entry_name).result(timeout=5)
            with decoded:
                assert decoded.size == (48, 64)
            item = next(item for item in BrowserItemDiscovery().discover(tmp_path).items if item.path == archive)
            result = worker.submit(
                BrowserThumbnailProvider.load_thumbnail_result, item, 80,
                archive_backend_registry=registry,
            ).result(timeout=5)
            assert result.image is not None and not result.image.isNull()
    finally:
        source.close()
