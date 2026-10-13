"""Original ZipPla item-owned redisplay adapted to a joint Qt pixel budget."""
from types import SimpleNamespace

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
