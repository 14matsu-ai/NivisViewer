from __future__ import annotations

from pathlib import Path
from threading import Event, Lock, current_thread, main_thread
from time import monotonic
import zipfile

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
from app.image_cache import ImageCache
from app.image_source import (
    FolderListingSnapshot,
    ImageSource,
    create_image_source,
)
from app.image_work_coordinator import ImageWorkCoordinator, ImageWorkPriority
from app.page_model import PageModel
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


def _drain_events(qapp, predicate, timeout: float = 3.0) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
    return predicate()


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
    coordinator.shutdown(wait_msecs=0)

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

    cache.set_source(source, source.ids)
    cache.ensure_loaded(1)
    assert coordinator.wait_for_viewer(2000)
    qapp.processEvents()
    assert source.started == ["running.jpg", "queued.jpg"]
    assert cache.get(1) is not None
    coordinator.shutdown()


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


def test_spread_displays_current_before_slow_partner(qapp, tmp_path):
    source = OrderedSource(tmp_path, block_partner=True)
    coordinator = ImageWorkCoordinator(max_workers=2)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"single_first_page": False})
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
        image_work_coordinator=coordinator,
    )
    window = ViewerWindow(
        config_manager=config,
        book_session=session,
        image_work_coordinator=coordinator,
    )
    first_frames: list[bool] = []
    window.first_frame_ready.connect(lambda _window: first_frames.append(True))
    window.resize(640, 480)
    window.show()
    assert window.open_path(tmp_path / "dummy.webp")
    assert _drain_events(
        qapp,
        lambda: any(
            image.image_id == "page0.webp" and image.pixmap is not None
            for image in window.viewer._images
        ),
    )
    assert any(image.loading for image in window.viewer._images)
    assert _drain_events(qapp, lambda: bool(first_frames))
    assert source.partner_started.wait(2)
    source.release_partner.set()
    assert session.image_cache.wait_for_done(2000)
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
