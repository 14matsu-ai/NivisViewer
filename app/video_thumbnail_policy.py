from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from PIL import Image, ImageFilter, ImageOps, ImageStat
from PySide6.QtGui import QImage

from .thumbnail_render import (
    ThumbnailRenderSpec,
    pil_to_qimage,
    render_pil_thumbnail,
)


VIDEO_FRAME_SELECTION_VERSION = 2
VIDEO_PREVIEW_IMPLEMENTATION_VERSION = 2


class VideoThumbnailFrameMode(str, Enum):
    SMART = "smart"
    ONE_THIRD = "one_third"
    WINDOWS_SHELL = "windows_shell"


@dataclass(frozen=True)
class VideoMetadata:
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    sample_aspect_ratio: float | None = None
    display_aspect_ratio: float | None = None
    rotation: int = 0


@dataclass(frozen=True)
class VideoFrameCandidate:
    timestamp: float
    image: Image.Image
    score: float


class VideoThumbnailPolicy:
    """Selects a representative video frame before common thumbnail rendering."""

    def __init__(self, mode: str | VideoThumbnailFrameMode = "smart") -> None:
        try:
            self.mode = (
                mode
                if isinstance(mode, VideoThumbnailFrameMode)
                else VideoThumbnailFrameMode(str(mode))
            )
        except ValueError:
            self.mode = VideoThumbnailFrameMode.SMART

    def candidate_timestamps(self, duration: float | None) -> tuple[float, ...]:
        usable_duration = self._finite_positive(duration)
        if usable_duration is None:
            return (1.0,)
        safe_end = max(0.0, usable_duration - min(1.0, usable_duration * 0.1))
        if self.mode is VideoThumbnailFrameMode.ONE_THIRD:
            return (min(safe_end, max(0.0, usable_duration / 3.0)),)
        if self.mode is VideoThumbnailFrameMode.SMART:
            output: list[float] = []
            for fraction in (1 / 3, 1 / 2, 2 / 3):
                value = min(safe_end, max(0.0, usable_duration * fraction))
                if not output or abs(value - output[-1]) >= 0.05:
                    output.append(value)
            return tuple(output) or (0.0,)
        return ()

    def select(
        self,
        candidates: list[tuple[float, Image.Image]],
    ) -> VideoFrameCandidate | None:
        if not candidates:
            return None
        scored = [
            VideoFrameCandidate(timestamp, image, self.score(image))
            for timestamp, image in candidates
        ]
        if self.mode is not VideoThumbnailFrameMode.SMART:
            return scored[0]
        # A tiny later-frame preference breaks near ties away from title cards.
        return max(scored, key=lambda candidate: (candidate.score, candidate.timestamp))

    @staticmethod
    def score(image: Image.Image) -> float:
        proxy = ImageOps.exif_transpose(image).convert("RGB")
        proxy.thumbnail((96, 96), Image.Resampling.BILINEAR)
        gray = ImageOps.grayscale(proxy)
        statistics = ImageStat.Stat(gray)
        mean = float(statistics.mean[0])
        variance = float(statistics.var[0])
        edge = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]
        saturation = ImageStat.Stat(proxy.convert("HSV").getchannel("S")).mean[0]
        extreme_penalty = max(0.0, 18.0 - min(mean, 255.0 - mean)) * 3.0
        uniform_penalty = max(0.0, 120.0 - variance) * 0.08
        return variance * 0.03 + edge * 1.6 + saturation * 0.18 - extreme_penalty - uniform_penalty

    @staticmethod
    def prepare_frame(
        image: Image.Image,
        metadata: VideoMetadata | None,
    ) -> Image.Image:
        prepared = image.copy()
        if metadata is None:
            return prepared
        rotation = int(metadata.rotation) % 360
        if rotation in {90, 180, 270}:
            prepared = prepared.rotate(-rotation, expand=True)
        display_ratio = VideoThumbnailPolicy.display_aspect_ratio(metadata)
        raw_ratio = prepared.width / max(1, prepared.height)
        if display_ratio is not None and abs(display_ratio - raw_ratio) > 0.001:
            width = max(1, round(prepared.height * display_ratio))
            prepared = prepared.resize((width, prepared.height), Image.Resampling.LANCZOS)
        return prepared

    @staticmethod
    def display_aspect_ratio(metadata: VideoMetadata) -> float | None:
        direct = VideoThumbnailPolicy._finite_positive(metadata.display_aspect_ratio)
        if direct is not None and direct < 100:
            return 1.0 / direct if metadata.rotation % 180 else direct
        if not metadata.width or not metadata.height:
            return None
        sar = VideoThumbnailPolicy._finite_positive(metadata.sample_aspect_ratio) or 1.0
        ratio = metadata.width * sar / metadata.height
        if metadata.rotation % 180:
            ratio = 1.0 / ratio
        return ratio if math.isfinite(ratio) and 0 < ratio < 100 else None

    @staticmethod
    def render(
        image: Image.Image,
        spec: ThumbnailRenderSpec,
        metadata: VideoMetadata | None = None,
    ) -> QImage:
        prepared = VideoThumbnailPolicy.prepare_frame(image, metadata)
        rendered, _crop = render_pil_thumbnail(prepared, spec)
        if (
            spec.crop_mode == "letterbox"
            and (
                rendered.width() != spec.frame_width
                or rendered.height() != spec.frame_height
            )
        ):
            fitted = VideoThumbnailPolicy.qimage_to_pil(rendered)
            canvas = Image.new(
                "RGBA",
                (spec.frame_width, spec.frame_height),
                (0, 0, 0, 0),
            )
            canvas.alpha_composite(
                fitted.convert("RGBA"),
                (
                    (spec.frame_width - fitted.width) // 2,
                    (spec.frame_height - fitted.height) // 2,
                ),
            )
            return pil_to_qimage(canvas)
        return rendered

    @staticmethod
    def cache_variant(mode: str | VideoThumbnailFrameMode) -> str:
        try:
            normalized = (
                mode.value
                if isinstance(mode, VideoThumbnailFrameMode)
                else VideoThumbnailFrameMode(str(mode)).value
            )
        except ValueError:
            normalized = VideoThumbnailFrameMode.SMART.value
        return (
            f"video-v{VIDEO_PREVIEW_IMPLEMENTATION_VERSION}"
            f"|selection-v{VIDEO_FRAME_SELECTION_VERSION}|mode={normalized}"
        )

    @staticmethod
    def qimage_to_pil(image: QImage) -> Image.Image:
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        return Image.frombytes(
            "RGBA",
            (converted.width(), converted.height()),
            bytes(converted.bits()),
        )

    @staticmethod
    def _finite_positive(value: float | None) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0 else None
