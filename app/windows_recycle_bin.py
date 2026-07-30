from __future__ import annotations

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
    """Small, mockable adapter around the Windows Shell recycle operation."""

    def __init__(self, shell_operation=None) -> None:
        if shell_operation is not None:
            self._shell_operation = shell_operation
        elif os.name == "nt":
            operation = ctypes.windll.shell32.SHFileOperationW
            operation.argtypes = [ctypes.POINTER(_SHFILEOPSTRUCTW)]
            operation.restype = ctypes.c_int
            self._shell_operation = operation
        else:
            self._shell_operation = None

    @property
    def available(self) -> bool:
        return self._shell_operation is not None

    def recycle(self, path: str | Path) -> RecycleBinResult:
        target = os.path.abspath(os.path.normpath(os.fspath(path)))
        if self._shell_operation is None:
            return RecycleBinResult(
                False,
                error_code="api_unavailable",
                error_message="Windowsのごみ箱APIを利用できません",
            )
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
                error_message="ごみ箱への移動がキャンセルされました",
            )
        if result_code != 0:
            return RecycleBinResult(
                False,
                error_code="shell_error",
                error_message=f"ごみ箱APIエラー: {result_code}",
            )
        if os.path.lexists(target):
            return RecycleBinResult(
                False,
                error_code="source_remains",
                error_message="ごみ箱への移動後も元の項目が残っています",
            )
        return RecycleBinResult(True)
