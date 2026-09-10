from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, features
from PySide6.QtGui import QImage

from .browser_model import BrowserItem, BrowserItemKind
from .thumbnail_render import (
    THUMBNAIL_ENCODER_QUALITY,
    ThumbnailEncodingPolicy,
    ThumbnailRenderSpec,
    normalize_thumbnail_webp_quality,
)


CACHE_SCHEMA_VERSION = 3
OBSOLETE_FORMAT_PRUNE_BATCH = 128


@dataclass(frozen=True)
class _Fingerprint:
    source_path: str
    item_kind: str
    source_size: int
    source_mtime_ns: int
    cover_path: str
    cover_size: int
    cover_mtime_ns: int
    entry_path: str
    thumbnail_size: int
    format_version: str

    @property
    def key(self) -> str:
        payload = json.dumps(self.__dict__, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CachedThumbnail:
    image: QImage
    cache_token: int
    frame_width: int
    frame_height: int
    low_resolution_placeholder: bool = False
    page_count: int | None = None


class ThumbnailDiskCache:
    """Portable SQLite-indexed thumbnail cache safe for worker-thread use."""

    def __init__(
        self,
        cache_dir: str | Path,
        *,
        enabled: bool = True,
        limit_mb: int = 512,
        limit_bytes: int | None = None,
        cleanup_interval: int = 32,
        max_unused_days: int = 0,
        encoder_quality: int = THUMBNAIL_ENCODER_QUALITY,
        preserve_alpha: bool = False,
        matte_color: str = "#ffffff",
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.files_dir = self.cache_dir / "files"
        self.index_path = self.cache_dir / "index.sqlite3"
        self.limit_bytes = (
            max(1, int(limit_bytes))
            if limit_bytes is not None
            else max(128, min(4096, int(limit_mb))) * 1024 * 1024
        )
        self.cleanup_interval = max(2, int(cleanup_interval))
        self.max_unused_days = self._normalize_unused_days(max_unused_days)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._pending_accesses: dict[str, float] = {}
        self._saves_since_cleanup = 0
        self._cached_usage_bytes = 0
        self._cached_entry_count = 0
        self._cached_last_cleanup = 0.0
        self._encoder, self._extension = self._select_encoder()
        self._encoding_policy = ThumbnailRenderSpec.from_settings(
            149, "portrait_1_sqrt2", "letterbox", encoder_quality=encoder_quality,
            preserve_alpha=preserve_alpha, matte_color=matte_color,
        ).encoding_policy
        self.enabled = False
        self.last_error: str | None = None
        if enabled:
            self.set_enabled(True)

    @property
    def encoder_quality(self) -> int:
        return self._encoding_policy.quality

    @property
    def encoding_policy(self) -> ThumbnailEncodingPolicy:
        return self._encoding_policy

    @property
    def format_version(self) -> str:
        return self._format_version_for_policy(self._encoding_policy)

    def set_encoder_quality(self, quality: int) -> None:
        # Atomic policy scalar only: never wait for encoding/SQLite on the GUI
        # thread. Workers own their immutable spec and snapshot their quality.
        self._encoding_policy = replace(self._encoding_policy, quality=normalize_thumbnail_webp_quality(quality))

    def set_encoding_policy(self, policy: ThumbnailEncodingPolicy) -> None:
        # One immutable assignment, without waiting for a worker's cache lock.
        self._encoding_policy = policy

    def _format_version_for_policy(self, policy: ThumbnailEncodingPolicy) -> str:
        return (
            f"{CACHE_SCHEMA_VERSION}-{self._encoder.lower()}-q{policy.quality}"
            f"-{policy.alpha_token}"
        )

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            if enabled and not self.enabled:
                self.enabled = self._initialize()
            elif not enabled and self.enabled:
                self.flush_accesses()
                self._close_connection()
                self.enabled = False

    def set_limit_mb(self, limit_mb: int) -> None:
        self.limit_bytes = max(128, min(4096, int(limit_mb))) * 1024 * 1024

    def set_max_unused_days(self, days: int) -> None:
        self.max_unused_days = self._normalize_unused_days(days)

    def touch_source_tokens(
        self,
        requests: tuple[tuple[str, int], ...],
    ) -> None:
        with self._lock:
            if not self.enabled or self._connection is None or not requests:
                return
            now = time.time()
            self._connection.executemany(
                """
                UPDATE entries SET last_used = ?
                 WHERE source_path = ? AND thumbnail_size = ?
                """,
                (
                    (now, self._normalize_path(Path(path)), int(token))
                    for path, token in requests
                ),
            )
            self._connection.commit()

    def get(
        self,
        item: BrowserItem,
        thumbnail_size: int,
        *,
        entry_path: str | None = None,
    ) -> QImage | None:
        with self._lock:
            if not self.enabled or self._connection is None:
                return None
            source = self._stat_path(item.path)
            if source is None:
                return None
            try:
                entry_clause = (
                    "" if entry_path is None else " AND entry_path = ?"
                )
                parameters: list[object] = [
                    self._normalize_path(item.path),
                    item.kind.value,
                    int(thumbnail_size),
                    self.format_version,
                ]
                if entry_path is not None:
                    parameters.append(str(entry_path))
                rows = self._connection.execute(
                    f"""
                    SELECT cache_key, file_name, source_size, source_mtime_ns,
                           cover_path, cover_size, cover_mtime_ns
                      FROM entries
                     WHERE source_path = ? AND item_kind = ?
                       AND thumbnail_size = ? AND format_version = ?
                       {entry_clause}
                    """,  # nosec B608: entry_clause is a fixed internal fragment.
                    tuple(parameters),
                ).fetchall()
            except sqlite3.DatabaseError as exc:
                self.last_error = str(exc)
                self.enabled = self._rebuild_database()
                return None
            invalid: list[tuple[str, str]] = []
            for row in rows:
                key, file_name, source_size, source_mtime_ns, cover_path, cover_size, cover_mtime_ns = row
                if (source[0], source[1]) != (source_size, source_mtime_ns):
                    invalid.append((key, file_name))
                    continue
                if cover_path:
                    cover = self._stat_path(Path(cover_path))
                    if cover is None or cover != (cover_size, cover_mtime_ns):
                        invalid.append((key, file_name))
                        continue
                cache_file = self.files_dir / file_name
                if not cache_file.is_file():
                    invalid.append((key, file_name))
                    continue
                image = self._read_qimage(cache_file)
                if image is None:
                    invalid.append((key, file_name))
                    continue
                self._pending_accesses.setdefault(key, time.time())
                if invalid:
                    self._remove_entries(invalid)
                return image
            if invalid:
                self._remove_entries(invalid)
            return None

    def get_suitable(
        self,
        item: BrowserItem,
        spec: ThumbnailRenderSpec,
        *,
        entry_path: str | None = None,
    ) -> CachedThumbnail | None:
        """Return the smallest compatible resolution, or the best lower placeholder."""
        with self._lock:
            if not self.enabled or self._connection is None:
                return None
            source = self._stat_path(item.path)
            if source is None:
                return None
            try:
                entry_clause = (
                    "" if entry_path is None else " AND entry_path = ?"
                )
                parameters: list[object] = [
                    self._normalize_path(item.path),
                    item.kind.value,
                    spec.family_token,
                    self._format_version_for_policy(spec.encoding_policy),
                ]
                if entry_path is not None:
                    parameters.append(str(entry_path))
                parameters.extend(
                    (
                        spec.cache_token,
                        spec.long_edge,
                        spec.long_edge,
                    )
                )
                rows = self._connection.execute(
                    f"""
                    SELECT cache_key, file_name, source_size, source_mtime_ns,
                           cover_path, cover_size, cover_mtime_ns,
                           thumbnail_size, frame_width, frame_height,
                           page_count
                      FROM entries
                     WHERE source_path = ? AND item_kind = ?
                       AND family_token = ? AND format_version = ?
                       {entry_clause}
                     ORDER BY CASE WHEN thumbnail_size = ? THEN -1 ELSE 0 END,
                     CASE
                         WHEN MAX(frame_width, frame_height) >= ? THEN 0 ELSE 1
                     END,
                     CASE
                         WHEN MAX(frame_width, frame_height) >= ?
                         THEN MAX(frame_width, frame_height)
                         ELSE -MAX(frame_width, frame_height)
                     END ASC
                    """,  # nosec B608: entry_clause is a fixed internal fragment.
                    tuple(parameters),
                ).fetchall()
            except sqlite3.DatabaseError as exc:
                self.last_error = str(exc)
                return None
            invalid: list[tuple[str, str]] = []
            for row in rows:
                (
                    key,
                    file_name,
                    source_size,
                    source_mtime_ns,
                    cover_path,
                    cover_size,
                    cover_mtime_ns,
                    cache_token,
                    frame_width,
                    frame_height,
                    page_count,
                ) = row
                if (source[0], source[1]) != (source_size, source_mtime_ns):
                    invalid.append((key, file_name))
                    continue
                if cover_path:
                    cover = self._stat_path(Path(cover_path))
                    if cover is None or cover != (cover_size, cover_mtime_ns):
                        invalid.append((key, file_name))
                        continue
                cache_file = self.files_dir / file_name
                if not cache_file.is_file():
                    invalid.append((key, file_name))
                    continue
                image = self._read_qimage(cache_file)
                if image is None:
                    invalid.append((key, file_name))
                    continue
                self._pending_accesses.setdefault(key, time.time())
                if invalid:
                    self._remove_entries(invalid)
                actual_edge = max(int(frame_width), int(frame_height))
                return CachedThumbnail(
                    image,
                    int(cache_token),
                    int(frame_width),
                    int(frame_height),
                    int(cache_token) != spec.cache_token
                    and actual_edge < spec.long_edge * 0.95,
                    None if page_count is None else max(0, int(page_count)),
                )
            if invalid:
                self._remove_entries(invalid)
            return None

    def put(
        self,
        item: BrowserItem,
        thumbnail_size: int | ThumbnailRenderSpec,
        image: QImage,
        *,
        cover_path: str | Path | None = None,
        entry_path: str = "",
        page_count: int | None = None,
        protected_thumbnail_sizes: set[int] | None = None,
        encoding_policy: ThumbnailEncodingPolicy | None = None,
    ) -> bool:
        with self._lock:
            if (
                not self.enabled
                or self._connection is None
                or image is None
                or image.isNull()
            ):
                return False
            if isinstance(thumbnail_size, ThumbnailRenderSpec):
                policy = thumbnail_size.encoding_policy
                quality = thumbnail_size.encoder_quality
                if type(quality) is not int or not 1 <= quality <= 100:
                    return False
                cache_token = thumbnail_size.cache_token
                family_token = thumbnail_size.family_token
                frame_width = max(1, image.width())
                frame_height = max(1, image.height())
            else:
                policy = encoding_policy if encoding_policy is not None else self._encoding_policy
                cache_token = int(thumbnail_size)
                family_token = cache_token
                frame_width = max(1, image.width())
                frame_height = max(1, image.height())
            fingerprint = self._fingerprint(
                item,
                cache_token,
                Path(cover_path) if cover_path else None,
                entry_path,
                format_version=self._format_version_for_policy(policy),
            )
            if fingerprint is None:
                return False
            if page_count is None:
                # Encoding quality does not invalidate source listing metadata.
                page_count = self.get_page_count(item)
            key = fingerprint.key
            file_name = f"{key}.{self._extension}"
            cache_file = self.files_dir / file_name
            temporary = self.files_dir / f".{key}.{uuid.uuid4().hex}.tmp"
            try:
                existing = self._connection.execute(
                    "SELECT file_name FROM entries WHERE cache_key = ?",
                    (key,),
                ).fetchone()
                if existing is not None and cache_file.is_file():
                    if page_count is not None:
                        self._connection.execute(
                            "UPDATE entries SET page_count = ? WHERE cache_key = ?",
                            (max(0, int(page_count)), key),
                        )
                        self._connection.commit()
                    return False
                self.files_dir.mkdir(parents=True, exist_ok=True)
                pil_image = policy.prepare_pixels(self._qimage_to_pil(image))
                if self._encoder == "WEBP":
                    pil_image.save(temporary, format="WEBP", **policy.webp_options())
                else:
                    pil_image.save(temporary, format="PNG", optimize=False)
                os.replace(temporary, cache_file)
                byte_size = cache_file.stat().st_size
                now = time.time()
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO entries (
                        cache_key, source_path, item_kind, source_size,
                        source_mtime_ns, cover_path, cover_size,
                        cover_mtime_ns, entry_path, thumbnail_size, family_token,
                        frame_width, frame_height, format_version,
                        file_name, byte_size, created_at, last_used, page_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        fingerprint.source_path,
                        fingerprint.item_kind,
                        fingerprint.source_size,
                        fingerprint.source_mtime_ns,
                        fingerprint.cover_path,
                        fingerprint.cover_size,
                        fingerprint.cover_mtime_ns,
                        fingerprint.entry_path,
                        fingerprint.thumbnail_size,
                        family_token,
                        frame_width,
                        frame_height,
                        fingerprint.format_version,
                        file_name,
                        byte_size,
                        now,
                        now,
                        (
                            None
                            if page_count is None
                            else max(0, int(page_count))
                        ),
                    ),
                )
                self._enforce_source_caps(
                    fingerprint.source_path,
                    fingerprint.item_kind,
                    fingerprint.entry_path,
                    family_token,
                    format_version=fingerprint.format_version,
                    current_key=key,
                    protected_thumbnail_sizes=protected_thumbnail_sizes or set(),
                )
                self._connection.commit()
                self._saves_since_cleanup += 1
                if self._saves_since_cleanup >= self.cleanup_interval:
                    self.prune(remove_orphans=True)
                else:
                    self._refresh_cached_statistics()
                return True
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                self.last_error = str(exc)
                return False
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def get_page_count(self, item: BrowserItem) -> int | None:
        """Return valid cached thumbnail metadata without reading image data."""

        with self._lock:
            if not self.enabled or self._connection is None:
                return None
            source = self._stat_path(item.path)
            if source is None:
                return None
            try:
                row = self._connection.execute(
                    """
                    SELECT page_count, source_size, source_mtime_ns
                      FROM entries
                     WHERE source_path = ? AND item_kind = ?
                       AND source_size = ? AND source_mtime_ns = ?
                       AND page_count IS NOT NULL
                     ORDER BY last_used DESC
                     LIMIT 1
                    """,
                    (
                        self._normalize_path(item.path),
                        item.kind.value,
                        source[0],
                        source[1],
                    ),
                ).fetchone()
            except sqlite3.DatabaseError as exc:
                self.last_error = str(exc)
                return None
            if row is None or source != (row[1], row[2]):
                return None
            return max(0, int(row[0]))

    def update_page_count(self, item: BrowserItem, page_count: int) -> int:
        """Attach metadata to existing valid variants for this source."""

        with self._lock:
            if not self.enabled or self._connection is None:
                return 0
            source = self._stat_path(item.path)
            if source is None:
                return 0
            try:
                cursor = self._connection.execute(
                    """
                    UPDATE entries SET page_count = ?
                     WHERE source_path = ? AND item_kind = ?
                       AND source_size = ? AND source_mtime_ns = ?
                    """,
                    (
                        max(0, int(page_count)),
                        self._normalize_path(item.path),
                        item.kind.value,
                        source[0],
                        source[1],
                    ),
                )
                self._connection.commit()
                return max(0, int(cursor.rowcount))
            except sqlite3.DatabaseError as exc:
                self.last_error = str(exc)
                return 0

    def usage_bytes(self) -> int:
        with self._lock:
            return self._cached_usage_bytes if self.enabled else 0

    def statistics(self) -> dict[str, object]:
        with self._lock:
            last_cleanup_display = (
                time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(self._cached_last_cleanup),
                )
                if self._cached_last_cleanup > 0
                else "未実行"
            )
            return {
                "usage_bytes": self._cached_usage_bytes if self.enabled else 0,
                "entry_count": self._cached_entry_count if self.enabled else 0,
                "last_cleanup": self._cached_last_cleanup,
                "last_cleanup_display": last_cleanup_display,
                "limit_bytes": self.limit_bytes,
                "max_unused_days": self.max_unused_days,
                "encoder": self._encoder,
            }

    def cleanup_if_due(self, *, force: bool = False) -> int:
        with self._lock:
            if not self.enabled or self._connection is None:
                return 0
            row = self._connection.execute(
                "SELECT value FROM maintenance WHERE key = 'last_cleanup'"
            ).fetchone()
            last_cleanup = float(row[0]) if row is not None else 0.0
            now = time.time()
            if not force and now - last_cleanup < 86400:
                return 0
            removed = self.prune(remove_orphans=True)
            self._connection.execute(
                "INSERT OR REPLACE INTO maintenance(key, value) VALUES (?, ?)",
                ("last_cleanup", str(now)),
            )
            self._connection.commit()
            self._cached_last_cleanup = now
            self._refresh_cached_statistics()
            return removed

    def flush_accesses(self) -> None:
        with self._lock:
            if (
                not self.enabled
                or self._connection is None
                or not self._pending_accesses
            ):
                return
            pending = tuple(self._pending_accesses.items())
            self._pending_accesses.clear()
            try:
                self._connection.executemany(
                    "UPDATE entries SET last_used = ? WHERE cache_key = ?",
                    ((timestamp, key) for key, timestamp in pending),
                )
                self._connection.commit()
            except sqlite3.DatabaseError as exc:
                self.last_error = str(exc)

    def prune(
        self,
        *,
        remove_orphans: bool = False,
        max_unused_days: int | None = None,
    ) -> int:
        with self._lock:
            if not self.enabled or self._connection is None:
                return 0
            self.flush_accesses()
            try:
                removed = 0
                # Retire obsolete payloads incrementally, even below the LRU
                # byte cap. Never visit or regenerate their source folders.
                obsolete = self._connection.execute(
                    "SELECT cache_key, file_name FROM entries "
                    "WHERE format_version != ? ORDER BY last_used ASC LIMIT ?",
                    (self.format_version, OBSOLETE_FORMAT_PRUNE_BATCH),
                ).fetchall()
                self._remove_entries_without_commit(obsolete)
                removed += len(obsolete)
                days = (
                    self.max_unused_days
                    if max_unused_days is None
                    else self._normalize_unused_days(max_unused_days)
                )
                if days:
                    cutoff = time.time() - days * 86400
                    expired = self._connection.execute(
                        "SELECT cache_key, file_name FROM entries WHERE last_used < ?",
                        (cutoff,),
                    ).fetchall()
                    self._remove_entries_without_commit(expired)
                    removed += len(expired)
                rows = self._connection.execute(
                    "SELECT cache_key, file_name, byte_size, last_used FROM entries "
                    "ORDER BY last_used ASC"
                ).fetchall()
                valid_rows: list[tuple[str, str, int, float]] = []
                for key, file_name, byte_size, last_used in rows:
                    if not (self.files_dir / file_name).is_file():
                        self._connection.execute(
                            "DELETE FROM entries WHERE cache_key = ?",
                            (key,),
                        )
                        removed += 1
                    else:
                        valid_rows.append((key, file_name, int(byte_size), float(last_used)))

                if remove_orphans and self.files_dir.exists():
                    known_files = {
                        row[0]
                        for row in self._connection.execute(
                            "SELECT file_name FROM entries"
                        ).fetchall()
                    }
                    for path in self.files_dir.iterdir():
                        if not path.is_file() or path.name in known_files:
                            continue
                        try:
                            path.unlink()
                            removed += 1
                        except OSError:
                            continue

                variants = self._connection.execute(
                    """
                    SELECT DISTINCT source_path, item_kind, entry_path, family_token
                      FROM entries
                     WHERE format_version = ?
                    """,
                    (self.format_version,),
                ).fetchall()
                for source_path, item_kind, entry_path, family_token in variants:
                    cap_rows = self._connection.execute(
                        """
                        SELECT cache_key, file_name, thumbnail_size, last_used
                          FROM entries
                         WHERE source_path = ? AND item_kind = ? AND entry_path = ?
                           AND family_token = ? AND format_version = ?
                         ORDER BY last_used DESC
                        """,
                        (
                            source_path,
                            item_kind,
                            entry_path,
                            family_token,
                            self.format_version,
                        ),
                    ).fetchall()
                    removed += self._trim_rows(
                        cap_rows,
                        limit=2,
                        current_key="",
                        protected_thumbnail_sizes=set(),
                    )

                items = self._connection.execute(
                    """
                    SELECT DISTINCT source_path, item_kind, entry_path
                      FROM entries
                     WHERE format_version = ?
                    """,
                    (self.format_version,),
                ).fetchall()
                for source_path, item_kind, entry_path in items:
                    cap_rows = self._connection.execute(
                        """
                        SELECT cache_key, file_name, thumbnail_size, last_used
                          FROM entries
                         WHERE source_path = ? AND item_kind = ? AND entry_path = ?
                           AND format_version = ?
                         ORDER BY last_used DESC
                        """,
                        (source_path, item_kind, entry_path, self.format_version),
                    ).fetchall()
                    removed += self._trim_rows(
                        cap_rows,
                        limit=4,
                        current_key="",
                        protected_thumbnail_sizes=set(),
                    )

                lru_rows = self._connection.execute(
                    "SELECT cache_key, file_name, byte_size, last_used FROM entries "
                    "ORDER BY last_used ASC"
                ).fetchall()
                total = sum(int(row[2]) for row in lru_rows)
                target = int(self.limit_bytes * 0.9)
                if total > self.limit_bytes:
                    for key, file_name, byte_size, _last_used in lru_rows:
                        if total <= target:
                            break
                        try:
                            (self.files_dir / file_name).unlink(missing_ok=True)
                        except OSError:
                            continue
                        self._connection.execute(
                            "DELETE FROM entries WHERE cache_key = ?",
                            (key,),
                        )
                        total -= int(byte_size)
                        removed += 1

                self._connection.commit()
                self._saves_since_cleanup = 0
                self._refresh_cached_statistics()
                return removed
            except (OSError, sqlite3.DatabaseError) as exc:
                self.last_error = str(exc)
                return 0

    def clear_all(self) -> bool:
        with self._lock:
            if not self.enabled or self._connection is None:
                return False
            success = True
            try:
                if self.files_dir.exists():
                    for path in self.files_dir.iterdir():
                        if not path.is_file():
                            continue
                        try:
                            path.unlink()
                        except OSError:
                            success = False
                self._connection.execute("DELETE FROM entries")
                self._connection.commit()
                self._pending_accesses.clear()
                self._saves_since_cleanup = 0
                self._cached_usage_bytes = 0
                self._cached_entry_count = 0
            except (OSError, sqlite3.DatabaseError) as exc:
                self.last_error = str(exc)
                return False
            return success

    def close(self) -> None:
        with self._lock:
            if self.enabled:
                self.flush_accesses()
            self._close_connection()
            self.enabled = False

    def _initialize(self) -> bool:
        try:
            self.files_dir.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                self.index_path,
                check_same_thread=False,
            )
            self._connection.execute("PRAGMA synchronous=NORMAL")
            user_version = int(
                self._connection.execute("PRAGMA user_version").fetchone()[0]
            )
            has_entries = (
                self._connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'entries'"
                ).fetchone()
                is not None
            )
            if has_entries and user_version != CACHE_SCHEMA_VERSION:
                self._connection.execute("DROP TABLE entries")
                self._connection.execute("PRAGMA user_version=0")
                self._connection.commit()
                self._create_schema()
                self._refresh_cached_statistics()
                return True
            if user_version not in (0, CACHE_SCHEMA_VERSION):
                raise sqlite3.DatabaseError("unsupported thumbnail cache version")
            self._create_schema()
            self._refresh_cached_statistics()
            return True
        except (OSError, sqlite3.DatabaseError) as exc:
            self.last_error = str(exc)
            return self._rebuild_database()

    def _rebuild_database(self) -> bool:
        self._close_connection()
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(f"{self.index_path}{suffix}").unlink(missing_ok=True)
            self.files_dir.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                self.index_path,
                check_same_thread=False,
            )
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._create_schema()
            self._refresh_cached_statistics()
            return True
        except (OSError, sqlite3.DatabaseError) as exc:
            self.last_error = str(exc)
            self._close_connection()
            return False

    def _create_schema(self) -> None:
        assert self._connection is not None
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                cache_key TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                item_kind TEXT NOT NULL,
                source_size INTEGER NOT NULL,
                source_mtime_ns INTEGER NOT NULL,
                cover_path TEXT NOT NULL,
                cover_size INTEGER NOT NULL,
                cover_mtime_ns INTEGER NOT NULL,
                entry_path TEXT NOT NULL,
                thumbnail_size INTEGER NOT NULL,
                family_token INTEGER NOT NULL,
                frame_width INTEGER NOT NULL,
                frame_height INTEGER NOT NULL,
                format_version TEXT NOT NULL,
                file_name TEXT NOT NULL UNIQUE,
                byte_size INTEGER NOT NULL,
                created_at REAL NOT NULL,
                last_used REAL NOT NULL,
                page_count INTEGER
            )
            """
        )
        columns = {
            str(row[1])
            for row in self._connection.execute(
                "PRAGMA table_info(entries)"
            ).fetchall()
        }
        if "page_count" not in columns:
            self._connection.execute(
                "ALTER TABLE entries ADD COLUMN page_count INTEGER"
            )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS lookup_entries "
            "ON entries(source_path, item_kind, thumbnail_size, format_version)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS suitable_entries "
            "ON entries(source_path, item_kind, family_token, format_version)"
        )
        self._connection.execute(f"PRAGMA user_version={CACHE_SCHEMA_VERSION}")
        self._connection.commit()

    def _fingerprint(
        self,
        item: BrowserItem,
        thumbnail_size: int,
        cover_path: Path | None,
        entry_path: str,
        *,
        format_version: str | None = None,
    ) -> _Fingerprint | None:
        source = self._stat_path(item.path)
        if source is None:
            return None
        if item.kind == BrowserItemKind.FOLDER:
            if cover_path is None:
                return None
            cover = self._stat_path(cover_path)
            if cover is None:
                return None
            normalized_cover = self._normalize_path(cover_path)
        else:
            cover = (0, 0)
            normalized_cover = ""
        return _Fingerprint(
            source_path=self._normalize_path(item.path),
            item_kind=item.kind.value,
            source_size=source[0],
            source_mtime_ns=source[1],
            cover_path=normalized_cover,
            cover_size=cover[0],
            cover_mtime_ns=cover[1],
            entry_path=str(entry_path),
            thumbnail_size=thumbnail_size,
            format_version=self.format_version if format_version is None else format_version,
        )

    def _remove_entries(self, entries: list[tuple[str, str]]) -> None:
        assert self._connection is not None
        self._remove_entries_without_commit(entries)
        self._connection.commit()
        self._refresh_cached_statistics()

    def _refresh_cached_statistics(self) -> None:
        if self._connection is None:
            self._cached_usage_bytes = 0
            self._cached_entry_count = 0
            self._cached_last_cleanup = 0.0
            return
        try:
            count, total = self._connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(byte_size), 0) FROM entries"
            ).fetchone()
            cleanup = self._connection.execute(
                "SELECT value FROM maintenance WHERE key = 'last_cleanup'"
            ).fetchone()
            self._cached_entry_count = max(0, int(count))
            self._cached_usage_bytes = max(0, int(total))
            self._cached_last_cleanup = (
                float(cleanup[0]) if cleanup is not None else 0.0
            )
        except (sqlite3.DatabaseError, TypeError, ValueError):
            self._cached_usage_bytes = 0
            self._cached_entry_count = 0
            self._cached_last_cleanup = 0.0

    def _remove_entries_without_commit(
        self,
        entries: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    ) -> None:
        assert self._connection is not None
        for key, file_name in entries:
            try:
                (self.files_dir / file_name).unlink(missing_ok=True)
            except OSError:
                pass
            self._connection.execute(
                "DELETE FROM entries WHERE cache_key = ?",
                (key,),
            )
            self._pending_accesses.pop(key, None)

    def _enforce_source_caps(
        self,
        source_path: str,
        item_kind: str,
        entry_path: str,
        family_token: int,
        *,
        format_version: str,
        current_key: str,
        protected_thumbnail_sizes: set[int],
    ) -> None:
        assert self._connection is not None
        protected = {int(value) for value in protected_thumbnail_sizes}
        variant_rows = self._connection.execute(
            """
            SELECT cache_key, file_name, thumbnail_size, last_used
              FROM entries
             WHERE source_path = ? AND item_kind = ? AND entry_path = ?
               AND family_token = ? AND format_version = ?
             ORDER BY last_used DESC
            """,
            (
                source_path,
                item_kind,
                entry_path,
                family_token,
                format_version,
            ),
        ).fetchall()
        self._trim_rows(
            variant_rows,
            limit=2,
            current_key=current_key,
            protected_thumbnail_sizes=protected,
        )
        item_rows = self._connection.execute(
            """
            SELECT cache_key, file_name, thumbnail_size, last_used,
                   family_token
              FROM entries
             WHERE source_path = ? AND item_kind = ? AND entry_path = ?
               AND format_version = ?
             ORDER BY CASE WHEN family_token = ? THEN 0 ELSE 1 END,
                      last_used DESC
            """,
            (
                source_path,
                item_kind,
                entry_path,
                format_version,
                family_token,
            ),
        ).fetchall()
        self._trim_rows(
            [row[:4] for row in item_rows],
            limit=4,
            current_key=current_key,
            protected_thumbnail_sizes=protected,
        )

    def _trim_rows(
        self,
        rows: list[tuple[object, ...]],
        *,
        limit: int,
        current_key: str,
        protected_thumbnail_sizes: set[int],
    ) -> int:
        if len(rows) <= limit:
            return 0
        keep: set[str] = {current_key} if current_key else set()
        for key, _file_name, thumbnail_size, _last_used in rows:
            if int(thumbnail_size) in protected_thumbnail_sizes:
                keep.add(str(key))
        for key, _file_name, _thumbnail_size, _last_used in rows:
            if len(keep) >= limit:
                break
            keep.add(str(key))
        victims = [
            (str(key), str(file_name))
            for key, file_name, _thumbnail_size, _last_used in rows
            if str(key) not in keep
        ]
        self._remove_entries_without_commit(victims)
        return len(victims)

    @staticmethod
    def _normalize_unused_days(days: int) -> int:
        value = max(0, min(3650, int(days)))
        return value if value == 0 or value >= 7 else 7

    def _close_connection(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.DatabaseError:
                pass

    @staticmethod
    def _stat_path(path: Path) -> tuple[int, int] | None:
        try:
            info = path.stat()
            return int(info.st_size), int(info.st_mtime_ns)
        except OSError:
            return None

    @staticmethod
    def _normalize_path(path: Path) -> str:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        return os.path.normcase(str(resolved))

    @staticmethod
    def _qimage_to_pil(image: QImage) -> Image.Image:
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        data = bytes(converted.constBits()[: converted.sizeInBytes()])
        return Image.frombytes(
            "RGBA",
            (converted.width(), converted.height()),
            data,
        )

    @staticmethod
    def _read_qimage(path: Path) -> QImage | None:
        try:
            with Image.open(path) as image:
                converted = image.convert("RGBA")
                data = converted.tobytes("raw", "RGBA")
                return QImage(
                    data,
                    converted.width,
                    converted.height,
                    converted.width * 4,
                    QImage.Format.Format_RGBA8888,
                ).copy()
        except Exception:
            return None

    @staticmethod
    def _select_encoder() -> tuple[str, str]:
        try:
            if features.check("webp"):
                return "WEBP", "webp"
        except Exception:
            pass
        return "PNG", "png"
