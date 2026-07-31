from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import logging
from threading import Event, Lock, current_thread, main_thread
from time import monotonic
from unittest.mock import Mock
import zipfile

import pytest
from PIL import Image
from PySide6.QtCore import QRect, QRunnable, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from app.application_controller import ApplicationController
from app.book_session import BookSession
from app.browser_item_delegate import BrowserItemDelegate
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_sort import BrowserDisplayDensity
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.config_manager import ConfigManager
from app.image_cache import (
    CachedImage,
    ImageCache,
    _ImageLoadResult,
    _ImageLoadTask,
)
from app.image_source import (
    FolderListingSnapshot,
    ImageSource,
    create_image_source,
)
from app.image_work_coordinator import ImageWorkCoordinator, ImageWorkPriority
from app.page_model import PageModel
from app.pdf_backend import PageRenderSpec
from app.shell_icon_provider import ShellAssociatedIconProvider
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import (
    FRAME_RATIOS,
    SmartCropCache,
    ThumbnailRenderSpec,
    detect_smart_crop,
    frame_size_from_long_edge,
    normalized_center_crop,
    render_pil_thumbnail,
    smart_crop_cache_key,
)
from app.viewer_window import ViewerWindow


class FunctionRunnable(QRunnable):
    def __init__(self, function) -> None:
        super().__init__()
        self.function = function

    def run(self) -> None:
        self.function()


class CancellableRunnable(QRunnable):
    def __init__(self, *, block: bool = True) -> None:
        super().__init__()
        self.block = block
        self.started = Event()
        self.cancelled = Event()
        self.release = Event()
        self.finished = Event()
        self.cancel_calls = 0
        self.run_calls = 0
        self.finished_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.cancelled.set()

    def run(self) -> None:
        self.run_calls += 1
        self.started.set()
        if self.block:
            self.release.wait(2)
        self.finished_calls += 1
        self.finished.set()


class FakeImageWorkPool:
    def __init__(self, *, wait_result: bool = True) -> None:
        self.wait_result = wait_result
        self.started: list[QRunnable] = []
        self.clear_calls = 0
        self.wait_calls: list[int] = []

    def start(self, runnable: QRunnable, _priority: int) -> None:
        self.started.append(runnable)

    def clear(self) -> None:
        self.clear_calls += 1
        self.started.clear()

    def waitForDone(self, msecs: int) -> bool:
        self.wait_calls.append(msecs)
        return self.wait_result

    def tryTake(self, runnable: QRunnable) -> bool:
        if runnable not in self.started:
            return False
        self.started.remove(runnable)
        return True


def _drain_events(qapp, predicate, timeout: float = 3.0) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
    return predicate()


def test_image_work_shutdown_empty_is_successful_and_rejects_new_work() -> None:
    coordinator = ImageWorkCoordinator(max_workers=2)

    assert coordinator.shutdown(wait_msecs=0)
    assert coordinator.shutdown(wait_msecs=0)
    assert coordinator.last_shutdown_error is None
    assert not coordinator.start_viewer(
        FunctionRunnable(lambda: None),
        ImageWorkPriority.VIEWER_CURRENT,
    )
    assert not coordinator.start_browser(
        FunctionRunnable(lambda: None),
        ImageWorkPriority.BROWSER_VISIBLE,
    )


def test_image_work_shutdown_cancels_queued_without_owner_bookkeeping() -> None:
    coordinator = ImageWorkCoordinator(max_workers=2)
    viewer_pool = FakeImageWorkPool()
    browser_pool = FakeImageWorkPool()
    coordinator._viewer_pool = viewer_pool
    coordinator._browser_pool = browser_pool
    worker = CancellableRunnable(block=False)
    owner_tracking = {worker}

    assert coordinator.start_viewer(
        worker,
        ImageWorkPriority.VIEWER_CURRENT,
    )
    assert coordinator.shutdown(wait_msecs=0)

    assert worker.cancelled.is_set()
    assert worker.cancel_calls == 1
    assert worker.run_calls == 0
    assert viewer_pool.clear_calls == 1
    assert browser_pool.clear_calls == 1
    assert viewer_pool.started == []
    assert owner_tracking == {worker}


@pytest.mark.parametrize("lane", ["viewer", "browser"])
def test_image_work_shutdown_timeout_is_retryable_and_logged_once(
    lane: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordinator = ImageWorkCoordinator(max_workers=2)
    worker = CancellableRunnable()
    if lane == "viewer":
        assert coordinator.start_viewer(
            worker,
            ImageWorkPriority.VIEWER_CURRENT,
        )
    else:
        assert coordinator.start_browser(
            worker,
            ImageWorkPriority.BROWSER_VISIBLE,
        )
    assert worker.started.wait(1)

    with caplog.at_level(logging.ERROR, logger="app.image_work_coordinator"):
        assert not coordinator.shutdown(wait_msecs=0)
        assert not coordinator.shutdown(wait_msecs=0)

    assert worker.cancelled.is_set()
    assert worker.cancel_calls == 1
    assert not worker.finished.is_set()
    assert coordinator.last_shutdown_error is not None
    assert lane in coordinator.last_shutdown_error
    assert sum(
        record.name == "app.image_work_coordinator"
        and "Image worker shutdown timed out" in record.getMessage()
        for record in caplog.records
    ) == 1

    worker.release.set()
    assert worker.finished.wait(1)
    assert coordinator.shutdown(wait_msecs=1000)
    assert coordinator.last_shutdown_error is None
    assert worker.finished_calls == 1


def test_image_work_shutdown_succeeds_when_running_worker_exits_before_deadline() -> None:
    coordinator = ImageWorkCoordinator(max_workers=2)
    worker = CancellableRunnable()
    assert coordinator.start_viewer(
        worker,
        ImageWorkPriority.VIEWER_CURRENT,
    )
    assert worker.started.wait(1)

    with ThreadPoolExecutor(max_workers=1) as executor:
        shutdown = executor.submit(coordinator.shutdown, wait_msecs=1000)
        assert worker.cancelled.wait(1)
        assert not shutdown.done()
        worker.release.set()
        assert shutdown.result(timeout=2)

    assert worker.finished.is_set()
    assert worker.finished_calls == 1


def test_viewer_reserved_lane_runs_while_browser_lane_is_busy(qapp):
    coordinator = ImageWorkCoordinator(max_workers=2)
    browser_started = Event()
    release_browser = Event()
    viewer_completed = Event()
    starts: list[str] = []
    lock = Lock()

    def slow_browser() -> None:
        with lock:
            starts.append("browser")
        browser_started.set()
        release_browser.wait(2)

    def current_viewer() -> None:
        with lock:
            starts.append("viewer")
        viewer_completed.set()

    assert coordinator.start_browser(
        FunctionRunnable(slow_browser),
        ImageWorkPriority.BROWSER_VISIBLE,
    )
    assert browser_started.wait(1)
    coordinator.start_viewer(
        FunctionRunnable(current_viewer),
        ImageWorkPriority.VIEWER_CURRENT,
    )
    assert viewer_completed.wait(1)
    assert starts[:2] == ["browser", "viewer"]
    release_browser.set()
    coordinator.shutdown()


def test_viewer_gate_holds_new_browser_decode_until_resumed(qapp, tmp_path):
    source = tmp_path / "cover.jpg"
    with Image.new("RGB", (20, 30), "white") as image:
        image.save(source)
    item = BrowserItem(
        source.name,
        source,
        BrowserItemKind.IMAGE,
        source.stat().st_mtime,
    )
    coordinator = ImageWorkCoordinator(max_workers=2)
    calls: list[str] = []

    def loader(_item, _size):
        calls.append(current_thread().name)
        return QImage(16, 16, QImage.Format.Format_RGB32)

    provider = BrowserThumbnailProvider(
        loader=loader,
        image_work_coordinator=coordinator,
    )
    generation = provider.generation
    coordinator.begin_viewer_interactive()
    assert not provider.request(
        item,
        128,
        generation=generation,
        priority=ThumbnailPriority.VISIBLE,
    )
    assert calls == []
    coordinator.end_viewer_interactive()
    assert provider.request(item, 128, generation=generation)
    assert provider.wait_for_done(2000)
    qapp.processEvents()
    assert len(calls) == 1
    provider.close()
    coordinator.shutdown()


def test_application_gate_resumes_browser_after_first_frame(qapp, tmp_path):
    path = tmp_path / "最初の表示.webp"
    with Image.new("RGB", (320, 480), "#335577") as image:
        image.save(path, "WEBP", lossless=True)
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
    )
    controller.start()
    viewer = controller.open_path(path)

    assert controller.image_work_coordinator.browser_paused
    assert viewer.image_cache.wait_for_done(3000)
    assert _drain_events(
        qapp,
        lambda: not controller.image_work_coordinator.browser_paused,
    )
    assert viewer.viewer._last_draw_layout
    for window in tuple(controller.viewer_windows):
        window.close()
    browser = controller.get_browser_window()
    if browser is not None:
        browser.close()
    qapp.processEvents()
    controller.shutdown()


