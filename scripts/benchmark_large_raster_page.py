"""Cold per-page worker cost, using real Folder/ZIP sources and production jobs.

No window or native input. OS file cache may be warm; decoded source/frame
caches are empty for every sample. Fixture construction is outside timings.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtWidgets import QApplication
from app.image_source import FolderImageSource, ZipImageSource
from app.zip_raster_book_runtime import (
    ZipRasterDisplayUnit, ZipRasterPage, ZipRasterRenderSpec,
    _UnitKey, _ZipRasterUnitJob,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=3200)
    parser.add_argument("--height", type=int, default=5000)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    rows = []
    with TemporaryDirectory(prefix="nivis-large-page-") as temp:
        root = Path(temp)
        # Repeat a deterministic detailed tile to avoid timing a solid image.
        archive = args.archive
        if archive is None:
            rng = random.Random(17)
            tile = Image.frombytes("RGB", (256, 256), rng.randbytes(256 * 256 * 3))
            with Image.new("RGB", (args.width, args.height)) as fixture:
                for y in range(0, args.height, 256):
                    for x in range(0, args.width, 256):
                        fixture.paste(tile, (x, y))
                for suffix in ("jpg", "png", "webp"):
                    path = root / f"日本語.{suffix}"
                    fixture.save(path, quality=92)
            tile.close()
            archive = root / "日本語.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for path in root.iterdir():
                    if path.suffix != ".zip":
                        output.write(path, path.name)
        with ThreadPoolExecutor(max_workers=1) as worker:
            for kind in (("zip",) if args.archive else ("folder", "zip")):
                source = FolderImageSource(root) if kind == "folder" else ZipImageSource(archive)
                unpack_ms = []
                if isinstance(source, ZipImageSource):
                    original_read = source._read_entry_qbytearray
                    def read_entry(*args, **kwargs):
                        start = perf_counter()
                        result = original_read(*args, **kwargs)
                        unpack_ms.append((perf_counter() - start) * 1000)
                        return result
                    source._read_entry_qbytearray = read_entry
                try:
                    for page_index, image_id in enumerate(source.list_images()[:args.pages]):
                        size = worker.submit(source.probe_image_size, image_id).result(timeout=30)
                        assert size
                        for viewport in ((1200, 800), (2560, 1440)):
                            samples = []
                            for index in range(args.samples):
                                unpack_ms.clear()
                                page = ZipRasterPage(0, image_id, size)
                                unit = ZipRasterDisplayUnit(0, (page,), True)
                                spec = ZipRasterRenderSpec(viewport, decoder_maximum_size=viewport, decoder_layout_sized=True)
                                job = _ZipRasterUnitJob(serial=index, request_id=index, source=source, unit=unit,
                                    key=_UnitKey(1, id(source), unit.identity, True, spec))
                                original_decode = job._decode_page
                                decode_ms = []
                                decoded_sizes = []
                                def decode(page):
                                    start = perf_counter()
                                    result = original_decode(page)
                                    decode_ms.append((perf_counter() - start) * 1000)
                                    decoded_sizes.append((result.qimage.width(), result.qimage.height()) if result.qimage is not None else None)
                                    return result
                                job._decode_page = decode
                                def run():
                                    start = perf_counter()
                                    pages = job._render_unit()
                                    elapsed = (perf_counter() - start) * 1000
                                    assert pages and all(not p.error and p.display_qimage is not None for p in pages)
                                    return elapsed
                                total = worker.submit(run).result(timeout=30)
                                samples.append({"decode_ms": sum(decode_ms),
                                    "zip_jpeg_read_ms": sum(unpack_ms),
                                    "decode_excluding_zip_read_ms": sum(decode_ms) - sum(unpack_ms),
                                    "render_ms": total - sum(decode_ms), "total_ms": total})
                            rows.append({"kind": kind, "page_index": page_index, "size": size, "format": Path(image_id).suffix, "viewport": viewport,
                                "decoded_size": decoded_sizes[-1],
                                **{key: round(statistics.median(row[key] for row in samples), 2) for key in samples[0]}})
                finally:
                    source.close()
    args.output.write_text(json.dumps({"size": [args.width, args.height], "rows": rows}, indent=2), encoding="utf-8")
    print(json.dumps(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
