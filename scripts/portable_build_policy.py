from __future__ import annotations

from pathlib import Path


def prepare_binaries(binaries, *, allowed_roots, environment_root=None):
    """Reject ambient toolchains and preserve delvewheel's package DLL layout."""
    roots = [Path(root).resolve() for root in allowed_roots]
    entries = list(binaries)
    for destination, source, _kind in entries:
        path = Path(source).resolve()
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError(f"Unapproved binary source: {destination}: {path}")
        if environment_root is not None and any(part.casefold().startswith(".venv") for part in path.parts):
            if not path.is_relative_to(Path(environment_root).resolve()):
                raise ValueError(f"Binary from a different virtual environment: {destination}: {path}")

    package_dlls = set()
    for destination, source, _kind in entries:
        parts = destination.replace("\\", "/").split("/")
        if len(parts) > 1 and any(part.endswith(".libs") for part in parts[:-1]):
            package_dlls.add((Path(source).resolve(), parts[-1].casefold()))

    result = []
    for entry in entries:
        destination, source, _kind = entry
        parts = destination.replace("\\", "/").split("/")
        # Patched package initializers add their .libs directory to the DLL
        # search path. A second identical copy at bundle root is redundant.
        if (
            len(parts) == 1 and destination.casefold().endswith(".dll")
            and (Path(source).resolve(), parts[-1].casefold()) in package_dlls
        ):
            continue
        result.append(entry)
    return result
