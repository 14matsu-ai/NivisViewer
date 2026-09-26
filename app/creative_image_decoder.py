from __future__ import annotations

from contextlib import contextmanager
import io
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from PIL import Image

from .supported_formats import (
    CLIP_EXTENSIONS,
    CREATIVE_PROJECT_EXTENSIONS,
    KRA_EXTENSIONS,
    ORA_EXTENSIONS,
    XCF_EXTENSIONS,
    xcf_loading_enabled,
)

from .gimp_xcf_backend import (
    GimpXcfBackendError,
    GimpXcfBackendUnavailable,
    GimpXcfCancelled,
    render_xcf_with_gimp,
)


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_HEADER_BYTES = 24
PROJECT_MEMBER_MAX_BYTES = 1024 * 1024 * 1024
PROJECT_MAX_PIXELS = 256 * 1024 * 1024

CLIP_ROOT_HEADER_SIZE = 24
CLIP_CHUNK_HEADER_SIZE = 16
CLIP_DATABASE_MAX_BYTES = 256 * 1024 * 1024
CLIP_PREVIEW_MAX_BYTES = 128 * 1024 * 1024
CLIP_IDENTIFIER_MAX_BYTES = 1024 * 1024
CLIP_MAX_PREVIEW_DIMENSION = 100_000

XCF_MAX_SUPPORTED_VERSION = 26
XCF_MAX_CANVAS_PIXELS = 64 * 1024 * 1024
XCF_HEADER_READ_BYTES = 64


CreativeSource = str | Path | bytes | bytearray | memoryview


class CreativeImageError(RuntimeError):
    pass


class CreativeImageUnsupportedError(CreativeImageError):
    pass


def is_creative_image_id(image_id: str | Path) -> bool:
    return Path(image_id).suffix.casefold() in CREATIVE_PROJECT_EXTENSIONS


def _normalized_suffix(
    source: CreativeSource,
    suffix: str | None,
) -> str:
    if suffix is not None:
        value = str(suffix).strip().casefold()
        return value if value.startswith(".") else f".{value}"
    if isinstance(source, (str, Path)):
        return Path(source).suffix.casefold()
    raise CreativeImageError("A file suffix is required for in-memory project data")


def _read_source_prefix(source: CreativeSource, size: int) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source[: max(0, int(size))])
    with open(source, "rb") as stream:
        return stream.read(max(0, int(size)))


@contextmanager
def _open_binary_source(source: CreativeSource):
    if isinstance(source, (bytes, bytearray, memoryview)):
        yield io.BytesIO(bytes(source))
        return
    with open(source, "rb") as stream:
        yield stream


@contextmanager
def _open_zip_source(source: CreativeSource):
    if isinstance(source, (bytes, bytearray, memoryview)):
        archive = zipfile.ZipFile(io.BytesIO(bytes(source)), "r")
    else:
        archive = zipfile.ZipFile(os.fspath(source), "r")
    try:
        yield archive
    finally:
        archive.close()


def _read_zip_member(
    archive: zipfile.ZipFile,
    name: str,
    *,
    maximum_bytes: int,
) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise CreativeImageError(f"Missing project member: {name}") from exc
    if info.is_dir() or info.file_size < 0:
        raise CreativeImageError(f"Invalid project member: {name}")
    if info.file_size > maximum_bytes:
        raise CreativeImageError(f"Project member is too large: {name}")
    with archive.open(info, "r") as stream:
        data = stream.read(maximum_bytes + 1)
    if len(data) > maximum_bytes or len(data) != info.file_size:
        raise CreativeImageError(f"Invalid project member payload: {name}")
    return data


def _read_zip_member_prefix(
    archive: zipfile.ZipFile,
    name: str,
    *,
    maximum_bytes: int,
    prefix_bytes: int,
) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise CreativeImageError(f"Missing project member: {name}") from exc
    if info.is_dir() or info.file_size < 0 or info.file_size > maximum_bytes:
        raise CreativeImageError(f"Invalid project member: {name}")
    with archive.open(info, "r") as stream:
        return stream.read(max(0, int(prefix_bytes)))


