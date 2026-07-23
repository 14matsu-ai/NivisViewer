from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QByteArray, QEvent, QSize, QTimer, Qt, QUrl, Signal
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
    QSlider,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .book_session import BookSession
from .config_manager import ConfigManager
from .image_cache import CachedImage, PRELOAD_RADIUS
from .image_source import ARCHIVE_EXTENSIONS, SUPPORTED_EXTENSIONS, ImageSourceError
from .thumbnail_provider import PageThumbnailProvider
from . import viewer_commands as commands
from .viewer_widget import ViewerImage, ViewerWidget


class ViewerWindow(QMainWindow):
    activated = Signal(object)
    closing = Signal(object)
    book_changed = Signal(object, str)

    def __init__(
        self,
        *,
        config_manager: ConfigManager,
        book_session: BookSession | None = None,
        open_path_handler: Callable[[str, bool | None, object], object] | None = None,
        adjacent_book_handler: Callable[[object, int], str] | None = None,
    ) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("NivisViewer")
        self.resize(1200, 820)

        self.config = config_manager
        self.settings = self.config.data
        self.book_session = book_session or BookSession(
            int(self.settings.get("cache_size", 10)),
            self,
        )
        self.model = self.book_session.model
        self.image_cache = self.book_session.image_cache
        self.image_cache.pageLoaded.connect(self._on_cache_page_loaded)
        self._open_path_handler = open_path_handler
        self._adjacent_book_handler = adjacent_book_handler
        self._shutdown_prepared = False
        self._active_request_id = 0
        self._visible_page_indexes: tuple[int, ...] = tuple()
        self._page_history_back: list[int] = []
        self._page_history_forward: list[int] = []
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
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.setInterval(max(500, self.slideshow_interval_ms))
        self.slideshow_timer.timeout.connect(self._advance_slideshow)
        self.image_cache.set_adjustments(brightness=self.brightness, contrast=self.contrast, gamma=self.gamma)

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
        return handled

    def _build_ui(self) -> None:
        self.viewer = ViewerWidget(self)
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setMinimum(1)
        self.slider.setMaximum(1)
        self.slider.setValue(1)
        self.slider.setEnabled(False)

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
        self.slider.valueChanged.connect(self._on_slider_changed)

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
        self._sync_actions()
        if refresh and self.model.total_pages:
            self._refresh_view()

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
        extensions = sorted(SUPPORTED_EXTENSIONS | ARCHIVE_EXTENSIONS)
        patterns = " ".join(f"*{extension}" for extension in extensions)
        image_filter = f"画像/書庫 ({patterns});;すべてのファイル (*.*)"
        path, _ = QFileDialog.getOpenFileName(self, "画像、ZIP/CBZ、またはフォルダを開く", start, image_filter)
        if path:
            self._request_open_path(path)
            return

        folder = QFileDialog.getExistingDirectory(self, "フォルダを開く", start)
        if folder:
            self._request_open_path(folder)

    def open_path(self, path: str | Path) -> bool:
        self._save_current_reading_position()
        self._active_request_id += 1
        try:
            opened = self.book_session.open_book(
                path,
                recursive_folder=self.recursive_folder,
                sort_descending=self.sort_descending,
            )
        except ImageSourceError as exc:
            QMessageBox.critical(self, "読み込みエラー", str(exc))
            return False

        if self.model.total_pages == 0:
            QMessageBox.warning(self, "画像なし", "対応画像が見つかりませんでした。")
            self.book_session.close_book()
            self.viewer.clear()
            self._clear_page_history()
            self._rebuild_page_list()
            self._update_slider()
            self._update_status()
            return False

        if opened.selected_image is None:
            self._restore_reading_position(self._current_book_key)

        opened_path = str(opened.requested_path)
        self.settings["last_open_path"] = opened_path
        self._add_recent_path(opened_path)
        self.image_cache.set_cache_size(self.cache_size)
        self._clear_page_history()
        self._rebuild_page_list()
        self._refresh_view()
        self.book_changed.emit(self, opened_path)
        return True

    def _restore_reading_position(self, book_key: str) -> None:
        positions = self.settings.get("reading_positions", {})
        if not isinstance(positions, dict):
            return
        try:
            page_index = int(positions.get(book_key, 0))
        except (TypeError, ValueError):
            return
        if 0 <= page_index < self.model.total_pages:
            self.model.go_to_index(page_index)

    def _save_current_reading_position(self) -> None:
        if not self._current_book_key or self.model.total_pages <= 0:
            return
        positions = self.settings.get("reading_positions")
        if not isinstance(positions, dict):
            positions = {}
        positions[self._current_book_key] = self.model.current_index
        while len(positions) > 100:
            oldest_key = next(iter(positions))
            del positions[oldest_key]
        self.settings["reading_positions"] = positions

    def _clear_page_history(self) -> None:
        self._page_history_back.clear()
        self._page_history_forward.clear()
        self._sync_actions()

    def _record_page_history(self, previous_index: int) -> None:
        if not 0 <= previous_index < self.model.total_pages:
            return
        if previous_index == self.model.current_index:
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
        old = self.model.current_index
        if raw:
            self.model.go_to_raw_index(page_index)
        else:
            self.model.go_to_index(page_index)
        if self.model.current_index == old:
            return False
        self._record_page_history(old)
        self.book_session.notify_page_changed()
        self._refresh_view()
        return True

    def _go_to_model_move_with_history(self, move) -> bool:
        if self.model.total_pages <= 0:
            return False
        old = self.model.current_index
        move()
        if self.model.current_index == old:
            return False
        self._record_page_history(old)
        self.book_session.notify_page_changed()
        self._refresh_view()
        return True

    def go_back_in_page_history(self) -> None:
        if self.model.total_pages <= 0 or not self._page_history_back:
            return
        current = self.model.current_index
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
        current = self.model.current_index
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
        if not Path(path).exists():
            QMessageBox.warning(self, "履歴を開けません", f"パスが見つかりません:\n{path}")
            recent = self.settings.get("recent_paths", [])
            if isinstance(recent, list):
                self.settings["recent_paths"] = [item for item in recent if item != path]
                self._rebuild_recent_menu()
            return
        self._request_open_path(path)

    def _clear_recent_paths(self) -> None:
        self.settings["recent_paths"] = []
        self._rebuild_recent_menu()

    def copy_current_image_path(self) -> None:
        if self.model.total_pages <= 0:
            return
        QApplication.clipboard().setText(self.model.display_path_for_index(self.model.current_index))

    def copy_current_image(self) -> None:
        if self.model.total_pages <= 0:
            return
        cached = self.image_cache.get(self.model.current_index)
        if cached is not None and cached.qimage is not None:
            QApplication.clipboard().setImage(cached.qimage)

    def copy_current_view(self) -> None:
        if self.model.total_pages <= 0:
            return
        QApplication.clipboard().setPixmap(self.viewer.grab())

    def show_page_info(self) -> None:
        if self.model.total_pages <= 0:
            return
        page_index = self.model.current_index
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
        target: Path | None = None
        if self._opened_path:
            opened = Path(self._opened_path)
            target = opened.parent if opened.is_file() else opened
        elif self._current_book_key:
            current = Path(self._current_book_key)
            target = current.parent if current.is_file() else current
        if target is not None and target.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

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
            current_index = self.model.current_index
            self.open_path(self._opened_path)
            if self.model.total_pages > 0:
                self.model.go_to_index(min(current_index, self.model.total_pages - 1))
                self._refresh_view()

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
            return
        result = self._adjacent_book_handler(self, direction)
        if result == "boundary":
            QMessageBox.information(self, "本の移動", "これ以上移動できません。")

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
        self._updating_page_list = True
        self.page_list.clear()
        filter_text = self.page_list_filter.text().casefold().strip()
        for index, image_id in enumerate(self.model.image_ids):
            label = f"{index + 1}: {Path(image_id).name}"
            if filter_text and filter_text not in label.casefold():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.page_list.addItem(item)
        self._updating_page_list = False
        self._sync_page_list_selection()

    def _sync_page_list_selection(self) -> None:
        if self.model.total_pages <= 0:
            return
        self._updating_page_list = True
        target_item = None
        for row in range(self.page_list.count()):
            item = self.page_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == self.model.current_index:
                target_item = item
                break
        if target_item is None:
            self.page_list.setCurrentRow(-1)
        else:
            self.page_list.setCurrentItem(target_item)
            self.page_list.scrollToItem(target_item)
        self._updating_page_list = False

    def _update_page_list_thumbnail(self, cached: CachedImage) -> None:
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
        self._active_request_id += 1
        spread = self.model.spread_at()
        self._visible_page_indexes = tuple(slot.page_index for slot in spread.slots)
        self.image_cache.preload_around(
            self.model.current_index,
            radius=PRELOAD_RADIUS,
            visible_indexes=self._visible_page_indexes,
        )
        self._render_spread(spread, self._active_request_id)

    def _render_spread(self, spread, request_id: int) -> None:
        if request_id != self._active_request_id:
            return

        pages: list[ViewerImage] = []
        for slot in spread.slots:
            cached = self.image_cache.get(slot.page_index)
            if cached is None:
                pages.append(ViewerWidget.loading_page(slot.page_index, slot.image_id))
            elif cached.error:
                pages.append(ViewerWidget.error_page(slot.page_index, slot.image_id, cached.error))
            elif cached.qimage is not None and cached.original_size is not None:
                pages.extend(self._viewer_images_for_cached(cached, split_allowed=spread.is_single))
            else:
                pages.append(ViewerWidget.error_page(slot.page_index, slot.image_id, "画像を表示できません。"))

        self.viewer.set_pages(spread, pages)
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
            return [ViewerWidget.from_qimage(cached.page_index, cached.image_id, cached.qimage, cached.original_size)]

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
        if cached.generation != self.image_cache.generation:
            return
        if cached.page_index not in self._visible_page_indexes:
            self._update_page_list_thumbnail(cached)
            return
        self._update_page_list_thumbnail(cached)
        self._render_spread(self.model.spread_at(), self._active_request_id)

    def _on_left_side_clicked(self) -> None:
        if self.reading_direction == "rtl":
            self.next_page()
        else:
            self.previous_page()

    def _on_right_side_clicked(self) -> None:
        if self.reading_direction == "rtl":
            self.previous_page()
        else:
            self.next_page()

    def dispatch_command(self, command: str) -> bool:
        normalized = commands.normalize_viewer_command(command)
        handlers: dict[str, Callable[[], None]] = {
            commands.PREVIOUS_PAGE: self.previous_page,
            commands.NEXT_PAGE: self.next_page,
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
        if self._local_path_from_drop(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        path = self._local_path_from_drop(event)
        if path:
            self._request_open_path(path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    @staticmethod
    def _local_path_from_drop(event: QDragEnterEvent | QDropEvent) -> str:
        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            return ""
        for url in mime_data.urls():
            if url.isLocalFile():
                return url.toLocalFile()
        return ""

    def _update_slider(self) -> None:
        total = max(1, self.model.total_pages)
        self.slider.blockSignals(True)
        self.slider.setEnabled(self.model.total_pages > 0)
        self.slider.setMinimum(1)
        self.slider.setMaximum(total)
        self.slider.setValue(min(total, self.model.current_index + 1))
        self.slider.blockSignals(False)

    def _update_status(self) -> None:
        if self.model.total_pages == 0:
            self.status.showMessage("画像が読み込まれていません")
            return

        path = self.model.display_path_for_index(self.model.current_index)
        page_text = f"{self.model.current_index + 1} / {self.model.total_pages}"
        resolution = self.viewer.current_resolution_text()
        size = self._format_file_size(self.model.file_size_for_index(self.model.current_index))
        details = "    ".join(part for part in (path, page_text, resolution, size) if part)
        self.status.showMessage(details)

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
        self._go_to_index_with_history(value - 1)

    def _on_zoom_changed(self, zoom: float) -> None:
        self.fit_mode = "manual_zoom"
        self._update_shared_setting("fit_mode", self.fit_mode)
        self._sync_actions()
        self._update_status()

    def set_view_mode(self, mode: str) -> None:
        self.view_mode = mode
        self._update_shared_setting("view_mode", mode)
        self.model.update_options(view_mode=mode)
        self._sync_actions()
        if self.model.total_pages:
            self._refresh_view()

    def toggle_view_mode(self) -> None:
        self.set_view_mode("single" if self.view_mode == "spread" else "spread")

    def set_reading_direction(self, direction: str) -> None:
        self.reading_direction = direction
        self._update_shared_setting("reading_direction", direction)
        self.model.update_options(reading_direction=direction)
        self._sync_actions()
        if self.model.total_pages:
            self._refresh_view()

    def toggle_reading_direction(self) -> None:
        self.set_reading_direction("ltr" if self.reading_direction == "rtl" else "rtl")

    def set_single_first_page(self, checked: bool) -> None:
        self.single_first_page = checked
        self._update_shared_setting("single_first_page", checked)
        self.model.update_options(single_first_page=checked)
        if self.model.total_pages:
            self._refresh_view()

    def set_treat_wide_image_as_single(self, checked: bool) -> None:
        self.treat_wide_image_as_single = checked
        self._update_shared_setting("treat_wide_image_as_single", checked)
        self.model.update_options(treat_wide_image_as_single=checked)
        if self.model.total_pages:
            self._refresh_view()

    def set_split_wide_image(self, checked: bool) -> None:
        self.split_wide_image = checked
        self._update_shared_setting("split_wide_image", checked)
        self._sync_actions()
        if self.model.total_pages:
            self._refresh_view()

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

    def zoom_in(self) -> None:
        self.viewer.set_manual_zoom(self.viewer.manual_zoom * 1.15)

    def zoom_out(self) -> None:
        self.viewer.set_manual_zoom(self.viewer.manual_zoom / 1.15)

    def rotate_left(self) -> None:
        self.rotation_angle = (self.rotation_angle - 90) % 360
        self.viewer.set_rotation_angle(self.rotation_angle)
        self._update_status()

    def rotate_right(self) -> None:
        self.rotation_angle = (self.rotation_angle + 90) % 360
        self.viewer.set_rotation_angle(self.rotation_angle)
        self._update_status()

    def reset_rotation(self) -> None:
        self.rotation_angle = 0
        self.viewer.set_rotation_angle(self.rotation_angle)
        self._update_status()

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
        moved = self._go_to_model_move_with_history(self.model.next)
        if not moved and self.auto_open_adjacent_book:
            self.open_next_book()

    def previous_page(self) -> None:
        moved = self._go_to_model_move_with_history(self.model.previous)
        if not moved and self.auto_open_adjacent_book:
            self.open_previous_book()

    def next_page_or_scroll(self) -> None:
        if not self.viewer.scroll_forward():
            self.next_page()

    def previous_page_or_scroll(self) -> None:
        if not self.viewer.scroll_backward():
            self.previous_page()

    def next_one_page(self) -> None:
        if self.model.total_pages <= 0:
            return
        moved = self._go_to_index_with_history(self.model.current_index + 1, raw=True)
        if not moved and self.auto_open_adjacent_book:
            self.open_next_book()

    def previous_one_page(self) -> None:
        if self.model.total_pages <= 0:
            return
        moved = self._go_to_index_with_history(self.model.current_index - 1, raw=True)
        if not moved and self.auto_open_adjacent_book:
            self.open_previous_book()

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
        self._go_to_model_move_with_history(self.model.first)

    def last_page(self) -> None:
        self.slideshow_timer.stop()
        self._sync_actions()
        self._go_to_model_move_with_history(self.model.last)

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
        show_chrome = not (self.isFullScreen() and self.hide_ui_in_fullscreen)
        self.menuBar().setVisible(show_chrome)
        self.slider.setVisible(show_chrome)
        self.status.setVisible(show_chrome)
        self.page_list_dock.setVisible(show_chrome and self.show_page_list)
        self._apply_cursor_visibility_policy()

    def _apply_cursor_visibility_policy(self) -> None:
        self.viewer.set_auto_hide_cursor(self.isFullScreen() and self.hide_cursor_in_fullscreen)

    def prepare_shutdown(self) -> None:
        if self._shutdown_prepared:
            return
        self._shutdown_prepared = True
        self.viewer.cancel_mouse_gesture()
        self.slideshow_timer.stop()
        self._save_current_reading_position()
        self.book_session.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        self.prepare_shutdown()
        self.closing.emit(self)
        super().closeEvent(event)
