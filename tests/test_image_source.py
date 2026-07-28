from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.image_source import (
    FolderImageSource,
    FolderListingSnapshot,
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


def test_folder_snapshot_file_size_does_not_restat_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "1.jpg"
    selected = tmp_path / "2.jpg"
    write_image(first)
    write_image(selected)
    first_size = first.stat().st_size
    selected_size = selected.stat().st_size
    snapshot = FolderListingSnapshot(
        tmp_path,
        (str(first), str(selected)),
        str(selected),
        (
            (str(first), first_size, first.stat().st_mtime_ns),
            (str(selected), selected_size, selected.stat().st_mtime_ns),
        ),
    )
    source, selected_image = create_image_source(
        selected,
        folder_snapshot=snapshot,
    )

    def unexpected_stat(*_args, **_kwargs):
        raise AssertionError("snapshot file size must not be restated")

    monkeypatch.setattr(Path, "stat", unexpected_stat)

    assert selected_image == str(selected)
    assert source.file_size(str(first)) == first_size
    assert source.file_size(str(selected)) == selected_size
    source.close()


def test_folder_loaded_bytes_supply_file_size_without_stat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "page.jpg"
    write_image(image_path)
    expected_size = image_path.stat().st_size
    source = FolderImageSource(
        tmp_path,
        image_snapshot=(str(image_path),),
    )
    assert source.file_size(str(image_path)) is None

    def unexpected_stat(*_args, **_kwargs):
        raise AssertionError("loaded byte count must not require stat")

    monkeypatch.setattr(Path, "stat", unexpected_stat)
    with source.open_image(str(image_path)):
        pass

    assert source.file_size(str(image_path)) == expected_size
    source.close()


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
