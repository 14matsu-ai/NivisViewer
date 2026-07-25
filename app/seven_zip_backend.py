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
from .seven_zip_locator import SevenZipInfo, SevenZipLocator
from .seven_zip_parser import parse_seven_zip_listing
from .seven_zip_process import SevenZipProcessResult, SevenZipProcessRunner


LISTING_TIMEOUT_SECONDS = 30.0
ENTRY_TIMEOUT_SECONDS = 120.0


class SevenZipBackend:
    def __init__(
        self,
        executable_path: str | Path | None = None,
        *,
        locator: SevenZipLocator | None = None,
        explicit_path_getter=None,
        runner_factory=SevenZipProcessRunner,
    ) -> None:
        self._fixed_executable = str(executable_path or "")
        self._locator = locator or SevenZipLocator()
        self._explicit_path_getter = explicit_path_getter or (lambda: "")
        self._runner_factory = runner_factory
        self._lock = RLock()
        self._info: SevenZipInfo | None = None
        self._runner: SevenZipProcessRunner | None = None

    @property
    def info(self) -> SevenZipInfo:
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
        try:
            result = self._run(
                [
                    "l",
                    "-slt",
                    "-sccUTF-8",
                    "-p-",
                    "-spd",
                    "--",
                    target,
                ],
                cancel_token=cancel_token,
                timeout_seconds=LISTING_TIMEOUT_SECONDS,
                maximum_stdout_bytes=32 * 1024 * 1024,
            )
        except ArchiveBackendError as exc:
            if exc.code is ArchiveErrorCode.ENTRY_TOO_LARGE:
                raise ArchiveBackendError(
                    ArchiveErrorCode.TOO_MANY_ENTRIES,
                    debug_message=exc.debug_message,
                ) from exc
            raise
        self._raise_for_result(result, archive_path=target, allow_warning=True)
        warning_messages = self._warning_messages(result)
        listing = parse_seven_zip_listing(
            result.stdout.decode("utf-8", errors="replace"),
            target,
            maximum_entries=MAX_ARCHIVE_ENTRIES,
            warning_messages=warning_messages,
        )
        if listing.encrypted:
            raise ArchiveBackendError(ArchiveErrorCode.PASSWORD_REQUIRED)
        return listing

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
            [
                "x",
                "-so",
                "-sccUTF-8",
                "-p-",
                "-spd",
                "--",
                target,
                str(entry_path),
            ],
            cancel_token=cancel_token,
            timeout_seconds=ENTRY_TIMEOUT_SECONDS,
            maximum_stdout_bytes=limit,
        )
        self._raise_for_result(
            result,
            archive_path=target,
            entry_path=entry_path,
            allow_warning=False,
        )
        if not result.stdout:
            raise ArchiveBackendError(
                ArchiveErrorCode.ENTRY_NOT_FOUND,
                debug_message="7-Zip returned no entry bytes",
            )
        return result.stdout

    def _ensure_info(self) -> SevenZipInfo:
        with self._lock:
            if self._info is not None:
                return self._info
        explicit = self._fixed_executable or str(self._explicit_path_getter() or "")
        info = self._locator.locate(explicit)
        with self._lock:
            self._info = info
            if info.available and self._runner is None:
                self._runner = self._runner_factory(info.executable_path)
        return info

    def _run(self, arguments: list[str], **kwargs) -> SevenZipProcessResult:
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
        result: SevenZipProcessResult,
        *,
        archive_path: str,
        entry_path: str | None = None,
        allow_warning: bool = True,
    ) -> None:
        if result.return_code == 0 or (allow_warning and result.return_code == 1):
            return
        diagnostic = result.stderr.decode("utf-8", errors="replace")
        lowered = diagnostic.casefold()
        if any(token in lowered for token in ("wrong password", "password is incorrect", "encrypted")):
            code = ArchiveErrorCode.PASSWORD_REQUIRED
        elif entry_path is not None and any(
            token in lowered for token in ("no files to process", "not found")
        ):
            code = ArchiveErrorCode.ENTRY_NOT_FOUND
        elif any(
            token in lowered
            for token in ("can not open the file as archive", "unexpected end of data", "data error")
        ):
            code = ArchiveErrorCode.CORRUPT_ARCHIVE
        elif "is not archive" in lowered or "unsupported method" in lowered:
            code = ArchiveErrorCode.UNSUPPORTED_ARCHIVE
        else:
            code = ArchiveErrorCode.PROCESS_FAILED
        raise ArchiveBackendError(
            code,
            debug_message=(
                f"return_code={result.return_code}; "
                f"archive={archive_path}; stderr={diagnostic[:2000]}"
            ),
        )

    @staticmethod
    def _warning_messages(result: SevenZipProcessResult) -> tuple[str, ...]:
        if not result.warning:
            return ()
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        return (diagnostic[:1000] or "7-Zip completed with warnings",)
