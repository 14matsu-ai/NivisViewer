from __future__ import annotations

import argparse
import atexit
from contextlib import contextmanager
import ctypes
from dataclasses import asdict, replace
import hashlib
import io
import json
import os
import random
import statistics
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator, Literal
import zipfile

from PIL import Image, ImageOps
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from app.config_manager import ConfigManager
from app.image_work_coordinator import ImageWorkCoordinator
from app.viewer_memory_policy import viewer_memory_mode_from_legacy_mib
from app.viewer_render import ViewerRenderTask
from app.viewer_window import ViewerWindow
from app.zippla_compatible_raster_path import ZipPlaCompatibleRasterPage


RasterPathMode = Literal["legacy", "compatible"]
ImagePattern = Literal["solid", "detail"]


def _jpeg_bytes(
    size: tuple[int, int],
    *,
    page_index: int = 0,
    image_pattern: ImagePattern = "solid",
) -> bytes:
    output = io.BytesIO()
    if image_pattern == "detail":
        # Deterministic full-frame luminance noise produces a large JPEG with
        # scan-like local detail.  Colorizing keeps the decoder output RGB
        # while avoiding three independent full-size random buffers.
        random_bytes = random.Random(
            0x4E49564953 + int(page_index),
        ).randbytes(max(1, int(size[0])) * max(1, int(size[1])))
        with Image.frombytes("L", size, random_bytes) as luminance:
            with ImageOps.colorize(
                luminance,
                black=(12, 24, 44),
                white=(246, 232, 204),
            ) as image:
                image.save(output, "JPEG", quality=92, subsampling=0)
        return output.getvalue()
    if image_pattern != "solid":
        raise ValueError(f"invalid image_pattern: {image_pattern!r}")
    color = (
        (73 + page_index * 29) % 256,
        (106 + page_index * 47) % 256,
        (140 + page_index * 61) % 256,
    )
    with Image.new("RGB", size, color) as image:
        image.save(output, "JPEG", quality=92, subsampling=0)
    return output.getvalue()


