from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
from threading import Event, Lock
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy

from app.application_controller import ApplicationController
from app.book_session import BookSession
from app.browser_model import BrowserItem, BrowserItemDiscovery, BrowserItemKind
from app.config_manager import ConfigManager
from app.image_source import (
    BOOK_FILE_EXTENSIONS,
    ImageSource,
    ImageSourceError,
    create_image_source,
)
from app.image_cache import ImageCache
from app.metadata_store import MetadataStore
from app.page_model import PageModel
from app.pdf_backend import (
    MAX_PDF_RENDER_DIMENSION,
    MAX_PDF_RENDER_PIXELS,
    PageRenderSpec,
    PdfBackendError,
    PdfDocumentInfo,
    PdfErrorCode,
    PdfPageInfo,
    PdfRenderPriority,
    PdfRenderRequest,
    PdfRenderResult,
    bucket_render_size,
    clamp_render_size,
    validate_page_size,
)
from app.pdf_image_source import PdfImageSource
from app.pdfium_backend import PdfiumBackend
from app.pdfium_service import PdfiumService, PdfiumServiceState
from app.thumbnail_provider import BrowserThumbnailProvider
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.viewer_widget import ViewerWidget
from app.viewer_window import ViewerWindow


class FakeBackend:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.opened: list[str] = []
        self.rendered: list[PdfRenderRequest] = []
        self.closed: list[str] = []
        self.close_all_count = 0
        self._active = 0
        self.maximum_active = 0
        self._lock = Lock()

    def is_available(self) -> bool:
        return True

    def open_document(self, path, *, password=None, cancel_token=None):
        with self._call():
            self.opened.append(str(path))
            document_id = f"document-{len(self.opened)}"
            pages = (
                PdfPageInfo(0, 612, 792, 0, "表紙"),
                PdfPageInfo(1, 792, 612, 0, None),
                PdfPageInfo(2, 612, 792, 90, None),
            )
            return PdfDocumentInfo(
                document_id,
                str(path),
                len(pages),
                pages,
                False,
                "題名",
                "著者",
            )

    def render_page(self, request, *, cancel_token=None):
        with self._call():
            self.rendered.append(request)
            width, height = clamp_render_size(
                request.target_width_px,
                request.target_height_px,
            )
            return PdfRenderResult(
                request.document_id,
                request.page_index,
                width,
                height,
                "RGBA",
                bytes((10, 20, 30, 255)) * width * height,
                request.generation,
                request.purpose,
            )

    def close_document(self, document_id):
        with self._call():
            self.closed.append(document_id)

    def close_all(self):
        with self._call():
            self.close_all_count += 1

    class _Call:
        def __init__(self, owner):
            self.owner = owner

        def __enter__(self):
            with self.owner._lock:
                self.owner._active += 1
                self.owner.maximum_active = max(
                    self.owner.maximum_active,
                    self.owner._active,
                )
            if self.owner.delay:
                time.sleep(self.owner.delay)

        def __exit__(self, *_args):
            with self.owner._lock:
                self.owner._active -= 1

    def _call(self):
        return self._Call(self)


class BlockingRenderBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()

    def render_page(self, request, *, cancel_token=None):
        if request.page_index == 99:
            self.started.set()
            assert self.release.wait(2)
        return super().render_page(request, cancel_token=cancel_token)


class FailingRenderBackend(FakeBackend):
    def render_page(self, request, *, cancel_token=None):
        if request.page_index == 1:
            raise PdfBackendError(PdfErrorCode.RENDER_FAILED)
        return super().render_page(request, cancel_token=cancel_token)


class CancellableBlockingRenderBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.block_document_id: str | None = None
        self.fail_document_ids: set[str] = set()
        self.render_started = Event()
        self.release_render = Event()
        self.render_finished = Event()
        self.lifecycle: list[tuple[str, str, int]] = []

    def render_page(self, request, *, cancel_token=None):
        with self._call():
            self.rendered.append(request)
            if (
                request.document_id == self.block_document_id
                and request.page_index == 0
            ):
                self.lifecycle.append(
                    ("render-started", request.document_id, request.generation)
                )
                self.render_started.set()
                assert self.release_render.wait(3)
                try:
                    if cancel_token is not None and cancel_token.is_set():
                        raise PdfBackendError(PdfErrorCode.CANCELLED)
                finally:
                    self.lifecycle.append(
                        ("render-finished", request.document_id, request.generation)
                    )
                    self.render_finished.set()
            if request.document_id in self.fail_document_ids:
                raise PdfBackendError(PdfErrorCode.RENDER_FAILED)
            width, height = clamp_render_size(
                request.target_width_px,
                request.target_height_px,
            )
            return PdfRenderResult(
                request.document_id,
                request.page_index,
                width,
                height,
                "RGBA",
                bytes((10, 20, 30, 255)) * width * height,
                request.generation,
                request.purpose,
            )

    def close_document(self, document_id):
        self.lifecycle.append(("document-closed", document_id, -1))
        super().close_document(document_id)


class OrderedPdfLikeSource(ImageSource):
    supports_target_rendering = True

    def __init__(
        self,
        root: Path,
        *,
        block_index: int | None = None,
        page_count: int = 30,
    ) -> None:
        super().__init__(root)
        self.ids = [f"page{index}.jpg" for index in range(page_count)]
        self.block_index = block_index
        self.started: list[int] = []
        self.cancelled: list[str] = []
        self.block_started = Event()
        self.release_block = Event()

    def list_images(self) -> list[str]:
        return list(self.ids)

    def open_image_for_render(
        self,
        image_id: str,
        _render_spec=None,
        *,
        priority=0,
        generation=0,
    ) -> Image.Image:
        index = self.ids.index(image_id)
        self.started.append(index)
        if index == self.block_index:
            self.block_started.set()
            assert self.release_block.wait(2)
        return Image.new("RGB", (8, 12), "white")

    def open_image(self, image_id: str) -> Image.Image:
        raise AssertionError(f"target renderer was not used: {image_id}")

    def cancel_image_request(self, image_id: str) -> None:
        self.cancelled.append(image_id)

    def display_path(self, image_id: str) -> str:
        return image_id


def _complete_deferred_pdf_prefetch(window, qapp) -> None:
    assert window.image_cache.wait_for_done(5000)
    qapp.processEvents()
    window._pdf_prefetch_timer.stop()
    window._start_deferred_pdf_prefetch()
    assert window.image_cache.wait_for_done(5000)
    qapp.processEvents()


def _open_many_page_pdf_window(
    tmp_path,
    qapp,
    *,
    settings: dict[str, object],
    complete_prefetch: bool = True,
):
    pdf = tmp_path / "prefetch.pdf"
    pdf.write_bytes(b"fake")
    backend = FakeBackend()

    def open_document(path, *, password=None, cancel_token=None):
        backend.opened.append(str(path))
        pages = tuple(
            PdfPageInfo(index, 612, 792, 0, None)
            for index in range(30)
        )
        return PdfDocumentInfo(
            "prefetch-document",
            str(path),
            len(pages),
            pages,
            False,
        )

    backend.open_document = open_document
    service = PdfiumService(backend)
    source = PdfImageSource(pdf, pdfium_service=service)
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
    )
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(settings)
    window = ViewerWindow(
        config_manager=config,
        book_session=session,
        pdfium_service=service,
    )
    opened = session.open_book(pdf)
    assert window._finish_opened_book(opened, modal_on_empty=False)
    assert window.image_cache.wait_for_done(5000)
    qapp.processEvents()
    if complete_prefetch:
        _complete_deferred_pdf_prefetch(window, qapp)
    return window, session, service, backend


def _close_measured_pdf_window(window, session, service, qapp) -> None:
    window.close()
    qapp.processEvents()
    session.shutdown(wait_msecs=3000)
    service.shutdown(wait_seconds=2)


def test_pdf_extension_is_a_book_and_browser_item(tmp_path):
    (tmp_path / "日本語.PDF").write_bytes(b"not rendered")
    result = BrowserItemDiscovery().discover(tmp_path)

    assert ".pdf" in BOOK_FILE_EXTENSIONS
    assert [(item.display_name, item.kind) for item in result.items] == [
        ("日本語.PDF", BrowserItemKind.PDF)
    ]


def test_pdf_config_defaults_and_normalization(tmp_path):
    config = ConfigManager(tmp_path / "config.json")
    assert config.data["pdf_render_base_dpi"] == 96
    assert config.data["pdf_render_annotations"] is True
    config.apply(
        {
            "pdf_render_base_dpi": 999,
            "pdf_render_annotations": "yes",
        }
    )
    assert config.data["pdf_render_base_dpi"] == 300
    assert config.data["pdf_render_annotations"] is True


def test_metadata_infers_pdf_item_type():
    assert MetadataStore._infer_item_type("C:/本/資料.PDF") == "pdf"


