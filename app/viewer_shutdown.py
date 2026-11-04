"""Viewer close ownership and ordered, resumable resource cleanup."""

from __future__ import annotations

from enum import StrEnum
import logging
from typing import TYPE_CHECKING
import weakref

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from .viewer_window import ViewerWindow


_LOG = logging.getLogger(__name__)
# Hidden standalone Viewers must outlive their workers and queued completions.
CLOSING_VIEWERS: set[object] = set()


class ViewerShutdownPhase(StrEnum):
    PRESENTATION = "presentation"
    PDF_LOUPE = "pdf_loupe"
    RENDERING = "rendering"
    BOOK_REQUESTS = "book_requests"
    BOOK_DRAIN = "book_drain"
    AUXILIARY_WORKERS = "auxiliary_workers"
    ARCHIVE_BACKENDS = "archive_backends"
    PDF_SERVICE = "pdf_service"
    PATH_PROBES = "path_probes"
    COMPLETE = "complete"


class ViewerShutdown:
    """Own close state; resume only the unfinished cleanup step.

    The window retains its cheap input fence. This owner is used only for close
    and explicit shutdown, never for page requests or rendering publication.
    The Qt-parented timer cannot outlive the window, and the weak reference does
    not keep an already-destroyed wrapper alive.
    """

    def __init__(self, window: ViewerWindow) -> None:
        self._window = weakref.ref(window)
        self.close_requested = False
        self.closing_source = None
        self.closing_path = None
        self.retired_runtime = None
        self._advancing = False
        self._requesting_close = False
        self._error_reported = False
        self._next_step = 0
        self._steps = (
            (ViewerShutdownPhase.PRESENTATION, self._prepare_presentation),
            (ViewerShutdownPhase.PDF_LOUPE, self._drain_loupe),
            (ViewerShutdownPhase.RENDERING, self._drain_rendering),
            (ViewerShutdownPhase.BOOK_REQUESTS, self._stop_book_requests),
            (ViewerShutdownPhase.BOOK_DRAIN, self._drain_book),
            (ViewerShutdownPhase.AUXILIARY_WORKERS, self._drain_auxiliary),
            (ViewerShutdownPhase.ARCHIVE_BACKENDS, self._close_archives),
            (ViewerShutdownPhase.PDF_SERVICE, self._close_pdf),
            (ViewerShutdownPhase.PATH_PROBES, self._close_path_probes),
        )
        self._timer = QTimer(window)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.continue_close)
        window.destroyed.connect(
            lambda *_args, owner=self._window: CLOSING_VIEWERS.discard(owner())
        )

    @property
    def complete(self) -> bool:
        return self._next_step == len(self._steps)

    @property
    def phase(self) -> ViewerShutdownPhase:
        return (ViewerShutdownPhase.COMPLETE if self.complete
                else self._steps[self._next_step][0])

    def request_close(self) -> None:
        """Hide now, cancel without waiting, and schedule bounded cleanup."""
        if self.close_requested or self.complete:
            return
        window = self._window()
        self.close_requested = True
        self._requesting_close = True
        try:
            self.closing_source = window.book_session.source
            self.closing_path = window.book_session.current_path
            window._shutdown_prepared = True
            CLOSING_VIEWERS.add(window)
            window._cancel_auxiliary_image_workers()
            window.hide()
            self._timer.start(0)
            try:
                self._retire_runtime()
            except Exception:
                self._report_error()
            # Release only this Viewer's Browser admission holds; its hidden
            # page list stays paused while native workers and artifacts drain.
            window._cancel_interactive_open(resume_page_list=False)
            window._release_raster_interactive_lane(resume_page_list=False)
            application = QApplication.instance()
            if application is not None:
                application.removeEventFilter(window)
                application.removeEventFilter(window.fullscreen_chrome)
            window.book_session.cancel_pending_open(invalidate=True)
            window._save_current_reading_position(flush_metadata=False)
        finally:
            self._requesting_close = False
        window.close_started.emit(window)

    def prepare(self, *, wait_msecs: int = 250) -> bool:
        if self.complete:
            return True
        # Signals emitted during cleanup can ask the application to shut down
        # again. They must not rerun the active step or skip its drain fence.
        if self._advancing or self._requesting_close:
            return False
        from .raster_execution_budget import RASTER_EXECUTION_BUDGET
        window = self._window()
        window._shutdown_prepared = True
        self._advancing = True
        try:
            RASTER_EXECUTION_BUDGET.release_owner(window)
            window._cancel_auxiliary_image_workers()
            while not self.complete:
                _, step = self._steps[self._next_step]
                if step(window, wait_msecs) is False:
                    return False
                self._next_step += 1
            return True
        finally:
            self._advancing = False

    def _retire_runtime(self) -> None:
        runtime = self._window()._zip_runtime
        if runtime is not None and runtime is not self.retired_runtime:
            runtime.retire()
            self.retired_runtime = runtime

    def continue_close(self) -> None:
        if not self.close_requested:
            return
        if self._requesting_close or self._advancing:
            self._timer.start(10)
            return
        window = self._window()
        try:
            if self.phase is ViewerShutdownPhase.PRESENTATION:
                self._retire_runtime()
                runtime = window._zip_runtime
                if runtime is not None and not runtime.drain_retired_artifacts(max_items=1):
                    # Keep GUI-owned pixmap disposal bounded between callbacks.
                    self._timer.start(10 if runtime.has_unfinished_tasks() else 8)
                    return
            complete = self.prepare(wait_msecs=0)
        except Exception:
            self._report_error()
            self._timer.start(100)
            return
        if complete:
            window.close()
        else:
            self._timer.start(10)

    def stop_timer(self) -> None:
        self._timer.stop()

    def release_retention(self) -> None:
        CLOSING_VIEWERS.discard(self._window())

    def _report_error(self) -> None:
        if not self._error_reported:
            _LOG.exception("Hidden Viewer shutdown did not complete phase=%s", self.phase)
            self._error_reported = True

    def _prepare_presentation(self, window: ViewerWindow, wait_msecs: int) -> None:
        if not self.close_requested:
            window._save_current_reading_position()
        elif window.metadata_store is not None:
            # The close request already staged the committed position. Never
            # overwrite a newer Viewer's progress when deferred cleanup resumes.
            window.metadata_store.flush()
        window._pending_book_open_projection = None
        window.presentation_state.close()
        window._project_presentation_surface()
        window._zip_runtime_request_timer.stop()
        window._pending_zip_runtime_request = None
        window._presentation_side_effect_timer.stop()
        window._pending_presentation_side_effect_token = None
        window._raster_magnifier_cancel_timer.stop()
        window._viewer_memory_pressure_timer.stop()
        window._raster_zoom_warmup_timer.stop()
        window._raster_zoom_warmup_context = None
        window._page_list_filter_timer.stop()
        window._page_list_viewport_timer.stop()
        window._set_page_list_paused(True)
        for runtime in {window._page_list_runtime, window._staged_page_list_runtime}:
            if runtime is not None:
                runtime.set_visible(False)
        window.page_list_model.clear()
        window._path_probe_generation += 1
        window._pending_path_probe = None
        window.fullscreen_chrome.shutdown()
        window._cancel_interactive_open()
        window.viewer.cancel_mouse_gesture()
        window.viewer.cancel_pending_canvas_click()
        window._deactivate_zip_runtime(clear_artifacts=True)

    def _drain_loupe(self, window: ViewerWindow, wait_msecs: int) -> bool:
        return window._pdf_loupe_cache.shutdown(max(5000, wait_msecs) if wait_msecs > 0 else 0)

    def _drain_rendering(self, window: ViewerWindow, wait_msecs: int) -> bool:
        return window.viewer.shutdown_rendering(max(5000, wait_msecs) if wait_msecs > 0 else 0)

    def _stop_book_requests(self, window: ViewerWindow, wait_msecs: int) -> None:
        window.slideshow_timer.stop()
        window._pdf_render_timer.stop()
        window._prepared_display_timer.stop()
        window._raster_viewport_timer.stop()
        window._raster_paint_fallback_timer.stop()
        window._raster_prefetch_after_paint = None
        window._clear_raster_prefetch_pipeline()
        window._release_raster_interactive_lane()
        window._cancel_pending_display_demand()
        window._cancel_pending_decode_demand()
        window._cancel_deferred_pdf_prefetch()
        window.book_session.shutdown(wait_msecs=wait_msecs)

    def _drain_book(self, window: ViewerWindow, wait_msecs: int) -> bool:
        # Preserve the owner-driven prepare contract: BookSession can retain
        # its own detached work. An accepted close must also release its source
        # before the window unregisters from file-operation ownership checks.
        return not self.close_requested or window.book_session.shutdown_complete

    def _drain_auxiliary(self, window: ViewerWindow, wait_msecs: int) -> bool:
        return window._auxiliary_image_pool is None or window._auxiliary_image_pool.waitForDone(0)

    def _close_archives(self, window: ViewerWindow, wait_msecs: int) -> None:
        if window._owns_archive_backend_registry:
            window.archive_backend_registry.close()

    def _close_pdf(self, window: ViewerWindow, wait_msecs: int) -> None:
        if window._owns_pdfium_service and not window.pdfium_service.shutdown():
            raise RuntimeError(window.pdfium_service.last_shutdown_error or "PDFium shutdown did not complete.")

    def _close_path_probes(self, window: ViewerWindow, wait_msecs: int) -> None:
        if window._owns_path_availability_service:
            window.path_availability_service.close()
