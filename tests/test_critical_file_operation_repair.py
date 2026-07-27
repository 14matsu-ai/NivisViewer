from __future__ import annotations

import errno
import hashlib
import os
import uuid
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QModelIndex

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
from app.file_operation_plan import FileOperationPlanner, FileOperationState
from app.file_operation_service import (
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

    def fail_replace(_old, _new):
        raise PermissionError("publish locked")

    monkeypatch.setattr("app.file_operation_service.os.replace", fail_replace)
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

    monkeypatch.setattr(
        "app.file_operation_service.os.rename",
        lambda *_args: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross volume")),
    )
    monkeypatch.setattr(
        "app.file_operation_service.os.replace",
        lambda *_args: (_ for _ in ()).throw(PermissionError("publish locked")),
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
    original_replace = os.replace

    monkeypatch.setattr(
        "app.file_operation_service.os.rename",
        lambda *_args: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross volume")),
    )

    def publish_then_cancel(old, new):
        result = original_replace(old, new)
        cancelled.set()
        return result

    monkeypatch.setattr("app.file_operation_service.os.replace", publish_then_cancel)
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


def test_planner_and_service_reject_internal_artifact_source(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    artifact = tmp_path / _artifact_name()
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


def test_artifact_never_enters_clipboard_drag_or_browser_model(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / _artifact_name()
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
    assert entries[0].preview_kind == "windows_shell"


def test_internal_artifacts_are_hidden_with_all_visibility_flags(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "files"
    folder.mkdir()
    artifact = folder / _artifact_name()
    nested = folder / _nested_legacy_name()
    ordinary_tmp = folder / "ordinary.tmp"
    artifact.write_bytes(b"artifact")
    nested.write_bytes(b"nested")
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
