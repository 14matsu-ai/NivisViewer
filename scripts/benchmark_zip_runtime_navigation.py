"""Offscreen navigation benchmark for the production ZIP raster runtime.

This script never shows the application window and never synthesizes native
input.  It creates one temporary, deterministic JPEG ZIP and drives the real
``ViewerWindow -> ZipRasterBookRuntime`` path with direct model navigation.

It deliberately does not present the old legacy/compatible A/B as a current
production comparison.  ZIP books no longer select that path; forcing it now
would require a benchmark-only non-ZIP ``ImageSource`` adapter and would not
measure the production ZIP ownership boundary.

The JSON report distinguishes exact application-visible counters from values
that Python cannot observe (Qt/plugin-internal copies and paints are outside
the scope of this probe).
"""

from __future__ import annotations

import argparse
import atexit
from collections import Counter
from contextlib import contextmanager
import ctypes
from dataclasses import asdict, dataclass
import gc
import io
import json
import os
from pathlib import Path
import random
import sys
import threading
import time
from tempfile import TemporaryDirectory
from types import MethodType
from typing import Any, Callable, Iterator
import zipfile

# This must be set before importing PySide6.  ``setdefault`` still lets a test
# runner explicitly choose another non-native platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from PIL import Image, ImageOps
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from app.config_manager import ConfigManager
from app.image_source import StreamedJpegDecode, ZipImageSource
from app.image_work_coordinator import ImageWorkCoordinator
from app.viewer_window import ViewerWindow
import app.zip_raster_book_runtime as runtime_module
from app.zip_raster_book_runtime import ZipRasterBookRuntime, ZipRasterFrame


_MIB = 1024 * 1024


