from types import SimpleNamespace
from unittest.mock import Mock
from threading import get_ident

import pytest

from app.adjacent_book_search import AdjacentBookBrowserSnapshot, AdjacentBookSnapshotEntry
from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from tests.test_application_controller import (
    make_controller, write_image, write_archive, finish_viewer_open, close_controller,
)


@pytest.fixture
def shelf(tmp_path, qapp):
    root = tmp_path / 'shelf'
    for name in ('a.png', 'c.png', 'b/1.png', 'd/1.png', 'b/child/1.png'):
        write_image(root / name)
    (root / 'f').mkdir()
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'mouse_side_buttons_folder_navigation': True,
                             'browser_folders_first': False, 'browser_sort_key': 'name',
                             'browser_sort_order': 'ascending'})
    browser = controller.create_browser_window()
    browser.set_current_folder(root)
    assert browser.wait_for_scan()
    try:
        yield controller, browser, root
    finally:
        close_controller(controller, qapp)


def open_from(controller, browser, path, qapp):
    viewer = controller.open_path(path, browser_snapshot=browser.adjacent_book_snapshot(path.parent))
    finish_viewer_open(qapp, viewer)
    return viewer


@pytest.mark.parametrize('button, expected', [('forward', 'd'), ('back', 'b')])
def test_displayed_image_is_initial_folder_navigation_anchor(shelf, qapp, button, expected):
    from tests.test_viewer_browser_page_sync import settle_page
    controller, browser, root = shelf
    controller.config.apply({'view_mode': 'single'})
    viewer = open_from(controller, browser, root / 'a.png', qapp)
    settle_page(qapp, viewer, root / 'a.png')
    viewer.next_page()
    settle_page(qapp, viewer, root / 'c.png')
    viewer._on_extra_mouse_button(button)
    finish_viewer_open(qapp, viewer)
    assert viewer.book_session.current_path == root / expected


def test_direct_folder_keeps_parent_anchor_after_child_paint(shelf, qapp):
    from tests.test_viewer_browser_page_sync import settle_page
    controller, browser, root = shelf
    viewer = open_from(controller, browser, root / 'b', qapp)
    settle_page(qapp, viewer, root / 'b' / '1.png')
    viewer._on_extra_mouse_button('forward')
    finish_viewer_open(qapp, viewer)
    assert viewer.book_session.current_path == root / 'd'
    settle_page(qapp, viewer, root / 'd' / '1.png')
    viewer._on_extra_mouse_button('back')
    finish_viewer_open(qapp, viewer)
    assert viewer.book_session.current_path == root / 'b'


def test_unpainted_page_target_does_not_replace_committed_anchor(shelf, qapp):
    from tests.test_viewer_browser_page_sync import settle_page
    controller, browser, root = shelf
    controller.config.apply({'view_mode': 'single'})
    viewer = open_from(controller, browser, root / 'a.png', qapp)
    settle_page(qapp, viewer, root / 'a.png')
    # Move the requested model position without publishing a new display.
    viewer.book_session.model.go_to_index(1)
    assert viewer.displayed_browser_path == str(root / 'a.png')
    viewer._on_extra_mouse_button('forward')
    finish_viewer_open(qapp, viewer)
    assert viewer.book_session.current_path == root / 'b'


def test_mixed_list_skips_files_keeps_parent_anchor_and_empty_fallback(shelf, qapp, monkeypatch):
    controller, browser, root = shelf
    viewer = open_from(controller, browser, root / 'a.png', qapp)
    threads = []
    factory = viewer.book_session._source_factory
    def record_factory(*args, **kwargs):
        threads.append(get_ident())
        return factory(*args, **kwargs)
    monkeypatch.setattr(viewer.book_session, '_source_factory', record_factory)
    for button, expected in [('forward', 'b'), ('forward', 'd'), ('back', 'b'), ('forward', 'd')]:
        viewer._on_extra_mouse_button(button)
        finish_viewer_open(qapp, viewer)
        assert viewer.book_session.current_path == root / expected
    assert threads and all(thread != get_ident() for thread in threads)
    viewer._on_extra_mouse_button('forward')
    assert viewer.book_session.wait_for_async()
    qapp.processEvents()
    assert browser.wait_for_scan()
    assert viewer.book_session.current_path == root / 'd'
    assert browser.current_path == root / 'f'
    viewer._on_extra_mouse_button('back')
    finish_viewer_open(qapp, viewer)
    assert viewer.book_session.current_path == root / 'd'
    assert controller._side_folder_contexts[id(viewer)][0] == str(root / 'd')
    viewer._on_extra_mouse_button('back')
    finish_viewer_open(qapp, viewer)
    assert viewer.book_session.current_path == root / 'b'


@pytest.mark.parametrize('order', [('a.png', 'b', 'c.png', 'd'), ('d', 'c.png', 'b', 'a.png'),
                                  ('c.png', 'd', 'a.png', 'b')])
