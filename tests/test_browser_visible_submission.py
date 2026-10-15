"""Visible acquisition yields to input without replaying an obsolete viewport."""
from dataclasses import replace

from app.browser_model import BrowserItemKind
from tests.test_browser_grid import RecordingThumbnailProvider, make_item, make_window


def prepare(tmp_path, qapp, monkeypatch):
    provider = RecordingThumbnailProvider()
    window = make_window(tmp_path, qapp, provider=provider)
    window.item_model.set_items([
        make_item(tmp_path / f"{row:04}.jpg", BrowserItemKind.IMAGE) for row in range(200)
    ])
    window.list_view.doItemsLayout()
    window._browser_workflow._timer.stop()
    provider.requests.clear()
    # Model a costly request without sleeping in the test event loop.
    clock = [0.0]
    monkeypatch.setattr("app.browser_window.monotonic", lambda: clock[0])
    original = provider.request
    def request(*args, **kwargs):
        clock[0] += 0.003
        return original(*args, **kwargs)
    monkeypatch.setattr(provider, "request", request)
    return window, provider


def test_input_supersedes_unsubmitted_visible_rows(tmp_path, qapp, monkeypatch):
    window, provider = prepare(tmp_path, qapp, monkeypatch)
    try:
        window._request_visible_thumbnails()
        assert len(provider.requests) == 1
        assert window._visible_submission_queue
        old_first, old_last = window._visible_row_range()
        window.list_view.verticalScrollBar().setValue(window.list_view.viewport().height() * 3)
        assert window._thumbnail_request_timer.isActive()
        before = len(provider.requests)
        window._submit_visible_thumbnail_batch()
        assert len(provider.requests) == before  # Old continuation yields to input.
        new_first, new_last = window._visible_row_range()
        assert new_first > old_last
        window._request_visible_thumbnails()
        while window._visible_submission_queue:
            window._submit_visible_thumbnail_batch()
        paths = [path for path, _, _ in provider.requests[before:]]
        expected = [str(window.item_model.item_at(row).path) for row in range(new_first, new_last + 1)]
        assert paths == expected  # Finish the new plan once, without replaying it.
    finally:
        window.close()
        qapp.processEvents()


def test_submission_continuation_rejects_changed_items_and_generation(tmp_path, qapp, monkeypatch):
    window, provider = prepare(tmp_path, qapp, monkeypatch)
    try:
        window._request_visible_thumbnails()
        row, path, _, _ = window._visible_submission_queue[0]
        window.item_model._items[row] = replace(window.item_model.item_at(row), modified_time_ns=123)
        before = len(provider.requests)
        window._submit_visible_thumbnail_batch()
        assert all(request[0] != str(path) for request in provider.requests[before:])
        assert window._visible_submission_queue
        window._generation += 1
        before = len(provider.requests)
        window._submit_visible_thumbnail_batch()
        assert len(provider.requests) == before
        assert not window._visible_submission_queue
    finally:
        window.close()
        qapp.processEvents()
