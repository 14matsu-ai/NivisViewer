from threading import Event, get_ident

import pytest

from app.adjacent_book_search import (
    SIDE_BOOKS, AdjacentBookBrowserSnapshot, AdjacentBookSnapshotEntry,
    AdjacentBookSearchRequest, AdjacentBookSearchService, AdjacentBookSearchStatus,
)
from app.folder_cover import NIVIS_COVER_NAME
from tests.test_application_controller import (
    make_controller, write_image, write_archive, close_controller, wait_until,
)
from tests.test_viewer_side_folders import finish_navigation


@pytest.mark.parametrize('route', ['parent_snapshot', 'inside_folder', 'no_browser'])
def test_side_buttons_traverse_folders_archives_and_skip_empty_folders(tmp_path, qapp, route):
    root = tmp_path / '本棚'
    for name in ('01/1.png', '05/1.png', '02/child/1.png', f'04/{NIVIS_COVER_NAME}'):
        write_image(root / name)
    write_archive(root / '03.cbz')
    write_image(root / '02.png')
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'mouse_side_buttons_folder_navigation': True,
                             'browser_folders_first': False, 'view_mode': 'single'})
    browser = None
    try:
        snapshot = None
        if route != 'no_browser':
            browser = controller.create_browser_window()
            browser.set_current_folder(root if route == 'parent_snapshot' else root / '01')
            assert browser.wait_for_scan()
            snapshot = browser.adjacent_book_snapshot(browser.current_path)
        viewer = controller.open_path(root / '01' / '1.png' if route == 'inside_folder' else root / '01',
                                      browser_snapshot=snapshot)
        finish_navigation(controller, qapp, viewer)
        probes = []
        probe = controller.adjacent_book_search.filesystem.directory_contains_supported_image
        def tracked(path, **kwargs):
            probes.append((path, get_ident()))
            return probe(path, **kwargs)
        controller.adjacent_book_search.filesystem.directory_contains_supported_image = tracked
        for button, expected in [('forward', '03.cbz'), ('forward', '05'),
                                 ('back', '03.cbz'), ('back', '01')]:
            viewer._on_extra_mouse_button(button)
            finish_navigation(controller, qapp, viewer)
            assert viewer.book_session.current_path == root / expected
            if browser is not None:
                assert browser.wait_for_scan()
                selected = browser.item_model.item_at(browser.list_view.currentIndex())
                assert selected.path == (root / expected if '.' in expected else root / expected / '1.png')
                assert browser.current_path == selected.path.parent
            else:
                assert controller.get_browser_window() is None
        assert probes and all(thread != get_ident() for _, thread in probes)
        assert len(controller.viewer_windows) == 1
    finally:
        close_controller(controller, qapp)


def test_empty_neighbors_loop_does_not_reopen_current_book(tmp_path, qapp):
    root = tmp_path / 'books'
    write_image(root / '01' / '1.png')
    (root / '02').mkdir()
    (root / '03').mkdir()
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'mouse_side_buttons_folder_navigation': True, 'loop_book_navigation': True})
    try:
        viewer = controller.open_path(root / '01')
        finish_navigation(controller, qapp, viewer)
        generation = viewer.book_session.generation
        for direction in ('forward', 'back', 'forward'):
            viewer._on_extra_mouse_button(direction)
            finish_navigation(controller, qapp, viewer)
            assert viewer.book_session.generation == generation
            assert viewer.book_session.current_path == root / '01'
        assert controller.get_browser_window() is None
    finally:
        close_controller(controller, qapp)


def test_captured_mixed_order_and_filter_override_filesystem_cache(tmp_path, qapp, monkeypatch):
    root = tmp_path / 'books'
    for name in ('a', 'b', 'c'):
        write_image(root / name / '1.png')
    write_archive(root / 'book.cbz')
    service = AdjacentBookSearchService()
    results = []
    service.result_ready.connect(results.append)
    try:
        def search(sequence, snapshot=None):
            assert service.search(AdjacentBookSearchRequest(
                sequence, str(root / 'b'), 1, False, snapshot, sequence, candidate_mode=SIDE_BOOKS))
            assert wait_until(qapp, lambda: len(results) == sequence)
            return results[-1]
        assert search(1).candidate_path == str(root / 'c')
        entries = tuple(AdjacentBookSnapshotEntry(str(root / name), kind, '.cbz' if kind == 'archive' else '', name)
                        for name, kind in [('c', 'folder'), ('b', 'folder'), ('book.cbz', 'archive'), ('a', 'folder')])
        snapshot = AdjacentBookBrowserSnapshot(str(root), 1, entries)
        monkeypatch.setattr(service.filesystem, 'scandir', lambda *_args, **_kw: pytest.fail('snapshot must preserve visible order'))
        assert search(2, snapshot).candidate_path == str(root / 'book.cbz')
        filtered = AdjacentBookBrowserSnapshot(str(root), 1, entries[:2])
        assert search(3, filtered).status == AdjacentBookSearchStatus.BOUNDARY
        assert search(4).candidate_path == str(root / 'c')
    finally:
        service.close()


