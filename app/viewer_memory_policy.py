from __future__ import annotations

from dataclasses import dataclass
import sys
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


@dataclass(frozen=True, slots=True)
class PhysicalMemorySnapshot:
    total_physical_bytes: int
    available_physical_bytes: int
    process_working_set_bytes: int


@dataclass(frozen=True, slots=True)
class ViewerMemoryResolution:
    mode: str
    bytes: int
    snapshot: PhysicalMemorySnapshot | None
    current_cache_bytes: int
    os_reserve_bytes: int

    @property
    def mib(self) -> int:
        return self.bytes // MIB


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
    """Resolve one immutable book/session budget.

    Auto is deliberately bucketed and is resolved from one snapshot. Callers
    keep the returned value rather than continuously following fluctuating
    ``available`` memory.
    """
    normalized = normalize_viewer_memory_mode(mode)
    cache_bytes = max(0, int(current_cache_bytes))
    if normalized != "auto":
        budget_bytes = _MODE_MIB[normalized] * MIB
        return ViewerMemoryResolution(
            normalized,
            budget_bytes,
            snapshot,
            cache_bytes,
            0,
        )

    actual_snapshot = snapshot if snapshot is not None else read_physical_memory_snapshot()
    if actual_snapshot is None or actual_snapshot.total_physical_bytes <= 0:
        # A safe, useful fallback for non-Windows tests and API failure.  It is
        # intentionally a stable bucket rather than a guessed live value.
        return ViewerMemoryResolution("auto", 512 * MIB, None, cache_bytes, 0)

    total = max(0, int(actual_snapshot.total_physical_bytes))
    available = min(total, max(0, int(actual_snapshot.available_physical_bytes)))
    working_set = max(0, int(actual_snapshot.process_working_set_bytes))
    reserve = min(total, max(2 * GIB, total // 5))
    non_cache_working_set = max(0, working_set - cache_bytes)
    # Existing cache is already reflected in ``available``. Add it back once
    # to avoid self-shrink, but still charge any reserve shortfall against the
    # cache so a large resident cache cannot be frozen under OS pressure. The
    # total-RAM bound also guards an inconsistent/stale available reading.
    available_bound = max(0, cache_bytes + available - reserve)
    total_bound = max(0, total - reserve - non_cache_working_set)
    candidate = min(available_bound, total_bound)
    candidate_mib = candidate // MIB
    selected_mib = 128
    for bucket_mib in _AUTO_BUCKETS_MIB:
        if bucket_mib > candidate_mib:
            break
        selected_mib = bucket_mib
    return ViewerMemoryResolution(
        "auto",
        selected_mib * MIB,
        actual_snapshot,
        cache_bytes,
        reserve,
    )
