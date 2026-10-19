from types import SimpleNamespace
from unittest.mock import Mock
from threading import get_ident

import pytest
from PySide6.QtCore import Qt

from app.adjacent_book_search import (
    AdjacentBookBrowserSnapshot, AdjacentBookSnapshotEntry,
    AdjacentBookSearchResult, AdjacentBookSearchStatus, SIBLING_FOLDERS,
)
from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from tests.test_application_controller import (
    make_controller, write_image, write_archive, finish_viewer_open, close_controller, wait_until,
)
from tests.test_viewer_xbutton_navigation import _send_mouse_button


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


@pytest.mark.parametrize('route', ['browser_image', 'direct_image', 'direct_folder'])
@pytest.mark.parametrize('button, expected', [
    (Qt.MouseButton.BackButton, '01'), (Qt.MouseButton.ForwardButton, '03'),
])
def test_image_folder_side_buttons_move_to_sibling_folder(tmp_path, qapp, route, button, expected):
    root = tmp_path / 'books'
    for folder in ('01', '02', '03'):
        for page in ('1.png', '2.png'):
            write_image(root / folder / page)
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'mouse_side_buttons_folder_navigation': True, 'view_mode': 'single'})
    browser = None
    try:
        if route == 'browser_image':
            browser = controller.create_browser_window()
            browser.set_current_folder(root / '02')
            assert browser.wait_for_scan()
            browser.open_item(browser.item_model.index(0, 0))
            viewer = controller.get_active_viewer()
        else:
            path = root / '02' if route == 'direct_folder' else root / '02' / '1.png'
            viewer = controller.open_path(path)
        finish_viewer_open(qapp, viewer)
        viewer.next_page()
        from tests.test_viewer_browser_page_sync import settle_page
        settle_page(qapp, viewer, root / '02' / '2.png')
        _send_mouse_button(viewer.viewer, button)
        assert wait_until(qapp, lambda: viewer.book_session.current_path == root / expected, timeout=5)
        finish_viewer_open(qapp, viewer)
        assert viewer.book_session.source.source_path == root / expected
        assert controller.viewer_windows == (viewer,)
        if browser is None:
            assert controller.get_browser_window() is None
        else:
            assert browser.current_path == root / '02'
    finally:
        close_controller(controller, qapp)


def test_sibling_folder_search_uses_parent_sort_and_worker_thread(tmp_path, qapp, monkeypatch):
    root = tmp_path / 'books'
    for name in ('01', '02', '03'):
        write_image(root / name / '1.png')
    write_archive(root / '02-extra.cbz')
    write_image(root / '02-extra.png')
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({
        'mouse_side_buttons_folder_navigation': True,
        'browser_folder_sort_rules': [{'path': str(root), 'scope': 'folder',
            'mode': 'specified', 'sort_key': 'name', 'sort_order': 'descending'}],
    })
    threads = []
    filesystem = controller.adjacent_book_search.filesystem
    scan = filesystem.scandir
    def record_scan(*args, **kwargs):
        threads.append(get_ident())
        return scan(*args, **kwargs)
    monkeypatch.setattr(filesystem, 'scandir', record_scan)
    monkeypatch.setattr(filesystem, 'directory_contains_supported_image',
        lambda *_args: pytest.fail('folder navigation must not inspect candidate image contents'))
    try:
        viewer = controller.open_path(root / '02')
        finish_viewer_open(qapp, viewer)
        _send_mouse_button(viewer.viewer, Qt.MouseButton.ForwardButton)
        assert wait_until(qapp, lambda: viewer.book_session.current_path == root / '01', timeout=5)
        finish_viewer_open(qapp, viewer)
        assert threads and all(thread != get_ident() for thread in threads)
        generation = viewer.book_session.generation
        _send_mouse_button(viewer.viewer, Qt.MouseButton.ForwardButton)
        assert wait_until(qapp, lambda: not controller._adjacent_request_by_window)
        assert viewer.book_session.current_path == root / '01'
        assert viewer.book_session.generation == generation
        controller.config.apply({'loop_book_navigation': True})
        _send_mouse_button(viewer.viewer, Qt.MouseButton.ForwardButton)
        assert wait_until(qapp, lambda: viewer.book_session.current_path == root / '03', timeout=5)
        finish_viewer_open(qapp, viewer)
    finally:
        close_controller(controller, qapp)


@pytest.mark.parametrize('change', ['newer_request', 'reopen', 'disable', 'pending_open'])
def test_sibling_folder_search_rejects_stale_result(tmp_path, qapp, monkeypatch, change):
    root = tmp_path / 'books'
    for name in ('01', '02', '03'):
        write_image(root / name / '1.png')
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'mouse_side_buttons_folder_navigation': True})
    searches = []
    monkeypatch.setattr(controller.adjacent_book_search, 'search', lambda request: searches.append(request) or True)
    try:
        viewer = controller.open_path(root / '02')
        finish_viewer_open(qapp, viewer)
        _send_mouse_button(viewer.viewer, Qt.MouseButton.ForwardButton)
        request = searches[-1]
        assert request.candidate_mode == SIBLING_FOLDERS
        if change == 'newer_request':
            _send_mouse_button(viewer.viewer, Qt.MouseButton.BackButton)
        elif change == 'reopen':
            viewer.open_path(root / '02')
            finish_viewer_open(qapp, viewer)
        elif change == 'pending_open':
            viewer.open_path(root / '03')
        else:
            controller.config.apply({'mouse_side_buttons_folder_navigation': False})
        opened = Mock()
        monkeypatch.setattr(controller, '_open_side_folder_candidate', opened)
        controller._on_adjacent_book_search_result(AdjacentBookSearchResult(
            request.request_id, AdjacentBookSearchStatus.FOUND, str(root / '03'), request.generation))
        opened.assert_not_called()
        if change == 'newer_request':
            latest = searches[-1]
            controller._on_adjacent_book_search_result(AdjacentBookSearchResult(
                latest.request_id, AdjacentBookSearchStatus.FOUND, str(root / '01'), latest.generation))
            opened.assert_called_once_with(viewer, str(root / '01'), None)
        elif change == 'disable':
            assert id(viewer) not in controller._adjacent_request_by_window
    finally:
        close_controller(controller, qapp)


def test_sibling_folder_empty_fallback_keeps_navigation_anchor(tmp_path, qapp):
    root = tmp_path / 'books'
    write_image(root / '01' / '1.png')
    (root / '02').mkdir()
    write_image(root / '03' / '1.png')
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'mouse_side_buttons_folder_navigation': True})
    try:
        viewer = controller.open_path(root / '01')
        finish_viewer_open(qapp, viewer)
        _send_mouse_button(viewer.viewer, Qt.MouseButton.ForwardButton)
        assert wait_until(qapp, lambda: controller.get_browser_window() is not None and
                          controller.get_browser_window().current_path == root / '02', timeout=5)
        assert viewer.book_session.current_path == root / '01'
        _send_mouse_button(viewer.viewer, Qt.MouseButton.ForwardButton)
        assert wait_until(qapp, lambda: viewer.book_session.current_path == root / '03', timeout=5)
        finish_viewer_open(qapp, viewer)
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
