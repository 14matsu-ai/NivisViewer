from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.single_instance import (
    InstanceMessage,
    SingleInstanceBroker,
    make_server_name,
)


def primary(server_name: str, ready: Path, result: Path) -> int:
    application = QCoreApplication([])
    broker = SingleInstanceBroker(server_name, application)

    def received(message: InstanceMessage) -> None:
        result.write_text(
            json.dumps(
                {"paths": message.paths, "sender_pid": message.sender_pid},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        application.quit()

    broker.open_request_received.connect(received)
    if not broker.try_forward_or_listen(InstanceMessage(sender_pid=os.getpid())):
        return 2
    ready.write_text("ready", encoding="ascii")
    QTimer.singleShot(10_000, application.quit)
    exit_code = application.exec()
    broker.close()
    return exit_code if result.exists() else 3


def secondary(server_name: str) -> int:
    application = QCoreApplication([])
    broker = SingleInstanceBroker(server_name, application)
    is_primary = broker.try_forward_or_listen(
        InstanceMessage(
            paths=(r"C:\日本語 folder\book one.cbz", r"\\server\share\本.pdf"),
            new_window=True,
            no_restore=True,
            sender_pid=os.getpid(),
        )
    )
    broker.close()
    return 4 if is_primary else 0


def orchestrate(output: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="NivisViewer-ipc-") as temporary:
        root = Path(temporary)
        ready = root / "ready"
        result = root / "result.json"
        server_name = make_server_name(
            root / f"portable-{uuid.uuid4().hex}",
            user_identity="integration-test",
        )
        primary_process = subprocess.Popen(
            [
                sys.executable,
                __file__,
                "--primary",
                server_name,
                str(ready),
                str(result),
            ],
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                if primary_process.poll() is not None:
                    return primary_process.returncode or 1
                time.sleep(0.03)
            if not ready.exists():
                return 5
            secondary_result = subprocess.run(
                [sys.executable, __file__, "--secondary", server_name],
                stdin=subprocess.DEVNULL,
                timeout=5,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            primary_result = primary_process.wait(timeout=5)
            if secondary_result.returncode or primary_result:
                return 6
            payload = json.loads(result.read_text(encoding="utf-8"))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "success": True,
                        "received": payload,
                        "acknowledged": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return 0
        finally:
            if primary_process.poll() is None:
                primary_process.terminate()
                try:
                    primary_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    primary_process.kill()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", nargs=3, metavar=("SERVER", "READY", "RESULT"))
    parser.add_argument("--secondary")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.primary:
        return primary(
            arguments.primary[0],
            Path(arguments.primary[1]),
            Path(arguments.primary[2]),
        )
    if arguments.secondary:
        return secondary(arguments.secondary)
    if arguments.output is None:
        parser.error("--output is required")
    return orchestrate(arguments.output)


if __name__ == "__main__":
    raise SystemExit(main())
