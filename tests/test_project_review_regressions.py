"""Whole-project lifetime and invalid-config regression coverage."""
import json
from threading import Event, Thread

import pytest
from PySide6.QtCore import QRunnable


def test_close_removes_cancelled_queued_cache_reads(tmp_path, qapp):
    from app.browser_model import BrowserItem, BrowserItemKind
    from app.thumbnail_disk_cache import ThumbnailDiskCache
    from app.thumbnail_provider import BrowserThumbnailProvider, _RETIRED_THUMBNAIL_PROVIDERS
    from tests.test_application_controller import wait_until, write_image

    provider = BrowserThumbnailProvider(disk_cache=ThumbnailDiskCache(tmp_path / 'cache'))
    provider._read_pool.setMaxThreadCount(1)
    entered, release = Event(), Event()

    class BusyRead(QRunnable):
        def run(self):
            entered.set()
            release.wait(5)

    provider._read_pool.start(BusyRead())
    try:
        assert entered.wait(2)
        path = tmp_path / 'page.png'
        write_image(path)
        item = BrowserItem(display_name=path.name, path=path, kind=BrowserItemKind.IMAGE,
                           modified_at=None)
        assert provider.request(item, 128)
        assert provider.pending_read_count == 1
        provider.close(wait_msecs=0)
        release.set()
        assert wait_until(qapp, lambda: provider.wait_for_done(0), timeout=2)
        assert provider.pending_read_count == 0
    finally:
        release.set()
        provider._read_pool.waitForDone(3000)
        # Preserve process isolation even when the regression assertion fails.
        provider._finalize_close()
        assert provider not in _RETIRED_THUMBNAIL_PROVIDERS

@pytest.mark.parametrize('key', [
    'viewer_delete_mode', 'browser_filename_display', 'archive_backend_preference',
    'video_thumbnail_backend', 'video_thumbnail_frame_mode',
    'browser_external_drop_behavior', 'favorite_drop_default_operation',
    'thumbnail_frame_ratio', 'thumbnail_crop_mode', 'browser_thumbnail_display_mode',
    'thumbnail_quality_mode', 'browser_sort_order', 'browser_display_density',
    'browser_sidebar_layout', 'folder_tree_sync_mode', 'viewer_canvas_left_click_action',
    'viewer_canvas_click_direction', 'open_viewer_behavior', 'viewer_prefetch_preset',
    'book_open_position', 'viewer_downscale_algorithm', 'viewer_upscale_algorithm',
    'magnifier_downscale_algorithm', 'magnifier_upscale_algorithm',
])
@pytest.mark.parametrize('value', [[], {}, None])
def test_invalid_setting_type_falls_back(tmp_path, key, value):
    from app.config_manager import ConfigManager
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({key: value, 'thumbnail_size': 192}), encoding='utf-8')
    config = ConfigManager(path)
    assert config.load()[key] == ConfigManager.DEFAULTS[key]
    assert config.get('thumbnail_size') == 192
    config.apply({key: value})
    assert config.get(key) == ConfigManager.DEFAULTS[key]


@pytest.mark.parametrize('operation', ['external', 'clipboard'])
def test_external_archive_export_propagates_cancellation(tmp_path, monkeypatch, qapp, operation):
    if operation == 'external':
        from app import viewer_external_open as module
    else:
        from app import viewer_clipboard_image as module
    from app.image_source import SevenZipImageSource
    from tests.test_external_archive_image_source import FakeBackend, archive_entry
    started, release = Event(), Event()
    captured = []

    class Backend(FakeBackend):
        def read_entry(self, *args, cancel_token=None, **kwargs):
            captured.append(cancel_token)
            started.set()
            release.wait(5)
            return self.data

    archive = tmp_path / 'book.cb7'
    archive.write_bytes(b'archive')
    backend = Backend((archive_entry('001.png'),))
    source = SevenZipImageSource(archive, backend=backend)
    monkeypatch.setattr(module, 'create_image_source', lambda *a, **kw: (source, None))
    worker = (module.ArchiveImageExport('request', archive, '001.png', tmp_path / 'export', None)
              if operation == 'external' else
              module.ClipboardImageDecode('request', archive, '001.png', source_options={}))
    thread = Thread(target=worker.run)
    thread.start()
    try:
        assert started.wait(3)
        worker.cancelled.set()
        assert captured[0].is_set(), 'active extraction did not receive the export cancellation'
    finally:
        release.set()
        thread.join(5)
        assert not thread.is_alive()


