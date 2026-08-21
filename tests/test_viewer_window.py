from __future__ import annotations

import os
import threading
import zipfile
from pathlib import Path
from time import monotonic

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.application_controller import ApplicationController
from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.image_cache import CachedImage
from app.image_source import ImageSource, ImageSourceError, ZipImageSource
from app import viewer_commands as commands
from app.viewer_window import ViewerWindow


def write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (8, 12), "white") as image:
        image.save(path)


def make_config(tmp_path: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return config


def finish_open(
    window: ViewerWindow,
    qapp: QApplication,
    *,
    timeout_ms: int = 3000,
) -> None:
    assert window.book_session.wait_for_async(timeout_ms)
    qapp.processEvents()


def current_page_list_page(window: ViewerWindow) -> int | None:
    index = window.page_list.currentIndex()
    if not index.isValid():
        return None
    return window.page_list_model.page_index_at(index.row())


class ControlledZipSource(ImageSource):
    load_sizes_lazily = True

    def __init__(self, path: Path, *, pages: int = 7) -> None:
        super().__init__(path)
        self.ids = [f"page-{index}.jpg" for index in range(pages)]
        self.read_calls: list[str] = []
        self.failures: set[str] = set()
        self._blocks: dict[str, tuple[threading.Event, threading.Event]] = {}

    def list_images(self) -> list[str]:
        return list(self.ids)

    def block(
        self,
        image_id: str,
    ) -> tuple[threading.Event, threading.Event]:
        started = threading.Event()
        release = threading.Event()
        self._blocks[image_id] = (started, release)
        return started, release

    def release_all(self) -> None:
        for _started, release in tuple(self._blocks.values()):
            release.set()

    def open_image(self, image_id: str) -> Image.Image:
        self.read_calls.append(image_id)
        block = self._blocks.get(image_id)
        if block is not None:
            started, release = block
            started.set()
            assert release.wait(3)
        if image_id in self.failures:
            raise ImageSourceError(f"broken archive entry: {image_id}")
        return Image.new("RGB", (80, 120), "white")

    def display_path(self, image_id: str) -> str:
        return f"{self.source_path}!/{image_id}"

    def fork_for_thumbnail(self) -> ImageSource:
        # PageList owns an independent read session; its low-priority reads
        # must not contaminate the main Viewer navigation assertions below.
        return ControlledZipSource(self.source_path, pages=len(self.ids))


def test_shared_config_is_injected(tmp_path: Path, qapp: QApplication) -> None:
    config = make_config(tmp_path)
    window = ViewerWindow(config_manager=config)

    assert window.config is config
    assert window.settings is config.data
    window.close()
    qapp.processEvents()


def test_each_viewer_owns_an_independent_book_session(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    first = ViewerWindow(config_manager=config)
    second = ViewerWindow(config_manager=config)

    assert first.book_session is not second.book_session
    assert first.image_cache is not second.image_cache
    first.viewer.set_manual_zoom(2.0)
    assert first.viewer.manual_zoom == 2.0
    assert second.viewer.manual_zoom == 1.0
    first.close()
    second.close()
    qapp.processEvents()


def test_open_path_displays_book(tmp_path: Path, qapp: QApplication) -> None:
    image = tmp_path / "日本語画像.jpg"
    write_image(image)
    window = ViewerWindow(config_manager=make_config(tmp_path))

    assert window.open_path(image)
    finish_open(window, qapp)
    assert window.book_session.current_path == image
    assert window.model.total_pages == 1
    # Folder books now use the book-scoped raster runtime.  Source install
    # still advances requested state first, but a tiny offscreen fixture may
    # finish before this test observes that transient state.
    assert window.presentation_state.requested_page == 0
    assert isinstance(
        window.book_session.viewer_runtime,
        FolderRasterBookRuntime,
    )
    deadline = monotonic() + 3
    while (
        window.presentation_state.displayed_page != 0
        and monotonic() < deadline
    ):
        qapp.processEvents()
        QTest.qWait(5)
    assert window.presentation_state.displayed_page == 0
    assert window.slider.isEnabled()
    window.close()
    qapp.processEvents()


def test_page_list_virtualizes_rows_and_loads_only_the_visible_frontier(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    class ManyPageSource(ImageSource):
        load_sizes_lazily = True

        def __init__(self, *, thumbnail: bool = False) -> None:
            super().__init__(tmp_path / "book")
            self.ids = [f"page-{index:04d}.jpg" for index in range(1000)]
            self.thumbnail = thumbnail
            self.open_calls: list[str] = []
            self.forks: list[ManyPageSource] = []
            self.closed = False

        def list_images(self) -> list[str]:
            return list(self.ids)

        def open_image(self, image_id: str) -> Image.Image:
            self.open_calls.append(image_id)
            return Image.new("RGB", (8, 12), "white")

        def display_path(self, image_id: str) -> str:
            return image_id

        def fork_for_thumbnail(self) -> ImageSource:
            fork = ManyPageSource(thumbnail=True)
            self.forks.append(fork)
            return fork

        def close(self) -> None:
            self.closed = True

    source = ManyPageSource()
    session = BookSession(
        source_factory=lambda *_args, **_kwargs: (source, None),
    )
    window = ViewerWindow(
        config_manager=make_config(tmp_path),
        book_session=session,
    )
    assert window.open_path(tmp_path / "book")
    finish_open(window, qapp)
    assert window.image_cache.wait_for_done(2000)
    qapp.processEvents()
    QTest.qWait(window._display_demand_timer.interval() + 20)
    qapp.processEvents()
    assert window.viewer.wait_for_rendering()
    qapp.processEvents()

    runtime = session.page_list_runtime
    assert runtime is not None
    assert window.page_list_model.rowCount() == 0
    assert not runtime.visible
    assert not runtime.has_unfinished_tasks()
    assert source.forks == []

    window.show()
    qapp.processEvents()
    window.set_page_list_visible(True)
    deadline = monotonic() + 3
    while not runtime.cached_pages and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(5)

    assert window.page_list_model.rowCount() == 1000
    assert current_page_list_page(window) == window.model.focused_index
    assert 0 < len(runtime.desired_pages) < 1000
    assert source.forks
    assert set(source.forks[0].open_calls).issubset(
        {
            runtime.image_ids[index]
            for index in runtime.desired_pages
        }
    )
    assert runtime.cached_pages
    first_cached = runtime.cached_pages[0]
    row = window.page_list_model.row_for_page(first_cached)
    icon = window.page_list_model.data(
        window.page_list_model.index(row, 0),
        Qt.ItemDataRole.DecorationRole,
    )
    assert icon is not None and not icon.isNull()

    window.set_page_list_visible(False)
    deadline = monotonic() + 3
    while runtime.has_unfinished_tasks() and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(5)
    qapp.processEvents()
    assert window.page_list_model.rowCount() == 0
    assert runtime.desired_pages == ()
    assert runtime.cached_pages == ()
    assert source.forks[0].closed
    window.close()
    qapp.processEvents()


def test_page_list_switches_with_first_committed_replacement_frame(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_folder = tmp_path / "first-book"
    second_folder = tmp_path / "second-book"
    first_image = first_folder / "first.jpg"
    second_image = second_folder / "second.jpg"
    write_image(first_image)
    write_image(second_image)
    config = make_config(tmp_path)
    config.apply({"show_page_list": True}, save=False)
    session = BookSession()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    window.show()
    try:
        first_opened = session.open_book(first_folder)
        assert window._finish_opened_book(first_opened, modal_on_empty=False)
        first_runtime = session.page_list_runtime
        assert first_runtime is not None
        deadline = monotonic() + 3
        while (
            window._page_list_runtime is not first_runtime
            and monotonic() < deadline
        ):
            qapp.processEvents()
            QTest.qWait(5)

        assert window._page_list_runtime is first_runtime
        assert window.page_list.isEnabled()
        assert window.page_list_model.image_id_for_page(0) == str(first_image)

        second_opened = session.open_book(second_folder)
        second_runtime = session.page_list_runtime
        assert second_runtime is not None
        assert second_runtime is not first_runtime

        # Source/model installation is not a presentation commit.  Keep the
        # preceding committed list as a disabled snapshot and do not retain a
        # pointer to BookSession's now-retired runtime.
        assert window._page_list_runtime is None
        assert window._staged_page_list_runtime is second_runtime
        assert not window.page_list.isEnabled()
        assert window.page_list_model.image_id_for_page(0) == str(first_image)
        qapp.processEvents()
        window._update_page_list_visible_work()
        assert window.page_list_model.image_id_for_page(0) == str(first_image)

        assert window._finish_opened_book(second_opened, modal_on_empty=False)
        assert session.image_cache.wait_for_done(2000)
        qapp.processEvents()
        QTest.qWait(window._display_demand_timer.interval() + 20)
        qapp.processEvents()
        assert window.viewer.wait_for_rendering()
        deadline = monotonic() + 3
        while (
            window._page_list_runtime is not second_runtime
            and monotonic() < deadline
        ):
            qapp.processEvents()
            window.viewer.wait_for_rendering()
            QTest.qWait(5)

        assert window._page_list_runtime is second_runtime
        assert window._staged_page_list_runtime is None
        assert window.page_list.isEnabled()
        assert window.page_list_model.image_id_for_page(0) == str(second_image)

        session.close_book()
        qapp.processEvents()
        assert window._page_list_runtime is None
        assert window._staged_page_list_runtime is None
        assert window.page_list_model.rowCount() == 0
    finally:
        window.close()
        qapp.processEvents()


def test_close_safely_shuts_down_book_session(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image = tmp_path / "page.jpg"
    write_image(image)
    session = BookSession()
    window = ViewerWindow(config_manager=make_config(tmp_path), book_session=session)
    window.open_path(image)

    window.close()

    assert session.source is None
    assert session.image_cache.source is None
    assert session.model.total_pages == 0
    qapp.processEvents()


@pytest.mark.parametrize("source_kind", ["folder", "zip"])
def test_folder_and_zip_source_preparation_runs_off_gui_thread(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    source_kind: str,
) -> None:
    folder = tmp_path / "book"
    first = folder / "1.jpg"
    second = folder / "2.jpg"
    write_image(first)
    write_image(second)
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(second, "2.jpg")
        output.write(first, "1.jpg")
    target = folder if source_kind == "folder" else archive
    gui_thread = threading.get_ident()
    stat_threads: list[int] = []
    listing_threads: list[int] = []
    original_stat = Path.stat
    original_scandir = os.scandir
    original_infolist = zipfile.ZipFile.infolist
    window = ViewerWindow(config_manager=make_config(tmp_path))

    def counted_stat(path: Path, *args, **kwargs):
        if str(path).startswith(str(tmp_path)):
            stat_threads.append(threading.get_ident())
        return original_stat(path, *args, **kwargs)

    def counted_scandir(path):
        if Path(path) == folder:
            listing_threads.append(threading.get_ident())
        return original_scandir(path)

    def counted_infolist(source: zipfile.ZipFile):
        if Path(source.filename) == archive:
            listing_threads.append(threading.get_ident())
        return original_infolist(source)

    monkeypatch.setattr(Path, "stat", counted_stat)
    monkeypatch.setattr(os, "scandir", counted_scandir)
    monkeypatch.setattr(zipfile.ZipFile, "infolist", counted_infolist)

    assert window.open_path(target)
    assert window.book_session.current_path is None
    finish_open(window, qapp)

    assert window.book_session.current_path == target
    assert window.model.image_ids == (
        [str(first), str(second)]
        if source_kind == "folder"
        else ["1.jpg", "2.jpg"]
    )
    assert stat_threads
    assert listing_threads
    assert all(thread_id != gui_thread for thread_id in stat_threads)
    assert all(thread_id != gui_thread for thread_id in listing_threads)
    assert len(listing_threads) == 1
    window.close()
    qapp.processEvents()


def test_open_returns_while_source_preparation_is_blocked_and_applies_on_gui(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    preparation_started = threading.Event()
    release_preparation = threading.Event()
    factory_threads: list[int] = []
    apply_threads: list[int] = []

    class PreparedSource(ImageSource):
        def __init__(self) -> None:
            super().__init__(tmp_path / "book")
            self.list_calls = 0

        def list_images(self) -> list[str]:
            self.list_calls += 1
            return ["page.jpg"]

        def open_image(self, _image_id: str) -> Image.Image:
            return Image.new("RGB", (8, 12), "white")

        def display_path(self, image_id: str) -> str:
            return image_id

    source = PreparedSource()

    def source_factory(
        _path: Path,
        **_kwargs: object,
    ):
        factory_threads.append(threading.get_ident())
        preparation_started.set()
        assert release_preparation.wait(2)
        return source, None

    session = BookSession(source_factory=source_factory)
    original_apply = session.model.set_prepared_source

    def record_apply(*args, **kwargs):
        apply_threads.append(threading.get_ident())
        return original_apply(*args, **kwargs)

    session.model.set_prepared_source = record_apply  # type: ignore[method-assign]
    window = ViewerWindow(
        config_manager=make_config(tmp_path),
        book_session=session,
    )
    gui_thread = threading.get_ident()

    assert window.open_path(tmp_path / "book")
    assert preparation_started.wait(1)
    assert session.current_path is None

    release_preparation.set()
    finish_open(window, qapp)

    assert len(factory_threads) == 1
    assert factory_threads[0] != gui_thread
    assert apply_threads == [gui_thread]
    assert source.list_calls >= 1
    assert session.current_path == tmp_path / "book"
    window.close()
    qapp.processEvents()


def test_close_during_source_preparation_discards_and_closes_result(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    preparation_started = threading.Event()
    release_preparation = threading.Event()

    class ClosingSource(ImageSource):
        def __init__(self) -> None:
            super().__init__(tmp_path / "book")
            self.closed = False

        def list_images(self) -> list[str]:
            return ["page.jpg"]

        def open_image(self, _image_id: str) -> Image.Image:
            return Image.new("RGB", (8, 12), "white")

        def display_path(self, image_id: str) -> str:
            return image_id

        def close(self) -> None:
            self.closed = True

    source = ClosingSource()

    def source_factory(
        _path: Path,
        **_kwargs: object,
    ):
        preparation_started.set()
        assert release_preparation.wait(2)
        return source, None

    session = BookSession(source_factory=source_factory)
    window = ViewerWindow(
        config_manager=make_config(tmp_path),
        book_session=session,
    )
    opened = []
    session.async_opened.connect(opened.append)

    assert window.open_path(tmp_path / "book")
    assert preparation_started.wait(1)
    window.close()
    window.close()
    release_preparation.set()
    assert session.wait_for_async(2000)
    qapp.processEvents()

    assert source.closed
    assert opened == []
    assert session.source is None
    assert session.current_path is None
    assert session._open_workers == {}


def test_closing_stale_window_does_not_roll_back_shared_setting(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    controller = ApplicationController(qapp, config_manager=config)
    first = controller.create_viewer_window()
    stale = controller.create_viewer_window()
    assert stale.view_mode == "spread"

    first.set_view_mode("single")
    controller.close_viewer_window(stale)

    assert controller.config.get("view_mode") == "single"
    assert ConfigManager(config.path).load()["view_mode"] == "single"
    close_remaining = controller.viewer_windows
    for window in close_remaining:
        controller.close_viewer_window(window)
    qapp.processEvents()
    controller.shutdown()


def test_short_offscreen_show_and_close(tmp_path: Path, qapp: QApplication) -> None:
    window = ViewerWindow(config_manager=make_config(tmp_path))

    window.show_initial()
    qapp.processEvents()
    assert window.isVisible()

    window.close()
    qapp.processEvents()


def test_dispatcher_routes_commands_and_rejects_unknown(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    window = ViewerWindow(config_manager=make_config(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(window, "next_page", lambda: calls.append("next"))
    monkeypatch.setattr(window, "open_previous_book", lambda: calls.append("book"))
    monkeypatch.setattr(window, "toggle_view_mode", lambda: calls.append("spread"))

    assert window.dispatch_command(commands.NEXT_PAGE)
    assert window.dispatch_command(commands.PREVIOUS_BOOK)
    assert window.dispatch_command(commands.TOGGLE_SPREAD)
    assert not window.dispatch_command("unknown")
    assert calls == ["next", "book", "spread"]
    window.close()
    qapp.processEvents()


def test_extra_buttons_use_book_commands_once(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    directions: list[int] = []

    def adjacent_handler(_window: object, direction: int) -> str:
        directions.append(direction)
        return "opened"

    window = ViewerWindow(
        config_manager=make_config(tmp_path),
        adjacent_book_handler=adjacent_handler,
    )

    window.viewer.extraMouseButtonPressed.emit("back")
    window.viewer.extraMouseButtonPressed.emit("forward")

    assert directions == [-1, 1]
    window.close()
    qapp.processEvents()


@pytest.mark.parametrize(
    ("direction", "result", "expected"),
    [
        (-1, "boundary", "前の書庫はありません"),
        (1, "boundary", "次の書庫はありません"),
        (1, "unavailable", "移動できる書庫がありません"),
    ],
)
def test_adjacent_book_failure_uses_non_modal_status_notification(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
    direction: int,
    result: str,
    expected: str,
) -> None:
    modal_calls: list[bool] = []
    monkeypatch.setattr(
        "app.viewer_window.QMessageBox.information",
        lambda *_args, **_kwargs: modal_calls.append(True),
    )
    window = ViewerWindow(
        config_manager=make_config(tmp_path),
        adjacent_book_handler=lambda _window, _direction: result,
    )
    messages: list[str] = []
    window.status.messageChanged.connect(messages.append)

    window._open_adjacent_book(direction)

    assert modal_calls == []
    assert messages == [expected]
    assert window.status.currentMessage() == expected
    window.close()
    qapp.processEvents()


def test_successful_adjacent_book_move_does_not_show_status_notification(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window = ViewerWindow(
        config_manager=make_config(tmp_path),
        adjacent_book_handler=lambda _window, _direction: "opened",
    )
    messages: list[str] = []
    window.status.messageChanged.connect(messages.append)
    initial_message = window.status.currentMessage()

    window.open_next_book()

    assert messages == []
    assert window.status.currentMessage() == initial_message
    window.close()
    qapp.processEvents()


def test_adjacent_book_notification_is_limited_to_target_viewer(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    target = ViewerWindow(
        config_manager=config,
        adjacent_book_handler=lambda _window, _direction: "unavailable",
    )
    other = ViewerWindow(config_manager=config)
    other_initial_message = other.status.currentMessage()

    target.open_previous_book()

    assert target.status.currentMessage() == "移動できる書庫がありません"
    assert other.status.currentMessage() == other_initial_message
    target.close()
    other.close()
    qapp.processEvents()


def test_default_down_gesture_closes_only_target_viewer(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    target = ViewerWindow(config_manager=config)
    other = ViewerWindow(config_manager=config)
    closed: list[object] = []
    target.closing.connect(closed.append)

    target.viewer.gestureRecognized.emit("D")

    assert closed == [target]
    assert not other._shutdown_prepared
    other.close()
    qapp.processEvents()


def test_up_gesture_toggles_fullscreen_once_and_unassigned_does_nothing(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    window = ViewerWindow(config_manager=make_config(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(
        window,
        "toggle_fullscreen",
        lambda: calls.append("fullscreen"),
    )

    window.viewer.gestureRecognized.emit("U")
    window.viewer.gestureRecognized.emit("L")

    assert calls == ["fullscreen"]
    window.close()
    qapp.processEvents()


def test_mouse_settings_apply_to_existing_viewer_immediately(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = make_config(tmp_path)
    window = ViewerWindow(config_manager=config)

    config.apply(
        {
            "mouse_gestures_enabled": False,
            "mouse_gesture_show_trail": False,
            "mouse_gesture_min_distance": 72,
            "mouse_gesture_bindings": {"U": "next_page"},
            "mouse_back_button_action": "",
            "mouse_forward_button_action": "first_page",
        }
    )

    assert not window.viewer.mouse_gestures_enabled
    assert not window.viewer.mouse_gesture_show_trail
    assert window.viewer.mouse_gesture_min_distance == 72
    assert window.mouse_gesture_bindings == {"U": "next_page"}
    assert window.mouse_back_button_action == ""
    assert window.mouse_forward_button_action == "first_page"
    window.close()
    qapp.processEvents()


def test_zip_miss_keeps_previous_frame_and_rapid_navigation_applies_latest(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = ControlledZipSource(tmp_path / "controlled.zip")
    config = make_config(tmp_path)
    config.apply(
        {
            "view_mode": "single",
            "viewer_prefetch_preset": "custom",
            "viewer_prefetch_image_forward_units": 0,
            "viewer_prefetch_image_backward_units": 0,
        },
        save=False,
    )
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
    )
    window = ViewerWindow(config_manager=config, book_session=session)
    applied: list[tuple[str, ...]] = []
    cleared: list[None] = []
    presentation_commits: list[
        tuple[int | None, int, str, int | None, tuple[str, ...]]
    ] = []
    try:
        window.resize(640, 480)
        window.show()
        opened = session.open_book(source.source_path)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        assert session.image_cache.wait_for_done(2000)
        qapp.processEvents()
        window.set_page_list_visible(True)
        qapp.processEvents()
        QTest.qWait(window.viewer._resize_render_timer.interval() + 20)
        qapp.processEvents()
        assert window.viewer.wait_for_rendering()
        qapp.processEvents()
        window.viewer.render(QPixmap(window.viewer.size()))
        previous_ids = tuple(
            image.image_id for image in window.viewer._images
        )
        previous_pixmap_keys = tuple(
            pixmap.cacheKey()
            for _rect, pixmap in window.viewer._last_draw_layout
        )
        assert previous_ids == ("page-0.jpg",)
        assert previous_pixmap_keys

        original_set_pages = window.viewer.set_pages
        original_clear = window.viewer.clear

        def record_set_pages(spread, pages, **kwargs):
            applied.append(tuple(image.image_id for image in pages))
            original_set_pages(spread, pages, **kwargs)

        def record_clear():
            cleared.append(None)
            original_clear()

        window.viewer.set_pages = record_set_pages  # type: ignore[method-assign]
        window.viewer.clear = record_clear  # type: ignore[method-assign]

        def record_presentation_commit(_commit) -> None:
            presentation_commits.append(
                (
                    window.presentation_state.displayed_page,
                    window.slider.value(),
                    window.status.currentMessage(),
                    current_page_list_page(window),
                    tuple(image.image_id for image in window.viewer._images),
                )
            )

        window.presentationCommitted.connect(record_presentation_commit)

        first_started, first_release = source.block("page-1.jpg")
        window.next_page()
        QTest.qWait(window._decode_demand_timer.interval() + 50)
        assert first_started.wait(2)
        qapp.processEvents()
        window.viewer.render(QPixmap(window.viewer.size()))

        assert applied == []
        assert cleared == []
        assert tuple(
            image.image_id for image in window.viewer._images
        ) == previous_ids
        assert tuple(
            pixmap.cacheKey()
            for _rect, pixmap in window.viewer._last_draw_layout
        ) == previous_pixmap_keys
        assert not any(image.loading for image in window.viewer._images)
        assert window.presentation_state.requested_page == 1
        assert window.presentation_state.displayed_page == 0
        assert window.slider.value() == 0
        assert "1 / 7" in window.status.currentMessage()
        assert current_page_list_page(window) == 0

        first_release.set()
        assert session.image_cache.wait_for_done(2000)
        qapp.processEvents()
        QTest.qWait(window._display_demand_timer.interval() + 5)
        qapp.processEvents()
        assert window.viewer.wait_for_rendering()
        qapp.processEvents()
        assert applied == [("page-1.jpg",)]
        assert tuple(
            image.image_id for image in window.viewer._images
        ) == ("page-1.jpg",)
        assert presentation_commits[-1][0:2] == (1, 1)
        assert "2 / 7" in presentation_commits[-1][2]
        assert presentation_commits[-1][3:] == (1, ("page-1.jpg",))
        assert [
            entry.values.page_index
            for entry in window.presentation_state.back_history
        ] == [0]
        assert window.presentation_state.progress_page == 1

        applied.clear()
        reads_before_rapid = list(source.read_calls)
        window.next_page()
        for _index in range(3):
            window.next_page()
        assert source.read_calls == reads_before_rapid
        QTest.qWait(window._decode_demand_timer.interval() + 50)
        qapp.processEvents()

        assert window.model.focused_index == 5
        assert window.presentation_state.requested_page == 5
        assert window.presentation_state.displayed_page == 1
        assert window.slider.value() == 1
        assert "2 / 7" in window.status.currentMessage()
        assert applied in ([], [("page-5.jpg",)])
        intermediate_ids = tuple(
            image.image_id for image in window.viewer._images
        )
        # set_pages records acceptance of the new source image before its
        # asynchronous render necessarily commits. Either the old complete
        # frame or the final target is valid at this intermediate boundary;
        # crossed pages are never valid.
        assert intermediate_ids in {
            ("page-1.jpg",),
            ("page-5.jpg",),
        }
        if not applied:
            assert intermediate_ids == ("page-1.jpg",)

        assert session.image_cache.wait_for_done(3000)
        qapp.processEvents()
        QTest.qWait(window._display_demand_timer.interval() + 5)
        qapp.processEvents()
        assert window.viewer.wait_for_rendering()
        qapp.processEvents()
        assert applied == [("page-5.jpg",)]
        assert tuple(
            image.image_id for image in window.viewer._images
        ) == ("page-5.jpg",)
        assert presentation_commits[-1][0:2] == (5, 5)
        assert "6 / 7" in presentation_commits[-1][2]
        assert presentation_commits[-1][3:] == (5, ("page-5.jpg",))
        assert [
            entry.values.page_index
            for entry in window.presentation_state.back_history
        ] == [0, 1]
        assert window.presentation_state.progress_page == 5
        assert not any(
            image_id in {"page-2.jpg", "page-3.jpg", "page-4.jpg"}
            for unit in applied
            for image_id in unit
        )
        assert not any(
            image_id in {"page-2.jpg", "page-3.jpg", "page-4.jpg"}
            for image_id in source.read_calls[len(reads_before_rapid) :]
        )

        applied.clear()
        window._on_cache_page_loaded(
            CachedImage(
                page_index=5,
                image_id="page-5.jpg",
                qimage=None,
                original_size=None,
                error="cancelled stale result",
                generation=session.image_cache.generation - 1,
            )
        )
        assert applied == []
        assert tuple(
            image.image_id for image in window.viewer._images
        ) == ("page-5.jpg",)

        source.failures.add("page-6.jpg")
        window.next_page()
        assert tuple(
            image.image_id for image in window.viewer._images
        ) == ("page-5.jpg",)
        assert applied == []
        QTest.qWait(window._decode_demand_timer.interval() + 50)
        assert session.image_cache.wait_for_done(2000)
        qapp.processEvents()
        QTest.qWait(window._display_demand_timer.interval() + 5)
        qapp.processEvents()
        assert applied == [("page-6.jpg",)]
        assert len(window.viewer._images) == 1
        assert window.viewer._images[0].error
        assert window.viewer._images[0].pixmap is None
        assert window.presentation_state.displayed_page == 6
        assert window.presentation_state.frame_failure is not None
        assert window.slider.value() == 6
        assert "7 / 7" in window.status.currentMessage()
        assert presentation_commits[-1][0:2] == (6, 6)
        assert window.presentation_state.progress_page == 6
        assert cleared == []
        window.viewer.render(QPixmap(window.viewer.size()))
        qapp.processEvents()
        assert window._raster_prefetch_after_paint is None
        assert not window._raster_interactive_lane_held

        applied.clear()
        window.prepare_shutdown(wait_msecs=3000)
        window._on_cache_page_loaded(
            CachedImage(
                page_index=6,
                image_id="page-6.jpg",
                qimage=None,
                original_size=None,
                error="late result after viewer shutdown",
                generation=session.image_cache.generation,
            )
        )
        assert applied == []
    finally:
        source.release_all()
        session.image_cache.wait_for_done(3000)
        window.close()
        qapp.processEvents()
