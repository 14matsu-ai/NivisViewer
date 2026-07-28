from __future__ import annotations

import io
import os
import threading
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from natsort import natsorted
from PIL import Image, ImageOps
from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QImageReader

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


def _read_image_file_bytes(path: str | Path) -> bytes:
    """Read a local image without preventing rename/delete on Windows."""
    target = Path(path)
    if os.name != "nt":
        return target.read_bytes()

    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(target),
        0x80000000,  # GENERIC_READ
        0x00000001 | 0x00000002 | 0x00000004,  # SHARE_READ | WRITE | DELETE
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), f"画像を開けません: {target}")
    try:
        file_descriptor = msvcrt.open_osfhandle(
            int(handle),
            os.O_RDONLY | os.O_BINARY,
        )
    except Exception:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise
    with os.fdopen(file_descriptor, "rb") as file:
        return file.read()


def _read_webp_qimage(data: bytes) -> QImage | None:
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        return None
    reader = QImageReader(buffer, b"webp")
    reader.setAutoTransform(True)
    image = reader.read()
    buffer.close()
    return None if image.isNull() else image.copy()


@dataclass(frozen=True)
class FolderListingSnapshot:
    folder: Path
    image_ids: tuple[str, ...]
    selected_image: str
    fingerprints: tuple[tuple[str, int | None, int | None], ...] = ()
    generation: int = 0
    sort_identity: str = "name:ascending"


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

    def page_identity(self, image_id: str) -> str:
        return str(image_id)

    def index_for_identity(self, identity: str) -> int:
        target = str(identity)
        for index, image_id in enumerate(self.list_images()):
            if self.page_identity(image_id) == target:
                return index
        return -1

    def path_for_index(self, index: int) -> str | None:
        images = self.list_images()
        if 0 <= int(index) < len(images):
            return images[int(index)]
        return None

    def open_qimage(self, image_id: str) -> QImage | None:
        return None

    def close(self) -> None:
        pass


class FolderImageSource(ImageSource):
    load_sizes_lazily = True

    def __init__(
        self,
        folder_path: str | Path,
        *,
        recursive: bool = False,
        sort_descending: bool = False,
        image_snapshot: tuple[str, ...] | None = None,
        file_size_snapshot: tuple[tuple[str, int | None], ...] | None = None,
    ) -> None:
        super().__init__(folder_path)
        self.recursive = recursive
        self.sort_descending = sort_descending
        self._image_snapshot = (
            tuple(image_snapshot) if image_snapshot is not None else None
        )
        self._listed_images: tuple[str, ...] | None = self._image_snapshot
        self._size_cache: dict[str, tuple[int, int]] = {}
        self._file_size_cache = {
            self._path_identity(image_id): max(0, int(file_size))
            for image_id, file_size in (file_size_snapshot or ())
            if file_size is not None
        }
        if not self.source_path.is_dir():
            raise ImageSourceError(f"フォルダが見つかりません: {self.source_path}")

    def list_images(self) -> list[str]:
        if self._listed_images is not None:
            return list(self._listed_images)
        try:
            iterator = self.source_path.rglob("*") if self.recursive else self.source_path.iterdir()
            files = [
                path
                for path in iterator
                if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
            ]
        except OSError as exc:
            raise ImageSourceError(f"フォルダを読み込めません: {self.source_path}") from exc

        listed = tuple(
            str(path)
            for path in natsorted(
                files,
                key=lambda item: str(item.relative_to(self.source_path)),
                reverse=self.sort_descending,
            )
        )
        self._listed_images = listed
        return list(listed)

    @staticmethod
    def _path_identity(path: str | Path) -> str:
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path)))
        ).casefold()

    def page_identity(self, image_id: str) -> str:
        return self._path_identity(image_id)

    def index_for_identity(self, identity: str) -> int:
        target = self._path_identity(identity)
        for index, image_id in enumerate(self.list_images()):
            if self._path_identity(image_id) == target:
                return index
        return -1

    def index_for_path(self, path: str | Path) -> int:
        return self.index_for_identity(os.fspath(path))

    def open_image(self, image_id: str) -> Image.Image:
        try:
            # Detach the decoder from the filesystem before Pillow performs
            # potentially expensive pixel decoding.  On Windows this avoids
            # holding the source file open while a queued Viewer task runs.
            data = _read_image_file_bytes(image_id)
            self._file_size_cache[self._path_identity(image_id)] = len(data)
            with Image.open(io.BytesIO(data)) as image:
                image.seek(0)
                prepared = ImageOps.exif_transpose(image)
                result = prepared.copy()
                self._size_cache[image_id] = result.size
                return result
        except Exception as exc:
            raise ImageSourceError(f"画像を読み込めません: {image_id}") from exc

    def open_qimage(self, image_id: str) -> QImage | None:
        if Path(image_id).suffix.casefold() != ".webp":
            return None
        try:
            data = _read_image_file_bytes(image_id)
        except OSError:
            return None
        self._file_size_cache[self._path_identity(image_id)] = len(data)
        image = _read_webp_qimage(data)
        if image is None:
            return None
        self._size_cache[image_id] = (image.width(), image.height())
        return image

    def logical_size(self, image_id: str) -> tuple[int, int] | None:
        cached = self._size_cache.get(image_id)
        if cached is not None:
            return cached
        try:
            with Image.open(image_id) as image:
                width, height = image.size
                orientation = image.getexif().get(274, 1)
                if orientation in {5, 6, 7, 8}:
                    width, height = height, width
                logical = (width, height)
                self._size_cache[image_id] = logical
                return logical
        except Exception:
            return None

    def display_path(self, image_id: str) -> str:
        return str(Path(image_id))

    def file_size(self, image_id: str) -> int | None:
        return self._file_size_cache.get(self._path_identity(image_id))


