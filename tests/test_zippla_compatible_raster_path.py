from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from time import monotonic, sleep

import pytest
from PySide6.QtCore import QThread
from PySide6.QtGui import QColor, QImage

from app.image_work_coordinator import ImageWorkCoordinator
from app.zippla_compatible_raster_path import (
    ZipPlaCompatibleRasterFrame,
    ZipPlaCompatibleRasterPage,
    ZipPlaCompatibleRasterPath,
    ZipPlaCompatibleRasterRequest,
)


@dataclass(frozen=True)
class _FakeStreamedDecode:
    qimage: QImage
    original_size: tuple[int, int]
    bytes_read: int
    read_calls: int


class _FakeStreamedJpegSource:
    def __init__(self, source_path: Path) -> None:
        self.source_path = source_path
        self.calls: list[tuple[str, tuple[int, int]]] = []
        self.cancel_calls: list[str] = []
        self._started: dict[str, Event] = {}
        self._release: dict[str, Event] = {}
        self._blocked: set[str] = set()
        self._ignore_cancel: set[str] = set()
        self._cancelled: set[str] = set()
        self._lock = Lock()

    def block(self, image_id: str, *, ignore_cancel: bool = False) -> None:
        with self._lock:
            self._blocked.add(image_id)
            if ignore_cancel:
                self._ignore_cancel.add(image_id)
            self._started.setdefault(image_id, Event())
            self._release.setdefault(image_id, Event())

    def started(self, image_id: str) -> Event:
        with self._lock:
            return self._started.setdefault(image_id, Event())

    def release(self, image_id: str) -> None:
        with self._lock:
            self._release.setdefault(image_id, Event()).set()

    def open_streamed_jpeg_at_most(
        self,
        image_id: str,
        maximum_size: tuple[int, int],
    ) -> _FakeStreamedDecode:
        with self._lock:
            self.calls.append((image_id, maximum_size))
            started = self._started.setdefault(image_id, Event())
            release = self._release.setdefault(image_id, Event())
            blocked = image_id in self._blocked
        started.set()
        while blocked and not release.wait(0.002):
            with self._lock:
                cancelled = image_id in self._cancelled
                ignores_cancel = image_id in self._ignore_cancel
            if cancelled and not ignores_cancel:
                raise RuntimeError("cancelled")

        width, height = maximum_size
        image = QImage(
            max(1, width),
            max(1, height),
            QImage.Format.Format_RGB32,
        )
        color_seed = sum(ord(character) for character in image_id)
        image.fill(
            QColor(
                32 + color_seed % 192,
                32 + (color_seed * 3) % 192,
                32 + (color_seed * 7) % 192,
            )
        )
        return _FakeStreamedDecode(
            qimage=image,
            original_size=(4000, 6000),
            bytes_read=1000 + color_seed,
            read_calls=3,
        )

    def cancel_image_request(self, image_id: str) -> None:
        with self._lock:
            self.cancel_calls.append(image_id)
            self._cancelled.add(image_id)


def _wait_until(qapp, predicate, timeout: float = 3.0) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        sleep(0.002)
    qapp.processEvents()
    return bool(predicate())


def _request(
    source: _FakeStreamedJpegSource,
    *,
    source_epoch: int,
    request_id: int,
    page_index: int,
    maximum_size: tuple[int, int] = (64, 96),
    dpr: float = 1.0,
    prefetch: tuple[int, ...] = (),
) -> ZipPlaCompatibleRasterRequest:
    return ZipPlaCompatibleRasterRequest(
        source=source,
        source_epoch=source_epoch,
        request_id=request_id,
        page_index=page_index,
        image_id=f"{page_index:03d}.jpg",
        maximum_size=maximum_size,
        device_pixel_ratio=dpr,
        prefetch=tuple(
            ZipPlaCompatibleRasterPage(index, f"{index:03d}.jpg")
            for index in prefetch
        ),
    )


