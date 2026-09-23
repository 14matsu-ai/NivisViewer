"""Live Window/timer and shared Browser broker integration; synthetic pixels only."""
from io import BytesIO
import zipfile

from PIL import Image
from PySide6.QtCore import QEvent

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.viewer_window import ViewerWindow
from app.viewer_navigation_policy import NavigationInputKind
from app.viewer_memory_policy import GIB, MIB, PhysicalMemorySnapshot, ResolvedViewerMemoryPolicy
from app.browser_thumbnail_memory import BrowserThumbnailMemoryBroker
from app.browser_thumbnail_memory_policy import physical_group_capacity
from test_zip_raster_viewer_integration import _window, _wait_until
from test_browser_thumbnail_memory_integration_qt import _Window, _mixed_items
from app.thumbnail_provider import ThumbnailLoadResult
from PySide6.QtGui import QImage


def _open(window, session, path, qapp):
    window.set_view_mode("single")
    window.show()
    qapp.processEvents()
    assert window._finish_opened_book(session.open_book(path), modal_on_empty=False)
    _wait_until(qapp, lambda: window.presentation_state.displayed_page == 0)
    return session.viewer_runtime


def test_real_zoom_timer_coalesces_without_new_presentation(tmp_path, qapp, monkeypatch):
    window, session, _, path = _window(tmp_path)
    try:
        runtime = _open(window, session, path, qapp)
        calls = []
        refresh = runtime.refresh_work_order
        def record(request):
            calls.append(request)
            return refresh(request)
        monkeypatch.setattr(runtime, "refresh_work_order", record)
        for zoom in (1.1, 1.2, 1.3):
            window.viewer.set_manual_zoom(zoom)
            assert window._raster_zoom_warmup_timer.isActive()
            assert not window._zip_runtime_request(window.model.spread_at()).warmup_plan.background_enabled
        request_id = window._active_request_id
        _wait_until(qapp, lambda: not window._raster_zoom_warmup_timer.isActive())
        assert len(calls) == 1
        assert calls[0].request_id == request_id == window._active_request_id
        assert calls[0].render_spec.manual_zoom == 1.3
        assert calls[0].warmup_plan.background_enabled
        _wait_until(qapp, lambda: runtime.cached_unit_count >= 3 and not runtime.has_unfinished_tasks())
        publications = []
        runtime.frameReady.connect(publications.append)
        window._release_settled_raster_warmup()  # a late duplicate has no context
        assert not publications and len(calls) == 1
        window.viewer.set_manual_zoom(1.4)
        window._deactivate_zip_runtime()
        assert not window._raster_zoom_warmup_timer.isActive()
        window._release_settled_raster_warmup()
        assert len(calls) == 1
    finally:
        window.close()
        qapp.processEvents()


def test_unchanged_complete_layout_keeps_frames_real_change_invalidates(tmp_path, qapp, monkeypatch):
    window, session, _, path = _window(tmp_path)
    try:
        runtime = _open(window, session, path, qapp)
        _wait_until(qapp, lambda: runtime.cached_unit_count == 3 and not runtime.has_unfinished_tasks())
        invalidations = []
        invalidate = runtime.invalidate_layout
        def record():
            invalidations.append(True)
            invalidate()
        monkeypatch.setattr(runtime, "invalidate_layout", record)
        before = runtime.metrics
        window._refresh_raster_decode_bounds()
        window._refresh_raster_decode_bounds()
        assert not invalidations
        assert runtime.metrics.qpixmap_creations == before.qpixmap_creations
        assert runtime.metrics.jobs_submitted == before.jobs_submitted
        # A -> B -> A before the debounce consumes geometry has the original spec.
        old = window.viewer.size()
        window.viewer.resize(old.width() + 20, old.height())
        window.viewer.resize(old)
        window._refresh_raster_decode_bounds()
        assert not invalidations
        window.viewer.resize(old.width() + 20, old.height())
        window._refresh_raster_decode_bounds()
        assert invalidations == [True]
    finally:
        window.close()
        qapp.processEvents()