def test_render_buckets_and_limits():
    assert bucket_render_size(1, 65) == (64, 128)
    width, height = clamp_render_size(100_000, 100_000)
    assert width <= MAX_PDF_RENDER_DIMENSION
    assert height <= MAX_PDF_RENDER_DIMENSION
    assert width * height <= MAX_PDF_RENDER_PIXELS
    edge_width, edge_height = clamp_render_size(
        MAX_PDF_RENDER_DIMENSION * 2,
        1,
    )
    assert edge_width == MAX_PDF_RENDER_DIMENSION
    assert edge_height >= 1
    structured = PdfBackendError(PdfErrorCode.RENDER_TOO_LARGE)
    assert structured.code is PdfErrorCode.RENDER_TOO_LARGE
    with pytest.raises(PdfBackendError) as exc_info:
        bucket_render_size(0, 10)
    assert exc_info.value.code is PdfErrorCode.INVALID_PAGE_SIZE


@pytest.mark.parametrize("bad", [(0, 1), (-1, 1), (float("nan"), 1), (1, float("inf"))])
def test_invalid_page_dimensions_are_rejected(bad):
    with pytest.raises(PdfBackendError) as exc_info:
        validate_page_size(*bad)
    assert exc_info.value.code is PdfErrorCode.INVALID_PAGE_SIZE


def test_page_render_spec_uses_dpr_and_bucket():
    assert PageRenderSpec(100, 200, 1.5).target_pixel_size == (192, 320)
    assert PageRenderSpec(100, 200, 2.0).target_pixel_size == (256, 448)


def test_image_cache_does_not_regenerate_inside_same_render_bucket(qapp):
    cache = ImageCache()
    assert cache.set_render_spec(PageRenderSpec(100, 100))
    generation = cache.generation
    assert not cache.set_render_spec(PageRenderSpec(101, 101))
    assert cache.generation == generation
    assert cache.set_render_spec(PageRenderSpec(129, 129))


@pytest.mark.parametrize(
    ("direction", "expected"),
    (
        (1, [10, 11, 12, 13, 9, 8, 7]),
        (-1, [10, 9, 8, 7, 11, 12, 13]),
    ),
)
def test_pdf_prefetch_prioritizes_near_pages_in_navigation_direction(
    tmp_path,
    qapp,
    direction,
    expected,
):
    source = OrderedPdfLikeSource(tmp_path, block_index=10)
    cache = ImageCache()
    cache.set_source(source, source.ids)

    cache.preload_around(
        10,
        radius=3,
        visible_indexes=(10,),
        preferred_direction=direction,
    )
    assert source.block_started.wait(1)
    source.release_block.set()
    assert cache.wait_for_done(3000)
    qapp.processEvents()

    assert source.started == expected
    assert cache._tasks == {}
    assert cache._in_flight == {}


def test_pdf_jump_removes_old_queued_prefetch_before_new_current(
    tmp_path,
    qapp,
):
    source = OrderedPdfLikeSource(tmp_path, block_index=10)
    cache = ImageCache()
    cache.set_source(source, source.ids)
    generation = cache.generation
    cache.preload_around(
        10,
        radius=0,
        visible_indexes=(10,),
        preferred_direction=1,
        prefetch_indexes=(11, 12, 13, 9, 8, 7),
    )
    assert source.block_started.wait(1)

    cache.preload_around(
        20,
        radius=0,
        visible_indexes=(20,),
        preferred_direction=0,
        prefetch_indexes=(21, 19, 22, 18, 23, 17),
    )

    assert (generation, 10) in cache._tasks
    assert all(
        (generation, index) not in cache._tasks
        for index in (7, 8, 9, 11, 12, 13)
    )
    source.release_block.set()
    assert cache.wait_for_done(3000)
    qapp.processEvents()

    assert source.started[0:2] == [10, 20]
    assert not {7, 8, 9, 11, 12, 13}.intersection(source.started)
    assert set(source.started[1:]) == set(range(17, 24))
    assert cache._tasks == {}
    assert cache._in_flight == {}


def test_pdf_direction_reversal_drops_old_forward_queue(
    tmp_path,
    qapp,
):
    source = OrderedPdfLikeSource(tmp_path, block_index=10)
    cache = ImageCache()
    cache.set_source(source, source.ids)
    generation = cache.generation
    cache.preload_around(
        10,
        radius=3,
        visible_indexes=(10,),
        preferred_direction=1,
    )
    assert source.block_started.wait(1)

    cache.preload_around(
        9,
        radius=0,
        visible_indexes=(9,),
        preferred_direction=-1,
    )

    assert (generation, 10) in cache._tasks
    assert all(
        (generation, index) not in cache._tasks
        for index in (7, 8, 11, 12, 13)
    )
    source.release_block.set()
    assert cache.wait_for_done(3000)
    qapp.processEvents()

    cache.preload_around(
        9,
        radius=3,
        visible_indexes=(9,),
        preferred_direction=-1,
    )
    assert cache.wait_for_done(3000)
    qapp.processEvents()

    assert source.started[:5] == [10, 9, 8, 7, 6]
    assert 13 not in source.started
    assert cache._tasks == {}
    assert cache._in_flight == {}


def test_active_pdf_prefetch_promoted_to_current_is_not_rendered_twice(
    tmp_path,
    qapp,
):
    source = OrderedPdfLikeSource(tmp_path, block_index=11)
    cache = ImageCache()
    cache.set_source(source, source.ids)
    cache.preload_around(
        10,
        radius=3,
        visible_indexes=(10,),
        preferred_direction=1,
    )
    assert source.block_started.wait(1)

    cache.preload_around(
        11,
        radius=0,
        visible_indexes=(11,),
        preferred_direction=1,
    )
    source.release_block.set()
    assert cache.wait_for_done(3000)
    qapp.processEvents()

    assert source.started.count(11) == 1
    assert cache.get(11) is not None
    assert cache._tasks == {}
    assert cache._in_flight == {}


def test_continuous_pdf_navigation_defers_far_prefetch_until_idle(
    tmp_path,
    qapp,
):
    window, session, service, backend = _open_many_page_pdf_window(
        tmp_path,
        qapp,
        settings={"view_mode": "single", "cache_size": 10},
        complete_prefetch=False,
    )
    try:
        window._pdf_prefetch_timer.stop()
        assert [request.page_index for request in backend.rendered] == [0, 1]

        for target in range(1, 6):
            assert window.image_cache.get(target) is not None
            window.model.go_to_index(target)
            window._refresh_view()
            assert window.image_cache.wait_for_done(5000)
            qapp.processEvents()
            window._pdf_prefetch_timer.stop()
            assert [request.page_index for request in backend.rendered] == list(
                range(target + 2)
            )

        assert sum(
            request.priority == PdfRenderPriority.VIEWER_CURRENT
            for request in backend.rendered
        ) == 1
        assert sum(
            request.priority >= PdfRenderPriority.VIEWER_NEXT
            for request in backend.rendered
        ) == 6
        before_idle = len(backend.rendered)
        window._start_deferred_pdf_prefetch()
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()

        assert [
            request.page_index for request in backend.rendered[before_idle:]
        ] == [7, 8]
        backend.rendered.clear()
        window.model.go_to_index(6)
        window._refresh_view()
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        window._pdf_prefetch_timer.stop()
        assert backend.rendered == []
    finally:
        _close_measured_pdf_window(
            window,
            session,
            service,
            qapp,
        )


def test_backward_pdf_navigation_rolls_previous_page_without_far_prefetch(
    tmp_path,
    qapp,
):
    window, session, service, backend = _open_many_page_pdf_window(
        tmp_path,
        qapp,
        settings={"view_mode": "single", "cache_size": 10},
        complete_prefetch=False,
    )
    try:
        window._pdf_prefetch_timer.stop()
        window.model.go_to_index(8)
        window._refresh_view()
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        window._pdf_prefetch_timer.stop()
        assert [
            request.page_index for request in backend.rendered[-2:]
        ] == [8, 9]

        backend.rendered.clear()
        window.model.go_to_index(7)
        window._refresh_view()
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        window._pdf_prefetch_timer.stop()

        assert [request.page_index for request in backend.rendered] == [7, 6]
        assert backend.rendered[0].priority == PdfRenderPriority.VIEWER_CURRENT
        assert backend.rendered[1].priority >= PdfRenderPriority.VIEWER_NEXT
    finally:
        _close_measured_pdf_window(
            window,
            session,
            service,
            qapp,
        )