def test_current_then_prefetch_and_pixmap_ring_cache(
    tmp_path: Path,
    qapp,
) -> None:
    source = _FakeStreamedJpegSource(tmp_path / "book.zip")
    path = ZipPlaCompatibleRasterPath()
    frames: list[ZipPlaCompatibleRasterFrame] = []
    frame_threads: list[QThread] = []
    path.frameReady.connect(
        lambda frame: (
            frames.append(frame),
            frame_threads.append(QThread.currentThread()),
        )
    )

    assert path.request(
        _request(
            source,
            source_epoch=7,
            request_id=101,
            page_index=5,
            dpr=2.0,
            prefetch=(6, 4),
        )
    )
    assert _wait_until(qapp, lambda: bool(frames))
    assert [image_id for image_id, _size in source.calls] == ["005.jpg"]
    assert path.release_prefetch(
        request_id=101,
        page_index=5,
        image_id="005.jpg",
    )
    assert _wait_until(
        qapp,
        lambda: set(path.artifact_page_indexes) == {4, 5, 6},
    )

    assert [image_id for image_id, _size in source.calls] == [
        "005.jpg",
        "006.jpg",
        "004.jpg",
    ]
    assert len(frames) == 1
    assert frames[0].page_index == 5
    assert not frames[0].cache_hit
    assert frame_threads == [qapp.thread()]
    assert frames[0].pixmap.devicePixelRatio() == pytest.approx(2.0)
    first_metrics = path.metrics
    assert first_metrics.jobs_submitted == 3
    assert first_metrics.queued_callbacks == 3
    assert first_metrics.qpixmap_creations == 3
    assert first_metrics.cache_hits == 0
    assert first_metrics.cache_misses == 1
    assert first_metrics.read_calls == 9
    assert first_metrics.bytes_read > 0

    emitted_before_hit = len(frames)
    assert path.request(
        _request(
            source,
            source_epoch=7,
            request_id=102,
            page_index=6,
            dpr=2.0,
            prefetch=(7, 5),
        )
    )
    # A hit publishes synchronously and does not cross a worker callback.
    assert len(frames) == emitted_before_hit + 1
    assert frames[-1].page_index == 6
    assert frames[-1].cache_hit
    assert path.release_prefetch(
        request_id=102,
        page_index=6,
        image_id="006.jpg",
    )
    assert _wait_until(qapp, lambda: 7 in path.artifact_page_indexes)
    assert set(path.artifact_page_indexes) == {5, 6, 7}
    assert [image_id for image_id, _size in source.calls][-1] == "007.jpg"
    final_metrics = path.metrics
    assert final_metrics.cache_hits == 1
    assert final_metrics.cache_misses == 1
    assert final_metrics.jobs_submitted == 4
    assert final_metrics.queued_callbacks == 4
    assert final_metrics.qpixmap_creations == 4

    assert path.shutdown(wait_msecs=1000)
    qapp.processEvents()


def test_direction_reversal_cancels_noncurrent_active_with_no_queue(
    tmp_path: Path,
    qapp,
) -> None:
    source = _FakeStreamedJpegSource(tmp_path / "book.zip")
    source.block("011.jpg")
    path = ZipPlaCompatibleRasterPath()
    frames: list[ZipPlaCompatibleRasterFrame] = []
    path.frameReady.connect(frames.append)

    assert path.request(
        _request(
            source,
            source_epoch=3,
            request_id=20,
            page_index=10,
            prefetch=(11, 9),
        )
    )
    assert _wait_until(
        qapp,
        lambda: bool(frames) and frames[-1].page_index == 10,
    )
    assert path.release_prefetch(
        request_id=20,
        page_index=10,
        image_id="010.jpg",
    )
    assert _wait_until(
        qapp,
        lambda: (
            bool(frames)
            and frames[-1].page_index == 10
            and source.started("011.jpg").is_set()
        ),
    )

    assert path.request(
        _request(
            source,
            source_epoch=3,
            request_id=21,
            page_index=9,
        )
    )
    assert path.queued_job_count == 0
    assert _wait_until(
        qapp,
        lambda: bool(frames) and frames[-1].page_index == 9,
    )

    assert "011.jpg" in source.cancel_calls
    assert 11 not in path.artifact_page_indexes
    assert [image_id for image_id, _size in source.calls] == [
        "010.jpg",
        "011.jpg",
        "009.jpg",
    ]
    assert path.metrics.cancel_requests == 1
    assert path.queued_job_count == 0
    assert path.shutdown(wait_msecs=1000)
    qapp.processEvents()


@pytest.mark.parametrize("changed_dimension", ["source", "layout", "request"])
def test_stale_source_layout_and_request_results_are_rejected_before_qpixmap(
    changed_dimension: str,
    tmp_path: Path,
    qapp,
) -> None:
    first_source = _FakeStreamedJpegSource(tmp_path / "first.zip")
    second_source = (
        _FakeStreamedJpegSource(tmp_path / "second.zip")
        if changed_dimension == "source"
        else first_source
    )
    path = ZipPlaCompatibleRasterPath()
    frames: list[ZipPlaCompatibleRasterFrame] = []
    path.frameReady.connect(frames.append)

    first = _request(
        first_source,
        source_epoch=1,
        request_id=1,
        page_index=1,
        maximum_size=(48, 72),
    )
    assert path.request(first)
    # Let the worker emit, but deliberately do not process the queued GUI
    # callback until a newer request has replaced one generation dimension.
    assert path.wait_for_done(1000)

    second = _request(
        second_source,
        source_epoch=2 if changed_dimension == "source" else 1,
        request_id=2,
        page_index=1,
        maximum_size=(
            (60, 90)
            if changed_dimension == "layout"
            else (48, 72)
        ),
    )
    assert path.request(second)
    assert _wait_until(
        qapp,
        lambda: bool(frames) and frames[-1].request_id == 2,
    )

    assert [frame.request_id for frame in frames] == [2]
    assert path.metrics.stale_results >= 1
    # The rejected QImage is discarded before it can allocate a QPixmap.
    assert path.metrics.qpixmap_creations == 1
    assert path.metrics.jobs_submitted == 2
    assert path.shutdown(wait_msecs=1000)
    qapp.processEvents()


