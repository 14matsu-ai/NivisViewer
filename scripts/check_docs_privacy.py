from __future__ import annotations

import argparse
import re
from pathlib import Path


PRIVATE_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]+Users[\\/]+|/(?:home|Users)/)"
    r"(?P<user>[^\\/\s`\"']+)",
    re.IGNORECASE,
)
MASKED_USERS = {"user", "<user>", "<username>", "%username%", "{username}"}
DOCUMENT_EXTENSIONS = {".md", ".rst", ".txt"}


def contains_private_information(text: str) -> bool:
    if any(match["user"].casefold() not in MASKED_USERS
           for match in PRIVATE_PATH.finditer(text)):
        return True
    local_user = Path.home().name.casefold()
    return (local_user not in MASKED_USERS
            and re.search(rf"(?<!\w){re.escape(local_user)}(?!\w)", text, re.IGNORECASE) is not None)


def find_private_paths(root: Path) -> list[tuple[str, int]]:
    findings = []
    documents = set((root / "docs").rglob("*"))
    documents.update(path for path in root.iterdir()
                     if path.suffix.casefold() in DOCUMENT_EXTENSIONS)
    for path in sorted(documents):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if contains_private_information(line):
                findings.append((path.relative_to(root).as_posix(), number))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    findings = find_private_paths(arguments.root)
    for filename, number in findings:
        # Report the location, never repeat the private value in a build log.
        print(f"Private user path in documentation: {filename}:{number}")
    if findings:
        return 1
    print("Documentation privacy check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
