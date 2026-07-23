from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.image_source import (
    FolderImageSource,
    ImageSourceError,
    ZipImageSource,
    create_image_source,
)


def write_image(path: Path, *, size: tuple[int, int] = (8, 12)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, "white") as image:
        image.save(path)


def test_folder_lists_only_supported_images_in_natural_order(tmp_path: Path) -> None:
    for name in ("10.jpg", "2.jpg", "1.jpg", "cover.png"):
        write_image(tmp_path / name)
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")

    source = FolderImageSource(tmp_path)

    assert [Path(image_id).name for image_id in source.list_images()] == [
        "1.jpg",
        "2.jpg",
        "10.jpg",
        "cover.png",
    ]


def test_single_image_opens_parent_folder_and_returns_selection(tmp_path: Path) -> None:
    selected = tmp_path / "日本語2.jpg"
    write_image(tmp_path / "日本語1.jpg")
    write_image(selected)

    source, selected_image = create_image_source(selected)

    assert isinstance(source, FolderImageSource)
    assert source.source_path == tmp_path
    assert selected_image == str(selected)
    assert selected_image in source.list_images()


def test_zip_lists_images_in_subfolders_and_ignores_other_files(tmp_path: Path) -> None:
    image1 = tmp_path / "1.jpg"
    image2 = tmp_path / "10.jpg"
    write_image(image1)
    write_image(image2)
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(image2, "chapter/10.jpg")
        output.writestr("chapter/readme.txt", "ignore")
        output.write(image1, "chapter/2/1.jpg")

    source = ZipImageSource(archive)
    try:
        assert source.list_images() == ["chapter/2/1.jpg", "chapter/10.jpg"]
        with source.open_image("chapter/2/1.jpg") as image:
            assert image.size == (8, 12)
    finally:
        source.close()


def test_corrupt_zip_raises_image_source_error(tmp_path: Path) -> None:
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"this is not a zip file")

    with pytest.raises(ImageSourceError):
        ZipImageSource(archive)