def _make_zip(
    path: Path,
    *,
    pages: int,
    size: tuple[int, int],
    image_pattern: ImagePattern = "solid",
) -> None:
    # Adjacent pages still have distinct colors for frame-retention checks,
    # while limiting full-size Pillow/JPEG generation keeps the benchmark
    # setup comfortably below the navigation work being measured.
    variant_count = (
        1
        if image_pattern == "detail"
        else max(1, min(pages, 8))
    )
    payloads = tuple(
        _jpeg_bytes(
            size,
            page_index=index,
            image_pattern=image_pattern,
        )
        for index in range(variant_count)
    )
    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as archive:
        for index in range(pages):
            info = zipfile.ZipInfo(
                f"日本語ページ/{index:03d}.jpg",
                date_time=(2024, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(
                info,
                payloads[index % variant_count],
            )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
        if window._compatible_raster_active:
            prepared = set(
                window._compatible_raster_path.artifact_page_indexes
            )
            settled = (
                not window._compatible_raster_path.has_unfinished_tasks()
                and window._compatible_raster_path.active_job_count == 0
                and window._compatible_raster_path.queued_job_count == 0
                and window._pending_compatible_request is None
                and not window._compatible_request_timer.isActive()
                and required.issubset(prepared)
            )
        else:
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
    render_first: bool = True,
) -> None:
    # One explicit paint releases the production after-paint stage. Do not
    # repaint on every predicate check: each paint re-arms the 16 ms prepared
    # timer and would itself prevent a stable idle observation.
    if render_first:
        _render_once(window)
        application.processEvents()
    stable_since: float | None = None

    def idle() -> bool:
        nonlocal stable_since
        if window._compatible_raster_active:
            settled = (
                not window._compatible_raster_path.has_unfinished_tasks()
                and window._compatible_raster_path.active_job_count == 0
                and window._compatible_raster_path.queued_job_count == 0
                and window._pending_compatible_request is None
                and not window._compatible_request_timer.isActive()
                and not window._raster_viewport_timer.isActive()
            )
        else:
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
    path_mode: RasterPathMode = "legacy",
    forward_units: int = 0,
    backward_units: int = 0,
    cache_mib: int = 512,
) -> Iterator[ViewerWindow]:
    if path_mode not in {"legacy", "compatible"}:
        raise ValueError(f"unsupported raster path mode: {path_mode}")
    if path_mode == "compatible" and not use_target_decode:
        raise ValueError(
            "compatible mode requires the display-bound JPEG decoder"
        )
    config = ConfigManager(root / f"config-{case_name}-{path_mode}.json")
    config.load()
    config.apply(
        {
            "view_mode": "single",
            "single_first_page": False,
            "viewer_downscale_algorithm": "auto",
            "viewer_upscale_algorithm": "auto",
            "fit_mode": "fit_window",
            "viewer_prefetch_preset": "custom",
            "viewer_prefetch_direction_priority_enabled": True,
            "viewer_prefetch_image_forward_units": int(forward_units),
            "viewer_prefetch_image_backward_units": int(backward_units),
            "viewer_memory_mode": viewer_memory_mode_from_legacy_mib(cache_mib),
            "show_page_list": False,
        },
        save=True,
    )
    coordinator = ImageWorkCoordinator(max_workers=1)
    window = ViewerWindow(
        config_manager=config,
        image_work_coordinator=coordinator,
    )
    if path_mode == "legacy":
        # Benchmark A must retain the same ZIP, JPEG target size, DPR, and
        # settings as B.  Disable only compatible-path selection; do not
        # disable the legacy decoder-sized source path.
        window._compatible_raster_request = (  # type: ignore[method-assign]
            lambda _spread: None
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
        if path_mode == "compatible" and not window._compatible_raster_active:
            raise AssertionError(
                f"{case_name}: compatible path was not selected"
            )
        if path_mode == "legacy" and window._compatible_raster_active:
            raise AssertionError(
                f"{case_name}: legacy path unexpectedly selected compatible"
            )
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
    """Benchmark-only probe for both raster implementations.

    It intentionally monkeypatches only the owned, temporary Viewer instance
    and restores every patch on exit.  Counts describe application-visible
    calls.  Qt plugin/internal copies are not observable from Python and are
    reported separately as an explicit limitation.
    """

    _COUNT_SCHEMA = (
        "zip_entry_open_calls",
        "zip_entry_bytes_read",
        "zip_entry_read_calls",
        "zip_entry_read_calls_estimated",
        "python_bytesio_payloads",
        "python_bytesio_payload_bytes",
        "decoder_calls",
        "qimage_decoder_outputs",
        "qimage_decoder_output_bytes",
        "whole_payload_handoff_calls",
        "whole_payload_handoff_bytes",
        "full_payload_materialization_calls",
        "full_payload_materialization_bytes",
        "render_task_runs",
        "load_gui_callbacks",
        "render_gui_callbacks",
        "compatible_frame_ready_callbacks",
        "worker_task_submissions",
        "queue_try_take_calls",
        "display_commits",
        "content_paints",
        "compatible_frame_paints",
        "legacy_qpixmap_from_image",
        "worker_to_gui_queued_callbacks",
        "qpixmap_from_image",
    )

    def __init__(self, window: ViewerWindow) -> None:
        self.window = window
        self.source = window.image_cache.source
        if self.source is None:
            raise AssertionError("worker timeline requires an image source")
        self.events: list[tuple[str, str, float, int]] = []
        self.counts: dict[str, int] = {}
        self._metric_events: list[tuple[str, int, float, int, str]] = []
        self._compatible_metric_events: list[
            tuple[str, int, float, int]
        ] = []
        self._lock = threading.Lock()
        self._last_payload_size: dict[str, int] = {}
        self._target_decoder = getattr(
            self.source,
            "open_qimage_at_most",
            None,
        )
        self._compatible_decoder = getattr(
            self.source,
            "open_compatible_jpeg_at_most",
            None,
        )
        self._streamed_decoder = getattr(
            self.source,
            "open_streamed_jpeg_at_most",
            None,
        )
        self._full_decoder = getattr(self.source, "open_image")
        self._read_entry_stream = getattr(
            self.source,
            "_read_entry_stream",
            None,
        )
        self._render_run = ViewerRenderTask.run
        self._cache_loaded = window.image_cache._on_loaded
        self._render_completed = window.viewer._on_render_completed
        self._coordinator_start = window.image_work_coordinator.start_viewer
        self._coordinator_try_take = (
            window.image_work_coordinator.try_take_viewer
        )
        self._compatible_path_bump = (
            window._compatible_raster_path._bump
        )
        self._path_metrics_before = asdict(
            window._compatible_raster_path.metrics
        )
        self._frame_callback_values: list[dict[str, float | int | bool]] = []
        self._working_set_samples: list[float] = []
        self.sample_memory()

    def _bump(
        self,
        name: str,
        amount: int = 1,
        *,
        image_id: str = "",
    ) -> None:
        timestamp = time.perf_counter()
        thread_id = threading.get_ident()
        with self._lock:
            self.counts[name] = self.counts.get(name, 0) + int(amount)
            self._metric_events.append(
                (
                    str(name),
                    int(amount),
                    timestamp,
                    thread_id,
                    str(image_id),
                )
            )

    def _record_compatible_metric(
        self,
        name: str,
        amount: int,
    ) -> None:
        timestamp = time.perf_counter()
        thread_id = threading.get_ident()
        with self._lock:
            self._compatible_metric_events.append(
                (str(name), int(amount), timestamp, thread_id)
            )

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

    def sample_memory(self) -> None:
        value = _process_memory_mib().get("working_set_mib")
        if value is None:
            return
        with self._lock:
            self._working_set_samples.append(float(value))

    def _record_qimage_output(
        self,
        result: object,
        *,
        image_id: str,
    ) -> None:
        image = getattr(result, "qimage", None)
        if image is None and isinstance(result, tuple) and result:
            image = result[0]
        if image is None or image.isNull():
            return
        self._bump(
            "qimage_decoder_outputs",
            image_id=image_id,
        )
        self._bump(
            "qimage_decoder_output_bytes",
            int(image.sizeInBytes()),
            image_id=image_id,
        )

    def __enter__(self) -> "_WorkerTimeline":
        def read_entry_stream(image_id, cancelled):
            assert self._read_entry_stream is not None
            result = self._read_entry_stream(image_id, cancelled)
            size = int(result.getbuffer().nbytes)
            with self._lock:
                self._last_payload_size[str(image_id)] = size
            self._bump(
                "zip_entry_open_calls",
                image_id=image_id,
            )
            self._bump(
                "zip_entry_bytes_read",
                size,
                image_id=image_id,
            )
            chunk_size = max(
                1,
                int(getattr(self.source, "_READ_CHUNK_BYTES", 1024 * 1024)),
            )
            self._bump(
                "zip_entry_read_calls_estimated",
                (size + chunk_size - 1) // chunk_size + 1,
                image_id=image_id,
            )
            self._bump(
                "python_bytesio_payloads",
                image_id=image_id,
            )
            self._bump(
                "python_bytesio_payload_bytes",
                size,
                image_id=image_id,
            )
            return result

        def target_decode(image_id, maximum_size):
            self._record("decode_started", image_id)
            self._record("buffered_decode_started", image_id)
            self._bump("decoder_calls", image_id=image_id)
            try:
                assert self._target_decoder is not None
                result = self._target_decoder(image_id, maximum_size)
                self._record_qimage_output(result, image_id=image_id)
                size = self._last_payload_size.get(str(image_id), 0)
                if size:
                    # getvalue -> Pillow BytesIO -> QByteArray are three
                    # whole-payload API handoffs. CPython/Qt may share or copy
                    # internally, so this is a semantic byte volume rather
                    # than a claim about physical memcpy volume.
                    self._bump(
                        "whole_payload_handoff_calls",
                        3,
                        image_id=image_id,
                    )
                    self._bump(
                        "whole_payload_handoff_bytes",
                        size * 3,
                        image_id=image_id,
                    )
                return result
            finally:
                self._record("buffered_decode_finished", image_id)
                self._record("decode_finished", image_id)

        def streamed_decode(image_id, maximum_size):
            self._record("decode_started", image_id)
            self._record("streamed_decode_started", image_id)
            self._bump("decoder_calls", image_id=image_id)
            try:
                assert self._streamed_decoder is not None
                result = self._streamed_decoder(image_id, maximum_size)
                self._bump(
                    "zip_entry_open_calls",
                    image_id=image_id,
                )
                self._record_qimage_output(result, image_id=image_id)
                if result is not None:
                    self._bump(
                        "zip_entry_bytes_read",
                        int(getattr(result, "bytes_read", 0)),
                        image_id=image_id,
                    )
                    self._bump(
                        "zip_entry_read_calls",
                        int(getattr(result, "read_calls", 0)),
                        image_id=image_id,
                    )
                return result
            finally:
                self._record("streamed_decode_finished", image_id)
                self._record("decode_finished", image_id)

        def compatible_decode(image_id, maximum_size):
            self._record("decode_started", image_id)
            self._record("compatible_decode_started", image_id)
            self._record(
                "compatible_decode_bound:"
                f"{int(maximum_size[0])}x{int(maximum_size[1])}",
                image_id,
            )
            self._bump("decoder_calls", image_id=image_id)
            try:
                assert self._compatible_decoder is not None
                result = self._compatible_decoder(image_id, maximum_size)
                self._bump(
                    "zip_entry_open_calls",
                    image_id=image_id,
                )
                self._record_qimage_output(result, image_id=image_id)
                if result is not None:
                    backend = str(
                        getattr(result, "backend", "unknown")
                    )
                    backend_metric = "".join(
                        character
                        if character.isalnum()
                        else "_"
                        for character in backend.casefold()
                    ).strip("_")
                    self._record(
                        f"decode_backend:{backend}",
                        image_id,
                    )
                    self._bump(
                        f"decoder_backend_{backend_metric or 'unknown'}",
                        image_id=image_id,
                    )
                    bytes_read = int(getattr(result, "bytes_read", 0))
                    read_calls = int(getattr(result, "read_calls", 0))
                    materializations = int(
                        getattr(
                            result,
                            "full_payload_materializations",
                            0,
                        )
                    )
                    self._bump(
                        "zip_entry_bytes_read",
                        bytes_read,
                        image_id=image_id,
                    )
                    self._bump(
                        "zip_entry_read_calls",
                        read_calls,
                        image_id=image_id,
                    )
                    self._bump(
                        "full_payload_materialization_calls",
                        materializations,
                        image_id=image_id,
                    )
                    self._bump(
                        "full_payload_materialization_bytes",
                        bytes_read * materializations,
                        image_id=image_id,
                    )
                return result
            finally:
                self._record("compatible_decode_finished", image_id)
                self._record("decode_finished", image_id)

        def full_decode(image_id):
            self._record("decode_started", image_id)
            self._record("full_decode_started", image_id)
            self._bump("decoder_calls", image_id=image_id)
            try:
                return self._full_decoder(image_id)
            finally:
                self._record("full_decode_finished", image_id)
                self._record("decode_finished", image_id)

        timeline = self

        def render_run(task: ViewerRenderTask) -> None:
            timeline._record("render_started", task.key.image_id)
            timeline._bump(
                "render_task_runs",
                image_id=task.key.image_id,
            )
            try:
                timeline._render_run(task)
            finally:
                timeline._record("render_finished", task.key.image_id)

        def cache_loaded(result) -> None:
            cached = getattr(result, "cached", None)
            image_id = getattr(cached, "image_id", "")
            timeline._record("load_gui_callback_started", image_id)
            timeline._bump(
                "load_gui_callbacks",
                image_id=image_id,
            )
            try:
                timeline._cache_loaded(result)
            finally:
                timeline._record("load_gui_callback_finished", image_id)

        def render_completed(result, task) -> None:
            image_id = getattr(getattr(result, "key", None), "image_id", "")
            timeline._record("render_gui_callback_started", image_id)
            timeline._bump(
                "render_gui_callbacks",
                image_id=image_id,
            )
            try:
                timeline._render_completed(result, task)
            finally:
                timeline._record("render_gui_callback_finished", image_id)

        def coordinator_start(runnable, priority):
            name = runnable.__class__.__name__
            image_id = str(
                getattr(getattr(runnable, "key", None), "image_id", "")
            )
            timeline._bump(
                "worker_task_submissions",
                image_id=image_id,
            )
            timeline._bump(
                f"worker_task_{name}",
                image_id=image_id,
            )
            return timeline._coordinator_start(runnable, priority)

        def coordinator_try_take(runnable):
            image_id = str(
                getattr(getattr(runnable, "key", None), "image_id", "")
            )
            timeline._bump(
                "queue_try_take_calls",
                image_id=image_id,
            )
            return timeline._coordinator_try_take(runnable)

        def display_committed(image_ids: object) -> None:
            values = tuple(image_ids) if isinstance(image_ids, tuple) else ()
            joined = "|".join(map(str, values))
            timeline._bump("display_commits", image_id=joined)
            timeline._record("display_committed", joined)

        def content_painted(image_ids: object) -> None:
            values = tuple(image_ids) if isinstance(image_ids, tuple) else ()
            joined = "|".join(map(str, values))
            timeline._bump("content_paints", image_id=joined)
            timeline._record("content_painted", joined)

        def frame_painted(frame_serial: int, image_ids: object) -> None:
            values = tuple(image_ids) if isinstance(image_ids, tuple) else ()
            joined = "|".join(map(str, values))
            timeline._bump(
                "compatible_frame_paints",
                image_id=joined,
            )
            timeline._record("frame_painted", joined)

        def render_finished(key: object, succeeded: bool) -> None:
            if succeeded:
                timeline._bump(
                    "legacy_qpixmap_from_image",
                    image_id=str(getattr(key, "image_id", "")),
                )

        def compatible_frame_ready(frame: object) -> None:
            frame_image_id = str(getattr(frame, "image_id", ""))
            timeline._bump(
                "compatible_frame_ready_callbacks",
                image_id=frame_image_id,
            )
            timeline._record(
                "compatible_frame_ready",
                frame_image_id,
            )
            timeline._frame_callback_values.append(
                {
                    "cache_hit": bool(getattr(frame, "cache_hit", False)),
                    "gui_callback_ms": round(
                        float(getattr(frame, "gui_callback_ms", 0.0)),
                        3,
                    ),
                    "worker_to_gui_ready_ms": round(
                        max(
                            0.0,
                            (
                                float(getattr(frame, "gui_ready_at", 0.0))
                                - float(
                                    getattr(
                                        frame,
                                        "worker_completed_at",
                                        0.0,
                                    )
                                )
                            )
                            * 1000,
                        ),
                        3,
                    ),
                }
            )

        def compatible_path_bump(name: str, amount: int = 1) -> None:
            timeline._compatible_path_bump(name, amount)
            timeline._record_compatible_metric(name, amount)

        if callable(self._read_entry_stream):
            self.source._read_entry_stream = read_entry_stream
        if callable(self._target_decoder):
            self.source.open_qimage_at_most = target_decode
        if callable(self._compatible_decoder):
            self.source.open_compatible_jpeg_at_most = compatible_decode
        if callable(self._streamed_decoder):
            self.source.open_streamed_jpeg_at_most = streamed_decode
        self.source.open_image = full_decode
        ViewerRenderTask.run = render_run
        self.window.image_cache._on_loaded = cache_loaded
        self.window.viewer._on_render_completed = render_completed
        self.window.image_work_coordinator.start_viewer = coordinator_start
        self.window.image_work_coordinator.try_take_viewer = (
            coordinator_try_take
        )
        self.window._compatible_raster_path._bump = compatible_path_bump
        self.window.viewer.displayCommitted.connect(display_committed)
        self.window.viewer.contentPainted.connect(content_painted)
        self.window.viewer.framePainted.connect(frame_painted)
        self.window.viewer.renderWorkFinished.connect(render_finished)
        self.window._compatible_raster_path.frameReady.connect(
            compatible_frame_ready
        )
        self._signal_callbacks = (
            display_committed,
            content_painted,
            frame_painted,
            render_finished,
            compatible_frame_ready,
        )
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        for name in (
            "_read_entry_stream",
            "open_qimage_at_most",
            "open_compatible_jpeg_at_most",
            "open_streamed_jpeg_at_most",
            "open_image",
        ):
            self.source.__dict__.pop(name, None)
        self.window.image_cache.__dict__.pop("_on_loaded", None)
        self.window.viewer.__dict__.pop("_on_render_completed", None)
        self.window.image_work_coordinator.__dict__.pop(
            "start_viewer",
            None,
        )
        self.window.image_work_coordinator.__dict__.pop(
            "try_take_viewer",
            None,
        )
        self.window._compatible_raster_path.__dict__.pop("_bump", None)
        ViewerRenderTask.run = self._render_run
        (
            display_committed,
            content_painted,
            frame_painted,
            render_finished,
            compatible_frame_ready,
        ) = self._signal_callbacks
        self.window.viewer.displayCommitted.disconnect(display_committed)
        self.window.viewer.contentPainted.disconnect(content_painted)
        self.window.viewer.framePainted.disconnect(frame_painted)
        self.window.viewer.renderWorkFinished.disconnect(render_finished)
        self.window._compatible_raster_path.frameReady.disconnect(
            compatible_frame_ready
        )

    def clear(self) -> None:
        with self._lock:
            self.events.clear()
            self.counts.clear()
            self._metric_events.clear()
            self._compatible_metric_events.clear()
            self._last_payload_size.clear()
        self._path_metrics_before = asdict(
            self.window._compatible_raster_path.metrics
        )
        self._frame_callback_values.clear()

    def exclude_compatible_setup_cache_hit(self) -> None:
        """Remove the synchronous current re-publish used to arm a test."""
        with self._lock:
            if self.counts.get("display_commits", 0) > 0:
                self.counts["display_commits"] -= 1
            if self.counts.get("compatible_frame_ready_callbacks", 0) > 0:
                self.counts["compatible_frame_ready_callbacks"] -= 1
            for index in range(len(self._metric_events) - 1, -1, -1):
                if self._metric_events[index][0] == "display_commits":
                    self._metric_events.pop(index)
                    break
            for index in range(len(self._metric_events) - 1, -1, -1):
                if (
                    self._metric_events[index][0]
                    == "compatible_frame_ready_callbacks"
                ):
                    self._metric_events.pop(index)
                    break
            for index in range(len(self.events) - 1, -1, -1):
                if self.events[index][0] == "display_committed":
                    self.events.pop(index)
                    break
            for index in range(len(self.events) - 1, -1, -1):
                if self.events[index][0] == "compatible_frame_ready":
                    self.events.pop(index)
                    break
            for index in range(
                len(self._compatible_metric_events) - 1,
                -1,
                -1,
            ):
                if (
                    self._compatible_metric_events[index][0]
                    == "cache_hits"
                ):
                    self._compatible_metric_events.pop(index)
                    break
        self._path_metrics_before["cache_hits"] = (
            int(self._path_metrics_before.get("cache_hits", 0)) + 1
        )
        if (
            self._frame_callback_values
            and self._frame_callback_values[-1].get("cache_hit") is True
        ):
            self._frame_callback_values.pop()

    def stages(self, stage: str) -> list[str]:
        with self._lock:
            return [
                image_id
                for event_stage, image_id, _timestamp, _thread_id
                in self.events
                if event_stage == stage
            ]

    def mark_current_request(self, image_id: str) -> None:
        self._record("current_request_started", image_id)

    def timed_stages(self, stage: str) -> list[tuple[str, float]]:
        with self._lock:
            return [
                (image_id, timestamp)
                for event_stage, image_id, timestamp, _thread_id
                in self.events
                if event_stage == stage
            ]

    def latest_event_timestamp(
        self,
        stage: str,
        image_ids: tuple[str, ...],
    ) -> float | None:
        wanted = set(image_ids)
        matches = [
            timestamp
            for value, timestamp in self.timed_stages(stage)
            if wanted.intersection(value.split("|"))
        ]
        return max(matches, default=None)

    def _normalized_counts(
        self,
        raw_counts: dict[str, int],
        path_counts: dict[str, int],
    ) -> dict[str, int]:
        counts = dict(raw_counts)
        for name in self._COUNT_SCHEMA:
            counts.setdefault(name, 0)
        if self.window._compatible_raster_active:
            counts["worker_to_gui_queued_callbacks"] = path_counts.get(
                "queued_callbacks",
                0,
            )
            counts["qpixmap_from_image"] = path_counts.get(
                "qpixmap_creations",
                0,
            )
        else:
            counts["worker_to_gui_queued_callbacks"] = (
                counts.get("load_gui_callbacks", 0)
                + counts.get("render_gui_callbacks", 0)
            )
            counts["qpixmap_from_image"] = counts.get(
                "legacy_qpixmap_from_image",
                0,
            )
        return dict(sorted(counts.items()))

    @staticmethod
    def _summed_metric_events(
        events: list[tuple[str, int, float, int, str]],
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for name, amount, _timestamp, _thread_id, _image_id in events:
            counts[name] = counts.get(name, 0) + int(amount)
        return counts

    def _summed_compatible_metric_events(
        self,
        events: list[tuple[str, int, float, int]],
    ) -> dict[str, int]:
        counts = {
            name: 0
            for name in self._path_metrics_before
        }
        for name, amount, _timestamp, _thread_id in events:
            counts[name] = counts.get(name, 0) + int(amount)
        return dict(sorted(counts.items()))

    def _phase_metrics(
        self,
        *,
        events: list[tuple[str, str, float, int]],
        metric_events: list[tuple[str, int, float, int, str]],
        compatible_metric_events: list[tuple[str, int, float, int]],
        current_image_ids: tuple[str, ...],
    ) -> dict[str, object] | None:
        if not events or not current_image_ids:
            return None
        current_ids = set(current_image_ids)
        display_commit_at = self.latest_event_timestamp(
            "display_committed",
            current_image_ids,
        )
        content_paint_at = self.latest_event_timestamp(
            "content_painted",
            current_image_ids,
        )
        split_at = (
            content_paint_at
            if content_paint_at is not None
            else display_commit_at
        )
        if split_at is None:
            return None

        request_intervals: list[tuple[float, float]] = []
        request_events = [
            (image_id, timestamp)
            for stage, image_id, timestamp, _thread_id in events
            if stage == "current_request_started"
            and image_id in current_ids
        ]
        paint_events = [
            (set(image_id.split("|")), timestamp)
            for stage, image_id, timestamp, _thread_id in events
            if stage == "content_painted"
        ]
        for image_id, requested_at in request_events:
            painted_at = next(
                (
                    timestamp
                    for painted_ids, timestamp in paint_events
                    if timestamp >= requested_at
                    and image_id in painted_ids
                ),
                None,
            )
            if painted_at is not None:
                request_intervals.append((requested_at, painted_at))

        def is_current_metric(
            event: tuple[str, int, float, int, str],
        ) -> bool:
            name, _amount, timestamp, _thread_id, image_id = event
            if request_intervals:
                if any(
                    requested_at <= timestamp <= painted_at
                    for requested_at, painted_at in request_intervals
                ):
                    return True
                return (
                    name == "compatible_frame_paints"
                    and bool(
                        current_ids.intersection(image_id.split("|"))
                    )
                )
            return (
                timestamp <= split_at
                or bool(current_ids.intersection(image_id.split("|")))
            )

        current_metric_events = [
            event for event in metric_events if is_current_metric(event)
        ]
        post_metric_events = [
            event for event in metric_events if not is_current_metric(event)
        ]
        def path_event_is_current(
            event: tuple[str, int, float, int],
        ) -> bool:
            return (
                any(
                    requested_at <= event[2] <= painted_at
                    for requested_at, painted_at in request_intervals
                )
                if request_intervals
                else event[2] <= split_at
            )

        current_path_events = [
            event
            for event in compatible_metric_events
            if path_event_is_current(event)
        ]
        post_path_events = [
            event
            for event in compatible_metric_events
            if not path_event_is_current(event)
        ]
        current_path_counts = self._summed_compatible_metric_events(
            current_path_events
        )
        post_path_counts = self._summed_compatible_metric_events(
            post_path_events
        )
        current_raw_counts = self._summed_metric_events(
            current_metric_events
        )
        post_raw_counts = self._summed_metric_events(post_metric_events)
        for name in set(current_raw_counts).union(post_raw_counts):
            current_raw_counts.setdefault(name, 0)
            post_raw_counts.setdefault(name, 0)
        origin = events[0][2]

        def relative_ms(timestamp: float | None) -> float | None:
            if timestamp is None:
                return None
            return round((timestamp - origin) * 1000, 3)

        return {
            "current_image_ids": list(current_image_ids),
            "display_commit_at_ms": relative_ms(display_commit_at),
            "current_paint_at_ms": relative_ms(content_paint_at),
            "split_after_stage": (
                "content_painted"
                if content_paint_at is not None
                else "display_committed"
            ),
            "current_intervals": [
                {
                    "request_at_ms": relative_ms(requested_at),
                    "paint_at_ms": relative_ms(painted_at),
                }
                for requested_at, painted_at in request_intervals
            ],
            "current_critical": {
                "counts": self._normalized_counts(
                    current_raw_counts,
                    current_path_counts,
                ),
                "compatible_path_delta": current_path_counts,
            },
            "post_commit_prefetch": {
                "counts": self._normalized_counts(
                    post_raw_counts,
                    post_path_counts,
                ),
                "compatible_path_delta": post_path_counts,
            },
            "scope_note": (
                "Current-critical uses each recorded request-to-content-paint "
                "interval when request markers are present; otherwise it "
                "includes measured work through the final current paint. "
                "Post-commit-prefetch contains work outside those intervals."
            ),
        }

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

    def metrics(
        self,
        *,
        current_image_ids: tuple[str, ...] = (),
    ) -> dict[str, object]:
        self.sample_memory()
        with self._lock:
            raw_counts = dict(self.counts)
            events = list(self.events)
            metric_events = list(self._metric_events)
            compatible_metric_events = list(
                self._compatible_metric_events
            )
            working_set_samples = list(self._working_set_samples)
        current_path_metrics = asdict(
            self.window._compatible_raster_path.metrics
        )
        path_delta = {
            key: int(current_path_metrics[key])
            - int(self._path_metrics_before.get(key, 0))
            for key in current_path_metrics
        }
        counts = self._normalized_counts(raw_counts, path_delta)

        decode_finished: dict[str, float] = {}
        commit_times: dict[str, float] = {}
        decode_to_paint: list[float] = []
        commit_to_paint: list[float] = []
        for stage, image_id, timestamp, _thread_id in events:
            if stage == "decode_finished":
                decode_finished[image_id] = timestamp
            elif stage == "display_committed":
                for value in image_id.split("|"):
                    if value:
                        commit_times[value] = timestamp
            elif stage == "content_painted":
                for value in image_id.split("|"):
                    if value in decode_finished:
                        decode_to_paint.append(
                            (
                                timestamp
                                - decode_finished.pop(value)
                            )
                            * 1000
                        )
                    if value in commit_times:
                        commit_to_paint.append(
                            (
                                timestamp
                                - commit_times.pop(value)
                            )
                            * 1000
                        )

        result = {
            "counts": counts,
            "compatible_path_delta": path_delta,
            "compatible_frame_callbacks": list(
                self._frame_callback_values
            ),
            "decode_to_paint_ms": [
                round(value, 3) for value in decode_to_paint
            ],
            "commit_to_paint_ms": [
                round(value, 3) for value in commit_to_paint
            ],
            "working_set": {
                "baseline_mib": (
                    working_set_samples[0]
                    if working_set_samples
                    else None
                ),
                "sampled_max_mib": (
                    max(working_set_samples)
                    if working_set_samples
                    else None
                ),
                "sampled_delta_mib": (
                    None
                    if not working_set_samples
                    else round(
                        max(working_set_samples)
                        - working_set_samples[0],
                        2,
                    )
                ),
                "process_snapshot": _process_memory_mib(),
            },
            "copy_accounting_note": (
                "Whole-payload handoff bytes count application-visible API "
                "boundaries; Qt/Python implicit sharing and native plugin "
                "copies are not observable here."
            ),
        }
        phase_metrics = self._phase_metrics(
            events=events,
            metric_events=metric_events,
            compatible_metric_events=compatible_metric_events,
            current_image_ids=current_image_ids,
        )
        if phase_metrics is not None:
            result["phases"] = phase_metrics
        return result


def _matches_image_id(candidate: str, image_id: str) -> bool:
    return candidate == image_id or candidate.startswith(f"{image_id}#")


def _assert_page_cold(window: ViewerWindow, page_index: int) -> None:
    image_id = window.model.image_id_at(page_index)
    if image_id is None:
        raise AssertionError(f"no image identity for page {page_index}")
    compatible_path = window._compatible_raster_path
    if page_index in compatible_path.artifact_page_indexes:
        raise AssertionError(
            f"page {page_index} is in compatible artifact cache"
        )
    active_job = compatible_path._active_job
    if (
        active_job is not None
        and not active_job.finished.is_set()
        and active_job.key.page_index == page_index
    ):
        raise AssertionError(
            f"page {page_index} has a compatible decode job"
        )
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


def _frame_state_without_paint(window: ViewerWindow) -> tuple[
    tuple[int, ...],
    tuple[str, ...],
    tuple[int, ...],
]:
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
    previous_frame = _frame_state_without_paint(window)
    started = time.perf_counter()
    move()
    handler_finished = time.perf_counter()
    immediate_frame = _frame_state_without_paint(window)
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
    _wait_for_idle(application, window, render_first=False)
    previous_frame = _frame_state_without_paint(window)
    if window._compatible_raster_active:
        compatible_path = window._compatible_raster_path
        for key in tuple(compatible_path._artifacts):
            if key.page_index == page_index:
                compatible_path._artifacts.pop(key, None)
        for key in tuple(compatible_path._failed_prefetch):
            if key.page_index == page_index:
                compatible_path._failed_prefetch.discard(key)
        if _frame_state_without_paint(window) != previous_frame:
            raise AssertionError(
                "forcing a compatible non-current cold miss changed the frame"
            )
        _assert_page_cold(window, page_index)
        return
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
    if _frame_state_without_paint(window) != previous_frame:
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
    if window._compatible_raster_active:
        path = window._compatible_raster_path
        artifacts = tuple(path._artifacts.values())
        unique_pixmaps: dict[int, int] = {}
        for artifact in artifacts:
            pixmap = artifact.pixmap
            unique_pixmaps.setdefault(
                int(pixmap.cacheKey()),
                max(0, pixmap.width()) * max(0, pixmap.height()) * 4,
            )
        for image in window.viewer._images:
            pixmap = image.pixmap
            if pixmap is None or pixmap.isNull():
                continue
            unique_pixmaps.setdefault(
                int(pixmap.cacheKey()),
                max(0, pixmap.width()) * max(0, pixmap.height()) * 4,
            )
        retained_pages = sorted(
            {
                artifact.page_index
                for artifact in artifacts
            }
            | set(window.viewer.displayed_page_indexes)
        )
        unique_bytes = sum(unique_pixmaps.values())
        return {
            "path_mode": "compatible",
            "decoded_pages": [],
            "prepared_pages": [],
            "rendered_pages": [
                artifact.page_index for artifact in artifacts
            ],
            "retained_pages": retained_pages,
            "decoded_entries": 0,
            "prepared_units": 0,
            "prepared_requests": 0,
            "render_entries": len(artifacts),
            "artifact_entries": len(artifacts),
            "artifact_pages": [
                artifact.page_index for artifact in artifacts
            ],
            "unique_qpixmap_backings": len(unique_pixmaps),
            "source_mib": 0.0,
            "render_mib": round(unique_bytes / (1024 * 1024), 2),
            "combined_mib": round(unique_bytes / (1024 * 1024), 2),
        }
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
        "path_mode": "legacy",
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
    path_mode: RasterPathMode,
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
        path_mode=path_mode,
    ) as window:
        target = center + 1
        target_id = window.model.image_id_at(target)
        assert target_id is not None
        _force_page_cold(application, window, target)
        _assert_page_cold(window, target)
        commits: list[tuple[str, ...]] = []
        window.viewer.displayCommitted.connect(commits.append)
        with _WorkerTimeline(window) as timeline:
            timeline.mark_current_request(target_id)
            handler, paint = _move_cold_and_measure(
                application,
                window,
                window.next_page,
                target,
                observer=timeline.sample_memory,
            )
            _wait_for_idle(application, window, render_first=False)
            decode_started = timeline.stages("decode_started")
            if not decode_started or decode_started[0] != target_id:
                raise AssertionError(
                    "forward cold did not start with the current target: "
                    f"{decode_started!r}"
                )
            if commits != [(target_id,)]:
                raise AssertionError(
                    f"forward cold commits were not atomic: {commits!r}"
                )
            if window._raster_prefetch_after_paint is not None:
                raise AssertionError("forward cold prefetch remained staged")
            if path_mode == "compatible":
                _pump_until(
                    application,
                    lambda: not window._raster_interactive_lane_held,
                    timeout_seconds=2.0,
                )
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
                "metrics": timeline.metrics(
                    current_image_ids=(target_id,),
                ),
                "retained": _logical_cache_snapshot(window),
            }


