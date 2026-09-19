"""Separate-process persisted Browser restore, synthetic workspace only."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import argparse
from io import BytesIO
import json
from pathlib import Path
import sys
import sqlite3
from zipfile import ZipFile, ZIP_STORED

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtWidgets import QApplication
from app.browser_model import BrowserItem, BrowserItemKind
from app.thumbnail_provider import BrowserThumbnailProvider
from app.config_manager import ConfigManager
from scripts.benchmark_browser_startup_probe import measure
from scripts.benchmark_viewer_navigation import _jpeg_payload
from tests.test_application_controller import make_controller, close_controller


def prepare(app, base, count):
    if base.exists():
        raise ValueError('Choose a NEW fixture directory; existing data is never overwritten')
    base.mkdir(parents=True)
    (base / 'synthetic-todo19.json').write_text(json.dumps({'entries': count}), encoding='utf-8')
    profile, root = base / 'profile', base / 'media'
    profile.mkdir()
    root.mkdir()
    payload = _jpeg_payload((2400, 3600), 0, fixture_mode='high-detail')
    formats = [('jpg', 'JPEG', {}), ('png', 'PNG', {}), ('avif', 'AVIF', {'speed': 10}),
               ('jxl', 'JXL', {'effort': 1}), ('webp', 'WEBP', {}), ('pdf', 'PDF', {})]
    templates = {}
    for ext, fmt, options in formats:
        target = base / f'template.{ext}'
        if ext == 'jpg':
            target.write_bytes(payload)
        else:
            with Image.open(BytesIO(payload)) as image:
                image.save(target, fmt, **options)
        templates[ext] = target
    with ZipFile(base / 'template.cbz', 'w', compression=ZIP_STORED) as archive:
        for i in range(8):
            archive.writestr(f'{i:03d}.jpg', payload)
    templates['cbz'] = base / 'template.cbz'
    items = []
    for i in range(count):
        ext = ('jpg', 'png', 'avif', 'jxl', 'folder', 'cbz', 'pdf', 'webp')[i % 8]
        # Bound NTFS hardlinks even for large fixtures.
        group = i // 512
        template = base / f'group-{group}.{"jpg" if ext == "folder" else ext}'
        if not template.exists():
            template.write_bytes(templates['jpg' if ext == 'folder' else ext].read_bytes())
        path = root / (f'{i:05d}' if ext == 'folder' else f'{i:05d}.{ext}')
        if ext == 'folder':
            path.mkdir()
            os.link(template, path / 'cover.jpg')
            kind = BrowserItemKind.FOLDER
        else:
            os.link(template, path)
            kind = BrowserItemKind.PDF if ext == 'pdf' else BrowserItemKind.ARCHIVE if ext == 'cbz' else BrowserItemKind.IMAGE
        items.append((ext, BrowserItem(path.name, path, kind, path.stat().st_mtime)))
    controller = make_controller(profile, app)
    controller._restore_on_start = False
    controller.config.apply({'last_browser_path': str(root), 'reopen_last_on_start': False,
                             'last_open_path': '', 'browser_folders_first': False,
                             'view_mode': 'single'}, save=True)
    browser = controller.create_browser_window()
    browser.resize(1200, 800)
    browser.show()
    browser.resize(1200, 800)
    app.processEvents()
    cache = browser.thumbnail_provider.disk_cache
    cache.set_enabled(True)
    cache.cleanup_interval = count + 1  # Fixture creation only, not measured startup.
    spec = browser.thumbnail_render_spec
    by_format = {}
    try:
        for i, (ext, item) in enumerate(items):
            if ext not in by_format:
                result = BrowserThumbnailProvider.load_thumbnail_result(item, spec, pdfium_service=controller.pdfium_service)
                assert result.image is not None, ext
                by_format[ext] = result
            result = by_format[ext]
            assert cache.put(item, spec, result.image,
                             cover_path=item.path / 'cover.jpg' if ext == 'folder' else None,
                             entry_path=result.entry_path, page_count=result.page_count)
            if (i+1) % 1000 == 0:
                print(f'Prepared {i+1} cached synthetic items', flush=True)
        cache.cleanup_if_due(force=True)
        (base / 'prepared.json').write_text(json.dumps(dict(count=count, cache_token=spec.cache_token,
                                                          family_token=spec.family_token,
                                                          cache_bytes=cache.usage_bytes(), render_spec=repr(spec)), indent=2), encoding='utf-8')
    finally:
        close_controller(controller, app)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'measure'])
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--count', type=int, default=1000)
    parser.add_argument('--workers', type=int, choices=[1, 2], default=1)
    parser.add_argument('--viewer-competition', action='store_true')
    parser.add_argument('--maintenance-due', action='store_true')
    parser.add_argument('--emulate-eager-maintenance', action='store_true')
    parser.add_argument('--wait-cleanup', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    base = args.fixture.resolve()
    if args.action == 'prepare':
        prepare(app, base, args.count)
        return
    if not (base / 'synthetic-todo19.json').is_file():
        raise ValueError('Only a marked synthetic fixture may be measured')
    if args.maintenance_due:
        config = ConfigManager(base / 'profile' / 'config.json')
        from app.thumbnail_disk_cache import ThumbnailDiskCache
        cache = ThumbnailDiskCache(config.thumbnail_cache_dir, enabled=False)
        with sqlite3.connect(cache.index_path) as connection:
            connection.execute("UPDATE maintenance SET value='0' WHERE key='last_cleanup'")
    result = measure(app, base / 'profile', base / 'media', 'fresh-process-warm',
                     restore=True, browser_workers=args.workers,
                     eager_maintenance=args.emulate_eager_maintenance, wait_cleanup=args.wait_cleanup,
                     viewer_book=base / 'template.cbz' if args.viewer_competition else None)
    result['fixture'] = str(base)
    result['maintenance_due'] = args.maintenance_due
    result['eager_barrier_emulation'] = args.emulate_eager_maintenance
    result['prepared'] = json.loads((base / 'prepared.json').read_text(encoding='utf-8'))
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    if result['status'] != 'completed' or result['restored_path'] != result['expected_path']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
