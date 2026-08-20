from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import viewer_render as viewer_render_module
from app.config_manager import ConfigManager
from app.image_work_coordinator import ImageWorkPriority
from app.page_model import DisplaySpread, PageSlot
from app.viewer_render import (
    RESAMPLING_MODE_LABELS,
    ViewerRenderKey,
    ViewerRenderResult,
    pillow_resampling_for,
    render_qimage,
)
from app.viewer_widget import ViewerImage, ViewerWidget
from app.viewer_window import ViewerWindow


def _image(width: int = 240, height: int = 160) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.white)
    return image


def _set_single_page(
    widget: ViewerWidget,
    *,
    image: QImage | None = None,
    rendered_size: tuple[int, int] | None = None,
) -> None:
    source = image or _image()
    widget.set_pages(
        DisplaySpread(0, (PageSlot("page", 0),), True),
        [
            ViewerWidget.from_qimage(
                0,
                "page",
                source,
                (source.width(), source.height()),
                rendered_size=rendered_size,
            )
        ],
    )


def _mouse(
    widget: ViewerWidget,
    event_type: QEvent.Type,
    x: int,
    y: int,
    *,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> None:
    point = QPointF(x, y)
    event = QMouseEvent(
        event_type,
        point,
        point,
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def _finish_display(widget: ViewerWidget, qapp: QApplication) -> None:
    qapp.processEvents()
    QTest.qWait(widget._resize_render_timer.interval() + 20)
    qapp.processEvents()
    assert widget.wait_for_rendering()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))


@pytest.mark.parametrize(
    ("mode", "shrinking", "expected"),
    [
        ("standard", True, None),
        ("moire_reduction", True, Image.Resampling.BOX),
        ("moire_reduction", False, Image.Resampling.BICUBIC),
        ("high_quality", True, Image.Resampling.LANCZOS),
        ("smooth", True, Image.Resampling.BILINEAR),
        ("pixel", True, Image.Resampling.NEAREST),
    ],
)
def test_resampling_mode_maps_to_expected_pillow_filter(
    mode: str,
    shrinking: bool,
    expected: Image.Resampling | None,
) -> None:
    target = (100, 100) if shrinking else (400, 400)
    assert pillow_resampling_for(mode, (200, 200), target) == expected


def test_render_key_carries_target_algorithm_rotation_dpr_and_crop() -> None:
    key = ViewerRenderKey(
        image_id="page",
        source_cache_key=42,
        target_width=3840,
        target_height=2160,
        mode="high_quality",
        rotation=90,
        device_pixel_ratio_milli=2000,
        crop=(10, 20, 110, 120),
        purpose="magnifier",
        request_generation=7,
    )

    assert key.target_width == 3840
    assert key.target_height == 2160
    assert key.mode == "high_quality"
    assert key.rotation == 90
    assert key.device_pixel_ratio_milli == 2000
    assert key.crop == (10, 20, 110, 120)
    assert key.request_generation == 7


def test_standard_uses_qt_target_resize_without_pillow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _image(100, 80)
    base = dict(
        image_id="page",
        source_cache_key=source.cacheKey(),
        target_width=300,
        target_height=200,
        rotation=0,
        device_pixel_ratio_milli=1000,
        crop=(10, 10, 60, 50),
        purpose="magnifier",
    )

    monkeypatch.setattr(
        viewer_render_module,
        "qimage_to_pillow",
        lambda _image: (_ for _ in ()).throw(
            AssertionError("standard rendering must stay on the Qt path")
        ),
    )
    standard, standard_resized = render_qimage(
        source,
        ViewerRenderKey(mode="standard", **base),
    )
    monkeypatch.undo()
    high_quality, high_quality_resized = render_qimage(
        source,
        ViewerRenderKey(mode="high_quality", **base),
    )

    assert standard.size().toTuple() == (300, 200)
    assert standard_resized
    assert standard.format() == QImage.Format.Format_ARGB32_Premultiplied
    assert high_quality.size().toTuple() == (300, 200)
    assert high_quality_resized


