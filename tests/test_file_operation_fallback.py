from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.application_controller import (
    ApplicationController,
    FileOperationCloseChoice,
)
from app.config_manager import ConfigManager
from app.file_operation_panel import FileOperationPanel
from app.file_operation_queue import FileOperationQueue
from app.file_operation_service import (
    FileOperationErrorCode,
    FileOperationItemResult,
    FileOperationKind,
    FileOperationRequest,
    FileOperationResult,
    FileOperationService,
)


class ControlledCreateDirectoryService:
    def __init__(self, outcome: str = "success") -> None:
        self.outcome = outcome
        self.started = Event()
        self.release = Event()
        self.cancel_observed = False
        self._service = FileOperationService()

    def execute(self, request, *, cancelled, progress):
        self.started.set()
        self.release.wait(3)
        self.cancel_observed = cancelled.is_set()
        if self.outcome == "exception":
            raise RuntimeError("fake create-directory failure")
        if self.outcome == "failure":
            return FileOperationResult(
                request.operation,
                (
                    FileOperationItemResult(
                        None,
                        request.destination_directory,
                        False,
                        FileOperationErrorCode.IO_ERROR.value,
                        "fake create-directory failure",
                    ),
                ),
                request_id=request.request_id,
                operation_id=request.operation_id,
            )
        return self._service.execute(
            request,
            cancelled=cancelled,
            progress=progress,
        )


def wait_until(
    qapp: QApplication,
    predicate,
    timeout: float = 3.0,
) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        QTest.qWait(5)
    qapp.processEvents()
    return bool(predicate())


def flush_deferred_deletes(qapp: QApplication) -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def operation_panels(qapp: QApplication) -> tuple[object, ...]:
    return tuple(
        widget
        for widget in qapp.topLevelWidgets()
        if widget.objectName() == "file_operation_panel"
    )


def start_controller_create_directory(
    tmp_path: Path,
    qapp: QApplication,
    service: ControlledCreateDirectoryService,
    *,
    name: str = "controller",
):
    root = tmp_path / name
    root.mkdir()
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(root / "config.json"),
    )
    controller._exit_evaluation_suspended += 1
    controller.file_operation_queue.service = service  # type: ignore[assignment]
    browser = controller.start(restore=False)
    browser.show()
    assert browser._start_file_operation(
        FileOperationKind.CREATE_DIRECTORY,
        destination=root,
        new_name="created",
    )
    assert wait_until(qapp, service.started.is_set)
    assert controller.file_operation_queue.busy
    return controller, browser, root / "created"


def cleanup_controller(
    controller: ApplicationController,
    service: ControlledCreateDirectoryService,
    qapp: QApplication,
) -> None:
    service.release.set()
    controller.file_operation_queue.wait_for_done(3000)
    qapp.processEvents()
    browser = controller.get_browser_window()
    if browser is not None:
        browser._application_close_guard = lambda _window: True
        browser.close()
    qapp.processEvents()
    controller._exit_evaluation_suspended = max(
        0,
        controller._exit_evaluation_suspended - 1,
    )
    assert controller.shutdown()
    flush_deferred_deletes(qapp)


