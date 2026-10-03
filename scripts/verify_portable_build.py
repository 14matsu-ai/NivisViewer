from __future__ import annotations

import argparse
import json
import hashlib
import os
import re
import sys
import importlib.util
import zipfile
from pathlib import Path


REQUIRED = (
    "NivisViewer.exe",
    "portable.flag",
    "LICENSE",
    "PROJECT_LICENSE.md",
    "THIRD_PARTY_NOTICES.md",
    "README.md",
)
FORBIDDEN_NAMES = {
    "config.json",
    "metadata.sqlite3",
    "logs",
    "tests",
    ".git",
}
FORBIDDEN_EXECUTABLES = {
    "winrar.exe",
    "unrar.exe",
    "rar.exe",
    "7z.exe",
    "7zz.exe",
    "ffmpeg.exe",
    "ffprobe.exe",
}


def audit_runtime_files(bundle: Path, expected_python: str) -> list[str]:
    errors = []
    if tuple(map(int, expected_python.split(".")[:2])) != sys.version_info[:2]:
        errors.append("bytecode audit must use the target Python interpreter series")
    expected_abi = "cp" + "".join(expected_python.split(".")[:2])
    paths = [path for path in bundle.rglob("*") if path.is_file()]
    readmes = [path for path in paths if path.name.casefold() == "readme.md"]
    if readmes != [bundle / "README.md"]:
        errors.append("bundle must contain exactly one README.md at its root")
    for path in paths:
        for abi in re.findall(r"(?:^|[._-])(cp\d{2,3}t?)(?=[._-]|$)", path.name.casefold()):
            if abi != expected_abi:
                errors.append(f"unexpected Python ABI tag: {path.relative_to(bundle)}")
        if path.suffix.lower() == ".pyc" and path.read_bytes()[:4] != importlib.util.MAGIC_NUMBER:
            errors.append(f"unexpected bytecode magic: {path.relative_to(bundle)}")
        if path.name == "base_library.zip":
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if name.endswith(".pyc"):
                        with archive.open(name) as entry:
                            if entry.read(4) != importlib.util.MAGIC_NUMBER:
                                errors.append(f"unexpected bytecode magic: base_library.zip/{name}")
    return errors


def audit_native(bundle: Path, expected_python: str, *, system_dir: Path | None = None) -> dict[str, object]:
    """Static PE import presence/version check; never loads or runs a binary."""
    import pefile

    binaries = sorted(path for path in bundle.rglob("*") if path.is_file() and path.suffix.lower() in {".exe", ".dll", ".pyd"})
    available = {path.name.casefold() for path in binaries}
    system_dir = system_dir or Path(os.environ["SystemRoot"]) / "System32"
    system_names = {path.name.casefold() for path in system_dir.glob("*.dll")}
    expected_name = "python" + "".join(expected_python.split(".")[:2]) + ".dll"
    errors = audit_runtime_files(bundle, expected_python)
    python_version = None
    missing = []
    for path in binaries:
        name = path.name.casefold()
        if re.fullmatch(r"python\d+\.dll", name) and name not in {expected_name, "python3.dll"}:
            errors.append(f"unexpected interpreter DLL: {path.relative_to(bundle)}")
        pe = None
        try:
            pe = pefile.PE(str(path))
            if pe.FILE_HEADER.Machine != 0x8664:
                errors.append(f"non-x64 binary: {path.relative_to(bundle)}")
            if name == expected_name:
                for group in getattr(pe, "FileInfo", ()):
                    for info in group:
                        for table in getattr(info, "StringTable", ()):
                            value = table.entries.get(b"ProductVersion")
                            if value:
                                python_version = value.decode("ascii").strip()
            for attribute in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
                for entry in getattr(pe, attribute, ()):
                    dependency = entry.dll.decode("ascii").casefold()
                    if dependency.startswith(("api-ms-", "ext-ms-")):
                        continue  # Windows API-set contracts, not loose DLLs.
                    if dependency not in available and dependency not in system_names:
                        missing.append({"binary": str(path.relative_to(bundle)), "dependency": dependency})
        except (OSError, pefile.PEFormatError) as exc:
            errors.append(f"invalid native binary {path.relative_to(bundle)}: {exc}")
        finally:
            if pe is not None:
                pe.close()
    if expected_name not in available:
        errors.append(f"interpreter DLL not found: {expected_name}")
    if python_version != expected_python:
        errors.append(f"interpreter ProductVersion mismatch: expected {expected_python}, found {python_version}")
    errors.extend(f"missing native import: {entry['binary']}: {entry['dependency']}" for entry in missing)
    return {
        "scope": "static PE version/architecture/import-presence check; not a native loader or executable smoke test",
        "expected_python": expected_python, "python_dll": expected_name,
        "python_product_version": python_version,
        "native_binary_count": len(binaries), "missing_imports": missing, "errors": errors,
    }


def verify(bundle: Path, smoke_result: Path | None = None) -> list[str]:
    errors = [
        f"missing: {name}" for name in REQUIRED if not (bundle / name).exists()
    ]
    if not ((bundle / "licenses").is_dir() or (bundle / "_internal" / "licenses").is_dir()):
        errors.append("missing: licenses")
    paths = tuple(bundle.rglob("*")) if bundle.exists() else ()
    package_dlls = {
        path.name.casefold(): path for path in paths
        if path.is_file() and path.suffix.casefold() == ".dll"
        and path.parent.name.endswith(".libs")
    }
    for path in paths:
        duplicate = package_dlls.get(path.name.casefold())
        if duplicate is not None and path.parent == bundle / "_internal":
            if hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(duplicate.read_bytes()).digest():
                errors.append(f"duplicate package DLL at bundle root: {path.name}")
    lower_names = {path.name.casefold() for path in paths}
    for name in FORBIDDEN_NAMES:
        if name.casefold() in lower_names:
            errors.append(f"unexpected user/development data: {name}")
    for name in FORBIDDEN_EXECUTABLES:
        if name in lower_names:
            errors.append(f"external executable must not be bundled: {name}")
    if not any(path.name.casefold() == "qwindows.dll" for path in paths):
        errors.append("Qt Windows platform plugin not found")
    if not any(
        path.name.casefold() in {"qjpeg.dll", "qwebp.dll"}
        and "imageformats" in str(path.parent).casefold()
        for path in paths
    ):
        errors.append("Qt imageformats plugins not found")
    if not any(
        "pdfium" in path.name.casefold() and path.suffix.casefold() == ".dll"
        for path in paths
    ):
        errors.append("PDFium native component not found")
    required_branding = {
        "nivisviewer.ico",
        "nivisviewer_icon.png",
        "nivisviewer_logo.png",
    }
    missing_branding = required_branding - lower_names
    for name in sorted(missing_branding):
        errors.append(f"branding asset not found: {name}")
    if smoke_result is not None:
        try:
            result = json.loads(smoke_result.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid smoke result: {exc}")
        else:
            if result.get("success") is not True:
                errors.append("frozen smoke did not succeed")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--smoke-result", type=Path)
    parser.add_argument("--expected-python")
    parser.add_argument("--native-report", type=Path)
    arguments = parser.parse_args()
    errors = verify(arguments.bundle, arguments.smoke_result)
    if arguments.native_report and not arguments.expected_python:
        parser.error("--native-report requires --expected-python")
    if arguments.expected_python:
        report = audit_native(arguments.bundle, arguments.expected_python)
        errors.extend(report["errors"])
        if arguments.native_report:
            arguments.native_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