def _png_size(
    data: bytes,
    *,
    expected_size: tuple[int, int] | None = None,
) -> tuple[int, int]:
    if (
        len(data) < PNG_HEADER_BYTES
        or data[:8] != PNG_SIGNATURE
        or data[8:12] != b"\x00\x00\x00\r"
        or data[12:16] != b"IHDR"
    ):
        raise CreativeImageError("Invalid embedded PNG header")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if (
        width <= 0
        or height <= 0
        or width * height > PROJECT_MAX_PIXELS
    ):
        raise CreativeImageError("Embedded PNG dimensions exceed the safety limit")
    size = (width, height)
    if expected_size is not None and size != expected_size:
        raise CreativeImageError("Embedded PNG dimensions do not match metadata")
    return size


def _decode_png(
    data: bytes,
    *,
    expected_size: tuple[int, int] | None = None,
) -> Image.Image:
    _png_size(data, expected_size=expected_size)
    try:
        with Image.open(io.BytesIO(data), formats=("PNG",)) as image:
            image.seek(0)
            result = image.copy()
            result.load()
            return result
    except CreativeImageError:
        raise
    except Exception as exc:
        raise CreativeImageError("Embedded PNG could not be decoded") from exc


def _validate_openraster(archive: zipfile.ZipFile) -> None:
    mimetype = _read_zip_member(
        archive,
        "mimetype",
        maximum_bytes=128,
    )
    if mimetype != b"image/openraster":
        raise CreativeImageError("Invalid OpenRaster mimetype")


def _zip_project_members(suffix: str) -> tuple[str, str]:
    if suffix in KRA_EXTENSIONS:
        return "mergedimage.png", "preview.png"
    if suffix in ORA_EXTENSIONS:
        return "mergedimage.png", "Thumbnails/thumbnail.png"
    raise CreativeImageUnsupportedError(f"Unsupported ZIP project format: {suffix}")


def _decode_zip_project(
    source: CreativeSource,
    suffix: str,
    *,
    thumbnail: bool,
    minimum_long_edge: int,
) -> Image.Image:
    merged_name, thumbnail_name = _zip_project_members(suffix)
    try:
        with _open_zip_source(source) as archive:
            if suffix in ORA_EXTENSIONS:
                _validate_openraster(archive)

            if thumbnail:
                try:
                    preview = _read_zip_member(
                        archive,
                        thumbnail_name,
                        maximum_bytes=CLIP_PREVIEW_MAX_BYTES,
                    )
                    preview_size = _png_size(preview)
                    if max(preview_size) >= max(0, int(minimum_long_edge)):
                        return _decode_png(preview)
                except (CreativeImageError, OSError, zipfile.BadZipFile, RuntimeError):
                    # A damaged optional preview must not hide a valid composite.
                    pass

            merged = _read_zip_member(
                archive,
                merged_name,
                maximum_bytes=PROJECT_MEMBER_MAX_BYTES,
            )
        return _decode_png(merged)
    except CreativeImageError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise CreativeImageError("Layered project archive could not be read") from exc


def _probe_zip_project_size(
    source: CreativeSource,
    suffix: str,
) -> tuple[int, int] | None:
    merged_name, _thumbnail_name = _zip_project_members(suffix)
    try:
        with _open_zip_source(source) as archive:
            if suffix in ORA_EXTENSIONS:
                _validate_openraster(archive)
            header = _read_zip_member_prefix(
                archive,
                merged_name,
                maximum_bytes=PROJECT_MEMBER_MAX_BYTES,
                prefix_bytes=PNG_HEADER_BYTES,
            )
        return _png_size(header)
    except Exception:
        return None


def _read_exact_at(stream, offset: int, size: int) -> bytes:
    if offset < 0 or size < 0:
        raise CreativeImageError("Negative CLIP offset or size")
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise CreativeImageError("Truncated CLIP container")
    return data


