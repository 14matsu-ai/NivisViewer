from __future__ import annotations

from app.i18n import tr


import os
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from app.app_icon import install_application_icon
from app.app_paths import _probe_writable, resolve_app_paths
from app.application_controller import ApplicationController
from app.command_line import parse_command_line, version_text
from app.config_manager import ConfigManager
from app.frozen_smoke import run_frozen_smoke
from app.freeze_diagnostics import FreezeDiagnostics
from app.logging_setup import install_exception_hook, setup_logging
from app.metadata_store import MetadataStore
from app.single_instance import (
    InstanceMessage,
    InstanceUnavailableError,
    SingleInstanceBroker,
    make_server_name,
)
from app.windows_app_id import set_windows_app_user_model_id
from app.windows_file_registration import WindowsFileRegistrationService


# Primary GUI processes keep the write lease through Qt/Python teardown.
_PROCESS_PROFILE_LOCKS: list[QLockFile] = []


def main(arguments: list[str] | None = None) -> int:
    raw_arguments = sys.argv[1:] if arguments is None else arguments
    launch_directory = Path.cwd()
    options = parse_command_line(
        raw_arguments,
        launch_directory=launch_directory,
    )
    if options.show_version:
        print(version_text())
        return 0

    set_windows_app_user_model_id()
    paths = resolve_app_paths(profile_override=options.profile_dir, check_writable=False)
    qt_arguments = [sys.argv[0]]
    application = QApplication(qt_arguments)
    application.setApplicationName("NivisViewer")
    application.setOrganizationName("14matsu-ai")
    install_application_icon(application, paths.resource_dir)

    message = InstanceMessage(
        paths=options.paths,
        new_window=options.new_window,
        reuse=options.reuse,
        browser_only=options.browser_only,
        no_restore=options.no_restore,
        sender_pid=os.getpid(),
    )
    profile_lock = None
    try:
        paths.profile_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        paths = replace(paths, writable=False, write_error=str(exc))
    if paths.writable:
        profile_lock = QLockFile(str(paths.profile_dir / '.nivisviewer-instance.lock'))
        profile_lock.setStaleLockTime(0)
        if not profile_lock.tryLock(0) and profile_lock.error() == QLockFile.LockError.PermissionError:
            profile_lock = None
            paths = replace(paths, writable=False, write_error='profile_lock_permission')
    may_start = profile_lock is None or profile_lock.isLocked()
    broker = None
    try:
        if not options.no_single_instance and options.smoke_test_output is None:
            broker = SingleInstanceBroker(make_server_name(
                paths.executable_dir, profile_dir=paths.profile_dir), application)
            if not broker.try_forward_or_listen(message, allow_listen=may_start):
                return 0
        if not may_start:
            raise InstanceUnavailableError('profile_locked')
        if options.no_single_instance and options.smoke_test_output is None:
            probe = SingleInstanceBroker(make_server_name(
                paths.executable_dir, profile_dir=paths.profile_dir), application)
            probe.reject_existing_owner()
        if paths.writable:
            writable, error = _probe_writable(paths.profile_dir)
            paths = replace(paths, writable=writable, write_error=error)
        if profile_lock is not None and options.smoke_test_output is None:
            _PROCESS_PROFILE_LOCKS.append(profile_lock)
        return _run_application(application, paths, options, message, broker)
    except InstanceUnavailableError:
        QMessageBox.warning(None, 'NivisViewer', tr(
            '前のNivisViewerが終了処理中か、このプロファイルを使用しています。終了を待ってから、もう一度開いてください。'))
        return 1
    finally:
        if broker is not None:
            broker.close()
        if profile_lock is not None and profile_lock.isLocked() and profile_lock not in _PROCESS_PROFILE_LOCKS:
            profile_lock.unlock()


def _run_application(application, paths, options, message, broker) -> int:
    logger = setup_logging(paths)
    install_exception_hook(logger)
    if options.smoke_test_output:
        success = run_frozen_smoke(
            application,
            paths,
            options.smoke_test_output,
        )
        return 0 if success else 1

    freeze_diagnostics = FreezeDiagnostics(paths, application)
    config = ConfigManager(paths.config_path, writable=paths.writable)
    metadata = MetadataStore(paths.metadata_path, initialize=paths.writable)
    controller = ApplicationController(
        application,
        config_manager=config,
        metadata_store=metadata,
        file_registration_service=(
            WindowsFileRegistrationService(paths.executable_path)
            if paths.frozen
            else None
        ),
    )
    if broker is not None:
        broker.request_handler = controller.handle_open_request
    controller.start(restore=not options.no_restore)
    controller.handle_open_request(message)
    if not paths.writable:
        QMessageBox.warning(
            controller.get_browser_window(),
            tr('読み取り専用プロファイル'),
            tr('設定、履歴、サムネイルキャッシュ、ログを保存できません。\n使用中のプロファイル: {p0}', p0=paths.profile_dir),
        )
    try:
        logger.info('Application event loop entered')
        return application.exec()
    finally:
        freeze_diagnostics.keep_during_exit()
        logger.info('Application event loop returned; checking shutdown owners')
        complete = controller.shutdown()
        logger.info('Application final shutdown returned complete=%s', complete)


if __name__ == "__main__":
    raise SystemExit(main())
