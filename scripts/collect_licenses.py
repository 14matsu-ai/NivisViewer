from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from importlib import metadata
from pathlib import Path


RUNTIME_DISTRIBUTIONS = (
    "PySide6",
    "PySide6_Addons",
    "PySide6_Essentials",
    "shiboken6",
    "Pillow",
    "natsort",
    "pypdfium2",
)
BUILD_DISTRIBUTIONS = ("PyInstaller",)
LICENSE_NAMES = ("license", "copying", "notice", "authors")


def collect(output: Path, *, strict: bool = False) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "manual_review_required": True,
        "runtime": [],
        "build_tools": [],
        "warnings": [],
    }
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
                source = distribution.locate_file(file)
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
                target = destination / target_name
                shutil.copy2(source, target)
                copied.append(target.name)
            if not copied:
                warning = f"{name} {distribution.version}: no license file found"
                manifest["warnings"].append(warning)
                if strict and group_name == "runtime":
                    raise RuntimeError(warning)
            elif all(name.startswith("LicenseRef-Qt-Commercial") for name in copied):
                manifest["warnings"].append(
                    f"{name} {distribution.version}: only a commercial license reference "
                    "was present; public-distribution terms require manual collection"
                )
            entries.append(
                {
                    "name": name,
                    "version": distribution.version,
                    "files": sorted(copied),
                }
            )
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
