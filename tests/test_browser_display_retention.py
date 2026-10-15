"""Display preparation must not displace reusable source thumbnails."""
from threading import Event
from time import monotonic, sleep
from pathlib import Path

import pytest

from PySide6.QtGui import QImage

from app.browser_item_delegate import BrowserDisplaySurfaceCache
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_thumbnail_surface import BrowserThumbnailDisplayPlan
from app.thumbnail_provider import BrowserThumbnailProvider, ThumbnailLoadResult
from app.thumbnail_render import ThumbnailRenderSpec


@pytest.mark.parametrize('raw', [
    '一覧/画像.png', './folder//image.png', 'folder/../image.png',
    '//server/share/image.png', '///server/share/image.png',
])
@pytest.mark.parametrize('as_path', [False, True])
def test_retention_paths_keep_string_and_path_identity(raw, as_path, qapp):
    # String inputs still need pathlib's separator/UNC interpretation. Already
    # parsed Paths can pass through directly on the scroll hot path.
    item = BrowserItem('image', Path(raw), BrowserItemKind.IMAGE, 1.)
    value = item.path if as_path else raw
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    model = BrowserItemModel()
    model.set_items([item])
    model.bind_thumbnail_memory(provider)
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0xff2468ac)
    try:
        entries = [(value, item.thumbnail_revision, 0)]
        provider.set_cache_retention_priorities(entries)
        provider.set_display_retention_priorities(entries)
        identity = (provider._path_key(item.path), item.thumbnail_revision)
        assert provider._cache_retention_rank == {identity: 0}
        assert provider._display_retention_rank == {identity: 0}
        # Provisional model pixels have no provider source: retention must keep
        # the requested orphan, then release it when it leaves the retained set.
        assert model.set_thumbnail_image(item.path, image, request_token=64)
        assert model.retain_thumbnail_images([value]) == 0
        assert model._has_compatible_thumbnail(item, 64)
        assert model.retain_thumbnail_images([]) == image.sizeInBytes()
        assert not model._thumbnail_images
    finally:
        provider.close()


def test_display_pressure_preserves_sources_and_next_viewport(tmp_path, qapp):
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    provider.enable_retained_display()
    cache = BrowserDisplaySurfaceCache()
    cache.bind_memory(provider)
    limit = 128 * 1024**2
    provider.configure_browser_memory(mode='128', limit_bytes=limit,
        desired_bytes=limit, group_capacity_bytes=limit, reason='ready')
    plan = BrowserThumbnailDisplayPlan((4, 3, 127, 180), 'fit', 2.5)
    spec = ThumbnailRenderSpec.from_settings(512, 'portrait_1_sqrt2', 'letterbox')
    provider.set_display_plan(plan)
    items = [BrowserItem(f'{i}.png', tmp_path/f'{i}.png', BrowserItemKind.IMAGE, 1.)
             for i in range(70)]
    images = []
    provider.set_cache_retention_priorities([
        (item.path, item.thumbnail_revision, 0 if i < 4 else 1)
        for i, item in enumerate(items[:8])])
    try:
        generation = provider.begin_generation()
        # Production completion order: each source brings its completed display
        # pixels, while the listing continues to fill in the background.
        for item in items:
            image = QImage(512, 768, QImage.Format.Format_RGB32)
            image.fill(0xff2468ac)
            images.append(image)
            provider._consume_thumbnail_finished(str(item.path), generation, spec.cache_token,
                item.thumbnail_revision, ThumbnailLoadResult(image, persist_to_disk=False))
            surfaces = plan.render(image, Event())
            if provider._store_display_artifact(image, plan, surfaces):
                cache.adopt(image, plan, surfaces)
            assert provider.memory_cache_bytes <= limit
        # Raw sources use 105 MiB: the entire listing fits, including the next
        # viewport. Optional distant display variants must yield first.
        assert all(provider.owns_memory_image(image) for image in images)
        assert provider.cache_statistics()['memory_cache_evictions'] == 0
        assert all(cache.has_plan(image, plan) for image in images[:8])
        # Reprioritize to an unvisited range, prepare it, then reverse. Neither
        # movement requires original-file or disk-cache reads.
        for start in (40, 62, 20, 0):
            near = items[start:start+8]
            provider.set_cache_retention_priorities([
                (item.path, item.thumbnail_revision, 0 if i < 4 else 1)
                for i, item in enumerate(near)])
            for image in images[start:start+8]:
                surfaces = provider.display_artifact(image, plan) or plan.render(image, Event())
                assert provider._store_display_artifact(image, plan, surfaces)
                cache.adopt(image, plan, surfaces)
            assert all(cache.has_plan(image, plan) for image in images[start:start+8])
            assert all(provider.owns_memory_image(image) for image in images)
            assert provider.memory_cache_bytes <= limit
        provider.configure_browser_memory(mode='128', limit_bytes=0,
            desired_bytes=0, group_capacity_bytes=0, reason='pressure')
        assert not provider.artifact_memory.owners
        assert not provider._display_pixel_rank and not provider._surface_reference_counts
        assert not any(provider._display_rank_buckets.values())
        assert cache.byte_cost == 0
    finally:
        cache.clear()
        provider.close()


