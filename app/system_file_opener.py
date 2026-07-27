from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from .file_operation_artifact import FileOperationArtifactPolicy

class SystemOpenStatus(str, Enum):
    OPENED = "opened"
    PICKER_OPENED = "picker_opened"
    FAILED = "failed"


@dataclass(frozen=True)
class SystemOpenResult:
    status: SystemOpenStatus
    error_code: int | None = None
    error_message: str | None = None

    @property
    def success(self) -> bool:
        return self.status in {
            SystemOpenStatus.OPENED,
            SystemOpenStatus.PICKER_OPENED,
        }


class SystemOpenAdapter(Protocol):
    def open_default(self, path: str, parent_hwnd: int | None) -> int: ...

    def open_picker(self, path: str, parent_hwnd: int | None) -> int: ...


class WindowsSystemOpenAdapter:
    """Thin wide-character Shell API adapter."""

    _SW_SHOWNORMAL = 1
    _OAIF_ALLOW_REGISTRATION = 0x00000001
    _OAIF_EXEC = 0x00000004

    class _OPENASINFO(ctypes.Structure):
        _fields_ = [
            ("pcszFile", ctypes.c_wchar_p),
            ("pcszClass", ctypes.c_wchar_p),
            ("oaifInFlags", ctypes.c_uint),
        ]

    def open_default(self, path: str, parent_hwnd: int | None) -> int:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell_execute = shell32.ShellExecuteW
        shell_execute.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        shell_execute.restype = ctypes.c_void_p
        result = shell_execute(
            ctypes.c_void_p(parent_hwnd or 0),
            "open",
            path,
            None,
            None,
            self._SW_SHOWNORMAL,
        )
        return int(result or 0)

    def open_picker(self, path: str, parent_hwnd: int | None) -> int:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        open_with = shell32.SHOpenWithDialog
        open_with.argtypes = [ctypes.c_void_p, ctypes.POINTER(self._OPENASINFO)]
        open_with.restype = ctypes.c_long
        info = self._OPENASINFO(
            path,
            None,
            self._OAIF_ALLOW_REGISTRATION | self._OAIF_EXEC,
        )
        return int(open_with(ctypes.c_void_p(parent_hwnd or 0), ctypes.byref(info)))


class SystemFileOpener:
    """Open a file through its Windows association, with picker fallback."""

    _SHELL_SUCCESS_MINIMUM = 32
    _SE_ERR_NOASSOC = 31

    def __init__(self, adapter: SystemOpenAdapter | None = None) -> None:
        self._adapter = adapter

    def open_with_default_application(
        self,
        path: str | Path,
        parent_hwnd: int | None = None,
    ) -> SystemOpenResult:
        target = self._absolute(path)
        if FileOperationArtifactPolicy.is_internal_operation_artifact(target):
            return SystemOpenResult(
                SystemOpenStatus.FAILED,
                error_message=(
                    "NivisViewerの未完了一時ファイルは開けません"
                ),
            )
        adapter = self._adapter_for_platform()
        if adapter is None:
            return SystemOpenResult(
                SystemOpenStatus.FAILED,
                error_message="Windowsの関連付け機能を利用できません",
            )
        try:
            result = adapter.open_default(target, parent_hwnd)
        except OSError as exc:
            return SystemOpenResult(
                SystemOpenStatus.FAILED,
                error_code=getattr(exc, "winerror", None),
                error_message=str(exc),
            )
        if result > self._SHELL_SUCCESS_MINIMUM:
            return SystemOpenResult(SystemOpenStatus.OPENED)
        if result == self._SE_ERR_NOASSOC:
            return self.open_with_application_picker(target, parent_hwnd)
        return SystemOpenResult(
            SystemOpenStatus.FAILED,
            error_code=result,
            error_message=f"関連付けアプリを起動できませんでした (Shell error {result})",
        )

    def open_with_application_picker(
        self,
        path: str | Path,
        parent_hwnd: int | None = None,
    ) -> SystemOpenResult:
        target = self._absolute(path)
        if FileOperationArtifactPolicy.is_internal_operation_artifact(target):
            return SystemOpenResult(
                SystemOpenStatus.FAILED,
                error_message=(
                    "NivisViewerの未完了一時ファイルは開けません"
                ),
            )
        adapter = self._adapter_for_platform()
        if adapter is None:
            return SystemOpenResult(
                SystemOpenStatus.FAILED,
                error_message="Windowsのアプリ選択画面を利用できません",
            )
        try:
            result = adapter.open_picker(target, parent_hwnd)
        except OSError as exc:
            return SystemOpenResult(
                SystemOpenStatus.FAILED,
                error_code=getattr(exc, "winerror", None),
                error_message=str(exc),
            )
        if result == 0:
            return SystemOpenResult(SystemOpenStatus.PICKER_OPENED)
        return SystemOpenResult(
            SystemOpenStatus.FAILED,
            error_code=result,
            error_message=f"アプリ選択画面を開けませんでした (HRESULT {result})",
        )

    def _adapter_for_platform(self) -> SystemOpenAdapter | None:
        if self._adapter is not None:
            return self._adapter
        if os.name != "nt":
            return None
        self._adapter = WindowsSystemOpenAdapter()
        return self._adapter

    @staticmethod
    def _absolute(path: str | Path) -> str:
        return os.path.abspath(os.path.normpath(os.fspath(path)))
