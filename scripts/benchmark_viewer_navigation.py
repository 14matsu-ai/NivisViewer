from __future__ import annotations

import argparse
import atexit
from contextlib import contextmanager
import ctypes
import io
import json
import os
import statistics
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator
import zipfile

from PIL import Image
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from app.config_manager import ConfigManager
from app.image_work_coordinator import ImageWorkCoordinator
from app.viewer_render import ViewerRenderTask
from app.viewer_window import ViewerWindow


def _jpeg_bytes(
    size: tuple[int, int],
    *,
    page_index: int = 0,
) -> bytes:
    output = io.BytesIO()
    color = (
        (73 + page_index * 29) % 256,
        (106 + page_index * 47) % 256,
        (140 + page_index * 61) % 256,
    )
    with Image.new("RGB", size, color) as image:
        image.save(output, "JPEG", quality=92, subsampling=0)
    return output.getvalue()


def _make_zip(path: Path, *, pages: int, size: tuple[int, int]) -> None:
    # Adjacent pages still have distinct colors for frame-retention checks,
    # while limiting full-size Pillow/JPEG generation keeps the benchmark
    # setup comfortably below the navigation work being measured.
    variant_count = max(1, min(pages, 8))
    payloads = tuple(
        _jpeg_bytes(size, page_index=index)
        for index in range(variant_count)
    )
    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as archive:
        for index in range(pages):
            archive.writestr(
                f"日本語ページ/{index:03d}.jpg",
                payloads[index % variant_count],
            )


def _process_memory_mib() -> dict[str, float]:
    if os.name != "nt":
        return {}

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    get_current_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
    get_current_process.restype = ctypes.c_void_p
    get_process_memory_info.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ProcessMemoryCounters),
        ctypes.c_ulong,
    )
    get_process_memory_info.restype = ctypes.c_int
    if not get_process_memory_info(
        get_current_process(),
        ctypes.byref(counters),
        counters.cb,
    ):
        return {}
    mebibyte = 1024 * 1024
    return {
        "working_set_mib": round(
            counters.WorkingSetSize / mebibyte,
            2,
        ),
        "peak_working_set_mib": round(
            counters.PeakWorkingSetSize / mebibyte,
            2,
        ),
    }


def _pump_until(
    application: QApplication,
    predicate,
    *,
    timeout_seconds: float = 15.0,
    observer=None,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        application.processEvents()
        if observer is not None:
            observer()
        if predicate():
            return
        time.sleep(0.001)
    raise TimeoutError("offscreen Viewer benchmark timed out")


def _render_once(window: ViewerWindow) -> None:
    window.viewer.render(QPixmap(window.viewer.size()))


def _wait_for_page(
    application: QApplication,
    window: ViewerWindow,
    page_index: int,
    *,
    observer=None,
) -> None:
    _pump_until(
        application,
        lambda: window.viewer.displayed_page_indexes == (page_index,),
        observer=observer,
    )
    _render_once(window)
    application.processEvents()
    if observer is not None:
        observer()


def _wait_for_warm_window(
    application: QApplication,
    window: ViewerWindow,
    *,
    required_page_indexes: tuple[int, ...],
) -> None:
    required = {
        int(index)
        for index in required_page_indexes
        if 0 <= int(index) < window.model.total_pages
    }
    # One paint releases raster prefetch. Repainting on every poll re-arms the
    # prepared-display timer and makes an otherwise reachable warm state race
    # against its own observer.
    _render_once(window)
    application.processEvents()
    stable_since: float | None = None

    def warm_window_ready() -> bool:
        nonlocal stable_since
        prepared = {
            index
            for key in window.viewer._prepared_units
            for index, _image_id in key.spread_identity
        }
        settled = (
            not window.image_cache.has_unfinished_tasks()
            and not window.viewer._render_tasks
            and window._pending_decode_demand is None
            and window._pending_display_demand is None
            and window.viewer._pending_display is None
            and window._raster_prefetch_after_paint is None
            and not window._decode_demand_timer.isActive()
            and not window._display_demand_timer.isActive()
            and not window._prepared_display_timer.isActive()
            and required.issubset(prepared)
        )
        if not settled:
            stable_since = None
            return False
        now = time.monotonic()
        if stable_since is None:
            stable_since = now
            return False
        return now - stable_since >= 0.003

    _pump_until(
        application,
        warm_window_ready,
        timeout_seconds=30.0,
    )
    application.processEvents()


def _move_and_measure(
    application: QApplication,
    window: ViewerWindow,
    move,
    expected_page: int,
) -> tuple[float, float]:
    started = time.perf_counter()
    move()
    handler_finished = time.perf_counter()
    _pump_until(
        application,
        lambda: window.viewer.displayed_page_indexes == (expected_page,),
    )
    _render_once(window)
    painted_at = time.perf_counter()
    return (
        (handler_finished - started) * 1000,
        (painted_at - started) * 1000,
    )


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "median_ms": round(statistics.median(values), 3),
        "max_ms": round(max(values), 3),
    }


