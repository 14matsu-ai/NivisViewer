from __future__ import annotations

import logging

from app.app_paths import resolve_app_paths
from app.logging_setup import setup_logging


def test_logging_is_utf8_rotating_and_not_duplicated(tmp_path):
    paths = resolve_app_paths(profile_override=str(tmp_path), source_root=tmp_path)
    logger = setup_logging(paths, logger_name="nivisviewer-test")
    logger.info("日本語 log")
    logger = setup_logging(paths, logger_name="nivisviewer-test")
    logger.info("second")
    for handler in logger.handlers:
        handler.flush()

    assert len(logger.handlers) == 1
    text = (paths.logs_dir / "NivisViewer.log").read_text(encoding="utf-8")
    assert "second" in text
    assert isinstance(logger.handlers[0], logging.Handler)


def test_read_only_profile_uses_null_handler(tmp_path):
    paths = resolve_app_paths(
        profile_override=str(tmp_path),
        source_root=tmp_path,
        check_writable=False,
    )
    paths = type(paths)(**{**paths.__dict__, "writable": False})
    logger = setup_logging(paths, logger_name="nivisviewer-read-only-test")
    assert isinstance(logger.handlers[0], logging.NullHandler)
