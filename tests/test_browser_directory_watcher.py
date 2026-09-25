from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject, QModelIndex, Signal
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListView

from app.browser_directory_watcher import BrowserDirectoryChange
from app.browser_filter import BrowserFilterState
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.file_operation_service import (
    FileOperationItemResult,
    FileOperationKind,
    FileOperationRequest,
    FileOperationResult,
)
from app.thumbnail_provider import BrowserThumbnailProvider


class FakeDirectoryWatcher(QObject):
    directory_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.path: str | None = None
        self.generation = 0
        self.watch_calls: list[tuple[str, int]] = []
        self.clear_count = 0

    def watch(self, path: str, generation: int) -> bool:
        self.path = os.path.abspath(os.path.normpath(path))
        self.generation = int(generation)
        self.watch_calls.append((self.path, self.generation))
        return True

    def clear(self) -> None:
        self.path = None
        self.generation = 0
        self.clear_count += 1

    def close(self) -> None:
        self.clear()

    def notify(
        self,
        *,
        path: str | None = None,
        generation: int | None = None,
    ) -> None:
        self.directory_changed.emit(
            BrowserDirectoryChange(
                path or self.path or "",
                self.generation if generation is None else generation,
            )
        )


class FakeFileOperationCoordinator(QObject):
    operation_started = Signal(object)
    operation_progress = Signal(object)
    operation_completed = Signal(object)
    conflicts_required = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.busy = False
        self.queue = None

    def execute(self, _request) -> bool:
        return False

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        pass


class NoopThumbnailProvider(BrowserThumbnailProvider):
    def __init__(self) -> None:
        super().__init__(disk_cache_enabled=False)
        self.requests: list[str] = []

    def request(self, *_args, **_kwargs) -> bool:
        if _args:
            self.requests.append(str(_args[0].path))
        return False


