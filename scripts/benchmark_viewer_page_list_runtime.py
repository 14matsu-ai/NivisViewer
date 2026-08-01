"""Offscreen A/B benchmark for the Viewer page-list subsystem.

The benchmark deliberately limits A to the superseded *page-list* ownership
model: one ``QListWidgetItem`` per book page, followed by GUI-thread smooth
scaling and ``QPixmap.fromImage`` for every requested thumbnail.  It does not
revive or compare the superseded Viewer frame engine.

B drives the production ``ViewerPageListModel`` and
``ViewerPageListRuntime`` directly.  The source is a real, temporary JPEG ZIP;
logical page ids are mapped to the same deterministic ZIP entry so a very
large virtual book does not require a very large temporary file.  No window is
shown, no native input is synthesized, and no persistent files are written.

The result is measurement evidence for the structural replacement, not a
claim about real-device responsiveness.  Qt/plugin-internal allocations and
copies cannot be counted from Python and are called out explicitly in the
report.
"""

from __future__ import annotations

import argparse
from collections import Counter
import ctypes
import gc
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Lock
import time
from typing import Callable, Iterable, Sequence
import zipfile

# This must be selected before importing PySide6.  An explicitly supplied
# non-native test platform still wins.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem

from app.image_source import ZipImageSource
from app.thumbnail_render import pil_to_qimage
from app.viewer_page_list_runtime import (
    ViewerPageListModel,
    ViewerPageListRuntime,
    ViewerPageThumbnail,
    ViewerPageThumbnailSpec,
)


_MIB = 1024 * 1024
_ARCHIVE_ENTRY = "payload/page.jpg"


