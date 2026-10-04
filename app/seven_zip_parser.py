from __future__ import annotations

from collections.abc import Iterable

from .archive_backend import (
    ArchiveBackendError,
    ArchiveEntry,
    ArchiveErrorCode,
    ArchiveListing,
    MAX_ARCHIVE_ENTRIES,
    normalize_archive_entry_path,
)


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value.strip()))
    except (TypeError, ValueError):
        return None


def _optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized in {"+", "yes", "true", "1"}:
        return True
    if normalized in {"-", "no", "false", "0"}:
        return False
    return None


def _blocks(
    lines: Iterable[str],
    *,
    maximum_blocks: int,
) -> list[tuple[dict[str, str], bool]]:
    blocks: list[tuple[dict[str, str], bool]] = []
    current: dict[str, str] = {}
    entries_started = False
    current_is_entry = False

    def finish() -> None:
        nonlocal current
        if current:
            blocks.append((current, current_is_entry))
            if len(blocks) > maximum_blocks:
                raise ArchiveBackendError(ArchiveErrorCode.TOO_MANY_ENTRIES)
            current = {}

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        if stripped and set(stripped) == {"-"} and len(stripped) >= 5:
            finish()
            entries_started = True
            current_is_entry = True
            continue
        if not stripped:
            finish()
            current_is_entry = entries_started
            continue
        if " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        key = key.strip()
        if not key:
            continue
        if key == "Path" and "Path" in current:
            finish()
            current_is_entry = entries_started
        current[key] = value
    finish()
    return blocks


def parse_seven_zip_listing(
    output: str,
    archive_path: str,
    *,
    maximum_entries: int = MAX_ARCHIVE_ENTRIES,
    warning_messages: tuple[str, ...] = (),
) -> ArchiveListing:
    blocks = _blocks(
        output.splitlines(),
        maximum_blocks=max(1, int(maximum_entries)) + 8,
    )
    if not blocks:
        raise ArchiveBackendError(
            ArchiveErrorCode.INVALID_OUTPUT,
            debug_message="7-Zip technical listing did not contain fields",
        )

    header: dict[str, str] | None = None
    entry_blocks: list[dict[str, str]] = []
    for fields, marked_entry in blocks:
        if header is None and (
            not marked_entry
            and (
                "Type" in fields
                or "Physical Size" in fields
                or fields.get("Path") == archive_path
            )
        ):
            header = fields
            continue
        if marked_entry and "Path" in fields:
            entry_blocks.append(fields)

    if header is None:
        for fields, _marked_entry in blocks:
            if "Type" in fields and "Path" in fields:
                header = fields
                break
    if header is None:
        raise ArchiveBackendError(
            ArchiveErrorCode.INVALID_OUTPUT,
            debug_message="7-Zip archive header was not found",
        )
    if len(entry_blocks) > max(1, int(maximum_entries)):
        raise ArchiveBackendError(ArchiveErrorCode.TOO_MANY_ENTRIES)

    entries: list[ArchiveEntry] = []
    for fields in entry_blocks:
        raw_path = fields.get("Path", "")
        if not raw_path:
            continue
        attributes = fields.get("Attributes", "")
        folder_flag = _optional_bool(fields.get("Folder"))
        is_directory = bool(folder_flag) or attributes.startswith("D")
        entries.append(
            ArchiveEntry(
                path=normalize_archive_entry_path(raw_path),
                original_path=raw_path,
                size=_optional_int(fields.get("Size")),
                packed_size=_optional_int(fields.get("Packed Size")),
                is_directory=is_directory,
                encrypted=bool(_optional_bool(fields.get("Encrypted"))),
                modified_time=fields.get("Modified"),
            )
        )

    archive_encrypted = bool(_optional_bool(header.get("Encrypted"))) or any(
        entry.encrypted for entry in entries
    )
    return ArchiveListing(
        archive_path=archive_path,
        entries=tuple(entries),
        archive_type=header.get("Type"),
        solid=_optional_bool(header.get("Solid")),
        encrypted=archive_encrypted,
        warning_messages=tuple(warning_messages),
    )
