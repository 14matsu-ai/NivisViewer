from __future__ import annotations

from pathlib import Path

import pytest

from app.archive_backend import ArchiveBackendError, ArchiveErrorCode
from app.winrar_backend import WinRARBackend
from app.winrar_locator import WinRARInfo, WinRARLocator
from app.winrar_process import WinRARProcessResult


class FakeRunner:
    def __init__(self, _executable: str, results) -> None:
        self.results = list(results)
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.cancelled = False

    def run(self, arguments, **kwargs):
        self.calls.append((list(arguments), kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def cancel_all(self) -> None:
        self.cancelled = True


def result(
    code: int,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> WinRARProcessResult:
    return WinRARProcessResult((), code, stdout, stderr, 0.01)


def make_backend(
    tmp_path: Path,
    results,
    *,
    extension: str = ".rar",
) -> tuple[WinRARBackend, FakeRunner]:
    executable = tmp_path / "UnRAR.exe"
    executable.write_bytes(b"fake")
    runner: FakeRunner | None = None

    def factory(path: str) -> FakeRunner:
        nonlocal runner
        runner = FakeRunner(path, results)
        return runner

    locator = WinRARLocator(
        environment={},
        which=lambda _name: None,
        probe=lambda path: WinRARInfo(path, True, "7.13 x64", None),
    )
    backend = WinRARBackend(
        executable,
        locator=locator,
        runner_factory=factory,
        archive_extension=extension,
    )
    assert backend.is_available()
    assert runner is not None
    return backend, runner


@pytest.mark.parametrize("suffix", [".rar", ".cbr", ".7z", ".cb7"])
def test_bare_utf8_listing_command_supports_external_extensions(
    tmp_path: Path,
    suffix: str,
) -> None:
    archive = tmp_path / f"日本語{suffix}"
    archive.write_bytes(b"unchanged")
    before = archive.stat().st_mtime_ns
    backend, runner = make_backend(
        tmp_path,
        [result(0, "第一話/page1.jpg\r\npage2.jpg\r\n".encode())],
        extension=suffix,
    )

    listing = backend.list_entries(str(archive))

    assert [entry.path for entry in listing.entries] == [
        "第一話/page1.jpg",
        "page2.jpg",
    ]
    assert runner.calls[0][0] == [
        "lb",
        "-scfr",
        "--",
        str(archive.absolute()),
    ]
    assert archive.stat().st_mtime_ns == before


def test_single_entry_is_printed_to_stdout_without_temp_extraction(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "book.rar"
    archive.write_bytes(b"archive")
    backend, runner = make_backend(tmp_path, [result(0, b"image-bytes")])

    data = backend.read_entry(str(archive), "日本語/page.jpg", maximum_bytes=99)

    assert data == b"image-bytes"
    assert runner.calls[0][0] == [
        "p",
        "-inul",
        "--",
        str(archive.absolute()),
        "日本語/page.jpg",
    ]
    assert runner.calls[0][1]["maximum_stdout_bytes"] == 99


@pytest.mark.parametrize(
    ("return_code", "expected"),
    [
        (3, ArchiveErrorCode.CORRUPT_ARCHIVE),
        (7, ArchiveErrorCode.UNSUPPORTED_ARCHIVE),
        (11, ArchiveErrorCode.PASSWORD_REQUIRED),
        (13, ArchiveErrorCode.CORRUPT_ARCHIVE),
        (255, ArchiveErrorCode.PROCESS_CANCELLED),
    ],
)
def test_return_codes_are_structured(
    tmp_path: Path,
    return_code: int,
    expected: ArchiveErrorCode,
) -> None:
    archive = tmp_path / "book.rar"
    archive.write_bytes(b"archive")
    backend, _runner = make_backend(tmp_path, [result(return_code)])

    with pytest.raises(ArchiveBackendError) as captured:
        backend.list_entries(str(archive))

    assert captured.value.code is expected


def test_entry_not_found_and_empty_output_are_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "book.rar"
    archive.write_bytes(b"archive")
    backend, _runner = make_backend(tmp_path, [result(10), result(0)])

    with pytest.raises(ArchiveBackendError) as missing:
        backend.read_entry(str(archive), "missing.jpg")
    with pytest.raises(ArchiveBackendError) as empty:
        backend.read_entry(str(archive), "empty.jpg")

    assert missing.value.code is ArchiveErrorCode.ENTRY_NOT_FOUND
    assert empty.value.code is ArchiveErrorCode.ENTRY_NOT_FOUND
