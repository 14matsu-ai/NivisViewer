from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.application_controller import (
    ApplicationController,
    FileOperationCloseChoice,
)
from app.config_manager import ConfigManager
from app.file_operation_service import (
    FileOperationErrorCode,
    FileOperationItemResult,
    FileOperationKind,
    FileOperationRequest,
    FileOperationResult,
)
from app.file_operation_worker import FileOperationExecutor


class ControlledOperationService:
    def __init__(self, outcome: str = "success") -> None:
        self.outcome = outcome
        self.started = Event()
        self.release = Event()
        self.cancel_observed = False

    def execute(self, request, *, cancelled, progress):
        del progress
        self.started.set()
        self.release.wait(3)
        self.cancel_observed = cancelled.is_set()
        if self.outcome == "exception":
            raise RuntimeError("fake worker failure")
        if self.outcome == "failure":
            items = (
                FileOperationItemResult(
                    request.source_paths[0],
                    request.destination_directory,
                    False,
                    FileOperationErrorCode.IO_ERROR.value,
                    "fake operation failure",
                ),
            )
        else:
            items = ()
        return FileOperationResult(
            request.operation,
            items,
            cancelled=self.cancel_observed,
            request_id=request.request_id,
            operation_id=request.operation_id,
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


def start_controlled_operation(
    tmp_path: Path,
    qapp: QApplication,
    service: ControlledOperationService,
    *,
    name: str = "controller",
) -> tuple[ApplicationController, object]:
    root = tmp_path / name
    root.mkdir()
    source = root / "source.txt"
    destination = root / "destination"
    source.write_text("data", encoding="utf-8")
    destination.mkdir()
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(root / "config.json"),
    )
    controller._exit_evaluation_suspended += 1
    controller.file_operation_queue.service = service  # type: ignore[assignment]
    browser = controller.start(restore=False)
    browser.show()
    assert browser._start_file_operation(
        FileOperationKind.COPY,
        sources=(str(source),),
        destination=destination,
    )
    assert wait_until(qapp, service.started.is_set)
    assert controller.file_operation_queue.busy
    assert browser._active_file_operation_id is not None
    return controller, browser


