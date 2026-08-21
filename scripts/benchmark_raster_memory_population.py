"""Measure memory-driven ZIP/folder raster cache population offscreen.

The benchmark drives the production ``RasterBookRuntime`` contract directly:
one current page is requested, its completed QPixmap is painted into an
offscreen QImage, and that paint is acknowledged so the lazy book-wide warm-up
planner may continue.  No application window or native input is created.

Each case runs in a fresh child process and creates only temporary JPEG
fixtures.  The default matrix intentionally avoids a full 2 x 3 x 3 cross
product while still covering both sources, all fixture profiles, and the
256 MiB / 4 GiB / Auto policies.  Use ``--full-matrix`` when every combination
is required, or repeat ``--case SOURCE:PROFILE:MODE`` for selected cases.
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
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from typing import Any, Callable, Iterable
import zipfile


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

# Codex's bundled Python supplies Pillow while the repository environment
# supplies Qt.  Normal project runs do not need this opt-in path.
from PIL import Image, ImageDraw

_EXTRA_SITE_PACKAGES = os.environ.get("NIVIS_BENCHMARK_EXTRA_SITE_PACKAGES")
if _EXTRA_SITE_PACKAGES and _EXTRA_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, _EXTRA_SITE_PACKAGES)

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.image_source import (
    FolderImageSource,
    StreamedJpegDecode,
    ZipImageSource,
)
from app.raster_book_runtime import (
    RasterBookRuntime,
    RasterDisplayUnit,
    RasterFrame,
    RasterPage,
    RasterRenderSpec,
    RasterRequest,
)
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
from app.viewer_memory_policy import (
    read_physical_memory_snapshot,
    resolve_viewer_memory_budget,
)
from app.zip_raster_book_runtime import ZipRasterBookRuntime


_MIB = 1024 * 1024
_SOURCE_KINDS = ("zip", "folder")
_PROFILES = ("small", "large", "mixed")
_MODES = ("256", "4096", "auto")
_PROFILE_SIZES: dict[str, tuple[tuple[int, int], ...]] = {
    "small": ((240, 360),),
    "large": ((2400, 3600),),
    "mixed": ((240, 360), (2400, 3600)),
}
_DEFAULT_CASES = tuple(
    [(source, profile, "256") for source in _SOURCE_KINDS for profile in _PROFILES]
    + [(source, "mixed", mode) for source in _SOURCE_KINDS for mode in ("4096", "auto")]
)


def _unit_identity(unit: RasterDisplayUnit) -> tuple[tuple[int, str], ...]:
    return unit.identity


def _unit_page_indexes(unit: RasterDisplayUnit) -> Iterable[int]:
    return (page.page_index for page in unit.pages)


def _process_memory_bytes() -> tuple[int, int] | None:
    """Return current and process-lifetime peak working set on Windows."""

    if os.name != "nt":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = (
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
        )

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    try:
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
            return None
    except Exception:
        return None
    return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)


class _SourceCounters:
    """Thread-safe, application-visible file/archive and decoder counters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._backend_calls: Counter[str] = Counter()
        self._decode_attempts_by_page: Counter[str] = Counter()
        self._successful_decodes_by_page: Counter[str] = Counter()
        self._payload_reads_by_page: Counter[str] = Counter()
        self._payload_read_bytes = 0
        self._low_level_read_calls = 0
        self._header_reads_by_page: Counter[str] = Counter()
        self._failures = 0

    def begin_decode(self, image_id: str, backend: str) -> None:
        with self._lock:
            self._backend_calls[backend] += 1
            self._decode_attempts_by_page[image_id] += 1

    def finish_decode(
        self,
        image_id: str,
        *,
        succeeded: bool,
        payload_bytes: int,
        low_level_read_calls: int,
    ) -> None:
        with self._lock:
            self._payload_reads_by_page[image_id] += 1
            self._payload_read_bytes += max(0, int(payload_bytes))
            self._low_level_read_calls += max(0, int(low_level_read_calls))
            if succeeded:
                self._successful_decodes_by_page[image_id] += 1
            else:
                self._failures += 1

    def record_header_read(self, image_id: str) -> None:
        with self._lock:
            self._header_reads_by_page[image_id] += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            successful = self._successful_decodes_by_page.copy()
            payload_reads = self._payload_reads_by_page.copy()
            header_reads = self._header_reads_by_page.copy()
            all_reads = payload_reads + header_reads
            return {
                "backend_calls": dict(sorted(self._backend_calls.items())),
                "decode_attempts": sum(self._decode_attempts_by_page.values()),
                "successful_decodes": sum(successful.values()),
                "decoded_page_count": len(successful),
                "duplicate_successful_decodes": sum(
                    max(0, count - 1) for count in successful.values()
                ),
                "payload_read_operations": sum(payload_reads.values()),
                "header_read_operations": sum(header_reads.values()),
                "total_read_operations": sum(all_reads.values()),
                "repeat_read_operations": sum(
                    max(0, count - 1) for count in all_reads.values()
                ),
                "payload_read_bytes": self._payload_read_bytes,
                "low_level_read_calls": self._low_level_read_calls,
                "failed_decoder_calls": self._failures,
            }


