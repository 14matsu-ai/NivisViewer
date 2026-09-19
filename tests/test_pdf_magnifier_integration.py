"""Integrated loupe regressions using generated vector PDFs, never user files."""
from __future__ import annotations

import time
from statistics import median
from threading import Event, get_ident

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QPixmap

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.pdf_image_source import PdfImageSource
from app.pdfium_backend import PdfiumBackend
from app.pdfium_service import PdfiumService
from app.viewer_window import ViewerWindow


def _write_pdf(path, *, width=240, height=320):
    # Small standards-compliant PDF fixture; alternating vector stripes make
    # geometric magnification measurable independently of raster resolution.
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R 5 0 R 7 0 R 9 0 R] /Count 4 >>"]
    for page in range(4):
        content = f"1 1 1 rg 0 0 {width} {height} re f\n".encode()
        color = b"1 0 0 rg" if page % 2 == 0 else b"0 0 1 rg"
        for x in range(0, width, 20):
            content += color + f" {x} 0 10 {height} re f\n".encode()
        objects.extend([
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources << >> /Contents {4 + page * 2} 0 R >>".encode(),
            f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream",
        ])
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    path.write_bytes(data)


class RecordingBackend(PdfiumBackend):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.threads = []
        self.block = False
        self.started = Event()
        self.release = Event()

    def render_page(self, request, **kwargs):
        self.requests.append(request)
        self.threads.append(get_ident())
        if self.block:
            self.started.set()
            assert self.release.wait(5)
        return super().render_page(request, **kwargs)


