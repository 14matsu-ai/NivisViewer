from __future__ import annotations

from dataclasses import dataclass
import inspect
from pathlib import Path
from threading import Event
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from .archive_backend import ArchiveErrorCode
from .image_cache import ImageCache
from .image_work_coordinator import ImageWorkCoordinator
from .image_source import (
    FolderListingSnapshot,
    ImageSource,
    ImageSourceError,
    create_image_source,
)
from .page_model import PageModel
from .performance_trace import performance_trace


SourceFactory = Callable[..., tuple[ImageSource, str | None]]


@dataclass(frozen=True)
class BookOpened:
    requested_path: Path
    source_path: Path
    selected_image: str | None
    total_pages: int
    generation: int
    requested_page_identity: str | None = None
    resolved_page_index: int = -1
    display_unit_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class AsyncBookOpenFailed:
    requested_path: Path
    generation: int
    message: str
    code: str | None
    cancelled: bool = False


@dataclass(frozen=True)
class _PreparedBook:
    requested_path: Path
    source: ImageSource | None
    selected_image: str | None
    image_ids: tuple[str, ...]
    generation: int
    error: ImageSourceError | None = None
    cancelled: bool = False
    trace_id: int = 0


class _BookOpenSignals(QObject):
    completed = Signal(object)


class _BookOpenWorker(QRunnable):
    def __init__(
        self,
        factory: SourceFactory,
        requested_path: Path,
        generation: int,
        cancelled: Event,
        *,
        recursive_folder: bool,
        sort_descending: bool,
        trace_id: int = 0,
        folder_snapshot: FolderListingSnapshot | None = None,
    ) -> None:
        super().__init__()
        self.factory = factory
        self.requested_path = requested_path
        self.generation = generation
        self.cancelled = cancelled
        self.recursive_folder = recursive_folder
        self.sort_descending = sort_descending
        self.trace_id = int(trace_id)
        self.folder_snapshot = folder_snapshot
        self.signals = _BookOpenSignals()

    @Slot()
    def run(self) -> None:
        source: ImageSource | None = None
        try:
            performance_trace.mark(
                self.trace_id,
                "image_source.prepare.started",
                str(self.requested_path),
            )
            source, selected = _invoke_source_factory(
                self.factory,
                self.requested_path,
                recursive_folder=self.recursive_folder,
                sort_descending=self.sort_descending,
                cancel_token=self.cancelled,
                folder_snapshot=self.folder_snapshot,
            )
            performance_trace.mark(
                self.trace_id,
                "image_source.prepare.completed",
            )
            images = tuple(source.list_images())
            performance_trace.mark(
                self.trace_id,
                "page_list.completed",
                f"pages={len(images)}",
            )
            if self.cancelled.is_set():
                source.close()
                source = None
                result = _PreparedBook(
                    self.requested_path,
                    None,
                    None,
                    (),
                    self.generation,
                    cancelled=True,
                    trace_id=self.trace_id,
                )
            else:
                result = _PreparedBook(
                    self.requested_path,
                    source,
                    selected,
                    images,
                    self.generation,
                    trace_id=self.trace_id,
                )
        except ImageSourceError as exc:
            if source is not None:
                source.close()
            result = _PreparedBook(
                self.requested_path,
                None,
                None,
                (),
                self.generation,
                error=exc,
                cancelled=(
                    self.cancelled.is_set()
                    or exc.code == ArchiveErrorCode.PROCESS_CANCELLED.value
                ),
                trace_id=self.trace_id,
            )
        except Exception as exc:
            if source is not None:
                source.close()
            result = _PreparedBook(
                self.requested_path,
                None,
                None,
                (),
                self.generation,
                error=ImageSourceError(
                    f"本を開けません: {self.requested_path}",
                    code="open_failed",
                ),
                trace_id=self.trace_id,
            )
            result.error.__cause__ = exc
        self.signals.completed.emit(result)


_RETIRED_BOOK_OPEN_POOLS: set[QThreadPool] = set()
_RETIRED_BOOK_SESSIONS: set[BookSession] = set()


