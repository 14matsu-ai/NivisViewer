"""PDF entry before workers, independent normal caches and bounded reuse."""
from dataclasses import replace
from threading import get_ident

import pytest
from PySide6.QtCore import QPoint, QEvent
from PySide6.QtGui import QImage, QPixmap

from tests.test_pdf_magnifier_integration import pdf_window, _wait, _settle, _paint, _stripe_width
from tests.test_pdf_loupe_performance import _sharp


@pytest.mark.parametrize("spread", [False, True])
@pytest.mark.parametrize("pixmap_only", [False, True])
@pytest.mark.parametrize("rotation,direction", [(0, "ltr"), (90, "rtl"), (180, "rtl"), (270, "ltr")])
def test_entry_and_pointer_motion_do_not_wait_for_any_worker(
        pdf_window, qapp, monkeypatch, spread, pixmap_only, rotation, direction):
    window, backend = pdf_window
    widget = window.viewer
    window.set_reading_direction(direction)
    for _ in range(rotation // 90):
        window.rotate_right()
    if spread:
        window.set_view_mode("spread")
        window.set_single_first_page(False)
        _wait(qapp, lambda: len(widget._last_image_layout) == 2)
    _settle(window, qapp)
    if pixmap_only:
        unit = window.model.spread_at()
        widget.prepare_display_units([(0, unit, list(widget._images), True)])
        _settle(window, qapp)
        assert widget.apply_prepared_display(unit,
            source_generation=window.image_cache.generation,
            source_identity=window._prepared_source_identity())
        _paint(widget)
        assert all(image.qimage is None for image in widget._images)
    normal = _paint(widget)
    keys = {i: v.qimage.cacheKey() for i, v in window.image_cache._cache.items()}
    generation = window.image_cache.generation
    normal_render_keys = {key: p.cacheKey() for key, p in widget._render_cache.items()}

    def forbidden(*args, **kwargs):
        raise AssertionError("Entry must not create/resize/transfer a fallback image")

    backend.block = True
    position = widget._last_image_layout[0][0].center()
    with monkeypatch.context() as patch:
        patch.setattr(widget, "_queue_render", forbidden)
        patch.setattr(QPixmap, "scaled", forbidden)
        patch.setattr(QPixmap, "toImage", forbidden)
        patch.setattr(QPixmap, "fromImage", forbidden)
        assert widget.toggle_magnifier(position)
        assert widget.magnifier_active and not widget.magnifier_selecting
        assert not widget._pdf_loupe_ready
        # render() executes paint even with every promotion blocked.
        widget.render(QPixmap(widget.size()))
    first = _paint(widget)
    assert _stripe_width(first, rotation) == pytest.approx(_stripe_width(normal, rotation) * 2, abs=3)
    _wait(qapp, backend.started.is_set)
    for x in (position.x() - 60, widget.width() // 2, position.x() + 70):
        widget._update_magnifier_selection(QPoint(x, position.y() + 30))
        assert widget.magnifier_active and not widget._pdf_loupe_ready
        _paint(widget)
    anchor = widget.magnifier_source_rect
    backend.release.set()
    _wait(qapp, lambda: _sharp(window))
    final = _paint(widget)
    assert widget.magnifier_source_rect == anchor
    assert _stripe_width(final, rotation) == pytest.approx(_stripe_width(first, rotation), abs=3)
    assert window.image_cache.generation == generation
    assert all(window.image_cache._cache[i].qimage.cacheKey() == key for i, key in keys.items())
    assert all(widget._render_cache[key].cacheKey() == value for key, value in normal_render_keys.items())
    assert all(thread != get_ident() for thread in backend.threads)


def test_cancel_reopen_reuses_running_then_completed_promotion(pdf_window, qapp):
    window, backend = pdf_window
    widget = window.viewer
    backend.block = True
    position = widget._last_image_layout[0][0].center()
    widget.toggle_magnifier(position)
    _wait(qapp, backend.started.is_set)
    count = len([r for r in backend.requests if r.purpose == "magnifier"])
    widget.cancel_magnifier()
    assert not widget.magnifier_active
    assert widget.toggle_magnifier(position)
    assert widget.magnifier_active
    backend.release.set()
    _wait(qapp, lambda: _sharp(window))
    final = {name: p.cacheKey() for name, p in widget._pdf_loupe_ready.items()}
    assert len([r for r in backend.requests if r.purpose == "magnifier"]) == count
    widget.cancel_magnifier()
    assert widget.toggle_magnifier(position)
    assert {name: p.cacheKey() for name, p in widget._pdf_loupe_ready.items()} == final
    assert not window._pdf_loupe_cache.pending_count


def test_zoom_policy_race_and_dpi_reject_old_results(pdf_window, qapp, monkeypatch):
    window, backend = pdf_window
    widget = window.viewer
    backend.block = True
    widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    _wait(qapp, backend.started.is_set)
    old = next(iter(widget._pdf_loupe_requests.values()))
    widget.set_magnifier_options(zoom=3)
    widget.set_resampling_algorithms(magnifier_upscale="bilinear")
    assert widget.magnifier_active
    fake = QPixmap(2, 2)
    assert not widget.apply_pdf_loupe_artifact(old, fake)
    backend.release.set()
    _wait(qapp, lambda: _sharp(window))
    assert all(key.upscale_algorithm == "bilinear" for key in widget._pdf_loupe_requests.values())
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: 2.)
    widget.event(QEvent(QEvent.Type.DevicePixelRatioChange))
    _wait(qapp, lambda: _sharp(window))
    assert all(key.device_pixel_ratio_milli == 2000 for key in widget._pdf_loupe_requests.values())


def test_promoted_cache_evicts_without_touching_normal_images(pdf_window, qapp):
    window, backend = pdf_window
    widget = window.viewer
    widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    _wait(qapp, lambda: _sharp(window))
    cache = window._pdf_loupe_cache
    generation = window.image_cache.generation
    key, pixmap = next(iter(cache._cache.items()))
    retained_bytes = pixmap.width() * pixmap.height() * 4
    cache.set_byte_limit(retained_bytes * 2)
    # Simulate completed distinct entries at the cache boundary, including the
    # actual QPixmap allocations; budget must hold while the current lens lives.
    from app.pdf_loupe import _Task
    for width in range(80, 92):
        variant = replace(key, render=replace(key.render, target_width=width))
        task = _Task(cache.source, variant)
        image = QImage(pixmap.width(), pixmap.height(), QImage.Format.Format_RGB32)
        image.fill(0xff335577)
        cache._finished(task, image, None)
        assert cache.cache_bytes <= cache.byte_limit
        assert len(cache._cache) <= 8
        assert widget.magnifier_active
    assert window.image_cache.generation == generation
    cache.set_byte_limit(0)
    assert cache.cache_bytes == 0 and widget.magnifier_active


def test_pdf_promotion_failure_keeps_interactive_fallback(pdf_window, qapp, monkeypatch):
    window, backend = pdf_window
    original = backend.render_page
    def fail(request, **kwargs):
        if request.purpose == "magnifier":
            raise RuntimeError("Synthetic PDF promotion failure")
        return original(request, **kwargs)
    monkeypatch.setattr(backend, "render_page", fail)
    failures = []
    window._pdf_loupe_cache.failed.connect(lambda *args: failures.append(args))
    widget = window.viewer
    widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    _wait(qapp, lambda: bool(failures))
    assert widget.magnifier_active and not widget._pdf_loupe_ready
    widget._update_magnifier_selection(QPoint(250, 170))
    _paint(widget)
    widget.cancel_magnifier()
    assert not widget.magnifier_active


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_split_pdf_uses_correct_half_after_promotion(pdf_window, qapp, tmp_path, rotation):
    from tests.test_pdf_magnifier_integration import _write_pdf
    window, backend = pdf_window
    wide = tmp_path / "合成横長.pdf"
    _write_pdf(wide, width=481)
    window.set_split_wide_image(True)
    for _ in range(rotation // 90):
        window.rotate_right()
    window.open_path(wide)
    _wait(qapp, lambda: window.book_session.source.source_path == wide
          and len(window.viewer._last_image_layout) == 2)
    _settle(window, qapp)
    widget = window.viewer
    assert all(image.split_range is not None for image in widget._images)
    widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    first = _paint(widget)
    _wait(qapp, lambda: _sharp(window))
    final = _paint(widget)
    assert _stripe_width(final, rotation) == pytest.approx(_stripe_width(first, rotation), abs=3)
    # Compare stripe phase as well as width. Matching colored pixels away from
    # resampling edges detect accidentally promoting the other half/rotation.
    matches, samples = 0, 0
    for y in range(20, final.height()-20, 7):
        for x in range(20, final.width()-20, 7):
            old, new = first.pixelColor(x, y), final.pixelColor(x, y)
            if old.red() > 230 and new.red() > 230:
                matches += (old.green() < 60) == (new.green() < 60)
                samples += 1
    assert matches / max(1, samples) > .92


def test_adjustment_change_rejects_old_policy_and_retains_anchor(pdf_window, qapp):
    window, backend = pdf_window
    widget = window.viewer
    backend.block = True
    widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    _wait(qapp, backend.started.is_set)
    anchor = widget.magnifier_source_rect
    old_key = next(iter(window._pdf_loupe_cache._tasks))
    window._set_image_adjustments(brightness=.5)
    window._on_pdf_loupe_ready(old_key, QPixmap(2, 2))
    assert not widget._pdf_loupe_ready
    backend.release.set()
    _wait(qapp, lambda: _sharp(window))
    assert widget.magnifier_source_rect == anchor
    result = _paint(widget)
    assert 120 <= result.pixelColor(result.width() // 2, result.height() // 2).red() <= 135


def test_normal_page_cancellation_does_not_cancel_loupe_job(pdf_window, qapp):
    window, backend = pdf_window
    widget = window.viewer
    backend.block = True
    widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    _wait(qapp, backend.started.is_set)
    task = next(iter(window._pdf_loupe_cache._tasks.values()))
    window.book_session.source.cancel_image_request("pdf-page:0")
    assert not task.cancelled.is_set()
    backend.release.set()
    _wait(qapp, lambda: _sharp(window))
    assert widget.magnifier_active


def test_spread_dpi_growth_keeps_aggregate_artifact_pixel_bound(pdf_window, qapp, monkeypatch):
    window, backend = pdf_window
    window.set_view_mode("spread")
    window.set_single_first_page(False)
    _wait(qapp, lambda: len(window.viewer._last_image_layout) == 2)
    _settle(window, qapp)
    widget = window.viewer
    # Intercept final work: this tests admission without allocating giant images.
    monkeypatch.setattr(window._pdf_loupe_cache, "request", lambda *args: None)
    widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: 16.)
    widget._request_pdf_loupe_artifacts()
    pixels = sum(key.target_width * key.target_height for key in widget._pdf_loupe_requests.values())
    assert pixels <= 32 * 1024 * 1024 + 20000  # integer edge rounding only
    assert widget.magnifier_active
