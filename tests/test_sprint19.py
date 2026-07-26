from __future__ import annotations

import errno
import os
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.chunked_file_copier import ChunkedFileCopier, CopyCancelled
from app.config_manager import ConfigManager
from app.destination_history import DestinationHistoryStore
from app.file_conflict_dialog import ConflictResolutionDialog
from app.file_operation_plan import (
    ConflictResolution,
    FileConflict,
    FileConflictKind,
    FileOperationPlan,
    FileOperationPlanner,
    FileOperationState,
)
from app.file_operation_panel import FileOperationPanel
from app.file_operation_queue import FileOperationQueue
from app.file_operation_service import (
    FileCollisionPolicy,
    FileOperationKind,
    FileOperationItemResult,
    FileOperationProgress,
    FileOperationRequest,
    FileOperationResult,
    FileOperationService,
)


def _request(
    request_id: int,
    source: Path,
    destination: Path,
    *,
    operation: FileOperationKind = FileOperationKind.COPY,
    policy: FileCollisionPolicy = FileCollisionPolicy.SKIP,
) -> FileOperationRequest:
    return FileOperationRequest(
        request_id,
        operation,
        (str(source),),
        str(destination),
        collision_policy=policy,
        operation_id=str(request_id),
    )


def _wait_until(
    app: QApplication,
    predicate,
    *,
    timeout: float = 3.0,
) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        sleep(0.005)
    assert predicate()


def test_planner_normalizes_parent_child_and_counts_bytes(tmp_path: Path) -> None:
    source = tmp_path / "本"
    child = source / "sub"
    child.mkdir(parents=True)
    (source / "1.jpg").write_bytes(b"abc")
    (child / "2.jpg").write_bytes(b"12345")
    destination = tmp_path / "移動先"
    destination.mkdir()
    request = FileOperationRequest(
        1,
        FileOperationKind.COPY,
        (str(source / "1.jpg"), str(source), str(source)),
        str(destination),
    )

    plan = FileOperationPlanner().prepare(request)

    assert plan.source_paths == (str(source),)
    assert plan.total_bytes == 8
    assert plan.total_files == 2
    assert plan.total_directories == 2
    assert plan.total_items == 4
    assert plan.state is FileOperationState.READY


def test_planner_classifies_file_file_and_directory_directory(tmp_path: Path) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    source_file = tmp_path / "book.cbz"
    source_file.write_bytes(b"new")
    (destination / source_file.name).write_bytes(b"old")
    plan = FileOperationPlanner().prepare(_request(1, source_file, destination))
    assert plan.conflicts[0].kind is FileConflictKind.FILE_FILE
    assert plan.conflicts[0].default_resolution is ConflictResolution.SKIP

    source_folder = tmp_path / "folder"
    source_folder.mkdir()
    (destination / source_folder.name).mkdir()
    folder_plan = FileOperationPlanner().prepare(
        _request(2, source_folder, destination)
    )
    assert folder_plan.conflicts[0].kind is FileConflictKind.DIRECTORY_DIRECTORY
    assert ConflictResolution.MERGE in folder_plan.conflicts[0].allowed_resolutions


def test_planner_classifies_missing_and_read_only_destination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("x", encoding="utf-8")
    missing = tmp_path / "missing"
    plan = FileOperationPlanner().prepare(_request(1, source, missing))
    assert {
        conflict.kind for conflict in plan.conflicts
    } == {FileConflictKind.DESTINATION_PARENT_MISSING}

    destination = tmp_path / "destination"
    destination.mkdir()
    monkeypatch.setattr("app.file_operation_plan.os.access", lambda *_args: False)
    plan = FileOperationPlanner().prepare(_request(2, source, destination))
    assert FileConflictKind.DESTINATION_READ_ONLY in {
        conflict.kind for conflict in plan.conflicts
    }


