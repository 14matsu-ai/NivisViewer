from __future__ import annotations

from io import BytesIO
from pathlib import Path
from threading import Event
import time

from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.archive_backend import ArchiveBackendError, ArchiveEntry, ArchiveErrorCode, ArchiveListing
from app.archive_backend_registry import ArchiveBackendRegistry
from app.config_manager import ConfigManager
from app.image_cache import ImageCache
from app.image_source import SevenZipImageSource
from app.metadata_store import MetadataStore
from app.viewer_window import ViewerWindow


def image_bytes() -> bytes:
    output = BytesIO()
    with Image.new("RGB", (20, 30), "white") as image:
        image.save(output, format="PNG")
    return output.getvalue()


class ViewerBackend:
    def __init__(
        self,
        *,
        started: Event | None = None,
        release: Event | None = None,
        failure: ArchiveErrorCode | None = None,
        pages: int = 2,
    ) -> None:
        self.started = started
        self.release = release
        self.failure = failure
        self.pages = pages
        self.cancelled_seen = Event()
        self.read_calls = 0

    def is_available(self) -> bool:
        return self.failure is not ArchiveErrorCode.BACKEND_NOT_FOUND

    def list_entries(self, archive_path: str, *, cancel_token=None) -> ArchiveListing:
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            while not self.release.wait(0.02):
                if cancel_token is not None and cancel_token.is_set():
                    self.cancelled_seen.set()
                    raise ArchiveBackendError(ArchiveErrorCode.PROCESS_CANCELLED)
        if self.failure is not None:
            raise ArchiveBackendError(self.failure)
        return ArchiveListing(
            archive_path,
            tuple(
                ArchiveEntry(f"{index + 1}.png", 100, 50, False, False)
                for index in range(self.pages)
            ),
            "7z",
            False,
            False,
        )

    def read_entry(self, archive_path, entry_path, *, cancel_token=None, maximum_bytes=None):
        self.read_calls += 1
        return image_bytes()


def make_window(
    tmp_path: Path,
    backend: ViewerBackend,
    *,
    metadata_store: MetadataStore | None = None,
) -> ViewerWindow:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return ViewerWindow(
        config_manager=config,
        metadata_store=metadata_store,
        archive_backend_registry=ArchiveBackendRegistry(
            config_manager=config,
            seven_zip_backend=backend,
        ),
    )


