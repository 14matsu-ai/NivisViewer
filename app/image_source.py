from __future__ import annotations

import io
import threading
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from natsort import natsorted
from PIL import Image, ImageOps

from .archive_backend import (
    ArchiveBackendError,
    ArchiveErrorCode,
    MAX_IMAGE_ENTRY_BYTES,
    select_image_entries,
)
from .supported_formats import (
    ARCHIVE_EXTENSIONS,
    BOOK_FILE_EXTENSIONS,
    EXTERNAL_ARCHIVE_EXTENSIONS,
    IMAGE_EXTENSIONS,
    PDF_EXTENSIONS,
    ZIP_ARCHIVE_EXTENSIONS,
)

SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS


class ImageSourceError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.code = code
        super().__init__(message)


class ImageSource(ABC):
    load_sizes_lazily = False

    def __init__(self, source_path: str | Path) -> None:
        self.source_path = Path(source_path)

    @abstractmethod
    def list_images(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def open_image(self, image_id: str) -> Image.Image:
        raise NotImplementedError

    @abstractmethod
    def display_path(self, image_id: str) -> str:
        raise NotImplementedError

    def file_size(self, image_id: str) -> int | None:
        return None

    def logical_size(self, image_id: str) -> tuple[int, int] | None:
        return None

    def close(self) -> None:
        pass


class FolderImageSource(ImageSource):
    def __init__(self, folder_path: str | Path, *, recursive: bool = False, sort_descending: bool = False) -> None:
        super().__init__(folder_path)
        self.recursive = recursive
        self.sort_descending = sort_descending
        if not self.source_path.is_dir():
            raise ImageSourceError(f"フォルダが見つかりません: {self.source_path}")

    def list_images(self) -> list[str]:
        try:
            iterator = self.source_path.rglob("*") if self.recursive else self.source_path.iterdir()
            files = [
                path
                for path in iterator
                if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
            ]
        except OSError as exc:
            raise ImageSourceError(f"フォルダを読み込めません: {self.source_path}") from exc

        return [
            str(path)
            for path in natsorted(
                files,
                key=lambda item: str(item.relative_to(self.source_path)),
                reverse=self.sort_descending,
            )
        ]

    def open_image(self, image_id: str) -> Image.Image:
        try:
            with Image.open(image_id) as image:
                return ImageOps.exif_transpose(image).convert("RGBA")
        except Exception as exc:
            raise ImageSourceError(f"画像を読み込めません: {image_id}") from exc

    def display_path(self, image_id: str) -> str:
        return str(Path(image_id))

    def file_size(self, image_id: str) -> int | None:
        try:
            return Path(image_id).stat().st_size
        except OSError:
            return None


class ZipImageSource(ImageSource):
    def __init__(self, archive_path: str | Path, *, sort_descending: bool = False) -> None:
        super().__init__(archive_path)
        self.sort_descending = sort_descending
        if not self.source_path.is_file():
            raise ImageSourceError(f"書庫が見つかりません: {self.source_path}")

        try:
            self._zip = zipfile.ZipFile(self.source_path, "r")
        except zipfile.BadZipFile as exc:
            raise ImageSourceError(f"ZIP/CBZとして開けません: {self.source_path}") from exc
        except OSError as exc:
            raise ImageSourceError(f"書庫を開けません: {self.source_path}") from exc
        self._lock = threading.RLock()

    def list_images(self) -> list[str]:
        self._display_names: dict[str, str] = {}
        names = []
        for info in self._zip.infolist():
            if info.is_dir() or Path(info.filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            names.append(info.filename)
            self._display_names[info.filename] = self._decode_display_name(info)
        return natsorted(
            names,
            key=lambda name: self._display_names.get(name, name),
            reverse=self.sort_descending,
        )

    def open_image(self, image_id: str) -> Image.Image:
        try:
            with self._lock:
                with self._zip.open(image_id, "r") as file:
                    data = file.read()
            stream: BinaryIO = io.BytesIO(data)
            with Image.open(stream) as image:
                return ImageOps.exif_transpose(image).convert("RGBA")
        except Exception as exc:
            raise ImageSourceError(f"書庫内の画像を読み込めません: {image_id}") from exc

    def display_path(self, image_id: str) -> str:
        display_name = getattr(self, "_display_names", {}).get(image_id, image_id)
        return f"{self.source_path}!/{display_name}"

    def file_size(self, image_id: str) -> int | None:
        try:
            return self._zip.getinfo(image_id).file_size
        except KeyError:
            return None

    def close(self) -> None:
        self._zip.close()

    @staticmethod
    def _decode_display_name(info: zipfile.ZipInfo) -> str:
        if info.flag_bits & 0x800:
            return info.filename
        try:
            return info.filename.encode("cp437").decode("cp932")
        except UnicodeError:
            return info.filename


class SevenZipImageSource(ImageSource):
    load_sizes_lazily = True

    def __init__(
        self,
        archive_path: str | Path,
        *,
        backend,
        sort_descending: bool = False,
        cancel_token=None,
    ) -> None:
        super().__init__(archive_path)
        self.backend = backend
        self.sort_descending = sort_descending
        self._closed = threading.Event()
        self._active_lock = threading.RLock()
        self._active_requests: dict[str, set[threading.Event]] = {}
        try:
            listing = backend.list_entries(
                str(self.source_path),
                cancel_token=cancel_token,
            )
            if listing.encrypted:
                raise ArchiveBackendError(ArchiveErrorCode.PASSWORD_REQUIRED)
            entries = select_image_entries(
                listing,
                SUPPORTED_EXTENSIONS,
                descending=sort_descending,
            )
        except ArchiveBackendError as exc:
            raise ImageSourceError(
                exc.user_message,
                code=exc.code.value,
            ) from exc
        if not entries:
            raise ImageSourceError(
                "表示可能な画像がありません。",
                code="no_images",
            )
        self.listing = listing
        self.solid = listing.solid
        self._entries = entries
        self._entry_by_id = {entry.path: entry for entry in entries}

    def list_images(self) -> list[str]:
        return [entry.path for entry in self._entries]

    def open_image(self, image_id: str) -> Image.Image:
        entry = self._entry_by_id.get(image_id)
        if entry is None:
            raise ImageSourceError(
                "書庫内の画像が見つかりません。",
                code=ArchiveErrorCode.ENTRY_NOT_FOUND.value,
            )
        if entry.size is not None and entry.size > MAX_IMAGE_ENTRY_BYTES:
            raise ImageSourceError(
                "書庫内の画像が大きすぎます。",
                code=ArchiveErrorCode.ENTRY_TOO_LARGE.value,
            )
        cancelled = threading.Event()
        with self._active_lock:
            if self._closed.is_set():
                cancelled.set()
            self._active_requests.setdefault(image_id, set()).add(cancelled)
        try:
            data = self.backend.read_entry(
                str(self.source_path),
                entry.extraction_path,
                cancel_token=cancelled,
                maximum_bytes=(
                    min(MAX_IMAGE_ENTRY_BYTES, entry.size)
                    if entry.size is not None
                    else MAX_IMAGE_ENTRY_BYTES
                ),
            )
            with Image.open(io.BytesIO(data)) as image:
                return ImageOps.exif_transpose(image).convert("RGBA")
        except ArchiveBackendError as exc:
            raise ImageSourceError(
                exc.user_message,
                code=exc.code.value,
            ) from exc
        except ImageSourceError:
            raise
        except Exception as exc:
            raise ImageSourceError(
                f"書庫内の画像を読み込めません: {image_id}",
                code="decode_failed",
            ) from exc
        finally:
            with self._active_lock:
                requests = self._active_requests.get(image_id)
                if requests is not None:
                    requests.discard(cancelled)
                    if not requests:
                        self._active_requests.pop(image_id, None)

    def cancel_image_request(self, image_id: str) -> None:
        with self._active_lock:
            for cancelled in tuple(self._active_requests.get(image_id, ())):
                cancelled.set()

    def display_path(self, image_id: str) -> str:
        return f"{self.source_path}!/{image_id}"

    def file_size(self, image_id: str) -> int | None:
        entry = self._entry_by_id.get(image_id)
        return entry.size if entry is not None else None

    def close(self) -> None:
        self._closed.set()
        with self._active_lock:
            for requests in tuple(self._active_requests.values()):
                for cancelled in tuple(requests):
                    cancelled.set()


def create_image_source(
    path: str | Path,
    *,
    recursive_folder: bool = False,
    sort_descending: bool = False,
    archive_backend_registry=None,
    pdfium_service=None,
    pdf_render_base_dpi: int = 96,
    pdf_render_annotations: bool = True,
    cancel_token=None,
) -> tuple[ImageSource, str | None]:
    target = Path(path)
    selected_image: str | None = None

    if target.is_dir():
        return FolderImageSource(target, recursive=recursive_folder, sort_descending=sort_descending), None

    suffix = target.suffix.lower()
    if suffix in PDF_EXTENSIONS:
        if pdfium_service is None:
            raise ImageSourceError(
                "PDFレンダリング機能を利用できません。",
                code="backend_unavailable",
            )
        from .pdf_image_source import PdfImageSource

        return (
            PdfImageSource(
                target,
                pdfium_service=pdfium_service,
                cancel_token=cancel_token,
                base_dpi=pdf_render_base_dpi,
                draw_annotations=pdf_render_annotations,
            ),
            None,
        )
    if suffix in ZIP_ARCHIVE_EXTENSIONS:
        return ZipImageSource(target, sort_descending=sort_descending), None
    if suffix in EXTERNAL_ARCHIVE_EXTENSIONS:
        backend = (
            archive_backend_registry.backend_for_path(target)
            if archive_backend_registry is not None
            else None
        )
        if backend is None:
            raise ImageSourceError(
                "RAR／7zの閲覧には7-Zipが必要です。"
                "設定から実行ファイルを指定してください。",
                code=ArchiveErrorCode.BACKEND_NOT_FOUND.value,
            )
        return (
            SevenZipImageSource(
                target,
                backend=backend,
                sort_descending=sort_descending,
                cancel_token=cancel_token,
            ),
            None,
        )

    if target.is_file() and suffix in SUPPORTED_EXTENSIONS:
        source = FolderImageSource(target.parent, recursive=recursive_folder, sort_descending=sort_descending)
        selected_image = str(target)
        return source, selected_image

    raise ImageSourceError(f"対応していない形式です: {target}")