def test_planner_rejects_descendant_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = source / "child"
    destination.mkdir(parents=True)
    plan = FileOperationPlanner().prepare(_request(1, source, destination))
    assert FileConflictKind.SAME_PATH in {
        conflict.kind for conflict in plan.conflicts
    }


def test_chunked_copier_reports_bytes_and_preserves_mtime(tmp_path: Path) -> None:
    source = tmp_path / "large.bin"
    destination = tmp_path / "copied.bin"
    source.write_bytes(b"a" * (9 * 1024 * 1024))
    os.utime(source, (1_700_000_000, 1_700_000_000))
    deltas: list[int] = []

    copied = ChunkedFileCopier().copy(source, destination, progress=deltas.append)

    assert copied == source.stat().st_size
    assert sum(deltas) == copied
    assert len(deltas) >= 2
    assert destination.read_bytes() == source.read_bytes()
    assert int(destination.stat().st_mtime) == int(source.stat().st_mtime)


def test_chunked_copy_cancel_removes_temporary_output(tmp_path: Path) -> None:
    source = tmp_path / "large.bin"
    destination = tmp_path / "copied.bin"
    source.write_bytes(b"x" * (10 * 1024 * 1024))
    cancelled = Event()

    with pytest.raises(CopyCancelled):
        ChunkedFileCopier().copy(
            source,
            destination,
            cancelled=cancelled,
            progress=lambda _delta: cancelled.set(),
        )

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".*.nivisviewer-*.tmp"))


def test_replace_failure_preserves_existing_destination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    original_replace = os.replace

    def fail_replace(old, new):
        if Path(new) == destination:
            raise PermissionError("locked")
        return original_replace(old, new)

    monkeypatch.setattr("app.chunked_file_copier.os.replace", fail_replace)
    with pytest.raises(PermissionError):
        ChunkedFileCopier().copy(source, destination, replace=True)
    assert destination.read_bytes() == b"old"
    assert not tuple(tmp_path.glob(".*.nivisviewer-*.tmp"))


def test_service_keep_both_and_replace(tmp_path: Path) -> None:
    source = tmp_path / "source" / "book.txt"
    destination = tmp_path / "destination"
    source.parent.mkdir()
    destination.mkdir()
    source.write_text("new", encoding="utf-8")
    (destination / "book.txt").write_text("old", encoding="utf-8")

    kept = FileOperationService().copy(
        [source],
        destination,
        collision_policy=FileCollisionPolicy.KEEP_BOTH,
    )
    assert kept.successes
    assert Path(kept.successes[0].destination_path or "").name == "book - コピー.txt"

    replaced = FileOperationService().copy(
        [source],
        destination,
        collision_policy=FileCollisionPolicy.REPLACE,
    )
    assert replaced.successes
    assert (destination / "book.txt").read_text(encoding="utf-8") == "new"


def test_service_folder_merge_does_not_delete_existing_children(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "folder"
    destination_root = tmp_path / "destination"
    destination = destination_root / "folder"
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "new.txt").write_text("new", encoding="utf-8")
    (destination / "existing.txt").write_text("old", encoding="utf-8")

    result = FileOperationService().copy(
        [source],
        destination_root,
        collision_policy=FileCollisionPolicy.MERGE,
    )

    assert result.successes
    assert (destination / "existing.txt").read_text(encoding="utf-8") == "old"
    assert (destination / "new.txt").read_text(encoding="utf-8") == "new"