def _read_clip_database(source: CreativeSource) -> bytes:
    """Extract only CHNKSQLi without scanning or materializing CHNKExta bodies.

    The envelope layout is a small read-only Python implementation based on
    independently verified CLIP container research. See CREATIVE_FORMATS_PHASE2.
    """

    with _open_binary_source(source) as stream:
        stream.seek(0, os.SEEK_END)
        actual_size = int(stream.tell())
        root = _read_exact_at(stream, 0, CLIP_ROOT_HEADER_SIZE)
        if root[:8] != b"CSFCHUNK":
            raise CreativeImageError("Invalid CLIP container signature")

        declared_size = int.from_bytes(root[8:16], "big")
        first_chunk_offset = int.from_bytes(root[16:24], "big")
        if declared_size != actual_size:
            raise CreativeImageError("CLIP declared size does not match the file")
        if (
            first_chunk_offset < CLIP_ROOT_HEADER_SIZE
            or first_chunk_offset + CLIP_CHUNK_HEADER_SIZE > actual_size
        ):
            raise CreativeImageError("Invalid CLIP first-chunk offset")

        chunk_header = _read_exact_at(
            stream,
            first_chunk_offset,
            CLIP_CHUNK_HEADER_SIZE,
        )
        if chunk_header[:8] != b"CHNKHead":
            raise CreativeImageError("CLIP CHNKHead is missing")
        header_size = int.from_bytes(chunk_header[8:16], "big")
        if (
            header_size < 24
            or header_size > 24 + CLIP_IDENTIFIER_MAX_BYTES
            or first_chunk_offset + CLIP_CHUNK_HEADER_SIZE + header_size
            > actual_size
        ):
            raise CreativeImageError("Invalid CLIP CHNKHead size")

        fixed_header = _read_exact_at(
            stream,
            first_chunk_offset + CLIP_CHUNK_HEADER_SIZE,
            24,
        )
        database_offset = int.from_bytes(fixed_header[8:16], "big")
        identifier_size = int.from_bytes(fixed_header[16:24], "big")
        if (
            identifier_size > CLIP_IDENTIFIER_MAX_BYTES
            or header_size != 24 + identifier_size
        ):
            raise CreativeImageError("Invalid CLIP identifier size")
        if (
            database_offset < CLIP_ROOT_HEADER_SIZE
            or database_offset + CLIP_CHUNK_HEADER_SIZE > actual_size
        ):
            raise CreativeImageError("Invalid CLIP database offset")

        database_header = _read_exact_at(
            stream,
            database_offset,
            CLIP_CHUNK_HEADER_SIZE,
        )
        if database_header[:8] != b"CHNKSQLi":
            raise CreativeImageError("CLIP database chunk is missing")
        database_size = int.from_bytes(database_header[8:16], "big")
        if database_size <= 0 or database_size > CLIP_DATABASE_MAX_BYTES:
            raise CreativeImageError("CLIP database exceeds the safety limit")
        database_end = (
            database_offset + CLIP_CHUNK_HEADER_SIZE + database_size
        )
        if database_end > actual_size:
            raise CreativeImageError("CLIP database extends past end of file")

        return _read_exact_at(
            stream,
            database_offset + CLIP_CHUNK_HEADER_SIZE,
            database_size,
        )


@contextmanager
def _sqlite_from_payload(payload: bytes):
    connection = sqlite3.connect(":memory:")
    temporary_path: str | None = None
    try:
        if hasattr(connection, "deserialize"):
            connection.deserialize(payload)
        else:
            connection.close()
            with tempfile.NamedTemporaryFile(
                suffix=".sqlite",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary_path = temporary.name
            uri = Path(temporary_path).resolve().as_uri()
            connection = sqlite3.connect(
                f"{uri}?mode=ro&immutable=1",
                uri=True,
            )

        connection.execute("PRAGMA query_only=ON")
        try:
            connection.execute("PRAGMA trusted_schema=OFF")
        except sqlite3.DatabaseError:
            pass
        yield connection
    except sqlite3.DatabaseError as exc:
        raise CreativeImageError("Invalid embedded CLIP SQLite database") from exc
    finally:
        try:
            connection.close()
        except Exception:
            pass
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _table_columns(
    connection: sqlite3.Connection,
    table: str,
) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(
            f'PRAGMA table_xinfo("{table}")'
        )
    }


