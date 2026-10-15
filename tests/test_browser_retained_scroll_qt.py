"""Background-enabled native thumbnail redisplay with a large current listing."""
import io
import json
import ctypes
import os
import zipfile
from time import monotonic, perf_counter, sleep

import pytest
from PIL import Image
from PySide6.QtWidgets import QStyledItemDelegate

from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from tests.test_browser_thumbnail_ram_restore import wait_visible


def process_memory():
    if os.name != 'nt':
        return {}
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong),
                    *[(name, ctypes.c_size_t) for name in ('peak_rss', 'rss', 'peak_paged',
                      'paged', 'peak_nonpaged', 'nonpaged', 'private', 'peak_private')]]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),
                                                   ctypes.byref(counters), counters.cb):
        return {}
    return {'rss': counters.rss, 'peak_rss': counters.peak_rss, 'private': counters.private}


def make_listing(root, count, mixed=False):
    root.mkdir()
    payload = io.BytesIO()
    with Image.new('RGB', (512, 768), 'navy') as image:
        image.save(payload, 'PNG')
        pdf = io.BytesIO()
        image.save(pdf, 'PDF')
    for row in range(count):
        name = root / f'{row:04}'
        kind = row % 4 if mixed else 0
        if kind == 1:
            name.mkdir()
            (name / '表紙.png').write_bytes(payload.getvalue())
        elif kind == 2:
            with zipfile.ZipFile(name.with_suffix('.zip'), 'w') as archive:
                archive.writestr('表紙.png', payload.getvalue())
        elif kind == 3:
            name.with_suffix('.pdf').write_bytes(pdf.getvalue())
        else:
            name.with_suffix('.png').write_bytes(payload.getvalue())


def make_browser(tmp_path, root, qapp, screens, disk):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.apply({'last_browser_path': str(root), 'browser_thumbnail_background_screens': screens,
                  'thumbnail_disk_cache_enabled': disk}, save=False)
    window = BrowserWindow(config_manager=config)
    window.resize(900, 700)
    window.show()
    assert window.wait_for_scan()
    qapp.processEvents()
    return window


def drain_background(window, qapp, timeout=60):
    deadline = monotonic() + timeout
    workflow = window._browser_workflow
    workflow.schedule_background()
    while monotonic() < deadline:
        qapp.processEvents()
        window.thumbnail_provider.wait_for_done(10)
        qapp.processEvents()
        if (workflow.stop_reason == 'range-complete'
                and window.thumbnail_provider.pending_count == 0 and not workflow._timer.isActive()
                and not window.thumbnail_provider._changing_paths):
            return
        sleep(0.001)
    pytest.fail(f'Background did not settle: {workflow.stop_reason}; {window.thumbnail_provider.cache_statistics()}')


