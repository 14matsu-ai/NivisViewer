from __future__ import annotations

from .i18n import tr

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
import re
from typing import Protocol, Sequence

from natsort import natsorted


EXTERNAL_ARCHIVE_EXTENSIONS = frozenset({".rar", ".cbr", ".7z", ".cb7"})
MAX_ARCHIVE_ENTRIES = 100_000
MAX_IMAGE_ENTRY_BYTES = 1024 * 1024 * 1024


class ArchiveErrorCode(str, Enum):
    BACKEND_NOT_FOUND = "backend_not_found"
    ARCHIVE_NOT_FOUND = "archive_not_found"
    UNSUPPORTED_ARCHIVE = "unsupported_archive"
    CORRUPT_ARCHIVE = "corrupt_archive"
    PASSWORD_REQUIRED = "password_required"
    ENTRY_NOT_FOUND = "entry_not_found"
    ENTRY_TOO_LARGE = "entry_too_large"
    TOO_MANY_ENTRIES = "too_many_entries"
    PROCESS_TIMEOUT = "process_timeout"
    PROCESS_CANCELLED = "process_cancelled"
    PROCESS_FAILED = "process_failed"
    INVALID_OUTPUT = "invalid_output"


_USER_MESSAGES = {
    ArchiveErrorCode.BACKEND_NOT_FOUND: (
        "RAR／7zを扱えるWinRARまたは7-Zipが見つかりません。"
        "設定から外部書庫バックエンドを確認してください。"
    ),
    ArchiveErrorCode.ARCHIVE_NOT_FOUND: "書庫が見つかりません。",
    ArchiveErrorCode.UNSUPPORTED_ARCHIVE: "この書庫形式には対応していません。",
    ArchiveErrorCode.CORRUPT_ARCHIVE: "書庫を開けません。壊れている可能性があります。",
    ArchiveErrorCode.PASSWORD_REQUIRED: "パスワード付き書庫は現在開けません。",
    ArchiveErrorCode.ENTRY_NOT_FOUND: "書庫内の画像が見つかりません。",
    ArchiveErrorCode.ENTRY_TOO_LARGE: "書庫内の画像が大きすぎます。",
    ArchiveErrorCode.TOO_MANY_ENTRIES: "書庫内の項目数が多すぎます。",
    ArchiveErrorCode.PROCESS_TIMEOUT: "外部書庫処理がタイムアウトしました。",
    ArchiveErrorCode.PROCESS_CANCELLED: "外部書庫処理をキャンセルしました。",
    ArchiveErrorCode.PROCESS_FAILED: "外部バックエンドで書庫を処理できませんでした。",
    ArchiveErrorCode.INVALID_OUTPUT: "外部バックエンドの書庫情報を解析できませんでした。",
}


class ArchiveBackendError(RuntimeError):
    def __init__(
        self,
        code: ArchiveErrorCode | str,
        *,
        user_message: str | None = None,
        debug_message: str | None = None,
    ) -> None:
        self.code = ArchiveErrorCode(code)
        self.user_message = user_message or tr(_USER_MESSAGES[self.code])
        self.debug_message = debug_message
        super().__init__(self.user_message)


@dataclass(frozen=True)
class ArchiveEntry:
    path: str
    size: int | None
    packed_size: int | None
    is_directory: bool
    encrypted: bool
    modified_time: str | None = None
    original_path: str | None = None

    @property
    def extraction_path(self) -> str:
        return self.original_path if self.original_path is not None else self.path


@dataclass(frozen=True)
class ArchiveListing:
    archive_path: str
    entries: tuple[ArchiveEntry, ...]
    archive_type: str | None
    solid: bool | None
    encrypted: bool
    warning_messages: tuple[str, ...] = ()


class ArchiveBackend(Protocol):
    def is_available(self) -> bool:
        ...

    def list_entries(
        self,
        archive_path: str,
        *,
        cancel_token=None,
    ) -> ArchiveListing:
        ...

    def read_entry(
        self,
        archive_path: str,
        entry_path: str,
        *,
        cancel_token=None,
        maximum_bytes: int | None = None,
    ) -> bytes:
        ...


def normalize_archive_entry_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def has_unsafe_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def select_image_entries(
    listing: ArchiveListing,
    supported_extensions: Sequence[str] | set[str] | frozenset[str],
    *,
    descending: bool = False,
) -> tuple[ArchiveEntry, ...]:
    extensions = {str(extension).lower() for extension in supported_extensions}
    selected: list[ArchiveEntry] = []
    seen_paths: set[str] = set()
    for entry in listing.entries:
        path = normalize_archive_entry_path(entry.path)
        if entry.is_directory or not path or has_unsafe_control_characters(path):
            continue
        if path.startswith("@"):
            continue
        parts = PurePosixPath(path).parts
        if any(part.casefold() == "__macosx" for part in parts):
            continue
        leaf = parts[-1] if parts else path
        if leaf.casefold() in {".ds_store", "thumbs.db"}:
            continue
        if any(part.startswith((".", "~$")) for part in parts):
            continue
        if PurePosixPath(path).suffix.lower() not in extensions:
            continue
        key = path.casefold()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        selected.append(
            ArchiveEntry(
                path=path,
                size=entry.size,
                packed_size=entry.packed_size,
                is_directory=False,
                encrypted=entry.encrypted,
                modified_time=entry.modified_time,
                original_path=entry.extraction_path,
            )
        )
    return tuple(
        natsorted(
            selected,
            key=lambda item: item.path,
            reverse=bool(descending),
        )
    )


_LATER_PART_RAR = re.compile(r"(?i)\.part0*(\d+)\.rar$")
_OLD_RAR_VOLUME = re.compile(r"(?i)\.r\d\d$")


def is_supported_archive_candidate(name: str) -> bool:
    lowered = str(name).casefold()
    if _OLD_RAR_VOLUME.search(lowered):
        return False
    part_match = _LATER_PART_RAR.search(lowered)
    if part_match is not None and int(part_match.group(1)) > 1:
        return False
    return any(lowered.endswith(extension) for extension in EXTERNAL_ARCHIVE_EXTENSIONS)
