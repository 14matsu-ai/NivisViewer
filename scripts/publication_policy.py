"""Publication exclusions shared by repository and source-package checks."""

from pathlib import Path
import subprocess

PUBLIC_DOCS = {
    "docs/PORTABLE_BUILD.md",
    "docs/ZIPPLAFORK_COMPARISON.md",
    "docs/RELEASE_LICENSE_AUDIT.md",
}
PUBLIC_SCREENSHOTS = {"assets/screenshots/browser-sample.png"}
PRIVATE_FILES = {"AGENTS.md", "config.json", "_folder_worker_probe.py", "_wheel_trace_probe.py"}
PRIVATE_PREFIXES = (
    "portable-backups/", "assets/screenshots/", "assets/branding/README.md",
    "data/", "cache/", "logs/", "build/", "dist/", "out/",
    ".venv", "venv/", ".codex/", ".agents/", ".vscode/", ".idea/",
)
PUBLIC_ROOT_DOCUMENTS = {
    "README.md", "README.en.md", "README.zh-CN.md", "README.zh-TW.md",
    "PROJECT_LICENSE.md", "THIRD_PARTY_NOTICES.md", "PUBLICATION_POLICY.md",
    "requirements.txt", "requirements-build.txt", "requirements-dev.txt", "requirements-release.txt",
}
PRIVATE_SCRIPT_PREFIXES = (
    "review_", "diagnose_", "measure_", "generate_stress_",
    "thumbnail_quality_",
)


def publishable(filename: str) -> bool:
    name = filename.replace("\\", "/")
    if name in PUBLIC_SCREENSHOTS:
        return True
    if name in PRIVATE_FILES or name.startswith(PRIVATE_PREFIXES):
        return False
    if name.casefold().endswith((".zip", ".pyc", ".pyo", ".log", ".tmp")) or "__pycache__" in name:
        return False
    if "/" not in name and Path(name).suffix.casefold() in {".md", ".txt", ".rst"}:
        if name not in PUBLIC_ROOT_DOCUMENTS and not name.startswith(("LICENSE", "COPYING", "NOTICE")):
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
        local_outputs = {"build", "dist", "out", "data", "cache", "logs", "__pycache__", "venv"}
        names = [file.relative_to(root).as_posix() for file in root.rglob("*")
                 if file.is_file()
                 and not any(part in local_outputs or part.startswith(".venv")
                             for part in file.relative_to(root).parts)
                 and file.suffix.casefold() not in {".pyc", ".pyo"}]
    return sorted(name for name in names if not publishable(name))
