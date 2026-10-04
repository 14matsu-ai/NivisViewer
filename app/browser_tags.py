"""Filename tags and the separate logical-tag display registry.

Physical tags remain ZipPla-compatible filename tokens. The registry separates
the UI display name from one or more physical tokens so a display-name rename
never needs to touch files.

Filename-tag validation and the existing mixed-selection semantics continue to
follow ZipPlaFork ZipPlaInfo/CatalogForm at revision
07955f5267e2fb92d6fc6e40fde2507d8fb07b3b (AGPL-3.0-or-later).
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from hashlib import sha1
import re
from uuid import uuid4

from .zippla_filename_metadata import ZipPlaFilenameMetadata


def valid_tag_name(name: str) -> bool:
    """Compatibility alias: validate a physical filename tag token."""
    return valid_tag_token(name)


def valid_tag_token(name: str) -> bool:
    return bool(
        name
        and name == name.strip()
        and not re.search(r'[,;\\/:*?"<>{}|\x00-\x1f]', name)
    )


def valid_tag_display_name(name: str) -> bool:
    return bool(
        isinstance(name, str)
        and name
        and name == name.strip()
        and len(name) <= 128
        and not re.search(r'[\x00-\x1f]', name)
    )


def _legacy_tag_id(name: str) -> str:
    digest = sha1(name.casefold().encode("utf-8")).hexdigest()[:16]
    return f"legacy-{digest}"


def new_tag_entry(
    token: str,
    *,
    display_name: str | None = None,
    color: str = "#80bfff",
) -> dict[str, object]:
    if not valid_tag_token(token):
        raise ValueError("Invalid tag token")
    label = display_name if display_name is not None else token
    if not valid_tag_display_name(label):
        raise ValueError("Invalid tag display name")
    return {
        "id": f"tag-{uuid4().hex[:16]}",
        "name": label,
        "display_name": label,
        "write_token": token,
        "tokens": [token],
        "color": color.lower(),
    }


def normalize_tag_registry(value: object) -> list[dict[str, object]]:
    """Normalize both the legacy and logical-tag registry schemas."""
    result: list[dict[str, object]] = []
    seen_display: set[str] = set()
    seen_tokens: set[str] = set()
    seen_ids: set[str] = set()

    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue

        legacy_name = raw.get("name")
        display = raw.get("display_name", legacy_name)
        if not isinstance(display, str) or not valid_tag_display_name(display):
            continue
        display_key = display.casefold()
        if display_key in seen_display:
            continue

        color = raw.get("color", "#80bfff")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = "#80bfff"

        raw_tokens = raw.get("tokens")
        candidates: list[str] = []
        if isinstance(raw_tokens, list):
            candidates.extend(
                token for token in raw_tokens if isinstance(token, str)
            )
        write = raw.get("write_token")
        if isinstance(write, str) and write not in candidates:
            candidates.insert(0, write)
        if not candidates and isinstance(legacy_name, str):
            candidates.append(legacy_name)

        tokens: list[str] = []
        local_seen: set[str] = set()
        for token in candidates:
            if (
                valid_tag_token(token)
                and token not in local_seen
                and token not in seen_tokens
            ):
                tokens.append(token)
                local_seen.add(token)
        if not tokens:
            continue

        if not isinstance(write, str) or write not in local_seen:
            write = tokens[0]

        raw_id = raw.get("id")
        tag_id = (
            raw_id.strip()
            if isinstance(raw_id, str) and raw_id.strip()
            else _legacy_tag_id(
                legacy_name
                if isinstance(legacy_name, str) and legacy_name
                else write
            )
        )
        if tag_id in seen_ids:
            tag_id = f"tag-{uuid4().hex[:16]}"

        result.append(
            {
                "id": tag_id,
                "name": display,
                "display_name": display,
                "write_token": write,
                "tokens": tokens,
                "color": color.lower(),
            }
        )
        seen_ids.add(tag_id)
        seen_display.add(display_key)
        seen_tokens.update(tokens)

    return result


def registry_by_token(registry: object) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for entry in normalize_tag_registry(registry):
        for token in entry["tokens"]:
            result[str(token)] = entry
    return result


def registry_by_id(registry: object) -> dict[str, dict[str, object]]:
    return {
        str(entry["id"]): entry
        for entry in normalize_tag_registry(registry)
    }


def canonical_tag_key(token: str, registry: object) -> str:
    entry = registry_by_token(registry).get(token)
    return str(entry["write_token"]) if entry is not None else token


def tag_token_groups(
    registry: object,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (
            str(entry["write_token"]),
            tuple(str(token) for token in entry["tokens"]),
        )
        for entry in normalize_tag_registry(registry)
    )


@lru_cache(maxsize=8192)
def filename_tags(path: str) -> tuple[str, ...]:
    return ZipPlaFilenameMetadata.parse(path).tags or ()


def edited_tags(
    original: tuple[str, ...],
    changes: dict[str, bool | None],
) -> tuple[str, ...]:
    """None preserves membership; unknown and unrelated tags survive."""
    if any(
        not valid_tag_token(name)
        for name, state in changes.items()
        if state is True
    ):
        raise ValueError("Invalid tag name")
    result = [name for name in original if changes.get(name) is not False]
    for name, state in changes.items():
        if state is True and name not in result:
            result.append(name)
    return tuple(result)


def expand_logical_tag_changes(
    original: tuple[str, ...],
    changes: dict[str, bool | None],
    registry: object,
) -> dict[str, bool | None]:
    """Convert logical/canonical changes into physical filename-token changes."""
    normalized = normalize_tag_registry(registry)
    by_key = {
        str(entry["write_token"]): entry
        for entry in normalized
    }
    by_id = {
        str(entry["id"]): entry
        for entry in normalized
    }
    membership = set(original)
    expanded: dict[str, bool | None] = {}

    for key, state in changes.items():
        entry = by_key.get(key) or by_id.get(key)
        if entry is None:
            expanded[key] = state
            continue
        tokens = tuple(str(token) for token in entry["tokens"])
        if state is True:
            if not any(token in membership for token in tokens):
                expanded[str(entry["write_token"])] = True
        elif state is False:
            for token in tokens:
                if token in membership:
                    expanded[token] = False
    return expanded


def visible_tags(
    path: str,
    registry: object,
) -> list[dict[str, str]]:
    """Return the historical display shape while resolving logical tokens."""
    membership = set(filename_tags(path))
    return [
        {
            "name": str(entry["display_name"]),
            "color": str(entry["color"]),
        }
        for entry in normalize_tag_registry(registry)
        if any(str(token) in membership for token in entry["tokens"])
    ]


def unmanaged_tag_counts(paths: object, registry: object) -> dict[str, int]:
    registered = set(registry_by_token(registry))
    counts: Counter[str] = Counter()
    for path in paths:
        for token in filename_tags(str(path)):
            if token not in registered:
                counts[token] += 1
    return dict(
        sorted(
            counts.items(),
            key=lambda item: (item[0].casefold(), item[0]),
        )
    )
