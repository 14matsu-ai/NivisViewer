from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from threading import Event

from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from app.browser_model import BrowserItem, BrowserItemKind
from app.file_preview import PreviewResultKind
from app.thumbnail_provider import BrowserThumbnailProvider
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_render import ThumbnailRenderSpec


def write_image(path: Path, *, color: str = "white") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (24, 32), color) as image:
        image.save(path)


def make_item(path: Path, kind: BrowserItemKind) -> BrowserItem:
    return BrowserItem(path.name, path, kind, path.stat().st_mtime)


def test_loads_image_folder_and_archive_thumbnails_with_unicode_paths(
    tmp_path: Path,
) -> None:
    image = tmp_path / "日本語画像.jpg"
    write_image(image)
    folder = tmp_path / "日本語フォルダ"
    write_image(folder / "2.jpg", color="blue")
    write_image(folder / "1.jpg", color="red")
    archive = tmp_path / "日本語書庫.cbz"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(folder / "2.jpg", "10.jpg")
        output.write(folder / "1.jpg", "2.jpg")

    image_thumbnail = BrowserThumbnailProvider.load_thumbnail(
        make_item(image, BrowserItemKind.IMAGE), 120
    )
    folder_thumbnail = BrowserThumbnailProvider.load_thumbnail(
        make_item(folder, BrowserItemKind.FOLDER), 120
    )
    archive_thumbnail = BrowserThumbnailProvider.load_thumbnail(
        make_item(archive, BrowserItemKind.ARCHIVE), 120
    )

    assert image_thumbnail is not None and not image_thumbnail.isNull()
    assert folder_thumbnail is not None and not folder_thumbnail.isNull()
    assert archive_thumbnail is not None and not archive_thumbnail.isNull()
    assert image_thumbnail.width() <= 120
    assert folder_thumbnail.height() <= 120
    assert folder_thumbnail.pixelColor(0, 0).red() > folder_thumbnail.pixelColor(0, 0).blue()
    assert archive_thumbnail.pixelColor(0, 0).red() > archive_thumbnail.pixelColor(0, 0).blue()


def test_center_crop_display_spec_uses_common_webp_folder_and_archive_paths(
    tmp_path: Path,
) -> None:
    image = tmp_path / "日本語の横長.webp"
    image.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (120, 40), "green") as source:
        source.save(image, "WEBP")
    folder = tmp_path / "表紙フォルダ"
    folder.mkdir()
    folder_cover = folder / "表紙.webp"
    with Image.new("RGB", (40, 120), "blue") as source:
        source.save(folder_cover, "WEBP")
    archive = tmp_path / "日本語書庫.cbz"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(image, "画像/表紙.webp")
    spec = ThumbnailRenderSpec.from_settings(
        120,
        "square_1_1",
        "letterbox",
        browser_display_mode="center_crop",
    )

    results = (
        BrowserThumbnailProvider.load_thumbnail(
            make_item(image, BrowserItemKind.IMAGE),
            spec,
        ),
        BrowserThumbnailProvider.load_thumbnail(
            make_item(folder, BrowserItemKind.FOLDER),
            spec,
        ),
        BrowserThumbnailProvider.load_thumbnail(
            make_item(archive, BrowserItemKind.ARCHIVE),
            spec,
        ),
    )

    assert all(result is not None and not result.isNull() for result in results)


def test_corrupt_image_and_archive_fall_back_to_none(tmp_path: Path) -> None:
    image = tmp_path / "broken.jpg"
    image.write_bytes(b"not an image")
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip")

    assert (
        BrowserThumbnailProvider.load_thumbnail(
            make_item(image, BrowserItemKind.IMAGE), 100
        )
        is None
    )
    assert (
        BrowserThumbnailProvider.load_thumbnail(
            make_item(archive, BrowserItemKind.ARCHIVE), 100
        )
        is None
    )


