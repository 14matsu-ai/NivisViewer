from __future__ import annotations

from pathlib import Path

import pytest

from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_sort import (
    BrowserSortKey,
    BrowserSortOrder,
    BrowserSortPolicy,
)


def item(
    root: Path,
    name: str,
    *,
    kind: BrowserItemKind = BrowserItemKind.IMAGE,
    modified_time_ns: int | None = 0,
    file_size: int | None = 0,
) -> BrowserItem:
    return BrowserItem(
        display_name=name,
        path=(root / name).absolute(),
        kind=kind,
        modified_at=None,
        file_size=file_size,
        modified_time_ns=modified_time_ns,
    )


def names(policy: BrowserSortPolicy, values: list[BrowserItem]) -> list[str]:
    return [entry.display_name for entry in policy.sorted_items(values)]


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        (BrowserSortOrder.ASCENDING, ["book1.jpg", "book2.jpg", "book10.jpg"]),
        (BrowserSortOrder.DESCENDING, ["book10.jpg", "book2.jpg", "book1.jpg"]),
    ],
)
def test_name_sort_is_natural_in_both_directions(
    tmp_path: Path,
    order: BrowserSortOrder,
    expected: list[str],
) -> None:
    values = [
        item(tmp_path, "book10.jpg"),
        item(tmp_path, "book1.jpg"),
        item(tmp_path, "book2.jpg"),
    ]

    assert names(
        BrowserSortPolicy(
            sort_key=BrowserSortKey.NAME,
            sort_order=order,
            folders_first=False,
        ),
        values,
    ) == expected


@pytest.mark.parametrize(
    ("sort_key", "order", "expected"),
    [
        (
            BrowserSortKey.MODIFIED_TIME,
            BrowserSortOrder.ASCENDING,
            ["古い.jpg", "同時1.jpg", "同時2.jpg", "新しい.jpg"],
        ),
        (
            BrowserSortKey.MODIFIED_TIME,
            BrowserSortOrder.DESCENDING,
            ["新しい.jpg", "同時1.jpg", "同時2.jpg", "古い.jpg"],
        ),
        (
            BrowserSortKey.FILE_SIZE,
            BrowserSortOrder.ASCENDING,
            ["古い.jpg", "同時1.jpg", "同時2.jpg", "新しい.jpg"],
        ),
        (
            BrowserSortKey.FILE_SIZE,
            BrowserSortOrder.DESCENDING,
            ["新しい.jpg", "同時1.jpg", "同時2.jpg", "古い.jpg"],
        ),
    ],
)
def test_time_and_size_sort_are_stable_by_natural_name(
    tmp_path: Path,
    sort_key: BrowserSortKey,
    order: BrowserSortOrder,
    expected: list[str],
) -> None:
    values = [
        item(tmp_path, "同時2.jpg", modified_time_ns=2, file_size=2),
        item(tmp_path, "新しい.jpg", modified_time_ns=3, file_size=3),
        item(tmp_path, "古い.jpg", modified_time_ns=1, file_size=1),
        item(tmp_path, "同時1.jpg", modified_time_ns=2, file_size=2),
    ]

    assert names(
        BrowserSortPolicy(sort_key, order, folders_first=False),
        values,
    ) == expected


def test_item_type_groups_zip_and_cbz_in_archive_category(tmp_path: Path) -> None:
    values = [
        item(tmp_path, "画像.jpg"),
        item(tmp_path, "本2.cbz", kind=BrowserItemKind.ARCHIVE),
        item(tmp_path, "フォルダ", kind=BrowserItemKind.FOLDER, file_size=None),
        item(tmp_path, "本1.zip", kind=BrowserItemKind.ARCHIVE),
    ]

    assert names(
        BrowserSortPolicy(
            BrowserSortKey.ITEM_TYPE,
            BrowserSortOrder.ASCENDING,
            folders_first=False,
        ),
        values,
    ) == ["フォルダ", "本1.zip", "本2.cbz", "画像.jpg"]


def test_folders_first_remains_first_even_in_descending_size_sort(
    tmp_path: Path,
) -> None:
    folder = item(
        tmp_path,
        "フォルダ",
        kind=BrowserItemKind.FOLDER,
        file_size=None,
    )
    values = [
        item(tmp_path, "小.jpg", file_size=1),
        folder,
        item(tmp_path, "大.jpg", file_size=100),
    ]

    fixed = BrowserSortPolicy(
        BrowserSortKey.FILE_SIZE,
        BrowserSortOrder.DESCENDING,
        folders_first=True,
    ).sorted_items(values)
    ordinary = BrowserSortPolicy(
        BrowserSortKey.FILE_SIZE,
        BrowserSortOrder.ASCENDING,
        folders_first=False,
    ).sorted_items(values)

    assert fixed[0] is folder
    assert names(
        BrowserSortPolicy(
            BrowserSortKey.FILE_SIZE,
            BrowserSortOrder.DESCENDING,
            folders_first=True,
        ),
        values,
    ) == ["フォルダ", "大.jpg", "小.jpg"]
    assert ordinary[0] is folder


def test_missing_stat_values_do_not_raise(tmp_path: Path) -> None:
    values = [
        item(tmp_path, "不明.jpg", modified_time_ns=None, file_size=None),
        item(tmp_path, "既知.jpg", modified_time_ns=10, file_size=10),
    ]

    for sort_key in (BrowserSortKey.MODIFIED_TIME, BrowserSortKey.FILE_SIZE):
        assert names(
            BrowserSortPolicy(
                sort_key,
                BrowserSortOrder.ASCENDING,
                folders_first=False,
            ),
            values,
        ) == ["不明.jpg", "既知.jpg"]
