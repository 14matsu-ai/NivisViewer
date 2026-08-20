"""Measure layout invalidation with and without decoded-source retention.

The benchmark is deliberately narrower than the navigation benchmark.  It
creates one deterministic JPEG in a temporary ZIP and applies the same five
layout changes to two completely separate ``ZipImageSource`` /
``ZipRasterBookRuntime`` pairs:

* A clears display frames and decoded sources for every layout change.
* B clears only layout-dependent display frames and retains decoded sources.

Each case runs in its own offscreen child process so its working-set peak and
Qt/Pillow caches cannot contaminate the other case.  No application window is
created, no native input is generated, and every runtime/source is explicitly
shut down before its worker process exits.
"""

from __future__ import annotations

import argparse
from collections import Counter
import ctypes
from dataclasses import asdict
import gc
import io
import json
import os
from pathlib import Path
import random
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from typing import Any, Callable
import zipfile

# The platform plugin must be selected before importing PySide6.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from PIL import Image, ImageOps

# Codex's bundled validation Python can supply Pillow while the repository's
# existing environment supplies Qt.  Normal project runs need no override;
# the opt-in path only makes that mixed validation environment possible.
_EXTRA_SITE_PACKAGES = os.environ.get("NIVIS_BENCHMARK_EXTRA_SITE_PACKAGES")
if _EXTRA_SITE_PACKAGES and _EXTRA_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, _EXTRA_SITE_PACKAGES)

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.image_source import ZipImageSource
import app.zip_raster_book_runtime as runtime_module
from app.zip_raster_book_runtime import (
    ZipRasterBookRuntime,
    ZipRasterDisplayUnit,
    ZipRasterFrame,
    ZipRasterPage,
    ZipRasterRenderSpec,
    ZipRasterRequest,
)


_MIB = 1024 * 1024
_ENTRY_NAME = "日本語ページ/000.jpg"
_CASE_CLEAR_ALL = "A-clear-all"
_CASE_REUSE_SOURCE = "B-reuse-source"


def _jpeg_bytes(size: tuple[int, int], *, pattern: str) -> bytes:
    output = io.BytesIO()
    if pattern == "detail":
        luminance = random.Random(0x4E49564953).randbytes(size[0] * size[1])
        with Image.frombytes("L", size, luminance) as gray:
            with ImageOps.colorize(
                gray,
                black=(12, 24, 44),
                white=(246, 232, 204),
            ) as image:
                image.save(output, "JPEG", quality=90, subsampling=2)
        return output.getvalue()
    if pattern != "solid":
        raise ValueError(f"unsupported JPEG pattern: {pattern!r}")
    with Image.new("RGB", size, (73, 106, 140)) as image:
        image.save(output, "JPEG", quality=90, subsampling=2)
    return output.getvalue()


def _write_fixture(
    archive_path: Path,
    *,
    image_size: tuple[int, int],
    pattern: str,
) -> int:
    payload = _jpeg_bytes(image_size, pattern=pattern)
    info = zipfile.ZipInfo(_ENTRY_NAME, date_time=(2024, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, payload)
    return len(payload)


def _process_memory_bytes() -> tuple[int, int] | None:
    """Return current and lifetime-peak working set on Windows."""

    if os.name != "nt":
        return None

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
        get_current_process(), ctypes.byref(counters), counters.cb
    ):
        return None
    return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)


def _mib(value: int | float | None) -> float | None:
    return None if value is None else round(float(value) / _MIB, 3)


def _delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    return {
        key: int(after.get(key, 0)) - int(before.get(key, 0))
        for key in sorted(set(after) | set(before))
        if int(after.get(key, 0)) - int(before.get(key, 0))
    }


