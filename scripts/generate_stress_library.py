from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from PIL import Image


def generate(
    output: Path,
    *,
    items: int,
    real_images: int,
    pdf_pages: int,
    force: bool,
) -> dict[str, int]:
    if output.exists() and any(output.iterdir()) and not force:
        raise FileExistsError("Output is not empty; use --force to add/replace generated names")
    output.mkdir(parents=True, exist_ok=True)
    items = max(0, int(items))
    real_images = max(0, min(int(real_images), items))
    estimated = real_images * 2048 + items * 128 + pdf_pages * 128
    print(f"Estimated generated size: about {estimated / 1024 / 1024:.1f} MiB")
    for index in range(items):
        directory = output / f"series-{index // 1000:02d}"
        directory.mkdir(exist_ok=True)
        name = directory / f"日本語 book {index + 1:05d}.jpg"
        if name.exists() and not force:
            raise FileExistsError(name)
        if index < real_images:
            Image.new(
                "RGB",
                (32 + index % 5, 48),
                (index % 255, 80, 120),
            ).save(name, "JPEG")
        else:
            name.with_suffix(".txt").write_text("list item\n", encoding="utf-8")
    sample = output / "sample.cbz"
    with zipfile.ZipFile(sample, "w") as archive:
        for path in sorted(output.rglob("*.jpg"))[:20]:
            archive.write(path, Path("日本語 pages") / path.name)
    pdf = output / "stress.pdf"
    if pdf_pages > 0:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument.new()
        try:
            for index in range(pdf_pages):
                size = (792, 612) if index % 10 == 0 else (612, 792)
                page = document.new_page(*size)
                page.close()
            document.save(pdf)
        finally:
            document.close()
    (output / "broken-image.jpg").write_bytes(b"not an image")
    (output / "unsupported.bin").write_bytes(b"\0")
    result = {
        "items": items,
        "real_images": real_images,
        "pdf_pages": max(0, pdf_pages),
    }
    (output / "generation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--items", type=int, default=10_000)
    parser.add_argument("--real-images", type=int, default=500)
    parser.add_argument("--pdf-pages", type=int, default=500)
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    generate(
        arguments.output,
        items=arguments.items,
        real_images=arguments.real_images,
        pdf_pages=arguments.pdf_pages,
        force=arguments.force,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