def test_create_directory_queue_completes_once_and_clears_panel(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    queue = FileOperationQueue(service=FileOperationService())
    panel = FileOperationPanel()
    panel.bind(queue)
    completed = []
    progress = []
    terminal_summaries = []
    terminal_active_ids = []
    destroyed = []
    queue.operation_completed.connect(completed.append)
    queue.operation_progress.connect(progress.append)
    queue.operation_completed.connect(
        lambda _result: (
            terminal_summaries.append(panel.summary_label.text()),
            terminal_active_ids.append(panel._active_operation_id),
        )
    )
    panel.destroyed.connect(lambda _object=None: destroyed.append(True))
    request = FileOperationRequest(
        17,
        FileOperationKind.CREATE_DIRECTORY,
        destination_directory=str(tmp_path),
        new_name="created",
    )

    assert queue.enqueue(request)
    assert queue.wait_for_done(3000)
    assert wait_until(qapp, lambda: len(completed) == 1)

    assert (tmp_path / "created").is_dir()
    assert len(completed) == 1
    assert completed[0].successes
    assert completed[0].operation_id == "17"
    assert progress
    assert progress[-1].operation_id == "17"
    assert not queue.busy
    assert queue._pool.activeThreadCount() == 0
    assert terminal_summaries == ["完了"]
    assert terminal_active_ids == [None]
    assert wait_until(qapp, lambda: destroyed == [True])
    flush_deferred_deletes(qapp)
    assert operation_panels(qapp) == ()
    queue.close()


def test_create_directory_close_after_completion_never_detaches_panel(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    service = ControlledCreateDirectoryService()
    controller, browser, created = start_controller_create_directory(
        tmp_path,
        qapp,
        service,
    )
    monkeypatch.setattr(
        controller,
        "_ask_file_operation_close_choice",
        lambda _window: FileOperationCloseChoice.CLOSE_AFTER_OPERATION,
    )
    closing = []
    browser.closing.connect(lambda _window: closing.append(True))

    try:
        browser.close()
        qapp.processEvents()

        assert closing == []
        assert browser.isVisible()
        assert controller._operation_fallback_panel is None
        assert operation_panels(qapp) == ()

        service.release.set()
        assert wait_until(qapp, lambda: closing == [True])

        assert created.is_dir()
        assert not controller.file_operation_queue.busy
        assert controller._operation_fallback_panel is None
        assert operation_panels(qapp) == ()
    finally:
        cleanup_controller(controller, service, qapp)


def test_create_directory_cancel_and_close_never_detaches_panel(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    service = ControlledCreateDirectoryService()
    controller, browser, created = start_controller_create_directory(
        tmp_path,
        qapp,
        service,
    )
    monkeypatch.setattr(
        controller,
        "_ask_file_operation_close_choice",
        lambda _window: FileOperationCloseChoice.CANCEL_AND_CLOSE,
    )
    original_cancel = controller.file_operation_queue.cancel
    cancel_calls = []

    def counted_cancel(operation_id=None):
        cancel_calls.append(operation_id)
        return original_cancel(operation_id)

    monkeypatch.setattr(controller.file_operation_queue, "cancel", counted_cancel)
    closing = []
    browser.closing.connect(lambda _window: closing.append(True))

    try:
        browser.close()
        browser.close()
        qapp.processEvents()

        assert len(cancel_calls) == 1
        assert closing == []
        assert controller._operation_fallback_panel is None
        assert operation_panels(qapp) == ()

        service.release.set()
        assert wait_until(qapp, lambda: closing == [True])

        assert service.cancel_observed
        assert not created.exists()
        assert len(cancel_calls) == 1
        assert not controller.file_operation_queue.busy
        assert controller._operation_fallback_panel is None
        assert operation_panels(qapp) == ()
    finally:
        cleanup_controller(controller, service, qapp)


@pytest.mark.parametrize(
    "outcome",
    ["success", "failure", "cancelled", "exception"],
)
def test_intentional_fallback_closes_after_every_terminal_outcome(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
    outcome: str,
) -> None:
    service = ControlledCreateDirectoryService(
        "success" if outcome == "cancelled" else outcome
    )
    controller, browser, _created = start_controller_create_directory(
        tmp_path,
        qapp,
        service,
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: None)
    controller._continue_operations_without_main_window = True
    browser._application_close_guard = lambda _window: True
    completed = []
    controller.file_operation_queue.operation_completed.connect(completed.append)

    try:
        browser.close()
        qapp.processEvents()
        panel = controller._operation_fallback_panel

        assert panel is not None
        assert panel.isVisible()
        assert panel.summary_label.text() == "create_directory: running"
        assert operation_panels(qapp) == (panel,)

        if outcome == "cancelled":
            original_cancel = controller.file_operation_queue.cancel
            cancel_calls = []

            def counted_cancel(operation_id=None):
                cancel_calls.append(operation_id)
                return original_cancel(operation_id)

            monkeypatch.setattr(
                controller.file_operation_queue,
                "cancel",
                counted_cancel,
            )
            panel.cancel_button.click()
            panel.cancel_button.click()
            assert len(cancel_calls) == 1
            assert cancel_calls == [panel._active_operation_id]
            assert "cancelling" in panel.summary_label.text()

        service.release.set()
        assert wait_until(
            qapp,
            lambda: (
                not controller.file_operation_queue.busy
                and controller._operation_fallback_panel is None
            ),
        )
        flush_deferred_deletes(qapp)

        assert len(completed) == 1
        assert completed[0].operation_id
        assert completed[0].cancelled is (outcome == "cancelled")
        assert controller.file_operation_queue._pool.activeThreadCount() == 0
        assert operation_panels(qapp) == ()
    finally:
        cleanup_controller(controller, service, qapp)


def test_fallback_panel_ignores_stale_result_and_closing_it_keeps_worker(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    service = ControlledCreateDirectoryService()
    controller, browser, _created = start_controller_create_directory(
        tmp_path,
        qapp,
        service,
    )
    controller._continue_operations_without_main_window = True
    browser._application_close_guard = lambda _window: True

    try:
        browser.close()
        qapp.processEvents()
        panel = controller._operation_fallback_panel
        assert panel is not None
        operation_id = panel._active_operation_id

        panel.show_result(
            FileOperationResult(
                FileOperationKind.CREATE_DIRECTORY,
                (),
                request_id=999,
                operation_id="stale",
            )
        )
        assert panel._active_operation_id == operation_id
        assert panel.summary_label.text() == "create_directory: running"

        panel.close()
        flush_deferred_deletes(qapp)
        assert controller.file_operation_queue.busy
        assert controller._operation_fallback_panel is None

        service.release.set()
        assert wait_until(
            qapp,
            lambda: not controller.file_operation_queue.busy,
        )
        assert controller.file_operation_queue._pool.activeThreadCount() == 0
    finally:
        cleanup_controller(controller, service, qapp)


def test_fallback_panels_from_two_controllers_do_not_share_state(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_service = ControlledCreateDirectoryService()
    second_service = ControlledCreateDirectoryService()
    first_controller, first_browser, _first_created = (
        start_controller_create_directory(
            tmp_path,
            qapp,
            first_service,
            name="first",
        )
    )
    second_controller, second_browser, _second_created = (
        start_controller_create_directory(
            tmp_path,
            qapp,
            second_service,
            name="second",
        )
    )
    first_controller._continue_operations_without_main_window = True
    second_controller._continue_operations_without_main_window = True
    first_browser._application_close_guard = lambda _window: True
    second_browser._application_close_guard = lambda _window: True

    try:
        first_browser.close()
        second_browser.close()
        qapp.processEvents()
        first_panel = first_controller._operation_fallback_panel
        second_panel = second_controller._operation_fallback_panel

        assert first_panel is not None
        assert second_panel is not None
        assert first_panel is not second_panel

        first_service.release.set()
        assert wait_until(
            qapp,
            lambda: first_controller._operation_fallback_panel is None,
        )
        assert second_controller._operation_fallback_panel is second_panel
        assert second_panel.isVisible()
        assert second_controller.file_operation_queue.busy

        second_service.release.set()
        assert wait_until(
            qapp,
            lambda: second_controller._operation_fallback_panel is None,
        )
        flush_deferred_deletes(qapp)
        assert operation_panels(qapp) == ()
    finally:
        cleanup_controller(first_controller, first_service, qapp)
        cleanup_controller(second_controller, second_service, qapp)
