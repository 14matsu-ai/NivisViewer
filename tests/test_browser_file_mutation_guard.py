from __future__ import annotations

import errno
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.file_operation_coordinator import FileOperationCoordinator
from app.file_operation_queue import FileOperationQueue
from app.file_operation_service import FileOperationKind, FileOperationService
from app.file_operation_worker import FileOperationExecutor
from app.folder_tree_model import FolderTreeModel
from app.metadata_store import MetadataStore
from tests.test_application_controller import wait_until
from tests.test_critical_followup import _close_browser, _finish_operation, _select_paths


def make_browser(tmp_path, qapp, folder, *, backend="queue"):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"last_browser_path": str(folder), "browser_show_unsupported_files": True}, save=False)
    metadata = MetadataStore(tmp_path / "metadata.sqlite3")
    service = FileOperationService()
    entered, release = Event(), Event()
    execute = service.execute

    def delayed_execute(request, **kwargs):
        entered.set()
        assert release.wait(10)
        return execute(request, **kwargs)

    service.execute = delayed_execute
    if backend == "queue":
        coordinator = FileOperationCoordinator(metadata, queue=FileOperationQueue(service=service))
    else:
        coordinator = FileOperationCoordinator(metadata, executor=FileOperationExecutor(service))
    window = BrowserWindow(config_manager=config, metadata_store=metadata, file_operation_coordinator=coordinator)
    window.show()
    assert window.wait_for_scan()
    qapp.processEvents()
    return window, coordinator, service, entered, release


def fixture_folders(tmp_path):
    parent = tmp_path / "元"
    source = parent / "対象"
    nested = source / "階下"
    nested.mkdir(parents=True)
    (nested / "日本語.txt").write_text("keep", encoding="utf-8")
    destination = tmp_path / "先"
    destination.mkdir()
    unrelated = parent / "対象と別のフォルダ"
    unrelated.mkdir()
    return parent, source, nested, destination, unrelated


def assert_tree_watches_restored(window, qapp, folder):
    assert wait_until(qapp, lambda: not window._tree_watch_suspended)
    model = window.file_system_model
    assert not model.testOption(FolderTreeModel.Option.DontWatchForChanges)
    index = model.index(str(folder))
    if model.canFetchMore(index):
        model.fetchMore(index)
    assert wait_until(qapp, lambda: model.rowCount(index) == 0)
    (folder / "監視復帰確認").mkdir()
    assert wait_until(qapp, lambda: model.rowCount(index) == 1)


@pytest.mark.parametrize("backend", ["queue", "executor"])
def test_cut_move_releases_existing_child_watches_and_blocks_only_affected_paths(tmp_path, qapp, backend):
    parent, source, nested, destination, unrelated = fixture_folders(tmp_path)
    window, coordinator, _service, entered, release = make_browser(tmp_path, qapp, parent, backend=backend)
    results = []
    coordinator.operation_completed.connect(results.append)
    try:
        assert window.navigate_to(nested)
        assert window.wait_for_scan()
        nested_history = window.navigation_history.current_index
        window._sync_tree_to_path(nested)
        QTest.qWait(50)
        assert window.navigate_to(parent)
        assert window.wait_for_scan()
        _select_paths(window, [source])
        window.cut_action.trigger()
        assert window.navigate_to(destination)
        assert window.wait_for_scan()
        assert window.paste_items()
        assert wait_until(qapp, entered.is_set)
        old_model = window.file_system_model
        history_index = window.navigation_history.current_index
        assert not window.navigate_to(nested)
        assert "移動中" in window.statusBar().currentMessage()
        assert window._pending_scan is None
        assert not window.go_to_history_index(nested_history)
        assert window.navigation_history.current_index == history_index
        opened = []
        window._open_path_handler = lambda *args, **kwargs: opened.append(args)
        window._invoke_open_path_handler(str(nested / "日本語.txt"), False)
        assert not opened
        assert window.navigate_to(unrelated)
        assert window.wait_for_scan()
        release.set()
        _finish_operation(window, coordinator, qapp)
        assert results[0].successes and not results[0].failures
        assert not source.exists()
        assert (destination / source.name / nested.name / "日本語.txt").read_text(encoding="utf-8") == "keep"
        assert window.current_path == unrelated
        assert not window._clipboard_cut
        assert not window._file_mutation_requests
        assert_tree_watches_restored(window, qapp, unrelated)
        assert window.file_system_model is not old_model
        assert window.directory_watcher.path == str(unrelated)
    finally:
        release.set()
        _close_browser(window, coordinator, qapp)


