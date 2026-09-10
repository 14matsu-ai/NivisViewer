"""Native bounds correction for the Viewer fullscreen surface only."""

from __future__ import annotations

import ctypes
from ctypes import wintypes


HWND_TOP = 0
FULLSCREEN_POSITION_FLAGS = 0x0010 | 0x0020 | 0x0040  # no activate, frame, show


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class WindowsFullscreenAdapter:
    """Capture the HWND's monitor, then use native rcMonitor unchanged.

    No Qt coordinates cross this boundary. In particular, mixed-DPI global
    screen origins cannot be converted by multiplying a QRect by its DPR.
    """

    def __init__(self, user32=None) -> None:
        self._api = user32 if user32 is not None else ctypes.WinDLL(
            "user32", use_last_error=True,
        )
        self._api.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self._api.MonitorFromWindow.restype = wintypes.HANDLE
        self._api.GetMonitorInfoW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_MonitorInfo),
        ]
        self._api.GetMonitorInfoW.restype = wintypes.BOOL
        self._api.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        self._api.SetWindowPos.restype = wintypes.BOOL

    def monitor_for_window(self, hwnd: int) -> int | None:
        return self._api.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST

    def apply(self, hwnd: int, monitor: int) -> bool:
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not self._api.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        rect = info.rcMonitor
        bounds = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        if bounds[2] <= 0 or bounds[3] <= 0:
            return False
        return bool(self._api.SetWindowPos(
            hwnd, HWND_TOP, *bounds, FULLSCREEN_POSITION_FLAGS,
        ))
