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

    assert provider.request(
        item,
        100,
        generation=first_generation,
        priority=ThumbnailPriority.READ_AHEAD,
    )
    assert not provider.request(
        item,
        100,
        generation=first_generation,
        priority=ThumbnailPriority.READ_AHEAD,
    )
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


def test_background_generation_decodes_folder_and_zip_into_disk_cache(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "背景フォルダ"
    write_image(folder / "01.jpg", color="red")
    write_image(folder / "02.jpg", color="blue")
    archive = tmp_path / "背景書庫.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(folder / "02.jpg", "02.jpg")
        output.write(folder / "01.jpg", "01.jpg")

    disk_cache = ThumbnailDiskCache(tmp_path / "thumbnail-cache")
    provider = BrowserThumbnailProvider(disk_cache=disk_cache)
    generation = provider.begin_generation()
    spec = ThumbnailRenderSpec.from_settings(96, "square_1_1", "letterbox")

    entries = [
        (folder, BrowserItemKind.FOLDER),
        (archive, BrowserItemKind.ARCHIVE),
    ]
    items = []
    for path, kind in entries:
        item = make_item(path, kind)
        items.append(item)
        assert provider.request_background(item, spec, generation=generation) == "queued"
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        stats = provider.cache_statistics()
        assert stats["generated_background"] >= 1
        assert stats["disk_saved_background"] >= 1
        assert stats["usage_bytes"] > 0

    assert provider.cache_statistics()["generated_background"] == 2
    provider.clear_memory_cache()
    for item in items:
        assert provider.request_background(
            item, spec, generation=generation
        ) == "queued"
        assert provider.wait_for_done(5000)
        qapp.processEvents()
    stats = provider.cache_statistics()
    assert stats["disk_hit"] >= 2
    assert stats["generated_background"] == 2
    provider.close()


def test_page_count_completion_releases_background_capacity(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "metadata-only-folder"
    write_image(folder / "01.jpg")
    item = make_item(folder, BrowserItemKind.FOLDER)
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    released: list[bool] = []
    provider.capacity_released.connect(lambda: released.append(True))
    generation = provider.begin_generation()

    assert provider.request_page_count(item, generation=generation)
    assert provider.wait_for_done(5000)
    qapp.processEvents()

    assert released
    assert provider.pending_count == 0
    provider.close()


def test_rejected_thumbnail_submission_releases_background_capacity(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "rejected.jpg"
    write_image(path)
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    provider._start_worker = lambda _worker, _priority: False
    rejected: list[tuple[object, ...]] = []
    provider.submission_rejected.connect(lambda *args: rejected.append(args))
    generation = provider.begin_generation()

    item = make_item(path, BrowserItemKind.IMAGE)
    assert not provider.request(item, 120, generation=generation)
    qapp.processEvents()

    assert rejected == [(str(path), generation, 120, item.thumbnail_revision)]
    assert provider.pending_count == 0
    provider.close()


def test_cancelling_queued_thumbnail_releases_background_capacity(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_path = tmp_path / "active.jpg"
    queued_path = tmp_path / "queued.jpg"
    write_image(first_path)
    write_image(queued_path)
    active = make_item(first_path, BrowserItemKind.IMAGE)
    queued = make_item(queued_path, BrowserItemKind.IMAGE)
    entered = Event()
    release_loader = Event()

    def loader(item, _size, _cancel_token):
        if item.path == active.path:
            entered.set()
            release_loader.wait(3)
        image = QImage(8, 8, QImage.Format.Format_RGBA8888)
        image.fill(0xFFFFFFFF)
        return image

    provider = BrowserThumbnailProvider(
        loader=loader, disk_cache_enabled=False
    )
    released: list[bool] = []
    provider.capacity_released.connect(lambda: released.append(True))
    generation = provider.begin_generation()
    assert provider.request(active, 100, generation=generation)
    assert entered.wait(2)
    assert provider.request(
        queued, 100, generation=generation, priority=ThumbnailPriority.READ_AHEAD
    )

    assert provider.cancel_requests_except(
        {str(active.path)}, size=100, generation=generation
    ) == 1
    qapp.processEvents()
    assert released
    assert provider.pending_count == 1

    release_loader.set()
    assert provider.wait_for_done(5000)
    qapp.processEvents()
    assert provider.pending_count == 0
    provider.close()


def test_browser_memory_cache_obeys_retained_byte_limit(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_path = tmp_path / "first.jpg"
    second_path = tmp_path / "second.jpg"
    write_image(first_path)
    write_image(second_path)
    provider = BrowserThumbnailProvider(
        cache_capacity=100,
        cache_capacity_bytes=4096,
        disk_cache_enabled=False,
    )
    generation = provider.begin_generation()
    first = make_item(first_path, BrowserItemKind.IMAGE)
    second = make_item(second_path, BrowserItemKind.IMAGE)

    for item in (first, second):
        image = QImage(24, 24, QImage.Format.Format_RGBA8888)
        image.fill(0xFFFFFFFF)
        provider._on_finished(
            str(item.path), generation, 120, item.thumbnail_revision, image
        )

    assert provider.memory_cache_usage_bytes == 24 * 24 * 4
    assert provider.memory_cache_usage_bytes <= 4096
    assert len(provider._cache) == 1
    provider.close()


def test_oversized_browser_thumbnail_is_not_retained_in_memory_cache(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "large.jpg"
    write_image(path)
    item = make_item(path, BrowserItemKind.IMAGE)
    provider = BrowserThumbnailProvider(
        cache_capacity_bytes=1024,
        disk_cache_enabled=False,
    )
    generation = provider.begin_generation()
    image = QImage(64, 64, QImage.Format.Format_RGBA8888)
    image.fill(0xFFFFFFFF)

    provider._on_finished(
        str(item.path), generation, 120, item.thumbnail_revision, image
    )

    assert provider.memory_cache_usage_bytes == 0
    assert not provider._cache
    provider.close()


def test_background_warmup_keeps_visible_and_next_viewport_in_ram(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    paths = []
    items = []
    for row in range(160):
        path = tmp_path / f"cached-{row:03}.jpg"
        path.write_bytes(b"fixture")
        paths.append(path)
        items.append(make_item(path, BrowserItemKind.IMAGE))

    loaded_paths = []

    def loader(item, _size, _cancel_token):
        loaded_paths.append(item.path)
        image = QImage(24, 24, QImage.Format.Format_RGBA8888)
        image.fill(0xFF336699)
        return image

    provider = BrowserThumbnailProvider(
        loader=loader,
        cache_capacity=128,
        disk_cache_enabled=False,
    )
    generation = provider.begin_generation()
    spec = ThumbnailRenderSpec.from_settings(96, "square_1_1", "letterbox")
    provider.set_cache_retention_priorities([
        (item.path, item.thumbnail_revision, 0) for item in items[:40]
    ] + [
        (item.path, item.thumbnail_revision, 1) for item in items[40:80]
    ])

    # Model an already-painted 40-row viewport through the normal provider
    # lane, then warm three forward screens without further scrolling.
    for item in items[:40]:
        assert provider.request(
            item,
            spec,
            generation=generation,
            priority=ThumbnailPriority.VISIBLE,
        )
    assert provider.wait_for_done(10000)
    qapp.processEvents()
    assert provider.pending_count == 0

    for item in items[40:]:
        assert provider.request_background(
            item, spec, generation=generation
        ) == "queued"
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        assert provider.pending_count == 0

    cached_paths = {entry[0] for entry in provider._cache}
    visible_paths = {provider._path_key(path) for path in paths[:40]}
    next_viewport_paths = {provider._path_key(path) for path in paths[40:80]}
    stats = provider.cache_statistics()
    assert visible_paths <= cached_paths
    assert next_viewport_paths <= cached_paths
    assert stats["memory_cache_entries"] == 128
    assert stats["generated_background"] == 120
    assert len(loaded_paths) == 160
    assert provider.pending_count == 0
    assert stats["memory_cache_usage_bytes"] <= stats["memory_cache_capacity_bytes"]
    provider.close()


def test_background_thumbnail_survives_failed_disk_write_in_ram(tmp_path, qapp):
    class FailedWriteDiskCache:
        enabled = True

        def usage_bytes(self):
            return 0

        def statistics(self):
            return {"usage_bytes": 0}

        def set_enabled(self, _enabled):
            return None

        def get_suitable(self, *_args, **_kwargs):
            return None

        def put(self, *_args, **_kwargs):
            return False

        def close(self):
            return None

    path = tmp_path / "write-failure.jpg"
    path.write_bytes(b"fixture")
    item = make_item(path, BrowserItemKind.IMAGE)
    decoded = []

    def loader(current, _size, _cancel_token):
        decoded.append(current.path)
        image = QImage(12, 12, QImage.Format.Format_RGBA8888)
        image.fill(0xFF224466)
        return image

    provider = BrowserThumbnailProvider(
        loader=loader,
        cache_capacity=4,
        disk_cache=FailedWriteDiskCache(),  # type: ignore[arg-type]
        disk_cache_enabled=True,
    )
    generation = provider.begin_generation()
    spec = ThumbnailRenderSpec.from_settings(48, "square_1_1", "letterbox")
    provider.set_cache_retention_priorities([(item.path, item.thumbnail_revision, 2)])

    assert provider.request_background(item, spec, generation=generation) == "queued"
    assert provider.wait_for_done(5000)
    qapp.processEvents()

    stats = provider.cache_statistics()
    assert len(decoded) == 1
    assert provider.has_memory_thumbnail(item, spec)
    assert stats["generated_background"] == 1
    assert stats.get("disk_saved_background", 0) == 0
    assert stats["memory_only"] == 1
    assert stats["generated_background_nonresident"] == 0
    assert stats["background_self_evictions"] == 0
    assert not provider.background_disk_cache_enabled
    provider.close()


def test_disk_cache_initialization_failure_is_not_reported_as_available(
    tmp_path, qapp
):
    class DisabledDiskCache:
        enabled = False

        def usage_bytes(self):
            return 0

        def statistics(self):
            return {"usage_bytes": 0}

        def set_enabled(self, _enabled):
            # Simulate a portable/read-only cache location that cannot open.
            self.enabled = False

        def put(self, *_args, **_kwargs):
            raise AssertionError("put must not run when cache initialization fails")

        def close(self):
            return None

    path = tmp_path / "cache-init-failure.jpg"
    path.write_bytes(b"fixture")
    item = make_item(path, BrowserItemKind.IMAGE)

    def loader(_item, _size, _cancel_token):
        image = QImage(12, 12, QImage.Format.Format_RGBA8888)
        image.fill(0xFF224466)
        return image

    provider = BrowserThumbnailProvider(
        loader=loader,
        disk_cache=DisabledDiskCache(),  # type: ignore[arg-type]
        disk_cache_enabled=True,
    )
    generation = provider.begin_generation()
    spec = ThumbnailRenderSpec.from_settings(48, "square_1_1", "letterbox")

    assert provider.request_background(item, spec, generation=generation) == "queued"
    assert provider.wait_for_done(5000)
    qapp.processEvents()

    assert provider.has_memory_thumbnail(item, spec)
    assert not provider.background_disk_cache_enabled
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
        item.thumbnail_revision,
        source,
    )
    cache_key = (provider._path_key(item.path), 120, item.thumbnail_revision)
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


def test_directional_read_ahead_decodes_images_but_not_folders_or_archives(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image_path = tmp_path / "image.jpg"
    write_image(image_path)
    folder_path = tmp_path / "folder"
    folder_path.mkdir()
    archive_path = tmp_path / "archive.cbz"
    archive_path.write_bytes(b"archive")
    calls: list[str] = []

    def loader(item, _size, _cancel_token=None, _priority=None):
        calls.append(str(item.path))
        return QImage(8, 8, QImage.Format.Format_RGBA8888)

    provider = BrowserThumbnailProvider(
        loader=loader,
        max_workers=1,
        disk_cache_enabled=False,
    )
    generation = provider.begin_generation()
    for path, kind in (
        (image_path, BrowserItemKind.IMAGE),
        (folder_path, BrowserItemKind.FOLDER),
        (archive_path, BrowserItemKind.ARCHIVE),
    ):
        assert provider.request(
            make_item(path, kind),
            100,
            generation=generation,
            priority=ThumbnailPriority.READ_AHEAD,
        )
    assert provider.wait_for_done(2000)
    qapp.processEvents()

    assert calls == [str(image_path)]
    stats = provider.cache_statistics()
    assert stats["generated_read_ahead"] == 1
    assert stats["prefetch_skipped"] == 2

    for path, kind in (
        (folder_path, BrowserItemKind.FOLDER),
        (archive_path, BrowserItemKind.ARCHIVE),
    ):
        assert provider.request(
            make_item(path, kind),
            100,
            generation=generation,
            priority=ThumbnailPriority.VISIBLE,
        )
    assert provider.wait_for_done(2000)
    qapp.processEvents()
    assert calls == [str(image_path), str(folder_path), str(archive_path)]
    provider.close()


def test_read_ahead_memory_hit_does_not_decode_again(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image_path = tmp_path / "cached.jpg"
    write_image(image_path)
    item = make_item(image_path, BrowserItemKind.IMAGE)
    calls: list[str] = []

    def loader(current, _size, _cancel_token=None, _priority=None):
        calls.append(str(current.path))
        return QImage(8, 8, QImage.Format.Format_RGBA8888)

    provider = BrowserThumbnailProvider(
        loader=loader,
        max_workers=1,
        disk_cache_enabled=False,
    )
    first = provider.begin_generation()
    assert provider.request(
        item,
        100,
        generation=first,
        priority=ThumbnailPriority.VISIBLE,
    )
    assert provider.wait_for_done(2000)
    qapp.processEvents()

    second = provider.begin_generation()
    assert not provider.request(
        item,
        100,
        generation=second,
        priority=ThumbnailPriority.READ_AHEAD,
    )
    qapp.processEvents()
    assert calls == [str(image_path)]
    assert provider.cache_statistics()["memory_hit"] == 1
    provider.close()


def test_read_ahead_persists_to_existing_disk_cache_for_next_visible_hit(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image_path = tmp_path / "disk-read-ahead.jpg"
    write_image(image_path)
    item = make_item(image_path, BrowserItemKind.IMAGE)
    cache_path = tmp_path / "cache"
    calls: list[str] = []

    def loader(current, _size, _cancel_token=None, _priority=None):
        calls.append(str(current.path))
        return QImage(8, 8, QImage.Format.Format_RGBA8888)

    first_provider = BrowserThumbnailProvider(
        loader=loader,
        disk_cache=ThumbnailDiskCache(cache_path, enabled=False),
        disk_cache_enabled=True,
    )
    first = first_provider.begin_generation()
    assert first_provider.request(
        item,
        100,
        generation=first,
        priority=ThumbnailPriority.READ_AHEAD,
    )
    assert first_provider.wait_for_done(2000)
    qapp.processEvents()
    first_stats = first_provider.cache_statistics()
    assert first_stats["disk_saved_read_ahead"] == 1
    first_provider.close()

    second_provider = BrowserThumbnailProvider(
        loader=loader,
        disk_cache=ThumbnailDiskCache(cache_path, enabled=False),
        disk_cache_enabled=True,
    )
    second = second_provider.begin_generation()
    assert second_provider.request(
        item,
        100,
        generation=second,
        priority=ThumbnailPriority.VISIBLE,
    )
    assert second_provider.wait_for_done(2000)
    qapp.processEvents()

    assert calls == [str(image_path)]
    assert second_provider.cache_statistics()["disk_hit"] == 1
    second_provider.close()


def test_background_replenishment_reads_disk_without_source_decode(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image_path = tmp_path / "background-disk-hit.jpg"
    write_image(image_path)
    item = make_item(image_path, BrowserItemKind.IMAGE)
    cache_path = tmp_path / "cache"
    calls: list[str] = []

    def loader(current, _size, _cancel_token=None, _priority=None):
        calls.append(str(current.path))
        image = QImage(10, 10, QImage.Format.Format_RGBA8888)
        image.fill(0xFF446688)
        return image

    spec = ThumbnailRenderSpec.from_settings(48, "square_1_1", "letterbox")
    writer = BrowserThumbnailProvider(
        loader=loader,
        disk_cache=ThumbnailDiskCache(cache_path, enabled=False),
        disk_cache_enabled=True,
    )
    generation = writer.begin_generation()
    writer.set_cache_retention_priorities([(item.path, item.thumbnail_revision, 2)])
    assert writer.request_background(item, spec, generation=generation) == "queued"
    assert writer.wait_for_done(5000)
    qapp.processEvents()
    assert writer.cache_statistics()["disk_saved_background"] == 1
    writer.close()

    reader = BrowserThumbnailProvider(
        loader=loader,
        disk_cache=ThumbnailDiskCache(cache_path, enabled=False),
        disk_cache_enabled=True,
    )
    second_generation = reader.begin_generation()
    reader.set_cache_retention_priorities([(item.path, item.thumbnail_revision, 2)])
    assert reader.request_background(
        item, spec, generation=second_generation
    ) == "queued"
    assert reader.wait_for_done(5000)
    qapp.processEvents()

    stats = reader.cache_statistics()
    assert calls == [str(image_path)]
    assert stats["disk_hit"] == 1
    assert stats["background_disk_hit"] == 1
    assert stats.get("generated_background", 0) == 0
    assert reader.has_memory_thumbnail(item, spec)
    reader.close()


def test_recenter_cancels_queued_old_visible_and_speculative_work(
    tmp_path: Path,
) -> None:
    blocker_path = tmp_path / "blocker.jpg"
    old_visible_path = tmp_path / "old-visible.jpg"
    old_ahead_path = tmp_path / "old-ahead.jpg"
    new_visible_path = tmp_path / "new-visible.jpg"
    for path in (
        blocker_path,
        old_visible_path,
        old_ahead_path,
        new_visible_path,
    ):
        write_image(path)
    started = Event()
    release = Event()
    calls: list[str] = []

    def loader(item, _size, _cancel_token=None, _priority=None):
        calls.append(str(item.path))
        if item.path == blocker_path:
            started.set()
            release.wait(2)
        return QImage(8, 8, QImage.Format.Format_RGBA8888)

    provider = BrowserThumbnailProvider(
        loader=loader,
        max_workers=1,
        disk_cache_enabled=False,
    )
    generation = provider.begin_generation()
    assert provider.request(
        make_item(blocker_path, BrowserItemKind.IMAGE),
        100,
        generation=generation,
        priority=ThumbnailPriority.VISIBLE,
    )
    assert started.wait(1)
    assert provider.request(
        make_item(old_visible_path, BrowserItemKind.IMAGE),
        100,
        generation=generation,
        priority=ThumbnailPriority.VISIBLE,
    )
    assert provider.request(
        make_item(old_ahead_path, BrowserItemKind.IMAGE),
        100,
        generation=generation,
        priority=ThumbnailPriority.READ_AHEAD,
    )
    assert provider.cancel_requests_except(
        {str(blocker_path), str(new_visible_path)},
        size=100,
        generation=generation,
    ) == 2
    assert provider.request(
        make_item(new_visible_path, BrowserItemKind.IMAGE),
        100,
        generation=generation,
        priority=ThumbnailPriority.VISIBLE,
    )
    release.set()
    assert provider.wait_for_done(2000)

    assert calls == [str(blocker_path), str(new_visible_path)]
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
