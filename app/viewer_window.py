from __future__ import annotations

from .i18n import tr
from .menu_icons import install_text_icon_menu_style, settings_icon


import inspect
import logging
import math
from pathlib import Path
from time import perf_counter_ns
from typing import Callable

from PySide6.QtCore import QByteArray, QEvent, QPoint, QSize, QThreadPool, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QColorDialog,
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStatusBar,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .archive_backend_registry import ArchiveBackendRegistry
from .adjacent_book_search import (
    AdjacentBookBrowserSnapshot,
    path_key as adjacent_path_key,
)
from .app_icon import install_window_icon
from .book_session import AsyncBookOpenFailed, BookOpened, BookSession
from .config_manager import (
    SLIDESHOW_INTERVAL_MAX_MS,
    SLIDESHOW_INTERVAL_MIN_MS,
    ConfigManager,
)
from .drag_drop import FolderDropProbe
from .external_drop_open import ExternalDropOpenController
from .fullscreen_chrome import FullscreenChromeController
from .image_cache import CachedImage
from .pdf_loupe import PdfLoupeCache
from .image_work_coordinator import ImageWorkCoordinator
from .image_source import (
    ARCHIVE_EXTENSIONS,
    PDF_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    FolderImageSource,
    FolderListingSnapshot,
    ImageSource,
    ImageSourceError,
    SevenZipImageSource,
    ZipImageSource,
    create_image_source,
)
from .metadata_store import MetadataStore
from .page_model import DisplaySpread, PageModel
from .pdf_backend import PageRenderSpec
from .pdf_image_source import PdfImageSource
from .path_availability import (
    PathAvailability,
    PathAvailabilityResult,
    PathAvailabilityService,
    lexical_absolute,
)
from .performance_trace import performance_trace
from .raster_book_runtime import (
    RasterBookRuntime,
    RasterDisplayUnit,
    RasterFrame,
    RasterPage,
    RasterRenderSpec,
    RasterRequest,
)
from .raster_warmup_planner import RasterWarmupPlan
from . import viewer_commands as commands
from .viewer_page_list_runtime import (
    ViewerPageListModel,
    ViewerPageListRuntime,
    ViewerPageThumbnail,
    ViewerPageThumbnailSpec,
)
from .viewer_page_navigation import ViewerPageNavigationController
from .viewer_page_slider import ViewerPageSlider
from .viewer_navigation_policy import (
    NavigationAdmissionDecision,
    NavigationAdmissionPolicy,
    NavigationInputKind,
)
from .viewer_close_shortcut import (
    key_event_combined,
    normalize_viewer_close_shortcut,
    sequence_combined,
)
from .shortcut_catalog import canonical_key, normalize_shortcut_bindings
from .viewer_memory_policy import (
    ResolvedViewerMemoryPolicy,
    read_physical_memory_snapshot,
)
from .viewer_display_unit import (
    ViewerDisplayUnit,
    ViewerSlotState,
)
from .viewer_presentation_state import (
    PresentationBook,
    PresentationCommit,
    PresentationFrameToken,
    PresentationNavigation,
    PresentationPage,
    PresentationUnit,
    PresentationValues,
    ViewerPresentationState,
)
from .viewer_widget import (
    ViewerFrameCommit,
    ViewerImage,
    ViewerWidget,
    calculate_spread_layout,
)
from .viewer_render import (
    DOWNSCALE_ALGORITHM_LABELS,
    UPSCALE_ALGORITHM_LABELS,
    normalize_downscale_algorithm,
    normalize_resampling_mode,
    normalize_upscale_algorithm,
    resampling_policy_for_legacy_mode,
)
_DISPLAY_LOG = logging.getLogger("nivisviewer.viewer.display_unit")
_PDF_PREFETCH_IDLE_GRACE_MS = 120
_PREPARED_DISPLAY_IDLE_GRACE_MS = 16
_DISPLAY_DEMAND_IDLE_GRACE_MS = 16
_RASTER_PAINT_FALLBACK_MS = 250
_RASTER_VIEWPORT_DEBOUNCE_MS = 120
_ZIP_RUNTIME_BROWSER_RESUME_GRACE_MS = 500
_PAGE_LIST_MAX_AHEAD_ROWS = 96
_PAGE_LIST_MAX_REAR_ROWS = 48


class _PageModelRasterTopology:
    """Live O(1)-storage view of PageModel's indexed display boundaries."""

    def __init__(
        self,
        model: PageModel,
        unit_factory: Callable[..., RasterDisplayUnit],
    ) -> None:
        self.model = model
        self.unit_factory = unit_factory

    def __len__(self) -> int:
        return self.model.display_unit_count

    def _spread(self, ordinal: int) -> DisplaySpread:
        start = self.model.display_unit_start_at_ordinal(ordinal)
        return self.model.spread_at(start)

    def unit_at(self, ordinal: int) -> RasterDisplayUnit:
        spread = self._spread(ordinal)
        return self.unit_factory(
            tuple(slot.page_index for slot in spread.slots),
            start_index=spread.start_index,
            is_single=spread.is_single,
        )

    def identity_at(self, ordinal: int) -> tuple[tuple[int, str], ...]:
        return tuple(
            (slot.page_index, slot.image_id)
            for slot in self._spread(ordinal).slots
        )

    def page_indexes_at(self, ordinal: int) -> tuple[int, ...]:
        return tuple(slot.page_index for slot in self._spread(ordinal).slots)

    def ordinal_for_identity(
        self,
        identity: tuple[tuple[int, str], ...],
    ) -> int | None:
        if not identity:
            return None
        try:
            page_index = int(identity[0][0])
        except (TypeError, ValueError, IndexError):
            return None
        ordinal = self.model.display_unit_ordinal_for_page(page_index)
        return ordinal if ordinal is not None and self.identity_at(ordinal) == identity else None

    def ordinal_for_page(self, page_index: int) -> int | None:
        return self.model.display_unit_ordinal_for_page(page_index)


