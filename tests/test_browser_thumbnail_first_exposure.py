"""Unvisited resident rows: quiet publication and first-paint surface readiness."""
from threading import Thread
import json
from time import monotonic, perf_counter, sleep

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QStyledItemDelegate

from app.browser_model import BrowserItemModel
from tests.test_browser_retained_scroll_qt import make_listing, make_browser, drain_background
from tests.test_browser_thumbnail_ram_restore import wait_visible
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager


def settle_presentation(window, qapp):
    controller = window._thumbnail_presentation
    controller.recenter()
    deadline = monotonic()+10
    def pending():
        return (controller._timer.isActive() or controller._waiting_capacity
                or window.thumbnail_provider.display_pending_count)
    while pending() and monotonic() < deadline:
        qapp.processEvents(); sleep(.001)
    assert not pending()


def fixture_listing(root, count):
    errors = []
    def make():
        try: make_listing(root, count)
        except BaseException as error: errors.append(error)
    thread = Thread(target=make); thread.start(); thread.join()
    assert not errors, errors


@pytest.mark.parametrize('screens', [2, -1])
@pytest.mark.parametrize('disk', [False, True])
@pytest.mark.parametrize('display', ['fit', 'center_crop'])
def test_first_exposure_uses_resident_model_and_prepared_band(tmp_path, qapp, screens, disk, display):
    root = tmp_path/'images'
    fixture_listing(root, 180)
    config = ConfigManager(tmp_path/'config.json')
    config.load()
    config.apply({'last_browser_path':str(root), 'browser_thumbnail_background_screens':screens,
                  'thumbnail_disk_cache_enabled':disk, 'browser_thumbnail_display_mode':display}, save=False)
    window = BrowserWindow(config_manager=config)
    window.resize(900,700); window.show()
    assert window.wait_for_scan()
    qapp.processEvents()
    provider = window.thumbnail_provider
    original = window.item_delegate
    events = []
    class Recorder(QStyledItemDelegate):
        def sizeHint(self, option, index): return original.sizeHint(option, index)
        def paint(self, painter, option, index):
            image = index.data(BrowserItemModel.ThumbnailImageRole)
            entry = window.item_model.item_at(index.row())
            cache = original._display_surface_cache
            before_hits, before_preps = cache.hits, cache.preparations
            before_model = isinstance(image, QImage) and not image.isNull()
            before_ram = provider.has_memory_thumbnail(entry, window.thumbnail_render_spec)
            original.paint(painter, option, index)
            events.append(dict(row=index.row(), model=before_model, ram=before_ram,
                               hits=cache.hits-before_hits, preparations=cache.preparations-before_preps))
    recorder = Recorder(window.list_view)
    window.list_view.setItemDelegate(recorder)
    try:
        wait_visible(window, qapp); drain_background(window, qapp)
        settle_presentation(window, qapp)
        cache = original._display_surface_cache
        first, last = window._visible_row_range()
        initial_seen = {event['row'] for event in events}
        resident = [entry for entry in window.item_model.items
                    if provider.has_memory_thumbnail(entry, window.thumbnail_render_spec)]
        assert len(resident) == (180 if screens < 0 else len(provider._cache))
        assert len(window.item_model._thumbnail_images) == len(resident)
        assert all(window.item_model._has_compatible_thumbnail(entry, window.thumbnail_render_spec.cache_token)
                   for entry in resident)
        assert len(resident) > len(initial_seen)
        # Warm preparation must not mark an unseen source as actually displayed.
        unseen = window.item_model.item_at(last+1)
        key = (provider._path_key(unseen.path), window.thumbnail_render_spec.cache_token, unseen.thumbnail_revision)
        assert key not in provider._read_cache_keys
        before = provider.cache_statistics()
        images_before = provider._cache_bytes
        surfaces_before = cache.byte_cost
        assert provider.artifact_memory.role_bytes['model'] == images_before
        assert provider.memory_cache_bytes <= provider.memory_cache_limit_bytes
        bar = window.list_view.verticalScrollBar()
        grid = window.list_view.gridSize().height()
        events.clear()
        # Partial row + small movement, no event-loop/wait/grab of the target
        # before the first recorded paint. Both lie inside the prewarmed band.
        movement_ms = []
        for position in (grid-1, grid+1, 2*grid-1, 2*grid+1):
            started = perf_counter()
            bar.setValue(position)
            window.list_view.viewport().grab()
            movement_ms.append((perf_counter()-started)*1000)
        exposed = [event for event in events if event['row'] not in initial_seen]
        assert exposed and all(e['model'] and e['ram'] for e in exposed)
        assert all(e['hits'] == 1 and e['preparations'] == 0 for e in exposed)
        assert key in provider._read_cache_keys, {
            'row':last+1, 'exposed':exposed, 'initial_seen':sorted(initial_seen),
            'keys_for_pixel': list(provider._pixel_cache_keys.get(int(provider._cache[key].cacheKey()), ())),
            'read_rows':[window.item_model.row_for_path(k[0]) for k in provider._read_cache_keys],
            'bound':cache._memory_owner is provider,
        }
        assert provider.cache_statistics()['generated'] == before['generated']
        assert provider.cache_statistics()['disk_hit'] == before['disk_hit']
        assert provider._cache_bytes == images_before
        assert cache.byte_cost == surfaces_before
        # Large jump and rapid burst may exceed the bounded surface band. When
        # the source is resident, every first delegate call still sees pixels.
        if screens < 0:
            initial_seen.update(e['row'] for e in events)
            events.clear()
            for position in (bar.maximum(), grid*11+1, grid*17-1, 0):
                bar.setValue(min(position, bar.maximum()))
                window.list_view.viewport().grab()
            new = [event for event in events if event['row'] not in initial_seen]
            assert new and all(e['model'] and e['ram'] for e in new)
            assert provider.cache_statistics()['generated'] == before['generated']
            assert provider.cache_statistics()['disk_hit'] == before['disk_hit']
            # A return without another event turn reuses the original surface.
            prep = cache.preparations
            window.list_view.viewport().grab()
            assert cache.preparations == prep
        assert window._thumbnail_presentation.maximum_slice_ms < 100  # Catch blocking whole-list preparation.
        (tmp_path/'measurements.json').write_text(json.dumps(dict(
            dpr=window.devicePixelRatioF(), screens=screens, disk=disk, display=display,
            resident=len(resident), model_entries=len(window.item_model._thumbnail_images),
            first_paints=exposed, movement_ms=movement_ms,
            source_bytes=images_before, prepared_surface_bytes=surfaces_before,
            memory=provider.browser_memory_diagnostics(),
            maximum_slice_ms=window._thumbnail_presentation.maximum_slice_ms,
        ), indent=2), encoding='utf-8')
    finally:
        window.close(); qapp.processEvents()


