from __future__ import annotations

import errno
import hashlib
import logging
import os
import stat
import uuid
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QStyleOptionViewItem

from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_scanner import BrowserScanRequest, scan_directory
from app.browser_visibility import BrowserVisibilityPolicy
from app.chunked_file_copier import ChunkedFileCopier
from app.drag_drop import build_path_mime_data, normalize_local_paths, paths_from_mime_data
from app.file_conflict_dialog import (
    ConflictPresentationModel,
    ConflictResolutionDialog,
)
from app.file_operation_artifact import (
    ArtifactCleanupResult,
    FileOperationArtifactPolicy,
    OrphanArtifactScanner,
)
from app.file_operation_plan import (
    ConflictResolution,
    FileOperationPlanner,
    FileOperationState,
)
from app.file_operation_service import (
    FileCollisionPolicy,
    FileOperationErrorCode,
    FileOperationItemState,
    FileOperationKind,
    FileOperationRequest,
    FileOperationService,
)
from app.internal_clipboard import (
    InternalClipboardOperation,
    InternalClipboardState,
)
from app.system_file_opener import SystemFileOpener, SystemOpenStatus


def _artifact_name(final_name: str = "image.png") -> str:
    return (
        f".{final_name}.nivisviewer-"
        f"{uuid.uuid4().hex}-{uuid.uuid4().hex}.tmp"
    )


def _nested_legacy_name(final_name: str = "image.png") -> str:
    first = uuid.uuid4().hex
    second = uuid.uuid4().hex
    return (
        f"..{final_name}.nivisviewer-{first}.tmp"
        f".nivisviewer-{second}.tmp"
    )


@pytest.mark.parametrize(
    ("name", "is_artifact"),
    [
        (_artifact_name(), True),
        (_nested_legacy_name(), True),
        (f".nivisviewer-{uuid.uuid4().hex}.tmp", True),
        (".nivisviewer-short.tmp", False),
        (".image.png.nivisviewer-not valid-item.tmp", False),
        (".image.png.nivisviewer-missingitem.tmp", False),
        ("ordinary.tmp", False),
        ("README", False),
        (".gitignore", False),
    ],
)
def test_artifact_detection_is_strict(name: str, is_artifact: bool) -> None:
    assert (
        FileOperationArtifactPolicy.is_internal_operation_artifact(name)
        is is_artifact
    )


def test_canonical_staging_is_derived_once_from_final(tmp_path: Path) -> None:
    final = tmp_path / "日本語 image.png"
    staging = Path(
        FileOperationArtifactPolicy.create_staging_path(
            final,
            "operation1",
            "item1",
        )
    )
    repeated = Path(
        FileOperationArtifactPolicy.create_staging_path(
            staging,
            "operation2",
            "item2",
        )
    )

    assert staging.name == ".日本語 image.png.nivisviewer-operation1-item1.tmp"
    assert repeated.name == ".日本語 image.png.nivisviewer-operation2-item2.tmp"
    assert ".tmp.nivisviewer-" not in repeated.name
    assert FileOperationArtifactPolicy.derive_final_destination(staging) == str(final)


def test_nested_legacy_artifact_derives_original_final(tmp_path: Path) -> None:
    nested = tmp_path / _nested_legacy_name("cover.png")
    nested.write_bytes(b"recoverable")

    description = FileOperationArtifactPolicy.describe_artifact(nested)

    assert description is not None
    assert description.nested
    assert description.final_destination == str(tmp_path / "cover.png")
    assert description.size == len(b"recoverable")
    assert not description.final_destination_exists


def test_orphan_scanner_is_read_only(tmp_path: Path) -> None:
    orphan = tmp_path / _artifact_name("book.cbz")
    orphan.write_bytes(b"unpublished")

    descriptions = OrphanArtifactScanner().scan(tmp_path)

    assert [item.path for item in descriptions] == [str(orphan)]
    assert orphan.read_bytes() == b"unpublished"


