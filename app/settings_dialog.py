from __future__ import annotations

from .i18n import UI_LANGUAGE_CHOICES, active_ui_language, tr


from collections.abc import Callable
from copy import deepcopy
import logging
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QObject,
    QRunnable,
    QSize,
    QThreadPool,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QFontMetrics,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPalette,
)
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
    QKeySequenceEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .browser_sort import (
    BROWSER_DISPLAY_DENSITY_LABELS,
    BROWSER_SORT_CHOICES,
    browser_sort_choice_index,
    new_browser_random_seed,
    BrowserDisplayDensity,
    BrowserSortKey,
)
from .app_icon import install_window_icon
from .browser_item_delegate import (
    BROWSER_FILE_FALLBACK_DEFAULT_COLOR,
    BROWSER_FOLDER_FALLBACK_DEFAULT_COLOR,
    GRID_PRESET_THUMBNAIL_SIZES,
)
from .fallback_background_editor import FallbackBackgroundEditor
from .browser_wheel_scroll import (
    BROWSER_WHEEL_SCROLL_CUSTOM_MAX_ROWS,
    BROWSER_WHEEL_SCROLL_CUSTOM_MIN_ROWS,
)
from .browser_folder_snapshot_cache import (
    BROWSER_FOLDER_SNAPSHOT_CACHE_ENTRY_LIMITS,
    estimate_browser_folder_snapshot_cache_mib,
    normalize_browser_folder_snapshot_cache_max_entries,
)
from .browser_icon_size import (
    BROWSER_ICON_SIZE_CUSTOM_MAX_PERCENT,
    BROWSER_ICON_SIZE_CUSTOM_MIN_PERCENT,
    BROWSER_ICON_SIZE_PRESETS,
    ICON_SIZE_SETTING_SPECS,
    normalize_browser_icon_size_custom_percent,
)
from .config_manager import ConfigManager
from .pdfium_service import PdfiumService
from .ffmpeg_thumbnail_backend import FFmpegLocator
from .thumbnail_render import (
    BROWSER_THUMBNAIL_DISPLAY_MODES,
    CROP_MODES,
    FRAME_RATIOS,
)
from .seven_zip_locator import SevenZipInfo, SevenZipLocator
from .viewer_commands import COMMAND_CHOICES
from .shortcut_catalog import (
    SPECS_BY_SCOPE,
    SHORTCUT_SPECS,
    ShortcutSpec,
    canonical_key,
    default_shortcut_bindings,
    normalize_shortcut_bindings,
)
from .viewer_memory_policy import VIEWER_MEMORY_MODE_LABELS
from .viewer_render import (
    DOWNSCALE_ALGORITHM_LABELS,
    UPSCALE_ALGORITHM_LABELS,
)
from .winrar_locator import WinRARInfo, WinRARLocator
from .windows_file_registration import WindowsFileRegistrationService


_LOGGER = logging.getLogger(__name__)
_FOLDER_SNAPSHOT_CACHE_TOOLTIP_TEXT = (
    'ファイル数の多いフォルダの再表示を高速化します。\n'
    '前回の一覧をメモリから先に表示し、あとで変更を確認します。\n'
    '保存した一覧を使い、検索・タグ・レートの絞り込みと解除も高速化します。\n'
    'HDDや大量ファイルのフォルダで特に効果的です。'
)
_FOLDER_SNAPSHOT_CACHE_HELP_TEXT = (
    'この設定は、ファイル数の多いフォルダを戻る・進むなどで再表示するときの待ち時間を短くするためのものです。'
    'HDDや大量ファイルのフォルダで特に効果的です。\n\n'
    '保存した一覧と並び順を再利用して、検索・タグ・レートの絞り込みと解除も高速化します。'
    'タグ・レートの変更やファイルの移動・追加・削除は一覧へ反映します。'
    '一覧が保存上限を超える場合や更新中は、通常の処理を使います。\n\n'
    '画像サムネイルではなく、フォルダ一覧のメタデータだけを現在のセッション中メモリに一時保存します。'
    '戻る・進むなどでは前回の一覧を先に表示し、バックグラウンドで追加・削除・変更を確認します。'
    '上限に達すると古い一覧から解放します。無効にすると保存しません。場所の履歴件数とは別の設定です。'
)
_THUMBNAIL_WEBP_QUALITY_TOOLTIP_TEXT = (
    '保存サムネイルの圧縮品質です（1～100）。\n'
    '高いほど画質は上がりますが、ファイルサイズも大きくなります。'
)
_THUMBNAIL_WEBP_QUALITY_HELP_TEXT = (
    '保存サムネイルの圧縮品質は1～100（既定値60）です。\n'
    '高いほど画質は上がりますが、ファイルサイズも大きくなります。'
    'WebP非対応時はPNG（可逆圧縮）で保存します。\n'
    '新しく生成するサムネイルに適用され、既存キャッシュは順次更新されます。'
)
_THUMBNAIL_ALPHA_TOOLTIP_TEXT = (
    '保存時の透明度を保持するか選びます。\n'
    '既定はオフで、オンにするとアルファを保持します。'
)
_THUMBNAIL_ALPHA_HELP_TEXT = (
    '保存サムネイルの透明度保持は既定ではオフです。\n'
    'オフ: Browserの背景色で透明部分を埋め、透明度を破棄してRGBを指定品質の非可逆WebPで保存します。\n'
    'オン: 透明度（アルファ）をそのまま保持し、RGBは指定品質で非可逆圧縮します。\n'
    'WebP非対応時はPNG（可逆圧縮）を使用します。'
)
_THUMBNAIL_MAX_EDGE_TOOLTIP_TEXT = (
    'サムネイル生成時の長辺上限を設定します。\n'
    '表示サイズや高DPIに合わせた物理解像度を調整できます。'
)
_THUMBNAIL_MAX_EDGE_HELP_TEXT = (
    '論理表示サイズと画面DPIから物理解像度を選び、複数のbucketを再利用します。\n'
    '設定変更後も互換キャッシュは再利用されます。\n'
    '完全に作り直す場合だけ「キャッシュを削除」を使用してください。'
)


class _SevenZipProbeSignals(QObject):
    completed = Signal(int, object)


class _CircularHelpButton(QToolButton):
    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(20, 20)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(20, 20)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        circle = self.rect().adjusted(1, 1, -1, -1)
        palette = self.palette()
        if self.isDown():
            painter.setBrush(palette.brush(QPalette.ColorRole.Highlight))
            painter.setPen(palette.color(QPalette.ColorRole.Highlight))
            text_color = palette.color(QPalette.ColorRole.HighlightedText)
        elif self.underMouse():
            painter.setBrush(palette.brush(QPalette.ColorRole.AlternateBase))
            painter.setPen(palette.color(QPalette.ColorRole.Mid))
            text_color = palette.color(QPalette.ColorRole.Text)
        else:
            painter.setBrush(palette.brush(QPalette.ColorRole.Base))
            painter.setPen(palette.color(QPalette.ColorRole.Mid))
            text_color = palette.color(QPalette.ColorRole.Text)
        painter.drawEllipse(circle)
        font = self.font()
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, '?')


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


TAB_SETTING_KEYS: dict[str, tuple[str, ...]] = {
    "shortcuts_browser": ("shortcut_bindings", "browser_cancel_clears_filters"),
    "shortcuts_viewer": ("shortcut_bindings", "viewer_slideshow_chord_enabled"),
    "viewer": (
        "open_viewer_behavior", "bring_viewer_to_front_on_open", "loop_book_navigation",
        "join_spread_pages", "gap", "single_first_page", "treat_wide_image_as_single",
        "book_open_position", "viewer_canvas_click_direction", "viewer_canvas_left_click_action",
        "viewer_slider_wheel_single_page_enabled", "magnifier_allow_outside_image",
        "viewer_prefetch_preset", "viewer_prefetch_direction_priority_enabled",
        "viewer_memory_mode", "viewer_downscale_algorithm", "viewer_upscale_algorithm",
        "magnifier_downscale_algorithm", "magnifier_upscale_algorithm",
        "viewer_prefetch_image_forward_units", "viewer_prefetch_image_backward_units",
        "viewer_prefetch_pdf_forward_units", "viewer_prefetch_pdf_backward_units",
        "hide_ui_in_fullscreen", "hide_cursor_in_fullscreen", "fullscreen_auto_reveal_ui",
        "fullscreen_top_edge_trigger_px", "fullscreen_bottom_edge_trigger_px",
        "fullscreen_ui_hide_delay_ms",
    ),
    "browser": (
        "thumbnail_size", "thumbnail_frame_ratio", "thumbnail_crop_mode",
        "browser_thumbnail_display_mode", "browser_folder_fallback_background", "browser_file_fallback_background",
        "browser_display_density", "browser_item_spacing_x", "browser_item_spacing_y", "browser_cell_padding",
        "browser_sort_key", "browser_sort_order", "browser_random_seed", "browser_folders_first",
        "browser_location_history_limit", "browser_search_history_limit", "browser_tag_grouped",
        "browser_filename_display", "browser_filename_elide_mode", "browser_filename_font_size",
        "browser_filename_show_extension", "browser_filename_gap", "browser_filename_padding_y",
        "browser_show_hidden_items", "browser_show_unsupported_files", "browser_show_system_items",
        "browser_folder_snapshot_cache_enabled", "browser_folder_snapshot_cache_max_entries",
        "browser_sidebar_layout", "folder_tree_sync_mode", "folder_tree_collapse_unrelated",
        "folder_tree_focus_rebase", "folder_tree_context_ancestor_levels", "favorite_row_padding_y",
        "favorite_row_spacing", "favorite_icon_size", "browser_preserve_search_for_viewer_roundtrip",
        "browser_center_folder_icon_size", "browser_center_folder_icon_custom_percent",
        "browser_center_file_icon_size", "browser_center_file_icon_custom_percent",
        "browser_badge_folder_icon_size", "browser_badge_folder_icon_custom_percent",
        "browser_badge_file_icon_size", "browser_badge_file_icon_custom_percent",
        "thumbnail_disk_cache_enabled", "thumbnail_cache_limit_mb", "thumbnail_cache_max_unused_days",
        "thumbnail_quality_mode", "thumbnail_webp_quality", "thumbnail_preserve_alpha",
        "thumbnail_cache_max_edge", "text_preview_enabled", "video_thumbnail_enabled",
        "video_thumbnail_backend", "video_thumbnail_frame_mode", "video_thumbnail_shell_placeholder",
        "ffmpeg_executable", "browser_external_drop_behavior",
    ),
    "file": (
        "file_operation_delete_confirm_focus_yes", "file_operation_delete_skip_confirmation",
    ),
    "archive": ("archive_backend_preference", "winrar_executable", "seven_zip_executable"),
    "mouse": (
        "mouse_gestures_enabled", "mouse_gesture_show_trail", "mouse_gesture_min_distance",
        "mouse_gesture_bindings", "browser_folder_gestures_enabled", "browser_wheel_scroll_mode",
        "browser_wheel_scroll_custom_rows", "mouse_back_button_action", "mouse_forward_button_action",
        "mouse_side_buttons_folder_navigation",
    ),
}


class _SingleShortcutEdit(QKeySequenceEdit):
    """Capture one simultaneous combination and reject multi-step values."""

    _MODIFIER_KEYS = frozenset(
        {
            Qt.Key.Key_Shift,
            Qt.Key.Key_Control,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        }
    )

    modifier_only_rejected = Signal()
    shortcut_input_started = Signal()
    shortcut_focus_left = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        self.rejected_multi_step = False
        self._modifier_gesture_started = False
        self._modifier_gesture_has_normal = False
        self._pressed_modifiers: set[Qt.Key] = set()
        super().__init__(parent)

    def setKeySequence(self, sequence: QKeySequence) -> None:  # noqa: N802
        candidate = QKeySequence(sequence)
        if candidate.count() > 1:
            self.rejected_multi_step = True
            self.keySequenceChanged.emit(self.keySequence())
            return
        self.rejected_multi_step = False
        QKeySequenceEdit.setKeySequence(self, candidate)

    @classmethod
    def _sequence_text(
        cls,
        event: QKeyEvent,
        held_modifiers: set[Qt.Key] | None = None,
    ) -> str:
        if event.key() in cls._MODIFIER_KEYS:
            return ""
        modifiers = int(event.modifiers().value)
        for key in held_modifiers or ():
            modifiers |= int(
                {
                    Qt.Key.Key_Control: Qt.KeyboardModifier.ControlModifier,
                    Qt.Key.Key_Shift: Qt.KeyboardModifier.ShiftModifier,
                    Qt.Key.Key_Alt: Qt.KeyboardModifier.AltModifier,
                    Qt.Key.Key_Meta: Qt.KeyboardModifier.MetaModifier,
                }[key].value
            )
        combined = int(event.key()) | modifiers
        return canonical_key(QKeySequence(combined))

    def event(self, event: QEvent) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.ShortcutOverride and isinstance(event, QKeyEvent):
            if event.key() in self._MODIFIER_KEYS or self._sequence_text(event):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        self.shortcut_input_started.emit()
        if event.key() in self._MODIFIER_KEYS:
            if not self._modifier_gesture_started:
                self._modifier_gesture_started = True
                self._modifier_gesture_has_normal = False
                self._pressed_modifiers.clear()
            self._pressed_modifiers.add(event.key())
            event.accept()
            return
        if self._modifier_gesture_started:
            self._modifier_gesture_has_normal = True
            sequence = self._sequence_text(event, self._pressed_modifiers)
            if sequence:
                QKeySequenceEdit.setKeySequence(self, QKeySequence(sequence))
                event.accept()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in self._MODIFIER_KEYS:
            self._pressed_modifiers.discard(event.key())
            if self._modifier_gesture_started and not self._pressed_modifiers:
                if not self._modifier_gesture_has_normal:
                    self.modifier_only_rejected.emit()
                self._modifier_gesture_started = False
                self._modifier_gesture_has_normal = False
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event: QEvent) -> None:  # type: ignore[override]
        self._pressed_modifiers.clear()
        self._modifier_gesture_started = False
        self._modifier_gesture_has_normal = False
        self.shortcut_focus_left.emit()
        super().focusOutEvent(event)


class _ShortcutSearchEdit(QLineEdit):
    """Capture one portable key combination as the shortcut search query."""

    _MODIFIER_KEYS = frozenset(
        {
            Qt.Key.Key_Shift,
            Qt.Key.Key_Control,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        }
    )

    _MODIFIER_LABELS = {
        Qt.Key.Key_Control: "Ctrl",
        Qt.Key.Key_Shift: "Shift",
        Qt.Key.Key_Alt: "Alt",
        Qt.Key.Key_Meta: "Meta",
    }
    _MODIFIER_ORDER = (
        Qt.Key.Key_Control,
        Qt.Key.Key_Shift,
        Qt.Key.Key_Alt,
        Qt.Key.Key_Meta,
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        self._modifier_gesture_started = False
        self._modifier_gesture_has_normal = False
        self._pressed_modifiers: set[Qt.Key] = set()
        self._modifier_gesture_keys: set[Qt.Key] = set()
        super().__init__(parent)

    @classmethod
    def _sequence_text(
        cls,
        event: QKeyEvent,
        held_modifiers: set[Qt.Key] | None = None,
    ) -> str:
        if event.key() in cls._MODIFIER_KEYS:
            return ""
        modifiers = int(event.modifiers().value)
        for key in held_modifiers or ():
            modifiers |= int(
                {
                    Qt.Key.Key_Control: Qt.KeyboardModifier.ControlModifier,
                    Qt.Key.Key_Shift: Qt.KeyboardModifier.ShiftModifier,
                    Qt.Key.Key_Alt: Qt.KeyboardModifier.AltModifier,
                    Qt.Key.Key_Meta: Qt.KeyboardModifier.MetaModifier,
                }[key].value
            )
        combined = int(event.key()) | modifiers
        sequence = QKeySequence(combined)
        return canonical_key(sequence)

    def event(self, event: QEvent) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.ShortcutOverride and isinstance(event, QKeyEvent):
            # Claim the candidate so the parent SettingsDialog does not run a
            # dialog shortcut before the search field sees the KeyPress.
            if event.key() in self._MODIFIER_KEYS or self._sequence_text(event):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in self._MODIFIER_KEYS:
            if not self._modifier_gesture_started:
                self._modifier_gesture_started = True
                self._modifier_gesture_has_normal = False
                self._pressed_modifiers.clear()
                self._modifier_gesture_keys.clear()
            self._pressed_modifiers.add(event.key())
            self._modifier_gesture_keys.add(event.key())
            event.accept()
            return
        sequence = self._sequence_text(
            event,
            self._pressed_modifiers if self._modifier_gesture_started else None,
        )
        if sequence:
            if self._modifier_gesture_started:
                self._modifier_gesture_has_normal = True
            self.setText(sequence)
            self.selectAll()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in self._MODIFIER_KEYS:
            self._pressed_modifiers.discard(event.key())
            if self._modifier_gesture_started and not self._pressed_modifiers:
                if not self._modifier_gesture_has_normal:
                    query = "+".join(
                        self._MODIFIER_LABELS[key]
                        for key in self._MODIFIER_ORDER
                        if key in self._modifier_gesture_keys
                    )
                    self.setText(query)
                    self.selectAll()
                self._modifier_gesture_started = False
                self._modifier_gesture_has_normal = False
                self._modifier_gesture_keys.clear()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event: QEvent) -> None:  # type: ignore[override]
        self._pressed_modifiers.clear()
        self._modifier_gesture_started = False
        self._modifier_gesture_has_normal = False
        self._modifier_gesture_keys.clear()
        super().focusOutEvent(event)


