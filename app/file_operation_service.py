from __future__ import annotations

from .file_operation_undo import FileUndoEntry, make_undo_entries, run_undo

from .i18n import tr


import errno
import logging
import os
import shutil
import stat
import time
import uuid
import zipfile
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Callable, Iterable

from .chunked_file_copier import ChunkedFileCopier, CopyCancelled
from .file_operation_artifact import (
    ArtifactCleanupResult,
    FileOperationArtifactPolicy,
)
from .windows_filename import generate_copy_name, validate_windows_filename
from .windows_recycle_bin import RecycleBinAdapter, WindowsRecycleBin


_LOG = logging.getLogger("nivisviewer.file_operation")


class FileOperationKind(str, Enum):
    RENAME = "rename"
    COPY = "copy"
    MOVE = "move"
    RECYCLE = "recycle"
    CREATE_DIRECTORY = "create_directory"
    CREATE_ZIP = "create_zip"

    UNDO = "undo"


class FileCollisionPolicy(str, Enum):
    SKIP = "skip"
    RENAME = "rename"
    KEEP_BOTH = "keep_both"
    REPLACE = "replace"
    MERGE = "merge"
    CANCEL = "cancel"


class FileOperationErrorCode(str, Enum):
    NOT_FOUND = "not_found"
    ACCESS_DENIED = "access_denied"
    IN_USE = "in_use"
    COLLISION = "collision"
    INVALID_NAME = "invalid_name"
    INVALID_DESTINATION = "invalid_destination"
    SAME_PATH = "same_path"
    DESCENDANT_DESTINATION = "descendant_destination"
    API_UNAVAILABLE = "api_unavailable"
    CANCELLED = "cancelled"
    PARTIAL_SUCCESS = "partial_success"
    INTERNAL_STAGING_ARTIFACT = "internal_staging_artifact"
    ARTIFACT_CLEANUP_FAILED = "artifact_cleanup_failed"
    IO_ERROR = "io_error"


class FileOperationItemState(str, Enum):
    COMPLETED = "completed"
    MOVED = "moved"
    COPIED = "copied"
    COPIED_SOURCE_REMAINS = "copied_source_remains"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SOURCE_REMOVAL_FAILED = "source_removal_failed"
    DESTINATION_PUBLISHED_SOURCE_REMAINS = (
        "destination_published_source_remains"
    )


class FileOperationLifecycleState(str, Enum):
    PLANNED = "planned"
    STAGING_CREATED = "staging_created"
    COPYING = "copying"
    STAGING_COMPLETE = "staging_complete"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    REMOVING_SOURCE = "removing_source"
    COMPLETED = "completed"
    PARTIAL_SOURCE_REMAINS = "partial_source_remains"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class FileOperationRequest:
    request_id: int
    operation: FileOperationKind
    source_paths: tuple[str, ...] = ()
    destination_directory: str | None = None
    new_name: str | None = None
    collision_policy: FileCollisionPolicy = FileCollisionPolicy.SKIP
    operation_id: str = ""
    collision_resolutions: tuple[tuple[str, str], ...] = ()
    planned_total_bytes: int = 0
    planned_item_bytes: tuple[tuple[str, int], ...] = ()

    undo_entries: tuple[FileUndoEntry, ...] = ()


@dataclass(frozen=True)
class FileOperationItemResult:
    source_path: str | None
    destination_path: str | None
    success: bool
    error_code: str | None = None
    error_message: str | None = None
    partial_success: bool = False
    state: FileOperationItemState = FileOperationItemState.COMPLETED
    destination_exists_after: bool | None = None
    source_exists_after: bool | None = None
    destination_published: bool = False
    source_removed: bool = False
    copied_bytes: int = 0
    expected_bytes: int | None = None
    residual_source_paths: tuple[str, ...] = ()
    operation: FileOperationKind | None = None
    replaced_existing: bool = False
    destination_existed_before: bool = False
    child_results: tuple[FileOperationItemResult, ...] = ()
    published_destination_paths: tuple[str, ...] = ()
    moved_source_paths: tuple[str, ...] = ()
    skipped_source_paths: tuple[str, ...] = ()
    failed_source_paths: tuple[str, ...] = ()
    retry_source_paths: tuple[str, ...] = ()
    source_root_removed: bool | None = None
    partially_completed: bool = False
    lifecycle_state: FileOperationLifecycleState = (
        FileOperationLifecycleState.COMPLETED
    )
    artifact_paths: tuple[str, ...] = ()
    cleanup_errors: tuple[str, ...] = ()

    def leaf_results(self) -> tuple[FileOperationItemResult, ...]:
        if not self.child_results:
            return (self,)
        leaves: list[FileOperationItemResult] = []
        for child in self.child_results:
            leaves.extend(child.leaf_results())
        return tuple(leaves)


@dataclass(frozen=True)
class FileOperationResult:
    operation: FileOperationKind
    items: tuple[FileOperationItemResult, ...]
    cancelled: bool = False
    request_id: int = 0
    operation_id: str = ""

    @property
    def successes(self) -> tuple[FileOperationItemResult, ...]:
        return tuple(item for item in self.items if item.success)

    @property
    def failures(self) -> tuple[FileOperationItemResult, ...]:
        return tuple(item for item in self.items if not item.success)

    @property
    def effective_items(self) -> tuple[FileOperationItemResult, ...]:
        items: list[FileOperationItemResult] = []
        for item in self.items:
            items.extend(item.leaf_results())
        return tuple(items)

    undo_entries: tuple[FileUndoEntry, ...] = ()
    metadata_sync_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileOperationProgress:
    request_id: int
    operation: FileOperationKind
    completed: int
    total: int
    source_path: str | None = None
    destination_path: str | None = None
    bytes_completed: int = 0
    bytes_total: int = 0
    bytes_per_second: float = 0.0
    eta_seconds: float | None = None
    failed: int = 0
    skipped: int = 0
    renamed: int = 0
    replaced: int = 0
    operation_id: str = ""
    state: str = "running"
    current_file_bytes_total: int | None = None
    current_file_bytes_completed: int = 0

    @property
    def operation_kind(self) -> str:
        return self.operation.value

    @property
    def current_source_path(self) -> str | None:
        return self.source_path

    @property
    def current_destination_path(self) -> str | None:
        return self.destination_path

    @property
    def item_index(self) -> int:
        return self.completed

    @property
    def total_items(self) -> int:
        return self.total

    @property
    def completed_items(self) -> int:
        return self.completed

    @property
    def estimated_seconds_remaining(self) -> float | None:
        return self.eta_seconds

    @property
    def failure_count(self) -> int:
        return self.failed


ProgressCallback = Callable[[FileOperationProgress], None]


class _OperationCancelled(Exception):
    pass


class _SourceDeleteFailed(Exception):
    pass


class _PublicationCollision(FileExistsError):
    """Only a non-replacing final rename may re-enter collision policy."""


class _ArtifactOperationError(OSError):
    def __init__(
        self,
        message: str,
        *,
        artifact_path: str | None = None,
        artifact_paths: tuple[str, ...] = (),
        cleanup: ArtifactCleanupResult | None = None,
        cleanups: tuple[ArtifactCleanupResult, ...] = (),
        published: bool = False,
    ) -> None:
        super().__init__(message)
        paths = artifact_paths or (
            (artifact_path,) if artifact_path is not None else ()
        )
        cleanup_results = cleanups or (
            (cleanup,) if cleanup is not None else ()
        )
        self.artifact_paths = paths
        self.cleanups = cleanup_results
        self.artifact_path = paths[0] if paths else ""
        self.cleanup = cleanup_results[0] if cleanup_results else None
        self.published = bool(published)


@dataclass(frozen=True)
class _SourceReceiptEntry:
    relative_path: str
    kind: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


