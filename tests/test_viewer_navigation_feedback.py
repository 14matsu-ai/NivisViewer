from pathlib import Path
from threading import Event
import re
import zipfile

from PIL import Image
import pytest
from PySide6.QtCore import Qt

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.image_source import FolderImageSource, ZipImageSource
from app.viewer_navigation_policy import NavigationInputKind
from app.viewer_window import ViewerWindow
from app.viewer_widget import ViewerFrameCommit
from tests.test_zip_raster_viewer_integration import _wait_until


def _assert_controls(window, index, total=5):
    assert window.slider.value() == index
    assert window.slider.maximum() == total - 1
    match = re.search(r"(\d+) / (\d+)", window.status.currentMessage())
    assert match is not None, window.status.currentMessage()
    assert tuple(map(int, match.groups())) == (index + 1, total)


@pytest.fixture(params=["zip", "folder"])
def blocked_viewer(request, tmp_path, qapp):
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for i in range(5):
            path = tmp_path / f"{i:03}.png"
            with Image.new("RGB", (80, 120), (i * 30, 60, 90)) as image:
                image.save(path)
            output.write(path, path.name)
    base = ZipImageSource if request.param == "zip" else FolderImageSource

    class BlockedSource(base):
        started = Event()
        release = Event()

        def open_image(self, image_id):
            if Path(image_id).stem != "000":
                self.started.set()
                assert self.release.wait(5)
            return super().open_image(image_id)

    path = archive if request.param == "zip" else tmp_path
    source = BlockedSource(path)
    session = BookSession(source_factory=lambda *_args, **_kwargs: (source, None))
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    window.set_view_mode("single")
    window.show()
    qapp.processEvents()
    try:
        assert window._finish_opened_book(session.open_book(path), modal_on_empty=False)
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        assert source.started.wait(1)
        yield window, source
    finally:
        source.release.set()
        window.close()
        qapp.processEvents()


def test_cold_wheel_target_feedback_precedes_release_and_preserves_commit(blocked_viewer, qapp):
    window, source = blocked_viewer
    state = window.presentation_state
    displayed, progress, history = state.displayed, state.progress_values, state.back_history
    pixmap = window.viewer._images[0].pixmap.cacheKey()
    for target in (1, 2, 3, 2):
        window._go_to_index_with_history(target, input_kind=NavigationInputKind.WHEEL)
        _assert_controls(window, target)
        assert state.displayed is displayed
        assert state.status_values is displayed.values
        assert state.progress_values is progress
        assert state.back_history == history
        assert window.viewer._images[0].pixmap.cacheKey() == pixmap
        assert displayed.values.path in window.status.currentMessage()
        if target == 3:
            assert window._pending_zip_runtime_request is not None
    # Direction reversal may admit its first cold target immediately; feedback
    # must work for both dispatched and still-staged accepted destinations.
    window._finish_wheel_navigation()
    _assert_controls(window, 2)
    source.release.set()
    _wait_until(qapp, lambda: state.displayed_page == 2)
    _assert_controls(window, 2)
    assert state.progress_page == 2
    assert [entry.values.page_index for entry in state.back_history] == [0]


def test_slider_drag_keeps_target_and_programmatic_feedback_is_silent(blocked_viewer, qapp):
    window, source = blocked_viewer
    emitted = []
    window.slider.focusedPageRequested.connect(emitted.append)
    serial = window.presentation_state.request_serial
    window.slider.setSliderDown(True)
    window.slider.setValue(3)
    _assert_controls(window, 3)
    assert window.presentation_state.request_serial == serial + 1
    for _ in range(3):
        window._update_slider()
    assert emitted == [3]
    assert window.slider.isSliderDown()
    assert window.presentation_state.displayed_page == 0
    window.slider.setSliderDown(False)
    source.release.set()
    _wait_until(qapp, lambda: window.presentation_state.displayed_page == 3)
    _assert_controls(window, 3)


