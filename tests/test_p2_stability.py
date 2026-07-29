from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.application_controller import ApplicationController
from app.application_shutdown import (
    ApplicationShutdownCoordinator,
    ApplicationShutdownState,
)
from app.config_manager import ConfigManager
from app.diagnostics_dialog import DiagnosticsDialog
from app.file_operation_queue import (
    FileOperationQueue,
    FileOperationQueueState,
)
from app.file_operation_service import (
    FileOperationKind,
    FileOperationRequest,
    FileOperationResult,
)
from app.file_preview import PreviewResultKind
from app.path_availability import (
    PathAvailability,
    PathAvailabilityFileSystem,
    PathAvailabilityService,
    PathAvailabilityServiceState,
)
from app.pdf_backend import PdfRenderRequest, PdfRenderResult
from app.pdfium_service import (
    PdfAvailabilityState,
    PdfiumService,
)
from app.startup_restore import (
    StartupRestoreCoordinator,
    StartupRestoreState,
)
from app.thumbnail_render import ThumbnailRenderSpec
from app.viewer_window import ViewerWindow
from app.windows_shell_preview import (
    ShellPreviewLifecycle,
    WindowsShellPreviewService,
)


def wait_until(
    qapp: QApplication,
    predicate,
    timeout: float = 2.0,
) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        sleep(0.005)
    return bool(predicate())


class ControlledFileSystem(PathAvailabilityFileSystem):
    def __init__(
        self,
        state: PathAvailability,
        *,
        started: Event | None = None,
        release: Event | None = None,
    ) -> None:
        self.state = state
        self.started = started
        self.release = release
        self.calls = 0

    def probe(self, path: str) -> PathAvailability:
        del path
        self.calls += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            self.release.wait(2)
        return self.state


@pytest.mark.parametrize(
    ("availability", "expected"),
    [
        (PathAvailability.MISSING, StartupRestoreState.FAILED),
        (PathAvailability.UNAVAILABLE, StartupRestoreState.FAILED),
        (PathAvailability.ERROR, StartupRestoreState.FAILED),
    ],
)
def test_startup_restore_failure_is_async_and_non_destructive(
    qapp: QApplication,
    availability: PathAvailability,
    expected: StartupRestoreState,
) -> None:
    service = PathAvailabilityService(
        filesystem=ControlledFileSystem(availability)
    )
    opened: list[str] = []
    notices: list[str] = []
    coordinator = StartupRestoreCoordinator(
        service,
        lambda path: opened.append(path),
    )
    coordinator.notification_requested.connect(notices.append)
    assert coordinator.start(r"\\server\offline\book.cbz")
    assert coordinator.state is StartupRestoreState.PROBING
    assert wait_until(qapp, lambda: coordinator.state is expected)
    assert not opened
    assert notices
    service.close()


def test_slow_startup_restore_keeps_qtimer_responsive(
    qapp: QApplication,
) -> None:
    started = Event()
    release = Event()
    service = PathAvailabilityService(
        filesystem=ControlledFileSystem(
            PathAvailability.AVAILABLE,
            started=started,
            release=release,
        )
    )
    opened: list[str] = []
    coordinator = StartupRestoreCoordinator(
        service,
        lambda path: opened.append(path) or True,
    )
    ticks: list[bool] = []
    QTimer.singleShot(0, lambda: ticks.append(True))
    before = monotonic()
    assert coordinator.start(r"\\server\slow\book.cbz")
    assert monotonic() - before < 0.1
    assert started.wait(1)
    qapp.processEvents()
    assert ticks == [True]
    assert opened == []
    release.set()
    assert wait_until(
        qapp,
        lambda: coordinator.state is StartupRestoreState.COMPLETED,
    )
    assert len(opened) == 1
    service.close()


def test_startup_restore_latest_explicit_request_wins(
    qapp: QApplication,
) -> None:
    release = Event()
    service = PathAvailabilityService(
        filesystem=ControlledFileSystem(
            PathAvailability.AVAILABLE,
            release=release,
        )
    )
    opened: list[str] = []
    coordinator = StartupRestoreCoordinator(service, opened.append)
    coordinator.start(r"\\server\slow\old.cbz")
    coordinator.cancel()
    release.set()
    qapp.processEvents()
    sleep(0.02)
    qapp.processEvents()
    assert coordinator.state is StartupRestoreState.CANCELLED
    assert opened == []
    service.close()


