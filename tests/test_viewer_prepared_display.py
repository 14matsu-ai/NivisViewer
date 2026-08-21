from __future__ import annotations

from collections import OrderedDict
import threading
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QRect, QSize
from PySide6.QtGui import QColor, QImage, QPixmap, QResizeEvent
from PySide6.QtWidgets import QApplication

from app.config_manager import ConfigManager
from app.image_cache import CachedImage, ImageCache
from app.image_work_coordinator import ImageWorkPriority
from app.page_model import DisplaySpread, PageSlot
from app.viewer_render import ViewerRenderKey, ViewerRenderResult
from app import viewer_render as viewer_render_module
from app.viewer_widget import ViewerImage, ViewerWidget
from app.viewer_window import ViewerWindow


def _qimage(width: int = 320, height: int = 500) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor("#386cb0"))
    return image


def _page(
    index: int,
    *,
    width: int = 320,
    height: int = 500,
    generation: int = 1,
    source: str = "book.zip",
) -> ViewerImage:
    image = _qimage(width, height)
    return ViewerWidget.from_qimage(
        index,
        f"page-{index}.png",
        image,
        (width, height),
        create_pixmap=False,
        source_generation=generation,
        source_identity=source,
    )


def _single(index: int) -> DisplaySpread:
    return DisplaySpread(
        index,
        (PageSlot(f"page-{index}.png", index),),
        True,
    )


@pytest.mark.parametrize(
    "mode",
    ("standard", "moire_reduction", "high_quality", "smooth", "pixel"),
)
def test_prepared_single_page_navigation_has_no_request_time_worker_or_pillow(
    qapp: QApplication,
    monkeypatch,
    mode: str,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal=mode)
    next_page = _page(1)

    widget.prepare_display_units([(1, _single(1), [next_page], True)])
    assert widget.wait_for_rendering()
    qapp.processEvents()
    assert len(widget._prepared_units) == 1

    queued: list[ViewerRenderKey] = []
    conversions: list[QImage] = []
    commits: list[tuple[str, ...]] = []
    original_queue = widget._queue_render
    original_conversion = viewer_render_module.qimage_to_pillow
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda image, key, **kwargs: (
            queued.append(key),
            original_queue(image, key, **kwargs),
        )[1],
    )
    monkeypatch.setattr(
        viewer_render_module,
        "qimage_to_pillow",
        lambda image: (
            conversions.append(image),
            original_conversion(image),
        )[1],
    )
    widget.displayCommitted.connect(commits.append)

    widget.set_pages(_single(1), [next_page])

    assert queued == []
    assert conversions == []
    assert commits == [("page-1.png",)]
    assert tuple(image.image_id for image in widget._images) == ("page-1.png",)
    widget.close()


