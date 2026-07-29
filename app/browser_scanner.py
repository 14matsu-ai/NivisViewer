from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Event, Lock
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from .archive_backend import EXTERNAL_ARCHIVE_EXTENSIONS, is_supported_archive_candidate
from .browser_sort import BrowserSortPolicy
from .browser_visibility import (
    LEGACY_SUPPORTED_ITEMS_POLICY,
    BrowserVisibilityPolicy,
    filesystem_visibility_flags,
)
from .image_source import ARCHIVE_EXTENSIONS, PDF_EXTENSIONS, SUPPORTED_EXTENSIONS
from .file_operation_artifact import FileOperationArtifactPolicy
from .performance_trace import performance_trace

if TYPE_CHECKING:
    from .browser_model import BrowserItem


DEFAULT_SCAN_BATCH_SIZE = 128
_INCOMPLETE_DOWNLOAD_SUFFIXES = (".part", ".crdownload")
_RETIRED_SCANNERS: set[BrowserDirectoryScanner] = set()
_TEXT_PREVIEW_EXTENSIONS = {
    ".txt", ".md", ".log", ".ini", ".cfg", ".conf", ".json", ".yaml",
    ".yml", ".toml", ".xml", ".csv", ".py", ".js", ".ts", ".css",
    ".html", ".htm", ".bat", ".cmd", ".ps1",
}
_VIDEO_PREVIEW_EXTENSIONS = {
    ".mp4", ".m4v", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".mpg",
    ".mpeg", ".mts", ".m2ts", ".ts", ".flv", ".ogv", ".3gp",
}


class BrowserScanStatus(str, Enum):
    NORMAL_DIRECTORY = "normal_directory"
    EMPTY_DIRECTORY = "empty_directory"
    NOT_FOUND = "not_found"
    NOT_DIRECTORY = "not_directory"
    ACCESS_DENIED = "access_denied"
    IO_ERROR = "io_error"
    CANCELLED = "cancelled"


class BrowserScanPriority(int, Enum):
    BACKGROUND = -100
    REFRESH = 0
    INTERACTIVE_NAVIGATION = 100


@dataclass(frozen=True)
class BrowserScanRequest:
    path: str
    generation: int
    batch_size: int = DEFAULT_SCAN_BATCH_SIZE
    visibility_policy: BrowserVisibilityPolicy = LEGACY_SUPPORTED_ITEMS_POLICY
    priority: BrowserScanPriority = BrowserScanPriority.INTERACTIVE_NAVIGATION
    trace_id: int = 0
    sort_policy: BrowserSortPolicy = BrowserSortPolicy()


@dataclass(frozen=True)
class BrowserScanEntry:
    path: str
    display_name: str
    item_kind: str
    modified_time_ns: int | None
    file_size: int | None
    extension: str = ""
    hidden: bool = False
    system: bool = False
    openable_by_nivisviewer: bool = True
    can_generate_preview: bool = True
    preview_kind: str = ""


@dataclass(frozen=True)
class BrowserScanBatch:
    path: str
    generation: int
    entries: tuple[BrowserScanEntry, ...]
    final_items_pending: bool = False


@dataclass(frozen=True)
class BrowserScanCompleted:
    path: str
    generation: int
    total_count: int
    cancelled: bool = False
    prepared_items: tuple[BrowserItem, ...] | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    sort_policy: BrowserSortPolicy | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    @property
    def status(self) -> BrowserScanStatus:
        if self.cancelled:
            return BrowserScanStatus.CANCELLED
        if self.total_count == 0:
            return BrowserScanStatus.EMPTY_DIRECTORY
        return BrowserScanStatus.NORMAL_DIRECTORY


@dataclass(frozen=True)
class BrowserScanError:
    path: str
    generation: int
    status: BrowserScanStatus
    message: str


