from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path


def package(bundle: Path, destination: Path, *, force: bool = False) -> str:
    if destination.exists() and not force:
        raise FileExistsError(f"Use --force to replace {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    archive.write(path, Path(bundle.name) / path.relative_to(bundle))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{digest}  {destination.name}\n",
        encoding="ascii",
    )
    return digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    print(package(arguments.bundle, arguments.destination, force=arguments.force))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
