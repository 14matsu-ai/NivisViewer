from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, features
from PySide6.QtGui import QImage

from .browser_model import BrowserItem, BrowserItemKind
from .thumbnail_render import ThumbnailRenderSpec


CACHE_SCHEMA_VERSION = 3


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
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._pending_accesses: dict[str, float] = {}
        self._saves_since_cleanup = 0
        self._encoder, self._extension = self._select_encoder()
        self.format_version = (
            f"{CACHE_SCHEMA_VERSION}-{self._encoder.lower()}-q90-alpha-lossless"
        )
        self.enabled = False
        self.last_error: str | None = None
        if enabled:
            self.set_enabled(True)

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

    def get(self, item: BrowserItem, thumbnail_size: int) -> QImage | None:
        with self._lock:
            if not self.enabled or self._connection is None:
                return None
            source = self._stat_path(item.path)
            if source is None:
                return None
            try:
                rows = self._connection.execute(
                    """
                    SELECT cache_key, file_name, source_size, source_mtime_ns,
                           cover_path, cover_size, cover_mtime_ns
                      FROM entries
                     WHERE source_path = ? AND item_kind = ?
                       AND thumbnail_size = ? AND format_version = ?
                    """,
                    (
                        self._normalize_path(item.path),
                        item.kind.value,
                        int(thumbnail_size),
                        self.format_version,
                    ),
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
    ) -> CachedThumbnail | None:
        """Return the smallest compatible resolution, or the best lower placeholder."""
        with self._lock:
            if not self.enabled or self._connection is None:
                return None
            source = self._stat_path(item.path)
            if source is None:
                return None
            try:
                rows = self._connection.execute(
                    """
                    SELECT cache_key, file_name, source_size, source_mtime_ns,
                           cover_path, cover_size, cover_mtime_ns,
                           thumbnail_size, frame_width, frame_height
                      FROM entries
                     WHERE source_path = ? AND item_kind = ?
                       AND family_token = ? AND format_version = ?
                     ORDER BY CASE WHEN thumbnail_size = ? THEN -1 ELSE 0 END,
                     CASE
                         WHEN MAX(frame_width, frame_height) >= ? THEN 0 ELSE 1
                     END,
                     CASE
                         WHEN MAX(frame_width, frame_height) >= ?
                         THEN MAX(frame_width, frame_height)
                         ELSE -MAX(frame_width, frame_height)
                     END ASC
                    """,
                    (
                        self._normalize_path(item.path),
                        item.kind.value,
                        spec.family_token,
                        self.format_version,
                        spec.cache_token,
                        spec.long_edge,
                        spec.long_edge,
                    ),
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
                cache_token = thumbnail_size.cache_token
                family_token = thumbnail_size.family_token
                frame_width = max(1, image.width())
                frame_height = max(1, image.height())
            else:
                cache_token = int(thumbnail_size)
                family_token = cache_token
                frame_width = max(1, image.width())
                frame_height = max(1, image.height())
            fingerprint = self._fingerprint(
                item,
                cache_token,
                Path(cover_path) if cover_path else None,
                entry_path,
            )
            if fingerprint is None:
                return False
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
                    return False
                self.files_dir.mkdir(parents=True, exist_ok=True)
                pil_image = self._qimage_to_pil(image)
                if self._encoder == "WEBP":
                    alpha_extrema = pil_image.getchannel("A").getextrema()
                    save_options = {
                        "format": "WEBP",
                        "quality": 90,
                        "method": 4,
                        "exact": True,
                    }
                    if alpha_extrema[0] < 255:
                        save_options["lossless"] = True
                    pil_image.save(temporary, **save_options)
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
                        file_name, byte_size, created_at, last_used
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                self._connection.commit()
                self._saves_since_cleanup += 1
                if self._saves_since_cleanup >= self.cleanup_interval:
                    self.prune(remove_orphans=True)
                return True
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                self.last_error = str(exc)
                return False
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def usage_bytes(self) -> int:
        with self._lock:
            if not self.enabled or self._connection is None:
                return 0
            try:
                value = self._connection.execute(
                    "SELECT COALESCE(SUM(byte_size), 0) FROM entries"
                ).fetchone()[0]
                return max(0, int(value))
            except sqlite3.DatabaseError:
                return 0

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

    def prune(self, *, remove_orphans: bool = False) -> int:
        with self._lock:
            if not self.enabled or self._connection is None:
                return 0
            self.flush_accesses()
            try:
                rows = self._connection.execute(
                    "SELECT cache_key, file_name, byte_size, last_used FROM entries "
                    "ORDER BY last_used ASC"
                ).fetchall()
                removed = 0
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

                total = sum(row[2] for row in valid_rows)
                target = int(self.limit_bytes * 0.9)
                if total > self.limit_bytes:
                    for key, file_name, byte_size, _last_used in valid_rows:
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
                        total -= byte_size
                        removed += 1

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

                self._connection.commit()
                self._saves_since_cleanup = 0
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
                return True
            if user_version not in (0, CACHE_SCHEMA_VERSION):
                raise sqlite3.DatabaseError("unsupported thumbnail cache version")
            self._create_schema()
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
                last_used REAL NOT NULL
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
            format_version=self.format_version,
        )

    def _remove_entries(self, entries: list[tuple[str, str]]) -> None:
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
        self._connection.commit()

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
