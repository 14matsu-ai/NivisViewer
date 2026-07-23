from __future__ import annotations

import hashlib
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.metadata_store import METADATA_SCHEMA_VERSION, MetadataStore


def write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (12, 18), "white") as image:
        image.save(path)


def file_snapshot(path: Path) -> tuple[str, int, int]:
    return (
        hashlib.sha256(path.read_bytes()).hexdigest(),
        path.stat().st_size,
        path.stat().st_mtime_ns,
    )


def test_creates_schema_and_reopens_persisted_history(tmp_path: Path) -> None:
    database = tmp_path / "data" / "metadata.sqlite3"
    book = tmp_path / "日本語の本"
    write_image(book / "1.jpg")
    store = MetadataStore(database)

    store.record_book_opened(
        str(book),
        item_type="folder",
        start_page_index=0,
        total_pages=4,
    )
    store.update_reading_progress(str(book), page_index=2, total_pages=4)
    store.flush()
    store.close()

    reopened = MetadataStore(database)
    progress = reopened.get_reading_progress(str(book))
    history = reopened.list_history()

    assert reopened.enabled
    assert reopened.schema_version == METADATA_SCHEMA_VERSION
    assert progress is not None
    assert (progress.page_index, progress.total_pages) == (2, 4)
    assert len(history) == 1
    assert history[0].path == str(book.absolute())
    reopened.close()


def test_repeated_open_updates_one_history_row_and_open_count(
    tmp_path: Path,
) -> None:
    book = tmp_path / "book"
    write_image(book / "1.jpg")
    store = MetadataStore(tmp_path / "metadata.sqlite3")

    store.record_book_opened(
        str(book), item_type="folder", start_page_index=0, total_pages=3
    )
    first_opened_at = store.list_history()[0].last_opened_at
    time.sleep(0.01)
    store.record_book_opened(
        str(book), item_type="folder", start_page_index=1, total_pages=3
    )
    history = store.list_history()

    assert len(history) == 1
    assert history[0].open_count == 2
    assert history[0].page_index == 1
    assert history[0].last_opened_at > first_opened_at
    store.close()


def test_unicode_missing_and_case_variants_do_not_duplicate(tmp_path: Path) -> None:
    book = tmp_path / "日本語Book"
    write_image(book / "1.jpg")
    missing = tmp_path / "存在しない本.zip"
    store = MetadataStore(tmp_path / "metadata.sqlite3")

    store.record_book_opened(
        str(book), item_type="folder", start_page_index=0, total_pages=1
    )
    store.record_book_opened(
        str(book).upper(), item_type="folder", start_page_index=0, total_pages=1
    )
    store.record_book_opened(
        str(missing), item_type="archive", start_page_index=7, total_pages=None
    )

    history = store.list_history()
    assert len(history) == 2
    assert next(entry for entry in history if "日本語" in entry.path).open_count == 2
    assert any(entry.path.endswith("存在しない本.zip") and not entry.exists for entry in history)
    store.close()


def test_bookmarks_are_unique_and_can_be_removed(tmp_path: Path) -> None:
    book = tmp_path / "本.zip"
    book.write_bytes(b"zip placeholder")
    store = MetadataStore(tmp_path / "metadata.sqlite3")

    store.add_browser_bookmark(str(book), label="最初", item_type="archive")
    store.add_browser_bookmark(str(book), label="更新", item_type="archive")

    bookmarks = store.list_browser_bookmarks()
    assert len(bookmarks) == 1
    assert bookmarks[0].label == "更新"
    assert store.is_browser_bookmarked(str(book))

    store.remove_browser_bookmark(str(book))
    assert store.list_browser_bookmarks() == []
    store.close()


def test_history_can_be_removed_individually_and_cleared(tmp_path: Path) -> None:
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    paths = [tmp_path / "1.zip", tmp_path / "2.cbz"]
    for path in paths:
        path.write_bytes(b"archive")
        store.record_book_opened(
            str(path), item_type="archive", start_page_index=0, total_pages=2
        )

    store.remove_history(str(paths[0]))
    assert [entry.path for entry in store.list_history()] == [str(paths[1].absolute())]

    store.clear_history()
    assert store.list_history() == []
    store.close()


