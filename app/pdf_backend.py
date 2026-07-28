from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
import math
from typing import Protocol


PDF_EXTENSION = ".pdf"
MAX_PDF_PAGES = 50_000
MAX_PDF_RENDER_DIMENSION = 32_768
MAX_PDF_RENDER_PIXELS = 64_000_000
PDF_RENDER_BUCKET_SIZE = 64
PDF_LOGICAL_DPI = 96
PDF_RENDER_IMPLEMENTATION_VERSION = 1


class PdfErrorCode(StrEnum):
    BACKEND_UNAVAILABLE = "backend_unavailable"
    FILE_NOT_FOUND = "file_not_found"
    PASSWORD_REQUIRED = "password_required"
    INVALID_PASSWORD = "invalid_password"
    CORRUPT_PDF = "corrupt_pdf"
    UNSUPPORTED_SECURITY = "unsupported_security"
    ZERO_PAGES = "zero_pages"
    TOO_MANY_PAGES = "too_many_pages"
    INVALID_PAGE_SIZE = "invalid_page_size"
    PAGE_OUT_OF_RANGE = "page_out_of_range"
    RENDER_TOO_LARGE = "render_too_large"
    RENDER_FAILED = "render_failed"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


_USER_MESSAGES = {
    PdfErrorCode.BACKEND_UNAVAILABLE: (
        "PDF表示機能を利用できません。"
        "pypdfium2がインストールされているか確認してください。"
    ),
    PdfErrorCode.FILE_NOT_FOUND: "PDFが見つかりません。",
    PdfErrorCode.PASSWORD_REQUIRED: (
        "このPDFはパスワードで保護されています。"
        "現在のバージョンでは開けません。"
    ),
    PdfErrorCode.INVALID_PASSWORD: "PDFのパスワードが正しくありません。",
    PdfErrorCode.CORRUPT_PDF: "PDFを開けません。壊れている可能性があります。",
    PdfErrorCode.UNSUPPORTED_SECURITY: "このPDFのセキュリティ方式には対応していません。",
    PdfErrorCode.ZERO_PAGES: "PDFに表示可能なページがありません。",
    PdfErrorCode.TOO_MANY_PAGES: "PDFのページ数が上限を超えています。",
    PdfErrorCode.INVALID_PAGE_SIZE: "PDFページの寸法が不正です。",
    PdfErrorCode.PAGE_OUT_OF_RANGE: "PDFページが範囲外です。",
    PdfErrorCode.RENDER_TOO_LARGE: "PDFのレンダリング解像度が上限を超えています。",
    PdfErrorCode.RENDER_FAILED: "PDFページを表示できません。",
    PdfErrorCode.CANCELLED: "PDF処理をキャンセルしました。",
    PdfErrorCode.INTERNAL_ERROR: "PDF処理中に内部エラーが発生しました。",
}


class PdfBackendError(RuntimeError):
    def __init__(
        self,
        code: PdfErrorCode | str,
        *,
        user_message: str | None = None,
        debug_message: str | None = None,
    ) -> None:
        self.code = PdfErrorCode(code)
        self.user_message = user_message or _USER_MESSAGES[self.code]
        self.debug_message = debug_message
        super().__init__(self.user_message)


class PdfRenderPriority(IntEnum):
    VIEWER_CURRENT = 0
    VIEWER_SPREAD_PARTNER = 10
    VIEWER_NEXT = 20
    VIEWER_PREVIOUS = 30
    DOCUMENT_OPEN = 40
    THUMBNAIL_VISIBLE = 50
    THUMBNAIL_SELECTED = 60
    THUMBNAIL_PREFETCH = 80
    DOCUMENT_CLOSE = 90


@dataclass(frozen=True)
class PdfPageInfo:
    page_index: int
    width_points: float
    height_points: float
    rotation_degrees: int
    label: str | None = None

    @property
    def logical_size(self) -> tuple[int, int]:
        width = self.width_points * PDF_LOGICAL_DPI / 72.0
        height = self.height_points * PDF_LOGICAL_DPI / 72.0
        if self.rotation_degrees in {90, 270}:
            width, height = height, width
        return max(1, round(width)), max(1, round(height))


