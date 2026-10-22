"""Original ZipPla item-owned redisplay adapted to a joint Qt pixel budget."""
from types import SimpleNamespace
import pytest

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage

from app.browser_artifact_memory import BrowserArtifactMemory
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_item_delegate import BrowserDisplaySurfaceCache
from app.browser_thumbnail_memory_policy import MIB, GIB
from app.browser_thumbnail_memory_policy import MANAGED_ENTRY_GUARD
from app.thumbnail_provider import BrowserThumbnailProvider, ThumbnailLoadResult
from app.thumbnail_render import ThumbnailRenderSpec
from tests.test_browser_thumbnail_memory_integration_qt import app, controlled_broker
from tests.test_browser_navigation_window import make_window


@pytest.mark.parametrize('resume', ('near', 'grant', 'foreground', 'clear', 'generation', 'revision',
                                  'disk_epoch', 'prune', 'other_instance_prune', 'writer_busy', 'access_update',
                                  'prune_before_gui', 'far_admission'))
def test_nonresident_saved_read_waits_for_useful_admission(tmp_path, qapp, resume):
    from dataclasses import replace
    from PIL import Image
    from app.thumbnail_disk_cache import ThumbnailDiskCache

    disk = ThumbnailDiskCache(tmp_path/'cache')
    path = tmp_path/'未表示.png'
    Image.new('RGB', (512, 512), 'navy').save(path)
    info = path.stat()
    item = BrowserItem(path.name, path, BrowserItemKind.IMAGE, info.st_mtime,
                       file_size=info.st_size, modified_time_ns=info.st_mtime_ns)
    spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
    image = QImage(512, 512, QImage.Format.Format_RGB32)
    image.fill(0xff123456)
    assert disk.put(item, spec, image)
    if resume == 'prune_before_gui':
        read = disk.get_suitable
        def read_then_prune(*args, **kwargs):
            result = read(*args, **kwargs)
            if result is not None:
                disk.limit_bytes = 1
                assert disk.prune() >= 1
            return result
        disk.get_suitable = read_then_prune
    provider = BrowserThumbnailProvider(disk_cache=disk)
    provider.enable_retained_display()
    limit = 2*image.sizeInBytes()
    provider.configure_browser_memory(mode='128', limit_bytes=limit, desired_bytes=limit,
                                      group_capacity_bytes=GIB, reason='ready')
    try:
        # Two distant, previously painted images fill RAM. A far saved read
        # loses admission, while a next-screen read must outrank those images.
        for row in range(2):
            old = BrowserItem(str(row), tmp_path/f'{row}.png', BrowserItemKind.IMAGE, 1.0)
            provider._consume_thumbnail_finished(str(old.path), provider.generation, spec.cache_token,
                old.thumbnail_revision, ThumbnailLoadResult(image.copy()))
        provider.set_cache_retention_priorities([(item.path, item.thumbnail_revision, 3)])
        if resume == 'far_admission':
            assert provider.request_background_read(item, spec, generation=provider.generation) == 'memory-deferred'
            assert provider.pending_count == 0
            assert provider.cache_statistics()['disk_hit'] == 0
            assert provider.cache_statistics()['background_memory_deferrals'] == 1
            return
        assert provider.request_background(item, spec, generation=provider.generation) == 'queued'
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        assert not provider.has_memory_thumbnail(item, spec)
        before = provider.cache_statistics()
        assert before['disk_hit'] == before['background_self_evictions'] == 1
        if resume == 'prune_before_gui':
            assert provider.request_background(item, spec, generation=provider.generation) == 'queued'
            assert provider.wait_for_done(5000)
            qapp.processEvents()
            assert provider.cache_statistics()['generated'] == 1
            return
        for _ in range(10):
            assert provider.request_background_read(item, spec, generation=provider.generation) == 'settled'
        assert provider.cache_statistics()['disk_hit'] == 1
        assert provider.cache_statistics()['background_capacity_skips'] == 10
        if resume == 'access_update':
            revision = disk.admission_revision
            disk.flush_accesses()
            assert disk.admission_revision == revision
            assert provider.request_background_read(item, spec, generation=provider.generation) == 'settled'
            assert provider.cache_statistics()['disk_hit'] == 1
            return
        if resume == 'writer_busy':
            from threading import Event, Thread
            from time import perf_counter
            entered, release = Event(), Event()
            def hold_writer():
                with disk._lock:
                    entered.set()
                    assert release.wait(5)
            writer = Thread(target=hold_writer)
            writer.start()
            try:
                assert entered.wait(2)
                assert disk.admission_revision is None
                started = perf_counter()
                assert provider.request_background(item, spec, generation=provider.generation) == 'queued'
                assert perf_counter()-started < .1
            finally:
                release.set()
                writer.join(5)
            assert provider.wait_for_done(5000)
            qapp.processEvents()
            assert provider.cache_statistics()['disk_hit'] == 2
            return
        if resume in ('prune', 'other_instance_prune'):
            epoch = disk.write_epoch
            revision = disk.admission_revision
            evictor = (ThumbnailDiskCache(disk.cache_dir, limit_bytes=1)
                       if resume == 'other_instance_prune' else disk)
            try:
                evictor.limit_bytes = 1
                assert evictor.prune() >= 1
                assert disk.write_epoch == epoch
                assert disk.admission_revision != revision
                assert provider.request_background(item, spec, generation=provider.generation) == 'queued'
                assert provider.wait_for_done(5000)
                qapp.processEvents()
                assert provider.cache_statistics()['generated'] == 1
            finally:
                if evictor is not disk:
                    evictor.close()
            return
        if resume == 'near':
            provider.set_cache_retention_priorities([(item.path, item.thumbnail_revision, 1)])
        elif resume == 'grant':
            provider.configure_browser_memory(mode='128', limit_bytes=2*limit, desired_bytes=2*limit,
                                              group_capacity_bytes=GIB, reason='ready')
        elif resume == 'clear':
            provider.clear_memory_cache()
        elif resume == 'generation':
            provider.begin_generation()
        elif resume == 'revision':
            item = replace(item, created_time_ns=1)
        elif resume == 'disk_epoch':
            disk.invalidate_pending_writes()
        if resume == 'foreground':
            assert provider.request(item, spec, generation=provider.generation)
        elif resume == 'near':
            assert provider.request_background_read(item, spec, generation=provider.generation) == 'queued'
        else:
            assert provider.request_background(item, spec, generation=provider.generation) == 'queued'
        assert provider.wait_for_done(5000)
        qapp.processEvents()
        assert provider.cache_statistics()['disk_hit'] == 2
        assert provider.memory_cache_bytes <= provider.memory_cache_limit_bytes
        if resume in ('near', 'grant', 'clear', 'foreground'):
            assert provider.has_memory_thumbnail(item, spec)
    finally:
        provider.close()