def test_standard_preserves_fast_or_smooth_qt_transform_setting() -> None:
    source = QImage(2, 1, QImage.Format.Format_RGB32)
    source.setPixelColor(0, 0, Qt.GlobalColor.black)
    source.setPixelColor(1, 0, Qt.GlobalColor.white)
    base = dict(
        image_id="page",
        source_cache_key=source.cacheKey(),
        target_width=3,
        target_height=1,
        mode="standard",
        rotation=0,
        device_pixel_ratio_milli=1000,
    )

    smooth, _ = render_qimage(
        source,
        ViewerRenderKey(smooth_transform=True, **base),
    )
    fast, _ = render_qimage(
        source,
        ViewerRenderKey(smooth_transform=False, **base),
    )

    assert smooth.pixelColor(1, 0) != fast.pixelColor(1, 0)


@pytest.mark.parametrize("mode", ("standard", "pixel"))
def test_split_render_crops_shared_source_in_worker(mode: str) -> None:
    source = QImage(8, 4, QImage.Format.Format_RGBA8888)
    source.fill(Qt.GlobalColor.red)
    for x in range(4, 8):
        for y in range(4):
            source.setPixelColor(x, y, Qt.GlobalColor.blue)
    key = ViewerRenderKey(
        image_id="wide#right",
        source_cache_key=source.cacheKey(),
        target_width=4,
        target_height=4,
        mode=mode,
        rotation=0,
        device_pixel_ratio_milli=1000,
        split_range=(4, 0, 4, 4),
    )

    rendered, resized = render_qimage(source, key)

    assert rendered.size().toTuple() == (4, 4)
    assert not resized
    assert rendered.pixelColor(0, 0).name() == "#0000ff"
    assert rendered.pixelColor(3, 3).name() == "#0000ff"


def test_async_resize_preserves_alpha() -> None:
    source = QImage(40, 40, QImage.Format.Format_RGBA8888)
    source.fill(Qt.GlobalColor.transparent)
    source.setPixelColor(20, 20, Qt.GlobalColor.red)
    key = ViewerRenderKey(
        "alpha",
        source.cacheKey(),
        80,
        80,
        "pixel",
        0,
        1000,
    )

    rendered, resized = render_qimage(source, key)

    assert resized
    assert rendered.hasAlphaChannel()
    assert rendered.pixelColor(0, 0).alpha() == 0
    assert rendered.pixelColor(40, 40).alpha() == 255


@pytest.mark.parametrize(
    ("image_format", "expected_mode"),
    [
        (QImage.Format.Format_RGB888, "RGB"),
        (QImage.Format.Format_RGBA8888, "RGBA"),
        (QImage.Format.Format_Grayscale8, "L"),
    ],
)
def test_qimage_to_pillow_preserves_common_raster_mode(
    image_format: QImage.Format,
    expected_mode: str,
) -> None:
    source = QImage(17, 11, image_format)
    source.fill(Qt.GlobalColor.white)

    converted = viewer_render_module.qimage_to_pillow(source)
    try:
        assert converted.mode == expected_mode
        assert converted.size == (17, 11)
    finally:
        converted.close()


def test_exact_target_skips_qimage_to_pillow_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = QImage(320, 500, QImage.Format.Format_RGB888)
    source.fill(Qt.GlobalColor.white)
    key = ViewerRenderKey(
        "page",
        source.cacheKey(),
        source.width(),
        source.height(),
        "high_quality",
        0,
        1000,
    )
    monkeypatch.setattr(
        viewer_render_module,
        "qimage_to_pillow",
        lambda _image: (_ for _ in ()).throw(
            AssertionError("exact target must not cross the Pillow boundary")
        ),
    )

    rendered, resized = viewer_render_module.render_qimage(source, key)

    assert not resized
    assert rendered.size() == source.size()


def test_config_saves_restores_modes_and_unknown_values_fall_back(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    config = ConfigManager(path)
    config.load()
    config.apply(
        {
            "viewer_resampling_mode": "pixel",
            "magnifier_resampling_mode": "smooth",
        },
        save=True,
    )

    restored = ConfigManager(path).load()
    assert restored["viewer_resampling_mode"] == "pixel"
    assert restored["magnifier_resampling_mode"] == "smooth"

    config.apply(
        {
            "viewer_resampling_mode": "future",
            "magnifier_resampling_mode": "future",
        }
    )
    assert config.get("viewer_resampling_mode") == "standard"
    assert config.get("magnifier_resampling_mode") == "standard"


def test_nonstandard_normal_render_is_atomic_and_keeps_previous_page(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    widget.set_resampling_modes(normal="high_quality")
    _set_single_page(widget, image=_image(240, 160))
    widget.show()
    qapp.processEvents()
    QTest.qWait(widget._resize_render_timer.interval() + 20)
    qapp.processEvents()
    assert widget._render_pending or widget._render_cache
    assert widget.wait_for_rendering()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))
    assert tuple(image.image_id for image in widget._images) == ("page",)

    next_image = _image(320, 200)
    next_page = ViewerWidget.from_qimage(
        1,
        "next",
        next_image,
        (next_image.width(), next_image.height()),
    )
    commits: list[tuple[str, ...]] = []
    original_commit = widget._commit_display

    def record_commit(spread, images, **kwargs):
        commits.append(tuple(image.image_id for image in images))
        original_commit(spread, images, **kwargs)

    widget._commit_display = record_commit  # type: ignore[method-assign]
    widget.set_pages(
        DisplaySpread(1, (PageSlot("next", 1),), True),
        [next_page],
    )
    widget.render(QPixmap(widget.size()))
    assert tuple(image.image_id for image in widget._images) == ("page",)
    assert commits == []

    assert widget.wait_for_rendering()
    qapp.processEvents()
    assert tuple(image.image_id for image in widget._images) == ("next",)
    assert commits == [("next",)]
    widget.close()


def test_standard_display_uses_qt_worker_once_and_never_pillow(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    widget.show()
    qapp.processEvents()
    queued: list[object] = []
    commits: list[tuple[str, ...]] = []
    pillow_conversions: list[object] = []
    original_conversion = viewer_render_module.qimage_to_pillow
    monkeypatch.setattr(
        viewer_render_module,
        "qimage_to_pillow",
        lambda image: (
            pillow_conversions.append(image),
            original_conversion(image),
        )[1],
    )
    original_queue = widget._queue_render
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda source, key, **kwargs: (
            queued.append((source, key)),
            original_queue(source, key, **kwargs),
        )[1],
    )
    original_commit = widget._commit_display

    def record_commit(spread, images, **kwargs):
        commits.append(tuple(image.image_id for image in images))
        original_commit(spread, images, **kwargs)

    monkeypatch.setattr(widget, "_commit_display", record_commit)
    _set_single_page(widget, image=_image(409, 650))
    assert widget.wait_for_rendering()
    qapp.processEvents()
    for _index in range(3):
        widget.render(QPixmap(widget.size()))

    assert widget.resampling_mode == "standard"
    assert len(queued) == 1
    assert widget._render_pending == {}
    assert len(widget._render_cache) == 1
    assert commits == [("page",)]
    assert pillow_conversions == []
    widget.close()


def test_nonstandard_cache_hit_does_not_queue_worker(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    widget.set_resampling_modes(normal="pixel")
    source = _image(240, 160)
    _set_single_page(widget, image=source)
    assert widget.wait_for_rendering()
    qapp.processEvents()
    assert widget._render_cache

    queued: list[object] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda image, key, **_kwargs: queued.append((image, key)),
    )
    _set_single_page(widget, image=source)

    assert queued == []
    assert tuple(image.image_id for image in widget._images) == ("page",)
    widget.close()


def test_nonstandard_spread_queues_each_page_once_and_commits_as_one_unit(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(1000, 700)
    _set_single_page(widget, image=_image(240, 160))
    widget.set_resampling_modes(normal="pixel")
    assert widget.wait_for_rendering()
    qapp.processEvents()
    previous_ids = tuple(image.image_id for image in widget._images)

    queued: list[ViewerRenderKey] = []
    commits: list[tuple[str, ...]] = []
    original_queue = widget._queue_render
    original_commit = widget._commit_display

    def record_queue(image, key, **kwargs):
        queued.append(key)
        original_queue(image, key, **kwargs)

    def record_commit(spread, images, **kwargs):
        commits.append(tuple(image.image_id for image in images))
        original_commit(spread, images, **kwargs)

    monkeypatch.setattr(widget, "_queue_render", record_queue)
    monkeypatch.setattr(widget, "_commit_display", record_commit)
    left = _image(360, 650)
    right = _image(84, 120)
    widget.set_pages(
        DisplaySpread(
            1,
            (PageSlot("left", 1), PageSlot("right", 2)),
            False,
        ),
        [
            ViewerWidget.from_qimage(1, "left", left, (3600, 6500)),
            ViewerWidget.from_qimage(2, "right", right, (840, 1200)),
        ],
    )

    assert tuple(image.image_id for image in widget._images) == previous_ids
    assert [key.image_id for key in queued] == ["left", "right"]
    assert commits == []
    assert widget.wait_for_rendering()
    qapp.processEvents()
    assert commits == [("left", "right")]
    assert tuple(image.image_id for image in widget._images) == ("left", "right")
    widget.close()


def test_actual_size_avoids_unnecessary_resize(qapp: QApplication) -> None:
    widget = ViewerWidget()
    widget.resize(500, 400)
    _set_single_page(widget, image=_image(240, 160))
    widget.set_fit_mode("actual_size")
    widget.set_resampling_modes(normal="high_quality")
    widget.show()
    qapp.processEvents()
    QTest.qWait(widget._resize_render_timer.interval() + 20)
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))

    assert widget._render_pending == {}
    assert widget._render_cache == {}
    widget.close()


