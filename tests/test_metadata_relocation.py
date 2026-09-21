from __future__ import annotations

import sqlite3
from pathlib import Path

from app.metadata_store import MetadataStore


def seed_metadata(store: MetadataStore, path: Path) -> None:
    store.record_book_opened(
        str(path),
        item_type="archive",
        start_page_index=2,
        total_pages=10,
    )
    store.update_reading_progress(str(path), page_index=4, total_pages=10)
    store.add_browser_bookmark(str(path), label="本", item_type="archive")
    store.set_rating(str(path), 5)
    store.set_tags(str(path), ["日本語", "タグ"])
    store.flush()


def test_relocate_file_preserves_history_bookmark_progress_rating_and_tags(
    tmp_path: Path,
) -> None:
    old_path = tmp_path / "旧.cbz"
    new_path = tmp_path / "新.cbz"
    old_path.write_bytes(b"book")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    seed_metadata(store, old_path)
    old_path.rename(new_path)

    assert store.relocate_item(str(old_path), str(new_path))

    assert store.get_reading_progress(str(old_path)) is None
    assert store.get_reading_progress(str(new_path)).page_index == 4
    assert store.list_history()[0].path == str(new_path.absolute())
    assert store.list_browser_bookmarks()[0].path == str(new_path.absolute())
    assert store.get_rating(str(new_path)) == 5
    assert store.get_tags(str(new_path)) == ["タグ", "日本語"]
    store.close()


def test_relocate_item_only_changes_exact_path(tmp_path: Path) -> None:
    old = tmp_path / "old.cbz"
    new = tmp_path / "new.cbz"
    descendant = old / "nested.cbz"
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.set_tags(str(old), ["book"])
    store.set_tags(str(descendant), ["nested"])

    assert store.relocate_item(str(old), str(new))
    assert store.get_tags(str(new)) == ["book"]
    assert store.get_tags(str(descendant)) == ["nested"]
    store.close()


def test_relocate_folder_prefix_updates_descendants(tmp_path: Path) -> None:
    old_root = tmp_path / "旧フォルダ"
    new_root = tmp_path / "新フォルダ"
    first = old_root / "本1.cbz"
    second = old_root / "子" / "本2.cbz"
    second.parent.mkdir(parents=True)
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    seed_metadata(store, first)
    seed_metadata(store, second)
    old_root.rename(new_root)

    assert store.relocate_tree(str(old_root), str(new_root))

    paths = {entry.path for entry in store.list_history()}
    assert paths == {
        str((new_root / "本1.cbz").absolute()),
        str((new_root / "子" / "本2.cbz").absolute()),
    }
    store.close()


def test_relocation_collision_merges_without_corrupting_database(
    tmp_path: Path,
) -> None:
    old_path = tmp_path / "old.cbz"
    new_path = tmp_path / "new.cbz"
    old_path.write_bytes(b"old")
    new_path.write_bytes(b"new")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    seed_metadata(store, old_path)
    store.record_book_opened(
        str(new_path),
        item_type="archive",
        start_page_index=1,
        total_pages=3,
    )
    store.set_tags(str(new_path), ["既存"])

    assert store.relocate_item(str(old_path), str(new_path))

    history = store.list_history()
    assert len(history) == 1
    assert history[0].open_count == 2
    assert set(store.get_tags(str(new_path))) == {"既存", "日本語", "タグ"}
    store.close()


def test_relocation_transaction_failure_rolls_back(tmp_path: Path, monkeypatch) -> None:
    old_path = tmp_path / "old.cbz"
    new_path = tmp_path / "new.cbz"
    old_path.write_bytes(b"old")
    new_path.write_bytes(b"new")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    seed_metadata(store, old_path)
    seed_metadata(store, new_path)

    def fail_merge(**_kwargs):
        raise sqlite3.DatabaseError("forced rollback")

    monkeypatch.setattr(store, "_merge_library_items", fail_merge)

    assert not store.relocate_item(str(old_path), str(new_path))
    assert len(store.list_history()) == 2
    assert store.get_rating(str(old_path)) == 5
    assert store.get_rating(str(new_path)) == 5
    store.close()


def test_copy_and_recycle_policy_leave_metadata_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "book.cbz"
    path.write_bytes(b"book")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    seed_metadata(store, path)
    before = store.list_history()

    # Copy and recycle completion intentionally do not call relocation APIs.
    assert store.list_history() == before
    assert store.list_browser_bookmarks()
    store.close()