@dataclass(frozen=True)
class PdfDocumentInfo:
    document_id: str
    path: str
    page_count: int
    pages: tuple[PdfPageInfo, ...]
    encrypted: bool
    title: str | None = None
    author: str | None = None


@dataclass(frozen=True)
class PdfRenderRequest:
    document_id: str
    page_index: int
    target_width_px: int
    target_height_px: int
    rotation_degrees: int = 0
    priority: int = int(PdfRenderPriority.VIEWER_CURRENT)
    generation: int = 0
    purpose: str = "viewer"
    draw_annotations: bool = True
    source_size: int | None = None
    source_mtime_ns: int | None = None
    device_pixel_ratio: float = 1.0

    @property
    def cache_key(self) -> tuple[object, ...]:
        return (
            self.document_id,
            self.page_index,
            self.generation,
            self.target_width_px,
            self.target_height_px,
            self.rotation_degrees % 360,
            self.purpose,
            self.draw_annotations,
            self.source_size,
            self.source_mtime_ns,
            round(max(0.1, float(self.device_pixel_ratio)) * 4) / 4,
            PDF_RENDER_IMPLEMENTATION_VERSION,
        )


@dataclass(frozen=True)
class PdfRenderResult:
    document_id: str
    page_index: int
    width: int
    height: int
    mode: str
    pixels: bytes
    generation: int
    purpose: str


@dataclass(frozen=True)
class PageRenderSpec:
    logical_width: int
    logical_height: int
    device_pixel_ratio: float = 1.0
    rotation_degrees: int = 0
    mode: str = "fit_window"
    size_bucket: tuple[int, int] | None = None

    @property
    def target_pixel_size(self) -> tuple[int, int]:
        if self.size_bucket is not None:
            return self.size_bucket
        return bucket_render_size(
            round(self.logical_width * self.device_pixel_ratio),
            round(self.logical_height * self.device_pixel_ratio),
        )


class PdfBackend(Protocol):
    def open_document(
        self,
        path: str,
        *,
        password: str | None = None,
        cancel_token=None,
    ) -> PdfDocumentInfo:
        ...

    def render_page(
        self,
        request: PdfRenderRequest,
        *,
        cancel_token=None,
    ) -> PdfRenderResult:
        ...

    def close_document(self, document_id: str) -> None:
        ...

    def close_all(self) -> None:
        ...


def bucket_render_size(
    width: int,
    height: int,
    *,
    bucket_size: int = PDF_RENDER_BUCKET_SIZE,
) -> tuple[int, int]:
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise PdfBackendError(PdfErrorCode.INVALID_PAGE_SIZE)
    bucket = max(1, int(bucket_size))
    width = ((width + bucket - 1) // bucket) * bucket
    height = ((height + bucket - 1) // bucket) * bucket
    return clamp_render_size(width, height)


def clamp_render_size(width: int, height: int) -> tuple[int, int]:
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise PdfBackendError(PdfErrorCode.INVALID_PAGE_SIZE)
    scale = min(
        1.0,
        MAX_PDF_RENDER_DIMENSION / width,
        MAX_PDF_RENDER_DIMENSION / height,
        math.sqrt(MAX_PDF_RENDER_PIXELS / (width * height)),
    )
    return max(1, math.floor(width * scale)), max(1, math.floor(height * scale))


def validate_page_size(width: float, height: float) -> tuple[float, float]:
    values = float(width), float(height)
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise PdfBackendError(PdfErrorCode.INVALID_PAGE_SIZE)
    return values


def is_cancelled(cancel_token) -> bool:
    if cancel_token is None:
        return False
    checker = getattr(cancel_token, "is_set", None)
    if callable(checker):
        return bool(checker())
    checker = getattr(cancel_token, "is_cancelled", None)
    if callable(checker):
        return bool(checker())
    return bool(cancel_token) if isinstance(cancel_token, bool) else False
