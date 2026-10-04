from __future__ import annotations

import io
from pathlib import Path
import sqlite3
import struct
import zipfile

from PIL import Image
import pytest
pytestmark = pytest.mark.usefixtures("enable_xcf")

from app import creative_image_decoder as decoder
from app.creative_image_decoder import (
    CLIP_DATABASE_MAX_BYTES,
    CreativeImageError,
    CreativeImageUnsupportedError,
    decode_creative_image,
    decode_creative_thumbnail,
    probe_creative_image_size,
    probe_xcf_header,
)


def _png_bytes(
    size: tuple[int, int],
    color: str,
) -> bytes:
    output = io.BytesIO()
    with Image.new("RGB", size, color) as image:
        image.save(output, "PNG")
    return output.getvalue()


def _project_archive(suffix: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        if suffix == ".ora":
            archive.writestr("mimetype", b"image/openraster")
            archive.writestr(
                "Thumbnails/thumbnail.png",
                _png_bytes((96, 144), "blue"),
            )
        else:
            archive.writestr(
                "preview.png",
                _png_bytes((96, 144), "blue"),
            )
        archive.writestr(
            "mergedimage.png",
            _png_bytes((320, 480), "red"),
        )
    return output.getvalue()


def _chunk(tag: bytes, payload: bytes) -> bytes:
    assert len(tag) == 8
    return tag + len(payload).to_bytes(8, "big") + payload


def _clip_bytes(
    *,
    preview_size: tuple[int, int] = (222, 333),
    png_size: tuple[int, int] | None = None,
) -> bytes:
    png_size = preview_size if png_size is None else png_size
    preview = _png_bytes(png_size, "green")

    database = sqlite3.connect(":memory:")
    database.execute(
        "CREATE TABLE Project("
        "ProjectInternalVersion TEXT, ProjectCanvas INTEGER)"
    )
    database.execute("INSERT INTO Project VALUES('1.1.0', 7)")
    database.execute(
        "CREATE TABLE CanvasPreview("
        "MainId INTEGER, CanvasId INTEGER, ImageType INTEGER, "
        "ImageWidth INTEGER, ImageHeight INTEGER, ImageData BLOB)"
    )
    database.execute(
        "INSERT INTO CanvasPreview VALUES(1, 7, 1, ?1, ?2, ?3)",
        (preview_size[0], preview_size[1], preview),
    )
    payload = database.serialize()
    database.close()

    root = bytearray(b"CSFCHUNK" + b"\x00" * 16)
    identifier = b"\x42" * 16
    head_payload = struct.pack(">QQQ", 256, 0, len(identifier)) + identifier
    head = _chunk(b"CHNKHead", head_payload)
    database_offset = 24 + len(head)
    head_payload = (
        struct.pack(">QQQ", 256, database_offset, len(identifier))
        + identifier
    )
    head = _chunk(b"CHNKHead", head_payload)
    sql = _chunk(b"CHNKSQLi", payload)
    foot = _chunk(b"CHNKFoot", b"")

    result = root + head + sql + foot
    result[8:16] = len(result).to_bytes(8, "big")
    result[16:24] = (24).to_bytes(8, "big")
    return bytes(result)


def _xcf_header(
    version: int,
    *,
    size: tuple[int, int] = (640, 480),
) -> bytes:
    version_tag = b"file" if version == 0 else f"v{version:03d}".encode()
    return (
        b"gimp xcf "
        + version_tag
        + b"\x00"
        + size[0].to_bytes(4, "big")
        + size[1].to_bytes(4, "big")
        + b"\x00" * 32
    )


@pytest.mark.parametrize("suffix", [".kra", ".ora"])
def test_layered_zip_projects_use_preview_then_merged(
    suffix: str,
) -> None:
    payload = _project_archive(suffix)

    assert probe_creative_image_size(payload, suffix=suffix) == (320, 480)

    with decode_creative_thumbnail(
        payload,
        suffix=suffix,
        minimum_long_edge=90,
    ) as preview:
        assert preview.size == (96, 144)
        assert preview.getpixel((0, 0))[2] > preview.getpixel((0, 0))[0]

    with decode_creative_thumbnail(
        payload,
        suffix=suffix,
        minimum_long_edge=200,
    ) as merged:
        assert merged.size == (320, 480)
        assert merged.getpixel((0, 0))[0] > merged.getpixel((0, 0))[2]

    with decode_creative_image(payload, suffix=suffix) as full:
        assert full.size == (320, 480)


def test_openraster_rejects_wrong_mimetype() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", b"application/not-openraster")
        archive.writestr(
            "mergedimage.png",
            _png_bytes((32, 48), "red"),
        )

    with pytest.raises(CreativeImageError, match="mimetype"):
        decode_creative_image(output.getvalue(), suffix=".ora")


def test_clip_reads_primary_canvas_preview() -> None:
    payload = _clip_bytes()

    assert probe_creative_image_size(payload, suffix=".clip") == (222, 333)
    with decode_creative_image(payload, suffix=".clip") as image:
        assert image.size == (222, 333)
        assert image.info["_nivis_preview_only"] is True


def test_clip_rejects_png_metadata_mismatch() -> None:
    payload = _clip_bytes(
        preview_size=(222, 333),
        png_size=(111, 222),
    )

    with pytest.raises(CreativeImageError, match="dimensions"):
        decode_creative_image(payload, suffix=".clip")


def test_clip_rejects_declared_file_size_mismatch() -> None:
    payload = bytearray(_clip_bytes())
    payload[15] ^= 1

    with pytest.raises(CreativeImageError, match="declared size"):
        decode_creative_image(bytes(payload), suffix=".clip")


def test_clip_rejects_oversized_database_before_payload_read() -> None:
    payload = bytearray(_clip_bytes())
    database_offset = int.from_bytes(payload[48:56], "big")
    payload[
        database_offset + 8:database_offset + 16
    ] = (CLIP_DATABASE_MAX_BYTES + 1).to_bytes(8, "big")

    with pytest.raises(CreativeImageError, match="database exceeds"):
        decode_creative_image(bytes(payload), suffix=".clip")


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (0, (0, (640, 480))),
        (13, (13, (640, 480))),
        (14, (14, (640, 480))),
        (26, (26, (640, 480))),
    ],
)
def test_xcf_header_probe(version: int, expected) -> None:
    assert probe_xcf_header(_xcf_header(version)) == expected