def test_cached_neighbor_is_rechecked_when_its_images_disappear(tmp_path, qapp):
    root = tmp_path / 'books'
    for name in ('01', '02', '03'):
        write_image(root / name / '1.png')
    service = AdjacentBookSearchService()
    results = []
    service.result_ready.connect(results.append)
    try:
        for sequence, expected in [(1, '02'), (2, '03')]:
            assert service.search(AdjacentBookSearchRequest(
                sequence, str(root / '01'), 1, False, None, sequence, candidate_mode=SIDE_BOOKS))
            assert wait_until(qapp, lambda: len(results) == sequence)
            assert results[-1].candidate_path == str(root / expected)
            if sequence == 1:
                (root / '02' / '1.png').unlink()
    finally:
        service.close()


@pytest.mark.parametrize('button, start, expected', [('forward', '01', '03'), ('back', '03', '01')])
def test_folder_becoming_empty_after_probe_is_skipped_without_browser_fallback(tmp_path, qapp, button, start, expected):
    root = tmp_path / 'books'
    for name in ('01', '02', '03'):
        write_image(root / name / '1.png')
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'mouse_side_buttons_folder_navigation': True})
    try:
        viewer = controller.open_path(root / start)
        finish_navigation(controller, qapp, viewer)
        factory = viewer.book_session._source_factory
        def changed_factory(path, **kwargs):
            if str(path) == str(root / '02'):
                (root / '02' / '1.png').unlink()
            return factory(path, **kwargs)
        viewer.book_session._source_factory = changed_factory
        viewer._on_extra_mouse_button(button)
        assert wait_until(qapp, lambda: viewer.book_session.current_path == root / expected, timeout=5)
        finish_navigation(controller, qapp, viewer)
        assert controller.get_browser_window() is None
    finally:
        close_controller(controller, qapp)


def test_rapid_direction_change_discards_blocked_neighbor_search(tmp_path, qapp):
    root = tmp_path / 'books'
    for name in ('01', '02', '03'):
        write_image(root / name / '1.png')
    write_archive(root / '04.cbz')
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'mouse_side_buttons_folder_navigation': True, 'browser_folders_first': False})
    entered, release = Event(), Event()
    try:
        viewer = controller.open_path(root / '03')
        finish_navigation(controller, qapp, viewer)
        filesystem = controller.adjacent_book_search.filesystem
        original = filesystem.directory_contains_supported_image
        def blocked(path, **kwargs):
            if path == str(root / '02'):
                entered.set()
                assert release.wait(5)
            return original(path, **kwargs)
        filesystem.directory_contains_supported_image = blocked
        viewer._on_extra_mouse_button('back')
        assert wait_until(qapp, entered.is_set)
        viewer._on_extra_mouse_button('forward')
        assert wait_until(qapp, lambda: viewer.book_session.current_path == root / '04.cbz')
        release.set()
        finish_navigation(controller, qapp, viewer)
        assert controller.adjacent_book_search._pool.waitForDone(3000)
        qapp.processEvents()
        assert viewer.book_session.current_path == root / '04.cbz'
    finally:
        release.set()
        close_controller(controller, qapp)


def test_image_only_browser_does_not_rebuild_full_snapshot_on_side_click(tmp_path, qapp, monkeypatch):
    root = tmp_path / 'books'
    for folder in ('01', '02'):
        for page in range(5):
            write_image(root / folder / f'{page}.png')
    (root / '01' / 'readme.txt').write_text('ignored non-book file', encoding='utf-8')
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'mouse_side_buttons_folder_navigation': True})
    try:
        browser = controller.create_browser_window()
        browser.set_current_folder(root / '01')
        assert browser.wait_for_scan()
        snapshot = browser.adjacent_book_snapshot(root / '01')
        viewer = controller.open_path(root / '01' / '0.png', browser_snapshot=snapshot)
        finish_navigation(controller, qapp, viewer)
        monkeypatch.setattr(browser, 'adjacent_book_snapshot', lambda *_a, **_kw: pytest.fail('must not recopy all page metadata'))
        viewer._on_extra_mouse_button('forward')
        finish_navigation(controller, qapp, viewer)
        assert viewer.book_session.current_path == root / '02'
    finally:
        close_controller(controller, qapp)
