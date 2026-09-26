from __future__ import annotations

from .i18n import tr
from .cloud_files import require_local


from pathlib import Path
from threading import Event, RLock

from PIL import Image

from .image_source import ImageSource, ImageSourceError
from .pdf_backend import (
    PDF_LOGICAL_DPI,
    PageRenderSpec,
    PdfBackendError,
    PdfDocumentInfo,
    PdfErrorCode,
    PdfRenderPriority,
    PdfRenderRequest,
    bucket_render_size,
)


class PdfImageSource(ImageSource):
    load_sizes_lazily = False
    supports_target_rendering = True

    def __init__(
        self,
        pdf_path: str | Path,
        *,
        pdfium_service,
        cancel_token=None,
        base_dpi: int = PDF_LOGICAL_DPI,
        draw_annotations: bool = True,
    ) -> None:
        super().__init__(pdf_path)
        require_local(pdf_path)
        self.pdfium_service = pdfium_service
        self.base_dpi = max(72, min(300, int(base_dpi)))
        self.draw_annotations = bool(draw_annotations)
        try:
            source_stat = self.source_path.stat()
            self._source_size = source_stat.st_size
            self._source_mtime_ns = source_stat.st_mtime_ns
        except OSError:
            self._source_size = None
            self._source_mtime_ns = None
        self._closed = Event()
        self._request_lock = RLock()
        self._active_requests: dict[tuple[str, str], set[Event]] = {}
        try:
            self.document_info: PdfDocumentInfo = pdfium_service.open_document(
                str(self.source_path),
                cancel_token=cancel_token,
            )
        except PdfBackendError as exc:
            raise ImageSourceError(
                exc.user_message,
                code=exc.code.value,
            ) from exc
        self.document_id = self.document_info.document_id
        self._ids = [f"pdf-page:{index}" for index in range(self.document_info.page_count)]
        self._index_by_id = {image_id: index for index, image_id in enumerate(self._ids)}

    def list_images(self) -> list[str]:
        return list(self._ids)

    def open_image(self, image_id: str) -> Image.Image:
        page_index = self._page_index(image_id)
        page = self.document_info.pages[page_index]
        logical_width = max(1, round(page.width_points * PDF_LOGICAL_DPI / 72))
        logical_height = max(1, round(page.height_points * PDF_LOGICAL_DPI / 72))
        if page.rotation_degrees in {90, 270}:
            logical_width, logical_height = logical_height, logical_width
        return self.open_image_for_render(
            image_id,
            PageRenderSpec(logical_width, logical_height),
        )

    def open_image_for_render(
        self,
        image_id: str,
        render_spec: PageRenderSpec | None = None,
        *,
        priority: int = int(PdfRenderPriority.VIEWER_CURRENT),
        generation: int = 0,
        purpose: str = "viewer",
        cancel_token: Event | None = None,
    ) -> Image.Image:
        page_index = self._page_index(image_id)
        page = self.document_info.pages[page_index]
        logical_size = self.logical_size(image_id)
        spec = render_spec or PageRenderSpec(*logical_size)
        target_width, target_height = (
            spec.target_pixel_size
            if render_spec is not None
            else bucket_render_size(*logical_size)
        )
        cancelled = cancel_token if cancel_token is not None else Event()
        request_group = (image_id, purpose)
        with self._request_lock:
            if self._closed.is_set():
                cancelled.set()
            self._active_requests.setdefault(request_group, set()).add(cancelled)
        try:
            result = self.pdfium_service.render_page(
                PdfRenderRequest(
                    self.document_id,
                    page_index,
                    target_width,
                    target_height,
                    rotation_degrees=spec.rotation_degrees,
                    priority=priority,
                    generation=generation,
                    purpose=purpose,
                    draw_annotations=self.draw_annotations,
                    source_size=self._source_size,
                    source_mtime_ns=self._source_mtime_ns,
                    device_pixel_ratio=spec.device_pixel_ratio,
                ),
                cancel_token=cancelled,
            )
            image = Image.frombytes(
                result.mode,
                (result.width, result.height),
                result.pixels,
            ).convert("RGBA")
            image.info["logical_size"] = logical_size
            image.info["pdf_render_size"] = (result.width, result.height)
            return image
        except PdfBackendError as exc:
            raise ImageSourceError(exc.user_message, code=exc.code.value) from exc
        finally:
            with self._request_lock:
                requests = self._active_requests.get(request_group)
                if requests is not None:
                    requests.discard(cancelled)
                    if not requests:
                        self._active_requests.pop(request_group, None)

    def logical_size(self, image_id: str) -> tuple[int, int]:
        page = self.document_info.pages[self._page_index(image_id)]
        width = max(1, round(page.width_points * PDF_LOGICAL_DPI / 72))
        height = max(1, round(page.height_points * PDF_LOGICAL_DPI / 72))
        if page.rotation_degrees in {90, 270}:
            width, height = height, width
        return width, height

    def display_path(self, image_id: str) -> str:
        page_index = self._page_index(image_id)
        label = self.document_info.pages[page_index].label
        page_name = label or f"Page {page_index + 1}"
        return f"{self.source_path} | {page_name}"

    def page_label(self, image_id: str) -> str | None:
        return self.document_info.pages[self._page_index(image_id)].label

    def file_size(self, image_id: str) -> int | None:
        return self._source_size

    def fork_for_thumbnail(self) -> ImageSource:
        return PdfImageSource(
            self.source_path,
            pdfium_service=self.pdfium_service,
            base_dpi=self.base_dpi,
            draw_annotations=self.draw_annotations,
        )

    def cancel_image_request(self, image_id: str) -> None:
        with self._request_lock:
            # ImageCache owns normal-page cancellation. Loupe jobs own their
            # individual tokens; a normal spec/queue update must not kill them.
            # Document close still cancels every purpose below.
            for (requested_id, purpose), requests in self._active_requests.items():
                if requested_id == image_id and purpose != "magnifier":
                    for cancelled in requests:
                        cancelled.set()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with self._request_lock:
            for requests in tuple(self._active_requests.values()):
                for cancelled in tuple(requests):
                    cancelled.set()
        self.pdfium_service.close_document(self.document_id)

    def _page_index(self, image_id: str) -> int:
        try:
            return self._index_by_id[image_id]
        except KeyError as exc:
            raise ImageSourceError(
                tr('PDFページが範囲外です。'),
                code=PdfErrorCode.PAGE_OUT_OF_RANGE.value,
            ) from exc
