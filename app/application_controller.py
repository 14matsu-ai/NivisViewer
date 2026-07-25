from __future__ import annotations

import os
import weakref
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget
from natsort import natsorted

from .archive_backend import (
    EXTERNAL_ARCHIVE_EXTENSIONS,
    is_supported_archive_candidate,
)
from .archive_backend_registry import ArchiveBackendRegistry
from .browser_window import BrowserWindow
from .config_manager import ConfigManager
from .file_operation_coordinator import FileOperationCoordinator
from .image_source import (
    ARCHIVE_EXTENSIONS,
    BOOK_FILE_EXTENSIONS,
    FolderImageSource,
    SUPPORTED_EXTENSIONS,
)
from .metadata_store import MetadataStore
from .viewer_window import ViewerWindow


WindowFactory = Callable[..., ViewerWindow]
BrowserWindowFactory = Callable[..., BrowserWindow]
VALID_OPEN_BEHAVIORS = {"reuse_active", "always_new", "reuse_or_create"}


class ApplicationController(QObject):
    exit_requested = Signal()
    viewer_created = Signal(object)
    viewer_closed = Signal(object)
    browser_created = Signal(object)
    browser_closed = Signal(object)

    def __init__(
        self,
        application: QApplication,
        parent: QObject | None = None,
        *,
        config_manager: ConfigManager | None = None,
        metadata_store: MetadataStore | None = None,
        window_factory: WindowFactory = ViewerWindow,
        browser_window_factory: BrowserWindowFactory = BrowserWindow,
    ) -> None:
        super().__init__(parent if parent is not None else application)
        self.application = application
        self.config = config_manager or ConfigManager()
        self.settings = self.config.load()
        self.metadata_store = metadata_store or MetadataStore(
            self.config.metadata_database_path,
            self,
        )
        self.archive_backend_registry = ArchiveBackendRegistry(
            config_manager=self.config,
        )
        self.config.settings_changed.connect(self._on_controller_settings_changed)
        self.file_operation_coordinator = FileOperationCoordinator(
            self.metadata_store,
            self,
        )
        self._migrate_legacy_metadata()
        self._window_factory = window_factory
        self._browser_window_factory = browser_window_factory
        self._viewer_windows: list[ViewerWindow] = []
        self._active_viewer: ViewerWindow | None = None
        self._browser_window: BrowserWindow | None = None
        self._shutdown = False
        self._quit_requested = False
        self.quit_when_last_viewer_closed = False
        self.application.setQuitOnLastWindowClosed(False)
        self.application.aboutToQuit.connect(self.shutdown)

    @property
    def viewer_windows(self) -> tuple[ViewerWindow, ...]:
        return tuple(self._viewer_windows)

    def start(self, initial_path: str | None = None) -> BrowserWindow:
        self._shutdown = False
        browser = self.show_browser_window()
        path_to_open = initial_path
        if not path_to_open:
            last_path = self.settings.get("last_open_path", "")
            if (
                bool(self.settings.get("reopen_last_on_start", False))
                and isinstance(last_path, str)
                and last_path
                and Path(last_path).exists()
            ):
                path_to_open = last_path
        if path_to_open:
            self.select_path_in_browser(path_to_open)
            self.open_path(path_to_open)
        return browser

    def create_browser_window(self) -> BrowserWindow:
        existing = self.get_browser_window()
        if existing is not None:
            return existing
        window = self._browser_window_factory(
            config_manager=self.config,
            metadata_store=self.metadata_store,
            open_path_handler=self._handle_browser_open_request,
            file_operation_coordinator=self.file_operation_coordinator,
            affected_viewers_handler=self.viewers_using_paths,
            close_affected_viewers_handler=self.close_viewers,
            archive_backend_registry=self.archive_backend_registry,
        )
        self._browser_window = window
        self._quit_requested = False
        window.closing.connect(self._on_browser_closing)
        window.destroyed.connect(
            lambda _object=None, window_id=id(window): self._on_browser_destroyed(window_id)
        )
        self.browser_created.emit(window)
        return window

    def get_browser_window(self) -> BrowserWindow | None:
        return self._browser_window

    def show_browser_window(self) -> BrowserWindow:
        window = self.get_browser_window() or self.create_browser_window()
        if not window.isVisible():
            window.show_initial()
        return window

    def select_path_in_browser(self, path: str | Path) -> None:
        browser = self.get_browser_window()
        if browser is not None:
            browser.select_path(path)

    def create_viewer_window(self) -> ViewerWindow:
        window = self._window_factory(
            config_manager=self.config,
            metadata_store=self.metadata_store,
            open_path_handler=self._handle_viewer_open_request,
            adjacent_book_handler=self.open_adjacent_book,
            archive_backend_registry=self.archive_backend_registry,
        )
        self._viewer_windows.append(window)
        self._active_viewer = window
        self._quit_requested = False
        window.activated.connect(self._on_viewer_activated)
        window.closing.connect(self._on_viewer_closing)
        window.book_changed.connect(self._on_viewer_book_changed)
        window.destroyed.connect(
            lambda _object=None, window_id=id(window): self._on_viewer_destroyed(window_id)
        )
        self.viewer_created.emit(window)
        return window

    def get_active_viewer(self) -> ViewerWindow | None:
        if self._active_viewer in self._viewer_windows:
            return self._active_viewer
        self._active_viewer = None
        return None

    def open_path(
        self,
        path: str | Path,
        *,
        open_in_new_window: bool | None = None,
    ) -> ViewerWindow:
        window = self._select_viewer_for_open(open_in_new_window)
        if not window.isVisible():
            window.show_initial()
        self._open_path_in_viewer(window, path)
        return window

    def close_viewer_window(self, window: ViewerWindow) -> None:
        if window in self._viewer_windows:
            window.close()

    def viewers_using_paths(
        self,
        paths: tuple[str, ...] | list[str],
    ) -> tuple[ViewerWindow, ...]:
        affected: list[ViewerWindow] = []
        targets = tuple(self._path_key(path) for path in paths)
        for window in self._viewer_windows:
            source = window.book_session.source
            current = window.book_session.current_path
            if source is None or current is None:
                continue
            source_key = self._path_key(source.source_path)
            current_key = self._path_key(current)
            for target_key in targets:
                if self._path_is_within(source_key, target_key):
                    affected.append(window)
                    break
                if self._path_is_within(current_key, target_key):
                    affected.append(window)
                    break
                if (
                    isinstance(source, FolderImageSource)
                    and self._path_is_within(target_key, source_key)
                ):
                    affected.append(window)
                    break
        return tuple(affected)

    def close_viewers(self, viewers: tuple[ViewerWindow, ...]) -> None:
        for window in tuple(viewers):
            if window in self._viewer_windows:
                window.close()

    def open_adjacent_book(self, window: object, direction: int) -> str:
        if not isinstance(window, ViewerWindow) or window not in self._viewer_windows:
            return "unavailable"
        current = self._book_navigation_path(window)
        candidates = self._book_candidates(window)
        if current is None or not candidates:
            return "unavailable"

        current_key = str(current.resolve()).casefold()
        keys = [str(path.resolve()).casefold() for path in candidates]
        try:
            current_index = keys.index(current_key)
        except ValueError:
            return "unavailable"

        next_index = current_index + direction
        if not (0 <= next_index < len(candidates)):
            if bool(self.settings.get("loop_book_navigation", False)):
                next_index %= len(candidates)
            else:
                return "boundary"
        return "opened" if self._open_path_in_viewer(window, candidates[next_index], bring_to_front=False) else "error"

    def bring_window_to_front_once(self, window: QWidget) -> None:
        window.show()
        if window.isActiveWindow():
            return
        window.raise_()
        window.activateWindow()

        window_ref = weakref.ref(window)

        def activate_after_show() -> None:
            target = window_ref()
            if target is None or target.isActiveWindow():
                return
            target.raise_()
            target.activateWindow()

        QTimer.singleShot(0, activate_after_show)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        active = self.get_active_viewer()
        if active is not None:
            self._save_standard_window_state(active)
        for window in tuple(self._viewer_windows):
            window.prepare_shutdown()
        browser = self.get_browser_window()
        if browser is not None:
            browser.prepare_shutdown()
        self.file_operation_coordinator.close()
        self.archive_backend_registry.close()
        self.config.save()
        self.metadata_store.flush()
        self.metadata_store.close()

    def _select_viewer_for_open(self, open_in_new_window: bool | None) -> ViewerWindow:
        if open_in_new_window is True:
            return self.create_viewer_window()

        active = self.get_active_viewer()
        if open_in_new_window is False:
            return active or (
                self._viewer_windows[-1] if self._viewer_windows else self.create_viewer_window()
            )

        behavior = str(self.settings.get("open_viewer_behavior", "reuse_or_create"))
        if behavior not in VALID_OPEN_BEHAVIORS:
            behavior = "reuse_or_create"
        if behavior == "always_new":
            return self.create_viewer_window()
        if behavior == "reuse_active":
            return active or self.create_viewer_window()
        return active or (
            self._viewer_windows[-1] if self._viewer_windows else self.create_viewer_window()
        )

    def _open_path_in_viewer(
        self,
        window: ViewerWindow,
        path: str | Path,
        *,
        bring_to_front: bool = True,
    ) -> bool:
        self._active_viewer = window
        opened = window.open_path(path)
        if (
            opened
            and bring_to_front
            and bool(self.settings.get("bring_viewer_to_front_on_open", True))
        ):
            self.bring_window_to_front_once(window)
        return opened

    def _handle_viewer_open_request(
        self,
        path: str,
        open_in_new_window: bool | None,
        source_window: object,
    ) -> ViewerWindow:
        if open_in_new_window is True:
            return self.open_path(path, open_in_new_window=True)
        if isinstance(source_window, ViewerWindow) and source_window in self._viewer_windows:
            self._open_path_in_viewer(source_window, path)
            return source_window
        return self.open_path(path, open_in_new_window=False)

    def _handle_browser_open_request(
        self,
        path: str,
        open_in_new_window: bool,
    ) -> ViewerWindow:
        return self.open_path(path, open_in_new_window=True if open_in_new_window else None)

    def _on_viewer_activated(self, window: object) -> None:
        if isinstance(window, ViewerWindow) and window in self._viewer_windows:
            self._active_viewer = window
            if window.book_session.current_path is not None:
                self.select_path_in_browser(window.book_session.current_path)

    def _on_viewer_book_changed(self, window: object, path: str) -> None:
        if window is self.get_active_viewer():
            self.select_path_in_browser(path)

    def _on_viewer_closing(self, window: object) -> None:
        if isinstance(window, ViewerWindow):
            self._unregister_viewer(window, save_window_state=window is self._active_viewer)

    def _on_viewer_destroyed(self, window_id: int) -> None:
        for window in tuple(self._viewer_windows):
            if id(window) == window_id:
                self._unregister_viewer(window, save_window_state=False)
                return

    def _on_browser_closing(self, window: object) -> None:
        if isinstance(window, BrowserWindow) and window is self._browser_window:
            self._unregister_browser(window)

    def _on_browser_destroyed(self, window_id: int) -> None:
        browser = self._browser_window
        if browser is not None and id(browser) == window_id:
            self._unregister_browser(browser)

    def _unregister_viewer(
        self,
        window: ViewerWindow,
        *,
        save_window_state: bool,
    ) -> None:
        if window not in self._viewer_windows:
            return
        if save_window_state:
            self._save_standard_window_state(window)
        self._viewer_windows.remove(window)
        if self._active_viewer is window:
            self._active_viewer = self._viewer_windows[-1] if self._viewer_windows else None
            if (
                self._active_viewer is not None
                and self._active_viewer.book_session.current_path is not None
            ):
                self.select_path_in_browser(self._active_viewer.book_session.current_path)
        self.viewer_closed.emit(window)
        self.config.save()
        self._evaluate_application_exit()

    def _unregister_browser(self, window: BrowserWindow) -> None:
        if window is not self._browser_window:
            return
        self._browser_window = None
        self.browser_closed.emit(window)
        self.config.save()
        self._evaluate_application_exit()

    def _evaluate_application_exit(self) -> None:
        if self._browser_window is None and not self._viewer_windows:
            self._request_application_exit()

    def _request_application_exit(self) -> None:
        if self._quit_requested:
            return
        self._quit_requested = True
        self.exit_requested.emit()
        self.application.quit()

    def _save_standard_window_state(self, window: ViewerWindow) -> None:
        self.config.data.update(window.window_state_snapshot())

    def _migrate_legacy_metadata(self) -> None:
        if bool(self.settings.get("metadata_migration_v1_completed", False)):
            return
        if not self.metadata_store.enabled:
            return

        recent = self.settings.get("recent_paths", [])
        positions = self.settings.get("reading_positions", {})
        recent_paths = (
            [path for path in recent if isinstance(path, str) and path]
            if isinstance(recent, list)
            else []
        )
        position_items = (
            [
                (path, page)
                for path, page in positions.items()
                if isinstance(path, str) and path
            ]
            if isinstance(positions, dict)
            else []
        )
        position_by_key: dict[str, int] = {}
        for path, raw_page in position_items:
            try:
                position_by_key[MetadataStore.normalize_path(path)] = max(
                    0, int(raw_page)
                )
            except (TypeError, ValueError):
                continue

        migrated: set[str] = set()
        ordered_paths = list(reversed(recent_paths))
        ordered_paths.extend(path for path, _page in position_items)
        for raw_path in ordered_paths:
            canonical, item_type = self._legacy_book_target(raw_path)
            key = MetadataStore.normalize_path(canonical)
            if key in migrated:
                continue
            migrated.add(key)
            start_page = position_by_key.get(
                key,
                position_by_key.get(MetadataStore.normalize_path(raw_path), 0),
            )
            self.metadata_store.record_book_opened(
                str(canonical),
                item_type=item_type,
                start_page_index=start_page,
                total_pages=None,
            )
            if not self.metadata_store.enabled:
                return

        self.metadata_store.flush()
        if self.metadata_store.enabled:
            self.config.apply(
                {"metadata_migration_v1_completed": True},
                save=True,
            )

    @staticmethod
    def _legacy_book_target(path: str) -> tuple[Path, str]:
        target = Path(path)
        suffix = target.suffix.lower()
        if suffix in SUPPORTED_EXTENSIONS:
            return target.parent, "folder"
        if suffix in ARCHIVE_EXTENSIONS:
            return target, "archive"
        return target, "folder" if target.is_dir() else "unknown"

    @staticmethod
    def _book_navigation_path(window: ViewerWindow) -> Path | None:
        current_path = window.book_session.current_path
        if current_path is None:
            return None
        if current_path.is_file() and current_path.suffix.lower() in SUPPORTED_EXTENSIONS:
            return current_path.parent
        return current_path

    def _book_candidates(self, window: ViewerWindow) -> list[Path]:
        current = self._book_navigation_path(window)
        if current is None:
            return []
        parent = current.parent
        if not parent.exists():
            return []

        candidates: list[Path] = []
        try:
            siblings = tuple(parent.iterdir())
        except OSError:
            return []
        for path in siblings:
            if path.is_dir():
                try:
                    has_images = any(
                        child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS
                        for child in path.iterdir()
                    )
                except OSError:
                    has_images = False
                if has_images:
                    candidates.append(path)
            elif path.is_file() and path.suffix.lower() in BOOK_FILE_EXTENSIONS:
                if (
                    path.suffix.lower() in EXTERNAL_ARCHIVE_EXTENSIONS
                    and not is_supported_archive_candidate(path.name)
                ):
                    continue
                candidates.append(path.parent if path.suffix.lower() in SUPPORTED_EXTENSIONS else path)

        unique: dict[str, Path] = {}
        for path in candidates:
            unique[str(path.resolve()).casefold()] = path
        return natsorted(unique.values(), key=lambda item: item.name.casefold())

    @staticmethod
    def _path_key(path: str | Path) -> str:
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path)))
        ).casefold()

    @staticmethod
    def _path_is_within(path_key: str, root_key: str) -> bool:
        if path_key == root_key:
            return True
        try:
            return os.path.commonpath((path_key, root_key)) == root_key
        except ValueError:
            return False

    def _on_controller_settings_changed(self, changed: dict[str, object]) -> None:
        if {
            "archive_backend_preference",
            "winrar_executable",
            "seven_zip_executable",
        }.intersection(changed):
            self.archive_backend_registry.reset()