class _ShortcutPage(QWidget):
    resized = Signal()

    def resizeEvent(self, event: QEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.resized.emit()


class SettingsDialog(QDialog):
    settings_applied = Signal(object)
    cache_clear_requested = Signal()

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
        pdfium_service: PdfiumService | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('設定'))
        install_window_icon(self)
        self.setModal(True)
        self.resize(620, 680)
        self.config = config_manager
        self._pdfium_service = pdfium_service
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
        self.tabs.addTab(self._scrollable_tab(self._build_file_tab()), tr('ファイル'))
        self.tabs.addTab(self._scrollable_tab(self._build_archive_tab()), tr('書庫'))
        self.tabs.addTab(self._scrollable_tab(self._build_mouse_tab()), "Mouse")
        self.tabs.addTab(
            self._scrollable_tab(self._build_windows_tab()),
            tr('Windows連携'),
        )
        self.tabs.addTab(
            self._build_shortcuts_tab(),
            tr('ショートカット'),
        )
        self.tabs.addTab(self._scrollable_tab(self._build_general_tab()), tr('一般'))

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
        if active_ui_language() == "en":
            # Longer translated labels may need a row above their controls.
            # Keep the existing Japanese layout and fonts unchanged.
            for form in self.findChildren(QFormLayout):
                form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

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

    def _build_shortcuts_tab(self) -> QWidget:
        tab = QWidget(self)
        tab.setObjectName("shortcuts_tab")
        layout = QVBoxLayout(tab)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        self.shortcut_search_edit = _ShortcutSearchEdit(tab)
        self.shortcut_search_edit.setObjectName("shortcut_search")
        self.shortcut_search_edit.setPlaceholderText(
            tr("キーの組み合わせで検索（1つのキーまたは同時押し）")
        )
        self.shortcut_search_edit.setToolTip(
            tr("キーの組み合わせを入力すると、そのキーを割り当てた機能を表示します。")
        )
        search_row.addWidget(self.shortcut_search_edit, 1)
        self.shortcut_search_clear_button = QToolButton(tab)
        self.shortcut_search_clear_button.setObjectName("shortcut_search_clear")
        self.shortcut_search_clear_button.setText("×")
        self.shortcut_search_clear_button.setAutoRaise(True)
        self.shortcut_search_clear_button.setToolTip(tr("検索をクリア"))
        self.shortcut_search_clear_button.clicked.connect(
            self._clear_shortcut_search
        )
        search_row.addWidget(self.shortcut_search_clear_button)
        layout.addLayout(search_row)
        self.shortcut_tabs = QTabWidget(tab)
        self.shortcut_scope_scrolls: dict[str, QScrollArea] = {}
        self.shortcut_editors: dict[tuple[str, str], list[QKeySequenceEdit]] = {}
        self._shortcut_extra_bindings: dict[tuple[str, str], list[str]] = {}
        self.shortcut_rows: dict[tuple[str, str], QWidget] = {}
        self.shortcut_labels: dict[tuple[str, str], QLabel] = {}
        self.shortcut_move_buttons: dict[tuple[str, str], QPushButton] = {}
        self.shortcut_reset_buttons: dict[tuple[str, str], QPushButton] = {}
        self._shortcut_pages: dict[str, _ShortcutPage] = {}
        self._shortcut_modifier_warning: tuple[str, str] | None = None
        self._shortcut_last_edited: tuple[str, str] | None = None
        self.shortcut_status = QLabel(tab)
        self.shortcut_status.setObjectName("shortcut_status")
        self.shortcut_status.setStyleSheet("color: #ff262a;")
        self.shortcut_status.setWordWrap(True)
        self.shortcut_status.hide()
        button_metrics = QFontMetrics(self.font())
        shortcut_button_group_width = max(
            96,
            button_metrics.horizontalAdvance(tr("既定に戻す")) + 20,
            button_metrics.horizontalAdvance(tr("競合を移動")) + 20,
        )
        for scope, title in (("browser", "Browser"), ("viewer", "Viewer")):
            page = _ShortcutPage(self.shortcut_tabs)
            page.resized.connect(self._position_shortcut_move_buttons)
            page.installEventFilter(self)
            self._shortcut_pages[scope] = page
            page_policy = page.sizePolicy()
            page_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            page.setSizePolicy(page_policy)
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(8, 8, 8, 8)
            for spec in SPECS_BY_SCOPE[scope]:
                row = QWidget(page)
                row.setObjectName(f"shortcut_row_{scope}_{spec.action_id}")
                row_layout = QFormLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setHorizontalSpacing(8)
                row_layout.setVerticalSpacing(4)
                row_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                label = QLabel(tr(spec.label), row)
                label.setObjectName(f"shortcut_label_{scope}_{spec.action_id}")
                label.setWordWrap(True)
                label.setSizePolicy(
                    QSizePolicy.Policy.Preferred,
                    QSizePolicy.Policy.Minimum,
                )
                if scope == "browser" and spec.action_id == "browser_cancel":
                    label.setToolTip(
                        tr("選択操作を先に解除し、次に絞り込み、最後にコピー／切り取り候補を解除します。")
                    )
                    row.setToolTip(label.toolTip())
                if scope == "browser" and spec.action_id == "browser_toggle_folder_bookmark":
                    label.setToolTip(
                        tr("右クリックした項目ではなく、現在開いているフォルダをお気に入りに登録／解除します。")
                    )
                    row.setToolTip(label.toolTip())
                label.setMinimumWidth(120)
                editor_block = QWidget(row)
                editor_block.setObjectName(
                    f"shortcut_editor_block_{scope}_{spec.action_id}"
                )
                editor_block.installEventFilter(self)
                editor_block.setMinimumHeight(38)
                is_browser_cancel = (
                    scope == "browser" and spec.action_id == "browser_cancel"
                )
                if is_browser_cancel:
                    editor_layout = QVBoxLayout(editor_block)
                    editor_layout.setContentsMargins(0, 0, 0, 0)
                    editor_layout.setSpacing(4)
                    self.browser_cancel_filter_option_row = QWidget(editor_block)
                    option_layout = QHBoxLayout(
                        self.browser_cancel_filter_option_row
                    )
                    option_layout.setContentsMargins(0, 0, 0, 0)
                    self.browser_cancel_clears_filters_checkbox = QCheckBox(
                        tr("検索・評価・タグ絞り込みを解除も含める"),
                        self.browser_cancel_filter_option_row,
                    )
                    self.browser_cancel_clears_filters_checkbox.setObjectName(
                        "browser_cancel_clears_filters"
                    )
                    self.browser_cancel_clears_filters_checkbox.setChecked(True)
                    self.browser_cancel_clears_filters_checkbox.toggled.connect(
                        self._sync_browser_cancel_filter_controls
                    )
                    option_layout.addWidget(
                        self.browser_cancel_clears_filters_checkbox
                    )
                    option_layout.addStretch(1)
                    editor_layout.addWidget(self.browser_cancel_filter_option_row)
                    editor_row = QWidget(editor_block)
                    editor_row_layout = QHBoxLayout(editor_row)
                    editor_row_layout.setContentsMargins(0, 0, 0, 0)
                    editor_row_layout.setSpacing(4)
                    editor_layout.addWidget(editor_row)
                    shortcut_controls_layout = editor_row_layout
                else:
                    editor_layout = QHBoxLayout(editor_block)
                    editor_layout.setContentsMargins(0, 0, 0, 0)
                    editor_layout.setSpacing(4)
                    shortcut_controls_layout = editor_layout
                editors: list[QKeySequenceEdit] = []
                editor_minimum_width = self._shortcut_editor_minimum_width()
                for index in range(3):
                    editor = _SingleShortcutEdit(row)
                    editor.setObjectName(
                        f"shortcut_edit_{scope}_{spec.action_id}_{index}"
                    )
                    editor.setMaximumSequenceLength(1)
                    editor.setMinimumWidth(editor_minimum_width)
                    editor.setSizePolicy(
                        QSizePolicy.Policy.Expanding,
                        QSizePolicy.Policy.Fixed,
                    )
                    editor.setToolTip(tr("空欄は未割り当て。1つのキーまたは同時押しのみ。"))
                    editor.keySequenceChanged.connect(
                        lambda _sequence, current_scope=scope, action_id=spec.action_id:
                        self._on_shortcut_editor_changed(current_scope, action_id)
                    )
                    editor.shortcut_input_started.connect(
                        lambda current_scope=scope, action_id=spec.action_id:
                        self._on_shortcut_editor_input_started(current_scope, action_id)
                    )
                    editor.modifier_only_rejected.connect(
                        lambda current_scope=scope, action_id=spec.action_id:
                        self._on_shortcut_modifier_only_rejected(current_scope, action_id)
                    )
                    editor.shortcut_focus_left.connect(
                        lambda current_scope=scope, action_id=spec.action_id:
                        self._on_shortcut_editor_focus_left(current_scope, action_id)
                    )
                    shortcut_controls_layout.addWidget(editor, 1)
                    editors.append(editor)
                button_group = QWidget(editor_block)
                button_layout = QVBoxLayout(button_group)
                button_layout.setContentsMargins(0, 0, 0, 0)
                button_layout.setSpacing(0)
                button_group.setFixedWidth(shortcut_button_group_width)
                reset_action = QPushButton(tr("既定に戻す"), button_group)
                reset_action.setObjectName(
                    f"shortcut_reset_{scope}_{spec.action_id}"
                )
                reset_action.clicked.connect(
                    lambda _checked=False, current_scope=scope, action_id=spec.action_id:
                    self._reset_shortcut_action(current_scope, action_id)
                )
                button_layout.addWidget(reset_action)
                move_button = QPushButton(tr("競合を移動"), button_group)
                move_button.setObjectName(
                    f"shortcut_move_{scope}_{spec.action_id}"
                )
                move_button.clicked.connect(
                    lambda _checked=False, current_scope=scope, action_id=spec.action_id:
                    self._move_shortcut_conflicts(current_scope, action_id)
                )
                move_button.setEnabled(False)
                move_button.setVisible(True)
                shortcut_controls_layout.addWidget(
                    button_group, 0, Qt.AlignmentFlag.AlignVCenter
                )
                move_button.setParent(page)
                move_button.raise_()
                row_layout.addRow(label, editor_block)
                row_layout.setAlignment(editor_block, Qt.AlignmentFlag.AlignVCenter)
                page_layout.addWidget(row)
                self.shortcut_editors[(scope, spec.action_id)] = editors
                self.shortcut_rows[(scope, spec.action_id)] = row
                self.shortcut_labels[(scope, spec.action_id)] = label
                self.shortcut_move_buttons[(scope, spec.action_id)] = move_button
                self.shortcut_reset_buttons[(scope, spec.action_id)] = reset_action
            # Move buttons sit in the following label band.  Reserve that
            # same band after the final row so the tab extras/footer remain
            # clear without increasing every shortcut row.
            page_layout.addSpacing(28)
            if scope == "viewer":
                self.viewer_slideshow_chord_checkbox = QCheckBox(
                    tr("数字キー＋Sでスライドショーを開始"), page
                )
                self.viewer_slideshow_chord_checkbox.setObjectName(
                    "viewer_slideshow_chord_enabled"
                )
                self.viewer_slideshow_chord_checkbox.toggled.connect(
                    self._sync_shortcut_status
                )
                page_layout.addWidget(self.viewer_slideshow_chord_checkbox)
                note = QLabel(
                    tr("有効時は未修飾の1〜9とSをこの機能が予約します。Ctrl付きは利用できます。"),
                    page,
                )
                note.setWordWrap(True)
                page_layout.addWidget(note)
            reset = QPushButton(tr("このタブのすべての設定を既定に戻す"), page)
            reset.setObjectName(f"reset_shortcuts_{scope}")
            reset.clicked.connect(
                lambda _checked=False, current_scope=scope:
                self._reset_tab_draft(f"shortcuts_{current_scope}")
            )
            page_layout.addWidget(reset)
            page_layout.addStretch(1)
            scope_scroll = QScrollArea(self.shortcut_tabs)
            scope_scroll.setObjectName(f"shortcut_scope_scroll_{scope}")
            scope_scroll.setWidgetResizable(True)
            scope_scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            scope_scroll.setWidget(page)
            self.shortcut_scope_scrolls[scope] = scope_scroll
            self.shortcut_tabs.addTab(scope_scroll, title)
        self._position_shortcut_move_buttons()
        layout.addWidget(self.shortcut_tabs, 1)
        layout.addWidget(self.shortcut_status)
        self.shortcut_search_edit.textChanged.connect(self._filter_shortcut_rows)
        return tab

    def showEvent(self, event: QEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._position_shortcut_move_buttons()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if isinstance(watched, _ShortcutPage) and event.type() == QEvent.Type.LayoutRequest:
            self._position_shortcut_move_buttons()
        elif (
            isinstance(watched, QWidget)
            and watched.objectName().startswith("shortcut_editor_block_")
            and event.type() == QEvent.Type.Resize
        ):
            self._position_shortcut_move_buttons()
        return super().eventFilter(watched, event)

    def _position_shortcut_move_buttons(self) -> None:
        for (scope, action_id), move_button in self.shortcut_move_buttons.items():
            page = self._shortcut_pages.get(scope)
            row = self.shortcut_rows.get((scope, action_id))
            reset_button = self.shortcut_reset_buttons.get((scope, action_id))
            if page is None or row is None or reset_button is None:
                continue
            if row.isHidden():
                move_button.hide()
                continue
            top_left = reset_button.mapTo(page, reset_button.rect().bottomLeft())
            move_button.setGeometry(
                top_left.x(),
                top_left.y(),
                reset_button.width(),
                reset_button.height(),
            )
            move_button.show()

    def _shortcut_editor_minimum_width(self) -> int:
        metrics = QFontMetrics(self.font())
        longest = max(
            metrics.horizontalAdvance("Ctrl+Shift+PageDown"),
            metrics.horizontalAdvance("Ctrl+Alt+Shift+W"),
        )
        # Keep the three columns usable in a compact settings window.  The
        # editor itself can scroll horizontally when a larger key name does
        # not fit; letting the minimum grow with a fallback font makes the
        # whole row wider than the visible shortcut page and clips the reset
        # column.
        return max(112, min(longest + 24, 136))

    def _on_shortcut_editor_changed(self, scope: str, action_id: str) -> None:
        self._sync_shortcut_status(scope, action_id)
        self._update_shortcut_editor_tooltips(scope, action_id)
        self._filter_shortcut_rows(self.shortcut_search_edit.text())

    def _sync_browser_cancel_filter_controls(self, *_args: object) -> None:
        enabled = not self.browser_cancel_clears_filters_checkbox.isChecked()
        key = ("browser", "browser_clear_filters")
        for editor in self.shortcut_editors.get(key, ()):
            editor.setEnabled(enabled)
        label = self.shortcut_labels.get(key)
        if label is not None:
            label.setEnabled(enabled)
        reset_button = self.shortcut_reset_buttons.get(key)
        if reset_button is not None:
            reset_button.setEnabled(enabled)
        move_button = self.shortcut_move_buttons.get(key)
        if move_button is not None:
            move_button.setEnabled(enabled and move_button.isEnabled())
        self._sync_shortcut_status()

    def _on_shortcut_editor_input_started(self, scope: str, action_id: str) -> None:
        if self._shortcut_modifier_warning is None:
            return
        self._shortcut_modifier_warning = None
        self._sync_shortcut_status(scope, action_id)

    def _on_shortcut_modifier_only_rejected(self, scope: str, action_id: str) -> None:
        self._shortcut_modifier_warning = (scope, action_id)
        self._sync_shortcut_status(scope, action_id)

    def _on_shortcut_editor_focus_left(self, scope: str, action_id: str) -> None:
        if self._shortcut_modifier_warning is None:
            return
        self._shortcut_modifier_warning = None
        self._sync_shortcut_status(scope, action_id)

    def _clear_shortcut_search(self) -> None:
        self.shortcut_search_edit.clear()
        self.shortcut_search_edit.setFocus(Qt.FocusReason.MouseFocusReason)

    def _update_shortcut_editor_tooltips(self, scope: str, action_id: str) -> None:
        editors = self.shortcut_editors[(scope, action_id)]
        keys = [
            canonical_key(editor.keySequence())
            for editor in editors
            if canonical_key(editor.keySequence())
        ]
        keys.extend(self._shortcut_extra_bindings.get((scope, action_id), ()))
        suffix = ", ".join(keys)
        tooltip = tr("空欄は未割り当て。1つのキーまたは同時押しのみ。")
        if suffix:
            tooltip = f"{tooltip}\n{suffix}"
        for editor in editors:
            editor.setToolTip(tooltip)

    def _make_tab_reset_button(self, scope: str, parent: QWidget) -> QPushButton:
        button = QPushButton(tr("このタブのすべての設定を既定に戻す"), parent)
        button.setObjectName(f"reset_{scope}_tab")
        button.clicked.connect(
            lambda _checked=False, current_scope=scope:
            self._reset_tab_draft(current_scope)
        )
        return button

    def _reset_tab_draft(self, scope: str) -> None:
        """Reset only the selected tab's draft controls.

        This intentionally writes widgets directly.  Replacing ``config.data``
        and reloading the whole dialog would discard unrelated invalid draft
        shortcuts and would also run cache/registration probes.
        """
        if scope == "shortcuts_browser":
            self._reset_shortcut_scope("browser")
        elif scope == "shortcuts_viewer":
            self._reset_shortcut_scope("viewer")
            self.viewer_slideshow_chord_checkbox.setChecked(
                bool(ConfigManager.DEFAULTS["viewer_slideshow_chord_enabled"])
            )
            self._sync_shortcut_status()
        elif scope == "viewer":
            self._reset_viewer_scope()
        elif scope == "browser":
            self._reset_browser_scope()
        elif scope == "file":
            self.delete_confirm_focus_yes_checkbox.setChecked(
                bool(ConfigManager.DEFAULTS["file_operation_delete_confirm_focus_yes"])
            )
            self.delete_skip_confirmation_checkbox.setChecked(
                bool(ConfigManager.DEFAULTS["file_operation_delete_skip_confirmation"])
            )
            self._sync_delete_confirmation_controls()
        elif scope == "archive":
            self._select_data(
                self.archive_backend_combo,
                ConfigManager.DEFAULTS["archive_backend_preference"],
            )
            self.winrar_path_edit.setText(str(ConfigManager.DEFAULTS["winrar_executable"] or ""))
            self.seven_zip_path_edit.setText(str(ConfigManager.DEFAULTS["seven_zip_executable"] or ""))
        elif scope == "mouse":
            self._reset_mouse_scope()

    def _reset_shortcut_scope(self, scope: str) -> None:
        defaults = ConfigManager.DEFAULTS["shortcut_bindings"].get(scope, {})
        for (editor_scope, action_id), editors in self.shortcut_editors.items():
            if editor_scope != scope:
                continue
            self._shortcut_extra_bindings.pop((editor_scope, action_id), None)
            values = defaults.get(action_id, ())
            for index, editor in enumerate(editors):
                editor.setKeySequence(
                    QKeySequence(values[index])
                    if index < len(values)
                    else QKeySequence()
                )
        if scope == "browser":
            self.browser_cancel_clears_filters_checkbox.setChecked(
                bool(ConfigManager.DEFAULTS["browser_cancel_clears_filters"])
            )
            self._sync_browser_cancel_filter_controls()
        self._sync_shortcut_status()

    def _reset_viewer_scope(self) -> None:
        defaults = ConfigManager.DEFAULTS
        self._select_data(self.open_behavior_combo, defaults["open_viewer_behavior"])
        self.bring_to_front_checkbox.setChecked(bool(defaults["bring_viewer_to_front_on_open"]))
        self.loop_navigation_checkbox.setChecked(bool(defaults["loop_book_navigation"]))
        self.join_spread_checkbox.setChecked(bool(defaults["join_spread_pages"]))
        self.gap_spin.setValue(int(defaults["gap"]))
        self.single_first_checkbox.setChecked(bool(defaults["single_first_page"]))
        self.wide_single_checkbox.setChecked(bool(defaults["treat_wide_image_as_single"]))
        self._select_data(self.book_open_position_combo, defaults["book_open_position"])
        self._select_data(self.viewer_canvas_click_direction_combo, defaults["viewer_canvas_click_direction"])
        self._select_data(self.viewer_canvas_left_click_combo, defaults["viewer_canvas_left_click_action"])
        self.viewer_slider_wheel_single_page_checkbox.setChecked(
            bool(defaults["viewer_slider_wheel_single_page_enabled"])
        )
        self.magnifier_allow_outside_image_checkbox.setChecked(
            bool(defaults["magnifier_allow_outside_image"])
        )
        self._custom_prefetch_values = {
            "image_forward_units": int(defaults["viewer_prefetch_image_forward_units"]),
            "image_backward_units": int(defaults["viewer_prefetch_image_backward_units"]),
            "pdf_forward_units": int(defaults["viewer_prefetch_pdf_forward_units"]),
            "pdf_backward_units": int(defaults["viewer_prefetch_pdf_backward_units"]),
        }
        self._select_data(self.prefetch_preset_combo, defaults["viewer_prefetch_preset"])
        self._select_data(self.viewer_memory_mode_combo, defaults["viewer_memory_mode"])
        self._select_data(self.viewer_downscale_algorithm_combo, defaults["viewer_downscale_algorithm"])
        self._select_data(self.viewer_upscale_algorithm_combo, defaults["viewer_upscale_algorithm"])
        self._select_data(self.magnifier_downscale_algorithm_combo, defaults["magnifier_downscale_algorithm"])
        self._select_data(self.magnifier_upscale_algorithm_combo, defaults["magnifier_upscale_algorithm"])
        self.prefetch_direction_priority_checkbox.setChecked(
            bool(defaults["viewer_prefetch_direction_priority_enabled"])
        )
        self._on_prefetch_preset_changed(self.prefetch_preset_combo.currentIndex())
        self.fullscreen_hide_ui_checkbox.setChecked(bool(defaults["hide_ui_in_fullscreen"]))
        self.fullscreen_hide_cursor_checkbox.setChecked(bool(defaults["hide_cursor_in_fullscreen"]))
        self.fullscreen_auto_reveal_checkbox.setChecked(bool(defaults["fullscreen_auto_reveal_ui"]))
        self.fullscreen_top_edge_trigger_spin.setValue(int(defaults["fullscreen_top_edge_trigger_px"]))
        self.fullscreen_bottom_edge_trigger_spin.setValue(int(defaults["fullscreen_bottom_edge_trigger_px"]))
        self.fullscreen_hide_delay_spin.setValue(int(defaults["fullscreen_ui_hide_delay_ms"]))
        self._sync_gap_enabled(self.join_spread_checkbox.isChecked())

    def _reset_browser_scope(self) -> None:
        defaults = ConfigManager.DEFAULTS
        self.thumbnail_size_spin.setValue(int(defaults["thumbnail_size"]))
        self._select_data(self.thumbnail_frame_ratio_combo, defaults["thumbnail_frame_ratio"])
        self._select_data(self.thumbnail_crop_mode_combo, defaults["thumbnail_crop_mode"])
        self._select_data(self.browser_thumbnail_display_mode_combo, defaults["browser_thumbnail_display_mode"])
        for key, editor in self._fallback_background_editors.items():
            editor.load_value(str(defaults[key]))
        for key, custom_key, _label in ICON_SIZE_SETTING_SPECS:
            self._select_data(self.browser_icon_size_combos[key], defaults[key])
            self.browser_icon_size_custom_spins[custom_key].setValue(int(defaults[custom_key]))
            self.browser_icon_size_custom_spins[custom_key].setEnabled(
                self.browser_icon_size_combos[key].currentData() == "custom"
            )
        self._select_data(self.thumbnail_quality_mode_combo, defaults["thumbnail_quality_mode"])
        self.thumbnail_webp_quality_spin.setValue(int(defaults["thumbnail_webp_quality"]))
        self.thumbnail_preserve_alpha_checkbox.setChecked(bool(defaults["thumbnail_preserve_alpha"]))
        self.thumbnail_cache_max_edge_spin.setValue(int(defaults["thumbnail_cache_max_edge"]))
        self._select_data(self.browser_display_density_combo, defaults["browser_display_density"])
        self._select_data(self.browser_filename_display_combo, defaults["browser_filename_display"])
        self.browser_filename_gap_spin.setValue(int(defaults["browser_filename_gap"]))
        self.browser_filename_padding_y_spin.setValue(int(defaults["browser_filename_padding_y"]))
        self.browser_tag_grouped_checkbox.setChecked(bool(defaults["browser_tag_grouped"]))
        self.browser_filename_extension_checkbox.setChecked(bool(defaults["browser_filename_show_extension"]))
        self._select_data(self.browser_filename_elide_combo, defaults["browser_filename_elide_mode"])
        self._select_data(self.browser_filename_font_size_combo, defaults["browser_filename_font_size"])
        self.browser_show_hidden_checkbox.setChecked(bool(defaults["browser_show_hidden_items"]))
        self.browser_show_unsupported_checkbox.setChecked(bool(defaults["browser_show_unsupported_files"]))
        self.browser_show_system_checkbox.setChecked(bool(defaults["browser_show_system_items"]))
        self.browser_folder_snapshot_cache_checkbox.setChecked(bool(defaults["browser_folder_snapshot_cache_enabled"]))
        self._select_data(
            self.browser_folder_snapshot_cache_max_entries_combo,
            defaults["browser_folder_snapshot_cache_max_entries"],
        )
        self._sync_browser_folder_snapshot_cache_controls()
        self._browser_random_seed = defaults["browser_random_seed"]
        self._browser_random_sort_order = defaults["browser_sort_order"]
        self.browser_sort_key_combo.setCurrentIndex(
            browser_sort_choice_index(defaults["browser_sort_key"], defaults["browser_sort_order"])
        )
        self.browser_folders_first_checkbox.setChecked(bool(defaults["browser_folders_first"]))
        self.browser_location_history_limit_spin.setValue(int(defaults["browser_location_history_limit"]))
        self.browser_search_history_limit_spin.setValue(int(defaults["browser_search_history_limit"]))
        self.browser_preserve_search_for_viewer_roundtrip_checkbox.setChecked(
            bool(defaults["browser_preserve_search_for_viewer_roundtrip"])
        )
        self.browser_item_spacing_x_spin.setValue(int(defaults["browser_item_spacing_x"]))
        self.browser_item_spacing_y_spin.setValue(int(defaults["browser_item_spacing_y"]))
        self.browser_cell_padding_spin.setValue(int(defaults["browser_cell_padding"]))
        self._select_data(self.browser_sidebar_layout_combo, defaults["browser_sidebar_layout"])
        self._select_data(self.folder_tree_sync_mode_combo, defaults["folder_tree_sync_mode"])
        self.folder_tree_collapse_checkbox.setChecked(bool(defaults["folder_tree_collapse_unrelated"]))
        self.folder_tree_focus_rebase_checkbox.setChecked(bool(defaults["folder_tree_focus_rebase"]))
        self.folder_tree_ancestor_levels_spin.setValue(int(defaults["folder_tree_context_ancestor_levels"]))
        self.favorite_row_padding_spin.setValue(int(defaults["favorite_row_padding_y"]))
        self.favorite_row_spacing_spin.setValue(int(defaults["favorite_row_spacing"]))
        self.favorite_icon_size_spin.setValue(int(defaults["favorite_icon_size"]))
        self.disk_cache_checkbox.setChecked(bool(defaults["thumbnail_disk_cache_enabled"]))
        self.cache_limit_spin.setValue(int(defaults["thumbnail_cache_limit_mb"]))
        unused_days = int(defaults["thumbnail_cache_max_unused_days"])
        index = self.cache_unused_days_combo.findData(unused_days)
        self.cache_unused_days_combo.setCurrentIndex(max(0, index))
        self.cache_unused_days_spin.setValue(max(7, unused_days or 90))
        self.cache_unused_days_spin.setEnabled(int(self.cache_unused_days_combo.currentData()) == -1)
        self.text_preview_checkbox.setChecked(bool(defaults["text_preview_enabled"]))
        self.video_thumbnail_checkbox.setChecked(bool(defaults["video_thumbnail_enabled"]))
        self._select_data(self.video_thumbnail_backend_combo, defaults["video_thumbnail_backend"])
        self._select_data(self.video_thumbnail_frame_mode_combo, defaults["video_thumbnail_frame_mode"])
        self.video_thumbnail_shell_placeholder_checkbox.setChecked(bool(defaults["video_thumbnail_shell_placeholder"]))
        self.ffmpeg_path_edit.setText(str(defaults["ffmpeg_executable"] or ""))
        self._select_data(self.browser_external_drop_combo, defaults["browser_external_drop_behavior"])
        self._sync_browser_filename_controls()

    def _reset_mouse_scope(self) -> None:
        defaults = ConfigManager.DEFAULTS
        self.mouse_gestures_checkbox.setChecked(bool(defaults["mouse_gestures_enabled"]))
        self.mouse_gesture_trail_checkbox.setChecked(bool(defaults["mouse_gesture_show_trail"]))
        self.mouse_gesture_distance_spin.setValue(int(defaults["mouse_gesture_min_distance"]))
        bindings = defaults["mouse_gesture_bindings"]
        self._gesture_bindings_base = deepcopy(bindings)
        self._select_command(self.gesture_down_combo, bindings.get("D", ""))
        self._select_command(self.gesture_up_combo, bindings.get("U", ""))
        self._select_command(self.gesture_left_combo, bindings.get("L", ""))
        self._select_command(self.gesture_right_combo, bindings.get("R", ""))
        self.browser_folder_gestures_checkbox.setChecked(bool(defaults["browser_folder_gestures_enabled"]))
        self._select_data(self.browser_wheel_scroll_mode_combo, defaults["browser_wheel_scroll_mode"])
        self.browser_wheel_scroll_custom_spin.setValue(int(defaults["browser_wheel_scroll_custom_rows"]))
        self._select_command(self.mouse_back_action_combo, defaults["mouse_back_button_action"])
        self._select_command(self.mouse_forward_action_combo, defaults["mouse_forward_button_action"])
        self.mouse_side_buttons_folder_navigation_checkbox.setChecked(
            bool(defaults["mouse_side_buttons_folder_navigation"])
        )
        self._sync_browser_wheel_scroll_controls()
        self._sync_gesture_controls(self.mouse_gestures_checkbox.isChecked())

    def _reset_shortcut_action(self, scope: str, action_id: str) -> None:
        spec = next(
            (
                candidate
                for candidate in SPECS_BY_SCOPE.get(scope, ())
                if candidate.action_id == action_id
            ),
            None,
        )
        editors = self.shortcut_editors.get((scope, action_id), ())
        if spec is None:
            return
        self._shortcut_extra_bindings.pop((scope, action_id), None)
        for index, editor in enumerate(editors):
            editor.setKeySequence(
                QKeySequence(spec.defaults[index])
                if index < len(spec.defaults)
                else QKeySequence()
            )
        self._sync_shortcut_status()

    def _shortcut_values_from_ui(self) -> dict[str, dict[str, list[str]]]:
        result = default_shortcut_bindings()
        for (scope, action_id), editors in self.shortcut_editors.items():
            values: list[str] = []
            for editor in editors:
                value = canonical_key(editor.keySequence())
                if value and value not in values:
                    values.append(value)
            for value in self._shortcut_extra_bindings.get((scope, action_id), ()):
                if value and value not in values:
                    values.append(value)
            result[scope][action_id] = values
        return normalize_shortcut_bindings(result)

    def _shortcut_conflicts(self) -> list[tuple[str, str, str, str]]:
        bindings = self._shortcut_values_from_ui()
        conflicts: list[tuple[str, str, str, str]] = []
        for scope, specs in SPECS_BY_SCOPE.items():
            owners: dict[str, str] = {}
            for spec in specs:
                if (
                    scope == "browser"
                    and spec.action_id == "browser_clear_filters"
                    and self.browser_cancel_clears_filters_checkbox.isChecked()
                ):
                    continue
                for sequence in bindings[scope].get(spec.action_id, []):
                    old = owners.get(sequence)
                    if old is not None and old != spec.action_id:
                        conflicts.append((scope, old, spec.action_id, sequence))
                    else:
                        owners[sequence] = spec.action_id
        if self.viewer_slideshow_chord_checkbox.isChecked():
            reserved = {str(index) for index in range(1, 10)} | {"S"}
            for action_id, values in bindings["viewer"].items():
                for sequence in values:
                    if action_id == "viewer_slideshow_toggle" and sequence == "S":
                        continue
                    if sequence in reserved:
                        conflicts.append(("viewer", "slideshow_chord", action_id, sequence))
        return conflicts

    @staticmethod
    def _shortcut_spec(scope: str, action_id: str) -> ShortcutSpec | None:
        return next(
            (
                spec
                for spec in SPECS_BY_SCOPE.get(scope, ())
                if spec.action_id == action_id
            ),
            None,
        )

    def _move_shortcut_conflicts(self, scope: str, action_id: str) -> None:
        target_values = set(
            self._shortcut_values_from_ui().get(scope, {}).get(action_id, [])
        )
        for (other_scope, other_id), editors in self.shortcut_editors.items():
            if other_scope != scope or other_id == action_id:
                continue
            for editor in editors:
                if canonical_key(editor.keySequence()) in target_values:
                    editor.clear()
        self._sync_shortcut_status()

    def _sync_shortcut_status(self, *args) -> bool:
        if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], str):
            self._shortcut_last_edited = (args[0], args[1])
        if any(
            getattr(editor, "rejected_multi_step", False)
            for editors in self.shortcut_editors.values()
            for editor in editors
        ):
            self.shortcut_status.setText(
                tr("ショートカットは1つのキーまたは同時押しで指定してください")
            )
            self.shortcut_status.show()
            return False
        if any(
            editor.keySequence().count() > 1
            for editors in self.shortcut_editors.values()
            for editor in editors
        ):
            self.shortcut_status.setText(
                tr("ショートカットは1つのキーまたは同時押しで指定してください")
            )
            self.shortcut_status.show()
            return False
        conflicts = self._shortcut_conflicts()
        for key, button in self.shortcut_move_buttons.items():
            button.setEnabled(False)
            row = self.shortcut_rows.get(key)
            button.setVisible(row is None or not row.isHidden())
        clear_filters_enabled = not self.browser_cancel_clears_filters_checkbox.isChecked()
        for editor in self.shortcut_editors.get(("browser", "browser_clear_filters"), ()):
            editor.setEnabled(clear_filters_enabled)
        clear_filters_label = self.shortcut_labels.get(
            ("browser", "browser_clear_filters")
        )
        if clear_filters_label is not None:
            clear_filters_label.setEnabled(clear_filters_enabled)
        reset_button = self.shortcut_reset_buttons.get(("browser", "browser_clear_filters"))
        if reset_button is not None:
            reset_button.setEnabled(clear_filters_enabled)
        self._position_shortcut_move_buttons()
        if conflicts:
            scope, old, new, sequence = conflicts[0]
            edited = self._shortcut_last_edited
            if old == "slideshow_chord":
                owner = self._shortcut_spec(scope, new)
                message = tr(
                    "数字キー＋Sのスライドショー機能が予約しているキーです。機能を無効にするか、割り当てを変更してください: {p0}（{p1}）",
                    p0=sequence,
                    p1=tr(owner.label) if owner is not None else new,
                )
            else:
                target = edited if edited in {
                    (scope, old), (scope, new)
                } else (scope, new)
                move_button = self.shortcut_move_buttons.get(target)
                if move_button is not None and (
                    target != ("browser", "browser_clear_filters")
                    or clear_filters_enabled
                ):
                    move_button.setEnabled(True)
                old_spec = self._shortcut_spec(scope, old)
                new_spec = self._shortcut_spec(scope, new)
                message = tr(
                    "同じショートカットが複数の機能に割り当てられています: {p0}（{p1} / {p2}）",
                    p0=sequence,
                    p1=tr(old_spec.label) if old_spec is not None else old,
                    p2=tr(new_spec.label) if new_spec is not None else new,
                )
            if self._shortcut_modifier_warning is not None:
                message = tr(
                    "Ctrl・Shift・Altだけでは登録できません。ほかのキーと組み合わせてください"
                )
            self.shortcut_status.setText(message)
            self.shortcut_status.show()
            return False
        if self._shortcut_modifier_warning is not None:
            self.shortcut_status.setText(
                tr(
                    "Ctrl・Shift・Altだけでは登録できません。ほかのキーと組み合わせてください"
                )
            )
            self.shortcut_status.show()
            return True
        extra_count = sum(
            len(values)
            for values in self._shortcut_extra_bindings.values()
        )
        if extra_count:
            self.shortcut_status.setText(
                tr(
                    "3欄を超える保存済みショートカットも保持します: {p0}件",
                    p0=extra_count,
                )
            )
            self.shortcut_status.show()
            return True
        self.shortcut_status.clear()
        self.shortcut_status.hide()
        return True

    def _filter_shortcut_rows(self, text: str) -> None:
        needle = str(text).strip()
        for (scope, action_id), row in self.shortcut_rows.items():
            spec = next(
                item for item in SHORTCUT_SPECS if item.scope == scope and item.action_id == action_id
            )
            if not needle:
                row.setVisible(True)
                continue
            translated_label = tr(spec.label).casefold()
            if needle.casefold() in translated_label:
                row.setVisible(True)
                continue
            keys = [
                canonical_key(editor.keySequence())
                for editor in self.shortcut_editors[(scope, action_id)]
            ]
            keys.extend(self._shortcut_extra_bindings.get((scope, action_id), ()))
            row.setVisible(self._shortcut_key_query_matches(needle, keys))
        cancel_row = self.shortcut_rows.get(("browser", "browser_cancel"))
        if cancel_row is not None:
            self.browser_cancel_filter_option_row.setVisible(
                not needle or not cancel_row.isHidden()
            )
        self._position_shortcut_move_buttons()

    @staticmethod
    def _shortcut_key_query_matches(query: str, keys: list[str]) -> bool:
        aliases = {
            "pagedown": "pgdown",
            "pageup": "pgup",
            "escape": "esc",
        }

        def normalize_token(token: str) -> str:
            lowered = token.strip().casefold()
            return aliases.get(lowered, lowered)

        normalized_query = query.strip().casefold()
        if not normalized_query:
            return True
        if "+" in query:
            normalized_sequence = canonical_key(query)
            query_tokens = tuple(
                normalize_token(token) for token in query.split("+") if token.strip()
            )
            modifier_tokens = {"ctrl", "shift", "alt", "meta", "win", "cmd"}
            if query_tokens and set(query_tokens).issubset(modifier_tokens):
                return any(
                    set(
                        normalize_token(token)
                        for token in key.split("+")
                        if token.strip()
                    ).issuperset(query_tokens)
                    for key in keys
                    if key
                )
            return any(
                (
                    normalized_sequence
                    and key.casefold() == normalized_sequence.casefold()
                )
                or tuple(normalize_token(token) for token in key.split("+") if token.strip())
                == query_tokens
                for key in keys
                if key
            )
        return any(
            normalize_token(normalized_query) in {
                normalize_token(token)
                for token in key.split("+")
                if token.strip()
            }
            for key in keys
            if key
        )

    def _load_shortcut_controls(self) -> None:
        bindings = normalize_shortcut_bindings(self.config.get("shortcut_bindings"))
        for (scope, action_id), editors in self.shortcut_editors.items():
            values = bindings.get(scope, {}).get(action_id, [])
            self._shortcut_extra_bindings[(scope, action_id)] = list(values[3:])
            for index, editor in enumerate(editors):
                editor.blockSignals(True)
                editor.setKeySequence(QKeySequence(values[index]) if index < len(values) else QKeySequence())
                editor.blockSignals(False)
            self._update_shortcut_editor_tooltips(scope, action_id)
        self.viewer_slideshow_chord_checkbox.blockSignals(True)
        self.viewer_slideshow_chord_checkbox.setChecked(
            bool(self.config.get("viewer_slideshow_chord_enabled", True))
        )
        self.viewer_slideshow_chord_checkbox.blockSignals(False)
        self.browser_cancel_clears_filters_checkbox.blockSignals(True)
        self.browser_cancel_clears_filters_checkbox.setChecked(
            bool(self.config.get("browser_cancel_clears_filters", True))
        )
        self.browser_cancel_clears_filters_checkbox.blockSignals(False)
        self._sync_browser_cancel_filter_controls()
        self.viewer_close_shortcut_edit = self.shortcut_editors[("viewer", "viewer_close")][0]
        self.viewer_close_shortcut_status = self.shortcut_status
        self._sync_shortcut_status()

    def _build_general_tab(self) -> QWidget:
        tab = QWidget(self)
        form = QFormLayout(tab)
        self.ui_language_combo = QComboBox(tab)
        self.ui_language_combo.setObjectName("ui_language")
        for label, language in UI_LANGUAGE_CHOICES:
            self.ui_language_combo.addItem(label, language)
        # Intentionally bilingual so this control is discoverable in either UI.
        language_label = "表示言語 / Language"
        if active_ui_language() == "zh-Hans":
            language_label = "显示语言 / Language"
        elif active_ui_language() == "zh-Hant":
            language_label = "顯示語言 / Language"
        self.ui_language_label = QLabel(language_label, tab)
        self.ui_language_label.setBuddy(self.ui_language_combo)
        form.addRow(self.ui_language_label, self.ui_language_combo)
        self.ui_language_restart_note = QLabel(
            tr('言語の変更はNivisViewerの再起動後に反映されます。'), tab,
        )
        self.ui_language_restart_note.setWordWrap(True)
        form.addRow(self.ui_language_restart_note)
        help_group = QGroupBox(tr('ヘルプ'), tab)
        help_layout = QVBoxLayout(help_group)
        self.shortcuts_help_button = QPushButton(tr('ショートカット一覧'), help_group)
        self.diagnostics_button = QPushButton(
            tr('NivisViewerについて／診断情報'), help_group,
        )
        self.shortcuts_help_button.clicked.connect(self.show_shortcuts_help)
        self.diagnostics_button.clicked.connect(self.show_diagnostics)
        help_layout.addWidget(self.shortcuts_help_button)
        help_layout.addWidget(self.diagnostics_button)
        form.addRow(help_group)
        return tab

    def show_shortcuts_help(self) -> None:
        from .shortcuts_help import show_shortcuts_help

        show_shortcuts_help(self)

    def show_diagnostics(self) -> None:
        from .diagnostics_dialog import DiagnosticsDialog

        dialog = DiagnosticsDialog(
            self.config.base_dir, self, pdfium_service=self._pdfium_service,
        )
        dialog.exec()

    def _build_windows_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.registration_status_label = QLabel(tab)
        self.registration_status_label.setWordWrap(True)
        layout.addWidget(self.registration_status_label)
        self.register_images_checkbox = QCheckBox(tr('画像を登録'), tab)
        self.register_archives_checkbox = QCheckBox(tr('漫画書庫を登録'), tab)
        self.register_pdf_checkbox = QCheckBox(tr('PDFを登録'), tab)
        self.register_context_menu_checkbox = QCheckBox(
            tr('「NivisViewerで開く」を右クリックメニューへ追加'),
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
            tr('登録は「プログラムから開く」と既定アプリ候補を追加するだけで、既定アプリを強制変更しません。'),
            tab,
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        self.register_button = QPushButton(tr('Windowsへ登録'), tab)
        self.unregister_button = QPushButton(tr('登録を解除'), tab)
        self.default_apps_button = QPushButton(
            tr('Windowsの既定のアプリ設定を開く'), tab
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
        self.open_behavior_combo.addItem(tr('アクティブなViewerを再利用'), "reuse_active")
        self.open_behavior_combo.addItem(tr('常に新しいViewerを開く'), "always_new")
        self.open_behavior_combo.addItem(
            tr('Viewerがあれば再利用し、なければ作成'),
            "reuse_or_create",
        )
        behavior_form.addRow(tr('ファイルを開く方法:'), self.open_behavior_combo)
        self.bring_to_front_checkbox = QCheckBox(
            tr('本を開いたときViewerWindowを一度だけ前面へ出す'),
            behavior_group,
        )
        behavior_form.addRow(self.bring_to_front_checkbox)
        self.loop_navigation_checkbox = QCheckBox(
            tr('最後の書庫から先頭へループする'),
            behavior_group,
        )
        behavior_form.addRow(self.loop_navigation_checkbox)

        spread_group = QGroupBox(tr('ページ表示'), tab)
        spread_form = QFormLayout(spread_group)
        self.join_spread_checkbox = QCheckBox(tr('見開きページを中央で密着表示'), spread_group)
        self.join_spread_checkbox.toggled.connect(self._sync_gap_enabled)
        spread_form.addRow(self.join_spread_checkbox)
        self.gap_spin = QSpinBox(spread_group)
        self.gap_spin.setRange(0, 100)
        self.gap_spin.setSuffix(" px")
        self.gap_note = QLabel(tr('密着表示時はページ間隔を使用しません。'), spread_group)
        gap_container = QWidget(spread_group)
        gap_layout = QVBoxLayout(gap_container)
        gap_layout.setContentsMargins(0, 0, 0, 0)
        gap_layout.addWidget(self.gap_spin)
        gap_layout.addWidget(self.gap_note)
        spread_form.addRow(tr('通常時のページ間隔:'), gap_container)
        self.single_first_checkbox = QCheckBox(tr('表紙を単独表示'), spread_group)
        spread_form.addRow(self.single_first_checkbox)
        self.wide_single_checkbox = QCheckBox(tr('横長画像を単独表示'), spread_group)
        spread_form.addRow(self.wide_single_checkbox)
        self.book_open_position_combo = QComboBox(spread_group)
        self.book_open_position_combo.addItem(
            tr('常に先頭ページから開く'),
            "first_page",
        )
        self.book_open_position_combo.addItem(
            tr('前回閉じたページから再開'),
            "resume_last",
        )
        spread_form.addRow(
            tr('書庫・PDF・フォルダーを開く位置:'),
            self.book_open_position_combo,
        )
        self.viewer_canvas_click_direction_combo = QComboBox(spread_group)
        self.viewer_canvas_click_direction_combo.addItem(
            tr('右側で次へ／左側で前へ'),
            "right_next",
        )
        self.viewer_canvas_click_direction_combo.addItem(
            tr('左側で次へ／右側で前へ'),
            "left_next",
        )
        self.viewer_canvas_click_direction_combo.addItem(
            tr('綴じ方向に合わせる（自動）'),
            "auto",
        )
        spread_form.addRow(
            tr('左右クリックのページ送り方向:'),
            self.viewer_canvas_click_direction_combo,
        )
        self.viewer_canvas_left_click_combo = QComboBox(spread_group)
        self.viewer_canvas_left_click_combo.addItem(
            tr('クリックでページ移動しない'),
            "none",
        )
        self.viewer_canvas_left_click_combo.addItem(
            tr('1ページずつ移動'),
            "next_single_page",
        )
        self.viewer_canvas_left_click_combo.addItem(
            tr('現在のページ送り単位で移動'),
            "next_display_unit",
        )
        spread_form.addRow(
            tr('左右クリックのページ移動量:'),
            self.viewer_canvas_left_click_combo,
        )
        self.viewer_slider_wheel_single_page_checkbox = QCheckBox(
            tr('下部UI上のマウスホイールで1ページずつ移動する'),
            spread_group,
        )
        spread_form.addRow(self.viewer_slider_wheel_single_page_checkbox)

        memory_group = QGroupBox(tr('Viewerメモリ'), tab)
        memory_form = QFormLayout(memory_group)
        self.viewer_memory_mode_combo = QComboBox(memory_group)
        for label, value in VIEWER_MEMORY_MODE_LABELS:
            self.viewer_memory_mode_combo.addItem(tr(label), value)
        memory_form.addRow(
            tr('ビューワーのメモリ使用量:'),
            self.viewer_memory_mode_combo,
        )

        resampling_group = QGroupBox(tr('画像の拡大縮小'), tab)
        resampling_layout = QVBoxLayout(resampling_group)

        normal_resampling_group = QGroupBox(tr('通常表示'), resampling_group)
        normal_resampling_form = QFormLayout(normal_resampling_group)
        self.viewer_downscale_algorithm_combo = QComboBox(
            normal_resampling_group
        )
        self.viewer_upscale_algorithm_combo = QComboBox(
            normal_resampling_group
        )
        for value, label in DOWNSCALE_ALGORITHM_LABELS.items():
            self.viewer_downscale_algorithm_combo.addItem(tr(label), value)
        for value, label in UPSCALE_ALGORITHM_LABELS.items():
            self.viewer_upscale_algorithm_combo.addItem(tr(label), value)
        normal_resampling_form.addRow(
            tr('縮小方式:'),
            self.viewer_downscale_algorithm_combo,
        )
        normal_resampling_form.addRow(
            tr('拡大方式:'),
            self.viewer_upscale_algorithm_combo,
        )
        resampling_layout.addWidget(normal_resampling_group)

        magnifier_resampling_group = QGroupBox(tr('拡大鏡'), resampling_group)
        magnifier_resampling_form = QFormLayout(magnifier_resampling_group)
        self.magnifier_allow_outside_image_checkbox = QCheckBox(
            tr('ルーペを画像の外側にも移動できる'), magnifier_resampling_group,
        )
        magnifier_resampling_form.addRow(self.magnifier_allow_outside_image_checkbox)
        self.magnifier_downscale_algorithm_combo = QComboBox(
            magnifier_resampling_group
        )
        self.magnifier_upscale_algorithm_combo = QComboBox(
            magnifier_resampling_group
        )
        for value, label in DOWNSCALE_ALGORITHM_LABELS.items():
            self.magnifier_downscale_algorithm_combo.addItem(tr(label), value)
        for value, label in UPSCALE_ALGORITHM_LABELS.items():
            self.magnifier_upscale_algorithm_combo.addItem(tr(label), value)
        magnifier_resampling_form.addRow(
            tr('縮小方式:'),
            self.magnifier_downscale_algorithm_combo,
        )
        magnifier_resampling_form.addRow(
            tr('拡大方式:'),
            self.magnifier_upscale_algorithm_combo,
        )
        resampling_layout.addWidget(magnifier_resampling_group)

        resampling_note = QLabel(
            tr('自動は倍率に応じて方式を選びます。設定の適用後は、開いている画像を再読込せずに表示用フレームだけを作り直します。'),
            resampling_group,
        )
        resampling_note.setWordWrap(True)
        resampling_layout.addWidget(resampling_note)

        prefetch_group = QGroupBox(tr('PDF・旧形式の先読み'), tab)
        prefetch_layout = QVBoxLayout(prefetch_group)
        prefetch_form = QFormLayout()
        self.prefetch_preset_combo = QComboBox(prefetch_group)
        for label, value in (
            (tr('無効'), "disabled"),
            (tr('省メモリ'), "memory_saver"),
            (tr('標準'), "standard"),
            (tr('多め'), "more"),
            (tr('カスタム'), "custom"),
        ):
            self.prefetch_preset_combo.addItem(label, value)
        prefetch_form.addRow(tr('先読みプリセット:'), self.prefetch_preset_combo)
        self.prefetch_direction_priority_checkbox = QCheckBox(
            tr('進行方向を優先する'),
            prefetch_group,
        )
        prefetch_form.addRow(self.prefetch_direction_priority_checkbox)
        prefetch_layout.addLayout(prefetch_form)

        self.prefetch_custom_group = QGroupBox(tr('カスタム設定'), prefetch_group)
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
            spin.setSuffix(tr(' 表示単位'))
        custom_form.addRow(
            tr('ZIP・Folder以外 進行方向:'),
            self.prefetch_image_forward_spin,
        )
        custom_form.addRow(
            tr('ZIP・Folder以外 逆方向:'),
            self.prefetch_image_backward_spin,
        )
        custom_form.addRow(tr('PDF 進行方向:'), self.prefetch_pdf_forward_spin)
        custom_form.addRow(tr('PDF 逆方向:'), self.prefetch_pdf_backward_spin)
        prefetch_layout.addWidget(self.prefetch_custom_group)
        display_unit_note = QLabel(
            tr('ZIP・Folderは上のメモリ量だけで保持量を決めます。この先読み設定はPDFと旧pipeline形式だけに適用されます。\n単ページ表示では1表示単位＝1ページ、見開き表示では1表示単位＝1見開きです。'),
            prefetch_group,
        )
        display_unit_note.setWordWrap(True)
        prefetch_layout.addWidget(display_unit_note)
        self.prefetch_preset_combo.currentIndexChanged.connect(
            self._on_prefetch_preset_changed
        )

        fullscreen_group = QGroupBox(tr('全画面UI'), tab)
        fullscreen_form = QFormLayout(fullscreen_group)
        self.fullscreen_hide_ui_checkbox = QCheckBox(
            tr('全画面時にUIを隠す'),
            fullscreen_group,
        )
        fullscreen_form.addRow(self.fullscreen_hide_ui_checkbox)
        self.fullscreen_hide_cursor_checkbox = QCheckBox(
            tr('全画面時にカーソルを隠す'),
            fullscreen_group,
        )
        fullscreen_form.addRow(self.fullscreen_hide_cursor_checkbox)
        self.fullscreen_auto_reveal_checkbox = QCheckBox(
            tr('全画面時、画面端でUIを表示'),
            fullscreen_group,
        )
        fullscreen_form.addRow(self.fullscreen_auto_reveal_checkbox)
        self.fullscreen_top_edge_trigger_spin = QSpinBox(fullscreen_group)
        self.fullscreen_top_edge_trigger_spin.setRange(4, 32)
        self.fullscreen_top_edge_trigger_spin.setSuffix(" px")
        fullscreen_form.addRow(
            tr('上端の反応範囲:'),
            self.fullscreen_top_edge_trigger_spin,
        )
        self.fullscreen_bottom_edge_trigger_spin = QSpinBox(fullscreen_group)
        self.fullscreen_bottom_edge_trigger_spin.setRange(12, 64)
        self.fullscreen_bottom_edge_trigger_spin.setSuffix(" px")
        fullscreen_form.addRow(
            tr('下端の反応範囲:'),
            self.fullscreen_bottom_edge_trigger_spin,
        )
        self.fullscreen_edge_trigger_spin = self.fullscreen_top_edge_trigger_spin
        self.fullscreen_hide_delay_spin = QSpinBox(fullscreen_group)
        self.fullscreen_hide_delay_spin.setRange(0, 3000)
        self.fullscreen_hide_delay_spin.setSingleStep(100)
        self.fullscreen_hide_delay_spin.setSuffix(" ms")
        fullscreen_form.addRow(
            tr('自動的に隠すまで:'),
            self.fullscreen_hide_delay_spin,
        )

        layout.addWidget(behavior_group)
        layout.addWidget(spread_group)
        layout.addWidget(memory_group)
        layout.addWidget(resampling_group)
        layout.addWidget(prefetch_group)
        layout.addWidget(fullscreen_group)
        layout.addWidget(self._make_tab_reset_button("viewer", tab))
        layout.addStretch(1)
        return tab

    def _build_archive_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        preference_group = QGroupBox(tr('外部書庫バックエンド'), tab)
        preference_form = QFormLayout(preference_group)
        self.archive_backend_combo = QComboBox(preference_group)
        self.archive_backend_combo.addItem(
            tr('自動（Windowsの関連付けを優先）'),
            "auto",
        )
        self.archive_backend_combo.addItem("WinRAR", "winrar")
        self.archive_backend_combo.addItem("7-Zip", "seven_zip")
        preference_form.addRow(tr('使用するバックエンド:'), self.archive_backend_combo)
        layout.addWidget(preference_group)

        winrar_group = QGroupBox("WinRAR", tab)
        winrar_form = QFormLayout(winrar_group)
        self.winrar_path_edit = QLineEdit(winrar_group)
        self.winrar_path_edit.setPlaceholderText(tr('空欄の場合は自動検出'))
        self.winrar_browse_button = QPushButton(tr('参照…'), winrar_group)
        self.winrar_browse_button.clicked.connect(self.browse_winrar)
        winrar_path_row = QWidget(winrar_group)
        winrar_path_layout = QHBoxLayout(winrar_path_row)
        winrar_path_layout.setContentsMargins(0, 0, 0, 0)
        winrar_path_layout.addWidget(self.winrar_path_edit, 1)
        winrar_path_layout.addWidget(self.winrar_browse_button)
        winrar_form.addRow(tr('実行ファイル:'), winrar_path_row)
        self.winrar_auto_button = QPushButton(tr('自動検出へ戻す'), winrar_group)
        self.winrar_auto_button.clicked.connect(self.use_automatic_winrar)
        self.winrar_redetect_button = QPushButton(tr('再検出'), winrar_group)
        self.winrar_redetect_button.clicked.connect(self.redetect_winrar)
        winrar_action_row = QWidget(winrar_group)
        winrar_action_layout = QHBoxLayout(winrar_action_row)
        winrar_action_layout.setContentsMargins(0, 0, 0, 0)
        winrar_action_layout.addWidget(self.winrar_auto_button)
        winrar_action_layout.addWidget(self.winrar_redetect_button)
        winrar_action_layout.addStretch(1)
        winrar_form.addRow(winrar_action_row)
        self.winrar_status_label = QLabel(tr('WinRAR：未確認'), winrar_group)
        self.winrar_status_label.setWordWrap(True)
        winrar_form.addRow(tr('現在の状態:'), self.winrar_status_label)
        layout.addWidget(winrar_group)

        group = QGroupBox("7-Zip", tab)
        form = QFormLayout(group)

        self.seven_zip_path_edit = QLineEdit(group)
        self.seven_zip_path_edit.setPlaceholderText(tr('空欄の場合は自動検出'))
        self.seven_zip_browse_button = QPushButton(tr('参照…'), group)
        self.seven_zip_browse_button.clicked.connect(self.browse_seven_zip)
        path_row = QWidget(group)
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.addWidget(self.seven_zip_path_edit, 1)
        path_layout.addWidget(self.seven_zip_browse_button)
        form.addRow(tr('7-Zip実行ファイル:'), path_row)

        self.seven_zip_auto_button = QPushButton(tr('自動検出へ戻す'), group)
        self.seven_zip_auto_button.clicked.connect(self.use_automatic_seven_zip)
        self.seven_zip_redetect_button = QPushButton(tr('再検出'), group)
        self.seven_zip_redetect_button.clicked.connect(self.redetect_seven_zip)
        action_row = QWidget(group)
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addWidget(self.seven_zip_auto_button)
        action_layout.addWidget(self.seven_zip_redetect_button)
        action_layout.addStretch(1)
        form.addRow(action_row)

        self.seven_zip_status_label = QLabel(tr('7-Zip：未確認'), group)
        self.seven_zip_status_label.setWordWrap(True)
        form.addRow(tr('現在の状態:'), self.seven_zip_status_label)
        note = QLabel(
            tr('WinRARと7-Zipは第三者ソフトウェアです。Windowsの関連付けと利用者が指定した実行ファイルだけを検証し、自動ダウンロードや自動インストールは行いません。'),
            group,
        )
        note.setWordWrap(True)
        form.addRow(note)
        layout.addWidget(group)
        layout.addWidget(self._make_tab_reset_button("archive", tab))
        layout.addStretch(1)
        return tab

    def _build_browser_wheel_scroll_group(self, parent: QWidget) -> QGroupBox:
        self.browser_wheel_scroll_group = QGroupBox(
            tr('Browser マウスホイール'),
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
            tr('標準ホイール1目盛りあたりの移動量です。System / DefaultではQtとWindowsの現在の動作をそのまま使用します。')
        )
        self.browser_wheel_scroll_mode_combo.currentIndexChanged.connect(
            self._sync_browser_wheel_scroll_controls
        )
        browser_wheel_form.addRow(
            tr('スクロール量:'),
            self.browser_wheel_scroll_mode_combo,
        )
        self.browser_wheel_scroll_custom_spin = QSpinBox(
            self.browser_wheel_scroll_group
        )
        self.browser_wheel_scroll_custom_spin.setRange(
            BROWSER_WHEEL_SCROLL_CUSTOM_MIN_ROWS,
            BROWSER_WHEEL_SCROLL_CUSTOM_MAX_ROWS,
        )
        self.browser_wheel_scroll_custom_spin.setSuffix(tr(' 行／1目盛り'))
        browser_wheel_form.addRow(
            tr('Customの行数:'),
            self.browser_wheel_scroll_custom_spin,
        )
        self.browser_wheel_scroll_restore_button = QPushButton(
            tr('既定に戻す'),
            self.browser_wheel_scroll_group,
        )
        self.browser_wheel_scroll_restore_button.clicked.connect(
            self._restore_browser_wheel_scroll_default
        )
        browser_wheel_form.addRow(self.browser_wheel_scroll_restore_button)
        browser_wheel_note = QLabel(
            tr('Smallは1行、Mediumは2行、Largeは3行を移動します。'),
            self.browser_wheel_scroll_group,
        )
        browser_wheel_note.setWordWrap(True)
        browser_wheel_form.addRow(browser_wheel_note)
        return self.browser_wheel_scroll_group

    def _build_file_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        self.file_operation_group = QGroupBox(tr('ファイル操作'), tab)
        file_operation_layout = QVBoxLayout(self.file_operation_group)
        self.delete_skip_confirmation_checkbox = QCheckBox(
            tr('削除確認を表示せずゴミ箱へ移動'),
            self.file_operation_group,
        )
        self.delete_confirm_focus_yes_checkbox = QCheckBox(
            tr('削除確認で「はい」を初期選択'),
            self.file_operation_group,
        )
        self.delete_skip_confirmation_checkbox.toggled.connect(
            self._sync_delete_confirmation_controls
        )
        file_operation_layout.addWidget(self.delete_skip_confirmation_checkbox)
        file_operation_layout.addWidget(self.delete_confirm_focus_yes_checkbox)
        recycle_note = QLabel(
            tr('どちらの設定でも削除先はWindowsのゴミ箱です。'),
            self.file_operation_group,
        )
        recycle_note.setWordWrap(True)
        file_operation_layout.addWidget(recycle_note)
        layout.addWidget(self.file_operation_group)
        layout.addWidget(self._make_tab_reset_button("file", tab))
        layout.addStretch(1)
        return tab

    def _build_browser_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        list_group = QGroupBox(tr('一覧表示'), tab)
        list_form = QFormLayout(list_group)
        self.browser_grid_preset_combo = QComboBox(list_group)
        for density, size in GRID_PRESET_THUMBNAIL_SIZES.items():
            label = tr(BROWSER_DISPLAY_DENSITY_LABELS[density])
            self.browser_grid_preset_combo.addItem(
                f"{label} ({size}px)",
                density.value,
            )
        self.browser_grid_preset_combo.activated.connect(
            self._apply_browser_grid_preset
        )
        list_form.addRow(tr('一覧プリセット:'), self.browser_grid_preset_combo)

        self.browser_display_density_combo = QComboBox(list_group)
        for value, label in BROWSER_DISPLAY_DENSITY_LABELS.items():
            self.browser_display_density_combo.addItem(tr(label), value.value)
        list_form.addRow(tr('表示密度:'), self.browser_display_density_combo)

        self.browser_sort_key_combo = QComboBox(list_group)
        for label, key, order in BROWSER_SORT_CHOICES:
            self.browser_sort_key_combo.addItem(tr(label), f"{key}:{order}")
        self.browser_sort_key_combo.activated.connect(self._activate_browser_sort)
        list_form.addRow(tr('並び替え:'), self.browser_sort_key_combo)

        self.browser_folders_first_checkbox = QCheckBox(
            tr('フォルダを常に先頭へ表示'),
            list_group,
        )
        list_form.addRow(self.browser_folders_first_checkbox)
        self.browser_location_history_limit_spin = QSpinBox(list_group)
        self.browser_location_history_limit_spin.setRange(1, 1000)
        self.browser_location_history_limit_spin.setSuffix(tr(' 件'))
        list_form.addRow(
            tr('場所の履歴を保持する件数:'),
            self.browser_location_history_limit_spin,
        )
        self.browser_search_history_limit_spin = QSpinBox(list_group)
        self.browser_search_history_limit_spin.setRange(0, 1000)
        self.browser_search_history_limit_spin.setSuffix(tr(' 件'))
        self.browser_search_history_limit_spin.setSpecialValueText(tr('保存しない'))
        list_form.addRow(
            tr('検索履歴の保持件数:'),
            self.browser_search_history_limit_spin,
        )
        self.browser_preserve_search_for_viewer_roundtrip_checkbox = QCheckBox(
            tr('検索結果からViewerを開いたとき、戻るまで検索を維持'),
            list_group,
        )
        list_form.addRow(
            self.browser_preserve_search_for_viewer_roundtrip_checkbox
        )
        self.browser_tag_grouped_checkbox = QCheckBox(
            tr('タグボタンをまとめる'),
            list_group,
        )
        self.browser_tag_grouped_checkbox.setToolTip(
            tr('登録タグの絞り込みを「タグ」ボタンのメニューへまとめます。')
        )
        list_form.addRow(self.browser_tag_grouped_checkbox)
        self.browser_item_spacing_x_spin = QSpinBox(list_group)
        self.browser_item_spacing_x_spin.setRange(0, 32)
        self.browser_item_spacing_x_spin.setSuffix(" px")
        self.browser_item_spacing_x_spin.setToolTip(
            tr('隣り合う項目の間に追加する横方向の空白です。画像やファイル名の幅は変えません。0で最小間隔。')
        )
        list_form.addRow(tr('項目の横間隔:'), self.browser_item_spacing_x_spin)
        self.browser_item_spacing_y_spin = QSpinBox(list_group)
        self.browser_item_spacing_y_spin.setRange(0, 32)
        self.browser_item_spacing_y_spin.setSuffix(" px")
        self.browser_item_spacing_y_spin.setToolTip(
            tr('ファイル名領域の下から次の行までに追加する空白です。0で最小間隔。')
        )
        list_form.addRow(tr('項目の縦間隔:'), self.browser_item_spacing_y_spin)
        self.browser_cell_padding_spin = QSpinBox(list_group)
        self.browser_cell_padding_spin.setRange(0, 12)
        self.browser_cell_padding_spin.setSuffix(" px")
        list_form.addRow(
            tr('項目内余白（全辺）:'),
            self.browser_cell_padding_spin,
        )
        self.browser_cell_padding_spin.setToolTip(
            tr('各項目の内側に確保する余白です。画像サイズは変えず、横幅と高さをそれぞれ値の2倍だけ増やします。')
        )
        self.browser_filename_display_combo = QComboBox(list_group)
        self.browser_filename_display_combo.addItem(tr('非表示'), "hidden")
        self.browser_filename_display_combo.addItem(tr('1行'), "one_line")
        self.browser_filename_display_combo.addItem(tr('2行'), "two_lines")
        list_form.addRow(tr('ファイル名:'), self.browser_filename_display_combo)
        self.browser_filename_extension_checkbox = QCheckBox(
            tr('ファイル名の拡張子を表示'), list_group
        )
        list_form.addRow(self.browser_filename_extension_checkbox)
        self.browser_filename_extension_checkbox.setToolTip(
            tr('ファイル名の表示だけを変えます。既定では拡張子を表示します。実際の名前、検索・並び替え・開く・コピーなどの動作や、ファイル種類のアイコンは変わりません。フォルダ名と拡張子がない名前はそのままです。')
        )
        self.browser_filename_gap_spin = QSpinBox(list_group)
        self.browser_filename_gap_spin.setRange(0, 32)
        self.browser_filename_gap_spin.setSuffix(" px")
        list_form.addRow(tr('画像とファイル名の間隔:'), self.browser_filename_gap_spin)
        self.browser_filename_gap_spin.setToolTip(
            tr('画像枠の下端とファイル名領域の間の空白です。ファイル名非表示時は使いません。')
        )
        self.browser_filename_padding_y_spin = QSpinBox(list_group)
        self.browser_filename_padding_y_spin.setRange(0, 16)
        self.browser_filename_padding_y_spin.setSuffix(" px")
        list_form.addRow(tr('ファイル名内余白（上下）:'), self.browser_filename_padding_y_spin)
        self.browser_filename_padding_y_spin.setToolTip(
            tr('ファイル名領域の内側で、文字の上と下にそれぞれ追加する余白です。0で文字の行高のみ。非表示時は使いません。')
        )
        self.browser_filename_elide_combo = QComboBox(list_group)
        self.browser_filename_elide_combo.addItem(
            tr('先頭を優先（後方を省略）'), "right"
        )
        self.browser_filename_elide_combo.addItem(
            tr('中央を省略'), "middle"
        )
        list_form.addRow(
            tr('長いファイル名:'), self.browser_filename_elide_combo
        )
        self.browser_filename_elide_combo.setToolTip(
            tr('1行表示と2行表示の末尾行で使う省略方式です。先頭優先は先頭側を残し、後方に省略記号を表示します。2行表示の折り返し位置は変えません。')
        )
        self.browser_filename_font_size_combo = QComboBox(list_group)
        self.browser_filename_font_size_combo.addItem(
            tr('自動（密度に合わせる）'), 0
        )
        for point_size in range(6, 25):
            self.browser_filename_font_size_combo.addItem(
                f"{point_size} pt", point_size
            )
        list_form.addRow(
            tr('ファイル名フォントサイズ:'),
            self.browser_filename_font_size_combo,
        )
        self.browser_filename_font_size_combo.setToolTip(
            tr('ファイル名の文字サイズです。表示領域の高さも文字に合わせて変わり、画像サイズは変わりません。自動は密度ごとの既定サイズを使います。')
        )
        self.browser_filename_display_combo.currentIndexChanged.connect(
            self._sync_browser_filename_controls
        )
        self.browser_show_hidden_checkbox = QCheckBox(
            tr('隠しファイルとフォルダを表示'),
            list_group,
        )
        list_form.addRow(self.browser_show_hidden_checkbox)
        self.browser_show_unsupported_checkbox = QCheckBox(
            tr('非対応ファイルも表示'),
            list_group,
        )
        list_form.addRow(self.browser_show_unsupported_checkbox)
        self.browser_show_system_checkbox = QCheckBox(
            tr('保護されたシステム項目を表示'),
            list_group,
        )
        self.browser_show_system_checkbox.setToolTip(
            tr('Windowsの保護されたシステム項目を表示します。操作時は注意してください。')
        )
        list_form.addRow(self.browser_show_system_checkbox)
        self.browser_folder_snapshot_cache_checkbox_row = QWidget(list_group)
        snapshot_cache_checkbox_layout = QHBoxLayout(
            self.browser_folder_snapshot_cache_checkbox_row
        )
        snapshot_cache_checkbox_layout.setContentsMargins(0, 0, 0, 0)
        snapshot_cache_checkbox_layout.setSpacing(4)
        snapshot_cache_checkbox_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.browser_folder_snapshot_cache_checkbox = QCheckBox(
            tr('フォルダ一覧をメモリに一時保存する'),
            self.browser_folder_snapshot_cache_checkbox_row,
        )
        self.browser_folder_snapshot_cache_checkbox.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )
        self.browser_folder_snapshot_cache_checkbox.setToolTip(
            tr(_FOLDER_SNAPSHOT_CACHE_TOOLTIP_TEXT)
        )
        snapshot_cache_checkbox_layout.addWidget(
            self.browser_folder_snapshot_cache_checkbox
        )
        self.browser_folder_snapshot_cache_help_button = _CircularHelpButton(
            self.browser_folder_snapshot_cache_checkbox_row
        )
        self.browser_folder_snapshot_cache_help_button.setText('?')
        self.browser_folder_snapshot_cache_help_button.setFixedSize(20, 20)
        self.browser_folder_snapshot_cache_help_button.setAutoRaise(True)
        self.browser_folder_snapshot_cache_help_button.setToolTip(
            tr(_FOLDER_SNAPSHOT_CACHE_TOOLTIP_TEXT)
        )
        self.browser_folder_snapshot_cache_help_button.setAccessibleName(
            tr('フォルダ一覧メモリ保存の説明')
        )
        self.browser_folder_snapshot_cache_help_button.clicked.connect(
            self._show_browser_folder_snapshot_cache_help
        )
        snapshot_cache_checkbox_layout.addWidget(
            self.browser_folder_snapshot_cache_help_button
        )
        list_form.addRow(self.browser_folder_snapshot_cache_checkbox_row)
        self.browser_folder_snapshot_cache_max_entries_combo = QComboBox(
            list_group
        )
        for limit in BROWSER_FOLDER_SNAPSHOT_CACHE_ENTRY_LIMITS:
            self.browser_folder_snapshot_cache_max_entries_combo.addItem(
                tr(
                    '{p0}項目（推定約{p1}MiB）',
                    p0=f'{limit:,}',
                    p1=estimate_browser_folder_snapshot_cache_mib(limit),
                ),
                limit,
            )
        self.browser_folder_snapshot_cache_max_entries_combo.setToolTip(
            tr('メモリに一時保存する一覧の合計項目数です。上限に達すると古い一覧から解放します。表示のメモリ量は保守的な概算で、推定96MiBの安全上限により先に解放される場合があります。')
        )
        snapshot_cache_cap_row = QWidget(list_group)
        snapshot_cache_cap_layout = QHBoxLayout(snapshot_cache_cap_row)
        snapshot_cache_cap_layout.setContentsMargins(0, 0, 0, 0)
        snapshot_cache_cap_layout.setSpacing(4)
        snapshot_cache_cap_layout.addWidget(
            self.browser_folder_snapshot_cache_max_entries_combo,
            1,
        )
        list_form.addRow(
            tr('メモリに保存する最大項目数'),
            snapshot_cache_cap_row,
        )
        self.browser_folder_snapshot_cache_checkbox.toggled.connect(
            self._sync_browser_folder_snapshot_cache_controls
        )
        self._sync_browser_folder_snapshot_cache_controls()

        cache_group = QGroupBox(tr('サムネイル'), tab)
        form = QFormLayout(cache_group)

        self.thumbnail_size_spin = QSpinBox(cache_group)
        self.thumbnail_size_spin.setRange(96, 384)
        self.thumbnail_size_spin.setSingleStep(32)
        self.thumbnail_size_spin.setSuffix(" px")
        form.addRow(tr('サムネイルサイズ:'), self.thumbnail_size_spin)

        self.thumbnail_frame_ratio_combo = QComboBox(cache_group)
        for ratio_id, (_ratio, label) in FRAME_RATIOS.items():
            self.thumbnail_frame_ratio_combo.addItem(tr(label), ratio_id)
        form.addRow(tr('画像枠の比率:'), self.thumbnail_frame_ratio_combo)

        self.browser_thumbnail_display_mode_combo = QComboBox(cache_group)
        for mode, label in BROWSER_THUMBNAIL_DISPLAY_MODES.items():
            self.browser_thumbnail_display_mode_combo.addItem(tr(label), mode)
        self.browser_thumbnail_display_mode_combo.setToolTip(
            tr('全体表示は画像全体を枠内へ収めます。中央クロップは縦横比を保ったまま画像中央で枠を埋めます。')
        )
        form.addRow(
            tr('Browser表示方式:'),
            self.browser_thumbnail_display_mode_combo,
        )

        folder_editor = FallbackBackgroundEditor(
            cache_group, default_color=BROWSER_FOLDER_FALLBACK_DEFAULT_COLOR,
            auto_label=tr('自動（薄い黄色）'), restore_label=tr('既定に戻す'),
        )
        file_editor = FallbackBackgroundEditor(
            cache_group, default_color=BROWSER_FILE_FALLBACK_DEFAULT_COLOR,
            auto_label=tr('自動（グレー）'), restore_label=tr('既定に戻す'),
        )
        # One explicit catalog drives both loading and serialization.
        self._fallback_background_editors = {
            "browser_folder_fallback_background": folder_editor,
            "browser_file_fallback_background": file_editor,
        }
        form.addRow(tr('フォルダの代替サムネイル背景:'), folder_editor)
        form.addRow(tr('ファイルの代替サムネイル背景:'), file_editor)
        # Keep existing control accessors; these are aliases, not state.
        self.browser_folder_fallback_background_combo = folder_editor.combo
        self.browser_folder_fallback_color_button = folder_editor.color_button
        self.browser_folder_fallback_restore_button = folder_editor.restore_button
        self.browser_file_fallback_background_combo = file_editor.combo
        self.browser_file_fallback_color_button = file_editor.color_button
        self.browser_file_fallback_restore_button = file_editor.restore_button

        self.thumbnail_crop_mode_combo = QComboBox(cache_group)
        for mode, label in CROP_MODES.items():
            self.thumbnail_crop_mode_combo.addItem(tr(label), mode)
        form.addRow(tr('切り抜き:'), self.thumbnail_crop_mode_combo)

        self.thumbnail_quality_mode_combo = QComboBox(cache_group)
        self.thumbnail_quality_mode_combo.addItem(tr('容量優先'), "economy")
        self.thumbnail_quality_mode_combo.addItem(tr('自動・推奨'), "auto")
        self.thumbnail_quality_mode_combo.addItem(tr('高画質'), "high")
        self.thumbnail_quality_mode_combo.setToolTip(
            tr('容量優先: 表示に近い解像度。自動: 高DPIと再縮小を考慮。高画質: より大きなキャッシュを使用します。')
        )
        form.addRow(tr('生成品質:'), self.thumbnail_quality_mode_combo)

        self.thumbnail_cache_max_edge_spin = QSpinBox(cache_group)
        self.thumbnail_cache_max_edge_spin.setRange(256, 2048)
        self.thumbnail_cache_max_edge_spin.setSingleStep(256)
        self.thumbnail_cache_max_edge_spin.setSuffix(" px")
        self.thumbnail_cache_max_edge_label_row = QWidget(cache_group)
        thumbnail_cache_max_edge_label_layout = QHBoxLayout(
            self.thumbnail_cache_max_edge_label_row
        )
        thumbnail_cache_max_edge_label_layout.setContentsMargins(0, 0, 0, 0)
        thumbnail_cache_max_edge_label_layout.setSpacing(4)
        thumbnail_cache_max_edge_label_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.thumbnail_cache_max_edge_label = QLabel(
            tr('生成最大辺:'),
            self.thumbnail_cache_max_edge_label_row,
        )
        self.thumbnail_cache_max_edge_label.setBuddy(
            self.thumbnail_cache_max_edge_spin
        )
        thumbnail_cache_max_edge_label_layout.addWidget(
            self.thumbnail_cache_max_edge_label
        )
        self.thumbnail_cache_max_edge_help_button = _CircularHelpButton(
            self.thumbnail_cache_max_edge_label_row
        )
        self.thumbnail_cache_max_edge_help_button.setText('?')
        self.thumbnail_cache_max_edge_help_button.setFixedSize(20, 20)
        self.thumbnail_cache_max_edge_help_button.setAutoRaise(True)
        self.thumbnail_cache_max_edge_help_button.setToolTip(
            tr(_THUMBNAIL_MAX_EDGE_TOOLTIP_TEXT)
        )
        self.thumbnail_cache_max_edge_help_button.setAccessibleName(
            tr('生成最大辺の説明')
        )
        self.thumbnail_cache_max_edge_help_button.clicked.connect(
            self._show_thumbnail_max_edge_help
        )
        thumbnail_cache_max_edge_label_layout.addWidget(
            self.thumbnail_cache_max_edge_help_button
        )
        self.thumbnail_cache_max_edge_spin.setToolTip(
            tr(_THUMBNAIL_MAX_EDGE_TOOLTIP_TEXT)
        )
        form.addRow(
            self.thumbnail_cache_max_edge_label_row,
            self.thumbnail_cache_max_edge_spin,
        )

        self.thumbnail_webp_quality_spin = QSpinBox(cache_group)
        self.thumbnail_webp_quality_spin.setObjectName("thumbnail_webp_quality_spin")
        self.thumbnail_webp_quality_spin.setRange(1, 100)
        self.thumbnail_webp_quality_label_row = QWidget(cache_group)
        thumbnail_webp_quality_label_layout = QHBoxLayout(
            self.thumbnail_webp_quality_label_row
        )
        thumbnail_webp_quality_label_layout.setContentsMargins(0, 0, 0, 0)
        thumbnail_webp_quality_label_layout.setSpacing(4)
        thumbnail_webp_quality_label_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.thumbnail_webp_quality_label = QLabel(
            tr('保存サムネイルの圧縮品質:'),
            self.thumbnail_webp_quality_label_row,
        )
        self.thumbnail_webp_quality_label.setBuddy(self.thumbnail_webp_quality_spin)
        thumbnail_webp_quality_label_layout.addWidget(
            self.thumbnail_webp_quality_label
        )
        self.thumbnail_webp_quality_help_button = _CircularHelpButton(
            self.thumbnail_webp_quality_label_row
        )
        self.thumbnail_webp_quality_help_button.setText('?')
        self.thumbnail_webp_quality_help_button.setFixedSize(20, 20)
        self.thumbnail_webp_quality_help_button.setAutoRaise(True)
        self.thumbnail_webp_quality_help_button.setToolTip(
            tr(_THUMBNAIL_WEBP_QUALITY_TOOLTIP_TEXT)
        )
        self.thumbnail_webp_quality_help_button.setAccessibleName(
            tr('保存サムネイルの圧縮品質の説明')
        )
        self.thumbnail_webp_quality_help_button.clicked.connect(
            self._show_thumbnail_webp_quality_help
        )
        thumbnail_webp_quality_label_layout.addWidget(
            self.thumbnail_webp_quality_help_button
        )
        self.thumbnail_webp_quality_spin.setToolTip(
            tr(_THUMBNAIL_WEBP_QUALITY_TOOLTIP_TEXT)
        )
        form.addRow(
            self.thumbnail_webp_quality_label_row,
            self.thumbnail_webp_quality_spin,
        )
        self.thumbnail_preserve_alpha_row = QWidget(cache_group)
        thumbnail_preserve_alpha_layout = QHBoxLayout(
            self.thumbnail_preserve_alpha_row
        )
        thumbnail_preserve_alpha_layout.setContentsMargins(0, 0, 0, 0)
        thumbnail_preserve_alpha_layout.setSpacing(4)
        thumbnail_preserve_alpha_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.thumbnail_preserve_alpha_checkbox = QCheckBox(
            tr('保存サムネイルの透明度を保持'),
            self.thumbnail_preserve_alpha_row,
        )
        self.thumbnail_preserve_alpha_checkbox.setObjectName("thumbnail_preserve_alpha_checkbox")
        self.thumbnail_preserve_alpha_checkbox.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )
        self.thumbnail_preserve_alpha_checkbox.setToolTip(
            tr(_THUMBNAIL_ALPHA_TOOLTIP_TEXT)
        )
        thumbnail_preserve_alpha_layout.addWidget(
            self.thumbnail_preserve_alpha_checkbox
        )
        self.thumbnail_preserve_alpha_help_button = _CircularHelpButton(
            self.thumbnail_preserve_alpha_row
        )
        self.thumbnail_preserve_alpha_help_button.setText('?')
        self.thumbnail_preserve_alpha_help_button.setFixedSize(20, 20)
        self.thumbnail_preserve_alpha_help_button.setAutoRaise(True)
        self.thumbnail_preserve_alpha_help_button.setToolTip(
            tr(_THUMBNAIL_ALPHA_TOOLTIP_TEXT)
        )
        self.thumbnail_preserve_alpha_help_button.setAccessibleName(
            tr('保存サムネイルの透明度の説明')
        )
        self.thumbnail_preserve_alpha_help_button.clicked.connect(
            self._show_thumbnail_preserve_alpha_help
        )
        thumbnail_preserve_alpha_layout.addWidget(
            self.thumbnail_preserve_alpha_help_button
        )
        form.addRow(self.thumbnail_preserve_alpha_row)

        self.disk_cache_checkbox = QCheckBox(
            tr('ディスクサムネイルキャッシュを使用する'),
            cache_group,
        )
        form.addRow(self.disk_cache_checkbox)

        self.cache_limit_spin = QSpinBox(cache_group)
        self.cache_limit_spin.setRange(128, 4096)
        self.cache_limit_spin.setSuffix(" MB")
        form.addRow(tr('キャッシュ最大容量:'), self.cache_limit_spin)

        self.cache_unused_days_combo = QComboBox(cache_group)
        for label, days in (
            (tr('使用しない'), 0),
            (tr('30日'), 30),
            (tr('90日（推奨）'), 90),
            (tr('180日'), 180),
            (tr('365日'), 365),
            (tr('カスタム'), -1),
        ):
            self.cache_unused_days_combo.addItem(label, days)
        self.cache_unused_days_spin = QSpinBox(cache_group)
        self.cache_unused_days_spin.setRange(7, 3650)
        self.cache_unused_days_spin.setSuffix(tr(' 日'))
        self.cache_unused_days_combo.currentIndexChanged.connect(
            lambda _index: self.cache_unused_days_spin.setEnabled(
                int(self.cache_unused_days_combo.currentData()) == -1
            )
        )
        form.addRow(tr('未使用期間の整理:'), self.cache_unused_days_combo)
        form.addRow(tr('カスタム日数:'), self.cache_unused_days_spin)
        cleanup_warning = QLabel(
            tr('短い期間では再生成とSSD書き込みが増える可能性があります。'),
            cache_group,
        )
        cleanup_warning.setWordWrap(True)
        form.addRow(cleanup_warning)

        self.cache_usage_label = QLabel(cache_group)
        self.cache_usage_label.setWordWrap(True)
        self.cache_usage_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.clear_cache_button = QPushButton(tr('キャッシュを削除'), cache_group)
        self.clear_cache_button.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Preferred,
        )
        self.clear_cache_button.clicked.connect(self.request_cache_clear)
        usage_row = QWidget(cache_group)
        usage_layout = QHBoxLayout(usage_row)
        usage_layout.setContentsMargins(0, 0, 0, 0)
        usage_layout.setSpacing(8)
        usage_layout.addWidget(self.cache_usage_label, 1)
        usage_layout.addWidget(
            self.clear_cache_button,
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        form.addRow(tr('現在の使用量:'), usage_row)

        layout.addWidget(list_group)
        layout.addWidget(cache_group)

        icon_size_group = QGroupBox(tr('ファイル種別アイコンの大きさ'), tab)
        icon_size_form = QFormLayout(icon_size_group)
        self.browser_icon_size_combos: dict[str, QComboBox] = {}
        self.browser_icon_size_custom_spins: dict[str, QSpinBox] = {}
        icon_size_labels = {
            "small": tr('小（75%）'),
            "medium": tr('中（100%）'),
            "large": tr('大（150%）'),
            "custom": tr('カスタム'),
        }
        for key, custom_key, label in ICON_SIZE_SETTING_SPECS:
            row = QWidget(icon_size_group)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            combo = QComboBox(row)
            for preset in BROWSER_ICON_SIZE_PRESETS:
                combo.addItem(icon_size_labels[preset], preset)
            custom_spin = QSpinBox(row)
            custom_spin.setRange(
                BROWSER_ICON_SIZE_CUSTOM_MIN_PERCENT,
                BROWSER_ICON_SIZE_CUSTOM_MAX_PERCENT,
            )
            custom_spin.setSuffix(" %")
            custom_spin.setEnabled(False)
            row_layout.addWidget(combo, 1)
            row_layout.addWidget(custom_spin)
            icon_size_form.addRow(tr(label), row)
            self.browser_icon_size_combos[key] = combo
            self.browser_icon_size_custom_spins[custom_key] = custom_spin

            def sync_custom_spin(index: int, *, combo=combo, spin=custom_spin) -> None:
                spin.setEnabled(combo.itemData(index) == "custom")

            combo.currentIndexChanged.connect(sync_custom_spin)
        icon_size_note = QLabel(
            tr('中（100%）は現在の表示サイズです。中央と左下、フォルダとそれ以外を個別に設定できます。'),
            icon_size_group,
        )
        icon_size_note.setWordWrap(True)
        icon_size_note.setStyleSheet("color: palette(mid);")
        icon_size_form.addRow(icon_size_note)
        layout.addWidget(icon_size_group)

        preview_group = QGroupBox(tr('汎用ファイルプレビュー'), tab)
        preview_form = QFormLayout(preview_group)
        self.text_preview_checkbox = QCheckBox(
            tr('テキストファイルの内容をプレビューする'),
            preview_group,
        )
        preview_form.addRow(self.text_preview_checkbox)
        self.video_thumbnail_checkbox = QCheckBox(
            tr('動画のサムネイルを表示する'),
            preview_group,
        )
        preview_form.addRow(self.video_thumbnail_checkbox)
        self.video_thumbnail_backend_combo = QComboBox(preview_group)
        self.video_thumbnail_backend_combo.addItem(tr('自動・推奨'), "auto")
        self.video_thumbnail_backend_combo.addItem(
            tr('Windows Shellのみ'),
            "windows_shell",
        )
        self.video_thumbnail_backend_combo.addItem(tr('FFmpegのみ'), "ffmpeg")
        self.video_thumbnail_backend_combo.addItem(tr('無効'), "disabled")
        preview_form.addRow(
            tr('動画バックエンド:'),
            self.video_thumbnail_backend_combo,
        )
        self.video_thumbnail_frame_mode_combo = QComboBox(preview_group)
        self.video_thumbnail_frame_mode_combo.addItem(tr('代表フレーム（推奨）'), "smart")
        self.video_thumbnail_frame_mode_combo.addItem(tr('再生時間の1/3'), "one_third")
        self.video_thumbnail_frame_mode_combo.addItem(
            tr('Windows Shellの選択'),
            "windows_shell",
        )
        preview_form.addRow(
            tr('動画フレーム:'),
            self.video_thumbnail_frame_mode_combo,
        )
        self.video_thumbnail_shell_placeholder_checkbox = QCheckBox(
            tr('FFmpeg結果までShell画像を一時表示する'),
            preview_group,
        )
        preview_form.addRow(self.video_thumbnail_shell_placeholder_checkbox)
        self.ffmpeg_path_edit = QLineEdit(preview_group)
        ffmpeg_path_row = QWidget(preview_group)
        ffmpeg_path_layout = QHBoxLayout(ffmpeg_path_row)
        ffmpeg_path_layout.setContentsMargins(0, 0, 0, 0)
        ffmpeg_path_layout.addWidget(self.ffmpeg_path_edit, 1)
        self.ffmpeg_browse_button = QPushButton(tr('参照...'), preview_group)
        self.ffmpeg_browse_button.clicked.connect(self.browse_ffmpeg)
        ffmpeg_path_layout.addWidget(self.ffmpeg_browse_button)
        self.ffmpeg_redetect_button = QPushButton(tr('再検出'), preview_group)
        self.ffmpeg_redetect_button.clicked.connect(self.redetect_ffmpeg)
        ffmpeg_path_layout.addWidget(self.ffmpeg_redetect_button)
        preview_form.addRow("FFmpeg:", ffmpeg_path_row)
        self.ffmpeg_status_label = QLabel(tr('FFmpeg：未確認'), preview_group)
        self.ffmpeg_status_label.setWordWrap(True)
        preview_form.addRow(tr('現在の状態:'), self.ffmpeg_status_label)
        ffmpeg_note = QLabel(
            tr('FFmpegは任意です。自動ダウンロードや自動同梱は行いません。'),
            preview_group,
        )
        ffmpeg_note.setWordWrap(True)
        preview_form.addRow(ffmpeg_note)
        self.browser_external_drop_combo = QComboBox(preview_group)
        self.browser_external_drop_combo.addItem(
            tr('一覧で選択・中央表示のみ'),
            "focus_only",
        )
        self.browser_external_drop_combo.addItem(
            tr('選択後に対応ファイルを開く'),
            "focus_and_open",
        )
        preview_form.addRow(
            tr('Browser中央への外部ドロップ:'),
            self.browser_external_drop_combo,
        )
        layout.addWidget(preview_group)

        sidebar_group = QGroupBox(tr('サイドバー'), tab)
        sidebar_form = QFormLayout(sidebar_group)
        self.browser_sidebar_layout_combo = QComboBox(sidebar_group)
        for value, label in (
            ("favorites_top_tree_bottom", tr('お気に入り上／ツリー下')),
            ("tree_top_favorites_bottom", tr('ツリー上／お気に入り下')),
            ("tabs", tr('タブ')),
            ("favorites_only", tr('お気に入りのみ')),
            ("tree_only", tr('ツリーのみ')),
        ):
            self.browser_sidebar_layout_combo.addItem(label, value)
        sidebar_form.addRow(
            tr('レイアウト:'),
            self.browser_sidebar_layout_combo,
        )
        self.folder_tree_sync_mode_combo = QComboBox(sidebar_group)
        self.folder_tree_sync_mode_combo.addItem(tr('同期しない'), "off")
        self.folder_tree_sync_mode_combo.addItem(
            tr('現在フォルダを選択'),
            "select_current",
        )
        self.folder_tree_sync_mode_combo.addItem(
            tr('現在フォルダへフォーカス'),
            "focus_current",
        )
        sidebar_form.addRow(
            tr('フォルダツリー同期:'),
            self.folder_tree_sync_mode_combo,
        )
        self.folder_tree_collapse_checkbox = QCheckBox(
            tr('無関係な自動展開を折りたたむ'),
            sidebar_group,
        )
        sidebar_form.addRow(self.folder_tree_collapse_checkbox)
        self.folder_tree_focus_rebase_checkbox = QCheckBox(
            tr('現在フォルダを基準にツリーの表示ルートを絞る'),
            sidebar_group,
        )
        sidebar_form.addRow(self.folder_tree_focus_rebase_checkbox)
        self.folder_tree_ancestor_levels_spin = QSpinBox(sidebar_group)
        self.folder_tree_ancestor_levels_spin.setRange(0, 12)
        sidebar_form.addRow(
            tr('現在フォルダの上位階層:'),
            self.folder_tree_ancestor_levels_spin,
        )
        self.favorite_row_padding_spin = QSpinBox(sidebar_group)
        self.favorite_row_padding_spin.setRange(0, 8)
        sidebar_form.addRow(
            tr('お気に入り上下余白:'),
            self.favorite_row_padding_spin,
        )
        self.favorite_row_spacing_spin = QSpinBox(sidebar_group)
        self.favorite_row_spacing_spin.setRange(0, 8)
        sidebar_form.addRow(
            tr('お気に入り行間隔:'),
            self.favorite_row_spacing_spin,
        )
        self.favorite_icon_size_spin = QSpinBox(sidebar_group)
        self.favorite_icon_size_spin.setRange(14, 24)
        sidebar_form.addRow(
            tr('お気に入りアイコン:'),
            self.favorite_icon_size_spin,
        )
        tree_focus_note = QLabel(
            tr('深いフォルダでインデントが増えすぎないよう、現在フォルダから指定した階層だけ上をツリーの表示基準にします。'),
            sidebar_group,
        )
        tree_focus_note.setWordWrap(True)
        sidebar_form.addRow(tree_focus_note)
        layout.addWidget(sidebar_group)
        layout.addWidget(self._make_tab_reset_button("browser", tab))
        layout.addStretch(1)
        return tab

    def _build_mouse_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        layout.addWidget(self._build_browser_wheel_scroll_group(tab))

        gesture_group = QGroupBox(tr('Viewer画像表示領域'), tab)
        gesture_form = QFormLayout(gesture_group)
        self.mouse_gestures_checkbox = QCheckBox(
            tr('Viewer画像表示領域でマウスジェスチャーを使用する'),
            gesture_group,
        )
        self.mouse_gestures_checkbox.toggled.connect(self._sync_gesture_controls)
        gesture_form.addRow(self.mouse_gestures_checkbox)
        self.mouse_gesture_trail_checkbox = QCheckBox(
            tr('操作中に軌跡を表示する（Viewer・Browser共通）'),
            gesture_group,
        )
        gesture_form.addRow(self.mouse_gesture_trail_checkbox)
        self.mouse_gesture_distance_spin = QSpinBox(gesture_group)
        self.mouse_gesture_distance_spin.setRange(12, 200)
        self.mouse_gesture_distance_spin.setSuffix(" px")
        gesture_form.addRow(tr('認識最小距離:'), self.mouse_gesture_distance_spin)
        self.gesture_down_combo = self._command_combo(gesture_group)
        self.gesture_up_combo = self._command_combo(gesture_group)
        self.gesture_left_combo = self._command_combo(gesture_group)
        self.gesture_right_combo = self._command_combo(gesture_group)
        gesture_form.addRow(tr('下へドラッグ (D):'), self.gesture_down_combo)
        gesture_form.addRow(tr('上へドラッグ (U):'), self.gesture_up_combo)
        gesture_form.addRow(tr('左へドラッグ (L):'), self.gesture_left_combo)
        gesture_form.addRow(tr('右へドラッグ (R):'), self.gesture_right_combo)

        browser_gesture_group = QGroupBox(
            tr('Browserサムネイル一覧領域'),
            tab,
        )
        browser_gesture_layout = QVBoxLayout(browser_gesture_group)
        self.browser_folder_gestures_checkbox = QCheckBox(
            tr('Browserのサムネイル一覧でフォルダージェスチャーを使用する'),
            browser_gesture_group,
        )
        browser_gesture_layout.addWidget(self.browser_folder_gestures_checkbox)
        browser_gesture_description = QLabel(
            tr('上：上の階層へ移動\u3000左：前のフォルダー\u3000右：次のフォルダー\u3000下：フォルダーを更新'),
            browser_gesture_group,
        )
        browser_gesture_description.setWordWrap(True)
        browser_gesture_layout.addWidget(browser_gesture_description)

        button_group = QGroupBox(tr('マウス追加ボタン'), tab)
        button_form = QFormLayout(button_group)
        self.mouse_back_action_combo = self._command_combo(button_group)
        self.mouse_forward_action_combo = self._command_combo(button_group)
        button_form.addRow(tr('戻る / XButton1:'), self.mouse_back_action_combo)
        button_form.addRow(tr('進む / XButton2:'), self.mouse_forward_action_combo)
        self.mouse_side_buttons_folder_navigation_checkbox = QCheckBox(
            tr('画像表示中はサイドボタンでフォルダー間を移動'), button_group,
        )
        button_form.addRow(self.mouse_side_buttons_folder_navigation_checkbox)

        layout.addWidget(gesture_group)
        layout.addWidget(browser_gesture_group)
        layout.addWidget(button_group)
        layout.addWidget(self._make_tab_reset_button("mouse", tab))
        layout.addStretch(1)
        return tab

    def load_current_values(self) -> None:
        self.mouse_side_buttons_folder_navigation_checkbox.setChecked(
            bool(self.config.get("mouse_side_buttons_folder_navigation", False))
        )
        self.magnifier_allow_outside_image_checkbox.setChecked(
            bool(self.config.get("magnifier_allow_outside_image", True))
        )
        self.ui_language_combo.setCurrentIndex(max(
            0, self.ui_language_combo.findData(self.config.get("ui_language", "ja")),
        ))
        behavior = str(self.config.get("open_viewer_behavior", "reuse_or_create"))
        index = self.open_behavior_combo.findData(behavior)
        self.open_behavior_combo.setCurrentIndex(max(0, index))
        self._load_shortcut_controls()
        self.bring_to_front_checkbox.setChecked(
            bool(self.config.get("bring_viewer_to_front_on_open", True))
        )
        self.delete_confirm_focus_yes_checkbox.setChecked(
            bool(
                self.config.get(
                    "file_operation_delete_confirm_focus_yes",
                    False,
                )
            )
        )
        self.delete_skip_confirmation_checkbox.setChecked(
            bool(
                self.config.get(
                    "file_operation_delete_skip_confirmation",
                    False,
                )
            )
        )
        self._sync_delete_confirmation_controls()
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
            self.config.get("viewer_canvas_click_direction", "auto"),
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
        for key, editor in self._fallback_background_editors.items():
            editor.load_value(str(self.config.get(key, "auto")))
        for key, custom_key, _label in ICON_SIZE_SETTING_SPECS:
            combo = self.browser_icon_size_combos[key]
            self._select_data(
                combo,
                self.config.get(key, "medium"),
            )
            spin = self.browser_icon_size_custom_spins[custom_key]
            spin.setValue(
                normalize_browser_icon_size_custom_percent(
                    self.config.get(custom_key, 100)
                )
            )
            spin.setEnabled(combo.currentData() == "custom")
        self._select_data(
            self.thumbnail_quality_mode_combo,
            self.config.get("thumbnail_quality_mode", "auto"),
        )
        self.thumbnail_webp_quality_spin.setValue(
            int(self.config.get("thumbnail_webp_quality", 60))
        )
        self.thumbnail_preserve_alpha_checkbox.setChecked(self.config.get("thumbnail_preserve_alpha", False) is True)
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
        self.browser_tag_grouped_checkbox.setChecked(
            bool(self.config.get("browser_tag_grouped", False))
        )
        self.browser_filename_extension_checkbox.setChecked(
            bool(self.config.get("browser_filename_show_extension", True))
        )
        self._select_data(
            self.browser_filename_elide_combo,
            self.config.get("browser_filename_elide_mode", "right"),
        )
        self._select_data(
            self.browser_filename_font_size_combo,
            int(self.config.get("browser_filename_font_size", 0)),
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
        self.browser_folder_snapshot_cache_checkbox.setChecked(
            bool(
                self.config.get(
                    "browser_folder_snapshot_cache_enabled",
                    True,
                )
            )
        )
        self._select_data(
            self.browser_folder_snapshot_cache_max_entries_combo,
            normalize_browser_folder_snapshot_cache_max_entries(
                self.config.get(
                    "browser_folder_snapshot_cache_max_entries",
                    60_000,
                )
            ),
        )
        self._sync_browser_folder_snapshot_cache_controls()
        self._select_data(
            self.browser_wheel_scroll_mode_combo,
            self.config.get("browser_wheel_scroll_mode", "system"),
        )
        self.browser_wheel_scroll_custom_spin.setValue(
            int(self.config.get("browser_wheel_scroll_custom_rows", 3))
        )
        self._sync_browser_wheel_scroll_controls()
        self._sync_browser_grid_preset()
        self._browser_random_seed = self.config.get("browser_random_seed", 0)
        # Random ignores direction, but an unchanged dialog must not rewrite
        # a legacy persisted descending value and trigger a redundant reset.
        self._browser_random_sort_order = self.config.get("browser_sort_order", "ascending")
        self.browser_sort_key_combo.setCurrentIndex(browser_sort_choice_index(
            self.config.get("browser_sort_key"), self.config.get("browser_sort_order"),
        ))
        self.browser_folders_first_checkbox.setChecked(
            bool(self.config.get("browser_folders_first", True))
        )
        self.browser_location_history_limit_spin.setValue(
            int(self.config.get("browser_location_history_limit", 50))
        )
        self.browser_search_history_limit_spin.setValue(
            int(self.config.get("browser_search_history_limit", 50))
        )
        self.browser_preserve_search_for_viewer_roundtrip_checkbox.setChecked(
            bool(
                self.config.get(
                    "browser_preserve_search_for_viewer_roundtrip",
                    True,
                )
            )
        )
        self.browser_item_spacing_x_spin.setValue(
            int(self.config.get("browser_item_spacing_x", 0))
        )
        self.browser_item_spacing_y_spin.setValue(
            int(self.config.get("browser_item_spacing_y", 0))
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
        self._sync_browser_filename_controls()
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
            self.gesture_left_combo,
            self._gesture_bindings_base.get("L", ""),
        )
        self._select_command(
            self.gesture_right_combo,
            self._gesture_bindings_base.get("R", ""),
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

    @property
    def _folder_fallback_custom_color(self) -> str:
        return self._fallback_background_editors["browser_folder_fallback_background"].custom_color

    @_folder_fallback_custom_color.setter
    def _folder_fallback_custom_color(self, value: str) -> None:
        self._fallback_background_editors["browser_folder_fallback_background"].custom_color = value

    @property
    def _file_fallback_custom_color(self) -> str:
        return self._fallback_background_editors["browser_file_fallback_background"].custom_color

    @_file_fallback_custom_color.setter
    def _file_fallback_custom_color(self, value: str) -> None:
        self._fallback_background_editors["browser_file_fallback_background"].custom_color = value

    def _choose_folder_fallback_background_color(self) -> None:
        self._fallback_background_editors["browser_folder_fallback_background"].choose_color()

    def _restore_folder_fallback_background(self) -> None:
        self._fallback_background_editors["browser_folder_fallback_background"].restore_default()

    def _restore_browser_wheel_scroll_default(self) -> None:
        self._select_data(self.browser_wheel_scroll_mode_combo, "system")
        self.browser_wheel_scroll_custom_spin.setValue(3)
        self._sync_browser_wheel_scroll_controls()

    def _show_browser_folder_snapshot_cache_help(self) -> None:
        QMessageBox.information(
            self,
            tr('フォルダ一覧メモリ保存の説明'),
            tr(_FOLDER_SNAPSHOT_CACHE_HELP_TEXT),
        )

    def _show_thumbnail_webp_quality_help(self) -> None:
        QMessageBox.information(
            self,
            tr('保存サムネイルの圧縮品質の説明'),
            tr(_THUMBNAIL_WEBP_QUALITY_HELP_TEXT),
        )

    def _show_thumbnail_preserve_alpha_help(self) -> None:
        QMessageBox.information(
            self,
            tr('保存サムネイルの透明度の説明'),
            tr(_THUMBNAIL_ALPHA_HELP_TEXT),
        )

    def _show_thumbnail_max_edge_help(self) -> None:
        QMessageBox.information(
            self,
            tr('生成最大辺の説明'),
            tr(_THUMBNAIL_MAX_EDGE_HELP_TEXT),
        )

    def _activate_browser_sort(self, index: int) -> None:
        if BROWSER_SORT_CHOICES[index][1] == BrowserSortKey.RANDOM.value:
            self._browser_random_seed = new_browser_random_seed(self._browser_random_seed)
            self._browser_random_sort_order = "ascending"

    def _sync_browser_wheel_scroll_controls(
        self,
        *_args: object,
    ) -> None:
        self.browser_wheel_scroll_custom_spin.setEnabled(
            self.browser_wheel_scroll_mode_combo.currentData() == "custom"
        )

    def _sync_browser_folder_snapshot_cache_controls(
        self,
        *_args: object,
    ) -> None:
        self.browser_folder_snapshot_cache_max_entries_combo.setEnabled(
            self.browser_folder_snapshot_cache_checkbox.isChecked()
        )

    def _sync_delete_confirmation_controls(
        self,
        *_args: object,
    ) -> None:
        self.delete_confirm_focus_yes_checkbox.setEnabled(
            not self.delete_skip_confirmation_checkbox.isChecked()
        )

    def _sync_folder_fallback_background_controls(self, *_args: object) -> None:
        self._fallback_background_editors["browser_folder_fallback_background"].sync_controls()

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

    def _sync_browser_filename_controls(self) -> None:
        visible = self.browser_filename_display_combo.currentData() != "hidden"
        self.browser_filename_gap_spin.setEnabled(visible)
        self.browser_filename_padding_y_spin.setEnabled(visible)
        self.browser_filename_extension_checkbox.setEnabled(visible)
        self.browser_filename_elide_combo.setEnabled(visible)
        self.browser_filename_font_size_combo.setEnabled(visible)

    def refresh_registration_status(self) -> None:
        service = self._file_registration_service
        if service is None:
            self.registration_status_label.setText(
                tr('Windows関連付けはこの起動環境では利用できません。')
            )
            return
        status = service.get_status()
        if status.registered:
            match = (
                tr('現在の実行ファイルと一致')
                if status.matches_current_executable
                else tr('現在の実行ファイルと不一致（移動後は再登録が必要）')
            )
            self.registration_status_label.setText(
                tr('登録済み: {p0}\n{p1}\n{p2}', p0=', '.join(status.registered_extensions), p1=status.executable_path, p2=match)
            )
        else:
            detail = f"\n{status.error_message}" if status.error_message else ""
            self.registration_status_label.setText(tr('未登録{p0}', p0=detail))

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
            QMessageBox.information(self, tr('Windows連携'), tr('登録する形式を選択してください。'))
            return
        answer = QMessageBox.question(
            self,
            tr('Windowsへ登録'),
            tr('選択した形式の「プログラムから開く」候補へNivisViewerを登録しますか？'),
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
            QMessageBox.warning(self, tr('Windows連携'), status.error_message)

    def _unregister_from_windows(self) -> None:
        service = self._file_registration_service
        if service is None:
            return
        answer = QMessageBox.question(
            self,
            tr('登録を解除'),
            tr('NivisViewerが作成したWindows関連付け情報を解除しますか？'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        status = service.unregister()
        self.refresh_registration_status()
        if status.error_message:
            QMessageBox.warning(self, tr('Windows連携'), status.error_message)

    def _open_default_apps(self) -> None:
        service = self._file_registration_service
        if service is not None and not service.open_default_apps_settings():
            QMessageBox.warning(
                self,
                tr('Windows連携'),
                tr('Windowsの既定のアプリ設定を開けませんでした。'),
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

    def _sync_viewer_close_shortcut_status(self) -> bool:
        return self._sync_shortcut_status()

    def values(self) -> dict[str, object]:
        shortcut_values = self._shortcut_values_from_ui()
        bindings = dict(self._gesture_bindings_base)
        for pattern, combo in (
            ("D", self.gesture_down_combo),
            ("U", self.gesture_up_combo),
            ("L", self.gesture_left_combo),
            ("R", self.gesture_right_combo),
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
            "ui_language": self.ui_language_combo.currentData(),
            "open_viewer_behavior": self.open_behavior_combo.currentData(),
            "shortcut_bindings": shortcut_values,
            "browser_cancel_clears_filters": (
                self.browser_cancel_clears_filters_checkbox.isChecked()
            ),
            "viewer_close_shortcut": (
                shortcut_values["viewer"].get("viewer_close", [""])[0]
                if shortcut_values["viewer"].get("viewer_close", [])
                else ""
            ),
            "viewer_slideshow_chord_enabled": self.viewer_slideshow_chord_checkbox.isChecked(),
            "bring_viewer_to_front_on_open": self.bring_to_front_checkbox.isChecked(),
            "file_operation_delete_confirm_focus_yes": (
                self.delete_confirm_focus_yes_checkbox.isChecked()
            ),
            "file_operation_delete_skip_confirmation": (
                self.delete_skip_confirmation_checkbox.isChecked()
            ),
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
            "magnifier_allow_outside_image": self.magnifier_allow_outside_image_checkbox.isChecked(),
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
            **{key: editor.value() for key, editor in self._fallback_background_editors.items()},
            **{
                key: str(self.browser_icon_size_combos[key].currentData() or "medium")
                for key, _custom_key, _label in ICON_SIZE_SETTING_SPECS
            },
            **{
                custom_key: self.browser_icon_size_custom_spins[custom_key].value()
                for _key, custom_key, _label in ICON_SIZE_SETTING_SPECS
            },
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
            "thumbnail_webp_quality": self.thumbnail_webp_quality_spin.value(),
            "thumbnail_preserve_alpha": self.thumbnail_preserve_alpha_checkbox.isChecked(),
            "browser_display_density": str(
                self.browser_display_density_combo.currentData()
            ),
            "browser_sort_key": BROWSER_SORT_CHOICES[self.browser_sort_key_combo.currentIndex()][1],
            "browser_sort_order": (
                self._browser_random_sort_order
                if BROWSER_SORT_CHOICES[self.browser_sort_key_combo.currentIndex()][1] == "random"
                else BROWSER_SORT_CHOICES[self.browser_sort_key_combo.currentIndex()][2]
            ),
            "browser_random_seed": self._browser_random_seed,
            "browser_folders_first": (
                self.browser_folders_first_checkbox.isChecked()
            ),
            "browser_location_history_limit": int(
                self.browser_location_history_limit_spin.value()
            ),
            "browser_search_history_limit": int(
                self.browser_search_history_limit_spin.value()
            ),
            "browser_preserve_search_for_viewer_roundtrip": (
                self.browser_preserve_search_for_viewer_roundtrip_checkbox.isChecked()
            ),
            "browser_tag_grouped": self.browser_tag_grouped_checkbox.isChecked(),
            "browser_item_spacing_x": self.browser_item_spacing_x_spin.value(),
            "browser_item_spacing_y": self.browser_item_spacing_y_spin.value(),
            "browser_cell_padding": self.browser_cell_padding_spin.value(),
            "browser_filename_display": str(
                self.browser_filename_display_combo.currentData()
            ),
            "browser_filename_gap": self.browser_filename_gap_spin.value(),
            "browser_filename_padding_y": (
                self.browser_filename_padding_y_spin.value()
            ),
            "browser_filename_show_extension": (
                self.browser_filename_extension_checkbox.isChecked()
            ),
            "browser_filename_elide_mode": str(
                self.browser_filename_elide_combo.currentData()
            ),
            "browser_filename_font_size": int(
                self.browser_filename_font_size_combo.currentData()
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
            "browser_folder_snapshot_cache_enabled": (
                self.browser_folder_snapshot_cache_checkbox.isChecked()
            ),
            "browser_folder_snapshot_cache_max_entries": (
                int(
                    self.browser_folder_snapshot_cache_max_entries_combo.currentData()
                )
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
            "mouse_side_buttons_folder_navigation": self.mouse_side_buttons_folder_navigation_checkbox.isChecked(),
        }

    def apply_settings(self) -> dict[str, object]:
        if not self._sync_viewer_close_shortcut_status():
            return {}
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
        if not self._sync_viewer_close_shortcut_status():
            return
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
            tr('FFmpeg実行ファイルを選択'),
            start,
            tr('FFmpeg executable (ffmpeg.exe);;実行ファイル (*.exe);;すべてのファイル (*.*)'),
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
        self.ffmpeg_status_label.setText(tr('FFmpeg：確認中…'))
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
                tr('FFmpeg：見つかりません（動画はWindows Shellを使用します）')
            )
        else:
            self.ffmpeg_status_label.setText(tr('FFmpeg：検出済み\n{p0}', p0=executable))

    def browse_winrar(self) -> None:
        if self._probes_closed:
            return
        start = self.winrar_path_edit.text().strip()
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            tr('WinRAR実行ファイルを選択'),
            start,
            tr('WinRAR executable (WinRAR.exe UnRAR.exe Rar.exe);;実行ファイル (*.exe);;すべてのファイル (*.*)'),
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
        self.winrar_status_label.setText(tr('WinRAR：確認中…'))
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
                tr('\nWindows関連付けから検出')
                if getattr(info, "discovery_source", None) == "association"
                else ""
            )
            self.winrar_status_label.setText(
                tr('WinRAR：検出済み{p0}\n{p1}{p2}', p0=source, p1=info.executable_path, p2=version)
            )
        else:
            detail = f"\n{info.error_message}" if info.error_message else ""
            self.winrar_status_label.setText(tr('WinRAR：見つかりません{p0}', p0=detail))

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
            tr('7-Zip実行ファイルを選択'),
            start,
            tr('7-Zip executable (7z.exe 7zz.exe);;実行ファイル (*.exe);;すべてのファイル (*.*)'),
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
        self.seven_zip_status_label.setText(tr('7-Zip：確認中…'))
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
                tr('7-Zip：検出済み\n{p0}{p1}', p0=info.executable_path, p1=version)
            )
        else:
            detail = f"\n{info.error_message}" if info.error_message else ""
            self.seven_zip_status_label.setText(tr('7-Zip：見つかりません{p0}', p0=detail))

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
                tr('サムネイルキャッシュを削除'),
                tr('保存済みのサムネイルキャッシュを削除しますか？'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.cache_clear_requested.emit()
        self.cache_usage_label.setText(tr('削除処理を要求しました。'))

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
                last_cleanup = str(stats.get("last_cleanup_display", tr('未実行')))
                self.cache_usage_label.setText(
                    tr('{p0} / {p1}件（今回 +{p2}）', p0=self._format_bytes(used), p1=entries, p2=self._format_bytes(growth))
                )
                self.cache_usage_label.setToolTip(
                    "\n".join(
                        (
                            tr('最後の整理: {p0}', p0=last_cleanup),
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
            self.cache_usage_label.setText(tr('取得できません'))
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
                tr('設定を保存できません'),
                tr('設定ファイルへ保存できませんでした。\n{p0}', p0=error),
            )
        return True

    def _sync_gap_enabled(self, joined: bool) -> None:
        self.gap_spin.setEnabled(not joined)
        self.gap_note.setVisible(joined)

    def _sync_gesture_controls(self, enabled: bool) -> None:
        # Trail visibility is shared with Browser gestures, independently of
        # whether Viewer recognition is enabled.
        self.mouse_gesture_trail_checkbox.setEnabled(True)
        for widget in (
            self.mouse_gesture_distance_spin,
            self.gesture_down_combo,
            self.gesture_up_combo,
            self.gesture_left_combo,
            self.gesture_right_combo,
        ):
            widget.setEnabled(enabled)

    @staticmethod
    def _command_combo(parent: QWidget) -> QComboBox:
        combo = QComboBox(parent)
        for label, command in COMMAND_CHOICES:
            combo.addItem(tr(label), command)
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
