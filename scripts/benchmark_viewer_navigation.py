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
from pathlib import Path
import statistics
import sys
from tempfile import TemporaryDirectory
import time
from typing import Callable
from zipfile import ZIP_DEFLATED, ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from PIL import Image
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from app.config_manager import ConfigManager
from app.image_work_coordinator import ImageWorkCoordinator
from app.raster_book_runtime import RasterBookRuntime
from app.viewer_memory_policy import viewer_memory_mode_from_legacy_mib
from app.viewer_window import ViewerWindow


def _jpeg_payload(size: tuple[int, int], page: int) -> bytes:
    output = io.BytesIO()
    color = (
        32 + page * 17 % 190,
        48 + page * 29 % 176,
        64 + page * 41 % 160,
    )
    with Image.new("RGB", size, color) as image:
        image.save(output, "JPEG", quality=88, subsampling=2)
    return output.getvalue()


def _build_zip(
    root: Path,
    *,
    pages: int,
    image_size: tuple[int, int],
) -> tuple[Path, str, int]:
    archive = root / "日本語-navigation-benchmark.zip"
    digest = hashlib.sha256()
    total_bytes = 0
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
        for page in range(pages):
            payload = _jpeg_payload(image_size, page)
            name = f"ページ {page:03d}.jpg"
            output.writestr(name, payload)
            digest.update(name.encode("utf-8"))
            digest.update(payload)
            total_bytes += len(payload)
    return archive, digest.hexdigest(), total_bytes


def _pump_until(
    application: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise TimeoutError("offscreen ZIP navigation benchmark timed out")


def _render_once(window: ViewerWindow) -> None:
    target = QPixmap(window.viewer.size())
    target.fill()
    window.viewer.render(target)


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
) -> dict[str, object]:
    application = QApplication.instance() or QApplication([])
    with TemporaryDirectory(prefix="nivis-current-zip-benchmark-") as temporary:
        root = Path(temporary)
        archive, fixture_sha256, payload_bytes = _build_zip(
            root,
            pages=pages,
            image_size=image_size,
        )
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
        window = ViewerWindow(
            config_manager=config,
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

            _wait_for_runtime_quiet(
                application,
                window,
                runtime,
                timeout=timeout,
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
                "schema_version": 2,
                "benchmark": "current RasterBookRuntime ZIP navigation",
                "qt_platform": "offscreen",
                "production_authority": {
                    "runtime_type": type(runtime).__name__,
                    "viewer_active": bool(window._zip_runtime_active),
                    "legacy_paths": "retired; no benchmark-only Viewer path override",
                },
                "fixture": {
                    "pages": pages,
                    "image_size": list(image_size),
                    "viewport_size": list(viewport_size),
                    "payload_bytes": payload_bytes,
                    "sha256": fixture_sha256,
                },
                "initial_open_to_paint_ms": initial_paint_ms,
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
    parser.add_argument("--timeout", type=float, default=30.0)
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
    return args


def main() -> int:
    args = _parse_args()
    report = run_benchmark(
        pages=int(args.pages),
        image_size=(int(args.width), int(args.height)),
        viewport_size=(int(args.viewport_width), int(args.viewport_height)),
        cache_mib=int(args.cache_mib),
        timeout=float(args.timeout),
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output is not None:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
