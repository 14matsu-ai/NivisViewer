from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess

import pytest

from app.archive_backend import ArchiveBackendError, ArchiveErrorCode
from app.winrar_process import WinRARProcessRunner


class FakeProcess:
    def __init__(self, *, running: bool = False, terminate_finishes: bool = True):
        self.stdout = BytesIO(b"output")
        self.stderr = BytesIO(b"")
        self.returncode = None if running else 0
        self.terminate_finishes = terminate_finishes
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if self.terminate_finishes:
            self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode


def test_runner_uses_safe_subprocess_boundary(tmp_path: Path) -> None:
    executable = (tmp_path / "UnRAR.exe").absolute()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    result = WinRARProcessRunner(executable, popen_factory=popen).run(
        ["lb", "--", "日本語.rar"]
    )

    assert result.stdout == b"output"
    assert calls[0][0] == [str(executable), "lb", "--", "日本語.rar"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert calls[0][1]["creationflags"] == getattr(
        subprocess,
        "CREATE_NO_WINDOW",
        0,
    )


def test_runner_requires_absolute_executable() -> None:
    with pytest.raises(ValueError):
        WinRARProcessRunner("UnRAR.exe")


def test_timeout_terminates_then_kills_and_leaves_no_active_process(
    tmp_path: Path,
) -> None:
    process = FakeProcess(running=True, terminate_finishes=False)
    runner = WinRARProcessRunner(
        (tmp_path / "UnRAR.exe").absolute(),
        popen_factory=lambda *_args, **_kwargs: process,
    )

    with pytest.raises(ArchiveBackendError) as captured:
        runner.run(["lb"], timeout_seconds=0.1)

    assert captured.value.code is ArchiveErrorCode.PROCESS_TIMEOUT
    assert process.terminated and process.killed
    assert not runner._active_processes