def _wait(qapp, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(.005)
    assert predicate()


def _settle(window, qapp):
    _wait(qapp, lambda: not window.image_cache.has_unfinished_tasks()
          and not window._pdf_loupe_cache.pending_count
          and not window.viewer._render_tasks
          and not window.viewer._pending_display
          and not window._pdf_render_timer.isActive()
          and window._pending_display_demand is None)
    window.viewer.render(QPixmap(window.viewer.size()))


@pytest.fixture
def pdf_window(tmp_path, qapp):
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "合成ルーペ.pdf"
    _write_pdf(pdf)
    backend = RecordingBackend()
    service = PdfiumService(backend)
    session = BookSession(source_factory=lambda path, **kw: (
        PdfImageSource(path, pdfium_service=service), None))
    config = ConfigManager(tmp_path / "settings.json")
    config.load()
    config.apply({"view_mode": "single", "single_first_page": False,
                  "pdf_prefetch_preset": "disabled", "magnifier_zoom": 2.0})
    window = ViewerWindow(config_manager=config, book_session=session, pdfium_service=service)
    window.resize(640, 480)
    window.show()  # QT_QPA_PLATFORM=offscreen, enforced by conftest.
    qapp.processEvents()
    try:
        assert window._finish_opened_book(session.open_book(pdf), modal_on_empty=False)
        _wait(qapp, lambda: bool(window.viewer._last_image_layout))
        _settle(window, qapp)
        yield window, backend
    finally:
        backend.release.set()
        window.close()
        qapp.processEvents()
        session.shutdown(wait_msecs=5000)
        service.shutdown(wait_seconds=5)


@pytest.mark.parametrize("spread", [False, True])
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("rotation,direction", [(0, "ltr"), (0, "rtl"), (90, "ltr"), (270, "rtl")])
def test_pdf_loupe_survives_resolution_refresh(pdf_window, qapp, spread, prepared, rotation, direction):
    window, backend = pdf_window
    # Leave real canvas margins even with two unrotated pages.
    if spread and rotation == 0:
        window.resize(1200, 480)
    window.set_reading_direction(direction)
    for _ in range(rotation // 90):
        window.rotate_right()
    _settle(window, qapp)
    if spread:
        window.set_view_mode("spread")
        window.set_single_first_page(False)
        _wait(qapp, lambda: len(window.viewer._last_image_layout) == 2)
        _settle(window, qapp)
    widget = window.viewer
    if prepared:
        unit = window.model.spread_at()
        widget.prepare_display_units([(0, unit, list(widget._images), True)])
        _settle(window, qapp)
        window._refresh_view()
        _settle(window, qapp)
    before = len(backend.requests)
    normal = _paint(widget)
    normal_rects = [entry[0] for entry in widget._last_image_layout]
    normal_sources = {image.image_id: image.qimage.cacheKey() for image in widget._images}
    assert widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    assert widget.magnifier_selecting or widget.magnifier_active
    _wait(qapp, lambda: widget.magnifier_active)
    _settle(window, qapp)
    assert widget.magnifier_active
    assert len(backend.requests) > before
    assert all(thread != get_ident() for thread in backend.threads)
    # The final artifacts use the promoted PDF raster, not just the old fit
    # image enlarged. PDF requests cover the whole artifact exactly once.
    for image in widget._images:
        key = widget._pdf_loupe_requests[image.image_id]
        promoted = widget._pdf_loupe_ready[image.image_id]
        assert image.qimage.cacheKey() == normal_sources[image.image_id]
        assert promoted.width() == key.target_width
        assert promoted.height() == key.target_height
        request = next(req for req in reversed(backend.requests)
                       if req.page_index == image.page_index and req.purpose == "magnifier")
        assert request.target_width_px <= key.target_width + 65
        assert request.target_height_px <= key.target_height + 65
    enlarged = _paint(widget)
    assert _stripe_width(enlarged, rotation) == pytest.approx(
        _stripe_width(normal, rotation) * 2, abs=3)
    assert [entry[0] for entry in widget._last_image_layout] == normal_rects
    if spread:
        keys = dict(widget._pdf_loupe_requests)
        requests = len(backend.requests)
        left, right = sorted(normal_rects, key=lambda rect: rect.x())
        for position in (left.center(), QPoint((left.right() + right.left()) // 2,
                                               left.center().y()), right.center()):
            widget._update_magnifier_selection(position)
            assert widget.magnifier_active
            _paint(widget)
        assert widget._pdf_loupe_requests == keys
        assert len(backend.requests) == requests
    # Moving outside the pages samples Viewer background, without new PDF work.
    keys = dict(widget._pdf_loupe_requests)
    requests = len(backend.requests)
    margin = next(point for point in (
        QPoint(2, 2), QPoint(widget.width() - 3, 2),
        QPoint(2, widget.height() - 3),
    ) if not any(rect.adjusted(-2, -2, 2, 2).contains(point) for rect in normal_rects))
    widget._update_magnifier_selection(margin)
    assert _paint(widget).pixelColor(widget.width() // 2, widget.height() // 2) == widget.background_color
    assert widget._pdf_loupe_requests == keys
    assert len(backend.requests) == requests
    widget._mouse_pos = margin
    window.config.apply({'magnifier_allow_outside_image': False})
    assert not widget.magnifier_allow_outside_image
    assert widget.magnifier_active or widget.magnifier_selecting
    window.config.apply({'magnifier_allow_outside_image': True})
    assert widget.magnifier_allow_outside_image
    _settle(window, qapp)
    assert _paint(widget).pixelColor(widget.width() // 2, widget.height() // 2) == widget.background_color
    widget._update_magnifier_selection(normal_rects[0].center())
    # A same-page refresh with an already prepared frame must retain the lens.
    unit = window.model.spread_at()
    widget.prepare_display_units([(0, unit, list(widget._images), True)])
    _settle(window, qapp)
    window._refresh_view()
    assert widget.magnifier_active or widget.magnifier_selecting
    _settle(window, qapp)
    assert widget.magnifier_active
    widget.cancel_magnifier()
    _settle(window, qapp)
    assert [entry[0] for entry in widget._last_image_layout] == normal_rects
    assert _stripe_width(_paint(widget), rotation) == _stripe_width(normal, rotation)


def _paint(widget):
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)
    return pixmap.toImage()


def _stripe_width(image, rotation):
    colors = ([image.pixelColor(image.width() // 4, y) for y in range(image.height())]
              if rotation in (90, 270) else
              [image.pixelColor(x, image.height() // 2) for x in range(image.width())])
    runs, count = [], 0
    for color in colors:
        if color.green() < 60 and max(color.red(), color.blue()) > 200:
            count += 1
        elif count:
            runs.append(count)
            count = 0
    assert len(runs) >= 3
    return median(runs[1:-1])


@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_pdf_pixmap_only_loupe_requests_pdf_source(pdf_window, qapp, rotation):
    window, backend = pdf_window
    for _ in range(rotation // 90):
        window.rotate_right()
    _settle(window, qapp)
    widget = window.viewer
    unit = window.model.spread_at()
    widget.prepare_display_units([(0, unit, list(widget._images), True)])
    _settle(window, qapp)
    assert widget.apply_prepared_display(unit,
        source_generation=window.image_cache.generation,
        source_identity=window._prepared_source_identity())
    assert widget._images[0].qimage is None
    widget.render(QPixmap(widget.size()))
    assert widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    assert window._pdf_magnifier_targets
    _wait(qapp, lambda: widget.magnifier_active)
    _settle(window, qapp)
    assert widget.magnifier_active


def test_pdf_synchronous_promotion_cancel_cannot_reinstall_old_loupe(pdf_window, qapp):
    window, backend = pdf_window
    widget = window.viewer
    widget.magnifierPdfResolutionRequested.connect(lambda *_: widget.cancel_magnifier())
    assert widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    assert widget._magnifier_key is None
    _settle(window, qapp)
    assert not widget.magnifier_active and not widget.magnifier_selecting


def test_pdf_pixmap_only_loupe_is_independent_of_normal_render_bucket(pdf_window, qapp, monkeypatch):
    window, backend = pdf_window
    widget = window.viewer
    unit = window.model.spread_at()
    widget.prepare_display_units([(0, unit, list(widget._images), True)])
    _settle(window, qapp)
    assert widget.apply_prepared_display(unit,
        source_generation=window.image_cache.generation,
        source_identity=window._prepared_source_identity())
    # Normal specs never change now: a separate promotion must still finish.
    spec = window._current_pdf_render_spec()
    monkeypatch.setattr(window, "_current_pdf_render_spec", lambda: spec)
    before = len(backend.requests)
    _paint(widget)
    assert widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    _wait(qapp, lambda: widget.magnifier_active)
    _settle(window, qapp)
    assert widget._images[0].qimage is None
    assert widget._pdf_loupe_ready
    assert len(backend.requests) > before


@pytest.mark.parametrize("action", ["cancel", "page", "book"])
@pytest.mark.parametrize("spread", [False, True])
def test_late_pdf_promotion_cannot_revive_loupe(pdf_window, qapp, tmp_path, action, spread):
    window, backend = pdf_window
    if spread:
        window.set_view_mode("spread")
        window.set_single_first_page(False)
        _wait(qapp, lambda: len(window.viewer._last_image_layout) == 2)
        _settle(window, qapp)
    widget = window.viewer
    backend.block = True
    assert widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    _wait(qapp, backend.started.is_set)
    if action == "cancel":
        widget.cancel_magnifier()
    elif action == "page":
        window.next_page()
    else:
        replacement = tmp_path / "別の合成.pdf"
        _write_pdf(replacement)
        # The real async open cancels the lens immediately while the old PDF
        # worker is still blocked; its eventual result belongs to the old book.
        assert window.open_path(replacement)
    assert not widget.magnifier_active and not widget.magnifier_selecting
    backend.release.set()
    if action == "book":
        _wait(qapp, lambda: window.book_session.source.source_path == replacement)
    _settle(window, qapp)
    assert not widget.magnifier_active and not widget.magnifier_selecting
    assert not window._pdf_magnifier_targets
    assert not widget._magnifier_spread_keys and widget._magnifier_key is None
