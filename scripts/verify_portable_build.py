from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED = (
    "NivisViewer.exe",
    "portable.flag",
    "LICENSE",
    "PROJECT_LICENSE.md",
    "THIRD_PARTY_NOTICES.md",
    "README.md",
    "README.en.md",
    "README.zh-CN.md",
    "README.zh-TW.md",
    "licenses",
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


def verify(bundle: Path, smoke_result: Path | None = None) -> list[str]:
    errors = [
        f"missing: {name}" for name in REQUIRED if not (bundle / name).exists()
    ]
    paths = tuple(bundle.rglob("*")) if bundle.exists() else ()
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
    arguments = parser.parse_args()
    errors = verify(arguments.bundle, arguments.smoke_result)
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
