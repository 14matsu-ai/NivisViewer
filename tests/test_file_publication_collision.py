from __future__ import annotations

import errno
import os
from pathlib import Path
from threading import Event

import pytest

from app.chunked_file_copier import ChunkedFileCopier
from app.file_operation_artifact import FileOperationArtifactPolicy
from app.file_operation_service import (
    FileCollisionPolicy, FileOperationErrorCode, FileOperationService,
    FileOperationKind, FileOperationRequest,
)


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("policy", [
    FileCollisionPolicy.SKIP, FileCollisionPolicy.KEEP_BOTH,
    FileCollisionPolicy.REPLACE,
])
def test_late_publication_collision_uses_policy(tmp_path, monkeypatch, move, policy):
    source = tmp_path / "日本語.txt"
    source.write_bytes(b"source")
    target = tmp_path / "target"
    target.mkdir()
    destination = target / source.name
    real_rename = os.rename
    if move:
        def cross_volume(old, new):
            if Path(old) == source:
                raise OSError(errno.EXDEV, "fake cross volume")
            return real_rename(old, new)
        monkeypatch.setattr(os, "rename", cross_volume)

    class RacingCopier(ChunkedFileCopier):
        raced = False

        def copy_to_staging(self, *args, **kwargs):
            result = super().copy_to_staging(*args, **kwargs)
            if not self.raced:
                self.raced = True
                destination.write_bytes(b"competing data")
            return result

    service = FileOperationService()
    service.file_copier = RacingCopier()
    result = (service.move if move else service.copy)(
        [source], target, collision_policy=policy,
    ).items[0]
    assert not FileOperationArtifactPolicy.find_orphans(target)
    if policy is FileCollisionPolicy.SKIP:
        assert not result.success
        assert result.error_code == FileOperationErrorCode.COLLISION.value
        assert source.read_bytes() == b"source"
        assert destination.read_bytes() == b"competing data"
        assert not result.destination_published
    else:
        assert result.success
        assert Path(result.destination_path).read_bytes() == b"source"
        assert source.exists() is not move
        if policy is FileCollisionPolicy.KEEP_BOTH:
            assert destination.read_bytes() == b"competing data"
            assert not result.replaced_existing
        else:
            assert result.replaced_existing


def test_generated_keep_both_name_also_handles_late_collision(tmp_path):
    source = tmp_path / "book.txt"
    source.write_bytes(b"source")
    target = tmp_path / "target"
    target.mkdir()
    (target / source.name).write_bytes(b"original")
    raced = []

    class RacingCopier(ChunkedFileCopier):
        def copy_to_staging(self, source, staging, **kwargs):
            result = super().copy_to_staging(source, staging, **kwargs)
            if not raced:
                candidate = Path(FileOperationArtifactPolicy.derive_final_destination(staging))
                candidate.write_bytes(b"late generated name")
                raced.append(candidate)
            return result

    service = FileOperationService()
    service.file_copier = RacingCopier()
    result = service.copy([source], target, collision_policy=FileCollisionPolicy.KEEP_BOTH).items[0]
    assert result.success
    assert raced[0].read_bytes() == b"late generated name"
    assert Path(result.destination_path).read_bytes() == b"source"
    assert Path(result.destination_path) != raced[0]
    assert not FileOperationArtifactPolicy.find_orphans(target)


def test_cancellation_at_publication_keeps_source_and_competitor(tmp_path):
    source = tmp_path / "book.txt"
    source.write_bytes(b"source")
    target = tmp_path / "target"
    target.mkdir()
    cancel = Event()

    class CancellingCopier(ChunkedFileCopier):
        def copy_to_staging(self, *args, **kwargs):
            result = super().copy_to_staging(*args, **kwargs)
            (target / source.name).write_bytes(b"competitor")
            cancel.set()
            return result

    service = FileOperationService()
    service.file_copier = CancellingCopier()
    result = service.copy([source], target, cancelled=cancel)
    assert result.cancelled
    assert source.read_bytes() == b"source"
    assert (target / source.name).read_bytes() == b"competitor"
    assert not FileOperationArtifactPolicy.find_orphans(target)


@pytest.mark.parametrize("move", [False, True])
def test_directory_publication_collision_preserves_both_trees(tmp_path, monkeypatch, move):
    source = tmp_path / "source" / "Folder.Name"
    source.mkdir(parents=True)
    (source / "child.txt").write_bytes(b"source")
    target = tmp_path / "target"
    target.mkdir()
    destination = target / source.name
    service = FileOperationService()
    real_copy = service._copy_directory

    def copy_then_compete(src, staging, cancel):
        real_copy(src, staging, cancel)
        destination.mkdir()
        (destination / "other.txt").write_bytes(b"competing tree")

    monkeypatch.setattr(service, "_copy_directory", copy_then_compete)
    real_rename = os.rename
    if move:
        def cross_volume(old, new):
            if Path(old) == source:
                raise OSError(errno.EXDEV, "cross volume")
            return real_rename(old, new)
        monkeypatch.setattr(os, "rename", cross_volume)
    result = (service.move if move else service.copy)([source], target).items[0]
    assert not result.success
    assert result.error_code == FileOperationErrorCode.COLLISION.value
    assert (source / "child.txt").read_bytes() == b"source"
    assert (destination / "other.txt").read_bytes() == b"competing tree"
    assert not (destination / "child.txt").exists()
    assert not FileOperationArtifactPolicy.find_orphans(target)


def test_merge_child_late_collision_keeps_partial_receipts(tmp_path):
    source = tmp_path / "source" / "folder"
    source.mkdir(parents=True)
    (source / "child.txt").write_bytes(b"source")
    target = tmp_path / "target"
    destination = target / source.name
    destination.mkdir(parents=True)

    class RacingCopier(ChunkedFileCopier):
        def copy_to_staging(self, *args, **kwargs):
            result = super().copy_to_staging(*args, **kwargs)
            (destination / "child.txt").write_bytes(b"competitor")
            return result

    service = FileOperationService()
    service.file_copier = RacingCopier()
    result = service.execute(FileOperationRequest(
        1, FileOperationKind.COPY, (str(source),), str(target),
        collision_resolutions=((str(destination), FileCollisionPolicy.MERGE.value),),
    )).items[0]
    assert result.child_results[0].error_code == FileOperationErrorCode.COLLISION.value
    assert not result.child_results[0].destination_published
    assert (destination / "child.txt").read_bytes() == b"competitor"
    assert not FileOperationArtifactPolicy.find_orphans(destination)


def test_same_volume_move_publication_collision_retains_source(tmp_path, monkeypatch):
    source = tmp_path / "book.txt"
    source.write_bytes(b"source")
    target = tmp_path / "target"
    target.mkdir()
    real_rename = os.rename

    def compete_then_rename(old, new):
        Path(new).write_bytes(b"competitor")
        return real_rename(old, new)

    monkeypatch.setattr(os, "rename", compete_then_rename)
    result = FileOperationService().move([source], target).items[0]
    assert result.error_code == FileOperationErrorCode.COLLISION.value
    assert source.read_bytes() == b"source"
    assert (target / source.name).read_bytes() == b"competitor"
