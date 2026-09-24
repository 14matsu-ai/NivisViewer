from __future__ import annotations

from .i18n import tr


import hashlib
import json
import os
import sqlite3
import stat
import threading
import time
import uuid
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, features
from PySide6.QtGui import QImage

from .browser_model import BrowserItem, BrowserItemKind
from .ffmpeg_thumbnail_backend import VIDEO_PREVIEW_EXTENSIONS
from .thumbnail_render import (
    THUMBNAIL_ENCODER_QUALITY,
    ThumbnailEncodingPolicy,
    ThumbnailRenderSpec,
    normalize_thumbnail_webp_quality,
)


CACHE_SCHEMA_VERSION = 3
OBSOLETE_FORMAT_PRUNE_BATCH = 128
CACHE_MAINTENANCE_BATCH = 128
_CONTENT_SIGNATURE_CHUNK_BYTES = 1024 * 1024
_WINDOWS_KERNEL32 = None


class _WindowsFileBasicInfo(ctypes.Structure):
    _fields_ = [
        ("creation_time", ctypes.c_longlong),
        ("last_access_time", ctypes.c_longlong),
        ("last_write_time", ctypes.c_longlong),
        ("change_time", ctypes.c_longlong),
        ("file_attributes", wintypes.DWORD),
    ]


