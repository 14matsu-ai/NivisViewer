"""RAM-only display restoration, including the first paint after a scroll."""
import io
import zipfile
from dataclasses import replace
from pathlib import Path
from time import monotonic

import pytest
from PIL import Image
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QStyledItemDelegate

from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec
from app.thumbnail_disk_cache import ThumbnailDiskCache
from tests.test_thumbnail_provider import make_item, write_image
from app.browser_model import BrowserItemKind
from tests.test_browser_navigation_window import make_window


def wait_visible(window, qapp):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        window._request_visible_thumbnails()
        window.thumbnail_provider.wait_for_done(50)
        qapp.processEvents()
        first, last = window._visible_row_range()
        if all(window.item_model._has_compatible_thumbnail(
                window.item_model.item_at(row), window.thumbnail_render_spec.cache_token)
                for row in range(first, last + 1)):
            return
    pytest.fail('Visible thumbnails did not settle')


@pytest.mark.parametrize('kind', ['image', 'folder', 'archive'])
@pytest.mark.parametrize('disk', [False, True])
def test_first_paint_on_reverse_and_rapid_return_uses_ram(tmp_path, qapp, kind, disk):
    folder = tmp_path / 'books'
    payload = io.BytesIO()
    Image.new('RGB', (256, 384), 'navy').save(payload, 'PNG')
    for row in range(48):
        base = folder / f'{row:03}'
        if kind == 'folder':
            write_image(base / '表紙.png')
        elif kind == 'archive':
            folder.mkdir(exist_ok=True)
            with zipfile.ZipFile(base.with_suffix('.zip'), 'w') as archive:
                archive.writestr('表紙.png', payload.getvalue())
        else:
            write_image(base.with_suffix('.png'))
    window = make_window(tmp_path, folder, qapp)
    try:
        window.config.apply({'browser_thumbnail_background_screens': 0,
                             'thumbnail_disk_cache_enabled': disk})
        window._browser_workflow._timer.stop()
        wait_visible(window, qapp)
        first, last = window._visible_row_range()
        origin = tuple(window.item_model.item_at(row) for row in range(first, last + 1))
        bar = window.list_view.verticalScrollBar()
        far = min(bar.maximum(), window.list_view.viewport().height() * 3)
        assert far > 0
        bar.setValue(far)
        wait_visible(window, qapp)
        assert all(window.thumbnail_provider.has_memory_thumbnail(item, window.thumbnail_render_spec)
                   for item in origin)
        assert all(window.item_model._has_compatible_thumbnail(item, window.thumbnail_render_spec.cache_token)
                   for item in origin)
        bar.setValue(0)
        window.list_view.viewport().grab()
        bar.setValue(far)
        window.list_view.viewport().grab()
        before = window.thumbnail_provider.cache_statistics()
        cache_bytes = window.thumbnail_provider.memory_cache_bytes
        painted = []
        class RecordingDelegate(QStyledItemDelegate):
            def sizeHint(self, option, index):
                return window.item_delegate.sizeHint(option, index)

            def paint(self, painter, option, index):
                painted.append(window.item_model._has_compatible_thumbnail(
                    window.item_model.item_at(index), window.thumbnail_render_spec.cache_token))
                window.item_delegate.paint(painter, option, index)

        recording_delegate = RecordingDelegate(window.list_view)
        window.list_view.setItemDelegate(recording_delegate)
        # No event-loop turn is allowed between changing the viewport and paint.
        for _ in range(12):
            bar.setValue(0)
            window.list_view.viewport().grab()
            assert all(window.item_model._has_compatible_thumbnail(item, window.thumbnail_render_spec.cache_token)
                       for item in origin)
            bar.setValue(far)
            window.list_view.viewport().grab()
            first, last = window._visible_row_range()
            assert len(window.item_model._thumbnail_images) <= window.thumbnail_provider.memory_cache_capacity_entries
        # Also exercise the pre-paint fallback independently of scroll signals.
        window.item_model.clear_thumbnails()
        window.list_view.viewport().grab()
        assert painted and all(painted)
        after = window.thumbnail_provider.cache_statistics()
        assert after['generated'] == before['generated']
        assert after['disk_hit'] == before['disk_hit']
        assert window.thumbnail_provider.memory_cache_bytes == cache_bytes
        assert cache_bytes <= window.thumbnail_provider.memory_cache_limit_bytes
    finally:
        window.close()
        qapp.processEvents()


