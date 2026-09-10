from types import SimpleNamespace

import pytest

from app.browser_filter import BrowserFilterState
from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_sort import BrowserSortKey, BrowserSortOrder
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager


def _item(folder, name, *, rating=4, openable=True):
    return BrowserItem(name, folder / name, BrowserItemKind.IMAGE, None,
                       file_size=123, modified_time_ns=456, extension=".jpg",
                       rating=rating, openable_by_nivisviewer=openable)


@pytest.fixture
def browser(tmp_path, qapp):
    window = BrowserWindow(config_manager=ConfigManager(tmp_path / "config.json"), restore_initial_location=False)
    window.current_path = tmp_path
    yield window
    window._pending_scan = None
    window.close()
    qapp.processEvents()


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("fallback", [False, True])
def test_open_shares_one_filtered_sorted_capture(browser, monkeypatch, partial, fallback):
    selected = _item(browser.current_path, "keep10.jpg", rating=3)
    earlier = _item(browser.current_path, "keep2.jpg", rating=5)
    rejected = _item(browser.current_path, "drop.jpg")
    low = _item(browser.current_path, "keep-low.jpg", rating=1)
    browser.browser_sort_key = BrowserSortKey.RATING
    browser.browser_sort_order = BrowserSortOrder.DESCENDING
    browser.item_model.configure_sort("rating", "descending", browser.browser_folders_first)
    browser.browser_filter_state = BrowserFilterState.normalized(search_text="keep", rating_mode="at_least", rating_reference=3)
    browser.item_model.configure_filter(browser.browser_filter_state)
    browser.item_model.set_items([selected] if partial else [selected, rejected, low, earlier])
    if partial:
        browser._pending_scan = SimpleNamespace(
            committed=True, path=browser.current_path,
            remaining_items=(selected, rejected, low, earlier), remaining_item_offset=1,
        )
    captured = []
    original = browser._visible_order_snapshot_items

    def capture():
        result = original()
        captured.append(result)
        return result

    monkeypatch.setattr(browser, "_visible_order_snapshot_items", capture)
    calls = []
    browser._open_path_handler = lambda *args: calls.append(args)
    index = browser.item_model.index(browser.item_model.row_for_path(selected.path), 0)
    if fallback:  # Same dispatch used by bookmark/history entry points.
        browser._invoke_open_path_handler(str(selected.path), True)
    else:
        browser.open_item(index, open_in_new_window=True)
    assert len(captured) == len(calls) == 1
    path, new_window, folder, mixed = calls[0]
    assert path == str(selected.path) and new_window is True
    assert folder.image_ids == mixed.image_paths == (str(earlier.path), str(selected.path))
    assert folder.fingerprints == mixed.image_fingerprints
    assert folder.selected_index == 1
    assert folder.selected_image == str(selected.path)
    assert folder.generation == mixed.scan_generation == browser._scan_generation
    assert folder.sort_identity == mixed.sort_identity
    assert folder.filter_identity == mixed.filter_identity
    assert "search='keep'" in folder.filter_identity


@pytest.mark.parametrize("arity", [2, 3, 4])
def test_handler_compatibility_supplied_snapshots_and_drop_opt_out(browser, monkeypatch, arity):
    item = _item(browser.current_path, "one.jpg")
    browser.item_model.set_items([item])
    calls = []
    handlers = {
        2: lambda path, new: calls.append((path, new)),
        3: lambda path, new, folder: calls.append((path, new, folder)),
        4: lambda path, new, folder, mixed: calls.append((path, new, folder, mixed)),
    }
    browser._open_path_handler = handlers[arity]
    browser.open_item(browser.item_model.index(0, 0))
    assert len(calls[-1]) == arity
    folder = browser._folder_snapshot_for_item(item)
    mixed = browser.adjacent_book_snapshot(browser.current_path)

    def forbidden():
        raise AssertionError("supplied snapshots / external drops must not capture")

    monkeypatch.setattr(browser, "_visible_order_snapshot_items", forbidden)
    browser._invoke_open_path_handler(str(item.path), True, folder, mixed)
    assert calls[-1][1] is True
    if arity >= 3:
        assert calls[-1][2] is folder
    if arity == 4:
        assert calls[-1][3] is mixed
    browser._open_dropped_paths((str(item.path), str(item.path)))
    assert calls[-2][1] is False and calls[-1][1] is True
    assert all(value is None for value in calls[-1][2:])


def test_initial_image_eligibility_is_not_silently_narrowed(browser):
    selected = _item(browser.current_path, "1.jpg")
    disabled = _item(browser.current_path, "2.jpg", openable=False)
    browser.item_model.set_items([selected, disabled])
    calls = []
    browser._open_path_handler = lambda *args: calls.append(args)
    browser.open_item(browser.item_model.index(0, 0))
    folder, mixed = calls[0][2:]
    assert folder.image_ids == (str(selected.path), str(disabled.path))
    assert mixed.image_paths == (str(selected.path),)


def test_partial_scan_from_other_location_is_not_captured(browser):
    selected = _item(browser.current_path, "1.jpg")
    browser.item_model.set_items([selected])
    browser._pending_scan = SimpleNamespace(
        committed=True, path=browser.current_path / "elsewhere",
        remaining_items=(_item(browser.current_path, "2.jpg"),), remaining_item_offset=0,
    )
    assert browser._visible_order_snapshot_items() == (selected,)


def test_direct_open_keeps_exact_selected_item_even_with_normalized_duplicates(browser):
    browser.item_model.set_items([
        _item(browser.current_path, "A.jpg"), _item(browser.current_path, "a.jpg"),
    ])
    selected = browser.item_model.item_at(0)
    calls = []
    browser._open_path_handler = lambda *args: calls.append(args)
    browser.open_item(browser.item_model.index(0, 0))
    assert calls[0][2].selected_image == str(selected.path)