def test_xcf_modern_version_uses_gimp_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    def fake_gimp(_source):
        calls.append(True)
        return Image.new("RGBA", (640, 480), (2, 3, 4, 255))

    monkeypatch.setattr(
        decoder,
        "render_xcf_with_gimp",
        fake_gimp,
    )

    with decode_creative_image(
        _xcf_header(26),
        suffix=".xcf",
    ) as image:
        assert image.size == (640, 480)
    assert calls == [True]


def test_xcf_modern_version_requires_gimp_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.gimp_xcf_backend import GimpXcfBackendUnavailable

    def unavailable(_source):
        raise GimpXcfBackendUnavailable("missing")

    monkeypatch.setattr(
        decoder,
        "render_xcf_with_gimp",
        unavailable,
    )

    with pytest.raises(
        CreativeImageUnsupportedError,
        match="requires an installed GIMP 3.x",
    ):
        decode_creative_image(
            _xcf_header(14),
            suffix=".xcf",
        )


def test_xcf_legacy_decoder_can_fall_back_to_gimp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_native(_source):
        raise RuntimeError("native decoder failed")

    def fake_gimp(_source):
        return Image.new("RGBA", (640, 480), (5, 6, 7, 255))

    monkeypatch.setattr(
        decoder,
        "_decode_xcf_native",
        broken_native,
    )
    monkeypatch.setattr(
        decoder,
        "render_xcf_with_gimp",
        fake_gimp,
    )

    with decode_creative_image(
        _xcf_header(13),
        suffix=".xcf",
    ) as image:
        assert image.size == (640, 480)


def test_xcf_supported_version_uses_bounded_decoder_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_decode(_source):
        return Image.new("RGBA", (640, 480), (1, 2, 3, 255))

    monkeypatch.setattr(
        decoder,
        "_decode_xcf_native",
        fake_decode,
    )
    with decode_creative_image(
        _xcf_header(13),
        suffix=".xcf",
    ) as image:
        assert image.size == (640, 480)


def test_probe_rejects_unknown_project_extension() -> None:
    assert probe_creative_image_size(b"data", suffix=".unknown") is None


