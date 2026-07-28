from __future__ import annotations

from dataclasses import dataclass
import importlib
import logging
import os
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .pdf_backend import (
    MAX_PDF_PAGES,
    PdfBackendError,
    PdfDocumentInfo,
    PdfErrorCode,
    PdfPageInfo,
    PdfRenderRequest,
    PdfRenderResult,
    clamp_render_size,
    is_cancelled,
    validate_page_size,
)


_PDFIUM_LOG = logging.getLogger("nivisviewer.pdfium")


@dataclass
class _PdfiumDocumentState:
    info: PdfDocumentInfo
    document: object


class PdfiumBackend:
    """pypdfium2 v5 adapter. Call only from PdfiumService's worker."""

    def __init__(self, *, pdfium_module=None, maximum_pages: int = MAX_PDF_PAGES) -> None:
        self._pdfium_module = pdfium_module
        self.maximum_pages = max(1, int(maximum_pages))
        self._documents: dict[str, _PdfiumDocumentState] = {}
        self._lock = RLock()

    def is_available(self) -> bool:
        try:
            self._module()
            return True
        except PdfBackendError:
            return False

    def open_document(
        self,
        path: str,
        *,
        password: str | None = None,
        cancel_token=None,
    ) -> PdfDocumentInfo:
        if is_cancelled(cancel_token):
            raise PdfBackendError(PdfErrorCode.CANCELLED)
        target = Path(path)
        try:
            if not target.is_file():
                raise PdfBackendError(PdfErrorCode.FILE_NOT_FOUND)
        except OSError as exc:
            raise PdfBackendError(
                PdfErrorCode.FILE_NOT_FOUND,
                debug_message=str(exc),
            ) from exc
        absolute = str(Path(os.path.abspath(os.path.normpath(os.fspath(target)))))
        module = self._module()
        document = None
        try:
            document = module.PdfDocument(absolute, password=password)
            page_count = len(document)
            if page_count <= 0:
                raise PdfBackendError(PdfErrorCode.ZERO_PAGES)
            if page_count > self.maximum_pages:
                raise PdfBackendError(PdfErrorCode.TOO_MANY_PAGES)
            pages: list[PdfPageInfo] = []
            for index in range(page_count):
                if is_cancelled(cancel_token):
                    raise PdfBackendError(PdfErrorCode.CANCELLED)
                page = None
                try:
                    page = document[index]
                    width, height = validate_page_size(*page.get_size())
                    rotation = int(page.get_rotation() or 0) % 360
                    # PDFium reports the effective size after intrinsic page
                    # rotation. Store canonical point dimensions so rotation
                    # is applied exactly once by PdfPageInfo/rendering.
                    if rotation in {90, 270}:
                        width, height = height, width
                finally:
                    if page is not None:
                        page.close()
                try:
                    label = document.get_page_label(index) or None
                except Exception:
                    label = None
                pages.append(
                    PdfPageInfo(index, width, height, rotation, label)
                )
            metadata = self._metadata(document)
            document_id = uuid4().hex
            info = PdfDocumentInfo(
                document_id=document_id,
                path=absolute,
                page_count=page_count,
                pages=tuple(pages),
                encrypted=bool(password),
                title=metadata.get("Title") or metadata.get("title"),
                author=metadata.get("Author") or metadata.get("author"),
            )
            with self._lock:
                self._documents[document_id] = _PdfiumDocumentState(info, document)
            document = None
            return info
        except PdfBackendError:
            raise
        except FileNotFoundError as exc:
            raise PdfBackendError(PdfErrorCode.FILE_NOT_FOUND) from exc
        except Exception as exc:
            raise self._classify_open_error(exc, password_supplied=password is not None) from exc
        finally:
            if document is not None:
                document.close()

    def render_page(
        self,
        request: PdfRenderRequest,
        *,
        cancel_token=None,
    ) -> PdfRenderResult:
        if is_cancelled(cancel_token):
            raise PdfBackendError(PdfErrorCode.CANCELLED)
        with self._lock:
            state = self._documents.get(request.document_id)
        if state is None:
            raise PdfBackendError(PdfErrorCode.INTERNAL_ERROR)
        if not 0 <= request.page_index < state.info.page_count:
            raise PdfBackendError(PdfErrorCode.PAGE_OUT_OF_RANGE)
        target_width, target_height = clamp_render_size(
            request.target_width_px,
            request.target_height_px,
        )
        page_info = state.info.pages[request.page_index]
        width_points, height_points = (
            page_info.width_points,
            page_info.height_points,
        )
        combined_rotation = (
            page_info.rotation_degrees + request.rotation_degrees
        ) % 360
        if combined_rotation in {90, 270}:
            width_points, height_points = height_points, width_points
        scale = min(target_width / width_points, target_height / height_points)
        if scale <= 0:
            raise PdfBackendError(PdfErrorCode.INVALID_PAGE_SIZE)

        page = None
        bitmap = None
        image = None
        copied = None
        try:
            page = state.document[request.page_index]
            bitmap = page.render(
                scale=scale,
                rotation=request.rotation_degrees % 360,
                fill_color=(255, 255, 255, 255),
                draw_annots=bool(request.draw_annotations),
                limit_image_cache=True,
                rev_byteorder=True,
                maybe_alpha=True,
            )
            image = bitmap.to_pil()
            copied = image.convert("RGBA").copy()
            if is_cancelled(cancel_token):
                raise PdfBackendError(PdfErrorCode.CANCELLED)
            pixels = copied.tobytes("raw", "RGBA")
            return PdfRenderResult(
                request.document_id,
                request.page_index,
                copied.width,
                copied.height,
                "RGBA",
                pixels,
                request.generation,
                request.purpose,
            )
        except PdfBackendError:
            raise
        except Exception as exc:
            raise PdfBackendError(
                PdfErrorCode.RENDER_FAILED,
                debug_message=str(exc),
            ) from exc
        finally:
            if copied is not None:
                copied.close()
            if image is not None:
                image.close()
            if bitmap is not None:
                bitmap.close()
            if page is not None:
                page.close()

    def close_document(self, document_id: str) -> None:
        with self._lock:
            state = self._documents.pop(document_id, None)
        if state is not None:
            state.document.close()

    def close_all(self) -> None:
        with self._lock:
            states = tuple(self._documents.items())
            self._documents.clear()
        failures = 0
        for document_id, state in states:
            try:
                state.document.close()
            except Exception:
                failures += 1
                _PDFIUM_LOG.exception(
                    "PDF document close failed document_id=%s",
                    document_id,
                )
        if failures:
            raise PdfBackendError(
                PdfErrorCode.INTERNAL_ERROR,
                debug_message=(
                    f"{failures} PDF document(s) could not be closed."
                ),
            )

    def _module(self):
        if self._pdfium_module is not None:
            return self._pdfium_module
        try:
            self._pdfium_module = importlib.import_module("pypdfium2")
        except (ImportError, OSError) as exc:
            raise PdfBackendError(
                PdfErrorCode.BACKEND_UNAVAILABLE,
                debug_message=str(exc),
            ) from exc
        return self._pdfium_module

    @staticmethod
    def _metadata(document: Any) -> dict[str, str]:
        try:
            value = document.get_metadata_dict()
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _classify_open_error(
        exc: Exception,
        *,
        password_supplied: bool,
    ) -> PdfBackendError:
        message = str(exc).casefold()
        error_code = getattr(exc, "err_code", None)
        if error_code == 4 or "password" in message:
            code = (
                PdfErrorCode.INVALID_PASSWORD
                if password_supplied
                else PdfErrorCode.PASSWORD_REQUIRED
            )
        elif "security" in message or "unsupported" in message:
            code = PdfErrorCode.UNSUPPORTED_SECURITY
        else:
            code = PdfErrorCode.CORRUPT_PDF
        return PdfBackendError(code, debug_message=str(exc))
