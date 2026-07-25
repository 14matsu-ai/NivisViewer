from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from threading import Lock

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage


THUMBNAIL_IMPLEMENTATION_VERSION = 2
SMART_CROP_ALGORITHM_VERSION = 1
THUMBNAIL_SIZE_BUCKETS = (96, 128, 160, 192, 256, 320, 384)

FRAME_RATIOS: dict[str, tuple[float, str]] = {
    "square_1_1": (1.0, "1:1"),
    "landscape_3_2": (3 / 2, "3:2 横"),
    "portrait_2_3": (2 / 3, "2:3 縦"),
    "landscape_4_3": (4 / 3, "4:3 横"),
    "portrait_3_4": (3 / 4, "3:4 縦"),
    "landscape_16_9": (16 / 9, "16:9 横"),
    "portrait_9_16": (9 / 16, "9:16 縦"),
    "landscape_sqrt2_1": (math.sqrt(2), "√2:1 横"),
    "portrait_1_sqrt2": (1 / math.sqrt(2), "1:√2 縦"),
}

CROP_MODES = {
    "letterbox": "レターボックス（全体を表示）",
    "center_crop": "パンスキャン（中央部分）",
    "smart_crop": "スマートクリップ（自動検出）",
}


def quantize_thumbnail_size(size: int) -> int:
    value = max(THUMBNAIL_SIZE_BUCKETS[0], min(THUMBNAIL_SIZE_BUCKETS[-1], int(size)))
    return min(THUMBNAIL_SIZE_BUCKETS, key=lambda bucket: (abs(bucket - value), bucket))


def frame_size_from_long_edge(size: int, ratio_id: str) -> QSize:
    long_edge = max(1, int(size))
    ratio = FRAME_RATIOS.get(
        ratio_id,
        FRAME_RATIOS["portrait_1_sqrt2"],
    )[0]
    if ratio >= 1:
        width = long_edge
        height = max(1, round(long_edge / ratio))
    else:
        height = long_edge
        width = max(1, round(long_edge * ratio))
    return QSize(width, height)


@dataclass(frozen=True)
class ThumbnailRenderSpec:
    frame_width: int
    frame_height: int
    frame_ratio_id: str
    crop_mode: str
    smart_crop_version: int = SMART_CROP_ALGORITHM_VERSION
    implementation_version: int = THUMBNAIL_IMPLEMENTATION_VERSION

    @classmethod
    def from_settings(
        cls,
        thumbnail_size: int,
        frame_ratio_id: str,
        crop_mode: str,
    ) -> ThumbnailRenderSpec:
        ratio_id = (
            frame_ratio_id
            if frame_ratio_id in FRAME_RATIOS
            else "portrait_1_sqrt2"
        )
        mode = crop_mode if crop_mode in CROP_MODES else "smart_crop"
        frame = frame_size_from_long_edge(
            quantize_thumbnail_size(thumbnail_size),
            ratio_id,
        )
        return cls(frame.width(), frame.height(), ratio_id, mode)

    @property
    def long_edge(self) -> int:
        return max(self.frame_width, self.frame_height)

    @property
    def cache_token(self) -> int:
        payload = (
            f"{self.frame_width}x{self.frame_height}|{self.frame_ratio_id}|"
            f"{self.crop_mode}|{self.smart_crop_version}|"
            f"{self.implementation_version}"
        ).encode("utf-8")
        return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & (
            (1 << 63) - 1
        )


class SmartCropCache:
    def __init__(self, capacity: int = 2048) -> None:
        self.capacity = max(16, int(capacity))
        self._lock = Lock()
        self._items: OrderedDict[tuple[object, ...], tuple[float, float, float, float]] = (
            OrderedDict()
        )

    def get(
        self,
        key: tuple[object, ...],
    ) -> tuple[float, float, float, float] | None:
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(
        self,
        key: tuple[object, ...],
        value: tuple[float, float, float, float],
    ) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)


def smart_crop_cache_key(
    path: str | Path,
    *,
    source_size: int | None,
    source_mtime_ns: int | None,
    entry_path: str = "",
    ratio_id: str,
) -> tuple[object, ...]:
    return (
        str(path),
        entry_path,
        source_size,
        source_mtime_ns,
        ratio_id,
        SMART_CROP_ALGORITHM_VERSION,
    )


