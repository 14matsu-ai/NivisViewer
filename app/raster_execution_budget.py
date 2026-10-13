"""Process-wide raster execution permits, independent of Qt parent pools."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from threading import BoundedSemaphore, Condition, Event
from weakref import WeakKeyDictionary
from PySide6.QtCore import QObject, Signal, QThreadPool, QTimer
from .raster_adaptive_policy import RasterAdaptivePolicy, RasterResourceProbe


class _BudgetSignals(QObject):
    capacity_available = Signal()


class SourceRequestToken(Event):
    """Owned by one raster job, never cancelled by another entry consumer."""


class _ExecutionLease:
    def __init__(self, budget, ticket):
        self.budget, self.ticket = budget, ticket

    def __enter__(self):
        return True

    def __exit__(self, *_args):
        self.release()

    def release(self, *, notify=True):
        if self.ticket is not None:
            ticket, self.ticket = self.ticket, None
            self.budget._release(ticket, notify=notify)


_source_token: ContextVar[Event | None] = ContextVar('raster_source_token', default=None)


@contextmanager
def source_request_scope(token: Event):
    previous = _source_token.set(token)
    try:
        yield
    finally:
        _source_token.reset(previous)


def source_request_token() -> Event:
    return _source_token.get() or Event()


class RasterExecutionBudget:
    """Shared, adjustable admission limit across all Viewer windows.

    Parents acquire a permit before accessing a source. A spread helper can
    only reserve a spare permit without waiting, and has its own single worker.
    No child is queued behind a parent waiting for it. PDF's native service and
    the reserved Browser pool remain separate; vector parents may call PDFium.
    """

    def __init__(self):
        self.signals = _BudgetSignals()
        self._condition = Condition()
        self._default_limit = 3
        self._limits = WeakKeyDictionary()
        self._near_reserves = WeakKeyDictionary()
        self._modes = WeakKeyDictionary()
        self._auto_limits = WeakKeyDictionary()
        self._auto_pending = WeakKeyDictionary()
        self._auto_memory = {}
        self._adaptive_policy = RasterAdaptivePolicy()
        self._resource_probe = RasterResourceProbe()
        self._adaptive_timer = QTimer(self.signals)
        self._adaptive_timer.setInterval(500)
        self._adaptive_timer.timeout.connect(self._sample_adaptive)
        self._emergency_tickets = set()
        self._permits = {}
        self._owned = 0
        self._active = 0
        self._peak = 0
        self._helper_submissions = 0
        self._helpers = BoundedSemaphore(1)
        self._near_helpers = BoundedSemaphore(1)
        self._extra = BoundedSemaphore(1)
        self._background_lanes = BoundedSemaphore(2)
        self._background_pool = QThreadPool()
        self._background_pool.setMaxThreadCount(2)
        self._auto_background_lanes = BoundedSemaphore(8)
        self._auto_background_pool = QThreadPool()
        self._auto_background_pool.setMaxThreadCount(8)
        self._emergency_lane = BoundedSemaphore(1)
        self._emergency_pool = QThreadPool()
        self._emergency_pool.setMaxThreadCount(1)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='raster-spread')
        self._near_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='raster-near-spread')
        self._waiters: dict[object, tuple[int, object]] = {}
        self._sequence = 0

    @property
    def limit(self):
        with self._condition:
            if self._autonomous():
                return self._adaptive_policy.normal
            fixed = min(self._limits.values(), default=self._default_limit)
            return min(fixed, self._adaptive_policy.normal) if self._auto_limits else fixed

    def _autonomous(self):
        return bool(self._auto_limits) and not self._limits and bool(self._modes) and all(
            mode == 'auto' for mode in self._modes.values())

    def configure_auto(self, owner, maximum=8, *, emergency=True):
        with self._condition:
            self._auto_limits[owner] = (max(4, min(12, int(maximum))), bool(emergency))
            self._adaptive_policy.set_maximum(min(v[0] for v in self._auto_limits.values()))
            if self._adaptive_policy.sample is None:
                self._adaptive_policy.sample = self._resource_probe.sample()
        if not self._adaptive_timer.isActive():
            self._adaptive_timer.start()

    def release_auto(self, owner):
        with self._condition:
            self._auto_limits.pop(owner, None)
            self._auto_pending.pop(owner, None)
            if self._auto_limits:
                self._adaptive_policy.set_maximum(min(v[0] for v in self._auto_limits.values()))
            else:
                self._adaptive_policy = RasterAdaptivePolicy()
                self._adaptive_timer.stop()
            self._condition.notify_all()
        self.signals.capacity_available.emit()

    def set_auto_current_pending(self, owner, pending):
        with self._condition:
            changed = bool(owner in self._auto_pending) != bool(pending)
            if pending and owner in self._auto_limits:
                if owner not in self._auto_pending:
                    self._adaptive_policy.current_pending()
                self._auto_pending[owner] = True
            else:
                self._auto_pending.pop(owner, None)
                if changed and not self._auto_pending:
                    self._adaptive_policy.current_ready()
            self._condition.notify_all()
        if changed:
            self.signals.capacity_available.emit()

    def _sample_adaptive(self):
        sample = self._resource_probe.sample()
        with self._condition:
            self._adaptive_policy.update(sample,
                background_owned=sum(not current() for current in self._permits.values()),
                current_pending=bool(self._auto_pending))
            self._condition.notify_all()
        # A resource recovery can admit work even without a cap change.
        if self._auto_limits:
            self.signals.capacity_available.emit()

    def reserve_auto_memory(self, job, estimated_bytes, *, current=False):
        with self._condition:
            if not current and not self._adaptive_policy.memory_admits(
                    estimated_bytes, sum(self._auto_memory.values())):
                return False
            self._auto_memory[job] = max(0, int(estimated_bytes))
            return True

    def release_auto_memory(self, job):
        with self._condition:
            self._auto_memory.pop(job, None)
            self._condition.notify_all()
        self.signals.capacity_available.emit()

    def observe_auto_job(self, signature, seconds, concurrency):
        with self._condition:
            self._adaptive_policy.observe(signature, seconds, concurrency)

    def configure(self, limit, *, owner=None):
        """Apply admission immediately; never revoke an executing permit.

        Independent Viewer preferences share the smaller cap. Their normal
        parent lanes, global extra lane and single helper remain unchanged.
        """
        limit = limit if type(limit) is int and limit in (3, 4) else 3
        with self._condition:
            if owner is None:
                self._default_limit = limit
            else:
                self._limits[owner] = limit
            self._condition.notify_all()
        self.signals.capacity_available.emit()

    def release_owner(self, owner):
        with self._condition:
            self._limits.pop(owner, None)
            self._condition.notify_all()
        self.signals.capacity_available.emit()

    def configure_near(self, owner, *, enabled, reserve=2):
        with self._condition:
            if enabled:
                self._near_reserves[owner] = max(0, min(2, int(reserve)))
            else:
                self._near_reserves.pop(owner, None)
            self._condition.notify_all()
        self.signals.capacity_available.emit()

    def configure_mode(self, owner, mode=None):
        with self._condition:
            if mode is None:
                self._modes.pop(owner, None)
            else:
                self._modes[owner] = mode
            self._condition.notify_all()
        self.signals.capacity_available.emit()

    def _emergency_enabled(self):
        if self._autonomous():
            return all(value[1] for value in self._auto_limits.values())
        return self.limit == 4 and bool(self._modes) and all(mode == 'burst' for mode in self._modes.values())

    def _normal_owned(self):
        return self._owned - len(self._emergency_tickets)

    def _claimable(self, current, emergency=False):
        total_limit = (self._adaptive_policy.maximum if self._autonomous()
                       else self.limit + (2 if self._emergency_enabled() else 0))
        return self._owned < total_limit and (self._normal_owned() < self.limit or
                (current and emergency and self._emergency_enabled()
                 and len(self._emergency_tickets) < 2))

    def _claim(self, ticket, is_current, emergency=False):
        if self._normal_owned() >= self.limit:
            self._emergency_tickets.add(ticket)
        self._owned += 1
        self._permits[ticket] = is_current

    def adaptive_pause_prefetch(self):
        with self._condition:
            return bool(self._auto_pending) or bool(
                self._auto_limits and self._adaptive_policy.sample is not None
                and self._adaptive_policy.sample.available_bytes < self._adaptive_policy.memory_reserve)

    def _background_available(self, *, already_queued=False):
        if self._auto_pending and not already_queued:
            return False
        if not already_queued and self._auto_limits and self._adaptive_policy.sample is not None and (
                self._adaptive_policy.sample.available_bytes < self._adaptive_policy.memory_reserve):
            return False
        reserve = min(max(self._near_reserves.values(), default=0), max(1, self.limit - 2))
        background = sum(not current() for ticket,current in self._permits.items() if ticket not in self._emergency_tickets)
        return (self._claimable(False) and background < self.limit - reserve
                and not any(current() for _, current in self._waiters.values()))

    def background_available(self):
        with self._condition:
            return self._background_available()

    def full_prefetch_available(self):
        with self._condition:
            return not max(self._near_reserves.values(), default=0) and self._background_available()

    def current_available(self, *, emergency=False):
        with self._condition:
            return self._claimable(True, emergency) and not any(current() for _, current in self._waiters.values())

    def claim_background(self, is_current, *, emergency=False):
        with self._condition:
            if not (self.current_available(emergency=emergency) if is_current() else self._background_available()):
                return None
            ticket = object()
            self._claim(ticket, is_current, emergency)
            return _ExecutionLease(self, ticket)

    def start_emergency(self, job, priority):
        if not self._emergency_lane.acquire(blocking=False):
            return None
        job.emergency_lane = True
        job.native_lane_release = self._release_emergency_lane
        try:
            self._emergency_pool.start(job, int(priority))
            return True
        except RuntimeError:
            job.native_lane_release = None
            self._release_emergency_lane()
            return False

    def _release_emergency_lane(self):
        self._emergency_lane.release()
        self.signals.capacity_available.emit()

    def take_emergency(self, job):
        return self._emergency_pool.tryTake(job)

    def start_background(self, job, priority, *, adaptive=False):
        lanes = self._auto_background_lanes if adaptive else self._background_lanes
        pool = self._auto_background_pool if adaptive else self._background_pool
        if not lanes.acquire(blocking=False):
            return None
        job.auxiliary_lane = True
        job.auto_auxiliary_lane = adaptive
        release = self._release_auto_background_lane if adaptive else self._release_background_lane
        job.native_lane_release = release
        try:
            pool.start(job, int(priority))
            return True
        except RuntimeError:
            job.native_lane_release = None
            release()
            return False

    def _release_background_lane(self):
        self._background_lanes.release()
        self.signals.capacity_available.emit()

    def _release_auto_background_lane(self):
        self._auto_background_lanes.release()
        self.signals.capacity_available.emit()

    def take_background(self, job):
        return (self._auto_background_pool if getattr(job, 'auto_auxiliary_lane', False)
                else self._background_pool).tryTake(job)

    @contextmanager
    def parent(self, cancelled, is_current, *, emergency=False):
        ticket = object()
        acquired = False
        with self._condition:
            self._sequence += 1
            self._waiters[ticket] = (self._sequence, is_current)
            while not cancelled.is_set():
                winner = min(self._waiters, key=lambda item: (
                    not self._waiters[item][1](), self._waiters[item][0],
                ))
                if (self._claimable(is_current(), emergency) and winner is ticket
                        and (is_current() or self._background_available(already_queued=True))):
                    self._claim(ticket, is_current, emergency)
                    acquired = True
                    break
                self._condition.wait(0.02)
            self._waiters.pop(ticket)
            self._condition.notify_all()
        try:
            yield acquired
        finally:
            if acquired:
                self._release(ticket)

    def helper(self, operation, *, is_current=lambda: True, near=False, emergency=False):
        background = not is_current()
        use_near = near or (background and bool(self._near_reserves))
        helpers = self._near_helpers if use_near else self._helpers
        executor = self._near_executor if use_near else self._executor
        if not helpers.acquire(blocking=False):
            # A former current can retain its native helper after a warm
            # navigation demotes it. Let urgent work use the idle near
            # executor, rather than waiting for that non-preemptible child.
            if use_near or background or not self._near_reserves or not self._near_helpers.acquire(blocking=False):
                return None
            helpers, executor = self._near_helpers, self._near_executor
        ticket = object()
        with self._condition:
            background = not is_current()
            # Waiting current work gets the spare permit before speculation.
            if (not self._claimable(not background, emergency)
                    or any(current() for _, current in self._waiters.values())
                    or (background and (self._waiters or not self._background_available()))):
                helpers.release()
                return None
            self._claim(ticket, is_current, emergency)
            self._helper_submissions += 1

        def run():
            try:
                return operation()
            finally:
                self._release(ticket)
                helpers.release()
        try:
            return executor.submit(run)
        except BaseException:
            with self._condition:
                self._owned -= 1
                self._permits.pop(ticket)
                self._emergency_tickets.discard(ticket)
                self._condition.notify_all()
            helpers.release()
            raise

    def _release(self, ticket, *, notify=True):
        with self._condition:
            self._owned -= 1
            self._permits.pop(ticket)
            self._emergency_tickets.discard(ticket)
            self._condition.notify_all()
        if notify:
            self.signals.capacity_available.emit()

    @contextmanager
    def native_operation(self):
        with self._condition:
            self._active += 1
            self._peak = max(self._peak, self._active)
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1

    def try_extra_lane(self):
        return self._extra.acquire(blocking=False)

    def release_extra_lane(self):
        self._extra.release()
        self.signals.capacity_available.emit()

    def debug_values(self):
        with self._condition:
            return {'native_limit': self.limit, 'active_native_jobs': self._active,
                    'effective_native_limit': (self._adaptive_policy.maximum if self._autonomous()
                        else self.limit + (2 if self._emergency_enabled() else 0)),
                    'adaptive_normal_limit': self._adaptive_policy.normal,
                    'adaptive_enabled': bool(self._auto_limits),
                    'adaptive_autonomous': self._autonomous(),
                    'adaptive_maximum': self._adaptive_policy.maximum,
                    'adaptive_reason': self._adaptive_policy.reason,
                    'adaptive_current_pending': len(self._auto_pending),
                    'adaptive_reserved_scratch_bytes': sum(self._auto_memory.values()),
                    'adaptive_cpu_busy': (self._adaptive_policy.sample.cpu_busy
                        if self._adaptive_policy.sample is not None else None),
                    'adaptive_available_bytes': (self._adaptive_policy.sample.available_bytes
                        if self._adaptive_policy.sample is not None else None),
                    'emergency_permits': len(self._emergency_tickets),
                    'normal_permits': self._normal_owned(),
                    'background_permits': sum(not current() for current in self._permits.values()),
                    'peak_native_jobs': self._peak, 'owned_permits': self._owned,
                    'waiting_jobs': len(self._waiters), 'spread_helper_submissions': self._helper_submissions}


RASTER_EXECUTION_BUDGET = RasterExecutionBudget()
