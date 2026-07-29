from __future__ import annotations

import os
from pathlib import Path
from threading import Event

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from app.browser_model import browser_item_from_scan_entry
from app.browser_scanner import (
    BrowserDirectoryScanner,
    BrowserScanCompleted,
    BrowserScanError,
    BrowserScanRequest,
    BrowserScanStatus,
    scan_directory,
    scan_entry_from_dir_entry,
)
from app.browser_sort import BrowserSortKey, BrowserSortOrder, BrowserSortPolicy


def write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (8, 12), "white") as image:
        image.save(path)


def test_scanner_emits_batches_with_generation_and_supported_kinds(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "日本語"
    child = folder / "子"
    child.mkdir(parents=True)
    write_image(folder / "1.jpg")
    (folder / "book.zip").write_bytes(b"zip")
    (folder / "book.cbz").write_bytes(b"cbz")
    (folder / "note.txt").write_text("ignore", encoding="utf-8")
    batches = []
    request = BrowserScanRequest(str(folder), generation=17, batch_size=2)

    result = scan_directory(request, Event(), batches.append)

    assert isinstance(result, BrowserScanCompleted)
    assert result.generation == 17
    assert result.status is BrowserScanStatus.NORMAL_DIRECTORY
    assert result.total_count == 4
    assert [len(batch.entries) for batch in batches] == [2, 2]
    assert all(batch.generation == 17 for batch in batches)
    assert {entry.item_kind for batch in batches for entry in batch.entries} == {
        "folder",
        "image",
        "archive",
    }
    assert all(Path(entry.path).is_absolute() for batch in batches for entry in batch.entries)


@pytest.mark.parametrize(
    "sort_policy",
    [
        BrowserSortPolicy(),
        BrowserSortPolicy(
            BrowserSortKey.NAME,
            BrowserSortOrder.DESCENDING,
            folders_first=False,
        ),
        BrowserSortPolicy(
            BrowserSortKey.MODIFIED_TIME,
            BrowserSortOrder.ASCENDING,
            folders_first=True,
        ),
        BrowserSortPolicy(
            BrowserSortKey.ITEM_TYPE,
            BrowserSortOrder.DESCENDING,
            folders_first=False,
        ),
        BrowserSortPolicy(
            BrowserSortKey.FILE_SIZE,
            BrowserSortOrder.DESCENDING,
            folders_first=True,
        ),
    ],
)
def test_scan_completion_prepares_exact_existing_sort_policy_order(
    tmp_path: Path,
    sort_policy: BrowserSortPolicy,
) -> None:
    (tmp_path / "章10").mkdir()
    (tmp_path / "章2").mkdir()
    for name, payload in (
        ("Book2.jpg", b"22"),
        ("book10.JPG", b"1" * 10),
        ("日本語3.png", b"333"),
        ("日本語12.png", b"1" * 12),
        ("同名2.cbz", b"cbz"),
        ("同名10.zip", b"archive"),
        ("note.txt", b"text"),
    ):
        (tmp_path / name).write_bytes(payload)
    batches = []
    request = BrowserScanRequest(
        str(tmp_path),
        generation=18,
        batch_size=3,
        sort_policy=sort_policy,
    )

    result = scan_directory(request, Event(), batches.append)

    assert isinstance(result, BrowserScanCompleted)
    assert result.sort_policy == sort_policy
    assert result.prepared_items is not None
    assert all(batch.final_items_pending for batch in batches)
    scanned_items = [
        browser_item_from_scan_entry(entry)
        for batch in batches
        for entry in batch.entries
    ]
    assert result.prepared_items == tuple(
        sort_policy.sorted_items(scanned_items)
    )


def test_empty_folder_completes_without_batches(tmp_path: Path) -> None:
    batches = []

    result = scan_directory(
        BrowserScanRequest(str(tmp_path), generation=3),
        Event(),
        batches.append,
    )

    assert result == BrowserScanCompleted(str(tmp_path), 3, 0)
    assert result.status is BrowserScanStatus.EMPTY_DIRECTORY
    assert result.prepared_items == ()
    assert result.sort_policy == BrowserSortPolicy()
    assert batches == []


def test_count_only_progress_batches_do_not_retain_scan_entries(
    tmp_path: Path,
) -> None:
    for index in range(5):
        (tmp_path / f"長い日本語名{index}.jpg").write_bytes(
            bytes([index]),
        )
    batches = []

    result = scan_directory(
        BrowserScanRequest(
            str(tmp_path),
            generation=4,
            batch_size=2,
            include_progress_entries=False,
        ),
        Event(),
        batches.append,
    )

    assert isinstance(result, BrowserScanCompleted)
    assert result.total_count == 5
    assert result.prepared_items is not None
    assert len(result.prepared_items) == 5
    assert [batch.item_count for batch in batches] == [2, 2, 1]
    assert all(batch.entries == () for batch in batches)


def test_pre_cancelled_scan_is_not_an_error(tmp_path: Path) -> None:
    write_image(tmp_path / "1.jpg")
    cancelled = Event()
    cancelled.set()
    batches = []

    result = scan_directory(
        BrowserScanRequest(str(tmp_path), generation=9),
        cancelled,
        batches.append,
    )

    assert isinstance(result, BrowserScanCompleted)
    assert result.cancelled
    assert result.status is BrowserScanStatus.CANCELLED
    assert result.generation == 9
    assert batches == []


def test_cancel_during_sort_does_not_deliver_prepared_items(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for index in range(3):
        (tmp_path / f"{index}.txt").write_text(str(index), encoding="utf-8")
    cancelled = Event()
    original_sorted_items = BrowserSortPolicy.sorted_items

    def cancel_after_sort(policy, items):
        ordered = original_sorted_items(policy, items)
        cancelled.set()
        return ordered

    monkeypatch.setattr(
        BrowserSortPolicy,
        "sorted_items",
        cancel_after_sort,
    )

    result = scan_directory(
        BrowserScanRequest(str(tmp_path), generation=10),
        cancelled,
        lambda _batch: None,
    )

    assert isinstance(result, BrowserScanCompleted)
    assert result.cancelled
    assert result.prepared_items is None
    assert result.sort_policy is None


def test_inaccessible_folder_returns_explicit_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def blocked(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "scandir", blocked)

    result = scan_directory(
        BrowserScanRequest(str(tmp_path), generation=5),
        Event(),
        lambda _batch: None,
    )

    assert isinstance(result, BrowserScanError)
    assert result.generation == 5
    assert result.status is BrowserScanStatus.ACCESS_DENIED
    assert "フォルダを読み込めません" in result.message


def test_missing_folder_returns_not_found_with_request_identity(
    tmp_path: Path,
) -> None:
    target = tmp_path / "missing"
    request = BrowserScanRequest(str(target), generation=31)

    result = scan_directory(request, Event(), lambda _batch: None)

    assert isinstance(result, BrowserScanError)
    assert result.path == str(target)
    assert result.generation == 31
    assert result.status is BrowserScanStatus.NOT_FOUND


def test_file_path_returns_not_directory_without_batches(
    tmp_path: Path,
) -> None:
    target = tmp_path / "book.cbz"
    target.write_bytes(b"not a directory")
    batches = []

    result = scan_directory(
        BrowserScanRequest(str(target), generation=32),
        Event(),
        batches.append,
    )

    assert isinstance(result, BrowserScanError)
    assert result.status is BrowserScanStatus.NOT_DIRECTORY
    assert result.path == str(target)
    assert result.generation == 32
    assert batches == []


def test_other_io_error_is_classified_separately(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def failed(_path):
        raise OSError("device failure")

    monkeypatch.setattr(os, "scandir", failed)

    result = scan_directory(
        BrowserScanRequest(str(tmp_path), generation=33),
        Event(),
        lambda _batch: None,
    )

    assert isinstance(result, BrowserScanError)
    assert result.status is BrowserScanStatus.IO_ERROR


def test_stat_failure_keeps_supported_entry_with_safe_values(
    tmp_path: Path,
) -> None:
    class StatFailureEntry:
        name = "読めない.jpg"
        path = str(tmp_path / name)

        @staticmethod
        def stat(*, follow_symlinks: bool):
            raise PermissionError("denied")

        @staticmethod
        def is_dir(*, follow_symlinks: bool) -> bool:
            return False

        @staticmethod
        def is_file(*, follow_symlinks: bool) -> bool:
            return True

    entry = scan_entry_from_dir_entry(StatFailureEntry())

    assert entry is not None
    assert entry.display_name == "読めない.jpg"
    assert entry.modified_time_ns is None
    assert entry.file_size is None


def test_directory_scanner_delivers_worker_results_via_qt_signals(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    for index in range(3):
        write_image(tmp_path / f"{index}.jpg")
    scanner = BrowserDirectoryScanner(max_workers=1)
    batches = []
    completed = []
    scanner.batch_ready.connect(batches.append)
    scanner.scan_completed.connect(completed.append)

    assert scanner.start(
        BrowserScanRequest(str(tmp_path), generation=21, batch_size=2)
    )
    assert scanner.wait_for_done(2000)
    qapp.processEvents()

    assert [len(batch.entries) for batch in batches] == [2, 1]
    assert completed == [BrowserScanCompleted(str(tmp_path), 21, 3)]
    scanner.close()


def test_directory_scanner_removes_cancelled_queued_work(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    started = Event()
    release = Event()
    executed: list[int] = []

    def controlled_scan(request, cancelled, emit_batch):
        executed.append(request.generation)
        if request.generation == 1:
            started.set()
            release.wait(2)
        return BrowserScanCompleted(
            request.path,
            request.generation,
            0,
            cancelled=cancelled.is_set(),
        )

    monkeypatch.setattr("app.browser_scanner.scan_directory", controlled_scan)
    scanner = BrowserDirectoryScanner(max_workers=1)
    assert scanner.start(BrowserScanRequest(str(tmp_path), generation=1))
    assert started.wait(1)
    assert scanner.start(BrowserScanRequest(str(tmp_path), generation=2))

    scanner.cancel(2)
    release.set()
    assert scanner.wait_for_done(2000)
    qapp.processEvents()

    assert executed == [1]
    scanner.close()