def write_item(path: Path, size: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def make_window(
    tmp_path: Path,
    folder: Path,
    qapp: QApplication,
    *,
    watcher: FakeDirectoryWatcher | None = None,
    coordinator: FakeFileOperationCoordinator | None = None,
) -> tuple[BrowserWindow, FakeDirectoryWatcher]:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    source = watcher or FakeDirectoryWatcher()
    window = BrowserWindow(
        config_manager=config,
        directory_watcher=source,  # type: ignore[arg-type]
        file_operation_coordinator=coordinator,  # type: ignore[arg-type]
        thumbnail_provider=NoopThumbnailProvider(),
    )
    window.resize(500, 360)
    window.show()
    assert window.wait_for_scan()
    qapp.processEvents()
    assert source.path == str(folder.absolute())
    return window, source


def reconcile(
    window: BrowserWindow,
    watcher: FakeDirectoryWatcher,
    qapp: QApplication,
) -> None:
    watcher.notify()
    assert window._directory_change_timer.isActive()
    window._flush_directory_changes()
    assert window.wait_for_scan()
    qapp.processEvents()


def wait_for_scan_chain(window: BrowserWindow, qapp: QApplication) -> None:
    for _ in range(4):
        if window.wait_for_scan():
            return
        qapp.processEvents()
    assert window.wait_for_scan()


def test_external_create_and_delete_reconcile_automatically(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    existing = folder / "existing.jpg"
    created = folder / "created.jpg"
    write_item(existing)
    window, watcher = make_window(tmp_path, folder, qapp)

    try:
        write_item(created)
        reconcile(window, watcher, qapp)
        assert window.item_model.row_for_path(created) >= 0

        existing.unlink()
        reconcile(window, watcher, qapp)
        assert window.item_model.row_for_path(existing) < 0
        assert window.item_model.row_for_path(created) >= 0
    finally:
        window.close()
        qapp.processEvents()


def test_same_directory_watcher_reconcile_preserves_active_search(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    existing = folder / "keep.jpg"
    created = folder / "keep-new.jpg"
    write_item(existing)
    window, watcher = make_window(tmp_path, folder, qapp)
    window.browser_search_edit.setText("keep")
    window._browser_search_timer.stop()
    window._apply_pending_browser_search()

    try:
        write_item(created)
        reconcile(window, watcher, qapp)

        assert window.browser_filter_state.search_text == "keep"
        assert window.browser_search_edit.text() == "keep"
        assert window.item_model.row_for_path(created) >= 0
    finally:
        window.close()
        qapp.processEvents()


def test_real_qt_directory_watcher_detects_external_create(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    config = ConfigManager(tmp_path / "real-watcher-config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    window = BrowserWindow(
        config_manager=config,
        thumbnail_provider=NoopThumbnailProvider(),
    )
    window.show()
    assert window.wait_for_scan()
    created = folder / "created.jpg"

    try:
        write_item(created)
        for _ in range(100):
            qapp.processEvents()
            if window._directory_change_timer.isActive():
                break
            QTest.qWait(5)
        assert window._directory_change_timer.isActive()
        window._flush_directory_changes()
        assert window.wait_for_scan()
        qapp.processEvents()
        assert window.item_model.row_for_path(created) >= 0

        write_item(created, 25)
        for _ in range(100):
            qapp.processEvents()
            if window._directory_change_timer.isActive():
                break
            QTest.qWait(5)
        assert window._directory_change_timer.isActive()
        window._flush_directory_changes()
        assert window.wait_for_scan()
        qapp.processEvents()
        changed = window.item_model.item_at(
            window.item_model.row_for_path(created)
        )
        assert changed is not None and changed.file_size == 25
    finally:
        window.close()
        qapp.processEvents()


def test_external_rename_removes_stale_selection_and_history_path(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    old_path = folder / "old.jpg"
    new_path = folder / "new.jpg"
    write_item(old_path)
    window, watcher = make_window(tmp_path, folder, qapp)
    old_index = window.item_model.index(
        window.item_model.row_for_path(old_path),
        0,
    )
    window.list_view.setCurrentIndex(old_index)
    window._update_current_navigation_state()

    try:
        old_path.rename(new_path)
        reconcile(window, watcher, qapp)

        assert window.item_model.row_for_path(old_path) < 0
        assert window.item_model.row_for_path(new_path) >= 0
        assert not window.list_view.currentIndex().isValid()
        current = window.navigation_history.current()
        assert current is not None and current.selected_path is None
    finally:
        window.close()
        qapp.processEvents()


def test_external_modification_updates_metadata_and_only_drops_changed_thumbnail(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    changed = folder / "changed.jpg"
    unchanged = folder / "unchanged.jpg"
    write_item(changed, 2)
    write_item(unchanged, 3)
    window, watcher = make_window(tmp_path, folder, qapp)
    changed_before = window.item_model.item_at(
        window.item_model.row_for_path(changed)
    )
    unchanged_before = window.item_model.item_at(
        window.item_model.row_for_path(unchanged)
    )
    assert changed_before is not None and unchanged_before is not None
    image = QImage(8, 8, QImage.Format.Format_ARGB32)
    image.fill(0xFF336699)
    token = window.thumbnail_render_spec.cache_token
    assert window.item_model.set_thumbnail_image(
        changed,
        image,
        request_token=token,
    )
    assert window.item_model.set_thumbnail_image(
        unchanged,
        image,
        request_token=token,
    )

    try:
        write_item(changed, 20)
        next_mtime = int(changed_before.modified_time_ns or 0) + 2_000_000_000
        os.utime(changed, ns=(next_mtime, next_mtime))
        reconcile(window, watcher, qapp)

        changed_after = window.item_model.item_at(
            window.item_model.row_for_path(changed)
        )
        unchanged_after = window.item_model.item_at(
            window.item_model.row_for_path(unchanged)
        )
        assert changed_after is not None and changed_after.file_size == 20
        assert changed_after.modified_time_ns == next_mtime
        assert unchanged_after is not None
        assert unchanged_after.file_size == unchanged_before.file_size
        changed_image = window.item_model.index(
            window.item_model.row_for_path(changed),
            0,
        ).data(BrowserItemModel.ThumbnailImageRole)
        unchanged_image = window.item_model.index(
            window.item_model.row_for_path(unchanged),
            0,
        ).data(BrowserItemModel.ThumbnailImageRole)
        assert changed_image is None
        assert unchanged_image is not None and not unchanged_image.isNull()
    finally:
        window.close()
        qapp.processEvents()


def test_tag_rename_watch_reconciles_without_duplicate_reset_and_sees_external_add(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    old = folder / "book.png"
    write_item(old)
    window, watcher = make_window(tmp_path, folder, qapp)
    resets: list[None] = []
    window.item_model.modelReset.connect(lambda: resets.append(None))
    generation = window.thumbnail_provider.generation
    try:
        assert window.set_rating_for_paths((str(old),), None, tag_changes={"Tagged": True})
        renamed = folder / "book {zpi$t=Tagged}.png"
        assert renamed.exists() and window.item_model.row_for_path(renamed) >= 0
        assert not resets
        watcher.notify()
        window._flush_directory_changes()
        assert window.wait_for_scan()
        qapp.processEvents()
        assert not resets
        assert window.thumbnail_provider.generation == generation

        added = folder / "external.png"
        write_item(added)
        watcher.notify()
        window._flush_directory_changes()
        assert window.wait_for_scan()
        qapp.processEvents()
        assert resets and window.item_model.row_for_path(added) >= 0
    finally:
        window.close()
        qapp.processEvents()


def test_unchanged_watch_retries_failed_thumbnail_without_model_reset(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    item = folder / "book.png"
    write_item(item)
    window, watcher = make_window(tmp_path, folder, qapp)
    resets: list[None] = []
    window.item_model.modelReset.connect(lambda: resets.append(None))
    generation = window.thumbnail_provider.generation
    window.thumbnail_provider._failed.add((str(item), 0, ()))
    try:
        watcher.notify()
        window._flush_directory_changes()
        assert window.wait_for_scan()
        assert not resets
        assert window.thumbnail_provider.generation == generation + 1
        assert not window.thumbnail_provider.has_failed_requests
    finally:
        window.close()
        qapp.processEvents()


def test_same_listing_filesystem_watch_keeps_video_shell_thumbnail(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    video = folder / "movie.mp4"
    write_item(video)
    provider = NoopThumbnailProvider()
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    watcher = FakeDirectoryWatcher()
    window = BrowserWindow(
        config_manager=config,
        directory_watcher=watcher,  # type: ignore[arg-type]
        thumbnail_provider=provider,
    )
    window.show()
    assert window.wait_for_scan()
    qapp.processEvents()
    item = window.item_model.item_at(0)
    assert item is not None and item.preview_kind == "video"
    image = QImage(32, 32, QImage.Format.Format_RGBA8888)
    image.fill(0xFF224466)
    assert window.item_model.set_thumbnail_image(
        video,
        image,
        request_token=window.thumbnail_render_spec.cache_token,
    )
    provider.requests.clear()

    watcher.notify()
    window._flush_directory_changes()
    assert window.wait_for_scan()
    qapp.processEvents()

    stored = window.item_model.data(
        window.item_model.index(0, 0),
        window.item_model.ThumbnailImageRole,
    )
    assert stored is not None and not stored.isNull()
    assert provider.requests == []
    window.close()
    qapp.processEvents()


def test_burst_is_coalesced_and_idle_does_not_start_scans(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    write_item(folder / "existing.jpg")
    window, watcher = make_window(tmp_path, folder, qapp)

    try:
        with patch.object(
            window.scanner,
            "start",
            wraps=window.scanner.start,
        ) as start:
            for number in range(50):
                write_item(folder / f"new-{number:03}.jpg")
                watcher.notify()
            assert start.call_count == 0
            assert window._directory_change_timer.isActive()

            window._flush_directory_changes()
            assert start.call_count == 1
            assert window.wait_for_scan()
            qapp.processEvents()
            assert len(window.items) == 51

            for _ in range(10):
                qapp.processEvents()
            assert start.call_count == 1
            assert not window._directory_change_timer.isActive()
    finally:
        window.close()
        qapp.processEvents()


def test_large_synthetic_model_has_zero_idle_work_and_one_event_reconciliation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    window, watcher = make_window(tmp_path, folder, qapp)
    window.item_model.set_sorted_items(
        tuple(
            BrowserItem(
                f"item-{number:05}.jpg",
                folder / f"item-{number:05}.jpg",
                BrowserItemKind.IMAGE,
                None,
            )
            for number in range(10_000)
        )
    )

    try:
        with patch.object(
            window.scanner,
            "start",
            wraps=window.scanner.start,
        ) as start:
            for _ in range(20):
                qapp.processEvents()
            assert start.call_count == 0
            assert not window._directory_change_timer.isActive()

            for _ in range(200):
                watcher.notify()
            window._flush_directory_changes()
            assert start.call_count == 1
            assert window.wait_for_scan()
            qapp.processEvents()
            assert start.call_count == 1
    finally:
        window.close()
        qapp.processEvents()


def test_late_event_from_old_directory_generation_is_rejected(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_item(first / "first.jpg")
    write_item(second / "second.jpg")
    window, watcher = make_window(tmp_path, first, qapp)
    old_path = watcher.path
    old_generation = watcher.generation

    try:
        assert window.navigate_to(second)
        assert watcher.path == str(second.absolute())
        watcher.notify(path=old_path, generation=old_generation)
        assert not window._directory_change_pending
        assert not window._directory_change_timer.isActive()
        pending = window._pending_scan
        assert pending is not None and not pending.directory_watch_dirty
        assert window.wait_for_scan()
        qapp.processEvents()
        assert window.current_path == second.absolute()
        assert [item.path for item in window.items] == [second / "second.jpg"]
    finally:
        window.close()
        qapp.processEvents()


def test_watcher_event_during_back_keeps_atomic_first_paint(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    for number in range(140):
        write_item(parent / f"item-{number:03}.jpg")
    window, watcher = make_window(tmp_path, parent, qapp)
    selected = parent / "item-120.jpg"
    selected_index = window.item_model.index(
        window.item_model.row_for_path(selected),
        0,
    )
    window.list_view.setCurrentIndex(selected_index)
    window.list_view.scrollTo(
        selected_index,
        QListView.ScrollHint.PositionAtCenter,
    )
    qapp.processEvents()
    child_index = window.item_model.index(
        window.item_model.row_for_path(child),
        0,
    )
    window.open_item(child_index)
    assert window.wait_for_scan()
    qapp.processEvents()
    first_paints: list[str | None] = []
    window.list_view.paintCompleted.connect(
        lambda: first_paints.append(
            str(item.path)
            if (
                item := window.item_model.item_at(
                    window.list_view.currentIndex()
                )
            )
            is not None
            else None
        )
    )

    try:
        assert window.go_back()
        watcher.notify()
        pending = window._pending_scan
        assert pending is not None and pending.directory_watch_dirty
        assert window.wait_for_scan()
        qapp.processEvents()
        assert first_paints and first_paints[0] == str(selected.absolute())
        current = window.item_model.item_at(window.list_view.currentIndex())
        assert current is not None and current.path == selected.absolute()
        assert window._directory_change_timer.isActive()

        window._flush_directory_changes()
        assert window.wait_for_scan()
        qapp.processEvents()
        current = window.item_model.item_at(window.list_view.currentIndex())
        assert current is not None and current.path == selected.absolute()
    finally:
        window.close()
        qapp.processEvents()


def test_deleted_active_directory_recovers_to_existing_parent(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    item = folder / "item.jpg"
    write_item(item)
    window, watcher = make_window(tmp_path, folder, qapp)

    try:
        item.unlink()
        folder.rmdir()
        watcher.notify()
        window._flush_directory_changes()
        wait_for_scan_chain(window, qapp)
        qapp.processEvents()
        assert window.current_path == tmp_path.absolute()
        assert window._directory_watch_path == tmp_path.absolute()
    finally:
        window.close()
        qapp.processEvents()


def test_externally_renamed_active_directory_recovers_to_parent(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    moved = tmp_path / "renamed-folder"
    write_item(folder / "item.jpg")
    window, watcher = make_window(tmp_path, folder, qapp)

    try:
        folder.rename(moved)
        watcher.notify()
        window._flush_directory_changes()
        wait_for_scan_chain(window, qapp)
        qapp.processEvents()
        assert window.current_path == tmp_path.absolute()
        assert window._directory_watch_path == tmp_path.absolute()
        assert window.item_model.row_for_path(moved) >= 0
    finally:
        window.close()
        qapp.processEvents()


def test_unselected_viewport_anchor_survives_unrelated_insert(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    for number in range(180):
        write_item(folder / f"item-{number:03}.jpg")
    window, watcher = make_window(tmp_path, folder, qapp)
    target = folder / "item-140.jpg"
    index = window.item_model.index(window.item_model.row_for_path(target), 0)
    window.list_view.scrollTo(index, QListView.ScrollHint.PositionAtTop)
    window.list_view.clearSelection()
    window.list_view.setCurrentIndex(QModelIndex())
    qapp.processEvents()
    anchor_index = window._visible_anchor_index()
    anchor_item = window.item_model.item_at(anchor_index)
    assert anchor_item is not None
    anchor_path = anchor_item.path
    anchor_y = window.list_view.visualRect(anchor_index).y()

    try:
        write_item(folder / "000-new.jpg")
        reconcile(window, watcher, qapp)
        restored_row = window.item_model.row_for_path(anchor_path)
        restored = window.item_model.index(restored_row, 0)
        assert restored_row >= 0
        assert window.list_view.visualRect(restored).y() == anchor_y
        assert not window.list_view.currentIndex().isValid()
    finally:
        window.close()
        qapp.processEvents()


def test_far_down_selection_and_viewport_survive_unrelated_insert(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    for number in range(180):
        write_item(folder / f"item-{number:03}.jpg")
    selected = folder / "item-150.jpg"
    window, watcher = make_window(tmp_path, folder, qapp)
    selected_index = window.item_model.index(
        window.item_model.row_for_path(selected),
        0,
    )
    window.list_view.setCurrentIndex(selected_index)
    window.list_view.scrollTo(
        selected_index,
        QListView.ScrollHint.PositionAtCenter,
    )
    qapp.processEvents()
    selected_y = window.list_view.visualRect(selected_index).y()

    try:
        write_item(folder / "000-new.jpg")
        reconcile(window, watcher, qapp)
        current_index = window.list_view.currentIndex()
        current = window.item_model.item_at(current_index)
        assert current is not None and current.path == selected.absolute()
        current_rect = window.list_view.visualRect(current_index)
        assert window.list_view.viewport().rect().contains(current_rect)
        assert abs(current_rect.y() - selected_y) <= (
            window.list_view.gridSize().height()
        )
        assert window.list_view.verticalScrollBar().value() > 0
    finally:
        window.close()
        qapp.processEvents()


def test_far_down_selection_stays_visible_after_unrelated_delete(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    for number in range(180):
        write_item(folder / f"item-{number:03}.jpg")
    selected = folder / "item-150.jpg"
    unrelated = folder / "item-010.jpg"
    window, watcher = make_window(tmp_path, folder, qapp)
    selected_index = window.item_model.index(
        window.item_model.row_for_path(selected),
        0,
    )
    window.list_view.setCurrentIndex(selected_index)
    window.list_view.scrollTo(
        selected_index,
        QListView.ScrollHint.PositionAtCenter,
    )
    qapp.processEvents()

    try:
        unrelated.unlink()
        reconcile(window, watcher, qapp)
        current_index = window.list_view.currentIndex()
        current = window.item_model.item_at(current_index)
        assert current is not None and current.path == selected.absolute()
        assert window.list_view.viewport().rect().contains(
            window.list_view.visualRect(current_index)
        )
        assert window.list_view.verticalScrollBar().value() > 0
    finally:
        window.close()
        qapp.processEvents()


def test_selected_item_deletion_does_not_select_same_row_replacement(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    for number in range(20):
        write_item(folder / f"item-{number:03}.jpg")
    selected = folder / "item-010.jpg"
    window, watcher = make_window(tmp_path, folder, qapp)
    window.list_view.setCurrentIndex(
        window.item_model.index(window.item_model.row_for_path(selected), 0)
    )

    try:
        selected.unlink()
        reconcile(window, watcher, qapp)
        assert window.item_model.row_for_path(selected) < 0
        assert not window.list_view.currentIndex().isValid()
        assert not window.list_view.selectionModel().selectedIndexes()
    finally:
        window.close()
        qapp.processEvents()


def test_external_name_and_size_changes_reenter_filter_and_sort_pipeline(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    hidden = folder / "other.jpg"
    small = folder / "keep-small.jpg"
    large = folder / "keep-large.jpg"
    write_item(hidden, 4)
    write_item(small, 2)
    write_item(large, 8)
    window, watcher = make_window(tmp_path, folder, qapp)
    window.config.apply(
        {
            "browser_sort_key": "file_size",
            "browser_sort_order": "ascending",
        }
    )
    window._set_browser_filter(BrowserFilterState.normalized(search_text="keep"))
    qapp.processEvents()
    renamed = folder / "keep-other.jpg"

    try:
        hidden.rename(renamed)
        reconcile(window, watcher, qapp)
        assert [item.path for item in window.items] == [small, renamed, large]

        write_item(small, 12)
        reconcile(window, watcher, qapp)
        assert [item.path for item in window.items] == [renamed, large, small]

        renamed.rename(folder / "other-again.jpg")
        reconcile(window, watcher, qapp)
        assert [item.path for item in window.items] == [large, small]
    finally:
        window.close()
        qapp.processEvents()


def test_own_rename_relocates_once_and_absorbs_deferred_watcher_burst(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    old_path = folder / "old.jpg"
    new_path = folder / "new.jpg"
    write_item(old_path)
    coordinator = FakeFileOperationCoordinator()
    window, watcher = make_window(
        tmp_path,
        folder,
        qapp,
        coordinator=coordinator,
    )
    request = FileOperationRequest(
        request_id=71,
        operation=FileOperationKind.RENAME,
        source_paths=(str(old_path),),
        new_name=new_path.name,
    )
    result = FileOperationResult(
        operation=FileOperationKind.RENAME,
        request_id=71,
        items=(
            FileOperationItemResult(
                source_path=str(old_path),
                destination_path=str(new_path),
                success=True,
                destination_published=True,
                source_removed=True,
            ),
        ),
    )
    window._file_operation_requests[71] = request
    window._file_operation_selection_before[71] = ((), None)
    window._active_file_operation_id = 71
    coordinator.busy = True

    try:
        with patch.object(
            window.scanner,
            "start",
            wraps=window.scanner.start,
        ) as start, patch.object(
            window.navigation_history,
            "relocate_tree",
            wraps=window.navigation_history.relocate_tree,
        ) as relocate:
            old_path.rename(new_path)
            watcher.notify()
            window._flush_directory_changes()
            assert start.call_count == 0
            assert window._directory_change_pending

            coordinator.busy = False
            coordinator.operation_completed.emit(result)
            assert relocate.call_count == 1
            assert start.call_count == 1
            assert not window._directory_change_pending
            assert window.wait_for_scan()
            qapp.processEvents()
            assert window.item_model.row_for_path(old_path) < 0
            assert window.item_model.row_for_path(new_path) >= 0
            assert start.call_count == 1
    finally:
        window.close()
        qapp.processEvents()


def test_own_recycle_absorbs_watcher_event_into_one_authoritative_refresh(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    deleted_path = folder / "deleted.jpg"
    remaining_path = folder / "remaining.jpg"
    write_item(deleted_path)
    write_item(remaining_path)
    coordinator = FakeFileOperationCoordinator()
    window, watcher = make_window(
        tmp_path,
        folder,
        qapp,
        coordinator=coordinator,
    )
    request = FileOperationRequest(
        request_id=72,
        operation=FileOperationKind.RECYCLE,
        source_paths=(str(deleted_path),),
    )
    result = FileOperationResult(
        operation=FileOperationKind.RECYCLE,
        request_id=72,
        items=(
            FileOperationItemResult(
                source_path=str(deleted_path),
                destination_path=None,
                success=True,
                source_removed=True,
            ),
        ),
    )
    window._file_operation_requests[72] = request
    window._file_operation_selection_before[72] = (
        (str(deleted_path),),
        window.item_model.row_for_path(deleted_path),
    )
    window._active_file_operation_id = 72
    coordinator.busy = True

    try:
        with patch.object(
            window.scanner,
            "start",
            wraps=window.scanner.start,
        ) as start:
            deleted_path.unlink()
            watcher.notify()
            window._flush_directory_changes()
            assert start.call_count == 0
            assert window._directory_change_pending

            coordinator.busy = False
            coordinator.operation_completed.emit(result)
            assert start.call_count == 1
            assert not window._directory_change_pending
            assert window.wait_for_scan()
            qapp.processEvents()
            assert window.item_model.row_for_path(deleted_path) < 0
            assert window.item_model.row_for_path(remaining_path) >= 0
            assert start.call_count == 1
    finally:
        window.close()
        qapp.processEvents()
