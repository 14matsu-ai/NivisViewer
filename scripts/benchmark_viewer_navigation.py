"""Offscreen navigation benchmark for the current production ZIP runtime.

The historical script compared two Viewer paths that no longer coexist in
production and depended on deleted compatibility-path state.  This maintained
benchmark exercises the book-owned ``RasterBookRuntime`` selected
by an ordinary ZIP open.  It measures sequential, reverse, immediate
direction reversal, ping-pong, and rapid-final navigation without showing a
window or synthesizing native input.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import io
import json
import os
import platform
from pathlib import Path
import random
import statistics
import sys
from tempfile import TemporaryDirectory
from threading import Lock
import time
import zlib
from typing import Callable
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from PIL import Image, ImageDraw, features
import PIL
import PySide6
from PySide6.QtCore import qVersion
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from app.config_manager import ConfigManager
from app.book_session import BookSession
from app.image_work_coordinator import ImageWorkCoordinator
from app.image_source import ZipImageSource
from app.raster_book_runtime import RasterBookRuntime
from app.viewer_navigation_policy import NavigationInputKind
from app.viewer_memory_policy import viewer_memory_mode_from_legacy_mib
from app.viewer_window import ViewerWindow


DETAIL_SEED = 20260905


def _jpeg_payload(size: tuple[int, int], page: int, *, fixture_mode: str = "flat") -> bytes:
    if fixture_mode not in {"flat", "high-detail"}:
        raise ValueError(f"unknown fixture mode: {fixture_mode}")
    output = io.BytesIO()
    color = (
        32 + page * 17 % 190,
        48 + page * 29 % 176,
        64 + page * 41 % 160,
    )
    with Image.new("RGB", size, color) as image:
        if fixture_mode == "high-detail":
            # One RGB page plus one 256-square tile at a time. Seeded luminance
            # texture, ruled panels and font-independent pseudo-glyph edges;
            # synthetic stress content, not a claim to reproduce a real scan.
            rng = random.Random(DETAIL_SEED + page)
            levels = bytes(168 + i % 64 for i in range(256))
            for y in range(0, size[1], 256):
                for x in range(0, size[0], 256):
                    with Image.frombytes("L", (256, 256), rng.randbytes(256 * 256).translate(levels)) as tile:
                        image.paste(tile, (x, y))
            draw = ImageDraw.Draw(image)
            for y in range(8, size[1], 24):
                draw.line((0, y, size[0], y), fill=(130, 145, 155), width=1)
                for x in range(8, size[0], 20):
                    bits = rng.getrandbits(8)
                    for bar in range(3):
                        width = 3 + ((bits >> (bar * 2)) & 7)
                        draw.rectangle((x, y + 4 + bar * 4, x + width, y + 5 + bar * 4), fill=(35, 40, 45))
            for x in range(0, size[0], 256):
                draw.line((x, 0, x, size[1]), fill=(80, 90, 100), width=2)
        image.save(output, "JPEG", quality=88, subsampling=2)
    return output.getvalue()


def _build_zip(
    root: Path,
    *,
    pages: int,
    image_size: tuple[int, int],
    compression: int = ZIP_DEFLATED,
    padding_bytes: int = 0,
    fixture_mode: str = "flat",
) -> tuple[Path, str, int]:
    archive = root / "日本語-navigation-benchmark.zip"
    digest = hashlib.sha256()
    total_bytes = 0
    with ZipFile(archive, "w", compression=compression) as output:
        for page in range(pages):
            payload = _jpeg_payload(image_size, page, fixture_mode=fixture_mode)
            name = f"ページ {page:03d}.jpg"
            info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = compression
            output.writestr(info, payload)
            digest.update(name.encode("utf-8"))
            digest.update(payload)
            total_bytes += len(payload)
        if padding_bytes > 0:
            # Stored non-image padding changes only the physical archive size.
            # It is deliberately ignored by ZipImageSource.list_images(), so
            # this fixture detects accidental whole-archive critical-path I/O
            # without changing page geometry or decode work.
            info = ZipInfo("__benchmark_archive_size_padding__.bin")
            info.compress_type = ZIP_STORED
            block = bytes(range(256)) * 4096
            remaining = int(padding_bytes)
            with output.open(info, "w", force_zip64=True) as padding:
                while remaining > 0:
                    chunk = block[: min(len(block), remaining)]
                    padding.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            total_bytes += int(padding_bytes)
    return archive, digest.hexdigest(), total_bytes


class _TrackedEntry:
    def __init__(self, stream, image_id: str, owner: "_TrackedZipImageSource") -> None:
        self._stream = stream
        self._image_id = image_id
        self._owner = owner

    def read(self, size: int = -1) -> bytes:
        payload = self._stream.read(size)
        self._owner.record_entry_read(self._image_id, len(payload))
        return payload

    def __enter__(self):
        self._stream.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._stream.__exit__(exc_type, exc, traceback)

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


class _TrackedZipImageSource(ZipImageSource):
    """Benchmark-only accounting around the production persistent ZIP source."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._tracking_lock = Lock()
        self._read_events: list[tuple[float, str, int]] = []
        self._decode_events: list[tuple[float, str, str]] = []
        original_open = self._zip.open

        def tracked_open(name, mode="r", pwd=None, *, force_zip64=False):
            stream = original_open(
                name,
                mode,
                pwd,
                force_zip64=force_zip64,
            )
            image_id = getattr(name, "filename", str(name))
            return _TrackedEntry(stream, image_id, self)

        self._zip.open = tracked_open

    def record_entry_read(self, image_id: str, byte_count: int) -> None:
        with self._tracking_lock:
            self._read_events.append(
                (time.perf_counter(), str(image_id), max(0, int(byte_count)))
            )

    def open_compatible_jpeg_at_most(self, image_id, maximum_size):
        with self._tracking_lock:
            self._decode_events.append(
                (time.perf_counter(), str(image_id), "started")
            )
        try:
            result = super().open_compatible_jpeg_at_most(
                image_id,
                maximum_size,
            )
        except Exception:
            with self._tracking_lock:
                self._decode_events.append(
                    (time.perf_counter(), str(image_id), "abandoned")
                )
            raise
        with self._tracking_lock:
            self._decode_events.append(
                (time.perf_counter(), str(image_id), "completed")
            )
        return result

    def interval_work(
        self,
        started_at: float,
        finished_at: float,
        *,
        target_image_id: str,
    ) -> dict[str, object]:
        with self._tracking_lock:
            reads = tuple(self._read_events)
            decodes = tuple(self._decode_events)
        interval_reads = tuple(
            event for event in reads if started_at <= event[0] <= finished_at
        )
        speculative_reads = tuple(
            event for event in interval_reads if event[1] != target_image_id
        )
        interval_decodes = tuple(
            event for event in decodes if started_at <= event[0] <= finished_at
        )
        return {
            "entry_read_bytes": sum(event[2] for event in interval_reads),
            "entry_read_calls": len(interval_reads),
            "speculative_entry_read_bytes": sum(
                event[2] for event in speculative_reads
            ),
            "speculative_entry_read_calls": len(speculative_reads),
            "decode_events": [
                {"image_id": image_id, "state": state}
                for _timestamp, image_id, state in interval_decodes
            ],
        }


