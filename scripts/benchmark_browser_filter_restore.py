"""Synthetic model-only filter/clear timings; no disk scan or native window."""
import json
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication
from app.browser_filter import BrowserFilterState
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel


def main():
    app = QApplication.instance() or QApplication([])
    items = [
        BrowserItem(f'item{i:05}.zip', Path(f'C:/synthetic/item{i:05}.zip'),
                    BrowserItemKind.ARCHIVE, 0)
        for i in range(30000)
    ]
    result = {'items': len(items), 'scope': 'model only; excludes layout, paint and thumbnails'}
    for enabled in (False, True):
        model = BrowserItemModel()
        model.configure_filter_restore_cache(enabled=enabled, max_entries=60000)
        model.set_sorted_items(items)
        filter_ms, clear_ms = [], []
        for _ in range(5):
            start = perf_counter()
            model.configure_filter(BrowserFilterState.normalized(search_text='001'))
            filter_ms.append((perf_counter() - start) * 1000)
            start = perf_counter()
            model.configure_filter(BrowserFilterState.normalized())
            clear_ms.append((perf_counter() - start) * 1000)
            assert model.items == tuple(items)
        result['enabled' if enabled else 'disabled'] = {
            'filter_median_ms': round(median(filter_ms), 3),
            'clear_median_ms': round(median(clear_ms), 3),
        }
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