def test_displayed_descendant_follows_successful_parent_move(tmp_path, qapp):
    _parent, source, nested, destination, _unrelated = fixture_folders(tmp_path)
    window, coordinator, _service, entered, release = make_browser(tmp_path, qapp, nested)
    try:
        _select_paths(window, [nested / "日本語.txt"])
        old_model = window.file_system_model
        assert window._start_file_operation(FileOperationKind.MOVE, sources=(str(source),), destination=destination)
        assert wait_until(qapp, entered.is_set)
        assert window.directory_watcher.path is None
        assert not window.refresh_current_folder()
        release.set()
        _finish_operation(window, coordinator, qapp)
        expected = destination / source.name / nested.name
        assert window.current_path == expected
        assert window.selected_file_operation_paths() == (str(expected / "日本語.txt"),)
        assert window.navigation_history.current().path == str(expected)
        assert window.directory_watcher.path == str(expected)
        assert wait_until(qapp, lambda: window.file_system_model is not old_model)
    finally:
        release.set()
        _close_browser(window, coordinator, qapp)


@pytest.mark.parametrize("outcome", ["failure", "cancel"])
def test_failed_or_cancelled_cut_restores_watches_and_retains_retry(tmp_path, qapp, monkeypatch, outcome):
    parent, source, nested, destination, unrelated = fixture_folders(tmp_path)
    window, coordinator, service, entered, release = make_browser(tmp_path, qapp, parent)
    move = service._move_safe
    if outcome == "failure":
        def deny(*args, **kwargs):
            raise PermissionError(errno.EACCES, "injected folder lock")
        monkeypatch.setattr(service, "_move_safe", deny)
    try:
        _select_paths(window, [source])
        window.cut_action.trigger()
        assert window.navigate_to(destination)
        assert window.wait_for_scan()
        assert window.paste_items()
        assert wait_until(qapp, entered.is_set)
        if outcome == "cancel":
            window.cancel_file_operation()
        release.set()
        _finish_operation(window, coordinator, qapp)
        assert source.exists()
        assert window._clipboard_paths == (str(source),)
        assert window._clipboard_cut
        assert not window._file_mutation_requests
        assert_tree_watches_restored(window, qapp, unrelated)
        if outcome == "failure":
            panel = window.file_operation_panel
            assert panel.isVisible()
            assert panel.awaiting_result_acknowledgement
            panel.details_button.click()
            assert panel.details_view.isVisible()
            assert "injected folder lock" in panel.details_view.toPlainText()
        assert window.navigate_to(nested)
        assert window.wait_for_scan()
        assert window.navigate_to(destination)
        assert window.wait_for_scan()
        monkeypatch.setattr(service, "_move_safe", move)
        assert window.paste_items()
        _finish_operation(window, coordinator, qapp)
        assert not source.exists()
        assert not window._clipboard_cut
        if outcome == "failure":
            assert panel.awaiting_result_acknowledgement
            assert "injected folder lock" in panel.details_view.toPlainText()
            panel.cancel_button.click()
            qapp.processEvents()
            assert panel.isHidden()
    finally:
        release.set()
        _close_browser(window, coordinator, qapp)


def test_queued_move_protects_newly_displayed_child_before_it_starts(tmp_path, qapp):
    parent, source, _nested, destination, _unrelated = fixture_folders(tmp_path)
    second = parent / "次の対象"
    second_nested = second / "階下"
    second_nested.mkdir(parents=True)
    (second_nested / "file.txt").write_text("second", encoding="utf-8")
    window, coordinator, _service, entered, release = make_browser(tmp_path, qapp, parent)
    results = []
    coordinator.operation_completed.connect(results.append)
    old_model = window.file_system_model
    try:
        assert window._start_file_operation(FileOperationKind.MOVE, sources=(str(source),), destination=destination)
        assert wait_until(qapp, entered.is_set)
        assert window.navigate_to(second_nested)
        assert window.wait_for_scan()
        assert window._start_file_operation(FileOperationKind.MOVE, sources=(str(second),), destination=destination)
        assert len(coordinator.queue.queued_requests) == 1
        release.set()
        _finish_operation(window, coordinator, qapp)
        assert len(results) == 2
        assert all(result.successes and not result.failures for result in results)
        assert window.current_path == destination / second.name / second_nested.name
        assert not window._file_mutation_requests
        assert wait_until(qapp, lambda: not window._tree_watch_suspended)
        assert window.file_system_model is not old_model
        assert window.directory_watcher.path == str(window.current_path)
    finally:
        release.set()
        _close_browser(window, coordinator, qapp)
