from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from app.application_controller import ApplicationController
from app.config_manager import ConfigManager


def write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (8, 12), "white") as image:
        image.save(path)


def make_controller(tmp_path: Path, qapp: QApplication) -> ApplicationController:
    return ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
    )


def close_controller(controller: ApplicationController, qapp: QApplication) -> None:
    controller.shutdown()
    for window in controller.viewer_windows:
        window.close()
    qapp.processEvents()


def test_start_creates_viewer_and_shares_config(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = make_controller(tmp_path, qapp)

    window = controller.start()

    assert controller.get_active_viewer() is window
    assert window in controller.viewer_windows
    assert window.config is controller.config
    assert window.settings is controller.settings
    close_controller(controller, qapp)


def test_start_passes_initial_path_to_open_processing(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image = tmp_path / "初期画像.jpg"
    write_image(image)
    controller = make_controller(tmp_path, qapp)

    window = controller.start(str(image))

    assert window.book_session.current_path == image
    assert str(image) in window.model.image_ids
    close_controller(controller, qapp)


def test_create_and_close_viewer_updates_registration(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = make_controller(tmp_path, qapp)
    window = controller.create_viewer_window()

    assert controller.viewer_windows == (window,)

    controller.close_viewer_window(window)

    assert controller.viewer_windows == ()
    assert controller.get_active_viewer() is None
    qapp.processEvents()


def test_reuse_active_reuses_active_viewer(tmp_path: Path, qapp: QApplication) -> None:
    image = tmp_path / "book.jpg"
    write_image(image)
    controller = make_controller(tmp_path, qapp)
    controller.settings["open_viewer_behavior"] = "reuse_active"
    existing = controller.create_viewer_window()

    opened = controller.open_path(image)

    assert opened is existing
    assert controller.viewer_windows == (existing,)
    close_controller(controller, qapp)


def test_always_new_creates_a_viewer_for_each_open(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_image = tmp_path / "first" / "1.jpg"
    second_image = tmp_path / "second" / "1.jpg"
    write_image(first_image)
    write_image(second_image)
    controller = make_controller(tmp_path, qapp)
    controller.settings["open_viewer_behavior"] = "always_new"

    first = controller.open_path(first_image)
    second = controller.open_path(second_image)

    assert first is not second
    assert controller.viewer_windows == (first, second)
    close_controller(controller, qapp)


def test_viewer_file_request_reuses_source_window_even_when_always_new(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image = tmp_path / "selected.jpg"
    write_image(image)
    controller = make_controller(tmp_path, qapp)
    controller.settings["open_viewer_behavior"] = "always_new"
    source_window = controller.create_viewer_window()

    source_window._request_open_path(image)

    assert controller.viewer_windows == (source_window,)
    assert source_window.book_session.current_path == image
    close_controller(controller, qapp)


def test_reuse_or_create_creates_then_reuses(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_image = tmp_path / "first.jpg"
    second_image = tmp_path / "second.jpg"
    write_image(first_image)
    write_image(second_image)
    controller = make_controller(tmp_path, qapp)
    controller.settings["open_viewer_behavior"] = "reuse_or_create"

    first = controller.open_path(first_image)
    second = controller.open_path(second_image)

    assert second is first
    assert controller.viewer_windows == (first,)
    assert first.book_session.current_path == second_image
    close_controller(controller, qapp)


def test_invalid_open_behavior_falls_back_to_reuse_or_create(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_image = tmp_path / "first.jpg"
    second_image = tmp_path / "second.jpg"
    write_image(first_image)
    write_image(second_image)
    controller = make_controller(tmp_path, qapp)
    controller.settings["open_viewer_behavior"] = "invalid-value"

    first = controller.open_path(first_image)
    second = controller.open_path(second_image)

    assert second is first
    assert len(controller.viewer_windows) == 1
    close_controller(controller, qapp)


def test_two_viewers_hold_independent_books_and_sessions(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_image = tmp_path / "first" / "1.jpg"
    second_image = tmp_path / "second" / "1.jpg"
    write_image(first_image)
    write_image(second_image)
    controller = make_controller(tmp_path, qapp)

    first = controller.open_path(first_image, open_in_new_window=True)
    second = controller.open_path(second_image, open_in_new_window=True)

    assert first.book_session is not second.book_session
    assert first.book_session.current_path == first_image
    assert second.book_session.current_path == second_image

    controller.close_viewer_window(first)

    assert controller.viewer_windows == (second,)
    assert second.book_session.current_path == second_image
    close_controller(controller, qapp)


def test_last_viewer_requests_application_exit_once(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = make_controller(tmp_path, qapp)
    exit_requests: list[bool] = []
    controller.exit_requested.connect(lambda: exit_requests.append(True))
    window = controller.create_viewer_window()

    controller.close_viewer_window(window)
    controller.close_viewer_window(window)

    assert exit_requests == [True]
    assert controller.viewer_windows == ()
    qapp.processEvents()


def test_bring_to_front_does_not_enable_always_on_top(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = make_controller(tmp_path, qapp)
    widget = QWidget()
    always_on_top_before = bool(widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    controller.bring_window_to_front_once(widget)
    qapp.processEvents()

    assert bool(widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) == always_on_top_before
    widget.close()
    controller.shutdown()


def test_shutdown_is_idempotent(tmp_path: Path, qapp: QApplication) -> None:
    controller = make_controller(tmp_path, qapp)
    controller.start()

    controller.shutdown()
    controller.shutdown()

    for window in controller.viewer_windows:
        window.close()
    qapp.processEvents()