class OrderedSource(ImageSource):
    load_sizes_lazily = True

    def __init__(
        self,
        root: Path,
        *,
        block_partner: bool = False,
    ) -> None:
        super().__init__(root)
        self.ids = ["page0.webp", "page1.webp", "page2.webp"]
        self.started: list[str] = []
        self.threads: list[object] = []
        self.partner_started = Event()
        self.release_partner = Event()
        self.block_partner = block_partner

    def list_images(self) -> list[str]:
        return list(self.ids)

    def open_qimage(self, image_id: str) -> QImage | None:
        self.started.append(image_id)
        self.threads.append(current_thread())
        if image_id == "page1.webp" and self.block_partner:
            self.partner_started.set()
            self.release_partner.wait(3)
        image = QImage(80, 120, QImage.Format.Format_RGB32)
        image.fill(QColor("#f0f0f0"))
        return image

    def open_image(self, image_id: str) -> Image.Image:
        raise AssertionError(f"Pillow fallback was not expected: {image_id}")

    def display_path(self, image_id: str) -> str:
        return image_id


class QueuedLoadSource(ImageSource):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.ids = ["running.jpg", "queued.jpg"]
        self.started: list[str] = []
        self.running_started = Event()
        self.release_running = Event()

    def list_images(self) -> list[str]:
        return list(self.ids)

    def open_image(self, image_id: str) -> Image.Image:
        self.started.append(image_id)
        if image_id == "running.jpg":
            self.running_started.set()
            assert self.release_running.wait(2)
        return Image.new("RGB", (8, 12), "white")

    def display_path(self, image_id: str) -> str:
        return image_id


class TargetDecodeImageSource(ImageSource):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.target_calls: list[tuple[str, tuple[int, int]]] = []
        self.full_calls: list[str] = []

    def list_images(self) -> list[str]:
        return ["large.jpg"]

    def open_qimage_at_most(
        self,
        image_id: str,
        maximum_size: tuple[int, int],
    ) -> tuple[QImage, tuple[int, int]]:
        self.target_calls.append((image_id, maximum_size))
        image = QImage(
            maximum_size[0],
            maximum_size[1],
            QImage.Format.Format_RGB32,
        )
        image.fill(QColor("#f0f0f0"))
        return image, (4096, 6500)

    def open_image(self, image_id: str) -> Image.Image:
        self.full_calls.append(image_id)
        return Image.new("RGB", (4096, 6500), "white")

    def display_path(self, image_id: str) -> str:
        return image_id


def _cached_qimage(
    index: int,
    width: int,
    height: int,
    *,
    generation: int = 0,
    image_id: str | None = None,
    error: str | None = None,
    render_spec_signature: tuple[object, ...] | None = None,
) -> CachedImage:
    qimage = None
    original_size = None
    if error is None:
        qimage = QImage(width, height, QImage.Format.Format_RGBA8888)
        qimage.fill(QColor("#f0f0f0"))
        original_size = (width, height)
    return CachedImage(
        page_index=index,
        image_id=image_id or f"page{index}.jpg",
        qimage=qimage,
        original_size=original_size,
        error=error,
        generation=generation,
        rendered_size=original_size,
        render_spec_signature=render_spec_signature,
    )


def _cached_image_with_byte_cost(
    index: int,
    byte_cost: int,
    *,
    generation: int = 0,
) -> CachedImage:
    qimage = Mock()
    qimage.isNull.return_value = False
    qimage.sizeInBytes.return_value = byte_cost
    return CachedImage(
        page_index=index,
        image_id=f"page{index}.jpg",
        qimage=qimage,
        original_size=(1, 1),
        error=None,
        generation=generation,
        rendered_size=(1, 1),
    )


def _install_synchronous_cache_loader(
    cache: ImageCache,
    byte_cost: int,
) -> dict[int, int]:
    decode_counts: dict[int, int] = {}

    def ensure_loaded(index: int) -> None:
        if index in cache._cache:
            return
        decode_counts[index] = decode_counts.get(index, 0) + 1
        cache._store_cached(
            _cached_image_with_byte_cost(
                index,
                byte_cost,
                generation=cache.generation,
            )
        )
        cache._enforce_limit()

    cache.ensure_loaded = ensure_loaded  # type: ignore[method-assign]
    return decode_counts


