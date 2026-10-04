"""Independent spacing, painted title padding, config migration and real UI."""

import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import QFormLayout, QListView, QStyleOptionViewItem

from app.browser_item_delegate import BrowserItemDelegate
from app.browser_sort import BrowserDisplayDensity
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.i18n import install_ui_language
from app.settings_dialog import SettingsDialog
from test_browser_horizontal_geometry import synthetic_grid, measure
from test_sprint15_browser_integration import make_window, close_window


@pytest.mark.parametrize('display', ['hidden', 'one_line', 'two_lines'])
@pytest.mark.parametrize('x,y', [(0, 0), (7, 0), (0, 9), (7, 9), (32, 32)])
def test_independent_item_gaps_preserve_frame_title_and_pixel_size(qapp, tmp_path, display, x, y):
    original, original_delegate, original_model = synthetic_grid(
        qapp, tmp_path, BrowserDisplayDensity.MEDIUM, 760, filename_display=display,
    )
    view, delegate, model = synthetic_grid(
        qapp, tmp_path, BrowserDisplayDensity.MEDIUM, 760, filename_display=display,
        item_spacing_x=x, item_spacing_y=y,
    )
    try:
        before = measure(original, original_delegate, original_model)
        after = measure(view, delegate, model)
        assert delegate.cell_size == original_delegate.cell_size
        assert delegate.grid_metrics.grid_size == delegate.cell_size + QSize(x, y)
        assert after['item_gap'] == x
        assert after['frame_gap'] == before['frame_gap'] + x
        assert after['row_pitch'] == before['row_pitch'] + y
        assert after['vertical_frame_gap'] == before['vertical_frame_gap'] + y
        tolerance = 2 / view.devicePixelRatioF() + 1e-6
        assert abs(after['pixel_width'] - before['pixel_width']) <= tolerance
        assert abs(after['pixel_gap'] - before['pixel_gap'] - x) <= tolerance
        assert abs(after['vertical_pixel_gap'] - before['vertical_pixel_gap'] - y) <= tolerance
        rect = view.visualRect(model.index(1, 0))
        assert rect.size() == delegate.cell_size
        if x:
            assert not view.indexAt(QPoint(rect.left() - 1, rect.center().y())).isValid()
        if y:
            assert not view.indexAt(QPoint(rect.center().x(), rect.bottom() + 1)).isValid()
        assert view.horizontalScrollBar().maximum() == 0
    finally:
        original.close()
        view.close()


def _title_pixels(delegate, dpr):
    metrics = delegate.grid_metrics
    option = QStyleOptionViewItem()
    option.rect = metrics.cell_rect()
    option.palette.setColor(QPalette.ColorRole.Text, QColor('black'))
    image = QImage(round(metrics.cell_size.width() * dpr),
                   round(metrics.cell_size.height() * dpr), QImage.Format.Format_RGB32)
    image.setDevicePixelRatio(dpr)
    image.fill(QColor('white'))
    painter = QPainter(image)
    delegate._paint_title(painter, option, metrics.thumbnail_frame_rect(option.rect), 'Ag\nBy')
    painter.end()
    points = [(x, y) for y in range(image.height()) for x in range(image.width())
              if image.pixelColor(x, y) != QColor('white')]
    return min(y for x, y in points), max(y for x, y in points)


@pytest.mark.parametrize('display', ['one_line', 'two_lines'])
def test_image_gap_and_filename_padding_have_distinct_rect_and_paint_effects(qapp, display):
    def make(**options):
        return BrowserItemDelegate(thumbnail_size=149, density=BrowserDisplayDensity.MEDIUM,
                                   filename_display=display, **options)
    base, gap, padded = make(), make(filename_gap=6), make(filename_padding_y=6)
    b, g, p = [d.grid_metrics for d in (base, gap, padded)]
    assert g.title_rect(g.cell_rect()).top() == b.title_rect(b.cell_rect()).top() + 6
    assert g.title_height == b.title_height
    assert p.title_rect(p.cell_rect()).top() == b.title_rect(b.cell_rect()).top()
    assert p.title_height == b.title_height + 12
    assert p.title_text_rect(p.cell_rect()).top() == p.title_rect(p.cell_rect()).top() + 6
    assert p.title_text_rect(p.cell_rect()).bottom() == p.title_rect(p.cell_rect()).bottom() - 6
    assert g.cell_size.height() == b.cell_size.height() + 6
    assert p.cell_size.height() == b.cell_size.height() + 12
    dpr = qapp.primaryScreen().devicePixelRatio()
    base_ink = _title_pixels(base, dpr)
    for delegate in (gap, padded):
        ink = _title_pixels(delegate, dpr)
        assert abs(ink[0] - base_ink[0] - 6 * dpr) <= 1
        assert abs(ink[1] - base_ink[1] - 6 * dpr) <= 1
    for metrics in (b, g, p):
        assert metrics.thumbnail_frame_rect(metrics.cell_rect()).size() == QSize(105, 149)
    print(display, 'base/gap/padding cell heights', [m.cell_size.height() for m in (b, g, p)],
          'paint y', [_title_pixels(d, dpr) for d in (base, gap, padded)])


