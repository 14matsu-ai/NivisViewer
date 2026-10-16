"""Native association stalls must never hold Browser paint or thumbnail reads."""
from threading import Event, get_ident
from time import monotonic, sleep

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QImage

from app.shell_icon_provider import ShellAssociatedIconProvider, _ShellIconPixels
from tests.test_browser_thumbnail_first_exposure import fixture_listing
from tests.test_browser_retained_scroll_qt import make_browser
from tests.test_browser_thumbnail_ram_restore import wait_visible
from tests.test_application_controller import wait_until


def test_shell_worker_transfers_plain_pixels_and_qt_images_stay_on_gui(qapp, monkeypatch):
    from app import shell_icon_provider as module
    gui = get_ident()
    constructor_threads, query_threads, notifications = [], [], []
    image_type = QImage

    def gui_image(*args, **kwargs):
        constructor_threads.append(get_ident())
        assert get_ident() == gui, 'Shell worker must not enter Qt image construction'
        return image_type(*args, **kwargs)
    gui_image.Format = image_type.Format
    monkeypatch.setattr(module, 'QImage', gui_image)

    def query(_extension, *, folder, physical_size):
        query_threads.append(get_ident())
        return _ShellIconPixels(physical_size, bytes((0x12, 0x34, 0x56, 0x78)) * physical_size ** 2)
    monkeypatch.setattr(ShellAssociatedIconProvider, '_windows_pixels', staticmethod(query))
    owner = QObject()
    icons = ShellAssociatedIconProvider()
    icons.enable_async(owner)
    icons.image_ready.connect(lambda: notifications.append(get_ident()))
    assert icons.image_for_extension('.test', logical_size=32, device_pixel_ratio=2.5).isNull()
    assert wait_until(qapp, lambda: bool(notifications), timeout=3)
    assert query_threads and all(thread != gui for thread in query_threads)
    assert constructor_threads and set(constructor_threads) == {gui}
    assert notifications == [gui]
    image = icons.image_for_extension('.test', logical_size=32, device_pixel_ratio=2.5)
    color = image.pixelColor(0, 0)
    assert (color.red(), color.green(), color.blue(), color.alpha()) == (0x56, 0x34, 0x12, 0x78)
    image.fill(Qt.GlobalColor.blue)
    assert icons.image_for_extension('.test', logical_size=32, device_pixel_ratio=2.5).pixelColor(0, 0) == color


def test_failed_shell_query_does_not_strand_following_request(qapp, monkeypatch):
    def query(extension, *, folder, physical_size):
        if extension == '.bad':
            raise OSError('association unavailable')
        return _ShellIconPixels(physical_size, bytes((0, 0, 255, 255)) * physical_size ** 2)
    monkeypatch.setattr(ShellAssociatedIconProvider, '_windows_pixels', staticmethod(query))
    owner = QObject()
    icons = ShellAssociatedIconProvider()
    icons.enable_async(owner)
    icons.image_for_extension('.bad')
    icons.image_for_extension('.good')
    assert wait_until(qapp, lambda: not icons._pending_icons, timeout=3)
    assert icons.image_for_extension('.bad').isNull()
    assert not icons.image_for_extension('.good').isNull()
    assert not icons._icon_running


def test_blocked_shell_lookup_does_not_block_browser_paint_or_thumbnail_reads(tmp_path, qapp, monkeypatch):
    entered, release = Event(), Event()
    threads = []
    gui = get_ident()
    def query(extension, *, folder, physical_size):
        threads.append(get_ident())
        entered.set()
        assert release.wait(10)
        return _ShellIconPixels(physical_size, bytes((0, 0, 255, 255)) * physical_size ** 2)
    monkeypatch.setattr(ShellAssociatedIconProvider, '_windows_pixels', staticmethod(query))
    fixture_listing(tmp_path/'images', 80)
    window = make_browser(tmp_path, tmp_path/'images', qapp, 2, True)
    try:
        wait_visible(window, qapp)
        icons = window.item_delegate.shell_icon_provider
        icons.image_for_extension('.unseen', logical_size=96, device_pixel_ratio=2.5)
        deadline = monotonic()+3
        while not entered.is_set() and monotonic()<deadline:
            qapp.processEvents(); sleep(.001)
        assert entered.is_set()
        assert threads and gui not in threads
        # Actual QListView paints, including an uncached large placeholder.
        window.item_model._thumbnail_images.clear()
        window.list_view.viewport().repaint()
        qapp.processEvents()
        assert not release.is_set()
        assert icons._icon_running
        for _ in range(50):
            assert icons.image_for_extension('.unseen', logical_size=96, device_pixel_ratio=2.5).isNull()
        assert len(icons._pending_icons) <= 16
        assert len([k for k in icons._pending_icons if k[0] == '.unseen']) == 1
        release.set()
        deadline = monotonic()+3
        while icons._pending_icons and monotonic()<deadline:
            qapp.processEvents(); sleep(.002)
        assert not icons._pending_icons
        assert not icons.image_for_extension('.unseen', logical_size=96, device_pixel_ratio=2.5).isNull()
        assert gui not in threads
    finally:
        release.set()
        assert wait_until(qapp, lambda: window.thumbnail_provider.wait_for_done(25), timeout=10)
        assert wait_until(qapp, window.prepare_shutdown, timeout=10)
        # Browser close can retire a provider after its bounded 250ms wait.
        # Keep the test process alive until its native save lane has drained.
        assert window.thumbnail_provider.wait_for_done(5000)
        window.close(); qapp.processEvents()


def test_thumbnail_completion_cannot_postpone_already_due_visible_request(tmp_path, qapp):
    fixture_listing(tmp_path/'images', 10)
    window = make_browser(tmp_path, tmp_path/'images', qapp, 0, True)
    try:
        window._thumbnail_request_timer.start(0)
        for _ in range(20):
            window._schedule_thumbnail_requests(30)
        assert window._thumbnail_request_timer.remainingTime() == 0
    finally:
        assert wait_until(qapp, lambda: window.thumbnail_provider.wait_for_done(25), timeout=10)
        assert wait_until(qapp, window.prepare_shutdown, timeout=10)
        assert window.thumbnail_provider.wait_for_done(5000)
        window.close(); qapp.processEvents()
