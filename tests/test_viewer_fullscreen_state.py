from __future__ import annotations

from types import SimpleNamespace
import ctypes
import pytest

from PySide6.QtCore import QByteArray, QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QMainWindow, QSlider, QStatusBar, QVBoxLayout, QWidget

from app import fullscreen_chrome as fullscreen_module
from app.fullscreen_chrome import FullscreenChromeController
from app.windows_fullscreen import (
    FULLSCREEN_POSITION_FLAGS,
    HWND_TOP,
    WindowsFullscreenAdapter,
    _MonitorInfo,
)


class _ObservedWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.transitions: list[tuple[str, object]] = []

    def hide(self) -> None:  # type: ignore[override]
        self.transitions.append(("hide", None))
        super().hide()

    def setWindowState(self, state) -> None:  # type: ignore[override]
        self.transitions.append(("state", state))
        super().setWindowState(state)

    def setWindowFlags(self, flags):  # type: ignore[override]
        self.transitions.append(("flags", flags))
        return super().setWindowFlags(flags)

    def setGeometry(self, geometry: QRect) -> None:  # type: ignore[override]
        self.transitions.append(("geometry", QRect(geometry)))
        super().setGeometry(geometry)

    def showFullScreen(self) -> None:  # type: ignore[override]
        self.transitions.append(("fullscreen", None))
        super().showFullScreen()

    def showMaximized(self) -> None:  # type: ignore[override]
        self.transitions.append(("maximized", None))
        super().showMaximized()

    def showNormal(self) -> None:  # type: ignore[override]
        self.transitions.append(("normal", None))
        super().showNormal()


def _make_controller() -> tuple[_ObservedWindow, FullscreenChromeController]:
    window = _ObservedWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = QSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
    )
    return window, controller


def _dispose_window(
    qapp,
    window: _ObservedWindow,
    controller: FullscreenChromeController,
) -> None:
    controller.shutdown()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


class _ScreenGeometry:
    def __init__(self, geometry: QRect, available: QRect) -> None:
        self._geometry = QRect(geometry)
        self._available = QRect(available)

    def geometry(self) -> QRect:
        return QRect(self._geometry)

    def availableGeometry(self) -> QRect:
        return QRect(self._available)


def test_true_fullscreen_projects_full_monitor_not_working_area(
    qapp,
    monkeypatch,
) -> None:
    window, controller = _make_controller()
    screen = _ScreenGeometry(
        QRect(1920, 0, 1600, 900),
        QRect(1920, 0, 1600, 852),
    )
    native_bounds: list[QRect] = []
    monkeypatch.setattr(window, "screen", lambda: screen)
    target_screens = []
    monkeypatch.setattr(controller, "_set_target_screen", target_screens.append)
    monkeypatch.setattr(controller, "_capture_native_fullscreen_monitor", lambda: 77)
    monkeypatch.setattr(
        controller,
        "_apply_native_fullscreen_bounds",
        lambda target_screen: native_bounds.append(target_screen),
    )

    window.setGeometry(QRect(2100, 100, 900, 650))
    window.show()
    saved_window_geometry = QByteArray(window.saveGeometry())
    controller.enter_true_fullscreen()
    qapp.processEvents()

    geometry_calls = [
        value for name, value in window.transitions if name == "geometry"
    ]
    assert geometry_calls[-1] == screen.geometry()
    assert geometry_calls[-1] != screen.availableGeometry()
    assert native_bounds == [77]
    assert window.isFullScreen()
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert controller.standard_window_geometry() == saved_window_geometry

    monkeypatch.setattr(window, "screen", lambda: qapp.primaryScreen())
    controller.leave_true_fullscreen()
    assert target_screens == [screen, screen]
    _dispose_window(qapp, window, controller)


def test_maximized_fullscreen_transition_is_hidden_and_restores_maximized(
    qapp,
    monkeypatch,
) -> None:
    window, controller = _make_controller()
    monkeypatch.setattr(
        controller,
        "_apply_native_fullscreen_bounds",
        lambda _geometry: None,
    )
    original_flags = window.windowFlags()
    window.setGeometry(QRect(120, 90, 840, 620))
    expected_normal_geometry = QRect(window.geometry())
    window.showMaximized()
    qapp.processEvents()
    assert window.isMaximized()

    window.transitions.clear()
    def capture_monitor():
        assert window.isMaximized()
        window.transitions.append(("capture_monitor", None))
        return 77

    monkeypatch.setattr(controller, "_capture_native_fullscreen_monitor", capture_monitor)
    controller.enter_true_fullscreen()
    qapp.processEvents()

    names = [name for name, _value in window.transitions]
    assert names.index("capture_monitor") < names.index("hide")
    assert names.index("hide") < names.index("state") < names.index("fullscreen")
    state = next(value for name, value in window.transitions if name == "state")
    assert state == Qt.WindowState.WindowNoState
    assert window.isFullScreen()

    controller.leave_true_fullscreen()
    qapp.processEvents()
    assert window.isMaximized()
    assert window.windowFlags() == original_flags
    assert window.normalGeometry() == expected_normal_geometry

    _dispose_window(qapp, window, controller)


