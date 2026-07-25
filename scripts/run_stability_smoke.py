from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run(command: list[str], output: Path, timeout: float) -> dict[str, object]:
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    elapsed = time.perf_counter() - started
    smoke = {}
    if output.exists():
        smoke = json.loads(output.read_text(encoding="utf-8"))
    return {
        "success": completed.returncode == 0 and smoke.get("success") is True,
        "elapsed_seconds": round(elapsed, 3),
        "return_code": completed.returncode,
        "smoke": smoke,
        "stderr": completed.stderr.decode("utf-8", "replace")[-2000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    arguments = parser.parse_args()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="NivisViewer-stability-") as temporary:
        root = Path(temporary)
        smoke_output = root / "smoke.json"
        profile = root / "profile"
        if arguments.executable:
            command = [str(arguments.executable)]
        else:
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py")]
        command += [
            "--no-single-instance",
            "--profile-dir",
            str(profile),
            "--smoke-test-output",
            str(smoke_output),
        ]
        result = run(command, smoke_output, arguments.timeout)
    result["notes"] = [
        "This automated smoke covers startup, bundled formats, stores, and shutdown.",
        "Use docs/PERFORMANCE_CHECKLIST.md for interactive 10,000-item and long-run checks.",
        "No telemetry or automatic upload is performed.",
    ]
    arguments.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown = arguments.output.with_suffix(".md")
    markdown.write_text(
        "# NivisViewer stability smoke\n\n"
        f"- Success: {result['success']}\n"
        f"- Elapsed: {result['elapsed_seconds']} seconds\n"
        f"- Return code: {result['return_code']}\n",
        encoding="utf-8",
    )
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
