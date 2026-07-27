from __future__ import annotations

import errno
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from time import monotonic

import pytest
from PIL import Image
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.adjacent_book_search import (
    SIBLING_FOLDERS,
    AdjacentBookBrowserSnapshot,
    AdjacentBookFileSystem,
    AdjacentBookSearchRequest,
    AdjacentBookSearchService,
    AdjacentBookSearchStatus,
    AdjacentBookSnapshotEntry,
)
from app.application_controller import ApplicationController
from app.bookmark_model import BookmarkModel
from app.config_manager import ConfigManager
from app.file_operation_coordinator import FileOperationCoordinator
from app.file_operation_service import (
    FileCollisionPolicy,
    FileOperationItemState,
    FileOperationKind,
    FileOperationRequest,
    FileOperationService,
)
from app.history_model import HistoryModel
from app.metadata_store import BrowserBookmark, HistoryEntry, MetadataStore
from app.path_availability import (
    PathAvailability,
    PathAvailabilityFileSystem,
    PathAvailabilityService,
)
from app.pdf_backend import (
    PdfBackendError,
    PdfDocumentInfo,
    PdfErrorCode,
    PdfPageInfo,
    PdfRenderRequest,
    PdfRenderResult,
)
from app.pdfium_service import PdfiumService, PdfiumServiceState


def _wait_until(qapp: QApplication, predicate, timeout: float = 2.0) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        QTest.qWait(5)
    return bool(predicate())


def _write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (8, 12), "white") as image:
        image.save(path)


class _SlowAdjacentFileSystem(AdjacentBookFileSystem):
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def directory_fingerprint(self, path: str) -> int | None:
        self.started.set()
        assert self.release.wait(2)
        return super().directory_fingerprint(path)


class _CountingAdjacentFileSystem(AdjacentBookFileSystem):
    def __init__(self) -> None:
        self.parent_scans = 0
        self.metadata_requests: list[bool] = []

    def scandir(self, path: str, **kwargs):
        self.parent_scans += 1
        self.metadata_requests.append(bool(kwargs.get("include_metadata", False)))
        return super().scandir(path, **kwargs)


def _controller(
    tmp_path: Path,
    qapp: QApplication,
    *,
    adjacent_service: AdjacentBookSearchService | None = None,
) -> ApplicationController:
    return ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
        adjacent_book_search_service=adjacent_service,
    )


def _close_controller(
    controller: ApplicationController,
    qapp: QApplication,
) -> None:
    for viewer in tuple(controller.viewer_windows):
        viewer.close()
    browser = controller.get_browser_window()
    if browser is not None:
        browser.close()
    qapp.processEvents()
    controller.shutdown()