def test_high_dpi_physical_target_is_part_of_render_key(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: 2.0)
    widget.set_resampling_modes(normal="pixel")
    _set_single_page(widget)
    widget.show()
    qapp.processEvents()
    QTest.qWait(widget._resize_render_timer.interval() + 20)
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))

    assert widget._render_pending
    key = next(iter(widget._render_pending))
    assert widget._pending_display is not None
    layout = widget._layout_for_images(
        widget._pending_display.spread,
        widget._pending_display.images,
    )
    assert key.target_width == layout.rects[0].width() * 2
    assert key.target_height == layout.rects[0].height() * 2
    assert key.device_pixel_ratio_milli == 2000
    assert widget.wait_for_rendering()
    qapp.processEvents()
    assert widget._render_cache[key].devicePixelRatioF() == 2.0
    widget.close()


def test_spread_independent_layout_is_shared_by_all_modes_and_high_dpi_cache(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(1400, 900)
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: 2.0)
    large = _image(360, 650)
    small = _image(84, 120)
    spread = DisplaySpread(
        0,
        (PageSlot("large", 0), PageSlot("small", 1)),
        False,
    )
    pages = [
        ViewerWidget.from_qimage(
            0,
            "large",
            large,
            (3600, 6500),
        ),
        ViewerWidget.from_qimage(
            1,
            "small",
            small,
            (840, 1200),
        ),
    ]
    widget.set_pages(spread, pages)
    baseline_rects = widget._layout_for_images(spread, pages).rects
    for mode in RESAMPLING_MODE_LABELS:
        widget.set_resampling_modes(normal=mode)
        assert widget._layout_for_images(spread, pages).rects == baseline_rects

    widget.set_resampling_modes(normal="pixel")
    widget.show()
    qapp.processEvents()
    QTest.qWait(widget._resize_render_timer.interval() + 20)
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))
    assert widget.wait_for_rendering()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))

    keys = {
        key.image_id: key
        for key in widget._render_cache
        if key.image_id in {"large", "small"}
    }
    assert set(keys) == {"large", "small"}
    for image_id, rect in zip(("large", "small"), baseline_rects):
        assert keys[image_id].target_width == rect.width() * 2
        assert keys[image_id].target_height == rect.height() * 2

    left_key = keys["large"]
    left_pixmap = widget._render_cache[left_key]
    right_key = keys["small"]
    widget._render_cache.pop(right_key)
    layout = widget._layout_for_current_images()
    left = widget._pixmap_for_paint(widget._images[0], layout.rects[0])
    widget._pixmap_for_paint(widget._images[1], layout.rects[1])
    assert left is left_pixmap
    assert widget._layout_for_current_images().rects == baseline_rects
    assert right_key not in widget._render_pending
    widget._refresh_current_render()
    refreshed_key = next(
        key
        for key in widget._render_pending
        if key.image_id == right_key.image_id
    )
    assert refreshed_key.layout_generation > right_key.layout_generation
    assert widget.wait_for_rendering()
    widget.close()


