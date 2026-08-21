from __future__ import annotations

import io
import os
import threading
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from math import ceil
from pathlib import Path

from natsort import natsorted
from PIL import Image, ImageOps
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader

from .archive_backend import (
    ArchiveEntry,
    ArchiveBackendError,
    ArchiveErrorCode,
    ArchiveListing,
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
JpegMaximumSize = tuple[int | None, int | None]


def jpeg_decode_target_size(
    logical_size: tuple[int, int],
    maximum_size: JpegMaximumSize,
) -> tuple[int, int]:
    """Return an aspect-preserving JPEG target for optional axis bounds.

    ``None`` leaves that axis unconstrained.  With no constrained axis the
    logical source size is returned, which keeps callers that deliberately
    request a full decode safe without inventing an arbitrary limit.
    """

    logical_width = max(1, int(logical_size[0]))
    logical_height = max(1, int(logical_size[1]))
    maximum_width = (
        None
        if maximum_size[0] is None
        else max(1, int(maximum_size[0]))
    )
    maximum_height = (
        None
        if maximum_size[1] is None
        else max(1, int(maximum_size[1]))
    )
    scale = 1.0
    if maximum_width is not None:
        scale = min(scale, maximum_width / logical_width)
    if maximum_height is not None:
        scale = min(scale, maximum_height / logical_height)

    target_width = max(1, round(logical_width * scale))
    target_height = max(1, round(logical_height * scale))
    if maximum_width is not None:
        target_width = min(target_width, maximum_width)
    if maximum_height is not None:
        target_height = min(target_height, maximum_height)
    return target_width, target_height


def jpeg_native_reduction_size(
    logical_size: tuple[int, int],
    maximum_size: JpegMaximumSize,
) -> tuple[int, int]:
    """Estimate Pillow/libjpeg's retained 1/2, 1/4, or 1/8 tier.

    ``Image.draft`` chooses a native decoder reduction whose result remains
    large enough for the requested target.  Folder Viewer keeps that tier as
    its reusable source instead of performing a second exact resize, so cache
    admission must budget the native dimensions rather than the smaller final
    target.
    """

    logical_width = max(1, int(logical_size[0]))
    logical_height = max(1, int(logical_size[1]))
    target_width, target_height = jpeg_decode_target_size(
        (logical_width, logical_height),
        maximum_size,
    )
    for divisor in (8, 4, 2, 1):
        reduced = (
            max(1, ceil(logical_width / divisor)),
            max(1, ceil(logical_height / divisor)),
        )
        if reduced[0] >= target_width and reduced[1] >= target_height:
            return reduced
    return logical_width, logical_height


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


def _read_jpeg_qimage_at_most(
    data: bytes,
    maximum_size: JpegMaximumSize,
) -> tuple[QImage, tuple[int, int]] | None:
    """Decode a JPEG near its display size using the decoder's scale path."""
    try:
        with Image.open(io.BytesIO(data)) as header:
            raw_width, raw_height = header.size
            orientation = int(header.getexif().get(274, 1))
    except Exception:
        return None
    if raw_width <= 0 or raw_height <= 0:
        return None

    swaps_axes = orientation in {5, 6, 7, 8}
    logical_size = (
        (raw_height, raw_width)
        if swaps_axes
        else (raw_width, raw_height)
    )
    logical_target = jpeg_decode_target_size(logical_size, maximum_size)
    raw_target = (
        (logical_target[1], logical_target[0])
        if swaps_axes
        else logical_target
    )

    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        return None
    reader = QImageReader(buffer, b"jpeg")
    reader.setAutoTransform(True)
    reader.setScaledSize(QSize(*raw_target))
    image = reader.read()
    buffer.close()
    if image.isNull():
        return None
    return image, logical_size


def _read_folder_jpeg_qimage_at_most(
    data: bytes,
    maximum_size: JpegMaximumSize,
) -> tuple[QImage, tuple[int, int]] | None:
    """Decode a folder JPEG without entering Qt's GUI-shared plugin loader.

    Folder Viewer work can overlap Browser QIcon painting.  On Windows and the
    offscreen platform, a worker-side ``QImageReader.read()`` can deadlock with
    that GUI-side lazy icon/plugin work.  Pillow uses its libjpeg decoder
    entirely inside the worker; ``draft`` requests native JPEG reduction and
    ``thumbnail`` finishes the exact bound before the detached QImage copy is
    created.
    """

    try:
        with Image.open(io.BytesIO(data)) as image:
            raw_width, raw_height = image.size
            orientation = int(image.getexif().get(274, 1))
            if raw_width <= 0 or raw_height <= 0:
                return None
            swaps_axes = orientation in {5, 6, 7, 8}
            logical_size = (
                (raw_height, raw_width)
                if swaps_axes
                else (raw_width, raw_height)
            )
            logical_target = jpeg_decode_target_size(
                logical_size,
                maximum_size,
            )
            raw_target = (
                (logical_target[1], logical_target[0])
                if swaps_axes
                else logical_target
            )
            image.draft("RGB", raw_target)
            image.load()
            prepared = ImageOps.exif_transpose(image)
            try:
                prepared.thumbnail(
                    logical_target,
                    Image.Resampling.LANCZOS,
                    reducing_gap=2.0,
                )
                from .thumbnail_render import pil_to_qimage

                qimage = pil_to_qimage(prepared)
            finally:
                if prepared is not image:
                    prepared.close()
    except Exception:
        return None
    if qimage.isNull():
        return None
    return qimage, logical_size


def _read_folder_compatible_jpeg_at_most(
    data: bytes,
    maximum_size: JpegMaximumSize,
) -> tuple[QImage, tuple[int, int]] | None:
    """Decode a folder JPEG to its native libjpeg preview tier.

    Unlike ``_read_folder_jpeg_qimage_at_most``, this path intentionally does
    not resize the draft result to the exact display bound.  Pillow/libjpeg
    therefore performs only its native 1/2, 1/4, or 1/8 reduction and the
    Viewer can retain that preview as a reusable decoded source.
    """

    try:
        with Image.open(io.BytesIO(data)) as image:
            raw_width, raw_height = image.size
            orientation = int(image.getexif().get(274, 1))
            if raw_width <= 0 or raw_height <= 0:
                return None
            swaps_axes = orientation in {5, 6, 7, 8}
            logical_size = (
                (raw_height, raw_width)
                if swaps_axes
                else (raw_width, raw_height)
            )
            logical_target = jpeg_decode_target_size(
                logical_size,
                maximum_size,
            )
            raw_target = (
                (logical_target[1], logical_target[0])
                if swaps_axes
                else logical_target
            )
            image.draft("RGB", raw_target)
            image.load()
            prepared = ImageOps.exif_transpose(image)
            try:
                prepared.load()
                from .thumbnail_render import pil_to_qimage

                qimage = pil_to_qimage(prepared)
            finally:
                if prepared is not image:
                    prepared.close()
    except Exception:
        return None
    if qimage.isNull():
        return None
    return qimage, logical_size


def _read_jpeg_qbytearray_at_most(
    data: QByteArray,
    maximum_size: JpegMaximumSize,
) -> tuple[QImage, tuple[int, int]] | None:
    """Decode one seekable Qt byte buffer without Python QIODevice callbacks."""
    # Reject a plainly invalid entry before entering Qt's JPEG plugin.  Besides
    # avoiding native work, this prevents a first-ever worker-side malformed
    # JPEG probe from hanging plugin error initialization on Windows/offscreen.
    if data.size() < 3 or not data.startsWith(QByteArray(b"\xff\xd8\xff")):
        return None
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        return None
    try:
        reader = QImageReader(buffer, b"jpeg")
        raw_size = reader.size()
        transformation = reader.transformation()
        raw_width = raw_size.width()
        raw_height = raw_size.height()
        if raw_width <= 0 or raw_height <= 0:
            return None

        swaps_axes = bool(
            transformation
            & QImageIOHandler.Transformation.TransformationRotate90
        )
        logical_size = (
            (raw_height, raw_width)
            if swaps_axes
            else (raw_width, raw_height)
        )
        # Retain the smallest decoder-native tier that is still large enough
        # for the final physical frame.  The Viewer then owns one deliberate
        # exact resample with the configured algorithm; an arbitrary decoder
        # resize here would create a hidden first quality pass.
        logical_target = jpeg_native_reduction_size(logical_size, maximum_size)
        raw_target = (
            (logical_target[1], logical_target[0])
            if swaps_axes
            else logical_target
        )
        reader.setAutoTransform(True)
        reader.setScaledSize(QSize(*raw_target))
        image = reader.read()
        if image.isNull():
            return None
        return image, logical_size
    finally:
        buffer.close()


@dataclass(frozen=True)
class FolderListingSnapshot:
    folder: Path
    image_ids: tuple[str, ...]
    selected_image: str
    fingerprints: tuple[tuple[str, int | None, int | None], ...] = ()
    generation: int = 0
    sort_identity: str = "name:ascending"


@dataclass(frozen=True)
class SevenZipListingSnapshot:
    listing: ArchiveListing
    image_entries: tuple[ArchiveEntry, ...]


@dataclass(frozen=True)
class StreamedJpegDecode:
    qimage: QImage
    original_size: tuple[int, int]
    bytes_read: int
    read_calls: int
    backend: str = "python-sequential-qiodevice"
    full_payload_materializations: int = 0


class _ZipEntrySequentialDevice(QIODevice):
    """Expose one ZipExtFile to Qt without materializing the full payload."""

    def __init__(
        self,
        entry,
        *,
        expected_size: int,
        cancelled: threading.Event,
        maximum_bytes: int,
        read_chunk_bytes: int,
    ) -> None:
        super().__init__()
        self._entry = entry
        self._expected_size = max(0, int(expected_size))
        self._cancelled = cancelled
        self._maximum_bytes = max(0, int(maximum_bytes))
        self._read_chunk_bytes = max(1, int(read_chunk_bytes))
        self.bytes_read = 0
        self.read_calls = 0
        self.too_large = False
        self.read_error: Exception | None = None

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        remaining = max(0, self._expected_size - self.bytes_read)
        return remaining + super().bytesAvailable()

    def readData(self, maximum_length: int) -> bytes:
        if (
            maximum_length <= 0
            or self._cancelled.is_set()
            or self.too_large
            or self.read_error is not None
        ):
            return b""
        allowed = self._maximum_bytes - self.bytes_read
        if allowed < 0:
            self.too_large = True
            return b""
        request_size = min(
            int(maximum_length),
            self._read_chunk_bytes,
            allowed + 1,
        )
        if request_size <= 0:
            return b""
        try:
            chunk = self._entry.read(request_size)
            self.read_calls += 1
        except Exception as exc:
            self.read_error = exc
            return b""
        self.bytes_read += len(chunk)
        if self.bytes_read > self._maximum_bytes:
            self.too_large = True
            return b""
        # A request can be cancelled while ZipExtFile.read() is inflating data.
        # Returning EOF lets QImageReader unwind; the caller then raises the
        # repository's existing PROCESS_CANCELLED error.
        if self._cancelled.is_set():
            return b""
        return chunk

    def writeData(self, _data: bytes) -> int:
        return -1


class ImageSourceError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.code = code
        super().__init__(message)


class ImageSource(ABC):
    load_sizes_lazily = False
    compatible_jpeg_unknown_area_multiplier = 1

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

    def open_qimage_at_most(
        self,
        image_id: str,
        maximum_size: JpegMaximumSize,
    ) -> tuple[QImage, tuple[int, int]] | None:
        return None

    def open_compatible_jpeg_at_most(
        self,
        image_id: str,
        maximum_size: JpegMaximumSize,
    ) -> StreamedJpegDecode | None:
        return None

    def estimate_compatible_jpeg_size(
        self,
        logical_size: tuple[int, int],
        maximum_size: JpegMaximumSize,
    ) -> tuple[int, int]:
        """Return the retained decoded size used for cache admission.

        The default matches QImageReader's exact scaled output.  Backends that
        retain a coarser native decoder tier override this without exposing
        backend checks to the shared RasterBookRuntime.
        """

        return jpeg_decode_target_size(logical_size, maximum_size)

    def probe_jpeg_size(self, image_id: str) -> tuple[int, int] | None:
        """Read JPEG header dimensions without decoding pixel storage.

        RasterBookRuntime calls this only from a prefetch worker when retained
        pixel cost cannot be bounded from the lazy page index.  Keeping the
        probe behind the source API preserves archive locking/cancellation and
        prevents PageModel or the GUI thread from becoming an eager metadata
        reader.
        """

        if Path(image_id).suffix.casefold() not in {".jpg", ".jpeg", ".jpe"}:
            return None
        return self.probe_image_size(image_id)

    def probe_image_size(self, image_id: str) -> tuple[int, int] | None:
        """Read logical header dimensions without decoding pixel storage."""

        return self.logical_size(image_id)

    def fork_for_thumbnail(self) -> ImageSource:
        """Return a source session suitable for PageList thumbnail work.

        The default keeps compatibility with lightweight/custom sources.  A
        caller owns the returned source only when it is not ``self``.
        """
        return self

    def close(self) -> None:
        pass


class FolderImageSource(ImageSource):
    load_sizes_lazily = True
    # Pillow/libjpeg may retain a native tier just under twice the requested
    # width and height when the original dimensions are not indexed yet.
    compatible_jpeg_unknown_area_multiplier = 4

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
            if self.recursive:
                files = [
                    path
                    for path in self.source_path.rglob("*")
                    if (
                        path.is_file()
                        and path.suffix.lower() in SUPPORTED_EXTENSIONS
                    )
                ]
            else:
                files = []
                with os.scandir(self.source_path) as entries:
                    for entry in entries:
                        if (
                            os.path.splitext(entry.name)[1].lower()
                            not in SUPPORTED_EXTENSIONS
                        ):
                            continue
                        try:
                            is_file = entry.is_file(follow_symlinks=True)
                        except OSError:
                            # Match Path.is_file(): an entry removed during
                            # enumeration, or otherwise unavailable, is simply
                            # no longer part of the book.
                            continue
                        if is_file:
                            files.append(Path(entry.path))
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
                result = ImageOps.exif_transpose(image)
                result.load()
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

    def open_qimage_at_most(
        self,
        image_id: str,
        maximum_size: JpegMaximumSize,
    ) -> tuple[QImage, tuple[int, int]] | None:
        if Path(image_id).suffix.casefold() not in {".jpg", ".jpeg", ".jpe"}:
            return None
        try:
            data = _read_image_file_bytes(image_id)
        except OSError:
            return None
        self._file_size_cache[self._path_identity(image_id)] = len(data)
        decoded = _read_folder_jpeg_qimage_at_most(data, maximum_size)
        if decoded is not None:
            _image, logical_size = decoded
            self._size_cache[image_id] = logical_size
        return decoded

    def open_compatible_jpeg_at_most(
        self,
        image_id: str,
        maximum_size: JpegMaximumSize,
    ) -> StreamedJpegDecode | None:
        if Path(image_id).suffix.casefold() not in {".jpg", ".jpeg", ".jpe"}:
            return None
        try:
            data = _read_image_file_bytes(image_id)
        except OSError:
            return None
        self._file_size_cache[self._path_identity(image_id)] = len(data)
        decoded = _read_folder_compatible_jpeg_at_most(data, maximum_size)
        if decoded is None:
            return None
        image, logical_size = decoded
        self._size_cache[image_id] = logical_size
        return StreamedJpegDecode(
            qimage=image,
            original_size=logical_size,
            bytes_read=len(data),
            read_calls=1,
            backend="pillow-jpeg-draft",
            full_payload_materializations=1,
        )

    def estimate_compatible_jpeg_size(
        self,
        logical_size: tuple[int, int],
        maximum_size: JpegMaximumSize,
    ) -> tuple[int, int]:
        return jpeg_native_reduction_size(logical_size, maximum_size)

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

    def fork_for_thumbnail(self) -> ImageSource:
        return FolderImageSource(
            self.source_path,
            recursive=self.recursive,
            sort_descending=self.sort_descending,
            image_snapshot=tuple(self.list_images()),
        )


class ZipImageSource(ImageSource):
    load_sizes_lazily = True
    _READ_CHUNK_BYTES = 1024 * 1024

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
        self._active_lock = threading.RLock()
        self._closed = threading.Event()
        self._zip_closed = False
        self._active_requests: dict[str, set[threading.Event]] = {}
        self._listed_images: tuple[str, ...] | None = None
        self._display_names: dict[str, str] = {}
        self._size_cache: dict[str, tuple[int, int]] = {}

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
        cancelled = self._begin_request(image_id)
        try:
            stream = self._read_entry_stream(image_id, cancelled)
            self._raise_if_cancelled(cancelled)
            with Image.open(stream) as image:
                image.seek(0)
                result = ImageOps.exif_transpose(image)
                result.load()
            if cancelled.is_set():
                result.close()
                self._raise_if_cancelled(cancelled)
            return result
        except ImageSourceError:
            raise
        except Exception as exc:
            raise ImageSourceError(f"書庫内の画像を読み込めません: {image_id}") from exc
        finally:
            self._finish_request(image_id, cancelled)

    def open_qimage(self, image_id: str) -> QImage | None:
        if Path(image_id).suffix.casefold() != ".webp":
            return None
        cancelled = self._begin_request(image_id)
        try:
            stream = self._read_entry_stream(image_id, cancelled)
            self._raise_if_cancelled(cancelled)
            image = _read_webp_qimage(stream.getvalue())
            self._raise_if_cancelled(cancelled)
            return image
        except ImageSourceError:
            raise
        except Exception:
            return None
        finally:
            self._finish_request(image_id, cancelled)

    def open_qimage_at_most(
        self,
        image_id: str,
        maximum_size: JpegMaximumSize,
    ) -> tuple[QImage, tuple[int, int]] | None:
        if Path(image_id).suffix.casefold() not in {".jpg", ".jpeg", ".jpe"}:
            return None
        cancelled = self._begin_request(image_id)
        try:
            stream = self._read_entry_stream(image_id, cancelled)
            self._raise_if_cancelled(cancelled)
            decoded = _read_jpeg_qimage_at_most(
                stream.getvalue(),
                maximum_size,
            )
            self._raise_if_cancelled(cancelled)
            return decoded
        except ImageSourceError:
            raise
        except Exception:
            return None
        finally:
            self._finish_request(image_id, cancelled)

    def open_streamed_jpeg_at_most(
        self,
        image_id: str,
        maximum_size: JpegMaximumSize,
    ) -> StreamedJpegDecode | None:
        """Decode a ZIP JPEG directly from ZipExtFile through QImageReader."""
        if Path(image_id).suffix.casefold() not in {".jpg", ".jpeg", ".jpe"}:
            return None
        cancelled = self._begin_request(image_id)
        try:
            self._raise_if_cancelled(cancelled)
            with self._lock:
                self._raise_if_cancelled(cancelled)
                info = self._zip.NameToInfo.get(image_id)
                if info is None:
                    raise ImageSourceError(
                        f"書庫内の画像が見つかりません: {image_id}",
                        code=ArchiveErrorCode.ENTRY_NOT_FOUND.value,
                    )
                if info.file_size > MAX_IMAGE_ENTRY_BYTES:
                    raise ImageSourceError(
                        "書庫内の画像が大きすぎます。",
                        code=ArchiveErrorCode.ENTRY_TOO_LARGE.value,
                    )
                self._raise_if_cancelled(cancelled)
                try:
                    entry = self._zip.open(info, "r")
                except ImageSourceError:
                    raise
                except Exception:
                    return None
                with entry:
                    device = _ZipEntrySequentialDevice(
                        entry,
                        expected_size=info.file_size,
                        cancelled=cancelled,
                        maximum_bytes=MAX_IMAGE_ENTRY_BYTES,
                        read_chunk_bytes=self._READ_CHUNK_BYTES,
                    )
                    if not device.open(QIODevice.OpenModeFlag.ReadOnly):
                        return None
                    try:
                        reader = QImageReader(device, b"jpeg")
                        raw_size = reader.size()
                        transformation = reader.transformation()
                        self._raise_if_cancelled(cancelled)
                        if device.too_large:
                            raise ImageSourceError(
                                "書庫内の画像が大きすぎます。",
                                code=ArchiveErrorCode.ENTRY_TOO_LARGE.value,
                            )
                        if device.read_error is not None:
                            return None
                        raw_width = raw_size.width()
                        raw_height = raw_size.height()
                        if raw_width <= 0 or raw_height <= 0:
                            return None

                        swaps_axes = bool(
                            transformation
                            & QImageIOHandler.Transformation.TransformationRotate90
                        )
                        logical_size = (
                            (raw_height, raw_width)
                            if swaps_axes
                            else (raw_width, raw_height)
                        )
                        logical_target = jpeg_decode_target_size(
                            logical_size,
                            maximum_size,
                        )
                        raw_target = (
                            (logical_target[1], logical_target[0])
                            if swaps_axes
                            else logical_target
                        )

                        reader.setAutoTransform(True)
                        reader.setScaledSize(QSize(*raw_target))
                        image = reader.read()
                        self._raise_if_cancelled(cancelled)
                        if device.too_large:
                            raise ImageSourceError(
                                "書庫内の画像が大きすぎます。",
                                code=ArchiveErrorCode.ENTRY_TOO_LARGE.value,
                            )
                        if device.read_error is not None or image.isNull():
                            return None
                        return StreamedJpegDecode(
                            qimage=image,
                            original_size=logical_size,
                            bytes_read=device.bytes_read,
                            read_calls=device.read_calls,
                        )
                    finally:
                        device.close()
        except ImageSourceError:
            raise
        except Exception:
            return None
        finally:
            self._finish_request(image_id, cancelled)

    def open_compatible_jpeg_at_most(
        self,
        image_id: str,
        maximum_size: JpegMaximumSize,
    ) -> StreamedJpegDecode | None:
        """Decode through one seekable QByteArray owned by the page job.

        A Python ``QIODevice.readData`` callback for every libjpeg request is
        measurably slower on large detailed JPEGs.  Reading the entry in large
        chunks directly into one reserved ``QByteArray`` keeps one payload
        materialization, then lets QBuffer/QImageReader stay in C++.
        """
        if Path(image_id).suffix.casefold() not in {".jpg", ".jpeg", ".jpe"}:
            return None
        cancelled = self._begin_request(image_id)
        try:
            payload, read_calls = self._read_entry_qbytearray(
                image_id,
                cancelled,
            )
            self._raise_if_cancelled(cancelled)
            if payload.size() < 3 or not payload.startsWith(
                QByteArray(b"\xff\xd8\xff")
            ):
                # A declared JPEG with no JPEG signature is terminal input,
                # not a reason to reopen/materialize the same ZIP entry through
                # every fallback decoder.  Failing here also keeps first-use
                # plugin initialization away from a malformed worker payload.
                raise ImageSourceError(
                    f"書庫内のJPEGが破損しています: {image_id}"
                )
            decoded = _read_jpeg_qbytearray_at_most(
                payload,
                maximum_size,
            )
            self._raise_if_cancelled(cancelled)
            if decoded is None:
                return None
            image, logical_size = decoded
            self._size_cache[image_id] = logical_size
            return StreamedJpegDecode(
                qimage=image,
                original_size=logical_size,
                bytes_read=payload.size(),
                read_calls=read_calls,
                backend="qbytearray-qbuffer",
                full_payload_materializations=1,
            )
        except ImageSourceError:
            raise
        except Exception:
            return None
        finally:
            self._finish_request(image_id, cancelled)

    def estimate_compatible_jpeg_size(
        self,
        logical_size: tuple[int, int],
        maximum_size: JpegMaximumSize,
    ) -> tuple[int, int]:
        return jpeg_native_reduction_size(logical_size, maximum_size)

    def probe_image_size(self, image_id: str) -> tuple[int, int] | None:
        """Probe one ZIP image header without allocating its pixel raster."""

        cached = self._size_cache.get(image_id)
        if cached is not None:
            return cached
        cancelled = self._begin_request(image_id)
        try:
            self._raise_if_cancelled(cancelled)
            with self._lock:
                self._raise_if_cancelled(cancelled)
                info = self._zip.NameToInfo.get(image_id)
                if info is None:
                    raise ImageSourceError(
                        f"書庫内の画像が見つかりません: {image_id}",
                        code=ArchiveErrorCode.ENTRY_NOT_FOUND.value,
                    )
                if info.file_size > MAX_IMAGE_ENTRY_BYTES:
                    raise ImageSourceError(
                        "書庫内の画像が大きすぎます。",
                        code=ArchiveErrorCode.ENTRY_TOO_LARGE.value,
                    )
                with self._zip.open(info, "r") as entry:
                    device = _ZipEntrySequentialDevice(
                        entry,
                        expected_size=info.file_size,
                        cancelled=cancelled,
                        maximum_bytes=MAX_IMAGE_ENTRY_BYTES,
                        read_chunk_bytes=self._READ_CHUNK_BYTES,
                    )
                    if not device.open(QIODevice.OpenModeFlag.ReadOnly):
                        return None
                    try:
                        reader = QImageReader(device)
                        raw_size = reader.size()
                        transformation = reader.transformation()
                        self._raise_if_cancelled(cancelled)
                        if device.too_large:
                            raise ImageSourceError(
                                "書庫内の画像が大きすぎます。",
                                code=ArchiveErrorCode.ENTRY_TOO_LARGE.value,
                            )
                        if device.read_error is not None:
                            return None
                        raw_width = raw_size.width()
                        raw_height = raw_size.height()
                        if raw_width <= 0 or raw_height <= 0:
                            return None
                        swaps_axes = bool(
                            transformation
                            & QImageIOHandler.Transformation.TransformationRotate90
                        )
                        logical_size = (
                            (raw_height, raw_width)
                            if swaps_axes
                            else (raw_width, raw_height)
                        )
                        self._size_cache[image_id] = logical_size
                        return logical_size
                    finally:
                        device.close()
        except ImageSourceError:
            raise
        except Exception:
            return None
        finally:
            self._finish_request(image_id, cancelled)

    def probe_jpeg_size(self, image_id: str) -> tuple[int, int] | None:
        if Path(image_id).suffix.casefold() not in {".jpg", ".jpeg", ".jpe"}:
            return None
        return self.probe_image_size(image_id)

    def _begin_request(self, image_id: str) -> threading.Event:
        cancelled = threading.Event()
        with self._active_lock:
            if self._closed.is_set():
                cancelled.set()
            self._active_requests.setdefault(image_id, set()).add(cancelled)
        return cancelled

    def _finish_request(
        self,
        image_id: str,
        cancelled: threading.Event,
    ) -> None:
        close_zip = False
        with self._active_lock:
            requests = self._active_requests.get(image_id)
            if requests is not None:
                requests.discard(cancelled)
                if not requests:
                    self._active_requests.pop(image_id, None)
            if (
                self._closed.is_set()
                and not self._active_requests
                and not self._zip_closed
            ):
                self._zip_closed = True
                close_zip = True
        if close_zip:
            with self._lock:
                self._zip.close()

    @staticmethod
    def _raise_if_cancelled(cancelled: threading.Event) -> None:
        if cancelled.is_set():
            raise ImageSourceError(
                "ZIP画像の読み込みを中止しました。",
                code=ArchiveErrorCode.PROCESS_CANCELLED.value,
            )

    def _read_entry_stream(
        self,
        image_id: str,
        cancelled: threading.Event,
    ) -> io.BytesIO:
        self._raise_if_cancelled(cancelled)
        buffer = io.BytesIO()
        with self._lock:
            try:
                info = self._zip.getinfo(image_id)
            except KeyError as exc:
                raise ImageSourceError(
                    f"書庫内の画像が見つかりません: {image_id}",
                    code=ArchiveErrorCode.ENTRY_NOT_FOUND.value,
                ) from exc
            if info.file_size > MAX_IMAGE_ENTRY_BYTES:
                raise ImageSourceError(
                    "書庫内の画像が大きすぎます。",
                    code=ArchiveErrorCode.ENTRY_TOO_LARGE.value,
                )
            self._raise_if_cancelled(cancelled)
            if info.file_size:
                buffer.seek(info.file_size - 1)
                buffer.write(b"\0")
                buffer.seek(0)
            self._raise_if_cancelled(cancelled)
            total = 0
            with self._zip.open(info, "r") as file:
                while True:
                    self._raise_if_cancelled(cancelled)
                    chunk = file.read(self._READ_CHUNK_BYTES)
                    self._raise_if_cancelled(cancelled)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_IMAGE_ENTRY_BYTES:
                        raise ImageSourceError(
                            "書庫内の画像が大きすぎます。",
                            code=ArchiveErrorCode.ENTRY_TOO_LARGE.value,
                        )
                    buffer.write(chunk)
        buffer.truncate(total)
        buffer.seek(0)
        return buffer

    def _read_entry_qbytearray(
        self,
        image_id: str,
        cancelled: threading.Event,
    ) -> tuple[QByteArray, int]:
        self._raise_if_cancelled(cancelled)
        payload = QByteArray()
        read_calls = 0
        with self._lock:
            try:
                info = self._zip.getinfo(image_id)
            except KeyError as exc:
                raise ImageSourceError(
                    f"書庫内の画像が見つかりません: {image_id}",
                    code=ArchiveErrorCode.ENTRY_NOT_FOUND.value,
                ) from exc
            if info.file_size > MAX_IMAGE_ENTRY_BYTES:
                raise ImageSourceError(
                    "書庫内の画像が大きすぎます。",
                    code=ArchiveErrorCode.ENTRY_TOO_LARGE.value,
                )
            payload.reserve(max(0, int(info.file_size)))
            self._raise_if_cancelled(cancelled)
            total = 0
            with self._zip.open(info, "r") as file:
                while True:
                    self._raise_if_cancelled(cancelled)
                    chunk = file.read(self._READ_CHUNK_BYTES)
                    read_calls += 1
                    self._raise_if_cancelled(cancelled)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_IMAGE_ENTRY_BYTES:
                        raise ImageSourceError(
                            "書庫内の画像が大きすぎます。",
                            code=ArchiveErrorCode.ENTRY_TOO_LARGE.value,
                        )
                    payload.append(chunk)
        return payload, read_calls

    def cancel_image_request(self, image_id: str) -> None:
        with self._active_lock:
            for cancelled in tuple(
                self._active_requests.get(image_id, ())
            ):
                cancelled.set()

    def display_path(self, image_id: str) -> str:
        display_name = getattr(self, "_display_names", {}).get(image_id, image_id)
        return f"{self.source_path}!/{display_name}"

    def file_size(self, image_id: str) -> int | None:
        try:
            return self._zip.getinfo(image_id).file_size
        except KeyError:
            return None

    def fork_for_thumbnail(self) -> ImageSource:
        return ZipImageSource(
            self.source_path,
            sort_descending=self.sort_descending,
        )

    def close(self) -> None:
        close_zip = False
        with self._active_lock:
            self._closed.set()
            for requests in tuple(self._active_requests.values()):
                for cancelled in tuple(requests):
                    cancelled.set()
            if not self._active_requests and not self._zip_closed:
                self._zip_closed = True
                close_zip = True
        # Do not make the UI wait for a running decoder that owns _lock.
        # The final _finish_request closes the persistent ZipFile instead.
        if close_zip:
            with self._lock:
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
        listing_snapshot: SevenZipListingSnapshot | None = None,
    ) -> None:
        super().__init__(archive_path)
        self.backend = backend
        self.sort_descending = sort_descending
        self._closed = threading.Event()
        self._active_lock = threading.RLock()
        self._active_requests: dict[str, set[threading.Event]] = {}
        try:
            if listing_snapshot is None:
                listing = backend.list_entries(
                    str(self.source_path),
                    cancel_token=cancel_token,
                )
                entries = tuple(
                    select_image_entries(
                        listing,
                        SUPPORTED_EXTENSIONS,
                        descending=sort_descending,
                    )
                )
            else:
                listing = listing_snapshot.listing
                entries = listing_snapshot.image_entries
            if listing.encrypted:
                raise ArchiveBackendError(ArchiveErrorCode.PASSWORD_REQUIRED)
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
        self._listing_snapshot = SevenZipListingSnapshot(listing, entries)

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

    def fork_for_thumbnail(self) -> ImageSource:
        return SevenZipImageSource(
            self.source_path,
            backend=self.backend,
            sort_descending=self.sort_descending,
            listing_snapshot=self._listing_snapshot,
        )

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