def test_exact_size_source_without_pixmap_materializes_display_artifact_once(
    qapp: QApplication,
    monkeypatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(320, 500)
    widget.set_resampling_modes(normal="high_quality")
    conversions: list[QImage] = []
    monkeypatch.setattr(
        viewer_render_module,
        "qimage_to_pillow",
        lambda image: conversions.append(image),
    )
    page = _page(0, width=320, height=500)

    widget.set_pages(_single(0), [page])
    assert widget.wait_for_rendering()
    qapp.processEvents()
    layout = widget._layout_for_current_images()
    pixmap = widget._pixmap_for_paint(
        widget._images[0],
        layout.rects[0],
    )

    assert pixmap is not None
    assert conversions == []
    assert len(widget._render_cache) == 1
    widget.close()


def test_display_pixmap_materialization_runs_on_gui_thread(
    qapp: QApplication,
    monkeypatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    gui_thread = threading.get_ident()
    completion_threads: list[int] = []
    original_completed = widget._on_render_completed

    def record_completion(result, task):
        completion_threads.append(threading.get_ident())
        original_completed(result, task)

    monkeypatch.setattr(widget, "_on_render_completed", record_completion)
    widget.set_pages(_single(0), [_page(0)])
    assert widget.wait_for_rendering()
    qapp.processEvents()

    assert completion_threads == [gui_thread]
    assert widget._render_cache
    widget.close()


def test_standard_smooth_toggle_rebuilds_cache_with_distinct_key(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_pages(
        _single(0),
        [_page(0, width=1600, height=1000)],
    )
    assert widget.wait_for_rendering()
    qapp.processEvents()
    smooth_keys = set(widget._render_cache)
    assert len(smooth_keys) == 1
    assert all(key.smooth_transform for key in smooth_keys)

    widget.set_smooth_scaling(False)
    assert widget.wait_for_rendering()
    qapp.processEvents()
    fast_keys = set(widget._render_cache)

    assert len(fast_keys) == 1
    assert all(not key.smooth_transform for key in fast_keys)
    assert fast_keys.isdisjoint(smooth_keys)
    widget.close()


def test_prepared_spread_is_committed_once_as_one_atomic_unit(
    qapp: QApplication,
    monkeypatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(1200, 800)
    widget.set_resampling_modes(normal="high_quality")
    spread = DisplaySpread(
        2,
        (PageSlot("page-2.png", 2), PageSlot("page-3.png", 3)),
        False,
    )
    pages = [
        _page(2, width=360, height=650),
        _page(3, width=84, height=120),
    ]
    widget.prepare_display_units([(1, spread, pages, True)])
    assert widget.wait_for_rendering()
    qapp.processEvents()

    commits: list[tuple[str, ...]] = []
    queued: list[ViewerRenderKey] = []
    widget.displayCommitted.connect(commits.append)
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _image, key, **_kwargs: queued.append(key),
    )

    widget.set_pages(spread, pages)

    assert queued == []
    assert commits == [("page-2.png", "page-3.png")]
    assert len(widget._images) == 2
    widget.close()


def test_prepared_unit_applies_without_raw_qimage_and_rejects_stale_source(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    page = _page(1, generation=7, source="ready.zip")
    spread = _single(1)
    widget.prepare_display_units([(0, spread, [page], True)])
    assert widget.wait_for_rendering()
    qapp.processEvents()

    stored = next(iter(widget._prepared_units.values()))
    assert stored.images[0].qimage is None
    assert stored.images[0].pixmap is None
    widget.prepare_display_units([(0, spread, None, True)])
    assert len(widget._prepared_units) == 1

    commits: list[tuple[str, ...]] = []
    widget.displayCommitted.connect(commits.append)
    assert not widget.apply_prepared_display(
        spread,
        source_generation=6,
        source_identity="ready.zip",
    )
    assert widget.apply_prepared_display(
        spread,
        source_generation=7,
        source_identity="ready.zip",
    )

    assert commits == [("page-1.png",)]
    assert widget._images[0].qimage is None
    assert widget._images[0].pixmap is not None
    prepared_pixmap = widget._images[0].pixmap
    assert widget._images[0].display_prepared

    widget.attach_current_sources([page])

    assert widget._images[0].qimage is not None
    assert widget._images[0].pixmap is prepared_pixmap
    widget.rotation_angle = 90
    assert widget._display_pixmap(widget._images[0]) is prepared_pixmap
    widget.close()


def test_spread_unit_is_not_ready_until_both_page_results_arrive(
    qapp: QApplication,
    monkeypatch,
) -> None:
    class CompletedTask:
        def __init__(self, key: ViewerRenderKey) -> None:
            self.key = key

    widget = ViewerWidget()
    widget.resize(1200, 800)
    widget.set_resampling_modes(normal="pixel")
    spread = DisplaySpread(
        2,
        (PageSlot("page-2.png", 2), PageSlot("page-3.png", 3)),
        False,
    )
    pages = [_page(2), _page(3)]
    queued: list[ViewerRenderKey] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _image, key, **_kwargs: queued.append(key),
    )
    widget.prepare_display_units([(1, spread, pages, True)])
    assert len(queued) == 2

    first, second = queued
    widget._on_render_completed(
        ViewerRenderResult(
            first,
            widget._render_generation,
            _qimage(first.target_width, first.target_height),
            True,
        ),
        CompletedTask(first),  # type: ignore[arg-type]
    )
    assert not widget._prepared_units
    assert widget._prepared_requests

    widget._on_render_completed(
        ViewerRenderResult(
            second,
            widget._render_generation,
            _qimage(second.target_width, second.target_height),
            True,
        ),
        CompletedTask(second),  # type: ignore[arg-type]
    )
    assert len(widget._prepared_units) == 1
    assert not widget._prepared_requests
    widget.close()


def test_preparation_priorities_are_current_next_previous_then_next_next(
    qapp: QApplication,
    monkeypatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="smooth")
    queued: list[tuple[str, int]] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _image, key, **kwargs: queued.append(
            (key.image_id, int(kwargs["priority"]))
        ),
    )

    widget.prepare_display_units(
        [
            (0, _single(2), [_page(2)], True),
            (1, _single(3), [_page(3)], True),
            (2, _single(1), [_page(1)], True),
            (3, _single(4), [_page(4)], False),
        ]
    )

    assert queued == [
        ("page-2.png", int(ImageWorkPriority.VIEWER_SPREAD_PARTNER)),
        ("page-3.png", int(ImageWorkPriority.VIEWER_SPREAD_PARTNER) - 1),
        ("page-1.png", int(ImageWorkPriority.VIEWER_SPREAD_PARTNER) - 2),
        ("page-4.png", int(ImageWorkPriority.VIEWER_SPREAD_PARTNER) - 3),
    ]
    widget.close()


def test_duplicate_render_request_is_deduplicated_and_can_be_reprioritized(
    qapp: QApplication,
) -> None:
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

    widget = ViewerWidget()
    pool = FakePool()
    widget._render_pool = pool  # type: ignore[assignment]
    source = _qimage()
    key = ViewerRenderKey(
        "page.png",
        source.cacheKey(),
        320,
        500,
        "pixel",
        0,
        1000,
    )

    widget._queue_render(source, key, priority=50)
    widget._queue_render(source, key, priority=40)
    widget._queue_render(source, key, priority=90)

    assert [priority for _task, priority in pool.started] == [50, 90]
    assert len(pool.taken) == 1
    assert widget._render_priorities[key] == 90
    widget.close()


def test_direction_reversal_reprioritizes_retained_queued_requests(
    qapp: QApplication,
) -> None:
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

    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    pool = FakePool()
    widget._render_pool = pool  # type: ignore[assignment]
    first_spread = _single(1)
    second_spread = _single(2)
    widget.prepare_display_units(
        [
            (1, first_spread, [_page(1)], True),
            (3, second_spread, [_page(2)], False),
        ]
    )

    widget.prepare_display_units(
        [
            (3, first_spread, None, False),
            (1, second_spread, None, True),
        ]
    )

    assert [priority for _task, priority in pool.started] == [
        int(ImageWorkPriority.VIEWER_SPREAD_PARTNER) - 1,
        int(ImageWorkPriority.VIEWER_SPREAD_PARTNER) - 3,
        int(ImageWorkPriority.VIEWER_SPREAD_PARTNER) - 1,
        int(ImageWorkPriority.VIEWER_SPREAD_PARTNER) - 3,
    ]
    assert len(pool.taken) == 2
    priorities = {
        key.image_id: priority
        for key, priority in widget._render_priorities.items()
    }
    assert priorities == {
        "page-1.png": int(ImageWorkPriority.VIEWER_SPREAD_PARTNER) - 3,
        "page-2.png": int(ImageWorkPriority.VIEWER_SPREAD_PARTNER) - 1,
    }
    widget.close()


def test_prefetch_replan_does_not_downgrade_pending_display_demand(
    qapp: QApplication,
) -> None:
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

    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    pool = FakePool()
    widget._render_pool = pool  # type: ignore[assignment]
    spread = _single(1)
    page = _page(1)
    widget.prepare_display_units([(3, spread, [page], False)])

    widget.set_pages(spread, [page])
    widget.prepare_display_units([(0, spread, None, True)])

    assert [priority for _task, priority in pool.started] == [
        int(ImageWorkPriority.VIEWER_SPREAD_PARTNER) - 3,
        int(ImageWorkPriority.VIEWER_CURRENT),
    ]
    key = next(iter(widget._render_priorities))
    assert (
        widget._render_priorities[key]
        == int(ImageWorkPriority.VIEWER_CURRENT)
    )
    assert widget._pending_display is not None
    widget.close()


def test_removed_far_request_result_is_not_cached(
    qapp: QApplication,
    monkeypatch,
) -> None:
    class CompletedTask:
        def __init__(self, key: ViewerRenderKey) -> None:
            self.key = key

    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    queued: list[ViewerRenderKey] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _image, key, **_kwargs: queued.append(key),
    )
    widget.prepare_display_units(
        [(3, _single(8), [_page(8)], False)]
    )
    key = queued[0]
    widget.prepare_display_units(())

    widget._on_render_completed(
        ViewerRenderResult(
            key,
            widget._render_generation,
            _qimage(key.target_width, key.target_height),
            True,
        ),
        CompletedTask(key),  # type: ignore[arg-type]
    )

    assert key not in widget._render_cache
    assert not widget._prepared_units
    widget.close()


def test_old_viewport_generation_result_is_not_cached(
    qapp: QApplication,
    monkeypatch,
) -> None:
    class CompletedTask:
        def __init__(self, key: ViewerRenderKey) -> None:
            self.key = key

    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    queued: list[ViewerRenderKey] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _image, key, **_kwargs: queued.append(key),
    )
    widget.prepare_display_units([(1, _single(1), [_page(1)], True)])
    old_key = queued[0]
    old_generation = widget._render_generation
    widget._invalidate_render_requests(clear_cache=True)

    widget._on_render_completed(
        ViewerRenderResult(
            old_key,
            old_generation,
            _qimage(old_key.target_width, old_key.target_height),
            True,
        ),
        CompletedTask(old_key),  # type: ignore[arg-type]
    )

    assert old_key not in widget._render_cache
    widget.close()


