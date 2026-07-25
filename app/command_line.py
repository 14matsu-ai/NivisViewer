from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .version import __version__


@dataclass(frozen=True)
class CommandLineOptions:
    paths: tuple[str, ...]
    new_window: bool = False
    reuse: bool = False
    browser_only: bool = False
    no_restore: bool = False
    profile_dir: str | None = None
    no_single_instance: bool = False
    smoke_test_output: str | None = None
    show_version: bool = False


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="NivisViewer")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--new-window", action="store_true")
    mode.add_argument("--reuse", action="store_true")
    parser.add_argument("--browser-only", action="store_true")
    parser.add_argument("--no-restore", action="store_true")
    parser.add_argument("--profile-dir")
    parser.add_argument("--version", action="store_true", dest="show_version")
    parser.add_argument("--no-single-instance", action="store_true")
    parser.add_argument("--smoke-test-output")
    parser.add_argument("paths", nargs="*")
    return parser


def parse_command_line(
    arguments: Sequence[str],
    *,
    launch_directory: str | Path | None = None,
) -> CommandLineOptions:
    namespace = create_parser().parse_args(list(arguments))
    base = Path.cwd() if launch_directory is None else Path(launch_directory)
    paths = _normalize_unique_paths(namespace.paths, base)
    profile = (
        normalize_path_argument(namespace.profile_dir, base)
        if namespace.profile_dir
        else None
    )
    smoke_output = (
        normalize_path_argument(namespace.smoke_test_output, base)
        if namespace.smoke_test_output
        else None
    )
    return CommandLineOptions(
        paths=paths,
        new_window=namespace.new_window,
        reuse=namespace.reuse,
        browser_only=namespace.browser_only,
        no_restore=namespace.no_restore,
        profile_dir=profile,
        no_single_instance=namespace.no_single_instance,
        smoke_test_output=smoke_output,
        show_version=namespace.show_version,
    )


def normalize_path_argument(value: str, launch_directory: Path) -> str:
    cleaned = str(value).strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()
    path = Path(cleaned)
    if not path.is_absolute():
        path = launch_directory / path
    return os.path.abspath(os.path.normpath(os.fspath(path)))


def _normalize_unique_paths(values: Sequence[str], base: Path) -> tuple[str, ...]:
    unique: dict[str, str] = {}
    for value in values:
        normalized = normalize_path_argument(value, base)
        unique.setdefault(os.path.normcase(normalized).casefold(), normalized)
    return tuple(unique.values())


def version_text() -> str:
    return f"NivisViewer {__version__}"
