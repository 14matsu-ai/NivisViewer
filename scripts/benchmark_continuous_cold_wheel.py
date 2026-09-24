"""Numeric-only offscreen comparison of wait-for-cache and continuous wheel.

Reads images for timing only; never shows a window, emits pixels, exports
source images, or reports source/member names. Settings live in a temp folder.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication
from app.config_manager import ConfigManager
from app.image_work_coordinator import ImageWorkCoordinator
from app.viewer_window import ViewerWindow
from app.zip_raster_book_runtime import _ZipRasterUnitJob


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--interval-ms", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-read-ahead", action="store_true")
    parser.add_argument("--compare-read-ahead", action="store_true")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    original_run = _ZipRasterUnitJob.run
    original_decode = _ZipRasterUnitJob._decode_page
    rows = []
    for repeat in range(args.repeats):
        modes = ("wait", "continuous") if repeat % 2 == 0 else ("continuous", "wait")
        enabled_values = ((False, True) if repeat % 2 == 0 else (True, False)) if args.compare_read_ahead else (not args.no_read_ahead,)
        for mode, enabled in ((mode, enabled) for mode in modes for enabled in enabled_values):
            jobs = []
            decodes = []
            def run(job):
                start = perf_counter()
                try:
                    return original_run(job)
                finally:
                    jobs.append((start, perf_counter()))
            def decode(job, page):
                start = perf_counter()
                result = original_decode(job, page)
                decodes.append((page.page_index, (perf_counter() - start) * 1000, result.qimage is not None))
                return result
            _ZipRasterUnitJob.run = run
            _ZipRasterUnitJob._decode_page = decode
            with TemporaryDirectory(prefix="nivis-wheel-timing-") as temp:
                config = ConfigManager(Path(temp) / "config.json")
                config.load()
                config.apply({"view_mode": "single", "single_first_page": False,
                    "fit_mode": "fit_window", "viewer_memory_mode": "4096", "show_page_list": False})
                coordinator = ImageWorkCoordinator()
                window = ViewerWindow(config_manager=config, image_work_coordinator=coordinator)
                window.book_session._zip_read_ahead_enabled = enabled
                window.resize(2560, 1440)
                window.ensurePolished()
                window.layout().activate()
                empty = QPixmap(window.size())
                window.render(empty)
                app.processEvents()
                del empty
                paint_needed = [False]
                window.presentationCommitted.connect(lambda _commit: paint_needed.__setitem__(0, True))
                def pump():
                    app.processEvents()
                    if paint_needed[0]:
                        paint_needed[0] = False
                        target = QPixmap(window.viewer.size())
                        window.viewer.render(target)
                    sleep(0.0005)
                def until(predicate):
                    deadline = perf_counter() + 60
                    while not predicate():
                        if perf_counter() >= deadline:
                            raise TimeoutError("offscreen wheel timing did not finish")
                        pump()
                try:
                    opened = perf_counter()
                    assert window.open_path(args.path)
                    until(lambda: window.presentation_state.displayed_page == 0)
                    runtime = window.book_session.viewer_runtime
                    assert runtime is not None
                    pages = window.model.total_pages
                    first_ms = (perf_counter() - opened) * 1000
                    if mode == "wait":
                        until(lambda: len(runtime.cached_page_indexes) == pages and not runtime.has_unfinished_tasks())
                    scrolling = perf_counter()
                    next_input = scrolling
                    packets = 0
                    held = 0
                    deadline = scrolling + 60
                    while window.presentation_state.displayed_page != pages - 1:
                        now = perf_counter()
                        if now > deadline:
                            raise TimeoutError("continuous wheel did not reach end")
                        if now >= next_input:
                            before = window.model.focused_index
                            event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, -120),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
                            event.setTimestamp(int(now * 1000))
                            QApplication.sendEvent(window.viewer, event)
                            packets += 1
                            held += int(before == window.model.focused_index)
                            next_input = now + args.interval_ms / 1000
                        pump()
                    finished = perf_counter()
                    counts = Counter(index for index, _ms, _ok in decodes)
                    ahead = getattr(window.book_session.source, "_read_ahead", None)
                    rows.append({"mode": mode, "repeat": repeat, "pages": pages,
                        "read_ahead_enabled": enabled,
                        "read_ahead_hits": ahead.hits if ahead else 0, "read_ahead_starts": ahead.starts if ahead else 0,
                        "first_ms": round(first_ms, 2), "total_ms": round((finished - opened) * 1000, 2),
                        "scroll_ms": round((finished - scrolling) * 1000, 2), "packets": packets, "held": held,
                        "decode_calls": len(decodes), "duplicate_decodes": sum(max(0, n - 1) for n in counts.values()),
                        "decode_ms": round(sum(ms for _i, ms, _ok in decodes), 2),
                        "job_ms": round(sum((end - start) * 1000 for start, end in jobs), 2),
                        "metrics": asdict(runtime.metrics)})
                finally:
                    window.prepare_shutdown(wait_msecs=10000)
                    window.close()
                    app.processEvents()
                    assert coordinator.shutdown(wait_msecs=10000)
            _ZipRasterUnitJob.run = original_run
            _ZipRasterUnitJob._decode_page = original_decode
    args.output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps(rows))


if __name__ == "__main__":
    main()
