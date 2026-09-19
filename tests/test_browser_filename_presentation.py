from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QFormLayout, QStyleOptionViewItem

from app.browser_grid_metrics import FILENAME_DISPLAY_LINES
from app.browser_item_delegate import (
    BrowserItemDelegate,
    elide_filename_line,
    elided_title_lines,
    filename_for_display,
)
from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_sort import BrowserDisplayDensity
from app.config_manager import ConfigManager
from app.i18n import install_ui_language
from app.settings_dialog import SettingsDialog
from test_browser_spacing import _title_pixels
from test_sprint15_browser_integration import close_window, make_window


class TextCapturePainter:
    def __init__(self):
        self.drawn_text: list[str] = []

    def setFont(self, _font):
        pass

    def setPen(self, _pen):
        pass

    def drawText(self, _rect, _flags, text):
        self.drawn_text.append(text)


def paint_filename(
    delegate,
    text: str,
    *,
    is_folder: bool = False,
    width: int | None = None,
) -> str:
    option = QStyleOptionViewItem()
    option.rect = delegate.grid_metrics.cell_rect()
    if width is not None:
        option.rect.setWidth(width)
    painter = TextCapturePainter()
    delegate._paint_title(
        painter,
        option,
        delegate.grid_metrics.thumbnail_frame_rect(option.rect),
        text,
        is_folder=is_folder,
    )
    return "\n".join(painter.drawn_text)


@pytest.mark.parametrize("density", list(BrowserDisplayDensity))
def test_density_default_font_and_explicit_title_geometry(qapp, density):
    automatic = BrowserItemDelegate(
        thumbnail_size=149,
        density=density,
        filename_display="two_lines",
        filename_padding_y=2,
    )
    explicit = BrowserItemDelegate(
        thumbnail_size=149,
        density=density,
        filename_display="two_lines",
        filename_padding_y=2,
        filename_font_size=16,
    )
    old = automatic.grid_metrics
    large = explicit.grid_metrics
    assert automatic.effective_filename_font_size == automatic.profile.font_size
    assert large.font_height == QFontMetrics(QFont("", 16)).height()
    assert large.title_height == 2 * large.font_height + 4
    assert large.cell_size.height() > old.cell_size.height()
    assert large.cell_size.width() == old.cell_size.width()
    assert large.frame_size == old.frame_size == QSize(105, 149)


@pytest.mark.parametrize("display", ["one_line", "two_lines"])
def test_new_tail_elision_is_default_and_middle_mode_remains_available(
    qapp, display
):
    name = "日本語の長い漫画タイトルの続きと末尾の識別子_📚🧪.cbz"
    leading = BrowserItemDelegate(
        thumbnail_size=149,
        density=BrowserDisplayDensity.MEDIUM,
        filename_display=display,
    )
    centered = BrowserItemDelegate(
        thumbnail_size=149,
        density=BrowserDisplayDensity.MEDIUM,
        filename_display=display,
        filename_elide_mode="middle",
    )
    leading_text = paint_filename(leading, name)
    centered_text = paint_filename(centered, name)
    assert leading.filename_elide_mode == "right"
    assert centered.filename_elide_mode == "middle"
    if display == "one_line":
        assert "…" in leading_text
        assert leading_text.endswith(".cbz")
        assert leading_text.startswith(name[:3])
        assert centered_text.endswith(".cbz")
        assert centered_text.startswith(name[:3])
    else:
        # First line wrapping stays unchanged; the selected policy applies to
        # the final line only when that line itself needs elision.
        assert leading_text.count("\n") == 1
        assert centered_text.count("\n") == 1
        assert leading_text.splitlines()[0] == centered_text.splitlines()[0]
        assert "…" in leading_text.splitlines()[-1]
        assert leading_text.splitlines()[-1].endswith(".cbz")
        assert centered_text.splitlines()[-1].endswith(".cbz")


