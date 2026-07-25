"""Measure thumbnail cache codec trade-offs using generated test images.

This script writes only to a temporary directory and needs Pillow plus Python's
standard library. It is intentionally independent from the GUI.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
from time import perf_counter

from PIL import Image, ImageDraw, ImageFilter, ImageStat


@dataclass(frozen=True)
class AuditResult:
    sample: str
    codec: str
    bytes: int
    save_ms: float
    load_ms: float
    psnr: float
    edge_ratio: float
    alpha_mse: float


def _samples(size: int = 512) -> dict[str, Image.Image]:
    line_art = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(line_art)
    for offset in range(8, size, 11):
        draw.line((0, offset, size, size - offset), fill="black", width=1)
    draw.text((18, 18), "NivisViewer 0123456789", fill="black")

    gradient = Image.new("RGB", (size, size))
    pixels = gradient.load()
    for y in range(size):
        for x in range(size):
            pixels[x, y] = (x * 255 // (size - 1), y * 255 // (size - 1), 128)

    photo = Image.effect_noise((size, size), 36).convert("RGB")
    photo = photo.filter(ImageFilter.GaussianBlur(0.65))

    rgba = line_art.convert("RGBA")
    alpha = Image.new("L", rgba.size)
    alpha_pixels = alpha.load()
    for y in range(size):
        for x in range(size):
            alpha_pixels[x, y] = (x + y) * 255 // (2 * (size - 1))
    rgba.putalpha(alpha)
    return {
        "line_text": line_art,
        "gradient": gradient,
        "photo": photo,
        "rgba": rgba,
    }


def _rgb_mse(left: Image.Image, right: Image.Image) -> float:
    lhs = left.convert("RGB")
    rhs = right.convert("RGB")
    total = 0
    count = lhs.width * lhs.height * 3
    for a, b in zip(lhs.tobytes(), rhs.tobytes(), strict=True):
        total += (a - b) ** 2
    return total / max(1, count)


def _alpha_mse(left: Image.Image, right: Image.Image) -> float:
    lhs = left.convert("RGBA").getchannel("A")
    rhs = right.convert("RGBA").getchannel("A")
    total = sum(
        (a - b) ** 2
        for a, b in zip(lhs.tobytes(), rhs.tobytes(), strict=True)
    )
    return total / max(1, lhs.width * lhs.height)


def _edge_energy(image: Image.Image) -> float:
    return ImageStat.Stat(
        image.convert("L").filter(ImageFilter.FIND_EDGES)
    ).mean[0]


def run_audit() -> tuple[AuditResult, ...]:
    encoders = {
        "webp-q80": ("WEBP", {"quality": 80, "method": 4, "exact": True}),
        "webp-q90": ("WEBP", {"quality": 90, "method": 4, "exact": True}),
        "webp-q95": ("WEBP", {"quality": 95, "method": 4, "exact": True}),
        "webp-lossless": ("WEBP", {"lossless": True, "method": 4, "exact": True}),
        "png": ("PNG", {"optimize": False}),
    }
    results: list[AuditResult] = []
    for sample_name, image in _samples().items():
        reference_edges = _edge_energy(image)
        for codec_name, (format_name, options) in encoders.items():
            output = BytesIO()
            started = perf_counter()
            image.save(output, format=format_name, **options)
            save_ms = (perf_counter() - started) * 1000
            encoded = output.getvalue()
            started = perf_counter()
            with Image.open(BytesIO(encoded)) as decoded_source:
                decoded = decoded_source.convert(image.mode)
            load_ms = (perf_counter() - started) * 1000
            mse = _rgb_mse(image, decoded)
            results.append(
                AuditResult(
                    sample_name,
                    codec_name,
                    len(encoded),
                    save_ms,
                    load_ms,
                    math.inf if mse == 0 else 10 * math.log10(255 * 255 / mse),
                    _edge_energy(decoded) / max(0.0001, reference_edges),
                    _alpha_mse(image, decoded),
                )
            )
    return tuple(results)


def main() -> None:
    print(
        "sample codec bytes save_ms load_ms psnr_db edge_ratio alpha_mse"
    )
    for result in run_audit():
        print(
            f"{result.sample:10} {result.codec:13} {result.bytes:7d} "
            f"{result.save_ms:7.2f} {result.load_ms:7.2f} "
            f"{result.psnr:7.2f} {result.edge_ratio:8.3f} "
            f"{result.alpha_mse:9.3f}"
        )


if __name__ == "__main__":
    main()
