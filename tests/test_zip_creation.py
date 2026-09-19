from __future__ import annotations

import os
import zipfile
from pathlib import Path
from threading import Event, get_ident

import pytest
from PIL import Image
from PySide6.QtCore import QItemSelectionModel, QPoint, QTimer
from PySide6.QtWidgets import QMenu, QMessageBox

from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.file_operation_coordinator import FileOperationCoordinator
from app.file_operation_queue import FileOperationQueue
from app.file_operation_service import FileOperationKind, FileOperationRequest, FileOperationService


def request(folder, sources, name="まとめ.zip"):
    return FileOperationRequest(1, FileOperationKind.CREATE_ZIP,
                                tuple(map(str, sources)), str(folder), name)


def test_zip_preserves_nested_files_empty_folders_and_unicode(tmp_path):
    source = tmp_path / "日本語"
    (source / "空のフォルダ").mkdir(parents=True)
    (source / "本文.txt").write_bytes(b"content")
    second = tmp_path / "別.txt"
    second.write_bytes(b"second")
    result = FileOperationService().execute(request(tmp_path, [source, second]))
    assert len(result.successes) == 1
    with zipfile.ZipFile(tmp_path / "まとめ.zip") as archive:
        assert set(archive.namelist()) == {"日本語/", "日本語/空のフォルダ/", "日本語/本文.txt", "別.txt"}
        assert archive.read("日本語/本文.txt") == b"content"
        assert archive.read("別.txt") == b"second"
    assert (source / "本文.txt").read_bytes() == b"content"
    assert second.read_bytes() == b"second"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("failure", ["missing", "read", "cancel"])
def test_zip_failure_leaves_sources_and_no_partial_output(tmp_path, monkeypatch, failure):
    source = tmp_path / "元.txt"
    source.write_bytes(b"source" * 200000)
    destination = tmp_path / "まとめ.zip"
    cancelled = Event()
    if failure == "missing":
        sources = [source, tmp_path / "missing"]
    else:
        sources = [source]
    if failure == "read":
        original_open = zipfile.ZipFile.open
        def denied(self, name, mode="r", *args, **kwargs):
            if mode == "w":
                raise PermissionError("injected read/write failure")
            return original_open(self, name, mode, *args, **kwargs)
        monkeypatch.setattr(zipfile.ZipFile, "open", denied)
    def progress(_value):
        if failure == "cancel":
            cancelled.set()
    result = FileOperationService().execute(
        request(tmp_path, sources), cancelled=cancelled, progress=progress,
    )
    assert result.failures and not result.successes
    assert source.read_bytes() == b"source" * 200000
    assert not destination.exists()
    assert result.cancelled == (failure == "cancel")
    assert not list(tmp_path.glob("*.tmp"))


