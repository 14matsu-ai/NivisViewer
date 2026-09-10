"""Bounded random sort + production single-open capture; no files or GUI input."""
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from PySide6.QtWidgets import QApplication

from app.browser_filter import BrowserFilterState
from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_sort import BrowserSortKey, BrowserSortPolicy
from scripts.benchmark_browser_open_capture import _CaptureHarness


def measure(operation):
    elapsed = []
    for _ in range(3):
        start = perf_counter()
        operation()
        elapsed.append((perf_counter() - start) * 1000)
    return round(median(elapsed), 3)


def run_benchmark():
    application = QApplication.instance() or QApplication([])
    rows = []
    for count in (1000, 10000, 50000):
        values = tuple(
            BrowserItem(f"本{i}.jpg", Path(f"C:/Books/本{i}.jpg"),
                        BrowserItemKind.IMAGE, None, rating=4)
            for i in range(count)
        )
        policy = BrowserSortPolicy(BrowserSortKey.RANDOM, random_seed=123456)
        browser = _CaptureHarness(values)
        browser.browser_sort_key = BrowserSortKey.RANDOM
        browser.browser_random_seed = 123456
        browser.browser_filter_state = BrowserFilterState()
        browser.item_model.configure_filter(browser.browser_filter_state)
        browser.item_model.configure_sort("random", "ascending", True, 123456)
        selected = browser.item_model.items[count // 2]

        def capture():
            browser.pipeline_calls = 0
            folder, mixed = browser._invoke_open_path_handler(
                str(selected.path), False, snapshot_item=selected,
            )
            assert browser.pipeline_calls == 1
            assert len(folder.image_ids) == len(mixed.entries) == count
            assert folder.image_ids == mixed.image_paths

        rows.append({"items": count,
                     "sort_median_ms": measure(lambda: policy.sorted_items(values)),
                     "single_open_capture_median_ms": measure(capture)})
    return {"qt_platform": application.platformName(), "results": rows,
            "method": "3-run medians; prebuilt items; sort and both production snapshots; no I/O/decode",
            "limits": "Synthetic timing, not native UI latency; setup excluded; no retained random list/cache"}


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), indent=2))