def test_zoom_timeout_preserves_staged_request_ownership(tmp_path, qapp):
    window, session, _, path = _window(tmp_path)
    try:
        runtime = _open(window, session, path, qapp)
        window.viewer.set_manual_zoom(1.25)
        request = window._zip_runtime_request(window.model.spread_at())
        assert window._stage_raster_runtime_request(runtime, request,
            input_kind=NavigationInputKind.SLIDER_SCRUB, repeat_key=None)
        publications = []
        runtime.frameReady.connect(publications.append)
        before = runtime.metrics.jobs_submitted
        _wait_until(qapp, lambda: not window._raster_zoom_warmup_timer.isActive())
        refreshed = window._pending_zip_runtime_request
        assert refreshed is not request
        assert refreshed.request_id == request.request_id
        assert refreshed.warmup_plan.background_enabled
        assert runtime._dispatch_suspended
        assert runtime.metrics.jobs_submitted == before
        assert not publications
        assert not runtime.release_staged(request)
        assert runtime.release_staged(refreshed)
        window._pending_zip_runtime_request = None
        _wait_until(qapp, lambda: bool(publications))
    finally:
        window.close()
        qapp.processEvents()


def test_resident_auto_runtime_recovers_and_updates_future_book_limits(tmp_path, qapp, monkeypatch):
    # Actual retained rasters, not a synthetic cumulative failure counter.
    path = tmp_path / "large.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for index in range(24):
            data = BytesIO()
            with Image.new("RGB", (1000, 1400), (index * 8, 90, 120)) as image:
                image.save(data, "JPEG")
            archive.writestr(f"{index:02d}.jpg", data.getvalue())
    clock = [0.0]
    initial = PhysicalMemorySnapshot(8 * GIB, 2 * GIB + 128 * MIB, 256 * MIB)
    recovered = PhysicalMemorySnapshot(8 * GIB, 6 * GIB, 512 * MIB)
    monkeypatch.setattr("app.viewer_window.read_physical_memory_snapshot", lambda: recovered)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    session = BookSession()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    try:
        window.viewer_memory_policy = ResolvedViewerMemoryPolicy("auto", snapshot=initial, clock=lambda: clock[0])
        window.set_fit_mode("actual_size")
        runtime = _open(window, session, path, qapp)
        monkeypatch.setattr(window, "isActiveWindow", lambda: True)
        window.viewer_memory_policy.set_active(True)
        window._apply_raster_memory_policy()
        assert runtime.cache_byte_budget == 128 * MIB
        _wait_until(qapp, lambda: runtime.auto_cache_growth_requested and not runtime.has_unfinished_tasks(), timeout_ms=10000)
        assert runtime.cache_bytes >= 96 * MIB
        count = runtime.cached_unit_count
        for at in (0, 5, 10):
            clock[0] = at
            window._sample_viewer_memory_pressure()
        assert runtime.cache_byte_budget == 384 * MIB
        assert session._viewer_runtime_hard_limit_bytes == 384 * MIB
        assert session._viewer_runtime_soft_target_bytes == runtime.cache_soft_target_bytes
        _wait_until(qapp, lambda: runtime.cached_unit_count > count and not runtime.has_unfinished_tasks(), timeout_ms=10000)
        assert runtime.cached_unit_count == 24
        assert not runtime.auto_cache_growth_requested
        assert runtime.cache_bytes <= runtime.cache_byte_budget
        for at in (15, 20, 25):
            clock[0] = at
            window._sample_viewer_memory_pressure()
        # Genuine Qt activation events, clock-controlled expiry, no native input.
        high = runtime.cache_soft_target_bytes
        qapp.sendEvent(window, QEvent(QEvent.Type.WindowDeactivate))
        assert runtime.cache_soft_target_bytes == high
        clock[0] += 9
        window._sample_viewer_memory_pressure()
        assert runtime.cache_soft_target_bytes == high
        clock[0] += 1
        window._sample_viewer_memory_pressure()
        assert runtime.cache_soft_target_bytes == 192 * MIB
        assert window.presentation_state.displayed_page == 0
    finally:
        window.close()
        qapp.processEvents()


