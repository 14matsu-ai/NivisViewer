from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from app.app_icon import install_application_icon
from app.app_paths import resolve_app_paths
from app.application_controller import ApplicationController
from app.command_line import parse_command_line, version_text
from app.config_manager import ConfigManager
from app.frozen_smoke import run_frozen_smoke
from app.logging_setup import install_exception_hook, setup_logging
from app.metadata_store import MetadataStore
from app.single_instance import (
    InstanceMessage,
    SingleInstanceBroker,
    make_server_name,
)
from app.windows_app_id import set_windows_app_user_model_id
from app.windows_file_registration import WindowsFileRegistrationService


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
    paths = resolve_app_paths(profile_override=options.profile_dir)
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
    broker: SingleInstanceBroker | None = None
    if not options.no_single_instance and options.smoke_test_output is None:
        broker = SingleInstanceBroker(
            make_server_name(
                paths.executable_dir,
                profile_dir=paths.profile_dir,
            ),
            application,
        )
        try:
            if not broker.try_forward_or_listen(message):
                return 0
        except RuntimeError:
            broker.close()
            broker = None

    logger = setup_logging(paths)
    install_exception_hook(logger)
    if options.smoke_test_output:
        success = run_frozen_smoke(
            application,
            paths,
            options.smoke_test_output,
        )
        return 0 if success else 1

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
        broker.open_request_received.connect(controller.handle_open_request)
    controller.start(restore=not options.no_restore)
    controller.handle_open_request(message)
    if not paths.writable:
        QMessageBox.warning(
            controller.get_browser_window(),
            "読み取り専用プロファイル",
            "設定、履歴、サムネイルキャッシュ、ログを保存できません。\n"
            f"使用中のプロファイル: {paths.profile_dir}",
        )
    try:
        return application.exec()
    finally:
        controller.shutdown()
        if broker is not None:
            broker.close()


if __name__ == "__main__":
    raise SystemExit(main())