def _pump_until(
    application: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout: float,
) -> None:
    deadline = time.perf_counter() + max(0.1, float(timeout))
    while time.perf_counter() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise TimeoutError("offscreen ZIP navigation benchmark timed out")


def _render_once(window: ViewerWindow) -> None:
    target = QPixmap(window.viewer.size())
    target.fill()
    window.viewer.render(target)


def _paint_progress_summary(
    *, started_at: float, observed_until: float,
    initial_unit: tuple[int, ...], final_unit: tuple[int, ...],
    paints: list[tuple[float, tuple[int, ...]]], timed_out: bool = False,
) -> dict[str, object]:
    """Measure page/unit residence, not repaint frequency or frame readiness.

    Include the initial page from the first input and the trailing interval to
    explicit observation end. Same-unit quality upgrades/repaints do not reset
    residence. Inputs and paint timestamps use the same perf_counter clock.
    """
    if observed_until < started_at:
        raise ValueError("observation ends before input starts")
    unit = initial_unit
    changed_at = started_at
    intervals = []
    first_final = started_at if initial_unit == final_unit else None
    transitions = []
    same_unit_paints = 0
    for timestamp, painted_unit in paints:
        if not started_at <= timestamp <= observed_until or not painted_unit:
            continue
        if painted_unit == final_unit and first_final is None:
            first_final = timestamp
        if painted_unit == unit:
            same_unit_paints += 1
            continue
        intervals.append(timestamp - changed_at)
        changed_at, unit = timestamp, painted_unit
        transitions.append({"pages": list(unit), "after_first_input_ms": (timestamp - started_at) * 1000})
    intervals.append(observed_until - changed_at)
    return {
        "status": "timeout" if timed_out else (
            "already_at_target" if initial_unit == final_unit else
            "completed" if first_final is not None else "incomplete"
        ),
        "observation_duration_ms": round((observed_until - started_at) * 1000, 3),
        "maximum_unchanged_page_interval_ms": round(max(intervals) * 1000, 3),
        "unchanged_intervals_ms": [round(value * 1000, 3) for value in intervals],
        "first_final_paint_ms": None if first_final is None else round((first_final - started_at) * 1000, 3),
        "initial_pages": list(initial_unit),
        "final_requested_pages": list(final_unit),
        "last_observed_pages": list(unit),
        "distinct_painted_transitions": [
            {**event, "after_first_input_ms": round(event["after_first_input_ms"], 3)}
            for event in transitions
        ],
        "same_unit_paints": same_unit_paints,
    }


