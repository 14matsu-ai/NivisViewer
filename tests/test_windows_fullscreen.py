from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.windows_fullscreen import (
    FULLSCREEN_POSITION_FLAGS, WindowsFullscreenAdapter,
    WindowsTaskbarFullscreenAdapter, _Guid, _MonitorInfo,
)


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


@pytest.mark.parametrize("same", [False, True])
def test_bounds_correction_is_conditional_and_preserves_z_order(same):
    calls = []

    def get_monitor(_monitor, pointer):
        info = ctypes.cast(pointer, ctypes.POINTER(_MonitorInfo)).contents
        info.rcMonitor = wintypes.RECT(-3840, -240, 0, 1920)
        info.rcWork = wintypes.RECT(-3840, -240, 0, 1840)
        return 1

    def get_window(_hwnd, pointer):
        rect = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = (-3840, -240, 0, 1920 if same else 1840)
        return 1

    api = SimpleNamespace(
        MonitorFromWindow=Function(lambda *_: 77),
        GetMonitorInfoW=Function(get_monitor), GetWindowRect=Function(get_window),
        SetWindowPos=Function(lambda *args: calls.append(args) or 1),
    )
    adapter = WindowsFullscreenAdapter(api)
    assert adapter.apply(0x123456789, 77)
    assert len(calls) == (0 if same else 1)
    if calls:
        assert calls[0][2:6] == (-3840, -240, 3840, 2160)
        assert calls[0][-1] == FULLSCREEN_POSITION_FLAGS
        assert FULLSCREEN_POSITION_FLAGS & 0x0004  # SWP_NOZORDER
        assert FULLSCREEN_POSITION_FLAGS & 0x0010  # SWP_NOACTIVATE
        assert not FULLSCREEN_POSITION_FLAGS & 0x0040  # Never show a hidden HWND.
    api.GetWindowRect.call = lambda *_: 0
    assert not adapter.apply(0x123456789, 77)


@pytest.mark.parametrize(
    "initialized, created, hr_init, marked, expected",
    [
        (0, 0, 0, 0, True), (1, 0, 0, 0, True),
        (-2147417850, 0, 0, 0, True),  # Existing different COM apartment.
        (-2147467259, 0, 0, 0, False),
        (0, -2147467259, 0, 0, False),
        (0, 0, -2147467259, 0, False),
        (0, 0, 0, -2147467259, False),
    ],
)
@pytest.mark.parametrize("active", [False, True])
def test_taskbar_com_lease_typed_hwnd_hresult_and_release(
    initialized, created, hr_init, marked, expected, active,
):
    calls = []
    convention = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    # A real in-process vtable made exclusively of test callbacks. No shell COM
    # object, native window or taskbar operation is involved.
    callbacks = [
        convention(wintypes.ULONG, ctypes.c_void_p)(lambda _this: calls.append("release") or 0),
        convention(ctypes.c_long, ctypes.c_void_p)(lambda _this: calls.append("hr_init") or hr_init),
        convention(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, wintypes.BOOL)(
            lambda _this, hwnd, full: calls.append(("mark", hwnd, full)) or marked,
        ),
    ]
    table = (ctypes.c_void_p * 9)()
    for index, callback in zip((2, 3, 8), callbacks):
        table[index] = ctypes.cast(callback, ctypes.c_void_p).value
    vtable = ctypes.cast(table, ctypes.POINTER(ctypes.c_void_p))
    interface = ctypes.pointer(vtable)

    def create(clsid, outer, context, iid, pointer):
        calls.append("create")
        assert outer is None and context == 1
        assert ctypes.string_at(clsid, 16) == UUID("56FDF344-FD6D-11D0-958A-006097C9A090").bytes_le
        assert ctypes.string_at(iid, 16) == UUID("602D4995-B13A-429B-A66E-1935E44F4317").bytes_le
        if created >= 0:
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(interface, ctypes.c_void_p)
        return created

    api = SimpleNamespace(
        CoInitializeEx=Function(lambda _unused, mode: calls.append(("initialize", mode)) or initialized),
        CoCreateInstance=Function(create),
        CoUninitialize=Function(lambda: calls.append("uninitialize")),
    )
    adapter = WindowsTaskbarFullscreenAdapter(api)
    hwnd = 0x123456789 if ctypes.sizeof(ctypes.c_void_p) == 8 else 1234
    assert adapter.mark(hwnd, active) == expected
    if initialized < 0 and initialized != -2147417850:
        assert calls == [("initialize", 2)]
        return
    assert ("uninitialize" in calls) == (initialized >= 0)
    assert ("release" in calls) == (created >= 0)
    if created >= 0 and hr_init >= 0:
        assert ("mark", hwnd, int(active)) in calls
    assert api.CoCreateInstance.restype is ctypes.c_long
    assert ctypes.sizeof(_Guid) == 16
    assert ctypes.alignment(_Guid) == 4
    # Invalid HWND is rejected before touching COM.
    count = len(calls)
    assert not adapter.mark(0, active)
    assert len(calls) == count
