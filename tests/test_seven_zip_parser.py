from __future__ import annotations

import pytest

from app.archive_backend import ArchiveBackendError, ArchiveErrorCode
from app.seven_zip_parser import parse_seven_zip_listing


RAR_OUTPUT = """\
Listing archive: C:\\漫画\\本.cbr

--
Path = C:\\漫画\\本.cbr
Type = Rar5
Physical Size = 1234
Solid = +
Encrypted = -

----------
Path = 第一話\\page1.jpg
Size = 100
Packed Size = 80
Modified = 2026-01-02 03:04:05
Attributes = A
Encrypted = -

Path = 第一話\\page2.jpg
Size = 200
Packed Size = 150
Attributes = A
Encrypted = -
Unknown Field = ignored

Path = 第一話
Size = 0
Packed Size = 0
Folder = +
Attributes = D
Encrypted = -
"""


def test_parser_separates_header_and_entries_and_preserves_unicode() -> None:
    result = parse_seven_zip_listing(RAR_OUTPUT, r"C:\漫画\本.cbr")

    assert result.archive_type == "Rar5"
    assert result.solid is True
    assert result.encrypted is False
    assert [entry.path for entry in result.entries] == [
        "第一話/page1.jpg",
        "第一話/page2.jpg",
        "第一話",
    ]
    assert result.entries[0].size == 100
    assert result.entries[0].packed_size == 80
    assert result.entries[0].modified_time == "2026-01-02 03:04:05"
    assert result.entries[-1].is_directory


def test_parser_handles_empty_archive() -> None:
    result = parse_seven_zip_listing(
        "Path = empty.7z\nType = 7z\nPhysical Size = 32\nSolid = -\n",
        "empty.7z",
    )

    assert result.archive_type == "7z"
    assert result.entries == ()
    assert result.solid is False


def test_parser_marks_entry_or_header_encryption() -> None:
    output = """\
Path = encrypted.7z
Type = 7z
Encrypted = -
----------
Path = page.jpg
Size = 1
Encrypted = +
"""
    assert parse_seven_zip_listing(output, "encrypted.7z").encrypted is True


def test_parser_accepts_duplicate_keys_without_crashing() -> None:
    output = """\
Path = book.7z
Type = unknown
Type = 7z
----------
Path = first.jpg
Path = second.jpg
Size = invalid
"""
    result = parse_seven_zip_listing(output, "book.7z")

    assert result.archive_type == "7z"
    assert [entry.path for entry in result.entries] == ["first.jpg", "second.jpg"]
    assert result.entries[-1].size is None


def test_parser_rejects_output_without_archive_header() -> None:
    with pytest.raises(ArchiveBackendError) as captured:
        parse_seven_zip_listing("not technical output", "broken.rar")

    assert captured.value.code is ArchiveErrorCode.INVALID_OUTPUT


def test_parser_enforces_entry_limit_early() -> None:
    output = ["Path = book.7z", "Type = 7z", "----------"]
    for index in range(4):
        output.extend((f"Path = {index}.jpg", "Size = 1", ""))

    with pytest.raises(ArchiveBackendError) as captured:
        parse_seven_zip_listing("\n".join(output), "book.7z", maximum_entries=3)

    assert captured.value.code is ArchiveErrorCode.TOO_MANY_ENTRIES


def test_parser_preserves_warning_messages() -> None:
    result = parse_seven_zip_listing(
        "Path = book.7z\nType = 7z\n",
        "book.7z",
        warning_messages=("warning",),
    )

    assert result.warning_messages == ("warning",)
