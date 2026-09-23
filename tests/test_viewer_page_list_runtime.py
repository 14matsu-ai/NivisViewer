from __future__ import annotations

from pathlib import Path
from threading import Event, Lock
from time import monotonic

from PIL import Image
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.book_session import BookSession
from app.image_source import ImageSource
from app.image_work_coordinator import ImageWorkCoordinator
from app.viewer_page_list_runtime import (
    ViewerPageListModel,
    ViewerPageListRuntime,
    ViewerPageThumbnailSpec,
)


def _wait_until(
    qapp: QApplication,
    predicate,
    *,
    timeout_ms: int = 3000,
) -> None:
    deadline = monotonic() + timeout_ms / 1000
    while not predicate() and monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(5)
    assert predicate()


def _thumbnail_spec(edge: int = 64) -> ViewerPageThumbnailSpec:
    return ViewerPageThumbnailSpec.create(edge, 1.0, 0, 1.0, 1.0, 1.0)


class _FakeThumbnailSource(ImageSource):
    def __init__(
        self,
        name: str,
        image_ids: tuple[str, ...],
        *,
        started: Event | None = None,
        release: Event | None = None,
        is_clone: bool = False,
    ) -> None:
        super().__init__(Path(name))
        self.image_ids = image_ids
        self.started = started
        self.release = release
        self.is_clone = is_clone
        self.calls: list[str] = []
        self.clones: list[_FakeThumbnailSource] = []
        self.closed = False
        self.active = 0
        self.max_active = 0
        self._guard = Lock()

    def list_images(self) -> list[str]:
        return list(self.image_ids)

    def open_image(self, image_id: str) -> Image.Image:
        self.calls.append(image_id)
        if self.is_clone:
            with self._guard:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                if self.started is not None:
                    self.started.set()
                if self.release is not None:
                    assert self.release.wait(3), "thumbnail job timed out"
            finally:
                with self._guard:
                    self.active -= 1
        return Image.new("RGB", (64, 64), "white")

    def display_path(self, image_id: str) -> str:
        return image_id

    def fork_for_thumbnail(self) -> ImageSource:
        clone = _FakeThumbnailSource(
            f"{self.source_path.name}-thumbnail",
            self.image_ids,
            started=self.started,
            release=self.release,
            is_clone=True,
        )
        self.clones.append(clone)
        return clone

    def close(self) -> None:
        self.closed = True


def test_model_virtualizes_unfiltered_rows_and_maps_filtered_pages() -> None:
    model = ViewerPageListModel()
    image_ids = tuple(f"folder/page-{index:04d}.jpg" for index in range(10_000))

    model.set_book(7, image_ids)

    assert model.rowCount() == 10_000
    assert model.page_index_at(7_654) == 7_654
    assert model.row_for_page(7_654) == 7_654
    assert model.data(model.index(7_654, 0)) == "7655: page-7654.jpg"
    # The large, unfiltered path maps row == page without allocating row
    # objects or a second full page-index/reverse-index pair.
    assert model._filtered_pages is None
    assert model._row_by_page is None

    model.set_filter("page-073")

    assert model.rowCount() == 10
    assert model.page_index_at(1) == 731
    assert model.row_for_page(731) == 1
    assert model.row_for_page(900) == -1
    assert (
        model.data(model.index(1, 0), model.PageIndexRole)
        == 731
    )
    assert (
        model.data(model.index(1, 0), model.ImageIdRole)
        == "folder/page-0731.jpg"
    )


