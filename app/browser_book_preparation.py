"""One selected archive's first two display units, using the Viewer runtime.

ZipPla 1.7.2.9 prepares a hidden Viewer before opening from its Catalog. This
adaptation transfers the existing Qt book owner instead of spawning a process.
See docs/ZIPPLAFORK_COMPARISON.md for fixed source and AGPL attribution.
Source structure: Copyright (C) 2016-2017 Rio's Toolbox, AGPL-3.0-or-later.
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import fields
import logging
from time import monotonic
import weakref

from PySide6.QtCore import QObject, QTimer, Signal
from shiboken6 import isValid

from .book_session import BookSession
from .image_source import SevenZipImageSource, ZipImageSource
from .raster_warmup_planner import RasterWarmupPlan
from .viewer_memory_policy import MIB, read_physical_memory_snapshot
from .raster_book_runtime import RasterDisplayUnit, RasterPage, RasterRequest


_LOG = logging.getLogger('nivisviewer.book_preparation')


class BrowserBookPreparation(QObject):
    """Latest selection only; cancellation drains before another owner starts."""

    drained = Signal()

    def __init__(self, target_factory, parent=None):
        super().__init__(parent)
        self._target_factory = target_factory
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start)
        self._advance = QTimer(self)
        self._advance.setSingleShot(True)
        self._advance.timeout.connect(self._drive)
        self._pressure = QTimer(self)
        self._pressure.setInterval(5000)
        self._pressure.timeout.connect(self._check_pressure)
        self._profile_check = QTimer(self)
        self._profile_check.setSingleShot(True)
        self._profile_check.timeout.connect(self._revalidate_profile)
        self.session: BookSession | None = None
        self._retiring: BookSession | None = None
        self._item = None
        self._target = lambda: None
        self._signature = None
        self._stage = 0
        self._request_id = 0
        self._topology = None
        self.ready = False
        self._closed = False
        self._selection_id = 0
        self._selected_at = 0.0

    def _log(self, event, detail='') -> None:
        # Trace the route without recording book names or personal paths.
        _LOG.info('preparation selection=%d event=%s elapsed_ms=%.1f %s',
                  self._selection_id, event,
                  (monotonic() - self._selected_at) * 1000 if self._selected_at else 0,
                  detail)

    def select(self, item) -> None:
        if self._closed:
            return
        if item == self._item or (item is not None and self._item is not None
                and item.path == self._item.path and item.thumbnail_revision == self._item.thumbnail_revision):
            return
        self.cancel('selection_changed')
        self._item = item
        if item is not None:
            self._selection_id += 1
            self._selected_at = monotonic()
            self._log('selected')
            # Coalesce selection/current changes in this event turn, but do
            # not consume the head start between a click and a double click.
            self._timer.start(0)

    def cancel(self, reason='cancelled') -> None:
        if self._item is not None:
            self._log('cancelled', reason)
        self._timer.stop()
        self._advance.stop()
        self._pressure.stop()
        self._profile_check.stop()
        self._item = None
        self.ready = False
        session, self.session = self.session, None
        if session is not None:
            self._disconnect(session)
            session.shutdown(wait_msecs=0)
            if session._has_owned_async_work():
                self._retiring = session
                session.destroyed.connect(self._retired_session_destroyed)
            else:
                session.deleteLater()

    def retry_selection(self) -> None:
        """Retry an unchanged selection after its occupied target closes."""
        if (not self._closed and self._item is not None and self.session is None
                and not self._timer.isActive()):
            self._timer.start(0)

    def settings_changed(self, changed) -> None:
        if {'archive_backend_preference', 'winrar_executable',
                'seven_zip_executable'}.intersection(changed):
            self.cancel('archive_backend_changed')
        elif self._item is not None:
            # Viewer consumes shared settings after the controller's slot.
            # History, Browser layout and unrelated preferences do not
            # invalidate a prepared book.
            self._profile_check.start(0)

    def _revalidate_profile(self) -> None:
        target = self._target()
        if (self.session is not None and target is not None
                and self._signature != self._profile(target)):
            item = self._item
            self.cancel('render_settings_changed')
            self.select(item)

    def _retired_session_destroyed(self, _object=None) -> None:
        if self._retiring is not None and not isValid(self._retiring):
            self._retiring = None
        if self._closed and self._retiring is None:
            self.drained.emit()

    def _drain_retiring(self) -> bool:
        session = self._retiring
        if session is None:
            return True
        if not isValid(session):
            self._retiring = None
            return True
        if session._has_owned_async_work():
            return False
        self._retiring = None
        session.deleteLater()
        return True

    def _start(self) -> None:
        if self._closed or self._item is None:
            return
        if not self._drain_retiring():
            self._timer.start(50)
            return
        target = self._target_factory()
        if target is None or target._shutdown_prepared:
            self._log('skipped', 'no_target')
            return
        if (target.book_session.source is not None
                and target.book_session.current_path == self._item.path):
            # Viewer -> Browser selection synchronization also reaches this
            # slot. Do not open/decode the currently displayed book again.
            self._log('skipped', 'already_open')
            return
        # This owner retains at most two frames and does not inherit unlimited
        # all-book prefetch. Leave room for the active Viewer and Browser.
        budget = min(256 * MIB, target.viewer_cache_budget_bytes)
        memory = read_physical_memory_snapshot()
        if memory is not None:
            budget = min(budget, memory.available_physical_bytes // 8)
        if budget < 32 * MIB:
            self._log('skipped', 'low_memory')
            return
        target.prepare_initial_render_layout()
        self._target = weakref.ref(target)
        self._signature = self._profile(target)
        self._log('started', f'viewport={self._signature[1].viewport_size} '
                  f'dpr={self._signature[1].device_pixel_ratio:g} budget={budget}')
        session = target.book_session.create_preparation_session(self, budget)
        self.session = session
        self._pressure.start()
        session.model.update_options(**self._signature[0])
        self._stage = self._request_id = 0
        session.async_opened.connect(self._opened)
        session.async_open_failed.connect(self._failed)
        session.open_book_async(self._item.path, sort_descending=target.sort_descending)

    @staticmethod
    def _profile(target):
        options = {name: getattr(target, name) for name in (
            'view_mode', 'reading_direction', 'single_first_page',
            'treat_wide_image_as_single', 'treat_square_image_as_single',
            'square_image_tolerance_percent',
        )}
        return options, target._raster_render_spec(), target.sort_descending

    def _check_pressure(self) -> None:
        memory = read_physical_memory_snapshot()
        runtime = self.session.viewer_runtime if self.session is not None else None
        if memory is not None and (memory.available_physical_bytes < 256 * MIB
                or (runtime is not None and runtime.cache_bytes > memory.available_physical_bytes // 8)):
            self.cancel('memory_pressure')

    def _opened(self, _opened) -> None:
        session = self.session
        if session is None:
            return
        if not isinstance(session.source, (ZipImageSource, SevenZipImageSource)):
            self.cancel('unsupported_source')
            return
        from .viewer_window import _PageModelRasterTopology
        self._topology = _PageModelRasterTopology(session.model, self._unit)
        runtime = session.viewer_runtime
        self._log('archive_opened', f'pages={session.model.total_pages}')
        runtime.layoutMetadataReady.connect(self._metadata)
        runtime.frameReady.connect(self._frame)
        runtime.idle.connect(self._idle)
        self._drive()

    def _idle(self) -> None:
        session = self.session
        if (session is not None and session.viewer_runtime is not None
                and session.viewer_runtime._dispatch_suspended and not self.ready):
            self._log('paused', 'optional_memory_admission')

    def _unit(self, indexes, *, start_index, is_single):
        model = self.session.model
        return RasterDisplayUnit(start_index, tuple(
            RasterPage(index, model.image_id_at(index), model.get_image_size(index))
            for index in indexes
        ), is_single)

    def _drive(self) -> None:
        session = self.session
        if session is None or session.viewer_runtime is None or self.ready:
            return
        spread = session.model.spread_at()
        unit = self._unit(tuple(s.page_index for s in spread.slots),
                          start_index=spread.start_index, is_single=spread.is_single)
        plan = RasterWarmupPlan(self._topology, current=unit,
            identity_of=lambda u: u.identity,
            page_indexes_of=lambda u: (p.page_index for p in u.pages),
            direction=1, background_enabled=False)
        self._request_id += 1
        session.viewer_runtime.request(RasterRequest(
            session.generation, self._request_id, unit, plan, self._signature[1],
            navigation_direction=1, resolve_layout_metadata=True,
        ))

    def _metadata(self, metadata) -> None:
        session = self.session
        if session is None or metadata.source_identity != id(session.source):
            return
        # A codec can allocate a full raster before reducing it. Speculative
        # work yields on real pressure; opening later retains normal semantics.
        sizes = tuple((index, size) for index, _name, size in metadata.observations if size)
        memory = read_physical_memory_snapshot()
        if memory is not None and any(w * h * 12 > memory.available_physical_bytes // 4
                                      for _index, (w, h) in sizes):
            self.cancel('decoder_scratch_pressure')
            return
        session.model.set_image_sizes(sizes)
        self._advance.start(0)

    def _frame(self, frame) -> None:
        session = self.session
        if session is None or frame.request_id != self._request_id:
            return
        if any(page.error for page in frame.pages):
            self.cancel('decode_failed')
            return
        sizes = {}
        for page in frame.pages:
            previous = sizes.get(page.page_index)
            width, height = page.original_size
            sizes[page.page_index] = (previous[0] + width, max(previous[1], height)) if previous else (width, height)
        session.model.set_image_sizes(tuple(sizes.items()))
        if self._stage == 0:
            following = session.model.next_display_spreads(direction=1, count=1)
            if following:
                self._stage = 1
                session.model.go_to_index(following[0].start_index)
                self._advance.start(0)
                return
        self.ready = True
        self._log('ready', f'cache_bytes={session.viewer_runtime.cache_bytes}')

    def _failed(self, _failure) -> None:
        self.cancel('archive_open_failed')

    def take(self, path, target):
        """Claim an in-flight or finished owner only for its planned book epoch."""
        session = self.session
        item = self._item
        reason = None
        if session is None or item is None:
            reason = 'not_started'
        elif self._target() is not target:
            reason = 'different_viewer'
        elif Path(path) != item.path:
            reason = 'different_book'
        elif session.source is None:
            reason = 'archive_open_pending'
        elif session.generation != target.book_session.generation + 1:
            reason = 'book_epoch_changed'
        elif self._signature != self._profile(target):
            actual = self._profile(target)
            changed = [field.name for field in fields(actual[1])
                       if getattr(actual[1], field.name) != getattr(self._signature[1], field.name)]
            reason = f'profile_changed render_fields={",".join(changed)}'
        if reason is not None:
            self._log('miss', reason)
            self.cancel(reason)
            return None
        try:
            stat = item.path.stat()
            valid = (stat.st_size == item.file_size and
                     stat.st_mtime_ns == item.modified_time_ns)
        except OSError:
            valid = False
        if not valid:
            self.cancel('file_revision_changed')
            return None
        self._log('adopted', f'ready={self.ready} stage={self._stage} '
                  f'cache_bytes={session.viewer_runtime.cache_bytes}')
        self._timer.stop()
        self._advance.stop()
        self._pressure.stop()
        self._profile_check.stop()
        self._disconnect(session)
        result = session.take_prepared_book()
        self.session = None
        self._item = None
        self.ready = False
        session.shutdown(wait_msecs=0)
        session.deleteLater()
        return result

    def _disconnect(self, session):
        session.async_opened.disconnect(self._opened)
        session.async_open_failed.disconnect(self._failed)
        runtime = session.viewer_runtime
        if runtime is not None:
            for signal, slot in ((runtime.layoutMetadataReady, self._metadata),
                                 (runtime.frameReady, self._frame),
                                 (runtime.idle, self._idle)):
                try:
                    signal.disconnect(slot)
                except RuntimeError:
                    pass

    def shutdown(self) -> bool:
        self._closed = True
        self.cancel('shutdown')
        return self._drain_retiring()
