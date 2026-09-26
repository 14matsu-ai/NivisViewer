from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from importlib import metadata
from pathlib import Path


RUNTIME_DISTRIBUTIONS = (
    "PySide6",
    "PySide6_Addons",
    "PySide6_Essentials",
    "shiboken6",
    "Pillow",
    "pillow-jxl-plugin",
    "psd-tools",
    "attrs",
    "numpy",
    "typing-extensions",
    "gimpformats",
    "aenum",
    "blendmodes",
    "brackettree",
    "loguru",
    "colorama",
    "win32-setctime",
    "packaging",
    "natsort",
    "pypdfium2",
)
BUILD_DISTRIBUTIONS = ("PyInstaller",)
LICENSE_NAMES = ("license", "copying", "notice", "authors")
ROOT = Path(__file__).resolve().parents[1]
QT_DISTRIBUTIONS = frozenset(RUNTIME_DISTRIBUTIONS[:4])
UPSTREAM_LICENSE_SUPPLEMENTS = {"brackettree": "0.2.5", "loguru": "0.7.3"}


def _copy_notice(source: Path, destination: Path) -> str:
    """Keep existing different notices instead of overwriting a basename clash."""
    payload = source.read_bytes()
    if destination.exists() and destination.read_bytes() != payload:
        digest = hashlib.sha256(payload).hexdigest()
        destination = destination.with_name(f"{destination.stem}__{digest}{destination.suffix}")
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return destination.name


def _qt_supplement(output: Path, version: str) -> dict[str, object] | None:
    # Curated verbatim texts, never a build-time network download. Only apply
    # to the exact audited version, not to arbitrary future Qt distributions.
    folder = ROOT / "licenses" / "Qt" / version
    index_path = folder / "SOURCES.json"
    if not index_path.is_file():
        return None
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index["version"] != version:
        raise RuntimeError("Qt license supplement version mismatch")
    for entry in index["files"]:
        name = entry["file"]
        if Path(name).name != name or "\\" in name or "/" in name:
            raise RuntimeError("Invalid Qt license supplement filename")
        source = folder / name
        if hashlib.sha256(source.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError(f"Qt license supplement checksum mismatch: {name}")
        target = output / "Qt" / version / name
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise RuntimeError(f"Conflicting Qt license supplement: {name}")
        _copy_notice(source, output / "Qt" / version / name)
    target_index = output / "Qt" / version / index_path.name
    if target_index.exists() and target_index.read_bytes() != index_path.read_bytes():
        raise RuntimeError("Conflicting Qt license supplement index")
    _copy_notice(index_path, target_index)
    return {
        "path": f"Qt/{version}/SOURCES.json",
        "scope": "license texts and limited attribution, not complete source/module compliance",
    }


def collect(output: Path, *, strict: bool = False) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "manual_review_required": True,
        "scope": "installed distributions, not a complete frozen-binary SBOM or release approval",
        "runtime": [],
        "build_tools": [],
        "warnings": [],
    }
    pins = {}
    pin_file = ROOT / "requirements-release.txt"
    if pin_file.is_file():
        pins = dict(line.strip().split("==", 1) for line in pin_file.read_text().splitlines()
                    if "==" in line and not line.lstrip().startswith("#"))
    for group_name, distributions in (
        ("runtime", RUNTIME_DISTRIBUTIONS),
        ("build_tools", BUILD_DISTRIBUTIONS),
    ):
        entries = manifest[group_name]
        assert isinstance(entries, list)
        for name in distributions:
            try:
                distribution = metadata.distribution(name)
            except metadata.PackageNotFoundError:
                warning = f"{name}: distribution not installed"
                manifest["warnings"].append(warning)
                if strict and group_name == "runtime":
                    raise RuntimeError(warning)
                continue
            destination = output / name
            destination.mkdir(exist_ok=True)
            copied: list[str] = []
            digests: set[str] = set()
            declared = set(distribution.metadata.get_all("License-File") or ())
            for file in distribution.files or ():
                base = Path(str(file)).name.casefold()
                file_text = str(file).replace("\\", "/")
                is_declared = any(
                    file_text.endswith(item.replace("\\", "/"))
                    for item in declared
                )
                is_license_tree = "/licenses/" in f"/{file_text.casefold()}"
                if not (
                    is_declared
                    or is_license_tree
                    or any(base.startswith(prefix) for prefix in LICENSE_NAMES)
                ):
                    continue
                source = Path(distribution.locate_file(file))
                if not source.is_file():
                    continue
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                if digest in digests:
                    continue
                digests.add(digest)
                parts = Path(file_text).parts
                lower_parts = [part.casefold() for part in parts]
                if "licenses" in lower_parts:
                    start = lower_parts.index("licenses") + 1
                    target_name = "__".join(parts[start:])
                else:
                    target_name = parts[-1]
                copied.append(_copy_notice(source, destination / target_name))
            supplement = _qt_supplement(output, distribution.version) if name in QT_DISTRIBUTIONS else None
            if not copied and UPSTREAM_LICENSE_SUPPLEMENTS.get(name) == distribution.version:
                folder = ROOT / "licenses" / name
                index = json.loads((folder / "SOURCES.json").read_text(encoding="utf-8"))
                source = folder / "LICENSE"
                if (index["version"] != distribution.version
                        or hashlib.sha256(source.read_bytes()).hexdigest() != index["sha256"]):
                    raise RuntimeError(f"{name}: upstream license supplement mismatch")
                copied.append(_copy_notice(source, destination / "LICENSE"))
                _copy_notice(folder / "SOURCES.json", destination / "SOURCES.json")
                supplement = {"path": f"{name}/SOURCES.json", "scope": "upstream license text"}
            if not copied:
                warning = f"{name} {distribution.version}: no license file found"
                manifest["warnings"].append(warning)
                if strict and group_name == "runtime":
                    raise RuntimeError(warning)
            elif all(file.startswith("LicenseRef-Qt-Commercial") for file in copied):
                manifest["warnings"].append(
                    f"{name} {distribution.version}: wheel has only a commercial reference; "
                    + ("official open-source texts supplemented, but module/third-party/source review remains"
                       if supplement else "open-source texts missing; inspect metadata and official sources, not a commercial-only conclusion")
                )
            if name in pins and distribution.version != pins[name]:
                manifest["warnings"].append(
                    f"{name}: installed {distribution.version} differs from release pin {pins[name]}"
                )
            entries.append(
                {
                    "name": name,
                    "version": distribution.version,
                    "files": sorted(copied),
                    "file_sha256": {file: hashlib.sha256((destination / file).read_bytes()).hexdigest()
                                    for file in sorted(copied)},
                    "declared_license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License"),
                    "declared_license_files": sorted(declared),
                    "supplement": supplement,
                }
            )
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        name = _copy_notice(python_license, output / "Python" / "LICENSE.txt")
        manifest["interpreter"] = {
            "name": "CPython", "version": ".".join(map(str, sys.version_info[:3])),
            "file": f"Python/{name}",
            "sha256": hashlib.sha256(python_license.read_bytes()).hexdigest(),
            "source": "installed interpreter LICENSE.txt; native dependency inventory still requires review",
        }
    else:
        manifest["warnings"].append("Python: installed interpreter LICENSE.txt not found; manual collection required")
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("licenses"))
    parser.add_argument("--strict", action="store_true")
    arguments = parser.parse_args()
    collect(arguments.output, strict=arguments.strict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
