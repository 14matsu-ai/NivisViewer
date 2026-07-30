from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ICON_SOURCE = (
    ROOT / "assets" / "branding" / "nivisviewer_logo_source.png"
)
DEFAULT_FULL_LOGO_SOURCE = (
    ROOT / "assets" / "branding" / "nivisviewer_full_logo_source.png"
)
DEFAULT_OUTPUT_DIR = ROOT / "assets" / "icons"

EXPECTED_SOURCE_SIZE = (1536, 1024)
ICON_SOURCE_BOX = (460, 170, 1080, 790)
FULL_LOGO_SYMBOL_BOX = (165, 330, 470, 615)
FULL_LOGO_TEXT_BOX = (480, 385, 1325, 575)
ICON_OUTPUT_SIZE = 512
ICON_SOURCE_PADDING = 24
FULL_LOGO_PADDING = 24
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


@dataclass(frozen=True)
class MaskRecipe:
    blur_radius: float
    max_channel_step: int
    alpha_blur_radius: float = 0.55


ICON_MASK = MaskRecipe(blur_radius=1.2, max_channel_step=3)
FULL_LOGO_MASK = MaskRecipe(blur_radius=0.8, max_channel_step=4)


def _edge_flood_alpha(image: Image.Image, recipe: MaskRecipe) -> Image.Image:
    """Remove only the smooth background connected to the crop boundary."""

    smoothed = image.convert("RGB").filter(
        ImageFilter.GaussianBlur(recipe.blur_radius)
    )
    width, height = smoothed.size
    pixels = smoothed.load()
    outside = bytearray(width * height)
    pending: deque[tuple[int, int]] = deque()

    def seed(x: int, y: int) -> None:
        index = y * width + x
        if outside[index]:
            return
        outside[index] = 1
        pending.append((x, y))

    for x in range(width):
        seed(x, 0)
        seed(x, height - 1)
    for y in range(height):
        seed(0, y)
        seed(width - 1, y)

    while pending:
        x, y = pending.popleft()
        current = pixels[x, y]
        for next_x, next_y in (
            (x - 1, y),
            (x + 1, y),
            (x, y - 1),
            (x, y + 1),
        ):
            if not (0 <= next_x < width and 0 <= next_y < height):
                continue
            index = next_y * width + next_x
            if outside[index]:
                continue
            candidate = pixels[next_x, next_y]
            if max(
                abs(current[channel] - candidate[channel])
                for channel in range(3)
            ) > recipe.max_channel_step:
                continue
            outside[index] = 1
            pending.append((next_x, next_y))

    alpha = Image.new("L", (width, height))
    alpha.putdata([0 if value else 255 for value in outside])
    return alpha.filter(ImageFilter.GaussianBlur(recipe.alpha_blur_radius))


def _extract_region(
    source: Image.Image,
    box: tuple[int, int, int, int],
    recipe: MaskRecipe,
) -> Image.Image:
    region = source.convert("RGB").crop(box)
    result = region.convert("RGBA")
    result.putalpha(_edge_flood_alpha(region, recipe))
    return result


def _trim_with_padding(image: Image.Image, padding: int) -> Image.Image:
    alpha_box = image.getchannel("A").getbbox()
    if alpha_box is None:
        raise ValueError("Foreground extraction produced an empty image.")
    trimmed = image.crop(alpha_box)
    output = Image.new(
        "RGBA",
        (trimmed.width + padding * 2, trimmed.height + padding * 2),
        (0, 0, 0, 0),
    )
    output.alpha_composite(trimmed, (padding, padding))
    return output


def create_icon(source: Image.Image) -> Image.Image:
    extracted = _extract_region(source, ICON_SOURCE_BOX, ICON_MASK)
    content = _trim_with_padding(extracted, 0)
    side = max(content.size) + ICON_SOURCE_PADDING * 2
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(
        content,
        ((side - content.width) // 2, (side - content.height) // 2),
    )
    return square.resize(
        (ICON_OUTPUT_SIZE, ICON_OUTPUT_SIZE),
        Image.Resampling.LANCZOS,
    )


def create_full_logo(source: Image.Image) -> Image.Image:
    symbol = _extract_region(source, FULL_LOGO_SYMBOL_BOX, FULL_LOGO_MASK)
    text = _extract_region(source, FULL_LOGO_TEXT_BOX, FULL_LOGO_MASK)
    composite = Image.new("RGBA", source.size, (0, 0, 0, 0))
    composite.alpha_composite(
        symbol,
        (FULL_LOGO_SYMBOL_BOX[0], FULL_LOGO_SYMBOL_BOX[1]),
    )
    composite.alpha_composite(
        text,
        (FULL_LOGO_TEXT_BOX[0], FULL_LOGO_TEXT_BOX[1]),
    )
    return _trim_with_padding(composite, FULL_LOGO_PADDING)


def generate(
    icon_source_path: Path,
    full_logo_source_path: Path,
    output_dir: Path,
) -> tuple[Path, Path, Path]:
    with Image.open(icon_source_path) as source:
        if source.size != EXPECTED_SOURCE_SIZE:
            raise ValueError(
                f"Unexpected icon source size: {source.size}; "
                f"expected {EXPECTED_SOURCE_SIZE}"
            )
        icon = create_icon(source)

    with Image.open(full_logo_source_path) as source:
        if source.size != EXPECTED_SOURCE_SIZE:
            raise ValueError(
                f"Unexpected full-logo source size: {source.size}; "
                f"expected {EXPECTED_SOURCE_SIZE}"
            )
        full_logo = create_full_logo(source)

    output_dir.mkdir(parents=True, exist_ok=True)
    full_logo_path = output_dir / "nivisviewer_logo.png"
    icon_path = output_dir / "nivisviewer_icon.png"
    ico_path = output_dir / "nivisviewer.ico"

    full_logo.save(full_logo_path, format="PNG", optimize=True)
    icon.save(icon_path, format="PNG", optimize=True)
    icon.save(
        ico_path,
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
    )
    return full_logo_path, icon_path, ico_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate transparent NivisViewer branding assets."
    )
    parser.add_argument(
        "--icon-source",
        type=Path,
        default=DEFAULT_ICON_SOURCE,
    )
    parser.add_argument(
        "--full-logo-source",
        type=Path,
        default=DEFAULT_FULL_LOGO_SOURCE,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    arguments = parser.parse_args()
    for path in generate(
        arguments.icon_source,
        arguments.full_logo_source,
        arguments.output_dir,
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