class ViewerWindow(QMainWindow):
    activated = Signal(object)
    closing = Signal(object)
    slideshow_stopped = Signal(object)
    book_changed = Signal(object, str)
    displayed_item_changed = Signal(object, str)
    side_folder_requested = Signal(object, int)
    interactive_open_started = Signal(object)
    first_frame_ready = Signal(object)
    interactive_open_cancelled = Signal(object)
    presentationCommitted = Signal(object)

    def __init__(
        self,
        *,
        config_manager: ConfigManager,
        metadata_store: MetadataStore | None = None,
        book_session: BookSession | None = None,
        open_path_handler: Callable[[str, bool | None, object], object] | None = None,
        adjacent_book_handler: Callable[[object, int], str] | None = None,
        archive_backend_registry=None,
        pdfium_service=None,
        image_work_coordinator: ImageWorkCoordinator | None = None,
        path_availability_service: PathAvailabilityService | None = None,
    ) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("NivisViewer")
        install_window_icon(self)
        self.resize(1200, 820)

        self.config = config_manager
        self.settings = self.config.data
        self.metadata_store = metadata_store
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
        self._owns_path_availability_service = path_availability_service is None
        self.path_availability_service = (
            path_availability_service or PathAvailabilityService(self)
        )
        self.path_availability_service.result_ready.connect(
            self._on_path_probe_result
        )
        self.image_work_coordinator = image_work_coordinator
        self.book_session = book_session or BookSession(
            int(self.settings.get("cache_size", 10)),
            self,
            source_factory=lambda source_path, **kwargs: create_image_source(
                source_path,
                archive_backend_registry=self.archive_backend_registry,
                pdfium_service=self.pdfium_service,
                pdf_render_base_dpi=int(
                    self.settings.get("pdf_render_base_dpi", 96)
                ),
                pdf_render_annotations=bool(
                    self.settings.get("pdf_render_annotations", True)
                ),
                **kwargs,
            ),
            image_work_coordinator=self.image_work_coordinator,
        )
        self.model = self.book_session.model
        self.image_cache = self.book_session.image_cache
        self.presentation_state = ViewerPresentationState()
        # Compatibility only for older focused scheduler tests. Production
        # requests always read the indexes owned by presentation_state.
        self._visible_page_indexes_adapter: tuple[int, ...] | None = None
        self._presentation_viewport_refresh_required = False
        self._pending_progress_seed: (
            tuple[int, PresentationValues] | None
        ) = None
        self._load_prefetch_settings()
        self.image_cache.pageLoaded.connect(self._on_cache_page_loaded)
        self.book_session.page_changed.connect(self._queue_metadata_progress)
        self.book_session.async_opened.connect(self._on_async_book_opened)
        self.book_session.async_open_failed.connect(self._on_async_book_open_failed)
        self._open_path_handler = open_path_handler
        self._close_request_handler: Callable[[object], object] | None = None
        self._drop_probe_workers: set[FolderDropProbe] = set()
        self._drop_active = False
        self._adjacent_book_handler = adjacent_book_handler
        self._browser_navigation_snapshot: AdjacentBookBrowserSnapshot | None = None
        self._browser_navigation_path = ""
        self._last_displayed_browser_item = None
        self._pending_browser_navigation: (
            tuple[str, AdjacentBookBrowserSnapshot | None] | None
        ) = None
        self._shutdown_prepared = False
        self._shutdown_cleanup_phase = 0
        self._shutdown_cleanup_complete = False
        self._reload_page_index: int | None = None
        self._pending_display_demand: (
            tuple[
                int,
                int,
                DisplaySpread,
                tuple[ViewerImage, ...],
                PresentationFrameToken,
            ]
            | None
        ) = None
        self._pending_decode_demand: (
            tuple[int, int, int, tuple[int, ...]] | None
        ) = None
        self._raster_prefetch_after_paint: (
            tuple[int, int, tuple[int, ...]] | None
        ) = None
        self._raster_prefetch_plan: (
            tuple[
                int,
                int,
                int,
                tuple[int, ...],
                tuple[tuple[int, ...], ...],
            ]
            | None
        ) = None
        self._raster_prefetch_active_unit: tuple[int, ...] = tuple()
        self._advancing_raster_prefetch = False
        self._raster_interactive_lane_held = False
        self._enforcing_combined_cache_budget = False
        self._metadata_book_path = ""
        self._metadata_book_item_type = ""
        self._pending_book_open_projection: (
            tuple[int, int, str, str, int, int, str] | None
        ) = None
        self._status_override_message: str | None = None
        self._status_override_token = 0
        self._path_probe_generation = 0
        self._pending_path_probe: tuple[int, int, str, str] | None = None
        self._awaiting_first_frame = False
        self._first_frame_image_id: str | None = None
        self._next_open_trace_id = 0
        self._active_open_trace_id = 0
        self.setAcceptDrops(True)

        self.view_mode = str(self.settings["view_mode"])
        self.reading_direction = str(self.settings["reading_direction"])
        self.fit_mode = str(self.settings["fit_mode"])
        self.gap = int(self.settings["gap"])
        self.join_spread_pages = bool(self.settings.get("join_spread_pages", False))
        self.single_first_page = bool(self.settings["single_first_page"])
        self.treat_wide_image_as_single = bool(self.settings["treat_wide_image_as_single"])
        self.split_wide_image = bool(self.settings.get("split_wide_image", False))
        self.horizontal_alignment = str(self.settings.get("horizontal_alignment", "center"))
        self.brightness = max(0.1, min(3.0, float(self.settings.get("brightness", 1.0))))
        self.contrast = max(0.1, min(3.0, float(self.settings.get("contrast", 1.0))))
        self.gamma = max(0.1, min(5.0, float(self.settings.get("gamma", 1.0))))
        self.cache_size = int(self.settings.get("cache_size", 10))
        self.rotation_angle = int(self.settings.get("rotation_angle", 0)) % 360
        self.slideshow_interval_ms = int(self.settings.get("slideshow_interval_ms", 3000))
        self.slideshow_repeat = bool(self.settings.get("slideshow_repeat", False))
        self._slideshow_waiting_for_next = False
        self._slideshow_opening_next = False
        self._slideshow_skip_next_for_book: str | None = None
        self.reopen_last_on_start = bool(self.settings.get("reopen_last_on_start", False))
        self.recursive_folder = bool(self.settings.get("recursive_folder", False))
        self.sort_descending = bool(self.settings.get("sort_descending", False))
        self.hide_ui_in_fullscreen = bool(self.settings.get("hide_ui_in_fullscreen", False))
        self.hide_cursor_in_fullscreen = bool(self.settings.get("hide_cursor_in_fullscreen", False))
        self.fullscreen_auto_reveal_ui = bool(
            self.settings.get("fullscreen_auto_reveal_ui", True)
        )
        self.fullscreen_top_edge_trigger_px = int(
            self.settings.get(
                "fullscreen_top_edge_trigger_px",
                self.settings.get("fullscreen_edge_trigger_px", 8),
            )
        )
        self.fullscreen_bottom_edge_trigger_px = int(
            self.settings.get("fullscreen_bottom_edge_trigger_px", 28)
        )
        self.fullscreen_edge_trigger_px = self.fullscreen_top_edge_trigger_px
        self.fullscreen_ui_hide_delay_ms = int(
            self.settings.get("fullscreen_ui_hide_delay_ms", 0)
        )
        self.show_page_list = bool(self.settings.get("show_page_list", False))
        self.thumbnail_size = int(self.settings.get("thumbnail_size", 96))
        self.auto_open_adjacent_book = bool(self.settings.get("auto_open_adjacent_book", False))
        self.magnifier_zoom = float(self.settings.get("magnifier_zoom", 2.0))
        self.magnifier_size = int(self.settings.get("magnifier_size", 220))
        self.viewer_downscale_algorithm = normalize_downscale_algorithm(
            self.settings.get("viewer_downscale_algorithm", "auto")
        )
        self.viewer_upscale_algorithm = normalize_upscale_algorithm(
            self.settings.get("viewer_upscale_algorithm", "auto")
        )
        self.magnifier_downscale_algorithm = normalize_downscale_algorithm(
            self.settings.get("magnifier_downscale_algorithm", "sharp")
        )
        self.magnifier_upscale_algorithm = normalize_upscale_algorithm(
            self.settings.get("magnifier_upscale_algorithm", "lanczos")
        )
        self.background_color = str(self.settings["background_color"])
        self.mouse_gestures_enabled = bool(
            self.settings.get("mouse_gestures_enabled", True)
        )
        self.mouse_gesture_show_trail = bool(
            self.settings.get("mouse_gesture_show_trail", True)
        )
        self.mouse_gesture_min_distance = int(
            self.settings.get("mouse_gesture_min_distance", 36)
        )
        bindings = self.settings.get("mouse_gesture_bindings", {})
        self.mouse_gesture_bindings = dict(bindings) if isinstance(bindings, dict) else {}
        self.shortcut_bindings = normalize_shortcut_bindings(
            self.settings.get("shortcut_bindings")
        ).get("viewer", {})
        self.viewer_slideshow_chord_enabled = bool(
            self.settings.get("viewer_slideshow_chord_enabled", True)
        )
        self.mouse_back_button_action = commands.normalize_viewer_command(
            self.settings.get("mouse_back_button_action")
        )
        self.mouse_forward_button_action = commands.normalize_viewer_command(
            self.settings.get("mouse_forward_button_action")
        )
        self.viewer_close_shortcut = self.shortcut_bindings.get(
            "viewer_close", [
                normalize_viewer_close_shortcut(
                    self.settings.get("viewer_close_shortcut")
                )
            ]
        )[0] if self.shortcut_bindings.get("viewer_close", []) else ""
        self._viewer_close_key_sequence = QKeySequence(
            self.viewer_close_shortcut
        )
        self._viewer_close_key_combined = sequence_combined(
            self._viewer_close_key_sequence
        )
        self._viewer_close_key_combinations = {
            sequence_combined(QKeySequence(value))
            for value in self.shortcut_bindings.get("viewer_close", [])
        }
        self.viewer_canvas_left_click_action = str(
            self.settings.get(
                "viewer_canvas_left_click_action",
                commands.NEXT_SINGLE_PAGE,
            )
        )
        self.viewer_canvas_click_direction = str(
            self.settings.get("viewer_canvas_click_direction", "auto")
        )
        self.viewer_slider_wheel_single_page_enabled = bool(
            self.settings.get(
                "viewer_slider_wheel_single_page_enabled",
                False,
            )
        )
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.setInterval(
            max(SLIDESHOW_INTERVAL_MIN_MS, self.slideshow_interval_ms)
        )
        self.slideshow_timer.timeout.connect(self._advance_slideshow)
        self._pdf_render_timer = QTimer(self)
        self._pdf_render_timer.setSingleShot(True)
        self._pdf_render_timer.setInterval(180)
        self._pdf_render_timer.timeout.connect(self._rerender_pdf)
        self._pdf_prefetch_timer = QTimer(self)
        self._pdf_prefetch_timer.setSingleShot(True)
        self._pdf_prefetch_timer.setInterval(_PDF_PREFETCH_IDLE_GRACE_MS)
        self._pdf_prefetch_timer.timeout.connect(self._start_deferred_pdf_prefetch)
        self._prepared_display_timer = QTimer(self)
        self._prepared_display_timer.setSingleShot(True)
        self._prepared_display_timer.setInterval(
            _PREPARED_DISPLAY_IDLE_GRACE_MS
        )
        self._prepared_display_timer.timeout.connect(
            self._schedule_prepared_display_prefetch
        )
        self._display_demand_timer = QTimer(self)
        self._display_demand_timer.setSingleShot(True)
        self._display_demand_timer.setInterval(
            _DISPLAY_DEMAND_IDLE_GRACE_MS
        )
        self._display_demand_timer.timeout.connect(
            self._apply_pending_display_demand
        )
        self._decode_demand_timer = QTimer(self)
        self._decode_demand_timer.setSingleShot(True)
        self._decode_demand_timer.setInterval(
            _DISPLAY_DEMAND_IDLE_GRACE_MS
        )
        self._decode_demand_timer.timeout.connect(
            self._apply_pending_decode_demand
        )
        self._zip_runtime_request_timer = QTimer(self)
        self._zip_runtime_request_timer.setSingleShot(True)
        self._zip_runtime_request_timer.setTimerType(
            Qt.TimerType.PreciseTimer
        )
        # Runtime idle can be emitted synchronously from stage()->tryTake().
        # This timer is only a re-entry/release boundary: zero for idle key
        # work, or a short device-cadence interval for a proven wheel burst.
        self._zip_runtime_request_timer.setInterval(0)
        self._zip_runtime_request_timer.timeout.connect(
            self._dispatch_pending_zip_runtime_request
        )
        self._page_list_filter_timer = QTimer(self)
        self._page_list_filter_timer.setSingleShot(True)
        self._page_list_filter_timer.setInterval(80)
        self._page_list_filter_timer.timeout.connect(
            self._apply_page_list_filter
        )
        self._page_list_viewport_timer = QTimer(self)
        self._page_list_viewport_timer.setSingleShot(True)
        self._page_list_viewport_timer.setInterval(0)
        self._page_list_viewport_timer.timeout.connect(
            self._update_page_list_visible_work
        )
        self._presentation_side_effect_timer = QTimer(self)
        self._presentation_side_effect_timer.setSingleShot(True)
        self._presentation_side_effect_timer.setInterval(0)
        self._presentation_side_effect_timer.timeout.connect(
            self._flush_presentation_side_effects
        )
        self._raster_paint_fallback_timer = QTimer(self)
        self._raster_paint_fallback_timer.setSingleShot(True)
        self._raster_paint_fallback_timer.setInterval(
            _RASTER_PAINT_FALLBACK_MS
        )
        self._raster_paint_fallback_timer.timeout.connect(
            self._release_raster_prefetch_without_paint
        )
        self._zip_runtime_browser_resume_timer = QTimer(self)
        self._zip_runtime_browser_resume_timer.setSingleShot(True)
        self._zip_runtime_browser_resume_timer.setInterval(
            _ZIP_RUNTIME_BROWSER_RESUME_GRACE_MS
        )
        self._zip_runtime_browser_resume_timer.timeout.connect(
            self._release_raster_interactive_lane
        )
        self._raster_viewport_timer = QTimer(self)
        self._raster_viewport_timer.setSingleShot(True)
        self._raster_viewport_timer.setInterval(
            _RASTER_VIEWPORT_DEBOUNCE_MS
        )
        self._raster_viewport_timer.timeout.connect(
            self._refresh_raster_decode_bounds
        )
        self._raster_magnifier_cancel_timer = QTimer(self)
        self._raster_magnifier_cancel_timer.setSingleShot(True)
        self._raster_magnifier_cancel_timer.setInterval(0)
        self._raster_magnifier_cancel_timer.timeout.connect(
            self._refresh_after_raster_magnifier_cancel
        )
        # This is only a trailing background-admission gate. Current rendering
        # and all worker ownership remain in the existing raster runtime.
        self._raster_zoom_warmup_timer = QTimer(self)
        self._raster_zoom_warmup_timer.setSingleShot(True)
        self._raster_zoom_warmup_timer.setInterval(150)
        self._raster_zoom_warmup_timer.timeout.connect(
            self._release_settled_raster_warmup
        )
        self._raster_zoom_warmup_context: tuple[int, int] | None = None
        self._viewer_memory_pressure_timer = QTimer(self)
        self._viewer_memory_pressure_timer.setInterval(5000)
        self._viewer_memory_pressure_timer.timeout.connect(
            self._sample_viewer_memory_pressure
        )
        self._viewer_memory_pressure_timer.start()
        self._pdf_prefetch_source: PdfImageSource | None = None
        self._pdf_prefetch_generation = -1
        self._pdf_prefetch_center = 0
        self._pdf_prefetch_visible_indexes: tuple[int, ...] = tuple()
        self._pdf_prefetch_direction = 0
        self._pdf_magnifier_targets: dict[int, QSize] = {}
        self._pdf_loupe_cache = PdfLoupeCache(self)
        self._pdf_loupe_cache.ready.connect(self._on_pdf_loupe_ready)
        self._last_preload_source: ImageSource | None = None
        self._last_preload_generation = -1
        self._last_preload_center: int | None = None
        self._last_preload_direction = 0
        self._zip_runtime_active = False
        # Historical private name retained for compatibility with focused ZIP
        # tests.  The owner is now the shared RasterBookRuntime used by ZIP and
        # folder-backed books alike.
        self._zip_runtime: RasterBookRuntime | None = None
        self._raster_topology_cache_key: tuple[int, int] | None = None
        self._raster_topology: _PageModelRasterTopology | None = None
        self._zip_runtime_current_frame_serial = 0
        self._zip_runtime_last_painted_serial = 0
        self._pending_zip_runtime_request: RasterRequest | None = None
        self._pending_raster_input_kind: NavigationInputKind | None = None
        self._pending_raster_repeat_key: int | None = None
        self._navigation_repeat_key: int | None = None
        self._active_viewer_pan_keys: set[int] = set()
        self._navigation_wheel_timestamp_ns: int | None = None
        self._navigation_admission = NavigationAdmissionPolicy()
        self._pending_presentation_side_effect_token: (
            PresentationFrameToken | None
        ) = None
        self._page_list_runtime: ViewerPageListRuntime | None = None
        self._staged_page_list_runtime: ViewerPageListRuntime | None = None
        self._page_list_waiting_for_current_paint = False
        self.image_cache.set_adjustments(brightness=self.brightness, contrast=self.contrast, gamma=self.gamma)
        self.page_navigation = ViewerPageNavigationController(
            self.model,
            self._on_page_navigation_changed,
            self,
        )

        self._build_ui()
        self.book_session.viewer_runtime_changed.connect(
            self._bind_zip_runtime
        )
        self._bind_zip_runtime(self.book_session.viewer_runtime)
        self.book_session.page_list_runtime_changed.connect(
            self._stage_page_list_runtime
        )
        self._stage_page_list_runtime(
            self.book_session.page_list_runtime
        )
        self.viewer.framePainted.connect(
            self._on_zip_runtime_frame_painted
        )
        self._connect_shortcuts()
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        from .slideshow_keys import SlideshowKeys

        self.slideshow_keys = SlideshowKeys(
            self, start=lambda seconds: self.start_slideshow(seconds),
            toggle=lambda: self.toggle_slideshow(),
            choose=lambda: self.set_slideshow_interval_dialog(start=True),
            chord_enabled=lambda: self.viewer_slideshow_chord_enabled,
            toggle_enabled=lambda: self._shortcut_has("viewer_slideshow_toggle", "S"),
            choose_enabled=lambda: self._shortcut_has("viewer_slideshow_interval", "Shift+S"),
        )
        self._restore_window_state()
        self._apply_settings_to_widgets()
        self.config.settings_changed.connect(self.apply_settings)

        self._start_fullscreen = bool(self.settings.get("fullscreen"))

    @property
    def _current_book_key(self) -> str:
        return self.book_session.book_key

    @property
    def _active_request_id(self) -> int:
        """Compatibility view of the presentation-owned request serial."""

        adapter = getattr(self, "_request_id_adapter", None)
        if self.presentation_state.requested is None and adapter is not None:
            return int(adapter)
        return self.presentation_state.pending_request_serial

    @_active_request_id.setter
    def _active_request_id(self, value: int) -> None:
        # Limited to old scheduler-focused tests which do not create a real
        # PresentationRequest. Production code never assigns this adapter.
        self._request_id_adapter = int(value)

    @property
    def _applied_display_request_id(self) -> int:
        displayed = self.presentation_state.displayed
        return displayed.token.request_serial if displayed is not None else 0

    @property
    def _visible_page_indexes(self) -> tuple[int, ...]:
        adapter = self._visible_page_indexes_adapter
        if self.presentation_state.requested is None and adapter is not None:
            return adapter
        return self.presentation_state.requested_page_indexes

    @_visible_page_indexes.setter
    def _visible_page_indexes(self, value: tuple[int, ...]) -> None:
        # Compatibility adapter for isolated prefetch-policy tests only.
        self._visible_page_indexes_adapter = tuple(int(index) for index in value)

    @property
    def _display_unit(self) -> ViewerDisplayUnit:
        tracker = self.presentation_state.display_tracker
        return tracker if tracker is not None else ViewerDisplayUnit.empty()

    @property
    def _opened_path(self) -> str:
        if self.book_session.current_path is None:
            return ""
        return str(self.book_session.current_path)

    def _is_fullscreen_mode(self) -> bool:
        chrome = getattr(self, "fullscreen_chrome", None)
        return bool(
            self.isFullScreen()
            or (
                chrome is not None
                and chrome.owns_true_fullscreen_transition
            )
        )

    @property
    def browser_navigation_snapshot(self) -> AdjacentBookBrowserSnapshot | None:
        pending = self._pending_browser_navigation
        if pending is not None:
            return pending[1]
        return self._browser_navigation_snapshot

    @property
    def browser_navigation_path(self) -> str:
        pending = self._pending_browser_navigation
        if pending is not None:
            return pending[0]
        return self._browser_navigation_path or self._opened_path

    @property
    def displayed_browser_path(self) -> str | None:
        displayed = self.presentation_state.displayed
        source = self.book_session.source
        if (
            displayed is None or source is None
            or displayed.token.book.epoch != self.book_session.generation
            or displayed.token.book.source_identity != id(source)
        ):
            return None
        if isinstance(source, FolderImageSource):
            return next(
                (page.image_id for page in displayed.unit.pages
                 if page.index == displayed.values.page_index), None,
            )
        return str(source.source_path)

    def show_initial(self) -> None:
        if self._start_fullscreen:
            self.fullscreen_chrome.enter_true_fullscreen()
        else:
            self.show()
        self._apply_chrome_visibility()

    def _request_open_path(self, path: str | Path) -> None:
        if self._open_path_handler is not None:
            self._open_path_handler(str(path), False, self)
            return
        self.open_path(path)

    def _update_shared_setting(self, key: str, value: object) -> None:
        self.config.set(key, value)

    def _update_shared_settings(self, values: dict[str, object]) -> None:
        self.config.apply(values)

    def window_state_snapshot(self) -> dict[str, object]:
        geometry = self.fullscreen_chrome.standard_window_geometry()
        return {
            "window_geometry": bytes(geometry.toBase64()).decode("ascii"),
            "window_state": bytes(self.saveState().toBase64()).decode("ascii"),
            "fullscreen": self._is_fullscreen_mode(),
            "rotation_angle": self.rotation_angle,
        }

    def event(self, event: QEvent) -> bool:  # type: ignore[override]
        handled = super().event(event)
        if event.type() == QEvent.Type.WindowActivate:
            self._set_viewer_memory_active(True)
            self.activated.emit(self)
        elif event.type() == QEvent.Type.WindowDeactivate and hasattr(self, "viewer"):
            self._set_viewer_memory_active(False)
            self.viewer.cancel_mouse_gesture()
            self.viewer.cancel_pending_canvas_click()
            self._finish_pending_navigation_sequence()
        if (
            hasattr(self, "fullscreen_chrome")
            and event.type()
            in {QEvent.Type.Resize, QEvent.Type.ScreenChangeInternal}
        ):
            QTimer.singleShot(0, self.fullscreen_chrome.reevaluate_visibility)
        if (
            hasattr(self, "_page_list_viewport_timer")
            and hasattr(self, "page_list_model")
            and event.type()
            in {
                QEvent.Type.Resize,
                QEvent.Type.ScreenChangeInternal,
                QEvent.Type.DevicePixelRatioChange,
            }
        ):
            self._refresh_page_list_thumbnail_spec()
        return handled

    def _build_ui(self) -> None:
        self.viewer = ViewerWidget(
            self,
            image_work_coordinator=self.image_work_coordinator,
        )
        self.viewer.apply_presentation_surface(
            self.presentation_state.surface
        )
        self.slider = ViewerPageSlider(self)
        self.slider.set_reading_direction(self.reading_direction)
        self.slider.set_page_state(0, 0)

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.viewer, 1)
        layout.addWidget(self.slider, 0)
        self.setCentralWidget(central)

        self.status = QStatusBar(self)
        self.setStatusBar(self.status)

        self._updating_page_list_selection = False
        self._page_list_work_identity: tuple[int, str] | None = None
        self._page_list_anchor_row: int | None = None
        self._page_list_scroll_direction = 1
        self.page_list_filter = QLineEdit(self)
        self.page_list_filter.setPlaceholderText(tr('ページ名で絞り込み'))
        self.page_list_filter.textChanged.connect(
            lambda _text: self._page_list_filter_timer.start()
        )
        self.page_list_model = ViewerPageListModel(self)
        self.page_list = QListView(self)
        self.page_list.setModel(self.page_list_model)
        self.page_list.setIconSize(QSize(self.thumbnail_size, self.thumbnail_size))
        self.page_list.setUniformItemSizes(True)
        self.page_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerItem
        )
        self.page_list.selectionModel().currentChanged.connect(
            self._on_page_list_current_changed
        )
        self.page_list.verticalScrollBar().valueChanged.connect(
            lambda _value: self._schedule_page_list_visible_work()
        )
        self._page_list_viewport = self.page_list.viewport()
        self._page_list_viewport.installEventFilter(self)
        page_list_container = QWidget(self)
        page_list_layout = QVBoxLayout(page_list_container)
        page_list_layout.setContentsMargins(4, 4, 4, 4)
        page_list_layout.setSpacing(4)
        page_list_layout.addWidget(self.page_list_filter)
        page_list_layout.addWidget(self.page_list, 1)
        self.page_list_dock = QDockWidget(tr('ページ一覧'), self)
        self.page_list_dock.setObjectName("page_list_dock")
        self.page_list_dock.setWidget(page_list_container)
        self.page_list_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.page_list_dock)
        self.page_list_dock.visibilityChanged.connect(self._on_page_list_dock_visibility_changed)
        self.page_list_dock.setVisible(self.show_page_list)

        self._create_menus()

        self.viewer.nextRequested.connect(
            lambda: self.next_page_or_scroll(
                input_kind=NavigationInputKind.WHEEL
            )
        )
        self.viewer.previousRequested.connect(
            lambda: self.previous_page_or_scroll(
                input_kind=NavigationInputKind.WHEEL
            )
        )
        self.viewer.wheelInputObserved.connect(
            self._observe_wheel_input
        )
        self.viewer.wheelSequenceFinished.connect(
            self._finish_wheel_navigation
        )
        self.viewer.fullscreenToggleRequested.connect(
            lambda: self.dispatch_command(commands.TOGGLE_FULLSCREEN)
        )
        self.viewer.leftSideClicked.connect(self._on_left_side_clicked)
        self.viewer.rightSideClicked.connect(self._on_right_side_clicked)
        self.viewer.contextMenuRequested.connect(self._show_viewer_context_menu)
        self.viewer.gestureRecognized.connect(self._on_mouse_gesture)
        self.viewer.extraMouseButtonPressed.connect(self._on_extra_mouse_button)
        self.viewer.zoomChanged.connect(self._on_zoom_changed)
        self.viewer.viewportChanged.connect(self._on_viewport_changed)
        self.viewer.contentPainted.connect(self._on_viewer_content_painted)
        self.viewer.frameCommitted.connect(
            self._on_viewer_frame_committed
        )
        self.viewer.displayCommitted.connect(
            self._on_prepared_display_committed
        )
        self.viewer.renderCacheChanged.connect(
            self._on_viewer_render_cache_changed
        )
        self.viewer.renderWorkFinished.connect(
            self._on_viewer_render_work_finished
        )
        self.viewer.magnifierPdfResolutionRequested.connect(
            self._request_pdf_magnifier_resolution
        )
        self.viewer.magnifierSourceResolutionRequested.connect(
            self._request_raster_magnifier_resolution
        )
        self.viewer.magnifierCancelled.connect(self._on_magnifier_cancelled)
        self.slider.focusedPageRequested.connect(self._on_slider_changed)
        self.slider.wheelInputObserved.connect(self._observe_wheel_input)
        self.slider.nextSinglePageRequested.connect(
            lambda: self.next_one_page(input_kind=NavigationInputKind.WHEEL)
        )
        self.slider.previousSinglePageRequested.connect(
            lambda: self.previous_one_page(input_kind=NavigationInputKind.WHEEL)
        )
        self.slider.nextDisplayUnitRequested.connect(
            lambda: self.next_page(input_kind=NavigationInputKind.WHEEL)
        )
        self.slider.previousDisplayUnitRequested.connect(
            lambda: self.previous_page(input_kind=NavigationInputKind.WHEEL)
        )
        self.slider.wheelSequenceFinished.connect(
            self._finish_wheel_navigation
        )
        self.slider.sliderReleased.connect(self._finish_slider_navigation)
        self.fullscreen_chrome = FullscreenChromeController(
            self,
            viewer=self.viewer,
            menu_bar=self.menuBar(),
            slider=self.slider,
            status_bar=self.status,
            auto_reveal=self.fullscreen_auto_reveal_ui,
            top_edge_trigger_px=self.fullscreen_top_edge_trigger_px,
            bottom_edge_trigger_px=self.fullscreen_bottom_edge_trigger_px,
            hide_delay_ms=self.fullscreen_ui_hide_delay_ms,
        )
        self.slider.wheelInteraction.connect(
            self.fullscreen_chrome.show_bottom
        )
        self.viewer.set_canvas_input_context(
            context_provider=self._canvas_context_token,
            click_allowed=self._canvas_click_allowed,
            press_flags=self._canvas_press_flags,
        )
        self.viewer.set_auto_hide_cursor(False)
        drop_targets = (
            self.viewer,
            central,
            self.fullscreen_chrome.top_overlay,
            self.fullscreen_chrome.bottom_overlay,
            self.fullscreen_chrome.bottom_reveal_strip,
            self.fullscreen_chrome.fullscreen_menu_bar,
            self.fullscreen_chrome.fullscreen_status_bar,
            self.menuBar(),
            self.slider,
            self.status,
        )
        self._drop_targets = drop_targets
        # Keep raw navigation-key handling away from editable controls and the
        # virtual page list. Those remain discrete UI commands. The canvas is
        # the only surface that needs press/repeat/release identity.
        self._navigation_key_targets = (self.viewer, central)
        for target in drop_targets:
            target.setAcceptDrops(True)
            target.installEventFilter(self)

    def _create_menus(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu(tr('ファイル'))
        open_action = QAction(tr('開く'), self)
        self.viewer_open_action = open_action
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_dialog)
        reload_action = QAction(tr('再読み込み'), self)
        self.viewer_reload_action = reload_action
        reload_action.setShortcut("F5")
        reload_action.triggered.connect(self.reload_current_book)
        export_view_action = QAction(tr('現在の表示をPNG保存'), self)
        export_view_action.triggered.connect(self.export_current_view)
        copy_path_action = QAction(tr('現在画像のパスをコピー'), self)
        self.viewer_copy_path_action = copy_path_action
        copy_path_action.setShortcut("Ctrl+Shift+C")
        copy_path_action.triggered.connect(self.copy_current_image_path)
        copy_image_action = QAction(tr('現在画像をコピー'), self)
        self.viewer_copy_image_action = copy_image_action
        copy_image_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_image_action.triggered.connect(self.copy_current_image)
        copy_view_action = QAction(tr('現在の表示をコピー'), self)
        self.viewer_copy_view_action = copy_view_action
        copy_view_action.setShortcut("Ctrl+Alt+C")
        copy_view_action.triggered.connect(self.copy_current_view)
        page_info_action = QAction(tr('ページ情報'), self)
        self.viewer_page_info_action = page_info_action
        page_info_action.setShortcut("Ctrl+I")
        page_info_action.triggered.connect(self.show_page_info)
        open_location_action = QAction(tr('現在の場所を開く'), self)
        open_location_action.triggered.connect(self.open_current_location)
        exit_action = QAction(tr('Viewerを閉じる'), self)
        self.viewer_close_action = exit_action
        # Keep the old attribute for extensions that inspected the menu action.
        self.viewer_quit_action = exit_action
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.CLOSE_VIEWER)
        )
        file_menu.addAction(open_action)
        file_menu.addAction(reload_action)
        file_menu.addAction(export_view_action)
        file_menu.addAction(copy_path_action)
        file_menu.addAction(copy_image_action)
        file_menu.addAction(copy_view_action)
        file_menu.addAction(page_info_action)
        file_menu.addAction(open_location_action)
        file_menu.addSeparator()
        self.reopen_last_action = QAction(tr('起動時に前回の本を開く'), self, checkable=True)
        self.reopen_last_action.triggered.connect(self.set_reopen_last_on_start)
        self.recursive_folder_action = QAction(tr('サブフォルダも読み込む'), self, checkable=True)
        self.recursive_folder_action.triggered.connect(self.set_recursive_folder)
        self.sort_descending_action = QAction(tr('逆順で読む'), self, checkable=True)
        self.sort_descending_action.triggered.connect(self.set_sort_descending)
        self.auto_open_adjacent_book_action = QAction(tr('終端で隣の本へ移動'), self, checkable=True)
        self.auto_open_adjacent_book_action.triggered.connect(self.set_auto_open_adjacent_book)
        file_menu.addAction(self.reopen_last_action)
        file_menu.addAction(self.recursive_folder_action)
        file_menu.addAction(self.sort_descending_action)
        file_menu.addAction(self.auto_open_adjacent_book_action)
        file_menu.addSeparator()
        self.recent_menu = file_menu.addMenu(tr('最近開いたもの'))
        self._rebuild_recent_menu()
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        view_menu = menu_bar.addMenu(tr('表示'))

        self.single_action = QAction(tr('単ページ表示'), self, checkable=True)
        self.single_action.triggered.connect(lambda: self.set_view_mode("single"))
        self.spread_action = QAction(tr('見開き表示'), self, checkable=True)
        self.spread_action.triggered.connect(lambda: self.set_view_mode("spread"))
        view_group = QActionGroup(self)
        view_group.addAction(self.single_action)
        view_group.addAction(self.spread_action)
        view_group.setExclusive(True)
        view_menu.addAction(self.single_action)
        view_menu.addAction(self.spread_action)
        view_menu.addSeparator()

        self.ltr_action = QAction(tr('左綴じ'), self, checkable=True)
        self.ltr_action.triggered.connect(lambda: self.set_reading_direction("ltr"))
        self.rtl_action = QAction(tr('右綴じ'), self, checkable=True)
        self.rtl_action.triggered.connect(lambda: self.set_reading_direction("rtl"))
        direction_group = QActionGroup(self)
        direction_group.addAction(self.ltr_action)
        direction_group.addAction(self.rtl_action)
        direction_group.setExclusive(True)
        view_menu.addAction(self.ltr_action)
        view_menu.addAction(self.rtl_action)
        view_menu.addSeparator()

        self.single_first_action = QAction(tr('表紙を単独表示'), self, checkable=True)
        self.single_first_action.triggered.connect(self.set_single_first_page)
        self.wide_single_action = QAction(tr('横長画像を単独表示'), self, checkable=True)
        self.wide_single_action.triggered.connect(self.set_treat_wide_image_as_single)
        self.split_wide_action = QAction(tr('横長画像を左右分割'), self, checkable=True)
        self.split_wide_action.triggered.connect(self.set_split_wide_image)
        view_menu.addAction(self.single_first_action)
        view_menu.addAction(self.wide_single_action)
        view_menu.addAction(self.split_wide_action)
        view_menu.addSeparator()

        self.fit_window_action = QAction(tr('ウィンドウに合わせる'), self, checkable=True)
        self.fit_window_action.triggered.connect(lambda: self.set_fit_mode("fit_window"))
        self.fit_no_upscale_action = QAction(tr('ウィンドウに合わせる（拡大しない）'), self, checkable=True)
        self.fit_no_upscale_action.triggered.connect(lambda: self.set_fit_mode("fit_no_upscale"))
        self.fit_width_action = QAction(tr('幅に合わせる'), self, checkable=True)
        self.fit_width_action.triggered.connect(lambda: self.set_fit_mode("fit_width"))
        self.fit_height_action = QAction(tr('高さに合わせる'), self, checkable=True)
        self.fit_height_action.triggered.connect(lambda: self.set_fit_mode("fit_height"))
        self.actual_size_action = QAction(tr('原寸表示'), self, checkable=True)
        self.actual_size_action.triggered.connect(lambda: self.set_fit_mode("actual_size"))
        fit_group = QActionGroup(self)
        fit_group.addAction(self.fit_window_action)
        fit_group.addAction(self.fit_no_upscale_action)
        fit_group.addAction(self.fit_width_action)
        fit_group.addAction(self.fit_height_action)
        fit_group.addAction(self.actual_size_action)
        fit_group.setExclusive(True)
        view_menu.addAction(self.fit_window_action)
        view_menu.addAction(self.fit_no_upscale_action)
        view_menu.addAction(self.fit_width_action)
        view_menu.addAction(self.fit_height_action)
        view_menu.addAction(self.actual_size_action)
        view_menu.addSeparator()

        self.normal_resampling_menu = view_menu.addMenu(tr('リサンプリング（通常表示）'))
        normal_downscale_menu = self.normal_resampling_menu.addMenu(tr('縮小', disambiguation="resampling"))
        normal_upscale_menu = self.normal_resampling_menu.addMenu(tr('拡大', disambiguation="resampling"))
        self.viewer_downscale_actions: dict[str, QAction] = {}
        self.viewer_upscale_actions: dict[str, QAction] = {}
        normal_downscale_group = QActionGroup(self)
        normal_upscale_group = QActionGroup(self)
        normal_downscale_group.setExclusive(True)
        normal_upscale_group.setExclusive(True)
        for algorithm, label in DOWNSCALE_ALGORITHM_LABELS.items():
            action = QAction(tr(label), self, checkable=True)
            action.triggered.connect(
                lambda _checked=False, selected=algorithm: (
                    self.set_viewer_downscale_algorithm(selected)
                )
            )
            normal_downscale_group.addAction(action)
            normal_downscale_menu.addAction(action)
            self.viewer_downscale_actions[algorithm] = action
        for algorithm, label in UPSCALE_ALGORITHM_LABELS.items():
            action = QAction(tr(label), self, checkable=True)
            action.triggered.connect(
                lambda _checked=False, selected=algorithm: (
                    self.set_viewer_upscale_algorithm(selected)
                )
            )
            normal_upscale_group.addAction(action)
            normal_upscale_menu.addAction(action)
            self.viewer_upscale_actions[algorithm] = action

        self.magnifier_resampling_menu = view_menu.addMenu(
            tr('リサンプリング（拡大鏡）')
        )
        magnifier_downscale_menu = self.magnifier_resampling_menu.addMenu(tr('縮小', disambiguation="resampling"))
        magnifier_upscale_menu = self.magnifier_resampling_menu.addMenu(tr('拡大', disambiguation="resampling"))
        self.magnifier_downscale_actions: dict[str, QAction] = {}
        self.magnifier_upscale_actions: dict[str, QAction] = {}
        magnifier_downscale_group = QActionGroup(self)
        magnifier_upscale_group = QActionGroup(self)
        magnifier_downscale_group.setExclusive(True)
        magnifier_upscale_group.setExclusive(True)
        for algorithm, label in DOWNSCALE_ALGORITHM_LABELS.items():
            action = QAction(tr(label), self, checkable=True)
            action.triggered.connect(
                lambda _checked=False, selected=algorithm: (
                    self.set_magnifier_downscale_algorithm(selected)
                )
            )
            magnifier_downscale_group.addAction(action)
            magnifier_downscale_menu.addAction(action)
            self.magnifier_downscale_actions[algorithm] = action
        for algorithm, label in UPSCALE_ALGORITHM_LABELS.items():
            action = QAction(tr(label), self, checkable=True)
            action.triggered.connect(
                lambda _checked=False, selected=algorithm: (
                    self.set_magnifier_upscale_algorithm(selected)
                )
            )
            magnifier_upscale_group.addAction(action)
            magnifier_upscale_menu.addAction(action)
            self.magnifier_upscale_actions[algorithm] = action

        alignment_menu = view_menu.addMenu(tr('横位置'))
        self.align_left_action = QAction(tr('左寄せ'), self, checkable=True)
        self.align_left_action.triggered.connect(lambda: self.set_horizontal_alignment("left"))
        self.align_center_action = QAction(tr('中央'), self, checkable=True)
        self.align_center_action.triggered.connect(lambda: self.set_horizontal_alignment("center"))
        self.align_right_action = QAction(tr('右寄せ'), self, checkable=True)
        self.align_right_action.triggered.connect(lambda: self.set_horizontal_alignment("right"))
        alignment_group = QActionGroup(self)
        alignment_group.addAction(self.align_left_action)
        alignment_group.addAction(self.align_center_action)
        alignment_group.addAction(self.align_right_action)
        alignment_group.setExclusive(True)
        alignment_menu.addAction(self.align_left_action)
        alignment_menu.addAction(self.align_center_action)
        alignment_menu.addAction(self.align_right_action)
        view_menu.addSeparator()

        fullscreen_action = QAction(tr('全画面'), self)
        self.viewer_toggle_fullscreen_action = fullscreen_action
        fullscreen_action.setShortcut("F")
        fullscreen_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.TOGGLE_FULLSCREEN)
        )
        view_menu.addAction(fullscreen_action)
        self.hide_ui_fullscreen_action = QAction(tr('全画面時にUIを隠す'), self, checkable=True)
        self.hide_ui_fullscreen_action.triggered.connect(self.set_hide_ui_in_fullscreen)
        view_menu.addAction(self.hide_ui_fullscreen_action)
        self.hide_cursor_fullscreen_action = QAction(tr('全画面時にカーソルを隠す'), self, checkable=True)
        self.hide_cursor_fullscreen_action.triggered.connect(self.set_hide_cursor_in_fullscreen)
        view_menu.addAction(self.hide_cursor_fullscreen_action)
        self.page_list_action = QAction(tr('ページ一覧'), self, checkable=True)
        self.page_list_action.triggered.connect(self.set_page_list_visible)
        view_menu.addAction(self.page_list_action)
        view_menu.addSeparator()

        rotate_left_action = QAction(tr('左に回転'), self)
        self.viewer_rotate_left_action = rotate_left_action
        rotate_left_action.setShortcut("Ctrl+Left")
        rotate_left_action.triggered.connect(self.rotate_left)
        rotate_right_action = QAction(tr('右に回転'), self)
        self.viewer_rotate_right_action = rotate_right_action
        rotate_right_action.setShortcut("Ctrl+Right")
        rotate_right_action.triggered.connect(self.rotate_right)
        reset_rotation_action = QAction(tr('回転を解除'), self)
        self.viewer_reset_rotation_action = reset_rotation_action
        reset_rotation_action.setShortcut("Ctrl+0")
        reset_rotation_action.triggered.connect(self.reset_rotation)
        view_menu.addAction(rotate_left_action)
        view_menu.addAction(rotate_right_action)
        view_menu.addAction(reset_rotation_action)
        view_menu.addSeparator()
        self.magnifier_action = QAction(tr('拡大鏡の切り替え'), self)
        self.magnifier_action.setShortcut("Z")
        self.magnifier_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.magnifier_action.triggered.connect(self.toggle_magnifier)
        magnifier_settings_action = QAction(tr('拡大鏡の設定'), self)
        magnifier_settings_action.triggered.connect(self.set_magnifier_options_dialog)
        view_menu.addAction(self.magnifier_action)
        view_menu.addAction(magnifier_settings_action)
        self.magnifier_zoom_menu = view_menu.addMenu(tr('部分拡大倍率'))
        self.magnifier_zoom_actions: dict[float, QAction] = {}
        magnifier_zoom_group = QActionGroup(self)
        magnifier_zoom_group.setExclusive(True)
        for zoom in (1.5, 2.0, 3.0, 4.0):
            action = QAction(tr('{p0:g}倍', p0=zoom), self, checkable=True)
            action.triggered.connect(
                lambda _checked=False, selected=zoom: self.set_magnifier_zoom(
                    selected
                )
            )
            magnifier_zoom_group.addAction(action)
            self.magnifier_zoom_menu.addAction(action)
            self.magnifier_zoom_actions[zoom] = action

        slideshow_menu = self.slideshow_menu = menu_bar.addMenu(tr('スライドショー'))
        self.slideshow_action = QAction(tr('開始/停止'), self, checkable=True)
        self.slideshow_action.triggered.connect(self.toggle_slideshow)
        slideshow_menu.addAction(self.slideshow_action)
        self.slideshow_custom_action = QAction(tr('カスタム'), self, checkable=True)
        self.slideshow_custom_action.triggered.connect(
            lambda: self.set_slideshow_interval_dialog(),
        )
        slideshow_menu.addAction(self.slideshow_custom_action)
        interval_group = QActionGroup(self)
        interval_group.setExclusive(True)
        interval_group.addAction(self.slideshow_custom_action)
        self.slideshow_interval_actions = {}
        for seconds in (1, 3, 5, 10, 20, 30, 60):
            action = QAction(tr('{seconds}秒', seconds=seconds), self, checkable=True)
            action.triggered.connect(
                lambda _checked=False, value=seconds: self.set_slideshow_interval(value),
            )
            interval_group.addAction(action)
            slideshow_menu.addAction(action)
            self.slideshow_interval_actions[seconds] = action
        slideshow_menu.addSeparator()
        self.slideshow_repeat_action = QAction(tr('最後のページで先頭に戻って繰り返す'), self, checkable=True)
        self.slideshow_repeat_action.triggered.connect(
            lambda checked: self._update_shared_setting("slideshow_repeat", checked),
        )
        slideshow_menu.addAction(self.slideshow_repeat_action)
        slideshow_menu.addAction(self.auto_open_adjacent_book_action)

        self.bookmark_menu = menu_bar.addMenu(tr('ブックマーク'))
        self._rebuild_bookmark_menu()
        self.bookmark_menu.aboutToShow.connect(
            self._rebuild_bookmark_menu
        )

        settings_menu = menu_bar.addMenu(tr('設定'))
        install_text_icon_menu_style(menu_bar)
        settings_menu.setObjectName("viewer_settings_menu")
        settings_menu.setIcon(settings_icon())
        gap_action = QAction(tr('画像間の余白'), self)
        gap_action.triggered.connect(self.set_gap_dialog)
        background_color_action = QAction(tr('背景色'), self)
        background_color_action.triggered.connect(self.set_background_color_dialog)
        thumbnail_size_action = QAction(tr('サムネイルサイズ'), self)
        thumbnail_size_action.triggered.connect(self.set_thumbnail_size_dialog)
        brightness_action = QAction(tr('明るさ'), self)
        brightness_action.triggered.connect(self.set_brightness_dialog)
        contrast_action = QAction(tr('コントラスト'), self)
        contrast_action.triggered.connect(self.set_contrast_dialog)
        gamma_action = QAction(tr('ガンマ'), self)
        gamma_action.triggered.connect(self.set_gamma_dialog)
        reset_adjustments_action = QAction(tr('画像補正をリセット'), self)
        reset_adjustments_action.triggered.connect(self.reset_image_adjustments)
        settings_menu.addAction(gap_action)
        settings_menu.addAction(background_color_action)
        settings_menu.addAction(thumbnail_size_action)
        settings_menu.addSeparator()
        settings_menu.addAction(brightness_action)
        settings_menu.addAction(contrast_action)
        settings_menu.addAction(gamma_action)
        settings_menu.addAction(reset_adjustments_action)

        move_menu = menu_bar.addMenu(tr('移動', disambiguation="navigation"))
        self.history_back_action = QAction(tr('表示履歴を戻る'), self)
        self.history_back_action.setShortcut("Alt+Left")
        self.history_back_action.triggered.connect(self.go_back_in_page_history)
        self.history_forward_action = QAction(tr('表示履歴を進む'), self)
        self.history_forward_action.setShortcut("Alt+Right")
        self.history_forward_action.triggered.connect(self.go_forward_in_page_history)
        next_action = QAction(tr('次ページ'), self)
        self.viewer_next_page_action = next_action
        next_action.setShortcut(Qt.Key.Key_Right)
        next_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.NEXT_PAGE)
        )
        previous_action = QAction(tr('前ページ'), self)
        self.viewer_previous_page_action = previous_action
        previous_action.setShortcut(Qt.Key.Key_Left)
        previous_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.PREVIOUS_PAGE)
        )
        next_one_page_action = QAction(tr('1ページ進む'), self)
        self.viewer_next_single_page_action = next_one_page_action
        next_one_page_action.setShortcut("Shift+Right")
        next_one_page_action.triggered.connect(self.next_one_page)
        previous_one_page_action = QAction(tr('1ページ戻る'), self)
        self.viewer_previous_single_page_action = previous_one_page_action
        previous_one_page_action.setShortcut("Shift+Left")
        previous_one_page_action.triggered.connect(self.previous_one_page)
        next_book_action = QAction(tr('次の本'), self)
        self.viewer_next_book_action = next_book_action
        next_book_action.setShortcut("Ctrl+PgDown")
        next_book_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.NEXT_BOOK)
        )
        previous_book_action = QAction(tr('前の本'), self)
        self.viewer_previous_book_action = previous_book_action
        previous_book_action.setShortcut("Ctrl+PgUp")
        previous_book_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.PREVIOUS_BOOK)
        )
        go_to_page_action = QAction(tr('ページ指定'), self)
        self.viewer_page_dialog_action = go_to_page_action
        go_to_page_action.setShortcut("G")
        go_to_page_action.triggered.connect(self.go_to_page_dialog)
        first_action = QAction(tr('先頭'), self)
        self.viewer_first_page_action = first_action
        first_action.setShortcut(Qt.Key.Key_Home)
        first_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.FIRST_PAGE)
        )
        last_action = QAction(tr('最後'), self)
        self.viewer_last_page_action = last_action
        last_action.setShortcut(Qt.Key.Key_End)
        last_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.LAST_PAGE)
        )
        move_menu.addAction(self.history_back_action)
        move_menu.addAction(self.history_forward_action)
        move_menu.addSeparator()
        move_menu.addAction(next_action)
        move_menu.addAction(previous_action)
        move_menu.addAction(next_one_page_action)
        move_menu.addAction(previous_one_page_action)
        move_menu.addAction(go_to_page_action)
        move_menu.addSeparator()
        move_menu.addAction(first_action)
        move_menu.addAction(last_action)
        move_menu.addSeparator()
        move_menu.addAction(next_book_action)
        move_menu.addAction(previous_book_action)

        help_menu = menu_bar.addMenu(tr('ヘルプ'))
        shortcuts_action = QAction(tr('ショートカット一覧'), self)
        shortcuts_action.triggered.connect(self.show_shortcuts_help)
        help_menu.addAction(shortcuts_action)
        about_action = QAction(tr('NivisViewerについて／診断情報'), self)
        about_action.triggered.connect(self.show_diagnostics)
        help_menu.addAction(about_action)

    def show_diagnostics(self) -> None:
        from .diagnostics_dialog import DiagnosticsDialog

        dialog = DiagnosticsDialog(
            self.config.base_dir,
            self,
            pdfium_service=self.pdfium_service,
        )
        dialog.exec()

    def _connect_shortcuts(self) -> None:
        self._viewer_dynamic_shortcuts: list[QShortcut] = []
        self._rebuild_viewer_shortcuts()

    def _shortcut_has(self, action_id: str, sequence: str) -> bool:
        return canonical_key(sequence) in {
            canonical_key(value)
            for value in self.shortcut_bindings.get(action_id, [])
        }

    def _rebuild_viewer_shortcuts(self) -> None:
        for shortcut in getattr(self, "_viewer_dynamic_shortcuts", []):
            shortcut.setKey(QKeySequence())
            shortcut.deleteLater()
        self._viewer_dynamic_shortcuts = []
        # These actions are driven by the press/repeat/release event path so
        # that rapid navigation has one owner and never fires twice.
        for attribute in (
            "viewer_next_page_action",
            "viewer_previous_page_action",
            "viewer_next_single_page_action",
            "viewer_previous_single_page_action",
            "viewer_first_page_action",
            "viewer_last_page_action",
        ):
            action = getattr(self, attribute, None)
            if action is not None:
                action.setShortcuts([])
        action_map = {
            "viewer_history_back": "history_back_action",
            "viewer_history_forward": "history_forward_action",
            "viewer_open": "viewer_open_action",
            "viewer_reload": "viewer_reload_action",
            "viewer_copy_path": "viewer_copy_path_action",
            "viewer_copy_image": "viewer_copy_image_action",
            "viewer_copy_view": "viewer_copy_view_action",
            "viewer_page_info": "viewer_page_info_action",
            "viewer_close": "viewer_close_action",
            "viewer_toggle_fullscreen": "viewer_toggle_fullscreen_action",
            "viewer_toggle_magnifier": "magnifier_action",
            "viewer_rotate_left": "viewer_rotate_left_action",
            "viewer_rotate_right": "viewer_rotate_right_action",
            "viewer_reset_rotation": "viewer_reset_rotation_action",
            "viewer_next_book": "viewer_next_book_action",
            "viewer_previous_book": "viewer_previous_book_action",
            "viewer_page_dialog": "viewer_page_dialog_action",
        }
        for action_id, attribute in action_map.items():
            action = getattr(self, attribute, None)
            if action is not None:
                action.setShortcuts([
                    QKeySequence(value)
                    for value in self.shortcut_bindings.get(action_id, [])
                ])
        handlers: dict[str, Callable[[], object]] = {
            "viewer_toggle_spread": lambda: self.dispatch_command(commands.TOGGLE_SPREAD),
            "viewer_toggle_reading_direction": lambda: self.dispatch_command(commands.TOGGLE_READING_DIRECTION),
            "viewer_fit_window": lambda: self.dispatch_command(commands.FIT_WINDOW),
            "viewer_zoom_in": lambda: self.dispatch_command(commands.ZOOM_IN),
            "viewer_zoom_out": lambda: self.dispatch_command(commands.ZOOM_OUT),
            "viewer_toggle_magnifier": self.toggle_magnifier,
            "viewer_toggle_bookmark": self.toggle_current_bookmark,
            "viewer_cancel_temporary": self._handle_escape,
            "viewer_slideshow_toggle": self.toggle_slideshow,
            "viewer_slideshow_interval": lambda: self.set_slideshow_interval_dialog(start=True),
        }
        # Navigation keeps its press/repeat/release path in eventFilter.
        for action_id, handler in handlers.items():
            if action_id in action_map:
                continue
            values = self.shortcut_bindings.get(action_id, [])
            for value in values:
                if action_id == "viewer_slideshow_toggle" and value == "S" and self.viewer_slideshow_chord_enabled:
                    continue
                if action_id == "viewer_slideshow_interval" and value == "Shift+S" and self.viewer_slideshow_chord_enabled:
                    continue
                shortcut = QShortcut(QKeySequence(value), self)
                shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
                shortcut.activated.connect(handler)
                self._viewer_dynamic_shortcuts.append(shortcut)

    def _restore_window_state(self) -> None:
        geometry_text = self.settings.get("window_geometry", "")
        if geometry_text:
            try:
                geometry = QByteArray.fromBase64(geometry_text.encode("ascii"))
                self.restoreGeometry(geometry)
            except Exception:
                pass
        state_text = self.settings.get("window_state", "")
        if state_text:
            try:
                state = QByteArray.fromBase64(state_text.encode("ascii"))
                self.restoreState(state)
            except Exception:
                pass

    def _apply_settings_to_widgets(self) -> None:
        self.viewer.set_background_color(self.background_color)
        self.viewer.set_render_cache_byte_limit_bytes(
            self.viewer_cache_budget_bytes
        )
        self._enforce_combined_cache_budget()
        self.viewer.set_gap(self.gap)
        self.viewer.set_join_spread_pages(self.join_spread_pages)
        self.viewer.set_rotation_angle(self.rotation_angle)
        self.viewer.set_horizontal_alignment(self.horizontal_alignment)
        self.viewer.set_magnifier_options(zoom=self.magnifier_zoom, size=self.magnifier_size)
        self.viewer.set_magnifier_options(
            allow_outside_image=bool(self.settings.get("magnifier_allow_outside_image", True)),
        )
        self.viewer.set_resampling_algorithms(
            normal_downscale=self.viewer_downscale_algorithm,
            normal_upscale=self.viewer_upscale_algorithm,
            magnifier_downscale=self.magnifier_downscale_algorithm,
            magnifier_upscale=self.magnifier_upscale_algorithm,
        )
        self.viewer.set_fit_mode(self.fit_mode)
        self.viewer.set_mouse_gesture_options(
            enabled=self.mouse_gestures_enabled,
            show_trail=self.mouse_gesture_show_trail,
            min_distance=self.mouse_gesture_min_distance,
        )
        self.slider.set_single_page_wheel_enabled(
            self.viewer_slider_wheel_single_page_enabled
        )
        self.viewer.set_canvas_side_click_enabled(
            self.viewer_canvas_left_click_action != "none"
        )
        self.model.update_options(
            view_mode=self.view_mode,
            reading_direction=self.reading_direction,
            single_first_page=self.single_first_page,
            treat_wide_image_as_single=self.treat_wide_image_as_single,
        )
        self._sync_actions()
        self._update_status()

    def apply_settings(self, changed: dict[str, object]) -> None:
        refresh = False
        fullscreen_policy_changed = False
        normal_resampling_changed = False
        magnifier_resampling_changed = False
        shortcut_bindings_changed = "shortcut_bindings" in changed
        if shortcut_bindings_changed:
            self.shortcut_bindings = normalize_shortcut_bindings(
                changed["shortcut_bindings"]
            ).get("viewer", {})
        if "viewer_slideshow_chord_enabled" in changed:
            self.viewer_slideshow_chord_enabled = bool(
                changed["viewer_slideshow_chord_enabled"]
            )
            shortcut_bindings_changed = True
        if "viewer_close_shortcut" in changed:
            self.viewer_close_shortcut = normalize_viewer_close_shortcut(
                changed["viewer_close_shortcut"]
            )
            self._viewer_close_key_sequence = QKeySequence(
                self.viewer_close_shortcut
            )
            self._viewer_close_key_combined = sequence_combined(
                self._viewer_close_key_sequence
            )
        if shortcut_bindings_changed:
            close_values = self.shortcut_bindings.get("viewer_close", [])
            self.viewer_close_shortcut = close_values[0] if close_values else ""
            self._viewer_close_key_sequence = QKeySequence(self.viewer_close_shortcut)
            self._viewer_close_key_combined = sequence_combined(
                self._viewer_close_key_sequence
            )
            self._viewer_close_key_combinations = {
                sequence_combined(QKeySequence(value)) for value in close_values
            }
            self.slideshow_keys.reset()
            self._rebuild_viewer_shortcuts()
        if "viewer_downscale_algorithm" in changed:
            self.viewer_downscale_algorithm = normalize_downscale_algorithm(
                changed["viewer_downscale_algorithm"]
            )
            normal_resampling_changed = True
        if "viewer_upscale_algorithm" in changed:
            self.viewer_upscale_algorithm = normalize_upscale_algorithm(
                changed["viewer_upscale_algorithm"]
            )
            normal_resampling_changed = True
        if "magnifier_downscale_algorithm" in changed:
            self.magnifier_downscale_algorithm = normalize_downscale_algorithm(
                changed["magnifier_downscale_algorithm"]
            )
            magnifier_resampling_changed = True
        if "magnifier_upscale_algorithm" in changed:
            self.magnifier_upscale_algorithm = normalize_upscale_algorithm(
                changed["magnifier_upscale_algorithm"]
            )
            magnifier_resampling_changed = True
        if normal_resampling_changed or magnifier_resampling_changed:
            self.viewer.set_resampling_algorithms(
                normal_downscale=self.viewer_downscale_algorithm,
                normal_upscale=self.viewer_upscale_algorithm,
                magnifier_downscale=self.magnifier_downscale_algorithm,
                magnifier_upscale=self.magnifier_upscale_algorithm,
            )
            if normal_resampling_changed:
                self.viewer.invalidate_prepared_displays()
                if self._zip_runtime_active and self._zip_runtime is not None:
                    # Frame artifacts are algorithm/layout specific; decoded
                    # native-tier sources remain reusable across this switch.
                    self._zip_runtime.invalidate_layout()
            refresh = refresh or normal_resampling_changed
        if "magnifier_zoom" in changed:
            self.magnifier_zoom = float(changed["magnifier_zoom"])
            self.viewer.set_magnifier_options(zoom=self.magnifier_zoom)
        if "slideshow_interval_ms" in changed:
            self.slideshow_interval_ms = int(changed["slideshow_interval_ms"])
            self.slideshow_timer.setInterval(self.slideshow_interval_ms)
        if "slideshow_repeat" in changed:
            self.slideshow_repeat = bool(changed["slideshow_repeat"])
        if "auto_open_adjacent_book" in changed:
            self.auto_open_adjacent_book = bool(changed["auto_open_adjacent_book"])
            self._slideshow_skip_next_for_book = None
        if "magnifier_allow_outside_image" in changed:
            self.viewer.set_magnifier_options(
                allow_outside_image=bool(changed["magnifier_allow_outside_image"]),
            )
        if "hide_ui_in_fullscreen" in changed:
            self.hide_ui_in_fullscreen = bool(changed["hide_ui_in_fullscreen"])
            fullscreen_policy_changed = True
        if "hide_cursor_in_fullscreen" in changed:
            self.hide_cursor_in_fullscreen = bool(
                changed["hide_cursor_in_fullscreen"]
            )
            fullscreen_policy_changed = True
        if "gap" in changed:
            self.gap = max(0, min(100, int(changed["gap"])))
            self.viewer.set_gap(self.gap)
            refresh = True
        if "join_spread_pages" in changed:
            self.join_spread_pages = bool(changed["join_spread_pages"])
            self.viewer.set_join_spread_pages(self.join_spread_pages)
            refresh = True
        model_updates: dict[str, object] = {}
        if "single_first_page" in changed:
            self.single_first_page = bool(changed["single_first_page"])
            model_updates["single_first_page"] = self.single_first_page
        if "treat_wide_image_as_single" in changed:
            self.treat_wide_image_as_single = bool(
                changed["treat_wide_image_as_single"]
            )
            model_updates["treat_wide_image_as_single"] = (
                self.treat_wide_image_as_single
            )
        if model_updates:
            self.model.update_options(**model_updates)
            refresh = True
        if "thumbnail_size" in changed:
            self.thumbnail_size = max(80, min(500, int(changed["thumbnail_size"])))
            self.page_list.setIconSize(
                QSize(self.thumbnail_size, self.thumbnail_size)
            )
            self._refresh_page_list_thumbnail_spec()
        fullscreen_chrome_changed = False
        if "fullscreen_auto_reveal_ui" in changed:
            self.fullscreen_auto_reveal_ui = bool(
                changed["fullscreen_auto_reveal_ui"]
            )
            fullscreen_chrome_changed = True
        if "fullscreen_edge_trigger_px" in changed:
            self.fullscreen_top_edge_trigger_px = max(
                4, min(32, int(changed["fullscreen_edge_trigger_px"]))
            )
            self.fullscreen_edge_trigger_px = self.fullscreen_top_edge_trigger_px
            fullscreen_chrome_changed = True
        if "fullscreen_top_edge_trigger_px" in changed:
            self.fullscreen_top_edge_trigger_px = max(
                4, min(32, int(changed["fullscreen_top_edge_trigger_px"]))
            )
            self.fullscreen_edge_trigger_px = self.fullscreen_top_edge_trigger_px
            fullscreen_chrome_changed = True
        if "fullscreen_bottom_edge_trigger_px" in changed:
            self.fullscreen_bottom_edge_trigger_px = max(
                12, min(64, int(changed["fullscreen_bottom_edge_trigger_px"]))
            )
            fullscreen_chrome_changed = True
        if "viewer_canvas_left_click_action" in changed:
            action = str(changed["viewer_canvas_left_click_action"])
            self.viewer_canvas_left_click_action = (
                action
                if action
                in {
                    commands.NEXT_SINGLE_PAGE,
                    commands.NEXT_DISPLAY_UNIT,
                    "none",
                }
                else commands.NEXT_SINGLE_PAGE
            )
            self.viewer.set_canvas_side_click_enabled(
                self.viewer_canvas_left_click_action != "none"
            )
        if "viewer_canvas_click_direction" in changed:
            direction = str(changed["viewer_canvas_click_direction"])
            self.viewer_canvas_click_direction = (
                direction
                if direction in {"right_next", "left_next", "auto"}
                else "auto"
            )
        if "viewer_slider_wheel_single_page_enabled" in changed:
            self.viewer_slider_wheel_single_page_enabled = bool(
                changed["viewer_slider_wheel_single_page_enabled"]
            )
            self.slider.set_single_page_wheel_enabled(
                self.viewer_slider_wheel_single_page_enabled
            )
        if "fullscreen_ui_hide_delay_ms" in changed:
            self.fullscreen_ui_hide_delay_ms = max(
                0, min(3000, int(changed["fullscreen_ui_hide_delay_ms"]))
            )
            fullscreen_chrome_changed = True
        if fullscreen_chrome_changed:
            self.fullscreen_chrome.configure(
                auto_reveal=self.fullscreen_auto_reveal_ui,
                top_edge_trigger_px=self.fullscreen_top_edge_trigger_px,
                bottom_edge_trigger_px=self.fullscreen_bottom_edge_trigger_px,
                hide_delay_ms=self.fullscreen_ui_hide_delay_ms,
            )
            self.fullscreen_chrome.reevaluate_visibility()
        if fullscreen_policy_changed:
            self._apply_chrome_visibility()
        if (
            {
                "archive_backend_preference",
                "winrar_executable",
                "seven_zip_executable",
            }.intersection(changed)
            and self._owns_archive_backend_registry
        ):
            self.archive_backend_registry.reset()
        gesture_options_changed = False
        if "mouse_gestures_enabled" in changed:
            self.mouse_gestures_enabled = bool(changed["mouse_gestures_enabled"])
            gesture_options_changed = True
        if "mouse_gesture_show_trail" in changed:
            self.mouse_gesture_show_trail = bool(changed["mouse_gesture_show_trail"])
            gesture_options_changed = True
        if "mouse_gesture_min_distance" in changed:
            self.mouse_gesture_min_distance = max(
                12, min(200, int(changed["mouse_gesture_min_distance"]))
            )
            gesture_options_changed = True
        if "mouse_gesture_bindings" in changed:
            raw_bindings = changed["mouse_gesture_bindings"]
            self.mouse_gesture_bindings = (
                dict(raw_bindings) if isinstance(raw_bindings, dict) else {}
            )
        if "mouse_back_button_action" in changed:
            self.mouse_back_button_action = commands.normalize_viewer_command(
                changed["mouse_back_button_action"]
            )
        if "mouse_forward_button_action" in changed:
            self.mouse_forward_button_action = commands.normalize_viewer_command(
                changed["mouse_forward_button_action"]
            )
        if gesture_options_changed:
            self.viewer.set_mouse_gesture_options(
                enabled=self.mouse_gestures_enabled,
                show_trail=self.mouse_gesture_show_trail,
                min_distance=self.mouse_gesture_min_distance,
            )
        prefetch_settings_changed = bool(
            {
                "viewer_prefetch_preset",
                "viewer_prefetch_direction_priority_enabled",
                "viewer_prefetch_image_forward_units",
                "viewer_prefetch_image_backward_units",
                "viewer_prefetch_pdf_forward_units",
                "viewer_prefetch_pdf_backward_units",
                "viewer_memory_mode",
            }.intersection(changed)
        )
        if prefetch_settings_changed:
            self._load_prefetch_settings()
        self._sync_actions()
        if refresh and self.model.total_pages:
            self._refresh_view()
        elif prefetch_settings_changed:
            self._reapply_prefetch_settings()

    def _load_prefetch_settings(self) -> None:
        values = self.config.viewer_prefetch_settings()
        self.prefetch_preset = str(values["preset"])
        self.prefetch_direction_priority_enabled = bool(
            values["direction_priority_enabled"]
        )
        self.image_prefetch_forward_units = int(values["image_forward_units"])
        self.image_prefetch_backward_units = int(values["image_backward_units"])
        self.pdf_prefetch_forward_units = int(values["pdf_forward_units"])
        self.pdf_prefetch_backward_units = int(values["pdf_backward_units"])
        memory_mode = self.config.viewer_memory_mode()
        memory_policy = getattr(
            self,
            "viewer_memory_policy",
            None,
        )
        runtime = getattr(self, "_zip_runtime", None)
        current_cache_bytes = (
            runtime.cache_bytes
            if isinstance(runtime, RasterBookRuntime)
            else self.image_cache.cache_bytes
            + (
                self.viewer.render_cache_bytes()
                if hasattr(self, "viewer")
                else 0
            )
        )
        snapshot = read_physical_memory_snapshot()
        if not isinstance(memory_policy, ResolvedViewerMemoryPolicy):
            memory_policy = ResolvedViewerMemoryPolicy(
                memory_mode,
                snapshot=snapshot,
                current_cache_bytes=current_cache_bytes,
            )
        elif memory_policy.resolution.mode != memory_mode:
            memory_policy.reconfigure(
                memory_mode,
                snapshot=snapshot,
                current_cache_bytes=current_cache_bytes,
            )
        self.viewer_memory_policy = memory_policy
        previous_resolution = memory_policy.resolution
        self.viewer_memory_mode = memory_mode
        self.viewer_memory_resolution = previous_resolution
        self.viewer_cache_budget_bytes = previous_resolution.hard_limit_bytes
        self.viewer_cache_soft_target_bytes = memory_policy.target_bytes
        # Compatibility for status/tests which still expose the resolved MiB
        # value.  Runtime propagation below is byte-exact up to 32 GiB.
        self.viewer_cache_memory_mib = previous_resolution.mib
        self.image_cache.set_cache_byte_budget_bytes(
            self.viewer_cache_budget_bytes
        )
        self.book_session.set_viewer_runtime_memory_limits(
            hard_limit_bytes=self.viewer_cache_budget_bytes,
            soft_target_bytes=self.viewer_cache_soft_target_bytes,
        )
        if getattr(self, "_zip_runtime", None) is not None:
            self._apply_raster_memory_policy()
        if hasattr(self, "viewer"):
            self.viewer.set_render_cache_byte_limit_bytes(
                self.viewer_cache_budget_bytes
            )
            self._enforce_combined_cache_budget()

    def _apply_raster_memory_policy(self) -> None:
        runtime = getattr(self, "_zip_runtime", None)
        policy = getattr(self, "viewer_memory_policy", None)
        if not isinstance(runtime, RasterBookRuntime) or not isinstance(
            policy,
            ResolvedViewerMemoryPolicy,
        ):
            return
        self.viewer_cache_budget_bytes = policy.hard_limit_bytes
        self.viewer_cache_soft_target_bytes = policy.target_bytes
        self.viewer_memory_resolution = policy.resolution
        self.viewer_cache_memory_mib = policy.hard_limit_bytes // (1024 * 1024)
        self.book_session.set_viewer_runtime_memory_limits(
            hard_limit_bytes=policy.hard_limit_bytes,
            soft_target_bytes=policy.target_bytes,
        )

    def _set_viewer_memory_active(self, active: bool) -> None:
        policy = getattr(self, "viewer_memory_policy", None)
        if not isinstance(policy, ResolvedViewerMemoryPolicy):
            return
        policy.set_active(active)
        self._sample_viewer_memory_pressure()

    @Slot()
    def _sample_viewer_memory_pressure(self) -> None:
        if getattr(self, "_shutdown_prepared", False):
            return
        policy = getattr(self, "viewer_memory_policy", None)
        if not isinstance(policy, ResolvedViewerMemoryPolicy):
            return
        snapshot = read_physical_memory_snapshot()
        if snapshot is None:
            policy.reset_growth_observation()
            self._apply_raster_memory_policy()
            return
        runtime = getattr(self, "_zip_runtime", None)
        current_cache_bytes = (
            runtime.cache_bytes
            if isinstance(runtime, RasterBookRuntime)
            else self.image_cache.cache_bytes
            + (
                self.viewer.render_cache_bytes()
                if hasattr(self, "viewer")
                else 0
            )
        )
        if isinstance(self.book_session.source, SevenZipImageSource):
            current_cache_bytes += self.book_session.source.payload_cache_bytes
        policy.observe_memory_pressure(
            snapshot,
            current_cache_bytes=current_cache_bytes,
            auto_growth_requested=bool(
                isinstance(runtime, RasterBookRuntime)
                and self._zip_runtime_active
                and self.isActiveWindow()
                and runtime.auto_cache_growth_requested
            ),
        )
        self._apply_raster_memory_policy()

    def viewer_memory_debug_values(self) -> dict[str, int | str | bool | None]:
        """Expose resolved policy state to CLI benchmarks without logging."""

        policy = getattr(self, "viewer_memory_policy", None)
        return (
            policy.debug_values()
            if isinstance(policy, ResolvedViewerMemoryPolicy)
            else {}
        )

    def _enforce_combined_cache_budget(self) -> None:
        if (
            self._enforcing_combined_cache_budget
            or not hasattr(self, "viewer")
        ):
            return
        self._enforcing_combined_cache_budget = True
        try:
            total_bytes = max(1, int(self.viewer_cache_budget_bytes))
            if self._zip_runtime_active and self._zip_runtime is not None:
                self._apply_raster_memory_policy()
                return
            # ZipPlaFork evicts the prefiltered source and display artifact for
            # a page together under one display-driven memory policy. Rebalance
            # both resident Nivis caches against one configured total instead
            # of granting that total independently to each cache.
            for _iteration in range(3):
                render_bytes = self.viewer.render_cache_bytes()
                self.image_cache.set_cache_byte_budget_bytes(
                    max(1, total_bytes - render_bytes)
                )
                source_bytes = self.image_cache.cache_bytes
                self.viewer.set_render_cache_byte_limit_bytes(
                    max(1, total_bytes - source_bytes)
                )
                if (
                    self.image_cache.cache_bytes
                    + self.viewer.render_cache_bytes()
                    <= total_bytes
                ):
                    break
            self._limit_pdf_loupe_cache()
        finally:
            self._enforcing_combined_cache_budget = False

    def _reapply_prefetch_settings(self) -> None:
        if self._shutdown_prepared or not self.model.total_pages:
            return
        center = self.model.focused_index
        visible_indexes = self._visible_page_indexes or tuple(
            slot.page_index for slot in self.model.spread_at().slots
        )
        if isinstance(self.image_cache.source, PdfImageSource):
            self.image_cache.set_render_spec(
                self._current_pdf_render_spec()
            )
            self._prepare_deferred_pdf_prefetch(center, visible_indexes)
        elif self._zip_runtime_active:
            if self._zip_runtime is not None:
                self._apply_raster_memory_policy()
        else:
            self.image_cache.set_raster_decode_bounds(
                self._current_raster_decode_bounds()
            )
            self._preload_image_source(center, visible_indexes)

    def _sync_actions(self) -> None:
        self.single_action.setChecked(self.view_mode == "single")
        self.spread_action.setChecked(self.view_mode == "spread")
        self.ltr_action.setChecked(self.reading_direction == "ltr")
        self.rtl_action.setChecked(self.reading_direction == "rtl")
        self.single_first_action.setChecked(self.single_first_page)
        self.wide_single_action.setChecked(self.treat_wide_image_as_single)
        self.split_wide_action.setChecked(self.split_wide_image)
        self.fit_window_action.setChecked(self.fit_mode == "fit_window")
        self.fit_no_upscale_action.setChecked(self.fit_mode == "fit_no_upscale")
        self.fit_width_action.setChecked(self.fit_mode == "fit_width")
        self.fit_height_action.setChecked(self.fit_mode == "fit_height")
        self.actual_size_action.setChecked(self.fit_mode == "actual_size")
        for algorithm, action in self.viewer_downscale_actions.items():
            action.setChecked(algorithm == self.viewer_downscale_algorithm)
        for algorithm, action in self.viewer_upscale_actions.items():
            action.setChecked(algorithm == self.viewer_upscale_algorithm)
        for algorithm, action in self.magnifier_downscale_actions.items():
            action.setChecked(algorithm == self.magnifier_downscale_algorithm)
        for algorithm, action in self.magnifier_upscale_actions.items():
            action.setChecked(algorithm == self.magnifier_upscale_algorithm)
        self.align_left_action.setChecked(self.horizontal_alignment == "left")
        self.align_center_action.setChecked(self.horizontal_alignment == "center")
        self.align_right_action.setChecked(self.horizontal_alignment == "right")
        self.slideshow_action.setChecked(self.slideshow_timer.isActive() or self._slideshow_waiting_for_next)
        self.slideshow_repeat_action.setChecked(self.slideshow_repeat)
        for seconds, action in self.slideshow_interval_actions.items():
            action.setChecked(self.slideshow_interval_ms == seconds * 1000)
        self.slideshow_custom_action.setChecked(
            self.slideshow_interval_ms not in {seconds * 1000 for seconds in self.slideshow_interval_actions}
        )
        self.reopen_last_action.setChecked(self.reopen_last_on_start)
        self.recursive_folder_action.setChecked(self.recursive_folder)
        self.sort_descending_action.setChecked(self.sort_descending)
        self.auto_open_adjacent_book_action.setChecked(self.auto_open_adjacent_book)
        self.hide_ui_fullscreen_action.setChecked(self.hide_ui_in_fullscreen)
        self.hide_cursor_fullscreen_action.setChecked(self.hide_cursor_in_fullscreen)
        self.page_list_action.setChecked(self.show_page_list)
        for zoom, action in self.magnifier_zoom_actions.items():
            action.setChecked(math.isclose(zoom, self.magnifier_zoom))
        if hasattr(self, "history_back_action"):
            self.history_back_action.setEnabled(
                bool(self.presentation_state.back_history)
            )
        if hasattr(self, "history_forward_action"):
            self.history_forward_action.setEnabled(
                bool(self.presentation_state.forward_history)
            )

    def _sync_page_history_actions(self) -> None:
        """Update only the two action states changed by page navigation."""
        if hasattr(self, "history_back_action"):
            self.history_back_action.setEnabled(
                bool(self.presentation_state.back_history)
            )
        if hasattr(self, "history_forward_action"):
            self.history_forward_action.setEnabled(
                bool(self.presentation_state.forward_history)
            )

    def open_dialog(self) -> None:
        start = self.settings.get("last_open_path") or str(Path.home())
        extensions = sorted(SUPPORTED_EXTENSIONS | ARCHIVE_EXTENSIONS | PDF_EXTENSIONS)
        patterns = " ".join(f"*{extension}" for extension in extensions)
        image_filter = tr('画像/書庫 ({p0});;すべてのファイル (*.*)', p0=patterns)
        path, _ = QFileDialog.getOpenFileName(self, tr('画像、ZIP/CBZ、またはフォルダを開く'), start, image_filter)
        if path:
            self._request_open_path(path)
            return

        folder = QFileDialog.getExistingDirectory(self, tr('フォルダを開く'), start)
        if folder:
            self._request_open_path(folder)

    def open_path(
        self,
        path: str | Path,
        *,
        folder_snapshot: FolderListingSnapshot | None = None,
        browser_snapshot: AdjacentBookBrowserSnapshot | None = None,
        preserve_current_page: bool = False,
        slideshow_transition: bool = False,
    ) -> bool:
        if slideshow_transition and self._slideshow_waiting_for_next:
            self._slideshow_opening_next = True
        else:
            self._slideshow_waiting_for_next = False
            self._slideshow_opening_next = False
        self._slideshow_skip_next_for_book = None
        if not preserve_current_page:
            self._reload_page_index = None
        self.viewer.cancel_pending_canvas_click()
        self.viewer.cancel_magnifier()
        self._pdf_loupe_cache.set_source(None)
        self._save_current_reading_position(flush_metadata=False)
        self._pending_book_open_projection = None
        self._pending_progress_seed = None
        self.presentation_state.begin_replacement_open()
        self._project_presentation_surface()
        self._raster_viewport_timer.stop()
        self._presentation_viewport_refresh_required = False
        self._request_id_adapter = None
        self._visible_page_indexes_adapter = None
        # A replacement open is provisional.  Stop the old runtime's work but
        # retain its completed book artifacts until BookSession either swaps
        # in the new source (and retires the old runtime) or reports failure.
        self._deactivate_zip_runtime(
            clear_artifacts=False,
            preserve_book_state=True,
        )
        self._active_open_trace_id = (
            self._next_open_trace_id
            or performance_trace.begin("viewer.open_path.started", str(path))
        )
        self._next_open_trace_id = 0
        performance_trace.mark(
            self._active_open_trace_id,
            "viewer.open_path.started",
            str(path),
        )
        self._begin_interactive_open()
        suffix = Path(path).suffix.lower()
        self._pending_browser_navigation = (
            lexical_absolute(path),
            browser_snapshot,
        )
        self.book_session.open_book_async(
            path,
            recursive_folder=self.recursive_folder,
            sort_descending=self.sort_descending,
            trace_id=self._active_open_trace_id,
            folder_snapshot=folder_snapshot,
        )
        if suffix in ARCHIVE_EXTENSIONS | PDF_EXTENSIONS:
            self._set_status_override(
                tr('PDFを読み込んでいます…')
                if suffix in PDF_EXTENSIONS
                else tr('書庫を読み込んでいます…')
            )
        return True

    def _finish_opened_book(
        self,
        opened: BookOpened,
        *,
        modal_on_empty: bool,
    ) -> bool:
        self._clear_status_override()
        self._pending_book_open_projection = None
        if self.model.total_pages == 0:
            self._pending_browser_navigation = None
            self._cancel_interactive_open()
            if modal_on_empty:
                QMessageBox.warning(self, tr('画像なし'), tr('対応画像が見つかりませんでした。'))
            else:
                self._set_status_override(tr('表示可能な画像がありません'), 5000)
            self.book_session.close_book()
            self._metadata_book_path = ""
            self._metadata_book_item_type = ""
            self._pending_progress_seed = None
            self.presentation_state.clear_book()
            self._project_presentation_surface()
            self._activate_page_list_runtime(None)
            self._update_slider()
            self._update_status()
            return False

        pending_browser_navigation = self._pending_browser_navigation
        self._pending_browser_navigation = None
        if (
            pending_browser_navigation is not None
            and adjacent_path_key(pending_browser_navigation[0])
            == adjacent_path_key(opened.requested_path)
        ):
            (
                self._browser_navigation_path,
                self._browser_navigation_snapshot,
            ) = pending_browser_navigation
        else:
            self._browser_navigation_path = lexical_absolute(opened.requested_path)
            self._browser_navigation_snapshot = None

        self._metadata_book_path = str(opened.source_path)
        self._metadata_book_item_type = self._metadata_item_type_for_source(
            self.book_session.source,
            opened.source_path,
        )
        reload_page_index = self._reload_page_index
        self._reload_page_index = None
        configured_open_position = self._uses_configured_book_open_position(
            opened
        )
        saved_page_index = (
            self._saved_reading_position(self._metadata_book_path)
            if configured_open_position
            else None
        )
        if reload_page_index is not None:
            self.model.go_to_index(
                min(max(0, reload_page_index), self.model.total_pages - 1)
            )
        elif (
            configured_open_position
            and self._resumes_last_book_position()
            and saved_page_index is not None
        ):
            self.model.go_to_index(saved_page_index)

        self._pending_progress_seed = (
            (
                opened.generation,
                PresentationValues(
                    self.model.total_pages,
                    saved_page_index,
                    self.model.display_path_for_index(saved_page_index),
                    self.model.file_size_for_index(saved_page_index),
                ),
            )
            if (
                configured_open_position
                and not self._resumes_last_book_position()
                and saved_page_index is not None
            )
            else None
        )

        opened_path = str(opened.requested_path)
        self.settings["last_open_path"] = opened_path
        opened_start_page = (
            saved_page_index
            if (
                configured_open_position
                and not self._resumes_last_book_position()
                and saved_page_index is not None
            )
            else self.model.focused_index
        )
        raster_fast_path = isinstance(
            self.book_session.source,
            (ZipImageSource, FolderImageSource, SevenZipImageSource),
        ) and self.book_session.viewer_runtime is not None
        if raster_fast_path:
            self._pending_book_open_projection = (
                int(opened.generation),
                id(self.book_session.source),
                self._metadata_book_path,
                self._metadata_book_item_type,
                int(opened_start_page),
                int(self.model.total_pages),
                opened_path,
            )
        else:
            self._apply_book_open_projection(
                (
                    int(opened.generation),
                    id(self.book_session.source),
                    self._metadata_book_path,
                    self._metadata_book_item_type,
                    int(opened_start_page),
                    int(self.model.total_pages),
                    opened_path,
                )
            )
            self.image_cache.set_cache_size(self.cache_size)
        self._first_frame_image_id = self.model.image_id_at(
            self.model.focused_index
        )
        # A resize received while the replacement was provisional was not
        # allowed to touch the old book.  The new request below reads the live
        # viewport/DPR directly, so that deferred marker is now consumed.
        self._presentation_viewport_refresh_required = False
        self._refresh_view(
            navigation=PresentationNavigation.BOOK_SWITCH
        )
        performance_trace.mark(
            self._active_open_trace_id,
            "viewer.initial_requests.completed",
        )
        return True

    def _on_async_book_opened(self, opened: BookOpened) -> None:
        if self._shutdown_prepared:
            return
        succeeded = self._finish_opened_book(opened, modal_on_empty=False)
        if self._slideshow_opening_next:
            self._slideshow_opening_next = False
            self._slideshow_waiting_for_next = False
            if succeeded:
                self.slideshow_timer.start()
            else:
                self._slideshow_repeat_or_stop()
            self._sync_actions()

    def _on_async_book_open_failed(self, failed: AsyncBookOpenFailed) -> None:
        resume_slideshow = self._slideshow_opening_next
        self._slideshow_opening_next = False
        self._pending_browser_navigation = None
        self._reload_page_index = None
        self._cancel_interactive_open()
        self.presentation_state.fail_replacement_open(
            None if failed.cancelled else failed.message
        )
        self._project_presentation_surface()
        if self._shutdown_prepared:
            self._presentation_viewport_refresh_required = False
            return
        if failed.cancelled:
            self._clear_status_override()
        else:
            self._set_status_override(
                failed.message or tr('書庫を開けません'),
                5000,
            )
        if self.book_session.is_open:
            # Opening a replacement temporarily deactivates the old runtime.
            # A failed replacement leaves the old book installed, so restore
            # that book's production Viewer instead of leaving it in an
            # inactive legacy-mode surface.
            self._presentation_viewport_refresh_required = False
            self._refresh_view()
        else:
            self._presentation_viewport_refresh_required = False

        if resume_slideshow:
            self._slideshow_waiting_for_next = False
            self._slideshow_skip_next_for_book = self._current_book_key
            if not failed.cancelled:
                self._slideshow_repeat_or_stop()
            else:
                self.slideshow_timer.stop()
                self._sync_actions()

    def _uses_configured_book_open_position(self, opened: BookOpened) -> bool:
        return (
            opened.selected_image is None
            and self._metadata_book_item_type in {"folder", "archive", "pdf"}
        )

    def _resumes_last_book_position(self) -> bool:
        return self.settings.get("book_open_position") == "resume_last"

    def _saved_reading_position(self, book_key: str) -> int | None:
        if self.model.total_pages <= 0:
            return None
        if self.metadata_store is not None:
            progress = self.metadata_store.get_reading_progress(book_key)
            if progress is not None:
                if progress.page_index < 0:
                    return None
                return min(progress.page_index, self.model.total_pages - 1)
        positions = self.settings.get("reading_positions", {})
        if not isinstance(positions, dict):
            return None
        try:
            page_index = int(positions.get(book_key, 0))
        except (TypeError, ValueError):
            return None
        if page_index < 0:
            return None
        return min(page_index, self.model.total_pages - 1)

    def _save_current_reading_position(
        self,
        *,
        flush_metadata: bool = True,
    ) -> None:
        displayed = self.presentation_state.displayed
        progress = self.presentation_state.progress_values
        if displayed is None or progress is None:
            return
        self._store_reading_position(
            displayed.token.book.book_key,
            progress.page_index,
        )
        if self.metadata_store is not None:
            # Multiple Viewer windows share one debounced metadata queue.  A
            # later commit from another window may have replaced this book's
            # pending value, so close must re-stage its own atomically
            # committed presentation before flushing.  This keeps close order
            # deterministic without ever saving a requested-only page.
            self.metadata_store.update_reading_progress(
                displayed.token.book.book_key,
                page_index=progress.page_index,
                total_pages=progress.total_pages,
                item_type=self._metadata_book_item_type or None,
            )
            if flush_metadata:
                self.metadata_store.flush()

    def _apply_book_open_projection(
        self,
        projection: tuple[int, int, str, str, int, int, str],
    ) -> None:
        (
            epoch,
            source_identity,
            metadata_path,
            item_type,
            start_page_index,
            total_pages,
            opened_path,
        ) = projection
        source = self.book_session.source
        if (
            source is None
            or epoch != self.book_session.generation
            or source_identity != id(source)
        ):
            return
        if self.metadata_store is not None:
            self.metadata_store.record_book_opened(
                metadata_path,
                item_type=item_type,
                start_page_index=start_page_index,
                total_pages=total_pages,
            )
        self._add_recent_path(opened_path)
        self.book_changed.emit(self, opened_path)

    def _flush_book_open_projection(self) -> None:
        projection = self._pending_book_open_projection
        self._pending_book_open_projection = None
        if projection is not None:
            self._apply_book_open_projection(projection)

    def _store_reading_position(
        self,
        book_key: str,
        page_index: int,
    ) -> None:
        if not book_key:
            return
        positions = self.settings.get("reading_positions")
        if not isinstance(positions, dict):
            positions = {}
        positions[book_key] = max(0, int(page_index))
        while len(positions) > 100:
            oldest_key = next(iter(positions))
            del positions[oldest_key]
        self.settings["reading_positions"] = positions

    def _queue_metadata_progress(self) -> None:
        displayed = self.presentation_state.displayed
        progress = self.presentation_state.progress_values
        if (
            displayed is None
            or progress is None
        ):
            return
        book_key = displayed.token.book.book_key
        self._store_reading_position(book_key, progress.page_index)
        if self.metadata_store is None:
            return
        self.metadata_store.update_reading_progress(
            book_key,
            page_index=progress.page_index,
            total_pages=progress.total_pages,
            item_type=self._metadata_book_item_type or None,
        )

    @staticmethod
    def _metadata_item_type_for_source(
        source: ImageSource | None,
        path: Path,
    ) -> str:
        if isinstance(source, FolderImageSource):
            return "folder"
        if isinstance(source, PdfImageSource):
            return "pdf"
        if isinstance(source, (ZipImageSource, SevenZipImageSource)):
            return "archive"
        if path.suffix.lower() in ARCHIVE_EXTENSIONS:
            return "archive"
        if path.suffix.lower() in PDF_EXTENSIONS:
            return "pdf"
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            return "image"
        return "folder"

    def _clear_page_history(self) -> None:
        self.presentation_state.clear_history()
        self._sync_page_history_actions()

    def _go_to_index_with_history(
        self,
        page_index: int,
        *,
        raw: bool = False,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> bool:
        if self.model.total_pages <= 0:
            return False
        old = self.model.focused_index
        old_start = self.model.current_index
        if raw:
            self.model.go_to_raw_index(page_index)
        else:
            self.model.go_to_index(page_index)
        if self.model.current_index == old_start and self.model.focused_index == old:
            return False
        self._refresh_view(
            navigation=PresentationNavigation.NORMAL,
            input_kind=input_kind,
        )
        return True

    def _go_to_model_move_with_history(
        self,
        move,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> bool:
        if self.model.total_pages <= 0:
            return False
        old = self.model.focused_index
        old_start = self.model.current_index
        move()
        if self.model.current_index == old_start and self.model.focused_index == old:
            return False
        self._refresh_view(
            navigation=PresentationNavigation.NORMAL,
            input_kind=input_kind,
        )
        return True

    def _on_page_navigation_changed(
        self,
        _previous_index: int,
        input_kind: NavigationInputKind,
    ) -> None:
        self.viewer.cancel_pending_canvas_click()
        self._refresh_view(
            navigation=PresentationNavigation.NORMAL,
            input_kind=input_kind,
            repeat_key=self._navigation_repeat_key,
            input_timestamp_ns=(
                self._navigation_wheel_timestamp_ns
                if input_kind is NavigationInputKind.WHEEL
                else None
            ),
        )

    def go_back_in_page_history(self) -> None:
        target = self.presentation_state.history_target(
            PresentationNavigation.BACK
        )
        if self.model.total_pages <= 0 or target is None:
            return
        self.model.go_to_index(target.values.page_index)
        self._refresh_view(navigation=PresentationNavigation.BACK)

    def go_forward_in_page_history(self) -> None:
        target = self.presentation_state.history_target(
            PresentationNavigation.FORWARD
        )
        if self.model.total_pages <= 0 or target is None:
            return
        self.model.go_to_index(target.values.page_index)
        self._refresh_view(navigation=PresentationNavigation.FORWARD)

    def _add_recent_path(self, path: str) -> None:
        recent = self.settings.get("recent_paths", [])
        if not isinstance(recent, list):
            recent = []
        recent = [item for item in recent if item != path]
        recent.insert(0, path)
        self.settings["recent_paths"] = recent[:12]
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = self.settings.get("recent_paths", [])
        if not isinstance(recent, list) or not recent:
            empty_action = QAction(tr('履歴なし'), self)
            empty_action.setEnabled(False)
            self.recent_menu.addAction(empty_action)
            return

        for path in recent[:12]:
            action = QAction(path, self)
            action.triggered.connect(lambda checked=False, value=path: self._open_recent_path(value))
            self.recent_menu.addAction(action)
        self.recent_menu.addSeparator()
        clear_action = QAction(tr('履歴をクリア'), self)
        clear_action.triggered.connect(self._clear_recent_paths)
        self.recent_menu.addAction(clear_action)

    def _open_recent_path(self, path: str) -> None:
        if self._shutdown_prepared:
            return
        display = lexical_absolute(path)
        if (
            self.path_availability_service.cached_state(display)
            is PathAvailability.AVAILABLE
        ):
            self._request_open_path(display)
            return
        self._request_path_probe(display, "recent_book_open")

    def _clear_recent_paths(self) -> None:
        self.settings["recent_paths"] = []
        self._rebuild_recent_menu()

    def copy_current_image_path(self) -> None:
        if self.model.total_pages <= 0:
            return
        QApplication.clipboard().setText(
            self.model.display_path_for_index(self.model.focused_index)
        )

    def copy_current_image(self) -> None:
        if self.model.total_pages <= 0:
            return
        page_index = self.presentation_state.displayed_page
        if page_index is not None:
            snapshot = self.viewer.displayed_source_snapshot(page_index)
            if snapshot is not None and snapshot[0] is not None:
                QApplication.clipboard().setImage(snapshot[0])
                return
        cached = self.image_cache.get(self.model.focused_index)
        if cached is not None and cached.qimage is not None:
            QApplication.clipboard().setImage(cached.qimage)

    def copy_current_view(self) -> None:
        if self.model.total_pages <= 0:
            return
        QApplication.clipboard().setPixmap(self.viewer.grab())

    def show_page_info(self) -> None:
        if self.model.total_pages <= 0:
            return
        page_index = (
            self.presentation_state.displayed_page
            if self.presentation_state.displayed_page is not None
            else self.model.focused_index
        )
        snapshot = self.viewer.displayed_source_snapshot(page_index)
        cached = self.image_cache.get(page_index)
        resolution = ""
        if snapshot is not None and snapshot[1] is not None:
            resolution = f"{snapshot[1][0]} x {snapshot[1][1]}"
        elif snapshot is not None and snapshot[2]:
            resolution = tr('読み込みエラー: {p0}', p0=snapshot[2])
        elif cached is not None and cached.original_size is not None:
            resolution = f"{cached.original_size[0]} x {cached.original_size[1]}"
        elif cached is not None and cached.error:
            resolution = tr('読み込みエラー: {p0}', p0=cached.error)

        QMessageBox.information(
            self,
            tr('ページ情報'),
            "\n".join(
                [
                    tr('ページ: {p0} / {p1}', p0=page_index + 1, p1=self.model.total_pages),
                    tr('パス: {p0}', p0=self.model.display_path_for_index(page_index)),
                    tr('サイズ: {p0}', p0=self._format_file_size(self.model.file_size_for_index(page_index)) or '-'),
                    tr('解像度: {p0}', p0=resolution or '-'),
                    tr('表示モード: {p0}', p0=self.view_mode),
                    tr('綴じ方向: {p0}', p0=tr('右綴じ') if self.reading_direction == 'rtl' else tr('左綴じ')),
                    tr('画像補正: 明るさ {p0:.2f} / コントラスト {p1:.2f} / ガンマ {p2:.2f}', p0=self.brightness, p1=self.contrast, p2=self.gamma),
                ]
            ),
        )

    def open_current_location(self) -> None:
        if self._shutdown_prepared:
            return
        target: str | None = None
        if self._opened_path:
            target = self._location_target(self._opened_path)
        elif self._current_book_key:
            target = self._location_target(self._current_book_key)
        if target is None:
            return
        if (
            self.path_availability_service.cached_state(target)
            is PathAvailability.AVAILABLE
        ):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))
            return
        self._request_path_probe(target, "reveal_location")

    def _request_path_probe(self, path: str, purpose: str) -> None:
        self._path_probe_generation += 1
        generation = self._path_probe_generation
        self._pending_path_probe = None
        request_id = self.path_availability_service.request_probe(
            path,
            purpose,
            generation,
        )
        if not request_id:
            self._set_status_override(tr('現在確認できません'), 3000)
            return
        self._pending_path_probe = (request_id, generation, purpose, path)
        self._set_status_override(tr('場所を確認しています…'))

    def _on_path_probe_result(self, result: PathAvailabilityResult) -> None:
        pending = self._pending_path_probe
        if (
            pending is None
            or self._shutdown_prepared
            or result.request_id != pending[0]
            or result.path != pending[3]
            or pending[1] != self._path_probe_generation
        ):
            return
        _request_id, _generation, purpose, path = pending
        self._pending_path_probe = None
        if result.state is PathAvailability.AVAILABLE:
            self._clear_status_override()
            self._update_status()
            if purpose == "recent_book_open":
                self._request_open_path(path)
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            return
        message = {
            PathAvailability.MISSING: tr('見つかりません'),
            PathAvailability.UNAVAILABLE: tr('現在アクセスできません'),
            PathAvailability.ERROR: tr('確認できません'),
        }.get(result.state, tr('確認できません'))
        self._set_status_override(message, 3000)

    @staticmethod
    def _location_target(path: str) -> str:
        display = lexical_absolute(path)
        if (
            Path(display).suffix.casefold()
            in SUPPORTED_EXTENSIONS | ARCHIVE_EXTENSIONS | PDF_EXTENSIONS
        ):
            return lexical_absolute(Path(display).parent)
        return display

    def export_current_view(self) -> None:
        if self.model.total_pages <= 0:
            return
        default_name = f"NivisViewer_page_{self.model.current_index + 1}.png"
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr('現在の表示をPNG保存'),
            default_name,
            tr('PNG画像 (*.png)'),
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if not self.viewer.grab().save(path, "PNG"):
            QMessageBox.warning(self, tr('保存エラー'), tr('現在の表示を保存できませんでした。'))

    def reload_current_book(
        self,
        *,
        preserve_order_snapshot: bool = True,
    ) -> None:
        if self._opened_path:
            self._reload_page_index = self.model.focused_index
            self.open_path(
                self._opened_path,
                folder_snapshot=(
                    self.book_session.folder_listing_snapshot
                    if preserve_order_snapshot
                    else None
                ),
                browser_snapshot=(
                    self._browser_navigation_snapshot
                    if preserve_order_snapshot
                    else None
                ),
                preserve_current_page=True,
            )

    def set_reopen_last_on_start(self, checked: bool) -> None:
        self.reopen_last_on_start = checked
        self._update_shared_setting("reopen_last_on_start", checked)
        self._sync_actions()

    def set_recursive_folder(self, checked: bool) -> None:
        self.recursive_folder = checked
        self._update_shared_setting("recursive_folder", checked)
        self._sync_actions()
        # This is an explicit topology change, so the non-recursive Browser
        # snapshot must not constrain the recursive source listing.
        self.reload_current_book(preserve_order_snapshot=False)

    def set_sort_descending(self, checked: bool) -> None:
        self.sort_descending = checked
        self._update_shared_setting("sort_descending", checked)
        self._sync_actions()
        # An explicit Viewer-side ordering command intentionally overrides the
        # Browser snapshot.  Plain reload keeps the snapshot unchanged.
        self.reload_current_book(preserve_order_snapshot=False)

    def set_auto_open_adjacent_book(self, checked: bool) -> None:
        self.auto_open_adjacent_book = checked
        self._update_shared_setting("auto_open_adjacent_book", checked)
        self._sync_actions()

    def set_hide_ui_in_fullscreen(self, checked: bool) -> None:
        self.hide_ui_in_fullscreen = checked
        self._update_shared_setting("hide_ui_in_fullscreen", checked)
        self._sync_actions()
        self._apply_chrome_visibility()

    def set_hide_cursor_in_fullscreen(self, checked: bool) -> None:
        self.hide_cursor_in_fullscreen = checked
        self._update_shared_setting("hide_cursor_in_fullscreen", checked)
        self._sync_actions()
        self._apply_cursor_visibility_policy()

    def set_page_list_visible(self, checked: bool) -> None:
        self.show_page_list = checked
        self._update_shared_setting("show_page_list", checked)
        self.page_list_dock.setVisible(checked)
        self._sync_actions()

    def toggle_magnifier(self) -> None:
        self.viewer.toggle_magnifier()

    def set_magnifier_zoom(self, zoom: float) -> None:
        normalized = float(zoom)
        if normalized not in {1.5, 2.0, 3.0, 4.0}:
            normalized = 2.0
        self.magnifier_zoom = normalized
        self._update_shared_setting("magnifier_zoom", normalized)
        self.viewer.set_magnifier_options(zoom=normalized)
        self._sync_actions()

    def _on_page_list_dock_visibility_changed(self, visible: bool) -> None:
        if self._is_fullscreen_mode():
            self._refresh_page_list_model()
            return
        self.show_page_list = visible
        self._update_shared_setting("show_page_list", visible)
        self._refresh_page_list_model()
        if hasattr(self, "page_list_action"):
            self._sync_actions()

    def set_gap_dialog(self) -> None:
        gap, accepted = QInputDialog.getInt(self, tr('画像間の余白'), tr('ピクセル:'), self.gap, 0, 100, 1)
        if not accepted:
            return
        self.gap = gap
        self._update_shared_setting("gap", gap)
        self.viewer.set_gap(gap)
        self._refresh_view()

    def set_background_color_dialog(self) -> None:
        color = QColorDialog.getColor(self.viewer.background_color, self, tr('背景色'))
        if not color.isValid():
            return
        self.background_color = color.name()
        self._update_shared_setting("background_color", self.background_color)
        self.viewer.set_background_color(self.background_color)

    def set_thumbnail_size_dialog(self) -> None:
        size, accepted = QInputDialog.getInt(
            self,
            tr('サムネイルサイズ'),
            tr('ピクセル:'),
            self.thumbnail_size,
            80,
            500,
            8,
        )
        if not accepted:
            return
        self.thumbnail_size = size
        self._update_shared_setting("thumbnail_size", size)
        self.page_list.setIconSize(QSize(size, size))
        self._refresh_page_list_thumbnail_spec()

    def set_brightness_dialog(self) -> None:
        value, accepted = QInputDialog.getDouble(self, tr('明るさ'), tr('倍率:'), self.brightness, 0.1, 3.0, 2)
        if accepted:
            self._set_image_adjustments(brightness=value)

    def set_contrast_dialog(self) -> None:
        value, accepted = QInputDialog.getDouble(self, tr('コントラスト'), tr('倍率:'), self.contrast, 0.1, 3.0, 2)
        if accepted:
            self._set_image_adjustments(contrast=value)

    def set_gamma_dialog(self) -> None:
        value, accepted = QInputDialog.getDouble(self, tr('ガンマ'), tr('値:'), self.gamma, 0.1, 5.0, 2)
        if accepted:
            self._set_image_adjustments(gamma=value)

    def reset_image_adjustments(self) -> None:
        self._set_image_adjustments(brightness=1.0, contrast=1.0, gamma=1.0)

    def _set_image_adjustments(
        self,
        *,
        brightness: float | None = None,
        contrast: float | None = None,
        gamma: float | None = None,
    ) -> None:
        if brightness is not None:
            self.brightness = max(0.1, min(3.0, float(brightness)))
            self._update_shared_setting("brightness", self.brightness)
        if contrast is not None:
            self.contrast = max(0.1, min(3.0, float(contrast)))
            self._update_shared_setting("contrast", self.contrast)
        if gamma is not None:
            self.gamma = max(0.1, min(5.0, float(gamma)))
            self._update_shared_setting("gamma", self.gamma)
        self.image_cache.set_adjustments(brightness=self.brightness, contrast=self.contrast, gamma=self.gamma)
        if self.viewer._pdf_loupe_requests:
            # The normalized anchor and last lens surface survive adjustments;
            # only the isolated final artifact policy changes.
            for page, size in tuple(self._pdf_magnifier_targets.items()):
                self._request_pdf_magnifier_resolution(page, size)
        self._refresh_page_list_thumbnail_spec()
        if self.model.total_pages > 0:
            self._refresh_view()

    def set_magnifier_options_dialog(self) -> None:
        choices = (tr('1.5倍'), tr('2倍'), tr('3倍'), tr('4倍'))
        current = (1.5, 2.0, 3.0, 4.0).index(self.magnifier_zoom)
        selected, accepted = QInputDialog.getItem(
            self,
            tr('拡大鏡の倍率'),
            tr('倍率:'),
            choices,
            current,
            False,
        )
        if not accepted:
            return
        # Labels are presentation only; the matching choice owns its value.
        self.set_magnifier_zoom((1.5, 2.0, 3.0, 4.0)[choices.index(selected)])

    def open_next_book(self) -> None:
        self._open_adjacent_book(1)

    def open_previous_book(self) -> None:
        self._open_adjacent_book(-1)

    def _open_adjacent_book(
        self,
        direction: int,
        *,
        require_browser_snapshot: bool = False,
    ) -> str | None:
        if self._adjacent_book_handler is None:
            self._set_status_override(tr('移動できる書庫がありません'), 2500)
            return "unavailable"
        try:
            parameters = tuple(
                inspect.signature(
                    self._adjacent_book_handler
                ).parameters.values()
            )
            accepts_snapshot_requirement = (
                len(parameters) >= 3
                or any(
                    parameter.kind is inspect.Parameter.VAR_POSITIONAL
                    for parameter in parameters
                )
            )
        except (TypeError, ValueError):
            accepts_snapshot_requirement = False
        result = (
            self._adjacent_book_handler(
                self,
                direction,
                require_browser_snapshot,
            )
            if accepts_snapshot_requirement
            else self._adjacent_book_handler(self, direction)
        )
        if result == "boundary":
            message = tr('前の書庫はありません') if direction < 0 else tr('次の書庫はありません')
            self._set_status_override(message, 2500)
        elif result == "unavailable":
            self._set_status_override(tr('移動できる書庫がありません'), 2500)
        return result

    def show_adjacent_book_searching(self, direction: int) -> None:
        label = tr('前') if direction < 0 else tr('次')
        self._set_status_override(tr('{p0}の本を検索中…', p0=label))

    def complete_adjacent_book_search(self, direction: int, result: str) -> None:
        if direction > 0 and self._slideshow_waiting_for_next and result != "opened":
            self._slideshow_waiting_for_next = False
            self._slideshow_skip_next_for_book = self._current_book_key
            self._slideshow_repeat_or_stop()
        if result == "opened":
            self._status_override_token += 1
            self._status_override_message = None
            self._update_status()
        elif result == "boundary":
            message = tr('前の書庫はありません') if direction < 0 else tr('次の書庫はありません')
            self._set_status_override(message, 2500)
        elif result in {"unavailable", "error"}:
            self._set_status_override(tr('移動できる書庫がありません'), 2500)

    def _bookmark_pages(self) -> list[int]:
        if not self._current_book_key:
            return []
        bookmarks = self.settings.get("bookmarks", {})
        if not isinstance(bookmarks, dict):
            return []
        raw_pages = bookmarks.get(self._current_book_key, [])
        if not isinstance(raw_pages, list):
            return []

        pages = []
        for page in raw_pages:
            try:
                index = int(page)
            except (TypeError, ValueError):
                continue
            if 0 <= index < self.model.total_pages:
                pages.append(index)
        return sorted(set(pages))

    def _set_bookmark_pages(self, pages: list[int]) -> None:
        bookmarks = self.settings.get("bookmarks")
        if not isinstance(bookmarks, dict):
            bookmarks = {}
        if self._current_book_key:
            if pages:
                bookmarks[self._current_book_key] = sorted(set(pages))
            else:
                bookmarks.pop(self._current_book_key, None)
        self.settings["bookmarks"] = bookmarks
        self._rebuild_bookmark_menu()

    def toggle_current_bookmark(self) -> None:
        if not self._current_book_key or self.model.total_pages <= 0:
            return
        pages = self._bookmark_pages()
        if self.model.current_index in pages:
            pages.remove(self.model.current_index)
        else:
            pages.append(self.model.current_index)
        self._set_bookmark_pages(pages)

    def next_bookmark(self) -> None:
        pages = self._bookmark_pages()
        if not pages:
            return
        for page in pages:
            if page > self.model.current_index:
                self._go_to_index_with_history(page)
                return
        self._go_to_index_with_history(pages[0])

    def previous_bookmark(self) -> None:
        pages = self._bookmark_pages()
        if not pages:
            return
        for page in reversed(pages):
            if page < self.model.current_index:
                self._go_to_index_with_history(page)
                return
        self._go_to_index_with_history(pages[-1])

    def clear_bookmarks_for_current_book(self) -> None:
        if self._current_book_key:
            self._set_bookmark_pages([])

    def _go_to_bookmark(self, page_index: int) -> None:
        if 0 <= page_index < self.model.total_pages:
            self._go_to_index_with_history(page_index)

    @Slot(object)
    def _stage_page_list_runtime(self, runtime: object) -> None:
        staged = runtime if isinstance(runtime, ViewerPageListRuntime) else None
        if staged is None:
            self._staged_page_list_runtime = None
            if not self.book_session.page_list_runtime_deferred:
                self._activate_page_list_runtime(None)
                return
            # A raster open deliberately defers constructing the replacement
            # PageList runtime until the first frame has painted.  Disconnect
            # the retired runtime without clearing its already-materialized
            # model projection, which remains a disabled rollback snapshot.
            previous = self._page_list_runtime
            if previous is not None:
                try:
                    previous.thumbnailReady.disconnect(
                        self._on_page_list_thumbnail_ready
                    )
                except (RuntimeError, TypeError):
                    pass
            self._page_list_runtime = None
            self.page_list.setEnabled(False)
            return
        displayed = self.presentation_state.displayed
        if (
            displayed is not None
            and displayed.token.book.epoch == staged.source_epoch
        ):
            self._activate_page_list_runtime(staged)
            return
        # The source has opened, but the preceding book remains the active
        # presentation until the first complete replacement frame commits.
        # BookSession may retire/delete the preceding runtime immediately, so
        # retain only its already-materialized model projection here.  The old
        # rows are read-only until the new frame commits and cannot navigate
        # the newly installed PageModel by an unrelated row index.
        previous = self._page_list_runtime
        if previous is not None and previous is not staged:
            try:
                previous.thumbnailReady.disconnect(
                    self._on_page_list_thumbnail_ready
                )
            except (RuntimeError, TypeError):
                pass
            try:
                previous.set_visible(False)
            except RuntimeError:
                pass
        self._page_list_runtime = None
        self._staged_page_list_runtime = staged
        self.page_list.setEnabled(False)
        staged.set_visible(False)

    def _activate_page_list_runtime(
        self,
        runtime: ViewerPageListRuntime | None,
    ) -> None:
        previous = self._page_list_runtime
        if previous is not None and previous is not runtime:
            try:
                previous.thumbnailReady.disconnect(
                    self._on_page_list_thumbnail_ready
                )
            except (RuntimeError, TypeError):
                pass
            try:
                previous.set_visible(False)
            except RuntimeError:
                pass
        self._page_list_runtime = runtime
        if self._staged_page_list_runtime is runtime:
            self._staged_page_list_runtime = None
        if runtime is not None and runtime is not previous:
            runtime.thumbnailReady.connect(
                self._on_page_list_thumbnail_ready
            )
        self.page_list.setEnabled(runtime is not None)
        self._refresh_page_list_model()

    def _commit_staged_page_list_runtime(self, epoch: int) -> None:
        runtime = self._staged_page_list_runtime
        if runtime is None or runtime.source_epoch != int(epoch):
            return
        self._activate_page_list_runtime(runtime)

    def _refresh_page_list_model(self) -> None:
        runtime = self._page_list_runtime
        if not self.page_list_dock.isVisible():
            if runtime is not None:
                runtime.set_visible(False)
            self.page_list_model.clear()
            return
        if runtime is None:
            # During a successful replacement open, keep the preceding
            # committed book's rows/icons as a disabled snapshot until the
            # first complete frame atomically activates the staged runtime.
            if self._staged_page_list_runtime is None:
                self.page_list_model.clear()
            return
        runtime.set_visible(True)
        self.page_list_model.set_book(
            runtime.source_epoch,
            runtime.image_ids,
            self.page_list_filter.text(),
        )
        self._sync_page_list_selection()
        self._schedule_page_list_visible_work()

    def _apply_page_list_filter(self) -> None:
        if not self.page_list_dock.isVisible():
            return
        self.page_list_model.set_filter(self.page_list_filter.text())
        self._sync_page_list_selection()
        self._schedule_page_list_visible_work()

    def _sync_page_list_selection(
        self,
        *,
        ensure_visible: bool = True,
        schedule_visible_work: bool = True,
    ) -> None:
        if not self.page_list_dock.isVisible():
            return
        self._updating_page_list_selection = True
        try:
            displayed = self.presentation_state.displayed
            focused_index = self.presentation_state.displayed_page
            row = -1
            if (
                displayed is not None
                and focused_index is not None
                and displayed.token.book.epoch
                == self.page_list_model.book_epoch
            ):
                row = self.page_list_model.row_for_page(focused_index)
            target = (
                self.page_list_model.index(row, 0)
                if row >= 0
                else self.page_list_model.index(-1, 0)
            )
            self.page_list.setCurrentIndex(target)
            if target.isValid() and ensure_visible:
                self.page_list.scrollTo(
                    target,
                    QAbstractItemView.ScrollHint.EnsureVisible,
                )
        finally:
            self._updating_page_list_selection = False
        if schedule_visible_work:
            self._schedule_page_list_visible_work()

    def _flush_presentation_side_effects(self) -> None:
        token = self._pending_presentation_side_effect_token
        self._pending_presentation_side_effect_token = None
        if self._shutdown_prepared or token is None:
            return
        displayed = self.presentation_state.displayed
        if displayed is None or displayed.token != token:
            return
        source = self.book_session.source
        if (
            source is None
            or token.book.epoch != self.book_session.generation
            or token.book.source_identity != id(source)
        ):
            return
        if self._zip_runtime_active:
            # ``framePainted`` is emitted from ViewerWidget.paintEvent.  Keep
            # model resets, large retired-cache destruction, SQLite/history,
            # recent actions, and Browser projection out of that signal's
            # direct call stack; this zero-timer runs after paintEvent returns.
            self.book_session.ensure_page_list_runtime()
            self._commit_staged_page_list_runtime(token.book.epoch)
            self.book_session.release_retired_book_resources()
            self._flush_book_open_projection()
        self._sync_page_history_actions()
        if self.page_list_dock.isVisible():
            self._sync_page_list_selection(
                ensure_visible=True,
                schedule_visible_work=True,
            )
        # Persistence is based on the already committed immutable snapshot,
        # but does not block the ViewerWidget's first paint of that frame.
        self.book_session.notify_page_changed()
        path = self.displayed_browser_path
        identity = (token.book.epoch, id(source), path)
        if path is not None and identity != self._last_displayed_browser_item:
            self._last_displayed_browser_item = identity
            self.displayed_item_changed.emit(self, path)

    def _on_page_list_current_changed(
        self,
        current: object,
        _previous: object,
    ) -> None:
        if (
            self._updating_page_list_selection
            or self._page_list_runtime is None
        ):
            return
        row = current.row() if hasattr(current, "row") else -1
        page_index = self.page_list_model.page_index_at(row)
        if page_index is None or not 0 <= page_index < self.model.total_pages:
            return
        self._go_to_index_with_history(page_index)
        # Selection is a projection of the committed frame, never ownership
        # of the requested/displayed semantic page.
        self._sync_page_list_selection()

    def _page_thumbnail_spec(self) -> ViewerPageThumbnailSpec:
        return ViewerPageThumbnailSpec.create(
            self.thumbnail_size,
            max(1.0, float(self.devicePixelRatioF())),
            self.rotation_angle,
            self.brightness,
            self.contrast,
            self.gamma,
        )

    def _refresh_page_list_thumbnail_spec(self) -> None:
        if (
            self._page_list_runtime is None
            and self._staged_page_list_runtime is not None
        ):
            return
        self.page_list_model.clear_thumbnails()
        self._schedule_page_list_visible_work()

    def _schedule_page_list_visible_work(self) -> None:
        if self._shutdown_prepared:
            return
        self._page_list_viewport_timer.start()

    def _update_page_list_visible_work(self) -> None:
        runtime = self._page_list_runtime
        count = self.page_list_model.rowCount()
        if (
            runtime is None
            or not self.page_list_dock.isVisible()
            or count <= 0
        ):
            if runtime is not None:
                runtime.set_visible(False)
            if self._staged_page_list_runtime is None:
                self.page_list_model.clear_thumbnails()
            return

        runtime.set_visible(True)
        viewport = self.page_list.viewport()
        identity = (
            self.page_list_model.book_epoch,
            self.page_list_filter.text().strip().casefold(),
        )
        if identity != self._page_list_work_identity:
            self._page_list_work_identity = identity
            self._page_list_anchor_row = None
            self._page_list_scroll_direction = 1
        top_index = self.page_list.indexAt(QPoint(1, 1))
        bottom_index = self.page_list.indexAt(
            QPoint(1, max(1, viewport.height() - 2))
        )
        current_index = self.page_list.currentIndex()
        top_row = (
            top_index.row()
            if top_index.isValid()
            else current_index.row()
            if current_index.isValid()
            else self.page_list.verticalScrollBar().value()
        )
        if bottom_index.isValid():
            bottom_row = bottom_index.row()
        else:
            item_extent = max(
                self.thumbnail_size,
                self.page_list.fontMetrics().height(),
            ) + 4
            bottom_row = min(
                count - 1,
                top_row + max(1, viewport.height() // item_extent),
            )
        first_visible = max(0, min(top_row, bottom_row))
        last_visible = min(count - 1, max(top_row, bottom_row))
        if self._page_list_anchor_row is not None:
            if first_visible > self._page_list_anchor_row:
                self._page_list_scroll_direction = 1
            elif first_visible < self._page_list_anchor_row:
                self._page_list_scroll_direction = -1
        self._page_list_anchor_row = first_visible

        visible_rows = list(range(first_visible, last_visible + 1))
        visible_pages = [
            page_index
            for row in visible_rows
            if (page_index := self.page_list_model.page_index_at(row))
            is not None
        ]
        displayed_page = self.presentation_state.displayed_page
        if displayed_page in visible_pages:
            visible_pages.remove(displayed_page)
            visible_pages.insert(0, displayed_page)

        # Read one viewport in the current scroll direction and at most half a
        # viewport behind it.  The fixed caps keep a very large dock from
        # turning this into an all-pages queue.  Rows, not source page numbers,
        # define the window so filtered lists keep the same scroll behavior.
        viewport_rows = max(1, len(visible_rows))
        ahead_count = min(viewport_rows, _PAGE_LIST_MAX_AHEAD_ROWS)
        rear_count = min((viewport_rows + 1) // 2, _PAGE_LIST_MAX_REAR_ROWS)
        if self._page_list_scroll_direction > 0:
            ahead_rows = range(
                last_visible + 1,
                min(count, last_visible + 1 + ahead_count),
            )
            rear_rows = range(
                first_visible - 1,
                max(-1, first_visible - rear_count - 1),
                -1,
            )
        else:
            ahead_rows = range(
                first_visible - 1,
                max(-1, first_visible - ahead_count - 1),
                -1,
            )
            rear_rows = range(
                last_visible + 1,
                min(count, last_visible + 1 + rear_count),
            )

        def mapped_pages(rows) -> list[int]:
            return [
                page_index
                for row in rows
                if (page_index := self.page_list_model.page_index_at(row))
                is not None
            ]

        desired = tuple(
            visible_pages + mapped_pages(ahead_rows) + mapped_pages(rear_rows)
        )
        # QPixmap/QIcon ownership stays with currently visible rows.  Ahead
        # images remain in the runtime's byte-bounded QImage LRU and are
        # republished from there when those rows become visible.
        self.page_list_model.retain_thumbnails(visible_pages)
        runtime.request_visible_pages(
            desired,
            self._page_thumbnail_spec(),
            visible_count=len(visible_pages),
        )

    @Slot(object)
    def _on_page_list_thumbnail_ready(self, result: object) -> None:
        runtime = self._page_list_runtime
        if (
            runtime is None
            or not isinstance(result, ViewerPageThumbnail)
            or result.runtime_id != runtime.runtime_id
            or result.source_epoch != runtime.source_epoch
            or result.source_epoch != self.page_list_model.book_epoch
            or result.spec != self._page_thumbnail_spec()
            or result.page_index not in runtime.visible_pages
            or not self.page_list_dock.isVisible()
            or result.qimage is None
            or result.qimage.isNull()
        ):
            return
        if (
            self.page_list_model.image_id_for_page(result.page_index)
            != result.image_id
        ):
            return
        pixmap = QPixmap.fromImage(result.qimage)
        if pixmap.isNull():
            return
        pixmap.setDevicePixelRatio(result.spec.device_pixel_ratio)
        self.page_list_model.set_thumbnail(
            result.page_index,
            QIcon(pixmap),
        )
        runtime.record_pixmap_upload()

    def _set_page_list_paused(self, paused: bool) -> None:
        if self._shutdown_prepared and not paused:
            paused = True
        seen: set[int] = set()
        for runtime in (
            self._page_list_runtime,
            self._staged_page_list_runtime,
        ):
            if runtime is None or id(runtime) in seen:
                continue
            seen.add(id(runtime))
            runtime.set_paused(paused)

    def _rebuild_bookmark_menu(self) -> None:
        self.bookmark_menu.clear()
        has_book = bool(self._current_book_key and self.model.total_pages > 0)
        pages = self._bookmark_pages() if has_book else []

        toggle_text = tr('現在ページをブックマーク')
        if has_book and self.model.current_index in pages:
            toggle_text = tr('現在ページのブックマークを解除')
        toggle_action = QAction(toggle_text, self)
        toggle_action.setEnabled(has_book)
        toggle_action.triggered.connect(self.toggle_current_bookmark)
        self.bookmark_menu.addAction(toggle_action)

        next_action = QAction(tr('次のブックマーク'), self)
        next_action.setEnabled(bool(pages))
        next_action.triggered.connect(self.next_bookmark)
        previous_action = QAction(tr('前のブックマーク'), self)
        previous_action.setEnabled(bool(pages))
        previous_action.triggered.connect(self.previous_bookmark)
        self.bookmark_menu.addAction(next_action)
        self.bookmark_menu.addAction(previous_action)
        self.bookmark_menu.addSeparator()

        if pages:
            for page in pages:
                action = QAction(tr('{p0} ページ', p0=page + 1), self)
                action.triggered.connect(lambda checked=False, value=page: self._go_to_bookmark(value))
                self.bookmark_menu.addAction(action)
            self.bookmark_menu.addSeparator()
        else:
            empty_action = QAction(tr('ブックマークなし'), self)
            empty_action.setEnabled(False)
            self.bookmark_menu.addAction(empty_action)
            self.bookmark_menu.addSeparator()

        clear_action = QAction(tr('この本のブックマークをクリア'), self)
        clear_action.setEnabled(bool(pages))
        clear_action.triggered.connect(self.clear_bookmarks_for_current_book)
        self.bookmark_menu.addAction(clear_action)

    @Slot(object)
    def _bind_zip_runtime(self, runtime: object) -> None:
        previous = self._zip_runtime
        if previous is not None:
            try:
                previous.frameReady.disconnect(self._on_zip_runtime_frame_ready)
            except (RuntimeError, TypeError):
                pass
            try:
                previous.layoutMetadataReady.disconnect(
                    self._on_raster_layout_metadata
                )
            except (RuntimeError, TypeError):
                pass
            try:
                previous.idle.disconnect(self._on_raster_runtime_idle)
            except (RuntimeError, TypeError):
                pass
        if previous is not runtime:
            # Deactivate while ``previous`` is still the bound owner.  This
            # avoids cancelling the newly installed book runtime merely as a
            # side effect of retiring the old book.
            self._deactivate_zip_runtime(clear_artifacts=False)
            self._raster_topology_cache_key = None
            self._raster_topology = None
        self._zip_runtime = (
            runtime if isinstance(runtime, RasterBookRuntime) else None
        )
        if self._zip_runtime is not None:
            self._apply_raster_memory_policy()
            self._zip_runtime.frameReady.connect(
                self._on_zip_runtime_frame_ready
            )
            self._zip_runtime.layoutMetadataReady.connect(
                self._on_raster_layout_metadata
            )
            self._zip_runtime.idle.connect(self._on_raster_runtime_idle)

    @Slot(object)
    def _on_raster_layout_metadata(self, metadata: object) -> None:
        from .raster_layout_metadata import RasterLayoutMetadata

        if (
            not isinstance(metadata, RasterLayoutMetadata)
            or self._shutdown_prepared
        ):
            return
        runtime = self._zip_runtime
        source = self.book_session.source
        if (
            runtime is None
            or runtime is not self.book_session.viewer_runtime
            or runtime.source is not source
            or metadata.source_epoch != self.book_session.generation
            or metadata.source_identity != id(source)
        ):
            return
        sizes = tuple(
            (index, size)
            for index, image_id, size in metadata.observations
            if size is not None and self.model.image_id_at(index) == image_id
        )
        if not self.model.set_image_sizes(sizes, preserve_position=True):
            return
        if (
            not self._zip_runtime_active
            or self.presentation_state.replacement_open_pending
            or not self.model.total_pages
        ):
            return
        requested = self.presentation_state.requested
        if requested is None:
            return
        spread = self.model.spread_at()
        focused = self.model.focused_index
        if focused not in {slot.page_index for slot in spread.slots}:
            self.model.go_to_index(focused)
            spread = self.model.spread_at()
        identity = tuple(
            (slot.page_index, slot.image_id) for slot in spread.slots
        )
        requested_identity = tuple(
            (page.index, page.image_id) for page in requested.unit.pages
        )
        if identity != requested_identity:
            had_timer = self._zip_runtime_request_timer.isActive()
            remaining = self._zip_runtime_request_timer.remainingTime()
            input_kind = self._pending_raster_input_kind
            repeat_key = self._pending_raster_repeat_key
            displayed = self.presentation_state.displayed
            self._refresh_view(
                navigation=(
                    PresentationNavigation.REFRESH
                    if displayed is not None
                    and displayed.token == requested.token
                    else requested.navigation
                ),
                input_kind=input_kind or NavigationInputKind.REFRESH,
                repeat_key=repeat_key,
            )
            if (
                had_timer
                and remaining >= 0
                and self._zip_runtime_request_timer.isActive()
            ):
                self._zip_runtime_request_timer.start(
                    min(
                        remaining,
                        max(0, self._zip_runtime_request_timer.remainingTime()),
                    )
                )
            return
        request = self._zip_runtime_request(spread)
        if request is not None and runtime.refresh_work_order(request):
            pending = self._pending_zip_runtime_request
            if (
                pending is not None
                and pending.request_id == request.request_id
            ):
                self._pending_zip_runtime_request = request

    def _zip_display_unit(
        self,
        indexes: tuple[int, ...],
        *,
        start_index: int,
        is_single: bool,
    ) -> RasterDisplayUnit:
        pages: list[RasterPage] = []
        for page_index in indexes:
            image_id = self.model.image_id_at(page_index)
            if image_id is None:
                continue
            pages.append(
                RasterPage(
                    page_index,
                    image_id,
                    self.model.get_image_size(page_index),
                )
            )
        return RasterDisplayUnit(
            start_index,
            tuple(pages),
            is_single,
        )

    def _raster_book_topology(
        self,
        source: ZipImageSource | FolderImageSource | SevenZipImageSource,
    ) -> _PageModelRasterTopology:
        """Expose canonical boundaries without materializing the full book.

        PageModel's indexed boundaries provide ordinal lookups and local
        metadata updates. The plan constructs only requested display units.
        """

        cache_key = (
            int(self.book_session.generation),
            id(source),
        )
        cached = self._raster_topology
        if self._raster_topology_cache_key == cache_key and cached is not None:
            return cached

        topology = _PageModelRasterTopology(
            self.model,
            self._zip_display_unit,
        )
        self._raster_topology_cache_key = cache_key
        self._raster_topology = topology
        return topology

    def _raster_background_allowed(self) -> bool:
        # Full-pixel fidelity is not a reason to disable prefetch. Unknown and
        # known full-source jobs still pass runtime admission/reservations.
        # Keep magnifier promotion isolated; cancel already restores normal
        # warmup. During repeated zoom changes prepare only the current frame.
        return not (
            self.viewer.magnifier_selecting
            or self.viewer.magnifier_active
            or (
                self.fit_mode == "manual_zoom"
                and self._raster_zoom_warmup_timer.isActive()
            )
        )

    def _zip_runtime_request(
        self,
        spread: DisplaySpread,
    ) -> RasterRequest | None:
        """Build the one runtime request used by every raster-book mode."""
        source = self.book_session.source
        runtime = self._zip_runtime
        if (
            not isinstance(source, (ZipImageSource, FolderImageSource, SevenZipImageSource))
            or runtime is None
            or runtime is not self.book_session.viewer_runtime
            or runtime.source is not source
            or self.model.source is not source
        ):
            return None
        current_indexes = tuple(slot.page_index for slot in spread.slots)
        current = self._zip_display_unit(
            current_indexes,
            start_index=spread.start_index,
            is_single=spread.is_single,
        )
        direction = self.presentation_state.direction
        decoder_bound = self._current_book_runtime_decode_bounds()
        if self.viewer.magnifier_selecting or self.viewer.magnifier_active:
            decoder_bound = None
        background_enabled = self._raster_background_allowed()
        warmup_plan = RasterWarmupPlan(
            self._raster_book_topology(source),
            current=current,
            identity_of=lambda unit: unit.identity,
            page_indexes_of=lambda unit: (
                page.page_index for page in unit.pages
            ),
            direction=direction,
            # Full-source promotion for the lens remains current-only;
            # stable actual/manual/nearest views can prefetch within budget.
            # The legacy PDF prefetch preset/count controls are unchanged.
            background_enabled=background_enabled,
            nearby_units=(
                self._zip_display_unit(
                    tuple(slot.page_index for slot in candidate.slots),
                    start_index=candidate.start_index,
                    is_single=candidate.is_single,
                )
                for candidate in self.model.prefetch_spreads(direction=direction)
            ),
        )

        render_spec = RasterRenderSpec(
            viewport_size=(
                max(1, self.viewer.width()),
                max(1, self.viewer.height()),
            ),
            device_pixel_ratio=max(
                1.0,
                float(self.viewer.devicePixelRatioF()),
            ),
            fit_mode=self.fit_mode,
            manual_zoom=self.viewer.manual_zoom,
            gap=self.gap,
            join_spread_pages=self.join_spread_pages,
            horizontal_alignment=self.horizontal_alignment,
            rotation=self.rotation_angle,
            downscale_algorithm=self.viewer_downscale_algorithm,
            upscale_algorithm=self.viewer_upscale_algorithm,
            split_wide_image=self.split_wide_image,
            reading_direction=self.reading_direction,
            brightness=self.brightness,
            contrast=self.contrast,
            gamma=self.gamma,
            decoder_maximum_size=decoder_bound,
            decoder_headroom=self._current_book_runtime_decode_headroom(),
            decoder_layout_sized=True,
        )
        return RasterRequest(
            self.book_session.generation,
            self._active_request_id,
            current,
            warmup_plan,
            render_spec,
            navigation_direction=direction,
            resolve_layout_metadata=(
                (
                    isinstance(source, FolderImageSource)
                    # A background unit can still become a single page after
                    # its header proves it is wide. Keep the geometry
                    # boundary in the same worker lane even when the
                    # currently visible unit is already provisional
                    # single-page; otherwise that unit may be decoded as a
                    # spread and decoded again after the layout repair.
                    or isinstance(source, ZipImageSource)
                )
                and self.view_mode == "spread"
                and self.treat_wide_image_as_single
            ),
        )

    def _activate_zip_runtime(self) -> None:
        if self._zip_runtime_active:
            return
        self._cancel_pending_decode_demand()
        self._cancel_deferred_pdf_prefetch()
        self._prepared_display_timer.stop()
        self._raster_paint_fallback_timer.stop()
        self._raster_prefetch_after_paint = None
        self._clear_raster_prefetch_pipeline()
        self.image_cache.suspend_for_book_runtime()
        self.viewer.set_direct_display_mode(True)
        self._zip_runtime_current_frame_serial = 0
        self._zip_runtime_last_painted_serial = 0
        self._zip_runtime_active = True

    def _deactivate_zip_runtime(
        self,
        *,
        clear_artifacts: bool = False,
        preserve_book_state: bool = False,
    ) -> None:
        self._raster_magnifier_cancel_timer.stop()
        self._raster_zoom_warmup_timer.stop()
        self._raster_zoom_warmup_context = None
        self._clear_pending_raster_navigation(reset_policy=True)
        if not self._zip_runtime_active:
            if clear_artifacts and self._zip_runtime is not None:
                self._zip_runtime.cancel(clear_artifacts=True)
            return
        self._zip_runtime_active = False
        if self._zip_runtime is not None:
            if preserve_book_state and not clear_artifacts:
                self._zip_runtime.suspend()
            else:
                self._zip_runtime.cancel(clear_artifacts=clear_artifacts)
        self._raster_paint_fallback_timer.stop()
        self._zip_runtime_current_frame_serial = 0
        self._zip_runtime_last_painted_serial = 0
        self.viewer.set_direct_display_mode(False)
        self._release_raster_interactive_lane()

    def _clear_pending_raster_navigation(
        self,
        *,
        reset_policy: bool = False,
    ) -> None:
        self._zip_runtime_request_timer.stop()
        self._pending_zip_runtime_request = None
        self._pending_raster_input_kind = None
        self._pending_raster_repeat_key = None
        if reset_policy:
            self._navigation_wheel_timestamp_ns = None
            self._navigation_admission.reset()

    @Slot(object)
    def _on_raster_runtime_idle(self, runtime: object) -> None:
        if (
            runtime is not self._zip_runtime
            or self._pending_zip_runtime_request is None
            or self._shutdown_prepared
            or not self._zip_runtime_active
        ):
            return
        if self._pending_raster_input_kind is NavigationInputKind.SLIDER_SCRUB:
            # Slider drag owns its final boundary. Runtime idle must not turn
            # every intermediate slider value back into a decode request.
            return
        if self._pending_raster_input_kind is NavigationInputKind.WHEEL:
            # A rapid wheel sequence owns a short cadence-derived trailing
            # boundary. Worker idle alone must not restart transit-page work.
            if not self._zip_runtime_request_timer.isActive():
                self._zip_runtime_request_timer.start(
                    self._navigation_admission.wheel_flush_delay_ms
                )
            return
        # ``stage`` can synchronously remove a queued job and emit idle.  The
        # zero timer is solely a re-entry guard; it is not an admission wait.
        self._zip_runtime_request_timer.start(0)

    def _stage_raster_runtime_request(
        self,
        runtime: RasterBookRuntime,
        request: RasterRequest,
        *,
        input_kind: NavigationInputKind,
        repeat_key: int | None,
        publish_cached: bool = False,
    ) -> bool:
        self._zip_runtime_request_timer.stop()
        # Set ownership before Runtime.stage(): cancelling a not-yet-started
        # job can synchronously emit idle back into this Window.
        self._pending_zip_runtime_request = request
        self._pending_raster_input_kind = input_kind
        self._pending_raster_repeat_key = repeat_key
        staged = (
            runtime.stage(request, publish_cached=True)
            if publish_cached
            else runtime.stage(request)
        )
        if not staged:
            self._fail_raster_runtime_request(runtime)
            return False
        if input_kind is NavigationInputKind.WHEEL:
            self._zip_runtime_request_timer.start(
                self._navigation_admission.wheel_flush_delay_ms
            )
            return True
        if (
            input_kind is not NavigationInputKind.SLIDER_SCRUB
            and not runtime.has_unfinished_tasks()
        ):
            self._zip_runtime_request_timer.start(0)
        return True

    def _dispatch_pending_zip_runtime_request(self) -> None:
        request = self._pending_zip_runtime_request
        if request is None:
            return
        self._clear_pending_raster_navigation(reset_policy=False)
        runtime = self._zip_runtime
        current_identity = tuple(
            (slot.page_index, slot.image_id)
            for slot in self.model.spread_at().slots
        )
        if (
            request is None
            or runtime is None
            or self._shutdown_prepared
            or not self._zip_runtime_active
            or request.request_id != self._active_request_id
            or request.source_epoch != self.book_session.generation
            or request.current.identity != current_identity
        ):
            # A queued idle callback can belong to an input superseded by a
            # ready hit or book transition.  Never cancel a newer Runtime
            # request merely because this pending serial is obsolete.
            return
        if runtime.release_staged(request):
            self._queue_ready_transit_frame_after_cold_dispatch(request)
            return
        self._fail_raster_runtime_request(runtime)

    def _queue_ready_transit_frame_after_cold_dispatch(
        self,
        request: RasterRequest,
    ) -> bool:
        """Post an update for a legitimate committed transit frame.

        Decode priority is already settled by ``runtime.request`` before this
        method runs.  PresentationState remains the semantic authority: only
        its most recently committed frame may paint, and only while a distinct
        cold request from the same book is pending. Qt may coalesce paints;
        no frame queue, timer or placeholder path is introduced.
        """

        displayed = self.presentation_state.displayed
        requested = self.presentation_state.requested
        if (
            displayed is None
            or requested is None
            or requested.token.request_serial != int(request.request_id)
            or displayed.token.book != requested.token.book
            or displayed.unit.identity == requested.unit.identity
            or self.viewer.displayed_page_indexes
            != displayed.unit.page_indexes
        ):
            return False
        return self.viewer.queue_pending_committed_frame_paint()

    def _finish_wheel_navigation(self) -> None:
        self._navigation_admission.finish_wheel()
        self._navigation_wheel_timestamp_ns = None
        if self._pending_raster_input_kind is NavigationInputKind.WHEEL:
            self._zip_runtime_request_timer.stop()
            self._dispatch_pending_zip_runtime_request()

    def _finish_key_repeat_navigation(self, key: int) -> None:
        self._navigation_admission.finish_key_repeat(key)
        if (
            self._pending_raster_input_kind is NavigationInputKind.KEY_REPEAT
            and self._pending_raster_repeat_key == int(key)
        ):
            self._zip_runtime_request_timer.stop()
            self._dispatch_pending_zip_runtime_request()

    def _finish_slider_navigation(self) -> None:
        self._navigation_admission.finish_slider()
        if self._pending_raster_input_kind is NavigationInputKind.SLIDER_SCRUB:
            self._zip_runtime_request_timer.stop()
            self._dispatch_pending_zip_runtime_request()

    @Slot(object)
    def _observe_wheel_input(self, timestamp_ms: object) -> None:
        observed = int(timestamp_ms)
        self._navigation_wheel_timestamp_ns = (
            observed * 1_000_000 if observed > 0 else perf_counter_ns()
        )

    def _finish_pending_navigation_sequence(self) -> None:
        if self._pending_raster_input_kind in {
            NavigationInputKind.WHEEL,
            NavigationInputKind.KEY_REPEAT,
            NavigationInputKind.SLIDER_SCRUB,
        }:
            self._zip_runtime_request_timer.stop()
            self._dispatch_pending_zip_runtime_request()
        self._navigation_repeat_key = None
        self._navigation_wheel_timestamp_ns = None
        self._navigation_admission.reset()

    def _fail_raster_runtime_request(
        self,
        runtime: RasterBookRuntime | None,
    ) -> None:
        self._clear_pending_raster_navigation(reset_policy=True)
        if runtime is not None:
            runtime.cancel(clear_artifacts=False)
        self._release_raster_interactive_lane()
        message = tr('Raster Viewer runtimeは要求を受け付けられません。')
        self.presentation_state.fail_pending(message)
        self._project_presentation_surface()
        self._set_status_override(message)

    def _on_zip_runtime_frame_ready(
        self,
        frame: RasterFrame,
    ) -> None:
        presentation_token = self._presentation_token_for_request(
            frame.request_id
        )
        current_spread = self.model.spread_at()
        current_identity = tuple(
            (slot.page_index, slot.image_id)
            for slot in current_spread.slots
        )
        if (
            self._shutdown_prepared
            or not self._zip_runtime_active
            or presentation_token is None
            or frame.request_id != self._active_request_id
            or frame.source_epoch != self.book_session.generation
            or frame.source_identity != id(self.book_session.source)
            or frame.unit.identity != current_identity
        ):
            return
        if not self._validate_external_raster_source():
            return
        by_logical_page: dict[int, list[object]] = {}
        for page in frame.pages:
            by_logical_page.setdefault(page.page_index, []).append(page)
        discovered_sizes: list[tuple[int, tuple[int, int]]] = []
        for page_index, outputs in by_logical_page.items():
            if len(outputs) == 1:
                logical_size = outputs[0].original_size
            else:
                logical_size = (
                    sum(output.original_size[0] for output in outputs),
                    max(output.original_size[1] for output in outputs),
                )
            discovered_sizes.append((page_index, logical_size))
        self.model.set_image_sizes(discovered_sizes)
        spread = self.model.spread_at()
        if tuple(
            (slot.page_index, slot.image_id) for slot in spread.slots
        ) != frame.unit.identity:
            self._refresh_view()
            return

        images: list[ViewerImage] = []
        for page in frame.pages:
            source = page.source_qimage
            images.append(
                ViewerImage(
                    page_index=page.page_index,
                    image_id=page.image_id,
                    pixmap=page.pixmap,
                    original_size=page.original_size,
                    error=page.error,
                    loading=False,
                    rendered_size=(
                        (source.width(), source.height())
                        if source is not None and page.source_is_preview
                        else None
                    ),
                    pre_rotated=False,
                    qimage=source,
                    source_generation=frame.source_epoch,
                    source_identity=str(frame.source_identity),
                    split_range=page.split_range,
                    display_prepared=(page.pixmap is not None),
                    source_is_preview=page.source_is_preview,
                )
            )
        for slot in self._display_unit.slots:
            outputs = by_logical_page.get(slot.page_index, [])
            error = next(
                (
                    str(output.error)
                    for output in outputs
                    if output.error
                ),
                None,
            )
            self.presentation_state.transition_slot(
                presentation_token,
                page_index=slot.page_index,
                image_id=slot.image_id,
                state=(
                    ViewerSlotState.FAILED
                    if error is not None
                    else ViewerSlotState.READY
                ),
                error=error,
            )
        self._zip_runtime_current_frame_serial = (
            self.viewer.commit_display_ready_frame(
                spread,
                images,
                presentation_token,
            )
        )
        first_frame_failed = bool(
            self._awaiting_first_frame
            and self._first_frame_image_id
            and any(
                page.logical_image_id == self._first_frame_image_id
                and page.error
                for page in frame.pages
            )
        )
        if first_frame_failed:
            # An error frame is a complete, atomically committed result.  Do
            # not leave the interactive-open/browser gate waiting for a paint
            # acknowledgement that represents a successful source image.
            # This mirrors the retained-path terminal-error contract while
            # keeping a successful spread partner visible.
            self._cancel_interactive_open()
        if (
            self._zip_runtime_current_frame_serial > 0
            and self._zip_runtime is not None
            and self.presentation_state.displayed is not None
            and self.presentation_state.displayed.token == presentation_token
        ):
            # The first accepted complete commit activates one continuous,
            # book-scoped worker. Later commits only recenter its unstarted
            # order; paint is not a scheduler gate. Terminal error frames are
            # complete units too, so they must not strand the rest of a book.
            self._zip_runtime.release_continuous_warmup(
                request_id=frame.request_id,
            )
        if any(
            page.source_qimage is not None and not page.source_is_preview
            for page in frame.pages
        ):
            self.viewer.resume_magnifier_after_source_render()
        performance_trace.mark(
            self._active_open_trace_id,
            "raster_runtime.commit.completed",
            f"unit={frame.unit.identity!r} cache_hit={frame.cache_hit}",
        )
        self._raster_paint_fallback_timer.start()

    def _on_zip_runtime_frame_painted(
        self,
        frame_serial: int,
        image_ids: object,
    ) -> None:
        if (
            not self._zip_runtime_active
            or int(frame_serial) != self._zip_runtime_current_frame_serial
            or int(frame_serial) <= self._zip_runtime_last_painted_serial
            or not isinstance(image_ids, tuple)
        ):
            return
        self._zip_runtime_last_painted_serial = int(frame_serial)
        self._raster_paint_fallback_timer.stop()
        # Runtime-backed page projections are deliberately queued only after
        # the complete frame has painted.  A zero timer started from commit
        # can otherwise run before Qt services the posted paint event.
        if self._pending_presentation_side_effect_token is not None:
            self._presentation_side_effect_timer.start()
        if self._zip_runtime is not None:
            self._zip_runtime.release_prefetch(
                request_id=self._active_request_id,
            )
        self._defer_zip_runtime_browser_resume()
        performance_trace.mark(
            self._active_open_trace_id,
            "raster_runtime.paint.completed",
        )

    def _presentation_layout_signature(self) -> tuple[object, ...]:
        return (
            max(1, self.viewer.width()),
            max(1, self.viewer.height()),
            self.fit_mode,
            round(float(self.viewer.manual_zoom), 6),
            self.gap,
            self.join_spread_pages,
            self.horizontal_alignment,
            self.rotation_angle % 360,
            self.viewer_downscale_algorithm,
            self.viewer_upscale_algorithm,
            self.split_wide_image,
            self.reading_direction,
            self.view_mode,
            self.single_first_page,
            self.treat_wide_image_as_single,
            round(float(self.brightness), 6),
            round(float(self.contrast), 6),
            round(float(self.gamma), 6),
            self._current_raster_decode_bounds(),
            self.image_cache.generation,
        )

    def _begin_presentation_request(
        self,
        spread: DisplaySpread,
        navigation: PresentationNavigation,
    ):
        source = self.book_session.source
        if not spread.slots or self.model.total_pages <= 0:
            raise RuntimeError("presentation request requires an active book")
        focused_index = self.model.focused_index
        focused_identity = (
            self.model.page_identity(focused_index)
            or self.model.image_id_at(focused_index)
        )
        if focused_identity is None:
            raise RuntimeError("focused presentation page has no identity")
        unit = PresentationUnit(
            spread.start_index,
            focused_index,
            focused_identity,
            tuple(
                PresentationPage(
                    slot.page_index,
                    self.model.page_identity(slot.page_index)
                    or slot.image_id,
                    slot.image_id,
                )
                for slot in spread.slots
            ),
            spread.is_single,
        )
        book_key = (
            self._current_book_key
            or str(self.book_session.current_path or "")
            or f"fake-book:{self.book_session.generation}:{id(self.model)}"
        )
        request = self.presentation_state.request_frame(
            PresentationBook(
                self.book_session.generation,
                id(source) if source is not None else id(self.model),
                book_key,
            ),
            unit,
            PresentationValues(
                self.model.total_pages,
                focused_index,
                self.model.display_path_for_index(focused_index),
                self.model.file_size_for_index(focused_index),
            ),
            self._presentation_layout_signature(),
            max(1.0, float(self.viewer.devicePixelRatioF())),
            navigation,
            progress_values=(
                self._pending_progress_seed[1]
                if (
                    self._pending_progress_seed is not None
                    and self._pending_progress_seed[0]
                    == self.book_session.generation
                    and self.presentation_state.current_book_epoch
                    != self.book_session.generation
                    and navigation
                    is not PresentationNavigation.NORMAL
                )
                else None
            ),
        )
        self._project_presentation_surface()
        if (
            navigation is PresentationNavigation.NORMAL
            and self._pending_progress_seed is not None
            and self._pending_progress_seed[0]
            == self.book_session.generation
        ):
            self._pending_progress_seed = None
        self._request_id_adapter = None
        self._visible_page_indexes_adapter = None
        return request

    def _presentation_token_for_request(
        self,
        request_id: int,
    ) -> PresentationFrameToken | None:
        requested = self.presentation_state.requested
        active_request_id = getattr(self, "_active_request_id", None)
        if (
            requested is None
            or requested.token.request_serial != int(request_id)
            or (
                active_request_id is not None
                and requested.token.request_serial != active_request_id
            )
        ):
            return None
        return requested.token

    def _on_viewer_frame_committed(self, event: object) -> None:
        if self._shutdown_prepared or not isinstance(event, ViewerFrameCommit):
            return
        token = event.frame_token
        if not isinstance(token, PresentationFrameToken):
            return
        requested = self.presentation_state.requested
        active_request_id = getattr(self, "_active_request_id", None)
        if (
            requested is None
            or requested.token != token
            or (
                active_request_id is not None
                and token.request_serial != active_request_id
            )
        ):
            return
        expected_identity = tuple(
            (page.index, page.image_id) for page in requested.unit.pages
        )
        if event.spread_identity != expected_identity:
            return
        failed_indexes = set(event.failed_page_indexes)
        errors_by_page = {
            slot.page_index: (
                slot.error or tr('画像を表示できません。')
            )
            for slot in requested.display_tracker.slots
            if slot.page_index in failed_indexes
        }
        commit = self.presentation_state.commit_frame(
            token,
            event.widget_frame_serial,
            (
                index
                for index in event.page_indexes
                if index not in failed_indexes
            ),
            errors_by_page,
        )
        if commit is None:
            return
        self._project_presentation_surface()
        self._apply_presentation_commit(commit)

    def _project_presentation_surface(self) -> bool:
        """Project canvas state and the separate page-only navigation feedback."""

        applied = self.viewer.apply_presentation_surface(
            self.presentation_state.surface
        )
        self._update_slider()
        self._update_status()
        return applied

    def _apply_presentation_commit(
        self,
        commit: PresentationCommit,
    ) -> None:
        # The semantic state was replaced by one immutable snapshot before
        # these projections run. This whole slot is synchronous with the
        # Widget's complete-frame swap.
        if not self._zip_runtime_active:
            self._commit_staged_page_list_runtime(
                commit.frame.token.book.epoch
            )
        if not self._zip_runtime_active:
            # Legacy/PDF callers keep their historical synchronous projection
            # contract. Raster runtimes defer these page-invariant widgets
            # until the complete frame's paint acknowledgement below.
            self._sync_page_list_selection(
                ensure_visible=False,
                schedule_visible_work=False,
            )
            self._sync_page_history_actions()
        # Raster PageList selection/scroll, action projection, thumbnail work,
        # and persistence stay outside commit->paint. PresentationState has
        # already atomically committed their semantic page here.
        self._pending_presentation_side_effect_token = commit.frame.token
        if self._zip_runtime_active:
            # A previous frame may already have armed its post-paint zero
            # timer.  A newer ready hit must disarm it until *its own* paint
            # acknowledgement, otherwise the new token could flush early.
            self._presentation_side_effect_timer.stop()
        else:
            self._presentation_side_effect_timer.start()
        if (
            self._pending_progress_seed is not None
            and self._pending_progress_seed[0]
            == commit.frame.token.book.epoch
        ):
            self._pending_progress_seed = None
        self.presentationCommitted.emit(commit)

    def _validate_external_raster_source(self) -> bool:
        source = self.book_session.source
        if isinstance(source, SevenZipImageSource):
            try:
                source.validate_archive()
            except ImageSourceError as exc:
                runtime = self.book_session.viewer_runtime
                if runtime is not None:
                    runtime.cancel(clear_artifacts=True)
                self.presentation_state.fail_pending(str(exc))
                self._set_status_override(str(exc))
                return False
        return True

    def _refresh_view(
        self,
        *,
        navigation: PresentationNavigation = PresentationNavigation.REFRESH,
        input_kind: NavigationInputKind = NavigationInputKind.REFRESH,
        repeat_key: int | None = None,
        input_timestamp_ns: int | None = None,
    ) -> None:
        if not self._validate_external_raster_source():
            return
        # A caller that cancelled the magnifier in order to navigate or change
        # layout owns this refresh.  Suppress the cancel signal's zero-timer
        # fallback so the same display unit is not requested twice.
        self._raster_magnifier_cancel_timer.stop()
        # Once a raster book owns direct display, legacy prepared-display
        # timers/tasks are already suspended.  Avoid touching that scheduler
        # on every ready page turn.
        if not self._zip_runtime_active:
            self._cancel_pending_display_demand()
            self.viewer.supersede_pending_display()
        if self._awaiting_first_frame:
            focused_image_id = self.model.image_id_at(self.model.focused_index)
            if focused_image_id is not None:
                # Navigation can occur before the original first frame paints.
                # Move the gate to the newly requested identity so an old page
                # cannot keep spread-partner work blocked indefinitely.
                self._first_frame_image_id = focused_image_id
        spread = self.model.spread_at()
        presentation_request = self._begin_presentation_request(
            spread,
            navigation,
        )
        request_id = presentation_request.token.request_serial
        pdf_source = self.book_session.source
        self._pdf_loupe_cache.set_source(
            pdf_source if isinstance(pdf_source, PdfImageSource) else None
        )
        self._pdf_loupe_cache.retain_pages({slot.page_index for slot in spread.slots})
        if tuple(
            slot.page_index for slot in spread.slots
        ) != self.viewer.displayed_page_indexes or (
            self.viewer.magnifier_source_page is not None
            and self.viewer.magnifier_source_page not in tuple(
                slot.page_index for slot in spread.slots
            )
        ):
            self.viewer.cancel_magnifier()
            self._raster_magnifier_cancel_timer.stop()
        zip_request = self._zip_runtime_request(spread)
        if zip_request is not None:
            # Every raster request already captures the live viewport/DPR in
            # its render spec. A resize timer queued before this navigation is
            # therefore obsolete; letting it fire mid-burst would invalidate
            # freshly built frames and reset the input work order.
            self._raster_viewport_timer.stop()
            self._presentation_viewport_refresh_required = False
            self._activate_zip_runtime()
            self._raster_paint_fallback_timer.stop()
            self._zip_runtime_current_frame_serial = 0
            runtime = self._zip_runtime
            if runtime is None:
                return
            admission = self._navigation_admission.decide(
                input_kind,
                direction=zip_request.navigation_direction,
                repeat_key=repeat_key,
                slider_drag_active=self.slider.isSliderDown(),
                now_ns=input_timestamp_ns,
            )
            if not runtime.has_cached_current(zip_request):
                self._hold_raster_interactive_lane()
                if admission is NavigationAdmissionDecision.STAGE:
                    self._stage_raster_runtime_request(
                        runtime,
                        zip_request,
                        input_kind=input_kind,
                        repeat_key=repeat_key,
                    )
                    return
                self._clear_pending_raster_navigation(reset_policy=False)
                # The runtime applies the source-overlap and worker-capacity
                # limits for started compatibility.  A cold wheel may retain
                # a started ZIP spread that shares a source page with the new
                # current; unrelated single-lane warmup is still cancelled.
                accepted = runtime.request(
                    zip_request,
                    preserve_started_compatible=(
                        input_kind is NavigationInputKind.WHEEL
                    ),
                )
                if accepted:
                    self._queue_ready_transit_frame_after_cold_dispatch(
                        zip_request
                    )
                    return
                self._fail_raster_runtime_request(runtime)
                return
            # A ready wheel frame publishes synchronously through request().
            # Its recentered background order can continue in the available
            # worker slot; a later cold target still preempts that work.
            if admission is NavigationAdmissionDecision.STAGE:
                self._stage_raster_runtime_request(
                    runtime,
                    zip_request,
                    input_kind=input_kind,
                    repeat_key=repeat_key,
                    publish_cached=True,
                )
                return
            self._clear_pending_raster_navigation(reset_policy=False)
            if runtime.request(zip_request):
                if input_kind is NavigationInputKind.WHEEL:
                    displayed = self.presentation_state.displayed
                    if (
                        displayed is not None
                        and displayed.token.request_serial
                        == zip_request.request_id
                        and displayed.token.book.epoch
                        == zip_request.source_epoch
                    ):
                        self.viewer.queue_pending_committed_frame_paint()
                return
            self._fail_raster_runtime_request(runtime)
            return
        if isinstance(
            self.book_session.source,
            (ZipImageSource, FolderImageSource, SevenZipImageSource),
        ):
            # Raster-runtime books never fall back per feature or decoder
            # failure. A missing runtime is a book-lifetime error, not an
            # eligibility decision.
            message = tr('Raster Viewer runtimeを初期化できません。')
            self.presentation_state.fail_pending(message)
            self._project_presentation_surface()
            self._set_status_override(message)
            return
        self._deactivate_zip_runtime(clear_artifacts=False)
        self.image_cache.set_raster_decode_bounds(
            self._current_raster_decode_bounds()
        )
        self.image_cache.set_render_spec(self._current_pdf_render_spec())
        if self.viewer.has_pending_prepared_rendering():
            # ZipPlaFork rebuilds the one pending work order around every new
            # current page. Replan queued display preparation here rather than
            # leaving the old direction active until the idle timer fires.
            self._schedule_prepared_display_prefetch()
        if self.viewer.apply_prepared_display(
            spread,
            source_generation=self.image_cache.generation,
            source_identity=self._prepared_source_identity(),
            frame_token=presentation_request.token,
        ):
            source_pages = self._cached_pages_for_prepared_spread(
                spread,
                include_tracked=True,
            )
            if source_pages is not None:
                # Keep only the current unit's decoded source attached for
                # magnifier/resize.  The prepared pixmap remains the paint
                # artifact, so this does not recreate it during navigation.
                self.viewer.attach_current_sources(source_pages)
                if self.viewer.magnifier_active or self.viewer.magnifier_selecting:
                    self.viewer.resume_magnifier_after_source_render()
        first_frame_gate = (
            self._awaiting_first_frame
            and self._first_frame_image_id is not None
        )
        request_center = (
            self.model.focused_index if first_frame_gate else self.model.current_index
        )
        gated_visible_indexes = (
            (self.model.focused_index,)
            if first_frame_gate
            else self._visible_page_indexes
        )
        if isinstance(self.image_cache.source, PdfImageSource):
            self._cancel_pending_decode_demand()
            self._raster_paint_fallback_timer.stop()
            self._raster_prefetch_after_paint = None
            pdf_current_is_cold = (
                self._awaiting_first_frame
                or self.viewer.displayed_page_indexes
                != gated_visible_indexes
            )
            if pdf_current_is_cold:
                self._page_list_waiting_for_current_paint = True
                self._hold_raster_interactive_lane()
            else:
                self._release_raster_interactive_lane()
            self._prepare_deferred_pdf_prefetch(
                request_center,
                gated_visible_indexes,
            )
        else:
            self._cancel_deferred_pdf_prefetch()
            if first_frame_gate:
                self._cancel_pending_decode_demand()
                self._preload_image_source(
                    request_center,
                    gated_visible_indexes,
                    immediate_only=True,
                )
            else:
                self._queue_decode_demand(
                    request_id,
                    request_center,
                    gated_visible_indexes,
                )
        if _DISPLAY_LOG.isEnabledFor(logging.DEBUG):
            _DISPLAY_LOG.debug(
                "display unit request=%s book_epoch=%s focused=%s slots=%r gate=%s",
                self._display_unit.request_id,
                self._display_unit.generation,
                self._display_unit.focused_page_identity,
                tuple(
                    (
                        slot.side,
                        slot.page_index,
                        slot.page_identity,
                        slot.state.value,
                    )
                    for slot in self._display_unit.slots
                ),
                first_frame_gate,
            )
        self._render_spread(spread, request_id)

    def _prepare_deferred_pdf_prefetch(
        self,
        center_index: int,
        visible_indexes: tuple[int, ...],
    ) -> None:
        source = self.image_cache.source
        if not isinstance(source, PdfImageSource):
            return
        self._pdf_prefetch_timer.stop()
        direction = self._navigation_prefetch_direction(
            center_index,
            visible_indexes,
        )
        self._pdf_prefetch_source = source
        self._pdf_prefetch_generation = self.image_cache.generation
        self._pdf_prefetch_center = center_index
        self._pdf_prefetch_visible_indexes = tuple(visible_indexes)
        self._pdf_prefetch_direction = direction
        rolling_indexes = self._next_pdf_rolling_indexes(
            center_index,
            direction,
            visible_indexes,
        )
        self.image_cache.preload_around(
            center_index,
            radius=0,
            visible_indexes=visible_indexes,
            preferred_direction=(
                direction
                if self.prefetch_direction_priority_enabled
                else 0
            ),
            rolling_indexes=rolling_indexes,
            prefetch_indexes=tuple(),
        )
        self._arm_deferred_pdf_prefetch()

    def _navigation_prefetch_direction(
        self,
        center_index: int,
        visible_indexes: tuple[int, ...],
    ) -> int:
        source = self.image_cache.source
        generation = self.image_cache.generation
        if (
            source is not self._last_preload_source
            or generation != self._last_preload_generation
            or self._last_preload_center is None
        ):
            direction = 0
        else:
            delta = center_index - self._last_preload_center
            normal_step = max(1, len(visible_indexes))
            if delta == 0:
                direction = self._last_preload_direction
            elif abs(delta) <= normal_step:
                direction = 1 if delta > 0 else -1
            else:
                direction = 0
        self._last_preload_source = source
        self._last_preload_generation = generation
        self._last_preload_center = center_index
        self._last_preload_direction = direction
        return direction

    def _display_units_from(
        self,
        center_index: int,
        step: int,
        count: int,
    ) -> tuple[tuple[int, ...], ...]:
        units: list[tuple[int, ...]] = []
        start = self.model.spread_start_for_index(center_index)
        for _ in range(max(0, count)):
            next_start = (
                self.model.next_index_from(start)
                if step > 0
                else self.model.previous_index_from(start)
            )
            if next_start == start:
                break
            units.append(
                tuple(
                    slot.page_index
                    for slot in self.model.spread_at(next_start).slots
                )
            )
            start = next_start
        return tuple(units)

    def _configured_prefetch_units(
        self,
        center_index: int,
        visible_indexes: tuple[int, ...],
        *,
        forward_units: int,
        backward_units: int,
        direction: int,
    ) -> tuple[tuple[int, ...], ...]:
        if self.prefetch_direction_priority_enabled and direction < 0:
            forward_step, backward_step = -1, 1
        else:
            forward_step, backward_step = 1, -1
        forward = self._display_units_from(
            center_index,
            forward_step,
            forward_units,
        )
        backward = self._display_units_from(
            center_index,
            backward_step,
            backward_units,
        )
        if self.prefetch_direction_priority_enabled and direction != 0:
            # Keep both immediate neighbours ready before spending the
            # remaining budget on farther work in the current direction.
            # This makes an abrupt direction reversal hit the nearest
            # completed display unit instead of waiting behind all forward
            # prefetches.
            units = []
            if forward:
                units.append(forward[0])
            if backward:
                units.append(backward[0])
            units.extend(forward[1:])
            units.extend(backward[1:])
        else:
            units = list(forward + backward)
            units.sort(
                key=lambda unit: min(
                    abs(index - center_index) for index in unit
                )
            )
        visible = set(visible_indexes)
        return tuple(
            tuple(index for index in unit if index not in visible)
            for unit in units
            if any(index not in visible for index in unit)
        )

    def _configured_prefetch_indexes(
        self,
        center_index: int,
        visible_indexes: tuple[int, ...],
        *,
        forward_units: int,
        backward_units: int,
        direction: int,
    ) -> tuple[int, ...]:
        units = self._configured_prefetch_units(
            center_index,
            visible_indexes,
            forward_units=forward_units,
            backward_units=backward_units,
            direction=direction,
        )
        indexes: list[int] = []
        for unit in units:
            for index in unit:
                if index not in indexes:
                    indexes.append(index)
        return tuple(indexes)

    def _preload_image_source(
        self,
        center_index: int,
        visible_indexes: tuple[int, ...],
        *,
        immediate_only: bool = False,
    ) -> None:
        direction = self._navigation_prefetch_direction(
            center_index,
            visible_indexes,
        )
        prefetch_units = (
            tuple()
            if immediate_only
            else self._configured_prefetch_units(
                center_index,
                visible_indexes,
                forward_units=self.image_prefetch_forward_units,
                backward_units=self.image_prefetch_backward_units,
                direction=direction,
            )
        )
        prefetch_indexes = tuple(
            index for unit in prefetch_units for index in unit
        )
        if not immediate_only:
            self._start_raster_prefetch_pipeline(
                center_index,
                visible_indexes,
                prefetch_units,
            )
            return
        self._clear_raster_prefetch_pipeline()
        self.image_cache.preload_around(
            center_index,
            radius=0,
            visible_indexes=visible_indexes,
            preferred_direction=(
                direction
                if self.prefetch_direction_priority_enabled
                else 0
            ),
            prefetch_indexes=prefetch_indexes,
            prefetch_units=prefetch_units,
        )

    def _start_raster_prefetch_pipeline(
        self,
        center_index: int,
        visible_indexes: tuple[int, ...],
        prefetch_units: tuple[tuple[int, ...], ...],
    ) -> None:
        """Run one complete display unit through decode and scale at a time.

        ZipPlaFork revision 07955f5267e2fb92d6fc6e40fde2507d8fb07b3b
        uses one page worker whose job owns entry read through display-ready
        publication. NivisViewer retains separate typed decode/render tasks,
        but this dispatcher gives them the same execution semantics: the next
        unit is not admitted until the active unit has a complete prepared
        display. See docs/ZIPPLAFORK_COMPARISON.md (AGPL-3.0-or-later).
        """
        normalized_units = tuple(
            tuple(dict.fromkeys(int(index) for index in unit))
            for unit in prefetch_units
            if unit
        )
        self._raster_prefetch_plan = (
            int(self.image_cache.generation),
            int(self._active_request_id),
            int(center_index),
            tuple(int(index) for index in visible_indexes),
            normalized_units,
        )
        self._raster_prefetch_active_unit = tuple()
        if not normalized_units:
            self.image_cache.preload_around(
                center_index,
                radius=0,
                visible_indexes=visible_indexes,
                preferred_direction=(
                    self._last_preload_direction
                    if self.prefetch_direction_priority_enabled
                    else 0
                ),
                prefetch_indexes=tuple(),
                prefetch_units=tuple(),
            )
            self._raster_prefetch_plan = None
            return
        self._advance_raster_prefetch_pipeline()

    def _advance_raster_prefetch_pipeline(self) -> None:
        if self._advancing_raster_prefetch:
            return
        self._advancing_raster_prefetch = True
        try:
            plan = self._raster_prefetch_plan
            if plan is None:
                return
            (
                generation,
                request_id,
                center_index,
                visible_indexes,
                remaining_units,
            ) = plan
            if (
                self._shutdown_prepared
                or isinstance(self.image_cache.source, PdfImageSource)
                or generation != self.image_cache.generation
                or request_id != self._active_request_id
                or visible_indexes != self._visible_page_indexes
            ):
                self._clear_raster_prefetch_pipeline()
                return

            active_unit = self._raster_prefetch_active_unit
            if active_unit:
                active_spread = self.model.spread_at(
                    self.model.spread_start_for_index(active_unit[0])
                )
                if not self.viewer.prepared_display_is_ready(
                    active_spread,
                    source_generation=generation,
                    source_identity=self._prepared_source_identity(),
                ):
                    missing_sources = tuple(
                        index
                        for index in active_unit
                        if not self.image_cache.contains(index)
                    )
                    if missing_sources and any(
                        self.image_cache.has_pending_page(index)
                        for index in missing_sources
                    ):
                        return
                    if missing_sources or self.viewer.tracks_prepared_display_unit(
                        active_spread,
                        source_generation=generation,
                        source_identity=self._prepared_source_identity(),
                    ):
                        return
                    # The active unit was cancelled, rejected by the current
                    # byte budget, or reached a terminal render failure.
                    # Never wait forever and never admit farther work after an
                    # unrenderable source unit.
                    self._clear_raster_prefetch_pipeline()
                    return
                self._raster_prefetch_active_unit = tuple()

            while remaining_units:
                unit = remaining_units[0]
                remaining_units = remaining_units[1:]
                self._raster_prefetch_plan = (
                    generation,
                    request_id,
                    center_index,
                    visible_indexes,
                    remaining_units,
                )
                spread = self.model.spread_at(
                    self.model.spread_start_for_index(unit[0])
                )
                if self.viewer.prepared_display_is_ready(
                    spread,
                    source_generation=generation,
                    source_identity=self._prepared_source_identity(),
                ):
                    continue
                self._raster_prefetch_active_unit = unit
                protected_source_indexes = tuple(
                    dict.fromkeys((*visible_indexes, *unit))
                )
                self.image_cache.preload_around(
                    center_index,
                    radius=0,
                    visible_indexes=protected_source_indexes,
                    preferred_direction=(
                        self._last_preload_direction
                        if self.prefetch_direction_priority_enabled
                        else 0
                    ),
                    prefetch_indexes=unit,
                    prefetch_units=(unit,),
                )
                if any(
                    not self.image_cache.contains(index)
                    and not self.image_cache.has_pending_page(index)
                    for index in unit
                ):
                    # The combined resident-cache budget could not admit the
                    # complete display unit. Stop prefetch at this frontier.
                    self._clear_raster_prefetch_pipeline()
                    return
                # A source-cache hit can make preparation synchronous. A miss
                # will return here and pageLoaded/renderCacheChanged resumes
                # the pipeline after the unit becomes display-ready.
                self._schedule_prepared_display_prefetch()
                if not self.viewer.prepared_display_is_ready(
                    spread,
                    source_generation=generation,
                    source_identity=self._prepared_source_identity(),
                ):
                    return
                self._raster_prefetch_active_unit = tuple()

            self._clear_raster_prefetch_pipeline()
        finally:
            self._advancing_raster_prefetch = False

    def _clear_raster_prefetch_pipeline(self) -> None:
        self._raster_prefetch_plan = None
        self._raster_prefetch_active_unit = tuple()
        if self.model.total_pages:
            visible_indexes = self._visible_page_indexes or tuple(
                slot.page_index for slot in self.model.spread_at().slots
            )
            # Active-unit source protection is temporary. ZipPlaFork drops a
            # page's source and display artifact under the same terminal
            # lifecycle; keeping the last admitted source protected after our
            # split pipeline finishes would strand a far full-size raster.
            self.image_cache.retain_visible_only(
                self.model.focused_index,
                visible_indexes,
            )

    def _queue_decode_demand(
        self,
        request_id: int,
        center_index: int,
        visible_indexes: tuple[int, ...],
    ) -> None:
        self._clear_raster_prefetch_pipeline()
        staged = self._raster_prefetch_after_paint
        if (
            staged is not None
            and (
                staged[0] != self.image_cache.generation
                or staged[2] != tuple(visible_indexes)
            )
        ):
            self._raster_paint_fallback_timer.stop()
            self._raster_prefetch_after_paint = None
            self._release_raster_interactive_lane()
        self._pending_decode_demand = (
            int(request_id),
            int(self.image_cache.generation),
            int(center_index),
            tuple(int(index) for index in visible_indexes),
        )
        if (
            not self.viewer.displayed_page_indexes
            or self._awaiting_first_frame
        ):
            self._decode_demand_timer.stop()
            self._apply_pending_decode_demand()
            return
        # Raster decode and archive reads begin only after the input stream has
        # been idle briefly. Repeated wheel events replace this demand, so
        # pages crossed during a rapid gesture never enter the worker queue.
        self._decode_demand_timer.start()

    def _apply_pending_decode_demand(self) -> None:
        demand = self._pending_decode_demand
        self._pending_decode_demand = None
        if demand is None or self._shutdown_prepared:
            return
        request_id, generation, center_index, visible_indexes = demand
        if (
            request_id != self._active_request_id
            or request_id != self._display_unit.request_id
            or generation != self.image_cache.generation
            or visible_indexes != self._visible_page_indexes
        ):
            return
        staged = self._raster_prefetch_after_paint
        current_stage = (
            staged is not None
            and staged[0] == generation
            and staged[2] == visible_indexes
        )
        target_already_displayed = (
            self.viewer.displayed_page_indexes == visible_indexes
        )
        cold_current = current_stage or not target_already_displayed
        if cold_current:
            # ZipPlaFork's page worker completes decode -> resize -> publish
            # before it advances to the next queued page. NivisViewer has
            # separate decode and display-preparation stages, so hold every
            # non-visible prefetch until this unit has actually painted.
            self._raster_prefetch_after_paint = (
                generation,
                center_index,
                visible_indexes,
            )
            self._hold_raster_interactive_lane()
        else:
            self._release_raster_interactive_lane()
        self._preload_image_source(
            center_index,
            visible_indexes,
            immediate_only=cold_current,
        )

    def _cancel_pending_decode_demand(self) -> None:
        self._decode_demand_timer.stop()
        self._pending_decode_demand = None
        self._clear_raster_prefetch_pipeline()

    def _hold_raster_interactive_lane(self) -> None:
        self._zip_runtime_browser_resume_timer.stop()
        self._set_page_list_paused(True)
        if (
            self._raster_interactive_lane_held
            or self.image_work_coordinator is None
        ):
            return
        self._raster_interactive_lane_held = True
        self.image_work_coordinator.begin_viewer_interactive()

    def _release_raster_interactive_lane(
        self,
        *,
        resume_page_list: bool = True,
    ) -> None:
        self._zip_runtime_browser_resume_timer.stop()
        self._page_list_waiting_for_current_paint = False
        self._set_page_list_paused(not resume_page_list)
        if (
            not self._raster_interactive_lane_held
            or self.image_work_coordinator is None
        ):
            return
        self._raster_interactive_lane_held = False
        self.image_work_coordinator.end_viewer_interactive()

    def _defer_zip_runtime_browser_resume(self) -> None:
        if (
            self._raster_interactive_lane_held
            and self.image_work_coordinator is not None
        ):
            self._zip_runtime_browser_resume_timer.start()

    def _release_raster_prefetch_without_paint(self) -> None:
        if self._zip_runtime_active:
            # Hidden/minimized widgets may never acknowledge a paint.  The
            # existing bounded fallback still commits persistence/UI
            # projections, but never before the normal paint opportunity.
            if self._pending_presentation_side_effect_token is not None:
                self._presentation_side_effect_timer.start()
            if self._zip_runtime is not None:
                self._zip_runtime.release_prefetch(
                    request_id=self._active_request_id,
                )
            self._release_raster_interactive_lane()
            return
        if self._page_list_waiting_for_current_paint:
            self._release_raster_interactive_lane()
            return
        staged = self._raster_prefetch_after_paint
        if staged is None:
            return
        self._raster_prefetch_after_paint = None
        self._release_raster_interactive_lane()
        if (
            self._shutdown_prepared
            or isinstance(self.image_cache.source, PdfImageSource)
            or staged[0] != self.image_cache.generation
            or staged[2] != self.viewer.displayed_page_indexes
        ):
            return
        self._preload_image_source(staged[1], staged[2])

    def _next_pdf_rolling_indexes(
        self,
        center_index: int,
        direction: int,
        visible_indexes: tuple[int, ...],
    ) -> tuple[int, ...]:
        if (
            not self.prefetch_direction_priority_enabled
            or self.pdf_prefetch_forward_units <= 0
        ):
            return tuple()
        if direction > 0:
            target_start = self.model.next_index_from(center_index)
        elif direction < 0:
            target_start = self.model.previous_index_from(center_index)
        else:
            candidates: list[tuple[int, int, int]] = []
            next_start = self.model.next_index_from(center_index)
            if next_start != center_index:
                candidates.append((abs(next_start - center_index), 0, next_start))
            previous_start = self.model.previous_index_from(center_index)
            if previous_start != center_index:
                candidates.append(
                    (abs(previous_start - center_index), 1, previous_start)
                )
            if not candidates:
                return tuple()
            target_start = min(candidates)[2]
        if target_start == center_index:
            return tuple()
        visible = set(visible_indexes)
        return tuple(
            slot.page_index
            for slot in self.model.spread_at(target_start).slots
            if slot.page_index not in visible
        )

    def _arm_deferred_pdf_prefetch(self) -> None:
        if (
            self._shutdown_prepared
            or (
                self.pdf_prefetch_forward_units <= 0
                and self.pdf_prefetch_backward_units <= 0
            )
            or self._pdf_prefetch_timer.isActive()
            or self.image_cache.source is not self._pdf_prefetch_source
            or self.image_cache.generation != self._pdf_prefetch_generation
        ):
            return
        if any(
            self.image_cache.get(index) is None
            for index in self._pdf_prefetch_visible_indexes
        ):
            return
        if not self._configured_prefetch_indexes(
            self._pdf_prefetch_center,
            self._pdf_prefetch_visible_indexes,
            forward_units=self.pdf_prefetch_forward_units,
            backward_units=self.pdf_prefetch_backward_units,
            direction=self._pdf_prefetch_direction,
        ):
            return
        self._pdf_prefetch_timer.start()

    def _start_deferred_pdf_prefetch(self) -> None:
        source = self._pdf_prefetch_source
        center_index = self._pdf_prefetch_center
        if (
            self._shutdown_prepared
            or source is None
            or self.image_cache.source is not source
            or self.image_cache.generation != self._pdf_prefetch_generation
            or center_index
            not in {self.model.current_index, self.model.focused_index}
        ):
            return
        prefetch_units = self._configured_prefetch_units(
            center_index,
            self._pdf_prefetch_visible_indexes,
            forward_units=self.pdf_prefetch_forward_units,
            backward_units=self.pdf_prefetch_backward_units,
            direction=self._pdf_prefetch_direction,
        )
        self.image_cache.preload_around(
            center_index,
            radius=0,
            visible_indexes=self._pdf_prefetch_visible_indexes,
            preferred_direction=(
                self._pdf_prefetch_direction
                if self.prefetch_direction_priority_enabled
                else 0
            ),
            prefetch_indexes=tuple(
                index for unit in prefetch_units for index in unit
            ),
            prefetch_units=prefetch_units,
        )

    def _cancel_deferred_pdf_prefetch(self) -> None:
        self._pdf_prefetch_timer.stop()
        self._pdf_prefetch_source = None
        self._pdf_prefetch_generation = -1
        self._pdf_prefetch_visible_indexes = tuple()
        self._pdf_prefetch_direction = 0

    def _render_spread(self, spread, request_id: int) -> None:
        presentation_token = self._presentation_token_for_request(request_id)
        if (
            self._shutdown_prepared
            or presentation_token is None
            or request_id != self._active_request_id
            or self._display_unit.request_id != request_id
            or tuple(
                (slot.page_index, slot.image_id) for slot in spread.slots
            )
            != tuple(
                (slot.page_index, slot.image_id)
                for slot in self._display_unit.slots
            )
        ):
            return

        pages: list[ViewerImage] = []
        target_is_terminal = True
        for slot in spread.slots:
            cached = self.image_cache.get(slot.page_index)
            if cached is None:
                state = (
                    ViewerSlotState.LOADING
                    if self.image_cache.source is not None
                    else ViewerSlotState.FAILED
                )
                self.presentation_state.transition_slot(
                    presentation_token,
                    page_index=slot.page_index,
                    image_id=slot.image_id,
                    state=state,
                    error=(
                        None
                        if state is ViewerSlotState.LOADING
                        else tr('画像ソースを利用できません。')
                    ),
                )
                if state is ViewerSlotState.LOADING:
                    target_is_terminal = False
                else:
                    pages.append(
                        ViewerWidget.error_page(
                            slot.page_index,
                            slot.image_id,
                            tr('画像ソースを利用できません。'),
                        )
                    )
            elif cached.error:
                self.presentation_state.transition_slot(
                    presentation_token,
                    page_index=slot.page_index,
                    image_id=slot.image_id,
                    state=ViewerSlotState.FAILED,
                    error=cached.error,
                )
                pages.append(
                    ViewerWidget.error_page(
                        slot.page_index,
                        slot.image_id,
                        cached.error,
                    )
                )
            elif cached.qimage is not None and cached.original_size is not None:
                self.presentation_state.transition_slot(
                    presentation_token,
                    page_index=slot.page_index,
                    image_id=slot.image_id,
                    state=ViewerSlotState.READY,
                )
                pages.extend(self._viewer_images_for_cached(cached, split_allowed=spread.is_single))
            else:
                self.presentation_state.transition_slot(
                    presentation_token,
                    page_index=slot.page_index,
                    image_id=slot.image_id,
                    state=ViewerSlotState.FAILED,
                    error=tr('画像を表示できません。'),
                )
                pages.append(ViewerWidget.error_page(slot.page_index, slot.image_id, tr('画像を表示できません。')))

        # A navigation target replaces the canvas only after every slot has
        # reached a terminal state. Until then the last complete frame stays
        # owned by ViewerWidget.
        should_apply = (
            target_is_terminal
            and request_id != self._applied_display_request_id
        )
        if should_apply:
            self._queue_display_demand(request_id, spread, pages)
        elif request_id == self._applied_display_request_id:
            if target_is_terminal:
                # A prepared unit can remain displayable after its decoded
                # source was evicted. Reattach the implicitly shared source
                # when it becomes available again without recreating pixmaps.
                self.viewer.attach_current_sources(pages)
            self._arm_prepared_display_prefetch()

    def _queue_display_demand(
        self,
        request_id: int,
        spread: DisplaySpread,
        pages: list[ViewerImage],
    ) -> None:
        presentation_token = self._presentation_token_for_request(request_id)
        if presentation_token is None:
            return
        demand = (
            int(request_id),
            int(self.image_cache.generation),
            spread,
            tuple(pages),
            presentation_token,
        )
        existing = self._pending_display_demand
        self._pending_display_demand = demand
        staged = self._raster_prefetch_after_paint
        current_cold_stage = (
            staged is not None
            and staged[0] == self.image_cache.generation
            and staged[2]
            == tuple(slot.page_index for slot in spread.slots)
        )
        if (
            not self.viewer.displayed_page_indexes
            or self._awaiting_first_frame
            or current_cold_stage
        ):
            self._display_demand_timer.stop()
            self._apply_pending_display_demand()
            return
        if (
            existing is None
            or existing[0] != demand[0]
            or existing[1] != demand[1]
        ):
            # A cold target replaces the previous queued target. Heavy native
            # scaling starts only after a short input-idle boundary so rapid
            # wheel events can coalesce without contending with the GUI thread.
            self._display_demand_timer.start()

    def _apply_pending_display_demand(self) -> None:
        demand = self._pending_display_demand
        self._pending_display_demand = None
        if demand is None or self._shutdown_prepared:
            return
        request_id, generation, spread, pages, presentation_token = demand
        if (
            request_id != self._active_request_id
            or request_id != self._display_unit.request_id
            or generation != self.image_cache.generation
            or self._presentation_token_for_request(request_id)
            != presentation_token
            or tuple(
                (slot.page_index, slot.image_id) for slot in spread.slots
            )
            != tuple(
                (slot.page_index, slot.image_id)
                for slot in self._display_unit.slots
            )
        ):
            return
        # Starting the target-size worker is intentionally outside the input
        # handler. The previous complete frame remains visible until this
        # request commits atomically.
        self.viewer.set_pages(
            spread,
            list(pages),
            frame_token=presentation_token,
        )

    def _cancel_pending_display_demand(self) -> None:
        self._display_demand_timer.stop()
        self._pending_display_demand = None

    def _viewer_images_for_cached(
        self,
        cached: CachedImage,
        *,
        split_allowed: bool,
        create_pixmap: bool | None = None,
    ) -> list[ViewerImage]:
        if cached.qimage is None or cached.original_size is None:
            return [ViewerWidget.error_page(cached.page_index, cached.image_id, tr('画像を表示できません。'))]

        if create_pixmap is None:
            # QPixmap is a GUI-thread display artifact.  All normal modes,
            # including standard, prepare and cache it before navigation.
            create_pixmap = False
        source_identity = self._prepared_source_identity()
        width, height = cached.original_size
        rendered_rotation = int(cached.rendered_rotation) % 360
        split_axis_pixels = (
            cached.qimage.height()
            if rendered_rotation in {90, 270}
            else cached.qimage.width()
        )
        should_split = (
            self.split_wide_image
            and split_allowed
            and height > 0
            and width / height >= 1.25
            and width >= 2
            and split_axis_pixels >= 2
        )
        if not should_split:
            return [
                ViewerWidget.from_qimage(
                    cached.page_index,
                    cached.image_id,
                    cached.qimage,
                    cached.original_size,
                    cached.rendered_size,
                    bool(cached.rendered_rotation),
                    create_pixmap,
                    cached.generation,
                    source_identity,
                    source_is_preview=cached.source_is_preview,
                )
            ]

        left_width = width // 2
        right_width = width - left_width
        left_pixel_span = max(
            1,
            min(
                split_axis_pixels - 1,
                round(split_axis_pixels * left_width / width),
            ),
        )
        if rendered_rotation == 90:
            left_split = (
                0,
                0,
                cached.qimage.width(),
                left_pixel_span,
            )
            right_split = (
                0,
                left_pixel_span,
                cached.qimage.width(),
                cached.qimage.height() - left_pixel_span,
            )
        elif rendered_rotation == 180:
            left_split = (
                cached.qimage.width() - left_pixel_span,
                0,
                left_pixel_span,
                cached.qimage.height(),
            )
            right_split = (
                0,
                0,
                cached.qimage.width() - left_pixel_span,
                cached.qimage.height(),
            )
        elif rendered_rotation == 270:
            left_split = (
                0,
                cached.qimage.height() - left_pixel_span,
                cached.qimage.width(),
                left_pixel_span,
            )
            right_split = (
                0,
                0,
                cached.qimage.width(),
                cached.qimage.height() - left_pixel_span,
            )
        else:
            left_split = (
                0,
                0,
                left_pixel_span,
                cached.qimage.height(),
            )
            right_split = (
                left_pixel_span,
                0,
                cached.qimage.width() - left_pixel_span,
                cached.qimage.height(),
            )
        left_page = ViewerWidget.from_qimage(
            cached.page_index,
            f"{cached.image_id}#left",
            cached.qimage,
            (left_width, height),
            rendered_size=(left_split[2], left_split[3]),
            pre_rotated=bool(rendered_rotation),
            create_pixmap=create_pixmap,
            source_generation=cached.generation,
            source_identity=source_identity,
            split_range=left_split,
            source_is_preview=cached.source_is_preview,
        )
        right_page = ViewerWidget.from_qimage(
            cached.page_index,
            f"{cached.image_id}#right",
            cached.qimage,
            (right_width, height),
            rendered_size=(right_split[2], right_split[3]),
            pre_rotated=bool(rendered_rotation),
            create_pixmap=create_pixmap,
            source_generation=cached.generation,
            source_identity=source_identity,
            split_range=right_split,
            source_is_preview=cached.source_is_preview,
        )
        if self.reading_direction == "rtl":
            return [right_page, left_page]
        return [left_page, right_page]

    def _on_cache_page_loaded(self, cached: CachedImage) -> None:
        requested = self.presentation_state.requested
        if (
            self._shutdown_prepared
            or self._zip_runtime_active
            or requested is None
            or cached.generation != self.image_cache.generation
        ):
            return
        self._enforce_combined_cache_budget()
        self.presentation_state.transition_slot(
            requested.token,
            page_index=cached.page_index,
            image_id=cached.image_id,
            state=(
                ViewerSlotState.FAILED
                if cached.error
                or cached.qimage is None
                or cached.original_size is None
                else ViewerSlotState.READY
            ),
            error=cached.error,
        )
        first_frame_result = (
            self._awaiting_first_frame
            and cached.image_id == self._first_frame_image_id
        )
        first_frame_failed = (
            bool(cached.error)
            or cached.qimage is None
            or cached.original_size is None
        )
        if first_frame_result and first_frame_failed:
            self._cancel_interactive_open()
        performance_trace.mark(
            self._active_open_trace_id,
            "viewer.result.arrived",
            f"page={cached.page_index}",
        )
        repositioned = self.model.set_image_size(
            cached.page_index,
            cached.original_size,
        )
        spread = self.model.spread_at()
        slot_identity_changed = tuple(
            (slot.page_index, slot.image_id) for slot in spread.slots
        ) != tuple(
            (slot.page_index, slot.image_id) for slot in self._display_unit.slots
        )
        if repositioned or slot_identity_changed:
            self._refresh_view()
            if first_frame_result:
                if isinstance(self.image_cache.source, PdfImageSource):
                    self._prepare_deferred_pdf_prefetch(
                        self.model.focused_index,
                        self._visible_page_indexes,
                    )
                else:
                    self._preload_image_source(
                        self.model.focused_index,
                        self._visible_page_indexes,
                        immediate_only=True,
                    )
            return
        if cached.page_index not in self._visible_page_indexes:
            if cached.page_index in self._raster_prefetch_active_unit:
                # The paced raster dispatcher has no farther unit admitted.
                # Continue this unit directly into display preparation rather
                # than paying the generic background-idle delay between the
                # decode and scale stages.
                self._prepared_display_timer.stop()
                self._schedule_prepared_display_prefetch()
            else:
                self._arm_prepared_display_prefetch()
            return
        if first_frame_result:
            # The logical current page has completed decoding, so the reserved
            # Viewer lane may now continue with its spread partner. Nearby PDF
            # pages wait for the idle grace; Browser work remains gated until
            # contentPainted confirms that the first frame reached the screen.
            if isinstance(self.image_cache.source, PdfImageSource):
                self._prepare_deferred_pdf_prefetch(
                    self.model.focused_index,
                    self._visible_page_indexes,
                )
            else:
                self._preload_image_source(
                    self.model.focused_index,
                    self._visible_page_indexes,
                    immediate_only=True,
                )
        if cached.page_index in self._visible_page_indexes:
            self._arm_deferred_pdf_prefetch()
        self._render_spread(self.model.spread_at(), self._active_request_id)
        if not cached.source_is_preview:
            self.viewer.resume_magnifier_after_source_render()

    def _on_prepared_display_committed(
        self,
        _image_ids: object,
    ) -> None:
        if self._zip_runtime_active:
            return
        if self.viewer.magnifier_active or self.viewer.magnifier_selecting:
            self.viewer.resume_magnifier_after_source_render()
        # Native QMenu reconstruction is deferred until the user opens it;
        # this slot is delivered synchronously during ready-page navigation.
        self._arm_prepared_display_prefetch()
        if self._page_list_waiting_for_current_paint:
            # Decode/render has finished.  A visible window normally releases
            # PageList work on contentPainted; the bounded fallback covers a
            # hidden/minimized/error-only surface without overlapping the
            # expensive current-frame operation itself.
            self._raster_paint_fallback_timer.start()
        staged = self._raster_prefetch_after_paint
        if (
            staged is not None
            and staged[0] == self.image_cache.generation
            and staged[2] == self.viewer.displayed_page_indexes
        ):
            # Visible windows normally clear this on the next paint. The
            # bounded fallback prevents a hidden/minimized/error-only frame
            # from leaving Browser work paused indefinitely.
            self._raster_paint_fallback_timer.start()

    def _arm_prepared_display_prefetch(
        self,
        *,
        after_paint: bool = False,
    ) -> None:
        if self._shutdown_prepared:
            self._prepared_display_timer.stop()
            return
        self._prepared_display_timer.start(
            0 if after_paint else _PREPARED_DISPLAY_IDLE_GRACE_MS
        )

    def _cached_pages_for_prepared_spread(
        self,
        spread: DisplaySpread,
        *,
        include_tracked: bool = False,
    ) -> list[ViewerImage] | None:
        if not include_tracked and self.viewer.tracks_prepared_display_unit(
            spread,
            source_generation=self.image_cache.generation,
            source_identity=self._prepared_source_identity(),
        ):
            return None
        pages: list[ViewerImage] = []
        for slot in spread.slots:
            cached = self.image_cache.get(slot.page_index)
            if (
                cached is None
                or cached.generation != self.image_cache.generation
            ):
                return None
            if cached.error:
                pages.append(
                    ViewerWidget.error_page(
                        slot.page_index,
                        slot.image_id,
                        cached.error,
                    )
                )
                continue
            if cached.qimage is None or cached.original_size is None:
                return None
            pages.extend(
                self._viewer_images_for_cached(
                    cached,
                    split_allowed=spread.is_single,
                    create_pixmap=False,
                )
            )
        return pages

    def _prepared_source_identity(self) -> str:
        return (
            str(self.model.source.source_path)
            if self.model.source is not None
            else ""
        )

    def _schedule_prepared_display_prefetch(self) -> None:
        if (
            self._shutdown_prepared
            or not self.model.total_pages
            or self._pending_display_demand is not None
        ):
            if self._shutdown_prepared or not self.model.total_pages:
                self.viewer.prepare_display_units(())
            return

        current = self.model.spread_at()
        current_start = current.start_index
        direction = (
            self._last_preload_direction
            if self.prefetch_direction_priority_enabled
            else 1
        )
        if direction == 0:
            direction = 1
        if isinstance(self.image_cache.source, PdfImageSource):
            forward_count = self.pdf_prefetch_forward_units
            backward_count = self.pdf_prefetch_backward_units
        else:
            forward_count = self.image_prefetch_forward_units
            backward_count = self.image_prefetch_backward_units
        forward_units = self._display_units_from(
            current_start,
            1 if direction > 0 else -1,
            forward_count,
        )
        backward_units = self._display_units_from(
            current_start,
            -1 if direction > 0 else 1,
            backward_count,
        )
        forward_starts = [min(unit) for unit in forward_units if unit]
        backward_starts = [min(unit) for unit in backward_units if unit]
        ordered_starts = [current_start]
        if forward_starts:
            ordered_starts.append(forward_starts[0])
        if backward_starts:
            ordered_starts.append(backward_starts[0])
        ordered_starts.extend(forward_starts[1:])
        ordered_starts.extend(backward_starts[1:])
        seen: set[int] = set()
        plan: list[
            tuple[
                int,
                DisplaySpread,
                list[ViewerImage] | None,
                bool,
            ]
        ] = []
        for priority, start in enumerate(ordered_starts):
            if start in seen:
                continue
            seen.add(start)
            spread = self.model.spread_at(start)
            pages = self._cached_pages_for_prepared_spread(spread)
            active_unit = set(self._raster_prefetch_active_unit)
            protected = (
                start == current_start
                or (forward_starts and start == forward_starts[0])
                or (backward_starts and start == backward_starts[0])
                or (
                    bool(active_unit)
                    and active_unit
                    == {slot.page_index for slot in spread.slots}
                )
            )
            plan.append((priority, spread, pages, protected))
        self.viewer.prepare_display_units(plan)
        self._advance_raster_prefetch_pipeline()

    def _on_viewer_render_cache_changed(self) -> None:
        if self._zip_runtime_active:
            return
        self._advance_raster_prefetch_pipeline()
        self._enforce_combined_cache_budget()
        self._advance_raster_prefetch_pipeline()

    def _on_viewer_render_work_finished(
        self,
        render_key: object,
        succeeded: bool,
    ) -> None:
        if self._zip_runtime_active:
            return
        if succeeded or not self._raster_prefetch_active_unit:
            return
        image_id = str(getattr(render_key, "image_id", ""))
        active_ids = tuple(
            self.model.image_id_at(index)
            for index in self._raster_prefetch_active_unit
        )
        if not any(
            expected is not None
            and (
                image_id == expected
                or image_id.startswith(f"{expected}#")
            )
            for expected in active_ids
        ):
            return
        self._clear_raster_prefetch_pipeline()
        self._enforce_combined_cache_budget()

    def _begin_interactive_open(self) -> None:
        if self._awaiting_first_frame:
            self.interactive_open_cancelled.emit(self)
        self._raster_prefetch_after_paint = None
        self._raster_paint_fallback_timer.stop()
        self._clear_raster_prefetch_pipeline()
        self._release_raster_interactive_lane(resume_page_list=False)
        self._awaiting_first_frame = True
        self._first_frame_image_id = None
        self.interactive_open_started.emit(self)

    def _cancel_interactive_open(self) -> None:
        if not self._awaiting_first_frame:
            return
        self._awaiting_first_frame = False
        self._first_frame_image_id = None
        self._release_raster_interactive_lane()
        self.interactive_open_cancelled.emit(self)

    def _on_viewer_content_painted(self, image_ids: object) -> None:
        if self._zip_runtime_active:
            if (
                self._awaiting_first_frame
                and self._first_frame_image_id
                and isinstance(image_ids, tuple)
                and any(
                    image_id == self._first_frame_image_id
                    or str(image_id).startswith(
                        f"{self._first_frame_image_id}#"
                    )
                    for image_id in image_ids
                )
            ):
                self._awaiting_first_frame = False
                self._first_frame_image_id = None
                performance_trace.mark(
                    self._active_open_trace_id,
                    "viewer.first_paint.completed",
                )
                self._set_page_list_paused(False)
                self.first_frame_ready.emit(self)
            return
        if isinstance(image_ids, tuple):
            if (
                self._page_list_waiting_for_current_paint
                and isinstance(self.image_cache.source, PdfImageSource)
                and self.viewer.displayed_page_indexes
                == self._visible_page_indexes
            ):
                self._raster_paint_fallback_timer.stop()
                self._release_raster_interactive_lane()
            self._arm_prepared_display_prefetch(after_paint=True)
            self._arm_deferred_pdf_prefetch()
            staged = self._raster_prefetch_after_paint
            if (
                staged is not None
                and not isinstance(
                    self.image_cache.source,
                    PdfImageSource,
                )
                and staged[0] == self.image_cache.generation
                and staged[2] == self.viewer.displayed_page_indexes
            ):
                self._raster_paint_fallback_timer.stop()
                self._raster_prefetch_after_paint = None
                self._release_raster_interactive_lane()
                self._preload_image_source(staged[1], staged[2])
        if (
            not self._awaiting_first_frame
            or not self._first_frame_image_id
            or not isinstance(image_ids, tuple)
        ):
            return
        expected = self._first_frame_image_id
        if not any(
            image_id == expected or str(image_id).startswith(f"{expected}#")
            for image_id in image_ids
        ):
            return
        self._awaiting_first_frame = False
        self._first_frame_image_id = None
        self._set_page_list_paused(False)
        performance_trace.mark(
            self._active_open_trace_id,
            "viewer.first_paint.completed",
        )
        if isinstance(self.image_cache.source, PdfImageSource):
            self._arm_deferred_pdf_prefetch()
        else:
            self._preload_image_source(
                self.model.focused_index,
                self._visible_page_indexes,
            )
        self.first_frame_ready.emit(self)

    def set_next_open_trace(self, trace_id: int) -> None:
        self._next_open_trace_id = int(trace_id)

    def _on_left_side_clicked(self) -> None:
        self._move_from_canvas_side("left")

    def _on_right_side_clicked(self) -> None:
        self._move_from_canvas_side("right")

    def _move_from_canvas_side(self, side: str) -> None:
        action = self.viewer_canvas_left_click_action
        if action == "none":
            return
        direction = self.viewer_canvas_click_direction
        if direction == "auto":
            # Resolve at the confirmed click, from the same effective Viewer
            # direction used by the spread and slider; never from UI locale.
            direction = "left_next" if self.reading_direction == "rtl" else "right_next"
        next_side = "right" if direction == "right_next" else "left"
        forward = side == next_side
        if action == commands.NEXT_SINGLE_PAGE:
            if forward:
                self.page_navigation.next_single_page()
            else:
                self.page_navigation.previous_single_page()
        elif action == commands.NEXT_DISPLAY_UNIT:
            if forward:
                self.next_page()
            else:
                self.previous_page()

    def _canvas_context_token(self) -> tuple[int, int, str | None]:
        return (
            id(self.book_session),
            self.book_session.generation,
            self.model.focused_page_identity,
        )

    def _canvas_click_allowed(self, global_position: QPoint) -> bool:
        if self._shutdown_prepared or self._drop_active:
            return False
        if self.viewer.gesture_in_progress:
            return False
        popup = QApplication.activePopupWidget()
        modal = QApplication.activeModalWidget()
        if (
            popup is not None
            and (popup is self or self.isAncestorOf(popup))
        ) or (
            modal is not None
            and (modal is self or self.isAncestorOf(modal))
        ):
            return False
        if self.fullscreen_chrome.fullscreen:
            if self.fullscreen_chrome.is_edge_trigger(global_position):
                return False
            for overlay in (
                self.fullscreen_chrome.top_overlay,
                self.fullscreen_chrome.bottom_overlay,
                self.fullscreen_chrome.bottom_reveal_strip,
            ):
                if (
                    overlay.isVisible()
                    and overlay.rect().contains(
                        overlay.mapFromGlobal(global_position)
                    )
                ):
                    return False
        return self.viewer.rect().contains(
            self.viewer.mapFromGlobal(global_position)
        )

    def _canvas_press_flags(self, global_position: QPoint) -> dict[str, bool]:
        fullscreen = self.fullscreen_chrome.fullscreen
        edge_trigger = (
            fullscreen
            and self.fullscreen_chrome.is_edge_trigger(global_position)
        )
        overlay = False
        if fullscreen:
            overlay = any(
                widget.isVisible()
                and widget.rect().contains(
                    widget.mapFromGlobal(global_position)
                )
                for widget in (
                    self.fullscreen_chrome.top_overlay,
                    self.fullscreen_chrome.bottom_overlay,
                    self.fullscreen_chrome.bottom_reveal_strip,
                )
            )
        return {
            "fullscreen": fullscreen,
            "overlay": overlay,
            "edge_trigger": edge_trigger,
            "mouse_gesture": self.viewer.gesture_in_progress,
            "drop_active": self._drop_active,
        }

    def dispatch_command(self, command: str) -> bool:
        normalized = commands.normalize_viewer_command(command)
        handlers: dict[str, Callable[[], None]] = {
            commands.PREVIOUS_PAGE: self.previous_page,
            commands.NEXT_PAGE: self.next_page,
            commands.PREVIOUS_DISPLAY_UNIT: self.previous_page,
            commands.NEXT_DISPLAY_UNIT: self.next_page,
            commands.PREVIOUS_SINGLE_PAGE: self.previous_one_page,
            commands.NEXT_SINGLE_PAGE: self.next_one_page,
            commands.FIRST_PAGE: self.first_page,
            commands.LAST_PAGE: self.last_page,
            commands.PREVIOUS_BOOK: self.open_previous_book,
            commands.NEXT_BOOK: self.open_next_book,
            commands.TOGGLE_FULLSCREEN: self.toggle_fullscreen,
            commands.CLOSE_VIEWER: self.request_close,
            commands.TOGGLE_SPREAD: self.toggle_view_mode,
            commands.TOGGLE_READING_DIRECTION: self.toggle_reading_direction,
            commands.FIT_WINDOW: lambda: self.set_fit_mode("fit_window"),
            commands.ZOOM_IN: self.zoom_in,
            commands.ZOOM_OUT: self.zoom_out,
        }
        handler = handlers.get(normalized)
        if handler is None:
            return False
        handler()
        return True

    def _on_mouse_gesture(self, pattern: str) -> None:
        command = self.mouse_gesture_bindings.get(pattern, "")
        self.dispatch_command(command)

    def _on_extra_mouse_button(self, button: str) -> None:
        command = (
            self.mouse_back_button_action if button == "back"
            else self.mouse_forward_button_action if button == "forward" else ""
        )
        if (
            bool(self.settings.get("mouse_side_buttons_folder_navigation", False))
            and isinstance(self.book_session.source, FolderImageSource)
            and command in {commands.PREVIOUS_BOOK, commands.NEXT_BOOK}
        ):
            self.side_folder_requested.emit(self, -1 if command == commands.PREVIOUS_BOOK else 1)
            return
        if button == "back":
            if self.mouse_back_button_action == commands.PREVIOUS_BOOK:
                self._open_adjacent_book(-1, require_browser_snapshot=True)
                return
            self.dispatch_command(self.mouse_back_button_action)
        elif button == "forward":
            if self.mouse_forward_button_action == commands.NEXT_BOOK:
                self._open_adjacent_book(1, require_browser_snapshot=True)
                return
            self.dispatch_command(self.mouse_forward_button_action)

    def set_close_request_handler(
        self,
        handler: Callable[[object], object] | None,
    ) -> None:
        """Let a host replace a Viewer close with a navigation request."""
        self._close_request_handler = handler

    def request_close(self) -> bool:
        handler = self._close_request_handler
        if callable(handler):
            result = handler(self)
            return True if result is None else bool(result)
        return bool(self.close())

    def _viewer_close_key_is_eligible(self, watched: object) -> bool:
        if not isinstance(watched, QWidget) or watched.window() is not self:
            return False
        application = QApplication.instance()
        if application is not None and (
            application.activeModalWidget() is not None
            or application.activePopupWidget() is not None
        ):
            return False
        focus = application.focusWidget() if application is not None else None
        if focus is None or focus.window() is not self:
            return False
        current = focus
        while current is not self:
            if isinstance(
                current,
                (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QDialog),
            ):
                return False
            if isinstance(current, QComboBox) and current.isEditable():
                return False
            current = current.parentWidget()
            if current is None:
                return False
        return True

    def _handle_viewer_close_key_event(
        self,
        watched: object,
        event: QEvent,
    ) -> bool:
        if (
            event.type()
            not in {
                QEvent.Type.ShortcutOverride,
                QEvent.Type.KeyPress,
            }
            or not isinstance(event, QKeyEvent)
            or not self._viewer_close_key_combinations
            or key_event_combined(event) not in self._viewer_close_key_combinations
            or not isinstance(watched, QWidget)
            or watched.window() is not self
        ):
            return False
        # The close QAction carries both aliases for menu discoverability. If
        # an editor owns the focus, consume only ShortcutOverride so that the
        # QAction cannot bypass the editor-safety rule while the native
        # KeyPress still reaches the editor.
        if not self._viewer_close_key_is_eligible(watched):
            if event.type() == QEvent.Type.ShortcutOverride:
                event.accept()
                return True
            # Let the editor receive its native KeyPress (Ctrl+A, Backspace,
            # printable input, copy/paste, and similar editing commands).
            return False
        event.accept()
        if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
            self.request_close()
        return True

    def _handle_escape(self) -> None:
        self.viewer.cancel_pending_canvas_click()
        if self.viewer.cancel_magnifier():
            return
        if self.viewer.cancel_mouse_gesture():
            return
        self.exit_fullscreen()

    def _show_viewer_context_menu(self, position) -> None:
        menu = QMenu(self)
        back_history_action = menu.addAction(tr('表示履歴を戻る'))
        forward_history_action = menu.addAction(tr('表示履歴を進む'))
        back_history_action.setEnabled(
            bool(self.presentation_state.back_history)
        )
        forward_history_action.setEnabled(
            bool(self.presentation_state.forward_history)
        )
        menu.addSeparator()
        next_action = menu.addAction(tr('次ページ'))
        previous_action = menu.addAction(tr('前ページ'))
        menu.addSeparator()
        bookmark_action = menu.addAction(tr('現在ページをブックマーク'))
        copy_path_action = menu.addAction(tr('パスをコピー'))
        copy_image_action = menu.addAction(tr('画像をコピー'))
        copy_view_action = menu.addAction(tr('表示をコピー'))
        page_info_action = menu.addAction(tr('ページ情報'))
        open_location_action = menu.addAction(tr('場所を開く'))
        menu.addSeparator()
        fullscreen_action = menu.addAction(tr('全画面切替'))

        selected = menu.exec(self.viewer.mapToGlobal(position))
        if selected == back_history_action:
            self.go_back_in_page_history()
        elif selected == forward_history_action:
            self.go_forward_in_page_history()
        elif selected == next_action:
            self.next_page_or_scroll()
        elif selected == previous_action:
            self.previous_page_or_scroll()
        elif selected == bookmark_action:
            self.toggle_current_bookmark()
        elif selected == copy_path_action:
            self.copy_current_image_path()
        elif selected == copy_image_action:
            self.copy_current_image()
        elif selected == copy_view_action:
            self.copy_current_view()
        elif selected == page_info_action:
            self.show_page_info()
        elif selected == open_location_action:
            self.open_current_location()
        elif selected == fullscreen_action:
            self.dispatch_command(commands.TOGGLE_FULLSCREEN)

    def show_shortcuts_help(self) -> None:
        from .shortcuts_help import show_shortcuts_help

        show_shortcuts_help(self)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if ExternalDropOpenController.local_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    @staticmethod
    def _event_sequence_text(event: QKeyEvent) -> str:
        combined = int(event.key()) | int(event.modifiers().value)
        return canonical_key(QKeySequence(combined))

    def _navigation_key_action(
        self,
        event: QKeyEvent,
    ) -> Callable[[NavigationInputKind], None] | None:
        sequence = self._event_sequence_text(event)
        actions: tuple[tuple[str, Callable[[NavigationInputKind], None]], ...] = (
            ("viewer_next_page_or_scroll", lambda kind: self.next_page_or_scroll(input_kind=kind)),
            ("viewer_previous_page_or_scroll", lambda kind: self.previous_page_or_scroll(input_kind=kind)),
            ("viewer_next_page", lambda kind: self.next_page(input_kind=kind)),
            ("viewer_previous_page", lambda kind: self.previous_page(input_kind=kind)),
            ("viewer_first_page", lambda kind: self.first_page(input_kind=kind)),
            ("viewer_last_page", lambda kind: self.last_page(input_kind=kind)),
            ("viewer_next_single_page", lambda kind: self.next_one_page(input_kind=kind)),
            ("viewer_previous_single_page", lambda kind: self.previous_one_page(input_kind=kind)),
        )
        for action_id, callback in actions:
            if self._shortcut_has(action_id, sequence):
                return callback
        return None

    def _handle_navigation_key_event(self, event: QKeyEvent) -> bool:
        key = int(event.key())
        modifiers = event.modifiers()
        event_type = event.type()
        if (
            event_type == QEvent.Type.ShortcutOverride
            and self.viewer.can_pan_with_key(key, modifiers)
        ):
            event.accept()
            return True
        if event_type == QEvent.Type.KeyPress and self.viewer.pan_with_key(
            key,
            modifiers,
        ):
            self._active_viewer_pan_keys.add(key)
            event.accept()
            return True
        if (
            event_type == QEvent.Type.KeyRelease
            and key in self._active_viewer_pan_keys
        ):
            if not event.isAutoRepeat():
                self._active_viewer_pan_keys.discard(key)
            event.accept()
            return True

        action = self._navigation_key_action(event)
        if action is None:
            return False
        if event_type == QEvent.Type.ShortcutOverride:
            # Suppress the legacy zero-argument QShortcut/QAction route so the
            # following QKeyEvent retains initial/repeat/release identity.
            event.accept()
            return True
        if event_type == QEvent.Type.KeyRelease:
            if not event.isAutoRepeat():
                self._finish_key_repeat_navigation(key)
                if self._navigation_repeat_key == key:
                    self._navigation_repeat_key = None
            event.accept()
            return True
        if event_type != QEvent.Type.KeyPress:
            return False
        input_kind = (
            NavigationInputKind.KEY_REPEAT
            if event.isAutoRepeat()
            else NavigationInputKind.KEY_INITIAL
        )
        self._navigation_repeat_key = key
        try:
            action(input_kind)
        finally:
            # The page-navigation callback reads this synchronously. Keep no
            # ambient key identity after the request has been constructed.
            self._navigation_repeat_key = None
        event.accept()
        return True

    def _handle_rotation_key_event(self, watched: object, event: QKeyEvent) -> bool:
        """Route modified arrows before menu shortcuts, with editor safety."""

        if event.type() not in {
            QEvent.Type.ShortcutOverride,
            QEvent.Type.KeyPress,
        } or not isinstance(watched, QWidget) or watched.window() is not self:
            return False
        sequence = self._event_sequence_text(event)
        if self._shortcut_has("viewer_rotate_left", sequence):
            callback = self.rotate_left
        elif self._shortcut_has("viewer_rotate_right", sequence):
            callback = self.rotate_right
        else:
            return False
        if not self._viewer_close_key_is_eligible(watched):
            if event.type() == QEvent.Type.ShortcutOverride:
                event.accept()
                return True
            return False
        event.accept()
        if event.type() == QEvent.Type.KeyPress:
            callback()
        return True

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # type: ignore[override]
        if self._handle_viewer_close_key_event(watched, event):
            return True
        if (
            isinstance(event, QKeyEvent)
            and self._handle_rotation_key_event(watched, event)
        ):
            return True
        if watched is getattr(self, "_page_list_viewport", None):
            if event.type() in {
                QEvent.Type.Resize,
                QEvent.Type.Show,
                QEvent.Type.LayoutRequest,
            }:
                self._schedule_page_list_visible_work()
        if (
            watched in getattr(self, "_navigation_key_targets", ())
            and event.type()
            in {
                QEvent.Type.ShortcutOverride,
                QEvent.Type.KeyPress,
                QEvent.Type.KeyRelease,
            }
            and isinstance(event, QKeyEvent)
            and self._handle_navigation_key_event(event)
        ):
            return True
        if watched in getattr(self, "_drop_targets", ()):
            if event.type() in {QEvent.Type.DragEnter, QEvent.Type.DragMove}:
                if ExternalDropOpenController.local_paths(event.mimeData()):
                    event.acceptProposedAction()
                    return True
                event.ignore()
                return True
            if event.type() == QEvent.Type.Drop:
                self._handle_drop_event(event)
                return True
        return super().eventFilter(watched, event)

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        if self._handle_drop_event(event):
            return
        super().dropEvent(event)

    def _handle_drop_event(self, event: QDropEvent) -> bool:
        self.viewer.cancel_pending_canvas_click()
        self._drop_active = True
        local_paths = ExternalDropOpenController.local_paths(event.mimeData())
        paths = ExternalDropOpenController.paths(event.mimeData())
        if local_paths:
            if len(paths) == len(local_paths):
                self._dispatch_dropped_paths(paths)
            else:
                worker = FolderDropProbe(local_paths)
                self._drop_probe_workers.add(worker)

                def finished(folders: tuple[str, ...]) -> None:
                    self._drop_probe_workers.discard(worker)
                    if self._shutdown_prepared:
                        return
                    allowed = set(paths) | set(folders)
                    dispatch = tuple(
                        path for path in local_paths if path in allowed
                    )
                    if dispatch:
                        self._dispatch_dropped_paths(dispatch)
                    else:
                        self._set_status_override(
                            tr('ドロップした項目はNivisViewerで表示できません'),
                            3000,
                        )

                worker.signals.finished.connect(finished)
                QThreadPool.globalInstance().start(worker)
            event.acceptProposedAction()
            self._drop_active = False
            return True
        event.ignore()
        self._drop_active = False
        return False

    def _dispatch_dropped_paths(self, paths: tuple[str, ...]) -> None:
        failures = 0
        for offset, path in enumerate(paths):
            try:
                if self._open_path_handler is not None:
                    self._open_path_handler(path, offset > 0, self)
                elif offset == 0:
                    self.open_path(path)
            except Exception:
                failures += 1
        if failures:
            self._set_status_override(
                tr('{p0}件を開けませんでした', p0=failures),
                3000,
            )

    def _update_slider(self) -> None:
        feedback = self.presentation_state.navigation_feedback
        self.slider.set_page_state(
            feedback.total_pages if feedback is not None else 0,
            feedback.page_index if feedback is not None else 0,
        )

    def _update_status(self) -> None:
        if self._status_override_message is not None:
            self.status.showMessage(self._status_override_message)
            return
        values = self.presentation_state.status_values
        feedback = self.presentation_state.navigation_feedback
        if feedback is None:
            self.status.showMessage(tr('画像が読み込まれていません'))
            return

        page_text = f"{feedback.page_index + 1} / {feedback.total_pages}"
        if values is None:
            self.status.showMessage(page_text)
            return
        path = values.path
        resolution = self.viewer.current_resolution_text()
        zoom = (
            f"{round(self.viewer.manual_zoom * 100)}%"
            if self.fit_mode == "manual_zoom"
            else ("100%" if self.fit_mode == "actual_size" else self.fit_mode)
        )
        size = self._format_file_size(values.file_size)
        frame_failure = self.presentation_state.frame_failure
        details = "    ".join(
            part
            for part in (
                path,
                page_text,
                resolution,
                zoom,
                size,
                frame_failure.message if frame_failure is not None else "",
            )
            if part
        )
        self.status.showMessage(details)

    def _set_status_override(
        self,
        message: str,
        duration_ms: int | None = None,
    ) -> None:
        self._status_override_token += 1
        token = self._status_override_token
        self._status_override_message = message
        self.status.showMessage(message)
        if duration_ms is None:
            return

        def release_override() -> None:
            if self._shutdown_prepared or token != self._status_override_token:
                return
            self._status_override_message = None
            self._update_status()

        QTimer.singleShot(max(0, int(duration_ms)), self, release_override)

    def _clear_status_override(self) -> None:
        self._status_override_token += 1
        self._status_override_message = None

    @staticmethod
    def _format_file_size(size: int | None) -> str:
        if size is None:
            return ""
        units = ["B", "KB", "MB", "GB"]
        value = float(size)
        unit = units[0]
        for unit in units:
            if value < 1024 or unit == units[-1]:
                break
            value /= 1024
        if unit == "B":
            return f"{int(value)} {unit}"
        return f"{value:.1f} {unit}"

    def _on_slider_changed(self, value: int) -> None:
        input_kind = (
            NavigationInputKind.SLIDER_SCRUB
            if self.slider.isSliderDown()
            else NavigationInputKind.DISCRETE
        )
        self.page_navigation.go_to_focused_page_index(
            value,
            input_kind=input_kind,
        )
        # Reconcile clamping/no-op input through the same page-only authority;
        # set_page_state blocks signals and keeps an accepted drag target.
        self._update_slider()

    def _on_zoom_changed(self, zoom: float) -> None:
        self.fit_mode = "manual_zoom"
        self._update_shared_setting("fit_mode", self.fit_mode)
        self._sync_actions()
        self._update_status()
        if self._zip_runtime_active:
            self._raster_zoom_warmup_context = (
                self.book_session.generation,
                id(self.book_session.source),
            )
            self._raster_zoom_warmup_timer.start()
            self._refresh_view()
            return
        if self.image_cache.set_raster_decode_bounds(
            self._current_raster_decode_bounds()
        ):
            self._refresh_view()
        else:
            self._schedule_pdf_rerender()

    def _release_settled_raster_warmup(self) -> None:
        context = self._raster_zoom_warmup_context
        self._raster_zoom_warmup_context = None
        if (
            context is None
            or self._shutdown_prepared
            or not self._zip_runtime_active
            or self.presentation_state.replacement_open_pending
            or self.fit_mode != "manual_zoom"
            or context != (
                self.book_session.generation,
                id(self.book_session.source),
            )
            or not self._raster_background_allowed()
        ):
            return
        request = self._zip_runtime_request(self.model.spread_at())
        runtime = self._zip_runtime
        if request is None or runtime is None:
            return
        # Refresh only the existing request's work order: no presentation,
        # history/progress change, new request serial, or input-gate release.
        if runtime.refresh_work_order(request):
            pending = self._pending_zip_runtime_request
            if pending is not None and pending.request_id == request.request_id:
                self._pending_zip_runtime_request = request

    def _current_raster_decode_bounds(self) -> tuple[int, int] | None:
        if (
            isinstance(self.book_session.source, PdfImageSource)
            or self.fit_mode not in {"fit_window", "fit_no_upscale"}
            or self.viewer_downscale_algorithm == "nearest"
        ):
            return None
        dpr = max(1.0, float(self.viewer.devicePixelRatioF()))
        return (
            max(1, round(self.viewer.width() * dpr)),
            max(1, round(self.viewer.height() * dpr)),
        )

    def _current_book_runtime_decode_bounds(
        self,
    ) -> tuple[int | None, int | None] | None:
        """Return the physical preview constraints for ZIP/folder runtimes.

        RasterBookRuntime can size each page from its final slot, so rotation,
        spread, split and image adjustments no longer require a full source.
        Actual/manual/pixel views retain their explicit full-pixel semantics.
        """

        if self.fit_mode not in {
            "fit_window",
            "fit_no_upscale",
            "fit_width",
            "fit_height",
        } or self.viewer_downscale_algorithm == "nearest":
            return None
        dpr = max(1.0, float(self.viewer.devicePixelRatioF()))
        width = max(1, round(self.viewer.width() * dpr))
        height = max(1, round(self.viewer.height() * dpr))
        if self.fit_mode == "fit_width":
            return width, None
        if self.fit_mode == "fit_height":
            return None, height
        return width, height

    def _current_book_runtime_decode_headroom(self) -> float:
        # JPEG sources now retain the smallest native decoder tier that is at
        # least the final physical slot.  Arbitrary algorithm-specific
        # headroom caused a hidden decoder resize followed by a second final
        # resize, so every final filter shares the same sufficient-source rule.
        return 1.0

    def _on_viewport_changed(self) -> None:
        if self.presentation_state.replacement_open_pending:
            # A provisional replacement must not reactivate or re-request the
            # still-installed old book.  Both success and failure consume the
            # live viewport when they next request their authoritative frame.
            self._presentation_viewport_refresh_required = True
            return
        # Qt can report duplicate geometry notifications without an effective
        # render-spec change. Do not fence a live wheel intent; keep only one
        # coalesced debounce so the timer callback can validate the key again.
        if self._zip_runtime_active and self._zip_runtime is not None:
            request = self._zip_runtime_request(self.model.spread_at())
            if (
                request is not None
                and self._zip_runtime.matches_render_spec(request.render_spec)
            ):
                self._raster_viewport_timer.start(
                    0
                    if self.presentation_state.displayed is None
                    else _RASTER_VIEWPORT_DEBOUNCE_MS
                )
                return
        pending_page = self.presentation_state.frame_loading
        if pending_page or self._zip_runtime_active:
            # Fence the old physical layout immediately.  With a committed
            # frame PresentationState keeps DISPLAYED ownership; without one
            # it projects LOADING rather than the idle prompt.
            self.presentation_state.supersede_pending(preserve_intent=True)
            self._project_presentation_surface()
            self._request_id_adapter = None
            self._visible_page_indexes_adapter = None
        self._presentation_viewport_refresh_required = (
            self._presentation_viewport_refresh_required or pending_page
        )
        self._schedule_pdf_rerender()
        if not isinstance(self.book_session.source, PdfImageSource):
            # A cold initial ZIP/folder frame has nothing valid to retain, so
            # issue the replacement layout request on the next event turn
            # instead of imposing the normal 120 ms resize debounce.
            if (
                self._zip_runtime_active
                and self.presentation_state.displayed is None
            ):
                self._raster_viewport_timer.start(0)
            else:
                self._raster_viewport_timer.start(
                    _RASTER_VIEWPORT_DEBOUNCE_MS
                )

    def _refresh_raster_decode_bounds(self) -> None:
        if self._shutdown_prepared or not self.model.total_pages:
            return
        if self.presentation_state.replacement_open_pending:
            self._presentation_viewport_refresh_required = True
            return
        force_refresh = self._presentation_viewport_refresh_required
        self._presentation_viewport_refresh_required = False
        if self._zip_runtime_active:
            runtime = self._zip_runtime
            render_changed = True
            pending_fenced = False
            if runtime is not None:
                request = self._zip_runtime_request(self.model.spread_at())
                # Duplicate size notifications and A -> B -> A during the
                # existing debounce do not invalidate an unchanged render key.
                # A genuinely different layout still takes the established
                # invalidation path. Source/adjustment keys are not weakened.
                render_changed = (
                    request is None
                    or not runtime.matches_render_spec(request.render_spec)
                )
                if render_changed:
                    runtime.invalidate_layout()
            requested = self.presentation_state.requested
            active_request_id = getattr(self, "_active_request_id", None)
            pending_fenced = bool(
                requested is not None
                and active_request_id is not None
                and requested.token.request_serial != active_request_id
            )
            if render_changed or pending_fenced:
                self._refresh_view()
            return
        if (
            self.image_cache.set_raster_decode_bounds(
                self._current_raster_decode_bounds()
            )
            or force_refresh
        ):
            self._refresh_view()

    def _current_pdf_render_spec(self) -> dict[int, PageRenderSpec] | None:
        if not isinstance(self.book_session.source, PdfImageSource):
            return None
        spread = self.model.spread_at()
        viewport_width = max(1, self.viewer.width())
        viewport_height = max(1, self.viewer.height())
        dpr = max(1.0, float(self.viewer.devicePixelRatioF()))
        specs: dict[int, PageRenderSpec] = {}
        span_units = max(
            self.pdf_prefetch_forward_units,
            self.pdf_prefetch_backward_units,
        )
        spec_indexes = {
            slot.page_index for slot in spread.slots
        }
        for unit in (
            self._display_units_from(
                self.model.current_index,
                1,
                span_units,
            )
            + self._display_units_from(
                self.model.current_index,
                -1,
                span_units,
            )
        ):
            spec_indexes.update(unit)
        page_scales: dict[int, float] = {}
        pending_indexes = set(spec_indexes)
        while pending_indexes:
            page_index = min(pending_indexes)
            unit = self.model.spread_at(page_index)
            unit_sizes = [
                self.model.get_image_size(slot.page_index) or (360, 520)
                for slot in unit.slots
            ]
            if self.rotation_angle in {90, 270}:
                unit_sizes = [
                    (height, width) for width, height in unit_sizes
                ]
            unit_layout = calculate_spread_layout(
                unit_sizes,
                (viewport_width, viewport_height),
                fit_mode=self.fit_mode,
                manual_zoom=self.viewer.manual_zoom,
                gap=self.gap,
                join_spread_pages=self.join_spread_pages,
                spread_is_single=unit.is_single,
                horizontal_alignment=self.horizontal_alignment,
            )
            for slot, page_scale in zip(unit.slots, unit_layout.scales):
                page_scales[slot.page_index] = page_scale
                pending_indexes.discard(slot.page_index)
            pending_indexes.discard(page_index)
        for page_index in sorted(spec_indexes):
            width, height = self.model.get_image_size(page_index) or (360, 520)
            if self.rotation_angle in {90, 270}:
                width, height = height, width
            page_scale = page_scales.get(page_index, 1.0)
            logical_width = max(1, round(width * page_scale))
            logical_height = max(1, round(height * page_scale))
            specs[page_index] = PageRenderSpec(
                logical_width,
                logical_height,
                device_pixel_ratio=dpr,
                rotation_degrees=self.rotation_angle,
                mode=self.fit_mode,
            )
        return specs

    def _schedule_pdf_rerender(self) -> None:
        if isinstance(self.book_session.source, PdfImageSource):
            self._pdf_render_timer.start()

    def _rerender_pdf(self) -> None:
        if not isinstance(self.book_session.source, PdfImageSource):
            return
        force_refresh = self._presentation_viewport_refresh_required
        self._presentation_viewport_refresh_required = False
        if (
            self.image_cache.set_render_spec(self._current_pdf_render_spec())
            or force_refresh
        ):
            self._refresh_view()

    def _request_pdf_magnifier_resolution(
        self,
        page_index: int,
        physical_size: QSize,
    ) -> None:
        if not isinstance(self.book_session.source, PdfImageSource):
            return
        requested = QSize(
            max(1, physical_size.width()),
            max(1, physical_size.height()),
        )
        self._pdf_magnifier_targets[page_index] = requested
        self._pdf_loupe_cache.set_source(self.book_session.source)
        self._limit_pdf_loupe_cache()
        for image in self.viewer._images:
            key = self.viewer._pdf_loupe_requests.get(image.image_id)
            if image.page_index == page_index and key is not None:
                self._pdf_loupe_cache.request(
                    page_index, key, (self.brightness, self.contrast, self.gamma),
                )

    def _on_pdf_loupe_ready(self, key, pixmap) -> None:
        if (self._pdf_loupe_cache.source is self.book_session.source
                and key.adjustments == (self.brightness, self.contrast, self.gamma)):
            self.viewer.apply_pdf_loupe_artifact(key.render, pixmap)

    def _limit_pdf_loupe_cache(self) -> None:
        cache = getattr(self, "_pdf_loupe_cache", None)
        if cache is not None and hasattr(self, "viewer"):
            # Evict reusable loupe entries before taking space from normal-fit
            # content. Current lens bindings remain bounded working surfaces.
            cache.set_byte_limit(max(0, self.viewer_cache_budget_bytes
                                     - self.image_cache.cache_bytes
                                     - self.viewer.render_cache_bytes()))

    def _request_raster_magnifier_resolution(
        self,
        page_index: int,
        _physical_size: QSize,
    ) -> None:
        if isinstance(self.book_session.source, PdfImageSource):
            return
        if self._zip_runtime_active:
            # The full-source render spec has its own key.  Adopting that
            # request is enough to hydrate the current page; clearing every
            # ready QPixmap would unnecessarily destroy navigation hits.
            runtime = self._zip_runtime
            request = self._zip_runtime_request(self.model.spread_at())
            if runtime is not None and request is not None:
                runtime.require_cached_current_source(request)
            self._refresh_view()
            return
        if not self.image_cache.ensure_full_resolution(page_index):
            self.viewer.resume_magnifier_after_source_render()

    def _on_magnifier_cancelled(self) -> None:
        """Return an interactive raster promotion to the retained preview.

        ``stage`` adopts the normal display key immediately, which fences and
        cancels an incompatible full-source job without clearing either frame
        tier.  The zero-timer then creates the presentation request that may
        atomically publish the retained preview.  Callers that cancel before
        an explicit page/layout refresh stop that timer at ``_refresh_view``.
        """

        self._clear_pdf_magnifier_resolution()
        if (
            self._shutdown_prepared
            or not self._zip_runtime_active
            or isinstance(self.book_session.source, PdfImageSource)
        ):
            return
        runtime = self._zip_runtime
        request = self._zip_runtime_request(self.model.spread_at())
        if runtime is None or request is None or not runtime.stage(request):
            return
        self._raster_magnifier_cancel_timer.start()

    def _refresh_after_raster_magnifier_cancel(self) -> None:
        if (
            self._shutdown_prepared
            or not self._zip_runtime_active
            or not self.model.total_pages
            or isinstance(self.book_session.source, PdfImageSource)
        ):
            return
        self._refresh_view()

    def _clear_pdf_magnifier_resolution(self) -> None:
        if not self._pdf_magnifier_targets:
            return
        self._pdf_magnifier_targets.clear()

    def set_view_mode(self, mode: str) -> None:
        self.viewer.cancel_magnifier()
        self.view_mode = mode
        self._update_shared_setting("view_mode", mode)
        self.model.update_options(view_mode=mode)
        self._sync_actions()
        if self.model.total_pages:
            self._refresh_view()
        self.fullscreen_chrome.reevaluate_visibility()

    def toggle_view_mode(self) -> None:
        self.set_view_mode("single" if self.view_mode == "spread" else "spread")

    def set_reading_direction(self, direction: str) -> None:
        self.viewer.cancel_magnifier()
        if direction != self.reading_direction:
            self.viewer.invalidate_prepared_displays()
        self.reading_direction = direction
        self.slider.set_reading_direction(direction)
        self._update_shared_setting("reading_direction", direction)
        self.model.update_options(reading_direction=direction)
        self._sync_actions()
        if self.model.total_pages:
            self._refresh_view()
        self.fullscreen_chrome.reevaluate_visibility()

    def toggle_reading_direction(self) -> None:
        self.set_reading_direction("ltr" if self.reading_direction == "rtl" else "rtl")

    def set_single_first_page(self, checked: bool) -> None:
        self.single_first_page = checked
        self._update_shared_setting("single_first_page", checked)
        self.model.update_options(single_first_page=checked)
        if self.model.total_pages:
            self._refresh_view()
        self.fullscreen_chrome.reevaluate_visibility()

    def set_treat_wide_image_as_single(self, checked: bool) -> None:
        self.treat_wide_image_as_single = checked
        self._update_shared_setting("treat_wide_image_as_single", checked)
        self.model.update_options(treat_wide_image_as_single=checked)
        if self.model.total_pages:
            self._refresh_view()
        self.fullscreen_chrome.reevaluate_visibility()

    def set_split_wide_image(self, checked: bool) -> None:
        self.viewer.cancel_magnifier()
        if bool(checked) != self.split_wide_image:
            self.viewer.invalidate_prepared_displays()
        self.split_wide_image = checked
        self._update_shared_setting("split_wide_image", checked)
        self._sync_actions()
        if self.model.total_pages:
            self._refresh_view()
        self.fullscreen_chrome.reevaluate_visibility()

    def set_smooth_scaling(self, checked: bool) -> None:
        # Compatibility-only entry point.  The persisted authority is the
        # explicit normal up/down pair; the obsolete boolean is never saved.
        self._update_shared_settings(
            {
                "viewer_downscale_algorithm": (
                    "auto" if checked else "fast"
                ),
                "viewer_upscale_algorithm": (
                    "auto" if checked else "nearest"
                ),
            }
        )

    def set_viewer_downscale_algorithm(self, algorithm: str) -> None:
        self._update_shared_setting(
            "viewer_downscale_algorithm",
            normalize_downscale_algorithm(algorithm),
        )

    def set_viewer_upscale_algorithm(self, algorithm: str) -> None:
        self._update_shared_setting(
            "viewer_upscale_algorithm",
            normalize_upscale_algorithm(algorithm),
        )

    def set_magnifier_downscale_algorithm(self, algorithm: str) -> None:
        self._update_shared_setting(
            "magnifier_downscale_algorithm",
            normalize_downscale_algorithm(algorithm),
        )

    def set_magnifier_upscale_algorithm(self, algorithm: str) -> None:
        self._update_shared_setting(
            "magnifier_upscale_algorithm",
            normalize_upscale_algorithm(algorithm),
        )

    def set_viewer_resampling_mode(self, mode: str) -> None:
        policy = resampling_policy_for_legacy_mode(
            normalize_resampling_mode(mode)
        )
        self._update_shared_settings(
            {
                "viewer_downscale_algorithm": policy.downscale_algorithm,
                "viewer_upscale_algorithm": policy.upscale_algorithm,
            }
        )

    def set_magnifier_resampling_mode(self, mode: str) -> None:
        policy = resampling_policy_for_legacy_mode(
            normalize_resampling_mode(mode)
        )
        self._update_shared_settings(
            {
                "magnifier_downscale_algorithm": policy.downscale_algorithm,
                "magnifier_upscale_algorithm": policy.upscale_algorithm,
            }
        )

    def set_horizontal_alignment(self, alignment: str) -> None:
        self.horizontal_alignment = alignment
        self._update_shared_setting("horizontal_alignment", alignment)
        self.viewer.set_horizontal_alignment(alignment)
        self._sync_actions()
        if self._zip_runtime_active and self.model.total_pages:
            self._refresh_view()

    def set_fit_mode(self, mode: str) -> None:
        if mode == "fit_window":
            self.viewer.reset_zoom()
        else:
            self.viewer.set_fit_mode(mode)
        self.fit_mode = mode
        self._update_shared_setting("fit_mode", mode)
        self._sync_actions()
        self._update_status()
        if self._zip_runtime_active and self.model.total_pages:
            self._refresh_view()
            self.fullscreen_chrome.reevaluate_visibility()
            return
        raster_changed = self.image_cache.set_raster_decode_bounds(
            self._current_raster_decode_bounds()
        )
        if raster_changed and self.model.total_pages:
            self._refresh_view()
        else:
            self._rerender_pdf()
        self.fullscreen_chrome.reevaluate_visibility()

    def zoom_in(self) -> None:
        base = self.viewer._scale_for_current_mode()
        self.viewer.set_manual_zoom(base * 1.15)

    def zoom_out(self) -> None:
        base = self.viewer._scale_for_current_mode()
        self.viewer.set_manual_zoom(base / 1.15)

    def rotate_left(self) -> None:
        self.rotation_angle = (self.rotation_angle - 90) % 360
        self.viewer.set_rotation_angle(self.rotation_angle)
        self._update_status()
        self._refresh_after_rotation_change()

    def rotate_right(self) -> None:
        self.rotation_angle = (self.rotation_angle + 90) % 360
        self.viewer.set_rotation_angle(self.rotation_angle)
        self._update_status()
        self._refresh_after_rotation_change()

    def reset_rotation(self) -> None:
        self.rotation_angle = 0
        self.viewer.set_rotation_angle(self.rotation_angle)
        self._update_status()
        self._refresh_after_rotation_change()

    def _refresh_after_rotation_change(self) -> None:
        self._refresh_page_list_thumbnail_spec()
        if self._zip_runtime_active:
            if self._zip_runtime is not None:
                self._zip_runtime.invalidate_layout()
            if self.model.total_pages:
                self._refresh_view()
            return
        self._rerender_pdf()

    def toggle_slideshow(self) -> None:
        if self.slideshow_timer.isActive() or self._slideshow_waiting_for_next:
            self.stop_slideshow()
        else:
            if self.model.total_pages > 0:
                self._slideshow_skip_next_for_book = None
                self.slideshow_timer.start()
        self._sync_actions()

    def set_slideshow_interval(self, seconds: float) -> None:
        self.slideshow_interval_ms = min(
            SLIDESHOW_INTERVAL_MAX_MS,
            max(SLIDESHOW_INTERVAL_MIN_MS, round(seconds * 1000)),
        )
        self._update_shared_setting("slideshow_interval_ms", self.slideshow_interval_ms)
        self.slideshow_timer.setInterval(self.slideshow_interval_ms)
        self._sync_actions()

    def stop_slideshow(self) -> None:
        self.slideshow_timer.stop()
        self._slideshow_waiting_for_next = False
        self._slideshow_opening_next = False
        self.slideshow_stopped.emit(self)
        self._sync_actions()

    def start_slideshow(self, seconds: float) -> None:
        self.set_slideshow_interval(seconds)
        self._slideshow_skip_next_for_book = None
        if self.model.total_pages > 0 and not self._slideshow_waiting_for_next:
            self.slideshow_timer.start()
        self._sync_actions()

    def set_slideshow_interval_dialog(self, *, start: bool = False) -> None:
        seconds, accepted = QInputDialog.getDouble(
            self,
            tr('スライドショー間隔'),
            tr('秒数:'),
            self.slideshow_interval_ms / 1000,
            SLIDESHOW_INTERVAL_MIN_MS / 1000,
            SLIDESHOW_INTERVAL_MAX_MS / 1000,
            1,
        )
        if not accepted:
            self._sync_actions()
            return
        if start:
            self.start_slideshow(seconds)
        else:
            self.set_slideshow_interval(seconds)

    def _advance_slideshow(self) -> None:
        if self._slideshow_waiting_for_next:
            return
        previous_anchor = self.model.current_index
        self.page_navigation.next_display_unit(input_kind=NavigationInputKind.REFRESH)
        if self.model.current_index != previous_anchor:
            return
        if (
            self.model.total_pages > 0 and self.auto_open_adjacent_book
            and self._slideshow_skip_next_for_book != self._current_book_key
        ):
            self._slideshow_waiting_for_next = True
            self.slideshow_timer.stop()
            result = self._open_adjacent_book(1)
            if result not in {"searching", "opened"} and self._slideshow_waiting_for_next:
                self.complete_adjacent_book_search(1, result or "unavailable")
            self._sync_actions()
            return
        self._slideshow_repeat_or_stop()

    def _slideshow_repeat_or_stop(self) -> None:
        if self.slideshow_repeat and self.model.total_pages > 0:
            self.page_navigation.first_page(input_kind=NavigationInputKind.REFRESH)
            self.slideshow_timer.start()
        else:
            self.slideshow_timer.stop()
        self._sync_actions()

    def next_page(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> None:
        if self._hold_unready_wheel_advance(input_kind, 1):
            return
        moved = self.page_navigation.next_display_unit(input_kind=input_kind)
        if not moved and self.auto_open_adjacent_book:
            self.open_next_book()

    def previous_page(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> None:
        if self._hold_unready_wheel_advance(input_kind, -1):
            return
        moved = self.page_navigation.previous_display_unit(input_kind=input_kind)
        if not moved and self.auto_open_adjacent_book:
            self.open_previous_book()

    def next_page_or_scroll(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> None:
        if not self.viewer.scroll_forward():
            self.next_page(input_kind=input_kind)

    def previous_page_or_scroll(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> None:
        if not self.viewer.scroll_backward():
            self.previous_page(input_kind=input_kind)

    def next_one_page(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> None:
        if self._hold_unready_wheel_advance(input_kind, 1):
            return
        self.page_navigation.next_single_page(input_kind=input_kind)

    def previous_one_page(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> None:
        if self._hold_unready_wheel_advance(input_kind, -1):
            return
        self.page_navigation.previous_single_page(input_kind=input_kind)

    def _hold_unready_wheel_advance(
        self,
        input_kind: NavigationInputKind,
        direction: int,
    ) -> bool:
        """Keep wheel position at one unready unit; allow reversal toward display.

        Repeated notches are consumed, not queued for replay. A reverse notch
        can return from the pending unit to the last complete frame. Direct
        seek, commands and cached wheel turns keep their normal semantics.
        """

        if input_kind is not NavigationInputKind.WHEEL or not self._zip_runtime_active:
            return False
        requested = self.presentation_state.requested
        displayed = self.presentation_state.displayed
        if requested is None or (
            displayed is not None and displayed.token == requested.token
        ):
            return False
        if (
            displayed is not None
            and displayed.token.book == requested.token.book
            and displayed.unit.identity == requested.unit.identity
        ):
            return False
        if (
            displayed is not None
            and displayed.token.book == requested.token.book
            and requested.direction == -direction
        ):
            return False
        return True

    def go_to_page_dialog(self) -> None:
        if self.model.total_pages <= 0:
            return
        page, accepted = QInputDialog.getInt(
            self,
            tr('ページ指定'),
            tr('ページ番号:'),
            self.model.current_index + 1,
            1,
            self.model.total_pages,
            1,
        )
        if accepted:
            self._go_to_index_with_history(page - 1)

    def first_page(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> None:
        self.stop_slideshow()
        if self.slideshow_action.isChecked():
            self.slideshow_action.setChecked(False)
        self.page_navigation.first_page(input_kind=input_kind)

    def last_page(
        self,
        *,
        input_kind: NavigationInputKind = NavigationInputKind.DISCRETE,
    ) -> None:
        self.stop_slideshow()
        if self.slideshow_action.isChecked():
            self.slideshow_action.setChecked(False)
        self.page_navigation.last_page(input_kind=input_kind)

    def toggle_fullscreen(self) -> None:
        if self._is_fullscreen_mode():
            self.fullscreen_chrome.leave_true_fullscreen()
        else:
            self.fullscreen_chrome.enter_true_fullscreen()
        self._apply_chrome_visibility()

    def exit_fullscreen(self) -> None:
        if self._is_fullscreen_mode():
            self.fullscreen_chrome.leave_true_fullscreen()
        self._apply_chrome_visibility()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._apply_chrome_visibility()

    def _apply_chrome_visibility(self) -> None:
        fullscreen = self._is_fullscreen_mode()
        self.fullscreen_chrome.set_fullscreen_state(
            fullscreen,
            hide_ui=self.hide_ui_in_fullscreen,
            hide_cursor=self.hide_cursor_in_fullscreen,
        )
        self.page_list_dock.setVisible(not fullscreen and self.show_page_list)

    def _apply_cursor_visibility_policy(self) -> None:
        self.viewer.set_auto_hide_cursor(False)
        self.fullscreen_chrome.set_hide_cursor_enabled(
            self._is_fullscreen_mode() and self.hide_cursor_in_fullscreen
        )

    def prepare_shutdown(self, *, wait_msecs: int = 250) -> bool:
        if self._shutdown_cleanup_complete:
            return True
        # This flag fences all new input/work on the first attempt and stays
        # set while timed-out cleanup phases are retried.
        self._shutdown_prepared = True
        phase = self._shutdown_cleanup_phase
        if phase == 0:
            self._save_current_reading_position()
            self._pending_book_open_projection = None
            self.presentation_state.close()
            self._project_presentation_surface()
            self._zip_runtime_request_timer.stop()
            self._pending_zip_runtime_request = None
            self._presentation_side_effect_timer.stop()
            self._pending_presentation_side_effect_token = None
            self._raster_magnifier_cancel_timer.stop()
            self._viewer_memory_pressure_timer.stop()
            self._raster_zoom_warmup_timer.stop()
            self._raster_zoom_warmup_context = None
            self._page_list_filter_timer.stop()
            self._page_list_viewport_timer.stop()
            self._set_page_list_paused(True)
            for runtime in {
                self._page_list_runtime,
                self._staged_page_list_runtime,
            }:
                if runtime is not None:
                    runtime.set_visible(False)
            self.page_list_model.clear()
            self._path_probe_generation += 1
            self._pending_path_probe = None
            self.fullscreen_chrome.shutdown()
            self._cancel_interactive_open()
            self.viewer.cancel_mouse_gesture()
            self.viewer.cancel_pending_canvas_click()
            self._deactivate_zip_runtime(clear_artifacts=True)
            self._shutdown_cleanup_phase = 1

        if self._shutdown_cleanup_phase == 1:
            if not self._pdf_loupe_cache.shutdown(max(5000, wait_msecs)):
                return False
            self._shutdown_cleanup_phase = 2

        if self._shutdown_cleanup_phase == 2:
            if not self.viewer.shutdown_rendering(max(5000, wait_msecs)):
                return False
            self._shutdown_cleanup_phase = 3

        if self._shutdown_cleanup_phase == 3:
            self.slideshow_timer.stop()
            self._pdf_render_timer.stop()
            self._prepared_display_timer.stop()
            self._raster_viewport_timer.stop()
            self._raster_paint_fallback_timer.stop()
            self._raster_prefetch_after_paint = None
            self._clear_raster_prefetch_pipeline()
            self._release_raster_interactive_lane()
            self._cancel_pending_display_demand()
            self._cancel_pending_decode_demand()
            self._cancel_deferred_pdf_prefetch()
            self.book_session.shutdown(wait_msecs=wait_msecs)
            self._shutdown_cleanup_phase = 4

        if self._shutdown_cleanup_phase == 4:
            if self._owns_archive_backend_registry:
                self.archive_backend_registry.close()
            self._shutdown_cleanup_phase = 5

        if self._shutdown_cleanup_phase == 5:
            if self._owns_pdfium_service and not self.pdfium_service.shutdown():
                raise RuntimeError(
                    self.pdfium_service.last_shutdown_error
                    or "PDFium shutdown did not complete."
                )
            self._shutdown_cleanup_phase = 6

        if self._shutdown_cleanup_phase == 6:
            if self._owns_path_availability_service:
                self.path_availability_service.close()
            self._shutdown_cleanup_phase = 7

        self._shutdown_cleanup_complete = self._shutdown_cleanup_phase == 7
        return self._shutdown_cleanup_complete

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        guard = getattr(self, "_application_close_guard", None)
        if callable(guard) and not guard(self):
            event.ignore()
            return
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        try:
            if not self.prepare_shutdown():
                event.ignore()
                return
        except Exception:
            _LOG.exception("Viewer shutdown preparation did not complete")
            event.ignore()
            return
        self.closing.emit(self)
        super().closeEvent(event)
