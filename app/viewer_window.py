from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QByteArray, QEvent, QPoint, QSize, QThreadPool, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .archive_backend_registry import ArchiveBackendRegistry
from .book_session import AsyncBookOpenFailed, BookOpened, BookSession
from .config_manager import ConfigManager
from .drag_drop import FolderDropProbe
from .external_drop_open import ExternalDropOpenController
from .fullscreen_chrome import FullscreenChromeController
from .image_cache import CachedImage
from .image_work_coordinator import ImageWorkCoordinator
from .image_source import (
    ARCHIVE_EXTENSIONS,
    PDF_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    FolderImageSource,
    FolderListingSnapshot,
    ImageSource,
    SevenZipImageSource,
    ZipImageSource,
    create_image_source,
)
from .metadata_store import MetadataStore
from .pdf_backend import PageRenderSpec
from .pdf_image_source import PdfImageSource
from .path_availability import (
    PathAvailability,
    PathAvailabilityResult,
    PathAvailabilityService,
    lexical_absolute,
)
from .performance_trace import performance_trace
from .thumbnail_provider import PageThumbnailProvider
from . import viewer_commands as commands
from .viewer_page_navigation import ViewerPageNavigationController
from .viewer_page_slider import ViewerPageSlider
from .viewer_display_unit import (
    ViewerDisplayUnit,
    ViewerSlotState,
)
from .viewer_widget import ViewerImage, ViewerWidget, calculate_spread_layout


_DISPLAY_LOG = logging.getLogger("nivisviewer.viewer.display_unit")
_PDF_PREFETCH_IDLE_GRACE_MS = 120