@pytest.mark.parametrize('rank', (0, 1, 2))
def test_only_visible_display_can_reclaim_distant_sources(tmp_path, qapp, rank):
    from threading import Event
    from app.browser_thumbnail_surface import BrowserThumbnailDisplayPlan

    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    provider.enable_retained_display()
    model = BrowserItemModel()
    model.bind_thumbnail_memory(provider)
    surfaces = BrowserDisplaySurfaceCache()
    surfaces.bind_memory(provider)
    items = [BrowserItem(str(i), tmp_path/f'{i}.png', BrowserItemKind.IMAGE, 1.0) for i in range(3)]
    model.set_items(items)
    image = QImage(512, 512, QImage.Format.Format_RGB32)
    image.fill(0xff123456)
    limit = 3*image.sizeInBytes()
    provider.configure_browser_memory(mode='128', limit_bytes=limit, desired_bytes=limit,
                                      group_capacity_bytes=GIB, reason='ready')
    plan = BrowserThumbnailDisplayPlan((0, 0, 128, 128), 'fit', 1.)
    provider.set_display_plan(plan)
    try:
        for item in items:
            provider._consume_thumbnail_finished(str(item.path), provider.generation, 64,
                item.thumbnail_revision, ThumbnailLoadResult(image.copy()))
        provider.set_cache_retention_priorities([(items[0].path, items[0].thumbnail_revision, rank),
                                                (items[1].path, items[1].thumbnail_revision, 2)])
        provider.set_display_retention_priorities([(items[0].path, items[0].thumbnail_revision, rank)])
        source_image = provider.restore_memory_thumbnail(items[0], 64, generation=provider.generation)[0]
        assert provider.can_prepare_display(source_image) == (rank == 0)
        ready = plan.render(source_image, Event())
        assert provider._store_display_artifact(source_image, plan, ready) == (rank == 0)
        if rank == 0:
            assert surfaces.adopt(source_image, plan, ready)
        for item in items[:2]:
            assert provider.has_memory_thumbnail(item, 64)
        assert provider.has_memory_thumbnail(items[2], 64) == (rank != 0)
        assert provider.memory_cache_bytes <= limit
        assert surfaces.has_plan(source_image, plan) == (rank == 0)
        if rank != 0:
            provider.configure_browser_memory(mode='128', limit_bytes=2*limit, desired_bytes=2*limit,
                                              group_capacity_bytes=GIB, reason='ready')
            assert provider.can_prepare_display(source_image)
            assert provider._store_display_artifact(source_image, plan, ready)
    finally:
        provider.close()