def test_folder_merge_preflight_lists_child_conflicts_and_applies_policy(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = tmp_path / "source" / "folder"
    destination_root = tmp_path / "destination"
    destination = destination_root / "folder"
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "same.txt").write_text("new", encoding="utf-8")
    (destination / "same.txt").write_text("old", encoding="utf-8")
    queue = FileOperationQueue()
    plans = []
    completed = []
    queue.conflicts_required.connect(plans.append)
    queue.operation_completed.connect(completed.append)
    assert queue.enqueue(_request(1, source, destination_root))
    _wait_until(qapp, lambda: bool(plans))
    plan = plans[0]
    assert [conflict.kind for conflict in plan.conflicts] == [
        FileConflictKind.DIRECTORY_DIRECTORY,
        FileConflictKind.FILE_FILE,
    ]
    resolutions = {
        conflict.conflict_id: (
            ConflictResolution.MERGE
            if conflict.kind is FileConflictKind.DIRECTORY_DIRECTORY
            else ConflictResolution.REPLACE
        )
        for conflict in plan.conflicts
    }
    assert queue.resolve_conflicts(plan.operation_id, resolutions)
    _wait_until(qapp, lambda: bool(completed))
    assert (destination / "same.txt").read_text(encoding="utf-8") == "new"
    queue.close()


def test_cross_volume_copy_failure_leaves_move_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source" / "book.cbz"
    destination = tmp_path / "destination"
    source.parent.mkdir()
    destination.mkdir()
    source.write_bytes(b"source")
    original_rename = os.rename

    def exdev(old, new):
        if Path(old) == source:
            raise OSError(errno.EXDEV, "cross-volume")
        return original_rename(old, new)

    monkeypatch.setattr("app.file_operation_service.os.rename", exdev)
    monkeypatch.setattr(
        FileOperationService,
        "_copy_atomic",
        lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
    )
    result = FileOperationService().move([source], destination)
    assert result.failures
    assert source.exists()
    assert not (destination / source.name).exists()