@pytest.mark.parametrize('legacy,expected', [
    ({}, (0, 0)),
    ({'browser_item_spacing_mode': 'preset', 'browser_item_spacing': 22}, (0, 0)),
    ({'browser_item_spacing': 22}, (0, 0)),
    ({'browser_item_spacing_mode': 'custom', 'browser_item_spacing': 7}, (7, 7)),
    ({'browser_item_spacing_mode': 'custom', 'browser_item_spacing': -1}, (0, 0)),
    ({'browser_item_spacing_mode': 'custom', 'browser_item_spacing': 99}, (32, 32)),
    ({'browser_item_spacing_mode': 'custom', 'browser_item_spacing': 'bad'}, (2, 2)),
    ({'browser_item_spacing_mode': 'custom', 'browser_item_spacing': 7,
      'browser_item_spacing_x': 3}, (3, 7)),
    ({'browser_item_spacing_mode': 'custom', 'browser_item_spacing': 7,
      'browser_item_spacing_x': 3, 'browser_item_spacing_y': 9}, (3, 9)),
    ({'browser_item_spacing_x': 99, 'browser_item_spacing_y': -7}, (32, 0)),
])
def test_spacing_migration_retains_explicit_choices_once(tmp_path, legacy, expected):
    path = tmp_path / 'synthetic-config.json'
    unrelated = {'browser_cell_padding': 3, 'browser_filename_gap': 4,
                 'browser_filename_padding_y': 5, 'browser_filename_display': 'two_lines',
                 'browser_display_density': 'large', 'thumbnail_size': 149}
    path.write_text(json.dumps({**unrelated, **legacy}), encoding='utf-8')
    config = ConfigManager(path)
    values = config.load()
    assert tuple(values[f'browser_item_spacing_{axis}'] for axis in 'xy') == expected
    assert all(values[key] == value for key, value in unrelated.items())
    assert 'browser_item_spacing_mode' not in values
    assert 'browser_item_spacing' not in values
    config.save()
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert 'browser_item_spacing' not in saved
    assert ConfigManager(path).load() == values
    # Normal Apply/save uses the same authority, with no parallel legacy state.
    config.apply({'browser_item_spacing_x': 1}, save=True)
    again = ConfigManager(path).load()
    assert again['browser_item_spacing_x'] == 1
    assert again['browser_item_spacing_y'] == expected[1]


@pytest.mark.parametrize('language', ['ja', 'en'])
def test_real_settings_exposes_distinct_bound_spacing_controls(qapp, tmp_path, monkeypatch, language):
    install_ui_language(language)
    # No executable discovery/probe is part of this layout/settings test.
    for method in ('redetect_winrar', 'redetect_seven_zip', 'redetect_ffmpeg'):
        monkeypatch.setattr(SettingsDialog, method, lambda self: None)
    config = ConfigManager(tmp_path / 'settings.json')
    config.load()
    config.apply({'browser_item_spacing_x': 3, 'browser_item_spacing_y': 5,
                  'browser_filename_gap': 4, 'browser_filename_padding_y': 2})
    dialog = SettingsDialog(config)
    try:
        dialog.show()
        dialog.tabs.setCurrentIndex(1)
        qapp.processEvents()
        scroll = dialog.tabs.currentWidget()
        controls = [dialog.browser_item_spacing_x_spin, dialog.browser_item_spacing_y_spin,
                    dialog.browser_filename_gap_spin, dialog.browser_filename_padding_y_spin]
        expected = (['項目の横間隔:', '項目の縦間隔:', '画像とファイル名の間隔:', 'ファイル名内余白（上下）:']
                    if language == 'ja' else ['Horizontal item gap:', 'Vertical item gap:',
                                              'Image-to-filename gap:', 'Filename padding (top/bottom):'])
        for control, label_text in zip(controls, expected):
            scroll.ensureWidgetVisible(control)
            qapp.processEvents()
            assert control.isVisible() and control.isEnabled()
            assert control.toolTip()
            form = control.parentWidget().layout()
            assert isinstance(form, QFormLayout)
            label = form.labelForField(control)
            assert label.text() == label_text
            assert label.width() >= label.fontMetrics().horizontalAdvance(label.text())
            assert scroll.viewport().rect().contains(control.mapTo(scroll.viewport(), control.rect().center()))
        assert [c.value() for c in controls] == [3, 5, 4, 2]
        assert not hasattr(dialog, 'browser_spacing_preset_checkbox')
        assert not hasattr(dialog, 'browser_item_spacing_spin')
        for control, value in zip(controls, [7, 9, 6, 3]):
            control.setValue(value)
        changed = dialog.apply_settings()
        for key, expected_value in [('browser_item_spacing_x', 7), ('browser_item_spacing_y', 9),
                                    ('browser_filename_gap', 6), ('browser_filename_padding_y', 3)]:
            assert changed[key] == expected_value
            assert ConfigManager(config.path).load()[key] == expected_value
        dialog.browser_filename_display_combo.setCurrentIndex(0)
        assert all(c.isEnabled() for c in controls[:2])
        assert all(not c.isEnabled() for c in controls[2:])
        dialog.browser_filename_display_combo.setCurrentIndex(2)
        assert all(c.isEnabled() for c in controls)
    finally:
        dialog.reject()
        install_ui_language('ja')
        qapp.processEvents()