def test_unseen_publication_is_quiet_shared_and_evicted_with_source(tmp_path, qapp):
    root = tmp_path/'images'
    fixture_listing(root, 90)
    window = make_browser(tmp_path, root, qapp, -1, False)
    try:
        wait_visible(window, qapp); drain_background(window, qapp)
        settle_presentation(window, qapp)
        provider = window.thumbnail_provider
        first, last = window._visible_row_range()
        row = 80
        assert row > last
        item = window.item_model.item_at(row)
        spec = window.thumbnail_render_spec
        key = (provider._path_key(item.path), spec.cache_token, item.thumbnail_revision)
        image = provider._cache[key]
        assert key not in provider._read_cache_keys
        model = window.item_model
        model._thumbnail_images.pop(model._key(item.path))
        model._thumbnail_signatures.pop(model._key(item.path))
        changes = []
        model.dataChanged.connect(lambda a,b,roles: changes.append((a.row(),b.row(),roles)))
        bytes_before = provider.memory_cache_bytes
        window._thumbnail_presentation._resident_ready(str(item.path), window._generation,
                                                     spec.cache_token, item.thumbnail_revision)
        assert model._has_compatible_thumbnail(item, spec.cache_token)
        assert int(model._thumbnail_images[model._key(item.path)].cacheKey()) == int(image.cacheKey())
        assert not changes and provider.memory_cache_bytes == bytes_before
        assert key not in provider._read_cache_keys
        # Explicit source/geometry invalidation cannot publish a stale queued task.
        surface_cache = window.item_delegate._display_surface_cache
        window.item_delegate.prepare_thumbnail_image(image, window.devicePixelRatioF())
        assert surface_cache._by_source.get(int(image.cacheKey()))
        assert key not in provider._read_cache_keys
        window._thumbnail_presentation._resident_ready(str(item.path), window._generation-1,
                                                     spec.cache_token, item.thumbnail_revision)
        provider.reconcile_items((item,), ())
        assert model._key(item.path) not in model._thumbnail_images
        assert not provider.owns_memory_image(image)
        assert not window.item_delegate._display_surface_cache._by_source.get(int(image.cacheKey()))
    finally:
        window.close(); qapp.processEvents()


