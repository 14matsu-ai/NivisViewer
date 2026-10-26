from io import BytesIO
from threading import Event
from types import SimpleNamespace
from zipfile import ZipFile

from PIL import Image
from PySide6.QtWidgets import QMessageBox
import pytest

from app.browser_window import BrowserWindow
from app.zippla_filename_metadata import ZipPlaFilenameMetadata
from tests.test_application_controller import (
    close_controller, finish_viewer_open, make_controller, wait_until,
)


@pytest.mark.parametrize("drained", [False, True])
def test_closed_viewer_is_drained_without_open_window_confirmation(monkeypatch, drained):
    retiring = SimpleNamespace(_close_pending=True)
    closed, statuses = [], []
    browser = SimpleNamespace(
        _affected_viewers_handler=lambda _paths: (retiring,),
        _close_affected_viewers_handler=lambda viewers: closed.append(viewers) or drained,
        _show_temporary_status=statuses.append,
    )
    def unexpected_question(*_args, **_kwargs):
        pytest.fail("already closed Viewer prompted to close again")
    monkeypatch.setattr(QMessageBox, "question", unexpected_question)

    assert BrowserWindow._confirm_and_close_affected_viewers(browser, ("book.zip",)) is drained
    assert closed == [(retiring,)]
    assert bool(statuses) is not drained


@pytest.mark.parametrize("accept", [False, True])
def test_live_viewer_still_requires_confirmation_alongside_closed_viewer(monkeypatch, accept):
    retiring = SimpleNamespace(_close_pending=True)
    live = SimpleNamespace(_close_pending=False)
    closed, questions = [], []
    browser = SimpleNamespace(
        _affected_viewers_handler=lambda _paths: (retiring, live),
        _close_affected_viewers_handler=lambda viewers: closed.append(viewers) or True,
        _show_temporary_status=lambda _message: None,
    )
    def answer(*_args, **_kwargs):
        questions.append(True)
        return QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", answer)

    assert BrowserWindow._confirm_and_close_affected_viewers(browser, ("book.zip",)) is accept
    assert questions == [True]
    assert closed == ([(retiring, live)] if accept else [])


@pytest.mark.parametrize("clear", [False, True])
def test_rating_after_close_keeps_archive_safe_without_false_open_warning(tmp_path, qapp, monkeypatch, clear):
    shelf = tmp_path / "books"
    shelf.mkdir()
    archive = shelf / "日本語.zip"
    if clear:
        archive = ZipPlaFilenameMetadata.parse(archive).with_rating(4).serialized_path()
    with Image.new("RGB", (32, 48), "navy") as image:
        payload = BytesIO()
        image.save(payload, "PNG")
    with ZipFile(archive, "w") as output:
        for page in range(3):
            output.writestr(f"{page}.png", payload.getvalue())
    controller = make_controller(tmp_path / "profile", qapp)
    controller.config.apply({"view_mode": "single", "show_page_list": False})
    browser = controller.create_browser_window()
    window = controller.create_viewer_window()
    monkeypatch.setattr(window, "_raster_background_allowed", lambda: False)
    entered, release = Event(), Event()
    statuses, waits = [], []
    try:
        assert browser.navigate_to(shelf, force_reload=True)
        assert browser.wait_for_scan()
        controller.open_path(archive, open_in_new_window=False)
        finish_viewer_open(qapp, window)
        source = window.book_session.source
        original = source.open_image
        def blocked_pixels(name):
            entered.set()
            assert release.wait(10)
            return original(name)
        monkeypatch.setattr(source, "open_image", blocked_pixels)
        window.next_page()
        assert wait_until(qapp, entered.is_set)
        window.close()
        assert window._close_pending and not window.isVisible()
        assert window in controller.viewers_using_paths((str(archive),))
        original_shutdown = window.prepare_shutdown
        def observed_shutdown(*, wait_msecs=250):
            waits.append(wait_msecs)
            return original_shutdown(wait_msecs=wait_msecs)
        monkeypatch.setattr(window, "prepare_shutdown", observed_shutdown)
        monkeypatch.setattr(browser, "_show_temporary_status", statuses.append)
        def unexpected_question(*_args, **_kwargs):
            pytest.fail("closed Viewer was treated as an open window")
        monkeypatch.setattr(QMessageBox, "question", unexpected_question)
        rating = None if clear else 3
        assert not browser.set_rating_for_paths((str(archive),), rating)
        assert waits and all(wait == 0 for wait in waits)
        assert statuses and "解放" in statuses[-1]
        assert archive.exists() and not source._closed.is_set()
        release.set()
        assert wait_until(qapp, lambda: window not in controller._viewer_windows, timeout=5)
        assert source._closed.is_set()
        assert not controller.viewers_using_paths((str(archive),))
        assert browser.set_rating_for_paths((str(archive),), rating)
        destination = ZipPlaFilenameMetadata.parse(archive).with_rating(rating).serialized_path()
        assert destination.exists() and not archive.exists()
    finally:
        release.set()
        close_controller(controller, qapp)
