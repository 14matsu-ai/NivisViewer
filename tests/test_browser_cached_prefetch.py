"""Saved-cache acquisition stays live without admitting speculative originals."""
from threading import Event, Thread, current_thread
from time import monotonic, sleep
from pathlib import Path
import os
import pytest

from app.browser_model import BrowserItemModel
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider
from app.thumbnail_render import ThumbnailRenderSpec
from tests.test_thumbnail_admission import source, pixels


def spin(qapp, predicate, timeout=10):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        sleep(.001)
    assert predicate()


@pytest.mark.parametrize('screens', [2, -1])
def test_unvisited_saved_video_is_prepared_without_speculative_extraction(tmp_path, qapp, screens):
    from dataclasses import replace
    from app.browser_model import BrowserItemKind
    from app.thumbnail_provider import ThumbnailLoadResult
    from tests.test_thumbnail_provider import make_item
    from tests.test_browser_thumbnail_first_exposure import fixture_listing, settle_presentation
    from tests.test_browser_retained_scroll_qt import make_browser, drain_background
    from tests.test_browser_thumbnail_ram_restore import wait_visible
    fixture_listing(tmp_path/'images', 180)
    window = make_browser(tmp_path, tmp_path/'images', qapp, 0, True)
    try:
        wait_visible(window, qapp)
        provider, model = window.thumbnail_provider, window.item_model
        spin(qapp, lambda: provider.pending_count == 0)
        last = window._visible_row_range()[1]
        items = list(model.items)
        videos = []
        for row, name in [(last+1, 'saved.mp4'), (last+2, 'missing.mp4'), (179, 'far.mp4')]:
            path = tmp_path/name
            path.write_bytes(b'synthetic-video-identity')
            item = replace(make_item(path, BrowserItemKind.OTHER), preview_kind='video',
                can_generate_preview=True, openable_by_nivisviewer=False)
            items[row] = item
            videos.append(item)
            if name != 'missing.mp4':
                assert provider._disk_cache.put(item, window.thumbnail_render_spec, pixels(),
                    entry_path=provider._preview_registry.disk_cache_variant(item))
        generated = []
        original = provider._generate_pipeline
        def generation(item, *args, **kwargs):
            if item.preview_kind == 'video':
                generated.append(item.path)
                return ThumbnailLoadResult(None, persist_to_disk=False)
            return original(item, *args, **kwargs)
        provider._generate_pipeline = generation
        model.set_sorted_items(items, preserve_thumbnails=True)
        window.config.apply({'browser_thumbnail_background_screens': screens}, save=False)
        drain_background(window, qapp)
        settle_presentation(window, qapp)
        assert provider.has_memory_thumbnail(videos[0], window.thumbnail_render_spec)
        assert not provider.has_memory_thumbnail(videos[1], window.thumbnail_render_spec)
        assert provider.has_memory_thumbnail(videos[2], window.thumbnail_render_spec) == (screens < 0)
        image = model._thumbnail_images[model._key(videos[0].path)]
        assert window.item_delegate._display_surface_cache.has_plan(image, window._thumbnail_presentation._plan)
        assert not generated
        assert last+2 not in window._browser_workflow._disk_misses
        # Idle must not repeatedly turn a saved-video miss into extraction.
        window._on_scroll_idle()
        drain_background(window, qapp)
        assert not generated
        # A speculative miss must still allow normal visible extraction.
        assert provider.request(videos[1], window.thumbnail_render_spec,
            generation=window._generation, priority=ThumbnailPriority.VISIBLE)
        spin(qapp, lambda: provider.pending_count == 0)
        assert generated == [videos[1].path]
    finally:
        window.close(); qapp.processEvents()