@pytest.mark.parametrize("suffix", [".zip", ".cbz", ".tar.gz", ".CBZ"])
@pytest.mark.parametrize("display", ["one_line", "two_lines"])
def test_leading_elision_keeps_a_normal_file_extension_when_it_fits(
    qapp, suffix, display
):
    name = "日本語の長い漫画タイトルと識別用の追加文字列_📚" + suffix
    delegate = BrowserItemDelegate(
        thumbnail_size=149,
        density=BrowserDisplayDensity.MEDIUM,
        filename_display=display,
    )
    painted = paint_filename(delegate, name)
    final_line = painted.splitlines()[-1]
    assert "…" in painted
    assert final_line.endswith(suffix)
    assert painted.splitlines()[0].startswith(name[:1])


@pytest.mark.parametrize("mode", ["right", "middle"])
@pytest.mark.parametrize("display", ["one_line", "two_lines"])
def test_hidden_extension_is_removed_before_either_elision_mode(qapp, mode, display):
    name = "LongJapanese漫画Title_識別子.cbz"
    visible = BrowserItemDelegate(
        thumbnail_size=149,
        density=BrowserDisplayDensity.MEDIUM,
        filename_display=display,
        filename_elide_mode=mode,
    )
    hidden = BrowserItemDelegate(
        thumbnail_size=149,
        density=BrowserDisplayDensity.MEDIUM,
        filename_display=display,
        filename_elide_mode=mode,
        show_filename_extension=False,
    )
    visible_text = paint_filename(visible, name)
    hidden_text = paint_filename(hidden, name)
    assert visible_text.splitlines()[-1].endswith(".cbz")
    assert ".cbz" not in hidden_text
    if mode == "middle":
        assert hidden_text.splitlines()[-1][-1] == "子"


@pytest.mark.parametrize(
    "name, expected",
    [
        ("日本語の本.zip", "日本語の本"),
        ("manga.CBZ", "manga"),
        ("bundle.tar.gz", "bundle"),
        (".env", ".env"),
        ("README", "README"),
        ("dot.name.", "dot.name."),
    ],
)
def test_extension_visibility_handles_compound_extensionless_and_dot_names(
    name, expected
):
    assert filename_for_display(name, show_extension=False) == expected
    assert filename_for_display(name, show_extension=True) == name


def test_extension_visibility_leaves_folder_names_and_type_icons_untouched(qapp):
    delegate = BrowserItemDelegate(
        thumbnail_size=149,
        density=BrowserDisplayDensity.MEDIUM,
        show_filename_extension=False,
    )
    assert filename_for_display(
        "Season.01", show_extension=False, is_folder=True
    ) == "Season.01"
    assert paint_filename(delegate, "A.zip", is_folder=True) == "A.zip"
    assert paint_filename(delegate, "A.zip") == "A"


def test_narrow_width_and_oversized_extension_have_a_nonempty_fallback(qapp):
    metrics = QFontMetrics(QFont("", 16))
    name = "longname." + ("x" * 256)
    for width in (0, 1, 5, 16, 32):
        result = elide_filename_line(
            metrics,
            name,
            width,
            "right",
            preserve_extension=True,
        )
        assert result
        assert result.endswith("…")
    lines = elided_title_lines(metrics, name, 1, 2, preserve_extension=True)
    assert lines and all(lines)


def test_font_metrics_resize_visible_title_ink_and_leave_thumbnail_unchanged(qapp):
    small = BrowserItemDelegate(
        thumbnail_size=149,
        density=BrowserDisplayDensity.MEDIUM,
        filename_display="one_line",
    )
    large = BrowserItemDelegate(
        thumbnail_size=149,
        density=BrowserDisplayDensity.MEDIUM,
        filename_display="one_line",
        filename_font_size=16,
    )
    small_ink = _title_pixels(small, 1.0)
    large_ink = _title_pixels(large, 1.0)
    assert large_ink[1] - large_ink[0] > small_ink[1] - small_ink[0]
    assert large.grid_metrics.title_height > small.grid_metrics.title_height
    assert large.grid_metrics.frame_size == small.grid_metrics.frame_size
    assert large.grid_metrics.thumbnail_frame_rect(
        large.grid_metrics.cell_rect()
    ).size() == QSize(105, 149)


