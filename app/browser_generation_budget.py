"""Process-wide, cancellation-aware admission for original-image work only."""
from contextlib import contextmanager
import os
from threading import Condition
from time import monotonic
from PIL import Image
from PySide6.QtCore import QObject, QCoreApplication, QThread, QTimer
from .browser_model import BrowserItemKind
from .viewer_memory_policy import read_physical_memory_snapshot

MIB = 1024 * 1024


def browser_read_workers():
    # Saved thumbnails plus their small display derivatives; original-image
    # decode has its own scratch admission and must not occupy these slots.
    return min(8, max(2, (os.cpu_count() or 2) // 2))


def browser_generation_workers():
    # ZipPla 1.7.2.9's ordinary CPU/2 work allowance, translated to a shared
    # process admission ceiling; the independent Viewer lane stays reserved.
    return max(1, (os.cpu_count() or 2) // 2)


class GenerationAdmissionDeferred(Exception):
    def __init__(self, cost):
        self.cost = cost


class BrowserGenerationBudget:
    def __init__(self):
        self.condition = Condition()
        self.active = 0
        self.bytes = 0
        self.peak_active = 0
        self.peak_bytes = 0
        self.snapshot_reader = None
        self.max_active = browser_generation_workers()
        self.deferred_count = 0

    @staticmethod
    def estimate(item):
        # Some optional Pillow plugins decode pixels inside Image.open(). Do
        # not invoke them before a scratch reservation exists. Use explicit
        # conventional header parsers; everything else gets the unknown cost.
        header_format = {
            '.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.bmp': 'BMP',
            '.gif': 'GIF', '.tif': 'TIFF', '.tiff': 'TIFF',
        }.get(item.path.suffix.lower()) if item.kind == BrowserItemKind.IMAGE else None
        if header_format is not None:
            try:
                with Image.open(item.path, formats=(header_format,)) as image:
                    return max(16*MIB, image.width * image.height * 20)
            except (OSError, ValueError):
                pass
        return 256*MIB  # Unknown container/vector/native scratch, conservatively.

    def _allowance(self):
        snapshot = (read_physical_memory_snapshot() if self.snapshot_reader is None
                    else self.snapshot_reader())
        if snapshot is _STALE_SAMPLE:
            return 0, 0
        if snapshot is None:
            # Monitoring unavailable: one decoder at a time, rather than a
            # guessed abundant-RAM value or a permanent failure on large jobs.
            return 1, None
        total = getattr(snapshot, 'total_physical_bytes', 0)
        reserve = min(2*1024*MIB, max(512*MIB, total//10)) if total else 2*1024*MIB
        headroom = max(0, snapshot.available_physical_bytes - reserve)
        commit = getattr(snapshot, 'available_commit_bytes', None)
        if commit is not None:
            headroom = min(headroom, max(0, commit - reserve))
        # Keep half of the observed surplus for Viewer, other applications and
        # estimate error. No fixed aggregate scratch ceiling on roomy machines.
        return self.max_active, headroom // 2

    def _can_admit(self, cost):
        workers, allowance = self._allowance()
        return (self.active < workers
                and (allowance is None or self.bytes + cost <= allowance))

    def can_admit(self, cost):
        with self.condition:
            return self._can_admit(cost)

    def _reserve(self, cost):
        self.active += 1
        self.bytes += cost
        self.peak_active = max(self.peak_active, self.active)
        self.peak_bytes = max(self.peak_bytes, self.bytes)

    def try_reserve(self, cost):
        with self.condition:
            if not self._can_admit(cost):
                return False
            self._reserve(cost)
            return True

    def release(self, cost):
        with self.condition:
            self.active -= 1
            self.bytes -= cost
            self.condition.notify_all()

    @contextmanager
    def hold(self, item, cancelled, *, cost=None, wait=True, reserved=False):
        cost = self.estimate(item) if cost is None else cost
        if not reserved:
            with self.condition:
                while True:
                    if cancelled.is_set():
                        raise InterruptedError
                    # Recheck after every wait; published memory can change.
                    if self._can_admit(cost):
                        break
                    self.deferred_count += 1
                    if not wait:
                        raise GenerationAdmissionDeferred(cost)
                    self.condition.wait(.05)
                if cancelled.is_set():
                    raise InterruptedError
                self._reserve(cost)
        try:
            if cancelled.is_set():
                raise InterruptedError
            yield
        finally:
            self.release(cost)


BROWSER_GENERATION_BUDGET = BrowserGenerationBudget()

_STALE_SAMPLE = object()


class _GenerationMemorySampler(QObject):
    """Publish immutable OS samples; native monitoring stays on the GUI owner."""
    def __init__(self, application, owner_thread):
        super().__init__(application)
        # Retain the binding's GUI-thread wrapper with the application-owned
        # publisher, rather than relying on temporary currentThread() results
        # across native pool completion and subsequent codec callbacks.
        self.owner_thread = owner_thread
        self.sample = None
        self.sampled_at = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        self.refresh()
        BROWSER_GENERATION_BUDGET.snapshot_reader = self.read
        self.timer.start()

    def refresh(self):
        if monotonic() - self.sampled_at < .1:
            return
        sample = read_physical_memory_snapshot()
        with BROWSER_GENERATION_BUDGET.condition:
            self.sample = sample
            self.sampled_at = monotonic()
            BROWSER_GENERATION_BUDGET.condition.notify_all()

    def read(self):
        return self.sample if monotonic()-self.sampled_at <= 1 else _STALE_SAMPLE


def refresh_generation_memory():
    application = QCoreApplication.instance()
    if application is None:
        return
    current_thread = QThread.currentThread()
    if current_thread != application.thread():
        return
    sampler = getattr(application, '_nivis_generation_memory_sampler', None)
    if sampler is None:
        sampler = _GenerationMemorySampler(application, current_thread)
        application._nivis_generation_memory_sampler = sampler
    else:
        sampler.refresh()