def scan_entry_from_dir_entry(
    entry: os.DirEntry[str],
    visibility_policy: BrowserVisibilityPolicy = LEGACY_SUPPORTED_ITEMS_POLICY,
) -> BrowserScanEntry | None:
    name = entry.name
    if (
        name in {".", ".."}
        or name.startswith("~$")
        or name.endswith(_INCOMPLETE_DOWNLOAD_SUFFIXES)
    ):
        return None
    if FileOperationArtifactPolicy.is_internal_operation_artifact(name):
        FileOperationArtifactPolicy.record_orphan(entry.path)
        return None

    attributes = 0
    try:
        entry_stat = entry.stat(follow_symlinks=False)
        attributes = getattr(entry_stat, "st_file_attributes", 0)
    except OSError:
        entry_stat = None
    hidden, system = filesystem_visibility_flags(name, attributes)

    try:
        if entry.is_dir(follow_symlinks=False):
            item_kind = "folder"
            preview_kind = "folder_cover"
            supported = True
            is_directory = True
        elif entry.is_file(follow_symlinks=False):
            suffix = Path(name).suffix.lower()
            if suffix in ARCHIVE_EXTENSIONS:
                if (
                    suffix in EXTERNAL_ARCHIVE_EXTENSIONS
                    and not is_supported_archive_candidate(name)
                ):
                    item_kind = "other"
                    preview_kind = "windows_shell"
                    supported = False
                else:
                    item_kind = "archive"
                    preview_kind = "archive"
                    supported = True
            elif suffix in SUPPORTED_EXTENSIONS:
                item_kind = "image"
                preview_kind = "image"
                supported = True
            elif suffix in PDF_EXTENSIONS:
                item_kind = "pdf"
                preview_kind = "pdf"
                supported = True
            elif suffix in _TEXT_PREVIEW_EXTENSIONS:
                item_kind = "other"
                preview_kind = "text"
                supported = False
            elif suffix in _VIDEO_PREVIEW_EXTENSIONS:
                item_kind = "other"
                preview_kind = "video"
                supported = False
            else:
                item_kind = "other"
                preview_kind = "windows_shell"
                supported = False
            is_directory = False
        else:
            return None
    except OSError:
        return None
    if not visibility_policy.allows(
        hidden=hidden,
        system=system,
        supported=supported,
        is_directory=is_directory,
    ):
        return None

    if entry_stat is None:
        try:
            entry_stat = entry.stat(follow_symlinks=False)
        except OSError:
            entry_stat = None
    return BrowserScanEntry(
        path=str(Path(entry.path).absolute()),
        display_name=name,
        item_kind=item_kind,
        modified_time_ns=(
            entry_stat.st_mtime_ns if entry_stat is not None else None
        ),
        file_size=(
            None
            if item_kind == "folder" or entry_stat is None
            else entry_stat.st_size
        ),
        extension=Path(name).suffix.casefold(),
        hidden=hidden,
        system=system,
        openable_by_nivisviewer=supported,
        can_generate_preview=True,
        preview_kind=preview_kind,
    )


def scan_directory(
    request: BrowserScanRequest,
    cancelled: Event,
    emit_batch: Callable[[BrowserScanBatch], None],
) -> BrowserScanCompleted | BrowserScanError:
    from .browser_model import browser_item_from_scan_entry

    target = Path(request.path)
    batch_size = max(1, min(1024, int(request.batch_size)))
    batch: list[BrowserScanEntry] = []
    prepared_items: list[BrowserItem] = []
    total_count = 0
    if cancelled.is_set():
        return BrowserScanCompleted(
            request.path,
            request.generation,
            0,
            cancelled=True,
        )
    if request.trace_id:
        performance_trace.mark(
            request.trace_id,
            "scanner.path_check.begin",
            request.path,
        )
    try:
        if request.trace_id:
            performance_trace.mark(
                request.trace_id,
                "scanner.scandir.begin",
                request.path,
            )
        with os.scandir(target) as entries:
            if request.trace_id:
                performance_trace.mark(
                    request.trace_id,
                    "scanner.path_check.complete",
                    request.path,
                )
            for entry in entries:
                if cancelled.is_set():
                    return BrowserScanCompleted(
                        request.path,
                        request.generation,
                        total_count,
                        cancelled=True,
                    )
                scanned = scan_entry_from_dir_entry(
                    entry,
                    request.visibility_policy,
                )
                if scanned is None:
                    continue
                try:
                    prepared_items.append(
                        browser_item_from_scan_entry(scanned)
                    )
                except ValueError:
                    continue
                batch.append(scanned)
                total_count += 1
                if len(batch) >= batch_size:
                    if request.trace_id and total_count == len(batch):
                        performance_trace.mark(
                            request.trace_id,
                            "scanner.first_batch.created",
                            str(total_count),
                        )
                    emit_batch(
                        BrowserScanBatch(
                            request.path,
                            request.generation,
                            tuple(batch),
                            final_items_pending=True,
                        )
                    )
                    batch.clear()
                    if cancelled.is_set():
                        return BrowserScanCompleted(
                            request.path,
                            request.generation,
                            total_count,
                            cancelled=True,
                        )
    except FileNotFoundError as exc:
        if request.trace_id:
            performance_trace.mark(
                request.trace_id,
                "scanner.path_check.complete",
                BrowserScanStatus.NOT_FOUND.value,
            )
        return BrowserScanError(
            request.path,
            request.generation,
            BrowserScanStatus.NOT_FOUND,
            f"フォルダを読み込めません: {request.path} ({exc})",
        )
    except NotADirectoryError as exc:
        if request.trace_id:
            performance_trace.mark(
                request.trace_id,
                "scanner.path_check.complete",
                BrowserScanStatus.NOT_DIRECTORY.value,
            )
        return BrowserScanError(
            request.path,
            request.generation,
            BrowserScanStatus.NOT_DIRECTORY,
            f"フォルダを読み込めません: {request.path} ({exc})",
        )
    except PermissionError as exc:
        if request.trace_id:
            performance_trace.mark(
                request.trace_id,
                "scanner.path_check.complete",
                BrowserScanStatus.ACCESS_DENIED.value,
            )
        return BrowserScanError(
            request.path,
            request.generation,
            BrowserScanStatus.ACCESS_DENIED,
            f"フォルダを読み込めません: {request.path} ({exc})",
        )
    except OSError as exc:
        if request.trace_id:
            performance_trace.mark(
                request.trace_id,
                "scanner.path_check.complete",
                BrowserScanStatus.IO_ERROR.value,
            )
        return BrowserScanError(
            request.path,
            request.generation,
            BrowserScanStatus.IO_ERROR,
            f"フォルダを読み込めません: {request.path} ({exc})",
        )
    else:
        if batch and not cancelled.is_set():
            if request.trace_id and total_count == len(batch):
                performance_trace.mark(
                    request.trace_id,
                    "scanner.first_batch.created",
                    str(total_count),
                )
            emit_batch(
                BrowserScanBatch(
                    request.path,
                    request.generation,
                    tuple(batch),
                    final_items_pending=True,
                )
            )
        if cancelled.is_set():
            return BrowserScanCompleted(
                request.path,
                request.generation,
                total_count,
                cancelled=True,
            )
        ordered_items = tuple(
            request.sort_policy.sorted_items(prepared_items)
        )
        return BrowserScanCompleted(
            request.path,
            request.generation,
            total_count,
            cancelled=cancelled.is_set(),
            prepared_items=ordered_items,
            sort_policy=request.sort_policy,
        )



