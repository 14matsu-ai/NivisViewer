from __future__ import annotations

from pathlib import Path

from app.file_operation_service import (
    FileOperationKind,
    FileOperationRequest,
    FileOperationService,
)


def test_move_600_files_keeps_and_executes_every_undo_receipt(tmp_path: Path, qapp) -> None:
    source_directory = tmp_path / "source"
    destination_directory = tmp_path / "destination"
    source_directory.mkdir()
    destination_directory.mkdir()
    sources = []
    for index in range(600):
        path = source_directory / f"image-{index:03}.jpg"
        path.write_bytes(f"image-{index}".encode("ascii"))
        sources.append(path)

    service = FileOperationService()
    moved = service.move(sources, destination_directory, request_id=1)

    assert len(moved.successes) == 600
    assert len(moved.undo_entries) == 600
    assert all(not path.exists() for path in sources)
    assert all((destination_directory / path.name).exists() for path in sources)

    undone = service.execute(FileOperationRequest(
        2,
        FileOperationKind.UNDO,
        tuple(entry.path for entry in moved.undo_entries),
        undo_entries=moved.undo_entries,
    ))

    assert len(undone.successes) == 600
    assert not undone.failures
    assert all(path.exists() for path in sources)
    assert all(not (destination_directory / path.name).exists() for path in sources)