def _clip_preview_record(
    source: CreativeSource,
    *,
    include_data: bool,
) -> tuple[bytes | None, tuple[int, int]]:
    database = _read_clip_database(source)
    with _sqlite_from_payload(database) as connection:
        required_preview = {
            "MainId",
            "CanvasId",
            "ImageType",
            "ImageWidth",
            "ImageHeight",
            "ImageData",
        }
        if not required_preview.issubset(
            _table_columns(connection, "CanvasPreview")
        ):
            raise CreativeImageError("Unsupported CLIP CanvasPreview schema")

        canvas_id: int | None = None
        if "ProjectCanvas" in _table_columns(connection, "Project"):
            row = connection.execute(
                "SELECT ProjectCanvas FROM Project ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row is not None and row[0] is not None:
                try:
                    candidate = int(row[0])
                except (TypeError, ValueError):
                    candidate = 0
                if candidate > 0:
                    canvas_id = candidate

        if canvas_id is None:
            row = connection.execute(
                "SELECT CanvasId FROM CanvasPreview ORDER BY MainId LIMIT 1"
            ).fetchone()
            if row is None:
                raise CreativeImageError("CLIP has no CanvasPreview")
            canvas_id = int(row[0])

        metadata = connection.execute(
            "SELECT MainId, ImageWidth, ImageHeight, length(ImageData) "
            "FROM CanvasPreview WHERE CanvasId = ?1 "
            "ORDER BY MainId LIMIT 1",
            (canvas_id,),
        ).fetchone()
        if metadata is None:
            raise CreativeImageError(
                "CLIP primary canvas has no embedded preview"
            )

        main_id = int(metadata[0])
        width = int(metadata[1])
        height = int(metadata[2])
        encoded_size = int(metadata[3] or 0)
        if (
            width <= 0
            or height <= 0
            or width > CLIP_MAX_PREVIEW_DIMENSION
            or height > CLIP_MAX_PREVIEW_DIMENSION
            or width * height > PROJECT_MAX_PIXELS
        ):
            raise CreativeImageError(
                "CLIP preview dimensions exceed the safety limit"
            )
        if (
            encoded_size <= 0
            or encoded_size > CLIP_PREVIEW_MAX_BYTES
        ):
            raise CreativeImageError(
                "CLIP preview payload exceeds the safety limit"
            )

        size = (width, height)
        if not include_data:
            return None, size

        row = connection.execute(
            "SELECT ImageData FROM CanvasPreview "
            "WHERE MainId = ?1 AND CanvasId = ?2 LIMIT 1",
            (main_id, canvas_id),
        ).fetchone()
        if row is None or row[0] is None:
            raise CreativeImageError("CLIP preview payload is missing")
        data = bytes(row[0])
        if len(data) != encoded_size:
            raise CreativeImageError("CLIP preview payload length changed")
        _png_size(data, expected_size=size)
        return data, size


def _decode_clip_preview(source: CreativeSource) -> Image.Image:
    data, size = _clip_preview_record(source, include_data=True)
    if data is None:
        raise CreativeImageError("CLIP preview payload is missing")
    result = _decode_png(data, expected_size=size)
    result.info["_nivis_preview_only"] = True
    return result


def probe_xcf_header(
    header: bytes | bytearray | memoryview,
) -> tuple[int, tuple[int, int]] | None:
    data = bytes(header)
    if len(data) < 18 or data[:9] != b"gimp xcf ":
        return None

    nul = data.find(b"\x00", 9, min(len(data), 32))
    if nul < 0:
        return None
    version_tag = data[9:nul]
    if version_tag == b"file":
        version = 0
    elif (
        len(version_tag) == 4
        and version_tag[:1] == b"v"
        and version_tag[1:].isdigit()
    ):
        version = int(version_tag[1:])
    else:
        return None

    offset = nul + 1
    if len(data) < offset + 8:
        return None
    width = int.from_bytes(data[offset:offset + 4], "big")
    height = int.from_bytes(data[offset + 4:offset + 8], "big")
    if width <= 0 or height <= 0:
        return None
    return version, (width, height)


def probe_xcf_size_from_header(
    header: bytes | bytearray | memoryview,
) -> tuple[int, int] | None:
    parsed = probe_xcf_header(header)
    return None if parsed is None else parsed[1]


def _xcf_metadata(
    source: CreativeSource,
) -> tuple[int, tuple[int, int]]:
    parsed = probe_xcf_header(
        _read_source_prefix(source, XCF_HEADER_READ_BYTES)
    )
    if parsed is None:
        raise CreativeImageError("Invalid XCF header")
    return parsed


def _decode_xcf_native(source: CreativeSource) -> Image.Image:
    from .xcf_raster_reader import decode_xcf_raster
    return decode_xcf_raster(source, max_pixels=XCF_MAX_CANVAS_PIXELS)


def _validated_xcf_result(
    result: Image.Image,
    expected_size: tuple[int, int],
) -> Image.Image:
    if result.size != expected_size:
        result.close()
        raise CreativeImageError(
            "XCF decoded size does not match its header"
        )
    return result


def _decode_xcf(source: CreativeSource) -> Image.Image:
    if not xcf_loading_enabled():
        raise CreativeImageUnsupportedError("XCF loading is disabled in settings")
    from .xcf_raster_reader import check_xcf_lane
    check_xcf_lane()
    version, size = _xcf_metadata(source)
    if size[0] * size[1] > XCF_MAX_CANVAS_PIXELS:
        raise CreativeImageError("XCF canvas exceeds the safety limit")

    try:
        return _validated_xcf_result(_decode_xcf_native(source), size)
    except GimpXcfCancelled:
        raise
    except Exception as exc:
        # Features outside the direct reader remain authoritative GIMP work.
        logging.getLogger(__name__).debug("XCF route=GIMP; direct reader rejected: %s", exc)

    # Unsupported precision, masks, groups, effects and blend spaces retain
    # the authoritative renderer; do not parse/decode the same file twice.
    try:
        return _validated_xcf_result(
            render_xcf_with_gimp(source),
            size,
        )
    except GimpXcfCancelled:
        raise
    except GimpXcfBackendUnavailable as exc:
        raise CreativeImageUnsupportedError(
            f"XCF v{version} requires an installed GIMP 3.x renderer"
        ) from exc
    except GimpXcfBackendError as exc:
        raise CreativeImageError(
            f"GIMP 3.x could not render XCF v{version}"
        ) from exc

def decode_creative_image(
    source: CreativeSource,
    *,
    suffix: str | None = None,
) -> Image.Image:
    normalized = _normalized_suffix(source, suffix)
    if normalized in KRA_EXTENSIONS | ORA_EXTENSIONS:
        return _decode_zip_project(
            source,
            normalized,
            thumbnail=False,
            minimum_long_edge=0,
        )
    if normalized in CLIP_EXTENSIONS:
        return _decode_clip_preview(source)
    if normalized in XCF_EXTENSIONS:
        return _decode_xcf(source)
    raise CreativeImageUnsupportedError(
        f"Unsupported creative project format: {normalized}"
    )


def decode_creative_thumbnail(
    source: CreativeSource,
    *,
    suffix: str | None = None,
    minimum_long_edge: int = 0,
) -> Image.Image:
    normalized = _normalized_suffix(source, suffix)
    if normalized in KRA_EXTENSIONS | ORA_EXTENSIONS:
        return _decode_zip_project(
            source,
            normalized,
            thumbnail=True,
            minimum_long_edge=minimum_long_edge,
        )
    if normalized in CLIP_EXTENSIONS:
        return _decode_clip_preview(source)
    if normalized in XCF_EXTENSIONS:
        image = _decode_xcf(source)
        target = max(256, max(0, int(minimum_long_edge)) * 2)
        if max(image.size) > target:
            image.thumbnail(
                (target, target),
                Image.Resampling.LANCZOS,
                reducing_gap=2.0,
            )
        return image
    raise CreativeImageUnsupportedError(
        f"Unsupported creative project format: {normalized}"
    )


def probe_creative_image_size(
    source: CreativeSource,
    *,
    suffix: str | None = None,
) -> tuple[int, int] | None:
    normalized = _normalized_suffix(source, suffix)
    if normalized in KRA_EXTENSIONS | ORA_EXTENSIONS:
        return _probe_zip_project_size(source, normalized)
    if normalized in CLIP_EXTENSIONS:
        try:
            _data, size = _clip_preview_record(
                source,
                include_data=False,
            )
            return size
        except Exception:
            return None
    if normalized in XCF_EXTENSIONS:
        try:
            _version, size = _xcf_metadata(source)
            return size
        except Exception:
            return None
    return None


__all__ = [
    "CLIP_DATABASE_MAX_BYTES",
    "CREATIVE_PROJECT_EXTENSIONS",
    "CreativeImageError",
    "CreativeImageUnsupportedError",
    "XCF_HEADER_READ_BYTES",
    "XCF_MAX_SUPPORTED_VERSION",
    "decode_creative_image",
    "decode_creative_thumbnail",
    "is_creative_image_id",
    "probe_creative_image_size",
    "probe_xcf_header",
    "probe_xcf_size_from_header",
]