def _wait_for_idle(
    application: QApplication,
    window: ViewerWindow,
    *,
    timeout_seconds: float = 30.0,
    observer=None,
) -> None:
    # One explicit paint releases the production after-paint stage. Do not
    # repaint on every predicate check: each paint re-arms the 16 ms prepared
    # timer and would itself prevent a stable idle observation.
    _render_once(window)
    application.processEvents()
    stable_since: float | None = None

    def idle() -> bool:
        nonlocal stable_since
        settled = (
            not window.image_cache.has_unfinished_tasks()
            and not window.viewer._render_tasks
            and window._pending_decode_demand is None
            and window._pending_display_demand is None
            and window.viewer._pending_display is None
            and window._raster_prefetch_after_paint is None
            and not window._decode_demand_timer.isActive()
            and not window._display_demand_timer.isActive()
            and not window._prepared_display_timer.isActive()
        )
        if not settled:
            stable_since = None
            return False
        now = time.monotonic()
        if stable_since is None:
            stable_since = now
            return False
        return now - stable_since >= 0.003

    _pump_until(
        application,
        idle,
        timeout_seconds=timeout_seconds,
        observer=observer,
    )
    application.processEvents()
    if observer is not None:
        observer()


@contextmanager
def _viewer_case(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    case_name: str,
    center_index: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
    forward_units: int = 0,
    backward_units: int = 0,
    cache_mib: int = 512,
) -> Iterator[ViewerWindow]:
    config = ConfigManager(root / f"config-{case_name}.json")
    config.load()
    config.apply(
        {
            "view_mode": "single",
            "single_first_page": False,
            "viewer_resampling_mode": "standard",
            "fit_mode": "fit_window",
            "viewer_prefetch_preset": "custom",
            "viewer_prefetch_direction_priority_enabled": True,
            "viewer_prefetch_image_forward_units": int(forward_units),
            "viewer_prefetch_image_backward_units": int(backward_units),
            "viewer_cache_max_memory_mib": int(cache_mib),
            "show_page_list": False,
        },
        save=True,
    )
    coordinator = ImageWorkCoordinator(max_workers=1)
    window = ViewerWindow(
        config_manager=config,
        image_work_coordinator=coordinator,
    )
    shutdown_complete = False

    def shutdown_window() -> None:
        nonlocal shutdown_complete
        if shutdown_complete:
            return
        shutdown_complete = True
        window.prepare_shutdown(wait_msecs=5000)
        window.close()
        application.processEvents()
        coordinator.shutdown(wait_msecs=5000)

    atexit.register(shutdown_window)
    try:
        if not use_target_decode:
            window._current_raster_decode_bounds = (  # type: ignore[method-assign]
                lambda: None
            )
        window.resize(*viewport_size)
        application.processEvents()
        window.open_path(archive_path)
        if not window.book_session.wait_for_async(10_000):
            raise TimeoutError(
                f"{case_name}: book source preparation timed out"
            )
        _pump_until(
            application,
            lambda: window.model.total_pages > 0,
            timeout_seconds=10.0,
        )
        _wait_for_page(application, window, 0)
        requested_center = max(
            0,
            min(int(center_index), window.model.total_pages - 1),
        )
        if requested_center != 0:
            window._go_to_index_with_history(requested_center)
            _wait_for_page(application, window, requested_center)
        _wait_for_idle(application, window)
        yield window
    finally:
        shutdown_window()
        atexit.unregister(shutdown_window)


class _WorkerTimeline:
    def __init__(self, source) -> None:
        self.source = source
        self.events: list[tuple[str, str, float, int]] = []
        self._lock = threading.Lock()
        self._target_decoder = getattr(source, "open_qimage_at_most")
        self._full_decoder = getattr(source, "open_image")
        self._render_run = ViewerRenderTask.run

    def _record(self, stage: str, image_id: str) -> None:
        with self._lock:
            self.events.append(
                (
                    str(stage),
                    str(image_id),
                    time.perf_counter(),
                    threading.get_ident(),
                )
            )

    def __enter__(self) -> "_WorkerTimeline":
        def target_decode(image_id, maximum_size):
            self._record("decode_started", image_id)
            try:
                return self._target_decoder(image_id, maximum_size)
            finally:
                self._record("decode_finished", image_id)

        def full_decode(image_id):
            self._record("decode_started", image_id)
            try:
                return self._full_decoder(image_id)
            finally:
                self._record("decode_finished", image_id)

        timeline = self

        def render_run(task: ViewerRenderTask) -> None:
            timeline._record("render_started", task.key.image_id)
            try:
                timeline._render_run(task)
            finally:
                timeline._record("render_finished", task.key.image_id)

        self.source.open_qimage_at_most = target_decode
        self.source.open_image = full_decode
        ViewerRenderTask.run = render_run
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.source.__dict__.pop("open_qimage_at_most", None)
        self.source.__dict__.pop("open_image", None)
        ViewerRenderTask.run = self._render_run

    def clear(self) -> None:
        with self._lock:
            self.events.clear()

    def stages(self, stage: str) -> list[str]:
        with self._lock:
            return [
                image_id
                for event_stage, image_id, _timestamp, _thread_id
                in self.events
                if event_stage == stage
            ]

    def serializable_events(self) -> list[dict[str, object]]:
        with self._lock:
            events = list(self.events)
        if not events:
            return []
        origin = events[0][2]
        return [
            {
                "stage": stage,
                "image_id": image_id,
                "at_ms": round((timestamp - origin) * 1000, 3),
                "thread_id": thread_id,
            }
            for stage, image_id, timestamp, thread_id in events
        ]


