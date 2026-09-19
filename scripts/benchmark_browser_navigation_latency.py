"""Synthetic persisted-folder navigation and offscreen input responsiveness probe."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import sys
import threading
import uuid
from time import perf_counter, sleep
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.browser_window import BrowserWindow
from app.browser_model import BrowserItemModel
from app.browser_sort import BrowserSortPolicy
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider
from app.config_manager import ConfigManager
from tests.test_application_controller import make_controller, close_controller


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--delay-ms', type=int, default=1100)
    parser.add_argument('--maintenance-due', action='store_true')
    parser.add_argument('--history-back', action='store_true')
    parser.add_argument('--misses', action='store_true')
    parser.add_argument('--viewer', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = args.fixture.resolve()
    assert (base / 'synthetic-todo19.json').is_file()
    root = base / 'media'
    empty = base / 'navigation-empty'
    empty.mkdir(exist_ok=True)
    for i in range(3):
        path = empty / f'{i}.jpg'
        if not path.exists():
            os.link(base / 'template.jpg', path)
    profile = base / 'profile'
    if args.misses:
        profile = base / ('navigation-misses-' + uuid.uuid4().hex)
        profile.mkdir()
        (profile / 'config.json').write_bytes((base / 'profile' / 'config.json').read_bytes())
    config = ConfigManager(profile / 'config.json')
    cache = ThumbnailDiskCache(config.thumbnail_cache_dir, enabled=False)
    if cache.index_path.exists():
        with sqlite3.connect(cache.index_path) as connection:
            connection.execute("UPDATE maintenance SET value=? WHERE key='last_cleanup'",
                               ('0' if args.maintenance_due else str(__import__('time').time()),))
    app = QApplication([])
    stages = defaultdict(list)
    main_thread = threading.get_ident()
    def timed(cls, name):
        original = getattr(cls, name)
        def call(*a, **kw):
            before = perf_counter()
            try:
                return original(*a, **kw)
            finally:
                stages[name].append((before, perf_counter(), threading.get_ident() == main_thread))
        return patch.object(cls, name, call)
    patches = [timed(cls, name) for cls, names in (
        (BrowserWindow, ['navigate_to', '_on_scan_completed', '_flush_pending_scan_batch',
                         '_request_visible_thumbnails', '_on_thumbnail_ready', '_update_status']),
        (BrowserItemModel, ['append_final_directory_scan', 'begin_final_directory_scan', 'set_page_count', 'page_count']),
        (BrowserSortPolicy, ['sorted_items']),
        (ThumbnailDiskCache, ['get_suitable', 'put', 'cleanup_if_due', 'prune', 'usage_bytes', 'statistics']),
        (BrowserThumbnailProvider, ['begin_generation', '_increment_stat']),
    ) for name in names]
    for p in patches:
        p.start()
    controller = make_controller(profile, app)
    controller._restore_on_start = False
    browser = controller.create_browser_window()
    browser.resize(1200, 800)
    browser.show()
    started = perf_counter()
    beats, inputs, observed_stacks = [], [], []
    stop_sampler = threading.Event()
    def sample():
        while not stop_sampler.wait(.02):
            if beats and perf_counter() - beats[-1] > .08:
                frame = sys._current_frames().get(main_thread)
                stack = []
                while frame:
                    stack.append(f'{Path(frame.f_code.co_filename).name}:{frame.f_lineno}:{frame.f_code.co_name}')
                    frame = frame.f_back
                observed_stacks.append(stack[:12])
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    heartbeat = QTimer()
    heartbeat.setInterval(5)
    heartbeat.timeout.connect(lambda: beats.append(perf_counter()))
    heartbeat.start()
    viewer = None
    viewer_pages = []
    navigated = []
    first_visible_request_at = None
    original_provider_request = BrowserThumbnailProvider.request
    def audited_provider_request(provider, item, size, **kwargs):
        nonlocal first_visible_request_at
        if (
            navigated
            and first_visible_request_at is None
            and kwargs.get('priority') == 1
        ):
            first_visible_request_at = perf_counter()
        return original_provider_request(provider, item, size, **kwargs)
    request_patch = patch.object(BrowserThumbnailProvider, 'request', audited_provider_request)
    request_patch.start()
    if args.history_back:
        browser.set_current_folder(root)
        deadline = perf_counter() + 10
        while browser.current_path != root or browser._pending_scan is not None or browser.thumbnail_provider.pending_count:
            app.processEvents()
            sleep(.001)
            assert perf_counter() < deadline
    browser.set_current_folder(empty)
    deadline = perf_counter() + 10
    while browser.current_path != empty or browser._pending_scan is not None:
        app.processEvents()
        sleep(.001)
        assert perf_counter() < deadline
    def navigate():
        nonlocal viewer
        navigated.append(perf_counter())
        if args.history_back:
            QTest.mouseClick(browser.list_view.viewport(), Qt.MouseButton.BackButton)
        else:
            browser.set_current_folder(root)
        if args.viewer:
            viewer = controller.create_viewer_window()
            viewer.book_changed.disconnect(controller._on_viewer_book_changed)
            viewer.resize(900, 700)
            viewer.open_path(base / 'template.cbz')
    QTimer.singleShot(max(0, args.delay_ms - int((perf_counter()-started)*1000)), navigate)
    ready_at = None
    visible_first_request_at = None
    visible_first_ready_at = None
    visible_ready_peak = 0
    count = 0
    next_input = started
    try:
        finish_after = max(started + 7, perf_counter() + max(0, args.delay_ms / 1000 - (perf_counter()-started)) + 5)
        while perf_counter() < finish_after:
            app.processEvents()
            now = perf_counter()
            if viewer is not None:
                surface = QImage(viewer.viewer.size(), QImage.Format.Format_ARGB32)
                viewer.viewer.render(surface)
                pages = viewer.viewer.displayed_page_indexes
                if pages and (not viewer_pages or viewer_pages[-1]['page'] != pages[0]):
                    viewer_pages.append(dict(page=pages[0], at_ms=(now-navigated[0])*1000))
                    if pages[0] < 7:
                        viewer.next_page()
            if navigated and browser.current_path == root and browser.item_model.rowCount() > 100:
                if ready_at is None:
                    ready_at = now
                browser.list_view.doItemsLayout()
                viewport = browser.list_view.viewport().rect()
                visible_paths = tuple(
                    str(browser.item_model.item_at(row).path)
                    for row in range(browser.item_model.rowCount())
                    if browser.item_model.item_at(row) is not None
                    and browser.list_view.visualRect(browser.item_model.index(row, 0)).intersects(viewport)
                )
                if visible_paths and visible_first_request_at is None and browser.thumbnail_provider.pending_count:
                    visible_first_request_at = now
                visible_ready = sum(
                    browser.item_model.data(
                        browser.item_model.index(browser.item_model.row_for_path(path), 0),
                        BrowserItemModel.ThumbnailImageRole,
                    ) is not None
                    for path in visible_paths
                )
                visible_ready_peak = max(visible_ready_peak, visible_ready)
                if visible_paths and visible_ready and visible_first_ready_at is None:
                    visible_first_ready_at = now
                if now >= next_input:
                    before = perf_counter()
                    bar = browser.list_view.verticalScrollBar()
                    bar.setValue(min(bar.maximum(), (count % 20) * browser.list_view.gridSize().height()))
                    QTest.mouseClick(browser.list_view.viewport(), Qt.MouseButton.LeftButton,
                                     pos=browser.list_view.viewport().rect().center())
                    inputs.append((before, perf_counter()))
                    count += 1
                    next_input = now + .075
            sleep(.001)
        end = perf_counter()
        result = dict(delay_ms=args.delay_ms, maintenance_due=args.maintenance_due, history_back=args.history_back,
                      misses=args.misses, viewer_pages=viewer_pages,
                      interpreter=sys.executable, rows=browser.item_model.rowCount(),
                      navigation_ms=None if not navigated else (navigated[0]-started)*1000,
                      rows_latency_ms=None if ready_at is None else (ready_at-navigated[0])*1000,
                      max_heartbeat_gap_ms=max((b-a for a,b in zip(beats, beats[1:])),default=0)*1000,
                      input_count=count, max_input_call_ms=max((b-a for a,b in inputs),default=0)*1000,
                      visible_count=len(visible_paths) if 'visible_paths' in locals() else 0,
                      visible_first_request_ms=None if visible_first_request_at is None else (visible_first_request_at-navigated[0])*1000,
                      actual_first_visible_request_ms=None if first_visible_request_at is None else (first_visible_request_at-navigated[0])*1000,
                      visible_first_ready_ms=None if visible_first_ready_at is None else (visible_first_ready_at-navigated[0])*1000,
                      visible_ready_peak=visible_ready_peak,
                      stats=dict(browser.thumbnail_provider._stats), stalled_stacks=observed_stacks[:12])
        result['stages'] = {name: dict(count=len(spans), max_ms=max((b-a)*1000 for a,b,_ in spans),
                                      total_ms=sum((b-a)*1000 for a,b,_ in spans),
                                      ui_max_ms=max(((b-a)*1000 for a,b,ui in spans if ui),default=0),
                                      starts_ms=[round((a-started)*1000,2) for a,b,ui in spans] if name in ('cleanup_if_due','prune','navigate_to') else [])
                            for name, spans in stages.items() if spans}
    finally:
        heartbeat.stop()
        stop_sampler.set()
        sampler.join(1)
        close_controller(controller, app)
        request_patch.stop()
        for p in reversed(patches):
            p.stop()
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('stalled_stacks','stats','stages')}))
    if result['rows'] != json.loads((base / 'synthetic-todo19.json').read_text())['entries'] or result['input_count'] < 5:
        raise SystemExit('Navigation/input probe did not reach the target folder')


if __name__ == '__main__':
    main()
