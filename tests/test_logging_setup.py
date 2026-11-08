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


def test_shutdown_phase_and_failure_reach_the_production_log(tmp_path, qapp, monkeypatch):
    from app.application_shutdown import ApplicationShutdownCoordinator
    logger = logging.getLogger('nivisviewer')
    monkeypatch.setattr(logger, 'handlers', [])
    monkeypatch.setattr(logger, 'propagate', logger.propagate)
    monkeypatch.setattr(logger, 'level', logger.level)
    paths = resolve_app_paths(profile_override=str(tmp_path), source_root=tmp_path)
    setup_logging(paths)
    coordinator = ApplicationShutdownCoordinator()
    coordinator.configure([('pending_worker', lambda: False)], diagnostics=lambda: {'jobs': 1})
    try:
        assert not coordinator.begin_shutdown()
        assert not coordinator.begin_shutdown()
        log = (paths.logs_dir / 'NivisViewer.log').read_text(encoding='utf-8')
        assert log.count('Application shutdown phase started step=pending_worker') == 1
        assert log.count('Application shutdown step failed step=pending_worker') == 1
        assert "state={'jobs': 1}" in log
    finally:
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def test_watchdog_remains_armed_after_qt_event_loop_returns(tmp_path, qapp, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from app import freeze_diagnostics
    class Application(QObject):
        aboutToQuit = Signal()
    application = Application()
    arms = []
    monkeypatch.setattr(freeze_diagnostics, '_EXIT_WATCHDOG_FILE', None)
    monkeypatch.setattr(freeze_diagnostics.faulthandler, 'dump_traceback_later',
                        lambda *args, **kwargs: arms.append(kwargs['file']))
    paths = resolve_app_paths(profile_override=str(tmp_path), source_root=tmp_path)
    watchdog = freeze_diagnostics.FreezeDiagnostics(paths, application)
    application.aboutToQuit.emit()
    watchdog.keep_during_exit()
    handle = freeze_diagnostics._EXIT_WATCHDOG_FILE
    try:
        assert len(arms) == 3 and all(file is handle for file in arms)
        assert not handle.closed and not watchdog._timer.isActive()
        log = next(paths.logs_dir.glob('freeze-*.log')).read_text(encoding='utf-8')
        assert 'Qt aboutToQuit' in log and 'Qt event loop returned' in log
    finally:
        handle.close()
