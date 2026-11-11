"""Cancellable, per-operation source preparation without mutation authority."""
from __future__ import annotations

from collections.abc import Callable, Iterable
import logging
import os
from pathlib import Path
import stat
from threading import Event
from time import monotonic


_LOG = logging.getLogger("nivisviewer.file_operation")


class SourcePreparationCancelled(Exception):
    pass


def _path_key(absolute_path: str) -> str:
    return os.path.normcase(absolute_path).casefold().rstrip(os.sep)


def _has_selected_ancestor(key: str, directories: set[str]) -> bool:
    parent = os.path.dirname(key)
    while parent != key:
        if parent.rstrip(os.sep) in directories:
            return True
        key = parent
        parent = os.path.dirname(key)
    return False


def prepare_source_paths(
    paths: Iterable[str],
    *,
    normalize: Callable[[str], str],
    phase: str,
    cancelled: Event | None = None,
) -> tuple[str, ...]:
    """Suppress children of selected directories, retaining displayed spellings.

    Kinds and resolved paths belong to this preparation only. Execution must
    still validate the current source and its identity before any deletion.
    """
    cancel = cancelled if cancelled is not None else Event()
    started = monotonic()
    input_count = 0
    unique: dict[str, str] = {}
    prepared: list[str] = []
    directories: set[str] = set()
    resolved_directories: set[str] = set()
    resolved: dict[str, str | None] = {}
    unresolved_directory = False
    state = "failed"
    _LOG.info("file operation source preparation start phase=%s", phase)

    def check_cancelled() -> None:
        if cancel.is_set():
            raise SourcePreparationCancelled

    def resolve(path: str, key: str) -> str | None:
        if key not in resolved:
            check_cancelled()
            try:
                resolved[key] = _path_key(os.path.realpath(path))
            except (OSError, RuntimeError, ValueError):
                resolved[key] = None
            check_cancelled()
        return resolved[key]

    try:
        check_cancelled()
        for path in paths:
            check_cancelled()
            input_count += 1
            absolute = normalize(path)
            unique.setdefault(_path_key(absolute), absolute)
        check_cancelled()
        ordered = sorted(unique.items(), key=lambda entry: (len(Path(entry[1]).parts), entry[0]))
        check_cancelled()
        for key, path in ordered:
            check_cancelled()
            if _has_selected_ancestor(key, directories):
                continue
            if directories:
                if unresolved_directory:
                    continue
                physical = resolve(path, key)
                # Preserve conservative exclusion when ancestry against a
                # selected directory cannot be established safely.
                if physical is None:
                    continue
                if _has_selected_ancestor(physical, resolved_directories):
                    continue
            check_cancelled()
            try:
                is_directory = stat.S_ISDIR(os.stat(path).st_mode)
            except (OSError, ValueError):
                # Missing/inaccessible items remain independent sources. Their
                # normal planning/execution paths report the actual failure.
                is_directory = False
            check_cancelled()
            prepared.append(path)
            if is_directory:
                directories.add(key)
                physical = resolve(path, key)
                if physical is None:
                    unresolved_directory = True
                else:
                    resolved_directories.add(physical)
        check_cancelled()
        state = "completed"
        return tuple(prepared)
    except SourcePreparationCancelled:
        state = "cancelled"
        raise
    finally:
        _LOG.info(
            "file operation source preparation end phase=%s state=%s input=%s "
            "unique=%s selected=%s directories=%s resolved=%s elapsed_ms=%.1f",
            phase, state, input_count, len(unique), len(prepared), len(directories),
            len(resolved), (monotonic() - started) * 1000,
        )