def test_image_cache_tracks_qimage_bytes_on_hit_replacement_and_clear(qapp):
    cache = ImageCache(cache_size=10)
    cache._cache_byte_budget = 512 * 1024 * 1024

    cache._store_cached(_cached_qimage(0, 512, 512))
    assert cache._cache_bytes == 512 * 512 * 4
    assert cache._cache_entry_bytes == {0: 512 * 512 * 4}

    assert cache.get(0) is not None
    assert cache._cache_bytes == 512 * 512 * 4

    cache._store_cached(_cached_qimage(0, 1920, 1080))
    assert cache._cache_bytes == 1920 * 1080 * 4
    assert cache._cache_entry_bytes == {0: 1920 * 1080 * 4}

    cache.clear()
    assert cache._cache == {}
    assert cache._cache_entry_bytes == {}
    assert cache._cache_bytes == 0


def test_image_cache_target_decode_can_upgrade_preview_to_full_source(
    tmp_path: Path,
    qapp,
) -> None:
    source = TargetDecodeImageSource(tmp_path)
    cache = ImageCache(cache_size=3)
    cache.set_source(source, source.list_images())
    cache.set_raster_decode_bounds((1361, 2160))
    cache.preload_around(0, radius=0, visible_indexes=(0,))
    assert cache.wait_for_done()
    qapp.processEvents()

    preview = cache.get(0)
    assert preview is not None
    assert preview.source_is_preview
    assert preview.original_size == (4096, 6500)
    assert preview.qimage is not None
    assert (preview.qimage.width(), preview.qimage.height()) == (1361, 2160)
    assert source.target_calls == [("large.jpg", (1361, 2160))]
    assert not source.full_calls

    assert cache.ensure_full_resolution(0)
    assert cache.wait_for_done()
    qapp.processEvents()

    full = cache.get(0)
    assert full is not None
    assert not full.source_is_preview
    assert full.original_size == (4096, 6500)
    assert source.full_calls == ["large.jpg"]


@pytest.mark.parametrize(
    ("image_format", "width", "height"),
    (
        (QImage.Format.Format_RGB888, 601, 800),
        (QImage.Format.Format_RGB32, 601, 800),
        (QImage.Format.Format_RGBA8888, 601, 800),
    ),
)
def test_image_cache_byte_estimate_uses_qimage_stride(
    qapp,
    image_format,
    width,
    height,
):
    image = QImage(width, height, image_format)
    cached = CachedImage(
        page_index=0,
        image_id="stride.png",
        qimage=image,
        original_size=(width, height),
        error=None,
        generation=0,
    )

    assert ImageCache._estimate_cached_bytes(cached) == (
        image.bytesPerLine() * image.height()
    )
    assert ImageCache._estimate_cached_bytes(cached) == image.sizeInBytes()


@pytest.mark.parametrize(
    ("budget_mib", "byte_cost", "expected_entries"),
    (
        (128, 600 * 800 * 4, 10),
        (128, 2400 * 3200 * 4, 4),
        (128, 4000 * 6000 * 4, 1),
        (256, 600 * 800 * 4, 10),
        (256, 2400 * 3200 * 4, 8),
        (256, 4000 * 6000 * 4, 2),
        (512, 600 * 800 * 4, 10),
        (512, 2400 * 3200 * 4, 10),
        (512, 4000 * 6000 * 4, 5),
    ),
)
def test_image_cache_presets_bound_large_image_retention(
    qapp,
    budget_mib,
    byte_cost,
    expected_entries,
):
    cache = ImageCache(cache_size=10)
    cache._cache_byte_budget = budget_mib * 1024 * 1024
    cache._center_index = 9
    for index in range(10):
        cache._store_cached(_cached_image_with_byte_cost(index, byte_cost))
        cache._enforce_limit()

    assert len(cache._cache) == expected_entries
    assert cache._center_index in cache._cache
    assert cache._cache_bytes == expected_entries * byte_cost


def test_large_single_prefetch_retains_next_page_across_navigation(qapp):
    cache = ImageCache(cache_size=10)
    cache._cache_byte_budget = 256 * 1024 * 1024
    source = Mock()
    source.supports_target_rendering = False
    image_ids = [f"page{index}.jpg" for index in range(12)]
    cache.set_source(source, image_ids)
    decode_counts = _install_synchronous_cache_loader(
        cache,
        4000 * 6000 * 4,
    )

    def navigate(
        center: int,
        direction: int,
        prefetch: tuple[int, ...],
    ) -> bool:
        before = decode_counts.get(center, 0)
        cache.preload_around(
            center,
            radius=0,
            visible_indexes=(center,),
            preferred_direction=direction,
            prefetch_indexes=prefetch,
        )
        return decode_counts.get(center, 0) == before

    assert not navigate(0, 0, (1, 2, 3))
    assert tuple(cache._cache) == (0, 1)
    assert navigate(1, 1, (2, 3, 4, 0))
    assert navigate(2, 1, (3, 4, 5, 1, 0))
    assert navigate(3, 1, (4, 5, 6, 2, 1, 0))
    assert navigate(4, 1, (5, 6, 7, 3, 2, 1))
    assert not navigate(3, -1, (2, 1, 0, 4, 5, 6))
    assert navigate(2, -1, (1, 0, 3, 4, 5))
    assert not navigate(3, 1, (4, 5, 6, 2, 1, 0))
    assert navigate(4, 1, (5, 6, 7, 3, 2, 1))
    assert decode_counts == {
        0: 1,
        1: 2,
        2: 2,
        3: 3,
        4: 2,
        5: 2,
    }


def test_large_spread_protects_current_pair_and_nearest_prefetch(qapp):
    cache = ImageCache(cache_size=10)
    cache._cache_byte_budget = 256 * 1024 * 1024
    source = Mock()
    source.supports_target_rendering = False
    image_ids = [f"page{index}.jpg" for index in range(12)]
    cache.set_source(source, image_ids)
    _install_synchronous_cache_loader(cache, 4000 * 6000 * 3)

    cache.preload_around(
        0,
        radius=0,
        visible_indexes=(0, 1),
        preferred_direction=1,
        prefetch_indexes=(2, 3, 4, 5),
    )

    assert set(cache._cache) == {0, 1, 2}
    assert cache._cache_bytes == 3 * 4000 * 6000 * 3


def test_first_large_result_removes_distant_queued_prefetch(
    qapp,
    tmp_path,
    monkeypatch,
):
    class LargeCostSource(ImageSource):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.ids = [f"page{index}.jpg" for index in range(8)]
            self.started: list[str] = []
            self.second_started = Event()
            self.release_second = Event()

        def list_images(self) -> list[str]:
            return list(self.ids)

        def open_image(self, image_id: str) -> Image.Image:
            self.started.append(image_id)
            if image_id == "page1.jpg":
                self.second_started.set()
                assert self.release_second.wait(2)
            return Image.new("RGB", (1, 1), "white")

        def display_path(self, image_id: str) -> str:
            return image_id

    def large_cost_qimage(_image):
        qimage = Mock()
        qimage.isNull.return_value = False
        qimage.sizeInBytes.return_value = 4000 * 6000 * 4
        return qimage

    monkeypatch.setattr(
        _ImageLoadTask,
        "_pil_to_qimage",
        staticmethod(large_cost_qimage),
    )
    source = LargeCostSource(tmp_path)
    cache = ImageCache(cache_size=10)
    cache._cache_byte_budget = 256 * 1024 * 1024
    cache.set_source(source, source.ids)

    cache.preload_around(
        0,
        radius=0,
        visible_indexes=(0,),
        preferred_direction=1,
        prefetch_indexes=(1, 2, 3, 4, 5, 6),
    )

    try:
        assert source.second_started.wait(1)
        qapp.processEvents()
    finally:
        source.release_second.set()
    assert _drain_events(qapp, lambda: not cache._tasks and not cache._in_flight)
    assert source.started == ["page0.jpg", "page1.jpg"]
    assert tuple(cache._cache) == (0, 1)