def _jpeg_bytes(size: tuple[int, int], *, pattern: str) -> bytes:
    output = io.BytesIO()
    if pattern == "detail":
        # One deterministic payload is reused for every entry.  This keeps ZIP
        # dimensions and compressed entry size identical across every case.
        random_bytes = random.Random(0x4E49564953).randbytes(
            max(1, int(size[0])) * max(1, int(size[1]))
        )
        with Image.frombytes("L", size, random_bytes) as luminance:
            with ImageOps.colorize(
                luminance,
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


def _make_zip(
    path: Path,
    *,
    pages: int,
    size: tuple[int, int],
    pattern: str,
) -> int:
    payload = _jpeg_bytes(size, pattern=pattern)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for index in range(pages):
            info = zipfile.ZipInfo(
                f"日本語ページ/{index:03d}.jpg",
                date_time=(2024, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, payload)
    return len(payload)


def _process_memory_bytes() -> tuple[int, int] | None:
    """Return current and process-lifetime peak working set on Windows."""

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


def _pump_until(
    application: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float,
    observer: Callable[[], None] | None = None,
) -> None:
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    while time.monotonic() < deadline:
        application.processEvents()
        if observer is not None:
            observer()
        if predicate():
            return
        time.sleep(0.001)
    raise TimeoutError("offscreen ZIP runtime benchmark timed out")


def _render_once(window: ViewerWindow) -> None:
    target = QPixmap(window.viewer.size())
    target.fill()
    window.viewer.render(target)


class _SourceIoProbe:
    """Instrument one temporary ZipImageSource without changing production."""

    def __init__(self, source: ZipImageSource) -> None:
        self.source = source
        self.lock = threading.Lock()
        self.local = threading.local()
        self.counts: Counter[str] = Counter()
        self.decode_events: list[tuple[float, str, str]] = []
        self._patched_names: list[str] = []
        self._patch_stream_reader()
        self._patch_qbytearray_reader()
        self._patch_decoders()

    def _bump(self, name: str, amount: int = 1) -> None:
        with self.lock:
            self.counts[name] += int(amount)

    def _record_decode(self, image_id: str, backend: str) -> None:
        with self.lock:
            self.decode_events.append(
                (time.monotonic(), str(image_id), str(backend))
            )
            self.counts["source_decode_calls"] += 1

    def _install(self, name: str, function: Callable[..., Any]) -> None:
        setattr(self.source, name, MethodType(function, self.source))
        self._patched_names.append(name)

    def _patch_stream_reader(self) -> None:
        original = self.source._read_entry_stream
        probe = self

        def wrapped(_source, image_id, cancelled):
            try:
                stream = original(image_id, cancelled)
            except Exception:
                probe._bump("zip_entry_read_failures")
                raise
            size = len(stream.getbuffer())
            estimated_calls = (size + _source._READ_CHUNK_BYTES - 1) // (
                _source._READ_CHUNK_BYTES
            ) + 1
            with probe.lock:
                probe.counts["zip_entry_open_calls"] += 1
                probe.counts["zip_entry_bytes_read"] += size
                probe.counts["zip_entry_read_calls_estimated"] += estimated_calls
                probe.counts["bytesio_payload_materializations"] += 1
                probe.counts["bytesio_payload_bytes"] += size
            sizes = getattr(probe.local, "stream_sizes", None)
            if sizes is None:
                sizes = []
                probe.local.stream_sizes = sizes
            sizes.append(size)
            return stream

        self._install("_read_entry_stream", wrapped)

    def _patch_qbytearray_reader(self) -> None:
        original = getattr(self.source, "_read_entry_qbytearray", None)
        if original is None:
            return
        probe = self

        def wrapped(_source, image_id, cancelled):
            try:
                payload, calls = original(image_id, cancelled)
            except Exception:
                probe._bump("zip_entry_read_failures")
                raise
            size = int(payload.size())
            with probe.lock:
                probe.counts["zip_entry_open_calls"] += 1
                probe.counts["zip_entry_bytes_read"] += size
                probe.counts["zip_entry_read_calls"] += int(calls)
                probe.counts["qbytearray_payload_materializations"] += 1
                probe.counts["qbytearray_payload_bytes"] += size
            return payload, calls

        self._install("_read_entry_qbytearray", wrapped)

    def _patch_decoders(self) -> None:
        for name in (
            "open_image",
            "open_qimage",
            "open_qimage_at_most",
            "open_compatible_jpeg_at_most",
            "open_streamed_jpeg_at_most",
        ):
            original = getattr(self.source, name, None)
            if original is None:
                continue
            probe = self

            def wrapped(
                _source,
                image_id,
                *args,
                __name=name,
                __original=original,
                **kwargs,
            ):
                probe._record_decode(image_id, __name)
                stream_sizes = getattr(probe.local, "stream_sizes", None)
                before = len(stream_sizes) if stream_sizes is not None else 0
                result = __original(image_id, *args, **kwargs)
                stream_sizes = getattr(probe.local, "stream_sizes", None)
                after_sizes = (
                    stream_sizes[before:]
                    if stream_sizes is not None
                    else []
                )
                # These production methods call BytesIO.getvalue(), creating a
                # second whole-payload handoff.  Pillow's open_image consumes
                # the BytesIO directly and therefore has no such copy here.
                if __name in {"open_qimage", "open_qimage_at_most"}:
                    with probe.lock:
                        probe.counts["whole_payload_handoff_calls"] += len(
                            after_sizes
                        )
                        probe.counts["whole_payload_handoff_bytes"] += sum(
                            after_sizes
                        )
                if isinstance(result, StreamedJpegDecode):
                    # Streamed/compatible APIs bypass _read_entry_stream.  The
                    # compatible implementation is already counted by the
                    # qbytearray wrapper; the sequential implementation is not.
                    if __name == "open_streamed_jpeg_at_most":
                        with probe.lock:
                            probe.counts["zip_entry_open_calls"] += 1
                            probe.counts["zip_entry_bytes_read"] += int(
                                result.bytes_read
                            )
                            probe.counts["zip_entry_read_calls"] += int(
                                result.read_calls
                            )
                    probe._bump("source_qimage_outputs")
                elif isinstance(result, QImage):
                    if not result.isNull():
                        probe._bump("source_qimage_outputs")
                elif (
                    isinstance(result, tuple)
                    and result
                    and isinstance(result[0], QImage)
                    and not result[0].isNull()
                ):
                    probe._bump("source_qimage_outputs")
                elif isinstance(result, Image.Image):
                    probe._bump("source_pil_outputs")
                return result

            self._install(name, wrapped)

    def snapshot(self) -> tuple[dict[str, int], int]:
        with self.lock:
            return dict(self.counts), len(self.decode_events)

    def events_since(self, offset: int) -> list[tuple[float, str, str]]:
        with self.lock:
            return list(self.decode_events[int(offset) :])

    def close(self) -> None:
        for name in reversed(self._patched_names):
            try:
                delattr(self.source, name)
            except AttributeError:
                pass
        self._patched_names.clear()


@dataclass(frozen=True)
class _ScenarioSnapshot:
    started_at: float
    runtime_metrics: dict[str, int]
    probe_counts: dict[str, int]
    event_offset: int
    decode_offset: int
    working_set_start: int | None
    process_peak_start: int | None


class _ViewerProbe:
    def __init__(self, window: ViewerWindow) -> None:
        runtime = window.book_session.viewer_runtime
        source = window.book_session.source
        if not isinstance(runtime, ZipRasterBookRuntime):
            raise AssertionError("ZipRasterBookRuntime was not installed")
        if not isinstance(source, ZipImageSource):
            raise AssertionError("ZipImageSource was not installed")
        self.window = window
        self.runtime = runtime
        self.source_probe = _SourceIoProbe(source)
        self.page_ids = tuple(source.list_images())
        self.events: list[tuple[str, float, object]] = []
        self.function_counts: Counter[str] = Counter()
        self._function_lock = threading.Lock()
        self._scenario_peak_working_set: int | None = None
        self._scenario_peak_cache_bytes = 0
        self._scenario_peak_cache_pages = 0
        self._last_memory_sample = 0.0
        self._original_pil_to_qimage = runtime_module.pil_to_qimage
        self._original_render_qimage = runtime_module.render_qimage
        self._patch_runtime_functions()

        runtime.frameReady.connect(self._on_frame_ready)
        runtime.artifactReady.connect(self._on_artifact_ready)
        window.viewer.displayCommitted.connect(self._on_display_committed)
        window.viewer.contentPainted.connect(self._on_content_painted)
        window.viewer.framePainted.connect(self._on_frame_painted)

    def _patch_runtime_functions(self) -> None:
        probe = self

        def counted_pil_to_qimage(*args, **kwargs):
            result = probe._original_pil_to_qimage(*args, **kwargs)
            with probe._function_lock:
                probe.function_counts["pil_to_qimage_outputs"] += int(
                    result is not None and not result.isNull()
                )
            return result

        def counted_render_qimage(*args, **kwargs):
            result = probe._original_render_qimage(*args, **kwargs)
            image = result[0]
            with probe._function_lock:
                probe.function_counts["display_qimage_outputs"] += int(
                    image is not None and not image.isNull()
                )
            return result

        runtime_module.pil_to_qimage = counted_pil_to_qimage
        runtime_module.render_qimage = counted_render_qimage

    def _event(self, kind: str, payload: object) -> None:
        self.events.append((kind, time.monotonic(), payload))

    def _on_frame_ready(self, frame: object) -> None:
        self._event("frame_ready", frame)

    def _on_artifact_ready(self, artifact: object) -> None:
        self._event("artifact_ready", artifact)

    def _on_display_committed(self, image_ids: object) -> None:
        self._event("display_committed", tuple(image_ids))

    def _on_content_painted(self, image_ids: object) -> None:
        self._event("content_painted", tuple(image_ids))

    def _on_frame_painted(self, serial: int, image_ids: object) -> None:
        self._event("frame_painted", (int(serial), tuple(image_ids)))

    def observe(self, *, force_memory: bool = False) -> None:
        cache_indexes = set(self.runtime.cached_page_indexes)
        self._scenario_peak_cache_pages = max(
            self._scenario_peak_cache_pages, len(cache_indexes)
        )
        self._scenario_peak_cache_bytes = max(
            self._scenario_peak_cache_bytes, self.runtime.cache_bytes
        )
        now = time.monotonic()
        if not force_memory and now - self._last_memory_sample < 0.005:
            return
        self._last_memory_sample = now
        memory = _process_memory_bytes()
        if memory is not None:
            working_set, _peak = memory
            self._scenario_peak_working_set = max(
                self._scenario_peak_working_set or 0, working_set
            )

    def begin(self) -> _ScenarioSnapshot:
        gc.collect()
        self.observe(force_memory=True)
        memory = _process_memory_bytes()
        self._scenario_peak_working_set = memory[0] if memory else None
        self._scenario_peak_cache_bytes = self.runtime.cache_bytes
        self._scenario_peak_cache_pages = len(
            set(self.runtime.cached_page_indexes)
        )
        source_counts, decode_offset = self.source_probe.snapshot()
        with self._function_lock:
            function_counts = dict(self.function_counts)
        return _ScenarioSnapshot(
            time.monotonic(),
            asdict(self.runtime.metrics),
            {**source_counts, **function_counts},
            len(self.events),
            decode_offset,
            memory[0] if memory else None,
            memory[1] if memory else None,
        )

    @staticmethod
    def _delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
        return {
            key: int(after.get(key, 0)) - int(before.get(key, 0))
            for key in sorted(set(after) | set(before))
            if int(after.get(key, 0)) - int(before.get(key, 0))
        }

    def finish(
        self,
        snapshot: _ScenarioSnapshot,
        *,
        requested_pages: list[int],
        final_page: int,
        request_ids: list[int],
        handler_ms: list[float],
        final_request_started_at: float,
        settled: bool,
        cleanup_cancelled: bool = False,
    ) -> dict[str, object]:
        self.observe(force_memory=True)
        memory = _process_memory_bytes()
        runtime_delta = self._delta(
            asdict(self.runtime.metrics), snapshot.runtime_metrics
        )
        source_counts, _decode_offset = self.source_probe.snapshot()
        with self._function_lock:
            all_counts = {**source_counts, **dict(self.function_counts)}
        counter_delta = self._delta(all_counts, snapshot.probe_counts)
        events = self.events[snapshot.event_offset :]
        event_counts = Counter(event[0] for event in events)
        decode_events = self.source_probe.events_since(snapshot.decode_offset)

        frame_events = [
            (timestamp, payload)
            for kind, timestamp, payload in events
            if kind == "frame_ready"
            and isinstance(payload, ZipRasterFrame)
            and payload.request_id in request_ids
        ]
        paint_events = [
            (timestamp, tuple(payload[1]))
            for kind, timestamp, payload in events
            if kind == "frame_painted"
        ]
        final_id = self.page_ids[final_page]
        final_frame = next(
            (
                (timestamp, frame)
                for timestamp, frame in reversed(frame_events)
                if frame.request_id == request_ids[-1]
            ),
            None,
        )
        final_commit_at = next(
            (
                timestamp
                for kind, timestamp, payload in events
                if kind == "display_committed" and final_id in payload
            ),
            None,
        )
        final_paint_at = next(
            (
                timestamp
                for timestamp, image_ids in paint_events
                if final_id in image_ids
                and timestamp >= final_request_started_at
            ),
            None,
        )

        decode_to_paint: list[float] = []
        gui_ready_to_paint: list[float] = []
        for _emitted_at, frame in frame_events:
            if frame.cache_hit:
                continue
            image_ids = {page.image_id for page in frame.pages}
            painted_at = next(
                (
                    timestamp
                    for timestamp, painted_ids in paint_events
                    if timestamp >= frame.gui_ready_at
                    and image_ids.intersection(painted_ids)
                ),
                None,
            )
            if painted_at is not None:
                decode_to_paint.append(
                    round((painted_at - frame.worker_completed_at) * 1000, 3)
                )
                gui_ready_to_paint.append(
                    round((painted_at - frame.gui_ready_at) * 1000, 3)
                )

        requested_ids = {
            self.page_ids[index]
            for index in requested_pages
            if 0 <= index < len(self.page_ids)
        }
        transit_ids = {
            self.page_ids[index]
            for index in requested_pages[:-1]
            if 0 <= index < len(self.page_ids)
        }
        painted_ids = {
            image_id
            for _timestamp, image_ids in paint_events
            for image_id in image_ids
        }
        decoded_ids = [image_id for _at, image_id, _backend in decode_events]

        bytesio_calls = counter_delta.get("bytesio_payload_materializations", 0)
        qbytearray_calls = counter_delta.get(
            "qbytearray_payload_materializations", 0
        )
        handoff_calls = counter_delta.get("whole_payload_handoff_calls", 0)
        bytesio_bytes = counter_delta.get("bytesio_payload_bytes", 0)
        qbytearray_bytes = counter_delta.get("qbytearray_payload_bytes", 0)
        handoff_bytes = counter_delta.get("whole_payload_handoff_bytes", 0)

        working_set_end = memory[0] if memory else None
        process_peak_end = memory[1] if memory else None
        return {
            "requested_pages_zero_based": requested_pages,
            "final_page_zero_based": final_page,
            "request_ids": request_ids,
            "handler_ms": [round(value, 3) for value in handler_ms],
            "request_to_commit_ms": (
                round((final_commit_at - final_request_started_at) * 1000, 3)
                if final_commit_at is not None
                else None
            ),
            "request_to_paint_ms": (
                round((final_paint_at - final_request_started_at) * 1000, 3)
                if final_paint_at is not None
                else None
            ),
            "decode_to_paint_ms": decode_to_paint,
            "gui_ready_to_paint_ms": gui_ready_to_paint,
            "final_frame_cache_hit": (
                bool(final_frame[1].cache_hit) if final_frame is not None else None
            ),
            "runtime": runtime_delta,
            "source_io": {
                "zip_entry_open_calls": counter_delta.get(
                    "zip_entry_open_calls", 0
                ),
                "zip_entry_bytes_read": counter_delta.get(
                    "zip_entry_bytes_read", 0
                ),
                "zip_entry_read_calls": counter_delta.get(
                    "zip_entry_read_calls", 0
                ),
                "zip_entry_read_calls_estimated": counter_delta.get(
                    "zip_entry_read_calls_estimated", 0
                ),
                "zip_entry_read_failures": counter_delta.get(
                    "zip_entry_read_failures", 0
                ),
                "observable_full_payload_materializations": (
                    bytesio_calls + qbytearray_calls + handoff_calls
                ),
                "observable_full_payload_materialization_bytes": (
                    bytesio_bytes + qbytearray_bytes + handoff_bytes
                ),
                "bytesio_payloads": bytesio_calls,
                "qbytearray_payloads": qbytearray_calls,
                "whole_payload_handoffs": handoff_calls,
                "source_decode_calls": counter_delta.get(
                    "source_decode_calls", 0
                ),
                "source_qimage_outputs": counter_delta.get(
                    "source_qimage_outputs", 0
                ),
                "source_pil_outputs": counter_delta.get(
                    "source_pil_outputs", 0
                ),
                "pil_to_qimage_outputs": counter_delta.get(
                    "pil_to_qimage_outputs", 0
                ),
                "display_qimage_outputs": counter_delta.get(
                    "display_qimage_outputs", 0
                ),
            },
            "gui": {
                "frame_ready_callbacks": int(event_counts["frame_ready"]),
                "artifact_ready_callbacks": int(event_counts["artifact_ready"]),
                "display_commits": int(event_counts["display_committed"]),
                "content_paints": int(event_counts["content_painted"]),
                "runtime_frame_paints": int(event_counts["frame_painted"]),
                "runtime_queued_callbacks": runtime_delta.get(
                    "queued_callbacks", 0
                ),
                "qpixmap_from_image": runtime_delta.get(
                    "qpixmap_creations", 0
                ),
            },
            "decode_pages": {
                "decode_call_image_ids": decoded_ids,
                "unique_decode_pages": len(set(decoded_ids)),
                "transit_requested_decode_calls": sum(
                    image_id in transit_ids for image_id in decoded_ids
                ),
                "unpainted_decode_calls": sum(
                    image_id not in painted_ids for image_id in decoded_ids
                ),
                "prefetch_or_other_decode_calls": sum(
                    image_id not in requested_ids for image_id in decoded_ids
                ),
            },
            "cache": {
                "page_indexes_zero_based": sorted(
                    set(self.runtime.cached_page_indexes)
                ),
                "retained_pages": len(set(self.runtime.cached_page_indexes)),
                "retained_bytes": self.runtime.cache_bytes,
                "peak_sampled_pages": self._scenario_peak_cache_pages,
                "peak_sampled_bytes": self._scenario_peak_cache_bytes,
            },
            "memory": {
                "working_set_start_mib": _mib(snapshot.working_set_start),
                "working_set_end_mib": _mib(working_set_end),
                "working_set_delta_mib": (
                    _mib(working_set_end - snapshot.working_set_start)
                    if working_set_end is not None
                    and snapshot.working_set_start is not None
                    else None
                ),
                "sampled_peak_working_set_mib": _mib(
                    self._scenario_peak_working_set
                ),
                "sampled_peak_delta_mib": (
                    _mib(
                        self._scenario_peak_working_set
                        - snapshot.working_set_start
                    )
                    if self._scenario_peak_working_set is not None
                    and snapshot.working_set_start is not None
                    else None
                ),
                "process_peak_working_set_start_mib": _mib(
                    snapshot.process_peak_start
                ),
                "process_peak_working_set_end_mib": _mib(process_peak_end),
            },
            "settled_before_timeout": bool(settled),
            "cleanup_cancelled": bool(cleanup_cancelled),
        }

    def count_events(self, kind: str, *, since: int) -> int:
        return sum(event[0] == kind for event in self.events[int(since) :])

    def has_event_for_image(
        self, kind: str, image_id: str, *, since: int
    ) -> bool:
        for event_kind, _at, payload in self.events[int(since) :]:
            if event_kind != kind:
                continue
            if kind == "frame_painted":
                _serial, image_ids = payload
            else:
                image_ids = payload
            if image_id in image_ids:
                return True
        return False

    def close(self) -> None:
        runtime_module.pil_to_qimage = self._original_pil_to_qimage
        runtime_module.render_qimage = self._original_render_qimage
        self.source_probe.close()
        for signal, callback in (
            (self.runtime.frameReady, self._on_frame_ready),
            (self.runtime.artifactReady, self._on_artifact_ready),
            (self.window.viewer.displayCommitted, self._on_display_committed),
            (self.window.viewer.contentPainted, self._on_content_painted),
            (self.window.viewer.framePainted, self._on_frame_painted),
        ):
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass


def _runtime_quiet(window: ViewerWindow) -> bool:
    runtime = window.book_session.viewer_runtime
    return bool(
        isinstance(runtime, ZipRasterBookRuntime)
        and not runtime.has_unfinished_tasks()
        and runtime._active_job is None
        and window._pending_zip_runtime_request is None
        and not window._zip_runtime_request_timer.isActive()
    )


def _wait_for_quiet(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    *,
    timeout_seconds: float,
    maximum_new_jobs: int | None = None,
    starting_jobs: int = 0,
) -> bool:
    stable_since: float | None = None
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    while time.monotonic() < deadline:
        application.processEvents()
        probe.observe()
        if (
            maximum_new_jobs is not None
            and probe.runtime.metrics.jobs_submitted - starting_jobs
            > maximum_new_jobs
        ):
            return False
        if _runtime_quiet(window):
            now = time.monotonic()
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= 0.005:
                return True
        else:
            stable_since = None
        time.sleep(0.001)
    return False


def _execute_leg(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    action: Callable[[], object],
    target_page: int,
    *,
    timeout_seconds: float,
) -> dict[str, object]:
    event_offset = len(probe.events)
    started_at = time.monotonic()
    action()
    handler_ms = (time.monotonic() - started_at) * 1000
    request_id = int(window._active_request_id)
    target_id = probe.page_ids[target_page]
    _pump_until(
        application,
        lambda: (
            window.viewer.displayed_page_indexes == (target_page,)
            and probe.has_event_for_image(
                "display_committed", target_id, since=event_offset
            )
        ),
        timeout_seconds=timeout_seconds,
        observer=probe.observe,
    )
    _render_once(window)
    application.processEvents()
    probe.observe(force_memory=True)
    _pump_until(
        application,
        lambda: probe.has_event_for_image(
            "frame_painted", target_id, since=event_offset
        ),
        timeout_seconds=timeout_seconds,
        observer=probe.observe,
    )
    return {
        "started_at": started_at,
        "handler_ms": handler_ms,
        "request_id": request_id,
    }


def _ensure_anchor_and_clear(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    anchor: int,
    *,
    timeout_seconds: float,
) -> None:
    if window.viewer.displayed_page_indexes != (anchor,):
        _execute_leg(
            application,
            window,
            probe,
            lambda: window._go_to_index_with_history(anchor),
            anchor,
            timeout_seconds=timeout_seconds,
        )
        if not _wait_for_quiet(
            application,
            window,
            probe,
            timeout_seconds=timeout_seconds,
        ):
            raise TimeoutError("anchor prefetch did not settle")
    probe.runtime.invalidate_layout()
    if not _wait_for_quiet(
        application,
        window,
        probe,
        timeout_seconds=timeout_seconds,
    ):
        raise TimeoutError("runtime did not settle after cold-cache reset")
    application.processEvents()
    gc.collect()


def _install_legacy_frontier_purge(runtime: ZipRasterBookRuntime) -> None:
    """Benchmark-only reproduction of the superseded three-unit purge.

    This does not add a production fallback. It lets the same process fixture,
    decoder and Viewer path measure only the cache-ownership policy changed by
    ``_ZipRasterFrameStore``.
    """

    original_request = runtime.request

    def request_with_frontier_purge(
        current_runtime: ZipRasterBookRuntime,
        request,
    ) -> bool:
        accepted = original_request(request)
        desired = set(current_runtime._work_keys)
        frames = current_runtime._frame_store._frames
        for key in tuple(frames):
            if desired and key not in desired:
                frames.pop(key, None)
        return accepted

    runtime.request = MethodType(request_with_frontier_purge, runtime)


def _finish_regular_scenario(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    snapshot: _ScenarioSnapshot,
    *,
    requested_pages: list[int],
    legs: list[dict[str, object]],
    timeout_seconds: float,
) -> dict[str, object]:
    settled = _wait_for_quiet(
        application,
        window,
        probe,
        timeout_seconds=timeout_seconds,
    )
    return probe.finish(
        snapshot,
        requested_pages=requested_pages,
        final_page=requested_pages[-1],
        request_ids=[int(leg["request_id"]) for leg in legs],
        handler_ms=[float(leg["handler_ms"]) for leg in legs],
        final_request_started_at=float(legs[-1]["started_at"]),
        settled=settled,
    )


@contextmanager
def _viewer_case(
    application: QApplication,
    root: Path,
    archive_path: Path,
    *,
    viewport: tuple[int, int],
    cache_mib: int,
    timeout_seconds: float,
    cache_policy: str,
) -> Iterator[tuple[ViewerWindow, _ViewerProbe]]:
    config = ConfigManager(root / "benchmark-config.json")
    config.load()
    config.apply(
        {
            "view_mode": "single",
            "single_first_page": False,
            "viewer_resampling_mode": "standard",
            "fit_mode": "fit_window",
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
    probe: _ViewerProbe | None = None
    shutdown_complete = False

    def shutdown() -> None:
        nonlocal shutdown_complete
        if shutdown_complete:
            return
        shutdown_complete = True
        window.prepare_shutdown(wait_msecs=10_000)
        if probe is not None:
            probe.close()
        window.close()
        application.processEvents()
        coordinator.shutdown(wait_msecs=10_000)

    atexit.register(shutdown)
    try:
        window.resize(*viewport)
        application.processEvents()
        window.open_path(archive_path)
        if not window.book_session.wait_for_async(
            round(timeout_seconds * 1000)
        ):
            raise TimeoutError("book source preparation timed out")
        _pump_until(
            application,
            lambda: window.model.total_pages > 0,
            timeout_seconds=timeout_seconds,
        )
        _pump_until(
            application,
            lambda: window.viewer.displayed_page_indexes == (0,),
            timeout_seconds=timeout_seconds,
        )
        _render_once(window)
        application.processEvents()
        if not window._zip_runtime_active:
            raise AssertionError("production ZIP runtime was not selected")
        probe = _ViewerProbe(window)
        if cache_policy == "legacy-frontier-purge":
            _install_legacy_frontier_purge(probe.runtime)
        if not _wait_for_quiet(
            application,
            window,
            probe,
            timeout_seconds=timeout_seconds,
        ):
            raise TimeoutError("initial ZIP runtime work did not settle")
        yield window, probe
    finally:
        shutdown()
        atexit.unregister(shutdown)


def _run_scenarios(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    *,
    timeout_seconds: float,
    pressure_mib: int,
) -> dict[str, object]:
    results: dict[str, object] = {}

    # Cold forward.
    _ensure_anchor_and_clear(
        application, window, probe, 1, timeout_seconds=timeout_seconds
    )
    snapshot = probe.begin()
    leg = _execute_leg(
        application,
        window,
        probe,
        lambda: window._go_to_index_with_history(2),
        2,
        timeout_seconds=timeout_seconds,
    )
    results["cold_forward"] = _finish_regular_scenario(
        application,
        window,
        probe,
        snapshot,
        requested_pages=[2],
        legs=[leg],
        timeout_seconds=timeout_seconds,
    )

    # Direction reversal: allow the forward current job to enter the worker,
    # then replace it with the reverse target before pumping a frame commit.
    _ensure_anchor_and_clear(
        application, window, probe, 4, timeout_seconds=timeout_seconds
    )
    snapshot = probe.begin()
    first_started = time.monotonic()
    window._go_to_index_with_history(5)
    first_handler = (time.monotonic() - first_started) * 1000
    first_request_id = int(window._active_request_id)
    submitted_before = snapshot.runtime_metrics.get("jobs_submitted", 0)
    _pump_until(
        application,
        lambda: probe.runtime.metrics.jobs_submitted > submitted_before,
        timeout_seconds=timeout_seconds,
        observer=probe.observe,
    )
    reverse_leg = _execute_leg(
        application,
        window,
        probe,
        lambda: window._go_to_index_with_history(3),
        3,
        timeout_seconds=timeout_seconds,
    )
    results["cold_reversal"] = _finish_regular_scenario(
        application,
        window,
        probe,
        snapshot,
        requested_pages=[5, 3],
        legs=[
            {
                "started_at": first_started,
                "handler_ms": first_handler,
                "request_id": first_request_id,
            },
            reverse_leg,
        ],
        timeout_seconds=timeout_seconds,
    )

    # Two cold legs.  Clearing runtime artifacts after the outward paint keeps
    # the return leg from becoming an accidental neighbor-cache hit.
    _ensure_anchor_and_clear(
        application, window, probe, 6, timeout_seconds=timeout_seconds
    )
    snapshot = probe.begin()
    outward = _execute_leg(
        application,
        window,
        probe,
        lambda: window._go_to_index_with_history(7),
        7,
        timeout_seconds=timeout_seconds,
    )
    probe.runtime.invalidate_layout()
    _wait_for_quiet(
        application,
        window,
        probe,
        timeout_seconds=timeout_seconds,
    )
    returned = _execute_leg(
        application,
        window,
        probe,
        lambda: window._go_to_index_with_history(6),
        6,
        timeout_seconds=timeout_seconds,
    )
    results["cold_roundtrip"] = _finish_regular_scenario(
        application,
        window,
        probe,
        snapshot,
        requested_pages=[7, 6],
        legs=[outward, returned],
        timeout_seconds=timeout_seconds,
    )

    # Rapid input is intentionally not event-pumped between page requests.
    _ensure_anchor_and_clear(
        application, window, probe, 1, timeout_seconds=timeout_seconds
    )
    rapid_pages = [2, 3, 4, 5, 6, 7]
    snapshot = probe.begin()

    def rapid_action() -> None:
        for page in rapid_pages:
            window._go_to_index_with_history(page)

    rapid_leg = _execute_leg(
        application,
        window,
        probe,
        rapid_action,
        rapid_pages[-1],
        timeout_seconds=timeout_seconds,
    )
    results["rapid_final"] = _finish_regular_scenario(
        application,
        window,
        probe,
        snapshot,
        requested_pages=rapid_pages,
        legs=[rapid_leg],
        timeout_seconds=timeout_seconds,
    )

    # Walk beyond the old three-unit frontier, let each current/neighbor job
    # settle, then return to a previously completed page without clearing the
    # book cache. This distinguishes retention from cold decode without
    # changing decoder, ZIP payload, viewport, or input timing.
    _ensure_anchor_and_clear(
        application, window, probe, 1, timeout_seconds=timeout_seconds
    )
    for page in (2, 3, 4, 5):
        _execute_leg(
            application,
            window,
            probe,
            lambda target=page: window._go_to_index_with_history(target),
            page,
            timeout_seconds=timeout_seconds,
        )
        if not _wait_for_quiet(
            application, window, probe, timeout_seconds=timeout_seconds
        ):
            raise TimeoutError("retention-path setup did not settle")
    snapshot = probe.begin()
    retained_return = _execute_leg(
        application,
        window,
        probe,
        lambda: window._go_to_index_with_history(2),
        2,
        timeout_seconds=timeout_seconds,
    )
    results["retained_roundtrip"] = _finish_regular_scenario(
        application,
        window,
        probe,
        snapshot,
        requested_pages=[2],
        legs=[retained_return],
        timeout_seconds=timeout_seconds,
    )

    # Populate a complete current/neighbor work order, then repeat the exact
    # current request to exercise the display-ready cache-hit path.
    _ensure_anchor_and_clear(
        application, window, probe, 8, timeout_seconds=timeout_seconds
    )
    _execute_leg(
        application,
        window,
        probe,
        window._refresh_view,
        8,
        timeout_seconds=timeout_seconds,
    )
    if not _wait_for_quiet(
        application, window, probe, timeout_seconds=timeout_seconds
    ):
        raise TimeoutError("ready-path setup did not settle")
    snapshot = probe.begin()
    ready_leg = _execute_leg(
        application,
        window,
        probe,
        window._refresh_view,
        8,
        timeout_seconds=timeout_seconds,
    )
    results["ready"] = _finish_regular_scenario(
        application,
        window,
        probe,
        snapshot,
        requested_pages=[8],
        legs=[ready_leg],
        timeout_seconds=timeout_seconds,
    )

    # Bound this case independently: if pressure causes repeated neighbor
    # submissions, report that fact and cancel only this runtime's request.
    normal_budget = window.image_cache.cache_byte_budget_bytes
    _ensure_anchor_and_clear(
        application, window, probe, 9, timeout_seconds=timeout_seconds
    )
    pressure_bytes = max(1, int(pressure_mib) * _MIB)
    window.image_cache.set_cache_byte_budget_bytes(pressure_bytes)
    snapshot = probe.begin()
    starting_jobs = probe.runtime.metrics.jobs_submitted
    pressure_leg = _execute_leg(
        application,
        window,
        probe,
        lambda: window._go_to_index_with_history(10),
        10,
        timeout_seconds=timeout_seconds,
    )
    settled = _wait_for_quiet(
        application,
        window,
        probe,
        timeout_seconds=min(5.0, timeout_seconds),
        maximum_new_jobs=12,
        starting_jobs=starting_jobs,
    )
    cleanup_cancelled = False
    if not settled:
        cleanup_cancelled = True
        probe.runtime.cancel(clear_artifacts=False)
        _wait_for_quiet(
            application,
            window,
            probe,
            timeout_seconds=timeout_seconds,
        )
    results["memory_pressure"] = probe.finish(
        snapshot,
        requested_pages=[10],
        final_page=10,
        request_ids=[int(pressure_leg["request_id"])],
        handler_ms=[float(pressure_leg["handler_ms"])],
        final_request_started_at=float(pressure_leg["started_at"]),
        settled=settled,
        cleanup_cancelled=cleanup_cancelled,
    )
    results["memory_pressure"]["configured_budget_bytes"] = pressure_bytes
    window.image_cache.set_cache_byte_budget_bytes(normal_budget)
    probe.runtime.set_cache_limits(byte_budget=normal_budget)

    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark ViewerWindow's production ZipRasterBookRuntime using "
            "one temporary offscreen JPEG ZIP."
        )
    )
    parser.add_argument("--pages", type=int, default=12)
    parser.add_argument("--width", type=int, default=3200)
    parser.add_argument("--height", type=int, default=5000)
    parser.add_argument(
        "--pattern", choices=("solid", "detail"), default="detail"
    )
    parser.add_argument("--viewport-width", type=int, default=1200)
    parser.add_argument("--viewport-height", type=int, default=800)
    parser.add_argument("--cache-mib", type=int, default=256)
    parser.add_argument("--pressure-mib", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--cache-policy",
        choices=("retention", "legacy-frontier-purge"),
        default="retention",
        help=(
            "retention is production B; legacy-frontier-purge is a "
            "benchmark-only reproduction of superseded A"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON output path; stdout is always written",
    )
    args = parser.parse_args()
    if args.pages < 11:
        parser.error("--pages must be at least 11 for the fixed scenarios")
    for name in (
        "width",
        "height",
        "viewport_width",
        "viewport_height",
        "cache_mib",
        "pressure_mib",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def main() -> int:
    args = _parse_args()
    application = QApplication.instance() or QApplication([])
    size = (int(args.width), int(args.height))
    viewport = (int(args.viewport_width), int(args.viewport_height))
    with TemporaryDirectory(prefix="nivis-zip-runtime-benchmark-") as temp:
        root = Path(temp)
        archive_path = root / "same-large-images.zip"
        entry_bytes = _make_zip(
            archive_path,
            pages=int(args.pages),
            size=size,
            pattern=str(args.pattern),
        )
        with _viewer_case(
            application,
            root,
            archive_path,
            viewport=viewport,
            cache_mib=int(args.cache_mib),
            timeout_seconds=float(args.timeout),
            cache_policy=str(args.cache_policy),
        ) as (window, probe):
            scenarios = _run_scenarios(
                application,
                window,
                probe,
                timeout_seconds=float(args.timeout),
                pressure_mib=int(args.pressure_mib),
            )
        report = {
            "schema_version": 2,
            "path": "ViewerWindow -> ZipRasterBookRuntime",
            "cache_policy": str(args.cache_policy),
            "qt_platform": os.environ.get("QT_QPA_PLATFORM"),
            "fixture": {
                "temporary_directory": True,
                "pages": int(args.pages),
                "image_size": list(size),
                "jpeg_pattern": str(args.pattern),
                "identical_payload_per_entry": True,
                "entry_uncompressed_bytes": entry_bytes,
                "zip_file_bytes": archive_path.stat().st_size,
                "viewport": list(viewport),
            },
            "measurement_scope": {
                "legacy_ab": (
                    "cache-policy A/B only: both modes use the production ZIP "
                    "runtime, decoder and render path; legacy-frontier-purge "
                    "reproduces only the superseded cache-membership rule"
                ),
                "retained_roundtrip": (
                    "walks four pages beyond the old three-unit frontier and "
                    "returns without an artificial cache clear"
                ),
                "zip_bytes": (
                    "exact for completed application-visible entry reads; "
                    "a read cancelled before the source returns is counted as "
                    "a failure but its partial bytes are not observable"
                ),
                "full_payload_materializations": (
                    "BytesIO/QByteArray payloads and explicit getvalue handoffs "
                    "in ZipImageSource; Qt plugin/internal copies are excluded"
                ),
                "qimage": (
                    "source decoder outputs, Pillow conversions, and display "
                    "render outputs in the runtime job"
                ),
                "qpixmap": "ZipRasterBookRuntime QPixmap.fromImage calls",
                "paint": (
                    "ViewerWidget contentPainted/framePainted emissions caused "
                    "by explicit offscreen QWidget.render calls"
                ),
                "memory": (
                    "Windows process working-set samples at approximately 5 ms; "
                    "process peak is lifetime-wide, not scenario-exclusive"
                ),
            },
            "scenarios": scenarios,
        }

    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output is not None:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
