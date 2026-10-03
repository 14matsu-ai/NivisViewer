"""Prepare a read-only, hash-bound obsolete-file plan from a known release ZIP.

This does not delete files or enable startup cleanup. It makes the proposed
overwrite-update scope reviewable before a cleanup mechanism is selected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile


def _relative_name(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    parts = value.split("/")
    if (not value or "\\" in value or ":" in value or "\x00" in value
            or path.is_absolute() or any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in parts)):
        raise ValueError(f"Unsafe program path: {value}")
    if path.parts[0].casefold() in {"config.json", "data"}:
        raise ValueError(f"Profile data cannot be a cleanup target: {value}")
    return path


def build_plan(old_zip: Path, new_bundle: Path, expected_old_sha256: str) -> dict[str, object]:
    if not new_bundle.is_dir():
        raise ValueError("New bundle directory does not exist")
    old_digest = hashlib.sha256(old_zip.read_bytes()).hexdigest()
    if old_digest != expected_old_sha256:
        raise ValueError("Old release ZIP checksum mismatch")
    new_names = {str(path.relative_to(new_bundle)).replace("\\", "/").casefold()
                 for path in new_bundle.rglob("*") if path.is_file()}
    obsolete = []
    seen = set()
    with zipfile.ZipFile(old_zip) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            # ZipInfo normalizes backslashes on Windows; validate the raw name.
            parts = entry.orig_filename.split("/")
            if not parts or parts[0] != "NivisViewer":
                raise ValueError("Unexpected old release ZIP root")
            relative = str(_relative_name("/".join(parts[1:])))
            if relative.casefold() in seen:
                raise ValueError("Duplicate old release ZIP program path")
            seen.add(relative.casefold())
            if relative.casefold() in new_names:
                continue
            digest = hashlib.sha256()
            with archive.open(entry) as source:
                while payload := source.read(1024 * 1024):
                    digest.update(payload)
            obsolete.append(dict(path=relative, size=entry.file_size, sha256=digest.hexdigest()))
    return dict(schema=1, scope="proposal only; no deletion or startup integration",
                old_release_sha256=old_digest, obsolete_files=sorted(obsolete, key=lambda item: item["path"]))


def inspect_target(bundle: Path, entries: list[dict[str, object]]) -> list[dict[str, object]]:
    """Classify a hypothetical overlaid installation without changing it."""
    results = []
    for entry in entries:
        relative = _relative_name(str(entry["path"]))
        target = bundle.joinpath(*relative.parts)
        status = "absent"
        chain = [bundle]
        for part in relative.parts:
            chain.append(chain[-1] / part)
        if any(path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT) for path in chain):
            status = "blocked_reparse_point"
        elif target.is_file():
            if target.stat().st_size == entry["size"] and hashlib.sha256(target.read_bytes()).hexdigest() == entry["sha256"]:
                status = "known_obsolete_program_file"
            else:
                status = "preserve_modified_or_unknown_file"
        results.append(dict(path=entry["path"], status=status))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-zip", type=Path, required=True)
    parser.add_argument("--new-bundle", type=Path, required=True)
    parser.add_argument("--expected-old-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = build_plan(args.old_zip, args.new_bundle, args.expected_old_sha256)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Proposed obsolete program files: {len(plan['obsolete_files'])}; no files deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