def test_image_cache_enforces_count_and_byte_lru_limits(qapp):
    count_limited = ImageCache(cache_size=2)
    count_limited._cache_byte_budget = 512 * 1024 * 1024
    count_limited._center_index = 2
    for index in range(3):
        count_limited._store_cached(_cached_qimage(index, 512, 512))
    count_limited._enforce_limit()

    assert tuple(count_limited._cache) == (1, 2)
    assert count_limited._cache_bytes == 2 * 512 * 512 * 4

    byte_limited = ImageCache(cache_size=10)
    one_mebibyte = 512 * 512 * 4
    byte_limited._cache_byte_budget = 2 * one_mebibyte
    byte_limited._center_index = 2
    byte_limited._store_cached(_cached_qimage(0, 512, 512))
    byte_limited._store_cached(_cached_qimage(1, 512, 512))
    assert byte_limited.get(0) is not None
    byte_limited._store_cached(_cached_qimage(2, 512, 512))
    byte_limited._enforce_limit()

    assert tuple(byte_limited._cache) == (0, 2)
    assert byte_limited._cache_bytes == 2 * one_mebibyte


@pytest.mark.parametrize(
    ("width", "height", "expected_entries", "expected_bytes"),
    (
        (512, 512, 10, 10_485_760),
        (1920, 1080, 10, 82_944_000),
        (3840, 2160, 8, 265_420_800),
        (7680, 4320, 2, 265_420_800),
    ),
)
def test_image_cache_default_budget_bounds_synthetic_render_sizes(
    qapp,
    width,
    height,
    expected_entries,
    expected_bytes,
):
    cache = ImageCache(cache_size=10)
    cache._center_index = 9
    image_bytes = width * height * 4
    for index in range(10):
        qimage = Mock()
        qimage.isNull.return_value = False
        qimage.sizeInBytes.return_value = image_bytes
        cache._store_cached(
            CachedImage(
                page_index=index,
                image_id=f"page{index}.pdf",
                qimage=qimage,
                original_size=(width, height),
                error=None,
                generation=0,
                rendered_size=(width, height),
            )
        )
        cache._enforce_limit()

    assert len(cache._cache) == expected_entries
    assert cache._cache_bytes == expected_bytes
    assert cache._center_index in cache._cache


def test_image_cache_byte_limit_prefers_current_and_spread_partner(qapp):
    cache = ImageCache(cache_size=10)
    one_mebibyte = 512 * 512 * 4
    cache._cache_byte_budget = 2 * one_mebibyte
    cache._center_index = 2
    cache._protected_indexes = {2, 3}
    cache._store_cached(_cached_qimage(2, 512, 512))
    cache._store_cached(_cached_qimage(3, 512, 512))
    cache._store_cached(_cached_qimage(1, 512, 512))
    cache._enforce_limit()

    assert tuple(cache._cache) == (2, 3)
    assert cache._cache_bytes == 2 * one_mebibyte

    cache._cache_byte_budget = one_mebibyte
    cache._enforce_limit()
    assert tuple(cache._cache) == (2, 3)
    assert cache._cache_bytes == 2 * one_mebibyte

    cache._cache_byte_budget = one_mebibyte // 2
    cache._enforce_limit()
    assert tuple(cache._cache) == (2, 3)
    assert cache._cache_bytes == 2 * one_mebibyte


def test_image_cache_releases_completed_prefetch_source_protection(qapp):
    cache = ImageCache(cache_size=10)
    cache.image_ids = [f"page{index}.jpg" for index in range(13)]
    entry_bytes = 80 * 1024 * 1024
    cache._cache_byte_budget = 128 * 1024 * 1024
    cache._center_index = 12
    cache._protected_indexes = {8, 12}
    cache._wanted_indexes = {8, 12}
    cache._store_cached(_cached_image_with_byte_cost(8, entry_bytes))
    cache._store_cached(_cached_image_with_byte_cost(12, entry_bytes))

    cache.retain_visible_only(12, (12,))

    assert tuple(cache._cache) == (12,)
    assert cache._cache_bytes == entry_bytes
    assert cache._protected_indexes == {12}
    assert cache._wanted_indexes == {12}


def test_image_cache_live_memory_budget_shrinks_with_lru_and_expands_in_place(
    qapp,
):
    cache = ImageCache(cache_size=10)
    entry_bytes = 32 * 1024 * 1024
    cache._center_index = 2
    cache._protected_indexes = {2, 3}
    for index in (0, 3, 2):
        qimage = Mock()
        qimage.isNull.return_value = False
        qimage.sizeInBytes.return_value = entry_bytes
        cache._store_cached(
            CachedImage(
                page_index=index,
                image_id=f"page{index}.pdf",
                qimage=qimage,
                original_size=(1, 1),
                error=None,
                generation=0,
                rendered_size=(1, 1),
            )
        )

    cache.set_cache_byte_budget_mib(64)

    assert tuple(cache._cache) == (3, 2)
    assert cache._cache_bytes == 2 * entry_bytes
    assert cache.cache_byte_budget_mib == 64

    before_expansion = tuple(cache._cache)
    cache.set_cache_byte_budget_mib(512)

    assert tuple(cache._cache) == before_expansion
    assert cache.cache_byte_budget_mib == 512


def test_image_cache_rejected_results_do_not_add_bytes(qapp, tmp_path):
    source = OrderedSource(tmp_path)
    cache = ImageCache()
    cache.set_source(source, source.ids)
    generation = cache.generation

    cache._on_loaded(
        _ImageLoadResult(
            _cached_qimage(0, 512, 512, generation=generation),
            source,
            cancelled=True,
        )
    )
    cache._on_loaded(
        _ImageLoadResult(
            _cached_qimage(1, 512, 512, generation=generation - 1),
            source,
        )
    )
    cache._on_loaded(
        _ImageLoadResult(
            _cached_qimage(
                2,
                0,
                0,
                generation=generation,
                image_id=source.ids[2],
                error="decode failed",
            ),
            source,
        )
    )

    assert tuple(cache._cache) == (2,)
    assert cache._cache_entry_bytes == {2: 0}
    assert cache._cache_bytes == 0


