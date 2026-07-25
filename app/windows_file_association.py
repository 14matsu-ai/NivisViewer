from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from threading import RLock
from typing import Callable


ASSOCSTR_EXECUTABLE = 2


@dataclass(frozen=True)
class FileAssociationResult:
    extension: str
    executable_path: str | None
    application_kind: str | None
    error_code: str | None = None


def identify_archive_application(executable_path: str) -> str | None:
    """Identify only archive applications whose executable names we trust."""
    name = Path(str(executable_path).strip().strip('"')).name.casefold()
    if name in {"winrar.exe", "unrar.exe", "rar.exe"}:
        return "winrar"
    if name in {"7z.exe", "7zz.exe"}:
        return "seven_zip"
    if name in {"7zfm.exe", "7zg.exe"}:
        return "seven_zip_gui"
    return None


def resolve_archive_cli(
    executable_path: str,
    *,
    is_file: Callable[[Path], bool] | None = None,
) -> tuple[str | None, str | None]:
    """Return a safe executable and backend kind without starting either."""
    candidate = Path(str(executable_path).strip().strip('"'))
    kind = identify_archive_application(str(candidate))
    checker = is_file or (lambda path: path.is_file())
    if kind == "seven_zip_gui":
        for name in ("7z.exe", "7zz.exe"):
            sibling = candidate.with_name(name)
            try:
                if checker(sibling):
                    return str(sibling), "seven_zip"
            except OSError:
                continue
        return None, None
    if kind in {"winrar", "seven_zip"}:
        return str(candidate), kind
    return None, None


AssociationQuery = Callable[[str, str], str | None]


class WindowsFileAssociationResolver:
    def __init__(
        self,
        *,
        query: AssociationQuery | None = None,
        platform_name: str | None = None,
        is_file: Callable[[Path], bool] | None = None,
    ) -> None:
        self._query = query
        self._platform_name = platform_name or sys.platform
        self._is_file = is_file or (lambda path: path.is_file())
        self._lock = RLock()
        self._cache: dict[tuple[str, str], FileAssociationResult] = {}

    def resolve_executable(
        self,
        extension: str,
        *,
        verb: str = "open",
    ) -> FileAssociationResult:
        normalized = self._normalize_extension(extension)
        key = (normalized.casefold(), str(verb or "open").casefold())
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        if self._platform_name != "win32":
            return self._store(
                key,
                FileAssociationResult(
                    normalized, None, None, "platform_not_supported"
                ),
            )
        try:
            raw_path = (
                self._query(normalized, verb)
                if self._query is not None
                else self._assoc_query_string(normalized, verb)
            )
        except Exception:
            raw_path = None
        if not raw_path:
            return self._store(
                key,
                FileAssociationResult(normalized, None, None, "association_not_found"),
            )

        raw_path = str(raw_path).strip().strip('"')
        if raw_path.startswith(("\\\\", "//")):
            return self._store(
                key,
                FileAssociationResult(normalized, None, None, "unsafe_association"),
            )
        absolute = Path(os.path.abspath(os.path.normpath(raw_path)))
        if not absolute.is_absolute():
            return self._store(
                key,
                FileAssociationResult(normalized, None, None, "invalid_path"),
            )
        resolved_path, kind = resolve_archive_cli(
            str(absolute),
            is_file=self._is_file,
        )
        if resolved_path is None or kind is None:
            error = (
                "unsupported_association"
                if identify_archive_application(str(absolute)) is None
                else "backend_not_found"
            )
            return self._store(
                key,
                FileAssociationResult(normalized, None, None, error),
            )
        try:
            if not self._is_file(Path(resolved_path)):
                raise OSError
        except OSError:
            return self._store(
                key,
                FileAssociationResult(normalized, None, None, "executable_not_found"),
            )
        return self._store(
            key,
            FileAssociationResult(
                normalized,
                str(Path(os.path.abspath(os.path.normpath(resolved_path)))),
                kind,
                None,
            ),
        )

    def resolve_archive_extension(self, extension: str) -> FileAssociationResult:
        normalized = self._normalize_extension(extension)
        fallbacks = {
            ".cbr": (".cbr", ".rar"),
            ".cb7": (".cb7", ".7z"),
            ".rar": (".rar",),
            ".7z": (".7z",),
        }.get(normalized.casefold(), (normalized,))
        last: FileAssociationResult | None = None
        for candidate in fallbacks:
            result = self.resolve_executable(candidate)
            last = result
            if result.executable_path and result.application_kind:
                return FileAssociationResult(
                    normalized,
                    result.executable_path,
                    result.application_kind,
                    None,
                )
        return FileAssociationResult(
            normalized,
            None,
            None,
            last.error_code if last is not None else "association_not_found",
        )

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()

    @staticmethod
    def _normalize_extension(extension: str) -> str:
        value = str(extension or "").strip()
        if not value.startswith("."):
            value = f".{value}"
        return value.casefold()

    @staticmethod
    def _assoc_query_string(extension: str, verb: str) -> str | None:
        shlwapi = ctypes.WinDLL("Shlwapi.dll", use_last_error=True)
        function = shlwapi.AssocQueryStringW
        function.argtypes = (
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint),
        )
        function.restype = ctypes.c_long
        length = ctypes.c_uint(0)
        function(
            0,
            ASSOCSTR_EXECUTABLE,
            extension,
            verb,
            None,
            ctypes.byref(length),
        )
        if length.value <= 1:
            return None
        buffer = ctypes.create_unicode_buffer(length.value)
        result = function(
            0,
            ASSOCSTR_EXECUTABLE,
            extension,
            verb,
            buffer,
            ctypes.byref(length),
        )
        if result != 0:
            return None
        return buffer.value or None

    def _store(
        self,
        key: tuple[str, str],
        result: FileAssociationResult,
    ) -> FileAssociationResult:
        with self._lock:
            self._cache[key] = result
        return result
