from __future__ import annotations

import os
import errno
from pathlib import Path
from threading import Event

import pytest

from app.file_operation_service import (
    FileCollisionPolicy,
    FileOperationErrorCode,
    FileOperationService,
)
from app.windows_recycle_bin import RecycleBinResult


class SuccessfulRecycleBin:
    def recycle(self, _path) -> RecycleBinResult:
        return RecycleBinResult(True)


def test_rename_file_and_folder_preserves_content(tmp_path: Path) -> None:
    service = FileOperationService(SuccessfulRecycleBin())
    source_file = tmp_path / "元.txt"
    source_file.write_bytes(b"content")
    source_folder = tmp_path / "旧"
    source_folder.mkdir()

    file_result = service.rename(source_file, "新.txt")
    folder_result = service.rename(source_folder, "新しい")

    assert file_result.successes[0].destination_path == str(tmp_path / "新.txt")
    assert (tmp_path / "新.txt").read_bytes() == b"content"
    assert folder_result.successes[0].destination_path == str(tmp_path / "新しい")
    assert not source_file.exists()
    assert not source_folder.exists()


def test_case_only_rename_is_safe(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("same", encoding="utf-8")

    result = FileOperationService().rename(source, "BOOK.txt")

    assert len(result.successes) == 1
    assert (tmp_path / "BOOK.txt").read_text(encoding="utf-8") == "same"


def test_rename_collision_never_overwrites(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "target.txt"
    source.write_text("source", encoding="utf-8")
    destination.write_text("target", encoding="utf-8")

    result = FileOperationService().rename(source, destination.name)

    assert result.failures[0].error_code == FileOperationErrorCode.COLLISION.value
    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "target"


def test_copy_file_preserves_content_and_mtime(tmp_path: Path) -> None:
    source_folder = tmp_path / "source"
    destination_folder = tmp_path / "destination"
    source_folder.mkdir()
    destination_folder.mkdir()
    source = source_folder / "日本語.txt"
    source.write_bytes(b"payload")
    timestamp_ns = 1_700_000_000_123_456_700
    os.utime(source, ns=(timestamp_ns, timestamp_ns))

    result = FileOperationService().copy([source], destination_folder)
    copied = destination_folder / source.name

    assert len(result.successes) == 1
    assert copied.read_bytes() == b"payload"
    assert copied.stat().st_mtime_ns == source.stat().st_mtime_ns
    assert source.read_bytes() == b"payload"


def test_copy_folder_and_multiple_items(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    folder = source / "本"
    folder.mkdir(parents=True)
    (folder / "1.txt").write_text("one", encoding="utf-8")
    standalone = source / "2.txt"
    standalone.write_text("two", encoding="utf-8")
    destination.mkdir()

    result = FileOperationService().copy([folder, standalone], destination)

    assert len(result.successes) == 2
    assert (destination / "本" / "1.txt").read_text(encoding="utf-8") == "one"
    assert (destination / "2.txt").read_text(encoding="utf-8") == "two"


def test_move_same_volume_and_same_path_rejection(tmp_path: Path) -> None:
    source_folder = tmp_path / "source"
    destination = tmp_path / "destination"
    source_folder.mkdir()
    destination.mkdir()
    source = source_folder / "book.cbz"
    source.write_bytes(b"book")
    service = FileOperationService()

    moved = service.move([source], destination)
    same = service.move([destination / "book.cbz"], destination)

    assert len(moved.successes) == 1
    assert not source.exists()
    assert (destination / "book.cbz").read_bytes() == b"book"
    assert same.failures[0].error_code == FileOperationErrorCode.SAME_PATH.value


@pytest.mark.parametrize("operation", ["copy", "move"])
def test_folder_cannot_be_transferred_into_its_descendant(
    tmp_path: Path,
    operation: str,
) -> None:
    source = tmp_path / "source"
    child = source / "child"
    child.mkdir(parents=True)
    service = FileOperationService()

    result = getattr(service, operation)([source], child)

    assert (
        result.failures[0].error_code
        == FileOperationErrorCode.DESCENDANT_DESTINATION.value
    )
    assert source.exists()


def test_duplicate_and_parent_child_sources_are_processed_once(tmp_path: Path) -> None:
    source = tmp_path / "source"
    folder = source / "folder"
    child = folder / "child.txt"
    child.parent.mkdir(parents=True)
    child.write_text("child", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()

    result = FileOperationService().copy(
        [folder, child, folder, Path(str(folder).upper())],
        destination,
    )

    assert len(result.items) == 1
    assert (destination / "folder" / "child.txt").exists()


def test_missing_and_permission_failure_are_item_scoped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    good = tmp_path / "good.txt"
    blocked = tmp_path / "blocked.txt"
    good.write_text("good", encoding="utf-8")
    blocked.write_text("blocked", encoding="utf-8")
    original_copy2 = __import__("shutil").copy2

    def copy2(source, target, *args, **kwargs):
        if os.path.normcase(os.fspath(source)) == os.path.normcase(str(blocked)):
            raise PermissionError("denied")
        return original_copy2(source, target, *args, **kwargs)

    monkeypatch.setattr("app.file_operation_service.shutil.copy2", copy2)

    result = FileOperationService().copy(
        [tmp_path / "missing.txt", blocked, good],
        destination,
    )

    by_name = {
        Path(item.source_path or "").name: item
        for item in result.items
    }
    assert not by_name["missing.txt"].success
    assert by_name["missing.txt"].error_code == FileOperationErrorCode.NOT_FOUND.value
    assert not by_name["blocked.txt"].success
    assert (
        by_name["blocked.txt"].error_code
        == FileOperationErrorCode.ACCESS_DENIED.value
    )
    assert by_name["good.txt"].success
    assert (destination / "good.txt").exists()


def test_collision_can_generate_copy_name_without_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "book.zip").write_bytes(b"new")
    (destination / "book.zip").write_bytes(b"old")

    result = FileOperationService().copy(
        [source / "book.zip"],
        destination,
        collision_policy=FileCollisionPolicy.RENAME,
    )

    assert (destination / "book.zip").read_bytes() == b"old"
    assert (destination / "book - コピー.zip").read_bytes() == b"new"
    assert len(result.successes) == 1


def test_cancel_between_items_reports_completed_items(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    first = source / "1.txt"
    second = source / "2.txt"
    first.write_text("1", encoding="utf-8")
    second.write_text("2", encoding="utf-8")
    cancelled = Event()

    def progress(item) -> None:
        if item.completed == 1:
            cancelled.set()

    result = FileOperationService().copy(
        [first, second],
        destination,
        cancelled=cancelled,
        progress=progress,
    )

    assert result.cancelled
    assert len(result.successes) == 1
    assert (destination / "1.txt").exists()
    assert not (destination / "2.txt").exists()


def test_cross_volume_move_cancel_rolls_back_completed_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_folder = tmp_path / "source"
    destination_folder = tmp_path / "destination"
    source_folder.mkdir()
    destination_folder.mkdir()
    source = source_folder / "book.cbz"
    destination = destination_folder / source.name
    source.write_bytes(b"source")
    cancelled = Event()
    service = FileOperationService()
    original_rename = os.rename

    def cross_volume_rename(old, new):
        if os.path.normcase(os.fspath(old)) == os.path.normcase(str(source)):
            raise OSError(errno.EXDEV, "cross-volume")
        return original_rename(old, new)

    def completed_copy(_source, target, _cancelled):
        Path(target).write_bytes(b"copy")
        cancelled.set()

    monkeypatch.setattr("app.file_operation_service.os.rename", cross_volume_rename)
    monkeypatch.setattr(service, "_copy_atomic", completed_copy)

    result = service.move(
        [source],
        destination_folder,
        cancelled=cancelled,
    )

    assert result.cancelled
    assert source.exists()
    assert not destination.exists()


def test_create_directory_and_invalid_destination_are_safe(tmp_path: Path) -> None:
    service = FileOperationService()

    created = service.create_directory(tmp_path, "新しいフォルダ")
    collision = service.create_directory(tmp_path, "新しいフォルダ")
    invalid = service.create_directory(tmp_path / "missing", "folder")

    assert len(created.successes) == 1
    assert (tmp_path / "新しいフォルダ").is_dir()
    assert collision.failures[0].error_code == FileOperationErrorCode.COLLISION.value
    assert (
        invalid.failures[0].error_code
        == FileOperationErrorCode.INVALID_DESTINATION.value
    )


def _inject_exdev_for_pair(monkeypatch, old_path: Path, new_path: Path) -> None:
    original_rename = os.rename
    old_key = os.path.normcase(os.path.abspath(old_path))
    new_key = os.path.normcase(os.path.abspath(new_path))

    def rename(old, new, *args, **kwargs):
        if (
            os.path.normcase(os.path.abspath(os.fspath(old))) == old_key
            and os.path.normcase(os.path.abspath(os.fspath(new))) == new_key
        ):
            raise OSError(errno.EXDEV, "injected cross-volume move")
        return original_rename(old, new, *args, **kwargs)

    monkeypatch.setattr("app.file_operation_service.os.rename", rename)


def test_cross_volume_move_preserves_file_added_after_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source.mkdir()
    destination_root.mkdir()
    (source / "copied.txt").write_text("copied", encoding="utf-8")
    destination = destination_root / source.name
    _inject_exdev_for_pair(monkeypatch, source, destination)
    service = FileOperationService()
    original_copy = service._copy_atomic

    def copy_then_add(src, dst, cancelled, **kwargs):
        published = original_copy(src, dst, cancelled, **kwargs)
        (Path(src) / "late.txt").write_text("preserve me", encoding="utf-8")
        return published

    monkeypatch.setattr(service, "_copy_atomic", copy_then_add)
    result = service.move([source], destination_root)

    item = result.items[0]
    assert destination.joinpath("copied.txt").read_text(encoding="utf-8") == "copied"
    assert not (destination / "late.txt").exists()
    assert (source / "late.txt").read_text(encoding="utf-8") == "preserve me"
    assert not item.success and item.partial_success
    assert item.destination_published and item.source_exists_after


def test_cross_volume_move_does_not_delete_replaced_source_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    moved_original = tmp_path / "original-source"
    destination_root = tmp_path / "destination"
    source.mkdir()
    destination_root.mkdir()
    (source / "old.txt").write_text("old", encoding="utf-8")
    destination = destination_root / source.name
    _inject_exdev_for_pair(monkeypatch, source, destination)
    service = FileOperationService()
    original_copy = service._copy_atomic
    original_rename = os.rename

    def copy_then_replace_root(src, dst, cancelled, **kwargs):
        published = original_copy(src, dst, cancelled, **kwargs)
        original_rename(src, moved_original)
        Path(src).mkdir()
        (Path(src) / "replacement.txt").write_text("keep", encoding="utf-8")
        return published

    monkeypatch.setattr(service, "_copy_atomic", copy_then_replace_root)
    result = service.move([source], destination_root)

    assert (moved_original / "old.txt").read_text(encoding="utf-8") == "old"
    assert (source / "replacement.txt").read_text(encoding="utf-8") == "keep"
    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"
    assert not result.items[0].success


def test_cross_volume_replace_cancel_after_publish_reports_partial_move(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    destination_root = tmp_path / "destination"
    destination_root.mkdir()
    destination = destination_root / source.name
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    cancelled = Event()
    service = FileOperationService()
    original_replace = os.replace

    def replace_with_exdev(old, new, *args, **kwargs):
        if os.path.abspath(os.fspath(old)) == os.path.abspath(source) and os.path.abspath(os.fspath(new)) == os.path.abspath(destination):
            raise OSError(errno.EXDEV, "injected cross-volume replace")
        return original_replace(old, new, *args, **kwargs)

    original_copy = service._copy_atomic_replace

    def publish_then_cancel(src, dst, token):
        original_copy(src, dst, token)
        token.set()

    monkeypatch.setattr("app.file_operation_service.os.replace", replace_with_exdev)
    monkeypatch.setattr(service, "_copy_atomic_replace", publish_then_cancel)
    result = service.move(
        [source], destination_root,
        collision_policy=FileCollisionPolicy.REPLACE,
        cancelled=cancelled,
    )

    item = result.items[0]
    assert result.cancelled
    assert source.read_text(encoding="utf-8") == "new"
    assert destination.read_text(encoding="utf-8") == "new"
    assert item.destination_published and item.replaced_existing
    assert item.source_exists_after and item.partial_success


def test_replace_cancel_before_publish_preserves_both_original_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    destination_root = tmp_path / "destination"
    destination_root.mkdir()
    destination = destination_root / source.name
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    cancelled = Event()
    cancelled.set()

    result = FileOperationService().move(
        [source], destination_root,
        collision_policy=FileCollisionPolicy.REPLACE,
        cancelled=cancelled,
    )

    assert result.cancelled
    assert source.read_text(encoding="utf-8") == "new"
    assert destination.read_text(encoding="utf-8") == "old"
    assert not result.items


def test_cross_volume_cancel_during_source_cleanup_retains_remaining_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source.mkdir()
    destination_root.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    (source / "b.txt").write_text("b", encoding="utf-8")
    destination = destination_root / source.name
    _inject_exdev_for_pair(monkeypatch, source, destination)
    service = FileOperationService()
    cancelled = Event()

    def stop_cleanup(src, _receipt, token):
        (Path(src) / "a.txt").unlink()
        token.set()
        raise OSError(errno.ECANCELED, "injected cleanup cancellation")

    monkeypatch.setattr(service, "_remove_source_receipt", stop_cleanup)
    result = service.move([source], destination_root, cancelled=cancelled)

    item = result.items[0]
    assert result.cancelled
    assert (destination / "a.txt").read_text(encoding="utf-8") == "a"
    assert (destination / "b.txt").read_text(encoding="utf-8") == "b"
    assert not (source / "a.txt").exists()
    assert (source / "b.txt").read_text(encoding="utf-8") == "b"
    assert item.destination_published and item.partial_success


def test_cross_volume_replace_cleanup_failure_preserves_published_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    destination_root = tmp_path / "destination"
    destination_root.mkdir()
    destination = destination_root / source.name
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    service = FileOperationService()
    original_replace = os.replace

    def replace_with_exdev(old, new, *args, **kwargs):
        if os.path.abspath(os.fspath(old)) == os.path.abspath(source) and os.path.abspath(os.fspath(new)) == os.path.abspath(destination):
            raise OSError(errno.EXDEV, "injected cross-volume replace")
        return original_replace(old, new, *args, **kwargs)

    def fail_cleanup(_src, _receipt, _cancelled):
        raise PermissionError("injected source cleanup failure")

    monkeypatch.setattr("app.file_operation_service.os.replace", replace_with_exdev)
    monkeypatch.setattr(service, "_remove_source_receipt", fail_cleanup)
    result = service.move(
        [source], destination_root,
        collision_policy=FileCollisionPolicy.REPLACE,
    )

    item = result.items[0]
    assert not result.cancelled
    assert source.read_text(encoding="utf-8") == "new"
    assert destination.read_text(encoding="utf-8") == "new"
    assert item.destination_published and item.replaced_existing
    assert item.source_exists_after and item.partial_success


def test_descendant_guard_resolves_destination_aliases_on_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.file_operation_plan import FileOperationPlanner

    source = tmp_path / "source"
    child = source / "child"
    alias = tmp_path / "outside-alias"
    source.mkdir()
    child.mkdir()
    original_realpath = os.path.realpath

    def resolve(path, *args, **kwargs):
        path_text = os.path.abspath(os.fspath(path))
        if os.path.normcase(path_text) == os.path.normcase(str(alias)):
            return str(child)
        return original_realpath(path, *args, **kwargs)

    monkeypatch.setattr("app.file_operation_service.os.path.realpath", resolve)
    assert FileOperationService._is_descendant(str(alias), str(source))
    assert FileOperationPlanner._is_descendant(str(alias), str(source))
