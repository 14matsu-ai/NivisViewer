from __future__ import annotations

import os
from pathlib import Path
from threading import RLock

from .archive_backend import (
    ArchiveBackendError,
    ArchiveErrorCode,
    ArchiveListing,
    MAX_ARCHIVE_ENTRIES,
    MAX_IMAGE_ENTRY_BYTES,
)
from .winrar_locator import WinRARInfo, WinRARLocator
from .winrar_parser import parse_winrar_bare_listing
from .winrar_process import WinRARProcessResult, WinRARProcessRunner


LISTING_TIMEOUT_SECONDS = 30.0
ENTRY_TIMEOUT_SECONDS = 120.0


class WinRARBackend:
    def __init__(
        self,
        executable_path: str | Path | None = None,
        *,
        locator: WinRARLocator | None = None,
        explicit_path_getter=None,
        runner_factory=WinRARProcessRunner,
        archive_extension: str = ".rar",
    ) -> None:
        self._fixed_executable = str(executable_path or "")
        self._locator = locator or WinRARLocator()
        self._explicit_path_getter = explicit_path_getter or (lambda: "")
        self._runner_factory = runner_factory
        self._archive_extension = archive_extension.casefold()
        self._lock = RLock()
        self._info: WinRARInfo | None = None
        self._runner: WinRARProcessRunner | None = None

    @property
    def info(self) -> WinRARInfo:
        return self._ensure_info()

    def is_available(self) -> bool:
        return self._ensure_info().available

    def reset(self) -> None:
        with self._lock:
            runner = self._runner
            self._runner = None
            self._info = None
        if runner is not None:
            runner.cancel_all()
        self._locator.reset()

    def close(self) -> None:
        with self._lock:
            runner = self._runner
        if runner is not None:
            runner.cancel_all()

    def list_entries(
        self,
        archive_path: str,
        *,
        cancel_token=None,
    ) -> ArchiveListing:
        target = self._archive_path(archive_path)
        result = self._run(
            ["lb", "-scfr", "--", target],
            cancel_token=cancel_token,
            timeout_seconds=LISTING_TIMEOUT_SECONDS,
            maximum_stdout_bytes=32 * 1024 * 1024,
        )
        self._raise_for_result(result, archive_path=target)
        return parse_winrar_bare_listing(
            result.stdout,
            target,
            maximum_entries=MAX_ARCHIVE_ENTRIES,
        )

    def read_entry(
        self,
        archive_path: str,
        entry_path: str,
        *,
        cancel_token=None,
        maximum_bytes: int | None = None,
    ) -> bytes:
        target = self._archive_path(archive_path)
        if not entry_path:
            raise ArchiveBackendError(ArchiveErrorCode.ENTRY_NOT_FOUND)
        limit = (
            MAX_IMAGE_ENTRY_BYTES
            if maximum_bytes is None
            else min(MAX_IMAGE_ENTRY_BYTES, max(1, int(maximum_bytes)))
        )
        result = self._run(
            ["p", "-inul", "--", target, str(entry_path)],
            cancel_token=cancel_token,
            timeout_seconds=ENTRY_TIMEOUT_SECONDS,
            maximum_stdout_bytes=limit,
        )
        self._raise_for_result(
            result,
            archive_path=target,
            entry_path=entry_path,
        )
        if not result.stdout:
            raise ArchiveBackendError(ArchiveErrorCode.ENTRY_NOT_FOUND)
        return result.stdout

    def _ensure_info(self) -> WinRARInfo:
        with self._lock:
            if self._info is not None:
                return self._info
        explicit = self._fixed_executable or str(self._explicit_path_getter() or "")
        info = self._locator.locate(
            explicit,
            extension=self._archive_extension,
        )
        with self._lock:
            self._info = info
            if info.available and self._runner is None:
                self._runner = self._runner_factory(info.executable_path)
        return info

    def _run(self, arguments: list[str], **kwargs) -> WinRARProcessResult:
        info = self._ensure_info()
        if not info.available:
            raise ArchiveBackendError(
                ArchiveErrorCode.BACKEND_NOT_FOUND,
                debug_message=info.error_message,
            )
        with self._lock:
            runner = self._runner
        if runner is None:
            raise ArchiveBackendError(ArchiveErrorCode.BACKEND_NOT_FOUND)
        return runner.run(arguments, **kwargs)

    @staticmethod
    def _archive_path(value: str) -> str:
        target = Path(value)
        try:
            if not target.is_file():
                raise ArchiveBackendError(ArchiveErrorCode.ARCHIVE_NOT_FOUND)
        except OSError as exc:
            raise ArchiveBackendError(
                ArchiveErrorCode.ARCHIVE_NOT_FOUND,
                debug_message=str(exc),
            ) from exc
        return str(Path(os.path.abspath(os.path.normpath(os.fspath(target)))))

    @staticmethod
    def _raise_for_result(
        result: WinRARProcessResult,
        *,
        archive_path: str,
        entry_path: str | None = None,
    ) -> None:
        if result.return_code == 0:
            return
        extension = Path(archive_path).suffix.casefold()
        code_by_return = {
            3: ArchiveErrorCode.CORRUPT_ARCHIVE,
            6: ArchiveErrorCode.ARCHIVE_NOT_FOUND,
            7: ArchiveErrorCode.UNSUPPORTED_ARCHIVE,
            10: (
                ArchiveErrorCode.ENTRY_NOT_FOUND
                if entry_path is not None
                else ArchiveErrorCode.UNSUPPORTED_ARCHIVE
            ),
            11: ArchiveErrorCode.PASSWORD_REQUIRED,
            12: ArchiveErrorCode.CORRUPT_ARCHIVE,
            13: (
                ArchiveErrorCode.UNSUPPORTED_ARCHIVE
                if extension in {".7z", ".cb7"}
                else ArchiveErrorCode.CORRUPT_ARCHIVE
            ),
            255: ArchiveErrorCode.PROCESS_CANCELLED,
        }
        code = code_by_return.get(
            result.return_code,
            ArchiveErrorCode.PROCESS_FAILED,
        )
        diagnostic = result.stderr.decode("utf-8", errors="replace")[:2000]
        raise ArchiveBackendError(
            code,
            debug_message=(
                f"return_code={result.return_code}; archive={archive_path}; "
                f"stderr={diagnostic}"
            ),
        )
