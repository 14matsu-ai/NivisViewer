from __future__ import annotations

from .i18n import tr


from dataclasses import dataclass
import inspect
import logging
from pathlib import Path
from threading import Event
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from .archive_backend import ArchiveErrorCode
from .image_cache import ImageCache
from .image_work_coordinator import ImageWorkCoordinator
from .image_source import (
    FolderImageSource,
    FolderListingSnapshot,
    ImageSource,
    ImageSourceError,
    SevenZipImageSource,
    ZipImageSource,
    create_image_source,
)
from .folder_raster_book_runtime import FolderRasterBookRuntime
from .page_model import PageModel
from .performance_trace import performance_trace
from .raster_book_runtime import RasterBookRuntime
from .viewer_page_list_runtime import ViewerPageListRuntime
from .zip_raster_book_runtime import ZipRasterBookRuntime


SourceFactory = Callable[..., tuple[ImageSource, str | None]]
_LOG = logging.getLogger("nivisviewer.book_session")


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
                self._close_source(source)
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
                self._close_source(source)
                source = None
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
                self._close_source(source)
                source = None
            result = _PreparedBook(
                self.requested_path,
                None,
                None,
                (),
                self.generation,
                error=ImageSourceError(
                    tr('本を開けません: {p0}', p0=self.requested_path),
                    code="open_failed",
                ),
                trace_id=self.trace_id,
            )
            result.error.__cause__ = exc
        self.signals.completed.emit(result)

    @staticmethod
    def _close_source(source: ImageSource) -> None:
        try:
            source.close()
        except Exception:
            _LOG.exception(
                "Prepared image source cleanup failed source=%s",
                source.source_path,
            )


_RETIRED_BOOK_OPEN_POOLS: set[QThreadPool] = set()
_RETIRED_BOOK_SESSIONS: set[BookSession] = set()
_DEFAULT_PAGE_LIST_CACHE_BYTES = 64 * 1024 * 1024


