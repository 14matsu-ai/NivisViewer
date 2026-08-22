from __future__ import annotations

import os
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.file_operation_coordinator import FileOperationCoordinator
from app.file_operation_service import (
    FileOperationKind,
    FileOperationResult,
    FileOperationService,
)
from app.file_operation_worker import FileOperationExecutor
from app.metadata_store import MetadataStore
from app.windows_recycle_bin import RecycleBinResult


class MovingRecycleBin:
    def __init__(self, trash: Path, *, fail: bool = False) -> None:
        self.trash = trash
        self.fail = fail
        self.paths: list[str] = []
        trash.mkdir()

    def recycle(self, path) -> RecycleBinResult:
        source = Path(path)
        self.paths.append(str(source))
        if self.fail:
            return RecycleBinResult(
                False,
                error_code="shell_error",
                error_message="failed",
            )
        source.rename(self.trash / source.name)
        return RecycleBinResult(True)


class SelectiveMovingRecycleBin(MovingRecycleBin):
    def __init__(self, trash: Path, failed_names: set[str]) -> None:
        super().__init__(trash)
        self.failed_names = failed_names

    def recycle(self, path) -> RecycleBinResult:
        source = Path(path)
        self.paths.append(str(source))
        if source.name in self.failed_names:
            return RecycleBinResult(
                False,
                error_code="shell_error",
                error_message=f"{source.name}を削除できません",
            )
        source.rename(self.trash / source.name)
        return RecycleBinResult(True)


def write_file(path: Path, content: str = "data") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_window(
    tmp_path: Path,
    qapp: QApplication,
    folder: Path,
    *,
    recycle_bin=None,
) -> tuple[BrowserWindow, FileOperationCoordinator]:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    metadata = MetadataStore(tmp_path / "metadata.sqlite3")
    service = FileOperationService(recycle_bin)
    coordinator = FileOperationCoordinator(
        metadata,
        executor=FileOperationExecutor(service),
    )
    window = BrowserWindow(
        config_manager=config,
        metadata_store=metadata,
        file_operation_coordinator=coordinator,
    )
    window.resize(700, 480)
    window.show()
    assert window.wait_for_scan()
    qapp.processEvents()
    return window, coordinator


def close_window(
    window: BrowserWindow,
    coordinator: FileOperationCoordinator,
    qapp: QApplication,
) -> None:
    window.close()
    coordinator.close()
    coordinator.metadata_store.close()
    qapp.processEvents()


def select_paths(window: BrowserWindow, paths: list[Path]) -> None:
    selection = window.list_view.selectionModel()
    selection.clearSelection()
    for position, path in enumerate(paths):
        row = window.item_model.row_for_path(path)
        assert row >= 0
        index = window.item_model.index(row, 0)
        selection.select(index, QItemSelectionModel.SelectionFlag.Select)
        if position == 0:
            window.list_view.setCurrentIndex(index)


def finish_operation(
    window: BrowserWindow,
    coordinator: FileOperationCoordinator,
    qapp: QApplication,
) -> None:
    assert coordinator.wait_for_done(3000)
    for _ in range(5):
        qapp.processEvents()
    if window._pending_scan is not None:
        assert window.wait_for_scan()
    for _ in range(5):
        qapp.processEvents()


