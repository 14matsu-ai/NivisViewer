from __future__ import annotations

from enum import IntEnum
import logging
from math import ceil
from time import monotonic
from weakref import WeakSet

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


_LOG = logging.getLogger(__name__)


class ImageWorkPriority(IntEnum):
    BROWSER_PREFETCH = 100
    BROWSER_SELECTED = 200
    BROWSER_VISIBLE = 300
    VIEWER_PREVIOUS = 400
    VIEWER_NEXT = 500
    VIEWER_INTERACTIVE_RERENDER = 600
    VIEWER_SPREAD_PARTNER = 700
    VIEWER_CURRENT = 800


class ImageWorkCoordinator(QObject):
    """Owns bounded Viewer and Browser execution lanes.

    The Viewer lane is never lent to Browser work. This deliberately leaves
    one slot idle when no Viewer work exists so Browser decoding cannot occupy
    every application image-processing slot immediately before an open.
    """

    browser_pause_changed = Signal(bool)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        max_workers: int = 2,
    ) -> None:
        super().__init__(parent)
        self.max_workers = max(1, min(4, int(max_workers)))
        self.viewer_workers = 1
        self.browser_workers = max(0, self.max_workers - self.viewer_workers)
        self._viewer_pool = QThreadPool(self)
        self._viewer_pool.setMaxThreadCount(self.viewer_workers)
        self._browser_pool = QThreadPool(self)
        self._browser_pool.setMaxThreadCount(max(1, self.browser_workers))
        self._browser_paused = False
        self._interactive_depth = 0
        self._accepting_requests = True
        self._shutdown_complete = False
        self._shutdown_timeout_logged = False
        self.last_shutdown_error: str | None = None
        self._viewer_runnables: WeakSet[QRunnable] = WeakSet()
        self._browser_runnables: WeakSet[QRunnable] = WeakSet()
        self._cancel_requested: WeakSet[QRunnable] = WeakSet()

    @property
    def browser_paused(self) -> bool:
        return self._browser_paused

    def begin_viewer_interactive(self) -> None:
        self._interactive_depth += 1
        self._set_browser_paused(True)

    def end_viewer_interactive(self) -> None:
        if self._interactive_depth > 0:
            self._interactive_depth -= 1
        if self._interactive_depth == 0:
            self._set_browser_paused(False)

    def cancel_viewer_interactive(self) -> None:
        self.end_viewer_interactive()

    def start_viewer(
        self,
        runnable: QRunnable,
        priority: ImageWorkPriority | int,
    ) -> bool:
        if not self._accepting_requests:
            return False
        self._viewer_runnables.add(runnable)
        self._viewer_pool.start(runnable, int(priority))
        return True

    def start_browser(
        self,
        runnable: QRunnable,
        priority: ImageWorkPriority | int,
    ) -> bool:
        if not self._accepting_requests or self._browser_paused:
            return False
        if self.browser_workers <= 0:
            self._viewer_runnables.add(runnable)
            self._viewer_pool.start(runnable, int(priority))
            return True
        self._browser_runnables.add(runnable)
        self._browser_pool.start(runnable, int(priority))
        return True

    def try_take_viewer(self, runnable: QRunnable) -> bool:
        try:
            return self._viewer_pool.tryTake(runnable)
        except RuntimeError:
            return False

    def try_take_browser(self, runnable: QRunnable) -> bool:
        if self.browser_workers <= 0:
            return self.try_take_viewer(runnable)
        try:
            return self._browser_pool.tryTake(runnable)
        except RuntimeError:
            return False

    def clear_browser_queue(self) -> None:
        self._browser_pool.clear()

    def wait_for_viewer(self, msecs: int) -> bool:
        return self._viewer_pool.waitForDone(max(0, int(msecs)))

    def wait_for_browser(self, msecs: int) -> bool:
        if self.browser_workers <= 0:
            return self.wait_for_viewer(msecs)
        return self._browser_pool.waitForDone(max(0, int(msecs)))

    def shutdown(self, *, wait_msecs: int = 250) -> bool:
        if self._shutdown_complete:
            return True
        self._accepting_requests = False
        self._request_worker_cancellation(self._viewer_runnables)
        self._request_worker_cancellation(self._browser_runnables)
        self._viewer_pool.clear()
        self._browser_pool.clear()
        deadline = monotonic() + max(0, int(wait_msecs)) / 1000
        viewer_done = self._viewer_pool.waitForDone(
            self._remaining_msecs(deadline)
        )
        browser_done = self._browser_pool.waitForDone(
            self._remaining_msecs(deadline)
        )
        if viewer_done and browser_done:
            self._shutdown_complete = True
            self.last_shutdown_error = None
            self._viewer_runnables.clear()
            self._browser_runnables.clear()
            self._cancel_requested.clear()
            return True

        pending_lanes = ", ".join(
            lane
            for lane, done in (
                ("viewer", viewer_done),
                ("browser", browser_done),
            )
            if not done
        )
        self.last_shutdown_error = (
            "Image worker shutdown timed out; "
            f"active lanes: {pending_lanes or 'unknown'}."
        )
        if not self._shutdown_timeout_logged:
            _LOG.error(self.last_shutdown_error)
            self._shutdown_timeout_logged = True
        return False

    def _request_worker_cancellation(
        self,
        runnables: WeakSet[QRunnable],
    ) -> None:
        for runnable in tuple(runnables):
            if runnable in self._cancel_requested:
                continue
            try:
                cancel = getattr(runnable, "cancel", None)
                if callable(cancel):
                    cancel()
                    self._cancel_requested.add(runnable)
                    continue
                cancelled = getattr(runnable, "cancelled", None)
                set_cancelled = getattr(cancelled, "set", None)
                if callable(set_cancelled):
                    set_cancelled()
                    self._cancel_requested.add(runnable)
            except RuntimeError:
                # Qt may already have auto-deleted a completed QRunnable.
                continue

    @staticmethod
    def _remaining_msecs(deadline: float) -> int:
        return max(0, ceil((deadline - monotonic()) * 1000))

    def _set_browser_paused(self, paused: bool) -> None:
        normalized = bool(paused)
        if normalized == self._browser_paused:
            return
        self._browser_paused = normalized
        self.browser_pause_changed.emit(normalized)
