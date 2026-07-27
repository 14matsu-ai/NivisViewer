from __future__ import annotations

import os
import shutil
import stat
import uuid
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Iterable

from .file_operation_service import FileOperationKind, FileOperationRequest
from .file_operation_artifact import FileOperationArtifactPolicy
from .windows_filename import validate_windows_filename


class FileOperationState(str, Enum):
    PREPARING = "preparing"
    WAITING_FOR_CONFLICTS = "waiting_for_conflicts"
    READY = "ready"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FileConflictKind(str, Enum):
    FILE_FILE = "file_file"
    DIRECTORY_DIRECTORY = "directory_directory"
    FILE_DIRECTORY = "file_directory"
    DIRECTORY_FILE = "directory_file"
    CASE_ONLY_NAME = "case_only_name"
    SAME_PATH = "same_path"
    DESTINATION_PARENT_MISSING = "destination_parent_missing"
    DESTINATION_READ_ONLY = "destination_read_only"
    INVALID_DESTINATION_NAME = "invalid_destination_name"
    INTERNAL_STAGING_ARTIFACT = "internal_staging_artifact"


class ConflictResolution(str, Enum):
    SKIP = "skip"
    KEEP_BOTH = "keep_both"
    REPLACE = "replace"
    MERGE = "merge"
    CANCEL = "cancel"


@dataclass(frozen=True)
class PlannedFileItem:
    source_path: str
    destination_path: str | None
    is_directory: bool
    size_bytes: int
    file_count: int
    directory_count: int
    reparse_point: bool = False
    source_mtime_ns: int | None = None
    same_volume: bool | None = None
    requires_copy: bool = True
    requires_source_removal: bool = False
    conflict_kind: FileConflictKind | None = None

    @property
    def item_kind(self) -> str:
        if self.reparse_point:
            return "reparse_point"
        return "directory" if self.is_directory else "file"


@dataclass(frozen=True)
class FileConflict:
    conflict_id: str
    kind: FileConflictKind
    source_path: str | None
    destination_path: str | None
    default_resolution: ConflictResolution = ConflictResolution.SKIP
    allowed_resolutions: tuple[ConflictResolution, ...] = (
        ConflictResolution.SKIP,
        ConflictResolution.KEEP_BOTH,
        ConflictResolution.REPLACE,
        ConflictResolution.CANCEL,
    )
    message: str = ""
    source_kind: str | None = None
    destination_kind: str | None = None
    source_size: int | None = None
    destination_size: int | None = None
    source_mtime_ns: int | None = None
    destination_mtime_ns: int | None = None


@dataclass(frozen=True)
class FileOperationPlan:
    operation_id: str
    request_id: int
    operation: FileOperationKind
    source_paths: tuple[str, ...]
    destination_directory: str | None
    items: tuple[PlannedFileItem, ...] = ()
    conflicts: tuple[FileConflict, ...] = ()
    total_bytes: int = 0
    total_items: int = 0
    total_files: int = 0
    total_directories: int = 0
    available_bytes: int | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    state: FileOperationState = FileOperationState.READY

    @property
    def requires_conflict_resolution(self) -> bool:
        return bool(self.conflicts)

    @property
    def ready(self) -> bool:
        return not self.errors and not self.conflicts

    @property
    def operation_kind(self) -> str:
        return self.operation.value


