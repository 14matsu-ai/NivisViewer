from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from PIL import Image
from PySide6.QtWidgets import QApplication

from app.application_controller import ApplicationController
from app.config_manager import ConfigManager
from app.performance_trace import performance_trace


def _make_webp(path: Path, size: tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, "#496a8c") as image:
        image.save(path, "WEBP", quality=82, method=1)


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    performance_trace.enabled = True
    performance_trace.clear()
    with TemporaryDirectory(prefix="nivisviewer-first-frame-") as temporary:
        root = Path(temporary)
        folder = root / "日本語 WebP 多数"
        for index in range(80):
            _make_webp(folder / f"{index:03d}.webp", (320, 480))
        target = folder / "040.webp"
        _make_webp(target, (3840, 2160))

        config = ConfigManager(root / "config.json")
        config.load()
        config.apply(
            {
                "last_browser_path": str(folder),
                "single_first_page": False,
            },
            save=True,
        )
        controller = ApplicationController(
            application,
            config_manager=config,
        )
        browser = controller.start()
        browser.wait_for_scan(5000)
        browser._request_visible_thumbnails()
        application.processEvents()
        row = browser.item_model.row_for_path(target)
        browser.open_item(browser.item_model.index(row, 0))
        operation_id = performance_trace.latest_operation_id

        deadline = monotonic() + 10
        while monotonic() < deadline:
            application.processEvents()
            names = {
                event.name
                for event in performance_trace.events_for(operation_id)
            }
            if "viewer.first_paint.completed" in names:
                break

        events = performance_trace.events_for(operation_id)
        origin = events[0].timestamp if events else 0.0
        output = {
            "operation_id": operation_id,
            "browser_pending_at_open": next(
                (
                    event.detail
                    for event in events
                    if event.name == "application_controller.open_path.started"
                ),
                "",
            ),
            "timeline_ms": [
                {
                    "event": event.name,
                    "at_ms": round((event.timestamp - origin) * 1000, 3),
                    "detail": event.detail,
                }
                for event in events
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))

        for viewer in tuple(controller.viewer_windows):
            viewer.close()
        browser.close()
        application.processEvents()
        controller.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