def _run_reversal_cold(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
    path_mode: RasterPathMode,
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
        path_mode=path_mode,
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
        _force_page_cold(application, window, old_running)
        _force_page_cold(application, window, old_queued)
        _force_page_cold(application, window, target)
        _assert_page_cold(window, old_running)
        _assert_page_cold(window, old_queued)
        _assert_page_cold(window, target)

        old_started = threading.Event()
        old_cancelled = threading.Event()
        emergency_release = threading.Event()
        original_read = getattr(source, "_read_entry_stream")
        original_streamed = getattr(
            source,
            "open_streamed_jpeg_at_most",
            None,
        )
        original_compatible = getattr(
            source,
            "open_compatible_jpeg_at_most",
            None,
        )
        original_cancel = source.cancel_image_request

        if path_mode == "compatible":
            blocked_cancel = threading.Event()

            def blocking_compatible(image_id, maximum_size):
                if image_id == old_running_id:
                    old_started.set()
                    while not emergency_release.wait(0.001):
                        if blocked_cancel.is_set():
                            old_cancelled.set()
                            raise RuntimeError("cancelled")
                decode_method = original_compatible or original_streamed
                assert decode_method is not None
                return decode_method(image_id, maximum_size)

            def tracking_cancel(image_id):
                if image_id == old_running_id:
                    blocked_cancel.set()
                return original_cancel(image_id)

            source.open_compatible_jpeg_at_most = blocking_compatible
            source.cancel_image_request = tracking_cancel
        else:
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
            with _WorkerTimeline(window) as timeline:
                if path_mode == "compatible":
                    request = window._compatible_raster_request(
                        window.model.spread_at()
                    )
                    if request is None:
                        raise AssertionError(
                            "compatible reversal request was unavailable"
                        )
                    window._compatible_raster_path.request(
                        replace(
                            request,
                            prefetch=(
                                ZipPlaCompatibleRasterPage(
                                    old_running,
                                    old_running_id,
                                ),
                                ZipPlaCompatibleRasterPage(
                                    old_queued,
                                    old_queued_id,
                                ),
                            ),
                        )
                    )
                else:
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
                # Re-requesting the already displayed compatible current
                # publishes its cache hit synchronously while arming the
                # blocked old-direction prefetch. It is setup, not a reversal
                # commit, so exclude it from the measured navigation.
                if path_mode == "compatible":
                    commits.clear()
                    timeline.exclude_compatible_setup_cache_hit()
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
                    observer=timeline.sample_memory,
                )
                _wait_for_idle(application, window, render_first=False)
                if not old_cancelled.is_set():
                    raise AssertionError(
                        "running old-direction ZIP request was not cancelled"
                    )
                decode_started = timeline.stages("decode_started")
                if decode_started[:2] != [old_running_id, target_id]:
                    raise AssertionError(
                        "reversal started stale queued work or wrong order: "
                        f"{decode_started!r}"
                    )
                if old_queued_id in decode_started:
                    raise AssertionError(
                        "queued old-direction page started after reversal"
                    )
                if (
                    old_running in window.image_cache._cache
                    or old_running
                    in window._compatible_raster_path.artifact_page_indexes
                ):
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
                    "metrics": timeline.metrics(
                        current_image_ids=(target_id,),
                    ),
                    "retained": _logical_cache_snapshot(window),
                }
        finally:
            emergency_release.set()
            source.__dict__.pop("_read_entry_stream", None)
            source.__dict__.pop("open_compatible_jpeg_at_most", None)
            source.__dict__.pop("open_streamed_jpeg_at_most", None)
            source.__dict__.pop("cancel_image_request", None)


