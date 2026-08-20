"""Shared book-scoped raster Viewer runtime contract.

The implementation originated in the ZIP runtime, then became source-agnostic
when folder books adopted the same one-job scheduler and artifact ownership.
The historical ``ZipRaster*`` public names remain available from
``zip_raster_book_runtime`` for compatibility; production integration should
use these neutral aliases.
"""

from __future__ import annotations

from .zip_raster_book_runtime import (
    RasterBookRuntime,
    ZipRasterDisplayUnit as RasterDisplayUnit,
    ZipRasterFrame as RasterFrame,
    ZipRasterFramePage as RasterFramePage,
    ZipRasterPage as RasterPage,
    ZipRasterRenderSpec as RasterRenderSpec,
    ZipRasterRequest as RasterRequest,
    ZipRasterRuntimeMetrics as RasterRuntimeMetrics,
)

__all__ = [
    "RasterBookRuntime",
    "RasterDisplayUnit",
    "RasterFrame",
    "RasterFramePage",
    "RasterPage",
    "RasterRenderSpec",
    "RasterRequest",
    "RasterRuntimeMetrics",
]
