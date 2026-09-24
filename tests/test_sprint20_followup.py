from __future__ import annotations

import errno
import os
from pathlib import Path
from threading import Event, get_ident
from time import perf_counter

import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QObject, QPoint, QPointF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PySide6.QtTest import QTest

from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_scanner import (
    BrowserScanBatch,
    BrowserScanCompleted,
    BrowserScanEntry,
    BrowserScanError,
    BrowserScanRequest,
    BrowserScanStatus,
    BrowserDirectoryScanner,
)
from app.browser_window import BrowserWindow
from app.browser_navigation import BrowserLocation
from app.config_manager import ConfigManager
from app.file_operation_coordinator import FileOperationCoordinator
from app.file_operation_service import (
    FileOperationItemResult,
    FileOperationItemState,
    FileOperationKind,
    FileOperationResult,
    FileOperationService,
)
from app.metadata_store import MetadataStore
from app.performance_trace import performance_trace
from app.preview_provider_registry import PreviewProviderRegistry
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.browser_sort import BrowserSortPolicy
from app.file_preview import PreviewResult, PreviewSource
from app.thumbnail_render import ThumbnailRenderSpec
from app.thumbnail_provider import BrowserThumbnailProvider
from app.video_thumbnail_policy import (
    VideoMetadata,
    VideoThumbnailFrameMode,
    VideoThumbnailPolicy,
)


class _FakeScanner(QObject):
    batch_ready = Signal(object)
    scan_completed = Signal(object)
    scan_failed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[BrowserScanRequest] = []
        self.cancelled: list[int] = []

    def start(self, request: BrowserScanRequest) -> bool:
        self.requests.append(request)
        return True

    def cancel(self, generation: int) -> None:
        self.cancelled.append(generation)

    def close(self) -> None:
        pass


def _config(tmp_path: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return config


def _favorite_window(tmp_path: Path, qapp):
    folder_a = tmp_path / "お気に入り A"
    folder_b = tmp_path / "お気に入り B"
    folder_a.mkdir()
    folder_b.mkdir()
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.add_folder_bookmark(str(folder_a))
    store.add_folder_bookmark(str(folder_b))
    scanner = _FakeScanner()
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        metadata_store=store,
        scanner=scanner,  # type: ignore[arg-type]
        restore_initial_location=False,
    )
    window.show()
    qapp.processEvents()
    return window, store, scanner, folder_a, folder_b


def _favorite_point(window: BrowserWindow, row: int = 0) -> QPoint:
    index = window.folder_bookmark_model.index(row, 0)
    return window.favorite_view.visualRect(index).center()


def test_favorite_release_navigates_without_double_click_wait(tmp_path, qapp) -> None:
    window, store, scanner, folder, _other = _favorite_window(tmp_path, qapp)
    try:
        started = perf_counter()
        QTest.mouseClick(
            window.favorite_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=_favorite_point(window),
        )
        elapsed = perf_counter() - started
        assert scanner.requests
        assert scanner.requests[-1].path == str(folder.absolute())
        assert elapsed < 0.1
        assert not window._favorite_click_timer.isActive()
        assert window.address_bar.text() == str(folder.absolute())
        assert "読み込み中" in window.statusBar().currentMessage()
    finally:
        window.close()
        qapp.processEvents()
        store.close()