def _run_roundtrip_cold(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
    path_mode: RasterPathMode,
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
        path_mode=path_mode,
    ) as window:
        first_id = window.model.image_id_at(first)
        second_id = window.model.image_id_at(second)
        assert first_id and second_id
        handlers: list[float] = []
        paints: list[float] = []
        expected_decode_ids: list[str] = []
        commits: list[tuple[str, ...]] = []
        window.viewer.displayCommitted.connect(commits.append)
        with _WorkerTimeline(window) as timeline:
            for leg in range(max(2, int(legs))):
                target = second if leg % 2 == 0 else first
                target_id = second_id if target == second else first_id
                _force_page_cold(application, window, target)
                move = (
                    window.next_page
                    if target == second
                    else window.previous_page
                )
                timeline.mark_current_request(target_id)
                handler, paint = _move_cold_and_measure(
                    application,
                    window,
                    move,
                    target,
                    observer=timeline.sample_memory,
                )
                _wait_for_idle(application, window, render_first=False)
                handlers.append(handler)
                paints.append(paint)
                expected_decode_ids.append(target_id)

            decode_started = timeline.stages("decode_started")
            requested_decode_started = [
                image_id
                for image_id in decode_started
                if image_id in {first_id, second_id}
            ]
            if requested_decode_started != expected_decode_ids:
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
                "timeline": timeline.serializable_events(),
            "metrics": timeline.metrics(
                    current_image_ids=(first_id, second_id),
                ),
                "retained": _logical_cache_snapshot(window),
            }


