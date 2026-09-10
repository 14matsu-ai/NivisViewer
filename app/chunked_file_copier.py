from __future__ import annotations

from .i18n import tr


import os
import shutil
import uuid
from pathlib import Path
from threading import Event
from typing import Callable

from .file_operation_artifact import FileOperationArtifactPolicy


class CopyCancelled(Exception):
    pass


ByteProgressCallback = Callable[[int], None]


class ChunkedFileCopier:
    def __init__(self, *, chunk_size: int = 4 * 1024 * 1024) -> None:
        self.chunk_size = max(64 * 1024, min(8 * 1024 * 1024, int(chunk_size)))

    def copy(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        cancelled: Event | None = None,
        progress: ByteProgressCallback | None = None,
        replace: bool = False,
    ) -> int:
        source_path = os.fspath(source)
        destination_path = os.fspath(destination)
        cancel = cancelled or Event()
        if FileOperationArtifactPolicy.is_internal_operation_artifact(
            destination_path
        ):
            raise ValueError(tr('内部一時ファイルを最終destinationに指定できません'))
        temporary = FileOperationArtifactPolicy.create_staging_path(
            destination_path,
            uuid.uuid4().hex,
            uuid.uuid4().hex,
        )
        try:
            copied = self.copy_to_staging(
                source_path,
                temporary,
                cancelled=cancel,
                progress=progress,
            )
            if cancel.is_set():
                raise CopyCancelled
            if replace:
                os.replace(temporary, destination_path)
            else:
                os.rename(temporary, destination_path)
            if (
                not os.path.lexists(destination_path)
                or os.path.lexists(temporary)
            ):
                raise OSError(tr('コピー公開後の事後条件を満たしていません'))
            return copied
        except BaseException:
            FileOperationArtifactPolicy.cleanup_staging_path(temporary)
            raise

    def copy_to_staging(
        self,
        source: str | Path,
        staging: str | Path,
        *,
        cancelled: Event | None = None,
        progress: ByteProgressCallback | None = None,
    ) -> int:
        """Copy bytes to an exact staging path without creating another temp."""
        source_path = os.fspath(source)
        staging_path = os.fspath(staging)
        cancel = cancelled or Event()
        expected = os.path.getsize(source_path)
        copied = 0
        if cancel.is_set():
            raise CopyCancelled
        if expected <= self.chunk_size:
            shutil.copy2(source_path, staging_path, follow_symlinks=False)
            copied = expected
            with open(staging_path, "r+b", buffering=0) as staged_file:
                staged_file.flush()
                os.fsync(staged_file.fileno())
            if progress is not None and copied:
                progress(copied)
            if cancel.is_set():
                raise CopyCancelled
            actual = os.path.getsize(staging_path)
            if actual != expected:
                raise OSError(
                    tr('コピーサイズが一致しません: expected={p0}, copied={p1}', p0=expected, p1=actual)
                )
            return copied
        with open(source_path, "rb", buffering=0) as input_file:
            with open(staging_path, "xb", buffering=0) as output_file:
                while True:
                    if cancel.is_set():
                        raise CopyCancelled
                    block = input_file.read(self.chunk_size)
                    if not block:
                        break
                    output_file.write(block)
                    copied += len(block)
                    if progress is not None:
                        progress(len(block))
                output_file.flush()
                os.fsync(output_file.fileno())
        if copied != expected or os.path.getsize(staging_path) != expected:
            raise OSError(
                tr('コピーサイズが一致しません: expected={p0}, copied={p1}', p0=expected, p1=copied)
            )
        shutil.copystat(source_path, staging_path, follow_symlinks=False)
        return copied

    @classmethod
    def find_temporary_files(
        cls,
        directory: str | Path,
    ) -> tuple[str, ...]:
        """Return only names created by this copier; never removes them."""
        found: list[str] = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if (
                        not entry.is_symlink()
                        and FileOperationArtifactPolicy.is_internal_operation_artifact(
                            entry.name
                        )
                    ):
                        found.append(entry.path)
        except OSError:
            return ()
        return tuple(found)
