from __future__ import annotations

import argparse
import atexit
import io
import json
import os
import statistics
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile

from PIL import Image
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from app.config_manager import ConfigManager
from app.viewer_window import ViewerWindow


def _jpeg_bytes(size: tuple[int, int]) -> bytes:
    output = io.BytesIO()
    with Image.new("RGB", size, "#496a8c") as image:
        image.save(output, "JPEG", quality=92, subsampling=0)
    return output.getvalue()


def _make_zip(path: Path, *, pages: int, size: tuple[int, int]) -> None:
    payload = _jpeg_bytes(size)
    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as archive:
        for index in range(pages):
            archive.writestr(f"日本語ページ/{index:03d}.jpg", payload)


def _pump_until(
    application: QApplication,
    predicate,
    *,
    timeout_seconds: float = 15.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise TimeoutError("offscreen Viewer benchmark timed out")


def _render_once(window: ViewerWindow) -> None:
    window.viewer.render(QPixmap(window.viewer.size()))


def _wait_for_page(
    application: QApplication,
    window: ViewerWindow,
    page_index: int,
) -> None:
    _pump_until(
        application,
        lambda: window.viewer.displayed_page_indexes == (page_index,),
    )
    _render_once(window)


def _wait_for_warm_window(
    application: QApplication,
    window: ViewerWindow,
    *,
    minimum_prepared_units: int,
) -> None:
    _pump_until(
        application,
        lambda: (
            not window.image_cache.has_unfinished_tasks()
            and not window.viewer._render_tasks
            and len(window.viewer._prepared_units) >= minimum_prepared_units
        ),
        timeout_seconds=30.0,
    )
    application.processEvents()


def _move_and_measure(
    application: QApplication,
    window: ViewerWindow,
    move,
    expected_page: int,
) -> tuple[float, float]:
    started = time.perf_counter()
    move()
    handler_finished = time.perf_counter()
    _pump_until(
        application,
        lambda: window.viewer.displayed_page_indexes == (expected_page,),
    )
    _render_once(window)
    painted_at = time.perf_counter()
    return (
        (handler_finished - started) * 1000,
        (painted_at - started) * 1000,
    )


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "median_ms": round(statistics.median(values), 3),
        "max_ms": round(max(values), 3),
    }