def test_stale_and_nonvisible_render_results_are_not_cached() -> None:
    widget = ViewerWidget()
    source = _image()
    key = ViewerRenderKey(
        "page",
        source.cacheKey(),
        100,
        80,
        "pixel",
        0,
        1000,
    )
    task = object()
    widget._on_render_completed(
        ViewerRenderResult(key, widget._render_generation - 1, _image(), True),
        task,  # type: ignore[arg-type]
    )
    assert not widget._render_cache

    nonvisible = ViewerRenderKey(
        "old-page",
        source.cacheKey(),
        100,
        80,
        "pixel",
        0,
        1000,
    )
    widget.resampling_mode = "pixel"
    widget._on_render_completed(
        ViewerRenderResult(
            nonvisible,
            widget._render_generation,
            _image(),
            True,
        ),
        task,  # type: ignore[arg-type]
    )
    assert not widget._render_cache
    widget.close()


def test_magnifier_render_uses_demand_priority(
    qapp: QApplication,
    monkeypatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    _set_single_page(widget)
    _finish_display(widget, qapp)
    widget.magnifier_source_page = 0
    widget._magnifier_source_image_id = "page"
    widget.magnifier_source_rect = QRectF(40, 30, 120, 80)
    queued: list[tuple[ViewerRenderKey, int]] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _source, key, **kwargs: queued.append(
            (key, int(kwargs["priority"]))
        ),
    )

    widget._request_magnifier_render(allow_pdf_request=False)

    assert len(queued) == 1
    assert queued[0][0].purpose == "magnifier"
    assert (
        queued[0][1]
        == int(ImageWorkPriority.VIEWER_INTERACTIVE_RERENDER)
    )
    widget.close()


def test_rapid_magnifier_requests_and_cancel_clear_all_task_tracking(
    qapp: QApplication,
) -> None:
    class FakeCoordinator:
        def __init__(self) -> None:
            self.started: list[tuple[object, int]] = []
            self.taken: list[object] = []

        def start_viewer(self, task, priority: int) -> bool:
            self.started.append((task, priority))
            return True

        def try_take_viewer(self, task) -> bool:
            self.taken.append(task)
            return False

    class FakePool:
        def __init__(self) -> None:
            self.started: list[tuple[object, int]] = []
            self.taken: list[object] = []

        def start(self, task, priority: int) -> None:
            self.started.append((task, priority))

        def tryTake(self, task) -> bool:
            self.taken.append(task)
            return True

        def clear(self) -> None:
            return None

        def waitForDone(self, _msecs: int) -> bool:
            return True

    coordinator = FakeCoordinator()
    widget = ViewerWidget(  # type: ignore[arg-type]
        image_work_coordinator=coordinator,
    )
    widget.resize(480, 320)
    widget.set_direct_display_mode(True)
    pool = FakePool()
    widget._render_pool = pool  # type: ignore[assignment]
    source = _image()
    page = ViewerWidget.from_qimage(
        0,
        "page",
        source,
        (source.width(), source.height()),
        create_pixmap=False,
    )
    widget._spread = DisplaySpread(
        0,
        (PageSlot("page", 0),),
        True,
    )
    widget._images = [page]
    widget.magnifier_selecting = True
    widget.magnifier_source_page = 0
    widget._magnifier_source_image_id = "page"

    for offset in (0, 10, 20):
        widget.magnifier_source_rect = QRectF(
            20 + offset,
            20,
            120,
            80,
        )
        widget._request_magnifier_render(allow_pdf_request=False)
        assert len(widget._render_tasks) == 1
        assert len(widget._render_pending) == 1
        assert len(widget._render_task_by_key) == 1
        assert len(widget._render_priorities) == 1

    assert len(pool.taken) == 2
    # Direct-mode magnifier work must not occupy the one book-runtime Viewer
    # lane. A running high-quality crop cannot then queue ahead of a page turn.
    assert not coordinator.started
    assert widget.cancel_magnifier()
    assert len(pool.taken) == 3
    assert not widget._render_tasks
    assert not widget._render_pending
    assert not widget._render_task_by_key
    assert not widget._render_priorities
    widget.close()


