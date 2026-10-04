from __future__ import annotations

from functools import lru_cache
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Iterable

from PIL import Image


GIMP_RENDER_TIMEOUT_SECONDS = 120
GIMP_VERSION_TIMEOUT_SECONDS = 10
GIMP_OUTPUT_MAX_BYTES = 1024 * 1024 * 1024
GIMP_OUTPUT_MAX_PIXELS = 64 * 1024 * 1024
_GIMP_RENDER_LOCK = threading.Lock()
_CANCEL_TOKEN = ContextVar("gimp_cancel_token", default=None)
_CONFIGURED_EXECUTABLE = ""


def configure_gimp_executable(path: str) -> None:
    global _CONFIGURED_EXECUTABLE
    _CONFIGURED_EXECUTABLE = str(path or "").strip().strip('"')
    clear_gimp_discovery_cache()


class GimpXcfBackendError(RuntimeError):
    pass


class GimpXcfBackendUnavailable(GimpXcfBackendError):
    pass


class GimpXcfCancelled(GimpXcfBackendError):
    pass


@contextmanager
def gimp_cancellation(cancel_token):
    """Bind a worker's request token without changing source/loader contracts."""
    binding = _CANCEL_TOKEN.set(cancel_token)
    try:
        yield
    finally:
        _CANCEL_TOKEN.reset(binding)


def _check_cancelled():
    token = _CANCEL_TOKEN.get()
    if token is not None and token.is_set():
        raise GimpXcfCancelled("XCF request cancelled")


@contextmanager
def _render_slot():
    while True:
        _check_cancelled()
        if _GIMP_RENDER_LOCK.acquire(timeout=0.05):
            break
    try:
        _check_cancelled()
        yield
    finally:
        _GIMP_RENDER_LOCK.release()