def test_abandonment_and_stale_error_do_not_corrupt_newer_feedback(blocked_viewer):
    window, _source = blocked_viewer
    window._go_to_index_with_history(2)
    obsolete = window.presentation_state.requested
    window._fail_raster_runtime_request(window._zip_runtime)
    assert window.slider.value() == 0
    window._clear_status_override()
    window._update_status()
    _assert_controls(window, 0)
    window._go_to_index_with_history(3)
    _assert_controls(window, 3)
    window._on_viewer_frame_committed(ViewerFrameCommit(
        obsolete.token, 99,
        tuple((page.index, page.image_id) for page in obsolete.unit.pages),
        obsolete.unit.page_indexes, tuple(page.image_id for page in obsolete.unit.pages),
        obsolete.unit.page_indexes,  # A late error for the obsolete frame.
    ))
    _assert_controls(window, 3)
    assert window.presentation_state.progress_page == 0
    window.presentation_state.supersede_pending()
    window._project_presentation_surface()
    _assert_controls(window, 0)
    window.presentation_state.clear_book()
    window._project_presentation_surface()
    assert window.slider.value() == 0 and not window.slider.isEnabled()
    assert not re.search(r"\d+ / \d+", window.status.currentMessage())


@pytest.mark.parametrize("direction", ["ltr", "rtl"])
def test_spread_feedback_uses_focused_page_and_does_not_reveal_hidden_chrome(blocked_viewer, direction):
    window, _source = blocked_viewer
    window.set_view_mode("spread")
    window.set_reading_direction(direction)
    window.slider.hide()
    window.status.hide()
    window._navigation_repeat_key = int(Qt.Key.Key_Right)
    window.page_navigation.go_to_focused_page_index(3, input_kind=NavigationInputKind.KEY_INITIAL)
    _assert_controls(window, 3)
    assert window.presentation_state.requested.unit.focused_index == 3
    assert 3 in window.presentation_state.requested.unit.page_indexes
    assert not window.slider.isVisible() and not window.status.isVisible()
    assert window.presentation_state.progress_page == 0
    window.page_navigation.go_to_focused_page_index(99, input_kind=NavigationInputKind.KEY_REPEAT)
    _assert_controls(window, 4)


def test_pdf_cold_target_uses_the_same_feedback_projection(tmp_path, qapp):
    from app.pdf_image_source import PdfImageSource
    from app.pdfium_service import PdfiumService
    from tests.test_pdf_support import FakeBackend

    release = Event()

    class BlockedBackend(FakeBackend):
        def render_page(self, request, *, cancel_token=None):
            if request.page_index != 0:
                assert release.wait(5)
            return super().render_page(request, cancel_token=cancel_token)

    path = tmp_path / "fake.pdf"
    path.write_bytes(b"fake-backend-fixture")
    service = PdfiumService(BlockedBackend())
    source = PdfImageSource(path, pdfium_service=service)
    session = BookSession(source_factory=lambda *_args, **_kwargs: (source, None))
    window = ViewerWindow(
        config_manager=ConfigManager(tmp_path / "config.json"),
        book_session=session, pdfium_service=service,
    )
    window.resize(640, 480)
    window.set_view_mode("single")
    try:
        assert window._finish_opened_book(session.open_book(path), modal_on_empty=False)
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
        window._go_to_index_with_history(2, input_kind=NavigationInputKind.WHEEL)
        _assert_controls(window, 2, total=3)
        assert window.presentation_state.displayed_page == window.presentation_state.progress_page == 0
        release.set()
        _wait_until(qapp, lambda: window.presentation_state.displayed_page == 2)
        _assert_controls(window, 2, total=3)
    finally:
        release.set()
        window.close()
        qapp.processEvents()
        session.shutdown(wait_msecs=3000)
        service.shutdown(wait_seconds=2)


def test_replacement_and_close_do_not_keep_the_previous_pending_target(blocked_viewer, tmp_path, monkeypatch, qapp):
    window, source = blocked_viewer
    window._go_to_index_with_history(3, input_kind=NavigationInputKind.WHEEL)
    _assert_controls(window, 3)
    old_epoch = window.presentation_state.current_book_epoch
    replacement = tmp_path / "replacement.zip"
    with zipfile.ZipFile(replacement, "w") as output:
        for i in range(2):
            output.writestr(f"new{i}.png", (tmp_path / f"{i:03}.png").read_bytes())
    monkeypatch.setattr(window.book_session, "_source_factory", lambda path, **_kwargs: (ZipImageSource(path), None))
    window.open_path(replacement)
    assert window.slider.value() == 0 and window.slider.maximum() == 4
    assert window.presentation_state.progress_page == 0
    source.release.set()
    _wait_until(qapp, lambda: (
        window.presentation_state.current_book_epoch not in (None, old_epoch)
    ))
    _assert_controls(window, 0, total=2)
    assert window.presentation_state.progress_page == 0
    window.prepare_shutdown(wait_msecs=3000)
    assert window.presentation_state.navigation_feedback is None
    assert window.slider.maximum() == 0 and not window.slider.isEnabled()
