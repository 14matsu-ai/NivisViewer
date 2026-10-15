"""Native association stalls must never hold Browser paint or thumbnail reads."""
from threading import Event, get_ident
from time import monotonic, sleep

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from app.shell_icon_provider import ShellAssociatedIconProvider
from tests.test_browser_thumbnail_first_exposure import fixture_listing
from tests.test_browser_retained_scroll_qt import make_browser
from tests.test_browser_thumbnail_ram_restore import wait_visible


def test_blocked_shell_lookup_does_not_block_browser_paint_or_thumbnail_reads(tmp_path, qapp, monkeypatch):
    entered, release = Event(), Event()
    threads = []
    gui = get_ident()
    def query(extension, *, folder, physical_size):
        threads.append(get_ident())
        entered.set()
        assert release.wait(10)
        image = QImage(physical_size, physical_size, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        return image
    monkeypatch.setattr(ShellAssociatedIconProvider, '_windows_image', staticmethod(query))
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
        window.close(); qapp.processEvents()