def _run_process(arguments, *, timeout):
    """Monitor only our child; release its slot only after the child is reaped."""
    _check_cancelled()
    # Files avoid pipe-reader threads outliving cancellation when a GIMP
    # plug-in inherits stdout. Keep only a bounded diagnostic tail in memory.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr,
                                   creationflags=_creation_flags())
        deadline = monotonic() + timeout
        try:
            while True:
                _check_cancelled()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(arguments, timeout)
                try:
                    process.wait(timeout=min(0.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
            _check_cancelled()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        def tail(stream):
            stream.seek(max(0, stream.seek(0, os.SEEK_END) - 8192))
            return stream.read().decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(arguments, process.returncode,
                                           tail(stdout), tail(stderr))


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _candidate_paths() -> Iterable[Path]:
    explicit = os.environ.get("NIVISVIEWER_GIMP_EXE", "").strip()
    if explicit:
        yield Path(explicit)

    for command in (
        "gimp-console-3.2",
        "gimp-console-3.0",
        "gimp-3.2",
        "gimp-3.0",
        "gimp-3",
        "gimp",
    ):
        resolved = shutil.which(command)
        if resolved:
            yield Path(resolved)

    if os.name != "nt":
        return

    install_roots = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        install_roots.append(Path(local_app_data) / "Programs")
    for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            install_roots.append(Path(root))
    for root in install_roots:
        binary_dir = root / "GIMP 3" / "bin"
        if not binary_dir.is_dir():
            continue
        for pattern in (
            "gimp-console-*.exe",
            "gimp-3*.exe",
            "gimp.exe",
        ):
            for path in sorted(binary_dir.glob(pattern), reverse=True):
                yield path


def _parse_version(output: str) -> tuple[int, int, int] | None:
    match = re.search(
        r"(?:GIMP|version)\D+(\d+)\.(\d+)(?:\.(\d+))?",
        output,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
    )


def _probe_version(path: Path) -> tuple[int, int, int] | None:
    try:
        completed = _run_process(
            [os.fspath(path), "--version"],
            timeout=GIMP_VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_version(
        f"{completed.stdout}\n{completed.stderr}"
    )


def _is_console_binary(path: Path) -> bool:
    return "gimp-console" in path.name.casefold()


def _is_usable_candidate(
    path: Path,
    version: tuple[int, int, int],
    *,
    explicit: bool,
) -> bool:
    if version[0] < 3:
        return False
    if explicit or _is_console_binary(path):
        return True

    # The standard Windows executable gained reliable no-interface operation
    # in GIMP 3.0.8. GIMP 3.2+ has it as a normal supported path.
    if os.name == "nt" and version[:2] == (3, 0):
        return version >= (3, 0, 8)
    return True


def find_gimp3_executable() -> str | None:
    explicit = _CONFIGURED_EXECUTABLE or os.environ.get("NIVISVIEWER_GIMP_EXE", "").strip()
    return _find_gimp_executable(explicit)


@lru_cache(maxsize=8)
def _find_gimp_executable(explicit: str) -> str | None:
    explicit_path = (
        Path(explicit).expanduser()
        if explicit
        else None
    )
    seen: set[str] = set()

    for candidate in ([explicit_path] if explicit_path is not None else _candidate_paths()):
        try:
            path = candidate.expanduser().resolve()
        except OSError:
            continue
        key = os.path.normcase(os.fspath(path)).casefold()
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            continue
        version = _probe_version(path)
        if version is None:
            continue
        if _is_usable_candidate(
            path,
            version,
            explicit=(
                explicit_path is not None
                and os.path.normcase(os.fspath(path)).casefold()
                == os.path.normcase(
                    os.fspath(explicit_path.resolve())
                ).casefold()
            ),
        ):
            return os.fspath(path)
    return None


def clear_gimp_discovery_cache() -> None:
    _find_gimp_executable.cache_clear()


def probe_gimp_executable(path: str) -> tuple[str | None, str]:
    """Explicit settings check; called on a worker, without changing runtime config."""
    clear_gimp_discovery_cache()
    explicit = path.strip().strip('"') or os.environ.get("NIVISVIEWER_GIMP_EXE", "").strip()
    found = _find_gimp_executable.__wrapped__(explicit)
    if found is None:
        return None, ""
    version = _probe_version(Path(found))
    return found, ".".join(map(str, version)) if version else ""


def _batch_code(output_path: Path) -> str:
    target = repr(os.fspath(output_path))
    return "\n".join(
        (
            "import gi",
            'gi.require_version("Gimp", "3.0")',
            "from gi.repository import Gimp, Gio",
            "images = Gimp.get_images()",
            'assert len(images) == 1, "NivisViewer expected exactly one image"',
            f"target = Gio.File.new_for_path({target})",
            "ok = Gimp.file_save("
            "Gimp.RunMode.NONINTERACTIVE, images[0], target, None)",
            'assert ok, "GIMP failed to export the XCF composite"',
        )
    )


def _stderr_tail(completed: subprocess.CompletedProcess[str]) -> str:
    text = (completed.stderr or completed.stdout or "").strip()
    if len(text) > 2000:
        text = text[-2000:]
    return text


def _run_gimp(
    executable: str,
    input_path: Path,
    output_path: Path,
) -> None:
    arguments = [
        executable,
        "--new-instance",
        "--no-interface",
        "--no-splash",
        "--console-messages",
        os.fspath(input_path),
        "--batch-interpreter=python-fu-eval",
        "--batch",
        _batch_code(output_path),
        "--quit",
    ]
    try:
        completed = _run_process(
            arguments,
            timeout=GIMP_RENDER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise GimpXcfBackendError(
            "GIMP XCF rendering timed out"
        ) from exc
    except OSError as exc:
        raise GimpXcfBackendError(
            "GIMP could not be started"
        ) from exc

    if completed.returncode != 0 or not output_path.is_file():
        detail = _stderr_tail(completed)
        raise GimpXcfBackendError(
            "GIMP failed to render the XCF"
            + (f": {detail}" if detail else "")
        )
    if output_path.stat().st_size > GIMP_OUTPUT_MAX_BYTES:
        raise GimpXcfBackendError(
            "GIMP render output exceeds the safety limit"
        )


def render_xcf_with_gimp(
    source: str | Path | bytes | bytearray | memoryview,
) -> Image.Image:
    _check_cancelled()
    executable = find_gimp3_executable()
    if executable is None:
        raise GimpXcfBackendUnavailable(
            "GIMP 3.x was not found. Install GIMP 3.x, put it on PATH, "
            "or set NIVISVIEWER_GIMP_EXE."
        )

    with _render_slot():
        with tempfile.TemporaryDirectory(
            prefix="NivisViewer-xcf-"
        ) as temporary_directory:
            temporary = Path(temporary_directory)
            if isinstance(source, (bytes, bytearray, memoryview)):
                input_path = temporary / "input.xcf"
                input_path.write_bytes(bytes(source))
            else:
                input_path = Path(source).resolve()

            output_path = temporary / "render.png"
            _run_gimp(
                executable,
                input_path,
                output_path,
            )
            try:
                with Image.open(
                    output_path,
                    formats=("PNG",),
                ) as image:
                    if image.width * image.height > GIMP_OUTPUT_MAX_PIXELS:
                        raise GimpXcfBackendError(
                            "GIMP render dimensions exceed the safety limit"
                        )
                    image.seek(0)
                    result = image.copy()
                    result.load()
                    return result
            except Exception as exc:
                raise GimpXcfBackendError(
                    "GIMP produced an unreadable PNG render"
                ) from exc


__all__ = [
    "GIMP_RENDER_TIMEOUT_SECONDS",
    "GimpXcfBackendError",
    "GimpXcfBackendUnavailable",
    "clear_gimp_discovery_cache",
    "find_gimp3_executable",
    "render_xcf_with_gimp",
]
