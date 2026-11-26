from __future__ import annotations

from .i18n import tr
from .file_operation_identity import FileIdentity
from .windows_file_operations import RecycleOnly


import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


@dataclass(frozen=True)
class RecycleBinResult:
    success: bool
    cancelled: bool = False
    error_code: str | None = None
    error_message: str | None = None


class RecycleBinAdapter(Protocol):
    def recycle(self, path: str | Path) -> RecycleBinResult:
        ...


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", ctypes.POINTER(ctypes.c_wchar)),
        ("pTo", ctypes.POINTER(ctypes.c_wchar)),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", wintypes.LPVOID),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


class WindowsRecycleBin:
    """Synchronous recycle-only adapter for callers outside the Browser queue."""

    def __init__(self, shell_operation=None) -> None:
        # Keep the injected compatibility seam for the legacy adapter tests.
        # Production never uses SHFileOperation's delete verb.
        self._shell_operation = shell_operation

    @property
    def available(self) -> bool:
        return self._shell_operation is not None or os.name == "nt"

    def recycle(self, path: str | Path) -> RecycleBinResult:
        target = os.path.abspath(os.path.normpath(os.fspath(path)))
        if not self.available:
            return RecycleBinResult(
                False,
                error_code="api_unavailable",
                error_message=tr('Windowsのごみ箱APIを利用できません'),
            )
        if self._shell_operation is None:
            try:
                identity = FileIdentity.from_stat(os.lstat(target))
            except OSError as exc:
                return RecycleBinResult(False, error_code="shell_error", error_message=str(exc))
            return self.recycle_confirmed(target, identity)
        operation = _SHFILEOPSTRUCTW()
        source_list = ctypes.create_unicode_buffer(f"{target}\0")
        operation.wFunc = FO_DELETE
        operation.pFrom = ctypes.cast(
            source_list,
            ctypes.POINTER(ctypes.c_wchar),
        )
        operation.pTo = None
        operation.fFlags = (
            FOF_ALLOWUNDO
            | FOF_NOCONFIRMATION
            | FOF_NOERRORUI
            | FOF_SILENT
        )
        try:
            result_code = int(self._shell_operation(ctypes.byref(operation)))
        except (OSError, ValueError) as exc:
            return RecycleBinResult(
                False,
                error_code="api_error",
                error_message=str(exc),
            )
        if operation.fAnyOperationsAborted:
            return RecycleBinResult(
                False,
                cancelled=True,
                error_code="cancelled",
                error_message=tr('ごみ箱への移動がキャンセルされました'),
            )
        if result_code != 0:
            return RecycleBinResult(
                False,
                error_code="shell_error",
                error_message=tr('ごみ箱APIエラー: {p0}', p0=result_code),
            )
        if os.path.lexists(target):
            return RecycleBinResult(
                False,
                error_code="source_remains",
                error_message=tr('ごみ箱への移動後も元の項目が残っています'),
            )
        return RecycleBinResult(True)

    def recycle_confirmed(self, path: str, identity: FileIdentity) -> RecycleBinResult:
        try:
            if not self.available or not identity.is_normal or not identity.still_at(path):
                raise OSError('source_changed_or_unsupported')
            if self._shell_operation is not None:
                return self.recycle(path)
            with RecycleOnly() as shell:
                shell.recycle(path, identity)
        except OSError as exc:
            return RecycleBinResult(False, error_code="shell_error", error_message=str(exc))
        return RecycleBinResult(True)
