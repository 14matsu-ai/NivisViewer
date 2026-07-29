from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from app.application_controller import ApplicationController
from app.config_manager import ConfigManager
from app.metadata_store import MetadataStore


def write_book(folder: Path, pages: int = 4) -> list[Path]:
    result: list[Path] = []
    folder.mkdir(parents=True, exist_ok=True)
    for number in range(1, pages + 1):
        path = folder / f"{number}.jpg"
        with Image.new("RGB", (8, 12), "white") as image:
            image.save(path)
        result.append(path)
    return result


def make_controller(tmp_path: Path, qapp: QApplication) -> ApplicationController:
    config = ConfigManager(tmp_path / "config.json")
    controller = ApplicationController(qapp, config_manager=config)
    controller.config.apply({"view_mode": "single"})
    return controller


def close_controller(
    controller: ApplicationController,
    qapp: QApplication,
) -> None:
    for window in tuple(controller.viewer_windows):
        window.close()
    browser = controller.get_browser_window()
    if browser is not None:
        browser.close()
    qapp.processEvents()
    controller.shutdown()


def finish_viewer_open(qapp: QApplication, window) -> None:
    assert window.book_session.wait_for_async(2000)
    qapp.processEvents()


def persisted_page(database_path: Path, book_path: Path) -> int:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT history.last_page_index
              FROM reading_history AS history
              JOIN library_items AS item
                ON item.id = history.library_item_id
             WHERE item.normalized_path = ?
            """,
            (MetadataStore.normalize_path(book_path),),
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_controller_shares_one_metadata_store_with_all_windows(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    first = controller.create_viewer_window()
    second = controller.create_viewer_window()

    assert browser.metadata_store is controller.metadata_store
    assert first.metadata_store is controller.metadata_store
    assert second.metadata_store is controller.metadata_store
    close_controller(controller, qapp)


def test_only_successful_open_is_added_to_history(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    book = tmp_path / "book"
    write_book(book, 2)
    controller = make_controller(tmp_path, qapp)
    window = controller.open_path(book)
    finish_viewer_open(qapp, window)
    before = controller.metadata_store.list_history()
    monkeypatch.setattr(
        "app.viewer_window.QMessageBox.critical",
        lambda *_args, **_kwargs: None,
    )

    assert window.open_path(tmp_path / "missing.zip")
    finish_viewer_open(qapp, window)
    assert controller.metadata_store.list_history() == before
    close_controller(controller, qapp)


def test_progress_is_batched_then_flushed_when_switching_books(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_book = tmp_path / "first"
    second_book = tmp_path / "second"
    write_book(first_book)
    write_book(second_book)
    controller = make_controller(tmp_path, qapp)
    window = controller.open_path(first_book)
    finish_viewer_open(qapp, window)

    window.next_one_page()

    assert window.model.current_index == 1
    assert persisted_page(controller.config.metadata_database_path, first_book) == 0

    assert window.open_path(second_book)
    assert persisted_page(controller.config.metadata_database_path, first_book) == 1
    close_controller(controller, qapp)


def test_viewer_close_flushes_latest_progress(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    book = tmp_path / "close-flush"
    write_book(book)
    controller = make_controller(tmp_path, qapp)
    controller.create_browser_window()
    window = controller.open_path(book)
    finish_viewer_open(qapp, window)
    window.next_one_page()

    window.close()
    qapp.processEvents()

    assert persisted_page(controller.config.metadata_database_path, book) == 1
    close_controller(controller, qapp)


def test_normal_reopen_restores_progress_and_clamps_to_page_count(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    book = tmp_path / "restore"
    write_book(book, 4)
    controller = make_controller(tmp_path, qapp)
    window = controller.open_path(book)
    finish_viewer_open(qapp, window)
    window.next_one_page()
    window.next_one_page()
    window.prepare_shutdown()

    reopened = controller.create_viewer_window()
    assert reopened.open_path(book)
    finish_viewer_open(qapp, reopened)
    assert reopened.model.current_index == 2

    controller.metadata_store.update_reading_progress(
        str(book),
        page_index=999,
        total_pages=None,
    )
    controller.metadata_store.flush()
    for page in (book / "3.jpg", book / "4.jpg"):
        page.unlink()
    clamped = controller.create_viewer_window()
    assert clamped.open_path(book)
    finish_viewer_open(qapp, clamped)
    assert clamped.model.current_index == 1
    close_controller(controller, qapp)


def test_explicit_image_selection_wins_over_saved_folder_progress(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    book = tmp_path / "selected"
    pages = write_book(book, 4)
    controller = make_controller(tmp_path, qapp)
    controller.metadata_store.record_book_opened(
        str(book),
        item_type="folder",
        start_page_index=3,
        total_pages=4,
    )

    window = controller.open_path(pages[1])
    finish_viewer_open(qapp, window)

    assert window.model.current_index == 1
    history = controller.metadata_store.list_history()
    assert len(history) == 1
    assert Path(history[0].path) == book.absolute()
    close_controller(controller, qapp)


def test_history_tab_opens_viewer_at_saved_progress(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    book = tmp_path / "history-open"
    write_book(book, 4)
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    controller.metadata_store.record_book_opened(
        str(book),
        item_type="folder",
        start_page_index=2,
        total_pages=4,
    )
    qapp.processEvents()

    browser.open_history(browser.history_model.index(0, 0))
    viewer = controller.get_active_viewer()

    assert viewer is not None
    finish_viewer_open(qapp, viewer)
    assert viewer.model.current_index == 2
    close_controller(controller, qapp)


def test_controller_open_notifies_browser_history_model(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    book = tmp_path / "controller-notification"
    write_book(book, 2)
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()

    assert browser.history_model.rowCount() == 0
    viewer = controller.open_path(book)
    finish_viewer_open(qapp, viewer)

    assert browser.history_model.rowCount() == 1
    assert Path(browser.history_model.entries[0].path) == book.absolute()
    close_controller(controller, qapp)


def test_restore_last_position_can_be_disabled(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    book = tmp_path / "restore-disabled"
    write_book(book, 4)
    controller = make_controller(tmp_path, qapp)
    controller.metadata_store.record_book_opened(
        str(book),
        item_type="folder",
        start_page_index=3,
        total_pages=4,
    )
    controller.config.apply({"restore_last_reading_position": False})

    window = controller.open_path(book)
    finish_viewer_open(qapp, window)

    assert window.model.current_index == 0
    close_controller(controller, qapp)


def test_multiple_viewers_update_shared_database_without_corruption(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    book = tmp_path / "shared"
    write_book(book, 4)
    controller = make_controller(tmp_path, qapp)
    first = controller.open_path(book, open_in_new_window=True)
    second = controller.open_path(book, open_in_new_window=True)
    finish_viewer_open(qapp, first)
    finish_viewer_open(qapp, second)

    first.next_one_page()
    second.next_one_page()
    second.next_one_page()
    first.close()
    second.close()
    qapp.processEvents()

    reopened = MetadataStore(controller.config.metadata_database_path)
    history = reopened.list_history()
    assert len(history) == 1
    assert history[0].open_count == 2
    assert persisted_page(controller.config.metadata_database_path, book) == 2
    reopened.close()
    close_controller(controller, qapp)


def test_controller_shutdown_flushes_and_closes_store_idempotently(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    book = tmp_path / "shutdown"
    write_book(book, 3)
    controller = make_controller(tmp_path, qapp)
    window = controller.open_path(book)
    finish_viewer_open(qapp, window)
    window.next_one_page()

    controller.shutdown()
    controller.shutdown()

    assert persisted_page(controller.config.metadata_database_path, book) == 1
    assert controller.metadata_store.enabled is False
    for viewer in tuple(controller.viewer_windows):
        viewer.close()
    qapp.processEvents()


@pytest.mark.parametrize(
    "operation",
    ("book_switch", "viewer_close", "browser_close", "application_shutdown"),
)
def test_lifecycle_metadata_flush_does_not_probe_source(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
    operation: str,
) -> None:
    first_book = tmp_path / "first"
    second_book = tmp_path / "second"
    write_book(first_book, 3)
    write_book(second_book, 3)
    controller = make_controller(tmp_path, qapp)
    controller.config.set("last_browser_path", str(tmp_path))
    browser = (
        controller.create_browser_window()
        if operation == "browser_close"
        else None
    )
    if browser is not None:
        assert browser.wait_for_scan()
    window = controller.open_path(first_book)
    finish_viewer_open(qapp, window)
    window.next_one_page()
    source_key = MetadataStore.normalize_path(first_book)
    calls = {"is_dir": 0}
    original_infer = MetadataStore._infer_item_type

    def guarded_infer(path: str) -> str:
        if MetadataStore.normalize_path(path) == source_key:
            calls["is_dir"] += 1
            raise AssertionError("lifecycle flush inferred type from source")
        return original_infer(path)

    monkeypatch.setattr(
        MetadataStore,
        "_infer_item_type",
        staticmethod(guarded_infer),
    )

    if operation == "book_switch":
        assert window.open_path(second_book)
    elif operation == "viewer_close":
        window.close()
        qapp.processEvents()
    elif operation == "browser_close":
        assert browser is not None
        browser._application_close_guard = lambda _window: True
        browser.close()
        qapp.processEvents()
    else:
        controller.shutdown()

    assert calls == {"is_dir": 0}
    expected_before_controller_close = 0 if operation == "browser_close" else 1
    assert (
        persisted_page(controller.config.metadata_database_path, first_book)
        == expected_before_controller_close
    )

    if operation != "application_shutdown":
        close_controller(controller, qapp)
        assert persisted_page(
            controller.config.metadata_database_path,
            first_book,
        ) == 1
        assert calls == {"is_dir": 0}
    else:
        for viewer in tuple(controller.viewer_windows):
            viewer.close()
        qapp.processEvents()


def test_legacy_config_migrates_once_without_deleting_original_data(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    book = tmp_path / "legacy"
    pages = write_book(book, 3)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.data["recent_paths"] = [str(pages[0])]
    config.data["reading_positions"] = {str(book): 2}
    config.data["bookmarks"] = {str(book): [1]}
    config.save()

    first = ApplicationController(qapp, config_manager=ConfigManager(config.path))
    history = first.metadata_store.list_history()

    assert len(history) == 1
    assert Path(history[0].path) == book.absolute()
    assert history[0].page_index == 2
    assert first.config.get("metadata_migration_v1_completed") is True
    assert first.config.get("recent_paths") == [str(pages[0])]
    assert first.config.get("reading_positions") == {str(book): 2}
    assert first.config.get("bookmarks") == {str(book): [1]}
    first.shutdown()

    second = ApplicationController(qapp, config_manager=ConfigManager(config.path))
    second_history = second.metadata_store.list_history()
    assert len(second_history) == 1
    assert second_history[0].open_count == 1
    second.shutdown()


def test_empty_legacy_migration_defers_config_save_until_shutdown(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()

    with patch.object(config, "save") as save:
        controller = ApplicationController(qapp, config_manager=config)

        assert config.get("metadata_migration_v1_completed") is True
        save.assert_not_called()

        controller.shutdown()
        save.assert_called_once()