class _BrowserScanWorkerSignals(QObject):
    batch_ready = Signal(object)
    completed = Signal(object)
    failed = Signal(object)


class _BrowserScanWorker(QRunnable):
    def __init__(self, request: BrowserScanRequest, cancelled: Event) -> None:
        super().__init__()
        self.request = request
        self.cancelled = cancelled
        self.signals = _BrowserScanWorkerSignals()

    @Slot()
    def run(self) -> None:
        if self.request.trace_id:
            performance_trace.mark(
                self.request.trace_id,
                "scanner.worker.begin",
                self.request.path,
            )
        result = scan_directory(
            self.request,
            self.cancelled,
            self.signals.batch_ready.emit,
        )
        if isinstance(result, BrowserScanError):
            self.signals.failed.emit(result)
        else:
            self.signals.completed.emit(result)


class BrowserDirectoryScanner(QObject):
    batch_ready = Signal(object)
    scan_completed = Signal(object)
    scan_failed = Signal(object)

    def __init__(self, parent: QObject | None = None, *, max_workers: int = 2) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, min(2, int(max_workers))))
        self._cancel_events: dict[int, Event] = {}
        self._workers: dict[int, _BrowserScanWorker] = {}
        self._lock = Lock()
        self._closed = False

    def start(self, request: BrowserScanRequest) -> bool:
        if self._closed:
            return False
        cancelled = Event()
        worker = _BrowserScanWorker(request, cancelled)
        worker.signals.batch_ready.connect(self._relay_batch)
        worker.signals.completed.connect(self._relay_completed)
        worker.signals.failed.connect(self._relay_failed)
        with self._lock:
            self._cancel_events[request.generation] = cancelled
            self._workers[request.generation] = worker
        if request.trace_id:
            performance_trace.mark(
                request.trace_id,
                "scanner.request.registered",
                request.path,
            )
        self._pool.start(worker, int(request.priority))
        return True

    def cancel(self, generation: int) -> None:
        with self._lock:
            cancelled = self._cancel_events.get(generation)
            worker = self._workers.get(generation)
        if cancelled is not None:
            cancelled.set()
        if worker is not None:
            try:
                removed = self._pool.tryTake(worker)
            except RuntimeError:
                removed = False
            if removed:
                self._forget(generation)

    def cancel_all(self) -> None:
        with self._lock:
            generations = tuple(self._cancel_events)
        for generation in generations:
            self.cancel(generation)
        self._pool.clear()

    def wait_for_done(self, msecs: int = 5000) -> bool:
        return self._pool.waitForDone(max(0, int(msecs)))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancel_all()
        with self._lock:
            has_running_workers = bool(self._workers)
        if has_running_workers:
            # Keep the QThreadPool alive after its BrowserWindow is deleted.
            # Running blocking I/O is never synchronously awaited here.
            _RETIRED_SCANNERS.add(self)

    @Slot(object)
    def _relay_batch(self, batch: BrowserScanBatch) -> None:
        if not self._closed:
            self.batch_ready.emit(batch)

    @Slot(object)
    def _relay_completed(self, result: BrowserScanCompleted) -> None:
        self._forget(result.generation)
        self._release_if_idle()
        if not self._closed:
            self.scan_completed.emit(result)

    @Slot(object)
    def _relay_failed(self, error: BrowserScanError) -> None:
        self._forget(error.generation)
        self._release_if_idle()
        if not self._closed:
            self.scan_failed.emit(error)

    def _forget(self, generation: int) -> None:
        with self._lock:
            self._cancel_events.pop(generation, None)
            self._workers.pop(generation, None)

    def _release_if_idle(self) -> None:
        if not self._closed:
            return
        with self._lock:
            is_idle = not self._workers
        if is_idle:
            _RETIRED_SCANNERS.discard(self)