def _windows_change_time_ns(path: Path) -> int | None:
    """Read NTFS/FileBasicInfo ChangeTime without reading file contents."""
    global _WINDOWS_KERNEL32
    if os.name != "nt":
        return None
    try:
        if _WINDOWS_KERNEL32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            kernel32.CreateFileW.restype = wintypes.HANDLE
            kernel32.GetFileInformationByHandleEx.argtypes = [
                wintypes.HANDLE,
                wintypes.INT,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            _WINDOWS_KERNEL32 = kernel32
        handle = _WINDOWS_KERNEL32.CreateFileW(
            str(path),
            0x00000080,  # FILE_READ_ATTRIBUTES
            0x00000001 | 0x00000002 | 0x00000004,  # share read/write/delete
            None,
            3,  # OPEN_EXISTING
            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS (also works for files)
            None,
        )
        invalid_handle = wintypes.HANDLE(-1).value
        if handle == invalid_handle:
            return None
        info = _WindowsFileBasicInfo()
        try:
            if not _WINDOWS_KERNEL32.GetFileInformationByHandleEx(
                handle,
                0,  # FileBasicInfo
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                return None
        finally:
            _WINDOWS_KERNEL32.CloseHandle(handle)
        # FILETIME is expressed in 100 ns ticks since 1601-01-01 UTC.
        return int(info.change_time) * 100
    except (OSError, AttributeError, TypeError, ValueError):
        return None


def _content_signature(path: Path) -> str | None:
    """Return a worker-side version token for one requested source.

    Windows uses the cheap file identity plus native ChangeTime. This catches
    both same-file writes and replacement files without reading a large ZIP or
    image. Filesystems without that native version signal use file identity
    and timestamps for video, and an exact digest for other requested files.
    """
    try:
        info = path.stat()
        if stat.S_ISDIR(info.st_mode):
            kind = "dir"
        elif stat.S_ISREG(info.st_mode):
            kind = "file"
        else:
            return None
        change_time_ns = _windows_change_time_ns(path)
        if change_time_ns is not None and change_time_ns > 0:
            file_id = f"{int(getattr(info, 'st_dev', 0))}:{int(getattr(info, 'st_ino', 0))}"
            return f"win:{kind}:{file_id}:{change_time_ns}:{int(info.st_size)}"
        if kind == "dir":
            return ""
        size = int(info.st_size)
        if path.suffix.casefold() in VIDEO_PREVIEW_EXTENSIONS:
            # The source metadata is already checked by the cache fingerprint.
            # Reading a whole video on every lookup can cost more than decoding
            # the thumbnail and makes each repeated visit feel like a reload.
            return (
                f"video-metadata:{int(getattr(info, 'st_dev', 0))}:"
                f"{int(getattr(info, 'st_ino', 0))}:{size}:"
                f"{int(info.st_mtime_ns)}:"
                f"{int(getattr(info, 'st_ctime_ns', 0))}"
            )
        digest = hashlib.blake2b(digest_size=16)
        digest.update(str(size).encode("ascii"))
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(_CONTENT_SIGNATURE_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


@dataclass(frozen=True)
class _Fingerprint:
    source_path: str
    item_kind: str
    source_size: int
    source_mtime_ns: int
    source_ctime_ns: int
    source_signature: str
    cover_path: str
    cover_size: int
    cover_mtime_ns: int
    cover_signature: str
    entry_path: str
    thumbnail_size: int
    format_version: str

    @property
    def key(self) -> str:
        payload = json.dumps(self.__dict__, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ThumbnailSourceIdentity:
    """Source and cover revision captured before a thumbnail is decoded."""

    source_path: str
    item_kind: str
    source_size: int
    source_mtime_ns: int
    source_ctime_ns: int = 0
    source_signature: str = ""
    cover_path: str = ""
    cover_size: int = 0
    cover_mtime_ns: int = 0
    cover_signature: str = ""

    @classmethod
    def capture(
        cls,
        item: BrowserItem,
        cover_path: str | Path | None = None,
    ) -> ThumbnailSourceIdentity | None:
        try:
            source_info = item.path.stat()
            source_size = int(source_info.st_size)
            source_mtime_ns = int(source_info.st_mtime_ns)
            source_ctime_ns = int(getattr(source_info, "st_ctime_ns", 0))
            source_signature = _content_signature(item.path)
        except OSError:
            return None
        if source_signature is None:
            return None
        if (
            (item.file_size is not None and item.file_size != source_size)
            or (
                item.modified_time_ns is not None
                and item.modified_time_ns != source_mtime_ns
            )
        ):
            return None

        normalized_cover = ""
        cover_size = 0
        cover_mtime_ns = 0
        cover_signature = ""
        if item.kind is BrowserItemKind.FOLDER:
            if cover_path is None:
                return None
            cover = Path(cover_path)
            try:
                cover_info = cover.stat()
                cover_size = int(cover_info.st_size)
                cover_mtime_ns = int(cover_info.st_mtime_ns)
                cover_signature = _content_signature(cover)
            except OSError:
                return None
            if cover_signature is None:
                return None
            normalized_cover = cls._normalize_path(cover)
        elif cover_path is not None:
            return None

        return cls(
            source_path=cls._normalize_path(item.path),
            item_kind=item.kind.value,
            source_size=source_size,
            source_mtime_ns=source_mtime_ns,
            source_ctime_ns=source_ctime_ns,
            source_signature=source_signature,
            cover_path=normalized_cover,
            cover_size=cover_size,
            cover_mtime_ns=cover_mtime_ns,
            cover_signature=cover_signature,
        )

    def matches(self, fingerprint: _Fingerprint) -> bool:
        return (
            self.source_path == fingerprint.source_path
            and self.item_kind == fingerprint.item_kind
            and self.source_size == fingerprint.source_size
            and self.source_mtime_ns == fingerprint.source_mtime_ns
            and self.source_ctime_ns == fingerprint.source_ctime_ns
            and self.source_signature == fingerprint.source_signature
            and self.cover_path == fingerprint.cover_path
            and self.cover_size == fingerprint.cover_size
            and self.cover_mtime_ns == fingerprint.cover_mtime_ns
            and self.cover_signature == fingerprint.cover_signature
        )

    @staticmethod
    def _normalize_path(path: Path) -> str:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        return os.path.normcase(str(resolved))


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
        self._write_epoch = 0
        self._entry_scan_cursor = ""
        self._entry_scan_complete = False
        self._orphan_scanner = None
        self._orphan_scan_complete = False
        self._maintenance_pending = False
        self._active_staging_paths: set[str] = set()
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
        self.last_put_status = "idle"
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
        # Workers keep an immutable policy snapshot. Invalidate queued saves
        # from the previous settings without changing already published rows.
        self._encoding_policy = replace(self._encoding_policy, quality=normalize_thumbnail_webp_quality(quality))
        self.invalidate_pending_writes()

    def set_encoding_policy(self, policy: ThumbnailEncodingPolicy) -> None:
        self._encoding_policy = policy
        self.invalidate_pending_writes()

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
                self._write_epoch += 1
                self.flush_accesses()
                if self._orphan_scanner is not None:
                    self._orphan_scanner.close()
                    self._orphan_scanner = None
                self._close_connection()
                self.enabled = False

    @property
    def write_epoch(self) -> int:
        with self._lock:
            return self._write_epoch

    def invalidate_pending_writes(self) -> int:
        with self._lock:
            self._write_epoch += 1
            return self._write_epoch

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
            source = self._source_for_item(item)
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
                           source_ctime_ns,
                           source_signature,
                           cover_path, cover_size, cover_mtime_ns,
                           cover_signature
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
                (
                    key,
                    file_name,
                    source_size,
                    source_mtime_ns,
                    source_ctime_ns,
                    source_signature,
                    cover_path,
                    cover_size,
                    cover_mtime_ns,
                    cover_signature,
                ) = row
                if source != (source_size, source_mtime_ns, source_ctime_ns):
                    invalid.append((key, file_name))
                    continue
                current_signature = _content_signature(item.path)
                if current_signature != source_signature:
                    invalid.append((key, file_name))
                    continue
                if cover_path:
                    cover = self._stat_path(Path(cover_path))
                    if cover is None or cover != (cover_size, cover_mtime_ns):
                        invalid.append((key, file_name))
                        continue
                    if _content_signature(Path(cover_path)) != cover_signature:
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
            source = self._source_for_item(item)
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
                           source_ctime_ns,
                           source_signature,
                           cover_path, cover_size, cover_mtime_ns,
                           cover_signature,
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
                    source_ctime_ns,
                    source_signature,
                    cover_path,
                    cover_size,
                    cover_mtime_ns,
                    cover_signature,
                    cache_token,
                    frame_width,
                    frame_height,
                    page_count,
                ) = row
                if source != (source_size, source_mtime_ns, source_ctime_ns):
                    invalid.append((key, file_name))
                    continue
                if _content_signature(item.path) != source_signature:
                    invalid.append((key, file_name))
                    continue
                if cover_path:
                    cover = self._stat_path(Path(cover_path))
                    if cover is None or cover != (cover_size, cover_mtime_ns):
                        invalid.append((key, file_name))
                        continue
                    if _content_signature(Path(cover_path)) != cover_signature:
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
        expected_write_epoch: int | None = None,
        expected_source_identity: ThumbnailSourceIdentity | None = None,
    ) -> bool:
        self.last_put_status = "cancelled"
        if image is None or image.isNull():
            self.last_put_status = "invalid"
            return False
        with self._lock:
            if (
                not self.enabled
                or self._connection is None
                or (
                    expected_write_epoch is not None
                    and expected_write_epoch != self._write_epoch
                )
            ):
                self.last_put_status = "disabled"
                return False
        if isinstance(thumbnail_size, ThumbnailRenderSpec):
            policy = thumbnail_size.encoding_policy
            quality = thumbnail_size.encoder_quality
            if type(quality) is not int or not 1 <= quality <= 100:
                self.last_put_status = "invalid"
                return False
            cache_token = thumbnail_size.cache_token
            family_token = thumbnail_size.family_token
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
            self.last_put_status = "source_changed"
            return False
        if (
            expected_source_identity is not None
            and not expected_source_identity.matches(fingerprint)
        ):
            self.last_put_status = "source_changed"
            return False
        key = fingerprint.key
        file_name = f"{key}.{self._extension}"
        cache_file = self.files_dir / file_name
        temporary = self.files_dir / f".{key}.{uuid.uuid4().hex}.tmp"
        try:
            with self._lock:
                if (
                    not self.enabled
                    or self._connection is None
                    or (
                        expected_write_epoch is not None
                        and expected_write_epoch != self._write_epoch
                    )
                ):
                    self.last_put_status = "disabled"
                    return False
                existing = self._connection.execute(
                    "SELECT file_name FROM entries WHERE cache_key = ?",
                    (key,),
                ).fetchone()
                self._active_staging_paths.add(os.path.normcase(str(temporary)))
                if existing is not None and cache_file.is_file():
                    if page_count is not None:
                        self._connection.execute(
                            "UPDATE entries SET page_count = ? WHERE cache_key = ?",
                            (max(0, int(page_count)), key),
                        )
                        self._connection.commit()
                    self.last_put_status = "already_present"
                    return False

            if page_count is None:
                # Metadata lookup is per source and happens outside the writer lock.
                page_count = self.get_page_count(item)
            self.files_dir.mkdir(parents=True, exist_ok=True)
            pil_image = policy.prepare_pixels(self._qimage_to_pil(image))
            if self._encoder == "WEBP":
                pil_image.save(temporary, format="WEBP", **policy.webp_options())
            else:
                pil_image.save(temporary, format="PNG", optimize=False)
            byte_size = temporary.stat().st_size
            with self._lock:
                if (
                    not self.enabled
                    or self._connection is None
                    or (
                        expected_write_epoch is not None
                        and expected_write_epoch != self._write_epoch
                    )
                ):
                    self.last_put_status = "disabled"
                    return False
                current_fingerprint = self._fingerprint(
                    item,
                    cache_token,
                    Path(cover_path) if cover_path else None,
                    entry_path,
                    format_version=self._format_version_for_policy(policy),
                )
                if current_fingerprint != fingerprint:
                    self.last_put_status = "source_changed"
                    return False
                if (
                    expected_source_identity is not None
                    and not expected_source_identity.matches(current_fingerprint)
                ):
                    self.last_put_status = "source_changed"
                    return False
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
                    self.last_put_status = "already_present"
                    return False
                os.replace(temporary, cache_file)
                now = time.time()
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO entries (
                        cache_key, source_path, item_kind, source_size,
                        source_mtime_ns, source_ctime_ns, source_signature,
                        cover_path, cover_size, cover_mtime_ns, cover_signature,
                        entry_path, thumbnail_size, family_token,
                        frame_width, frame_height, format_version,
                        file_name, byte_size, created_at, last_used, page_count
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        key,
                        fingerprint.source_path,
                        fingerprint.item_kind,
                        fingerprint.source_size,
                        fingerprint.source_mtime_ns,
                        fingerprint.source_ctime_ns,
                        fingerprint.source_signature,
                        fingerprint.cover_path,
                        fingerprint.cover_size,
                        fingerprint.cover_mtime_ns,
                        fingerprint.cover_signature,
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
                        None if page_count is None else max(0, int(page_count)),
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
                self._refresh_cached_statistics()
                self._evict_global_capacity_locked(protected_key=key)
                self.last_put_status = "stored"
                return True
        except (OSError, sqlite3.DatabaseError, ValueError) as exc:
            self.last_error = str(exc)
            self.last_put_status = "io_error"
            return False
        finally:
            with self._lock:
                self._active_staging_paths.discard(
                    os.path.normcase(str(temporary))
                )
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def get_page_count(self, item: BrowserItem) -> int | None:
        """Return valid cached thumbnail metadata without reading image data."""

        with self._lock:
            if not self.enabled or self._connection is None:
                return None
            source = self._source_for_item(item)
            if source is None:
                return None
            try:
                row = self._connection.execute(
                    """
                    SELECT page_count, source_size, source_mtime_ns,
                           source_ctime_ns, source_signature
                      FROM entries
                     WHERE source_path = ? AND item_kind = ?
                       AND source_size = ? AND source_mtime_ns = ?
                       AND source_ctime_ns = ?
                       AND source_signature = ?
                       AND page_count IS NOT NULL
                     ORDER BY last_used DESC
                     LIMIT 1
                    """,
                    (
                        self._normalize_path(item.path),
                        item.kind.value,
                        source[0],
                        source[1],
                        source[2],
                        _content_signature(item.path),
                    ),
                ).fetchone()
            except sqlite3.DatabaseError as exc:
                self.last_error = str(exc)
                return None
            if (
                row is None
                or source != (row[1], row[2], row[3])
                or row[4] != _content_signature(item.path)
            ):
                return None
            return max(0, int(row[0]))

    def update_page_count(self, item: BrowserItem, page_count: int) -> int:
        """Attach metadata to existing valid variants for this source."""

        with self._lock:
            if not self.enabled or self._connection is None:
                return 0
            source = self._source_for_item(item)
            if source is None:
                return 0
            try:
                cursor = self._connection.execute(
                    """
                    UPDATE entries SET page_count = ?
                     WHERE source_path = ? AND item_kind = ?
                       AND source_size = ? AND source_mtime_ns = ?
                       AND source_ctime_ns = ?
                       AND source_signature = ?
                    """,
                    (
                        max(0, int(page_count)),
                        self._normalize_path(item.path),
                        item.kind.value,
                        source[0],
                        source[1],
                        source[2],
                        _content_signature(item.path),
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
                else tr('未実行')
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
            if (
                not force
                and not self._maintenance_pending
                and now - last_cleanup < 86400
            ):
                return 0
            removed = self.prune(remove_orphans=True)
            if not self._maintenance_pending:
                self._connection.execute(
                    "INSERT OR REPLACE INTO maintenance(key, value) VALUES (?, ?)",
                    ("last_cleanup", str(now)),
                )
                self._connection.commit()
                self._cached_last_cleanup = now
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
            if not self._maintenance_pending:
                # A completed cycle is a boundary.  Reset both passes
                # together only when a genuinely new cycle begins.
                self._entry_scan_cursor = ""
                self._entry_scan_complete = False
                self._orphan_scan_complete = False
                if self._orphan_scanner is not None:
                    try:
                        self._orphan_scanner.close()
                    except OSError:
                        pass
                    self._orphan_scanner = None
            self.flush_accesses()
            try:
                removed = 0
                connection = self._connection
                batch = max(1, int(CACHE_MAINTENANCE_BATCH))
                obsolete = self._connection.execute(
                    "SELECT cache_key, file_name FROM entries "
                    "WHERE format_version != ? ORDER BY last_used ASC LIMIT ?",
                    (self.format_version, min(batch, OBSOLETE_FORMAT_PRUNE_BATCH) + 1),
                ).fetchall()
                obsolete_more = len(obsolete) > min(batch, OBSOLETE_FORMAT_PRUNE_BATCH)
                obsolete_batch = obsolete[:min(batch, OBSOLETE_FORMAT_PRUNE_BATCH)]
                self._remove_entries_without_commit(obsolete_batch)
                removed += len(obsolete_batch)
                days = (
                    self.max_unused_days
                    if max_unused_days is None
                    else self._normalize_unused_days(max_unused_days)
                )
                expired_more = False
                if days:
                    cutoff = time.time() - days * 86400
                    expired = self._connection.execute(
                        "SELECT cache_key, file_name FROM entries "
                        "WHERE last_used < ? ORDER BY last_used ASC LIMIT ?",
                        (cutoff, batch + 1),
                    ).fetchall()
                    expired_more = len(expired) > batch
                    expired_batch = expired[:batch]
                    self._remove_entries_without_commit(expired_batch)
                    removed += len(expired_batch)

                scan_more = False
                if not self._entry_scan_complete:
                    cursor = self._entry_scan_cursor
                    scan_rows = connection.execute(
                        "SELECT cache_key, file_name FROM entries "
                        "WHERE cache_key > ? ORDER BY cache_key LIMIT ?",
                        (cursor, batch + 1),
                    ).fetchall()
                    scan_more = len(scan_rows) > batch
                    scan_batch = scan_rows[:batch]
                    for key, file_name in scan_batch:
                        if not (self.files_dir / file_name).is_file():
                            try:
                                (self.files_dir / file_name).unlink(missing_ok=True)
                            except OSError:
                                pass
                            connection.execute(
                                "DELETE FROM entries WHERE cache_key = ?", (key,)
                            )
                            removed += 1
                    if scan_more and scan_batch:
                        self._entry_scan_cursor = str(scan_batch[-1][0])
                    else:
                        self._entry_scan_complete = True
                    connection.execute(
                        "INSERT OR REPLACE INTO maintenance(key, value) VALUES (?, ?)",
                        ("entry_scan_cursor", self._entry_scan_cursor),
                    )

                if remove_orphans:
                    if not self._orphan_scan_complete:
                        removed += self._prune_orphan_batch_locked(batch)
                        self._orphan_scan_complete = self._orphan_scanner is None
                else:
                    self._orphan_scan_complete = True

                self._refresh_cached_statistics()
                capacity_more = self._cached_usage_bytes > self.limit_bytes
                if capacity_more:
                    rows = connection.execute(
                        "SELECT cache_key, file_name, byte_size FROM entries "
                        "ORDER BY last_used ASC LIMIT ?",
                        (batch + 1,),
                    ).fetchall()
                    target = int(self.limit_bytes * 0.9)
                    total = self._cached_usage_bytes
                    victims: list[tuple[str, str]] = []
                    for key, file_name, byte_size in rows[:batch]:
                        victims.append((str(key), str(file_name)))
                        total -= max(0, int(byte_size))
                        if total <= target:
                            break
                    self._remove_entries_without_commit(victims)
                    removed += len(victims)

                self._connection.commit()
                self._refresh_cached_statistics()
                self._saves_since_cleanup = 0
                self._maintenance_pending = bool(
                    obsolete_more
                    or expired_more
                    or (not self._entry_scan_complete)
                    or (remove_orphans and not self._orphan_scan_complete)
                    or self._cached_usage_bytes > self.limit_bytes
                )
                return removed
            except (OSError, sqlite3.DatabaseError) as exc:
                self.last_error = str(exc)
                return 0

    @property
    def maintenance_pending(self) -> bool:
        with self._lock:
            return self._maintenance_pending

    def _prune_orphan_batch_locked(self, limit: int) -> int:
        connection = self._connection
        if connection is None or not self.files_dir.exists():
            return 0
        scanner = self._orphan_scanner
        if scanner is None:
            try:
                scanner = os.scandir(self.files_dir)
            except OSError:
                return 0
            self._orphan_scanner = scanner
        removed = 0
        try:
            for _ in range(max(1, int(limit))):
                try:
                    entry = next(scanner)
                except StopIteration:
                    scanner.close()
                    self._orphan_scanner = None
                    break
                if not entry.is_file(follow_symlinks=False):
                    continue
                normalized = os.path.normcase(str(entry.path))
                if normalized in self._active_staging_paths:
                    continue
                if entry.name.startswith(".") and entry.name.endswith(".tmp"):
                    try:
                        # A writer that crashed can be reclaimed eventually,
                        # but an unregistered staging file younger than one
                        # hour is still allowed to finish its publish step.
                        if time.time() - entry.stat().st_mtime < 3600:
                            continue
                    except OSError:
                        continue
                known = connection.execute(
                    "SELECT 1 FROM entries WHERE file_name = ? LIMIT 1",
                    (entry.name,),
                ).fetchone()
                if known is not None:
                    continue
                try:
                    (self.files_dir / entry.name).unlink()
                    removed += 1
                except OSError:
                    continue
        except OSError:
            try:
                scanner.close()
            except OSError:
                pass
            self._orphan_scanner = None
        return removed

    def _evict_global_capacity_locked(self, *, protected_key: str) -> int:
        """Evict a bounded oldest batch using trigger-maintained byte totals."""
        connection = self._connection
        if not self.enabled or connection is None:
            return 0
        if self._cached_usage_bytes <= self.limit_bytes:
            return 0
        target = int(self.limit_bytes * 0.9)
        try:
            rows = connection.execute(
                "SELECT cache_key, file_name, byte_size FROM entries "
                "WHERE cache_key != ? ORDER BY last_used ASC LIMIT 128",
                (protected_key,),
            ).fetchall()
            if not rows:
                # Keep a single thumbnail even when it is larger than the cap.
                return 0
            victims: list[tuple[str, str]] = []
            total = self._cached_usage_bytes
            for key, file_name, byte_size in rows:
                victims.append((str(key), str(file_name)))
                total -= max(0, int(byte_size))
                if total <= target:
                    break
            self._remove_entries_without_commit(victims)
            connection.commit()
            self._refresh_cached_statistics()
            self._maintenance_pending = self._cached_usage_bytes > self.limit_bytes
            return len(victims)
        except (OSError, sqlite3.DatabaseError) as exc:
            self.last_error = str(exc)
            return 0

    def clear_all(self) -> bool:
        with self._lock:
            if not self.enabled or self._connection is None:
                return False
            self._write_epoch += 1
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
                self._entry_scan_cursor = ""
                self._entry_scan_complete = False
                self._maintenance_pending = False
                self._orphan_scan_complete = False
                if self._orphan_scanner is not None:
                    self._orphan_scanner.close()
                    self._orphan_scanner = None
            except (OSError, sqlite3.DatabaseError) as exc:
                self.last_error = str(exc)
                return False
            return success

    def close(self) -> None:
        with self._lock:
            self._write_epoch += 1
            if self.enabled:
                self.flush_accesses()
            if self._orphan_scanner is not None:
                self._orphan_scanner.close()
                self._orphan_scanner = None
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
                self._connection.execute("DROP TABLE IF EXISTS cache_statistics")
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
        stats_table_exists = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'cache_statistics'"
        ).fetchone() is not None
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                cache_key TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                item_kind TEXT NOT NULL,
                source_size INTEGER NOT NULL,
                source_mtime_ns INTEGER NOT NULL,
                source_ctime_ns INTEGER NOT NULL DEFAULT 0,
                source_signature TEXT NOT NULL DEFAULT '',
                cover_path TEXT NOT NULL,
                cover_size INTEGER NOT NULL,
                cover_mtime_ns INTEGER NOT NULL,
                cover_signature TEXT NOT NULL DEFAULT '',
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
        if "source_ctime_ns" not in columns:
            self._connection.execute(
                "ALTER TABLE entries ADD COLUMN source_ctime_ns INTEGER NOT NULL DEFAULT 0"
            )
        if "source_signature" not in columns:
            self._connection.execute(
                "ALTER TABLE entries ADD COLUMN source_signature TEXT NOT NULL DEFAULT ''"
            )
        if "cover_signature" not in columns:
            self._connection.execute(
                "ALTER TABLE entries ADD COLUMN cover_signature TEXT NOT NULL DEFAULT ''"
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
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS cache_statistics ("
            "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
            "entry_count INTEGER NOT NULL, usage_bytes INTEGER NOT NULL)"
        )
        stats_row = self._connection.execute(
            "SELECT entry_count, usage_bytes FROM cache_statistics WHERE singleton = 1"
        ).fetchone()
        if not stats_table_exists or stats_row is None:
            count, total = self._connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(byte_size), 0) FROM entries"
            ).fetchone()
            self._connection.execute(
                "INSERT OR REPLACE INTO cache_statistics "
                "(singleton, entry_count, usage_bytes) VALUES (1, ?, ?)",
                (max(0, int(count)), max(0, int(total))),
            )
        self._connection.execute("PRAGMA recursive_triggers=ON")
        self._connection.executescript(
            """
            DROP TRIGGER IF EXISTS entries_stats_insert;
            DROP TRIGGER IF EXISTS entries_stats_delete;
            DROP TRIGGER IF EXISTS entries_stats_update;
            CREATE TRIGGER entries_stats_insert AFTER INSERT ON entries BEGIN
              UPDATE cache_statistics SET entry_count = entry_count + 1,
                  usage_bytes = usage_bytes + NEW.byte_size WHERE singleton = 1;
            END;
            CREATE TRIGGER entries_stats_delete AFTER DELETE ON entries BEGIN
              UPDATE cache_statistics SET entry_count = MAX(0, entry_count - 1),
                  usage_bytes = MAX(0, usage_bytes - OLD.byte_size) WHERE singleton = 1;
            END;
            CREATE TRIGGER entries_stats_update AFTER UPDATE OF byte_size ON entries BEGIN
              UPDATE cache_statistics SET
                  usage_bytes = MAX(0, usage_bytes + NEW.byte_size - OLD.byte_size)
                  WHERE singleton = 1;
            END;
            """
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
        source = self._source_for_item(item)
        if source is None:
            return None
        source_signature = _content_signature(item.path)
        if source_signature is None:
            return None
        if item.kind == BrowserItemKind.FOLDER:
            if cover_path is None:
                return None
            cover = self._stat_path(cover_path)
            if cover is None:
                return None
            normalized_cover = self._normalize_path(cover_path)
            cover_signature = _content_signature(cover_path)
            if cover_signature is None:
                return None
        else:
            cover = (0, 0)
            normalized_cover = ""
            cover_signature = ""
        return _Fingerprint(
            source_path=self._normalize_path(item.path),
            item_kind=item.kind.value,
            source_size=source[0],
            source_mtime_ns=source[1],
            source_ctime_ns=source[2],
            source_signature=source_signature,
            cover_path=normalized_cover,
            cover_size=cover[0],
            cover_mtime_ns=cover[1],
            cover_signature=cover_signature,
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
            stats = self._connection.execute(
                "SELECT entry_count, usage_bytes FROM cache_statistics "
                "WHERE singleton = 1"
            ).fetchone()
            cleanup = self._connection.execute(
                "SELECT value FROM maintenance WHERE key = 'last_cleanup'"
            ).fetchone()
            self._cached_entry_count = max(0, int(stats[0])) if stats else 0
            self._cached_usage_bytes = max(0, int(stats[1])) if stats else 0
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

    def _source_for_item(self, item: BrowserItem) -> tuple[int, int, int] | None:
        # A decode/count from the scanned version must never be published into
        # a later write's cache identity. Called only on the worker/cache path.
        try:
            info = item.path.stat()
            source = (
                int(info.st_size),
                int(info.st_mtime_ns),
                int(getattr(info, "st_ctime_ns", 0)),
            )
        except OSError:
            return None
        if (
            (item.file_size is not None and item.file_size != source[0])
            or (item.modified_time_ns is not None and item.modified_time_ns != source[1])
        ):
            return None
        return source

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
