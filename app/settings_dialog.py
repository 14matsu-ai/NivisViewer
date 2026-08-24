from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .browser_sort import (
    BROWSER_DISPLAY_DENSITY_LABELS,
    BROWSER_SORT_KEY_LABELS,
    BROWSER_SORT_ORDER_LABELS,
    BrowserDisplayDensity,
    BrowserSortKey,
    BrowserSortOrder,
)
from .app_icon import install_window_icon
from .browser_item_delegate import GRID_PRESET_THUMBNAIL_SIZES
from .browser_wheel_scroll import (
    BROWSER_WHEEL_SCROLL_CUSTOM_MAX_ROWS,
    BROWSER_WHEEL_SCROLL_CUSTOM_MIN_ROWS,
)
from .config_manager import ConfigManager
from .ffmpeg_thumbnail_backend import FFmpegLocator
from .thumbnail_render import (
    BROWSER_THUMBNAIL_DISPLAY_MODES,
    CROP_MODES,
    FRAME_RATIOS,
)
from .seven_zip_locator import SevenZipInfo, SevenZipLocator
from .viewer_commands import COMMAND_CHOICES
from .viewer_memory_policy import VIEWER_MEMORY_MODE_LABELS
from .viewer_render import (
    DOWNSCALE_ALGORITHM_LABELS,
    UPSCALE_ALGORITHM_LABELS,
)
from .winrar_locator import WinRARInfo, WinRARLocator
from .windows_file_registration import WindowsFileRegistrationService


_LOGGER = logging.getLogger(__name__)


class _SevenZipProbeSignals(QObject):
    completed = Signal(int, object)


class _SevenZipProbeWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        locator: SevenZipLocator,
        path: str,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.locator = locator
        self.path = path
        self.signals = _SevenZipProbeSignals()

    @Slot()
    def run(self) -> None:
        try:
            info = self.locator.locate(self.path, force=True)
        except Exception as exc:
            _LOGGER.exception("7-Zip probe failed")
            info = SevenZipInfo(self.path, False, None, str(exc))
        try:
            self.signals.completed.emit(self.generation, info)
        except RuntimeError:
            pass


class _WinRARProbeSignals(QObject):
    completed = Signal(int, object)


class _WinRARProbeWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        locator: WinRARLocator,
        path: str,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.locator = locator
        self.path = path
        self.signals = _WinRARProbeSignals()

    @Slot()
    def run(self) -> None:
        try:
            try:
                info = self.locator.locate(
                    self.path,
                    extension=".rar",
                    force=True,
                )
            except TypeError:
                info = self.locator.locate(  # type: ignore[call-arg]
                    self.path,
                    force=True,
                )
        except Exception as exc:
            _LOGGER.exception("WinRAR probe failed")
            info = WinRARInfo(self.path, False, None, str(exc))
        try:
            self.signals.completed.emit(self.generation, info)
        except RuntimeError:
            pass


class _FFmpegProbeSignals(QObject):
    completed = Signal(int, object)


class _FFmpegProbeWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        locator: FFmpegLocator,
        path: str,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.locator = locator
        self.path = path
        self.signals = _FFmpegProbeSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.locator.locate(self.path)
        except Exception:
            _LOGGER.exception("FFmpeg probe failed")
            result = None
        try:
            self.signals.completed.emit(self.generation, result)
        except RuntimeError:
            pass


_RETIRED_SETTINGS_DIALOGS: set[QDialog] = set()


