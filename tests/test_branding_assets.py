from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops

from scripts.generate_branding_assets import ICO_SIZES, generate


ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "assets" / "icons"
BRANDING = ROOT / "assets" / "branding"


def test_transparent_branding_pngs_have_expected_geometry():
    with Image.open(ICONS / "nivisviewer_icon.png") as icon:
        assert icon.mode == "RGBA"
        assert icon.size == (512, 512)
        assert icon.getchannel("A").getextrema() == (0, 255)
        assert icon.getchannel("A").getbbox() not in (None, (0, 0, 512, 512))

    with Image.open(ICONS / "nivisviewer_logo.png") as logo:
        assert logo.mode == "RGBA"
        assert logo.width > logo.height * 3
        assert logo.getchannel("A").getextrema() == (0, 255)
        assert logo.getchannel("A").getbbox() not in (
            None,
            (0, 0, logo.width, logo.height),
        )


def test_windows_icon_contains_all_required_sizes():
    with Image.open(ICONS / "nivisviewer.ico") as icon:
        assert icon.format == "ICO"
        assert set(ICO_SIZES).issubset(
            {width for width, height in icon.ico.sizes() if width == height}
        )


def test_checked_in_assets_match_generator(tmp_path):
    generated_logo, generated_icon, generated_ico = generate(
        BRANDING / "nivisviewer_logo_source.png",
        BRANDING / "nivisviewer_full_logo_source.png",
        tmp_path,
    )
    for generated, checked_in in (
        (generated_logo, ICONS / "nivisviewer_logo.png"),
        (generated_icon, ICONS / "nivisviewer_icon.png"),
    ):
        with Image.open(generated) as actual, Image.open(checked_in) as expected:
            assert actual.mode == expected.mode
            assert actual.size == expected.size
            assert ImageChops.difference(actual, expected).getbbox() is None

    with (
        Image.open(generated_ico) as actual_ico,
        Image.open(ICONS / "nivisviewer.ico") as expected_ico,
    ):
        assert actual_ico.ico.sizes() == expected_ico.ico.sizes()
        for size in actual_ico.ico.sizes():
            actual = actual_ico.ico.getimage(size).convert("RGBA")
            expected = expected_ico.ico.getimage(size).convert("RGBA")
            assert ImageChops.difference(actual, expected).getbbox() is None
