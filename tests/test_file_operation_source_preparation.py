from __future__ import annotations

import errno
import json
import logging
import os
from pathlib import Path
import stat
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer

from app.file_operation_plan import FileOperationPlanner, FileOperationState
from app.file_operation_queue import FileOperationQueue
from app.file_operation_service import (
    FileOperationKind, FileOperationRequest, FileOperationService,
)
from app.file_operation_sources import SourcePreparationCancelled


@pytest.fixture(params=[FileOperationPlanner, FileOperationService], ids=["planner", "service"])
def owner(request):
    return request.param()


@pytest.mark.parametrize("count", [1, 200, 600])
@pytest.mark.parametrize("directory", [False, True], ids=["images", "directories"])
def test_source_preparation_filesystem_calls_are_linear(owner, monkeypatch, count, directory):
    paths = [f"C:/synthetic-source/image-{index:04}.jpg" for index in range(count)]
    calls = {"stat": 0, "realpath": 0, "isdir": 0}
    original_stat, original_realpath, original_isdir = os.stat, os.path.realpath, os.path.isdir

    def synthetic(path):
        return os.path.normcase(os.fspath(path)).startswith("c:\\synthetic-source\\")

    def kind(path, *args, **kwargs):
        if synthetic(path):
            calls["stat"] += 1
            return SimpleNamespace(st_mode=stat.S_IFDIR if directory else stat.S_IFREG)
        return original_stat(path, *args, **kwargs)

    def resolve(path, *args, **kwargs):
        if synthetic(path):
            calls["realpath"] += 1
            return path
        return original_realpath(path, *args, **kwargs)

    def isdir(path):
        if synthetic(path):
            calls["isdir"] += 1
            return directory
        return original_isdir(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", kind)
        patch.setattr(os.path, "realpath", resolve)
        patch.setattr(os.path, "isdir", isdir)
        selected = owner._prepare_sources(reversed(paths))
    assert selected == tuple(owner._absolute(path) for path in paths)
    assert calls == {"stat": count, "realpath": count if directory else 0, "isdir": 0}
    print(json.dumps({"phase": type(owner).__name__, "selected": count, "directory": directory, **calls}))


def test_directory_children_duplicates_and_order(owner, tmp_path):
    folder = tmp_path / "folder"
    nested = folder / "nested"
    nested.mkdir(parents=True)
    child = nested / "image.png"
    child.write_bytes(b"child")
    first = tmp_path / "a.png"
    last = tmp_path / "z.png"
    first.write_bytes(b"a")
    last.write_bytes(b"z")
    selected = owner._prepare_sources([child, last, nested, folder, first, str(folder).upper(), last])
    assert selected == tuple(str(path) for path in sorted([folder, first, last], key=lambda p: str(p).casefold()))


def test_resolved_directory_alias_suppresses_physical_child_once(owner, tmp_path, monkeypatch):
    alias = tmp_path / "alias"
    physical = tmp_path / "physical" / "folder"
    physical.mkdir(parents=True)
    children = [physical / f"{index:03}.png" for index in range(20)]
    for child in children:
        child.write_bytes(b"child")
    original_stat, original_realpath = os.stat, os.path.realpath
    resolved = []

    def kind(path, *args, **kwargs):
        if Path(path) == alias:
            return original_stat(physical)
        return original_stat(path, *args, **kwargs)

    def resolve(path, *args, **kwargs):
        resolved.append(os.fspath(path))
        return str(physical) if Path(path) == alias else original_realpath(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", kind)
    monkeypatch.setattr(os.path, "realpath", resolve)
    assert owner._prepare_sources([*reversed(children), alias]) == (str(alias),)
    assert len(resolved) == len(set(resolved)) == 21


@pytest.mark.parametrize("failure", [OSError(errno.EACCES, "denied"), RuntimeError("loop"), ValueError("invalid")])
def test_unresolved_selected_directory_remains_conservative(owner, tmp_path, monkeypatch, failure):
    root = tmp_path / "a"
    root.mkdir()
    other = tmp_path / "z.png"
    other.write_bytes(b"source")
    original_realpath = os.path.realpath

    def resolve(path, *args, **kwargs):
        if Path(path) == root:
            raise failure
        return original_realpath(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", resolve)
    assert owner._prepare_sources([other, root]) == (str(root),)
    assert other.read_bytes() == b"source"


def test_missing_and_inaccessible_sources_are_not_parent_directories(owner, tmp_path, monkeypatch):
    missing = tmp_path / "missing"
    child = missing / "child.png"
    blocked = tmp_path / "blocked"
    blocked_child = blocked / "child.png"
    original_stat = os.stat

    def kind(path, *args, **kwargs):
        if Path(path) == blocked:
            raise PermissionError(errno.EACCES, "denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", kind)
    sources = [child, blocked_child, missing, blocked]
    expected = sorted(map(str, sources), key=lambda p: (len(Path(p).parts), p.casefold()))
    assert owner._prepare_sources(sources) == tuple(expected)


@pytest.mark.parametrize("root", [r"\\server\share", "Z:\\"])
def test_network_source_spellings_and_root_ancestry_are_lexical(owner, monkeypatch, root):
    child = os.path.join(root, "画像.png")
    original_stat = os.stat

    def kind(path, *args, **kwargs):
        if os.path.normcase(os.fspath(path)).rstrip("\\") == os.path.normcase(root).rstrip("\\"):
            return SimpleNamespace(st_mode=stat.S_IFDIR)
        return original_stat(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", kind)
        patch.setattr(os.path, "realpath", lambda path: path)
        selected = owner._prepare_sources([child, root])
    assert selected == (owner._absolute(root),)


def test_pre_cancelled_preparation_never_iterates_or_probes(owner, monkeypatch, caplog):
    cancelled = Event()
    cancelled.set()

    def paths():
        raise AssertionError("cancelled sources must not be consumed")
        yield "unused"

    with caplog.at_level(logging.INFO, logger="nivisviewer.file_operation"):
        with pytest.raises(SourcePreparationCancelled):
            owner._prepare_sources(paths(), cancelled=cancelled)
    assert "state=cancelled" in caplog.text
    assert "input=0" in caplog.text


def test_cancel_during_normalization_stops_before_filesystem_probe(owner, monkeypatch):
    cancelled = Event()
    original_absolute = owner._absolute
    normalized = []

    def normalize(path):
        normalized.append(path)
        if len(normalized) == 3:
            cancelled.set()
        return original_absolute(path)

    monkeypatch.setattr(owner, "_absolute", normalize)
    with pytest.raises(SourcePreparationCancelled):
        owner._prepare_sources([f"C:/synthetic/{i}.png" for i in range(600)], cancelled=cancelled)
    assert len(normalized) == 3


@pytest.mark.parametrize("phase", ["planner", "service", "zip"])
def test_cancel_during_kind_probe_returns_normal_cancel_result(tmp_path, monkeypatch, caplog, phase):
    sources = [tmp_path / f"{index:03}.png" for index in range(5)]
    for source in sources:
        source.write_bytes(b"source")
    cancel = Event()
    calls = []
    original_stat = os.stat

    def kind(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if Path(path) in sources:
            calls.append(Path(path))
            cancel.set()
        return result

    monkeypatch.setattr(os, "stat", kind)
    request = FileOperationRequest(
        31, FileOperationKind.CREATE_ZIP if phase == "zip" else FileOperationKind.MOVE,
        tuple(map(str, sources)), str(tmp_path), new_name="bundle.zip",
    )
    with caplog.at_level(logging.INFO, logger="nivisviewer.file_operation"):
        result = (FileOperationPlanner().prepare(request, cancelled=cancel) if phase == "planner"
                  else FileOperationService().execute(request, cancelled=cancel))
    if phase == "planner":
        assert result.state is FileOperationState.CANCELLED
    else:
        assert result.cancelled
    assert len(calls) == 1
    assert all(source.read_bytes() == b"source" for source in sources)
    assert not (tmp_path / "bundle.zip").exists()
    assert "source preparation end" in caplog.text and "state=cancelled" in caplog.text
    assert str(tmp_path) not in caplog.text


def test_cancel_during_resolution_stops_after_current_call(owner, tmp_path, monkeypatch):
    root = tmp_path / "folder"
    root.mkdir()
    cancel = Event()
    resolved = []

    def resolve(path, *args, **kwargs):
        resolved.append(os.fspath(path))
        cancel.set()
        return path

    monkeypatch.setattr(os.path, "realpath", resolve)
    with pytest.raises(SourcePreparationCancelled):
        owner._prepare_sources([root, *[root / f"{index}.png" for index in range(600)]], cancelled=cancel)
    assert resolved == [str(root)]
    assert root.is_dir()


def test_source_kinds_are_refreshed_between_preparations(owner, tmp_path):
    root = tmp_path / "item"
    root.write_bytes(b"initial file")
    child = root / "child.png"
    assert owner._prepare_sources([child, root]) == (str(root), str(child))
    root.unlink()
    root.mkdir()
    child.write_bytes(b"new child")
    assert owner._prepare_sources([child, root]) == (str(root),)
    assert child.read_bytes() == b"new child"


@pytest.mark.parametrize("operation", [FileOperationKind.COPY, FileOperationKind.MOVE])
@pytest.mark.parametrize("count", [200, 600])
def test_large_image_selection_through_planner_and_service(tmp_path, operation, count):
    source_folder = tmp_path / "source"
    source_folder.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    sources = tuple(source_folder / f"image-{index:04}.png" for index in range(count))
    for source in sources:
        source.write_bytes(source.name.encode("ascii"))
    request = FileOperationRequest(34, operation, tuple(map(str, reversed(sources))), str(target))
    plan = FileOperationPlanner().prepare(request)
    assert plan.ready and plan.total_files == count
    result = FileOperationService().execute(request)
    assert len(result.successes) == count and not result.failures
    assert tuple(item.source_path for item in result.items) == tuple(map(str, sources))
    for source in sources:
        assert (target / source.name).read_bytes() == source.name.encode("ascii")
        assert source.exists() is (operation is FileOperationKind.COPY)
    assert len(result.undo_entries) == count


@pytest.mark.parametrize("operation", [FileOperationKind.COPY, FileOperationKind.MOVE,
                                      FileOperationKind.RENAME, FileOperationKind.RECYCLE])
def test_pre_cancelled_service_common_operations_do_no_source_io(monkeypatch, operation):
    cancel = Event()
    cancel.set()
    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", lambda *a, **k: pytest.fail("source stat after cancellation"))
        result = FileOperationService().execute(FileOperationRequest(
            32, operation, (r"\\server\share\source.png",), r"C:\destination", new_name="renamed.png",
        ), cancelled=cancel)
    assert result.cancelled and not result.items


def test_changed_source_reparse_state_is_rechecked_at_execution(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    source.write_bytes(b"protected")
    target = tmp_path / "target"
    target.mkdir()
    service = FileOperationService()
    prepare = service._prepare_sources
    original_lstat = os.lstat
    prepared = False

    def prepare_then_change(*args, **kwargs):
        nonlocal prepared
        sources = prepare(*args, **kwargs)
        prepared = True
        return sources

    def reparse(path, *args, **kwargs):
        value = original_lstat(path, *args, **kwargs)
        if prepared and Path(path) == source:
            attributes = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
            attributes["st_file_attributes"] = 0x400
            return SimpleNamespace(**attributes)
        return value

    monkeypatch.setattr(service, "_prepare_sources", prepare_then_change)
    monkeypatch.setattr(os, "lstat", reparse)
    item = service.move([source], target).items[0]
    assert not item.success and not item.source_removed
    assert source.read_bytes() == b"protected" and not tuple(target.iterdir())


def test_queue_cancel_during_source_probe_does_not_process_remaining_selection(qapp, monkeypatch, caplog):
    entered, release = Event(), Event()
    calls = []
    original_stat = os.stat
    source_prefix = os.path.normcase("C:\\synthetic-source\\")

    def delayed_kind(path, *args, **kwargs):
        if os.path.normcase(os.fspath(path)).startswith(source_prefix):
            calls.append(os.fspath(path))
            entered.set()
            assert release.wait(3)
            return SimpleNamespace(st_mode=stat.S_IFREG)
        return original_stat(path, *args, **kwargs)

    queue = FileOperationQueue()
    executed, completed, ticks = [], [], []
    queue.operation_started.connect(executed.append)
    queue.operation_completed.connect(completed.append)
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    try:
        with monkeypatch.context() as patch, caplog.at_level(logging.INFO, logger="nivisviewer.file_operation"):
            patch.setattr(os, "stat", delayed_kind)
            timer.start()
            assert queue.enqueue(FileOperationRequest(
                33, FileOperationKind.MOVE,
                tuple(f"C:/synthetic-source/{index:03}.png" for index in range(600)), r"C:\target",
            ))
            assert entered.wait(2)
            deadline = monotonic() + 1
            while len(ticks) < 3 and monotonic() < deadline:
                qapp.processEvents()
                sleep(.001)
            assert len(ticks) >= 3
            assert queue.cancel()
            assert not completed
            release.set()
            assert queue.wait_for_done(3000)
            qapp.processEvents()
        assert len(calls) == 1
        assert not executed
        assert len(completed) == 1 and completed[0].cancelled
        assert "state=cancelled" in caplog.text
        assert source_prefix not in caplog.text.lower()
    finally:
        release.set()
        timer.stop()
        queue.close()
        queue.wait_for_done(3000)
        qapp.processEvents()
