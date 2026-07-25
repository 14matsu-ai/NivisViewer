from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import QApplication

from app.archive_backend import ArchiveBackendError, ArchiveEntry, ArchiveErrorCode, ArchiveListing
from app.archive_backend_registry import ArchiveBackendRegistry
from app.browser_model import BrowserItem, BrowserItemKind
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider


def png_bytes(color: str) -> bytes:
    output = BytesIO()
    with Image.new("RGB", (20, 30), color) as image:
        image.save(output, format="PNG")
    return output.getvalue()


class ThumbnailBackend:
    def __init__(self, *, failure: ArchiveErrorCode | None = None) -> None:
        self.failure = failure
        self.list_calls = 0
        self.read_paths: list[str] = []

    def is_available(self) -> bool:
        return self.failure is not ArchiveErrorCode.BACKEND_NOT_FOUND

    def list_entries(self, archive_path: str, *, cancel_token=None) -> ArchiveListing:
        self.list_calls += 1
        if self.failure is not None:
            raise ArchiveBackendError(self.failure)
        return ArchiveListing(
            archive_path,
            (
                ArchiveEntry("10.png", 100, 50, False, False),
                ArchiveEntry("2.png", 100, 50, False, False),
            ),
            "7z",
            False,
            False,
        )

    def read_entry(self, archive_path, entry_path, *, cancel_token=None, maximum_bytes=None):
        self.read_paths.append(entry_path)
        return png_bytes("red")


def item_for(path: Path) -> BrowserItem:
    stat = path.stat()
    return BrowserItem(
        path.name,
        path,
        BrowserItemKind.ARCHIVE,
        stat.st_mtime,
        stat.st_size,
        stat.st_mtime_ns,
    )


def test_external_archive_thumbnail_extracts_only_naturally_first_image(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "book.cb7"
    archive.write_bytes(b"archive")
    backend = ThumbnailBackend()
    registry = ArchiveBackendRegistry(seven_zip_backend=backend)

    result = BrowserThumbnailProvider.load_thumbnail_result(
        item_for(archive),
        120,
        registry,
    )

    assert result.image is not None and not result.image.isNull()
    assert backend.list_calls == 1
    assert backend.read_paths == ["2.png"]


def test_external_thumbnail_failures_silently_fall_back(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "book.rar"
    archive.write_bytes(b"archive")
    for failure in (
        ArchiveErrorCode.CORRUPT_ARCHIVE,
        ArchiveErrorCode.PASSWORD_REQUIRED,
        ArchiveErrorCode.BACKEND_NOT_FOUND,
    ):
        registry = ArchiveBackendRegistry(
            seven_zip_backend=ThumbnailBackend(failure=failure)
        )
        result = BrowserThumbnailProvider.load_thumbnail_result(
            item_for(archive),
            100,
            registry,
        )
        assert result.image is None


def test_external_thumbnail_uses_existing_memory_and_disk_cache_layers(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "book.7z"
    archive.write_bytes(b"archive")
    backend = ThumbnailBackend()
    registry = ArchiveBackendRegistry(seven_zip_backend=backend)
    cache_dir = tmp_path / "cache"
    provider = BrowserThumbnailProvider(
        archive_backend_registry=registry,
        disk_cache=ThumbnailDiskCache(cache_dir, enabled=False),
        disk_cache_enabled=True,
    )
    generation = provider.begin_generation()
    item = item_for(archive)

    assert provider.request(item, 120, generation=generation)
    assert provider.wait_for_done(2000)
    qapp.processEvents()
    assert not provider.request(item, 120, generation=generation)
    qapp.processEvents()
    assert backend.list_calls == 1
    provider.close()

    second_backend = ThumbnailBackend()
    second = BrowserThumbnailProvider(
        archive_backend_registry=ArchiveBackendRegistry(
            seven_zip_backend=second_backend
        ),
        disk_cache=ThumbnailDiskCache(cache_dir, enabled=False),
        disk_cache_enabled=True,
    )
    generation = second.begin_generation()
    assert second.request(item, 120, generation=generation)
    assert second.wait_for_done(2000)
    qapp.processEvents()
    assert second_backend.list_calls == 0
    second.close()