def _matches_image_id(candidate: str, image_id: str) -> bool:
    return candidate == image_id or candidate.startswith(f"{image_id}#")


def _assert_page_cold(window: ViewerWindow, page_index: int) -> None:
    image_id = window.model.image_id_at(page_index)
    if image_id is None:
        raise AssertionError(f"no image identity for page {page_index}")
    if page_index in window.image_cache._cache:
        raise AssertionError(f"page {page_index} is present in raw cache")
    if any(
        index == page_index
        for _generation, index in window.image_cache._tasks
    ):
        raise AssertionError(f"page {page_index} has a decode task")
    if any(
        index == page_index
        for _generation, index in window.image_cache._in_flight
    ):
        raise AssertionError(f"page {page_index} has an in-flight decode")

    tracked_units = (
        tuple(window.viewer._prepared_units)
        + tuple(window.viewer._prepared_requests)
    )
    if any(
        any(index == page_index for index, _identity in key.spread_identity)
        for key in tracked_units
    ):
        raise AssertionError(f"page {page_index} is prepared or preparing")
    if any(
        _matches_image_id(key.image_id, image_id)
        for key in window.viewer._render_cache
    ):
        raise AssertionError(f"page {page_index} is in render cache")
    if any(
        _matches_image_id(key.image_id, image_id)
        for key in window.viewer._render_pending
    ):
        raise AssertionError(f"page {page_index} has a pending render")
    if any(
        _matches_image_id(candidate, image_id)
        for candidate in window.viewer._last_rendered_by_image
    ):
        raise AssertionError(f"page {page_index} has a rendered fallback")


def _frame_snapshot(window: ViewerWindow) -> tuple[
    tuple[int, ...],
    tuple[str, ...],
    tuple[int, ...],
]:
    _render_once(window)
    return (
        tuple(window.viewer.displayed_page_indexes),
        tuple(image.image_id for image in window.viewer._images),
        tuple(
            pixmap.cacheKey()
            for _rect, pixmap in window.viewer._last_draw_layout
        ),
    )


def _move_cold_and_measure(
    application: QApplication,
    window: ViewerWindow,
    move,
    expected_page: int,
    *,
    observer=None,
) -> tuple[float, float]:
    previous_frame = _frame_snapshot(window)
    started = time.perf_counter()
    move()
    handler_finished = time.perf_counter()
    immediate_frame = _frame_snapshot(window)
    if immediate_frame != previous_frame:
        raise AssertionError(
            "cold navigation replaced the completed frame before target ready"
        )
    _wait_for_page(
        application,
        window,
        expected_page,
        observer=observer,
    )
    painted_at = time.perf_counter()
    return (
        (handler_finished - started) * 1000,
        (painted_at - started) * 1000,
    )


def _force_page_cold(
    application: QApplication,
    window: ViewerWindow,
    page_index: int,
) -> None:
    if page_index in window.viewer.displayed_page_indexes:
        raise AssertionError("cannot evict the currently displayed page")
    _wait_for_idle(application, window)
    previous_frame = _frame_snapshot(window)
    window.image_cache._evict_cached(page_index)
    window.viewer.prepare_display_units(())
    image_id = window.model.image_id_at(page_index)
    if image_id is None:
        raise AssertionError(f"no image identity for page {page_index}")
    for key in tuple(window.viewer._render_cache):
        if _matches_image_id(key.image_id, image_id):
            window.viewer._render_cache.pop(key, None)
    for candidate in tuple(window.viewer._last_rendered_by_image):
        if _matches_image_id(candidate, image_id):
            window.viewer._last_rendered_by_image.pop(candidate, None)
    if _frame_snapshot(window) != previous_frame:
        raise AssertionError("forcing a non-current cold miss changed the frame")
    _assert_page_cold(window, page_index)


