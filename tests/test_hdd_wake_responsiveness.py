from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.book_session import BookSession
from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.path_availability import PathAvailability, PathAvailabilityService
from app.system_file_opener import SystemFileOpener, SystemOpenResult, SystemOpenStatus


def _spin(qapp: QApplication, condition, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return bool(condition())


def _window(tmp_path: Path, qapp: QApplication, **kwargs) -> BrowserWindow:
    folder = tmp_path / "initial"
    folder.mkdir(exist_ok=True)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    window = BrowserWindow(config_manager=config, **kwargs)
    assert window.wait_for_scan()
    qapp.processEvents()
    return window


def test_book_open_worker_keeps_qt_heartbeat_running(qapp: QApplication) -> None:
    entered = threading.Event()
    release = threading.Event()
    ticks: list[int] = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))

    def factory(_path, **_kwargs):
        entered.set()
        assert release.wait(2)
        raise RuntimeError("finished fake storage wait")

    session = BookSession(source_factory=factory)
    try:
        timer.start()
        session.open_book_async("sleeping.zip")
        assert entered.wait(1)
        assert _spin(qapp, lambda: len(ticks) >= 4, 0.5)
    finally:
        release.set()
        timer.stop()
        assert session.wait_for_async(2000)
        qapp.processEvents()
        session.shutdown()


def test_snapshot_navigation_does_not_check_is_dir_before_publish(
    tmp_path: Path, qapp: QApplication, monkeypatch
) -> None:
    window = _window(tmp_path, qapp)
    target = tmp_path / "cached"
    target.mkdir()
    try:
        window.folder_snapshot_cache.set_enabled(True)
        window.folder_snapshot_cache.put(
            target,
            window._current_browser_visibility_policy(),
            window._current_browser_sort_policy(),
            (BrowserItem("cached.jpg", target / "cached.jpg", BrowserItemKind.IMAGE, None),),
        )
        assert window.folder_snapshot_cache.get(
            target,
            window._current_browser_visibility_policy(),
            window._current_browser_sort_policy(),
        ) is not None
        original = Path.is_dir

        def guarded(path):
            if path == target:
                raise AssertionError("synchronous folder probe")
            return original(path)

        monkeypatch.setattr(Path, "is_dir", guarded)
        assert window.navigate_to(target)
        assert window.current_path == target
        assert window._snapshot_reconcile_pending
    finally:
        monkeypatch.undo()
        window.close()
        qapp.processEvents()


def test_shell_actions_wait_for_probe_and_discard_superseded_result(
    tmp_path: Path, qapp: QApplication
) -> None:
    entered = threading.Event()
    release = threading.Event()

    class Filesystem:
        def probe(self, _path):
            entered.set()
            assert release.wait(2)
            return PathAvailability.AVAILABLE

    class Opener:
        def __init__(self):
            self.calls = []

        def open_with_default_application(self, path, parent_hwnd=None):
            self.calls.append(("default", str(path)))
            return SystemOpenResult(SystemOpenStatus.OPENED)

        def open_with_application_picker(self, path, parent_hwnd=None):
            self.calls.append(("picker", str(path)))
            return SystemOpenResult(SystemOpenStatus.PICKER_OPENED)

        def open_in_explorer(self, path, *, is_directory, assume_available=False):
            self.calls.append(("explorer", str(path), is_directory, assume_available))
            return SystemOpenResult(SystemOpenStatus.OPENED)

    service = PathAvailabilityService(filesystem=Filesystem())
    opener = Opener()
    window = _window(
        tmp_path, qapp, path_availability_service=service, system_file_opener=opener
    )
    path = tmp_path / "sleeping.bin"
    item = BrowserItem(path.name, path, BrowserItemKind.OTHER, None)
    ticks: list[int] = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    try:
        timer.start()
        assert window._open_system_file(path)
        assert entered.wait(1)
        assert window._open_with_application_picker(item)
        assert window._open_item_in_explorer(item)
        assert _spin(qapp, lambda: len(ticks) >= 4, 0.5)
        assert "待って" in window.statusBar().currentMessage()
        assert not opener.calls
        release.set()
        assert _spin(qapp, lambda: len(opener.calls) == 1)
        assert opener.calls == [("explorer", str(path), False, True)]
    finally:
        release.set()
        timer.stop()
        window.close()
        service.close()
        qapp.processEvents()


def test_explorer_assume_available_skips_duplicate_exists(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"x")

    class Adapter:
        def __init__(self):
            self.calls = []

        def open_explorer(self, target, is_directory):
            self.calls.append((target, is_directory))
            return 0

    adapter = Adapter()
    opener = SystemFileOpener(adapter)
    monkeypatch.setattr(
        "app.system_file_opener.os.path.exists",
        lambda _path: (_ for _ in ()).throw(AssertionError("duplicate stat")),
    )
    assert opener.open_in_explorer(path, is_directory=False, assume_available=True).success
    assert adapter.calls == [(str(path), False)]


@pytest.mark.parametrize(
    "state",
    [PathAvailability.MISSING, PathAvailability.UNAVAILABLE, PathAvailability.ERROR],
)
def test_unavailable_shell_target_never_launches(
    tmp_path: Path, qapp: QApplication, state: PathAvailability
) -> None:
    class Filesystem:
        def probe(self, _path):
            return state

    class Opener:
        def open_with_default_application(self, *_args, **_kwargs):
            raise AssertionError("Shell action launched for unavailable path")

    service = PathAvailabilityService(filesystem=Filesystem())
    window = _window(
        tmp_path, qapp, path_availability_service=service, system_file_opener=Opener()
    )
    try:
        assert window._open_system_file(tmp_path / "absent.bin")
        assert _spin(qapp, lambda: window._pending_system_open is None)
    finally:
        window.close()
        service.close()
        qapp.processEvents()


def test_shell_action_accepts_shared_probe_with_different_path_case(
    tmp_path: Path, qapp: QApplication
) -> None:
    entered = threading.Event()
    release = threading.Event()

    class Filesystem:
        def __init__(self):
            self.calls = 0

        def probe(self, _path):
            self.calls += 1
            entered.set()
            assert release.wait(2)
            return PathAvailability.AVAILABLE

    class Opener:
        def __init__(self):
            self.calls = []

        def open_with_default_application(self, path, parent_hwnd=None):
            self.calls.append(str(path))
            return SystemOpenResult(SystemOpenStatus.OPENED)

    filesystem = Filesystem()
    service = PathAvailabilityService(filesystem=filesystem)
    opener = Opener()
    window = _window(
        tmp_path, qapp, path_availability_service=service, system_file_opener=opener
    )
    original = str(tmp_path / "mixed-case.bin")
    differently_cased = original.upper()
    try:
        first_request = service.request_probe(original, force=True)
        assert entered.wait(1)
        assert window._open_system_file(differently_cased)
        assert window._pending_system_open is not None
        assert window._pending_system_open.request_id == first_request
        release.set()
        assert _spin(qapp, lambda: len(opener.calls) == 1)
        assert opener.calls == [differently_cased]
        assert filesystem.calls == 1
    finally:
        release.set()
        window.close()
        service.close()
        qapp.processEvents()
