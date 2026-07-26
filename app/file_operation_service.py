from __future__ import annotations

import errno
import os
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Callable, Iterable

from .chunked_file_copier import ChunkedFileCopier, CopyCancelled
from .windows_filename import generate_copy_name, validate_windows_filename
from .windows_recycle_bin import RecycleBinAdapter, WindowsRecycleBin


class FileOperationKind(str, Enum):
    RENAME = "rename"
    COPY = "copy"
    MOVE = "move"
    RECYCLE = "recycle"
    CREATE_DIRECTORY = "create_directory"


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
    IO_ERROR = "io_error"


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


@dataclass(frozen=True)
class FileOperationItemResult:
    source_path: str | None
    destination_path: str | None
    success: bool
    error_code: str | None = None
    error_message: str | None = None
    partial_success: bool = False


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


class FileOperationService:
    def __init__(self, recycle_bin: RecycleBinAdapter | None = None) -> None:
        self.recycle_bin = recycle_bin or WindowsRecycleBin()
        self.file_copier = ChunkedFileCopier()
        self._byte_progress: Callable[[int], None] | None = None

    def execute(
        self,
        request: FileOperationRequest,
        *,
        cancelled: Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> FileOperationResult:
        cancel_event = cancelled or Event()
        if request.operation is FileOperationKind.CREATE_DIRECTORY:
            return self._create_directory(request, cancel_event, progress)

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
                    "操作がキャンセルされました",
                )
                was_cancelled = True
            except BaseException as exc:
                item = self._exception_failure(source, None, exc)
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
        if not os.path.lexists(source):
            return self._failure(
                source,
                None,
                FileOperationErrorCode.NOT_FOUND,
                "対象が見つかりません",
            )
        if self._is_reparse_path(source):
            return self._failure(
                source,
                None,
                FileOperationErrorCode.IO_ERROR,
                "再解析ポイントはファイル操作の再帰対象にしません",
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
            "未対応のファイル操作です",
        )

    def _rename_item(
        self,
        source: str,
        new_name: str | None,
        request: FileOperationRequest | None = None,
    ) -> FileOperationItemResult:
        validation = validate_windows_filename(new_name or "")
        if not validation.valid:
            return self._failure(
                source,
                None,
                FileOperationErrorCode.INVALID_NAME,
                validation.error_message or "名前が無効です",
            )
        destination = os.path.join(os.path.dirname(source), validation.normalized_name)
        same_key = self._path_key(source) == self._path_key(destination)
        if same_key and os.path.basename(source) == validation.normalized_name:
            return self._failure(
                source,
                destination,
                FileOperationErrorCode.SAME_PATH,
                "名前が変更されていません",
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
                "大文字／小文字だけの名前変更をスキップしました",
            )
        if self._name_exists(destination, ignore_path=source if same_key else None):
            return self._failure(
                source,
                destination,
                FileOperationErrorCode.COLLISION,
                "同じ名前の項目が存在します",
            )
        try:
            if same_key:
                temporary = self._temporary_sibling(source)
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
        return FileOperationItemResult(source, destination, True)

    def _transfer_item(
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
                "コピー／移動先がフォルダではありません",
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
                        "衝突しない名前を生成できません",
                    )
                destination = os.path.join(destination_root, generated)
            else:
                return self._failure(
                    source,
                    destination,
                    FileOperationErrorCode.SAME_PATH,
                    "コピー元とコピー先が同じです",
                )
        if os.path.isdir(source) and self._is_descendant(destination_root, source):
            return self._failure(
                source,
                destination,
                FileOperationErrorCode.DESCENDANT_DESTINATION,
                "フォルダ自身の子階層へコピー／移動できません",
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
                        "衝突しない名前を生成できません",
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
                    "同名項目があるためスキップしました",
                )
        try:
            if request.operation is FileOperationKind.COPY:
                self._copy_atomic(source, destination, cancelled)
            else:
                self._move_safe(source, destination, cancelled)
        except _OperationCancelled:
            raise
        except _SourceDeleteFailed as exc:
            return FileOperationItemResult(
                source,
                destination,
                False,
                FileOperationErrorCode.PARTIAL_SUCCESS.value,
                str(exc),
                True,
            )
        except BaseException as exc:
            return self._exception_failure(source, destination, exc)
        return FileOperationItemResult(source, destination, True)

    def _replace_transfer(
        self,
        request: FileOperationRequest,
        source: str,
        destination: str,
        cancelled: Event,
    ) -> FileOperationItemResult:
        try:
            if request.operation is FileOperationKind.COPY:
                self._copy_atomic_replace(source, destination, cancelled)
            else:
                self._move_replace(source, destination, cancelled)
        except _OperationCancelled:
            raise
        except _SourceDeleteFailed as exc:
            return FileOperationItemResult(
                source,
                destination,
                False,
                FileOperationErrorCode.PARTIAL_SUCCESS.value,
                str(exc),
                True,
            )
        except BaseException as exc:
            return self._exception_failure(source, destination, exc)
        return FileOperationItemResult(source, destination, True)

    def _merge_transfer(
        self,
        request: FileOperationRequest,
        source: str,
        destination: str,
        cancelled: Event,
    ) -> FileOperationItemResult:
        try:
            self._copy_directory_merge(
                source,
                destination,
                cancelled,
                move=request.operation is FileOperationKind.MOVE,
                request=request,
            )
            if request.operation is FileOperationKind.MOVE:
                try:
                    os.rmdir(source)
                except OSError as exc:
                    return FileOperationItemResult(
                        source,
                        destination,
                        False,
                        FileOperationErrorCode.PARTIAL_SUCCESS.value,
                        f"コピー後にコピー元を削除できませんでした: {exc}",
                        True,
                    )
        except _OperationCancelled:
            raise
        except BaseException as exc:
            return self._exception_failure(source, destination, exc)
        return FileOperationItemResult(source, destination, True)

    def _recycle_item(self, source: str) -> FileOperationItemResult:
        result = self.recycle_bin.recycle(source)
        if result.success:
            return FileOperationItemResult(source, None, True)
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
            result.error_message or "ごみ箱へ移動できません",
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
            )
        if not validation.valid:
            item = self._failure(
                None,
                None,
                FileOperationErrorCode.INVALID_NAME,
                validation.error_message or "名前が無効です",
            )
        elif not os.path.isdir(parent):
            item = self._failure(
                None,
                parent or None,
                FileOperationErrorCode.INVALID_DESTINATION,
                "作成先がフォルダではありません",
            )
        else:
            destination = os.path.join(parent, validation.normalized_name)
            if self._name_exists(destination):
                item = self._failure(
                    None,
                    destination,
                    FileOperationErrorCode.COLLISION,
                    "同じ名前の項目が存在します",
                )
            else:
                try:
                    os.mkdir(destination)
                    item = FileOperationItemResult(None, destination, True)
                except BaseException as exc:
                    item = self._exception_failure(None, destination, exc)
        if progress is not None:
            progress(
                FileOperationProgress(
                    request.request_id,
                    request.operation,
                    1,
                    1,
                )
            )
        return FileOperationResult(
            request.operation,
            (item,),
            request_id=request.request_id,
        )

    def _copy_atomic(self, source: str, destination: str, cancelled: Event) -> None:
        temporary = self._temporary_sibling(destination)
        try:
            if os.path.isdir(source):
                self._copy_directory(source, temporary, cancelled)
            else:
                self.file_copier.copy(
                    source,
                    temporary,
                    cancelled=cancelled,
                    progress=self._byte_progress,
                )
            if cancelled.is_set():
                raise _OperationCancelled
            os.rename(temporary, destination)
        except CopyCancelled as exc:
            self._remove_temporary(temporary)
            raise _OperationCancelled from exc
        except BaseException:
            self._remove_temporary(temporary)
            raise

    def _copy_atomic_replace(
        self,
        source: str,
        destination: str,
        cancelled: Event,
    ) -> None:
        if not os.path.isdir(source):
            try:
                self.file_copier.copy(
                    source,
                    destination,
                    cancelled=cancelled,
                    progress=self._byte_progress,
                    replace=True,
                )
            except CopyCancelled as exc:
                raise _OperationCancelled from exc
            return
        temporary = self._temporary_sibling(destination)
        backup = self._temporary_sibling(destination)
        try:
            self._copy_directory(source, temporary, cancelled)
            if cancelled.is_set():
                raise _OperationCancelled
            os.rename(destination, backup)
            try:
                os.rename(temporary, destination)
            except BaseException:
                os.rename(backup, destination)
                raise
            self._remove_temporary(backup)
        except BaseException:
            self._remove_temporary(temporary)
            if os.path.lexists(backup) and not os.path.lexists(destination):
                try:
                    os.rename(backup, destination)
                except OSError:
                    pass
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
                        "再解析ポイントはコピーできません",
                        child_source,
                    )
                if entry.is_dir(follow_symlinks=False):
                    self._copy_directory(child_source, child_destination, cancelled)
                else:
                    try:
                        self.file_copier.copy(
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
    ) -> None:
        with os.scandir(source) as entries:
            for entry in entries:
                if cancelled.is_set():
                    raise _OperationCancelled
                child_source = entry.path
                child_destination = os.path.join(destination, entry.name)
                if self._is_reparse_path(child_source):
                    raise OSError(
                        errno.ELOOP,
                        "再解析ポイントはマージできません",
                        child_source,
                    )
                is_directory = entry.is_dir(follow_symlinks=False)
                if not os.path.lexists(child_destination):
                    if move:
                        try:
                            os.rename(child_source, child_destination)
                            continue
                        except OSError as exc:
                            if exc.errno != errno.EXDEV:
                                raise
                    self._copy_atomic(child_source, child_destination, cancelled)
                    if move:
                        self._remove_source(child_source)
                    continue
                resolution = self._collision_resolution(request, child_destination)
                if (
                    is_directory
                    and os.path.isdir(child_destination)
                    and resolution == FileCollisionPolicy.MERGE.value
                ):
                    self._copy_directory_merge(
                        child_source,
                        child_destination,
                        cancelled,
                        move=move,
                        request=request,
                    )
                    if move:
                        try:
                            os.rmdir(child_source)
                        except OSError:
                            pass
                elif resolution == FileCollisionPolicy.REPLACE.value:
                    if move:
                        self._move_replace(child_source, child_destination, cancelled)
                    else:
                        self._copy_atomic_replace(
                            child_source, child_destination, cancelled
                        )
                elif resolution in {
                    FileCollisionPolicy.RENAME.value,
                    FileCollisionPolicy.KEEP_BOTH.value,
                }:
                    generated = generate_copy_name(
                        entry.name,
                        self._directory_names(destination),
                        is_directory=is_directory,
                    )
                    if generated is not None:
                        target = os.path.join(destination, generated)
                        if move:
                            self._move_safe(child_source, target, cancelled)
                        else:
                            self._copy_atomic(child_source, target, cancelled)
                elif resolution == FileCollisionPolicy.CANCEL.value:
                    raise _OperationCancelled

    def _move_safe(self, source: str, destination: str, cancelled: Event) -> None:
        if cancelled.is_set():
            raise _OperationCancelled
        try:
            os.rename(source, destination)
            return
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
        self._copy_atomic(source, destination, cancelled)
        if cancelled.is_set():
            self._remove_temporary(destination)
            raise _OperationCancelled
        try:
            self._remove_source(source)
        except OSError as exc:
            raise _SourceDeleteFailed(
                f"コピーは完了しましたがコピー元を削除できませんでした: {exc}"
            ) from exc

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
                return
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
        self._copy_atomic_replace(source, destination, cancelled)
        if cancelled.is_set():
            raise _OperationCancelled
        try:
            self._remove_source(source)
        except OSError as exc:
            raise _SourceDeleteFailed(
                f"置換は完了しましたがコピー元を削除できませんでした: {exc}"
            ) from exc

    @staticmethod
    def _remove_source(source: str) -> None:
        if os.path.isdir(source):
            shutil.rmtree(source)
        else:
            os.unlink(source)

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
        try:
            common = os.path.commonpath((candidate_key, root_key))
        except ValueError:
            return False
        return common == root_key and candidate_key != root_key

    @classmethod
    def _name_exists(cls, path: str, *, ignore_path: str | None = None) -> bool:
        target_name = os.path.basename(path).casefold()
        ignore_key = cls._path_key(ignore_path) if ignore_path else None
        try:
            with os.scandir(os.path.dirname(path)) as entries:
                for entry in entries:
                    if entry.name.casefold() != target_name:
                        continue
                    if ignore_key is not None and cls._path_key(entry.path) == ignore_key:
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
        parent = os.path.dirname(path)
        name = os.path.basename(path)
        for _ in range(100):
            candidate = os.path.join(
                parent,
                f".{name}.nivisviewer-{uuid.uuid4().hex}.tmp",
            )
            if not os.path.lexists(candidate):
                return candidate
        raise OSError("一時パスを作成できません")

    @staticmethod
    def _remove_temporary(path: str) -> None:
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.lexists(path):
                os.unlink(path)
        except OSError:
            pass

    @staticmethod
    def _failure(
        source: str | None,
        destination: str | None,
        code: FileOperationErrorCode,
        message: str,
    ) -> FileOperationItemResult:
        return FileOperationItemResult(
            source,
            destination,
            False,
            code.value,
            message,
        )

    @classmethod
    def _exception_failure(
        cls,
        source: str | None,
        destination: str | None,
        error: BaseException,
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
        else:
            code = FileOperationErrorCode.IO_ERROR
        return cls._failure(source, destination, code, str(error))
