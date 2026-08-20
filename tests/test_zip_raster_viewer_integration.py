from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic
import zipfile

from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.image_source import ImageSourceError, ZipImageSource
from app.viewer_window import ViewerWindow
from app.zip_raster_book_runtime import (
    ZipRasterDisplayUnit,
    ZipRasterPage,
    ZipRasterRenderSpec,
    ZipRasterRequest,
)


def _write_zip(tmp_path: Path, *, pages: int = 3) -> Path:
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for index in range(pages):
            path = tmp_path / f"{index:03d}.png"
            with Image.new(
                "RGB",
                (120 + index * 10, 180 + index * 10),
                (80, 100 + index * 20, 140),
            ) as image:
                image.save(path)
            output.write(path, path.name)
    return archive


def _window(
    tmp_path: Path,
) -> tuple[ViewerWindow, BookSession, ZipImageSource, Path]:
    archive = _write_zip(tmp_path)
    source = ZipImageSource(archive)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
    )
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    return window, session, source, archive


def _wait_until(
    qapp: QApplication,
    predicate,
    *,
    timeout_ms: int = 3000,
) -> None:
    deadline = monotonic() + timeout_ms / 1000
    while not predicate() and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(5)
    assert predicate()


def test_zip_book_uses_one_runtime_across_spread_rotation_filter_and_page_list(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    window, session, _source, archive = _window(tmp_path)
    try:
        opened = session.open_book(archive)
        runtime = session.viewer_runtime
        assert runtime is not None
        captured: list[ZipRasterRequest] = []
        monkeypatch.setattr(runtime, "has_cached_current", lambda _request: True)
        monkeypatch.setattr(
            runtime,
            "request",
            lambda request: captured.append(request) or True,
        )
        monkeypatch.setattr(
            window,
            "_render_spread",
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("ZIP book entered legacy Viewer pipeline")
            ),
        )

        assert window._finish_opened_book(opened, modal_on_empty=False)
        assert window._zip_runtime_active
        assert window.viewer._direct_display_mode

        window.set_view_mode("spread")
        window.rotate_right()
        window.set_viewer_resampling_mode("high_quality")
        window._set_image_adjustments(brightness=1.2)
        window.page_list_dock.show()
        window._refresh_view()

        assert captured
        assert all(request.source_epoch == session.generation for request in captured)
        assert captured[-1].render_spec.rotation == 90
        assert captured[-1].render_spec.resampling_mode == "high_quality"
        assert captured[-1].render_spec.brightness == 1.2
        assert window._zip_runtime is runtime
        assert window._zip_runtime_active
        assert window.viewer._direct_display_mode
    finally:
        window.close()
        qapp.processEvents()


def test_zip_runtime_commits_complete_spread_and_retains_source_for_magnifier(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, session, _source, archive = _window(tmp_path)
    window.view_mode = "spread"
    window.single_first_page = False
    window.reading_direction = "ltr"
    window.model.update_options(
        view_mode="spread",
        single_first_page=False,
        reading_direction="ltr",
    )
    try:
        opened = session.open_book(archive)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        runtime = session.viewer_runtime
        assert runtime is not None
        _wait_until(
            qapp,
            lambda: len(window.viewer._images) == 2
            and all(
                image.display_prepared and image.pixmap is not None
                for image in window.viewer._images
            ),
        )

        assert window.viewer.displayed_page_indexes == (0, 1)
        assert all(image.qimage is not None for image in window.viewer._images)
        assert window._applied_display_request_id == window._active_request_id
        assert window._zip_runtime_current_frame_serial > 0

        # The ZIP runtime owns the main display, but the NivisViewer
        # magnifier remains an interactive projection of the committed source.
        source_image = window.viewer._images[0]
        assert source_image.qimage is not None
        window.viewer.magnifier_selecting = True
        window.viewer.magnifier_source_page = source_image.page_index
        window.viewer._magnifier_source_image_id = source_image.image_id
        window.viewer.magnifier_source_rect = QRectF(
            0,
            0,
            max(1, source_image.qimage.width() // 2),
            max(1, source_image.qimage.height() // 2),
        )
        window.viewer._request_magnifier_render()
        _wait_until(qapp, lambda: window.viewer.magnifier_active)
        assert window.viewer._magnifier_pixmap is not None
    finally:
        window.close()
        qapp.processEvents()


def test_book_switch_keeps_archive_alive_until_runtime_job_stops(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = _write_zip(tmp_path)

    class BlockingZipSource(ZipImageSource):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.entered = Event()
            self.release = Event()

        def open_image(self, image_id: str) -> Image.Image:
            self.entered.set()
            self.release.wait(3.0)
            return super().open_image(image_id)

    source = BlockingZipSource(archive)
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
    )
    try:
        session.open_book(archive)
        runtime = session.viewer_runtime
        assert runtime is not None
        page = runtime.source.list_images()[0]
        unit = ZipRasterDisplayUnit(
            0,
            (ZipRasterPage(0, page),),
            True,
        )
        assert runtime.request(
            ZipRasterRequest(
                session.generation,
                1,
                unit,
                (unit,),
                ZipRasterRenderSpec((640, 480)),
            )
        )
        assert source.entered.wait(2.0)

        session.close_book()
        qapp.processEvents()

        assert session.viewer_runtime is None
        assert not source._closed.is_set()

        source.release.set()
        assert runtime.wait_for_done(3000)
        _wait_until(qapp, source._closed.is_set)
    finally:
        source.release.set()
        session.shutdown(3000)
        qapp.processEvents()


def test_failed_replacement_open_keeps_active_zip_runtime_epoch(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = _write_zip(tmp_path)
    source = ZipImageSource(archive)

    def source_factory(path: Path, **_kwargs):
        if path.name == "broken.zip":
            raise ImageSourceError("broken replacement")
        return source, None

    session = BookSession(source_factory=source_factory)
    config = ConfigManager(tmp_path / "failed-open-config.json")
    config.load()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    try:
        opened = session.open_book(archive)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        runtime = session.viewer_runtime
        active_epoch = session.generation
        assert runtime is not None
        _wait_until(qapp, lambda: window._zip_runtime_current_frame_serial > 0)
        old_displayed_page = window.presentation_state.displayed_page
        old_slider = window.slider.value()
        old_status = window.status.currentMessage()
        old_progress = window.presentation_state.progress_page
        old_back_history = window.presentation_state.back_history

        window.open_path(tmp_path / "broken.zip")
        assert window.presentation_state.replacement_open_pending
        assert window.presentation_state.displayed_page == old_displayed_page
        assert window.slider.value() == old_slider
        assert session.wait_for_async(3000)
        _wait_until(
            qapp,
            lambda: window._zip_runtime_active
            and window._zip_runtime_current_frame_serial > 0,
        )

        assert session.generation == active_epoch
        assert session.viewer_runtime is runtime
        assert window._zip_runtime is runtime
        assert window.viewer.displayed_page_indexes == (0,)
        assert window.presentation_state.current_book_epoch == active_epoch
        assert window.presentation_state.displayed_page == old_displayed_page
        assert window.presentation_state.progress_page == old_progress
        assert window.presentation_state.back_history == old_back_history
        assert window.slider.value() == old_slider
        # The temporary open-error override may still be visible. Once it is
        # released, the committed presentation status remains the old book.
        window._clear_status_override()
        window._update_status()
        assert window.status.currentMessage() == old_status
    finally:
        window.close()
        qapp.processEvents()
