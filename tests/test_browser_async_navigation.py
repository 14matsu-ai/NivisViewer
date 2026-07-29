from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from time import monotonic

import pytest
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from app.browser_model import browser_item_from_scan_entry
from app.browser_scanner import (
    BrowserDirectoryScanner,
    BrowserScanBatch,
    BrowserScanCompleted,
    BrowserScanEntry,
    BrowserScanError,
    BrowserScanRequest,
    BrowserScanStatus,
)
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.thumbnail_provider import BrowserThumbnailProvider


class FakeScanner(QObject):
    batch_ready = Signal(object)
    scan_completed = Signal(object)
    scan_failed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[BrowserScanRequest] = []
        self.cancelled: list[int] = []
        self.closed = False

    def start(self, request: BrowserScanRequest) -> bool:
        self.requests.append(request)
        return not self.closed

    def cancel(self, generation: int) -> None:
        self.cancelled.append(generation)

    def close(self) -> None:
        self.closed = True


class RecordingThumbnailProvider(BrowserThumbnailProvider):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[tuple[str, int, int, ThumbnailPriority]] = []
        self.cancel_calls: list[set[str]] = []

    def request(
        self,
        item,
        size: int,
        *,
        generation: int | None = None,
        priority: ThumbnailPriority = ThumbnailPriority.VISIBLE,
    ) -> bool:
        self.requests.append(
            (str(item.path), size, int(generation or 0), priority)
        )
        return True

    def cancel_prefetch_except(
        self,
        paths: set[str],
        *,
        size: int,
        generation: int,
    ) -> int:
        self.cancel_calls.append(set(paths))
        return 0