def test_chunked_copy_to_staging_does_not_create_second_artifact(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(os.urandom(300_000))
    final = tmp_path / "final.bin"
    staging = Path(
        FileOperationArtifactPolicy.create_staging_path(final, "op", "item")
    )

    copied = ChunkedFileCopier(chunk_size=64 * 1024).copy_to_staging(
        source,
        staging,
    )

    assert copied == source.stat().st_size
    assert staging.read_bytes() == source.read_bytes()
    assert FileOperationArtifactPolicy.find_orphans(tmp_path) == (str(staging),)
    assert ".tmp.nivisviewer-" not in staging.name


def test_cross_volume_move_ten_round_trips_has_no_artifacts_and_same_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "A"
    second = tmp_path / "B"
    first.mkdir()
    second.mkdir()
    source = first / "image.png"
    payload = os.urandom(900_000)
    source.write_bytes(payload)
    expected_hash = hashlib.sha256(payload).hexdigest()
    original_rename = os.rename

    def cross_volume_rename(old, new):
        if Path(old).name == "image.png" and Path(new).name == "image.png":
            raise OSError(errno.EXDEV, "cross volume")
        return original_rename(old, new)

    monkeypatch.setattr("app.file_operation_service.os.rename", cross_volume_rename)
    service = FileOperationService()
    current = source
    for iteration in range(10):
        destination_folder = second if current.parent == first else first
        result = service.move([current], destination_folder)
        destination = destination_folder / "image.png"
        assert result.items[0].state is FileOperationItemState.MOVED
        assert not current.exists()
        assert destination.exists()
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == expected_hash
        assert FileOperationArtifactPolicy.find_orphans(first) == ()
        assert FileOperationArtifactPolicy.find_orphans(second) == ()
        current = destination


def test_publish_failure_keeps_source_and_cleans_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_folder = tmp_path / "source"
    destination_folder = tmp_path / "destination"
    source_folder.mkdir()
    destination_folder.mkdir()
    source = source_folder / "image.png"
    source.write_bytes(b"source")

    def fail_publish(_old, _new):
        raise PermissionError("publish locked")

    monkeypatch.setattr("app.file_operation_service.os.rename", fail_publish)
    result = FileOperationService().copy([source], destination_folder)

    assert not result.items[0].success
    assert source.read_bytes() == b"source"
    assert not (destination_folder / source.name).exists()
    assert FileOperationArtifactPolicy.find_orphans(destination_folder) == ()


def test_cleanup_failure_is_structured_and_source_is_not_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_folder = tmp_path / "source"
    destination_folder = tmp_path / "destination"
    source_folder.mkdir()
    destination_folder.mkdir()
    source = source_folder / "image.png"
    source.write_bytes(b"source")

    def fail_publish_after_cross_volume(old, _new):
        if Path(old) == source:
            raise OSError(errno.EXDEV, "cross volume")
        raise PermissionError("publish locked")

    monkeypatch.setattr(
        "app.file_operation_service.os.rename", fail_publish_after_cross_volume,
    )

    def cleanup_failed(path):
        return ArtifactCleanupResult(str(path), False, "cleanup locked")

    monkeypatch.setattr(
        FileOperationArtifactPolicy,
        "cleanup_staging_path",
        staticmethod(cleanup_failed),
    )
    result = FileOperationService().move([source], destination_folder)
    item = result.items[0]

    assert not item.success
    assert item.error_code == FileOperationErrorCode.ARTIFACT_CLEANUP_FAILED.value
    assert item.artifact_paths
    assert item.cleanup_errors == ("cleanup locked",)
    assert source.exists()
    assert not (destination_folder / source.name).exists()


def test_directory_replace_publish_failure_restores_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source" / "book"
    destination_root = tmp_path / "destination"
    destination = destination_root / source.name
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "new.txt").write_text("new", encoding="utf-8")
    (destination / "old.txt").write_text("old", encoding="utf-8")
    original_replace = os.replace
    backup_paths: list[Path] = []

    def fail_publish(old, new):
        old_path = Path(old)
        new_path = Path(new)
        if old_path == destination:
            backup_paths.append(new_path)
            return original_replace(old, new)
        if new_path == destination and old_path != backup_paths[0]:
            raise PermissionError("publish locked")
        return original_replace(old, new)

    monkeypatch.setattr("app.file_operation_service.os.replace", fail_publish)

    result = FileOperationService().copy(
        [source],
        destination_root,
        collision_policy=FileCollisionPolicy.REPLACE,
    )
    item = result.items[0]

    assert not item.success
    assert source.exists()
    assert destination.exists()
    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"
    assert not backup_paths[0].exists()
    assert item.artifact_paths == ()
    assert item.cleanup_errors == ()
    assert FileOperationArtifactPolicy.find_orphans(destination_root) == ()