def test_image_cache_render_spec_and_source_changes_reset_byte_tracking(
    qapp,
    tmp_path,
):
    cache = ImageCache()
    first_spec = PageRenderSpec(1920, 1080, size_bucket=(1920, 1080))
    second_spec = PageRenderSpec(3840, 2160, size_bucket=(3840, 2160))
    assert cache.set_render_spec(first_spec)
    first_signature = cache._page_render_spec_signature(first_spec)
    cache._store_cached(
        _cached_qimage(
            0,
            1920,
            1080,
            generation=cache.generation,
            render_spec_signature=first_signature,
        )
    )

    assert cache.set_render_spec(second_spec)
    assert cache._cache == {}
    assert cache._cache_entry_bytes == {}
    assert cache._cache_bytes == 0

    cache._store_cached(_cached_qimage(0, 512, 512))
    source = OrderedSource(tmp_path)
    cache.set_source(source, source.ids)
    assert cache._cache == {}
    assert cache._cache_entry_bytes == {}
    assert cache._cache_bytes == 0


def test_adjustment_change_cancels_old_generation_without_running_queued_decode(
    qapp,
    tmp_path,
):
    coordinator = ImageWorkCoordinator(max_workers=2)
    source = QueuedLoadSource(tmp_path)
    cancelled: list[str] = []
    source.cancel_image_request = cancelled.append
    cache = ImageCache(image_work_coordinator=coordinator)
    delivered: list[str] = []
    cache.pageLoaded.connect(lambda cached: delivered.append(cached.image_id))
    cache.set_source(source, source.ids)
    stale_generation = cache.generation
    cache.ensure_loaded(0)
    assert source.running_started.wait(1)
    cache.ensure_loaded(1)

    cache.set_adjustments(brightness=1.1, contrast=1.0, gamma=1.0)
    current_generation = cache.generation
    cache.ensure_loaded(1)

    assert cancelled == source.ids
    assert (stale_generation, 0) in cache._tasks
    assert (stale_generation, 1) not in cache._tasks
    assert (current_generation, 1) in cache._tasks
    assert source.started == ["running.jpg"]

    source.release_running.set()
    assert coordinator.wait_for_viewer(2000)
    qapp.processEvents()

    assert source.started == ["running.jpg", "queued.jpg"]
    assert delivered == ["queued.jpg"]
    assert cache.get(1) is not None
    assert cache._tasks == {}
    assert cache._in_flight == {}
    coordinator.shutdown()


def test_image_cache_registers_logical_current_before_rtl_partner(qapp, tmp_path):
    coordinator = ImageWorkCoordinator(max_workers=2)
    source = OrderedSource(tmp_path)
    cache = ImageCache(image_work_coordinator=coordinator)
    cache.set_source(source, source.ids)
    cache.preload_around(0, radius=1, visible_indexes=(1, 0))
    assert cache.wait_for_done(2000)
    qapp.processEvents()

    assert source.started[0] == "page0.webp"
    assert all(thread is not main_thread() for thread in source.threads)
    coordinator.shutdown()


def test_image_cache_releases_queued_tracking_before_coordinator_clear(
    qapp,
    tmp_path,
):
    coordinator = ImageWorkCoordinator(max_workers=2)
    source = QueuedLoadSource(tmp_path)
    cache = ImageCache(image_work_coordinator=coordinator)
    idle_sources: list[ImageSource] = []
    delivered: list[str] = []
    cache.sourceIdle.connect(idle_sources.append)
    cache.pageLoaded.connect(lambda cached: delivered.append(cached.image_id))
    cache.set_source(source, source.ids)
    generation = cache.generation
    cache.ensure_loaded(0)
    assert source.running_started.wait(1)
    cache.ensure_loaded(1)
    assert set(cache._tasks) == {(generation, 0), (generation, 1)}

    cache.clear()
    cache.clear()
    assert not coordinator.shutdown(wait_msecs=0)

    assert (generation, 0) in cache._tasks
    assert (generation, 1) not in cache._tasks
    assert source.started == ["running.jpg"]

    source.release_running.set()
    assert coordinator.wait_for_viewer(2000)
    qapp.processEvents()
    assert cache._tasks == {}
    assert cache._in_flight == {}
    assert idle_sources == [source]
    assert delivered == []
    assert coordinator.shutdown()


def test_stale_running_image_load_does_not_remove_new_generation_task(
    qapp,
    tmp_path,
):
    coordinator = ImageWorkCoordinator(max_workers=2)
    source = QueuedLoadSource(tmp_path)
    cache = ImageCache(image_work_coordinator=coordinator)
    delivered: list[str] = []
    cache.pageLoaded.connect(lambda cached: delivered.append(cached.image_id))
    cache.set_source(source, source.ids)
    stale_generation = cache.generation
    cache.ensure_loaded(0)
    assert source.running_started.wait(1)
    cache.ensure_loaded(1)

    cache.clear()
    cache.set_source(source, source.ids)
    current_generation = cache.generation
    cache.ensure_loaded(1)

    assert (stale_generation, 0) in cache._tasks
    assert (stale_generation, 1) not in cache._tasks
    assert (current_generation, 1) in cache._tasks

    source.release_running.set()
    assert coordinator.wait_for_viewer(2000)
    qapp.processEvents()
    assert cache._tasks == {}
    assert cache._in_flight == {}
    assert source.started == ["running.jpg", "queued.jpg"]
    assert delivered == ["queued.jpg"]
    assert cache.get(1) is not None
    coordinator.shutdown()


def test_pending_viewer_prefetch_is_promoted_when_it_becomes_current(
    qapp,
    tmp_path,
):
    coordinator = ImageWorkCoordinator(max_workers=2)
    source = OrderedSource(tmp_path)
    first_started = Event()
    release_first = Event()
    original_open = source.open_qimage

    def blocking_open(image_id: str):
        if image_id == "page0.webp":
            first_started.set()
            release_first.wait(2)
        return original_open(image_id)

    source.open_qimage = blocking_open  # type: ignore[method-assign]
    cache = ImageCache(image_work_coordinator=coordinator)
    cache.set_source(source, source.ids)
    cache.preload_around(0, radius=2, visible_indexes=(0,))
    assert first_started.wait(1)
    cache.preload_around(2, radius=2, visible_indexes=(2,))
    release_first.set()
    assert cache.wait_for_done(3000)
    qapp.processEvents()

    assert source.started[:2] == ["page0.webp", "page2.webp"]
    coordinator.shutdown()