class ZipImageSource(ImageSource):
    load_sizes_lazily = True

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
        self._listed_images: tuple[str, ...] | None = None
        self._display_names: dict[str, str] = {}

    def list_images(self) -> list[str]:
        if self._listed_images is not None:
            return list(self._listed_images)
        names = []
        for info in self._zip.infolist():
            if info.is_dir() or Path(info.filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            names.append(info.filename)
            self._display_names[info.filename] = self._decode_display_name(info)
        self._listed_images = tuple(
            natsorted(
                names,
                key=lambda name: self._display_names.get(name, name),
                reverse=self.sort_descending,
            )
        )
        return list(self._listed_images)

    def open_image(self, image_id: str) -> Image.Image:
        try:
            with self._lock:
                with self._zip.open(image_id, "r") as file:
                    data = file.read()
            stream: BinaryIO = io.BytesIO(data)
            with Image.open(stream) as image:
                image.seek(0)
                return ImageOps.exif_transpose(image).copy()
        except Exception as exc:
            raise ImageSourceError(f"書庫内の画像を読み込めません: {image_id}") from exc

    def open_qimage(self, image_id: str) -> QImage | None:
        if Path(image_id).suffix.casefold() != ".webp":
            return None
        try:
            with self._lock:
                with self._zip.open(image_id, "r") as file:
                    data = file.read()
            return _read_webp_qimage(data)
        except Exception:
            return None

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
                image.seek(0)
                return ImageOps.exif_transpose(image).copy()
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
    folder_snapshot: FolderListingSnapshot | None = None,
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
        snapshot_paths: tuple[str, ...] | None = None
        snapshot_file_sizes: tuple[tuple[str, int | None], ...] | None = None
        selected_from_snapshot: str | None = None
        if (
            folder_snapshot is not None
            and not recursive_folder
            and _path_identity_key(folder_snapshot.folder)
            == _path_identity_key(target.parent)
        ):
            target_key = _path_identity_key(target)
            selected_from_snapshot = next(
                (
                    image_id
                    for image_id in folder_snapshot.image_ids
                    if _path_identity_key(image_id) == target_key
                ),
                None,
            )
            if selected_from_snapshot is not None:
                snapshot_paths = folder_snapshot.image_ids
                snapshot_file_sizes = tuple(
                    (image_id, file_size)
                    for image_id, file_size, _modified_time_ns
                    in folder_snapshot.fingerprints
                )
        source = FolderImageSource(
            target.parent,
            recursive=recursive_folder,
            sort_descending=sort_descending,
            image_snapshot=snapshot_paths,
            file_size_snapshot=snapshot_file_sizes,
        )
        selected_image = selected_from_snapshot or str(target)
        return source, selected_image

    raise ImageSourceError(f"対応していない形式です: {target}")


def _path_identity_key(path: str | Path) -> str:
    return os.path.normcase(
        os.path.abspath(os.path.normpath(os.fspath(path)))
    ).casefold()
