from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Callable

from PySide6.QtCore import (
    QByteArray,
    QCoreApplication,
    QDir,
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QMimeData,
    QPoint,
    QSize,
    Qt,
    QTimer,
    QThreadPool,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QClipboard,
    QDesktopServices,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFileSystemModel,
    QInputDialog,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QTabWidget,
    QToolBar,
    QTreeView,
    QWidget,
)

from .browser_model import (
    BROWSER_ARCHIVE_EXTENSIONS,
    BROWSER_IMAGE_EXTENSIONS,
    BrowserItem,
    BrowserItemDiscovery,
    BrowserItemKind,
    BrowserItemModel,
    browser_item_from_scan_entry,
)
from .browser_main_drop import (
    BrowserMainDropController,
    PendingBrowserFocusRequest,
)
from .browser_item_delegate import (
    BrowserItemDelegate,
)
from .browser_navigation import BrowserLocation, BrowserNavigationHistory
from .browser_scanner import (
    BrowserDirectoryScanner,
    BrowserScanBatch,
    BrowserScanCompleted,
    BrowserScanEntry,
    BrowserScanError,
    BrowserScanRequest,
    BrowserScanPriority,
    BrowserScanStatus,
)
from .browser_sort import (
    BROWSER_DISPLAY_DENSITY_LABELS,
    BROWSER_SORT_KEY_LABELS,
    BROWSER_SORT_ORDER_LABELS,
    BrowserDisplayDensity,
    BrowserSortKey,
    BrowserSortOrder,
    normalize_browser_display_density,
    normalize_browser_sort_key,
    normalize_browser_sort_order,
)
from .browser_thumbnail_scheduler import (
    ThumbnailPriority,
    build_thumbnail_request_plan,
    calculate_grid_visible_range,
)
from .browser_visibility import BrowserVisibilityPolicy
from .archive_backend_registry import ArchiveBackendRegistry
from .adjacent_book_search import (
    AdjacentBookBrowserSnapshot,
    AdjacentBookSnapshotEntry,
    path_key as adjacent_path_key,
)
from .bookmark_model import BookmarkModel
from .config_manager import ConfigManager
from .destination_history import DestinationHistoryStore
from .file_conflict_dialog import ConflictResolutionDialog
from .file_operation_artifact import FileOperationArtifactPolicy
from .file_operation_coordinator import FileOperationCoordinator
from .file_operation_panel import FileOperationPanel
from .file_operation_plan import ConflictResolution, FileOperationPlan
from .file_operation_service import (
    FileCollisionPolicy,
    FileOperationKind,
    FileOperationProgress,
    FileOperationRequest,
    FileOperationResult,
    FileOperationItemState,
)
from .drag_drop import (
    FolderDropProbe,
    choose_drop_operation,
    is_invalid_drop_target,
    is_lexically_supported_viewer_path,
)
from .explorer_list_view import ExplorerListView, PathDropTreeView
from .favorite_item_delegate import FavoriteItemDelegate
from .favorite_row_metrics import FavoriteRowMetrics
from .folder_bookmark_model import FolderBookmarkModel
from .folder_tree_sync import FolderTreeSyncController
from .history_model import HistoryModel
from .image_work_coordinator import ImageWorkCoordinator
from .image_source import FolderListingSnapshot
from .internal_clipboard import (
    InternalClipboardOperation,
    InternalClipboardState,
)
from .metadata_store import MetadataStore
from .path_availability import PathAvailabilityService
from .performance_trace import performance_trace
from .settings_dialog import SettingsDialog
from .sidebar_layout import SidebarLayoutController
from .thumbnail_provider import BrowserThumbnailProvider
from .thumbnail_disk_cache import ThumbnailDiskCache
from .thumbnail_render import ThumbnailRenderPolicy, ThumbnailRenderSpec
from .system_file_opener import SystemFileOpener
from .windows_filename import (
    generate_numbered_name,
    validate_windows_filename,
)


BrowserOpenHandler = Callable[..., object]
AffectedViewersHandler = Callable[[tuple[str, ...]], tuple[object, ...]]
CloseAffectedViewersHandler = Callable[[tuple[object, ...]], bool | None]
FolderNavigationHandler = Callable[[object, int], str]
_THUMBNAIL_LOG = logging.getLogger("nivisviewer.thumbnail")
_FILE_OPERATION_LOG = logging.getLogger("nivisviewer.file_operation")


@dataclass(frozen=True)
class _ListViewState:
    selected_paths: tuple[str, ...]
    current_path: str | None
    anchor_path: str | None
    vertical_scroll: int
    horizontal_scroll: int


@dataclass
class _PendingDirectoryScan:
    path: Path
    generation: int
    record_history: bool
    restore_location: BrowserLocation
    refresh: bool
    failure_history_revert: str | None = None
    committed: bool = False
    refresh_entries: list[BrowserScanEntry] = field(default_factory=list)
    buffered_entries: list[BrowserScanEntry] = field(default_factory=list)
    trace_id: int = 0
    navigation_source: str = "interactive"
    first_batch_arrived: bool = False
    first_batch_applied: bool = False