def test_adjacent_open_returns_immediately_and_qtimer_runs(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "books" / "book1" / "1.jpg"
    second = tmp_path / "books" / "book2" / "1.jpg"
    _write_image(first)
    _write_image(second)
    filesystem = _SlowAdjacentFileSystem()
    service = AdjacentBookSearchService(filesystem=filesystem)
    controller = _controller(tmp_path, qapp, adjacent_service=service)
    viewer = controller.open_path(first)
    ticks: list[bool] = []
    QTimer.singleShot(0, lambda: ticks.append(True))

    started = monotonic()
    assert controller.open_adjacent_book(viewer, 1) == "searching"
    assert monotonic() - started < 0.1
    assert filesystem.started.wait(1)
    qapp.processEvents()
    assert ticks == [True]
    assert viewer.status.currentMessage() == "次の本を検索中…"

    filesystem.release.set()
    assert _wait_until(
        qapp,
        lambda: viewer.book_session.current_path == second.parent,
    )
    _close_controller(controller, qapp)


def test_adjacent_open_calls_no_gui_thread_filesystem_queries(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    first = tmp_path / "books" / "book1" / "1.jpg"
    second = tmp_path / "books" / "book2" / "1.jpg"
    _write_image(first)
    _write_image(second)
    filesystem = _SlowAdjacentFileSystem()
    service = AdjacentBookSearchService(filesystem=filesystem)
    controller = _controller(tmp_path, qapp, adjacent_service=service)
    viewer = controller.open_path(first)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("synchronous filesystem I/O on GUI thread")

    with monkeypatch.context() as patch:
        for name in ("exists", "is_dir", "is_file", "resolve", "iterdir", "stat"):
            patch.setattr(Path, name, forbidden)
        patch.setattr(os, "scandir", forbidden)
        patch.setattr(os, "stat", forbidden)
        assert controller.open_adjacent_book(viewer, 1) == "searching"
        assert filesystem.started.wait(1)
    filesystem.release.set()
    assert _wait_until(
        qapp,
        lambda: viewer.book_session.current_path == second.parent,
    )
    _close_controller(controller, qapp)


def test_adjacent_snapshot_avoids_parent_rescan(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "books"
    parent.mkdir()
    first = parent / "book1.zip"
    second = parent / "book2.cbz"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    filesystem = _CountingAdjacentFileSystem()
    service = AdjacentBookSearchService(filesystem=filesystem)
    results = []
    service.result_ready.connect(results.append)
    snapshot = AdjacentBookBrowserSnapshot(
        str(parent),
        12,
        (
            AdjacentBookSnapshotEntry(str(first), "archive", ".zip", "book1"),
            AdjacentBookSnapshotEntry(str(second), "archive", ".cbz", "book2"),
        ),
    )
    request = AdjacentBookSearchRequest(1, str(first), 1, False, snapshot, 1)

    assert service.search(request)
    assert _wait_until(qapp, lambda: bool(results))
    assert results[0].candidate_path == str(second)
    assert filesystem.parent_scans == 0
    service.close()


@pytest.mark.parametrize(
    "name",
    ("book2.rar", "book2.cbr", "book2.7z", "book2.cb7", "book2.pdf"),
)
def test_adjacent_search_supports_book_file_kinds(
    tmp_path: Path,
    qapp: QApplication,
    name: str,
) -> None:
    parent = tmp_path / "books"
    parent.mkdir()
    first = parent / "book1.zip"
    candidate = parent / name
    first.write_bytes(b"1")
    candidate.write_bytes(b"2")
    service = AdjacentBookSearchService()
    results = []
    service.result_ready.connect(results.append)
    assert service.search(
        AdjacentBookSearchRequest(1, str(first), 1, False, None, 1)
    )
    assert _wait_until(qapp, lambda: bool(results))
    assert results[0].status is AdjacentBookSearchStatus.FOUND
    assert results[0].candidate_path == str(candidate)
    service.close()


def test_adjacent_search_natural_order_excludes_later_rar_parts(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "books"
    parent.mkdir()
    for name in (
        "book1.zip",
        "book2.rar",
        "book10.cbz",
        "series.part2.rar",
        "series.r00",
    ):
        (parent / name).write_bytes(b"x")
    service = AdjacentBookSearchService()
    results = []
    service.result_ready.connect(results.append)
    service.search(
        AdjacentBookSearchRequest(
            1,
            str(parent / "book1.zip"),
            1,
            False,
            None,
            1,
        )
    )
    assert _wait_until(qapp, lambda: bool(results))
    assert results[0].candidate_path == str(parent / "book2.rar")
    service.close()


def test_sibling_folder_search_uses_browser_sort_and_excludes_files(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "folders"
    parent.mkdir()
    first = parent / "book1"
    current = parent / "book2"
    last = parent / "book10"
    for folder in (first, current, last):
        folder.mkdir()
    (parent / "book3.jpg").write_bytes(b"not a folder")
    filesystem = _CountingAdjacentFileSystem()
    service = AdjacentBookSearchService(filesystem=filesystem)
    results = []
    service.result_ready.connect(results.append)

    assert service.search(
        AdjacentBookSearchRequest(
            request_id=1,
            current_book_path=str(current),
            direction=1,
            loop=False,
            browser_snapshot=None,
            generation=1,
            candidate_mode=SIBLING_FOLDERS,
            sort_key="name",
            sort_order="ascending",
        )
    )
    assert _wait_until(qapp, lambda: len(results) == 1)
    assert results[0].status is AdjacentBookSearchStatus.FOUND
    assert results[0].candidate_path == str(last)

    assert service.search(
        AdjacentBookSearchRequest(
            request_id=2,
            current_book_path=str(current),
            direction=1,
            loop=False,
            browser_snapshot=None,
            generation=2,
            candidate_mode=SIBLING_FOLDERS,
            sort_key="name",
            sort_order="descending",
        )
    )
    assert _wait_until(qapp, lambda: len(results) == 2)
    assert results[1].status is AdjacentBookSearchStatus.FOUND
    assert results[1].candidate_path == str(first)
    assert filesystem.metadata_requests == [False, False]
    service.close()


def test_sibling_folder_search_never_wraps_at_boundary(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "folders"
    parent.mkdir()
    first = parent / "01"
    second = parent / "02"
    first.mkdir()
    second.mkdir()
    service = AdjacentBookSearchService()
    results = []
    service.result_ready.connect(results.append)

    assert service.search(
        AdjacentBookSearchRequest(
            request_id=1,
            current_book_path=str(first),
            direction=-1,
            loop=False,
            browser_snapshot=None,
            generation=1,
            candidate_mode=SIBLING_FOLDERS,
        )
    )
    assert _wait_until(qapp, lambda: bool(results))
    assert results[0].status is AdjacentBookSearchStatus.BOUNDARY
    assert results[0].candidate_path is None
    service.close()


def test_adjacent_cache_is_invalidatable(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "books"
    parent.mkdir()
    first = parent / "book1.zip"
    second = parent / "book2.zip"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    filesystem = _CountingAdjacentFileSystem()
    service = AdjacentBookSearchService(filesystem=filesystem)
    results = []
    service.result_ready.connect(results.append)
    for request_id in (1, 2):
        service.search(
            AdjacentBookSearchRequest(
                request_id,
                str(first),
                1,
                False,
                None,
                request_id,
            )
        )
        assert _wait_until(qapp, lambda: len(results) == request_id)
    assert filesystem.parent_scans == 1
    service.invalidate(parent)
    service.search(
        AdjacentBookSearchRequest(3, str(first), 1, False, None, 3)
    )
    assert _wait_until(qapp, lambda: len(results) == 3)
    assert filesystem.parent_scans == 2
    service.close()


class _SequencedAdjacentFileSystem(AdjacentBookFileSystem):
    def __init__(self) -> None:
        self._lock = Lock()
        self._calls = 0
        self.first_started = Event()
        self.release_first = Event()

    def directory_fingerprint(self, path: str) -> int | None:
        with self._lock:
            self._calls += 1
            call = self._calls
        if call == 1:
            self.first_started.set()
            assert self.release_first.wait(2)
        return super().directory_fingerprint(path)


def test_latest_adjacent_request_wins(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    paths = [
        tmp_path / "books" / name / "1.jpg"
        for name in ("book1", "book2", "book3")
    ]
    for path in paths:
        _write_image(path)
    filesystem = _SequencedAdjacentFileSystem()
    service = AdjacentBookSearchService(filesystem=filesystem, max_workers=2)
    controller = _controller(tmp_path, qapp, adjacent_service=service)
    viewer = controller.open_path(paths[1])
    assert controller.open_adjacent_book(viewer, 1) == "searching"
    assert filesystem.first_started.wait(1)
    assert controller.open_adjacent_book(viewer, -1) == "searching"
    assert _wait_until(
        qapp,
        lambda: viewer.book_session.current_path == paths[0].parent,
    )
    filesystem.release_first.set()
    QTest.qWait(50)
    qapp.processEvents()
    assert viewer.book_session.current_path == paths[0].parent
    _close_controller(controller, qapp)


def test_latest_sibling_folder_request_wins(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    paths = [
        tmp_path / "folders" / name / "1.jpg"
        for name in ("01", "02", "03")
    ]
    for path in paths:
        _write_image(path)
    filesystem = _SequencedAdjacentFileSystem()
    service = AdjacentBookSearchService(filesystem=filesystem, max_workers=2)
    controller = _controller(tmp_path, qapp, adjacent_service=service)
    browser = controller.create_browser_window()
    browser.set_current_folder(paths[1].parent)
    assert browser.wait_for_scan()

    assert (
        controller.handle_browser_folder_navigation(browser, 1)
        == "searching"
    )
    assert filesystem.first_started.wait(1)
    assert (
        controller.handle_browser_folder_navigation(browser, -1)
        == "searching"
    )
    assert _wait_until(
        qapp,
        lambda: browser.current_path == paths[0].parent,
    )
    filesystem.release_first.set()
    QTest.qWait(50)
    qapp.processEvents()
    assert browser.current_path == paths[0].parent
    _close_controller(controller, qapp)


class _AvailabilityFileSystem(PathAvailabilityFileSystem):
    def __init__(
        self,
        state: PathAvailability,
        *,
        release: Event | None = None,
    ) -> None:
        self.state = state
        self.release = release
        self.calls = 0

    def probe(self, path: str) -> PathAvailability:
        self.calls += 1
        if self.release is not None:
            assert self.release.wait(2)
        return self.state


def test_metadata_entry_properties_are_pure_data(monkeypatch) -> None:
    monkeypatch.setattr(
        Path,
        "exists",
        lambda *_args: (_ for _ in ()).throw(AssertionError("I/O")),
    )
    history = HistoryEntry("Z:\\missing\\本.zip", "archive", 1.0, 0, 1, 1)
    bookmark = BrowserBookmark(
        "Z:\\missing\\本.zip",
        "本",
        "archive",
        0,
        1.0,
    )
    assert history.exists is None
    assert bookmark.exists is None
    assert history.display_name == "本.zip"
    assert bookmark.display_name == "本"


@pytest.mark.parametrize(
    ("state", "expected_text"),
    [
        (PathAvailability.UNKNOWN, ""),
        (PathAvailability.CHECKING, "確認中"),
        (PathAvailability.AVAILABLE, ""),
        (PathAvailability.MISSING, "見つかりません"),
        (PathAvailability.UNAVAILABLE, "現在確認できません"),
        (PathAvailability.ERROR, "現在確認できません"),
    ],
)
def test_history_and_bookmark_models_use_cached_availability(
    tmp_path: Path,
    qapp: QApplication,
    state: PathAvailability,
    expected_text: str,
) -> None:
    path = tmp_path / "日本語" / "本.zip"
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.record_book_opened(
        str(path),
        item_type="archive",
        start_page_index=0,
        total_pages=1,
    )
    store.add_browser_bookmark(str(path), label="本", item_type="archive")
    filesystem = _AvailabilityFileSystem(state)
    service = PathAvailabilityService(filesystem=filesystem)
    history = HistoryModel(store, availability_service=service)
    bookmark = BookmarkModel(store, availability_service=service)
    assert _wait_until(
        qapp,
        lambda: history.data(
            history.index(0, 0),
            HistoryModel.AvailabilityRole,
        )
        == state.value,
    )
    assert expected_text in str(
        history.data(history.index(0, 0), Qt.ItemDataRole.DisplayRole)
    )
    assert expected_text in str(
        bookmark.data(bookmark.index(0, 0), Qt.ItemDataRole.DisplayRole)
    )
    assert _wait_until(qapp, lambda: filesystem.calls == 1)
    assert store.list_history()
    service.close()
    store.close()


def test_slow_path_probe_keeps_qtimer_responsive(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    release = Event()
    filesystem = _AvailabilityFileSystem(
        PathAvailability.UNAVAILABLE,
        release=release,
    )
    service = PathAvailabilityService(filesystem=filesystem)
    ticks: list[bool] = []
    QTimer.singleShot(0, lambda: ticks.append(True))
    service.probe(r"\\server\sleeping\本.zip")
    qapp.processEvents()
    assert ticks == [True]
    release.set()
    service.close()


def test_path_probe_deduplicates_same_path(
    qapp: QApplication,
) -> None:
    release = Event()
    filesystem = _AvailabilityFileSystem(
        PathAvailability.AVAILABLE,
        release=release,
    )
    service = PathAvailabilityService(filesystem=filesystem)
    first = service.probe(r"C:\本\同じ.zip")
    second = service.probe(r"c:\本\同じ.zip")
    assert first == second
    release.set()
    assert _wait_until(qapp, lambda: filesystem.calls == 1)
    service.close()


class _BlockingPdfBackend:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.rendered: list[int] = []
        self.events: list[str] = []

    def is_available(self):
        return True

    def open_document(self, path, *, password=None, cancel_token=None):
        return PdfDocumentInfo(
            "doc",
            str(path),
            1,
            (PdfPageInfo(0, 10, 10, 0),),
            False,
        )

    def render_page(self, request, *, cancel_token=None):
        self.rendered.append(request.page_index)
        self.events.append(f"render:{request.document_id}:{request.page_index}")
        if request.page_index == 99:
            self.started.set()
            assert self.release.wait(2)
        return PdfRenderResult(
            request.document_id,
            request.page_index,
            1,
            1,
            "RGBA",
            b"\0\0\0\0",
            request.generation,
            request.purpose,
        )

    def close_document(self, document_id):
        self.events.append(f"close:{document_id}")

    def close_all(self):
        self.events.append("close_all")


def test_pdf_shutdown_cancels_pending_and_closes_after_active() -> None:
    backend = _BlockingPdfBackend()
    service = PdfiumService(backend)
    with ThreadPoolExecutor(max_workers=8) as executor:
        active = executor.submit(
            service.render_page,
            PdfRenderRequest("doc", 99, 8, 8),
        )
        assert backend.started.wait(1)
        pending = [
            executor.submit(
                service.render_page,
                PdfRenderRequest("doc", index, 8, 8),
            )
            for index in range(6)
        ]
        service.shutdown(wait_seconds=0)
        assert service.state is PdfiumServiceState.SHUTTING_DOWN
        backend.release.set()
        active.result(timeout=2)
        for future in pending:
            with pytest.raises(PdfBackendError) as exc_info:
                future.result(timeout=2)
            assert exc_info.value.code is PdfErrorCode.CANCELLED
    service.shutdown(wait_seconds=2)
    assert backend.rendered == [99]
    assert backend.events == ["render:doc:99", "close_all"]
    assert service.state is PdfiumServiceState.STOPPED
    assert not service._pending
    assert not service._worker.is_alive()


def test_pdf_rejects_new_requests_after_shutdown() -> None:
    backend = _BlockingPdfBackend()
    backend.release.set()
    service = PdfiumService(backend)
    service.shutdown(wait_seconds=2)
    with pytest.raises(PdfBackendError) as exc_info:
        service.render_page(PdfRenderRequest("doc", 1, 8, 8))
    assert exc_info.value.code is PdfErrorCode.CANCELLED
    with pytest.raises(PdfBackendError):
        service.open_document("stopped.pdf")
    assert backend.rendered == []


def test_pdf_close_document_preempts_pending_same_document() -> None:
    backend = _BlockingPdfBackend()
    service = PdfiumService(backend)
    with ThreadPoolExecutor(max_workers=4) as executor:
        active = executor.submit(
            service.render_page,
            PdfRenderRequest("doc-a", 99, 8, 8),
        )
        assert backend.started.wait(1)
        cancelled = executor.submit(
            service.render_page,
            PdfRenderRequest("doc-a", 1, 8, 8),
        )
        unrelated = executor.submit(
            service.render_page,
            PdfRenderRequest("doc-b", 2, 8, 8),
        )
        close = executor.submit(
            service.close_document,
            "doc-a",
            wait=True,
        )
        backend.release.set()
        active.result(timeout=2)
        close.result(timeout=2)
        with pytest.raises(PdfBackendError):
            cancelled.result(timeout=2)
        unrelated.result(timeout=2)
    assert backend.events.index("close:doc-a") < backend.events.index(
        "render:doc-b:2"
    )
    service.shutdown(wait_seconds=2)


def _merge_request(
    source: Path,
    destination: Path,
    *,
    operation: FileOperationKind = FileOperationKind.MOVE,
    resolutions: tuple[tuple[str, str], ...] = (),
) -> FileOperationRequest:
    return FileOperationRequest(
        1,
        operation,
        (str(source),),
        str(destination.parent),
        collision_policy=FileCollisionPolicy.MERGE,
        collision_resolutions=resolutions,
    )


def test_folder_merge_move_reports_children_and_residuals(tmp_path: Path) -> None:
    source = tmp_path / "source" / "book"
    destination = tmp_path / "destination" / "book"
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "moved.txt").write_text("new", encoding="utf-8")
    (source / "skipped.txt").write_text("source", encoding="utf-8")
    (destination / "skipped.txt").write_text("destination", encoding="utf-8")

    result = FileOperationService().execute(
        _merge_request(source, destination)
    )
    root = result.items[0]
    assert not root.success and root.partially_completed
    assert len(root.child_results) == 2
    assert destination / "moved.txt" in map(Path, root.published_destination_paths)
    assert source / "moved.txt" in map(Path, root.moved_source_paths)
    assert source / "skipped.txt" in map(Path, root.skipped_source_paths)
    assert root.retry_source_paths == (str(source / "skipped.txt"),)
    assert not (source / "moved.txt").exists()
    assert (source / "skipped.txt").exists()
    assert (destination / "moved.txt").exists()


def test_folder_merge_move_replace_all_children_removes_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "book"
    destination = tmp_path / "destination" / "book"
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "new.txt").write_text("new", encoding="utf-8")
    (source / "replace.txt").write_text("source", encoding="utf-8")
    (destination / "replace.txt").write_text("old", encoding="utf-8")
    replace_target = str(destination / "replace.txt")

    result = FileOperationService().execute(
        _merge_request(
            source,
            destination,
            resolutions=((replace_target, FileCollisionPolicy.REPLACE.value),),
        )
    )
    root = result.items[0]
    assert root.success
    assert root.source_root_removed is True
    assert not source.exists()
    assert (destination / "replace.txt").read_text(encoding="utf-8") == "source"
    replaced = [
        item for item in root.leaf_results() if item.replaced_existing
    ]
    assert len(replaced) == 1
    assert replaced[0].destination_existed_before


def test_folder_merge_root_rmdir_failure_is_structured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source" / "book"
    destination = tmp_path / "destination" / "book"
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "moved.txt").write_text("x", encoding="utf-8")
    original_rmdir = os.rmdir

    def fail_root(path):
        if os.path.normcase(os.fspath(path)) == os.path.normcase(str(source)):
            raise PermissionError("root locked")
        return original_rmdir(path)

    monkeypatch.setattr(os, "rmdir", fail_root)
    result = FileOperationService().execute(
        _merge_request(source, destination)
    )
    root = result.items[0]
    assert not root.success
    assert root.source_root_removed is False
    assert root.partially_completed
    assert "root locked" in str(root.error_message)
    assert source.exists()


def _seed_content(
    store: MetadataStore,
    path: Path,
    *,
    rating: int,
    tag: str,
    comment: str,
    page: int,
    bookmark: str | None = None,
) -> None:
    store.record_book_opened(
        str(path),
        item_type="archive",
        start_page_index=page,
        total_pages=10,
    )
    store.set_rating(str(path), rating)
    store.set_tags(str(path), [tag])
    store.set_comment(str(path), comment)
    if bookmark is not None:
        store.add_browser_bookmark(
            str(path),
            label=bookmark,
            item_type="archive",
        )


def test_copy_replace_resets_destination_content_but_preserves_path_bookmark(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "book.zip"
    destination_dir = tmp_path / "destination"
    destination = destination_dir / source.name
    source.parent.mkdir()
    destination_dir.mkdir()
    source.write_bytes(b"source")
    destination.write_bytes(b"old")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    _seed_content(store, source, rating=5, tag="source", comment="source", page=4)
    _seed_content(
        store,
        destination,
        rating=1,
        tag="old",
        comment="old",
        page=2,
        bookmark="destination bookmark",
    )
    result = FileOperationService().copy(
        [source],
        destination_dir,
        collision_policy=FileCollisionPolicy.REPLACE,
    )
    FileOperationCoordinator(store)._on_completed(result)

    assert store.get_rating(str(source)) == 5
    assert store.get_tags(str(source)) == ["source"]
    assert store.get_rating(str(destination)) is None
    assert store.get_tags(str(destination)) == []
    assert store.get_comment(str(destination)) == ""
    assert store.get_reading_progress(str(destination)) is None
    assert store.is_browser_bookmarked(str(destination))
    store.close()


def test_move_replace_relocates_source_content_without_merging_old_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "book.zip"
    destination_dir = tmp_path / "destination"
    destination = destination_dir / source.name
    source.parent.mkdir()
    destination_dir.mkdir()
    source.write_bytes(b"source")
    destination.write_bytes(b"old")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    _seed_content(
        store,
        source,
        rating=5,
        tag="source",
        comment="source",
        page=4,
        bookmark="source bookmark",
    )
    _seed_content(
        store,
        destination,
        rating=1,
        tag="old",
        comment="old",
        page=2,
        bookmark="destination bookmark",
    )
    result = FileOperationService().move(
        [source],
        destination_dir,
        collision_policy=FileCollisionPolicy.REPLACE,
    )
    FileOperationCoordinator(store)._on_completed(result)

    assert not source.exists() and destination.exists()
    assert store.get_rating(str(destination)) == 5
    assert store.get_tags(str(destination)) == ["source"]
    assert store.get_comment(str(destination)) == "source"
    assert store.get_reading_progress(str(destination)).page_index == 4
    assert store.get_rating(str(source)) is None
    assert not store.is_browser_bookmarked(str(source))
    assert store.is_browser_bookmarked(str(destination))
    store.close()


def test_partial_move_replace_resets_destination_and_keeps_source_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source" / "book.zip"
    destination_dir = tmp_path / "destination"
    destination = destination_dir / source.name
    source.parent.mkdir()
    destination_dir.mkdir()
    source.write_bytes(b"source")
    destination.write_bytes(b"old")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    _seed_content(store, source, rating=5, tag="source", comment="source", page=4)
    _seed_content(store, destination, rating=1, tag="old", comment="old", page=2)
    original_replace = os.replace

    def exdev(old, new):
        if os.path.normcase(os.fspath(old)) == os.path.normcase(str(source)):
            raise OSError(errno.EXDEV, "cross volume")
        return original_replace(old, new)

    monkeypatch.setattr(os, "replace", exdev)
    monkeypatch.setattr(
        FileOperationService,
        "_remove_source",
        staticmethod(
            lambda _path: (_ for _ in ()).throw(PermissionError("locked"))
        ),
    )
    result = FileOperationService().move(
        [source],
        destination_dir,
        collision_policy=FileCollisionPolicy.REPLACE,
    )
    item = result.items[0]
    assert item.replaced_existing and item.destination_published
    assert item.state is FileOperationItemState.SOURCE_REMOVAL_FAILED
    FileOperationCoordinator(store)._on_completed(result)

    assert source.exists() and destination.exists()
    assert store.get_rating(str(source)) == 5
    assert store.get_tags(str(source)) == ["source"]
    assert store.get_rating(str(destination)) is None
    assert store.get_tags(str(destination)) == []
    assert store.get_reading_progress(str(destination)) is None
    store.close()


def test_move_replace_metadata_transaction_rolls_back(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.zip"
    destination = tmp_path / "destination.zip"
    source.write_bytes(b"source")
    destination.write_bytes(b"destination")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    _seed_content(store, source, rating=5, tag="source", comment="source", page=4)
    _seed_content(
        store,
        destination,
        rating=1,
        tag="destination",
        comment="destination",
        page=2,
    )
    original = store._replace_destination_content_locked

    def fail_after_change(**kwargs):
        original(**kwargs)
        raise RuntimeError("rollback")

    monkeypatch.setattr(store, "_replace_destination_content_locked", fail_after_change)
    assert not store.apply_move_replace_metadata(str(source), str(destination))
    assert store.get_rating(str(source)) == 5
    assert store.get_rating(str(destination)) == 1
    assert store.get_tags(str(destination)) == ["destination"]
    store.close()
