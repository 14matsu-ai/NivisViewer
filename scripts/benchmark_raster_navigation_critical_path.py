"""Measure the production ZIP/Folder raster navigation critical path.

The parent process builds one deterministic mixed-size fixture in a temporary
directory and starts a fresh offscreen child for each source kind.  Each child
opens the normal ``ViewerWindow -> RasterBookRuntime`` production path and
drives it with synthetic Qt wheel events or direct page jumps.  The historical
admission variants are not reconstructed: reported values always describe the
current production policy.  No window is shown and no native input is
generated.

Timing probes are benchmark-only instance wrappers.  They deliberately avoid
adding production logging to the page-turn path and use ``perf_counter_ns`` so
sub-frame handler work is not rounded to the Windows scheduler tick.
"""

from __future__ import annotations

import argparse
from collections import Counter
import ctypes
from dataclasses import asdict, dataclass
import io
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from types import MethodType
from typing import Any, Callable, Iterable
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

# The bundled validation Python owns the matching native Pillow extension.
# Import it before adding a repository Qt-only site-packages directory.
from PIL import Image, ImageOps

_EXTRA_SITE_PACKAGES = os.environ.get("NIVIS_BENCHMARK_EXTRA_SITE_PACKAGES")
if _EXTRA_SITE_PACKAGES and _EXTRA_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, _EXTRA_SITE_PACKAGES)

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication

from app.config_manager import ConfigManager
from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.image_source import (
    FolderImageSource,
    StreamedJpegDecode,
    ZipImageSource,
)
from app.image_work_coordinator import ImageWorkCoordinator
from app.raster_book_runtime import RasterBookRuntime
from app.viewer_window import ViewerWindow


_MIB = 1024 * 1024
_SOURCE_KINDS = ("zip", "folder")
_ADMISSION_MODES = ("current",)


def _now_ns() -> int:
    return time.perf_counter_ns()


def _ms(delta_ns: int | None) -> float | None:
    if delta_ns is None:
        return None
    return round(float(delta_ns) / 1_000_000.0, 3)


def _mib(value: int | float | None) -> float | None:
    return None if value is None else round(float(value) / _MIB, 3)


def _process_memory_bytes() -> tuple[int, int] | None:
    if os.name != "nt":
        return None

    class _ProcessMemoryCounters(ctypes.Structure):
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

    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    current_process = ctypes.windll.kernel32.GetCurrentProcess
    memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
    current_process.restype = ctypes.c_void_p
    memory_info.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessMemoryCounters),
        ctypes.c_ulong,
    )
    memory_info.restype = ctypes.c_int
    if not memory_info(
        current_process(),
        ctypes.byref(counters),
        counters.cb,
    ):
        return None
    return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)


def _delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    return {
        key: int(after.get(key, 0)) - int(before.get(key, 0))
        for key in sorted(set(after) | set(before))
    }


def _summary(values: Iterable[float | None]) -> dict[str, float | int | None]:
    numeric = sorted(float(value) for value in values if value is not None)
    if not numeric:
        return {
            "count": 0,
            "minimum": None,
            "median": None,
            "p95": None,
            "maximum": None,
        }
    p95_index = max(0, math.ceil(len(numeric) * 0.95) - 1)
    return {
        "count": len(numeric),
        "minimum": round(numeric[0], 3),
        "median": round(statistics.median(numeric), 3),
        "p95": round(numeric[p95_index], 3),
        "maximum": round(numeric[-1], 3),
    }


def _jpeg_bytes(size: tuple[int, int], *, detail: bool) -> bytes:
    output = io.BytesIO()
    if detail:
        noise = random.Random(0x4E49564953 + size[0] * 31 + size[1]).randbytes(
            max(1, int(size[0])) * max(1, int(size[1]))
        )
        with Image.frombytes("L", size, noise) as luminance:
            with ImageOps.colorize(
                luminance,
                black=(12, 24, 44),
                white=(246, 232, 204),
            ) as image:
                image.save(output, "JPEG", quality=90, subsampling=2)
    else:
        with Image.new("RGB", size, (73, 106, 140)) as image:
            image.save(output, "JPEG", quality=90, subsampling=2)
    return output.getvalue()


