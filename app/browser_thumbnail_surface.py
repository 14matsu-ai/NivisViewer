"""Worker-safe display geometry and final thumbnail pixels (no widgets)."""
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QPainter


def _snap_display_rect(rect, dpr):
    """Keep image dimensions fixed when only its screen position changes.

    Rounding both absolute edges makes a thumbnail gain/lose a physical pixel
    while scrolling at fractional DPI. That requires several full rasters per
    item. Round the size once and the placement independently instead.
    """
    return QRectF(round(rect.x()*dpr)/dpr, round(rect.y()*dpr)/dpr,
                  max(1, round(rect.width()*dpr))/dpr,
                  max(1, round(rect.height()*dpr))/dpr)


def surface_geometry_key(source, target, dpr):
    return (round(source.x(), 6), round(source.y(), 6),
            round(source.width(), 6), round(source.height(), 6),
            max(1, round(target.width()*dpr)), max(1, round(target.height()*dpr)),
            round(dpr, 6))


@dataclass(frozen=True)
class BrowserThumbnailDisplayPlan:
    frame: tuple[int, int, int, int]
    display_mode: str
    dpr: float

    def geometries(self, image):
        return _display_geometries(self, image.width(), image.height())

    def render(self, image, cancelled):
        result = []
        for key, source in self.geometries(image):
            if cancelled.is_set():
                return ()
            target = QRectF(0, 0, key[4]/self.dpr, key[5]/self.dpr)
            surface = prepare_display_thumbnail_surface(image, QRectF(*source), target, self.dpr)
            result.append((key, surface.image))
        return tuple(result)


@lru_cache(maxsize=128)
def _display_geometries(plan, width, height):
    target, source = thumbnail_image_rects(
        QRect(*plan.frame), QSize(width, height), plan.display_mode,
        device_pixel_ratio=plan.dpr,
    )
    return ((surface_geometry_key(source, target, plan.dpr),
             (source.x(), source.y(), source.width(), source.height())),)


@dataclass(frozen=True)
class DisplayThumbnailSurface:
    image: QImage
    cache_hit: bool
    prepared_with_resampling: bool


def prepare_display_thumbnail_surface(
    image: QImage,
    source_rect: QRectF,
    target_rect: QRectF,
    device_pixel_ratio: float,
) -> DisplayThumbnailSurface:
    """Prepare physical display pixels once for a later unscaled paint."""

    dpr = max(0.5, float(device_pixel_ratio))
    physical_width = max(1, round(target_rect.width() * dpr))
    physical_height = max(1, round(target_rect.height() * dpr))
    source_is_integral = all(
        abs(value - round(value)) < 0.000001
        for value in (
            source_rect.x(),
            source_rect.y(),
            source_rect.width(),
            source_rect.height(),
        )
    )
    source_matches_surface = (
        source_is_integral
        and round(source_rect.width()) == physical_width
        and round(source_rect.height()) == physical_height
    )
    if source_matches_surface:
        source = QRect(
            round(source_rect.x()),
            round(source_rect.y()),
            physical_width,
            physical_height,
        )
        if source == image.rect():
            prepared = QImage(image)
        else:
            prepared = image.copy(source)
        prepared.setDevicePixelRatio(dpr)
        return DisplayThumbnailSurface(prepared, False, False)

    prepared = QImage(
        physical_width,
        physical_height,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    prepared.fill(Qt.GlobalColor.transparent)
    surface_painter = QPainter(prepared)
    try:
        surface_painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            True,
        )
        surface_painter.drawImage(
            QRectF(0, 0, physical_width, physical_height),
            image,
            source_rect,
        )
    finally:
        surface_painter.end()
    prepared.setDevicePixelRatio(dpr)
    return DisplayThumbnailSurface(prepared, False, True)


def thumbnail_image_rects(
    thumbnail_rect: QRect,
    image_size: QSize,
    display_mode: str,
    *,
    device_pixel_ratio: float = 1.0,
) -> tuple[QRectF, QRectF]:
    """Return target/source rectangles for the Browser thumbnail paint."""
    image_width = max(1, image_size.width())
    image_height = max(1, image_size.height())
    dpr = max(0.5, float(device_pixel_ratio))
    content_rect = thumbnail_content_rect(
        thumbnail_rect,
        display_mode=display_mode,
        device_pixel_ratio=dpr,
    )
    if display_mode == "center_crop":
        target = content_rect
        target_ratio = target.width() / max(1.0, target.height())
        source_ratio = image_width / image_height
        if source_ratio > target_ratio:
            source_width = image_height * target_ratio
            source = QRectF(
                (image_width - source_width) / 2,
                0.0,
                source_width,
                float(image_height),
            )
        else:
            source_height = image_width / max(0.0001, target_ratio)
            source = QRectF(
                0.0,
                (image_height - source_height) / 2,
                float(image_width),
                source_height,
            )
        return target, source

    available = content_rect
    source_ratio = image_width / image_height
    target_ratio = available.width() / max(1.0, available.height())
    if source_ratio > target_ratio:
        width = available.width()
        height = width / source_ratio
    else:
        height = available.height()
        width = height * source_ratio
    no_upscale = min(
        1.0,
        image_width / max(1.0, width * dpr),
        image_height / max(1.0, height * dpr),
    )
    width *= no_upscale
    height *= no_upscale
    target = QRectF(
        available.center().x() - width / 2,
        available.center().y() - height / 2,
        width,
        height,
    )
    return (
        _snap_display_rect(target, dpr),
        QRectF(0.0, 0.0, float(image_width), float(image_height)),
    )


def thumbnail_content_rect(
    thumbnail_rect: QRect,
    *,
    display_mode: str = "fit",
    device_pixel_ratio: float = 1.0,
) -> QRectF:
    """Return the shared maximum target for real or placeholder content."""

    inset = 1 if display_mode == "center_crop" else 4
    return _snap_display_rect(
        QRectF(thumbnail_rect.adjusted(inset, inset, -inset, -inset)),
        max(0.5, float(device_pixel_ratio)),
    )
