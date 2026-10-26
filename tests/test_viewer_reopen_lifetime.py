from dataclasses import replace
from io import BytesIO
from threading import Event
from zipfile import ZipFile

from PIL import Image
from PySide6.QtGui import QScreen
import pytest

from tests.test_application_controller import (
    close_controller, finish_viewer_open, make_controller, wait_until,
)


@pytest.mark.parametrize("close_first", [False, True])
def test_same_zip_reopen_after_screen_loss_rejects_old_scroll_completion(
    tmp_path, qapp, monkeypatch, close_first,
):
    shelf = tmp_path / "books"
    shelf.mkdir()
    archive = shelf / "日本語.zip"
    with ZipFile(archive, "w") as output:
        for page in range(5):
            payload = BytesIO()
            with Image.new("RGB", (128, 192), (page * 40, 80, 100)) as image:
                image.save(payload, "PNG")
            output.writestr(f"{page}.png", payload.getvalue())
    controller = make_controller(tmp_path / "profile", qapp)
    controller.config.apply({
        "fullscreen": True, "view_mode": "single", "show_page_list": False,
        "book_open_position": "first_page", "viewer_memory_mode": "256",
        "viewer_spread_parallel_trial": False, "viewer_current_extra_trial": False,
    })
    browser = controller.create_browser_window()
    window = controller.create_viewer_window()
    monkeypatch.setattr(window, "_raster_background_allowed", lambda: False)
    entered, release = Event(), Event()
    try:
        assert browser.navigate_to(shelf, force_reload=True)
        assert browser.wait_for_scan()
        controller.open_path(archive, open_in_new_window=False)
        finish_viewer_open(qapp, window)
        assert wait_until(qapp, lambda: window.viewer.displayed_page_indexes == (0,))
        for page in (1, 2):
            window.next_page()
            assert wait_until(qapp, lambda: window.viewer.displayed_page_indexes == (page,))
        chrome = window.fullscreen_chrome
        chrome._fullscreen_restore_state = replace(
            chrome._fullscreen_restore_state, screen=QScreen.__new__(QScreen),
        )
        window.mouse_gesture_bindings = {"U": "toggle_fullscreen"}
        window._on_mouse_gesture("U")
        assert not window.isFullScreen() and window.isVisible()
        assert not chrome._fullscreen_transitioning
        assert window.viewer.displayed_page_indexes == (2,)

        source = window.book_session.source
        runtime = window.book_session.viewer_runtime
        original = source.open_image
        def blocked_old_pixels(name):
            if name == "3.png":
                entered.set()
                assert release.wait(10)
            return original(name)
        monkeypatch.setattr(source, "open_image", blocked_old_pixels)
        window.next_page()
        assert wait_until(qapp, entered.is_set)
        assert window.viewer.displayed_page_indexes == (2,)
        if close_first:
            window.close()
            assert window._close_pending and not window.isVisible()
            assert not window.dispatch_command("toggle_fullscreen")
            assert not window.isVisible()
        reopened = controller.open_path(archive, open_in_new_window=False)
        assert (reopened is not window) is close_first
        assert wait_until(qapp, lambda: reopened.book_session.source is not source, timeout=5)
        # The old uninterruptible decoder may still own a shared execution
        # lane. Release it after source replacement, so its late completion
        # races the new cover without requiring an extra native decode lane.
        release.set()
        assert wait_until(qapp, lambda: reopened.viewer.displayed_page_indexes == (0,), timeout=5)
        finish_viewer_open(qapp, reopened)
        assert reopened.model.current_index == 0
        assert reopened.book_session.source is not source
        assert reopened.book_session.viewer_runtime is not runtime
        token = reopened.presentation_state.displayed.token
        assert wait_until(qapp, lambda: not runtime.has_unfinished_tasks(), timeout=5)
        assert wait_until(qapp, source._closed.is_set, timeout=5)
        if close_first:
            assert wait_until(qapp, lambda: window not in controller._viewer_windows, timeout=5)
        qapp.processEvents()
        assert reopened.viewer.displayed_page_indexes == (0,)
        assert reopened.presentation_state.displayed.token == token
        reopened.next_page()
        assert wait_until(qapp, lambda: reopened.viewer.displayed_page_indexes == (1,), timeout=5)
    finally:
        release.set()
        close_controller(controller, qapp)
