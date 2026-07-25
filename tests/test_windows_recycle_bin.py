from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.file_operation_service import FileOperationService
from app.windows_recycle_bin import (
    FOF_ALLOWUNDO,
    FOF_NOCONFIRMATION,
    RecycleBinResult,
    WindowsRecycleBin,
)


class FakeRecycleBin:
    def __init__(self, results: list[RecycleBinResult]) -> None:
        self.results = list(results)
        self.paths: list[str] = []

    def recycle(self, path: str | Path) -> RecycleBinResult:
        self.paths.append(str(path))
        return self.results.pop(0)


def test_shell_adapter_uses_unicode_double_null_and_no_confirmation(
    tmp_path: Path,
) -> None:
    captured = {}

    def shell_operation(pointer) -> int:
        operation = pointer._obj
        captured["path"] = operation.pFrom
        captured["flags"] = operation.fFlags
        return 0

    target = tmp_path / "日本語.txt"
    adapter = WindowsRecycleBin(shell_operation)

    result = adapter.recycle(target)

    assert result.success
    assert captured["path"].startswith(str(target.absolute()))
    assert captured["flags"] & FOF_ALLOWUNDO
    assert captured["flags"] & FOF_NOCONFIRMATION


def test_shell_adapter_reports_api_failure_and_cancel(tmp_path: Path) -> None:
    failed = WindowsRecycleBin(lambda _pointer: 5)

    def cancelled(pointer) -> int:
        pointer._obj.fAnyOperationsAborted = True
        return 0

    assert failed.recycle(tmp_path / "x").error_code == "shell_error"
    result = WindowsRecycleBin(cancelled).recycle(tmp_path / "日本語")
    assert result.cancelled
    assert not result.success


def test_shell_success_code_is_not_success_if_source_remains(
    tmp_path: Path,
) -> None:
    target = tmp_path / "remains.txt"
    target.write_text("still here", encoding="utf-8")

    result = WindowsRecycleBin(lambda _pointer: 0).recycle(target)

    assert not result.success
    assert result.error_code == "source_remains"
    assert target.exists()


def test_recycle_multiple_items_reports_partial_failure_without_permanent_delete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = tmp_path / "1.txt"
    second = tmp_path / "日本語.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    recycle_bin = FakeRecycleBin(
        [
            RecycleBinResult(True),
            RecycleBinResult(False, error_code="shell_error", error_message="failed"),
        ]
    )
    service = FileOperationService(recycle_bin)
    monkeypatch.setattr(os, "remove", lambda *_args: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(
        shutil,
        "rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
    )

    result = service.recycle([first, second])

    assert [item.success for item in result.items] == [True, False]
    assert recycle_bin.paths == [str(first.absolute()), str(second.absolute())]
    assert first.exists()
    assert second.exists()