def test_zip_rejects_output_inside_selected_tree(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    result = FileOperationService().execute(request(source, [source]))
    assert result.failures
    assert list(source.iterdir()) == []


@pytest.mark.parametrize("race", [False, True])
def test_zip_collision_auto_numbers_without_overwriting(tmp_path, monkeypatch, race):
    source = tmp_path / "元.txt"
    source.write_bytes(b"source")
    (tmp_path / "まとめ.zip").write_bytes(b"keep base")
    (tmp_path / "まとめ (1).zip").mkdir()
    publish_name = "rename" if os.name == "nt" else "link"
    original_publish = getattr(os, publish_name)
    if race:
        def collide_once(staging, target):
            if Path(target).name == "まとめ (2).zip":
                Path(target).write_bytes(b"race winner")
                raise FileExistsError("race")
            return original_publish(staging, target)
        monkeypatch.setattr(os, publish_name, collide_once)
    result = FileOperationService().execute(request(tmp_path, [source]))
    assert result.successes
    expected = tmp_path / ("まとめ (3).zip" if race else "まとめ (2).zip")
    assert result.successes[0].destination_path == str(expected)
    with zipfile.ZipFile(expected) as archive:
        assert archive.read("元.txt") == b"source"
    assert (tmp_path / "まとめ.zip").read_bytes() == b"keep base"
    assert (tmp_path / "まとめ (1).zip").is_dir()
    if race:
        assert (tmp_path / "まとめ (2).zip").read_bytes() == b"race winner"
    assert source.read_bytes() == b"source"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("selection", ["none", "single", "multiple", "folder", "collision", "failure"])
def test_browser_context_zip_uses_queue_and_selects_output(tmp_path, qapp, monkeypatch, selection):
    folder = tmp_path / "日本語フォルダ"
    folder.mkdir()
    source = folder / "画像.png"
    Image.new("RGB", (8, 8), "red").save(source)
    original = source.read_bytes()
    nested = folder / "子フォルダ"
    nested.mkdir()
    (nested / "内容.txt").write_bytes(b"nested")
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    queue = FileOperationQueue()
    coordinator = FileOperationCoordinator(None, queue=queue)
    window = BrowserWindow(config_manager=config, file_operation_coordinator=coordinator)
    results = []
    coordinator.operation_completed.connect(results.append)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    chosen = [] if selection == "none" else [source]
    if selection == "multiple":
        chosen.append(nested)
    if selection == "folder":
        chosen = [nested]
    expected_name = "日本語フォルダ.zip" if selection == "multiple" else "画像.png.zip"
    if selection == "folder":
        expected_name = "子フォルダ.zip"
    if selection == "collision":
        with zipfile.ZipFile(folder / expected_name, "w"):
            pass
    if selection == "failure":
        monkeypatch.setattr(queue.service, "_is_reparse_path", lambda _path: True)
    prompts = []
    def prompt(_title, _label, initial):
        prompts.append(initial)
        return initial
    monkeypatch.setattr(window, "_prompt_for_filename", prompt)
    gui_thread = get_ident()
    worker_threads = []
    original_execute = queue.service.execute
    def execute(*args, **kwargs):
        worker_threads.append(get_ident())
        return original_execute(*args, **kwargs)
    monkeypatch.setattr(queue.service, "execute", execute)
    def choose_zip(menu, _position):
        action = next(action for action in menu.actions() if action.text() == "zipに圧縮")
        assert action.isEnabled() == bool(chosen)
        return action if action.isEnabled() else None
    class FakeMenu(QMenu):
        def exec(self, position):
            return choose_zip(self, position)
    monkeypatch.setattr("app.browser_window.QMenu", FakeMenu)
    try:
        window.show_initial()
        assert window.wait_for_scan()
        for path in chosen:
            index = window.item_model.index(window.item_model.row_for_path(path), 0)
            window.list_view.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        window._show_context_menu(QPoint(-1, -1))
        assert coordinator.wait_for_done(5000)
        qapp.processEvents()
        assert ticks == [True]
        if not chosen:
            assert not prompts and not results and not worker_threads
            assert window.compress_selected_to_zip() is False
            return
        assert prompts == [expected_name]
        assert worker_threads and all(thread != gui_thread for thread in worker_threads)
        if selection == "failure":
            assert results and results[0].failures
            assert not (folder / expected_name).exists()
            assert source.read_bytes() == original
            assert "失敗" in window.statusBar().currentMessage()
            return
        assert results and results[0].successes
        assert window.wait_for_scan()
        qapp.processEvents()
        destination = folder / ("画像.png (1).zip" if selection == "collision" else expected_name)
        with zipfile.ZipFile(destination) as archive:
            if selection != "folder":
                assert archive.read(source.name) == original
            if selection in {"multiple", "folder"}:
                assert archive.read("子フォルダ/内容.txt") == b"nested"
        if selection == "collision":
            with zipfile.ZipFile(folder / expected_name) as existing:
                assert not existing.namelist()
        assert source.read_bytes() == original
        assert (nested / "内容.txt").read_bytes() == b"nested"
        assert str(destination) in window.selected_file_operation_paths()
        assert not warnings
    finally:
        window.close()
        coordinator.close()
        assert coordinator.wait_for_done(5000)
        qapp.processEvents()
