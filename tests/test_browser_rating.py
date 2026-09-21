from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, QPoint
from PySide6.QtGui import QImage

from app.browser_filter import BrowserFilterState, RatingFilterMode
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


@pytest.mark.parametrize(
    ("name", "rating", "expected_set", "expected_clear"),
    (
        ("Folder", None, "Folder {zpi$r=3}", "Folder"),
        ("Folder.Name", None, "Folder.Name {zpi$r=3}", "Folder.Name"),
        ("Folder {zpi$r=3}", 3, "Folder {zpi$r=3}", "Folder"),
        (
            "Folder {zpi$t=foo}",
            None,
            "Folder {zpi$r=3;t=foo}",
            "Folder {zpi$t=foo}",
        ),
        (
            "Folder {zpi$r=3;t=foo}",
            3,
            "Folder {zpi$r=3;t=foo}",
            "Folder {zpi$t=foo}",
        ),
    ),
)
def test_zippla_folder_grammar_preserves_dots_and_unrelated_metadata(
    name: str,
    rating: int | None,
    expected_set: str,
    expected_clear: str,
) -> None:
    parsed = ZipPlaFilenameMetadata.parse(name)

    assert parsed.rating == rating
    assert parsed.with_rating(3).serialized_path(is_directory=True).name == (
        expected_set
    )
    assert parsed.with_rating(None).serialized_path(is_directory=True).name == (
        expected_clear
    )


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


def test_folder_ratings_participate_in_sort_and_all_rating_filters(
    tmp_path: Path,
) -> None:
    rated = BrowserItem(
        "rated folder",
        tmp_path / "rated folder {zpi$r=4}",
        BrowserItemKind.FOLDER,
        1.0,
        rating=4,
    )
    unrated = BrowserItem(
        "unrated folder",
        tmp_path / "unrated folder",
        BrowserItemKind.FOLDER,
        1.0,
        rating=None,
    )
    model = BrowserItemModel()
    model.set_items([unrated, rated])

    model.configure_sort(BrowserSortKey.RATING, BrowserSortOrder.ASCENDING, False)
    assert [item.rating for item in model.items] == [4, None]
    assert model.configure_filter(
        BrowserFilterState(rating_mode=RatingFilterMode.AT_LEAST, rating_reference=3)
    )
    assert [item.path for item in model.items] == [rated.path]
    assert model.configure_filter(
        BrowserFilterState(rating_mode=RatingFilterMode.EQUAL, rating_reference=4)
    )
    assert [item.path for item in model.items] == [rated.path]
    assert model.configure_filter(
        BrowserFilterState(rating_mode=RatingFilterMode.UNRATED)
    )
    assert [item.path for item in model.items] == [unrated.path]


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


def test_single_tag_rename_keeps_model_and_thumbnail_without_reset(tmp_path: Path) -> None:
    old = tmp_path / "b.png"
    new = tmp_path / "b {zpi$t=Tagged}.png"
    model = BrowserItemModel()
    model.set_items([_item(tmp_path / name, None) for name in ("a.png", "b.png", "c.png")])
    image = QImage(8, 8, QImage.Format.Format_ARGB32)
    image.fill(0xFF336699)
    model.set_thumbnail_image(old, image)
    resets: list[None] = []
    changed: list[None] = []
    model.modelReset.connect(lambda: resets.append(None))
    model.dataChanged.connect(lambda *_: changed.append(None))

    assert model.apply_rating_renames(((old, new, None),))
    assert not resets and len(changed) == 1
    assert model.row_for_path(new) == 1
    assert model.row_for_path(old) == -1
    assert model.data(model.index(1, 0), model.ThumbnailImageRole).cacheKey() == image.cacheKey()


def test_tag_rename_that_changes_filter_membership_resets_model(tmp_path: Path) -> None:
    old = tmp_path / "b.png"
    new = tmp_path / "b {zpi$t=Tagged}.png"
    model = BrowserItemModel()
    model.set_items([_item(old, None)])
    model.configure_filter(BrowserFilterState.normalized(include_tags=("Tagged",)))
    resets: list[None] = []
    model.modelReset.connect(lambda: resets.append(None))

    assert model.apply_rating_renames(((old, new, None),))
    assert resets and model.row_for_path(new) == 0


def test_single_tag_rename_reorders_tied_names_and_updates_indices(tmp_path: Path) -> None:
    old = tmp_path / "book {zpi$t=A}.png"
    middle = tmp_path / "book {zpi$t=B}.png"
    new = tmp_path / "book {zpi$t=Z}.png"
    model = BrowserItemModel()
    model.set_items([
        replace(_item(old, None), display_name="book.png"),
        replace(_item(middle, None), display_name="book.png"),
    ])

    assert model.apply_rating_renames(((old, new, None),))
    assert [item.path for item in model.source_items] == [middle, new]
    assert [item.path for item in model.items] == [middle, new]
    assert model.row_for_path(old) == -1
    assert model.row_for_path(middle) == 0
    assert model.row_for_path(new) == 1
    assert model.apply_rating_renames(((new, old, None),))
    assert [item.path for item in model.source_items] == [old, middle]
    assert model.row_for_path(old) == 0
    assert model.row_for_path(middle) == 1


def test_stable_multitag_rename_updates_rows_without_model_reset(tmp_path: Path) -> None:
    old = [tmp_path / f"book-{number:02}.png" for number in range(3)]
    new = [tmp_path / f"book-{number:02} {{zpi$t=Tagged}}.png" for number in range(3)]
    model = BrowserItemModel()
    model.set_items([_item(path, None) for path in old])
    resets: list[None] = []
    model.modelReset.connect(lambda: resets.append(None))

    assert model.apply_rating_renames(tuple(
        (source, destination, None) for source, destination in zip(old, new)
    ))
    assert not resets
    assert [item.path for item in model.items] == new
    assert all(model.row_for_path(path) == index for index, path in enumerate(new))


def test_multitag_rename_with_filter_membership_change_rebuilds_visible_rows(
    tmp_path: Path,
) -> None:
    old = [tmp_path / f"book-{number:02}.png" for number in range(3)]
    new = [tmp_path / f"book-{number:02} {{zpi$t=Tagged}}.png" for number in range(2)]
    model = BrowserItemModel()
    model.set_items([_item(path, None) for path in old])
    model.configure_filter(BrowserFilterState.normalized(exclude_tags=("Tagged",)))
    resets: list[None] = []
    model.modelReset.connect(lambda: resets.append(None))

    assert model.apply_rating_renames(tuple(
        (source, destination, None) for source, destination in zip(old[:2], new)
    ))
    assert resets
    assert [item.path for item in model.source_items] == [new[0], new[1], old[2]]
    assert [item.path for item in model.items] == [old[2]]
    assert all(model.row_for_path(path) == -1 for path in old[:2])


def test_rating_hit_test_maps_thumbnail_overlay_to_five_stars(qapp) -> None:
    delegate = BrowserItemDelegate(thumbnail_size=180)
    cell = QRect(0, 0, 240, 280)
    overlay = delegate.rating_overlay_rect(cell)

    assert delegate.rating_at_position(cell, QPoint(overlay.left() + 4, overlay.center().y())) == 1
    assert delegate.rating_at_position(cell, QPoint(overlay.right() - 4, overlay.center().y())) == 5
    assert delegate.rating_at_position(cell, QPoint(overlay.right() + 10, overlay.center().y())) is None