def test_new_visible_source_alias_protects_shared_display_pixels(tmp_path, qapp):
    from tests.test_browser_thumbnail_prerender import provider_image
    provider, item, image, spec = provider_image(tmp_path)
    cache = BrowserDisplaySurfaceCache(); cache.bind_memory(provider)
    alias = BrowserItem('alias.png', tmp_path/'alias.png', BrowserItemKind.IMAGE, 1.)
    other = BrowserItem('other.png', tmp_path/'other.png', BrowserItemKind.IMAGE, 1.)
    plan = BrowserThumbnailDisplayPlan((4, 3, 127, 180), 'fit', 2.5)
    provider.set_display_plan(plan)
    provider.set_display_retention_priorities([(alias.path, alias.thumbnail_revision, 0)])
    try:
        generation = provider.begin_generation()
        provider._consume_thumbnail_finished(str(item.path), generation, spec.cache_token,
            item.thumbnail_revision, ThumbnailLoadResult(image, persist_to_disk=False))
        surfaces = plan.render(image, Event())
        assert provider._store_display_artifact(image, plan, surfaces)
        cache.adopt(image, plan, surfaces)
        provider._consume_thumbnail_finished(str(alias.path), generation, spec.cache_token,
            alias.thumbnail_revision, ThumbnailLoadResult(image, persist_to_disk=False))
        other_image = image.copy()
        provider._consume_thumbnail_finished(str(other.path), generation, spec.cache_token,
            other.thumbnail_revision, ThumbnailLoadResult(other_image, persist_to_disk=False))
        other_surfaces = plan.render(other_image, Event())
        assert provider._store_display_artifact(other_image, plan, other_surfaces)
        cache.adopt(other_image, plan, other_surfaces)
        limit = provider.memory_cache_bytes - sum(int(surface.sizeInBytes()) for _, surface in other_surfaces)
        provider.configure_browser_memory(mode='128', limit_bytes=limit,
            desired_bytes=limit, group_capacity_bytes=limit, reason='pressure')
        assert cache.has_plan(image, plan)
        assert not cache.has_plan(other_image, plan)
        assert provider.owns_memory_image(image) and provider.owns_memory_image(other_image)
    finally:
        cache.clear(); provider.close()


