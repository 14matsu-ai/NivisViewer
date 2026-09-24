from __future__ import annotations

from concurrent.futures import Future
import os
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtGui import QColor, QImage

import app.thumbnail_disk_cache as thumbnail_disk_cache_module
from app.browser_model import BrowserItem, BrowserItemKind
from app.file_operation_plan import FileOperationPlanner
from app.file_operation_service import (
    FileCollisionPolicy,
    FileOperationErrorCode,
    FileOperationKind,
    FileOperationRequest,
    FileOperationService,
)
from app.pdf_backend import PdfBackendError, PdfErrorCode
from app.pdfium_service import PdfiumService
from app.thumbnail_disk_cache import ThumbnailDiskCache, ThumbnailSourceIdentity
from app.thumbnail_render import ThumbnailRenderSpec
from app.thumbnail_provider import BrowserThumbnailProvider, ThumbnailLoadResult
from app.browser_thumbnail_scheduler import ThumbnailPriority


def _item(path: Path) -> BrowserItem:
    info = path.stat()
    return BrowserItem(
        path.name,
        path,
        BrowserItemKind.IMAGE,
        info.st_mtime,
        file_size=info.st_size,
        modified_time_ns=info.st_mtime_ns,
    )


def _image() -> QImage:
    image = QImage(16, 16, QImage.Format.Format_RGBA8888)
    image.fill(QColor("red"))
    return image


def test_cross_volume_receipt_removes_unchanged_single_file(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"unchanged")
    service = FileOperationService()
    receipt = service._capture_source_receipt(str(source))

    service._remove_source_receipt(str(source), receipt, Event())

    assert not source.exists()


