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
        assert runtime.cached_pages == (0,)
        assert 0 < runtime.cache_bytes <= 13_000
        assert runtime.metrics.cache_evictions == 2

        runtime.set_visible(False)
        qapp.processEvents()

        assert runtime.desired_pages == ()
        assert runtime.cached_pages == ()
        assert runtime.cache_bytes == 0
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
