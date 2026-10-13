import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from app.browser_filter import BrowserFilterState, RatingFilterMode
from app.config_manager import ConfigManager
from app.browser_location_bar import LocationPopupEntry
from app.settings_dialog import SettingsDialog
from app.zippla_filename_metadata import ZipPlaFilenameMetadata
from tests.test_browser_interaction_regressions import send_wheel
from tests.test_browser_navigation_window import make_window, write_image


@pytest.mark.parametrize('surface', ['edit', 'shell'])
def test_newest_endpoint_clears_only_search(tmp_path, qapp, surface):
    folder = tmp_path / 'books'
    for name in ('alpha', 'beta', 'gamma'):
        path = ZipPlaFilenameMetadata.parse(folder / f'{name}.png').with_rating(3).with_tag_changes({'keep': True}).serialized_path()
        write_image(path)
    window = make_window(tmp_path, folder, qapp)
    try:
        window.config.apply({'browser_search_history': ['alpha', 'beta', 'gamma']})
        window._set_browser_filter(BrowserFilterState.normalized(rating_mode=RatingFilterMode.AT_LEAST,
                                    rating_reference=3, include_tags=('keep',)))
        target = window.browser_search_edit if surface == 'edit' else window.browser_search_container
        for angle, expected in ((120, ''), (-120, 'alpha'), (-120, 'beta'), (-120, 'gamma'),
                                (-120, 'gamma'), (120, 'beta'), (120, 'alpha'), (120, ''), (120, '')):
            send_wheel(qapp, target, angle=angle)
            assert window.active_search_query == expected
        assert set(window.search_history.entries) == {'alpha', 'beta', 'gamma'}
        assert window.item_model.rowCount() == 3
        assert window.browser_filter_state.include_tags == ('keep',)
        assert window.browser_filter_state.rating_reference == 3
        assert not window._browser_search_timer.isActive()
        QTest.qWait(150)
        assert window.active_search_query == ''
        send_wheel(qapp, target, angle=-120)
        assert window.active_search_query == 'alpha'
    finally:
        window.close(); qapp.processEvents()


@pytest.mark.parametrize('pixel', [False, True])
@pytest.mark.parametrize('shift_enabled', [False, True])
def test_precision_phases_popup_and_shift_option(tmp_path, qapp, pixel, shift_enabled):
    folder = tmp_path / 'books'
    write_image(folder / 'alpha.png')
    window = make_window(tmp_path, folder, qapp)
    try:
        window.config.apply({'browser_search_history': ['alpha', 'beta', 'gamma'],
                             'browser_search_wheel_shift_oldest': shift_enabled})
        target = window.browser_search_edit
        target.setText('draft')
        half = {'pixel': -20} if pixel else {'angle': -60}
        send_wheel(qapp, target, **half)
        send_wheel(qapp, target, phase=Qt.ScrollPhase.ScrollBegin)
        send_wheel(qapp, target, **half)
        assert target.text() == 'draft'
        send_wheel(qapp, target, phase=Qt.ScrollPhase.ScrollEnd)
        send_wheel(qapp, target, **half)
        assert target.text() == 'draft'
        send_wheel(qapp, target, **half)
        assert window.active_search_query == 'alpha'
        popup = window._show_search_history_popup()
        point = window.browser_search_container.mapToGlobal(QPoint(8, 8))
        shift_half = {'pixel': 20} if pixel else {'angle': 60}
        for _ in range(2):
            send_wheel(qapp, popup.list_widget.viewport(), global_point=point,
                       modifiers=Qt.KeyboardModifier.ShiftModifier, **shift_half)
        assert window.active_search_query == ('gamma' if shift_enabled else '')
        assert not popup.isVisible()
        assert 'draft' not in window.search_history.entries
    finally:
        window.close(); qapp.processEvents()


