from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest

from app.archive_backend import ArchiveBackendError, ArchiveErrorCode
from app.seven_zip_backend import SevenZipBackend
from app.seven_zip_locator import SevenZipInfo, SevenZipLocator
from app.seven_zip_process import SevenZipProcessResult


LISTING = b"""\
Path = book.7z
Type = 7z
Solid = -
Encrypted = -
----------
Path = \xe6\x97\xa5\xe6\x9c\xac\xe8\xaa\x9e/page1.jpg
Size = 4
Packed Size = 3
Encrypted = -
"""


class FakeRunner:
    def __init__(self, executable: str, results) -> None:
        self.executable = executable
        self.results = results
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


def make_backend(tmp_path: Path, results) -> tuple[SevenZipBackend, FakeRunner]:
    executable = tmp_path / "7z.exe"
    executable.write_bytes(b"fake")
    runner: FakeRunner | None = None

    def runner_factory(path: str) -> FakeRunner:
        nonlocal runner
        runner = FakeRunner(path, list(results))
        return runner

    locator = SevenZipLocator(
        application_dir=tmp_path,
        environment={},
        which=lambda _name: None,
        probe=lambda path: SevenZipInfo(path, True, "7-Zip 26", None),
    )
    backend = SevenZipBackend(
        executable,
        locator=locator,
        runner_factory=runner_factory,
    )
    assert backend.is_available()
    assert runner is not None
    return backend, runner


def result(code: int, stdout: bytes = b"", stderr: bytes = b"") -> SevenZipProcessResult:
    return SevenZipProcessResult((), code, stdout, stderr, 0.01, code == 1)


def test_list_entries_uses_technical_utf8_noninteractive_command(tmp_path: Path) -> None:
    archive = tmp_path / "book.7z"
    archive.write_bytes(b"unchanged")
    backend, runner = make_backend(tmp_path, [result(0, LISTING)])

    listing = backend.list_entries(str(archive))

    assert listing.archive_type == "7z"
    assert listing.entries[0].path == "日本語/page1.jpg"
    arguments = runner.calls[0][0]
    assert arguments[:5] == ["l", "-slt", "-sccUTF-8", "-p-", "-spd"]
    assert arguments[-2:] == ["--", str(archive.absolute())]


def test_read_entry_uses_stdout_and_preserves_unicode_arguments(tmp_path: Path) -> None:
    archive = tmp_path / "日本語.cb7"
    archive.write_bytes(b"archive")
    backend, runner = make_backend(tmp_path, [result(0, b"image-bytes")])

    data = backend.read_entry(str(archive), "第一話/page1.jpg", maximum_bytes=123)

    assert data == b"image-bytes"
    arguments, kwargs = runner.calls[0]
    assert arguments[:5] == ["x", "-so", "-sccUTF-8", "-p-", "-spd"]
    assert arguments[-3:] == ["--", str(archive.absolute()), "第一話/page1.jpg"]
    assert kwargs["maximum_stdout_bytes"] == 123


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (b"Wrong password", ArchiveErrorCode.PASSWORD_REQUIRED),
        (b"Can not open the file as archive", ArchiveErrorCode.CORRUPT_ARCHIVE),
        (b"Unsupported Method", ArchiveErrorCode.UNSUPPORTED_ARCHIVE),
        (b"fatal error", ArchiveErrorCode.PROCESS_FAILED),
    ],
)
def test_process_errors_are_structured(
    tmp_path: Path,
    stderr: bytes,
    expected: ArchiveErrorCode,
) -> None:
    archive = tmp_path / "book.rar"
    archive.write_bytes(b"archive")
    backend, _runner = make_backend(tmp_path, [result(2, stderr=stderr)])

    with pytest.raises(ArchiveBackendError) as captured:
        backend.list_entries(str(archive))

    assert captured.value.code is expected
    assert len(str(captured.value)) < 200


def test_missing_archive_is_classified_before_process_start(tmp_path: Path) -> None:
    backend, runner = make_backend(tmp_path, [])

    with pytest.raises(ArchiveBackendError) as captured:
        backend.list_entries(str(tmp_path / "missing.rar"))

    assert captured.value.code is ArchiveErrorCode.ARCHIVE_NOT_FOUND
    assert runner.calls == []


def test_empty_entry_output_is_not_treated_as_success(tmp_path: Path) -> None:
    archive = tmp_path / "book.rar"
    archive.write_bytes(b"archive")
    backend, _runner = make_backend(tmp_path, [result(0)])

    with pytest.raises(ArchiveBackendError) as captured:
        backend.read_entry(str(archive), "missing.jpg")

    assert captured.value.code is ArchiveErrorCode.ENTRY_NOT_FOUND


def test_runner_timeout_and_cancel_errors_are_preserved(tmp_path: Path) -> None:
    archive = tmp_path / "book.rar"
    archive.write_bytes(b"archive")
    timeout = ArchiveBackendError(ArchiveErrorCode.PROCESS_TIMEOUT)
    cancelled = ArchiveBackendError(ArchiveErrorCode.PROCESS_CANCELLED)
    backend, _runner = make_backend(tmp_path, [timeout, cancelled])

    with pytest.raises(ArchiveBackendError) as first:
        backend.list_entries(str(archive))
    with pytest.raises(ArchiveBackendError) as second:
        backend.list_entries(str(archive), cancel_token=Event())

    assert first.value.code is ArchiveErrorCode.PROCESS_TIMEOUT
    assert second.value.code is ArchiveErrorCode.PROCESS_CANCELLED


def test_listing_warning_is_retained(tmp_path: Path) -> None:
    archive = tmp_path / "book.7z"
    archive.write_bytes(b"archive")
    backend, _runner = make_backend(tmp_path, [result(1, LISTING, b"minor warning")])

    listing = backend.list_entries(str(archive))

    assert listing.warning_messages == ("minor warning",)


def test_extraction_warning_is_not_accepted_as_complete_image(tmp_path: Path) -> None:
    archive = tmp_path / "book.7z"
    archive.write_bytes(b"archive")
    backend, _runner = make_backend(
        tmp_path,
        [result(1, b"partial-image", b"Data Error")],
    )

    with pytest.raises(ArchiveBackendError) as captured:
        backend.read_entry(str(archive), "page.jpg")

    assert captured.value.code is ArchiveErrorCode.CORRUPT_ARCHIVE