def test_first_paint_result_survives_gui_index_clear(tmp_path, qapp):
    from tests.test_browser_thumbnail_prerender import provider_image
    from app.browser_thumbnail_surface import thumbnail_image_rects
    from PySide6.QtCore import QRect
    provider, item, image, spec = provider_image(tmp_path)
    cache = BrowserDisplaySurfaceCache(); cache.bind_memory(provider)
    plan = BrowserThumbnailDisplayPlan((4, 3, 127, 180), 'center_crop', 2.5)
    provider.set_display_plan(plan)
    try:
        generation = provider.begin_generation()
        provider._consume_thumbnail_finished(str(item.path), generation, spec.cache_token,
            item.thumbnail_revision, ThumbnailLoadResult(image, persist_to_disk=False))
        target, source = thumbnail_image_rects(QRect(*plan.frame), image.size(),
            plan.display_mode, device_pixel_ratio=plan.dpr)
        result = cache.prepare(image, source, target, plan.dpr)
        assert not result.cache_hit
        used = provider.memory_cache_bytes
        cache.clear()
        assert provider.memory_cache_bytes == used
        again = cache.prepare(image, source, target, plan.dpr)
        assert again.cache_hit and again.image.cacheKey() == result.image.cacheKey()
        assert cache.preparations == 1
    finally:
        cache.clear(); provider.close()


def test_scroll_under_display_pressure_keeps_acquired_images(tmp_path, qapp):
    """Real Browser at 250%: pre-render pressure cannot punch raw-cache holes."""
    from app.browser_window import BrowserWindow
    from app.config_manager import ConfigManager
    from tests.test_browser_thumbnail_first_exposure import fixture_listing, settle_presentation
    from tests.test_browser_retained_scroll_qt import drain_background
    from tests.test_browser_thumbnail_ram_restore import wait_visible
    root = tmp_path / 'images'
    fixture_listing(root, 180)
    config = ConfigManager(tmp_path/'config.json')
    config.load()
    config.apply({'last_browser_path': str(root), 'browser_thumbnail_background_screens': -1,
                  'browser_thumbnail_memory_mode': '256',
                  'thumbnail_disk_cache_enabled': False}, save=False)
    window = BrowserWindow(config_manager=config)
    window.resize(900, 700)
    window.show()
    try:
        assert window.wait_for_scan()
        if window.list_view.devicePixelRatioF() != 2.5:
            pytest.skip('Run with QT_SCALE_FACTOR=2.5 for display-pressure coverage')
        wait_visible(window, qapp)
        drain_background(window, qapp)
        settle_presentation(window, qapp)
        provider = window.thumbnail_provider
        controller = window._thumbnail_presentation
        assert len(provider._cache) == 180
        before = provider.cache_statistics()
        missing = controller.pre_scroll_missing
        missed_moves = []
        bar = window.list_view.verticalScrollBar()
        step = window.list_view.gridSize().height()
        # Each movement exposes unvisited cells beyond the initial preparation
        # band, followed by reversal and a repeated pass. Real Qt paints run.
        for positions in (range(1, 25), range(23, -1, -1), range(1, 25)):
            for row in positions:
                old_missing = controller.pre_scroll_missing
                bar.setValue(row * step)
                if controller.pre_scroll_missing != old_missing:
                    missed_moves.append((row, controller.pre_scroll_missing-old_missing))
                window.list_view.viewport().grab()
                until = monotonic() + .016
                while monotonic() < until:
                    qapp.processEvents()
                    sleep(.001)
                assert len(provider._cache) == 180
                assert provider.memory_cache_bytes <= provider.memory_cache_limit_bytes
        after = provider.cache_statistics()
        assert after['generated'] == before['generated']
        assert after['disk_hit'] == before['disk_hit']
        assert after['memory_cache_evictions'] == before['memory_cache_evictions'] == 0
        assert controller.pre_scroll_missing == missing, missed_moves
        settle_presentation(window, qapp)
        # A saturated display tier must settle rather than requeueing discarded
        # distant work on a 1-ms timer forever.
        slices = controller.slices
        until = monotonic() + .1
        while monotonic() < until:
            qapp.processEvents()
            sleep(.001)
        assert controller.slices == slices
    finally:
        window.close()
        qapp.processEvents()
