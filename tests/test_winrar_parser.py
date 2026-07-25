from __future__ import annotations

import pytest

from app.archive_backend import ArchiveBackendError, ArchiveErrorCode, select_image_entries
from app.winrar_parser import decode_winrar_output, parse_winrar_bare_listing


def test_bare_list_preserves_unicode_subfolders_and_directories() -> None:
    listing = parse_winrar_bare_listing(
        "第一話\\\r\n第一話\\10.jpg\r\n第一話\\2.jpg\r\n",
        "book.rar",
    )

    assert listing.entries[0].is_directory
    assert listing.entries[1].path == "第一話/10.jpg"
    selected = select_image_entries(listing, {".jpg"})
    assert [entry.path for entry in selected] == [
        "第一話/2.jpg",
        "第一話/10.jpg",
    ]


def test_empty_and_malformed_bare_list_are_safe() -> None:
    assert parse_winrar_bare_listing("", "empty.rar").entries == ()
    listing = parse_winrar_bare_listing(
        "good.jpg\nbad\ufffdname.jpg\nbad\x01name.jpg\n",
        "book.rar",
    )
    assert [entry.path for entry in listing.entries] == ["good.jpg"]


def test_entry_limit_is_enforced() -> None:
    with pytest.raises(ArchiveBackendError) as captured:
        parse_winrar_bare_listing("1.jpg\n2.jpg\n", "book.rar", maximum_entries=1)

    assert captured.value.code is ArchiveErrorCode.TOO_MANY_ENTRIES


def test_output_decoder_prefers_utf8_and_replaces_invalid_bytes() -> None:
    assert decode_winrar_output("日本語.jpg".encode()) == "日本語.jpg"
    assert isinstance(decode_winrar_output(b"\xff\xfe\xff"), str)
