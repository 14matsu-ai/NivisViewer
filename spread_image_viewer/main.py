from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("NivisViewer")
    app.setOrganizationName("NivisViewer")

    window = MainWindow()
    window.show_initial()
    if len(sys.argv) > 1:
        window.open_path(sys.argv[1])
    else:
        window.open_startup_book()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
