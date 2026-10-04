"""ZipPla-compatible filename metadata.

Structural translation of ZipPlaFork ``ZipPlaInfo`` at revision
07955f5267e2fb92d6fc6e40fde2507d8fb07b3b.  The filename remains the sole
rating authority; NivisViewer does not mirror ratings into its SQLite store.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
import re


_COVER = r"\s*c\s*=\s*([0-9]{1,9})(\.[0-9]+)?\s*"
_BINDING = r"\s*b\s*=\s*(r|l|n)?\s*"
_RATING = r"\s*r\s*=\s*([1-5])\s*"
_TAGS = r"\s*t\s*=\s*((?:[^,]+,)*[^,]+,?)\s*"
_DIRECTION = r"\s*d\s*=\s*(l|r)?\s*"
_PARAMETER = rf"(?:{_COVER}|{_BINDING}|{_RATING}|{_TAGS}|{_DIRECTION})"
_INFO_BLOCK_RE = re.compile(
    rf"\s?\{{\s*zpi\s*\$\s*((?:{_PARAMETER};)*{_PARAMETER})?;?\s*\}}",
    re.IGNORECASE,
)
_PARAMETER_RES = (
    ("c", re.compile(rf"^{_COVER}$", re.IGNORECASE)),
    ("b", re.compile(rf"^{_BINDING}$", re.IGNORECASE)),
    ("r", re.compile(rf"^{_RATING}$", re.IGNORECASE)),
    ("t", re.compile(rf"^{_TAGS}$", re.IGNORECASE)),
    ("d", re.compile(rf"^{_DIRECTION}$", re.IGNORECASE)),
)


@dataclass(frozen=True)
class ZipPlaFilenameMetadata:
    """Parsed values from the first valid ``{zpi$...}`` filename block."""

    original_path: Path
    cover: tuple[int, float] | None = None
    binding: str | None = None
    rating: int | None = None
    tags: tuple[str, ...] | None = None
    legacy_direction: str | None = None
    matched: bool = False
    _tags_only: bool = field(default=False, compare=False, repr=False)

    @classmethod
    def parse(cls, path: str | Path) -> ZipPlaFilenameMetadata:
        original = Path(path)
        match = _INFO_BLOCK_RE.search(original.name)
        if match is None:
            return cls(original)

        values: dict[str, object] = {}
        body = match.group(1) or ""
        for raw_parameter in body.split(";") if body else ():
            for mnemonic, pattern in _PARAMETER_RES:
                parsed = pattern.fullmatch(raw_parameter)
                if parsed is None:
                    continue
                if mnemonic == "c":
                    fraction = parsed.group(2) or ""
                    values[mnemonic] = (
                        int(parsed.group(1)),
                        float(f"0{fraction}") if fraction else 0.0,
                    )
                elif mnemonic == "r":
                    values[mnemonic] = int(parsed.group(1))
                elif mnemonic == "t":
                    tags = tuple(
                        token.strip()
                        for token in parsed.group(1).split(",")
                        if token.strip()
                    )
                    values[mnemonic] = tags or None
                else:
                    values[mnemonic] = (parsed.group(1) or "").casefold()
                break
        return cls(
            original,
            cover=values.get("c"),  # type: ignore[arg-type]
            binding=values.get("b"),  # type: ignore[arg-type]
            rating=values.get("r"),  # type: ignore[arg-type]
            tags=values.get("t"),  # type: ignore[arg-type]
            legacy_direction=values.get("d"),  # type: ignore[arg-type]
            matched=True,
        )

    @property
    def display_name(self) -> str:
        """Return the physical name without valid ZipPla metadata blocks."""

        return _INFO_BLOCK_RE.sub("", self.original_path.name)

    def with_rating(self, rating: int | None) -> ZipPlaFilenameMetadata:
        normalized = int(rating) if rating is not None else None
        if normalized is not None and not 1 <= normalized <= 5:
            normalized = None
        return replace(self, rating=normalized, _tags_only=False)

    def with_tag_changes(self, changes: dict[str, bool | None]) -> ZipPlaFilenameMetadata:
        from .browser_tags import edited_tags
        original = self.parse(self.original_path)
        tags_only = (self.cover, self.binding, self.rating, self.legacy_direction) == (
            original.cover, original.binding, original.rating, original.legacy_direction,
        )
        return replace(self, tags=edited_tags(self.tags or (), changes) or None, _tags_only=tags_only)

    def serialized_path(self, *, is_directory: bool = False) -> Path:
        """Serialize known metadata in ZipPlaInfo's canonical parameter order."""

        if self._tags_only:
            match = _INFO_BLOCK_RE.search(self.original_path.name)
            if match is not None:
                # Change only t; preserve other parameter text and precision.
                parts = [part for part in (match.group(1) or '').split(';')
                         if part.strip() and _PARAMETER_RES[3][1].fullmatch(part) is None]
                if self.tags:
                    parts.append('t=' + ','.join(self.tags))
                block = ' {zpi$' + ';'.join(parts) + '}' if parts else ''
                name = self.original_path.name
                return self.original_path.with_name(name[:match.start()] + block + name[match.end():])

        name = self.original_path.name
        if is_directory:
            base_name = _INFO_BLOCK_RE.sub("", name)
            suffix = ""
        else:
            suffix = self.original_path.suffix
            stem = name[: -len(suffix)] if suffix else name
            base_name = _INFO_BLOCK_RE.sub("", stem)
        info = self._info_string()
        serialized_name = (
            f"{base_name} {info}{suffix}" if info else f"{base_name}{suffix}"
        )
        return self.original_path.with_name(serialized_name)

    def _info_string(self) -> str | None:
        parameters: list[str] = []
        if self.cover is not None:
            page, fraction = self.cover
            safe_page = max(0, int(page))
            safe_fraction = min(0.999, max(0.0, float(fraction)))
            fraction_text = f"{safe_fraction:.3f}"[1:]
            value = f"{safe_page}{fraction_text}".rstrip("0").rstrip(".")
            parameters.append(f"c={value}")
        if self.binding is not None:
            parameters.append(f"b={self.binding if self.binding in {'l', 'r', 'n'} else 'n'}")
        if self.rating is not None:
            parameters.append(f"r={self.rating}")
        if self.tags:
            parameters.append(f"t={','.join(self.tags)}")
        if self.legacy_direction is not None:
            direction = self.legacy_direction
            parameters.append(f"d={direction if direction in {'l', 'r'} else 'l'}")
        return f"{{zpi${';'.join(parameters)}}}" if parameters else None


def zippla_rating(path: str | Path) -> int | None:
    return ZipPlaFilenameMetadata.parse(path).rating


def zippla_display_name(path: str | Path) -> str:
    return ZipPlaFilenameMetadata.parse(path).display_name


__all__ = [
    "ZipPlaFilenameMetadata",
    "zippla_display_name",
    "zippla_rating",
]
