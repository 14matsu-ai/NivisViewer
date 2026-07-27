from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path


_LOG = logging.getLogger("nivisviewer.file_operation")
_IDENTIFIER_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
_CANONICAL_PATTERN = re.compile(
    rf"^\.(?P<final>.+)\.nivisviewer-"
    rf"(?P<operation>{_IDENTIFIER_PATTERN})-"
    rf"(?P<item>{_IDENTIFIER_PATTERN})\.tmp$",
    re.IGNORECASE,
)
_LEGACY_PATTERN = re.compile(
    r"^\.(?P<final>.+)\.nivisviewer-(?P<identifier>[0-9a-f]{32})\.tmp$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ArtifactCleanupResult:
    path: str
    removed: bool
    error_message: str | None = None


@dataclass(frozen=True)
class OperationArtifactDescription:
    path: str
    final_destination: str
    nested: bool
    size: int | None
    modified_time_ns: int | None
    final_destination_exists: bool
    source_known: bool = False


class FileOperationArtifactPolicy:
    """Canonical naming and safe handling for unpublished operation artifacts."""

    @classmethod
    def create_staging_path(
        cls,
        final_destination: str | Path,
        operation_id: str | None = None,
        item_id: str | None = None,
    ) -> str:
        requested = cls._absolute(final_destination)
        final = cls.derive_final_destination(requested) or requested
        operation = cls._identifier(operation_id)
        item = cls._identifier(item_id)
        parent = os.path.dirname(final)
        name = os.path.basename(final)
        if not name:
            raise ValueError("final destination must include a file or folder name")
        candidate = os.path.join(
            parent,
            f".{name}.nivisviewer-{operation}-{item}.tmp",
        )
        if cls.is_internal_operation_artifact(candidate):
            if os.path.lexists(candidate):
                raise FileExistsError(
                    f"staging path already exists: {candidate}"
                )
            return candidate
        raise ValueError("canonical staging path could not be created")

    @classmethod
    def is_internal_operation_artifact(cls, path: str | Path) -> bool:
        name = os.path.basename(os.fspath(path))
        return cls._match_name(name) is not None

    @classmethod
    def derive_final_destination(
        cls,
        staging_path: str | Path,
    ) -> str | None:
        current = cls._absolute(staging_path)
        match = cls._match_name(os.path.basename(current))
        if match is None:
            return None
        parent = os.path.dirname(current)
        final_name = match.group("final")
        derived = os.path.join(parent, final_name)
        # Historical double-artifacts encode the preceding artifact as the
        # "final" portion. Unwrap all valid layers, with a hard safety bound.
        for _ in range(8):
            nested_match = cls._match_name(os.path.basename(derived))
            if nested_match is None:
                return derived
            derived = os.path.join(
                os.path.dirname(derived),
                nested_match.group("final"),
            )
        raise ValueError("artifact nesting exceeds the safe recovery limit")

    @classmethod
    def cleanup_staging_path(
        cls,
        path: str | Path,
    ) -> ArtifactCleanupResult:
        target = cls._absolute(path)
        if not cls.is_internal_operation_artifact(target):
            return ArtifactCleanupResult(
                target,
                False,
                "内部一時ファイルではないため削除しません",
            )
        try:
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.unlink(target)
        except FileNotFoundError:
            return ArtifactCleanupResult(target, True)
        except OSError as exc:
            return ArtifactCleanupResult(target, False, str(exc))
        return ArtifactCleanupResult(target, not os.path.lexists(target))

    @classmethod
    def describe_artifact(
        cls,
        path: str | Path,
    ) -> OperationArtifactDescription | None:
        target = cls._absolute(path)
        final = cls.derive_final_destination(target)
        if final is None:
            return None
        nested = cls._nested_depth(target) > 1
        try:
            path_stat = os.lstat(target)
        except OSError:
            size = None
            modified_time_ns = None
        else:
            size = int(path_stat.st_size)
            modified_time_ns = int(getattr(path_stat, "st_mtime_ns", 0)) or None
        return OperationArtifactDescription(
            path=target,
            final_destination=final,
            nested=nested,
            size=size,
            modified_time_ns=modified_time_ns,
            final_destination_exists=os.path.lexists(final),
        )

    @classmethod
    def record_orphan(cls, path: str | Path) -> None:
        description = cls.describe_artifact(path)
        if description is None or not _LOG.isEnabledFor(logging.DEBUG):
            return
        _LOG.debug(
            "未完了のファイル操作一時データを検出しました "
            "artifact=%s final=%s nested=%s size=%s mtime_ns=%s "
            "final_exists=%s source_known=%s",
            description.path,
            description.final_destination,
            description.nested,
            description.size,
            description.modified_time_ns,
            description.final_destination_exists,
            description.source_known,
        )

    @classmethod
    def find_orphans(cls, directory: str | Path) -> tuple[str, ...]:
        found: list[str] = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if cls.is_internal_operation_artifact(entry.name):
                        found.append(entry.path)
        except OSError:
            return ()
        return tuple(found)

    @classmethod
    def _nested_depth(cls, path: str) -> int:
        name = os.path.basename(path)
        depth = 0
        for _ in range(8):
            match = cls._match_name(name)
            if match is None:
                break
            depth += 1
            name = match.group("final")
        return depth

    @staticmethod
    def _match_name(name: str) -> re.Match[str] | None:
        return _CANONICAL_PATTERN.fullmatch(name) or _LEGACY_PATTERN.fullmatch(name)

    @staticmethod
    def _identifier(value: str | None) -> str:
        candidate = str(value or "").strip()
        if re.fullmatch(_IDENTIFIER_PATTERN, candidate):
            return candidate
        return uuid.uuid4().hex

    @staticmethod
    def _absolute(path: str | Path) -> str:
        return os.path.abspath(os.path.normpath(os.fspath(path)))


class OrphanArtifactScanner:
    """Read-only diagnostics for recoverable unpublished operation data."""

    def scan(self, directory: str | Path) -> tuple[OperationArtifactDescription, ...]:
        descriptions: list[OperationArtifactDescription] = []
        for path in FileOperationArtifactPolicy.find_orphans(directory):
            description = FileOperationArtifactPolicy.describe_artifact(path)
            if description is not None:
                descriptions.append(description)
                FileOperationArtifactPolicy.record_orphan(path)
        return tuple(descriptions)
