from pathlib import Path
from unittest.mock import patch

import pytest

from app.browser_filter import BrowserFilterState
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel


def make_model():
    model = BrowserItemModel()
    model.configure_filter_restore_cache(enabled=True, max_entries=100)
    model.set_sorted_items([
        BrowserItem(f'{i}.zip', Path(f'C:/synthetic/{i}.zip'), BrowserItemKind.ARCHIVE, 0)
        for i in range(10)
    ])
    return model


def test_clear_reuses_order_and_path_index_without_sorting(qapp):
    model = make_model()
    original = model.items
    model.configure_filter(BrowserFilterState.normalized(search_text='1'))
    model.configure_filter(BrowserFilterState.normalized(include_tags=('missing',)))
    with patch.object(model, 'visible_items', side_effect=AssertionError('recomputed')):
        model.configure_filter(BrowserFilterState.normalized())
    assert model.items == original
    assert [model.row_for_path(item.path) for item in model.items] == list(range(10))


@pytest.mark.parametrize('sort_key,sort_order', [('name', 'descending'), ('random', 'ascending')])
def test_cached_clear_preserves_current_sort_and_random_order(qapp, sort_key, sort_order):
    model = make_model()
    model.configure_sort(sort_key, sort_order, True, random_seed=123)
    expected = model.items
    model.configure_filter(BrowserFilterState.normalized(search_text='1'))
    model.configure_filter(BrowserFilterState.normalized())
    assert model.items == expected


@pytest.mark.parametrize('change', ['disable', 'limit', 'sort', 'rename', 'page_count', 'replace', 'scan'])
def test_current_view_cache_never_restores_stale_items(qapp, change):
    model = make_model()
    model.configure_filter(BrowserFilterState.normalized(search_text='1'))
    if change == 'disable':
        model.configure_filter_restore_cache(enabled=False, max_entries=100)
    elif change == 'limit':
        model.configure_filter_restore_cache(enabled=True, max_entries=1)
    elif change == 'sort':
        model.configure_sort('name', 'descending', True)
    elif change == 'rename':
        model.apply_rating_renames(((Path('C:/synthetic/2.zip'), Path('C:/synthetic/new.zip'), 3),))
    elif change == 'page_count':
        model.set_page_count('C:/synthetic/2.zip', 42)
    elif change == 'replace':
        model.set_sorted_items(model.source_items[:3])
    else:
        model.begin_final_directory_scan(model.source_items[:3], generation=123)
        model.append_final_directory_scan(make_model().source_items[3:5], generation=123)
        model.finish_directory_scan(generation=123)
    expected = model.sort_items(model.source_items)
    model.configure_filter(BrowserFilterState.normalized())
    assert model.items == expected
    assert all(model.row_for_path(item.path) == row for row, item in enumerate(model.items))
    if change == 'page_count':
        assert model.page_count('C:/synthetic/2.zip') == 42


@pytest.mark.parametrize('enabled,limit', [(False, 100), (True, 1)])
def test_disabled_or_over_limit_uses_normal_path(qapp, enabled, limit):
    model = make_model()
    model.configure_filter_restore_cache(enabled=enabled, max_entries=limit)
    model.configure_filter(BrowserFilterState.normalized(search_text='1'))
    with patch.object(model, 'visible_items', wraps=model.visible_items) as rebuild:
        model.configure_filter(BrowserFilterState.normalized())
    rebuild.assert_called_once()


@pytest.mark.parametrize('sort_key,order', [('name', 'ascending'), ('name', 'descending'), ('rating', 'descending'), ('random', 'ascending')])
def test_filter_changes_reuse_saved_order_without_sorting(qapp, sort_key, order):
    model = make_model()
    model.apply_rating_renames(((Path('C:/synthetic/2.zip'), Path('C:/synthetic/2{zpi$t=赤;}.zip'), 4),))
    model.configure_sort(sort_key, order, True, random_seed=57)
    original = model.items
    states = [
        BrowserFilterState.normalized(include_tags=('赤',)),
        BrowserFilterState.normalized(rating_mode='at_least', rating_reference=3),
        BrowserFilterState.normalized(exclude_tags=('赤',)),
        BrowserFilterState.normalized(search_text='1'),
        BrowserFilterState.normalized(),
    ]
    with patch.object(model, 'visible_items', side_effect=AssertionError('recomputed')):
        for state in states:
            model.configure_filter(state)
            assert model.items == tuple(item for item in original if state.matches(item))
            assert all(model.row_for_path(item.path) == row for row, item in enumerate(model.items))


def test_page_counts_patch_hidden_cached_rows_without_losing_fast_filter(qapp):
    model = make_model()
    model.configure_filter(BrowserFilterState.normalized(search_text='1'))
    saved = model._unfiltered_view
    model.set_page_count('C:/synthetic/2.zip', 42)
    model.set_page_count('C:/synthetic/1.zip', 17)
    assert model._unfiltered_view is saved
    with patch.object(model, 'visible_items', side_effect=AssertionError('recomputed')):
        model.configure_filter(BrowserFilterState.normalized(search_text='2'))
        assert model.items[0].page_count == 42
        model.configure_filter(BrowserFilterState.normalized())
    assert model.page_count('C:/synthetic/1.zip') == 17


@pytest.mark.parametrize('mutation', ['rename', 'replace', 'sort', 'scan'])
def test_filter_after_mutation_matches_fresh_model(qapp, mutation):
    model = make_model()
    model.configure_filter(BrowserFilterState.normalized(search_text='1'))
    if mutation == 'rename':
        model.apply_rating_renames(((Path('C:/synthetic/2.zip'), Path('C:/synthetic/2{zpi$t=赤;}.zip'), 4),))
    elif mutation == 'replace':
        # A reconciliation result after moving/deleting files and adding a file.
        model.set_items([*model.source_items[3:], BrowserItem('new.zip', Path('C:/synthetic/new.zip'), BrowserItemKind.ARCHIVE, 0)])
    elif mutation == 'sort':
        model.configure_sort('name', 'descending', True)
    else:
        model.begin_directory_scan(generation=9)
        model.append_scan_batch(make_model().source_items[4:], generation=9)
        assert model.append_scan_batch(make_model().source_items[:2], generation=8) == 0
        model.finish_directory_scan(generation=9)
    reference = BrowserItemModel()
    if mutation == 'sort':
        reference.configure_sort('name', 'descending', True)
    reference.set_items(model.source_items)
    for state in [BrowserFilterState.normalized(include_tags=('赤',)), BrowserFilterState.normalized(rating_mode='at_least', rating_reference=3), BrowserFilterState.normalized()]:
        model.configure_filter(state)
        reference.configure_filter(state)
        assert model.items == reference.items
        assert model._row_by_key == reference._row_by_key