def test_pending_prefetch_priority_is_downgraded_after_queue_reversal(
    tmp_path,
    monkeypatch,
):
    source = OrderedSource(tmp_path)
    cache = ImageCache()
    cache.set_source(source, source.ids)
    starts: list[int] = []
    monkeypatch.setattr(
        cache,
        "_start_task",
        lambda _task, priority: starts.append(int(priority)),
    )
    monkeypatch.setattr(cache, "_try_take_task", lambda _task: True)
    cache._configured_prefetch_order = True
    cache._configured_prefetch_ranks = {1: 0}

    cache.ensure_loaded(1)
    first_priority = int(cache._tasks[(cache.generation, 1)][1])
    cache._configured_prefetch_ranks = {1: 7}
    cache.ensure_loaded(1)
    reversed_priority = int(cache._tasks[(cache.generation, 1)][1])

    assert reversed_priority < first_priority
    assert starts == [first_priority, reversed_priority]


def test_folder_prefetch_uses_custom_display_units_without_pdf_idle_timer(
    qapp,
    tmp_path,
):
    source = OrderedSource(tmp_path)
    source.ids = [f"page{index}.webp" for index in range(15)]
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(
        {
            "view_mode": "single",
            "viewer_prefetch_preset": "custom",
            "viewer_prefetch_image_forward_units": 2,
            "viewer_prefetch_image_backward_units": 1,
        }
    )
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
    )
    window = ViewerWindow(
        config_manager=config,
        book_session=session,
    )
    try:
        def raster_prefetch_idle() -> bool:
            return (
                window._raster_prefetch_plan is None
                and not window.image_cache.has_unfinished_tasks()
                and not window.viewer.has_pending_prepared_rendering()
            )

        opened = session.open_book(tmp_path)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        assert _drain_events(
            qapp,
            lambda: window.viewer.displayed_page_indexes == (0,),
        )
        window.viewer.render(QPixmap(window.viewer.size()))
        assert _drain_events(
            qapp,
            lambda: source.started
            == ["page0.webp", "page1.webp", "page2.webp"],
        )
        assert _drain_events(qapp, raster_prefetch_idle)

        assert not window._pdf_prefetch_timer.isActive()
        assert window._configured_prefetch_indexes(
            5,
            (5,),
            forward_units=2,
            backward_units=1,
            direction=1,
        ) == (6, 4, 7)

        source.started.clear()
        window.model.go_to_index(5)
        window._refresh_view()
        assert _drain_events(
            qapp,
            lambda: window.viewer.displayed_page_indexes == (5,),
        )
        window.viewer.render(QPixmap(window.viewer.size()))
        assert _drain_events(
            qapp,
            lambda: source.started
            == [
                "page5.webp",
                "page6.webp",
                "page4.webp",
                "page7.webp",
            ],
        )
        assert _drain_events(qapp, raster_prefetch_idle)

        source.started.clear()
        window.model.go_to_index(6)
        window._refresh_view()
        assert _drain_events(
            qapp,
            lambda: window.viewer.displayed_page_indexes == (6,),
        )
        window.viewer.render(QPixmap(window.viewer.size()))
        assert _drain_events(
            qapp,
            lambda: source.started == ["page8.webp"],
        ), source.started
        assert _drain_events(qapp, raster_prefetch_idle)
    finally:
        window.prepare_shutdown(wait_msecs=3000)
        window.close()
        qapp.processEvents()
    session.shutdown(wait_msecs=3000)


def test_jump_removes_old_queued_prefetch_before_new_nearby_work(
    qapp,
    tmp_path,
):
    class JumpSource(ImageSource):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.ids = [f"page{index}.jpg" for index in range(30)]
            self.started: list[str] = []
            self.cancelled: list[str] = []
            self.current_started = Event()
            self.release_current = Event()

        def list_images(self) -> list[str]:
            return list(self.ids)

        def open_image(self, image_id: str) -> Image.Image:
            self.started.append(image_id)
            if image_id == "page0.jpg":
                self.current_started.set()
                assert self.release_current.wait(2)
            return Image.new("RGB", (8, 12), "white")

        def cancel_image_request(self, image_id: str) -> None:
            self.cancelled.append(image_id)

        def display_path(self, image_id: str) -> str:
            return image_id

    coordinator = ImageWorkCoordinator(max_workers=2)
    source = JumpSource(tmp_path)
    cache = ImageCache(image_work_coordinator=coordinator)
    cache.set_source(source, source.ids)
    cache.preload_around(0, radius=3, visible_indexes=(0,))
    assert source.current_started.wait(1)
    generation = cache.generation
    assert {
        (generation, index)
        for index in range(4)
    }.issubset(cache._tasks)

    cache.preload_around(20, radius=3, visible_indexes=(20,))

    assert (generation, 0) in cache._tasks
    assert all(
        (generation, index) not in cache._tasks
        for index in (1, 2, 3)
    )
    assert all(
        (generation, index) in cache._tasks
        for index in range(17, 24)
    )

    source.release_current.set()
    assert cache.wait_for_done(3000)
    qapp.processEvents()

    assert source.started == [
        "page0.jpg",
        "page20.jpg",
        "page21.jpg",
        "page22.jpg",
        "page23.jpg",
        "page17.jpg",
        "page18.jpg",
        "page19.jpg",
    ]
    assert all(
        old_prefetch not in source.started
        for old_prefetch in ("page1.jpg", "page2.jpg", "page3.jpg")
    )
    assert cache._tasks == {}
    assert cache._in_flight == {}
    coordinator.shutdown()


def test_spread_keeps_complete_previous_unit_until_slow_partner_is_ready(
    qapp,
    tmp_path,
):
    source = OrderedSource(tmp_path)
    source.ids = [f"page{index}.webp" for index in range(4)]
    target_partner_started = Event()
    release_target_partner = Event()
    original_open = source.open_qimage

    def block_target_partner(image_id: str):
        if image_id == "page3.webp":
            target_partner_started.set()
            assert release_target_partner.wait(3)
        return original_open(image_id)

    source.open_qimage = block_target_partner  # type: ignore[method-assign]
    coordinator = ImageWorkCoordinator(max_workers=2)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(
        {
            "view_mode": "spread",
            "single_first_page": False,
            "viewer_prefetch_preset": "custom",
            "viewer_prefetch_image_forward_units": 0,
            "viewer_prefetch_image_backward_units": 0,
        }
    )
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
        image_work_coordinator=coordinator,
    )
    window = ViewerWindow(
        config_manager=config,
        book_session=session,
        image_work_coordinator=coordinator,
    )
    window.resize(640, 480)
    window.show()
    try:
        def current_paint_pixmap_keys() -> tuple[int, ...]:
            layout = window.viewer._layout_for_current_images()
            assert len(layout.rects) == len(window.viewer._images)
            pixmaps = tuple(
                window.viewer._pixmap_for_paint(image, rect)
                for image, rect in zip(window.viewer._images, layout.rects)
            )
            assert all(pixmap is not None for pixmap in pixmaps)
            return tuple(
                pixmap.cacheKey()
                for pixmap in pixmaps
                if pixmap is not None
            )

        assert window.open_path(tmp_path / "dummy.webp")
        assert _drain_events(
            qapp,
            lambda: len(window.viewer._images) == 2
            and {
                image.image_id for image in window.viewer._images
            }
            == {"page0.webp", "page1.webp"},
        )
        assert all(not image.loading for image in window.viewer._images)
        previous_images = tuple(window.viewer._images)
        previous_pixmap_keys = current_paint_pixmap_keys()
        applied: list[tuple[str, ...]] = []
        original_set_pages = window.viewer.set_pages

        def record_set_pages(spread, pages):
            applied.append(tuple(image.image_id for image in pages))
            original_set_pages(spread, pages)

        window.viewer.set_pages = record_set_pages  # type: ignore[method-assign]
        window.model.go_to_index(2)
        window._refresh_view()
        assert _drain_events(qapp, target_partner_started.is_set, timeout=2)
        qapp.processEvents()

        assert applied == []
        assert tuple(window.viewer._images) == previous_images
        assert current_paint_pixmap_keys() == previous_pixmap_keys
        assert not any(image.loading for image in window.viewer._images)

        release_target_partner.set()
        assert session.image_cache.wait_for_done(2000)
        assert _drain_events(
            qapp,
            lambda: len(window.viewer._images) == 2
            and {
                image.image_id for image in window.viewer._images
            }
            == {"page2.webp", "page3.webp"},
        )
        assert len(applied) == 1
        assert set(applied[0]) == {"page2.webp", "page3.webp"}
        assert all(not image.loading for image in window.viewer._images)
        assert len(current_paint_pixmap_keys()) == 2
    finally:
        release_target_partner.set()
        session.image_cache.wait_for_done(3000)
        window.close()
        qapp.processEvents()
        coordinator.shutdown()