def test_favorite_double_click_does_not_start_duplicate_scan(tmp_path, qapp) -> None:
    window, store, scanner, _folder, _other = _favorite_window(tmp_path, qapp)
    try:
        point = _favorite_point(window)
        QTest.mouseClick(window.favorite_view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseDClick(window.favorite_view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseRelease(window.favorite_view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        assert len(scanner.requests) == 1
    finally:
        window.close()
        qapp.processEvents()
        store.close()


@pytest.mark.parametrize(
    "modifiers",
    [Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier],
)
def test_favorite_modified_click_does_not_navigate(tmp_path, qapp, modifiers) -> None:
    window, store, scanner, _folder, _other = _favorite_window(tmp_path, qapp)
    try:
        QTest.mouseClick(
            window.favorite_view.viewport(),
            Qt.MouseButton.LeftButton,
            modifiers,
            _favorite_point(window),
        )
        assert scanner.requests == []
    finally:
        window.close()
        qapp.processEvents()
        store.close()


def test_favorite_click_uses_cached_model_path_without_gui_io_or_db_query(
    tmp_path,
    qapp,
    monkeypatch,
) -> None:
    window, store, scanner, folder, _other = _favorite_window(tmp_path, qapp)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("synchronous I/O/query on favorite click")

    monkeypatch.setattr(Path, "exists", forbidden)
    monkeypatch.setattr(Path, "is_dir", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(os, "scandir", forbidden)
    monkeypatch.setattr(store, "list_folder_bookmarks", forbidden)
    try:
        QTest.mouseClick(
            window.favorite_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=_favorite_point(window),
        )
        assert scanner.requests[-1].path == str(folder.absolute())
    finally:
        window.close()
        qapp.processEvents()
        store.close()


@pytest.mark.parametrize("blocking_state", ["drag", "drop"])
def test_favorite_drag_or_file_drop_state_does_not_navigate(
    tmp_path,
    qapp,
    blocking_state,
) -> None:
    window, store, scanner, _folder, _other = _favorite_window(tmp_path, qapp)
    try:
        if blocking_state == "drag":
            window.favorite_view._drag_started = True
        else:
            window.favorite_view._drop_in_progress = True
        window._on_favorite_clicked(window.folder_bookmark_model.index(0, 0))
        assert scanner.requests == []
    finally:
        window.close()
        qapp.processEvents()
        store.close()


def test_slow_scanner_path_check_does_not_block_gui_timer(
    tmp_path,
    qapp,
    monkeypatch,
) -> None:
    folder = tmp_path / "slow"
    folder.mkdir()
    (folder / "item.txt").write_text("item", encoding="utf-8")
    scanner = BrowserDirectoryScanner()
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        scanner=scanner,
        restore_initial_location=False,
    )
    entered = Event()
    release = Event()
    import app.browser_scanner as scanner_module
    original_scandir = scanner_module.os.scandir

    def delayed_scandir(path):
        entered.set()
        release.wait(0.5)
        return original_scandir(path)

    monkeypatch.setattr(scanner_module.os, "scandir", delayed_scandir)
    timer_fired: list[bool] = []
    QTimer.singleShot(0, lambda: timer_fired.append(True))
    started = perf_counter()
    assert window.navigate_to(folder)
    assert perf_counter() - started < 0.1
    deadline = perf_counter() + 0.3
    while perf_counter() < deadline and (not entered.is_set() or not timer_fired):
        qapp.processEvents()
        QTest.qWait(1)
    assert entered.is_set()
    assert timer_fired
    release.set()
    scanner.wait_for_done(1000)
    qapp.processEvents()
    window.close()
    qapp.processEvents()


def test_failed_favorite_scan_keeps_previous_list_history_and_address(
    tmp_path,
    qapp,
) -> None:
    window, store, scanner, folder, _other = _favorite_window(tmp_path, qapp)
    previous = tmp_path / "previous"
    previous.mkdir()
    previous_item = previous / "kept.txt"
    window.current_path = previous.absolute()
    window.item_model.set_items(
        [
            BrowserItem(
                previous_item.name,
                previous_item,
                BrowserItemKind.OTHER,
                None,
            )
        ]
    )
    window.navigation_history.visit(BrowserLocation(str(previous.absolute())))
    window._sync_address_bar()
    history_before = len(window.navigation_history)
    try:
        QTest.mouseClick(
            window.favorite_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=_favorite_point(window),
        )
        request = scanner.requests[-1]
        scanner.scan_failed.emit(
            BrowserScanError(
                request.path,
                request.generation,
                BrowserScanStatus.NOT_FOUND,
                "missing",
            )
        )
        assert window.current_path == previous.absolute()
        assert window.item_model.row_for_path(previous_item) >= 0
        assert len(window.navigation_history) == history_before
        assert window.address_bar.text() == str(previous.absolute())
        assert window.config.get("last_browser_path", "") != str(folder.absolute())
    finally:
        window.close()
        qapp.processEvents()
        store.close()


def test_latest_favorite_generation_wins_and_stale_batch_is_discarded(
    tmp_path,
    qapp,
) -> None:
    window, store, scanner, folder_a, folder_b = _favorite_window(tmp_path, qapp)
    try:
        QTest.mouseClick(
            window.favorite_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=_favorite_point(window, 0),
        )
        first = scanner.requests[-1]
        QTest.mouseClick(
            window.favorite_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=_favorite_point(window, 1),
        )
        second = scanner.requests[-1]
        assert first.generation in scanner.cancelled
        stale_path = folder_a / "stale.txt"
        scanner.batch_ready.emit(
            BrowserScanBatch(
                first.path,
                first.generation,
                (
                    BrowserScanEntry(
                        str(stale_path),
                        stale_path.name,
                        "other",
                        None,
                        1,
                    ),
                ),
            )
        )
        fresh_path = folder_b / "fresh.txt"
        scanner.batch_ready.emit(
            BrowserScanBatch(
                second.path,
                second.generation,
                (
                    BrowserScanEntry(
                        str(fresh_path),
                        fresh_path.name,
                        "other",
                        None,
                        1,
                    ),
                ),
            )
        )
        scanner.scan_completed.emit(
            BrowserScanCompleted(
                second.path,
                second.generation,
                1,
            )
        )
        assert window.current_path == folder_b.absolute()
        assert window.item_model.row_for_path(fresh_path) >= 0
        assert window.item_model.row_for_path(stale_path) < 0
    finally:
        window.close()
        qapp.processEvents()
        store.close()


def test_same_favorite_folder_preserves_scroll_and_does_not_rescan(
    tmp_path,
    qapp,
) -> None:
    window, store, scanner, folder, _other = _favorite_window(tmp_path, qapp)
    try:
        window.current_path = folder.absolute()
        window.item_model.set_items(
            [
                BrowserItem(
                    f"{index:03}.txt",
                    folder / f"{index:03}.txt",
                    BrowserItemKind.OTHER,
                    None,
                )
                for index in range(100)
            ]
        )
        qapp.processEvents()
        window.list_view.verticalScrollBar().setValue(7)
        history_before = len(window.navigation_history)
        QTest.mouseClick(
            window.favorite_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=_favorite_point(window),
        )
        assert scanner.requests == []
        assert len(window.navigation_history) == history_before
        assert window.list_view.verticalScrollBar().value() == 7
    finally:
        window.close()
        qapp.processEvents()
        store.close()


def test_final_sorted_initial_batch_paints_before_tree_sync_and_thumbnail_request(
    tmp_path,
    qapp,
    monkeypatch,
) -> None:
    window, store, scanner, folder, _other = _favorite_window(tmp_path, qapp)
    calls: list[str] = []
    monkeypatch.setattr(window, "_sync_tree_to_path", lambda _path: calls.append("tree"))
    monkeypatch.setattr(
        window,
        "_schedule_thumbnail_requests",
        lambda *_args: calls.append("thumbnail"),
    )
    try:
        QTest.mouseClick(
            window.favorite_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=_favorite_point(window),
        )
        request = scanner.requests[-1]
        item = folder / "first.txt"
        scanner.batch_ready.emit(
            BrowserScanBatch(
                request.path,
                request.generation,
                (BrowserScanEntry(str(item), item.name, "other", None, 1),),
            )
        )
        assert calls == []
        scanner.scan_completed.emit(
            BrowserScanCompleted(request.path, request.generation, 1)
        )
        assert calls == []
        window._on_list_paint_completed()
        qapp.processEvents()
        assert calls == ["tree", "thumbnail"]
    finally:
        window.close()
        qapp.processEvents()
        store.close()


@pytest.mark.parametrize(
    ("item_count", "expected_initial"),
    [(20, 20), (200, 40), (1000, 40)],
)
def test_normal_and_favorite_large_folder_navigation_do_the_same_work(
    tmp_path,
    qapp,
    monkeypatch,
    item_count: int,
    expected_initial: int,
) -> None:
    folder = tmp_path / f"large-{item_count}"
    folder.mkdir()
    for index in range(item_count):
        (folder / f"{index:04}.txt").write_bytes(b"x")

    import app.browser_scanner as scanner_module
    import app.browser_model as browser_model_module
    import app.browser_sort as browser_sort_module

    original_scandir = scanner_module.os.scandir

    def run_navigation(route: str) -> dict[str, int]:
        route_root = tmp_path / route
        route_root.mkdir()
        store = MetadataStore(route_root / "metadata.sqlite3")
        store.add_folder_bookmark(str(folder))
        scanner = BrowserDirectoryScanner(max_workers=1)
        window = BrowserWindow(
            config_manager=_config(route_root),
            metadata_store=store,
            scanner=scanner,
            restore_initial_location=False,
        )
        window.show()
        qapp.processEvents()
        window._thumbnail_request_timer.stop()
        metrics = {
            "navigate": 0,
            "scan": 0,
            "enumeration": 0,
            "progress_items": 0,
            "progress_entry_refs": 0,
            "worker_convert": 0,
            "worker_natural_key": 0,
            "worker_sort": 0,
            "gui_sort": 0,
            "snapshot": 0,
            "model_reset": 0,
            "initial_batch": 0,
            "remaining_append": 0,
            "thumbnail_schedule": 0,
            "thumbnail_request": 0,
            "history_visit": 0,
            "tree_sync": 0,
            "config_save": 0,
            "metadata_query_or_save": 0,
        }
        worker_threads: set[int] = set()

        with monkeypatch.context() as patch:
            original_navigate = window.navigate_to
            original_start = scanner.start
            original_convert = browser_model_module.browser_item_from_scan_entry
            original_natural_key = browser_sort_module._natural_key
            original_worker_sort = BrowserSortPolicy.sorted_items
            original_gui_sort = window.item_model.sort_items
            original_begin = window.item_model.begin_final_directory_scan
            original_append = window.item_model.append_final_directory_scan
            original_snapshot = window._folder_snapshot_for_item
            original_history_visit = window.navigation_history.visit

            def counted_navigate(*args, **kwargs):
                metrics["navigate"] += 1
                return original_navigate(*args, **kwargs)

            def counted_start(request):
                metrics["scan"] += 1
                return original_start(request)

            def counted_scandir(path):
                metrics["enumeration"] += 1
                return original_scandir(path)

            def counted_progress_batch(batch):
                metrics["progress_items"] += batch.item_count
                metrics["progress_entry_refs"] += len(batch.entries)

            def counted_convert(scan_entry):
                worker_threads.add(get_ident())
                metrics["worker_convert"] += 1
                return original_convert(scan_entry)

            def counted_worker_sort(policy, items):
                worker_threads.add(get_ident())
                metrics["worker_sort"] += 1
                return original_worker_sort(policy, items)

            def counted_natural_key(value):
                worker_threads.add(get_ident())
                metrics["worker_natural_key"] += 1
                return original_natural_key(value)

            def counted_gui_sort(items):
                metrics["gui_sort"] += 1
                return original_gui_sort(items)

            def counted_begin(items, *, generation):
                metrics["initial_batch"] += 1
                return original_begin(items, generation=generation)

            def counted_append(items, *, generation):
                metrics["remaining_append"] += 1
                return original_append(items, generation=generation)

            def counted_snapshot(item, **kwargs):
                metrics["snapshot"] += 1
                return original_snapshot(item, **kwargs)

            def counted_history_visit(location):
                metrics["history_visit"] += 1
                return original_history_visit(location)

            def counted_metadata_call(*_args, **_kwargs):
                metrics["metadata_query_or_save"] += 1
                return None

            patch.setattr(window, "navigate_to", counted_navigate)
            patch.setattr(scanner, "start", counted_start)
            patch.setattr(scanner_module.os, "scandir", counted_scandir)
            scanner.batch_ready.connect(counted_progress_batch)
            patch.setattr(
                browser_model_module,
                "browser_item_from_scan_entry",
                counted_convert,
            )
            patch.setattr(
                BrowserSortPolicy,
                "sorted_items",
                counted_worker_sort,
            )
            patch.setattr(
                browser_sort_module,
                "_natural_key",
                counted_natural_key,
            )
            patch.setattr(window.item_model, "sort_items", counted_gui_sort)
            patch.setattr(
                window.item_model,
                "begin_final_directory_scan",
                counted_begin,
            )
            patch.setattr(
                window.item_model,
                "append_final_directory_scan",
                counted_append,
            )
            patch.setattr(window, "_folder_snapshot_for_item", counted_snapshot)
            patch.setattr(
                window.navigation_history,
                "visit",
                counted_history_visit,
            )
            patch.setattr(
                window,
                "_initial_scan_item_count",
                lambda count: min(count, 40),
            )
            patch.setattr(
                window,
                "_visible_row_range",
                lambda: (0, min(window.item_model.rowCount() - 1, 19)),
            )
            patch.setattr(
                window,
                "_sync_tree_to_path",
                lambda _path: metrics.__setitem__(
                    "tree_sync",
                    metrics["tree_sync"] + 1,
                ),
            )
            patch.setattr(
                window,
                "_schedule_thumbnail_requests",
                lambda *_args, **_kwargs: metrics.__setitem__(
                    "thumbnail_schedule",
                    metrics["thumbnail_schedule"] + 1,
                ),
            )
            patch.setattr(
                window.thumbnail_provider,
                "request",
                lambda *_args, **_kwargs: metrics.__setitem__(
                    "thumbnail_request",
                    metrics["thumbnail_request"] + 1,
                )
                or False,
            )
            patch.setattr(
                window.config,
                "save",
                lambda *_args, **_kwargs: metrics.__setitem__(
                    "config_save",
                    metrics["config_save"] + 1,
                ),
            )
            patch.setattr(store, "list_folder_bookmarks", counted_metadata_call)
            patch.setattr(store, "add_folder_bookmark", counted_metadata_call)
            patch.setattr(store, "remove_folder_bookmark", counted_metadata_call)
            patch.setattr(store, "reorder_folder_bookmarks", counted_metadata_call)
            patch.setattr(store, "flush", counted_metadata_call)
            window.item_model.modelReset.connect(
                lambda: metrics.__setitem__(
                    "model_reset",
                    metrics["model_reset"] + 1,
                )
            )
            gui_thread = get_ident()

            if route == "favorite":
                window._on_favorite_clicked(
                    window.folder_bookmark_model.index(0, 0)
                )
            else:
                window.navigate_to(folder)

            assert window.wait_for_scan(5000)
            window._on_list_paint_completed()
            for _ in range(4):
                qapp.processEvents()
            assert window.item_model.rowCount() == item_count
            assert worker_threads
            assert gui_thread not in worker_threads
            window._request_visible_thumbnails()

        window.close()
        qapp.processEvents()
        store.close()
        return metrics

    normal = run_navigation("normal")
    favorite = run_navigation("favorite")

    expected = {
        "navigate": 1,
        "scan": 1,
        "enumeration": 1,
        "progress_items": item_count,
        "progress_entry_refs": 0,
        "worker_convert": item_count,
        "worker_natural_key": item_count,
        "worker_sort": 1,
        "gui_sort": 0,
        "snapshot": 0,
        "model_reset": 1,
        "initial_batch": 1,
        "remaining_append": int(item_count > expected_initial),
        "thumbnail_schedule": 1,
        "thumbnail_request": expected_initial,
        "history_visit": 1,
        "tree_sync": 1,
        "config_save": 0,
        "metadata_query_or_save": 0,
    }
    assert normal == expected
    assert favorite == expected


@pytest.mark.parametrize(
    ("item_count", "expected_initial"),
    [(20, 20), (200, 40), (1000, 40)],
)
def test_scan_finish_applies_stable_initial_range_then_appends_without_reset(
    tmp_path,
    qapp,
    monkeypatch,
    item_count: int,
    expected_initial: int,
) -> None:
    window, store, scanner, folder, _other = _favorite_window(tmp_path, qapp)
    requested: list[str] = []
    committed: list[str] = []
    resets: list[bool] = []
    inserts: list[tuple[int, int]] = []
    sort_calls = 0
    original_sort_items = window.item_model.sort_items

    def counted_sort_items(items):
        nonlocal sort_calls
        sort_calls += 1
        return original_sort_items(items)

    monkeypatch.setattr(
        window.thumbnail_provider,
        "request",
        lambda item, *_args, **_kwargs: requested.append(str(item.path)) or False,
    )
    monkeypatch.setattr(
        window,
        "_initial_scan_item_count",
        lambda count: min(count, 40),
    )
    monkeypatch.setattr(
        window,
        "_visible_row_range",
        lambda: (0, min(window.item_model.rowCount() - 1, 19)),
    )
    monkeypatch.setattr(
        window.item_model,
        "sort_items",
        counted_sort_items,
    )
    window.directory_scan_committed.connect(committed.append)
    window.item_model.modelReset.connect(lambda: resets.append(True))
    window.item_model.rowsInserted.connect(
        lambda _parent, first, last: inserts.append((first, last))
    )
    try:
        QTest.mouseClick(
            window.favorite_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=_favorite_point(window),
        )
        assert len(scanner.requests) == 1
        request = scanner.requests[-1]
        entries = tuple(
            BrowserScanEntry(
                str(folder / f"{index:04}.jpg"),
                f"{index:04}.jpg",
                "image",
                index,
                index + 1,
            )
            for index in range(item_count)
        )
        split_at = max(1, item_count // 2)
        scanner.batch_ready.emit(
            BrowserScanBatch(
                request.path,
                request.generation,
                entries[:split_at],
            )
        )
        assert window.item_model.rowCount() == 0
        assert requested == []
        assert sort_calls == 0
        scanner.batch_ready.emit(
            BrowserScanBatch(
                request.path,
                request.generation,
                entries[split_at:],
            )
        )
        assert window.item_model.rowCount() == 0
        assert sort_calls == 0

        scanner.scan_completed.emit(
            BrowserScanCompleted(
                request.path,
                request.generation,
                item_count,
            )
        )
        assert window.item_model.rowCount() == expected_initial
        assert len(resets) == 1
        assert sort_calls == 1
        assert inserts == []
        initial_paths = tuple(str(item.path) for item in window.items)
        assert initial_paths == tuple(
            str(folder / f"{index:04}.jpg")
            for index in range(expected_initial)
        )
        assert committed == (
            []
            if item_count > expected_initial
            else [str(folder.absolute())]
        )
        if item_count <= expected_initial:
            assert window._pending_scan is None
        else:
            assert window._pending_scan is not None

        window._thumbnail_request_timer.stop()
        window._request_visible_thumbnails()
        requested_before_append = tuple(requested)
        assert len(requested_before_append) == expected_initial
        assert len(set(requested_before_append)) == expected_initial
        window._flush_pending_scan_batch()

        assert window.item_model.rowCount() == item_count
        assert len(resets) == 1
        assert tuple(requested) == requested_before_append
        assert inserts == (
            []
            if item_count <= expected_initial
            else [(expected_initial, item_count - 1)]
        )
        assert tuple(
            str(item.path) for item in window.items[:expected_initial]
        ) == initial_paths
        assert committed == [str(folder.absolute())]
        assert window._pending_scan is None
        assert "読み込み中" not in window.statusBar().currentMessage()
    finally:
        window.close()
        qapp.processEvents()
        store.close()


def test_favorite_performance_trace_covers_release_to_first_paint(
    tmp_path,
    qapp,
) -> None:
    window, store, scanner, folder, _other = _favorite_window(tmp_path, qapp)
    previous = performance_trace.enabled
    performance_trace.enabled = True
    performance_trace.clear()
    try:
        QTest.mouseClick(
            window.favorite_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=_favorite_point(window),
        )
        request = scanner.requests[-1]
        item = folder / "first.txt"
        scanner.batch_ready.emit(
            BrowserScanBatch(
                request.path,
                request.generation,
                (BrowserScanEntry(str(item), item.name, "other", None, 1),),
            )
        )
        scanner.scan_completed.emit(
            BrowserScanCompleted(request.path, request.generation, 1)
        )
        window._on_list_paint_completed()
        deadline = perf_counter() + 0.75
        while perf_counter() < deadline:
            qapp.processEvents()
            current_names = {
                event.name
                for event in performance_trace.events_for(request.trace_id)
            }
            if "folder_tree.sync.complete" in current_names:
                break
            QTest.qWait(5)
        names = {
            event.name for event in performance_trace.events_for(request.trace_id)
        }
        assert {
            "favorite.mouse_press",
            "favorite.mouse_release",
            "favorite.navigation.confirmed",
            "navigation.navigate_to.called",
            "navigation.generation.issued",
            "scanner.first_batch.gui_arrived",
            "browser.model.first_batch.applied",
            "browser.list.first_paint",
            "folder_tree.sync.begin",
            "folder_tree.sync.complete",
            "thumbnail.request.begin",
        } <= names
    finally:
        performance_trace.enabled = previous
        window.close()
        qapp.processEvents()
        store.close()


@pytest.mark.parametrize("case", range(20))
def test_move_same_volume_postcondition_on_real_filesystem(tmp_path, case) -> None:
    source_root = tmp_path / f"source-{case}"
    destination_root = tmp_path / f"destination-{case}"
    source_root.mkdir()
    destination_root.mkdir()
    source = source_root / ("item" if case % 2 else "item.bin")
    if case % 2:
        source.mkdir()
        (source / "日本語.txt").write_text(str(case), encoding="utf-8")
    else:
        source.write_bytes(bytes([case]) * (case + 1))
    result = FileOperationService().move((source,), destination_root)
    item = result.items[0]
    destination = destination_root / source.name
    assert item.state is FileOperationItemState.MOVED
    assert item.success and item.source_removed and item.destination_published
    assert not source.exists()
    assert destination.exists()


@pytest.mark.parametrize("case", range(20))
def test_move_cross_volume_fallback_postcondition_on_real_filesystem(
    tmp_path,
    case,
    monkeypatch,
) -> None:
    source_root = tmp_path / f"cross-source-{case}"
    destination_root = tmp_path / f"cross-destination-{case}"
    source_root.mkdir()
    destination_root.mkdir()
    source = source_root / ("folder" if case % 2 else "file.bin")
    if case % 2:
        source.mkdir()
        (source / "child.txt").write_text("child", encoding="utf-8")
    else:
        source.write_bytes(b"x" * (case + 1))
    destination = destination_root / source.name
    original_rename = os.rename
    raised = False

    def exdev_once(src, dst, *args, **kwargs):
        nonlocal raised
        if (
            not raised
            and os.path.normcase(os.fspath(src)) == os.path.normcase(str(source))
            and os.path.normcase(os.fspath(dst)) == os.path.normcase(str(destination))
        ):
            raised = True
            raise OSError(errno.EXDEV, "different volume")
        return original_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "rename", exdev_once)
    result = FileOperationService().move((source,), destination_root)
    item = result.items[0]
    assert raised
    assert item.state is FileOperationItemState.MOVED
    assert not source.exists()
    assert destination.exists()


def test_move_source_removal_failure_is_partial_not_success(
    tmp_path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source_root.mkdir()
    destination_root.mkdir()
    source = source_root / "item.bin"
    source.write_bytes(b"payload")
    destination = destination_root / source.name
    original_rename = os.rename

    def force_cross_volume(src, dst, *args, **kwargs):
        if os.path.normcase(os.fspath(src)) == os.path.normcase(str(source)):
            raise OSError(errno.EXDEV, "different volume")
        return original_rename(src, dst, *args, **kwargs)

    service = FileOperationService()
    monkeypatch.setattr(os, "rename", force_cross_volume)
    monkeypatch.setattr(
        service,
        "_remove_source",
        lambda _path: (_ for _ in ()).throw(PermissionError("sharing violation")),
    )
    item = service.move((source,), destination_root).items[0]
    assert not item.success and item.partial_success
    assert item.state is FileOperationItemState.SOURCE_REMOVAL_FAILED
    assert item.destination_exists_after and item.source_exists_after
    assert source.exists() and destination.exists()


def test_metadata_is_not_relocated_for_published_destination_with_source_remaining(
    tmp_path,
) -> None:
    calls: list[tuple[str, str]] = []

    class Store:
        def relocate_tree(self, source, destination):
            calls.append((source, destination))

    coordinator = FileOperationCoordinator(Store())  # type: ignore[arg-type]
    item = FileOperationItemResult(
        "source",
        "destination",
        False,
        "partial_success",
        "source remains",
        True,
        state=FileOperationItemState.SOURCE_REMOVAL_FAILED,
        destination_exists_after=True,
        source_exists_after=True,
        destination_published=True,
    )
    coordinator._on_completed(
        FileOperationResult(FileOperationKind.MOVE, (item,))
    )
    assert calls == []
    coordinator.close()


@pytest.mark.parametrize(
    ("ratio", "crop", "expected"),
    [
        ("square_1_1", "letterbox", (192, 192)),
        ("landscape_4_3", "center_crop", (192, 144)),
        ("landscape_16_9", "smart_crop", (192, 108)),
        ("portrait_1_sqrt2", "letterbox", (136, 192)),
    ],
)
def test_video_thumbnail_uses_selected_frame_ratio_and_crop(
    ratio,
    crop,
    expected,
) -> None:
    spec = ThumbnailRenderSpec.from_settings(192, ratio, crop)
    image = Image.new("RGB", (640, 360), "navy")
    rendered = VideoThumbnailPolicy.render(image, spec)
    assert (rendered.width(), rendered.height()) == expected


def test_video_policy_uses_one_third_and_smart_avoids_title_frame() -> None:
    one_third = VideoThumbnailPolicy(VideoThumbnailFrameMode.ONE_THIRD)
    assert one_third.candidate_timestamps(90.0) == (30.0,)
    smart = VideoThumbnailPolicy(VideoThumbnailFrameMode.SMART)
    assert smart.candidate_timestamps(90.0) == (30.0, 45.0, 60.0)
    title = Image.new("RGB", (64, 64), "black")
    scene = Image.new("RGB", (64, 64))
    for y in range(64):
        for x in range(64):
            scene.putpixel((x, y), ((x * 4) % 256, (y * 4) % 256, 180))
    selected = smart.select([(30.0, title), (45.0, scene)])
    assert selected is not None and selected.timestamp == 45.0


def test_video_policy_handles_sar_dar_and_rotation() -> None:
    metadata = VideoMetadata(
        width=720,
        height=576,
        sample_aspect_ratio=16 / 15,
        display_aspect_ratio=4 / 3,
        rotation=90,
    )
    assert VideoThumbnailPolicy.display_aspect_ratio(metadata) == pytest.approx(3 / 4)
    prepared = VideoThumbnailPolicy.prepare_frame(
        Image.new("RGB", (720, 576), "red"),
        metadata,
    )
    assert prepared.width / prepared.height == pytest.approx(3 / 4, rel=0.01)


def test_video_registry_returns_shell_placeholder_then_ffmpeg_final(tmp_path) -> None:
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"video")
    shell_image = QImage(320, 180, QImage.Format.Format_ARGB32)
    shell_image.fill(Qt.GlobalColor.red)
    final_image = QImage(192, 192, QImage.Format.Format_ARGB32)
    final_image.fill(Qt.GlobalColor.green)

    class Shell:
        def request_thumbnail(self, *_args, **_kwargs):
            return PreviewResult.ready_image(
                shell_image,
                source=PreviewSource.WINDOWS_SHELL,
                persist_to_disk=False,
            )

        def shutdown(self):
            pass

    class FFmpeg:
        policy = VideoThumbnailPolicy()

        def generate(self, *_args, **_kwargs):
            return PreviewResult.ready_image(
                final_image,
                source=PreviewSource.FFMPEG,
                persist_to_disk=True,
                entry_path=VideoThumbnailPolicy.cache_variant("smart"),
            )

    from app.browser_model import BrowserItem, BrowserItemKind

    registry = PreviewProviderRegistry(
        settings={
            "video_thumbnail_backend": "auto",
            "video_thumbnail_frame_mode": "smart",
            "video_thumbnail_shell_placeholder": True,
        },
        shell_service=Shell(),  # type: ignore[arg-type]
        ffmpeg_backend=FFmpeg(),  # type: ignore[arg-type]
    )
    item = BrowserItem(path.name, path, BrowserItemKind.OTHER, None, extension=".mp4")
    result = registry.generate(
        item,
        ThumbnailRenderSpec.from_settings(192, "square_1_1", "center_crop"),
        priority=ThumbnailPriority.VISIBLE,
    )
    assert result.source is PreviewSource.FFMPEG
    assert result.provisional_image is not None
    assert result.provisional_image.pixelColor(0, 0).red() > 240


def test_video_provider_forwards_shell_placeholder_before_final(tmp_path) -> None:
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"video")
    shell_image = QImage(48, 48, QImage.Format.Format_ARGB32)
    shell_image.fill(Qt.GlobalColor.red)
    final_image = QImage(48, 48, QImage.Format.Format_ARGB32)
    final_image.fill(Qt.GlobalColor.green)

    class Shell:
        def request_thumbnail(self, *_args, **_kwargs):
            return PreviewResult.ready_image(
                shell_image, source=PreviewSource.WINDOWS_SHELL,
                persist_to_disk=False,
            )

        def shutdown(self):
            pass

    class FFmpeg:
        policy = VideoThumbnailPolicy()

        def generate(self, *_args, **_kwargs):
            return PreviewResult.ready_image(
                final_image, source=PreviewSource.FFMPEG,
                persist_to_disk=True,
                entry_path=VideoThumbnailPolicy.cache_variant("smart"),
            )

    from app.browser_model import BrowserItem, BrowserItemKind

    registry = PreviewProviderRegistry(
        settings={"video_thumbnail_backend": "auto",
                  "video_thumbnail_shell_placeholder": True},
        shell_service=Shell(),  # type: ignore[arg-type]
        ffmpeg_backend=FFmpeg(),  # type: ignore[arg-type]
    )
    provider = BrowserThumbnailProvider(
        preview_registry=registry, disk_cache_enabled=False,
    )
    item = BrowserItem(path.name, path, BrowserItemKind.OTHER, None, extension=".mp4")
    result = provider._load_pipeline(
        item, ThumbnailRenderSpec.from_settings(128, "square_1_1", "letterbox"),
        thumbnail_priority=ThumbnailPriority.VISIBLE,
    )
    assert result.provisional_image is not None
    assert result.provisional_image.pixelColor(0, 0).red() > 240
    assert result.image is not None
    assert result.image.pixelColor(0, 0).green() > 240
    provider.close(wait_msecs=1000)


def test_external_drop_event_reaches_browser_viewport_and_is_focus_only(
    tmp_path,
    qapp,
    monkeypatch,
) -> None:
    path = tmp_path / "外部 drop.txt"
    path.write_text("drop", encoding="utf-8")
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.show()
    qapp.processEvents()
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        window.browser_main_drop,
        "handle_external_paths",
        lambda paths, *, behavior: calls.append(tuple(paths)) or 1,
    )
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path.absolute()))])
    enter = QDragEnterEvent(
        QPoint(3, 3),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qapp.sendEvent(window.list_view.viewport(), enter)
    assert enter.isAccepted()
    drop = QDropEvent(
        QPointF(3, 3),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qapp.sendEvent(window.list_view.viewport(), drop)
    assert drop.isAccepted()
    assert calls == [(str(path.absolute()),)]
    window.close()
    qapp.processEvents()


def test_http_drop_is_ignored_by_browser_viewport(tmp_path, qapp) -> None:
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/image.jpg")])
    enter = QDragEnterEvent(
        QPoint(3, 3),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qapp.sendEvent(window.list_view.viewport(), enter)
    assert not enter.isAccepted()
    window.close()
    qapp.processEvents()
