from __future__ import annotations

import pytest

from app.file_operation_coordinator import FileOperationCoordinator
from app.file_operation_service import (
    FileOperationItemResult,
    FileOperationItemState,
    FileOperationKind,
    FileOperationResult,
    FileOperationService,
)


class FailingMetadataStore:
    last_error = "injected metadata failure"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def apply_copy_replace_metadata(self, path: str) -> bool:
        self.calls.append(("apply_copy_replace_metadata", (path,)))
        return False

    def apply_move_replace_metadata(self, source: str, destination: str) -> bool:
        self.calls.append(("apply_move_replace_metadata", (source, destination)))
        return False

    def apply_partial_move_replace_metadata(
        self, source: str, destination: str
    ) -> bool:
        self.calls.append(
            ("apply_partial_move_replace_metadata", (source, destination))
        )
        return False

    def relocate_tree(self, source: str, destination: str) -> bool:
        self.calls.append(("relocate_tree", (source, destination)))
        return False


@pytest.mark.parametrize(
    ("operation", "item", "expected_method"),
    [
        (
            FileOperationKind.COPY,
            FileOperationItemResult(
                "source", "destination", True,
                destination_published=True,
                replaced_existing=True,
            ),
            "apply_copy_replace_metadata",
        ),
        (
            FileOperationKind.MOVE,
            FileOperationItemResult(
                "source", "destination", True,
                state=FileOperationItemState.MOVED,
                destination_published=True,
                source_removed=True,
                replaced_existing=True,
            ),
            "apply_move_replace_metadata",
        ),
        (
            FileOperationKind.MOVE,
            FileOperationItemResult(
                "source", "destination", False,
                state=FileOperationItemState.SOURCE_REMOVAL_FAILED,
                destination_published=True,
                source_exists_after=True,
                partial_success=True,
                replaced_existing=True,
            ),
            "apply_partial_move_replace_metadata",
        ),
        (
            FileOperationKind.RENAME,
            FileOperationItemResult(
                "source", "destination", True,
                destination_published=True,
            ),
            "relocate_tree",
        ),
        (
            FileOperationKind.MOVE,
            FileOperationItemResult(
                "source", "destination", True,
                state=FileOperationItemState.MOVED,
                destination_published=True,
                source_removed=True,
            ),
            "relocate_tree",
        ),
    ],
)
def test_metadata_sync_failure_warns_without_reversing_filesystem_success(
    operation: FileOperationKind,
    item: FileOperationItemResult,
    expected_method: str,
) -> None:
    store = FailingMetadataStore()
    coordinator = FileOperationCoordinator(store, service=FileOperationService())
    emitted: list[FileOperationResult] = []
    coordinator.operation_completed.connect(emitted.append)

    coordinator._on_completed(FileOperationResult(operation, (item,)))

    assert len(emitted) == 1
    assert emitted[0].items[0].success is item.success
    assert len(emitted[0].metadata_sync_warnings) == 1
    assert expected_method in emitted[0].metadata_sync_warnings[0]
    assert "injected metadata failure" in emitted[0].metadata_sync_warnings[0]
    assert store.calls[0][0] == expected_method
    coordinator.close()