def _wait_for_page(
    application: QApplication,
    window: ViewerWindow,
    page_index: int,
    *,
    timeout: float,
) -> None:
    _pump_until(
        application,
        lambda: window.viewer.displayed_page_indexes == (int(page_index),),
        timeout=timeout,
    )
    _render_once(window)
    application.processEvents()


def _wait_for_runtime_quiet(
    application: QApplication,
    window: ViewerWindow,
    runtime: RasterBookRuntime,
    *,
    timeout: float,
) -> None:
    stable_since: float | None = None

    def quiet() -> bool:
        nonlocal stable_since
        settled = bool(
            not runtime.has_unfinished_tasks()
            and runtime.active_job_count == 0
            and window._pending_zip_runtime_request is None
            and not window._zip_runtime_request_timer.isActive()
            and not window._raster_viewport_timer.isActive()
        )
        if not settled:
            stable_since = None
            return False
        now = time.monotonic()
        if stable_since is None:
            stable_since = now
            return False
        return now - stable_since >= 0.005

    _pump_until(application, quiet, timeout=timeout)


def _go_to(window: ViewerWindow, page_index: int) -> None:
    window._go_to_index_with_history(int(page_index))


def _measure_navigation(
    application: QApplication,
    window: ViewerWindow,
    action: Callable[[], None],
    *,
    expected_page: int,
    timeout: float,
) -> dict[str, float | int]:
    started = time.perf_counter()
    action()
    handler_finished = time.perf_counter()
    _wait_for_page(
        application,
        window,
        expected_page,
        timeout=timeout,
    )
    painted = time.perf_counter()
    return {
        "target_page": int(expected_page),
        "handler_ms": round((handler_finished - started) * 1000, 3),
        "request_to_paint_ms": round((painted - started) * 1000, 3),
    }


def _summary(legs: list[dict[str, float | int]]) -> dict[str, object]:
    handler = [float(leg["handler_ms"]) for leg in legs]
    painted = [float(leg["request_to_paint_ms"]) for leg in legs]
    return {
        "legs": legs,
        "handler_median_ms": round(statistics.median(handler), 3),
        "handler_max_ms": round(max(handler), 3),
        "request_to_paint_median_ms": round(statistics.median(painted), 3),
        "request_to_paint_max_ms": round(max(painted), 3),
    }