def test_last_browser_close_does_not_quit_with_pending_thumbnail_save(tmp_path, monkeypatch, qapp):
    from tests.test_application_controller import make_controller, wait_until
    from app.thumbnail_provider import _RETIRED_THUMBNAIL_PROVIDERS
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    provider = browser.thumbnail_provider
    entered, release = Event(), Event()
    quit_calls = []
    monkeypatch.setattr(qapp, 'quit', lambda: quit_calls.append(True))

    class SlowSave(QRunnable):
        def run(self):
            entered.set()
            release.wait(10)

    provider._save_pool.start(SlowSave())
    try:
        assert entered.wait(3)
        browser.close()
        qapp.processEvents()
        assert provider in _RETIRED_THUMBNAIL_PROVIDERS
        assert provider._save_pool.activeThreadCount() == 1
        assert not quit_calls, 'application quit committed while retired thumbnail save was still active'
        release.set()
        assert wait_until(qapp, lambda: bool(quit_calls), timeout=5)
        assert controller._shutdown_complete
        assert provider not in _RETIRED_THUMBNAIL_PROVIDERS
    finally:
        release.set()
        assert provider.wait_for_done(3000)
        provider._release_retired_if_idle()
        assert wait_until(qapp, controller.shutdown, timeout=5)
        qapp.processEvents()


def test_folder_tree_retry_is_cancelled_before_browser_destroy(tmp_path, monkeypatch, qapp):
    import sys
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtTest import QTest
    from tests.test_application_controller import make_controller
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    errors = []
    monkeypatch.setattr(qapp, 'quit', lambda: None)
    monkeypatch.setattr(sys, 'excepthook', lambda typ, value, tb: errors.append(str(value)))
    browser.folder_tree_sync.sync(tmp_path / 'missing', mode='focus_current', collapse_unrelated=True)
    browser.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QTest.qWait(100)
    assert controller.shutdown()
    assert not errors, errors


def test_shutdown_does_not_wait_for_another_owners_thumbnail_provider(tmp_path, qapp):
    from app.thumbnail_provider import BrowserThumbnailProvider
    from tests.test_application_controller import make_controller, wait_until
    controller = make_controller(tmp_path, qapp)
    foreign = BrowserThumbnailProvider()
    entered, release = Event(), Event()

    class ForeignSave(QRunnable):
        def run(self):
            entered.set()
            release.wait(10)

    foreign._save_pool.start(ForeignSave())
    try:
        assert entered.wait(3)
        foreign.close(wait_msecs=0)
        assert controller.shutdown(), 'an unrelated thumbnail owner blocked this controller'
    finally:
        release.set()
        assert foreign.wait_for_done(3000)
        foreign._release_retired_if_idle()
        assert wait_until(qapp, controller.shutdown, timeout=5)


def test_pdf_clipboard_cancellation_reaches_render(tmp_path, monkeypatch, qapp):
    from app import viewer_clipboard_image as module
    from app.pdf_image_source import PdfImageSource
    from tests.test_pdf_support import FakeBackend
    started, release = Event(), Event()
    tokens = []

    class Service(FakeBackend):
        def render_page(self, request, *, cancel_token=None):
            tokens.append(cancel_token)
            started.set()
            release.wait(5)
            return super().render_page(request, cancel_token=cancel_token)

    path = tmp_path / 'book.pdf'
    path.write_bytes(b'controlled service')
    service = Service()
    source = PdfImageSource(path, pdfium_service=service)
    monkeypatch.setattr(module, 'create_image_source', lambda *a, **kw: (source, None))
    worker = module.ClipboardImageDecode('request', path, source.list_images()[0], source_options={})
    thread = Thread(target=worker.run)
    thread.start()
    try:
        assert started.wait(3)
        worker.cancelled.set()
        assert tokens[0].is_set()
    finally:
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert source.document_id in service.closed


def test_viewer_hides_but_keeps_auxiliary_source_until_worker_drains(tmp_path, monkeypatch, qapp):
    from PIL import Image
    from app import viewer_clipboard_image as module
    from app.config_manager import ConfigManager
    from app.viewer_window import ViewerWindow
    from tests.test_application_controller import wait_until
    started, release, source_closed = Event(), Event(), Event()

    class Source:
        def open_image(self, image_id):
            started.set()
            release.wait(5)
            return Image.new('RGB', (8, 8))

        def close(self):
            source_closed.set()

    monkeypatch.setattr(module, 'create_image_source', lambda *a, **kw: (Source(), None))
    window = ViewerWindow(config_manager=ConfigManager(tmp_path / 'config.json'))
    worker = module.ClipboardImageDecode('request', tmp_path, 'image', source_options={})
    window._clipboard_image_worker = worker
    window.show()
    window._start_auxiliary_image_worker(worker)
    try:
        assert started.wait(3)
        window.close()
        assert not window.isVisible()
        qapp.processEvents()
        assert worker.cancelled.is_set()
        assert not window._shutdown_cleanup_complete
        assert not source_closed.is_set()
        release.set()
        assert wait_until(qapp, lambda: window._shutdown_cleanup_complete, timeout=5)
        assert source_closed.is_set()
    finally:
        release.set()
        assert wait_until(qapp, lambda: window._shutdown_cleanup_complete, timeout=5)
