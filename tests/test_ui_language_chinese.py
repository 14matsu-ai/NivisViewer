from __future__ import annotations

from string import Formatter
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QAbstractButton, QTabWidget

from app.config_manager import ConfigManager
from app.i18n import (
    UI_LANGUAGE_CHOICES,
    detect_windows_ui_language,
    install_ui_language,
    normalize_ui_language,
    tr,
)
from app.settings_dialog import SettingsDialog
from app.translations_en import ENGLISH, ENGLISH_DISAMBIGUATED
from app.translations_zh import (
    TRADITIONAL_CHINESE, SIMPLIFIED_CHINESE,
    TRADITIONAL_CHINESE_DISAMBIGUATED, SIMPLIFIED_CHINESE_DISAMBIGUATED,
)

_TECHNICAL_WORDS = {
    "NivisViewer", "Viewer", "Browser", "Windows", "Shell", "FFmpeg", "WinRAR", "ZIP", "Zip",
    "PDF", "RAR", "CBZ", "JPEG", "WebP", "PNG", "Qt", "Python", "HDD", "SSD",
    "MiB", "Ctrl", "Shift", "Alt", "Left", "Right", "Wheel", "Enter", "PageDown", "PageUp", "Esc", "S", "RGB",
    "Alpha", "Open", "Close", "Small", "Medium", "Large", "7-Zip", "pypdfium2",
    "AND", "OR", "DPI", "API", "CLI", "artifact", "final", "nested", "size", "mtime_ns", "final_exists", "source_known",
    "XButton1", "XButton2", "ffmpeg", "exe", "UnRAR", "Rar", "HRESULT",
    "png", "Space", "Backspace", "Home", "End", "Double-click", "QPixmap",
    "pillow-jxl-plugin", "Escape", "INTERNAL", "STAGING", "ARTIFACT", "System", "Default",
    "mtime", "exists", "source", "known",
}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("en-US", "en"),
        ("ja-JP", "ja"),
        ("zh", "zh-Hans"),
        ("zh-CN", "zh-Hans"),
        ("zh_SG", "zh-Hans"),
        ("zh-TW", "zh-Hant"),
        ("zh-HK", "zh-Hant"),
        ("zh-Hans", "zh-Hans"),
        ("zh-Hant", "zh-Hant"),
        ("fr", "ja"),
    ],
)
def test_chinese_language_aliases(value, expected):
    assert normalize_ui_language(value) == expected


@pytest.mark.parametrize("language", ["zh-Hans", "zh-Hant"])
def test_fresh_profile_uses_detected_language_and_existing_legacy_stays_japanese(
    tmp_path: Path, monkeypatch, language,
):
    monkeypatch.setattr("app.config_manager.detect_windows_ui_language", lambda: language)
    fresh = ConfigManager(tmp_path / "fresh.json")
    assert fresh.load()["ui_language"] == language
    fresh.save()
    assert ConfigManager(fresh.path).load()["ui_language"] == language

    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text('{"last_open_path": "C:/old"}', encoding="utf-8")
    legacy = ConfigManager(legacy_path)
    assert legacy.load()["ui_language"] == "ja"


def test_explicit_language_is_not_replaced_by_os_detection(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.config_manager.detect_windows_ui_language", lambda: "zh-Hant")
    path = tmp_path / "explicit.json"
    path.write_text('{"ui_language": "en"}', encoding="utf-8")
    assert ConfigManager(path).load()["ui_language"] == "en"


def test_catalogs_have_same_inventory_and_format_fields():
    formatter = Formatter()
    assert len(ENGLISH) == 1057
    for catalog in (SIMPLIFIED_CHINESE, TRADITIONAL_CHINESE):
        assert set(catalog) == set(ENGLISH)
        for source, translated in catalog.items():
            expected = sorted(field for _, field, _, _ in formatter.parse(ENGLISH[source]) if field)
            actual = sorted(field for _, field, _, _ in formatter.parse(translated) if field)
            assert actual == expected, source
            assert translated.count("\n") == source.count("\n"), source
            assert not re.search(r"[ぁ-ゖァ-ヺ]", translated), source
            assert "界面提示" not in translated and "介面提示" not in translated
            text_without_fields = re.sub(r"\{[^}]+\}", "", translated)
            for technical in sorted(_TECHNICAL_WORDS, key=len, reverse=True):
                text_without_fields = re.sub(
                    rf"(?<![A-Za-z0-9]){re.escape(technical)}(?![A-Za-z0-9])",
                    "",
                    text_without_fields,
                )
            words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text_without_fields)
            assert [word for word in words if word not in _TECHNICAL_WORDS] == [], (source, translated)
    assert set(SIMPLIFIED_CHINESE_DISAMBIGUATED) == set(ENGLISH_DISAMBIGUATED)
    assert set(TRADITIONAL_CHINESE_DISAMBIGUATED) == set(ENGLISH_DISAMBIGUATED)