class BrowserWindow(QMainWindow):
    activated = Signal(object)
    closing = Signal(object)
    directory_scan_committed = Signal(str)

    def __init__(
        self,
        *,
        config_manager: ConfigManager,
        open_path_handler: BrowserOpenHandler | None = None,
        discovery: BrowserItemDiscovery | None = None,
        scanner: BrowserDirectoryScanner | None = None,
        thumbnail_provider: BrowserThumbnailProvider | None = None,
        metadata_store: MetadataStore | None = None,
        file_operation_coordinator: FileOperationCoordinator | None = None,
        affected_viewers_handler: AffectedViewersHandler | None = None,
        close_affected_viewers_handler: CloseAffectedViewersHandler | None = None,
        archive_backend_registry=None,
        pdfium_service=None,
        file_registration_service=None,
        image_work_coordinator: ImageWorkCoordinator | None = None,
        path_availability_service: PathAvailabilityService | None = None,
        system_file_opener: SystemFileOpener | None = None,
        folder_navigation_handler: FolderNavigationHandler | None = None,
        restore_initial_location: bool = True,
    ) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAcceptDrops(True)
        self.setWindowTitle("NivisViewer - ブラウザ")
        self.resize(1100, 760)

        self.config = config_manager
        self.settings = config_manager.data
        self.metadata_store = metadata_store
        self._open_path_handler = open_path_handler
        self._affected_viewers_handler = affected_viewers_handler
        self._close_affected_viewers_handler = close_affected_viewers_handler
        self._folder_navigation_handler = folder_navigation_handler
        self._owns_archive_backend_registry = archive_backend_registry is None
        self.archive_backend_registry = (
            archive_backend_registry
            or ArchiveBackendRegistry(config_manager=self.config)
        )
        if pdfium_service is None:
            from .pdfium_service import PdfiumService

            pdfium_service = PdfiumService()
            self._owns_pdfium_service = True
        else:
            self._owns_pdfium_service = False
        self.pdfium_service = pdfium_service
        self.file_registration_service = file_registration_service
        self.image_work_coordinator = image_work_coordinator
        self.system_file_opener = system_file_opener or SystemFileOpener()
        self._owns_path_availability_service = (
            path_availability_service is None
        )
        self.path_availability_service = (
            path_availability_service or PathAvailabilityService(self)
        )
        self._owns_file_operation_coordinator = file_operation_coordinator is None
        self.file_operation_coordinator = (
            file_operation_coordinator
            or FileOperationCoordinator(self.metadata_store)
        )
        self.file_operation_coordinator.operation_started.connect(
            self._on_file_operation_started
        )
        self.file_operation_coordinator.operation_progress.connect(
            self._on_file_operation_progress
        )
        self.file_operation_coordinator.operation_completed.connect(
            self._on_file_operation_completed
        )
        self.file_operation_coordinator.conflicts_required.connect(
            self._on_file_operation_conflicts_required
        )
        self.destination_history = DestinationHistoryStore(self.config)
        self._conflict_dialogs: dict[str, ConflictResolutionDialog] = {}
        self.discovery = discovery or BrowserItemDiscovery()
        # The scanner is intentionally not a QObject child: a running
        # QThreadPool must not be destroyed synchronously with this window.
        self.scanner = scanner or BrowserDirectoryScanner()
        self.scanner.batch_ready.connect(self._on_scan_batch)
        self.scanner.scan_completed.connect(self._on_scan_completed)
        self.scanner.scan_failed.connect(self._on_scan_failed)
        if thumbnail_provider is None:
            disk_cache = ThumbnailDiskCache(
                self.config.thumbnail_cache_dir,
                enabled=False,
                limit_mb=int(self.settings.get("thumbnail_cache_limit_mb", 512)),
                max_unused_days=int(
                    self.settings.get("thumbnail_cache_max_unused_days", 0)
                ),
            )
            thumbnail_provider = BrowserThumbnailProvider(
                self,
                disk_cache=disk_cache,
                disk_cache_enabled=bool(
                    self.config.writable
                    and
                    self.settings.get("thumbnail_disk_cache_enabled", True)
                ),
                archive_backend_registry=self.archive_backend_registry,
                pdfium_service=self.pdfium_service,
                image_work_coordinator=self.image_work_coordinator,
                preview_settings=self.settings,
            )
        self.thumbnail_provider = thumbnail_provider
        self.thumbnail_provider.thumbnail_ready.connect(self._on_thumbnail_ready)
        thumbnail_failed = getattr(self.thumbnail_provider, "thumbnail_failed", None)
        if thumbnail_failed is not None:
            thumbnail_failed.connect(self._on_thumbnail_failed)
        provisional = getattr(self.thumbnail_provider, "thumbnail_provisional", None)
        if provisional is not None:
            provisional.connect(self._on_thumbnail_provisional)
        preview_state_changed = getattr(
            self.thumbnail_provider,
            "preview_state_changed",
            None,
        )
        if preview_state_changed is not None:
            preview_state_changed.connect(self._on_preview_state_changed)
        resumed = getattr(self.thumbnail_provider, "scheduling_resumed", None)
        if resumed is not None:
            resumed.connect(self._schedule_thumbnail_requests)
        self.current_path: Path | None = None
        self.navigation_history = BrowserNavigationHistory()
        self._generation = self.thumbnail_provider.generation
        self._scan_generation = 0
        self._pending_scan: _PendingDirectoryScan | None = None
        self._pending_tree_navigation_path: Path | None = None
        self._deferred_tree_sync_generation: int | None = None
        self._first_paint_pending_generation: int | None = None
        self._first_paint_trace_id = 0
        self._tree_trace_ids: dict[int, int] = {}
        self._favorite_trace_id = 0
        self._favorite_release_navigated = False
        self._location_restore_token = 0
        self._list_view_restore_token = 0
        self._status_message_token = 0
        self._temporary_status_message: str | None = None
        self._screen_tracking_window = None
        self._pressed_extra_buttons: set[Qt.MouseButton] = set()
        self._shutdown_prepared = False
        self._fast_scrolling = False
        self._last_scroll_value = 0
        self._last_scroll_time = 0.0
        self._file_operation_request_id = 0
        self._active_file_operation_id: int | None = None
        self._internal_clipboard_state = InternalClipboardState()
        self._clipboard_paths: tuple[str, ...] = ()
        self._clipboard_cut = False
        self._setting_clipboard = False
        self._operation_restore_paths: tuple[str, ...] = ()
        self._operation_restore_row: int | None = None
        self._operation_refresh_generation: int | None = None
        self._operation_completion_message: str | None = None
        self._file_operation_requests: dict[int, FileOperationRequest] = {}
        self._drop_probe_workers: set[FolderDropProbe] = set()
        self.browser_main_drop = BrowserMainDropController(self)
        self.browser_main_drop.focus_request_ready.connect(
            self._begin_browser_drop_focus
        )
        self._pending_browser_focus: PendingBrowserFocusRequest | None = None
        self._starting_drop_focus_navigation = False
        self.browser_external_drop_behavior = str(
            self.settings.get(
                "browser_external_drop_behavior",
                "focus_only",
            )
        )
        self.browser_folder_gestures_enabled = bool(
            self.settings.get("browser_folder_gestures_enabled", True)
        )
        self.mouse_gesture_show_trail = bool(
            self.settings.get("mouse_gesture_show_trail", True)
        )
        self.mouse_gesture_min_distance = int(
            self.settings.get("mouse_gesture_min_distance", 36)
        )
        self._file_operation_selection_before: dict[
            int, tuple[tuple[str, ...], int | None]
        ] = {}
        self._sidebar_width = self._safe_sidebar_width(
            self.settings.get("browser_sidebar_width", 280)
        )
        self.thumbnail_size = self._safe_thumbnail_size(
            self.settings.get("thumbnail_size", 180)
        )
        self.thumbnail_frame_ratio = str(
            self.settings.get("thumbnail_frame_ratio", "portrait_1_sqrt2")
        )
        self.thumbnail_crop_mode = str(
            self.settings.get("thumbnail_crop_mode", "smart_crop")
        )
        self.browser_thumbnail_display_mode = str(
            self.settings.get("browser_thumbnail_display_mode", "fit")
        )
        self.thumbnail_quality_mode = str(
            self.settings.get("thumbnail_quality_mode", "auto")
        )
        self.thumbnail_cache_max_edge = max(
            256,
            min(2048, int(self.settings.get("thumbnail_cache_max_edge", 1024))),
        )
        self._thumbnail_dpr = self._current_device_pixel_ratio()
        self.thumbnail_render_spec = self._build_thumbnail_render_spec()
        self.thumbnail_bucket_size = self.thumbnail_render_spec.long_edge
        self.browser_sort_key = normalize_browser_sort_key(
            self.settings.get("browser_sort_key", BrowserSortKey.NAME.value)
        )
        self.browser_sort_order = normalize_browser_sort_order(
            self.settings.get(
                "browser_sort_order",
                BrowserSortOrder.ASCENDING.value,
            )
        )
        self.browser_folders_first = bool(
            self.settings.get("browser_folders_first", True)
        )
        self.browser_display_density = normalize_browser_display_density(
            self.settings.get(
                "browser_display_density",
                BrowserDisplayDensity.STANDARD.value,
            )
        )
        self.browser_item_spacing_mode = str(
            self.settings.get("browser_item_spacing_mode", "preset")
        )
        self.browser_item_spacing = max(
            0, min(32, int(self.settings.get("browser_item_spacing", 2)))
        )
        self.browser_cell_padding = max(
            0, min(12, int(self.settings.get("browser_cell_padding", 0)))
        )
        self.browser_filename_display = str(
            self.settings.get("browser_filename_display", "one_line")
        )
        self.browser_filename_gap = max(
            0, min(32, int(self.settings.get("browser_filename_gap", 0)))
        )
        self.browser_filename_padding_y = max(
            0, min(16, int(self.settings.get("browser_filename_padding_y", 0)))
        )
        self.browser_show_hidden_items = bool(
            self.settings.get("browser_show_hidden_items", True)
        )
        self.browser_show_unsupported_files = bool(
            self.settings.get("browser_show_unsupported_files", True)
        )
        self.browser_show_system_items = bool(
            self.settings.get("browser_show_system_items", False)
        )
        self.browser_sidebar_layout = str(
            self.settings.get(
                "browser_sidebar_layout",
                "favorites_top_tree_bottom",
            )
        )
        raw_sidebar_sizes = self.settings.get(
            "browser_sidebar_splitter_sizes",
            [220, 420],
        )
        self.browser_sidebar_splitter_sizes = (
            [int(value) for value in raw_sidebar_sizes[:2]]
            if isinstance(raw_sidebar_sizes, list)
            else [220, 420]
        )
        self.browser_show_favorites = bool(
            self.settings.get("browser_show_favorites", True)
        )
        self.browser_show_folder_tree = bool(
            self.settings.get("browser_show_folder_tree", True)
        )
        self.browser_show_history = bool(
            self.settings.get("browser_show_history", True)
        )
        self.folder_tree_sync_mode = str(
            self.settings.get("folder_tree_sync_mode", "focus_current")
        )
        self.folder_tree_collapse_unrelated = bool(
            self.settings.get("folder_tree_collapse_unrelated", True)
        )
        self.folder_tree_focus_rebase = bool(
            self.settings.get("folder_tree_focus_rebase", True)
        )
        self.folder_tree_context_ancestor_levels = max(
            0,
            min(
                12,
                int(
                    self.settings.get(
                        "folder_tree_context_ancestor_levels",
                        3,
                    )
                ),
            ),
        )
        self.favorite_row_metrics = FavoriteRowMetrics.normalized(
            padding_y=int(self.settings.get("favorite_row_padding_y", 1)),
            spacing=int(self.settings.get("favorite_row_spacing", 0)),
            icon_size=int(self.settings.get("favorite_icon_size", 16)),
        )
        # Kept as an inert compatibility object for integrations that adjusted
        # the former delay timer. Favorite navigation no longer starts it.
        self._favorite_click_timer = QTimer(self)
        self._favorite_click_timer.setSingleShot(True)
        self._pending_favorite_path: str | None = None
        self._hovered_list_path: str | None = None

        self._folder_change_timer = QTimer(self)
        self._folder_change_timer.setSingleShot(True)
        self._folder_change_timer.setInterval(120)
        self._folder_change_timer.timeout.connect(self._apply_pending_tree_path)

        self._thumbnail_request_timer = QTimer(self)
        self._thumbnail_request_timer.setSingleShot(True)
        self._thumbnail_request_timer.setInterval(30)
        self._thumbnail_request_timer.timeout.connect(
            self._request_visible_thumbnails
        )
        self._scroll_idle_timer = QTimer(self)
        self._scroll_idle_timer.setSingleShot(True)
        self._scroll_idle_timer.setInterval(180)
        self._scroll_idle_timer.timeout.connect(self._on_scroll_idle)
        self._scan_status_timer = QTimer(self)
        self._scan_status_timer.setSingleShot(True)
        self._scan_status_timer.setInterval(120)
        self._scan_status_timer.timeout.connect(self._update_status)
        self._scan_batch_timer = QTimer(self)
        self._scan_batch_timer.setSingleShot(True)
        self._scan_batch_timer.setInterval(120)
        self._scan_batch_timer.timeout.connect(self._flush_pending_scan_batch)

        self._build_ui()
        QTimer.singleShot(1000, self, self._run_idle_cache_cleanup)
        self.config.settings_changed.connect(self.apply_settings)
        self._restore_window_state()
        if restore_initial_location:
            self._restore_initial_folder()

    @property
    def items(self) -> tuple[BrowserItem, ...]:
        return self.item_model.items

    def adjacent_book_snapshot(
        self,
        parent_path: str | Path,
    ) -> AdjacentBookBrowserSnapshot | None:
        """Return committed model data without querying the filesystem."""
        if (
            self.current_path is None
            or self._pending_scan is not None
            or adjacent_path_key(self.current_path)
            != adjacent_path_key(parent_path)
        ):
            return None
        return AdjacentBookBrowserSnapshot(
            parent_folder=str(self.current_path),
            scan_generation=self._scan_generation,
            entries=tuple(
                AdjacentBookSnapshotEntry(
                    absolute_path=str(item.path),
                    item_kind=item.kind.value,
                    extension=item.extension,
                    natural_sort_identity=item.display_name.casefold(),
                    modified_time_ns=item.modified_time_ns,
                    file_size=item.file_size,
                )
                for item in self.items
            ),
        )

    def show_initial(self) -> None:
        self.show()

    def wait_for_scan(self, msecs: int = 5000) -> bool:
        """Diagnostic/test helper; normal UI code must not wait for scans."""
        wait = getattr(self.scanner, "wait_for_done", None)
        if callable(wait):
            wait(max(0, int(msecs)))
        for _ in range(3):
            QCoreApplication.processEvents()
        return self._pending_scan is None

    def navigate_to(
        self,
        path: str | Path,
        *,
        record_history: bool = True,
        restore_location: BrowserLocation | None = None,
        force_reload: bool = False,
        capture_current: bool = True,
        failure_history_revert: str | None = None,
        navigation_source: str = "interactive",
        trace_id: int = 0,
    ) -> bool:
        if trace_id:
            performance_trace.mark(trace_id, "navigation.navigate_to.called")
        if not self._starting_drop_focus_navigation:
            self._cancel_browser_drop_focus()
        target = self._absolute_browser_path(path)

        same_path = self._same_path(self.current_path, target)
        if same_path and not force_reload:
            if restore_location is not None:
                self._schedule_location_restore(restore_location)
            if navigation_source != "favorite":
                self._sync_tree_to_path(target)
            self._sync_address_bar()
            self._update_navigation_actions()
            return True

        pending = self._pending_scan
        if (
            pending is not None
            and self._same_path(pending.path, target)
            and not force_reload
        ):
            pending.restore_location = (
                restore_location or pending.restore_location
            )
            return True

        if capture_current:
            self._update_current_navigation_state()
        self._cancel_pending_scan(rollback_history=True)
        self._scan_generation += 1
        if trace_id:
            performance_trace.mark(
                trace_id,
                "navigation.generation.issued",
                str(self._scan_generation),
            )
        request = BrowserScanRequest(
            path=str(target),
            generation=self._scan_generation,
            visibility_policy=BrowserVisibilityPolicy(
                show_hidden_items=self.browser_show_hidden_items,
                show_unsupported_files=self.browser_show_unsupported_files,
                show_system_items=self.browser_show_system_items,
            ),
            priority=(
                BrowserScanPriority.REFRESH
                if force_reload
                else BrowserScanPriority.INTERACTIVE_NAVIGATION
            ),
            trace_id=trace_id,
        )
        self._pending_scan = _PendingDirectoryScan(
            path=target,
            generation=request.generation,
            record_history=record_history,
            restore_location=(
                restore_location or BrowserLocation(str(target))
            ),
            refresh=same_path and force_reload,
            failure_history_revert=failure_history_revert,
            trace_id=trace_id,
            navigation_source=navigation_source,
        )
        self.address_bar.setText(str(target))
        if not self.scanner.start(request):
            self._pending_scan = None
            self._show_temporary_status("フォルダへアクセスできません")
            self._sync_address_bar()
            return False
        self._update_status(force=True)
        return True

    def set_current_folder(self, folder: str | Path) -> bool:
        return self.navigate_to(folder)

    def _on_scan_batch(self, batch: BrowserScanBatch) -> None:
        pending = self._matching_pending_scan(batch.generation, batch.path)
        if pending is None or self._shutdown_prepared:
            return
        if pending.trace_id and not pending.first_batch_arrived:
            pending.first_batch_arrived = True
            performance_trace.mark(
                pending.trace_id,
                "scanner.first_batch.gui_arrived",
                str(len(batch.entries)),
            )
        if pending.refresh:
            pending.refresh_entries.extend(batch.entries)
            self._schedule_scan_status_update()
            return

        if not pending.committed:
            self._commit_pending_scan(pending)
            self._append_scan_entries(pending, batch.entries)
        else:
            pending.buffered_entries.extend(batch.entries)
            if not self._scan_batch_timer.isActive():
                self._scan_batch_timer.start()
        self._schedule_scan_status_update()
        if self._first_paint_pending_generation != pending.generation:
            self._schedule_thumbnail_requests()

    def _append_scan_entries(
        self,
        pending: _PendingDirectoryScan,
        entries: tuple[BrowserScanEntry, ...],
    ) -> None:
        previous_state = self._capture_list_view_state()
        items = self._items_from_scan_entries(entries)
        self.item_model.append_scan_batch(
            items,
            generation=pending.generation,
        )
        if pending.trace_id and not pending.first_batch_applied:
            pending.first_batch_applied = True
            performance_trace.mark(
                pending.trace_id,
                "browser.model.first_batch.applied",
                str(len(entries)),
            )
        self._restore_list_view_state(previous_state)
        self._restore_pending_scan_location(pending, final=False)
        self._apply_pending_browser_focus(final=False)

    def _flush_pending_scan_batch(self) -> None:
        pending = self._pending_scan
        if (
            pending is None
            or pending.refresh
            or not pending.committed
            or not pending.buffered_entries
        ):
            return
        entries = tuple(pending.buffered_entries)
        pending.buffered_entries.clear()
        self._append_scan_entries(pending, entries)
        self._schedule_scan_status_update()
        self._schedule_thumbnail_requests()

    def _on_scan_completed(self, result: BrowserScanCompleted) -> None:
        pending = self._matching_pending_scan(result.generation, result.path)
        if pending is None or self._shutdown_prepared:
            return
        if result.cancelled:
            self._scan_batch_timer.stop()
            if pending.committed:
                self.item_model.cancel_directory_scan(
                    generation=pending.generation
                )
            self._pending_scan = None
            self._pending_browser_focus = None
            self._update_status()
            return

        if pending.refresh:
            state = self._capture_list_view_state()
            items = self._items_from_scan_entries(
                tuple(pending.refresh_entries)
            )
            self._generation = self.thumbnail_provider.begin_generation()
            self.item_model.set_items(items)
            if self._pending_browser_focus is None:
                self._schedule_list_view_state_restore(state)
            self._restore_location(
                pending.restore_location,
                update_status=False,
            )
        else:
            if not pending.committed:
                self._commit_pending_scan(pending)
            self._scan_batch_timer.stop()
            self._flush_pending_scan_batch()
            self.item_model.finish_directory_scan(
                generation=pending.generation
            )
            selected_path = pending.restore_location.selected_path
            if (
                selected_path
                and self.item_model.row_for_path(selected_path) < 0
            ):
                self._restore_pending_scan_location(pending, final=True)
        self._apply_pending_browser_focus(final=True)

        self._pending_scan = None
        self.directory_scan_committed.emit(str(pending.path))
        self._update_status()
        if (
            pending.refresh
            and self._first_paint_pending_generation != pending.generation
        ):
            self._schedule_thumbnail_requests()
        if pending.refresh:
            QTimer.singleShot(
                0,
                lambda: (
                    self._show_temporary_status("フォルダを更新しました")
                    if not self._shutdown_prepared
                    else None
                ),
            )
        if self._operation_refresh_generation == result.generation:
            self._operation_refresh_generation = None
            QTimer.singleShot(0, self._restore_file_operation_selection)

    def _on_scan_failed(self, error: BrowserScanError) -> None:
        pending = self._matching_pending_scan(error.generation, error.path)
        if pending is None or self._shutdown_prepared:
            return
        if pending.committed:
            self.item_model.cancel_directory_scan(
                generation=pending.generation
            )
        else:
            self._rollback_pending_history(pending)
        self._pending_scan = None
        self._pending_browser_focus = None
        self._scan_batch_timer.stop()
        self._sync_address_bar()
        self._update_navigation_actions()
        if error.status is BrowserScanStatus.NOT_FOUND:
            message = "フォルダが見つかりません"
        elif error.status is BrowserScanStatus.NOT_DIRECTORY:
            message = "このファイル形式は表示できません"
        else:
            message = "フォルダへアクセスできません"
        self._show_temporary_status(message)

    def _commit_pending_scan(self, pending: _PendingDirectoryScan) -> None:
        if pending.committed:
            return
        pending.committed = True
        self.current_path = pending.path
        self.config.set("last_browser_path", str(pending.path))
        self._generation = self.thumbnail_provider.begin_generation()
        self.item_model.begin_directory_scan(generation=pending.generation)
        self.list_view.clearSelection()
        self.list_view.setCurrentIndex(QModelIndex())
        if pending.record_history:
            self.navigation_history.visit(BrowserLocation(str(pending.path)))
        self._folder_change_timer.stop()
        self._pending_tree_navigation_path = None
        self.folder_tree_sync.cancel()
        self._deferred_tree_sync_generation = pending.generation
        self._first_paint_pending_generation = pending.generation
        self._first_paint_trace_id = pending.trace_id
        self.list_view.notify_after_next_paint()
        self._sync_address_bar()
        self._update_navigation_actions()

    def _cancel_pending_scan(self, *, rollback_history: bool) -> None:
        pending = self._pending_scan
        if pending is None:
            return
        self.scanner.cancel(pending.generation)
        self._scan_batch_timer.stop()
        if pending.committed:
            self.item_model.cancel_directory_scan(
                generation=pending.generation
            )
        elif rollback_history:
            self._rollback_pending_history(pending)
        self._pending_scan = None

    def _rollback_pending_history(self, pending: _PendingDirectoryScan) -> None:
        if pending.failure_history_revert == "forward":
            self.navigation_history.go_forward()
        elif pending.failure_history_revert == "back":
            self.navigation_history.go_back()

    def _matching_pending_scan(
        self,
        generation: int,
        path: str,
    ) -> _PendingDirectoryScan | None:
        pending = self._pending_scan
        if (
            pending is None
            or pending.generation != generation
            or not self._same_path(pending.path, Path(path))
        ):
            return None
        return pending

    @staticmethod
    def _items_from_scan_entries(
        entries: tuple[BrowserScanEntry, ...],
    ) -> list[BrowserItem]:
        items: list[BrowserItem] = []
        for entry in entries:
            try:
                items.append(browser_item_from_scan_entry(entry))
            except ValueError:
                continue
        return items

    def _restore_pending_scan_location(
        self,
        pending: _PendingDirectoryScan,
        *,
        final: bool,
    ) -> None:
        selected_path = pending.restore_location.selected_path
        current_item = self.item_model.item_at(
            self.list_view.currentIndex()
        )
        if current_item is not None and (
            selected_path is None
            or not self._same_path(current_item.path, Path(selected_path))
        ):
            return
        if (
            selected_path
            and self.item_model.row_for_path(selected_path) < 0
            and not final
        ):
            return
        self._restore_location(
            pending.restore_location,
            update_status=False,
        )

    def _schedule_scan_status_update(self) -> None:
        if not self._scan_status_timer.isActive():
            self._scan_status_timer.start()

    def select_path(self, path: str | Path) -> None:
        target = self._absolute_browser_path(path)
        if target.suffix.lower() in (
            BROWSER_IMAGE_EXTENSIONS | BROWSER_ARCHIVE_EXTENSIONS
        ):
            folder = target.parent
            selected = target
        else:
            parent = target.parent
            folder = parent if parent != target else target
            selected = target

        location = BrowserLocation(str(folder), selected_path=str(selected))
        if not self.navigate_to(folder, restore_location=location):
            return
        self._restore_location(location)

    def go_back(self) -> bool:
        self._update_current_navigation_state()
        location = self.navigation_history.go_back()
        if location is None:
            self._update_navigation_actions()
            return False
        if self.navigate_to(
            location.path,
            record_history=False,
            restore_location=location,
            capture_current=False,
            failure_history_revert="forward",
        ):
            return True
        self.navigation_history.go_forward()
        self._update_navigation_actions()
        return False

    def go_forward(self) -> bool:
        self._update_current_navigation_state()
        location = self.navigation_history.go_forward()
        if location is None:
            self._update_navigation_actions()
            return False
        if self.navigate_to(
            location.path,
            record_history=False,
            restore_location=location,
            capture_current=False,
            failure_history_revert="back",
        ):
            return True
        self.navigation_history.go_back()
        self._update_navigation_actions()
        return False

    def go_up(self) -> bool:
        if self.current_path is None:
            return False
        parent = self.current_path.parent
        if self._same_path(parent, self.current_path):
            self._update_navigation_actions()
            return False
        return self.navigate_to(
            parent,
            restore_location=BrowserLocation(
                str(parent),
                selected_path=str(self.current_path),
            ),
        )

    def refresh_current_folder(self) -> bool:
        if self.current_path is None:
            return False
        location = self._current_location()
        if not self.navigate_to(
            self.current_path,
            record_history=False,
            restore_location=location,
            force_reload=True,
        ):
            return False
        return True

    def _on_browser_folder_gesture(self, pattern: str) -> None:
        if not self.browser_folder_gestures_enabled:
            return
        if pattern == "U":
            self.go_up()
        elif pattern == "D":
            self.refresh_current_folder()
        elif pattern in {"L", "R"} and self._folder_navigation_handler is not None:
            self._folder_navigation_handler(
                self,
                -1 if pattern == "L" else 1,
            )

    def focus_address_bar(self) -> None:
        self.address_bar.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.address_bar.selectAll()

    def show_history_location(self, index: QModelIndex) -> None:
        entry = self.history_model.entry_at(index)
        if entry is not None:
            self._show_path_in_browser(entry.path)

    def open_item(self, index: QModelIndex, *, open_in_new_window: bool = False) -> None:
        item = self.item_model.item_at(index)
        if item is None:
            return
        if item.kind == BrowserItemKind.FOLDER:
            self.navigate_to(item.path)
            return
        if not item.openable_by_nivisviewer or item.kind is BrowserItemKind.OTHER:
            self._open_system_file(item.path)
            return
        if self._open_path_handler is not None:
            self._invoke_open_path_handler(
                str(item.path),
                open_in_new_window,
                self._folder_snapshot_for_item(item),
            )

    def _open_system_file(self, path: str | Path) -> bool:
        result = self.system_file_opener.open_with_default_application(
            path,
            parent_hwnd=int(self.winId()),
        )
        if result.success:
            self._show_temporary_status(
                "NivisViewerでは表示できません。既定のアプリで開きました"
            )
            return True
        self._show_temporary_status(
            "NivisViewerでは表示できません。"
            + (
                f" {result.error_message}"
                if result.error_message
                else " 関連付けアプリで開けませんでした"
            )
        )
        return False

    def _folder_snapshot_for_item(
        self,
        item: BrowserItem,
    ) -> FolderListingSnapshot | None:
        if (
            item.kind is not BrowserItemKind.IMAGE
            or self.current_path is None
        ):
            return None
        image_ids = tuple(
            str(candidate.path)
            for candidate in self.items
            if candidate.kind is BrowserItemKind.IMAGE
        )
        if str(item.path) not in image_ids:
            return None
        return FolderListingSnapshot(
            self.current_path,
            image_ids,
            str(item.path),
            tuple(
                (
                    str(candidate.path),
                    candidate.file_size,
                    candidate.modified_time_ns,
                )
                for candidate in self.items
                if candidate.kind is BrowserItemKind.IMAGE
            ),
            generation=self._scan_generation,
            sort_identity=(
                f"{self.browser_sort_key.value}:"
                f"{self.browser_sort_order.value}:"
                f"folders_first={int(self.browser_folders_first)}"
            ),
        )

    def _invoke_open_path_handler(
        self,
        path: str,
        open_in_new_window: bool,
        folder_snapshot: FolderListingSnapshot | None = None,
    ) -> object | None:
        handler = self._open_path_handler
        if handler is None:
            return None
        try:
            import inspect

            signature = inspect.signature(handler)
            accepts_snapshot = len(signature.parameters) >= 3 or any(
                parameter.kind is inspect.Parameter.VAR_POSITIONAL
                for parameter in signature.parameters.values()
            )
        except (TypeError, ValueError):
            accepts_snapshot = False
        if accepts_snapshot:
            return handler(path, open_in_new_window, folder_snapshot)
        return handler(path, open_in_new_window)

    def selected_file_operation_paths(self) -> tuple[str, ...]:
        indexes = sorted(
            self.list_view.selectionModel().selectedIndexes(),
            key=lambda index: index.row(),
        )
        paths: list[str] = []
        seen: set[str] = set()
        for index in indexes:
            item = self.item_model.item_at(index)
            if item is None:
                continue
            path = str(self._absolute_browser_path(item.path))
            if FileOperationArtifactPolicy.is_internal_operation_artifact(path):
                continue
            key = self._path_key(path)
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
        return tuple(paths)

    def copy_selected_items(self) -> bool:
        paths = self.selected_file_operation_paths()
        if not paths:
            return False
        self._set_file_clipboard(paths, cut=False)
        self._show_temporary_status(f"{len(paths)}項目をコピー候補にしました")
        return True

    def cut_selected_items(self) -> bool:
        paths = self.selected_file_operation_paths()
        if not paths:
            return False
        self._set_file_clipboard(paths, cut=True)
        self._show_temporary_status(f"{len(paths)}項目を切り取り候補にしました")
        return True

    def clear_file_clipboard(self) -> None:
        self._internal_clipboard_state.clear()
        self._clipboard_paths = ()
        self._clipboard_cut = False
        self.item_model.set_cut_paths(())
        self._update_file_action_states()

    def paste_items(self) -> bool:
        if self.current_path is None:
            return False
        sources = self._clipboard_paths or self._clipboard_file_urls()
        if not sources:
            self._show_temporary_status("貼り付けるファイルがありません")
            return False
        operation = (
            FileOperationKind.MOVE
            if self._internal_clipboard_state.paths
            and self._internal_clipboard_state.is_cut
            else FileOperationKind.COPY
        )
        if _FILE_OPERATION_LOG.isEnabledFor(logging.DEBUG):
            snapshot = self._internal_clipboard_state.snapshot
            _FILE_OPERATION_LOG.debug(
                "paste request kind=%s internal_cut=%s identity=%s "
                "os_drop_effect=%s sources=%r destination=%s",
                operation.value,
                self._internal_clipboard_state.is_cut,
                snapshot.request_identity if snapshot is not None else None,
                InternalClipboardState.preferred_drop_effect(
                    QApplication.clipboard().mimeData()
                ),
                sources,
                self.current_path,
            )
        if (
            operation is FileOperationKind.MOVE
            and all(
                self._same_path(Path(path).parent, self.current_path)
                for path in sources
            )
        ):
            self._show_temporary_status("同じフォルダへの移動は行いません")
            return False
        return self._start_file_operation(
            operation,
            sources=sources,
            destination=self.current_path,
        )

    def rename_selected_item(self) -> bool:
        paths = self.selected_file_operation_paths()
        if len(paths) != 1:
            return False
        source = Path(paths[0])
        new_name = self._prompt_for_filename(
            "名前の変更",
            "新しい名前:",
            source.name,
        )
        if new_name is None or new_name == source.name:
            return False
        old_suffix = source.suffix.casefold()
        new_suffix = Path(new_name).suffix.casefold()
        if old_suffix != new_suffix:
            answer = QMessageBox.question(
                self,
                "拡張子の変更",
                "拡張子を変更すると項目を開けなくなる場合があります。続行しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        return self._start_file_operation(
            FileOperationKind.RENAME,
            sources=paths,
            new_name=new_name,
        )

    def move_selected_to_recycle_bin(self) -> bool:
        paths = self.selected_file_operation_paths()
        if not paths:
            return False
        if len(paths) == 1:
            prompt = f"「{Path(paths[0]).name}」をごみ箱へ移動しますか？"
        else:
            prompt = f"{len(paths)}項目をごみ箱へ移動しますか？"
        answer = QMessageBox.question(
            self,
            "ごみ箱へ移動",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        return self._start_file_operation(
            FileOperationKind.RECYCLE,
            sources=paths,
        )

    def copy_selected_to(self, destination: str | Path | None = None) -> bool:
        paths = self.selected_file_operation_paths()
        if not paths:
            return False
        target = self._choose_destination("コピー先を選択", destination)
        if target is None:
            return False
        return self._start_file_operation(
            FileOperationKind.COPY,
            sources=paths,
            destination=target,
        )

    def move_selected_to(self, destination: str | Path | None = None) -> bool:
        paths = self.selected_file_operation_paths()
        if not paths:
            return False
        target = self._choose_destination("移動先を選択", destination)
        if target is None:
            return False
        return self._start_file_operation(
            FileOperationKind.MOVE,
            sources=paths,
            destination=target,
        )

    def create_new_folder(self) -> bool:
        if self.current_path is None:
            return False
        initial_name = generate_numbered_name(
            "新しいフォルダ",
            (item.display_name for item in self.items),
        )
        if initial_name is None:
            self._show_temporary_status("新しいフォルダ名を生成できません")
            return False
        name = self._prompt_for_filename(
            "新しいフォルダ",
            "フォルダ名:",
            initial_name,
        )
        if name is None:
            return False
        return self._start_file_operation(
            FileOperationKind.CREATE_DIRECTORY,
            destination=self.current_path,
            new_name=name,
        )

    def cancel_file_operation(self) -> None:
        if self.file_operation_coordinator.busy:
            self.file_operation_coordinator.cancel()
            self.statusBar().showMessage("安全な境界でキャンセルしています…")

    def _start_file_operation(
        self,
        operation: FileOperationKind,
        *,
        sources: tuple[str, ...] = (),
        destination: str | Path | None = None,
        new_name: str | None = None,
    ) -> bool:
        filtered_sources = tuple(
            path
            for path in sources
            if not FileOperationArtifactPolicy.is_internal_operation_artifact(
                path
            )
        )
        if sources and not filtered_sources:
            self._show_temporary_status(
                "NivisViewerの未完了一時ファイルは操作できません"
            )
            return False
        sources = filtered_sources
        if (
            self.file_operation_coordinator.busy
            and self.file_operation_coordinator.queue is None
        ):
            self._show_temporary_status("別のファイル操作を実行中です")
            return False
        if operation in {
            FileOperationKind.RENAME,
            FileOperationKind.MOVE,
            FileOperationKind.RECYCLE,
        } and not self._confirm_and_close_affected_viewers(sources):
            return False
        self._file_operation_request_id += 1
        request = FileOperationRequest(
            self._file_operation_request_id,
            operation,
            tuple(str(self._absolute_browser_path(path)) for path in sources),
            (
                str(self._absolute_browser_path(destination))
                if destination is not None
                else None
            ),
            new_name,
            FileCollisionPolicy.SKIP,
        )
        if _FILE_OPERATION_LOG.isEnabledFor(logging.DEBUG):
            _FILE_OPERATION_LOG.debug(
                "operation boundary browser->coordinator request=%s kind=%s "
                "sources=%r destination=%s internal_cut=%s",
                request.request_id,
                request.operation.value,
                request.source_paths,
                request.destination_directory,
                self._internal_clipboard_state.is_cut,
            )
        selected_paths = self.selected_file_operation_paths()
        current_row = (
            self.list_view.currentIndex().row()
            if self.list_view.currentIndex().isValid()
            else None
        )
        self._file_operation_requests[request.request_id] = request
        self._file_operation_selection_before[request.request_id] = (
            selected_paths,
            current_row,
        )
        if not self.file_operation_coordinator.execute(request):
            self._file_operation_requests.pop(request.request_id, None)
            self._file_operation_selection_before.pop(request.request_id, None)
            self._show_temporary_status("ファイル操作を開始できません")
            return False
        return True

    def _on_file_operation_conflicts_required(
        self,
        plan: FileOperationPlan,
    ) -> None:
        if (
            self._shutdown_prepared
            or plan.request_id not in self._file_operation_requests
            or self.file_operation_coordinator.queue is None
        ):
            if self.file_operation_coordinator.queue is not None:
                self.file_operation_coordinator.queue.cancel(plan.operation_id)
            return
        dialog = ConflictResolutionDialog(plan, self)
        self._conflict_dialogs[plan.operation_id] = dialog
        dialog.resolved.connect(self._resolve_file_operation_conflicts)
        dialog.finished.connect(
            lambda _result, operation_id=plan.operation_id: (
                self._conflict_dialogs.pop(operation_id, None)
            )
        )
        dialog.open()

    def _resolve_file_operation_conflicts(
        self,
        operation_id: str,
        resolutions: dict[str, ConflictResolution],
        apply_to_same_kind: bool,
    ) -> None:
        queue = self.file_operation_coordinator.queue
        if queue is None:
            return
        plan_dialog = self._conflict_dialogs.get(operation_id)
        plan = plan_dialog.plan if plan_dialog is not None else None
        replace_destinations = (
            tuple(
                conflict.destination_path
                for conflict in plan.conflicts
                if conflict.destination_path
                and resolutions.get(
                    conflict.conflict_id,
                    ConflictResolution.SKIP,
                )
                is ConflictResolution.REPLACE
            )
            if plan is not None
            else ()
        )
        if (
            replace_destinations
            and not self._confirm_and_close_affected_viewers(replace_destinations)
        ):
            queue.cancel(operation_id)
            return
        queue.resolve_conflicts(
            operation_id,
            resolutions,
            apply_to_same_kind=apply_to_same_kind,
        )

    def _confirm_and_close_affected_viewers(
        self,
        paths: tuple[str, ...],
    ) -> bool:
        if self._affected_viewers_handler is None:
            return True
        viewers = self._affected_viewers_handler(paths)
        if not viewers:
            return True
        answer = QMessageBox.question(
            self,
            "ViewerWindowで使用中",
            "この項目はViewerWindowで開かれています。\n"
            "対象Viewerを閉じて操作を続けますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        if self._close_affected_viewers_handler is None:
            return False
        closed = self._close_affected_viewers_handler(viewers)
        if closed is False:
            self._show_temporary_status(
                "PDFの解放を待機中のためファイル操作を開始できません"
            )
            return False
        return True

    def _prompt_for_filename(
        self,
        title: str,
        label: str,
        initial_name: str,
    ) -> str | None:
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setInputMode(QInputDialog.InputMode.TextInput)
        dialog.setTextValue(initial_name)
        editor = dialog.findChild(QLineEdit)
        if editor is not None:
            stem_length = len(Path(initial_name).stem)
            QTimer.singleShot(
                0,
                lambda editor=editor, length=stem_length: editor.setSelection(0, length),
            )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        name = dialog.textValue()
        validation = validate_windows_filename(name)
        if not validation.valid:
            QMessageBox.warning(
                self,
                "名前を使用できません",
                validation.error_message or "名前が無効です",
            )
            return None
        return validation.normalized_name

    def _choose_destination(
        self,
        title: str,
        destination: str | Path | None,
    ) -> Path | None:
        if destination is not None:
            return self._absolute_browser_path(destination)
        selected = QFileDialog.getExistingDirectory(
            self,
            title,
            str(self.current_path or Path.home()),
        )
        return self._absolute_browser_path(selected) if selected else None

    def _populate_destination_menu(
        self,
        menu: QMenu,
        operation: FileOperationKind,
    ) -> None:
        menu.clear()
        recent_menu = menu.addMenu("最近使った移動先")
        recent = self.destination_history.entries()
        if not recent:
            empty = recent_menu.addAction("（履歴なし）")
            empty.setEnabled(False)
        for entry in recent:
            action = recent_menu.addAction(entry.display_label)
            action.setToolTip(entry.path)
            action.triggered.connect(
                lambda _checked=False, path=entry.path, kind=operation: (
                    self.copy_selected_to(path)
                    if kind is FileOperationKind.COPY
                    else self.move_selected_to(path)
                )
            )
        favorite_menu = menu.addMenu("お気に入り")
        favorite_entries = tuple(getattr(self.folder_bookmark_model, "entries", ()))
        if not favorite_entries:
            empty = favorite_menu.addAction("（お気に入りなし）")
            empty.setEnabled(False)
        for entry in favorite_entries:
            action = favorite_menu.addAction(entry.display_name)
            action.setToolTip(entry.path)
            action.triggered.connect(
                lambda _checked=False, path=entry.path, kind=operation: (
                    self.copy_selected_to(path)
                    if kind is FileOperationKind.COPY
                    else self.move_selected_to(path)
                )
            )
        menu.addSeparator()
        specified = menu.addAction("指定先...")
        specified.triggered.connect(
            lambda _checked=False, kind=operation: (
                self.copy_selected_to()
                if kind is FileOperationKind.COPY
                else self.move_selected_to()
            )
        )

    def _set_file_clipboard(self, paths: tuple[str, ...], *, cut: bool) -> None:
        snapshot = self._internal_clipboard_state.replace(
            paths,
            (
                InternalClipboardOperation.CUT
                if cut
                else InternalClipboardOperation.COPY
            ),
        )
        self._clipboard_paths = snapshot.paths
        self._clipboard_cut = snapshot.is_cut
        self.item_model.set_cut_paths(snapshot.paths if snapshot.is_cut else ())
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path) for path in snapshot.paths])
        self._internal_clipboard_state.write_marker(mime)
        self._setting_clipboard = True
        try:
            QApplication.clipboard().setMimeData(mime)
        finally:
            self._setting_clipboard = False
        self._update_file_action_states()

    def _clipboard_file_urls(self) -> tuple[str, ...]:
        mime = QApplication.clipboard().mimeData()
        if mime is None or not mime.hasUrls():
            return ()
        paths = [
            url.toLocalFile()
            for url in mime.urls()
            if (
                url.isLocalFile()
                and url.toLocalFile()
                and not FileOperationArtifactPolicy.is_internal_operation_artifact(
                    url.toLocalFile()
                )
            )
        ]
        return tuple(paths)

    def _on_system_clipboard_changed(
        self,
        mode: QClipboard.Mode | None = None,
    ) -> None:
        if mode is not None and mode != QClipboard.Mode.Clipboard:
            return
        mime = QApplication.clipboard().mimeData()
        if self._internal_clipboard_state.matches_mime(mime):
            return
        if not self._setting_clipboard:
            self.clear_file_clipboard()

    def _on_file_operation_started(self, request: FileOperationRequest) -> None:
        if self._shutdown_prepared:
            return
        self._active_file_operation_id = request.request_id
        self.cancel_operation_button.setVisible(
            self.file_operation_coordinator.queue is None
        )
        self.cancel_operation_button.setEnabled(True)
        self._update_file_action_states()
        self.statusBar().showMessage(
            f"{self._operation_label(request.operation)}中… 0 / "
            f"{max(1, len(request.source_paths))}"
        )

    def _on_file_operation_progress(
        self,
        progress: FileOperationProgress,
    ) -> None:
        if (
            self._shutdown_prepared
            or progress.request_id != self._active_file_operation_id
        ):
            return
        self.statusBar().showMessage(
            f"{self._operation_label(progress.operation)}中… "
            f"{progress.completed} / {progress.total}"
        )

    def _on_file_operation_completed(
        self,
        result: FileOperationResult,
    ) -> None:
        request = self._file_operation_requests.pop(result.request_id, None)
        before_paths, before_row = self._file_operation_selection_before.pop(
            result.request_id,
            ((), None),
        )
        if (
            self._shutdown_prepared
            or request is None
            or result.request_id != self._active_file_operation_id
        ):
            return
        self._active_file_operation_id = None
        self.cancel_operation_button.setEnabled(False)
        self.cancel_operation_button.setVisible(False)
        self._update_file_action_states()

        if result.operation in {FileOperationKind.RENAME, FileOperationKind.MOVE}:
            relocation_items = tuple(
                item
                for item in result.items
                if item.success and item.child_results
            ) or result.effective_items
            for item in relocation_items:
                if item.source_path and item.destination_path:
                    if item.success:
                        self.navigation_history.relocate_tree(
                            item.source_path,
                            item.destination_path,
                        )

        if result.operation is FileOperationKind.MOVE and self._clipboard_cut:
            top_level = {
                self._path_key(item.source_path): item
                for item in result.items
                if item.source_path
            }
            remaining_list: list[str] = []
            for path in self._clipboard_paths:
                item = top_level.get(self._path_key(path))
                if item is None:
                    remaining_list.append(path)
                elif item.success:
                    continue
                elif item.child_results and item.retry_source_paths:
                    remaining_list.extend(item.retry_source_paths)
                else:
                    remaining_list.append(path)
            remaining = tuple(dict.fromkeys(remaining_list))
            if remaining:
                self._set_file_clipboard(remaining, cut=True)
            else:
                self.clear_file_clipboard()

        if _FILE_OPERATION_LOG.isEnabledFor(logging.DEBUG):
            for item in result.effective_items:
                _FILE_OPERATION_LOG.debug(
                    "operation result request=%s kind=%s source=%s destination=%s "
                    "published=%s removed=%s source_exists_after=%s "
                    "destination_exists_after=%s state=%s",
                    result.request_id,
                    result.operation.value,
                    item.source_path,
                    item.destination_path,
                    item.destination_published,
                    item.source_removed,
                    item.source_exists_after,
                    item.destination_exists_after,
                    item.state.value,
                )

        effective_items = result.effective_items
        success_count = sum(item.success for item in effective_items)
        failure_count = sum(not item.success for item in effective_items)
        root_cleanup_failures = sum(
            bool(
                item.child_results
                and not item.success
                and item.source_root_removed is False
                and all(child.success for child in item.leaf_results())
            )
            for item in result.items
        )
        failure_count += root_cleanup_failures
        if result.cancelled:
            completion_message = (
                f"{self._operation_label(result.operation)}をキャンセルしました"
            )
        elif failure_count:
            skipped_count = sum(
                item.state is FileOperationItemState.SKIPPED
                for item in effective_items
                if not item.success
            )
            source_remaining_count = sum(
                item.state
                in {
                    FileOperationItemState.COPIED_SOURCE_REMAINS,
                    FileOperationItemState.SOURCE_REMOVAL_FAILED,
                    FileOperationItemState.DESTINATION_PUBLISHED_SOURCE_REMAINS,
                }
                for item in effective_items
                if not item.success
            )
            completion_message = (
                f"{self._operation_label(result.operation)}完了: "
                f"成功{success_count}件、スキップ{skipped_count}件、"
                f"失敗{max(0, failure_count - skipped_count)}件"
            )
            if source_remaining_count:
                completion_message += f"（元項目残留{source_remaining_count}件）"
            codes = sorted(
                {
                    item.error_code or "unknown"
                    for item in effective_items
                    if not item.success
                }
            )
            if self.file_operation_coordinator.queue is None:
                QMessageBox.warning(
                    self,
                    "ファイル操作の一部を完了できませんでした",
                    f"成功: {success_count}件\n失敗: {failure_count}件\n"
                    f"エラー種別: {', '.join(codes)}\n"
                    "同名項目は上書きせずスキップします。",
                )
        else:
            completion_message = (
                f"{self._operation_label(result.operation)}が完了しました"
            )

        if (
            request.destination_directory
            and result.operation in {FileOperationKind.COPY, FileOperationKind.MOVE}
            and any(item.destination_published for item in effective_items)
        ):
            self.destination_history.record(request.destination_directory)

        if self.current_path is None:
            self._show_temporary_status(completion_message)
            return

        current_key = self._path_key(self.current_path)
        top_level_items = result.items
        successful_sources = {
            self._path_key(item.source_path)
            for item in (*top_level_items, *effective_items)
            if item.success and item.source_path
        }
        destination_paths = tuple(
            item.destination_path
            for item in (*top_level_items, *effective_items)
            if item.destination_published
            and item.destination_path
            and self._path_key(Path(item.destination_path).parent) == current_key
        )
        source_is_current = any(
            item.source_path
            and self._path_key(Path(item.source_path).parent) == current_key
            for item in (*top_level_items, *effective_items)
        )
        destination_is_current = bool(destination_paths)
        should_refresh = (
            result.operation
            in {
                FileOperationKind.RENAME,
                FileOperationKind.RECYCLE,
                FileOperationKind.CREATE_DIRECTORY,
            }
            and (source_is_current or destination_is_current)
        ) or (
            result.operation is FileOperationKind.MOVE
            and (source_is_current or destination_is_current)
        ) or (
            result.operation is FileOperationKind.COPY
            and destination_is_current
        )
        if not should_refresh:
            self._show_temporary_status(completion_message)
            return

        unaffected = tuple(
            path
            for path in before_paths
            if self._path_key(path) not in successful_sources
        )
        self._operation_restore_paths = destination_paths or unaffected
        self._operation_restore_row = (
            before_row
            if result.operation is FileOperationKind.RECYCLE
            and not self._operation_restore_paths
            else None
        )
        self._operation_completion_message = completion_message
        if self.refresh_current_folder() and self._pending_scan is not None:
            self._operation_refresh_generation = self._pending_scan.generation
        else:
            self._show_temporary_status(completion_message)

    def _restore_file_operation_selection(self) -> None:
        selection_model = self.list_view.selectionModel()
        selection_model.clearSelection()
        current_index = QModelIndex()
        for path in self._operation_restore_paths:
            row = self.item_model.row_for_path(path)
            if row < 0:
                continue
            index = self.item_model.index(row, 0)
            selection_model.select(
                index,
                QItemSelectionModel.SelectionFlag.Select,
            )
            if not current_index.isValid():
                current_index = index
        if not current_index.isValid() and self._operation_restore_row is not None:
            count = self.item_model.rowCount()
            if count:
                row = min(max(0, self._operation_restore_row), count - 1)
                current_index = self.item_model.index(row, 0)
                selection_model.select(
                    current_index,
                    QItemSelectionModel.SelectionFlag.Select,
                )
        if current_index.isValid():
            self.list_view.setCurrentIndex(current_index)
            self.list_view.scrollTo(current_index)
        else:
            self.list_view.setCurrentIndex(QModelIndex())
        self._operation_restore_paths = ()
        self._operation_restore_row = None
        message = self._operation_completion_message
        self._operation_completion_message = None
        if message:
            self._show_temporary_status(message)

    @staticmethod
    def _operation_label(operation: FileOperationKind) -> str:
        return {
            FileOperationKind.RENAME: "名前変更",
            FileOperationKind.COPY: "コピー",
            FileOperationKind.MOVE: "移動",
            FileOperationKind.RECYCLE: "ごみ箱への移動",
            FileOperationKind.CREATE_DIRECTORY: "フォルダ作成",
        }[operation]

    def add_browser_bookmark(
        self,
        path: str | Path,
        *,
        item_type: str,
        label: str | None = None,
    ) -> None:
        if self.metadata_store is None:
            return
        self.metadata_store.add_browser_bookmark(
            str(path),
            item_type=item_type,
            label=label,
        )

    def remove_browser_bookmark(self, path: str | Path) -> None:
        if self.metadata_store is not None:
            self.metadata_store.remove_browser_bookmark(str(path))

    def add_current_folder_bookmark(self) -> None:
        if self.current_path is None or self.metadata_store is None:
            return
        if self.metadata_store.add_folder_bookmark(str(self.current_path)):
            self._show_temporary_status("現在のフォルダをお気に入りへ追加しました")
        else:
            self._show_temporary_status("このフォルダは登録済みです")

    def add_selected_folder_bookmark(self) -> bool:
        item = self.item_model.item_at(self.list_view.currentIndex())
        if (
            item is None
            or item.kind is not BrowserItemKind.FOLDER
            or self.metadata_store is None
        ):
            return False
        added = self.metadata_store.add_folder_bookmark(
            str(item.path),
            label=item.display_name,
        )
        self._show_temporary_status(
            "フォルダをお気に入りへ追加しました"
            if added
            else "このフォルダは登録済みです"
        )
        return added

    def toggle_current_folder_bookmark(self) -> None:
        if self.current_path is None or self.metadata_store is None:
            return
        if any(
            self._same_path(Path(entry.path), self.current_path)
            for entry in self.metadata_store.list_folder_bookmarks()
        ):
            self.metadata_store.remove_folder_bookmark(str(self.current_path))
            self._show_temporary_status("現在のフォルダをお気に入りから削除しました")
        else:
            self.add_current_folder_bookmark()

    def open_folder_bookmark(self, index: QModelIndex) -> None:
        entry = self.folder_bookmark_model.entry_at(index)
        if entry is None:
            return
        self.navigate_to(
            entry.path,
            navigation_source="favorite",
            trace_id=self._favorite_trace_id,
        )

    def _on_favorite_pressed(self, path: str) -> None:
        self._favorite_release_navigated = False
        self._favorite_trace_id = performance_trace.begin(
            "favorite.mouse_press",
            path,
        )

    def _on_favorite_release_confirmed(self, path: str) -> None:
        trace_id = self._favorite_trace_id
        if trace_id:
            performance_trace.mark(trace_id, "favorite.mouse_release", path)

    def _on_favorite_clicked(self, index: QModelIndex) -> None:
        if (
            self.favorite_view.selection_controller.press_modifiers
            != Qt.KeyboardModifier.NoModifier
        ):
            return
        if (
            self.favorite_view.drag_started
            or self.favorite_view.drop_in_progress
            or not index.isValid()
        ):
            return
        entry = self.folder_bookmark_model.entry_at(index)
        if entry is None:
            return
        trace_id = self._favorite_trace_id
        if trace_id:
            performance_trace.mark(
                trace_id,
                "favorite.navigation.confirmed",
                entry.path,
            )
        self._favorite_release_navigated = True
        self.open_folder_bookmark(index)
        QTimer.singleShot(
            0,
            lambda: setattr(self, "_favorite_release_navigated", False),
        )

    def _on_favorite_double_clicked(self, index: QModelIndex) -> None:
        # The first release already committed the navigation. Delaying the
        # single click to distinguish this signal would add the platform
        # double-click interval to every favorite navigation.
        if not self._favorite_release_navigated:
            self._favorite_release_navigated = True
            self.open_folder_bookmark(index)

    def _open_pending_favorite_click(self) -> None:
        """Compatibility hook: favorite clicks are now release-confirmed."""

    def rename_folder_bookmark(
        self,
        index: QModelIndex,
        label: str | None = None,
    ) -> bool:
        entry = self.folder_bookmark_model.entry_at(index)
        if entry is None or self.metadata_store is None:
            return False
        if label is None:
            label, accepted = QInputDialog.getText(
                self,
                "お気に入りの表示名",
                "表示名:",
                text=entry.label,
            )
            if not accepted:
                return False
        return self.metadata_store.rename_bookmark_label(entry.path, label)

    def move_folder_bookmark(self, index: QModelIndex, offset: int) -> bool:
        entry = self.folder_bookmark_model.entry_at(index)
        if entry is None or self.metadata_store is None:
            return False
        paths = [candidate.path for candidate in self.folder_bookmark_model.entries]
        old_row = index.row()
        new_row = max(0, min(len(paths) - 1, old_row + int(offset)))
        if new_row == old_row:
            return False
        paths.insert(new_row, paths.pop(old_row))
        changed = self.metadata_store.reorder_folder_bookmarks(paths)
        if changed:
            QTimer.singleShot(
                0,
                lambda path=entry.path: self._select_folder_bookmark_path(path),
            )
        return changed

    def _select_folder_bookmark_path(self, path: str) -> None:
        row = self.folder_bookmark_model.row_for_path(path)
        if row >= 0:
            self.favorite_view.setCurrentIndex(
                self.folder_bookmark_model.index(row, 0)
            )

    def open_bookmark(
        self,
        index: QModelIndex,
        *,
        open_in_new_window: bool = False,
    ) -> None:
        entry = self.bookmark_model.entry_at(index)
        if entry is None:
            return
        availability = self.bookmark_model.data(
            index,
            self.bookmark_model.AvailabilityRole,
        )
        if availability == "missing":
            self.statusBar().showMessage("ブックマーク先が見つかりません", 3000)
            return
        if entry.item_type == "folder":
            self.navigate_to(entry.path)
            return
        if self._open_path_handler is not None:
            self._invoke_open_path_handler(entry.path, open_in_new_window)

    def open_history(
        self,
        index: QModelIndex,
        *,
        open_in_new_window: bool = False,
    ) -> None:
        entry = self.history_model.entry_at(index)
        if entry is None:
            return
        availability = self.history_model.data(
            index,
            self.history_model.AvailabilityRole,
        )
        if availability == "missing":
            self.statusBar().showMessage("履歴の項目が見つかりません", 3000)
            return
        if self._open_path_handler is not None:
            self._invoke_open_path_handler(entry.path, open_in_new_window)

    def remove_history_entry(self, index: QModelIndex) -> None:
        entry = self.history_model.entry_at(index)
        if entry is not None and self.metadata_store is not None:
            self.metadata_store.remove_history(entry.path)

    def clear_history(self, *, confirm: bool = True) -> bool:
        if self.metadata_store is None:
            return False
        if confirm:
            answer = QMessageBox.question(
                self,
                "閲覧履歴を消去",
                "閲覧履歴をすべて消去しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self.metadata_store.clear_history()
        return True

    def set_sidebar_visible(self, visible: bool) -> None:
        if not visible and self.sidebar.isVisible():
            sizes = self.splitter.sizes()
            if sizes and sizes[0] > 0:
                self._sidebar_width = sizes[0]
        self.sidebar.setVisible(visible)
        if visible:
            total = max(self.splitter.width(), self.width(), self._sidebar_width + 1)
            self.splitter.setSizes([self._sidebar_width, max(1, total - self._sidebar_width)])
        self.sidebar_action.blockSignals(True)
        self.sidebar_action.setChecked(visible)
        self.sidebar_action.blockSignals(False)
        self.config.set("browser_sidebar_visible", visible)
        self.config.set("browser_sidebar_width", self._sidebar_width)

    def _apply_sidebar_layout(self) -> None:
        selected = self.folder_bookmark_model.entry_at(
            self.favorite_view.currentIndex()
        )
        selected_path = selected.path if selected is not None else None
        self.sidebar_layout_controller.apply(
            self.browser_sidebar_layout,
            splitter_sizes=self.browser_sidebar_splitter_sizes,
            show_favorites=self.browser_show_favorites,
            show_tree=self.browser_show_folder_tree,
            show_history=self.browser_show_history,
        )
        if selected_path:
            row = self.folder_bookmark_model.row_for_path(selected_path)
            if row >= 0:
                self.favorite_view.setCurrentIndex(
                    self.folder_bookmark_model.index(row, 0)
                )

    def _on_sidebar_splitter_sizes_changed(self, sizes: object) -> None:
        if not isinstance(sizes, list) or len(sizes) < 2:
            return
        self.browser_sidebar_splitter_sizes = [
            max(40, min(4000, int(value)))
            for value in sizes[:2]
        ]
        self.config.set(
            "browser_sidebar_splitter_sizes",
            list(self.browser_sidebar_splitter_sizes),
        )

    def set_sidebar_layout(self, layout_name: str) -> None:
        self.config.apply(
            {"browser_sidebar_layout": str(layout_name)},
            save=True,
        )

    def set_sidebar_component_visible(self, component: str, visible: bool) -> None:
        key = {
            "favorites": "browser_show_favorites",
            "tree": "browser_show_folder_tree",
            "history": "browser_show_history",
        }.get(component)
        if key is not None:
            self.config.apply({key: bool(visible)}, save=True)

    def set_folder_tree_sync_mode(self, mode: str) -> None:
        self.config.apply({"folder_tree_sync_mode": str(mode)}, save=True)

    def set_folder_tree_collapse_unrelated(self, enabled: bool) -> None:
        self.config.apply(
            {"folder_tree_collapse_unrelated": bool(enabled)},
            save=True,
        )

    def _sync_sidebar_actions(self) -> None:
        if not hasattr(self, "favorites_visible_action"):
            return
        pairs = (
            (self.favorites_visible_action, self.browser_show_favorites),
            (self.folder_tree_visible_action, self.browser_show_folder_tree),
            (self.history_visible_action, self.browser_show_history),
        )
        for action, checked in pairs:
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)
        for name, action in self.sidebar_layout_actions.items():
            action.setChecked(name == self.browser_sidebar_layout)
        for name, action in self.folder_tree_sync_actions.items():
            action.setChecked(name == self.folder_tree_sync_mode)
        self.collapse_auto_tree_action.setChecked(
            self.folder_tree_collapse_unrelated
        )

    def prepare_shutdown(self) -> None:
        if self._shutdown_prepared:
            return
        self._shutdown_prepared = True
        self.clear_file_clipboard()
        if (
            self._owns_file_operation_coordinator
            and self._active_file_operation_id is not None
        ):
            self.file_operation_coordinator.cancel()
        if self._owns_file_operation_coordinator:
            self.file_operation_coordinator.close()
        self.browser_main_drop.close()
        self._pending_browser_focus = None
        self._cancel_pending_scan(rollback_history=False)
        self.scanner.close()
        self._thumbnail_request_timer.stop()
        self._scroll_idle_timer.stop()
        self._scan_status_timer.stop()
        self._scan_batch_timer.stop()
        self._save_window_state()
        self.thumbnail_provider.close()
        if self._owns_archive_backend_registry:
            self.archive_backend_registry.close()
        if self._owns_pdfium_service:
            if not self.pdfium_service.shutdown():
                raise RuntimeError(
                    self.pdfium_service.last_shutdown_error
                    or "PDFium shutdown did not complete."
                )
        if self._owns_path_availability_service:
            self.path_availability_service.close()

    def _run_idle_cache_cleanup(self) -> None:
        if not self._shutdown_prepared:
            self.thumbnail_provider.cleanup_caches_async(force=False)

    def apply_settings(self, changed: dict[str, object]) -> None:
        if "browser_folder_gestures_enabled" in changed:
            self.browser_folder_gestures_enabled = bool(
                changed["browser_folder_gestures_enabled"]
            )
        if "mouse_gesture_show_trail" in changed:
            self.mouse_gesture_show_trail = bool(
                changed["mouse_gesture_show_trail"]
            )
        if "mouse_gesture_min_distance" in changed:
            self.mouse_gesture_min_distance = max(
                12,
                min(200, int(changed["mouse_gesture_min_distance"])),
            )
        if {
            "browser_folder_gestures_enabled",
            "mouse_gesture_show_trail",
            "mouse_gesture_min_distance",
        }.intersection(changed):
            self.list_view.set_folder_gesture_options(
                enabled=self.browser_folder_gestures_enabled,
                show_trail=self.mouse_gesture_show_trail,
                min_distance=self.mouse_gesture_min_distance,
            )
        list_keys = {
            "thumbnail_size",
            "thumbnail_frame_ratio",
            "thumbnail_crop_mode",
            "browser_thumbnail_display_mode",
            "thumbnail_quality_mode",
            "thumbnail_cache_max_edge",
            "browser_sort_key",
            "browser_sort_order",
            "browser_folders_first",
            "browser_display_density",
            "browser_item_spacing_mode",
            "browser_item_spacing",
            "browser_cell_padding",
            "browser_filename_display",
            "browser_filename_gap",
            "browser_filename_padding_y",
        }
        list_changed = bool(list_keys.intersection(changed))
        view_state = self._capture_list_view_state() if list_changed else None

        if "browser_sort_key" in changed:
            self.browser_sort_key = normalize_browser_sort_key(
                changed["browser_sort_key"]
            )
        if "browser_sort_order" in changed:
            self.browser_sort_order = normalize_browser_sort_order(
                changed["browser_sort_order"]
            )
        if "browser_folders_first" in changed:
            self.browser_folders_first = bool(changed["browser_folders_first"])
        if "browser_display_density" in changed:
            self.browser_display_density = normalize_browser_display_density(
                changed["browser_display_density"]
            )
        if "browser_item_spacing_mode" in changed:
            self.browser_item_spacing_mode = str(
                changed["browser_item_spacing_mode"]
            )
        if "browser_item_spacing" in changed:
            self.browser_item_spacing = max(
                0, min(32, int(changed["browser_item_spacing"]))
            )
        if "browser_cell_padding" in changed:
            self.browser_cell_padding = max(
                0, min(12, int(changed["browser_cell_padding"]))
            )
        if "browser_filename_display" in changed:
            self.browser_filename_display = str(changed["browser_filename_display"])
        if "browser_filename_gap" in changed:
            self.browser_filename_gap = max(
                0, min(32, int(changed["browser_filename_gap"]))
            )
        if "browser_filename_padding_y" in changed:
            self.browser_filename_padding_y = max(
                0, min(16, int(changed["browser_filename_padding_y"]))
            )
        if {
            "browser_sort_key",
            "browser_sort_order",
            "browser_folders_first",
        }.intersection(changed):
            self.item_model.configure_sort(
                self.browser_sort_key,
                self.browser_sort_order,
                self.browser_folders_first,
            )

        thumbnail_changed = bool(
            {
                "thumbnail_size",
                "thumbnail_frame_ratio",
                "thumbnail_crop_mode",
                "browser_thumbnail_display_mode",
                "thumbnail_quality_mode",
                "thumbnail_cache_max_edge",
            }.intersection(changed)
        )
        if thumbnail_changed:
            new_size = self._safe_thumbnail_size(
                changed.get("thumbnail_size", self.thumbnail_size)
            )
            new_ratio = str(
                changed.get(
                    "thumbnail_frame_ratio",
                    self.thumbnail_frame_ratio,
                )
            )
            new_crop_mode = str(
                changed.get(
                    "thumbnail_crop_mode",
                    self.thumbnail_crop_mode,
                )
            )
            new_display_mode = str(
                changed.get(
                    "browser_thumbnail_display_mode",
                    self.browser_thumbnail_display_mode,
                )
            )
            new_quality_mode = str(
                changed.get(
                    "thumbnail_quality_mode",
                    self.thumbnail_quality_mode,
                )
            )
            new_max_edge = max(
                256,
                min(
                    2048,
                    int(
                        changed.get(
                            "thumbnail_cache_max_edge",
                            self.thumbnail_cache_max_edge,
                        )
                    ),
                ),
            )
            old_ratio = self.thumbnail_frame_ratio
            old_crop_mode = self.thumbnail_crop_mode
            old_display_mode = self.browser_thumbnail_display_mode
            old_quality_mode = self.thumbnail_quality_mode
            old_max_edge = self.thumbnail_cache_max_edge
            self.thumbnail_size = new_size
            self.thumbnail_frame_ratio = new_ratio
            self.thumbnail_crop_mode = new_crop_mode
            self.browser_thumbnail_display_mode = new_display_mode
            self.thumbnail_quality_mode = new_quality_mode
            self.thumbnail_cache_max_edge = new_max_edge
            new_spec = self._build_thumbnail_render_spec()
            bucket_changed = (
                new_spec.cache_token != self.thumbnail_render_spec.cache_token
            )
            self.thumbnail_render_spec = new_spec
            self.thumbnail_bucket_size = new_spec.long_edge
            if bucket_changed:
                policy_changed = (
                    old_ratio != new_ratio
                    or old_crop_mode != new_crop_mode
                    or old_display_mode != new_display_mode
                    or old_quality_mode != new_quality_mode
                    or old_max_edge != new_max_edge
                )
                if policy_changed and (old_ratio != new_ratio or old_crop_mode != new_crop_mode):
                    self.item_model.clear_thumbnails()
                self._generation = self.thumbnail_provider.begin_generation()
        if thumbnail_changed or {
            "browser_display_density",
            "browser_item_spacing_mode",
            "browser_item_spacing",
            "browser_cell_padding",
            "browser_filename_display",
            "browser_filename_gap",
            "browser_filename_padding_y",
        }.intersection(changed):
            self._apply_list_view_geometry()

        if list_changed:
            self._sync_browser_controls()
            if view_state is not None:
                self._schedule_list_view_state_restore(view_state)
            self._update_status()
            self._schedule_thumbnail_requests()
        sidebar_keys = {
            "browser_sidebar_layout",
            "browser_sidebar_splitter_sizes",
            "browser_show_favorites",
            "browser_show_folder_tree",
            "browser_show_history",
        }
        if sidebar_keys.intersection(changed):
            self.browser_sidebar_layout = str(
                changed.get(
                    "browser_sidebar_layout",
                    self.browser_sidebar_layout,
                )
            )
            raw_sizes = changed.get(
                "browser_sidebar_splitter_sizes",
                self.browser_sidebar_splitter_sizes,
            )
            if isinstance(raw_sizes, list) and len(raw_sizes) == 2:
                self.browser_sidebar_splitter_sizes = [
                    max(40, min(4000, int(value)))
                    for value in raw_sizes
                ]
            self.browser_show_favorites = bool(
                changed.get(
                    "browser_show_favorites",
                    self.browser_show_favorites,
                )
            )
            self.browser_show_folder_tree = bool(
                changed.get(
                    "browser_show_folder_tree",
                    self.browser_show_folder_tree,
                )
            )
            self.browser_show_history = bool(
                changed.get(
                    "browser_show_history",
                    self.browser_show_history,
                )
            )
            self._apply_sidebar_layout()
            self._sync_sidebar_actions()
        if "folder_tree_sync_mode" in changed:
            self.folder_tree_sync_mode = str(changed["folder_tree_sync_mode"])
        if "folder_tree_collapse_unrelated" in changed:
            self.folder_tree_collapse_unrelated = bool(
                changed["folder_tree_collapse_unrelated"]
            )
        if "folder_tree_focus_rebase" in changed:
            self.folder_tree_focus_rebase = bool(
                changed["folder_tree_focus_rebase"]
            )
        if "folder_tree_context_ancestor_levels" in changed:
            self.folder_tree_context_ancestor_levels = max(
                0,
                min(12, int(changed["folder_tree_context_ancestor_levels"])),
            )
        if {
            "folder_tree_sync_mode",
            "folder_tree_collapse_unrelated",
            "folder_tree_focus_rebase",
            "folder_tree_context_ancestor_levels",
        }.intersection(changed) and self.current_path is not None:
            self._sync_tree_to_path(self.current_path)
        favorite_metric_keys = {
            "favorite_row_padding_y",
            "favorite_row_spacing",
            "favorite_icon_size",
        }
        if favorite_metric_keys.intersection(changed):
            self.favorite_row_metrics = FavoriteRowMetrics.normalized(
                padding_y=int(
                    changed.get(
                        "favorite_row_padding_y",
                        self.favorite_row_metrics.padding_y,
                    )
                ),
                spacing=int(
                    changed.get(
                        "favorite_row_spacing",
                        self.favorite_row_metrics.spacing,
                    )
                ),
                icon_size=int(
                    changed.get(
                        "favorite_icon_size",
                        self.favorite_row_metrics.icon_size,
                    )
                ),
            )
            self._apply_favorite_row_metrics()
        visibility_changed = bool(
            {
                "browser_show_hidden_items",
                "browser_show_unsupported_files",
                "browser_show_system_items",
            }.intersection(changed)
        )
        if "browser_show_hidden_items" in changed:
            self.browser_show_hidden_items = bool(
                changed["browser_show_hidden_items"]
            )
        if "browser_show_unsupported_files" in changed:
            self.browser_show_unsupported_files = bool(
                changed["browser_show_unsupported_files"]
            )
        if "browser_show_system_items" in changed:
            self.browser_show_system_items = bool(
                changed["browser_show_system_items"]
            )
        if "browser_external_drop_behavior" in changed:
            self.browser_external_drop_behavior = str(
                changed["browser_external_drop_behavior"]
            )
        if visibility_changed and self.current_path is not None:
            self.refresh_current_folder()
        if "thumbnail_disk_cache_enabled" in changed:
            self.thumbnail_provider.set_disk_cache_enabled(
                bool(changed["thumbnail_disk_cache_enabled"])
            )
        if "thumbnail_cache_limit_mb" in changed:
            self.thumbnail_provider.set_disk_cache_limit_mb(
                int(changed["thumbnail_cache_limit_mb"])
            )
        if "thumbnail_cache_max_unused_days" in changed:
            self.thumbnail_provider.set_disk_cache_max_unused_days(
                int(changed["thumbnail_cache_max_unused_days"])
            )
        preview_setting_keys = {
            "text_preview_enabled",
            "video_thumbnail_enabled",
            "video_thumbnail_backend",
            "video_thumbnail_frame_mode",
            "video_thumbnail_shell_placeholder",
            "ffmpeg_executable",
        }
        if preview_setting_keys.intersection(changed):
            update_preview_settings = getattr(
                self.thumbnail_provider,
                "update_preview_settings",
                None,
            )
            if callable(update_preview_settings):
                self.item_model.clear_thumbnails()
                self._generation = int(
                    update_preview_settings(self.settings)
                )
                self._schedule_thumbnail_requests()
        if (
            {
                "archive_backend_preference",
                "winrar_executable",
                "seven_zip_executable",
            }.intersection(changed)
            and self._owns_archive_backend_registry
        ):
            self.archive_backend_registry.reset()
        if {
            "archive_backend_preference",
            "winrar_executable",
            "seven_zip_executable",
        }.intersection(changed):
            self.thumbnail_provider.clear_memory_cache()
            self.item_model.clear_thumbnails()
            self._generation = self.thumbnail_provider.begin_generation()
            self._schedule_thumbnail_requests()

    def _apply_browser_controls(self, *_args: object) -> None:
        self.config.apply(
            {
                "browser_sort_key": str(
                    self.browser_sort_key_combo.currentData()
                ),
                "browser_sort_order": str(
                    self.browser_sort_order_combo.currentData()
                ),
                "browser_folders_first": (
                    self.browser_folders_first_checkbox.isChecked()
                ),
                "browser_display_density": str(
                    self.browser_display_density_combo.currentData()
                ),
            },
            save=True,
        )

    def _sync_browser_controls(self) -> None:
        controls = (
            self.browser_sort_key_combo,
            self.browser_sort_order_combo,
            self.browser_folders_first_checkbox,
            self.browser_display_density_combo,
        )
        for control in controls:
            control.blockSignals(True)
        try:
            self._select_combo_data(
                self.browser_sort_key_combo,
                self.browser_sort_key.value,
            )
            self._select_combo_data(
                self.browser_sort_order_combo,
                self.browser_sort_order.value,
            )
            self.browser_folders_first_checkbox.setChecked(
                self.browser_folders_first
            )
            self._select_combo_data(
                self.browser_display_density_combo,
                self.browser_display_density.value,
            )
        finally:
            for control in controls:
                control.blockSignals(False)

    def _apply_list_view_geometry(self) -> None:
        self.item_delegate.configure(
            thumbnail_size=self.thumbnail_size,
            density=self.browser_display_density,
            frame_ratio_id=self.thumbnail_frame_ratio,
            thumbnail_display_mode=self.browser_thumbnail_display_mode,
            cell_padding=self.browser_cell_padding,
            filename_display=self.browser_filename_display,
            filename_gap=self.browser_filename_gap,
            filename_padding_y=self.browser_filename_padding_y,
            item_spacing=(
                self.item_delegate.profile.spacing
                if self.browser_item_spacing_mode == "preset"
                else self.browser_item_spacing
            ),
        )
        self.list_view.setIconSize(QSize(self.thumbnail_size, self.thumbnail_size))
        self.list_view.setGridSize(self.item_delegate.grid_metrics.grid_size)
        self.list_view.setSpacing(self.item_delegate.grid_metrics.item_spacing)
        self.list_view.setWordWrap(
            self.item_delegate.grid_metrics.title_lines > 1
        )
        self.list_view.viewport().update()

    def _on_list_current_changed(
        self,
        current: QModelIndex,
        previous: QModelIndex,
    ) -> None:
        self._update_index_rect(previous)
        self._update_index_rect(current)
        self._update_status()

    def _on_list_selection_changed(self, selected, deselected) -> None:
        for index in tuple(selected.indexes()) + tuple(deselected.indexes()):
            self._update_index_rect(index)
        self._update_status()
        self._update_file_action_states()

    def _on_list_hovered(self, index: QModelIndex) -> None:
        old_path = self._hovered_list_path
        item = self.item_model.item_at(index)
        new_path = str(item.path) if item is not None else None
        if old_path == new_path:
            return
        if old_path:
            old_row = self.item_model.row_for_path(old_path)
            if old_row >= 0:
                self._update_index_rect(self.item_model.index(old_row, 0))
        self._hovered_list_path = new_path
        self._update_index_rect(index)

    def _update_index_rect(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        rect = self.list_view.visualRect(index)
        if rect.isValid():
            self.list_view.viewport().update(rect)

    def _update_list_viewport(self, *_args: object) -> None:
        self._hovered_list_path = None
        self.list_view.viewport().update()

    def open_settings_dialog(self) -> None:
        shutdown_guard = getattr(self, "_settings_dialog_open_guard", None)
        if self._shutdown_prepared or (
            callable(shutdown_guard) and not shutdown_guard()
        ):
            return
        dialog = SettingsDialog(
            self.config,
            self,
            cache_usage_getter=self.thumbnail_provider.disk_cache_usage_bytes,
            cache_statistics_getter=self.thumbnail_provider.cache_statistics,
            seven_zip_locator=(
                self.archive_backend_registry.locator
                if self.archive_backend_registry is not None
                else None
            ),
            winrar_locator=(
                self.archive_backend_registry.winrar_locator
                if self.archive_backend_registry is not None
                else None
            ),
            file_registration_service=self.file_registration_service,
        )
        dialog.cache_clear_requested.connect(
            self.thumbnail_provider.clear_all_caches_async
        )
        dialog.cache_cleanup_requested.connect(
            lambda: self.thumbnail_provider.cleanup_caches_async(force=True)
        )
        self.thumbnail_provider.cache_cleared.connect(dialog.refresh_cache_usage)
        dialog.exec()

    def event(self, event: QEvent) -> bool:  # type: ignore[override]
        handled = super().event(event)
        if event.type() == QEvent.Type.WindowActivate:
            self.activated.emit(self)
        elif event.type() in {
            QEvent.Type.Show,
            QEvent.Type.ScreenChangeInternal,
        }:
            QTimer.singleShot(0, self._install_screen_tracking)
            QTimer.singleShot(0, self._reevaluate_thumbnail_dpr)
        return handled

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # type: ignore[override]
        if watched is self.list_view.viewport():
            event_type = event.type()
            if event_type == QEvent.Type.DragEnter:
                self.list_view.dragEnterEvent(event)
                return True
            if event_type == QEvent.Type.DragMove:
                self.list_view.dragMoveEvent(event)
                return True
            if event_type == QEvent.Type.DragLeave:
                self.list_view.dragLeaveEvent(event)
                return True
            if event_type == QEvent.Type.Drop:
                self.list_view.dropEvent(event)
                return True
        if watched in (self.list_view, self.list_view.viewport()):
            if event.type() in {
                QEvent.Type.FocusIn,
                QEvent.Type.FocusOut,
            }:
                self._update_index_rect(self.list_view.currentIndex())
            elif event.type() == QEvent.Type.Leave:
                old_path = self._hovered_list_path
                self._hovered_list_path = None
                if old_path:
                    row = self.item_model.row_for_path(old_path)
                    if row >= 0:
                        self._update_index_rect(self.item_model.index(row, 0))
        if watched is self.address_bar and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if isinstance(key_event, QKeyEvent) and key_event.key() == Qt.Key.Key_Escape:
                self._sync_address_bar()
                self.address_bar.selectAll()
                return True
        if (
            watched is self.address_bar
            and event.type()
            in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
            )
            and isinstance(event, QMouseEvent)
            and event.button()
            in (
                Qt.MouseButton.BackButton,
                Qt.MouseButton.ForwardButton,
            )
        ):
            return True

        if watched is not self.address_bar and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
        ):
            mouse_event = event
            if isinstance(mouse_event, QMouseEvent):
                button = mouse_event.button()
                if button in (
                    Qt.MouseButton.BackButton,
                    Qt.MouseButton.ForwardButton,
                ):
                    if event.type() == QEvent.Type.MouseButtonPress:
                        if button not in self._pressed_extra_buttons:
                            self._pressed_extra_buttons.add(button)
                            if button == Qt.MouseButton.BackButton:
                                self.go_back()
                            else:
                                self.go_forward()
                    else:
                        self._pressed_extra_buttons.discard(button)
                    return True

        if (
            watched is not self.address_bar
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Escape
            and watched in (self.list_view, self.list_view.viewport())
            and bool(self._clipboard_paths)
        ):
            self.clear_file_clipboard()
            self._show_temporary_status("切り取り／コピー候補を解除しました")
            return True
        if (
            watched is not self.address_bar
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Backspace
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            self.go_back()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if (
            event.key() == Qt.Key.Key_Backspace
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and not self.address_bar.hasFocus()
        ):
            self.go_back()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        guard = getattr(self, "_application_close_guard", None)
        if callable(guard) and not guard(self):
            event.ignore()
            return
        self.prepare_shutdown()
        self.closing.emit(self)
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._schedule_thumbnail_requests()

    def _build_ui(self) -> None:
        self.file_system_model = QFileSystemModel(self)
        self.file_system_model.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives
        )
        self.file_system_model.setRootPath("")

        self.folder_tree = PathDropTreeView(self)
        self.folder_tree.setModel(self.file_system_model)
        self.folder_tree.setRootIndex(QModelIndex())
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.folder_tree.customContextMenuRequested.connect(
            self._show_folder_tree_context_menu
        )
        self.folder_tree.paths_dropped.connect(self._on_tree_paths_dropped)
        for column in range(1, self.file_system_model.columnCount()):
            self.folder_tree.hideColumn(column)
        self.folder_tree.navigationConfirmed.connect(
            self._on_tree_navigation_confirmed
        )
        self.folder_tree_sync = FolderTreeSyncController(
            self.folder_tree,
            self.file_system_model,
            self,
        )
        self.folder_tree_sync.sync_finished.connect(
            self._on_folder_tree_sync_finished
        )

        self.bookmark_model = BookmarkModel(
            self.metadata_store,
            self,
            availability_service=self.path_availability_service,
        )
        self.bookmark_view = QListView(self)
        self.bookmark_view.setModel(self.bookmark_model)
        self.bookmark_view.activated.connect(self.open_bookmark)
        self.bookmark_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.bookmark_view.customContextMenuRequested.connect(
            self._show_bookmark_context_menu
        )
        self.folder_bookmark_model = FolderBookmarkModel(
            self.metadata_store,
            self,
            availability_service=self.path_availability_service,
        )
        self.favorite_view = ExplorerListView(self)
        self.favorite_view.setObjectName("folder_favorite_view")
        self.favorite_view.setModel(self.folder_bookmark_model)
        self.favorite_view.setItemDelegate(
            FavoriteItemDelegate(
                self.favorite_view,
                metrics=self.favorite_row_metrics,
            )
        )
        self.favorite_view.setIconSize(
            QSize(
                self.favorite_row_metrics.icon_size,
                self.favorite_row_metrics.icon_size,
            )
        )
        self.favorite_view.setSpacing(self.favorite_row_metrics.spacing)
        self.favorite_view.setUniformItemSizes(True)
        self.favorite_view.setWordWrap(False)
        self.favorite_view.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.favorite_view.clicked.connect(self._on_favorite_clicked)
        self.favorite_view.doubleClicked.connect(self._on_favorite_double_clicked)
        self.favorite_view.itemPressCaptured.connect(self._on_favorite_pressed)
        self.favorite_view.itemReleaseConfirmed.connect(
            self._on_favorite_release_confirmed
        )
        self.favorite_view.enterActivated.connect(self.open_folder_bookmark)
        self.favorite_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.favorite_view.customContextMenuRequested.connect(
            self._show_folder_bookmark_context_menu
        )
        self.favorite_view.paths_dropped.connect(self._on_favorite_paths_dropped)

        self.history_model = HistoryModel(
            self.metadata_store,
            self,
            availability_service=self.path_availability_service,
        )
        self.history_view = QListView(self)
        self.history_view.setModel(self.history_model)
        self.history_view.activated.connect(self.open_history)
        self.history_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.history_view.customContextMenuRequested.connect(
            self._show_history_context_menu
        )
        self.sidebar = QWidget(self)
        self.sidebar.setObjectName("browser_sidebar")
        self.sidebar_layout_controller = SidebarLayoutController(
            self.sidebar,
            favorites_view=self.favorite_view,
            folder_tree=self.folder_tree,
            history_view=self.history_view,
            bookmarks_view=self.bookmark_view,
        )
        self.sidebar_layout_controller.splitter_sizes_changed.connect(
            self._on_sidebar_splitter_sizes_changed
        )
        self._apply_sidebar_layout()

        self.item_model = BrowserItemModel(self)
        self.item_model.configure_sort(
            self.browser_sort_key,
            self.browser_sort_order,
            self.browser_folders_first,
        )
        style = self.style()
        self.item_model.set_fallback_icons(
            {
                BrowserItemKind.FOLDER: style.standardIcon(QStyle.StandardPixmap.SP_DirIcon),
                BrowserItemKind.ARCHIVE: style.standardIcon(
                    QStyle.StandardPixmap.SP_DialogOpenButton
                ),
                BrowserItemKind.IMAGE: style.standardIcon(QStyle.StandardPixmap.SP_FileIcon),
                BrowserItemKind.PDF: style.standardIcon(QStyle.StandardPixmap.SP_FileIcon),
                BrowserItemKind.OTHER: style.standardIcon(QStyle.StandardPixmap.SP_FileIcon),
            }
        )

        self.list_view = ExplorerListView(self)
        self.list_view.setModel(self.item_model)
        self.list_view.set_folder_gesture_options(
            enabled=self.browser_folder_gestures_enabled,
            show_trail=self.mouse_gesture_show_trail,
            min_distance=self.mouse_gesture_min_distance,
        )
        self.list_view.folderGestureRecognized.connect(
            self._on_browser_folder_gesture
        )
        self.list_view.paintCompleted.connect(self._on_list_paint_completed)
        self.item_delegate = BrowserItemDelegate(
            self.list_view,
            thumbnail_size=self.thumbnail_size,
            density=self.browser_display_density,
            frame_ratio_id=self.thumbnail_frame_ratio,
            thumbnail_display_mode=self.browser_thumbnail_display_mode,
            cell_padding=self.browser_cell_padding,
            filename_display=self.browser_filename_display,
            filename_gap=self.browser_filename_gap,
            filename_padding_y=self.browser_filename_padding_y,
            item_spacing=0,
        )
        self.list_view.setItemDelegate(self.item_delegate)
        self.list_view.setViewMode(QListView.ViewMode.IconMode)
        self.list_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.list_view.setMovement(QListView.Movement.Static)
        self.list_view.setVerticalScrollMode(
            QListView.ScrollMode.ScrollPerPixel
        )
        self.list_view.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.list_view.setUniformItemSizes(True)
        self.list_view.setMouseTracking(True)
        self.list_view.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list_view.ensure_viewport_drop_target()
        self._apply_list_view_geometry()
        self.list_view.activated.connect(self.open_item)
        self.list_view.selectionModel().currentChanged.connect(
            self._on_list_current_changed
        )
        self.list_view.selectionModel().selectionChanged.connect(
            self._on_list_selection_changed
        )
        self.list_view.entered.connect(self._on_list_hovered)
        self.item_model.modelReset.connect(self._update_list_viewport)
        self.item_model.layoutChanged.connect(self._update_list_viewport)
        self.item_model.rowsInserted.connect(
            lambda _parent, _first, _last: self._update_list_viewport()
        )
        self.item_model.rowsRemoved.connect(
            lambda _parent, _first, _last: self._update_list_viewport()
        )
        self.list_view.verticalScrollBar().valueChanged.connect(
            self._on_list_scrolled
        )
        self.list_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(self._show_context_menu)
        self.list_view.paths_dropped.connect(self._on_browser_paths_dropped)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.list_view)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, True)
        self.setCentralWidget(self.splitter)
        self.splitter.setAcceptDrops(True)

        self.navigation_toolbar = QToolBar("ナビゲーション", self)
        self.navigation_toolbar.setObjectName("browser_navigation_toolbar")
        self.navigation_toolbar.setMovable(False)
        self.navigation_toolbar.setIconSize(QSize(24, 24))
        self.navigation_toolbar.setMinimumHeight(36)

        self.back_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowBack),
            "戻る",
            self,
        )
        self.back_action.setToolTip("前に表示していたフォルダへ戻る (Alt+Left)")
        self.back_action.setShortcuts(
            [QKeySequence("Alt+Left")]
        )
        self.back_action.triggered.connect(self.go_back)
        self.navigation_toolbar.addAction(self.back_action)

        self.forward_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowForward),
            "進む",
            self,
        )
        self.forward_action.setToolTip("戻る前のフォルダへ進む (Alt+Right)")
        self.forward_action.setShortcut(QKeySequence("Alt+Right"))
        self.forward_action.triggered.connect(self.go_forward)
        self.navigation_toolbar.addAction(self.forward_action)

        self.up_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowUp),
            "上へ",
            self,
        )
        self.up_action.setToolTip("ひとつ上の階層へ移動 (Alt+Up)")
        self.up_action.setShortcut(QKeySequence("Alt+Up"))
        self.up_action.triggered.connect(self.go_up)
        self.navigation_toolbar.addAction(self.up_action)

        self.refresh_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
            "更新",
            self,
        )
        self.refresh_action.setToolTip("現在のフォルダを更新 (F5)")
        self.refresh_action.setShortcut(QKeySequence("F5"))
        self.refresh_action.triggered.connect(self.refresh_current_folder)
        self.navigation_toolbar.addAction(self.refresh_action)
        self.navigation_toolbar.addSeparator()

        self.address_bar = QLineEdit(self)
        self.address_bar.setObjectName("browser_address_bar")
        self.address_bar.setClearButtonEnabled(True)
        self.address_bar.setPlaceholderText("フォルダ、画像、ZIP/CBZのパス")
        self.address_bar.setToolTip(
            "パスを入力してEnterで移動。相対パスは現在のフォルダ基準です。"
        )
        self.address_bar.returnPressed.connect(self._navigate_from_address_bar)
        self.address_bar.installEventFilter(self)
        self.navigation_toolbar.addWidget(self.address_bar)
        self.navigation_toolbar.addSeparator()

        self.browser_sort_key_combo = QComboBox(self)
        self.browser_sort_key_combo.setObjectName("browser_sort_key_combo")
        self.browser_sort_key_combo.setToolTip("一覧の並び替え基準")
        for value, label in BROWSER_SORT_KEY_LABELS.items():
            self.browser_sort_key_combo.addItem(label, value.value)
        self.navigation_toolbar.addWidget(self.browser_sort_key_combo)

        self.browser_sort_order_combo = QComboBox(self)
        self.browser_sort_order_combo.setObjectName("browser_sort_order_combo")
        self.browser_sort_order_combo.setToolTip("一覧の並び順")
        for value, label in BROWSER_SORT_ORDER_LABELS.items():
            self.browser_sort_order_combo.addItem(label, value.value)
        self.navigation_toolbar.addWidget(self.browser_sort_order_combo)

        self.browser_folders_first_checkbox = QCheckBox("フォルダ先頭", self)
        self.browser_folders_first_checkbox.setObjectName(
            "browser_folders_first_checkbox"
        )
        self.browser_folders_first_checkbox.setToolTip(
            "昇順・降順にかかわらずフォルダを先頭へ表示"
        )
        self.navigation_toolbar.addWidget(self.browser_folders_first_checkbox)

        self.browser_display_density_combo = QComboBox(self)
        self.browser_display_density_combo.setObjectName(
            "browser_display_density_combo"
        )
        self.browser_display_density_combo.setToolTip("一覧の表示密度")
        for value, label in BROWSER_DISPLAY_DENSITY_LABELS.items():
            self.browser_display_density_combo.addItem(label, value.value)
        self.navigation_toolbar.addWidget(self.browser_display_density_combo)
        self._sync_browser_controls()
        self.browser_sort_key_combo.currentIndexChanged.connect(
            self._apply_browser_controls
        )
        self.browser_sort_order_combo.currentIndexChanged.connect(
            self._apply_browser_controls
        )
        self.browser_folders_first_checkbox.toggled.connect(
            self._apply_browser_controls
        )
        self.browser_display_density_combo.currentIndexChanged.connect(
            self._apply_browser_controls
        )
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.navigation_toolbar)

        self.focus_address_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        self.focus_address_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.focus_address_shortcut.activated.connect(self.focus_address_bar)

        self.rename_shortcut = QShortcut(QKeySequence("F2"), self.list_view)
        self.rename_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.rename_shortcut.activated.connect(self.rename_selected_item)
        self.recycle_shortcut = QShortcut(QKeySequence("Delete"), self.list_view)
        self.recycle_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.recycle_shortcut.activated.connect(self.move_selected_to_recycle_bin)
        self.copy_shortcut = QShortcut(QKeySequence("Ctrl+C"), self.list_view)
        self.copy_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.copy_shortcut.activated.connect(self.copy_selected_items)
        self.cut_shortcut = QShortcut(QKeySequence("Ctrl+X"), self.list_view)
        self.cut_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.cut_shortcut.activated.connect(self.cut_selected_items)
        self.paste_shortcut = QShortcut(QKeySequence("Ctrl+V"), self.list_view)
        self.paste_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.paste_shortcut.activated.connect(self.paste_items)
        self.new_folder_shortcut = QShortcut(
            QKeySequence("Ctrl+Shift+N"),
            self.list_view,
        )
        self.new_folder_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.new_folder_shortcut.activated.connect(self.create_new_folder)

        for target in (
            self,
            self.list_view,
            self.list_view.viewport(),
            self.folder_tree,
            self.folder_tree.viewport(),
            self.bookmark_view,
            self.bookmark_view.viewport(),
            self.favorite_view,
            self.favorite_view.viewport(),
            self.history_view,
            self.history_view.viewport(),
            self.sidebar,
            self.splitter,
        ):
            target.installEventFilter(self)

        file_menu = self.menuBar().addMenu("ファイル")
        self.copy_action = file_menu.addAction("コピー")
        self.copy_action.triggered.connect(self.copy_selected_items)
        self.cut_action = file_menu.addAction("切り取り")
        self.cut_action.triggered.connect(self.cut_selected_items)
        self.paste_action = file_menu.addAction("貼り付け")
        self.paste_action.triggered.connect(self.paste_items)
        file_menu.addSeparator()
        self.copy_to_action = file_menu.addAction("指定先へコピー...")
        self.copy_to_action.triggered.connect(
            lambda _checked=False: self.copy_selected_to()
        )
        self.move_to_action = file_menu.addAction("指定先へ移動...")
        self.move_to_action.triggered.connect(
            lambda _checked=False: self.move_selected_to()
        )
        self.copy_destination_menu = file_menu.addMenu("コピー先")
        self.move_destination_menu = file_menu.addMenu("移動先")
        self.copy_destination_menu.aboutToShow.connect(
            lambda: self._populate_destination_menu(
                self.copy_destination_menu,
                FileOperationKind.COPY,
            )
        )
        self.move_destination_menu.aboutToShow.connect(
            lambda: self._populate_destination_menu(
                self.move_destination_menu,
                FileOperationKind.MOVE,
            )
        )
        file_menu.addSeparator()
        self.rename_action = file_menu.addAction("名前の変更")
        self.rename_action.triggered.connect(self.rename_selected_item)
        self.recycle_action = file_menu.addAction("ごみ箱へ移動")
        self.recycle_action.triggered.connect(self.move_selected_to_recycle_bin)
        file_menu.addSeparator()
        self.new_folder_action = file_menu.addAction("新しいフォルダ")
        self.new_folder_action.triggered.connect(self.create_new_folder)

        view_menu = self.menuBar().addMenu("表示")
        self.sidebar_action = QAction("サイドバーを表示", self)
        self.sidebar_action.setCheckable(True)
        self.sidebar_action.setChecked(
            bool(self.settings.get("browser_sidebar_visible", True))
        )
        self.sidebar_action.toggled.connect(self.set_sidebar_visible)
        view_menu.addAction(self.sidebar_action)
        self.favorites_visible_action = QAction("お気に入りを表示", self)
        self.favorites_visible_action.setCheckable(True)
        self.favorites_visible_action.toggled.connect(
            lambda checked: self.set_sidebar_component_visible(
                "favorites", checked
            )
        )
        view_menu.addAction(self.favorites_visible_action)
        self.folder_tree_visible_action = QAction("フォルダツリーを表示", self)
        self.folder_tree_visible_action.setCheckable(True)
        self.folder_tree_visible_action.toggled.connect(
            lambda checked: self.set_sidebar_component_visible("tree", checked)
        )
        view_menu.addAction(self.folder_tree_visible_action)
        self.history_visible_action = QAction("履歴を表示", self)
        self.history_visible_action.setCheckable(True)
        self.history_visible_action.toggled.connect(
            lambda checked: self.set_sidebar_component_visible(
                "history", checked
            )
        )
        view_menu.addAction(self.history_visible_action)
        layout_menu = view_menu.addMenu("サイドバーレイアウト")
        self.sidebar_layout_actions: dict[str, QAction] = {}
        for name, label in (
            ("favorites_top_tree_bottom", "お気に入り上／ツリー下"),
            ("tree_top_favorites_bottom", "ツリー上／お気に入り下"),
            ("tabs", "タブ"),
            ("favorites_only", "お気に入りのみ"),
            ("tree_only", "ツリーのみ"),
        ):
            action = layout_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, value=name: self.set_sidebar_layout(value)
            )
            self.sidebar_layout_actions[name] = action
        tree_sync_menu = view_menu.addMenu("フォルダツリー同期")
        self.folder_tree_sync_actions: dict[str, QAction] = {}
        for name, label in (
            ("off", "同期しない"),
            ("select_current", "現在フォルダを選択"),
            ("focus_current", "現在フォルダへフォーカス"),
        ):
            action = tree_sync_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, value=name: (
                    self.set_folder_tree_sync_mode(value)
                )
            )
            self.folder_tree_sync_actions[name] = action
        self.collapse_auto_tree_action = tree_sync_menu.addAction(
            "無関係な自動展開を折りたたむ"
        )
        self.collapse_auto_tree_action.setCheckable(True)
        self.collapse_auto_tree_action.triggered.connect(
            self.set_folder_tree_collapse_unrelated
        )
        self._sync_sidebar_actions()

        bookmark_menu = self.menuBar().addMenu("ブックマーク")
        self.add_folder_bookmark_action = QAction("現在のフォルダを追加", self)
        self.add_folder_bookmark_action.triggered.connect(
            self.add_current_folder_bookmark
        )
        bookmark_menu.addAction(self.add_folder_bookmark_action)
        self.toggle_folder_bookmark_shortcut = QShortcut(
            QKeySequence("Ctrl+B"),
            self,
        )
        self.toggle_folder_bookmark_shortcut.setContext(
            Qt.ShortcutContext.WindowShortcut
        )
        self.toggle_folder_bookmark_shortcut.activated.connect(
            self.toggle_current_folder_bookmark
        )

        history_menu = self.menuBar().addMenu("履歴")
        clear_history_action = QAction("閲覧履歴をすべて消去...", self)
        clear_history_action.triggered.connect(
            lambda _checked=False: self.clear_history()
        )
        history_menu.addAction(clear_history_action)

        self.settings_action = QAction("環境設定…", self)
        self.settings_action.triggered.connect(self.open_settings_dialog)
        self.menuBar().addAction(self.settings_action)

        self.statusBar().showMessage("フォルダを選択してください。")
        self.cancel_operation_button = QPushButton("キャンセル", self)
        self.cancel_operation_button.setObjectName(
            "cancel_file_operation_button"
        )
        self.cancel_operation_button.clicked.connect(self.cancel_file_operation)
        self.cancel_operation_button.setVisible(False)
        self.statusBar().addPermanentWidget(self.cancel_operation_button)
        self.file_operation_panel = FileOperationPanel(self)
        if self.file_operation_coordinator.queue is not None:
            self.file_operation_panel.bind(self.file_operation_coordinator.queue)
            self.statusBar().addPermanentWidget(self.file_operation_panel, 1)
        QApplication.clipboard().changed.connect(
            self._on_system_clipboard_changed
        )
        self.splitter.setSizes([self._sidebar_width, max(1, self.width() - self._sidebar_width)])
        self.set_sidebar_visible(self.sidebar_action.isChecked())
        self._update_navigation_actions()
        self._update_file_action_states()

    def _restore_initial_folder(self) -> None:
        raw_path = self.settings.get("last_browser_path", "")
        candidate = Path(raw_path) if isinstance(raw_path, str) and raw_path else Path.home()
        self.set_current_folder(candidate)

    def _restore_window_state(self) -> None:
        raw_geometry = self.settings.get("browser_window_geometry", "")
        if not isinstance(raw_geometry, str) or not raw_geometry:
            return
        try:
            self.restoreGeometry(QByteArray.fromBase64(raw_geometry.encode("ascii")))
        except (ValueError, TypeError):
            return

    def _save_window_state(self) -> None:
        if self.sidebar.isVisible():
            sizes = self.splitter.sizes()
            if sizes and sizes[0] > 0:
                self._sidebar_width = sizes[0]
        self.config.set("browser_sidebar_visible", self.sidebar.isVisible())
        self.config.set("browser_sidebar_width", self._sidebar_width)
        self.config.set(
            "browser_sidebar_splitter_sizes",
            self.sidebar_layout_controller.current_splitter_sizes(),
        )
        self.config.set(
            "browser_window_geometry",
            bytes(self.saveGeometry().toBase64()).decode("ascii"),
        )

    def _on_tree_current_changed(
        self,
        current: QModelIndex,
        _previous: QModelIndex,
    ) -> None:
        # Compatibility hook: currentChanged is display state, never a
        # navigation command. User navigation comes from click confirmation.
        del current, _previous

    def _on_tree_navigation_confirmed(self, index: QModelIndex) -> None:
        if self.folder_tree_sync.applying or not index.isValid():
            return
        path = self.file_system_model.filePath(index)
        if path:
            self.navigate_to(path)

    def _apply_pending_tree_path(self) -> None:
        # Kept for binary/test compatibility with Sprint 15. No currentChanged
        # path is queued by the click-confirmed tree implementation. A direct
        # call is treated as an explicit legacy activation, not a signal.
        path = self._pending_tree_navigation_path
        self._pending_tree_navigation_path = None
        if path is None and self.folder_tree.currentIndex().isValid():
            current_path = self.file_system_model.filePath(
                self.folder_tree.currentIndex()
            )
            path = Path(current_path) if current_path else None
        if path is not None:
            self.navigate_to(path)

    def _apply_favorite_row_metrics(self) -> None:
        if not hasattr(self, "favorite_view"):
            return
        self.favorite_view.setItemDelegate(
            FavoriteItemDelegate(
                self.favorite_view,
                metrics=self.favorite_row_metrics,
            )
        )
        self.favorite_view.setIconSize(
            QSize(
                self.favorite_row_metrics.icon_size,
                self.favorite_row_metrics.icon_size,
            )
        )
        self.favorite_view.setSpacing(self.favorite_row_metrics.spacing)
        self.favorite_view.doItemsLayout()

    def _sync_tree_to_path(self, path: Path) -> None:
        self.folder_tree_sync.sync(
            path,
            mode=self.folder_tree_sync_mode,
            collapse_unrelated=self.folder_tree_collapse_unrelated,
            focus_rebase=self.folder_tree_focus_rebase,
            context_ancestor_levels=self.folder_tree_context_ancestor_levels,
        )

    def _on_list_paint_completed(self) -> None:
        generation = self._first_paint_pending_generation
        if generation is None:
            return
        self._first_paint_pending_generation = None
        trace_id = self._first_paint_trace_id
        self._first_paint_trace_id = 0
        if trace_id:
            performance_trace.mark(trace_id, "browser.list.first_paint")
        QTimer.singleShot(
            0,
            lambda generation=generation, trace_id=trace_id: (
                self._run_deferred_after_first_paint(generation, trace_id)
            ),
        )

    def _run_deferred_after_first_paint(
        self,
        generation: int,
        trace_id: int,
    ) -> None:
        if (
            self._shutdown_prepared
            or generation != self._scan_generation
            or self.current_path is None
        ):
            return
        if self._deferred_tree_sync_generation == generation:
            self._deferred_tree_sync_generation = None
            if trace_id:
                performance_trace.mark(trace_id, "folder_tree.sync.begin")
                self._tree_trace_ids[
                    self.folder_tree_sync.generation + 1
                ] = trace_id
            self._sync_tree_to_path(self.current_path)
        if trace_id:
            performance_trace.mark(trace_id, "thumbnail.request.begin")
        self._schedule_thumbnail_requests(0)

    def _on_folder_tree_sync_finished(
        self,
        generation: int,
        path: str,
    ) -> None:
        trace_id = self._tree_trace_ids.pop(int(generation), 0)
        if trace_id:
            performance_trace.mark(
                trace_id,
                "folder_tree.sync.complete",
                path,
            )

    def _on_tree_directory_loaded(self, _path: str) -> None:
        # Compatibility hook retained for older tests and integrations.
        if self.current_path is not None:
            self._sync_tree_to_path(self.current_path)

    def _request_visible_thumbnails(self) -> None:
        row_count = self.item_model.rowCount()
        if self._shutdown_prepared or row_count <= 0:
            return
        visible_range = self._visible_row_range()
        if visible_range is None:
            return
        selected_rows = tuple(
            index.row()
            for index in self.list_view.selectionModel().selectedIndexes()
        )
        plan = build_thumbnail_request_plan(
            row_count=row_count,
            first_visible=visible_range[0],
            last_visible=visible_range[1],
            selected_rows=selected_rows,
            prefetch_screens=1,
            fast_scrolling=self._fast_scrolling,
        )
        selected_set = set(selected_rows)
        set_fast_scroll = getattr(
            self.thumbnail_provider,
            "set_fast_scroll_suppressed",
            None,
        )
        if callable(set_fast_scroll):
            set_fast_scroll(self._fast_scrolling)
        for rows, priority in (
            (plan.visible_rows, ThumbnailPriority.VISIBLE),
            (plan.selected_rows, ThumbnailPriority.SELECTED),
            (plan.prefetch_rows, ThumbnailPriority.PREFETCH),
        ):
            for row in rows:
                item = self.item_model.item_at(row)
                if (
                    item is None
                    or not item.can_generate_preview
                ):
                    continue
                effective_priority = priority
                if self._fast_scrolling and priority is ThumbnailPriority.VISIBLE:
                    if row in selected_set:
                        effective_priority = ThumbnailPriority.SELECTED
                self.thumbnail_provider.request(
                    item,
                    self.thumbnail_render_spec,
                    generation=self._generation,
                    priority=effective_priority,
                )
        if self._fast_scrolling:
            keep_paths = {
                str(item.path)
                for row in plan.visible_rows + plan.selected_rows
                if (item := self.item_model.item_at(row)) is not None
            }
            self.thumbnail_provider.cancel_prefetch_except(
                keep_paths,
                size=self.thumbnail_render_spec,
                generation=self._generation,
            )

    def _current_device_pixel_ratio(self) -> float:
        window = self.windowHandle()
        screen = window.screen() if window is not None else QApplication.primaryScreen()
        return max(1.0, float(screen.devicePixelRatio())) if screen is not None else 1.0

    def _build_thumbnail_render_spec(self) -> ThumbnailRenderSpec:
        policy = ThumbnailRenderPolicy(
            logical_thumbnail_size=self.thumbnail_size,
            frame_ratio_id=self.thumbnail_frame_ratio,
            crop_mode=self.thumbnail_crop_mode,
            device_pixel_ratio=self._thumbnail_dpr,
            quality_mode=self.thumbnail_quality_mode,
            max_edge=self.thumbnail_cache_max_edge,
            browser_display_mode=self.browser_thumbnail_display_mode,
        )
        spec = policy.render_spec()
        if _THUMBNAIL_LOG.isEnabledFor(logging.DEBUG):
            metrics = policy.diagnostics()
            _THUMBNAIL_LOG.debug(
                "policy logical=%s dpr=%.3f cache=%s qimage_dpr=%.1f "
                "display=%s upscale=%.3f resize_count=%d smooth=%s",
                metrics.logical_frame,
                metrics.window_dpr,
                metrics.cache_pixels,
                metrics.qimage_dpr,
                metrics.display_pixels,
                metrics.upscale_factor,
                metrics.resize_count,
                metrics.smooth_pixmap_transform,
            )
        return spec

    def _install_screen_tracking(self) -> None:
        if self._shutdown_prepared:
            return
        window = self.windowHandle()
        if window is None or window is self._screen_tracking_window:
            return
        self._screen_tracking_window = window
        window.screenChanged.connect(self._on_screen_changed)

    def _on_screen_changed(self, _screen) -> None:
        QTimer.singleShot(0, self._reevaluate_thumbnail_dpr)

    def _reevaluate_thumbnail_dpr(self) -> None:
        if self._shutdown_prepared:
            return
        current = self._current_device_pixel_ratio()
        if abs(current - self._thumbnail_dpr) < 0.01:
            return
        self._thumbnail_dpr = current
        new_spec = self._build_thumbnail_render_spec()
        if new_spec.cache_token == self.thumbnail_render_spec.cache_token:
            return
        self.thumbnail_render_spec = new_spec
        self.thumbnail_bucket_size = new_spec.long_edge
        self._generation = self.thumbnail_provider.begin_generation()
        self._schedule_thumbnail_requests(0)

    def _visible_row_range(self) -> tuple[int, int] | None:
        viewport = self.list_view.viewport()
        grid = self.list_view.gridSize()
        return calculate_grid_visible_range(
            row_count=self.item_model.rowCount(),
            viewport_width=viewport.width(),
            viewport_height=viewport.height(),
            grid_width=grid.width(),
            grid_height=grid.height(),
            vertical_offset=self.list_view.verticalScrollBar().value(),
        )

    def _schedule_thumbnail_requests(self, delay_ms: int = 30) -> None:
        if self._shutdown_prepared:
            return
        self._thumbnail_request_timer.start(max(0, int(delay_ms)))

    def _on_list_scrolled(self, value: int) -> None:
        now = monotonic()
        delta = abs(int(value) - self._last_scroll_value)
        elapsed = now - self._last_scroll_time
        threshold = max(
            self.list_view.gridSize().height(),
            self.list_view.viewport().height() // 2,
        )
        self._fast_scrolling = delta >= threshold or (
            elapsed < 0.12 and delta > 0
        )
        self._last_scroll_value = int(value)
        self._last_scroll_time = now
        self._schedule_thumbnail_requests(0)
        self._scroll_idle_timer.start()

    def _on_scroll_idle(self) -> None:
        self._fast_scrolling = False
        self._schedule_thumbnail_requests(0)

    def _on_thumbnail_ready(self, path: str, generation: int, qimage) -> None:
        if (
            self._shutdown_prepared
            or generation != self._generation
            or qimage is None
            or qimage.isNull()
        ):
            return
        self.item_model.set_thumbnail_image(path, qimage, low_resolution=False)

    def _on_thumbnail_failed(
        self,
        path: str,
        generation: int,
        message: str,
    ) -> None:
        if self._shutdown_prepared or generation != self._generation:
            return
        self.item_model.set_thumbnail_error(path, message)

    def _on_preview_state_changed(
        self,
        path: str,
        generation: int,
        status: str,
    ) -> None:
        if self._shutdown_prepared or generation != self._generation:
            return
        self.item_model.set_preview_status(path, status)

    def _on_thumbnail_provisional(
        self,
        path: str,
        generation: int,
        qimage,
    ) -> None:
        if (
            self._shutdown_prepared
            or generation != self._generation
            or qimage is None
            or qimage.isNull()
        ):
            return
        self.item_model.set_thumbnail_image(path, qimage, low_resolution=True)

    def _update_status(
        self,
        *,
        error: str | None = None,
        force: bool = False,
    ) -> None:
        if self._shutdown_prepared:
            return
        if self._active_file_operation_id is not None:
            return
        if self._temporary_status_message is not None and not force:
            return
        if force:
            self._temporary_status_message = None
        self._status_message_token += 1
        if error:
            self.statusBar().showMessage(error)
            return
        pending = self._pending_scan
        if pending is not None:
            count = (
                len(pending.refresh_entries)
                if pending.refresh
                else (
                    self.item_model.rowCount() + len(pending.buffered_entries)
                    if pending.committed
                    else 0
                )
            )
            self.statusBar().showMessage(
                f"{pending.path} — 読み込み中… {count}項目"
            )
            return
        count = self.item_model.rowCount()
        selected = self.item_model.item_at(self.list_view.currentIndex())
        folder = str(self.current_path) if self.current_path is not None else ""
        sort_label = BROWSER_SORT_KEY_LABELS[self.browser_sort_key]
        order_label = BROWSER_SORT_ORDER_LABELS[self.browser_sort_order]
        density_label = BROWSER_DISPLAY_DENSITY_LABELS[self.browser_display_density]
        message = (
            f"{folder} — {count}件 — {sort_label}・{order_label}"
            f" — 表示: {density_label}"
        )
        if selected is not None:
            message += f" — 選択: {selected.display_name}"
            selected_count = len(self.list_view.selectionModel().selectedIndexes())
            if selected_count > 1:
                message += f" ほか{selected_count - 1}件"
        self.statusBar().showMessage(message)

    def _show_temporary_status(self, message: str, timeout_ms: int = 3000) -> None:
        self._status_message_token += 1
        token = self._status_message_token
        self._temporary_status_message = message
        self.statusBar().showMessage(message)

        def restore_status() -> None:
            if self._shutdown_prepared:
                return
            if token == self._status_message_token:
                self._temporary_status_message = None
                self._update_status(force=True)

        QTimer.singleShot(timeout_ms, restore_status)

    def _navigate_from_address_bar(self) -> None:
        raw_path = self.address_bar.text().strip()
        if len(raw_path) >= 2 and raw_path[0] == raw_path[-1] and raw_path[0] in {
            '"',
            "'",
        }:
            raw_path = raw_path[1:-1].strip()
        if not raw_path:
            self._sync_address_bar()
            return

        target = self._absolute_browser_path(raw_path)
        if target.suffix.lower() in (
            BROWSER_IMAGE_EXTENSIONS | BROWSER_ARCHIVE_EXTENSIONS
        ):
            location = BrowserLocation(
                str(target.parent),
                selected_path=str(target),
            )
            if self.navigate_to(target.parent, restore_location=location):
                self._restore_location(location)
            return
        self.navigate_to(target)

    def _show_path_in_browser(self, path: str | Path) -> None:
        self.select_path(path)

    def _current_location(self) -> BrowserLocation:
        selected = self.item_model.item_at(self.list_view.currentIndex())
        return BrowserLocation(
            path=str(self.current_path or ""),
            selected_path=str(selected.path) if selected is not None else None,
            vertical_scroll=self.list_view.verticalScrollBar().value(),
            horizontal_scroll=self.list_view.horizontalScrollBar().value(),
        )

    def _capture_list_view_state(self) -> _ListViewState:
        selected_paths: list[str] = []
        selection_model = self.list_view.selectionModel()
        if selection_model is not None:
            for index in selection_model.selectedIndexes():
                item = self.item_model.item_at(index)
                if item is not None:
                    selected_paths.append(str(item.path))
        current_item = self.item_model.item_at(self.list_view.currentIndex())
        anchor_item = self.item_model.item_at(self._visible_anchor_index())
        return _ListViewState(
            selected_paths=tuple(selected_paths),
            current_path=str(current_item.path) if current_item is not None else None,
            anchor_path=str(anchor_item.path) if anchor_item is not None else None,
            vertical_scroll=self.list_view.verticalScrollBar().value(),
            horizontal_scroll=self.list_view.horizontalScrollBar().value(),
        )

    def _visible_anchor_index(self) -> QModelIndex:
        viewport = self.list_view.viewport()
        grid_width = max(1, self.list_view.gridSize().width())
        sample_x = min(max(1, grid_width // 2), max(1, viewport.width() - 1))
        for y in range(1, max(2, viewport.height()), 4):
            index = self.list_view.indexAt(QPoint(sample_x, y))
            if index.isValid():
                return index
        return self.list_view.currentIndex()

    def _schedule_list_view_state_restore(self, state: _ListViewState) -> None:
        self._list_view_restore_token += 1
        token = self._list_view_restore_token
        self._restore_list_view_state(state)

        def restore_after_layout() -> None:
            if token != self._list_view_restore_token:
                return
            self._restore_list_view_state(state)
            self._schedule_thumbnail_requests()

        QTimer.singleShot(0, restore_after_layout)

    def _restore_list_view_state(self, state: _ListViewState) -> None:
        selection_model = self.list_view.selectionModel()
        if selection_model is None:
            return
        selection_model.clearSelection()
        for path in state.selected_paths:
            row = self.item_model.row_for_path(path)
            if row >= 0:
                selection_model.select(
                    self.item_model.index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select,
                )

        current = QModelIndex()
        if state.current_path:
            row = self.item_model.row_for_path(state.current_path)
            if row >= 0:
                current = self.item_model.index(row, 0)
        selection_model.setCurrentIndex(
            current,
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )

        anchor = QModelIndex()
        if state.anchor_path:
            row = self.item_model.row_for_path(state.anchor_path)
            if row >= 0:
                anchor = self.item_model.index(row, 0)
        if anchor.isValid():
            self.list_view.scrollTo(anchor, QListView.ScrollHint.PositionAtTop)
        else:
            self.list_view.verticalScrollBar().setValue(state.vertical_scroll)
            self.list_view.horizontalScrollBar().setValue(state.horizontal_scroll)
        self._update_status()

    def _update_current_navigation_state(self) -> None:
        if self.current_path is None:
            return
        location = self._current_location()
        self.navigation_history.update_current_view_state(
            selected_path=location.selected_path,
            vertical_scroll=location.vertical_scroll,
            horizontal_scroll=location.horizontal_scroll,
        )

    def _schedule_location_restore(self, location: BrowserLocation) -> None:
        self._location_restore_token += 1
        token = self._location_restore_token
        self._restore_location(location)

        def restore_after_layout() -> None:
            if token != self._location_restore_token:
                return
            self._restore_location(location, update_status=False)

        QTimer.singleShot(0, restore_after_layout)

    def _restore_location(
        self,
        location: BrowserLocation,
        *,
        update_status: bool = True,
    ) -> None:
        if not self._same_path(self.current_path, Path(location.path)):
            return
        index = QModelIndex()
        if location.selected_path:
            row = self.item_model.row_for_path(location.selected_path)
            if row >= 0:
                index = self.item_model.index(row, 0)
        if index.isValid():
            self.list_view.setCurrentIndex(index)
            self.list_view.scrollTo(index, QListView.ScrollHint.EnsureVisible)
        else:
            self.list_view.clearSelection()
            self.list_view.setCurrentIndex(QModelIndex())

        vertical = self.list_view.verticalScrollBar()
        horizontal = self.list_view.horizontalScrollBar()
        if not index.isValid() or location.vertical_scroll > 0:
            vertical.setValue(
                max(
                    vertical.minimum(),
                    min(location.vertical_scroll, vertical.maximum()),
                )
            )
        if not index.isValid() or location.horizontal_scroll > 0:
            horizontal.setValue(
                max(
                    horizontal.minimum(),
                    min(location.horizontal_scroll, horizontal.maximum()),
                )
            )
        if update_status:
            self._update_status()

    def _sync_address_bar(self) -> None:
        self.address_bar.setText(
            str(self.current_path) if self.current_path is not None else ""
        )

    def _update_navigation_actions(self) -> None:
        self.back_action.setEnabled(self.navigation_history.can_go_back())
        self.forward_action.setEnabled(self.navigation_history.can_go_forward())
        can_go_up = False
        if self.current_path is not None:
            can_go_up = not self._same_path(
                self.current_path,
                self.current_path.parent,
            )
        self.up_action.setEnabled(can_go_up)
        self.refresh_action.setEnabled(self.current_path is not None)
        self._update_file_action_states()

    def _update_file_action_states(self) -> None:
        if not hasattr(self, "rename_action"):
            return
        selected_count = len(
            self.list_view.selectionModel().selectedIndexes()
        )
        busy = (
            self.file_operation_coordinator.busy
            and self.file_operation_coordinator.queue is None
        )
        has_selection = selected_count > 0
        self.rename_action.setEnabled(selected_count == 1 and not busy)
        self.recycle_action.setEnabled(has_selection and not busy)
        self.copy_action.setEnabled(has_selection and not busy)
        self.cut_action.setEnabled(has_selection and not busy)
        self.copy_to_action.setEnabled(has_selection and not busy)
        self.move_to_action.setEnabled(has_selection and not busy)
        self.paste_action.setEnabled(
            self.current_path is not None
            and not busy
            and bool(self._clipboard_paths or self._clipboard_file_urls())
        )
        self.new_folder_action.setEnabled(
            self.current_path is not None and not busy
        )

    def _absolute_browser_path(self, path: str | Path) -> Path:
        target = Path(path).expanduser()
        if not target.is_absolute():
            base = self.current_path or Path.cwd()
            target = base / target
        return Path(os.path.abspath(os.path.normpath(os.fspath(target))))

    @staticmethod
    def _same_path(first: Path | None, second: Path | None) -> bool:
        if first is None or second is None:
            return first is second
        first_key = os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(first)))
        ).casefold()
        second_key = os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(second)))
        ).casefold()
        return first_key == second_key

    @staticmethod
    def _path_key(path: str | Path) -> str:
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path)))
        ).casefold()

    def _on_browser_paths_dropped(
        self,
        paths: tuple[str, ...],
        index: QModelIndex,
        modifiers: Qt.KeyboardModifier,
        source: object,
    ) -> None:
        if source is not self.list_view:
            self.browser_main_drop.handle_external_paths(
                paths,
                behavior=self.browser_external_drop_behavior,
            )
            return
        item = self.item_model.item_at(index)
        if item is not None and item.kind is BrowserItemKind.FOLDER:
            self._start_drop_operation(paths, item.path, modifiers)
            return
        supported = tuple(
            path
            for path in paths
            if Path(path).suffix
            and is_lexically_supported_viewer_path(path)
        )
        if supported and len(supported) == len(paths):
            self._open_dropped_paths(supported)
            return
        if len(paths) == 1:
            self._probe_dropped_folders(
                paths,
                lambda folders: (
                    self.navigate_to(folders[0], record_history=True)
                    if len(folders) == 1
                    else None
                ),
            )

    def _begin_browser_drop_focus(
        self,
        request: PendingBrowserFocusRequest,
    ) -> None:
        if (
            self._shutdown_prepared
            or request.request_id != self.browser_main_drop.active_request_id
        ):
            return
        if request.ignored_count:
            self._show_temporary_status(
                f"先頭のフォルダを使用します（ほか{request.ignored_count}件）"
            )
        if not request.paths:
            self._starting_drop_focus_navigation = True
            try:
                self.navigate_to(request.folder, record_history=True)
            finally:
                self._starting_drop_focus_navigation = False
            return
        if self._same_path(self.current_path, request.folder):
            active_scan = self._pending_scan
            scan_in_progress = bool(
                active_scan is not None
                and self._same_path(active_scan.path, request.folder)
            )
            self._pending_browser_focus = request.with_generation(
                (
                    active_scan.generation
                    if scan_in_progress and active_scan is not None
                    else self._scan_generation
                )
            )
            self._apply_pending_browser_focus(final=not scan_in_progress)
            return
        self._starting_drop_focus_navigation = True
        try:
            started = self.navigate_to(
                request.folder,
                record_history=True,
            )
        finally:
            self._starting_drop_focus_navigation = False
        if not started:
            return
        pending_scan = self._pending_scan
        if pending_scan is None or not self._same_path(
            pending_scan.path,
            request.folder,
        ):
            return
        self._pending_browser_focus = request.with_generation(
            pending_scan.generation
        )

    def _apply_pending_browser_focus(self, *, final: bool) -> None:
        request = self._pending_browser_focus
        if (
            request is None
            or self._shutdown_prepared
            or request.request_id != self.browser_main_drop.active_request_id
            or request.scan_generation != self._scan_generation
            or not self._same_path(self.current_path, request.folder)
        ):
            return
        selection_model = self.list_view.selectionModel()
        if selection_model is None:
            return
        found: list[tuple[Path, QModelIndex]] = []
        for path in request.paths:
            row = self.item_model.row_for_path(path)
            if row >= 0:
                found.append((path, self.item_model.index(row, 0)))
        if found:
            selection_model.clearSelection()
            for _path, index in found:
                selection_model.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select,
                )
            primary_index = QModelIndex()
            if request.primary is not None:
                primary_row = self.item_model.row_for_path(request.primary)
                if primary_row >= 0:
                    primary_index = self.item_model.index(primary_row, 0)
            if not primary_index.isValid():
                primary_index = found[0][1]
            selection_model.setCurrentIndex(
                primary_index,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
            self.list_view.scrollTo(
                primary_index,
                QListView.ScrollHint.PositionAtCenter,
            )
        if not final:
            return
        self._pending_browser_focus = None
        if not found:
            self._show_temporary_status(
                "ドロップした項目は現在の一覧に表示されません"
            )
            return
        if request.open_after:
            primary = self.item_model.item_at(
                self.list_view.currentIndex()
            )
            if (
                primary is not None
                and primary.openable_by_nivisviewer
                and primary.kind is not BrowserItemKind.OTHER
            ):
                self.open_item(self.list_view.currentIndex())

    def _cancel_browser_drop_focus(self) -> None:
        self._pending_browser_focus = None
        self.browser_main_drop.cancel()

    def _on_favorite_paths_dropped(
        self,
        paths: tuple[str, ...],
        index: QModelIndex,
        modifiers: Qt.KeyboardModifier,
        source: object,
    ) -> None:
        entry = self.folder_bookmark_model.entry_at(index)
        if source is self.favorite_view and self.metadata_store is not None:
            ordered = [
                item.path
                for item in self.folder_bookmark_model.entries
                if self._path_key(item.path)
                not in {self._path_key(path) for path in paths}
            ]
            target_row = index.row() if index.isValid() else len(ordered)
            for offset, path in enumerate(paths):
                ordered.insert(min(len(ordered), target_row + offset), path)
            self.metadata_store.reorder_folder_bookmarks(ordered)
            return
        if entry is not None:
            self._start_drop_operation(paths, entry.path, modifiers)
            return
        self._probe_dropped_folders(paths, self._add_dropped_folder_bookmarks)

    def _on_tree_paths_dropped(
        self,
        paths: tuple[str, ...],
        index: QModelIndex,
        modifiers: Qt.KeyboardModifier,
        _source: object,
    ) -> None:
        if not index.isValid():
            return
        self._start_drop_operation(
            paths,
            self.file_system_model.filePath(index),
            modifiers,
        )

    def _start_drop_operation(
        self,
        paths: tuple[str, ...],
        destination: str | Path,
        modifiers: Qt.KeyboardModifier,
    ) -> bool:
        if not paths or any(
            is_invalid_drop_target(path, destination) for path in paths
        ):
            self._show_temporary_status("この場所にはドロップできません")
            return False
        operation_name = choose_drop_operation(paths, destination, modifiers)
        operation = (
            FileOperationKind.MOVE
            if operation_name == "move"
            else FileOperationKind.COPY
        )
        if (
            operation is FileOperationKind.MOVE
            and all(
                self._same_path(Path(path).parent, Path(destination))
                for path in paths
            )
        ):
            self._show_temporary_status("同じフォルダへの移動は行いません")
            return False
        return self._start_file_operation(
            operation,
            sources=paths,
            destination=destination,
        )

    def _open_dropped_paths(self, paths: tuple[str, ...]) -> None:
        if self._open_path_handler is None:
            return
        for offset, path in enumerate(paths):
            self._invoke_open_path_handler(path, offset > 0, None)

    def _probe_dropped_folders(
        self,
        paths: tuple[str, ...],
        callback: Callable[[tuple[str, ...]], object],
    ) -> None:
        worker = FolderDropProbe(paths)
        self._drop_probe_workers.add(worker)

        def finished(folders: tuple[str, ...]) -> None:
            self._drop_probe_workers.discard(worker)
            if not self._shutdown_prepared:
                callback(folders)

        worker.signals.finished.connect(finished)
        QThreadPool.globalInstance().start(worker)

    def _add_dropped_folder_bookmarks(self, folders: tuple[str, ...]) -> None:
        if self.metadata_store is None:
            return
        added = 0
        for folder in folders:
            if self.metadata_store.add_folder_bookmark(folder):
                added += 1
        if added:
            self._show_temporary_status(f"{added}件をお気に入りへ追加しました")

    def _show_context_menu(self, position: QPoint) -> None:
        if self.list_view.consume_folder_gesture_context_menu_suppression():
            return
        index = self.list_view.indexAt(position)
        item = self.item_model.item_at(index)
        if item is not None and not self.list_view.selectionModel().isSelected(index):
            self.list_view.selectionModel().select(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )
            self.list_view.setCurrentIndex(index)
        selection_count = len(self.selected_file_operation_paths())
        busy = (
            self.file_operation_coordinator.busy
            and self.file_operation_coordinator.queue is None
        )
        menu = QMenu(self)
        unsupported = bool(
            item is not None
            and (
                item.kind is BrowserItemKind.OTHER
                or not item.openable_by_nivisviewer
            )
        )
        open_action = (
            menu.addAction(
                "既定のアプリで開く" if unsupported else "開く"
            )
            if item is not None
            else None
        )
        external_open_action = (
            menu.addAction("既定のアプリで開く")
            if item is not None
            and item.kind is not BrowserItemKind.FOLDER
            and not unsupported
            else None
        )
        new_action = (
            menu.addAction("新しいViewerWindowで開く")
            if item is not None
            else None
        )
        if open_action is not None:
            open_action.setEnabled(selection_count == 1)
        if new_action is not None:
            new_action.setEnabled(
                selection_count == 1
                and item.kind is not BrowserItemKind.FOLDER
                and not unsupported
            )
        retry_thumbnail_action = None
        if (
            item is not None
            and item.can_generate_preview
            and index.data(BrowserItemModel.ThumbnailErrorRole)
        ):
            retry_thumbnail_action = menu.addAction("サムネイルを再試行")
        if item is not None:
            menu.addSeparator()
        cut_action = menu.addAction("切り取り")
        copy_action = menu.addAction("コピー")
        paste_action = menu.addAction("貼り付け")
        cut_action.setEnabled(selection_count > 0 and not busy)
        copy_action.setEnabled(selection_count > 0 and not busy)
        paste_action.setEnabled(
            self.current_path is not None
            and not busy
            and bool(self._clipboard_paths or self._clipboard_file_urls())
        )
        menu.addSeparator()
        copy_to_action = menu.addAction("指定先へコピー...")
        move_to_action = menu.addAction("指定先へ移動...")
        copy_to_action.setEnabled(selection_count > 0 and not busy)
        move_to_action.setEnabled(selection_count > 0 and not busy)
        menu.addSeparator()
        rename_action = menu.addAction("名前の変更")
        recycle_action = menu.addAction("ごみ箱へ移動")
        rename_action.setEnabled(selection_count == 1 and not busy)
        recycle_action.setEnabled(selection_count > 0 and not busy)
        menu.addSeparator()
        new_folder_action = menu.addAction("新しいフォルダ")
        refresh_action = menu.addAction("更新")
        new_folder_action.setEnabled(self.current_path is not None and not busy)
        refresh_action.setEnabled(self.current_path is not None)
        location_action = None
        properties_action = None
        bookmark_action = None
        if item is not None:
            menu.addSeparator()
            location_action = menu.addAction("エクスプローラーで場所を開く")
            properties_action = menu.addAction("プロパティ")
            properties_action.setEnabled(False)
        if item is not None and self.metadata_store is not None:
            menu.addSeparator()
            if self.metadata_store.is_browser_bookmarked(str(item.path)):
                bookmark_action = menu.addAction("ブックマークから削除")
            else:
                bookmark_action = menu.addAction("ブックマークに追加")
        selected = menu.exec(self.list_view.viewport().mapToGlobal(position))
        if open_action is not None and selected == open_action:
            if unsupported:
                assert item is not None
                self._open_system_file(item.path)
            else:
                self.open_item(index)
        elif external_open_action is not None and selected == external_open_action:
            assert item is not None
            self._open_system_file(item.path)
        elif new_action is not None and selected == new_action:
            self.open_item(index, open_in_new_window=True)
        elif (
            retry_thumbnail_action is not None
            and selected == retry_thumbnail_action
        ):
            assert item is not None
            self.item_model.clear_thumbnail_error(item.path)
            self.thumbnail_provider.retry(
                item,
                self.thumbnail_render_spec,
                generation=self._generation,
            )
        elif selected == cut_action:
            self.cut_selected_items()
        elif selected == copy_action:
            self.copy_selected_items()
        elif selected == paste_action:
            self.paste_items()
        elif selected == copy_to_action:
            self.copy_selected_to()
        elif selected == move_to_action:
            self.move_selected_to()
        elif selected == rename_action:
            self.rename_selected_item()
        elif selected == recycle_action:
            self.move_selected_to_recycle_bin()
        elif selected == new_folder_action:
            self.create_new_folder()
        elif selected == refresh_action:
            self.refresh_current_folder()
        elif location_action is not None and selected == location_action:
            assert item is not None
            target = item.path if item.kind == BrowserItemKind.FOLDER else item.path.parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        elif bookmark_action is not None and selected == bookmark_action:
            assert item is not None
            if self.metadata_store is None:
                return
            if self.metadata_store.is_browser_bookmarked(str(item.path)):
                self.remove_browser_bookmark(item.path)
            else:
                self.add_browser_bookmark(
                    item.path,
                    item_type=self._bookmark_item_type(item),
                    label=item.display_name,
                )

    def _show_bookmark_context_menu(self, position: QPoint) -> None:
        index = self.bookmark_view.indexAt(position)
        entry = self.bookmark_model.entry_at(index)
        if entry is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction("開く")
        new_action = menu.addAction("新しいViewerWindowで開く")
        if entry.item_type == "folder":
            new_action.setEnabled(False)
        menu.addSeparator()
        remove_action = menu.addAction("ブックマークから削除")
        selected = menu.exec(self.bookmark_view.viewport().mapToGlobal(position))
        if selected == open_action:
            self.open_bookmark(index)
        elif selected == new_action:
            self.open_bookmark(index, open_in_new_window=True)
        elif selected == remove_action:
            self.remove_browser_bookmark(entry.path)

    def _show_folder_bookmark_context_menu(self, position: QPoint) -> None:
        index = self.favorite_view.indexAt(position)
        entry = self.folder_bookmark_model.entry_at(index)
        if entry is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction("移動")
        location_action = menu.addAction("エクスプローラーで場所を開く")
        menu.addSeparator()
        rename_action = menu.addAction("表示名を変更")
        move_up_action = menu.addAction("上へ移動")
        move_down_action = menu.addAction("下へ移動")
        remove_action = menu.addAction("お気に入りから削除")
        menu.addSeparator()
        copy_here_action = menu.addAction("選択項目をここへコピー")
        move_here_action = menu.addAction("選択項目をここへ移動")
        has_selection = bool(self.selected_file_operation_paths())
        busy = (
            self.file_operation_coordinator.busy
            and self.file_operation_coordinator.queue is None
        )
        copy_here_action.setEnabled(has_selection and not busy and entry.exists is not False)
        move_here_action.setEnabled(has_selection and not busy and entry.exists is not False)
        move_up_action.setEnabled(index.row() > 0)
        move_down_action.setEnabled(
            0 <= index.row() < self.folder_bookmark_model.rowCount() - 1
        )
        selected = menu.exec(self.favorite_view.viewport().mapToGlobal(position))
        if selected == open_action:
            self.open_folder_bookmark(index)
        elif selected == location_action:
            QDesktopServices.openUrl(QUrl.fromLocalFile(entry.path))
        elif selected == rename_action:
            self.rename_folder_bookmark(index)
        elif selected == move_up_action:
            self.move_folder_bookmark(index, -1)
        elif selected == move_down_action:
            self.move_folder_bookmark(index, 1)
        elif selected == remove_action and self.metadata_store is not None:
            self.metadata_store.remove_folder_bookmark(entry.path)
        elif selected == copy_here_action:
            self.copy_selected_to_folder_bookmark(index)
        elif selected == move_here_action:
            self.move_selected_to_folder_bookmark(index)

    def copy_selected_to_folder_bookmark(self, index: QModelIndex) -> bool:
        entry = self.folder_bookmark_model.entry_at(index)
        if entry is None or entry.exists is False:
            return False
        return self.copy_selected_to(entry.path)

    def move_selected_to_folder_bookmark(self, index: QModelIndex) -> bool:
        entry = self.folder_bookmark_model.entry_at(index)
        if entry is None or entry.exists is False:
            return False
        return self.move_selected_to(entry.path)

    def _show_folder_tree_context_menu(self, position: QPoint) -> None:
        index = self.folder_tree.indexAt(position)
        menu = QMenu(self)
        full_tree_action = menu.addAction("ツリーのルートを戻す")
        menu.addSeparator()
        copy_here_action = menu.addAction("選択項目をここへコピー")
        move_here_action = menu.addAction("選択項目をここへ移動")
        has_selection = bool(self.selected_file_operation_paths())
        busy = (
            self.file_operation_coordinator.busy
            and self.file_operation_coordinator.queue is None
        )
        copy_here_action.setEnabled(index.isValid() and has_selection and not busy)
        move_here_action.setEnabled(index.isValid() and has_selection and not busy)
        selected = menu.exec(self.folder_tree.viewport().mapToGlobal(position))
        if selected == full_tree_action:
            self.folder_tree_sync.show_full_tree()
        elif selected == copy_here_action:
            self.copy_selected_to_tree_index(index)
        elif selected == move_here_action:
            self.move_selected_to_tree_index(index)

    def copy_selected_to_tree_index(self, index: QModelIndex) -> bool:
        if not index.isValid():
            return False
        return self.copy_selected_to(self.file_system_model.filePath(index))

    def move_selected_to_tree_index(self, index: QModelIndex) -> bool:
        if not index.isValid():
            return False
        return self.move_selected_to(self.file_system_model.filePath(index))

    def _show_history_context_menu(self, position: QPoint) -> None:
        index = self.history_view.indexAt(position)
        entry = self.history_model.entry_at(index)
        if entry is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction("開く")
        new_action = menu.addAction("新しいViewerWindowで開く")
        location_action = menu.addAction("親フォルダを表示")
        menu.addSeparator()
        remove_action = menu.addAction("この履歴を削除")
        clear_action = menu.addAction("閲覧履歴をすべて消去...")
        selected = menu.exec(self.history_view.viewport().mapToGlobal(position))
        if selected == open_action:
            self.open_history(index)
        elif selected == new_action:
            self.open_history(index, open_in_new_window=True)
        elif selected == location_action:
            self.show_history_location(index)
        elif selected == remove_action:
            self.remove_history_entry(index)
        elif selected == clear_action:
            self.clear_history()

    @staticmethod
    def _bookmark_item_type(item: BrowserItem) -> str:
        if item.kind == BrowserItemKind.FOLDER:
            return "folder"
        if item.kind == BrowserItemKind.ARCHIVE:
            return "archive"
        if item.kind == BrowserItemKind.PDF:
            return "pdf"
        if item.kind == BrowserItemKind.OTHER:
            return "other"
        return "image"

    @staticmethod
    def _safe_thumbnail_size(value: object) -> int:
        try:
            size = int(value)
        except (TypeError, ValueError):
            size = 180
        return max(96, min(384, size))

    @staticmethod
    def _select_combo_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _safe_sidebar_width(value: object) -> int:
        try:
            width = int(value)
        except (TypeError, ValueError):
            width = 280
        return max(120, min(1200, width))