def test_queue_preflights_and_runs_operations_serially(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("1", encoding="utf-8")
    second.write_text("2", encoding="utf-8")
    queue = FileOperationQueue()
    starts: list[int] = []
    completed = []
    queue.operation_started.connect(lambda request: starts.append(request.request_id))
    queue.operation_completed.connect(completed.append)

    assert queue.enqueue(_request(1, first, destination))
    assert queue.enqueue(_request(2, second, destination))
    _wait_until(qapp, lambda: len(completed) == 2)

    assert starts == [1, 2]
    assert [result.request_id for result in completed] == [1, 2]
    assert not queue.busy
    queue.close()


def test_queue_waits_for_conflict_resolution_then_replaces(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    source = tmp_path / "book.txt"
    source.write_text("new", encoding="utf-8")
    (destination / source.name).write_text("old", encoding="utf-8")
    queue = FileOperationQueue()
    plans = []
    completed = []
    queue.conflicts_required.connect(plans.append)
    queue.operation_completed.connect(completed.append)
    assert queue.enqueue(_request(1, source, destination))
    _wait_until(qapp, lambda: bool(plans))
    assert queue.active_state is FileOperationState.WAITING_FOR_CONFLICTS
    conflict = plans[0].conflicts[0]

    assert queue.resolve_conflicts(
        plans[0].operation_id,
        {conflict.conflict_id: ConflictResolution.REPLACE},
    )
    _wait_until(qapp, lambda: bool(completed))
    assert completed[0].successes
    assert (destination / source.name).read_text(encoding="utf-8") == "new"
    queue.close()


def test_queue_can_cancel_waiting_conflict_without_running(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    source = tmp_path / "book.txt"
    source.write_text("new", encoding="utf-8")
    (destination / source.name).write_text("old", encoding="utf-8")
    queue = FileOperationQueue()
    plans = []
    completed = []
    queue.conflicts_required.connect(plans.append)
    queue.operation_completed.connect(completed.append)
    queue.enqueue(_request(1, source, destination))
    _wait_until(qapp, lambda: bool(plans))

    assert queue.cancel(plans[0].operation_id)
    _wait_until(qapp, lambda: bool(completed))
    assert completed[0].cancelled
    assert (destination / source.name).read_text(encoding="utf-8") == "old"
    queue.close()


def test_conflict_dialog_defaults_to_skip_and_supports_same_kind(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    sources = []
    for name in ("a.txt", "b.txt"):
        source = tmp_path / name
        source.write_text("new", encoding="utf-8")
        (destination / name).write_text("old", encoding="utf-8")
        sources.append(str(source))
    plan = FileOperationPlanner().prepare(
        FileOperationRequest(
            1,
            FileOperationKind.COPY,
            tuple(sources),
            str(destination),
        )
    )
    dialog = ConflictResolutionDialog(plan)
    dialog.resize(480, 260)
    dialog.show()
    qapp.processEvents()

    assert dialog.model.rowCount() == 2
    assert set(dialog.resolutions.values()) == {ConflictResolution.SKIP}
    dialog.model.set_resolution(
        (0,),
        ConflictResolution.REPLACE,
        same_kind=True,
    )
    assert set(dialog.resolutions.values()) == {ConflictResolution.REPLACE}
    assert dialog.buttons.isVisible()
    dialog.close()


def test_destination_history_is_bounded_deduplicated_and_lexical(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    store = DestinationHistoryStore(config, limit=3)
    monkeypatch.setattr(
        Path,
        "exists",
        lambda _self: (_ for _ in ()).throw(AssertionError("must not stat")),
    )

    for value in ("A", "B", "C", "A", "D"):
        store.record(tmp_path / value)

    assert [Path(path).name for path in store.list()] == ["D", "A", "C"]


def test_planner_classifies_case_only_rename(tmp_path: Path) -> None:
    source = tmp_path / "Book.txt"
    source.write_text("book", encoding="utf-8")
    plan = FileOperationPlanner().prepare(
        FileOperationRequest(
            1,
            FileOperationKind.RENAME,
            (str(source),),
            new_name="book.txt",
        )
    )
    assert plan.conflicts[0].kind is FileConflictKind.CASE_ONLY_NAME
    assert plan.items[0].conflict_kind is FileConflictKind.CASE_ONLY_NAME


def test_type_mismatch_conflict_does_not_offer_replace(tmp_path: Path) -> None:
    source = tmp_path / "book"
    source.write_text("file", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "book").mkdir()
    plan = FileOperationPlanner().prepare(_request(1, source, destination))
    conflict = plan.conflicts[0]
    assert conflict.kind is FileConflictKind.FILE_DIRECTORY
    assert ConflictResolution.REPLACE not in conflict.allowed_resolutions


def test_same_folder_copy_can_keep_both_but_move_cannot(tmp_path: Path) -> None:
    source = tmp_path / "book.cbz"
    source.write_bytes(b"book")
    copy_plan = FileOperationPlanner().prepare(_request(1, source, tmp_path))
    assert ConflictResolution.KEEP_BOTH in (
        copy_plan.conflicts[0].allowed_resolutions
    )
    result = FileOperationService().copy(
        [source],
        tmp_path,
        collision_policy=FileCollisionPolicy.KEEP_BOTH,
    )
    assert result.successes
    assert (tmp_path / "book - コピー.cbz").exists()

    move_plan = FileOperationPlanner().prepare(
        _request(
            2,
            source,
            tmp_path,
            operation=FileOperationKind.MOVE,
        )
    )
    assert ConflictResolution.KEEP_BOTH not in (
        move_plan.conflicts[0].allowed_resolutions
    )


def test_planner_stops_before_copy_when_disk_space_is_clearly_insufficient(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"x" * 32)
    destination = tmp_path / "destination"
    destination.mkdir()
    usage = os.statvfs if hasattr(os, "statvfs") else None
    del usage
    monkeypatch.setattr(
        "app.file_operation_plan.shutil.disk_usage",
        lambda _path: type("Usage", (), {"free": 1})(),
    )
    plan = FileOperationPlanner().prepare(_request(1, source, destination))
    assert plan.state is FileOperationState.FAILED
    assert "空き容量" in plan.errors[0]


def test_cross_volume_source_delete_failure_is_partial_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source" / "book.cbz"
    destination = tmp_path / "destination"
    source.parent.mkdir()
    destination.mkdir()
    source.write_bytes(b"book")
    original_rename = os.rename

    def exdev(old, new):
        if Path(old) == source:
            raise OSError(errno.EXDEV, "cross-volume")
        return original_rename(old, new)

    monkeypatch.setattr("app.file_operation_service.os.rename", exdev)
    monkeypatch.setattr(
        FileOperationService,
        "_remove_source",
        staticmethod(lambda _path: (_ for _ in ()).throw(PermissionError("locked"))),
    )
    result = FileOperationService().move([source], destination)
    assert result.failures[0].partial_success
    assert result.failures[0].error_code == "partial_success"
    assert source.exists()
    assert (destination / source.name).exists()


def test_progress_has_byte_aliases_ewma_and_final_update(tmp_path: Path) -> None:
    source = tmp_path / "large.bin"
    destination = tmp_path / "destination"
    destination.mkdir()
    source.write_bytes(b"x" * (9 * 1024 * 1024))
    updates: list[FileOperationProgress] = []
    request = _request(1, source, destination)
    request = FileOperationRequest(
        **{
            **request.__dict__,
            "planned_total_bytes": source.stat().st_size,
            "planned_item_bytes": ((str(source), source.stat().st_size),),
        }
    )
    result = FileOperationService().execute(request, progress=updates.append)
    assert result.successes
    assert updates[-1].bytes_completed == source.stat().st_size
    assert updates[-1].current_file_bytes_completed == source.stat().st_size
    assert updates[-1].operation_kind == "copy"
    assert updates[-1].completed_items == 1
    assert updates[-1].bytes_per_second > 0
    assert len(updates) <= 4


def test_queue_rejects_competing_operation_for_same_source(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = tmp_path / "book.txt"
    source.write_text("book", encoding="utf-8")
    first_destination = tmp_path / "first"
    second_destination = tmp_path / "second"
    first_destination.mkdir()
    second_destination.mkdir()
    release = Event()

    class SlowPlanner(FileOperationPlanner):
        def prepare(self, request, *, cancelled=None):
            release.wait(2)
            return super().prepare(request, cancelled=cancelled)

    queue = FileOperationQueue(planner=SlowPlanner())
    assert queue.enqueue(_request(1, source, first_destination))
    assert not queue.enqueue(_request(2, source, second_destination))
    assert queue.has_path_conflict(_request(3, source, second_destination))
    release.set()
    assert queue.wait_for_done(3000)
    qapp.processEvents()
    queue.close()


def test_slow_preflight_keeps_qt_event_loop_responsive(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source = tmp_path / "book.txt"
    source.write_text("book", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    release = Event()

    class SlowPlanner(FileOperationPlanner):
        def prepare(self, request, *, cancelled=None):
            release.wait(2)
            return super().prepare(request, cancelled=cancelled)

    queue = FileOperationQueue(planner=SlowPlanner())
    started = monotonic()
    assert queue.enqueue(_request(1, source, destination))
    assert monotonic() - started < 0.2
    ticks: list[bool] = []
    QTimer.singleShot(0, lambda: ticks.append(True))
    qapp.processEvents()
    assert ticks == [True]
    release.set()
    assert queue.wait_for_done(3000)
    queue.close()


def test_file_operation_panel_keeps_partial_failure_details(
    qapp: QApplication,
) -> None:
    panel = FileOperationPanel()
    result = FileOperationResult(
        FileOperationKind.MOVE,
        (
            FileOperationItemResult(
                "C:\\元\\book.cbz",
                "D:\\先\\book.cbz",
                False,
                "partial_success",
                "元を削除できません",
                True,
            ),
        ),
    )
    panel.show_result(result)
    panel.show()
    qapp.processEvents()
    assert panel.isVisible()
    assert panel.details_button.isVisible()
    assert "元を削除できません" in panel.details_view.toPlainText()
    panel.close()
