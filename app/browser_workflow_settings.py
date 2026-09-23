"""Browser preferences, with one existing ConfigManager as persistence owner."""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QWidget, QToolTip)
from .browser_workflow_policy import normalize_workflow_settings, WORKFLOW_DEFAULTS
from .i18n import tr


class BrowserWorkflowSettings(QGroupBox):
    def __init__(self, parent=None) -> None:
        super().__init__(tr("背景生成・選択表示"), parent)
        form = QFormLayout(self)
        self.mode = QComboBox(self)
        for text, value in (("表示範囲のみ", "visible"), ("画面数を指定", "bounded"),
                            ("無制限（現在の一覧全体）", "unlimited")):
            self.mode.addItem(tr(text), value)
        self.screens = QSpinBox(self)
        self.screens.setRange(1, 100)
        self.screens.setSuffix(tr(" 画面分"))
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.mode)
        layout.addWidget(self.screens)
        self.mode.currentIndexChanged.connect(
            lambda _index: self.screens.setEnabled(self.mode.currentData() == "bounded"))
        form.addRow(tr("サムネイル背景生成の範囲:"), row)
        note = QLabel(tr("画面内を最優先し、停止後に前後の指定画面数を生成します。"
                         "無制限でも待機ジョブ・メモリ・ディスクの上限は維持します。"
                         "画像・フォルダ・書庫・PDFが対象です。"), self)
        note.setWordWrap(True)
        form.addRow(note)
        self.opacity = QSpinBox(self)
        self.opacity.setRange(0, 100)
        self.opacity.setSuffix(" %")
        self.opacity.setToolTip(tr("ファイル名にかかる選択色の不透明度。0%で透明、100%で不透明です。"))
        form.addRow(tr("ファイル名の選択色の不透明度:"), self.opacity)
        self.border_width_spin = QSpinBox(self)
        self.border_width_spin.setRange(1, 12)
        self.border_width_spin.setSuffix(" px")
        self.border_width_spin.setToolTip(tr("サムネイルとファイル名を囲む選択枠の太さ。画面倍率に追従します。"))
        form.addRow(tr("選択帯（外枠）の太さ:"), self.border_width_spin)
        self.color_button = QPushButton(tr("選択色を変更"), self)
        self.automatic = QCheckBox(tr("システムの選択色を使う"), self)
        self.color_button.clicked.connect(self._choose_color)
        self.automatic.toggled.connect(lambda value: self.color_button.setEnabled(not value))
        color_row = QWidget(self)
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.addWidget(self.automatic)
        color_layout.addWidget(self.color_button)
        form.addRow(tr("選択色:"), color_row)
        self._color = "#308cc6"
        self.load(WORKFLOW_DEFAULTS)

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, tr("選択色"))
        if color.isValid():
            self._color = color.name()
            self.color_button.setText(self._color)

    def load(self, settings) -> None:
        values = normalize_workflow_settings(settings)
        screens = int(values["browser_thumbnail_background_screens"])
        mode = "unlimited" if screens < 0 else "visible" if screens == 0 else "bounded"
        self.mode.setCurrentIndex(self.mode.findData(mode))
        self.screens.setValue(max(1, screens))
        self.opacity.setValue(int(values["browser_selection_filename_opacity"]))
        self.border_width_spin.setValue(int(values["browser_selection_border_width"]))
        color = str(values["browser_selection_color"])
        self.automatic.setChecked(color == "auto")
        if color != "auto":
            self._color = color
        self.color_button.setText(self._color)
        self.color_button.setEnabled(color != "auto")

    def values(self) -> dict[str, object]:
        mode = self.mode.currentData()
        return normalize_workflow_settings({
            "browser_thumbnail_background_screens": -1 if mode == "unlimited" else 0 if mode == "visible" else self.screens.value(),
            "browser_selection_filename_opacity": self.opacity.value(),
            "browser_selection_border_width": self.border_width_spin.value(),
            "browser_selection_color": "auto" if self.automatic.isChecked() else self._color,
        })


def gesture_help_row(checkbox, parent, button_class):
    """Reuse the Settings dialog's existing circular help-button appearance."""
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    layout.addWidget(checkbox)
    button = button_class(row)
    button.setObjectName(checkbox.objectName() + "_gesture_help")
    button.setAccessibleName(tr("マウスジェスチャーの使い方"))
    message = tr("右クリックを押したままマウスを動かし、右ボタンを離すと実行します。")
    button.setToolTip(message)
    button.clicked.connect(lambda _checked=False: QToolTip.showText(
        button.mapToGlobal(button.rect().bottomLeft()), message, button))
    layout.addWidget(button)
    layout.addStretch(1)
    return row