def _build_fixture(
    root: Path,
    *,
    pages: int,
    small_size: tuple[int, int],
    large_size: tuple[int, int],
    detail: bool,
) -> dict[str, Any]:
    payloads = {
        small_size: _jpeg_bytes(small_size, detail=detail),
        large_size: _jpeg_bytes(large_size, detail=detail),
    }
    sizes = tuple(
        large_size if index % 2 else small_size for index in range(pages)
    )
    folder = root / "大小混在フォルダ"
    folder.mkdir(parents=True, exist_ok=True)
    archive_path = root / "大小混在.zip"
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as archive:
        for index, size in enumerate(sizes):
            payload = payloads[size]
            name = f"日本語ページ-{index:03d}.jpg"
            (folder / name).write_bytes(payload)
            info = zipfile.ZipInfo(
                f"日本語ページ/{name}",
                date_time=(2024, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, payload)
    return {
        "paths": {
            "zip": str(archive_path),
            "folder": str(folder),
        },
        "pages": int(pages),
        "page_sizes": [list(size) for size in sizes],
        "small_size": list(small_size),
        "large_size": list(large_size),
        "payload_bytes": {
            f"{width}x{height}": len(payloads[(width, height)])
            for width, height in (small_size, large_size)
        },
        "detail": bool(detail),
    }


def _successful_decode(result: object) -> bool:
    if isinstance(result, StreamedJpegDecode):
        return not result.qimage.isNull()
    if isinstance(result, QImage):
        return not result.isNull()
    if isinstance(result, Image.Image):
        return True
    if isinstance(result, tuple) and result:
        first = result[0]
        return isinstance(first, QImage) and not first.isNull()
    return False


def _decoded_output_size(result: object) -> tuple[int | None, int | None]:
    image: object = result
    if isinstance(result, StreamedJpegDecode):
        image = result.qimage
    elif isinstance(result, tuple) and result:
        image = result[0]
    if isinstance(image, QImage):
        if image.isNull():
            return None, None
        return int(image.width()), int(image.height())
    if isinstance(image, Image.Image):
        return int(image.width), int(image.height)
    return None, None


@dataclass(frozen=True)
class _DecodeEvent:
    image_id: str
    backend: str
    started_ns: int
    completed_ns: int
    success: bool
    output_width: int | None
    output_height: int | None


class _SourceProbe:
    """Count application-visible source reads and decoder calls."""

    def __init__(self, source: ZipImageSource | FolderImageSource) -> None:
        self.source = source
        self._lock = threading.Lock()
        self.counts: Counter[str] = Counter()
        self.decode_events: list[_DecodeEvent] = []
        self._installed: list[tuple[str, bool, object | None]] = []
        if isinstance(source, ZipImageSource):
            self._patch_zip_reads()
        self._patch_decoders()

    def _install(self, name: str, function: Callable[..., object]) -> None:
        had_instance_value = name in self.source.__dict__
        previous = self.source.__dict__.get(name)
        setattr(self.source, name, MethodType(function, self.source))
        self._installed.append((name, had_instance_value, previous))

    def _bump(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self.counts[name] += int(amount)

    def _record_read(self, size: int, *, chunks: int = 1) -> None:
        with self._lock:
            self.counts["read_calls"] += 1
            self.counts["read_bytes"] += max(0, int(size))
            self.counts["read_chunks"] += max(0, int(chunks))

    def _patch_zip_reads(self) -> None:
        source = self.source
        assert isinstance(source, ZipImageSource)
        original_stream = getattr(source, "_read_entry_stream", None)
        if callable(original_stream):
            probe = self

            def read_stream(_source, image_id, cancelled):
                try:
                    stream = original_stream(image_id, cancelled)
                except Exception:
                    probe._bump("read_failures")
                    raise
                size = len(stream.getbuffer())
                chunks = (size + _source._READ_CHUNK_BYTES - 1) // (
                    _source._READ_CHUNK_BYTES
                ) + 1
                probe._record_read(size, chunks=chunks)
                return stream

            self._install("_read_entry_stream", read_stream)

        original_qbytearray = getattr(source, "_read_entry_qbytearray", None)
        if callable(original_qbytearray):
            probe = self

            def read_qbytearray(_source, image_id, cancelled):
                try:
                    payload, calls = original_qbytearray(image_id, cancelled)
                except Exception:
                    probe._bump("read_failures")
                    raise
                probe._record_read(int(payload.size()), chunks=int(calls))
                return payload, calls

            self._install("_read_entry_qbytearray", read_qbytearray)

    def _patch_decoders(self) -> None:
        for name in (
            "open_image",
            "open_qimage",
            "open_qimage_at_most",
            "open_compatible_jpeg_at_most",
            "open_streamed_jpeg_at_most",
        ):
            original = getattr(self.source, name, None)
            if not callable(original):
                continue
            probe = self

            def decode(
                _source,
                image_id,
                *args,
                __name=name,
                __original=original,
                **kwargs,
            ):
                started = _now_ns()
                probe._bump("decode_attempts")
                success = False
                output_width: int | None = None
                output_height: int | None = None
                try:
                    result = __original(image_id, *args, **kwargs)
                    success = _successful_decode(result)
                    output_width, output_height = _decoded_output_size(result)
                    if success:
                        probe._bump("decode_successes")
                    if (
                        isinstance(probe.source, FolderImageSource)
                        and Path(str(image_id)).is_file()
                    ):
                        try:
                            probe._record_read(Path(str(image_id)).stat().st_size)
                        except OSError:
                            probe._bump("read_failures")
                    if (
                        __name == "open_streamed_jpeg_at_most"
                        and isinstance(result, StreamedJpegDecode)
                    ):
                        probe._record_read(
                            int(result.bytes_read),
                            chunks=int(result.read_calls),
                        )
                    return result
                except Exception:
                    probe._bump("decode_failures")
                    raise
                finally:
                    completed = _now_ns()
                    with probe._lock:
                        probe.decode_events.append(
                            _DecodeEvent(
                                str(image_id),
                                str(__name),
                                started,
                                completed,
                                bool(success),
                                output_width,
                                output_height,
                            )
                        )

            self._install(name, decode)

    def snapshot(self) -> tuple[dict[str, int], int]:
        with self._lock:
            return dict(self.counts), len(self.decode_events)

    def events_since(self, offset: int) -> list[_DecodeEvent]:
        with self._lock:
            return list(self.decode_events[max(0, int(offset)) :])

    def close(self) -> None:
        for name, had_value, previous in reversed(self._installed):
            if had_value:
                setattr(self.source, name, previous)
            else:
                self.source.__dict__.pop(name, None)
        self._installed.clear()


@dataclass(frozen=True)
class _TraceEvent:
    kind: str
    at_ns: int
    payload: dict[str, object]


@dataclass(frozen=True)
class _InputRecord:
    kind: str
    request_id: int
    target_page: int
    image_id: str
    started_ns: int
    completed_ns: int


@dataclass(frozen=True)
class _ScenarioSnapshot:
    started_ns: int
    event_offset: int
    decode_offset: int
    runtime_metrics: dict[str, int]
    source_counts: dict[str, int]
    working_set_start: int | None
    process_peak_start: int | None


class _NavigationProbe:
    """Benchmark-only timings around the production navigation boundary."""

    _UI_METHODS = (
        ("_update_slider", "slider"),
        ("_update_status", "status"),
        ("_sync_page_list_selection", "page_list_selection"),
        ("_sync_page_history_actions", "history_actions"),
        ("_sync_actions", "all_actions"),
    )

    def __init__(self, window: ViewerWindow) -> None:
        runtime = window.book_session.viewer_runtime
        source = window.book_session.source
        if not isinstance(runtime, RasterBookRuntime):
            raise AssertionError("production RasterBookRuntime was not installed")
        if not isinstance(source, (ZipImageSource, FolderImageSource)):
            raise AssertionError("benchmark source is not ZIP or Folder")
        self.window = window
        self.runtime = runtime
        self.source = source
        self.source_probe = _SourceProbe(source)
        self.events: list[_TraceEvent] = []
        self._installed: list[tuple[object, str, bool, object | None]] = []
        self._connect_signals()
        self._patch_production_boundaries()

    def _event(self, kind: str, **payload: object) -> None:
        self.events.append(_TraceEvent(kind, _now_ns(), dict(payload)))

    def _install(
        self,
        owner: object,
        name: str,
        function: Callable[..., object],
    ) -> bool:
        namespace = getattr(owner, "__dict__", None)
        if not isinstance(namespace, dict) or not callable(getattr(owner, name, None)):
            return False
        had_value = name in namespace
        previous = namespace.get(name)
        setattr(owner, name, MethodType(function, owner))
        self._installed.append((owner, name, had_value, previous))
        return True

    @staticmethod
    def _request_identity(request: object) -> tuple[tuple[int, str], ...]:
        current = getattr(request, "current", None)
        identity = getattr(current, "identity", ())
        return tuple(
            (int(page_index), str(image_id))
            for page_index, image_id in identity
        )

    def _connect_signals(self) -> None:
        self.runtime.frameReady.connect(self._on_frame_ready)
        self.runtime.artifactReady.connect(self._on_artifact_ready)
        self.window.viewer.displayCommitted.connect(self._on_display_committed)
        self.window.viewer.contentPainted.connect(self._on_content_painted)
        self.window.viewer.framePainted.connect(self._on_frame_painted)
        self.window.presentationCommitted.connect(self._on_presentation_committed)

    def _patch_production_boundaries(self) -> None:
        probe = self
        window = self.window
        runtime = self.runtime
        viewer = window.viewer

        original_begin = window._begin_presentation_request

        def begin_request(_window, spread, navigation):
            started = _now_ns()
            result = original_begin(spread, navigation)
            token = getattr(result, "token", None)
            probe._event(
                "presentation_request",
                request_id=int(getattr(token, "request_serial", -1)),
                page_index=int(_window.model.focused_index),
                duration_ns=_now_ns() - started,
            )
            return result

        self._install(window, "_begin_presentation_request", begin_request)

        original_build = window._zip_runtime_request

        def build_request(_window, spread):
            started = _now_ns()
            result = original_build(spread)
            probe._event(
                "raster_request_built",
                request_id=int(getattr(result, "request_id", -1)),
                identity=probe._request_identity(result),
                duration_ns=_now_ns() - started,
            )
            return result

        self._install(window, "_zip_runtime_request", build_request)

        original_lookup = runtime.has_cached_current

        def cache_lookup(_runtime, request):
            started = _now_ns()
            hit = bool(original_lookup(request))
            probe._event(
                "cache_lookup",
                request_id=int(getattr(request, "request_id", -1)),
                identity=probe._request_identity(request),
                hit=hit,
                duration_ns=_now_ns() - started,
            )
            return hit

        self._install(runtime, "has_cached_current", cache_lookup)

        original_runtime_request = runtime.request

        def dispatch_request(_runtime, request):
            started = _now_ns()
            accepted = bool(original_runtime_request(request))
            probe._event(
                "runtime_request",
                request_id=int(getattr(request, "request_id", -1)),
                identity=probe._request_identity(request),
                accepted=accepted,
                duration_ns=_now_ns() - started,
            )
            return accepted

        self._install(runtime, "request", dispatch_request)

        original_submit = getattr(runtime, "_submit", None)
        if callable(original_submit):

            def submit(_runtime, key, priority):
                probe._event(
                    "job_submit",
                    priority=int(priority),
                    identity=tuple(getattr(key, "unit_identity", ())),
                )
                return original_submit(key, priority)

            self._install(runtime, "_submit", submit)

        original_commit = viewer.commit_display_ready_frame

        def commit_frame(_viewer, spread, images, frame_token):
            prepared = tuple(images)
            started = _now_ns()
            result = original_commit(spread, prepared, frame_token)
            probe._event(
                "widget_commit",
                request_id=int(getattr(frame_token, "request_serial", -1)),
                image_ids=tuple(image.image_id for image in prepared),
                duration_ns=_now_ns() - started,
            )
            return result

        self._install(viewer, "commit_display_ready_frame", commit_frame)

        original_apply = window._apply_presentation_commit

        def apply_presentation(_window, commit):
            started = _now_ns()
            result = original_apply(commit)
            token = getattr(getattr(commit, "frame", None), "token", None)
            probe._event(
                "presentation_apply",
                request_id=int(getattr(token, "request_serial", -1)),
                duration_ns=_now_ns() - started,
            )
            return result

        self._install(window, "_apply_presentation_commit", apply_presentation)

        for method_name, label in self._UI_METHODS:
            original = getattr(window, method_name, None)
            if not callable(original):
                continue

            def timed_ui(
                _window,
                *args,
                __original=original,
                __label=label,
                **kwargs,
            ):
                started = _now_ns()
                try:
                    return __original(*args, **kwargs)
                finally:
                    probe._event(
                        "ui_projection",
                        projection=__label,
                        duration_ns=_now_ns() - started,
                    )

            self._install(window, method_name, timed_ui)

        session = window.book_session
        original_notify = session.notify_page_changed

        def notify_page_changed(_session):
            started = _now_ns()
            try:
                return original_notify()
            finally:
                probe._event(
                    "ui_projection",
                    projection="history_progress_metadata",
                    duration_ns=_now_ns() - started,
                )

        self._install(session, "notify_page_changed", notify_page_changed)

    def _on_frame_ready(self, frame: object) -> None:
        self._event(
            "frame_ready",
            request_id=int(getattr(frame, "request_id", -1)),
            cache_hit=bool(getattr(frame, "cache_hit", False)),
            image_ids=tuple(
                str(getattr(page, "image_id", ""))
                for page in getattr(frame, "pages", ())
            ),
        )

    def _on_artifact_ready(self, artifact: object) -> None:
        unit = getattr(artifact, "unit", None)
        self._event(
            "artifact_ready",
            identity=tuple(getattr(unit, "identity", ())),
        )

    def _on_display_committed(self, image_ids: object) -> None:
        self._event("display_committed", image_ids=tuple(image_ids))

    def _on_content_painted(self, image_ids: object) -> None:
        self._event("content_painted", image_ids=tuple(image_ids))

    def _on_frame_painted(self, serial: int, image_ids: object) -> None:
        self._event(
            "frame_painted",
            frame_serial=int(serial),
            image_ids=tuple(image_ids),
        )

    def _on_presentation_committed(self, commit: object) -> None:
        frame = getattr(commit, "frame", None)
        token = getattr(frame, "token", None)
        unit = getattr(frame, "unit", None)
        self._event(
            "presentation_committed",
            request_id=int(getattr(token, "request_serial", -1)),
            page_index=int(getattr(getattr(frame, "values", None), "page_index", -1)),
            image_ids=tuple(
                str(getattr(page, "image_id", ""))
                for page in getattr(unit, "pages", ())
            ),
        )

    def begin(self) -> _ScenarioSnapshot:
        source_counts, decode_offset = self.source_probe.snapshot()
        memory = _process_memory_bytes()
        return _ScenarioSnapshot(
            _now_ns(),
            len(self.events),
            decode_offset,
            asdict(self.runtime.metrics),
            source_counts,
            memory[0] if memory is not None else None,
            memory[1] if memory is not None else None,
        )

    def has_commit(self, request_id: int, *, since_ns: int = 0) -> bool:
        return any(
            event.kind == "presentation_committed"
            and event.at_ns >= int(since_ns)
            and int(event.payload.get("request_id", -1)) == int(request_id)
            for event in self.events
        )

    def commit_time(self, request_id: int, *, since_ns: int = 0) -> int | None:
        return next(
            (
                event.at_ns
                for event in self.events
                if event.kind == "presentation_committed"
                and event.at_ns >= int(since_ns)
                and int(event.payload.get("request_id", -1)) == int(request_id)
            ),
            None,
        )

    def page_commit_event(
        self,
        page_index: int,
        *,
        since_ns: int = 0,
        before_ns: int | None = None,
    ) -> _TraceEvent | None:
        return next(
            (
                event
                for event in self.events
                if event.kind == "presentation_committed"
                and event.at_ns >= int(since_ns)
                and (before_ns is None or event.at_ns < int(before_ns))
                and int(event.payload.get("page_index", -1))
                == int(page_index)
            ),
            None,
        )

    def has_paint(
        self,
        image_id: str,
        *,
        since_ns: int,
    ) -> bool:
        return any(
            event.kind == "frame_painted"
            and event.at_ns >= int(since_ns)
            and str(image_id) in event.payload.get("image_ids", ())
            for event in self.events
        )

    @staticmethod
    def _first_event(
        events: list[_TraceEvent],
        kind: str,
        request_id: int,
        *,
        after_ns: int,
    ) -> _TraceEvent | None:
        return next(
            (
                event
                for event in events
                if event.kind == kind
                and event.at_ns >= int(after_ns)
                and int(event.payload.get("request_id", -1)) == int(request_id)
            ),
            None,
        )

    def finish(
        self,
        snapshot: _ScenarioSnapshot,
        inputs: list[_InputRecord],
        *,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        ended_ns = _now_ns()
        memory = _process_memory_bytes()
        events = self.events[snapshot.event_offset :]
        source_counts, _ = self.source_probe.snapshot()
        decode_events = self.source_probe.events_since(snapshot.decode_offset)
        runtime_delta = _delta(
            asdict(self.runtime.metrics),
            snapshot.runtime_metrics,
        )
        source_delta = _delta(source_counts, snapshot.source_counts)
        event_counts = Counter(event.kind for event in events)

        per_input: list[dict[str, object]] = []
        for input_offset, record in enumerate(inputs):
            next_input_ns = (
                inputs[input_offset + 1].started_ns
                if input_offset + 1 < len(inputs)
                else None
            )
            original_request_event = self._first_event(
                events,
                "presentation_request",
                record.request_id,
                after_ns=record.started_ns,
            )
            commit_event = self._first_event(
                events,
                "presentation_committed",
                record.request_id,
                after_ns=record.started_ns,
            )
            if commit_event is None:
                commit_event = next(
                    (
                        event
                        for event in events
                        if event.kind == "presentation_committed"
                        and event.at_ns >= record.started_ns
                        and (
                            next_input_ns is None
                            or event.at_ns < next_input_ns
                        )
                        and int(event.payload.get("page_index", -1))
                        == record.target_page
                    ),
                    None,
                )
            effective_request_id = (
                int(commit_event.payload.get("request_id", -1))
                if commit_event is not None
                else record.request_id
            )
            request_event = self._first_event(
                events,
                "presentation_request",
                effective_request_id,
                after_ns=record.started_ns,
            )
            if request_event is None:
                request_event = original_request_event
            cache_event = self._first_event(
                events,
                "cache_lookup",
                effective_request_id,
                after_ns=record.started_ns,
            )
            if cache_event is None and effective_request_id != record.request_id:
                cache_event = self._first_event(
                    events,
                    "cache_lookup",
                    record.request_id,
                    after_ns=record.started_ns,
                )
            dispatch_event = self._first_event(
                events,
                "runtime_request",
                effective_request_id,
                after_ns=record.started_ns,
            )
            decode_candidates = [
                event
                for event in decode_events
                if event.image_id == record.image_id
                and event.started_ns >= (
                    request_event.at_ns if request_event is not None else record.started_ns
                )
                and (
                    commit_event is None
                    or event.started_ns <= commit_event.at_ns
                )
            ]
            decode_event = decode_candidates[0] if decode_candidates else None
            completed_decode = next(
                (
                    event
                    for event in reversed(decode_candidates)
                    if event.success
                    and (
                        commit_event is None
                        or event.completed_ns <= commit_event.at_ns
                    )
                ),
                None,
            )
            paint_event = next(
                (
                    event
                    for event in events
                    if event.kind == "frame_painted"
                    and event.at_ns
                    >= (
                        commit_event.at_ns
                        if commit_event is not None
                        else record.started_ns
                    )
                    and record.image_id in event.payload.get("image_ids", ())
                ),
                None,
            )
            request_at = request_event.at_ns if request_event is not None else None
            per_input.append(
                {
                    "kind": record.kind,
                    "request_id": record.request_id,
                    "committed_request_id": (
                        effective_request_id if commit_event is not None else None
                    ),
                    "request_was_superseded_same_page": bool(
                        commit_event is not None
                        and effective_request_id != record.request_id
                    ),
                    "target_page_zero_based": record.target_page,
                    "handler_ms": _ms(record.completed_ns - record.started_ns),
                    "cache": (
                        "hit"
                        if cache_event is not None
                        and bool(cache_event.payload.get("hit", False))
                        else "miss" if cache_event is not None else "not_checked"
                    ),
                    "dispatched": dispatch_event is not None,
                    "committed": commit_event is not None,
                    "painted": paint_event is not None,
                    "input_to_request_ms": (
                        _ms(request_at - record.started_ns)
                        if request_at is not None
                        else None
                    ),
                    "request_to_cache_decision_ms": (
                        _ms(cache_event.at_ns - request_at)
                        if cache_event is not None and request_at is not None
                        else None
                    ),
                    "request_to_runtime_dispatch_ms": (
                        _ms(dispatch_event.at_ns - request_at)
                        if dispatch_event is not None and request_at is not None
                        else None
                    ),
                    "request_to_decode_start_ms": (
                        _ms(decode_event.started_ns - request_at)
                        if decode_event is not None and request_at is not None
                        else None
                    ),
                    "decode_to_commit_ms": (
                        _ms(commit_event.at_ns - completed_decode.completed_ns)
                        if commit_event is not None and completed_decode is not None
                        else None
                    ),
                    "input_to_commit_ms": (
                        _ms(commit_event.at_ns - record.started_ns)
                        if commit_event is not None
                        else None
                    ),
                    "commit_to_paint_ms": (
                        _ms(paint_event.at_ns - commit_event.at_ns)
                        if paint_event is not None and commit_event is not None
                        else None
                    ),
                    "input_to_paint_ms": (
                        _ms(paint_event.at_ns - record.started_ns)
                        if paint_event is not None
                        else None
                    ),
                }
            )

        lookup_events = [event for event in events if event.kind == "cache_lookup"]
        lookup_hits = sum(bool(event.payload.get("hit", False)) for event in lookup_events)
        successful_decodes = [event for event in decode_events if event.success]
        committed_image_ids = {
            str(image_id)
            for event in events
            if event.kind == "presentation_committed"
            for image_id in event.payload.get("image_ids", ())
        }
        final_input = inputs[-1] if inputs else None
        final_commit_event = (
            self.page_commit_event(
                final_input.target_page,
                since_ns=final_input.started_ns,
            )
            if final_input is not None
            else None
        )
        final_commit_ns = (
            final_commit_event.at_ns if final_commit_event is not None else None
        )
        ui_durations: dict[str, list[float]] = {}
        for event in events:
            if event.kind != "ui_projection":
                continue
            label = str(event.payload.get("projection", "unknown"))
            ui_durations.setdefault(label, []).append(
                float(event.payload.get("duration_ns", 0)) / 1_000_000.0
            )
        presentation_apply = [
            float(event.payload.get("duration_ns", 0)) / 1_000_000.0
            for event in events
            if event.kind == "presentation_apply"
        ]
        widget_commit = [
            float(event.payload.get("duration_ns", 0)) / 1_000_000.0
            for event in events
            if event.kind == "widget_commit"
        ]
        critical_events = (
            [
                event
                for event in events
                if final_commit_ns is not None
                and event.at_ns <= final_commit_ns
            ]
            if final_commit_ns is not None
            else []
        )
        critical_decode_events = (
            [
                event
                for event in decode_events
                if event.started_ns <= final_commit_ns
            ]
            if final_commit_ns is not None
            else []
        )

        latency_names = (
            "handler_ms",
            "input_to_request_ms",
            "request_to_cache_decision_ms",
            "request_to_runtime_dispatch_ms",
            "request_to_decode_start_ms",
            "decode_to_commit_ms",
            "input_to_commit_ms",
            "commit_to_paint_ms",
            "input_to_paint_ms",
        )
        return {
            "metadata": dict(metadata or {}),
            "elapsed_ms": _ms(ended_ns - snapshot.started_ns),
            "input_count": len(inputs),
            "per_input": per_input,
            "latency_summary_ms": {
                name: _summary(item.get(name) for item in per_input)
                for name in latency_names
            },
            "final_input": per_input[-1] if per_input else None,
            "critical_path_until_final_commit": {
                "jobs_submitted": sum(
                    event.kind == "job_submit" for event in critical_events
                ),
                "worker_result_callbacks": sum(
                    event.kind == "artifact_ready" for event in critical_events
                ),
                "frame_ready_callbacks": sum(
                    event.kind == "frame_ready" for event in critical_events
                ),
                "decode_attempts": len(critical_decode_events),
                "successful_decodes": sum(
                    event.success for event in critical_decode_events
                ),
            },
            "work": {
                "jobs_submitted": runtime_delta.get("jobs_submitted", 0),
                "queued_worker_callbacks": runtime_delta.get("queued_callbacks", 0),
                "cancel_requests": runtime_delta.get("cancel_requests", 0),
                "stale_results": runtime_delta.get("stale_results", 0),
                "job_submit_events": int(event_counts["job_submit"]),
            },
            "source": {
                "read_calls": source_delta.get("read_calls", 0),
                "read_bytes": source_delta.get("read_bytes", 0),
                "read_chunks": source_delta.get("read_chunks", 0),
                "read_failures": source_delta.get("read_failures", 0),
                "decode_attempts": source_delta.get("decode_attempts", 0),
                "decode_successes": source_delta.get("decode_successes", 0),
                "decode_failures": source_delta.get("decode_failures", 0),
                "decode_image_ids": [event.image_id for event in successful_decodes],
                "decode_outputs": [
                    {
                        "image_id": event.image_id,
                        "backend": event.backend,
                        "width": event.output_width,
                        "height": event.output_height,
                    }
                    for event in successful_decodes
                ],
                "unique_decoded_pages": len(
                    {event.image_id for event in successful_decodes}
                ),
                "duplicate_decode_calls": max(
                    0,
                    len(successful_decodes)
                    - len({event.image_id for event in successful_decodes}),
                ),
            },
            "cache": {
                "lookup_calls": len(lookup_events),
                "lookup_hits": lookup_hits,
                "lookup_misses": len(lookup_events) - lookup_hits,
                "lookup_hit_rate": (
                    round(lookup_hits / len(lookup_events), 4)
                    if lookup_events
                    else None
                ),
                "runtime_cache_hits": runtime_delta.get("cache_hits", 0),
                "runtime_cache_misses": runtime_delta.get("cache_misses", 0),
                "source_cache_hits": runtime_delta.get("source_cache_hits", 0),
                "source_cache_misses": runtime_delta.get("source_cache_misses", 0),
                "evictions": runtime_delta.get("cache_evictions", 0),
                "source_evictions": runtime_delta.get(
                    "source_cache_evictions", 0
                ),
                "retained_page_indexes_zero_based": sorted(
                    set(self.runtime.cached_page_indexes)
                ),
                "retained_frame_pages": len(
                    set(self.runtime.cached_page_indexes)
                ),
                "decoded_source_pages": self.runtime.decoded_source_count,
                "retained_bytes": self.runtime.cache_bytes,
            },
            "gui": {
                "frame_ready_callbacks": int(event_counts["frame_ready"]),
                "artifact_ready_callbacks": int(event_counts["artifact_ready"]),
                "display_commits": int(event_counts["display_committed"]),
                "presentation_commits": int(
                    event_counts["presentation_committed"]
                ),
                "content_paints": int(event_counts["content_painted"]),
                "frame_paints": int(event_counts["frame_painted"]),
                "qpixmap_from_image": runtime_delta.get("qpixmap_creations", 0),
                "widget_commit_ms": _summary(widget_commit),
                "presentation_apply_ms": _summary(presentation_apply),
                "projection_ms": {
                    label: _summary(values)
                    for label, values in sorted(ui_durations.items())
                },
            },
            "waste": {
                "decoded_but_never_committed": sum(
                    event.image_id not in committed_image_ids
                    for event in successful_decodes
                ),
                "decode_started_before_final_commit": (
                    sum(
                        event.started_ns < final_commit_ns
                        for event in successful_decodes
                    )
                    if final_commit_ns is not None
                    else None
                ),
                "decode_started_after_final_commit": (
                    sum(
                        event.started_ns >= final_commit_ns
                        for event in successful_decodes
                    )
                    if final_commit_ns is not None
                    else None
                ),
            },
            "runtime_metrics": runtime_delta,
            "memory": {
                "working_set_start_mib": _mib(snapshot.working_set_start),
                "working_set_end_mib": _mib(
                    memory[0] if memory is not None else None
                ),
                "working_set_delta_mib": (
                    _mib(memory[0] - snapshot.working_set_start)
                    if memory is not None
                    and snapshot.working_set_start is not None
                    else None
                ),
                "process_peak_start_mib": _mib(snapshot.process_peak_start),
                "process_peak_end_mib": _mib(
                    memory[1] if memory is not None else None
                ),
            },
        }

    def close(self) -> None:
        for signal, callback in (
            (self.runtime.frameReady, self._on_frame_ready),
            (self.runtime.artifactReady, self._on_artifact_ready),
            (self.window.viewer.displayCommitted, self._on_display_committed),
            (self.window.viewer.contentPainted, self._on_content_painted),
            (self.window.viewer.framePainted, self._on_frame_painted),
            (self.window.presentationCommitted, self._on_presentation_committed),
        ):
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        for owner, name, had_value, previous in reversed(self._installed):
            namespace = getattr(owner, "__dict__", None)
            if not isinstance(namespace, dict):
                continue
            if had_value:
                setattr(owner, name, previous)
            else:
                namespace.pop(name, None)
        self._installed.clear()
        self.source_probe.close()


def _pump_for(
    application: QApplication,
    duration_ms: float,
) -> None:
    deadline = _now_ns() + max(0, round(float(duration_ms) * 1_000_000))
    while _now_ns() < deadline:
        application.processEvents()
        time.sleep(0.0005)


def _pump_until(
    application: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout: float,
) -> None:
    deadline = _now_ns() + max(1, round(float(timeout) * 1_000_000_000))
    while _now_ns() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.0005)
    raise TimeoutError("offscreen raster navigation benchmark timed out")


def _render_once(window: ViewerWindow) -> None:
    target = QPixmap(window.viewer.size())
    target.fill()
    window.viewer.render(target)


class _Driver:
    def __init__(
        self,
        application: QApplication,
        window: ViewerWindow,
        probe: _NavigationProbe,
        *,
        timeout: float,
        page_sizes: tuple[tuple[int, int], ...],
        cache_units: int,
    ) -> None:
        self.application = application
        self.window = window
        self.probe = probe
        self.runtime = probe.runtime
        self.timeout = float(timeout)
        self.page_sizes = page_sizes
        self.cache_units = int(cache_units)
        self._synthetic_wheel_timestamp_ms = 100_000

    def _quiet(self) -> bool:
        request_timer = getattr(self.window, "_zip_runtime_request_timer", None)
        pending = getattr(self.window, "_pending_zip_runtime_request", None)
        return bool(
            not self.runtime.has_unfinished_tasks()
            and self.runtime.active_job_count == 0
            and pending is None
            and not (
                request_timer is not None
                and callable(getattr(request_timer, "isActive", None))
                and request_timer.isActive()
            )
        )

    def wait_quiet(self) -> None:
        stable_since: int | None = None
        deadline = _now_ns() + round(self.timeout * 1_000_000_000)
        while _now_ns() < deadline:
            self.application.processEvents()
            if self._quiet():
                now = _now_ns()
                if stable_since is None:
                    stable_since = now
                elif now - stable_since >= 5_000_000:
                    return
            else:
                stable_since = None
            time.sleep(0.0005)
        raise TimeoutError("RasterBookRuntime did not become quiet")

    def _record_action(
        self,
        kind: str,
        action: Callable[[], object],
    ) -> _InputRecord:
        before_page = int(self.window.model.focused_index)
        started = _now_ns()
        action()
        completed = _now_ns()
        target_page = int(self.window.model.focused_index)
        if target_page == before_page:
            raise AssertionError(
                f"{kind} did not move from page {before_page}"
            )
        image_id = self.window.model.image_id_at(target_page)
        if image_id is None:
            raise AssertionError(f"page {target_page} has no image identity")
        return _InputRecord(
            kind,
            int(self.window._active_request_id),
            target_page,
            str(image_id),
            started,
            completed,
        )

    def wheel(
        self,
        direction: int,
        *,
        timestamp_ms: int | None = None,
    ) -> _InputRecord:
        angle_y = -120 if int(direction) > 0 else 120

        def action() -> None:
            event = QWheelEvent(
                QPointF(10, 10),
                QPointF(self.window.viewer.mapToGlobal(QPoint(10, 10))),
                QPoint(0, 0),
                QPoint(0, angle_y),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.ScrollUpdate,
                False,
            )
            if timestamp_ms is not None:
                event.setTimestamp(int(timestamp_ms))
            QApplication.sendEvent(self.window.viewer, event)
            if not event.isAccepted():
                raise AssertionError("Viewer wheel input was not accepted")

        return self._record_action(
            "wheel_next" if direction > 0 else "wheel_previous",
            action,
        )

    def jump(self, page: int, *, kind: str = "page_jump") -> _InputRecord:
        return self._record_action(
            kind,
            lambda: self.window._go_to_index_with_history(int(page)),
        )

    def wait_input(self, record: _InputRecord) -> None:
        try:
            _pump_until(
                self.application,
                lambda: (
                    self.window.presentation_state.displayed_page
                    == record.target_page
                    and self.probe.page_commit_event(
                        record.target_page,
                        since_ns=record.started_ns,
                    ) is not None
                ),
                timeout=self.timeout,
            )
        except TimeoutError as exc:
            requested = self.window.presentation_state.requested
            pending = getattr(self.window, "_pending_zip_runtime_request", None)
            timer = getattr(self.window, "_zip_runtime_request_timer", None)
            recent = [
                {
                    "kind": event.kind,
                    "request_id": event.payload.get("request_id"),
                    "page_index": event.payload.get("page_index"),
                    "identity": event.payload.get("identity"),
                }
                for event in self.probe.events[-12:]
            ]
            raise TimeoutError(
                "offscreen input did not commit: "
                f"record={record!r}, focused={self.window.model.focused_index}, "
                f"displayed={self.window.presentation_state.displayed_page}, "
                f"requested_serial="
                f"{getattr(getattr(requested, 'token', None), 'request_serial', None)}, "
                f"active_request={self.window._active_request_id}, "
                f"pending_request={getattr(pending, 'request_id', None)}, "
                f"timer_active={bool(timer is not None and timer.isActive())}, "
                f"unfinished={self.runtime.has_unfinished_tasks()}, "
                f"active_jobs={self.runtime.active_job_count}, "
                f"recent_events={recent!r}"
            ) from exc
        commit_event = self.probe.page_commit_event(
            record.target_page,
            since_ns=record.started_ns,
        )
        if commit_event is None:
            raise AssertionError("target committed without a trace boundary")
        commit_ns = commit_event.at_ns
        _render_once(self.window)
        self.application.processEvents()
        _pump_until(
            self.application,
            lambda: self.probe.has_paint(
                record.image_id,
                since_ns=commit_ns,
            ),
            timeout=self.timeout,
        )

    def ensure_page(self, page: int) -> None:
        page = int(page)
        if (
            self.window.model.focused_index == page
            and self.window.presentation_state.displayed_page == page
        ):
            self.wait_quiet()
            return
        if self.window.model.focused_index == page:
            started = _now_ns()
            self.window._refresh_view()
            completed = _now_ns()
            request_id = int(self.window._active_request_id)
            image_id = self.window.model.image_id_at(page)
            assert image_id is not None
            setup = _InputRecord(
                "setup_refresh",
                request_id,
                page,
                str(image_id),
                started,
                completed,
            )
        else:
            setup = self.jump(page, kind="setup_jump")
        self.wait_input(setup)
        self.wait_quiet()

    def prepare_cold(self, anchor: int) -> None:
        self.ensure_page(anchor)
        self.runtime.cancel(clear_artifacts=True)
        self.application.processEvents()
        self.wait_quiet()
        # A deferred same-page refresh can legally run while the first clear
        # drains.  Once Runtime is quiet and Window has no pending demand,
        # perform the final synchronous clear that defines this scenario's
        # cold boundary.
        self.runtime.cancel(clear_artifacts=True)
        # The anchor move is fixture setup, not part of the measured user's
        # input burst. Start each cold scenario from an idle input sequence.
        self.window._clear_pending_raster_navigation(reset_policy=True)
        if self.runtime.cached_unit_count or self.runtime.decoded_source_count:
            raise AssertionError("cold reset retained runtime artifacts")

    def prewarm_pair(self, first: int, second: int) -> None:
        self.ensure_page(first)
        if second not in set(self.runtime.cached_page_indexes):
            moved = self.jump(second, kind="setup_pair")
            self.wait_input(moved)
            self.wait_quiet()
            moved = self.jump(first, kind="setup_pair_return")
            self.wait_input(moved)
            self.wait_quiet()
        if second not in set(self.runtime.cached_page_indexes):
            raise AssertionError("ready-pair setup did not retain the neighbor")

    def finish(
        self,
        snapshot: _ScenarioSnapshot,
        inputs: list[_InputRecord],
        *,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.wait_quiet()
        details = dict(metadata or {})
        details["target_page_sizes"] = [
            list(self.page_sizes[item.target_page]) for item in inputs
        ]
        return self.probe.finish(snapshot, inputs, metadata=details)

    def ready(self) -> dict[str, object]:
        self.prewarm_pair(2, 3)
        snapshot = self.probe.begin()
        item = self.wheel(1)
        self.wait_input(item)
        return self.finish(snapshot, [item])

    def forward_10(self, *, anchor: int = 0) -> dict[str, object]:
        self.prepare_cold(anchor)
        snapshot = self.probe.begin()
        inputs: list[_InputRecord] = []
        for _ in range(10):
            item = self.wheel(1)
            inputs.append(item)
            self.wait_input(item)
            self.wait_quiet()
        return self.finish(snapshot, inputs)

    def paced_wheel(self, interval_ms: int) -> dict[str, object]:
        self.prepare_cold(0)
        snapshot = self.probe.begin()
        inputs: list[_InputRecord] = []
        timestamp_base = self._synthetic_wheel_timestamp_ms
        for index in range(12):
            item = self.wheel(
                1,
                timestamp_ms=timestamp_base + index * int(interval_ms),
            )
            inputs.append(item)
            if interval_ms > 0:
                _pump_for(self.application, interval_ms)
        self._synthetic_wheel_timestamp_ms = (
            timestamp_base + max(1, 12 * int(interval_ms)) + 100
        )
        self.wait_input(inputs[-1])
        return self.finish(
            snapshot,
            inputs,
            metadata={"inter_input_event_pump_ms": int(interval_ms)},
        )

    def cold_single_wheel(self) -> dict[str, object]:
        self.prepare_cold(0)
        snapshot = self.probe.begin()
        item = self.wheel(1)
        self.wait_input(item)
        return self.finish(snapshot, [item])

    def reversal(self) -> dict[str, object]:
        self.prepare_cold(8)
        snapshot = self.probe.begin()
        first = self.wheel(1)
        _pump_until(
            self.application,
            lambda: any(
                event.image_id == first.image_id
                for event in self.probe.source_probe.events_since(
                    snapshot.decode_offset
                )
            )
            or self.runtime.metrics.jobs_submitted
            > snapshot.runtime_metrics.get("jobs_submitted", 0),
            timeout=self.timeout,
        )
        back_to_anchor = self.wheel(-1)
        final = self.wheel(-1)
        self.wait_input(final)
        return self.finish(
            snapshot,
            [first, back_to_anchor, final],
            metadata={"reversed_after_first_job_admitted": True},
        )

    def two_page_roundtrip(self) -> dict[str, object]:
        self.prewarm_pair(5, 6)
        snapshot = self.probe.begin()
        inputs: list[_InputRecord] = []
        for direction in (1, -1) * 5:
            item = self.wheel(direction)
            inputs.append(item)
            self.wait_input(item)
        return self.finish(snapshot, inputs)

    def outside_cache_return(self) -> dict[str, object]:
        self.prepare_cold(0)
        snapshot = self.probe.begin()
        inputs: list[_InputRecord] = []
        walk_count = min(10, len(self.page_sizes) - 2)
        for _ in range(walk_count):
            item = self.wheel(1)
            inputs.append(item)
            self.wait_input(item)
            self.wait_quiet()
        return_page = 1
        cached_before = return_page in set(self.runtime.cached_page_indexes)
        returned = self.jump(return_page, kind="outside_cache_return")
        inputs.append(returned)
        self.wait_input(returned)
        return self.finish(
            snapshot,
            inputs,
            metadata={
                "configured_unit_limit": self.cache_units,
                "walked_pages": walk_count,
                "return_page_zero_based": return_page,
                "return_frame_cached_before_request": cached_before,
            },
        )

    def mixed_size_direction_changes(self) -> dict[str, object]:
        self.prepare_cold(12)
        snapshot = self.probe.begin()
        inputs: list[_InputRecord] = []
        for direction in (1, 1, 1, 1, 1, -1, -1, 1, 1, 1):
            item = self.wheel(direction)
            inputs.append(item)
            self.wait_input(item)
            self.wait_quiet()
        return self.finish(
            snapshot,
            inputs,
            metadata={"alternating_small_large_fixture": True},
        )


def _aggregate(scenarios: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
        "scenario_count": len(scenarios),
        "elapsed_ms": round(
            sum(float(scenario.get("elapsed_ms") or 0.0) for scenario in scenarios.values()),
            3,
        ),
        "inputs": sum(int(scenario["input_count"]) for scenario in scenarios.values()),
        "jobs": sum(int(scenario["work"]["jobs_submitted"]) for scenario in scenarios.values()),
        "queued_worker_callbacks": sum(
            int(scenario["work"]["queued_worker_callbacks"])
            for scenario in scenarios.values()
        ),
        "reads": sum(int(scenario["source"]["read_calls"]) for scenario in scenarios.values()),
        "decode_attempts": sum(
            int(scenario["source"]["decode_attempts"])
            for scenario in scenarios.values()
        ),
        "decode_successes": sum(
            int(scenario["source"]["decode_successes"])
            for scenario in scenarios.values()
        ),
        "stale_results": sum(
            int(scenario["work"]["stale_results"])
            for scenario in scenarios.values()
        ),
        "cancel_requests": sum(
            int(scenario["work"]["cancel_requests"])
            for scenario in scenarios.values()
        ),
        "cache_lookup_hits": sum(
            int(scenario["cache"]["lookup_hits"])
            for scenario in scenarios.values()
        ),
        "cache_lookup_misses": sum(
            int(scenario["cache"]["lookup_misses"])
            for scenario in scenarios.values()
        ),
        "evictions": sum(
            int(scenario["cache"]["evictions"])
            for scenario in scenarios.values()
        ),
    }


def _run_worker(args: argparse.Namespace) -> dict[str, object]:
    manifest = json.loads(
        Path(args.fixture_manifest).read_text(encoding="utf-8")
    )
    source_kind = str(args.worker_source)
    source_path = Path(manifest["paths"][source_kind])
    page_sizes = tuple(
        (int(size[0]), int(size[1])) for size in manifest["page_sizes"]
    )
    application = QApplication.instance() or QApplication([])
    config_path = Path(args.fixture_manifest).parent / f"config-{source_kind}.json"
    config = ConfigManager(config_path)
    config.load()
    config.apply(
        {
            "view_mode": "single",
            "single_first_page": False,
            "fit_mode": "fit_window",
            "viewer_resampling_mode": "standard",
            "viewer_cache_max_memory_mib": int(args.cache_mib),
            "cache_size": int(args.cache_units),
            "show_page_list": False,
        },
        save=True,
    )
    coordinator = ImageWorkCoordinator(max_workers=1)
    window = ViewerWindow(
        config_manager=config,
        image_work_coordinator=coordinator,
    )
    probe: _NavigationProbe | None = None
    shutdown: dict[str, object] = {}
    admission_mode = str(args.worker_admission)
    try:
        window.resize(int(args.viewport_width), int(args.viewport_height))
        if not window.open_path(source_path):
            raise AssertionError(f"Viewer rejected {source_path}")
        if not window.book_session.wait_for_async(round(float(args.timeout) * 1000)):
            raise TimeoutError("book source preparation timed out")
        _pump_until(
            application,
            lambda: window.model.total_pages >= 24,
            timeout=float(args.timeout),
        )
        _pump_until(
            application,
            lambda: window.presentation_state.displayed_page == 0,
            timeout=float(args.timeout),
        )
        _render_once(window)
        application.processEvents()
        runtime = window.book_session.viewer_runtime
        source = window.book_session.source
        if not isinstance(runtime, RasterBookRuntime):
            raise AssertionError("production raster runtime is absent")
        if source_kind == "zip" and not isinstance(source, ZipImageSource):
            raise AssertionError("ZIP child did not open ZipImageSource")
        if source_kind == "folder" and not isinstance(source, FolderImageSource):
            raise AssertionError("Folder child did not open FolderImageSource")
        if source_kind == "zip" and not runtime.__class__.__name__.startswith("Zip"):
            raise AssertionError("ZIP child did not select ZipRasterBookRuntime")
        if source_kind == "folder" and not isinstance(
            runtime, FolderRasterBookRuntime
        ):
            raise AssertionError("Folder child did not select FolderRasterBookRuntime")
        probe = _NavigationProbe(window)
        driver = _Driver(
            application,
            window,
            probe,
            timeout=float(args.timeout),
            page_sizes=page_sizes,
            cache_units=int(args.cache_units),
        )
        driver.wait_quiet()
        worker_memory_start = _process_memory_bytes()
        if args.minimal:
            scenarios = {
                "cold_single_wheel": driver.cold_single_wheel(),
                "ready_hit": driver.ready(),
                "paced_wheel_12_8ms": driver.paced_wheel(8),
                "direction_reversal": driver.reversal(),
            }
        else:
            scenarios = {
                "cold_single_wheel": driver.cold_single_wheel(),
                "ready_hit": driver.ready(),
                "forward_10": driver.forward_10(),
                "paced_wheel_12_0ms": driver.paced_wheel(0),
                "paced_wheel_12_4ms": driver.paced_wheel(4),
                "paced_wheel_12_8ms": driver.paced_wheel(8),
                "direction_reversal": driver.reversal(),
                "two_page_roundtrip": driver.two_page_roundtrip(),
                "outside_cache_return": driver.outside_cache_return(),
                "mixed_size_direction_changes": driver.mixed_size_direction_changes(),
            }
        worker_memory_end = _process_memory_bytes()
        return {
            "source_kind": source_kind,
            "admission_mode": admission_mode,
            "path": f"ViewerWindow -> {runtime.__class__.__name__}",
            "scenarios": scenarios,
            "totals": _aggregate(scenarios),
            "memory": {
                "working_set_start_mib": _mib(
                    worker_memory_start[0]
                    if worker_memory_start is not None
                    else None
                ),
                "working_set_end_mib": _mib(
                    worker_memory_end[0]
                    if worker_memory_end is not None
                    else None
                ),
                "working_set_delta_mib": (
                    _mib(worker_memory_end[0] - worker_memory_start[0])
                    if worker_memory_start is not None
                    and worker_memory_end is not None
                    else None
                ),
                "process_peak_start_mib": _mib(
                    worker_memory_start[1]
                    if worker_memory_start is not None
                    else None
                ),
                "process_peak_end_mib": _mib(
                    worker_memory_end[1]
                    if worker_memory_end is not None
                    else None
                ),
                "process_peak_growth_mib": (
                    _mib(worker_memory_end[1] - worker_memory_start[1])
                    if worker_memory_start is not None
                    and worker_memory_end is not None
                    else None
                ),
            },
            "shutdown": shutdown,
        }
    finally:
        started = _now_ns()
        window.prepare_shutdown(wait_msecs=10_000)
        if probe is not None:
            probe.close()
        window.close()
        application.processEvents()
        coordinator_complete = coordinator.shutdown(wait_msecs=10_000)
        application.processEvents()
        shutdown.update(
            {
                "elapsed_ms": _ms(_now_ns() - started),
                "coordinator_complete": bool(coordinator_complete),
                "viewer_runtime_tasks_remaining": (
                    bool(
                        window.book_session.viewer_runtime
                        and window.book_session.viewer_runtime.has_unfinished_tasks()
                    )
                    if window.book_session.viewer_runtime is not None
                    else False
                ),
            }
        )


def _worker_command(
    args: argparse.Namespace,
    source_kind: str,
    admission_mode: str,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-source",
        source_kind,
        "--worker-admission",
        admission_mode,
        "--fixture-manifest",
        str(args.fixture_manifest),
        "--viewport-width",
        str(args.viewport_width),
        "--viewport-height",
        str(args.viewport_height),
        "--cache-mib",
        str(args.cache_mib),
        "--cache-units",
        str(args.cache_units),
        "--timeout",
        str(args.timeout),
    ]
    if args.minimal:
        command.append("--minimal")
    return command


def _run_isolated(
    args: argparse.Namespace,
    source_kind: str,
    admission_mode: str,
) -> dict[str, object]:
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        _worker_command(args, source_kind, admission_mode),
        cwd=_REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=max(180.0, float(args.timeout) * 20.0),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{source_kind}/{admission_mode} child failed with exit code "
            f"{completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{source_kind}/{admission_mode} child returned invalid JSON\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        ) from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=24)
    parser.add_argument("--small-width", type=int, default=900)
    parser.add_argument("--small-height", type=int, default=1350)
    parser.add_argument("--large-width", type=int, default=2400)
    parser.add_argument("--large-height", type=int, default=3600)
    parser.add_argument("--viewport-width", type=int, default=1200)
    parser.add_argument("--viewport-height", type=int, default=800)
    parser.add_argument("--cache-mib", type=int, default=256)
    parser.add_argument("--cache-units", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--solid", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="run only cold-single, ready, 8 ms wheel, and reversal",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--worker-source",
        choices=_SOURCE_KINDS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--worker-admission",
        choices=_ADMISSION_MODES,
        default="current",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--fixture-manifest",
        type=Path,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if args.quick and args.worker_source is None:
        args.small_width = 320
        args.small_height = 480
        args.large_width = 900
        args.large_height = 1350
        args.viewport_width = 640
        args.viewport_height = 420
        args.cache_mib = 64
    if args.pages < 24:
        parser.error("--pages must be at least 24")
    for name in (
        "small_width",
        "small_height",
        "large_width",
        "large_height",
        "viewport_width",
        "viewport_height",
        "cache_mib",
        "cache_units",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.worker_source is not None and args.fixture_manifest is None:
        parser.error("--fixture-manifest is required for a worker")
    return args


def main() -> int:
    args = _parse_args()
    if args.worker_source is not None:
        print(json.dumps(_run_worker(args), ensure_ascii=False, sort_keys=True))
        return 0

    with TemporaryDirectory(prefix="nivis-raster-navigation-critical-") as temporary:
        root = Path(temporary)
        fixture = _build_fixture(
            root,
            pages=int(args.pages),
            small_size=(int(args.small_width), int(args.small_height)),
            large_size=(int(args.large_width), int(args.large_height)),
            detail=not bool(args.solid),
        )
        manifest_path = root / "fixture.json"
        manifest_path.write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        args.fixture_manifest = manifest_path
        variants = {
            admission_mode: {
                source_kind: _run_isolated(
                    args,
                    source_kind,
                    admission_mode,
                )
                for source_kind in _SOURCE_KINDS
            }
            for admission_mode in _ADMISSION_MODES
        }
        report = {
            "schema_version": 2,
            "benchmark": "raster-navigation-critical-path",
            "clock": "time.perf_counter_ns",
            "qt_platform": os.environ.get("QT_QPA_PLATFORM"),
            "fixture": {
                "temporary_directory": True,
                "pages": fixture["pages"],
                "page_sizes": fixture["page_sizes"],
                "small_size": fixture["small_size"],
                "large_size": fixture["large_size"],
                "payload_bytes": fixture["payload_bytes"],
                "detail": fixture["detail"],
                "viewport": [
                    int(args.viewport_width),
                    int(args.viewport_height),
                ],
                "cache_mib": int(args.cache_mib),
                "cache_units": int(args.cache_units),
            },
            "measurement_scope": {
                "input": (
                    "offscreen QWheelEvent through ViewerWidget, except the "
                    "single outside-cache return jump"
                ),
                "request": "ViewerPresentationState request completion",
                "cache_decision": "RasterBookRuntime.has_cached_current return",
                "decode": "application-visible ImageSource decoder call",
                "commit": (
                    "presentationCommitted after atomic slider/status projection; "
                    "page-list/history/persistence projection follows paint"
                ),
                "paint": "framePainted after explicit offscreen QWidget.render",
                "read": (
                    "ZIP entry materialization or Folder decoder file-open call; "
                    "native plugin-internal reads are not observable"
                ),
                "post_paint_prefetch": (
                    "scenario counts intentionally include work released by the "
                    "final paint; waste fields split it around final commit"
                ),
            },
            "ab_variants": {
                "current": (
                    "production input-kind admission: discrete/leading input "
                    "dispatches immediately; only rapid wheel or key-repeat "
                    "bursts stage"
                ),
            },
            "variants": variants,
            # Convenience alias for existing one-variant report readers.
            "workers": variants["current"],
        }

    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output is not None:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
