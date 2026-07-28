from __future__ import annotations

import threading
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from app.application_controller import ApplicationController
from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.image_source import ImageSource
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
    assert window.slider.isEnabled()
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
    original_iterdir = Path.iterdir
    original_infolist = zipfile.ZipFile.infolist
    window = ViewerWindow(config_manager=make_config(tmp_path))

    def counted_stat(path: Path, *args, **kwargs):
        if str(path).startswith(str(tmp_path)):
            stat_threads.append(threading.get_ident())
        return original_stat(path, *args, **kwargs)

    def counted_iterdir(path: Path):
        if path == folder:
            listing_threads.append(threading.get_ident())
        return original_iterdir(path)

    def counted_infolist(source: zipfile.ZipFile):
        if Path(source.filename) == archive:
            listing_threads.append(threading.get_ident())
        return original_infolist(source)

    monkeypatch.setattr(Path, "stat", counted_stat)
    monkeypatch.setattr(Path, "iterdir", counted_iterdir)
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
