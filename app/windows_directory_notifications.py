"""Windows content-change notifications, without decoding or directory polling.

A BrowserDirectoryWatcher owns at most one of these handles and polls only its
signalled state with a zero-time wait. Qt's directory watcher remains in place
for directory deletion/renaming and as a fallback. No native input is produced.
"""
from __future__ import annotations

import ctypes
import ntpath
import os
from ctypes import wintypes

FILE_NOTIFY_CHANGE_SIZE = 0x00000008
FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010
CONTENT_CHANGE_FILTER = FILE_NOTIFY_CHANGE_SIZE | FILE_NOTIFY_CHANGE_LAST_WRITE
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF


def extended_windows_path(path: str) -> str:
    """Lexical long-path conversion; do not resolve a network path on the GUI."""
    path = ntpath.normpath(path)
    if path.startswith('\\\\?\\'):
        return path
    if path.startswith('\\\\'):
        return '\\\\?\\UNC\\' + path[2:]
    drive, tail = ntpath.splitdrive(path)
    if not drive or not tail.startswith('\\'):
        raise ValueError("An absolute Windows directory is required")
    return '\\\\?\\' + path


class WindowsContentNotification:
    """Single-owner HANDLE with deterministic, idempotent closure."""
    def __init__(self, path: str, *, api=None) -> None:
        if api is None:
            if os.name != "nt":
                raise OSError("Windows content notifications are unavailable")
            api = ctypes.WinDLL("kernel32", use_last_error=True)
        self._api = api
        self._handle = None
        self._first = api.FindFirstChangeNotificationW
        self._first.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
        self._first.restype = wintypes.HANDLE
        self._next = api.FindNextChangeNotification
        self._next.argtypes = [wintypes.HANDLE]
        self._next.restype = wintypes.BOOL
        self._wait = api.WaitForSingleObject
        self._wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._wait.restype = wintypes.DWORD
        self._close = api.FindCloseChangeNotification
        self._close.argtypes = [wintypes.HANDLE]
        self._close.restype = wintypes.BOOL
        handle = self._first(extended_windows_path(path), False, CONTENT_CHANGE_FILTER)
        if handle in (None, 0, -1, ctypes.c_void_p(-1).value):
            raise self._error("FindFirstChangeNotificationW")
        self._handle = handle

    @staticmethod
    def _error(operation: str) -> OSError:
        number = getattr(ctypes, "get_last_error", lambda: 0)()
        return OSError(number, f"{operation} failed (Win32 error {number})")

    def poll(self) -> bool:
        if self._handle is None:
            return False
        result = self._wait(self._handle, 0)  # Never block the Qt event loop.
        if result == WAIT_TIMEOUT:
            return False
        if result != WAIT_OBJECT_0:
            raise self._error("WaitForSingleObject")
        # Re-arm before emitting a higher-level signal. A slot may navigate or
        # close the Browser, and must never leave an old handle armed afterward.
        if not self._next(self._handle):
            raise self._error("FindNextChangeNotification")
        return True

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            if not self._close(handle):
                raise self._error("FindCloseChangeNotification")

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