class SettingsDialog(QDialog):
    settings_applied = Signal(object)
    cache_clear_requested = Signal()
    cache_cleanup_requested = Signal()

    def __init__(
        self,
        config_manager: ConfigManager,
        parent: QWidget | None = None,
        *,
        cache_usage_getter: Callable[[], int] | None = None,
        cache_statistics_getter: Callable[[], dict[str, object]] | None = None,
        seven_zip_locator: SevenZipLocator | None = None,
        winrar_locator: WinRARLocator | None = None,
        ffmpeg_locator: FFmpegLocator | None = None,
        file_registration_service: WindowsFileRegistrationService | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("設定")
        install_window_icon(self)
        self.setModal(True)
        self.resize(620, 680)
        self.config = config_manager
        self._cache_usage_getter = cache_usage_getter
        self._cache_statistics_getter = cache_statistics_getter
        self._last_save_error_reported: str | None = None
        self._seven_zip_locator = seven_zip_locator or SevenZipLocator()
        self._winrar_locator = winrar_locator or WinRARLocator()
        self._ffmpeg_locator = ffmpeg_locator or FFmpegLocator()
        self._file_registration_service = file_registration_service
        self._probe_pool = QThreadPool(self)
        self._probe_pool.setMaxThreadCount(2)
        self._probes_closed = False
        self._delete_scheduled = False
        self._probe_generation = 0
        self._probe_workers: dict[int, _SevenZipProbeWorker] = {}
        self._pending_explicit_path: str | None = None
        self._accept_after_probe = False
        self._winrar_probe_generation = 0
        self._winrar_probe_workers: dict[int, _WinRARProbeWorker] = {}
        self._pending_winrar_path: str | None = None
        self._accept_after_winrar_probe = False
        self._ffmpeg_probe_generation = 0
        self._ffmpeg_probe_workers: dict[int, _FFmpegProbeWorker] = {}
        self._initial_probe_started = False
        self._custom_prefetch_values = {
            "image_forward_units": int(
                self.config.get("viewer_prefetch_image_forward_units", 3)
            ),
            "image_backward_units": int(
                self.config.get("viewer_prefetch_image_backward_units", 3)
            ),
            "pdf_forward_units": int(
                self.config.get("viewer_prefetch_pdf_forward_units", 3)
            ),
            "pdf_backward_units": int(
                self.config.get("viewer_prefetch_pdf_backward_units", 3)
            ),
        }
        self._displayed_prefetch_preset: str | None = None
        self._folder_fallback_custom_color = "#000000"
        raw_bindings = self.config.get("mouse_gesture_bindings", {})
        self._gesture_bindings_base = (
            dict(raw_bindings) if isinstance(raw_bindings, dict) else {}
        )

        self._build_ui()
        self.load_current_values()

    def _build_ui(self) -> None:
        self.tabs = QTabWidget(self)
        self._scroll_areas: list[QScrollArea] = []
        self.tabs.addTab(self._scrollable_tab(self._build_viewer_tab()), "Viewer")
        self.tabs.addTab(self._scrollable_tab(self._build_browser_tab()), "Browser")
        self.tabs.addTab(self._scrollable_tab(self._build_archive_tab()), "書庫")
        self.tabs.addTab(self._scrollable_tab(self._build_mouse_tab()), "Mouse")
        self.tabs.addTab(
            self._scrollable_tab(self._build_windows_tab()),
            "Windows連携",
        )

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply,
            self,
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        apply_button = self.button_box.button(QDialogButtonBox.StandardButton.Apply)
        if apply_button is not None:
            apply_button.clicked.connect(self.apply_settings)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.button_box)

    def _scrollable_tab(self, content: QWidget) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setObjectName("settings_scroll_area")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        content_policy = content.sizePolicy()
        content_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        content.setSizePolicy(content_policy)
        scroll.setWidget(content)
        self._scroll_areas.append(scroll)
        return scroll

    def _build_windows_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.registration_status_label = QLabel(tab)
        self.registration_status_label.setWordWrap(True)
        layout.addWidget(self.registration_status_label)
        self.register_images_checkbox = QCheckBox("画像を登録", tab)
        self.register_archives_checkbox = QCheckBox("漫画書庫を登録", tab)
        self.register_pdf_checkbox = QCheckBox("PDFを登録", tab)
        self.register_context_menu_checkbox = QCheckBox(
            "「NivisViewerで開く」を右クリックメニューへ追加",
            tab,
        )
        for checkbox in (
            self.register_images_checkbox,
            self.register_archives_checkbox,
            self.register_pdf_checkbox,
            self.register_context_menu_checkbox,
        ):
            layout.addWidget(checkbox)
        note = QLabel(
            "登録は「プログラムから開く」と既定アプリ候補を追加するだけで、"
            "既定アプリを強制変更しません。",
            tab,
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        self.register_button = QPushButton("Windowsへ登録", tab)
        self.unregister_button = QPushButton("登録を解除", tab)
        self.default_apps_button = QPushButton(
            "Windowsの既定のアプリ設定を開く", tab
        )
        buttons.addWidget(self.register_button)
        buttons.addWidget(self.unregister_button)
        layout.addLayout(buttons)
        layout.addWidget(self.default_apps_button)
        layout.addStretch(1)
        self.register_button.clicked.connect(self._register_with_windows)
        self.unregister_button.clicked.connect(self._unregister_from_windows)
        self.default_apps_button.clicked.connect(self._open_default_apps)
        available = self._file_registration_service is not None
        self.register_button.setEnabled(available)
        self.unregister_button.setEnabled(available)
        self.default_apps_button.setEnabled(available)
        return tab

    def _build_viewer_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        behavior_group = QGroupBox("ViewerWindow", tab)
        behavior_form = QFormLayout(behavior_group)
        self.open_behavior_combo = QComboBox(behavior_group)
        self.open_behavior_combo.addItem("アクティブなViewerを再利用", "reuse_active")
        self.open_behavior_combo.addItem("常に新しいViewerを開く", "always_new")
        self.open_behavior_combo.addItem(
            "Viewerがあれば再利用し、なければ作成",
            "reuse_or_create",
        )
        behavior_form.addRow("ファイルを開く方法:", self.open_behavior_combo)
        self.bring_to_front_checkbox = QCheckBox(
            "本を開いたときViewerWindowを一度だけ前面へ出す",
            behavior_group,
        )
        behavior_form.addRow(self.bring_to_front_checkbox)
        self.loop_navigation_checkbox = QCheckBox(
            "最後の書庫から先頭へループする",
            behavior_group,
        )
        behavior_form.addRow(self.loop_navigation_checkbox)

        spread_group = QGroupBox("ページ表示", tab)
        spread_form = QFormLayout(spread_group)
        self.join_spread_checkbox = QCheckBox("見開きページを中央で密着表示", spread_group)
        self.join_spread_checkbox.toggled.connect(self._sync_gap_enabled)
        spread_form.addRow(self.join_spread_checkbox)
        self.gap_spin = QSpinBox(spread_group)
        self.gap_spin.setRange(0, 100)
        self.gap_spin.setSuffix(" px")
        self.gap_note = QLabel("密着表示時はページ間隔を使用しません。", spread_group)
        gap_container = QWidget(spread_group)
        gap_layout = QVBoxLayout(gap_container)
        gap_layout.setContentsMargins(0, 0, 0, 0)
        gap_layout.addWidget(self.gap_spin)
        gap_layout.addWidget(self.gap_note)
        spread_form.addRow("通常時のページ間隔:", gap_container)
        self.single_first_checkbox = QCheckBox("表紙を単独表示", spread_group)
        spread_form.addRow(self.single_first_checkbox)
        self.wide_single_checkbox = QCheckBox("横長画像を単独表示", spread_group)
        spread_form.addRow(self.wide_single_checkbox)
        self.book_open_position_combo = QComboBox(spread_group)
        self.book_open_position_combo.addItem(
            "常に先頭ページから開く",
            "first_page",
        )
        self.book_open_position_combo.addItem(
            "前回閉じたページから再開",
            "resume_last",
        )
        spread_form.addRow(
            "書庫・PDF・フォルダーを開く位置:",
            self.book_open_position_combo,
        )
        self.viewer_canvas_click_direction_combo = QComboBox(spread_group)
        self.viewer_canvas_click_direction_combo.addItem(
            "右側で次へ／左側で前へ",
            "right_next",
        )
        self.viewer_canvas_click_direction_combo.addItem(
            "左側で次へ／右側で前へ",
            "left_next",
        )
        spread_form.addRow(
            "左右クリックのページ送り方向:",
            self.viewer_canvas_click_direction_combo,
        )
        self.viewer_canvas_left_click_combo = QComboBox(spread_group)
        self.viewer_canvas_left_click_combo.addItem(
            "クリックでページ移動しない",
            "none",
        )
        self.viewer_canvas_left_click_combo.addItem(
            "1ページずつ移動",
            "next_single_page",
        )
        self.viewer_canvas_left_click_combo.addItem(
            "現在のページ送り単位で移動",
            "next_display_unit",
        )
        spread_form.addRow(
            "左右クリックのページ移動量:",
            self.viewer_canvas_left_click_combo,
        )
        self.viewer_slider_wheel_single_page_checkbox = QCheckBox(
            "下部UI上のマウスホイールで1ページずつ移動する",
            spread_group,
        )
        spread_form.addRow(self.viewer_slider_wheel_single_page_checkbox)

        memory_group = QGroupBox("Viewerメモリ", tab)
        memory_form = QFormLayout(memory_group)
        self.viewer_memory_mode_combo = QComboBox(memory_group)
        for label, value in VIEWER_MEMORY_MODE_LABELS:
            self.viewer_memory_mode_combo.addItem(label, value)
        memory_form.addRow(
            "ビューワーのメモリ使用量:",
            self.viewer_memory_mode_combo,
        )

        resampling_group = QGroupBox("画像の拡大縮小", tab)
        resampling_layout = QVBoxLayout(resampling_group)

        normal_resampling_group = QGroupBox("通常表示", resampling_group)
        normal_resampling_form = QFormLayout(normal_resampling_group)
        self.viewer_downscale_algorithm_combo = QComboBox(
            normal_resampling_group
        )
        self.viewer_upscale_algorithm_combo = QComboBox(
            normal_resampling_group
        )
        for value, label in DOWNSCALE_ALGORITHM_LABELS.items():
            self.viewer_downscale_algorithm_combo.addItem(label, value)
        for value, label in UPSCALE_ALGORITHM_LABELS.items():
            self.viewer_upscale_algorithm_combo.addItem(label, value)
        normal_resampling_form.addRow(
            "縮小方式:",
            self.viewer_downscale_algorithm_combo,
        )
        normal_resampling_form.addRow(
            "拡大方式:",
            self.viewer_upscale_algorithm_combo,
        )
        resampling_layout.addWidget(normal_resampling_group)

        magnifier_resampling_group = QGroupBox("拡大鏡", resampling_group)
        magnifier_resampling_form = QFormLayout(magnifier_resampling_group)
        self.magnifier_downscale_algorithm_combo = QComboBox(
            magnifier_resampling_group
        )
        self.magnifier_upscale_algorithm_combo = QComboBox(
            magnifier_resampling_group
        )
        for value, label in DOWNSCALE_ALGORITHM_LABELS.items():
            self.magnifier_downscale_algorithm_combo.addItem(label, value)
        for value, label in UPSCALE_ALGORITHM_LABELS.items():
            self.magnifier_upscale_algorithm_combo.addItem(label, value)
        magnifier_resampling_form.addRow(
            "縮小方式:",
            self.magnifier_downscale_algorithm_combo,
        )
        magnifier_resampling_form.addRow(
            "拡大方式:",
            self.magnifier_upscale_algorithm_combo,
        )
        resampling_layout.addWidget(magnifier_resampling_group)

        resampling_note = QLabel(
            "自動は倍率に応じて方式を選びます。設定の適用後は、"
            "開いている画像を再読込せずに表示用フレームだけを作り直します。",
            resampling_group,
        )
        resampling_note.setWordWrap(True)
        resampling_layout.addWidget(resampling_note)

        prefetch_group = QGroupBox("PDF・旧形式の先読み", tab)
        prefetch_layout = QVBoxLayout(prefetch_group)
        prefetch_form = QFormLayout()
        self.prefetch_preset_combo = QComboBox(prefetch_group)
        for label, value in (
            ("無効", "disabled"),
            ("省メモリ", "memory_saver"),
            ("標準", "standard"),
            ("多め", "more"),
            ("カスタム", "custom"),
        ):
            self.prefetch_preset_combo.addItem(label, value)
        prefetch_form.addRow("先読みプリセット:", self.prefetch_preset_combo)
        self.prefetch_direction_priority_checkbox = QCheckBox(
            "進行方向を優先する",
            prefetch_group,
        )
        prefetch_form.addRow(self.prefetch_direction_priority_checkbox)
        prefetch_layout.addLayout(prefetch_form)

        self.prefetch_custom_group = QGroupBox("カスタム設定", prefetch_group)
        custom_form = QFormLayout(self.prefetch_custom_group)
        self.prefetch_image_forward_spin = QSpinBox(self.prefetch_custom_group)
        self.prefetch_image_backward_spin = QSpinBox(self.prefetch_custom_group)
        self.prefetch_pdf_forward_spin = QSpinBox(self.prefetch_custom_group)
        self.prefetch_pdf_backward_spin = QSpinBox(self.prefetch_custom_group)
        for spin in (
            self.prefetch_image_forward_spin,
            self.prefetch_image_backward_spin,
            self.prefetch_pdf_forward_spin,
            self.prefetch_pdf_backward_spin,
        ):
            spin.setRange(0, 20)
            spin.setSuffix(" 表示単位")
        custom_form.addRow(
            "ZIP・Folder以外 進行方向:",
            self.prefetch_image_forward_spin,
        )
        custom_form.addRow(
            "ZIP・Folder以外 逆方向:",
            self.prefetch_image_backward_spin,
        )
        custom_form.addRow("PDF 進行方向:", self.prefetch_pdf_forward_spin)
        custom_form.addRow("PDF 逆方向:", self.prefetch_pdf_backward_spin)
        prefetch_layout.addWidget(self.prefetch_custom_group)
        display_unit_note = QLabel(
            "ZIP・Folderは上のメモリ量だけで保持量を決めます。"
            "この先読み設定はPDFと旧pipeline形式だけに適用されます。\n"
            "単ページ表示では1表示単位＝1ページ、"
            "見開き表示では1表示単位＝1見開きです。",
            prefetch_group,
        )
        display_unit_note.setWordWrap(True)
        prefetch_layout.addWidget(display_unit_note)
        self.prefetch_preset_combo.currentIndexChanged.connect(
            self._on_prefetch_preset_changed
        )

        fullscreen_group = QGroupBox("全画面UI", tab)
        fullscreen_form = QFormLayout(fullscreen_group)
        self.fullscreen_hide_ui_checkbox = QCheckBox(
            "全画面時にUIを隠す",
            fullscreen_group,
        )
        fullscreen_form.addRow(self.fullscreen_hide_ui_checkbox)
        self.fullscreen_hide_cursor_checkbox = QCheckBox(
            "全画面時にカーソルを隠す",
            fullscreen_group,
        )
        fullscreen_form.addRow(self.fullscreen_hide_cursor_checkbox)
        self.fullscreen_auto_reveal_checkbox = QCheckBox(
            "全画面時、画面端でUIを表示",
            fullscreen_group,
        )
        fullscreen_form.addRow(self.fullscreen_auto_reveal_checkbox)
        self.fullscreen_top_edge_trigger_spin = QSpinBox(fullscreen_group)
        self.fullscreen_top_edge_trigger_spin.setRange(4, 32)
        self.fullscreen_top_edge_trigger_spin.setSuffix(" px")
        fullscreen_form.addRow(
            "上端の反応範囲:",
            self.fullscreen_top_edge_trigger_spin,
        )
        self.fullscreen_bottom_edge_trigger_spin = QSpinBox(fullscreen_group)
        self.fullscreen_bottom_edge_trigger_spin.setRange(12, 64)
        self.fullscreen_bottom_edge_trigger_spin.setSuffix(" px")
        fullscreen_form.addRow(
            "下端の反応範囲:",
            self.fullscreen_bottom_edge_trigger_spin,
        )
        self.fullscreen_edge_trigger_spin = self.fullscreen_top_edge_trigger_spin
        self.fullscreen_hide_delay_spin = QSpinBox(fullscreen_group)
        self.fullscreen_hide_delay_spin.setRange(0, 3000)
        self.fullscreen_hide_delay_spin.setSingleStep(100)
        self.fullscreen_hide_delay_spin.setSuffix(" ms")
        fullscreen_form.addRow(
            "自動的に隠すまで:",
            self.fullscreen_hide_delay_spin,
        )

        layout.addWidget(behavior_group)
        layout.addWidget(spread_group)
        layout.addWidget(memory_group)
        layout.addWidget(resampling_group)
        layout.addWidget(prefetch_group)
        layout.addWidget(fullscreen_group)
        layout.addStretch(1)
        return tab

    def _build_archive_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        preference_group = QGroupBox("外部書庫バックエンド", tab)
        preference_form = QFormLayout(preference_group)
        self.archive_backend_combo = QComboBox(preference_group)
        self.archive_backend_combo.addItem(
            "自動（Windowsの関連付けを優先）",
            "auto",
        )
        self.archive_backend_combo.addItem("WinRAR", "winrar")
        self.archive_backend_combo.addItem("7-Zip", "seven_zip")
        preference_form.addRow("使用するバックエンド:", self.archive_backend_combo)
        layout.addWidget(preference_group)

        winrar_group = QGroupBox("WinRAR", tab)
        winrar_form = QFormLayout(winrar_group)
        self.winrar_path_edit = QLineEdit(winrar_group)
        self.winrar_path_edit.setPlaceholderText("空欄の場合は自動検出")
        self.winrar_browse_button = QPushButton("参照…", winrar_group)
        self.winrar_browse_button.clicked.connect(self.browse_winrar)
        winrar_path_row = QWidget(winrar_group)
        winrar_path_layout = QHBoxLayout(winrar_path_row)
        winrar_path_layout.setContentsMargins(0, 0, 0, 0)
        winrar_path_layout.addWidget(self.winrar_path_edit, 1)
        winrar_path_layout.addWidget(self.winrar_browse_button)
        winrar_form.addRow("実行ファイル:", winrar_path_row)
        self.winrar_auto_button = QPushButton("自動検出へ戻す", winrar_group)
        self.winrar_auto_button.clicked.connect(self.use_automatic_winrar)
        self.winrar_redetect_button = QPushButton("再検出", winrar_group)
        self.winrar_redetect_button.clicked.connect(self.redetect_winrar)
        winrar_action_row = QWidget(winrar_group)
        winrar_action_layout = QHBoxLayout(winrar_action_row)
        winrar_action_layout.setContentsMargins(0, 0, 0, 0)
        winrar_action_layout.addWidget(self.winrar_auto_button)
        winrar_action_layout.addWidget(self.winrar_redetect_button)
        winrar_action_layout.addStretch(1)
        winrar_form.addRow(winrar_action_row)
        self.winrar_status_label = QLabel("WinRAR：未確認", winrar_group)
        self.winrar_status_label.setWordWrap(True)
        winrar_form.addRow("現在の状態:", self.winrar_status_label)
        layout.addWidget(winrar_group)

        group = QGroupBox("7-Zip", tab)
        form = QFormLayout(group)

        self.seven_zip_path_edit = QLineEdit(group)
        self.seven_zip_path_edit.setPlaceholderText("空欄の場合は自動検出")
        self.seven_zip_browse_button = QPushButton("参照…", group)
        self.seven_zip_browse_button.clicked.connect(self.browse_seven_zip)
        path_row = QWidget(group)
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.addWidget(self.seven_zip_path_edit, 1)
        path_layout.addWidget(self.seven_zip_browse_button)
        form.addRow("7-Zip実行ファイル:", path_row)

        self.seven_zip_auto_button = QPushButton("自動検出へ戻す", group)
        self.seven_zip_auto_button.clicked.connect(self.use_automatic_seven_zip)
        self.seven_zip_redetect_button = QPushButton("再検出", group)
        self.seven_zip_redetect_button.clicked.connect(self.redetect_seven_zip)
        action_row = QWidget(group)
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addWidget(self.seven_zip_auto_button)
        action_layout.addWidget(self.seven_zip_redetect_button)
        action_layout.addStretch(1)
        form.addRow(action_row)

        self.seven_zip_status_label = QLabel("7-Zip：未確認", group)
        self.seven_zip_status_label.setWordWrap(True)
        form.addRow("現在の状態:", self.seven_zip_status_label)
        note = QLabel(
            "WinRARと7-Zipは第三者ソフトウェアです。Windowsの関連付けと"
            "利用者が指定した実行ファイルだけを検証し、自動ダウンロードや"
            "自動インストールは行いません。",
            group,
        )
        note.setWordWrap(True)
        form.addRow(note)
        layout.addWidget(group)
        layout.addStretch(1)
        return tab

    def _build_browser_wheel_scroll_group(self, parent: QWidget) -> QGroupBox:
        self.browser_wheel_scroll_group = QGroupBox(
            "Browser マウスホイール",
            parent,
        )
        browser_wheel_form = QFormLayout(self.browser_wheel_scroll_group)
        self.browser_wheel_scroll_mode_combo = QComboBox(
            self.browser_wheel_scroll_group
        )
        for label, mode in (
            ("System / Default", "system"),
            ("Small", "small"),
            ("Medium", "medium"),
            ("Large", "large"),
            ("Custom", "custom"),
        ):
            self.browser_wheel_scroll_mode_combo.addItem(label, mode)
        self.browser_wheel_scroll_mode_combo.setToolTip(
            "標準ホイール1目盛りあたりの移動量です。"
            "System / DefaultではQtとWindowsの現在の動作をそのまま使用します。"
        )
        self.browser_wheel_scroll_mode_combo.currentIndexChanged.connect(
            self._sync_browser_wheel_scroll_controls
        )
        browser_wheel_form.addRow(
            "スクロール量:",
            self.browser_wheel_scroll_mode_combo,
        )
        self.browser_wheel_scroll_custom_spin = QSpinBox(
            self.browser_wheel_scroll_group
        )
        self.browser_wheel_scroll_custom_spin.setRange(
            BROWSER_WHEEL_SCROLL_CUSTOM_MIN_ROWS,
            BROWSER_WHEEL_SCROLL_CUSTOM_MAX_ROWS,
        )
        self.browser_wheel_scroll_custom_spin.setSuffix(" 行／1目盛り")
        browser_wheel_form.addRow(
            "Customの行数:",
            self.browser_wheel_scroll_custom_spin,
        )
        self.browser_wheel_scroll_restore_button = QPushButton(
            "既定に戻す",
            self.browser_wheel_scroll_group,
        )
        self.browser_wheel_scroll_restore_button.clicked.connect(
            self._restore_browser_wheel_scroll_default
        )
        browser_wheel_form.addRow(self.browser_wheel_scroll_restore_button)
        browser_wheel_note = QLabel(
            "Smallは1行、Mediumは2行、Largeは3行を移動します。",
            self.browser_wheel_scroll_group,
        )
        browser_wheel_note.setWordWrap(True)
        browser_wheel_form.addRow(browser_wheel_note)
        return self.browser_wheel_scroll_group

    def _build_browser_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        list_group = QGroupBox("一覧表示", tab)
        list_form = QFormLayout(list_group)
        self.browser_grid_preset_combo = QComboBox(list_group)
        for density, size in GRID_PRESET_THUMBNAIL_SIZES.items():
            label = BROWSER_DISPLAY_DENSITY_LABELS[density]
            self.browser_grid_preset_combo.addItem(
                f"{label} ({size}px)",
                density.value,
            )
        self.browser_grid_preset_combo.activated.connect(
            self._apply_browser_grid_preset
        )
        list_form.addRow("一覧プリセット:", self.browser_grid_preset_combo)

        self.browser_display_density_combo = QComboBox(list_group)
        for value, label in BROWSER_DISPLAY_DENSITY_LABELS.items():
            self.browser_display_density_combo.addItem(label, value.value)
        list_form.addRow("表示密度:", self.browser_display_density_combo)

        self.browser_sort_key_combo = QComboBox(list_group)
        for value, label in BROWSER_SORT_KEY_LABELS.items():
            self.browser_sort_key_combo.addItem(label, value.value)
        list_form.addRow("並び替え:", self.browser_sort_key_combo)

        self.browser_sort_order_combo = QComboBox(list_group)
        for value, label in BROWSER_SORT_ORDER_LABELS.items():
            self.browser_sort_order_combo.addItem(label, value.value)
        list_form.addRow("順序:", self.browser_sort_order_combo)

        self.browser_folders_first_checkbox = QCheckBox(
            "フォルダを常に先頭へ表示",
            list_group,
        )
        list_form.addRow(self.browser_folders_first_checkbox)
        self.browser_location_history_limit_spin = QSpinBox(list_group)
        self.browser_location_history_limit_spin.setRange(1, 1000)
        self.browser_location_history_limit_spin.setSuffix(" 件")
        list_form.addRow(
            "場所の履歴を保持する件数:",
            self.browser_location_history_limit_spin,
        )
        self.browser_search_history_limit_spin = QSpinBox(list_group)
        self.browser_search_history_limit_spin.setRange(0, 1000)
        self.browser_search_history_limit_spin.setSuffix(" 件")
        self.browser_search_history_limit_spin.setSpecialValueText("保存しない")
        list_form.addRow(
            "検索履歴の保持件数:",
            self.browser_search_history_limit_spin,
        )
        self.browser_spacing_preset_checkbox = QCheckBox(
            "密度プリセットに従う",
            list_group,
        )
        self.browser_spacing_preset_checkbox.toggled.connect(
            self._sync_browser_spacing_controls
        )
        list_form.addRow(self.browser_spacing_preset_checkbox)
        self.browser_item_spacing_spin = QSpinBox(list_group)
        self.browser_item_spacing_spin.setRange(0, 32)
        self.browser_item_spacing_spin.setSuffix(" px")
        list_form.addRow(
            "サムネイル間隔:",
            self.browser_item_spacing_spin,
        )
        self.browser_cell_padding_spin = QSpinBox(list_group)
        self.browser_cell_padding_spin.setRange(0, 12)
        self.browser_cell_padding_spin.setSuffix(" px")
        list_form.addRow(
            "セル内余白:",
            self.browser_cell_padding_spin,
        )
        self.browser_filename_display_combo = QComboBox(list_group)
        self.browser_filename_display_combo.addItem("非表示", "hidden")
        self.browser_filename_display_combo.addItem("1行", "one_line")
        self.browser_filename_display_combo.addItem("2行", "two_lines")
        list_form.addRow("ファイル名:", self.browser_filename_display_combo)
        self.browser_filename_gap_spin = QSpinBox(list_group)
        self.browser_filename_gap_spin.setRange(0, 32)
        self.browser_filename_gap_spin.setSuffix(" px")
        list_form.addRow("画像との間隔:", self.browser_filename_gap_spin)
        self.browser_filename_padding_y_spin = QSpinBox(list_group)
        self.browser_filename_padding_y_spin.setRange(0, 16)
        self.browser_filename_padding_y_spin.setSuffix(" px")
        list_form.addRow("ファイル名上下余白:", self.browser_filename_padding_y_spin)
        self.browser_show_hidden_checkbox = QCheckBox(
            "隠しファイルとフォルダを表示",
            list_group,
        )
        list_form.addRow(self.browser_show_hidden_checkbox)
        self.browser_show_unsupported_checkbox = QCheckBox(
            "非対応ファイルも表示",
            list_group,
        )
        list_form.addRow(self.browser_show_unsupported_checkbox)
        self.browser_show_system_checkbox = QCheckBox(
            "保護されたシステム項目を表示",
            list_group,
        )
        self.browser_show_system_checkbox.setToolTip(
            "Windowsの保護されたシステム項目を表示します。操作時は注意してください。"
        )
        list_form.addRow(self.browser_show_system_checkbox)

        cache_group = QGroupBox("サムネイル", tab)
        form = QFormLayout(cache_group)

        self.thumbnail_size_spin = QSpinBox(cache_group)
        self.thumbnail_size_spin.setRange(96, 384)
        self.thumbnail_size_spin.setSingleStep(32)
        self.thumbnail_size_spin.setSuffix(" px")
        form.addRow("サムネイルサイズ:", self.thumbnail_size_spin)

        self.thumbnail_frame_ratio_combo = QComboBox(cache_group)
        for ratio_id, (_ratio, label) in FRAME_RATIOS.items():
            self.thumbnail_frame_ratio_combo.addItem(label, ratio_id)
        form.addRow("画像枠の比率:", self.thumbnail_frame_ratio_combo)

        self.browser_thumbnail_display_mode_combo = QComboBox(cache_group)
        for mode, label in BROWSER_THUMBNAIL_DISPLAY_MODES.items():
            self.browser_thumbnail_display_mode_combo.addItem(label, mode)
        self.browser_thumbnail_display_mode_combo.setToolTip(
            "全体表示は画像全体を枠内へ収めます。"
            "中央クロップは縦横比を保ったまま画像中央で枠を埋めます。"
        )
        form.addRow(
            "Browser表示方式:",
            self.browser_thumbnail_display_mode_combo,
        )

        self.browser_folder_fallback_background_combo = QComboBox(cache_group)
        self.browser_folder_fallback_background_combo.addItem(
            "自動（ZipPla互換の黒）",
            "auto",
        )
        self.browser_folder_fallback_background_combo.addItem(
            "カスタム色",
            "custom",
        )
        self.browser_folder_fallback_background_combo.currentIndexChanged.connect(
            self._sync_folder_fallback_background_controls
        )
        self.browser_folder_fallback_color_button = QPushButton(
            "色を選択…",
            cache_group,
        )
        self.browser_folder_fallback_color_button.clicked.connect(
            self._choose_folder_fallback_background_color
        )
        self.browser_folder_fallback_restore_button = QPushButton(
            "既定に戻す",
            cache_group,
        )
        self.browser_folder_fallback_restore_button.clicked.connect(
            self._restore_folder_fallback_background
        )
        folder_fallback_controls = QWidget(cache_group)
        folder_fallback_layout = QHBoxLayout(folder_fallback_controls)
        folder_fallback_layout.setContentsMargins(0, 0, 0, 0)
        folder_fallback_layout.addWidget(
            self.browser_folder_fallback_background_combo
        )
        folder_fallback_layout.addWidget(self.browser_folder_fallback_color_button)
        folder_fallback_layout.addWidget(
            self.browser_folder_fallback_restore_button
        )
        form.addRow("代替サムネイル背景:", folder_fallback_controls)

        self.thumbnail_crop_mode_combo = QComboBox(cache_group)
        for mode, label in CROP_MODES.items():
            self.thumbnail_crop_mode_combo.addItem(label, mode)
        form.addRow("切り抜き:", self.thumbnail_crop_mode_combo)

        self.thumbnail_quality_mode_combo = QComboBox(cache_group)
        self.thumbnail_quality_mode_combo.addItem("容量優先", "economy")
        self.thumbnail_quality_mode_combo.addItem("自動・推奨", "auto")
        self.thumbnail_quality_mode_combo.addItem("高画質", "high")
        self.thumbnail_quality_mode_combo.setToolTip(
            "容量優先: 表示に近い解像度。自動: 高DPIと再縮小を考慮。"
            "高画質: より大きなキャッシュを使用します。"
        )
        form.addRow("生成品質:", self.thumbnail_quality_mode_combo)

        self.thumbnail_cache_max_edge_spin = QSpinBox(cache_group)
        self.thumbnail_cache_max_edge_spin.setRange(256, 2048)
        self.thumbnail_cache_max_edge_spin.setSingleStep(256)
        self.thumbnail_cache_max_edge_spin.setSuffix(" px")
        form.addRow("生成最大辺:", self.thumbnail_cache_max_edge_spin)

        bucket_note = QLabel(
            "論理表示サイズと画面DPIから物理解像度を選び、複数のbucketを"
            "再利用します。設定変更後も互換キャッシュは再利用されます。"
            "完全に作り直す場合だけ「キャッシュを削除」を使用してください。",
            cache_group,
        )
        bucket_note.setWordWrap(True)
        form.addRow(bucket_note)

        self.disk_cache_checkbox = QCheckBox(
            "ディスクサムネイルキャッシュを使用する",
            cache_group,
        )
        form.addRow(self.disk_cache_checkbox)

        self.cache_limit_spin = QSpinBox(cache_group)
        self.cache_limit_spin.setRange(128, 4096)
        self.cache_limit_spin.setSuffix(" MB")
        form.addRow("キャッシュ最大容量:", self.cache_limit_spin)

        self.cache_unused_days_combo = QComboBox(cache_group)
        for label, days in (
            ("使用しない", 0),
            ("30日", 30),
            ("90日（推奨）", 90),
            ("180日", 180),
            ("365日", 365),
            ("カスタム", -1),
        ):
            self.cache_unused_days_combo.addItem(label, days)
        self.cache_unused_days_spin = QSpinBox(cache_group)
        self.cache_unused_days_spin.setRange(7, 3650)
        self.cache_unused_days_spin.setSuffix(" 日")
        self.cache_unused_days_combo.currentIndexChanged.connect(
            lambda _index: self.cache_unused_days_spin.setEnabled(
                int(self.cache_unused_days_combo.currentData()) == -1
            )
        )
        form.addRow("未使用期間の整理:", self.cache_unused_days_combo)
        form.addRow("カスタム日数:", self.cache_unused_days_spin)
        cleanup_warning = QLabel(
            "短い期間では再生成とSSD書き込みが増える可能性があります。",
            cache_group,
        )
        cleanup_warning.setWordWrap(True)
        form.addRow(cleanup_warning)

        self.cache_usage_label = QLabel(cache_group)
        self.clear_cache_button = QPushButton("キャッシュを削除", cache_group)
        self.clear_cache_button.clicked.connect(self.request_cache_clear)
        self.cleanup_cache_button = QPushButton("今すぐ整理", cache_group)
        self.cleanup_cache_button.clicked.connect(
            self.cache_cleanup_requested.emit
        )
        usage_row = QWidget(cache_group)
        usage_layout = QHBoxLayout(usage_row)
        usage_layout.setContentsMargins(0, 0, 0, 0)
        usage_layout.addWidget(self.cache_usage_label, 1)
        usage_layout.addWidget(self.cleanup_cache_button)
        usage_layout.addWidget(self.clear_cache_button)
        form.addRow("現在の使用量:", usage_row)

        layout.addWidget(list_group)
        layout.addWidget(cache_group)

        preview_group = QGroupBox("汎用ファイルプレビュー", tab)
        preview_form = QFormLayout(preview_group)
        self.text_preview_checkbox = QCheckBox(
            "テキストファイルの内容をプレビューする",
            preview_group,
        )
        preview_form.addRow(self.text_preview_checkbox)
        self.video_thumbnail_checkbox = QCheckBox(
            "動画のサムネイルを表示する",
            preview_group,
        )
        preview_form.addRow(self.video_thumbnail_checkbox)
        self.video_thumbnail_backend_combo = QComboBox(preview_group)
        self.video_thumbnail_backend_combo.addItem("自動・推奨", "auto")
        self.video_thumbnail_backend_combo.addItem(
            "Windows Shellのみ",
            "windows_shell",
        )
        self.video_thumbnail_backend_combo.addItem("FFmpegのみ", "ffmpeg")
        self.video_thumbnail_backend_combo.addItem("無効", "disabled")
        preview_form.addRow(
            "動画バックエンド:",
            self.video_thumbnail_backend_combo,
        )
        self.video_thumbnail_frame_mode_combo = QComboBox(preview_group)
        self.video_thumbnail_frame_mode_combo.addItem("代表フレーム（推奨）", "smart")
        self.video_thumbnail_frame_mode_combo.addItem("再生時間の1/3", "one_third")
        self.video_thumbnail_frame_mode_combo.addItem(
            "Windows Shellの選択",
            "windows_shell",
        )
        preview_form.addRow(
            "動画フレーム:",
            self.video_thumbnail_frame_mode_combo,
        )
        self.video_thumbnail_shell_placeholder_checkbox = QCheckBox(
            "FFmpeg結果までShell画像を一時表示する",
            preview_group,
        )
        preview_form.addRow(self.video_thumbnail_shell_placeholder_checkbox)
        self.ffmpeg_path_edit = QLineEdit(preview_group)
        ffmpeg_path_row = QWidget(preview_group)
        ffmpeg_path_layout = QHBoxLayout(ffmpeg_path_row)
        ffmpeg_path_layout.setContentsMargins(0, 0, 0, 0)
        ffmpeg_path_layout.addWidget(self.ffmpeg_path_edit, 1)
        self.ffmpeg_browse_button = QPushButton("参照...", preview_group)
        self.ffmpeg_browse_button.clicked.connect(self.browse_ffmpeg)
        ffmpeg_path_layout.addWidget(self.ffmpeg_browse_button)
        self.ffmpeg_redetect_button = QPushButton("再検出", preview_group)
        self.ffmpeg_redetect_button.clicked.connect(self.redetect_ffmpeg)
        ffmpeg_path_layout.addWidget(self.ffmpeg_redetect_button)
        preview_form.addRow("FFmpeg:", ffmpeg_path_row)
        self.ffmpeg_status_label = QLabel("FFmpeg：未確認", preview_group)
        self.ffmpeg_status_label.setWordWrap(True)
        preview_form.addRow("現在の状態:", self.ffmpeg_status_label)
        ffmpeg_note = QLabel(
            "FFmpegは任意です。自動ダウンロードや自動同梱は行いません。",
            preview_group,
        )
        ffmpeg_note.setWordWrap(True)
        preview_form.addRow(ffmpeg_note)
        self.browser_external_drop_combo = QComboBox(preview_group)
        self.browser_external_drop_combo.addItem(
            "一覧で選択・中央表示のみ",
            "focus_only",
        )
        self.browser_external_drop_combo.addItem(
            "選択後に対応ファイルを開く",
            "focus_and_open",
        )
        preview_form.addRow(
            "Browser中央への外部ドロップ:",
            self.browser_external_drop_combo,
        )
        layout.addWidget(preview_group)

        sidebar_group = QGroupBox("サイドバー", tab)
        sidebar_form = QFormLayout(sidebar_group)
        self.browser_sidebar_layout_combo = QComboBox(sidebar_group)
        for value, label in (
            ("favorites_top_tree_bottom", "お気に入り上／ツリー下"),
            ("tree_top_favorites_bottom", "ツリー上／お気に入り下"),
            ("tabs", "タブ"),
            ("favorites_only", "お気に入りのみ"),
            ("tree_only", "ツリーのみ"),
        ):
            self.browser_sidebar_layout_combo.addItem(label, value)
        sidebar_form.addRow(
            "レイアウト:",
            self.browser_sidebar_layout_combo,
        )
        self.folder_tree_sync_mode_combo = QComboBox(sidebar_group)
        self.folder_tree_sync_mode_combo.addItem("同期しない", "off")
        self.folder_tree_sync_mode_combo.addItem(
            "現在フォルダを選択",
            "select_current",
        )
        self.folder_tree_sync_mode_combo.addItem(
            "現在フォルダへフォーカス",
            "focus_current",
        )
        sidebar_form.addRow(
            "フォルダツリー同期:",
            self.folder_tree_sync_mode_combo,
        )
        self.folder_tree_collapse_checkbox = QCheckBox(
            "無関係な自動展開を折りたたむ",
            sidebar_group,
        )
        sidebar_form.addRow(self.folder_tree_collapse_checkbox)
        self.folder_tree_focus_rebase_checkbox = QCheckBox(
            "現在フォルダを基準にツリーの表示ルートを絞る",
            sidebar_group,
        )
        sidebar_form.addRow(self.folder_tree_focus_rebase_checkbox)
        self.folder_tree_ancestor_levels_spin = QSpinBox(sidebar_group)
        self.folder_tree_ancestor_levels_spin.setRange(0, 12)
        sidebar_form.addRow(
            "現在フォルダの上位階層:",
            self.folder_tree_ancestor_levels_spin,
        )
        self.favorite_row_padding_spin = QSpinBox(sidebar_group)
        self.favorite_row_padding_spin.setRange(0, 8)
        sidebar_form.addRow(
            "お気に入り上下余白:",
            self.favorite_row_padding_spin,
        )
        self.favorite_row_spacing_spin = QSpinBox(sidebar_group)
        self.favorite_row_spacing_spin.setRange(0, 8)
        sidebar_form.addRow(
            "お気に入り行間隔:",
            self.favorite_row_spacing_spin,
        )
        self.favorite_icon_size_spin = QSpinBox(sidebar_group)
        self.favorite_icon_size_spin.setRange(14, 24)
        sidebar_form.addRow(
            "お気に入りアイコン:",
            self.favorite_icon_size_spin,
        )
        tree_focus_note = QLabel(
            "深いフォルダでインデントが増えすぎないよう、現在フォルダから"
            "指定した階層だけ上をツリーの表示基準にします。",
            sidebar_group,
        )
        tree_focus_note.setWordWrap(True)
        sidebar_form.addRow(tree_focus_note)
        layout.addWidget(sidebar_group)
        layout.addStretch(1)
        return tab

    def _build_mouse_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        layout.addWidget(self._build_browser_wheel_scroll_group(tab))

        gesture_group = QGroupBox("Viewer画像表示領域", tab)
        gesture_form = QFormLayout(gesture_group)
        self.mouse_gestures_checkbox = QCheckBox(
            "Viewer画像表示領域でマウスジェスチャーを使用する",
            gesture_group,
        )
        self.mouse_gestures_checkbox.toggled.connect(self._sync_gesture_controls)
        gesture_form.addRow(self.mouse_gestures_checkbox)
        self.mouse_gesture_trail_checkbox = QCheckBox(
            "操作中に軌跡を表示する",
            gesture_group,
        )
        gesture_form.addRow(self.mouse_gesture_trail_checkbox)
        self.mouse_gesture_distance_spin = QSpinBox(gesture_group)
        self.mouse_gesture_distance_spin.setRange(12, 200)
        self.mouse_gesture_distance_spin.setSuffix(" px")
        gesture_form.addRow("認識最小距離:", self.mouse_gesture_distance_spin)
        self.gesture_down_combo = self._command_combo(gesture_group)
        self.gesture_up_combo = self._command_combo(gesture_group)
        gesture_form.addRow("下へドラッグ (D):", self.gesture_down_combo)
        gesture_form.addRow("上へドラッグ (U):", self.gesture_up_combo)

        browser_gesture_group = QGroupBox(
            "Browserサムネイル一覧領域",
            tab,
        )
        browser_gesture_layout = QVBoxLayout(browser_gesture_group)
        self.browser_folder_gestures_checkbox = QCheckBox(
            "Browserのサムネイル一覧でフォルダージェスチャーを使用する",
            browser_gesture_group,
        )
        browser_gesture_layout.addWidget(self.browser_folder_gestures_checkbox)
        browser_gesture_description = QLabel(
            "上：上の階層へ移動　左：前のフォルダー　"
            "右：次のフォルダー　下：フォルダーを更新",
            browser_gesture_group,
        )
        browser_gesture_description.setWordWrap(True)
        browser_gesture_layout.addWidget(browser_gesture_description)

        button_group = QGroupBox("マウス追加ボタン", tab)
        button_form = QFormLayout(button_group)
        self.mouse_back_action_combo = self._command_combo(button_group)
        self.mouse_forward_action_combo = self._command_combo(button_group)
        button_form.addRow("戻る / XButton1:", self.mouse_back_action_combo)
        button_form.addRow("進む / XButton2:", self.mouse_forward_action_combo)

        layout.addWidget(gesture_group)
        layout.addWidget(browser_gesture_group)
        layout.addWidget(button_group)
        layout.addStretch(1)
        return tab

    def load_current_values(self) -> None:
        behavior = str(self.config.get("open_viewer_behavior", "reuse_or_create"))
        index = self.open_behavior_combo.findData(behavior)
        self.open_behavior_combo.setCurrentIndex(max(0, index))
        self.bring_to_front_checkbox.setChecked(
            bool(self.config.get("bring_viewer_to_front_on_open", True))
        )
        self.loop_navigation_checkbox.setChecked(
            bool(self.config.get("loop_book_navigation", False))
        )
        self.join_spread_checkbox.setChecked(
            bool(self.config.get("join_spread_pages", False))
        )
        self.gap_spin.setValue(int(self.config.get("gap", 12)))
        self.single_first_checkbox.setChecked(
            bool(self.config.get("single_first_page", True))
        )
        self.wide_single_checkbox.setChecked(
            bool(self.config.get("treat_wide_image_as_single", True))
        )
        self._select_data(
            self.book_open_position_combo,
            self.config.get("book_open_position", "first_page"),
        )
        self._select_data(
            self.viewer_canvas_click_direction_combo,
            self.config.get("viewer_canvas_click_direction", "right_next"),
        )
        self._select_data(
            self.viewer_canvas_left_click_combo,
            self.config.get(
                "viewer_canvas_left_click_action",
                "next_single_page",
            ),
        )
        self.viewer_slider_wheel_single_page_checkbox.setChecked(
            bool(
                self.config.get(
                    "viewer_slider_wheel_single_page_enabled",
                    False,
                )
            )
        )
        self._custom_prefetch_values = {
            "image_forward_units": int(
                self.config.get("viewer_prefetch_image_forward_units", 3)
            ),
            "image_backward_units": int(
                self.config.get("viewer_prefetch_image_backward_units", 3)
            ),
            "pdf_forward_units": int(
                self.config.get("viewer_prefetch_pdf_forward_units", 3)
            ),
            "pdf_backward_units": int(
                self.config.get("viewer_prefetch_pdf_backward_units", 3)
            ),
        }
        self._displayed_prefetch_preset = None
        self._select_data(
            self.prefetch_preset_combo,
            self.config.get("viewer_prefetch_preset", "standard"),
        )
        self._select_data(
            self.viewer_memory_mode_combo,
            self.config.get("viewer_memory_mode", "auto"),
        )
        self._select_data(
            self.viewer_downscale_algorithm_combo,
            self.config.get("viewer_downscale_algorithm", "auto"),
        )
        self._select_data(
            self.viewer_upscale_algorithm_combo,
            self.config.get("viewer_upscale_algorithm", "auto"),
        )
        self._select_data(
            self.magnifier_downscale_algorithm_combo,
            self.config.get("magnifier_downscale_algorithm", "sharp"),
        )
        self._select_data(
            self.magnifier_upscale_algorithm_combo,
            self.config.get("magnifier_upscale_algorithm", "lanczos"),
        )
        self.prefetch_direction_priority_checkbox.setChecked(
            bool(
                self.config.get(
                    "viewer_prefetch_direction_priority_enabled",
                    True,
                )
            )
        )
        self._on_prefetch_preset_changed(
            self.prefetch_preset_combo.currentIndex()
        )
        self.fullscreen_hide_ui_checkbox.setChecked(
            bool(self.config.get("hide_ui_in_fullscreen", False))
        )
        self.fullscreen_hide_cursor_checkbox.setChecked(
            bool(self.config.get("hide_cursor_in_fullscreen", False))
        )
        self.fullscreen_auto_reveal_checkbox.setChecked(
            bool(self.config.get("fullscreen_auto_reveal_ui", True))
        )
        self.fullscreen_top_edge_trigger_spin.setValue(
            int(
                self.config.get(
                    "fullscreen_top_edge_trigger_px",
                    self.config.get("fullscreen_edge_trigger_px", 8),
                )
            )
        )
        self.fullscreen_bottom_edge_trigger_spin.setValue(
            int(
                self.config.get(
                    "fullscreen_bottom_edge_trigger_px",
                    28,
                )
            )
        )
        self.fullscreen_hide_delay_spin.setValue(
            int(self.config.get("fullscreen_ui_hide_delay_ms", 0))
        )
        self.thumbnail_size_spin.setValue(int(self.config.get("thumbnail_size", 180)))
        self._select_data(
            self.thumbnail_frame_ratio_combo,
            self.config.get("thumbnail_frame_ratio", "portrait_1_sqrt2"),
        )
        self._select_data(
            self.thumbnail_crop_mode_combo,
            self.config.get("thumbnail_crop_mode", "smart_crop"),
        )
        self._select_data(
            self.browser_thumbnail_display_mode_combo,
            self.config.get("browser_thumbnail_display_mode", "fit"),
        )
        fallback_background = str(
            self.config.get("browser_folder_fallback_background", "auto")
        )
        if fallback_background == "auto":
            self._folder_fallback_custom_color = "#000000"
            self._select_data(
                self.browser_folder_fallback_background_combo,
                "auto",
            )
        else:
            color = QColor(fallback_background)
            self._folder_fallback_custom_color = (
                color.name() if color.isValid() else "#000000"
            )
            self._select_data(
                self.browser_folder_fallback_background_combo,
                "custom",
            )
        self._sync_folder_fallback_background_controls()
        self._select_data(
            self.thumbnail_quality_mode_combo,
            self.config.get("thumbnail_quality_mode", "auto"),
        )
        self.thumbnail_cache_max_edge_spin.setValue(
            int(self.config.get("thumbnail_cache_max_edge", 1024))
        )
        self._select_data(
            self.browser_display_density_combo,
            self.config.get(
                "browser_display_density",
                BrowserDisplayDensity.STANDARD.value,
            ),
        )
        self._select_data(
            self.browser_filename_display_combo,
            self.config.get("browser_filename_display", "one_line"),
        )
        self.browser_filename_gap_spin.setValue(
            int(self.config.get("browser_filename_gap", 0))
        )
        self.browser_filename_padding_y_spin.setValue(
            int(self.config.get("browser_filename_padding_y", 0))
        )
        self.browser_show_hidden_checkbox.setChecked(
            bool(self.config.get("browser_show_hidden_items", True))
        )
        self.browser_show_unsupported_checkbox.setChecked(
            bool(self.config.get("browser_show_unsupported_files", True))
        )
        self.browser_show_system_checkbox.setChecked(
            bool(self.config.get("browser_show_system_items", False))
        )
        self._select_data(
            self.browser_wheel_scroll_mode_combo,
            self.config.get("browser_wheel_scroll_mode", "system"),
        )
        self.browser_wheel_scroll_custom_spin.setValue(
            int(self.config.get("browser_wheel_scroll_custom_rows", 3))
        )
        self._sync_browser_wheel_scroll_controls()
        self._sync_browser_grid_preset()
        self._select_data(
            self.browser_sort_key_combo,
            self.config.get("browser_sort_key", BrowserSortKey.NAME.value),
        )
        self._select_data(
            self.browser_sort_order_combo,
            self.config.get(
                "browser_sort_order",
                BrowserSortOrder.ASCENDING.value,
            ),
        )
        self.browser_folders_first_checkbox.setChecked(
            bool(self.config.get("browser_folders_first", True))
        )
        self.browser_location_history_limit_spin.setValue(
            int(self.config.get("browser_location_history_limit", 50))
        )
        self.browser_search_history_limit_spin.setValue(
            int(self.config.get("browser_search_history_limit", 50))
        )
        self.browser_spacing_preset_checkbox.setChecked(
            self.config.get("browser_item_spacing_mode", "preset") == "preset"
        )
        self.browser_item_spacing_spin.setValue(
            int(self.config.get("browser_item_spacing", 2))
        )
        self.browser_cell_padding_spin.setValue(
            int(self.config.get("browser_cell_padding", 0))
        )
        self._select_data(
            self.browser_sidebar_layout_combo,
            self.config.get(
                "browser_sidebar_layout",
                "favorites_top_tree_bottom",
            ),
        )
        self._select_data(
            self.folder_tree_sync_mode_combo,
            self.config.get("folder_tree_sync_mode", "focus_current"),
        )
        self.folder_tree_collapse_checkbox.setChecked(
            bool(self.config.get("folder_tree_collapse_unrelated", True))
        )
        self.folder_tree_focus_rebase_checkbox.setChecked(
            bool(self.config.get("folder_tree_focus_rebase", True))
        )
        self.folder_tree_ancestor_levels_spin.setValue(
            int(self.config.get("folder_tree_context_ancestor_levels", 3))
        )
        self.favorite_row_padding_spin.setValue(
            int(self.config.get("favorite_row_padding_y", 1))
        )
        self.favorite_row_spacing_spin.setValue(
            int(self.config.get("favorite_row_spacing", 0))
        )
        self.favorite_icon_size_spin.setValue(
            int(self.config.get("favorite_icon_size", 16))
        )
        self._sync_browser_spacing_controls(
            self.browser_spacing_preset_checkbox.isChecked()
        )
        self.disk_cache_checkbox.setChecked(
            bool(self.config.get("thumbnail_disk_cache_enabled", True))
        )
        self.cache_limit_spin.setValue(
            int(self.config.get("thumbnail_cache_limit_mb", 512))
        )
        unused_days = int(self.config.get("thumbnail_cache_max_unused_days", 0))
        preset_index = self.cache_unused_days_combo.findData(unused_days)
        if preset_index < 0:
            preset_index = self.cache_unused_days_combo.findData(-1)
        self.cache_unused_days_combo.setCurrentIndex(preset_index)
        self.cache_unused_days_spin.setValue(max(7, unused_days or 90))
        self.cache_unused_days_spin.setEnabled(
            int(self.cache_unused_days_combo.currentData()) == -1
        )
        self.text_preview_checkbox.setChecked(
            bool(self.config.get("text_preview_enabled", True))
        )
        self.video_thumbnail_checkbox.setChecked(
            bool(self.config.get("video_thumbnail_enabled", True))
        )
        self._select_data(
            self.video_thumbnail_backend_combo,
            self.config.get("video_thumbnail_backend", "auto"),
        )
        self._select_data(
            self.video_thumbnail_frame_mode_combo,
            self.config.get("video_thumbnail_frame_mode", "smart"),
        )
        self.video_thumbnail_shell_placeholder_checkbox.setChecked(
            bool(self.config.get("video_thumbnail_shell_placeholder", True))
        )
        self.ffmpeg_path_edit.setText(
            str(self.config.get("ffmpeg_executable", "") or "")
        )
        self._select_data(
            self.browser_external_drop_combo,
            self.config.get("browser_external_drop_behavior", "focus_only"),
        )
        self._select_data(
            self.archive_backend_combo,
            self.config.get("archive_backend_preference", "auto"),
        )
        self.winrar_path_edit.setText(
            str(self.config.get("winrar_executable", "") or "")
        )
        self.seven_zip_path_edit.setText(
            str(self.config.get("seven_zip_executable", "") or "")
        )
        self.mouse_gestures_checkbox.setChecked(
            bool(self.config.get("mouse_gestures_enabled", True))
        )
        self.browser_folder_gestures_checkbox.setChecked(
            bool(self.config.get("browser_folder_gestures_enabled", True))
        )
        self.mouse_gesture_trail_checkbox.setChecked(
            bool(self.config.get("mouse_gesture_show_trail", True))
        )
        self.mouse_gesture_distance_spin.setValue(
            int(self.config.get("mouse_gesture_min_distance", 36))
        )
        raw_bindings = self.config.get("mouse_gesture_bindings", {})
        self._gesture_bindings_base = (
            dict(raw_bindings) if isinstance(raw_bindings, dict) else {}
        )
        self._select_command(
            self.gesture_down_combo,
            self._gesture_bindings_base.get("D", ""),
        )
        self._select_command(
            self.gesture_up_combo,
            self._gesture_bindings_base.get("U", ""),
        )
        self._select_command(
            self.mouse_back_action_combo,
            self.config.get("mouse_back_button_action", ""),
        )
        self._select_command(
            self.mouse_forward_action_combo,
            self.config.get("mouse_forward_button_action", ""),
        )
        self._sync_gap_enabled(self.join_spread_checkbox.isChecked())
        self._sync_gesture_controls(self.mouse_gestures_checkbox.isChecked())
        self.refresh_cache_usage()
        self.refresh_registration_status()

    def _choose_folder_fallback_background_color(self) -> None:
        color = QColorDialog.getColor(
            QColor(self._folder_fallback_custom_color),
            self,
            "代替サムネイル背景色",
        )
        if not color.isValid():
            return
        self._folder_fallback_custom_color = color.name()
        self._select_data(
            self.browser_folder_fallback_background_combo,
            "custom",
        )
        self._sync_folder_fallback_background_controls()

    def _restore_folder_fallback_background(self) -> None:
        self._folder_fallback_custom_color = "#000000"
        self._select_data(
            self.browser_folder_fallback_background_combo,
            "auto",
        )
        self._sync_folder_fallback_background_controls()

    def _restore_browser_wheel_scroll_default(self) -> None:
        self._select_data(self.browser_wheel_scroll_mode_combo, "system")
        self.browser_wheel_scroll_custom_spin.setValue(3)
        self._sync_browser_wheel_scroll_controls()

    def _sync_browser_wheel_scroll_controls(
        self,
        *_args: object,
    ) -> None:
        self.browser_wheel_scroll_custom_spin.setEnabled(
            self.browser_wheel_scroll_mode_combo.currentData() == "custom"
        )

    def _sync_folder_fallback_background_controls(
        self,
        *_args: object,
    ) -> None:
        custom = (
            self.browser_folder_fallback_background_combo.currentData()
            == "custom"
        )
        self.browser_folder_fallback_color_button.setEnabled(custom)
        color = QColor(self._folder_fallback_custom_color)
        text_color = "#000000" if color.lightness() >= 128 else "#ffffff"
        self.browser_folder_fallback_color_button.setText(color.name().upper())
        self.browser_folder_fallback_color_button.setStyleSheet(
            "QPushButton {"
            f"background-color: {color.name()}; color: {text_color};"
            "}"
        )

    def _apply_browser_grid_preset(self, index: int) -> None:
        density_value = str(self.browser_grid_preset_combo.itemData(index))
        density = next(
            (
                candidate
                for candidate in GRID_PRESET_THUMBNAIL_SIZES
                if candidate.value == density_value
            ),
            None,
        )
        if density is None:
            return
        self._select_data(self.browser_display_density_combo, density.value)
        self.thumbnail_size_spin.setValue(
            GRID_PRESET_THUMBNAIL_SIZES[density]
        )

    def _sync_browser_grid_preset(self) -> None:
        density_value = str(self.browser_display_density_combo.currentData())
        density = next(
            (
                candidate
                for candidate in GRID_PRESET_THUMBNAIL_SIZES
                if candidate.value == density_value
            ),
            None,
        )
        expected_size = (
            GRID_PRESET_THUMBNAIL_SIZES.get(density)
            if density is not None
            else None
        )
        index = (
            self.browser_grid_preset_combo.findData(density_value)
            if expected_size == self.thumbnail_size_spin.value()
            else -1
        )
        self.browser_grid_preset_combo.blockSignals(True)
        self.browser_grid_preset_combo.setCurrentIndex(index if index >= 0 else -1)
        self.browser_grid_preset_combo.blockSignals(False)

    def _sync_browser_spacing_controls(self, use_preset: bool) -> None:
        self.browser_item_spacing_spin.setEnabled(not use_preset)
        self.browser_cell_padding_spin.setEnabled(True)

    def refresh_registration_status(self) -> None:
        service = self._file_registration_service
        if service is None:
            self.registration_status_label.setText(
                "Windows関連付けはこの起動環境では利用できません。"
            )
            return
        status = service.get_status()
        if status.registered:
            match = (
                "現在の実行ファイルと一致"
                if status.matches_current_executable
                else "現在の実行ファイルと不一致（移動後は再登録が必要）"
            )
            self.registration_status_label.setText(
                f"登録済み: {', '.join(status.registered_extensions)}\n"
                f"{status.executable_path}\n{match}"
            )
        else:
            detail = f"\n{status.error_message}" if status.error_message else ""
            self.registration_status_label.setText(f"未登録{detail}")

    def _register_with_windows(self) -> None:
        service = self._file_registration_service
        if service is None:
            return
        from .supported_formats import FORMAT_CATEGORIES

        extensions: set[str] = set()
        if self.register_images_checkbox.isChecked():
            extensions.update(FORMAT_CATEGORIES["image"])
        if self.register_archives_checkbox.isChecked():
            extensions.update(FORMAT_CATEGORIES["archive"])
        if self.register_pdf_checkbox.isChecked():
            extensions.update(FORMAT_CATEGORIES["pdf"])
        if not extensions:
            QMessageBox.information(self, "Windows連携", "登録する形式を選択してください。")
            return
        answer = QMessageBox.question(
            self,
            "Windowsへ登録",
            "選択した形式の「プログラムから開く」候補へNivisViewerを登録しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        status = service.register(
            tuple(extensions),
            add_context_menu=self.register_context_menu_checkbox.isChecked(),
        )
        self.refresh_registration_status()
        if status.error_message:
            QMessageBox.warning(self, "Windows連携", status.error_message)

    def _unregister_from_windows(self) -> None:
        service = self._file_registration_service
        if service is None:
            return
        answer = QMessageBox.question(
            self,
            "登録を解除",
            "NivisViewerが作成したWindows関連付け情報を解除しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        status = service.unregister()
        self.refresh_registration_status()
        if status.error_message:
            QMessageBox.warning(self, "Windows連携", status.error_message)

    def _open_default_apps(self) -> None:
        service = self._file_registration_service
        if service is not None and not service.open_default_apps_settings():
            QMessageBox.warning(
                self,
                "Windows連携",
                "Windowsの既定のアプリ設定を開けませんでした。",
            )

    def _on_prefetch_preset_changed(self, index: int) -> None:
        preset = str(self.prefetch_preset_combo.itemData(index) or "standard")
        if (
            self._displayed_prefetch_preset == "custom"
            and preset != "custom"
        ):
            self._custom_prefetch_values = self._prefetch_spin_values()
        values = (
            self._custom_prefetch_values
            if preset == "custom"
            else ConfigManager.VIEWER_PREFETCH_PRESETS.get(
                preset,
                ConfigManager.VIEWER_PREFETCH_PRESETS["standard"],
            )
        )
        self.prefetch_image_forward_spin.setValue(
            int(values["image_forward_units"])
        )
        self.prefetch_image_backward_spin.setValue(
            int(values["image_backward_units"])
        )
        self.prefetch_pdf_forward_spin.setValue(
            int(values["pdf_forward_units"])
        )
        self.prefetch_pdf_backward_spin.setValue(
            int(values["pdf_backward_units"])
        )
        self.prefetch_custom_group.setEnabled(preset == "custom")
        self._displayed_prefetch_preset = preset

    def _prefetch_spin_values(self) -> dict[str, int]:
        return {
            "image_forward_units": self.prefetch_image_forward_spin.value(),
            "image_backward_units": self.prefetch_image_backward_spin.value(),
            "pdf_forward_units": self.prefetch_pdf_forward_spin.value(),
            "pdf_backward_units": self.prefetch_pdf_backward_spin.value(),
        }

    def values(self) -> dict[str, object]:
        bindings = dict(self._gesture_bindings_base)
        for pattern, combo in (
            ("D", self.gesture_down_combo),
            ("U", self.gesture_up_combo),
        ):
            command = str(combo.currentData() or "")
            if command:
                bindings[pattern] = command
            else:
                bindings.pop(pattern, None)
        preset = str(self.prefetch_preset_combo.currentData() or "standard")
        if preset == "custom":
            self._custom_prefetch_values = self._prefetch_spin_values()
        custom_prefetch = self._custom_prefetch_values
        return {
            "open_viewer_behavior": self.open_behavior_combo.currentData(),
            "bring_viewer_to_front_on_open": self.bring_to_front_checkbox.isChecked(),
            "loop_book_navigation": self.loop_navigation_checkbox.isChecked(),
            "join_spread_pages": self.join_spread_checkbox.isChecked(),
            "gap": self.gap_spin.value(),
            "single_first_page": self.single_first_checkbox.isChecked(),
            "treat_wide_image_as_single": self.wide_single_checkbox.isChecked(),
            "book_open_position": str(
                self.book_open_position_combo.currentData() or "first_page"
            ),
            "viewer_canvas_click_direction": str(
                self.viewer_canvas_click_direction_combo.currentData()
            ),
            "viewer_canvas_left_click_action": str(
                self.viewer_canvas_left_click_combo.currentData()
            ),
            "viewer_slider_wheel_single_page_enabled": (
                self.viewer_slider_wheel_single_page_checkbox.isChecked()
            ),
            "viewer_prefetch_preset": preset,
            "viewer_prefetch_direction_priority_enabled": (
                self.prefetch_direction_priority_checkbox.isChecked()
            ),
            "viewer_memory_mode": str(
                self.viewer_memory_mode_combo.currentData() or "auto"
            ),
            "viewer_downscale_algorithm": str(
                self.viewer_downscale_algorithm_combo.currentData() or "auto"
            ),
            "viewer_upscale_algorithm": str(
                self.viewer_upscale_algorithm_combo.currentData() or "auto"
            ),
            "magnifier_downscale_algorithm": str(
                self.magnifier_downscale_algorithm_combo.currentData()
                or "sharp"
            ),
            "magnifier_upscale_algorithm": str(
                self.magnifier_upscale_algorithm_combo.currentData()
                or "lanczos"
            ),
            "viewer_prefetch_image_forward_units": custom_prefetch[
                "image_forward_units"
            ],
            "viewer_prefetch_image_backward_units": custom_prefetch[
                "image_backward_units"
            ],
            "viewer_prefetch_pdf_forward_units": custom_prefetch[
                "pdf_forward_units"
            ],
            "viewer_prefetch_pdf_backward_units": custom_prefetch[
                "pdf_backward_units"
            ],
            "hide_ui_in_fullscreen": self.fullscreen_hide_ui_checkbox.isChecked(),
            "hide_cursor_in_fullscreen": (
                self.fullscreen_hide_cursor_checkbox.isChecked()
            ),
            "fullscreen_auto_reveal_ui": (
                self.fullscreen_auto_reveal_checkbox.isChecked()
            ),
            "fullscreen_edge_trigger_px": (
                self.fullscreen_top_edge_trigger_spin.value()
            ),
            "fullscreen_top_edge_trigger_px": (
                self.fullscreen_top_edge_trigger_spin.value()
            ),
            "fullscreen_bottom_edge_trigger_px": (
                self.fullscreen_bottom_edge_trigger_spin.value()
            ),
            "fullscreen_ui_hide_delay_ms": self.fullscreen_hide_delay_spin.value(),
            "thumbnail_size": self.thumbnail_size_spin.value(),
            "thumbnail_frame_ratio": str(
                self.thumbnail_frame_ratio_combo.currentData()
            ),
            "thumbnail_crop_mode": str(
                self.thumbnail_crop_mode_combo.currentData()
            ),
            "browser_thumbnail_display_mode": str(
                self.browser_thumbnail_display_mode_combo.currentData()
            ),
            "browser_folder_fallback_background": (
                self._folder_fallback_custom_color
                if self.browser_folder_fallback_background_combo.currentData()
                == "custom"
                else "auto"
            ),
            "browser_wheel_scroll_mode": str(
                self.browser_wheel_scroll_mode_combo.currentData() or "system"
            ),
            "browser_wheel_scroll_custom_rows": (
                self.browser_wheel_scroll_custom_spin.value()
            ),
            "thumbnail_quality_mode": str(
                self.thumbnail_quality_mode_combo.currentData()
            ),
            "thumbnail_cache_max_edge": self.thumbnail_cache_max_edge_spin.value(),
            "browser_display_density": str(
                self.browser_display_density_combo.currentData()
            ),
            "browser_sort_key": str(self.browser_sort_key_combo.currentData()),
            "browser_sort_order": str(
                self.browser_sort_order_combo.currentData()
            ),
            "browser_folders_first": (
                self.browser_folders_first_checkbox.isChecked()
            ),
            "browser_location_history_limit": int(
                self.browser_location_history_limit_spin.value()
            ),
            "browser_search_history_limit": int(
                self.browser_search_history_limit_spin.value()
            ),
            "browser_item_spacing_mode": (
                "preset"
                if self.browser_spacing_preset_checkbox.isChecked()
                else "custom"
            ),
            "browser_item_spacing": self.browser_item_spacing_spin.value(),
            "browser_cell_padding": self.browser_cell_padding_spin.value(),
            "browser_filename_display": str(
                self.browser_filename_display_combo.currentData()
            ),
            "browser_filename_gap": self.browser_filename_gap_spin.value(),
            "browser_filename_padding_y": (
                self.browser_filename_padding_y_spin.value()
            ),
            "browser_show_hidden_items": (
                self.browser_show_hidden_checkbox.isChecked()
            ),
            "browser_show_unsupported_files": (
                self.browser_show_unsupported_checkbox.isChecked()
            ),
            "browser_show_system_items": (
                self.browser_show_system_checkbox.isChecked()
            ),
            "browser_sidebar_layout": str(
                self.browser_sidebar_layout_combo.currentData()
            ),
            "folder_tree_sync_mode": str(
                self.folder_tree_sync_mode_combo.currentData()
            ),
            "folder_tree_collapse_unrelated": (
                self.folder_tree_collapse_checkbox.isChecked()
            ),
            "folder_tree_focus_rebase": (
                self.folder_tree_focus_rebase_checkbox.isChecked()
            ),
            "folder_tree_context_ancestor_levels": (
                self.folder_tree_ancestor_levels_spin.value()
            ),
            "favorite_row_padding_y": self.favorite_row_padding_spin.value(),
            "favorite_row_spacing": self.favorite_row_spacing_spin.value(),
            "favorite_icon_size": self.favorite_icon_size_spin.value(),
            "thumbnail_disk_cache_enabled": self.disk_cache_checkbox.isChecked(),
            "thumbnail_cache_limit_mb": self.cache_limit_spin.value(),
            "thumbnail_cache_max_unused_days": (
                self.cache_unused_days_spin.value()
                if int(self.cache_unused_days_combo.currentData()) == -1
                else int(self.cache_unused_days_combo.currentData())
            ),
            "text_preview_enabled": self.text_preview_checkbox.isChecked(),
            "video_thumbnail_enabled": self.video_thumbnail_checkbox.isChecked(),
            "video_thumbnail_backend": str(
                self.video_thumbnail_backend_combo.currentData() or "auto"
            ),
            "video_thumbnail_frame_mode": str(
                self.video_thumbnail_frame_mode_combo.currentData() or "smart"
            ),
            "video_thumbnail_shell_placeholder": (
                self.video_thumbnail_shell_placeholder_checkbox.isChecked()
            ),
            "ffmpeg_executable": self.ffmpeg_path_edit.text().strip().strip('"'),
            "browser_external_drop_behavior": str(
                self.browser_external_drop_combo.currentData() or "focus_only"
            ),
            "archive_backend_preference": str(
                self.archive_backend_combo.currentData() or "auto"
            ),
            "winrar_executable": self.winrar_path_edit.text().strip().strip('"'),
            "seven_zip_executable": self.seven_zip_path_edit.text().strip().strip('"'),
            "mouse_gestures_enabled": self.mouse_gestures_checkbox.isChecked(),
            "browser_folder_gestures_enabled": (
                self.browser_folder_gestures_checkbox.isChecked()
            ),
            "mouse_gesture_show_trail": self.mouse_gesture_trail_checkbox.isChecked(),
            "mouse_gesture_min_distance": self.mouse_gesture_distance_spin.value(),
            "mouse_gesture_bindings": bindings,
            "mouse_back_button_action": str(
                self.mouse_back_action_combo.currentData() or ""
            ),
            "mouse_forward_button_action": str(
                self.mouse_forward_action_combo.currentData() or ""
            ),
        }

    def apply_settings(self) -> dict[str, object]:
        values = self.values()
        requested_winrar = str(values.pop("winrar_executable", "") or "")
        requested_path = str(values.pop("seven_zip_executable", "") or "")
        changed = self.config.apply(values, save=True)
        self._report_save_error()
        current_winrar = str(self.config.get("winrar_executable", "") or "")
        if requested_winrar == current_winrar:
            self.redetect_winrar()
        elif not requested_winrar:
            archive_changed = self.config.apply(
                {"winrar_executable": ""},
                save=True,
            )
            changed.update(archive_changed)
            self.redetect_winrar()
        else:
            self._start_winrar_probe(
                requested_winrar,
                apply_on_success=True,
                accept_after=False,
            )
        current_path = str(self.config.get("seven_zip_executable", "") or "")
        if requested_path == current_path:
            self.redetect_seven_zip()
        elif not requested_path:
            archive_changed = self.config.apply(
                {"seven_zip_executable": ""},
                save=True,
            )
            changed.update(archive_changed)
            self.redetect_seven_zip()
        else:
            self._start_seven_zip_probe(
                requested_path,
                apply_on_success=True,
                accept_after=False,
            )
        self.load_current_values()
        if requested_winrar and requested_winrar != current_winrar:
            self.winrar_path_edit.setText(requested_winrar)
        if requested_path and requested_path != current_path:
            self.seven_zip_path_edit.setText(requested_path)
        self.settings_applied.emit(changed)
        return changed

    def accept(self) -> None:  # type: ignore[override]
        requested_winrar = self.winrar_path_edit.text().strip().strip('"')
        current_winrar = str(self.config.get("winrar_executable", "") or "")
        if requested_winrar and requested_winrar != current_winrar:
            values = self.values()
            values.pop("winrar_executable", None)
            values.pop("seven_zip_executable", None)
            changed = self.config.apply(values, save=True)
            if changed:
                self.settings_applied.emit(changed)
            if self._report_save_error():
                return
            self._start_winrar_probe(
                requested_winrar,
                apply_on_success=True,
                accept_after=True,
            )
            return
        requested_path = self.seven_zip_path_edit.text().strip().strip('"')
        current_path = str(self.config.get("seven_zip_executable", "") or "")
        if requested_path and requested_path != current_path:
            values = self.values()
            values.pop("winrar_executable", None)
            values.pop("seven_zip_executable", None)
            changed = self.config.apply(values, save=True)
            if changed:
                self.settings_applied.emit(changed)
            if self._report_save_error():
                return
            self._start_seven_zip_probe(
                requested_path,
                apply_on_success=True,
                accept_after=True,
            )
            return
        self.apply_settings()
        if self.config.last_error:
            return
        self._complete_accept()

    def reject(self) -> None:  # type: ignore[override]
        self._close_probe_workers()
        super().reject()
        self._delete_when_probes_finish()

    def _complete_accept(self) -> None:
        self._close_probe_workers()
        super().accept()
        self._delete_when_probes_finish()

    def _close_probe_workers(self) -> None:
        if self._probes_closed:
            return
        self._probes_closed = True
        self._probe_generation += 1
        self._winrar_probe_generation += 1
        self._ffmpeg_probe_generation += 1
        self._pending_explicit_path = None
        self._accept_after_probe = False
        self._pending_winrar_path = None
        self._accept_after_winrar_probe = False
        for workers in (
            self._probe_workers,
            self._winrar_probe_workers,
            self._ffmpeg_probe_workers,
        ):
            for generation, worker in tuple(workers.items()):
                try:
                    removed = self._probe_pool.tryTake(worker)
                except RuntimeError:
                    removed = False
                if removed:
                    workers.pop(generation, None)

    def _probe_workers_idle(self) -> bool:
        return not (
            self._probe_workers
            or self._winrar_probe_workers
            or self._ffmpeg_probe_workers
        )

    def _delete_when_probes_finish(self) -> None:
        if not self._probes_closed:
            return
        if self._probe_workers_idle():
            _RETIRED_SETTINGS_DIALOGS.discard(self)
            if not self._delete_scheduled:
                self._delete_scheduled = True
                self.deleteLater()
            return
        self.setParent(None)
        _RETIRED_SETTINGS_DIALOGS.add(self)

    def _prepare_application_shutdown(self, *, wait_msecs: int) -> bool:
        if not self._probes_closed:
            self.reject()
        try:
            pool_done = self._probe_pool.waitForDone(max(0, int(wait_msecs)))
        except RuntimeError:
            pool_done = self._probe_workers_idle()
        if not pool_done:
            return False
        for workers in (
            self._probe_workers,
            self._winrar_probe_workers,
            self._ffmpeg_probe_workers,
        ):
            workers.clear()
        self._delete_when_probes_finish()
        return self._probe_workers_idle()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._close_probe_workers()
        super().closeEvent(event)
        self._delete_when_probes_finish()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            maximum_height = max(420, int(available.height() * 0.88))
            maximum_width = max(520, int(available.width() * 0.9))
            self.setMaximumHeight(maximum_height)
            self.resize(
                min(maximum_width, max(520, self.width())),
                min(maximum_height, max(480, self.height())),
            )
        if not self._initial_probe_started:
            self._initial_probe_started = True
            self.redetect_winrar()
            self.redetect_seven_zip()
            self.redetect_ffmpeg()

    def browse_ffmpeg(self) -> None:
        if self._probes_closed:
            return
        start = self.ffmpeg_path_edit.text().strip()
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "FFmpeg実行ファイルを選択",
            start,
            "FFmpeg executable (ffmpeg.exe);;実行ファイル (*.exe);;すべてのファイル (*.*)",
        )
        if path:
            self.ffmpeg_path_edit.setText(path)
            self.redetect_ffmpeg()

    def redetect_ffmpeg(self) -> None:
        if self._probes_closed:
            return
        explicit = self.ffmpeg_path_edit.text().strip().strip('"')
        self._ffmpeg_probe_generation += 1
        generation = self._ffmpeg_probe_generation
        self.ffmpeg_status_label.setText("FFmpeg：確認中…")
        self.ffmpeg_redetect_button.setEnabled(False)
        worker = _FFmpegProbeWorker(
            generation,
            self._ffmpeg_locator,
            explicit,
        )
        worker.signals.completed.connect(self._on_ffmpeg_probe_completed)
        self._ffmpeg_probe_workers[generation] = worker
        self._probe_pool.start(worker)

    @Slot(int, object)
    def _on_ffmpeg_probe_completed(
        self,
        generation: int,
        executable: Path | None,
    ) -> None:
        self._ffmpeg_probe_workers.pop(generation, None)
        self._delete_when_probes_finish()
        if self._probes_closed or generation != self._ffmpeg_probe_generation:
            return
        self.ffmpeg_redetect_button.setEnabled(True)
        if executable is None:
            self.ffmpeg_status_label.setText(
                "FFmpeg：見つかりません（動画はWindows Shellを使用します）"
            )
        else:
            self.ffmpeg_status_label.setText(f"FFmpeg：検出済み\n{executable}")

    def browse_winrar(self) -> None:
        if self._probes_closed:
            return
        start = self.winrar_path_edit.text().strip()
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "WinRAR実行ファイルを選択",
            start,
            "WinRAR executable (WinRAR.exe UnRAR.exe Rar.exe);;実行ファイル (*.exe);;すべてのファイル (*.*)",
        )
        if path:
            self.winrar_path_edit.setText(path)
            self._start_winrar_probe(path)

    def use_automatic_winrar(self) -> None:
        self.winrar_path_edit.clear()
        self.redetect_winrar()

    def redetect_winrar(self) -> None:
        self._start_winrar_probe(
            self.winrar_path_edit.text().strip().strip('"')
        )

    def _start_winrar_probe(
        self,
        path: str,
        *,
        apply_on_success: bool = False,
        accept_after: bool = False,
    ) -> None:
        if self._probes_closed:
            return
        self._winrar_probe_generation += 1
        generation = self._winrar_probe_generation
        self._pending_winrar_path = path if apply_on_success else None
        self._accept_after_winrar_probe = bool(accept_after)
        self.winrar_status_label.setText("WinRAR：確認中…")
        self.winrar_redetect_button.setEnabled(False)
        worker = _WinRARProbeWorker(
            generation,
            self._winrar_locator,
            path,
        )
        worker.signals.completed.connect(self._on_winrar_probe_completed)
        self._winrar_probe_workers[generation] = worker
        self._probe_pool.start(worker)

    @Slot(int, object)
    def _on_winrar_probe_completed(
        self,
        generation: int,
        info: WinRARInfo,
    ) -> None:
        self._winrar_probe_workers.pop(generation, None)
        self._delete_when_probes_finish()
        if self._probes_closed or generation != self._winrar_probe_generation:
            return
        self.winrar_redetect_button.setEnabled(True)
        if info.available:
            version = f"\n{info.version_text}" if info.version_text else ""
            source = (
                "\nWindows関連付けから検出"
                if getattr(info, "discovery_source", None) == "association"
                else ""
            )
            self.winrar_status_label.setText(
                f"WinRAR：検出済み{source}\n{info.executable_path}{version}"
            )
        else:
            detail = f"\n{info.error_message}" if info.error_message else ""
            self.winrar_status_label.setText(f"WinRAR：見つかりません{detail}")

        pending = self._pending_winrar_path
        close_after = self._accept_after_winrar_probe
        self._pending_winrar_path = None
        self._accept_after_winrar_probe = False
        if pending is not None:
            if not info.available:
                return
            installation_path = str(
                getattr(info, "installation_executable_path", None) or pending
            )
            changed = self.config.apply(
                {"winrar_executable": installation_path},
                save=True,
            )
            self.winrar_path_edit.setText(installation_path)
            if changed:
                self.settings_applied.emit(changed)
        if close_after and (pending is None or info.available):
            self.accept()

    def browse_seven_zip(self) -> None:
        if self._probes_closed:
            return
        start = self.seven_zip_path_edit.text().strip()
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "7-Zip実行ファイルを選択",
            start,
            "7-Zip executable (7z.exe 7zz.exe);;実行ファイル (*.exe);;すべてのファイル (*.*)",
        )
        if path:
            self.seven_zip_path_edit.setText(path)
            self._start_seven_zip_probe(path)

    def use_automatic_seven_zip(self) -> None:
        self.seven_zip_path_edit.clear()
        self.redetect_seven_zip()

    def redetect_seven_zip(self) -> None:
        self._start_seven_zip_probe(
            self.seven_zip_path_edit.text().strip().strip('"')
        )

    def _start_seven_zip_probe(
        self,
        path: str,
        *,
        apply_on_success: bool = False,
        accept_after: bool = False,
    ) -> None:
        if self._probes_closed:
            return
        self._probe_generation += 1
        generation = self._probe_generation
        self._pending_explicit_path = path if apply_on_success else None
        self._accept_after_probe = bool(accept_after)
        self.seven_zip_status_label.setText("7-Zip：確認中…")
        self.seven_zip_redetect_button.setEnabled(False)
        worker = _SevenZipProbeWorker(
            generation,
            self._seven_zip_locator,
            path,
        )
        worker.signals.completed.connect(self._on_seven_zip_probe_completed)
        self._probe_workers[generation] = worker
        self._probe_pool.start(worker)

    @Slot(int, object)
    def _on_seven_zip_probe_completed(
        self,
        generation: int,
        info: SevenZipInfo,
    ) -> None:
        self._probe_workers.pop(generation, None)
        self._delete_when_probes_finish()
        if self._probes_closed or generation != self._probe_generation:
            return
        self.seven_zip_redetect_button.setEnabled(True)
        if info.available:
            version = f"\n{info.version_text}" if info.version_text else ""
            self.seven_zip_status_label.setText(
                f"7-Zip：検出済み\n{info.executable_path}{version}"
            )
        else:
            detail = f"\n{info.error_message}" if info.error_message else ""
            self.seven_zip_status_label.setText(f"7-Zip：見つかりません{detail}")

        pending = self._pending_explicit_path
        close_after = self._accept_after_probe
        self._pending_explicit_path = None
        self._accept_after_probe = False
        if pending is not None:
            if not info.available:
                return
            changed = self.config.apply(
                {"seven_zip_executable": info.executable_path},
                save=True,
            )
            self.seven_zip_path_edit.setText(info.executable_path)
            if changed:
                self.settings_applied.emit(changed)
        if close_after and (pending is None or info.available):
            self._complete_accept()

    def request_cache_clear(self, *, confirm: bool = True) -> None:
        if confirm:
            answer = QMessageBox.question(
                self,
                "サムネイルキャッシュを削除",
                "保存済みのサムネイルキャッシュを削除しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.cache_clear_requested.emit()
        self.cache_usage_label.setText("削除処理を要求しました。")

    def refresh_cache_usage(self) -> None:
        if self._cache_statistics_getter is not None:
            try:
                stats = self._cache_statistics_getter()
            except Exception:
                stats = {}
            if stats:
                used = max(0, int(stats.get("usage_bytes", 0)))
                entries = max(0, int(stats.get("entry_count", 0)))
                growth = max(0, int(stats.get("session_growth_bytes", 0)))
                last_cleanup = str(stats.get("last_cleanup_display", "未実行"))
                self.cache_usage_label.setText(
                    f"{self._format_bytes(used)} / {entries}件"
                    f"（今回 +{self._format_bytes(growth)}）"
                )
                self.cache_usage_label.setToolTip(
                    "\n".join(
                        (
                            f"最後の整理: {last_cleanup}",
                            f"memory hit: {int(stats.get('memory_hit', 0))}",
                            f"disk hit: {int(stats.get('disk_hit', 0))}",
                            f"generated: {int(stats.get('generated', 0))}",
                            f"disk saved: {int(stats.get('disk_saved', 0))}",
                            f"prefetch skipped: {int(stats.get('prefetch_skipped', 0))}",
                        )
                    )
                )
                return
        try:
            used = max(0, int(self._cache_usage_getter())) if self._cache_usage_getter else 0
        except Exception:
            self.cache_usage_label.setText("取得できません")
            return
        self.cache_usage_label.setText(self._format_bytes(used))

    def _report_save_error(self) -> bool:
        error = self.config.last_error
        if not error:
            self._last_save_error_reported = None
            return False
        if error != self._last_save_error_reported:
            self._last_save_error_reported = error
            QMessageBox.warning(
                self,
                "設定を保存できません",
                f"設定ファイルへ保存できませんでした。\n{error}",
            )
        return True

    def _sync_gap_enabled(self, joined: bool) -> None:
        self.gap_spin.setEnabled(not joined)
        self.gap_note.setVisible(joined)

    def _sync_gesture_controls(self, enabled: bool) -> None:
        for widget in (
            self.mouse_gesture_trail_checkbox,
            self.mouse_gesture_distance_spin,
            self.gesture_down_combo,
            self.gesture_up_combo,
        ):
            widget.setEnabled(enabled)

    @staticmethod
    def _command_combo(parent: QWidget) -> QComboBox:
        combo = QComboBox(parent)
        for label, command in COMMAND_CHOICES:
            combo.addItem(label, command)
        return combo

    @staticmethod
    def _select_command(combo: QComboBox, command: object) -> None:
        index = combo.findData(command)
        combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _select_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _format_bytes(value: int) -> str:
        if value >= 1024 * 1024:
            return f"{value / (1024 * 1024):.1f} MB"
        if value >= 1024:
            return f"{value / 1024:.1f} KB"
        return f"{value} bytes"
