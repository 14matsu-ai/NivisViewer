from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
import time

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage

from app.application_controller import ApplicationController
from app.book_session import BookSession
from app.browser_model import BrowserItem, BrowserItemDiscovery, BrowserItemKind
from app.config_manager import ConfigManager
from app.image_source import BOOK_FILE_EXTENSIONS, ImageSourceError, create_image_source
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
from app.pdfium_service import PdfiumService
from app.thumbnail_provider import BrowserThumbnailProvider
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.viewer_widget import ViewerWidget


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
    service.shutdown()
    service.shutdown()
    assert backend.close_all_count == 1


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
        while window.model.total_pages != 3 and time.monotonic() < deadline:
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
            not {1, 2}.issubset(
                {request.page_index for request in backend.rendered}
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
