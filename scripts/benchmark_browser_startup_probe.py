"""TODO19 read-only production-path probe using synthetic temporary media only.

Cold means empty app thumbnail cache, not flushed OS/storage caches. Each run
recreates controller/provider; warm follows cold with the same disk cache.
No production scheduler settings or worker counts are changed.
"""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import argparse
from io import BytesIO
from collections import defaultdict
import json
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, sleep
from unittest.mock import patch
from zipfile import ZipFile, ZIP_STORED

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from app.browser_window import BrowserWindow
from app.browser_sort import BrowserSortPolicy
from app.thumbnail_provider import BrowserThumbnailProvider as ThumbnailProvider
from app.thumbnail_disk_cache import ThumbnailDiskCache
from tests.test_application_controller import make_controller, close_controller
from scripts.benchmark_viewer_navigation import _jpeg_payload


def measure(app, profile, root, mode, crop_mode='smart_crop', viewer_book=None,
            restore=False, browser_workers=1, eager_maintenance=False, wait_cleanup=False):
    stages = defaultdict(list)
    cache_outcomes = defaultdict(int)
    milestones = {}
    start = None
    def timed(cls, name, label, static=False):
        original = getattr(cls, name)
        def wrapper(*args, **kwargs):
            before = perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                stages[label].append((before, perf_counter()))
        return patch.object(cls, name, staticmethod(wrapper) if static else wrapper)
    original_get = ThumbnailDiskCache.get_suitable
    maintenance_emulated = False
    def audited_get(cache, item, spec, **kwargs):
        nonlocal maintenance_emulated
        before = perf_counter()
        with cache._lock:
            acquired = perf_counter()
            stages['cache_lock_wait'].append((before, acquired))
            if eager_maintenance and not maintenance_emulated:
                maintenance_emulated = True
                cache.cleanup_if_due()
            result = original_get(cache, item, spec, **kwargs)
            stages['disk_get'].append((before, perf_counter()))
            if result is not None:
                cache_outcomes['placeholder' if result.low_resolution_placeholder else 'full_hit'] += 1
            else:
                rows = cache._connection.execute(
                    'SELECT family_token, format_version FROM entries WHERE source_path = ?',
                    (cache._normalize_path(item.path),)).fetchall() if cache._connection else []
                reason = 'no_valid_source_entry' if not rows else 'family_or_encoding_mismatch'
                if any(row == (spec.family_token, cache._format_version_for_policy(spec.encoding_policy)) for row in rows):
                    reason = 'matching_policy_but_invalid_source_cover_file_or_decode'
                cache_outcomes[reason] += 1
            return result
    patches = [patch.object(ThumbnailDiskCache, 'get_suitable', audited_get),
               timed(ThumbnailDiskCache, '_initialize', 'cache_initialize'),
               timed(ThumbnailDiskCache, '_read_qimage', 'cache_decode', True),
               timed(ThumbnailDiskCache, 'put', 'disk_put'),
               timed(ThumbnailDiskCache, 'cleanup_if_due', 'maintenance'),
               timed(ThumbnailProvider, 'load_thumbnail_result', 'generate', True),
               timed(ThumbnailProvider, '_load_pipeline', 'pipeline'),
               timed(BrowserWindow, '_on_thumbnail_ready', 'ui_publish'),
               timed(BrowserWindow, '_on_scan_completed', 'scan_commit'),
               timed(BrowserWindow, '_flush_pending_scan_batch', 'append_remainder'),
               timed(BrowserSortPolicy, 'sorted_items', 'sort')]
    if browser_workers != 1:
        from app.image_work_coordinator import ImageWorkCoordinator
        patches.append(patch('app.application_controller.ImageWorkCoordinator',
                             side_effect=lambda parent, **_: ImageWorkCoordinator(parent, max_workers=browser_workers+1)))
    for p in patches:
        p.start()
    if restore:
        start = perf_counter()
    controller = make_controller(profile, app)
    if not restore:
        controller.settings['thumbnail_crop_mode'] = crop_mode
        controller.settings['browser_folders_first'] = False
        controller.settings['view_mode'] = 'single'
    # Never restore a saved/home location or prestart the measured scan.
    if restore:
        browser = controller.start()
    else:
        controller._restore_on_start = False
        browser = controller.create_browser_window()
        browser.resize(1200, 800)
        browser.show()
        browser.resize(1200, 800)
        app.processEvents()
    provider = browser.thumbnail_provider
    viewer = controller.create_viewer_window() if viewer_book else None
    if viewer is not None:
        viewer.resize(900, 700)
        # Keep the measured shelf in place while opening an unrelated synthetic
        # Viewer book. Scheduling/interactive pause signals stay connected.
        viewer.book_changed.disconnect(controller._on_viewer_book_changed)
    def mark(name):
        if start is not None:
            milestones.setdefault(name, (perf_counter() - start) * 1000)
    browser.scanner.batch_ready.connect(lambda result: mark('first_scan_batch') if Path(result.path) == root else None)
    browser.scanner.scan_completed.connect(lambda result: mark('scan_complete_gui') if Path(result.path) == root else None)
    browser.item_model.rowsInserted.connect(lambda *_: mark('first_rows'))
    browser.item_model.modelReset.connect(lambda: mark('first_reset'))
    provider.thumbnail_ready.connect(lambda *_: mark('first_ready'))
    per_item = {}
    provider.thumbnail_ready.connect(lambda path, *_: per_item.setdefault(Path(path).name, round((perf_counter()-start)*1000, 3)) if start else None)
    beats = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: beats.append(perf_counter()))
    timer.start()
    paints = []
    target = ()
    previous = -1
    if not restore:
        start = perf_counter()
        browser.set_current_folder(root)
    viewer_pages = []
    viewer_request_time = start
    viewer_last = None
    if viewer is not None:
        viewer.open_path(viewer_book)
    try:
        deadline = start + 45
        while perf_counter() < deadline:
            app.processEvents()
            if viewer is not None:
                surface = QImage(viewer.viewer.size(), QImage.Format.Format_ARGB32)
                viewer.viewer.render(surface)
                pages = viewer.viewer.displayed_page_indexes
                if pages and pages != viewer_last:
                    viewer_pages.append(dict(page=pages[0], at_ms=round((perf_counter()-start)*1000, 3),
                                             latency_ms=round((perf_counter()-viewer_request_time)*1000, 3)))
                    viewer_last = pages
                    if pages[0] < 7:
                        viewer_request_time = perf_counter()
                        viewer.next_page()
            if browser.item_model.rowCount():
                if not target:
                    browser.list_view.doItemsLayout()
                    viewport = browser.list_view.viewport().rect()
                    target = tuple(str(browser.item_model.item_at(row).path)
                                   for row in range(min(200, browser.item_model.rowCount()))
                                   if browser.list_view.visualRect(browser.item_model.index(row, 0)).intersects(viewport))
                ready = sum(isinstance(browser.item_model.data(
                    browser.item_model.index(browser.item_model.row_for_path(path), 0),
                    browser.item_model.ThumbnailImageRole), QImage) for path in target)
                if ready != previous:
                    surface = QImage(browser.list_view.viewport().size(), QImage.Format.Format_ARGB32)
                    browser.list_view.viewport().render(surface)
                    paints.append((round((perf_counter()-start)*1000, 3), ready))
                    previous = ready
                    if ready:
                        mark('first_visible_render')
                    if target and ready == len(target):
                        mark('viewport_filled_render')
                if 'viewport_filled_render' in milestones and browser._pending_scan is None and (viewer is None or (viewer_last and viewer_last[0] == 7)) and (not wait_cleanup or stages['maintenance']):
                    break
            sleep(.001)
        stop = perf_counter()
        stats = dict(provider._stats)
        result = dict(mode=mode, milestones_ms={k: round(v, 3) for k, v in milestones.items()},
                      restored_path=str(browser.current_path), expected_path=str(root),
                      cache_token=browser.thumbnail_render_spec.cache_token,
                      family_token=browser.thumbnail_render_spec.family_token,
                      render_spec=repr(browser.thumbnail_render_spec),
                      viewport_items=len(target), source_items=len(browser.item_model.source_items),
                      render_progress=paints, workers=controller.image_work_coordinator.browser_workers,
                      visible_item_ready_ms={Path(path).name: per_item.get(Path(path).name) for path in target},
                      viewer_pages=viewer_pages,
                      stats=stats, max_heartbeat_gap_ms=round(max((b-a for a,b in zip(beats,beats[1:])),default=0)*1000,3))
        result['cache_outcomes'] = dict(cache_outcomes)
    finally:
        timer.stop()
        close_controller(controller, app)
        for p in reversed(patches):
            p.stop()
    summary = {}
    for label, spans in stages.items():
        values = [(b-a)*1000 for a,b in spans if start <= a <= b <= stop]
        if values:
            summary[label] = dict(count=len(values), total_ms=round(sum(values),3),
                                  median_ms=round(median(values),3), max_ms=round(max(values),3),
                                  first_start_ms=round((min(a for a,b in spans if start <= a <= b <= stop)-start)*1000,3))
    result['stages'] = summary
    result['status'] = 'completed' if 'viewport_filled_render' in milestones and (viewer is None or (viewer_last and viewer_last[0] == 7)) and (not wait_cleanup or stages['maintenance']) else 'timeout'
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', choices=['image','folder','archive','pdf','mixed'], default='image')
    parser.add_argument('--count', type=int, default=1000)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--crop-mode', choices=['letterbox', 'smart_crop'], default='smart_crop')
    parser.add_argument('--viewer-competition', action='store_true')
    args = parser.parse_args()
    if args.count < 1:
        parser.error('--count must be positive')
    app = QApplication.instance() or QApplication([])
    with TemporaryDirectory(prefix='nivis-todo19-') as directory:
        base = Path(directory)
        root = base / 'media'
        root.mkdir()
        payload = _jpeg_payload((2400, 3600), 0, fixture_mode='high-detail')
        template = base / 'template.jpg'
        template.write_bytes(payload)
        small_payload = _jpeg_payload((8, 12), 0)
        mixed_templates = {}
        if args.kind == 'mixed':
            for extension, format_name, options in [('png', 'PNG', {}), ('webp', 'WEBP', {'quality': 85}),
                                                     ('avif', 'AVIF', {'quality': 85, 'speed': 10}),
                                                     ('jxl', 'JXL', {'quality': 85, 'effort': 1}), ('pdf', 'PDF', {})]:
                path = base / f'template.{extension}'
                with Image.open(BytesIO(payload)) as source:
                    source.save(path, format_name, **options)
                mixed_templates[extension] = path
        viewer_book = None
        if args.viewer_competition:
            viewer_book = base / 'viewer.cbz'
            with ZipFile(viewer_book, 'w', compression=ZIP_STORED) as archive:
                for index in range(8):
                    archive.writestr(f'{index:03d}.jpg', payload)
        for i in range(args.count):
            name = f'{i:05d}'
            kind = args.kind
            if kind == 'mixed':
                kind = ('image', 'png', 'avif', 'jxl', 'folder', 'archive', 'pdf', 'webp')[i % 8]
            if kind == 'image':
                if i % 512 == 0:
                    template = base / f'template-{i}.jpg'
                    template.write_bytes(payload)
                os.link(template, root / f'{name}.jpg')
            elif kind == 'folder':
                if i % 64 == 0:
                    template = base / f'folder-template-{i}.jpg'
                    template.write_bytes(payload)
                folder = root / name
                folder.mkdir()
                for j in range(8):
                    os.link(template, folder / f'{j:03d}.jpg')
            elif kind == 'archive':
                with ZipFile(root / f'{name}.cbz', 'w', compression=ZIP_STORED) as archive:
                    for j in range(8):
                        archive.writestr(f'{j:03d}.jpg', payload if j == 0 else small_payload)
            elif kind in mixed_templates:
                os.link(mixed_templates[kind], root / f'{name}.{kind}')
            else:
                with Image.open(template) as image:
                    image.save(root / f'{name}.pdf', 'PDF', resolution=150)
        profile = base / 'profile'
        profile.mkdir()
        rows = [measure(app, profile, root, mode, args.crop_mode, viewer_book) for mode in ('cold','warm')]
        result = dict(kind=args.kind, entries=args.count, image_size=[2400,3600],
                      crop_mode=args.crop_mode,
                      viewer_competition=args.viewer_competition,
                      payload_bytes=len(payload), qt_platform=app.platformName(), runs=rows,
                      limits='Synthetic local repeated JPEG; hardlinked image/folder payload; OS cache not flushed; warm is a fresh provider/controller in the same process; measured render not native display; setup/shutdown excluded.')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
        if any(row['status'] != 'completed' for row in rows):
            raise SystemExit(1)


if __name__ == '__main__':
    main()