def test_rating_and_unicode_tags_round_trip(tmp_path: Path) -> None:
    book = tmp_path / "本.zip"
    book.write_bytes(b"archive")
    store = MetadataStore(tmp_path / "metadata.sqlite3")

    store.set_rating(str(book), 5)
    store.set_tags(str(book), [" 漫画 ", "", "お気に入り", "漫画", "Tag", "tag"])

    assert store.get_rating(str(book)) == 5
    assert store.get_tags(str(book)) == ["Tag", "お気に入り", "漫画"]

    store.set_rating(str(book), None)
    assert store.get_rating(str(book)) is None
    with pytest.raises(ValueError):
        store.set_rating(str(book), 6)
    store.close()


def test_metadata_operations_never_modify_source_files_or_create_sidecars(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    image = library / "画像.jpg"
    write_image(image)
    archive = library / "本.cbz"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(image, "1.jpg")
    before_image = file_snapshot(image)
    before_archive = file_snapshot(archive)
    before_names = sorted(path.name for path in library.iterdir())
    folder_mtime = library.stat().st_mtime_ns
    store = MetadataStore(tmp_path / "portable" / "data" / "metadata.sqlite3")

    store.record_book_opened(
        str(archive), item_type="archive", start_page_index=0, total_pages=1
    )
    store.update_reading_progress(str(archive), page_index=0, total_pages=1)
    store.add_browser_bookmark(str(archive), item_type="archive")
    store.set_rating(str(archive), 4)
    store.set_tags(str(archive), ["日本語"])
    store.flush()

    assert file_snapshot(image) == before_image
    assert file_snapshot(archive) == before_archive
    assert sorted(path.name for path in library.iterdir()) == before_names
    assert library.stat().st_mtime_ns == folder_mtime
    assert not Path(f"{archive}:NivisViewer").exists()
    assert store.database_path.parent == tmp_path / "portable" / "data"
    store.close()


def test_corrupt_database_is_backed_up_and_rebuilt(tmp_path: Path) -> None:
    database = tmp_path / "data" / "metadata.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not a sqlite database")

    store = MetadataStore(database)

    assert store.enabled
    assert store.schema_version == METADATA_SCHEMA_VERSION
    assert store.corrupt_backup_path is not None
    assert store.corrupt_backup_path.read_bytes() == b"not a sqlite database"
    store.close()


def test_schema_contains_future_metadata_tables(tmp_path: Path) -> None:
    database = tmp_path / "metadata.sqlite3"
    store = MetadataStore(database)
    store.close()

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert {
        "library_items",
        "reading_history",
        "browser_bookmarks",
        "tags",
        "item_tags",
    } <= tables
    assert version == METADATA_SCHEMA_VERSION


def test_flush_and_close_are_idempotent(tmp_path: Path) -> None:
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.update_reading_progress(
        str(tmp_path / "missing.zip"),
        page_index=3,
        total_pages=5,
    )

    store.flush()
    store.flush()
    store.close()
    store.close()


def test_relative_and_trailing_separator_paths_share_one_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    book = tmp_path / "relative-book"
    write_image(book / "1.jpg")
    monkeypatch.chdir(tmp_path)
    store = MetadataStore(tmp_path / "metadata.sqlite3")

    store.record_book_opened(
        "relative-book",
        item_type="folder",
        start_page_index=0,
        total_pages=1,
    )
    store.record_book_opened(
        f"{book}{Path('/').anchor}",
        item_type="folder",
        start_page_index=0,
        total_pages=1,
    )

    assert len(store.list_history()) == 1
    assert store.list_history()[0].open_count == 2
    store.close()


def test_database_open_failure_disables_only_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail_connect(*_args, **_kwargs):
        raise sqlite3.OperationalError("read only")

    monkeypatch.setattr("app.metadata_store.sqlite3.connect", fail_connect)

    store = MetadataStore(tmp_path / "unwritable" / "metadata.sqlite3")
    store.record_book_opened(
        str(tmp_path / "book"),
        item_type="folder",
        start_page_index=0,
        total_pages=1,
    )
    store.add_browser_bookmark(str(tmp_path / "book"), item_type="folder")
    store.set_rating(str(tmp_path / "book"), 3)
    store.set_tags(str(tmp_path / "book"), ["タグ"])
    store.flush()
    store.close()

    assert store.enabled is False
    assert store.list_history() == []
    assert store.list_browser_bookmarks() == []