def test_windowed_fullscreen_cycles_restore_flags_and_geometry_without_drift(
    qapp,
    monkeypatch,
) -> None:
    window, controller = _make_controller()
    monkeypatch.setattr(
        controller,
        "_apply_native_fullscreen_bounds",
        lambda _geometry: None,
    )
    original_geometry = QRect(140, 110, 910, 670)
    window.setGeometry(original_geometry)
    window.show()
    qapp.processEvents()
    original_flags = window.windowFlags()

    for _ in range(3):
        controller.enter_true_fullscreen()
        qapp.processEvents()
        assert window.isFullScreen()
        controller.leave_true_fullscreen()
        qapp.processEvents()
        assert not window.isFullScreen()
        assert not window.isMaximized()
        assert window.geometry() == original_geometry
        assert window.windowFlags() == original_flags

    controller.enter_true_fullscreen()
    window.showNormal()  # Simulate an external/native state transition.
    assert controller.owns_true_fullscreen_transition
    controller.leave_true_fullscreen()
    qapp.processEvents()
    assert window.geometry() == original_geometry
    assert window.windowFlags() == original_flags

    _dispose_window(qapp, window, controller)


def test_windows_borderless_maximized_state_preserves_bottom_reveal(
    qapp,
    monkeypatch,
) -> None:
    window, controller = _make_controller()
    original_geometry = QRect(140, 110, 910, 670)
    window.setGeometry(original_geometry)
    calls: list[tuple[str, int]] = []

    class Adapter:
        def monitor_for_window(self, _hwnd):
            return 77

        def apply(self, _hwnd, monitor):
            calls.append(("bounds", monitor))
            return True

    adapter = Adapter()
    monkeypatch.setattr(
        fullscreen_module,
        "_is_native_windows_platform",
        lambda: True,
    )
    monkeypatch.setattr(
        fullscreen_module,
        "WindowsFullscreenAdapter",
        lambda: adapter,
    )
    monkeypatch.setattr(controller, "_native_window_id", lambda: 1234)
    monkeypatch.setattr(controller, "_apply_native_fullscreen_frame", lambda _active: None)

    window.show()
    qapp.processEvents()
    controller.enter_true_fullscreen()
    qapp.processEvents()
    assert calls == [("bounds", 77)]
    assert window.isMaximized()
    assert not window.isFullScreen()
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    controller.set_fullscreen_state(
        True,
        hide_ui=True,
        hide_cursor=False,
    )

    global_position = controller.overlay_parent.mapToGlobal(
        QPoint(20, controller.overlay_parent.height() - 1)
    )
    local_position = controller.viewer.mapFromGlobal(global_position)
    mouse_move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(local_position),
        QPointF(global_position),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QCoreApplication.sendEvent(controller.viewer, mouse_move)
    assert controller.bottom_overlay_visible
    assert controller.bottom_reveal_strip.isVisible()

    controller.leave_true_fullscreen()
    assert not window.isFullScreen()
    assert not window.isMaximized()
    assert window.geometry() == original_geometry
    controller.set_fullscreen_state(
        False,
        hide_ui=True,
        hide_cursor=False,
    )
    _dispose_window(qapp, window, controller)


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize("origin", [(0, 0), (-3840, -240), (1920, 180)])
def test_windows_native_projection_uses_monitor_native_bounds(
    qapp,
    monkeypatch,
    scale,
    origin,
) -> None:
    window, controller = _make_controller()
    window.show()
    qapp.processEvents()
    calls: list[tuple[object, ...]] = []

    class Function:
        def __init__(self, call):
            self.call = call

        def __call__(self, *args):
            return self.call(*args)

    x, y = origin
    native = (x, y, 3840, 2160)
    # Qt preserves Windows screen origins while scaling each screen's size.
    logical = QRect(x, y, int(3840 / scale), int(2160 / scale))
    monkeypatch.setattr(window, "screen", lambda: SimpleNamespace(geometry=lambda: logical))
    target_monitor = 0x123456789

    def monitor_for_window(hwnd, flags):
        assert hwnd == 1234
        assert flags == 2
        return target_monitor

    def get_info(monitor, pointer):
        info = ctypes.cast(pointer, ctypes.POINTER(_MonitorInfo)).contents
        assert info.cbSize == ctypes.sizeof(_MonitorInfo)
        if monitor != target_monitor:
            return 0
        bounds = (origin[0], origin[1], origin[0] + 3840, origin[1] + 2160)
        info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom = bounds
        info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom = (
            bounds[0], bounds[1], bounds[2], bounds[3] - 80,
        )
        return 1

    api = SimpleNamespace(
        MonitorFromWindow=Function(monitor_for_window),
        GetMonitorInfoW=Function(get_info),
        SetWindowPos=Function(lambda *args: calls.append(args) or 1),
    )
    adapter = WindowsFullscreenAdapter(api)

    monkeypatch.setattr(
        fullscreen_module,
        "_is_native_windows_platform",
        lambda: True,
    )
    monkeypatch.setattr(
        fullscreen_module,
        "WindowsFullscreenAdapter",
        lambda: adapter,
    )
    monkeypatch.setattr(controller, "_native_window_id", lambda: 1234)

    captured = controller._capture_native_fullscreen_monitor()
    assert captured == target_monitor
    controller._apply_native_fullscreen_bounds(captured)

    assert len(calls) == 1
    hwnd, insert_after, x, y, width, height, flags = calls[0]
    assert hwnd == 1234
    assert insert_after == HWND_TOP
    assert (x, y, width, height) == native
    assert flags == FULLSCREEN_POSITION_FLAGS
    assert api.SetWindowPos.restype is ctypes.wintypes.BOOL
    assert ctypes.sizeof(api.GetMonitorInfoW.argtypes[0]) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(api.MonitorFromWindow.restype) == ctypes.sizeof(ctypes.c_void_p)
    assert not adapter.apply(1234, 0)
    assert len(calls) == 1

    _dispose_window(qapp, window, controller)