@pytest.mark.parametrize(
    ("settings", "immediate_pages", "completed_pages"),
    (
        ({"view_mode": "single"}, [0, 1], [0, 1, 2, 3]),
        (
            {
                "view_mode": "spread",
                "single_first_page": False,
                "reading_direction": "ltr",
            },
            [0, 1, 2, 3],
            list(range(8)),
        ),
    ),
)
def test_pdf_idle_timer_eventually_completes_default_prefetch(
    tmp_path,
    qapp,
    settings,
    immediate_pages,
    completed_pages,
):
    window, session, service, backend = _open_many_page_pdf_window(
        tmp_path,
        qapp,
        settings=settings,
        complete_prefetch=False,
    )
    try:
        assert [
            request.page_index for request in backend.rendered
        ] == immediate_pages
        assert backend.rendered[0].priority == PdfRenderPriority.VIEWER_CURRENT
        visible_count = 1
        if settings["view_mode"] == "spread":
            assert (
                backend.rendered[1].priority
                == PdfRenderPriority.VIEWER_SPREAD_PARTNER
            )
            visible_count = 2
        assert all(
            request.priority >= PdfRenderPriority.VIEWER_NEXT
            for request in backend.rendered[visible_count:]
        )
        before_idle = len(backend.rendered)
        timeout = QSignalSpy(window._pdf_prefetch_timer.timeout)
        assert window._pdf_prefetch_timer.isActive()
        assert timeout.wait(1000)
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()

        assert [
            request.page_index for request in backend.rendered
        ] == completed_pages
        assert len(backend.rendered) - before_idle == (
            len(completed_pages) - len(immediate_pages)
        )
    finally:
        _close_measured_pdf_window(
            window,
            session,
            service,
            qapp,
        )


@pytest.mark.parametrize(
    ("preset", "expected_pages", "memory_mib"),
    (
        ("disabled", [0], 128),
        ("memory_saver", [0, 1], 128),
        ("standard", [0, 1, 2, 3], 256),
        ("more", [0, 1, 2, 3, 4], 512),
    ),
)
def test_pdf_prefetch_presets_control_display_unit_range_and_memory(
    tmp_path,
    qapp,
    preset,
    expected_pages,
    memory_mib,
):
    window, session, service, backend = _open_many_page_pdf_window(
        tmp_path,
        qapp,
        settings={
            "view_mode": "single",
            "viewer_prefetch_preset": preset,
        },
        complete_prefetch=False,
    )
    try:
        if window._pdf_prefetch_timer.isActive():
            _complete_deferred_pdf_prefetch(window, qapp)

        assert [
            request.page_index for request in backend.rendered
        ] == expected_pages
        assert window.viewer_cache_memory_mib == memory_mib
        assert (
            window.image_cache.cache_bytes
            + window.viewer.render_cache_bytes()
            <= memory_mib * 1024 * 1024
        )
        if preset == "disabled":
            assert not window._pdf_prefetch_timer.isActive()
    finally:
        _close_measured_pdf_window(
            window,
            session,
            service,
            qapp,
        )


def test_disabled_pdf_prefetch_still_loads_spread_partner(
    tmp_path,
    qapp,
):
    window, session, service, backend = _open_many_page_pdf_window(
        tmp_path,
        qapp,
        settings={
            "view_mode": "spread",
            "single_first_page": False,
            "reading_direction": "ltr",
            "viewer_prefetch_preset": "disabled",
        },
        complete_prefetch=False,
    )
    try:
        assert [request.page_index for request in backend.rendered] == [0, 1]
        assert not window._pdf_prefetch_timer.isActive()
    finally:
        _close_measured_pdf_window(
            window,
            session,
            service,
            qapp,
        )


def test_pdf_direction_priority_off_defers_nearest_background_work(
    tmp_path,
    qapp,
):
    window, session, service, backend = _open_many_page_pdf_window(
        tmp_path,
        qapp,
        settings={
            "view_mode": "single",
            "viewer_prefetch_direction_priority_enabled": False,
        },
        complete_prefetch=False,
    )
    try:
        assert [request.page_index for request in backend.rendered] == [0]
        assert window._pdf_prefetch_timer.isActive()

        window.model.go_to_index(10)
        window._refresh_view()
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        window._pdf_prefetch_timer.stop()
        assert backend.rendered[-1].page_index == 10

        backend.rendered.clear()
        window._start_deferred_pdf_prefetch()
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()

        assert [request.page_index for request in backend.rendered] == [
            11,
            9,
            12,
            8,
            13,
            7,
        ]
    finally:
        _close_measured_pdf_window(
            window,
            session,
            service,
            qapp,
        )


def test_custom_pdf_forward_zero_disables_rolling_and_live_apply_is_non_destructive(
    tmp_path,
    qapp,
):
    window, session, service, backend = _open_many_page_pdf_window(
        tmp_path,
        qapp,
        settings={"view_mode": "single"},
        complete_prefetch=False,
    )
    try:
        generation = window.image_cache.generation
        current = window.image_cache.get(0)
        window.config.apply(
            {
                "viewer_prefetch_preset": "custom",
                "viewer_prefetch_pdf_forward_units": 0,
                "viewer_prefetch_pdf_backward_units": 2,
                "viewer_cache_max_memory_mib": 64,
            }
        )
        qapp.processEvents()

        assert window.image_cache.generation == generation
        assert window.image_cache.get(0) is current
        assert window.image_cache.cache_byte_budget_mib == 64
        assert not window._pdf_prefetch_timer.isActive()

        backend.rendered.clear()
        window.model.go_to_index(8)
        window._refresh_view()
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        assert [request.page_index for request in backend.rendered] == [8]

        window._pdf_prefetch_timer.stop()
        window._start_deferred_pdf_prefetch()
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        assert [request.page_index for request in backend.rendered] == [
            8,
            7,
            6,
        ]
    finally:
        _close_measured_pdf_window(
            window,
            session,
            service,
            qapp,
        )


def test_pdf_prefetch_timer_does_not_start_work_after_viewer_close(
    tmp_path,
    qapp,
):
    window, session, service, backend = _open_many_page_pdf_window(
        tmp_path,
        qapp,
        settings={"view_mode": "single"},
        complete_prefetch=False,
    )
    rendered_before_close = len(backend.rendered)
    window.prepare_shutdown(wait_msecs=3000)
    qapp.processEvents()

    window._start_deferred_pdf_prefetch()

    assert len(backend.rendered) == rendered_before_close
    assert not window._pdf_prefetch_timer.isActive()
    session.shutdown(wait_msecs=3000)
    service.shutdown(wait_seconds=2)


def test_single_pdf_prefetch_survives_navigation_and_real_spec_changes_rerender(
    tmp_path,
    qapp,
):
    window, session, service, backend = _open_many_page_pdf_window(
        tmp_path,
        qapp,
        settings={"view_mode": "single", "cache_size": 10},
    )
    try:
        assert [request.page_index for request in backend.rendered] == [
            0,
            1,
            2,
            3,
        ]
        generation = window.image_cache.generation
        movement_render_counts = []
        for target, expected_prefetch in ((1, 4), (2, 5), (3, 6)):
            assert window.image_cache.get(target) is not None
            backend.rendered.clear()
            window.model.go_to_index(target)
            window._refresh_view()
            _complete_deferred_pdf_prefetch(window, qapp)
            assert all(
                request.page_index != target
                for request in backend.rendered
            )
            assert [request.page_index for request in backend.rendered] == [
                expected_prefetch
            ]
            movement_render_counts.append(len(backend.rendered))
            assert window.image_cache.generation == generation

        assert movement_render_counts == [1, 1, 1]
        backend.rendered.clear()
        window.model.go_to_index(2)
        window._refresh_view()
        _complete_deferred_pdf_prefetch(window, qapp)
        assert backend.rendered == []
        assert window.image_cache.generation == generation

        window.model.go_to_index(4)
        window._refresh_view()
        _complete_deferred_pdf_prefetch(window, qapp)
        assert 0 in window.image_cache._cache
        backend.rendered.clear()
        window.model.go_to_index(0)
        window._refresh_view()
        _complete_deferred_pdf_prefetch(window, qapp)
        assert backend.rendered == []
        assert window.image_cache.generation == generation

        retained_before_jump = set(window.image_cache._cache)
        backend.rendered.clear()
        window.model.go_to_index(20)
        window._refresh_view()
        assert window.image_cache.generation == generation
        assert retained_before_jump.issubset(window.image_cache._cache)
        _complete_deferred_pdf_prefetch(window, qapp)

        backend.rendered.clear()
        window.viewer.resize(900, 650)
        window._refresh_view()
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        assert window.image_cache.generation == generation + 1
        assert any(request.page_index == 20 for request in backend.rendered)
        resized_sizes = {
            (request.target_width_px, request.target_height_px)
            for request in backend.rendered
        }

        backend.rendered.clear()
        window.set_fit_mode("actual_size")
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        assert window.image_cache.generation == generation + 2
        assert any(request.page_index == 20 for request in backend.rendered)
        assert {
            (request.target_width_px, request.target_height_px)
            for request in backend.rendered
        } != resized_sizes

        backend.rendered.clear()
        window.viewer.set_manual_zoom(1.5)
        window._pdf_render_timer.stop()
        window._rerender_pdf()
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        assert window.image_cache.generation == generation + 3
        assert any(request.page_index == 20 for request in backend.rendered)
    finally:
        _close_measured_pdf_window(
            window,
            session,
            service,
            qapp,
        )


