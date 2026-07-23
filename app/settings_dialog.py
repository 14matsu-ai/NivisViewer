from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config_manager import ConfigManager


class SettingsDialog(QDialog):
    settings_applied = Signal(object)
    cache_clear_requested = Signal()

    def __init__(
        self,
        config_manager: ConfigManager,
        parent: QWidget | None = None,
        *,
        cache_usage_getter: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("環境設定")
        self.setModal(True)
        self.resize(560, 470)
        self.config = config_manager
        self._cache_usage_getter = cache_usage_getter

        self._build_ui()
        self.load_current_values()

    def _build_ui(self) -> None:
        tabs = QTabWidget(self)
        tabs.addTab(self._build_viewer_tab(), "Viewer")
        tabs.addTab(self._build_browser_tab(), "Browser")

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
        layout.addWidget(tabs, 1)
        layout.addWidget(self.button_box)

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

        layout.addWidget(behavior_group)
        layout.addWidget(spread_group)
        layout.addStretch(1)
        return tab

    def _build_browser_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        cache_group = QGroupBox("サムネイル", tab)
        form = QFormLayout(cache_group)

        self.thumbnail_size_spin = QSpinBox(cache_group)
        self.thumbnail_size_spin.setRange(80, 500)
        self.thumbnail_size_spin.setSuffix(" px")
        form.addRow("サムネイルサイズ:", self.thumbnail_size_spin)

        self.disk_cache_checkbox = QCheckBox(
            "ディスクサムネイルキャッシュを使用する",
            cache_group,
        )
        form.addRow(self.disk_cache_checkbox)

        self.cache_limit_spin = QSpinBox(cache_group)
        self.cache_limit_spin.setRange(128, 4096)
        self.cache_limit_spin.setSuffix(" MB")
        form.addRow("キャッシュ最大容量:", self.cache_limit_spin)

        self.cache_usage_label = QLabel(cache_group)
        self.clear_cache_button = QPushButton("キャッシュを削除", cache_group)
        self.clear_cache_button.clicked.connect(self.request_cache_clear)
        usage_row = QWidget(cache_group)
        usage_layout = QHBoxLayout(usage_row)
        usage_layout.setContentsMargins(0, 0, 0, 0)
        usage_layout.addWidget(self.cache_usage_label, 1)
        usage_layout.addWidget(self.clear_cache_button)
        form.addRow("現在の使用量:", usage_row)

        layout.addWidget(cache_group)
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
        self.thumbnail_size_spin.setValue(int(self.config.get("thumbnail_size", 180)))
        self.disk_cache_checkbox.setChecked(
            bool(self.config.get("thumbnail_disk_cache_enabled", True))
        )
        self.cache_limit_spin.setValue(
            int(self.config.get("thumbnail_cache_limit_mb", 512))
        )
        self._sync_gap_enabled(self.join_spread_checkbox.isChecked())
        self.refresh_cache_usage()

    def values(self) -> dict[str, object]:
        return {
            "open_viewer_behavior": self.open_behavior_combo.currentData(),
            "bring_viewer_to_front_on_open": self.bring_to_front_checkbox.isChecked(),
            "loop_book_navigation": self.loop_navigation_checkbox.isChecked(),
            "join_spread_pages": self.join_spread_checkbox.isChecked(),
            "gap": self.gap_spin.value(),
            "single_first_page": self.single_first_checkbox.isChecked(),
            "treat_wide_image_as_single": self.wide_single_checkbox.isChecked(),
            "thumbnail_size": self.thumbnail_size_spin.value(),
            "thumbnail_disk_cache_enabled": self.disk_cache_checkbox.isChecked(),
            "thumbnail_cache_limit_mb": self.cache_limit_spin.value(),
        }

    def apply_settings(self) -> dict[str, object]:
        changed = self.config.apply(self.values(), save=True)
        self.load_current_values()
        self.settings_applied.emit(changed)
        return changed

    def accept(self) -> None:  # type: ignore[override]
        self.apply_settings()
        super().accept()

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
        try:
            used = max(0, int(self._cache_usage_getter())) if self._cache_usage_getter else 0
        except Exception:
            self.cache_usage_label.setText("取得できません")
            return
        self.cache_usage_label.setText(self._format_bytes(used))

    def _sync_gap_enabled(self, joined: bool) -> None:
        self.gap_spin.setEnabled(not joined)
        self.gap_note.setVisible(joined)

    @staticmethod
    def _format_bytes(value: int) -> str:
        if value >= 1024 * 1024:
            return f"{value / (1024 * 1024):.1f} MB"
        if value >= 1024:
            return f"{value / 1024:.1f} KB"
        return f"{value} bytes"
