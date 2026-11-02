from threading import Event

import pytest

from app import browser_scanner
from app.browser_scanner import BrowserScanRequest, scan_directory
from app.browser_scanner import BrowserScanBatch
from tests.test_application_controller import make_controller, close_controller, write_image, wait_until


def test_priority_scan_emits_real_selected_entry_before_other_metadata(tmp_path, monkeypatch):
    for name in ('a.png', 'z.png'):
        write_image(tmp_path / name)
    (tmp_path / '子フォルダ').mkdir()
    original = browser_scanner.scan_entry_from_dir_entry
    visited, batches = [], []
    def observed(entry, *args):
        visited.append(entry.name)
        return original(entry, *args)
    monkeypatch.setattr(browser_scanner, 'scan_entry_from_dir_entry', observed)
    def receive(batch):
        batches.append(batch)
        if batch.priority_ready is not None:
            assert visited == ['z.png']
            batch.priority_ready.set()
    result = scan_directory(BrowserScanRequest(str(tmp_path), 1, priority_path=str(tmp_path / 'z.png')),
                            Event(), receive)
    assert batches[0].priority_item
    assert [entry.path for entry in batches[0].entries] == [str(tmp_path / 'z.png')]
    assert {item.path.name for item in result.prepared_items} == {'a.png', 'z.png', '子フォルダ'}
    assert result.total_count == 3


def test_cancelled_priority_scan_does_not_wait_for_gui_acknowledgment(tmp_path):
    write_image(tmp_path / 'a.png')
    cancelled = Event()
    def receive(batch):
        if batch.priority_item:
            cancelled.set()
    result = scan_directory(BrowserScanRequest(str(tmp_path), 1, priority_path=str(tmp_path / 'a.png')),
                            cancelled, receive)
    assert result.cancelled


def test_stale_priority_batch_releases_worker_without_changing_browser(tmp_path, qapp):
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    try:
        old = browser.current_path
        ready = Event()
        browser._on_scan_batch(BrowserScanBatch(str(tmp_path), -999, (), priority_item=True, priority_ready=ready))
        assert ready.is_set() and browser.current_path == old
    finally:
        close_controller(controller, qapp)


@pytest.mark.parametrize('supersede', [False, True])
def test_priority_selection_is_visible_while_full_scan_waits_and_keeps_latest_target(
    tmp_path, qapp, monkeypatch, supersede,
):
    old = tmp_path / '元の本'
    target = tmp_path / '移動先'
    elsewhere = tmp_path / '別の場所'
    elsewhere.mkdir()
    write_image(old / 'old.png')
    for name in ('a.png', 'z.png'):
        write_image(target / name)
    (target / '子フォルダ').mkdir()
    controller = make_controller(tmp_path / 'profile', qapp)
    browser = controller.create_browser_window()
    entered, release = Event(), Event()
    original = browser_scanner.scan_entry_from_dir_entry
    try:
        browser.set_current_folder(old)
        assert browser.wait_for_scan()
        def blocked(entry, *args):
            if entry.path == str(target / 'a.png'):
                entered.set()
                assert release.wait(10)
            return original(entry, *args)
        monkeypatch.setattr(browser_scanner, 'scan_entry_from_dir_entry', blocked)
        browser.select_path(target / 'z.png', prioritize_selection=True)
        def selected():
            item = browser.item_model.item_at(browser.list_view.currentIndex())
            return item.path if item is not None else None
        assert wait_until(qapp, entered.is_set)
        assert selected() == target / 'z.png'
        assert browser.current_path == target and browser.item_model.rowCount() == 1
        assert browser._pending_scan.priority_item_applied
        assert browser.folder_snapshot_cache.get(target, browser._current_browser_visibility_policy(),
                                                  browser._browser_sort_policy_for_path(target)) is None
        if supersede:
            assert browser.navigate_to(elsewhere)
            assert wait_until(qapp, lambda: browser.current_path == elsewhere)
        else:
            # A Viewer page turn can arrive before the other rows exist.
            browser.synchronize_viewer_item(target / 'a.png', expected_parent=target, preserve_selection=True)
        release.set()
        assert browser.wait_for_scan()
        if supersede:
            assert browser.current_path == elsewhere and browser.item_model.rowCount() == 0
        else:
            assert selected() == target / 'a.png'
            assert {item.path.name for item in browser.item_model.source_items} == {'a.png', 'z.png', '子フォルダ'}
    finally:
        release.set()
        close_controller(controller, qapp)