class _SourceProbe:
    """Counters shared by the base source and all thumbnail forks."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counts: Counter[str] = Counter()
        self._decode_events: list[dict[str, object]] = []

    def bump(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counts[name] += int(amount)

    def begin_decode(self, mode: str, image_id: str) -> int:
        with self._lock:
            token = len(self._decode_events)
            self._decode_events.append(
                {
                    "mode": str(mode),
                    "image_id": str(image_id),
                    "success": False,
                    "finished": False,
                }
            )
            self._counts["decode_requests"] += 1
            return token

    def finish_decode(self, token: int, *, success: bool) -> None:
        with self._lock:
            event = self._decode_events[token]
            event["success"] = bool(success)
            event["finished"] = True
            self._counts[
                "decode_successes" if success else "decode_non_successes"
            ] += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            counts = {
                name: int(self._counts.get(name, 0))
                for name in (
                    "source_opens",
                    "source_forks",
                    "thumbnail_source_opens",
                    "source_closes",
                    "thumbnail_source_closes",
                    "source_cancel_requests",
                    "zip_entry_reads",
                    "zip_entry_bytes_read",
                    "entry_to_bytesio_materializations",
                    "entry_to_bytesio_bytes",
                    "bytesio_getvalue_full_copies",
                    "decode_requests",
                    "decode_successes",
                    "decode_non_successes",
                    "full_resolution_pillow_images",
                    "target_sized_source_qimages",
                )
            }
            return {
                **counts,
                "decode_event_count": len(self._decode_events),
                "unfinished_decode_events": sum(
                    not bool(event["finished"]) for event in self._decode_events
                ),
            }

    def decode_counts_outside(self, final_ids: set[str]) -> dict[str, int]:
        with self._lock:
            transient = [
                event
                for event in self._decode_events
                if str(event["image_id"]) not in final_ids
            ]
            return {
                "transient_decode_requests": len(transient),
                "transient_decode_successes": sum(
                    bool(event["success"]) for event in transient
                ),
            }


class _ProbeZipImageSource(ZipImageSource):
    """Real ZIP reads/decodes with logical page ids and observable cloning."""

    def __init__(
        self,
        archive_path: str | Path,
        *,
        probe: _SourceProbe,
        decode_delay_seconds: float,
        is_thumbnail_fork: bool = False,
    ) -> None:
        self._probe = probe
        self._decode_delay_seconds = max(0.0, float(decode_delay_seconds))
        self._probe_closed = False
        self._is_thumbnail_fork = bool(is_thumbnail_fork)
        super().__init__(archive_path)
        self._probe.bump("source_opens")
        if self._is_thumbnail_fork:
            self._probe.bump("thumbnail_source_opens")

    def list_images(self) -> list[str]:
        # The runtime receives its book snapshot directly; listing this one-
        # entry physical archive would misrepresent the virtual-book scenario.
        return [_ARCHIVE_ENTRY]

    def fork_for_thumbnail(self):
        self._probe.bump("source_forks")
        return _ProbeZipImageSource(
            self.source_path,
            probe=self._probe,
            decode_delay_seconds=self._decode_delay_seconds,
            is_thumbnail_fork=True,
        )

    def open_image(self, image_id: str) -> Image.Image:
        token = self._probe.begin_decode("full-pillow", image_id)
        success = False
        try:
            result = super().open_image(_ARCHIVE_ENTRY)
            success = True
            self._probe.bump("full_resolution_pillow_images")
            return result
        finally:
            self._probe.finish_decode(token, success=success)

    def open_qimage_at_most(
        self,
        image_id: str,
        maximum_size: tuple[int, int],
    ):
        token = self._probe.begin_decode("target-qimage", image_id)
        success = False
        try:
            result = super().open_qimage_at_most(_ARCHIVE_ENTRY, maximum_size)
            success = bool(result is not None and not result[0].isNull())
            if success:
                self._probe.bump("target_sized_source_qimages")
                # ZipImageSource.open_qimage_at_most currently calls
                # BytesIO.getvalue exactly once after the entry read.
                self._probe.bump("bytesio_getvalue_full_copies")
            return result
        finally:
            self._probe.finish_decode(token, success=success)

    def cancel_image_request(self, image_id: str) -> None:
        self._probe.bump("source_cancel_requests")
        super().cancel_image_request(_ARCHIVE_ENTRY)

    def close(self) -> None:
        if not self._probe_closed:
            self._probe_closed = True
            self._probe.bump("source_closes")
            if self._is_thumbnail_fork:
                self._probe.bump("thumbnail_source_closes")
        super().close()

    def _read_entry_stream(self, image_id, cancelled):
        # The short cancellable hold makes replacement/pause behavior
        # deterministic enough for a quick benchmark without fake workers.
        deadline = time.monotonic() + self._decode_delay_seconds
        while time.monotonic() < deadline:
            self._raise_if_cancelled(cancelled)
            time.sleep(min(0.001, max(0.0, deadline - time.monotonic())))
        stream = super()._read_entry_stream(_ARCHIVE_ENTRY, cancelled)
        byte_count = len(stream.getbuffer())
        self._probe.bump("zip_entry_reads")
        self._probe.bump("zip_entry_bytes_read", byte_count)
        self._probe.bump("entry_to_bytesio_materializations")
        self._probe.bump("entry_to_bytesio_bytes", byte_count)
        return stream


class _MemoryProbe:
    def __init__(self) -> None:
        gc.collect()
        current = _working_set_bytes()
        self.start = current
        self.peak = current

    def observe(self) -> None:
        current = _working_set_bytes()
        if current is not None:
            self.peak = max(self.peak or 0, current)

    def report(self) -> dict[str, float | None]:
        self.observe()
        end = _working_set_bytes()
        return {
            "working_set_start_mib": _mib(self.start),
            "working_set_end_mib": _mib(end),
            "working_set_delta_mib": (
                _mib(end - self.start)
                if end is not None and self.start is not None
                else None
            ),
            "sampled_peak_working_set_mib": _mib(self.peak),
            "sampled_peak_delta_mib": (
                _mib(self.peak - self.start)
                if self.peak is not None and self.start is not None
                else None
            ),
        }


class _LegacyPageListA:
    """Benchmark-only reproduction of the removed eager PageList owner."""

    def __init__(
        self,
        archive_path: Path,
        image_ids: Sequence[str],
        *,
        decode_delay_seconds: float,
        memory: _MemoryProbe,
    ) -> None:
        self.probe = _SourceProbe()
        self.source = _ProbeZipImageSource(
            archive_path,
            probe=self.probe,
            decode_delay_seconds=decode_delay_seconds,
        )
        self.image_ids = tuple(image_ids)
        self.widget = QListWidget()
        self.memory = memory
        self.row_build_ms = 0.0
        self.gui_scale_ms = 0.0
        self.gui_callbacks = 0
        self.qimage_conversions = 0
        self.qpixmap_uploads = 0
        self._icon_bytes_by_page: dict[int, int] = {}

    def build_visible_rows(self) -> None:
        started = time.perf_counter()
        for page, image_id in enumerate(self.image_ids):
            item = QListWidgetItem(f"{page + 1}: {Path(image_id).name}")
            item.setData(Qt.ItemDataRole.UserRole, page)
            self.widget.addItem(item)
        self.row_build_ms += (time.perf_counter() - started) * 1000
        self.memory.observe()

    def render_pages(
        self,
        pages: Iterable[int],
        spec: ViewerPageThumbnailSpec,
    ) -> None:
        for page in pages:
            image_id = self.image_ids[int(page)]
            image = self.source.open_image(image_id)
            try:
                large_qimage = pil_to_qimage(image)
            finally:
                image.close()
            self.qimage_conversions += 1
            self.memory.observe()
            scale_started = time.perf_counter()
            scaled = large_qimage.scaled(
                spec.physical_edge,
                spec.physical_edge,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pixmap = QPixmap.fromImage(scaled)
            self.gui_scale_ms += (time.perf_counter() - scale_started) * 1000
            self.gui_callbacks += 1
            self.qpixmap_uploads += 1
            self.widget.item(int(page)).setIcon(QIcon(pixmap))
            self._icon_bytes_by_page[int(page)] = _qimage_bytes(scaled)
            self.memory.observe()

    def report(
        self,
        *,
        elapsed_ms: float,
        final_pages: Sequence[int],
        memory_report: dict[str, float | None],
    ) -> dict[str, object]:
        final_ids = {self.image_ids[page] for page in final_pages}
        return {
            "implementation": "A: eager QListWidget + GUI smooth scale/upload",
            "elapsed_ms": round(elapsed_ms, 3),
            "gui": {
                "row_build_ms": round(self.row_build_ms, 3),
                "gui_scale_and_pixmap_ms": round(self.gui_scale_ms, 3),
                "row_objects": self.widget.count(),
                "model_rows": self.widget.count(),
                "thumbnail_callbacks": self.gui_callbacks,
                "qimage_conversions_from_full_pillow": self.qimage_conversions,
                "qpixmap_from_image_calls": self.qpixmap_uploads,
                "paint_events_observed": 0,
            },
            "work": {
                "jobs_submitted": 0,
                "logical_thumbnail_work_items": self.gui_callbacks,
                "queued_callbacks": self.gui_callbacks,
                "cancel_requests": 0,
                "stale_results": 0,
                **self.probe.decode_counts_outside(final_ids),
            },
            "cache": {
                "desired_pages": list(final_pages),
                "retained_pages": sorted(self._icon_bytes_by_page),
                "retained_page_count": len(self._icon_bytes_by_page),
                "estimated_retained_thumbnail_bytes": sum(
                    self._icon_bytes_by_page.values()
                ),
                "estimated_total_retained_artifact_bytes": sum(
                    self._icon_bytes_by_page.values()
                ),
                "byte_budget": None,
                "evictions": 0,
            },
            "source": self.probe.snapshot(),
            "memory": memory_report,
            "contracts": {
                "hidden_work_deferred": self.widget.count() == 0,
                "active_job_preemptible": False,
                "per_page_row_objects": True,
            },
        }

    def close(self) -> None:
        self.widget.clear()
        self.widget.deleteLater()
        self.source.close()


class _ProductionPageListB:
    """Thin offscreen harness around the production model/runtime."""

    def __init__(
        self,
        archive_path: Path,
        image_ids: Sequence[str],
        *,
        decode_delay_seconds: float,
        cache_byte_budget: int,
        memory: _MemoryProbe,
    ) -> None:
        self.probe = _SourceProbe()
        self.source = _ProbeZipImageSource(
            archive_path,
            probe=self.probe,
            decode_delay_seconds=decode_delay_seconds,
        )
        self.image_ids = tuple(image_ids)
        self.model = ViewerPageListModel()
        self.runtime = ViewerPageListRuntime(
            self.source,
            self.image_ids,
            1,
            cache_byte_budget=cache_byte_budget,
            collect_metrics=True,
        )
        self.cache_byte_budget = int(cache_byte_budget)
        self.runtime.thumbnailReady.connect(self._on_thumbnail_ready)
        self.memory = memory
        self.row_build_ms = 0.0
        self.thumbnail_callbacks = 0
        self.qpixmap_uploads = 0
        self._icon_pages: set[int] = set()
        self._icon_bytes_by_page: dict[int, int] = {}
        self.last_spec: ViewerPageThumbnailSpec | None = None

    def build_visible_model(self, requested_rows: Sequence[int]) -> None:
        started = time.perf_counter()
        self.model.set_book(1, self.image_ids)
        # Exercise the same lazy data access a view performs for visible rows.
        for row in requested_rows:
            self.model.data(self.model.index(int(row), 0))
        self.row_build_ms += (time.perf_counter() - started) * 1000
        self.runtime.set_visible(True)
        self.memory.observe()

    def request(
        self,
        pages: Sequence[int],
        spec: ViewerPageThumbnailSpec,
    ) -> None:
        desired = tuple(int(page) for page in pages)
        self.last_spec = spec
        self.model.retain_thumbnails(desired)
        retained = set(desired)
        self._icon_pages.intersection_update(retained)
        self._icon_bytes_by_page = {
            page: byte_count
            for page, byte_count in self._icon_bytes_by_page.items()
            if page in retained
        }
        if not self.runtime.request_visible_pages(desired, spec):
            raise RuntimeError("production PageList runtime rejected visible work")

    def wait_for_idle(
        self,
        application: QApplication,
        *,
        timeout_seconds: float,
    ) -> None:
        _pump_until(
            application,
            lambda: not self.runtime.has_unfinished_tasks(),
            timeout_seconds=timeout_seconds,
            observer=self.memory.observe,
        )
        application.processEvents()
        self.memory.observe()

    def report(
        self,
        *,
        elapsed_ms: float,
        final_pages: Sequence[int],
        memory_report: dict[str, float | None],
    ) -> dict[str, object]:
        final_ids = {self.image_ids[page] for page in final_pages}
        metrics = self.runtime.metrics
        retained_pages = sorted(set(self.runtime.cached_pages) | self._icon_pages)
        return {
            "implementation": "B: production virtual model + page-list runtime",
            "elapsed_ms": round(elapsed_ms, 3),
            "gui": {
                "row_build_ms": round(self.row_build_ms, 3),
                "gui_scale_and_pixmap_ms": 0.0,
                "row_objects": 0,
                "model_rows": self.model.rowCount(),
                "thumbnail_callbacks": self.thumbnail_callbacks,
                "qimage_conversions_from_full_pillow": 0,
                "qpixmap_from_image_calls": self.qpixmap_uploads,
                "paint_events_observed": 0,
            },
            "work": {
                "jobs_submitted": metrics.jobs_submitted,
                "logical_thumbnail_work_items": metrics.jobs_submitted,
                "queued_callbacks": metrics.queued_callbacks,
                "cancel_requests": metrics.cancel_requests,
                "stale_results": metrics.stale_results,
                "terminal_errors": metrics.terminal_errors,
                "runtime_qimage_creations": metrics.qimage_creations,
                "runtime_qpixmap_uploads": metrics.qpixmap_uploads,
                "cache_hits": metrics.cache_hits,
                "cache_misses": metrics.cache_misses,
                **self.probe.decode_counts_outside(final_ids),
            },
            "cache": {
                "desired_pages": list(self.runtime.desired_pages),
                "retained_pages": retained_pages,
                "retained_page_count": len(retained_pages),
                "qimage_cache_pages": list(self.runtime.cached_pages),
                "qimage_cache_page_count": len(self.runtime.cached_pages),
                "retained_icon_pages": sorted(self._icon_pages),
                "estimated_retained_thumbnail_bytes": sum(
                    self._icon_bytes_by_page.values()
                ),
                "qimage_cache_bytes": self.runtime.cache_bytes,
                "estimated_total_retained_artifact_bytes": (
                    self.runtime.cache_bytes
                    + sum(self._icon_bytes_by_page.values())
                ),
                "byte_budget": self.cache_byte_budget,
                "evictions": metrics.cache_evictions,
            },
            "source": self.probe.snapshot(),
            "memory": memory_report,
            "contracts": {
                "hidden_work_deferred": (
                    self.model.rowCount() == 0
                    and metrics.jobs_submitted == 0
                    and self.probe.snapshot().get("source_forks", 0) == 0
                ),
                "active_job_preemptible": True,
                "per_page_row_objects": False,
                "workers_drained": not self.runtime.has_unfinished_tasks(),
            },
        }

    def _on_thumbnail_ready(self, result: object) -> None:
        self.thumbnail_callbacks += 1
        if (
            not isinstance(result, ViewerPageThumbnail)
            or result.runtime_id != self.runtime.runtime_id
            or result.source_epoch != self.runtime.source_epoch
            or result.source_epoch != self.model.book_epoch
            or result.spec != self.last_spec
            or result.page_index not in self.runtime.desired_pages
            or result.qimage is None
            or result.qimage.isNull()
        ):
            return
        if self.model.image_id_for_page(result.page_index) != result.image_id:
            return
        pixmap = QPixmap.fromImage(result.qimage)
        if pixmap.isNull():
            return
        pixmap.setDevicePixelRatio(result.spec.device_pixel_ratio)
        if self.model.set_thumbnail(result.page_index, QIcon(pixmap)):
            self.qpixmap_uploads += 1
            self.runtime.record_pixmap_upload()
            self._icon_pages.add(result.page_index)
            self._icon_bytes_by_page[result.page_index] = _qimage_bytes(
                result.qimage
            )
        self.memory.observe()

    def close(self, application: QApplication) -> bool:
        self.runtime.set_visible(False)
        application.processEvents()
        drained = self.runtime.shutdown(wait_msecs=10_000)
        application.processEvents()
        self.source.close()
        self.model.clear()
        self.runtime.deleteLater()
        self.model.deleteLater()
        return bool(drained and not self.runtime.has_unfinished_tasks())


def _pump_until(
    application: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float,
    observer: Callable[[], None] | None = None,
) -> None:
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    while time.monotonic() < deadline:
        application.processEvents()
        if observer is not None:
            observer()
        if predicate():
            return
        time.sleep(0.001)
    raise TimeoutError("offscreen Viewer PageList benchmark timed out")


def _working_set_bytes() -> int | None:
    if os.name != "nt":
        return None

    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    current_process = ctypes.windll.kernel32.GetCurrentProcess
    memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
    current_process.restype = ctypes.c_void_p
    memory_info.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessMemoryCounters),
        ctypes.c_ulong,
    )
    memory_info.restype = ctypes.c_int
    if not memory_info(current_process(), ctypes.byref(counters), counters.cb):
        return None
    return int(counters.WorkingSetSize)


def _mib(value: int | float | None) -> float | None:
    return None if value is None else round(float(value) / _MIB, 3)


def _qimage_bytes(image: QImage) -> int:
    try:
        return max(0, int(image.sizeInBytes()))
    except (AttributeError, RuntimeError):
        return max(0, int(image.bytesPerLine()) * int(image.height()))


def _make_archive(path: Path, size: tuple[int, int]) -> int:
    output = io.BytesIO()
    with Image.new("RGB", size, (74, 108, 142)) as image:
        image.save(output, "JPEG", quality=90, subsampling=2)
    payload = output.getvalue()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        info = zipfile.ZipInfo(
            _ARCHIVE_ENTRY,
            date_time=(2024, 1, 1, 0, 0, 0),
        )
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, payload)
    return len(payload)


def _page_ids(page_count: int) -> tuple[str, ...]:
    return tuple(f"virtual-book/page-{index:06d}.jpg" for index in range(page_count))


def _window(start: int, count: int, total: int) -> tuple[int, ...]:
    first = max(0, min(int(start), max(0, int(total) - int(count))))
    return tuple(range(first, min(int(total), first + int(count))))


def _scroll_windows(
    page_count: int,
    visible_count: int,
    step_count: int,
) -> tuple[tuple[int, ...], ...]:
    maximum = max(0, page_count - visible_count)
    if step_count <= 1:
        starts = (maximum,)
    else:
        starts = tuple(round(maximum * index / (step_count - 1)) for index in range(step_count))
    return tuple(_window(start, visible_count, page_count) for start in starts)


def _run_a(
    application: QApplication,
    archive_path: Path,
    image_ids: Sequence[str],
    spec: ViewerPageThumbnailSpec,
    *,
    scenario: str,
    windows: Sequence[Sequence[int]],
    decode_delay_seconds: float,
) -> dict[str, object]:
    memory = _MemoryProbe()
    started = time.perf_counter()
    runner = _LegacyPageListA(
        archive_path,
        image_ids,
        decode_delay_seconds=decode_delay_seconds,
        memory=memory,
    )
    final_pages: tuple[int, ...] = ()
    try:
        if scenario != "hidden_large_book":
            runner.build_visible_rows()
        if scenario == "viewer_current_pause_resume":
            # A has no independent replaceable worker to preempt.  A request
            # held before its callback does no work, then blocks during resume.
            application.processEvents()
        for pages in windows:
            final_pages = tuple(pages)
            runner.render_pages(final_pages, spec)
            application.processEvents()
        elapsed_ms = (time.perf_counter() - started) * 1000
        memory_report = memory.report()
        report = runner.report(
            elapsed_ms=elapsed_ms,
            final_pages=final_pages,
            memory_report=memory_report,
        )
    finally:
        runner.close()
        application.processEvents()
    report["source"] = runner.probe.snapshot()
    report["cleanup"] = {"workers_drained": True, "source_closed": True}
    return report


def _run_b(
    application: QApplication,
    archive_path: Path,
    image_ids: Sequence[str],
    spec: ViewerPageThumbnailSpec,
    *,
    scenario: str,
    windows: Sequence[Sequence[int]],
    decode_delay_seconds: float,
    cache_byte_budget: int,
    timeout_seconds: float,
) -> dict[str, object]:
    memory = _MemoryProbe()
    started = time.perf_counter()
    runner = _ProductionPageListB(
        archive_path,
        image_ids,
        decode_delay_seconds=decode_delay_seconds,
        cache_byte_budget=cache_byte_budget,
        memory=memory,
    )
    final_pages: tuple[int, ...] = ()
    pause_observation: dict[str, object] | None = None
    try:
        if scenario != "hidden_large_book":
            first_rows = tuple(windows[0]) if windows else ()
            runner.build_visible_model(first_rows)

        if scenario == "rapid_scroll_work_order_replacement":
            first = tuple(windows[0])
            runner.request(first, spec)
            _pump_until(
                application,
                lambda: int(runner.probe.snapshot().get("decode_requests", 0)) > 0,
                timeout_seconds=timeout_seconds,
                observer=memory.observe,
            )
            for pages in windows[1:]:
                final_pages = tuple(pages)
                runner.request(final_pages, spec)
            runner.wait_for_idle(application, timeout_seconds=timeout_seconds)
        elif scenario == "viewer_current_pause_resume":
            final_pages = tuple(windows[-1])
            runner.request(final_pages, spec)
            _pump_until(
                application,
                lambda: int(runner.probe.snapshot().get("decode_requests", 0)) > 0,
                timeout_seconds=timeout_seconds,
                observer=memory.observe,
            )
            runner.runtime.set_paused(True)
            runner.wait_for_idle(application, timeout_seconds=timeout_seconds)
            jobs_while_paused = runner.runtime.metrics.jobs_submitted
            reads_while_paused = int(
                runner.probe.snapshot().get("zip_entry_reads", 0)
            )
            deadline = time.monotonic() + 0.01
            while time.monotonic() < deadline:
                application.processEvents()
                memory.observe()
                time.sleep(0.001)
            pause_observation = {
                "jobs_before_and_after_hold": [
                    jobs_while_paused,
                    runner.runtime.metrics.jobs_submitted,
                ],
                "entry_reads_before_and_after_hold": [
                    reads_while_paused,
                    int(runner.probe.snapshot().get("zip_entry_reads", 0)),
                ],
            }
            runner.runtime.set_paused(False)
            runner.wait_for_idle(application, timeout_seconds=timeout_seconds)
        else:
            for pages in windows:
                final_pages = tuple(pages)
                runner.request(final_pages, spec)
                runner.wait_for_idle(application, timeout_seconds=timeout_seconds)

        elapsed_ms = (time.perf_counter() - started) * 1000
        memory_report = memory.report()
        report = runner.report(
            elapsed_ms=elapsed_ms,
            final_pages=final_pages,
            memory_report=memory_report,
        )
        if pause_observation is not None:
            report["pause_observation"] = pause_observation
    finally:
        drained = runner.close(application)
    report["source"] = runner.probe.snapshot()
    report["cleanup"] = {
        "workers_drained": bool(drained),
        "source_closed": True,
    }
    if not drained:
        raise RuntimeError("production PageList worker did not drain")
    return report


def _run_scenarios(
    application: QApplication,
    archive_path: Path,
    image_ids: Sequence[str],
    spec: ViewerPageThumbnailSpec,
    *,
    visible_count: int,
    scroll_steps: int,
    decode_delay_seconds: float,
    cache_byte_budget: int,
    timeout_seconds: float,
) -> dict[str, object]:
    page_count = len(image_ids)
    initial = _window(0, visible_count, page_count)
    rapid = _scroll_windows(page_count, visible_count, scroll_steps)
    memory_windows = _scroll_windows(page_count, visible_count, max(3, scroll_steps))
    definitions = (
        ("hidden_large_book", ()),
        ("visible_initial", (initial,)),
        ("rapid_scroll_work_order_replacement", rapid),
        ("viewer_current_pause_resume", (initial,)),
        ("byte_budget_memory", memory_windows),
    )
    scenarios: dict[str, object] = {}
    for name, windows in definitions:
        a = _run_a(
            application,
            archive_path,
            image_ids,
            spec,
            scenario=name,
            windows=windows,
            decode_delay_seconds=decode_delay_seconds,
        )
        b = _run_b(
            application,
            archive_path,
            image_ids,
            spec,
            scenario=name,
            windows=windows,
            decode_delay_seconds=decode_delay_seconds,
            cache_byte_budget=cache_byte_budget,
            timeout_seconds=timeout_seconds,
        )
        scenarios[name] = {
            "A": a,
            "B": b,
            "comparison": {
                "elapsed_A_over_B": (
                    round(float(a["elapsed_ms"]) / float(b["elapsed_ms"]), 3)
                    if float(b["elapsed_ms"]) > 0
                    else None
                ),
                "row_objects_removed": int(a["gui"]["row_objects"])
                - int(b["gui"]["row_objects"]),
                "qpixmap_uploads_avoided": int(
                    a["gui"]["qpixmap_from_image_calls"]
                )
                - int(b["gui"]["qpixmap_from_image_calls"]),
                "transient_decode_successes_avoided": int(
                    a["work"]["transient_decode_successes"]
                )
                - int(b["work"]["transient_decode_successes"]),
                "retained_pages_delta_B_minus_A": int(
                    b["cache"]["retained_page_count"]
                )
                - int(a["cache"]["retained_page_count"]),
            },
        }
    return scenarios


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Offscreen PageList-only A/B: eager QListWidget/GUI scaling versus "
            "the production virtual model and ViewerPageListRuntime."
        )
    )
    parser.add_argument("--pages", type=int, default=2000)
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=2400)
    parser.add_argument("--thumbnail-edge", type=int, default=160)
    parser.add_argument("--visible-pages", type=int, default=6)
    parser.add_argument("--scroll-steps", type=int, default=6)
    parser.add_argument("--decode-delay-ms", type=float, default=8.0)
    parser.add_argument("--cache-kib", type=int, default=200)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--quick",
        action="store_true",
        help=(
            "use a 500-row, 800x1200, four-visible-page smoke profile; "
            "explicit numeric arguments still take precedence only without --quick"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON output path; stdout is always written",
    )
    args = parser.parse_args()
    if args.quick:
        args.pages = 500
        args.width = 800
        args.height = 1200
        args.thumbnail_edge = 128
        args.visible_pages = 4
        args.scroll_steps = 4
        args.decode_delay_ms = 4.0
        args.cache_kib = 96
    if args.pages < 8:
        parser.error("--pages must be at least 8")
    if args.width < 1 or args.height < 1:
        parser.error("image dimensions must be positive")
    if not 1 <= args.visible_pages <= args.pages:
        parser.error("--visible-pages must be between 1 and --pages")
    if args.scroll_steps < 2:
        parser.error("--scroll-steps must be at least 2")
    if args.thumbnail_edge < 1 or args.cache_kib < 1:
        parser.error("thumbnail size and cache budget must be positive")
    return args


def main() -> int:
    args = _parse_args()
    application = QApplication.instance() or QApplication([])
    image_ids = _page_ids(args.pages)
    spec = ViewerPageThumbnailSpec.create(
        args.thumbnail_edge,
        1.0,
        0,
        1.0,
        1.0,
        1.0,
    )
    with TemporaryDirectory(prefix="nivis-pagelist-benchmark-") as temp:
        archive_path = Path(temp) / "same-image-pages.zip"
        entry_bytes = _make_archive(
            archive_path,
            (int(args.width), int(args.height)),
        )
        scenarios = _run_scenarios(
            application,
            archive_path,
            image_ids,
            spec,
            visible_count=int(args.visible_pages),
            scroll_steps=int(args.scroll_steps),
            decode_delay_seconds=float(args.decode_delay_ms) / 1000,
            cache_byte_budget=int(args.cache_kib) * 1024,
            timeout_seconds=float(args.timeout_seconds),
        )

    report = {
        "benchmark": "Viewer PageList structural replacement",
        "platform": os.environ.get("QT_QPA_PLATFORM"),
        "profile": {
            "quick": bool(args.quick),
            "virtual_book_pages": int(args.pages),
            "physical_zip_entries": 1,
            "logical_page_dimensions": [int(args.width), int(args.height)],
            "zip_entry_bytes": entry_bytes,
            "thumbnail_physical_edge": spec.physical_edge,
            "visible_pages": int(args.visible_pages),
            "scroll_steps": int(args.scroll_steps),
            "cache_byte_budget": int(args.cache_kib) * 1024,
            "decode_delay_ms_for_deterministic_cancel": float(
                args.decode_delay_ms
            ),
        },
        "scope": {
            "A": (
                "benchmark-only reproduction of the removed eager PageList; "
                "it is not the superseded Viewer frame engine"
            ),
            "B": (
                "production ViewerPageListModel and ViewerPageListRuntime with "
                "a distinct read-only ZipImageSource fork"
            ),
            "safety": (
                "offscreen Qt, one temporary ZIP, direct method calls, no shown "
                "window, no native input, no external GUI"
            ),
        },
        "counter_notes": {
            "zip_bytes": "exact uncompressed entry bytes returned by ZipImageSource",
            "full_byte_copies": (
                "entry_to_bytesio_materializations and BytesIO.getvalue copies are "
                "reported separately; Qt/plugin-internal copies are unobservable"
            ),
            "qimage": (
                "source returns, A's explicit PIL-to-QImage conversions, and B's "
                "accepted runtime QImages are reported separately"
            ),
            "qpixmap": "every explicit QPixmap.fromImage call is counted",
            "callbacks": (
                "A callback count is the benchmark's old-style GUI application; "
                "B reports queued worker callbacks and accepted GUI callbacks"
            ),
            "paint": (
                "no view is shown or rendered, so paint_events_observed is zero; "
                "this benchmark measures artifact publication, not paint latency"
            ),
            "memory": (
                "Windows working-set samples are process-wide and allocator retention "
                "can carry between sequential A/B cases"
            ),
        },
        "scenarios": scenarios,
        "interpretation": (
            "These offscreen numbers verify work ownership, cancellation, virtual "
            "rows, and bounded retention. They do not establish real-device feel."
        ),
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output is not None:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