def finish_and_cleanup(
    controller: ApplicationController,
    service: ControlledOperationService,
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
    controller.shutdown()


def test_file_operation_close_dialog_has_explicit_japanese_buttons(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
    )
    controller._exit_evaluation_suspended += 1
    browser = controller.start(restore=False)

    dialog, buttons = controller._build_file_operation_close_dialog(browser)

    assert dialog.windowTitle() == "ファイル操作を実行中"
    assert dialog.text() == "ファイル操作が完了していません。どうしますか？"
    assert {
        button.text()
        for button in buttons.values()
    } == {
        "完了後に閉じる",
        "操作を中止して閉じる",
        "閉じない",
    }
    assert dialog.standardButtons() == QMessageBox.StandardButton.NoButton
    dialog.close()
    browser._application_close_guard = lambda _window: True
    browser.close()
    qapp.processEvents()
    controller._exit_evaluation_suspended -= 1
    controller.shutdown()


def test_keep_open_leaves_window_and_operation_running(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    service = ControlledOperationService()
    controller, browser = start_controlled_operation(tmp_path, qapp, service)
    prompts = []

    def keep_open_with_reentrant_close_check(window):
        prompts.append(True)
        assert not controller._allow_window_close(window)
        return FileOperationCloseChoice.KEEP_OPEN

    monkeypatch.setattr(
        controller,
        "_ask_file_operation_close_choice",
        keep_open_with_reentrant_close_check,
    )
    closing = []
    browser.closing.connect(lambda _window: closing.append(True))

    try:
        browser.close()
        qapp.processEvents()

        assert prompts == [True]
        assert closing == []
        assert browser.isVisible()
        assert controller.file_operation_queue.busy
        assert not browser._close_after_operation
        assert not browser._close_after_cancel

        service.release.set()
        assert wait_until(qapp, lambda: not controller.file_operation_queue.busy)
        assert closing == []
        assert browser.isVisible()
    finally:
        finish_and_cleanup(controller, service, qapp)


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_close_after_operation_waits_for_terminal_result_and_closes_once(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
    outcome: str,
) -> None:
    service = ControlledOperationService(outcome)
    controller, browser = start_controlled_operation(tmp_path, qapp, service)
    prompts = []
    monkeypatch.setattr(
        controller,
        "_ask_file_operation_close_choice",
        lambda _window: (
            prompts.append(True)
            or FileOperationCloseChoice.CLOSE_AFTER_OPERATION
        ),
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: None)
    closing = []
    exits = []
    browser.closing.connect(lambda _window: closing.append(True))
    controller.exit_requested.connect(lambda: exits.append(True))

    try:
        browser.close()
        browser.close()
        qapp.processEvents()

        assert prompts == [True]
        assert closing == []
        assert exits == []
        assert browser.isVisible()
        assert browser._close_after_operation

        service.release.set()
        assert wait_until(qapp, lambda: len(closing) == 1)
        qapp.processEvents()

        assert closing == [True]
        assert exits == []
        assert not controller.file_operation_queue.busy
    finally:
        finish_and_cleanup(controller, service, qapp)


def test_cancel_and_close_requests_cancel_once_and_waits_for_completion(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    service = ControlledOperationService()
    controller, browser = start_controlled_operation(tmp_path, qapp, service)
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
        assert browser.isVisible()
        assert browser._close_after_cancel
        assert browser._cancel_requested
        assert "ファイル操作を中止しています" in (
            browser.statusBar().currentMessage()
        )

        service.release.set()
        assert wait_until(qapp, lambda: len(closing) == 1)

        assert service.cancel_observed
        assert len(cancel_calls) == 1
        assert closing == [True]
        assert not controller.file_operation_queue.busy
    finally:
        finish_and_cleanup(controller, service, qapp)


def test_worker_exception_releases_busy_and_closes_waiting_browser(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    service = ControlledOperationService("exception")
    controller, browser = start_controlled_operation(tmp_path, qapp, service)
    monkeypatch.setattr(
        controller,
        "_ask_file_operation_close_choice",
        lambda _window: FileOperationCloseChoice.CLOSE_AFTER_OPERATION,
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: None)
    closing = []
    completed = []
    browser.closing.connect(lambda _window: closing.append(True))
    controller.file_operation_queue.operation_completed.connect(completed.append)

    try:
        browser.close()
        service.release.set()
        assert wait_until(qapp, lambda: len(closing) == 1)

        assert len(completed) == 1
        assert completed[0].failures
        assert "fake worker failure" in completed[0].failures[0].error_message
        assert not controller.file_operation_queue.busy
        assert closing == [True]
    finally:
        finish_and_cleanup(controller, service, qapp)


def test_executor_worker_exception_emits_terminal_failure_and_clears_busy(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    service = ControlledOperationService("exception")
    executor = FileOperationExecutor(service)  # type: ignore[arg-type]
    completed = []
    executor.operation_completed.connect(completed.append)
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination"
    source.write_text("data", encoding="utf-8")
    destination.mkdir()
    request = FileOperationRequest(
        1,
        FileOperationKind.COPY,
        (str(source),),
        str(destination),
        operation_id="executor-operation",
    )

    assert executor.execute(request)
    assert service.started.wait(1)
    service.release.set()
    assert executor.wait_for_done(3000)
    assert wait_until(qapp, lambda: bool(completed))

    assert len(completed) == 1
    assert completed[0].operation_id == "executor-operation"
    assert completed[0].failures
    assert "fake worker failure" in completed[0].failures[0].error_message
    assert not executor.busy
    executor.close()


def test_stale_terminal_result_does_not_release_current_close_wait(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    service = ControlledOperationService()
    controller, browser = start_controlled_operation(tmp_path, qapp, service)
    monkeypatch.setattr(
        controller,
        "_ask_file_operation_close_choice",
        lambda _window: FileOperationCloseChoice.CLOSE_AFTER_OPERATION,
    )
    closing = []
    browser.closing.connect(lambda _window: closing.append(True))

    try:
        browser.close()
        expected_operation_id = browser._close_operation_id
        controller._on_background_file_operation_completed(
            FileOperationResult(
                FileOperationKind.COPY,
                (),
                request_id=999,
                operation_id="stale-operation",
            )
        )
        qapp.processEvents()

        assert browser._close_operation_id == expected_operation_id
        assert browser._close_after_operation
        assert closing == []

        service.release.set()
        assert wait_until(qapp, lambda: len(closing) == 1)
        assert closing == [True]
    finally:
        finish_and_cleanup(controller, service, qapp)


def test_two_controllers_keep_browser_close_state_independent(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    first_service = ControlledOperationService()
    second_service = ControlledOperationService()
    first_controller, first_browser = start_controlled_operation(
        tmp_path,
        qapp,
        first_service,
        name="first",
    )
    second_controller, second_browser = start_controlled_operation(
        tmp_path,
        qapp,
        second_service,
        name="second",
    )
    monkeypatch.setattr(
        first_controller,
        "_ask_file_operation_close_choice",
        lambda _window: FileOperationCloseChoice.CLOSE_AFTER_OPERATION,
    )
    first_closing = []
    second_closing = []
    first_browser.closing.connect(lambda _window: first_closing.append(True))
    second_browser.closing.connect(lambda _window: second_closing.append(True))

    try:
        first_browser.close()
        assert first_browser._close_after_operation
        assert not second_browser._close_after_operation

        second_service.release.set()
        assert wait_until(
            qapp,
            lambda: not second_controller.file_operation_queue.busy,
        )
        assert first_closing == []
        assert second_closing == []

        first_service.release.set()
        assert wait_until(qapp, lambda: first_closing == [True])
        assert second_closing == []
        assert second_browser.isVisible()
    finally:
        finish_and_cleanup(first_controller, first_service, qapp)
        finish_and_cleanup(second_controller, second_service, qapp)
