"""Publish completed display pixels; prepare future exposure before moving."""
from collections import deque
from time import perf_counter

from PySide6.QtCore import QObject, QRect, QTimer, Slot
from .browser_thumbnail_surface import BrowserThumbnailDisplayPlan
from .browser_thumbnail_scheduler import ThumbnailPriority, calculate_grid_visible_range


class BrowserThumbnailPresentation(QObject):
    SLICE_SECONDS = .002
    SLICE_UNITS = 4

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._closed = False
        self._revision = 0
        self._context = None
        self._plan = None
        self._shape = None
        self._band = None
        self._band_direction = None
        self._display_priority_rows = {}
        self._queue = deque()
        self._queued = set()
        self._decoration_rows = deque()
        self._scan_row = 0
        self._phases = 1  # A worker job completes all distinct physical sizes.
        self._preparing_scroll = False
        self._waiting_capacity = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(1)
        self._timer.timeout.connect(self._pump)
        self.slices = 0
        self.maximum_slice_ms = 0.0
        self.prepared = 0
        self.pre_scroll_calls = 0
        self.pre_scroll_ready = 0
        self.pre_scroll_missing = 0
        self.maximum_pre_scroll_ms = 0.0
        model = window.item_model
        for signal in (model.modelReset, model.layoutChanged, model.rowsInserted, model.rowsRemoved):
            signal.connect(self._model_changed)
        provider = window.thumbnail_provider
        capacity = getattr(provider, 'capacity_released', None)
        if capacity is not None:
            capacity.connect(self._capacity_ready)
        resident = getattr(provider, 'resident_thumbnail_ready', None)
        if resident is not None:
            resident.connect(self._resident_ready)
        display = getattr(provider, 'display_surfaces_ready', None)
        if display is not None:
            display.connect(self._display_ready)
        resumed = getattr(provider, 'scheduling_resumed', None)
        if resumed is not None:
            resumed.connect(self.recenter)
        window.config.settings_changed.connect(self._settings_changed)
        window.list_view.scrollPreparing.connect(self.prepare_scroll)
        self._synchronize()

    def shutdown(self):
        self._closed = True
        self._timer.stop()
        self._queue.clear()
        self._queued.clear()
        self._decoration_rows.clear()

    def _model_changed(self, *_):
        self._revision += 1
        self.recenter()

    def _capacity_ready(self):
        self._waiting_capacity = False
        self._schedule()

    def _settings_changed(self, *_):
        # A larger manual grant can admit previously deferred resident images.
        self._band = None
        self._display_priority_rows.clear()
        self._scan_row = 0
        self.recenter()

    def _synchronize(self):
        window = self.window
        if self._closed or window._shutdown_prepared:
            return False
        delegate = window.item_delegate
        metrics = delegate.grid_metrics
        frame = metrics.thumbnail_frame_rect(metrics.cell_rect())
        plan = BrowserThumbnailDisplayPlan(tuple(frame.getRect()), delegate.thumbnail_display_mode,
            round(max(.5, window.list_view.viewport().devicePixelRatioF()), 6))
        context = (window._generation, window.thumbnail_render_spec.cache_token, self._revision, plan)
        if context != self._context:
            plan_changed = self._plan is not None and self._plan != plan
            if plan_changed:
                delegate._display_surface_cache.clear()
            self._context = context
            self._shape = self._plan = plan
            self._queue.clear()
            self._queued.clear()
            self._decoration_rows.clear()
            self._band = None
            self._display_priority_rows.clear()
            self._scan_row = 0
            self._waiting_capacity = False
            configure = getattr(window.thumbnail_provider, 'set_display_plan', None)
            if configure is not None:
                configure(plan)
            if plan_changed and getattr(window.thumbnail_provider, 'memory_images_need_plan_check', False):
                # A DPR/geometry change can retain the same disk bucket. Reopen
                # consumed source attempts whose compact pixels no longer fit.
                workflow = getattr(window, '_browser_workflow', None)
                if workflow is not None:
                    workflow._model_changed()
                window._schedule_thumbnail_requests(0)
        return True

    def recenter(self, *_):
        if not self._synchronize():
            return
        region = self.window._visible_row_range()
        if region is None:
            return
        first, last = region
        span = max(1, last-first+1)
        count = self.window.item_model.rowCount()
        # Keep a second resident viewport ready while workers refill the first
        # during fast input. This borrows RAM only; it never expands acquisition.
        band = (max(0, first-2*span), min(count-1, last+2*span), first, last)
        direction = self.window._thumbnail_scroll_direction
        if band != self._band or direction != self._band_direction:
            reversed_direction = direction != self._band_direction
            self._band, self._band_direction = band, direction
            ahead = range(last+1, band[1]+1) if direction >= 0 else range(first-1, band[0]-1, -1)
            behind = range(first-1, band[0]-1, -1) if direction >= 0 else range(last+1, band[1]+1)
            ordered = [*ahead, *range(first, last+1), *behind]
            # Bounded to the nearby display band; never query the shell on
            # the scroll signal or walk the entire listing for decorations.
            decorations = {}
            columns = max(1, (self.window.list_view.viewport().width()-1)
                          // max(1, self.window.list_view.gridSize().width()))
            reverse_guard = set(list(behind)[:columns])
            entries = []
            priorities = {}
            for row in ordered:
                item = self.window.item_model.item_at(row)
                if item is not None:
                    association = self.window.item_delegate._badge_association(item)
                    decorations.setdefault(association, row)
                    rank = (0 if first <= row <= last else
                            1 if (row > last) == (direction >= 0) or row in reverse_guard else 2)
                    entries.append((item.path, item.thumbnail_revision, rank))
                    priorities[row] = rank
            self._decoration_rows = deque(decorations.values())
            self.window.thumbnail_provider.set_display_retention_priorities(entries)
            for row in ordered:
                # Unchanged overlap is already resident/queued. Rechecking all
                # image variants on every pixel scroll wastes the input budget.
                if reversed_direction or self._display_priority_rows.get(row) != priorities.get(row):
                    self._enqueue(row)
            self._display_priority_rows = priorities
            # Preserve overlapping unfinished work, changing priority even on
            # a reversal inside the same row range. Hits never consume units.
            priority = {row: index for index, row in enumerate(ordered)}
            self._queue = deque(sorted(self._queue, key=lambda task: priority.get(task[0], len(priority))))
        self._schedule()

    def _enqueue(self, row, *, front=False):
        image = self._publish(row)
        if image is None or self._restore_display(image):
            return
        if not self.window.thumbnail_provider.can_prepare_display(image):
            return
        task = (row, 0)
        if task in self._queued:
            if front:
                self._queue.remove(task)
                self._queue.appendleft(task)
            return
        self._queued.add(task)
        (self._queue.appendleft if front else self._queue.append)(task)

    def _restore_display(self, image):
        cache = self.window.item_delegate._display_surface_cache
        if cache.has_plan(image, self._plan):
            return True
        borrow = getattr(self.window.thumbnail_provider, 'display_artifact', None)
        surfaces = borrow(image, self._plan) if borrow is not None else None
        if surfaces is not None:
            cache.adopt(image, self._plan, surfaces)
            return cache.has_plan(image, self._plan)
        return False

    def _schedule(self):
        if (not self._closed and not self._waiting_capacity and not self._timer.isActive() and
                (self._queue or self._decoration_rows
                 or self._scan_row < self.window.item_model.rowCount())):
            self._timer.start()

    def _publish(self, row):
        window = self.window
        item = window.item_model.item_at(row)
        if item is None or not item.can_generate_preview or window._file_mutation_for_path(item.path) is not None:
            return None
        model = window.item_model
        image = model._thumbnail_images.get(model._key(item.path))
        provider = window.thumbnail_provider
        if (image is not None and model._has_compatible_thumbnail(item, window.thumbnail_render_spec.cache_token)
                and provider.owns_memory_image(image)):
            return image
        borrow = getattr(provider, 'restore_memory_thumbnail', None)
        if not callable(borrow):
            return None
        result = borrow(item, window.thumbnail_render_spec, generation=window._generation, displayed=False)
        if result is None:
            return None
        image, _page_count = result
        region = window._visible_row_range()
        visible = region is not None and region[0] <= row <= region[1]
        model.set_thumbnail_image(item.path, image, request_token=window.thumbnail_render_spec.cache_token,
                                  notify=visible, displayed=False)
        return image if provider.owns_memory_image(image) else None

    @Slot(str, int, object, object)
    def _resident_ready(self, path, generation, token, revision):
        window = self.window
        if (self._closed or window._shutdown_prepared or generation != window._generation
                or token != window.thumbnail_render_spec.cache_token):
            return
        row = window.item_model.row_for_path(path)
        item = window.item_model.item_at(row) if row >= 0 else None
        if item is None or item.thumbnail_revision != tuple(revision):
            return
        self._enqueue(row)
        self._schedule()

    @Slot(str, int, object, object, object, object, object)
    def _display_ready(self, path, generation, token, revision, image, plan, surfaces):
        if not self._synchronize():
            return
        window = self.window
        row = window.item_model.row_for_path(path)
        item = window.item_model.item_at(row) if row >= 0 else None
        if (generation != window._generation or token != window.thumbnail_render_spec.cache_token
                or plan != self._plan or item is None or item.thumbnail_revision != tuple(revision)
                or window._file_mutation_for_path(item.path) is not None):
            return
        self.prepared += window.item_delegate._display_surface_cache.adopt(image, plan, surfaces)
        self._publish(row)
        rect = window.list_view.visualRect(window.item_model.index(row, 0))
        if rect.intersects(window.list_view.viewport().rect()):
            window.list_view.viewport().update(rect)
        # Publish this receipt immediately; the existing coalesced timer owns
        # subsequent preparation. A burst of source completions must not also
        # run a fresh display scan in every notification.
        self._schedule()

    @Slot(int, int)
    def prepare_scroll(self, old_value, new_value):
        if self._preparing_scroll or old_value == new_value or not self._synchronize():
            return
        window = self.window
        if getattr(window.thumbnail_provider, '_paused', False):
            return
        self._preparing_scroll = True
        started = perf_counter()
        try:
            delegate, view = window.item_delegate, window.list_view
            delegate.begin_exposure_preparation()
            height = view.viewport().height()
            delta = new_value-old_value
            exposure = QRect(0, max(0, height-delta) if delta > 0 else 0,
                             view.viewport().width(), min(height, abs(delta)))
            grid = view.gridSize()
            region = calculate_grid_visible_range(row_count=window.item_model.rowCount(),
                viewport_width=max(1, view.viewport().width()-1), viewport_height=exposure.height(),
                grid_width=grid.width(), grid_height=grid.height(),
                vertical_offset=new_value+exposure.top())
            if region is None:
                return
            offset = view.verticalScrollBar().value()-new_value
            rows = []
            missing = []
            for row in range(region[0], region[1]+1):
                rect = view.visualRect(window.item_model.index(row, 0)).translated(0, offset)
                if rect.intersects(exposure):
                    rows.append((row, rect))
            for row, rect in rows:
                image = self._publish(row)
                if image is None:
                    self.pre_scroll_missing += 1
                    continue
                self._restore_display(image)
                if delegate.prepare_exposure_image(image, self._plan.dpr, rect):
                    self.pre_scroll_ready += 1
                else:
                    self.pre_scroll_missing += 1
                    missing.append(row)
            for row in reversed(missing):
                self._enqueue(row, front=True)
            self.pre_scroll_calls += 1
            self._schedule()
        finally:
            self.maximum_pre_scroll_ms = max(self.maximum_pre_scroll_ms, (perf_counter()-started)*1000)
            self._preparing_scroll = False

    @Slot()
    def _pump(self, *, prepare_decorations=True):
        if not self._synchronize():
            return
        window = self.window
        provider = window.thumbnail_provider
        if not window.isVisible() or getattr(provider, '_paused', False):
            return
        started, units = perf_counter(), 0
        request = getattr(provider, 'request_display_surfaces', None)
        while units < self.SLICE_UNITS and perf_counter()-started < self.SLICE_SECONDS:
            if self._queue:
                if getattr(provider, 'display_pending_count', 0) >= 2:
                    self._waiting_capacity = True
                    break
                row, phase = self._queue.popleft()
                self._queued.discard((row, phase))
                image = self._publish(row)
                if image is not None and request is not None:
                    if not provider.can_prepare_display(image):
                        units += 1
                        continue
                    if not window.item_delegate._display_surface_cache.has_plan(image, self._plan):
                        item = window.item_model.item_at(row)
                        if not request(item, window.thumbnail_render_spec, image, generation=window._generation,
                                       priority=ThumbnailPriority.READ_AHEAD):
                            self._queue.appendleft((row, phase)); self._queued.add((row, phase))
                            self._waiting_capacity = True
                            break
            elif self._decoration_rows:
                if not prepare_decorations:
                    break  # Shell queries belong to an idle timer, not completion bursts.
                item = window.item_model.item_at(self._decoration_rows.popleft())
                if item is not None:
                    window.item_delegate.prepare_item_decoration(item, self._plan.dpr)
            elif self._scan_row < window.item_model.rowCount():
                self._enqueue(self._scan_row)
                self._scan_row += 1
            else:
                break
            units += 1
        self.slices += 1
        self.maximum_slice_ms = max(self.maximum_slice_ms, (perf_counter()-started)*1000)
        self._schedule()
