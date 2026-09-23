from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
import sys
from time import monotonic
from typing import Final


MIB: Final = 1024 * 1024
GIB: Final = 1024 * MIB

VIEWER_MEMORY_MODES: Final[tuple[str, ...]] = (
    "auto",
    "minimal",
    "256",
    "512",
    "1024",
    "2048",
    "4096",
    "8192",
    "16384",
    "32768",
)

VIEWER_MEMORY_MODE_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("最小限", "minimal"),
    ("256 MB前後", "256"),
    ("512 MB前後", "512"),
    ("1 GB前後", "1024"),
    ("2 GB前後", "2048"),
    ("4 GB前後", "4096"),
    ("8 GB前後", "8192"),
    ("16 GB前後", "16384"),
    ("32 GB前後", "32768"),
    ("自動", "auto"),
)

_MODE_MIB: Final[dict[str, int]] = {
    "minimal": 128,
    "256": 256,
    "512": 512,
    "1024": 1024,
    "2048": 2048,
    "4096": 4096,
    "8192": 8192,
    "16384": 16384,
    "32768": 32768,
}
_AUTO_BUCKETS_MIB: Final[tuple[int, ...]] = tuple(_MODE_MIB.values())

# The user-selected value is a hard cache ceiling, not an allocation request.
# Background work stays below the active soft target so a decoder result and Qt
# native allocations have headroom.  An inactive Viewer releases substantially
# more distant cache without changing the user's configured ceiling.
_ACTIVE_SOFT_NUMERATOR: Final = 7
_ACTIVE_SOFT_DENOMINATOR: Final = 8
_INACTIVE_SOFT_NUMERATOR: Final = 1
_INACTIVE_SOFT_DENOMINATOR: Final = 2
_PRESSURE_GRANULARITY_BYTES: Final = 16 * MIB
_MIN_RECOVERY_STEP_BYTES: Final = 64 * MIB
_MIN_RECOVERY_HEADROOM_BYTES: Final = 32 * MIB
# These govern *observations*, not a second image scheduler. The Window owns
# the existing five-second pressure timer. Growth changes permission only.
_AUTO_GROWTH_SETTLE_SECONDS: Final = 10.0
_AUTO_GROWTH_INTERVAL_SECONDS: Final = 5.0
_AUTO_GROWTH_MAX_SAMPLE_GAP_SECONDS: Final = 15.0
_AUTO_GROWTH_STEP_BYTES: Final = 256 * MIB
_INACTIVE_CACHE_GRACE_SECONDS: Final = 10.0


@dataclass(frozen=True, slots=True)
class PhysicalMemorySnapshot:
    total_physical_bytes: int
    available_physical_bytes: int
    process_working_set_bytes: int


@dataclass(frozen=True, slots=True)
class ViewerMemoryResolution:
    mode: str
    hard_limit_bytes: int
    active_soft_target_bytes: int
    inactive_soft_target_bytes: int
    snapshot: PhysicalMemorySnapshot | None
    current_cache_bytes: int
    os_reserve_bytes: int
    pressure_ceiling_bytes: int

    @property
    def bytes(self) -> int:
        """Backward-compatible alias for the former single budget."""
        return self.hard_limit_bytes

    @property
    def mib(self) -> int:
        """Backward-compatible MiB view of :attr:`hard_limit_bytes`."""
        return self.hard_limit_bytes // MIB

    def target_bytes_for(self, *, active: bool) -> int:
        """Return the background population target for the Viewer state."""
        if active:
            return self.active_soft_target_bytes
        return self.inactive_soft_target_bytes

    def debug_values(self, *, active: bool = True) -> dict[str, int | str | None]:
        """Return benchmark/debug data without producing a production log."""
        snapshot = self.snapshot
        return {
            "mode": self.mode,
            "hard_limit_bytes": self.hard_limit_bytes,
            "active_soft_target_bytes": self.active_soft_target_bytes,
            "inactive_soft_target_bytes": self.inactive_soft_target_bytes,
            "selected_soft_target_bytes": self.target_bytes_for(active=active),
            "pressure_ceiling_bytes": self.pressure_ceiling_bytes,
            "os_reserve_bytes": self.os_reserve_bytes,
            "current_cache_bytes": self.current_cache_bytes,
            "total_physical_bytes": (
                snapshot.total_physical_bytes if snapshot is not None else None
            ),
            "available_physical_bytes": (
                snapshot.available_physical_bytes if snapshot is not None else None
            ),
            "process_working_set_bytes": (
                snapshot.process_working_set_bytes if snapshot is not None else None
            ),
        }


def _soft_target_bytes(hard_limit_bytes: int, *, active: bool) -> int:
    hard_limit = max(0, int(hard_limit_bytes))
    if active:
        return (
            hard_limit * _ACTIVE_SOFT_NUMERATOR // _ACTIVE_SOFT_DENOMINATOR
        )
    return hard_limit * _INACTIVE_SOFT_NUMERATOR // _INACTIVE_SOFT_DENOMINATOR


