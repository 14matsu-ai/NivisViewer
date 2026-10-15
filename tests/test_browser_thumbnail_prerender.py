"""Completed worker pixels and preparation before the viewport moves."""
from threading import Event, get_ident
from time import monotonic, sleep

import pytest
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QAbstractSlider, QStyledItemDelegate

from app.browser_item_delegate import BrowserDisplaySurfaceCache
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.browser_thumbnail_surface import (
    BrowserThumbnailDisplayPlan, surface_geometry_key, thumbnail_image_rects,
)
from app.thumbnail_provider import BrowserThumbnailProvider, ThumbnailLoadResult
from app.thumbnail_render import ThumbnailRenderSpec
from tests.test_browser_thumbnail_first_exposure import fixture_listing, settle_presentation
from tests.test_browser_retained_scroll_qt import make_browser, drain_background
from tests.test_browser_thumbnail_ram_restore import wait_visible


def test_first_two_system_wheels_use_prepared_association_badges(tmp_path, qapp, monkeypatch):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    from app.shell_icon_provider import ShellAssociatedIconProvider
    root = tmp_path/'images'
    fixture_listing(root, 180)
    # Keep a second association out of the initial viewport. PNG signatures
    # remain decodable under the supported WebP extension in this fixture.
    for row, path in enumerate(sorted(root.glob('*.png'))):
        if row >= 36:
            path.rename(path.with_suffix('.webp'))
    queries = []
    def query(extension, *, folder, physical_size):
        queries.append((extension, physical_size))
        image = QImage(physical_size, physical_size, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        return image
    monkeypatch.setattr(ShellAssociatedIconProvider, '_windows_image', staticmethod(query))
    old_lines = qapp.wheelScrollLines()
    qapp.setWheelScrollLines(12)
    window = make_browser(tmp_path, root, qapp, 5, False)
    try:
        wait_visible(window, qapp)
        drain_background(window, qapp)
        settle_presentation(window, qapp)
        assert window._visible_row_range()[1] < 36
        assert any(extension == '.webp' for extension, _ in queries)
        # A cached badge must never return to the shell in first-exposure paint.
        unexpected_queries = []
        def unexpected(*args, **kwargs):
            unexpected_queries.append(args)
            return query(*args, **kwargs)
        monkeypatch.setattr(ShellAssociatedIconProvider, '_windows_image', staticmethod(unexpected))
        cache = window.item_delegate._display_surface_cache
        before = cache.preparations
        for _ in range(2):
            event = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(), QPoint(0, -120),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            qapp.sendEvent(window.list_view.viewport(), event)
            qapp.processEvents()
        assert window.list_view.verticalScrollBar().value() > 0
        first, last = window._visible_row_range()
        assert any(window.item_model.item_at(row).path.suffix == '.webp'
                   for row in range(first, last+1))
        assert not unexpected_queries
        assert cache.preparations == before
    finally:
        qapp.setWheelScrollLines(old_lines)
        window.close(); qapp.processEvents()


def provider_image(tmp_path):
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    provider.enable_retained_display()
    provider.configure_browser_memory(mode='128', limit_bytes=128*1024**2,
        desired_bytes=128*1024**2, group_capacity_bytes=128*1024**2, reason='ready')
    item = BrowserItem('preview.png', tmp_path/'preview.png', BrowserItemKind.IMAGE, 1.)
    image = QImage(181, 256, QImage.Format.Format_RGB32)
    image.fill(0xff2468ac)
    spec = ThumbnailRenderSpec.from_settings(180, 'portrait_1_sqrt2', 'letterbox')
    return provider, item, image, spec


@pytest.mark.parametrize('dpr', [1., 1.25, 1.5, 2., 2.5, 3.5])
@pytest.mark.parametrize('mode', ['fit', 'center_crop'])
def test_worker_plan_covers_fractional_dpr_offsets_without_duplicate_images(dpr, mode):
    plan = BrowserThumbnailDisplayPlan((4, 3, 127, 180), mode, dpr)
    image = QImage(181, 256, QImage.Format.Format_RGB32)
    image.fill(0xff2468ac)
    surfaces = plan.render(image, Event())
    keys = {key for key, _ in surfaces}
    # Scrolling changes physical placement, never the size/crop of the image.
    assert len(keys) == len(surfaces) == 1
    for x in range(9):
        for y in range(-9, 10):
            target, source = thumbnail_image_rects(QRect(*plan.frame).translated(x, y),
                image.size(), mode, device_pixel_ratio=dpr)
            assert surface_geometry_key(source, target, dpr) in keys
    assert all(surface.width() == key[4] and surface.height() == key[5]
               and surface.devicePixelRatio() == dpr for key, surface in surfaces)


def test_display_only_worker_rebuilds_cleared_pixels_without_gui_resize_or_source_read(tmp_path, qapp, monkeypatch):
    provider, item, image, spec = provider_image(tmp_path)
    cache = BrowserDisplaySurfaceCache(); cache.bind_memory(provider)
    plan = BrowserThumbnailDisplayPlan((4, 3, 127, 180), 'fit', 2.5)
    calls = []
    original = BrowserThumbnailDisplayPlan.render
    def render(self, source, cancelled):
        calls.append(get_ident())
        return original(self, source, cancelled)
    monkeypatch.setattr(BrowserThumbnailDisplayPlan, 'render', render)
    provider.set_display_plan(plan)
    provider.display_surfaces_ready.connect(lambda _p, _g, _t, _r, raw, ready_plan, surfaces:
        cache.adopt(raw, ready_plan, surfaces))
    try:
        generation = provider.begin_generation()
        provider._consume_thumbnail_finished(str(item.path), generation, spec.cache_token,
            item.thumbnail_revision, ThumbnailLoadResult(image, persist_to_disk=False))
        before = provider.cache_statistics()
        for _ in range(3):
            cache.clear()
            assert provider.request_display_surfaces(item, spec, image, generation=generation,
                                                    priority=ThumbnailPriority.READ_AHEAD)
            assert provider.request_display_surfaces(item, spec, image, generation=generation,
                                                    priority=ThumbnailPriority.READ_AHEAD)
            assert provider.display_pending_count == 1
            assert provider.wait_for_done(5000)
            qapp.processEvents()
            assert provider.display_pending_count == 0 and cache.has_plan(image, plan)
            assert cache.preparations == 0
            assert provider.artifact_memory.role_bytes['surface'] == cache.byte_cost
        assert len(calls) == 3 and all(thread != get_ident() for thread in calls)
        assert provider.cache_statistics()['generated'] == before['generated']
        assert provider.cache_statistics()['disk_hit'] == before['disk_hit']
    finally:
        cache.clear(); provider.close()


def test_completed_display_artifact_survives_gui_index_clear_and_releases_with_final_source_alias(tmp_path, qapp):
    provider, item, image, spec = provider_image(tmp_path)
    cache = BrowserDisplaySurfaceCache(); cache.bind_memory(provider)
    plan = BrowserThumbnailDisplayPlan((4, 3, 127, 180), 'fit', 2.5)
    provider.set_display_plan(plan)
    alias = BrowserItem('alias.png', tmp_path/'alias.png', BrowserItemKind.IMAGE, 1.)
    surfaces = plan.render(image, Event())
    try:
        generation = provider.begin_generation()
        for entry in (item, alias):
            provider._consume_thumbnail_finished(str(entry.path), generation, spec.cache_token,
                entry.thumbnail_revision, ThumbnailLoadResult(image, persist_to_disk=False))
        assert provider._store_display_artifact(image, plan, surfaces)
        cache.adopt(image, plan, surfaces)
        retained = provider.memory_cache_bytes
        cache.clear()
        assert provider.memory_cache_bytes == retained
        assert provider.artifact_memory.role_bytes.get('surface', 0) == 0
        assert provider.display_artifact(image, plan) is surfaces
        cache.adopt(image, plan, provider.display_artifact(image, plan))
        assert cache.has_plan(image, plan) and cache.preparations == 0
        assert provider.memory_cache_bytes == retained
        provider.reconcile_items((item, alias), (alias,))
        assert provider.display_artifact(image, plan) is surfaces and cache.has_plan(image, plan)
        provider.configure_browser_memory(mode='128', limit_bytes=0,
            desired_bytes=0, group_capacity_bytes=0, reason='synthetic-pressure')
        assert not provider._display_artifacts and not provider.artifact_memory.owners
        assert provider.memory_cache_bytes == 0 and cache.byte_cost == 0
    finally:
        cache.clear(); provider.close()


@pytest.mark.parametrize('cancel', ['source', 'budget', 'generation', 'plan'])
def test_source_artifact_publication_rechecks_reentrant_ownership(tmp_path, qapp, monkeypatch, cancel):
    provider, item, image, spec = provider_image(tmp_path)
    plan = BrowserThumbnailDisplayPlan((4, 3, 127, 180), 'fit', 2.5)
    surfaces = plan.render(image, Event())
    original = provider._ensure_display_headroom
    try:
        for _ in range(3):
            provider.set_display_plan(plan)
            provider.configure_browser_memory(mode='128', limit_bytes=128*1024**2,
                desired_bytes=128*1024**2, group_capacity_bytes=128*1024**2, reason='ready')
            generation = provider.begin_generation()
            provider._consume_thumbnail_finished(str(item.path), generation, spec.cache_token,
                item.thumbnail_revision, ThumbnailLoadResult(image, persist_to_disk=False))
            entered = []
            def pressure(extra=0):
                if extra and not entered:
                    entered.append(True)
                    if cancel == 'source': provider.reconcile_items((item,), ())
                    elif cancel == 'budget': provider.configure_browser_memory(mode='128', limit_bytes=0,
                        desired_bytes=0, group_capacity_bytes=0, reason='synthetic-pressure')
                    elif cancel == 'generation': provider.begin_generation()
                    else: provider.set_display_plan(BrowserThumbnailDisplayPlan(plan.frame, 'center_crop', 2.5))
                return original(extra)
            monkeypatch.setattr(provider, '_ensure_display_headroom', pressure)
            assert not provider._store_display_artifact(image, plan, surfaces) and entered
            assert not provider._display_artifacts
            assert not [owner for owner in provider.artifact_memory.owners if owner[0] == 'display']
            assert provider.artifact_memory.role_bytes.get('display', 0) == 0
    finally:
        provider.close()


def test_source_worker_publishes_finished_display_pixels_before_model_ready(tmp_path, qapp):
    provider, item, image, spec = provider_image(tmp_path)
    cache = BrowserDisplaySurfaceCache(); cache.bind_memory(provider)
    plan = BrowserThumbnailDisplayPlan((4, 3, 127, 180), 'fit', 2.5)
    provider.set_display_plan(plan)
    provider._loader = lambda *_: image
    order = []
    def display(_path, _generation, _token, _revision, raw, ready_plan, surfaces):
        cache.adopt(raw, ready_plan, surfaces)
        order.append('display')
    def ready(_path, _generation, raw):
        assert cache.has_plan(raw, plan)
        order.append('model')
    provider.display_surfaces_ready.connect(display)
    provider.thumbnail_ready.connect(ready)
    try:
        generation = provider.begin_generation()
        assert provider.request(item, spec, generation=generation)
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        assert order == ['display', 'model']
    finally:
        cache.clear(); provider.close()


@pytest.mark.parametrize('route', ['system', 'custom', 'pixel', 'action', 'drag'])
def test_scroll_preparation_precedes_qt_value_commit_without_changing_motion(qapp, route):
    from tests.test_browser_wheel_scroll import _scroll_view, _wheel_event
    view = _scroll_view(qapp, mode='pixels' if route == 'custom' else 'system')
    bar = view.verticalScrollBar()
    calls = []
    view.scrollPreparing.connect(lambda old, new: calls.append((bar.value(), old, new)))
    try:
        before = bar.value()
        if route == 'pixel': view.wheelEvent(_wheel_event(view.viewport(), pixel_y=-13, angle_y=-120))
        elif route in ('system', 'custom'): view.wheelEvent(_wheel_event(view.viewport(), angle_y=-120))
        elif route == 'action': bar.triggerAction(QAbstractSlider.SliderAction.SliderPageStepAdd)
        else:
            bar.setSliderDown(True)
            bar.setSliderPosition(before+23)
            bar.setSliderDown(False)
        assert bar.value() != before
        assert calls and all(current == old and old != new for current, old, new in calls)
        assert calls[-1][2] == bar.value()
        if route == 'custom': assert bar.value()-before == 96
        if route == 'drag': assert bar.value()-before == 23
    finally:
        view.close(); qapp.processEvents()


@pytest.mark.parametrize('direction', [Qt.LayoutDirection.LeftToRight, Qt.LayoutDirection.RightToLeft])
def test_exposure_plan_matches_future_cells_before_position_changes(tmp_path, qapp, direction):
    root = tmp_path/'images'; fixture_listing(root, 120)
    window = make_browser(tmp_path, root, qapp, -1, False)
    try:
        wait_visible(window, qapp); drain_background(window, qapp); settle_presentation(window, qapp)
        view, delegate, model = window.list_view, window.item_delegate, window.item_model
        view.setLayoutDirection(direction); qapp.processEvents()
        bar = view.verticalScrollBar(); grid = view.gridSize().height()
        bar.setValue(grid*12+8)
        checks = []
        def prepared(old, new):
            assert bar.value() == old
            delta = new-old
            viewport = view.viewport().rect()
            strip = (QRect(0, max(0, viewport.height()-delta), viewport.width(), min(delta, viewport.height()))
                     if delta > 0 else QRect(0, 0, viewport.width(), min(-delta, viewport.height())))
            expected = set()
            for row in range(model.rowCount()):
                index = model.index(row, 0)
                future = view.visualRect(index).translated(0, old-new)
                if future.intersects(strip):
                    image = index.data(BrowserItemModel.ThumbnailImageRole)
                    frame = delegate.grid_metrics.thumbnail_frame_rect(future)
                    expected.add((int(image.cacheKey()), *frame.getRect(),
                                  round(view.viewport().devicePixelRatioF(), 6), delegate.thumbnail_display_mode))
            assert expected == set(delegate._exposure_plans)
            checks.append(True)
        view.scrollPreparing.connect(prepared)
        for target in (bar.value()+1, bar.value()-1, grid*15+3, 0, bar.maximum()):
            bar.setValue(target)
        assert len(checks) == 5
    finally:
        window.close(); qapp.processEvents()


@pytest.mark.parametrize('eviction', ['source', 'budget', 'clear'])
def test_worker_batch_reservation_does_not_resurrect_canceled_owners(tmp_path, qapp, monkeypatch, eviction):
    provider, item, image, spec = provider_image(tmp_path)
    cache = BrowserDisplaySurfaceCache(); cache.bind_memory(provider)
    plan = BrowserThumbnailDisplayPlan((4, 3, 127, 180), 'fit', 2.5)
    surfaces = plan.render(image, Event())
    original = provider._ensure_display_headroom
    try:
        for _ in range(3):
            provider.configure_browser_memory(mode='128', limit_bytes=128*1024**2,
                desired_bytes=128*1024**2, group_capacity_bytes=128*1024**2, reason='ready')
            generation = provider.begin_generation()
            provider._consume_thumbnail_finished(str(item.path), generation, spec.cache_token,
                item.thumbnail_revision, ThumbnailLoadResult(image, persist_to_disk=False))
            entered = []
            def pressure(extra=0):
                if extra and not entered:
                    entered.append(True)
                    if eviction == 'source': provider.reconcile_items((item,), ())
                    elif eviction == 'budget': provider.configure_browser_memory(mode='128', limit_bytes=0,
                        desired_bytes=0, group_capacity_bytes=0, reason='synthetic-pressure')
                    else: cache.clear()
                return original(extra)
            monkeypatch.setattr(provider, '_ensure_display_headroom', pressure)
            assert cache.adopt(image, plan, surfaces) == 0 and entered
            cache.clear()
            assert not cache._items and not cache._by_source and cache.byte_cost == 0
            assert not [owner for owner in provider.artifact_memory.owners if owner[0] == 'surface']
            assert provider.artifact_memory.role_bytes.get('surface', 0) == 0
    finally:
        cache.clear(); provider.close()


@pytest.mark.parametrize('cancel', ['generation', 'plan', 'source', 'close'])
def test_display_worker_rejects_stale_results_and_owns_native_until_done(tmp_path, qapp, monkeypatch, cancel):
    from shiboken6 import isValid
    provider, item, image, spec = provider_image(tmp_path)
    plan = BrowserThumbnailDisplayPlan((4, 3, 127, 180), 'fit', 2.5)
    entered, release = Event(), Event()
    original = BrowserThumbnailDisplayPlan.render
    def render(self, source, cancelled):
        entered.set()
        assert release.wait(5)
        return original(self, source, cancelled)
    monkeypatch.setattr(BrowserThumbnailDisplayPlan, 'render', render)
    provider.set_display_plan(plan)
    results = []
    provider.display_surfaces_ready.connect(lambda *args: results.append(args))
    try:
        generation = provider.begin_generation()
        provider._consume_thumbnail_finished(str(item.path), generation, spec.cache_token,
            item.thumbnail_revision, ThumbnailLoadResult(image, persist_to_disk=False))
        assert provider.request_display_surfaces(item, spec, image, generation=generation,
                                                priority=ThumbnailPriority.READ_AHEAD)
        assert entered.wait(2)
        worker = next(p.worker for p in provider._pending.values() if p.worker.display_only)
        if cancel == 'generation': provider.begin_generation()
        elif cancel == 'plan': provider.set_display_plan(BrowserThumbnailDisplayPlan(plan.frame, 'center_crop', 2.5))
        elif cancel == 'source': provider.reconcile_items((item,), ())
        else: provider.close(0)
        provider._collect_owned_workers()
        assert worker in provider._owned_workers and isValid(worker)
        release.set()
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        provider._collect_owned_workers()
        assert not results and not provider.display_pending_count
        assert worker.loader is None
        assert not provider._owned_workers
    finally:
        release.set(); provider.close(5000)


def test_continuous_unvisited_rows_use_finished_worker_images_and_pre_move_plans(tmp_path, qapp, monkeypatch):
    root = tmp_path/'images'; fixture_listing(root, 160)
    window = make_browser(tmp_path, root, qapp, -1, False)
    try:
        wait_visible(window, qapp); drain_background(window, qapp); settle_presentation(window, qapp)
        cache = window.item_delegate._display_surface_cache
        controller = window._thumbnail_presentation
        assert all(cache.has_plan(image, controller._plan) for image in window.item_model._thumbnail_images.values())
        import app.browser_item_delegate as delegate_module
        def no_gui_resize(*_): pytest.fail('Resident display pixels must already be complete')
        monkeypatch.setattr(delegate_module, 'prepare_display_thumbnail_surface', no_gui_resize)
        original = window.item_delegate
        seen, fresh = set(), []
        class Recorder(QStyledItemDelegate):
            def sizeHint(self, option, index): return original.sizeHint(option, index)
            def paint(self, painter, option, index):
                before = cache.preparations
                image = index.data(BrowserItemModel.ThumbnailImageRole)
                original.paint(painter, option, index)
                if index.row() not in seen:
                    fresh.append((index.row(), image is not None, cache.preparations-before))
                    seen.add(index.row())
        recorder = Recorder(window.list_view); window.list_view.setItemDelegate(recorder)
        window.list_view.viewport().grab()
        fresh.clear()
        bar = window.list_view.verticalScrollBar(); grid = window.list_view.gridSize().height()
        ordering = []
        window.list_view.scrollPreparing.connect(lambda old, new:
            ordering.append((bar.value(), old, new, len(original._exposure_plans))))
        stats = window.thumbnail_provider.cache_statistics()
        timer = QTimer(); timer.setInterval(8)
        moves = [grid*i for i in range(1, 29)] + [grid*i for i in range(27, 9, -1)] + [grid*35, grid*20, grid*36, 0]
        def tick():
            if moves: bar.setValue(moves.pop(0))
            else: timer.stop()
        timer.timeout.connect(tick); timer.start()
        deadline = monotonic()+10
        while timer.isActive() and monotonic() < deadline:
            qapp.processEvents(); sleep(.001)
        assert not timer.isActive()
        window.list_view.viewport().grab()
        assert len(fresh) > 80 and all(image and count == 0 for _, image, count in fresh)
        assert ordering and all(current == old and plans > 0 for current, old, _, plans in ordering)
        assert window.thumbnail_provider.cache_statistics()['generated'] == stats['generated']
        assert window.thumbnail_provider.cache_statistics()['disk_hit'] == stats['disk_hit']
        assert window.thumbnail_provider.memory_cache_bytes <= window.thumbnail_provider.memory_cache_limit_bytes
        assert controller.pre_scroll_calls > 20
        # Qt's native slider action also announces the future value before commit.
        ordering.clear()
        bar.triggerAction(QAbstractSlider.SliderAction.SliderSingleStepAdd)
        assert ordering and ordering[0][0] == ordering[0][1]
        # A new geometry has no completed artifact. Preserve pending work and
        # update its direction even when the visible row interval stays equal.
        window.thumbnail_provider.set_paused(True)
        original.configure(thumbnail_size=window.thumbnail_size+1, density=window.browser_display_density)
        bar.setValue(grid*20+8)
        controller._model_changed()
        band = controller._band
        bar.setValue(bar.value()+1)
        bar.setValue(bar.value()-1)
        assert controller._band == band
        assert controller._queue[0][0] == window._visible_row_range()[0]-1
    finally:
        window.close(); qapp.processEvents()
