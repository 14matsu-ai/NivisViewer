"""Reject private material in newly published Git history (pre-push input)."""

from pathlib import Path
import subprocess
import sys

from publication_policy import publishable


def git(*arguments: str) -> bytes:
    return subprocess.check_output(["git", *arguments])


def main() -> int:
    seen = set()
    for line in sys.stdin:
        fields = line.split()
        if len(fields) != 4:
            print("Invalid publication reference input.", file=sys.stderr)
            return 1
        local_ref, local_id, remote_ref, remote_id = fields
        if set(local_id) == {"0"}:
            continue
        arguments = ["rev-list", local_id]
        if set(remote_id) != {"0"}:
            exists = subprocess.run(["git", "cat-file", "-e", remote_id], capture_output=True)
            if exists.returncode == 0:
                arguments.append("^" + remote_id)
        revisions = git(*arguments).decode("ascii").splitlines()
        for revision in revisions:
            if revision in seen:
                continue
            seen.add(revision)
            entries = git("ls-tree", "-r", "-z", revision).split(b"\0")
            for entry in entries:
                if not entry:
                    continue
                metadata, filename = entry.split(b"\t", 1)
                name = filename.decode("utf-8")
                if not publishable(name) or name.startswith("spread_image_viewer/"):
                    print(f"Publication blocked: private material in {remote_ref}: {name}", file=sys.stderr)
                    return 1
                payload = git("cat-file", "blob", metadata.split()[-1].decode("ascii"))
                owner = Path.home().name
                if owner.casefold() != "user" and any(
                    value in payload.lower()
                    for value in (owner.encode().lower(), owner.encode("utf-16-le").lower())
                ):
                    print(f"Publication blocked: private identity in {remote_ref}: {name}", file=sys.stderr)
                    return 1
    print("Published history privacy check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