def test_shutdown_cancels_and_waits_only_owned_shared_lane_job(
    tmp_path: Path,
    qapp,
) -> None:
    source = _FakeStreamedJpegSource(tmp_path / "book.zip")
    source.block("001.jpg")
    coordinator = ImageWorkCoordinator(max_workers=1)
    path = ZipPlaCompatibleRasterPath(
        image_work_coordinator=coordinator,
    )
    frames: list[ZipPlaCompatibleRasterFrame] = []
    path.frameReady.connect(frames.append)

    assert path.request(
        _request(
            source,
            source_epoch=9,
            request_id=30,
            page_index=1,
        )
    )
    assert source.started("001.jpg").wait(1.0)
    assert path.shutdown(wait_msecs=1000)
    assert not path.has_unfinished_tasks()
    assert path.queued_job_count == 0
    assert path.metrics.cancel_requests == 1

    qapp.processEvents()
    assert not frames
    assert path.metrics.qpixmap_creations == 0
    assert coordinator.shutdown(wait_msecs=1000)


def test_cold_current_finishes_before_any_prefetch_job_starts(
    tmp_path: Path,
    qapp,
) -> None:
    source = _FakeStreamedJpegSource(tmp_path / "book.zip")
    source.block("005.jpg")
    path = ZipPlaCompatibleRasterPath()
    frames: list[ZipPlaCompatibleRasterFrame] = []
    path.frameReady.connect(frames.append)

    assert path.request(
        _request(
            source,
            source_epoch=12,
            request_id=40,
            page_index=5,
            prefetch=(6, 4),
        )
    )
    assert source.started("005.jpg").wait(1.0)
    qapp.processEvents()

    assert source.calls == [("005.jpg", (64, 96))]
    assert not source.started("006.jpg").is_set()
    assert not source.started("004.jpg").is_set()
    assert frames == []
    assert path.active_job_count == 1
    assert path.queued_job_count == 0

    source.release("005.jpg")
    assert _wait_until(qapp, lambda: bool(frames))
    assert source.calls == [("005.jpg", (64, 96))]
    assert path.release_prefetch(
        request_id=40,
        page_index=5,
        image_id="005.jpg",
    )
    assert _wait_until(
        qapp,
        lambda: set(path.artifact_page_indexes) == {4, 5, 6},
    )

    assert [image_id for image_id, _size in source.calls] == [
        "005.jpg",
        "006.jpg",
        "004.jpg",
    ]
    assert [frame.page_index for frame in frames] == [5]
    assert path.metrics.jobs_submitted == 3
    assert path.shutdown(wait_msecs=1000)
    qapp.processEvents()


def test_direction_reversal_rejects_uncancellable_old_prefetch_before_qpixmap(
    tmp_path: Path,
    qapp,
) -> None:
    source = _FakeStreamedJpegSource(tmp_path / "book.zip")
    source.block("011.jpg", ignore_cancel=True)
    path = ZipPlaCompatibleRasterPath()
    frames: list[ZipPlaCompatibleRasterFrame] = []
    path.frameReady.connect(frames.append)

    assert path.request(
        _request(
            source,
            source_epoch=13,
            request_id=50,
            page_index=10,
            prefetch=(11, 9),
        )
    )
    assert _wait_until(
        qapp,
        lambda: bool(frames) and frames[-1].page_index == 10,
    )
    assert path.release_prefetch(
        request_id=50,
        page_index=10,
        image_id="010.jpg",
    )
    assert _wait_until(
        qapp,
        lambda: (
            bool(frames)
            and frames[-1].page_index == 10
            and source.started("011.jpg").is_set()
        ),
    )

    assert path.request(
        _request(
            source,
            source_epoch=13,
            request_id=51,
            page_index=9,
        )
    )
    assert "011.jpg" in source.cancel_calls
    assert path.active_job_count == 1
    assert path.queued_job_count == 0

    # The backend deliberately ignores cancellation.  Its obsolete decode may
    # finish, but the request/direction generation must reject it before the
    # GUI thread creates a QPixmap or publishes a frame.
    source.release("011.jpg")
    assert _wait_until(
        qapp,
        lambda: bool(frames) and frames[-1].page_index == 9,
    )

    assert [frame.page_index for frame in frames] == [10, 9]
    assert [image_id for image_id, _size in source.calls] == [
        "010.jpg",
        "011.jpg",
        "009.jpg",
    ]
    assert path.metrics.stale_results >= 1
    assert path.metrics.cancel_requests == 1
    assert path.metrics.qpixmap_creations == 2
    assert 11 not in path.artifact_page_indexes
    assert path.shutdown(wait_msecs=1000)
    qapp.processEvents()
