"""Compare baseline/current WebP on identical synthetic pixels, never user images."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import random
from statistics import median
import tempfile
from time import perf_counter

from PIL import Image, ImageDraw, ImageFont, features

from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_render import (
    THUMBNAIL_ENCODER_QUALITY,
    ThumbnailRenderSpec, render_pil_thumbnail,
)
from scripts.thumbnail_quality_audit import _rgb_mse


def samples() -> dict[str, Image.Image]:
    width, height = 724, 1024
    rng = random.Random(703149)
    data = bytearray()
    for y in range(height):
        for x in range(width):
            noise = rng.randint(-28, 28)
            for value in (75 + 90 * x / width + 45 * math.sin(y / 37),
                          80 + 85 * y / height + 35 * math.sin(x / 23),
                          135 + 55 * math.sin((x + y) / 45)):
                data.append(max(0, min(255, round(value + noise))))
    cover = Image.frombytes("RGB", (width, height), bytes(data))
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "meiryo.ttc"
    font = ImageFont.truetype(str(font_path), 30) if font_path.exists() else ImageFont.load_default(size=30)
    title = ImageFont.truetype(str(font_path), 62) if font_path.exists() else ImageFont.load_default(size=62)
    draw = ImageDraw.Draw(cover)
    for n in range(32):
        x, y = rng.randrange(width), rng.randrange(height)
        draw.ellipse((x - 80, y - 40, x + 80, y + 40), outline=(230, 180, 80), width=3)
    draw.rectangle((25, 45, width - 25, 170), fill=(25, 32, 52))
    draw.text((38, 65), "NIVIS 2026", font=title, fill="white")
    text = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(text)
    for y in range(28, height - 40, 47):
        draw.text((24, y), "画像と文字 012345 / NivisViewer", font=font, fill="black")
    lines = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(lines)
    for n in range(0, width, 23):
        draw.line((n, 0, width - n, height), fill="black", width=2)
    for y in range(0, height, 37):
        draw.line((0, y, width, y), fill=(60, 60, 60), width=1)
    draw.rectangle((20, 35, width - 20, 125), fill="white")
    draw.text((32, 44), "線画 / LINE ART", font=title, fill="black")
    alpha = text.convert("RGBA")
    alpha.putalpha(Image.linear_gradient("L").resize(alpha.size))
    return {"color_cover": cover, "text": text, "line_art": lines, "transparent": alpha}


def run(output: Path, repetitions: int = 7, baseline_quality: int = 70) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="nivis-webp-audit-") as directory:
        for name, source in samples().items():
            for dpr in (1.0, 1.25, 1.5, 2.0):
                spec = ThumbnailRenderSpec.from_settings(
                    149, "portrait_1_sqrt2", "letterbox", device_pixel_ratio=dpr,
                    quality_mode="auto", max_edge=512,
                )
                old = replace(spec, encoder_quality=baseline_quality)
                qimage, _ = render_pil_thumbnail(source, spec)
                old_image, _ = render_pil_thumbnail(source, old)
                assert qimage == old_image  # Encoding policy does not change rendering.
                pixels = spec.encoding_policy.prepare_pixels(ThumbnailDiskCache._qimage_to_pil(qimage))
                for quality in (baseline_quality, THUMBNAIL_ENCODER_QUALITY):
                    path = Path(directory) / f"{name}-{dpr}-{quality}.webp"
                    options = replace(spec.encoding_policy, quality=quality).webp_options()
                    saves, loads = [], []
                    for _ in range(repetitions):
                        started = perf_counter()
                        pixels.save(path, format="WEBP", **options)
                        saves.append((perf_counter() - started) * 1000)
                        started = perf_counter()
                        loaded = ThumbnailDiskCache._read_qimage(path)
                        loads.append((perf_counter() - started) * 1000)
                        assert loaded is not None
                    decoded = ThumbnailDiskCache._qimage_to_pil(loaded)
                    mse = _rgb_mse(pixels, decoded)
                    decoded.save(output / f"{name}-{dpr}-q{quality}.png")
                    results.append(dict(sample=name, dpr=dpr, quality=quality,
                                        pixels=list(pixels.size), bytes=path.stat().st_size,
                                        encode_ms=median(saves), read_ms=median(loads),
                                        psnr_db=None if mse == 0 else 10 * math.log10(255 ** 2 / mse),
                                        lossless=False, alpha_policy=spec.encoding_policy.alpha_token))
    (output / "measurements.json").write_text(json.dumps({
        "webp_version": features.version("webp"), "repetitions": repetitions,
        "conditions": "Synthetic images; 149 logical px; 1:sqrt2; auto; max edge512; letterbox; warm filesystem",
        "results": results,
    }, indent=2), encoding="utf-8")
    for r in results:
        print(f"{r['sample']:12} DPR={r['dpr']:4} q{r['quality']} {r['pixels']} "
              f"{r['bytes']:6} B encode={r['encode_ms']:.3f}ms read={r['read_ms']:.3f}ms")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-quality", type=int, choices=range(1, 101), default=70)
    args = parser.parse_args()
    run(args.output, baseline_quality=args.baseline_quality)