def test_magnifier_worker_failure_clears_waiting_state(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    source = _image()
    key = ViewerRenderKey(
        "page",
        source.cacheKey(),
        480,
        320,
        "high_quality",
        0,
        1000,
        purpose="magnifier",
        request_generation=1,
    )
    widget.magnifier_selecting = True
    widget.magnifier_source_page = 0
    widget._magnifier_source_image_id = "page"
    widget._magnifier_key = key

    widget._on_render_completed(
        ViewerRenderResult(
            key,
            widget._render_generation,
            None,
            False,
            "magnifier failed",
        ),
        object(),  # type: ignore[arg-type]
    )

    assert not widget.magnifier_selecting
    assert not widget.magnifier_active
    assert widget._magnifier_key is None
    widget.close()


def test_middle_button_immediately_starts_tracks_and_toggles_magnifier(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    widget.show()
    qapp.processEvents()
    _set_single_page(widget)
    _finish_display(widget, qapp)

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        240,
        160,
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.MiddleButton,
    )
    assert widget.magnifier_selecting
    assert not widget.magnifier_active
    assert widget.magnifier_source_page == 0
    assert widget.magnifier_source_rect is not None
    assert widget._magnifier_selection_rect is not None
    assert widget._magnifier_key is not None
    assert widget._last_draw_layout
    assert widget.wait_for_rendering()
    qapp.processEvents()
    assert widget.magnifier_active
    first_key = widget._magnifier_key
    first_rect = QRectF(widget.magnifier_source_rect)

    _mouse(
        widget,
        QEvent.Type.MouseMove,
        300,
        170,
        button=Qt.MouseButton.NoButton,
        buttons=Qt.MouseButton.NoButton,
    )
    assert widget.wait_for_rendering()
    qapp.processEvents()
    assert widget.magnifier_active
    assert widget._magnifier_pixmap is not None
    assert widget._magnifier_key != first_key
    assert widget.magnifier_source_rect != first_rect

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        240,
        160,
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.MiddleButton,
    )
    assert not widget.magnifier_active
    assert widget.magnifier_source_rect is None
    widget.close()


