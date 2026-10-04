from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess
from threading import Event

import pytest

from app.archive_backend import ArchiveBackendError, ArchiveErrorCode
from app.seven_zip_process import SevenZipProcessRunner


class FakeProcess:
    def __init__(
        self,
        stdout: bytes = b"out",
        stderr: bytes = b"err",
        *,
        running: bool = False,
        terminate_finishes: bool = True,
    ) -> None:
        self.stdout = BytesIO(stdout)
        self.stderr = BytesIO(stderr)
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


def test_runner_uses_argument_list_no_shell_closed_stdin_and_hidden_console(
    tmp_path: Path,
) -> None:
    executable = (tmp_path / "7z.exe").absolute()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess(b"stdout", b"stderr")

    runner = SevenZipProcessRunner(executable, popen_factory=popen)
    result = runner.run(["l", "--", "日本語.rar"])

    assert result.stdout == b"stdout"
    assert result.stderr == b"stderr"
    assert calls[0][0] == [str(executable), "l", "--", "日本語.rar"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert calls[0][1]["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_runner_requires_absolute_executable() -> None:
    with pytest.raises(ValueError):
        SevenZipProcessRunner("7z.exe")


def test_pre_cancelled_request_does_not_start_process(tmp_path: Path) -> None:
    calls: list[bool] = []
    cancelled = Event()
    cancelled.set()
    runner = SevenZipProcessRunner(
        (tmp_path / "7z.exe").absolute(),
        popen_factory=lambda *_args, **_kwargs: calls.append(True),
    )

    with pytest.raises(ArchiveBackendError) as captured:
        runner.run(["l"], cancel_token=cancelled)

    assert captured.value.code is ArchiveErrorCode.PROCESS_CANCELLED
    assert calls == []


def test_output_limit_stops_process_and_returns_structured_error(tmp_path: Path) -> None:
    process = FakeProcess(b"x" * 100)
    runner = SevenZipProcessRunner(
        (tmp_path / "7z.exe").absolute(),
        popen_factory=lambda *_args, **_kwargs: process,
    )

    with pytest.raises(ArchiveBackendError) as captured:
        runner.run(["x"], maximum_stdout_bytes=10)

    assert captured.value.code is ArchiveErrorCode.ENTRY_TOO_LARGE


def test_timeout_terminates_then_kills_if_needed(tmp_path: Path) -> None:
    process = FakeProcess(running=True, terminate_finishes=False)
    runner = SevenZipProcessRunner(
        (tmp_path / "7z.exe").absolute(),
        popen_factory=lambda *_args, **_kwargs: process,
    )

    with pytest.raises(ArchiveBackendError) as captured:
        runner.run(["l"], timeout_seconds=0.1)

    assert captured.value.code is ArchiveErrorCode.PROCESS_TIMEOUT
    assert process.terminated
    assert process.killed


def test_launch_failure_is_backend_not_found(tmp_path: Path) -> None:
    def failed(*_args, **_kwargs):
        raise OSError("cannot execute")

    runner = SevenZipProcessRunner(
        (tmp_path / "7z.exe").absolute(),
        popen_factory=failed,
    )

    with pytest.raises(ArchiveBackendError) as captured:
        runner.run(["i"])

    assert captured.value.code is ArchiveErrorCode.BACKEND_NOT_FOUND