def test_runtime_replaces_visible_order_and_pauses_one_active_lane(
    qapp: QApplication,
) -> None:
    started = Event()
    release = Event()
    image_ids = tuple(f"page-{index}.jpg" for index in range(5))
    source = _FakeThumbnailSource(
        "book",
        image_ids,
        started=started,
        release=release,
    )
    runtime = ViewerPageListRuntime(
        source,
        image_ids,
        4,
        collect_metrics=True,
    )
    delivered = []
    runtime.thumbnailReady.connect(delivered.append)
    try:
        runtime.set_visible(True)
        assert runtime.request_visible_pages((0, 1, 2), _thumbnail_spec())
        assert started.wait(1)

        runtime.set_paused(True)
        assert runtime.request_visible_pages((3, 4), _thumbnail_spec())
        release.set()
        assert runtime.wait_for_done(2000)
        _wait_until(qapp, lambda: runtime.metrics.queued_callbacks == 1)

        assert delivered == []
        assert runtime.desired_pages == (3, 4)
        assert runtime.metrics.stale_results == 1

        runtime.set_paused(False)
        _wait_until(
            qapp,
            lambda: not runtime.has_unfinished_tasks()
            and [item.page_index for item in delivered] == [3, 4],
        )

        clone = source.clones[0]
        assert clone.calls == ["page-0.jpg", "page-3.jpg", "page-4.jpg"]
        assert clone.max_active == 1
        assert runtime.cached_pages == (3, 4)
        assert runtime.metrics.jobs_submitted == 3
        assert runtime.metrics.cancel_requests == 1
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_runtime_bounds_cache_and_hidden_state_closes_owned_clone(
    qapp: QApplication,
) -> None:
    image_ids = ("0.jpg", "1.jpg", "2.jpg")
    source = _FakeThumbnailSource("book", image_ids)
    runtime = ViewerPageListRuntime(
        source,
        image_ids,
        8,
        cache_byte_budget=13_000,
        collect_metrics=True,
    )
    try:
        runtime.set_visible(True)
        assert runtime.request_visible_pages((0, 1, 2), _thumbnail_spec())
        _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())

        assert len(source.clones) == 1
        assert source.clones[0].calls == ["0.jpg", "1.jpg", "2.jpg"]
        assert runtime.cached_pages == (2,)
        assert 0 < runtime.cache_bytes <= 13_000
        assert runtime.metrics.cache_evictions == 2

        runtime.set_visible(False)
        qapp.processEvents()

        assert runtime.desired_pages == ()
        # Hiding releases the source clone but retains the bounded image LRU
        # so reopening the dock can use it without another decode.
        assert runtime.cached_pages == (2,)
        assert 0 < runtime.cache_bytes <= 13_000
        assert source.clones[0].closed
        assert not source.closed
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_book_switch_and_close_keep_source_until_thumbnail_callback_drains(
    qapp: QApplication,
) -> None:
    for transition in ("switch", "close"):
        started = Event()
        release = Event()
        first = _FakeThumbnailSource(
            "first",
            ("page.jpg",),
            started=started,
            release=release,
        )
        second = _FakeThumbnailSource("second", ("page.jpg",))
        sources = {"first": first, "second": second}

        def source_factory(path: Path, **_kwargs: object):
            return sources[path.name], None

        coordinator = ImageWorkCoordinator(max_workers=2)
        session = BookSession(
            source_factory=source_factory,
            image_work_coordinator=coordinator,
        )
        session.open_book("first")
        runtime = session.page_list_runtime
        assert runtime is not None
        delivered = []
        runtime.thumbnailReady.connect(delivered.append)
        runtime.set_visible(True)
        assert runtime.request_visible_pages((0,), _thumbnail_spec())
        assert started.wait(1)

        if transition == "switch":
            session.open_book("second")
            assert session.source is second
        else:
            session.close_book()
            assert session.source is None

        assert not first.closed
        release.set()
        assert coordinator.wait_for_browser(2000)
        # Native/source work is over, but the queued callback still owns the
        # old runtime and therefore keeps its main source alive.
        assert not first.closed
        assert runtime.has_unfinished_tasks()

        _wait_until(qapp, lambda: first.closed)

        assert delivered == []
        assert first.clones[0].closed
        session.shutdown(wait_msecs=3000)
        assert coordinator.shutdown(wait_msecs=3000)


def _wait_for_runtime_window(
    qapp: QApplication,
    runtime: ViewerPageListRuntime,
    *,
    visible_count: int,
    timeout_ms: int = 3000,
) -> None:
    _wait_until(
        qapp,
        lambda: not runtime.has_unfinished_tasks()
        and len(runtime.ready_ahead_pages)
        == max(0, len(runtime.desired_pages) - visible_count),
        timeout_ms=timeout_ms,
    )


