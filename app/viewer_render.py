from __future__ import annotations

from dataclasses import dataclass

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, Signal, Slot
from PySide6.QtGui import QImage

from .thumbnail_render import pil_to_qimage


RESAMPLING_MODE_LABELS = {
    "standard": "標準",
    "moire_reduction": "モアレ低減",
    "high_quality": "高品質",
    "smooth": "滑らか",
    "pixel": "ドット保持",
}


def normalize_resampling_mode(value: object) -> str:
    mode = str(value)
    return mode if mode in RESAMPLING_MODE_LABELS else "standard"


def pillow_resampling_for(
    mode: str,
    source_size: tuple[int, int],
    target_size: tuple[int, int],
) -> Image.Resampling | None:
    normalized = normalize_resampling_mode(mode)
    if normalized == "standard":
        return None
    if normalized == "moire_reduction":
        shrinking = (
            target_size[0] < source_size[0]
            or target_size[1] < source_size[1]
        )
        return (
            Image.Resampling.BOX
            if shrinking
            else Image.Resampling.BICUBIC
        )
    return {
        "high_quality": Image.Resampling.LANCZOS,
        "smooth": Image.Resampling.BILINEAR,
        "pixel": Image.Resampling.NEAREST,
    }[normalized]


@dataclass(frozen=True)
class ViewerRenderKey:
    image_id: str
    source_cache_key: int
    target_width: int
    target_height: int
    mode: str
    rotation: int
    device_pixel_ratio_milli: int
    crop: tuple[int, int, int, int] | None = None
    purpose: str = "viewer"
    request_generation: int = 0


@dataclass(frozen=True)
class ViewerRenderResult:
    key: ViewerRenderKey
    generation: int
    image: QImage | None
    resized: bool
    error: str | None = None


class ViewerRenderSignals(QObject):
    completed = Signal(object)


class ViewerRenderTask(QRunnable):
    def __init__(
        self,
        source: QImage,
        key: ViewerRenderKey,
        generation: int,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.source = QImage(source)
        self.key = key
        self.generation = int(generation)
        self.signals = ViewerRenderSignals()

    @Slot()
    def run(self) -> None:
        try:
            image, resized = render_qimage(self.source, self.key)
            result = ViewerRenderResult(
                self.key,
                self.generation,
                image,
                resized,
            )
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            result = ViewerRenderResult(
                self.key,
                self.generation,
                None,
                False,
                str(exc),
            )
        self.signals.completed.emit(result)


def qimage_to_pillow(image: QImage) -> Image.Image:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombytes(
        "RGBA",
        (converted.width(), converted.height()),
        bytes(converted.bits()),
    )


def render_qimage(
    source: QImage,
    key: ViewerRenderKey,
) -> tuple[QImage, bool]:
    prepared = qimage_to_pillow(source)
    try:
        rotation = key.rotation % 360
        if rotation:
            rotated = prepared.rotate(-rotation, expand=True)
            prepared.close()
            prepared = rotated

        if key.crop is not None:
            left, top, right, bottom = key.crop
            cropped = prepared.crop((left, top, right, bottom))
            prepared.close()
            prepared = cropped

        target = (
            max(1, int(key.target_width)),
            max(1, int(key.target_height)),
        )
        resized = prepared.size != target
        if resized:
            resampling = pillow_resampling_for(
                key.mode,
                prepared.size,
                target,
            )
            if resampling is not None:
                output = prepared.resize(target, resampling)
                prepared.close()
                prepared = output
            else:
                resized = False
        return pil_to_qimage(prepared), resized
    finally:
        prepared.close()
