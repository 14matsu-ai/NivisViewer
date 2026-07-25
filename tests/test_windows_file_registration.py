from __future__ import annotations

from app.supported_formats import FORMAT_CATEGORIES
from app.windows_file_registration import (
    CAPABILITIES,
    CLASSES,
    REGISTERED_APPLICATIONS,
    MemoryRegistryAdapter,
    WindowsFileRegistrationService,
)


def test_registration_is_hkcu_relative_opt_in_and_quotes_command(tmp_path):
    registry = MemoryRegistryAdapter()
    notified = []
    executable = tmp_path / "portable folder" / "NivisViewer.exe"
    service = WindowsFileRegistrationService(
        executable,
        registry=registry,
        platform_name="win32",
        shell_notifier=lambda: notified.append(True),
    )
    selected = tuple(
        sorted(FORMAT_CATEGORIES["image"] | FORMAT_CATEGORIES["pdf"])
    )
    status = service.register(selected, add_context_menu=True)

    assert status.registered
    assert status.matches_current_executable
    assert registry.get_value(REGISTERED_APPLICATIONS, "NivisViewer") == CAPABILITIES
    command = registry.get_value(
        CLASSES + r"\NivisViewer.Image\shell\open\command", ""
    )
    assert command == f'"{executable}" "%1"'
    assert all("UserChoice" not in key for key, _name in registry.values)
    assert notified == [True]


def test_unregister_removes_only_owned_values(tmp_path):
    registry = MemoryRegistryAdapter()
    registry.set_value(
        CLASSES + r"\.jpg\OpenWithProgids", "Other.Application", ""
    )
    service = WindowsFileRegistrationService(
        tmp_path / "NivisViewer.exe",
        registry=registry,
        platform_name="win32",
        shell_notifier=lambda: None,
    )
    service.register([".jpg"], add_context_menu=False)
    service.unregister()

    assert (
        registry.get_value(
            CLASSES + r"\.jpg\OpenWithProgids", "Other.Application"
        )
        == ""
    )
    assert service.get_status().registered is False


def test_non_windows_is_unavailable_and_does_not_open_settings(tmp_path):
    service = WindowsFileRegistrationService(
        tmp_path / "NivisViewer.exe",
        platform_name="linux",
    )
    assert not service.get_status().registered
    assert not service.open_default_apps_settings()
