"""Compare initial raster warm-up and the first ten page turns offscreen.

The script can load NivisViewer from an extracted historical tree through
``--target-root``.  This keeps an A/B read-only: callers may use ``git archive``
for the baseline and pass the exact same temporary ZIP to both processes.
No Viewer window, native input, or external GUI application is created.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from threading import Lock
from time import monotonic, sleep
import zipfile


def _create_fixture(path: Path, *, pages: int, width: int, height: int) -> None:
    from PIL import Image, ImageDraw

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for index in range(pages):
            image = Image.new(
                "RGB",
                (width, height),
                (
                    28 + index * 17 % 180,
                    36 + index * 29 % 170,
                    44 + index * 37 % 160,
                ),
            )
            draw = ImageDraw.Draw(image)
            for y in range(0, height, 48):
                colour = (
                    (index * 13 + y // 3) % 256,
                    (index * 19 + y // 5) % 256,
                    (index * 23 + y // 7) % 256,
                )
                draw.line((0, y, width, height - y // 2), fill=colour, width=3)
            for x in range(0, width, 64):
                draw.line(
                    (x, 0, width - x // 2, height),
                    fill=(220 - index % 80, 90 + x % 120, 150),
                    width=2,
                )
            payload = io.BytesIO()
            image.save(payload, "JPEG", quality=88, optimize=False)
            image.close()
            info = zipfile.ZipInfo(f"{index:04d}.jpg")
            info.date_time = (2020, 1, 1, 0, 0, 0)
            archive.writestr(info, payload.getvalue())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _run(args: argparse.Namespace) -> dict[str, object]:
    target_root = Path(args.target_root).resolve()
    sys.path.insert(0, str(target_root))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QEventLoop
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from app import zip_raster_book_runtime as runtime_module
    from app.image_source import ZipImageSource
    from app.raster_warmup_planner import (
        RasterBookTopology,
        RasterWarmupPlan,
        RasterWarmupPlanner,
    )
    from app.zip_raster_book_runtime import (
        ZipRasterBookRuntime,
        ZipRasterDisplayUnit,
        ZipRasterPage,
        ZipRasterRenderSpec,
        ZipRasterRequest,
    )

    class CountingSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.counter_lock = Lock()
            self.decode_attempts = 0
            self.successful_decodes: Counter[str] = Counter()
            self.entry_reads = 0
            self.entry_bytes = 0
            self.entry_read_calls = 0
            self.decode_attempt_order: list[str] = []
            self.successful_decode_order: list[str] = []

        def _read_entry_qbytearray(self, image_id, cancelled):
            payload, read_calls = super()._read_entry_qbytearray(
                image_id,
                cancelled,
            )
            with self.counter_lock:
                self.entry_reads += 1
                self.entry_bytes += int(payload.size())
                self.entry_read_calls += int(read_calls)
            return payload, read_calls

        def _read_entry_stream(self, image_id, cancelled):
            stream = super()._read_entry_stream(image_id, cancelled)
            with self.counter_lock:
                self.entry_reads += 1
                self.entry_bytes += int(stream.getbuffer().nbytes)
                self.entry_read_calls += 1
            return stream

        def open_compatible_jpeg_at_most(self, image_id, maximum_size):
            with self.counter_lock:
                self.decode_attempts += 1
                self.decode_attempt_order.append(str(image_id))
            decoded = super().open_compatible_jpeg_at_most(
                image_id,
                maximum_size,
            )
            if decoded is not None:
                with self.counter_lock:
                    self.successful_decodes[image_id] += 1
                    self.successful_decode_order.append(str(image_id))
            return decoded

        def open_image(self, image_id):
            with self.counter_lock:
                self.decode_attempts += 1
                self.decode_attempt_order.append(str(image_id))
            image = super().open_image(image_id)
            with self.counter_lock:
                self.successful_decodes[image_id] += 1
                self.successful_decode_order.append(str(image_id))
            return image

        def counters(self) -> dict[str, int]:
            with self.counter_lock:
                return {
                    "decode_attempts": int(self.decode_attempts),
                    "successful_decodes": int(sum(self.successful_decodes.values())),
                    "duplicate_successful_decodes": int(
                        sum(max(0, count - 1) for count in self.successful_decodes.values())
                    ),
                    "entry_reads": int(self.entry_reads),
                    "entry_bytes": int(self.entry_bytes),
                    "entry_read_calls": int(self.entry_read_calls),
                    "decode_attempt_order": list(self.decode_attempt_order),
                    "successful_decode_order": list(
                        self.successful_decode_order
                    ),
                }

    archive = Path(args.archive).resolve()
    page_names = tuple(f"{index:04d}.jpg" for index in range(args.pages))
    pages = tuple(
        ZipRasterPage(
            index,
            page_names[index],
            (args.width, args.height),
        )
        for index in range(args.pages)
    )
    if args.view_mode == "single":
        units = tuple(
            ZipRasterDisplayUnit(index, (page,), True)
            for index, page in enumerate(pages)
        )
    else:
        spread_units: list[ZipRasterDisplayUnit] = []
        for start_index in range(0, len(pages), 2):
            spread_pages = pages[start_index : start_index + 2]
            if args.reading_direction == "rtl" and len(spread_pages) == 2:
                spread_pages = tuple(reversed(spread_pages))
            spread_units.append(
                ZipRasterDisplayUnit(
                    start_index,
                    tuple(spread_pages),
                    len(spread_pages) == 1,
                )
            )
        units = tuple(spread_units)
    topology = RasterBookTopology(
        units,
        identity_of=lambda unit: unit.identity,
        page_indexes_of=lambda unit: (
            page.page_index for page in unit.pages
        ),
        page_count=args.pages,
    )
    spec = ZipRasterRenderSpec(
        (args.viewport_width, args.viewport_height),
        reading_direction=args.reading_direction,
        decoder_maximum_size=(args.viewport_width, args.viewport_height),
        decoder_headroom=1.0,
        decoder_layout_sized=args.decoder_layout_sized,
    )

    def request(request_id: int, unit_ordinal: int, direction: int = 1):
        current = units[unit_ordinal]
        plan = RasterWarmupPlan(
            topology,
            current=current,
            identity_of=lambda unit: unit.identity,
            page_indexes_of=lambda unit: (
                page.page_index for page in unit.pages
            ),
            direction=direction,
            background_enabled=True,
        )
        return ZipRasterRequest(
            1,
            request_id,
            current,
            plan,
            spec,
            navigation_direction=direction,
        )

    qapp = QApplication.instance() or QApplication([])
    planner_init_count = 0
    planner_refs: list[object] = []
    planner_candidates: list[object] = []
    planner_recenters: list[dict[str, object]] = []
    job_adoptions: list[dict[str, object]] = []
    original_planner_init = RasterWarmupPlanner.__init__
    original_next_candidate = RasterWarmupPlanner.next_candidate
    original_recenter = getattr(RasterWarmupPlanner, "recenter", None)
    job_type = getattr(runtime_module, "_ZipRasterUnitJob", None)
    original_job_adopt = (
        getattr(job_type, "adopt_request", None)
        if job_type is not None
        else None
    )

    def counted_planner_init(planner, *positional, **keyword):
        nonlocal planner_init_count
        planner_init_count += 1
        planner_refs.append(planner)
        original_planner_init(planner, *positional, **keyword)

    def counted_next_candidate(planner, *positional, **keyword):
        candidate = original_next_candidate(planner, *positional, **keyword)
        if candidate is not None:
            planner_candidates.append(candidate.identity)
        return candidate

    def counted_recenter(planner, plan):
        assert original_recenter is not None
        planner_recenters.append(
            {
                "anchor_ordinal": int(plan.anchor_ordinal),
                "current_identity": plan.current_identity,
                "direction": int(plan.direction),
            }
        )
        return original_recenter(planner, plan)

    def counted_job_adopt(job, request_id, *, as_current=False):
        assert original_job_adopt is not None
        job_adoptions.append(
            {
                "request_id": int(request_id),
                "as_current": bool(as_current),
                "unit_identity": job.key.unit_identity,
            }
        )
        return original_job_adopt(
            job,
            request_id,
            as_current=as_current,
        )

    RasterWarmupPlanner.__init__ = counted_planner_init
    RasterWarmupPlanner.next_candidate = counted_next_candidate
    if original_recenter is not None:
        RasterWarmupPlanner.recenter = counted_recenter
    if job_type is not None and original_job_adopt is not None:
        job_type.adopt_request = counted_job_adopt

    def pump_once() -> None:
        qapp.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 2)

    def wait_until(predicate, timeout: float) -> None:
        deadline = monotonic() + timeout
        while not predicate():
            if monotonic() >= deadline:
                raise TimeoutError("offscreen raster benchmark timed out")
            pump_once()
            sleep(0.0005)

    def release_commit(runtime, request_id: int) -> bool:
        release = getattr(runtime, "release_continuous_warmup", None)
        if release is None:
            release = runtime.release_startup_runway
        return bool(release(request_id=request_id))

    def paint_frame(frame) -> float:
        started = monotonic()
        surface = QImage(
            args.viewport_width,
            args.viewport_height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        surface.fill(0xFF101010)
        painter = QPainter(surface)
        try:
            for page in frame.pages:
                if page.pixmap is not None:
                    painter.drawPixmap(0, 0, page.pixmap)
        finally:
            painter.end()
        return (monotonic() - started) * 1000.0

    def ready_ahead(runtime, current_ordinal: int) -> int:
        count = 0
        for unit in units[current_ordinal + 1 :]:
            key = runtime._key_for(unit, spec)
            if key not in runtime._frame_store:
                break
            count += 1
        return count

    def optional_counter(metrics, name: str) -> int | None:
        value = getattr(metrics, name, None)
        return None if value is None else int(value)

    def instrumentation_delta(
        candidate_start: int,
        recenter_start: int,
        adoption_start: int,
    ) -> dict[str, object]:
        adoptions = job_adoptions[adoption_start:]
        return {
            "planner_candidate_order": planner_candidates[candidate_start:],
            "planner_recenter_order": planner_recenters[recenter_start:],
            "job_adoptions": len(adoptions),
            "current_job_adoptions": sum(
                bool(event["as_current"]) for event in adoptions
            ),
            "prefetch_job_adoptions": sum(
                not bool(event["as_current"]) for event in adoptions
            ),
            "job_adoption_order": adoptions,
        }

    def snapshot(runtime, current_index: int) -> dict[str, object]:
        debug = runtime.cache_debug_values()
        metrics = runtime.metrics
        return {
            "ready_ahead_units": ready_ahead(runtime, current_index),
            "ready_units": int(runtime.cached_unit_count),
            "cache_bytes": int(runtime.cache_bytes),
            "jobs": int(metrics.jobs_submitted),
            "cancel": int(metrics.cancel_requests),
            "stale": int(metrics.stale_results),
            "qpixmap_creations": optional_counter(metrics, "qpixmap_creations"),
            "compatible_old_results": optional_counter(
                metrics,
                "compatible_old_results",
            ),
            "worker_running": bool(runtime.active_job_count),
            "stop_reason": str(runtime.warmup_stop_reason),
            "runtime_ready_ahead": debug.get("ready_ahead_unit_count"),
            "runtime_planner_creations": debug.get("warmup_planner_creations"),
            "runtime_planner_recenters": debug.get("warmup_planner_recenters"),
        }

    class IdleMonitor:
        def __init__(self, runtime) -> None:
            self.runtime = runtime
            self.last = monotonic()
            self.last_idle = False
            self.idle_seconds = 0.0
            self.idle_events = 0
            runtime.idle.connect(self._on_idle)

        def _on_idle(self, _runtime) -> None:
            self.idle_events += 1

        def sample(self) -> None:
            now = monotonic()
            if self.last_idle:
                self.idle_seconds += now - self.last
            planner = getattr(self.runtime, "_warmup_planner", None)
            stop_reason = str(self.runtime.warmup_stop_reason)
            runnable = bool(
                planner is not None
                and planner.background_released
                and stop_reason not in {
                    "complete",
                    "complete_with_skips",
                    "soft_target",
                    "hard_limit",
                    "suspended",
                }
            )
            self.last_idle = bool(
                runnable and self.runtime.active_job_count == 0
            )
            self.last = now

        @property
        def milliseconds(self) -> float:
            self.sample()
            return self.idle_seconds * 1000.0

    def pump_until_time(deadline: float, monitor: IdleMonitor) -> None:
        while monotonic() < deadline:
            pump_once()
            monitor.sample()
            sleep(0.0005)
        pump_once()
        monitor.sample()

    def close_runtime(runtime, source) -> bool:
        runtime.cancel(clear_artifacts=True)
        runtime.wait_for_done(5000)
        deadline = monotonic() + 5.0
        while runtime.has_unfinished_tasks() and monotonic() < deadline:
            pump_once()
            sleep(0.0005)
        clean = bool(runtime.shutdown(wait_msecs=5000))
        source.close()
        pump_once()
        return clean

    def new_runtime():
        source = CountingSource(archive)
        runtime = ZipRasterBookRuntime(
            source,
            1,
            cache_byte_budget=args.cache_mib << 20,
            cache_soft_target_bytes=args.soft_target_mib << 20,
        )
        frames: list[object] = []
        runtime.frameReady.connect(frames.append)
        return source, runtime, frames

    try:
        population_start_count = planner_init_count
        population_candidate_start = len(planner_candidates)
        population_recenter_start = len(planner_recenters)
        population_adoption_start = len(job_adoptions)
        source, runtime, frames = new_runtime()
        first = request(1, 0)
        requested_at = monotonic()
        if not runtime.request(first):
            raise RuntimeError("runtime rejected initial population request")
        wait_until(lambda: bool(frames), args.timeout)
        committed_at = monotonic()
        first_frame_ms = (committed_at - requested_at) * 1000.0
        if not release_commit(runtime, 1):
            raise RuntimeError("runtime rejected first commit release")
        monitor = IdleMonitor(runtime)
        monitor.sample()
        population_snapshots: dict[str, object] = {
            "commit": snapshot(runtime, 0),
        }
        commit_deadline = committed_at
        paint_deadline = commit_deadline + args.paint_delay_ms / 1000.0
        pump_until_time(paint_deadline, monitor)
        paint_ms = paint_frame(frames[-1])
        if not runtime.release_prefetch(request_id=1):
            raise RuntimeError("runtime rejected paint acknowledgement")
        population_snapshots["paint"] = snapshot(runtime, 0)
        for milliseconds in (50, 100, 250):
            pump_until_time(commit_deadline + milliseconds / 1000.0, monitor)
            population_snapshots[f"{milliseconds}ms"] = snapshot(runtime, 0)
        population = {
            "first_frame_ms": round(first_frame_ms, 3),
            "offscreen_paint_ms": round(paint_ms, 3),
            "snapshots": population_snapshots,
            "planner_init_count": planner_init_count - population_start_count,
            "scheduler_idle_ms": round(monitor.milliseconds, 3),
            "idle_signal_count": int(monitor.idle_events),
            "qpixmap_creations": optional_counter(
                runtime.metrics,
                "qpixmap_creations",
            ),
            "compatible_old_results": optional_counter(
                runtime.metrics,
                "compatible_old_results",
            ),
            "runtime_planner_creations": optional_counter(
                runtime.metrics,
                "warmup_planner_creations",
            ),
            "runtime_planner_recenters": optional_counter(
                runtime.metrics,
                "warmup_planner_recenters",
            ),
            **instrumentation_delta(
                population_candidate_start,
                population_recenter_start,
                population_adoption_start,
            ),
            **source.counters(),
        }
        population["clean_shutdown"] = close_runtime(runtime, source)

        navigation_start_count = planner_init_count
        navigation_candidate_start = len(planner_candidates)
        navigation_recenter_start = len(planner_recenters)
        navigation_adoption_start = len(job_adoptions)
        source, runtime, frames = new_runtime()
        planner_objects: list[object] = []
        current_request = request(1, 0)
        nav_started_at = monotonic()
        if not runtime.request(current_request):
            raise RuntimeError("runtime rejected initial navigation request")
        planner_objects.append(runtime._warmup_planner)
        wait_until(lambda: bool(frames), args.timeout)
        first_nav_commit_at = monotonic()
        if not release_commit(runtime, 1):
            raise RuntimeError("runtime rejected navigation commit release")
        nav_monitor = IdleMonitor(runtime)
        nav_monitor.sample()
        pump_until_time(
            first_nav_commit_at + args.paint_delay_ms / 1000.0,
            nav_monitor,
        )
        paint_frame(frames[-1])
        runtime.release_prefetch(request_id=1)

        turns: list[dict[str, object]] = []
        for unit_ordinal in range(1, min(len(units), 11)):
            if args.turn_delay_ms > 0:
                pump_until_time(
                    monotonic() + args.turn_delay_ms / 1000.0,
                    nav_monitor,
                )
            request_id = unit_ordinal + 1
            current_request = request(request_id, unit_ordinal)
            ahead_before = ready_ahead(runtime, unit_ordinal - 1)
            predicted_hit = bool(runtime.has_cached_current(current_request))
            turn_started = monotonic()
            if not runtime.request(current_request):
                raise RuntimeError(
                    f"runtime rejected display unit {unit_ordinal}"
                )
            planner_objects.append(runtime._warmup_planner)
            wait_until(
                lambda expected=request_id: bool(frames)
                and frames[-1].request_id == expected,
                args.timeout,
            )
            committed = frames[-1]
            release_commit(runtime, request_id)
            paint_frame(committed)
            runtime.release_prefetch(request_id=request_id)
            nav_monitor.sample()
            turns.append(
                {
                    "page": int(current_request.current.start_index),
                    "unit_ordinal": unit_ordinal,
                    "page_indexes": [
                        page.page_index
                        for page in current_request.current.pages
                    ],
                    "predicted_hit": predicted_hit,
                    "frame_cache_hit": bool(committed.cache_hit),
                    "latency_ms": round((monotonic() - turn_started) * 1000.0, 3),
                    "ready_ahead_before": ahead_before,
                }
            )

        metrics = runtime.metrics
        navigation = {
            "first_frame_ms": round(
                (first_nav_commit_at - nav_started_at) * 1000.0,
                3,
            ),
            "turns": turns,
            "hits": sum(bool(turn["frame_cache_hit"]) for turn in turns),
            "misses": sum(not bool(turn["frame_cache_hit"]) for turn in turns),
            "final_target_latency_ms": turns[-1]["latency_ms"] if turns else None,
            "planner_init_count": planner_init_count - navigation_start_count,
            "unique_planner_objects": len({id(item) for item in planner_objects}),
            "runtime_planner_creations": getattr(
                metrics,
                "warmup_planner_creations",
                None,
            ),
            "runtime_planner_recenters": optional_counter(
                metrics,
                "warmup_planner_recenters",
            ),
            "jobs": int(metrics.jobs_submitted),
            "cancel": int(metrics.cancel_requests),
            "stale": int(metrics.stale_results),
            "qpixmap_creations": optional_counter(metrics, "qpixmap_creations"),
            "compatible_old_results": optional_counter(
                metrics,
                "compatible_old_results",
            ),
            "scheduler_idle_ms": round(nav_monitor.milliseconds, 3),
            "idle_signal_count": int(nav_monitor.idle_events),
            "elapsed_ms": round((monotonic() - nav_started_at) * 1000.0, 3),
            "final_ready_ahead": ready_ahead(runtime, min(len(units) - 1, 10)),
            **instrumentation_delta(
                navigation_candidate_start,
                navigation_recenter_start,
                navigation_adoption_start,
            ),
            **source.counters(),
        }
        navigation["clean_shutdown"] = close_runtime(runtime, source)
    finally:
        RasterWarmupPlanner.__init__ = original_planner_init
        RasterWarmupPlanner.next_candidate = original_next_candidate
        if original_recenter is not None:
            RasterWarmupPlanner.recenter = original_recenter
        if job_type is not None and original_job_adopt is not None:
            job_type.adopt_request = original_job_adopt

    return {
        "label": args.label,
        "target_root": str(target_root),
        "archive": str(archive),
        "archive_sha256": _sha256(archive),
        "pages": args.pages,
        "display_units": len(units),
        "view_mode": args.view_mode,
        "reading_direction": args.reading_direction,
        "decoder_layout_sized": args.decoder_layout_sized,
        "image_size": [args.width, args.height],
        "viewport": [args.viewport_width, args.viewport_height],
        "paint_delay_ms": args.paint_delay_ms,
        "turn_delay_ms": args.turn_delay_ms,
        "cache_mib": args.cache_mib,
        "soft_target_mib": args.soft_target_mib,
        "population": population,
        "navigation": navigation,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-root")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--label", default="worktree")
    parser.add_argument("--create-fixture", action="store_true")
    parser.add_argument("--pages", type=int, default=100)
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=2400)
    parser.add_argument("--viewport-width", type=int, default=400)
    parser.add_argument("--viewport-height", type=int, default=600)
    parser.add_argument(
        "--view-mode",
        choices=("single", "spread"),
        default="single",
    )
    parser.add_argument(
        "--reading-direction",
        choices=("ltr", "rtl"),
        default="ltr",
    )
    parser.add_argument("--decoder-layout-sized", action="store_true")
    parser.add_argument("--paint-delay-ms", type=int, default=20)
    parser.add_argument("--turn-delay-ms", type=int, default=0)
    parser.add_argument("--cache-mib", type=int, default=512)
    parser.add_argument("--soft-target-mib", type=int, default=448)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    archive = Path(args.archive).resolve()
    if args.create_fixture:
        _create_fixture(
            archive,
            pages=max(1, args.pages),
            width=max(1, args.width),
            height=max(1, args.height),
        )
        print(
            json.dumps(
                {
                    "archive": str(archive),
                    "sha256": _sha256(archive),
                    "bytes": archive.stat().st_size,
                },
                ensure_ascii=False,
            )
        )
        return 0
    if not args.target_root:
        raise SystemExit("--target-root is required unless --create-fixture is used")
    result = _run(args)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    if not args.quiet:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
