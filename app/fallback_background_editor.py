"""Draft-only controls for the Browser's two fallback background settings.

No persistence or Browser effects live here: SettingsDialog applies the draft
through ConfigManager, and BrowserWindow owns repainting.
"""

from .i18n import tr

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QComboBox, QHBoxLayout, QPushButton, QWidget


class FallbackBackgroundEditor(QWidget):
    def __init__(self, parent: QWidget, *, default_color: str,
                 auto_label: str, restore_label: str) -> None:
        super().__init__(parent)
        self.default_color = default_color
        self.custom_color = default_color
        self.combo = QComboBox(self)
        self.combo.addItem(auto_label, "auto")
        self.combo.addItem(tr('カスタム色'), "custom")
        self.color_button = QPushButton(tr('色を選択…'), self)
        self.restore_button = QPushButton(restore_label, self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for control in (self.combo, self.color_button, self.restore_button):
            layout.addWidget(control)
        self.combo.currentIndexChanged.connect(self.sync_controls)
        self.color_button.clicked.connect(self.choose_color)
        self.restore_button.clicked.connect(self.restore_default)

    def load_value(self, value: str) -> None:
        # ConfigManager owns validation. Defend only the Qt color projection,
        # as the previous dialog implementation did.
        color = QColor(value)
        custom = value != "auto" and color.isValid()
        self.custom_color = color.name() if custom else self.default_color
        self.combo.setCurrentIndex(self.combo.findData("custom" if custom else "auto"))
        self.sync_controls()

    def value(self) -> str:
        return self.custom_color if self.combo.currentData() == "custom" else "auto"

    def choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.custom_color), self.window(), tr('代替サムネイル背景色'))
        if color.isValid():
            self.custom_color = color.name()
            self.combo.setCurrentIndex(self.combo.findData("custom"))
            self.sync_controls()

    def restore_default(self) -> None:
        self.load_value("auto")

    def sync_controls(self, *_args: object) -> None:
        self.color_button.setEnabled(self.combo.currentData() == "custom")
        color = QColor(self.custom_color)
        text_color = "#000000" if color.lightness() >= 128 else "#ffffff"
        self.color_button.setText(color.name().upper())
        self.color_button.setStyleSheet(
            "QPushButton {"
            f"background-color: {color.name()}; color: {text_color};"
            "}"
        )
