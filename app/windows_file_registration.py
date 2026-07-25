from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from .supported_formats import FORMAT_CATEGORIES, normalize_extensions


CLASSES = r"Software\Classes"
CAPABILITIES = r"Software\14matsu-ai\NivisViewer\Capabilities"
REGISTERED_APPLICATIONS = r"Software\RegisteredApplications"
APPLICATION = CLASSES + r"\Applications\NivisViewer.exe"
PROG_IDS = {
    "image": "NivisViewer.Image",
    "archive": "NivisViewer.ArchiveBook",
    "pdf": "NivisViewer.PDF",
}
FRIENDLY_NAMES = {
    "image": "NivisViewer image",
    "archive": "NivisViewer comic archive",
    "pdf": "NivisViewer PDF",
}


@dataclass(frozen=True)
class FileRegistrationStatus:
    registered: bool
    executable_path: str | None
    matches_current_executable: bool
    registered_extensions: tuple[str, ...]
    error_message: str | None = None


class RegistryAdapter(Protocol):
    def set_value(self, key: str, name: str, value: str) -> None: ...
    def get_value(self, key: str, name: str) -> str | None: ...
    def delete_value(self, key: str, name: str) -> None: ...
    def delete_tree(self, key: str) -> None: ...


class WinRegistryAdapter:
    def __init__(self) -> None:
        import winreg

        self.winreg = winreg

    def set_value(self, key: str, name: str, value: str) -> None:
        with self.winreg.CreateKeyEx(
            self.winreg.HKEY_CURRENT_USER,
            key,
            0,
            self.winreg.KEY_SET_VALUE,
        ) as handle:
            self.winreg.SetValueEx(handle, name, 0, self.winreg.REG_SZ, value)

    def get_value(self, key: str, name: str) -> str | None:
        try:
            with self.winreg.OpenKey(
                self.winreg.HKEY_CURRENT_USER,
                key,
                0,
                self.winreg.KEY_QUERY_VALUE,
            ) as handle:
                return str(self.winreg.QueryValueEx(handle, name)[0])
        except OSError:
            return None

    def delete_value(self, key: str, name: str) -> None:
        try:
            with self.winreg.OpenKey(
                self.winreg.HKEY_CURRENT_USER,
                key,
                0,
                self.winreg.KEY_SET_VALUE,
            ) as handle:
                self.winreg.DeleteValue(handle, name)
        except OSError:
            pass

    def delete_tree(self, key: str) -> None:
        try:
            self.winreg.DeleteKeyEx(self.winreg.HKEY_CURRENT_USER, key)
            return
        except AttributeError:
            pass
        except OSError:
            pass
        try:
            with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, key) as handle:
                children: list[str] = []
                index = 0
                while True:
                    try:
                        children.append(self.winreg.EnumKey(handle, index))
                        index += 1
                    except OSError:
                        break
            for child in children:
                self.delete_tree(key + "\\" + child)
            self.winreg.DeleteKey(self.winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass


class MemoryRegistryAdapter:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_value(self, key: str, name: str, value: str) -> None:
        self.values[(key, name)] = value

    def get_value(self, key: str, name: str) -> str | None:
        return self.values.get((key, name))

    def delete_value(self, key: str, name: str) -> None:
        self.values.pop((key, name), None)

    def delete_tree(self, key: str) -> None:
        prefix = key + "\\"
        for item in tuple(self.values):
            if item[0] == key or item[0].startswith(prefix):
                self.values.pop(item, None)


class WindowsFileRegistrationService:
    def __init__(
        self,
        executable_path: str | Path,
        *,
        registry: RegistryAdapter | None = None,
        platform_name: str | None = None,
        shell_notifier=None,
        uri_opener=None,
    ) -> None:
        self.executable_path = os.path.abspath(os.fspath(executable_path))
        self.platform_name = platform_name or sys.platform
        self.registry = (
            registry
            if registry is not None
            else WinRegistryAdapter() if self.platform_name == "win32" else None
        )
        self.shell_notifier = shell_notifier or self._notify_shell
        self.uri_opener = uri_opener or self._open_uri

    def get_status(self) -> FileRegistrationStatus:
        if self.registry is None:
            return FileRegistrationStatus(
                False, None, False, (), "Windowsでのみ利用できます。"
            )
        try:
            command = self.registry.get_value(
                APPLICATION + r"\shell\open\command", ""
            )
            executable = _executable_from_command(command)
            extensions = tuple(
                extension
                for extension in sorted(
                    extension
                    for values in FORMAT_CATEGORIES.values()
                    for extension in values
                )
                if any(
                    self.registry.get_value(
                        CLASSES + "\\" + extension + r"\OpenWithProgids",
                        prog_id,
                    )
                    is not None
                    for prog_id in PROG_IDS.values()
                )
            )
            return FileRegistrationStatus(
                bool(executable and extensions),
                executable,
                bool(
                    executable
                    and os.path.normcase(executable)
                    == os.path.normcase(self.executable_path)
                ),
                extensions,
            )
        except Exception as exc:
            return FileRegistrationStatus(False, None, False, (), str(exc))

    def register(
        self,
        extensions: Sequence[str],
        *,
        add_context_menu: bool,
    ) -> FileRegistrationStatus:
        if self.registry is None:
            return self.get_status()
        selected = normalize_extensions(extensions)
        command = f'"{self.executable_path}" "%1"'
        icon = f'"{self.executable_path}",0'
        try:
            for category, prog_id in PROG_IDS.items():
                if not FORMAT_CATEGORIES[category].intersection(selected):
                    continue
                root = CLASSES + "\\" + prog_id
                self.registry.set_value(root, "", FRIENDLY_NAMES[category])
                self.registry.set_value(root + r"\DefaultIcon", "", icon)
                self.registry.set_value(root + r"\shell\open\command", "", command)

            self.registry.set_value(APPLICATION, "FriendlyAppName", "NivisViewer")
            self.registry.set_value(
                APPLICATION + r"\shell\open\command", "", command
            )
            self.registry.set_value(CAPABILITIES, "ApplicationName", "NivisViewer")
            self.registry.set_value(
                CAPABILITIES, "ApplicationDescription", "漫画・画像ビューア"
            )
            self.registry.set_value(
                REGISTERED_APPLICATIONS, "NivisViewer", CAPABILITIES
            )
            for extension in selected:
                category = _category_for(extension)
                prog_id = PROG_IDS[category]
                self.registry.set_value(
                    APPLICATION + r"\SupportedTypes", extension, ""
                )
                self.registry.set_value(
                    CAPABILITIES + r"\FileAssociations", extension, prog_id
                )
                self.registry.set_value(
                    CLASSES + "\\" + extension + r"\OpenWithProgids",
                    prog_id,
                    "",
                )
                context_root = (
                    CLASSES
                    + r"\SystemFileAssociations"
                    + "\\"
                    + extension
                    + r"\shell\NivisViewer.Open"
                )
                if add_context_menu:
                    self.registry.set_value(
                        context_root, "", "NivisViewerで開く"
                    )
                    self.registry.set_value(
                        context_root + r"\command", "", command
                    )
                else:
                    self.registry.delete_tree(context_root)
            self.shell_notifier()
        except Exception as exc:
            status = self.get_status()
            return FileRegistrationStatus(
                status.registered,
                status.executable_path,
                status.matches_current_executable,
                status.registered_extensions,
                str(exc),
            )
        return self.get_status()

    def unregister(self) -> FileRegistrationStatus:
        if self.registry is None:
            return self.get_status()
        try:
            for extension in sorted(
                extension
                for values in FORMAT_CATEGORIES.values()
                for extension in values
            ):
                for prog_id in PROG_IDS.values():
                    self.registry.delete_value(
                        CLASSES + "\\" + extension + r"\OpenWithProgids",
                        prog_id,
                    )
                self.registry.delete_tree(
                    CLASSES
                    + r"\SystemFileAssociations"
                    + "\\"
                    + extension
                    + r"\shell\NivisViewer.Open"
                )
            for prog_id in PROG_IDS.values():
                self.registry.delete_tree(CLASSES + "\\" + prog_id)
            self.registry.delete_tree(APPLICATION)
            self.registry.delete_tree(CAPABILITIES)
            self.registry.delete_value(REGISTERED_APPLICATIONS, "NivisViewer")
            self.shell_notifier()
        except Exception as exc:
            status = self.get_status()
            return FileRegistrationStatus(
                status.registered,
                status.executable_path,
                status.matches_current_executable,
                status.registered_extensions,
                str(exc),
            )
        return self.get_status()

    def open_default_apps_settings(self) -> bool:
        if self.platform_name != "win32":
            return False
        for uri in (
            "ms-settings:defaultapps?registeredAppUser=NivisViewer",
            "ms-settings:defaultapps",
        ):
            try:
                if self.uri_opener(uri):
                    return True
            except OSError:
                continue
        return False

    def _notify_shell(self) -> None:
        if self.platform_name != "win32":
            return
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)

    @staticmethod
    def _open_uri(uri: str) -> bool:
        subprocess.Popen(
            ["explorer.exe", uri],
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True


def _category_for(extension: str) -> str:
    for category, values in FORMAT_CATEGORIES.items():
        if extension in values:
            return category
    raise ValueError(f"Unsupported extension: {extension}")


def _executable_from_command(command: str | None) -> str | None:
    if not command:
        return None
    value = command.strip()
    if value.startswith('"'):
        end = value.find('"', 1)
        return value[1:end] if end > 1 else None
    return value.split(maxsplit=1)[0]
