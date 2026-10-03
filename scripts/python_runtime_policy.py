"""The supported security baseline for the Windows development/build runtime."""
from __future__ import annotations

import argparse
import json
import platform
import struct
import sys
import sysconfig
from importlib import metadata
from pathlib import Path


MINIMUM_VERSION = (3, 13, 16)


def runtime_details() -> dict[str, object]:
    return {
        "executable": sys.executable,
        "version": list(sys.version_info[:3]),
        "releaselevel": sys.version_info.releaselevel,
        "platform": sys.platform,
        "machine": platform.machine(),
        "bits": struct.calcsize("P") * 8,
        "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
    }


def validate_runtime(details: dict[str, object], *, require_venv: bool = False) -> None:
    version = tuple(details["version"])
    if version[:2] != (3, 13) or version < MINIMUM_VERSION or details["releaselevel"] != "final":
        raise ValueError("Python 3.13.16 or a later stable 3.13 security update is required")
    if details["platform"] != "win32" or details["bits"] != 64 or str(details["machine"]).upper() not in {"AMD64", "X86_64"}:
        raise ValueError("Standard Windows x64 Python is required")
    if details["free_threaded"]:
        raise ValueError("Free-threaded Python is not supported by this build")
    if require_venv and details["prefix"] == details["base_prefix"]:
        raise ValueError("An independent virtual environment is required")


def require_supported_runtime(*, require_venv: bool = False) -> dict[str, object]:
    details = runtime_details()
    validate_runtime(details, require_venv=require_venv)
    return details


def validate_release_dependencies(requirements: Path) -> None:
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, expected = line.split("==")
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            raise ValueError(f"Required build dependency is missing: {name}") from None
        if installed != expected:
            raise ValueError(f"Build dependency mismatch: {name}: expected {expected}, found {installed}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-venv", action="store_true")
    parser.add_argument("--release-dependencies", type=Path)
    args = parser.parse_args()
    try:
        details = require_supported_runtime(require_venv=args.require_venv)
        if args.release_dependencies:
            validate_release_dependencies(args.release_dependencies)
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(details, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