def test_presentation_yields_and_rejects_old_geometry_generation_after_pause(tmp_path, qapp):
    root = tmp_path/'images'
    fixture_listing(root, 90)
    window = make_browser(tmp_path, root, qapp, -1, False)
    try:
        wait_visible(window, qapp); drain_background(window, qapp)
        settle_presentation(window, qapp)
        controller = window._thumbnail_presentation
        provider = window.thumbnail_provider
        cache = window.item_delegate._display_surface_cache
        before = provider.cache_statistics()
        cache.clear()
        controller._model_changed()
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(controller._scan_row))
        qapp.processEvents()
        assert heartbeat and heartbeat[0] < window.item_model.rowCount()  # Reconciliation yields before the whole list.
        settle_presentation(window, qapp)
        assert provider.cache_statistics()['generated'] == before['generated']
        assert provider.cache_statistics()['disk_hit'] == before['disk_hit']
        # A queued plan contains no QImage/old model index. After invalidation it
        # borrows the current row/spec again rather than publishing stale pixels.
        old_generation = window._generation
        old_token = window.thumbnail_render_spec.cache_token
        item = window.item_model.item_at(70)
        provider.set_paused(True)
        controller._model_changed()
        qapp.processEvents()
        preparations = cache.preparations
        controller._pump()
        assert cache.preparations == preparations
        window.config.apply({'thumbnail_size':149, 'browser_cell_padding':7}, save=False)
        assert window.thumbnail_render_spec.cache_token != old_token
        retained_signature = window.item_model._thumbnail_signatures.get(window.item_model._key(item.path))
        controller._resident_ready(str(item.path), old_generation, old_token, item.thumbnail_revision)
        assert window.item_model._thumbnail_signatures.get(window.item_model._key(item.path)) == retained_signature
        provider.set_paused(False)
        wait_visible(window, qapp); drain_background(window, qapp)
        settle_presentation(window, qapp)
        assert controller._context[0] == window._generation
        assert controller._context[1] == window.thumbnail_render_spec.cache_token
        assert all(window.item_model._has_compatible_thumbnail(entry, window.thumbnail_render_spec.cache_token)
                   for entry in window.item_model.items)
        window.close()
        assert controller._closed and not controller._queue
    finally:
        from shiboken6 import isValid
        if isValid(window):
            window.close()
        qapp.processEvents()


def test_quiet_publication_does_not_resurrect_a_source_evicted_in_reference_callback(tmp_path, qapp, monkeypatch):
    root = tmp_path/'images'
    fixture_listing(root, 90)
    window = make_browser(tmp_path, root, qapp, -1, False)
    try:
        wait_visible(window, qapp); drain_background(window, qapp)
        settle_presentation(window, qapp)
        provider, model = window.thumbnail_provider, window.item_model
        item = model.item_at(80)
        path_key = model._key(item.path)
        model._thumbnail_images.pop(path_key)
        model._thumbnail_signatures.pop(path_key)
        original = provider.model_image_reference
        def evict_during_registration(path, image):
            if path == path_key and image is not None:
                provider.reconcile_items((item,), ())
            original(path, image)
        monkeypatch.setattr(provider, 'model_image_reference', evict_during_registration)
        window._thumbnail_presentation._resident_ready(str(item.path), window._generation,
            window.thumbnail_render_spec.cache_token, item.thumbnail_revision)
        assert path_key not in model._thumbnail_images
        assert path_key not in model._thumbnail_signatures
        assert path_key not in provider._orphan_model_images
        assert ('model',path_key) not in provider.artifact_memory.owners
    finally:
        window.close(); qapp.processEvents()