def test_directory_replace_publish_and_rollback_failure_reports_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source" / "book"
    destination_root = tmp_path / "destination"
    destination = destination_root / source.name
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "new.txt").write_text("new", encoding="utf-8")
    (destination / "old.txt").write_text("old", encoding="utf-8")
    original_replace = os.replace
    backup_paths: list[Path] = []
    rollback_attempts = 0

    def fail_publish_and_rollback(old, new):
        nonlocal rollback_attempts
        old_path = Path(old)
        new_path = Path(new)
        if old_path == destination:
            backup_paths.append(new_path)
            return original_replace(old, new)
        if new_path == destination:
            if backup_paths and old_path == backup_paths[0]:
                rollback_attempts += 1
                raise PermissionError("rollback locked")
            raise PermissionError("publish locked")
        return original_replace(old, new)

    monkeypatch.setattr(
        "app.file_operation_service.os.replace",
        fail_publish_and_rollback,
    )

    result = FileOperationService().move(
        [source],
        destination_root,
        collision_policy=FileCollisionPolicy.REPLACE,
    )
    item = result.items[0]
    backup = backup_paths[0]

    assert not item.success
    assert item.error_code == FileOperationErrorCode.ARTIFACT_CLEANUP_FAILED.value
    assert source.exists()
    assert (source / "new.txt").read_text(encoding="utf-8") == "new"
    assert not destination.exists()
    assert backup.exists()
    assert (backup / "old.txt").read_text(encoding="utf-8") == "old"
    assert item.source_exists_after is True
    assert item.destination_exists_after is False
    assert item.destination_published is False
    assert item.artifact_paths == (str(backup),)
    assert item.cleanup_errors == (
        "backup復元に失敗しました: rollback locked",
    )
    assert rollback_attempts == 2
    assert FileOperationArtifactPolicy.find_orphans(destination_root) == (
        str(backup),
    )


def test_directory_replace_triple_failure_reports_every_remaining_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source" / "book"
    destination_root = tmp_path / "destination"
    destination = destination_root / source.name
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "new.txt").write_text("new", encoding="utf-8")
    (destination / "old.txt").write_text("old", encoding="utf-8")
    original_replace = os.replace
    original_cleanup = FileOperationArtifactPolicy.cleanup_staging_path
    backup_paths: list[Path] = []
    staging_paths: list[Path] = []

    def fail_publish_and_rollback(old, new):
        old_path = Path(old)
        new_path = Path(new)
        if old_path == destination:
            backup_paths.append(new_path)
            return original_replace(old, new)
        if new_path == destination:
            if backup_paths and old_path == backup_paths[0]:
                raise PermissionError("rollback locked")
            staging_paths.append(old_path)
            raise PermissionError("publish locked")
        return original_replace(old, new)

    def fail_staging_cleanup(path):
        target = Path(path)
        if staging_paths and target == staging_paths[0]:
            return ArtifactCleanupResult(
                str(target),
                False,
                "staging cleanup locked",
            )
        return original_cleanup(path)

    monkeypatch.setattr(
        "app.file_operation_service.os.replace",
        fail_publish_and_rollback,
    )
    monkeypatch.setattr(
        FileOperationArtifactPolicy,
        "cleanup_staging_path",
        staticmethod(fail_staging_cleanup),
    )

    item = FileOperationService().move(
        [source],
        destination_root,
        collision_policy=FileCollisionPolicy.REPLACE,
    ).items[0]
    backup = backup_paths[0]
    staging = staging_paths[0]

    assert not item.success
    assert source.exists()
    assert not destination.exists()
    assert backup.exists()
    assert staging.exists()
    assert item.destination_exists_after is False
    assert item.destination_published is False
    assert item.artifact_paths == (str(backup), str(staging))
    assert item.cleanup_errors == (
        "backup復元に失敗しました: rollback locked",
        "staging cleanup locked",
    )


def test_cancel_during_copy_removes_staging_and_keeps_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_folder = tmp_path / "source"
    destination_folder = tmp_path / "destination"
    source_folder.mkdir()
    destination_folder.mkdir()
    source = source_folder / "large.bin"
    source.write_bytes(b"x" * (9 * 1024 * 1024))
    cancelled = Event()
    original_rename = os.rename

    def cross_volume_rename(old, new):
        if Path(old) == source:
            raise OSError(errno.EXDEV, "cross volume")
        return original_rename(old, new)

    monkeypatch.setattr("app.file_operation_service.os.rename", cross_volume_rename)
    result = FileOperationService().move(
        [source],
        destination_folder,
        cancelled=cancelled,
        progress=lambda progress: (
            cancelled.set() if progress.bytes_completed > 0 else None
        ),
    )

    assert result.cancelled
    assert source.exists()
    assert not (destination_folder / source.name).exists()
    assert FileOperationArtifactPolicy.find_orphans(destination_folder) == ()


def test_cancel_after_publish_keeps_final_and_source_as_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_folder = tmp_path / "source"
    destination_folder = tmp_path / "destination"
    source_folder.mkdir()
    destination_folder.mkdir()
    source = source_folder / "image.png"
    source.write_bytes(b"source")
    cancelled = Event()
    original_rename = os.rename

    def publish_then_cancel(old, new):
        if Path(old) == source:
            raise OSError(errno.EXDEV, "cross volume")
        result = original_rename(old, new)
        cancelled.set()
        return result

    monkeypatch.setattr("app.file_operation_service.os.rename", publish_then_cancel)
    result = FileOperationService().move(
        [source],
        destination_folder,
        cancelled=cancelled,
    )
    item = result.items[0]

    assert not item.success
    assert item.partial_success
    assert item.destination_published
    assert source.exists()
    assert (destination_folder / source.name).read_bytes() == b"source"
    assert FileOperationArtifactPolicy.find_orphans(destination_folder) == ()