class _CountingZipSource(ZipImageSource):
    """Count completed application-visible entry reads and decoder calls."""

    def __init__(self, archive_path: Path) -> None:
        super().__init__(archive_path)
        self._probe_lock = threading.Lock()
        self.counts: Counter[str] = Counter()

    def _bump(self, name: str, amount: int = 1) -> None:
        with self._probe_lock:
            self.counts[name] += int(amount)

    def snapshot(self) -> dict[str, int]:
        with self._probe_lock:
            return dict(self.counts)

    def _read_entry_stream(self, image_id, cancelled):
        try:
            stream = super()._read_entry_stream(image_id, cancelled)
        except Exception:
            self._bump("zip_entry_read_failures")
            raise
        size = len(stream.getbuffer())
        self._bump("zip_entry_read_count")
        self._bump("zip_entry_read_bytes", size)
        self._bump("bytesio_payload_count")
        self._bump("bytesio_payload_bytes", size)
        return stream

    def open_qimage_at_most(self, image_id, maximum_size):
        self._bump("decode_calls")
        self._bump("qimage_reader_decode_calls")
        result = super().open_qimage_at_most(image_id, maximum_size)
        # This production method hands BytesIO.getvalue() to the Qt decoder.
        size = self.file_size(image_id) or 0
        self._bump("whole_payload_copy_count")
        self._bump("whole_payload_copy_bytes", size)
        if result is not None and not result[0].isNull():
            self._bump("source_qimage_outputs")
        return result

    def open_image(self, image_id: str) -> Image.Image:
        self._bump("decode_calls")
        self._bump("pillow_decode_calls")
        image = super().open_image(image_id)
        self._bump("source_pil_outputs")
        return image


class _CaseProbe:
    def __init__(self, runtime: ZipRasterBookRuntime) -> None:
        self.runtime = runtime
        self.frame_events: list[tuple[float, ZipRasterFrame]] = []
        self.artifact_callbacks = 0
        self.display_qimage_outputs = 0
        self.pil_to_qimage_outputs = 0
        self.peak_working_set: int | None = None
        self.peak_source_bytes = 0
        self.peak_frame_bytes = 0
        self.peak_total_cache_bytes = 0
        self._original_render_qimage = runtime_module.render_qimage
        self._original_pil_to_qimage = runtime_module.pil_to_qimage

        def counted_render_qimage(*args, **kwargs):
            result = self._original_render_qimage(*args, **kwargs)
            image = result[0]
            if isinstance(image, QImage) and not image.isNull():
                self.display_qimage_outputs += 1
            return result

        def counted_pil_to_qimage(*args, **kwargs):
            image = self._original_pil_to_qimage(*args, **kwargs)
            if isinstance(image, QImage) and not image.isNull():
                self.pil_to_qimage_outputs += 1
            return image

        runtime_module.render_qimage = counted_render_qimage
        runtime_module.pil_to_qimage = counted_pil_to_qimage
        runtime.frameReady.connect(self._on_frame_ready)
        runtime.artifactReady.connect(self._on_artifact_ready)

    def _on_frame_ready(self, frame: object) -> None:
        if isinstance(frame, ZipRasterFrame):
            self.frame_events.append((time.perf_counter(), frame))

    def _on_artifact_ready(self, _artifact: object) -> None:
        self.artifact_callbacks += 1

    def observe(self) -> None:
        memory = _process_memory_bytes()
        if memory is not None:
            self.peak_working_set = max(
                self.peak_working_set or 0,
                memory[0],
            )
        source_bytes = self.runtime.decoded_source_bytes
        total_bytes = self.runtime.cache_bytes
        frame_bytes = max(0, total_bytes - source_bytes)
        self.peak_source_bytes = max(self.peak_source_bytes, source_bytes)
        self.peak_frame_bytes = max(self.peak_frame_bytes, frame_bytes)
        self.peak_total_cache_bytes = max(
            self.peak_total_cache_bytes,
            total_bytes,
        )

    def close(self) -> None:
        runtime_module.render_qimage = self._original_render_qimage
        runtime_module.pil_to_qimage = self._original_pil_to_qimage
        for signal, callback in (
            (self.runtime.frameReady, self._on_frame_ready),
            (self.runtime.artifactReady, self._on_artifact_ready),
        ):
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass


def _pump_until(
    application: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float,
    observer: Callable[[], None],
) -> None:
    deadline = time.perf_counter() + max(0.1, float(timeout_seconds))
    while time.perf_counter() < deadline:
        application.processEvents()
        observer()
        if predicate():
            return
        time.sleep(0.001)
    raise TimeoutError("ZIP layout-reuse benchmark timed out")


def _layout_specs(
    viewport: tuple[int, int],
) -> tuple[tuple[str, ZipRasterRenderSpec, str], ...]:
    width, height = viewport
    smaller = (max(1, width * 3 // 4), max(1, height * 3 // 4))
    return (
        (
            "initial_preview",
            ZipRasterRenderSpec(
                viewport,
                decoder_maximum_size=viewport,
            ),
            "initial fit-window decoder-sized source",
        ),
        (
            "smaller_resize",
            ZipRasterRenderSpec(
                smaller,
                decoder_maximum_size=smaller,
            ),
            "smaller fit-window layout",
        ),
        (
            "full_resolution_rotation",
            ZipRasterRenderSpec(
                viewport,
                rotation=90,
                decoder_maximum_size=None,
            ),
            "rotation forces one full-resolution source",
        ),
        (
            "dpi_zoom",
            ZipRasterRenderSpec(
                viewport,
                device_pixel_ratio=1.5,
                fit_mode="manual_zoom",
                manual_zoom=0.25,
                decoder_maximum_size=None,
            ),
            "high-DPI manual zoom using the full source",
        ),
        (
            "magnifier_equivalent",
            ZipRasterRenderSpec(
                (max(1, width * 4 // 5), max(1, height * 4 // 5)),
                fit_mode="manual_zoom",
                manual_zoom=0.5,
                decoder_maximum_size=None,
            ),
            "full-source magnifier-equivalent render demand",
        ),
    )


def _run_worker_case(args: argparse.Namespace) -> dict[str, Any]:
    application = QApplication.instance() or QApplication([])
    source = _CountingZipSource(Path(args.archive))
    runtime = ZipRasterBookRuntime(
        source,
        1,
        cache_unit_limit=3,
        cache_byte_budget=int(args.cache_mib) * _MIB,
    )
    probe = _CaseProbe(runtime)
    frames: list[ZipRasterFrame] = []
    runtime.frameReady.connect(frames.append)
    image_size = (int(args.width), int(args.height))
    page = ZipRasterPage(0, _ENTRY_NAME, known_size=image_size)
    unit = ZipRasterDisplayUnit(0, (page,), True)
    gc.collect()
    memory_start = _process_memory_bytes()
    probe.observe()
    metrics_start = asdict(runtime.metrics)
    counts_start = source.snapshot()
    qimage_start = (
        probe.display_qimage_outputs,
        probe.pil_to_qimage_outputs,
    )
    frame_callback_start = len(probe.frame_events)
    artifact_callback_start = probe.artifact_callbacks
    steps: list[dict[str, Any]] = []
    total_started_at = time.perf_counter()
    shutdown_complete = False
    source_closed = False
    try:
        for request_id, (name, spec, description) in enumerate(
            _layout_specs((int(args.viewport_width), int(args.viewport_height))),
            start=1,
        ):
            step_started_at = time.perf_counter()
            if request_id > 1:
                if args.worker_case == _CASE_CLEAR_ALL:
                    runtime.cancel(clear_artifacts=True)
                else:
                    runtime.invalidate_layout()

            before_metrics = asdict(runtime.metrics)
            before_counts = source.snapshot()
            before_frame_callbacks = len(probe.frame_events)
            before_artifact_callbacks = probe.artifact_callbacks
            before_display_qimages = probe.display_qimage_outputs
            before_pil_qimages = probe.pil_to_qimage_outputs
            request = ZipRasterRequest(
                1,
                request_id,
                unit,
                (unit,),
                spec,
            )
            if not runtime.request(request):
                raise RuntimeError(f"runtime rejected request {request_id}")
            _pump_until(
                application,
                lambda current=request_id: bool(frames)
                and frames[-1].request_id == current
                and not runtime.has_unfinished_tasks(),
                timeout_seconds=float(args.timeout),
                observer=probe.observe,
            )
            application.processEvents()
            probe.observe()
            frame = frames[-1]
            if any(page.error is not None for page in frame.pages):
                raise RuntimeError(
                    f"{name} returned an error frame: "
                    f"{[page.error for page in frame.pages]!r}"
                )
            if any(page.pixmap is None for page in frame.pages):
                raise RuntimeError(f"{name} returned an incomplete frame")

            metrics_delta = _delta(asdict(runtime.metrics), before_metrics)
            counts_delta = _delta(source.snapshot(), before_counts)
            source_bytes = runtime.decoded_source_bytes
            total_cache_bytes = runtime.cache_bytes
            steps.append(
                {
                    "name": name,
                    "description": description,
                    "elapsed_ms": round(
                        (time.perf_counter() - step_started_at) * 1000,
                        3,
                    ),
                    "zip_entry_read_count": counts_delta.get(
                        "zip_entry_read_count", 0
                    ),
                    "zip_entry_read_bytes": counts_delta.get(
                        "zip_entry_read_bytes", 0
                    ),
                    "decode_count": counts_delta.get("decode_calls", 0),
                    "jobs_submitted": metrics_delta.get("jobs_submitted", 0),
                    "queued_callbacks": metrics_delta.get(
                        "queued_callbacks", 0
                    ),
                    "frame_ready_callbacks": (
                        len(probe.frame_events) - before_frame_callbacks
                    ),
                    "artifact_ready_callbacks": (
                        probe.artifact_callbacks - before_artifact_callbacks
                    ),
                    "qpixmap_from_image": metrics_delta.get(
                        "qpixmap_creations", 0
                    ),
                    "display_qimage_outputs": (
                        probe.display_qimage_outputs - before_display_qimages
                    ),
                    "pil_to_qimage_outputs": (
                        probe.pil_to_qimage_outputs - before_pil_qimages
                    ),
                    "source_cache_hits": metrics_delta.get(
                        "source_cache_hits", 0
                    ),
                    "source_cache_misses": metrics_delta.get(
                        "source_cache_misses", 0
                    ),
                    "source_cache_bytes": source_bytes,
                    "frame_cache_bytes": max(
                        0, total_cache_bytes - source_bytes
                    ),
                    "total_cache_bytes": total_cache_bytes,
                    "retained_source_pages": runtime.decoded_source_count,
                    "retained_frame_units": runtime.cached_unit_count,
                }
            )

        total_elapsed_ms = round(
            (time.perf_counter() - total_started_at) * 1000,
            3,
        )
        totals_metrics = _delta(asdict(runtime.metrics), metrics_start)
        totals_counts = _delta(source.snapshot(), counts_start)
        memory_end = _process_memory_bytes()
        source_bytes = runtime.decoded_source_bytes
        total_cache_bytes = runtime.cache_bytes
        result = {
            "case": str(args.worker_case),
            "policy": (
                "cancel(clear_artifacts=True) before every layout change"
                if args.worker_case == _CASE_CLEAR_ALL
                else "invalidate_layout() preserves decoded sources"
            ),
            "total_elapsed_ms": total_elapsed_ms,
            "steps": steps,
            "totals": {
                "zip_entry_read_count": totals_counts.get(
                    "zip_entry_read_count", 0
                ),
                "zip_entry_read_bytes": totals_counts.get(
                    "zip_entry_read_bytes", 0
                ),
                "decode_count": totals_counts.get("decode_calls", 0),
                "qimage_reader_decode_calls": totals_counts.get(
                    "qimage_reader_decode_calls", 0
                ),
                "pillow_decode_calls": totals_counts.get(
                    "pillow_decode_calls", 0
                ),
                "observable_full_payload_copy_count": totals_counts.get(
                    "whole_payload_copy_count", 0
                ),
                "observable_full_payload_copy_bytes": totals_counts.get(
                    "whole_payload_copy_bytes", 0
                ),
                "jobs_submitted": totals_metrics.get("jobs_submitted", 0),
                "queued_callbacks": totals_metrics.get(
                    "queued_callbacks", 0
                ),
                "frame_ready_callbacks": (
                    len(probe.frame_events) - frame_callback_start
                ),
                "artifact_ready_callbacks": (
                    probe.artifact_callbacks - artifact_callback_start
                ),
                "qpixmap_from_image": totals_metrics.get(
                    "qpixmap_creations", 0
                ),
                "display_qimage_outputs": (
                    probe.display_qimage_outputs - qimage_start[0]
                ),
                "pil_to_qimage_outputs": (
                    probe.pil_to_qimage_outputs - qimage_start[1]
                ),
                "source_cache_hits": totals_metrics.get(
                    "source_cache_hits", 0
                ),
                "source_cache_misses": totals_metrics.get(
                    "source_cache_misses", 0
                ),
            },
            "cache": {
                "source_bytes": source_bytes,
                "frame_bytes": max(0, total_cache_bytes - source_bytes),
                "total_bytes": total_cache_bytes,
                "peak_sampled_source_bytes": probe.peak_source_bytes,
                "peak_sampled_frame_bytes": probe.peak_frame_bytes,
                "peak_sampled_total_bytes": probe.peak_total_cache_bytes,
                "retained_source_pages": runtime.decoded_source_count,
                "retained_frame_units": runtime.cached_unit_count,
            },
            "memory": {
                "working_set_start_mib": _mib(
                    memory_start[0] if memory_start else None
                ),
                "working_set_end_mib": _mib(
                    memory_end[0] if memory_end else None
                ),
                "working_set_delta_mib": (
                    _mib(memory_end[0] - memory_start[0])
                    if memory_start is not None and memory_end is not None
                    else None
                ),
                "sampled_peak_working_set_mib": _mib(
                    probe.peak_working_set
                ),
                "sampled_peak_delta_mib": (
                    _mib(probe.peak_working_set - memory_start[0])
                    if probe.peak_working_set is not None
                    and memory_start is not None
                    else None
                ),
                "process_peak_start_mib": _mib(
                    memory_start[1] if memory_start else None
                ),
                "process_peak_end_mib": _mib(
                    memory_end[1] if memory_end else None
                ),
                "process_peak_delta_mib": (
                    _mib(memory_end[1] - memory_start[1])
                    if memory_start is not None and memory_end is not None
                    else None
                ),
            },
            "settled": not runtime.has_unfinished_tasks(),
        }
    finally:
        runtime.frameReady.disconnect(frames.append)
        probe.close()
        shutdown_complete = runtime.shutdown(
            wait_msecs=max(1, round(float(args.timeout) * 1000))
        )
        source.close()
        source_closed = bool(source._zip_closed)
        frames.clear()
        application.processEvents()
        application.quit()

    if not shutdown_complete:
        raise RuntimeError("runtime did not complete shutdown")
    if not source_closed:
        raise RuntimeError("ZIP source remained open after shutdown")
    result["shutdown"] = {
        "runtime_complete": shutdown_complete,
        "archive_closed": source_closed,
        "active_jobs": runtime.active_job_count,
        "unfinished_tasks": runtime.has_unfinished_tasks(),
    }
    return result


def _worker_command(
    args: argparse.Namespace,
    archive_path: Path,
    case: str,
) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-case",
        case,
        "--archive",
        str(archive_path),
        "--width",
        str(args.width),
        "--height",
        str(args.height),
        "--viewport-width",
        str(args.viewport_width),
        "--viewport-height",
        str(args.viewport_height),
        "--cache-mib",
        str(args.cache_mib),
        "--timeout",
        str(args.timeout),
    ]


def _run_isolated_case(
    args: argparse.Namespace,
    archive_path: Path,
    case: str,
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONUNBUFFERED"] = "1"
    completed = subprocess.run(
        _worker_command(args, archive_path, case),
        cwd=_REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=max(30.0, float(args.timeout) * 8.0),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{case} worker failed with exit code {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{case} worker returned invalid JSON\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        ) from exc


def _comparison(
    old_case: dict[str, Any],
    new_case: dict[str, Any],
) -> dict[str, Any]:
    old_totals = old_case["totals"]
    new_totals = new_case["totals"]

    def saved(name: str) -> int:
        return int(old_totals[name]) - int(new_totals[name])

    old_ms = float(old_case["total_elapsed_ms"])
    new_ms = float(new_case["total_elapsed_ms"])
    return {
        "elapsed_ms_saved": round(old_ms - new_ms, 3),
        "speedup_ratio_A_over_B": (
            round(old_ms / new_ms, 3) if new_ms > 0 else None
        ),
        "zip_entry_reads_avoided": saved("zip_entry_read_count"),
        "zip_entry_bytes_avoided": saved("zip_entry_read_bytes"),
        "decodes_avoided": saved("decode_count"),
        "full_payload_copies_avoided": saved(
            "observable_full_payload_copy_count"
        ),
        "jobs_difference_A_minus_B": saved("jobs_submitted"),
        "callbacks_difference_A_minus_B": saved("queued_callbacks"),
        "qpixmap_uploads_difference_A_minus_B": saved(
            "qpixmap_from_image"
        ),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=2400)
    parser.add_argument("--height", type=int, default=3600)
    parser.add_argument("--viewport-width", type=int, default=1200)
    parser.add_argument("--viewport-height", type=int, default=800)
    parser.add_argument(
        "--pattern", choices=("detail", "solid"), default="detail"
    )
    parser.add_argument("--cache-mib", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="use a smaller detailed JPEG for a fast smoke benchmark",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--worker-case",
        choices=(_CASE_CLEAR_ALL, _CASE_REUSE_SOURCE),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--archive", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.quick and args.worker_case is None:
        args.width = 900
        args.height = 1350
        args.viewport_width = 600
        args.viewport_height = 400
    for name in (
        "width",
        "height",
        "viewport_width",
        "viewport_height",
        "cache_mib",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.worker_case is not None and args.archive is None:
        parser.error("--archive is required for a worker case")
    return args


def main() -> int:
    args = _parse_args()
    if args.worker_case is not None:
        print(
            json.dumps(
                _run_worker_case(args),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    image_size = (int(args.width), int(args.height))
    viewport = (int(args.viewport_width), int(args.viewport_height))
    with TemporaryDirectory(prefix="nivis-zip-layout-reuse-") as temp:
        archive_path = Path(temp) / "same-layout-image.zip"
        entry_bytes = _write_fixture(
            archive_path,
            image_size=image_size,
            pattern=str(args.pattern),
        )
        old_case = _run_isolated_case(args, archive_path, _CASE_CLEAR_ALL)
        new_case = _run_isolated_case(args, archive_path, _CASE_REUSE_SOURCE)
        report = {
            "schema_version": 1,
            "benchmark": "ZipRasterBookRuntime layout source reuse",
            "qt_platform": "offscreen",
            "fixture": {
                "temporary_directory": True,
                "entry_name": _ENTRY_NAME,
                "image_size": list(image_size),
                "viewport": list(viewport),
                "jpeg_pattern": str(args.pattern),
                "zip_compression": "stored",
                "entry_bytes": entry_bytes,
                "cache_budget_bytes": int(args.cache_mib) * _MIB,
            },
            "measurement_scope": {
                "A": (
                    "separate source/runtime/process; cancel(clear_artifacts=True) "
                    "before each layout change"
                ),
                "B": (
                    "separate source/runtime/process; invalidate_layout() keeps "
                    "decoded QImage sources while clearing QPixmap frames"
                ),
                "magnifier_equivalent": (
                    "full-resolution source demand plus a distinct manual-zoom "
                    "display artifact; no ViewerWidget or native input"
                ),
                "zip_bytes": (
                    "exact completed _read_entry_stream payload bytes; partial "
                    "cancelled reads would be reported as failures"
                ),
                "working_set": (
                    "each case is an isolated process; sampled peak is observed "
                    "during event pumping and the OS process peak is also reported"
                ),
            },
            "A_old_clear_all": old_case,
            "B_new_reuse_source": new_case,
            "comparison": _comparison(old_case, new_case),
        }

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