def test_spread_pdf_uses_prefetched_current_and_partner_without_rerender(
    tmp_path,
    qapp,
):
    window, session, service, backend = _open_many_page_pdf_window(
        tmp_path,
        qapp,
        settings={
            "view_mode": "spread",
            "single_first_page": False,
            "reading_direction": "ltr",
        },
    )
    try:
        assert window._visible_page_indexes == (0, 1)
        assert [
            (request.page_index, request.priority)
            for request in backend.rendered
        ] == [
            (0, PdfRenderPriority.VIEWER_CURRENT),
            (1, PdfRenderPriority.VIEWER_SPREAD_PARTNER),
            (2, int(PdfRenderPriority.VIEWER_NEXT) + 2),
            (3, int(PdfRenderPriority.VIEWER_NEXT) + 4),
            (4, int(PdfRenderPriority.VIEWER_NEXT) + 6),
            (5, int(PdfRenderPriority.VIEWER_NEXT) + 8),
            (6, int(PdfRenderPriority.VIEWER_NEXT) + 10),
            (7, int(PdfRenderPriority.VIEWER_NEXT) + 12),
        ]
        generation = window.image_cache.generation
        backend.rendered.clear()
        window.model.go_to_index(2)
        window._refresh_view()
        _complete_deferred_pdf_prefetch(window, qapp)

        assert window._visible_page_indexes == (2, 3)
        assert window.image_cache.generation == generation
        assert all(
            request.page_index not in {2, 3}
            for request in backend.rendered
        )
        assert [request.page_index for request in backend.rendered] == [8, 9]

        backend.rendered.clear()
        window.set_reading_direction("rtl")
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()
        assert window._visible_page_indexes == (3, 2)
        assert window.image_cache.generation == generation
        assert backend.rendered == []
    finally:
        _close_measured_pdf_window(
            window,
            session,
            service,
            qapp,
        )


def test_disjoint_pdf_jump_validates_retained_cache_when_page_returns(
    tmp_path,
    qapp,
):
    window, session, service, backend = _open_many_page_pdf_window(
        tmp_path,
        qapp,
        settings={"view_mode": "single", "cache_size": 20},
    )
    try:
        original = window.image_cache.get(0)
        assert original is not None
        original_signature = original.render_spec_signature
        generation = window.image_cache.generation

        window.viewer.resize(900, 650)
        window.model.go_to_index(20)
        window._refresh_view()
        assert window.image_cache.generation == generation
        assert window.image_cache.get(0) is original
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()

        backend.rendered.clear()
        window.model.go_to_index(0)
        window._refresh_view()
        assert window.image_cache.generation == generation + 1
        assert window.image_cache.wait_for_done(5000)
        qapp.processEvents()

        assert any(request.page_index == 0 for request in backend.rendered)
        rerendered = window.image_cache.get(0)
        assert rerendered is not None
        assert rerendered.render_spec_signature != original_signature
    finally:
        _close_measured_pdf_window(
            window,
            session,
            service,
            qapp,
        )


def test_pdf_book_switch_cancels_old_image_tasks_without_applying_error(
    tmp_path,
    qapp,
):
    pdf_a = tmp_path / "A.pdf"
    pdf_b = tmp_path / "B.pdf"
    pdf_a.write_bytes(b"fake")
    pdf_b.write_bytes(b"fake")
    backend = CancellableBlockingRenderBackend()
    service = PdfiumService(backend)
    source_a = PdfImageSource(pdf_a, pdfium_service=service)
    source_b = PdfImageSource(pdf_b, pdfium_service=service)
    backend.block_document_id = source_a.document_id
    sources = {
        pdf_a: source_a,
        pdf_b: source_b,
    }
    session = BookSession(
        source_factory=lambda path, **_kwargs: (sources[Path(path)], None),
    )
    delivered = []
    session.image_cache.pageLoaded.connect(delivered.append)
    try:
        session.open_book(pdf_a)
        old_generation = session.image_cache.generation
        session.image_cache.set_render_spec(
            PageRenderSpec(64, 64, size_bucket=(64, 64))
        )
        old_generation = session.image_cache.generation
        session.image_cache.preload_around(
            0,
            radius=1,
            visible_indexes=(0,),
        )
        assert backend.render_started.wait(1)
        assert (old_generation, 0) in session.image_cache._tasks
        assert (old_generation, 1) in session.image_cache._tasks

        session.open_book(pdf_b)
        current_generation = session.image_cache.generation
        assert current_generation != old_generation
        assert (old_generation, 0) in session.image_cache._tasks
        assert (old_generation, 1) not in session.image_cache._tasks
        assert (old_generation, 1) not in session.image_cache._in_flight
        assert source_a.document_id not in backend.closed
        assert all(
            not (
                request.document_id == source_a.document_id
                and request.page_index == 1
            )
            for request in backend.rendered
        )
        qapp.processEvents()
        assert not source_a._closed.is_set()
        assert (old_generation, 0) in session.image_cache._tasks

        session.image_cache.preload_around(
            0,
            radius=0,
            visible_indexes=(0,),
        )
        backend.release_render.set()
        assert session.image_cache.wait_for_done(3000)
        qapp.processEvents()
        assert service.flush(wait_seconds=2)
        qapp.processEvents()

        assert backend.render_finished.is_set()
        assert session.image_cache._tasks == {}
        assert session.image_cache._in_flight == {}
        assert all(cached.generation == current_generation for cached in delivered)
        assert all(cached.error is None for cached in delivered)
        current = session.image_cache.get(0)
        assert current is not None
        assert current.generation == current_generation
        assert current.error is None
        assert source_a.document_id in backend.closed
        assert backend.lifecycle.index(
            ("render-finished", source_a.document_id, old_generation)
        ) < backend.lifecycle.index(
            ("document-closed", source_a.document_id, -1)
        )
    finally:
        backend.release_render.set()
        session.shutdown(wait_msecs=3000)
        service.shutdown(wait_seconds=2)


def test_pdf_viewer_close_discards_late_cancelled_render(
    tmp_path,
    qapp,
):
    pdf = tmp_path / "close.pdf"
    pdf.write_bytes(b"fake")
    backend = CancellableBlockingRenderBackend()
    service = PdfiumService(backend)
    source = PdfImageSource(pdf, pdfium_service=service)
    backend.block_document_id = source.document_id
    session = BookSession(
        source_factory=lambda _path, **_kwargs: (source, None),
    )
    delivered = []
    session.image_cache.pageLoaded.connect(delivered.append)
    try:
        session.open_book(pdf)
        generation = session.image_cache.generation
        session.image_cache.set_render_spec(
            PageRenderSpec(64, 64, size_bucket=(64, 64))
        )
        generation = session.image_cache.generation
        session.image_cache.preload_around(
            0,
            radius=0,
            visible_indexes=(0,),
        )
        assert backend.render_started.wait(1)

        session.close_book()
        assert (generation, 0) in session.image_cache._tasks
        assert source.document_id not in backend.closed
        qapp.processEvents()
        assert not source._closed.is_set()
        assert (generation, 0) in session.image_cache._tasks

        backend.release_render.set()
        assert session.image_cache.wait_for_done(3000)
        qapp.processEvents()
        assert service.flush(wait_seconds=2)
        qapp.processEvents()

        assert delivered == []
        assert session.image_cache.source is None
        assert session.image_cache._cache == {}
        assert session.image_cache._tasks == {}
        assert session.image_cache._in_flight == {}
        assert backend.lifecycle.index(
            ("render-finished", source.document_id, generation)
        ) < backend.lifecycle.index(
            ("document-closed", source.document_id, -1)
        )
    finally:
        backend.release_render.set()
        session.shutdown(wait_msecs=3000)
        service.shutdown(wait_seconds=2)


def test_current_generation_pdf_cancellation_is_not_cached_or_delivered(
    tmp_path,
    qapp,
):
    pdf = tmp_path / "cancel.pdf"
    pdf.write_bytes(b"fake")
    backend = CancellableBlockingRenderBackend()
    service = PdfiumService(backend)
    source = PdfImageSource(pdf, pdfium_service=service)
    backend.block_document_id = source.document_id
    cache = ImageCache()
    delivered = []
    cache.pageLoaded.connect(delivered.append)
    try:
        cache.set_source(source, source.list_images())
        cache.set_render_spec(PageRenderSpec(64, 64, size_bucket=(64, 64)))
        generation = cache.generation
        cache.preload_around(0, radius=0, visible_indexes=(0,))
        assert backend.render_started.wait(1)

        source.cancel_image_request("pdf-page:0")
        backend.release_render.set()
        assert cache.wait_for_done(3000)
        qapp.processEvents()

        assert delivered == []
        assert cache.get(0) is None
        assert (generation, 0) not in cache._tasks
        assert (generation, 0) not in cache._in_flight
    finally:
        backend.release_render.set()
        cache.clear()
        qapp.processEvents()
        source.close()
        service.shutdown(wait_seconds=2)


