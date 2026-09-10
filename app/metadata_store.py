from __future__ import annotations

from .i18n import tr


import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QObject, Signal

from .image_source import ARCHIVE_EXTENSIONS, PDF_EXTENSIONS, SUPPORTED_EXTENSIONS


METADATA_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReadingProgress:
    path: str
    page_index: int
    total_pages: int | None


@dataclass(frozen=True)
class HistoryEntry:
    path: str
    item_type: str
    last_opened_at: float
    page_index: int
    total_pages: int | None
    open_count: int
    exists: bool | None = None

    @property
    def display_name(self) -> str:
        return Path(self.path).name or self.path

@dataclass(frozen=True)
class BrowserBookmark:
    path: str
    label: str
    item_type: str
    sort_order: int
    created_at: float
    exists: bool | None = None

    @property
    def display_name(self) -> str:
        return self.label or Path(self.path).name or self.path

@dataclass(frozen=True)
class _PendingProgress:
    display_path: str
    page_index: int
    total_pages: int | None
    item_type: str | None = None


class MetadataStore(QObject):
    history_changed = Signal()
    bookmarks_changed = Signal()
    metadata_changed = Signal(str)

    def __init__(
        self,
        database_path: str | Path,
        parent: QObject | None = None,
        *,
        initialize: bool = True,
    ) -> None:
        super().__init__(parent)
        self.database_path = Path(database_path)
        self.enabled = False
        self.last_error: str | None = None
        self.corrupt_backup_path: Path | None = None
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._pending_progress: dict[str, _PendingProgress] = {}
        self._closed = False
        if initialize:
            self._initialize()
        else:
            self.last_error = tr('プロファイルは読み取り専用です。')

    @property
    def schema_version(self) -> int:
        with self._lock:
            if self._connection is None:
                return 0
            try:
                return int(
                    self._connection.execute("PRAGMA user_version").fetchone()[0]
                )
            except sqlite3.DatabaseError:
                return 0

    @staticmethod
    def normalize_path(path: str | Path) -> str:
        expanded = os.path.expanduser(os.fspath(path))
        absolute = os.path.abspath(os.path.normpath(expanded))
        return os.path.normcase(absolute).casefold()

    @staticmethod
    def display_path(path: str | Path) -> str:
        expanded = os.path.expanduser(os.fspath(path))
        return os.path.abspath(os.path.normpath(expanded))

    def record_book_opened(
        self,
        path: str,
        *,
        item_type: str,
        start_page_index: int,
        total_pages: int | None,
    ) -> None:
        changed = False
        with self._lock:
            if not self._available:
                return
            try:
                self._flush_pending_locked()
                item_id = self._ensure_library_item(path, item_type=item_type)
                now = time.time()
                page_index = self._clamp_page(start_page_index, total_pages)
                self._connection.execute(
                    """
                    INSERT INTO reading_history (
                        library_item_id, last_opened_at, last_page_index,
                        total_pages, open_count
                    ) VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(library_item_id) DO UPDATE SET
                        last_opened_at = excluded.last_opened_at,
                        last_page_index = excluded.last_page_index,
                        total_pages = excluded.total_pages,
                        open_count = reading_history.open_count + 1
                    """,
                    (item_id, now, page_index, self._safe_total(total_pages)),
                )
                self._connection.commit()
                changed = True
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                self._disable(exc)
        if changed:
            self.history_changed.emit()

    def update_reading_progress(
        self,
        path: str,
        *,
        page_index: int,
        total_pages: int | None,
        item_type: str | None = None,
    ) -> None:
        with self._lock:
            if not self._available:
                return
            display = self.display_path(path)
            normalized = self.normalize_path(display)
            previous = self._pending_progress.get(normalized)
            self._pending_progress[normalized] = _PendingProgress(
                display_path=display,
                page_index=self._clamp_page(page_index, total_pages),
                total_pages=self._safe_total(total_pages),
                item_type=(
                    item_type
                    if item_type is not None
                    else previous.item_type if previous is not None else None
                ),
            )

    def get_reading_progress(self, path: str) -> ReadingProgress | None:
        normalized = self.normalize_path(path)
        with self._lock:
            pending = self._pending_progress.get(normalized)
            if pending is not None:
                return ReadingProgress(
                    pending.display_path,
                    pending.page_index,
                    pending.total_pages,
                )
            if not self._available:
                return None
            try:
                row = self._connection.execute(
                    """
                    SELECT item.display_path, history.last_page_index,
                           history.total_pages
                      FROM reading_history AS history
                      JOIN library_items AS item
                        ON item.id = history.library_item_id
                     WHERE item.normalized_path = ?
                    """,
                    (normalized,),
                ).fetchone()
            except sqlite3.DatabaseError as exc:
                self._disable(exc)
                return None
        if row is None:
            return None
        return ReadingProgress(str(row[0]), int(row[1]), self._optional_int(row[2]))

    def list_history(self, *, limit: int = 500) -> list[HistoryEntry]:
        with self._lock:
            if not self._available:
                return []
            try:
                rows = self._connection.execute(
                    """
                    SELECT item.display_path, item.item_type,
                           history.last_opened_at, history.last_page_index,
                           history.total_pages, history.open_count
                      FROM reading_history AS history
                      JOIN library_items AS item
                        ON item.id = history.library_item_id
                     ORDER BY history.last_opened_at DESC, item.id DESC
                     LIMIT ?
                    """,
                    (max(1, min(5000, int(limit))),),
                ).fetchall()
            except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
                self._disable(exc)
                return []
        return [
            HistoryEntry(
                path=str(row[0]),
                item_type=str(row[1]),
                last_opened_at=float(row[2]),
                page_index=int(row[3]),
                total_pages=self._optional_int(row[4]),
                open_count=int(row[5]),
            )
            for row in rows
        ]

    def remove_history(self, path: str) -> None:
        normalized = self.normalize_path(path)
        changed = False
        with self._lock:
            self._pending_progress.pop(normalized, None)
            if not self._available:
                return
            try:
                cursor = self._connection.execute(
                    """
                    DELETE FROM reading_history
                     WHERE library_item_id = (
                        SELECT id FROM library_items WHERE normalized_path = ?
                     )
                    """,
                    (normalized,),
                )
                self._connection.commit()
                changed = cursor.rowcount > 0
            except sqlite3.DatabaseError as exc:
                self._disable(exc)
        if changed:
            self.history_changed.emit()

    def clear_history(self) -> None:
        changed = False
        with self._lock:
            self._pending_progress.clear()
            if not self._available:
                return
            try:
                cursor = self._connection.execute("DELETE FROM reading_history")
                self._connection.commit()
                changed = cursor.rowcount > 0
            except sqlite3.DatabaseError as exc:
                self._disable(exc)
        if changed:
            self.history_changed.emit()

    def add_browser_bookmark(
        self,
        path: str,
        *,
        label: str | None = None,
        item_type: str | None = None,
    ) -> None:
        changed = False
        with self._lock:
            if not self._available:
                return
            try:
                actual_type = item_type or self._infer_item_type(path)
                item_id = self._ensure_library_item(
                    path,
                    item_type=actual_type,
                )
                display = self.display_path(path)
                bookmark_label = (label or "").strip() or Path(display).name or display
                next_order = int(
                    self._connection.execute(
                        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM browser_bookmarks"
                    ).fetchone()[0]
                )
                self._connection.execute(
                    """
                    INSERT INTO browser_bookmarks (
                        library_item_id, label, item_type, sort_order, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(library_item_id) DO UPDATE SET
                        label = excluded.label,
                        item_type = excluded.item_type
                    """,
                    (item_id, bookmark_label, actual_type, next_order, time.time()),
                )
                self._connection.commit()
                changed = True
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                self._disable(exc)
        if changed:
            self.bookmarks_changed.emit()

    def remove_browser_bookmark(self, path: str) -> None:
        changed = False
        with self._lock:
            if not self._available:
                return
            try:
                cursor = self._connection.execute(
                    """
                    DELETE FROM browser_bookmarks
                     WHERE library_item_id = (
                        SELECT id FROM library_items WHERE normalized_path = ?
                     )
                    """,
                    (self.normalize_path(path),),
                )
                self._connection.commit()
                changed = cursor.rowcount > 0
            except sqlite3.DatabaseError as exc:
                self._disable(exc)
        if changed:
            self.bookmarks_changed.emit()

    def is_browser_bookmarked(self, path: str) -> bool:
        with self._lock:
            if not self._available:
                return False
            try:
                row = self._connection.execute(
                    """
                    SELECT 1
                      FROM browser_bookmarks AS bookmark
                      JOIN library_items AS item
                        ON item.id = bookmark.library_item_id
                     WHERE item.normalized_path = ?
                    """,
                    (self.normalize_path(path),),
                ).fetchone()
                return row is not None
            except sqlite3.DatabaseError as exc:
                self._disable(exc)
                return False

    def list_browser_bookmarks(self) -> list[BrowserBookmark]:
        with self._lock:
            if not self._available:
                return []
            try:
                rows = self._connection.execute(
                    """
                    SELECT item.display_path, bookmark.label, bookmark.item_type,
                           bookmark.sort_order, bookmark.created_at
                      FROM browser_bookmarks AS bookmark
                      JOIN library_items AS item
                        ON item.id = bookmark.library_item_id
                     ORDER BY bookmark.sort_order, bookmark.created_at
                    """
                ).fetchall()
            except sqlite3.DatabaseError as exc:
                self._disable(exc)
                return []
        return [
            BrowserBookmark(
                path=str(row[0]),
                label=str(row[1]),
                item_type=str(row[2]),
                sort_order=int(row[3]),
                created_at=float(row[4]),
            )
            for row in rows
        ]

    def list_folder_bookmarks(self) -> list[BrowserBookmark]:
        return [
            entry
            for entry in self.list_browser_bookmarks()
            if entry.item_type == "folder"
        ]

    def add_folder_bookmark(
        self,
        path: str,
        *,
        label: str | None = None,
    ) -> bool:
        if self.is_browser_bookmarked(path):
            return False
        self.add_browser_bookmark(path, label=label, item_type="folder")
        return self.is_browser_bookmarked(path)

    def remove_folder_bookmark(self, path: str) -> bool:
        if not any(
            self.normalize_path(entry.path) == self.normalize_path(path)
            for entry in self.list_folder_bookmarks()
        ):
            return False
        self.remove_browser_bookmark(path)
        return True

    def rename_bookmark_label(self, path: str, label: str) -> bool:
        normalized_label = str(label).strip()
        if not normalized_label:
            return False
        changed = False
        with self._lock:
            if not self._available:
                return False
            try:
                cursor = self._connection.execute(
                    """
                    UPDATE browser_bookmarks
                       SET label = ?
                     WHERE library_item_id = (
                        SELECT id FROM library_items WHERE normalized_path = ?
                     )
                    """,
                    (normalized_label, self.normalize_path(path)),
                )
                self._connection.commit()
                changed = cursor.rowcount > 0
            except sqlite3.DatabaseError as exc:
                self._disable(exc)
        if changed:
            self.bookmarks_changed.emit()
        return changed

    def reorder_folder_bookmarks(self, ordered_paths: Iterable[str]) -> bool:
        entries = self.list_folder_bookmarks()
        by_key = {
            self.normalize_path(entry.path): entry
            for entry in entries
        }
        requested: list[BrowserBookmark] = []
        seen: set[str] = set()
        for path in ordered_paths:
            key = self.normalize_path(path)
            entry = by_key.get(key)
            if entry is not None and key not in seen:
                seen.add(key)
                requested.append(entry)
        requested.extend(
            entry
            for entry in entries
            if self.normalize_path(entry.path) not in seen
        )
        if [entry.path for entry in requested] == [entry.path for entry in entries]:
            return False
        available_orders = sorted(entry.sort_order for entry in entries)
        with self._lock:
            if not self._available:
                return False
            try:
                for entry, sort_order in zip(requested, available_orders):
                    self._connection.execute(
                        """
                        UPDATE browser_bookmarks
                           SET sort_order = ?
                         WHERE library_item_id = (
                            SELECT id FROM library_items
                             WHERE normalized_path = ?
                         )
                        """,
                        (sort_order, self.normalize_path(entry.path)),
                    )
                self._connection.commit()
            except sqlite3.DatabaseError as exc:
                self._disable(exc)
                return False
        self.bookmarks_changed.emit()
        return True

    def set_rating(self, path: str, rating: int | None) -> None:
        if rating is not None and not 0 <= int(rating) <= 5:
            raise ValueError("rating must be between 0 and 5")
        with self._lock:
            if not self._available:
                return
            try:
                item_id = self._ensure_library_item(
                    path,
                    item_type=self._infer_item_type(path),
                )
                self._connection.execute(
                    """
                    UPDATE library_items
                       SET rating = ?, metadata_updated_at = ?
                     WHERE id = ?
                    """,
                    (None if rating is None else int(rating), time.time(), item_id),
                )
                self._connection.commit()
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                self._disable(exc)
                return
        self.metadata_changed.emit(self.display_path(path))

    def get_rating(self, path: str) -> int | None:
        value = self._library_value(path, "rating")
        return None if value is None else int(value)

    def set_comment(self, path: str, comment: str) -> None:
        with self._lock:
            if not self._available:
                return
            try:
                item_id = self._ensure_library_item(
                    path,
                    item_type=self._infer_item_type(path),
                )
                self._connection.execute(
                    """
                    UPDATE library_items
                       SET comment = ?, metadata_updated_at = ?
                     WHERE id = ?
                    """,
                    (str(comment), time.time(), item_id),
                )
                self._connection.commit()
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                self._disable(exc)
                return
        self.metadata_changed.emit(self.display_path(path))

    def get_comment(self, path: str) -> str:
        value = self._library_value(path, "comment")
        return "" if value is None else str(value)

    def set_tags(self, path: str, tags: list[str]) -> None:
        normalized_tags = self._normalize_tags(tags)
        with self._lock:
            if not self._available:
                return
            try:
                item_id = self._ensure_library_item(
                    path,
                    item_type=self._infer_item_type(path),
                )
                self._connection.execute(
                    "DELETE FROM item_tags WHERE library_item_id = ?",
                    (item_id,),
                )
                for normalized_name, display_name in normalized_tags:
                    self._connection.execute(
                        """
                        INSERT INTO tags (normalized_name, display_name)
                        VALUES (?, ?)
                        ON CONFLICT(normalized_name) DO UPDATE SET
                            display_name = excluded.display_name
                        """,
                        (normalized_name, display_name),
                    )
                    tag_id = int(
                        self._connection.execute(
                            "SELECT id FROM tags WHERE normalized_name = ?",
                            (normalized_name,),
                        ).fetchone()[0]
                    )
                    self._connection.execute(
                        """
                        INSERT OR IGNORE INTO item_tags (library_item_id, tag_id)
                        VALUES (?, ?)
                        """,
                        (item_id, tag_id),
                    )
                self._connection.execute(
                    """
                    UPDATE library_items
                       SET metadata_updated_at = ?
                     WHERE id = ?
                    """,
                    (time.time(), item_id),
                )
                self._connection.commit()
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                self._disable(exc)
                return
        self.metadata_changed.emit(self.display_path(path))

    def get_tags(self, path: str) -> list[str]:
        with self._lock:
            if not self._available:
                return []
            try:
                rows = self._connection.execute(
                    """
                    SELECT tag.display_name
                      FROM tags AS tag
                      JOIN item_tags AS link ON link.tag_id = tag.id
                      JOIN library_items AS item
                        ON item.id = link.library_item_id
                     WHERE item.normalized_path = ?
                     ORDER BY tag.normalized_name
                    """,
                    (self.normalize_path(path),),
                ).fetchall()
            except sqlite3.DatabaseError as exc:
                self._disable(exc)
                return []
        return [str(row[0]) for row in rows]

    def relocate_item(self, old_path: str, new_path: str) -> bool:
        return self.relocate_tree(old_path, new_path)

    def reset_content_metadata_for_path(self, path: str) -> bool:
        """Reset content identity while preserving path-oriented bookmarks."""
        normalized = self.normalize_path(path)
        with self._lock:
            if not self._available:
                return False
            assert self._connection is not None
            try:
                self._flush_pending_locked()
                with self._connection:
                    row = self._connection.execute(
                        "SELECT id FROM library_items WHERE normalized_path = ?",
                        (normalized,),
                    ).fetchone()
                    if row is not None:
                        self._reset_content_metadata_locked(int(row[0]))
            except Exception as exc:
                self.last_error = str(exc)
                try:
                    self._connection.rollback()
                except sqlite3.DatabaseError:
                    pass
                return False
        self.history_changed.emit()
        self.metadata_changed.emit(self.display_path(path))
        return True

    def apply_copy_replace_metadata(self, destination_path: str) -> bool:
        return self.reset_content_metadata_for_path(destination_path)

    def apply_partial_move_replace_metadata(
        self,
        source_path: str,
        destination_path: str,
    ) -> bool:
        del source_path
        return self.reset_content_metadata_for_path(destination_path)

    def apply_move_replace_metadata(
        self,
        source_path: str,
        destination_path: str,
    ) -> bool:
        """Replace destination content metadata with source in one transaction."""
        source_display = self.display_path(source_path)
        destination_display = self.display_path(destination_path)
        source_key = self.normalize_path(source_display)
        destination_key = self.normalize_path(destination_display)
        with self._lock:
            if not self._available:
                return False
            assert self._connection is not None
            try:
                self._flush_pending_locked()
                with self._connection:
                    source_row = self._connection.execute(
                        "SELECT id FROM library_items WHERE normalized_path = ?",
                        (source_key,),
                    ).fetchone()
                    destination_row = self._connection.execute(
                        "SELECT id FROM library_items WHERE normalized_path = ?",
                        (destination_key,),
                    ).fetchone()
                    source_id = (
                        int(source_row[0]) if source_row is not None else None
                    )
                    destination_id = (
                        int(destination_row[0])
                        if destination_row is not None
                        else None
                    )
                    if source_id is None:
                        if destination_id is not None:
                            self._reset_content_metadata_locked(destination_id)
                    elif destination_id is None:
                        self._connection.execute(
                            """
                            UPDATE library_items
                               SET normalized_path = ?, display_path = ?,
                                   last_verified_at = ?
                             WHERE id = ?
                            """,
                            (
                                destination_key,
                                destination_display,
                                time.time(),
                                source_id,
                            ),
                        )
                    else:
                        self._replace_destination_content_locked(
                            source_id=source_id,
                            destination_id=destination_id,
                            destination_path=destination_display,
                        )
            except Exception as exc:
                self.last_error = str(exc)
                try:
                    self._connection.rollback()
                except sqlite3.DatabaseError:
                    pass
                return False
        self.history_changed.emit()
        self.bookmarks_changed.emit()
        self.metadata_changed.emit(destination_display)
        return True

    def relocate_tree(self, old_root: str, new_root: str) -> bool:
        old_display = self.display_path(old_root)
        new_display = self.display_path(new_root)
        old_key = self.normalize_path(old_display)
        changed_paths: list[str] = []
        with self._lock:
            if not self._available:
                return False
            assert self._connection is not None
            try:
                self._flush_pending_locked()
                self._connection.commit()
                rows = self._connection.execute(
                    """
                    SELECT id, normalized_path, display_path
                      FROM library_items
                     ORDER BY LENGTH(normalized_path)
                    """
                ).fetchall()
                affected = [
                    (int(row[0]), str(row[1]), str(row[2]))
                    for row in rows
                    if self._path_is_within(str(row[1]), old_key)
                ]
                if not affected:
                    return True
                with self._connection:
                    for item_id, _normalized, display_path in affected:
                        relocated = self._replace_path_prefix(
                            display_path,
                            old_display,
                            new_display,
                        )
                        relocated_key = self.normalize_path(relocated)
                        collision = self._connection.execute(
                            """
                            SELECT id FROM library_items
                             WHERE normalized_path = ? AND id != ?
                            """,
                            (relocated_key, item_id),
                        ).fetchone()
                        if collision is not None:
                            self._merge_library_items(
                                source_id=item_id,
                                destination_id=int(collision[0]),
                                destination_path=relocated,
                            )
                        else:
                            self._connection.execute(
                                """
                                UPDATE library_items
                                   SET normalized_path = ?, display_path = ?,
                                       last_verified_at = ?
                                 WHERE id = ?
                                """,
                                (relocated_key, relocated, time.time(), item_id),
                            )
                        changed_paths.append(relocated)
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                self.last_error = str(exc)
                try:
                    self._connection.rollback()
                except sqlite3.DatabaseError:
                    pass
                return False
        self.history_changed.emit()
        self.bookmarks_changed.emit()
        for path in changed_paths:
            self.metadata_changed.emit(path)
        return True

    def flush(self) -> None:
        changed = False
        with self._lock:
            if not self._available:
                return
            try:
                changed = self._flush_pending_locked()
                self._connection.commit()
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                self._disable(exc)
        if changed:
            self.history_changed.emit()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
        self.flush()
        with self._lock:
            connection = self._connection
            self._connection = None
            self.enabled = False
            self._closed = True
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.DatabaseError:
                    pass

    @property
    def _available(self) -> bool:
        return self.enabled and not self._closed and self._connection is not None

    def _initialize(self) -> None:
        with self._lock:
            try:
                self.database_path.parent.mkdir(parents=True, exist_ok=True)
                self._open_connection()
                self._migrate_schema()
                self.enabled = True
            except sqlite3.OperationalError as exc:
                self.last_error = str(exc)
                self._close_connection()
                self.enabled = False
            except (OSError, sqlite3.DatabaseError) as exc:
                self.last_error = str(exc)
                if not self._recover_corrupt_database():
                    self.enabled = False

    def _open_connection(self) -> None:
        self._connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
        )
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")

    def _migrate_schema(self) -> None:
        assert self._connection is not None
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in (0, METADATA_SCHEMA_VERSION):
            raise sqlite3.DatabaseError(
                f"unsupported metadata schema version: {version}"
            )
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS library_items (
                id INTEGER PRIMARY KEY,
                normalized_path TEXT NOT NULL UNIQUE,
                display_path TEXT NOT NULL,
                item_type TEXT NOT NULL,
                file_size INTEGER,
                source_mtime_ns INTEGER,
                identity_hint TEXT,
                rating INTEGER,
                comment TEXT NOT NULL DEFAULT '',
                metadata_updated_at REAL NOT NULL,
                created_at REAL NOT NULL,
                last_verified_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reading_history (
                library_item_id INTEGER PRIMARY KEY
                    REFERENCES library_items(id) ON DELETE CASCADE,
                last_opened_at REAL NOT NULL,
                last_page_index INTEGER NOT NULL,
                total_pages INTEGER,
                open_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS browser_bookmarks (
                library_item_id INTEGER PRIMARY KEY
                    REFERENCES library_items(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                item_type TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY,
                normalized_name TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS item_tags (
                library_item_id INTEGER NOT NULL
                    REFERENCES library_items(id) ON DELETE CASCADE,
                tag_id INTEGER NOT NULL
                    REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (library_item_id, tag_id)
            );

            CREATE INDEX IF NOT EXISTS history_recent
                ON reading_history(last_opened_at DESC);
            CREATE INDEX IF NOT EXISTS bookmark_order
                ON browser_bookmarks(sort_order);
            """
        )
        self._connection.execute(
            f"PRAGMA user_version={METADATA_SCHEMA_VERSION}"
        )
        self._connection.commit()

    def _recover_corrupt_database(self) -> bool:
        self._close_connection()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            if self.database_path.exists():
                backup = self.database_path.with_name(
                    f"{self.database_path.name}.corrupt-{timestamp}"
                )
                counter = 1
                while backup.exists():
                    backup = self.database_path.with_name(
                        f"{self.database_path.name}.corrupt-{timestamp}-{counter}"
                    )
                    counter += 1
                self.database_path.replace(backup)
                self.corrupt_backup_path = backup
            for suffix in ("-wal", "-shm", "-journal"):
                auxiliary = Path(f"{self.database_path}{suffix}")
                if auxiliary.exists():
                    auxiliary.replace(
                        auxiliary.with_name(f"{auxiliary.name}.corrupt-{timestamp}")
                    )
            self._open_connection()
            self._migrate_schema()
            self.enabled = True
            return True
        except (OSError, sqlite3.DatabaseError) as exc:
            self.last_error = str(exc)
            self._close_connection()
            return False

    def _ensure_library_item(self, path: str, *, item_type: str) -> int:
        """Create or update a library identity without inspecting the source."""
        assert self._connection is not None
        display = self.display_path(path)
        normalized = self.normalize_path(display)
        now = time.time()
        self._connection.execute(
            """
            INSERT INTO library_items (
                normalized_path, display_path, item_type, file_size,
                source_mtime_ns, identity_hint, rating, comment,
                metadata_updated_at, created_at, last_verified_at
            ) VALUES (?, ?, ?, NULL, NULL, NULL, NULL, '', ?, ?, ?)
            ON CONFLICT(normalized_path) DO UPDATE SET
                display_path = excluded.display_path,
                item_type = excluded.item_type
            """,
            (
                normalized,
                display,
                item_type,
                now,
                now,
                0.0,
            ),
        )
        row = self._connection.execute(
            "SELECT id FROM library_items WHERE normalized_path = ?",
            (normalized,),
        ).fetchone()
        if row is None:
            raise sqlite3.DatabaseError("library item was not created")
        return int(row[0])

    def _flush_pending_locked(self) -> bool:
        assert self._connection is not None
        if not self._pending_progress:
            return False
        pending = tuple(self._pending_progress.values())
        self._pending_progress.clear()
        now = time.time()
        for progress in pending:
            item_type = progress.item_type
            if item_type is None:
                normalized = self.normalize_path(progress.display_path)
                row = self._connection.execute(
                    "SELECT item_type FROM library_items WHERE normalized_path = ?",
                    (normalized,),
                ).fetchone()
                item_type = (
                    str(row[0])
                    if row is not None
                    else self._infer_item_type_without_source_io(
                        progress.display_path
                    )
                )
            item_id = self._ensure_library_item(
                progress.display_path,
                item_type=item_type,
            )
            self._connection.execute(
                """
                INSERT INTO reading_history (
                    library_item_id, last_opened_at, last_page_index,
                    total_pages, open_count
                ) VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(library_item_id) DO UPDATE SET
                    last_page_index = excluded.last_page_index,
                    total_pages = excluded.total_pages
                """,
                (
                    item_id,
                    now,
                    progress.page_index,
                    progress.total_pages,
                ),
            )
        return True

    def _merge_library_items(
        self,
        *,
        source_id: int,
        destination_id: int,
        destination_path: str,
    ) -> None:
        assert self._connection is not None
        source_history = self._connection.execute(
            """
            SELECT last_opened_at, last_page_index, total_pages, open_count
              FROM reading_history WHERE library_item_id = ?
            """,
            (source_id,),
        ).fetchone()
        destination_history = self._connection.execute(
            """
            SELECT last_opened_at, last_page_index, total_pages, open_count
              FROM reading_history WHERE library_item_id = ?
            """,
            (destination_id,),
        ).fetchone()
        if source_history is not None:
            if destination_history is None:
                self._connection.execute(
                    """
                    INSERT INTO reading_history (
                        library_item_id, last_opened_at, last_page_index,
                        total_pages, open_count
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (destination_id, *source_history),
                )
            else:
                newest = (
                    source_history
                    if float(source_history[0]) >= float(destination_history[0])
                    else destination_history
                )
                self._connection.execute(
                    """
                    UPDATE reading_history
                       SET last_opened_at = ?, last_page_index = ?,
                           total_pages = ?, open_count = ?
                     WHERE library_item_id = ?
                    """,
                    (
                        newest[0],
                        newest[1],
                        newest[2],
                        int(source_history[3]) + int(destination_history[3]),
                        destination_id,
                    ),
                )

        source_bookmark = self._connection.execute(
            """
            SELECT label, item_type, sort_order, created_at
              FROM browser_bookmarks WHERE library_item_id = ?
            """,
            (source_id,),
        ).fetchone()
        destination_bookmark = self._connection.execute(
            "SELECT 1 FROM browser_bookmarks WHERE library_item_id = ?",
            (destination_id,),
        ).fetchone()
        if source_bookmark is not None and destination_bookmark is None:
            self._connection.execute(
                """
                INSERT INTO browser_bookmarks (
                    library_item_id, label, item_type, sort_order, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (destination_id, *source_bookmark),
            )

        self._connection.execute(
            """
            INSERT OR IGNORE INTO item_tags (library_item_id, tag_id)
            SELECT ?, tag_id FROM item_tags WHERE library_item_id = ?
            """,
            (destination_id, source_id),
        )
        source_metadata = self._connection.execute(
            "SELECT rating, comment FROM library_items WHERE id = ?",
            (source_id,),
        ).fetchone()
        destination_metadata = self._connection.execute(
            "SELECT rating, comment FROM library_items WHERE id = ?",
            (destination_id,),
        ).fetchone()
        if source_metadata is not None and destination_metadata is not None:
            rating = (
                destination_metadata[0]
                if destination_metadata[0] is not None
                else source_metadata[0]
            )
            comment = str(destination_metadata[1] or source_metadata[1] or "")
            self._connection.execute(
                """
                UPDATE library_items
                   SET display_path = ?, rating = ?, comment = ?,
                       metadata_updated_at = ?
                 WHERE id = ?
                """,
                (
                    destination_path,
                    rating,
                    comment,
                    time.time(),
                    destination_id,
                ),
            )
        self._connection.execute(
            "DELETE FROM library_items WHERE id = ?",
            (source_id,),
        )

    def _reset_content_metadata_locked(self, item_id: int) -> None:
        assert self._connection is not None
        self._connection.execute(
            "DELETE FROM reading_history WHERE library_item_id = ?",
            (item_id,),
        )
        self._connection.execute(
            "DELETE FROM item_tags WHERE library_item_id = ?",
            (item_id,),
        )
        self._connection.execute(
            """
            UPDATE library_items
               SET file_size = NULL, source_mtime_ns = NULL,
                   identity_hint = NULL, rating = NULL, comment = '',
                   metadata_updated_at = ?
             WHERE id = ?
            """,
            (time.time(), item_id),
        )

    def _replace_destination_content_locked(
        self,
        *,
        source_id: int,
        destination_id: int,
        destination_path: str,
    ) -> None:
        assert self._connection is not None
        source_content = self._connection.execute(
            """
            SELECT item_type, file_size, source_mtime_ns, identity_hint,
                   rating, comment, metadata_updated_at
              FROM library_items WHERE id = ?
            """,
            (source_id,),
        ).fetchone()
        if source_content is None:
            self._reset_content_metadata_locked(destination_id)
            return
        self._reset_content_metadata_locked(destination_id)
        self._connection.execute(
            """
            UPDATE library_items
               SET display_path = ?, item_type = ?, file_size = ?,
                   source_mtime_ns = ?, identity_hint = ?, rating = ?,
                   comment = ?, metadata_updated_at = ?,
                   last_verified_at = ?
             WHERE id = ?
            """,
            (
                destination_path,
                source_content[0],
                source_content[1],
                source_content[2],
                source_content[3],
                source_content[4],
                source_content[5],
                source_content[6],
                time.time(),
                destination_id,
            ),
        )
        self._connection.execute(
            """
            INSERT INTO reading_history (
                library_item_id, last_opened_at, last_page_index,
                total_pages, open_count
            )
            SELECT ?, last_opened_at, last_page_index, total_pages, open_count
              FROM reading_history WHERE library_item_id = ?
            """,
            (destination_id, source_id),
        )
        self._connection.execute(
            """
            INSERT OR IGNORE INTO item_tags (library_item_id, tag_id)
            SELECT ?, tag_id FROM item_tags WHERE library_item_id = ?
            """,
            (destination_id, source_id),
        )
        source_bookmark = self._connection.execute(
            """
            SELECT label, item_type, sort_order, created_at
              FROM browser_bookmarks WHERE library_item_id = ?
            """,
            (source_id,),
        ).fetchone()
        destination_bookmark = self._connection.execute(
            "SELECT 1 FROM browser_bookmarks WHERE library_item_id = ?",
            (destination_id,),
        ).fetchone()
        if source_bookmark is not None and destination_bookmark is None:
            self._connection.execute(
                """
                INSERT INTO browser_bookmarks (
                    library_item_id, label, item_type, sort_order, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (destination_id, *source_bookmark),
            )
        self._connection.execute(
            "DELETE FROM library_items WHERE id = ?",
            (source_id,),
        )

    def _library_value(self, path: str, column: str):
        if column not in {"rating", "comment"}:
            raise ValueError("unsupported library value")
        with self._lock:
            if not self._available:
                return None
            try:
                row = self._connection.execute(
                    f"SELECT {column} FROM library_items WHERE normalized_path = ?",
                    (self.normalize_path(path),),
                ).fetchone()
            except sqlite3.DatabaseError as exc:
                self._disable(exc)
                return None
        return None if row is None else row[0]

    @staticmethod
    def _path_is_within(path_key: str, root_key: str) -> bool:
        if path_key == root_key:
            return True
        separator = os.sep.casefold()
        return path_key.startswith(root_key.rstrip("\\/") + separator)

    @classmethod
    def _replace_path_prefix(
        cls,
        path: str,
        old_root: str,
        new_root: str,
    ) -> str:
        path_key = cls.normalize_path(path)
        old_key = cls.normalize_path(old_root)
        if path_key == old_key:
            return cls.display_path(new_root)
        relative = os.path.relpath(cls.display_path(path), cls.display_path(old_root))
        return cls.display_path(os.path.join(new_root, relative))

    def _disable(self, error: BaseException) -> None:
        self.last_error = str(error)
        self.enabled = False
        self._pending_progress.clear()
        self._close_connection()

    def _close_connection(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.DatabaseError:
                pass

    @staticmethod
    def _safe_total(total_pages: int | None) -> int | None:
        if total_pages is None:
            return None
        return max(0, int(total_pages))

    @classmethod
    def _clamp_page(cls, page_index: int, total_pages: int | None) -> int:
        index = max(0, int(page_index))
        total = cls._safe_total(total_pages)
        if total is not None and total > 0:
            return min(index, total - 1)
        return index

    @staticmethod
    def _optional_int(value: object) -> int | None:
        return None if value is None else int(value)

    @staticmethod
    def _infer_item_type(path: str) -> str:
        target = Path(path)
        if target.is_dir():
            return "folder"
        suffix = target.suffix.lower()
        if suffix in ARCHIVE_EXTENSIONS:
            return "archive"
        if suffix in PDF_EXTENSIONS:
            return "pdf"
        if suffix in SUPPORTED_EXTENSIONS:
            return "image"
        return "unknown"

    @staticmethod
    def _infer_item_type_without_source_io(path: str) -> str:
        suffix = Path(path).suffix.lower()
        if suffix in ARCHIVE_EXTENSIONS:
            return "archive"
        if suffix in PDF_EXTENSIONS:
            return "pdf"
        if suffix in SUPPORTED_EXTENSIONS:
            return "image"
        return "folder"

    @staticmethod
    def _normalize_tags(tags: Iterable[str]) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_tag in tags:
            display = str(raw_tag).strip()
            if not display:
                continue
            normalized = display.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append((normalized, display))
        return result
