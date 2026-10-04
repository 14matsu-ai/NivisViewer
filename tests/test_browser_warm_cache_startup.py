from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy

from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_window import BrowserWindow
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec


def test_existing_thumbnail_publishes_before_due_maintenance(tmp_path, qapp, monkeypatch):
    source = tmp_path / '保存済み.png'
    Image.new('RGB', (201, 301), 'red').save(source)
    item = BrowserItem(source.name, source, BrowserItemKind.IMAGE, source.stat().st_mtime)
    spec = ThumbnailRenderSpec.from_settings(180, 'portrait_1_sqrt2', 'smart_crop')
    cache = ThumbnailDiskCache(tmp_path / 'cache')
    thumbnail = QImage(181, 256, QImage.Format.Format_RGB32)
    thumbnail.fill('red')
    assert cache.put(item, spec, thumbnail, page_count=1)
    # No last_cleanup row: daily maintenance is due on a reopened process.
    cache.close()
    reopened = ThumbnailDiskCache(tmp_path / 'cache', enabled=False)
    maintenance = Mock(wraps=reopened.cleanup_if_due)
    monkeypatch.setattr(reopened, 'cleanup_if_due', maintenance)
    decode = Mock(side_effect=AssertionError('Valid saved thumbnail must not regenerate'))
    provider = BrowserThumbnailProvider(disk_cache=reopened, loader=decode)
    ready = QSignalSpy(provider.thumbnail_ready)
    try:
        generation = provider.begin_generation()
        assert provider.request(item, spec, generation=generation)
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert ready.count() == 1
        decode.assert_not_called()
        maintenance.assert_not_called()
        # Cached page-count queries also must not initiate a whole-cache walk.
        result = provider._load_page_count_pipeline(item, 16)
        assert result.page_count == 1
        maintenance.assert_not_called()
        provider.cleanup_caches_async(force=False)
        assert provider.wait_for_done(3000)
        maintenance.assert_called_once_with(force=False)
        assert reopened.get_suitable(item, spec) is not None
    finally:
        provider.close()


def test_idle_cleanup_waits_for_scan_and_thumbnails_and_stops_on_close(monkeypatch):
    retry = Mock()
    monkeypatch.setattr('app.browser_window.QTimer.singleShot', retry)
    cleanup = Mock()
    owner = SimpleNamespace(_shutdown_prepared=False, _pending_scan=object(),
                            thumbnail_provider=SimpleNamespace(pending_count=0, cleanup_caches_async=cleanup))
    owner._run_idle_cache_cleanup = lambda: BrowserWindow._run_idle_cache_cleanup(owner)
    owner._run_idle_cache_cleanup()
    cleanup.assert_not_called()
    assert retry.call_count == 1
    owner._pending_scan = None
    owner.thumbnail_provider.pending_count = 1
    owner._run_idle_cache_cleanup()
    assert retry.call_count == 2
    cleanup.assert_not_called()
    owner.thumbnail_provider.pending_count = 0
    owner._run_idle_cache_cleanup()
    cleanup.assert_called_once_with(force=False)
    owner._shutdown_prepared = True
    owner._run_idle_cache_cleanup()
    assert retry.call_count == 2
    assert cleanup.call_count == 1


def test_idle_cleanup_retries_when_viewer_lane_temporarily_pauses_browser(monkeypatch):
    retry = Mock()
    monkeypatch.setattr('app.browser_window.QTimer.singleShot', retry)
    cleanup = Mock(return_value=False)
    owner = SimpleNamespace(_shutdown_prepared=False, _pending_scan=None,
                            thumbnail_provider=SimpleNamespace(pending_count=0, cleanup_caches_async=cleanup))
    owner._run_idle_cache_cleanup = lambda: BrowserWindow._run_idle_cache_cleanup(owner)
    owner._run_idle_cache_cleanup()
    retry.assert_called_once_with(500, owner, owner._run_idle_cache_cleanup)
    cleanup.return_value = True
    owner._run_idle_cache_cleanup()
    assert retry.call_count == 1