@pytest.mark.parametrize("visible_indexes", [(0,), (0, 1)])
def test_current_pdf_render_failure_remains_an_error_for_single_and_spread(
    tmp_path,
    qapp,
    visible_indexes,
):
    pdf = tmp_path / "failure.pdf"
    pdf.write_bytes(b"fake")
    backend = CancellableBlockingRenderBackend()
    service = PdfiumService(backend)
    source = PdfImageSource(pdf, pdfium_service=service)
    backend.fail_document_ids.add(source.document_id)
    cache = ImageCache()
    delivered = []
    cache.pageLoaded.connect(delivered.append)
    try:
        cache.set_source(source, source.list_images())
        cache.set_render_spec(PageRenderSpec(64, 64, size_bucket=(64, 64)))
        cache.preload_around(
            0,
            radius=0,
            visible_indexes=visible_indexes,
        )
        assert cache.wait_for_done(3000)
        qapp.processEvents()

        expected_pages = set(visible_indexes)
        assert {cached.page_index for cached in delivered} == expected_pages
        assert all(cached.error for cached in delivered)
        assert all(cached.qimage is None for cached in delivered)
        assert all(cache.get(index).error for index in expected_pages)
    finally:
        cache.clear()
        qapp.processEvents()
        source.close()
        service.shutdown(wait_seconds=2)


def test_pdf_switch_cycle_reopens_same_path_without_stale_tracking(
    tmp_path,
    qapp,
):
    pdf_a = tmp_path / "A.pdf"
    pdf_b = tmp_path / "B.pdf"
    pdf_a.write_bytes(b"fake")
    pdf_b.write_bytes(b"fake")
    backend = FakeBackend()
    service = PdfiumService(backend)
    sources_by_path = {
        pdf_a: [
            PdfImageSource(pdf_a, pdfium_service=service),
            PdfImageSource(pdf_a, pdfium_service=service),
        ],
        pdf_b: [
            PdfImageSource(pdf_b, pdfium_service=service),
            PdfImageSource(pdf_b, pdfium_service=service),
        ],
    }

    def source_factory(path, **_kwargs):
        return sources_by_path[Path(path)].pop(0), None

    session = BookSession(source_factory=source_factory)
    document_ids = []
    try:
        for path in (pdf_a, pdf_b, pdf_a, pdf_b):
            session.open_book(path)
            document_ids.append(session.source.document_id)
            session.image_cache.set_render_spec(
                PageRenderSpec(64, 64, size_bucket=(64, 64))
            )
            session.image_cache.preload_around(
                0,
                radius=0,
                visible_indexes=(0,),
            )
            assert session.image_cache.wait_for_done(3000)
            qapp.processEvents()
            cached = session.image_cache.get(0)
            assert cached is not None
            assert cached.error is None
            assert cached.generation == session.image_cache.generation
            assert session.image_cache._tasks == {}
            assert session.image_cache._in_flight == {}

        assert len(set(document_ids)) == 4
        session.close_book()
        qapp.processEvents()
        assert service.flush(wait_seconds=2)
        assert session.image_cache._tasks == {}
        assert session.image_cache._in_flight == {}
    finally:
        session.shutdown(wait_msecs=3000)
        service.shutdown(wait_seconds=2)


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, (816, 1056)),
        (90, (1056, 816)),
        (180, (816, 1056)),
        (270, (1056, 816)),
    ],
)
def test_pdf_logical_size_applies_intrinsic_rotation_once(rotation, expected):
    assert PdfPageInfo(0, 612, 792, rotation).logical_size == expected


def test_viewer_uses_logical_size_instead_of_dpr_render_pixels(qapp):
    widget = ViewerWidget()
    high_dpi_image = QImage(1632, 2112, QImage.Format.Format_RGBA8888)
    viewer_image = widget.from_qimage(
        0,
        "pdf-page:0",
        high_dpi_image,
        (816, 1056),
        (1632, 2112),
    )
    assert widget._base_size(viewer_image).toTuple() == (816, 1056)

    widget.set_rotation_angle(90)
    pre_rotated = widget.from_qimage(
        0,
        "pdf-page:0",
        QImage(2112, 1632, QImage.Format.Format_RGBA8888),
        (816, 1056),
        (2112, 1632),
        True,
    )
    assert widget._base_size(pre_rotated).toTuple() == (1056, 816)
    assert widget._display_pixmap(pre_rotated).size().toTuple() == (2112, 1632)
    widget.close()


def test_pdf_service_serializes_different_documents():
    backend = FakeBackend(delay=0.03)
    service = PdfiumService(backend)
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            infos = list(
                executor.map(
                    service.open_document,
                    ("a.pdf", "b.pdf", "c.pdf"),
                )
            )
        assert len(infos) == 3
        assert backend.maximum_active == 1
        assert service.maximum_concurrent_calls == 1
    finally:
        service.shutdown()


def test_pdf_service_deduplicates_identical_pending_renders():
    backend = FakeBackend(delay=0.04)
    service = PdfiumService(backend)
    request = PdfRenderRequest("doc", 0, 64, 64)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _value: service.render_page(request), range(2)))
        assert len(results) == 2
        assert len(backend.rendered) == 1
    finally:
        service.shutdown()


def test_pdf_service_does_not_deduplicate_a_new_generation_onto_cancelled_future():
    backend = CancellableBlockingRenderBackend()
    backend.block_document_id = "doc"
    service = PdfiumService(backend)
    old_cancelled = Event()
    second_submit_returned = Event()
    original_submit = service._submit
    submit_count = 0

    def tracked_submit(*args, **kwargs):
        nonlocal submit_count
        future = original_submit(*args, **kwargs)
        submit_count += 1
        if submit_count == 2:
            second_submit_returned.set()
        return future

    service._submit = tracked_submit
    old_request = PdfRenderRequest("doc", 0, 64, 64, generation=1)
    new_request = PdfRenderRequest("doc", 0, 64, 64, generation=2)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            old_future = executor.submit(
                lambda: service.render_page(
                    old_request,
                    cancel_token=old_cancelled,
                )
            )
            assert backend.render_started.wait(1)
            new_future = executor.submit(service.render_page, new_request)
            assert second_submit_returned.wait(1)
            old_cancelled.set()
            backend.release_render.set()
            with pytest.raises(PdfBackendError) as exc_info:
                old_future.result(timeout=2)
            new_result = new_future.result(timeout=2)

        assert exc_info.value.code is PdfErrorCode.CANCELLED
        assert new_result.generation == 2
        assert [request.generation for request in backend.rendered] == [1, 2]
    finally:
        backend.release_render.set()
        service.shutdown(wait_seconds=2)


def test_pdf_service_prioritizes_viewer_over_queued_thumbnail_and_keeps_fifo():
    backend = BlockingRenderBackend()
    service = PdfiumService(backend)
    blocker = PdfRenderRequest("doc", 99, 8, 8)
    low_first = PdfRenderRequest(
        "doc",
        1,
        8,
        8,
        priority=int(PdfRenderPriority.THUMBNAIL_PREFETCH),
    )
    low_second = PdfRenderRequest(
        "doc",
        2,
        8,
        8,
        priority=int(PdfRenderPriority.THUMBNAIL_PREFETCH),
    )
    current = PdfRenderRequest(
        "doc",
        3,
        8,
        8,
        priority=int(PdfRenderPriority.VIEWER_CURRENT),
    )
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(service.render_page, blocker)]
            assert backend.started.wait(1)
            futures.extend(
                executor.submit(service.render_page, request)
                for request in (low_first, low_second, current)
            )
            time.sleep(0.02)
            backend.release.set()
            for future in futures:
                future.result(timeout=2)
        assert [request.page_index for request in backend.rendered] == [
            99,
            3,
            1,
            2,
        ]
    finally:
        backend.release.set()
        service.shutdown()


def test_pdf_service_promotes_an_identical_pending_request():
    backend = BlockingRenderBackend()
    service = PdfiumService(backend)
    blocker = PdfRenderRequest("doc", 99, 8, 8)
    promoted_low = PdfRenderRequest(
        "doc",
        4,
        8,
        8,
        priority=int(PdfRenderPriority.THUMBNAIL_PREFETCH),
    )
    promoted_high = PdfRenderRequest(
        "doc",
        4,
        8,
        8,
        priority=int(PdfRenderPriority.VIEWER_CURRENT),
    )
    ordinary = PdfRenderRequest(
        "doc",
        5,
        8,
        8,
        priority=int(PdfRenderPriority.THUMBNAIL_VISIBLE),
    )
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(service.render_page, blocker)]
            assert backend.started.wait(1)
            futures.append(executor.submit(service.render_page, promoted_low))
            futures.append(executor.submit(service.render_page, ordinary))
            futures.append(executor.submit(service.render_page, promoted_high))
            time.sleep(0.02)
            backend.release.set()
            for future in futures:
                future.result(timeout=2)
        assert [request.page_index for request in backend.rendered] == [99, 4, 5]
    finally:
        backend.release.set()
        service.shutdown()