def test_fast_input_reads_full_configured_horizon_without_waiting_for_idle(tmp_path, qapp):
    from tests.test_browser_thumbnail_first_exposure import fixture_listing
    from tests.test_browser_retained_scroll_qt import make_browser, drain_background
    from tests.test_browser_thumbnail_ram_restore import wait_visible
    fixture_listing(tmp_path/'images', 240)
    window = make_browser(tmp_path, tmp_path/'images', qapp, 0, True)
    try:
        wait_visible(window, qapp)
        provider, workflow = window.thumbnail_provider, window._browser_workflow
        spin(qapp, lambda: provider.pending_count == 0)
        first, last = window._visible_row_range()
        span = last-first+1
        near_row, far_row = last+1, last+3*span
        for row in (near_row, far_row):
            assert provider._disk_cache.put(window.item_model.item_at(row), window.thumbnail_render_spec, pixels())
        window.config.apply({'browser_thumbnail_background_screens': 5}, save=False)
        window._fast_scrolling = True
        window._scroll_idle_timer.stop()
        workflow._pump()
        spin(qapp, lambda: workflow.stop_reason == 'near-read-complete' and provider.pending_count == 0)
        assert provider.has_memory_thumbnail(window.item_model.item_at(near_row), window.thumbnail_render_spec)
        assert window._fast_scrolling
        assert not window._scroll_idle_timer.isActive()
        assert not workflow._memory_refill
        assert provider.has_memory_thumbnail(window.item_model.item_at(far_row), window.thumbnail_render_spec)
    finally:
        window.close(); qapp.processEvents()


@pytest.mark.parametrize('owner, method', [
    (BrowserThumbnailProvider, '_path_key'), (BrowserItemModel, '_key'),
])
def test_path_key_cache_preserves_unicode_and_relative_directory_semantics(tmp_path, monkeypatch, owner, method):
    path_key = getattr(owner, method)
    absolute = tmp_path/'日本語'/'..'/'画像.png'
    expected = os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(absolute)))).casefold()
    assert path_key(absolute) == expected
    assert path_key(str(absolute)) == expected
    first, second = tmp_path/'first', tmp_path/'second'
    first.mkdir(); second.mkdir()
    monkeypatch.chdir(first)
    key = path_key(Path('画像.png'))
    monkeypatch.chdir(second)
    assert path_key(Path('画像.png')) != key
    assert path_key(absolute) == expected
    assert owner._absolute_path_key.cache_info().maxsize == 8192


def test_usage_update_path_resolution_does_not_block_saved_reads(tmp_path):
    item = source(tmp_path/'日本語画像.png')
    cache = ThumbnailDiskCache(tmp_path/'cache')
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    assert cache.put(item, spec, pixels())
    entered, release = Event(), Event()
    normalize = cache._normalize_path
    errors = []
    def slow_normalize(path):
        if current_thread() is worker:
            entered.set()
            assert release.wait(5)
        return normalize(path)
    def touch():
        try:
            cache.touch_source_tokens(((str(item.path), spec.cache_token),))
        except Exception as exc:
            errors.append(exc)
    worker = Thread(target=touch)
    cache._normalize_path = slow_normalize
    try:
        worker.start()
        assert entered.wait(2)
        # Acquiring the metadata lock must remain possible while Windows is
        # resolving a path for the unrelated idle usage-update batch.
        assert cache._lock.acquire(timeout=1)
        try:
            assert cache.get_suitable(item, spec) is not None
        finally:
            cache._lock.release()
    finally:
        release.set()
        worker.join(5)
        cache.close()
    assert not worker.is_alive() and not errors


def test_saved_miss_defers_generation_without_poisoning_later_request(tmp_path, qapp):
    item = source(tmp_path/'image.png')
    cache = ThumbnailDiskCache(tmp_path/'cache')
    calls, states, previews = [], [], []
    def decode(*_):
        calls.append(True)
        return pixels()
    provider = BrowserThumbnailProvider(disk_cache=cache, loader=decode)
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    provider.work_settled.connect(lambda *args: states.append(args[-1]))
    provider.preview_state_changed.connect(lambda *args: previews.append(args[-1]))
    try:
        assert provider.request_background_read(item, spec, generation=provider.generation) == 'queued'
        spin(qapp, lambda: states)
        assert states == ['disk-miss'] and not calls and not previews
        assert not provider.pending_count
        assert not provider._failed and not provider._quiet_results
        assert provider.request_background(item, spec, generation=provider.generation) == 'queued'
        spin(qapp, lambda: len(states) == 2)
        assert calls == [True] and states[-1] == 'ready'
        assert provider.has_memory_thumbnail(item, spec)
    finally:
        provider.close()


