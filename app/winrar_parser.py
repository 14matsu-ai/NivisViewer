from __future__ import annotations

from .archive_backend import (
    ArchiveBackendError,
    ArchiveEntry,
    ArchiveErrorCode,
    ArchiveListing,
    MAX_ARCHIVE_ENTRIES,
    has_unsafe_control_characters,
    normalize_archive_entry_path,
)


def decode_winrar_output(data: bytes) -> str:
    for encoding in ("utf-8-sig", "mbcs"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def parse_winrar_bare_listing(
    output: str | bytes,
    archive_path: str,
    *,
    maximum_entries: int = MAX_ARCHIVE_ENTRIES,
) -> ArchiveListing:
    text = decode_winrar_output(output) if isinstance(output, bytes) else output
    entries: list[ArchiveEntry] = []
    limit = max(1, int(maximum_entries))
    for raw_line in text.splitlines():
        raw_path = raw_line.rstrip("\r\n")
        if not raw_path:
            continue
        if "\ufffd" in raw_path or has_unsafe_control_characters(raw_path):
            continue
        normalized = normalize_archive_entry_path(raw_path)
        if not normalized:
            continue
        is_directory = raw_path.endswith(("/", "\\"))
        entries.append(
            ArchiveEntry(
                path=normalized.rstrip("/") if is_directory else normalized,
                original_path=raw_path,
                size=None,
                packed_size=None,
                is_directory=is_directory,
                encrypted=False,
            )
        )
        if len(entries) > limit:
            raise ArchiveBackendError(ArchiveErrorCode.TOO_MANY_ENTRIES)
    return ArchiveListing(
        archive_path=archive_path,
        entries=tuple(entries),
        archive_type="RAR",
        solid=None,
        encrypted=False,
    )