def _run_rapid_final_cold(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
    path_mode: RasterPathMode,
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
        path_mode=path_mode,
    ) as window:
        for index in targets:
            _force_page_cold(application, window, index)
            _assert_page_cold(window, index)
        final_id = window.model.image_id_at(final)
        assert final_id is not None
        previous_frame = _frame_snapshot(window)
        commits: list[tuple[str, ...]] = []
        window.viewer.displayCommitted.connect(commits.append)
        handler_times: list[float] = []
        with _WorkerTimeline(window) as timeline:
            timeline.mark_current_request(final_id)
            started = time.perf_counter()
            for _target in targets:
                handler_started = time.perf_counter()
                window.next_page()
                handler_times.append(
                    (time.perf_counter() - handler_started) * 1000
                )
            handlers_finished = time.perf_counter()
            if (
                path_mode == "legacy"
                and timeline.stages("decode_started")
            ):
                raise AssertionError(
                    "rapid crossed pages started before input idle"
                )
            if _frame_state_without_paint(window) != previous_frame:
                raise AssertionError(
                    "rapid input replaced the old frame before final ready"
                )
            _wait_for_page(
                application,
                window,
                final,
                observer=timeline.sample_memory,
            )
            painted = time.perf_counter()
            _wait_for_idle(application, window, render_first=False)
            decode_started = timeline.stages("decode_started")
            if path_mode == "legacy" and decode_started != [final_id]:
                raise AssertionError(
                    "rapid input did not coalesce to final page: "
                    f"{decode_started!r}"
                )
            if path_mode == "compatible" and final_id not in decode_started:
                raise AssertionError(
                    "compatible rapid input never decoded final page: "
                    f"{decode_started!r}"
                )
            if commits != [(final_id,)]:
                raise AssertionError(
                    f"rapid final commits were not atomic: {commits!r}"
                )
            if path_mode == "legacy":
                for index in crossed:
                    _assert_page_cold(window, index)
            else:
                allowed_artifacts = {
                    index
                    for index in (final - 1, final, final + 1)
                    if 0 <= index < pages
                }
                retained_artifacts = set(
                    window._compatible_raster_path.artifact_page_indexes
                )
                if not retained_artifacts.issubset(allowed_artifacts):
                    raise AssertionError(
                        "compatible rapid input retained crossed artifacts: "
                        f"{sorted(retained_artifacts - allowed_artifacts)}"
                    )
            final_commit_at = timeline.latest_event_timestamp(
                "display_committed",
                (final_id,),
            )
            if final_commit_at is None:
                raise AssertionError(
                    "rapid final did not record its display-commit boundary"
                )
            decode_events = timeline.timed_stages("decode_started")
            decode_before_commit = [
                image_id
                for image_id, timestamp in decode_events
                if timestamp < final_commit_at
            ]
            decode_after_commit = [
                image_id
                for image_id, timestamp in decode_events
                if timestamp > final_commit_at
            ]
            page_by_image_id = {
                image_id: index
                for index in range(pages)
                if (image_id := window.model.image_id_at(index)) is not None
            }
            crossed_ids = {
                image_id
                for index in crossed
                if (image_id := window.model.image_id_at(index)) is not None
            }
            neighbor_pages = {
                index
                for index in (final - 1, final + 1)
                if 0 <= index < pages
            }
            neighbor_ids = {
                image_id
                for index in neighbor_pages
                if (image_id := window.model.image_id_at(index)) is not None
            }
            crossed_before_commit = [
                image_id
                for image_id in decode_before_commit
                if image_id in crossed_ids
            ]
            neighbor_after_commit = [
                image_id
                for image_id in decode_after_commit
                if image_id in neighbor_ids
            ]
            crossed_before_commit_pages = [
                page_by_image_id[image_id]
                for image_id in crossed_before_commit
            ]
            post_commit_prefetch_pages = [
                page_by_image_id[image_id]
                for image_id in neighbor_after_commit
            ]
            fully_suppressed = [
                index
                for index in crossed
                if index not in set(crossed_before_commit_pages)
            ]
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
                "decode_started_before_final_commit": decode_before_commit,
                "decode_started_after_final_commit": decode_after_commit,
                "crossed_pages_without_commit": list(crossed),
                "suppressed_pages": fully_suppressed,
                "crossed_decode_before_final_commit_pages": (
                    crossed_before_commit_pages
                ),
                "crossed_decode_before_final_commit_image_ids": (
                    crossed_before_commit
                ),
                "post_commit_prefetch_pages": (
                    post_commit_prefetch_pages
                ),
                "neighbor_prefetch_after_final_commit_image_ids": (
                    neighbor_after_commit
                ),
                # Backward-compatible alias. Its scope is now explicit and
                # excludes valid neighbors released after the final commit.
                "started_crossed_image_ids": crossed_before_commit,
                "commits": [list(commit) for commit in commits],
                "old_frame_held": True,
                "timeline": timeline.serializable_events(),
                "metrics": timeline.metrics(
                    current_image_ids=(final_id,),
                ),
                "retained": _logical_cache_snapshot(window),
            }


