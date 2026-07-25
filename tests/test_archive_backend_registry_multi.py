from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.archive_backend import (
    ArchiveBackendError,
    ArchiveErrorCode,
    ArchiveListing,
)
from app.archive_backend_registry import ArchiveBackendRegistry
from app.windows_file_association import FileAssociationResult


class FakeConfig:
    def __init__(self, **values: str) -> None:
        self.values = values

    def get(self, key: str, default=""):
        return self.values.get(key, default)


class FakeResolver:
    def __init__(self, kinds: dict[str, str | None]) -> None:
        self.kinds = kinds

    def resolve_archive_extension(self, extension: str) -> FileAssociationResult:
        kind = self.kinds.get(extension)
        return FileAssociationResult(
            extension,
            rf"C:\Apps\{kind}.exe" if kind else None,
            kind,
            None if kind else "association_not_found",
        )

    def reset(self) -> None:
        pass


@dataclass(eq=False)
class FakeBackend:
    name: str
    failure: ArchiveErrorCode | None = None

    def __post_init__(self) -> None:
        self.list_calls = 0
        self.read_calls = 0

    def is_available(self) -> bool:
        return self.failure is not ArchiveErrorCode.BACKEND_NOT_FOUND

    def list_entries(self, archive_path: str, *, cancel_token=None) -> ArchiveListing:
        self.list_calls += 1
        if self.failure is not None:
            raise ArchiveBackendError(self.failure)
        return ArchiveListing(archive_path, (), self.name, None, False)

    def read_entry(self, archive_path: str, entry_path: str, **_kwargs) -> bytes:
        self.read_calls += 1
        if self.failure is not None:
            raise ArchiveBackendError(self.failure)
        return self.name.encode()

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


def registry(
    winrar: FakeBackend,
    seven_zip: FakeBackend,
    *,
    kinds: dict[str, str | None],
    preference: str = "auto",
) -> ArchiveBackendRegistry:
    return ArchiveBackendRegistry(
        config_manager=FakeConfig(archive_backend_preference=preference),
        association_resolver=FakeResolver(kinds),  # type: ignore[arg-type]
        winrar_backend=winrar,
        seven_zip_backend=seven_zip,
    )


def test_auto_can_select_different_backend_per_extension() -> None:
    winrar = FakeBackend("winrar")
    seven_zip = FakeBackend("seven_zip")
    selected = registry(
        winrar,
        seven_zip,
        kinds={".rar": "winrar", ".7z": "seven_zip"},
    )

    assert selected.backend_for_path("book.rar").list_entries("book.rar").archive_type == "winrar"  # type: ignore[union-attr]
    assert selected.backend_for_path("book.7z").list_entries("book.7z").archive_type == "seven_zip"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("preference", "expected"),
    [("winrar", "winrar"), ("seven_zip", "seven_zip")],
)
def test_explicit_backend_preference_wins(
    preference: str,
    expected: str,
) -> None:
    selected = registry(
        FakeBackend("winrar"),
        FakeBackend("seven_zip"),
        kinds={".rar": "seven_zip"},
        preference=preference,
    )

    listing = selected.backend_for_path("book.rar").list_entries("book.rar")  # type: ignore[union-attr]
    assert listing.archive_type == expected


@pytest.mark.parametrize(
    "retryable",
    [ArchiveErrorCode.BACKEND_NOT_FOUND, ArchiveErrorCode.UNSUPPORTED_ARCHIVE],
)
def test_auto_retries_one_alternative_only_for_retryable_errors(
    retryable: ArchiveErrorCode,
) -> None:
    winrar = FakeBackend("winrar", retryable)
    seven_zip = FakeBackend("seven_zip")
    selected = registry(
        winrar,
        seven_zip,
        kinds={".rar": "winrar"},
    )

    listing = selected.backend_for_path("book.rar").list_entries("book.rar")  # type: ignore[union-attr]
    assert listing.archive_type == "seven_zip"
    assert winrar.list_calls == seven_zip.list_calls == 1


@pytest.mark.parametrize(
    "terminal",
    [ArchiveErrorCode.PASSWORD_REQUIRED, ArchiveErrorCode.CORRUPT_ARCHIVE],
)
def test_password_and_corruption_do_not_retry(
    terminal: ArchiveErrorCode,
) -> None:
    winrar = FakeBackend("winrar", terminal)
    seven_zip = FakeBackend("seven_zip")
    selected = registry(
        winrar,
        seven_zip,
        kinds={".rar": "winrar"},
    )

    with pytest.raises(ArchiveBackendError) as captured:
        selected.backend_for_path("book.rar").list_entries("book.rar")  # type: ignore[union-attr]

    assert captured.value.code is terminal
    assert seven_zip.list_calls == 0


def test_selection_is_cached_by_extension_and_reset_reselects() -> None:
    winrar = FakeBackend("winrar")
    seven_zip = FakeBackend("seven_zip")
    selected = registry(
        winrar,
        seven_zip,
        kinds={".rar": "winrar"},
    )

    first = selected.backend_for_path("a.rar")
    assert selected.backend_for_path("b.rar") is first
    selected.reset()
    assert selected.backend_for_path("c.rar") is not first
