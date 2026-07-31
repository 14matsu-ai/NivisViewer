from __future__ import annotations

from dataclasses import dataclass

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, Qt, Signal, Slot
from PySide6.QtGui import QImage, QTransform

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
    layout_generation: int = 0
    source_generation: int = 0
    source_identity: str = ""
    split_range: tuple[int, int, int, int] | None = None
    smooth_transform: bool = True
    source_sized: bool = False


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
    direct_formats = {
        QImage.Format.Format_RGB888: ("RGB", "RGB"),
        QImage.Format.Format_RGBA8888: ("RGBA", "RGBA"),
        QImage.Format.Format_Grayscale8: ("L", "L"),
        QImage.Format.Format_RGBX8888: ("RGB", "RGBX"),
        QImage.Format.Format_RGB32: ("RGB", "BGRX"),
        QImage.Format.Format_ARGB32: ("RGBA", "BGRA"),
    }
    mode_and_raw_mode = direct_formats.get(image.format())
    if mode_and_raw_mode is not None:
        mode, raw_mode = mode_and_raw_mode
        return Image.frombytes(
            mode,
            (image.width(), image.height()),
            bytes(image.constBits()),
            "raw",
            raw_mode,
            image.bytesPerLine(),
            1,
        )

    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombytes(
        "RGBA",
        (converted.width(), converted.height()),
        bytes(converted.constBits()),
        "raw",
        "RGBA",
        converted.bytesPerLine(),
        1,
    )


def render_qimage(
    source: QImage,
    key: ViewerRenderKey,
) -> tuple[QImage, bool]:
    target = (
        max(1, int(key.target_width)),
        max(1, int(key.target_height)),
    )
    if (
        key.rotation % 360 == 0
        and key.crop is None
        and key.split_range is None
        and (source.width(), source.height()) == target
        and key.mode != "standard"
    ):
        return QImage(source), False

    if key.mode == "standard":
        prepared = QImage(source)
        if key.split_range is not None:
            left, top, width, height = key.split_range
            prepared = prepared.copy(
                left,
                top,
                max(1, width),
                max(1, height),
            )
        rotation = key.rotation % 360
        if rotation:
            prepared = prepared.transformed(
                QTransform().rotate(rotation),
                Qt.TransformationMode.SmoothTransformation,
            )
        if key.crop is not None:
            left, top, right, bottom = key.crop
            prepared = prepared.copy(
                left,
                top,
                max(1, right - left),
                max(1, bottom - top),
            )
        resized = (prepared.width(), prepared.height()) != target
        if resized:
            prepared = prepared.scaled(
                target[0],
                target[1],
                Qt.AspectRatioMode.IgnoreAspectRatio,
                (
                    Qt.TransformationMode.SmoothTransformation
                    if key.smooth_transform
                    else Qt.TransformationMode.FastTransformation
                ),
            )
        native_format = (
            QImage.Format.Format_ARGB32_Premultiplied
            if prepared.hasAlphaChannel()
            else QImage.Format.Format_RGB32
        )
        if prepared.format() != native_format:
            prepared = prepared.convertToFormat(native_format)
        return prepared, resized

    prepared = qimage_to_pillow(source)
    try:
        if key.split_range is not None:
            left, top, width, height = key.split_range
            split = prepared.crop(
                (
                    left,
                    top,
                    left + max(1, width),
                    top + max(1, height),
                )
            )
            prepared.close()
            prepared = split

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
