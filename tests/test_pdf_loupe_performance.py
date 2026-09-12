"""Comparable synthetic entry/promotion observations, not native benchmarks."""
import json
import time

import pytest
from PySide6.QtCore import QTimer

from tests.test_pdf_magnifier_integration import pdf_window, _wait, _settle, _paint


def _sharp(window):
    widget = window.viewer
    if hasattr(widget, "_pdf_loupe_requests"):
        return bool(widget._pdf_loupe_requests) and all(
            name in widget._pdf_loupe_ready for name in widget._pdf_loupe_requests)
    return (widget.magnifier_active and not widget._magnifier_waiting_for_pdf
            and not window.image_cache.has_unfinished_tasks()
            and not widget._render_tasks and window._pending_display_demand is None)


@pytest.mark.parametrize("spread", [False, True])
@pytest.mark.parametrize("pixmap_only", [False, True])
def test_entry_observation(pdf_window, qapp, spread, pixmap_only):
    window, backend = pdf_window
    widget = window.viewer
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
    start_generation = window.image_cache.generation
    normal_keys = {i: v.qimage.cacheKey() for i, v in window.image_cache._cache.items()}
    renders = len(backend.requests)
    backend.block = True
    QTimer.singleShot(150, backend.release.set)
    start = time.perf_counter()
    assert widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    handler_ms = (time.perf_counter() - start) * 1000
    active_on_return = widget.magnifier_active
    _wait(qapp, lambda: widget.magnifier_active)
    _paint(widget)
    first_ms = (time.perf_counter() - start) * 1000
    _wait(qapp, lambda: _sharp(window))
    _paint(widget)
    sharp_ms = (time.perf_counter() - start) * 1000
    widget.cancel_magnifier()
    _settle(window, qapp)
    renders_after_cancel = len(backend.requests)
    assert widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    _wait(qapp, lambda: _sharp(window))
    _paint(widget)
    widget.cancel_magnifier()
    _settle(window, qapp)
    remaining_keys = {i: v.qimage.cacheKey() for i, v in window.image_cache._cache.items()}
    print("PDF_LOUPE_OBSERVATION=" + json.dumps(dict(
        spread=spread, pixmap_only=pixmap_only, active_on_return=active_on_return,
        handler_ms=round(handler_ms, 3), first_magnified_paint_ms=round(first_ms, 3),
        sharp_paint_ms=round(sharp_ms, 3),
        generation_changes=window.image_cache.generation-start_generation,
        first_cycle_renders=renders_after_cancel-renders,
        reopen_cycle_renders=len(backend.requests)-renders_after_cancel,
        normal_cache_preserved=all(remaining_keys.get(i) == key for i, key in normal_keys.items()),
    )))