@pytest.mark.parametrize(
    "provided, expected_elide, expected_size, expected_extension",
    [
        ({}, "right", 0, True),
        (
            {
                "browser_filename_elide_mode": "middle",
                "browser_filename_font_size": 18,
            },
            "middle",
            18,
            True,
        ),
        (
            {
                "browser_filename_elide_mode": "invalid",
                "browser_filename_font_size": "bad",
                "browser_filename_show_extension": "false",
            },
            "right",
            0,
            True,
        ),
        ({"browser_filename_elide_mode": []}, "right", 0, True),
        ({"browser_filename_font_size": 3}, "right", 0, True),
        ({"browser_filename_font_size": 99}, "right", 24, True),
        ({"browser_filename_show_extension": False}, "right", 0, False),
    ],
)
def test_config_defaults_bounds_and_roundtrip(
    tmp_path, provided, expected_elide, expected_size, expected_extension
):
    path = tmp_path / "synthetic-browser-settings.json"
    path.write_text(json.dumps(provided), encoding="utf-8")
    config = ConfigManager(path)
    values = config.load()
    assert values["browser_filename_elide_mode"] == expected_elide
    assert values["browser_filename_font_size"] == expected_size
    assert values["browser_filename_show_extension"] is expected_extension
    config.save()
    assert ConfigManager(path).load() == values


@pytest.mark.parametrize("language", ["ja", "en"])
def test_settings_controls_apply_and_hide_with_filenames(
    qapp, tmp_path, monkeypatch, language
):
    install_ui_language(language)
    for method in ("redetect_winrar", "redetect_seven_zip", "redetect_ffmpeg"):
        monkeypatch.setattr(SettingsDialog, method, lambda self: None)
    config = ConfigManager(tmp_path / "settings.json")
    config.load()
    dialog = SettingsDialog(config)
    try:
        dialog.tabs.setCurrentIndex(1)
        controls = (
            dialog.browser_filename_elide_combo,
            dialog.browser_filename_font_size_combo,
        )
        labels = (
            ["長いファイル名:", "ファイル名フォントサイズ:"]
            if language == "ja"
            else ["Long filename elision:", "Filename font size:"]
        )
        extension_label = (
            "ファイル名の拡張子を表示"
            if language == "ja"
            else "Show filename extensions"
        )
        middle_label = "中央を省略" if language == "ja" else "Elide the middle"
        assert dialog.browser_filename_extension_checkbox.text() == extension_label
        assert dialog.browser_filename_extension_checkbox.toolTip()
        assert dialog.browser_filename_extension_checkbox.isChecked()
        assert dialog.browser_filename_elide_combo.itemText(
            dialog.browser_filename_elide_combo.findData("middle")
        ) == middle_label
        for control, expected_label in zip(controls, labels):
            label = control.parentWidget().layout().labelForField(control)
            assert isinstance(control.parentWidget().layout(), QFormLayout)
            assert label.text() == expected_label
            assert control.toolTip()
            assert control.isEnabled()
        assert dialog.browser_filename_font_size_combo.currentData() == 0
        dialog.browser_filename_elide_combo.setCurrentIndex(1)
        dialog.browser_filename_font_size_combo.setCurrentIndex(
            dialog.browser_filename_font_size_combo.findData(16)
        )
        dialog.browser_filename_extension_checkbox.setChecked(False)
        changed = dialog.apply_settings()
        assert changed["browser_filename_elide_mode"] == "middle"
        assert changed["browser_filename_font_size"] == 16
        assert changed["browser_filename_show_extension"] is False
        assert ConfigManager(config.path).load()["browser_filename_font_size"] == 16
        assert (
            ConfigManager(config.path).load()["browser_filename_show_extension"]
            is False
        )
        dialog.browser_filename_display_combo.setCurrentIndex(0)
        assert all(not control.isEnabled() for control in controls)
        assert not dialog.browser_filename_extension_checkbox.isEnabled()
        dialog.browser_filename_display_combo.setCurrentIndex(2)
        assert all(control.isEnabled() for control in controls)
        assert dialog.browser_filename_extension_checkbox.isEnabled()
    finally:
        dialog.reject()
        install_ui_language("ja")
        qapp.processEvents()