def test_pdf_service_does_not_start_cancelled_pending_request():
    backend = BlockingRenderBackend()
    service = PdfiumService(backend)
    token = Event()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            blocker = executor.submit(
                service.render_page,
                PdfRenderRequest("doc", 99, 8, 8),
            )
            assert backend.started.wait(1)
            cancelled = executor.submit(
                lambda: service.render_page(
                    PdfRenderRequest("doc", 6, 8, 8),
                    cancel_token=token,
                )
            )
            time.sleep(0.02)
            token.set()
            backend.release.set()
            blocker.result(timeout=2)
            with pytest.raises(PdfBackendError) as exc_info:
                cancelled.result(timeout=2)
        assert exc_info.value.code is PdfErrorCode.CANCELLED
        assert [request.page_index for request in backend.rendered] == [99]
    finally:
        backend.release.set()
        service.shutdown()


def test_pdf_service_continues_after_render_exception():
    backend = FailingRenderBackend()
    service = PdfiumService(backend)
    try:
        with pytest.raises(PdfBackendError) as exc_info:
            service.render_page(PdfRenderRequest("doc", 1, 8, 8))
        assert exc_info.value.code is PdfErrorCode.RENDER_FAILED
        result = service.render_page(PdfRenderRequest("doc", 2, 8, 8))
        assert result.page_index == 2
    finally:
        service.shutdown()


def test_pdf_service_skips_cancelled_request_and_shutdown_is_idempotent():
    backend = FakeBackend()
    service = PdfiumService(backend)
    cancelled = Event()
    cancelled.set()
    with pytest.raises(PdfBackendError) as exc_info:
        service.render_page(
            PdfRenderRequest("doc", 0, 64, 64),
            cancel_token=cancelled,
        )
    assert exc_info.value.code is PdfErrorCode.CANCELLED
    assert not backend.rendered
    assert service.shutdown()
    assert service.shutdown()
    assert backend.close_all_count == 1
    assert not service._worker.is_alive()


def test_controller_shares_one_pdf_service_with_browser_and_viewers(
    tmp_path,
    qapp: QApplication,
):
    backend = FakeBackend()
    service = PdfiumService(backend)
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
        pdfium_service=service,
    )
    browser = controller.create_browser_window()
    first = controller.create_viewer_window()
    second = controller.create_viewer_window()
    try:
        assert browser.pdfium_service is service
        assert browser.thumbnail_provider._pdfium_service is service
        assert first.pdfium_service is service
        assert second.pdfium_service is service
    finally:
        first.close()
        second.close()
        browser.close()
        qapp.processEvents()
        controller.shutdown()
    assert backend.close_all_count == 1


def test_browser_close_keeps_shared_pdf_service_running_for_viewer(
    tmp_path,
    qapp: QApplication,
):
    pdf = tmp_path / "browser-close.pdf"
    pdf.write_bytes(b"fake")
    backend = FakeBackend()
    service = PdfiumService(backend)
    shutdown = Mock(wraps=service.shutdown)
    service.shutdown = shutdown
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
        pdfium_service=service,
    )
    browser = controller.create_browser_window()
    viewer = controller.open_path(pdf, open_in_new_window=True)
    try:
        deadline = time.monotonic() + 3
        while viewer.model.total_pages != 3 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert viewer.model.total_pages == 3
        assert not browser._owns_pdfium_service
        assert not viewer._owns_pdfium_service

        assert viewer.image_cache.wait_for_done(3000)
        qapp.processEvents()
        backend.rendered.clear()
        viewer.image_cache.set_render_spec(
            PageRenderSpec(96, 96, size_bucket=(64, 64))
        )
        viewer.image_cache.preload_around(1, radius=0, visible_indexes=(1,))
        deadline = time.monotonic() + 3
        while viewer.image_cache.get(1) is None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        cached_before_close = viewer.image_cache.get(1)
        assert cached_before_close is not None
        assert cached_before_close.error is None
        assert len(backend.rendered) == 1
        assert shutdown.call_count == 0

        browser.close()
        qapp.processEvents()

        assert controller.get_browser_window() is None
        assert controller.viewer_windows == (viewer,)
        assert service.state is PdfiumServiceState.RUNNING
        assert shutdown.call_count == 0
        assert backend.close_all_count == 0

        backend.rendered.clear()
        viewer.image_cache.set_render_spec(
            PageRenderSpec(96, 96, size_bucket=(128, 128))
        )
        viewer.image_cache.preload_around(1, radius=0, visible_indexes=(1,))
        deadline = time.monotonic() + 3
        while viewer.image_cache.get(1) is None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        cached = viewer.image_cache.get(1)
        assert cached is not None
        assert cached.error is None
        assert len(backend.rendered) == 1
        assert backend.rendered[0].page_index == 1
        assert service.state is PdfiumServiceState.RUNNING
        assert shutdown.call_count == 0
    finally:
        viewer.close()
        qapp.processEvents()
        controller.shutdown()
    assert shutdown.call_count == 1
    assert backend.close_all_count == 1


def test_viewer_close_keeps_shared_pdf_service_running_for_browser_preview(
    tmp_path,
    qapp: QApplication,
):
    pdf = tmp_path / "viewer-close.pdf"
    pdf.write_bytes(b"fake")
    backend = FakeBackend()
    service = PdfiumService(backend)
    shutdown = Mock(wraps=service.shutdown)
    service.shutdown = shutdown
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
        pdfium_service=service,
    )
    browser = controller.create_browser_window()
    viewer = controller.open_path(pdf, open_in_new_window=True)
    try:
        deadline = time.monotonic() + 3
        while viewer.model.total_pages != 3 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert viewer.model.total_pages == 3

        viewer.close()
        qapp.processEvents()

        assert controller.viewer_windows == ()
        assert controller.get_browser_window() is browser
        assert service.state is PdfiumServiceState.RUNNING
        assert shutdown.call_count == 0
        result = BrowserThumbnailProvider.load_thumbnail_result(
            BrowserItem(
                display_name=pdf.name,
                path=pdf,
                kind=BrowserItemKind.PDF,
                modified_at=None,
            ),
            64,
            pdfium_service=service,
        )
        assert result.image is not None
        assert not result.image.isNull()
        assert service.state is PdfiumServiceState.RUNNING
        assert shutdown.call_count == 0
    finally:
        browser.close()
        qapp.processEvents()
        controller.shutdown()
    assert shutdown.call_count == 1
    assert backend.close_all_count == 1


def test_pdf_viewer_async_open_spread_render_and_history(
    tmp_path,
    qapp: QApplication,
):
    pdf = tmp_path / "日本語の本.pdf"
    pdf.write_bytes(b"fake")
    backend = FakeBackend()
    service = PdfiumService(backend)
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
        pdfium_service=service,
    )
    window = controller.open_path(pdf)
    try:
        deadline = time.monotonic() + 3
        while (
            (
                window.model.total_pages != 3
                or window.presentation_state.displayed_page != 0
            )
            and time.monotonic() < deadline
        ):
            qapp.processEvents()
            time.sleep(0.005)
        assert isinstance(window.book_session.source, PdfImageSource)
        assert window.model.total_pages == 3
        assert window.slider.maximum() == 2
        assert controller.metadata_store.list_history()[0].item_type == "pdf"

        window.set_view_mode("spread")
        window.set_reading_direction("rtl")
        window.set_treat_wide_image_as_single(False)
        window.config.set("join_spread_pages", True)
        backend.rendered.clear()
        window.next_page()
        window.set_fit_mode("actual_size")
        window.viewer.set_manual_zoom(1.5)
        window.rotate_right()
        deadline = time.monotonic() + 3
        while (
            (
                not {1, 2}.issubset(
                    {request.page_index for request in backend.rendered}
                )
                or window.presentation_state.displayed_page
                != window.model.focused_index
            )
            and time.monotonic() < deadline
        ):
            qapp.processEvents()
            time.sleep(0.005)
        assert backend.rendered
        assert any(request.purpose == "viewer" for request in backend.rendered)
        priorities = {request.page_index: request.priority for request in backend.rendered}
        assert priorities[1] == PdfRenderPriority.VIEWER_CURRENT
        assert priorities[2] == PdfRenderPriority.VIEWER_SPREAD_PARTNER
        assert "日本語の本.pdf" in window.status.currentMessage()
    finally:
        window.close()
        qapp.processEvents()
        controller.shutdown()
    assert backend.close_all_count == 1