def _run_dark_frame_check(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
    path_mode: RasterPathMode,
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
        path_mode=path_mode,
    ) as window:
        source = window.image_cache.source
        if source is None or not hasattr(source, "_read_entry_stream"):
            raise AssertionError("dark-frame check requires ZIP source")
        target = center + 1
        target_id = window.model.image_id_at(target)
        assert target_id is not None
        _force_page_cold(application, window, target)
        _assert_page_cold(window, target)
        # Do not paint after evicting the target: paint-gated compatible
        # prefetch would legitimately refill that next-page artifact before
        # the blocking probe is installed.
        previous_frame = _frame_state_without_paint(window)
        original_read = source._read_entry_stream
        original_streamed = getattr(
            source,
            "open_streamed_jpeg_at_most",
            None,
        )
        original_compatible = getattr(
            source,
            "open_compatible_jpeg_at_most",
            None,
        )
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

        if path_mode == "compatible":
            def blocking_compatible(image_id, maximum_size):
                if image_id == target_id:
                    target_started.set()
                    release.wait()
                decode_method = original_compatible or original_streamed
                assert decode_method is not None
                return decode_method(image_id, maximum_size)

            source.open_compatible_jpeg_at_most = blocking_compatible
        else:
            source._read_entry_stream = blocking_read
        window.viewer.clear = forbidden_clear
        commits: list[tuple[str, ...]] = []
        window.viewer.displayCommitted.connect(commits.append)
        timeline = _WorkerTimeline(window)
        timeline.__enter__()
        try:
            timeline.mark_current_request(target_id)
            window.next_page()
            _pump_until(
                application,
                target_started.is_set,
                timeout_seconds=5.0,
                observer=timeline.sample_memory,
            )
            for _iteration in range(3):
                application.processEvents()
                blocked_frame = _frame_snapshot(window)
                if (
                    blocked_frame[:2] != previous_frame[:2]
                    or not blocked_frame[2]
                ):
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
            _wait_for_page(
                application,
                window,
                target,
                observer=timeline.sample_memory,
            )
            _wait_for_idle(application, window, render_first=False)
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
                "timeline": timeline.serializable_events(),
                "metrics": timeline.metrics(
                    current_image_ids=(target_id,),
                ),
                "retained": _logical_cache_snapshot(window),
            }
        finally:
            release.set()
            timeline.__exit__(None, None, None)
            source.__dict__.pop("_read_entry_stream", None)
            source.__dict__.pop("open_compatible_jpeg_at_most", None)
            source.__dict__.pop("open_streamed_jpeg_at_most", None)
            window.viewer.clear = original_clear


