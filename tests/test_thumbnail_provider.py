from __future__ import annotations

import zipfile
from pathlib import Path

from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.browser_model import BrowserItem, BrowserItemKind
from app.thumbnail_provider import BrowserThumbnailProvider


def write_image(path: Path, *, color: str = "white") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (24, 32), color) as image:
        image.save(path)


def make_item(path: Path, kind: BrowserItemKind) -> BrowserItem:
    return BrowserItem(path.name, path, kind, path.stat().st_mtime)


def test_loads_image_folder_and_archive_thumbnails_with_unicode_paths(
    tmp_path: Path,
) -> None:
    image = tmp_path / "日本語画像.jpg"
    write_image(image)
    folder = tmp_path / "日本語フォルダ"
    write_image(folder / "2.jpg", color="blue")
    write_image(folder / "1.jpg", color="red")
    archive = tmp_path / "日本語書庫.cbz"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(folder / "2.jpg", "10.jpg")
        output.write(folder / "1.jpg", "2.jpg")

    image_thumbnail = BrowserThumbnailProvider.load_thumbnail(
        make_item(image, BrowserItemKind.IMAGE), 120
    )
    folder_thumbnail = BrowserThumbnailProvider.load_thumbnail(
        make_item(folder, BrowserItemKind.FOLDER), 120
    )
    archive_thumbnail = BrowserThumbnailProvider.load_thumbnail(
        make_item(archive, BrowserItemKind.ARCHIVE), 120
    )

    assert image_thumbnail is not None and not image_thumbnail.isNull()
    assert folder_thumbnail is not None and not folder_thumbnail.isNull()
    assert archive_thumbnail is not None and not archive_thumbnail.isNull()
    assert image_thumbnail.width() <= 120
    assert folder_thumbnail.height() <= 120
    assert folder_thumbnail.pixelColor(0, 0).red() > folder_thumbnail.pixelColor(0, 0).blue()
    assert archive_thumbnail.pixelColor(0, 0).red() > archive_thumbnail.pixelColor(0, 0).blue()


def test_corrupt_image_and_archive_fall_back_to_none(tmp_path: Path) -> None:
    image = tmp_path / "broken.jpg"
    image.write_bytes(b"not an image")
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip")

    assert (
        BrowserThumbnailProvider.load_thumbnail(
            make_item(image, BrowserItemKind.IMAGE), 100
        )
        is None
    )
    assert (
        BrowserThumbnailProvider.load_thumbnail(
            make_item(archive, BrowserItemKind.ARCHIVE), 100
        )
        is None
    )


def test_duplicate_requests_are_suppressed_and_old_generation_is_discarded(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image_path = tmp_path / "page.jpg"
    write_image(image_path)
    item = make_item(image_path, BrowserItemKind.IMAGE)
    delivered: list[tuple[str, int]] = []

    def loader(_item: BrowserItem, _size: int) -> QImage:
        return QImage(8, 8, QImage.Format.Format_RGBA8888)

    provider = BrowserThumbnailProvider(loader=loader, max_workers=1)
    provider.thumbnail_ready.connect(
        lambda path, generation, _image: delivered.append((path, generation))
    )
    first_generation = provider.begin_generation()

    assert provider.request(item, 100, generation=first_generation)
    assert not provider.request(item, 100, generation=first_generation)
    provider.begin_generation()
    assert provider.wait_for_done(2000)
    qapp.processEvents()

    assert delivered == []
    provider.close()
