from __future__ import annotations

import hashlib
import os
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


@pytest.mark.parametrize(
    ("source_name", "item_type"),
    (
        ("folder-book", "folder"),
        ("single-image.jpg", "image"),
        ("book.zip", "archive"),
        ("book.pdf", "pdf"),
        ("missing.cbz", "archive"),
        (r"\\offline-server\share\book", "folder"),
    ),
)
def test_pending_flush_never_inspects_source_filesystem(
    tmp_path: Path,
    monkeypatch,
    source_name: str,
    item_type: str,
) -> None:
    database = tmp_path / "metadata.sqlite3"
    source = Path(source_name)
    if not source.is_absolute():
        source = tmp_path / source
    source_key = MetadataStore.normalize_path(source)
    calls = {
        "stat": 0,
        "is_dir": 0,
        "exists": 0,
        "resolve": 0,
        "os_stat": 0,
    }
    original_stat = Path.stat
    original_is_dir = Path.is_dir
    original_exists = Path.exists
    original_resolve = Path.resolve
    original_os_stat = os.stat

    def is_source(path: object) -> bool:
        try:
            return MetadataStore.normalize_path(path) == source_key
        except (TypeError, ValueError):
            return False

    def guarded_stat(path: Path, *args, **kwargs):
        if is_source(path):
            calls["stat"] += 1
            raise AssertionError("metadata flush inspected source with Path.stat")
        return original_stat(path, *args, **kwargs)

    def guarded_is_dir(path: Path) -> bool:
        if is_source(path):
            calls["is_dir"] += 1
            raise AssertionError("metadata flush inspected source with Path.is_dir")
        return original_is_dir(path)

    def guarded_exists(path: Path) -> bool:
        if is_source(path):
            calls["exists"] += 1
            raise AssertionError("metadata flush inspected source with Path.exists")
        return original_exists(path)

    def guarded_resolve(path: Path, *args, **kwargs) -> Path:
        if is_source(path):
            calls["resolve"] += 1
            raise AssertionError("metadata flush inspected source with Path.resolve")
        return original_resolve(path, *args, **kwargs)

    def guarded_os_stat(path: object, *args, **kwargs):
        if is_source(path):
            calls["os_stat"] += 1
            raise AssertionError("metadata flush inspected source with os.stat")
        return original_os_stat(path, *args, **kwargs)

    store = MetadataStore(database)
    monkeypatch.setattr(Path, "stat", guarded_stat)
    monkeypatch.setattr(Path, "is_dir", guarded_is_dir)
    monkeypatch.setattr(Path, "exists", guarded_exists)
    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    monkeypatch.setattr("app.metadata_store.os.stat", guarded_os_stat)

    store.update_reading_progress(
        str(source),
        page_index=2,
        total_pages=4,
        item_type=item_type,
    )
    store.flush()

    assert calls == {
        "stat": 0,
        "is_dir": 0,
        "exists": 0,
        "resolve": 0,
        "os_stat": 0,
    }
    progress = store.get_reading_progress(str(source))
    history = store.list_history()
    assert progress is not None and progress.page_index == 2
    assert [
        (entry.item_type, MetadataStore.normalize_path(entry.path))
        for entry in history
    ] == [(item_type, source_key)]
    store.close()

    reopened = MetadataStore(database)
    restored = reopened.get_reading_progress(str(source))
    assert restored is not None and restored.page_index == 2
    reopened.close()


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
    assert store.corrupt_backup_path is None
    assert "read only" in (store.last_error or "")
    assert store.list_history() == []
    assert store.list_browser_bookmarks() == []


def test_read_only_metadata_profile_does_not_create_a_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "readonly" / "metadata.sqlite3"
    store = MetadataStore(database, initialize=False)
    try:
        assert not store.enabled
        assert store.last_error
        assert not database.exists()
        assert store.corrupt_backup_path is None
    finally:
        store.close()


