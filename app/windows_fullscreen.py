"""Native monitor bounds and shell marking for one Viewer HWND at a time."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from uuid import UUID


HWND_TOP = 0
FULLSCREEN_POSITION_FLAGS = 0x0004 | 0x0010 | 0x0020  # no z-order, no activate, frame


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
        self._api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self._api.GetWindowRect.restype = wintypes.BOOL

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
        current = wintypes.RECT()
        if not self._api.GetWindowRect(hwnd, ctypes.byref(current)):
            return False
        if (current.left, current.top, current.right, current.bottom) == (
            rect.left, rect.top, rect.right, rect.bottom,
        ):
            return True
        return bool(self._api.SetWindowPos(
            hwnd, HWND_TOP, *bounds, FULLSCREEN_POSITION_FLAGS,
        ))


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", wintypes.DWORD), ("data2", wintypes.WORD),
        ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> _Guid:
        return cls.from_buffer_copy(UUID(value).bytes_le)


class WindowsTaskbarFullscreenAdapter:
    """Use a short COM lease on the calling GUI thread; retain no COM pointer.

    MarkFullscreenWindow is HWND-local and lets the shell manage activation,
    unlike topmost or changes to the user's taskbar configuration.
    """

    def __init__(self, ole32=None) -> None:
        self._api = ole32 if ole32 is not None else ctypes.WinDLL("ole32")
        self._api.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        self._api.CoInitializeEx.restype = ctypes.c_long
        self._api.CoUninitialize.argtypes = []
        self._api.CoUninitialize.restype = None
        self._api.CoCreateInstance.argtypes = [
            ctypes.POINTER(_Guid), ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p),
        ]
        self._api.CoCreateInstance.restype = ctypes.c_long

    @staticmethod
    def _method(pointer, index, result_type, *argument_types):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argument_types)(table[index])

    def mark(self, hwnd: int, fullscreen: bool) -> bool:
        if not hwnd:
            return False
        # Qt may already own this thread's apartment. RPC_E_CHANGED_MODE means
        # use that apartment without balancing someone else's initialization.
        initialized = int(self._api.CoInitializeEx(None, 0x2))
        if initialized < 0 and initialized != -2147417850:
            return False
        pointer = ctypes.c_void_p()
        try:
            clsid = _Guid.from_string("56FDF344-FD6D-11D0-958A-006097C9A090")
            iid = _Guid.from_string("602D4995-B13A-429B-A66E-1935E44F4317")
            result = self._api.CoCreateInstance(
                ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(pointer),
            )
            if result < 0 or not pointer.value:
                return False
            if self._method(pointer, 3, ctypes.c_long)(pointer) < 0:  # HrInit
                return False
            return self._method(
                pointer, 8, ctypes.c_long, wintypes.HWND, wintypes.BOOL,
            )(pointer, hwnd, bool(fullscreen)) >= 0
        finally:
            if pointer.value:
                self._method(pointer, 2, wintypes.ULONG)(pointer)  # Release
            if initialized >= 0:
                self._api.CoUninitialize()
