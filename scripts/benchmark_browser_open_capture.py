"""Full initial-open capture A/B against the ACCEPTED post-index baseline.

Uses production Browser/model methods with a fake handler and prebuilt items;
never opens an image, scans a directory, or launches the application.
"""
import json
from pathlib import Path
import platform

from PySide6.QtWidgets import QApplication

from app.browser_filter import BrowserFilterState
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_sort import BrowserSortKey, BrowserSortOrder
from app.browser_window import BrowserWindow
from scripts.benchmark_browser_snapshot_index import _median_ms, _memory


class _CaptureHarness:
    adjacent_book_snapshot = BrowserWindow.adjacent_book_snapshot
    _folder_snapshot_for_item = BrowserWindow._folder_snapshot_for_item
    _folder_snapshot_for_path = BrowserWindow._folder_snapshot_for_path
    _invoke_open_path_handler = BrowserWindow._invoke_open_path_handler

    def __init__(self, items):
        self.current_path = Path("C:/Books")
        self._scan_generation = 17
        self._pending_scan = None
        self.browser_sort_key = BrowserSortKey.RATING
        self.browser_sort_order = BrowserSortOrder.DESCENDING
        self.browser_folders_first = True
        self.browser_show_hidden_items = False
        self.browser_show_unsupported_files = False
        self.browser_show_system_items = False
        self.browser_filter_state = BrowserFilterState.normalized(
            search_text="keep", rating_mode="at_least", rating_reference=3,
        )
        self.item_model = BrowserItemModel()
        self.item_model.configure_sort("rating", "descending", True)
        self.item_model.configure_filter(self.browser_filter_state)
        self.item_model.set_items(items)
        self.pipeline_calls = 0

    def _visible_order_snapshot_items(self):
        self.pipeline_calls += 1
        return BrowserWindow._visible_order_snapshot_items(self)

    def _open_path_handler(self, path, new, folder, mixed):
        return folder, mixed


def run_benchmark():
    application = QApplication.instance() or QApplication([])
    rows = []
    for count in (1000, 10000, 50000):
        items = tuple(
            BrowserItem(
                f"{'keep' if i % 7 else 'drop'}日本語{i}.{'jpg' if i % 2 else 'zip'}",
                Path(f"C:/Books/{i}.{'jpg' if i % 2 else 'zip'}"),
                BrowserItemKind.IMAGE if i % 2 else BrowserItemKind.ARCHIVE,
                None, file_size=123, modified_time_ns=456, rating=i % 5 + 1,
            ) for i in reversed(range(count))
        )
        browser = _CaptureHarness(items)
        selected = next(item for item in browser.item_model.items if item.kind is BrowserItemKind.IMAGE)

        def before():
            # Previous open_item explicitly prepared BOTH snapshots separately.
            # Crucially, mixed snapshot still uses the accepted derived index.
            folder = browser._folder_snapshot_for_item(selected)
            mixed = browser.adjacent_book_snapshot(browser.current_path)
            return browser._invoke_open_path_handler(str(selected.path), False, folder, mixed)

        def after():
            return browser._invoke_open_path_handler(str(selected.path), False, snapshot_item=selected)

        assert before() == after()
        row = {"source_items": count, "visible_items": browser.item_model.rowCount()}
        for name, operation in (("before", before), ("after", after)):
            browser.pipeline_calls = 0
            result = operation()
            row[name] = {
                "pipeline_calls_per_open": browser.pipeline_calls,
                "preparation_median_ms": _median_ms(operation),
                "allocations": _memory(operation),
                "image_count": len(result[0].image_ids),
                "mixed_count": len(result[1].entries),
            }
        rows.append(row)
    return {
        "python": platform.python_version(), "qt_platform": application.platformName(),
        "baseline": "accepted snapshot-index implementation plus original two-capture open_item; not pre-index baseline",
        "method": "same prebuilt items; rating descending/folders-first, keep search, rating>=3; 7 medians, separate tracemalloc; both snapshot constructors and handler dispatch included",
        "limits": "excludes model setup/shared input storage, file open/decode and real UI latency",
        "results": rows,
    }


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), indent=2))
