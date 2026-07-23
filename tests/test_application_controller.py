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


def close_controller(controller: ApplicationController, qapp: QApplication) -> None:
    controller.shutdown()
    for window in controller.viewer_windows:
        window.close()
    qapp.processEvents()


def test_start_creates_window_and_shares_config(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    controller = ApplicationController(qapp, config_manager=config)

    window = controller.start()

    assert controller.main_window is window
    assert window.config is controller.config
    assert window.settings is controller.settings
    close_controller(controller, qapp)


def test_start_passes_initial_path_to_open_processing(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image = tmp_path / "初期画像.jpg"
    write_image(image)
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
    )

    window = controller.start(str(image))

    assert window.book_session.current_path == image
    assert str(image) in window.model.image_ids
    close_controller(controller, qapp)


def test_bring_to_front_does_not_enable_always_on_top(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
    )
    widget = QWidget()
    always_on_top_before = bool(widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    controller.bring_window_to_front_once(widget)
    qapp.processEvents()

    assert bool(widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) == always_on_top_before
    widget.close()
    controller.shutdown()


def test_shutdown_is_idempotent(tmp_path: Path, qapp: QApplication) -> None:
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
    )
    controller.start()

    controller.shutdown()
    controller.shutdown()

    for window in controller.viewer_windows:
        window.close()
    qapp.processEvents()
