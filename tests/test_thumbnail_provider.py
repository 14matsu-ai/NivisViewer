from __future__ import annotations

import io
import logging
import os
import zipfile
import time
from collections import OrderedDict
from pathlib import Path
from threading import Event

import pytest
from PIL import Image
from psd_tools import PSDImage
from PySide6.QtCore import QRunnable
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from app.browser_model import BrowserItem, BrowserItemKind
from app.file_preview import PreviewResult, PreviewResultKind, PreviewSource
from app.preview_provider_registry import PreviewProviderRegistry
from app.thumbnail_provider import BrowserThumbnailProvider, ThumbnailLoadResult
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.thumbnail_disk_cache import ThumbnailDiskCache, ThumbnailSourceIdentity
from app import thumbnail_render as thumbnail_render_module
from app.thumbnail_render import ThumbnailRenderSpec, render_pil_thumbnail


def write_image(path: Path, *, color: str = "white") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (24, 32), color) as image:
        image.save(path)


def write_psd(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (120, 180), "navy") as image:
        PSDImage.frompil(image).save(path)


def write_kra(path: Path) -> None:
    merged = io.BytesIO()
    preview = io.BytesIO()
    with Image.new("RGB", (120, 180), "red") as image:
        image.save(merged, "PNG")
    with Image.new("RGB", (96, 144), "blue") as image:
        image.save(preview, "PNG")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mergedimage.png", merged.getvalue())
        archive.writestr("preview.png", preview.getvalue())