def test_shared_pixels_are_charged_once_and_detached_surfaces_are_charged(qapp):
    ledger = BrowserArtifactMemory()
    image = QImage(64, 64, QImage.Format.Format_ARGB32)
    image.fill(0xFF2468AC)
    ledger.retain(('cache', 1), image)
    ledger.retain(('model', 1), QImage(image))
    ledger.retain(('surface', 1), QImage(image))
    assert ledger.bytes == image.sizeInBytes()
    assert ledger.display_bytes == image.sizeInBytes()
    detached = image.copy()
    ledger.retain(('surface', 2), detached)
    assert ledger.bytes == image.sizeInBytes() * 2
    for owner in tuple(ledger.owners):
        ledger.release(owner)
    assert ledger.bytes == ledger.display_bytes == 0
    assert not ledger.pixels


def test_unseen_completion_cannot_displace_read_images_or_surfaces(tmp_path, qapp, monkeypatch):
    def loader(*args):
        image = QImage(512, 512, QImage.Format.Format_ARGB32)
        image.fill(0xFF123456)
        return image
    provider = BrowserThumbnailProvider(disk_cache_enabled=False, loader=loader)
    model = BrowserItemModel()
    surfaces = BrowserDisplaySurfaceCache()
    items = [BrowserItem(f'{i}.png', tmp_path / f'{i}.png', BrowserItemKind.IMAGE, 1.0) for i in range(203)]
    model.set_items(items)
    provider.enable_retained_display()
    model.bind_thumbnail_memory(provider)
    surfaces.bind_memory(provider)
    provider.configure_browser_memory(mode='128', limit_bytes=4*MIB,
                                      desired_bytes=128*MIB, group_capacity_bytes=GIB, reason='ready')
    try:
        for item in items[:3]:
            image = QImage(512, 512, QImage.Format.Format_ARGB32)
            image.fill(0xFF2468AC)
            provider._consume_thumbnail_finished(str(item.path), provider.generation, 64,
                                                 item.thumbnail_revision, ThumbnailLoadResult(image))
            model.set_thumbnail_image(item.path, image, request_token=64)
            surfaces.prepare(image, QRectF(image.rect()), QRectF(0, 0, 96, 96), 1.0)
        original = {str(item.path): int(model.data(model.index(i, 0), model.ThumbnailImageRole).cacheKey())
                    for i, item in enumerate(items[:3])}
        before = surfaces.preparations
        def forbidden(*args, **kwargs):
            raise AssertionError('A completion/scroll must not rebuild the full cache index')
        monkeypatch.setattr(provider, '_rebuild_cache_rank_index', forbidden)
        spec = ThumbnailRenderSpec.from_settings(64, 'square_1_1', 'letterbox')
        for item in items[3:]:
            assert provider.request_background(item, spec, generation=provider.generation) == 'queued'
            assert provider.wait_for_done(5000)
            qapp.processEvents()
            # Recenter a bounded visible/near set without sorting all read entries.
            provider.set_cache_retention_priorities([(items[0].path, items[0].thumbnail_revision, 0)])
            model.retain_thumbnail_images(())
            assert provider.memory_cache_bytes <= 4*MIB
        for i, item in enumerate(items[:3]):
            image = model.data(model.index(i, 0), model.ThumbnailImageRole)
            assert int(image.cacheKey()) == original[str(item.path)]
            surfaces.prepare(image, QRectF(image.rect()), QRectF(0, 0, 96, 96), 1.0)
        assert surfaces.preparations == before
        # Shrinking removes model and prepared references with the actual source.
        provider.configure_browser_memory(mode='128', limit_bytes=2*MIB,
                                          desired_bytes=128*MIB, group_capacity_bytes=GIB, reason='headroom_limited')
        assert provider.memory_cache_bytes <= 2*MIB
        assert len(model._thumbnail_images) < 3
        provider.clear_memory_cache()
        assert provider.memory_cache_bytes == 0
        assert model.thumbnail_image_bytes == surfaces.byte_cost == 0
    finally:
        provider.close()


