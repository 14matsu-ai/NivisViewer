from pathlib import Path

import pytest

from app.browser_filter import BrowserFilterState
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel


def item(path):
    return BrowserItem(Path(path).name, Path(path), BrowserItemKind.ARCHIVE, None)


@pytest.mark.parametrize('route', ['set', 'final_scan', 'progressive_scan'])
def test_counts_follow_hidden_sources_append_rename_and_reset(qapp, tmp_path, route):
    model = BrowserItemModel()
    first, second = item(tmp_path / 'a.zip'), item(tmp_path / 'b.zip')
    if route == 'set':
        model.set_items([first, second])
    elif route == 'final_scan':
        model.begin_final_directory_scan([first], generation=1)
        model.append_final_directory_scan([first, second], generation=1)
    else:
        model.begin_directory_scan(generation=1)
        model.append_scan_batch([first, second, second], generation=1)
    assert model.source_count == 2
    model.configure_filter(BrowserFilterState(search_text='a.zip'))
    assert model.row_for_path(second.path) == -1
    assert model.set_page_count(second.path, 42)
    assert model.page_count(second.path) == 42
    assert not model.set_page_count(second.path, 42)
    renamed = tmp_path / 'B-renamed.zip'
    assert model.apply_rating_renames(((second.path, renamed, 3),))
    assert model.page_count(second.path) is None
    assert not model.set_page_count(second.path, 99)
    assert model.set_page_count(str(renamed).upper(), 43)
    model.configure_filter(BrowserFilterState())
    assert model.item_at(model.row_for_path(renamed)).page_count == 43
    assert model.source_items[1].page_count == 43
    model.begin_directory_scan(generation=2)
    assert model.append_final_directory_scan([second], generation=1) == 0
    assert model.page_count(renamed) is None
    assert not model.set_page_count(renamed, 44)
    model.append_scan_batch([first], generation=2)
    assert model.set_page_count(first.path, -5)
    assert model.page_count(first.path) == 0


def test_count_updates_do_not_resolve_every_path_in_large_folder(qapp, tmp_path, monkeypatch):
    model = BrowserItemModel()
    items = [item(tmp_path / f'{i}.zip') for i in range(10000)]
    model.set_items(items)
    model.configure_filter(BrowserFilterState(search_text='no visible match'))
    original = model._key
    resolved = []
    def key(path):
        resolved.append(path)
        return original(path)
    monkeypatch.setattr(model, '_key', key)
    for entry in items[100:130]:
        assert model.set_page_count(entry.path, 20)
        assert model.page_count(entry.path) == 20
    # Bound path work per delivered result, independent of the folder's size.
    assert len(resolved) <= 120


def test_duplicate_source_paths_keep_existing_count_semantics(qapp, tmp_path):
    model = BrowserItemModel()
    entry = item(tmp_path / 'duplicate.zip')
    model.set_sorted_items([entry, entry])
    assert model.set_page_count(entry.path, 12)
    assert [row.page_count for row in model.source_items] == [12, 12]
