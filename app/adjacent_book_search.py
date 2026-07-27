from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Event, Lock

from natsort import natsorted
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from .archive_backend import (
    EXTERNAL_ARCHIVE_EXTENSIONS,
    is_supported_archive_candidate,
)
from .browser_model import BrowserItemKind
from .browser_sort import (
    BrowserSortKey,
    BrowserSortOrder,
    BrowserSortPolicy,
    normalize_browser_sort_key,
    normalize_browser_sort_order,
)
from .image_source import BOOK_FILE_EXTENSIONS, SUPPORTED_EXTENSIONS


ADJACENT_BOOKS = "books"
SIBLING_FOLDERS = "sibling_folders"


class AdjacentBookSearchStatus(StrEnum):
    FOUND = "found"
    BOUNDARY = "boundary"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True)
class AdjacentBookSnapshotEntry:
    absolute_path: str
    item_kind: str
    extension: str
    natural_sort_identity: str
    modified_time_ns: int | None = None
    file_size: int | None = None


@dataclass(frozen=True)
class AdjacentBookBrowserSnapshot:
    parent_folder: str
    scan_generation: int
    entries: tuple[AdjacentBookSnapshotEntry, ...]


@dataclass(frozen=True)
class AdjacentBookSearchRequest:
    request_id: int
    current_book_path: str
    direction: int
    loop: bool
    browser_snapshot: AdjacentBookBrowserSnapshot | None
    generation: int
    candidate_mode: str = ADJACENT_BOOKS
    sort_key: str = BrowserSortKey.NAME.value
    sort_order: str = BrowserSortOrder.ASCENDING.value
    folders_first: bool = True


@dataclass(frozen=True)
class AdjacentBookSearchResult:
    request_id: int
    status: AdjacentBookSearchStatus
    candidate_path: str | None
    generation: int
    error_code: str | None = None


@dataclass(frozen=True)
class _FileSystemEntry:
    path: str
    name: str
    is_directory: bool
    is_file: bool
    extension: str
    modified_time_ns: int | None = None
    file_size: int | None = None

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def kind(self) -> BrowserItemKind:
        return (
            BrowserItemKind.FOLDER
            if self.is_directory
            else BrowserItemKind.OTHER
        )


@dataclass(frozen=True)
class _CacheEntry:
    fingerprint: int | None
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class _WorkerOutcome:
    result: AdjacentBookSearchResult
    parent_key: str
    cache_variant: tuple[str, str, str, bool]
    fingerprint: int | None
    candidates: tuple[str, ...] | None


class AdjacentBookFileSystem:
    """Filesystem adapter used only from an adjacent-search worker."""

    def directory_fingerprint(self, path: str) -> int | None:
        return int(os.stat(path).st_mtime_ns)

    def scandir(
        self,
        path: str,
        *,
        include_metadata: bool = False,
    ) -> tuple[_FileSystemEntry, ...]:
        result: list[_FileSystemEntry] = []
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    is_directory = entry.is_dir(follow_symlinks=False)
                    is_file = entry.is_file(follow_symlinks=False)
                    stat = (
                        entry.stat(follow_symlinks=False)
                        if include_metadata
                        else None
                    )
                except OSError:
                    continue
                result.append(
                    _FileSystemEntry(
                        path=lexical_absolute(entry.path),
                        name=entry.name,
                        is_directory=is_directory,
                        is_file=is_file,
                        extension=os.path.splitext(entry.name)[1].lower(),
                        modified_time_ns=(
                            stat.st_mtime_ns if stat is not None else None
                        ),
                        file_size=(
                            stat.st_size
                            if stat is not None and is_file
                            else None
                        ),
                    )
                )
        return tuple(result)

    def directory_contains_supported_image(self, path: str) -> bool:
        for entry in self.scandir(path):
            if entry.is_file and entry.extension in SUPPORTED_EXTENSIONS:
                return True
        return False


class _SearchSignals(QObject):
    finished = Signal(object)


