"""Offscreen A/B benchmark for folder-backed Viewer production paths.

Case A wraps the real ``FolderImageSource`` in a benchmark-only generic
``ImageSource`` adapter.  That deliberately makes the Viewer use the retained
non-folder ``ImageCache -> ViewerRenderTask -> prepared display`` path without
adding a production feature flag.  Case B passes the same folder source to the
normal production selector and requires ``FolderRasterBookRuntime``.

Both cases run in fresh child processes, use the same temporary JPEG files,
never show a window, never synthesize native input, and explicitly drain only
the Viewer/coordinator work they create.  Counts are application-visible; Qt
plugin-internal copies and native decoder allocations are outside Python's
observable boundary and are labelled as such in the report.
"""

from __future__ import annotations

import argparse
import atexit
from collections import Counter
from dataclasses import asdict, dataclass
import ctypes
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
from types import MethodType
from typing import Any, Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from PIL import Image, ImageOps

# Codex's bundled validation Python supplies a matching Pillow build while the
# repository environment supplies Qt.  Import Pillow first so adding that Qt
# path cannot select the repository's different-minor-version Pillow binary.
_EXTRA_SITE_PACKAGES = os.environ.get("NIVIS_BENCHMARK_EXTRA_SITE_PACKAGES")
if _EXTRA_SITE_PACKAGES and _EXTRA_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, _EXTRA_SITE_PACKAGES)

from PySide6.QtCore import QEvent, QRectF
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

import app.image_cache as image_cache_module
import app.image_source as image_source_module
import app.thumbnail_render as thumbnail_render_module
import app.viewer_render as viewer_render_module
import app.zip_raster_book_runtime as runtime_module
from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.image_source import FolderImageSource, ImageSource
from app.image_work_coordinator import ImageWorkCoordinator
from app.raster_book_runtime import RasterBookRuntime, RasterFrame
from app.viewer_window import ViewerWindow


_MIB = 1024 * 1024
_CASE_A = "A-legacy-two-stage"
_CASE_B = "B-folder-runtime"


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


def _write_folder(
    folder: Path,
    sizes: tuple[tuple[int, int], ...],
    payloads: dict[tuple[int, int], bytes],
) -> tuple[str, ...]:
    folder.mkdir(parents=True, exist_ok=True)
    image_ids: list[str] = []
    for index, size in enumerate(sizes):
        path = folder / f"日本語ページ-{index:03d}.jpg"
        path.write_bytes(payloads[size])
        image_ids.append(str(path))
    return tuple(image_ids)


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
    small = _write_folder(
        root / "小画像ブック",
        (small_size,) * pages,
        payloads,
    )
    large = _write_folder(
        root / "大画像ブック",
        (large_size,) * pages,
        payloads,
    )
    mixed_sizes = tuple(
        large_size if index % 2 else small_size for index in range(pages)
    )
    mixed = _write_folder(root / "大小混在ブック", mixed_sizes, payloads)
    single = _write_folder(root / "単一画像ブック", (large_size,), payloads)
    replacement = _write_folder(
        root / "切替先ブック",
        (small_size, large_size),
        payloads,
    )
    return {
        "folders": {
            "small": str(Path(small[0]).parent),
            "large": str(Path(large[0]).parent),
            "mixed": str(Path(mixed[0]).parent),
            "single": str(Path(single[0]).parent),
            "replacement": str(Path(replacement[0]).parent),
        },
        "image_ids": {
            "small": list(small),
            "large": list(large),
            "mixed": list(mixed),
            "single": list(single),
            "replacement": list(replacement),
        },
        "payload_bytes": {
            f"{small_size[0]}x{small_size[1]}": len(payloads[small_size]),
            f"{large_size[0]}x{large_size[1]}": len(payloads[large_size]),
        },
        "small_size": list(small_size),
        "large_size": list(large_size),
        "pages_per_navigation_book": pages,
    }


def _process_memory_bytes() -> tuple[int, int] | None:
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