class FileOperationPlanner:
    """Build a filesystem operation plan without importing Qt.

    All filesystem probing is intentionally performed by ``prepare`` so callers
    can run it wholly on a worker thread.
    """

    def prepare(
        self,
        request: FileOperationRequest,
        *,
        cancelled: Event | None = None,
    ) -> FileOperationPlan:
        cancel = cancelled or Event()
        operation_id = request.operation_id or str(request.request_id or uuid.uuid4())
        sources = self._prepare_sources(request.source_paths)
        destination_root = (
            self._absolute(request.destination_directory)
            if request.destination_directory
            else None
        )
        warnings: list[str] = []
        errors: list[str] = []
        conflicts: list[FileConflict] = []
        items: list[PlannedFileItem] = []

        if cancel.is_set():
            return self._cancelled_plan(request, operation_id, sources, destination_root)

        if request.operation in {FileOperationKind.COPY, FileOperationKind.MOVE}:
            self._validate_destination(
                destination_root,
                conflicts,
            )

        for source in sources:
            if cancel.is_set():
                return self._cancelled_plan(
                    request, operation_id, sources, destination_root
                )
            if FileOperationArtifactPolicy.is_internal_operation_artifact(source):
                errors.append(
                    "INTERNAL_STAGING_ARTIFACT: "
                    "NivisViewerの未完了一時ファイルは通常の"
                    f"ファイル操作対象にできません: {source}"
                )
                continue
            try:
                item = self._inspect_source(source, destination_root, cancel, warnings)
            except FileNotFoundError:
                warnings.append(f"対象が見つかりません: {source}")
                continue
            except PermissionError:
                warnings.append(f"アクセスできません: {source}")
                continue
            except OSError as exc:
                warnings.append(f"確認できません: {source}: {exc}")
                continue
            item = replace(
                item,
                requires_copy=(
                    request.operation is FileOperationKind.COPY
                    or (
                        request.operation is FileOperationKind.MOVE
                        and item.same_volume is not True
                    )
                ),
                requires_source_removal=(
                    request.operation is FileOperationKind.MOVE
                    and item.same_volume is not True
                ),
            )
            if request.operation is FileOperationKind.RENAME:
                conflict_count = len(conflicts)
                item = self._plan_rename(request, item, conflicts)
                if len(conflicts) > conflict_count:
                    item = replace(item, conflict_kind=conflicts[-1].kind)
            elif request.operation in {
                FileOperationKind.COPY,
                FileOperationKind.MOVE,
            }:
                conflict_count = len(conflicts)
                self._classify_transfer_conflict(
                    item,
                    source,
                    conflicts,
                    request.operation,
                )
                if len(conflicts) > conflict_count:
                    item = replace(item, conflict_kind=conflicts[-1].kind)
                if (
                    item.is_directory
                    and item.destination_path
                    and os.path.isdir(item.destination_path)
                ):
                    self._classify_directory_children(
                        item.source_path,
                        item.destination_path,
                        conflicts,
                        cancel,
                        warnings,
                    )
            items.append(item)

        total_bytes = sum(item.size_bytes for item in items)
        total_files = sum(item.file_count for item in items)
        total_directories = sum(item.directory_count for item in items)
        available_bytes: int | None = None
        if (
            destination_root
            and request.operation in {FileOperationKind.COPY, FileOperationKind.MOVE}
            and not any(
                conflict.kind
                in {
                    FileConflictKind.DESTINATION_PARENT_MISSING,
                    FileConflictKind.DESTINATION_READ_ONLY,
                }
                for conflict in conflicts
            )
        ):
            try:
                available_bytes = int(shutil.disk_usage(destination_root).free)
                if request.operation is FileOperationKind.COPY and total_bytes > available_bytes:
                    errors.append(
                        "コピー先の空き容量が不足しています"
                        f"（必要 {total_bytes} bytes / 空き {available_bytes} bytes）"
                    )
            except OSError as exc:
                warnings.append(f"コピー先の空き容量を確認できません: {exc}")

        if cancel.is_set():
            state = FileOperationState.CANCELLED
        elif errors:
            state = FileOperationState.FAILED
        elif conflicts:
            state = FileOperationState.WAITING_FOR_CONFLICTS
        else:
            state = FileOperationState.READY
        return FileOperationPlan(
            operation_id=operation_id,
            request_id=request.request_id,
            operation=request.operation,
            source_paths=sources,
            destination_directory=destination_root,
            items=tuple(items),
            conflicts=tuple(conflicts),
            total_bytes=total_bytes,
            total_items=total_files + total_directories,
            total_files=total_files,
            total_directories=total_directories,
            available_bytes=available_bytes,
            warnings=tuple(warnings),
            errors=tuple(errors),
            state=state,
        )

    def _inspect_source(
        self,
        source: str,
        destination_root: str | None,
        cancelled: Event,
        warnings: list[str],
    ) -> PlannedFileItem:
        source_stat = os.lstat(source)
        is_directory = stat.S_ISDIR(source_stat.st_mode)
        reparse = self._is_reparse(source_stat)
        size, files, directories = self._measure(
            source, is_directory, reparse, cancelled, warnings
        )
        destination = (
            os.path.join(destination_root, os.path.basename(source))
            if destination_root
            else None
        )
        same_volume: bool | None = None
        if destination_root is not None:
            try:
                same_volume = source_stat.st_dev == os.stat(destination_root).st_dev
            except OSError:
                same_volume = None
        return PlannedFileItem(
            source,
            destination,
            is_directory,
            size,
            files,
            directories,
            reparse,
            int(getattr(source_stat, "st_mtime_ns", 0)) or None,
            same_volume,
            requires_copy=not (
                same_volume is True
                and destination_root is not None
            ),
            requires_source_removal=False,
        )

    def _plan_rename(
        self,
        request: FileOperationRequest,
        item: PlannedFileItem,
        conflicts: list[FileConflict],
    ) -> PlannedFileItem:
        validation = validate_windows_filename(request.new_name or "")
        destination = os.path.join(
            os.path.dirname(item.source_path),
            validation.normalized_name or (request.new_name or ""),
        )
        planned = replace(item, destination_path=destination)
        if not validation.valid:
            self._add_conflict(
                conflicts,
                FileConflictKind.INVALID_DESTINATION_NAME,
                item.source_path,
                destination,
                validation.error_message or "名前が無効です",
                (ConflictResolution.SKIP, ConflictResolution.CANCEL),
            )
            return planned
        same_key = self._path_key(item.source_path) == self._path_key(destination)
        if same_key:
            kind = (
                FileConflictKind.SAME_PATH
                if os.path.basename(item.source_path) == validation.normalized_name
                else FileConflictKind.CASE_ONLY_NAME
            )
            self._add_conflict(
                conflicts,
                kind,
                item.source_path,
                destination,
                (
                    "名前が変更されていません"
                    if kind is FileConflictKind.SAME_PATH
                    else "大文字／小文字だけが異なる名前です"
                ),
                (
                    ConflictResolution.SKIP,
                    ConflictResolution.REPLACE,
                    ConflictResolution.CANCEL,
                ),
            )
        elif os.path.lexists(destination):
            destination_is_directory = os.path.isdir(destination)
            if item.is_directory and destination_is_directory:
                kind = FileConflictKind.DIRECTORY_DIRECTORY
            elif item.is_directory:
                kind = FileConflictKind.DIRECTORY_FILE
            elif destination_is_directory:
                kind = FileConflictKind.FILE_DIRECTORY
            else:
                kind = FileConflictKind.FILE_FILE
            self._add_conflict(
                conflicts,
                kind,
                item.source_path,
                destination,
                "同名項目が存在します",
                (
                    ConflictResolution.SKIP,
                    ConflictResolution.CANCEL,
                ),
            )
        return planned

    def _measure(
        self,
        path: str,
        is_directory: bool,
        reparse: bool,
        cancelled: Event,
        warnings: list[str],
    ) -> tuple[int, int, int]:
        if cancelled.is_set():
            return 0, 0, 0
        if reparse:
            warnings.append(f"再解析ポイントは再帰しません: {path}")
            return 0, 0 if is_directory else 1, 1 if is_directory else 0
        if not is_directory:
            return int(os.lstat(path).st_size), 1, 0
        total_bytes = 0
        files = 0
        directories = 1
        with os.scandir(path) as entries:
            for entry in entries:
                if cancelled.is_set():
                    break
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                    entry_reparse = self._is_reparse(entry_stat)
                    entry_is_dir = entry.is_dir(follow_symlinks=False)
                    size, child_files, child_directories = self._measure(
                        entry.path,
                        entry_is_dir,
                        entry_reparse,
                        cancelled,
                        warnings,
                    )
                    total_bytes += size
                    files += child_files
                    directories += child_directories
                except (FileNotFoundError, PermissionError, OSError) as exc:
                    warnings.append(f"項目を確認できません: {entry.path}: {exc}")
        return total_bytes, files, directories

    def _validate_destination(
        self,
        destination: str | None,
        conflicts: list[FileConflict],
    ) -> None:
        if not destination or not os.path.isdir(destination):
            self._add_conflict(
                conflicts,
                FileConflictKind.DESTINATION_PARENT_MISSING,
                None,
                destination,
                "移動先フォルダが存在しません",
                (ConflictResolution.CANCEL,),
            )
            return
        if not os.access(destination, os.W_OK):
            self._add_conflict(
                conflicts,
                FileConflictKind.DESTINATION_READ_ONLY,
                None,
                destination,
                "移動先へ書き込めません",
                (ConflictResolution.CANCEL,),
            )

    def _classify_transfer_conflict(
        self,
        item: PlannedFileItem,
        source: str,
        conflicts: list[FileConflict],
        operation: FileOperationKind,
    ) -> None:
        destination = item.destination_path
        if destination is None:
            return
        validation = validate_windows_filename(os.path.basename(destination))
        if not validation.valid:
            self._add_conflict(
                conflicts,
                FileConflictKind.INVALID_DESTINATION_NAME,
                source,
                destination,
                validation.error_message or "移動先の名前が無効です",
                (ConflictResolution.SKIP, ConflictResolution.CANCEL),
            )
            return
        if self._path_key(source) == self._path_key(destination):
            kind = (
                FileConflictKind.CASE_ONLY_NAME
                if os.path.basename(source) != os.path.basename(destination)
                else FileConflictKind.SAME_PATH
            )
            self._add_conflict(
                conflicts,
                kind,
                source,
                destination,
                "コピー元と移動先が同じです",
                (
                    (
                        ConflictResolution.SKIP,
                        ConflictResolution.KEEP_BOTH,
                        ConflictResolution.CANCEL,
                    )
                    if operation is FileOperationKind.COPY
                    else (
                        ConflictResolution.SKIP,
                        ConflictResolution.CANCEL,
                    )
                ),
            )
            return
        if item.is_directory and self._is_descendant(destination, source):
            self._add_conflict(
                conflicts,
                FileConflictKind.SAME_PATH,
                source,
                destination,
                "フォルダ自身の子階層へコピー／移動できません",
                (ConflictResolution.SKIP, ConflictResolution.CANCEL),
            )
            return
        if not os.path.lexists(destination):
            return
        destination_is_directory = os.path.isdir(destination)
        if item.is_directory and destination_is_directory:
            kind = FileConflictKind.DIRECTORY_DIRECTORY
            allowed = (
                ConflictResolution.SKIP,
                ConflictResolution.KEEP_BOTH,
                ConflictResolution.MERGE,
                ConflictResolution.CANCEL,
            )
        elif item.is_directory:
            kind = FileConflictKind.DIRECTORY_FILE
            allowed = (
                ConflictResolution.SKIP,
                ConflictResolution.KEEP_BOTH,
                ConflictResolution.CANCEL,
            )
        elif destination_is_directory:
            kind = FileConflictKind.FILE_DIRECTORY
            allowed = (
                ConflictResolution.SKIP,
                ConflictResolution.KEEP_BOTH,
                ConflictResolution.CANCEL,
            )
        else:
            kind = FileConflictKind.FILE_FILE
            allowed = (
                ConflictResolution.SKIP,
                ConflictResolution.KEEP_BOTH,
                ConflictResolution.REPLACE,
                ConflictResolution.CANCEL,
            )
        self._add_conflict(
            conflicts,
            kind,
            source,
            destination,
            "同名項目が移動先に存在します",
            allowed,
        )

    def _classify_directory_children(
        self,
        source: str,
        destination: str,
        conflicts: list[FileConflict],
        cancelled: Event,
        warnings: list[str],
    ) -> None:
        if cancelled.is_set():
            return
        try:
            with os.scandir(destination) as destination_entries:
                destinations = {
                    entry.name.casefold(): (entry.name, entry.path)
                    for entry in destination_entries
                }
            with os.scandir(source) as source_entries:
                for entry in source_entries:
                    if cancelled.is_set():
                        return
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                        if self._is_reparse(entry_stat):
                            warnings.append(
                                f"再解析ポイントは再帰しません: {entry.path}"
                            )
                            continue
                        destination_entry = destinations.get(entry.name.casefold())
                        if destination_entry is None:
                            continue
                        destination_name, destination_path = destination_entry
                        source_is_directory = entry.is_dir(follow_symlinks=False)
                        destination_is_directory = os.path.isdir(destination_path)
                        if entry.name != destination_name:
                            kind = FileConflictKind.CASE_ONLY_NAME
                            allowed = (
                                ConflictResolution.SKIP,
                                ConflictResolution.KEEP_BOTH,
                                (
                                    ConflictResolution.MERGE
                                    if source_is_directory
                                    and destination_is_directory
                                    else ConflictResolution.REPLACE
                                ),
                                ConflictResolution.CANCEL,
                            )
                        elif source_is_directory and destination_is_directory:
                            kind = FileConflictKind.DIRECTORY_DIRECTORY
                            allowed = (
                                ConflictResolution.SKIP,
                                ConflictResolution.KEEP_BOTH,
                                ConflictResolution.MERGE,
                                ConflictResolution.CANCEL,
                            )
                        elif source_is_directory:
                            kind = FileConflictKind.DIRECTORY_FILE
                            allowed = (
                                ConflictResolution.SKIP,
                                ConflictResolution.KEEP_BOTH,
                                ConflictResolution.CANCEL,
                            )
                        elif destination_is_directory:
                            kind = FileConflictKind.FILE_DIRECTORY
                            allowed = (
                                ConflictResolution.SKIP,
                                ConflictResolution.KEEP_BOTH,
                                ConflictResolution.CANCEL,
                            )
                        else:
                            kind = FileConflictKind.FILE_FILE
                            allowed = (
                                ConflictResolution.SKIP,
                                ConflictResolution.KEEP_BOTH,
                                ConflictResolution.REPLACE,
                                ConflictResolution.CANCEL,
                            )
                        self._add_conflict(
                            conflicts,
                            kind,
                            entry.path,
                            destination_path,
                            "統合先に同名の子項目があります",
                            allowed,
                        )
                        if source_is_directory and destination_is_directory:
                            self._classify_directory_children(
                                entry.path,
                                destination_path,
                                conflicts,
                                cancelled,
                                warnings,
                            )
                    except (FileNotFoundError, PermissionError, OSError) as exc:
                        warnings.append(
                            f"子項目の衝突を確認できません: {entry.path}: {exc}"
                        )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            warnings.append(
                f"フォルダ統合の衝突を確認できません: {source}: {exc}"
            )

    @staticmethod
    def _add_conflict(
        conflicts: list[FileConflict],
        kind: FileConflictKind,
        source: str | None,
        destination: str | None,
        message: str,
        allowed: tuple[ConflictResolution, ...],
    ) -> None:
        source_info = FileOperationPlanner._path_info(source)
        destination_info = FileOperationPlanner._path_info(destination)
        conflicts.append(
            FileConflict(
                uuid.uuid4().hex,
                kind,
                source,
                destination,
                ConflictResolution.SKIP,
                allowed,
                message,
                source_info[0],
                destination_info[0],
                source_info[1],
                destination_info[1],
                source_info[2],
                destination_info[2],
            )
        )

    @staticmethod
    def _path_info(path: str | None) -> tuple[str | None, int | None, int | None]:
        if not path:
            return None, None, None
        try:
            path_stat = os.lstat(path)
        except OSError:
            return None, None, None
        kind = "directory" if stat.S_ISDIR(path_stat.st_mode) else "file"
        return (
            kind,
            None if kind == "directory" else int(path_stat.st_size),
            int(getattr(path_stat, "st_mtime_ns", 0)) or None,
        )

    def _prepare_sources(self, paths: Iterable[str]) -> tuple[str, ...]:
        unique: dict[str, str] = {}
        for path in paths:
            absolute = self._absolute(path)
            unique.setdefault(self._path_key(absolute), absolute)
        ordered = sorted(
            unique.values(), key=lambda value: (len(Path(value).parts), self._path_key(value))
        )
        prepared: list[str] = []
        for candidate in ordered:
            if any(self._is_descendant(candidate, parent) for parent in prepared):
                continue
            prepared.append(candidate)
        return tuple(prepared)

    @staticmethod
    def _absolute(path: str | Path | None) -> str:
        if path is None:
            return ""
        return os.path.abspath(os.path.normpath(os.path.expanduser(os.fspath(path))))

    @classmethod
    def _path_key(cls, path: str | Path) -> str:
        return os.path.normcase(cls._absolute(path)).casefold()

    @classmethod
    def _is_descendant(cls, candidate: str, root: str) -> bool:
        candidate_key = cls._path_key(candidate)
        root_key = cls._path_key(root)
        try:
            common = os.path.commonpath((candidate_key, root_key))
        except ValueError:
            return False
        return common == root_key and candidate_key != root_key

    @staticmethod
    def _is_reparse(path_stat: os.stat_result) -> bool:
        attributes = int(getattr(path_stat, "st_file_attributes", 0))
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        return stat.S_ISLNK(path_stat.st_mode) or bool(attributes & reparse_flag)

    @staticmethod
    def _cancelled_plan(
        request: FileOperationRequest,
        operation_id: str,
        sources: tuple[str, ...],
        destination: str | None,
    ) -> FileOperationPlan:
        return FileOperationPlan(
            operation_id,
            request.request_id,
            request.operation,
            sources,
            destination,
            state=FileOperationState.CANCELLED,
        )
