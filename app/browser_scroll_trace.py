"""Opt-in, bounded source-run diagnostics without file names or image content."""
from collections import deque
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
from threading import Thread
from time import perf_counter, time_ns

from PySide6.QtCore import QObject, QTimer


class BrowserScrollTrace(QObject):
    def __init__(self, view, snapshot, output_directory):
        super().__init__(view)
        self._view = view
        self._snapshot = snapshot
        self._output_directory = Path(output_directory)
        self._identity = str(time_ns())
        self._started = None
        self._last_probe = None
        self._active = True
        self._wheel = 0
        self._flush_number = 0
        self._events = deque(maxlen=256)
        self._probe = QTimer(self)
        self._probe.setInterval(16)
        self._probe.timeout.connect(self._probe_tick)
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self.flush)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self.finish)

    def mark(self, name, **values):
        if self._active and self._started is not None:
            self._events.append(dict(event=name, wheel=self._wheel,
                ms=round((perf_counter()-self._started)*1000, 3), **values))

    def begin_wheel(self, event):
        if not self._active:
            return
        if self._wheel >= 6:
            self.finish()
            return
        if self._started is None:
            self._started = self._last_probe = perf_counter()
            self._probe.start()
            self._deadline.start(30000)
        self._wheel += 1
        self.mark('wheel.begin', angle_y=event.angleDelta().y(), pixel_y=event.pixelDelta().y(),
                  state=self._snapshot())

    def end_wheel(self):
        if self._active:
            self.mark('wheel.end', state=self._snapshot())
            self._flush_timer.start(1000)

    def _probe_tick(self):
        now = perf_counter()
        gap = (now-self._last_probe)*1000
        self._last_probe = now
        if gap >= 40:
            self.mark('event_loop.gap', gap_ms=round(gap, 3), state=self._snapshot())

    def flush(self):
        if self._started is None:
            return
        self._flush_number += 1
        payload = dict(schema=1, events=list(self._events), wheels=self._wheel,
                       final_state=self._snapshot())
        path = self._output_directory/f'{self._identity}-{self._flush_number:02}.json'
        # No serialization, source hashing, or file writes on the input thread.
        Thread(target=self._write, args=(path, payload), daemon=True).start()
        if self._wheel >= 6:
            self._active = False
            self._probe.stop()
            self._deadline.stop()

    def finish(self):
        self._flush_timer.stop()
        self._probe.stop()
        self._deadline.stop()
        self.flush()
        self._active = False

    @staticmethod
    def _write(path, payload):
        try:
            root = Path(__file__).parent
            payload['source_sha256'] = {name: hashlib.sha256((root/name).read_bytes()).hexdigest()
                for name in ('browser_window.py', 'browser_workflow_controller.py',
                             'browser_thumbnail_presentation.py', 'thumbnail_provider.py',
                             'explorer_list_view.py')}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        except OSError as error:
            logging.getLogger(__name__).warning('Scroll trace write failed: %s', type(error).__name__)


def install_browser_scroll_trace(window):
    root = Path(__file__).resolve().parents[1]/'out'/'internal'
    # An ignored local marker enables this only for source runs. Tests and
    # packaged applications never silently start a diagnostic recording.
    if (getattr(sys, 'frozen', False) or 'PYTEST_CURRENT_TEST' in os.environ
            or not (root/'browser-scroll-trace.enabled').is_file()):
        return
    def snapshot():
        provider = window.thumbnail_provider
        presentation = window._thumbnail_presentation
        workflow = window._browser_workflow
        cache = window.item_delegate._display_surface_cache
        coordinator = provider._coordinator
        read_pool = coordinator._browser_read_pool if coordinator is not None else provider._read_pool
        return dict(offset=window.list_view.verticalScrollBar().value(),
            rows=window.item_model.rowCount(), dpr=window.list_view.devicePixelRatioF(),
            pending=provider.pending_count, pending_reads=provider.pending_read_count,
            read_workers=read_pool.activeThreadCount(),
            display_pending=provider.display_pending_count, resident=len(provider._cache),
            display_entries=cache.item_count, paint_resizes=cache.preparations,
            exposure_ready=presentation.pre_scroll_ready, exposure_missing=presentation.pre_scroll_missing,
            presentation_queue=len(presentation._queue), presentation_scan=presentation._scan_row,
            refill_queue=len(workflow._memory_refill), workflow=workflow.stop_reason,
            fast=window._fast_scrolling)
    window.list_view._scroll_trace = BrowserScrollTrace(
        window.list_view, snapshot, root/'browser-scroll-traces')