class _SourceTracker:
    """Thread-safe application-visible file/decode accounting."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counts: Counter[str] = Counter()
        self.decode_events: list[tuple[float, str, str, str]] = []
        self.sources: list[_CountingFolderSource] = []
        self._original_read = image_source_module._read_image_file_bytes
        tracker = self

        def counted_read(path: str | Path) -> bytes:
            data = tracker._original_read(path)
            with tracker.lock:
                tracker.counts["file_read_calls"] += 1
                tracker.counts["file_read_bytes"] += len(data)
                # The returned Python bytes object is the first complete
                # application-visible payload materialization.
                tracker.counts["full_payload_copy_calls"] += 1
                tracker.counts["full_payload_copy_bytes"] += len(data)
            return data

        image_source_module._read_image_file_bytes = counted_read

    def register(self, source: "_CountingFolderSource") -> None:
        with self.lock:
            self.sources.append(source)

    def record_attempt(self, image_id: str, api: str) -> None:
        with self.lock:
            self.counts["decode_api_attempts"] += 1
            self.counts[f"decode_attempt_{api}"] += 1

    def record_success(self, image_id: str, api: str, kind: str) -> None:
        size = 0
        try:
            size = Path(image_id).stat().st_size
        except OSError:
            pass
        # Every current Folder decode API creates exactly one observable
        # whole-compressed-payload handoff after read_bytes(): a BytesIO for
        # Pillow JPEG/full decode or a QByteArray for WebP.  Pixel conversion
        # into QImage is counted separately and is not a compressed-byte copy.
        handoffs = 1
        now = time.monotonic()
        with self.lock:
            self.counts["decode_calls"] += 1
            self.counts[f"decode_success_{api}"] += 1
            self.counts[f"source_{kind}_outputs"] += 1
            self.counts["full_payload_copy_calls"] += handoffs
            self.counts["full_payload_copy_bytes"] += size * handoffs
            self.decode_events.append((now, str(image_id), api, kind))

    def record_failure(self, api: str) -> None:
        with self.lock:
            self.counts["decode_failures"] += 1
            self.counts[f"decode_failure_{api}"] += 1

    def snapshot(self) -> tuple[dict[str, int], int]:
        with self.lock:
            return dict(self.counts), len(self.decode_events)

    def events_since(self, offset: int) -> list[tuple[float, str, str, str]]:
        with self.lock:
            return list(self.decode_events[int(offset) :])

    def close(self) -> None:
        image_source_module._read_image_file_bytes = self._original_read


class _CountingFolderSource(FolderImageSource):
    def __init__(
        self,
        folder_path: str | Path,
        tracker: _SourceTracker,
        *,
        recursive: bool = False,
        sort_descending: bool = False,
    ) -> None:
        super().__init__(
            folder_path,
            recursive=recursive,
            sort_descending=sort_descending,
        )
        self.tracker = tracker
        self.closed = False
        tracker.register(self)

    @staticmethod
    def _output_kind(result: object) -> str | None:
        if isinstance(result, QImage):
            return "qimage" if not result.isNull() else None
        if (
            isinstance(result, tuple)
            and result
            and isinstance(result[0], QImage)
            and not result[0].isNull()
        ):
            return "qimage"
        if isinstance(result, Image.Image):
            return "pil"
        return None

    def _tracked_call(
        self,
        api: str,
        image_id: str,
        call: Callable[[], object],
    ) -> object:
        self.tracker.record_attempt(image_id, api)
        try:
            result = call()
        except Exception:
            self.tracker.record_failure(api)
            raise
        kind = self._output_kind(result)
        if kind is not None:
            self.tracker.record_success(image_id, api, kind)
        return result

    def open_image(self, image_id: str) -> Image.Image:
        return self._tracked_call(
            "open_image",
            image_id,
            lambda: super(_CountingFolderSource, self).open_image(image_id),
        )  # type: ignore[return-value]

    def open_qimage(self, image_id: str) -> QImage | None:
        return self._tracked_call(
            "open_qimage",
            image_id,
            lambda: super(_CountingFolderSource, self).open_qimage(image_id),
        )  # type: ignore[return-value]

    def open_qimage_at_most(
        self,
        image_id: str,
        maximum_size: tuple[int, int],
    ) -> tuple[QImage, tuple[int, int]] | None:
        return self._tracked_call(
            "open_qimage_at_most",
            image_id,
            lambda: super(_CountingFolderSource, self).open_qimage_at_most(
                image_id, maximum_size
            ),
        )  # type: ignore[return-value]

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        with self.tracker.lock:
            self.tracker.counts["source_close_calls"] += 1
        super().close()


class _LegacyFolderAdapter(ImageSource):
    """Benchmark-only type fence that selects the retained generic path."""

    # Preserve Folder production's lazy-size contract; only the Viewer engine
    # type fence may differ between A and B.
    load_sizes_lazily = FolderImageSource.load_sizes_lazily

    def __init__(self, delegate: _CountingFolderSource) -> None:
        super().__init__(delegate.source_path)
        self.delegate = delegate

    def list_images(self) -> list[str]:
        return self.delegate.list_images()

    def open_image(self, image_id: str) -> Image.Image:
        return self.delegate.open_image(image_id)

    def open_qimage(self, image_id: str) -> QImage | None:
        return self.delegate.open_qimage(image_id)

    def open_qimage_at_most(
        self,
        image_id: str,
        maximum_size: tuple[int, int],
    ) -> tuple[QImage, tuple[int, int]] | None:
        return self.delegate.open_qimage_at_most(image_id, maximum_size)

    def display_path(self, image_id: str) -> str:
        return self.delegate.display_path(image_id)

    def file_size(self, image_id: str) -> int | None:
        return self.delegate.file_size(image_id)

    def logical_size(self, image_id: str) -> tuple[int, int] | None:
        return self.delegate.logical_size(image_id)

    def page_identity(self, image_id: str) -> str:
        return self.delegate.page_identity(image_id)

    def index_for_identity(self, identity: str) -> int:
        return self.delegate.index_for_identity(identity)

    def path_for_index(self, index: int) -> str | None:
        return self.delegate.path_for_index(index)

    def close(self) -> None:
        self.delegate.close()


@dataclass(frozen=True)
class _Snapshot:
    started_at: float
    source_counts: dict[str, int]
    decode_offset: int
    function_counts: dict[str, int]
    runtime_id: int | None
    runtime_metrics: dict[str, int]
    event_offset: int
    working_set_start: int | None
    process_peak_start: int | None


class _ViewerProbe:
    def __init__(
        self,
        window: ViewerWindow,
        tracker: _SourceTracker,
    ) -> None:
        self.window = window
        self.tracker = tracker
        self.runtime: RasterBookRuntime | None = None
        self.events: list[tuple[str, float, object]] = []
        self.counts: Counter[str] = Counter()
        self.lock = threading.Lock()
        self._scenario_peak_ws: int | None = None
        self._scenario_peak_cache = 0
        self._scenario_peak_pages = 0
        self._last_sample = 0.0
        self._originals = {
            "cache_pil": image_cache_module.pil_to_qimage,
            "runtime_pil": runtime_module.pil_to_qimage,
            "thumbnail_pil": thumbnail_render_module.pil_to_qimage,
            "viewer_pil": viewer_render_module.pil_to_qimage,
            "runtime_render": runtime_module.render_qimage,
            "viewer_render": viewer_render_module.render_qimage,
            "coordinator_start": window.image_work_coordinator.start_viewer,
        }
        self._install_function_probes()

        window.book_session.viewer_runtime_changed.connect(
            self._on_runtime_changed
        )
        window.image_cache.pageLoaded.connect(self._on_page_loaded)
        window.viewer.renderWorkFinished.connect(self._on_render_finished)
        window.viewer.displayCommitted.connect(self._on_display_committed)
        window.viewer.contentPainted.connect(self._on_content_painted)
        window.viewer.framePainted.connect(self._on_frame_painted)
        self._on_runtime_changed(window.book_session.viewer_runtime)

    def _bump(self, name: str, amount: int = 1) -> None:
        with self.lock:
            self.counts[name] += int(amount)

    def _install_function_probes(self) -> None:
        probe = self

        def conversion_wrapper(original, category: str):
            def wrapped(*args, **kwargs):
                result = original(*args, **kwargs)
                if isinstance(result, QImage) and not result.isNull():
                    probe._bump(category)
                    probe._bump(f"{category}_bytes", int(result.sizeInBytes()))
                return result

            return wrapped

        def render_wrapper(original, category: str):
            def wrapped(*args, **kwargs):
                result = original(*args, **kwargs)
                image = result[0]
                if isinstance(image, QImage) and not image.isNull():
                    probe._bump(category)
                    probe._bump(f"{category}_bytes", int(image.sizeInBytes()))
                return result

            return wrapped

        image_cache_module.pil_to_qimage = conversion_wrapper(
            self._originals["cache_pil"], "pil_to_qimage_outputs"
        )
        runtime_module.pil_to_qimage = conversion_wrapper(
            self._originals["runtime_pil"], "pil_to_qimage_outputs"
        )
        # Folder JPEG target decode imports this module attribute inside the
        # helper, so it must be probed explicitly rather than inferred from a
        # historical Qt decoder path.
        thumbnail_render_module.pil_to_qimage = conversion_wrapper(
            self._originals["thumbnail_pil"], "pil_to_qimage_outputs"
        )
        viewer_render_module.pil_to_qimage = conversion_wrapper(
            self._originals["viewer_pil"], "pil_to_qimage_outputs"
        )
        runtime_module.render_qimage = render_wrapper(
            self._originals["runtime_render"], "display_qimage_outputs"
        )
        viewer_render_module.render_qimage = render_wrapper(
            self._originals["viewer_render"], "display_qimage_outputs"
        )

        original_start = self._originals["coordinator_start"]

        def counted_start(runnable, priority):
            probe._bump("viewer_jobs")
            probe._bump(f"viewer_job_{type(runnable).__name__}")
            return original_start(runnable, priority)

        self.window.image_work_coordinator.start_viewer = counted_start

    def _event(self, kind: str, payload: object) -> None:
        self.events.append((kind, time.monotonic(), payload))

    def _on_page_loaded(self, cached: object) -> None:
        self._bump("image_cache_callbacks")
        self._event("image_cache_loaded", getattr(cached, "image_id", ""))

    def _on_render_finished(self, key: object, succeeded: bool) -> None:
        self._bump("render_callbacks")
        if succeeded:
            self._bump("viewer_qpixmap_uploads")
        self._event(
            "render_finished",
            (str(getattr(key, "image_id", "")), bool(succeeded)),
        )

    def _on_display_committed(self, image_ids: object) -> None:
        self._event("display_committed", tuple(image_ids))

    def _on_content_painted(self, image_ids: object) -> None:
        self._event("content_painted", tuple(image_ids))

    def _on_frame_painted(self, serial: int, image_ids: object) -> None:
        self._event("frame_painted", (int(serial), tuple(image_ids)))

    def _on_frame_ready(self, frame: object) -> None:
        self._event("frame_ready", frame)

    def _on_artifact_ready(self, artifact: object) -> None:
        self._event("artifact_ready", artifact)

    def _on_runtime_changed(self, runtime: object) -> None:
        previous = self.runtime
        if previous is not None:
            for signal, callback in (
                (previous.frameReady, self._on_frame_ready),
                (previous.artifactReady, self._on_artifact_ready),
            ):
                try:
                    signal.disconnect(callback)
                except (RuntimeError, TypeError):
                    pass
        self.runtime = runtime if isinstance(runtime, RasterBookRuntime) else None
        if self.runtime is not None:
            self.runtime.frameReady.connect(self._on_frame_ready)
            self.runtime.artifactReady.connect(self._on_artifact_ready)

    def cache_snapshot(self) -> dict[str, int]:
        runtime = self.runtime
        if runtime is not None:
            source_bytes = int(runtime.decoded_source_bytes)
            total_bytes = int(runtime.cache_bytes)
            return {
                "source_bytes": source_bytes,
                "frame_bytes": max(0, total_bytes - source_bytes),
                "combined_bytes": total_bytes,
                "retained_pages": len(set(runtime.cached_page_indexes)),
                "source_pages": int(runtime.decoded_source_count),
            }
        source_bytes = int(self.window.image_cache.cache_bytes)
        frame_bytes = int(self.window.viewer.render_cache_bytes())
        retained = set(self.window.image_cache._cache)
        for key in self.window.viewer._prepared_units:
            retained.update(index for index, _identity in key.spread_identity)
        return {
            "source_bytes": source_bytes,
            "frame_bytes": frame_bytes,
            "combined_bytes": source_bytes + frame_bytes,
            "retained_pages": len(retained),
            "source_pages": len(self.window.image_cache._cache),
        }

    def observe(self, *, force: bool = False) -> None:
        cache = self.cache_snapshot()
        self._scenario_peak_cache = max(
            self._scenario_peak_cache, cache["combined_bytes"]
        )
        self._scenario_peak_pages = max(
            self._scenario_peak_pages, cache["retained_pages"]
        )
        now = time.monotonic()
        if not force and now - self._last_sample < 0.005:
            return
        self._last_sample = now
        memory = _process_memory_bytes()
        if memory is not None:
            self._scenario_peak_ws = max(
                self._scenario_peak_ws or 0, memory[0]
            )

    def begin(self) -> _Snapshot:
        gc.collect()
        self.observe(force=True)
        memory = _process_memory_bytes()
        source_counts, decode_offset = self.tracker.snapshot()
        with self.lock:
            function_counts = dict(self.counts)
        runtime = self.runtime
        return _Snapshot(
            started_at=time.monotonic(),
            source_counts=source_counts,
            decode_offset=decode_offset,
            function_counts=function_counts,
            runtime_id=id(runtime) if runtime is not None else None,
            runtime_metrics=asdict(runtime.metrics) if runtime is not None else {},
            event_offset=len(self.events),
            working_set_start=memory[0] if memory else None,
            process_peak_start=memory[1] if memory else None,
        )

    def finish(
        self,
        snapshot: _Snapshot,
        *,
        requested_ids: tuple[str, ...],
        final_id: str,
        final_request_started_at: float,
        handler_ms: tuple[float, ...],
        settled: bool,
    ) -> dict[str, Any]:
        self.observe(force=True)
        now = time.monotonic()
        memory = _process_memory_bytes()
        source_counts, _ = self.tracker.snapshot()
        with self.lock:
            function_counts = dict(self.counts)
        source_delta = _delta(source_counts, snapshot.source_counts)
        function_delta = _delta(function_counts, snapshot.function_counts)
        runtime = self.runtime
        if runtime is None:
            runtime_delta: dict[str, int] = {}
        elif snapshot.runtime_id == id(runtime):
            runtime_delta = _delta(
                asdict(runtime.metrics), snapshot.runtime_metrics
            )
        else:
            runtime_delta = {
                key: int(value)
                for key, value in asdict(runtime.metrics).items()
                if int(value)
            }
        events = self.events[snapshot.event_offset :]
        event_counts = Counter(kind for kind, _at, _payload in events)
        final_commit_at = next(
            (
                at
                for kind, at, payload in events
                if kind == "display_committed"
                and final_id in payload
                and at >= final_request_started_at
            ),
            None,
        )
        final_paint_at = next(
            (
                at
                for kind, at, payload in events
                if kind == "content_painted"
                and final_id in payload
                and at >= final_request_started_at
            ),
            None,
        )
        decode_events = self.tracker.events_since(snapshot.decode_offset)
        decode_to_paint: list[float] = []
        if final_paint_at is not None:
            decode_to_paint = [
                round((final_paint_at - at) * 1000, 3)
                for at, image_id, _api, _kind in decode_events
                if image_id == final_id and at <= final_paint_at
            ]
        decoded_ids = [event[1] for event in decode_events]
        painted_ids = {
            image_id
            for kind, _at, payload in events
            if kind == "content_painted"
            for image_id in payload
        }
        transit = set(requested_ids[:-1])
        cache = self.cache_snapshot()
        working_set_end = memory[0] if memory else None
        process_peak_end = memory[1] if memory else None
        runtime_uploads = int(runtime_delta.get("qpixmap_creations", 0))
        viewer_uploads = int(function_delta.get("viewer_qpixmap_uploads", 0))
        gui_callbacks = (
            int(function_delta.get("image_cache_callbacks", 0))
            + int(function_delta.get("render_callbacks", 0))
            + int(runtime_delta.get("queued_callbacks", 0))
        )
        return {
            "elapsed_ms": round((now - snapshot.started_at) * 1000, 3),
            "requested_image_ids": list(requested_ids),
            "final_image_id": final_id,
            "handler_ms": [round(value, 3) for value in handler_ms],
            "input_to_commit_ms": (
                round((final_commit_at - final_request_started_at) * 1000, 3)
                if final_commit_at is not None
                else None
            ),
            "input_to_paint_ms": (
                round((final_paint_at - final_request_started_at) * 1000, 3)
                if final_paint_at is not None
                else None
            ),
            "decode_to_paint_ms": decode_to_paint,
            "source_io": {
                "file_read_calls": source_delta.get("file_read_calls", 0),
                "file_read_bytes": source_delta.get("file_read_bytes", 0),
                "observable_full_payload_copy_calls": source_delta.get(
                    "full_payload_copy_calls", 0
                ),
                "observable_full_payload_copy_bytes": source_delta.get(
                    "full_payload_copy_bytes", 0
                ),
            },
            "decode": {
                "api_attempts": source_delta.get("decode_api_attempts", 0),
                "successful_calls": source_delta.get("decode_calls", 0),
                "failures": source_delta.get("decode_failures", 0),
                "source_qimage_outputs": source_delta.get(
                    "source_qimage_outputs", 0
                ),
                "source_pil_outputs": source_delta.get("source_pil_outputs", 0),
                "pil_to_qimage_outputs": function_delta.get(
                    "pil_to_qimage_outputs", 0
                ),
                "display_qimage_outputs": function_delta.get(
                    "display_qimage_outputs", 0
                ),
                "image_ids": decoded_ids,
                "duplicate_decode_calls": max(
                    0, len(decoded_ids) - len(set(decoded_ids))
                ),
                "transit_decode_calls": sum(value in transit for value in decoded_ids),
                "unpainted_decode_calls": sum(
                    value not in painted_ids for value in decoded_ids
                ),
            },
            "work": {
                "viewer_jobs": function_delta.get("viewer_jobs", 0),
                "jobs_by_class": {
                    key.removeprefix("viewer_job_"): value
                    for key, value in function_delta.items()
                    if key.startswith("viewer_job_")
                },
                "runtime_jobs": runtime_delta.get("jobs_submitted", 0),
                "runtime_cancel_requests": runtime_delta.get(
                    "cancel_requests", 0
                ),
                "runtime_stale_results": runtime_delta.get("stale_results", 0),
            },
            "gui": {
                "gui_callbacks": gui_callbacks,
                "image_cache_callbacks": function_delta.get(
                    "image_cache_callbacks", 0
                ),
                "render_callbacks": function_delta.get("render_callbacks", 0),
                "runtime_queued_callbacks": runtime_delta.get(
                    "queued_callbacks", 0
                ),
                "runtime_frame_ready_emissions": int(event_counts["frame_ready"]),
                "runtime_artifact_ready_emissions": int(
                    event_counts["artifact_ready"]
                ),
                "display_commits": int(event_counts["display_committed"]),
                "content_paints": int(event_counts["content_painted"]),
                "frame_paints": int(event_counts["frame_painted"]),
                "qpixmap_from_image": runtime_uploads + viewer_uploads,
                "runtime_qpixmap_uploads": runtime_uploads,
                "viewer_qpixmap_uploads": viewer_uploads,
            },
            "cache": {
                **cache,
                "peak_sampled_bytes": self._scenario_peak_cache,
                "peak_sampled_pages": self._scenario_peak_pages,
                "runtime_source_cache_hits": runtime_delta.get(
                    "source_cache_hits", 0
                ),
                "runtime_cache_hits": runtime_delta.get("cache_hits", 0),
                "runtime_evictions": runtime_delta.get("cache_evictions", 0),
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
                "sampled_peak_working_set_mib": _mib(self._scenario_peak_ws),
                "sampled_peak_delta_mib": (
                    _mib(self._scenario_peak_ws - snapshot.working_set_start)
                    if self._scenario_peak_ws is not None
                    and snapshot.working_set_start is not None
                    else None
                ),
                "process_peak_start_mib": _mib(snapshot.process_peak_start),
                "process_peak_end_mib": _mib(process_peak_end),
            },
            "settled": bool(settled),
        }

    def close(self) -> None:
        self._on_runtime_changed(None)
        for signal, callback in (
            (
                self.window.book_session.viewer_runtime_changed,
                self._on_runtime_changed,
            ),
            (self.window.image_cache.pageLoaded, self._on_page_loaded),
            (self.window.viewer.renderWorkFinished, self._on_render_finished),
            (self.window.viewer.displayCommitted, self._on_display_committed),
            (self.window.viewer.contentPainted, self._on_content_painted),
            (self.window.viewer.framePainted, self._on_frame_painted),
        ):
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        image_cache_module.pil_to_qimage = self._originals["cache_pil"]
        runtime_module.pil_to_qimage = self._originals["runtime_pil"]
        thumbnail_render_module.pil_to_qimage = self._originals[
            "thumbnail_pil"
        ]
        viewer_render_module.pil_to_qimage = self._originals["viewer_pil"]
        runtime_module.render_qimage = self._originals["runtime_render"]
        viewer_render_module.render_qimage = self._originals["viewer_render"]
        self.window.image_work_coordinator.start_viewer = self._originals[
            "coordinator_start"
        ]


def _pump_until(
    application: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout: float,
    observer: Callable[[], None] | None = None,
) -> None:
    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        application.processEvents()
        if observer is not None:
            observer()
        if predicate():
            return
        time.sleep(0.001)
    raise TimeoutError("offscreen folder runtime benchmark timed out")


def _render_once(window: ViewerWindow) -> None:
    target = QPixmap(window.viewer.size())
    target.fill()
    window.viewer.render(target)


def _quiet(window: ViewerWindow) -> bool:
    runtime = window.book_session.viewer_runtime
    if isinstance(runtime, RasterBookRuntime):
        return bool(
            not runtime.has_unfinished_tasks()
            and runtime.active_job_count == 0
            and window._pending_zip_runtime_request is None
            and not window._zip_runtime_request_timer.isActive()
            and not window._raster_viewport_timer.isActive()
            and not window.viewer._render_tasks
        )
    return bool(
        not window.image_cache.has_unfinished_tasks()
        and not window.viewer._render_tasks
        and window._pending_decode_demand is None
        and window._pending_display_demand is None
        and window.viewer._pending_display is None
        and window._raster_prefetch_after_paint is None
        and not window._decode_demand_timer.isActive()
        and not window._display_demand_timer.isActive()
        and not window._prepared_display_timer.isActive()
        and not window._raster_viewport_timer.isActive()
        and not window.viewer._resize_render_timer.isActive()
    )


def _wait_quiet(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    *,
    timeout: float,
) -> bool:
    stable_since: float | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        probe.observe()
        if _quiet(window):
            now = time.monotonic()
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= 0.005:
                return True
        else:
            stable_since = None
        time.sleep(0.001)
    return False


def _has_event(
    probe: _ViewerProbe,
    kind: str,
    image_id: str,
    *,
    offset: int,
) -> bool:
    for event_kind, _at, payload in probe.events[offset:]:
        if event_kind != kind:
            continue
        image_ids = payload[1] if kind == "frame_painted" else payload
        if image_id in image_ids:
            return True
    return False


def _execute(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    action: Callable[[], object],
    *,
    target_page: int,
    timeout: float,
    accept_render_completion: bool = False,
) -> dict[str, Any]:
    target_id = window.model.image_id_at(target_page)
    if target_id is None:
        raise AssertionError(f"page {target_page} has no image identity")
    offset = len(probe.events)
    started = time.monotonic()
    action()
    handler_ms = (time.monotonic() - started) * 1000
    _pump_until(
        application,
        lambda: (
            target_page in window.viewer.displayed_page_indexes
            and (
                _has_event(
                    probe, "display_committed", target_id, offset=offset
                )
                or (
                    accept_render_completion
                    and _has_event(
                        probe, "render_finished", target_id, offset=offset
                    )
                )
            )
        ),
        timeout=timeout,
        observer=probe.observe,
    )
    _render_once(window)
    application.processEvents()
    probe.observe(force=True)
    _pump_until(
        application,
        lambda: _has_event(probe, "content_painted", target_id, offset=offset),
        timeout=timeout,
        observer=probe.observe,
    )
    return {
        "started_at": started,
        "handler_ms": handler_ms,
        "target_id": target_id,
    }


def _clear_artifacts(window: ViewerWindow) -> None:
    runtime = window.book_session.viewer_runtime
    if isinstance(runtime, RasterBookRuntime):
        runtime.cancel(clear_artifacts=True)
        return
    window.image_cache._clear_cache()
    window.viewer.prepare_display_units(())
    window.viewer._render_cache.clear()
    window.viewer._last_rendered_by_image.clear()


def _ensure_anchor(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    page: int,
    *,
    timeout: float,
) -> None:
    if window.viewer.displayed_page_indexes != (page,):
        _execute(
            application,
            window,
            probe,
            lambda: window._go_to_index_with_history(page),
            target_page=page,
            timeout=timeout,
        )
        _wait_quiet(application, window, probe, timeout=timeout)


def _measure_legs(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    snapshot: _Snapshot,
    legs: list[dict[str, Any]],
    requested_ids: tuple[str, ...],
    *,
    timeout: float,
) -> dict[str, Any]:
    settled = _wait_quiet(application, window, probe, timeout=timeout)
    final = legs[-1]
    return probe.finish(
        snapshot,
        requested_ids=requested_ids,
        final_id=str(final["target_id"]),
        final_request_started_at=float(final["started_at"]),
        handler_ms=tuple(float(leg["handler_ms"]) for leg in legs),
        settled=settled,
    )


def _forward_sequence(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    *,
    timeout: float,
) -> dict[str, Any]:
    _ensure_anchor(application, window, probe, 0, timeout=timeout)
    _clear_artifacts(window)
    snapshot = probe.begin()
    legs: list[dict[str, Any]] = []
    for page in (1, 2, 3):
        leg = _execute(
            application,
            window,
            probe,
            lambda target=page: window._go_to_index_with_history(target),
            target_page=page,
            timeout=timeout,
        )
        legs.append(leg)
        _wait_quiet(application, window, probe, timeout=timeout)
    return _measure_legs(
        application,
        window,
        probe,
        snapshot,
        legs,
        tuple(str(leg["target_id"]) for leg in legs),
        timeout=timeout,
    )


def _cold_forward(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    *,
    timeout: float,
) -> dict[str, Any]:
    _ensure_anchor(application, window, probe, 1, timeout=timeout)
    _clear_artifacts(window)
    snapshot = probe.begin()
    leg = _execute(
        application,
        window,
        probe,
        lambda: window._go_to_index_with_history(2),
        target_page=2,
        timeout=timeout,
    )
    return _measure_legs(
        application,
        window,
        probe,
        snapshot,
        [leg],
        (str(leg["target_id"]),),
        timeout=timeout,
    )


def _reversal(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    *,
    timeout: float,
) -> dict[str, Any]:
    _ensure_anchor(application, window, probe, 4, timeout=timeout)
    _clear_artifacts(window)
    snapshot = probe.begin()
    first_id = window.model.image_id_at(5)
    assert first_id is not None
    first_started = time.monotonic()
    window._go_to_index_with_history(5)
    first_handler = (time.monotonic() - first_started) * 1000
    _pump_until(
        application,
        lambda: (
            len(probe.tracker.events_since(snapshot.decode_offset)) > 0
            or probe.counts.get("viewer_jobs", 0)
            > snapshot.function_counts.get("viewer_jobs", 0)
        ),
        timeout=timeout,
        observer=probe.observe,
    )
    reverse = _execute(
        application,
        window,
        probe,
        lambda: window._go_to_index_with_history(3),
        target_page=3,
        timeout=timeout,
    )
    first = {
        "started_at": first_started,
        "handler_ms": first_handler,
        "target_id": first_id,
    }
    return _measure_legs(
        application,
        window,
        probe,
        snapshot,
        [first, reverse],
        (str(first_id), str(reverse["target_id"])),
        timeout=timeout,
    )


def _roundtrip(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    *,
    timeout: float,
) -> dict[str, Any]:
    _ensure_anchor(application, window, probe, 5, timeout=timeout)
    _clear_artifacts(window)
    snapshot = probe.begin()
    outward = _execute(
        application,
        window,
        probe,
        lambda: window._go_to_index_with_history(6),
        target_page=6,
        timeout=timeout,
    )
    _wait_quiet(application, window, probe, timeout=timeout)
    _clear_artifacts(window)
    returned = _execute(
        application,
        window,
        probe,
        lambda: window._go_to_index_with_history(5),
        target_page=5,
        timeout=timeout,
    )
    return _measure_legs(
        application,
        window,
        probe,
        snapshot,
        [outward, returned],
        (str(outward["target_id"]), str(returned["target_id"])),
        timeout=timeout,
    )


def _rapid_final(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    *,
    timeout: float,
) -> dict[str, Any]:
    _ensure_anchor(application, window, probe, 0, timeout=timeout)
    _clear_artifacts(window)
    pages = (1, 2, 3, 4, 5, 6)
    requested_ids = tuple(
        str(window.model.image_id_at(page)) for page in pages
    )
    snapshot = probe.begin()

    def action() -> None:
        for page in pages:
            window._go_to_index_with_history(page)

    leg = _execute(
        application,
        window,
        probe,
        action,
        target_page=pages[-1],
        timeout=timeout,
    )
    return _measure_legs(
        application,
        window,
        probe,
        snapshot,
        [leg],
        requested_ids,
        timeout=timeout,
    )


def _ready(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    *,
    timeout: float,
) -> dict[str, Any]:
    target = min(7, window.model.total_pages - 1)
    _ensure_anchor(application, window, probe, target, timeout=timeout)
    window._refresh_view()
    _wait_quiet(application, window, probe, timeout=timeout)
    snapshot = probe.begin()
    leg = _execute(
        application,
        window,
        probe,
        window._refresh_view,
        target_page=target,
        timeout=timeout,
    )
    return _measure_legs(
        application,
        window,
        probe,
        snapshot,
        [leg],
        (str(leg["target_id"]),),
        timeout=timeout,
    )


def _layout_action(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    action: Callable[[], object],
    *,
    timeout: float,
) -> dict[str, Any]:
    page = window.presentation_state.displayed_page
    if page is None:
        raise AssertionError("layout scenario has no committed page")
    image_id = window.model.image_id_at(page)
    if image_id is None:
        raise AssertionError("layout scenario page has no image identity")
    snapshot = probe.begin()
    offset = len(probe.events)
    started = time.monotonic()
    action()
    handler = (time.monotonic() - started) * 1000
    settled = _wait_quiet(application, window, probe, timeout=timeout)
    if not settled:
        raise TimeoutError("layout replacement did not settle")
    _render_once(window)
    application.processEvents()
    _pump_until(
        application,
        lambda: _has_event(
            probe, "content_painted", image_id, offset=offset
        ),
        timeout=timeout,
        observer=probe.observe,
    )
    return probe.finish(
        snapshot,
        requested_ids=(image_id,),
        final_id=image_id,
        final_request_started_at=started,
        handler_ms=(handler,),
        settled=True,
    )


def _magnifier(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    *,
    timeout: float,
) -> dict[str, Any]:
    image = next(
        (candidate for candidate in window.viewer._images if candidate.qimage),
        None,
    )
    if image is None or image.qimage is None:
        raise AssertionError("magnifier scenario requires a committed source")
    snapshot = probe.begin()
    offset = len(probe.events)
    started = time.monotonic()
    window.viewer.magnifier_selecting = True
    window.viewer.magnifier_source_page = image.page_index
    window.viewer._magnifier_source_image_id = image.image_id
    window.viewer.magnifier_source_rect = QRectF(
        0,
        0,
        max(1, image.qimage.width() // 2),
        max(1, image.qimage.height() // 2),
    )
    # Legacy preview images upgrade to a full-resolution ImageCache source
    # before the crop is rendered.  The real gesture records this normalized
    # rectangle in _begin_magnifier_selection(); reproduce that contract here
    # so the synthetic offscreen request can resume after the upgrade.
    window.viewer._magnifier_source_normalized = QRectF(0.0, 0.0, 0.5, 0.5)
    window.viewer._request_magnifier_render()
    handler = (time.monotonic() - started) * 1000
    _pump_until(
        application,
        lambda: (
            window.viewer.magnifier_active
            and window.viewer._magnifier_pixmap is not None
        ),
        timeout=timeout,
        observer=probe.observe,
    )
    _render_once(window)
    application.processEvents()
    _pump_until(
        application,
        lambda: _has_event(
            probe, "content_painted", image.image_id, offset=offset
        ),
        timeout=timeout,
        observer=probe.observe,
    )
    settled = _wait_quiet(application, window, probe, timeout=timeout)
    result = probe.finish(
        snapshot,
        requested_ids=(image.image_id,),
        final_id=image.image_id,
        final_request_started_at=started,
        handler_ms=(handler,),
        settled=settled,
    )
    window.viewer.cancel_magnifier()
    return result


def _switch_book(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    folder: Path,
    *,
    timeout: float,
) -> dict[str, Any]:
    target_id = str(sorted(folder.glob("*.jpg"))[0])
    snapshot = probe.begin()
    offset = len(probe.events)
    started = time.monotonic()
    window.open_path(folder)
    handler = (time.monotonic() - started) * 1000
    _pump_until(
        application,
        lambda: (
            window.book_session.current_path == folder
            and window.viewer.displayed_page_indexes == (0,)
            and _has_event(
                probe, "display_committed", target_id, offset=offset
            )
        ),
        timeout=timeout,
        observer=probe.observe,
    )
    _render_once(window)
    application.processEvents()
    _pump_until(
        application,
        lambda: _has_event(
            probe, "content_painted", target_id, offset=offset
        ),
        timeout=timeout,
        observer=probe.observe,
    )
    settled = _wait_quiet(application, window, probe, timeout=timeout)
    return probe.finish(
        snapshot,
        requested_ids=(target_id,),
        final_id=target_id,
        final_request_started_at=started,
        handler_ms=(handler,),
        settled=settled,
    )


def _open_initial(
    application: QApplication,
    window: ViewerWindow,
    probe: _ViewerProbe,
    folder: Path,
    *,
    timeout: float,
) -> None:
    window.resize(800, 600)
    application.processEvents()
    window.open_path(folder)
    _pump_until(
        application,
        lambda: (
            window.book_session.current_path == folder
            and window.model.total_pages > 0
            and window.viewer.displayed_page_indexes == (0,)
        ),
        timeout=timeout,
        observer=probe.observe,
    )
    _render_once(window)
    application.processEvents()
    if not _wait_quiet(application, window, probe, timeout=timeout):
        raise TimeoutError("initial folder Viewer work did not settle")


def _aggregate(scenarios: dict[str, Any]) -> dict[str, Any]:
    flat: list[dict[str, Any]] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if "source_io" in value and "gui" in value:
                flat.append(value)
                return
            for child in value.values():
                visit(child)

    visit(scenarios)

    def total(section: str, name: str) -> int:
        return sum(int(item[section].get(name, 0)) for item in flat)

    memory_peaks = [
        item["memory"].get("sampled_peak_delta_mib") for item in flat
    ]
    cache_peaks = [int(item["cache"]["peak_sampled_bytes"]) for item in flat]
    return {
        "scenario_count": len(flat),
        "scenario_elapsed_ms": round(
            sum(float(item["elapsed_ms"]) for item in flat), 3
        ),
        "file_read_calls": total("source_io", "file_read_calls"),
        "file_read_bytes": total("source_io", "file_read_bytes"),
        "observable_full_payload_copy_calls": total(
            "source_io", "observable_full_payload_copy_calls"
        ),
        "observable_full_payload_copy_bytes": total(
            "source_io", "observable_full_payload_copy_bytes"
        ),
        "decode_calls": total("decode", "successful_calls"),
        "source_qimage_outputs": total("decode", "source_qimage_outputs"),
        "source_pil_outputs": total("decode", "source_pil_outputs"),
        "pil_to_qimage_outputs": total("decode", "pil_to_qimage_outputs"),
        "display_qimage_outputs": total("decode", "display_qimage_outputs"),
        "duplicate_decode_calls": total("decode", "duplicate_decode_calls"),
        "transit_decode_calls": total("decode", "transit_decode_calls"),
        "viewer_jobs": total("work", "viewer_jobs"),
        "gui_callbacks": total("gui", "gui_callbacks"),
        "qpixmap_from_image": total("gui", "qpixmap_from_image"),
        "display_commits": total("gui", "display_commits"),
        "content_paints": total("gui", "content_paints"),
        "peak_sampled_cache_bytes": max(cache_peaks, default=0),
        "peak_sampled_working_set_delta_mib": max(
            (float(value) for value in memory_peaks if value is not None),
            default=None,
        ),
    }


def _run_worker(args: argparse.Namespace) -> dict[str, Any]:
    fixture = json.loads(Path(args.fixture_manifest).read_text(encoding="utf-8"))
    folders = {key: Path(value) for key, value in fixture["folders"].items()}
    application = QApplication.instance() or QApplication([])
    tracker = _SourceTracker()
    case = str(args.worker_case)

    def source_factory(
        path: Path,
        *,
        recursive_folder: bool = False,
        sort_descending: bool = False,
        **_kwargs: object,
    ) -> tuple[ImageSource, str | None]:
        target = Path(path)
        selected = str(target) if target.is_file() else None
        folder = target.parent if selected is not None else target
        source = _CountingFolderSource(
            folder,
            tracker,
            recursive=recursive_folder,
            sort_descending=sort_descending,
        )
        return (
            _LegacyFolderAdapter(source) if case == _CASE_A else source,
            selected,
        )

    config = ConfigManager(
        Path(args.fixture_manifest).parent / f"config-{case}.json"
    )
    config.load()
    config.apply(
        {
            "view_mode": "single",
            "single_first_page": False,
            "viewer_resampling_mode": "standard",
            "fit_mode": "fit_window",
            "viewer_prefetch_preset": "custom",
            "viewer_prefetch_direction_priority_enabled": True,
            "viewer_prefetch_image_forward_units": 1,
            "viewer_prefetch_image_backward_units": 1,
            "viewer_cache_max_memory_mib": int(args.cache_mib),
            "show_page_list": False,
        },
        save=True,
    )
    coordinator = ImageWorkCoordinator(max_workers=1)
    session = BookSession(
        source_factory=source_factory,
        image_work_coordinator=coordinator,
    )
    window = ViewerWindow(
        config_manager=config,
        book_session=session,
        image_work_coordinator=coordinator,
    )
    probe = _ViewerProbe(window, tracker)
    shutdown_complete = False
    close_result: dict[str, Any] = {}

    def shutdown() -> None:
        nonlocal shutdown_complete, close_result
        if shutdown_complete:
            return
        shutdown_complete = True
        started = time.monotonic()
        window.prepare_shutdown(wait_msecs=10_000)
        window.close()
        application.processEvents()
        coordinator_complete = coordinator.shutdown(wait_msecs=10_000)
        application.processEvents()
        close_result = {
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "coordinator_complete": bool(coordinator_complete),
            "open_workers": len(session._open_workers),
            "retired_viewer_runtimes": len(session._retired_viewer_runtimes),
            "retired_page_list_runtimes": len(
                session._retired_page_list_runtimes
            ),
            "image_cache_unfinished": bool(
                session.image_cache.has_unfinished_tasks()
            ),
            "viewer_render_tasks": len(window.viewer._render_tasks),
            "created_sources": len(tracker.sources),
            "closed_sources": sum(source.closed for source in tracker.sources),
        }

    atexit.register(shutdown)
    try:
        window.resize(int(args.viewport_width), int(args.viewport_height))
        _open_initial(
            application,
            window,
            probe,
            folders["small"],
            timeout=float(args.timeout),
        )
        if case == _CASE_A:
            if session.viewer_runtime is not None or window._zip_runtime_active:
                raise AssertionError("A did not select the legacy two-stage path")
            if window.viewer._direct_display_mode:
                raise AssertionError("A unexpectedly selected direct display")
        else:
            if not isinstance(session.viewer_runtime, FolderRasterBookRuntime):
                raise AssertionError("B did not install FolderRasterBookRuntime")
            if not window._zip_runtime_active or not window.viewer._direct_display_mode:
                raise AssertionError("B did not select production direct display")

        scenarios: dict[str, Any] = {
            "small": {
                "forward_sequence": _forward_sequence(
                    application,
                    window,
                    probe,
                    timeout=float(args.timeout),
                )
            }
        }
        scenarios["book_switch_small_to_large"] = _switch_book(
            application,
            window,
            probe,
            folders["large"],
            timeout=float(args.timeout),
        )
        scenarios["large"] = {
            "forward_sequence": _forward_sequence(
                application,
                window,
                probe,
                timeout=float(args.timeout),
            )
        }
        scenarios["book_switch_large_to_mixed"] = _switch_book(
            application,
            window,
            probe,
            folders["mixed"],
            timeout=float(args.timeout),
        )
        scenarios["mixed"] = {
            "cold_forward": _cold_forward(
                application, window, probe, timeout=float(args.timeout)
            ),
            "reversal": _reversal(
                application, window, probe, timeout=float(args.timeout)
            ),
            "cold_roundtrip": _roundtrip(
                application, window, probe, timeout=float(args.timeout)
            ),
            "rapid_final": _rapid_final(
                application, window, probe, timeout=float(args.timeout)
            ),
            "ready": _ready(
                application, window, probe, timeout=float(args.timeout)
            ),
        }

        _ensure_anchor(
            application, window, probe, 1, timeout=float(args.timeout)
        )
        _clear_artifacts(window)
        _execute(
            application,
            window,
            probe,
            window._refresh_view,
            target_page=1,
            timeout=float(args.timeout),
        )
        _wait_quiet(application, window, probe, timeout=float(args.timeout))
        scenarios["layout"] = {}
        scenarios["layout"]["resize"] = _layout_action(
            application,
            window,
            probe,
            lambda: window.resize(
                max(320, int(args.viewport_width) - 180),
                max(240, int(args.viewport_height) - 120),
            ),
            timeout=float(args.timeout),
        )
        scenarios["layout"]["rotation"] = _layout_action(
            application,
            window,
            probe,
            window.rotate_right,
            timeout=float(args.timeout),
        )
        window.viewer.devicePixelRatioF = MethodType(
            lambda _viewer: 2.0, window.viewer
        )
        scenarios["layout"]["dpi_change"] = _layout_action(
            application,
            window,
            probe,
            lambda: QApplication.sendEvent(
                window.viewer, QEvent(QEvent.Type.DevicePixelRatioChange)
            ),
            timeout=float(args.timeout),
        )
        scenarios["layout"]["magnifier"] = _magnifier(
            application,
            window,
            probe,
            timeout=float(args.timeout),
        )

        scenarios["book_switch_to_single"] = _switch_book(
            application,
            window,
            probe,
            folders["single"],
            timeout=float(args.timeout),
        )
        scenarios["single_ready"] = _ready(
            application,
            window,
            probe,
            timeout=float(args.timeout),
        )
        shutdown()
        totals = _aggregate(scenarios)
        totals["close_time_ms"] = close_result["elapsed_ms"]
        return {
            "case": case,
            "path": (
                "ImageCache -> ViewerRenderTask -> prepared display"
                if case == _CASE_A
                else "FolderRasterBookRuntime -> direct atomic display"
            ),
            "production_runtime_required": case == _CASE_B,
            "scenarios": scenarios,
            "totals": totals,
            "shutdown": close_result,
        }
    finally:
        shutdown()
        probe.close()
        tracker.close()
        atexit.unregister(shutdown)


def _worker_command(args: argparse.Namespace, case: str) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-case",
        case,
        "--fixture-manifest",
        str(args.fixture_manifest),
        "--viewport-width",
        str(args.viewport_width),
        "--viewport-height",
        str(args.viewport_height),
        "--cache-mib",
        str(args.cache_mib),
        "--timeout",
        str(args.timeout),
    ]


def _run_isolated(args: argparse.Namespace, case: str) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        _worker_command(args, case),
        cwd=_REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=max(60.0, float(args.timeout) * 30.0),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{case} failed with exit code {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{case} returned invalid JSON\nstdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        ) from exc


def _comparison(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    old = a["totals"]
    new = b["totals"]
    integer_metrics = (
        "file_read_calls",
        "file_read_bytes",
        "observable_full_payload_copy_calls",
        "observable_full_payload_copy_bytes",
        "decode_calls",
        "source_qimage_outputs",
        "source_pil_outputs",
        "pil_to_qimage_outputs",
        "display_qimage_outputs",
        "duplicate_decode_calls",
        "transit_decode_calls",
        "viewer_jobs",
        "gui_callbacks",
        "qpixmap_from_image",
        "display_commits",
        "content_paints",
        "peak_sampled_cache_bytes",
    )
    differences = {
        f"{name}_A_minus_B": int(old[name]) - int(new[name])
        for name in integer_metrics
    }
    a_elapsed = float(old["scenario_elapsed_ms"])
    b_elapsed = float(new["scenario_elapsed_ms"])
    return {
        **differences,
        "scenario_elapsed_ms_A_minus_B": round(a_elapsed - b_elapsed, 3),
        "scenario_elapsed_speedup_A_over_B": (
            round(a_elapsed / b_elapsed, 3) if b_elapsed > 0 else None
        ),
        "close_time_ms_A_minus_B": round(
            float(old["close_time_ms"]) - float(new["close_time_ms"]), 3
        ),
        "peak_sampled_working_set_delta_mib_A_minus_B": (
            round(
                float(old["peak_sampled_working_set_delta_mib"])
                - float(new["peak_sampled_working_set_delta_mib"]),
                3,
            )
            if old["peak_sampled_working_set_delta_mib"] is not None
            and new["peak_sampled_working_set_delta_mib"] is not None
            else None
        ),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=8)
    parser.add_argument("--small-width", type=int, default=900)
    parser.add_argument("--small-height", type=int, default=1350)
    parser.add_argument("--large-width", type=int, default=2400)
    parser.add_argument("--large-height", type=int, default=3600)
    parser.add_argument("--viewport-width", type=int, default=1200)
    parser.add_argument("--viewport-height", type=int, default=800)
    parser.add_argument("--cache-mib", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--solid", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--worker-case", choices=(_CASE_A, _CASE_B), help=argparse.SUPPRESS
    )
    parser.add_argument("--fixture-manifest", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.quick and args.worker_case is None:
        args.small_width = 320
        args.small_height = 480
        args.large_width = 900
        args.large_height = 1350
        args.viewport_width = 640
        args.viewport_height = 420
        args.pages = 8
    if args.pages < 8:
        parser.error("--pages must be at least 8")
    for name in (
        "small_width",
        "small_height",
        "large_width",
        "large_height",
        "viewport_width",
        "viewport_height",
        "cache_mib",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.worker_case is not None and args.fixture_manifest is None:
        parser.error("--fixture-manifest is required for a worker case")
    return args


def main() -> int:
    args = _parse_args()
    if args.worker_case is not None:
        print(json.dumps(_run_worker(args), ensure_ascii=False, sort_keys=True))
        return 0

    with TemporaryDirectory(prefix="nivis-folder-runtime-ab-") as temporary:
        root = Path(temporary)
        fixture = _build_fixture(
            root,
            pages=int(args.pages),
            small_size=(int(args.small_width), int(args.small_height)),
            large_size=(int(args.large_width), int(args.large_height)),
            detail=not bool(args.solid),
        )
        manifest = root / "fixture.json"
        manifest.write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        args.fixture_manifest = manifest
        old_case = _run_isolated(args, _CASE_A)
        new_case = _run_isolated(args, _CASE_B)
        report = {
            "schema_version": 1,
            "benchmark": "Folder ImageCache two-stage A / FolderRasterBookRuntime B",
            "qt_platform": "offscreen",
            "fixture": fixture,
            "measurement_scope": {
                "A": (
                    "benchmark-only non-Folder ImageSource adapter around the "
                    "same FolderImageSource decoder; no production fallback flag"
                ),
                "B": (
                    "normal production FolderImageSource selection; worker fails "
                    "unless FolderRasterBookRuntime and direct display are active"
                ),
                "file_read": (
                    "completed calls to _read_image_file_bytes and returned bytes; "
                    "header-only logical_size probes are not byte-counted"
                ),
                "full_payload_copy": (
                    "observable Python bytes plus explicit BytesIO/QByteArray whole-"
                    "payload handoffs; implicit Qt/Python sharing is not claimed"
                ),
                "qpixmap": (
                    "runtime QPixmap.fromImage metrics plus successful legacy or "
                    "magnifier render callbacks"
                ),
                "paint": (
                    "contentPainted/framePainted caused by explicit offscreen "
                    "QWidget.render into a temporary QPixmap"
                ),
                "memory": (
                    "Windows working-set samples at about 5 ms; process peak is "
                    "process-lifetime-wide and each A/B case is isolated"
                ),
            },
            "A": old_case,
            "B": new_case,
            "comparison": _comparison(old_case, new_case),
        }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output is not None:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