def test_pdf_open_position_defaults_to_first_and_can_resume_saved_page(
    tmp_path,
    qapp: QApplication,
):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"fake")
    backend = FakeBackend()
    service = PdfiumService(backend)
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
        pdfium_service=service,
    )
    controller.metadata_store.record_book_opened(
        str(pdf),
        item_type="pdf",
        start_page_index=2,
        total_pages=3,
    )
    first = controller.open_path(pdf, open_in_new_window=True)
    try:
        deadline = time.monotonic() + 3
        while first.model.total_pages != 3 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert first.model.current_index == 0
        assert controller.metadata_store.get_reading_progress(str(pdf)).page_index == 2

        controller.config.apply({"book_open_position": "resume_last"})
        assert first.model.current_index == 0
        resumed = controller.open_path(pdf, open_in_new_window=True)
        deadline = time.monotonic() + 3
        while resumed.model.total_pages != 3 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert resumed.model.current_index == 2
    finally:
        for window in tuple(controller.viewer_windows):
            window.close()
        qapp.processEvents()
        controller.shutdown()


def test_closing_pdf_viewer_flushes_document_before_file_operation(
    tmp_path,
    qapp,
):
    pdf = tmp_path / "open.pdf"
    pdf.write_bytes(b"fake")
    backend = FakeBackend()
    service = PdfiumService(backend)
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
        pdfium_service=service,
    )
    window = controller.open_path(pdf)
    deadline = time.monotonic() + 3
    while window.model.total_pages == 0 and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    document_id = window.book_session.source.document_id

    assert controller.close_viewers((window,))
    assert document_id in backend.closed
    renamed = tmp_path / "renamed.pdf"
    pdf.rename(renamed)
    assert renamed.is_file()
    controller.shutdown()


def test_pdf_backend_unavailable_isolated_to_pdf(tmp_path, monkeypatch):
    pdf = tmp_path / "本.pdf"
    pdf.write_bytes(b"fake")

    def missing_import(name):
        if name == "pypdfium2":
            raise ImportError("missing")
        raise AssertionError(name)

    monkeypatch.setattr("app.pdfium_backend.importlib.import_module", missing_import)
    service = PdfiumService(PdfiumBackend())
    try:
        with pytest.raises(ImageSourceError) as exc_info:
            create_image_source(pdf, pdfium_service=service)
        assert exc_info.value.code == PdfErrorCode.BACKEND_UNAVAILABLE.value
        item = BrowserItem("本.pdf", pdf, BrowserItemKind.PDF, pdf.stat().st_mtime)
        result = BrowserThumbnailProvider.load_thumbnail_result(
            item,
            96,
            pdfium_service=service,
        )
        assert result.image is None

        image_path = tmp_path / "1.png"
        with Image.new("RGB", (4, 4), "white") as image:
            image.save(image_path)
        source, selected = create_image_source(image_path)
        assert selected == str(image_path)
        source.close()
    finally:
        service.shutdown()


def test_password_errors_are_structured_without_password_text():
    required = PdfiumBackend._classify_open_error(
        RuntimeError("PDFium password error"),
        password_supplied=False,
    )
    invalid = PdfiumBackend._classify_open_error(
        RuntimeError("PDFium password error"),
        password_supplied=True,
    )
    assert required.code is PdfErrorCode.PASSWORD_REQUIRED
    assert invalid.code is PdfErrorCode.INVALID_PASSWORD
    assert "password" not in required.user_message.casefold()


def test_pdf_image_source_exposes_logical_pages_and_renders(tmp_path):
    pdf = tmp_path / "本.pdf"
    pdf.write_bytes(b"fake")
    backend = FakeBackend()
    service = PdfiumService(backend)
    try:
        source = PdfImageSource(pdf, pdfium_service=service)
        assert source.list_images() == [
            "pdf-page:0",
            "pdf-page:1",
            "pdf-page:2",
        ]
        assert source.logical_size("pdf-page:0") == (816, 1056)
        assert source.logical_size("pdf-page:2") == (1056, 816)
        assert source.display_path("pdf-page:0").endswith("| 表紙")
        with source.open_image_for_render(
            "pdf-page:1",
            PageRenderSpec(100, 120, 1.5),
            priority=int(PdfRenderPriority.VIEWER_CURRENT),
            generation=7,
        ) as image:
            assert image.size == (192, 192)
            assert image.info["logical_size"] == (1056, 816)
            assert image.info["pdf_render_size"] == (192, 192)
        source.close()
        source.close()
        time.sleep(0.05)
        assert source.document_id in backend.closed
    finally:
        service.shutdown()


def test_pdf_image_source_page_bounds_are_structured(tmp_path):
    pdf = tmp_path / "本.pdf"
    pdf.write_bytes(b"fake")
    service = PdfiumService(FakeBackend())
    try:
        source = PdfImageSource(pdf, pdfium_service=service)
        with pytest.raises(ImageSourceError) as exc_info:
            source.logical_size("pdf-page:99")
        assert exc_info.value.code == PdfErrorCode.PAGE_OUT_OF_RANGE.value
        source.close()
    finally:
        service.shutdown()


def test_pdf_page_model_uses_metadata_without_rendering(tmp_path):
    pdf = tmp_path / "本.pdf"
    pdf.write_bytes(b"fake")
    backend = FakeBackend()
    service = PdfiumService(backend)
    try:
        source = PdfImageSource(pdf, pdfium_service=service)
        model = PageModel()
        model.set_source(source)
        assert model.get_image_size(0) == (816, 1056)
        assert model.is_wide_image(1)
        assert not backend.rendered
        source.close()
    finally:
        service.shutdown()


def test_create_image_source_uses_injected_pdf_service(tmp_path):
    pdf = tmp_path / "本.pdf"
    pdf.write_bytes(b"fake")
    service = PdfiumService(FakeBackend())
    try:
        source, selected = create_image_source(pdf, pdfium_service=service)
        assert isinstance(source, PdfImageSource)
        assert selected is None
        source.close()
    finally:
        service.shutdown()


def test_pdf_async_prepare_failure_keeps_current_book(tmp_path, qapp):
    image_path = tmp_path / "images" / "1.png"
    image_path.parent.mkdir()
    with Image.new("RGB", (10, 20), "white") as image:
        image.save(image_path)
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"fake")

    def factory(path, **kwargs):
        if Path(path).suffix.lower() == ".pdf":
            raise ImageSourceError("broken", code=PdfErrorCode.CORRUPT_PDF.value)
        return create_image_source(path, **kwargs)

    session = BookSession(source_factory=factory)
    session.open_book(image_path)
    original_source = session.source
    failures = []
    session.async_open_failed.connect(failures.append)
    session.open_book_async(pdf)
    assert session.wait_for_async(5000)
    deadline = time.monotonic() + 2
    while not failures and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert failures
    assert session.source is original_source
    assert session.current_path == image_path
    session.shutdown()


def test_pdf_thumbnail_renders_only_first_page(tmp_path, qapp):
    pdf = tmp_path / "本.pdf"
    pdf.write_bytes(b"fake")
    backend = FakeBackend()
    service = PdfiumService(backend)
    item = BrowserItem("本.pdf", pdf, BrowserItemKind.PDF, pdf.stat().st_mtime)
    try:
        result = BrowserThumbnailProvider.load_thumbnail_result(
            item,
            96,
            pdfium_service=service,
        )
        assert result.image is not None
        assert result.image.width() <= 96
        assert [request.page_index for request in backend.rendered] == [0]
        assert backend.rendered[0].purpose == "thumbnail"
        assert backend.rendered[0].priority == PdfRenderPriority.THUMBNAIL_VISIBLE
    finally:
        service.shutdown()


@pytest.mark.parametrize(
    ("thumbnail_priority", "pdf_priority"),
    [
        (ThumbnailPriority.VISIBLE, PdfRenderPriority.THUMBNAIL_VISIBLE),
        (ThumbnailPriority.SELECTED, PdfRenderPriority.THUMBNAIL_SELECTED),
        (ThumbnailPriority.PREFETCH, PdfRenderPriority.THUMBNAIL_PREFETCH),
    ],
)
def test_pdf_thumbnail_scheduler_priority_reaches_shared_service(
    tmp_path,
    qapp,
    thumbnail_priority,
    pdf_priority,
):
    pdf = tmp_path / f"{thumbnail_priority.name}.pdf"
    pdf.write_bytes(b"fake")
    backend = FakeBackend()
    service = PdfiumService(backend)
    provider = BrowserThumbnailProvider(
        pdfium_service=service,
        disk_cache_enabled=False,
    )
    item = BrowserItem("本.pdf", pdf, BrowserItemKind.PDF, pdf.stat().st_mtime)
    try:
        generation = provider.begin_generation()
        provider.request(
            item,
            96,
            generation=generation,
            priority=thumbnail_priority,
        )
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert backend.rendered[0].priority == pdf_priority
    finally:
        provider.close()
        service.shutdown()


class FakeBitmap:
    def __init__(self) -> None:
        self.closed = False

    def to_pil(self):
        return Image.new("RGBA", (20, 30), "white")

    def close(self):
        self.closed = True