def test_snapshot_folder_neighbors_preserve_arbitrary_browser_order(tmp_path, order):
    entries = tuple(AdjacentBookSnapshotEntry(str(tmp_path / name),
        'image' if '.' in name else 'folder', '.png' if '.' in name else '', name) for name in order)
    snapshot = AdjacentBookBrowserSnapshot(str(tmp_path), 1, entries, sort_identity='random:captured')
    for i, name in enumerate(order):
        for direction in (-1, 1):
            remaining = order[i + 1:] if direction > 0 else tuple(reversed(order[:i]))
            expected = next((value for value in remaining if '.' not in value), None)
            status, candidate = snapshot.adjacent_folder_path(tmp_path / name, direction)
            assert candidate == (str(tmp_path / expected) if expected else None)
            assert status.value == ('found' if expected else 'boundary')


def test_disabled_archive_custom_and_keyboard_routes_unchanged(shelf, qapp, monkeypatch):
    controller, browser, root = shelf
    viewer = open_from(controller, browser, root / 'a.png', qapp)
    normal = Mock()
    monkeypatch.setattr(viewer, '_open_adjacent_book', normal)
    controller.config.apply({'mouse_side_buttons_folder_navigation': False})
    viewer._on_extra_mouse_button('forward')
    normal.assert_called_once_with(1, require_browser_snapshot=True)
    controller.config.apply({'mouse_side_buttons_folder_navigation': True})
    normal.reset_mock()
    viewer.open_next_book()
    normal.assert_called_once_with(1)
    from app import viewer_commands as commands
    dispatch = Mock()
    monkeypatch.setattr(viewer, 'dispatch_command', dispatch)
    controller.config.apply({'mouse_forward_button_action': commands.NEXT_PAGE})
    viewer._on_extra_mouse_button('forward')
    dispatch.assert_called_once_with(commands.NEXT_PAGE)
    archive = root / 'book.cbz'
    write_archive(archive)
    viewer.open_path(archive)
    finish_viewer_open(qapp, viewer)
    normal.reset_mock()
    viewer._on_extra_mouse_button('back')
    normal.assert_called_once_with(-1, require_browser_snapshot=True)


def test_stale_empty_result_does_not_navigate(shelf, qapp):
    controller, browser, root = shelf
    viewer = open_from(controller, browser, root / 'a.png', qapp)
    viewer._on_extra_mouse_button('forward')
    pending = controller._side_folder_pending[id(viewer)]
    controller._finish_side_folder(viewer, SimpleNamespace(generation=pending[0] - 1,
        cancelled=False, code='no_images'), success=False)
    assert browser.current_path == root
    viewer.open_path(root / 'c.png')
    finish_viewer_open(qapp, viewer)
    controller._finish_side_folder(viewer, SimpleNamespace(generation=pending[0],
        cancelled=False, code='no_images'), success=False)
    assert browser.current_path == root
    assert viewer.book_session.current_path == root / 'c.png'


def test_missing_candidate_preserves_source_and_browser(shelf, qapp, monkeypatch):
    controller, browser, root = shelf
    viewer = open_from(controller, browser, root / 'a.png', qapp)
    snapshot = browser.adjacent_book_snapshot(root)
    monkeypatch.setattr(browser, 'adjacent_book_snapshot', lambda _parent: snapshot)
    (root / 'b').rename(root / 'moved-b')
    viewer._on_extra_mouse_button('forward')
    assert viewer.book_session.wait_for_async()
    qapp.processEvents()
    assert viewer.book_session.current_path == root / 'a.png'
    assert browser.current_path == root
    assert id(viewer) not in controller._side_folder_pending


def test_inactive_viewer_empty_result_does_not_move_browser(shelf, qapp):
    controller, browser, root = shelf
    viewer = open_from(controller, browser, root / 'd', qapp)
    other = controller.create_viewer_window()
    viewer._on_extra_mouse_button('forward')
    controller._on_viewer_activated(other)
    assert viewer.book_session.wait_for_async()
    qapp.processEvents()
    assert browser.current_path == root
    assert viewer.book_session.current_path == root / 'd'


def test_no_browser_or_snapshot_is_explicitly_unavailable(tmp_path, qapp):
    write_image(tmp_path / 'a.png')
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'mouse_side_buttons_folder_navigation': True})
    try:
        viewer = controller.open_path(tmp_path / 'a.png')
        finish_viewer_open(qapp, viewer)
        assert controller.open_side_folder(viewer, 1) == 'unavailable'
        assert controller.get_browser_window() is None
    finally:
        close_controller(controller, qapp)


def test_setting_default_apply_cancel_and_normalization(tmp_path, qapp):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    assert config.get('mouse_side_buttons_folder_navigation') is False
    dialog = SettingsDialog(config)
    dialog.mouse_side_buttons_folder_navigation_checkbox.setChecked(True)
    dialog.reject()
    assert config.get('mouse_side_buttons_folder_navigation') is False
    dialog = SettingsDialog(config)
    dialog.mouse_side_buttons_folder_navigation_checkbox.setChecked(True)
    dialog.apply_settings()
    dialog.reject()
    assert ConfigManager(config.path).load()['mouse_side_buttons_folder_navigation'] is True
    config.apply({'mouse_side_buttons_folder_navigation': 'invalid'})
    assert config.get('mouse_side_buttons_folder_navigation') is False