def _page_index_for_artifact(
    window: ViewerWindow,
    artifact_id: str,
) -> int | None:
    for index, image_id in enumerate(window.model.image_ids):
        if _matches_image_id(artifact_id, image_id):
            return index
    return None


def _logical_cache_snapshot(window: ViewerWindow) -> dict[str, object]:
    decoded = set(window.image_cache._cache)
    prepared = {
        index
        for key in (
            tuple(window.viewer._prepared_units)
            + tuple(window.viewer._prepared_requests)
        )
        for index, _image_id in key.spread_identity
    }
    rendered = {
        index
        for key in window.viewer._render_cache
        if (
            index := _page_index_for_artifact(window, key.image_id)
        )
        is not None
    }
    source_bytes = int(window.image_cache.cache_bytes)
    render_bytes = int(window.viewer.render_cache_bytes())
    return {
        "decoded_pages": sorted(decoded),
        "prepared_pages": sorted(prepared),
        "rendered_pages": sorted(rendered),
        "retained_pages": sorted(decoded | prepared | rendered),
        "decoded_entries": len(decoded),
        "prepared_units": len(window.viewer._prepared_units),
        "prepared_requests": len(window.viewer._prepared_requests),
        "render_entries": len(window.viewer._render_cache),
        "source_mib": round(source_bytes / (1024 * 1024), 2),
        "render_mib": round(render_bytes / (1024 * 1024), 2),
        "combined_mib": round(
            (source_bytes + render_bytes) / (1024 * 1024),
            2,
        ),
    }


