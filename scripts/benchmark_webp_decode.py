from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
import zipfile

from PIL import Image, ImageOps
from PySide6.QtCore import QByteArray, QBuffer, QCoreApplication, QIODevice
from PySide6.QtGui import QImage, QImageReader

from app.thumbnail_render import pil_to_qimage


CASES = (
    ("横 RGB", (1920, 1080), "RGB"),
    ("縦 RGB", (1080, 1920), "RGB"),
    ("大型横 RGB", (3840, 2160), "RGB"),
    ("大型縦 RGBA", (2160, 3840), "RGBA"),
)


def _sample_image(size: tuple[int, int], mode: str) -> Image.Image:
    width, height = size
    horizontal = Image.linear_gradient("L").resize(size)
    vertical = Image.linear_gradient("L").rotate(90, expand=True).resize(size)
    blue = Image.new("L", size, 128)
    image = Image.merge("RGB", (horizontal, vertical, blue))
    if mode == "RGBA":
        alpha = Image.linear_gradient("L").resize(size)
        image.putalpha(alpha)
    return image


def _timed(operation, repeats: int) -> float:
    values: list[float] = []
    for _index in range(max(1, repeats)):
        started = perf_counter()
        result = operation()
        values.append((perf_counter() - started) * 1000)
        if hasattr(result, "close"):
            result.close()
    return median(values)


def _legacy_decode(path: Path) -> QImage:
    with Image.open(path) as image:
        prepared = ImageOps.exif_transpose(image).convert("RGBA")
        rgba = prepared.convert("RGBA")
        data = rgba.tobytes("raw", "RGBA")
        return QImage(
            data,
            rgba.width,
            rgba.height,
            QImage.Format.Format_RGBA8888,
        ).copy()


def _pillow_decode(path: Path) -> QImage:
    with Image.open(path) as image:
        image.seek(0)
        prepared = ImageOps.exif_transpose(image).copy()
    return pil_to_qimage(prepared)


def _qt_decode(path: Path) -> QImage:
    reader = QImageReader(str(path), b"webp")
    reader.setAutoTransform(True)
    return reader.read()


def _qt_bytes_decode(data: bytes) -> QImage:
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"webp")
    reader.setAutoTransform(True)
    image = reader.read()
    buffer.close()
    return image


def run_benchmark(repeats: int = 3) -> dict[str, object]:
    QCoreApplication.instance() or QCoreApplication([])
    results: list[dict[str, object]] = []
    with TemporaryDirectory(prefix="nivisviewer-webp-") as temporary:
        root = Path(temporary)
        for label, size, mode in CASES:
            path = root / f"日本語 {label}.webp"
            with _sample_image(size, mode) as image:
                image.save(path, "WEBP", quality=82, method=4, exact=True)
            with Image.open(path) as header:
                header_started = perf_counter()
                header_size = header.size
                orientation = header.getexif().get(274, 1)
                header_ms = (perf_counter() - header_started) * 1000
            data = path.read_bytes()
            results.append(
                {
                    "case": label,
                    "size": list(size),
                    "mode": mode,
                    "header_ms": round(header_ms, 3),
                    "header_size": list(header_size),
                    "orientation": orientation,
                    "legacy_ms": round(
                        _timed(lambda value=path: _legacy_decode(value), repeats),
                        3,
                    ),
                    "pillow_ms": round(
                        _timed(lambda value=path: _pillow_decode(value), repeats),
                        3,
                    ),
                    "qimagereader_ms": round(
                        _timed(lambda value=path: _qt_decode(value), repeats),
                        3,
                    ),
                    "qbuffer_ms": round(
                        _timed(lambda value=data: _qt_bytes_decode(value), repeats),
                        3,
                    ),
                }
            )

        zip_path = root / "日本語 WebP.zip"
        first_path = next(root.glob("*.webp"))
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(first_path, "画像/日本語.webp")
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive_bytes = archive.read("画像/日本語.webp")
        zip_qbuffer_ms = _timed(
            lambda: _qt_bytes_decode(archive_bytes),
            repeats,
        )
    return {
        "repeats": repeats,
        "cases": results,
        "zip_qbuffer_ms": round(zip_qbuffer_ms, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    print(
        json.dumps(
            run_benchmark(max(1, args.repeats)),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

