from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from app.file_operation_panel import (
    QT_PROGRESS_MAXIMUM,
    FileOperationPanel,
    _project_qt_progress,
)
from app.file_operation_service import FileOperationKind, FileOperationProgress


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
