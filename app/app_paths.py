from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class AppPaths:
    frozen: bool
    portable: bool
    writable: bool
    executable_path: Path
    executable_dir: Path
    resource_dir: Path
    profile_dir: Path
    data_dir: Path
    config_path: Path
    metadata_path: Path
    thumbnail_cache_dir: Path
    logs_dir: Path
    licenses_dir: Path
    write_error: str | None = None


def resolve_app_paths(
    *,
    profile_override: str | None = None,
    environ: Mapping[str, str] | None = None,
    frozen: bool | None = None,
    executable_path: str | Path | None = None,
    resource_dir: str | Path | None = None,
    source_root: str | Path | None = None,
    check_writable: bool = True,
) -> AppPaths:
    env = os.environ if environ is None else environ
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    root = (
        Path(source_root)
        if source_root is not None
        else Path(__file__).resolve().parents[1]
    )
    executable = (
        Path(executable_path)
        if executable_path is not None
        else Path(sys.executable) if is_frozen else root / "main.py"
    )
    executable = _lexical_absolute(executable)
    executable_dir = executable.parent
    if resource_dir is not None:
        resources = _lexical_absolute(Path(resource_dir))
    elif is_frozen:
        resources = _lexical_absolute(
            Path(getattr(sys, "_MEIPASS", executable_dir / "_internal"))
        )
    else:
        resources = _lexical_absolute(root)

    explicit_profile = profile_override or env.get("NIVISVIEWER_PROFILE_DIR")
    portable = (executable_dir / "portable.flag").is_file()
    if explicit_profile:
        profile = _lexical_absolute(Path(_strip_outer_quotes(explicit_profile)))
    elif portable:
        profile = executable_dir
    else:
        local_app_data = env.get("LOCALAPPDATA")
        profile = (
            _lexical_absolute(Path(local_app_data) / "NivisViewer")
            if local_app_data
            else executable_dir
        )

    writable, error = (
        _probe_writable(profile) if check_writable else (True, None)
    )
    data = profile / "data"
    return AppPaths(
        frozen=is_frozen,
        portable=portable,
        writable=writable,
        executable_path=executable,
        executable_dir=executable_dir,
        resource_dir=resources,
        profile_dir=profile,
        data_dir=data,
        config_path=profile / "config.json",
        metadata_path=data / "metadata.sqlite3",
        thumbnail_cache_dir=data / "thumbnail_cache",
        logs_dir=data / "logs",
        licenses_dir=(
            executable_dir / "licenses"
            if (executable_dir / "licenses").exists()
            else resources / "licenses"
        ),
        write_error=error,
    )


def _probe_writable(profile_dir: Path) -> tuple[bool, str | None]:
    first = profile_dir / f".nivisviewer-write-{uuid.uuid4().hex}.tmp"
    second = first.with_suffix(".replace")
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
        first.write_bytes(b"NivisViewer")
        first.replace(second)
        second.unlink()
        return True, None
    except OSError as exc:
        for candidate in (first, second):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
        return False, str(exc)


def _strip_outer_quotes(value: str) -> str:
    normalized = str(value).strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {'"', "'"}:
        return normalized[1:-1].strip()
    return normalized


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))