class BookSession(QObject):
    book_opened = Signal(object)
    book_closed = Signal()
    page_changed = Signal()
    error_occurred = Signal(str)
    async_opened = Signal(object)
    async_open_failed = Signal(object)
    viewer_runtime_changed = Signal(object)
    page_list_runtime_changed = Signal(object)

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
        self._image_work_coordinator = image_work_coordinator
        self.current_path: Path | None = None
        self.source: ImageSource | None = None
        self.viewer_runtime: RasterBookRuntime | None = None
        self.page_list_runtime: ViewerPageListRuntime | None = None
        self._pending_page_list_source: ImageSource | None = None
        self._pending_page_list_image_ids: tuple[str, ...] = ()
        self._page_list_cache_byte_budget = _DEFAULT_PAGE_LIST_CACHE_BYTES
        self._viewer_runtime_hard_limit_bytes = (
            self.image_cache.cache_byte_budget_bytes
        )
        self._viewer_runtime_soft_target_bytes = (
            self._viewer_runtime_hard_limit_bytes * 7 // 8
        )
        # The active book epoch must change only when the installed source
        # changes.  Pending-open tokens are separate so a failed/cancelled
        # replacement cannot invalidate the Viewer runtime of the book that
        # remains on screen.
        self.generation = 0
        self._open_generation = 0
        self._source_factory = source_factory
        self._retired_sources: dict[int, ImageSource] = {}
        self._retired_viewer_runtimes: dict[int, RasterBookRuntime] = {}
        self._retired_page_list_runtimes: dict[
            int, ViewerPageListRuntime
        ] = {}
        self._retired_cleanup_deferred = False
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
    def page_list_runtime_deferred(self) -> bool:
        """Whether the active raster book is awaiting post-paint PageList."""

        return (
            self.source is not None
            and self._pending_page_list_source is self.source
            and self.page_list_runtime is None
        )

    @property
    def book_key(self) -> str:
        if self.source is None:
            return ""
        return str(self.source.source_path)

    @property
    def folder_listing_snapshot(self) -> FolderListingSnapshot | None:
        """Return the immutable Browser order owned by the active folder book."""

        source = self.source
        if not isinstance(source, FolderImageSource):
            return None
        return source.listing_snapshot

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
        self._open_generation += 1
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
            if not image_ids:
                raise ImageSourceError(
                    tr('表示可能な画像がありません。'),
                    code="no_images",
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
            error = ImageSourceError(tr('本を開けません: {p0}', p0=requested_path))
            self.error_occurred.emit(str(error))
            raise error from exc

        old_source = self.source
        self.generation += 1
        self.source = new_source
        self.current_path = requested_path
        self._replace_viewer_runtime(new_source)
        self._replace_page_list_runtime(
            new_source,
            tuple(self.model.image_ids),
        )
        if self.viewer_runtime is not None:
            self.image_cache.suspend_for_book_runtime()
        else:
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
        self._open_generation += 1
        self._shutdown = False
        generation = self._open_generation
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
        if cancelled is None:
            return
        cancelled.set()
        queued_generation: int | None = None
        queued_worker: _BookOpenWorker | None = None
        for generation, worker in tuple(self._open_workers.items()):
            if worker.cancelled is cancelled:
                queued_generation = generation
                queued_worker = worker
                break
        if queued_worker is None or queued_generation is None:
            return
        try:
            removed = self._open_pool.tryTake(queued_worker)
        except RuntimeError:
            removed = False
        if removed:
            self._open_workers.pop(queued_generation, None)
            if not self._open_workers:
                _RETIRED_BOOK_OPEN_POOLS.discard(self._open_pool)

    def wait_for_async(self, msecs: int = 5000) -> bool:
        return self._open_pool.waitForDone(max(0, int(msecs)))

    def close_book(self) -> None:
        self.cancel_pending_open()
        self._open_generation += 1
        old_source = self.source
        self._replace_viewer_runtime(None)
        self._replace_page_list_runtime(None, ())
        had_book = old_source is not None
        self.generation += 1
        self.image_cache.clear()
        self.model.clear_source()
        self.source = None
        self.current_path = None
        if old_source is not None:
            self._retire_source(old_source)
        self.release_retired_book_resources()
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
        if self._open_workers:
            _RETIRED_BOOK_OPEN_POOLS.add(self._open_pool)
        runtime_deadline = max(0, int(wait_msecs))
        completed_runtimes = tuple(
            runtime
            for runtime in tuple(self._retired_viewer_runtimes.values())
            if runtime.shutdown(wait_msecs=runtime_deadline)
        )
        for runtime in completed_runtimes:
            if self._retired_viewer_runtimes.get(id(runtime)) is runtime:
                self._finalize_viewer_runtime(runtime)
        completed_page_list_runtimes = tuple(
            runtime
            for runtime in tuple(
                self._retired_page_list_runtimes.values()
            )
            if runtime.shutdown(wait_msecs=runtime_deadline)
        )
        for runtime in completed_page_list_runtimes:
            if (
                self._retired_page_list_runtimes.get(id(runtime))
                is runtime
            ):
                self._finalize_page_list_runtime(runtime)
        if self.image_cache.wait_for_owned_tasks(wait_msecs):
            for source in tuple(self._retired_sources.values()):
                if any(
                    runtime.source is source
                    and runtime.has_unfinished_tasks()
                    for runtime in self._retired_viewer_runtimes.values()
                ) or any(
                    runtime.source is source
                    and runtime.has_unfinished_tasks()
                    for runtime in self._retired_page_list_runtimes.values()
                ):
                    continue
                retired = self._retired_sources.pop(id(source), None)
                if retired is source:
                    self._close_source(source)
        if self._has_owned_async_work():
            self.setParent(None)
            _RETIRED_BOOK_SESSIONS.add(self)
        self._maybe_finalize_shutdown()

    @Slot(object)
    def _on_async_prepared(self, result: _PreparedBook) -> None:
        self._open_workers.pop(result.generation, None)
        if not self._open_workers:
            _RETIRED_BOOK_OPEN_POOLS.discard(self._open_pool)
        if self._shutdown or result.generation != self._open_generation:
            if result.source is not None:
                self._close_source(result.source)
            self._maybe_finalize_shutdown()
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
                tr('本を開けません: {p0}', p0=result.requested_path)
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
        if not result.image_ids:
            self._close_source(result.source)
            error = ImageSourceError(
                tr('表示可能な画像がありません。'),
                code="no_images",
            )
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
            error = ImageSourceError(tr('本を開けません: {p0}', p0=result.requested_path))
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
        self.generation += 1
        self.source = result.source
        self.current_path = result.requested_path
        raster_fast_path = isinstance(
            result.source,
            (ZipImageSource, FolderImageSource),
        )
        if not raster_fast_path:
            # A rapid A(raster) -> B(raster, not painted) -> C(legacy) chain
            # has no later raster paint boundary for A.  Release any older
            # deferred owners before installing C; running callbacks retain
            # their sources through the normal idle-drain maps.
            self.release_retired_book_resources()
        self._replace_viewer_runtime(
            result.source,
            defer_retired_cleanup=raster_fast_path,
        )
        self._replace_page_list_runtime(
            result.source,
            tuple(self.model.image_ids),
            defer=raster_fast_path,
            defer_retired_cleanup=raster_fast_path,
        )
        trace_id = result.trace_id
        if self.viewer_runtime is not None:
            # Raster books have one source owner.  Do not copy the full page
            # list into ImageCache only to suspend that legacy cache when the
            # Window issues its first runtime request.
            self.image_cache.suspend_for_book_runtime()
        else:
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
            generation=self.generation,
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
        if self._source_has_owned_async_work(source):
            self._retired_sources[id(source)] = source
            return
        self._close_source(source)

    def _release_retired_source(self, source: ImageSource) -> None:
        self._try_release_retired_source(source)
        self._maybe_finalize_shutdown()

    def _source_has_owned_async_work(self, source: ImageSource) -> bool:
        if self.image_cache.has_in_flight_for_source(source):
            return True
        if any(
            runtime.source is source and runtime.has_unfinished_tasks()
            for runtime in self._retired_viewer_runtimes.values()
        ):
            return True
        return any(
            runtime.source is source and runtime.has_unfinished_tasks()
            for runtime in self._retired_page_list_runtimes.values()
        )

    def _try_release_retired_source(self, source: ImageSource) -> bool:
        retired = self._retired_sources.get(id(source))
        if retired is not source or self._source_has_owned_async_work(source):
            return False
        self._retired_sources.pop(id(source), None)
        self._close_source(source)
        return True

    def _replace_viewer_runtime(
        self,
        source: ImageSource | None,
        *,
        defer_retired_cleanup: bool = False,
    ) -> None:
        old_runtime = self.viewer_runtime
        runtime_type: type[RasterBookRuntime] | None
        if isinstance(source, ZipImageSource):
            runtime_type = ZipRasterBookRuntime
        elif isinstance(source, FolderImageSource):
            runtime_type = FolderRasterBookRuntime
        elif isinstance(source, SevenZipImageSource):
            # Share ZIP/folder admission, source tiers and atomic commits.
            runtime_type = RasterBookRuntime
        else:
            runtime_type = None
        hard, soft = self._source_runtime_limits(source)
        new_runtime = (
            runtime_type(
                source,
                self.generation,
                self,
                image_work_coordinator=self._image_work_coordinator,
                cache_byte_budget=hard,
                cache_soft_target_bytes=soft,
            )
            if runtime_type is not None and source is not None
            else None
        )
        self.viewer_runtime = new_runtime
        if new_runtime is not None:
            new_runtime.idle.connect(self._release_retired_viewer_runtime)
        if old_runtime is not None and old_runtime is not new_runtime:
            self._retire_viewer_runtime(
                old_runtime,
                defer_cleanup=defer_retired_cleanup,
            )
        self.viewer_runtime_changed.emit(new_runtime)

    def _retire_viewer_runtime(
        self,
        runtime: RasterBookRuntime,
        *,
        defer_cleanup: bool = False,
    ) -> None:
        if defer_cleanup:
            self._retired_cleanup_deferred = True
            self._retired_viewer_runtimes[id(runtime)] = runtime
            runtime.retire()
            return
        completed = runtime.shutdown(wait_msecs=0)
        if completed:
            self._finalize_viewer_runtime(runtime)
            return
        self._retired_viewer_runtimes[id(runtime)] = runtime

    @Slot(object)
    def _release_retired_viewer_runtime(self, runtime: object) -> None:
        if not isinstance(runtime, RasterBookRuntime):
            return
        retired = self._retired_viewer_runtimes.get(id(runtime))
        if (
            retired is not runtime
            or self._retired_cleanup_deferred
            or runtime.has_unfinished_tasks()
        ):
            return
        source = runtime.source
        self._finalize_viewer_runtime(runtime)
        self._try_release_retired_source(source)
        self._maybe_finalize_shutdown()

    def _finalize_viewer_runtime(
        self,
        runtime: RasterBookRuntime,
    ) -> None:
        self._retired_viewer_runtimes.pop(id(runtime), None)
        runtime.shutdown(wait_msecs=0)
        runtime.setParent(None)
        runtime.deleteLater()

    def _replace_page_list_runtime(
        self,
        source: ImageSource | None,
        image_ids: tuple[str, ...],
        *,
        defer: bool = False,
        defer_retired_cleanup: bool = False,
    ) -> None:
        old_runtime = self.page_list_runtime
        self._pending_page_list_source = source if defer else None
        self._pending_page_list_image_ids = tuple(image_ids) if defer else ()
        new_runtime = (
            ViewerPageListRuntime(
                source,
                tuple(image_ids),
                self.generation,
                self,
                image_work_coordinator=self._image_work_coordinator,
                cache_byte_budget=self._page_list_cache_byte_budget,
            )
            if source is not None and not defer
            else None
        )
        self.page_list_runtime = new_runtime
        if new_runtime is not None:
            new_runtime.idle.connect(
                self._release_retired_page_list_runtime
            )
        if old_runtime is not None and old_runtime is not new_runtime:
            self._retire_page_list_runtime(
                old_runtime,
                defer_cleanup=defer_retired_cleanup,
            )
        self.page_list_runtime_changed.emit(new_runtime)

    def ensure_page_list_runtime(self) -> ViewerPageListRuntime | None:
        """Materialize the optional PageList owner after first-frame paint."""

        runtime = self.page_list_runtime
        if runtime is not None:
            return runtime
        source = self._pending_page_list_source
        if source is None or source is not self.source:
            return None
        image_ids = self._pending_page_list_image_ids
        self._replace_page_list_runtime(source, image_ids, defer=False)
        return self.page_list_runtime

    def set_viewer_runtime_memory_limits(
        self,
        *,
        hard_limit_bytes: int,
        soft_target_bytes: int,
    ) -> None:
        """Keep raster runtime construction independent from ImageCache."""

        hard = max(1, int(hard_limit_bytes))
        soft = max(1, min(hard, int(soft_target_bytes)))
        self._viewer_runtime_hard_limit_bytes = hard
        self._viewer_runtime_soft_target_bytes = soft
        runtime = self.viewer_runtime
        if runtime is not None:
            hard, soft = self._source_runtime_limits(runtime.source)
            runtime.set_memory_limits(
                hard_limit_bytes=hard,
                soft_target_bytes=soft,
            )

    def _source_runtime_limits(self, source: ImageSource | None) -> tuple[int, int]:
        hard = self._viewer_runtime_hard_limit_bytes
        soft = self._viewer_runtime_soft_target_bytes
        if isinstance(source, SevenZipImageSource):
            # Payload retention is reserved WITHIN the configured Viewer
            # cache budget, not an independently growing extra allowance.
            reserve = min(64 * 1024 * 1024, hard // 8)
            source.set_payload_cache_budget(reserve)
            reserve = source.payload_cache_budget
            hard = max(1, hard - reserve)
            soft = max(1, min(hard, soft - reserve))
        return hard, soft

    def _retire_page_list_runtime(
        self,
        runtime: ViewerPageListRuntime,
        *,
        defer_cleanup: bool = False,
    ) -> None:
        if defer_cleanup:
            self._retired_cleanup_deferred = True
            self._retired_page_list_runtimes[id(runtime)] = runtime
            runtime.retire()
            return
        completed = runtime.shutdown(wait_msecs=0)
        if completed:
            self._finalize_page_list_runtime(runtime)
            return
        self._retired_page_list_runtimes[id(runtime)] = runtime

    @Slot(object)
    def _release_retired_page_list_runtime(self, runtime: object) -> None:
        if not isinstance(runtime, ViewerPageListRuntime):
            return
        retired = self._retired_page_list_runtimes.get(id(runtime))
        if (
            retired is not runtime
            or self._retired_cleanup_deferred
            or runtime.has_unfinished_tasks()
        ):
            return
        source = runtime.source
        self._finalize_page_list_runtime(runtime)
        self._try_release_retired_source(source)
        self._maybe_finalize_shutdown()

    def _finalize_page_list_runtime(
        self,
        runtime: ViewerPageListRuntime,
    ) -> None:
        self._retired_page_list_runtimes.pop(id(runtime), None)
        runtime.shutdown(wait_msecs=0)
        runtime.setParent(None)
        runtime.deleteLater()

    def release_retired_book_resources(self) -> None:
        """Reclaim the preceding book after the replacement frame paints."""

        self._retired_cleanup_deferred = False
        for runtime in tuple(self._retired_viewer_runtimes.values()):
            if not runtime.has_unfinished_tasks():
                self._finalize_viewer_runtime(runtime)
        for runtime in tuple(self._retired_page_list_runtimes.values()):
            if not runtime.has_unfinished_tasks():
                self._finalize_page_list_runtime(runtime)
        for source in tuple(self._retired_sources.values()):
            self._try_release_retired_source(source)

    def _has_owned_async_work(self) -> bool:
        return bool(
            self._open_workers
            or self._retired_sources
            or any(
                runtime.has_unfinished_tasks()
                for runtime in self._retired_viewer_runtimes.values()
            )
            or any(
                runtime.has_unfinished_tasks()
                for runtime in self._retired_page_list_runtimes.values()
            )
            or self.image_cache.has_unfinished_tasks()
        )

    def _maybe_finalize_shutdown(self) -> None:
        if not self._shutdown or self._has_owned_async_work():
            return
        _RETIRED_BOOK_OPEN_POOLS.discard(self._open_pool)
        if self in _RETIRED_BOOK_SESSIONS:
            _RETIRED_BOOK_SESSIONS.discard(self)
            self.deleteLater()

    def _close_source(self, source: ImageSource) -> None:
        try:
            source.close()
        except Exception as exc:
            self.error_occurred.emit(tr('画像ソースを閉じられません: {p0}', p0=exc))


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
