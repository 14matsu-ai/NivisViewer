from __future__ import annotations

import pytest

from app.windows_filename import (
    generate_copy_name,
    generate_numbered_name,
    validate_windows_filename,
)


@pytest.mark.parametrize(
    "name",
    ["book.zip", "日本語の本.cbz", "作品 01"],
)
def test_valid_windows_filenames_preserve_input(name: str) -> None:
    result = validate_windows_filename(name)

    assert result.valid
    assert result.normalized_name == name
    assert result.error_code is None


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("", "empty"),
        ('bad:name.zip', "invalid_character"),
        (r"folder\book.zip", "invalid_character"),
        ("trailing ", "trailing_space_or_dot"),
        ("trailing.", "trailing_space_or_dot"),
        ("control\x01name", "control_character"),
        (".", "dot_name"),
        ("..", "dot_name"),
        ("CON", "reserved_name"),
        ("nul.txt", "reserved_name"),
        ("COM9.cbz", "reserved_name"),
        ("LPT1", "reserved_name"),
        ("あ" * 256, "too_long"),
    ],
)
def test_invalid_windows_filenames_report_structured_reason(
    name: str,
    code: str,
) -> None:
    result = validate_windows_filename(name)

    assert not result.valid
    assert result.error_code == code
    assert result.error_message


def test_copy_name_preserves_extension_and_is_case_insensitive() -> None:
    generated = generate_copy_name(
        "book.zip",
        {"BOOK - コピー.ZIP", "book.zip"},
    )

    assert generated == "book - コピー (2).zip"


def test_copy_name_supports_folders_and_has_safe_attempt_limit() -> None:
    assert generate_copy_name(
        "本",
        {"本", "本 - コピー"},
        is_directory=True,
    ) == "本 - コピー (2)"
    assert generate_copy_name(
        "book.zip",
        {"book - コピー.zip"},
        max_attempts=1,
    ) is None


def test_numbered_folder_name_uses_first_available_number() -> None:
    assert generate_numbered_name(
        "新しいフォルダ",
        {"新しいフォルダ", "新しいフォルダ (2)"},
    ) == "新しいフォルダ (3)"