def _os_reserve_bytes(total_physical_bytes: int) -> int:
    total = max(0, int(total_physical_bytes))
    return min(total, max(2 * GIB, total // 5))


def _safe_cache_capacity_bytes(
    snapshot: PhysicalMemorySnapshot,
    *,
    current_cache_bytes: int,
) -> tuple[int, int]:
    """Return cache capacity that preserves the OS reserve and its reserve.

    The cache is part of both process working set and unavailable physical
    memory.  Add it back exactly once before calculating how much cache can
    remain, while charging non-cache process memory separately.
    """
    total = max(0, int(snapshot.total_physical_bytes))
    available = min(total, max(0, int(snapshot.available_physical_bytes)))
    working_set = max(0, int(snapshot.process_working_set_bytes))
    cache_bytes = max(0, int(current_cache_bytes))
    reserve = _os_reserve_bytes(total)
    non_cache_working_set = max(0, working_set - cache_bytes)
    available_bound = max(0, cache_bytes + available - reserve)
    total_bound = max(0, total - reserve - non_cache_working_set)
    return min(available_bound, total_bound), reserve


def _resolution(
    mode: str,
    hard_limit_bytes: int,
    *,
    snapshot: PhysicalMemorySnapshot | None,
    current_cache_bytes: int,
    os_reserve_bytes: int,
    pressure_ceiling_bytes: int | None = None,
) -> ViewerMemoryResolution:
    hard_limit = max(0, int(hard_limit_bytes))
    pressure_ceiling = hard_limit
    if pressure_ceiling_bytes is not None:
        pressure_ceiling = max(
            0,
            min(hard_limit, int(pressure_ceiling_bytes)),
        )
    return ViewerMemoryResolution(
        mode=mode,
        hard_limit_bytes=hard_limit,
        active_soft_target_bytes=min(
            _soft_target_bytes(hard_limit, active=True),
            pressure_ceiling,
        ),
        inactive_soft_target_bytes=min(
            _soft_target_bytes(hard_limit, active=False),
            pressure_ceiling,
        ),
        snapshot=snapshot,
        current_cache_bytes=max(0, int(current_cache_bytes)),
        os_reserve_bytes=max(0, int(os_reserve_bytes)),
        pressure_ceiling_bytes=pressure_ceiling,
    )


def normalize_viewer_memory_mode(value: object) -> str:
    mode = str(value).strip().lower()
    return mode if mode in VIEWER_MEMORY_MODES else "auto"


def viewer_memory_mode_from_legacy_mib(value: object) -> str:
    """Map the old free-form MiB limit to the nearest stable mode."""
    if value is None or str(value).strip().casefold() == "auto":
        return "auto"
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return "auto"
    if parsed <= 0:
        return "auto"
    memory_mib = max(128, min(32768, parsed))
    nearest = min(
        _AUTO_BUCKETS_MIB,
        key=lambda candidate: (abs(candidate - memory_mib), candidate),
    )
    return "minimal" if nearest == 128 else str(nearest)


def read_physical_memory_snapshot() -> PhysicalMemorySnapshot | None:
    """Return a single Windows memory snapshot without adding a dependency."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = (
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            )

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = (
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            )

        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GlobalMemoryStatusEx.argtypes = [
            ctypes.POINTER(MEMORYSTATUSEX)
        ]
        kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        process = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        ):
            return None
        return PhysicalMemorySnapshot(
            total_physical_bytes=max(0, int(status.ullTotalPhys)),
            available_physical_bytes=max(0, int(status.ullAvailPhys)),
            process_working_set_bytes=max(0, int(counters.WorkingSetSize)),
        )
    except Exception:
        return None


def resolve_viewer_memory_budget(
    mode: object,
    *,
    snapshot: PhysicalMemorySnapshot | None = None,
    current_cache_bytes: int = 0,
) -> ViewerMemoryResolution:
    """Resolve an initial immutable budget snapshot.

    Initial Auto selection is bucketed. ResolvedViewerMemoryPolicy owns later
    pressure changes and opt-in, demand-driven growth; this function never
    samples continuously or allocates cache storage.
    """
    normalized = normalize_viewer_memory_mode(mode)
    cache_bytes = max(0, int(current_cache_bytes))
    if normalized != "auto":
        hard_limit_bytes = _MODE_MIB[normalized] * MIB
        pressure_ceiling = hard_limit_bytes
        reserve = 0
        if snapshot is not None and snapshot.total_physical_bytes > 0:
            capacity, reserve = _safe_cache_capacity_bytes(
                snapshot,
                current_cache_bytes=cache_bytes,
            )
            pressure_ceiling = min(hard_limit_bytes, capacity)
        return _resolution(
            normalized,
            hard_limit_bytes,
            snapshot=snapshot,
            current_cache_bytes=cache_bytes,
            os_reserve_bytes=reserve,
            pressure_ceiling_bytes=pressure_ceiling,
        )

    actual_snapshot = snapshot if snapshot is not None else read_physical_memory_snapshot()
    if actual_snapshot is None or actual_snapshot.total_physical_bytes <= 0:
        # A safe, useful fallback for non-Windows tests and API failure.  It is
        # intentionally a stable bucket rather than a guessed live value.
        return _resolution(
            "auto",
            512 * MIB,
            snapshot=None,
            current_cache_bytes=cache_bytes,
            os_reserve_bytes=0,
        )

    candidate, reserve = _safe_cache_capacity_bytes(
        actual_snapshot,
        current_cache_bytes=cache_bytes,
    )
    candidate_mib = candidate // MIB
    selected_mib = 128
    for bucket_mib in _AUTO_BUCKETS_MIB:
        if bucket_mib > candidate_mib:
            break
        selected_mib = bucket_mib
    return _resolution(
        "auto",
        selected_mib * MIB,
        snapshot=actual_snapshot,
        current_cache_bytes=cache_bytes,
        os_reserve_bytes=reserve,
        pressure_ceiling_bytes=candidate,
    )


class ResolvedViewerMemoryPolicy:
    """Fixed user ceilings or demand-driven Auto growth, with pressure targets.

    Fixed modes never exceed their selected hard limit. Auto can expand only
    for the active, capacity-limited raster reader after sustained headroom.
    Pressure still shrinks soft targets immediately; activation grace affects
    retention only and never raises the pressure ceiling.
    """

    __slots__ = (
        "_active",
        "_clock",
        "_inactive_since",
        "_auto_growth_since",
        "_last_auto_growth_at",
        "_last_observation_at",
        "_pressure_ceiling_bytes",
        "_resolution",
    )

    def __init__(
        self,
        mode: object,
        *,
        snapshot: PhysicalMemorySnapshot | None = None,
        current_cache_bytes: int = 0,
        active: bool = True,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._active = bool(active)
        self._clock = clock
        # Starting inactive is not a brief focus transition.
        self._inactive_since: float | None = None
        self._auto_growth_since: float | None = None
        self._last_auto_growth_at: float | None = None
        self._last_observation_at: float | None = None
        self._resolution = resolve_viewer_memory_budget(
            mode,
            snapshot=snapshot,
            current_cache_bytes=current_cache_bytes,
        )
        self._pressure_ceiling_bytes = (
            self._resolution.pressure_ceiling_bytes
        )

    @property
    def resolution(self) -> ViewerMemoryResolution:
        return self._resolution

    @property
    def active(self) -> bool:
        return self._active

    @property
    def hard_limit_bytes(self) -> int:
        return self._resolution.hard_limit_bytes

    @property
    def target_bytes(self) -> int:
        return self._resolution.target_bytes_for(
            active=self._active or self.inactive_grace_active,
        )

    @property
    def inactive_grace_active(self) -> bool:
        if self._active or self._inactive_since is None:
            return False
        elapsed = self._clock() - self._inactive_since
        return isfinite(elapsed) and 0 <= elapsed < _INACTIVE_CACHE_GRACE_SECONDS

    def set_active(self, active: bool) -> int:
        """Keep brief focus changes from immediately discarding distant frames."""
        normalized = bool(active)
        if normalized != self._active:
            self._inactive_since = None if normalized else self._clock()
            self._active = normalized
            self.reset_growth_observation()
        return self.target_bytes

    def reset_growth_observation(self) -> None:
        """A missing sample, mode change, or focus change cannot prove recovery."""
        self._auto_growth_since = None
        self._last_observation_at = None

    def reconfigure(
        self,
        mode: object,
        *,
        snapshot: PhysicalMemorySnapshot | None = None,
        current_cache_bytes: int = 0,
    ) -> ViewerMemoryResolution:
        """Explicitly re-resolve a changed setting and reset pressure state."""
        self.reset_growth_observation()
        self._last_auto_growth_at = None
        self._resolution = resolve_viewer_memory_budget(
            mode,
            snapshot=snapshot,
            current_cache_bytes=current_cache_bytes,
        )
        self._pressure_ceiling_bytes = (
            self._resolution.pressure_ceiling_bytes
        )
        return self._resolution

    def observe_memory_pressure(
        self,
        snapshot: PhysicalMemorySnapshot,
        *,
        current_cache_bytes: int,
        auto_growth_requested: bool = False,
        auto_growth_cap_bytes: int | None = None,
    ) -> ViewerMemoryResolution:
        """Apply a live sample, optionally expanding a capacity-limited Auto cache.

        Soft pressure targets shrink immediately and retain their bounded
        recovery rule. Opt-in Auto hard growth requires ten seconds of useful
        headroom and adds at most 256 MiB per five seconds. The owner supplies
        the observation cadence; no Qt timer or logging is hidden here.
        """
        # Invalid observations cannot establish sustained recovery (or mutate
        # the last good limits). The Windows reader returns integer byte counts.
        values = (
            snapshot.total_physical_bytes,
            snapshot.available_physical_bytes,
            snapshot.process_working_set_bytes,
            current_cache_bytes,
        )
        if (
            any(type(value) is not int for value in values)
            or snapshot.total_physical_bytes <= 0
            or not 0 <= snapshot.available_physical_bytes <= snapshot.total_physical_bytes
            or snapshot.process_working_set_bytes < 0
            or current_cache_bytes < 0
        ):
            self.reset_growth_observation()
            return self._resolution
        cache_bytes = max(0, int(current_cache_bytes))
        capacity, reserve = _safe_cache_capacity_bytes(
            snapshot,
            current_cache_bytes=cache_bytes,
        )
        hard_limit = self._maybe_expand_auto_limit(
            capacity,
            current_cache_bytes=cache_bytes,
            requested=auto_growth_requested,
            external_cap_bytes=auto_growth_cap_bytes,
        )
        desired_ceiling = min(hard_limit, capacity)
        if desired_ceiling < hard_limit:
            desired_ceiling = (
                desired_ceiling // _PRESSURE_GRANULARITY_BYTES
            ) * _PRESSURE_GRANULARITY_BYTES

        current_ceiling = self._pressure_ceiling_bytes
        if desired_ceiling < current_ceiling:
            next_ceiling = desired_ceiling
        else:
            recovery_headroom = max(
                _MIN_RECOVERY_HEADROOM_BYTES,
                hard_limit // 32,
            )
            if desired_ceiling < current_ceiling + recovery_headroom:
                next_ceiling = current_ceiling
            else:
                recovery_step = max(
                    _MIN_RECOVERY_STEP_BYTES,
                    hard_limit // 8,
                )
                next_ceiling = min(
                    desired_ceiling,
                    current_ceiling + recovery_step,
                )

        self._pressure_ceiling_bytes = next_ceiling
        self._resolution = _resolution(
            self._resolution.mode,
            hard_limit,
            snapshot=snapshot,
            current_cache_bytes=cache_bytes,
            os_reserve_bytes=reserve,
            pressure_ceiling_bytes=next_ceiling,
        )
        return self._resolution

    def _maybe_expand_auto_limit(
        self,
        capacity_bytes: int,
        *,
        current_cache_bytes: int,
        requested: bool,
        external_cap_bytes: int | None,
    ) -> int:
        hard = self._resolution.hard_limit_bytes
        now = self._clock()
        if not isfinite(now):
            self.reset_growth_observation()
            return hard
        last = self._last_observation_at
        if last is None or not 0 <= now - last <= _AUTO_GROWTH_MAX_SAMPLE_GAP_SECONDS:
            self._auto_growth_since = None
        self._last_observation_at = now
        cap = min(max(_AUTO_BUCKETS_MIB) * MIB, max(0, capacity_bytes))
        if external_cap_bytes is not None:
            # Optional grant from an existing application memory arbiter.
            # Do not construct a second global budget owner here.
            cap = min(cap, max(0, int(external_cap_bytes)))
        cap = cap // _PRESSURE_GRANULARITY_BYTES * _PRESSURE_GRANULARITY_BYTES
        if not (
            self._resolution.mode == "auto"
            and self._active
            and requested
            and current_cache_bytes >= hard * 3 // 4
            and self._pressure_ceiling_bytes >= hard * 7 // 8
            and cap >= hard + _MIN_RECOVERY_HEADROOM_BYTES
        ):
            self._auto_growth_since = None
            return hard
        if self._auto_growth_since is None:
            self._auto_growth_since = now
            return hard
        if now - self._auto_growth_since < _AUTO_GROWTH_SETTLE_SECONDS:
            return hard
        last_growth = self._last_auto_growth_at
        if last_growth is not None and now - last_growth < _AUTO_GROWTH_INTERVAL_SECONDS:
            return hard
        self._last_auto_growth_at = now
        return min(cap, hard + _AUTO_GROWTH_STEP_BYTES)

    def debug_values(self) -> dict[str, int | str | bool | None]:
        """Expose the resolved policy to tests/benchmarks without logging."""
        values: dict[str, int | str | bool | None] = dict(
            self._resolution.debug_values(active=self._active)
        )
        values["active"] = self._active
        values["inactive_grace_active"] = self.inactive_grace_active
        values["selected_soft_target_bytes"] = self.target_bytes
        values["auto_growth_waiting"] = self._auto_growth_since is not None
        return values