@pytest.mark.parametrize("artifact_name", [_artifact_name(), f".nivisviewer-{uuid.uuid4().hex}.tmp"])
def test_planner_and_service_reject_internal_artifact_source(
    tmp_path: Path,
    artifact_name: str,
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    artifact = tmp_path / artifact_name
    artifact.write_bytes(b"recoverable")
    request = FileOperationRequest(
        1,
        FileOperationKind.MOVE,
        (str(artifact),),
        str(destination),
    )

    plan = FileOperationPlanner().prepare(request)
    result = FileOperationService().execute(request)

    assert plan.state is FileOperationState.FAILED
    assert "INTERNAL_STAGING_ARTIFACT" in plan.errors[0]
    assert (
        result.items[0].error_code
        == FileOperationErrorCode.INTERNAL_STAGING_ARTIFACT.value
    )
    assert artifact.exists()
    assert not tuple(destination.iterdir())


@pytest.mark.parametrize("artifact_name", [_artifact_name(), f".nivisviewer-{uuid.uuid4().hex}.tmp"])
def test_artifact_never_enters_clipboard_drag_or_browser_model(
    tmp_path: Path,
    artifact_name: str,
) -> None:
    artifact = tmp_path / artifact_name
    normal = tmp_path / "README"
    artifact.write_bytes(b"artifact")
    normal.write_bytes(b"normal")

    state = InternalClipboardState()
    snapshot = state.replace(
        (str(artifact), str(normal)),
        InternalClipboardOperation.CUT,
    )
    mime = build_path_mime_data((str(artifact), str(normal)))
    model = BrowserItemModel()
    model.set_items(
        [
            BrowserItem(artifact.name, artifact, BrowserItemKind.OTHER, None),
            BrowserItem(normal.name, normal, BrowserItemKind.OTHER, None),
        ]
    )

    assert snapshot.paths == (str(normal),)
    assert normalize_local_paths((str(artifact), str(normal))) == (str(normal),)
    assert paths_from_mime_data(mime) == (str(normal),)
    assert model.rowCount(QModelIndex()) == 1
    assert model.item_at(0).path == normal


@pytest.mark.parametrize(
    "name",
    [
        "Note",
        "README",
        "LICENSE",
        "日本語名",
        "space name",
        "zero-byte",
        "binary",
        "ordinary.tmp",
    ],
)
def test_extensionless_and_normal_tmp_files_are_generic_browser_items(
    tmp_path: Path,
    name: str,
) -> None:
    folder = tmp_path / "files"
    folder.mkdir()
    path = folder / name
    path.write_bytes(b"" if name == "zero-byte" else b"\x00data")
    batches = []

    result = scan_directory(
        BrowserScanRequest(
            str(folder),
            1,
            visibility_policy=BrowserVisibilityPolicy(
                show_hidden_items=True,
                show_unsupported_files=True,
                show_system_items=True,
            ),
        ),
        Event(),
        batches.append,
    )
    entries = [entry for batch in batches for entry in batch.entries]

    assert result.total_count == 1
    assert entries[0].display_name == name
    assert entries[0].item_kind == "other"
    assert not entries[0].openable_by_nivisviewer
    if name.endswith(".tmp"):
        assert entries[0].preview_kind == ""
        assert not entries[0].can_generate_preview
    else:
        assert entries[0].preview_kind == "windows_shell"


def test_internal_artifacts_are_hidden_with_all_visibility_flags(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "files"
    folder.mkdir()
    artifact = folder / _artifact_name()
    nested = folder / _nested_legacy_name()
    compact = folder / f".nivisviewer-{uuid.uuid4().hex}.tmp"
    ordinary_tmp = folder / "ordinary.tmp"
    artifact.write_bytes(b"artifact")
    nested.write_bytes(b"nested")
    compact.write_bytes(b"compact")
    ordinary_tmp.write_bytes(b"normal")
    batches = []

    result = scan_directory(
        BrowserScanRequest(
            str(folder),
            1,
            visibility_policy=BrowserVisibilityPolicy(
                show_hidden_items=True,
                show_unsupported_files=True,
                show_system_items=True,
            ),
        ),
        Event(),
        batches.append,
    )
    names = {
        entry.display_name
        for batch in batches
        for entry in batch.entries
    }

    assert result.total_count == 1
    assert names == {"ordinary.tmp"}
    assert artifact.exists()
    assert nested.exists()


class _RecordingOpenAdapter:
    def __init__(self, default_result: int = 33, picker_result: int = 0) -> None:
        self.default_result = default_result
        self.picker_result = picker_result
        self.calls: list[tuple[str, str]] = []

    def open_default(self, path: str, _parent_hwnd: int | None) -> int:
        self.calls.append(("default", path))
        return self.default_result

    def open_picker(self, path: str, _parent_hwnd: int | None) -> int:
        self.calls.append(("picker", path))
        return self.picker_result


@pytest.mark.parametrize("default_result", [33, 31])
def test_extensionless_system_open_uses_default_or_picker(
    tmp_path: Path,
    default_result: int,
) -> None:
    path = tmp_path / "README"
    path.write_text("read me", encoding="utf-8")
    adapter = _RecordingOpenAdapter(default_result=default_result)

    result = SystemFileOpener(adapter).open_with_default_application(path)

    assert result.status is (
        SystemOpenStatus.OPENED
        if default_result == 33
        else SystemOpenStatus.PICKER_OPENED
    )
    assert adapter.calls[0] == ("default", str(path.absolute()))
    if default_result == 31:
        assert adapter.calls[1] == ("picker", str(path.absolute()))


def test_system_open_rejects_internal_artifact_without_shell_call(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / _artifact_name()
    artifact.write_bytes(b"data")
    adapter = _RecordingOpenAdapter()

    result = SystemFileOpener(adapter).open_with_default_application(artifact)

    assert result.status is SystemOpenStatus.FAILED
    assert not adapter.calls


def test_conflict_presentation_contains_both_paths_and_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "コピー元 日本語" / "README"
    destination_folder = tmp_path / "コピー先 with spaces"
    source.parent.mkdir()
    destination_folder.mkdir()
    source.write_bytes(b"new-data")
    destination = destination_folder / source.name
    destination.write_bytes(b"old")
    plan = FileOperationPlanner().prepare(
        FileOperationRequest(
            1,
            FileOperationKind.COPY,
            (str(source),),
            str(destination_folder),
        )
    )
    conflict = plan.conflicts[0]

    presentation = ConflictPresentationModel.from_conflict(
        conflict,
        item_index=1,
        total_count=1,
        selected_action=conflict.default_resolution,
    )

    assert presentation.source_name == "README"
    assert presentation.destination_name == "README"
    assert presentation.source_path == str(source)
    assert presentation.destination_path == str(destination)
    assert presentation.source_size == "8 bytes"
    assert presentation.destination_size == "3 bytes"
    assert presentation.source_modified_time != "(不明)"
    assert presentation.destination_modified_time != "(不明)"
    assert presentation.item_position == "1 / 1"


def test_real_conflict_dialog_updates_all_detail_labels(
    tmp_path: Path,
    qapp,
) -> None:
    source_folder = tmp_path / ("非常に長いコピー元" * 6)
    destination_folder = tmp_path / ("非常に長いコピー先" * 6)
    source_folder.mkdir()
    destination_folder.mkdir()
    sources: list[str] = []
    for name, data in (("README", b"first"), ("日本語 file.txt", b"second")):
        source = source_folder / name
        source.write_bytes(data)
        (destination_folder / name).write_bytes(b"old")
        sources.append(str(source))
    plan = FileOperationPlanner().prepare(
        FileOperationRequest(
            1,
            FileOperationKind.COPY,
            tuple(sources),
            str(destination_folder),
        )
    )
    dialog = ConflictResolutionDialog(plan)
    dialog.show()
    qapp.processEvents()

    assert dialog.detail_labels["position"].text() == "1 / 2"
    assert dialog.detail_labels["source_name"].text() == "README"
    assert dialog.detail_labels["source_path"].text() == sources[0]
    assert dialog.detail_labels["destination_path"].text() == str(
        destination_folder / "README"
    )
    assert "bytes" in dialog.detail_labels["source_meta"].text()
    dialog.table.setCurrentIndex(dialog.model.index(1, 0))
    dialog.table.selectRow(1)
    qapp.processEvents()
    assert dialog.detail_labels["position"].text() == "2 / 2"
    assert dialog.detail_labels["source_name"].text() == "日本語 file.txt"
    assert dialog.detail_labels["source_path"].toolTip() == sources[1]
    dialog._apply(plan.conflicts[1].allowed_resolutions[1])
    assert dialog.detail_labels["action"].text() == "keep_both"
    dialog.close()


@pytest.mark.parametrize("name", ["a" * 180 + ".bin", "画像" * 90 + ".bin"])
@pytest.mark.parametrize("move", [False, True])
def test_long_name_transfer_uses_detectable_short_artifact(tmp_path, monkeypatch, name, move):
    source = tmp_path / name
    source.write_bytes(b"long name data")
    target = tmp_path / "target"
    target.mkdir()
    staging = Path(FileOperationArtifactPolicy.create_staging_path(target / name))
    assert len(staging.name) < 80
    assert FileOperationArtifactPolicy.is_internal_operation_artifact(staging)
    with pytest.raises(ValueError):
        FileOperationArtifactPolicy.create_staging_path(staging)
    # A compact orphan must not guess the original filename from a digest.
    staging.write_bytes(b"unfinished")
    description = OrphanArtifactScanner().scan(target)[0]
    assert description.final_destination is None
    assert description.final_destination_exists is None
    assert FileOperationArtifactPolicy.cleanup_staging_path(staging).removed
    original_rename = os.rename

    def cross_volume(old, new):
        if Path(old) == source:
            raise OSError(errno.EXDEV, "cross volume")
        return original_rename(old, new)

    monkeypatch.setattr(os, "rename", cross_volume)
    service = FileOperationService()
    result = (service.move if move else service.copy)([source], target)
    assert result.items[0].success
    assert (target / name).read_bytes() == b"long name data"
    assert source.exists() is not move
    assert result.undo_entries
    assert not FileOperationArtifactPolicy.find_orphans(target)
    if move:
        # The short artifact must not prevent a normal move undo receipt.
        undo = service.execute(FileOperationRequest(
            2, FileOperationKind.UNDO, undo_entries=result.undo_entries,
        ))
        assert undo.items[0].success
        assert source.read_bytes() == b"long name data"
        assert not (target / name).exists()


@pytest.mark.parametrize("size", [0, 1024, 4 * 1024 * 1024, 4 * 1024 * 1024 + 1])
@pytest.mark.parametrize("folder", [False, True])
def test_readonly_copy_preserves_source_and_destination_attributes(tmp_path, size, folder):
    source_folder = tmp_path / "source"
    source_folder.mkdir()
    source = source_folder / "読み取り専用.bin"
    source.write_bytes(b"r" * size)
    mode = source.stat().st_mode
    source.chmod(stat.S_IREAD)
    readonly_mode = source.stat().st_mode
    target = tmp_path / "target"
    target.mkdir()
    destination = target / source_folder.name / source.name if folder else target / source.name
    try:
        result = FileOperationService().copy([source_folder if folder else source], target)
        assert result.items[0].success
        assert destination.read_bytes() == source.read_bytes()
        assert source.stat().st_mode == readonly_mode
        assert not destination.stat().st_mode & stat.S_IWRITE
        assert not FileOperationArtifactPolicy.find_orphans(target)
    finally:
        source.chmod(mode)
        if destination.exists():
            destination.chmod(mode)


@pytest.mark.parametrize("folder", [False, True])
def test_readonly_staging_is_cleaned_after_publish_failure(tmp_path, monkeypatch, folder, caplog):
    source_folder = tmp_path / "source"
    source_folder.mkdir()
    source = source_folder / "readonly.bin"
    source.write_bytes(b"protected source")
    mode = source.stat().st_mode
    source.chmod(stat.S_IREAD)
    readonly_mode = source.stat().st_mode
    target = tmp_path / "target"
    target.mkdir()

    def fail_publish(old, new):
        raise PermissionError(errno.EACCES, "private message", str(old), 5, str(new))

    monkeypatch.setattr(os, "rename", fail_publish)
    try:
        with caplog.at_level(logging.INFO, logger="nivisviewer.file_operation"):
            result = FileOperationService().copy([source_folder if folder else source], target)
        item = result.items[0]
        assert not item.success
        assert item.error_code == FileOperationErrorCode.ACCESS_DENIED.value
        assert not item.destination_published
        assert not FileOperationArtifactPolicy.find_orphans(target)
        assert source.read_bytes() == b"protected source"
        assert source.stat().st_mode == readonly_mode
        assert "stage=publish" in caplog.text
        assert "winerror=5" in caplog.text
        assert str(tmp_path) not in caplog.text
        assert "private message" not in caplog.text
    finally:
        source.chmod(mode)


@pytest.mark.parametrize("failure_stage", ["flush", "metadata"])
@pytest.mark.parametrize("move", [False, True])
def test_transfer_io_failure_preserves_source_and_existing_destination(
    tmp_path, monkeypatch, caplog, failure_stage, move,
):
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    target = tmp_path / "target"
    target.mkdir()
    destination = target / source.name
    destination.write_bytes(b"existing")
    original_rename = os.rename
    original_replace = os.replace

    def cross_volume(old, new):
        if Path(old) == source:
            raise OSError(errno.EXDEV, "cross volume")
        return original_rename(old, new)

    def fail(*args, **kwargs):
        raise OSError(errno.EIO, "simulated transfer failure")

    def cross_volume_replace(old, new):
        if Path(old) == source:
            raise OSError(errno.EXDEV, "cross volume")
        return original_replace(old, new)

    monkeypatch.setattr(os, "rename", cross_volume)
    monkeypatch.setattr(os, "replace", cross_volume_replace)
    monkeypatch.setattr(
        "app.chunked_file_copier.os.fsync" if failure_stage == "flush"
        else "app.chunked_file_copier.shutil.copystat", fail,
    )
    service = FileOperationService()
    with caplog.at_level(logging.INFO, logger="nivisviewer.file_operation"):
        item = (service.move if move else service.copy)(
            [source], target, collision_policy=FileCollisionPolicy.REPLACE,
        ).items[0]
    assert not item.success
    assert not item.destination_published
    assert source.read_bytes() == b"source"
    assert destination.read_bytes() == b"existing"
    assert not FileOperationArtifactPolicy.find_orphans(target)
    assert f"file copy failure stage={failure_stage}" in caplog.text
    assert "stage_ms=" in caplog.text
    assert str(tmp_path) not in caplog.text
    assert "simulated transfer failure" not in caplog.text


def test_readonly_copy_cancelled_after_metadata_cleans_staging(tmp_path):
    source = tmp_path / "readonly.bin"
    source.write_bytes(b"source")
    original_mode = source.stat().st_mode
    source.chmod(stat.S_IREAD)
    target = tmp_path / "target"
    target.mkdir()
    cancelled = Event()

    class CancellingCopier(ChunkedFileCopier):
        def copy_to_staging(self, *args, **kwargs):
            copied = super().copy_to_staging(*args, **kwargs)
            cancelled.set()
            return copied

    service = FileOperationService()
    service.file_copier = CancellingCopier()
    try:
        result = service.copy([source], target, cancelled=cancelled)
        assert result.cancelled
        assert not result.items[0].success
        assert source.read_bytes() == b"source"
        assert not source.stat().st_mode & stat.S_IWRITE
        assert not tuple(target.iterdir())
    finally:
        source.chmod(original_mode)


def test_copy_to_staging_does_not_overwrite_existing_small_file(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    staging = tmp_path / "existing.bin"
    staging.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        ChunkedFileCopier().copy_to_staging(source, staging)
    assert staging.read_bytes() == b"existing"
    assert source.read_bytes() == b"source"


@pytest.mark.parametrize("identity_field", ["st_dev", "st_ino"])
def test_cross_volume_move_without_source_identity_is_rejected_before_copy(
    tmp_path, monkeypatch, identity_field,
):
    from types import SimpleNamespace

    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    target = tmp_path / "target"
    target.mkdir()
    original_lstat = os.lstat
    original_rename = os.rename

    def unavailable_identity(path, *args, **kwargs):
        value = original_lstat(path, *args, **kwargs)
        if Path(path) == source:
            attributes = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
            attributes[identity_field] = 0
            return SimpleNamespace(**attributes)
        return value

    def cross_volume(old, new):
        if Path(old) == source:
            raise OSError(errno.EXDEV, "cross volume")
        return original_rename(old, new)

    monkeypatch.setattr(os, "lstat", unavailable_identity)
    monkeypatch.setattr(os, "rename", cross_volume)
    item = FileOperationService().move([source], target).items[0]
    assert not item.success
    assert not item.destination_published
    assert source.read_bytes() == b"source"
    assert not tuple(target.iterdir())


@pytest.mark.parametrize("source", [r"\\server\share\画像.bin", r"Z:\画像.bin"])
def test_network_path_mime_round_trip_is_lexical(source):
    mime = build_path_mime_data((source,))
    assert paths_from_mime_data(mime) == (source,)


def test_single_conflict_upper_table_renders_integer_roles_compactly(
    tmp_path: Path,
    qapp,
) -> None:
    source = tmp_path / "コピー元" / ("日本語 と空白のある非常に長い名前" * 4)
    destination_folder = tmp_path / "コピー先"
    source.parent.mkdir()
    destination_folder.mkdir()
    source.write_bytes(b"new")
    destination = destination_folder / source.name
    destination.write_bytes(b"old")
    plan = FileOperationPlanner().prepare(
        FileOperationRequest(
            1,
            FileOperationKind.COPY,
            (str(source),),
            str(destination_folder),
        )
    )
    dialog = ConflictResolutionDialog(plan)
    dark = dialog.palette()
    dark.setColor(QPalette.ColorRole.Window, QColor("#202124"))
    dark.setColor(QPalette.ColorRole.WindowText, QColor("#f1f3f4"))
    dark.setColor(QPalette.ColorRole.Base, QColor("#171717"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#f1f3f4"))
    dark.setColor(QPalette.ColorRole.Highlight, QColor("#245a83"))
    dark.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    dialog.setPalette(dark)
    dialog.show()
    qapp.processEvents()

    assert dialog.model.rowCount() == 1
    assert dialog.model.headerData(
        0,
        Qt.Orientation.Horizontal,
        int(Qt.ItemDataRole.DisplayRole),
    ) == "コピー元"
    displayed = [
        dialog.model.data(
            dialog.model.index(0, column),
            int(Qt.ItemDataRole.DisplayRole),
        )
        for column in range(dialog.model.columnCount())
    ]
    assert displayed[0] == source.name
    assert displayed[1] == destination.name
    assert all(isinstance(text, str) and text for text in displayed)

    option = QStyleOptionViewItem()
    option.widget = dialog.table
    dialog.table.itemDelegate().initStyleOption(
        option,
        dialog.model.index(0, 0),
    )
    assert option.text == source.name
    assert dialog.table.geometry().width() > 0
    assert dialog.table.viewport().geometry().height() > 0
    assert dialog.table.rowHeight(0) > 0
    expected_maximum = (
        dialog.table.horizontalHeader().height()
        + dialog.table.rowHeight(0)
        + dialog.table.horizontalScrollBar().sizeHint().height()
        + dialog.table.frameWidth() * 2
        + 2
    )
    assert dialog.table.height() <= expected_maximum
    assert dialog.table.height() < dialog.height() // 2
    palette = dialog.table.viewport().palette()
    assert (
        palette.color(QPalette.ColorRole.Text)
        != palette.color(QPalette.ColorRole.Base)
    )
    assert dialog.table.textElideMode() is Qt.TextElideMode.ElideMiddle
    tooltip = dialog.model.data(
        dialog.model.index(0, 0),
        int(Qt.ItemDataRole.ToolTipRole),
    )
    assert str(source) in tooltip
    assert str(destination) in tooltip
    assert dialog.detail_labels["source_name"].text() == source.name
    assert set(dialog.resolutions.values()) == {ConflictResolution.SKIP}
    dialog._apply(ConflictResolution.KEEP_BOTH)
    assert set(dialog.resolutions.values()) == {ConflictResolution.KEEP_BOTH}
    dialog._apply(ConflictResolution.REPLACE)
    assert set(dialog.resolutions.values()) == {ConflictResolution.REPLACE}
    dialog.close()


def test_multiple_conflict_upper_table_updates_visible_rows_and_apply_all(
    tmp_path: Path,
    qapp,
) -> None:
    source_folder = tmp_path / "source"
    destination_folder = tmp_path / "destination"
    source_folder.mkdir()
    destination_folder.mkdir()
    names = ("README", "日本語 file without suffix")
    sources: list[Path] = []
    for name in names:
        source = source_folder / name
        source.write_bytes(b"new")
        (destination_folder / name).write_bytes(b"old")
        sources.append(source)
    plan = FileOperationPlanner().prepare(
        FileOperationRequest(
            1,
            FileOperationKind.COPY,
            tuple(str(path) for path in sources),
            str(destination_folder),
        )
    )
    dialog = ConflictResolutionDialog(plan)
    dialog.show()
    qapp.processEvents()

    assert dialog.model.rowCount() == 2
    for row, name in enumerate(names):
        values = [
            dialog.model.data(
                dialog.model.index(row, column),
                int(Qt.ItemDataRole.DisplayRole),
            )
            for column in range(dialog.model.columnCount())
        ]
        assert values[0] == name
        assert values[1] == name
        assert all(isinstance(text, str) and text for text in values)
        option = QStyleOptionViewItem()
        option.widget = dialog.table
        dialog.table.itemDelegate().initStyleOption(
            option,
            dialog.model.index(row, 0),
        )
        assert option.text == name
        assert dialog.table.visualRect(dialog.model.index(row, 0)).width() > 0

    second = dialog.model.index(1, 0)
    dialog.table.setCurrentIndex(second)
    dialog.table.selectRow(1)
    qapp.processEvents()
    assert dialog.detail_labels["position"].text() == "2 / 2"
    assert dialog.detail_labels["source_name"].text() == names[1]
    assert dialog.detail_labels["source_path"].text() == str(sources[1])
    dialog.same_kind_checkbox.setChecked(True)
    dialog._apply(ConflictResolution.KEEP_BOTH)
    assert set(dialog.resolutions.values()) == {ConflictResolution.KEEP_BOTH}
    assert dialog.detail_labels["action"].text() == "keep_both"
    dialog.close()