def test_superseded_cold_demand_cannot_commit_or_cache_old_result(
    qapp: QApplication,
    monkeypatch,
) -> None:
    class CompletedTask:
        def __init__(self, key: ViewerRenderKey) -> None:
            self.key = key

    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    queued: list[ViewerRenderKey] = []
    commits: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _image, key, **_kwargs: queued.append(key),
    )
    widget.displayCommitted.connect(commits.append)
    widget.set_pages(_single(1), [_page(1)])
    assert widget._pending_display is not None
    old_key = queued[0]
    old_generation = widget._render_generation

    widget.supersede_pending_display()
    widget._on_render_completed(
        ViewerRenderResult(
            old_key,
            old_generation,
            _qimage(old_key.target_width, old_key.target_height),
            True,
        ),
        CompletedTask(old_key),  # type: ignore[arg-type]
    )

    assert widget._pending_display is None
    assert old_key not in widget._render_cache
    assert commits == []
    assert widget._images == []
    widget.close()


@pytest.mark.parametrize("mode", ("standard", "pixel"))
def test_resize_debounce_preserves_pending_target_and_last_complete_frame(
    qapp: QApplication,
    monkeypatch,
    mode: str,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal=mode)
    current = _page(0)
    widget.set_pages(_single(0), [current])
    assert widget.wait_for_rendering()
    qapp.processEvents()
    old_keys = set(widget._render_cache)
    assert old_keys

    queued: list[ViewerRenderKey] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _image, key, **_kwargs: queued.append(key),
    )
    target = _page(1)
    widget.set_pages(_single(1), [target])
    assert widget._pending_display is not None
    widget.resizeEvent(
        QResizeEvent(QSize(900, 700), QSize(800, 600))
    )

    assert tuple(image.image_id for image in widget._images) == ("page-0.png",)
    assert old_keys.issubset(widget._render_cache)
    assert widget._pending_display is None
    assert widget._deferred_render_target is not None
    assert widget._deferred_render_target[1][0].image_id == "page-1.png"

    widget._refresh_current_render()

    assert widget._pending_display is not None
    assert widget._pending_display.images[0].image_id == "page-1.png"
    assert queued[-1].layout_generation == (
        0 if queued[-1].source_sized else widget._render_generation
    )
    widget._resize_render_timer.stop()
    widget.close()