def test_running_saved_probe_promoted_to_visible_can_generate(tmp_path, qapp):
    item = source(tmp_path/'image.png')
    cache = ThumbnailDiskCache(tmp_path/'cache')
    entered, release = Event(), Event()
    calls = []
    def lookup(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return None
    cache.get_suitable = lookup
    provider = BrowserThumbnailProvider(disk_cache=cache, loader=lambda *_: calls.append(True) or pixels())
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    try:
        assert provider.request_background_read(item, spec, generation=provider.generation) == 'queued'
        assert entered.wait(2)
        provider.request(item, spec, priority=ThumbnailPriority.VISIBLE)
        release.set()
        spin(qapp, lambda: provider.has_memory_thumbnail(item, spec))
        assert calls == [True]
    finally:
        release.set();provider.close()


def test_saved_read_does_not_wait_for_unrelated_write_backpressure(tmp_path, qapp):
    item = source(tmp_path/'image.png')
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    cache = ThumbnailDiskCache(tmp_path/'cache')
    assert cache.put(item, spec, pixels())
    provider = BrowserThumbnailProvider(disk_cache=cache)
    provider._save_enqueue_backpressured = True
    try:
        assert provider.request_background_read(item, spec, generation=provider.generation) == 'queued'
        spin(qapp, lambda: provider.has_memory_thumbnail(item, spec))
        assert provider.cache_statistics()['disk_hit'] == 1
        assert provider.cache_statistics()['generated'] == 0
    finally:
        provider.close()


def test_visible_recenter_preserves_only_near_background_queue(tmp_path, qapp):
    items = [source(tmp_path/f'{i}.png') for i in range(3)]
    cache = ThumbnailDiskCache(tmp_path/'cache')
    entered, release = Event(), Event()
    def lookup(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return None
    cache.get_suitable = lookup
    provider = BrowserThumbnailProvider(disk_cache=cache)
    provider._read_pool.setMaxThreadCount(1)
    provider.configure_browser_memory(mode='128', limit_bytes=128*1024**2,
        desired_bytes=128*1024**2, group_capacity_bytes=128*1024**2, reason='ready')
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    try:
        assert provider.request_background_read(items[0], spec, generation=provider.generation) == 'queued'
        assert entered.wait(2)
        for item in items[1:]:
            assert provider.request_background_read(item, spec, generation=provider.generation) == 'queued'
        provider.set_cache_retention_priorities([(items[1].path, items[1].thumbnail_revision, 1)])
        assert provider.cancel_requests_except({str(items[0].path)},size=spec,generation=provider.generation) == 1
        assert provider.has_pending_request(str(items[1].path), spec.cache_token, provider.generation)
        assert not provider.has_pending_request(str(items[2].path), spec.cache_token, provider.generation)
        # A later reversal/settings change retires the old horizon too.
        provider.set_cache_retention_priorities([])
        assert provider.cancel_requests_except({str(items[0].path)},size=spec,generation=provider.generation) == 1
    finally:
        release.set();provider.close()


def test_scroll_idle_reopens_near_saved_misses(tmp_path, qapp):
    from tests.test_browser_thumbnail_first_exposure import fixture_listing
    from tests.test_browser_retained_scroll_qt import make_browser, drain_background
    from tests.test_browser_thumbnail_ram_restore import wait_visible
    fixture_listing(tmp_path/'images', 80)
    window = make_browser(tmp_path, tmp_path/'images', qapp, 0, True)
    try:
        wait_visible(window, qapp)
        provider, workflow = window.thumbnail_provider, window._browser_workflow
        spin(qapp, lambda: provider.pending_count == 0)
        before = provider.cache_statistics()['generated']
        window.config.apply({'browser_thumbnail_background_screens':2},save=False)
        window._fast_scrolling = True
        window._scroll_idle_timer.stop()
        workflow._pump()
        spin(qapp, lambda: workflow._disk_misses and provider.pending_count == 0 and not workflow._memory_refill)
        assert provider.cache_statistics()['generated'] == before
        missing_rows = set(workflow._disk_misses)
        window._on_scroll_idle()
        drain_background(window, qapp)
        assert not workflow._disk_misses
        assert all(provider.has_memory_thumbnail(window.item_model.item_at(row), window.thumbnail_render_spec)
                   for row in missing_rows)
    finally:
        window.close();qapp.processEvents()


def test_completion_burst_yields_before_one_background_dispatch(tmp_path, qapp):
    from tests.test_browser_thumbnail_first_exposure import fixture_listing
    from tests.test_browser_retained_scroll_qt import make_browser
    from tests.test_browser_thumbnail_ram_restore import wait_visible
    fixture_listing(tmp_path/'images', 40)
    window = make_browser(tmp_path, tmp_path/'images', qapp, 0, True)
    try:
        wait_visible(window, qapp)
        provider, workflow = window.thumbnail_provider, window._browser_workflow
        spin(qapp, lambda: provider.pending_count == 0)
        workflow._timer.stop()
        calls = []
        workflow._pump_batch = lambda: calls.append(True)
        # A queued completion burst and foreground pass must not each execute
        # speculative work on the input/publication call stack.
        for _ in range(16):
            workflow._settled('', window._generation, window.thumbnail_render_spec.cache_token, (), 'ready')
        window._request_visible_thumbnails()
        assert not calls
        assert workflow._timer.isActive()
        spin(qapp, lambda: calls)
        assert calls == [True]
        assert not workflow._timer.isActive()
    finally:
        window.close();qapp.processEvents()


def test_fast_scroll_defers_submission_and_settlement_replaces_only_one_slot(tmp_path, qapp, monkeypatch):
    from tests.test_browser_thumbnail_first_exposure import fixture_listing
    from tests.test_browser_retained_scroll_qt import make_browser
    from tests.test_browser_thumbnail_ram_restore import wait_visible
    fixture_listing(tmp_path/'images', 160)
    window = make_browser(tmp_path, tmp_path/'images', qapp, 0, True)
    try:
        wait_visible(window, qapp)
        provider, workflow = window.thumbnail_provider, window._browser_workflow
        spin(qapp, lambda: provider.pending_count == 0)
        window.config.apply({'browser_thumbnail_background_screens': 1}, save=False)
        workflow.recenter_cache_retention()
        window._fast_scrolling = True
        calls = []
        def read(item, spec, *, generation):
            calls.append(window.item_model.row_for_path(item.path))
            return 'queued'
        monkeypatch.setattr(provider, 'request_background_read', read)
        monkeypatch.setattr(workflow, '_pump_batch', lambda: pytest.fail('No dispatch slice inside a completion'))
        planned = {row for row, _identity in workflow._memory_scope}
        # Input restores already prepared RAM pixels, but must not create/start
        # read workers before Qt gets to paint the movement. The scheduled
        # visible/background pass and existing completions keep acquisition going.
        window._on_list_scrolled(window._last_scroll_value + window.list_view.viewport().height())
        assert not calls
        token = window.thumbnail_render_spec.cache_token
        workflow._settled('', window._generation, token, (), 'ready')
        assert len(calls) == 1
        before = len(calls)
        workflow._settled('', window._generation-1, token, (), 'ready')
        assert len(calls) == before
        workflow._rebuild_memory_refill()  # Recenter must not replay inflight rows.
        workflow._settled('', window._generation, token, (), 'ready')
        assert len(calls) == before+1
        assert set(calls) <= planned
        assert len(calls) == len(set(calls))
        key = next(iter(workflow._inflight_jobs))
        assert workflow._inflight != key
        workflow._submission_rejected(*key[1:5])
        assert workflow._memory_refill[0] == key[0]
        workflow._refill_saved_slot()
        assert calls[-1] == key[0]  # An older rejected job must remain retryable.
        workflow._memory_scope = tuple(pair for pair in workflow._memory_scope if pair[0] != key[0])
        workflow._submission_rejected(*key[1:5])
        assert key[0] not in workflow._memory_refill  # Do not retry beyond the moved horizon.
        before = len(calls)
        window._fast_scrolling = False
        workflow._settled('', window._generation, token, (), 'ready')
        assert len(calls) == before
        workflow._timer.stop()
    finally:
        window.close(); qapp.processEvents()


def test_visible_pass_reuses_retention_but_selection_refreshes_it(tmp_path, qapp):
    from PySide6.QtCore import QItemSelectionModel
    from tests.test_browser_thumbnail_first_exposure import fixture_listing
    from tests.test_browser_retained_scroll_qt import make_browser
    from tests.test_browser_thumbnail_ram_restore import wait_visible
    fixture_listing(tmp_path/'images', 80)
    window = make_browser(tmp_path, tmp_path/'images', qapp, 0, True)
    try:
        wait_visible(window, qapp)
        provider, workflow = window.thumbnail_provider, window._browser_workflow
        spin(qapp, lambda: provider.pending_count == 0)
        workflow.recenter_cache_retention()
        calls = []
        original = provider.set_cache_retention_priorities
        def retain(entries):
            calls.append(True)
            return original(entries)
        provider.set_cache_retention_priorities = retain
        for _ in range(3):
            window._request_visible_thumbnails()
        assert not calls
        index = window.item_model.index(70, 0)
        window.list_view.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select)
        item = window.item_model.item_at(70)
        assert calls
        assert provider._cache_retention_rank[(provider._path_key(item.path), item.thumbnail_revision)] == 0
        # A selected provisional preview in the next band is not counted in
        # selected_rows' extra allowance, but still needs model-only retention.
        window.config.apply({'browser_thumbnail_background_screens': 2}, save=False)
        row = window._visible_row_range()[1] + 1
        item = window.item_model.item_at(row)
        preview = pixels()
        window.item_model.set_thumbnail_image(item.path, preview,
            request_token=window.thumbnail_render_spec.cache_token)
        window.list_view.selectionModel().select(window.item_model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select)
        assert window.item_model._thumbnail_images[window.item_model._key(item.path)] is not None
    finally:
        window.close();qapp.processEvents()


def test_page_count_burst_preserves_thumbnail_plan_but_content_changes_reopen_it(tmp_path, qapp):
    from dataclasses import replace
    from tests.test_browser_thumbnail_first_exposure import fixture_listing
    from tests.test_browser_retained_scroll_qt import make_browser, drain_background
    from tests.test_browser_thumbnail_ram_restore import wait_visible
    fixture_listing(tmp_path/'images', 80)
    window = make_browser(tmp_path, tmp_path/'images', qapp, 2, True)
    try:
        wait_visible(window, qapp)
        drain_background(window, qapp)
        workflow, model = window._browser_workflow, window.item_model
        workflow.recenter_cache_retention()
        row = workflow._viewport[1] + 1
        assert row < model.rowCount()
        workflow._cursor.complete(row)
        viewport = workflow._viewport
        scope = workflow._memory_scope
        original = workflow._update_cache_retention
        updates = []
        def retain(*args, **kwargs):
            updates.append(True)
            return original(*args, **kwargs)
        workflow._update_cache_retention = retain
        for number in range(row, min(model.rowCount(), row+20)):
            item = model.item_at(number)
            window._on_page_count_ready(str(item.path), window._generation, 123)
            assert model.item_at(number).page_count == 123
            workflow.recenter_cache_retention(refresh=False)
        assert not updates
        assert workflow._viewport == viewport and workflow._memory_scope == scope
        assert workflow._cursor._done[row] == 1
        # ItemRole alone is not metadata-only: source revision changes must
        # still reopen the row and schedule fresh work.
        model._items[row] = replace(model.item_at(row), modified_time_ns=987654321)
        index = model.index(row, 0)
        model.dataChanged.emit(index, index, [model.ItemRole])
        assert workflow._viewport is None
        assert workflow._cursor._done[row] == 0
        assert workflow._timer.isActive()
    finally:
        window.close();qapp.processEvents()
