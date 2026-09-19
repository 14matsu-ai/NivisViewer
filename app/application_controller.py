from __future__ import annotations

from .i18n import initialize_ui_language, tr


import inspect
import os
import weakref
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QMessageBox,
    QWidget,
)

from .adjacent_book_search import (
    SIBLING_FOLDERS,
    AdjacentBookBrowserSnapshot,
    AdjacentBookSearchRequest,
    AdjacentBookSearchResult,
    AdjacentBookSearchService,
    AdjacentBookSearchStatus,
    lexical_absolute,
    path_key as adjacent_path_key,
)
from .archive_backend_registry import ArchiveBackendRegistry
from .browser_window import BrowserWindow
from .config_manager import ConfigManager
from .file_operation_coordinator import FileOperationCoordinator
from .file_conflict_dialog import ConflictResolutionDialog
from .file_operation_panel import FileOperationPanel
from .file_operation_queue import FileOperationQueue
from .image_source import (
    ARCHIVE_EXTENSIONS,
    FolderImageSource,
    FolderListingSnapshot,
    SUPPORTED_EXTENSIONS,
)
from .image_work_coordinator import ImageWorkCoordinator
from .metadata_store import MetadataStore
from .path_availability import PathAvailabilityService
from .pdfium_service import PdfiumService
from .performance_trace import performance_trace
from .single_instance import InstanceMessage
from .settings_dialog import SettingsDialog, _RETIRED_SETTINGS_DIALOGS
from .startup_restore import StartupRestoreCoordinator
from .application_shutdown import (
    ApplicationShutdownCoordinator,
    ApplicationShutdownSnapshot,
    ApplicationShutdownState,
)
from .viewer_window import ViewerWindow


WindowFactory = Callable[..., ViewerWindow]
BrowserWindowFactory = Callable[..., BrowserWindow]
VALID_OPEN_BEHAVIORS = {"reuse_active", "always_new", "reuse_or_create"}


@dataclass(frozen=True)
class _ViewerSearchReturnContext:
    browser_ref: weakref.ReferenceType[BrowserWindow]
    viewer_ref: weakref.ReferenceType[ViewerWindow]
    location_key: str
    query: str


