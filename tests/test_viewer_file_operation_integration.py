from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication, QMessageBox

from app.application_controller import ApplicationController
from app.config_manager import ConfigManager


def write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (8, 12), "white") as image:
        image.save(path)


def make_controller(
    tmp_path: Path,
    qapp: QApplication,
) -> ApplicationController:
    return ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
    )


def close_controller(
    controller: ApplicationController,
    qapp: QApplication,
) -> None:
    for viewer in tuple(controller.viewer_windows):
        viewer.close()
    browser = controller.get_browser_window()
    if browser is not None:
        browser.close()
    qapp.processEvents()
    controller.shutdown()


def select_item(browser, path: Path) -> None:
    row = browser.item_model.row_for_path(path)
    assert row >= 0, (
        browser.current_path,
        tuple((item.display_name, item.path) for item in browser.items),
    )
    index = browser.item_model.index(row, 0)
    browser.list_view.selectionModel().select(
        index,
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    browser.list_view.setCurrentIndex(index)


def test_controller_detects_only_viewers_affected_by_path(
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

    assert controller.viewers_using_paths((str(first_image.parent),)) == (first,)
    assert controller.viewers_using_paths((str(first_image),)) == (first,)
    assert controller.viewers_using_paths((str(tmp_path),)) == (first, second)
    assert controller.viewers_using_paths((str(tmp_path / "unrelated"),)) == ()
    close_controller(controller, qapp)


def test_mutation_cancel_keeps_viewer_and_source_open(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    shelf = tmp_path / "shelf"
    image = shelf / "book" / "1.jpg"
    write_image(image)
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    assert browser.navigate_to(shelf, force_reload=True)
    assert browser.wait_for_scan()
    qapp.processEvents()
    viewer = controller.open_path(image, open_in_new_window=True)
    monkeypatch.setattr(
        browser,
        "selected_file_operation_paths",
        lambda: (str(image.parent.absolute()),),
    )
    monkeypatch.setattr(
        browser,
        "_prompt_for_filename",
        lambda *_args: "renamed",
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )

    assert not browser.rename_selected_item()

    assert controller.viewer_windows == (viewer,)
    assert viewer.book_session.is_open
    assert image.parent.exists()
    close_controller(controller, qapp)


def test_continue_closes_only_affected_viewer_then_starts_mutation(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    shelf = tmp_path / "shelf"
    first_image = shelf / "first" / "1.jpg"
    second_image = shelf / "second" / "1.jpg"
    write_image(first_image)
    write_image(second_image)
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    first = controller.open_path(first_image, open_in_new_window=True)
    second = controller.open_path(second_image, open_in_new_window=True)
    monkeypatch.setattr(
        browser,
        "selected_file_operation_paths",
        lambda: (str(first_image.parent.absolute()),),
    )
    monkeypatch.setattr(
        browser,
        "_prompt_for_filename",
        lambda *_args: "renamed",
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    assert browser.rename_selected_item()
    assert controller.file_operation_coordinator.wait_for_done(3000)
    for _ in range(5):
        qapp.processEvents()

    assert first not in controller.viewer_windows
    assert controller.viewer_windows == (second,)
    assert second.book_session.is_open
    assert controller.get_browser_window() is browser
    assert (shelf / "renamed").is_dir()
    close_controller(controller, qapp)


def test_copy_does_not_request_viewer_close(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    shelf = tmp_path / "shelf"
    destination = tmp_path / "destination"
    image = shelf / "book" / "1.jpg"
    write_image(image)
    destination.mkdir()
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    viewer = controller.open_path(image, open_in_new_window=True)
    monkeypatch.setattr(
        browser,
        "selected_file_operation_paths",
        lambda: (str(image.parent.absolute()),),
    )
    confirmations = []
    monkeypatch.setattr(
        browser,
        "_confirm_and_close_affected_viewers",
        lambda _paths: confirmations.append(True) or True,
    )

    assert browser.copy_selected_to(destination)
    assert controller.file_operation_coordinator.wait_for_done(3000)
    qapp.processEvents()

    assert confirmations == []
    assert viewer in controller.viewer_windows
    assert (destination / "book" / "1.jpg").exists()
    close_controller(controller, qapp)