def _run_memory_pressure(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
    path_mode: RasterPathMode,
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
        path_mode=path_mode,
        forward_units=1,
        backward_units=1,
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
            render_first=False,
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
        current = window.model.focused_index
        protected = {
            index
            for index in (current - 1, current, current + 1)
            if 0 <= index < pages
        }
        if window._compatible_raster_active:
            combined_bytes = window._compatible_raster_path.artifact_bytes
            protected_source_bytes = 0
            protected_render_bytes = sum(
                artifact.pixmap.width()
                * artifact.pixmap.height()
                * 4
                for artifact
                in window._compatible_raster_path._artifacts.values()
                if artifact.page_index in protected
            )
        else:
            combined_bytes = (
                window.image_cache.cache_bytes
                + window.viewer.render_cache_bytes()
            )
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
    path_mode: RasterPathMode,
) -> tuple[dict[str, object], dict[str, object]]:
    cold = {
        "forward": _run_forward_cold(
            application,
            root,
            archive_path,
            pages=pages,
            viewport_size=viewport_size,
            use_target_decode=use_target_decode,
            path_mode=path_mode,
        ),
        "reversal": _run_reversal_cold(
            application,
            root,
            archive_path,
            pages=pages,
            viewport_size=viewport_size,
            use_target_decode=use_target_decode,
            path_mode=path_mode,
        ),
        "roundtrip": _run_roundtrip_cold(
            application,
            root,
            archive_path,
            pages=pages,
            viewport_size=viewport_size,
            use_target_decode=use_target_decode,
            path_mode=path_mode,
        ),
        "rapid_final": _run_rapid_final_cold(
            application,
            root,
            archive_path,
            pages=pages,
            viewport_size=viewport_size,
            use_target_decode=use_target_decode,
            path_mode=path_mode,
        ),
        "dark_frame": _run_dark_frame_check(
            application,
            root,
            archive_path,
            pages=pages,
            viewport_size=viewport_size,
            use_target_decode=use_target_decode,
            path_mode=path_mode,
        ),
    }
    memory = _run_memory_pressure(
        application,
        root,
        archive_path,
        pages=pages,
        viewport_size=viewport_size,
        use_target_decode=use_target_decode,
        path_mode=path_mode,
    )
    return cold, memory