def test_browser_recovery_with_two_viewer_policies_uses_existing_owners(tmp_path, qapp, monkeypatch):
    clock = [0.0]
    snap = [PhysicalMemorySnapshot(16 * GIB, 1 * GIB, 4 * GIB)]
    attribute = "_nivis_browser_thumbnail_memory_broker"
    previous = getattr(qapp, attribute, None)
    broker = BrowserThumbnailMemoryBroker(qapp, reader=lambda: snap[0], clock=lambda: clock[0])
    setattr(qapp, attribute, broker)
    browser = None
    viewers = []
    try:
        browser = _Window(tmp_path, _mixed_items(tmp_path, 20), mode="auto",
                          loader=lambda *_args, **_kwargs: ThumbnailLoadResult(QImage(64, 64, QImage.Format.Format_RGB32)))
        browser.workflow._memory_near_bytes = 600 * MIB
        broker.sample_now()
        low = browser.thumbnail_provider.memory_cache_limit_bytes
        policies = [ResolvedViewerMemoryPolicy("auto", snapshot=PhysicalMemorySnapshot(16 * GIB, 6 * GIB, 3 * GIB),
                    active=active, clock=lambda: clock[0]) for active in (True, False)]
        snap[0] = PhysicalMemorySnapshot(16 * GIB, 12 * GIB, 4 * GIB)
        for at in (0, 5, 10, 15):
            clock[0] = at
            for policy in policies:
                policy.observe_memory_pressure(snap[0], current_cache_bytes=1800 * MIB, auto_growth_requested=True)
            broker.sample_now()
        assert policies[0].hard_limit_bytes > 2 * GIB
        assert policies[1].hard_limit_bytes == 2 * GIB
        assert browser.thumbnail_provider.memory_cache_limit_bytes > low
        assert broker._governor.capacity == physical_group_capacity(snap[0], browser.thumbnail_provider.memory_cache_bytes)
        assert browser.thumbnail_provider.memory_cache_limit_bytes <= broker._governor.capacity
        # Also connect two real, completed small-book Windows. Their Window
        # sampling path must NOT request growth from the old policy demand.
        monkeypatch.setattr("app.viewer_window.read_physical_memory_snapshot", lambda: snap[0])
        for index, policy in enumerate(policies):
            root = tmp_path / f"viewer-{index}"
            root.mkdir()
            window, session, _, path = _window(root)
            viewers.append(window)
            runtime = _open(window, session, path, qapp)
            _wait_until(qapp, lambda: runtime.cached_unit_count == 3 and not runtime.has_unfinished_tasks())
            window.viewer_memory_policy = policy
            policy.set_active(index == 0)
            monkeypatch.setattr(window, "isActiveWindow", lambda active=index == 0: active)
        limits = [policy.hard_limit_bytes for policy in policies]
        for at in (20, 25, 30):
            clock[0] = at
            broker.sample_now()
            for window in viewers:
                window._sample_viewer_memory_pressure()
        assert [policy.hard_limit_bytes for policy in policies] == limits
        for window, policy in zip(viewers, policies):
            assert window.book_session.viewer_runtime.cache_byte_budget == policy.hard_limit_bytes
            assert not window.book_session.viewer_runtime.auto_cache_growth_requested
    finally:
        for window in viewers:
            window.close()
        qapp.processEvents()
        if browser is not None:
            browser.close_resources(qapp)
        broker.close()
        if previous is None:
            delattr(qapp, attribute)
        else:
            setattr(qapp, attribute, previous)