def run_benchmark(
    *,
    pages: int,
    image_size: tuple[int, int],
    viewport_size: tuple[int, int],
    cache_mib: int,
    timeout: float,
    compression: int = ZIP_DEFLATED,
    padding_bytes: int = 0,
    immediate_target: int = 5,
    fixture_mode: str = "flat",
) -> dict[str, object]:
    application = QApplication.instance() or QApplication([])
    with TemporaryDirectory(prefix="nivis-current-zip-benchmark-") as temporary:
        root = Path(temporary)
        fixture_started = time.perf_counter()
        archive, fixture_sha256, payload_bytes = _build_zip(
            root,
            pages=pages,
            image_size=image_size,
            compression=compression,
            padding_bytes=padding_bytes,
            fixture_mode=fixture_mode,
        )
        generation_ms = (time.perf_counter() - fixture_started) * 1000
        with archive.open("rb") as stream:
            archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        fixture_report = {
            "mode": fixture_mode,
            "seed": DETAIL_SEED if fixture_mode == "high-detail" else None,
            "algorithm": "seeded-tile-texture-pseudo-glyphs-v1" if fixture_mode == "high-detail" else "flat-page-color-v1",
            "parameters": {"texture_tile": 256, "luminance": [168, 231], "glyph_pitch": [20, 24], "bars_per_glyph": 3} if fixture_mode == "high-detail" else {},
            "jpeg_quality": 88, "jpeg_subsampling": 2,
            "pages": pages, "image_size": list(image_size),
            "viewport_size": list(viewport_size),
            "payload_bytes": payload_bytes, "archive_bytes": archive.stat().st_size,
            "jpeg_payload_bytes": payload_bytes - int(padding_bytes),
            "compression": "stored" if compression == ZIP_STORED else "deflated",
            "padding_bytes": int(padding_bytes), "sha256": fixture_sha256,
            "archive_sha256": archive_sha256,
            "generation_ms": round(generation_ms, 3),
        }
        versions = {
            "python": platform.python_version(), "pillow": PIL.__version__,
            "jpeg": features.version("jpg"), "libjpeg_turbo": features.version("libjpeg_turbo"),
            "pyside6": PySide6.__version__, "qt": qVersion(), "os": platform.platform(),
            "zlib": zlib.ZLIB_RUNTIME_VERSION,
        }
        config = ConfigManager(root / "config.json")
        config.load()
        config.apply(
            {
                "view_mode": "single",
                "single_first_page": False,
                "fit_mode": "fit_window",
                "show_page_list": False,
                "viewer_memory_mode": viewer_memory_mode_from_legacy_mib(
                    cache_mib
                ),
                "viewer_prefetch_preset": "standard",
            },
            save=True,
        )
        coordinator = ImageWorkCoordinator(max_workers=1)
        tracked_sources: list[_TrackedZipImageSource] = []

        def source_factory(path: Path, **_kwargs):
            source = _TrackedZipImageSource(Path(path))
            tracked_sources.append(source)
            return source, None

        session = BookSession(
            source_factory=source_factory,
            image_work_coordinator=coordinator,
        )
        window = ViewerWindow(
            config_manager=config,
            book_session=session,
            image_work_coordinator=coordinator,
        )
        window.resize(*viewport_size)
        application.processEvents()
        try:
            initial_started = time.perf_counter()
            window.open_path(archive)
            if not window.book_session.wait_for_async(round(timeout * 1000)):
                raise TimeoutError("ZIP source preparation timed out")
            _pump_until(
                application,
                lambda: window.model.total_pages == pages,
                timeout=timeout,
            )
            _wait_for_page(application, window, 0, timeout=timeout)
            first_visible_at = time.perf_counter()
            initial_paint_ms = round(
                (time.perf_counter() - initial_started) * 1000,
                3,
            )
            runtime = window.book_session.viewer_runtime
            if not isinstance(runtime, RasterBookRuntime):
                raise AssertionError(
                    "ordinary ZIP did not select the production RasterBookRuntime"
                )
            if not window._zip_runtime_active or window._zip_runtime is not runtime:
                raise AssertionError("ViewerWindow did not activate its ZIP runtime")
            source = tracked_sources[-1]

            def neighborhood_snapshot(
                label: str,
                center: int,
                *,
                radius: int = 6,
            ) -> dict[str, object]:
                ready = set(runtime.cached_page_indexes)
                source_ready = set(runtime._source_store.page_indexes)
                start = max(0, int(center) - max(0, int(radius)))
                stop = min(pages, int(center) + max(0, int(radius)) + 1)
                states = []
                for page_index in range(start, stop):
                    if page_index in ready:
                        state = "display_frame_ready"
                    elif page_index in source_ready:
                        state = "source_ready_only"
                    else:
                        state = "cold"
                    states.append({"page": page_index, "state": state})
                return {
                    "label": label,
                    "after_first_visible_ms": round(
                        (time.perf_counter() - first_visible_at) * 1000,
                        3,
                    ),
                    "center": int(center),
                    "states": states,
                    "display_frame_ready_pages": sorted(ready),
                    "source_ready_only_pages": sorted(source_ready - ready),
                    "cold_page_count": pages - len(ready | source_ready),
                    "cache_bytes": runtime.cache_bytes,
                }

            artifact_events: list[dict[str, object]] = []

            def record_artifact(frame) -> None:
                artifact_events.append(
                    {
                        "pages": [
                            int(page.page_index) for page in frame.unit.pages
                        ],
                        "after_first_visible_ms": round(
                            (time.perf_counter() - first_visible_at) * 1000,
                            3,
                        ),
                    }
                )

            runtime.artifactReady.connect(record_artifact)
            first_visible_neighborhood = neighborhood_snapshot(
                "first_visible",
                0,
            )

            target = max(1, min(int(immediate_target), pages - 1))
            target_image_id = window.model.image_ids[target]
            active = runtime._active_job
            active_at_input = None
            if active is not None:
                active_at_input = {
                    "page_indexes": [
                        page_index
                        for page_index, _image_id in active.key.unit_identity
                    ],
                    "started": active.started.is_set(),
                    "finished": active.finished.is_set(),
                    "cancelled": active.cancelled.is_set(),
                }
            cached_before = target in runtime.cached_page_indexes
            metrics_before = asdict(runtime.metrics)
            commit_events: list[dict[str, float | int]] = []
            paint_events: list[dict[str, object]] = []
            raw_paints: list[tuple[float, tuple[int, ...]]] = []
            input_events: list[dict[str, float | int | bool]] = []

            def record_commit(commit) -> None:
                commit_events.append(
                    {
                        "page": int(commit.frame.unit.focused_index),
                        "after_first_visible_ms": round(
                            (time.perf_counter() - first_visible_at) * 1000,
                            3,
                        ),
                    }
                )

            image_indexes = {
                image_id: index
                for index, image_id in enumerate(window.model.image_ids)
            }

            def record_paint(_serial: int, image_ids: object) -> None:
                ids = image_ids if isinstance(image_ids, tuple) else ()
                timestamp = time.perf_counter()
                painted_unit = tuple(image_indexes[image_id] for image_id in ids if image_id in image_indexes)
                raw_paints.append((timestamp, painted_unit))
                paint_events.append(
                    {
                        "pages": [
                            image_indexes[image_id]
                            for image_id in ids
                            if image_id in image_indexes
                        ],
                        "serial": _serial,
                        "after_first_visible_ms": round(
                            (timestamp - first_visible_at) * 1000,
                            3,
                        ),
                    }
                )

            window.presentationCommitted.connect(record_commit)
            window.viewer.framePainted.connect(record_paint)
            initial_unit = tuple(window.viewer.displayed_page_indexes)
            request_started = time.perf_counter()
            for step in range(1, target + 1):
                input_events.append(
                    {
                        "page": step,
                        "after_first_visible_ms": round(
                            (time.perf_counter() - first_visible_at) * 1000,
                            3,
                        ),
                        "cached_before_input": (
                            step in runtime.cached_page_indexes
                        ),
                    }
                )
                window.next_page(input_kind=NavigationInputKind.WHEEL)
            window._finish_wheel_navigation()
            request_finished = time.perf_counter()
            accepted_target = window.presentation_state.requested_page
            timed_out = False

            def observe_final_paint() -> bool:
                # No pumping between burst inputs. Afterwards each existing
                # event-pump iteration explicitly renders the current surface,
                # so ready intermediates are observable even for a hidden window.
                _render_once(window)
                return any(unit == (target,) for _at, unit in raw_paints)

            try:
                _pump_until(application, observe_final_paint, timeout=timeout)
            except TimeoutError:
                timed_out = True
            observed_until = time.perf_counter()
            progress = _paint_progress_summary(
                started_at=request_started, observed_until=observed_until,
                initial_unit=initial_unit, final_unit=(target,),
                paints=raw_paints, timed_out=timed_out,
            )
            target_neighborhood = neighborhood_snapshot(
                "immediate_observation_timeout" if timed_out else "immediate_target_painted",
                target,
            )
            committed_pages = list(commit_events)
            painted_pages = list(paint_events)
            window.presentationCommitted.disconnect(record_commit)
            window.viewer.framePainted.disconnect(record_paint)
            metrics_after = asdict(runtime.metrics)
            immediate = {
                "target_page": target,
                "first_visible_to_request_ms": round(
                    (request_started - first_visible_at) * 1000,
                    3,
                ),
                "handler_ms": round(
                    (request_finished - request_started) * 1000,
                    3,
                ),
                "request_to_paint_ms": progress["first_final_paint_ms"],
                "paint_progress": progress,
                "input_count": len(input_events),
                "final_accepted_target": accepted_target,
                "observation_policy": "Synchronous next_page(WHEEL) burst, no inter-input pumping; finish_wheel_navigation then processEvents + forced render each poll, existing 1 ms idle sleep. framePainted is offscreen rendering, not Windows compositor presentation.",
                "target_cache_hit_before_request": cached_before,
                "active_job_at_input": active_at_input,
                "navigation_inputs": input_events,
                "committed_pages": committed_pages,
                "painted_pages": painted_pages,
                "runtime_metric_delta": {
                    key: metrics_after[key] - metrics_before[key]
                    for key in metrics_after
                },
                "work_through_final_observation": source.interval_work(
                    first_visible_at,
                    observed_until,
                    target_image_id=target_image_id,
                ),
            }

            if timed_out:
                runtime.artifactReady.disconnect(record_artifact)
                return {
                    "schema_version": 5, "status": "timeout", "qt_platform": "offscreen",
                    "fixture": fixture_report, "versions": versions,
                    "initial_open_to_paint_ms": initial_paint_ms,
                    "immediate_after_first_visible": immediate,
                    "remaining_scenarios": "not run: immediate target was not painted before timeout",
                }

            _wait_for_runtime_quiet(
                application,
                window,
                runtime,
                timeout=timeout,
            )
            idle_neighborhood = neighborhood_snapshot(
                "post_open_warm_idle",
                target,
            )
            display_ready_sequence = list(artifact_events)
            runtime.artifactReady.disconnect(record_artifact)
            immediate["display_ready_neighborhood"] = {
                "snapshots": [
                    first_visible_neighborhood,
                    target_neighborhood,
                    idle_neighborhood,
                ],
                "artifact_ready_sequence": display_ready_sequence,
            }
            warm_origin = max(0, target - 1)
            _go_to(window, warm_origin)
            _wait_for_page(
                application,
                window,
                warm_origin,
                timeout=timeout,
            )
            warm_target_cached = target in runtime.cached_page_indexes
            warm_idle_control = _measure_navigation(
                application,
                window,
                lambda: _go_to(window, target),
                expected_page=target,
                timeout=timeout,
            )
            warm_idle_control["target_cache_hit_before_request"] = (
                warm_target_cached
            )
            anchor = max(2, min(pages - 4, pages // 2))
            _go_to(window, anchor)
            _wait_for_page(application, window, anchor, timeout=timeout)

            sequential = _measure_navigation(
                application,
                window,
                lambda: _go_to(window, anchor + 1),
                expected_page=anchor + 1,
                timeout=timeout,
            )
            reverse = _measure_navigation(
                application,
                window,
                lambda: _go_to(window, anchor),
                expected_page=anchor,
                timeout=timeout,
            )

            def reverse_immediately() -> None:
                _go_to(window, anchor + 1)
                _go_to(window, anchor - 1)

            reversal = _measure_navigation(
                application,
                window,
                reverse_immediately,
                expected_page=anchor - 1,
                timeout=timeout,
            )

            ping_pong_legs: list[dict[str, float | int]] = []
            for target in (anchor, anchor + 1, anchor, anchor + 1, anchor):
                ping_pong_legs.append(
                    _measure_navigation(
                        application,
                        window,
                        lambda target=target: _go_to(window, target),
                        expected_page=target,
                        timeout=timeout,
                    )
                )

            rapid_targets = tuple(range(anchor + 1, min(pages, anchor + 6)))
            rapid_final = rapid_targets[-1]

            def rapid_input() -> None:
                for target in rapid_targets:
                    _go_to(window, target)

            rapid = _measure_navigation(
                application,
                window,
                rapid_input,
                expected_page=rapid_final,
                timeout=timeout,
            )
            _wait_for_runtime_quiet(
                application,
                window,
                runtime,
                timeout=timeout,
            )
            metrics = asdict(runtime.metrics)
            return {
                "schema_version": 5,
                "status": "completed",
                "versions": versions,
                "benchmark": "current RasterBookRuntime ZIP navigation",
                "qt_platform": "offscreen",
                "production_authority": {
                    "runtime_type": type(runtime).__name__,
                    "viewer_active": bool(window._zip_runtime_active),
                    "legacy_paths": "retired; no benchmark-only Viewer path override",
                },
                "fixture": fixture_report,
                "initial_open_to_paint_ms": initial_paint_ms,
                "immediate_after_first_visible": immediate,
                "warm_idle_control": warm_idle_control,
                "sequential": sequential,
                "reverse": reverse,
                "direction_reversal": reversal,
                "ping_pong": _summary(ping_pong_legs),
                "rapid_final": {
                    **rapid,
                    "requested_pages": list(rapid_targets),
                },
                "runtime": {
                    "cached_page_indexes": list(runtime.cached_page_indexes),
                    "cache_bytes": runtime.cache_bytes,
                    "cache_byte_budget": runtime.cache_byte_budget,
                    "metrics": metrics,
                },
            }
        finally:
            window.prepare_shutdown(wait_msecs=5000)
            window.close()
            application.processEvents()
            coordinator.shutdown(wait_msecs=5000)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=21)
    parser.add_argument("--width", type=int, default=4096)
    parser.add_argument("--height", type=int, default=6500)
    parser.add_argument("--viewport-width", type=int, default=3840)
    parser.add_argument("--viewport-height", type=int, default=2106)
    parser.add_argument("--cache-mib", type=int, default=512)
    parser.add_argument("--fixture-mode", choices=("flat", "high-detail"), default="flat")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--zip-compression",
        choices=("deflated", "stored"),
        default="deflated",
    )
    parser.add_argument(
        "--archive-padding-mib",
        type=int,
        default=0,
        help=(
            "Add ignored stored bytes to isolate physical archive size from "
            "page dimensions and count."
        ),
    )
    parser.add_argument("--immediate-target", type=int, default=5)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use a compact 13-page fixture for maintained smoke verification.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.quick:
        args.pages = 13
        args.width = 900
        args.height = 1350
        args.viewport_width = 640
        args.viewport_height = 420
        args.cache_mib = 256
    if args.pages < 8:
        parser.error("--pages must be at least 8")
    if min(
        args.width,
        args.height,
        args.viewport_width,
        args.viewport_height,
        args.cache_mib,
    ) <= 0:
        parser.error("sizes and cache budget must be positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.archive_padding_mib < 0:
        parser.error("--archive-padding-mib must not be negative")
    if args.immediate_target <= 0:
        parser.error("--immediate-target must be positive")
    return args


def main() -> int:
    args = _parse_args()
    report = run_benchmark(
        pages=int(args.pages),
        image_size=(int(args.width), int(args.height)),
        viewport_size=(int(args.viewport_width), int(args.viewport_height)),
        cache_mib=int(args.cache_mib),
        timeout=float(args.timeout),
        compression=(
            ZIP_STORED
            if args.zip_compression == "stored"
            else ZIP_DEFLATED
        ),
        padding_bytes=int(args.archive_padding_mib) * 1024 * 1024,
        immediate_target=int(args.immediate_target),
        fixture_mode=args.fixture_mode,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output is not None:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