def test_merge_rejects_destination_symlink_before_writing_outside_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "chapter").mkdir()
    (source / "chapter" / "page.txt").write_text("safe", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (destination / "source").mkdir()
        os.symlink(outside, destination / "source" / "chapter", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable in this Windows test environment")

    result = FileOperationService().copy(
        [source],
        destination,
        collision_policy=FileCollisionPolicy.MERGE,
    )

    assert not (outside / "page.txt").exists()
    assert any(
        item.error_code == FileOperationErrorCode.IO_ERROR.value
        for item in result.failures
    )


def test_thumbnail_maintenance_finishes_each_pass_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(thumbnail_disk_cache_module, "CACHE_MAINTENANCE_BATCH", 128)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    try:
        for index in range(256):
            path = tmp_path / f"{index:03d}.png"
            with Image.new("RGB", (8, 8), (index % 255, 1, 2)) as image:
                image.save(path)
            assert cache.put(_item(path), 64, _image())

        cache.prune(remove_orphans=False)
        assert cache.maintenance_pending
        cache.prune(remove_orphans=False)
        assert not cache.maintenance_pending
        assert cache._entry_scan_complete
    finally:
        cache.close()


def test_thumbnail_prune_keeps_active_staging_file(tmp_path: Path) -> None:
    cache = ThumbnailDiskCache(tmp_path / "cache")
    staging = cache.files_dir / ".active-publish.tmp"
    try:
        staging.write_bytes(b"in-flight")
        os.utime(staging, (1, 1))
        cache._active_staging_paths.add(os.path.normcase(str(staging)))

        cache.prune(remove_orphans=True)

        assert staging.exists()
    finally:
        cache.close()


def test_source_change_refusal_keeps_persistence_route_healthy(tmp_path: Path) -> None:
    source = tmp_path / "changed.png"
    source.write_bytes(b"before")
    item = _item(source)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    provider = BrowserThumbnailProvider(
        disk_cache=cache,
        disk_cache_enabled=True,
    )
    identity = ThumbnailSourceIdentity.capture(item)
    assert identity is not None
    result = ThumbnailLoadResult(
        _image(),
        source_identity=identity,
        persist_to_disk=True,
    )
    try:
        provider._save_drain_active = True
        assert provider._queue_thumbnail_save(
            item,
            64,
            result,
            ThumbnailPriority.BACKGROUND,
        )
        source.write_bytes(b"after!")

        provider._drain_thumbnail_saves()

        assert cache.last_put_status == "source_changed"
        assert provider.background_disk_cache_enabled
    finally:
        provider.close()


def test_save_queue_saturation_backpressures_without_settling_ram_entry(
    tmp_path: Path,
) -> None:
    source = tmp_path / "queued.png"
    source.write_bytes(b"fixture")
    item = _item(source)
    second_source = tmp_path / "queued-second.png"
    second_source.write_bytes(b"fixture-2")
    second_item = _item(second_source)
    third_source = tmp_path / "queued-third.png"
    third_source.write_bytes(b"fixture-3")
    third_item = _item(third_source)
    fourth_source = tmp_path / "queued-fourth.png"
    fourth_source.write_bytes(b"fixture-4")
    fourth_item = _item(fourth_source)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    provider = BrowserThumbnailProvider(
        disk_cache=cache,
        disk_cache_enabled=True,
    )
    save_size = ThumbnailRenderSpec.from_settings(64, "square_1_1", "letterbox")
    released: list[bool] = []
    provider.capacity_released.connect(lambda: released.append(True))
    provider._retry_save_max_items = 1
    identity = ThumbnailSourceIdentity.capture(item)
    assert identity is not None
    result = ThumbnailLoadResult(
        _image(),
        source_identity=identity,
        persist_to_disk=True,
    )
    try:
        provider._save_queue_max_items = 1
        provider._deferred_save_max_items = 1
        provider._save_drain_active = True
        assert not provider._queue_thumbnail_save(
            item,
            save_size,
            result,
            ThumbnailPriority.BACKGROUND,
        )
        second_identity = ThumbnailSourceIdentity.capture(second_item)
        assert second_identity is not None
        second_result = ThumbnailLoadResult(
            _image(),
            source_identity=second_identity,
            persist_to_disk=True,
        )
        assert not provider._queue_thumbnail_save(
            second_item,
            save_size,
            second_result,
            ThumbnailPriority.BACKGROUND,
        )
        third_identity = ThumbnailSourceIdentity.capture(third_item)
        assert third_identity is not None
        third_result = ThumbnailLoadResult(
            _image(),
            source_identity=third_identity,
            persist_to_disk=True,
        )
        third_cache_key = (
            provider._path_key(third_item.path),
            save_size.cache_token,
            third_item.thumbnail_revision,
        )
        provider._cache[third_cache_key] = _image()
        assert not provider._queue_thumbnail_save(
            third_item,
            save_size,
            third_result,
            ThumbnailPriority.BACKGROUND,
        )
        assert third_cache_key not in provider._cache
        assert provider.request_background(
            third_item, save_size, generation=provider._generation
        ) == "blocked"
        fourth_identity = ThumbnailSourceIdentity.capture(fourth_item)
        assert fourth_identity is not None
        fourth_cache_key = (
            provider._path_key(fourth_item.path),
            save_size.cache_token,
            fourth_item.thumbnail_revision,
        )
        provider._cache[fourth_cache_key] = _image()
        assert not provider._queue_thumbnail_save(
            fourth_item,
            save_size,
            ThumbnailLoadResult(
                _image(),
                source_identity=fourth_identity,
                persist_to_disk=True,
            ),
            ThumbnailPriority.BACKGROUND,
        )
        assert fourth_cache_key not in provider._cache
        assert provider.request_background(
            fourth_item, save_size, generation=provider._generation
        ) == "blocked"
        with provider._save_lock:
            assert len(provider._deferred_save_queue) == 1
            assert len(provider._retry_save_queue) == 1
            provider._save_drain_active = False
            provider._promote_deferred_saves_locked()
            assert len(provider._save_queue) == 1
        provider._drain_thumbnail_saves()
        assert cache.get(item, save_size.cache_token) is not None
        assert cache.get(second_item, save_size.cache_token) is not None
        assert provider.request_background(
            third_item, save_size, generation=provider._generation
        ) == "queued"
        assert released == [True]
    finally:
        provider.close()


def test_thumbnail_identity_rejects_same_size_mtime_replacement(tmp_path: Path) -> None:
    source = tmp_path / "replacement.png"
    with Image.new("RGB", (16, 16), "red") as image:
        image.save(source)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    try:
        item = _item(source)
        assert cache.put(item, 64, _image())
        original_mtime = source.stat().st_mtime_ns
        with Image.new("RGB", (16, 16), "blue") as image:
            image.save(source)
        os.utime(source, ns=(original_mtime, original_mtime))

        assert cache.get(_item(source), 64) is None
    finally:
        cache.close()


def test_thumbnail_identity_rejects_same_size_mtime_source_and_cover_replacement(
    tmp_path: Path,
) -> None:
    source = tmp_path / "same-source.bin"
    source.write_bytes(b"A" * 65536 + b"B" * 1024 + b"C" * 65536)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    try:
        item = _item(source)
        assert cache.put(item, 64, _image())
        source_mtime = source.stat().st_mtime_ns
        with source.open("r+b") as stream:
            stream.seek(65536)
            stream.write(b"X" * 1024)
        os.utime(source, ns=(source_mtime, source_mtime))
        assert cache.get(_item(source), 64) is None
    finally:
        cache.close()

    folder = tmp_path / "same-cover-folder"
    folder.mkdir()
    cover = folder / "cover.bin"
    cover.write_bytes(b"C" * 65536 + b"D" * 1024 + b"E" * 65536)
    folder_stat = folder.stat()
    folder_item = BrowserItem(
        folder.name,
        folder,
        BrowserItemKind.FOLDER,
        folder_stat.st_mtime,
        file_size=folder_stat.st_size,
        modified_time_ns=folder_stat.st_mtime_ns,
    )
    cache = ThumbnailDiskCache(tmp_path / "cover-cache")
    try:
        assert cache.put(folder_item, 64, _image(), cover_path=cover)
        cover_mtime = cover.stat().st_mtime_ns
        with cover.open("r+b") as stream:
            stream.seek(65536)
            stream.write(b"Y" * 1024)
        os.utime(cover, ns=(cover_mtime, cover_mtime))
        assert cache.get(folder_item, 64) is None
    finally:
        cache.close()


def test_metadata_only_rename_plan_does_not_measure_descendants(tmp_path: Path) -> None:
    source = tmp_path / "folder"
    source.mkdir()
    for index in range(128):
        (source / f"{index:03d}.txt").write_text("x", encoding="utf-8")
    planner = FileOperationPlanner()
    calls = 0
    original_measure = planner._measure

    def counted_measure(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_measure(*args, **kwargs)

    planner._measure = counted_measure
    plan = planner.prepare(
        FileOperationRequest(
            1,
            FileOperationKind.RENAME,
            (str(source),),
            new_name="renamed",
        )
    )

    assert plan.ready
    assert calls == 0


def test_pdf_cancelled_consumer_does_not_poison_same_render_key() -> None:
    service = PdfiumService(auto_probe=False)
    started = Event()
    release = Event()
    first_cancelled = Event()
    key = ("render", "same-document", 0)

    def blocked_operation() -> str:
        started.set()
        assert release.wait(2)
        return "first"

    try:
        first = service._submit(
            key,
            0,
            blocked_operation,
            cancel_token=first_cancelled,
            deduplicate=True,
        )
        assert started.wait(1)
        first_cancelled.set()

        second = service._submit(
            key,
            0,
            lambda: "second",
            cancel_token=Event(),
            deduplicate=True,
        )
        third = service._submit(
            key,
            0,
            lambda: "third",
            cancel_token=Event(),
            deduplicate=True,
        )

        assert second is not first
        assert third is second
        with pytest.raises(PdfBackendError) as exc_info:
            service._wait(first, first_cancelled)
        assert exc_info.value.code is PdfErrorCode.CANCELLED

        release.set()
        assert service._wait(second, Event()) == "second"
    finally:
        release.set()
        service.shutdown(wait_seconds=2)


def test_pdf_wait_detaches_cancelled_consumer_before_future_finishes() -> None:
    future: Future[str] = Future()
    token = Event()
    errors: list[PdfBackendError] = []

    def wait_for_result() -> None:
        try:
            PdfiumService._wait(future, token)
        except PdfBackendError as exc:
            errors.append(exc)

    thread = Thread(target=wait_for_result)
    thread.start()
    token.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert errors and errors[0].code is PdfErrorCode.CANCELLED
    future.set_result("late")


def test_pdf_cancelled_open_closes_document_after_late_completion() -> None:
    class RegisteringBackend:
        def __init__(self) -> None:
            self.open_started = Event()
            self.release_open = Event()
            self.opened: set[str] = set()
            self.closed: list[str] = []

        def open_document(self, _path, *, password=None, cancel_token=None):
            del password, cancel_token
            self.open_started.set()
            self.opened.add("late-document")
            assert self.release_open.wait(2)
            return SimpleNamespace(document_id="late-document")

        def close_document(self, document_id):
            self.opened.discard(str(document_id))
            self.closed.append(str(document_id))

        def close_all(self):
            self.opened.clear()

    backend = RegisteringBackend()
    service = PdfiumService(backend, auto_probe=False)
    token = Event()
    errors: list[PdfBackendError] = []

    def open_for_consumer() -> None:
        try:
            service.open_document("late.pdf", cancel_token=token)
        except PdfBackendError as exc:
            errors.append(exc)

    thread = Thread(target=open_for_consumer)
    thread.start()
    try:
        assert backend.open_started.wait(1)
        token.set()
        thread.join(timeout=1)
        assert not thread.is_alive()
        backend.release_open.set()
        deadline = monotonic() + 2.0
        while monotonic() < deadline and not backend.closed:
            sleep(0.005)
        assert errors and errors[0].code is PdfErrorCode.CANCELLED
        assert backend.closed == ["late-document"]
        assert not backend.opened
    finally:
        backend.release_open.set()
        service.shutdown(wait_seconds=2)