def test_no_selection_gap_change_restores_anchor_offset_without_new_generation(qapp, tmp_path):
    window, store, _folder = make_window(tmp_path, qapp, count=100)
    try:
        window.list_view.clearSelection()
        window.list_view.scrollTo(window.item_model.index(55, 0), QListView.ScrollHint.PositionAtTop)
        window.list_view.verticalScrollBar().setValue(window.list_view.verticalScrollBar().value() + 17)
        qapp.processEvents()
        before = window._capture_list_view_state()
        assert not before.selected_paths
        generation = window.thumbnail_provider.generation
        history = len(window.navigation_history)
        order = tuple(window.item_model.item_at(window.item_model.index(row, 0)).path
                      for row in range(window.item_model.rowCount()))
        window.config.apply({'browser_item_spacing_x': 11, 'browser_item_spacing_y': 13})
        for _ in range(3):
            qapp.processEvents()
        after = window._capture_list_view_state()
        row = window.item_model.row_for_path(before.anchor_path)
        assert window.list_view.visualRect(window.item_model.index(row, 0)).y() == before.anchor_y
        assert not after.selected_paths
        assert window.thumbnail_provider.generation == generation
        assert len(window.navigation_history) == history
        assert tuple(window.item_model.item_at(window.item_model.index(row, 0)).path
                     for row in range(window.item_model.rowCount())) == order
    finally:
        close_window(window, store, qapp)


def test_settings_apply_updates_existing_browser_without_thumbnail_invalidation(qapp, tmp_path, monkeypatch):
    for method in ('redetect_winrar', 'redetect_seven_zip', 'redetect_ffmpeg'):
        monkeypatch.setattr(SettingsDialog, method, lambda self: None)
    window, store, _folder = make_window(tmp_path, qapp, count=3)
    dialog = SettingsDialog(window.config)
    try:
        window._thumbnail_request_timer.stop()
        assert window.thumbnail_provider.wait_for_done(2000)
        qapp.processEvents()
        window._thumbnail_request_timer.stop()
        before = window.item_delegate.grid_metrics
        generation = window.thumbnail_provider.generation
        token = window.thumbnail_render_spec.cache_token
        def unexpected(*args, **kwargs):
            pytest.fail('spacing must not invalidate thumbnails or rescan the directory')
        monkeypatch.setattr(window.item_model, 'clear_thumbnails', unexpected)
        monkeypatch.setattr(window.thumbnail_provider, 'begin_generation', unexpected)
        monkeypatch.setattr(window, 'navigate_to', unexpected)
        dialog.browser_item_spacing_x_spin.setValue(7)
        dialog.browser_item_spacing_y_spin.setValue(9)
        dialog.apply_settings()
        # Existing ConfigManager signal updates the live Browser synchronously.
        assert window.list_view.gridSize() == before.cell_size + QSize(7, 9)
        assert window.item_delegate.cell_size == before.cell_size
        assert window.thumbnail_provider.generation == generation
        assert window.thumbnail_render_spec.cache_token == token
        assert ConfigManager(window.config.path).load()['browser_item_spacing_y'] == 9
    finally:
        dialog.reject()
        close_window(window, store, qapp)


@pytest.mark.parametrize('x', [0, 7, 32])
def test_exact_wrap_boundary_keeps_actual_visible_rows_at_visible_priority(qapp, tmp_path, x):
    view, delegate, model = synthetic_grid(qapp, tmp_path, BrowserDisplayDensity.MEDIUM, 760,
                                         item_spacing_x=x, item_spacing_y=9)
    try:
        # Keep the vertical scrollbar visible and choose an exact pitch multiple.
        chrome = view.width() - view.viewport().width()
        view.resize(delegate.grid_metrics.grid_size.width() * 4 + chrome, 420)
        view.doItemsLayout()
        qapp.processEvents()
        assert view.viewport().width() == view.gridSize().width() * 4
        assert measure(view, delegate, model)['columns'] == 3
        view.verticalScrollBar().setValue(view.gridSize().height() * 5 + 7)
        qapp.processEvents()
        first, last = BrowserWindow._visible_row_range(SimpleNamespace(list_view=view, item_model=model))
        actual_visible = [row for row in range(model.rowCount())
                          if view.visualRect(model.index(row, 0)).intersects(view.viewport().rect())]
        assert actual_visible
        assert all(first <= row <= last for row in actual_visible)
    finally:
        view.close()
