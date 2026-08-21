from __future__ import annotations

import hashlib
import os
from pathlib import Path

from PySide6.QtCore import QRect, QPoint
from PySide6.QtGui import QImage

from app.browser_item_delegate import BrowserItemDelegate
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_sort import BrowserSortKey, BrowserSortOrder
from app.rating_rename_service import RatingRenameService
from app.zippla_filename_metadata import ZipPlaFilenameMetadata


def test_zippla_rating_grammar_and_other_metadata_round_trip() -> None:
    path = Path("漫画 第1巻 {zpi$c=12.5;b=l;r=3;t=細線,日本語;d=r}.cover.jpg")

    parsed = ZipPlaFilenameMetadata.parse(path)

    assert parsed.matched is True
    assert parsed.rating == 3
    assert parsed.cover == (12, 0.5)
    assert parsed.binding == "l"
    assert parsed.tags == ("細線", "日本語")
    assert parsed.legacy_direction == "r"
    assert parsed.display_name == "漫画 第1巻.cover.jpg"
    assert parsed.with_rating(5).serialized_path().name == (
        "漫画 第1巻.cover {zpi$c=12.5;b=l;r=5;t=細線,日本語;d=r}.jpg"
    )
    assert parsed.with_rating(None).serialized_path().name == (
        "漫画 第1巻.cover {zpi$c=12.5;b=l;t=細線,日本語;d=r}.jpg"
    )


def test_malformed_rating_is_an_ordinary_filename() -> None:
    for name in (
        "normal {zpi$r=0}.jpg",
        "normal {zpi$r=6}.jpg",
        "normal {zpi$unknown=3}.jpg",
        "normal {zpi$r=3.jpg",
    ):
        parsed = ZipPlaFilenameMetadata.parse(name)
        assert parsed.matched is False
        assert parsed.rating is None
        assert parsed.display_name == name


def test_rating_rename_preserves_timestamp_and_content(tmp_path: Path) -> None:
    source = tmp_path / "日本語.multi.part.jpg"
    payload = b"unchanged image payload"
    source.write_bytes(payload)
    initial_mtime_ns = 1_234_567_890_123_456_700
    os.utime(source, ns=(initial_mtime_ns, initial_mtime_ns))
    digest = hashlib.sha256(payload).hexdigest()
    service = RatingRenameService()

    current = source
    for rating in (3, 5, 1, None):
        result = service.set_rating(current, rating)
        assert result.success is True
        current = result.destination_path
        assert current.stat().st_mtime_ns == initial_mtime_ns
        assert hashlib.sha256(current.read_bytes()).hexdigest() == digest
        assert ZipPlaFilenameMetadata.parse(current).rating == rating

    assert current.name == source.name


def test_rating_rename_does_not_overwrite_collision(tmp_path: Path) -> None:
    source = tmp_path / "page.jpg"
    collision = tmp_path / "page {zpi$r=3}.jpg"
    source.write_bytes(b"source")
    collision.write_bytes(b"collision")

    result = RatingRenameService().set_rating(source, 3)

    assert result.success is False
    assert source.read_bytes() == b"source"
    assert collision.read_bytes() == b"collision"


def _item(path: Path, rating: int | None) -> BrowserItem:
    return BrowserItem(
        display_name=path.name,
        path=path,
        kind=BrowserItemKind.IMAGE,
        modified_at=1.0,
        file_size=10,
        modified_time_ns=1_000_000_000,
        extension=path.suffix,
        rating=rating,
    )


def test_rating_sort_keeps_unrated_last_in_both_directions(tmp_path: Path) -> None:
    items = [
        _item(tmp_path / "none.jpg", None),
        _item(tmp_path / "one.jpg", 1),
        _item(tmp_path / "five.jpg", 5),
        _item(tmp_path / "three.jpg", 3),
    ]
    model = BrowserItemModel()
    model.set_items(items)

    model.configure_sort(BrowserSortKey.RATING, BrowserSortOrder.ASCENDING, False)
    assert [item.rating for item in model.items] == [1, 3, 5, None]

    model.configure_sort(BrowserSortKey.RATING, BrowserSortOrder.DESCENDING, False)
    assert [item.rating for item in model.items] == [5, 3, 1, None]


def test_rating_model_relocation_retains_thumbnail_and_dimensions(tmp_path: Path) -> None:
    old = tmp_path / "page.jpg"
    new = tmp_path / "page {zpi$r=4}.jpg"
    item = _item(old, None)
    model = BrowserItemModel()
    model.set_items([item])
    thumbnail = QImage(24, 16, QImage.Format.Format_ARGB32)
    thumbnail.fill(0xFF336699)
    model.set_thumbnail_image(old, thumbnail)
    model.set_image_dimensions(old, (4000, 3000))
    old_cache_key = model.data(model.index(0, 0), model.ThumbnailImageRole).cacheKey()

    assert model.apply_rating_renames(((old, new, 4),)) is True

    row = model.row_for_path(new)
    assert row >= 0
    index = model.index(row, 0)
    assert model.data(index, model.RatingRole) == 4
    assert model.data(index, model.ThumbnailImageRole).cacheKey() == old_cache_key
    assert model.image_dimensions(new) == (4000, 3000)


def test_rating_hit_test_maps_thumbnail_overlay_to_five_stars(qapp) -> None:
    delegate = BrowserItemDelegate(thumbnail_size=180)
    cell = QRect(0, 0, 240, 280)
    overlay = delegate.rating_overlay_rect(cell)

    assert delegate.rating_at_position(cell, QPoint(overlay.left() + 4, overlay.center().y())) == 1
    assert delegate.rating_at_position(cell, QPoint(overlay.right() - 4, overlay.center().y())) == 5
    assert delegate.rating_at_position(cell, QPoint(overlay.right() + 10, overlay.center().y())) is None
