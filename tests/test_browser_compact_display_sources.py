"""Saved thumbnails can retain exact display pixels under a joint RAM limit."""
from hashlib import sha256
from threading import Event, get_ident

import pytest
from PIL import Image
from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from app.browser_item_delegate import BrowserDisplaySurfaceCache
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_thumbnail_surface import BrowserThumbnailDisplayPlan, thumbnail_image_rects
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.gui_garbage_collector import ensure_gui_garbage_collector
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider, _DISPLAY_SOURCE_METADATA
from app.thumbnail_render import ThumbnailRenderSpec


def pixels(image):
    return bytes(image.constBits())


class SavedPreview:
    def __init__(self, tmp_path, qapp, monkeypatch, dpr, *, mode='center_crop', roomy=False, alpha=False):
        ensure_gui_garbage_collector()
        path = tmp_path/'日本語 Ω 表紙.png'
        Image.new('RGB', (272, 384), 'navy').save(path)
        stat = path.stat()
        self.item = BrowserItem(path.name, path, BrowserItemKind.IMAGE, stat.st_mtime,
            modified_time_ns=stat.st_mtime_ns, file_size=stat.st_size)
        self.spec = ThumbnailRenderSpec.from_settings(149, 'portrait_1_sqrt2', 'smart_crop',
            device_pixel_ratio=dpr, quality_mode='economy', encoder_quality=40,
            browser_display_mode=mode, preserve_alpha=alpha)
        self.disk = ThumbnailDiskCache(tmp_path/'cache')
        image = QImage(272, 384, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(23, 45, 67, 91 if alpha else 255))
        painter = QPainter(image)
        painter.fillRect(10, 30, 140, 250, QColor(230, 20, 190, 120 if alpha else 255))
        painter.end()
        assert self.disk.put(self.item, self.spec, image, page_count=1)
        self.saved_hashes = self.hashes(tmp_path/'cache')
        assert self.saved_hashes
        self.provider = BrowserThumbnailProvider(disk_cache=self.disk)
        self.provider.enable_retained_display()
        self.plan = BrowserThumbnailDisplayPlan((4, 3, 105, 149), mode, dpr)
        cost = sum(k[4]*k[5]*4 for k, _ in self.plan.geometries(image))
        self.limit = 128*1024**2 if roomy else image.sizeInBytes()+cost-1
        self.provider.configure_browser_memory(mode='128', limit_bytes=self.limit,
            desired_bytes=self.limit, group_capacity_bytes=self.limit, reason='test')
        self.provider.set_display_plan(self.plan)
        self.provider.set_cache_retention_priorities([(self.item.path, self.item.thumbnail_revision, 0)])
        self.model = BrowserItemModel(); self.model.set_items([self.item])
        self.model.bind_thumbnail_memory(self.provider)
        self.cache = BrowserDisplaySurfaceCache(); self.cache.bind_memory(self.provider)
        self.provider.display_surfaces_ready.connect(lambda _p, _g, _t, _r, raw, plan, surfaces:
            self.cache.adopt(raw, plan, surfaces))
        self.receipts = []
        def ready(path, generation, raw):
            self.receipts.append(raw)
            self.model.set_thumbnail_image(path, raw, request_token=self.spec.cache_token)
        self.provider.thumbnail_ready.connect(ready)
        self.renders = []
        original = BrowserThumbnailDisplayPlan.render
        def rendered(plan, raw, cancelled):
            result = original(plan, raw, cancelled)
            self.renders.append((plan, QImage(raw), tuple((k, QImage(s)) for k, s in result), get_ident()))
            return result
        monkeypatch.setattr(BrowserThumbnailDisplayPlan, 'render', rendered)
        monkeypatch.setattr(self.disk, 'put', lambda *a, **kw: pytest.fail('saved hit must not rewrite disk pixels'))
        self.generation = self.provider.begin_generation()
        self.qapp = qapp

    @staticmethod
    def hashes(root):
        return {p.name: sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.webp')}

    def request(self):
        assert self.provider.request(self.item, self.spec, generation=self.generation)
        assert self.provider.wait_for_done(5000)
        self.qapp.processEvents()
        return self.receipts[-1]

    def close(self):
        self.cache.clear(); self.provider.close(); self.qapp.processEvents()


@pytest.mark.parametrize('dpr,count,compact', [
    (1., 600, True), (1.25, 600, True), (2.5, 600, False),
    (1., 0, False), (1., 100, False), (1., 3000, False),
])
@pytest.mark.parametrize('alpha', [False, True])
def test_listing_display_pixels_fit_before_raw_cache_pressure(
        tmp_path, qapp, monkeypatch, dpr, count, compact, alpha):
    preview = SavedPreview(tmp_path, qapp, monkeypatch, dpr, roomy=True, alpha=alpha)
    preview.provider.set_display_working_set_count(count)
    try:
        image = preview.request()
        plan, raw, surfaces, thread = preview.renders[0]
        assert thread != get_ident()
        assert bool(image.text(_DISPLAY_SOURCE_METADATA)) is compact
        if compact:
            assert pixels(image) == pixels(surfaces[0][1])
            assert preview.provider.memory_cache_bytes == image.sizeInBytes()
            assert len(preview.provider.artifact_memory.pixels) == 1
            preview.cache.clear()
            preview.provider._drop_display_artifact(int(image.cacheKey()))
            target, source = thumbnail_image_rects(QRect(*plan.frame), image.size(),
                'center_crop', device_pixel_ratio=dpr)
            prepared = preview.cache.prepare(image, source, target, dpr)
            assert prepared.cache_hit and not prepared.prepared_with_resampling
        else:
            assert image.cacheKey() == raw.cacheKey()
        assert preview.provider.memory_cache_bytes <= preview.limit
        assert preview.hashes(tmp_path/'cache') == preview.saved_hashes
    finally:
        preview.close()


def test_far_saved_worker_keeps_display_plan_when_one_backing_fits(
        tmp_path, qapp, monkeypatch):
    preview = SavedPreview(tmp_path, qapp, monkeypatch, 1.)
    preview.provider.set_display_working_set_count(1)
    preview.provider.set_cache_retention_priorities([])
    limit = 103 * 147 * 4
    preview.provider.configure_browser_memory(mode='128', limit_bytes=limit,
        desired_bytes=limit, group_capacity_bytes=limit, reason='exact-display-grant')
    try:
        assert preview.provider.request(preview.item, preview.spec,
            generation=preview.generation, priority=ThumbnailPriority.BACKGROUND,
            disk_only=True)
        assert preview.provider.wait_for_done(5000); qapp.processEvents()
        assert len(preview.renders) == 1
        restored = preview.provider.restore_memory_thumbnail(preview.item, preview.spec,
            generation=preview.generation)
        assert restored is not None
        image = restored[0]
        assert image.text(_DISPLAY_SOURCE_METADATA)
        assert pixels(image) == pixels(preview.renders[0][2][0][1])
        assert preview.provider.memory_cache_bytes == image.sizeInBytes() == limit
        assert preview.provider.cache_statistics()['background_self_evictions'] == 0
    finally:
        preview.close()


@pytest.mark.parametrize('dpr', [1., 1.25, 2.5])
@pytest.mark.parametrize('alpha', [False, True])
def test_saved_compaction_preserves_exact_pixels_and_one_backing(tmp_path, qapp, monkeypatch, dpr, alpha):
    preview = SavedPreview(tmp_path, qapp, monkeypatch, dpr, alpha=alpha)
    try:
        final = preview.request()
        plan, raw, surfaces, thread = preview.renders[0]
        assert thread != get_ident() and not raw.text(_DISPLAY_SOURCE_METADATA)
        assert final.text(_DISPLAY_SOURCE_METADATA)
        assert pixels(final) == pixels(surfaces[0][1])
        assert preview.model._has_compatible_thumbnail(preview.item, preview.spec.cache_token)
        assert preview.provider.memory_cache_bytes == final.sizeInBytes() <= preview.limit
        assert len(preview.provider.artifact_memory.pixels) == 1
        assert preview.cache.has_plan(final, plan)
        assert int(final.cacheKey()) not in preview.provider._display_pixel_rank
        preview.provider._reclaim_display_headroom(preview.limit, minimum_rank=0)
        assert preview.cache.has_plan(final, plan)  # A zero-byte victim cannot help.
        # Clearing derivative indexes must recover the owned canonical pixels,
        # without a decode, worker resize, GUI resample or extra backing.
        preview.cache.clear(); preview.provider._drop_display_artifact(int(final.cacheKey()))
        monkeypatch.setattr(preview.provider, 'reserve_display_surfaces',
            lambda *a, **kw: pytest.fail('owned aliases need no reservation'))
        for x, y in [(0, 0), (1, -3), (7, 5)]:
            target, source = thumbnail_image_rects(QRect(*plan.frame).translated(x, y),
                final.size(), 'center_crop', device_pixel_ratio=dpr)
            prepared = preview.cache.prepare(final, source, target, dpr)
            assert prepared.cache_hit and not prepared.prepared_with_resampling
            assert pixels(prepared.image) == pixels(surfaces[0][1])
            # Final painting at fractional placements uses the same surface.
            results = []
            for image in (surfaces[0][1], prepared.image):
                canvas = QImage(420, 570, QImage.Format.Format_RGB32); canvas.fill(QColor('white'))
                canvas.setDevicePixelRatio(dpr)
                painter = QPainter(canvas); painter.setOpacity(.63)
                painter.drawImage(target, image, QRectF(image.rect())); painter.end()
                results.append(pixels(canvas))
            assert results[0] == results[1]
        assert preview.cache.preparations == 0 and len(preview.renders) == 1
        assert preview.provider.cache_statistics()['disk_hit'] == 1
        assert preview.provider.cache_statistics()['generated'] == 0
        assert preview.hashes(tmp_path/'cache') == preview.saved_hashes
    finally:
        preview.close()


def test_same_spec_dpi_change_rejects_old_queued_hit_and_reloads_saved_original(tmp_path, qapp, monkeypatch):
    preview = SavedPreview(tmp_path, qapp, monkeypatch, 2.5)
    try:
        old = preview.request()
        assert not preview.provider.request(preview.item, preview.spec, generation=preview.generation)
        new_plan = BrowserThumbnailDisplayPlan(preview.plan.frame, 'center_crop', 1.25)
        preview.provider.set_display_plan(new_plan)
        qapp.processEvents()
        assert len(preview.receipts) == 1  # Queued old-plan memory hit is stale.
        assert not preview.provider.memory_image_compatible(QImage(old))
        assert not preview.provider.can_prepare_display(old)
        assert not preview.provider.request_display_surfaces(preview.item, preview.spec, old,
            generation=preview.generation, priority=ThumbnailPriority.READ_AHEAD)
        assert not preview.provider._store_display_artifact(old, new_plan,
            preview.renders[0][2])
        assert not preview.model._has_compatible_thumbnail(preview.item, preview.spec.cache_token)
        assert not preview.provider.has_memory_thumbnail(preview.item, preview.spec)
        assert preview.provider.restore_memory_thumbnail(preview.item, preview.spec,
            generation=preview.generation) is None
        new = preview.request()
        assert new.size() != old.size() and preview.provider.memory_image_compatible(new)
        assert preview.model._has_compatible_thumbnail(preview.item, preview.spec.cache_token)
        assert preview.model._thumbnail_images[preview.model._key(preview.item.path)].cacheKey() == new.cacheKey()
        assert preview.renders[-1][1].size() == preview.renders[0][1].size()
        assert not preview.renders[-1][1].text(_DISPLAY_SOURCE_METADATA)
        assert preview.provider.cache_statistics()['disk_hit'] == 2
        assert preview.hashes(tmp_path/'cache') == preview.saved_hashes
    finally:
        preview.close()


@pytest.mark.parametrize('mode,roomy', [('fit', False), ('center_crop', True)])
def test_other_mode_and_roomy_budget_keep_raw_representation(tmp_path, qapp, monkeypatch, mode, roomy):
    preview = SavedPreview(tmp_path, qapp, monkeypatch, 2.5, mode=mode, roomy=roomy)
    try:
        image = preview.request()
        assert image.size() == preview.renders[0][1].size()
        assert not image.text(_DISPLAY_SOURCE_METADATA)
        assert preview.provider.memory_cache_bytes <= preview.limit
    finally:
        preview.close()


def test_old_plan_worker_result_keeps_original_and_cross_size_does_not_borrow_compact(tmp_path, qapp, monkeypatch):
    preview = SavedPreview(tmp_path, qapp, monkeypatch, 2.5)
    try:
        compact = preview.request()
        other = ThumbnailRenderSpec.from_settings(96, 'portrait_1_sqrt2', 'smart_crop',
            device_pixel_ratio=2.5, quality_mode='economy', encoder_quality=40,
            browser_display_mode='center_crop')
        assert preview.provider._memory_candidate(preview.provider._path_key(preview.item.path),
            preview.item.thumbnail_revision, other) is None
        preview.provider.clear_memory_cache()
        entered, release = Event(), Event()
        original = BrowserThumbnailDisplayPlan.render
        def blocked(plan, raw, cancelled):
            result = original(plan, raw, cancelled)
            entered.set(); assert release.wait(5)
            return result
        monkeypatch.setattr(BrowserThumbnailDisplayPlan, 'render', blocked)
        assert preview.provider.request(preview.item, preview.spec, generation=preview.generation)
        assert entered.wait(5)
        preview.provider.set_display_plan(BrowserThumbnailDisplayPlan(preview.plan.frame, 'fit', 1.25))
        release.set()
        assert preview.provider.wait_for_done(5000); qapp.processEvents()
        image = preview.receipts[-1]
        assert image.size() != compact.size()
        assert not image.text(_DISPLAY_SOURCE_METADATA)
        assert preview.provider.memory_image_compatible(image)
        assert not preview.cache.has_plan(image, preview.plan)
    finally:
        preview.close()


def test_plan_change_inside_budget_callback_retains_original(tmp_path, qapp, monkeypatch):
    preview = SavedPreview(tmp_path, qapp, monkeypatch, 2.5)
    original = preview.provider._ensure_display_headroom
    changed = []
    def pressure(extra=0):
        if extra and not changed:
            changed.append(True)
            preview.provider.set_display_plan(BrowserThumbnailDisplayPlan(preview.plan.frame, 'fit', 1.))
        return original(extra)
    monkeypatch.setattr(preview.provider, '_ensure_display_headroom', pressure)
    try:
        image = preview.request()
        assert changed and not image.text(_DISPLAY_SOURCE_METADATA)
        assert image.size() == preview.renders[0][1].size()
        assert preview.provider.memory_image_compatible(image)
        assert not preview.cache.has_plan(image, preview.plan)
        assert preview.provider.memory_cache_bytes <= preview.limit
    finally:
        preview.close()


def test_oversized_final_surface_keeps_raw_that_fits(tmp_path, qapp, monkeypatch):
    preview = SavedPreview(tmp_path, qapp, monkeypatch, 2.5)
    limit = 272*384*4+1
    preview.provider.configure_browser_memory(mode='128', limit_bytes=limit,
        desired_bytes=limit, group_capacity_bytes=limit, reason='test-small-grant')
    preview.provider.set_display_plan(BrowserThumbnailDisplayPlan(preview.plan.frame, 'center_crop', 4.))
    try:
        image = preview.request()
        assert not image.text(_DISPLAY_SOURCE_METADATA)
        assert preview.provider.owns_memory_image(image)
        assert image.size() == preview.renders[0][1].size()
        assert preview.provider.memory_cache_bytes <= limit
    finally:
        preview.close()


def test_distinct_gui_backing_remains_reclaimable_and_plan_clears_aliases(tmp_path, qapp, monkeypatch):
    preview = SavedPreview(tmp_path, qapp, monkeypatch, 2.5)
    try:
        image = preview.request()
        pixel = int(image.cacheKey())
        preview.cache.clear()
        limit = image.sizeInBytes()*3
        preview.provider.configure_browser_memory(mode='128', limit_bytes=limit,
            desired_bytes=limit, group_capacity_bytes=limit, reason='test-distinct')
        geometry, _source = preview.plan.geometries(image)[0]
        assert preview.cache.adopt(image, preview.plan, ((geometry, image.copy()),)) == 1
        assert pixel in preview.provider._display_pixel_rank
        assert preview.provider._distinct_surface_reference_counts[pixel] == 1
        preview.provider._reclaim_display_headroom(limit, minimum_rank=0)
        assert not preview.cache.has_plan(image, preview.plan)
        assert preview.provider.memory_cache_bytes == image.sizeInBytes()
        assert not preview.provider._distinct_surface_reference_counts
        preview.cache.adopt(image, preview.plan, preview.provider.display_artifact(image, preview.plan))
        assert preview.cache.has_plan(image, preview.plan)
        preview.provider.set_display_plan(BrowserThumbnailDisplayPlan(preview.plan.frame, 'fit', 1.))
        assert preview.cache.item_count == 0
        assert not preview.provider._surface_reference_counts
    finally:
        preview.close()


@pytest.mark.parametrize('compact', [False, True])
def test_screen_dpr_change_in_same_disk_bucket_reopens_plan_bound_sources(tmp_path, qapp, monkeypatch, compact):
    from collections import deque
    from types import SimpleNamespace
    from app.browser_window import BrowserWindow
    from app.browser_thumbnail_presentation import BrowserThumbnailPresentation
    preview = SavedPreview(tmp_path, qapp, monkeypatch, 2.5, roomy=not compact)
    calls = []
    try:
        old = preview.request()
        preview.provider._nonresident_saved_reads[('old-plan', 1, ())] = (1, 4, 1, None)
        window = SimpleNamespace(_shutdown_prepared=False, _generation=preview.generation,
            _thumbnail_dpr=2.5, _current_device_pixel_ratio=lambda: 2.4,
            _build_thumbnail_render_spec=lambda: preview.spec,
            thumbnail_render_spec=preview.spec, thumbnail_provider=preview.provider,
            _browser_workflow=SimpleNamespace(_model_changed=lambda: calls.append('scope')),
            _schedule_thumbnail_requests=lambda delay: calls.append(('visible', delay)),
            item_delegate=SimpleNamespace(thumbnail_display_mode='center_crop',
                _display_surface_cache=preview.cache, grid_metrics=SimpleNamespace(
                    cell_rect=lambda: QRect(), thumbnail_frame_rect=lambda _: QRect(*preview.plan.frame))),
            list_view=SimpleNamespace(viewport=lambda: SimpleNamespace(devicePixelRatioF=lambda: 2.4)))
        presentation = SimpleNamespace(window=window, _closed=False, _revision=0,
            _context=None, _plan=preview.plan, _queue=deque(), _queued=set(),
            _decoration_rows=deque(), _display_priority_rows={})
        window._thumbnail_presentation = SimpleNamespace(
            recenter=lambda: BrowserThumbnailPresentation._synchronize(presentation))
        BrowserWindow._reevaluate_thumbnail_dpr(window)
        assert preview.provider._display_plan.dpr == 2.4
        assert window._generation == preview.generation  # Same disk bucket reuses the book epoch.
        if compact:
            assert calls == ['scope', ('visible', 0)]
            assert not preview.provider._nonresident_saved_reads
            assert not preview.provider.memory_image_compatible(old)
            new = preview.request()
            assert preview.provider.memory_image_compatible(new)
            assert preview.provider.cache_statistics()['disk_hit'] == 2
            assert preview.model._has_compatible_thumbnail(preview.item, preview.spec.cache_token)
        else:
            assert not calls
            assert preview.provider.memory_image_compatible(old)
            assert preview.provider.has_memory_thumbnail(preview.item, preview.spec)
            assert preview.provider.cache_statistics()['disk_hit'] == 1
        assert preview.hashes(tmp_path/'cache') == preview.saved_hashes
    finally:
        preview.close()
