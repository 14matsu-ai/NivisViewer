"""Compare bounded raster overlap on a ZIP, recording timings only.

Never shows, exports, saves, classifies, or OCRs source images. File/member
names and pixels are excluded from the report. Decoding is solely for timing.
OS file cache can be warm; each pass creates an empty application cache.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
from time import perf_counter, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from app.image_source import ZipImageSource
from app.image_work_coordinator import ImageWorkCoordinator
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
from app.zip_raster_book_runtime import (
    ZipRasterBookRuntime, ZipRasterDisplayUnit, ZipRasterPage,
    ZipRasterRenderSpec, ZipRasterRequest,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--pages", type=int, default=12)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare-read-ahead", action="store_true")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    def until(predicate):
        end = perf_counter() + 30
        while not predicate():
            if perf_counter() >= end:
                raise TimeoutError("raster timing run did not complete")
            app.processEvents()
            sleep(0.001)
    reports = []
    for repeat in range(args.repeats):
        variants = ((1, False), (1, True)) if args.compare_read_ahead else ((1, False), (2, False))
        for workers, read_ahead in (variants if repeat % 2 == 0 else tuple(reversed(variants))):
            source = ZipImageSource(args.archive)
            ids = source.list_images()[:args.pages]
            with ThreadPoolExecutor(1) as probe:
                sizes = list(probe.map(source.probe_image_size, ids))
            units = tuple(ZipRasterDisplayUnit(i, (ZipRasterPage(i, name, size),), True)
                for i, (name, size) in enumerate(zip(ids, sizes)))
            topology = RasterBookTopology(units, identity_of=lambda u: u.identity,
                page_indexes_of=lambda u: (p.page_index for p in u.pages), page_count=len(units))
            spec = ZipRasterRenderSpec((2560, 1440), decoder_maximum_size=(2560, 1440), decoder_layout_sized=True)
            coordinator = ImageWorkCoordinator(folder_supplemental_workers=workers - 1)
            runtime = ZipRasterBookRuntime(source, 1, max_active_jobs=workers,
                zip_read_ahead=read_ahead,
                image_work_coordinator=coordinator, cache_byte_budget=512 * 1024 * 1024)
            frames = []
            runtime.frameReady.connect(frames.append)
            serial = 0
            previous_index = 0
            def request(index, background=True):
                nonlocal serial, previous_index
                serial += 1
                direction = -1 if index < previous_index else 1
                previous_index = index
                unit = units[index]
                plan = RasterWarmupPlan(topology, current=unit, identity_of=lambda u: u.identity,
                    page_indexes_of=lambda u: (p.page_index for p in u.pages), direction=direction,
                    background_enabled=background)
                runtime.request(ZipRasterRequest(1, serial, unit, plan, spec, navigation_direction=direction))
            def drain():
                runtime.cancel(clear_artifacts=True)
                until(lambda: not runtime.has_unfinished_tasks())
            try:
                start = perf_counter()
                request(0)
                until(lambda: frames and frames[-1].request_id == serial)
                first_ms = (perf_counter() - start) * 1000
                runtime.release_continuous_warmup(request_id=serial)
                until(lambda: runtime.cached_unit_count == len(units) and not runtime.has_unfinished_tasks())
                warmup_ms = (perf_counter() - start) * 1000
                row = {"workers": workers, "repeat": repeat, "pages": len(units),
                    "read_ahead_enabled": read_ahead,
                    "first_ms": round(first_ms, 2), "all_ready_ms": round(warmup_ms, 2)}
                # Real cold-cache sequences; only the most recent request
                # may publish after a rapid reversal/direct-seek burst.
                sequences = {"forward": (0, 1, 2, 3), "reverse": (3, 2, 1, 0),
                    "roundtrip": (0, 1, 2, 1, 0), "rapid": (0, 4, 2, 5, 1, 3)}
                for name, indexes in sequences.items():
                    if max(indexes) >= len(units):
                        continue
                    drain()
                    jobs_before = runtime.metrics.jobs_submitted
                    ahead_before = source._read_ahead.starts if source._read_ahead else 0
                    start = perf_counter()
                    for index in indexes:
                        request(index, background=args.compare_read_ahead)
                        if name != "rapid":
                            until(lambda: frames[-1].request_id == serial)
                            if args.compare_read_ahead:
                                runtime.release_continuous_warmup(request_id=serial)
                        elif args.compare_read_ahead:
                            app.processEvents()
                            sleep(0.008)
                    until(lambda: frames[-1].request_id == serial)
                    assert frames[-1].unit.start_index == indexes[-1]
                    row[name + "_ms"] = round((perf_counter() - start) * 1000, 2)
                    row[name + "_jobs"] = runtime.metrics.jobs_submitted - jobs_before
                    row[name + "_encoded_reads"] = (source._read_ahead.starts if source._read_ahead else 0) - ahead_before
                row["terminal_errors"] = runtime.metrics.terminal_errors
                reports.append(row)
            finally:
                drain()
                assert runtime.shutdown()
                assert coordinator.shutdown()
                source.close()
    args.output.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(json.dumps(reports))


if __name__ == "__main__":
    main()
