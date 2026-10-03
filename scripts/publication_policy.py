"""Publication exclusions shared by repository and source-package checks."""

from pathlib import Path
import subprocess

PUBLIC_DOCS = {
    "docs/PORTABLE_BUILD.md",
    "docs/ZIPPLAFORK_COMPARISON.md",
    "docs/RELEASE_LICENSE_AUDIT.md",
}
PRIVATE_FILES = {"AGENTS.md", "_folder_worker_probe.py", "_wheel_trace_probe.py"}
PRIVATE_PREFIXES = ("portable-backups/", "assets/screenshots/", "assets/branding/README.md")
PRIVATE_SCRIPT_PREFIXES = (
    "review_", "diagnose_", "measure_", "generate_stress_",
    "thumbnail_quality_", "generate_branding_",
)


def publishable(filename: str) -> bool:
    name = filename.replace("\\", "/")
    if name in PRIVATE_FILES or name.startswith(PRIVATE_PREFIXES):
        return False
    if name.casefold().endswith((".zip", ".pyc", ".pyo", ".log", ".tmp")) or "__pycache__" in name:
        return False
    if name.startswith("docs/") and name not in PUBLIC_DOCS:
        return False
    if name.startswith("scripts/"):
        basename = name.rsplit("/", 1)[-1]
        if basename.startswith(PRIVATE_SCRIPT_PREFIXES):
            return False
        if basename.startswith("benchmark_") and name != "scripts/benchmark_viewer_navigation.py":
            return False
    return True


def find_publication_violations(root: Path) -> list[str]:
    if (root / ".git").exists():
        result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
        names = [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]
    else:
        names = [file.relative_to(root).as_posix() for file in root.rglob("*") if file.is_file()]
    return sorted(name for name in names if not publishable(name))
