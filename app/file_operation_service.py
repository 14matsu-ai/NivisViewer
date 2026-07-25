from __future__ import annotations

import errno
import os
import shutil
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Callable, Iterable

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
    IO_ERROR = "io_error"


@dataclass(frozen=True)
class FileOperationRequest:
    request_id: int
    operation: FileOperationKind
    source_paths: tuple[str, ...] = ()
    destination_directory: str | None = None
    new_name: str | None = None
    collision_policy: FileCollisionPolicy = FileCollisionPolicy.SKIP


@dataclass(frozen=True)
class FileOperationItemResult:
    source_path: str | None
    destination_path: str | None
    success: bool
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class FileOperationResult:
    operation: FileOperationKind
    items: tuple[FileOperationItemResult, ...]
    cancelled: bool = False
    request_id: int = 0

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


ProgressCallback = Callable[[FileOperationProgress], None]


class _OperationCancelled(Exception):
    pass


class FileOperationService:
    def __init__(self, recycle_bin: RecycleBinAdapter | None = None) -> None:
        self.recycle_bin = recycle_bin or WindowsRecycleBin()

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
        for index, source in enumerate(sources):
            if cancel_event.is_set():
                was_cancelled = True
                break
            try:
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
            results.append(item)
            if progress is not None:
                progress(
                    FileOperationProgress(
                        request.request_id,
                        request.operation,
                        index + 1,
                        total,
                        source,
                    )
                )
            if was_cancelled:
                break
        return FileOperationResult(
            request.operation,
            tuple(results),
            cancelled=was_cancelled or cancel_event.is_set(),
            request_id=request.request_id,
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
        if request.operation is FileOperationKind.RENAME:
            return self._rename_item(source, request.new_name)
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
            if request.collision_policy is FileCollisionPolicy.RENAME:
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
                shutil.copy2(source, temporary)
                if cancelled.is_set():
                    raise _OperationCancelled
            if cancelled.is_set():
                raise _OperationCancelled
            os.rename(temporary, destination)
        except BaseException:
            self._remove_temporary(temporary)
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
                if entry.is_dir(follow_symlinks=False):
                    self._copy_directory(child_source, child_destination, cancelled)
                else:
                    shutil.copy2(child_source, child_destination, follow_symlinks=False)
        shutil.copystat(source, destination, follow_symlinks=False)

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
        if os.path.isdir(source):
            shutil.rmtree(source)
        else:
            os.unlink(source)

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
