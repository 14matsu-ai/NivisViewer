"""Bounded, process-wide raster concurrency feedback; no GUI/source ownership."""
from collections import OrderedDict
from dataclasses import dataclass
import os
import sys
from time import monotonic


@dataclass(frozen=True)
class RasterResourceSample:
    cpu_busy: float | None
    available_bytes: int
    total_bytes: int


class RasterResourceProbe:
    def __init__(self):
        self._previous_cpu = None
        self._kernel = None
        self._memory_type = None
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            # Private function objects: other memory samplers set ctypes
            # signatures too, so do not share ctypes.windll's cached handles.
            self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            self._kernel.GetSystemTimes.argtypes = [ctypes.POINTER(wintypes.FILETIME)] * 3
            self._kernel.GetSystemTimes.restype = wintypes.BOOL
            class MemoryStatus(ctypes.Structure):
                _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
                    (name, ctypes.c_ulonglong) for name in
                    ('total', 'available', 'total_page', 'available_page',
                     'total_virtual', 'available_virtual', 'extended')]
            self._memory_type = MemoryStatus
            self._kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
            self._kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL

    def sample(self):
        if self._kernel is None:
            return None
        import ctypes
        from ctypes import wintypes
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        status = self._memory_type()
        status.length = ctypes.sizeof(status)
        if not self._kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        busy = None
        if self._kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            ticks = lambda value: (value.dwHighDateTime << 32) | value.dwLowDateTime
            current = (ticks(idle), ticks(kernel) + ticks(user))
            if self._previous_cpu is not None:
                idle_delta = current[0] - self._previous_cpu[0]
                total_delta = current[1] - self._previous_cpu[1]
                if total_delta > 0:
                    busy = max(0., min(1., 1. - idle_delta / total_delta))
            self._previous_cpu = current
        return RasterResourceSample(busy, int(status.available), int(status.total))


class RasterAdaptivePolicy:
    """Hysteresis and comparable-job latency feedback, never active-job revocation."""
    def __init__(self, maximum=8, *, logical_cpus=None):
        cpus = max(1, int(logical_cpus or os.cpu_count() or 2))
        self.cpu_ceiling = max(1, cpus - 1)
        self.maximum = max(4, min(12, int(maximum)))
        self.normal = min(4 if cpus >= 8 else 2, self.ceiling)
        self.cooldown_until = 0.
        self.last_change = 0.
        self.healthy_samples = 0
        self.sample = None
        self.reason = 'starting'
        self._pending_normal = None
        self._pressure_until = 0.
        self._durations = OrderedDict()

    @property
    def ceiling(self):
        return max(1, min(self.maximum - 2, self.cpu_ceiling))

    @property
    def memory_reserve(self):
        return max(512 * 1024 * 1024, (self.sample.total_bytes // 20) if self.sample else 0)

    def set_maximum(self, maximum):
        self.maximum = max(4, min(12, int(maximum)))
        self.normal = min(self.normal, self.ceiling)

    def current_pending(self, *, now=None):
        now = monotonic() if now is None else now
        if self._pending_normal is None:
            self._pending_normal = self.normal
        self.normal = min(self.normal, 2)
        self.cooldown_until = max(self.cooldown_until, now + .5)
        self.healthy_samples = 0
        self.reason = 'current_pending'

    def current_ready(self, *, now=None):
        now = monotonic() if now is None else now
        previous, self._pending_normal = self._pending_normal, None
        if (previous is not None and now >= self._pressure_until and self.sample is not None
                and self.sample.available_bytes >= self.memory_reserve
                and (self.sample.cpu_busy is None or self.sample.cpu_busy < .85)):
            self.normal = min(self.ceiling, max(self.normal, previous))
            self.reason = 'current_ready'

    def observe(self, signature, seconds, concurrency, *, now=None):
        """Compare only non-cancelled pixel jobs of a similar source/size tier."""
        if seconds <= 0:
            return
        now = monotonic() if now is None else now
        baseline = self._durations.get(signature)
        if baseline is not None:
            reference, reference_limit = baseline
            if concurrency > reference_limit and seconds > max(reference * 1.35, reference + .04):
                self.normal = max(1, self.normal - 1)
                self.cooldown_until = max(self.cooldown_until, now + 2.)
                self._pressure_until = max(self._pressure_until, now + 2.)
                self.healthy_samples = 0
                self.reason = 'decode_slowdown'
            if concurrency <= reference_limit:
                baseline = (reference * .8 + seconds * .2, concurrency)
        else:
            baseline = (seconds, concurrency)
        self._durations[signature] = baseline
        self._durations.move_to_end(signature)
        while len(self._durations) > 64:
            self._durations.popitem(last=False)

    def update(self, sample, *, background_owned, current_pending=False, now=None):
        now = monotonic() if now is None else now
        self.sample = sample
        if current_pending:
            self.current_pending(now=now)
            return
        if sample is None:
            self.normal = min(self.normal, 2)
            self.healthy_samples = 0
            self.reason = 'resources_unavailable'
            return
        memory_pressure = sample.available_bytes < self.memory_reserve
        if memory_pressure or (sample.cpu_busy is not None and sample.cpu_busy >= .85):
            self.normal = max(1, self.normal - 1)
            self.cooldown_until = max(self.cooldown_until, now + 2.)
            self._pressure_until = max(self._pressure_until, now + 2.)
            self.healthy_samples = 0
            self.reason = 'memory_pressure' if memory_pressure else 'cpu_pressure'
            return
        if (sample.cpu_busy is not None and sample.cpu_busy <= .65
                and background_owned >= self.normal and now >= self.cooldown_until):
            self.healthy_samples += 1
            if self.healthy_samples >= 2 and now - self.last_change >= 1.:
                self.normal = min(self.ceiling, self.normal + 1)
                self.last_change = now
                self.healthy_samples = 0
                self.reason = 'headroom'
        else:
            self.healthy_samples = 0

    def memory_admits(self, estimated_bytes, reserved_bytes):
        # Retained source/frame budgets are enforced separately by the runtime.
        # This additional guard accounts for approximate full decoder scratch.
        if self.sample is None:
            return reserved_bytes == 0  # Unknown resources: one speculative job.
        return (
            self.sample.available_bytes - self.memory_reserve
            >= max(0, estimated_bytes) + max(0, reserved_bytes))