def run_benchmark(
    *,
    pages: int = 21,
    image_size: tuple[int, int] = (4096, 6500),
    viewport_size: tuple[int, int] = (3840, 2106),
    use_target_decode: bool = True,
) -> dict[str, object]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    with TemporaryDirectory(prefix="nivisviewer-navigation-") as temporary:
        root = Path(temporary)
        archive_path = root / "日本語 大画像.zip"
        _make_zip(archive_path, pages=pages, size=image_size)
        config = ConfigManager(root / "config.json")
        config.load()
        config.apply(
            {
                "view_mode": "single",
                "single_first_page": False,
                "viewer_resampling_mode": "standard",
                "fit_mode": "fit_window",
                "viewer_prefetch_preset": "custom",
                "viewer_prefetch_direction_priority_enabled": True,
                "viewer_prefetch_image_forward_units": 6,
                "viewer_prefetch_image_backward_units": 4,
                "viewer_cache_max_memory_mib": 512,
                "show_page_list": False,
            },
            save=True,
        )
        window = ViewerWindow(config_manager=config)
        shutdown_complete = False

        def shutdown_window() -> None:
            nonlocal shutdown_complete
            if shutdown_complete:
                return
            shutdown_complete = True
            window.prepare_shutdown(wait_msecs=5000)
            window.close()
            application.processEvents()

        atexit.register(shutdown_window)
        if not use_target_decode:
            window._current_raster_decode_bounds = lambda: None  # type: ignore[method-assign]
        window.resize(*viewport_size)
        application.processEvents()
        window.open_path(archive_path)
        if not window.book_session.wait_for_async(10_000):
            raise TimeoutError("book source preparation timed out")
        application.processEvents()
        center = pages // 2
        window._go_to_index_with_history(center)
        _wait_for_page(application, window, center)
        _wait_for_warm_window(
            application,
            window,
            minimum_prepared_units=min(
                pages,
                11 if use_target_decode else 5,
            ),
        )

        forward_handlers: list[float] = []
        forward_paints: list[float] = []
        for target in range(center + 1, center + 6):
            handler, paint = _move_and_measure(
                application,
                window,
                window.next_page,
                target,
            )
            forward_handlers.append(handler)
            forward_paints.append(paint)

        reverse_handlers: list[float] = []
        reverse_paints: list[float] = []
        for target in range(center + 4, center - 1, -1):
            handler, paint = _move_and_measure(
                application,
                window,
                window.previous_page,
                target,
            )
            reverse_handlers.append(handler)
            reverse_paints.append(paint)

        roundtrip_handlers: list[float] = []
        roundtrip_paints: list[float] = []
        for move, target in (
            (window.next_page, center + 1),
            (window.previous_page, center),
        ) * 4:
            handler, paint = _move_and_measure(
                application,
                window,
                move,
                target,
            )
            roundtrip_handlers.append(handler)
            roundtrip_paints.append(paint)

        window._go_to_index_with_history(center)
        _wait_for_page(application, window, center)
        application.processEvents()
        rapid_target = min(pages - 1, center + 8)
        rapid_started = time.perf_counter()
        rapid_handlers: list[float] = []
        for _target in range(center + 1, rapid_target + 1):
            handler_started = time.perf_counter()
            window.next_page()
            rapid_handlers.append(
                (time.perf_counter() - handler_started) * 1000
            )
        rapid_handler_finished = time.perf_counter()
        _wait_for_page(application, window, rapid_target)
        rapid_finished = time.perf_counter()

        cache_snapshot = {
            "decoded_entries": len(window.image_cache._cache),
            "decoded_mib": round(
                window.image_cache._cache_bytes / (1024 * 1024),
                2,
            ),
            "prepared_units": len(window.viewer._prepared_units),
            "prepared_pixmaps": len(window.viewer._render_cache),
            "preview_sources": sum(
                cached.source_is_preview
                for cached in window.image_cache._cache.values()
            ),
        }
        result = {
            "source": {
                "pages": pages,
                "image_size": list(image_size),
                "zip_bytes": archive_path.stat().st_size,
                "viewport_size": list(viewport_size),
                "target_decode": use_target_decode,
            },
            "ready_forward": {
                "handler": _summary(forward_handlers),
                "request_to_paint": _summary(forward_paints),
            },
            "ready_reverse": {
                "handler": _summary(reverse_handlers),
                "request_to_paint": _summary(reverse_paints),
            },
            "ready_roundtrip": {
                "handler": _summary(roundtrip_handlers),
                "request_to_paint": _summary(roundtrip_paints),
            },
            "rapid_frontier": {
                "requests": len(rapid_handlers),
                "handler": _summary(rapid_handlers),
                "handlers_total_ms": round(
                    (rapid_handler_finished - rapid_started) * 1000,
                    3,
                ),
                "request_to_final_paint_ms": round(
                    (rapid_finished - rapid_started) * 1000,
                    3,
                ),
                "final_page": rapid_target,
            },
            "cache": cache_snapshot,
        }
        shutdown_window()
        atexit.unregister(shutdown_window)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offscreen large-ZIP Viewer navigation benchmark."
    )
    parser.add_argument("--pages", type=int, default=21)
    parser.add_argument("--width", type=int, default=4096)
    parser.add_argument("--height", type=int, default=6500)
    parser.add_argument(
        "--full-decode",
        action="store_true",
        help="Disable the display-bound JPEG decode fast path.",
    )
    arguments = parser.parse_args()
    result = run_benchmark(
        pages=max(13, arguments.pages),
        image_size=(
            max(64, arguments.width),
            max(64, arguments.height),
        ),
        use_target_decode=not arguments.full_decode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