def _run_initial_cold(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
    path_mode: RasterPathMode,
) -> dict[str, object]:
    """Measure the first image request after an indexed source is attached."""
    config = ConfigManager(root / f"config-initial-cold-{path_mode}.json")
    config.load()
    config.apply(
        {
            "view_mode": "single",
            "single_first_page": False,
            "viewer_downscale_algorithm": "auto",
            "viewer_upscale_algorithm": "auto",
            "fit_mode": "fit_window",
            "viewer_prefetch_preset": "custom",
            "viewer_prefetch_direction_priority_enabled": True,
            "viewer_prefetch_image_forward_units": 0,
            "viewer_prefetch_image_backward_units": 0,
            "viewer_memory_mode": viewer_memory_mode_from_legacy_mib(512),
            "show_page_list": False,
        },
        save=True,
    )
    coordinator = ImageWorkCoordinator(max_workers=1)
    window = ViewerWindow(
        config_manager=config,
        image_work_coordinator=coordinator,
    )
    if path_mode == "legacy":
        window._compatible_raster_request = (  # type: ignore[method-assign]
            lambda _spread: None
        )
    if not use_target_decode:
        window._current_raster_decode_bounds = (  # type: ignore[method-assign]
            lambda: None
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
        window.resize(*viewport_size)
        window.show()
        application.processEvents()
        window._raster_viewport_timer.stop()
        window._refresh_raster_decode_bounds()
        application.processEvents()
        # Source/index construction is deliberately outside this timing. The
        # measured interval starts with production's post-open UI work and
        # first raster request, while the page itself is completely cold.
        opened = window.book_session.open_book(archive_path)
        first_id = window.model.image_id_at(0)
        if first_id is None:
            raise AssertionError("initial cold case has no first image")
        commits: list[tuple[str, ...]] = []
        window.viewer.displayCommitted.connect(commits.append)
        with _WorkerTimeline(window) as timeline:
            timeline.mark_current_request(first_id)
            started = time.perf_counter()
            if not window._finish_opened_book(
                opened,
                modal_on_empty=False,
            ):
                raise AssertionError("initial cold book was rejected")
            handler_finished = time.perf_counter()
            _wait_for_page(
                application,
                window,
                0,
                observer=timeline.sample_memory,
            )
            painted = time.perf_counter()
            _wait_for_idle(application, window, render_first=False)
            if commits != [(first_id,)]:
                raise AssertionError(
                    f"initial frame was not committed once: {commits!r}"
                )
            if path_mode == "compatible":
                if not window._compatible_raster_active:
                    raise AssertionError(
                        "compatible initial frame used the legacy path"
                    )
            elif window._compatible_raster_active:
                raise AssertionError(
                    "legacy initial frame used the compatible path"
                )
            return {
                "handler_ms": round(
                    (handler_finished - started) * 1000,
                    3,
                ),
                "request_to_paint_ms": round(
                    (painted - started) * 1000,
                    3,
                ),
                "commits": [list(commit) for commit in commits],
                "timeline": timeline.serializable_events(),
                "metrics": timeline.metrics(
                    current_image_ids=(first_id,),
                ),
                "retained": _logical_cache_snapshot(window),
            }
    finally:
        shutdown_window()
        atexit.unregister(shutdown_window)


def _run_ready_sequences(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    pages: int,
    viewport_size: tuple[int, int],
    use_target_decode: bool,
    path_mode: RasterPathMode,
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
        path_mode=path_mode,
        forward_units=1,
        backward_units=1,
        cache_mib=512,
    ) as window:
        required_ready = tuple(
            index
            for index in (center - 1, center, center + 1)
            if 0 <= index < pages
        )
        _wait_for_warm_window(
            application,
            window,
            required_page_indexes=required_ready,
        )

        forward_handlers: list[float] = []
        forward_paints: list[float] = []
        forward_target = center + 1
        forward_id = window.model.image_id_at(forward_target)
        assert forward_id is not None
        with _WorkerTimeline(window) as forward_timeline:
            forward_timeline.mark_current_request(forward_id)
            handler, paint = _move_and_measure(
                application,
                window,
                window.next_page,
                forward_target,
            )
            forward_handlers.append(handler)
            forward_paints.append(paint)
            _wait_for_idle(
                application,
                window,
                render_first=False,
            )
            forward_metrics = forward_timeline.metrics(
                current_image_ids=(forward_id,),
            )
            forward_events = forward_timeline.serializable_events()

        reverse_handlers: list[float] = []
        reverse_paints: list[float] = []
        reverse_id = window.model.image_id_at(center)
        assert reverse_id is not None
        with _WorkerTimeline(window) as reverse_timeline:
            reverse_timeline.mark_current_request(reverse_id)
            handler, paint = _move_and_measure(
                application,
                window,
                window.previous_page,
                center,
            )
            reverse_handlers.append(handler)
            reverse_paints.append(paint)
            _wait_for_idle(
                application,
                window,
                render_first=False,
            )
            reverse_metrics = reverse_timeline.metrics(
                current_image_ids=(reverse_id,),
            )
            reverse_events = reverse_timeline.serializable_events()

        roundtrip_handlers: list[float] = []
        roundtrip_paints: list[float] = []
        with _WorkerTimeline(window) as roundtrip_timeline:
            for move, target in (
                (window.next_page, center + 1),
                (window.previous_page, center),
            ) * 4:
                target_id = window.model.image_id_at(target)
                assert target_id is not None
                roundtrip_timeline.mark_current_request(target_id)
                handler, paint = _move_and_measure(
                    application,
                    window,
                    move,
                    target,
                )
                roundtrip_handlers.append(handler)
                roundtrip_paints.append(paint)
            _wait_for_idle(
                application,
                window,
                render_first=False,
            )
            roundtrip_metrics = roundtrip_timeline.metrics(
                current_image_ids=(reverse_id, forward_id),
            )
            roundtrip_events = roundtrip_timeline.serializable_events()

        window._go_to_index_with_history(center)
        _wait_for_page(application, window, center)
        application.processEvents()
        rapid_target = min(pages - 1, center + 8)
        rapid_id = window.model.image_id_at(rapid_target)
        assert rapid_id is not None
        rapid_handlers: list[float] = []
        with _WorkerTimeline(window) as rapid_timeline:
            rapid_timeline.mark_current_request(rapid_id)
            rapid_started = time.perf_counter()
            for _target in range(center + 1, rapid_target + 1):
                handler_started = time.perf_counter()
                window.next_page()
                rapid_handlers.append(
                    (time.perf_counter() - handler_started) * 1000
                )
            rapid_handler_finished = time.perf_counter()
            _wait_for_page(application, window, rapid_target)
            rapid_finished = time.perf_counter()
            _wait_for_idle(
                application,
                window,
                render_first=False,
            )
            rapid_metrics = rapid_timeline.metrics(
                current_image_ids=(rapid_id,),
            )
            rapid_events = rapid_timeline.serializable_events()

        return {
            "environment": {
                "path_mode": path_mode,
                "window_size": [window.width(), window.height()],
                "viewer_size": [
                    window.viewer.width(),
                    window.viewer.height(),
                ],
                "device_pixel_ratio": round(
                    float(window.viewer.devicePixelRatioF()),
                    3,
                ),
                "decode_bounds": list(
                    window._current_raster_decode_bounds() or ()
                ),
                "compatible_active": window._compatible_raster_active,
            },
            "ready_forward": {
                "handler": _summary(forward_handlers),
                "request_to_paint": _summary(forward_paints),
                "timeline": forward_events,
                "metrics": forward_metrics,
            },
            "ready_reverse": {
                "handler": _summary(reverse_handlers),
                "request_to_paint": _summary(reverse_paints),
                "timeline": reverse_events,
                "metrics": reverse_metrics,
            },
            "ready_roundtrip": {
                "handler": _summary(roundtrip_handlers),
                "request_to_paint": _summary(roundtrip_paints),
                "timeline": roundtrip_events,
                "metrics": roundtrip_metrics,
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
                "timeline": rapid_events,
                "metrics": rapid_metrics,
            },
            "cache": _logical_cache_snapshot(window),
        }


def run_benchmark(
    *,
    pages: int = 21,
    image_size: tuple[int, int] = (4096, 6500),
    viewport_size: tuple[int, int] = (3840, 2106),
    use_target_decode: bool = True,
    path_modes: tuple[RasterPathMode, ...] = (
        "legacy",
        "compatible",
    ),
    image_pattern: ImagePattern = "solid",
) -> dict[str, object]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    normalized_modes = tuple(dict.fromkeys(path_modes))
    if not normalized_modes or any(
        mode not in {"legacy", "compatible"}
        for mode in normalized_modes
    ):
        raise ValueError(f"invalid path_modes: {path_modes!r}")
    if not use_target_decode and "compatible" in normalized_modes:
        raise ValueError(
            "compatible A/B requires use_target_decode=True"
        )
    if image_pattern not in {"solid", "detail"}:
        raise ValueError(f"invalid image_pattern: {image_pattern!r}")
    with TemporaryDirectory(prefix="nivisviewer-navigation-") as temporary:
        root = Path(temporary)
        archive_path = root / "日本語 大画像.zip"
        _make_zip(
            archive_path,
            pages=pages,
            size=image_size,
            image_pattern=image_pattern,
        )
        result: dict[str, object] = {
            "source": {
                "pages": pages,
                "image_size": list(image_size),
                "image_pattern": image_pattern,
                "zip_bytes": archive_path.stat().st_size,
                "zip_sha256": _sha256_file(archive_path),
                "requested_window_size": list(viewport_size),
                "target_decode": use_target_decode,
                "same_archive_for_all_paths": True,
            },
            "paths": {},
            "measurement_notes": [
                (
                    "All paths use the same temporary ZIP bytes, requested "
                    "window size, settings, and target decoder bounds."
                ),
                (
                    "QImage/QPixmap byte counts are nominal application-level "
                    "buffers. Native Qt plugin copies and implicit-sharing "
                    "detach operations are not observable from Python."
                ),
                (
                    "QWidget.render uses an offscreen QPixmap and is supporting "
                    "evidence only; real-machine feel remains authoritative."
                ),
            ],
        }
        # Every Viewer is owned by ``_viewer_case``. Its finally block closes
        # the ZIP and coordinator before an exception can unwind into
        # TemporaryDirectory cleanup on Windows.
        path_results = result["paths"]
        assert isinstance(path_results, dict)
        for path_mode in normalized_modes:
            path_result = _run_ready_sequences(
                application,
                root,
                archive_path,
                pages=pages,
                viewport_size=viewport_size,
                use_target_decode=use_target_decode,
                path_mode=path_mode,
            )
            path_result["initial_cold"] = _run_initial_cold(
                application,
                root,
                archive_path,
                viewport_size=viewport_size,
                use_target_decode=use_target_decode,
                path_mode=path_mode,
            )
            cold_miss, memory_pressure = _run_cold_matrix(
                application,
                root,
                archive_path,
                pages=pages,
                viewport_size=viewport_size,
                use_target_decode=use_target_decode,
                path_mode=path_mode,
            )
            path_result["cold_miss"] = cold_miss
            path_result["memory_pressure"] = memory_pressure
            path_results[path_mode] = path_result
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
    parser.add_argument(
        "--path-mode",
        choices=("both", "legacy", "compatible"),
        default="both",
        help=(
            "Run legacy A, compatible B, or both against the same temporary "
            "ZIP. --full-decode is legacy-only."
        ),
    )
    parser.add_argument(
        "--image-pattern",
        choices=("solid", "detail"),
        default="solid",
        help=(
            "Generate compact solid JPEGs or deterministic large, "
            "scan-like detail JPEGs."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write JSON to this path instead of standard output.",
    )
    parser.add_argument("--viewport-width", type=int, default=3840)
    parser.add_argument("--viewport-height", type=int, default=2106)
    arguments = parser.parse_args()
    if arguments.full_decode and arguments.path_mode != "legacy":
        parser.error("--full-decode requires --path-mode legacy")
    selected_modes: tuple[RasterPathMode, ...] = (
        ("legacy", "compatible")
        if arguments.path_mode == "both"
        else (arguments.path_mode,)
    )
    result = run_benchmark(
        pages=max(13, arguments.pages),
        image_size=(
            max(64, arguments.width),
            max(64, arguments.height),
        ),
        viewport_size=(
            max(64, arguments.viewport_width),
            max(64, arguments.viewport_height),
        ),
        use_target_decode=not arguments.full_decode,
        path_modes=selected_modes,
        image_pattern=arguments.image_pattern,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if arguments.output is None:
        print(serialized)
    else:
        arguments.output.write_text(serialized + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