def test_external_open_returns_immediately_and_qt_timers_keep_running(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "slow.7z"
    archive.write_bytes(b"archive")
    started = Event()
    release = Event()
    window = make_window(
        tmp_path,
        ViewerBackend(started=started, release=release),
    )
    ticks: list[bool] = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()

    before = time.monotonic()
    assert window.open_path(archive)
    elapsed = time.monotonic() - before
    assert elapsed < 0.2
    assert started.wait(1)
    for _index in range(10):
        qapp.processEvents()
        time.sleep(0.002)
    assert ticks

    release.set()
    assert window.book_session.wait_for_async(2000)
    qapp.processEvents()
    assert window.book_session.current_path == archive
    assert window.model.total_pages == 2
    timer.stop()
    window.close()
    qapp.processEvents()


def test_failed_external_open_preserves_current_book_and_history(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    with Image.new("RGB", (10, 20), "white") as image:
        image.save(folder / "1.jpg")
    archive = tmp_path / "broken.rar"
    archive.write_bytes(b"broken")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    window = make_window(
        tmp_path,
        ViewerBackend(failure=ArchiveErrorCode.CORRUPT_ARCHIVE),
        metadata_store=store,
    )
    assert window.open_path(folder)
    assert window.book_session.wait_for_async(2000)
    qapp.processEvents()
    original_source = window.book_session.source
    original_history = store.list_history()

    assert window.open_path(archive)
    assert window.book_session.wait_for_async(2000)
    qapp.processEvents()

    assert window.book_session.source is original_source
    assert window.book_session.current_path == folder
    assert store.list_history() == original_history
    assert "書庫" in window.status.currentMessage()
    window.close()
    store.close()
    qapp.processEvents()


def test_successful_external_open_records_and_restores_clamped_position(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "book.cbr"
    archive.write_bytes(b"archive")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.record_book_opened(
        str(archive),
        item_type="archive",
        start_page_index=99,
        total_pages=100,
    )
    window = make_window(tmp_path, ViewerBackend(pages=3), metadata_store=store)

    assert window.open_path(archive)
    assert window.book_session.wait_for_async(2000)
    qapp.processEvents()

    assert window.model.current_index == 1
    assert store.list_history()[0].path == str(archive.absolute())
    window.close()
    store.close()
    qapp.processEvents()


def test_closing_viewer_cancels_slow_listing_without_waiting_for_release(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "slow.rar"
    archive.write_bytes(b"archive")
    started = Event()
    release = Event()
    backend = ViewerBackend(started=started, release=release)
    window = make_window(tmp_path, backend)
    assert window.open_path(archive)
    assert started.wait(1)

    before = time.monotonic()
    window.close()
    elapsed = time.monotonic() - before
    qapp.processEvents()

    assert elapsed < 0.5
    assert backend.cancelled_seen.wait(1)


def test_two_viewers_keep_external_sources_independent(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_archive = tmp_path / "first.7z"
    second_archive = tmp_path / "second.cb7"
    first_archive.write_bytes(b"first")
    second_archive.write_bytes(b"second")
    first = make_window(tmp_path, ViewerBackend(pages=1))
    second = make_window(tmp_path, ViewerBackend(pages=3))

    first.open_path(first_archive)
    second.open_path(second_archive)
    assert first.book_session.wait_for_async(2000)
    assert second.book_session.wait_for_async(2000)
    qapp.processEvents()

    assert first.model.total_pages == 1
    assert second.model.total_pages == 3
    assert first.book_session.source is not second.book_session.source
    first.close()
    second.close()
    qapp.processEvents()


def test_rewanted_cancelled_archive_page_retries_without_error_delivery(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "rapid.7z"
    archive.write_bytes(b"archive")

    class RewantedCancelBackend(ViewerBackend):
        def __init__(self) -> None:
            super().__init__(pages=2)
            self.first_read_started = Event()
            self.cancel_observed = Event()
            self.release_cancel_result = Event()

        def read_entry(
            self,
            archive_path,
            entry_path,
            *,
            cancel_token=None,
            maximum_bytes=None,
        ):
            self.read_calls += 1
            if self.read_calls == 1:
                self.first_read_started.set()
                while cancel_token is not None and not cancel_token.wait(0.01):
                    pass
                self.cancel_observed.set()
                assert self.release_cancel_result.wait(3)
                raise ArchiveBackendError(
                    ArchiveErrorCode.PROCESS_CANCELLED
                )
            return image_bytes()

    backend = RewantedCancelBackend()
    source = SevenZipImageSource(archive, backend=backend)
    cache = ImageCache()
    delivered = []
    cache.pageLoaded.connect(delivered.append)
    try:
        cache.set_source(source, source.list_images())
        cache.preload_around(0, radius=0, visible_indexes=(0,))
        assert backend.first_read_started.wait(1)

        cache.preload_around(1, radius=0, visible_indexes=(1,))
        assert backend.cancel_observed.wait(1)
        cache.preload_around(0, radius=0, visible_indexes=(0,))
        assert cache.get(0) is None

        backend.release_cancel_result.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            qapp.processEvents()
            if cache.get(0) is not None and not cache._tasks:
                break
            time.sleep(0.005)

        assert cache.wait_for_done(2000)
        qapp.processEvents()
        assert backend.read_calls == 2
        assert len(delivered) == 1
        assert delivered[0].page_index == 0
        assert delivered[0].error is None
        assert cache.get(0) is not None
        assert cache.get(0).error is None
        assert cache._tasks == {}
        assert cache._in_flight == {}
        assert cache._cancel_requested_tasks == set()
    finally:
        backend.release_cancel_result.set()
        cache.clear()
        cache.wait_for_done(3000)
        qapp.processEvents()
        source.close()
