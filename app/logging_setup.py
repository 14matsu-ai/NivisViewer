from __future__ import annotations

from .i18n import tr


import logging
import platform
import sys
from importlib import metadata
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .app_paths import AppPaths
from .version import __version__


def setup_logging(
    paths: AppPaths,
    *,
    logger_name: str = "nivisviewer",
) -> logging.Logger:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if paths.writable:
        try:
            paths.logs_dir.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                paths.logs_dir / "NivisViewer.log",
                maxBytes=2 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        except OSError:
            pass
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    logger.info(
        "NivisViewer %s; frozen=%s; Python=%s; OS=%s; executable=%s; profile=%s; portable=%s",
        __version__,
        paths.frozen,
        platform.python_version(),
        platform.platform(),
        paths.executable_path,
        paths.profile_dir,
        paths.portable,
    )
    logger.info("Dependencies: %s", dependency_versions())
    return logger


def dependency_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for display, distribution in (
        ("PySide6", "PySide6"),
        ("Pillow", "Pillow"),
        ("psd-tools", "psd-tools"),
        ("natsort", "natsort"),
        ("pypdfium2", "pypdfium2"),
    ):
        try:
            result[display] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            result[display] = "unavailable"
    return result


def install_exception_hook(logger: logging.Logger) -> None:
    previous = sys.excepthook
    handling = False

    def hook(exception_type, exception, traceback) -> None:
        nonlocal handling
        if issubclass(exception_type, KeyboardInterrupt):
            previous(exception_type, exception, traceback)
            return
        if handling:
            previous(exception_type, exception, traceback)
            return
        handling = True
        try:
            logger.critical(
                "Unhandled exception",
                exc_info=(exception_type, exception, traceback),
            )
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox

                if QApplication.instance() is not None:
                    QMessageBox.critical(
                        None,
                        "NivisViewer",
                        tr('予期しないエラーが発生しました。詳細はローカルのログを確認してください。'),
                    )
            except Exception:
                pass
        finally:
            handling = False

    sys.excepthook = hook