class _CountingFolderSource(FolderImageSource):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.benchmark_counters = _SourceCounters()

    def _payload_size(self, image_id: str) -> int:
        try:
            return Path(image_id).stat().st_size
        except OSError:
            return 0

    def open_compatible_jpeg_at_most(
        self,
        image_id: str,
        maximum_size,
    ) -> StreamedJpegDecode | None:
        self.benchmark_counters.begin_decode(image_id, "compatible_jpeg")
        result: StreamedJpegDecode | None = None
        try:
            result = super().open_compatible_jpeg_at_most(
                image_id, maximum_size
            )
            return result
        finally:
            self.benchmark_counters.finish_decode(
                image_id,
                succeeded=result is not None and not result.qimage.isNull(),
                payload_bytes=(
                    result.bytes_read if result is not None else self._payload_size(image_id)
                ),
                low_level_read_calls=(result.read_calls if result is not None else 1),
            )

    def open_qimage_at_most(self, image_id: str, maximum_size):
        self.benchmark_counters.begin_decode(image_id, "qimage_at_most")
        result = None
        try:
            result = super().open_qimage_at_most(image_id, maximum_size)
            return result
        finally:
            self.benchmark_counters.finish_decode(
                image_id,
                succeeded=result is not None and not result[0].isNull(),
                payload_bytes=self._payload_size(image_id),
                low_level_read_calls=1,
            )

    def open_image(self, image_id: str) -> Image.Image:
        self.benchmark_counters.begin_decode(image_id, "pillow_full")
        result: Image.Image | None = None
        try:
            result = super().open_image(image_id)
            return result
        finally:
            self.benchmark_counters.finish_decode(
                image_id,
                succeeded=result is not None,
                payload_bytes=self._payload_size(image_id),
                low_level_read_calls=1,
            )

    def probe_image_size(self, image_id: str) -> tuple[int, int] | None:
        had_cached_size = image_id in self._size_cache
        result = super().probe_image_size(image_id)
        if not had_cached_size:
            self.benchmark_counters.record_header_read(image_id)
        return result