class FakePage:
    def __init__(self, owner, index):
        self.owner = owner
        self.index = index
        self.closed = False

    def get_size(self):
        return self.owner.sizes[self.index]

    def get_rotation(self):
        return self.owner.rotations[self.index]

    def render(self, **kwargs):
        self.owner.render_arguments.append(kwargs)
        bitmap = FakeBitmap()
        self.owner.bitmaps.append(bitmap)
        return bitmap

    def close(self):
        self.closed = True
        self.owner.closed_pages += 1


class FakeDocument:
    def __init__(self, owner):
        self.owner = owner
        self.closed = False

    def __len__(self):
        return len(self.owner.sizes)

    def __getitem__(self, index):
        return FakePage(self.owner, index)

    def get_page_label(self, index):
        return "i" if index == 0 else ""

    def get_metadata_dict(self):
        return {"Title": "Fake title", "Author": "Fake author"}

    def close(self):
        self.closed = True
        self.owner.document_closed += 1


class FakePdfiumModule:
    def __init__(self, sizes=((612, 792),), rotations=(0,)):
        self.sizes = sizes
        self.rotations = rotations
        self.documents = []
        self.render_arguments = []
        self.bitmaps = []
        self.closed_pages = 0
        self.document_closed = 0

    def PdfDocument(self, _path, password=None):  # noqa: N802
        document = FakeDocument(self)
        self.documents.append(document)
        return document


def test_pdfium_backend_v5_page_api_and_explicit_lifetimes(tmp_path):
    pdf = tmp_path / "日本語.pdf"
    pdf.write_bytes(b"fake")
    module = FakePdfiumModule(
        sizes=((612, 792), (792, 612)),
        rotations=(0, 90),
    )
    backend = PdfiumBackend(pdfium_module=module)

    info = backend.open_document(str(pdf))
    assert info.page_count == 2
    assert info.pages[1].rotation_degrees == 90
    assert info.pages[0].label == "i"
    assert info.title == "Fake title"
    result = backend.render_page(
        PdfRenderRequest(info.document_id, 0, 200, 300, rotation_degrees=90)
    )
    assert result.mode == "RGBA"
    assert result.pixels == bytes((255, 255, 255, 255)) * 20 * 30
    arguments = module.render_arguments[0]
    assert arguments["rotation"] == 90
    assert arguments["fill_color"] == (255, 255, 255, 255)
    assert arguments["draw_annots"] is True
    assert module.bitmaps[0].closed
    backend.close_document(info.document_id)
    backend.close_document(info.document_id)
    assert module.document_closed == 1


def test_pdfium_backend_close_failure_keeps_document_for_retry():
    class FlakyDocument:
        def __init__(self) -> None:
            self.attempts = 0

        def close(self) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("close locked")

    document = FlakyDocument()
    backend = PdfiumBackend(pdfium_module=FakePdfiumModule())
    backend._documents = {
        "document": SimpleNamespace(document=document),
    }

    with pytest.raises(PdfBackendError) as exc_info:
        backend.close_document("document")

    assert exc_info.value.code is PdfErrorCode.INTERNAL_ERROR
    assert "document" in backend._documents

    backend.close_document("document")
    assert backend._documents == {}
    assert document.attempts == 2


def test_pdfium_backend_close_all_attempts_every_document_and_aggregates_errors(
    caplog,
):
    attempts: list[str] = []

    class ClosingDocument:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self.name = name
            self.fail = fail

        def close(self) -> None:
            attempts.append(self.name)
            if self.fail:
                raise RuntimeError(f"close failed: {self.name}")

    backend = PdfiumBackend(pdfium_module=FakePdfiumModule())
    first = ClosingDocument("first", fail=True)
    backend._documents = {
        "first": SimpleNamespace(document=first),
        "second": SimpleNamespace(document=ClosingDocument("second")),
        "third": SimpleNamespace(document=ClosingDocument("third")),
    }

    with caplog.at_level(logging.ERROR, logger="nivisviewer.pdfium"):
        with pytest.raises(PdfBackendError) as exc_info:
            backend.close_all()

    assert exc_info.value.code is PdfErrorCode.INTERNAL_ERROR
    assert exc_info.value.debug_message is not None
    assert "1" in exc_info.value.debug_message
    assert attempts == ["first", "second", "third"]
    assert tuple(backend._documents) == ("first",)
    close_errors = [
        record
        for record in caplog.records
        if record.name == "nivisviewer.pdfium"
        and "PDF document close failed" in record.getMessage()
    ]
    assert len(close_errors) == 1

    first.fail = False
    backend.close_all()
    assert attempts == ["first", "second", "third", "first"]
    assert backend._documents == {}
    assert sum(
        record.name == "nivisviewer.pdfium"
        and "PDF document close failed" in record.getMessage()
        for record in caplog.records
    ) == 1


def test_pdf_service_flush_reports_async_close_failure_until_retry():
    class FlakyCloseBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.close_attempts = 0

        def close_document(self, document_id):
            self.close_attempts += 1
            if self.close_attempts == 1:
                raise PdfBackendError(
                    PdfErrorCode.INTERNAL_ERROR,
                    debug_message="close locked",
                )
            super().close_document(document_id)

    backend = FlakyCloseBackend()
    service = PdfiumService(backend, auto_probe=False)
    try:
        service.close_document("document")
        assert not service.flush(wait_seconds=2)
        assert backend.close_attempts == 1

        service.close_document("document", wait=True)
        assert service.flush(wait_seconds=2)
        assert backend.close_attempts == 2
        assert backend.closed == ["document"]
    finally:
        assert service.shutdown(wait_seconds=2)


def test_pdfium_backend_rejects_zero_pages_and_closes(tmp_path):
    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(b"fake")
    module = FakePdfiumModule(sizes=(), rotations=())
    backend = PdfiumBackend(pdfium_module=module)

    with pytest.raises(PdfBackendError) as exc_info:
        backend.open_document(str(pdf))
    assert exc_info.value.code is PdfErrorCode.ZERO_PAGES
    assert module.document_closed == 1


def test_pdfium_backend_rejects_too_many_pages_and_invalid_sizes(tmp_path):
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"fake")
    too_many = FakePdfiumModule(sizes=((1, 1), (1, 1)), rotations=(0, 0))
    with pytest.raises(PdfBackendError) as exc_info:
        PdfiumBackend(pdfium_module=too_many, maximum_pages=1).open_document(str(pdf))
    assert exc_info.value.code is PdfErrorCode.TOO_MANY_PAGES
    assert too_many.document_closed == 1

    invalid = FakePdfiumModule(sizes=((0, 100),), rotations=(0,))
    with pytest.raises(PdfBackendError) as exc_info:
        PdfiumBackend(pdfium_module=invalid).open_document(str(pdf))
    assert exc_info.value.code is PdfErrorCode.INVALID_PAGE_SIZE
    assert invalid.document_closed == 1


def test_real_pypdfium2_open_and_render_when_available(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    pdf = tmp_path / "日本語 ページサイズ混在.pdf"
    document = pdfium.PdfDocument.new()
    try:
        portrait = document.new_page(612, 792)
        portrait.close()
        landscape = document.new_page(792, 612)
        landscape.close()
        rotated = document.new_page(612, 792)
        rotated.set_rotation(90)
        rotated.close()
        document.save(pdf)
    finally:
        document.close()

    original_mtime = pdf.stat().st_mtime_ns
    backend = PdfiumBackend()
    info = backend.open_document(str(pdf))
    try:
        assert info.page_count == 3
        assert (info.pages[0].width_points, info.pages[0].height_points) == (
            612,
            792,
        )
        assert (info.pages[1].width_points, info.pages[1].height_points) == (
            792,
            612,
        )
        assert (info.pages[2].width_points, info.pages[2].height_points) == (
            612,
            792,
        )
        assert info.pages[2].logical_size == (1056, 816)
        result = backend.render_page(
            PdfRenderRequest(info.document_id, 1, 384, 384)
        )
        assert result.mode == "RGBA"
        assert result.width > result.height
        assert len(result.pixels) == result.width * result.height * 4
        with Image.frombytes(
            result.mode,
            (result.width, result.height),
            result.pixels,
        ) as rendered:
            assert rendered.getpixel((0, 0)) == (255, 255, 255, 255)
        rotated_result = backend.render_page(
            PdfRenderRequest(info.document_id, 2, 384, 384)
        )
        assert rotated_result.width > rotated_result.height
        combined_rotation = backend.render_page(
            PdfRenderRequest(
                info.document_id,
                2,
                384,
                384,
                rotation_degrees=90,
            )
        )
        assert combined_rotation.width < combined_rotation.height
    finally:
        backend.close_document(info.document_id)
    assert pdf.stat().st_mtime_ns == original_mtime
    assert set(tmp_path.iterdir()) == {pdf}
    renamed = tmp_path / "日本語 rename.pdf"
    pdf.rename(renamed)
    moved_dir = tmp_path / "移動先"
    moved_dir.mkdir()
    moved = moved_dir / renamed.name
    renamed.rename(moved)
    assert moved.is_file()