def test_duplicate_requests_are_suppressed_and_old_generation_is_discarded(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image_path = tmp_path / "page.jpg"
    write_image(image_path)
    item = make_item(image_path, BrowserItemKind.IMAGE)
    delivered: list[tuple[str, int]] = []

    def loader(_item: BrowserItem, _size: int) -> QImage:
        return QImage(8, 8, QImage.Format.Format_RGBA8888)

    provider = BrowserThumbnailProvider(loader=loader, max_workers=1)
    provider.thumbnail_ready.connect(
        lambda path, generation, _image: delivered.append((path, generation))
    )
    first_generation = provider.begin_generation()

    assert provider.request(item, 100, generation=first_generation)
    assert not provider.request(item, 100, generation=first_generation)
    provider.begin_generation()
    assert provider.wait_for_done(2000)
    qapp.processEvents()

    assert delivered == []
    provider.close()


def test_memory_cache_is_checked_before_decoder(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image_path = tmp_path / "page.jpg"
    write_image(image_path)
    item = make_item(image_path, BrowserItemKind.IMAGE)
    decoder_calls: list[bool] = []

    def loader(_item: BrowserItem, _size: int) -> QImage:
        decoder_calls.append(True)
        return QImage(12, 12, QImage.Format.Format_RGBA8888)

    provider = BrowserThumbnailProvider(loader=loader)
    generation = provider.begin_generation()
    assert provider.request(item, 120, generation=generation)
    assert provider.wait_for_done(2000)
    qapp.processEvents()

    assert not provider.request(item, 120, generation=generation)
    qapp.processEvents()
    assert decoder_calls == [True]
    provider.close()


def test_memory_cache_hit_uses_cow_image_handles_without_sharing_mutation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image_path = tmp_path / "page.jpg"
    write_image(image_path)
    item = make_item(image_path, BrowserItemKind.IMAGE)
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    delivered: list[QImage] = []
    provider.thumbnail_ready.connect(
        lambda _path, _generation, image: delivered.append(image)
    )
    generation = provider.begin_generation()
    source = QImage(12, 12, QImage.Format.Format_RGB32)
    source.fill(0xFFFF0000)

    provider._on_finished(
        str(item.path),
        generation,
        120,
        item.modified_at,
        source,
    )
    cache_key = (provider._path_key(item.path), 120, item.modified_at)
    cached = provider._cache[cache_key]
    assert cached is not source
    assert cached.cacheKey() == source.cacheKey()

    source.fill(0xFF0000FF)
    assert cached.pixelColor(0, 0).red() == 255
    assert cached.pixelColor(0, 0).blue() == 0

    delivered.clear()
    assert not provider.request(item, 120, generation=generation)
    qapp.processEvents()
    assert len(delivered) == 1
    cache_hit = delivered[0]
    assert cache_hit is not cached
    assert cache_hit.cacheKey() == cached.cacheKey()

    cache_hit.fill(0xFF00FF00)
    assert cached.pixelColor(0, 0).red() == 255
    assert cached.pixelColor(0, 0).green() == 0
    provider.close()


def test_disk_hit_skips_decoder_and_miss_is_persisted(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image_path = tmp_path / "日本語.jpg"
    write_image(image_path)
    item = make_item(image_path, BrowserItemKind.IMAGE)
    cache_path = tmp_path / "cache"
    disk_cache = ThumbnailDiskCache(cache_path)
    assert disk_cache.put(
        item,
        120,
        QImage(12, 12, QImage.Format.Format_RGBA8888),
    )
    disk_cache.close()
    decoder_calls: list[bool] = []

    def loader(_item: BrowserItem, _size: int) -> QImage:
        decoder_calls.append(True)
        return QImage(8, 8, QImage.Format.Format_RGBA8888)

    provider = BrowserThumbnailProvider(
        loader=loader,
        disk_cache=ThumbnailDiskCache(cache_path, enabled=False),
        disk_cache_enabled=True,
    )
    delivered: list[QImage] = []
    provider.thumbnail_ready.connect(
        lambda _path, _generation, image: delivered.append(image)
    )
    generation = provider.begin_generation()
    assert provider.request(item, 120, generation=generation)
    assert provider.wait_for_done(2000)
    qapp.processEvents()

    assert decoder_calls == []
    assert delivered and not delivered[0].isNull()
    provider.close()

    other_size_calls: list[bool] = []

    def other_loader(_item: BrowserItem, _size: int) -> QImage:
        other_size_calls.append(True)
        return QImage(10, 10, QImage.Format.Format_RGBA8888)

    miss_provider = BrowserThumbnailProvider(
        loader=other_loader,
        disk_cache=ThumbnailDiskCache(cache_path, enabled=False),
        disk_cache_enabled=True,
    )
    generation = miss_provider.begin_generation()
    assert miss_provider.request(item, 160, generation=generation)
    assert miss_provider.wait_for_done(2000)
    qapp.processEvents()
    assert other_size_calls == [True]
    miss_provider.close()

    persisted = ThumbnailDiskCache(cache_path)
    assert persisted.get(item, 160) is not None
    persisted.close()


def test_disabled_disk_cache_is_not_accessed(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image_path = tmp_path / "page.jpg"
    write_image(image_path)
    item = make_item(image_path, BrowserItemKind.IMAGE)

    class FakeDiskCache:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def set_enabled(self, _enabled: bool) -> None:
            self.calls.append("set_enabled")

        def get(self, _item, _size):
            self.calls.append("get")
            return None

        def put(self, _item, _size, _image, **_kwargs):
            self.calls.append("put")
            return True

        def close(self) -> None:
            pass

    fake = FakeDiskCache()
    provider = BrowserThumbnailProvider(
        loader=lambda _item, _size: QImage(
            8, 8, QImage.Format.Format_RGBA8888
        ),
        disk_cache=fake,  # type: ignore[arg-type]
        disk_cache_enabled=False,
    )
    generation = provider.begin_generation()
    assert provider.request(item, 100, generation=generation)
    assert provider.wait_for_done(2000)
    qapp.processEvents()

    assert fake.calls == []
    provider.close()


def test_failed_thumbnail_is_not_retried_each_generation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image_path = tmp_path / "broken.jpg"
    image_path.write_bytes(b"broken")
    item = make_item(image_path, BrowserItemKind.IMAGE)
    calls: list[bool] = []

    def failing_loader(_item: BrowserItem, _size: int):
        calls.append(True)
        return None

    provider = BrowserThumbnailProvider(loader=failing_loader)
    first = provider.begin_generation()
    assert provider.request(item, 100, generation=first)
    assert provider.wait_for_done(2000)
    qapp.processEvents()
    second = provider.begin_generation()
    assert provider.request(item, 100, generation=second)
    assert provider.wait_for_done(2000)
    qapp.processEvents()

    assert calls == [True]
    provider.close()


def test_pending_prefetch_can_be_cancelled_while_visible_work_runs(
    tmp_path: Path,
) -> None:
    visible_path = tmp_path / "visible.jpg"
    prefetch_path = tmp_path / "prefetch.jpg"
    write_image(visible_path)
    write_image(prefetch_path)
    started = Event()
    release = Event()

    def loader(item: BrowserItem, _size: int) -> QImage:
        if item.path == visible_path:
            started.set()
            release.wait(2)
        return QImage(8, 8, QImage.Format.Format_RGBA8888)

    provider = BrowserThumbnailProvider(loader=loader, max_workers=1)
    generation = provider.begin_generation()
    assert provider.request(
        make_item(visible_path, BrowserItemKind.IMAGE),
        100,
        generation=generation,
        priority=ThumbnailPriority.VISIBLE,
    )
    assert started.wait(1)
    assert provider.request(
        make_item(prefetch_path, BrowserItemKind.IMAGE),
        100,
        generation=generation,
        priority=ThumbnailPriority.PREFETCH,
    )

    assert provider.cancel_prefetch_except(
        {str(visible_path)},
        size=100,
        generation=generation,
    ) == 1
    assert provider.pending_count == 1
    release.set()
    assert provider.wait_for_done(2000)
    provider.close()


def test_loader_exception_finishes_once_clears_pending_and_can_retry(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
    caplog,
) -> None:
    image_path = tmp_path / "worker-error.jpg"
    write_image(image_path)
    item = make_item(image_path, BrowserItemKind.IMAGE)
    calls = 0

    def loader(_item: BrowserItem, _size: int) -> QImage:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("thumbnail loader failed")
        return QImage(8, 8, QImage.Format.Format_RGBA8888)

    provider = BrowserThumbnailProvider(loader=loader, max_workers=1)
    finished_calls: list[tuple[object, ...]] = []
    original_finished = provider._on_finished

    def record_finished(*args) -> None:
        finished_calls.append(args)
        original_finished(*args)

    monkeypatch.setattr(provider, "_on_finished", record_finished)
    failures = QSignalSpy(provider.thumbnail_failed)
    states = QSignalSpy(provider.preview_state_changed)
    ready = QSignalSpy(provider.thumbnail_ready)
    generation = provider.begin_generation()

    with caplog.at_level(logging.ERROR, logger="nivisviewer.thumbnail"):
        assert provider.request(item, 100, generation=generation)
        assert provider.wait_for_done(2000)
        qapp.processEvents()

    assert len(finished_calls) == 1
    assert provider.pending_count == 0
    assert failures.count() == 1
    assert states.count() == 1
    assert states.at(0)[2] == PreviewResultKind.FAILED.value
    assert ready.count() == 0
    worker_errors = [
        record
        for record in caplog.records
        if record.name == "nivisviewer.thumbnail"
        and "thumbnail worker failed" in record.getMessage()
    ]
    assert len(worker_errors) == 1
    assert worker_errors[0].exc_info is not None

    assert provider.request(item, 100, generation=generation)
    assert provider.wait_for_done(2000)
    qapp.processEvents()

    assert calls == 2
    assert len(finished_calls) == 2
    assert provider.pending_count == 0
    assert failures.count() == 1
    assert states.count() == 2
    assert states.at(1)[2] == PreviewResultKind.READY.value
    assert ready.count() == 1
    provider.close()


def test_loader_exception_from_stale_generation_is_not_applied(
    tmp_path: Path,
    qapp: QApplication,
    caplog,
) -> None:
    image_path = tmp_path / "stale-error.jpg"
    write_image(image_path)
    item = make_item(image_path, BrowserItemKind.IMAGE)
    started = Event()
    release = Event()

    def loader(_item: BrowserItem, _size: int) -> QImage:
        started.set()
        release.wait(2)
        raise RuntimeError("stale thumbnail failed")

    provider = BrowserThumbnailProvider(loader=loader, max_workers=1)
    failures = QSignalSpy(provider.thumbnail_failed)
    states = QSignalSpy(provider.preview_state_changed)
    ready = QSignalSpy(provider.thumbnail_ready)
    generation = provider.begin_generation()

    with caplog.at_level(logging.ERROR, logger="nivisviewer.thumbnail"):
        assert provider.request(item, 100, generation=generation)
        assert started.wait(1)
        provider.begin_generation()
        release.set()
        assert provider.wait_for_done(2000)
        qapp.processEvents()

    assert provider.pending_count == 0
    assert failures.count() == 0
    assert states.count() == 0
    assert ready.count() == 0
    provider.close()


def test_loader_exception_after_shutdown_is_not_applied(
    tmp_path: Path,
    qapp: QApplication,
    caplog,
) -> None:
    image_path = tmp_path / "shutdown-error.jpg"
    write_image(image_path)
    item = make_item(image_path, BrowserItemKind.IMAGE)
    started = Event()
    release = Event()

    def loader(_item: BrowserItem, _size: int) -> QImage:
        started.set()
        release.wait(2)
        raise RuntimeError("shutdown thumbnail failed")

    provider = BrowserThumbnailProvider(loader=loader, max_workers=1)
    failures = QSignalSpy(provider.thumbnail_failed)
    states = QSignalSpy(provider.preview_state_changed)
    ready = QSignalSpy(provider.thumbnail_ready)
    generation = provider.begin_generation()

    with caplog.at_level(logging.ERROR, logger="nivisviewer.thumbnail"):
        assert provider.request(item, 100, generation=generation)
        assert started.wait(1)
        provider.close(wait_msecs=0)
        release.set()
        assert provider.wait_for_done(2000)
        qapp.processEvents()

    assert provider.pending_count == 0
    assert failures.count() == 0
    assert states.count() == 0
    assert ready.count() == 0