class _CountingZipSource(ZipImageSource):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.benchmark_counters = _SourceCounters()

    def _payload_size(self, image_id: str) -> int:
        return int(self.file_size(image_id) or 0)

    def open_compatible_jpeg_at_most(
        self,
        image_id: str,
        maximum_size,
    ) -> StreamedJpegDecode | None:
        self.benchmark_counters.begin_decode(image_id, "compatible_jpeg")
        result: StreamedJpegDecode | None = None
        try:
            result = super().open_compatible_jpeg_at_most(
                image_id, maximum_size
            )
            return result
        finally:
            self.benchmark_counters.finish_decode(
                image_id,
                succeeded=result is not None and not result.qimage.isNull(),
                payload_bytes=(
                    result.bytes_read if result is not None else self._payload_size(image_id)
                ),
                low_level_read_calls=(result.read_calls if result is not None else 1),
            )

    def open_qimage_at_most(self, image_id: str, maximum_size):
        self.benchmark_counters.begin_decode(image_id, "qimage_at_most")
        result = None
        try:
            result = super().open_qimage_at_most(image_id, maximum_size)
            return result
        finally:
            self.benchmark_counters.finish_decode(
                image_id,
                succeeded=result is not None and not result[0].isNull(),
                payload_bytes=self._payload_size(image_id),
                low_level_read_calls=1,
            )

    def open_image(self, image_id: str) -> Image.Image:
        self.benchmark_counters.begin_decode(image_id, "pillow_full")
        result: Image.Image | None = None
        try:
            result = super().open_image(image_id)
            return result
        finally:
            self.benchmark_counters.finish_decode(
                image_id,
                succeeded=result is not None,
                payload_bytes=self._payload_size(image_id),
                low_level_read_calls=1,
            )

    def probe_image_size(self, image_id: str) -> tuple[int, int] | None:
        had_cached_size = image_id in self._size_cache
        result = super().probe_image_size(image_id)
        if not had_cached_size:
            self.benchmark_counters.record_header_read(image_id)
        return result


