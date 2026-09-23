"""Conservative, session-only receipts for the existing file-operation lane.

No recycle-bin restoration, recursive deletion, replacement restoration, or
cross-volume guesswork. Filesystem validation and execution run in the same
existing operation worker, never in a keyboard callback.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from threading import Event
    from .file_operation_service import FileOperationResult, FileOperationRequest


@dataclass(frozen=True)
class UndoStamp:
    device: int
    inode: int
    kind: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class FileUndoEntry:
    action: str
    path: str
    original_path: str | None
    stamp: UndoStamp


def safe_stamp(path: str) -> UndoStamp | None:
    """Reject missing paths and reparse/symlink chains (including parents)."""
    try:
        candidate = Path(os.path.abspath(path))
        for part in (candidate, *candidate.parents):
            value = os.lstat(part)
            if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
                return None
        value = os.lstat(candidate)
        kind = stat.S_IFMT(value.st_mode)
        if kind not in {stat.S_IFREG, stat.S_IFDIR} or not value.st_ino:
            return None
        return UndoStamp(value.st_dev, value.st_ino, kind, value.st_size, value.st_mtime_ns)
    except (OSError, ValueError):
        return None


def is_empty_directory(path: str) -> bool:
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is None
    except OSError:
        return False


def make_undo_entries(result: FileOperationResult) -> tuple[FileUndoEntry, ...]:
    """Capture only newly published, fully successful, non-merging results.

    A mixed/partial batch is intentionally not offered as an atomic undo.
    This also prevents crossing an unsupported operation to undo an older one.
    """
    from .file_operation_service import FileOperationKind as Kind
    if result.cancelled or not result.items:
        return ()
    if result.operation not in {Kind.COPY, Kind.MOVE, Kind.RENAME,
                                 Kind.CREATE_DIRECTORY, Kind.CREATE_ZIP}:
        return ()
    entries = []
    for item in result.items:
        if (not item.success or item.partial_success or item.partially_completed
                or item.child_results or item.replaced_existing
                or item.destination_existed_before or not item.destination_path):
            return ()
        path = item.destination_path
        fingerprint = safe_stamp(path)
        if fingerprint is None:
            return ()
        original = item.source_path
        if result.operation in {Kind.MOVE, Kind.RENAME}:
            if not original or os.path.lexists(original):
                return ()  # Includes case-only renames on Windows.
            # Different-name MOVE (Keep Both) needs a compound transaction.
            if result.operation is Kind.MOVE and Path(original).name != Path(path).name:
                return ()
            action = result.operation.value
        elif fingerprint.kind == stat.S_IFDIR:
            if not is_empty_directory(path):
                return ()  # Never recursively remove a copied folder.
            action = "remove_empty_directory"
            original = None
        else:
            if result.operation is Kind.CREATE_DIRECTORY:
                return ()
            action = "recycle"
            original = None
        entries.append(FileUndoEntry(action, path, original, fingerprint))
    return tuple(reversed(entries))


def run_undo(service, request: FileOperationRequest, cancelled: Event | None,
             progress=None) -> FileOperationResult:
    """Use existing service operations after conservative revalidation.

    Validation is not a security sandbox against a malicious concurrent writer.
    No-overwrite publication in the service is still mandatory. Each result is
    truthful about success/skip/cancellation, including a partially undone batch.
    """
    from threading import Event
    from .file_operation_service import (
        FileCollisionPolicy, FileOperationItemResult, FileOperationItemState,
        FileOperationKind as Kind, FileOperationProgress, FileOperationRequest,
        FileOperationResult,
    )
    cancel = cancelled or Event()
    items = []
    entries = request.undo_entries
    for entry in entries:
        if cancel.is_set():
            break
        inverse_kind = {"move": Kind.MOVE, "rename": Kind.RENAME,
                        "recycle": Kind.RECYCLE,
                        "remove_empty_directory": Kind.RECYCLE}.get(entry.action)
        destination = entry.original_path
        valid = inverse_kind is not None and safe_stamp(entry.path) == entry.stamp
        if destination:
            valid = valid and not os.path.lexists(destination)
            parent_stamp = safe_stamp(str(Path(destination).parent))
            valid = valid and parent_stamp is not None and parent_stamp.kind == stat.S_IFDIR
        if entry.action == "remove_empty_directory":
            valid = valid and is_empty_directory(entry.path)
        if not valid:
            items.append(FileOperationItemResult(
                entry.path, destination, False, error_code="undo_precondition_changed",
                error_message="操作後に項目または戻し先が変更されたため、取り消しません。",
                state=FileOperationItemState.SKIPPED, operation=inverse_kind))
        elif entry.action == "remove_empty_directory":
            try:
                # rmdir fails rather than removing newly added content.
                os.rmdir(entry.path)
                items.append(FileOperationItemResult(
                    entry.path, None, True, source_removed=True,
                    source_exists_after=False, operation=Kind.RECYCLE))
            except OSError as exc:
                items.append(FileOperationItemResult(
                    entry.path, None, False, error_code="undo_directory_changed",
                    error_message=str(exc), state=FileOperationItemState.SKIPPED,
                    operation=Kind.RECYCLE))
        else:
            inverse = FileOperationRequest(
                request.request_id, inverse_kind, (entry.path,),
                str(Path(destination).parent) if entry.action == "move" else None,
                Path(destination).name if entry.action == "rename" else None,
                FileCollisionPolicy.SKIP, operation_id=request.operation_id)
            # Do not create a new undo receipt for an inverse operation.
            result = service._execute_request(inverse, cancelled=cancel)
            items.extend(replace(item, operation=inverse_kind) for item in result.items)
        if progress:
            progress(FileOperationProgress(
                request.request_id, Kind.UNDO, len(items), len(entries),
                source_path=entry.path, operation_id=request.operation_id))
    return FileOperationResult(Kind.UNDO, tuple(items), cancelled=cancel.is_set(),
                               request_id=request.request_id, operation_id=request.operation_id)