def make_config(tmp_path: Path, folder: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    return config


def entry(path: Path, *, kind: str = "image", size: int = 10) -> BrowserScanEntry:
    return BrowserScanEntry(
        path=str(path.absolute()),
        display_name=path.name,
        item_kind=kind,
        modified_time_ns=1,
        file_size=None if kind == "folder" else size,
    )


def complete_empty(scanner: FakeScanner, request: BrowserScanRequest) -> None:
    scanner.scan_completed.emit(
        BrowserScanCompleted(request.path, request.generation, 0)
    )


def make_committed_window(
    tmp_path: Path,
    qapp: QApplication,
    *,
    provider: RecordingThumbnailProvider | None = None,
) -> tuple[BrowserWindow, FakeScanner]:
    initial = tmp_path / "initial"
    initial.mkdir()
    scanner = FakeScanner()
    window = BrowserWindow(
        config_manager=make_config(tmp_path, initial),
        scanner=scanner,  # type: ignore[arg-type]
        thumbnail_provider=provider,
    )
    complete_empty(scanner, scanner.requests[0])
    qapp.processEvents()
    assert window.current_path == initial.absolute()
    return window, scanner


def test_navigate_returns_before_scan_and_event_loop_remains_responsive(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    target = tmp_path / "slow"
    target.mkdir()
    ticks: list[bool] = []
    QTimer.singleShot(0, lambda: ticks.append(True))

    assert window.navigate_to(target)
    assert window.current_path != target.absolute()
    qapp.processEvents()

    assert ticks == [True]
    assert scanner.requests[-1].path == str(target.absolute())
    assert "読み込み中" in window.statusBar().currentMessage()
    window.close()


def test_navigate_does_not_use_synchronous_path_queries(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    target = tmp_path / "lexical-only"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GUI navigation performed a synchronous path query")

    with monkeypatch.context() as path_patch:
        for method_name in ("stat", "exists", "is_dir", "resolve"):
            path_patch.setattr(Path, method_name, forbidden)
        path_patch.setattr(os, "stat", forbidden)
        path_patch.setattr(os.path, "exists", forbidden)
        path_patch.setattr(os.path, "isdir", forbidden)

        assert window.navigate_to(target)
        assert scanner.requests[-1].path == os.path.abspath(str(target))
    window.close()


def test_slow_scanner_validation_does_not_block_navigate_or_qtimer(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    initial = tmp_path / "initial"
    target = tmp_path / "slow-validation"
    initial.mkdir()
    target.mkdir()
    scanner = BrowserDirectoryScanner(max_workers=1)
    window = BrowserWindow(
        config_manager=make_config(tmp_path, initial),
        scanner=scanner,
    )
    assert window.wait_for_scan()
    started = Event()
    release = Event()
    original_scandir = os.scandir

    def slow_scandir(path):
        if os.path.normcase(os.fspath(path)) == os.path.normcase(str(target)):
            started.set()
            release.wait(2)
        return original_scandir(path)

    monkeypatch.setattr("app.browser_scanner.os.scandir", slow_scandir)
    try:
        before = monotonic()
        assert window.navigate_to(target)
        elapsed = monotonic() - before
        assert elapsed < 0.5
        assert started.wait(1)

        ticks: list[bool] = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        qapp.processEvents()
        assert ticks == [True]
        assert window.current_path == initial.absolute()
        assert "読み込み中" in window.statusBar().currentMessage()
    finally:
        release.set()
        scanner.wait_for_done(2000)
        qapp.processEvents()
        window.close()


def test_scan_batches_remain_private_until_final_sort_is_available(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    target = tmp_path / "incremental"
    target.mkdir()
    assert window.navigate_to(target)
    request = scanner.requests[-1]

    scanner.batch_ready.emit(
        BrowserScanBatch(
            request.path,
            request.generation,
            (entry(target / "book2.jpg"),),
        )
    )

    assert window.current_path != target.absolute()
    assert window.items == ()
    assert window._pending_scan is not None

    scanner.batch_ready.emit(
        BrowserScanBatch(
            request.path,
            request.generation,
            (entry(target / "book10.jpg"),),
        )
    )
    assert window.current_path != target.absolute()
    assert window.items == ()

    scanner.scan_completed.emit(
        BrowserScanCompleted(request.path, request.generation, 2)
    )

    assert [item.display_name for item in window.items] == [
        "book2.jpg",
        "book10.jpg",
    ]
    assert window._pending_scan is None
    window.close()


def test_new_navigation_cancels_and_discards_old_generation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    first = tmp_path / "A"
    second = tmp_path / "B"
    first.mkdir()
    second.mkdir()
    assert window.navigate_to(first)
    first_request = scanner.requests[-1]
    assert window.navigate_to(second)
    second_request = scanner.requests[-1]

    scanner.batch_ready.emit(
        BrowserScanBatch(
            first_request.path,
            first_request.generation,
            (entry(first / "old.jpg"),),
        )
    )
    scanner.batch_ready.emit(
        BrowserScanBatch(
            second_request.path,
            second_request.generation,
            (entry(second / "new.jpg"),),
        )
    )
    scanner.scan_completed.emit(
        BrowserScanCompleted(
            first_request.path,
            first_request.generation,
            1,
        )
    )
    assert window.current_path != first.absolute()
    scanner.scan_completed.emit(
        BrowserScanCompleted(
            second_request.path,
            second_request.generation,
            1,
        )
    )

    assert first_request.generation in scanner.cancelled
    assert window.current_path == second.absolute()
    assert [item.display_name for item in window.items] == ["new.jpg"]
    window.close()


def test_scan_finish_uses_latest_sort_settings(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    target = tmp_path / "sort-change"
    target.mkdir()
    assert window.navigate_to(target)
    request = scanner.requests[-1]
    scanner.batch_ready.emit(
        BrowserScanBatch(
            request.path,
            request.generation,
            (
                entry(target / "book2.jpg"),
                entry(target / "book10.jpg"),
            ),
        )
    )

    window.config.apply(
        {
            "browser_sort_key": "name",
            "browser_sort_order": "descending",
        }
    )
    scanner.scan_completed.emit(
        BrowserScanCompleted(request.path, request.generation, 2)
    )

    assert [item.display_name for item in window.items] == [
        "book10.jpg",
        "book2.jpg",
    ]
    window.close()


def test_visibility_change_restarts_pending_target_and_discards_old_buffer(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    target = tmp_path / "visibility-change"
    target.mkdir()
    assert window.navigate_to(target)
    first = scanner.requests[-1]
    scanner.batch_ready.emit(
        BrowserScanBatch(
            first.path,
            first.generation,
            (entry(target / "old.jpg"),),
        )
    )

    window.config.apply({"browser_show_hidden_items": False})
    second = scanner.requests[-1]
    assert second.path == first.path
    assert second.generation != first.generation
    assert first.generation in scanner.cancelled

    scanner.scan_completed.emit(
        BrowserScanCompleted(first.path, first.generation, 1)
    )
    scanner.batch_ready.emit(
        BrowserScanBatch(
            second.path,
            second.generation,
            (entry(target / "new.jpg"),),
        )
    )
    scanner.scan_completed.emit(
        BrowserScanCompleted(second.path, second.generation, 1)
    )

    assert [item.display_name for item in window.items] == ["new.jpg"]
    window.close()


def test_failed_scan_preserves_current_list_history_and_last_path(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    current = window.current_path
    original_items = window.items
    original_history = len(window.navigation_history)
    original_last_path = window.config.get("last_browser_path")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    assert window.navigate_to(blocked)
    request = scanner.requests[-1]

    scanner.scan_failed.emit(
        BrowserScanError(
            request.path,
            request.generation,
            BrowserScanStatus.ACCESS_DENIED,
            "denied",
        )
    )

    assert window.current_path == current
    assert window.items == original_items
    assert len(window.navigation_history) == original_history
    assert window.config.get("last_browser_path") == original_last_path
    assert window.statusBar().currentMessage() == "フォルダへアクセスできません"
    window.close()


def test_refresh_error_retains_committed_items_and_clears_loading(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    current = window.current_path
    assert current is not None
    existing = browser_item_from_scan_entry(entry(current / "existing.jpg"))
    window.item_model.set_items([existing])

    assert window.refresh_current_folder()
    request = scanner.requests[-1]
    scanner.batch_ready.emit(
        BrowserScanBatch(
            request.path,
            request.generation,
            (entry(current / "partial.jpg"),),
        )
    )
    scanner.scan_failed.emit(
        BrowserScanError(
            request.path,
            request.generation,
            BrowserScanStatus.ACCESS_DENIED,
            "denied",
        )
    )

    assert window.items == (existing,)
    assert window._pending_scan is None
    assert "読み込み中" not in window.statusBar().currentMessage()
    window.close()


def test_identical_refresh_does_not_reset_restore_or_request_thumbnails(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    provider = RecordingThumbnailProvider()
    window, scanner = make_committed_window(
        tmp_path,
        qapp,
        provider=provider,
    )
    current = window.current_path
    assert current is not None
    scanned = entry(current / "same.jpg", size=25)
    item = browser_item_from_scan_entry(scanned)
    window.item_model.set_items([item])
    window.list_view.setCurrentIndex(window.item_model.index(0, 0))
    resets: list[bool] = []
    restores: list[bool] = []
    window.item_model.modelReset.connect(lambda: resets.append(True))
    monkeypatch.setattr(
        window,
        "_schedule_list_view_state_restore",
        lambda _state: restores.append(True),
    )
    provider.requests.clear()
    generation_before = window._generation

    assert window.refresh_current_folder()
    request = scanner.requests[-1]
    scanner.batch_ready.emit(
        BrowserScanBatch(
            request.path,
            request.generation,
            (scanned,),
        )
    )
    scanner.scan_completed.emit(
        BrowserScanCompleted(request.path, request.generation, 1)
    )

    assert resets == []
    assert restores == []
    assert provider.requests == []
    assert window._generation == generation_before
    assert window.list_view.currentIndex().row() == 0
    assert window._pending_scan is None
    window.close()


def test_repeated_identical_refreshes_remain_noop(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    provider = RecordingThumbnailProvider()
    window, scanner = make_committed_window(
        tmp_path,
        qapp,
        provider=provider,
    )
    current = window.current_path
    assert current is not None
    scanned = entry(current / "same.jpg", size=25)
    window.item_model.set_items([browser_item_from_scan_entry(scanned)])
    resets: list[bool] = []
    window.item_model.modelReset.connect(lambda: resets.append(True))
    generation_before = window._generation
    provider.requests.clear()

    for _ in range(3):
        assert window.refresh_current_folder()
        request = scanner.requests[-1]
        scanner.batch_ready.emit(
            BrowserScanBatch(
                request.path,
                request.generation,
                (scanned,),
            )
        )
        scanner.scan_completed.emit(
            BrowserScanCompleted(request.path, request.generation, 1)
        )

    assert resets == []
    assert provider.requests == []
    assert window._generation == generation_before
    assert window._pending_scan is None
    window.close()


def test_changed_refresh_replaces_once_and_preserves_existing_selection(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    current = window.current_path
    assert current is not None
    existing_entries = tuple(
        entry(current / f"{index:03}.jpg", size=index + 1)
        for index in range(30)
    )
    window.item_model.set_items(
        [browser_item_from_scan_entry(value) for value in existing_entries]
    )
    selected_path = current / "010.jpg"
    selected_row = window.item_model.row_for_path(selected_path)
    window.list_view.setCurrentIndex(
        window.item_model.index(selected_row, 0)
    )
    resets: list[bool] = []
    window.item_model.modelReset.connect(lambda: resets.append(True))

    assert window.refresh_current_folder()
    request = scanner.requests[-1]
    refreshed = existing_entries + (
        entry(current / "new.jpg", size=99),
    )
    scanner.batch_ready.emit(
        BrowserScanBatch(
            request.path,
            request.generation,
            refreshed,
        )
    )
    scanner.scan_completed.emit(
        BrowserScanCompleted(
            request.path,
            request.generation,
            len(refreshed),
        )
    )

    current_item = window.item_model.item_at(
        window.list_view.currentIndex()
    )
    assert resets == [True]
    assert current_item is not None
    assert current_item.path == selected_path
    assert window.item_model.row_for_path(current / "new.jpg") >= 0
    window.close()


def test_refresh_clears_current_index_when_selected_item_was_removed(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    current = window.current_path
    assert current is not None
    removed = entry(current / "removed.jpg")
    kept = entry(current / "kept.jpg")
    window.item_model.set_items(
        [
            browser_item_from_scan_entry(removed),
            browser_item_from_scan_entry(kept),
        ]
    )
    row = window.item_model.row_for_path(removed.path)
    window.list_view.setCurrentIndex(window.item_model.index(row, 0))

    assert window.refresh_current_folder()
    request = scanner.requests[-1]
    scanner.batch_ready.emit(
        BrowserScanBatch(request.path, request.generation, (kept,))
    )
    scanner.scan_completed.emit(
        BrowserScanCompleted(request.path, request.generation, 1)
    )

    assert not window.list_view.currentIndex().isValid()
    assert window.item_model.row_for_path(kept.path) == 0
    window.close()


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (BrowserScanStatus.NOT_FOUND, "フォルダが見つかりません"),
        (BrowserScanStatus.ACCESS_DENIED, "フォルダへアクセスできません"),
        (BrowserScanStatus.NOT_DIRECTORY, "このファイル形式は表示できません"),
    ],
)
def test_uncommittable_path_failure_preserves_browser_state(
    tmp_path: Path,
    qapp: QApplication,
    status: BrowserScanStatus,
    message: str,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    current = window.current_path
    original_items = window.items
    original_history = len(window.navigation_history)
    original_last_path = window.config.get("last_browser_path")
    selected = tmp_path / "initial" / "selected.jpg"
    selected_item = browser_item_from_scan_entry(entry(selected))
    window.item_model.set_items([selected_item])
    window.list_view.setCurrentIndex(window.item_model.index(0, 0))
    target = tmp_path / f"failed-{status.value}"
    assert window.navigate_to(target)
    request = scanner.requests[-1]

    scanner.scan_failed.emit(
        BrowserScanError(request.path, request.generation, status, "failed")
    )

    assert window.current_path == current
    assert window.items == (selected_item,)
    assert window.list_view.currentIndex().row() == 0
    assert len(window.navigation_history) == original_history
    assert window.config.get("last_browser_path") == original_last_path
    assert window.statusBar().currentMessage() == message
    window.close()


def test_empty_folder_commits_only_after_successful_completion(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    target = tmp_path / "empty"
    assert window.navigate_to(target)
    request = scanner.requests[-1]

    assert window.current_path != target.absolute()
    complete_empty(scanner, request)

    assert window.current_path == target.absolute()
    assert window.items == ()
    assert window.config.get("last_browser_path") == str(target.absolute())
    window.close()


def test_late_error_from_superseded_generation_is_ignored(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    first = tmp_path / "A"
    second = tmp_path / "B"
    assert window.navigate_to(first)
    first_request = scanner.requests[-1]
    assert window.navigate_to(second)
    second_request = scanner.requests[-1]
    complete_empty(scanner, second_request)
    status_before = window.statusBar().currentMessage()

    scanner.scan_failed.emit(
        BrowserScanError(
            first_request.path,
            first_request.generation,
            BrowserScanStatus.ACCESS_DENIED,
            "late",
        )
    )

    assert window.current_path == second.absolute()
    assert window.statusBar().currentMessage() == status_before
    window.close()


def test_cancelled_scan_is_not_reported_as_an_error(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    target = tmp_path / "cancelled"
    assert window.navigate_to(target)
    request = scanner.requests[-1]

    scanner.scan_completed.emit(
        BrowserScanCompleted(
            request.path,
            request.generation,
            0,
            cancelled=True,
        )
    )

    assert "アクセスできません" not in window.statusBar().currentMessage()
    assert "見つかりません" not in window.statusBar().currentMessage()
    window.close()


def test_selection_restore_waits_for_later_batch_without_opening_viewer(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    opened: list[str] = []
    initial = tmp_path / "initial"
    target = tmp_path / "target"
    initial.mkdir()
    target.mkdir()
    scanner = FakeScanner()
    window = BrowserWindow(
        config_manager=make_config(tmp_path, initial),
        scanner=scanner,  # type: ignore[arg-type]
        open_path_handler=lambda path, _new: opened.append(path),
    )
    complete_empty(scanner, scanner.requests[0])
    selected = target / "selected.jpg"
    selected.write_bytes(b"image")
    window.select_path(selected)
    request = scanner.requests[-1]

    scanner.batch_ready.emit(
        BrowserScanBatch(
            request.path,
            request.generation,
            (entry(target / "other.jpg"),),
        )
    )
    assert not window.list_view.currentIndex().isValid()

    scanner.batch_ready.emit(
        BrowserScanBatch(
            request.path,
            request.generation,
            (entry(selected),),
        )
    )
    assert not window.list_view.currentIndex().isValid()
    scanner.scan_completed.emit(
        BrowserScanCompleted(request.path, request.generation, 2)
    )

    current = window.item_model.item_at(window.list_view.currentIndex())
    assert current is not None and current.path == selected.absolute()
    assert opened == []
    window.close()


def test_large_model_requests_only_visible_and_limited_prefetch(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    provider = RecordingThumbnailProvider()
    window, scanner = make_committed_window(tmp_path, qapp, provider=provider)
    target = tmp_path / "large"
    target.mkdir()
    assert window.navigate_to(target)
    request = scanner.requests[-1]
    entries = tuple(
        entry(target / f"book{index}.jpg")
        for index in range(10_000)
    )
    scanner.batch_ready.emit(
        BrowserScanBatch(request.path, request.generation, entries)
    )
    scanner.scan_completed.emit(
        BrowserScanCompleted(request.path, request.generation, len(entries))
    )
    provider.requests.clear()

    window._request_visible_thumbnails()

    assert 0 < len(provider.requests) < 500
    priorities = {request[3] for request in provider.requests}
    assert ThumbnailPriority.VISIBLE in priorities
    assert ThumbnailPriority.PREFETCH in priorities
    window.close()


def test_fast_scroll_suppresses_prefetch_and_idle_resumes_it(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    provider = RecordingThumbnailProvider()
    window, scanner = make_committed_window(tmp_path, qapp, provider=provider)
    target = tmp_path / "scroll"
    target.mkdir()
    assert window.navigate_to(target)
    request = scanner.requests[-1]
    scanner.batch_ready.emit(
        BrowserScanBatch(
            request.path,
            request.generation,
            tuple(entry(target / f"{index}.jpg") for index in range(200)),
        )
    )
    scanner.scan_completed.emit(
        BrowserScanCompleted(request.path, request.generation, 200)
    )
    window._flush_pending_scan_batch()

    window._on_list_scrolled(1000)
    provider.requests.clear()
    window._request_visible_thumbnails()
    assert all(
        item[3] is not ThumbnailPriority.PREFETCH
        for item in provider.requests
    )
    assert provider.cancel_calls

    provider.requests.clear()
    window._on_scroll_idle()
    window._request_visible_thumbnails()
    assert any(
        item[3] is ThumbnailPriority.PREFETCH
        for item in provider.requests
    )
    window.close()


def test_close_ignores_late_scan_results(tmp_path: Path, qapp: QApplication) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    target = tmp_path / "late"
    target.mkdir()
    assert window.navigate_to(target)
    request = scanner.requests[-1]

    window.close()
    scanner.batch_ready.emit(
        BrowserScanBatch(
            request.path,
            request.generation,
            (entry(target / "late.jpg"),),
        )
    )
    qapp.processEvents()

    assert scanner.closed


def test_close_discards_private_scan_buffers(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, scanner = make_committed_window(tmp_path, qapp)
    target = tmp_path / "buffered"
    target.mkdir()
    assert window.navigate_to(target)
    request = scanner.requests[-1]
    scanner.batch_ready.emit(
        BrowserScanBatch(
            request.path,
            request.generation,
            (entry(target / "buffered.jpg"),),
        )
    )
    pending = window._pending_scan
    assert pending is not None and pending.buffered_entries

    window.close()

    assert window._pending_scan is None
    assert pending.buffered_entries == []
    assert pending.refresh_entries == []
    assert pending.remaining_items == []


def test_initial_scan_count_is_bounded_by_viewport_prefetch_plan(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window, _scanner = make_committed_window(tmp_path, qapp)

    assert window._initial_scan_item_count(0) == 0
    assert window._initial_scan_item_count(20) == 20
    assert 1 <= window._initial_scan_item_count(200) <= 80
    assert 1 <= window._initial_scan_item_count(1000) <= 80
    window.close()


def test_initial_stable_item_can_open_with_complete_final_folder_snapshot(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    opened: list[tuple[str, object]] = []
    initial = tmp_path / "initial"
    target = tmp_path / "target"
    initial.mkdir()
    target.mkdir()
    scanner = FakeScanner()
    window = BrowserWindow(
        config_manager=make_config(tmp_path, initial),
        scanner=scanner,  # type: ignore[arg-type]
        open_path_handler=lambda path, _new, snapshot=None: opened.append(
            (path, snapshot)
        ),
    )
    complete_empty(scanner, scanner.requests[0])
    monkeypatch.setattr(
        window,
        "_initial_scan_item_count",
        lambda count: min(count, 10),
    )
    assert window.navigate_to(target)
    request = scanner.requests[-1]
    entries = tuple(
        entry(target / f"{index:03}.jpg")
        for index in range(100)
    )
    scanner.batch_ready.emit(
        BrowserScanBatch(request.path, request.generation, entries)
    )
    scanner.scan_completed.emit(
        BrowserScanCompleted(request.path, request.generation, 100)
    )

    assert window.item_model.rowCount() == 10
    window.open_item(window.item_model.index(0, 0))

    assert len(opened) == 1
    assert opened[0][0].endswith("000.jpg")
    snapshot = opened[0][1]
    assert snapshot is not None
    assert len(snapshot.image_ids) == 100
    window.close()


def test_close_does_not_wait_for_running_scanner(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "slow-close"
    folder.mkdir()
    started = Event()
    release = Event()

    def blocking_scan(request, cancelled, emit_batch):
        started.set()
        release.wait(2)
        return BrowserScanCompleted(
            request.path,
            request.generation,
            0,
            cancelled=cancelled.is_set(),
        )

    monkeypatch.setattr("app.browser_scanner.scan_directory", blocking_scan)
    scanner = BrowserDirectoryScanner(max_workers=1)
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        scanner=scanner,
    )
    assert started.wait(1)

    before = monotonic()
    window.close()
    elapsed = monotonic() - before

    assert elapsed < 0.5
    release.set()
    assert scanner.wait_for_done(2000)
    qapp.processEvents()