def _jpeg_payload(size: tuple[int, int]) -> bytes:
    """Create deterministic detail without a dangerously large fixture."""

    width, height = size
    with Image.new("RGB", size, (38, 55, 76)) as image:
        draw = ImageDraw.Draw(image)
        band = max(8, width // 24)
        for x in range(0, width, band):
            phase = (x // band) % 8
            color = (
                34 + phase * 19,
                58 + ((phase * 37) % 140),
                82 + ((phase * 23) % 130),
            )
            draw.rectangle((x, 0, min(width, x + band), height), fill=color)
        step = max(12, height // 40)
        for y in range(0, height, step):
            shade = 40 + (y // step * 17) % 170
            draw.line((0, y, width, min(height, y + width // 12)), fill=(shade, 210 - shade // 2, 140), width=max(1, width // 300))
        output = io.BytesIO()
        image.save(output, "JPEG", quality=86, subsampling=2, optimize=False)
        return output.getvalue()


def _write_fixture(
    root: Path,
    *,
    source_kind: str,
    profile: str,
    pages: int,
) -> tuple[Path, dict[str, object]]:
    sizes = _PROFILE_SIZES[profile]
    payloads = {size: _jpeg_payload(size) for size in sizes}
    total_payload_bytes = 0
    if source_kind == "zip":
        source_path = root / f"population-{profile}.zip"
        with zipfile.ZipFile(
            source_path, "w", compression=zipfile.ZIP_STORED
        ) as archive:
            for index in range(pages):
                size = sizes[index % len(sizes)]
                payload = payloads[size]
                total_payload_bytes += len(payload)
                info = zipfile.ZipInfo(
                    f"pages/{index:04d}.jpg",
                    date_time=(2024, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, payload)
        fixture_bytes = source_path.stat().st_size
    else:
        source_path = root / f"population-{profile}"
        source_path.mkdir()
        for index in range(pages):
            size = sizes[index % len(sizes)]
            payload = payloads[size]
            total_payload_bytes += len(payload)
            (source_path / f"{index:04d}.jpg").write_bytes(payload)
        fixture_bytes = total_payload_bytes
    return source_path, {
        "temporary": True,
        "source_kind": source_kind,
        "profile": profile,
        "pages": pages,
        "source_dimensions": [list(size) for size in sizes],
        "unique_payload_count": len(payloads),
        "payload_bytes_by_size": {
            f"{width}x{height}": len(payload)
            for (width, height), payload in payloads.items()
        },
        "total_uncompressed_payload_bytes": total_payload_bytes,
        "fixture_bytes": fixture_bytes,
    }


def _make_source(source_kind: str, path: Path):
    return (
        _CountingZipSource(path)
        if source_kind == "zip"
        else _CountingFolderSource(path)
    )


def _make_runtime(
    source_kind: str,
    source,
    *,
    hard_limit_bytes: int,
    soft_target_bytes: int,
) -> RasterBookRuntime:
    runtime_type: type[RasterBookRuntime] = (
        ZipRasterBookRuntime
        if source_kind == "zip"
        else FolderRasterBookRuntime
    )
    return runtime_type(
        source,
        1,
        cache_byte_budget=hard_limit_bytes,
        cache_soft_target_bytes=soft_target_bytes,
    )


def _paint_frame(frame: RasterFrame, viewport: tuple[int, int]) -> None:
    canvas = QImage(
        viewport[0],
        viewport[1],
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    canvas.fill(Qt.GlobalColor.black)
    painter = QPainter(canvas)
    try:
        x = 0
        for page in frame.pages:
            if page.pixmap is None or page.pixmap.isNull():
                continue
            painter.drawPixmap(x, 0, page.pixmap)
            x += max(1, page.pixmap.width())
    finally:
        painter.end()


def _pump_until(
    application: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float,
) -> bool:
    deadline = time.perf_counter() + max(0.0, timeout_seconds)
    while time.perf_counter() < deadline:
        application.processEvents()
        if predicate():
            return True
        time.sleep(0.002)
    application.processEvents()
    return bool(predicate())


def _pump_until_time(application: QApplication, deadline: float) -> None:
    while time.perf_counter() < deadline:
        application.processEvents()
        time.sleep(min(0.005, max(0.0, deadline - time.perf_counter())))
    application.processEvents()


def _snapshot(
    runtime: RasterBookRuntime,
    source,
    *,
    label: str,
    seconds_after_first_paint: float,
    seconds_after_request: float,
    total_pages: int,
    baseline_working_set_bytes: int | None,
) -> dict[str, object]:
    memory = _process_memory_bytes()
    metrics = asdict(runtime.metrics)
    cache_values = runtime.cache_debug_values()
    cache_used = int(cache_values["cache_used_bytes"])
    ready_pages = int(cache_values["ready_page_count"])
    source_bytes = runtime.decoded_source_bytes
    working_set = memory[0] if memory is not None else None
    return {
        "label": label,
        "seconds_after_first_paint": round(seconds_after_first_paint, 3),
        "seconds_after_request": round(seconds_after_request, 3),
        **cache_values,
        "missing_ready_page_count": max(0, int(total_pages) - ready_pages),
        "source_cache_bytes": source_bytes,
        "frame_cache_bytes": max(0, cache_used - source_bytes),
        "runtime_metrics": metrics,
        "source_activity": source.benchmark_counters.snapshot(),
        "process_working_set_bytes": working_set,
        "process_working_set_delta_bytes": (
            None
            if working_set is None or baseline_working_set_bytes is None
            else working_set - baseline_working_set_bytes
        ),
        "process_peak_working_set_bytes": (
            memory[1] if memory is not None else None
        ),
    }


def _probe_unlocked(path: Path) -> bool:
    probe = path.with_name(path.name + ".closed-probe")
    try:
        path.rename(probe)
        probe.rename(path)
        return True
    except OSError:
        if probe.exists() and not path.exists():
            try:
                probe.rename(path)
            except OSError:
                pass
        return False


def _run_worker_case(args: argparse.Namespace) -> dict[str, object]:
    source_kind, profile, mode = _parse_case(str(args.worker_case))
    pages = int(args.pages)
    viewport = (int(args.viewport_width), int(args.viewport_height))
    application = QApplication.instance() or QApplication([])
    temp_path: Path | None = None
    result: dict[str, object] = {}

    with TemporaryDirectory(prefix="nivis-raster-population-") as temp:
        temp_path = Path(temp)
        source_path, fixture = _write_fixture(
            temp_path,
            source_kind=source_kind,
            profile=profile,
            pages=pages,
        )
        source = _make_source(source_kind, source_path)
        memory_after_fixture = _process_memory_bytes()
        baseline_working_set = (
            memory_after_fixture[0] if memory_after_fixture is not None else None
        )
        source_closed = False
        runtime: RasterBookRuntime | None = None
        frames: list[RasterFrame] = []
        shutdown_started = 0.0
        shutdown_complete = False
        drained_callbacks = False
        fixture_unlocked = False
        try:
            memory_snapshot = read_physical_memory_snapshot()
            resolution = resolve_viewer_memory_budget(
                mode,
                snapshot=memory_snapshot,
                current_cache_bytes=0,
            )
            hard_limit = resolution.hard_limit_bytes
            soft_target = resolution.active_soft_target_bytes
            runtime = _make_runtime(
                source_kind,
                source,
                hard_limit_bytes=hard_limit,
                soft_target_bytes=soft_target,
            )
            runtime.frameReady.connect(frames.append)

            image_ids = source.list_images()
            if len(image_ids) != pages:
                raise RuntimeError(
                    f"fixture listed {len(image_ids)} pages, expected {pages}"
                )
            units = tuple(
                RasterDisplayUnit(
                    index,
                    (RasterPage(index, image_id, None),),
                    True,
                )
                for index, image_id in enumerate(image_ids)
            )
            topology = RasterBookTopology(
                units,
                identity_of=_unit_identity,
                page_indexes_of=_unit_page_indexes,
                page_count=pages,
            )
            current = units[0]
            plan = RasterWarmupPlan(
                topology,
                current=current,
                identity_of=_unit_identity,
                page_indexes_of=_unit_page_indexes,
                direction=1,
                background_enabled=True,
            )
            render_spec = RasterRenderSpec(
                viewport,
                fit_mode="fit_window",
                decoder_maximum_size=viewport,
                decoder_layout_sized=True,
            )
            request = RasterRequest(
                1,
                1,
                current,
                plan,
                render_spec,
                navigation_direction=1,
            )
            requested_at = time.perf_counter()
            if not runtime.request(request):
                raise RuntimeError("runtime rejected initial request")
            if not _pump_until(
                application,
                lambda: bool(frames),
                timeout_seconds=float(args.first_frame_timeout),
            ):
                raise TimeoutError("first frame did not complete")
            frame = frames[-1]
            errors = [page.error for page in frame.pages if page.error]
            if errors:
                raise RuntimeError(f"first frame failed: {errors!r}")
            _paint_frame(frame, viewport)
            first_paint_at = time.perf_counter()
            if not runtime.release_prefetch(request_id=1):
                raise RuntimeError("runtime rejected paint acknowledgement")
            frames.clear()

            snapshots = [
                _snapshot(
                    runtime,
                    source,
                    label="first_paint",
                    seconds_after_first_paint=0.0,
                    seconds_after_request=first_paint_at - requested_at,
                    total_pages=pages,
                    baseline_working_set_bytes=baseline_working_set,
                )
            ]
            for seconds in args.snapshot_seconds:
                _pump_until_time(application, first_paint_at + float(seconds))
                now = time.perf_counter()
                snapshots.append(
                    _snapshot(
                        runtime,
                        source,
                        label=f"{float(seconds):g}s",
                        seconds_after_first_paint=now - first_paint_at,
                        seconds_after_request=now - requested_at,
                        total_pages=pages,
                        baseline_working_set_bytes=baseline_working_set,
                    )
                )

            fixture["maximum_retained_preview_pixel_bytes"] = (
                pages * viewport[0] * viewport[1] * 4 * 2
            )
            fixture["safety_note"] = (
                "4 GiB/Auto are hard ceilings, not allocation requests; "
                "300 layout-sized source+frame rasters are bounded to the "
                "reported preview pixel ceiling, with one active worker."
            )
            result = {
                "case": {
                    "source": source_kind,
                    "profile": profile,
                    "memory_mode": mode,
                },
                "fixture": fixture,
                "resolved_memory_policy": resolution.debug_values(active=True),
                "memory_baseline_after_fixture": {
                    "working_set_bytes": baseline_working_set,
                    "process_lifetime_peak_bytes": (
                        memory_after_fixture[1]
                        if memory_after_fixture is not None
                        else None
                    ),
                },
                "first_paint_latency_ms": round(
                    (first_paint_at - requested_at) * 1000, 3
                ),
                "snapshots": snapshots,
            }
        finally:
            shutdown_started = time.perf_counter()
            if runtime is not None:
                try:
                    runtime.frameReady.disconnect(frames.append)
                except (RuntimeError, TypeError):
                    pass
                runtime.cancel(clear_artifacts=True)
                drained_callbacks = _pump_until(
                    application,
                    lambda: not runtime.has_unfinished_tasks(),
                    timeout_seconds=float(args.shutdown_timeout),
                )
                shutdown_complete = runtime.shutdown(
                    wait_msecs=max(1, round(float(args.shutdown_timeout) * 1000))
                )
            source.close()
            source_closed = bool(
                getattr(source, "_zip_closed", True)
                and not getattr(source, "_active_requests", {})
            )
            application.processEvents()
            gc.collect()
            fixture_unlocked = _probe_unlocked(source_path)

        result["shutdown"] = {
            "runtime_complete": shutdown_complete,
            "callbacks_drained": drained_callbacks,
            "active_jobs": runtime.active_job_count if runtime is not None else 0,
            "unfinished_tasks": (
                runtime.has_unfinished_tasks() if runtime is not None else False
            ),
            "source_closed": source_closed,
            "fixture_unlocked": fixture_unlocked,
            "elapsed_ms": round((time.perf_counter() - shutdown_started) * 1000, 3),
        }
        application.quit()

    result["shutdown"]["temporary_fixture_removed"] = bool(
        temp_path is not None and not temp_path.exists()
    )
    return result


def _parse_case(value: str) -> tuple[str, str, str]:
    parts = tuple(part.strip().lower() for part in value.split(":"))
    if len(parts) != 3:
        raise ValueError("case must be SOURCE:PROFILE:MODE")
    source, profile, mode = parts
    if source not in _SOURCE_KINDS:
        raise ValueError(f"unsupported source: {source}")
    if profile not in _PROFILES:
        raise ValueError(f"unsupported profile: {profile}")
    if mode not in _MODES:
        raise ValueError(f"unsupported memory mode: {mode}")
    return source, profile, mode


def _case_text(case: tuple[str, str, str]) -> str:
    return ":".join(case)


def _worker_command(
    args: argparse.Namespace,
    case: tuple[str, str, str],
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-case",
        _case_text(case),
        "--pages",
        str(args.pages),
        "--viewport-width",
        str(args.viewport_width),
        "--viewport-height",
        str(args.viewport_height),
        "--first-frame-timeout",
        str(args.first_frame_timeout),
        "--shutdown-timeout",
        str(args.shutdown_timeout),
        "--snapshot-seconds",
    ]
    command.extend(str(value) for value in args.snapshot_seconds)
    return command


def _run_isolated_case(
    args: argparse.Namespace,
    case: tuple[str, str, str],
) -> dict[str, object]:
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    last_snapshot = max(float(value) for value in args.snapshot_seconds)
    timeout = max(
        60.0,
        float(args.first_frame_timeout)
        + last_snapshot
        + float(args.shutdown_timeout)
        + 45.0,
    )
    completed = subprocess.run(
        _worker_command(args, case),
        cwd=_REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{_case_text(case)} worker failed ({completed.returncode})\n"
            f"stdout:\n{completed.stdout[-4000:]}\n"
            f"stderr:\n{completed.stderr[-4000:]}"
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{_case_text(case)} returned invalid JSON\n"
            f"stdout:\n{completed.stdout[-4000:]}\n"
            f"stderr:\n{completed.stderr[-4000:]}"
        ) from exc
    result["worker_process"] = {
        "exit_code": completed.returncode,
        "stderr": completed.stderr.strip(),
    }
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=300)
    parser.add_argument("--viewport-width", type=int, default=400)
    parser.add_argument("--viewport-height", type=int, default=600)
    parser.add_argument(
        "--snapshot-seconds",
        type=float,
        nargs="+",
        default=(1.0, 3.0, 10.0),
    )
    parser.add_argument("--first-frame-timeout", type=float, default=30.0)
    parser.add_argument("--shutdown-timeout", type=float, default=15.0)
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        metavar="SOURCE:PROFILE:MODE",
        help="repeat for selected cases; source=zip/folder, profile=small/large/mixed",
    )
    parser.add_argument(
        "--full-matrix",
        action="store_true",
        help="run all 18 source/profile/mode combinations",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-case", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 200 <= int(args.pages) <= 500:
        parser.error("--pages must be between 200 and 500")
    if args.viewport_width <= 0 or args.viewport_height <= 0:
        parser.error("viewport dimensions must be positive")
    if args.first_frame_timeout <= 0 or args.shutdown_timeout <= 0:
        parser.error("timeouts must be positive")
    normalized_snapshots = tuple(sorted(set(args.snapshot_seconds)))
    if not normalized_snapshots or normalized_snapshots[0] <= 0:
        parser.error("--snapshot-seconds must contain positive values")
    args.snapshot_seconds = normalized_snapshots
    try:
        if args.worker_case:
            _parse_case(args.worker_case)
        for value in args.case:
            _parse_case(value)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main() -> int:
    args = _parse_args()
    if args.worker_case:
        print(
            json.dumps(
                _run_worker_case(args),
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0

    if args.case:
        cases = tuple(dict.fromkeys(_parse_case(value) for value in args.case))
        matrix_name = "selected"
    elif args.full_matrix:
        cases = tuple(
            (source, profile, mode)
            for source in _SOURCE_KINDS
            for profile in _PROFILES
            for mode in _MODES
        )
        matrix_name = "full"
    else:
        cases = _DEFAULT_CASES
        matrix_name = "default-safe"

    results: list[dict[str, object]] = []
    for index, case in enumerate(cases, start=1):
        print(
            f"[{index}/{len(cases)}] {_case_text(case)}",
            file=sys.stderr,
            flush=True,
        )
        results.append(_run_isolated_case(args, case))

    report = {
        "schema_version": 1,
        "benchmark": "RasterBookRuntime memory-driven population",
        "production_path": (
            "ZipRasterBookRuntime/FolderRasterBookRuntime -> "
            "RasterWarmupPlan -> combined source/frame byte ledger"
        ),
        "qt_platform": os.environ.get("QT_QPA_PLATFORM"),
        "matrix": matrix_name,
        "pages": int(args.pages),
        "snapshot_seconds_after_first_paint": list(args.snapshot_seconds),
        "measurement_scope": {
            "first_paint": (
                "the first production frame QPixmap is drawn into an offscreen "
                "QImage, then release_prefetch acknowledges that paint"
            ),
            "read_operations": (
                "application-visible file/archive payload opens plus uncached "
                "header probes; Qt/plugin-internal reads are not visible"
            ),
            "duplicate_decode": (
                "successful decoder outputs beyond the first output for the "
                "same logical page within one isolated book runtime"
            ),
            "memory": (
                "runtime cache bytes are exact source+frame ledger values; "
                "working-set deltas use the post-fixture baseline; lifetime "
                "peak also includes temporary JPEG generation"
            ),
        },
        "cases": results,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
