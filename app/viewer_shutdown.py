"""Viewer close ownership and ordered, resumable resource cleanup."""

from __future__ import annotations

from enum import StrEnum
import logging
from time import monotonic
from typing import TYPE_CHECKING
import weakref

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .viewer_memory_policy import read_physical_memory_snapshot

if TYPE_CHECKING:
    from .viewer_window import ViewerWindow


_LOG = logging.getLogger('nivisviewer.viewer_shutdown')
# Hidden standalone Viewers must outlive their workers and queued completions.
CLOSING_VIEWERS: set[object] = set()


class ViewerShutdownPhase(StrEnum):
    RASTER_ARTIFACTS = 'raster_artifacts'
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
        self._logged_phase = None
        self._phase_started = 0.0
        self._next_progress_log = 0.0
        self._steps = (
            (ViewerShutdownPhase.RASTER_ARTIFACTS, self._drain_artifacts),
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
            window.hide()
            self._timer.start(0)
            try:
                window._cancel_auxiliary_image_workers()
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
                phase, step = self._steps[self._next_step]
                self._log_phase(window, phase)
                if step(window, wait_msecs) is False:
                    return False
                _LOG.info('Viewer shutdown phase complete owner=%x phase=%s elapsed_ms=%.1f',
                          id(window), phase, (monotonic() - self._phase_started) * 1000)
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
            complete = self.prepare(wait_msecs=0)
        except Exception:
            self._report_error()
            self._timer.start(100)
            return
        if complete:
            if _LOG.isEnabledFor(logging.INFO):
                _LOG.info('Viewer close ready state=%s', self.snapshot())
            window.close()
        else:
            self._timer.start(8 if self.phase is ViewerShutdownPhase.RASTER_ARTIFACTS else 10)

    def _log_phase(self, window: ViewerWindow, phase: ViewerShutdownPhase) -> None:
        now = monotonic()
        if self._logged_phase != phase:
            self._logged_phase = phase
            self._phase_started = now
            self._next_progress_log = now + 2.0
            _LOG.info('Viewer shutdown phase started owner=%x phase=%s', id(window), phase)
        elif now >= self._next_progress_log and _LOG.isEnabledFor(logging.INFO):
            self._next_progress_log = now + 5.0
            _LOG.info('Viewer shutdown waiting elapsed_ms=%.1f state=%s',
                      (now - self._phase_started) * 1000, self.snapshot())

    def snapshot(self) -> dict[str, object]:
        try:
            return self._snapshot()
        except Exception as exc:
            # Diagnostics must never stop close, including after a Qt owner
            # was destroyed while a completion was being delivered.
            return {'owner': id(self._window()), 'phase': self.phase.value,
                    'diagnostic_error': type(exc).__name__}

    def _snapshot(self) -> dict[str, object]:
        window = self._window()
        memory = read_physical_memory_snapshot()
        images = window.viewer._images
        return {'owner': id(window), 'phase': self.phase.value, 'hidden_close': self.close_requested,
                'rss_bytes': memory.process_working_set_bytes if memory else None,
                'private_commit_bytes': memory.process_private_commit_bytes if memory else None,
                'book': window.book_session.shutdown_snapshot(),
                'canvas_cache_bytes': window.viewer.render_cache_bytes(),
                'visible_qimage_bytes': sum(im.qimage.sizeInBytes() for im in images if im.qimage is not None),
                'visible_qpixmap_estimated_bytes': sum(im.pixmap.width() * im.pixmap.height() * 4
                                                       for im in images if im.pixmap is not None),
                'canvas_jobs': len(window.viewer._render_tasks),
                'auxiliary_jobs': (window._auxiliary_image_pool.activeThreadCount()
                                   if window._auxiliary_image_pool is not None else 0)}

    def _drain_artifacts(self, window: ViewerWindow, wait_msecs: int) -> bool:
        if not self.close_requested or wait_msecs > 0:
            return True
        self._retire_runtime()
        return window.book_session.drain_closing_raster_artifacts()

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
        return not self.close_requested or window.book_session.drain_shutdown()

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
