from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from app.file_operation_coordinator import FileOperationCoordinator
from app.file_operation_panel import (
    QT_PROGRESS_MAXIMUM,
    FileOperationPanel,
    _project_qt_progress,
)
from app.file_operation_queue import FileOperationQueue
from app.file_operation_service import (
    FileOperationItemResult,
    FileOperationKind,
    FileOperationProgress,
    FileOperationRequest,
    FileOperationResult,
    FileOperationService,
)


@pytest.mark.parametrize(
    ("completed", "total"),
    (
        (0, 8 * 1024**3),
        (3 * 1024**3, 8 * 1024**3),
        (8 * 1024**3, 8 * 1024**3),
        (10**18, 10**24),
        (10**24, 10**24),
    ),
)
def test_qt_progress_projection_preserves_ratio_and_endpoints(
    completed: int,
    total: int,
) -> None:
    maximum, value = _project_qt_progress(completed, total)

    assert maximum == QT_PROGRESS_MAXIMUM
    assert 0 <= value <= maximum
    if completed == 0:
        assert value == 0
    elif completed == total:
        assert value == maximum
    else:
        assert 0 < value < maximum
        assert abs(value * total - completed * maximum) <= total // 2 + 1


def test_qt_progress_projection_keeps_small_exact_counts() -> None:
    assert _project_qt_progress(250, 1000) == (1000, 250)
    assert _project_qt_progress(1000, 1000) == (1000, 1000)


def test_file_operation_panel_projects_all_large_progress_without_overflow(
    qapp: QApplication,
) -> None:
    del qapp
    panel = FileOperationPanel()
    item_total = QT_PROGRESS_MAXIMUM + 10_001
    item_completed = item_total // 3
    aggregate_total = 96 * 1024**4
    aggregate_completed = aggregate_total // 4
    current_total = 16 * 1024**4
    current_completed = current_total // 2

    panel.show_progress(
        FileOperationProgress(
            request_id=1,
            operation=FileOperationKind.RECYCLE,
            completed=item_completed,
            total=item_total,
            bytes_completed=aggregate_completed,
            bytes_total=aggregate_total,
            current_file_bytes_completed=current_completed,
            current_file_bytes_total=current_total,
        )
    )

    assert panel.item_progress.maximum() == QT_PROGRESS_MAXIMUM
    assert panel.item_progress.value() == _project_qt_progress(
        item_completed,
        item_total,
    )[1]
    assert panel.byte_progress.maximum() == 1000
    assert panel.byte_progress.value() == 250
    assert panel.current_file_progress.maximum() == QT_PROGRESS_MAXIMUM
    assert panel.current_file_progress.value() == _project_qt_progress(
        current_completed,
        current_total,
    )[1]
    assert panel.byte_progress.format() == (
        f"{aggregate_completed} / {aggregate_total} bytes"
    )
    assert panel.current_file_progress.format() == (
        f"{current_completed} / {current_total}"
    )
    panel.close()


def test_file_operation_panel_preserves_small_and_indeterminate_ranges(
    qapp: QApplication,
) -> None:
    del qapp
    panel = FileOperationPanel()
    below_two_gib = 1024**3
    panel.show_progress(
        FileOperationProgress(
            request_id=1,
            operation=FileOperationKind.COPY,
            completed=1,
            total=2,
            bytes_completed=below_two_gib // 2,
            bytes_total=below_two_gib,
            current_file_bytes_completed=below_two_gib // 2,
            current_file_bytes_total=below_two_gib,
        )
    )
    assert panel.current_file_progress.maximum() == below_two_gib
    assert panel.current_file_progress.value() == below_two_gib // 2
    assert panel.byte_progress.maximum() == 1000
    assert panel.byte_progress.value() == 500

    panel.show_progress(
        FileOperationProgress(
            request_id=1,
            operation=FileOperationKind.COPY,
            completed=0,
            total=1,
            bytes_total=0,
            current_file_bytes_total=None,
        )
    )
    assert (panel.byte_progress.minimum(), panel.byte_progress.maximum()) == (
        0,
        0,
    )
    assert (
        panel.current_file_progress.minimum(),
        panel.current_file_progress.maximum(),
    ) == (0, 0)
    panel.close()


class _FailedRelocationMetadataStore:
    last_error = "injected database is locked"

    def relocate_tree(self, _source: str, _destination: str) -> bool:
        return False


def test_bound_panel_keeps_coordinator_metadata_warning_until_dismissed(
    tmp_path,
    qapp: QApplication,
) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "renamed.txt"
    source.write_text("content", encoding="utf-8")
    queue = FileOperationQueue(service=FileOperationService())
    coordinator = FileOperationCoordinator(
        _FailedRelocationMetadataStore(),
        queue=queue,
    )
    panel = FileOperationPanel()
    panel.bind(queue, completion_source=coordinator)
    destroyed: list[bool] = []
    panel.destroyed.connect(lambda _object=None: destroyed.append(True))

    assert coordinator.execute(
        FileOperationRequest(
            55,
            FileOperationKind.RENAME,
            (str(source),),
            new_name=destination.name,
        )
    )
    assert queue.wait_for_done(3000)
    for _ in range(3):
        qapp.processEvents()

    assert "完了" in panel.summary_label.text()
    assert "メタデータ同期" in panel.summary_label.text()
    assert "injected database is locked" in panel.details_view.toPlainText()
    assert panel.details_button.isVisible()
    assert panel.details_view.isVisible()
    assert panel.cancel_button.text() == "閉じる"
    assert panel.cancel_button.isEnabled()
    assert not panel._hide_timer.isActive()
    assert panel.isVisible()
    assert destroyed == []
    assert not queue.busy
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "content"

    panel.details_button.click()
    assert not panel.details_view.isVisible()
    panel.details_button.click()
    assert panel.details_view.isVisible()
    panel.cancel_button.click()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    assert destroyed == [True]
    coordinator.close()


@pytest.mark.parametrize(
    ("result", "expected_summary"),
    (
        (
            FileOperationResult(
                FileOperationKind.COPY,
                (FileOperationItemResult("source", "destination", True),),
            ),
            "完了",
        ),
        (
            FileOperationResult(
                FileOperationKind.COPY,
                (
                    FileOperationItemResult(
                        "source", "destination", False,
                        error_message="injected failure",
                    ),
                ),
            ),
            "失敗",
        ),
        (
            FileOperationResult(
                FileOperationKind.COPY,
                (),
                cancelled=True,
            ),
            "キャンセルしました",
        ),
    ),
)
def test_bound_panel_keeps_existing_non_warning_terminal_lifecycle(
    result: FileOperationResult,
    expected_summary: str,
    qapp: QApplication,
) -> None:
    queue = FileOperationQueue()
    panel = FileOperationPanel()
    panel.bind(queue)
    destroyed: list[bool] = []
    panel.destroyed.connect(lambda _object=None: destroyed.append(True))

    queue.operation_completed.emit(result)
    assert expected_summary in panel.summary_label.text()
    for _ in range(3):
        qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()

    assert destroyed == [True]
    queue.close()