def _run_forward_cold(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
) -> dict[str, object]:
    center = min(pages - 2, pages // 2)
    with _viewer_case(
        application,
        root,
        archive_path,
        case_name="cold-forward",
        center_index=center,
        viewport_size=viewport_size,
        use_target_decode=use_target_decode,
    ) as window:
        target = center + 1
        target_id = window.model.image_id_at(target)
        assert target_id is not None
        _assert_page_cold(window, target)
        commits: list[tuple[str, ...]] = []
        window.viewer.displayCommitted.connect(commits.append)
        with _WorkerTimeline(window.image_cache.source) as timeline:
            handler, paint = _move_cold_and_measure(
                application,
                window,
                window.next_page,
                target,
            )
            _wait_for_idle(application, window)
            decode_started = timeline.stages("decode_started")
            if decode_started != [target_id]:
                raise AssertionError(
                    "forward cold started unexpected decode jobs: "
                    f"{decode_started!r}"
                )
            if commits != [(target_id,)]:
                raise AssertionError(
                    f"forward cold commits were not atomic: {commits!r}"
                )
            if window._raster_prefetch_after_paint is not None:
                raise AssertionError("forward cold prefetch remained staged")
            if window._raster_interactive_lane_held:
                raise AssertionError("forward cold retained interactive lane")
            return {
                "target_page": target,
                "handler_ms": round(handler, 3),
                "request_to_paint_ms": round(paint, 3),
                "decode_started": decode_started,
                "commits": [list(commit) for commit in commits],
                "old_frame_held": True,
                "timeline": timeline.serializable_events(),
            }


def _run_reversal_cold(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
) -> dict[str, object]:
    center = max(1, min(pages - 3, pages // 2))
    with _viewer_case(
        application,
        root,
        archive_path,
        case_name="cold-reversal",
        center_index=center,
        viewport_size=viewport_size,
        use_target_decode=use_target_decode,
    ) as window:
        source = window.image_cache.source
        if source is None or not hasattr(source, "_read_entry_stream"):
            raise AssertionError("reversal benchmark requires ZIP source")
        old_running = center + 1
        old_queued = center + 2
        target = center - 1
        old_running_id = window.model.image_id_at(old_running)
        old_queued_id = window.model.image_id_at(old_queued)
        target_id = window.model.image_id_at(target)
        assert old_running_id and old_queued_id and target_id
        _assert_page_cold(window, old_running)
        _assert_page_cold(window, old_queued)
        _assert_page_cold(window, target)

        original_read = source._read_entry_stream
        old_started = threading.Event()
        old_cancelled = threading.Event()
        emergency_release = threading.Event()

        def blocking_read(image_id, cancelled):
            if image_id == old_running_id:
                old_started.set()
                while not emergency_release.wait(0.001):
                    if cancelled.is_set():
                        old_cancelled.set()
                        source._raise_if_cancelled(cancelled)
            return original_read(image_id, cancelled)

        source._read_entry_stream = blocking_read
        commits: list[tuple[str, ...]] = []
        window.viewer.displayCommitted.connect(commits.append)
        try:
            with _WorkerTimeline(source) as timeline:
                window.image_prefetch_forward_units = 2
                window.image_prefetch_backward_units = 0
                window._last_preload_direction = 1
                window._last_preload_center = center
                window._preload_image_source(center, (center,))
                _pump_until(
                    application,
                    old_started.is_set,
                    timeout_seconds=5.0,
                )
                # The old-direction work is now running/queued. The reversed
                # target must cancel it, and no post-paint prefetch should
                # obscure the exact start order being asserted below.
                window.image_prefetch_forward_units = 0
                window.image_prefetch_backward_units = 0
                handler, paint = _move_cold_and_measure(
                    application,
                    window,
                    window.previous_page,
                    target,
                )
                _wait_for_idle(application, window)
                if not old_cancelled.is_set():
                    raise AssertionError(
                        "running old-direction ZIP request was not cancelled"
                    )
                decode_started = timeline.stages("decode_started")
                if decode_started != [old_running_id, target_id]:
                    raise AssertionError(
                        "reversal started stale queued work or wrong order: "
                        f"{decode_started!r}"
                    )
                if old_queued_id in decode_started:
                    raise AssertionError(
                        "queued old-direction page started after reversal"
                    )
                if old_running in window.image_cache._cache:
                    raise AssertionError(
                        "cancelled old-direction page entered raw cache"
                    )
                if commits != [(target_id,)]:
                    raise AssertionError(
                        f"reversal commits were not atomic: {commits!r}"
                    )
                return {
                    "target_page": target,
                    "cancelled_running_page": old_running,
                    "suppressed_queued_page": old_queued,
                    "handler_ms": round(handler, 3),
                    "request_to_paint_ms": round(paint, 3),
                    "decode_started": decode_started,
                    "old_request_cancelled": True,
                    "old_frame_held": True,
                    "commits": [list(commit) for commit in commits],
                    "timeline": timeline.serializable_events(),
                }
        finally:
            emergency_release.set()
            source.__dict__.pop("_read_entry_stream", None)


def _run_roundtrip_cold(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
    legs: int = 6,
) -> dict[str, object]:
    first = min(pages - 2, pages // 2)
    second = first + 1
    with _viewer_case(
        application,
        root,
        archive_path,
        case_name="cold-roundtrip",
        center_index=first,
        viewport_size=viewport_size,
        use_target_decode=use_target_decode,
    ) as window:
        first_id = window.model.image_id_at(first)
        second_id = window.model.image_id_at(second)
        assert first_id and second_id
        handlers: list[float] = []
        paints: list[float] = []
        expected_decode_ids: list[str] = []
        commits: list[tuple[str, ...]] = []
        window.viewer.displayCommitted.connect(commits.append)
        with _WorkerTimeline(window.image_cache.source) as timeline:
            for leg in range(max(2, int(legs))):
                target = second if leg % 2 == 0 else first
                target_id = second_id if target == second else first_id
                _force_page_cold(application, window, target)
                move = (
                    window.next_page
                    if target == second
                    else window.previous_page
                )
                handler, paint = _move_cold_and_measure(
                    application,
                    window,
                    move,
                    target,
                )
                _wait_for_idle(application, window)
                handlers.append(handler)
                paints.append(paint)
                expected_decode_ids.append(target_id)

            decode_started = timeline.stages("decode_started")
            if decode_started != expected_decode_ids:
                raise AssertionError(
                    "roundtrip did not remain cold on every leg: "
                    f"{decode_started!r}"
                )
            expected_commits = [
                (image_id,) for image_id in expected_decode_ids
            ]
            if commits != expected_commits:
                raise AssertionError(
                    f"roundtrip commits were not one-per-leg: {commits!r}"
                )
            return {
                "legs": len(handlers),
                "handler": _summary(handlers),
                "request_to_paint": _summary(paints),
                "decode_started": decode_started,
                "commits": [list(commit) for commit in commits],
                "old_frame_held": True,
            }


def _run_rapid_final_cold(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
) -> dict[str, object]:
    center = pages // 2
    final = min(pages - 1, center + 8)
    crossed = tuple(range(center + 1, final))
    targets = tuple(range(center + 1, final + 1))
    with _viewer_case(
        application,
        root,
        archive_path,
        case_name="cold-rapid-final",
        center_index=center,
        viewport_size=viewport_size,
        use_target_decode=use_target_decode,
    ) as window:
        for index in targets:
            _assert_page_cold(window, index)
        final_id = window.model.image_id_at(final)
        assert final_id is not None
        previous_frame = _frame_snapshot(window)
        commits: list[tuple[str, ...]] = []
        window.viewer.displayCommitted.connect(commits.append)
        handler_times: list[float] = []
        with _WorkerTimeline(window.image_cache.source) as timeline:
            started = time.perf_counter()
            for _target in targets:
                handler_started = time.perf_counter()
                window.next_page()
                handler_times.append(
                    (time.perf_counter() - handler_started) * 1000
                )
            handlers_finished = time.perf_counter()
            if timeline.stages("decode_started"):
                raise AssertionError(
                    "rapid crossed pages started before input idle"
                )
            if _frame_snapshot(window) != previous_frame:
                raise AssertionError(
                    "rapid input replaced the old frame before final ready"
                )
            _wait_for_page(application, window, final)
            painted = time.perf_counter()
            _wait_for_idle(application, window)
            decode_started = timeline.stages("decode_started")
            if decode_started != [final_id]:
                raise AssertionError(
                    "rapid input did not coalesce to final page: "
                    f"{decode_started!r}"
                )
            if commits != [(final_id,)]:
                raise AssertionError(
                    f"rapid final commits were not atomic: {commits!r}"
                )
            for index in crossed:
                _assert_page_cold(window, index)
            return {
                "requests": len(targets),
                "final_page": final,
                "handler": _summary(handler_times),
                "handlers_total_ms": round(
                    (handlers_finished - started) * 1000,
                    3,
                ),
                "request_to_final_paint_ms": round(
                    (painted - started) * 1000,
                    3,
                ),
                "decode_started": decode_started,
                "suppressed_pages": list(crossed),
                "commits": [list(commit) for commit in commits],
                "old_frame_held": True,
            }


def _run_dark_frame_check(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
) -> dict[str, object]:
    center = min(pages - 2, pages // 2)
    with _viewer_case(
        application,
        root,
        archive_path,
        case_name="cold-dark-frame",
        center_index=center,
        viewport_size=viewport_size,
        use_target_decode=use_target_decode,
    ) as window:
        source = window.image_cache.source
        if source is None or not hasattr(source, "_read_entry_stream"):
            raise AssertionError("dark-frame check requires ZIP source")
        target = center + 1
        target_id = window.model.image_id_at(target)
        assert target_id is not None
        _assert_page_cold(window, target)
        previous_frame = _frame_snapshot(window)
        original_read = source._read_entry_stream
        original_clear = window.viewer.clear
        target_started = threading.Event()
        release = threading.Event()

        def blocking_read(image_id, cancelled):
            if image_id == target_id:
                target_started.set()
                while not release.wait(0.001):
                    source._raise_if_cancelled(cancelled)
            return original_read(image_id, cancelled)

        def forbidden_clear() -> None:
            raise AssertionError("ViewerWidget.clear() ran during cold miss")

        source._read_entry_stream = blocking_read
        window.viewer.clear = forbidden_clear
        commits: list[tuple[str, ...]] = []
        window.viewer.displayCommitted.connect(commits.append)
        try:
            window.next_page()
            _pump_until(
                application,
                target_started.is_set,
                timeout_seconds=5.0,
            )
            for _iteration in range(3):
                application.processEvents()
                if _frame_snapshot(window) != previous_frame:
                    raise AssertionError(
                        "blocked cold load exposed a dark or partial frame"
                    )
            if commits:
                raise AssertionError(
                    f"blocked target committed prematurely: {commits!r}"
                )
            requested_while_blocked = window.model.focused_index
            slider_while_blocked = window.slider.value()
            displayed_while_blocked = tuple(
                window.viewer.displayed_page_indexes
            )
            release.set()
            _wait_for_page(application, window, target)
            _wait_for_idle(application, window)
            if commits != [(target_id,)]:
                raise AssertionError(
                    f"dark-frame target commit mismatch: {commits!r}"
                )
            return {
                "target_page": target,
                "old_frame_held": True,
                "clear_called": False,
                "commits_while_blocked": 0,
                "requested_page_while_blocked": requested_while_blocked,
                "slider_page_while_blocked": slider_while_blocked,
                "displayed_pages_while_blocked": list(
                    displayed_while_blocked
                ),
                "final_commits": [list(commit) for commit in commits],
            }
        finally:
            release.set()
            source.__dict__.pop("_read_entry_stream", None)
            window.viewer.clear = original_clear


def _run_memory_pressure(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
) -> dict[str, object]:
    # Keep this below the total target-decoded footprint of the 21-page book
    # so the run exercises real cross-cache eviction rather than only
    # reporting an unconstrained steady state.
    cache_mib = 128
    with _viewer_case(
        application,
        root,
        archive_path,
        case_name="memory-pressure",
        center_index=0,
        viewport_size=viewport_size,
        use_target_decode=use_target_decode,
        forward_units=6,
        backward_units=4,
        cache_mib=cache_mib,
    ) as window:
        working_set_samples: list[float] = []

        def sample_memory() -> None:
            memory = _process_memory_mib()
            value = memory.get("working_set_mib")
            if value is not None:
                working_set_samples.append(float(value))

        sample_memory()
        baseline_working_set = (
            working_set_samples[-1] if working_set_samples else None
        )
        final_target = min(pages - 3, 12)
        for target in range(1, final_target + 1):
            window.next_page()
            _wait_for_page(
                application,
                window,
                target,
                observer=sample_memory,
            )
        _wait_for_idle(
            application,
            window,
            observer=sample_memory,
        )
        _wait_for_warm_window(
            application,
            window,
            required_page_indexes=tuple(
                index
                for index in (
                    window.model.focused_index - 1,
                    window.model.focused_index,
                    window.model.focused_index + 1,
                )
                if 0 <= index < pages
            ),
        )
        sample_memory()
        snapshot = _logical_cache_snapshot(window)
        budget_bytes = cache_mib * 1024 * 1024
        combined_bytes = (
            window.image_cache.cache_bytes
            + window.viewer.render_cache_bytes()
        )
        current = window.model.focused_index
        protected = {
            index
            for index in (current - 1, current, current + 1)
            if 0 <= index < pages
        }
        protected_source_bytes = sum(
            window.image_cache._cache_entry_bytes.get(index, 0)
            for index in protected
        )
        protected_render_bytes = sum(
            pixmap.width() * pixmap.height() * 4
            for key, pixmap in window.viewer._render_cache.items()
            if _page_index_for_artifact(window, key.image_id) in protected
        )
        protected_artifact_bytes = (
            protected_source_bytes + protected_render_bytes
        )
        # Current/next/previous retention is a stronger invariant than the
        # configured byte target.  Full 4096 x 6500 sources can make that
        # minimum protected set larger than 128 MiB; accept and report only
        # that necessary overcommit.  If the protected floor would fit, any
        # over-budget result is an eviction regression.
        if (
            combined_bytes > budget_bytes
            and protected_artifact_bytes <= budget_bytes
        ):
            raise AssertionError(
                "combined logical Viewer cache exceeded the configured "
                "budget although the protected working set would fit: "
                f"combined={combined_bytes}, "
                f"protected={protected_artifact_bytes}, "
                f"budget={budget_bytes}, snapshot={snapshot!r}"
            )
        retained = set(snapshot["retained_pages"])
        if not protected.issubset(retained):
            raise AssertionError(
                "current/next/previous were not retained under memory load: "
                f"missing={sorted(protected - retained)}"
            )
        process_memory = _process_memory_mib()
        sampled_max = (
            max(working_set_samples) if working_set_samples else None
        )
        return {
            "configured_budget_mib": cache_mib,
            "logical_cache": snapshot,
            "protected_pages": sorted(protected),
            "protected_pages_retained": True,
            "logical_budget_respected": combined_bytes <= budget_bytes,
            "protected_artifact_mib": round(
                protected_artifact_bytes / (1024 * 1024),
                2,
            ),
            "protected_overcommit_required": (
                protected_artifact_bytes > budget_bytes
            ),
            "viewer_baseline_working_set_mib": baseline_working_set,
            "viewer_sampled_max_working_set_mib": sampled_max,
            "viewer_working_set_delta_mib": (
                None
                if baseline_working_set is None or sampled_max is None
                else round(sampled_max - baseline_working_set, 2)
            ),
            "process_memory": process_memory,
            "note": (
                "Process peak includes temporary ZIP/JPEG generation; "
                "sampled delta starts after the initial Viewer is ready."
            ),
        }


def _run_cold_matrix(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    cold = {
        "forward": _run_forward_cold(
            application,
            root,
            archive_path,
            pages=pages,
            viewport_size=viewport_size,
            use_target_decode=use_target_decode,
        ),
        "reversal": _run_reversal_cold(
            application,
            root,
            archive_path,
            pages=pages,
            viewport_size=viewport_size,
            use_target_decode=use_target_decode,
        ),
        "roundtrip": _run_roundtrip_cold(
            application,
            root,
            archive_path,
            pages=pages,
            viewport_size=viewport_size,
            use_target_decode=use_target_decode,
        ),
        "rapid_final": _run_rapid_final_cold(
            application,
            root,
            archive_path,
            pages=pages,
            viewport_size=viewport_size,
            use_target_decode=use_target_decode,
        ),
        "dark_frame": _run_dark_frame_check(
            application,
            root,
            archive_path,
            pages=pages,
            viewport_size=viewport_size,
            use_target_decode=use_target_decode,
        ),
    }
    memory = _run_memory_pressure(
        application,
        root,
        archive_path,
        pages=pages,
        viewport_size=viewport_size,
        use_target_decode=use_target_decode,
    )
    return cold, memory


def _run_ready_sequences(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
) -> dict[str, object]:
    center = pages // 2
    with _viewer_case(
        application,
        root,
        archive_path,
        case_name="ready",
        center_index=center,
        viewport_size=viewport_size,
        use_target_decode=use_target_decode,
        forward_units=6,
        backward_units=4,
        cache_mib=512,
    ) as window:
        required_ready = (
            tuple(range(center, min(pages, center + 6)))
            if use_target_decode
            else tuple(
                index
                for index in (center - 1, center, center + 1)
                if 0 <= index < pages
            )
        )
        _wait_for_warm_window(
            application,
            window,
            required_page_indexes=required_ready,
        )

        forward_handlers: list[float] = []
        forward_paints: list[float] = []
        for target in range(center + 1, center + 6):
            handler, paint = _move_and_measure(
                application,
                window,
                window.next_page,
                target,
            )
            forward_handlers.append(handler)
            forward_paints.append(paint)

        reverse_handlers: list[float] = []
        reverse_paints: list[float] = []
        for target in range(center + 4, center - 1, -1):
            handler, paint = _move_and_measure(
                application,
                window,
                window.previous_page,
                target,
            )
            reverse_handlers.append(handler)
            reverse_paints.append(paint)

        roundtrip_handlers: list[float] = []
        roundtrip_paints: list[float] = []
        for move, target in (
            (window.next_page, center + 1),
            (window.previous_page, center),
        ) * 4:
            handler, paint = _move_and_measure(
                application,
                window,
                move,
                target,
            )
            roundtrip_handlers.append(handler)
            roundtrip_paints.append(paint)

        window._go_to_index_with_history(center)
        _wait_for_page(application, window, center)
        application.processEvents()
        rapid_target = min(pages - 1, center + 8)
        rapid_started = time.perf_counter()
        rapid_handlers: list[float] = []
        for _target in range(center + 1, rapid_target + 1):
            handler_started = time.perf_counter()
            window.next_page()
            rapid_handlers.append(
                (time.perf_counter() - handler_started) * 1000
            )
        rapid_handler_finished = time.perf_counter()
        _wait_for_page(application, window, rapid_target)
        rapid_finished = time.perf_counter()

        return {
            "ready_forward": {
                "handler": _summary(forward_handlers),
                "request_to_paint": _summary(forward_paints),
            },
            "ready_reverse": {
                "handler": _summary(reverse_handlers),
                "request_to_paint": _summary(reverse_paints),
            },
            "ready_roundtrip": {
                "handler": _summary(roundtrip_handlers),
                "request_to_paint": _summary(roundtrip_paints),
            },
            "rapid_frontier": {
                "requests": len(rapid_handlers),
                "handler": _summary(rapid_handlers),
                "handlers_total_ms": round(
                    (rapid_handler_finished - rapid_started) * 1000,
                    3,
                ),
                "request_to_final_paint_ms": round(
                    (rapid_finished - rapid_started) * 1000,
                    3,
                ),
                "final_page": rapid_target,
            },
            "cache": {
                "decoded_entries": len(window.image_cache._cache),
                "decoded_mib": round(
                    window.image_cache._cache_bytes / (1024 * 1024),
                    2,
                ),
                "prepared_units": len(window.viewer._prepared_units),
                "prepared_pixmaps": len(window.viewer._render_cache),
                "preview_sources": sum(
                    cached.source_is_preview
                    for cached in window.image_cache._cache.values()
                ),
            },
        }


def run_benchmark(
    *,
    pages: int = 21,
    image_size: tuple[int, int] = (4096, 6500),
    viewport_size: tuple[int, int] = (3840, 2106),
    use_target_decode: bool = True,
) -> dict[str, object]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    with TemporaryDirectory(prefix="nivisviewer-navigation-") as temporary:
        root = Path(temporary)
        archive_path = root / "日本語 大画像.zip"
        _make_zip(archive_path, pages=pages, size=image_size)
        result: dict[str, object] = {
            "source": {
                "pages": pages,
                "image_size": list(image_size),
                "zip_bytes": archive_path.stat().st_size,
                "viewport_size": list(viewport_size),
                "target_decode": use_target_decode,
            },
        }
        # Every Viewer is owned by ``_viewer_case``. Its finally block closes
        # the ZIP and coordinator before an exception can unwind into
        # TemporaryDirectory cleanup on Windows.
        result.update(
            _run_ready_sequences(
                application,
                root,
                archive_path,
                pages=pages,
                viewport_size=viewport_size,
                use_target_decode=use_target_decode,
            )
        )
        cold_miss, memory_pressure = _run_cold_matrix(
            application,
            root,
            archive_path,
            pages=pages,
            viewport_size=viewport_size,
            use_target_decode=use_target_decode,
        )
        result["cold_miss"] = cold_miss
        result["memory_pressure"] = memory_pressure
        return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offscreen large-ZIP Viewer navigation benchmark."
    )
    parser.add_argument("--pages", type=int, default=21)
    parser.add_argument("--width", type=int, default=4096)
    parser.add_argument("--height", type=int, default=6500)
    parser.add_argument(
        "--full-decode",
        action="store_true",
        help="Disable the display-bound JPEG decode fast path.",
    )
    arguments = parser.parse_args()
    result = run_benchmark(
        pages=max(13, arguments.pages),
        image_size=(
            max(64, arguments.width),
            max(64, arguments.height),
        ),
        use_target_decode=not arguments.full_decode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
