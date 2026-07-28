from __future__ import annotations

import errno
import os
from pathlib import Path
from threading import Event
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
from app.file_preview import PreviewResult, PreviewSource
from app.thumbnail_render import ThumbnailRenderSpec
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


def test_first_batch_paints_before_tree_sync_and_thumbnail_request(
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
        window._on_list_paint_completed()
        qapp.processEvents()
        assert calls == ["tree", "thumbnail"]
    finally:
        window.close()
        qapp.processEvents()
        store.close()


@pytest.mark.parametrize("item_count", [20, 200, 1000])
def test_incremental_scan_finish_does_not_reset_or_rerequest_thumbnails(
    tmp_path,
    qapp,
    monkeypatch,
    item_count: int,
) -> None:
    window, store, scanner, folder, _other = _favorite_window(tmp_path, qapp)
    requested: list[str] = []
    committed: list[str] = []
    resets: list[bool] = []
    location_restores: list[bool] = []
    original_restore = window._restore_pending_scan_location

    monkeypatch.setattr(
        window.thumbnail_provider,
        "request",
        lambda item, *_args, **_kwargs: requested.append(str(item.path)) or False,
    )

    def record_restore(pending, *, final: bool) -> None:
        location_restores.append(final)
        original_restore(pending, final=final)

    monkeypatch.setattr(
        window,
        "_restore_pending_scan_location",
        record_restore,
    )
    window.directory_scan_committed.connect(committed.append)
    window.item_model.modelReset.connect(lambda: resets.append(True))
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

        selected = window.item_model.index(min(5, split_at - 1), 0)
        window.list_view.setCurrentIndex(selected)
        selected_path = selected.data(window.item_model.PathRole)
        scroll = window.list_view.verticalScrollBar()
        scroll.setValue(min(3, scroll.maximum()))
        scanner.batch_ready.emit(
            BrowserScanBatch(
                request.path,
                request.generation,
                entries[split_at:],
            )
        )
        window._flush_pending_scan_batch()
        current_after_batch = window.list_view.currentIndex()
        assert current_after_batch.isValid()
        assert (
            current_after_batch.data(window.item_model.PathRole)
            == selected_path
        )
        assert window.item_model.rowCount() == item_count
        scroll.setValue(min(3, scroll.maximum()))
        scroll_before_finish = scroll.value()
        window._first_paint_pending_generation = None
        qapp.processEvents()
        window._thumbnail_request_timer.stop()
        requested.clear()
        window._request_visible_thumbnails()
        requested_before_finish = tuple(requested)
        assert requested_before_finish
        assert len(set(requested_before_finish)) == len(
            requested_before_finish
        )
        resets_before_finish = len(resets)
        restores_before_finish = len(location_restores)

        scanner.scan_completed.emit(
            BrowserScanCompleted(
                request.path,
                request.generation,
                item_count,
            )
        )
        QTest.qWait(40)
        qapp.processEvents()

        current = window.list_view.currentIndex()
        assert len(resets) == resets_before_finish
        assert tuple(requested) == requested_before_finish
        assert len(location_restores) == restores_before_finish
        assert current.isValid()
        assert current.data(window.item_model.PathRole) == selected_path
        assert scroll.value() == scroll_before_finish
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