def test_page_request_during_resize_debounce_supersedes_empty_snapshot(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.resizeEvent(
        QResizeEvent(QSize(800, 600), QSize(640, 480))
    )
    assert widget._deferred_render_target is not None
    assert widget._deferred_render_target[1] == ()

    target = _page(1)
    widget.set_pages(_single(1), [target])

    assert widget._deferred_render_target is not None
    assert widget._deferred_render_target[1][0].image_id == "page-1.png"
    widget._resize_render_timer.stop()
    widget._refresh_current_render()
    assert widget.wait_for_rendering()
    qapp.processEvents()
    assert tuple(image.image_id for image in widget._images) == (
        "page-1.png",
    )
    widget.close()


def test_prepared_hit_cancels_older_resize_debounce_snapshot(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    target = _page(1)
    widget.prepare_display_units([(0, _single(1), [target], True)])
    assert widget.wait_for_rendering()
    qapp.processEvents()
    widget._deferred_render_target = (_single(0), (_page(0),))
    widget._resize_render_timer.start()

    assert widget.apply_prepared_display(
        _single(1),
        source_generation=1,
        source_identity="book.zip",
    )

    assert not widget._resize_render_timer.isActive()
    assert widget._deferred_render_target is None
    assert widget.displayed_page_indexes == (1,)
    widget.close()


def test_byte_limit_evicts_far_unit_but_protects_three_near_units(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    plan = [
        (0, _single(2), [_page(2)], True),
        (1, _single(3), [_page(3)], True),
        (2, _single(1), [_page(1)], True),
        (3, _single(4), [_page(4)], False),
    ]
    widget.prepare_display_units(plan)
    assert widget.wait_for_rendering()
    qapp.processEvents()
    keys_by_image = {
        key.image_id: key
        for unit in widget._prepared_units
        for key in unit.render_keys
        if key is not None
    }
    protected = {
        keys_by_image["page-2.png"],
        keys_by_image["page-3.png"],
        keys_by_image["page-1.png"],
    }
    bytes_per_page = next(iter(widget._render_cache.values())).width()
    bytes_per_page *= next(iter(widget._render_cache.values())).height() * 4
    widget._render_cache_byte_limit = bytes_per_page * 3

    widget._enforce_render_cache_limit()

    assert protected.issubset(widget._render_cache)
    assert keys_by_image["page-4.png"] not in widget._render_cache
    widget.close()


@pytest.mark.parametrize("mode", ("standard", "pixel"))
def test_byte_limit_never_evicts_current_display_artifact(
    qapp: QApplication,
    mode: str,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal=mode)
    widget.set_pages(_single(0), [_page(0)])
    assert widget.wait_for_rendering()
    qapp.processEvents()
    current_keys = widget._current_render_keys()
    assert len(current_keys) == 1
    assert current_keys.issubset(widget._render_cache)

    widget._render_cache_byte_limit = 1
    widget._enforce_render_cache_limit()

    assert current_keys.issubset(widget._render_cache)
    widget.close()


def test_byte_limit_keeps_partial_pending_spread_until_atomic_commit(
    qapp: QApplication,
    monkeypatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    spread = DisplaySpread(
        0,
        (
            PageSlot("page-0.png", 0),
            PageSlot("page-1.png", 1),
        ),
        False,
    )
    pages = [_page(0), _page(1)]
    queued: list[ViewerRenderKey] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _source, key, **_kwargs: queued.append(key),
    )
    widget._render_cache_byte_limit = 1

    widget.set_pages(spread, pages)

    assert widget._pending_display is not None
    assert len(queued) == 2
    first, second = queued
    first_pixmap = QPixmap.fromImage(
        _qimage(first.target_width, first.target_height)
    )
    widget._render_cache[first] = first_pixmap
    widget._enforce_render_cache_limit()

    assert first in widget._render_cache
    assert widget._pending_display is not None

    widget._render_cache[second] = QPixmap.fromImage(
        _qimage(second.target_width, second.target_height)
    )
    widget._commit_pending_display()

    assert widget._pending_display is None
    assert widget.displayed_page_indexes == (0, 1)
    assert {first, second}.issubset(widget._render_cache)
    widget.close()


def test_byte_limit_cancels_incomplete_far_prepared_unit_atomically(
    qapp: QApplication,
    monkeypatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    spread = DisplaySpread(
        0,
        (
            PageSlot("page-0.png", 0),
            PageSlot("page-1.png", 1),
        ),
        False,
    )
    pages = [_page(0), _page(1)]
    queued: list[ViewerRenderKey] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _source, key, **_kwargs: queued.append(key),
    )

    widget.prepare_display_units([(9, spread, pages, False)])
    unit_key = next(iter(widget._prepared_requests))
    first = queued[0]
    first_pixmap = QPixmap.fromImage(
        _qimage(first.target_width, first.target_height)
    )
    widget._render_cache[first] = first_pixmap
    widget._last_rendered_by_image[first.image_id] = first_pixmap
    widget._render_cache_byte_limit = 1

    widget._enforce_render_cache_limit()

    assert unit_key not in widget._prepared_requests
    assert all(key not in widget._render_cache for key in queued)
    assert first.image_id not in widget._last_rendered_by_image
    widget.close()


def test_byte_limit_does_not_cancel_far_request_used_by_pending_display(
    qapp: QApplication,
    monkeypatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    spread = DisplaySpread(
        0,
        (
            PageSlot("page-0.png", 0),
            PageSlot("page-1.png", 1),
        ),
        False,
    )
    pages = [_page(0), _page(1)]
    queued: list[ViewerRenderKey] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _source, key, **_kwargs: queued.append(key),
    )
    widget.prepare_display_units([(9, spread, pages, False)])
    unit_key = next(iter(widget._prepared_requests))
    widget.set_pages(spread, pages)
    assert widget._pending_display is not None
    first, second = widget._pending_display.keys
    assert first is not None and second is not None
    widget._render_cache[first] = QPixmap.fromImage(
        _qimage(first.target_width, first.target_height)
    )
    widget._render_cache_byte_limit = 1

    widget._enforce_render_cache_limit()

    assert first in widget._render_cache
    assert unit_key in widget._prepared_requests
    widget._render_cache[second] = QPixmap.fromImage(
        _qimage(second.target_width, second.target_height)
    )
    widget._commit_pending_display()
    assert widget._pending_display is None
    assert widget.displayed_page_indexes == (0, 1)
    widget.close()


def test_standard_manual_zoom_caps_artifact_to_rotated_split_source_size(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.set_resampling_modes(normal="standard")
    widget.rotation_angle = 90
    source = _qimage(640, 500)
    page = ViewerWidget.from_qimage(
        0,
        "wide.png#left",
        source,
        (320, 500),
        create_pixmap=False,
        split_range=(0, 0, 320, 500),
    )

    key = widget._viewer_render_key(
        page,
        QRect(0, 0, 4000, 5000),
    )

    assert key is not None
    assert (key.target_width, key.target_height) == (500, 320)
    assert key.source_sized
    assert key.layout_generation == 0
    widget.close()


def test_standard_source_sized_artifact_is_reused_across_zoom_steps(
    qapp: QApplication,
    monkeypatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="standard")
    widget.fit_mode = "manual_zoom"
    widget.manual_zoom = 2.0
    widget.set_pages(_single(0), [_page(0)])
    assert widget.wait_for_rendering()
    qapp.processEvents()
    source_keys = {
        key for key in widget._render_cache if key.source_sized
    }
    assert len(source_keys) == 1
    queued: list[ViewerRenderKey] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _source, key, **_kwargs: queued.append(key),
    )

    widget.set_manual_zoom(3.0)

    assert queued == []
    assert source_keys.issubset(widget._render_cache)
    assert widget.displayed_page_indexes == (0,)
    widget.close()


@pytest.mark.parametrize("page_count", (1, 2))
def test_render_worker_failure_commits_terminal_error_slots(
    qapp: QApplication,
    monkeypatch,
    page_count: int,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    pages = [_page(index) for index in range(page_count)]
    spread = DisplaySpread(
        0,
        tuple(
            PageSlot(f"page-{index}.png", index)
            for index in range(page_count)
        ),
        page_count == 1,
    )
    queued: list[ViewerRenderKey] = []
    monkeypatch.setattr(
        widget,
        "_queue_render",
        lambda _source, key, **_kwargs: queued.append(key),
    )
    widget.set_pages(spread, pages)
    assert widget._pending_display is not None

    for key in queued:
        widget._on_render_completed(
            ViewerRenderResult(
                key,
                widget._render_generation,
                None,
                False,
                "render failed",
            ),
            object(),  # type: ignore[arg-type]
        )

    assert widget._pending_display is None
    assert widget.displayed_page_indexes == tuple(range(page_count))
    assert all(image.error == "render failed" for image in widget._images)
    assert all(not image.loading for image in widget._images)
    assert all(image.qimage is None for image in widget._images)
    widget.close()


def test_device_pixel_ratio_change_refreshes_prepared_render(
    qapp: QApplication,
    monkeypatch,
) -> None:
    widget = ViewerWidget()
    refreshes: list[None] = []
    monkeypatch.setattr(
        widget,
        "_refresh_current_render",
        lambda: refreshes.append(None),
    )

    QApplication.sendEvent(
        widget,
        QEvent(QEvent.Type.DevicePixelRatioChange),
    )

    assert refreshes == [None]
    widget.close()


def test_source_and_generation_are_part_of_prepared_render_identity(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    source_image = _qimage()
    first = ViewerWidget.from_qimage(
        0,
        "same.png",
        source_image,
        (320, 500),
        create_pixmap=False,
        source_generation=4,
        source_identity="A.zip",
    )
    second = ViewerWidget.from_qimage(
        0,
        "same.png",
        source_image,
        (320, 500),
        create_pixmap=False,
        source_generation=5,
        source_identity="B.pdf",
    )

    first_key = widget._prepared_unit_key(_single(0), [first]).render_keys[0]
    second_key = widget._prepared_unit_key(_single(0), [second]).render_keys[0]

    assert first_key is not None and second_key is not None
    assert first_key != second_key
    assert first_key.source_generation == 4
    assert second_key.source_identity == "B.pdf"
    widget.close()


def test_split_range_is_part_of_prepared_render_identity(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.set_resampling_modes(normal="pixel")
    image = _qimage(640, 500)
    left = ViewerWidget.from_qimage(
        0,
        "wide.png#left",
        image,
        (320, 500),
        create_pixmap=False,
        split_range=(0, 0, 320, 500),
    )
    right = ViewerWidget.from_qimage(
        0,
        "wide.png#right",
        image,
        (320, 500),
        create_pixmap=False,
        split_range=(320, 0, 320, 500),
    )
    spread = DisplaySpread(
        0,
        (PageSlot("wide.png", 0),),
        True,
    )

    keys = widget._prepared_unit_key(spread, [left, right]).render_keys

    assert keys[0] is not None and keys[1] is not None
    assert left.qimage is not None and right.qimage is not None
    assert left.qimage.cacheKey() == image.cacheKey()
    assert right.qimage.cacheKey() == image.cacheKey()
    assert keys[0].split_range == (0, 0, 320, 500)
    assert keys[1].split_range == (320, 0, 320, 500)
    widget.close()


@pytest.mark.parametrize(
    ("rotation", "qimage_size", "expected"),
    (
        (
            0,
            (1600, 800),
            {
                "pdf-page:0#left": (0, 0, 800, 800),
                "pdf-page:0#right": (800, 0, 800, 800),
            },
        ),
        (
            90,
            (800, 1600),
            {
                "pdf-page:0#left": (0, 0, 800, 800),
                "pdf-page:0#right": (0, 800, 800, 800),
            },
        ),
        (
            180,
            (1600, 800),
            {
                "pdf-page:0#left": (800, 0, 800, 800),
                "pdf-page:0#right": (0, 0, 800, 800),
            },
        ),
        (
            270,
            (800, 1600),
            {
                "pdf-page:0#left": (0, 800, 800, 800),
                "pdf-page:0#right": (0, 0, 800, 800),
            },
        ),
    ),
)
def test_pdf_split_uses_rendered_pixel_coordinates_for_all_rotations(
    tmp_path,
    qapp: QApplication,
    rotation: int,
    qimage_size: tuple[int, int],
    expected: dict[str, tuple[int, int, int, int]],
) -> None:
    config = ConfigManager(tmp_path / f"pdf-split-{rotation}.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        window.split_wide_image = True
        window.reading_direction = "ltr"
        qimage = _qimage(*qimage_size)
        cached = CachedImage(
            0,
            "pdf-page:0",
            qimage,
            (4000, 2000),
            None,
            window.image_cache.generation,
            rendered_size=qimage_size,
            rendered_rotation=rotation,
        )

        pages = window._viewer_images_for_cached(
            cached,
            split_allowed=True,
            create_pixmap=False,
        )

        assert [page.image_id for page in pages] == [
            "pdf-page:0#left",
            "pdf-page:0#right",
        ]
        assert {
            page.image_id: page.split_range for page in pages
        } == expected
        assert all(page.pre_rotated == bool(rotation) for page in pages)
        assert all(
            page.rendered_size
            == (
                page.split_range[2],
                page.split_range[3],
            )
            for page in pages
            if page.split_range is not None
        )
    finally:
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize(
    ("attribute", "initial", "updated", "setter_name"),
    (
        ("split_wide_image", False, True, "set_split_wide_image"),
        ("split_wide_image", True, False, "set_split_wide_image"),
        ("reading_direction", "ltr", "rtl", "set_reading_direction"),
        ("reading_direction", "rtl", "ltr", "set_reading_direction"),
    ),
)
def test_split_and_reading_direction_changes_invalidate_prepared_units(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
    attribute: str,
    initial: object,
    updated: object,
    setter_name: str,
) -> None:
    config = ConfigManager(tmp_path / f"{attribute}-{initial}.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        setattr(window, attribute, initial)
        window.model.image_ids = ["wide.png"]
        page = _page(0, width=640, height=240)
        window.viewer.prepare_display_units(
            [(0, _single(0), [page], True)]
        )
        assert window.viewer.wait_for_rendering()
        qapp.processEvents()
        assert window.viewer._prepared_units
        generation = window.viewer._render_generation
        monkeypatch.setattr(window, "_refresh_view", lambda: None)

        getattr(window, setter_name)(updated)

        assert window.viewer._render_generation == generation + 1
        assert not window.viewer._prepared_units
        assert not window.viewer._prepared_requests
    finally:
        window.close()
        qapp.processEvents()


def test_page_navigation_removes_queued_magnifier_and_uses_current_priority(
    qapp: QApplication,
) -> None:
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

    widget = ViewerWidget()
    widget.resize(800, 600)
    widget.resampling_mode = "pixel"
    pool = FakePool()
    widget._render_pool = pool  # type: ignore[assignment]
    source = _qimage()
    magnifier_key = ViewerRenderKey(
        "page-0.png",
        source.cacheKey(),
        220,
        220,
        "high_quality",
        0,
        1000,
        crop=(0, 0, 100, 100),
        purpose="magnifier",
    )
    widget._queue_render(source, magnifier_key, priority=0)

    widget.set_pages(_single(1), [_page(1)])

    assert [priority for _task, priority in pool.started] == [
        0,
        int(ImageWorkPriority.VIEWER_CURRENT),
    ]
    assert pool.taken == [pool.started[0][0]]
    assert magnifier_key not in widget._render_pending
    widget.close()


def test_window_scheduler_reprioritizes_on_direction_reversal(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    window.viewer_resampling_mode = "pixel"
    window.viewer.set_resampling_modes(normal="pixel")
    window.model.image_ids = [f"page-{index}.png" for index in range(6)]
    window.model.update_options(view_mode="single")
    window.model.current_index = 2
    window.model._size_cache = {
        index: (320, 500) for index in range(6)
    }
    window.image_cache._cache = OrderedDict(
        (
            index,
            CachedImage(
                index,
                f"page-{index}.png",
                _qimage(),
                (320, 500),
                None,
                window.image_cache.generation,
            ),
        )
        for index in range(6)
    )
    plans: list[list[tuple[int, int, bool]]] = []

    def capture(units) -> None:
        plans.append(
            [
                (priority, spread.start_index, protected)
                for priority, spread, _pages, protected in units
            ]
        )

    monkeypatch.setattr(window.viewer, "prepare_display_units", capture)
    window._last_preload_direction = 1
    window._schedule_prepared_display_prefetch()
    window._last_preload_direction = -1
    window._schedule_prepared_display_prefetch()

    assert plans == [
        [
            (0, 2, True),
            (1, 3, True),
            (2, 1, True),
            (3, 4, False),
            (4, 5, False),
            (5, 0, False),
        ],
        [
            (0, 2, True),
            (1, 1, True),
            (2, 3, True),
            (3, 0, False),
            (4, 4, False),
            (5, 5, False),
        ],
    ]
    window.close()
    qapp.processEvents()


def test_raster_prefetch_pipeline_admits_one_complete_unit_at_a_time(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        window.model.image_ids = [
            f"page-{index}.png" for index in range(9)
        ]
        window.model.update_options(view_mode="single")
        window.model.current_index = 4
        window.model._size_cache = {
            index: (320, 500) for index in range(9)
        }
        window._active_request_id = 7
        window._visible_page_indexes = (4,)
        cached = {4}
        pending: set[int] = set()
        ready: set[int] = set()
        admitted: list[tuple[int, ...]] = []
        protected_calls: list[tuple[int, ...]] = []

        monkeypatch.setattr(
            window.image_cache,
            "contains",
            lambda index: index in cached,
        )
        monkeypatch.setattr(
            window.image_cache,
            "has_pending_page",
            lambda index: index in pending,
        )

        def preload_around(
            _center,
            *,
            prefetch_indexes,
            visible_indexes,
            **_kwargs,
        ) -> None:
            unit = tuple(prefetch_indexes)
            admitted.append(unit)
            protected_calls.append(tuple(visible_indexes))
            pending.update(unit)

        monkeypatch.setattr(
            window.image_cache,
            "preload_around",
            preload_around,
        )
        monkeypatch.setattr(
            window.viewer,
            "prepared_display_is_ready",
            lambda spread, **_kwargs: spread.start_index in ready,
        )
        monkeypatch.setattr(
            window.viewer,
            "tracks_prepared_display_unit",
            lambda _spread, **_kwargs: True,
        )
        monkeypatch.setattr(
            window,
            "_schedule_prepared_display_prefetch",
            lambda: None,
        )
        monkeypatch.setattr(
            window,
            "_enforce_combined_cache_budget",
            lambda: None,
        )

        window._start_raster_prefetch_pipeline(
            4,
            (4,),
            ((5,), (3,), (6,)),
        )
        assert admitted == [(5,)]
        assert protected_calls == [(4, 5)]
        assert window._raster_prefetch_active_unit == (5,)

        pending.remove(5)
        cached.add(5)
        ready.add(5)
        window._on_viewer_render_cache_changed()
        assert admitted == [(5,), (3,)]
        assert protected_calls[-1] == (4, 3)

        pending.remove(3)
        cached.add(3)
        ready.add(3)
        window._on_viewer_render_cache_changed()
        assert admitted == [(5,), (3,), (6,)]

        pending.remove(6)
        cached.add(6)
        ready.add(6)
        window._on_viewer_render_cache_changed()
        assert window._raster_prefetch_plan is None
        assert window._raster_prefetch_active_unit == tuple()
    finally:
        window.close()
        qapp.processEvents()


def test_raster_prefetch_pipeline_resolves_rtl_spread_from_unit_start(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        window.model.image_ids = [
            f"page-{index}.png" for index in range(8)
        ]
        window.model.update_options(
            view_mode="spread",
            reading_direction="rtl",
            single_first_page=False,
        )
        window.model._size_cache = {
            index: (320, 500) for index in range(8)
        }
        window.model._rebuild_spread_boundaries()
        window.model.current_index = 6
        window._active_request_id = 11
        window._visible_page_indexes = (7, 6)
        cached = {6, 7}
        pending: set[int] = set()
        ready_starts: set[int] = set()
        checked_starts: list[int] = []
        admitted: list[tuple[int, ...]] = []

        monkeypatch.setattr(
            window.image_cache,
            "contains",
            lambda index: index in cached,
        )
        monkeypatch.setattr(
            window.image_cache,
            "has_pending_page",
            lambda index: index in pending,
        )

        def preload_around(
            _center,
            *,
            prefetch_indexes,
            **_kwargs,
        ) -> None:
            unit = tuple(prefetch_indexes)
            admitted.append(unit)
            pending.update(unit)

        def is_ready(spread, **_kwargs) -> bool:
            checked_starts.append(spread.start_index)
            return spread.start_index in ready_starts

        monkeypatch.setattr(
            window.image_cache,
            "preload_around",
            preload_around,
        )
        monkeypatch.setattr(
            window.viewer,
            "prepared_display_is_ready",
            is_ready,
        )
        monkeypatch.setattr(
            window.viewer,
            "tracks_prepared_display_unit",
            lambda _spread, **_kwargs: True,
        )
        monkeypatch.setattr(
            window,
            "_schedule_prepared_display_prefetch",
            lambda: None,
        )

        window._start_raster_prefetch_pipeline(
            6,
            (7, 6),
            ((5, 4), (3, 2)),
        )
        assert admitted == [(5, 4)]

        pending.difference_update((5, 4))
        cached.update((5, 4))
        ready_starts.add(4)
        window._advance_raster_prefetch_pipeline()

        assert 4 in checked_starts
        assert admitted == [(5, 4), (3, 2)]
    finally:
        window.close()
        qapp.processEvents()


def test_raster_prefetch_pipeline_stops_when_unit_is_not_admitted(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        window.model.image_ids = [
            f"page-{index}.png" for index in range(5)
        ]
        window.model.update_options(view_mode="single")
        window.model.current_index = 2
        window.model._size_cache = {
            index: (320, 500) for index in range(5)
        }
        window._active_request_id = 3
        window._visible_page_indexes = (2,)
        monkeypatch.setattr(
            window.image_cache,
            "contains",
            lambda index: index == 2,
        )
        monkeypatch.setattr(
            window.image_cache,
            "has_pending_page",
            lambda _index: False,
        )
        monkeypatch.setattr(
            window.image_cache,
            "preload_around",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            window.viewer,
            "prepared_display_is_ready",
            lambda _spread, **_kwargs: False,
        )

        window._start_raster_prefetch_pipeline(
            2,
            (2,),
            ((3,), (4,)),
        )

        assert window._raster_prefetch_plan is None
        assert window._raster_prefetch_active_unit == tuple()
    finally:
        window.close()
        qapp.processEvents()


def test_raster_prefetch_render_failure_stops_farther_pipeline(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        window.model.image_ids = [
            f"page-{index}.png" for index in range(6)
        ]
        window.model.update_options(view_mode="single")
        window.model.current_index = 2
        window.model._size_cache = {
            index: (320, 500) for index in range(6)
        }
        window._active_request_id = 5
        window._visible_page_indexes = (2,)
        window._raster_prefetch_plan = (
            window.image_cache.generation,
            5,
            2,
            (2,),
            ((4,),),
        )
        window._raster_prefetch_active_unit = (3,)
        admitted: list[tuple[int, ...]] = []
        monkeypatch.setattr(
            window.image_cache,
            "preload_around",
            lambda _center, *, prefetch_indexes, **_kwargs: admitted.append(
                tuple(prefetch_indexes)
            ),
        )
        monkeypatch.setattr(
            window,
            "_enforce_combined_cache_budget",
            lambda: None,
        )
        key = ViewerRenderKey(
            "page-3.png",
            1,
            320,
            500,
            "standard",
            0,
            1000,
        )

        window._on_viewer_render_work_finished(key, False)

        assert window._raster_prefetch_plan is None
        assert window._raster_prefetch_active_unit == tuple()
        assert admitted == []
    finally:
        window.close()
        qapp.processEvents()


def test_shared_coordinator_shutdown_waits_only_widget_owned_render_tasks(
    qapp: QApplication,
) -> None:
    coordinator = Mock()
    coordinator.try_take_viewer.return_value = False
    widget = ViewerWidget(image_work_coordinator=coordinator)
    task = Mock()
    task.finished = threading.Event()
    task.finished.set()
    widget._render_tasks.add(task)

    assert widget.shutdown_rendering(50)
    coordinator.wait_for_viewer.assert_not_called()
    widget.close()


@pytest.mark.parametrize("mode", ("standard", "pixel"))
def test_custom_prefetch_counts_reach_prepared_scheduler_in_both_directions(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
    mode: str,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(
        {
            "viewer_prefetch_preset": "custom",
            "viewer_prefetch_image_forward_units": 6,
            "viewer_prefetch_image_backward_units": 4,
        }
    )
    window = ViewerWindow(config_manager=config)
    try:
        window.viewer_resampling_mode = mode
        window.viewer.set_resampling_modes(normal=mode)
        window.model.image_ids = [
            f"page-{index}.png" for index in range(20)
        ]
        window.model.update_options(view_mode="single")
        window.model.current_index = 10
        window.model._size_cache = {
            index: (320, 500) for index in range(20)
        }
        window.image_cache._cache = OrderedDict(
            (
                index,
                CachedImage(
                    index,
                    f"page-{index}.png",
                    _qimage(),
                    (320, 500),
                    None,
                    window.image_cache.generation,
                ),
            )
            for index in range(20)
        )
        plans: list[list[int]] = []
        monkeypatch.setattr(
            window.viewer,
            "prepare_display_units",
            lambda units: plans.append(
                [spread.start_index for _, spread, _, _ in units]
            ),
        )

        window._last_preload_direction = 1
        window._schedule_prepared_display_prefetch()
        window._last_preload_direction = -1
        window._schedule_prepared_display_prefetch()

        assert plans == [
            [10, 11, 9, 12, 13, 14, 15, 16, 8, 7, 6],
            [10, 9, 11, 8, 7, 6, 5, 4, 12, 13, 14],
        ]
    finally:
        window.close()
        qapp.processEvents()


def test_configured_prefetch_can_exceed_legacy_count_when_bytes_allow(
    qapp: QApplication,
) -> None:
    cache = ImageCache(cache_size=10)
    cache.image_ids = [f"page-{index}.png" for index in range(20)]
    cache._center_index = 10
    cache._protected_indexes = {10}
    cache._preferred_direction = 1
    cache._configured_prefetch_order = True
    ordered = (11, 9, 12, 13, 14, 15, 16, 8, 7, 6)
    cache._configured_prefetch_ranks = {
        index: rank for rank, index in enumerate(ordered)
    }
    wanted = {10, *ordered}
    for index in wanted:
        cache._store_cached(
            CachedImage(
                index,
                f"page-{index}.png",
                _qimage(64, 64),
                (64, 64),
                None,
                cache.generation,
            )
        )
    cache._wanted_indexes = cache._limit_wanted_to_capacity(wanted)
    cache._enforce_limit()

    assert cache._wanted_indexes == wanted
    assert set(cache._cache) == wanted
    assert len(cache._cache) == 11


def test_byte_limited_spread_prefetch_keeps_units_atomic(
    qapp: QApplication,
) -> None:
    cache = ImageCache(cache_size=10)
    cache.image_ids = [f"page-{index}.png" for index in range(6)]
    cache._center_index = 0
    cache._protected_indexes = {0, 1}
    cache._configured_prefetch_order = True
    cache._configured_prefetch_ranks = {
        index: rank for rank, index in enumerate((2, 3, 4, 5))
    }
    cache._configured_prefetch_units = ((2, 3), (4, 5))
    entry_bytes = 64 * 1024 * 1024
    cache._cache_entry_bytes = {0: entry_bytes}
    cache._cache_byte_budget = 3 * entry_bytes

    assert cache._limit_wanted_to_capacity(set(range(6))) == {0, 1}

    cache._cache_byte_budget = 4 * entry_bytes
    assert cache._limit_wanted_to_capacity(set(range(6))) == {
        0,
        1,
        2,
        3,
    }


def test_viewer_memory_mode_also_bounds_prepared_pixmaps(
    tmp_path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(
        {
            "viewer_prefetch_preset": "more",
            "viewer_memory_mode": "512",
        }
    )
    window = ViewerWindow(config_manager=config)
    try:
        assert window.viewer_cache_memory_mib == 512
        assert window.image_cache.cache_byte_budget_mib == 512
        assert (
            window.viewer._render_cache_byte_limit
            == 512 * 1024 * 1024
        )
    finally:
        window.close()
        qapp.processEvents()


def test_viewer_memory_setting_is_one_combined_source_and_pixmap_budget(
    tmp_path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(
        {
            "viewer_prefetch_preset": "custom",
            "viewer_memory_mode": "minimal",
        }
    )
    window = ViewerWindow(config_manager=config)
    try:
        bytes_per_source = 32 * 1024 * 1024
        window.image_cache._center_index = 2
        for index in range(3):
            qimage = Mock()
            qimage.isNull.return_value = False
            qimage.sizeInBytes.return_value = bytes_per_source
            window.image_cache._store_cached(
                CachedImage(
                    page_index=index,
                    image_id=f"page-{index}.jpg",
                    qimage=qimage,
                    original_size=(1, 1),
                    error=None,
                    generation=window.image_cache.generation,
                )
            )
        for index in range(2):
            key = ViewerRenderKey(
                f"prepared-{index}",
                index + 1,
                2560,
                2048,
                "standard",
                0,
                1000,
            )
            pixmap = Mock()
            pixmap.width.return_value = 2560
            pixmap.height.return_value = 2048
            window.viewer._render_cache[key] = pixmap

        window._enforce_combined_cache_budget()

        combined = (
            window.image_cache.cache_bytes
            + window.viewer.render_cache_bytes()
        )
        assert combined <= 128 * 1024 * 1024
        assert 2 in window.image_cache._cache
    finally:
        window.close()
        qapp.processEvents()


def test_repeated_split_prepared_plan_reuses_tracked_crop(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        window.viewer_resampling_mode = "pixel"
        window.viewer.set_resampling_modes(normal="pixel")
        window.split_wide_image = True
        window.model.image_ids = ["wide.png"]
        window.model.update_options(view_mode="single")
        window.model.current_index = 0
        window.model._size_cache = {0: (640, 240)}
        window.image_cache._cache = OrderedDict(
            {
                0: CachedImage(
                    0,
                    "wide.png",
                    _qimage(640, 240),
                    (640, 240),
                    None,
                    window.image_cache.generation,
                )
            }
        )
        original = window._viewer_images_for_cached
        calls: list[int] = []
        page_batches: list[list[ViewerImage]] = []

        def counted(cached, **kwargs):
            calls.append(cached.page_index)
            pages = original(cached, **kwargs)
            page_batches.append(pages)
            return pages

        monkeypatch.setattr(
            window,
            "_viewer_images_for_cached",
            counted,
        )

        window._schedule_prepared_display_prefetch()
        assert calls == [0]
        source_image = window.image_cache._cache[0].qimage
        assert source_image is not None
        assert all(
            page.qimage is not None
            and page.qimage.cacheKey() == source_image.cacheKey()
            for page in page_batches[0]
        )
        window._schedule_prepared_display_prefetch()
        assert calls == [0]
        assert len(window.viewer._prepared_requests) == 1
        assert window.viewer.wait_for_rendering()
        qapp.processEvents()
    finally:
        window.close()
        qapp.processEvents()


def test_window_applies_ready_page_after_raw_cache_eviction(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        window.viewer_resampling_mode = "pixel"
        window.viewer.set_resampling_modes(normal="pixel")
        window.model.image_ids = [
            f"page-{index}.png" for index in range(6)
        ]
        window.model.update_options(view_mode="single")
        window.model.current_index = 2
        window.model._size_cache = {
            index: (320, 500) for index in range(6)
        }
        window.image_cache._cache = OrderedDict(
            (
                index,
                CachedImage(
                    index,
                    f"page-{index}.png",
                    _qimage(),
                    (320, 500),
                    None,
                    window.image_cache.generation,
                ),
            )
            for index in range(6)
        )
        window._last_preload_direction = 1
        window._schedule_prepared_display_prefetch()
        assert window.viewer.wait_for_rendering()
        qapp.processEvents()
        assert any(
            key.spread_identity == ((3, "page-3.png"),)
            for key in window.viewer._prepared_units
        )
        window.image_cache._cache.pop(3)
        queued: list[ViewerRenderKey] = []
        monkeypatch.setattr(
            window.viewer,
            "_queue_render",
            lambda _image, key, **_kwargs: queued.append(key),
        )
        commits: list[tuple[str, ...]] = []
        window.viewer.displayCommitted.connect(commits.append)

        window.model.go_to_index(3)
        window._refresh_view()

        assert commits == [("page-3.png",)]
        assert window.viewer.displayed_page_indexes == (3,)
        assert (
            window._applied_display_request_id
            == window._active_request_id
        )
        assert queued == []
        assert window.viewer._images[0].qimage is None

        restored = CachedImage(
            3,
            "page-3.png",
            _qimage(),
            (320, 500),
            None,
            window.image_cache.generation,
        )
        window.image_cache._cache[3] = restored
        window._on_cache_page_loaded(restored)

        assert window.viewer._images[0].qimage is not None
        assert commits == [("page-3.png",)]
        assert queued == []
    finally:
        window.close()
        qapp.processEvents()


def test_window_ready_standard_page_reuses_pixmap_and_attaches_current_source(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        window.model.image_ids = [
            f"page-{index}.png" for index in range(4)
        ]
        window.model.update_options(view_mode="single")
        window.model.current_index = 1
        window.model._size_cache = {
            index: (320, 500) for index in range(4)
        }
        window.image_cache._cache = OrderedDict(
            (
                index,
                CachedImage(
                    index,
                    f"page-{index}.png",
                    _qimage(),
                    (320, 500),
                    None,
                    window.image_cache.generation,
                ),
            )
            for index in range(4)
        )
        window._schedule_prepared_display_prefetch()
        assert window.viewer.wait_for_rendering()
        qapp.processEvents()
        queued: list[ViewerRenderKey] = []
        monkeypatch.setattr(
            window.viewer,
            "_queue_render",
            lambda _image, key, **_kwargs: queued.append(key),
        )

        window.model.go_to_index(2)
        window._refresh_view()

        assert queued == []
        assert window.viewer.displayed_page_indexes == (2,)
        current = window.viewer._images[0]
        assert current.display_prepared
        assert current.pixmap is not None
        assert current.qimage is not None
    finally:
        window.close()
        qapp.processEvents()


def test_window_cold_display_demand_coalesces_to_latest_after_input_idle(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(
        {
            "view_mode": "single",
            "viewer_prefetch_preset": "custom",
            "viewer_prefetch_image_forward_units": 0,
            "viewer_prefetch_image_backward_units": 0,
        }
    )
    window = ViewerWindow(config_manager=config)
    try:
        window.model.image_ids = [
            f"page-{index}.png" for index in range(6)
        ]
        window.model.update_options(view_mode="single")
        window.model._size_cache = {
            index: (320, 500) for index in range(6)
        }
        window.image_cache._cache = OrderedDict(
            (
                index,
                CachedImage(
                    index,
                    f"page-{index}.png",
                    _qimage(),
                    (320, 500),
                    None,
                    window.image_cache.generation,
                ),
            )
            for index in range(6)
        )
        window._refresh_view()
        assert window.viewer.wait_for_rendering()
        qapp.processEvents()
        assert window.viewer.displayed_page_indexes == (0,)
        window._prepared_display_timer.stop()
        window.viewer.prepare_display_units(())

        applied: list[tuple[str, ...]] = []
        original_set_pages = window.viewer.set_pages

        def record_set_pages(spread, pages, **kwargs):
            applied.append(tuple(page.image_id for page in pages))
            original_set_pages(spread, pages, **kwargs)

        monkeypatch.setattr(window.viewer, "set_pages", record_set_pages)
        for index in (1, 2, 3):
            window.model.go_to_index(index)
            window._refresh_view()

        assert applied == []
        assert window._display_demand_timer.isActive()
        assert window._pending_display_demand is not None
        assert window._pending_display_demand[0] == window._active_request_id
        assert tuple(
            page.image_id
            for page in window._pending_display_demand[3]
        ) == ("page-3.png",)

        window._display_demand_timer.stop()
        window._apply_pending_display_demand()

        assert applied == [("page-3.png",)]
        # set_pages starts the final resize but is not a presentation commit.
        assert window._applied_display_request_id != window._active_request_id
        assert window.presentation_state.displayed_page == 0
        assert window.slider.value() == 0
        assert window.viewer.wait_for_rendering()
        qapp.processEvents()
        assert window._applied_display_request_id == window._active_request_id
        assert window.presentation_state.displayed_page == 3
        assert window.slider.value() == 3
        assert window.viewer.wait_for_rendering()
        qapp.processEvents()
        assert window.viewer.displayed_page_indexes == (3,)
    finally:
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize("reading_direction", ("ltr", "rtl"))
def test_window_scheduler_prefetches_next_spread_as_one_unit_after_commit(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
    reading_direction: str,
) -> None:
    config = ConfigManager(tmp_path / f"{reading_direction}.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    window.viewer_resampling_mode = "pixel"
    window.viewer.set_resampling_modes(normal="pixel")
    window.model.image_ids = [f"page-{index}.png" for index in range(8)]
    window.model.update_options(
        view_mode="spread",
        reading_direction=reading_direction,
        single_first_page=False,
    )
    window.model.current_index = 2
    window.model._size_cache = {
        index: ((3600, 6500) if index % 2 == 0 else (840, 1200))
        for index in range(8)
    }
    window.model._rebuild_spread_boundaries()
    window.image_cache._cache = OrderedDict(
        (
            index,
            CachedImage(
                index,
                f"page-{index}.png",
                _qimage(
                    360 if index % 2 == 0 else 84,
                    650 if index % 2 == 0 else 120,
                ),
                window.model._size_cache[index],
                None,
                window.image_cache.generation,
            ),
        )
        for index in range(8)
    )
    plans: list[list[tuple[int, DisplaySpread, list[ViewerImage], bool]]] = []
    monkeypatch.setattr(
        window.viewer,
        "prepare_display_units",
        lambda units: plans.append(list(units)),
    )
    window._last_preload_direction = 1

    window.viewer.displayCommitted.emit(("page-2.png", "page-3.png"))

    assert plans == []
    assert window._prepared_display_timer.isActive()
    window._prepared_display_timer.stop()
    window._prepared_display_timer.timeout.emit()
    assert plans
    current, next_unit, previous, next_next = plans[-1]
    assert [item[0] for item in plans[-1]] == [0, 1, 2, 3]
    assert next_unit[1].start_index == 4
    assert len(next_unit[1].slots) == 2
    assert len(next_unit[2]) == 2
    expected_order = (
        (5, 4) if reading_direction == "rtl" else (4, 5)
    )
    assert tuple(page.page_index for page in next_unit[2]) == expected_order
    assert all(item[3] for item in (current, next_unit, previous))
    assert not next_next[3]
    window.close()
    qapp.processEvents()


def test_display_commit_defers_bookmark_menu_rebuild_until_menu_open(
    tmp_path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    rebuilds: list[int] = []
    original = ViewerWindow._rebuild_bookmark_menu

    def record_rebuild(window: ViewerWindow) -> None:
        rebuilds.append(window.model.current_index)
        original(window)

    monkeypatch.setattr(
        ViewerWindow,
        "_rebuild_bookmark_menu",
        record_rebuild,
    )
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    try:
        initial_count = len(rebuilds)

        window._on_prepared_display_committed(("page-0.png",))

        assert len(rebuilds) == initial_count
        window.bookmark_menu.aboutToShow.emit()
        assert len(rebuilds) == initial_count + 1
    finally:
        window.close()
        qapp.processEvents()