def test_f2_rename_refreshes_and_selects_new_path_without_history_or_open(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "books"
    source = folder / "old.cbz"
    write_file(source)
    window, coordinator = make_window(tmp_path, qapp, folder)
    select_paths(window, [source])
    opened = []
    window._open_path_handler = lambda *args: opened.append(args)
    monkeypatch.setattr(
        window,
        "_prompt_for_filename",
        lambda *_args: "新しい.cbz",
    )
    history_size = len(window.navigation_history)
    window.list_view.setFocus()

    QTest.keyClick(window.list_view, Qt.Key.Key_F2)
    finish_operation(window, coordinator, qapp)

    destination = folder / "新しい.cbz"
    assert destination.exists()
    assert not source.exists()
    current = window.item_model.item_at(window.list_view.currentIndex())
    assert current is not None and current.path == destination.absolute()
    assert len(window.navigation_history) == history_size
    assert opened == []
    close_window(window, coordinator, qapp)


@pytest.mark.parametrize("is_directory", [False, True])
def test_properties_rename_file_or_folder_updates_metadata_history_and_selection(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
    is_directory: bool,
) -> None:
    folder = tmp_path / "books"
    folder.mkdir()
    source = folder / ("old folder" if is_directory else "old.cbz")
    if is_directory:
        source.mkdir()
        write_file(source / "child.txt")
        new_name = "new folder"
    else:
        write_file(source)
        new_name = "new.cbz"
    window, coordinator = make_window(tmp_path, qapp, folder)
    select_paths(window, [source])
    window.navigation_history.update_current_view_state(
        selected_path=str(source),
        vertical_scroll=0,
        horizontal_scroll=0,
    )
    metadata_calls: list[tuple[str, str]] = []
    history_calls: list[tuple[str, str]] = []
    relocate_metadata = window.metadata_store.relocate_tree
    relocate_history = window.navigation_history.relocate_tree

    def record_metadata(old_path: str, new_path: str) -> bool:
        metadata_calls.append((old_path, new_path))
        return relocate_metadata(old_path, new_path)

    def record_history(old_path: str, new_path: str) -> bool:
        history_calls.append((old_path, new_path))
        return relocate_history(old_path, new_path)

    monkeypatch.setattr(window.metadata_store, "relocate_tree", record_metadata)
    monkeypatch.setattr(
        window.navigation_history,
        "relocate_tree",
        record_history,
    )

    assert window.show_selected_properties()
    dialog = next(iter(window._properties_dialogs))
    assert dialog.name_edit.text() == source.name
    dialog.name_edit.setText(new_name)
    if is_directory:
        dialog.ok_button.click()
    else:
        dialog.apply_button.click()
    finish_operation(window, coordinator, qapp)

    destination = folder / new_name
    assert destination.exists()
    assert not source.exists()
    assert dialog.isVisible() is not is_directory
    assert dialog.path == destination
    assert metadata_calls == [(str(source.absolute()), str(destination))]
    assert history_calls == [(str(source.absolute()), str(destination))]
    current = window.item_model.item_at(window.list_view.currentIndex())
    assert current is not None and current.path == destination.absolute()
    dialog.reject()
    close_window(window, coordinator, qapp)


def test_properties_rename_rejects_invalid_and_collision_without_closing(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "books"
    source = folder / "source.cbz"
    collision = folder / "collision.cbz"
    write_file(source, "source")
    write_file(collision, "collision")
    window, coordinator = make_window(tmp_path, qapp, folder)
    select_paths(window, [source])
    metadata_calls = []
    history_calls = []
    monkeypatch.setattr(
        window.metadata_store,
        "relocate_tree",
        lambda *args: metadata_calls.append(args),
    )
    monkeypatch.setattr(
        window.navigation_history,
        "relocate_tree",
        lambda *args: history_calls.append(args),
    )

    assert window.show_selected_properties()
    dialog = next(iter(window._properties_dialogs))
    dialog.name_edit.setText("CON.txt")
    dialog.apply_button.click()

    assert dialog.isVisible()
    assert "予約名" in dialog.error_label.text()
    assert not coordinator.busy

    dialog.name_edit.setText(collision.name)
    dialog.apply_button.click()
    finish_operation(window, coordinator, qapp)

    assert dialog.isVisible()
    assert "同じ名前" in dialog.error_label.text()
    assert source.read_text(encoding="utf-8") == "source"
    assert collision.read_text(encoding="utf-8") == "collision"
    assert metadata_calls == []
    assert history_calls == []
    dialog.reject()
    close_window(window, coordinator, qapp)


def test_properties_cancel_and_unchanged_ok_do_not_start_rename(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "books"
    source = folder / "source.cbz"
    write_file(source)
    window, coordinator = make_window(tmp_path, qapp, folder)
    select_paths(window, [source])

    assert window.show_selected_properties()
    first_dialog = next(iter(window._properties_dialogs))
    first_dialog.name_edit.setText("cancelled.cbz")
    first_dialog.cancel_button.click()
    qapp.processEvents()
    assert source.exists()
    assert not coordinator.busy

    assert window.show_selected_properties()
    second_dialog = next(iter(window._properties_dialogs))
    second_dialog.ok_button.click()
    qapp.processEvents()
    assert source.exists()
    assert not coordinator.busy
    close_window(window, coordinator, qapp)


def test_delete_uses_recycle_confirmation_and_refreshes_near_selection(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "books"
    first = folder / "1.cbz"
    second = folder / "2.cbz"
    write_file(first)
    write_file(second)
    recycle = MovingRecycleBin(tmp_path / "trash")
    window, coordinator = make_window(
        tmp_path,
        qapp,
        folder,
        recycle_bin=recycle,
    )
    select_paths(window, [first])
    prompts = []

    def confirm(*args, **kwargs):
        prompts.append((args, kwargs))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    window.list_view.setFocus()

    QTest.keyClick(window.list_view, Qt.Key.Key_Delete)
    finish_operation(window, coordinator, qapp)

    assert len(prompts) == 1
    assert recycle.paths == [str(first.absolute())]
    assert not first.exists()
    current = window.item_model.item_at(window.list_view.currentIndex())
    assert current is not None and current.path == second.absolute()
    close_window(window, coordinator, qapp)


def test_delete_single_folder_uses_recycle_bin(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "books"
    selected_folder = folder / "selected"
    selected_folder.mkdir(parents=True)
    write_file(selected_folder / "child.txt")
    recycle = MovingRecycleBin(tmp_path / "trash")
    window, coordinator = make_window(
        tmp_path,
        qapp,
        folder,
        recycle_bin=recycle,
    )
    select_paths(window, [selected_folder])
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    assert window.move_selected_to_recycle_bin()
    finish_operation(window, coordinator, qapp)

    assert recycle.paths == [str(selected_folder.absolute())]
    assert not selected_folder.exists()
    assert window.item_model.row_for_path(selected_folder) == -1
    close_window(window, coordinator, qapp)


def test_delete_confirmation_cancel_and_viewer_refusal_do_not_call_adapter(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "books"
    source = folder / "source.cbz"
    write_file(source)
    recycle = MovingRecycleBin(tmp_path / "trash")
    window, coordinator = make_window(
        tmp_path,
        qapp,
        folder,
        recycle_bin=recycle,
    )
    select_paths(window, [source])
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )

    assert not window.move_selected_to_recycle_bin()
    assert recycle.paths == []
    assert source.exists()

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        window,
        "_confirm_and_close_affected_viewers",
        lambda _paths: False,
    )
    assert not window.move_selected_to_recycle_bin()
    assert recycle.paths == []
    assert source.exists()
    close_window(window, coordinator, qapp)


def test_delete_mixed_selection_passes_all_paths_to_recycle_adapter(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "books"
    first = folder / "1.cbz"
    second = folder / "2.cbz"
    subfolder = folder / "folder"
    write_file(first)
    write_file(second)
    subfolder.mkdir(parents=True)
    write_file(subfolder / "child.txt")
    recycle = MovingRecycleBin(tmp_path / "trash")
    window, coordinator = make_window(
        tmp_path,
        qapp,
        folder,
        recycle_bin=recycle,
    )
    select_paths(window, [first, second, subfolder])
    expected_paths = list(window.selected_file_operation_paths())
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    assert window.move_selected_to_recycle_bin()
    finish_operation(window, coordinator, qapp)

    assert set(recycle.paths) == set(expected_paths)
    assert not first.exists()
    assert not second.exists()
    assert not subfolder.exists()
    assert window.item_model.row_for_path(first) == -1
    assert window.item_model.row_for_path(second) == -1
    assert window.item_model.row_for_path(subfolder) == -1
    close_window(window, coordinator, qapp)


def test_delete_partial_failure_removes_only_successful_items(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "books"
    successful = folder / "success.cbz"
    failed = folder / "failed"
    write_file(successful)
    failed.mkdir(parents=True)
    recycle = SelectiveMovingRecycleBin(tmp_path / "trash", {failed.name})
    window, coordinator = make_window(
        tmp_path,
        qapp,
        folder,
        recycle_bin=recycle,
    )
    select_paths(window, [successful, failed])
    expected_paths = list(window.selected_file_operation_paths())
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    assert window.move_selected_to_recycle_bin()
    finish_operation(window, coordinator, qapp)

    assert set(recycle.paths) == set(expected_paths)
    assert not successful.exists()
    assert failed.exists()
    assert window.item_model.row_for_path(successful) == -1
    failed_row = window.item_model.row_for_path(failed)
    assert failed_row >= 0
    selected = window.selected_file_operation_paths()
    assert selected == (str(failed.absolute()),)
    assert len(warnings) == 1
    close_window(window, coordinator, qapp)


def test_copy_cut_paste_and_multiple_selection_use_absolute_paths(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    source_folder = tmp_path / "source"
    destination = tmp_path / "destination"
    first = source_folder / "1.cbz"
    second = source_folder / "2.cbz"
    write_file(first, "one")
    write_file(second, "two")
    destination.mkdir()
    window, coordinator = make_window(tmp_path, qapp, source_folder)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: None)
    select_paths(window, [first, second])

    assert window.copy_selected_items()
    assert window._clipboard_paths == (
        str(first.absolute()),
        str(second.absolute()),
    )
    assert window.navigate_to(destination)
    assert window.wait_for_scan()
    assert window.paste_items()
    finish_operation(window, coordinator, qapp)

    assert (destination / "1.cbz").read_text(encoding="utf-8") == "one"
    assert (destination / "2.cbz").read_text(encoding="utf-8") == "two"
    assert {
        Path(item.path).name
        for item in window.items
    } == {"1.cbz", "2.cbz"}

    assert window.navigate_to(source_folder)
    assert window.wait_for_scan()
    select_paths(window, [first])
    assert window.cut_selected_items()
    assert window.navigate_to(destination)
    assert window.wait_for_scan()
    assert window.paste_items()
    finish_operation(window, coordinator, qapp)
    # A collision is skipped; cut state remains and neither file is overwritten.
    assert first.exists()
    assert window._clipboard_cut
    qapp.clipboard().clear()
    qapp.processEvents()
    close_window(window, coordinator, qapp)


def test_specified_copy_move_and_new_folder_refresh_only_relevant_folder(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    source_folder = tmp_path / "source"
    destination = tmp_path / "destination"
    copied = source_folder / "copy.cbz"
    moved = source_folder / "move.cbz"
    write_file(copied)
    write_file(moved)
    destination.mkdir()
    window, coordinator = make_window(tmp_path, qapp, source_folder)
    history_size = len(window.navigation_history)

    select_paths(window, [copied])
    assert window.copy_selected_to(destination)
    finish_operation(window, coordinator, qapp)
    assert (destination / copied.name).exists()
    assert copied.exists()

    select_paths(window, [moved])
    assert window.move_selected_to(destination)
    finish_operation(window, coordinator, qapp)
    assert not moved.exists()
    assert (destination / moved.name).exists()

    monkeypatch.setattr(
        window,
        "_prompt_for_filename",
        lambda *_args: "新しいフォルダ",
    )
    assert window.create_new_folder()
    finish_operation(window, coordinator, qapp)
    created = source_folder / "新しいフォルダ"
    assert created.is_dir()
    selected = window.item_model.item_at(window.list_view.currentIndex())
    assert selected is not None and selected.path == created.absolute()
    assert len(window.navigation_history) == history_size
    close_window(window, coordinator, qapp)


def test_partial_failure_is_reported_once_and_successes_are_refreshed(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    first = source / "1.cbz"
    second = source / "2.cbz"
    write_file(first)
    write_file(second)
    destination.mkdir()
    write_file(destination / "1.cbz", "collision")
    window, coordinator = make_window(tmp_path, qapp, source)
    select_paths(window, [first, second])
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    assert window.copy_selected_to(destination)
    finish_operation(window, coordinator, qapp)

    assert len(warnings) == 1
    assert (destination / "1.cbz").read_text(encoding="utf-8") == "collision"
    assert (destination / "2.cbz").exists()
    close_window(window, coordinator, qapp)


def test_shortcuts_do_not_steal_address_bar_text_editing(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    file_path = folder / "book.cbz"
    write_file(file_path)
    recycle = MovingRecycleBin(tmp_path / "trash")
    window, coordinator = make_window(
        tmp_path,
        qapp,
        folder,
        recycle_bin=recycle,
    )
    window.address_bar.setText("abcdef")
    window.focus_address_bar()
    window.address_bar.setText("abcdef")
    window.address_bar.setSelection(1, 2)

    QTest.keyClick(
        window.address_bar,
        Qt.Key.Key_C,
        Qt.KeyboardModifier.ControlModifier,
    )
    QTest.keyClick(window.address_bar, Qt.Key.Key_Delete)

    assert window.address_bar.text() == "adef"
    assert recycle.paths == []
    close_window(window, coordinator, qapp)


def test_context_menu_has_exact_labels_and_separator_order(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    window, coordinator = make_window(tmp_path, qapp, folder)
    items: list[str | None] = []
    actions: dict[str, FakeAction] = {}

    class FakeAction:
        def __init__(self, text: str) -> None:
            self._text = text
            self.enabled = True

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    class FakeMenu:
        def __init__(self, _parent=None) -> None:
            pass

        def addAction(self, text: str):
            items.append(text)
            action = FakeAction(text)
            actions[text] = action
            return action

        def addSeparator(self) -> None:
            items.append(None)

        def addMenu(self, text: str):
            items.append(text)

            class FakeSubMenu:
                @staticmethod
                def addAction(label: str):
                    return FakeAction(label)

            return FakeSubMenu()

        def exec(self, _position):
            return None

    monkeypatch.setattr("app.browser_window.QMenu", FakeMenu)

    window._show_context_menu(QPoint(-10, -10))

    assert items == [
        "開く",
        "関連付けで開く...",
        "エクスプローラーで開く",
        None,
        "切り取り",
        "コピー",
        "貼り付け",
        None,
        "削除",
        "レート",
        None,
        "プロパティ",
    ]
    for unwanted in (
        "ごみ箱へ移動",
        "名前の変更",
        "指定先へコピー",
        "指定先へ移動",
    ):
        assert unwanted not in items
    assert not actions["削除"].enabled
    assert not actions["関連付けで開く..."].enabled
    close_window(window, coordinator, qapp)


def test_context_menu_open_calls_existing_open_item_not_system_opener(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "folder"
    source = folder / "book.cbz"
    write_file(source)
    window, coordinator = make_window(tmp_path, qapp, folder)
    select_paths(window, [source])
    actions: dict[str, object] = {}

    class FakeAction:
        def __init__(self, text: str) -> None:
            self.text = text

        def setEnabled(self, _enabled: bool) -> None:
            pass

    class FakeMenu:
        def __init__(self, _parent=None) -> None:
            pass

        def addAction(self, text: str):
            action = FakeAction(text)
            actions[text] = action
            return action

        def addSeparator(self) -> None:
            pass

        def addMenu(self, _text: str):
            return self

        def exec(self, _position):
            return actions["開く"]

    opened = []
    system_opened = []
    monkeypatch.setattr("app.browser_window.QMenu", FakeMenu)
    monkeypatch.setattr(
        window,
        "open_item",
        lambda index, **kwargs: opened.append((index, kwargs)),
    )
    monkeypatch.setattr(
        window,
        "_open_system_file",
        lambda path: system_opened.append(path),
    )
    row = window.item_model.row_for_path(source)
    index = window.item_model.index(row, 0)

    window._show_context_menu(window.list_view.visualRect(index).center())

    assert len(opened) == 1
    assert opened[0][0] == index
    assert system_opened == []
    close_window(window, coordinator, qapp)


def test_context_menu_open_with_picker_calls_explicit_picker_for_file(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "folder"
    source = folder / "日本語 book.cbz"
    write_file(source)
    window, coordinator = make_window(tmp_path, qapp, folder)
    select_paths(window, [source])
    actions: dict[str, object] = {}

    class FakeAction:
        def __init__(self, text: str) -> None:
            self.text = text
            self.enabled = True

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    class FakeMenu:
        def __init__(self, _parent=None) -> None:
            pass

        def addAction(self, text: str):
            action = FakeAction(text)
            actions[text] = action
            return action

        def addSeparator(self) -> None:
            pass

        def addMenu(self, _text: str):
            return self

        def exec(self, _position):
            return actions["関連付けで開く..."]

    opened = []
    monkeypatch.setattr("app.browser_window.QMenu", FakeMenu)
    monkeypatch.setattr(
        window,
        "_open_with_application_picker",
        lambda item: opened.append(item),
    )
    row = window.item_model.row_for_path(source)
    index = window.item_model.index(row, 0)

    window._show_context_menu(window.list_view.visualRect(index).center())

    assert actions["関連付けで開く..."].enabled
    assert len(opened) == 1
    assert opened[0].path == source
    close_window(window, coordinator, qapp)


def test_context_menu_open_with_picker_enablement(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "folder"
    first = folder / "first.cbz"
    second = folder / "second.pdf"
    child = folder / "child"
    write_file(first)
    write_file(second)
    child.mkdir()
    window, coordinator = make_window(tmp_path, qapp, folder)
    menus: list[dict[str, object]] = []

    class FakeAction:
        def __init__(self, text: str) -> None:
            self.text = text
            self.enabled = True

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    class FakeMenu:
        def __init__(self, _parent=None) -> None:
            self.actions: dict[str, FakeAction] = {}
            menus.append(self.actions)

        def addAction(self, text: str):
            action = FakeAction(text)
            self.actions[text] = action
            return action

        def addSeparator(self) -> None:
            pass

        def addMenu(self, _text: str):
            class FakeSubMenu:
                @staticmethod
                def addAction(label: str):
                    return FakeAction(label)

            return FakeSubMenu()

        def exec(self, _position):
            return None

    monkeypatch.setattr("app.browser_window.QMenu", FakeMenu)

    def show_for(path: Path, selected: list[Path]) -> FakeAction:
        select_paths(window, selected)
        row = window.item_model.row_for_path(path)
        index = window.item_model.index(row, 0)
        window._show_context_menu(window.list_view.visualRect(index).center())
        return menus[-1]["関連付けで開く..."]  # type: ignore[return-value]

    assert show_for(first, [first]).enabled
    assert not show_for(child, [child]).enabled
    assert not show_for(first, [first, second]).enabled
    first.unlink()
    assert not show_for(first, [first]).enabled
    close_window(window, coordinator, qapp)


def test_context_menu_explorer_uses_clicked_item_with_multiple_selection(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "folder"
    first = folder / "first.zip"
    second = folder / "second.pdf"
    write_file(first)
    write_file(second)
    window, coordinator = make_window(tmp_path, qapp, folder)
    select_paths(window, [first, second])
    actions: dict[str, object] = {}

    class FakeAction:
        def __init__(self, text: str) -> None:
            self.text = text
            self.enabled = True

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = enabled

    class FakeMenu:
        def __init__(self, _parent=None) -> None:
            pass

        def addAction(self, text: str):
            action = FakeAction(text)
            actions[text] = action
            return action

        def addSeparator(self) -> None:
            pass

        def addMenu(self, _text: str):
            return self

        def exec(self, _position):
            return actions["エクスプローラーで開く"]

    opened = []
    monkeypatch.setattr("app.browser_window.QMenu", FakeMenu)
    monkeypatch.setattr(
        window,
        "_open_item_in_explorer",
        lambda item: opened.append(item),
    )
    row = window.item_model.row_for_path(second)
    index = window.item_model.index(row, 0)

    window._show_context_menu(window.list_view.visualRect(index).center())

    assert len(opened) == 1
    assert opened[0].path == second
    close_window(window, coordinator, qapp)


class SlowService(FileOperationService):
    def __init__(self, started: Event, release: Event) -> None:
        self.started = started
        self.release = release

    def execute(self, request, *, cancelled=None, progress=None):
        self.started.set()
        if progress is not None:
            from app.file_operation_service import FileOperationProgress

            progress(
                FileOperationProgress(
                    request.request_id,
                    request.operation,
                    1,
                    2,
                )
            )
        self.release.wait(2)
        return FileOperationResult(
            request.operation,
            (),
            cancelled=bool(cancelled and cancelled.is_set()),
            request_id=request.request_id,
        )


def test_cancelled_cut_paste_keeps_source_and_cut_state(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source_folder = tmp_path / "source"
    destination = tmp_path / "destination"
    source = source_folder / "book.cbz"
    write_file(source)
    destination.mkdir()
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(source_folder))
    metadata = MetadataStore(tmp_path / "metadata.sqlite3")
    started = Event()
    release = Event()
    coordinator = FileOperationCoordinator(
        metadata,
        executor=FileOperationExecutor(SlowService(started, release)),
    )
    window = BrowserWindow(
        config_manager=config,
        metadata_store=metadata,
        file_operation_coordinator=coordinator,
    )
    window.show()
    assert window.wait_for_scan()
    select_paths(window, [source])

    try:
        assert window.cut_selected_items()
        assert window.navigate_to(destination)
        assert window.wait_for_scan()
        assert window.paste_items()
        assert started.wait(1)

        window.cancel_file_operation()
        release.set()
        finish_operation(window, coordinator, qapp)

        assert source.exists()
        assert not (destination / source.name).exists()
        assert window._clipboard_cut
        assert window._clipboard_paths == (str(source.absolute()),)
        qapp.clipboard().clear()
        qapp.processEvents()
    finally:
        release.set()
        close_window(window, coordinator, qapp)


def test_slow_operation_shows_progress_and_keeps_qtimer_running(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    source = folder / "book.cbz"
    write_file(source)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    started = Event()
    release = Event()
    coordinator = FileOperationCoordinator(
        None,
        executor=FileOperationExecutor(SlowService(started, release)),
    )
    window = BrowserWindow(
        config_manager=config,
        file_operation_coordinator=coordinator,
    )
    window.show()
    assert window.wait_for_scan()
    select_paths(window, [source])
    try:
        assert window.copy_selected_to(tmp_path)
        assert started.wait(1)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        qapp.processEvents()
        assert ticks == [True]
        assert "1 / 2" in window.statusBar().currentMessage()
        assert window.cancel_operation_button.isVisible()
    finally:
        release.set()
        coordinator.wait_for_done(2000)
        qapp.processEvents()
        window.close()
        coordinator.close()