def normalized_center_crop(
    source_size: tuple[int, int],
    target_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    width, height = (max(1, int(value)) for value in source_size)
    target_width, target_height = (max(1, int(value)) for value in target_size)
    source_ratio = width / height
    target_ratio = target_width / target_height
    if source_ratio > target_ratio:
        crop_width = target_ratio / source_ratio
        return ((1.0 - crop_width) / 2, 0.0, crop_width, 1.0)
    crop_height = source_ratio / target_ratio
    return (0.0, (1.0 - crop_height) / 2, 1.0, crop_height)


def detect_smart_crop(
    image: Image.Image,
    target_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    try:
        frame = image.copy()
        frame.seek(0)
        frame.thumbnail((256, 256), Image.Resampling.BILINEAR)
        proxy = frame.convert("RGBA")
        alpha = proxy.getchannel("A")
        alpha_box = alpha.getbbox()
        content_box = alpha_box or (0, 0, proxy.width, proxy.height)

        rgb = proxy.convert("RGB")
        corner = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
        difference = ImageChops.difference(rgb, corner).convert("L")
        difference = difference.point(lambda value: 255 if value > 10 else 0)
        variance_box = difference.getbbox()
        if variance_box is not None:
            left = max(content_box[0], variance_box[0])
            top = max(content_box[1], variance_box[1])
            right = min(content_box[2], variance_box[2])
            bottom = min(content_box[3], variance_box[3])
            if right - left >= 8 and bottom - top >= 8:
                content_box = (left, top, right, bottom)

        content = rgb.crop(content_box)
        target_ratio = max(0.01, target_size[0] / max(1, target_size[1]))
        content_ratio = content.width / max(1, content.height)
        if content_ratio > target_ratio:
            window_width = max(1, round(content.height * target_ratio))
            window_height = content.height
            travel = max(0, content.width - window_width)
            candidates = [
                (round(travel * index / 20), 0, window_width, window_height)
                for index in range(21)
            ]
        else:
            window_width = content.width
            window_height = max(1, round(content.width / target_ratio))
            travel = max(0, content.height - window_height)
            candidates = [
                (0, round(travel * index / 20), window_width, window_height)
                for index in range(21)
            ]

        energy = ImageOps.grayscale(content).filter(ImageFilter.FIND_EDGES)
        scores: list[float] = []
        for x, y, width, height in candidates:
            region = energy.crop((x, y, x + width, y + height))
            score = ImageStat.Stat(region).mean[0]
            center_x = x + width / 2
            center_y = y + height / 2
            distance = abs(center_x / content.width - 0.5) + abs(
                center_y / content.height - 0.5
            )
            scores.append(score - distance * 2.0)
        if not scores or max(scores) - min(scores) < 0.75:
            return normalized_center_crop(image.size, target_size)
        best = candidates[max(range(len(scores)), key=scores.__getitem__)]
        x, y, width, height = best
        left = content_box[0] + x
        top = content_box[1] + y
        return (
            left / proxy.width,
            top / proxy.height,
            width / proxy.width,
            height / proxy.height,
        )
    except Exception:
        return normalized_center_crop(image.size, target_size)


def render_pil_thumbnail(
    image: Image.Image,
    spec: ThumbnailRenderSpec,
    *,
    normalized_crop: tuple[float, float, float, float] | None = None,
) -> tuple[QImage, tuple[float, float, float, float] | None]:
    frame = image.copy()
    frame.seek(0)
    prepared = ImageOps.exif_transpose(frame)
    crop = normalized_crop
    if spec.crop_mode != "letterbox":
        if crop is None:
            crop = (
                detect_smart_crop(
                    prepared,
                    (spec.frame_width, spec.frame_height),
                )
                if spec.crop_mode == "smart_crop"
                else normalized_center_crop(
                    prepared.size,
                    (spec.frame_width, spec.frame_height),
                )
            )
        left = max(0, min(prepared.width - 1, round(crop[0] * prepared.width)))
        top = max(0, min(prepared.height - 1, round(crop[1] * prepared.height)))
        right = max(
            left + 1,
            min(prepared.width, round((crop[0] + crop[2]) * prepared.width)),
        )
        bottom = max(
            top + 1,
            min(prepared.height, round((crop[1] + crop[3]) * prepared.height)),
        )
        prepared = prepared.crop((left, top, right, bottom))
        target = (spec.frame_width, spec.frame_height)
    else:
        target = (spec.frame_width, spec.frame_height)
        prepared.thumbnail(target, Image.Resampling.LANCZOS)

    if spec.crop_mode != "letterbox":
        prepared = prepared.resize(target, Image.Resampling.LANCZOS)
    return pil_to_qimage(prepared), crop


def pil_to_qimage(image: Image.Image) -> QImage:
    if image.mode == "RGB":
        data = image.tobytes("raw", "RGB")
        return QImage(
            data,
            image.width,
            image.height,
            image.width * 3,
            QImage.Format.Format_RGB888,
        ).copy()
    if image.mode == "RGBA":
        data = image.tobytes("raw", "RGBA")
        return QImage(
            data,
            image.width,
            image.height,
            image.width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
    if image.mode == "L":
        data = image.tobytes("raw", "L")
        return QImage(
            data,
            image.width,
            image.height,
            image.width,
            QImage.Format.Format_Grayscale8,
        ).copy()
    converted = image.convert("RGBA" if "transparency" in image.info else "RGB")
    return pil_to_qimage(converted)