class _SearchWorker(QRunnable):
    def __init__(
        self,
        request: AdjacentBookSearchRequest,
        filesystem: AdjacentBookFileSystem,
        cached: _CacheEntry | None,
        cancelled: Event,
    ) -> None:
        super().__init__()
        self.request = request
        self.filesystem = filesystem
        self.cached = cached
        self.cancelled = cancelled
        self.signals = _SearchSignals()

    @Slot()
    def run(self) -> None:
        request = self.request
        current = lexical_absolute(request.current_book_path)
        parent = lexical_absolute(os.path.dirname(current))
        parent_key = path_key(parent)
        fingerprint: int | None = None
        candidates: tuple[str, ...] | None = None
        try:
            if self.cancelled.is_set():
                self._emit(
                    AdjacentBookSearchStatus.CANCELLED,
                    parent_key,
                    fingerprint,
                    candidates,
                )
                return
            fingerprint = self.filesystem.directory_fingerprint(parent)
            if (
                self.cached is not None
                and self.cached.fingerprint == fingerprint
            ):
                candidates = self.cached.candidates
            else:
                snapshot = request.browser_snapshot
                if (
                    snapshot is not None
                    and path_key(snapshot.parent_folder) == parent_key
                ):
                    candidates = self._from_snapshot(snapshot)
                else:
                    candidates = self._from_filesystem(parent)
            if self.cancelled.is_set():
                self._emit(
                    AdjacentBookSearchStatus.CANCELLED,
                    parent_key,
                    fingerprint,
                    candidates,
                )
                return
            keys = tuple(path_key(candidate) for candidate in candidates)
            try:
                current_index = keys.index(path_key(current))
            except ValueError:
                self._emit(
                    AdjacentBookSearchStatus.UNAVAILABLE,
                    parent_key,
                    fingerprint,
                    candidates,
                )
                return
            next_index = current_index + (-1 if request.direction < 0 else 1)
            if not 0 <= next_index < len(candidates):
                if request.loop and candidates:
                    next_index %= len(candidates)
                else:
                    self._emit(
                        AdjacentBookSearchStatus.BOUNDARY,
                        parent_key,
                        fingerprint,
                        candidates,
                    )
                    return
            self._emit(
                AdjacentBookSearchStatus.FOUND,
                parent_key,
                fingerprint,
                candidates,
                candidates[next_index],
            )
        except FileNotFoundError:
            self._emit(
                AdjacentBookSearchStatus.UNAVAILABLE,
                parent_key,
                fingerprint,
                candidates,
                error_code="not_found",
            )
        except PermissionError:
            self._emit(
                AdjacentBookSearchStatus.UNAVAILABLE,
                parent_key,
                fingerprint,
                candidates,
                error_code="access_denied",
            )
        except OSError as exc:
            self._emit(
                AdjacentBookSearchStatus.ERROR,
                parent_key,
                fingerprint,
                candidates,
                error_code=f"os_error:{getattr(exc, 'winerror', None) or exc.errno}",
            )

    def _from_filesystem(self, parent: str) -> tuple[str, ...]:
        include_metadata = (
            self.request.candidate_mode == SIBLING_FOLDERS
            and normalize_browser_sort_key(self.request.sort_key)
            is BrowserSortKey.MODIFIED_TIME
        )
        return self._collect_candidates(
            self.filesystem.scandir(
                parent,
                include_metadata=include_metadata,
            )
            if include_metadata
            else self.filesystem.scandir(parent)
        )

    def _from_snapshot(
        self,
        snapshot: AdjacentBookBrowserSnapshot,
    ) -> tuple[str, ...]:
        entries = tuple(
            _FileSystemEntry(
                path=lexical_absolute(entry.absolute_path),
                name=os.path.basename(entry.absolute_path),
                is_directory=entry.item_kind == "folder",
                is_file=entry.item_kind != "folder",
                extension=entry.extension.lower(),
                modified_time_ns=entry.modified_time_ns,
                file_size=entry.file_size,
            )
            for entry in snapshot.entries
        )
        return self._collect_candidates(entries)

    def _collect_candidates(
        self,
        entries: tuple[_FileSystemEntry, ...],
    ) -> tuple[str, ...]:
        if self.request.candidate_mode == SIBLING_FOLDERS:
            policy = BrowserSortPolicy(
                sort_key=normalize_browser_sort_key(self.request.sort_key),
                sort_order=normalize_browser_sort_order(
                    self.request.sort_order
                ),
                folders_first=bool(self.request.folders_first),
            )
            return tuple(
                entry.path
                for entry in policy.sorted_items(
                    entry for entry in entries if entry.is_directory
                )
            )

        candidates: dict[str, str] = {}
        for entry in entries:
            if self.cancelled.is_set():
                break
            candidate: str | None = None
            if entry.is_directory:
                if self.filesystem.directory_contains_supported_image(entry.path):
                    candidate = entry.path
            elif entry.is_file and entry.extension in BOOK_FILE_EXTENSIONS:
                if (
                    entry.extension in EXTERNAL_ARCHIVE_EXTENSIONS
                    and not is_supported_archive_candidate(entry.name)
                ):
                    continue
                candidate = (
                    lexical_absolute(os.path.dirname(entry.path))
                    if entry.extension in SUPPORTED_EXTENSIONS
                    else entry.path
                )
            if candidate is not None:
                candidates[path_key(candidate)] = lexical_absolute(candidate)
        ordered = natsorted(
            candidates.values(),
            key=lambda value: os.path.basename(value).casefold(),
        )
        return tuple(ordered)

    def _emit(
        self,
        status: AdjacentBookSearchStatus,
        parent_key: str,
        fingerprint: int | None,
        candidates: tuple[str, ...] | None,
        candidate_path: str | None = None,
        *,
        error_code: str | None = None,
    ) -> None:
        self.signals.finished.emit(
            _WorkerOutcome(
                AdjacentBookSearchResult(
                    self.request.request_id,
                    status,
                    candidate_path,
                    self.request.generation,
                    error_code,
                ),
                parent_key,
                (
                    self.request.candidate_mode,
                    str(self.request.sort_key),
                    str(self.request.sort_order),
                    bool(self.request.folders_first),
                ),
                fingerprint,
                candidates,
            )
        )