@pytest.mark.parametrize('screens,disk', [(2, False), (-1, False), (-1, True)])
def test_large_listing_horizon_retention_and_first_paint(tmp_path, qapp, screens, disk):
    root = tmp_path / 'books'
    make_listing(root, 900)
    start = perf_counter()
    window = make_browser(tmp_path, root, qapp, screens, disk)
    measurements = {'screens': screens, 'disk': disk}
    try:
        wait_visible(window, qapp)
        measurements['initial_visible_ms'] = (perf_counter() - start) * 1000
        provider = window.thumbnail_provider
        first, last = window._visible_row_range()
        initial_generated = provider.cache_statistics()['generated']
        assert initial_generated < window.item_model.rowCount() // 4
        measurements['initial_generated'] = initial_generated
        measurements['initial_process'] = process_memory()
        drain_background(window, qapp)
        span = last - first + 1
        if screens >= 0:
            high = min(window.item_model.rowCount() - 1, last + screens * span)
            assert all(window.item_model.row_for_path(key[0]) <= high for key in provider._cache)
        else:
            assert len(provider._cache) == 900
            assert all(provider.has_memory_thumbnail(item, window.thumbnail_render_spec)
                       for item in window.item_model.items)
            assert provider.memory_cache_limit_bytes > 128 * 1024 * 1024
        measurements['resident_after_background'] = provider.browser_memory_diagnostics()
        bar = window.list_view.verticalScrollBar()
        grid = window.list_view.gridSize().height()
        positions = [0, grid * 3, grid * 7, grid * 24, grid * 48, grid * 80,
                     min(bar.maximum(), grid * 120)]
        for position in positions:
            bar.setValue(position)
            wait_visible(window, qapp)
            drain_background(window, qapp)
            window.list_view.viewport().grab()
        # Settle geometry/surface variants before measuring hot redisplay.
        for position in [*positions, *reversed(positions)]:
            bar.setValue(position)
            window.list_view.viewport().grab()
        painted = []
        class RecordingDelegate(QStyledItemDelegate):
            def sizeHint(self, option, index):
                return window.item_delegate.sizeHint(option, index)

            def paint(self, painter, option, index):
                painted.append(window.item_model._has_compatible_thumbnail(
                    window.item_model.item_at(index), window.thumbnail_render_spec.cache_token))
                window.item_delegate.paint(painter, option, index)
        recording = RecordingDelegate(window.list_view)
        window.list_view.setItemDelegate(recording)
        source_before = provider.cache_statistics()
        surfaces = window.item_delegate._display_surface_cache
        prepare_before = surfaces.preparations
        surface_before = surfaces.byte_cost
        memory_before = provider.memory_cache_bytes
        scroll_ms, paint_ms = [], []
        for _ in range(8):
            for position in [*positions, *reversed(positions)]:
                begin = perf_counter()
                bar.setValue(position)
                scroll_ms.append((perf_counter() - begin) * 1000)
                begin = perf_counter()
                window.list_view.viewport().grab()
                paint_ms.append((perf_counter() - begin) * 1000)
        assert painted and all(painted)
        source_after = provider.cache_statistics()
        assert source_after['generated'] == source_before['generated']
        assert source_after['disk_hit'] == source_before['disk_hit']
        assert surfaces.preparations == prepare_before
        assert surfaces.byte_cost == surface_before
        assert provider.memory_cache_bytes == memory_before
        assert provider.memory_cache_bytes <= provider.memory_cache_limit_bytes
        assert surfaces.item_count > 96
        # Initial displayed items still own their completed image after leaving
        # the generation horizon; no model refill request is needed to paint.
        for row in range(first, last + 1):
            assert window.item_model._has_compatible_thumbnail(
                window.item_model.item_at(row), window.thumbnail_render_spec.cache_token)
        measurements.update(paints=len(painted), missing=painted.count(False),
            generation_delta=source_after['generated']-source_before['generated'],
            disk_delta=source_after['disk_hit']-source_before['disk_hit'],
            surface_prepare_delta=surfaces.preparations-prepare_before,
            scroll_max_ms=max(scroll_ms), scroll_mean_ms=sum(scroll_ms)/len(scroll_ms),
            paint_max_ms=max(paint_ms), paint_mean_ms=sum(paint_ms)/len(paint_ms),
            final_memory=provider.browser_memory_diagnostics(), surface_items=surfaces.item_count,
            surface_bytes=surfaces.byte_cost, model_refs=len(window.item_model._thumbnail_images))
        measurements['final_process'] = process_memory()
        (tmp_path / 'measurements.json').write_text(json.dumps(measurements, indent=2), encoding='utf-8')
    finally:
        window.close()
        qapp.processEvents()


def test_unlimited_mixed_image_folder_zip_pdf_retains_unseen_images(tmp_path, qapp):
    root = tmp_path / 'mixed'
    make_listing(root, 160, mixed=True)
    window = make_browser(tmp_path, root, qapp, -1, False)
    try:
        wait_visible(window, qapp)
        drain_background(window, qapp)
        provider = window.thumbnail_provider
        assert len(provider._cache) == 160, {
            'statistics': provider.cache_statistics(),
            'missing': [(item.path.name, item.kind.value) for item in window.item_model.items
                        if not provider.has_memory_thumbnail(item, window.thumbnail_render_spec)],
        }
        assert len({item.kind for item in window.item_model.items}) == 4
        bar = window.list_view.verticalScrollBar()
        positions = [0, bar.maximum()//3, bar.maximum()*2//3, bar.maximum()]
        for position in positions:
            bar.setValue(position)
            window.list_view.viewport().grab()
            first, last = window._visible_row_range()
            assert all(window.item_model._has_compatible_thumbnail(window.item_model.item_at(row),
                window.thumbnail_render_spec.cache_token) for row in range(first, last+1))
        before = provider.cache_statistics()
        for _ in range(10):
            for position in [*positions, *reversed(positions)]:
                bar.setValue(position)
                window.list_view.viewport().grab()
        after = provider.cache_statistics()
        assert after['generated'] == before['generated']
        assert after['disk_hit'] == before['disk_hit']
        assert provider.memory_cache_bytes <= provider.memory_cache_limit_bytes
    finally:
        window.close()
        qapp.processEvents()