def test_runtime_prioritizes_visible_then_reuses_forward_and_reverse_cache(
    qapp: QApplication,
) -> None:
    image_ids = tuple(f"page-{index:03d}.jpg" for index in range(40))
    source = _FakeThumbnailSource("book", image_ids)
    runtime = ViewerPageListRuntime(
        source,
        image_ids,
        21,
        cache_byte_budget=32 * 1024 * 1024,
        collect_metrics=True,
    )
    delivered = []
    runtime.thumbnailReady.connect(delivered.append)
    try:
        runtime.set_visible(True)
        first_window = (3, 4, 5, 6, 7, 8)
        assert runtime.request_visible_pages(
            first_window,
            _thumbnail_spec(),
            visible_count=3,
        )
        _wait_for_runtime_window(qapp, runtime, visible_count=3)
        assert source.clones[0].calls[:3] == [
            "page-003.jpg",
            "page-004.jpg",
            "page-005.jpg",
        ]
        assert runtime.ready_ahead_pages == (6, 7, 8)

        forward_window = (6, 7, 8, 9, 10, 11)
        assert runtime.request_visible_pages(
            forward_window,
            _thumbnail_spec(),
            visible_count=3,
        )
        _wait_for_runtime_window(qapp, runtime, visible_count=3)

        assert runtime.request_visible_pages(
            first_window,
            _thumbnail_spec(),
            visible_count=3,
        )
        _wait_for_runtime_window(qapp, runtime, visible_count=3)

        assert source.clones[0].calls == [
            f"page-{index:03d}.jpg" for index in range(3, 12)
        ]
        assert len(source.clones[0].calls) == len(set(source.clones[0].calls))
        assert runtime.ready_ahead_pages == (6, 7, 8)
        assert runtime.cache_bytes <= 32 * 1024 * 1024
        assert runtime.metrics.cache_hits == 6
        assert runtime.metrics.cache_misses == 9
        assert runtime.metrics.jobs_submitted == 9
        reused = [item for item in delivered if item.cache_hit]
        assert {item.page_index for item in reused} >= {3, 4, 5, 6, 7, 8}
        assert all(
            item.qimage is not None and not item.qimage.isNull()
            for item in reused
        )
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_runtime_cache_never_exceeds_default_32_mib_budget(
    qapp: QApplication,
) -> None:
    image_ids = tuple(f"large-{index}.jpg" for index in range(5))
    source = _FakeThumbnailSource("large-book", image_ids)
    runtime = ViewerPageListRuntime(source, image_ids, 23, collect_metrics=True)
    try:
        runtime.set_visible(True)
        assert runtime.request_visible_pages(
            range(len(image_ids)),
            _thumbnail_spec(1500),
        )
        _wait_until(
            qapp,
            lambda: not runtime.has_unfinished_tasks(),
            timeout_ms=10000,
        )

        assert 0 < runtime.cache_bytes <= 32 * 1024 * 1024
        assert 0 < len(runtime.cached_pages) < len(image_ids)
        assert runtime.metrics.cache_evictions > 0
    finally:
        assert runtime.shutdown(wait_msecs=3000)
        source.close()


def test_long_jump_cancels_active_prefetch_and_replaces_queued_order(
    qapp: QApplication,
) -> None:
    blocked_started = Event()
    release_blocked = Event()
    image_ids = tuple(f"page-{index:03d}.jpg" for index in range(100))

    class BlockingPrefetchSource(_FakeThumbnailSource):
        def open_image(self, image_id: str) -> Image.Image:
            self.calls.append(image_id)
            if self.is_clone:
                with self._guard:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    if image_id == "page-001.jpg":
                        blocked_started.set()
                        assert release_blocked.wait(3)
                finally:
                    with self._guard:
                        self.active -= 1
            return Image.new("RGB", (64, 64), "white")

        def fork_for_thumbnail(self) -> ImageSource:
            clone = BlockingPrefetchSource(
                f"{self.source_path.name}-thumbnail",
                self.image_ids,
                is_clone=True,
            )
            self.clones.append(clone)
            return clone

    source = BlockingPrefetchSource("book", image_ids)
    runtime = ViewerPageListRuntime(
        source,
        image_ids,
        22,
        collect_metrics=True,
    )
    try:
        runtime.set_visible(True)
        assert runtime.request_visible_pages(
            (0, 1, 2, 3),
            _thumbnail_spec(),
            visible_count=1,
        )
        _wait_until(qapp, blocked_started.is_set, timeout_ms=2000)
        assert source.clones[0].calls == ["page-000.jpg", "page-001.jpg"]

        assert runtime.request_visible_pages(
            (50, 51, 52),
            _thumbnail_spec(),
            visible_count=1,
        )
        release_blocked.set()
        _wait_for_runtime_window(qapp, runtime, visible_count=1)

        assert source.clones[0].calls[:3] == [
            "page-000.jpg",
            "page-001.jpg",
            "page-050.jpg",
        ]
        assert "page-002.jpg" not in source.clones[0].calls
        assert "page-003.jpg" not in source.clones[0].calls
        assert runtime.metrics.cancel_requests >= 1
        assert runtime.metrics.stale_results >= 1
    finally:
        release_blocked.set()
        assert runtime.shutdown(wait_msecs=3000)
        source.close()