class AdjacentBookSearchService(QObject):
    result_ready = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        filesystem: AdjacentBookFileSystem | None = None,
        max_workers: int = 2,
    ) -> None:
        super().__init__(parent)
        self.filesystem = filesystem or AdjacentBookFileSystem()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max(2, int(max_workers)))
        self._lock = Lock()
        self._cancel_events: dict[int, Event] = {}
        self._cache: dict[
            tuple[str, str, str, str, bool],
            _CacheEntry,
        ] = {}
        self._closed = False

    def search(self, request: AdjacentBookSearchRequest) -> bool:
        parent_key = path_key(os.path.dirname(request.current_book_path))
        cache_key = (
            parent_key,
            request.candidate_mode,
            str(request.sort_key),
            str(request.sort_order),
            bool(request.folders_first),
        )
        with self._lock:
            if self._closed:
                return False
            cancelled = Event()
            self._cancel_events[request.request_id] = cancelled
            cached = self._cache.get(cache_key)
        worker = _SearchWorker(request, self.filesystem, cached, cancelled)
        worker.signals.finished.connect(self._on_finished)
        self._pool.start(worker)
        return True

    def cancel(self, request_id: int) -> None:
        with self._lock:
            event = self._cancel_events.get(int(request_id))
        if event is not None:
            event.set()

    def invalidate(self, parent_path: str | Path | None = None) -> None:
        with self._lock:
            if parent_path is None:
                self._cache.clear()
            else:
                parent_key = path_key(parent_path)
                for key in tuple(self._cache):
                    if key[0] == parent_key:
                        self._cache.pop(key, None)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            pending = tuple(self._cancel_events.values())
            self._cancel_events.clear()
            self._cache.clear()
        for event in pending:
            event.set()
        self._pool.clear()

    @Slot(object)
    def _on_finished(self, outcome: _WorkerOutcome) -> None:
        with self._lock:
            cancelled = self._cancel_events.pop(
                outcome.result.request_id,
                None,
            )
            if self._closed:
                return
            if (
                outcome.candidates is not None
                and outcome.result.status
                not in {
                    AdjacentBookSearchStatus.CANCELLED,
                    AdjacentBookSearchStatus.ERROR,
                }
            ):
                self._cache[
                    (outcome.parent_key, *outcome.cache_variant)
                ] = _CacheEntry(
                    outcome.fingerprint,
                    outcome.candidates,
                )
        if cancelled is not None and cancelled.is_set():
            return
        self.result_ready.emit(outcome.result)


def lexical_absolute(path: str | Path) -> str:
    return os.path.abspath(os.path.normpath(os.fspath(path)))


def path_key(path: str | Path) -> str:
    return os.path.normcase(lexical_absolute(path)).casefold()