def test_metadata_permission_error_is_not_treated_as_corruption(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "metadata.sqlite3"
    database.write_bytes(b"user data that must not be moved")
    original = database.read_bytes()

    def deny_open(*_args, **_kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr("app.metadata_store.sqlite3.connect", deny_open)
    store = MetadataStore(database)
    try:
        assert not store.enabled
        assert store.corrupt_backup_path is None
        assert database.read_bytes() == original
        assert not tuple(tmp_path.glob("metadata.sqlite3.corrupt-*"))
    finally:
        store.close()


def test_locked_metadata_database_is_not_renamed_as_corrupt(
    tmp_path: Path,
) -> None:
    database = tmp_path / "locked.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("CREATE TABLE supported (value TEXT)")
        connection.execute("PRAGMA user_version=1")
    original = database.read_bytes()
    blocker = sqlite3.connect(database, timeout=0)
    store = None
    try:
        blocker.execute("BEGIN EXCLUSIVE")
        store = MetadataStore(database)
        assert not store.enabled
        assert store.corrupt_backup_path is None
        assert database.read_bytes() == original
    finally:
        blocker.rollback()
        blocker.close()
        if store is not None:
            store.close()


def test_future_metadata_schema_is_preserved_and_requires_newer_app(
    tmp_path: Path,
) -> None:
    database = tmp_path / "future.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE future_data (value TEXT)")
        connection.execute("INSERT INTO future_data VALUES ('keep')")
        connection.execute("PRAGMA user_version=2")
    before = database.read_bytes()

    store = MetadataStore(database)
    try:
        assert not store.enabled
        assert store.corrupt_backup_path is None
        assert "新しい" in (store.last_error or "")
        assert database.read_bytes() == before
        with sqlite3.connect(database) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
            assert connection.execute("SELECT value FROM future_data").fetchone()[0] == "keep"
    finally:
        store.close()


def test_transient_sqlite_busy_keeps_progress_and_flush_recovers(
    tmp_path: Path,
) -> None:
    database = tmp_path / "metadata.sqlite3"
    book = str(tmp_path / "book.cbz")
    store = MetadataStore(database)
    store._connection.execute("PRAGMA busy_timeout=0")
    store.update_reading_progress(
        book, page_index=8, total_pages=24, item_type="archive"
    )
    blocker = sqlite3.connect(database, timeout=0)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        assert store.flush() is False
        assert store.enabled
        assert store.get_reading_progress(book).page_index == 8
        assert len(store._pending_progress) == 1
        assert "再試行" in (store.last_error or "")
        assert store.close() is False
        assert not store._closed and store.enabled

        blocker.rollback()
        assert store.flush() is True
        assert not store._pending_progress
        row = store._connection.execute(
            "SELECT history.last_page_index FROM reading_history AS history "
            "JOIN library_items AS item ON item.id=history.library_item_id "
            "WHERE item.normalized_path=?",
            (MetadataStore.normalize_path(book),),
        ).fetchone()
        assert row == (8,)
    finally:
        blocker.close()
        store.close()


def test_progress_commit_failure_retains_data_for_later_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    book = str(tmp_path / "book.zip")
    store.update_reading_progress(
        book, page_index=3, total_pages=9, item_type="archive"
    )
    original_commit = store._commit_transaction_locked

    def fail_commit_once():
        monkeypatch.setattr(store, "_commit_transaction_locked", original_commit)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_commit_transaction_locked", fail_commit_once)
    try:
        assert store.flush() is False
        assert store.enabled
        assert len(store._pending_progress) == 1
        assert store.flush() is True
        assert not store._pending_progress
        progress = store.get_reading_progress(book)
        assert progress is not None and progress.page_index == 3
    finally:
        store.close()


def test_new_progress_during_commit_is_not_cleared_with_old_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    book = str(tmp_path / "book.cbz")
    store.update_reading_progress(
        book, page_index=1, total_pages=12, item_type="archive"
    )
    original_commit = store._commit_transaction_locked
    queued_newer = False

    def commit_with_newer_progress():
        nonlocal queued_newer
        if not queued_newer:
            queued_newer = True
            store.update_reading_progress(
                book, page_index=7, total_pages=12, item_type="archive"
            )
        original_commit()

    monkeypatch.setattr(store, "_commit_transaction_locked", commit_with_newer_progress)
    try:
        assert store.flush() is False
        assert store.get_reading_progress(book).page_index == 7
        assert len(store._pending_progress) == 1
        monkeypatch.setattr(store, "_commit_transaction_locked", original_commit)
        assert store.flush() is True
        assert store.get_reading_progress(book).page_index == 7
    finally:
        store.close()
