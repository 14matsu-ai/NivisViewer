from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from threading import Event, Lock, Semaphore, Thread
import time
from typing import Callable, Sequence

from .archive_backend import ArchiveBackendError, ArchiveErrorCode


DEFAULT_LISTING_LIMIT = 32 * 1024 * 1024
DEFAULT_STDERR_LIMIT = 1024 * 1024
_PROCESS_SLOTS = Semaphore(2)


@dataclass(frozen=True)
class SevenZipProcessResult:
    arguments: tuple[str, ...]
    return_code: int
    stdout: bytes
    stderr: bytes
    elapsed_seconds: float
    warning: bool = False


class SevenZipProcessRunner:
    def __init__(
        self,
        executable_path: str | Path,
        *,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        executable = Path(executable_path)
        if not executable.is_absolute():
            raise ValueError("7-Zip executable path must be absolute")
        self.executable_path = str(executable)
        self._popen_factory = popen_factory
        self._active_lock = Lock()
        self._active_processes: set[subprocess.Popen[bytes]] = set()

    def run(
        self,
        arguments: Sequence[str],
        *,
        cancel_token=None,
        timeout_seconds: float = 30.0,
        maximum_stdout_bytes: int = DEFAULT_LISTING_LIMIT,
        maximum_stderr_bytes: int = DEFAULT_STDERR_LIMIT,
    ) -> SevenZipProcessResult:
        if not isinstance(arguments, (list, tuple)):
            raise TypeError("7-Zip arguments must be a list or tuple")
        if _is_cancelled(cancel_token):
            raise ArchiveBackendError(ArchiveErrorCode.PROCESS_CANCELLED)

        acquired = False
        while not acquired:
            if _is_cancelled(cancel_token):
                raise ArchiveBackendError(ArchiveErrorCode.PROCESS_CANCELLED)
            acquired = _PROCESS_SLOTS.acquire(timeout=0.05)

        started_at = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        try:
            command = [self.executable_path, *[str(value) for value in arguments]]
            creation_flags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            )
            try:
                process = self._popen_factory(
                    command,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=creation_flags,
                )
            except (OSError, ValueError) as exc:
                raise ArchiveBackendError(
                    ArchiveErrorCode.BACKEND_NOT_FOUND,
                    debug_message=str(exc),
                ) from exc

            with self._active_lock:
                self._active_processes.add(process)
            stdout, stderr, exceeded = self._collect_output(
                process,
                cancel_token=cancel_token,
                timeout_seconds=max(0.1, float(timeout_seconds)),
                maximum_stdout_bytes=max(1, int(maximum_stdout_bytes)),
                maximum_stderr_bytes=max(1, int(maximum_stderr_bytes)),
                started_at=started_at,
            )
            if exceeded:
                raise ArchiveBackendError(
                    ArchiveErrorCode.ENTRY_TOO_LARGE,
                    debug_message="7-Zip output exceeded the configured byte limit",
                )
            return_code = int(process.returncode or 0)
            return SevenZipProcessResult(
                arguments=tuple(command),
                return_code=return_code,
                stdout=stdout,
                stderr=stderr,
                elapsed_seconds=max(0.0, time.monotonic() - started_at),
                warning=return_code == 1,
            )
        finally:
            if process is not None:
                with self._active_lock:
                    self._active_processes.discard(process)
            _PROCESS_SLOTS.release()

    def cancel_all(self) -> None:
        with self._active_lock:
            processes = tuple(self._active_processes)
        for process in processes:
            self._stop_process(process)

    def _collect_output(
        self,
        process: subprocess.Popen[bytes],
        *,
        cancel_token,
        timeout_seconds: float,
        maximum_stdout_bytes: int,
        maximum_stderr_bytes: int,
        started_at: float,
    ) -> tuple[bytes, bytes, bool]:
        stdout_data = bytearray()
        stderr_data = bytearray()
        output_exceeded = Event()

        def read_pipe(pipe, target: bytearray, limit: int) -> None:
            if pipe is None:
                return
            try:
                while True:
                    chunk = pipe.read(64 * 1024)
                    if not chunk:
                        return
                    remaining = limit - len(target)
                    if remaining > 0:
                        target.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        output_exceeded.set()
                        return
            except (OSError, ValueError):
                return

        stdout_thread = Thread(
            target=read_pipe,
            args=(process.stdout, stdout_data, maximum_stdout_bytes),
            daemon=True,
        )
        stderr_thread = Thread(
            target=read_pipe,
            args=(process.stderr, stderr_data, maximum_stderr_bytes),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        failure: ArchiveBackendError | None = None
        while process.poll() is None:
            if _is_cancelled(cancel_token):
                failure = ArchiveBackendError(ArchiveErrorCode.PROCESS_CANCELLED)
                break
            if output_exceeded.is_set():
                break
            if time.monotonic() - started_at >= timeout_seconds:
                failure = ArchiveBackendError(ArchiveErrorCode.PROCESS_TIMEOUT)
                break
            time.sleep(0.02)

        if process.poll() is None:
            self._stop_process(process)
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)
        if failure is not None:
            raise failure
        return bytes(stdout_data), bytes(stderr_data), output_exceeded.is_set()

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=0.5)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.kill()
            process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _is_cancelled(cancel_token) -> bool:
    if cancel_token is None:
        return False
    checker = getattr(cancel_token, "is_set", None)
    if callable(checker):
        return bool(checker())
    checker = getattr(cancel_token, "is_cancelled", None)
    if callable(checker):
        return bool(checker())
    return bool(getattr(cancel_token, "cancelled", False))
