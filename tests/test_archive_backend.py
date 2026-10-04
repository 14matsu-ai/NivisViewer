from __future__ import annotations

import pytest

from app.archive_backend import (
    ArchiveEntry,
    ArchiveListing,
    is_supported_archive_candidate,
    normalize_archive_entry_path,
    select_image_entries,
)
from app.image_source import SUPPORTED_EXTENSIONS


def listing(*entries: ArchiveEntry) -> ArchiveListing:
    return ArchiveListing("book.7z", entries, "7z", False, False)


def entry(path: str, *, directory: bool = False) -> ArchiveEntry:
    return ArchiveEntry(path, 10, 5, directory, False, original_path=path)


def test_image_entry_selection_filters_noise_and_naturally_sorts() -> None:
    selected = select_image_entries(
        listing(
            entry("page10.JPG"),
            entry("page2.jpg"),
            entry("page1.png"),
            entry("notes.txt"),
            entry("__MACOSX/page0.jpg"),
            entry("folder/.DS_Store"),
            entry("folder/Thumbs.db"),
            entry("@listfile.jpg"),
            entry("directory", directory=True),
        ),
        SUPPORTED_EXTENSIONS,
    )

    assert [value.path for value in selected] == [
        "page1.png",
        "page2.jpg",
        "page10.JPG",
    ]


def test_image_entry_selection_keeps_unicode_and_subfolders() -> None:
    selected = select_image_entries(
        listing(entry(r"第一話\日本語2.webp"), entry("第一話/日本語10.WEBP")),
        SUPPORTED_EXTENSIONS,
    )

    assert [value.path for value in selected] == [
        "第一話/日本語2.webp",
        "第一話/日本語10.WEBP",
    ]
    assert selected[0].extraction_path == r"第一話\日本語2.webp"


def test_duplicate_normalized_paths_are_kept_once() -> None:
    selected = select_image_entries(
        listing(entry(r"chapter\1.jpg"), entry("CHAPTER/1.JPG")),
        SUPPORTED_EXTENSIONS,
    )

    assert len(selected) == 1


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("book.rar", True),
        ("book.RAR", True),
        ("book.part1.rar", True),
        ("book.part01.rar", True),
        ("book.part2.rar", False),
        ("book.part12.RAR", False),
        ("book.r00", False),
        ("book.r01", False),
        ("book.7z", True),
        ("book.cb7", True),
        ("book.cbr", True),
        ("book.7z.001", False),
    ],
)
def test_multivolume_candidate_policy(name: str, expected: bool) -> None:
    assert is_supported_archive_candidate(name) is expected


def test_entry_path_normalization_is_lexical_only() -> None:
    assert normalize_archive_entry_path(r".\folder\..\page.jpg") == "folder/../page.jpg"
