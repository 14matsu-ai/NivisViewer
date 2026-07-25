from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MAX_WINDOWS_FILENAME_UNITS = 255
_INVALID_CHARACTERS = frozenset('<>:"/\\|?*')
_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


@dataclass(frozen=True)
class FilenameValidationResult:
    valid: bool
    normalized_name: str
    error_code: str | None = None
    error_message: str | None = None


def validate_windows_filename(name: str) -> FilenameValidationResult:
    candidate = str(name)
    if not candidate:
        return _invalid(candidate, "empty", "名前を入力してください")
    if candidate in {".", ".."}:
        return _invalid(candidate, "dot_name", "「.」と「..」は使用できません")
    if candidate[-1:] in {" ", "."}:
        return _invalid(
            candidate,
            "trailing_space_or_dot",
            "名前の末尾に空白またはピリオドは使用できません",
        )
    if any(character in _INVALID_CHARACTERS for character in candidate):
        return _invalid(
            candidate,
            "invalid_character",
            '名前に < > : " / \\ | ? * は使用できません',
        )
    if any(ord(character) < 32 for character in candidate):
        return _invalid(
            candidate,
            "control_character",
            "名前に制御文字は使用できません",
        )
    stem = candidate.split(".", 1)[0].upper()
    if stem in _RESERVED_STEMS:
        return _invalid(
            candidate,
            "reserved_name",
            f"Windowsの予約名「{stem}」は使用できません",
        )
    if _utf16_units(candidate) > MAX_WINDOWS_FILENAME_UNITS:
        return _invalid(
            candidate,
            "too_long",
            "名前が長すぎます",
        )
    return FilenameValidationResult(True, candidate)


def generate_copy_name(
    original_name: str,
    existing_names: Iterable[str],
    *,
    is_directory: bool = False,
    max_attempts: int = 1000,
) -> str | None:
    existing = {str(name).casefold() for name in existing_names}
    source = Path(original_name)
    suffix = "" if is_directory else source.suffix
    stem = original_name if is_directory or not suffix else original_name[: -len(suffix)]
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        marker = " - コピー" if attempt == 1 else f" - コピー ({attempt})"
        candidate = f"{stem}{marker}{suffix}"
        validation = validate_windows_filename(candidate)
        if validation.valid and candidate.casefold() not in existing:
            return candidate
    return None


def generate_numbered_name(
    base_name: str,
    existing_names: Iterable[str],
    *,
    max_attempts: int = 1000,
) -> str | None:
    existing = {str(name).casefold() for name in existing_names}
    validation = validate_windows_filename(base_name)
    if validation.valid and base_name.casefold() not in existing:
        return base_name
    for number in range(2, max(2, int(max_attempts)) + 2):
        candidate = f"{base_name} ({number})"
        validation = validate_windows_filename(candidate)
        if validation.valid and candidate.casefold() not in existing:
            return candidate
    return None


def _invalid(name: str, code: str, message: str) -> FilenameValidationResult:
    return FilenameValidationResult(False, name, code, message)


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2