def test_kra_browser_thumbnail_uses_embedded_preview(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cover.kra"
    write_kra(path)
    spec = ThumbnailRenderSpec.from_settings(
        80,
        "square_1_1",
        "letterbox",
    )

    result = BrowserThumbnailProvider.load_thumbnail(
        make_item(path, BrowserItemKind.IMAGE),
        spec,
    )

    assert result is not None
    assert not result.isNull()
    center = result.pixelColor(result.width() // 2, result.height() // 2)
    assert center.blue() > center.red()


def test_psd_browser_thumbnail_uses_supported_image_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cover.psd"
    write_psd(path)
    spec = ThumbnailRenderSpec.from_settings(
        96,
        "square_1_1",
        "letterbox",
    )

    result = BrowserThumbnailProvider.load_thumbnail(
        make_item(path, BrowserItemKind.IMAGE),
        spec,
    )

    assert result is not None
    assert not result.isNull()


def make_item(path: Path, kind: BrowserItemKind) -> BrowserItem:
    return BrowserItem(path.name, path, kind, path.stat().st_mtime)


@pytest.mark.parametrize("priority", [ThumbnailPriority.READ_AHEAD,
    ThumbnailPriority.PREFETCH, ThumbnailPriority.BACKGROUND])
def test_xcf_speculation_never_invokes_decoder(tmp_path, monkeypatch, priority):
    import app.thumbnail_provider as module
    path = tmp_path / "drawing.xcf"
    path.write_bytes(b"not needed for a skipped decode")
    def forbidden(*args, **kwargs):
        pytest.fail("Speculative XCF work must not flatten layers")
    monkeypatch.setattr(module, "decode_creative_thumbnail", forbidden)
    provider = BrowserThumbnailProvider()
    try:
        result = provider._load_pipeline(make_item(path, BrowserItemKind.IMAGE),
                                         80, thumbnail_priority=priority)
        assert result.resolved_kind is PreviewResultKind.NOT_APPLICABLE
        assert result.persist_to_disk is False
    finally:
        provider.close()


def _wait_for_spy(spy: QSignalSpy, qapp: QApplication, timeout_ms: int = 3000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while spy.count() == 0 and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.002)
    qapp.processEvents()
    return spy.count() > 0


def test_video_prefetch_shell_is_provisional_then_visible_runs_ffmpeg(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"video-fixture")
    item = make_item(path, BrowserItemKind.OTHER)
    spec = ThumbnailRenderSpec.from_settings(96, "square_1_1", "letterbox")

    class Shell:
        def request_thumbnail(self, *_args, **_kwargs):
            image = QImage(32, 32, QImage.Format.Format_RGBA8888)
            image.fill(0xFF224466)
            return PreviewResult.ready_image(
                image,
                source=PreviewSource.WINDOWS_SHELL,
                persist_to_disk=False,
            )

    class FFmpeg:
        def __init__(self) -> None:
            self.calls = 0
            self.policy = None

        def generate(self, _path, _spec, _cancel_token):
            self.calls += 1
            image = QImage(32, 32, QImage.Format.Format_RGBA8888)
            image.fill(0xFF6688AA)
            return PreviewResult.ready_image(
                image,
                source=PreviewSource.FFMPEG,
                persist_to_disk=True,
                entry_path="video-test-final",
            )

    ffmpeg = FFmpeg()
    registry = PreviewProviderRegistry(
        settings={
            "video_thumbnail_enabled": True,
            "video_thumbnail_backend": "auto",
            "video_thumbnail_frame_mode": "smart",
            "video_thumbnail_shell_placeholder": True,
        },
        shell_service=Shell(),  # type: ignore[arg-type]
        ffmpeg_backend=ffmpeg,  # type: ignore[arg-type]
    )
    provider = BrowserThumbnailProvider(
        disk_cache_enabled=False,
        preview_registry=registry,
    )
    provisional = QSignalSpy(provider.thumbnail_provisional)
    ready = QSignalSpy(provider.thumbnail_ready)
    generation = provider.begin_generation()
    try:
        assert provider.request(
            item,
            spec,
            generation=generation,
            priority=ThumbnailPriority.PREFETCH,
        )
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert provisional.count() == 1
        assert ready.count() == 0
        assert not provider.has_memory_thumbnail(item, spec)
        assert ffmpeg.calls == 0

        assert provider.request(
            item,
            spec,
            generation=generation,
            priority=ThumbnailPriority.VISIBLE,
        )
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert ready.count() == 1
        assert provider.has_memory_thumbnail(item, spec)
        assert ffmpeg.calls == 1
    finally:
        provider.close(wait_msecs=1000)
        qapp.processEvents()


def test_running_video_prefetch_promotes_to_one_visible_ffmpeg_final(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "running-movie.mp4"
    path.write_bytes(b"video-fixture")
    item = make_item(path, BrowserItemKind.OTHER)
    spec = ThumbnailRenderSpec.from_settings(96, "square_1_1", "letterbox")
    shell_started = Event()
    release_shell = Event()

    class Shell:
        def request_thumbnail(self, *_args, **_kwargs):
            shell_started.set()
            assert release_shell.wait(3)
            image = QImage(32, 32, QImage.Format.Format_RGBA8888)
            image.fill(0xFF224466)
            return PreviewResult.ready_image(
                image,
                source=PreviewSource.WINDOWS_SHELL,
                persist_to_disk=False,
            )

    class FFmpeg:
        def __init__(self) -> None:
            self.calls = 0
            self.policy = None

        def generate(self, _path, _spec, _cancel_token):
            self.calls += 1
            image = QImage(32, 32, QImage.Format.Format_RGBA8888)
            image.fill(0xFF6688AA)
            return PreviewResult.ready_image(
                image,
                source=PreviewSource.FFMPEG,
                persist_to_disk=True,
            )

    ffmpeg = FFmpeg()
    registry = PreviewProviderRegistry(
        settings={
            "video_thumbnail_enabled": True,
            "video_thumbnail_backend": "auto",
            "video_thumbnail_frame_mode": "smart",
            "video_thumbnail_shell_placeholder": True,
        },
        shell_service=Shell(),  # type: ignore[arg-type]
        ffmpeg_backend=ffmpeg,  # type: ignore[arg-type]
    )
    provider = BrowserThumbnailProvider(
        disk_cache_enabled=False,
        preview_registry=registry,
    )
    provisional = QSignalSpy(provider.thumbnail_provisional)
    ready = QSignalSpy(provider.thumbnail_ready)
    generation = provider.begin_generation()
    try:
        assert provider.request(
            item,
            spec,
            generation=generation,
            priority=ThumbnailPriority.PREFETCH,
        )
        assert shell_started.wait(2)

        # The visible request arrives while the prefetch worker is already in
        # the Shell call. It must not create a second worker or claim Shell as
        # the final RAM result.
        assert not provider.request(
            item,
            spec,
            generation=generation,
            priority=ThumbnailPriority.VISIBLE,
        )
        release_shell.set()
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert provisional.count() == 1
        assert ready.count() == 0
        assert ffmpeg.calls == 0

        assert provider.request(
            item,
            spec,
            generation=generation,
            priority=ThumbnailPriority.VISIBLE,
        )
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert ready.count() == 1
        assert ffmpeg.calls == 1
        assert provider.has_memory_thumbnail(item, spec)

        # Repeated retention/request passes use the final RAM entry and do not
        # regenerate FFmpeg output.
        assert not provider.request(
            item,
            spec,
            generation=generation,
            priority=ThumbnailPriority.VISIBLE,
        )
        qapp.processEvents()
        assert ffmpeg.calls == 1
    finally:
        release_shell.set()
        provider.close(wait_msecs=1000)
        qapp.processEvents()


def test_windows_shell_video_mode_keeps_shell_result_final(tmp_path: Path) -> None:
    path = tmp_path / "shell-only.mp4"
    path.write_bytes(b"video-fixture")
    item = make_item(path, BrowserItemKind.OTHER)
    spec = ThumbnailRenderSpec.from_settings(96, "square_1_1", "letterbox")

    class Shell:
        def request_thumbnail(self, *_args, **_kwargs):
            image = QImage(16, 16, QImage.Format.Format_RGBA8888)
            image.fill(0xFF224466)
            return PreviewResult.ready_image(
                image,
                source=PreviewSource.WINDOWS_SHELL,
                persist_to_disk=False,
            )

    registry = PreviewProviderRegistry(
        settings={
            "video_thumbnail_enabled": True,
            "video_thumbnail_backend": "windows_shell",
            "video_thumbnail_frame_mode": "windows_shell",
        },
        shell_service=Shell(),  # type: ignore[arg-type]
    )
    result = registry.generate(
        item,
        spec,
        priority=ThumbnailPriority.VISIBLE,
    )
    assert result.kind is PreviewResultKind.READY
    assert result.ready
    assert result.provisional_image is None
    assert result.cache_in_memory


def test_generation_change_clears_queued_cache_maintenance_and_allows_retry(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    disk_cache = ThumbnailDiskCache(tmp_path / "queued-maintenance-cache")
    provider = BrowserThumbnailProvider(disk_cache=disk_cache)
    lane_started = Event()
    release_lane = Event()
    maintenance_calls: list[bool] = []

    class BlockingRunnable(QRunnable):
        def run(self) -> None:
            lane_started.set()
            release_lane.wait(5)

    def record_maintenance(*, force: bool = False) -> bool:
        maintenance_calls.append(force)
        return True

    disk_cache.cleanup_if_due = record_maintenance  # type: ignore[method-assign]
    try:
        provider._pool.start(BlockingRunnable())
        assert lane_started.wait(2)
        assert provider.cleanup_caches_async(force=True)
        queued_worker = provider._maintenance_worker
        assert queued_worker is not None
        assert not queued_worker.run_started.is_set()
        assert provider._maintenance_running

        provider.begin_generation()
        assert not provider._maintenance_running
        assert provider._maintenance_worker is None

        release_lane.set()
        assert provider._pool.waitForDone(3000)
        assert maintenance_calls == []

        assert provider.cleanup_caches_async(force=True)
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert maintenance_calls == [True]
        assert not provider._maintenance_running
    finally:
        release_lane.set()
        provider.close(wait_msecs=1000)
        qapp.processEvents()


def test_running_cache_maintenance_remains_single_flight_across_generation_change(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    disk_cache = ThumbnailDiskCache(tmp_path / "running-maintenance-cache")
    provider = BrowserThumbnailProvider(disk_cache=disk_cache)
    maintenance_started = Event()
    release_maintenance = Event()
    calls: list[bool] = []
    active = 0
    maximum_active = 0

    def controlled_maintenance(*, force: bool = False) -> bool:
        nonlocal active, maximum_active
        calls.append(force)
        active += 1
        maximum_active = max(maximum_active, active)
        try:
            if len(calls) == 1:
                maintenance_started.set()
                release_maintenance.wait(5)
            return True
        finally:
            active -= 1

    disk_cache.cleanup_if_due = controlled_maintenance  # type: ignore[method-assign]
    try:
        assert provider.cleanup_caches_async(force=True)
        assert maintenance_started.wait(2)
        running_worker = provider._maintenance_worker
        assert running_worker is not None
        assert running_worker.run_started.is_set()
        assert provider._maintenance_running

        provider.begin_generation()
        assert provider._maintenance_running
        assert not provider.cleanup_caches_async(force=True)
        assert calls == [True]
        assert maximum_active == 1

        release_maintenance.set()
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert not provider._maintenance_running

        assert provider.cleanup_caches_async(force=True)
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert calls == [True, True]
        assert maximum_active == 1
        assert not provider._maintenance_running
    finally:
        release_maintenance.set()
        provider.close(wait_msecs=1000)
        qapp.processEvents()


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
    assert first_provider.wait_for_done(5000)
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
    assert writer.wait_for_done(5000)
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


def test_slow_disk_persistence_cannot_withhold_ready_thumbnail(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    source = tmp_path / "slow-save.jpg"
    write_image(source)
    item = make_item(source, BrowserItemKind.IMAGE)
    disk_cache = ThumbnailDiskCache(tmp_path / "thumbnail-cache")
    original_put = disk_cache.put
    save_started = Event()
    release_save = Event()

    def slow_put(*args, **kwargs):
        save_started.set()
        release_save.wait(3)
        return original_put(*args, **kwargs)

    monkeypatch.setattr(disk_cache, "put", slow_put)

    def loader(_item: BrowserItem, _size: int) -> QImage:
        image = QImage(32, 32, QImage.Format.Format_RGBA8888)
        image.fill(0xFF336699)
        return image

    provider = BrowserThumbnailProvider(loader=loader, disk_cache=disk_cache)
    ready = QSignalSpy(provider.thumbnail_ready)
    generation = provider.begin_generation()
    spec = ThumbnailRenderSpec.from_settings(96, "square_1_1", "letterbox")
    assert provider.request(item, spec, generation=generation)

    assert _wait_for_spy(ready, qapp, 2000)
    assert save_started.wait(1)
    assert ready.count() == 1
    assert not release_save.is_set()
    release_save.set()
    assert provider.wait_for_done(5000)
    qapp.processEvents()
    assert provider.cache_statistics()["disk_saved"] == 1
    provider.close()


def test_deferred_save_does_not_cache_pixels_for_a_newer_source(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    source = tmp_path / "deferred-source.jpg"
    write_image(source, color="red")
    # Deliberately omit file_size/modified_time_ns as can happen for an item
    # supplied by callers that have not retained scan metadata.
    item = BrowserItem(source.name, source, BrowserItemKind.IMAGE, source.stat().st_mtime)
    disk_cache = ThumbnailDiskCache(tmp_path / "thumbnail-cache")
    original_put = disk_cache.put
    save_started = Event()
    release_save = Event()

    def paused_put(*args, **kwargs):
        save_started.set()
        release_save.wait(3)
        return original_put(*args, **kwargs)

    monkeypatch.setattr(disk_cache, "put", paused_put)

    def loader(_item: BrowserItem, _size: int) -> QImage:
        image = QImage(24, 24, QImage.Format.Format_RGBA8888)
        image.fill(0xFFFF0000)
        return image

    provider = BrowserThumbnailProvider(loader=loader, disk_cache=disk_cache)
    ready = QSignalSpy(provider.thumbnail_ready)
    generation = provider.begin_generation()
    spec = ThumbnailRenderSpec.from_settings(96, "square_1_1", "letterbox")
    assert provider.request(item, spec, generation=generation)
    assert _wait_for_spy(ready, qapp, 2000)
    assert save_started.wait(1)

    old_mtime_ns = source.stat().st_mtime_ns
    write_image(source, color="blue")
    os.utime(source, ns=(old_mtime_ns + 2_000_000_000, old_mtime_ns + 2_000_000_000))
    release_save.set()

    assert provider.wait_for_done(5000)
    qapp.processEvents()
    assert disk_cache.get_suitable(make_item(source, BrowserItemKind.IMAGE), spec) is None
    assert provider.cache_statistics()["disk_saved"] == 0
    provider.close()


def test_deferred_folder_save_does_not_cache_pixels_for_a_newer_cover(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "deferred-folder"
    cover = folder / "cover.jpg"
    write_image(cover, color="red")
    item = make_item(folder, BrowserItemKind.FOLDER)
    disk_cache = ThumbnailDiskCache(tmp_path / "thumbnail-cache")
    original_put = disk_cache.put
    save_started = Event()
    release_save = Event()

    def paused_put(*args, **kwargs):
        save_started.set()
        release_save.wait(3)
        return original_put(*args, **kwargs)

    def decoded_cover(*_args, **_kwargs):
        image = QImage(24, 24, QImage.Format.Format_RGBA8888)
        image.fill(0xFFFF0000)
        return image

    monkeypatch.setattr(disk_cache, "put", paused_put)
    monkeypatch.setattr(
        BrowserThumbnailProvider,
        "_load_image_path",
        staticmethod(decoded_cover),
    )
    provider = BrowserThumbnailProvider(disk_cache=disk_cache)
    ready = QSignalSpy(provider.thumbnail_ready)
    generation = provider.begin_generation()
    spec = ThumbnailRenderSpec.from_settings(96, "square_1_1", "letterbox")
    assert provider.request(item, spec, generation=generation)
    assert _wait_for_spy(ready, qapp, 2000)
    assert save_started.wait(1)

    old_mtime_ns = cover.stat().st_mtime_ns
    write_image(cover, color="blue")
    os.utime(cover, ns=(old_mtime_ns + 2_000_000_000, old_mtime_ns + 2_000_000_000))
    release_save.set()

    assert provider.wait_for_done(5000)
    qapp.processEvents()
    assert disk_cache.get_suitable(make_item(folder, BrowserItemKind.FOLDER), spec) is None
    assert provider.cache_statistics()["disk_saved"] == 0
    provider.close()


def test_duplicate_deferred_saves_coalesce_by_source_and_render_spec(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    source = tmp_path / "duplicate-save.jpg"
    write_image(source)
    item = make_item(source, BrowserItemKind.IMAGE)
    disk_cache = ThumbnailDiskCache(tmp_path / "thumbnail-cache")
    original_put = disk_cache.put
    save_started = Event()
    release_save = Event()
    put_calls = 0

    def paused_put(*args, **kwargs):
        nonlocal put_calls
        put_calls += 1
        save_started.set()
        release_save.wait(3)
        return original_put(*args, **kwargs)

    monkeypatch.setattr(disk_cache, "put", paused_put)
    provider = BrowserThumbnailProvider(loader=lambda *_: None, disk_cache=disk_cache)
    spec = ThumbnailRenderSpec.from_settings(96, "square_1_1", "letterbox")
    image = QImage(24, 24, QImage.Format.Format_RGBA8888)
    image.fill(0xFF336699)
    source_identity = ThumbnailSourceIdentity.capture(item)
    assert source_identity is not None
    result = ThumbnailLoadResult(image, source_identity=source_identity)

    assert provider._queue_thumbnail_save(
        item, spec, result, ThumbnailPriority.BACKGROUND
    )
    assert save_started.wait(1)
    assert provider._queue_thumbnail_save(
        item, spec, result, ThumbnailPriority.VISIBLE
    )
    release_save.set()

    assert provider.wait_for_done(5000)
    assert put_calls == 1
    assert provider.cache_statistics()["disk_saved"] == 1
    provider.close()


def test_cache_clear_invalidates_a_save_already_waiting_to_encode(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    source = tmp_path / "clear-race.jpg"
    write_image(source)
    item = make_item(source, BrowserItemKind.IMAGE)
    disk_cache = ThumbnailDiskCache(tmp_path / "thumbnail-cache")
    original_put = disk_cache.put
    save_started = Event()
    release_save = Event()

    def slow_put(*args, **kwargs):
        save_started.set()
        release_save.wait(3)
        return original_put(*args, **kwargs)

    monkeypatch.setattr(disk_cache, "put", slow_put)

    def loader(_item: BrowserItem, _size: int) -> QImage:
        image = QImage(24, 24, QImage.Format.Format_RGBA8888)
        image.fill(0xFFAA5500)
        return image

    provider = BrowserThumbnailProvider(loader=loader, disk_cache=disk_cache)
    ready = QSignalSpy(provider.thumbnail_ready)
    generation = provider.begin_generation()
    spec = ThumbnailRenderSpec.from_settings(96, "square_1_1", "letterbox")
    assert provider.request(item, spec, generation=generation)
    assert _wait_for_spy(ready, qapp, 2000)
    assert save_started.wait(1)

    cleared = QSignalSpy(provider.cache_cleared)
    provider.clear_all_caches_async()
    release_save.set()
    assert _wait_for_spy(cleared, qapp, 3000)
    assert provider.wait_for_done(3000)
    qapp.processEvents()
    assert disk_cache.usage_bytes() == 0
    provider.close()


def test_folder_and_zip_fallback_stop_between_candidates_when_cancelled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    folder = tmp_path / "cancel-folder"
    write_image(folder / "01.jpg")
    write_image(folder / "02.jpg")
    folder_cancel = Event()
    folder_attempts: list[Path] = []

    def cancel_folder_candidate(path, *_args, **_kwargs):
        folder_attempts.append(path)
        folder_cancel.set()
        return None

    monkeypatch.setattr(
        BrowserThumbnailProvider,
        "_load_image_path",
        staticmethod(cancel_folder_candidate),
    )
    spec = ThumbnailRenderSpec.from_settings(96, "square_1_1", "letterbox")
    folder_result = BrowserThumbnailProvider.load_thumbnail_result(
        make_item(folder, BrowserItemKind.FOLDER),
        spec,
        cancel_token=folder_cancel,
    )
    assert folder_result.resolved_kind is PreviewResultKind.CANCELLED
    assert len(folder_attempts) == 1

    archive = tmp_path / "cancel.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(folder / "01.jpg", "01.jpg")
        output.write(folder / "02.jpg", "02.jpg")
    archive_cancel = Event()
    render_attempts: list[str] = []

    def cancel_archive_render(*_args, **_kwargs):
        render_attempts.append("first")
        archive_cancel.set()
        raise OSError("simulate an unreadable first archive image")

    monkeypatch.setattr(
        BrowserThumbnailProvider,
        "_render_image",
        staticmethod(cancel_archive_render),
    )
    archive_result = BrowserThumbnailProvider.load_thumbnail_result(
        make_item(archive, BrowserItemKind.ARCHIVE),
        spec,
        cancel_token=archive_cancel,
    )
    assert archive_result.resolved_kind is PreviewResultKind.CANCELLED
    assert len(render_attempts) == 1


def test_zip_thumbnail_skips_unreadable_first_member_and_uses_next_image(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "fallback.zip"
    valid_image = tmp_path / "valid.png"
    write_image(valid_image)
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("00-unreadable.jpg", b"not an image")
        output.write(valid_image, "01-valid.png")

    spec = ThumbnailRenderSpec.from_settings(96, "square_1_1", "letterbox")
    result = BrowserThumbnailProvider.load_thumbnail_result(
        make_item(archive, BrowserItemKind.ARCHIVE),
        spec,
    )

    assert result.image is not None and not result.image.isNull()
    assert result.entry_path == "01-valid.png"
    assert result.page_count == 2


def test_zip_jpeg_uses_draft_decode_before_full_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "large-archive-entry.jpg"
    with Image.new("RGB", (3000, 4200), "#426b8f") as image:
        image.save(source, format="JPEG", quality=90)
    archive = tmp_path / "large-entry.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.write(source, "large-entry.jpg")

    observed_sizes: list[tuple[int, int]] = []
    original_draft = thumbnail_render_module._draft_jpeg_before_copy

    def record_draft(image, spec, normalized_crop):
        changed = original_draft(image, spec, normalized_crop)
        observed_sizes.append(image.size)
        return changed

    monkeypatch.setattr(
        thumbnail_render_module,
        "_draft_jpeg_before_copy",
        record_draft,
    )
    spec = ThumbnailRenderSpec.from_settings(
        256,
        "square_1_1",
        "letterbox",
        quality_mode="high",
    )
    result = BrowserThumbnailProvider.load_thumbnail_result(
        make_item(archive, BrowserItemKind.ARCHIVE),
        spec,
    )

    assert result.image is not None and not result.image.isNull()
    assert observed_sizes
    assert observed_sizes[0] != (3000, 4200)


@pytest.mark.parametrize("orientation", [6, 8])
def test_jpeg_draft_precedes_copy_and_preserves_oriented_target_size(
    tmp_path: Path,
    qapp: QApplication,
    orientation: int,
) -> None:
    source = tmp_path / f"oriented-large-{orientation}.jpg"
    exif = Image.Exif()
    exif[274] = orientation
    with Image.new("RGB", (2400, 3600), "#336699") as image:
        image.save(source, format="JPEG", quality=92, exif=exif)
    spec = ThumbnailRenderSpec.from_settings(
        256,
        "portrait_1_sqrt2",
        "smart_crop",
        max_edge=512,
    )
    with Image.open(source) as image:
        original_size = image.size
        rendered, crop = render_pil_thumbnail(image, spec)
        assert image.size != original_size
    assert (rendered.width(), rendered.height()) == (
        spec.frame_width,
        spec.frame_height,
    )
    assert crop is not None


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


@pytest.mark.parametrize("entry_count", [256, 4096, 16384])
def test_browser_cache_candidate_and_trim_use_key_indexes(
    qapp: QApplication,
    entry_count: int,
) -> None:
    provider = BrowserThumbnailProvider(
        loader=lambda *_: None,
        disk_cache_enabled=False,
        cache_capacity=entry_count + 1,
        cache_capacity_bytes=(entry_count + 1) * 4,
    )
    spec = ThumbnailRenderSpec.from_settings(48, "square_1_1", "letterbox")
    image = QImage(1, 1, QImage.Format.Format_RGB32)
    image.fill(0)
    revision = ("test-revision",)
    provider._browser_memory_managed = True
    provider._cache_specs[spec.cache_token] = spec
    for index in range(entry_count):
        path_key = f"fixture-{index}"
        key = (path_key, spec.cache_token, revision)
        provider._cache[key] = image
        provider._cache_bytes += int(image.sizeInBytes())
        provider._cache_page_counts[key] = None
        provider._cache_index_add(key)
    provider._rebuild_cache_rank_index()

    class NoAllCacheWalk(OrderedDict):
        def items(self):
            raise AssertionError("completion path walked every cached image")

        def __iter__(self):
            raise AssertionError("completion path iterated every cached key")

    provider._cache = NoAllCacheWalk(list(provider._cache.items()))
    try:
        candidate = provider._memory_candidate(
            f"fixture-{entry_count - 1}", revision, spec
        )
        assert candidate is not None

        provider._cache_capacity = entry_count - 1
        provider._cache_capacity_bytes = (entry_count - 1) * int(image.sizeInBytes())
        provider._trim_browser_memory()
        assert len(provider._cache) <= entry_count - 1
        assert provider.memory_cache_bytes <= provider.memory_cache_limit_bytes
    finally:
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