def test_controller_no_restore_does_not_probe_last_path(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    filesystem = ControlledFileSystem(PathAvailability.AVAILABLE)
    service = PathAvailabilityService(filesystem=filesystem)
    config = ConfigManager(tmp_path / "config.json")
    config.data.update(
        {
            "reopen_last_on_start": True,
            "last_open_path": r"\\server\slow\book.cbz",
        }
    )
    controller = ApplicationController(
        qapp,
        config_manager=config,
        path_availability_service=service,
    )
    controller.start(restore=False)
    qapp.processEvents()
    assert filesystem.calls == 0
    assert controller.startup_restore.state is StartupRestoreState.IDLE
    controller.shutdown()


def test_controller_shares_one_path_service_with_browser_models(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    service = PathAvailabilityService(
        filesystem=ControlledFileSystem(PathAvailability.AVAILABLE)
    )
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
        path_availability_service=service,
    )
    browser = controller.create_browser_window()
    assert browser.history_model.availability_service is service
    assert browser.bookmark_model.availability_service is service
    assert browser.folder_bookmark_model.availability_service is service
    controller.shutdown()


@pytest.mark.parametrize(
    ("availability", "message"),
    [
        (PathAvailability.MISSING, "見つかりません"),
        (PathAvailability.UNAVAILABLE, "アクセスできません"),
        (PathAvailability.ERROR, "確認できません"),
    ],
)
def test_recent_probe_failure_keeps_history_and_current_book(
    tmp_path: Path,
    qapp: QApplication,
    availability: PathAvailability,
    message: str,
) -> None:
    service = PathAvailabilityService(
        filesystem=ControlledFileSystem(availability)
    )
    config = ConfigManager(tmp_path / "config.json")
    config.data["recent_paths"] = [r"\\server\book.cbz"]
    window = ViewerWindow(
        config_manager=config,
        path_availability_service=service,
    )
    opened: list[str] = []
    window._open_path_handler = (
        lambda path, _new, _source: opened.append(path)
    )
    window._open_recent_path(r"\\server\book.cbz")
    assert wait_until(qapp, lambda: message in window.status.currentMessage())
    assert config.data["recent_paths"] == [r"\\server\book.cbz"]
    assert opened == []
    window.prepare_shutdown()
    service.close()


def test_recent_probe_latest_request_only(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    release = Event()
    service = PathAvailabilityService(
        filesystem=ControlledFileSystem(
            PathAvailability.AVAILABLE,
            release=release,
        ),
        max_workers=2,
    )
    window = ViewerWindow(
        config_manager=ConfigManager(tmp_path / "config.json"),
        path_availability_service=service,
    )
    opened: list[str] = []
    window._open_path_handler = (
        lambda path, _new, _source: opened.append(path)
    )
    window._open_recent_path(r"C:\old.cbz")
    window._open_recent_path(r"C:\new.cbz")
    release.set()
    assert wait_until(qapp, lambda: bool(opened))
    assert len(opened) == 1
    assert opened[0].casefold().endswith(r"\new.cbz")
    window.prepare_shutdown()
    service.close()


def test_location_probe_uses_no_path_predicates_on_gui_thread(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = PathAvailabilityService(
        filesystem=ControlledFileSystem(PathAvailability.MISSING)
    )
    window = ViewerWindow(
        config_manager=ConfigManager(tmp_path / "config.json"),
        path_availability_service=service,
    )
    window.book_session.current_path = Path(r"C:\本\page.jpg")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("synchronous GUI-thread path predicate")

    monkeypatch.setattr(Path, "exists", forbidden)
    monkeypatch.setattr(Path, "is_file", forbidden)
    monkeypatch.setattr(Path, "is_dir", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    window.open_current_location()
    assert wait_until(qapp, lambda: "見つかりません" in window.status.currentMessage())
    window.prepare_shutdown()
    service.close()


def test_path_availability_shutdown_rejects_new_and_finishes(
    qapp: QApplication,
) -> None:
    release = Event()
    service = PathAvailabilityService(
        filesystem=ControlledFileSystem(
            PathAvailability.AVAILABLE,
            release=release,
        )
    )
    service.probe(r"\\server\slow")
    service.close()
    assert service.state is PathAvailabilityServiceState.SHUTTING_DOWN
    assert service.probe(r"C:\new") == 0
    release.set()
    assert wait_until(
        qapp,
        lambda: service.state is PathAvailabilityServiceState.STOPPED,
    )


def test_shared_probe_consumer_cancel_does_not_cancel_physical_work(
    qapp: QApplication,
) -> None:
    release = Event()
    filesystem = ControlledFileSystem(
        PathAvailability.AVAILABLE,
        release=release,
    )
    service = PathAvailabilityService(filesystem=filesystem)
    first = service.probe(r"C:\same.cbz")
    second = service.probe(r"c:\same.cbz")
    assert first == second
    assert service.cancel_request(first)
    results: list[object] = []
    service.result_ready.connect(results.append)
    release.set()
    assert wait_until(qapp, lambda: len(results) == 1)
    assert filesystem.calls == 1
    service.close()


class PdfBackend:
    def __init__(
        self,
        *,
        available: bool = True,
        availability_release: Event | None = None,
        render_release: Event | None = None,
    ) -> None:
        self.available = available
        self.availability_release = availability_release
        self.render_release = render_release
        self.availability_calls = 0
        self.render_started = Event()

    def is_available(self) -> bool:
        self.availability_calls += 1
        if self.availability_release is not None:
            self.availability_release.wait(2)
        return self.available

    def render_page(self, request, *, cancel_token=None):
        del cancel_token
        self.render_started.set()
        if self.render_release is not None:
            self.render_release.wait(2)
        return PdfRenderResult(
            request.document_id,
            request.page_index,
            1,
            1,
            "RGBA",
            b"\0\0\0\0",
            request.generation,
            request.purpose,
        )

    def open_document(self, path, *, password=None, cancel_token=None):
        raise AssertionError(path)

    def close_document(self, document_id):
        del document_id

    def close_all(self):
        return None


def test_pdf_availability_state_transitions() -> None:
    release = Event()
    backend = PdfBackend(availability_release=release)
    service = PdfiumService(backend, auto_probe=False)
    assert service.availability_snapshot.state is PdfAvailabilityState.UNKNOWN
    assert service.request_availability_probe()
    assert service.availability_snapshot.state is PdfAvailabilityState.CHECKING
    release.set()
    deadline = monotonic() + 2
    while (
        service.availability_snapshot.state is PdfAvailabilityState.CHECKING
        and monotonic() < deadline
    ):
        sleep(0.005)
    assert service.availability_snapshot.state is PdfAvailabilityState.AVAILABLE
    service.shutdown(wait_seconds=1)
    assert service.availability_snapshot.state is PdfAvailabilityState.STOPPED
    assert not service.request_availability_probe(force=True)


def test_pdf_unavailable_snapshot_is_cached() -> None:
    backend = PdfBackend(available=False)
    service = PdfiumService(backend)
    deadline = monotonic() + 2
    while (
        service.availability_snapshot.state is PdfAvailabilityState.CHECKING
        and monotonic() < deadline
    ):
        sleep(0.005)
    assert service.availability_snapshot.state is PdfAvailabilityState.UNAVAILABLE
    assert not service.is_available
    assert backend.availability_calls == 1
    assert not service.request_availability_probe()
    assert backend.availability_calls == 1
    service.shutdown(wait_seconds=1)


def test_diagnostics_reads_cached_pdf_state_without_queueing(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    backend = PdfBackend()
    service = PdfiumService(backend, auto_probe=False)
    before = service.pending_count
    started = monotonic()
    dialog = DiagnosticsDialog(tmp_path, pdfium_service=service)
    dialog.show()
    qapp.processEvents()
    assert monotonic() - started < 0.1
    assert service.pending_count == before
    assert backend.availability_calls == 0
    assert "PDF: 未確認" in dialog.text_edit.toPlainText()
    dialog.close()
    service.shutdown(wait_seconds=1)


def test_open_diagnostics_updates_from_cached_async_result(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    release = Event()
    service = PdfiumService(
        PdfBackend(availability_release=release),
        auto_probe=False,
    )
    dialog = DiagnosticsDialog(tmp_path, pdfium_service=service)
    dialog.show()
    assert service.request_availability_probe()
    assert "PDF: 未確認" in dialog.text_edit.toPlainText()
    release.set()
    assert wait_until(
        qapp,
        lambda: "PDF: 利用可能" in dialog.text_edit.toPlainText(),
    )
    dialog.close()
    assert not dialog._availability_timer.isActive()
    service.shutdown(wait_seconds=1)


def test_diagnostics_opens_while_render_queue_is_blocked(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    render_release = Event()
    backend = PdfBackend(render_release=render_release)
    service = PdfiumService(backend)
    deadline = monotonic() + 2
    while (
        service.availability_snapshot.state is PdfAvailabilityState.CHECKING
        and monotonic() < deadline
    ):
        sleep(0.005)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        service.render_page,
        PdfRenderRequest("doc", 0, 1, 1),
    )
    assert backend.render_started.wait(1)
    before_pending = service.pending_count
    started = monotonic()
    dialog = DiagnosticsDialog(tmp_path, pdfium_service=service)
    dialog.show()
    qapp.processEvents()
    assert monotonic() - started < 0.1
    assert service.pending_count == before_pending
    render_release.set()
    future.result(timeout=2)
    executor.shutdown()
    dialog.close()
    service.shutdown(wait_seconds=1)


class BlockingOperationService:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def execute(self, request, *, cancelled, progress):
        del progress
        self.started.set()
        self.release.wait(2)
        return FileOperationResult(
            request.operation,
            (),
            cancelled=cancelled.is_set(),
            request_id=request.request_id,
            operation_id=request.operation_id,
        )


def operation_request(tmp_path: Path, request_id: int = 1) -> FileOperationRequest:
    source = tmp_path / f"{request_id}.txt"
    source.write_text("x", encoding="utf-8")
    destination = tmp_path / "to"
    destination.mkdir(exist_ok=True)
    return FileOperationRequest(
        request_id,
        FileOperationKind.COPY,
        (str(source),),
        str(destination),
        operation_id=str(request_id),
    )


def test_file_operation_shutdown_empty_is_idempotent(
    qapp: QApplication,
) -> None:
    queue = FileOperationQueue()
    finished: list[bool] = []
    queue.shutdown_finished.connect(lambda: finished.append(True))
    assert queue.begin_shutdown()
    qapp.processEvents()
    assert queue.lifecycle is FileOperationQueueState.STOPPED
    assert finished == [True]
    assert not queue.begin_shutdown()


def test_file_operation_shutdown_cancels_waiting_requests(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    backend = BlockingOperationService()
    queue = FileOperationQueue(service=backend)  # type: ignore[arg-type]
    assert queue.enqueue(operation_request(tmp_path, 1))
    assert wait_until(qapp, backend.started.is_set)
    assert queue.enqueue(operation_request(tmp_path, 2))
    assert len(queue.queued_requests) == 1
    queue.begin_shutdown(cancel_active=True)
    assert queue.queued_requests == ()
    backend.release.set()
    assert wait_until(
        qapp,
        lambda: queue.lifecycle is FileOperationQueueState.STOPPED,
    )

def test_file_operation_shutdown_cancels_active_and_rejects_new(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    backend = BlockingOperationService()
    queue = FileOperationQueue(service=backend)  # type: ignore[arg-type]
    assert queue.enqueue(operation_request(tmp_path, 1))
    assert wait_until(qapp, backend.started.is_set)
    assert queue.begin_shutdown(cancel_active=True, timeout_msecs=1000)
    assert not queue.enqueue(operation_request(tmp_path, 2))
    assert queue.lifecycle is FileOperationQueueState.SHUTTING_DOWN
    backend.release.set()
    assert wait_until(
        qapp,
        lambda: queue.lifecycle is FileOperationQueueState.STOPPED,
    )


def test_file_operation_shutdown_timeout_does_not_force_worker(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    backend = BlockingOperationService()
    queue = FileOperationQueue(service=backend)  # type: ignore[arg-type]
    failures: list[str] = []
    queue.shutdown_failed.connect(failures.append)
    queue.enqueue(operation_request(tmp_path))
    assert wait_until(qapp, backend.started.is_set)
    queue.begin_shutdown(timeout_msecs=10)
    assert wait_until(qapp, lambda: bool(failures))
    assert queue.lifecycle is FileOperationQueueState.SHUTTING_DOWN
    backend.release.set()
    assert wait_until(
        qapp,
        lambda: queue.lifecycle is FileOperationQueueState.STOPPED,
    )


def test_queue_source_has_no_process_events() -> None:
    source = Path("app/file_operation_queue.py").read_text(encoding="utf-8")
    assert "processEvents(" not in source
    assert "AllEvents" not in source


class ShellAdapter:
    def __init__(self, release: Event | None = None) -> None:
        self.release = release
        self.started = Event()
        self.calls = 0

    def get_image(self, path, width, height, flags):
        del path, flags
        self.calls += 1
        self.started.set()
        if self.release is not None:
            self.release.wait(2)
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(0xFF00FF00)
        return image


def shell_spec() -> ThumbnailRenderSpec:
    return ThumbnailRenderSpec.from_settings(
        32,
        "square",
        "letterbox",
    )


def test_shell_shutdown_joins_normal_worker() -> None:
    service = WindowsShellPreviewService(ShellAdapter())
    assert service.shutdown(join_timeout=1)
    assert service.state is ShellPreviewLifecycle.STOPPED
    assert not service.worker_alive
    assert service.shutdown(join_timeout=0)


def test_shell_shutdown_cancels_pending_and_reports_stuck() -> None:
    release = Event()
    adapter = ShellAdapter(release)
    service = WindowsShellPreviewService(adapter)
    executor = ThreadPoolExecutor(max_workers=2)
    active = executor.submit(
        service.request_thumbnail,
        "active.mp4",
        shell_spec(),
        cache_only=False,
    )
    assert adapter.started.wait(1)
    pending = executor.submit(
        service.request_thumbnail,
        "pending.mp4",
        shell_spec(),
        cache_only=False,
    )
    assert not service.shutdown(join_timeout=0.01)
    assert service.state is ShellPreviewLifecycle.STUCK
    assert service.last_shutdown_error
    assert pending.result(timeout=1).kind is PreviewResultKind.CANCELLED
    assert active.result(timeout=1).kind is PreviewResultKind.CANCELLED
    release.set()
    assert service.shutdown(join_timeout=1)
    assert service.state is ShellPreviewLifecycle.STOPPED
    assert adapter.calls == 1
    executor.shutdown()


def test_shell_rejects_new_request_after_shutdown() -> None:
    service = WindowsShellPreviewService(ShellAdapter())
    service.shutdown(join_timeout=1)
    result = service.request_thumbnail(
        "late.mp4",
        shell_spec(),
        cache_only=False,
    )
    assert result.kind is PreviewResultKind.CANCELLED


def test_application_shutdown_coordinator_order_and_idempotency() -> None:
    order: list[str] = []
    coordinator = ApplicationShutdownCoordinator(
        steps=(
            ("workers", lambda: order.append("workers") or True),
            ("metadata", lambda: order.append("metadata")),
            ("config", lambda: order.append("config")),
        )
    )
    finished: list[bool] = []
    coordinator.shutdown_finished.connect(lambda: finished.append(True))
    assert coordinator.begin_shutdown()
    assert coordinator.begin_shutdown()
    assert coordinator.state is ApplicationShutdownState.STOPPED
    assert order == ["workers", "metadata", "config"]
    assert finished == [True]

    empty = ApplicationShutdownCoordinator()
    assert empty.begin_shutdown()
    assert empty.begin_shutdown()
    assert empty.state is ApplicationShutdownState.STOPPED


def test_application_shutdown_false_stops_and_retries_from_failed_step() -> None:
    order: list[str] = []
    blocked = True

    def incomplete() -> bool:
        order.append("incomplete")
        return not blocked

    coordinator = ApplicationShutdownCoordinator(
        steps=(
            ("first", lambda: order.append("first")),
            ("incomplete", incomplete),
            ("last", lambda: order.append("last")),
        )
    )
    failures: list[str] = []
    finished: list[bool] = []
    coordinator.shutdown_failed.connect(failures.append)
    coordinator.shutdown_finished.connect(lambda: finished.append(True))

    assert not coordinator.begin_shutdown()
    assert coordinator.state is ApplicationShutdownState.TIMED_OUT
    assert coordinator.failed_step_index == 1
    assert coordinator.failed_step_name == "incomplete"
    assert order == ["first", "incomplete"]
    assert finished == []

    blocked = False
    assert coordinator.begin_shutdown()
    assert coordinator.state is ApplicationShutdownState.STOPPED
    assert coordinator.failed_step_index is None
    assert coordinator.failed_step_name is None
    assert order == ["first", "incomplete", "incomplete", "last"]
    assert len(failures) == 1
    assert finished == [True]

    assert coordinator.begin_shutdown()
    assert order == ["first", "incomplete", "incomplete", "last"]
    assert finished == [True]


def test_application_shutdown_exception_is_retryable_and_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []
    blocked = True

    def fail() -> None:
        order.append("fail")
        if blocked:
            raise RuntimeError("blocked")

    coordinator = ApplicationShutdownCoordinator(
        steps=(
            ("first", lambda: order.append("first")),
            ("fail", fail),
            ("never", lambda: order.append("never")),
        )
    )
    failures: list[str] = []
    coordinator.shutdown_failed.connect(failures.append)
    with caplog.at_level(logging.ERROR, logger="app.application_shutdown"):
        assert not coordinator.begin_shutdown()
        assert not coordinator.begin_shutdown()

    assert coordinator.state is ApplicationShutdownState.TIMED_OUT
    assert coordinator.failed_step_index == 1
    assert coordinator.failed_step_name == "fail"
    assert order == ["first", "fail", "fail"]
    assert failures == ["blocked", "blocked"]
    assert sum(
        record.name == "app.application_shutdown"
        and "Application shutdown step failed" in record.getMessage()
        for record in caplog.records
    ) == 1

    blocked = False
    assert coordinator.begin_shutdown()
    assert order == ["first", "fail", "fail", "fail", "never"]
    assert coordinator.state is ApplicationShutdownState.STOPPED


def test_controller_shutdown_retries_image_step_and_defers_application_quit(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
    )
    original_image_coordinator = controller.image_work_coordinator
    original_pdfium_shutdown = controller.pdfium_service.shutdown
    original_metadata_close = controller.metadata_store.close
    order: list[str] = []
    image_worker_released = False
    quit_calls: list[bool] = []
    exit_requests: list[bool] = []

    def image_shutdown() -> bool:
        order.append("image")
        return image_worker_released

    monkeypatch.setattr(
        controller,
        "_prepare_viewers_for_shutdown",
        lambda: order.append("viewers"),
    )
    monkeypatch.setattr(
        controller,
        "_prepare_browser_for_shutdown",
        lambda: order.append("browser"),
    )
    monkeypatch.setattr(
        controller,
        "image_work_coordinator",
        SimpleNamespace(shutdown=image_shutdown),
    )
    monkeypatch.setattr(
        controller,
        "_shutdown_pdfium_service",
        lambda: order.append("pdfium"),
    )
    monkeypatch.setattr(
        controller.config,
        "save",
        lambda: order.append("config"),
    )
    monkeypatch.setattr(
        controller.metadata_store,
        "flush",
        lambda: order.append("metadata_flush"),
    )
    monkeypatch.setattr(
        controller.metadata_store,
        "close",
        lambda: order.append("metadata_close"),
    )
    controller.application = SimpleNamespace(
        quit=lambda: quit_calls.append(True)
    )
    controller.exit_requested.connect(lambda: exit_requests.append(True))

    controller._request_application_exit()

    assert controller._shutdown
    assert not controller._shutdown_complete
    assert not controller._quit_committed
    assert controller.shutdown_coordinator.failed_step_name == "image_workers"
    assert order == ["viewers", "browser", "pdfium", "image"]
    assert quit_calls == []
    assert exit_requests == []
    with pytest.raises(RuntimeError):
        controller.create_viewer_window()

    image_worker_released = True
    assert controller.shutdown()

    assert controller._shutdown_complete
    assert controller._quit_committed
    assert order == [
        "viewers",
        "browser",
        "pdfium",
        "image",
        "image",
        "config",
        "metadata_flush",
        "metadata_close",
    ]
    assert quit_calls == [True]
    assert exit_requests == [True]

    assert controller.shutdown()
    assert order.count("viewers") == 1
    assert order.count("browser") == 1
    assert order.count("pdfium") == 1
    assert order.count("image") == 2
    assert order.count("config") == 1
    assert original_image_coordinator.shutdown()
    assert original_pdfium_shutdown()
    original_metadata_close()


def test_controller_rejects_new_viewer_after_shutdown(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
    )
    controller.shutdown()
    with pytest.raises(RuntimeError):
        controller.create_viewer_window()