def test_middle_button_background_and_spread_gutter_do_not_start(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(600, 400)
    image = _image(120, 240)
    widget.show()
    qapp.processEvents()
    widget.set_pages(
        DisplaySpread(
            0,
            (PageSlot("left", 0), PageSlot("right", 1)),
            False,
        ),
        [
            ViewerWidget.from_qimage(0, "left", image, (120, 240)),
            ViewerWidget.from_qimage(1, "right", image, (120, 240)),
        ],
    )
    _finish_display(widget, qapp)
    left, right = widget._last_draw_layout
    gutter_x = (left[0].right() + right[0].left()) // 2

    for x, y in ((5, 5), (gutter_x, widget.height() // 2)):
        _mouse(
            widget,
            QEvent.Type.MouseButtonPress,
            x,
            y,
            button=Qt.MouseButton.MiddleButton,
            buttons=Qt.MouseButton.MiddleButton,
        )
        assert not widget.magnifier_selecting
    widget.close()


def test_active_magnifier_preserves_canvas_click_and_right_drag_gesture(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    widget.show()
    qapp.processEvents()
    _set_single_page(widget)
    _finish_display(widget, qapp)
    right_clicks: list[None] = []
    gestures: list[str] = []
    widget.rightSideClicked.connect(lambda: right_clicks.append(None))
    widget.gestureRecognized.connect(gestures.append)

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        240,
        160,
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.MiddleButton,
    )
    assert widget.wait_for_rendering()
    qapp.processEvents()
    assert widget.magnifier_active

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        400,
        160,
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.LeftButton,
    )
    _mouse(
        widget,
        QEvent.Type.MouseButtonRelease,
        400,
        160,
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.NoButton,
    )
    assert right_clicks == [None]

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        200,
        100,
        button=Qt.MouseButton.RightButton,
        buttons=Qt.MouseButton.RightButton,
    )
    _mouse(
        widget,
        QEvent.Type.MouseMove,
        200,
        180,
        button=Qt.MouseButton.NoButton,
        buttons=Qt.MouseButton.RightButton,
    )
    _mouse(
        widget,
        QEvent.Type.MouseButtonRelease,
        200,
        180,
        button=Qt.MouseButton.RightButton,
        buttons=Qt.MouseButton.NoButton,
    )
    assert gestures == ["D"]
    widget.close()


def test_spread_magnifier_targets_only_page_under_cursor(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(600, 400)
    image = _image(120, 240)
    widget.show()
    qapp.processEvents()
    widget.set_pages(
        DisplaySpread(
            0,
            (PageSlot("left", 0), PageSlot("right", 1)),
            False,
        ),
        [
            ViewerWidget.from_qimage(0, "left", image, (120, 240)),
            ViewerWidget.from_qimage(1, "right", image, (120, 240)),
        ],
    )
    _finish_display(widget, qapp)
    right_rect = widget._last_image_layout[1][0]

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        right_rect.center().x(),
        right_rect.center().y(),
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.MiddleButton,
    )

    assert widget.magnifier_source_page == 1
    assert widget._magnifier_source_image_id == "right"
    widget.cancel_magnifier()
    widget.close()


def test_rotated_source_mapping_is_clamped_to_rotated_bounds(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    widget.set_rotation_angle(90)
    widget.show()
    qapp.processEvents()
    _set_single_page(widget, image=_image(300, 120))
    _finish_display(widget, qapp)
    rect = widget._last_draw_layout[0][0]

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        rect.right(),
        rect.bottom(),
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.MiddleButton,
    )

    source = widget.magnifier_source_rect
    assert source is not None
    assert source.left() >= 0
    assert source.top() >= 0
    assert source.right() <= 120
    assert source.bottom() <= 300
    widget.cancel_magnifier()
    widget.close()


def test_pdf_magnifier_requests_higher_resolution_before_crop(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    requests: list[tuple[int, object]] = []
    widget.magnifierPdfResolutionRequested.connect(
        lambda page, size: requests.append((page, size))
    )
    widget.show()
    qapp.processEvents()
    _set_single_page(widget, image=_image(240, 160), rendered_size=(240, 160))
    _finish_display(widget, qapp)

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        240,
        160,
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.MiddleButton,
    )
    _mouse(
        widget,
        QEvent.Type.MouseButtonRelease,
        240,
        160,
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.NoButton,
    )

    assert requests
    assert requests[0][0] == 0
    assert requests[0][1].width() > 240
    assert widget._magnifier_waiting_for_pdf
    assert not widget.magnifier_active
    widget.close()


def test_raster_preview_magnifier_requests_full_source_before_crop(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    requests: list[tuple[int, object]] = []
    widget.magnifierSourceResolutionRequested.connect(
        lambda page, size: requests.append((page, size))
    )
    widget.show()
    qapp.processEvents()
    source = _image(240, 160)
    widget.set_pages(
        DisplaySpread(0, (PageSlot("page", 0),), True),
        [
            ViewerWidget.from_qimage(
                0,
                "page",
                source,
                (960, 640),
                source_is_preview=True,
            )
        ],
    )
    _finish_display(widget, qapp)

    _mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        240,
        160,
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.MiddleButton,
    )
    _mouse(
        widget,
        QEvent.Type.MouseButtonRelease,
        240,
        160,
        button=Qt.MouseButton.MiddleButton,
        buttons=Qt.MouseButton.NoButton,
    )

    assert requests
    assert requests[0][0] == 0
    assert widget._magnifier_waiting_for_pdf
    assert not widget.magnifier_active
    assert not any(
        task.key.purpose == "magnifier"
        for task in widget._render_tasks
    )
    widget.close()


def test_pixmap_only_ready_frame_rehydrates_source_for_magnifier(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(480, 320)
    widget.set_direct_display_mode(True)
    requests: list[tuple[int, object]] = []
    widget.magnifierSourceResolutionRequested.connect(
        lambda page, size: requests.append((page, size))
    )
    widget.show()
    qapp.processEvents()
    source = _image(960, 640)
    pixmap = QPixmap.fromImage(source.scaled(480, 320))
    spread = DisplaySpread(0, (PageSlot("page", 0),), True)
    widget.commit_display_ready_frame(
        spread,
        (
            ViewerImage(
                page_index=0,
                image_id="page",
                pixmap=pixmap,
                original_size=(960, 640),
                qimage=None,
                display_prepared=True,
            ),
        ),
        object(),
    )
    widget.render(QPixmap(widget.size()))

    assert widget.toggle_magnifier(widget.rect().center())
    assert requests and requests[0][0] == 0
    assert widget._magnifier_waiting_for_pdf
    widget._request_magnifier_render()
    widget._request_magnifier_render()
    assert len(requests) == 1
    snapshot = widget.displayed_source_snapshot(0)
    assert snapshot is not None and snapshot[0] is not None
    widget.close()


def test_z_shortcut_toggles_from_child_focus_escape_cancels_and_m_does_nothing(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    window.resize(640, 480)
    window.show()
    qapp.processEvents()
    _set_single_page(window.viewer)
    _finish_display(window.viewer, qapp)
    image_rect = window.viewer._last_image_layout[0][0]
    QTest.mouseMove(window.viewer, image_rect.center())
    window.slider.setFocus()

    assert window.magnifier_action.text() == "拡大鏡の切り替え"
    assert window.magnifier_action.shortcut().toString() == "Z"
    QTest.keyClick(window.slider, Qt.Key.Key_M)
    qapp.processEvents()
    assert not window.viewer.magnifier_selecting
    assert not window.viewer.magnifier_active

    QTest.keyClick(window.slider, Qt.Key.Key_Z)
    qapp.processEvents()
    assert window.viewer.magnifier_selecting or window.viewer.magnifier_active
    assert window.viewer.wait_for_rendering()
    qapp.processEvents()
    assert window.viewer.magnifier_active

    QTest.keyClick(window.slider, Qt.Key.Key_Z)
    qapp.processEvents()
    assert not window.viewer.magnifier_active
    assert not window.viewer.magnifier_selecting

    window.viewer.setFocus()
    QTest.keyClick(window.viewer, Qt.Key.Key_Z)
    assert window.viewer.wait_for_rendering()
    qapp.processEvents()
    assert window.viewer.magnifier_active
    QTest.keyClick(window.viewer, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert not window.viewer.magnifier_active
    window.close()
    qapp.processEvents()


def test_new_page_and_escape_cancel_magnifier(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    window.viewer.magnifier_active = True
    window.viewer._magnifier_pixmap = QPixmap.fromImage(_image())
    window.viewer._magnifier_key = ViewerRenderKey(
        "page",
        1,
        100,
        100,
        "high_quality",
        0,
        1000,
        purpose="magnifier",
    )

    window._handle_escape()
    assert not window.viewer.magnifier_active
    assert not window.isFullScreen()

    window.viewer.magnifier_active = True
    window.viewer._magnifier_key = ViewerRenderKey(
        "page",
        1,
        100,
        100,
        "high_quality",
        0,
        1000,
        purpose="magnifier",
    )
    window.viewer.set_pages(
        DisplaySpread(0, (PageSlot("page", 0),), True),
        [ViewerWidget.from_qimage(0, "page", _image(), (240, 160))],
    )
    window.viewer.set_pages(
        DisplaySpread(1, (PageSlot("next", 1),), True),
        [ViewerWidget.from_qimage(1, "next", _image(), (240, 160))],
    )
    assert not window.viewer.magnifier_active
    window.close()
    qapp.processEvents()


def test_stale_magnifier_result_is_rejected() -> None:
    widget = ViewerWidget()
    first = ViewerRenderKey(
        "page",
        1,
        100,
        100,
        "high_quality",
        0,
        1000,
        crop=(0, 0, 20, 20),
        purpose="magnifier",
        request_generation=1,
    )
    current = ViewerRenderKey(
        "page",
        1,
        100,
        100,
        "high_quality",
        0,
        1000,
        crop=(0, 0, 20, 20),
        purpose="magnifier",
        request_generation=2,
    )
    widget._magnifier_key = current

    widget._on_render_completed(
        ViewerRenderResult(
            first,
            widget._render_generation,
            _image(),
            True,
        ),
        object(),  # type: ignore[arg-type]
    )

    assert not widget.magnifier_active
    assert widget._magnifier_pixmap is None
    widget.close()


def test_viewer_menu_modes_are_radio_actions_and_persist(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)

    assert {
        action.text() for action in window.normal_resampling_actions.values()
    } == set(RESAMPLING_MODE_LABELS.values())
    assert window.normal_resampling_actions["standard"].isChecked()
    assert window.magnifier_resampling_actions["high_quality"].isChecked()

    window.set_viewer_resampling_mode("pixel")
    window.set_magnifier_resampling_mode("smooth")
    assert config.get("viewer_resampling_mode") == "pixel"
    assert config.get("magnifier_resampling_mode") == "smooth"
    assert window.normal_resampling_actions["pixel"].isChecked()
    assert window.magnifier_resampling_actions["smooth"].isChecked()
    window.close()
    qapp.processEvents()
