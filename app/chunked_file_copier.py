from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path
from threading import Event
from typing import Callable


class CopyCancelled(Exception):
    pass


ByteProgressCallback = Callable[[int], None]


class ChunkedFileCopier:
    TEMPORARY_PATTERN = re.compile(
        r"^\..+\.nivisviewer-[0-9a-f]{32}\.tmp$",
        re.IGNORECASE,
    )
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
        temporary = self._temporary_sibling(destination_path)
        copied = 0
        try:
            source_size = os.path.getsize(source_path)
            if source_size <= self.chunk_size:
                if cancel.is_set():
                    raise CopyCancelled
                shutil.copy2(source_path, temporary, follow_symlinks=False)
                copied = source_size
                if progress is not None and copied:
                    progress(copied)
                if cancel.is_set():
                    raise CopyCancelled
                if replace:
                    os.replace(temporary, destination_path)
                else:
                    os.rename(temporary, destination_path)
                return copied
            with open(source_path, "rb", buffering=0) as input_file:
                with open(temporary, "xb", buffering=0) as output_file:
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
            shutil.copystat(source_path, temporary, follow_symlinks=False)
            if cancel.is_set():
                raise CopyCancelled
            if replace:
                os.replace(temporary, destination_path)
            else:
                os.rename(temporary, destination_path)
            return copied
        except BaseException:
            self._remove(temporary)
            raise

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
                        and cls.TEMPORARY_PATTERN.fullmatch(entry.name)
                    ):
                        found.append(entry.path)
        except OSError:
            return ()
        return tuple(found)

    @staticmethod
    def _remove(path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass
