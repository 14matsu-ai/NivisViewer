from __future__ import annotations

from .browser_workflow_controller import BrowserWorkflowController
from .browser_workflow_policy import paste_is_move

from .i18n import tr
from .menu_icons import install_text_icon_menu_style, settings_icon


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
    QRect,
    QSize,
    Qt,
    QTimer,
    QThreadPool,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QCloseEvent,
    QClipboard,
    QContextMenuEvent,
    QDesktopServices,
    QColor,
    QFontMetrics,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPalette,
    QIcon,
    QPixmap,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QToolButton,
    QTreeView,
    QWidget,
    QWidgetAction,
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
from .browser_directory_watcher import (
    BrowserDirectoryChange,
    BrowserDirectoryWatcher,
)
from .app_icon import install_window_icon
from .browser_main_drop import (
    BrowserMainDropController,
    PendingBrowserFocusRequest,
)
from .browser_address_bar import BrowserAddressBar
from .browser_location_bar import (
    BrowserLocationBreadcrumb,
    BrowserLocationListPopup,
    LocationDirectoryLoader,
    LocationDirectoryResult,
    LocationPopupEntry,
)
from .browser_item_delegate import (
    BrowserItemDelegate,
)
from .browser_icon_size import (
    ICON_SIZE_SETTING_SPECS,
    ICON_POSITION_SETTING_KEYS,
    normalize_browser_icon_margin,
    normalize_browser_icon_size_custom_percent,
    normalize_browser_icon_size_preset,
)
from .browser_navigation import BrowserLocation, BrowserNavigationHistory
from .shortcut_catalog import canonical_key, normalize_shortcut_bindings
from .browser_scanner import (
    DEFAULT_SCAN_BATCH_SIZE,
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
    BROWSER_SORT_CHOICES,
    browser_sort_choice_index,
    new_browser_random_seed,
    normalize_browser_random_seed,
    BrowserDisplayDensity,
    BrowserSortKey,
    BrowserSortOrder,
    BrowserSortPolicy,
    normalize_browser_display_density,
    normalize_browser_sort_key,
    normalize_browser_sort_order,
)
from .browser_filter import BrowserFilterState, RatingFilterMode
from .browser_rating_filter_widget import BrowserRatingFilterWidget
from .browser_tag_quick_filters import BrowserTagQuickFilterStrip, TagFilterMenuButton
from .browser_search_history import BrowserSearchHistory
from .browser_image_detail import (
    BrowserImageDetailProbe,
    BrowserImageDetailResult,
)
from .browser_thumbnail_scheduler import (
    ThumbnailPriority,
    build_thumbnail_request_plan,
    calculate_grid_visible_range,
)
from .browser_visibility import BrowserVisibilityPolicy
from .browser_folder_snapshot_cache import (
    DEFAULT_MAX_ENTRIES,
    BrowserFolderSnapshotCache,
    normalize_browser_folder_snapshot_cache_max_entries,
)
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
from .file_properties_dialog import FilePropertiesDialog
from .file_operation_artifact import FileOperationArtifactPolicy
from .file_operation_coordinator import FileOperationCoordinator
from .file_operation_panel import FileOperationPanel
from .file_operation_plan import ConflictResolution, FileOperationPlan, FileConflictKind
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
    ClipboardPasteReceipt,
    InternalClipboardOperation,
    InternalClipboardState,
)
from .metadata_store import MetadataStore
from .path_availability import PathAvailabilityService
from .rating_rename_service import RatingRenameService
from .zippla_filename_metadata import ZipPlaFilenameMetadata
from .performance_trace import performance_trace
from .settings_dialog import SettingsDialog
from .sidebar_layout import SidebarLayoutController
from .thumbnail_provider import BrowserThumbnailProvider
from .thumbnail_disk_cache import ThumbnailDiskCache
from .thumbnail_render import ThumbnailRenderPolicy, ThumbnailRenderSpec, normalize_thumbnail_webp_quality
from .system_file_opener import SystemFileOpener
from .windows_filename import (
    generate_numbered_name,
    validate_windows_filename,
)


BROWSER_SHORTCUT_RUNTIME_IDS = frozenset(
    {
        "browser_back",
        "browser_forward",
        "browser_up",
        "browser_refresh",
        "browser_focus_address",
        "browser_focus_search",
        "browser_undo",
        "browser_rename",
        "browser_delete",
        "browser_copy",
        "browser_cut",
        "browser_paste",
        "browser_new_folder",
        "browser_toggle_folder_bookmark",
        "browser_backspace",
        "browser_cancel",
        "browser_clear_filters",
        "browser_open_selection",
    }
)


BrowserOpenHandler = Callable[..., object]
AffectedViewersHandler = Callable[[tuple[str, ...]], tuple[object, ...]]
CloseAffectedViewersHandler = Callable[[tuple[object, ...]], bool | None]
FolderNavigationHandler = Callable[[object, int], str]
_THUMBNAIL_LOG = logging.getLogger("nivisviewer.thumbnail")
_FILE_OPERATION_LOG = logging.getLogger("nivisviewer.file_operation")
BROWSER_NAVIGATION_TOOLBAR_MIN_HEIGHT = 27
BROWSER_NAVIGATION_TOOLBAR_MARGINS = (1, 0, 1, 0)
BROWSER_NAVIGATION_BUTTON_SIZE = 26
BROWSER_NAVIGATION_ICON_SIZE = 20
BROWSER_CHROME_CONTROL_HEIGHT = 24
BROWSER_CHROME_CONTROL_SPACING = 2
BROWSER_STATUS_BAR_SPACING = 3
BROWSER_STATUS_LEFT_SPACING = 12
BROWSER_STATUS_DETAIL_SPACING = 14
BROWSER_STATUS_BAR_MIN_IDLE_HEIGHT = 22
BROWSER_DIRECTORY_CHANGE_COALESCE_MS = 350


@dataclass(frozen=True)
class _ListViewState:
    selected_paths: tuple[str, ...]
    current_path: str | None
    anchor_path: str | None
    anchor_row: int
    anchor_x: int
    anchor_y: int
    vertical_scroll: int
    horizontal_scroll: int