class BookSession(QObject):
    book_opened = Signal(object)
    book_closed = Signal()
    page_changed = Signal()
    error_occurred = Signal(str)
    async_opened = Signal(object)
    async_open_failed = Signal(object)

    def __init__(
        self,
        cache_size: int = 10,
        parent: QObject | None = None,
        *,
        source_factory: SourceFactory = create_image_source,
        image_work_coordinator: ImageWorkCoordinator | None = None,
    ) -> None:
        super().__init__(parent)
        self.model = PageModel()
        self.image_cache = ImageCache(
            cache_size,
            self,
            image_work_coordinator=image_work_coordinator,
        )
        self.current_path: Path | None = None
        self.source: ImageSource | None = None
        self.generation = 0
        self._source_factory = source_factory
        self._retired_sources: dict[int, ImageSource] = {}
        self._open_pool = QThreadPool()
        self._open_pool.setMaxThreadCount(1)
        self._open_cancel: Event | None = None
        self._open_workers: dict[int, _BookOpenWorker] = {}
        self._shutdown = False
        self.image_cache.sourceIdle.connect(self._release_retired_source)

    @property
    def is_open(self) -> bool:
        return self.source is not None

    @property
    def book_key(self) -> str:
        if self.source is None:
            return ""
        return str(self.source.source_path)

    def open_book(
        self,
        path: str | Path,
        *,
        recursive_folder: bool = False,
        sort_descending: bool = False,
        trace_id: int = 0,
        folder_snapshot: FolderListingSnapshot | None = None,
    ) -> BookOpened:
        self.cancel_pending_open()
        requested_path = Path(path)
        self.generation += 1
        self._shutdown = False

        new_source: ImageSource | None = None
        try:
            performance_trace.mark(
                trace_id,
                "image_source.prepare.started",
                str(requested_path),
            )
            new_source, selected_image = _invoke_source_factory(
                self._source_factory,
                requested_path,
                recursive_folder=recursive_folder,
                sort_descending=sort_descending,
                cancel_token=Event(),
                folder_snapshot=folder_snapshot,
            )
            performance_trace.mark(trace_id, "image_source.prepare.completed")
            image_ids = new_source.list_images()
            performance_trace.mark(
                trace_id,
                "page_list.completed",
                f"pages={len(image_ids)}",
            )
            self.model.set_prepared_source(
                new_source,
                image_ids,
                selected_image,
            )
            performance_trace.mark(
                trace_id,
                "page_model.constructed",
                f"pages={self.model.total_pages}",
            )
        except ImageSourceError as exc:
            if new_source is not None:
                self._close_source(new_source)
            self.error_occurred.emit(str(exc))
            raise
        except Exception as exc:
            if new_source is not None:
                self._close_source(new_source)
            error = ImageSourceError(f"本を開けません: {requested_path}")
            self.error_occurred.emit(str(error))
            raise error from exc

        old_source = self.source
        self.source = new_source
        self.current_path = requested_path
        self.image_cache.set_source(
            new_source,
            self.model.image_ids,
            trace_id=trace_id,
        )
        if old_source is not None and old_source is not new_source:
            self._retire_source(old_source)

        opened = BookOpened(
            requested_path=requested_path,
            source_path=new_source.source_path,
            selected_image=selected_image,
            total_pages=self.model.total_pages,
            generation=self.generation,
            requested_page_identity=(
                new_source.page_identity(selected_image)
                if selected_image is not None
                else None
            ),
            resolved_page_index=self.model.focused_index,
            display_unit_indices=tuple(
                slot.page_index for slot in self.model.spread_at().slots
            ),
        )
        self.book_opened.emit(opened)
        return opened

    def open_book_async(
        self,
        path: str | Path,
        *,
        recursive_folder: bool = False,
        sort_descending: bool = False,
        trace_id: int = 0,
        folder_snapshot: FolderListingSnapshot | None = None,
    ) -> int:
        self.cancel_pending_open()
        self.generation += 1
        self._shutdown = False
        generation = self.generation
        cancelled = Event()
        worker = _BookOpenWorker(
            self._source_factory,
            Path(path),
            generation,
            cancelled,
            recursive_folder=recursive_folder,
            sort_descending=sort_descending,
            trace_id=trace_id,
            folder_snapshot=folder_snapshot,
        )
        worker.signals.completed.connect(self._on_async_prepared)
        self._open_cancel = cancelled
        self._open_workers[generation] = worker
        self._open_pool.start(worker)
        return generation

    def cancel_pending_open(self) -> None:
        cancelled = self._open_cancel
        self._open_cancel = None
        if cancelled is not None:
            cancelled.set()
        self._open_pool.clear()

    def wait_for_async(self, msecs: int = 5000) -> bool:
        return self._open_pool.waitForDone(max(0, int(msecs)))

    def close_book(self) -> None:
        self.cancel_pending_open()
        old_source = self.source
        had_book = old_source is not None
        self.generation += 1
        self.image_cache.clear()
        self.model.clear_source()
        self.source = None
        self.current_path = None
        if old_source is not None:
            self._retire_source(old_source)
        if had_book:
            self.book_closed.emit()

    def notify_page_changed(self) -> None:
        if self.is_open:
            self.page_changed.emit()

    def shutdown(self, wait_msecs: int = 250) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self.close_book()
        if not self._open_pool.waitForDone(0):
            _RETIRED_BOOK_OPEN_POOLS.add(self._open_pool)
        if self.image_cache.wait_for_done(wait_msecs):
            for source in list(self._retired_sources.values()):
                self._close_source(source)
            self._retired_sources.clear()
        else:
            self.setParent(None)
            _RETIRED_BOOK_SESSIONS.add(self)

    @Slot(object)
    def _on_async_prepared(self, result: _PreparedBook) -> None:
        self._open_workers.pop(result.generation, None)
        if not self._open_workers:
            _RETIRED_BOOK_OPEN_POOLS.discard(self._open_pool)
        if self._shutdown or result.generation != self.generation:
            if result.source is not None:
                self._close_source(result.source)
            return
        self._open_cancel = None
        if result.cancelled:
            self.async_open_failed.emit(
                AsyncBookOpenFailed(
                    result.requested_path,
                    result.generation,
                    "",
                    ArchiveErrorCode.PROCESS_CANCELLED.value,
                    cancelled=True,
                )
            )
            return
        if result.error is not None or result.source is None:
            error = result.error or ImageSourceError(
                f"本を開けません: {result.requested_path}"
            )
            failed = AsyncBookOpenFailed(
                result.requested_path,
                result.generation,
                str(error),
                error.code,
            )
            self.error_occurred.emit(str(error))
            self.async_open_failed.emit(failed)
            return

        try:
            self.model.set_prepared_source(
                result.source,
                list(result.image_ids),
                result.selected_image,
            )
            performance_trace.mark(
                result.trace_id,
                "page_model.constructed",
                f"pages={self.model.total_pages}",
            )
        except Exception as exc:
            self._close_source(result.source)
            error = ImageSourceError(f"本を開けません: {result.requested_path}")
            error.__cause__ = exc
            self.error_occurred.emit(str(error))
            self.async_open_failed.emit(
                AsyncBookOpenFailed(
                    result.requested_path,
                    result.generation,
                    str(error),
                    error.code,
                )
            )
            return
        old_source = self.source
        self.source = result.source
        self.current_path = result.requested_path
        trace_id = result.trace_id
        self.image_cache.set_source(
            result.source,
            self.model.image_ids,
            trace_id=trace_id,
        )
        if old_source is not None and old_source is not result.source:
            self._retire_source(old_source)
        opened = BookOpened(
            requested_path=result.requested_path,
            source_path=result.source.source_path,
            selected_image=result.selected_image,
            total_pages=self.model.total_pages,
            generation=result.generation,
            requested_page_identity=(
                result.source.page_identity(result.selected_image)
                if result.selected_image is not None
                else None
            ),
            resolved_page_index=self.model.focused_index,
            display_unit_indices=tuple(
                slot.page_index for slot in self.model.spread_at().slots
            ),
        )
        self.book_opened.emit(opened)
        self.async_opened.emit(opened)

    def _retire_source(self, source: ImageSource) -> None:
        if self.image_cache.has_in_flight_for_source(source):
            self._retired_sources[id(source)] = source
            return
        self._close_source(source)

    def _release_retired_source(self, source: ImageSource) -> None:
        retired = self._retired_sources.pop(id(source), None)
        if retired is source:
            self._close_source(source)
        if (
            self._shutdown
            and not self._retired_sources
            and self.image_cache.wait_for_done(0)
        ):
            _RETIRED_BOOK_SESSIONS.discard(self)
            self.deleteLater()

    def _close_source(self, source: ImageSource) -> None:
        try:
            source.close()
        except Exception as exc:
            self.error_occurred.emit(f"画像ソースを閉じられません: {exc}")


def _invoke_source_factory(
    factory: SourceFactory,
    path: Path,
    *,
    recursive_folder: bool,
    sort_descending: bool,
    cancel_token: Event,
    folder_snapshot: FolderListingSnapshot | None = None,
) -> tuple[ImageSource, str | None]:
    kwargs = {
        "recursive_folder": recursive_folder,
        "sort_descending": sort_descending,
    }
    try:
        signature = inspect.signature(factory)
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        accepts_cancel = (
            "cancel_token" in signature.parameters
            or accepts_kwargs
        )
        accepts_snapshot = (
            "folder_snapshot" in signature.parameters or accepts_kwargs
        )
    except (TypeError, ValueError):
        accepts_cancel = False
        accepts_snapshot = False
    if accepts_cancel:
        kwargs["cancel_token"] = cancel_token
    if accepts_snapshot:
        kwargs["folder_snapshot"] = folder_snapshot
    return factory(path, **kwargs)