@pytest.mark.parametrize("suffix", [".kra", ".ora"])
@pytest.mark.parametrize("preview", [b"broken", _png_bytes((100, 100), "blue")[:40]])
def test_corrupt_optional_preview_falls_back_to_composite(suffix, preview):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        if suffix == ".ora":
            archive.writestr("mimetype", b"image/openraster")
        archive.writestr(decoder._zip_project_members(suffix)[1], preview)
        archive.writestr("mergedimage.png", _png_bytes((32, 48), "red"))
    with decode_creative_thumbnail(output.getvalue(), suffix=suffix) as image:
        assert image.size == (32, 48)
        assert image.getpixel((0, 0)) == (255, 0, 0)


def _tiny_legacy_xcf():
    # A one-pixel, one-layer uncompressed XCF v0, with absolute pointers.
    def ints(*values):
        return struct.pack(">" + "I" * len(values), *values)
    header = b"gimp xcf file\0" + ints(1, 1, 0) + ints(0, 0)
    layer_offset = len(header) + 12
    layer = ints(1, 1, 0) + ints(2) + b"L\0"
    layer += ints(6, 4, 255) + ints(8, 4, 1) + ints(7, 4, 0) + ints(0, 0)
    hierarchy_offset = layer_offset + len(layer) + 8
    level_offset = hierarchy_offset + 20
    tile_offset = level_offset + 16
    return (header + ints(layer_offset, 0, 0) + layer
            + ints(hierarchy_offset, 0) + ints(1, 1, 3, level_offset, 0)
            + ints(1, 1, tile_offset, 0) + bytes((220, 30, 40)))


@pytest.mark.parametrize("from_file", [False, True])
def test_real_legacy_xcf_decoder_without_external_backend(tmp_path, monkeypatch, from_file):
    def forbidden(_source):
        pytest.fail("Valid legacy XCF must not invoke GIMP")
    monkeypatch.setattr(decoder, "render_xcf_with_gimp", forbidden)
    payload = _tiny_legacy_xcf()
    path = tmp_path / "日本語.xcf"
    path.write_bytes(payload)
    with decode_creative_image(path if from_file else payload, suffix=".xcf") as image:
        assert image.size == (1, 1)
        assert image.convert("RGB").getpixel((0, 0)) == (220, 30, 40)
    assert path.read_bytes() == payload


@pytest.mark.parametrize("suffix", [".kra", ".ora", ".clip"])
def test_projects_inside_zip_decode_and_publish_geometry(tmp_path, suffix):
    from app.image_source import ZipImageSource
    payload = _clip_bytes() if suffix == ".clip" else _project_archive(suffix)
    path = tmp_path / "日本語.zip"
    name = "作品" + suffix
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, payload)
    source = ZipImageSource(path)
    try:
        assert source.list_images() == [name]
        assert source.probe_image_size(name) is None
        with source.open_image(name) as image:
            expected = (222, 333) if suffix == ".clip" else (320, 480)
            assert image.size == expected
        # Geometry is returned with the decoded raster, without another nested
        # ZIP/SQLite probe (the caller publishes it to the page model).
    finally:
        source.close()


@pytest.mark.parametrize("suffix", [".kra", ".ora", ".clip", ".xcf"])
def test_external_archive_project_paths_use_decoder_without_external_apps(tmp_path, suffix):
    from app.image_source import SevenZipImageSource
    from test_external_archive_image_source import FakeBackend, archive_entry
    payload = (_clip_bytes() if suffix == ".clip" else _tiny_legacy_xcf()
               if suffix == ".xcf" else _project_archive(suffix))
    path = tmp_path / "作品.cb7"
    path.write_bytes(b"fake external archive")
    name = "作品" + suffix
    backend = FakeBackend((archive_entry(name, len(payload)),), data=payload)
    source = SevenZipImageSource(path, backend=backend)
    expected = ((222, 333) if suffix == ".clip" else (1, 1)
                if suffix == ".xcf" else (320, 480))
    try:
        assert source.list_images() == [name]
        assert source.probe_image_size(name) == expected
        with source.open_image(name) as image:
            assert image.size == expected
        with source.open_thumbnail_image(name, minimum_long_edge=90) as thumbnail:
            assert thumbnail.size == ((96, 144) if suffix in (".kra", ".ora") else expected)
    finally:
        source.close()
