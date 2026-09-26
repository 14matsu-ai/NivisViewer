from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image
if TYPE_CHECKING:
    from psd_tools import PSDImage

from .supported_formats import PSD_EXTENSIONS


# psd-tools 1.17.4+ can guard rendering allocations per document.
# Viewer work is allowed a generous ceiling while Browser thumbnail work is
# kept smaller so a pathological PSD cannot monopolise the thumbnail lane.
PSD_VIEWER_MAX_ALLOC_BYTES = 2 * 1024 * 1024 * 1024
PSD_THUMBNAIL_MAX_ALLOC_BYTES = 512 * 1024 * 1024
PSD_HEADER_SIZE = 26


class PsdDecodeError(RuntimeError):
    pass


def is_psd_image_id(image_id: str | Path) -> bool:
    return Path(image_id).suffix.casefold() in PSD_EXTENSIONS


def probe_psd_size_from_header(
    header: bytes | bytearray | memoryview,
) -> tuple[int, int] | None:
    """Return PSD/PSB canvas dimensions without decoding pixel storage."""

    data = bytes(header)
    if len(data) < PSD_HEADER_SIZE or data[:4] != b"8BPS":
        return None

    version = int.from_bytes(data[4:6], "big")
    if version not in {1, 2}:
        return None

    height = int.from_bytes(data[14:18], "big")
    width = int.from_bytes(data[18:22], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


def _open_psd(
    source: str | Path | bytes,
    *,
    max_alloc_bytes: int,
) -> PSDImage:
    # Keep NumPy/PSD parser initialization out of normal Viewer startup.
    from psd_tools import PSDImage

    if isinstance(source, bytes):
        return PSDImage.open(
            io.BytesIO(source),
            max_alloc_bytes=max_alloc_bytes,
        )
    return PSDImage.open(
        source,
        max_alloc_bytes=max_alloc_bytes,
    )


def _detach(image: Image.Image) -> Image.Image:
    try:
        result = image.copy()
        result.load()
        return result
    finally:
        image.close()


def _merged_preview(psd: PSDImage) -> Image.Image:
    """Return Photoshop's stored merged preview.

    Phase 1 deliberately does not install psd-tools[composite].  Rendering
    hundreds of layers/effects is a different workload from image viewing and
    would also pull scipy/scikit-image into the portable build.  Files saved
    without a merged preview therefore fail explicitly instead of silently
    showing an inaccurate layer reconstruction.
    """

    if not psd.has_preview():
        raise PsdDecodeError("PSD/PSB has no saved merged preview")
    image = psd.topil()
    if image is None:
        raise PsdDecodeError(
            "PSD/PSB does not contain a readable merged preview"
        )
    return _detach(image)


def decode_psd_image(
    source: str | Path | bytes,
) -> Image.Image:
    """Decode the document's saved composite for Viewer use."""

    try:
        psd = _open_psd(
            source,
            max_alloc_bytes=PSD_VIEWER_MAX_ALLOC_BYTES,
        )
        return _merged_preview(psd)
    except PsdDecodeError:
        raise
    except Exception as exc:
        raise PsdDecodeError("Failed to decode PSD/PSB") from exc


def decode_psd_thumbnail(
    source: str | Path | bytes,
    *,
    minimum_long_edge: int = 0,
) -> Image.Image:
    """Prefer the embedded thumbnail and avoid full composite decode.

    A small embedded preview is not upscaled when the Browser requests a
    substantially larger thumbnail; in that case the saved merged composite
    supplies the higher-quality source.
    """

    try:
        psd = _open_psd(
            source,
            max_alloc_bytes=PSD_THUMBNAIL_MAX_ALLOC_BYTES,
        )
        thumbnail = psd.thumbnail()
        if thumbnail is not None:
            required = max(0, int(minimum_long_edge))
            if max(thumbnail.size) >= required:
                return _detach(thumbnail)
            thumbnail.close()

        return _merged_preview(psd)
    except PsdDecodeError:
        raise
    except Exception as exc:
        raise PsdDecodeError("Failed to decode PSD/PSB thumbnail") from exc