class FileOperationService:
    def __init__(self, recycle_bin: RecycleBinAdapter | None = None) -> None:
        self.recycle_bin = recycle_bin or WindowsRecycleBin()
        self.file_copier = ChunkedFileCopier()
        self._byte_progress: Callable[[int], None] | None = None

    def _execute_request(
        self,
        request: FileOperationRequest,
        *,
        cancelled: Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> FileOperationResult:
        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "service execute request=%s kind=%s sources=%r destination=%s",
                request.request_id,
                request.operation.value,
                request.source_paths,
                request.destination_directory,
            )
        cancel_event = cancelled or Event()
        if request.operation is FileOperationKind.CREATE_DIRECTORY:
            return self._create_directory(request, cancel_event, progress)
        if request.operation is FileOperationKind.CREATE_ZIP:
            return self._create_zip(request, cancel_event, progress)

        sources = self._prepare_sources(request.source_paths)
        results: list[FileOperationItemResult] = []
        total = len(sources)
        was_cancelled = False
        bytes_total = max(0, int(request.planned_total_bytes))
        item_totals = {
            self._path_key(path): max(0, int(size))
            for path, size in request.planned_item_bytes
        }
        bytes_completed = 0
        started_at = time.monotonic()
        last_emit = 0.0
        last_speed_sample_at = started_at
        last_speed_sample_bytes = 0
        ewma_speed = 0.0

        def emit_byte_progress(
            delta: int,
            source_path: str,
            destination_path: str | None,
            completed_items: int,
            *,
            force: bool = False,
            item_start_bytes: int = 0,
            item_total_bytes: int | None = None,
        ) -> None:
            nonlocal bytes_completed, last_emit
            nonlocal last_speed_sample_at, last_speed_sample_bytes, ewma_speed
            bytes_completed += max(0, int(delta))
            if progress is None:
                return
            now = time.monotonic()
            if not force and now - last_emit < 0.075:
                return
            sample_elapsed = max(0.001, now - last_speed_sample_at)
            sample_bytes = max(0, bytes_completed - last_speed_sample_bytes)
            instantaneous = sample_bytes / sample_elapsed
            if sample_bytes:
                ewma_speed = (
                    instantaneous
                    if ewma_speed <= 0
                    else 0.25 * instantaneous + 0.75 * ewma_speed
                )
                last_speed_sample_at = now
                last_speed_sample_bytes = bytes_completed
            speed = ewma_speed
            remaining = max(0, bytes_total - bytes_completed)
            failed_count = sum(not item.success for item in results)
            skipped_count = sum(
                item.error_code == FileOperationErrorCode.COLLISION.value
                for item in results
            )
            renamed_count = sum(
                bool(
                    item.success
                    and item.source_path
                    and item.destination_path
                    and os.path.basename(item.source_path)
                    != os.path.basename(item.destination_path)
                )
                for item in results
            )
            replaced_destinations = {
                self._path_key(path)
                for path, resolution in request.collision_resolutions
                if resolution == FileCollisionPolicy.REPLACE.value
            }
            replaced_count = sum(
                bool(
                    item.success
                    and item.destination_path
                    and self._path_key(item.destination_path)
                    in replaced_destinations
                )
                for item in results
            )
            progress(
                FileOperationProgress(
                    request.request_id,
                    request.operation,
                    completed_items,
                    total,
                    source_path,
                    destination_path,
                    bytes_completed,
                    bytes_total,
                    speed,
                    remaining / speed if speed > 0 and bytes_total else None,
                    failed_count,
                    skipped_count,
                    renamed_count,
                    replaced_count,
                    operation_id=request.operation_id,
                    current_file_bytes_total=item_total_bytes,
                    current_file_bytes_completed=max(
                        0,
                        bytes_completed - item_start_bytes,
                    ),
                )
            )
            last_emit = now

        for index, source in enumerate(sources):
            if cancel_event.is_set():
                was_cancelled = True
                break
            try:
                item_start_bytes = bytes_completed
                item_total_bytes = item_totals.get(self._path_key(source))
                destination = (
                    os.path.join(
                        self._absolute(request.destination_directory),
                        os.path.basename(source),
                    )
                    if request.destination_directory
                    and request.operation in {FileOperationKind.COPY, FileOperationKind.MOVE}
                    else None
                )
                self._byte_progress = lambda delta, source=source, destination=destination, index=index, item_start_bytes=item_start_bytes, item_total_bytes=item_total_bytes: emit_byte_progress(
                    delta,
                    source,
                    destination,
                    index,
                    item_start_bytes=item_start_bytes,
                    item_total_bytes=item_total_bytes,
                )
                item = self._execute_item(request, source, cancel_event)
            except _OperationCancelled:
                item = self._failure(
                    source,
                    None,
                    FileOperationErrorCode.CANCELLED,
                    tr('操作がキャンセルされました'),
                    operation=request.operation,
                )
                was_cancelled = True
            except BaseException as exc:
                item = self._exception_failure(
                    source,
                    None,
                    exc,
                    operation=request.operation,
                )
            item = replace(
                item,
                copied_bytes=max(0, bytes_completed - item_start_bytes),
                expected_bytes=item_total_bytes,
            )
            if (
                item.success
                and request.operation is FileOperationKind.MOVE
                and item_total_bytes is not None
                and bytes_completed == item_start_bytes
            ):
                bytes_completed += item_total_bytes
            results.append(item)
            self._byte_progress = None
            if progress is not None:
                emit_byte_progress(
                    0,
                    source,
                    item.destination_path,
                    index + 1,
                    force=True,
                    item_start_bytes=item_start_bytes,
                    item_total_bytes=item_total_bytes,
                )
            if was_cancelled:
                break
        return FileOperationResult(
            request.operation,
            tuple(results),
            cancelled=was_cancelled or cancel_event.is_set(),
            request_id=request.request_id,
            operation_id=request.operation_id,
        )

    def rename(
        self,
        source_path: str | Path,
        new_name: str,
        *,
        request_id: int = 0,
    ) -> FileOperationResult:
        return self.execute(
            FileOperationRequest(
                request_id,
                FileOperationKind.RENAME,
                (self._absolute(source_path),),
                new_name=new_name,
            )
        )

    def copy(
        self,
        source_paths: Iterable[str | Path],
        destination_directory: str | Path,
        *,
        collision_policy: FileCollisionPolicy = FileCollisionPolicy.SKIP,
        request_id: int = 0,
        cancelled: Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> FileOperationResult:
        return self.execute(
            FileOperationRequest(
                request_id,
                FileOperationKind.COPY,
                tuple(self._absolute(path) for path in source_paths),
                self._absolute(destination_directory),
                collision_policy=collision_policy,
            ),
            cancelled=cancelled,
            progress=progress,
        )

    def move(
        self,
        source_paths: Iterable[str | Path],
        destination_directory: str | Path,
        *,
        collision_policy: FileCollisionPolicy = FileCollisionPolicy.SKIP,
        request_id: int = 0,
        cancelled: Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> FileOperationResult:
        return self.execute(
            FileOperationRequest(
                request_id,
                FileOperationKind.MOVE,
                tuple(self._absolute(path) for path in source_paths),
                self._absolute(destination_directory),
                collision_policy=collision_policy,
            ),
            cancelled=cancelled,
            progress=progress,
        )

    def recycle(
        self,
        source_paths: Iterable[str | Path],
        *,
        request_id: int = 0,
        cancelled: Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> FileOperationResult:
        return self.execute(
            FileOperationRequest(
                request_id,
                FileOperationKind.RECYCLE,
                tuple(self._absolute(path) for path in source_paths),
            ),
            cancelled=cancelled,
            progress=progress,
        )

    def create_directory(
        self,
        destination_directory: str | Path,
        name: str,
        *,
        request_id: int = 0,
    ) -> FileOperationResult:
        return self.execute(
            FileOperationRequest(
                request_id,
                FileOperationKind.CREATE_DIRECTORY,
                destination_directory=self._absolute(destination_directory),
                new_name=name,
            )
        )

    def _execute_item(
        self,
        request: FileOperationRequest,
        source: str,
        cancelled: Event,
    ) -> FileOperationItemResult:
        if FileOperationArtifactPolicy.is_internal_operation_artifact(source):
            return self._failure(
                source,
                None,
                FileOperationErrorCode.INTERNAL_STAGING_ARTIFACT,
                tr('NivisViewerの未完了一時ファイルは通常のファイル操作対象にできません。'),
                operation=request.operation,
            )
        if not os.path.lexists(source):
            return self._failure(
                source,
                None,
                FileOperationErrorCode.NOT_FOUND,
                tr('対象が見つかりません'),
            )
        if self._is_reparse_path(source):
            return self._failure(
                source,
                None,
                FileOperationErrorCode.IO_ERROR,
                tr('再解析ポイントはファイル操作の再帰対象にしません'),
            )
        if request.operation is FileOperationKind.RENAME:
            return self._rename_item(source, request.new_name, request)
        if request.operation is FileOperationKind.RECYCLE:
            return self._recycle_item(source)
        if request.operation in {FileOperationKind.COPY, FileOperationKind.MOVE}:
            return self._transfer_item(request, source, cancelled)
        return self._failure(
            source,
            None,
            FileOperationErrorCode.IO_ERROR,
            tr('未対応のファイル操作です'),
        )

    def _rename_item(
        self,
        source: str,
        new_name: str | None,
        request: FileOperationRequest | None = None,
    ) -> FileOperationItemResult:
        try:
            source_stat = os.stat(source, follow_symlinks=False)
        except OSError:
            source_stat = None
        validation = validate_windows_filename(new_name or "")
        if not validation.valid:
            return self._failure(
                source,
                None,
                FileOperationErrorCode.INVALID_NAME,
                validation.error_message or tr('名前が無効です'),
            )
        destination = os.path.join(os.path.dirname(source), validation.normalized_name)
        same_key = self._path_key(source) == self._path_key(destination)
        if same_key and os.path.basename(source) == validation.normalized_name:
            return self._failure(
                source,
                destination,
                FileOperationErrorCode.SAME_PATH,
                tr('名前が変更されていません'),
            )
        if (
            same_key
            and request is not None
            and request.collision_resolutions
            and self._collision_resolution(request, destination)
            == FileCollisionPolicy.SKIP.value
        ):
            return self._failure(
                source,
                destination,
                FileOperationErrorCode.COLLISION,
                tr('大文字／小文字だけの名前変更をスキップしました'),
            )
        if self._name_exists(destination, ignore_path=source if same_key else None):
            return self._failure(
                source,
                destination,
                FileOperationErrorCode.COLLISION,
                tr('同じ名前の項目が存在します'),
            )
        try:
            if same_key:
                temporary = FileOperationArtifactPolicy.create_staging_path(
                    source,
                    request.operation_id if request is not None else None,
                    uuid.uuid4().hex,
                )
                os.rename(source, temporary)
                try:
                    os.rename(temporary, destination)
                except BaseException:
                    os.rename(temporary, source)
                    raise
            else:
                os.rename(source, destination)
        except BaseException as exc:
            return self._exception_failure(source, destination, exc)
        if source_stat is not None:
            try:
                destination_stat = os.stat(destination, follow_symlinks=False)
                if destination_stat.st_mtime_ns != source_stat.st_mtime_ns:
                    os.utime(
                        destination,
                        ns=(
                            destination_stat.st_atime_ns,
                            source_stat.st_mtime_ns,
                        ),
                        follow_symlinks=False,
                    )
            except (OSError, NotImplementedError, ValueError) as exc:
                # The rename is already complete. Timestamp restoration is a
                # best-effort compatibility measure and must not misreport a
                # successful filesystem move as a failed rename.
                _LOG.warning(
                    "rename mtime restore failed source=%s destination=%s: %s",
                    source,
                    destination,
                    exc,
                )
        return FileOperationItemResult(
            source,
            destination,
            True,
            state=FileOperationItemState.MOVED,
            destination_exists_after=os.path.lexists(destination),
            source_exists_after=os.path.lexists(source),
            destination_published=os.path.lexists(destination),
            source_removed=not os.path.lexists(source),
        )

    def _transfer_item(
        self,
        request: FileOperationRequest,
        source: str,
        cancelled: Event,
    ) -> FileOperationItemResult:
        while True:
            if cancelled.is_set():
                raise _OperationCancelled
            try:
                return self._transfer_item_attempt(request, source, cancelled)
            except _PublicationCollision:
                # Atomic publication lost a race. Staging has been cleaned;
                # re-enter the same collision policy, including generated names
                # and per-destination resolutions. A move still owns its source.
                continue

    def _transfer_item_attempt(
        self,
        request: FileOperationRequest,
        source: str,
        cancelled: Event,
    ) -> FileOperationItemResult:
        destination_root = (
            self._absolute(request.destination_directory)
            if request.destination_directory
            else ""
        )
        if not destination_root or not os.path.isdir(destination_root):
            return self._failure(
                source,
                destination_root or None,
                FileOperationErrorCode.INVALID_DESTINATION,
                tr('コピー／移動先がフォルダではありません'),
            )
        destination = os.path.join(destination_root, os.path.basename(source))
        if self._path_key(source) == self._path_key(destination):
            resolution = self._collision_resolution(request, destination)
            if (
                request.operation is FileOperationKind.COPY
                and resolution
                in {
                    FileCollisionPolicy.RENAME.value,
                    FileCollisionPolicy.KEEP_BOTH.value,
                }
            ):
                generated = generate_copy_name(
                    os.path.basename(source),
                    self._directory_names(destination_root),
                    is_directory=os.path.isdir(source),
                )
                if generated is None:
                    return self._failure(
                        source,
                        destination,
                        FileOperationErrorCode.COLLISION,
                        tr('衝突しない名前を生成できません'),
                    )
                destination = os.path.join(destination_root, generated)
            else:
                return self._failure(
                    source,
                    destination,
                    FileOperationErrorCode.SAME_PATH,
                    tr('コピー元とコピー先が同じです'),
                )
        if os.path.isdir(source) and self._is_descendant(destination_root, source):
            return self._failure(
                source,
                destination,
                FileOperationErrorCode.DESCENDANT_DESTINATION,
                tr('フォルダ自身の子階層へコピー／移動できません'),
            )
        if self._name_exists(destination):
            resolution = self._collision_resolution(request, destination)
            if resolution in {
                FileCollisionPolicy.RENAME.value,
                FileCollisionPolicy.KEEP_BOTH.value,
            }:
                generated = generate_copy_name(
                    os.path.basename(source),
                    self._directory_names(destination_root),
                    is_directory=os.path.isdir(source),
                )
                if generated is None:
                    return self._failure(
                        source,
                        destination,
                        FileOperationErrorCode.COLLISION,
                        tr('衝突しない名前を生成できません'),
                    )
                destination = os.path.join(destination_root, generated)
            elif resolution == FileCollisionPolicy.CANCEL.value:
                raise _OperationCancelled
            elif resolution == FileCollisionPolicy.REPLACE.value:
                return self._replace_transfer(request, source, destination, cancelled)
            elif (
                resolution == FileCollisionPolicy.MERGE.value
                and os.path.isdir(source)
                and os.path.isdir(destination)
            ):
                return self._merge_transfer(request, source, destination, cancelled)
            else:
                return self._failure(
                    source,
                    destination,
                    FileOperationErrorCode.COLLISION,
                    tr('同名項目があるためスキップしました'),
                )
        try:
            if request.operation is FileOperationKind.COPY:
                self._copy_atomic(source, destination, cancelled)
            else:
                self._move_safe(source, destination, cancelled)
        except _OperationCancelled:
            raise
        except _PublicationCollision:
            raise
        except _SourceDeleteFailed as exc:
            return self._source_removal_failure(
                source,
                destination,
                str(exc),
                operation=request.operation,
            )
        except BaseException as exc:
            return self._exception_failure(
                source,
                destination,
                exc,
                operation=request.operation,
            )
        if request.operation is FileOperationKind.MOVE:
            return self._move_postcondition(
                source,
                destination,
                operation=request.operation,
            )
        return self._copy_success(
            source,
            destination,
            operation=request.operation,
        )

    def _replace_transfer(
        self,
        request: FileOperationRequest,
        source: str,
        destination: str,
        cancelled: Event,
    ) -> FileOperationItemResult:
        destination_existed_before = os.path.lexists(destination)
        try:
            if request.operation is FileOperationKind.COPY:
                self._copy_atomic_replace(source, destination, cancelled)
            else:
                self._move_replace(source, destination, cancelled)
        except _OperationCancelled:
            raise
        except _SourceDeleteFailed as exc:
            return self._source_removal_failure(
                source,
                destination,
                str(exc),
                operation=request.operation,
                replaced_existing=True,
                destination_existed_before=destination_existed_before,
            )
        except BaseException as exc:
            failed = self._exception_failure(
                source,
                destination,
                exc,
                operation=request.operation,
            )
            return replace(
                failed,
                replaced_existing=bool(
                    destination_existed_before
                    and getattr(exc, "published", False)
                ),
                destination_existed_before=destination_existed_before,
            )
        if request.operation is FileOperationKind.MOVE:
            return replace(
                self._move_postcondition(
                    source,
                    destination,
                    operation=request.operation,
                ),
                replaced_existing=True,
                destination_existed_before=destination_existed_before,
            )
        return self._copy_success(
            source,
            destination,
            operation=request.operation,
            replaced_existing=True,
            destination_existed_before=destination_existed_before,
        )

    def _merge_transfer(
        self,
        request: FileOperationRequest,
        source: str,
        destination: str,
        cancelled: Event,
    ) -> FileOperationItemResult:
        children = self._copy_directory_merge(
            source,
            destination,
            cancelled,
            move=request.operation is FileOperationKind.MOVE,
            request=request,
        )
        root_remove_error: str | None = None
        if request.operation is FileOperationKind.MOVE and os.path.lexists(source):
            try:
                os.rmdir(source)
            except OSError as exc:
                root_remove_error = str(exc)
        return self._aggregate_merge_result(
            source,
            destination,
            request.operation,
            children,
            root_remove_error=root_remove_error,
        )

    def _recycle_item(self, source: str) -> FileOperationItemResult:
        result = self.recycle_bin.recycle(source)
        if result.success:
            return FileOperationItemResult(
                source,
                None,
                True,
                state=FileOperationItemState.MOVED,
                source_exists_after=os.path.lexists(source),
                source_removed=not os.path.lexists(source),
            )
        if result.cancelled:
            code = FileOperationErrorCode.CANCELLED
        elif result.error_code == "api_unavailable":
            code = FileOperationErrorCode.API_UNAVAILABLE
        else:
            code = FileOperationErrorCode.IO_ERROR
        return self._failure(
            source,
            None,
            code,
            result.error_message or tr('ごみ箱へ移動できません'),
        )

    def _create_directory(
        self,
        request: FileOperationRequest,
        cancelled: Event,
        progress: ProgressCallback | None,
    ) -> FileOperationResult:
        parent = (
            self._absolute(request.destination_directory)
            if request.destination_directory
            else ""
        )
        validation = validate_windows_filename(request.new_name or "")
        if cancelled.is_set():
            return FileOperationResult(
                request.operation,
                (),
                cancelled=True,
                request_id=request.request_id,
                operation_id=request.operation_id,
            )
        if not validation.valid:
            item = self._failure(
                None,
                None,
                FileOperationErrorCode.INVALID_NAME,
                validation.error_message or tr('名前が無効です'),
            )
        elif not os.path.isdir(parent):
            item = self._failure(
                None,
                parent or None,
                FileOperationErrorCode.INVALID_DESTINATION,
                tr('作成先がフォルダではありません'),
            )
        else:
            destination = os.path.join(parent, validation.normalized_name)
            if self._name_exists(destination):
                item = self._failure(
                    None,
                    destination,
                    FileOperationErrorCode.COLLISION,
                    tr('同じ名前の項目が存在します'),
                )
            else:
                try:
                    os.mkdir(destination)
                    item = FileOperationItemResult(
                        None,
                        destination,
                        True,
                        operation=request.operation,
                        destination_exists_after=True,
                        destination_published=True,
                        published_destination_paths=(destination,),
                    )
                except BaseException as exc:
                    item = self._exception_failure(None, destination, exc)
        if progress is not None:
            progress(
                FileOperationProgress(
                    request.request_id,
                    request.operation,
                    1,
                    1,
                    operation_id=request.operation_id,
                )
            )
        return FileOperationResult(
            request.operation,
            (item,),
            request_id=request.request_id,
            operation_id=request.operation_id,
        )

    def _create_zip(
        self,
        request: FileOperationRequest,
        cancelled: Event,
        progress: ProgressCallback | None,
    ) -> FileOperationResult:
        destination: str | None = None
        temporary: str | None = None
        owns_temporary = False
        published = False
        copied_bytes = 0
        last_progress = 0.0

        def check_cancel() -> None:
            if cancelled.is_set():
                raise _OperationCancelled

        def write_entry(archive: zipfile.ZipFile, source: str, name: str) -> None:
            nonlocal copied_bytes, last_progress
            check_cancel()
            if FileOperationArtifactPolicy.is_internal_operation_artifact(source):
                raise OSError(tr('未完了の一時ファイルはZIPに含められません'))
            source_stat = os.lstat(source)
            if stat.S_ISLNK(source_stat.st_mode) or self._is_reparse_path(source):
                raise OSError(tr('リンクやジャンクションはZIPに含められません'))
            if stat.S_ISDIR(source_stat.st_mode):
                archive.writestr(name + "/", b"")
                with os.scandir(source) as entries:
                    for entry in entries:
                        write_entry(archive, entry.path, name + "/" + entry.name)
            elif stat.S_ISREG(source_stat.st_mode):
                info = zipfile.ZipInfo.from_file(source, name, strict_timestamps=False)
                info.compress_type = zipfile.ZIP_DEFLATED
                with (
                    open(source, "rb") as reader,
                    archive.open(info, "w", force_zip64=True) as writer,
                ):
                    while True:
                        check_cancel()
                        chunk = reader.read(1024 * 1024)
                        if not chunk:
                            break
                        writer.write(chunk)
                        copied_bytes += len(chunk)
                        now = time.monotonic()
                        if progress is not None and now - last_progress >= 0.1:
                            last_progress = now
                            progress(FileOperationProgress(
                                request.request_id, request.operation, 0, 1,
                                source, destination,
                                bytes_completed=copied_bytes,
                                bytes_total=request.planned_total_bytes,
                                operation_id=request.operation_id,
                            ))
            else:
                raise OSError(tr('通常のファイルまたはフォルダだけをZIPにできます'))

        try:
            check_cancel()
            validation = validate_windows_filename(request.new_name or "")
            if not validation.valid:
                raise ValueError(validation.error_message or tr('名前が無効です'))
            parent = self._absolute(request.destination_directory)
            if not request.destination_directory or not os.path.isdir(parent):
                raise NotADirectoryError(tr('作成先がフォルダではありません'))
            destination = os.path.join(parent, validation.normalized_name)
            sources = self._prepare_sources(request.source_paths)
            if not sources:
                raise ValueError(tr('圧縮する項目が選択されていません'))
            # Browser selections are direct children of the output folder.
            # Enforce that boundary so the archive cannot include its own staging file.
            if any(
                self._path_key(os.path.dirname(source)) != self._path_key(parent)
                for source in sources
            ):
                raise ValueError(tr('現在のフォルダ内の項目を選択してください'))
            temporary = FileOperationArtifactPolicy.create_staging_path(destination)
            with open(temporary, "xb") as output:
                owns_temporary = True
                with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for source in sources:
                        write_entry(archive, source, os.path.basename(source))
            stem, suffix = os.path.splitext(validation.normalized_name)
            number = 0
            while True:
                check_cancel()
                name = (
                    validation.normalized_name if number == 0
                    else f"{stem} ({number}){suffix}"
                )
                if not validate_windows_filename(name).valid:
                    raise ValueError(tr('名前が長すぎます'))
                destination = os.path.join(parent, name)
                number += 1
                if self._name_exists(destination):
                    continue
                try:
                    if os.name == "nt":
                        os.rename(temporary, destination)  # Windows: never replaces.
                    else:
                        os.link(temporary, destination)  # POSIX: never replaces.
                    break
                except FileExistsError:
                    # Another operation may publish after the name check.
                    continue
            published = True
            item = FileOperationItemResult(
                None, destination, True, operation=request.operation,
                destination_exists_after=True, destination_published=True,
                copied_bytes=copied_bytes, published_destination_paths=(destination,),
            )
        except _OperationCancelled:
            item = self._failure(
                None, destination, FileOperationErrorCode.CANCELLED,
                tr('操作がキャンセルされました'), operation=request.operation,
            )
        except FileExistsError as exc:
            item = self._failure(
                None, destination, FileOperationErrorCode.COLLISION,
                str(exc), operation=request.operation,
            )
        except ValueError as exc:
            item = self._failure(
                None, destination, FileOperationErrorCode.INVALID_NAME,
                str(exc), operation=request.operation,
            )
        except BaseException as exc:
            item = self._exception_failure(None, destination, exc, operation=request.operation)
        finally:
            if owns_temporary and temporary is not None:
                cleanup = FileOperationArtifactPolicy.cleanup_staging_path(temporary)
                if not cleanup.removed:
                    item = replace(
                        item, success=False,
                        error_code=FileOperationErrorCode.ARTIFACT_CLEANUP_FAILED.value,
                        error_message=cleanup.error_message,
                        artifact_paths=(temporary,),
                        cleanup_errors=(cleanup.error_message or "cleanup failed",),
                        destination_published=published,
                        partial_success=published,
                        state=FileOperationItemState.FAILED,
                        lifecycle_state=FileOperationLifecycleState.FAILED,
                    )
        if progress is not None and item.success:
            progress(FileOperationProgress(
                request.request_id, request.operation, 1, 1,
                destination_path=destination, bytes_completed=copied_bytes,
                bytes_total=copied_bytes, operation_id=request.operation_id,
            ))
        return FileOperationResult(
            request.operation, (item,),
            cancelled=item.error_code == FileOperationErrorCode.CANCELLED.value,
            request_id=request.request_id, operation_id=request.operation_id,
        )

    def _copy_atomic(
        self, source: str, destination: str, cancelled: Event,
        *, replace_existing: bool = False,
    ) -> bool:
        temporary = FileOperationArtifactPolicy.create_staging_path(
            destination,
            uuid.uuid4().hex,
            uuid.uuid4().hex,
        )
        published = False
        try:
            if os.path.isdir(source):
                self._copy_directory(source, temporary, cancelled)
            else:
                self.file_copier.copy_to_staging(
                    source,
                    temporary,
                    cancelled=cancelled,
                    progress=self._byte_progress,
                )
            if cancelled.is_set():
                raise _OperationCancelled
            if not os.path.lexists(temporary):
                raise OSError(tr('stagingの完成を確認できません'))
            if replace_existing:
                os.replace(temporary, destination)
            else:
                # Windows rename is atomically non-replacing, for files and
                # directories alike (as in ChunkedFileCopier.copy).
                try:
                    os.rename(temporary, destination)
                except FileExistsError as exc:
                    raise _PublicationCollision(*exc.args) from exc
            published = True
            if (
                not os.path.lexists(destination)
                or os.path.lexists(temporary)
            ):
                raise OSError(tr('publish後の事後条件を満たしていません'))
            return True
        except CopyCancelled as exc:
            cleanup = FileOperationArtifactPolicy.cleanup_staging_path(temporary)
            if not cleanup.removed:
                raise _ArtifactOperationError(
                    tr('キャンセル後にstagingを回収できません'),
                    artifact_path=temporary,
                    cleanup=cleanup,
                    published=published,
                ) from exc
            raise _OperationCancelled from exc
        except _OperationCancelled as exc:
            cleanup = FileOperationArtifactPolicy.cleanup_staging_path(temporary)
            if not cleanup.removed:
                raise _ArtifactOperationError(
                    tr('キャンセル後にstagingを回収できません'),
                    artifact_path=temporary,
                    cleanup=cleanup,
                    published=published,
                ) from exc
            raise
        except BaseException as exc:
            cleanup = FileOperationArtifactPolicy.cleanup_staging_path(temporary)
            if not cleanup.removed:
                raise _ArtifactOperationError(
                    tr('publishに失敗し、stagingも回収できません: {p0}', p0=exc),
                    artifact_path=temporary,
                    cleanup=cleanup,
                    published=published,
                ) from exc
            raise

    def _copy_atomic_replace(
        self,
        source: str,
        destination: str,
        cancelled: Event,
    ) -> None:
        if not os.path.isdir(source):
            self._copy_atomic(source, destination, cancelled, replace_existing=True)
            return
        temporary = FileOperationArtifactPolicy.create_staging_path(
            destination,
            uuid.uuid4().hex,
            uuid.uuid4().hex,
        )
        backup = FileOperationArtifactPolicy.create_staging_path(
            destination,
            uuid.uuid4().hex,
            uuid.uuid4().hex,
        )
        published = False
        rollback_error: OSError | None = None
        try:
            self._copy_directory(source, temporary, cancelled)
            if cancelled.is_set():
                raise _OperationCancelled
            os.replace(destination, backup)
            try:
                os.replace(temporary, destination)
                published = True
            except BaseException:
                try:
                    os.replace(backup, destination)
                except OSError as exc:
                    rollback_error = exc
                raise
            cleanup = FileOperationArtifactPolicy.cleanup_staging_path(backup)
            if not cleanup.removed:
                raise _ArtifactOperationError(
                    tr('置換後のbackup artifactを回収できません'),
                    artifact_path=backup,
                    cleanup=cleanup,
                    published=True,
                )
            if (
                not os.path.lexists(destination)
                or os.path.lexists(temporary)
            ):
                raise OSError(tr('置換publish後の事後条件を満たしていません'))
        except BaseException as exc:
            temporary_cleanup = (
                FileOperationArtifactPolicy.cleanup_staging_path(temporary)
            )
            if os.path.lexists(backup) and not os.path.lexists(destination):
                try:
                    os.replace(backup, destination)
                except OSError as retry_error:
                    rollback_error = retry_error
            if (
                os.path.lexists(backup)
                and not os.path.lexists(destination)
            ):
                rollback_message = (
                    tr('backup復元に失敗しました: {p0}', p0=rollback_error)
                    if rollback_error is not None
                    else tr('backup復元後の事後条件を満たしていません')
                )
                rollback_cleanup = ArtifactCleanupResult(
                    backup,
                    False,
                    rollback_message,
                )
                artifact_paths = [backup]
                cleanups = [rollback_cleanup]
                if not temporary_cleanup.removed:
                    cleanups.append(temporary_cleanup)
                    if os.path.lexists(temporary):
                        artifact_paths.append(temporary)
                raise _ArtifactOperationError(
                    tr('置換publishに失敗し、backupを復元できません: {p0}', p0=exc),
                    artifact_paths=tuple(artifact_paths),
                    cleanups=tuple(cleanups),
                    published=published,
                ) from (rollback_error or exc)
            if isinstance(exc, _ArtifactOperationError):
                raise
            if not temporary_cleanup.removed:
                raise _ArtifactOperationError(
                    tr('置換に失敗し、stagingも回収できません: {p0}', p0=exc),
                    artifact_path=temporary,
                    cleanup=temporary_cleanup,
                    published=published,
                ) from exc
            raise

    def _copy_directory(self, source: str, destination: str, cancelled: Event) -> None:
        if cancelled.is_set():
            raise _OperationCancelled
        os.mkdir(destination)
        with os.scandir(source) as entries:
            for entry in entries:
                if cancelled.is_set():
                    raise _OperationCancelled
                child_source = entry.path
                child_destination = os.path.join(destination, entry.name)
                if self._is_reparse_path(child_source):
                    raise OSError(
                        errno.ELOOP,
                        tr('再解析ポイントはコピーできません'),
                        child_source,
                    )
                if entry.is_dir(follow_symlinks=False):
                    self._copy_directory(child_source, child_destination, cancelled)
                else:
                    try:
                        self.file_copier.copy_to_staging(
                            child_source,
                            child_destination,
                            cancelled=cancelled,
                            progress=self._byte_progress,
                        )
                    except CopyCancelled as exc:
                        raise _OperationCancelled from exc
        shutil.copystat(source, destination, follow_symlinks=False)

    def _copy_directory_merge(
        self,
        source: str,
        destination: str,
        cancelled: Event,
        *,
        move: bool,
        request: FileOperationRequest,
    ) -> tuple[FileOperationItemResult, ...]:
        results: list[FileOperationItemResult] = []
        try:
            with os.scandir(source) as scanned:
                entries = tuple(scanned)
        except BaseException as exc:
            return (
                self._exception_failure(
                    source,
                    destination,
                    exc,
                    operation=request.operation,
                ),
            )
        for entry in entries:
            child_source = entry.path
            child_destination = os.path.join(destination, entry.name)
            if cancelled.is_set():
                results.append(
                    self._failure(
                        child_source,
                        child_destination,
                        FileOperationErrorCode.CANCELLED,
                        tr('操作がキャンセルされました'),
                        operation=request.operation,
                    )
                )
                break
            try:
                result = self._merge_child(
                    request,
                    child_source,
                    child_destination,
                    entry.is_dir(follow_symlinks=False),
                    cancelled,
                    move=move,
                )
            except _OperationCancelled:
                result = self._failure(
                    child_source,
                    child_destination,
                    FileOperationErrorCode.CANCELLED,
                    tr('操作がキャンセルされました'),
                    operation=request.operation,
                )
            except BaseException as exc:
                result = self._exception_failure(
                    child_source,
                    child_destination,
                    exc,
                    operation=request.operation,
                )
            results.append(result)
            if result.state is FileOperationItemState.CANCELLED:
                break
        return tuple(results)

    def _merge_child(
        self,
        request: FileOperationRequest,
        child_source: str,
        child_destination: str,
        is_directory: bool,
        cancelled: Event,
        *,
        move: bool,
    ) -> FileOperationItemResult:
        while True:
            if cancelled.is_set():
                raise _OperationCancelled
            try:
                return self._merge_child_attempt(
                    request, child_source, child_destination, is_directory,
                    cancelled, move=move,
                )
            except _PublicationCollision:
                continue

    def _merge_child_attempt(
        self,
        request: FileOperationRequest,
        child_source: str,
        child_destination: str,
        is_directory: bool,
        cancelled: Event,
        *,
        move: bool,
    ) -> FileOperationItemResult:
        if self._is_reparse_path(child_source):
            return self._failure(
                child_source,
                child_destination,
                FileOperationErrorCode.IO_ERROR,
                tr('再解析ポイントはマージできません'),
                operation=request.operation,
            )
        if not os.path.lexists(child_destination):
            try:
                if move:
                    self._move_safe(child_source, child_destination, cancelled)
                    return self._move_postcondition(
                        child_source,
                        child_destination,
                        operation=request.operation,
                    )
                self._copy_atomic(child_source, child_destination, cancelled)
                return self._copy_success(
                    child_source,
                    child_destination,
                    operation=request.operation,
                )
            except _SourceDeleteFailed as exc:
                return self._source_removal_failure(
                    child_source,
                    child_destination,
                    str(exc),
                    operation=request.operation,
                )
        resolution = self._collision_resolution(request, child_destination)
        if (
            is_directory
            and os.path.isdir(child_destination)
            and resolution == FileCollisionPolicy.MERGE.value
        ):
            children = self._copy_directory_merge(
                child_source,
                child_destination,
                cancelled,
                move=move,
                request=request,
            )
            remove_error: str | None = None
            if move and os.path.lexists(child_source):
                try:
                    os.rmdir(child_source)
                except OSError as exc:
                    remove_error = str(exc)
            return self._aggregate_merge_result(
                child_source,
                child_destination,
                request.operation,
                children,
                root_remove_error=remove_error,
            )
        if resolution == FileCollisionPolicy.REPLACE.value:
            return self._replace_transfer(
                request,
                child_source,
                child_destination,
                cancelled,
            )
        if resolution in {
            FileCollisionPolicy.RENAME.value,
            FileCollisionPolicy.KEEP_BOTH.value,
        }:
            generated = generate_copy_name(
                os.path.basename(child_source),
                self._directory_names(os.path.dirname(child_destination)),
                is_directory=is_directory,
            )
            if generated is None:
                return self._failure(
                    child_source,
                    child_destination,
                    FileOperationErrorCode.COLLISION,
                    tr('衝突しない名前を生成できません'),
                    operation=request.operation,
                )
            target = os.path.join(os.path.dirname(child_destination), generated)
            try:
                if move:
                    self._move_safe(child_source, target, cancelled)
                    return self._move_postcondition(
                        child_source,
                        target,
                        operation=request.operation,
                    )
                self._copy_atomic(child_source, target, cancelled)
                return self._copy_success(
                    child_source,
                    target,
                    operation=request.operation,
                )
            except _SourceDeleteFailed as exc:
                return self._source_removal_failure(
                    child_source,
                    target,
                    str(exc),
                    operation=request.operation,
                )
        if resolution == FileCollisionPolicy.CANCEL.value:
            raise _OperationCancelled
        return self._failure(
            child_source,
            child_destination,
            FileOperationErrorCode.COLLISION,
            tr('同名項目があるためスキップしました'),
            operation=request.operation,
        )

    def _aggregate_merge_result(
        self,
        source: str,
        destination: str,
        operation: FileOperationKind,
        child_results: tuple[FileOperationItemResult, ...],
        *,
        root_remove_error: str | None,
    ) -> FileOperationItemResult:
        leaves: list[FileOperationItemResult] = []
        for child in child_results:
            leaves.extend(child.leaf_results())
        published = tuple(
            item.destination_path
            for item in leaves
            if item.destination_published and item.destination_path
        )
        moved = tuple(
            item.source_path
            for item in leaves
            if item.success
            and item.state is FileOperationItemState.MOVED
            and item.source_path
        )
        skipped = tuple(
            item.source_path
            for item in leaves
            if item.state is FileOperationItemState.SKIPPED and item.source_path
        )
        failed = tuple(
            item.source_path
            for item in leaves
            if not item.success
            and item.state is not FileOperationItemState.SKIPPED
            and item.source_path
        )
        source_exists = os.path.lexists(source)
        destination_exists = os.path.lexists(destination)
        residual = self._residual_source_paths(source)
        retry = self._immediate_residual_paths(source)
        cancelled = any(
            item.state is FileOperationItemState.CANCELLED for item in leaves
        )
        if operation is FileOperationKind.MOVE:
            complete = (
                all(item.success for item in leaves)
                and not source_exists
                and not residual
                and not skipped
                and not failed
                and not cancelled
            )
            state = (
                FileOperationItemState.MOVED
                if complete
                else (
                    FileOperationItemState.CANCELLED
                    if cancelled and not published
                    else FileOperationItemState.DESTINATION_PUBLISHED_SOURCE_REMAINS
                    if destination_exists and source_exists
                    else FileOperationItemState.SOURCE_REMOVAL_FAILED
                    if root_remove_error
                    else FileOperationItemState.FAILED
                )
            )
        else:
            complete = (
                all(item.success for item in leaves)
                and not skipped
                and not failed
                and not cancelled
            )
            state = (
                FileOperationItemState.COPIED
                if complete
                else FileOperationItemState.CANCELLED
                if cancelled and not published
                else FileOperationItemState.FAILED
            )
        partial = bool(published or moved) and not complete
        if complete:
            code = None
            message = None
        elif partial:
            code = FileOperationErrorCode.PARTIAL_SUCCESS.value
            message = (
                tr('フォルダの一部だけを処理しました')
                + (f": {root_remove_error}" if root_remove_error else "")
            )
        elif cancelled:
            code = FileOperationErrorCode.CANCELLED.value
            message = tr('操作がキャンセルされました')
        else:
            code = FileOperationErrorCode.IO_ERROR.value
            message = root_remove_error or tr('フォルダを統合できませんでした')
        return FileOperationItemResult(
            source,
            destination,
            complete,
            code,
            message,
            partial,
            state=state,
            destination_exists_after=destination_exists,
            source_exists_after=source_exists,
            destination_published=bool(published) or destination_exists,
            source_removed=not source_exists,
            residual_source_paths=residual,
            operation=operation,
            destination_existed_before=True,
            child_results=child_results,
            published_destination_paths=published,
            moved_source_paths=moved,
            skipped_source_paths=skipped,
            failed_source_paths=failed,
            retry_source_paths=retry,
            source_root_removed=not source_exists,
            partially_completed=partial,
        )

    def _move_safe(self, source: str, destination: str, cancelled: Event) -> None:
        if cancelled.is_set():
            raise _OperationCancelled
        try:
            os.rename(source, destination)
            if os.path.lexists(source) or not os.path.lexists(destination):
                raise OSError(tr('移動後の事後条件を満たしていません'))
            if _LOG.isEnabledFor(logging.DEBUG):
                _LOG.debug(
                    "same-volume rename completed source=%s destination=%s "
                    "source_exists=%s destination_exists=%s",
                    source,
                    destination,
                    os.path.lexists(source),
                    os.path.lexists(destination),
                )
            return
        except FileExistsError as exc:
            raise _PublicationCollision(*exc.args) from exc
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
        source_receipt = self._capture_source_receipt(source)
        published = self._copy_atomic(source, destination, cancelled)
        if cancelled.is_set():
            if published is not True and os.path.lexists(destination):
                # A copier that does not return the explicit postcondition
                # receipt may have left an unpublished output. Roll back only
                # that unverified legacy boundary; production publishes return
                # True after final-exists/staging-missing checks.
                self._remove_source(destination)
                raise _OperationCancelled
            raise _SourceDeleteFailed(
                tr('移動先は完成しましたが、キャンセルにより元項目を残しました')
            )
        try:
            if _LOG.isEnabledFor(logging.DEBUG):
                _LOG.debug(
                    "cross-volume source delete attempted source=%s destination=%s",
                    source,
                    destination,
                )
            self._remove_source_receipt(source, source_receipt, cancelled)
        except OSError as exc:
            if _LOG.isEnabledFor(logging.DEBUG):
                _LOG.debug(
                    "cross-volume source delete failed source=%s destination=%s error=%r",
                    source,
                    destination,
                    exc,
                )
            raise _SourceDeleteFailed(
                tr('コピーは完了しましたがコピー元を削除できませんでした: {p0}', p0=exc)
            ) from exc
        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "cross-volume source delete completed source=%s "
                "source_exists=%s destination_exists=%s",
                source,
                os.path.lexists(source),
                os.path.lexists(destination),
            )
        if os.path.lexists(source):
            raise _SourceDeleteFailed(
                tr('コピーは完了しましたがコピー元が残っています')
            )
        if not os.path.lexists(destination):
            raise OSError(tr('移動先の完成を確認できません'))

    def _move_replace(
        self,
        source: str,
        destination: str,
        cancelled: Event,
    ) -> None:
        if cancelled.is_set():
            raise _OperationCancelled
        if not os.path.isdir(source) and not os.path.isdir(destination):
            try:
                os.replace(source, destination)
                if os.path.lexists(source) or not os.path.lexists(destination):
                    raise OSError(tr('置換後の事後条件を満たしていません'))
                return
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
        source_receipt = self._capture_source_receipt(source)
        self._copy_atomic_replace(source, destination, cancelled)
        if cancelled.is_set():
            raise _SourceDeleteFailed(
                tr('置換先は公開済みですが、キャンセルにより元項目を残しました')
            )
        try:
            if _LOG.isEnabledFor(logging.DEBUG):
                _LOG.debug(
                    "replace source delete attempted source=%s destination=%s",
                    source,
                    destination,
                )
            self._remove_source_receipt(source, source_receipt, cancelled)
        except OSError as exc:
            if _LOG.isEnabledFor(logging.DEBUG):
                _LOG.debug(
                    "replace source delete failed source=%s destination=%s error=%r",
                    source,
                    destination,
                    exc,
                )
            raise _SourceDeleteFailed(
                tr('置換は完了しましたがコピー元を削除できませんでした: {p0}', p0=exc)
            ) from exc
        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "replace source delete completed source=%s "
                "source_exists=%s destination_exists=%s",
                source,
                os.path.lexists(source),
                os.path.lexists(destination),
            )
        if os.path.lexists(source):
            raise _SourceDeleteFailed(
                tr('置換は完了しましたがコピー元が残っています')
            )
        if not os.path.lexists(destination):
            raise OSError(tr('置換先の完成を確認できません'))

    @staticmethod
    def _remove_source(source: str) -> None:
        if os.path.isdir(source):
            shutil.rmtree(source)
        else:
            os.unlink(source)

    @classmethod
    def _capture_source_receipt(
        cls,
        source: str,
    ) -> tuple[_SourceReceiptEntry, ...]:
        """Record the copied source tree before a cross-volume move.

        A later cleanup may remove only these exact objects. Reparse points and
        platforms without stable file identities fail closed; same-volume
        rename paths never pay this enumeration cost.
        """
        receipt: list[_SourceReceiptEntry] = []

        def visit(path: str, relative_path: str) -> None:
            value = os.lstat(path)
            attributes = int(getattr(value, "st_file_attributes", 0))
            reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
            if stat.S_ISLNK(value.st_mode) or attributes & reparse_flag:
                raise OSError(errno.ELOOP, tr('再解析ポイントの移動は安全に確認できません'), path)
            kind = (
                "directory" if stat.S_ISDIR(value.st_mode)
                else "file" if stat.S_ISREG(value.st_mode)
                else "other"
            )
            device = int(getattr(value, "st_dev", 0))
            inode = int(getattr(value, "st_ino", 0))
            if kind == "other" or device == 0 or inode == 0:
                raise OSError(errno.ENOTSUP, tr('元項目の同一性を確認できません'), path)
            receipt.append(
                _SourceReceiptEntry(
                    relative_path,
                    kind,
                    device,
                    inode,
                    int(value.st_size),
                    int(getattr(value, "st_mtime_ns", 0)),
                    int(getattr(value, "st_ctime_ns", 0)),
                )
            )
            if kind == "directory":
                with os.scandir(path) as children:
                    names = sorted(entry.name for entry in children)
                for name in names:
                    child_relative = (
                        name if not relative_path
                        else os.path.join(relative_path, name)
                    )
                    visit(os.path.join(path, name), child_relative)

        visit(source, "")
        return tuple(sorted(receipt, key=lambda item: item.relative_path))

    @classmethod
    def _remove_source_receipt(
        cls,
        source: str,
        receipt: tuple[_SourceReceiptEntry, ...],
        cancelled: Event,
    ) -> None:
        """Remove only receipt-matched objects, never recursively delete a root."""
        current = cls._capture_source_receipt(source)
        if current != receipt:
            raise OSError(errno.EBUSY, tr('コピー中に元項目が変更されたため残しました'), source)

        expected = {item.relative_path: item for item in receipt}
        ordered = sorted(
            receipt,
            key=lambda item: (
                item.relative_path.count(os.sep),
                item.kind != "directory",
                item.relative_path,
            ),
            reverse=True,
        )
        for entry in ordered:
            if cancelled.is_set():
                raise OSError(errno.ECANCELED, tr('キャンセルにより元項目を残しました'), source)
            path = (
                source if not entry.relative_path
                else os.path.join(source, entry.relative_path)
            )
            parts = Path(entry.relative_path).parts
            ancestors = [""]
            for count in range(1, len(parts)):
                ancestors.append(os.path.join(*parts[:count]))
            for relative_parent in ancestors:
                parent_entry = expected.get(relative_parent)
                parent_path = (
                    source if not relative_parent
                    else os.path.join(source, relative_parent)
                )
                try:
                    parent_stat = os.lstat(parent_path)
                except FileNotFoundError:
                    continue
                if (
                    parent_entry is None
                    or not stat.S_ISDIR(parent_stat.st_mode)
                    or int(getattr(parent_stat, "st_dev", 0)) != parent_entry.device
                    or int(getattr(parent_stat, "st_ino", 0)) != parent_entry.inode
                ):
                    raise OSError(errno.EBUSY, tr('元フォルダが置き換わったため残しました'), parent_path)
            try:
                value = os.lstat(path)
            except FileNotFoundError:
                continue
            same_identity = (
                int(getattr(value, "st_dev", 0)) == entry.device
                and int(getattr(value, "st_ino", 0)) == entry.inode
                and (
                    stat.S_ISDIR(value.st_mode)
                    if entry.kind == "directory"
                    else stat.S_ISREG(value.st_mode)
                )
            )
            same_content_stamp = entry.kind == "directory" or (
                int(value.st_size) == entry.size
                and int(getattr(value, "st_mtime_ns", 0)) == entry.mtime_ns
                and int(getattr(value, "st_ctime_ns", 0)) == entry.ctime_ns
            )
            if not same_identity or not same_content_stamp:
                raise OSError(errno.EBUSY, tr('コピー元の項目が置き換わったため残しました'), path)
            if entry.kind == "directory":
                os.rmdir(path)
            else:
                os.unlink(path)
        if os.path.lexists(source):
            raise OSError(errno.ENOTEMPTY, tr('コピー元に未確認の項目が残っています'), source)

    @staticmethod
    def _is_reparse_path(path: str) -> bool:
        path_stat = os.lstat(path)
        attributes = int(getattr(path_stat, "st_file_attributes", 0))
        flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        return stat.S_ISLNK(path_stat.st_mode) or bool(attributes & flag)

    def _collision_resolution(
        self,
        request: FileOperationRequest,
        destination: str,
    ) -> str:
        destination_key = self._path_key(destination)
        for path, resolution in request.collision_resolutions:
            if self._path_key(path) == destination_key:
                return str(resolution)
        return request.collision_policy.value

    def _prepare_sources(self, paths: Iterable[str]) -> tuple[str, ...]:
        unique: list[str] = []
        seen: set[str] = set()
        for raw_path in paths:
            absolute = self._absolute(raw_path)
            key = self._path_key(absolute)
            if key in seen:
                continue
            seen.add(key)
            unique.append(absolute)
        unique.sort(key=lambda path: (len(Path(path).parts), self._path_key(path)))
        prepared: list[str] = []
        for candidate in unique:
            if any(
                os.path.isdir(parent) and self._is_descendant(candidate, parent)
                for parent in prepared
            ):
                continue
            prepared.append(candidate)
        return tuple(prepared)

    @staticmethod
    def _absolute(path: str | Path | None) -> str:
        if path is None:
            return ""
        return os.path.abspath(os.path.normpath(os.path.expanduser(os.fspath(path))))

    @staticmethod
    def _path_key(path: str | Path) -> str:
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path)))
        ).casefold()

    @classmethod
    def _is_descendant(cls, candidate: str, root: str) -> bool:
        candidate_key = cls._path_key(candidate)
        root_key = cls._path_key(root)

        def is_below(candidate_value: str, root_value: str) -> bool:
            try:
                common = os.path.commonpath((candidate_value, root_value))
            except ValueError:
                return False
            return common == root_value and candidate_value != root_value

        if is_below(candidate_key, root_key):
            return True
        try:
            resolved_candidate = cls._path_key(os.path.realpath(candidate))
            resolved_root = cls._path_key(os.path.realpath(root))
        except (OSError, RuntimeError, ValueError):
            # A destination that cannot be resolved with certainty is unsafe
            # for recursive publication into a source directory.
            return True
        return is_below(resolved_candidate, resolved_root)

    @classmethod
    def _name_exists(cls, path: str, *, ignore_path: str | None = None) -> bool:
        target_name = os.path.basename(path).casefold()
        ignore_entry = os.path.abspath(ignore_path) if ignore_path else None
        try:
            with os.scandir(os.path.dirname(path)) as entries:
                for entry in entries:
                    if entry.name.casefold() != target_name:
                        continue
                    # Ignore only the actual source entry, not another object
                    # with the same folded name in a case-sensitive directory.
                    if ignore_entry is not None and os.path.abspath(entry.path) == ignore_entry:
                        continue
                    return True
        except OSError:
            return os.path.lexists(path)
        return False

    @staticmethod
    def _directory_names(path: str) -> tuple[str, ...]:
        try:
            with os.scandir(path) as entries:
                return tuple(entry.name for entry in entries)
        except OSError:
            return ()

    @staticmethod
    def _temporary_sibling(path: str) -> str:
        return FileOperationArtifactPolicy.create_staging_path(
            path,
            uuid.uuid4().hex,
            uuid.uuid4().hex,
        )

    @staticmethod
    def _remove_temporary(path: str) -> None:
        FileOperationArtifactPolicy.cleanup_staging_path(path)

    @staticmethod
    def _failure(
        source: str | None,
        destination: str | None,
        code: FileOperationErrorCode,
        message: str,
        *,
        operation: FileOperationKind | None = None,
    ) -> FileOperationItemResult:
        state = (
            FileOperationItemState.CANCELLED
            if code is FileOperationErrorCode.CANCELLED
            else (
                FileOperationItemState.SKIPPED
                if code
                in {
                    FileOperationErrorCode.COLLISION,
                    FileOperationErrorCode.SAME_PATH,
                }
                else FileOperationItemState.FAILED
            )
        )
        return FileOperationItemResult(
            source,
            destination,
            False,
            code.value,
            message,
            state=state,
            destination_exists_after=(
                os.path.lexists(destination) if destination else None
            ),
            source_exists_after=(os.path.lexists(source) if source else None),
            operation=operation,
            lifecycle_state=(
                FileOperationLifecycleState.CANCELLED
                if code is FileOperationErrorCode.CANCELLED
                else FileOperationLifecycleState.FAILED
            ),
        )

    @staticmethod
    def _copy_success(
        source: str,
        destination: str,
        *,
        operation: FileOperationKind | None = FileOperationKind.COPY,
        replaced_existing: bool = False,
        destination_existed_before: bool = False,
    ) -> FileOperationItemResult:
        return FileOperationItemResult(
            source,
            destination,
            True,
            state=FileOperationItemState.COPIED,
            destination_exists_after=os.path.lexists(destination),
            source_exists_after=os.path.lexists(source),
            destination_published=os.path.lexists(destination),
            source_removed=False,
            operation=operation,
            replaced_existing=replaced_existing,
            destination_existed_before=destination_existed_before,
            published_destination_paths=(destination,),
            lifecycle_state=FileOperationLifecycleState.COMPLETED,
        )

    @classmethod
    def _move_postcondition(
        cls,
        source: str,
        destination: str,
        *,
        operation: FileOperationKind | None = FileOperationKind.MOVE,
    ) -> FileOperationItemResult:
        source_exists = os.path.lexists(source)
        destination_exists = os.path.lexists(destination)
        if destination_exists and not source_exists:
            return FileOperationItemResult(
                source,
                destination,
                True,
                state=FileOperationItemState.MOVED,
                destination_exists_after=True,
                source_exists_after=False,
                destination_published=True,
                source_removed=True,
                operation=operation,
                published_destination_paths=(destination,),
                moved_source_paths=(source,),
                source_root_removed=True,
                lifecycle_state=FileOperationLifecycleState.COMPLETED,
            )
        state = (
            FileOperationItemState.DESTINATION_PUBLISHED_SOURCE_REMAINS
            if destination_exists and source_exists
            else FileOperationItemState.FAILED
        )
        return FileOperationItemResult(
            source,
            destination,
            False,
            FileOperationErrorCode.PARTIAL_SUCCESS.value,
            (
                tr('移動先は完成しましたが、元項目が残っています')
                if destination_exists and source_exists
                else tr('移動後のファイル状態を確認できません')
            ),
            destination_exists and source_exists,
            state=state,
            destination_exists_after=destination_exists,
            source_exists_after=source_exists,
            destination_published=destination_exists,
            source_removed=not source_exists,
            residual_source_paths=cls._residual_source_paths(source),
            operation=operation,
            published_destination_paths=((destination,) if destination_exists else ()),
            failed_source_paths=((source,) if source else ()),
            retry_source_paths=cls._immediate_residual_paths(source),
            source_root_removed=not source_exists,
            partially_completed=destination_exists,
            lifecycle_state=(
                FileOperationLifecycleState.PARTIAL_SOURCE_REMAINS
                if destination_exists and source_exists
                else FileOperationLifecycleState.FAILED
            ),
        )

    @classmethod
    def _source_removal_failure(
        cls,
        source: str,
        destination: str,
        message: str,
        *,
        operation: FileOperationKind | None = FileOperationKind.MOVE,
        replaced_existing: bool = False,
        destination_existed_before: bool = False,
    ) -> FileOperationItemResult:
        source_exists = os.path.lexists(source)
        destination_exists = os.path.lexists(destination)
        return FileOperationItemResult(
            source,
            destination,
            False,
            FileOperationErrorCode.PARTIAL_SUCCESS.value,
            message,
            True,
            state=FileOperationItemState.SOURCE_REMOVAL_FAILED,
            destination_exists_after=destination_exists,
            source_exists_after=source_exists,
            destination_published=destination_exists,
            source_removed=not source_exists,
            residual_source_paths=cls._residual_source_paths(source),
            operation=operation,
            replaced_existing=replaced_existing,
            destination_existed_before=destination_existed_before,
            published_destination_paths=((destination,) if destination_exists else ()),
            failed_source_paths=((source,) if source_exists else ()),
            retry_source_paths=cls._immediate_residual_paths(source),
            source_root_removed=not source_exists,
            partially_completed=destination_exists,
            lifecycle_state=FileOperationLifecycleState.PARTIAL_SOURCE_REMAINS,
        )

    @staticmethod
    def _residual_source_paths(source: str) -> tuple[str, ...]:
        if not os.path.lexists(source):
            return ()
        if not os.path.isdir(source):
            return (source,)
        residual = [source]
        try:
            for root, directories, files in os.walk(source):
                residual.extend(os.path.join(root, name) for name in directories)
                residual.extend(os.path.join(root, name) for name in files)
        except OSError:
            pass
        return tuple(residual)

    @staticmethod
    def _immediate_residual_paths(source: str) -> tuple[str, ...]:
        if not os.path.lexists(source):
            return ()
        if not os.path.isdir(source):
            return (source,)
        try:
            with os.scandir(source) as entries:
                return tuple(entry.path for entry in entries)
        except OSError:
            return (source,)

    @classmethod
    def _exception_failure(
        cls,
        source: str | None,
        destination: str | None,
        error: BaseException,
        *,
        operation: FileOperationKind | None = None,
    ) -> FileOperationItemResult:
        if isinstance(error, FileNotFoundError):
            code = FileOperationErrorCode.NOT_FOUND
        elif isinstance(error, PermissionError):
            code = FileOperationErrorCode.ACCESS_DENIED
        elif isinstance(error, OSError) and getattr(error, "winerror", None) in {
            32,
            33,
        }:
            code = FileOperationErrorCode.IN_USE
        elif isinstance(error, _ArtifactOperationError):
            code = (
                FileOperationErrorCode.ARTIFACT_CLEANUP_FAILED
                if error.cleanup is not None and not error.cleanup.removed
                else FileOperationErrorCode.IO_ERROR
            )
        else:
            code = FileOperationErrorCode.IO_ERROR
        result = cls._failure(
            source,
            destination,
            code,
            str(error),
            operation=operation,
        )
        if not isinstance(error, _ArtifactOperationError):
            return result
        destination_exists = bool(destination and os.path.lexists(destination))
        source_exists = bool(source and os.path.lexists(source))
        cleanup_errors = tuple(
            cleanup.error_message or "staging cleanup failed"
            for cleanup in error.cleanups
            if not cleanup.removed
        )
        artifact_paths = tuple(
            path
            for path in dict.fromkeys(error.artifact_paths)
            if os.path.lexists(path)
        )
        return replace(
            result,
            partial_success=bool(error.published and destination_exists),
            state=(
                FileOperationItemState.DESTINATION_PUBLISHED_SOURCE_REMAINS
                if operation is FileOperationKind.MOVE
                and destination_exists
                and source_exists
                else FileOperationItemState.FAILED
            ),
            destination_exists_after=destination_exists,
            source_exists_after=source_exists,
            destination_published=bool(error.published and destination_exists),
            source_removed=not source_exists,
            partially_completed=bool(error.published and destination_exists),
            lifecycle_state=(
                FileOperationLifecycleState.PARTIAL_SOURCE_REMAINS
                if error.published and destination_exists and source_exists
                else FileOperationLifecycleState.FAILED
            ),
            artifact_paths=artifact_paths,
            cleanup_errors=cleanup_errors,
        )

    def execute(self, request, *, cancelled=None, progress=None):
        if request.operation is FileOperationKind.UNDO:
            return run_undo(self, request, cancelled, progress)
        result = self._execute_request(request, cancelled=cancelled, progress=progress)
        try:
            entries = make_undo_entries(result)
        except Exception:
            _LOG.exception("Undo receipt unavailable after completed operation")
            entries = ()
        return replace(result, undo_entries=entries)