def test_webp_qimagereader_preserves_rgb_and_rgba(tmp_path):
    rgb_path = tmp_path / "日本語 RGB.webp"
    rgba_path = tmp_path / "日本語 RGBA.webp"
    with Image.new("RGB", (96, 64), "#336699") as image:
        image.save(rgb_path, "WEBP", lossless=True)
    with Image.new("RGBA", (64, 96), (20, 40, 60, 80)) as image:
        image.save(rgba_path, "WEBP", lossless=True)
    source = create_image_source(rgb_path)[0]
    rgb = source.open_qimage(str(rgb_path))
    rgba = source.open_qimage(str(rgba_path))

    assert rgb is not None and not rgb.isNull()
    assert rgba is not None and not rgba.isNull()
    assert (rgb.width(), rgb.height()) == (96, 64)
    assert (rgba.width(), rgba.height()) == (64, 96)
    assert not rgb.hasAlphaChannel()
    assert rgba.hasAlphaChannel()
    assert rgba.pixelColor(10, 10).alpha() < 255
    source.close()


def test_animated_and_zip_webp_use_first_frame(tmp_path):
    animated = tmp_path / "日本語 animation.webp"
    first = Image.new("RGB", (48, 32), "red")
    second = Image.new("RGB", (48, 32), "blue")
    first.save(
        animated,
        "WEBP",
        save_all=True,
        append_images=[second],
        duration=100,
        loop=0,
        lossless=True,
    )
    source = create_image_source(animated)[0]
    qimage = source.open_qimage(str(animated))
    assert qimage is not None
    color = qimage.pixelColor(10, 10)
    assert color.red() > color.blue()
    source.close()

    archive = tmp_path / "日本語.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(animated, "サブ/表紙.webp")
    zip_source = create_image_source(archive)[0]
    image_id = zip_source.list_images()[0]
    zipped = zip_source.open_qimage(image_id)
    assert zipped is not None
    zip_color = zipped.pixelColor(10, 10)
    assert zip_color.red() > zip_color.blue()
    zip_source.close()


def test_qimagereader_failure_falls_back_to_pillow_once(qapp, tmp_path):
    path = tmp_path / "fallback.webp"
    with Image.new("RGB", (40, 60), "navy") as image:
        image.save(path, "WEBP")
    source = create_image_source(path)[0]
    calls = {"qt": 0, "pillow": 0}
    original_open = source.open_image

    def fail_qt(_image_id):
        calls["qt"] += 1
        return None

    def count_pillow(image_id):
        calls["pillow"] += 1
        return original_open(image_id)

    source.open_qimage = fail_qt  # type: ignore[method-assign]
    source.open_image = count_pillow  # type: ignore[method-assign]
    cache = ImageCache()
    cache.set_source(source, [str(path)])
    cache.preload_around(0, visible_indexes=(0,))
    assert cache.wait_for_done(2000)
    qapp.processEvents()
    assert cache.get(0) is not None
    cache.preload_around(0, visible_indexes=(0,))
    assert calls == {"qt": 1, "pillow": 1}
    source.close()


def test_folder_snapshot_avoids_second_directory_listing(tmp_path, monkeypatch):
    folder = tmp_path / "本"
    folder.mkdir()
    first = folder / "1.jpg"
    second = folder / "2.jpg"
    for path in (first, second):
        with Image.new("RGB", (8, 12), "white") as image:
            image.save(path)
    snapshot = FolderListingSnapshot(
        folder,
        (str(first), str(second)),
        str(second),
    )
    source, selected = create_image_source(second, folder_snapshot=snapshot)
    monkeypatch.setattr(Path, "iterdir", lambda _path: (_ for _ in ()).throw(
        AssertionError("folder was enumerated again")
    ))

    assert source.list_images() == [str(first), str(second)]
    assert selected == str(second)
    source.close()


def test_lazy_folder_page_model_does_not_probe_all_dimensions(tmp_path, monkeypatch):
    folder = tmp_path / "多数WebP"
    folder.mkdir()
    paths: list[Path] = []
    for index in range(20):
        path = folder / f"{index:02d}.webp"
        with Image.new("RGB", (20, 30), "white") as image:
            image.save(path, "WEBP")
        paths.append(path)
    source = create_image_source(paths[-1])[0]
    probes: list[str] = []

    def record_probe(image_id: str):
        probes.append(image_id)
        raise AssertionError("lazy PageModel must not probe headers on the GUI path")

    monkeypatch.setattr(source, "logical_size", record_probe)
    model = PageModel()
    model.set_source(source, str(paths[-1]))

    assert model.current_index == len(paths) - 1
    assert probes == []
    source.close()


def test_all_frame_ratio_presets_use_long_edge():
    expected = {
        "square_1_1": (180, 180),
        "landscape_3_2": (180, 120),
        "portrait_2_3": (120, 180),
        "landscape_4_3": (180, 135),
        "portrait_3_4": (135, 180),
        "landscape_16_9": (180, 101),
        "portrait_9_16": (101, 180),
        "landscape_sqrt2_1": (180, 127),
        "portrait_1_sqrt2": (127, 180),
    }
    assert set(expected) == set(FRAME_RATIOS)
    for ratio_id, size in expected.items():
        frame = frame_size_from_long_edge(180, ratio_id)
        assert (frame.width(), frame.height()) == size