def test_restore_rejects_stale_generation_revision_changing_path_and_incompatible_size(tmp_path, qapp, monkeypatch):
    path = tmp_path / 'page.png'
    write_image(path)
    item = make_item(path, BrowserItemKind.IMAGE)
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    generation = provider.begin_generation()
    try:
        assert provider.request(item, spec, generation=generation)
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        ready = QSignalSpy(provider.thumbnail_ready)
        def forbidden(*args, **kwargs):
            pytest.fail('RAM display restoration must not read files, decode, or access disk cache')

        with monkeypatch.context() as guard:
            guard.setattr(Path, 'stat', forbidden)
            guard.setattr(Image, 'open', forbidden)
            guard.setattr(ThumbnailDiskCache, 'get', forbidden)
            guard.setattr(ThumbnailDiskCache, 'get_suitable', forbidden)
            restored = provider.restore_memory_thumbnail(item, spec, generation=generation)
        assert restored is not None
        assert ready.count() == 0
        changed = replace(item, modified_time_ns=1)
        assert provider.restore_memory_thumbnail(changed, spec, generation=generation) is None
        large = ThumbnailRenderSpec.from_settings(256, 'square_1_1', 'letterbox')
        high_dpi = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox', device_pixel_ratio=2)
        crop = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'center_crop')
        assert provider.restore_memory_thumbnail(item, large, generation=generation) is None
        assert provider.restore_memory_thumbnail(item, high_dpi, generation=generation) is None
        assert provider.restore_memory_thumbnail(item, crop, generation=generation) is None
        provider.defer_changing_item(item)
        assert provider.restore_memory_thumbnail(item, spec, generation=generation) is None
        provider.release_changing_item(item)
        assert provider.restore_memory_thumbnail(item, spec, generation=generation) is not None
        provider.reconcile_items([item], [changed])
        assert provider.restore_memory_thumbnail(item, spec, generation=generation) is None
        provider.begin_generation()
        assert provider.restore_memory_thumbnail(item, spec, generation=generation) is None
        assert ready.count() == 0
    finally:
        provider.close()


def test_evicted_ram_image_is_not_restored_or_read_from_disk(tmp_path, qapp, monkeypatch):
    path = tmp_path / 'page.png'
    write_image(path)
    item = make_item(path, BrowserItemKind.IMAGE)
    provider = BrowserThumbnailProvider(disk_cache_enabled=True, cache_capacity=1,
                                        disk_cache=ThumbnailDiskCache(tmp_path / 'cache'))
    generation = provider.begin_generation()
    try:
        assert provider.request(item, 64, generation=generation)
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        other = tmp_path / 'other.png'
        write_image(other)
        assert provider.request(make_item(other, BrowserItemKind.IMAGE), 64, generation=generation)
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        assert not provider.has_memory_thumbnail(item, 64)
        stats = provider.cache_statistics()
        monkeypatch.setattr(ThumbnailDiskCache, 'get', lambda *a, **kw: pytest.fail('Unexpected disk read'))
        monkeypatch.setattr(ThumbnailDiskCache, 'get_suitable', lambda *a, **kw: pytest.fail('Unexpected disk read'))
        assert provider.restore_memory_thumbnail(item, 64, generation=generation) is None
        after = provider.cache_statistics()
        assert after['generated'] == stats['generated']
        assert after['disk_hit'] == stats['disk_hit']
    finally:
        provider.close()


def test_larger_compatible_ram_image_can_restore_smaller_display(tmp_path, qapp):
    path = tmp_path / 'page.png'
    with Image.new('RGB', (256, 384), 'navy') as source:
        source.save(path)
    item = make_item(path, BrowserItemKind.IMAGE)
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    generation = provider.begin_generation()
    large = ThumbnailRenderSpec.from_settings(128, 'square_1_1', 'letterbox')
    small = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    try:
        assert provider.request(item, large, generation=generation)
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        stats = provider.cache_statistics()
        cached = provider.restore_memory_thumbnail(item, small, generation=generation)
        assert cached is not None
        assert max(cached[0].width(), cached[0].height()) >= small.long_edge
        assert provider.cache_statistics()['generated'] == stats['generated']
        assert provider.cache_statistics()['disk_hit'] == stats['disk_hit']
    finally:
        provider.close()


def test_directory_switch_with_same_filename_rejects_previous_generation(tmp_path, qapp):
    old = tmp_path / 'old'
    new = tmp_path / 'new'
    write_image(old / 'page.png', color='red')
    write_image(new / 'page.png', color='blue')
    window = make_window(tmp_path, old, qapp)
    try:
        wait_visible(window, qapp)
        old_item = window.item_model.item_at(0)
        old_generation = window._generation
        assert window.navigate_to(new)
        assert window.wait_for_scan()
        wait_visible(window, qapp)
        assert window.thumbnail_provider.restore_memory_thumbnail(
            old_item, window.thumbnail_render_spec, generation=old_generation) is None
        window.item_model.clear_thumbnails()
        window.list_view.viewport().grab()
        image = window.item_model.data(window.item_model.index(0, 0), window.item_model.ThumbnailImageRole)
        assert image is not None
        color = image.pixelColor(image.width() // 2, image.height() // 2)
        assert color.blue() > color.red()
    finally:
        window.close()
        qapp.processEvents()
