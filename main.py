from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.application_controller import ApplicationController


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("NivisViewer")
    app.setOrganizationName("NivisViewer")

    controller = ApplicationController(app)
    initial_path = sys.argv[1] if len(sys.argv) > 1 else None
    controller.start(initial_path)
    exit_code = app.exec()
    controller.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