class FileOperationCloseChoice(StrEnum):
    CLOSE_AFTER_OPERATION = "close_after_operation"
    CANCEL_AND_CLOSE = "cancel_and_close"
    KEEP_OPEN = "keep_open"


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
        pdfium_service: PdfiumService | None = None,
        adjacent_book_search_service: AdjacentBookSearchService | None = None,
        path_availability_service: PathAvailabilityService | None = None,
        file_registration_service=None,
    ) -> None:
        super().__init__(parent if parent is not None else application)
        self.application = application
        self.config = config_manager or ConfigManager()
        self.settings = self.config.load()
        initialize_ui_language(self.settings.get("ui_language", "ja"))
        self.metadata_store = metadata_store or MetadataStore(
            self.config.metadata_database_path,
            self,
        )
        self.archive_backend_registry = ArchiveBackendRegistry(
            config_manager=self.config,
        )
        self.pdfium_service = pdfium_service or PdfiumService()
        self.adjacent_book_search = (
            adjacent_book_search_service or AdjacentBookSearchService(self)
        )
        self.path_availability_service = (
            path_availability_service or PathAvailabilityService(self)
        )
        self._applying_startup_restore = False
        self.startup_restore = StartupRestoreCoordinator(
            self.path_availability_service,
            self._open_startup_restored_path,
            self,
        )
        self.startup_restore.notification_requested.connect(
            self._show_browser_status
        )
        self.image_work_coordinator = ImageWorkCoordinator(self, max_workers=2)
        self.file_registration_service = file_registration_service
        self.config.settings_changed.connect(self._on_controller_settings_changed)
        self.file_operation_queue = FileOperationQueue(self)
        self.file_operation_coordinator = FileOperationCoordinator(
            self.metadata_store,
            self,
            queue=self.file_operation_queue,
        )
        self._migrate_legacy_metadata()
        self._window_factory = window_factory
        self._browser_window_factory = browser_window_factory
        self._viewer_windows: list[ViewerWindow] = []
        self._active_viewer: ViewerWindow | None = None
        self._browser_window: BrowserWindow | None = None
        self._viewer_search_return_context: (
            _ViewerSearchReturnContext | None
        ) = None
        self._shutdown = False
        self._shutdown_complete = False
        self.shutdown_coordinator = ApplicationShutdownCoordinator(self)
        self._adjacent_request_sequence = 0
        self._adjacent_generation = 0
        self._adjacent_request_by_window: dict[int, int] = {}
        self._side_folder_contexts: dict[int, tuple] = {}
        self._side_folder_pending: dict[int, tuple] = {}
        self._adjacent_context: dict[
            int,
            tuple[weakref.ReferenceType[object], int, int, str, str],
        ] = {}
        self._operation_fallback_panel: FileOperationPanel | None = None
        self._operation_conflict_dialog: ConflictResolutionDialog | None = None
        self._continue_operations_without_main_window = False
        self._pending_close_window: weakref.ReferenceType[QWidget] | None = None
        self._quit_requested = False
        self._quit_committed = False
        self._exit_evaluation_suspended = 0
        self._settings_probe_shutdown_timeout_msecs = 250
        self._restore_on_start = True
        self.quit_when_last_viewer_closed = False
        self.application.setQuitOnLastWindowClosed(False)
        self.application.aboutToQuit.connect(self.shutdown)
        self.file_operation_queue.operation_completed.connect(
            self._on_background_file_operation_completed
        )
        self.file_operation_queue.conflicts_required.connect(
            self._on_background_conflicts_required
        )
        self.file_operation_queue.shutdown_finished.connect(
            self._on_file_operation_shutdown_finished
        )
        self.file_operation_queue.shutdown_failed.connect(
            self._on_file_operation_shutdown_failed
        )
        self.adjacent_book_search.result_ready.connect(
            self._on_adjacent_book_search_result
        )

    @property
    def viewer_windows(self) -> tuple[ViewerWindow, ...]:
        return tuple(self._viewer_windows)

    def start(
        self,
        initial_path: str | None = None,
        *,
        restore: bool = True,
    ) -> BrowserWindow:
        if self._shutdown:
            raise RuntimeError("application is shutting down")
        self._restore_on_start = restore
        browser = self.show_browser_window()
        if initial_path:
            self.startup_restore.cancel()
            self.select_path_in_browser(initial_path)
            self.open_path(initial_path)
            return browser
        if restore:
            last_path = self.settings.get("last_open_path", "")
            if (
                bool(self.settings.get("reopen_last_on_start", False))
                and isinstance(last_path, str)
                and last_path
            ):
                self.startup_restore.start(last_path)
        return browser

    def handle_open_request(self, message: InstanceMessage) -> None:
        """Apply one startup/IPC request without changing persistent behavior."""
        if self._shutdown:
            return
        self.startup_restore.cancel()
        self.show_browser_window()
        unique: dict[str, str] = {}
        for path in message.paths:
            unique.setdefault(self._path_key(path), path)
        paths = tuple(unique.values())
        if message.browser_only:
            for path in paths:
                self.select_path_in_browser(path)
            return
        for index, path in enumerate(paths):
            try:
                self.select_path_in_browser(path)
                if message.new_window or index > 0:
                    open_in_new_window: bool | None = True
                elif message.reuse:
                    open_in_new_window = False
                else:
                    open_in_new_window = None
                self.open_path(path, open_in_new_window=open_in_new_window)
            except (OSError, RuntimeError, ValueError):
                continue

    def create_browser_window(self) -> BrowserWindow:
        if self._shutdown:
            raise RuntimeError("application is shutting down")
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
            pdfium_service=self.pdfium_service,
            file_registration_service=self.file_registration_service,
            image_work_coordinator=self.image_work_coordinator,
            path_availability_service=self.path_availability_service,
            folder_navigation_handler=self.handle_browser_folder_navigation,
            restore_initial_location=self._restore_on_start,
        )
        self._browser_window = window
        window._application_close_guard = self._allow_window_close
        window._settings_dialog_open_guard = lambda: not self._shutdown
        self._quit_requested = False
        window.closing.connect(self._on_browser_closing)
        window.activated.connect(self._on_browser_activated)
        window.location_changed.connect(self._on_browser_location_changed)
        window.search_query_edited.connect(
            self._on_browser_search_query_edited
        )
        window.directory_scan_committed.connect(
            self.adjacent_book_search.invalidate
        )
        window.destroyed.connect(
            lambda _object=None, window_id=id(window): self._on_browser_destroyed(window_id)
        )
        self.browser_created.emit(window)
        return window

    def get_browser_window(self) -> BrowserWindow | None:
        return self._browser_window

    def show_browser_window(self) -> BrowserWindow:
        if self._shutdown:
            raise RuntimeError("application is shutting down")
        window = self.get_browser_window() or self.create_browser_window()
        if not window.isVisible():
            window.show_initial()
        return window

    def select_path_in_browser(self, path: str | Path) -> None:
        if self._shutdown:
            return
        browser = self.get_browser_window()
        if browser is not None:
            browser.select_path(path)

    def create_viewer_window(self) -> ViewerWindow:
        if self._shutdown:
            raise RuntimeError("application is shutting down")
        window = self._window_factory(
            config_manager=self.config,
            metadata_store=self.metadata_store,
            open_path_handler=self._handle_viewer_open_request,
            adjacent_book_handler=self.open_adjacent_book,
            archive_backend_registry=self.archive_backend_registry,
            pdfium_service=self.pdfium_service,
            image_work_coordinator=self.image_work_coordinator,
            path_availability_service=self.path_availability_service,
        )
        self._viewer_windows.append(window)
        window._application_close_guard = self._allow_window_close
        self._active_viewer = window
        self._quit_requested = False
        window.activated.connect(self._on_viewer_activated)
        window.closing.connect(self._on_viewer_closing)
        window.slideshow_stopped.connect(
            lambda target: self._cancel_adjacent_search(target, clear_status=True),
        )
        window.book_changed.connect(self._on_viewer_book_changed)
        window.displayed_item_changed.connect(self._on_viewer_displayed_item_changed)
        window.side_folder_requested.connect(self.open_side_folder)
        window.book_session.async_opened.connect(
            lambda result, target=window: self._finish_side_folder(target, result, success=True),
        )
        window.book_session.async_open_failed.connect(
            lambda result, target=window: self._finish_side_folder(target, result, success=False),
        )
        window.interactive_open_started.connect(
            self._on_viewer_interactive_open_started
        )
        window.first_frame_ready.connect(self._on_viewer_first_frame_ready)
        window.interactive_open_cancelled.connect(
            self._on_viewer_interactive_open_cancelled
        )
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
        folder_snapshot: FolderListingSnapshot | None = None,
        browser_snapshot: AdjacentBookBrowserSnapshot | None = None,
    ) -> ViewerWindow:
        if self._shutdown:
            raise RuntimeError("application is shutting down")
        self._invalidate_viewer_search_return_context()
        if not self._applying_startup_restore:
            self.startup_restore.cancel()
        window = self._select_viewer_for_open(open_in_new_window)
        if not window.isVisible():
            window.show_initial()
        self._open_path_in_viewer(
            window,
            path,
            folder_snapshot=folder_snapshot,
            browser_snapshot=browser_snapshot,
        )
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

    def close_viewers(self, viewers: tuple[ViewerWindow, ...]) -> bool:
        self._exit_evaluation_suspended += 1
        try:
            for window in tuple(viewers):
                if window in self._viewer_windows:
                    window.prepare_shutdown(wait_msecs=2000)
                    window.close()
            return self.pdfium_service.flush(wait_seconds=2.0)
        finally:
            self._exit_evaluation_suspended -= 1
            if self._exit_evaluation_suspended == 0:
                QTimer.singleShot(0, self._evaluate_application_exit)

    def open_adjacent_book(
        self,
        window: object,
        direction: int,
        require_browser_snapshot: bool = False,
    ) -> str:
        if self._shutdown:
            return "unavailable"
        if not isinstance(window, ViewerWindow) or window not in self._viewer_windows:
            return "unavailable"
        browser_snapshot = window.browser_navigation_snapshot
        snapshot_current = window.browser_navigation_path
        if browser_snapshot is not None and snapshot_current:
            status, candidate = browser_snapshot.adjacent_viewer_path(
                snapshot_current,
                direction,
                loop=bool(self.settings.get("loop_book_navigation", False)),
            )
            if status is not AdjacentBookSearchStatus.FOUND or candidate is None:
                return status.value
            opened = self._open_path_in_viewer(
                window,
                candidate,
                bring_to_front=False,
                folder_snapshot=self._folder_snapshot_from_browser_navigation(
                    browser_snapshot,
                    candidate,
                ),
                browser_snapshot=browser_snapshot,
            )
            return "opened" if opened else "error"
        if require_browser_snapshot:
            return "unavailable"
        current = self._book_navigation_path_lexical(window)
        if current is None:
            return "unavailable"
        self._cancel_adjacent_search(window)
        self._adjacent_request_sequence += 1
        self._adjacent_generation += 1
        request_id = self._adjacent_request_sequence
        generation = self._adjacent_generation
        request = AdjacentBookSearchRequest(
            request_id=request_id,
            current_book_path=current,
            direction=-1 if direction < 0 else 1,
            loop=bool(self.settings.get("loop_book_navigation", False)),
            browser_snapshot=None,
            generation=generation,
        )
        self._adjacent_request_by_window[id(window)] = request_id
        self._adjacent_context[request_id] = (
            weakref.ref(window),
            generation,
            request.direction,
            adjacent_path_key(current),
            "book",
        )
        window.show_adjacent_book_searching(request.direction)
        if not self.adjacent_book_search.search(request):
            self._adjacent_request_by_window.pop(id(window), None)
            self._adjacent_context.pop(request_id, None)
            window.complete_adjacent_book_search(request.direction, "unavailable")
            return "unavailable"
        return "searching"

    def open_side_folder(self, window: object, direction: int) -> str:
        if (
            self._shutdown or not isinstance(window, ViewerWindow)
            or window not in self._viewer_windows or window._shutdown_prepared
            or not bool(self.settings.get("mouse_side_buttons_folder_navigation", False))
            or not isinstance(window.book_session.source, FolderImageSource)
        ):
            return "unavailable"
        source = window.book_session.source
        epoch = window.book_session.generation
        context = self._side_folder_contexts.get(id(window))
        retained_folder_anchor = context is not None and context[2:] == (epoch, id(source))
        if retained_folder_anchor:
            anchor, snapshot = context[:2]
        else:
            anchor, snapshot = window.browser_navigation_path, window.browser_navigation_snapshot
        browser = self.get_browser_window()
        # Refresh only the relevant parent. Reading pages inside an opened
        # folder must never rebase navigation onto that folder's children.
        parent = snapshot.parent_folder if snapshot is not None else str(Path(anchor).parent)
        if (browser is not None and browser.current_path is not None
                and adjacent_path_key(browser.current_path) == adjacent_path_key(parent)):
            refreshed = browser.adjacent_book_snapshot(parent)
            if refreshed is not None and refreshed.contains_viewer_path(anchor):
                snapshot = refreshed
        if snapshot is None:
            window._set_status_override(tr('移動元のBrowser一覧がありません'), 2500)
            return "unavailable"
        if not retained_folder_anchor:
            # This property validates the committed display's epoch/source.
            # Membership keeps a folder opened from its parent anchored to
            # that folder, rather than rebasing onto the child being painted.
            displayed_path = window.displayed_browser_path
            if displayed_path is not None and snapshot.contains_viewer_path(displayed_path):
                anchor = displayed_path
        status, candidate = snapshot.adjacent_folder_path(
            anchor, direction, loop=bool(self.settings.get("loop_book_navigation", False)),
        )
        if candidate is None:
            window._set_status_override(tr('移動できるフォルダーがありません'), 2500)
            return status.value
        self._side_folder_pending.pop(id(window), None)
        self._cancel_adjacent_search(window, clear_status=True)
        self._open_path_in_viewer(window, candidate, bring_to_front=False, browser_snapshot=snapshot)
        # BookSession publishes only the matching generation. Preserve an
        # additional request fence for the no-images Browser fallback.
        self._side_folder_pending[id(window)] = (
            window.book_session._open_generation, candidate, snapshot,
        )
        return "opening"

    def _finish_side_folder(self, window: ViewerWindow, result, *, success: bool) -> None:
        pending = self._side_folder_pending.get(id(window))
        if pending is None:
            return
        # Successful opens carry the committed book epoch, whereas failures
        # carry the request generation. Failed/cancelled opens separate them.
        if success:
            if (result.generation != window.book_session.generation
                    or adjacent_path_key(result.requested_path) != adjacent_path_key(pending[1])):
                return
        elif pending[0] != result.generation:
            return
        self._side_folder_pending.pop(id(window), None)
        if (
            self._shutdown or window not in self._viewer_windows or window._shutdown_prepared
            or pending[0] != window.book_session._open_generation
            or not bool(self.settings.get("mouse_side_buttons_folder_navigation", False))
            or (not success and (result.cancelled or result.code != "no_images"))
        ):
            return
        _, candidate, snapshot = pending
        self._side_folder_contexts[id(window)] = (
            candidate, snapshot, window.book_session.generation, id(window.book_session.source),
        )
        if not success and window is self.get_active_viewer():
            browser = self.get_browser_window() or self.create_browser_window()
            browser.navigate_to(candidate)
            browser.show()
            window._set_status_override(tr('画像がないためBrowserでフォルダーを表示しました'), 3000)

    def handle_browser_folder_navigation(
        self,
        window: object,
        direction: int,
    ) -> str:
        if (
            self._shutdown
            or not isinstance(window, BrowserWindow)
            or window is not self._browser_window
            or window.current_path is None
        ):
            return "unavailable"
        current_folder = lexical_absolute(window.current_path)
        parent = lexical_absolute(os.path.dirname(current_folder))
        if adjacent_path_key(parent) == adjacent_path_key(current_folder):
            return "boundary"

        self._cancel_adjacent_search(window)
        self._adjacent_request_sequence += 1
        self._adjacent_generation += 1
        request_id = self._adjacent_request_sequence
        generation = self._adjacent_generation
        request = AdjacentBookSearchRequest(
            request_id=request_id,
            current_book_path=current_folder,
            direction=-1 if direction < 0 else 1,
            loop=False,
            browser_snapshot=None,
            generation=generation,
            candidate_mode=SIBLING_FOLDERS,
            sort_key=window.browser_sort_key.value,
            sort_order=window.browser_sort_order.value,
            folders_first=window.browser_folders_first,
            random_seed=window.browser_random_seed,
        )
        self._adjacent_request_by_window[id(window)] = request_id
        self._adjacent_context[request_id] = (
            weakref.ref(window),
            generation,
            request.direction,
            adjacent_path_key(current_folder),
            "browser_folder",
        )
        if not self.adjacent_book_search.search(request):
            self._adjacent_request_by_window.pop(id(window), None)
            self._adjacent_context.pop(request_id, None)
            return "unavailable"
        return "searching"

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

    def shutdown(self) -> bool:
        if self._shutdown_complete:
            return True
        if not self._shutdown:
            self._shutdown = True
            self.startup_restore.cancel()
            active = self.get_active_viewer()
            if active is not None:
                self._save_standard_window_state(active)
            for window in tuple(self._viewer_windows):
                window.setEnabled(False)
            browser = self.get_browser_window()
            if browser is not None:
                browser.setEnabled(False)
            snapshot = ApplicationShutdownSnapshot(
                running_file_operations=int(
                    self.file_operation_queue.active_operation is not None
                ),
                queued_file_operations=len(
                    self.file_operation_queue.queued_requests
                ),
                pending_pdf_jobs=self.pdfium_service.pending_count,
                path_probe_pending=self.path_availability_service.pending_count,
                thumbnail_pending=(
                    self._browser_window.thumbnail_provider.pending_count
                    if self._browser_window is not None
                    else 0
                ),
                shell_preview_pending=(
                    self._browser_window.thumbnail_provider.shell_preview_pending_count
                    if self._browser_window is not None
                    else 0
                ),
            )
            if self._operation_conflict_dialog is not None:
                self._operation_conflict_dialog.close()
                self._operation_conflict_dialog = None
            if self._operation_fallback_panel is not None:
                self._operation_fallback_panel.close()
                self._operation_fallback_panel = None
            self.shutdown_coordinator.configure(
                (
                    ("viewer_sessions", self._prepare_viewers_for_shutdown),
                    ("browser_workers", self._prepare_browser_for_shutdown),
                    ("settings_probes", self._shutdown_settings_probes),
                    ("archive_backends", self.archive_backend_registry.close),
                    ("adjacent_book_search", self.adjacent_book_search.close),
                    ("path_availability", self.path_availability_service.close),
                    ("pdfium", self._shutdown_pdfium_service),
                    ("image_workers", self.image_work_coordinator.shutdown),
                    ("config_save", self.config.save),
                    ("metadata_flush", self.metadata_store.flush),
                    ("metadata_close", self.metadata_store.close),
                ),
                snapshot=snapshot,
            )
        queue_shutdown_started = (
            self.file_operation_queue.lifecycle.value == "running"
        )
        if queue_shutdown_started:
            self.file_operation_queue.begin_shutdown(cancel_active=True)
        if not self.file_operation_queue.busy:
            if (
                queue_shutdown_started
                and self.shutdown_coordinator.state
                is ApplicationShutdownState.TIMED_OUT
            ):
                return False
            return self._continue_application_shutdown()
        return False

    def _continue_application_shutdown(self) -> bool:
        if not self.shutdown_coordinator.begin_shutdown():
            return False
        self._shutdown_complete = True
        self._commit_application_exit()
        return True

    def _prepare_viewers_for_shutdown(self) -> None:
        for window in tuple(self._viewer_windows):
            window.prepare_shutdown()

    def _prepare_browser_for_shutdown(self) -> None:
        browser = self.get_browser_window()
        if browser is not None:
            browser.prepare_shutdown()

    def _shutdown_settings_probes(self) -> bool:
        dialogs_by_id = {
            id(dialog): dialog for dialog in tuple(_RETIRED_SETTINGS_DIALOGS)
        }
        browser = self.get_browser_window()
        if browser is not None:
            for dialog in browser.findChildren(SettingsDialog):
                dialogs_by_id[id(dialog)] = dialog

        deadline = (
            monotonic()
            + max(0, int(self._settings_probe_shutdown_timeout_msecs)) / 1000
        )
        all_done = True
        for dialog in tuple(dialogs_by_id.values()):
            remaining_msecs = max(0, int((deadline - monotonic()) * 1000))
            if not dialog._prepare_application_shutdown(
                wait_msecs=remaining_msecs
            ):
                all_done = False
        return all_done

    def _shutdown_pdfium_service(self) -> None:
        if self.pdfium_service.shutdown():
            return
        raise RuntimeError(
            self.pdfium_service.last_shutdown_error
            or "PDFium shutdown did not complete."
        )

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
        folder_snapshot: FolderListingSnapshot | None = None,
        browser_snapshot: AdjacentBookBrowserSnapshot | None = None,
        slideshow_transition: bool = False,
    ) -> bool:
        self._cancel_adjacent_search(window, clear_status=True)
        self._active_viewer = window
        browser = self.get_browser_window()
        pending = (
            browser.thumbnail_provider.pending_count
            if browser is not None
            else 0
        )
        trace_id = performance_trace.begin(
            "application_controller.open_path.started",
            f"path={path} browser_pending={pending}",
        )
        window.set_next_open_trace(trace_id)
        open_kwargs = dict(folder_snapshot=folder_snapshot, browser_snapshot=browser_snapshot)
        if slideshow_transition:
            open_kwargs["slideshow_transition"] = True
        opened = window.open_path(path, **open_kwargs)
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
        self._invalidate_viewer_search_return_context()
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
        folder_snapshot: FolderListingSnapshot | None = None,
        browser_snapshot: AdjacentBookBrowserSnapshot | None = None,
    ) -> ViewerWindow:
        try:
            accepts_snapshot = (
                "folder_snapshot"
                in inspect.signature(self.open_path).parameters
            )
            accepts_browser_snapshot = (
                "browser_snapshot"
                in inspect.signature(self.open_path).parameters
            )
        except (TypeError, ValueError):
            accepts_snapshot = False
            accepts_browser_snapshot = False
        if not accepts_snapshot:
            viewer = self.open_path(
                path,
                open_in_new_window=True if open_in_new_window else None,
            )
        else:
            kwargs = {
                "open_in_new_window": True if open_in_new_window else None,
                "folder_snapshot": folder_snapshot,
            }
            if accepts_browser_snapshot:
                kwargs["browser_snapshot"] = browser_snapshot
            viewer = self.open_path(path, **kwargs)
        self._capture_viewer_search_return_context(viewer)
        return viewer

    def _capture_viewer_search_return_context(
        self,
        viewer: ViewerWindow,
    ) -> None:
        self._viewer_search_return_context = None
        browser = self.get_browser_window()
        if browser is None or browser.current_path is None:
            return
        query = browser.active_search_query
        if not bool(
            self.settings.get(
                "browser_preserve_search_for_viewer_roundtrip",
                True,
            )
        ):
            browser.clear_active_browser_search()
            return
        if not query:
            return
        self._viewer_search_return_context = _ViewerSearchReturnContext(
            weakref.ref(browser),
            weakref.ref(viewer),
            adjacent_path_key(browser.current_path),
            query,
        )

    def _invalidate_viewer_search_return_context(self) -> None:
        self._viewer_search_return_context = None

    def _on_browser_location_changed(
        self,
        browser: object,
        _path: str,
    ) -> None:
        context = self._viewer_search_return_context
        if context is not None and context.browser_ref() is browser:
            self._invalidate_viewer_search_return_context()

    def _on_browser_search_query_edited(
        self,
        browser: object,
        _query: str,
    ) -> None:
        context = self._viewer_search_return_context
        if context is not None and context.browser_ref() is browser:
            self._invalidate_viewer_search_return_context()

    def _on_browser_activated(self, browser: object) -> None:
        context = self._viewer_search_return_context
        if context is None or context.browser_ref() is not browser:
            return
        self._viewer_search_return_context = None
        viewer = context.viewer_ref()
        if (
            not isinstance(browser, BrowserWindow)
            or not isinstance(viewer, ViewerWindow)
            or viewer not in self._viewer_windows
            or browser.current_path is None
            or adjacent_path_key(browser.current_path) != context.location_key
        ):
            return
        browser.restore_viewer_roundtrip_search(context.query)

    def _synchronize_browser_to_viewer_item(
        self,
        window: ViewerWindow,
        path: str | Path,
    ) -> None:
        snapshot = window.browser_navigation_snapshot
        if snapshot is None:
            self.select_path_in_browser(path)
            return
        browser = self.get_browser_window()
        if (
            browser is None
            or browser.current_path is None
            or adjacent_path_key(browser.current_path)
            != adjacent_path_key(snapshot.parent_folder)
        ):
            return
        if not snapshot.contains_viewer_path(path):
            return
        browser.synchronize_viewer_item(
            path,
            expected_parent=snapshot.parent_folder,
        )

    def _on_viewer_activated(self, window: object) -> None:
        if isinstance(window, ViewerWindow) and window in self._viewer_windows:
            self._active_viewer = window
            if self._sync_displayed_item_in_current_browser(window):
                return
            if window.book_session.current_path is not None:
                self._synchronize_browser_to_viewer_item(
                    window,
                    window.browser_navigation_path,
                )

    def _on_viewer_book_changed(self, window: object, path: str) -> None:
        if window is not self.get_active_viewer():
            return
        self._synchronize_browser_to_viewer_item(
            window,
            window.browser_navigation_path or path,
        )

    def _sync_displayed_item_in_current_browser(self, window: ViewerWindow) -> bool:
        browser = self.get_browser_window()
        path = window.displayed_browser_path
        if (
            browser is None or browser._shutdown_prepared
            or browser.current_path is None or path is None
            or adjacent_path_key(browser.current_path) != adjacent_path_key(Path(path).parent)
        ):
            return False
        browser.synchronize_viewer_item(
            path, expected_parent=browser.current_path, preserve_selection=True,
        )
        # A filtered-out item must not trigger a fallback navigation/search reset.
        return True

    def _on_viewer_displayed_item_changed(self, window: object, path: str) -> None:
        if (
            not isinstance(window, ViewerWindow)
            or window not in self._viewer_windows
            or window is not self.get_active_viewer()
            or window._shutdown_prepared
            or path != window.displayed_browser_path
        ):
            return
        self._sync_displayed_item_in_current_browser(window)

    def _on_viewer_interactive_open_started(self, _window: object) -> None:
        self.image_work_coordinator.begin_viewer_interactive()

    def _on_viewer_first_frame_ready(self, _window: object) -> None:
        self.image_work_coordinator.end_viewer_interactive()

    def _on_viewer_interactive_open_cancelled(self, _window: object) -> None:
        self.image_work_coordinator.cancel_viewer_interactive()

    def _on_viewer_closing(self, window: object) -> None:
        if isinstance(window, ViewerWindow):
            self._side_folder_contexts.pop(id(window), None)
            self._side_folder_pending.pop(id(window), None)
            context = self._viewer_search_return_context
            if context is not None and context.viewer_ref() is window:
                self._invalidate_viewer_search_return_context()
            self._cancel_adjacent_search(window)
            self._unregister_viewer(window, save_window_state=window is self._active_viewer)

    def _on_viewer_destroyed(self, window_id: int) -> None:
        for window in tuple(self._viewer_windows):
            if id(window) == window_id:
                self._unregister_viewer(window, save_window_state=False)
                return

    def _on_browser_closing(self, window: object) -> None:
        if isinstance(window, BrowserWindow) and window is self._browser_window:
            self._invalidate_viewer_search_return_context()
            self._cancel_adjacent_search(window)
            self._unregister_browser(window)
            if (
                self.file_operation_queue.busy
                and self._continue_operations_without_main_window
            ):
                self._show_operation_fallback()

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
        if self._exit_evaluation_suspended > 0:
            return
        if self._browser_window is None and not self._viewer_windows:
            if (
                self.file_operation_queue.busy
                and self._continue_operations_without_main_window
            ):
                self._show_operation_fallback()
                return
            self._request_application_exit()

    def _allow_window_close(self, window: QWidget) -> bool:
        if isinstance(window, BrowserWindow):
            return self._allow_browser_window_close(window)
        if not self.file_operation_queue.busy:
            return True
        is_last = (
            window is self._browser_window and not self._viewer_windows
        ) or (
            window in self._viewer_windows
            and self._browser_window is None
            and len(self._viewer_windows) == 1
        )
        if not is_last:
            return True
        answer = QMessageBox.question(
            window,
            tr('ファイル操作を実行中'),
            tr('ファイル操作を実行中です。\n［はい］: 操作を続けてウィンドウを閉じる\n［いいえ］: 操作をキャンセルして閉じる\n［キャンセル］: ウィンドウを閉じない'),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.No:
            self._continue_operations_without_main_window = False
            self._pending_close_window = weakref.ref(window)
            window.setEnabled(False)
            status_bar = getattr(window, "statusBar", None)
            if callable(status_bar):
                status_bar().showMessage(tr('ファイル操作をキャンセルして終了しています…'))
            self.file_operation_queue.begin_shutdown(cancel_active=True)
            return False
        self._continue_operations_without_main_window = True
        self._show_operation_fallback()
        return True

    def _allow_browser_window_close(self, window: BrowserWindow) -> bool:
        if window._close_authorized:
            window._close_authorized = False
            return True
        if window._close_after_operation or window._close_after_cancel:
            return False
        if not self.file_operation_queue.busy:
            return True
        if window._close_dialog_visible:
            return False

        active = self.file_operation_queue.active_operation
        operation_id = (
            str(active.operation_id)
            if active is not None and active.operation_id
            else None
        )
        window._close_dialog_visible = True
        try:
            choice = self._ask_file_operation_close_choice(window)
        finally:
            window._close_dialog_visible = False

        if choice is FileOperationCloseChoice.KEEP_OPEN:
            return False

        active_after_dialog = self.file_operation_queue.active_operation
        active_after_id = (
            str(active_after_dialog.operation_id)
            if active_after_dialog is not None
            and active_after_dialog.operation_id
            else None
        )
        window._close_operation_id = (
            operation_id if operation_id == active_after_id else None
        )
        if choice is FileOperationCloseChoice.CLOSE_AFTER_OPERATION:
            window._close_after_operation = True
            window.statusBar().showMessage(
                tr('ファイル操作の完了後にウィンドウを閉じます…')
            )
        else:
            window._close_after_cancel = True
            window.statusBar().showMessage(tr('ファイル操作を中止しています…'))
            window.cancel_operation_button.setEnabled(False)
            if (
                not window._cancel_requested
                and window._close_operation_id is not None
            ):
                window._cancel_requested = True
                self.file_operation_queue.cancel(window._close_operation_id)

        self._maybe_queue_browser_close(window)
        return False

    @staticmethod
    def _build_file_operation_close_dialog(
        window: BrowserWindow,
    ) -> tuple[
        QMessageBox,
        dict[FileOperationCloseChoice, QAbstractButton],
    ]:
        dialog = QMessageBox(window)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle(tr('ファイル操作を実行中'))
        dialog.setText(tr('ファイル操作が完了していません。どうしますか？'))
        buttons = {
            FileOperationCloseChoice.CLOSE_AFTER_OPERATION: dialog.addButton(
                tr('完了後に閉じる'),
                QMessageBox.ButtonRole.AcceptRole,
            ),
            FileOperationCloseChoice.CANCEL_AND_CLOSE: dialog.addButton(
                tr('操作を中止して閉じる'),
                QMessageBox.ButtonRole.DestructiveRole,
            ),
            FileOperationCloseChoice.KEEP_OPEN: dialog.addButton(
                tr('閉じない'),
                QMessageBox.ButtonRole.RejectRole,
            ),
        }
        keep_open_button = buttons[FileOperationCloseChoice.KEEP_OPEN]
        dialog.setDefaultButton(keep_open_button)
        dialog.setEscapeButton(keep_open_button)
        return dialog, buttons

    def _ask_file_operation_close_choice(
        self,
        window: BrowserWindow,
    ) -> FileOperationCloseChoice:
        dialog, buttons = self._build_file_operation_close_dialog(window)
        dialog.exec()
        clicked = dialog.clickedButton()
        for choice, button in buttons.items():
            if clicked is button:
                return choice
        return FileOperationCloseChoice.KEEP_OPEN

    def _maybe_queue_browser_close(self, window: BrowserWindow) -> None:
        if not (window._close_after_operation or window._close_after_cancel):
            return
        if self.file_operation_queue.busy or window._deferred_close_queued:
            return
        window._deferred_close_queued = True
        window_ref = weakref.ref(window)
        QTimer.singleShot(
            0,
            lambda: self._finish_deferred_browser_close(window_ref),
        )

    def _finish_deferred_browser_close(
        self,
        window_ref: weakref.ReferenceType[BrowserWindow],
    ) -> None:
        window = window_ref()
        if window is None:
            return
        window._deferred_close_queued = False
        if self.file_operation_queue.busy:
            return
        if not (window._close_after_operation or window._close_after_cancel):
            return
        window._close_after_operation = False
        window._close_after_cancel = False
        window._cancel_requested = False
        window._close_operation_id = None
        window._close_authorized = True
        window.cancel_operation_button.setEnabled(False)
        window.close()

    def _show_operation_fallback(self) -> None:
        if self._operation_fallback_panel is None:
            panel = FileOperationPanel()
            panel.setWindowTitle(tr('NivisViewer - ファイル操作'))
            panel.bind(self.file_operation_queue)
            panel_id = id(panel)
            panel.destroyed.connect(
                lambda _object=None, panel_id=panel_id: (
                    self._clear_operation_fallback_panel(panel_id)
                )
            )
            self._operation_fallback_panel = panel
        active = self.file_operation_queue.active_operation
        state = self.file_operation_queue.active_state
        if active is not None and state is not None:
            self._operation_fallback_panel.show_operation(active, state)
        self._operation_fallback_panel.show()

    def _clear_operation_fallback_panel(self, panel_id: int) -> None:
        panel = self._operation_fallback_panel
        if panel is not None and id(panel) == panel_id:
            self._operation_fallback_panel = None

    def _on_background_file_operation_completed(self, result: object) -> None:
        self.adjacent_book_search.invalidate()
        browser = self._browser_window
        if browser is not None and (
            browser._close_after_operation or browser._close_after_cancel
        ):
            expected_operation_id = browser._close_operation_id
            result_operation_id = str(
                getattr(result, "operation_id", "") or ""
            )
            if (
                expected_operation_id is None
                or result_operation_id == expected_operation_id
            ):
                browser._close_operation_id = None
                self._maybe_queue_browser_close(browser)
        if self.file_operation_queue.busy:
            return
        if self._operation_fallback_panel is not None:
            self._operation_fallback_panel.close_if_idle()
        if (
            self._continue_operations_without_main_window
            and self._browser_window is None
            and not self._viewer_windows
        ):
            self._continue_operations_without_main_window = False
            QTimer.singleShot(0, self._evaluate_application_exit)

    def _on_file_operation_shutdown_finished(self) -> None:
        pending_ref = self._pending_close_window
        self._pending_close_window = None
        if self._shutdown:
            self._continue_application_shutdown()
        if pending_ref is None:
            return
        window = pending_ref()
        if window is None:
            return
        window.setEnabled(True)
        QTimer.singleShot(0, window.close)

    def _on_file_operation_shutdown_failed(self, message: str) -> None:
        pending_ref = self._pending_close_window
        if pending_ref is None:
            return
        window = pending_ref()
        if window is None:
            return
        window.setEnabled(True)
        status_bar = getattr(window, "statusBar", None)
        if callable(status_bar):
            status_bar().showMessage(message, 5000)

    def _on_background_conflicts_required(self, plan: object) -> None:
        if self._browser_window is not None or self._shutdown:
            return
        dialog = ConflictResolutionDialog(plan, self.get_active_viewer())
        self._operation_conflict_dialog = dialog
        dialog.resolved.connect(
            lambda operation_id, resolutions, apply_same: (
                self.file_operation_queue.resolve_conflicts(
                    operation_id,
                    resolutions,
                    apply_to_same_kind=apply_same,
                )
            )
        )
        dialog.finished.connect(
            lambda _result: setattr(self, "_operation_conflict_dialog", None)
        )
        dialog.open()

    def _request_application_exit(self) -> None:
        if self._quit_requested:
            return
        self._quit_requested = True
        if self.shutdown():
            self._commit_application_exit()

    def _commit_application_exit(self) -> None:
        if not self._quit_requested or self._quit_committed:
            return
        self._quit_committed = True
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
        if not ordered_paths:
            # A fresh profile has no legacy records to transfer.  Persisting
            # this marker here would add a synchronous config write before
            # the first Browser paint; the normal shutdown save persists it.
            self.config.apply({"metadata_migration_v1_completed": True})
            return
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
        return target, "folder"

    def _open_startup_restored_path(self, path: str) -> bool:
        if self._shutdown:
            return False
        self._applying_startup_restore = True
        try:
            self.select_path_in_browser(path)
            self.open_path(path)
        except (OSError, RuntimeError, ValueError):
            return False
        finally:
            self._applying_startup_restore = False
        return True

    def _show_browser_status(self, message: str) -> None:
        browser = self.get_browser_window()
        if browser is not None and not self._shutdown:
            browser.statusBar().showMessage(message, 3000)

    @staticmethod
    def _book_navigation_path_lexical(window: ViewerWindow) -> str | None:
        current_path = window.book_session.current_path
        if current_path is None:
            return None
        current = lexical_absolute(current_path)
        if os.path.splitext(current)[1].lower() in SUPPORTED_EXTENSIONS:
            return lexical_absolute(os.path.dirname(current))
        return current

    @staticmethod
    def _folder_snapshot_from_browser_navigation(
        snapshot: AdjacentBookBrowserSnapshot,
        selected_path: str,
    ) -> FolderListingSnapshot | None:
        selected_index = snapshot.image_index_for_path(selected_path)
        if selected_index is None:
            return None
        image_ids = snapshot.image_paths
        selected_image = image_ids[selected_index]
        return FolderListingSnapshot(
            Path(snapshot.parent_folder),
            image_ids,
            selected_image,
            snapshot.image_fingerprints,
            generation=snapshot.scan_generation,
            sort_identity=snapshot.sort_identity,
            selected_index=selected_index,
            filter_identity=snapshot.filter_identity,
        )

    def _cancel_adjacent_search(
        self,
        window: object,
        *,
        clear_status: bool = False,
    ) -> None:
        request_id = self._adjacent_request_by_window.pop(id(window), None)
        if request_id is None:
            return
        context = self._adjacent_context.pop(request_id, None)
        self.adjacent_book_search.cancel(request_id)
        if (
            clear_status
            and isinstance(window, ViewerWindow)
            and context is not None
            and context[4] == "book"
        ):
            window.complete_adjacent_book_search(1, "opened")

    def _on_adjacent_book_search_result(
        self,
        result: AdjacentBookSearchResult,
    ) -> None:
        context = self._adjacent_context.pop(result.request_id, None)
        if context is None or self._shutdown:
            return
        window_ref, generation, direction, expected_current_key, operation = (
            context
        )
        window = window_ref()
        if window is None or result.generation != generation:
            return
        if self._adjacent_request_by_window.get(id(window)) != result.request_id:
            return
        self._adjacent_request_by_window.pop(id(window), None)

        if operation == "book":
            if (
                not isinstance(window, ViewerWindow)
                or window not in self._viewer_windows
            ):
                return
            current = self._book_navigation_path_lexical(window)
        else:
            if (
                not isinstance(window, BrowserWindow)
                or window is not self._browser_window
                or window.current_path is None
            ):
                return
            current = lexical_absolute(window.current_path)
        if current is None or adjacent_path_key(current) != expected_current_key:
            return
        if (
            result.status is AdjacentBookSearchStatus.FOUND
            and result.candidate_path
        ):
            if operation == "browser_folder":
                window.navigate_to(result.candidate_path)
                return
            transition = {"slideshow_transition": True} if window._slideshow_waiting_for_next else {}
            opened = self._open_path_in_viewer(
                window, result.candidate_path, bring_to_front=False, **transition,
            )
            window.complete_adjacent_book_search(
                direction,
                "opened" if opened else "error",
            )
            return
        if operation == "book":
            window.complete_adjacent_book_search(direction, result.status.value)

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
        if (
            "browser_preserve_search_for_viewer_roundtrip" in changed
            and not bool(changed["browser_preserve_search_for_viewer_roundtrip"])
        ):
            context = self._viewer_search_return_context
            browser = context.browser_ref() if context is not None else None
            self._invalidate_viewer_search_return_context()
            if browser is not None:
                browser.clear_active_browser_search()
        if {
            "archive_backend_preference",
            "winrar_executable",
            "seven_zip_executable",
        }.intersection(changed):
            self.archive_backend_registry.reset()
