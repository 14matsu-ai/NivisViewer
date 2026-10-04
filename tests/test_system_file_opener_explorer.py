from __future__ import annotations

from pathlib import Path

import pytest

from app import system_file_opener as opener_module
from app.system_file_opener import (
    SystemFileOpener,
    SystemOpenStatus,
    WindowsSystemOpenAdapter,
)


class RecordingAdapter:
    def __init__(self) -> None:
        self.explorer_calls: list[tuple[str, bool]] = []

    def open_default(self, _path: str, _parent_hwnd: int | None) -> int:
        return 33

    def open_picker(self, _path: str, _parent_hwnd: int | None) -> int:
        return 0

    def open_explorer(self, path: str, is_directory: bool) -> int:
        self.explorer_calls.append((path, is_directory))
        return 0


@pytest.mark.parametrize("suffix", [".jpg", ".zip", ".pdf", ".txt"])
def test_windows_explorer_file_uses_select_with_separate_safe_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    target = tmp_path / f"日本語 & (draft) file{suffix}"
    target.write_bytes(b"data")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(arguments, **kwargs):
        calls.append((list(arguments), dict(kwargs)))
        return object()

    monkeypatch.setattr(opener_module.subprocess, "Popen", fake_popen)

    result = WindowsSystemOpenAdapter().open_explorer(str(target), False)

    assert result == 0
    assert calls == [
        (
            ["explorer.exe", "/select,", str(target)],
            {"shell": False},
        )
    ]


def test_windows_explorer_folder_opens_folder_itself(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = tmp_path / "日本語 folder & symbols"
    folder.mkdir()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(arguments, **kwargs):
        calls.append((list(arguments), dict(kwargs)))
        return object()

    monkeypatch.setattr(opener_module.subprocess, "Popen", fake_popen)

    WindowsSystemOpenAdapter().open_explorer(str(folder), True)

    assert calls == [
        (
            ["explorer.exe", str(folder)],
            {"shell": False},
        )
    ]


@pytest.mark.parametrize("is_directory", [False, True])
def test_system_explorer_opener_normalizes_absolute_path(
    tmp_path: Path,
    is_directory: bool,
) -> None:
    target = tmp_path / ("folder" if is_directory else "file.pdf")
    if is_directory:
        target.mkdir()
    else:
        target.write_bytes(b"pdf")
    adapter = RecordingAdapter()

    result = SystemFileOpener(adapter).open_in_explorer(
        target,
        is_directory=is_directory,
    )

    assert result.status is SystemOpenStatus.OPENED
    assert adapter.explorer_calls == [
        (str(target.absolute()), is_directory)
    ]


def test_system_explorer_opener_rejects_missing_target_without_adapter_call(
    tmp_path: Path,
) -> None:
    adapter = RecordingAdapter()

    result = SystemFileOpener(adapter).open_in_explorer(
        tmp_path / "missing.zip",
        is_directory=False,
    )

    assert result.status is SystemOpenStatus.FAILED
    assert adapter.explorer_calls == []