@pytest.mark.parametrize('reverse', [False, True])
def test_oldest_first_both_directions(tmp_path, qapp, reverse):
    folder = tmp_path / 'books'
    write_image(folder / 'alpha.png')
    window = make_window(tmp_path, folder, qapp)
    try:
        window.config.apply({'browser_search_history': ['alpha', 'beta', 'gamma'],
                             'browser_search_wheel_oldest_first': True,
                             'browser_search_wheel_reverse_oldest': reverse})
        target = window.browser_search_edit
        send_wheel(qapp, target, angle=1200)
        assert window.active_search_query == ''
        send_wheel(qapp, target, angle=-1200)
        assert window.active_search_query == 'gamma'
        toward_newer = -120 if reverse else 120
        for expected in ('beta', 'alpha', ''):
            send_wheel(qapp, target, angle=toward_newer)
            assert window.active_search_query == expected
        send_wheel(qapp, target, angle=-120)
        assert window.active_search_query == 'gamma'
        send_wheel(qapp, target, angle=-toward_newer)
        assert window.active_search_query == 'gamma'
        assert set(window.search_history.entries) == {'alpha', 'beta', 'gamma'}
    finally:
        window.close(); qapp.processEvents()


def test_live_options_manual_draft_and_history_limit(tmp_path, qapp):
    folder = tmp_path / 'books'
    write_image(folder / 'alpha.png')
    window = make_window(tmp_path, folder, qapp)
    try:
        target = window.browser_search_edit
        window.config.apply({'browser_search_history': ['alpha', 'beta', 'gamma']})
        send_wheel(qapp, target, angle=-360)
        assert window.active_search_query == 'gamma'
        send_wheel(qapp, target, angle=1200)
        assert window.active_search_query == ''
        target.setText('edited')
        send_wheel(qapp, target, angle=120)
        assert target.text() == 'edited'
        window.config.apply({'browser_search_wheel_enabled': False,
                             'browser_search_wheel_shift_oldest': True})
        send_wheel(qapp, target, angle=-1200)
        send_wheel(qapp, target, angle=120, modifiers=Qt.KeyboardModifier.ShiftModifier)
        assert target.text() == 'edited'
        window.config.apply({'browser_search_wheel_enabled': True,
                             'browser_search_history': ['new', 'last']})
        send_wheel(qapp, target, angle=120, modifiers=Qt.KeyboardModifier.ShiftModifier)
        assert window.active_search_query == 'last'
        window.config.apply({'browser_search_history_limit': 0})
        send_wheel(qapp, target, angle=1200)
        assert window.active_search_query == 'last'
        assert window.search_history.entries == ()
    finally:
        window.close(); qapp.processEvents()


def test_defaults_save_cancel_reset(tmp_path, qapp):
    path = tmp_path / 'old.json'
    path.write_text(json.dumps({'browser_search_history': ['oldest']}), encoding='utf-8')
    config = ConfigManager(path)
    config.load()
    defaults = {'browser_search_wheel_enabled': True, 'browser_search_wheel_shift_oldest': False,
                'browser_search_wheel_oldest_first': False, 'browser_search_wheel_reverse_oldest': False}
    for key, default in defaults.items():
        assert config.get(key) is default
        for value in (None, 0, 1, 'false', []):
            config.apply({key: value})
            assert config.get(key) is default
    dialog = SettingsDialog(config)
    try:
        assert not dialog.browser_search_wheel_reverse_oldest_checkbox.isEnabled()
        for key, default in defaults.items():
            getattr(dialog, key + '_checkbox').setChecked(not default)
        assert dialog.browser_search_wheel_reverse_oldest_checkbox.isEnabled()
        dialog.apply_settings()
        saved = ConfigManager(path).load()
        for key, default in defaults.items():
            assert saved[key] is not default
        dialog._reset_browser_scope()
        for key, default in defaults.items():
            assert getattr(dialog, key + '_checkbox').isChecked() is default
        assert not dialog.browser_search_wheel_reverse_oldest_checkbox.isEnabled()
        dialog.reject()
        assert config.get('browser_search_wheel_enabled') is False
        assert config.get('viewer_prefetch_forward_screens') == 5
        assert config.get('viewer_spread_parallel_trial')
        assert config.get('viewer_current_extra_trial')
    finally:
        dialog.close(); qapp.processEvents()


@pytest.mark.parametrize('sort_key,order', [('name', 'ascending'), ('name', 'descending'),
                                          ('random', 'ascending')])