@pytest.mark.parametrize('eviction', ['source', 'budget', 'clear'])
def test_surface_registration_cancellation_balances_ledger_repeatedly(tmp_path, qapp, monkeypatch, eviction):
    from app.thumbnail_provider import ThumbnailLoadResult
    root = tmp_path/'images'
    fixture_listing(root, 90)
    window = make_browser(tmp_path, root, qapp, -1, False)
    try:
        wait_visible(window, qapp); drain_background(window, qapp)
        settle_presentation(window, qapp)
        window._thumbnail_presentation.shutdown()
        provider = window.thumbnail_provider
        cache = window.item_delegate._display_surface_cache
        cache.clear()
        item = window.item_model.item_at(80)
        key = (provider._path_key(item.path), window.thumbnail_render_spec.cache_token, item.thumbnail_revision)
        image = QImage(provider._cache[key])
        original = provider._ensure_display_headroom
        for repeat in range(3):
            provider.configure_browser_memory(mode='auto', limit_bytes=128*1024**2,
                desired_bytes=128*1024**2, group_capacity_bytes=128*1024**2, reason='ready')
            window._generation = provider.begin_generation()
            provider._consume_thumbnail_finished(str(item.path), window._generation,
                window.thumbnail_render_spec.cache_token, item.thumbnail_revision,
                ThumbnailLoadResult(image, persist_to_disk=False))
            assert provider.owns_memory_image(image)
            entered = []
            def ensure(extra=0):
                if extra and not entered:
                    entered.append(True)
                    if eviction == 'source':
                        provider.reconcile_items((item,), ())
                    else:
                        cache.clear()
                return original(extra)
            if eviction == 'budget':
                def pressure_grant():
                    entered.append(True)
                    provider.configure_browser_memory(mode='auto', limit_bytes=0,
                        desired_bytes=0, group_capacity_bytes=0, reason='synthetic-pressure')
                monkeypatch.setattr(provider, '_ensure_display_headroom', original)
                provider._browser_memory_desired_bytes = 0
                provider._display_growth_asked = 0
                monkeypatch.setattr(provider, '_display_budget_callback', pressure_grant)
            else:
                monkeypatch.setattr(provider, '_ensure_display_headroom', ensure)
            surface = window.item_delegate.prepare_thumbnail_image(image, window.devicePixelRatioF())
            assert entered and not surface.image.isNull()
            assert provider.owns_memory_image(image) is (eviction == 'clear')
            assert not cache._items and not cache._by_source and cache.byte_cost == 0
            cache.clear()
            assert not [owner for owner in provider.artifact_memory.owners if owner[0] == 'surface']
            assert provider.artifact_memory.role_bytes.get('surface', 0) == 0
            if eviction == 'budget':
                assert provider.memory_cache_bytes == 0
    finally:
        window.close(); qapp.processEvents()


def test_surface_registration_does_not_overwrite_reentrant_replacement(qapp, monkeypatch):
    from PySide6.QtCore import QRectF
    from app.browser_item_delegate import BrowserDisplaySurfaceCache
    from app.thumbnail_provider import BrowserThumbnailProvider
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    cache = BrowserDisplaySurfaceCache()
    cache.bind_memory(provider)
    image = QImage(64,64,QImage.Format.Format_RGB32); image.fill(0xff2468ac)
    original = provider._ensure_display_headroom
    entered = []
    def ensure(extra=0):
        if extra and not entered:
            entered.append(True)
            cache.clear()
            cache.prepare(image,QRectF(image.rect()),QRectF(0,0,32,32),1.)
        return original(extra)
    monkeypatch.setattr(provider, '_ensure_display_headroom', ensure)
    try:
        cache.prepare(image,QRectF(image.rect()),QRectF(0,0,32,32),1.)
        assert entered and cache.item_count == 1
        key, current = next(iter(cache._items.items()))
        reference = ('surface',id(cache),key)
        assert provider.artifact_memory.owners[reference] == int(current.cacheKey())
        assert provider.artifact_memory.role_bytes['surface'] == cache.byte_cost
        cache.clear()
        assert provider.memory_cache_bytes == 0 and not provider.artifact_memory.owners
    finally:
        cache.clear()
        provider.close()


def test_model_only_provisional_pixels_still_prepare_for_paint(tmp_path, qapp):
    from PySide6.QtCore import QRectF
    from app.browser_item_delegate import BrowserDisplaySurfaceCache
    from app.browser_model import BrowserItem, BrowserItemKind
    from app.thumbnail_provider import BrowserThumbnailProvider
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    cache = BrowserDisplaySurfaceCache(); cache.bind_memory(provider)
    model = BrowserItemModel(); model.bind_thumbnail_memory(provider)
    item = BrowserItem('preview.png',tmp_path/'preview.png',BrowserItemKind.IMAGE,1.)
    model.set_items((item,))
    image = QImage(64,64,QImage.Format.Format_RGB32); image.fill(0xff2468ac)
    try:
        assert model.set_thumbnail_image(item.path,image,low_resolution=True)
        assert not provider.owns_memory_image(image)
        surface = cache.prepare(image,QRectF(image.rect()),QRectF(0,0,32,32),1.)
        assert not surface.image.isNull() and cache.item_count == 1
        assert surface.image.pixelColor(0,0) == image.pixelColor(0,0)
        assert provider.artifact_memory.role_bytes['surface'] == cache.byte_cost
        cache.clear()
        assert provider.artifact_memory.role_bytes.get('surface',0) == 0
        assert model._thumbnail_images
        model.clear_thumbnails()
        assert provider.memory_cache_bytes == 0
    finally:
        cache.clear(); model.clear_thumbnails()
        provider.close()