def test_live_presentation_apply_keeps_selection_cache_and_scan_generation(
    qapp, tmp_path, monkeypatch
):
    window, store, _folder = make_window(tmp_path, qapp, count=6)
    dialog = SettingsDialog(window.config)
    try:
        window._thumbnail_request_timer.stop()
        assert window.thumbnail_provider.wait_for_done(2000)
        qapp.processEvents()
        window._thumbnail_request_timer.stop()
        current = window.item_model.index(2, 0)
        window.list_view.selectionModel().select(
            current,
            window.list_view.selectionModel().SelectionFlag.ClearAndSelect,
        )
        window.list_view.setCurrentIndex(current)
        qapp.processEvents()
        selected = window.item_model.item_at(current).path
        image_before = window.item_model.data(
            current, window.item_model.ThumbnailImageRole
        )
        frame_before = window.item_delegate.grid_metrics.frame_size
        generation = window.thumbnail_provider.generation
        source_items = window.item_model.source_items

        def unexpected(*_args, **_kwargs):
            pytest.fail("filename presentation must not rescan or invalidate thumbnails")

        monkeypatch.setattr(window.scanner, "start", unexpected)
        monkeypatch.setattr(window, "navigate_to", unexpected)
        monkeypatch.setattr(window.item_model, "clear_thumbnails", unexpected)
        monkeypatch.setattr(window.thumbnail_provider, "begin_generation", unexpected)
        dialog.browser_filename_font_size_combo.setCurrentIndex(
            dialog.browser_filename_font_size_combo.findData(16)
        )
        dialog.apply_settings()
        qapp.processEvents()
        selected_index = window.item_model.index(
            window.item_model.row_for_path(selected), 0
        )
        assert window.list_view.currentIndex() == selected_index
        assert window.item_model.source_items == source_items
        assert window.item_model.data(selected_index).splitlines()[0].endswith(
            ".jpg"
        )
        assert window.thumbnail_provider.generation == generation
        assert window.item_model.data(
            selected_index, window.item_model.ThumbnailImageRole
        ).cacheKey() == image_before.cacheKey()

        scroll_before_extension_change = window.list_view.verticalScrollBar().value()
        grid_size_before_extension_change = window.item_delegate.grid_metrics.grid_size
        window._thumbnail_request_timer.stop()
        monkeypatch.setattr(window, "_schedule_thumbnail_requests", unexpected)
        dialog.browser_filename_extension_checkbox.setChecked(False)
        changed = dialog.apply_settings()
        qapp.processEvents()
        assert changed["browser_filename_show_extension"] is False
        assert window.browser_filename_show_extension is False
        assert window.item_delegate.show_filename_extension is False
        assert window.list_view.currentIndex() == selected_index
        assert (
            window.list_view.verticalScrollBar().value()
            == scroll_before_extension_change
        )
        assert (
            window.item_delegate.grid_metrics.grid_size
            == grid_size_before_extension_change
        )
        assert window.item_model.source_items == source_items
        assert window.item_model.data(selected_index).splitlines()[0].endswith(
            ".jpg"
        )
        assert window.thumbnail_provider.generation == generation
        assert window.item_model.data(
            selected_index, window.item_model.ThumbnailImageRole
        ).cacheKey() == image_before.cacheKey()
        assert window.item_delegate.grid_metrics.frame_size == frame_before
        assert window.item_delegate.grid_metrics.title_height > 0
        assert window.item_delegate.grid_metrics.font_height == QFontMetrics(
            QFont("", 16)
        ).height()

        middle_size = window.item_delegate.grid_metrics.cell_size
        middle_title_height = window.item_delegate.grid_metrics.title_height
        dialog.browser_filename_elide_combo.setCurrentIndex(
            dialog.browser_filename_elide_combo.findData("middle")
        )
        changed = dialog.apply_settings()
        qapp.processEvents()
        assert changed["browser_filename_elide_mode"] == "middle"
        assert window.item_delegate.filename_elide_mode == "middle"
        assert window.item_delegate.grid_metrics.cell_size == middle_size
        assert window.item_delegate.grid_metrics.title_height == middle_title_height
        assert window.item_model.source_items == source_items
        assert window.thumbnail_provider.generation == generation
        assert window.item_model.data(
            selected_index, window.item_model.ThumbnailImageRole
        ).cacheKey() == image_before.cacheKey()
    finally:
        dialog.reject()
        close_window(window, store, qapp)
        install_ui_language("ja")
