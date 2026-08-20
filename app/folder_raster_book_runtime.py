"""Book-scoped raster Viewer runtime for folder-backed image books.

Folder and directly opened image paths use the shared RasterBookRuntime
pipeline.  Files are opened only while a worker decodes them; decoded QImages
and layout-dependent QPixmaps have separate book-scoped lifetimes.
"""

from __future__ import annotations

from .image_source import FolderImageSource
from .raster_book_runtime import RasterBookRuntime


class FolderRasterBookRuntime(RasterBookRuntime):
    """Raster runtime constrained to a folder snapshot source."""

    _source_type = FolderImageSource
    _runtime_display_name = "Folder"


__all__ = ["FolderRasterBookRuntime"]