def test_context_sensitive_browser_viewer_settings_and_errors():
    examples = {
        "フォルダ一覧をメモリに一時保存する": (
            "将文件夹列表暂存于内存", "將資料夾清單暫存於記憶體"),
        "絞り込み解除はコピー／切り取り候補解除より先に行います。": (
            "先清除筛选条件，再取消待复制或剪切的项目。",
            "先清除篩選條件，再取消待複製或剪下的項目。"),
        "移動先": ("目标位置", "目的地"),
        "現在の階層をエクスプローラーで開く": (
            "在文件资源管理器中打开当前文件夹", "在檔案總管中開啟目前資料夾"),
        "ごみ箱への移動がキャンセルされました": (
            "移至回收站的操作已取消", "移至資源回收筒的操作已取消"),
        "Browser ショートカット": ("Browser 快捷键", "Browser 快捷鍵"),
        "Viewer ショートカット": ("Viewer 快捷键", "Viewer 快捷鍵"),
    }
    for source, (simplified, traditional) in examples.items():
        assert SIMPLIFIED_CHINESE[source] == simplified
        assert TRADITIONAL_CHINESE[source] == traditional


@pytest.mark.parametrize("language", ["zh-Hans", "zh-Hant"])
def test_chinese_translation_boundary_and_settings_choices(qapp, tmp_path, language):
    install_ui_language(language)
    assert tr("設定") in {"设置", "設定"}
    assert tr("{p0}件を開けませんでした", p0=3) in {
        "无法打开 3 个项目", "無法開啟 3 個項目",
    }
    dialog = SettingsDialog(ConfigManager(tmp_path / f"{language}.json"))
    try:
        assert [
            (dialog.ui_language_combo.itemText(i), dialog.ui_language_combo.itemData(i))
            for i in range(dialog.ui_language_combo.count())
        ] == list(UI_LANGUAGE_CHOICES)
        assert dialog.ui_language_label.text() in {"显示语言 / Language", "顯示語言 / Language"}
        dialog.show()
        dialog.resize(620, 680)
        qapp.processEvents()
        assert dialog.isVisible()
        assert dialog.size().width() > 0 and dialog.size().height() > 0
        clipped = []
        for tabs in dialog.findChildren(QTabWidget):
            for index in range(tabs.count()):
                tabs.setCurrentIndex(index)
                qapp.processEvents()
                for button in dialog.findChildren(QAbstractButton):
                    if (button.isVisible() and button.text() and not button.text().startswith("#")
                            and button.width() < button.minimumSizeHint().width()):
                        clipped.append(button.text())
        assert not clipped, clipped
    finally:
        dialog.close()
        install_ui_language("ja")


def test_non_windows_detection_has_safe_fallback(monkeypatch):
    monkeypatch.setattr("app.i18n.sys.platform", "linux")
    assert detect_windows_ui_language() == "en"


@pytest.mark.parametrize(
    "lang_id,expected",
    [(0x0411, "ja"), (0x0804, "zh-Hans"), (0x1004, "zh-Hans"),
     (0x0404, "zh-Hant"), (0x0C04, "zh-Hant"), (0x0419, "en")],
)
def test_windows_display_language_mapping(monkeypatch, lang_id, expected):
    monkeypatch.setattr("app.i18n.sys.platform", "win32")
    monkeypatch.setattr(
        "app.i18n.ctypes.windll",
        SimpleNamespace(kernel32=SimpleNamespace(GetUserDefaultUILanguage=lambda: lang_id)),
        raising=False,
    )
    assert detect_windows_ui_language() == expected
