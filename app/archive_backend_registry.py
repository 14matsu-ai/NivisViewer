from __future__ import annotations

import os
from pathlib import Path
from threading import RLock

from .archive_backend import (
    ArchiveBackend,
    ArchiveBackendError,
    ArchiveErrorCode,
    EXTERNAL_ARCHIVE_EXTENSIONS,
)
from .seven_zip_backend import SevenZipBackend
from .seven_zip_locator import SevenZipLocator
from .windows_file_association import WindowsFileAssociationResolver
from .winrar_backend import WinRARBackend
from .winrar_locator import WinRARLocator


_RETRYABLE_BACKEND_ERRORS = {
    ArchiveErrorCode.BACKEND_NOT_FOUND,
    ArchiveErrorCode.UNSUPPORTED_ARCHIVE,
}


class _FailoverArchiveBackend:
    """Try one bounded alternative and remember the winner per archive."""

    def __init__(self, primary: ArchiveBackend, alternative: ArchiveBackend) -> None:
        self.primary = primary
        self.alternative = alternative
        self._lock = RLock()
        self._selected_by_archive: dict[str, ArchiveBackend] = {}

    def is_available(self) -> bool:
        return self.primary.is_available() or self.alternative.is_available()

    def list_entries(self, archive_path: str, *, cancel_token=None):
        key = self._key(archive_path)
        with self._lock:
            selected = self._selected_by_archive.get(key)
        if selected is not None:
            return selected.list_entries(archive_path, cancel_token=cancel_token)
        try:
            listing = self.primary.list_entries(
                archive_path,
                cancel_token=cancel_token,
            )
            winner = self.primary
        except ArchiveBackendError as exc:
            if exc.code not in _RETRYABLE_BACKEND_ERRORS:
                raise
            listing = self.alternative.list_entries(
                archive_path,
                cancel_token=cancel_token,
            )
            winner = self.alternative
        with self._lock:
            self._selected_by_archive[key] = winner
        return listing

    def read_entry(
        self,
        archive_path: str,
        entry_path: str,
        *,
        cancel_token=None,
        maximum_bytes: int | None = None,
    ) -> bytes:
        key = self._key(archive_path)
        with self._lock:
            selected = self._selected_by_archive.get(key)
        if selected is not None:
            return selected.read_entry(
                archive_path,
                entry_path,
                cancel_token=cancel_token,
                maximum_bytes=maximum_bytes,
            )
        try:
            return self.primary.read_entry(
                archive_path,
                entry_path,
                cancel_token=cancel_token,
                maximum_bytes=maximum_bytes,
            )
        except ArchiveBackendError as exc:
            if exc.code not in _RETRYABLE_BACKEND_ERRORS:
                raise
            return self.alternative.read_entry(
                archive_path,
                entry_path,
                cancel_token=cancel_token,
                maximum_bytes=maximum_bytes,
            )

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path)))
        ).casefold()


class ArchiveBackendRegistry:
    def __init__(
        self,
        *,
        config_manager=None,
        locator: SevenZipLocator | None = None,
        seven_zip_locator: SevenZipLocator | None = None,
        winrar_locator: WinRARLocator | None = None,
        association_resolver: WindowsFileAssociationResolver | None = None,
        seven_zip_backend: ArchiveBackend | None = None,
        winrar_backend: ArchiveBackend | None = None,
    ) -> None:
        self.config_manager = config_manager
        self.association_resolver = (
            association_resolver or WindowsFileAssociationResolver()
        )
        self.seven_zip_locator = seven_zip_locator or locator or SevenZipLocator()
        # Backward compatible public attribute used by the current settings UI.
        self.locator = self.seven_zip_locator
        self.winrar_locator = winrar_locator or WinRARLocator(
            association_resolver=self.association_resolver,
        )
        self._provided_seven_zip_backend = seven_zip_backend
        self._provided_winrar_backend = winrar_backend
        self._lock = RLock()
        self._selected: dict[str, ArchiveBackend] = {}
        self._owned_backends: set[ArchiveBackend] = set()

    def backend_for_path(self, path: str | Path) -> ArchiveBackend | None:
        extension = Path(path).suffix.lower()
        if extension not in EXTERNAL_ARCHIVE_EXTENSIONS:
            return None
        with self._lock:
            cached = self._selected.get(extension)
            if cached is not None:
                return cached
        selected = self._select_backend(extension)
        with self._lock:
            existing = self._selected.setdefault(extension, selected)
        return existing

    def reset(self) -> None:
        with self._lock:
            backends = tuple(self._owned_backends)
            self._selected.clear()
            self._owned_backends.clear()
        for backend in backends:
            reset = getattr(backend, "reset", None)
            if callable(reset):
                reset()
        self.seven_zip_locator.reset()
        self.winrar_locator.reset()
        self.association_resolver.reset()

    def close(self) -> None:
        with self._lock:
            backends = tuple(self._owned_backends)
        for backend in backends:
            close = getattr(backend, "close", None)
            if callable(close):
                close()

    def _select_backend(self, extension: str) -> ArchiveBackend:
        if (
            self._provided_seven_zip_backend is not None
            and self._provided_winrar_backend is None
        ):
            return self._provided_seven_zip_backend
        if (
            self._provided_winrar_backend is not None
            and self._provided_seven_zip_backend is None
        ):
            return self._provided_winrar_backend
        preference = self._preference()
        association = self.association_resolver.resolve_archive_extension(extension)
        winrar = self._winrar_backend(extension)
        seven_zip = self._seven_zip_backend(
            association.executable_path
            if association.application_kind == "seven_zip"
            else None
        )

        if preference == "winrar":
            return winrar
        if preference == "seven_zip":
            return seven_zip

        explicit_winrar = self._configured_executable("winrar_executable")
        explicit_seven_zip = self._configured_executable("seven_zip_executable")
        if explicit_winrar and not explicit_seven_zip:
            primary, alternative = winrar, seven_zip
        elif explicit_seven_zip and not explicit_winrar:
            primary, alternative = seven_zip, winrar
        elif association.application_kind == "seven_zip":
            primary, alternative = seven_zip, winrar
        else:
            # WinRAR association and automatic standard/PATH discovery both
            # prefer WinRAR, with exactly one safe 7-Zip fallback.
            primary, alternative = winrar, seven_zip
        wrapper = _FailoverArchiveBackend(primary, alternative)
        self._owned_backends.add(wrapper)
        return wrapper

    def _winrar_backend(self, extension: str) -> ArchiveBackend:
        if self._provided_winrar_backend is not None:
            return self._provided_winrar_backend
        backend = WinRARBackend(
            locator=self.winrar_locator,
            explicit_path_getter=lambda: self._configured_executable(
                "winrar_executable"
            ),
            archive_extension=extension,
        )
        self._owned_backends.add(backend)
        return backend

    def _seven_zip_backend(
        self,
        associated_executable: str | None,
    ) -> ArchiveBackend:
        if self._provided_seven_zip_backend is not None:
            return self._provided_seven_zip_backend
        backend = SevenZipBackend(
            executable_path=associated_executable,
            locator=self.seven_zip_locator,
            explicit_path_getter=lambda: self._configured_executable(
                "seven_zip_executable"
            ),
        )
        self._owned_backends.add(backend)
        return backend

    def _preference(self) -> str:
        if self.config_manager is None:
            return "auto"
        value = str(
            self.config_manager.get("archive_backend_preference", "auto") or "auto"
        )
        return value if value in {"auto", "winrar", "seven_zip"} else "auto"

    def _configured_executable(self, key: str) -> str:
        if self.config_manager is None:
            return ""
        return str(self.config_manager.get(key, "") or "")