def test_auto_grows_for_completed_artifacts_and_shrinks_under_os_pressure(tmp_path, qapp, app, controlled_broker):
    # Accounting/growth test uses real distinct QImages, not invented byte claims.
    broker, snapshot = controlled_broker
    root = tmp_path / 'books'
    root.mkdir()
    window = make_window(tmp_path, root, qapp)
    items = [BrowserItem(f'{i}.png', root / f'{i}.png', BrowserItemKind.IMAGE, 1.0) for i in range(180)]
    try:
        window.config.apply({'browser_thumbnail_background_screens': 0})
        window.item_model.set_items(items)
        provider = window.thumbnail_provider
        for item in items:
            image = QImage(512, 512, QImage.Format.Format_ARGB32)
            image.fill(0xFF2468AC)
            provider._consume_thumbnail_finished(str(item.path), provider.generation,
                window.thumbnail_render_spec.cache_token, item.thumbnail_revision, ThumbnailLoadResult(image))
            window.item_model.set_thumbnail_image(item.path, image,
                                                 request_token=window.thumbnail_render_spec.cache_token)
        assert len(window.item_model._thumbnail_images) == 180
        assert provider.memory_cache_bytes == 180*MIB
        assert provider.memory_cache_limit_bytes == 256*MIB
        assert provider.artifact_memory.role_bytes['model'] == provider.memory_cache_bytes
        window.hide()
        broker.rebalance()
        assert provider.memory_cache_bytes == 180*MIB
        assert len(window.item_model._thumbnail_images) == 180
        snapshot[0] = SimpleNamespace(total_physical_bytes=16*GIB, available_physical_bytes=GIB)
        broker.sample_now()
        assert provider.memory_cache_bytes <= provider.memory_cache_limit_bytes
        assert len(window.item_model._thumbnail_images) < 180
    finally:
        window.close()
        qapp.processEvents()


def test_shared_model_aliases_are_released_on_final_pixel_eviction(tmp_path, qapp):
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    model = BrowserItemModel()
    items = [BrowserItem(str(i), tmp_path / f'{i}.png', BrowserItemKind.IMAGE, 1.0) for i in range(2)]
    model.set_items(items)
    provider.enable_retained_display()
    model.bind_thumbnail_memory(provider)
    provider.configure_browser_memory(mode='128', limit_bytes=MIB, desired_bytes=MIB,
                                      group_capacity_bytes=GIB, reason='ready')
    image = QImage(64, 64, QImage.Format.Format_ARGB32)
    image.fill(0)
    try:
        for item in items:
            provider._consume_thumbnail_finished(str(item.path), provider.generation, 64,
                                                 item.thumbnail_revision, ThumbnailLoadResult(image))
            model.set_thumbnail_image(item.path, image, request_token=64)
        assert provider.memory_cache_bytes == image.sizeInBytes()
        provider.configure_browser_memory(mode='128', limit_bytes=0, desired_bytes=MIB,
                                          group_capacity_bytes=0, reason='headroom_limited')
        assert provider.memory_cache_bytes == 0
        assert not model._thumbnail_images
    finally:
        provider.close()


def test_extreme_entry_count_is_bounded_even_with_shared_tiny_pixels(qapp):
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    provider.enable_retained_display()
    provider.configure_browser_memory(mode='128', limit_bytes=MIB, desired_bytes=128*MIB,
                                      group_capacity_bytes=GIB, reason='ready')
    image = QImage(1, 1, QImage.Format.Format_RGB32)
    image.fill(0)
    try:
        for row in range(MANAGED_ENTRY_GUARD + 1):
            provider._consume_thumbnail_finished(f'{row}.png', provider.generation, 64,
                                                 (1,), ThumbnailLoadResult(image))
        assert len(provider._cache) == MANAGED_ENTRY_GUARD
        assert provider.memory_cache_bytes == image.sizeInBytes()
        assert provider.cache_statistics()['memory_cache_evictions'] == 1
    finally:
        provider.close()
