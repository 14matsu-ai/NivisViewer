from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import QApplication

from app.application_controller import ApplicationController
from app.book_session import BookSession
from app.config_manager import ConfigManager
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