def test_wheel_preserves_history_order_and_restores_original_list_view(tmp_path, qapp, sort_key, order):
    folder = tmp_path / 'books'
    for i in range(60):
        write_image(folder / f'page{i:02}.png')
    window = make_window(tmp_path, folder, qapp)
    try:
        window.item_model.configure_sort(sort_key, order, True, random_seed=57)
        qapp.processEvents()
        selected = window.item_model.index(35, 0)
        window.list_view.setCurrentIndex(selected)
        window.list_view.scrollTo(selected)
        qapp.processEvents()
        original_order = tuple(item.path for item in window.item_model.items)
        original = window._capture_list_view_state()
        window.config.apply({'browser_search_history': ['page00', 'page01', 'page02']})
        target = window.browser_search_edit
        for expected in ('page00', 'page01', 'page02'):
            send_wheel(qapp, target, angle=-120)
            qapp.processEvents()
            assert target.selectedText() == expected
            assert target.hasFocus()
            assert not window._commit_browser_search_history()
            assert window.search_history.entries == ('page00', 'page01', 'page02')
            assert window.config.get('browser_search_history') == ['page00', 'page01', 'page02']
        popup = window._show_search_history_popup()
        assert popup is not None
        assert window.search_history.entries == ('page00', 'page01', 'page02')
        popup.close()
        send_wheel(qapp, target, angle=360)
        qapp.processEvents()
        assert window.active_search_query == ''
        assert tuple(item.path for item in window.item_model.items) == original_order
        restored = window._capture_list_view_state()
        assert restored.selected_paths == original.selected_paths
        assert restored.current_path == original.current_path
        assert restored.anchor_path == original.anchor_path
        assert restored.anchor_y == original.anchor_y
        assert restored.vertical_scroll == original.vertical_scroll
    finally:
        window.close(); qapp.processEvents()


def test_whole_query_can_be_deleted_and_manual_search_still_records(tmp_path, qapp):
    folder = tmp_path / 'books'
    write_image(folder / 'alpha.png')
    window = make_window(tmp_path, folder, qapp)
    try:
        window.config.apply({'browser_search_history': ['alpha', 'beta']})
        target = window.browser_search_edit
        send_wheel(qapp, target, angle=-240)
        assert target.selectedText() == 'beta'
        assert window.search_history.entries == ('alpha', 'beta')
        QTest.keyClick(target, Qt.Key.Key_Backspace)
        QTest.qWait(150)
        assert target.text() == ''
        assert window.active_search_query == ''
        assert window.search_history.entries == ('alpha', 'beta')
        target.setText('manual')
        window._commit_browser_search_history()
        assert window.search_history.entries == ('manual', 'alpha', 'beta')
        window._activate_search_history_entry(LocationPopupEntry('beta', 'beta', 'beta'))
        assert window.search_history.entries == ('beta', 'manual', 'alpha')
    finally:
        window.close(); qapp.processEvents()


def test_explicit_clear_and_book_switch_discard_old_view_restore(tmp_path, qapp):
    folder = tmp_path / 'books'
    other = tmp_path / 'other'
    write_image(folder / 'alpha.png')
    write_image(folder / 'beta.png')
    write_image(other / 'other.png')
    window = make_window(tmp_path, folder, qapp)
    try:
        window.config.apply({'browser_search_history': ['alpha', 'beta']})
        target = window.browser_search_edit
        send_wheel(qapp, target, angle=-240)
        assert window._search_wheel_view_state is not None
        window.clear_browser_filters()
        assert window._search_wheel_view_state is None
        send_wheel(qapp, target, angle=-120)
        assert window.active_search_query == 'alpha'
        window.navigate_to(other)
        assert window.wait_for_scan()
        qapp.processEvents()
        send_wheel(qapp, target, angle=-120)
        send_wheel(qapp, target, angle=120)
        qapp.processEvents()
        assert [item.path.name for item in window.item_model.items] == ['other.png']
        assert not window._capture_list_view_state().selected_paths
    finally:
        window.close(); qapp.processEvents()