@dataclass
class _RatingRenameBatch:
    rating: int | None
    view_state: _ListViewState
    pending_folders: list[tuple[str, str]] = field(default_factory=list)
    replacements: list[tuple[str, str, int | None]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    active_request_id: int | None = None
    tag_edit: bool = False


@dataclass
class _PendingDirectoryScan:
    path: Path
    generation: int
    record_history: bool
    restore_location: BrowserLocation
    refresh: bool
    failure_history_revert: str | int | None = None
    committed: bool = False
    refresh_entries: list[BrowserScanEntry] = field(default_factory=list)
    buffered_entries: list[BrowserScanEntry] = field(default_factory=list)
    scanned_count: int = 0
    remaining_items: tuple[BrowserItem, ...] = ()
    remaining_item_offset: int = 0
    trace_id: int = 0
    navigation_source: str = "interactive"
    atomic_restore: bool = False
    directory_watch_dirty: bool = False
    first_batch_arrived: bool = False
    first_batch_applied: bool = False
    snapshot_hit: bool = False


class _BrowserContextFilenameEdit(QLineEdit):
    """Read-only menu text selection; never a filename mutation surface."""

    def __init__(self, name: str, menu: QMenu) -> None:
        super().__init__(menu)
        self._menu = menu
        self.setObjectName("browser_context_filename")
        self.setReadOnly(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.setAccessibleName(tr('ファイル名（読み取り専用）'))
        self.setText(name)
        self.setCursorPosition(0)
        self.setTextMargins(6, 2, 6, 2)
        screen = menu.screen()
        maximum = (
            min(480, max(80, screen.availableGeometry().width() - 80))
            if screen else 480
        )
        text_width = self.fontMetrics().horizontalAdvance(name) + 24
        self.setFixedWidth(min(maximum, max(240, text_width)))

    def copy_selected_text(self) -> bool:
        text = self.selectedText()
        if not text:
            return False
        mime = QMimeData()
        mime.setText(text)
        QApplication.clipboard().setMimeData(mime)
        return True

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride:
            # Keep all Browser/file shortcuts out while this editor owns focus.
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selected_text()
        elif event.matches(QKeySequence.StandardKey.SelectAll):
            self.selectAll()
        elif event.key() == Qt.Key.Key_Escape:
            self._menu.close()
        elif event.key() in {Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Tab, Qt.Key.Key_Backtab}:
            actions = [
                action for action in self._menu.actions()
                if action.isEnabled() and not action.isSeparator()
                and not isinstance(action, QWidgetAction)
            ]
            self._menu.setFocus(Qt.FocusReason.OtherFocusReason)
            if actions:
                backwards = event.key() in {Qt.Key.Key_Up, Qt.Key.Key_Backtab}
                self._menu.setActiveAction(actions[-1] if backwards else actions[0])
        elif (
            event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End}
            and not event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            super().keyPressEvent(event)  # Includes Shift/Ctrl selection navigation.
        # Typing, Enter, Delete, F2, cut/paste and other shortcuts are inert.
        event.accept()

    def focusOutEvent(self, event) -> None:
        start, text = self.selectionStart(), self.selectedText()
        super().focusOutEvent(event)
        if start >= 0 and text:
            # QLineEdit positions count UTF-16 units, including surrogate pairs.
            self.setSelection(start, len(text.encode("utf-16-le", errors="surrogatepass")) // 2)


class _BrowserSearchEdit(QLineEdit):
    """Compact search editor hosted by the integrated history control."""

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        return QSize(170, hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        hint = super().minimumSizeHint()
        return QSize(100, hint.height())


class _BrowserSortItemDelegate(QStyledItemDelegate):
    """Font-aware compact rows, independent of native combo menu padding."""

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QSize:
        hint = super().sizeHint(option, index)
        item_option = QStyleOptionViewItem(option)
        self.initStyleOption(item_option, index)
        # Use the actual font height with no added vertical row padding.
        hint.setHeight(item_option.fontMetrics.height())
        return hint

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        prepared = QStyleOptionViewItem(option)
        self.initStyleOption(prepared, index)
        highlighted = QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver
        if not prepared.state & highlighted:
            super().paint(painter, option, index)
            return
        group = QPalette.ColorGroup.Active
        if not prepared.state & QStyle.StateFlag.State_Enabled:
            group = QPalette.ColorGroup.Disabled
        elif not prepared.state & QStyle.StateFlag.State_Active:
            group = QPalette.ColorGroup.Inactive
        painter.save()
        try:
            painter.setClipRect(prepared.rect, Qt.ClipOperation.IntersectClip)
            painter.fillRect(prepared.rect, prepared.palette.brush(group, QPalette.ColorRole.Highlight))
            # Own only the full-row highlight. Native Windows 11 item painting
            # otherwise insets/rounds it even when adjacent visualRects touch.
            # Keep native text layout, but don't paint that second highlight.
            prepared.state &= ~(highlighted | QStyle.StateFlag.State_HasFocus)
            prepared.backgroundBrush = QBrush(Qt.BrushStyle.NoBrush)
            prepared.features &= ~QStyleOptionViewItem.ViewItemFeature.Alternate
            prepared.palette.setCurrentColorGroup(group)
            prepared.palette.setColor(group, QPalette.ColorRole.Text,
                                      prepared.palette.color(group, QPalette.ColorRole.HighlightedText))
            style = prepared.widget.style() if prepared.widget else QApplication.style()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, prepared, painter, prepared.widget)
        finally:
            painter.restore()


class _BrowserDropDownShell(QComboBox):
    """Native combo chrome around a custom Browser content widget."""

    dropDownRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._content_widget: QWidget | None = None
        self._drop_down_available = True
        self._preferred_width: int | None = None
        self.addItem("")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_preferred_width(self, width: int | None) -> None:
        self._preferred_width = None if width is None else max(1, int(width))
        self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        if self._preferred_width is None:
            return hint
        return QSize(self._preferred_width, hint.height())

    def set_content_widget(self, widget: QWidget) -> None:
        self._content_widget = widget
        widget.setParent(self)
        widget.show()
        self.setFocusProxy(widget)
        self._layout_content()

    def set_drop_down_available(self, available: bool) -> None:
        self._drop_down_available = bool(available)

    def drop_down_rect(self) -> QRect:
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        return self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxArrow,
            self,
        )

    def edit_field_rect(self) -> QRect:
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        return self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )

    def _layout_content(self) -> None:
        content = getattr(self, "_content_widget", None)
        if content is None:
            return
        content.setGeometry(self.edit_field_rect())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_content()

    def event(self, event: QEvent) -> bool:
        result = super().event(event)
        if event.type() in {
            QEvent.Type.StyleChange,
            QEvent.Type.FontChange,
            QEvent.Type.PaletteChange,
        }:
            self._layout_content()
        return result

    def showPopup(self) -> None:  # noqa: N802
        if self._drop_down_available:
            self.dropDownRequested.emit()


_CLEAR_SEARCH_HISTORY = object()


class BrowserWindow(QMainWindow):
    activated = Signal(object)
    closing = Signal(object)
    directory_scan_committed = Signal(str)
    location_changed = Signal(object, str)
    search_query_edited = Signal(object, str)

    def __init__(
        self,
        *,
        config_manager: ConfigManager,
        open_path_handler: BrowserOpenHandler | None = None,
        discovery: BrowserItemDiscovery | None = None,
        scanner: BrowserDirectoryScanner | None = None,
        directory_watcher: BrowserDirectoryWatcher | None = None,
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
        self.setWindowTitle(tr('NivisViewer - ブラウザ'))
        install_window_icon(self)
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
        self.file_operation_coordinator.operation_completed.connect(
            self._release_deferred_directory_change
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
        self.folder_snapshot_cache = BrowserFolderSnapshotCache(
            enabled=bool(
                self.settings.get("browser_folder_snapshot_cache_enabled", True)
            ),
            max_entries=normalize_browser_folder_snapshot_cache_max_entries(
                self.settings.get(
                    "browser_folder_snapshot_cache_max_entries",
                    DEFAULT_MAX_ENTRIES,
                )
            ),
        )
        self._snapshot_reconcile_pending = False
        self._owns_directory_watcher = directory_watcher is None
        self.directory_watcher = directory_watcher or BrowserDirectoryWatcher(
            self
        )
        self.directory_watcher.directory_changed.connect(
            self._on_directory_changed
        )
        self.location_directory_loader = LocationDirectoryLoader(self)
        self.location_directory_loader.completed.connect(
            self._on_location_directory_loaded
        )
        self._location_directory_menu: BrowserLocationListPopup | None = None
        self._location_directory_generation = 0
        self._location_directory_pending_path: str | None = None
        self._location_directory_menu_path: str | None = None
        self._navigation_history_menu: BrowserLocationListPopup | None = None
        self._location_history_popup: BrowserLocationListPopup | None = None
        self._search_history_popup: BrowserLocationListPopup | None = None
        if thumbnail_provider is None:
            disk_cache = ThumbnailDiskCache(
                self.config.thumbnail_cache_dir,
                # Opening the SQLite index may include directory creation and
                # aggregate queries.  The provider opens it on its worker when
                # the first thumbnail is requested, so it must not delay the
                # first Browser window paint.
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
        page_count_ready = getattr(
            self.thumbnail_provider,
            "page_count_ready",
            None,
        )
        if page_count_ready is not None:
            page_count_ready.connect(self._on_page_count_ready)
        resumed = getattr(self.thumbnail_provider, "scheduling_resumed", None)
        if resumed is not None:
            resumed.connect(self._schedule_thumbnail_requests)
        self.current_path: Path | None = None
        self.navigation_history = BrowserNavigationHistory(
            recent_limit=int(
                self.settings.get("browser_location_history_limit", 50)
            )
        )
        self.search_history = BrowserSearchHistory(
            self.settings.get("browser_search_history", []),
            limit=int(self.settings.get("browser_search_history_limit", 50)),
        )
        self._generation = self.thumbnail_provider.generation
        self._scan_generation = 0
        self._pending_scan: _PendingDirectoryScan | None = None
        self._directory_watch_generation = 0
        self._directory_watch_path: Path | None = None
        self._directory_change_pending = False
        self._pending_tree_navigation_path: Path | None = None
        self._deferred_tree_sync_generation: int | None = None
        self._first_paint_pending_generation: int | None = None
        self._first_paint_trace_id = 0
        self._tree_trace_ids: dict[int, int] = {}
        self._favorite_trace_id = 0
        self._favorite_release_navigated = False
        self._location_restore_token = 0
        self._list_view_restore_token = 0
        self._restoring_list_view_state = False
        self._status_message_token = 0
        self._temporary_status_message: str | None = None
        self._screen_tracking_window = None
        self._pressed_extra_buttons: set[Qt.MouseButton] = set()
        self._shutdown_prepared = False
        self._zip_progress_dialog: QProgressDialog | None = None
        self._zip_progress_request_id: int | None = None
        self._fast_scrolling = False
        self._thumbnail_scroll_direction = 1
        self._last_scroll_value = 0
        self._last_scroll_time = 0.0
        self._file_operation_request_id = 0
        self._active_file_operation_id: int | None = None
        self._close_after_operation = False
        self._close_after_cancel = False
        self._cancel_requested = False
        self._close_dialog_visible = False
        self._close_operation_id: str | None = None
        self._deferred_close_queued = False
        self._close_authorized = False
        self._internal_clipboard_state = InternalClipboardState()
        self._clipboard_paths: tuple[str, ...] = ()
        self._clipboard_cut = False
        self._setting_clipboard = False
        self._operation_restore_paths: tuple[str, ...] = ()
        self._operation_restore_row: int | None = None
        self._operation_refresh_generation: int | None = None
        self._operation_completion_message: str | None = None
        self._file_operation_requests: dict[int, FileOperationRequest] = {}
        self._clipboard_paste_receipts: dict[int, ClipboardPasteReceipt] = {}
        self._properties_dialogs: set[FilePropertiesDialog] = set()
        self._property_rename_requests: dict[
            int, tuple[FilePropertiesDialog, bool]
        ] = {}
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
        self.browser_folder_fallback_background = str(
            self.settings.get("browser_folder_fallback_background", "auto")
        )
        self.browser_file_fallback_background = str(
            self.settings.get("browser_file_fallback_background", "auto")
        )
        self.browser_center_folder_icon_size = normalize_browser_icon_size_preset(
            self.settings.get("browser_center_folder_icon_size")
        )
        self.browser_center_folder_icon_custom_percent = normalize_browser_icon_size_custom_percent(
            self.settings.get("browser_center_folder_icon_custom_percent")
        )
        self.browser_center_file_icon_size = normalize_browser_icon_size_preset(
            self.settings.get("browser_center_file_icon_size")
        )
        self.browser_center_file_icon_custom_percent = normalize_browser_icon_size_custom_percent(
            self.settings.get("browser_center_file_icon_custom_percent")
        )
        self.browser_badge_folder_icon_size = normalize_browser_icon_size_preset(
            self.settings.get("browser_badge_folder_icon_size")
        )
        self.browser_badge_folder_icon_custom_percent = normalize_browser_icon_size_custom_percent(
            self.settings.get("browser_badge_folder_icon_custom_percent")
        )
        self.browser_badge_file_icon_size = normalize_browser_icon_size_preset(
            self.settings.get("browser_badge_file_icon_size")
        )
        self.browser_badge_file_icon_custom_percent = normalize_browser_icon_size_custom_percent(
            self.settings.get("browser_badge_file_icon_custom_percent")
        )
        self.browser_wheel_scroll_mode = str(
            self.settings.get("browser_wheel_scroll_mode", "system")
        )
        for key in ICON_POSITION_SETTING_KEYS:
            setattr(self, key, normalize_browser_icon_margin(self.settings.get(key, -1)))
        self.browser_wheel_scroll_custom_rows = int(
            self.settings.get("browser_wheel_scroll_custom_rows", 3)
        )
        self.thumbnail_quality_mode = str(
            self.settings.get("thumbnail_quality_mode", "auto")
        )
        self.thumbnail_cache_max_edge = max(
            256,
            min(2048, int(self.settings.get("thumbnail_cache_max_edge", 1024))),
        )
        self.thumbnail_webp_quality = normalize_thumbnail_webp_quality(
            self.settings.get("thumbnail_webp_quality")
        )
        self.thumbnail_preserve_alpha = self.settings.get("thumbnail_preserve_alpha", False) is True
        self._thumbnail_dpr = self._current_device_pixel_ratio()
        self.thumbnail_render_spec = self._build_thumbnail_render_spec()
        self.thumbnail_provider.set_disk_cache_encoding_policy(self.thumbnail_render_spec.encoding_policy)
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
        self.browser_random_seed = normalize_browser_random_seed(self.settings.get("browser_random_seed"))
        self.browser_folders_first = bool(
            self.settings.get("browser_folders_first", True)
        )
        # Search text and quick rating filters are intentionally session-only:
        # reopening the app must never start with files unexpectedly hidden.
        self.browser_filter_state = BrowserFilterState()
        self.browser_tag_grouped = bool(
            self.settings.get("browser_tag_grouped", False)
        )
        self.browser_display_density = normalize_browser_display_density(
            self.settings.get(
                "browser_display_density",
                BrowserDisplayDensity.STANDARD.value,
            )
        )
        self.browser_item_spacing_x = max(
            0, min(32, int(self.settings.get("browser_item_spacing_x", 0)))
        )
        self.browser_item_spacing_y = max(
            0, min(32, int(self.settings.get("browser_item_spacing_y", 0)))
        )
        self.browser_cell_padding = max(
            0, min(12, int(self.settings.get("browser_cell_padding", 0)))
        )
        self.browser_filename_display = str(
            self.settings.get("browser_filename_display", "one_line")
        )
        self.browser_filename_elide_mode = str(
            self.settings.get("browser_filename_elide_mode", "right")
        )
        self.browser_filename_font_size = max(
            0, min(24, int(self.settings.get("browser_filename_font_size", 0)))
        )
        self.browser_filename_show_extension = bool(
            self.settings.get("browser_filename_show_extension", True)
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
        self._rating_hover_path: str | None = None
        self._rating_press: tuple[str, int | None, Qt.MouseButton] | None = None
        self._rating_batch: _RatingRenameBatch | None = None
        self._detail_generation = 0
        self._detail_request_identity: tuple[int, str] | None = None
        self._page_count_request_identity: tuple[int, str] | None = None

        self.rating_rename_service = RatingRenameService()
        self.image_detail_probe = BrowserImageDetailProbe(self)
        self.image_detail_probe.completed.connect(
            self._on_image_detail_completed
        )

        self._folder_change_timer = QTimer(self)
        self._folder_change_timer.setSingleShot(True)
        self._folder_change_timer.setInterval(120)
        self._folder_change_timer.timeout.connect(self._apply_pending_tree_path)

        self._browser_search_timer = QTimer(self)
        self._browser_search_timer.setSingleShot(True)
        self._browser_search_timer.setInterval(100)
        self._browser_search_timer.timeout.connect(
            self._apply_pending_browser_search
        )

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
        self._directory_change_timer = QTimer(self)
        self._directory_change_timer.setSingleShot(True)
        self._directory_change_timer.setInterval(
            BROWSER_DIRECTORY_CHANGE_COALESCE_MS
        )
        self._directory_change_timer.timeout.connect(
            self._flush_directory_changes
        )
        self._build_ui()
        self._browser_workflow = BrowserWorkflowController(self)
        self.shortcut_bindings = normalize_shortcut_bindings(
            self.settings.get("shortcut_bindings")
        ).get("browser", {})
        self.browser_cancel_clears_filters = bool(
            self.settings.get("browser_cancel_clears_filters", True)
        )
        self._apply_browser_shortcuts()
        application = QApplication.instance()
        if application is not None:
            # Side-button events can target a viewport child or delegate
            # helper while history restore replaces the model. Observe them
            # before the target interprets the packet as an ordinary click.
            application.installEventFilter(self)
        self._refresh_thumbnail_encoding_policy()
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
        *,
        snapshot_items: tuple[BrowserItem, ...] | None = None,
    ) -> AdjacentBookBrowserSnapshot | None:
        """Return committed model data without querying the filesystem."""
        if (
            self.current_path is None
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
                    openable_by_nivisviewer=item.openable_by_nivisviewer,
                    created_time_ns=item.created_time_ns,
                    accessed_time_ns=item.accessed_time_ns,
                )
                for item in (
                    self._visible_order_snapshot_items()
                    if snapshot_items is None else snapshot_items
                )
            ),
            sort_identity=(
                f"{self.browser_sort_key.value}:"
                f"{self.browser_sort_order.value}:"
                f"folders_first={int(self.browser_folders_first)}"
                + (f":seed={self.browser_random_seed}" if self.browser_sort_key is BrowserSortKey.RANDOM else "")
            ),
            filter_identity=(
                f"hidden={int(self.browser_show_hidden_items)}:"
                f"unsupported={int(self.browser_show_unsupported_files)}:"
                f"system={int(self.browser_show_system_items)}:"
                f"search={self.browser_filter_state.search_text.casefold()!r}:"
                f"rating={self.browser_filter_state.rating_mode.value}:"
                f"reference={self.browser_filter_state.rating_reference}"
                f":tags={(self.browser_filter_state.include_tags, self.browser_filter_state.exclude_tags, self.browser_filter_state.tag_match)!r}"
            ),
        )

    def show_initial(self) -> None:
        self.show()

    def wait_for_scan(self, msecs: int = 5000) -> bool:
        """Diagnostic/test helper; normal UI code must not wait for scans."""
        wait = getattr(self.scanner, "wait_for_done", None)
        deadline = monotonic() + max(0, int(msecs)) / 1000.0
        while monotonic() < deadline:
            remaining_msecs = max(
                1,
                int((deadline - monotonic()) * 1000),
            )
            if callable(wait):
                wait(min(100, remaining_msecs))
            for _ in range(3):
                QCoreApplication.processEvents()
            pending = self._pending_scan
            if (
                pending is not None
                and pending.committed
                and pending.remaining_items
            ):
                self._scan_batch_timer.stop()
                self._flush_pending_scan_batch()
                continue
            if pending is None:
                return True
        return self._pending_scan is None

    def navigate_to(
        self,
        path: str | Path,
        *,
        record_history: bool = True,
        restore_location: BrowserLocation | None = None,
        force_reload: bool = False,
        capture_current: bool = True,
        failure_history_revert: str | int | None = None,
        navigation_source: str = "interactive",
        trace_id: int = 0,
        atomic_restore: bool = False,
    ) -> bool:
        if trace_id:
            performance_trace.mark(trace_id, "navigation.navigate_to.called")
        if not self._starting_drop_focus_navigation:
            self._cancel_browser_drop_focus()
        target = self._absolute_browser_path(path)
        if self._snapshot_reconcile_pending and not self._same_path(
            self.current_path,
            target,
        ):
            self._snapshot_reconcile_pending = False

        pending = self._pending_scan
        if (
            pending is not None
            and self._same_path(pending.path, target)
            and not force_reload
        ):
            pending.restore_location = (
                restore_location or pending.restore_location
            )
            pending.atomic_restore = pending.atomic_restore or atomic_restore
            return True

        same_path = self._same_path(self.current_path, target)
        snapshot_reconcile_requested = bool(
            self._snapshot_reconcile_pending and same_path and force_reload
        )
        if same_path and not force_reload:
            if pending is not None:
                self._cancel_pending_scan(
                    rollback_history=not atomic_restore,
                )
                self._restore_current_directory_watch()
            if restore_location is not None:
                self._schedule_location_restore(restore_location)
            if navigation_source != "favorite":
                self._sync_tree_to_path(target)
            self._sync_address_bar()
            self._update_navigation_actions()
            return True

        if not force_reload and not same_path:
            if capture_current:
                self._update_current_navigation_state()
            snapshot = self.folder_snapshot_cache.get(
                target,
                self._current_browser_visibility_policy(),
                self._current_browser_sort_policy(),
            )
            if snapshot is not None:
                try:
                    target_exists = target.is_dir()
                except OSError:
                    target_exists = False
                if target_exists:
                    return self._navigate_from_folder_snapshot(
                        target,
                        snapshot.items,
                        record_history=record_history,
                        restore_location=(
                            restore_location or BrowserLocation(str(target))
                        ),
                        failure_history_revert=failure_history_revert,
                        navigation_source=navigation_source,
                        trace_id=trace_id,
                        atomic_restore=atomic_restore,
                    )

        self._cancel_pending_scan(rollback_history=not atomic_restore)
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
            visibility_policy=self._current_browser_visibility_policy(),
            priority=(
                BrowserScanPriority.REFRESH
                if force_reload
                else BrowserScanPriority.INTERACTIVE_NAVIGATION
            ),
            trace_id=trace_id,
            sort_policy=self._current_browser_sort_policy(),
            include_progress_entries=False,
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
            navigation_source=(
                "snapshot_reconcile"
                if snapshot_reconcile_requested
                else navigation_source
            ),
            atomic_restore=atomic_restore,
        )
        if not self._same_path(self._directory_watch_path, target):
            self._set_active_directory_watch(target)
        self.address_bar.setText(str(target))
        if not self.scanner.start(request):
            self._pending_scan = None
            self._restore_current_directory_watch()
            self._show_temporary_status(tr('フォルダへアクセスできません'))
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
        pending.scanned_count += batch.item_count
        if pending.trace_id and not pending.first_batch_arrived:
            pending.first_batch_arrived = True
            performance_trace.mark(
                pending.trace_id,
                "scanner.first_batch.gui_arrived",
                str(batch.item_count),
            )
        if not batch.final_items_pending:
            if pending.refresh:
                pending.refresh_entries.extend(batch.entries)
            else:
                pending.buffered_entries.extend(batch.entries)
        self._schedule_scan_status_update()

    def _flush_pending_scan_batch(self) -> None:
        pending = self._pending_scan
        if (
            pending is None
            or pending.refresh
            or not pending.committed
            or not pending.remaining_items
        ):
            return
        if pending.snapshot_hit:
            batch_end = min(
                len(pending.remaining_items),
                pending.remaining_item_offset + DEFAULT_SCAN_BATCH_SIZE * 4,
            )
            remaining = pending.remaining_items[
                pending.remaining_item_offset : batch_end
            ]
            pending.remaining_item_offset = batch_end
        else:
            remaining = pending.remaining_items[pending.remaining_item_offset :]
            pending.remaining_items = ()
            pending.remaining_item_offset = 0
        self.item_model.append_final_directory_scan(
            remaining,
            generation=pending.generation,
        )
        if pending.snapshot_hit and pending.remaining_item_offset < len(
            pending.remaining_items
        ):
            self._scan_batch_timer.start(0)
            self._schedule_scan_status_update()
            return
        pending.remaining_items = ()
        pending.remaining_item_offset = 0
        self.item_model.finish_directory_scan(generation=pending.generation)
        self._restore_pending_scan_location(pending, final=True)
        self._finish_pending_scan(pending)

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
            self._discard_pending_scan_buffers(pending)
            self._pending_scan = None
            self._pending_browser_focus = None
            self._restore_current_directory_watch()
            self._update_status()
            return

        current_sort_policy = self._current_browser_sort_policy()
        if result.prepared_items is not None:
            if result.sort_policy != current_sort_policy:
                self._restart_pending_scan(pending)
                return
            items = result.prepared_items
        else:
            buffered_entries = (
                pending.refresh_entries
                if pending.refresh
                else pending.buffered_entries
            )
            items = self.item_model.sort_items(
                self._items_from_scan_entries(
                    tuple(buffered_entries)
                )
            )

        self._store_folder_snapshot(result.path, items)

        if pending.refresh:
            state = self._capture_list_view_state()
            pending.refresh_entries.clear()
            items = self.item_model.reuse_known_page_counts(items)
            # A completed write can release a sharing lock without changing
            # listing metadata. Retry failed previews once per coalesced event.
            filesystem_change = pending.navigation_source == "filesystem_watch"
            listing_changed = tuple(items) != self.item_model.source_items
            retry_failed = filesystem_change and self.thumbnail_provider.has_failed_requests
            if listing_changed:
                self._generation = self.thumbnail_provider.begin_generation(
                    retry_failed=filesystem_change,
                )
                self.item_model.set_sorted_items(
                    items,
                    preserve_thumbnails=True,
                )
                if self._pending_browser_focus is None:
                    self._schedule_list_view_state_restore(
                        state,
                        update_navigation_history=True,
                    )
                self._restore_location(
                    pending.restore_location,
                    update_status=False,
                )
            elif retry_failed:
                # A finished external write may make a previously failed
                # thumbnail readable even when listing metadata is unchanged.
                # Retry it without resetting an identical model.
                self._generation = self.thumbnail_provider.begin_generation(
                    retry_failed=True,
                )
            # A snapshot history restore can reach this refresh completion
            # before its first-paint callback runs.  That callback is fenced
            # by the newer scan generation, so explicitly re-arm the bounded
            # visible-range request here. Compatible memory/disk thumbnails
            # remain provider cache hits; only missing visible work starts.
            if listing_changed or retry_failed or not filesystem_change:
                self._schedule_thumbnail_requests(0)
        else:
            pending.buffered_entries.clear()
            self._commit_pending_scan(pending)
            initial_count = self._initial_scan_item_count(len(items))
            if pending.atomic_restore:
                # History Back/Forward used to materialize all rows before the
                # first paint so the saved scrollbar could be restored. That
                # made the thumbnail request wait behind a large model reset.
                # Materialize through the saved viewport (and selected item)
                # instead; the remaining rows are appended with the same
                # generation and the final restore keeps the exact location.
                initial_count = self._initial_restore_scan_item_count(
                    items,
                    pending.restore_location,
                )
            initial_items = items[:initial_count]
            if initial_count < len(items):
                pending.remaining_items = items
                pending.remaining_item_offset = initial_count
            self.item_model.begin_final_directory_scan(
                initial_items,
                generation=pending.generation,
            )
            if pending.atomic_restore:
                # Model reset invalidates the scroll range until the view lays
                # out the new grid.  Resolve that geometry in this same event
                # turn so a no-selection history entry can restore its saved
                # scrollbar position before the first paint.
                self.list_view.doItemsLayout()
            pending.first_batch_applied = True
            if pending.trace_id:
                performance_trace.mark(
                    pending.trace_id,
                    "browser.model.first_batch.applied",
                    str(len(initial_items)),
                )
            self._restore_pending_scan_location(pending, final=False)
            self._apply_pending_browser_focus(final=False)
            self._scan_batch_timer.stop()
            if pending.remaining_items:
                self._scan_batch_timer.start()
                self._schedule_scan_status_update()
                return
            self.item_model.finish_directory_scan(
                generation=pending.generation
            )
            self._restore_pending_scan_location(pending, final=True)
        self._finish_pending_scan(pending)

    def _finish_pending_scan(self, pending: _PendingDirectoryScan) -> None:
        if self._pending_scan is not pending:
            return
        self._apply_pending_browser_focus(final=True)
        reconcile_again = pending.directory_watch_dirty
        snapshot_reconcile_path = (
            pending.path if pending.snapshot_hit else None
        )
        if pending.navigation_source == "snapshot_reconcile":
            self._snapshot_reconcile_pending = False
        self._pending_scan = None
        self.directory_scan_committed.emit(str(pending.path))
        self._update_status()
        if pending.refresh and pending.navigation_source != "filesystem_watch":
            QTimer.singleShot(
                0,
                lambda: (
                    self._show_temporary_status(tr('フォルダを更新しました'))
                    if not self._shutdown_prepared
                    else None
                ),
            )
        if self._operation_refresh_generation == pending.generation:
            self._operation_refresh_generation = None
            QTimer.singleShot(0, self._restore_file_operation_selection)
        if reconcile_again:
            self._schedule_directory_reconciliation()
        if snapshot_reconcile_path is not None:
            QTimer.singleShot(
                0,
                lambda path=snapshot_reconcile_path: self._start_snapshot_reconcile(path),
            )

    def _on_scan_failed(self, error: BrowserScanError) -> None:
        pending = self._matching_pending_scan(error.generation, error.path)
        if pending is None or self._shutdown_prepared:
            return
        recover_missing_watched_directory = bool(
            pending.refresh
            and pending.navigation_source == "filesystem_watch"
            and error.status
            in {
                BrowserScanStatus.NOT_FOUND,
                BrowserScanStatus.NOT_DIRECTORY,
            }
            and self._same_path(self.current_path, pending.path)
        )
        if pending.navigation_source == "recent_location":
            self.navigation_history.remove_recent(str(pending.path))
        if pending.navigation_source == "snapshot_reconcile":
            self._snapshot_reconcile_pending = False
        if pending.committed:
            self.item_model.cancel_directory_scan(
                generation=pending.generation
            )
        else:
            self._rollback_pending_history(pending)
        self._discard_pending_scan_buffers(pending)
        self._pending_scan = None
        self._pending_browser_focus = None
        self._scan_batch_timer.stop()
        if recover_missing_watched_directory:
            self._clear_active_directory_watch()
        else:
            self._restore_current_directory_watch()
        self._sync_address_bar()
        self._update_navigation_actions()
        if error.status is BrowserScanStatus.NOT_FOUND:
            message = tr('フォルダが見つかりません')
        elif error.status is BrowserScanStatus.NOT_DIRECTORY:
            message = tr('このファイル形式は表示できません')
        else:
            message = tr('フォルダへアクセスできません')
        self._show_temporary_status(message)
        if recover_missing_watched_directory:
            parent = pending.path.parent
            if not self._same_path(parent, pending.path):
                self.navigate_to(
                    parent,
                    navigation_source="filesystem_watch_recovery",
                )

    def _commit_pending_scan(self, pending: _PendingDirectoryScan) -> None:
        if pending.committed:
            return
        previous_path = self.current_path
        location_changed = bool(
            previous_path is not None
            and not self._same_path(previous_path, pending.path)
        )
        if location_changed:
            self._replace_active_search_query(
                "",
                preserve_view_state=False,
                request_thumbnails=False,
            )
        pending.committed = True
        self.current_path = pending.path
        self.config.set("last_browser_path", str(pending.path))
        self._generation = self.thumbnail_provider.begin_generation()
        self._thumbnail_scroll_direction = 1
        self.list_view.clearSelection()
        self.list_view.setCurrentIndex(QModelIndex())
        if pending.record_history:
            self.navigation_history.visit(BrowserLocation(str(pending.path)))
        else:
            self.navigation_history.mark_recent(pending.restore_location)
        self._folder_change_timer.stop()
        self._pending_tree_navigation_path = None
        self.folder_tree_sync.cancel()
        self._deferred_tree_sync_generation = pending.generation
        self._first_paint_pending_generation = pending.generation
        self._first_paint_trace_id = pending.trace_id
        self.list_view.notify_after_next_paint()
        self._ensure_pending_directory_watch(pending)
        self._sync_address_bar()
        self._update_navigation_actions()
        if location_changed:
            self.location_changed.emit(self, str(pending.path))

    def _initial_scan_item_count(self, total_count: int) -> int:
        count = max(0, int(total_count))
        if count <= 0:
            return 0
        if count <= 40:
            return count
        viewport = self.list_view.viewport()
        grid = self.list_view.gridSize()
        visible_range = calculate_grid_visible_range(
            row_count=count,
            # IconMode wraps at an exact grid-width boundary. Include that
            # strict edge in the existing range calculation so newly chosen
            # item gaps cannot demote the actual first visible row.
            viewport_width=max(1, viewport.width() - 1),
            viewport_height=viewport.height(),
            grid_width=grid.width(),
            grid_height=grid.height(),
            vertical_offset=0,
        )
        if visible_range is None:
            return min(count, 1)
        plan = build_thumbnail_request_plan(
            row_count=count,
            first_visible=visible_range[0],
            last_visible=visible_range[1],
            prefetch_screens=1,
        )
        return min(count, max(1, min(80, len(plan.requested_rows))))

    def _initial_restore_scan_item_count(
        self,
        items: tuple[BrowserItem, ...] | list[BrowserItem],
        location: BrowserLocation,
    ) -> int:
        """Keep history's first paint bounded while retaining its viewport."""

        count = len(items)
        if count <= 40:
            return count
        viewport = self.list_view.viewport()
        grid = self.list_view.gridSize()
        visible_range = calculate_grid_visible_range(
            row_count=count,
            viewport_width=max(1, viewport.width() - 1),
            viewport_height=viewport.height(),
            grid_width=grid.width(),
            grid_height=grid.height(),
            vertical_offset=max(0, int(location.vertical_scroll)),
        )
        target = self._initial_scan_item_count(count)
        if visible_range is not None:
            target = max(target, visible_range[1] + 1)
        filter_state = self.item_model.filter_state
        if filter_state.active:
            # ``items`` is the sorted source sequence, while the first model
            # reset applies the active filter.  A visible row in that filtered
            # sequence can therefore be much farther into the source prefix
            # when matching entries are sparse.  Find the source cutoff for
            # the saved filtered viewport without materializing every row.
            filtered_matches = [filter_state.matches(item) for item in items]
            filtered_count = sum(filtered_matches)
            filtered_range = calculate_grid_visible_range(
                row_count=filtered_count,
                viewport_width=max(1, viewport.width() - 1),
                viewport_height=viewport.height(),
                grid_width=grid.width(),
                grid_height=grid.height(),
                vertical_offset=max(0, int(location.vertical_scroll)),
            )
            if filtered_range is not None:
                required_filtered_row = filtered_range[1]
                matched_rows = 0
                for source_row, is_match in enumerate(filtered_matches):
                    if not is_match:
                        continue
                    matched_rows += 1
                    if matched_rows > required_filtered_row:
                        target = max(target, source_row + 1)
                        break
        if location.selected_path:
            selected_key = self._path_key(location.selected_path)
            for row, item in enumerate(items):
                if self._path_key(item.path) == selected_key:
                    target = max(target, row + 1)
                    break
        return min(count, max(1, target))

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
        self._discard_pending_scan_buffers(pending)
        self._pending_scan = None
        if pending.snapshot_hit:
            self._snapshot_reconcile_pending = False

    def _restart_pending_scan(self, pending: _PendingDirectoryScan) -> bool:
        if self._pending_scan is not pending:
            return False
        self._cancel_pending_scan(rollback_history=False)
        return self.navigate_to(
            pending.path,
            record_history=pending.record_history,
            restore_location=pending.restore_location,
            force_reload=pending.refresh,
            capture_current=False,
            failure_history_revert=pending.failure_history_revert,
            navigation_source=pending.navigation_source,
            trace_id=pending.trace_id,
            atomic_restore=pending.atomic_restore,
        )

    @staticmethod
    def _discard_pending_scan_buffers(pending: _PendingDirectoryScan) -> None:
        pending.refresh_entries.clear()
        pending.buffered_entries.clear()
        pending.remaining_items = ()
        pending.remaining_item_offset = 0

    def _rollback_pending_history(self, pending: _PendingDirectoryScan) -> None:
        if pending.failure_history_revert == "forward":
            self.navigation_history.go_forward()
        elif pending.failure_history_revert == "back":
            self.navigation_history.go_back()
        elif isinstance(pending.failure_history_revert, int):
            self.navigation_history.go_to(pending.failure_history_revert)

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

    def _current_browser_sort_policy(self) -> BrowserSortPolicy:
        return BrowserSortPolicy(
            self.browser_sort_key,
            self.browser_sort_order,
            self.browser_folders_first,
            self.browser_random_seed,
        )

    def _current_browser_visibility_policy(self) -> BrowserVisibilityPolicy:
        return BrowserVisibilityPolicy(
            show_hidden_items=self.browser_show_hidden_items,
            show_unsupported_files=self.browser_show_unsupported_files,
            show_system_items=self.browser_show_system_items,
        )

    def _store_folder_snapshot(
        self,
        path: str | Path,
        items: tuple[BrowserItem, ...] | list[BrowserItem],
    ) -> None:
        self.folder_snapshot_cache.put(
            path,
            self._current_browser_visibility_policy(),
            self._current_browser_sort_policy(),
            items,
        )

    def _start_snapshot_reconcile(self, path: Path) -> None:
        if self._shutdown_prepared or not self._same_path(self.current_path, path):
            self._snapshot_reconcile_pending = False
            return
        if not self._refresh_current_folder(navigation_source="snapshot_reconcile"):
            self._snapshot_reconcile_pending = False

    def _navigate_from_folder_snapshot(
        self,
        path: Path,
        snapshot_items: tuple[BrowserItem, ...],
        *,
        record_history: bool,
        restore_location: BrowserLocation,
        failure_history_revert: str | int | None,
        navigation_source: str,
        trace_id: int,
        atomic_restore: bool,
    ) -> bool:
        """Publish a cached listing, then reconcile it through a normal refresh."""

        self._cancel_pending_scan(rollback_history=not atomic_restore)
        self._scan_generation += 1
        if trace_id:
            performance_trace.mark(
                trace_id,
                "navigation.generation.issued",
                str(self._scan_generation),
            )
        pending = _PendingDirectoryScan(
            path=path,
            generation=self._scan_generation,
            record_history=record_history,
            restore_location=restore_location,
            refresh=False,
            failure_history_revert=failure_history_revert,
            trace_id=trace_id,
            navigation_source=navigation_source,
            atomic_restore=atomic_restore,
            snapshot_hit=True,
        )
        self._pending_scan = pending
        self._snapshot_reconcile_pending = True
        if not self._same_path(self._directory_watch_path, path):
            self._set_active_directory_watch(path)
        self.address_bar.setText(str(path))
        self._commit_pending_scan(pending)

        initial_count = self._initial_scan_item_count(len(snapshot_items))
        if atomic_restore:
            initial_count = self._initial_restore_scan_item_count(
                snapshot_items,
                restore_location,
            )
        initial_items = snapshot_items[:initial_count]
        pending.remaining_items = snapshot_items
        pending.remaining_item_offset = initial_count
        self.item_model.begin_final_directory_scan(
            initial_items,
            generation=pending.generation,
        )
        if atomic_restore:
            self.list_view.doItemsLayout()
        pending.first_batch_applied = True
        if pending.trace_id:
            performance_trace.mark(
                pending.trace_id,
                "browser.model.first_batch.applied",
                str(len(initial_items)),
            )
        self._restore_pending_scan_location(pending, final=False)
        self._apply_pending_browser_focus(final=False)
        self._scan_batch_timer.stop()
        if pending.remaining_item_offset < len(pending.remaining_items):
            self._scan_batch_timer.start(0)
            self._schedule_scan_status_update()
        else:
            self.item_model.finish_directory_scan(generation=pending.generation)
            self._restore_pending_scan_location(pending, final=True)
            self._finish_pending_scan(pending)
        self._update_status(force=True)
        return True

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

    def synchronize_viewer_item(
        self,
        path: str | Path,
        *,
        expected_parent: str | Path,
        preserve_selection: bool = False,
    ) -> bool:
        """Select one visible Viewer item without changing Browser location."""

        if self.current_path is None or not self._same_path(
            self.current_path,
            Path(expected_parent),
        ):
            return False
        target = self._absolute_browser_path(path)
        row = self.item_model.row_for_path(target)
        selection_model = self.list_view.selectionModel()
        if selection_model is None:
            return False
        if row < 0:
            if preserve_selection:
                return False
            selection_model.clearSelection()
            selection_model.setCurrentIndex(
                QModelIndex(),
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
            self._update_status()
            return False

        index = self.item_model.index(row, 0)
        current_item = self.item_model.item_at(self.list_view.currentIndex())
        selected_items = tuple(
            self.item_model.item_at(selected)
            for selected in selection_model.selectedIndexes()
        )
        if (
            current_item is not None
            and self._same_path(current_item.path, target)
            and (preserve_selection or len(selected_items) == 1)
            and any(selected is not None and self._same_path(selected.path, target)
                    for selected in selected_items)
        ):
            self._update_status()
            return True

        state = self._capture_list_view_state()
        selected_paths = (
            state.selected_paths
            if preserve_selection and any(
                selected is not None and self._same_path(selected.path, target)
                for selected in selected_items
            ) else (str(target),)
        )
        self._restore_list_view_state(
            _ListViewState(
                selected_paths=selected_paths,
                current_path=str(target),
                anchor_path=state.anchor_path,
                anchor_row=state.anchor_row,
                anchor_x=state.anchor_x,
                anchor_y=state.anchor_y,
                vertical_scroll=state.vertical_scroll,
                horizontal_scroll=state.horizontal_scroll,
            )
        )
        return self.list_view.currentIndex() == index

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
            atomic_restore=True,
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
            atomic_restore=True,
        ):
            return True
        self.navigation_history.go_back()
        self._update_navigation_actions()
        return False

    def go_to_history_index(self, index: int) -> bool:
        """Jump within BrowserNavigationHistory without creating a new stack."""

        self._update_current_navigation_state()
        previous_index = self.navigation_history.current_index
        location = self.navigation_history.go_to(index)
        if location is None:
            self._update_navigation_actions()
            return False
        if self.navigate_to(
            location.path,
            record_history=False,
            restore_location=location,
            capture_current=False,
            failure_history_revert=previous_index,
            atomic_restore=True,
        ):
            return True
        self.navigation_history.go_to(previous_index)
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
        return self._refresh_current_folder(navigation_source="manual_refresh")

    def _refresh_current_folder(self, *, navigation_source: str) -> bool:
        if self.current_path is None:
            return False
        if self._snapshot_reconcile_pending:
            navigation_source = "snapshot_reconcile"
        location = self._current_location()
        if not self.navigate_to(
            self.current_path,
            record_history=False,
            restore_location=location,
            force_reload=True,
            navigation_source=navigation_source,
        ):
            return False
        return True

    def _set_active_directory_watch(self, path: str | Path) -> None:
        if self._shutdown_prepared:
            return
        target = self._absolute_browser_path(path)
        self._directory_watch_generation += 1
        generation = self._directory_watch_generation
        watched = self.directory_watcher.watch(str(target), generation)
        self._directory_watch_path = target if watched else None

    def _clear_active_directory_watch(self) -> None:
        self._directory_watch_generation += 1
        self._directory_watch_path = None
        self._directory_change_pending = False
        if hasattr(self, "_directory_change_timer"):
            self._directory_change_timer.stop()
        self.directory_watcher.clear()

    def _restore_current_directory_watch(self) -> None:
        if self._shutdown_prepared:
            return
        if self.current_path is None:
            self._clear_active_directory_watch()
            return
        if self._same_path(self._directory_watch_path, self.current_path):
            return
        self._set_active_directory_watch(self.current_path)

    def _ensure_pending_directory_watch(
        self,
        pending: _PendingDirectoryScan,
    ) -> None:
        if not self._same_path(self._directory_watch_path, pending.path):
            self._set_active_directory_watch(pending.path)

    def _on_directory_changed(self, change: BrowserDirectoryChange) -> None:
        if self._shutdown_prepared:
            return
        if change.generation != self._directory_watch_generation:
            return
        changed_path = Path(change.path)
        if not self._same_path(self._directory_watch_path, changed_path):
            return
        pending = self._pending_scan
        if pending is not None:
            if self._same_path(pending.path, changed_path):
                pending.directory_watch_dirty = True
            return
        if not self._same_path(self.current_path, changed_path):
            return
        self._directory_change_pending = True
        self._directory_change_timer.start()

    def _schedule_directory_reconciliation(self) -> None:
        if self._shutdown_prepared or self.current_path is None:
            return
        if not self._same_path(
            self.current_path,
            self._directory_watch_path,
        ):
            return
        self._directory_change_pending = True
        self._directory_change_timer.start()

    def _flush_directory_changes(self) -> None:
        self._directory_change_timer.stop()
        if not self._directory_change_pending or self._shutdown_prepared:
            return
        if self.file_operation_coordinator.busy:
            return
        if self.current_path is None or not self._same_path(
            self.current_path,
            self._directory_watch_path,
        ):
            self._directory_change_pending = False
            return
        pending = self._pending_scan
        if pending is not None:
            if self._same_path(pending.path, self.current_path):
                pending.directory_watch_dirty = True
            self._directory_change_pending = False
            return
        self._directory_change_pending = False
        self._refresh_current_folder(navigation_source="filesystem_watch")

    def _release_deferred_directory_change(
        self,
        _result: FileOperationResult,
    ) -> None:
        if not self._directory_change_pending or self._shutdown_prepared:
            return
        pending = self._pending_scan
        if (
            pending is not None
            and pending.refresh
            and self._same_path(pending.path, self.current_path)
        ):
            # The production file-operation refresh is already a complete
            # reconciliation of this directory, so it absorbs notifications
            # accumulated while that operation was running.
            self._directory_change_pending = False
            return
        self._directory_change_timer.start()

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
        self._sync_address_bar()
        self.location_stack.setCurrentWidget(self.address_bar)
        self.address_bar.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.address_bar.selectAll()

    def _show_breadcrumb_mode(self) -> None:
        if not hasattr(self, "location_stack"):
            return
        self.location_stack.setCurrentWidget(self.location_breadcrumb)

    def _finish_address_edit(self) -> None:
        if not self.address_bar.hasFocus():
            self._sync_address_bar()
            self._show_breadcrumb_mode()

    def _navigate_from_breadcrumb(self, path: str) -> None:
        target = Path(path)
        if self._same_path(self.current_path, target):
            return
        self.navigate_to(target)

    @staticmethod
    def _location_menu_label(path: str) -> str:
        target = Path(path)
        return target.name or target.anchor or str(target)

    def _show_navigation_history_menu(
        self,
        direction: str,
        global_position: QPoint,
    ) -> BrowserLocationListPopup | None:
        entries = self.navigation_history.entries
        current_index = self.navigation_history.current_index
        if direction == "back":
            indices = range(current_index - 1, -1, -1)
        else:
            indices = range(current_index + 1, len(entries))
        indices = tuple(indices)
        if not indices:
            return None
        self._close_owned_popup("_navigation_history_menu")
        popup_entries: list[LocationPopupEntry] = []
        current = self.navigation_history.current()
        if current is not None:
            popup_entries.append(
                LocationPopupEntry(
                    tr('現在: {p0}', p0=self._location_menu_label(current.path)),
                    None,
                    current.path,
                    enabled=False,
                    current=True,
                )
            )
        for index in indices:
            location = entries[index]
            popup_entries.append(
                LocationPopupEntry(
                    self._location_menu_label(location.path),
                    index,
                    location.path,
                )
            )
        popup = BrowserLocationListPopup(tuple(popup_entries), self)
        self._navigation_history_menu = popup
        popup.entryActivated.connect(
            lambda entry: self.go_to_history_index(int(entry.value))
        )
        popup.closed.connect(
            lambda value=popup: self._release_owned_popup(
                "_navigation_history_menu",
                value,
            )
        )
        popup.show_at(global_position)
        return popup

    def _show_location_history_popup(self) -> BrowserLocationListPopup | None:
        recent = self.navigation_history.recent_unique()
        if not recent:
            return None
        if (
            self._location_history_popup is not None
            and self._location_history_popup.isVisible()
        ):
            self._location_history_popup.close()
            return None
        self._close_owned_popup("_location_history_popup")
        current = self.navigation_history.current()
        popup_entries: list[LocationPopupEntry] = []
        for index, location in recent:
            is_current = bool(
                current is not None
                and self._same_path(Path(current.path), Path(location.path))
            )
            popup_entries.append(
                LocationPopupEntry(
                    self._location_menu_label(location.path),
                    location,
                    location.path,
                    enabled=not is_current,
                    current=is_current,
                )
            )
        popup = BrowserLocationListPopup(tuple(popup_entries), self)
        self._location_history_popup = popup
        popup.entryActivated.connect(self._activate_location_history_entry)
        popup.closed.connect(
            lambda value=popup: self._release_owned_popup(
                "_location_history_popup",
                value,
            )
        )
        popup.show_for(self.browser_location_control)
        return popup

    def _activate_location_history_entry(self, entry: LocationPopupEntry) -> None:
        location = entry.value
        if not isinstance(location, BrowserLocation):
            return
        self.navigate_to(
            location.path,
            restore_location=location,
            navigation_source="recent_location",
        )

    def _show_breadcrumb_children(
        self,
        parent_path: str,
    ) -> None:
        normalized_path = os.path.abspath(os.path.normpath(parent_path))
        if (
            self._location_directory_menu is not None
            and self._location_directory_menu.isVisible()
            and self._same_path(
                Path(normalized_path),
                Path(self._location_directory_menu_path or normalized_path),
            )
        ):
            self._location_directory_menu.close()
            return
        if (
            self._location_directory_pending_path is not None
            and self._same_path(
                Path(normalized_path),
                Path(self._location_directory_pending_path),
            )
        ):
            return
        self._close_location_directory_popup(cancel_pending=True)
        self._location_directory_pending_path = normalized_path
        self._location_directory_generation = (
            self.location_directory_loader.request(
                normalized_path,
                BrowserVisibilityPolicy(
                    show_hidden_items=self.browser_show_hidden_items,
                    show_unsupported_files=self.browser_show_unsupported_files,
                    show_system_items=self.browser_show_system_items,
                ),
            )
        )

    def _on_location_directory_loaded(
        self,
        result: LocationDirectoryResult,
    ) -> None:
        if (
            self._shutdown_prepared
            or result.generation != self._location_directory_generation
            or self._location_directory_pending_path is None
            or not self._same_path(
                Path(result.parent_path),
                Path(self._location_directory_pending_path),
            )
        ):
            return
        self._location_directory_pending_path = None
        anchor = self.location_breadcrumb.separator_button_for_path(
            result.parent_path
        )
        if anchor is None:
            return
        popup_entries: list[LocationPopupEntry] = []
        if result.error:
            popup_entries.append(
                LocationPopupEntry(
                    tr('フォルダを読み込めません'),
                    None,
                    result.error,
                    enabled=False,
                )
            )
        elif not result.directories:
            popup_entries.append(
                LocationPopupEntry(
                    tr('子フォルダはありません'),
                    None,
                    enabled=False,
                )
            )
        else:
            for directory in result.directories:
                popup_entries.append(
                    LocationPopupEntry(
                        directory.label,
                        directory.path,
                        directory.path,
                    )
                )
        popup = BrowserLocationListPopup(tuple(popup_entries), self)
        self._location_directory_menu = popup
        self._location_directory_menu_path = result.parent_path
        popup.entryActivated.connect(
            lambda entry: self.navigate_to(str(entry.value))
        )
        popup.closed.connect(
            lambda value=popup: self._release_location_directory_popup(value)
        )
        popup.show_for(anchor)

    def _close_owned_popup(self, attribute: str) -> None:
        menu = getattr(self, attribute, None)
        if menu is None:
            return
        setattr(self, attribute, None)
        menu.close()
        menu.deleteLater()

    def _release_owned_popup(
        self,
        attribute: str,
        menu: BrowserLocationListPopup,
    ) -> None:
        if getattr(self, attribute, None) is menu:
            setattr(self, attribute, None)
        menu.deleteLater()
        QTimer.singleShot(0, self._restore_focus_after_popup)

    def _close_location_directory_popup(
        self,
        *,
        cancel_pending: bool,
    ) -> None:
        if cancel_pending and self._location_directory_pending_path is not None:
            self._location_directory_generation = (
                self.location_directory_loader.cancel()
            )
            self._location_directory_pending_path = None
        menu = self._location_directory_menu
        self._location_directory_menu = None
        self._location_directory_menu_path = None
        if menu is not None:
            menu.close()
            menu.deleteLater()

    def _release_location_directory_popup(
        self,
        menu: BrowserLocationListPopup,
    ) -> None:
        if self._location_directory_menu is menu:
            self._location_directory_menu = None
            self._location_directory_menu_path = None
        menu.deleteLater()
        QTimer.singleShot(0, self._restore_focus_after_popup)

    def _restore_focus_after_popup(self) -> None:
        if self._shutdown_prepared or not self.isVisible():
            return
        self.list_view.setFocus(Qt.FocusReason.PopupFocusReason)

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
                snapshot_item=item,
            )

    def _open_system_file(self, path: str | Path) -> bool:
        result = self.system_file_opener.open_with_default_application(
            path,
            parent_hwnd=int(self.winId()),
        )
        if result.success:
            self._show_temporary_status(
                tr('NivisViewerでは表示できません。既定のアプリで開きました')
            )
            return True
        self._show_temporary_status(
            tr('NivisViewerでは表示できません。')
            + (
                f" {result.error_message}"
                if result.error_message
                else tr(' 関連付けアプリで開けませんでした')
            )
        )
        return False

    def _open_with_application_picker(self, item: BrowserItem) -> bool:
        target = self._absolute_browser_path(item.path)
        if item.kind is BrowserItemKind.FOLDER or not target.is_file():
            self._show_temporary_status(tr('関連付けで開く対象が見つかりません'))
            return False
        result = self.system_file_opener.open_with_application_picker(
            target,
            parent_hwnd=int(self.winId()),
        )
        if result.success:
            self._show_temporary_status(tr('アプリ選択画面を開きました'))
            return True
        self._show_temporary_status(
            result.error_message or tr('アプリ選択画面を開けませんでした')
        )
        return False

    def _open_item_in_explorer(self, item: BrowserItem) -> bool:
        target = self._absolute_browser_path(item.path)
        result = self.system_file_opener.open_in_explorer(
            target,
            is_directory=item.kind is BrowserItemKind.FOLDER,
        )
        if result.success:
            return True
        self._show_temporary_status(
            result.error_message or tr('Explorerを開けませんでした')
        )
        return False

    def _open_current_folder_in_explorer(self, item: BrowserItem) -> bool:
        result = self.system_file_opener.open_in_explorer(
            item.path,
            is_directory=False,
        )
        if result.success:
            return True
        self._show_temporary_status(
            result.error_message or tr('Explorerを開けませんでした')
        )
        return False

    def _folder_snapshot_for_item(
        self,
        item: BrowserItem,
        *,
        snapshot_items: tuple[BrowserItem, ...] | None = None,
    ) -> FolderListingSnapshot | None:
        if (
            item.kind is not BrowserItemKind.IMAGE
            or self.current_path is None
        ):
            return None
        candidates = (
            self._visible_order_snapshot_items()
            if snapshot_items is None else snapshot_items
        )
        # Keep initial-open IMAGE membership and exact path selection intact.
        # The mixed snapshot projection intentionally has a narrower openable
        # filter and normalized first-match identity; they are not interchangeable.

        image_ids = tuple(
            str(candidate.path)
            for candidate in candidates
            if candidate.kind is BrowserItemKind.IMAGE
        )
        if str(item.path) not in image_ids:
            return None
        selected_index = image_ids.index(str(item.path))
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
                for candidate in candidates
                if candidate.kind is BrowserItemKind.IMAGE
            ),
            generation=self._scan_generation,
            sort_identity=(
                f"{self.browser_sort_key.value}:"
                f"{self.browser_sort_order.value}:"
                f"folders_first={int(self.browser_folders_first)}"
                + (f":seed={self.browser_random_seed}" if self.browser_sort_key is BrowserSortKey.RANDOM else "")
            ),
            selected_index=selected_index,
            filter_identity=(
                f"hidden={int(self.browser_show_hidden_items)}:"
                f"unsupported={int(self.browser_show_unsupported_files)}:"
                f"system={int(self.browser_show_system_items)}:"
                f"search={self.browser_filter_state.search_text.casefold()!r}:"
                f"rating={self.browser_filter_state.rating_mode.value}:"
                f"reference={self.browser_filter_state.rating_reference}"
                f":tags={(self.browser_filter_state.include_tags, self.browser_filter_state.exclude_tags, self.browser_filter_state.tag_match)!r}"
            ),
        )

    def _visible_order_snapshot_items(self) -> tuple[BrowserItem, ...]:
        """Freeze the existing source/filter/sort pipeline without rescanning."""

        candidates = self.item_model.source_items
        pending = self._pending_scan
        if (
            pending is not None
            and pending.committed
            and self._same_path(pending.path, self.current_path)
            and pending.remaining_items
        ):
            candidates += pending.remaining_items[
                pending.remaining_item_offset :
            ]
        return self.item_model.visible_items(candidates)

    def _folder_snapshot_for_path(
        self,
        path: str | Path,
        *,
        snapshot_items: tuple[BrowserItem, ...] | None = None,
    ) -> FolderListingSnapshot | None:
        """Snapshot the current visible Browser order for one image path."""

        row = self.item_model.row_for_path(path)
        if row < 0:
            return None
        item = self.item_model.item_at(self.item_model.index(row, 0))
        if item is None:
            return None
        return self._folder_snapshot_for_item(item, snapshot_items=snapshot_items)

    def _invoke_open_path_handler(
        self,
        path: str,
        open_in_new_window: bool,
        folder_snapshot: FolderListingSnapshot | None = None,
        browser_snapshot: AdjacentBookBrowserSnapshot | None = None,
        *,
        use_browser_order: bool = True,
        snapshot_item: BrowserItem | None = None,
    ) -> object | None:
        handler = self._open_path_handler
        if handler is None:
            return None
        # One action-local capture, including pending progressive scan items.
        # Supplied snapshots remain untouched; external drops explicitly opt out.
        snapshot_items = None
        if (
            use_browser_order
            and self.current_path is not None
            and (folder_snapshot is None or browser_snapshot is None)
        ):
            snapshot_items = self._visible_order_snapshot_items()
        if folder_snapshot is None and use_browser_order:
            # Every open originating from the current Browser model must use
            # the model's visible image order, including bookmark/history
            # entry points that happen to target the displayed folder.  Paths
            # dropped from outside explicitly opt out below and keep direct-
            # open semantics.
            folder_snapshot = (
                self._folder_snapshot_for_item(snapshot_item, snapshot_items=snapshot_items)
                if snapshot_item is not None
                else self._folder_snapshot_for_path(path, snapshot_items=snapshot_items)
            )
        if (
            browser_snapshot is None
            and use_browser_order
            and self.current_path is not None
        ):
            browser_snapshot = self.adjacent_book_snapshot(
                self.current_path, snapshot_items=snapshot_items,
            )
        try:
            import inspect

            signature = inspect.signature(handler)
            accepts_browser_snapshot = (
                "browser_snapshot" in signature.parameters
                or len(signature.parameters) >= 4
                or any(
                    parameter.kind is inspect.Parameter.VAR_POSITIONAL
                    for parameter in signature.parameters.values()
                )
            )
            accepts_snapshot = len(signature.parameters) >= 3 or any(
                parameter.kind is inspect.Parameter.VAR_POSITIONAL
                for parameter in signature.parameters.values()
            )
        except (TypeError, ValueError):
            accepts_browser_snapshot = False
            accepts_snapshot = False
        if accepts_browser_snapshot:
            return handler(
                path,
                open_in_new_window,
                folder_snapshot,
                browser_snapshot,
            )
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
        self._show_temporary_status(tr('{p0}項目をコピー候補にしました', p0=len(paths)))
        return True

    def copy_selected_names(self) -> bool:
        paths = self.selected_file_operation_paths()
        if not paths:
            return False
        mime = QMimeData()
        mime.setText("\n".join(Path(path).name for path in paths))
        QApplication.clipboard().setMimeData(mime)
        self._show_temporary_status(tr('{p0}項目の名前をコピーしました', p0=len(paths)))
        return True

    def cut_selected_items(self) -> bool:
        paths = self.selected_file_operation_paths()
        if not paths:
            return False
        self._set_file_clipboard(paths, cut=True)
        self._show_temporary_status(tr('{p0}項目を切り取り候補にしました', p0=len(paths)))
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
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        clipboard_receipt = ClipboardPasteReceipt.capture(
            self._internal_clipboard_state,
            mime,
        )
        sources = self._clipboard_file_urls(mime)
        if not sources:
            self._show_temporary_status(tr('貼り付けるファイルがありません'))
            return False
        internal_matches = self._internal_clipboard_state.matches_mime(mime)
        preferred_effect = InternalClipboardState.preferred_drop_effect(mime)
        operation = (
            FileOperationKind.MOVE
            if paste_is_move(
                internal_matches=internal_matches,
                internal_cut=self._internal_clipboard_state.is_cut,
                preferred_effect=preferred_effect,
            )
            else FileOperationKind.COPY
        )
        if not clipboard_receipt.matches(
            self._internal_clipboard_state,
            clipboard.mimeData(),
        ):
            self._show_temporary_status(tr('クリップボードが変更されたため貼り付けを中止しました'))
            return False
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
            self._show_temporary_status(tr('同じフォルダへの移動は行いません'))
            return False
        return self._start_file_operation(
            operation,
            sources=sources,
            destination=self.current_path,
            clipboard_receipt=(clipboard_receipt if operation is FileOperationKind.MOVE else None),
        )

    def rename_selected_item(self) -> bool:
        paths = self.selected_file_operation_paths()
        if len(paths) != 1:
            return False
        source = Path(paths[0])
        new_name = self._prompt_for_filename(
            tr('名前の変更'),
            tr('新しい名前:'),
            source.name,
        )
        if new_name is None or new_name == source.name:
            return False
        old_suffix = source.suffix.casefold()
        new_suffix = Path(new_name).suffix.casefold()
        if old_suffix != new_suffix:
            answer = QMessageBox.question(
                self,
                tr('拡張子の変更'),
                tr('拡張子を変更すると項目を開けなくなる場合があります。続行しますか？'),
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
        if not bool(
            self.config.get(
                "file_operation_delete_skip_confirmation",
                False,
            )
        ):
            if len(paths) == 1:
                prompt = tr('「{p0}」をごみ箱へ移動しますか？', p0=Path(paths[0]).name)
            else:
                prompt = tr('{p0}項目をごみ箱へ移動しますか？', p0=len(paths))
            default_button = (
                QMessageBox.StandardButton.Yes
                if bool(
                    self.config.get(
                        "file_operation_delete_confirm_focus_yes",
                        False,
                    )
                )
                else QMessageBox.StandardButton.No
            )
            answer = QMessageBox.question(
                self,
                tr('削除'),
                prompt,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                default_button,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        return self._start_file_operation(
            FileOperationKind.RECYCLE,
            sources=paths,
        )

    def show_selected_properties(self) -> bool:
        paths = self.selected_file_operation_paths()
        if len(paths) != 1:
            return False
        dialog = FilePropertiesDialog(paths[0], self)
        self._properties_dialogs.add(dialog)
        dialog.rename_requested.connect(
            lambda name, close_on_success, dialog=dialog: (
                self._rename_from_properties(
                    dialog,
                    name,
                    close_on_success=close_on_success,
                )
            )
        )
        dialog.finished.connect(
            lambda _result, dialog=dialog: self._properties_dialogs.discard(dialog)
        )
        dialog.open()
        return True

    def _rename_from_properties(
        self,
        dialog: FilePropertiesDialog,
        new_name: str,
        *,
        close_on_success: bool,
    ) -> None:
        source = dialog.path
        if new_name == source.name:
            dialog.clear_error()
            if close_on_success:
                dialog.accept()
            return
        validation = validate_windows_filename(new_name)
        if not validation.valid:
            dialog.show_error(validation.error_message or tr('名前が無効です'))
            return
        if source.is_file() and source.suffix.casefold() != Path(
            validation.normalized_name
        ).suffix.casefold():
            answer = QMessageBox.question(
                dialog,
                tr('拡張子の変更'),
                tr('拡張子を変更すると項目を開けなくなる場合があります。続行しますか？'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if not self._start_file_operation(
            FileOperationKind.RENAME,
            sources=(str(source),),
            new_name=validation.normalized_name,
        ):
            dialog.show_error(tr('名前変更を開始できませんでした'))
            return
        request_id = self._file_operation_request_id
        self._property_rename_requests[request_id] = (
            dialog,
            close_on_success,
        )
        dialog.clear_error()
        dialog.set_busy(True)

    def copy_selected_to(self, destination: str | Path | None = None) -> bool:
        paths = self.selected_file_operation_paths()
        if not paths:
            return False
        target = self._choose_destination(tr('コピー先を選択'), destination)
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
        target = self._choose_destination(tr('移動先を選択'), destination)
        if target is None:
            return False
        return self._start_file_operation(
            FileOperationKind.MOVE,
            sources=paths,
            destination=target,
        )

    def compress_selected_to_zip(self) -> bool:
        paths = self.selected_file_operation_paths()
        if not paths or self.current_path is None:
            return False
        base_name = Path(paths[0]).name if len(paths) == 1 else self.current_path.name
        name = self._prompt_for_filename(
            tr('zipに圧縮'), tr('ZIPファイル名:'), (base_name or "Archive") + ".zip",
        )
        if name is None:
            return False
        if not name.lower().endswith(".zip"):
            name += ".zip"
        return self._start_file_operation(
            FileOperationKind.CREATE_ZIP,
            sources=paths,
            destination=self.current_path,
            new_name=name,
        )

    def create_new_folder(self) -> bool:
        if self.current_path is None:
            return False
        initial_name = generate_numbered_name(
            "新しいフォルダ",
            (item.display_name for item in self.items),
        )
        if initial_name is None:
            self._show_temporary_status(tr('新しいフォルダ名を生成できません'))
            return False
        name = self._prompt_for_filename(
            tr('新しいフォルダ'),
            tr('フォルダ名:'),
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
            self.statusBar().showMessage(tr('安全な境界でキャンセルしています…'))

    def _start_file_operation(
        self,
        operation: FileOperationKind,
        *,
        sources: tuple[str, ...] = (),
        destination: str | Path | None = None,
        new_name: str | None = None,
        clipboard_receipt: ClipboardPasteReceipt | None = None,
    ) -> bool:
        if self._snapshot_reconcile_pending:
            self._show_temporary_status(tr('一覧を更新中のため操作できません'))
            return False
        filtered_sources = tuple(
            path
            for path in sources
            if not FileOperationArtifactPolicy.is_internal_operation_artifact(
                path
            )
        )
        if sources and not filtered_sources:
            self._show_temporary_status(
                tr('NivisViewerの未完了一時ファイルは操作できません')
            )
            return False
        sources = filtered_sources
        if (
            self.file_operation_coordinator.busy
            and self.file_operation_coordinator.queue is None
        ):
            self._show_temporary_status(tr('別のファイル操作を実行中です'))
            return False
        if operation in {
            FileOperationKind.RENAME,
            FileOperationKind.MOVE,
            FileOperationKind.RECYCLE,
        FileOperationKind.UNDO} and not self._confirm_and_close_affected_viewers(sources):
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
        if clipboard_receipt is not None:
            current_mime = QApplication.clipboard().mimeData()
            if (
                not clipboard_receipt.matches(self._internal_clipboard_state, current_mime)
                or (
                    clipboard_receipt.internal_request_identity is not None
                    and not clipboard_receipt.matches_internal_snapshot(
                        self._internal_clipboard_state
                    )
                )
            ):
                self._show_temporary_status(tr('クリップボードが変更されたため貼り付けを中止しました'))
                return False
        selected_paths = self.selected_file_operation_paths()
        current_row = (
            self.list_view.currentIndex().row()
            if self.list_view.currentIndex().isValid()
            else None
        )
        self._file_operation_requests[request.request_id] = request
        if clipboard_receipt is not None:
            self._clipboard_paste_receipts[request.request_id] = clipboard_receipt
        self._file_operation_selection_before[request.request_id] = (
            selected_paths,
            current_row,
        )
        if clipboard_receipt is not None:
            current_mime = QApplication.clipboard().mimeData()
            if (
                not clipboard_receipt.matches(self._internal_clipboard_state, current_mime)
                or (
                    clipboard_receipt.internal_request_identity is not None
                    and not clipboard_receipt.matches_internal_snapshot(
                        self._internal_clipboard_state
                    )
                )
            ):
                self._file_operation_requests.pop(request.request_id, None)
                self._clipboard_paste_receipts.pop(request.request_id, None)
                self._file_operation_selection_before.pop(request.request_id, None)
                self._show_temporary_status(tr('クリップボードが変更されたため貼り付けを中止しました'))
                return False
        if not self.file_operation_coordinator.execute(request):
            self._file_operation_requests.pop(request.request_id, None)
            self._clipboard_paste_receipts.pop(request.request_id, None)
            self._file_operation_selection_before.pop(request.request_id, None)
            self._show_temporary_status(tr('ファイル操作を開始できません'))
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
        if (self._rating_batch is not None and self._rating_batch.tag_edit
                and self._rating_batch.active_request_id == plan.request_id):
            # REPLACE here permits only the planner's case-only rename;
            # the rename service still rejects any distinct target entry.
            self.file_operation_coordinator.queue.resolve_conflicts(
                plan.operation_id,
                {conflict.conflict_id: (ConflictResolution.REPLACE
                 if conflict.kind is FileConflictKind.CASE_ONLY_NAME else ConflictResolution.SKIP)
                 for conflict in plan.conflicts},
            )
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
            tr('ViewerWindowで使用中'),
            tr('この項目はViewerWindowで開かれています。\n対象Viewerを閉じて操作を続けますか？'),
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
                tr('PDFの解放を待機中のためファイル操作を開始できません')
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
                tr('名前を使用できません'),
                validation.error_message or tr('名前が無効です'),
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
        recent_menu = menu.addMenu(tr('最近使った移動先'))
        recent = self.destination_history.entries()
        if not recent:
            empty = recent_menu.addAction(tr('（履歴なし）'))
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
        favorite_menu = menu.addMenu(tr('お気に入り'))
        favorite_entries = tuple(getattr(self.folder_bookmark_model, "entries", ()))
        if not favorite_entries:
            empty = favorite_menu.addAction(tr('（お気に入りなし）'))
            empty.setEnabled(False)
        for entry in favorite_entries:
            action = favorite_menu.addAction(entry.label)
            action.setToolTip(entry.path)
            action.triggered.connect(
                lambda _checked=False, path=entry.path, kind=operation: (
                    self.copy_selected_to(path)
                    if kind is FileOperationKind.COPY
                    else self.move_selected_to(path)
                )
            )
        menu.addSeparator()
        specified = menu.addAction(tr('指定先...'))
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

    def _clipboard_file_urls(self, mime: QMimeData | None = None) -> tuple[str, ...]:
        if mime is None:
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
        if request.operation is FileOperationKind.CREATE_ZIP:
            self._close_zip_progress_dialog()
            dialog = QProgressDialog(tr('ZIPに圧縮する準備をしています…'), tr('キャンセル'), 0, 0, self)
            dialog.setWindowTitle(tr('ZIPに圧縮中'))
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.setAutoClose(False)
            dialog.setAutoReset(False)
            dialog.setMinimumDuration(0)
            dialog.setMinimumWidth(380)
            queue = self.file_operation_coordinator.queue
            dialog.canceled.connect(
                (lambda: queue.cancel(request.operation_id))
                if queue is not None else self.file_operation_coordinator.cancel
            )
            self._zip_progress_dialog = dialog
            self._zip_progress_request_id = request.request_id
            dialog.show()
        self.statusBar().setMaximumHeight(16777215)
        self._active_file_operation_id = request.request_id
        self.cancel_operation_button.setVisible(
            self.file_operation_coordinator.queue is None
        )
        self.cancel_operation_button.setEnabled(True)
        self._update_file_action_states()
        self.statusBar().showMessage(
            tr('{p0}中… 0 / {p1}', p0=self._operation_label(request.operation), p1=max(1, len(request.source_paths)))
        )

    def _close_zip_progress_dialog(self) -> None:
        dialog = self._zip_progress_dialog
        self._zip_progress_dialog = None
        self._zip_progress_request_id = None
        if dialog is not None:
            dialog.reset()
            dialog.deleteLater()

    def _on_file_operation_progress(
        self,
        progress: FileOperationProgress,
    ) -> None:
        if (
            self._shutdown_prepared
            or progress.request_id != self._active_file_operation_id
        ):
            return
        if self._close_after_cancel:
            self.statusBar().showMessage(tr('ファイル操作を中止しています…'))
            return
        dialog = self._zip_progress_dialog
        if dialog is not None and self._zip_progress_request_id == progress.request_id and not dialog.wasCanceled():
            dialog.setLabelText(tr(
                'ZIPに圧縮中… {p0}\n{p1:.1f} MiB 処理済み',
                p0=Path(progress.source_path).name if progress.source_path else "",
                p1=progress.bytes_completed / (1024 * 1024),
            ))
            if progress.bytes_total > 0:
                dialog.setRange(0, 1000)
                dialog.setValue(min(1000, progress.bytes_completed * 1000 // progress.bytes_total))
        self.statusBar().showMessage(
            tr('{p0}中… {p1} / {p2}', p0=self._operation_label(progress.operation), p1=progress.completed, p2=progress.total)
        )

    def _on_file_operation_completed(
        self,
        result: FileOperationResult,
    ) -> None:
        property_rename = self._property_rename_requests.pop(
            result.request_id,
            None,
        )
        if result.request_id == self._zip_progress_request_id:
            self._close_zip_progress_dialog()
        request = self._file_operation_requests.pop(result.request_id, None)
        clipboard_receipt = self._clipboard_paste_receipts.pop(
            result.request_id,
            None,
        )
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
        QTimer.singleShot(
            3200 if self.file_operation_coordinator.queue is not None else 0,
            self,
            self._compact_status_bar_if_idle,
        )
        self._update_file_action_states()

        if result.operation in {FileOperationKind.RENAME, FileOperationKind.MOVE, FileOperationKind.UNDO}:
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

        if (
            self._rating_batch is not None
            and self._rating_batch.active_request_id == result.request_id
        ):
            self._complete_folder_rating_rename(result)
            return

        if result.operation is FileOperationKind.MOVE and clipboard_receipt is not None:
            self._finish_paste_clipboard(result, clipboard_receipt)

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
                tr('{p0}をキャンセルしました', p0=self._operation_label(result.operation))
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
                tr('{p0}完了: 成功{p1}件、スキップ{p2}件、失敗{p3}件', p0=self._operation_label(result.operation), p1=success_count, p2=skipped_count, p3=max(0, failure_count - skipped_count))
            )
            if source_remaining_count:
                completion_message += tr('（元項目残留{p0}件）', p0=source_remaining_count)
            codes = sorted(
                {
                    item.error_code or "unknown"
                    for item in effective_items
                    if not item.success
                }
            )
            if (
                property_rename is None
                and self.file_operation_coordinator.queue is None
            ):
                QMessageBox.warning(
                    self,
                    tr('ファイル操作の一部を完了できませんでした'),
                    tr('成功: {p0}件\n失敗: {p1}件\nエラー種別: {p2}\n同名項目は上書きせずスキップします。', p0=success_count, p1=failure_count, p2=', '.join(codes)),
                )
        else:
            completion_message = (
                tr('{p0}が完了しました', p0=self._operation_label(result.operation))
            )

        if property_rename is not None:
            dialog, close_on_success = property_rename
            dialog.set_busy(False)
            renamed_item = next(
                (
                    item
                    for item in result.effective_items
                    if item.success and item.destination_path
                ),
                None,
            )
            if renamed_item is None:
                failure = next(
                    (item for item in result.effective_items if not item.success),
                    None,
                )
                dialog.show_error(
                    (
                        failure.error_message
                        if failure is not None
                        else None
                    )
                    or tr('名前を変更できませんでした')
                )
            else:
                dialog.mark_renamed(renamed_item.destination_path)
                if close_on_success:
                    dialog.accept()
                    self.list_view.setFocus(Qt.FocusReason.OtherFocusReason)

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
                FileOperationKind.CREATE_ZIP,
            FileOperationKind.UNDO}
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
            FileOperationKind.RENAME: tr('名前変更'),
            FileOperationKind.COPY: tr('コピー'),
            FileOperationKind.MOVE: tr('移動'),
            FileOperationKind.RECYCLE: tr('削除'),
            FileOperationKind.CREATE_DIRECTORY: tr('フォルダ作成'),
            FileOperationKind.CREATE_ZIP: tr('zipに圧縮'),
            FileOperationKind.UNDO: tr('元に戻す'),
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
            self._show_temporary_status(tr('現在のフォルダをお気に入りへ追加しました'))
        else:
            self._show_temporary_status(tr('このフォルダは登録済みです'))

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
            tr('フォルダをお気に入りへ追加しました')
            if added
            else tr('このフォルダは登録済みです')
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
            self._show_temporary_status(tr('現在のフォルダをお気に入りから削除しました'))
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
                tr('お気に入りの表示名'),
                tr('表示名:'),
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
            self.statusBar().showMessage(tr('ブックマーク先が見つかりません'), 3000)
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
            self.statusBar().showMessage(tr('履歴の項目が見つかりません'), 3000)
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
                tr('閲覧履歴を消去'),
                tr('閲覧履歴をすべて消去しますか？'),
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
        self._close_zip_progress_dialog()
        self.clear_file_clipboard()
        if (
            self._owns_file_operation_coordinator
            and self._active_file_operation_id is not None
        ):
            self.file_operation_coordinator.cancel()
        if self._owns_file_operation_coordinator:
            self.file_operation_coordinator.close()
        self.browser_main_drop.close()
        self.image_detail_probe.close()
        self._pending_browser_focus = None
        self._clear_active_directory_watch()
        if self._owns_directory_watcher:
            self.directory_watcher.close()
        self._cancel_pending_scan(rollback_history=False)
        self.scanner.close()
        self.location_directory_loader.close()
        self._location_directory_pending_path = None
        self._close_location_directory_popup(cancel_pending=False)
        self._close_owned_popup("_navigation_history_menu")
        self._close_owned_popup("_location_history_popup")
        self._close_owned_popup("_search_history_popup")
        self._thumbnail_request_timer.stop()
        self._browser_search_timer.stop()
        self._scroll_idle_timer.stop()
        self._scan_status_timer.stop()
        self._scan_batch_timer.stop()
        self._directory_change_timer.stop()
        self._save_window_state()
        workflow = getattr(self, "_browser_workflow", None)
        if workflow is not None:
            workflow.shutdown()
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
        if self._shutdown_prepared:
            return
        # A large initial scan may outlast the startup timer. Do not let its
        # cache-wide walk get ahead of the first visible thumbnail requests.
        if self._pending_scan is not None or self.thumbnail_provider.pending_count:
            QTimer.singleShot(500, self, self._run_idle_cache_cleanup)
            return
        if not self.thumbnail_provider.cleanup_caches_async(force=False):
            # Viewer interaction can temporarily reject Browser submissions.
            QTimer.singleShot(500, self, self._run_idle_cache_cleanup)

    def _fallback_background_delegate_options(self) -> dict[str, str]:
        """Project window-owned values for initial, layout and live updates."""
        return {
            "folder_fallback_background": self.browser_folder_fallback_background,
            "file_fallback_background": self.browser_file_fallback_background,
        }

    def _browser_icon_delegate_options(self) -> dict[str, object]:
        return {
            **{key.removeprefix("browser_"): getattr(self, key) for key in ICON_POSITION_SETTING_KEYS},
            "center_folder_icon_size": self.browser_center_folder_icon_size,
            "center_folder_icon_custom_percent": self.browser_center_folder_icon_custom_percent,
            "center_file_icon_size": self.browser_center_file_icon_size,
            "center_file_icon_custom_percent": self.browser_center_file_icon_custom_percent,
            "badge_folder_icon_size": self.browser_badge_folder_icon_size,
            "badge_folder_icon_custom_percent": self.browser_badge_folder_icon_custom_percent,
            "badge_file_icon_size": self.browser_badge_file_icon_size,
            "badge_file_icon_custom_percent": self.browser_badge_file_icon_custom_percent,
        }

    def _apply_fallback_background_settings(self, changed: dict[str, object]) -> None:
        """Color-only effect boundary: configure once and repaint; no layout/I/O."""
        folder_key = "browser_folder_fallback_background"
        file_key = "browser_file_fallback_background"
        if folder_key not in changed and file_key not in changed:
            return
        if folder_key in changed:
            self.browser_folder_fallback_background = str(changed[folder_key])
        if file_key in changed:
            self.browser_file_fallback_background = str(changed[file_key])
        self.item_delegate.configure(
            thumbnail_size=self.thumbnail_size,
            density=self.browser_display_density,
            **self._fallback_background_delegate_options(),
            **self._browser_icon_delegate_options(),
        )
        self.list_view.viewport().update()

    def _apply_browser_icon_size_settings(self, changed: dict[str, object]) -> None:
        changed_keys = {
            key
            for spec in ICON_SIZE_SETTING_SPECS
            for key in spec[:2]
            if key in changed
        }
        changed_keys.update(set(ICON_POSITION_SETTING_KEYS).intersection(changed))
        if not changed_keys:
            return
        for key in ICON_POSITION_SETTING_KEYS:
            if key in changed:
                setattr(self, key, normalize_browser_icon_margin(changed[key]))
        for key, custom_key, _label in ICON_SIZE_SETTING_SPECS:
            attr = key
            if key in changed:
                setattr(
                    self,
                    attr,
                    normalize_browser_icon_size_preset(changed[key]),
                )
            if custom_key in changed:
                setattr(
                    self,
                    custom_key,
                    normalize_browser_icon_size_custom_percent(changed[custom_key]),
                )
        self.item_delegate.configure(
            thumbnail_size=self.thumbnail_size,
            density=self.browser_display_density,
            **self._browser_icon_delegate_options(),
        )
        self.list_view.viewport().update()

    def apply_settings(self, changed: dict[str, object]) -> None:
        if "shortcut_bindings" in changed:
            self.shortcut_bindings = normalize_shortcut_bindings(
                changed["shortcut_bindings"]
            ).get("browser", {})
            self._apply_browser_shortcuts()
        if "browser_cancel_clears_filters" in changed:
            self.browser_cancel_clears_filters = bool(
                changed["browser_cancel_clears_filters"]
            )
            self._apply_browser_shortcuts()
        if "browser_tag_grouped" in changed:
            self.browser_tag_grouped = bool(changed["browser_tag_grouped"])
            self._rebuild_tag_menu()
            self._sync_tag_quick_filter_registry()
            self._sync_browser_filter_controls()
        if 'browser_tag_registry' in changed:
            self.item_delegate.tag_registry = self.config.get('browser_tag_registry', [])
            self.list_view.viewport().update()
            self._rebuild_tag_menu()
            self._sync_tag_quick_filter_registry()
        wheel_settings_changed = bool(
            {
                "browser_wheel_scroll_mode",
                "browser_wheel_scroll_custom_rows",
            }.intersection(changed)
        )
        if wheel_settings_changed:
            self.browser_wheel_scroll_mode = str(
                changed.get(
                    "browser_wheel_scroll_mode",
                    self.browser_wheel_scroll_mode,
                )
            )
            self.browser_wheel_scroll_custom_rows = int(
                changed.get(
                    "browser_wheel_scroll_custom_rows",
                    self.browser_wheel_scroll_custom_rows,
                )
            )
            self.list_view.set_wheel_scroll_policy(
                self.browser_wheel_scroll_mode,
                self.browser_wheel_scroll_custom_rows,
            )
        self._apply_fallback_background_settings(changed)
        self._apply_browser_icon_size_settings(changed)
        if "browser_location_history_limit" in changed:
            self.navigation_history.set_recent_limit(
                int(changed["browser_location_history_limit"])
            )
            self._close_owned_popup("_location_history_popup")
        search_history_changed = False
        if "browser_search_history_limit" in changed:
            search_history_changed = self.search_history.set_limit(
                int(changed["browser_search_history_limit"])
            )
        if "browser_search_history" in changed:
            value = changed["browser_search_history"]
            if isinstance(value, list):
                search_history_changed = (
                    self.search_history.replace(value) or search_history_changed
                )
        if search_history_changed:
            self._close_owned_popup("_search_history_popup")
            self._sync_search_history_control()
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
            "browser_item_spacing_x",
            "browser_item_spacing_y",
            "browser_cell_padding",
            "browser_filename_display",
            "browser_filename_elide_mode",
            "browser_filename_font_size",
            "browser_filename_show_extension",
            "browser_filename_gap",
            "browser_filename_padding_y",
        }
        list_changed = bool(list_keys.intersection(changed))
        list_changed = list_changed or "browser_random_seed" in changed
        view_state = self._capture_list_view_state() if list_changed else None

        if "browser_random_seed" in changed:
            self.browser_random_seed = normalize_browser_random_seed(changed["browser_random_seed"])

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
        if "browser_item_spacing_x" in changed:
            self.browser_item_spacing_x = max(
                0, min(32, int(changed["browser_item_spacing_x"]))
            )
        if "browser_item_spacing_y" in changed:
            self.browser_item_spacing_y = max(
                0, min(32, int(changed["browser_item_spacing_y"]))
            )
        if "browser_cell_padding" in changed:
            self.browser_cell_padding = max(
                0, min(12, int(changed["browser_cell_padding"]))
            )
        if "browser_filename_display" in changed:
            self.browser_filename_display = str(changed["browser_filename_display"])
        if "browser_filename_elide_mode" in changed:
            self.browser_filename_elide_mode = str(
                changed["browser_filename_elide_mode"]
            )
        if "browser_filename_font_size" in changed:
            self.browser_filename_font_size = max(
                0, min(24, int(changed["browser_filename_font_size"]))
            )
        if "browser_filename_show_extension" in changed:
            self.browser_filename_show_extension = bool(
                changed["browser_filename_show_extension"]
            )
        if "browser_filename_gap" in changed:
            self.browser_filename_gap = max(
                0, min(32, int(changed["browser_filename_gap"]))
            )
        if "browser_filename_padding_y" in changed:
            self.browser_filename_padding_y = max(
                0, min(16, int(changed["browser_filename_padding_y"]))
            )
        sort_changed = bool(
            {
                "browser_random_seed",
                "browser_sort_key",
                "browser_sort_order",
                "browser_folders_first",
            }.intersection(changed)
        )
        if sort_changed:
            pending_scan = self._pending_scan
            snapshot_scan = bool(
                pending_scan is not None and pending_scan.snapshot_hit
            )
            if snapshot_scan:
                self._cancel_pending_scan(rollback_history=False)
            elif (
                pending_scan is not None
                and pending_scan.committed
                and pending_scan.remaining_items
            ):
                self._scan_batch_timer.stop()
                self._flush_pending_scan_batch()
            elif pending_scan is not None and not pending_scan.committed:
                self._restart_pending_scan(pending_scan)
            self.item_model.configure_sort(
                self.browser_sort_key,
                self.browser_sort_order,
                self.browser_folders_first,
                self.browser_random_seed,
            )
            if snapshot_scan and self.current_path is not None:
                self._snapshot_reconcile_pending = True
                if not self._refresh_current_folder(
                    navigation_source="snapshot_reconcile"
                ):
                    self._snapshot_reconcile_pending = False

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
        geometry_changed = thumbnail_changed or bool(
            {
                "browser_display_density",
                "browser_item_spacing_x",
                "browser_item_spacing_y",
                "browser_cell_padding",
                "browser_filename_display",
                "browser_filename_font_size",
                "browser_filename_gap",
                "browser_filename_padding_y",
            }.intersection(changed)
        )
        if geometry_changed:
            self._apply_list_view_geometry()
        elif {
            "browser_filename_elide_mode",
            "browser_filename_show_extension",
        }.intersection(changed):
            self.item_delegate.configure(
                thumbnail_size=self.thumbnail_size,
                density=self.browser_display_density,
                filename_elide_mode=self.browser_filename_elide_mode,
                show_filename_extension=self.browser_filename_show_extension,
            )
            self.list_view.viewport().update()

        if list_changed:
            self._sync_browser_controls()
            if view_state is not None:
                self._schedule_list_view_state_restore(
                    view_state,
                    request_thumbnails=geometry_changed,
                )
            self._update_status()
            if geometry_changed:
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
        if visibility_changed:
            pending_scan = self._pending_scan
            if pending_scan is not None:
                snapshot_scan = pending_scan.snapshot_hit
                reloaded = self.navigate_to(
                    pending_scan.path,
                    record_history=pending_scan.record_history,
                    restore_location=pending_scan.restore_location,
                    force_reload=True,
                    capture_current=False,
                    failure_history_revert=(
                        pending_scan.failure_history_revert
                    ),
                    navigation_source=(
                        "snapshot_reconcile"
                        if snapshot_scan
                        else pending_scan.navigation_source
                    ),
                    trace_id=pending_scan.trace_id,
                    atomic_restore=pending_scan.atomic_restore,
                )
                if snapshot_scan and reloaded:
                    self._snapshot_reconcile_pending = True
            elif self.current_path is not None:
                self.refresh_current_folder()
        if "browser_folder_snapshot_cache_enabled" in changed:
            self.folder_snapshot_cache.set_enabled(
                bool(changed["browser_folder_snapshot_cache_enabled"])
            )
        if "browser_folder_snapshot_cache_max_entries" in changed:
            self.folder_snapshot_cache.set_max_entries(
                normalize_browser_folder_snapshot_cache_max_entries(
                    changed["browser_folder_snapshot_cache_max_entries"]
                )
            )
        if (
            "browser_folder_snapshot_cache_enabled" in changed
            or "browser_folder_snapshot_cache_max_entries" in changed
        ):
            self.item_model.configure_filter_restore_cache(
                enabled=self.folder_snapshot_cache.enabled,
                max_entries=self.folder_snapshot_cache.max_entries,
            )
        if "thumbnail_webp_quality" in changed or "thumbnail_preserve_alpha" in changed:
            self.thumbnail_webp_quality = normalize_thumbnail_webp_quality(
                changed.get("thumbnail_webp_quality", self.thumbnail_webp_quality)
            )
            self.thumbnail_preserve_alpha = changed.get("thumbnail_preserve_alpha", self.thumbnail_preserve_alpha) is True
            self._refresh_thumbnail_encoding_policy()
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
        _, key, order = BROWSER_SORT_CHOICES[self.browser_sort_key_combo.currentIndex()]
        values = {"browser_sort_key": key, "browser_sort_order": order}
        if key == BrowserSortKey.RANDOM.value:
            values["browser_random_seed"] = new_browser_random_seed(self.browser_random_seed)
        self.config.apply(values, save=True)

    def _sync_browser_controls(self) -> None:
        controls = (
            self.browser_sort_key_combo,
        )
        for control in controls:
            control.blockSignals(True)
        try:
            self.browser_sort_key_combo.setCurrentIndex(browser_sort_choice_index(
                self.browser_sort_key, self.browser_sort_order,
            ))
        finally:
            for control in controls:
                control.blockSignals(False)

    def _on_browser_search_text_changed(self, _text: str) -> None:
        self.search_query_edited.emit(self, str(_text))
        self._browser_search_timer.start()

    @property
    def active_search_query(self) -> str:
        return self.browser_filter_state.search_text

    def clear_active_browser_search(self) -> bool:
        """Clear only the active query while retaining rating and query MRU."""

        return self._replace_active_search_query("")

    def clear_browser_filters(self) -> None:
        """Cancel queued search and clear both transient filter controls."""
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return
        self._browser_search_timer.stop()
        self._set_browser_filter(BrowserFilterState.normalized())
        # Explicit clearing invalidates even a temporarily hidden return query.
        self.search_query_edited.emit(self, "")

    def restore_viewer_roundtrip_search(self, query: str) -> bool:
        """Restore one controller-authorized transient Viewer return query."""

        return self._replace_active_search_query(query)

    def _replace_active_search_query(
        self,
        query: str,
        *,
        preserve_view_state: bool = True,
        request_thumbnails: bool = True,
    ) -> bool:
        self._browser_search_timer.stop()
        state = BrowserFilterState.normalized(
            search_text=query,
            rating_mode=self.browser_filter_state.rating_mode,
            rating_reference=self.browser_filter_state.rating_reference,
            include_tags=self.browser_filter_state.include_tags,
            exclude_tags=self.browser_filter_state.exclude_tags,
            tag_match=self.browser_filter_state.tag_match,
        )
        if preserve_view_state:
            return self._set_browser_filter(state)
        if state == self.browser_filter_state:
            self._sync_browser_filter_controls()
            return False
        self._list_view_restore_token += 1
        self.browser_filter_state = state
        changed = self.item_model.configure_filter(state)
        self._sync_browser_filter_controls()
        self._update_status(force=True)
        if changed and request_thumbnails:
            self._schedule_thumbnail_requests()
        return changed

    def _commit_browser_search_history(self) -> bool:
        if self._shutdown_prepared:
            return False
        self._browser_search_timer.stop()
        self._apply_pending_browser_search()
        if not self.search_history.record(self.browser_search_edit.text()):
            return False
        self._persist_browser_search_history()
        self._sync_search_history_control()
        return True

    def _persist_browser_search_history(self) -> None:
        self.config.apply(
            {"browser_search_history": list(self.search_history.entries)},
            save=True,
        )

    def _sync_search_history_control(self) -> None:
        if not hasattr(self, "browser_search_container"):
            return
        self.browser_search_container.set_drop_down_available(
            self.search_history.limit > 0
        )
        count = len(self.search_history.entries)
        self.browser_search_container.setToolTip(
            tr('検索履歴を表示（{p0}件）', p0=count)
        )

    def _show_search_history_popup(
        self,
    ) -> BrowserLocationListPopup | None:
        if (
            self._search_history_popup is not None
            and self._search_history_popup.isVisible()
        ):
            self._search_history_popup.close()
            return None
        self._commit_browser_search_history()
        entries = self.search_history.entries
        if not entries:
            return None
        self._close_owned_popup("_search_history_popup")
        popup_entries = [
            LocationPopupEntry(query, query, query)
            for query in entries
        ]
        popup_entries.extend(
            (
                LocationPopupEntry("────────", None, enabled=False),
                LocationPopupEntry(
                    tr('検索履歴を消去'),
                    _CLEAR_SEARCH_HISTORY,
                ),
            )
        )
        popup = BrowserLocationListPopup(
            tuple(popup_entries),
            self,
            compact_rows=True,
        )
        self._search_history_popup = popup
        popup.entryActivated.connect(self._activate_search_history_entry)
        popup.closed.connect(
            lambda value=popup: self._release_owned_popup(
                "_search_history_popup",
                value,
            )
        )
        popup.show_for(self.browser_search_container)
        return popup

    def _activate_search_history_entry(
        self,
        entry: LocationPopupEntry,
    ) -> None:
        if entry.value is _CLEAR_SEARCH_HISTORY:
            if self.search_history.clear():
                self._persist_browser_search_history()
                self._sync_search_history_control()
            return
        query = str(entry.value).strip()
        if not query:
            return
        if self.search_history.record(query):
            self._persist_browser_search_history()
        self.browser_search_edit.setText(query)
        self._browser_search_timer.stop()
        self._apply_pending_browser_search()

    def _apply_pending_browser_search(self) -> None:
        self._set_browser_filter(
            BrowserFilterState.normalized(
                search_text=self.browser_search_edit.text(),
                rating_mode=self.browser_filter_state.rating_mode,
                rating_reference=self.browser_filter_state.rating_reference,
                include_tags=self.browser_filter_state.include_tags,
                exclude_tags=self.browser_filter_state.exclude_tags,
                tag_match=self.browser_filter_state.tag_match,
            )
        )

    def _on_rating_quick_filter_changed(
        self,
        mode: str,
        reference: int,
    ) -> None:
        # Compose with text typed during the short search debounce.
        self._browser_search_timer.stop()
        self._set_browser_filter(
            BrowserFilterState.normalized(
                search_text=self.browser_search_edit.text(),
                rating_mode=mode,
                rating_reference=reference,
                include_tags=self.browser_filter_state.include_tags,
                exclude_tags=self.browser_filter_state.exclude_tags,
                tag_match=self.browser_filter_state.tag_match,
            )
        )

    def _set_browser_filter(self, state: BrowserFilterState) -> bool:
        normalized = BrowserFilterState.normalized(
            search_text=state.search_text,
            rating_mode=state.rating_mode,
            rating_reference=state.rating_reference,
            include_tags=state.include_tags,
            exclude_tags=state.exclude_tags,
            tag_match=state.tag_match,
        )
        if normalized == self.browser_filter_state:
            self._sync_browser_filter_controls()
            return False
        view_state = self._capture_list_view_state()
        self.browser_filter_state = normalized
        changed = self.item_model.configure_filter(normalized)
        self._sync_browser_filter_controls()
        if not changed:
            return False
        restored = self._coerce_list_view_state_to_visible(view_state)
        self._schedule_list_view_state_restore(restored)
        self._update_status(force=True)
        self._schedule_thumbnail_requests()
        return True

    def _sync_browser_filter_controls(self) -> None:
        if hasattr(self, 'tag_quick_filter_strip'):
            self.tag_quick_filter_strip.set_filter_state(
                self.browser_filter_state
            )
        if hasattr(self, 'tag_button'):
            count = len(self.browser_filter_state.include_tags) + len(self.browser_filter_state.exclude_tags)
            self.tag_button.setText(tr('タグ') if not count else tr('タグ ({p0})', p0=count))
            self._reserve_tag_button_width()
            self._sync_grouped_tag_menu_actions()
            self._update_tag_quick_filter_geometry(force=True)
        if not hasattr(self, "rating_filter_widget"):
            return
        if self.browser_search_edit.text() != self.browser_filter_state.search_text:
            self.browser_search_edit.blockSignals(True)
            try:
                self.browser_search_edit.setText(
                    self.browser_filter_state.search_text
                )
            finally:
                self.browser_search_edit.blockSignals(False)
        self.rating_filter_widget.blockSignals(True)
        try:
            self.rating_filter_widget.set_filter(
                self.browser_filter_state.rating_mode,
                self.browser_filter_state.rating_reference,
            )
        finally:
            self.rating_filter_widget.blockSignals(False)

    def _reserve_tag_button_width(self) -> None:
        """Reserve the translated two-digit label without displaying a dummy."""
        button = getattr(self, 'tag_button', None)
        if button is None:
            return
        metrics = QFontMetrics(button.font())
        current_width = metrics.horizontalAdvance(button.text())
        natural_width = button.sizeHint().width()
        if not hasattr(self, '_tag_button_text_extra_width'):
            self._tag_button_text_extra_width = max(
                0,
                natural_width - current_width,
            )
        representative = tr('タグ ({p0})', p0=99)
        reserved = metrics.horizontalAdvance(representative) + int(
            self._tag_button_text_extra_width
        )
        if reserved > button.minimumWidth():
            button.setMinimumWidth(reserved)

    def _rebuild_tag_menu(self) -> None:
        menu = getattr(self, 'tag_menu', None)
        if menu is None:
            return
        menu.clear()
        self._grouped_tag_actions: dict[str, QAction] = {}
        registry = self.config.get('browser_tag_registry', [])
        if self.browser_tag_grouped:
            for entry in registry:
                name = entry['name']
                action = menu.addAction(name.replace('&', '&&'))
                action.setCheckable(True)
                pixmap = QPixmap(10, 10)
                pixmap.fill(QColor(entry['color']))
                action.setIcon(QIcon(pixmap))
                action.triggered.connect(
                    lambda _checked=False, tag=name: self._on_tag_quick_filter_activated(
                        tag,
                        QApplication.keyboardModifiers(),
                        Qt.MouseButton.LeftButton,
                    )
                )
                self._grouped_tag_actions[name] = action
            if self._grouped_tag_actions:
                menu.addSeparator()
        menu.addAction(tr('選択項目のタグ'), lambda: self.edit_selected_tags())
        menu.addAction(tr('タグで絞り込み'), self.edit_tag_filter)
        menu.addSeparator()
        menu.addAction(tr('タグの管理'), lambda: self.manage_tags())
        self._tag_clear_action = menu.addAction(
            tr('選択の解除'), self._clear_tag_filters
        )
        self._sync_grouped_tag_menu_actions()

    def _sync_grouped_tag_menu_actions(self) -> None:
        actions = getattr(self, '_grouped_tag_actions', {})
        include = set(self.browser_filter_state.include_tags)
        exclude = set(self.browser_filter_state.exclude_tags)
        for name, action in actions.items():
            action.setChecked(name in include)
            label = f'− {name}' if name in exclude else name
            action.setText(label.replace('&', '&&'))
            action.setToolTip(
                tr('タグを除外中: {p0}', p0=name)
                if name in exclude
                else tr('クリックでタグ絞り込み: {p0}', p0=name)
            )
        clear_action = getattr(self, '_tag_clear_action', None)
        if clear_action is not None:
            clear_action.setEnabled(bool(include or exclude))

    def _clear_tag_filters(self) -> None:
        self._browser_search_timer.stop()
        self._set_browser_filter(
            BrowserFilterState.normalized(
                search_text=self.browser_search_edit.text(),
                rating_mode=self.browser_filter_state.rating_mode,
                rating_reference=self.browser_filter_state.rating_reference,
                tag_match='all',
            )
        )

    def _sync_tag_quick_filter_registry(self) -> None:
        strip = getattr(self, 'tag_quick_filter_strip', None)
        layout = getattr(self, '_rating_filter_layout', None)
        if strip is None or layout is None:
            return
        registry = self.config.get('browser_tag_registry', [])
        strip.set_registry(registry)
        in_layout = layout.indexOf(strip) >= 0
        has_tags = bool(strip.registry)
        if self.browser_tag_grouped:
            if in_layout:
                layout.removeWidget(strip)
            strip.hide()
            self._refresh_rating_filter_corner_layout()
            return
        if has_tags and not in_layout:
            layout.insertWidget(
                0,
                strip,
                0,
                Qt.AlignmentFlag.AlignVCenter,
            )
        elif not has_tags and in_layout:
            layout.removeWidget(strip)
            strip.hide()
        if has_tags:
            strip.show()
        self._refresh_rating_filter_corner_layout()
        self._update_tag_quick_filter_geometry(force=True)

    def _refresh_rating_filter_corner_layout(self) -> None:
        """Propagate corner-widget size changes through QMenuBar immediately."""
        container = getattr(self, 'rating_filter_container', None)
        layout = getattr(self, '_rating_filter_layout', None)
        if container is None or layout is None:
            return
        layout.invalidate()
        layout.activate()
        hint = layout.sizeHint()
        if hint.isValid():
            container.setFixedWidth(max(0, hint.width()))
        container.updateGeometry()
        menu_bar = self.menuBar()
        menu_bar.updateGeometry()
        menu_layout = menu_bar.layout()
        if menu_layout is not None:
            menu_layout.invalidate()
            menu_layout.activate()
        # QMenuBar does not always reposition an already installed corner
        # widget when only its size hint changes. Keep the rating group
        # anchored to the menu bar's right edge synchronously.
        if container.parentWidget() is menu_bar:
            geometry = container.geometry()
            container.move(
                max(0, menu_bar.width() - container.width()),
                geometry.y(),
            )
        menu_bar.update()

    def _update_tag_quick_filter_geometry(self, *, force: bool = False) -> None:
        strip = getattr(self, 'tag_quick_filter_strip', None)
        if self.browser_tag_grouped:
            self._refresh_rating_filter_corner_layout()
            return
        if strip is None or not strip.registry:
            return
        menu_bar = self.menuBar()
        settings_action = getattr(self, 'settings_action', None)
        if settings_action is None:
            strip.set_available_width(menu_bar.width())
            return
        settings_rect = menu_bar.actionGeometry(settings_action)
        rating_layout = getattr(self, '_rating_filter_layout', None)
        if rating_layout is None:
            return
        fixed_width = (
            max(
                self.tag_button.minimumWidth(),
                self.tag_button.sizeHint().width(),
            )
            + self.rating_filter_widget.sizeHint().width()
            + rating_layout.contentsMargins().left()
            + rating_layout.contentsMargins().right()
            + rating_layout.spacing() * 2
        )
        # The quick buttons grow into the space before the right-anchored
        # corner controls. Reserve the menu action row and the existing tag /
        # rating controls so Settings and the menu actions remain reachable.
        menu_width = menu_bar.width()
        if settings_rect.isValid() and settings_rect.width() > 0:
            available = menu_width - settings_rect.right() - fixed_width - 8
        else:
            available = menu_width - fixed_width - 8
        strip.set_available_width(max(0, available))
        self._refresh_rating_filter_corner_layout()

    def _on_tag_quick_filter_activated(
        self,
        name: str,
        modifiers: object,
        _button: object,
    ) -> None:
        # A click can arrive during the 100 ms search debounce. Compose with
        # the editor's current text so the pending keystrokes are not lost.
        self._browser_search_timer.stop()
        modifier_flags = Qt.KeyboardModifier(modifiers)
        include = list(self.browser_filter_state.include_tags)
        exclude = list(self.browser_filter_state.exclude_tags)
        if modifier_flags & Qt.KeyboardModifier.ShiftModifier:
            if name in exclude:
                exclude.remove(name)
            else:
                exclude.append(name)
                if name in include:
                    include.remove(name)
        else:
            if name in include:
                include.remove(name)
            else:
                include.append(name)
                if name in exclude:
                    exclude.remove(name)
            # Ctrl-click follows ZipPlaFork's OR include gesture while keeping
            # the existing exclusion list and filename/rating predicates.
        if modifier_flags & Qt.KeyboardModifier.ControlModifier:
            tag_match = 'any'
        else:
            tag_match = self.browser_filter_state.tag_match
        self._set_browser_filter(
            BrowserFilterState.normalized(
                search_text=self.browser_search_edit.text(),
                rating_mode=self.browser_filter_state.rating_mode,
                rating_reference=self.browser_filter_state.rating_reference,
                include_tags=tuple(include),
                exclude_tags=tuple(exclude),
                tag_match=tag_match,
            )
        )

    def _coerce_list_view_state_to_visible(
        self,
        state: _ListViewState,
    ) -> _ListViewState:
        selected_paths = tuple(
            path
            for path in state.selected_paths
            if self.item_model.row_for_path(path) >= 0
        )
        current_path = state.current_path
        if current_path and self.item_model.row_for_path(current_path) < 0:
            current_path = None
        return _ListViewState(
            selected_paths=selected_paths,
            current_path=current_path,
            anchor_path=(
                state.anchor_path
                if state.anchor_path
                and self.item_model.row_for_path(state.anchor_path) >= 0
                else None
            ),
            anchor_row=state.anchor_row,
            anchor_x=state.anchor_x,
            anchor_y=state.anchor_y,
            vertical_scroll=state.vertical_scroll,
            horizontal_scroll=state.horizontal_scroll,
        )

    def _apply_list_view_geometry(self) -> None:
        self.item_delegate.configure(
            thumbnail_size=self.thumbnail_size,
            density=self.browser_display_density,
            frame_ratio_id=self.thumbnail_frame_ratio,
            thumbnail_display_mode=self.browser_thumbnail_display_mode,
            cell_padding=self.browser_cell_padding,
            filename_display=self.browser_filename_display,
            filename_elide_mode=self.browser_filename_elide_mode,
            filename_font_size=self.browser_filename_font_size,
            show_filename_extension=self.browser_filename_show_extension,
            filename_gap=self.browser_filename_gap,
            filename_padding_y=self.browser_filename_padding_y,
            item_spacing_x=self.browser_item_spacing_x,
            item_spacing_y=self.browser_item_spacing_y,
            **self._fallback_background_delegate_options(),
            **self._browser_icon_delegate_options(),
        )
        self.list_view.setIconSize(QSize(self.thumbnail_size, self.thumbnail_size))
        self.list_view.setGridSize(self.item_delegate.grid_metrics.grid_size)
        self.list_view.setSpacing(0)  # Inter-item gaps belong to grid_size.
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
        self._update_selected_detail()

    def _on_list_selection_changed(self, selected, deselected) -> None:
        for index in tuple(selected.indexes()) + tuple(deselected.indexes()):
            self._update_index_rect(index)
        self._update_status()
        self._update_selected_detail()
        self._update_file_action_states()
        workflow = getattr(self, "_browser_workflow", None)
        if workflow is not None:
            workflow.recenter_cache_retention()

    def _finish_paste_clipboard(
        self,
        result: FileOperationResult,
        receipt: ClipboardPasteReceipt,
    ) -> None:
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if not receipt.matches(self._internal_clipboard_state, mime):
            return
        if (
            receipt.internal_request_identity is not None
            and not receipt.matches_internal_snapshot(self._internal_clipboard_state)
        ):
            return

        if receipt.privately_owned and receipt.matches_internal_snapshot(
            self._internal_clipboard_state
        ):
            top_level = {
                self._path_key(item.source_path): item
                for item in result.items
                if item.source_path
            }
            remaining_list: list[str] = []
            for path in receipt.internal_paths:
                item = top_level.get(self._path_key(path))
                if item is None:
                    remaining_list.append(path)
                elif item.success and not item.partial_success and not item.partially_completed:
                    continue
                elif item.child_results and item.retry_source_paths:
                    remaining_list.extend(item.retry_source_paths)
                else:
                    remaining_list.append(path)
            remaining = tuple(dict.fromkeys(remaining_list))
            if remaining and remaining != receipt.internal_paths:
                self._set_file_clipboard(remaining, cut=True)
                return

        fully_moved = bool(result.items) and all(
            item.success
            and not item.partial_success
            and not item.partially_completed
            and (item.source_removed or item.source_root_removed is True)
            for item in result.items
        )
        if not fully_moved or not receipt.matches(
            self._internal_clipboard_state,
            clipboard.mimeData(),
        ):
            return
        if (
            receipt.internal_request_identity is not None
            and not receipt.matches_internal_snapshot(self._internal_clipboard_state)
        ):
            return
        # A successful cut is one-shot. The Windows sequence + Qt MIME identity
        # check above prevents an older queued completion from clearing a newer
        # clipboard, even when its URLs happen to be identical.
        clipboard.clear(QClipboard.Mode.Clipboard)
        if receipt.matches_internal_snapshot(self._internal_clipboard_state):
            self.clear_file_clipboard()

    def _rating_hit(self, position: QPoint) -> tuple[BrowserItem, int] | None:
        index = self.list_view.indexAt(position)
        item = self.item_model.item_at(index)
        if item is None:
            return None
        cell_rect = self.list_view.visualRect(index)
        rating = self.item_delegate.rating_at_position(
            cell_rect,
            position,
            self.list_view.font(),
        )
        if rating is None:
            return None
        return item, rating

    def _update_rating_hover(self, position: QPoint) -> None:
        hit = self._rating_hit(position)
        new_path = str(hit[0].path) if hit is not None else None
        new_rating = hit[1] if hit is not None else None
        if self._rating_hover_path and (
            new_path is None
            or not self._same_path(self._rating_hover_path, new_path)
        ):
            self.item_model.set_rating_preview(self._rating_hover_path, None)
            self._rating_hover_path = None
        if new_path is not None:
            self.item_model.set_rating_preview(new_path, new_rating)
            self._rating_hover_path = new_path

    def _clear_rating_hover(self) -> None:
        if self._rating_hover_path is not None:
            self.item_model.set_rating_preview(self._rating_hover_path, None)
            self._rating_hover_path = None
        self._rating_press = None

    def set_rating_for_paths(
        self,
        paths: tuple[str, ...],
        rating: int | None,
        *,
        tag_changes: dict[str, bool | None] | None = None,
    ) -> bool:
        """Apply filename ratings without rescanning or decoding thumbnails."""

        if (
            not paths
            or self.file_operation_coordinator.busy
            or self._rating_batch is not None
        ):
            return False
        normalized_rating = (
            int(rating)
            if rating is not None and 1 <= int(rating) <= 5
            else None
        )
        existing_items: list[tuple[str, BrowserItemKind]] = []
        for path in paths:
            row = self.item_model.row_for_path(path)
            item = self.item_model.item_at(row) if row >= 0 else None
            if item is None:
                continue
            existing_items.append(
                (str(self._absolute_browser_path(path)), item.kind)
            )
        if not existing_items:
            return False
        if tag_changes is not None:
            # A draft applied without edits must not close an open Viewer.
            existing_items = [(path, kind) for path, kind in existing_items
                              if ZipPlaFilenameMetadata.parse(path).with_tag_changes(tag_changes)
                              != ZipPlaFilenameMetadata.parse(path)]
            if not existing_items:
                return False
        existing = tuple(path for path, _kind in existing_items)
        # A selection-triggered Pillow header probe briefly owns a Windows
        # file handle. Retire its stale pending work and drain only the active
        # header read before the same-file rename, otherwise WinError 32 can
        # turn a direct star click into a spurious failure.
        self._detail_generation += 1
        self._detail_request_identity = None
        if any(kind in {BrowserItemKind.IMAGE, BrowserItemKind.FOLDER}
               for _path, kind in existing_items):
            self.image_detail_probe.close()
        # Folder books retain immutable page identities for their lifetime.
        # Reuse the established safety contract instead of leaving an open
        # Viewer with a stale page path after the metadata rename.
        direct_file_paths = tuple(
            path
            for path, kind in existing_items
            if kind is not BrowserItemKind.FOLDER
        )
        if (
            direct_file_paths
            and not self._confirm_and_close_affected_viewers(direct_file_paths)
        ):
            return False

        state = self._capture_list_view_state()
        batch = _RatingRenameBatch(
            normalized_rating,
            state,
            tag_edit=tag_changes is not None,
        )
        # A single archive tag edit has no Pillow detail handle to drain.
        # Publish its successful filename change before a possibly slow
        # profile-database relocation on rotational storage.
        quick_archive_tag = (
            tag_changes is not None
            and len(existing_items) == 1
            and existing_items[0][1] is BrowserItemKind.ARCHIVE
        )
        pending_metadata_relocation: tuple[str, str] | None = None
        for path, kind in existing_items:
            original_metadata = ZipPlaFilenameMetadata.parse(path)
            metadata = (original_metadata.with_tag_changes(tag_changes) if tag_changes is not None
                        else original_metadata.with_rating(normalized_rating))
            if tag_changes is not None and metadata == original_metadata:
                continue
            if kind is BrowserItemKind.FOLDER:
                destination = metadata.serialized_path(is_directory=True)
                if str(Path(path)) == str(destination):
                    continue
                batch.pending_folders.append((path, destination.name))
                continue

            result = (self.rating_rename_service.set_metadata(metadata) if tag_changes is not None
                      else self.rating_rename_service.set_rating(path, normalized_rating))
            if not result.success:
                batch.failures.append(
                    f"{Path(path).name}: {result.error_message or tr('変更できません')}"
                )
                continue
            if not result.changed:
                continue
            old_path = str(result.source_path)
            new_path = str(result.destination_path)
            batch.replacements.append((old_path, new_path, result.rating))
            if quick_archive_tag:
                pending_metadata_relocation = (old_path, new_path)
            elif self.metadata_store is not None:
                self.metadata_store.relocate_item(old_path, new_path)
            self.navigation_history.relocate_tree(old_path, new_path)

        if batch.pending_folders:
            self._rating_batch = batch
            if self._start_next_folder_rating_rename():
                return True
            return bool(batch.replacements) and not batch.failures
        result = self._finalize_rating_batch(batch)
        if pending_metadata_relocation is not None and self.metadata_store is not None:
            new_path = pending_metadata_relocation[1]
            row = self.item_model.row_for_path(new_path)
            if row >= 0:
                rect = self.list_view.visualRect(self.item_model.index(row, 0))
                if not rect.isEmpty():
                    self.list_view.viewport().repaint(rect)
            self.metadata_store.relocate_item(*pending_metadata_relocation)
        return result

    def _start_next_folder_rating_rename(self) -> bool:
        batch = self._rating_batch
        if batch is None:
            return False
        while batch.pending_folders:
            source_path, new_name = batch.pending_folders.pop(0)
            if self._start_file_operation(
                FileOperationKind.RENAME,
                sources=(source_path,),
                new_name=new_name,
            ):
                batch.active_request_id = self._file_operation_request_id
                return True
            batch.failures.append(
                tr('{p0}: 名前変更を開始できませんでした', p0=Path(source_path).name)
            )
        self._finalize_rating_batch(batch)
        return False

    def _complete_folder_rating_rename(
        self,
        result: FileOperationResult,
    ) -> None:
        batch = self._rating_batch
        if batch is None or batch.active_request_id != result.request_id:
            return
        batch.active_request_id = None
        item = result.effective_items[0] if result.effective_items else None
        if (
            item is not None
            and item.success
            and item.source_path
            and item.destination_path
        ):
            batch.replacements.append(
                (item.source_path, item.destination_path,
                 ZipPlaFilenameMetadata.parse(item.destination_path).rating)
            )
        else:
            source = (
                item.source_path
                if item is not None and item.source_path
                else tr('フォルダ')
            )
            message = (
                item.error_message
                if item is not None and item.error_message
                else tr('変更できません')
            )
            batch.failures.append(f"{Path(source).name}: {message}")
        if not self._start_next_folder_rating_rename():
            return

    def _finalize_rating_batch(self, batch: _RatingRenameBatch) -> bool:
        self.file_operation_coordinator.invalidate_undo()
        if self._rating_batch is batch:
            self._rating_batch = None
        replacements = batch.replacements
        if replacements:
            self._clear_rating_hover()
            self.item_model.apply_rating_renames(tuple(replacements))
            remap = {
                self._path_key(old): new for old, new, _value in replacements
            }

            def relocated(path: str | None) -> str | None:
                if path is None:
                    return None
                return remap.get(self._path_key(path), path)

            relocated_state = _ListViewState(
                selected_paths=tuple(
                    relocated(path) or path
                    for path in batch.view_state.selected_paths
                ),
                current_path=relocated(batch.view_state.current_path),
                anchor_path=relocated(batch.view_state.anchor_path),
                anchor_row=batch.view_state.anchor_row,
                anchor_x=batch.view_state.anchor_x,
                anchor_y=batch.view_state.anchor_y,
                vertical_scroll=batch.view_state.vertical_scroll,
                horizontal_scroll=batch.view_state.horizontal_scroll,
            )
            visible_state = self._coerce_list_view_state_to_visible(
                relocated_state
            )
            self._schedule_list_view_state_restore(visible_state)
            self._update_selected_detail()

        changed_count = len(replacements)
        if batch.failures:
            summary = (
                tr('タグ変更: {p0}件成功、{p1}件失敗', p0=changed_count, p1=len(batch.failures)) if getattr(batch, 'tag_edit', False) else
                tr('レート変更: {p0}件成功、{p1}件失敗', p0=changed_count, p1=len(batch.failures))
            )
            self._show_temporary_status(summary, 5000)
            logging.getLogger("nivisviewer.rating").warning(
                "%s: %s", summary, "; ".join(batch.failures)
            )
        elif changed_count:
            self._show_temporary_status(tr('{p0}件のタグを変更しました', p0=changed_count) if getattr(batch, 'tag_edit', False) else
                                        tr('{p0}件のレートを変更しました', p0=changed_count))
        return changed_count > 0 and not batch.failures

    def manage_tags(self, new_names=()) -> None:
        from .browser_tag_dialogs import TagManagerDialog
        registry = list(self.config.get('browser_tag_registry', []))
        registry.extend({'name': name, 'color': '#80bfff'} for name in new_names
                        if name not in {tag['name'] for tag in registry})
        dialog = TagManagerDialog(registry, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config.apply({'browser_tag_registry': dialog.registry()}, save=True)

    def edit_selected_tags(self, paths=None) -> None:
        from .browser_tag_dialogs import ItemTagsDialog
        paths = tuple(paths) if paths is not None else self.selected_file_operation_paths()
        if not paths or self.file_operation_coordinator.busy or self._rating_batch is not None:
            return
        dialog = ItemTagsDialog(paths, self.config.get('browser_tag_registry', []), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_tags_for_paths(paths, dialog.changes())

    def set_tags_for_paths(self, paths: tuple[str, ...], changes: dict[str, bool | None]) -> bool:
        return self.set_rating_for_paths(paths, None, tag_changes=changes)

    def edit_tag_filter(self) -> None:
        from .browser_tag_dialogs import TagFilterDialog
        previous_focus = QApplication.focusWidget()
        dialog = TagFilterDialog(self.browser_filter_state, self.config.get('browser_tag_registry', []), self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted:
            self._browser_search_timer.stop()
            self._set_browser_filter(BrowserFilterState.normalized(
                search_text=self.browser_search_edit.text(),
                rating_mode=self.browser_filter_state.rating_mode,
                rating_reference=self.browser_filter_state.rating_reference,
                **dialog.filter_values(),
            ))
        if self.isVisible() and QApplication.activeModalWidget() is None:
            target = (
                previous_focus
                if isinstance(previous_focus, QWidget)
                and previous_focus.isVisible()
                and self.isAncestorOf(previous_focus)
                and not self._is_browser_editing_surface(previous_focus)
                else getattr(self, "tag_button", None)
            )
            if isinstance(target, QWidget):
                self.activateWindow()
                target.setFocus(Qt.FocusReason.PopupFocusReason)

    @staticmethod
    def _format_file_size(size: int | None) -> str:
        if size is None:
            return "—"
        value = float(max(0, int(size)))
        units = ("B", "KB", "MB", "GB", "TB")
        unit = units[0]
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                break
            value /= 1024.0
        if unit == "B":
            return f"{int(value)} B"
        return f"{value:.1f} {unit}"

    def _set_selected_metadata(
        self,
        size_text: str = "",
        secondary_text: str = "",
    ) -> None:
        self.file_size_label.setText(size_text)
        self.file_detail_label.setText(secondary_text)

    def _compact_status_bar_if_idle(self) -> None:
        if self._shutdown_prepared or self._active_file_operation_id is not None:
            return
        status_bar = self.statusBar()
        idle_height = max(
            BROWSER_STATUS_BAR_MIN_IDLE_HEIGHT,
            status_bar.fontMetrics().height() + 6,
        )
        status_bar.setMaximumHeight(idle_height)

    def _update_selected_detail(self) -> None:
        if not hasattr(self, "file_detail_label"):
            return
        self._detail_generation += 1
        generation = self._detail_generation
        indexes = self.list_view.selectionModel().selectedIndexes()
        if len(indexes) != 1:
            self._detail_request_identity = None
            self._page_count_request_identity = None
            self._cancel_obsolete_page_count_requests(None)
            self._set_selected_metadata()
            return
        item = self.item_model.item_at(indexes[0])
        if item is None:
            self._detail_request_identity = None
            self._page_count_request_identity = None
            self._cancel_obsolete_page_count_requests(None)
            self._set_selected_metadata()
            return
        size_text = self._format_file_size(item.file_size)
        if item.kind in {
            BrowserItemKind.FOLDER,
            BrowserItemKind.ARCHIVE,
        }:
            self._detail_request_identity = None
            count_text = (
                "—" if item.page_count is None else str(item.page_count)
            )
            self._set_selected_metadata(
                size_text,
                tr('{p0} ページ', p0=count_text),
            )
            if item.page_count is None:
                self._page_count_request_identity = (
                    generation,
                    self._path_key(item.path),
                )
                self._cancel_obsolete_page_count_requests(item.path)
                self._request_selected_page_count()
            else:
                self._page_count_request_identity = None
                self._cancel_obsolete_page_count_requests(None)
            return
        self._page_count_request_identity = None
        self._cancel_obsolete_page_count_requests(None)
        if item.kind is not BrowserItemKind.IMAGE:
            self._detail_request_identity = None
            self._set_selected_metadata(size_text)
            return
        path = str(item.path)
        dimensions = self.item_model.image_dimensions(path)
        if dimensions is not None:
            self._detail_request_identity = None
            self._set_selected_metadata(
                size_text,
                f"{dimensions[0]} × {dimensions[1]}",
            )
            return
        self._set_selected_metadata(size_text, "—")
        identity = (generation, self._path_key(path))
        self._detail_request_identity = identity
        self.image_detail_probe.request(path, generation)

    def _cancel_obsolete_page_count_requests(
        self,
        keep_path: str | Path | None,
    ) -> None:
        cancel = getattr(
            self.thumbnail_provider,
            "cancel_page_count_requests_except",
            None,
        )
        if callable(cancel):
            cancel(keep_path, generation=self._generation)

    def _request_selected_page_count(self) -> None:
        identity = self._page_count_request_identity
        if identity is None or identity[0] != self._detail_generation:
            return
        indexes = self.list_view.selectionModel().selectedIndexes()
        if len(indexes) != 1:
            return
        item = self.item_model.item_at(indexes[0])
        if (
            item is None
            or self._path_key(item.path) != identity[1]
            or item.page_count is not None
        ):
            return
        request = getattr(
            self.thumbnail_provider,
            "request_page_count",
            None,
        )
        if request is not None:
            request(
                item,
                generation=self._generation,
                priority=ThumbnailPriority.SELECTED,
            )

    def _on_page_count_ready(
        self,
        path: str,
        generation: int,
        page_count: int,
    ) -> None:
        if self._shutdown_prepared or generation != self._generation:
            return
        path_key = self._path_key(path)
        self.item_model.set_page_count(path, page_count)
        indexes = self.list_view.selectionModel().selectedIndexes()
        if len(indexes) != 1:
            return
        item = self.item_model.item_at(indexes[0])
        if (
            item is None
            or self._path_key(item.path) != path_key
            or item.kind
            not in {BrowserItemKind.FOLDER, BrowserItemKind.ARCHIVE}
        ):
            return
        identity = self._page_count_request_identity
        if identity is not None and identity[1] != path_key:
            return
        self._page_count_request_identity = None
        self._set_selected_metadata(
            self._format_file_size(item.file_size),
            tr('{p0} ページ', p0=max(0, int(page_count))),
        )

    def _on_image_detail_completed(
        self,
        result: BrowserImageDetailResult,
    ) -> None:
        if self._shutdown_prepared:
            return
        identity = (int(result.generation), self._path_key(result.path))
        if identity != self._detail_request_identity:
            return
        indexes = self.list_view.selectionModel().selectedIndexes()
        if len(indexes) != 1:
            return
        item = self.item_model.item_at(indexes[0])
        if item is None or self._path_key(item.path) != identity[1]:
            return
        self._detail_request_identity = None
        size_text = self._format_file_size(item.file_size)
        if result.dimensions is None:
            self._set_selected_metadata(size_text, "—")
            return
        self.item_model.set_image_dimensions(item.path, result.dimensions)
        self._set_selected_metadata(
            size_text,
            f"{result.dimensions[0]} × {result.dimensions[1]}",
        )

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
            pdfium_service=self.pdfium_service,
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
            if event.type() == QEvent.Type.Show:
                self._update_tag_quick_filter_geometry(force=True)
            QTimer.singleShot(0, self._install_screen_tracking)
            QTimer.singleShot(0, self._reevaluate_thumbnail_dpr)
        return handled

    def _handle_extra_button_event(
        self,
        watched: object,
        event: QEvent,
    ) -> bool | None:
        if (
            event.type()
            not in {
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick,
            }
            or not isinstance(event, QMouseEvent)
            or event.button()
            not in {
                Qt.MouseButton.BackButton,
                Qt.MouseButton.ForwardButton,
            }
        ):
            return None
        owned = isinstance(watched, QWidget) and (
            watched is self or self.isAncestorOf(watched)
        )
        button = event.button()
        if event.type() == QEvent.Type.MouseButtonRelease:
            self._pressed_extra_buttons.discard(button)
            return True if owned else None
        if not owned:
            return None
        address_target = (
            watched is self.address_bar
            or self.address_bar.isAncestorOf(watched)
        )
        if event.type() == QEvent.Type.MouseButtonDblClick:
            # Qt can classify a rapid XButton press as DblClick. If the
            # leading press was already handled, consume only the duplicate;
            # if it was the first observable packet, admit it once here.
            if button in self._pressed_extra_buttons:
                return True
        elif button in self._pressed_extra_buttons:
            # A lost release must not latch the next physical press forever.
            self._pressed_extra_buttons.discard(button)
        self._pressed_extra_buttons.add(button)
        if not address_target:
            if button == Qt.MouseButton.BackButton:
                self.go_back()
            else:
                self.go_forward()
        return True

    def _is_browser_history_key_surface(self, watched: object) -> bool:
        """Return whether a key event belongs to a Browser navigation view."""
        if not isinstance(watched, QWidget):
            return False
        application = QApplication.instance()
        if application is not None and (
            application.activeModalWidget() is not None
            or application.activePopupWidget() is not None
        ):
            return False
        for candidate in (watched, application.focusWidget() if application else None):
            current = candidate
            while isinstance(current, QWidget) and current is not self:
                if isinstance(
                    current,
                    (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox),
                ):
                    return False
                if isinstance(current, QComboBox) and current.isEditable():
                    return False
                current = current.parentWidget()
        for name in (
            "list_view",
            "folder_tree",
            "favorite_view",
            "history_view",
            "bookmark_view",
        ):
            view = getattr(self, name, None)
            if isinstance(view, QWidget) and (
                watched is view or view.isAncestorOf(watched)
            ):
                return True
        return False

    def _browser_shortcut_has(self, action_id: str, event: QKeyEvent) -> bool:
        combined = int(event.key()) | int(event.modifiers().value)
        sequence = canonical_key(QKeySequence(combined))
        return sequence in {
            canonical_key(value)
            for value in self.shortcut_bindings.get(action_id, [])
        }

    def _apply_browser_shortcuts(self) -> None:
        for shortcut in getattr(self, "_browser_dynamic_shortcuts", []):
            shortcut.setKey(QKeySequence())
            shortcut.deleteLater()
        self._browser_dynamic_shortcuts: list[QShortcut] = []
        shortcut_map = {
            "browser_focus_address": "focus_address_shortcut",
            "browser_rename": "rename_shortcut",
            "browser_delete": "recycle_shortcut",
            "browser_copy": "copy_shortcut",
            "browser_cut": "cut_shortcut",
            "browser_paste": "paste_shortcut",
            "browser_new_folder": "new_folder_shortcut",
            "browser_toggle_folder_bookmark": "toggle_folder_bookmark_shortcut",
            "browser_cancel": "clear_browser_search_shortcut",
            "browser_clear_filters": "clear_filters_shortcut",
        }
        shortcut_handlers = {
            "browser_focus_address": self.focus_address_bar,
            "browser_rename": self.rename_selected_item,
            "browser_delete": self.move_selected_to_recycle_bin,
            "browser_copy": self.copy_selected_items,
            "browser_cut": self.cut_selected_items,
            "browser_paste": self.paste_items,
            "browser_new_folder": self.create_new_folder,
            "browser_toggle_folder_bookmark": self.toggle_current_folder_bookmark,
            "browser_cancel": self._handle_browser_cancel_shortcut,
            "browser_clear_filters": self._handle_browser_clear_filters_shortcut,
        }
        for action_id, attribute in shortcut_map.items():
            shortcut = getattr(self, attribute, None)
            values = self.shortcut_bindings.get(action_id, [])
            if action_id == "browser_clear_filters" and self.browser_cancel_clears_filters:
                values = []
            if shortcut is not None:
                shortcut.setKey(QKeySequence(values[0]) if values else QKeySequence())
                handler = shortcut_handlers[action_id]
                for value in values[1:]:
                    extra = QShortcut(QKeySequence(value), shortcut.parent())
                    extra.setContext(shortcut.context())
                    extra.activated.connect(handler)
                    self._browser_dynamic_shortcuts.append(extra)
        action_map = {
            "browser_back": getattr(self, "back_action", None),
            "browser_forward": getattr(self, "forward_action", None),
            "browser_up": getattr(self, "up_action", None),
            "browser_refresh": getattr(self, "refresh_action", None),
        }
        for action_id, action in action_map.items():
            if action is not None:
                action.setShortcuts([
                    QKeySequence(value)
                    for value in self.shortcut_bindings.get(action_id, [])
                ])

    @staticmethod
    def _is_browser_editing_surface(watched: object) -> bool:
        current = watched
        while isinstance(current, QWidget):
            if isinstance(
                current,
                (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox),
            ):
                return True
            if isinstance(current, QComboBox) and current.isEditable():
                return True
            current = current.parentWidget()
        return False

    def _is_browser_cancel_surface(self, watched: object) -> bool:
        if self._is_browser_editing_surface(watched):
            return False
        return self._is_browser_cancel_command_surface(watched)

    def _is_browser_cancel_command_surface(self, watched: object) -> bool:
        """Return whether a non-editing widget belongs to this Browser window."""

        # A child QDialog can have this Browser as its parent while still
        # owning an independent keyboard context.
        return isinstance(watched, QWidget) and watched.window() is self

    def _browser_list_view_owns_cancel(self) -> bool:
        checker = getattr(self.list_view, "has_active_interaction", None)
        return bool(checker()) if callable(checker) else False

    def _handle_browser_cancel_shortcut(self, watched: object | None = None) -> bool:
        focus = QApplication.focusWidget()
        if (
            QApplication.activeModalWidget() is not None
            or QApplication.activePopupWidget() is not None
            or not self._is_browser_cancel_command_surface(watched or focus)
        ):
            return False
        if (
            self._is_browser_editing_surface(watched)
            or (
                self._is_browser_editing_surface(focus)
                and focus is not self.browser_search_edit
            )
        ):
            return False
        in_browser_context = self._is_browser_cancel_surface(watched) or self._is_browser_cancel_surface(focus)
        if in_browser_context and self.list_view.cancel_interaction():
            return True
        if (
            self.browser_cancel_clears_filters
            and (
                self.browser_search_edit.text()
                or self.browser_filter_state != BrowserFilterState.normalized()
            )
        ):
            self.clear_browser_filters()
            return True
        if in_browser_context and self._clipboard_paths:
            self.clear_file_clipboard()
            self._show_temporary_status(tr('切り取り／コピー候補を解除しました'))
            return True
        return False

    def _handle_browser_clear_filters_shortcut(self) -> bool:
        if self.browser_cancel_clears_filters:
            return False
        focus = QApplication.focusWidget()
        if (
            QApplication.activeModalWidget() is not None
            or QApplication.activePopupWidget() is not None
            or self._is_browser_editing_surface(focus)
        ):
            return False
        self.clear_browser_filters()
        return True

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # type: ignore[override]
        workflow = getattr(self, "_browser_workflow", None)
        if workflow is not None:
            if (watched in (self, self.list_view.viewport())
                    and event.type() in {QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.WindowActivate}):
                self._schedule_thumbnail_requests(0)
            if workflow.handle_key(watched, event):
                return True
        extra_button_result = self._handle_extra_button_event(watched, event)
        if extra_button_result is not None:
            return extra_button_result
        key_event = (
            event
            if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent)
            else None
        )
        if (
            key_event is not None
            and self._is_browser_cancel_command_surface(watched)
            and self._browser_shortcut_has("browser_cancel", key_event)
            and QApplication.activeModalWidget() is None
            and QApplication.activePopupWidget() is None
            and not self._is_browser_editing_surface(watched)
            and not self._is_browser_editing_surface(QApplication.focusWidget())
        ):
            if self._handle_browser_cancel_shortcut(watched):
                return True
        if (
            watched in (self.list_view, self.list_view.viewport())
            and key_event is not None
        ):
            if self._browser_shortcut_has("browser_open_selection", key_event):
                index = self.list_view.currentIndex()
                if index.isValid():
                    self.open_item(index)
                return True
            if (
                key_event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not self._browser_shortcut_has(
                    "browser_open_selection", key_event
                )
            ):
                # QListView's built-in activation would otherwise keep the
                # default Return binding alive after it was unassigned.
                return True
        if (
            watched in (self.list_view, self.list_view.viewport())
            and isinstance(event, QContextMenuEvent)
            and event.reason() == QContextMenuEvent.Reason.Keyboard
        ):
            index = self.list_view.currentIndex()
            position = (
                self.list_view.visualRect(index).center()
                if index.isValid() else QPoint(-1, -1)
            )
            self._show_context_menu(position, keyboard=True)
            return True
        if watched is self.list_view and event.type() == QEvent.Type.PaletteChange:
            self._refresh_thumbnail_encoding_policy()
        if watched is self.list_view.viewport():
            event_type = event.type()
            if event_type == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
                self._update_rating_hover(event.position().toPoint())
            elif event_type == QEvent.Type.Leave:
                self._clear_rating_hover()
            elif (
                event_type == QEvent.Type.MouseButtonPress
                and isinstance(event, QMouseEvent)
                and event.button()
                in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton)
            ):
                hit = self._rating_hit(event.position().toPoint())
                if hit is not None:
                    item, rating = hit
                    self._rating_press = (str(item.path), rating, event.button())
                    return True
            elif (
                event_type == QEvent.Type.MouseButtonRelease
                and isinstance(event, QMouseEvent)
                and event.button()
                in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton)
                and self._rating_press is not None
            ):
                pressed_path, pressed_rating, pressed_button = self._rating_press
                self._rating_press = None
                hit = self._rating_hit(event.position().toPoint())
                if hit is not None and self._same_path(hit[0].path, pressed_path):
                    rating = None if pressed_button == Qt.MouseButton.MiddleButton else pressed_rating
                    self.set_rating_for_paths((pressed_path,), rating)
                return True
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
                self._show_breadcrumb_mode()
                self.list_view.setFocus(Qt.FocusReason.ShortcutFocusReason)
                return True
        if (
            key_event is not None
            and self._is_browser_history_key_surface(watched)
            and self._browser_shortcut_has("browser_backspace", key_event)
        ):
            self.go_back()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if (
            self._browser_shortcut_has("browser_backspace", event)
            and self._is_browser_history_key_surface(
                QApplication.focusWidget()
            )
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
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_tag_quick_filter_geometry()
        if hasattr(self, "location_directory_loader"):
            self._close_location_directory_popup(cancel_pending=True)
            self._close_owned_popup("_navigation_history_menu")
            self._close_owned_popup("_location_history_popup")
            self._close_owned_popup("_search_history_popup")
        self._schedule_thumbnail_requests()

    def _build_ui(self) -> None:
        # Keep only the top-level menu row compact. Popup QMenu geometry and
        # application fonts remain owned by the active platform style.
        install_text_icon_menu_style(self.menuBar(), compact=True)
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
        self.item_model.configure_filter_restore_cache(
            enabled=self.folder_snapshot_cache.enabled,
            max_entries=self.folder_snapshot_cache.max_entries,
        )
        self.item_model.configure_sort(
            self.browser_sort_key,
            self.browser_sort_order,
            self.browser_folders_first,
            self.browser_random_seed,
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
            filename_elide_mode=self.browser_filename_elide_mode,
            filename_font_size=self.browser_filename_font_size,
            show_filename_extension=self.browser_filename_show_extension,
            filename_gap=self.browser_filename_gap,
            filename_padding_y=self.browser_filename_padding_y,
            item_spacing_x=self.browser_item_spacing_x,
            item_spacing_y=self.browser_item_spacing_y,
            **self._fallback_background_delegate_options(),
            **self._browser_icon_delegate_options(),
        )
        self.list_view.setItemDelegate(self.item_delegate)
        self.list_view.setViewMode(QListView.ViewMode.IconMode)
        self.list_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.list_view.setMovement(QListView.Movement.Static)
        self.list_view.setVerticalScrollMode(
            QListView.ScrollMode.ScrollPerPixel
        )
        self.list_view.set_wheel_scroll_policy(
            self.browser_wheel_scroll_mode,
            self.browser_wheel_scroll_custom_rows,
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

        self.navigation_toolbar = QToolBar(tr('ナビゲーション'), self)
        self.navigation_toolbar.setObjectName("browser_navigation_toolbar")
        self.navigation_toolbar.setMovable(False)
        self.navigation_toolbar.setIconSize(
            QSize(BROWSER_NAVIGATION_ICON_SIZE, BROWSER_NAVIGATION_ICON_SIZE)
        )
        self.navigation_toolbar.setFixedHeight(
            BROWSER_NAVIGATION_TOOLBAR_MIN_HEIGHT
        )
        self.navigation_toolbar.layout().setContentsMargins(
            *BROWSER_NAVIGATION_TOOLBAR_MARGINS
        )

        self.back_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowBack),
            tr('戻る'),
            self,
        )
        self.back_action.setToolTip(tr('前に表示していたフォルダへ戻る (Alt+Left)'))
        self.back_action.setShortcuts(
            [QKeySequence("Alt+Left")]
        )
        self.back_action.triggered.connect(self.go_back)
        self.navigation_toolbar.addAction(self.back_action)
        self.back_button = self.navigation_toolbar.widgetForAction(self.back_action)
        if self.back_button is not None:
            self.back_button.setContextMenuPolicy(
                Qt.ContextMenuPolicy.CustomContextMenu
            )
            self.back_button.customContextMenuRequested.connect(
                lambda position: self._show_navigation_history_menu(
                    "back",
                    self.back_button.mapToGlobal(position),
                )
            )

        self.forward_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowForward),
            tr('進む'),
            self,
        )
        self.forward_action.setToolTip(tr('戻る前のフォルダへ進む (Alt+Right)'))
        self.forward_action.setShortcut(QKeySequence("Alt+Right"))
        self.forward_action.triggered.connect(self.go_forward)
        self.navigation_toolbar.addAction(self.forward_action)
        self.forward_button = self.navigation_toolbar.widgetForAction(
            self.forward_action
        )
        if self.forward_button is not None:
            self.forward_button.setContextMenuPolicy(
                Qt.ContextMenuPolicy.CustomContextMenu
            )
            self.forward_button.customContextMenuRequested.connect(
                lambda position: self._show_navigation_history_menu(
                    "forward",
                    self.forward_button.mapToGlobal(position),
                )
            )

        self.up_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowUp),
            tr('上へ'),
            self,
        )
        self.up_action.setToolTip(tr('ひとつ上の階層へ移動 (Alt+Up)'))
        self.up_action.setShortcut(QKeySequence("Alt+Up"))
        self.up_action.triggered.connect(self.go_up)
        self.navigation_toolbar.addAction(self.up_action)
        self.up_button = self.navigation_toolbar.widgetForAction(self.up_action)

        self.refresh_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
            tr('更新'),
            self,
        )
        self.refresh_action.setToolTip(tr('現在のフォルダを更新 (F5)'))
        self.refresh_action.setShortcut(QKeySequence("F5"))
        self.refresh_action.triggered.connect(self.refresh_current_folder)
        self.navigation_toolbar.addAction(self.refresh_action)
        self.refresh_button = self.navigation_toolbar.widgetForAction(
            self.refresh_action
        )
        self.navigation_toolbar.addSeparator()

        for button in (
            self.back_button,
            self.forward_button,
            self.up_button,
            self.refresh_button,
        ):
            if button is not None:
                button.setStyleSheet(
                    "QToolButton { padding: 0px; margin: 0px; }"
                )
                button.setFixedSize(
                    BROWSER_NAVIGATION_BUTTON_SIZE,
                    BROWSER_NAVIGATION_BUTTON_SIZE,
                )

        self.browser_toolbar_content = QWidget(self.navigation_toolbar)
        self.browser_toolbar_content.setObjectName("browser_toolbar_content")
        self.browser_toolbar_content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        browser_toolbar_layout = QHBoxLayout(self.browser_toolbar_content)
        browser_toolbar_layout.setContentsMargins(0, 0, 0, 0)
        browser_toolbar_layout.setSpacing(BROWSER_CHROME_CONTROL_SPACING)

        self.browser_location_control = _BrowserDropDownShell(
            self.browser_toolbar_content
        )
        self.browser_location_control.setObjectName("browser_location_control")
        self.browser_location_control.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.browser_location_control.setMinimumWidth(60)
        self.browser_location_control.setFixedHeight(
            BROWSER_CHROME_CONTROL_HEIGHT
        )

        self.location_stack = QStackedWidget(self.browser_location_control)
        self.location_stack.setObjectName("browser_location_stack")
        self.location_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.location_stack.setMinimumWidth(30)
        self.location_breadcrumb = BrowserLocationBreadcrumb(
            self.location_stack
        )
        self.location_breadcrumb.locationActivated.connect(
            self._navigate_from_breadcrumb
        )
        self.location_breadcrumb.childrenRequested.connect(
            self._show_breadcrumb_children
        )
        self.location_breadcrumb.editRequested.connect(self.focus_address_bar)

        self.address_bar = BrowserAddressBar(self.location_stack)
        self.address_bar.setObjectName("browser_address_bar")
        self.address_bar.setFrame(False)
        self.address_bar.setClearButtonEnabled(True)
        self.address_bar.setPlaceholderText(tr('フォルダ、画像、ZIP/CBZのパス'))
        self.address_bar.setToolTip(
            tr('パスを入力してEnterで移動。相対パスは現在のフォルダ基準です。')
        )
        self.address_bar.returnPressed.connect(self._navigate_from_address_bar)
        self.address_bar.editingFinished.connect(self._finish_address_edit)
        self.address_bar.installEventFilter(self)
        self.location_stack.addWidget(self.location_breadcrumb)
        self.location_stack.addWidget(self.address_bar)
        self.location_stack.setCurrentWidget(self.location_breadcrumb)
        self.browser_location_control.set_content_widget(self.location_stack)
        self.browser_location_control.setToolTip(tr('最近表示したフォルダ'))
        self.browser_location_control.dropDownRequested.connect(
            self._show_location_history_popup
        )
        browser_toolbar_layout.addWidget(self.browser_location_control, 1)

        self.browser_sort_row = QWidget(self.browser_toolbar_content)
        self.browser_sort_row.setObjectName("browser_sort_search_row")
        self.browser_sort_row.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )
        browser_sort_layout = QHBoxLayout(self.browser_sort_row)
        browser_sort_layout.setContentsMargins(1, 0, 0, 0)
        browser_sort_layout.setSpacing(BROWSER_CHROME_CONTROL_SPACING)

        self.browser_sort_key_combo = QComboBox(self.browser_sort_row)
        self.browser_sort_key_combo.setObjectName("browser_sort_key_combo")
        self.browser_sort_key_combo.setToolTip(tr('一覧の並び替え（ランダムを選び直すと並び直します）'))
        self.browser_sort_key_combo.setFixedHeight(
            BROWSER_CHROME_CONTROL_HEIGHT
        )
        self.browser_sort_key_combo.setItemDelegate(
            _BrowserSortItemDelegate(self.browser_sort_key_combo)
        )
        for label, key, order in BROWSER_SORT_CHOICES:
            self.browser_sort_key_combo.addItem(tr(label), f"{key}:{order}")
        # Show the full catalog, not Qt's default ten-row subset. Qt still
        # bounds the popup to the screen and enables scrolling when necessary.
        self.browser_sort_key_combo.setMaxVisibleItems(
            self.browser_sort_key_combo.count()
        )
        browser_sort_layout.addWidget(self.browser_sort_key_combo)

        self._sync_browser_controls()
        self.browser_sort_key_combo.activated.connect(
            self._apply_browser_controls
        )
        self.browser_search_edit = _BrowserSearchEdit(self.browser_sort_row)
        self.browser_search_edit.setObjectName("browser_search_edit")
        self.browser_search_edit.setPlaceholderText(tr('ファイル名を検索'))
        self.browser_search_edit.setClearButtonEnabled(True)
        self.browser_search_edit.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )
        self.browser_search_edit.setFrame(False)
        self.browser_search_edit.setMinimumWidth(100)
        self.browser_search_edit.textChanged.connect(
            self._on_browser_search_text_changed
        )
        self.browser_search_edit.returnPressed.connect(
            self._commit_browser_search_history
        )
        self.browser_search_container = _BrowserDropDownShell(
            self.browser_sort_row
        )
        self.browser_search_container.setObjectName("browser_search_container")
        self.browser_search_container.setFixedHeight(
            BROWSER_CHROME_CONTROL_HEIGHT
        )
        self.browser_search_container.set_preferred_width(190)
        self.browser_search_container.setMinimumWidth(150)
        self.browser_search_container.setMaximumWidth(230)
        self.browser_search_container.set_content_widget(
            self.browser_search_edit
        )
        self.browser_search_container.dropDownRequested.connect(
            self._show_search_history_popup
        )
        browser_sort_layout.addWidget(self.browser_search_container)
        self._sync_search_history_control()
        browser_toolbar_layout.addWidget(self.browser_sort_row)
        self.navigation_toolbar.addWidget(self.browser_toolbar_content)

        self.rating_filter_container = QWidget(self.menuBar())
        self.rating_filter_container.setObjectName(
            "browser_rating_filter_container"
        )
        self.rating_filter_container.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Preferred,
        )
        rating_layout = QHBoxLayout(self.rating_filter_container)
        rating_layout.setContentsMargins(4, 0, 6, 0)
        rating_layout.setSpacing(BROWSER_CHROME_CONTROL_SPACING)
        rating_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._rating_filter_layout = rating_layout
        self.tag_quick_filter_strip = BrowserTagQuickFilterStrip(
            self.rating_filter_container
        )
        self.tag_quick_filter_strip.activated.connect(
            self._on_tag_quick_filter_activated
        )
        self.rating_filter_widget = BrowserRatingFilterWidget(
            self.rating_filter_container
        )
        self.rating_filter_widget.filterChanged.connect(
            self._on_rating_quick_filter_changed
        )
        rating_layout.addWidget(
            self.rating_filter_widget,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        self._sync_browser_filter_controls()

        self.clear_browser_search_shortcut = QShortcut(
            QKeySequence("Escape"),
            self.browser_search_edit,
        )
        self.clear_browser_search_shortcut.setContext(
            Qt.ShortcutContext.WidgetShortcut
        )
        self.clear_browser_search_shortcut.activated.connect(
            self._handle_browser_cancel_shortcut
        )
        self.clear_filters_shortcut = QShortcut(QKeySequence(), self)
        self.clear_filters_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.clear_filters_shortcut.activated.connect(
            self._handle_browser_clear_filters_shortcut
        )
        self.rating_filter_widget.installEventFilter(self)
        self.tag_button = TagFilterMenuButton(self)
        self.tag_button.setText(tr('タグ'))
        self.tag_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.tag_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tag_menu = QMenu(self.tag_button)
        self.tag_button.setMenu(self.tag_menu)
        self._reserve_tag_button_width()
        self._rebuild_tag_menu()
        rating_layout.insertWidget(
            0,
            self.tag_button,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        self._sync_tag_quick_filter_registry()
        self.item_delegate.tag_registry = self.config.get('browser_tag_registry', [])
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

        file_menu = self.menuBar().addMenu(tr('ファイル'))
        self.copy_action = file_menu.addAction(tr('コピー'))
        self.copy_action.triggered.connect(self.copy_selected_items)
        self.cut_action = file_menu.addAction(tr('切り取り'))
        self.cut_action.triggered.connect(self.cut_selected_items)
        self.paste_action = file_menu.addAction(tr('貼り付け'))
        self.paste_action.triggered.connect(self.paste_items)
        file_menu.addSeparator()
        self.copy_to_action = file_menu.addAction(tr('指定先へコピー...'))
        self.copy_to_action.triggered.connect(
            lambda _checked=False: self.copy_selected_to()
        )
        self.move_to_action = file_menu.addAction(tr('指定先へ移動...'))
        self.move_to_action.triggered.connect(
            lambda _checked=False: self.move_selected_to()
        )
        self.copy_destination_menu = file_menu.addMenu(tr('コピー先'))
        self.move_destination_menu = file_menu.addMenu(tr('移動先'))
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
        self.rename_action = file_menu.addAction(tr('名前の変更'))
        self.rename_action.triggered.connect(self.rename_selected_item)
        self.recycle_action = file_menu.addAction(tr('削除'))
        self.recycle_action.triggered.connect(self.move_selected_to_recycle_bin)
        file_menu.addSeparator()
        self.new_folder_action = file_menu.addAction(tr('新しいフォルダ'))
        self.new_folder_action.triggered.connect(self.create_new_folder)

        view_menu = self.menuBar().addMenu(tr('表示'))
        self.sidebar_action = QAction(tr('サイドバーを表示'), self)
        self.sidebar_action.setCheckable(True)
        self.sidebar_action.setChecked(
            bool(self.settings.get("browser_sidebar_visible", True))
        )
        self.sidebar_action.toggled.connect(self.set_sidebar_visible)
        view_menu.addAction(self.sidebar_action)
        self.favorites_visible_action = QAction(tr('お気に入りを表示'), self)
        self.favorites_visible_action.setCheckable(True)
        self.favorites_visible_action.toggled.connect(
            lambda checked: self.set_sidebar_component_visible(
                "favorites", checked
            )
        )
        view_menu.addAction(self.favorites_visible_action)
        self.folder_tree_visible_action = QAction(tr('フォルダツリーを表示'), self)
        self.folder_tree_visible_action.setCheckable(True)
        self.folder_tree_visible_action.toggled.connect(
            lambda checked: self.set_sidebar_component_visible("tree", checked)
        )
        view_menu.addAction(self.folder_tree_visible_action)
        self.history_visible_action = QAction(tr('履歴を表示'), self)
        self.history_visible_action.setCheckable(True)
        self.history_visible_action.toggled.connect(
            lambda checked: self.set_sidebar_component_visible(
                "history", checked
            )
        )
        view_menu.addAction(self.history_visible_action)
        layout_menu = view_menu.addMenu(tr('サイドバーレイアウト'))
        self.sidebar_layout_actions: dict[str, QAction] = {}
        for name, label in (
            ("favorites_top_tree_bottom", tr('お気に入り上／ツリー下')),
            ("tree_top_favorites_bottom", tr('ツリー上／お気に入り下')),
            ("tabs", tr('タブ')),
            ("favorites_only", tr('お気に入りのみ')),
            ("tree_only", tr('ツリーのみ')),
        ):
            action = layout_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, value=name: self.set_sidebar_layout(value)
            )
            self.sidebar_layout_actions[name] = action
        tree_sync_menu = view_menu.addMenu(tr('フォルダツリー同期'))
        self.folder_tree_sync_actions: dict[str, QAction] = {}
        for name, label in (
            ("off", tr('同期しない')),
            ("select_current", tr('現在フォルダを選択')),
            ("focus_current", tr('現在フォルダへフォーカス')),
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
            tr('無関係な自動展開を折りたたむ')
        )
        self.collapse_auto_tree_action.setCheckable(True)
        self.collapse_auto_tree_action.triggered.connect(
            self.set_folder_tree_collapse_unrelated
        )
        self._sync_sidebar_actions()

        bookmark_menu = self.menuBar().addMenu(tr('ブックマーク'))
        self.add_folder_bookmark_action = QAction(tr('現在のフォルダを追加'), self)
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

        history_menu = self.menuBar().addMenu(tr('履歴'))
        clear_history_action = QAction(tr('閲覧履歴をすべて消去...'), self)
        clear_history_action.triggered.connect(
            lambda _checked=False: self.clear_history()
        )
        history_menu.addAction(clear_history_action)

        self.settings_action = QAction(tr('設定'), self)
        self.settings_action.setIcon(settings_icon())
        self.settings_action.setIconVisibleInMenu(True)
        self.settings_action.triggered.connect(self.open_settings_dialog)
        self.menuBar().addAction(self.settings_action)
        self.menuBar().setCornerWidget(
            self.rating_filter_container,
            Qt.Corner.TopRightCorner,
        )
        QWidget.setTabOrder(
            self.browser_sort_key_combo,
            self.browser_search_edit,
        )
        browser_status_bar = self.statusBar()
        browser_status_bar.setStyleSheet(
            "QStatusBar::item { border: none; }"
        )
        browser_status_bar.layout().setSpacing(BROWSER_STATUS_BAR_SPACING)
        self.browser_status_summary_widget = QWidget(self)
        self.browser_status_summary_widget.setObjectName(
            "browser_status_summary_widget"
        )
        status_summary_layout = QHBoxLayout(
            self.browser_status_summary_widget
        )
        status_summary_layout.setContentsMargins(0, 0, 0, 0)
        status_summary_layout.setSpacing(BROWSER_STATUS_LEFT_SPACING)
        self.browser_item_count_label = QLabel(
            tr('0 個の項目'),
            self.browser_status_summary_widget,
        )
        self.browser_item_count_label.setObjectName(
            "browser_item_count_label"
        )
        self.browser_item_count_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.browser_item_count_label.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        status_summary_layout.addWidget(self.browser_item_count_label)

        self.browser_selected_path_edit = QLineEdit(
            self.browser_status_summary_widget
        )
        self.browser_selected_path_edit.setObjectName(
            "browser_selected_path_edit"
        )
        self.browser_selected_path_edit.setAccessibleName(
            tr('選択項目のパス')
        )
        self.browser_selected_path_edit.setReadOnly(True)
        self.browser_selected_path_edit.setFrame(False)
        self.browser_selected_path_edit.setTextMargins(0, 0, 0, 0)
        self.browser_selected_path_edit.setStyleSheet(
            "QLineEdit { border: none; padding: 0; background: transparent; }"
        )
        self.browser_selected_path_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.browser_selected_path_edit.setMaximumHeight(
            BROWSER_STATUS_BAR_MIN_IDLE_HEIGHT - 2
        )
        status_summary_layout.addWidget(self.browser_selected_path_edit, 1)
        browser_status_bar.addWidget(
            self.browser_status_summary_widget,
            1,
        )
        self.selected_detail_widget = QWidget(self)
        self.selected_detail_widget.setObjectName(
            "browser_selected_detail_widget"
        )
        selected_detail_layout = QHBoxLayout(self.selected_detail_widget)
        selected_detail_layout.setContentsMargins(0, 0, 0, 0)
        selected_detail_layout.setSpacing(BROWSER_STATUS_DETAIL_SPACING)
        self.file_size_label = QLabel(self.selected_detail_widget)
        self.file_size_label.setObjectName("browser_file_size_label")
        self.file_size_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.file_size_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        size_slot_width = (
            self.file_size_label.fontMetrics().horizontalAdvance(
                "999.9 GB"
            )
            + 6
        )
        self.file_size_label.setFixedWidth(size_slot_width)
        selected_detail_layout.addWidget(self.file_size_label)

        self.file_detail_label = QLabel(self.selected_detail_widget)
        self.file_detail_label.setObjectName("browser_file_detail_label")
        self.file_detail_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        detail_metrics = self.file_detail_label.fontMetrics()
        detail_slot_width = max(
            detail_metrics.horizontalAdvance(tr('999999 ページ')),
            detail_metrics.horizontalAdvance("99999 × 99999"),
        ) + 6
        self.file_detail_label.setFixedWidth(detail_slot_width)
        self.file_detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        selected_detail_layout.addWidget(self.file_detail_label)
        browser_status_bar.addPermanentWidget(self.selected_detail_widget)
        self.cancel_operation_button = QPushButton(tr('キャンセル'), self)
        self.cancel_operation_button.setObjectName(
            "cancel_file_operation_button"
        )
        self.cancel_operation_button.clicked.connect(self.cancel_file_operation)
        self.cancel_operation_button.setVisible(False)
        browser_status_bar.addPermanentWidget(self.cancel_operation_button)
        self.file_operation_panel = FileOperationPanel(self)
        if self.file_operation_coordinator.queue is not None:
            self.file_operation_panel.bind(self.file_operation_coordinator.queue)
            browser_status_bar.addPermanentWidget(self.file_operation_panel, 1)
        # QStatusBar rebuilds its private layout as permanent widgets are
        # inserted, so compact its idle chrome only after the final insertion.
        browser_status_bar.layout().setSpacing(BROWSER_STATUS_BAR_SPACING)
        self._compact_status_bar_if_idle()
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
        workflow = getattr(self, "_browser_workflow", None)
        if workflow is not None:
            # Recenter cache retention before the visible requests below can
            # touch the LRU. This also runs while rapid scrolling suppresses
            # speculative generation, without doing any image or disk I/O.
            workflow.recenter_cache_retention()
            workflow.schedule_background()
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
            prefetch_screens=0,
            opposite_safety_fraction=0.0,
            scroll_direction=self._thumbnail_scroll_direction,
            fast_scrolling=self._fast_scrolling,
        )
        request_token = self.thumbnail_render_spec.cache_token
        keep_paths = {
            str(item.path)
            for row in plan.requested_rows
            if (item := self.item_model.item_at(row)) is not None
        }
        cancel_outside_plan = getattr(
            self.thumbnail_provider,
            "cancel_requests_except",
            None,
        )
        if callable(cancel_outside_plan):
            cancel_outside_plan(
                keep_paths,
                size=self.thumbnail_render_spec,
                generation=self._generation,
            )
        elif self._fast_scrolling:
            self.thumbnail_provider.cancel_prefetch_except(
                keep_paths,
                size=self.thumbnail_render_spec,
                generation=self._generation,
            )
        for rows, priority in (
            (plan.visible_rows, ThumbnailPriority.VISIBLE),
            (plan.selected_rows, ThumbnailPriority.SELECTED),
            (plan.directional_rows, ThumbnailPriority.READ_AHEAD),
            (plan.safety_rows, ThumbnailPriority.PREFETCH),
        ):
            for row in rows:
                item = self.item_model.item_at(row)
                if (
                    item is None
                    or not item.can_generate_preview
                    or self.item_model._has_compatible_thumbnail(
                        item,
                        request_token,
                    )
                ):
                    continue
                self.thumbnail_provider.request(
                    item,
                    self.thumbnail_render_spec,
                    generation=self._generation,
                    priority=priority,
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
            encoder_quality=self.thumbnail_webp_quality,
            preserve_alpha=self.thumbnail_preserve_alpha,
            # The delegate fills real thumbnail frames with option.palette.base().
            # Fallback-only colors and the Viewer background are unrelated.
            matte_color=(self.list_view.palette() if hasattr(self, "list_view") else self.palette()).color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Base
            ).name(),
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

    def _refresh_thumbnail_encoding_policy(self) -> None:
        if self._shutdown_prepared:
            return
        new_spec = self._build_thumbnail_render_spec()
        self.thumbnail_provider.set_disk_cache_encoding_policy(new_spec.encoding_policy)
        if new_spec.cache_token == self.thumbnail_render_spec.cache_token:
            return
        self.thumbnail_render_spec = new_spec
        self._generation = self.thumbnail_provider.begin_generation()
        # Keep painted thumbnails/viewport while normal requested work updates.
        self._schedule_thumbnail_requests(0)

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
            # Match QListView's strict right-edge wrapping, including when
            # the chosen item spacing makes the viewport an exact multiple.
            viewport_width=max(1, viewport.width() - 1),
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
        if self._restoring_list_view_state:
            self._last_scroll_value = int(value)
            self._last_scroll_time = now
            return
        raw_delta = int(value) - self._last_scroll_value
        if raw_delta:
            self._thumbnail_scroll_direction = 1 if raw_delta > 0 else -1
        delta = abs(raw_delta)
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
        self.item_model.set_thumbnail_image(
            path,
            qimage,
            low_resolution=False,
            request_token=self.thumbnail_render_spec.cache_token,
        )

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
            remaining_items = getattr(
                pending,
                "remaining_items",
                getattr(pending, "buffered_entries", ()),
            )
            remaining_offset = int(
                getattr(pending, "remaining_item_offset", 0)
            )
            legacy_entries = getattr(
                pending,
                (
                    "refresh_entries"
                    if getattr(pending, "refresh", False)
                    else "buffered_entries"
                ),
                (),
            )
            scanned_count = int(
                getattr(
                    pending,
                    "scanned_count",
                    len(legacy_entries),
                )
            )
            count = (
                (
                    self.item_model.rowCount()
                    + max(
                        0,
                        len(remaining_items) - remaining_offset,
                    )
                    if pending.committed
                    else scanned_count
                )
            )
            self.statusBar().showMessage(
                tr('{p0} — 読み込み中… {p1}項目', p0=pending.path, p1=count)
            )
            return
        count = self.item_model.rowCount()
        selected_indexes = self.list_view.selectionModel().selectedIndexes()
        selected_text = ""
        if len(selected_indexes) == 1:
            selected = self.item_model.item_at(selected_indexes[0])
            if selected is not None:
                selected_text = str(selected.path)
        elif len(selected_indexes) > 1:
            selected_text = tr('{p0} 個を選択', p0=len(selected_indexes))

        self.browser_item_count_label.setText(tr('{p0} 個の項目', p0=count))
        if self.browser_selected_path_edit.text() != selected_text:
            self.browser_selected_path_edit.setText(selected_text)
            self.browser_selected_path_edit.setCursorPosition(0)
            self.browser_selected_path_edit.deselect()
        self.statusBar().clearMessage()

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
            self._show_breadcrumb_mode()
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
                self._show_breadcrumb_mode()
            return
        if self.navigate_to(target):
            self._show_breadcrumb_mode()

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
        current_item = (
            self.item_model.item_at(self.list_view.currentIndex())
            if selected_paths
            else None
        )
        if (
            current_item is not None
            and str(current_item.path) not in selected_paths
        ):
            current_item = None
        anchor_index = self._visible_anchor_index()
        anchor_item = self.item_model.item_at(anchor_index)
        anchor_rect = self.list_view.visualRect(anchor_index)
        return _ListViewState(
            selected_paths=tuple(selected_paths),
            current_path=str(current_item.path) if current_item is not None else None,
            anchor_path=str(anchor_item.path) if anchor_item is not None else None,
            anchor_row=anchor_index.row() if anchor_index.isValid() else -1,
            anchor_x=anchor_rect.x() if anchor_rect.isValid() else 0,
            anchor_y=anchor_rect.y() if anchor_rect.isValid() else 0,
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
        return QModelIndex()

    def _schedule_list_view_state_restore(
        self,
        state: _ListViewState,
        *,
        request_thumbnails: bool = True,
        update_navigation_history: bool = False,
    ) -> None:
        self._list_view_restore_token += 1
        token = self._list_view_restore_token
        self._restore_list_view_state(state)

        def restore_after_layout() -> None:
            if token != self._list_view_restore_token:
                return
            self._restore_list_view_state(state)
            if update_navigation_history:
                self._update_current_navigation_state()
            if request_thumbnails:
                self._schedule_thumbnail_requests()

        QTimer.singleShot(0, restore_after_layout)

    def _restore_list_view_state(self, state: _ListViewState) -> None:
        selection_model = self.list_view.selectionModel()
        if selection_model is None:
            return
        self._restoring_list_view_state = True
        try:
            selection_model.clearSelection()
            restored_selection: list[QModelIndex] = []
            for path in state.selected_paths:
                row = self.item_model.row_for_path(path)
                if row >= 0:
                    index = self.item_model.index(row, 0)
                    restored_selection.append(index)
                    selection_model.select(
                        index,
                        QItemSelectionModel.SelectionFlag.Select,
                    )

            current = QModelIndex()
            if state.current_path:
                row = self.item_model.row_for_path(state.current_path)
                if row >= 0:
                    current = self.item_model.index(row, 0)
            if not current.isValid() and restored_selection:
                current = restored_selection[0]
            selection_model.setCurrentIndex(
                current,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )

            if restored_selection and current.isValid():
                current_rect = self.list_view.visualRect(current)
                visible_rect = self.list_view.viewport().rect().adjusted(
                    1,
                    1,
                    -1,
                    -1,
                )
                horizontal = self.list_view.horizontalScrollBar()
                vertical = self.list_view.verticalScrollBar()
                if current_rect.left() < visible_rect.left():
                    horizontal.setValue(
                        horizontal.value()
                        + current_rect.left()
                        - visible_rect.left()
                    )
                elif current_rect.right() > visible_rect.right():
                    horizontal.setValue(
                        horizontal.value()
                        + current_rect.right()
                        - visible_rect.right()
                    )
                if current_rect.top() < visible_rect.top():
                    vertical.setValue(
                        vertical.value()
                        + current_rect.top()
                        - visible_rect.top()
                    )
                elif current_rect.bottom() > visible_rect.bottom():
                    vertical.setValue(
                        vertical.value()
                        + current_rect.bottom()
                        - visible_rect.bottom()
                    )
            else:
                anchor = QModelIndex()
                if state.anchor_path:
                    row = self.item_model.row_for_path(state.anchor_path)
                    if row >= 0:
                        anchor = self.item_model.index(row, 0)
                if not anchor.isValid() and self.item_model.rowCount() > 0:
                    row = max(
                        0,
                        min(state.anchor_row, self.item_model.rowCount() - 1),
                    )
                    anchor = self.item_model.index(row, 0)
                anchor_rect = self.list_view.visualRect(anchor)
                if anchor.isValid() and anchor_rect.isValid():
                    horizontal = self.list_view.horizontalScrollBar()
                    vertical = self.list_view.verticalScrollBar()
                    horizontal.setValue(
                        horizontal.value() + anchor_rect.x() - state.anchor_x
                    )
                    vertical.setValue(
                        vertical.value() + anchor_rect.y() - state.anchor_y
                    )
                else:
                    self.list_view.verticalScrollBar().setValue(
                        state.vertical_scroll
                    )
                    self.list_view.horizontalScrollBar().setValue(
                        state.horizontal_scroll
                    )
        finally:
            self._restoring_list_view_state = False
        self._update_status()

    def _update_current_navigation_state(self) -> None:
        if self.current_path is None:
            return
        current_history = self.navigation_history.current()
        if current_history is None or not self._same_path(
            self.current_path,
            Path(current_history.path),
        ):
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
        if hasattr(self, "location_directory_loader"):
            self._close_location_directory_popup(cancel_pending=True)
            self._close_owned_popup("_navigation_history_menu")
            self._close_owned_popup("_location_history_popup")
            self._close_owned_popup("_search_history_popup")
        path = str(self.current_path) if self.current_path is not None else ""
        self.address_bar.setText(path)
        if hasattr(self, "location_breadcrumb"):
            self.location_breadcrumb.set_location(self.current_path)

    def _update_navigation_actions(self) -> None:
        self.back_action.setEnabled(self.navigation_history.can_go_back())
        self.forward_action.setEnabled(self.navigation_history.can_go_forward())
        if hasattr(self, "browser_location_control"):
            self.browser_location_control.set_drop_down_available(
                len(self.navigation_history) > 0
            )
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
                tr('先頭のフォルダを使用します（ほか{p0}件）', p0=request.ignored_count)
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
                tr('ドロップした項目は現在の一覧に表示されません')
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
            self._show_temporary_status(tr('この場所にはドロップできません'))
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
            self._show_temporary_status(tr('同じフォルダへの移動は行いません'))
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
            self._invoke_open_path_handler(
                path,
                offset > 0,
                None,
                use_browser_order=False,
            )

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
            self._show_temporary_status(tr('{p0}件をお気に入りへ追加しました', p0=added))

    def _show_context_menu(self, position: QPoint, *, keyboard: bool = False) -> None:
        if self.list_view.consume_folder_gesture_context_menu_suppression():
            return
        index = self.list_view.currentIndex() if keyboard else self.list_view.indexAt(position)
        item = self.item_model.item_at(index)
        if item is not None and not self.list_view.selectionModel().isSelected(index):
            self.list_view.selectionModel().select(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )
            # Selection is already explicit. QListView.setCurrentIndex can
            # toggle it again when Ctrl is held while opening this menu.
            self.list_view.selectionModel().setCurrentIndex(
                index, QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        selected_paths = self.selected_file_operation_paths()
        selection_count = len(selected_paths)
        rating_paths = tuple(
            path
            for path in selected_paths
            if (
                (row := self.item_model.row_for_path(path)) >= 0
                and self.item_model.item_at(row) is not None
            )
        )
        busy = (
            self.file_operation_coordinator.busy
            and self.file_operation_coordinator.queue is None
        )
        menu = QMenu(self)
        filename_edit = None
        selected_text_copy = selected_text_search = None
        if selection_count == 1 and item is not None:
            filename_edit = _BrowserContextFilenameEdit(Path(selected_paths[0]).name, menu)
            filename_action = QWidgetAction(menu)
            filename_action.setDefaultWidget(filename_edit)
            menu.addAction(filename_action)
            selected_text_copy = menu.addAction(tr('選択文字をコピー'))
            selected_text_search = menu.addAction(tr('選択文字で検索'))
            def update_text_actions() -> None:
                enabled = bool(filename_edit.selectedText())
                selected_text_copy.setEnabled(enabled)
                selected_text_search.setEnabled(enabled)
            filename_edit.selectionChanged.connect(update_text_actions)
            update_text_actions()
            menu.addSeparator()
        open_action = menu.addAction(tr('開く'))
        open_with_action = menu.addAction(tr('関連付けで開く...'))
        location_action = menu.addAction(tr('エクスプローラーで開く'))
        current_location_action = (
            menu.addAction(tr('現在の階層をエクスプローラーで開く'))
            if item is not None and item.kind is BrowserItemKind.FOLDER else None
        )
        target_exists = bool(
            item is not None
            and self._absolute_browser_path(item.path).exists()
        )
        open_action.setEnabled(selection_count == 1 and item is not None)
        open_with_action.setEnabled(
            selection_count == 1
            and item is not None
            and item.kind is not BrowserItemKind.FOLDER
            and target_exists
        )
        location_action.setEnabled(item is not None and target_exists)
        if current_location_action is not None:
            current_location_action.setEnabled(target_exists)
        menu.addSeparator()
        zip_action = menu.addAction(tr('zipに圧縮'))
        zip_action.setEnabled(
            selection_count > 0 and self.current_path is not None and not busy
        )
        menu.addSeparator()
        cut_action = menu.addAction(tr('切り取り'))
        copy_action = menu.addAction(tr('コピー'))
        copy_name_action = (
            menu.addAction(tr('名前をコピー')) if selection_count > 0 else None
        )
        paste_action = menu.addAction(tr('貼り付け'))
        cut_action.setEnabled(selection_count > 0 and not busy)
        copy_action.setEnabled(selection_count > 0 and not busy)
        paste_action.setEnabled(
            self.current_path is not None
            and not busy
            and bool(self._clipboard_paths or self._clipboard_file_urls())
        )
        menu.addSeparator()
        rating_menu = menu.addMenu(tr('レート'))
        from .browser_tag_dialogs import TagSelectionMenu
        tags_menu = TagSelectionMenu(rating_paths, self.config.get('browser_tag_registry', []), menu)
        menu.addMenu(tags_menu)
        tags_menu.setEnabled(bool(rating_paths) and not busy and self._rating_batch is None)
        rating_actions: dict[QAction, int | None] = {}
        for label, value in (
            (tr('なし'), None),
            ("★", 1),
            ("★★", 2),
            ("★★★", 3),
            ("★★★★", 4),
            ("★★★★★", 5),
        ):
            action = rating_menu.addAction(label)
            action.setEnabled(bool(rating_paths) and not busy)
            rating_actions[action] = value
        menu.addSeparator()
        recycle_action = menu.addAction(tr('削除'))
        recycle_action.setEnabled(selection_count > 0 and not busy)
        menu.addSeparator()
        properties_action = menu.addAction(tr('プロパティ'))
        properties_action.setEnabled(selection_count == 1 and not busy)
        anchor = position
        if keyboard and not self.list_view.viewport().rect().contains(anchor):
            anchor = self.list_view.viewport().rect().center()
        selected = menu.exec(self.list_view.viewport().mapToGlobal(anchor))
        if tags_menu.changes():
            self.set_tags_for_paths(rating_paths, tags_menu.changes())
            return
        if filename_edit is not None and selected == selected_text_copy:
            filename_edit.copy_selected_text()
        elif filename_edit is not None and selected == selected_text_search:
            text = filename_edit.selectedText()
            if text:
                # BrowserSearchPredicate is literal, case-folded substring
                # matching. Quoting/escaping would add unwanted literal text.
                self.browser_search_edit.setText(text)
                self._commit_browser_search_history()
        elif selected == open_action and item is not None:
            self.open_item(index)
        elif selected == open_with_action and item is not None:
            self._open_with_application_picker(item)
        elif selected == location_action and item is not None:
            self._open_item_in_explorer(item)
        elif current_location_action is not None and selected == current_location_action:
            self._open_current_folder_in_explorer(item)
        elif selected == cut_action:
            self.cut_selected_items()
        elif selected == copy_action:
            self.copy_selected_items()
        elif copy_name_action is not None and selected == copy_name_action:
            self.copy_selected_names()
        elif selected == paste_action:
            self.paste_items()
        elif selected == recycle_action:
            self.move_selected_to_recycle_bin()
        elif selected == zip_action:
            self.compress_selected_to_zip()
        elif selected in rating_actions:
            self.set_rating_for_paths(rating_paths, rating_actions[selected])
        elif selected == tags_menu.editor_action:
            self.manage_tags()
        elif selected == tags_menu.import_action:
            self.manage_tags(tags_menu.unknown)
        elif selected == properties_action:
            self.show_selected_properties()

    def _show_bookmark_context_menu(self, position: QPoint) -> None:
        index = self.bookmark_view.indexAt(position)
        entry = self.bookmark_model.entry_at(index)
        if entry is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction(tr('開く'))
        new_action = menu.addAction(tr('新しいViewerWindowで開く'))
        if entry.item_type == "folder":
            new_action.setEnabled(False)
        menu.addSeparator()
        remove_action = menu.addAction(tr('ブックマークから削除'))
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
        open_action = menu.addAction(tr('移動'))
        location_action = menu.addAction(tr('エクスプローラーで場所を開く'))
        menu.addSeparator()
        rename_action = menu.addAction(tr('表示名を変更'))
        move_up_action = menu.addAction(tr('上へ移動'))
        move_down_action = menu.addAction(tr('下へ移動'))
        remove_action = menu.addAction(tr('お気に入りから削除'))
        menu.addSeparator()
        copy_here_action = menu.addAction(tr('選択項目をここへコピー'))
        move_here_action = menu.addAction(tr('選択項目をここへ移動'))
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
        full_tree_action = menu.addAction(tr('ツリーのルートを戻す'))
        menu.addSeparator()
        copy_here_action = menu.addAction(tr('選択項目をここへコピー'))
        move_here_action = menu.addAction(tr('選択項目をここへ移動'))
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
        open_action = menu.addAction(tr('開く'))
        new_action = menu.addAction(tr('新しいViewerWindowで開く'))
        location_action = menu.addAction(tr('親フォルダを表示'))
        menu.addSeparator()
        remove_action = menu.addAction(tr('この履歴を削除'))
        clear_action = menu.addAction(tr('閲覧履歴をすべて消去...'))
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
