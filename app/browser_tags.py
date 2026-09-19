"""Filename tag identity and the separate, ordered display registry.

Validation and mixed-selection semantics follow ZipPlaFork ZipPlaInfo and
CatalogForm at 07955f5267e2fb92d6fc6e40fde2507d8fb07b3b (AGPL-3.0-or-later).
"""
from functools import lru_cache
import re

from .zippla_filename_metadata import ZipPlaFilenameMetadata


def valid_tag_name(name: str) -> bool:
    return bool(name and name == name.strip() and not re.search(r'[,;\\/:*?"<>{}|\x00-\x1f]', name))


def normalize_tag_registry(value: object) -> list[dict[str, str]]:
    result, seen = [], set()
    for entry in value if isinstance(value, list) else []:
        if not isinstance(entry, dict):
            continue
        name, color = entry.get('name'), entry.get('color', '#80bfff')
        if not isinstance(name, str) or not valid_tag_name(name) or name.casefold() in seen:
            continue
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            color = '#80bfff'
        result.append({'name': name, 'color': color.lower()})
        seen.add(name.casefold())
    return result


@lru_cache(maxsize=8192)
def filename_tags(path: str) -> tuple[str, ...]:
    return ZipPlaFilenameMetadata.parse(path).tags or ()


def edited_tags(original: tuple[str, ...], changes: dict[str, bool | None]) -> tuple[str, ...]:
    """None preserves membership; unknown and unrelated tags survive."""
    if any(not valid_tag_name(name) for name, state in changes.items() if state is True):
        raise ValueError('Invalid tag name')
    result = [name for name in original if changes.get(name) is not False]
    for name, state in changes.items():
        if state is True and name not in result:
            result.append(name)
    return tuple(result)


def visible_tags(path: str, registry: list[dict[str, str]]) -> list[dict[str, str]]:
    membership = set(filename_tags(path))
    return [entry for entry in registry if entry['name'] in membership]