def test_letterbox_and_center_crop_keep_aspect_without_stretching():
    image = Image.new("RGB", (200, 100), "red")
    letterbox = ThumbnailRenderSpec(100, 100, "square_1_1", "letterbox")
    center = ThumbnailRenderSpec(100, 100, "square_1_1", "center_crop")
    letterboxed, _ = render_pil_thumbnail(image, letterbox)
    cropped, crop = render_pil_thumbnail(image, center)

    assert (letterboxed.width(), letterboxed.height()) == (100, 50)
    assert (cropped.width(), cropped.height()) == (100, 100)
    assert crop == normalized_center_crop((200, 100), (100, 100))


def test_smart_crop_is_deterministic_and_keeps_off_center_content():
    image = Image.new("RGB", (300, 120), "white")
    for x in range(230, 285):
        for y in range(25, 100):
            image.putpixel((x, y), (0, 0, 0))
    first = detect_smart_crop(image, (100, 100))
    second = detect_smart_crop(image, (100, 100))

    assert first == second
    assert first[0] > 0.4
    assert first[0] + first[2] >= 0.9


def test_smart_crop_key_reuses_size_changes_but_not_ratio_changes(tmp_path):
    common = {
        "source_size": 123,
        "source_mtime_ns": 456,
        "entry_path": "page.webp",
    }
    portrait = smart_crop_cache_key(
        tmp_path / "book.zip",
        ratio_id="portrait_1_sqrt2",
        **common,
    )
    same_portrait = smart_crop_cache_key(
        tmp_path / "book.zip",
        ratio_id="portrait_1_sqrt2",
        **common,
    )
    square = smart_crop_cache_key(
        tmp_path / "book.zip",
        ratio_id="square_1_1",
        **common,
    )
    cache = SmartCropCache()
    cache.put(portrait, (0.1, 0.0, 0.8, 1.0))

    assert cache.get(same_portrait) == (0.1, 0.0, 0.8, 1.0)
    assert cache.get(square) is None


def test_two_dimensional_thumbnail_token_tracks_ratio_crop_and_bucket():
    first = ThumbnailRenderSpec.from_settings(
        250,
        "portrait_1_sqrt2",
        "smart_crop",
    )
    same_bucket = ThumbnailRenderSpec.from_settings(
        255,
        "portrait_1_sqrt2",
        "smart_crop",
    )
    square = ThumbnailRenderSpec.from_settings(
        250,
        "square_1_1",
        "smart_crop",
    )
    letterbox = ThumbnailRenderSpec.from_settings(
        250,
        "portrait_1_sqrt2",
        "letterbox",
    )

    assert (first.frame_width, first.frame_height) == (181, 256)
    assert first.cache_token == same_bucket.cache_token
    assert first.cache_token != square.cache_token
    assert first.cache_token != letterbox.cache_token


def test_provider_outputs_fixed_frame_and_reuses_smart_crop_across_sizes(
    qapp,
    tmp_path,
    monkeypatch,
):
    import app.thumbnail_render as thumbnail_render

    path = tmp_path / "cover.webp"
    with Image.new("RGB", (400, 200), "white") as image:
        for x in range(280, 380):
            for y in range(40, 170):
                image.putpixel((x, y), (20, 20, 20))
        image.save(path, "WEBP", lossless=True)
    item = BrowserItem(
        path.name,
        path,
        BrowserItemKind.IMAGE,
        path.stat().st_mtime,
        path.stat().st_size,
    )
    detections: list[bool] = []
    original_detect = thumbnail_render.detect_smart_crop

    def record_detect(image, target_size):
        detections.append(True)
        return original_detect(image, target_size)

    monkeypatch.setattr(thumbnail_render, "detect_smart_crop", record_detect)
    provider = BrowserThumbnailProvider(max_workers=1)
    images: list[QImage] = []
    provider.thumbnail_ready.connect(
        lambda _path, _generation, image: images.append(image.copy())
    )
    first = ThumbnailRenderSpec.from_settings(
        180,
        "portrait_1_sqrt2",
        "smart_crop",
    )
    larger = ThumbnailRenderSpec.from_settings(
        250,
        "portrait_1_sqrt2",
        "smart_crop",
    )
    assert provider.request(item, first)
    assert provider.wait_for_done(2000)
    qapp.processEvents()
    assert provider.request(item, larger)
    assert provider.wait_for_done(2000)
    qapp.processEvents()

    assert [(image.width(), image.height()) for image in images] == [
        (136, 192),
        (181, 256),
    ]
    assert detections == [True]
    provider.close()


def test_shell_icons_are_cached_per_extension(monkeypatch):
    provider = ShellAssociatedIconProvider()
    calls: list[str] = []
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor("green"))
    icon = QIcon(pixmap)

    def fake_query(extension: str, *, folder: bool):
        calls.append("folder" if folder else extension)
        return icon

    monkeypatch.setattr(provider, "_windows_icon", fake_query)
    first = provider.icon_for_extension(".webp")
    second = provider.icon_for_extension(".webp")
    folder = provider.icon_for_extension("", folder=True)

    assert not first.isNull() and not second.isNull() and not folder.isNull()
    assert calls == [".webp", "folder"]
    assert provider.cache_size == 2


def test_selected_delegate_keeps_thumbnail_center_visible(qapp, tmp_path):
    model = BrowserItemModel()
    item = BrowserItem(
        "cover.jpg",
        tmp_path / "cover.jpg",
        BrowserItemKind.IMAGE,
        None,
    )
    model.set_items([item])
    thumbnail = QPixmap(127, 180)
    thumbnail.fill(QColor("#d02020"))
    model.set_thumbnail(item.path, QIcon(thumbnail))
    shell_pixmap = QPixmap(16, 16)
    shell_pixmap.fill(QColor("green"))

    class ShellProvider:
        def icon_for(self, _item):
            return QIcon(shell_pixmap)

    delegate = BrowserItemDelegate(
        thumbnail_size=180,
        density=BrowserDisplayDensity.STANDARD,
        shell_icon_provider=ShellProvider(),
    )
    canvas = QImage(171, 238, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("white"))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 171, 238)
    option.palette = qapp.palette()
    option.state = (
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_Selected
        | QStyle.StateFlag.State_HasFocus
    )
    painter = QPainter(canvas)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()

    center = canvas.pixelColor(85, 90)
    highlight = option.palette.highlight().color()
    assert center.red() > 150 and center.green() < 100
    assert center != highlight


def test_config_normalizes_thumbnail_ratio_and_crop_mode(tmp_path):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(
        {
            "thumbnail_frame_ratio": "bad",
            "thumbnail_crop_mode": "bad",
            "browser_thumbnail_display_mode": "bad",
        }
    )
    assert config.get("thumbnail_frame_ratio") == "portrait_1_sqrt2"
    assert config.get("thumbnail_crop_mode") == "smart_crop"
    assert config.get("browser_thumbnail_display_mode") == "fit"
