from __future__ import annotations

from dataclasses import dataclass
from threading import Event

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

# New rendering callers describe reduction and enlargement independently.  The
# old combined modes above remain a compatibility boundary for non-raster and
# not-yet-migrated callers; they are intentionally not the source of truth for
# the new book-scoped raster runtimes.
DOWNSCALE_ALGORITHM_LABELS = {
    "auto": "自動",
    "fast": "高速",
    "smooth": "滑らか",
    "sharp": "シャープ",
    "area": "面積平均",
    "nearest": "最近傍",
}

UPSCALE_ALGORITHM_LABELS = {
    "auto": "自動",
    "bilinear": "バイリニア",
    "bicubic": "バイキュービック",
    "lanczos": "Lanczos",
    "nearest": "最近傍",
}


def normalize_downscale_algorithm(value: object) -> str:
    algorithm = str(value)
    return (
        algorithm
        if algorithm in DOWNSCALE_ALGORITHM_LABELS
        else "auto"
    )


def normalize_upscale_algorithm(value: object) -> str:
    algorithm = str(value)
    return (
        algorithm
        if algorithm in UPSCALE_ALGORITHM_LABELS
        else "auto"
    )


@dataclass(frozen=True)
class ResamplingPolicy:
    """Independent native-backed filters for reduction and enlargement."""

    downscale_algorithm: str = "auto"
    upscale_algorithm: str = "auto"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "downscale_algorithm",
            normalize_downscale_algorithm(self.downscale_algorithm),
        )
        object.__setattr__(
            self,
            "upscale_algorithm",
            normalize_upscale_algorithm(self.upscale_algorithm),
        )


def resampling_policy_for_legacy_mode(
    mode: object,
) -> ResamplingPolicy:
    """Translate a legacy combined setting without keeping two authorities."""

    normalized = normalize_resampling_mode(mode)
    downscale_algorithm, upscale_algorithm = {
        "standard": ("auto", "auto"),
        "moire_reduction": ("area", "bicubic"),
        "high_quality": ("sharp", "lanczos"),
        "smooth": ("smooth", "bilinear"),
        "pixel": ("nearest", "nearest"),
    }[normalized]
    return ResamplingPolicy(downscale_algorithm, upscale_algorithm)


_DOWNSCALE_PILLOW_FILTERS = {
    # All entries execute in Pillow's native backend; no per-pixel Python work
    # is introduced.  ``auto`` is scale-aware and resolved separately below.
    "fast": Image.Resampling.BILINEAR,
    "smooth": Image.Resampling.BICUBIC,
    "sharp": Image.Resampling.LANCZOS,
    "area": Image.Resampling.BOX,
    "nearest": Image.Resampling.NEAREST,
}

_UPSCALE_PILLOW_FILTERS = {
    "bilinear": Image.Resampling.BILINEAR,
    "bicubic": Image.Resampling.BICUBIC,
    "lanczos": Image.Resampling.LANCZOS,
    "nearest": Image.Resampling.NEAREST,
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


def pillow_resampling_for_policy(
    policy: ResamplingPolicy,
    source_size: tuple[int, int],
    target_size: tuple[int, int],
) -> Image.Resampling:
    """Resolve the one filter used for the final physical-size resample.

    An anisotropic resize is treated as a reduction when either axis shrinks,
    because that axis needs an anti-aliasing filter.  Equal-size images never
    call this function from :func:`render_qimage`.
    """

    shrinking = (
        target_size[0] < source_size[0]
        or target_size[1] < source_size[1]
    )
    if shrinking:
        if policy.downscale_algorithm == "auto":
            scale = min(
                target_size[0] / max(1, source_size[0]),
                target_size[1] / max(1, source_size[1]),
            )
            # BOX is a native area-style reduction and avoids spending a wide
            # Lanczos kernel on a source that is reduced by at least half.
            # Moderate reductions keep more edge detail with Lanczos.
            return (
                Image.Resampling.BOX
                if scale <= 0.5
                else Image.Resampling.LANCZOS
            )
        return _DOWNSCALE_PILLOW_FILTERS[policy.downscale_algorithm]
    if policy.upscale_algorithm == "auto":
        return Image.Resampling.BICUBIC
    return _UPSCALE_PILLOW_FILTERS[policy.upscale_algorithm]


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
    # ``None`` means that this is an old combined-mode caller.  New raster
    # callers pass both values, making the effective algorithms part of the
    # frozen key's equality/hash identity and therefore of every frame cache.
    downscale_algorithm: str | None = None
    upscale_algorithm: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", normalize_resampling_mode(self.mode))
        if self.downscale_algorithm is not None:
            object.__setattr__(
                self,
                "downscale_algorithm",
                normalize_downscale_algorithm(self.downscale_algorithm),
            )
        if self.upscale_algorithm is not None:
            object.__setattr__(
                self,
                "upscale_algorithm",
                normalize_upscale_algorithm(self.upscale_algorithm),
            )

    @property
    def has_explicit_resampling_policy(self) -> bool:
        return (
            self.downscale_algorithm is not None
            or self.upscale_algorithm is not None
        )

    @property
    def resampling_policy(self) -> ResamplingPolicy:
        legacy = resampling_policy_for_legacy_mode(self.mode)
        return ResamplingPolicy(
            (
                self.downscale_algorithm
                if self.downscale_algorithm is not None
                else legacy.downscale_algorithm
            ),
            (
                self.upscale_algorithm
                if self.upscale_algorithm is not None
                else legacy.upscale_algorithm
            ),
        )


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
        self.finished = Event()

    @Slot()
    def run(self) -> None:
        try:
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
        finally:
            self.finished.set()


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
        and (
            key.mode != "standard"
            or key.has_explicit_resampling_policy
        )
    ):
        return QImage(source), False

    if key.mode == "standard" and not key.has_explicit_resampling_policy:
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
            if key.has_explicit_resampling_policy:
                resampling = pillow_resampling_for_policy(
                    key.resampling_policy,
                    prepared.size,
                    target,
                )
            else:
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