class ViewerWindow(QMainWindow):
    activated = Signal(object)
    closing = Signal(object)
    book_changed = Signal(object, str)
    interactive_open_started = Signal(object)
    first_frame_ready = Signal(object)
    interactive_open_cancelled = Signal(object)

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
        self._load_prefetch_settings()
        self.image_cache.pageLoaded.connect(self._on_cache_page_loaded)
        self.book_session.page_changed.connect(self._queue_metadata_progress)
        self.book_session.async_opened.connect(self._on_async_book_opened)
        self.book_session.async_open_failed.connect(self._on_async_book_open_failed)
        self._open_path_handler = open_path_handler
        self._drop_probe_workers: set[FolderDropProbe] = set()
        self._drop_active = False
        self._adjacent_book_handler = adjacent_book_handler
        self._shutdown_prepared = False
        self._active_request_id = 0
        self._reload_page_index: int | None = None
        self._applied_display_request_id = 0
        self._visible_page_indexes: tuple[int, ...] = tuple()
        self._display_unit = ViewerDisplayUnit.empty()
        self._page_history_back: list[int] = []
        self._page_history_forward: list[int] = []
        self._metadata_book_path = ""
        self._metadata_book_item_type = ""
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
        self.smooth_scaling = bool(self.settings.get("smooth_scaling", True))
        self.horizontal_alignment = str(self.settings.get("horizontal_alignment", "center"))
        self.brightness = max(0.1, min(3.0, float(self.settings.get("brightness", 1.0))))
        self.contrast = max(0.1, min(3.0, float(self.settings.get("contrast", 1.0))))
        self.gamma = max(0.1, min(5.0, float(self.settings.get("gamma", 1.0))))
        self.cache_size = int(self.settings.get("cache_size", 10))
        self.rotation_angle = int(self.settings.get("rotation_angle", 0)) % 360
        self.slideshow_interval_ms = int(self.settings.get("slideshow_interval_ms", 3000))
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
        self.magnifier_enabled = bool(self.settings.get("magnifier_enabled", False))
        self.magnifier_zoom = float(self.settings.get("magnifier_zoom", 2.0))
        self.magnifier_size = int(self.settings.get("magnifier_size", 220))
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
        self.mouse_back_button_action = commands.normalize_viewer_command(
            self.settings.get("mouse_back_button_action")
        )
        self.mouse_forward_button_action = commands.normalize_viewer_command(
            self.settings.get("mouse_forward_button_action")
        )
        self.viewer_canvas_left_click_action = str(
            self.settings.get(
                "viewer_canvas_left_click_action",
                commands.NEXT_SINGLE_PAGE,
            )
        )
        self.viewer_canvas_click_direction = str(
            self.settings.get("viewer_canvas_click_direction", "right_next")
        )
        self.viewer_slider_wheel_single_page_enabled = bool(
            self.settings.get(
                "viewer_slider_wheel_single_page_enabled",
                False,
            )
        )
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.setInterval(max(500, self.slideshow_interval_ms))
        self.slideshow_timer.timeout.connect(self._advance_slideshow)
        self._pdf_render_timer = QTimer(self)
        self._pdf_render_timer.setSingleShot(True)
        self._pdf_render_timer.setInterval(180)
        self._pdf_render_timer.timeout.connect(self._rerender_pdf)
        self._pdf_prefetch_timer = QTimer(self)
        self._pdf_prefetch_timer.setSingleShot(True)
        self._pdf_prefetch_timer.setInterval(_PDF_PREFETCH_IDLE_GRACE_MS)
        self._pdf_prefetch_timer.timeout.connect(self._start_deferred_pdf_prefetch)
        self._pdf_prefetch_source: PdfImageSource | None = None
        self._pdf_prefetch_generation = -1
        self._pdf_prefetch_center = 0
        self._pdf_prefetch_visible_indexes: tuple[int, ...] = tuple()
        self._pdf_prefetch_direction = 0
        self._last_preload_source: ImageSource | None = None
        self._last_preload_generation = -1
        self._last_preload_center: int | None = None
        self._last_preload_direction = 0
        self.image_cache.set_adjustments(brightness=self.brightness, contrast=self.contrast, gamma=self.gamma)
        self.page_navigation = ViewerPageNavigationController(
            self.model,
            self._on_page_navigation_changed,
            self,
        )

        self._build_ui()
        self._connect_shortcuts()
        self._restore_window_state()
        self._apply_settings_to_widgets()
        self.config.settings_changed.connect(self.apply_settings)

        self._start_fullscreen = bool(self.settings.get("fullscreen"))

    @property
    def _current_book_key(self) -> str:
        return self.book_session.book_key

    @property
    def _opened_path(self) -> str:
        if self.book_session.current_path is None:
            return ""
        return str(self.book_session.current_path)

    def show_initial(self) -> None:
        if self._start_fullscreen:
            self.showFullScreen()
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

    def window_state_snapshot(self) -> dict[str, object]:
        return {
            "window_geometry": bytes(self.saveGeometry().toBase64()).decode("ascii"),
            "window_state": bytes(self.saveState().toBase64()).decode("ascii"),
            "fullscreen": self.isFullScreen(),
            "rotation_angle": self.rotation_angle,
        }

    def event(self, event: QEvent) -> bool:  # type: ignore[override]
        handled = super().event(event)
        if event.type() == QEvent.Type.WindowActivate:
            self.activated.emit(self)
        elif event.type() == QEvent.Type.WindowDeactivate and hasattr(self, "viewer"):
            self.viewer.cancel_mouse_gesture()
            self.viewer.cancel_pending_canvas_click()
        if (
            hasattr(self, "fullscreen_chrome")
            and event.type()
            in {QEvent.Type.Resize, QEvent.Type.ScreenChangeInternal}
        ):
            QTimer.singleShot(0, self.fullscreen_chrome.reevaluate_visibility)
        return handled

    def _build_ui(self) -> None:
        self.viewer = ViewerWidget(self)
        self.slider = ViewerPageSlider(self)
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

        self._updating_page_list = False
        self._page_list_dirty = False
        self.page_list_filter = QLineEdit(self)
        self.page_list_filter.setPlaceholderText("ページ名で絞り込み")
        self.page_list_filter.textChanged.connect(lambda _text: self._rebuild_page_list())
        self.page_list = QListWidget(self)
        self.page_list.setIconSize(QSize(self.thumbnail_size, self.thumbnail_size))
        self.page_list.currentRowChanged.connect(self._on_page_list_row_changed)
        page_list_container = QWidget(self)
        page_list_layout = QVBoxLayout(page_list_container)
        page_list_layout.setContentsMargins(4, 4, 4, 4)
        page_list_layout.setSpacing(4)
        page_list_layout.addWidget(self.page_list_filter)
        page_list_layout.addWidget(self.page_list, 1)
        self.page_list_dock = QDockWidget("ページ一覧", self)
        self.page_list_dock.setObjectName("page_list_dock")
        self.page_list_dock.setWidget(page_list_container)
        self.page_list_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.page_list_dock)
        self.page_list_dock.visibilityChanged.connect(self._on_page_list_dock_visibility_changed)
        self.page_list_dock.setVisible(self.show_page_list)

        self._create_menus()

        self.viewer.nextRequested.connect(self.next_page_or_scroll)
        self.viewer.previousRequested.connect(self.previous_page_or_scroll)
        self.viewer.fullscreenToggleRequested.connect(
            lambda: self.dispatch_command(commands.TOGGLE_FULLSCREEN)
        )
        self.viewer.leftSideClicked.connect(self._on_left_side_clicked)
        self.viewer.rightSideClicked.connect(self._on_right_side_clicked)
        self.viewer.contextMenuRequested.connect(self._show_viewer_context_menu)
        self.viewer.gestureRecognized.connect(self._on_mouse_gesture)
        self.viewer.extraMouseButtonPressed.connect(self._on_extra_mouse_button)
        self.viewer.zoomChanged.connect(self._on_zoom_changed)
        self.viewer.viewportChanged.connect(self._schedule_pdf_rerender)
        self.viewer.contentPainted.connect(self._on_viewer_content_painted)
        self.slider.focusedPageRequested.connect(self._on_slider_changed)
        self.slider.nextSinglePageRequested.connect(
            self.page_navigation.next_single_page
        )
        self.slider.previousSinglePageRequested.connect(
            self.page_navigation.previous_single_page
        )
        self.slider.nextDisplayUnitRequested.connect(self.next_page)
        self.slider.previousDisplayUnitRequested.connect(self.previous_page)
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
        for target in drop_targets:
            target.setAcceptDrops(True)
            target.installEventFilter(self)

    def _create_menus(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("ファイル")
        open_action = QAction("開く", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_dialog)
        reload_action = QAction("再読み込み", self)
        reload_action.setShortcut("F5")
        reload_action.triggered.connect(self.reload_current_book)
        export_view_action = QAction("現在の表示をPNG保存", self)
        export_view_action.triggered.connect(self.export_current_view)
        copy_path_action = QAction("現在画像のパスをコピー", self)
        copy_path_action.setShortcut("Ctrl+Shift+C")
        copy_path_action.triggered.connect(self.copy_current_image_path)
        copy_image_action = QAction("現在画像をコピー", self)
        copy_image_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_image_action.triggered.connect(self.copy_current_image)
        copy_view_action = QAction("現在の表示をコピー", self)
        copy_view_action.setShortcut("Ctrl+Alt+C")
        copy_view_action.triggered.connect(self.copy_current_view)
        page_info_action = QAction("ページ情報", self)
        page_info_action.setShortcut("Ctrl+I")
        page_info_action.triggered.connect(self.show_page_info)
        open_location_action = QAction("現在の場所を開く", self)
        open_location_action.triggered.connect(self.open_current_location)
        exit_action = QAction("終了", self)
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
        self.reopen_last_action = QAction("起動時に前回の本を開く", self, checkable=True)
        self.reopen_last_action.triggered.connect(self.set_reopen_last_on_start)
        self.recursive_folder_action = QAction("サブフォルダも読み込む", self, checkable=True)
        self.recursive_folder_action.triggered.connect(self.set_recursive_folder)
        self.sort_descending_action = QAction("逆順で読む", self, checkable=True)
        self.sort_descending_action.triggered.connect(self.set_sort_descending)
        self.auto_open_adjacent_book_action = QAction("終端で隣の本へ移動", self, checkable=True)
        self.auto_open_adjacent_book_action.triggered.connect(self.set_auto_open_adjacent_book)
        file_menu.addAction(self.reopen_last_action)
        file_menu.addAction(self.recursive_folder_action)
        file_menu.addAction(self.sort_descending_action)
        file_menu.addAction(self.auto_open_adjacent_book_action)
        file_menu.addSeparator()
        self.recent_menu = file_menu.addMenu("最近開いたもの")
        self._rebuild_recent_menu()
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        view_menu = menu_bar.addMenu("表示")

        self.single_action = QAction("単ページ表示", self, checkable=True)
        self.single_action.triggered.connect(lambda: self.set_view_mode("single"))
        self.spread_action = QAction("見開き表示", self, checkable=True)
        self.spread_action.triggered.connect(lambda: self.set_view_mode("spread"))
        view_group = QActionGroup(self)
        view_group.addAction(self.single_action)
        view_group.addAction(self.spread_action)
        view_group.setExclusive(True)
        view_menu.addAction(self.single_action)
        view_menu.addAction(self.spread_action)
        view_menu.addSeparator()

        self.ltr_action = QAction("左綴じ", self, checkable=True)
        self.ltr_action.triggered.connect(lambda: self.set_reading_direction("ltr"))
        self.rtl_action = QAction("右綴じ", self, checkable=True)
        self.rtl_action.triggered.connect(lambda: self.set_reading_direction("rtl"))
        direction_group = QActionGroup(self)
        direction_group.addAction(self.ltr_action)
        direction_group.addAction(self.rtl_action)
        direction_group.setExclusive(True)
        view_menu.addAction(self.ltr_action)
        view_menu.addAction(self.rtl_action)
        view_menu.addSeparator()

        self.single_first_action = QAction("表紙を単独表示", self, checkable=True)
        self.single_first_action.triggered.connect(self.set_single_first_page)
        self.wide_single_action = QAction("横長画像を単独表示", self, checkable=True)
        self.wide_single_action.triggered.connect(self.set_treat_wide_image_as_single)
        self.split_wide_action = QAction("横長画像を左右分割", self, checkable=True)
        self.split_wide_action.triggered.connect(self.set_split_wide_image)
        view_menu.addAction(self.single_first_action)
        view_menu.addAction(self.wide_single_action)
        view_menu.addAction(self.split_wide_action)
        view_menu.addSeparator()

        self.fit_window_action = QAction("ウィンドウに合わせる", self, checkable=True)
        self.fit_window_action.triggered.connect(lambda: self.set_fit_mode("fit_window"))
        self.fit_no_upscale_action = QAction("ウィンドウに合わせる（拡大しない）", self, checkable=True)
        self.fit_no_upscale_action.triggered.connect(lambda: self.set_fit_mode("fit_no_upscale"))
        self.fit_width_action = QAction("幅に合わせる", self, checkable=True)
        self.fit_width_action.triggered.connect(lambda: self.set_fit_mode("fit_width"))
        self.fit_height_action = QAction("高さに合わせる", self, checkable=True)
        self.fit_height_action.triggered.connect(lambda: self.set_fit_mode("fit_height"))
        self.actual_size_action = QAction("原寸表示", self, checkable=True)
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

        self.smooth_scaling_action = QAction("高品質拡大縮小", self, checkable=True)
        self.smooth_scaling_action.triggered.connect(self.set_smooth_scaling)
        view_menu.addAction(self.smooth_scaling_action)

        alignment_menu = view_menu.addMenu("横位置")
        self.align_left_action = QAction("左寄せ", self, checkable=True)
        self.align_left_action.triggered.connect(lambda: self.set_horizontal_alignment("left"))
        self.align_center_action = QAction("中央", self, checkable=True)
        self.align_center_action.triggered.connect(lambda: self.set_horizontal_alignment("center"))
        self.align_right_action = QAction("右寄せ", self, checkable=True)
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

        fullscreen_action = QAction("全画面", self)
        fullscreen_action.setShortcut("F")
        fullscreen_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.TOGGLE_FULLSCREEN)
        )
        view_menu.addAction(fullscreen_action)
        self.hide_ui_fullscreen_action = QAction("全画面時にUIを隠す", self, checkable=True)
        self.hide_ui_fullscreen_action.triggered.connect(self.set_hide_ui_in_fullscreen)
        view_menu.addAction(self.hide_ui_fullscreen_action)
        self.hide_cursor_fullscreen_action = QAction("全画面時にカーソルを隠す", self, checkable=True)
        self.hide_cursor_fullscreen_action.triggered.connect(self.set_hide_cursor_in_fullscreen)
        view_menu.addAction(self.hide_cursor_fullscreen_action)
        self.page_list_action = QAction("ページ一覧", self, checkable=True)
        self.page_list_action.triggered.connect(self.set_page_list_visible)
        view_menu.addAction(self.page_list_action)
        view_menu.addSeparator()

        rotate_left_action = QAction("左に回転", self)
        rotate_left_action.setShortcut("Ctrl+Left")
        rotate_left_action.triggered.connect(self.rotate_left)
        rotate_right_action = QAction("右に回転", self)
        rotate_right_action.setShortcut("Ctrl+Right")
        rotate_right_action.triggered.connect(self.rotate_right)
        reset_rotation_action = QAction("回転を解除", self)
        reset_rotation_action.setShortcut("Ctrl+0")
        reset_rotation_action.triggered.connect(self.reset_rotation)
        view_menu.addAction(rotate_left_action)
        view_menu.addAction(rotate_right_action)
        view_menu.addAction(reset_rotation_action)
        view_menu.addSeparator()
        self.magnifier_action = QAction("拡大鏡", self, checkable=True)
        self.magnifier_action.setShortcut("M")
        self.magnifier_action.triggered.connect(self.set_magnifier_enabled)
        magnifier_settings_action = QAction("拡大鏡の設定", self)
        magnifier_settings_action.triggered.connect(self.set_magnifier_options_dialog)
        view_menu.addAction(self.magnifier_action)
        view_menu.addAction(magnifier_settings_action)

        slideshow_menu = menu_bar.addMenu("スライドショー")
        self.slideshow_action = QAction("開始/停止", self, checkable=True)
        self.slideshow_action.setShortcut("S")
        self.slideshow_action.triggered.connect(self.toggle_slideshow)
        slideshow_interval_action = QAction("間隔を設定", self)
        slideshow_interval_action.triggered.connect(self.set_slideshow_interval_dialog)
        slideshow_menu.addAction(self.slideshow_action)
        slideshow_menu.addAction(slideshow_interval_action)

        self.bookmark_menu = menu_bar.addMenu("ブックマーク")
        self._rebuild_bookmark_menu()

        settings_menu = menu_bar.addMenu("設定")
        gap_action = QAction("画像間の余白", self)
        gap_action.triggered.connect(self.set_gap_dialog)
        cache_size_action = QAction("キャッシュ上限", self)
        cache_size_action.triggered.connect(self.set_cache_size_dialog)
        background_color_action = QAction("背景色", self)
        background_color_action.triggered.connect(self.set_background_color_dialog)
        thumbnail_size_action = QAction("サムネイルサイズ", self)
        thumbnail_size_action.triggered.connect(self.set_thumbnail_size_dialog)
        brightness_action = QAction("明るさ", self)
        brightness_action.triggered.connect(self.set_brightness_dialog)
        contrast_action = QAction("コントラスト", self)
        contrast_action.triggered.connect(self.set_contrast_dialog)
        gamma_action = QAction("ガンマ", self)
        gamma_action.triggered.connect(self.set_gamma_dialog)
        reset_adjustments_action = QAction("画像補正をリセット", self)
        reset_adjustments_action.triggered.connect(self.reset_image_adjustments)
        settings_menu.addAction(gap_action)
        settings_menu.addAction(cache_size_action)
        settings_menu.addAction(background_color_action)
        settings_menu.addAction(thumbnail_size_action)
        settings_menu.addSeparator()
        settings_menu.addAction(brightness_action)
        settings_menu.addAction(contrast_action)
        settings_menu.addAction(gamma_action)
        settings_menu.addAction(reset_adjustments_action)

        move_menu = menu_bar.addMenu("移動")
        self.history_back_action = QAction("表示履歴を戻る", self)
        self.history_back_action.setShortcut("Alt+Left")
        self.history_back_action.triggered.connect(self.go_back_in_page_history)
        self.history_forward_action = QAction("表示履歴を進む", self)
        self.history_forward_action.setShortcut("Alt+Right")
        self.history_forward_action.triggered.connect(self.go_forward_in_page_history)
        next_action = QAction("次ページ", self)
        next_action.setShortcut(Qt.Key.Key_Right)
        next_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.NEXT_PAGE)
        )
        previous_action = QAction("前ページ", self)
        previous_action.setShortcut(Qt.Key.Key_Left)
        previous_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.PREVIOUS_PAGE)
        )
        next_one_page_action = QAction("1ページ進む", self)
        next_one_page_action.setShortcut("Shift+Right")
        next_one_page_action.triggered.connect(self.next_one_page)
        previous_one_page_action = QAction("1ページ戻る", self)
        previous_one_page_action.setShortcut("Shift+Left")
        previous_one_page_action.triggered.connect(self.previous_one_page)
        next_book_action = QAction("次の本", self)
        next_book_action.setShortcut("Ctrl+PgDown")
        next_book_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.NEXT_BOOK)
        )
        previous_book_action = QAction("前の本", self)
        previous_book_action.setShortcut("Ctrl+PgUp")
        previous_book_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.PREVIOUS_BOOK)
        )
        go_to_page_action = QAction("ページ指定", self)
        go_to_page_action.setShortcut("G")
        go_to_page_action.triggered.connect(self.go_to_page_dialog)
        first_action = QAction("先頭", self)
        first_action.setShortcut(Qt.Key.Key_Home)
        first_action.triggered.connect(
            lambda _checked=False: self.dispatch_command(commands.FIRST_PAGE)
        )
        last_action = QAction("最後", self)
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

        help_menu = menu_bar.addMenu("ヘルプ")
        shortcuts_action = QAction("ショートカット一覧", self)
        shortcuts_action.triggered.connect(self.show_shortcuts_help)
        help_menu.addAction(shortcuts_action)
        about_action = QAction("NivisViewerについて／診断情報", self)
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
        shortcuts = [
            ("Space", self.next_page_or_scroll),
            ("Backspace", self.previous_page_or_scroll),
            ("PgDown", self.next_page_or_scroll),
            ("PgUp", self.previous_page_or_scroll),
            ("+", lambda: self.dispatch_command(commands.ZOOM_IN)),
            ("=", lambda: self.dispatch_command(commands.ZOOM_IN)),
            ("-", lambda: self.dispatch_command(commands.ZOOM_OUT)),
            ("0", lambda: self.dispatch_command(commands.FIT_WINDOW)),
            ("Esc", self._handle_escape),
            ("D", lambda: self.dispatch_command(commands.TOGGLE_SPREAD)),
            ("R", lambda: self.dispatch_command(commands.TOGGLE_READING_DIRECTION)),
            ("B", self.toggle_current_bookmark),
            ("Ctrl+B", self.toggle_current_bookmark),
        ]
        for key, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)

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
        self.viewer.set_gap(self.gap)
        self.viewer.set_join_spread_pages(self.join_spread_pages)
        self.viewer.set_rotation_angle(self.rotation_angle)
        self.viewer.set_smooth_scaling(self.smooth_scaling)
        self.viewer.set_horizontal_alignment(self.horizontal_alignment)
        self.viewer.set_magnifier_options(zoom=self.magnifier_zoom, size=self.magnifier_size)
        self.viewer.set_magnifier_enabled(self.magnifier_enabled)
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
            for index in range(self.model.total_pages):
                cached = self.image_cache.get(index)
                if cached is not None:
                    self._update_page_list_thumbnail(cached)
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
                if direction in {"right_next", "left_next"}
                else "right_next"
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
                "viewer_cache_max_memory_mib",
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
        self.viewer_cache_memory_mib = int(values["cache_memory_mib"])
        self.image_cache.set_cache_byte_budget_mib(
            self.viewer_cache_memory_mib
        )

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
        else:
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
        self.smooth_scaling_action.setChecked(self.smooth_scaling)
        self.align_left_action.setChecked(self.horizontal_alignment == "left")
        self.align_center_action.setChecked(self.horizontal_alignment == "center")
        self.align_right_action.setChecked(self.horizontal_alignment == "right")
        self.slideshow_action.setChecked(self.slideshow_timer.isActive())
        self.reopen_last_action.setChecked(self.reopen_last_on_start)
        self.recursive_folder_action.setChecked(self.recursive_folder)
        self.sort_descending_action.setChecked(self.sort_descending)
        self.auto_open_adjacent_book_action.setChecked(self.auto_open_adjacent_book)
        self.hide_ui_fullscreen_action.setChecked(self.hide_ui_in_fullscreen)
        self.hide_cursor_fullscreen_action.setChecked(self.hide_cursor_in_fullscreen)
        self.page_list_action.setChecked(self.show_page_list)
        self.magnifier_action.setChecked(self.magnifier_enabled)
        if hasattr(self, "history_back_action"):
            self.history_back_action.setEnabled(bool(self._page_history_back))
        if hasattr(self, "history_forward_action"):
            self.history_forward_action.setEnabled(bool(self._page_history_forward))

    def open_dialog(self) -> None:
        start = self.settings.get("last_open_path") or str(Path.home())
        extensions = sorted(SUPPORTED_EXTENSIONS | ARCHIVE_EXTENSIONS | PDF_EXTENSIONS)
        patterns = " ".join(f"*{extension}" for extension in extensions)
        image_filter = f"画像/書庫 ({patterns});;すべてのファイル (*.*)"
        path, _ = QFileDialog.getOpenFileName(self, "画像、ZIP/CBZ、またはフォルダを開く", start, image_filter)
        if path:
            self._request_open_path(path)
            return

        folder = QFileDialog.getExistingDirectory(self, "フォルダを開く", start)
        if folder:
            self._request_open_path(folder)

    def open_path(
        self,
        path: str | Path,
        *,
        folder_snapshot: FolderListingSnapshot | None = None,
        preserve_current_page: bool = False,
    ) -> bool:
        if not preserve_current_page:
            self._reload_page_index = None
        self.viewer.cancel_pending_canvas_click()
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
        self._save_current_reading_position()
        self._active_request_id += 1
        suffix = Path(path).suffix.lower()
        self.book_session.open_book_async(
            path,
            recursive_folder=self.recursive_folder,
            sort_descending=self.sort_descending,
            trace_id=self._active_open_trace_id,
            folder_snapshot=folder_snapshot,
        )
        if suffix in ARCHIVE_EXTENSIONS | PDF_EXTENSIONS:
            self._set_status_override(
                "PDFを読み込んでいます…"
                if suffix in PDF_EXTENSIONS
                else "書庫を読み込んでいます…"
            )
        return True

    def _finish_opened_book(
        self,
        opened: BookOpened,
        *,
        modal_on_empty: bool,
    ) -> bool:
        self._clear_status_override()
        if self.model.total_pages == 0:
            self._cancel_interactive_open()
            if modal_on_empty:
                QMessageBox.warning(self, "画像なし", "対応画像が見つかりませんでした。")
            else:
                self._set_status_override("表示可能な画像がありません", 5000)
            self.book_session.close_book()
            self._metadata_book_path = ""
            self._metadata_book_item_type = ""
            self.viewer.clear()
            self._clear_page_history()
            self._rebuild_page_list()
            self._update_slider()
            self._update_status()
            return False

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

        if self.metadata_store is not None:
            self.metadata_store.record_book_opened(
                self._metadata_book_path,
                item_type=self._metadata_book_item_type,
                start_page_index=(
                    saved_page_index
                    if (
                        configured_open_position
                        and not self._resumes_last_book_position()
                        and saved_page_index is not None
                    )
                    else self.model.focused_index
                ),
                total_pages=self.model.total_pages,
            )

        opened_path = str(opened.requested_path)
        self.settings["last_open_path"] = opened_path
        self._add_recent_path(opened_path)
        self.image_cache.set_cache_size(self.cache_size)
        self._clear_page_history()
        self._rebuild_page_list()
        self._first_frame_image_id = self.model.image_id_at(
            self.model.focused_index
        )
        self._refresh_view()
        performance_trace.mark(
            self._active_open_trace_id,
            "viewer.initial_requests.completed",
        )
        self.book_changed.emit(self, opened_path)
        return True

    def _on_async_book_opened(self, opened: BookOpened) -> None:
        if self._shutdown_prepared:
            return
        self._finish_opened_book(opened, modal_on_empty=False)

    def _on_async_book_open_failed(self, failed: AsyncBookOpenFailed) -> None:
        self._reload_page_index = None
        self._cancel_interactive_open()
        if self._shutdown_prepared or failed.cancelled:
            return
        self._set_status_override(
            failed.message or "書庫を開けません",
            5000,
        )

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

    def _save_current_reading_position(self) -> None:
        if not self._current_book_key or self.model.total_pages <= 0:
            return
        positions = self.settings.get("reading_positions")
        if not isinstance(positions, dict):
            positions = {}
        positions[self._current_book_key] = self.model.focused_index
        while len(positions) > 100:
            oldest_key = next(iter(positions))
            del positions[oldest_key]
        self.settings["reading_positions"] = positions
        if self.metadata_store is not None:
            self.metadata_store.flush()

    def _queue_metadata_progress(self) -> None:
        if (
            self.metadata_store is None
            or not self._metadata_book_path
            or self.model.total_pages <= 0
        ):
            return
        self.metadata_store.update_reading_progress(
            self._metadata_book_path,
            page_index=self.model.focused_index,
            total_pages=self.model.total_pages,
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
        self._page_history_back.clear()
        self._page_history_forward.clear()
        self._sync_actions()

    def _record_page_history(self, previous_index: int) -> None:
        if not 0 <= previous_index < self.model.total_pages:
            return
        if previous_index == self.model.focused_index:
            return
        if self._page_history_back and self._page_history_back[-1] == previous_index:
            self._page_history_forward.clear()
            self._sync_actions()
            return
        self._page_history_back.append(previous_index)
        del self._page_history_back[:-100]
        self._page_history_forward.clear()
        self._sync_actions()

    def _go_to_index_with_history(self, page_index: int, *, raw: bool = False) -> bool:
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
        self._record_page_history(old)
        self.book_session.notify_page_changed()
        self._refresh_view()
        return True

    def _go_to_model_move_with_history(self, move) -> bool:
        if self.model.total_pages <= 0:
            return False
        old = self.model.focused_index
        old_start = self.model.current_index
        move()
        if self.model.current_index == old_start and self.model.focused_index == old:
            return False
        self._record_page_history(old)
        self.book_session.notify_page_changed()
        self._refresh_view()
        return True

    def _on_page_navigation_changed(self, previous_index: int) -> None:
        self.viewer.cancel_pending_canvas_click()
        self._record_page_history(previous_index)
        self.book_session.notify_page_changed()
        self._refresh_view()

    def go_back_in_page_history(self) -> None:
        if self.model.total_pages <= 0 or not self._page_history_back:
            return
        current = self.model.focused_index
        target = self._page_history_back.pop()
        if 0 <= current < self.model.total_pages:
            self._page_history_forward.append(current)
            del self._page_history_forward[:-100]
        self.model.go_to_index(target)
        self.book_session.notify_page_changed()
        self._refresh_view()
        self._sync_actions()

    def go_forward_in_page_history(self) -> None:
        if self.model.total_pages <= 0 or not self._page_history_forward:
            return
        current = self.model.focused_index
        target = self._page_history_forward.pop()
        if 0 <= current < self.model.total_pages:
            self._page_history_back.append(current)
            del self._page_history_back[:-100]
        self.model.go_to_index(target)
        self.book_session.notify_page_changed()
        self._refresh_view()
        self._sync_actions()

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
            empty_action = QAction("履歴なし", self)
            empty_action.setEnabled(False)
            self.recent_menu.addAction(empty_action)
            return

        for path in recent[:12]:
            action = QAction(path, self)
            action.triggered.connect(lambda checked=False, value=path: self._open_recent_path(value))
            self.recent_menu.addAction(action)
        self.recent_menu.addSeparator()
        clear_action = QAction("履歴をクリア", self)
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
        page_index = self.model.focused_index
        cached = self.image_cache.get(page_index)
        resolution = ""
        if cached is not None and cached.original_size is not None:
            resolution = f"{cached.original_size[0]} x {cached.original_size[1]}"
        elif cached is not None and cached.error:
            resolution = f"読み込みエラー: {cached.error}"

        QMessageBox.information(
            self,
            "ページ情報",
            "\n".join(
                [
                    f"ページ: {page_index + 1} / {self.model.total_pages}",
                    f"パス: {self.model.display_path_for_index(page_index)}",
                    f"サイズ: {self._format_file_size(self.model.file_size_for_index(page_index)) or '-'}",
                    f"解像度: {resolution or '-'}",
                    f"表示モード: {self.view_mode}",
                    f"綴じ方向: {'右綴じ' if self.reading_direction == 'rtl' else '左綴じ'}",
                    f"画像補正: 明るさ {self.brightness:.2f} / コントラスト {self.contrast:.2f} / ガンマ {self.gamma:.2f}",
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
            self._set_status_override("現在確認できません", 3000)
            return
        self._pending_path_probe = (request_id, generation, purpose, path)
        self._set_status_override("場所を確認しています…")

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
            PathAvailability.MISSING: "見つかりません",
            PathAvailability.UNAVAILABLE: "現在アクセスできません",
            PathAvailability.ERROR: "確認できません",
        }.get(result.state, "確認できません")
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
            "現在の表示をPNG保存",
            default_name,
            "PNG画像 (*.png)",
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if not self.viewer.grab().save(path, "PNG"):
            QMessageBox.warning(self, "保存エラー", "現在の表示を保存できませんでした。")

    def reload_current_book(self) -> None:
        if self._opened_path:
            self._reload_page_index = self.model.focused_index
            self.open_path(self._opened_path, preserve_current_page=True)

    def set_reopen_last_on_start(self, checked: bool) -> None:
        self.reopen_last_on_start = checked
        self._update_shared_setting("reopen_last_on_start", checked)
        self._sync_actions()

    def set_recursive_folder(self, checked: bool) -> None:
        self.recursive_folder = checked
        self._update_shared_setting("recursive_folder", checked)
        self._sync_actions()
        self.reload_current_book()

    def set_sort_descending(self, checked: bool) -> None:
        self.sort_descending = checked
        self._update_shared_setting("sort_descending", checked)
        self._sync_actions()
        self.reload_current_book()

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

    def set_magnifier_enabled(self, checked: bool) -> None:
        self.magnifier_enabled = checked
        self._update_shared_setting("magnifier_enabled", checked)
        self.viewer.set_magnifier_enabled(checked)
        self._sync_actions()

    def _on_page_list_dock_visibility_changed(self, visible: bool) -> None:
        if self.isFullScreen() and self.hide_ui_in_fullscreen:
            return
        self.show_page_list = visible
        self._update_shared_setting("show_page_list", visible)
        if visible and self._page_list_dirty:
            self._rebuild_page_list()
        if hasattr(self, "page_list_action"):
            self._sync_actions()

    def set_gap_dialog(self) -> None:
        gap, accepted = QInputDialog.getInt(self, "画像間の余白", "ピクセル:", self.gap, 0, 100, 1)
        if not accepted:
            return
        self.gap = gap
        self._update_shared_setting("gap", gap)
        self.viewer.set_gap(gap)
        self._refresh_view()

    def set_cache_size_dialog(self) -> None:
        cache_size, accepted = QInputDialog.getInt(
            self,
            "キャッシュ上限",
            "ページ数:",
            self.cache_size,
            1,
            100,
            1,
        )
        if not accepted:
            return
        self.cache_size = cache_size
        self._update_shared_setting("cache_size", cache_size)
        self.image_cache.set_cache_size(cache_size)
        if self.model.total_pages > 0:
            self._refresh_view()

    def set_background_color_dialog(self) -> None:
        color = QColorDialog.getColor(self.viewer.background_color, self, "背景色")
        if not color.isValid():
            return
        self.background_color = color.name()
        self._update_shared_setting("background_color", self.background_color)
        self.viewer.set_background_color(self.background_color)

    def set_thumbnail_size_dialog(self) -> None:
        size, accepted = QInputDialog.getInt(
            self,
            "サムネイルサイズ",
            "ピクセル:",
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
        for index in range(self.model.total_pages):
            cached = self.image_cache.get(index)
            if cached is not None:
                self._update_page_list_thumbnail(cached)

    def set_brightness_dialog(self) -> None:
        value, accepted = QInputDialog.getDouble(self, "明るさ", "倍率:", self.brightness, 0.1, 3.0, 2)
        if accepted:
            self._set_image_adjustments(brightness=value)

    def set_contrast_dialog(self) -> None:
        value, accepted = QInputDialog.getDouble(self, "コントラスト", "倍率:", self.contrast, 0.1, 3.0, 2)
        if accepted:
            self._set_image_adjustments(contrast=value)

    def set_gamma_dialog(self) -> None:
        value, accepted = QInputDialog.getDouble(self, "ガンマ", "値:", self.gamma, 0.1, 5.0, 2)
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
        self.page_list.clear()
        self._rebuild_page_list()
        if self.model.total_pages > 0:
            self._refresh_view()

    def set_magnifier_options_dialog(self) -> None:
        zoom, accepted = QInputDialog.getDouble(
            self,
            "拡大鏡の倍率",
            "倍率:",
            self.magnifier_zoom,
            1.1,
            8.0,
            1,
        )
        if not accepted:
            return
        size, accepted = QInputDialog.getInt(
            self,
            "拡大鏡のサイズ",
            "ピクセル:",
            self.magnifier_size,
            80,
            600,
            10,
        )
        if not accepted:
            return
        self.magnifier_zoom = zoom
        self.magnifier_size = size
        self._update_shared_setting("magnifier_zoom", zoom)
        self._update_shared_setting("magnifier_size", size)
        self.viewer.set_magnifier_options(zoom=zoom, size=size)

    def open_next_book(self) -> None:
        self._open_adjacent_book(1)

    def open_previous_book(self) -> None:
        self._open_adjacent_book(-1)

    def _open_adjacent_book(self, direction: int) -> None:
        if self._adjacent_book_handler is None:
            self._set_status_override("移動できる書庫がありません", 2500)
            return
        result = self._adjacent_book_handler(self, direction)
        if result == "boundary":
            message = "前の書庫はありません" if direction < 0 else "次の書庫はありません"
            self._set_status_override(message, 2500)
        elif result == "unavailable":
            self._set_status_override("移動できる書庫がありません", 2500)

    def show_adjacent_book_searching(self, direction: int) -> None:
        label = "前" if direction < 0 else "次"
        self._set_status_override(f"{label}の本を検索中…")

    def complete_adjacent_book_search(self, direction: int, result: str) -> None:
        if result == "opened":
            self._status_override_token += 1
            self._status_override_message = None
            self._update_status()
        elif result == "boundary":
            message = "前の書庫はありません" if direction < 0 else "次の書庫はありません"
            self._set_status_override(message, 2500)
        elif result in {"unavailable", "error"}:
            self._set_status_override("移動できる書庫がありません", 2500)

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

    def _rebuild_page_list(self) -> None:
        if not self.page_list_dock.isVisible():
            self._page_list_dirty = True
            if self.page_list.count():
                self._updating_page_list = True
                try:
                    self.page_list.clear()
                finally:
                    self._updating_page_list = False
            return
        self._page_list_dirty = False
        self._updating_page_list = True
        try:
            self.page_list.clear()
            filter_text = self.page_list_filter.text().casefold().strip()
            for index, image_id in enumerate(self.model.image_ids):
                label = f"{index + 1}: {Path(image_id).name}"
                if filter_text and filter_text not in label.casefold():
                    continue
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, index)
                self.page_list.addItem(item)
        finally:
            self._updating_page_list = False
        for index in range(self.model.total_pages):
            cached = self.image_cache.get(index)
            if cached is not None:
                self._update_page_list_thumbnail(cached)
        self._sync_page_list_selection()

    def _sync_page_list_selection(self) -> None:
        if self._page_list_dirty:
            return
        if self.model.total_pages <= 0:
            return
        self._updating_page_list = True
        target_item = None
        focused_index = self.model.focused_index
        for row in range(self.page_list.count()):
            item = self.page_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == focused_index:
                target_item = item
                break
        if target_item is None:
            self.page_list.setCurrentRow(-1)
        else:
            self.page_list.setCurrentItem(target_item)
            self.page_list.scrollToItem(target_item)
        self._updating_page_list = False

    def _update_page_list_thumbnail(self, cached: CachedImage) -> None:
        if self._page_list_dirty:
            return
        if cached.qimage is None or cached.error:
            return
        item = None
        for row in range(self.page_list.count()):
            candidate = self.page_list.item(row)
            if candidate is not None and candidate.data(Qt.ItemDataRole.UserRole) == cached.page_index:
                item = candidate
                break
        if item is None:
            return
        item.setIcon(PageThumbnailProvider.create_icon(cached.qimage, self.thumbnail_size))

    def _on_page_list_row_changed(self, row: int) -> None:
        if self._updating_page_list or row < 0:
            return
        item = self.page_list.item(row)
        if item is None:
            return
        page_index = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(page_index, int) or not 0 <= page_index < self.model.total_pages:
            return
        self._go_to_index_with_history(page_index)

    def _rebuild_bookmark_menu(self) -> None:
        self.bookmark_menu.clear()
        has_book = bool(self._current_book_key and self.model.total_pages > 0)
        pages = self._bookmark_pages() if has_book else []

        toggle_text = "現在ページをブックマーク"
        if has_book and self.model.current_index in pages:
            toggle_text = "現在ページのブックマークを解除"
        toggle_action = QAction(toggle_text, self)
        toggle_action.setEnabled(has_book)
        toggle_action.triggered.connect(self.toggle_current_bookmark)
        self.bookmark_menu.addAction(toggle_action)

        next_action = QAction("次のブックマーク", self)
        next_action.setEnabled(bool(pages))
        next_action.triggered.connect(self.next_bookmark)
        previous_action = QAction("前のブックマーク", self)
        previous_action.setEnabled(bool(pages))
        previous_action.triggered.connect(self.previous_bookmark)
        self.bookmark_menu.addAction(next_action)
        self.bookmark_menu.addAction(previous_action)
        self.bookmark_menu.addSeparator()

        if pages:
            for page in pages:
                action = QAction(f"{page + 1} ページ", self)
                action.triggered.connect(lambda checked=False, value=page: self._go_to_bookmark(value))
                self.bookmark_menu.addAction(action)
            self.bookmark_menu.addSeparator()
        else:
            empty_action = QAction("ブックマークなし", self)
            empty_action.setEnabled(False)
            self.bookmark_menu.addAction(empty_action)
            self.bookmark_menu.addSeparator()

        clear_action = QAction("この本のブックマークをクリア", self)
        clear_action.setEnabled(bool(pages))
        clear_action.triggered.connect(self.clear_bookmarks_for_current_book)
        self.bookmark_menu.addAction(clear_action)

    def _refresh_view(self) -> None:
        if hasattr(self, "fullscreen_chrome"):
            self.fullscreen_chrome.reevaluate_visibility()
        if self._awaiting_first_frame:
            focused_image_id = self.model.image_id_at(self.model.focused_index)
            if focused_image_id is not None:
                # Navigation can occur before the original first frame paints.
                # Move the gate to the newly requested identity so an old page
                # cannot keep spread-partner work blocked indefinitely.
                self._first_frame_image_id = focused_image_id
        self._active_request_id += 1
        spread = self.model.spread_at()
        self._visible_page_indexes = tuple(slot.page_index for slot in spread.slots)
        self.image_cache.set_render_spec(self._current_pdf_render_spec())
        self._display_unit = self._display_unit.cancel_loading()
        self._display_unit = ViewerDisplayUnit.create(
            request_id=self._active_request_id,
            generation=self.image_cache.generation,
            focused_page_identity=self.model.focused_page_identity,
            pages=(
                (
                    slot.page_index,
                    self.model.page_identity(slot.page_index) or slot.image_id,
                    slot.image_id,
                )
                for slot in spread.slots
            ),
        )
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
            self._prepare_deferred_pdf_prefetch(
                request_center,
                gated_visible_indexes,
            )
        else:
            self._cancel_deferred_pdf_prefetch()
            self._preload_image_source(
                request_center,
                gated_visible_indexes,
                immediate_only=first_frame_gate,
            )
        if _DISPLAY_LOG.isEnabledFor(logging.DEBUG):
            _DISPLAY_LOG.debug(
                "display unit request=%s generation=%s focused=%s slots=%r gate=%s",
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
        self._render_spread(spread, self._active_request_id)

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

    def _configured_prefetch_indexes(
        self,
        center_index: int,
        visible_indexes: tuple[int, ...],
        *,
        forward_units: int,
        backward_units: int,
        direction: int,
    ) -> tuple[int, ...]:
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
        units = list(forward + backward)
        if not self.prefetch_direction_priority_enabled or direction == 0:
            units.sort(
                key=lambda unit: min(
                    abs(index - center_index) for index in unit
                )
            )
        visible = set(visible_indexes)
        indexes: list[int] = []
        for unit in units:
            for index in unit:
                if index not in visible and index not in indexes:
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
        prefetch_indexes = (
            tuple()
            if immediate_only
            else self._configured_prefetch_indexes(
                center_index,
                visible_indexes,
                forward_units=self.image_prefetch_forward_units,
                backward_units=self.image_prefetch_backward_units,
                direction=direction,
            )
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
            prefetch_indexes=prefetch_indexes,
        )

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
        self.image_cache.preload_around(
            center_index,
            radius=0,
            visible_indexes=self._pdf_prefetch_visible_indexes,
            preferred_direction=(
                self._pdf_prefetch_direction
                if self.prefetch_direction_priority_enabled
                else 0
            ),
            prefetch_indexes=self._configured_prefetch_indexes(
                center_index,
                self._pdf_prefetch_visible_indexes,
                forward_units=self.pdf_prefetch_forward_units,
                backward_units=self.pdf_prefetch_backward_units,
                direction=self._pdf_prefetch_direction,
            ),
        )

    def _cancel_deferred_pdf_prefetch(self) -> None:
        self._pdf_prefetch_timer.stop()
        self._pdf_prefetch_source = None
        self._pdf_prefetch_generation = -1
        self._pdf_prefetch_visible_indexes = tuple()
        self._pdf_prefetch_direction = 0

    def _render_spread(self, spread, request_id: int) -> None:
        if (
            self._shutdown_prepared
            or request_id != self._active_request_id
            or self._display_unit.request_id != request_id
            or self._display_unit.generation != self.image_cache.generation
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
                self._display_unit = self._display_unit.transition(
                    page_index=slot.page_index,
                    image_id=slot.image_id,
                    generation=self.image_cache.generation,
                    state=state,
                    error=(
                        None
                        if state is ViewerSlotState.LOADING
                        else "画像ソースを利用できません。"
                    ),
                )
                if state is ViewerSlotState.LOADING:
                    target_is_terminal = False
                else:
                    pages.append(
                        ViewerWidget.error_page(
                            slot.page_index,
                            slot.image_id,
                            "画像ソースを利用できません。",
                        )
                    )
            elif cached.error:
                self._display_unit = self._display_unit.transition(
                    page_index=slot.page_index,
                    image_id=slot.image_id,
                    generation=cached.generation,
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
                self._display_unit = self._display_unit.transition(
                    page_index=slot.page_index,
                    image_id=slot.image_id,
                    generation=cached.generation,
                    state=ViewerSlotState.READY,
                )
                pages.extend(self._viewer_images_for_cached(cached, split_allowed=spread.is_single))
            else:
                self._display_unit = self._display_unit.transition(
                    page_index=slot.page_index,
                    image_id=slot.image_id,
                    generation=cached.generation,
                    state=ViewerSlotState.FAILED,
                    error="画像を表示できません。",
                )
                pages.append(ViewerWidget.error_page(slot.page_index, slot.image_id, "画像を表示できません。"))

        # A navigation target replaces the canvas only after every slot has
        # reached a terminal state. Until then the last complete frame stays
        # owned by ViewerWidget.
        if (
            target_is_terminal
            and request_id != self._applied_display_request_id
        ):
            self.viewer.set_pages(spread, pages)
            self._applied_display_request_id = request_id
        self._update_slider()
        self._update_status()
        self._sync_page_list_selection()
        self._rebuild_bookmark_menu()
        self._sync_actions()

    def _viewer_images_for_cached(self, cached: CachedImage, *, split_allowed: bool) -> list[ViewerImage]:
        if cached.qimage is None or cached.original_size is None:
            return [ViewerWidget.error_page(cached.page_index, cached.image_id, "画像を表示できません。")]

        width, height = cached.original_size
        should_split = (
            self.split_wide_image
            and split_allowed
            and height > 0
            and width / height >= 1.25
            and width >= 2
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
                )
            ]

        left_width = width // 2
        right_width = width - left_width
        left = cached.qimage.copy(0, 0, left_width, height)
        right = cached.qimage.copy(left_width, 0, right_width, height)
        left_page = ViewerWidget.from_qimage(cached.page_index, f"{cached.image_id}#left", left, (left_width, height))
        right_page = ViewerWidget.from_qimage(cached.page_index, f"{cached.image_id}#right", right, (right_width, height))
        if self.reading_direction == "rtl":
            return [right_page, left_page]
        return [left_page, right_page]

    def _on_cache_page_loaded(self, cached: CachedImage) -> None:
        if (
            self._shutdown_prepared
            or cached.generation != self.image_cache.generation
        ):
            return
        self._display_unit = self._display_unit.transition(
            page_index=cached.page_index,
            image_id=cached.image_id,
            generation=cached.generation,
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
            self._update_page_list_thumbnail(cached)
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
                    )
            return
        if cached.page_index not in self._visible_page_indexes:
            self._update_page_list_thumbnail(cached)
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
                )
        if cached.page_index in self._visible_page_indexes:
            self._arm_deferred_pdf_prefetch()
        self._update_page_list_thumbnail(cached)
        self._render_spread(self.model.spread_at(), self._active_request_id)

    def _begin_interactive_open(self) -> None:
        if self._awaiting_first_frame:
            self.interactive_open_cancelled.emit(self)
        self._awaiting_first_frame = True
        self._first_frame_image_id = None
        self.interactive_open_started.emit(self)

    def _cancel_interactive_open(self) -> None:
        if not self._awaiting_first_frame:
            return
        self._awaiting_first_frame = False
        self._first_frame_image_id = None
        self.interactive_open_cancelled.emit(self)

    def _on_viewer_content_painted(self, image_ids: object) -> None:
        if isinstance(image_ids, tuple):
            self._arm_deferred_pdf_prefetch()
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
        next_side = (
            "right"
            if self.viewer_canvas_click_direction == "right_next"
            else "left"
        )
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
            commands.CLOSE_VIEWER: self.close,
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
        if button == "back":
            self.dispatch_command(self.mouse_back_button_action)
        elif button == "forward":
            self.dispatch_command(self.mouse_forward_button_action)

    def _handle_escape(self) -> None:
        self.viewer.cancel_pending_canvas_click()
        if self.viewer.cancel_mouse_gesture():
            return
        self.exit_fullscreen()

    def _show_viewer_context_menu(self, position) -> None:
        menu = QMenu(self)
        back_history_action = menu.addAction("表示履歴を戻る")
        forward_history_action = menu.addAction("表示履歴を進む")
        back_history_action.setEnabled(bool(self._page_history_back))
        forward_history_action.setEnabled(bool(self._page_history_forward))
        menu.addSeparator()
        next_action = menu.addAction("次ページ")
        previous_action = menu.addAction("前ページ")
        menu.addSeparator()
        bookmark_action = menu.addAction("現在ページをブックマーク")
        copy_path_action = menu.addAction("パスをコピー")
        copy_image_action = menu.addAction("画像をコピー")
        copy_view_action = menu.addAction("表示をコピー")
        page_info_action = menu.addAction("ページ情報")
        open_location_action = menu.addAction("場所を開く")
        menu.addSeparator()
        fullscreen_action = menu.addAction("全画面切替")

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
        QMessageBox.information(
            self,
            "ショートカット一覧",
            "\n".join(
                [
                    "Right: 次ページ",
                    "Left: 前ページ",
                    "Alt+Left / Alt+Right: 表示履歴を戻る / 進む",
                    "Space / PageDown: 下スクロールまたは次ページ",
                    "Backspace / PageUp: 上スクロールまたは前ページ",
                    "Shift+Right: 1ページ進む",
                    "Shift+Left: 1ページ戻る",
                    "Home / End: 先頭 / 最後",
                    "G: ページ指定",
                    "D: 単ページ / 見開き切替",
                    "R: 左綴じ / 右綴じ切替",
                    "F: 全画面切替",
                    "Esc: 全画面解除",
                    "M: 拡大鏡",
                    "+ / - / Ctrl+Wheel: ズーム",
                    "0: ウィンドウに合わせる",
                    "S: スライドショー",
                    "B / Ctrl+B: ブックマーク切替",
                    "Ctrl+PageDown / Ctrl+PageUp: 次 / 前の本",
                    "Ctrl+C: 現在画像をコピー",
                    "Ctrl+Shift+C: 現在画像のパスをコピー",
                    "Ctrl+Alt+C: 現在の表示をコピー",
                    "Ctrl+I: ページ情報",
                    "Double Click: 全画面切替",
                ]
            ),
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if ExternalDropOpenController.local_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # type: ignore[override]
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
                            "ドロップした項目はNivisViewerで表示できません",
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
                f"{failures}件を開けませんでした",
                3000,
            )

    def _update_slider(self) -> None:
        self.slider.set_page_state(
            self.model.total_pages,
            self.model.focused_index,
        )

    def _update_status(self) -> None:
        if self._status_override_message is not None:
            self.status.showMessage(self._status_override_message)
            return
        if self.model.total_pages == 0:
            self.status.showMessage("画像が読み込まれていません")
            return

        focused_index = self.model.focused_index
        path = self.model.display_path_for_index(focused_index)
        page_text = f"{focused_index + 1} / {self.model.total_pages}"
        resolution = self.viewer.current_resolution_text()
        zoom = (
            f"{round(self.viewer.manual_zoom * 100)}%"
            if self.fit_mode == "manual_zoom"
            else ("100%" if self.fit_mode == "actual_size" else self.fit_mode)
        )
        size = self._format_file_size(self.model.file_size_for_index(focused_index))
        details = "    ".join(
            part for part in (path, page_text, resolution, zoom, size) if part
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
        self.page_navigation.go_to_focused_page_index(value)

    def _on_zoom_changed(self, zoom: float) -> None:
        self.fit_mode = "manual_zoom"
        self._update_shared_setting("fit_mode", self.fit_mode)
        self._sync_actions()
        self._update_status()
        self._schedule_pdf_rerender()

    def _current_pdf_render_spec(self) -> dict[int, PageRenderSpec] | None:
        if not isinstance(self.book_session.source, PdfImageSource):
            return None
        spread = self.model.spread_at()
        viewport_width = max(1, self.viewer.width())
        viewport_height = max(1, self.viewer.height())
        logical_sizes = [
            self.model.get_image_size(slot.page_index) or (360, 520)
            for slot in spread.slots
        ]
        if self.rotation_angle in {90, 270}:
            logical_sizes = [(height, width) for width, height in logical_sizes]
        layout = calculate_spread_layout(
            logical_sizes,
            (viewport_width, viewport_height),
            fit_mode=self.fit_mode,
            manual_zoom=self.viewer.manual_zoom,
            gap=self.gap,
            join_spread_pages=self.join_spread_pages,
            spread_is_single=spread.is_single,
            horizontal_alignment=self.horizontal_alignment,
        )
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
        for page_index in sorted(spec_indexes):
            width, height = self.model.get_image_size(page_index) or (360, 520)
            if self.rotation_angle in {90, 270}:
                width, height = height, width
            specs[page_index] = PageRenderSpec(
                max(1, round(width * layout.scale)),
                max(1, round(height * layout.scale)),
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
        if self.image_cache.set_render_spec(self._current_pdf_render_spec()):
            self._refresh_view()

    def set_view_mode(self, mode: str) -> None:
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
        self.reading_direction = direction
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
        self.split_wide_image = checked
        self._update_shared_setting("split_wide_image", checked)
        self._sync_actions()
        if self.model.total_pages:
            self._refresh_view()
        self.fullscreen_chrome.reevaluate_visibility()

    def set_smooth_scaling(self, checked: bool) -> None:
        self.smooth_scaling = checked
        self._update_shared_setting("smooth_scaling", checked)
        self.viewer.set_smooth_scaling(checked)
        self._sync_actions()

    def set_horizontal_alignment(self, alignment: str) -> None:
        self.horizontal_alignment = alignment
        self._update_shared_setting("horizontal_alignment", alignment)
        self.viewer.set_horizontal_alignment(alignment)
        self._sync_actions()

    def set_fit_mode(self, mode: str) -> None:
        if mode == "fit_window":
            self.viewer.reset_zoom()
        else:
            self.viewer.set_fit_mode(mode)
        self.fit_mode = mode
        self._update_shared_setting("fit_mode", mode)
        self._sync_actions()
        self._update_status()
        self._rerender_pdf()
        self.fullscreen_chrome.reevaluate_visibility()

    def zoom_in(self) -> None:
        self.viewer.set_manual_zoom(self.viewer.manual_zoom * 1.15)

    def zoom_out(self) -> None:
        self.viewer.set_manual_zoom(self.viewer.manual_zoom / 1.15)

    def rotate_left(self) -> None:
        self.rotation_angle = (self.rotation_angle - 90) % 360
        self.viewer.set_rotation_angle(self.rotation_angle)
        self._update_status()
        self._rerender_pdf()

    def rotate_right(self) -> None:
        self.rotation_angle = (self.rotation_angle + 90) % 360
        self.viewer.set_rotation_angle(self.rotation_angle)
        self._update_status()
        self._rerender_pdf()

    def reset_rotation(self) -> None:
        self.rotation_angle = 0
        self.viewer.set_rotation_angle(self.rotation_angle)
        self._update_status()
        self._rerender_pdf()

    def toggle_slideshow(self) -> None:
        if self.slideshow_timer.isActive():
            self.slideshow_timer.stop()
        else:
            if self.model.total_pages > 0:
                self.slideshow_timer.start()
        self._sync_actions()

    def set_slideshow_interval_dialog(self) -> None:
        seconds, accepted = QInputDialog.getDouble(
            self,
            "スライドショー間隔",
            "秒数:",
            self.slideshow_interval_ms / 1000,
            0.5,
            60.0,
            1,
        )
        if not accepted:
            return
        self.slideshow_interval_ms = int(seconds * 1000)
        self._update_shared_setting("slideshow_interval_ms", self.slideshow_interval_ms)
        self.slideshow_timer.setInterval(self.slideshow_interval_ms)

    def _advance_slideshow(self) -> None:
        old = self.model.current_index
        self.next_page()
        if self.model.current_index == old:
            self.slideshow_timer.stop()
            self._sync_actions()

    def next_page(self) -> None:
        moved = self.page_navigation.next_display_unit()
        if not moved and self.auto_open_adjacent_book:
            self.open_next_book()

    def previous_page(self) -> None:
        moved = self.page_navigation.previous_display_unit()
        if not moved and self.auto_open_adjacent_book:
            self.open_previous_book()

    def next_page_or_scroll(self) -> None:
        if not self.viewer.scroll_forward():
            self.next_page()

    def previous_page_or_scroll(self) -> None:
        if not self.viewer.scroll_backward():
            self.previous_page()

    def next_one_page(self) -> None:
        self.page_navigation.next_single_page()

    def previous_one_page(self) -> None:
        self.page_navigation.previous_single_page()

    def go_to_page_dialog(self) -> None:
        if self.model.total_pages <= 0:
            return
        page, accepted = QInputDialog.getInt(
            self,
            "ページ指定",
            "ページ番号:",
            self.model.current_index + 1,
            1,
            self.model.total_pages,
            1,
        )
        if accepted:
            self._go_to_index_with_history(page - 1)

    def first_page(self) -> None:
        self.slideshow_timer.stop()
        self._sync_actions()
        self.page_navigation.first_page()

    def last_page(self) -> None:
        self.slideshow_timer.stop()
        self._sync_actions()
        self.page_navigation.last_page()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self._apply_chrome_visibility()

    def exit_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        self._apply_chrome_visibility()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._apply_chrome_visibility()

    def _apply_chrome_visibility(self) -> None:
        fullscreen = self.isFullScreen()
        self.fullscreen_chrome.set_fullscreen_state(
            fullscreen,
            hide_ui=self.hide_ui_in_fullscreen,
            hide_cursor=self.hide_cursor_in_fullscreen,
        )
        self.page_list_dock.setVisible(not fullscreen and self.show_page_list)

    def _apply_cursor_visibility_policy(self) -> None:
        self.viewer.set_auto_hide_cursor(False)
        self.fullscreen_chrome.set_hide_cursor_enabled(
            self.isFullScreen() and self.hide_cursor_in_fullscreen
        )

    def prepare_shutdown(self, *, wait_msecs: int = 250) -> None:
        if self._shutdown_prepared:
            return
        self._shutdown_prepared = True
        self._path_probe_generation += 1
        self._pending_path_probe = None
        self.fullscreen_chrome.shutdown()
        self._cancel_interactive_open()
        self.viewer.cancel_mouse_gesture()
        self.viewer.cancel_pending_canvas_click()
        self.slideshow_timer.stop()
        self._pdf_render_timer.stop()
        self._cancel_deferred_pdf_prefetch()
        self._save_current_reading_position()
        self.book_session.shutdown(wait_msecs=wait_msecs)
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

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        guard = getattr(self, "_application_close_guard", None)
        if callable(guard) and not guard(self):
            event.ignore()
            return
        self.prepare_shutdown()
        self.closing.emit(self)
        super().closeEvent(event)
