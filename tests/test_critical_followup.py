from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QItemSelectionModel,
    QMimeData,
    Qt,
    QUrl,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.browser_item_delegate import BrowserItemDelegate
from app.browser_model import BrowserItem, BrowserItemKind, BrowserItemModel
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.file_operation_coordinator import FileOperationCoordinator
from app.file_operation_queue import FileOperationQueue
from app.file_operation_service import FileOperationKind, FileOperationService
from app.file_operation_worker import FileOperationExecutor
from app.internal_clipboard import (
    InternalClipboardOperation,
    InternalClipboardState,
)
from app.metadata_store import MetadataStore
from app.system_file_opener import (
    SystemFileOpener,
    SystemOpenStatus,
)
from app.viewer_display_unit import ViewerDisplayUnit, ViewerSlotState
from app.viewer_window import ViewerWindow


class FakeSystemOpenAdapter:
    def __init__(self, *, default_result: int = 33, picker_result: int = 0) -> None:
        self.default_result = default_result
        self.picker_result = picker_result
        self.default_calls: list[tuple[str, int | None]] = []
        self.picker_calls: list[tuple[str, int | None]] = []

    def open_default(self, path: str, parent_hwnd: int | None) -> int:
        self.default_calls.append((path, parent_hwnd))
        return self.default_result

    def open_picker(self, path: str, parent_hwnd: int | None) -> int:
        self.picker_calls.append((path, parent_hwnd))
        return self.picker_result


def _write_file(path: Path, content: str = "data") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_browser(
    tmp_path: Path,
    qapp: QApplication,
    folder: Path,
    *,
    system_file_opener: SystemFileOpener | None = None,
) -> tuple[BrowserWindow, FileOperationCoordinator]:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(
        {
            "last_browser_path": str(folder),
            "browser_show_unsupported_files": True,
        },
        save=False,
    )
    metadata = MetadataStore(tmp_path / "metadata.sqlite3")
    coordinator = FileOperationCoordinator(
        metadata,
        executor=FileOperationExecutor(FileOperationService()),
    )
    window = BrowserWindow(
        config_manager=config,
        metadata_store=metadata,
        file_operation_coordinator=coordinator,
        system_file_opener=system_file_opener,
    )
    window.resize(760, 520)
    window.show()
    assert window.wait_for_scan()
    for _ in range(3):
        qapp.processEvents()
        QTest.qWait(1)
    return window, coordinator


def _close_browser(
    window: BrowserWindow,
    coordinator: FileOperationCoordinator,
    qapp: QApplication,
) -> None:
    window.close()
    coordinator.close()
    coordinator.metadata_store.close()
    qapp.clipboard().clear()
    qapp.processEvents()


def _select_paths(window: BrowserWindow, paths: list[Path]) -> None:
    selection = window.list_view.selectionModel()
    selection.clearSelection()
    for position, path in enumerate(paths):
        row = window.item_model.row_for_path(path)
        assert row >= 0
        index = window.item_model.index(row, 0)
        if position == 0:
            window.list_view.setCurrentIndex(index)
        selection.select(index, QItemSelectionModel.SelectionFlag.Select)
    window._update_file_action_states()


def _finish_operation(
    window: BrowserWindow,
    coordinator: FileOperationCoordinator,
    qapp: QApplication,
) -> None:
    assert coordinator.wait_for_done(3000)
    for _ in range(5):
        qapp.processEvents()
    if window._pending_scan is not None:
        assert window.wait_for_scan()
    for _ in range(5):
        qapp.processEvents()


def _make_png_pages(folder: Path, *, corrupt_page: int | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for page in range(1, 11):
        target = folder / f"{page:03}.png"
        if page == corrupt_page:
            target.write_bytes(b"not a png")
            continue
        mode = "RGBA" if page % 2 else "RGB"
        size = (360 + (page % 3) * 24, 560 + (page % 2) * 16)
        color = (
            (page * 21 % 255, page * 39 % 255, page * 57 % 255, 205)
            if mode == "RGBA"
            else (page * 21 % 255, page * 39 % 255, page * 57 % 255)
        )
        image = Image.new(mode, size, color)
        ImageDraw.Draw(image).text((16, 16), str(page), fill="white")
        image.save(target)
        image.close()


def _wait_for_viewer_terminal(
    window: ViewerWindow,
    qapp: QApplication,
    timeout: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if (
            not window.book_session._open_workers
            and window.viewer._images
            and window._display_unit.slots
            and window._applied_display_request_id
            == window._display_unit.request_id
            and all(
                slot.state
                in {ViewerSlotState.READY, ViewerSlotState.FAILED}
                for slot in window._display_unit.slots
            )
            and all(
                not image.loading for image in window.viewer._images
            )
        ):
            return
        QTest.qWait(5)
    pytest.fail(
        f"viewer slots did not finish: "
        f"{[(image.page_index, image.loading) for image in window.viewer._images]}"
    )


def _close_viewer(window: ViewerWindow, qapp: QApplication) -> None:
    window.image_cache.wait_for_done(3000)
    window.prepare_shutdown(wait_msecs=3000)
    window.close()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_internal_clipboard_marker_survives_delayed_changed_signal(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    state = InternalClipboardState()
    source = tmp_path / "日本語 & (book).cbz"
    snapshot = state.replace((str(source),), InternalClipboardOperation.CUT)
    mime = qapp.clipboard().mimeData()
    from PySide6.QtCore import QMimeData

    marked = QMimeData()
    state.write_marker(marked)
    assert state.matches_mime(marked)
    marked.setData(
        'application/x-qt-windows-mime;value="Preferred DropEffect"',
        (2).to_bytes(4, byteorder="little"),
    )
    assert state.preferred_drop_effect(marked) == "move"
    assert snapshot.is_cut
    assert not state.matches_mime(mime)


@pytest.mark.parametrize(
    ("cut", "is_directory"),
    [
        (True, False),
        (True, True),
        (False, False),
    ],
)
def test_windows_rehydrated_clipboard_preserves_paste_operation(
    tmp_path: Path,
    qapp: QApplication,
    cut: bool,
    is_directory: bool,
) -> None:
    source_folder = tmp_path / "source"
    destination = tmp_path / "destination"
    source = source_folder / ("folder" if is_directory else "book.cbz")
    if is_directory:
        source.mkdir(parents=True)
        _write_file(source / "child.txt")
    else:
        _write_file(source)
    destination.mkdir()
    window, coordinator = _make_browser(tmp_path, qapp, source_folder)
    kinds: list[FileOperationKind] = []
    coordinator.operation_started.connect(
        lambda request: kinds.append(request.operation)
    )
    _select_paths(window, [source])

    if cut:
        assert window.cut_selected_items()
    else:
        assert window.copy_selected_items()
    rehydrated = QMimeData()
    rehydrated.setUrls([QUrl.fromLocalFile(str(source.absolute()))])
    rehydrated.setData(
        "Preferred DropEffect",
        (2 if cut else 1).to_bytes(4, byteorder="little"),
    )
    qapp.clipboard().setMimeData(rehydrated)
    qapp.processEvents()
    window._on_system_clipboard_changed()

    assert window._clipboard_cut is cut
    assert window._clipboard_paths == (str(source.absolute()),)
    assert window.navigate_to(destination)
    assert window.wait_for_scan()
    assert window.paste_items()
    _finish_operation(window, coordinator, qapp)

    expected_kind = FileOperationKind.MOVE if cut else FileOperationKind.COPY
    moved_or_copied = destination / source.name
    assert kinds == [expected_kind]
    assert moved_or_copied.exists()
    assert source.exists() is not cut
    if is_directory:
        assert (moved_or_copied / "child.txt").exists()
    _close_browser(window, coordinator, qapp)
    qapp.clipboard().clear()
    qapp.processEvents()


@pytest.mark.parametrize("use_menu_actions", [False, True])
def test_real_cut_paste_ui_path_physically_moves_source(
    tmp_path: Path,
    qapp: QApplication,
    use_menu_actions: bool,
) -> None:
    source_folder = tmp_path / "source"
    destination = tmp_path / "destination"
    source = source_folder / "日本語 & (book).cbz"
    _write_file(source)
    destination.mkdir()
    window, coordinator = _make_browser(tmp_path, qapp, source_folder)
    kinds: list[FileOperationKind] = []
    coordinator.operation_started.connect(lambda request: kinds.append(request.operation))
    _select_paths(window, [source])

    if use_menu_actions:
        assert window.cut_action.isEnabled()
        window.cut_action.trigger()
    else:
        window.list_view.setFocus()
        QTest.keyClick(window.list_view, Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    window._on_system_clipboard_changed()
    assert window._clipboard_cut
    assert window.item_model.data(
        window.item_model.index(window.item_model.row_for_path(source), 0),
        BrowserItemModel.CutRole,
    )

    assert window.navigate_to(destination)
    assert window.wait_for_scan()
    window._update_file_action_states()
    if use_menu_actions:
        window.paste_action.trigger()
    else:
        window.list_view.setFocus()
        QTest.keyClick(window.list_view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    _finish_operation(window, coordinator, qapp)

    assert kinds == [FileOperationKind.MOVE]
    assert not source.exists()
    assert (destination / source.name).read_text(encoding="utf-8") == "data"
    assert not window._clipboard_cut
    assert window._clipboard_paths == ()
    _close_browser(window, coordinator, qapp)


def test_real_multi_cut_partial_success_retains_only_residual_path(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_folder = tmp_path / "source"
    destination = tmp_path / "destination"
    moved = source_folder / "move.cbz"
    skipped = source_folder / "skip.cbz"
    _write_file(moved, "moved")
    _write_file(skipped, "source")
    destination.mkdir()
    _write_file(destination / skipped.name, "existing")
    window, coordinator = _make_browser(tmp_path, qapp, source_folder)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: None)
    _select_paths(window, [moved, skipped])

    assert window.cut_action.isEnabled()
    window.cut_action.trigger()
    assert window.navigate_to(destination)
    assert window.wait_for_scan()
    window._update_file_action_states()
    window.paste_action.trigger()
    _finish_operation(window, coordinator, qapp)

    assert not moved.exists()
    assert (destination / moved.name).exists()
    assert skipped.exists()
    assert (destination / skipped.name).read_text(encoding="utf-8") == "existing"
    assert window._clipboard_cut
    assert window._clipboard_paths == (str(skipped.absolute()),)
    _close_browser(window, coordinator, qapp)


def test_real_cut_preserves_move_across_browser_queue_and_service(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source_folder = tmp_path / "source"
    destination = tmp_path / "destination"
    source = source_folder / "queued.cbz"
    _write_file(source)
    destination.mkdir()
    config = ConfigManager(tmp_path / "queue-config.json")
    config.load()
    config.set("last_browser_path", str(source_folder))
    metadata = MetadataStore(tmp_path / "queue-metadata.sqlite3")
    service = FileOperationService()
    service_kinds: list[FileOperationKind] = []
    original_execute = service.execute

    def record_service(request, **kwargs):
        service_kinds.append(request.operation)
        return original_execute(request, **kwargs)

    service.execute = record_service  # type: ignore[method-assign]
    queue = FileOperationQueue(service=service)
    queued_kinds: list[FileOperationKind] = []
    queue.operation_queued.connect(
        lambda request: queued_kinds.append(request.operation)
    )
    coordinator = FileOperationCoordinator(metadata, queue=queue)
    browser_kinds: list[FileOperationKind] = []
    coordinator.operation_started.connect(
        lambda request: browser_kinds.append(request.operation)
    )
    window = BrowserWindow(
        config_manager=config,
        metadata_store=metadata,
        file_operation_coordinator=coordinator,
    )
    window.show()
    assert window.wait_for_scan()
    qapp.processEvents()
    _select_paths(window, [source])

    window.list_view.setFocus()
    QTest.keyClick(window.list_view, Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier)
    assert window.navigate_to(destination)
    assert window.wait_for_scan()
    QTest.keyClick(window.list_view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    _finish_operation(window, coordinator, qapp)

    assert browser_kinds == [FileOperationKind.MOVE]
    assert queued_kinds == [FileOperationKind.MOVE]
    assert service_kinds == [FileOperationKind.MOVE]
    assert not source.exists()
    assert (destination / source.name).exists()
    _close_browser(window, coordinator, qapp)


def test_copy_ui_path_keeps_source_and_has_no_cut_visual(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source_folder = tmp_path / "source"
    destination = tmp_path / "destination"
    source = source_folder / "copy.cbz"
    _write_file(source)
    destination.mkdir()
    window, coordinator = _make_browser(tmp_path, qapp, source_folder)
    _select_paths(window, [source])
    assert window.copy_action.isEnabled()
    window.copy_action.trigger()
    row = window.item_model.row_for_path(source)
    assert not window.item_model.data(
        window.item_model.index(row, 0),
        BrowserItemModel.CutRole,
    )
    assert window.navigate_to(destination)
    assert window.wait_for_scan()
    window._update_file_action_states()
    window.paste_action.trigger()
    _finish_operation(window, coordinator, qapp)
    assert source.exists()
    assert (destination / source.name).exists()
    _close_browser(window, coordinator, qapp)


def test_cut_visual_is_path_based_and_survives_sort_and_reset(tmp_path: Path) -> None:
    model = BrowserItemModel()
    first = BrowserItem("b.cbz", tmp_path / "b.cbz", BrowserItemKind.ARCHIVE, None)
    second = BrowserItem("a.cbz", tmp_path / "a.cbz", BrowserItemKind.ARCHIVE, None)
    model.set_items((first, second))
    changed_rows: list[int] = []
    model.dataChanged.connect(
        lambda top, _bottom, roles: (
            changed_rows.append(top.row())
            if BrowserItemModel.CutRole in roles
            else None
        )
    )

    assert model.set_cut_paths((first.path,))
    first_row = model.row_for_path(first.path)
    first_index = model.index(first_row, 0)
    assert first_index.data(BrowserItemModel.CutRole)
    assert BrowserItemDelegate.content_opacity(first_index, first) == 0.52
    assert changed_rows == [first_row]

    model.configure_sort("name", "descending", True)
    assert model.index(model.row_for_path(first.path), 0).data(BrowserItemModel.CutRole)
    model.set_items((second, first))
    assert model.index(model.row_for_path(first.path), 0).data(BrowserItemModel.CutRole)
    assert not model.index(model.row_for_path(second.path), 0).data(
        BrowserItemModel.CutRole
    )


def test_system_file_opener_uses_picker_only_for_no_association(tmp_path: Path) -> None:
    target = tmp_path / "日本語 & (draft).office"
    adapter = FakeSystemOpenAdapter(default_result=31, picker_result=0)
    result = SystemFileOpener(adapter).open_with_default_application(target, 123)
    assert result.status is SystemOpenStatus.PICKER_OPENED
    assert adapter.default_calls == [(str(target.absolute()), 123)]
    assert adapter.picker_calls == [(str(target.absolute()), 123)]


@pytest.mark.parametrize("activation", ["double_click", "enter"])
def test_unsupported_browser_activation_opens_system_once_without_viewer(
    tmp_path: Path,
    qapp: QApplication,
    activation: str,
) -> None:
    folder = tmp_path / "files"
    target = folder / "日本語 & (draft).office"
    _write_file(target)
    adapter = FakeSystemOpenAdapter()
    opener = SystemFileOpener(adapter)
    window, coordinator = _make_browser(
        tmp_path,
        qapp,
        folder,
        system_file_opener=opener,
    )
    internal_opens: list[object] = []
    window._open_path_handler = lambda *args: internal_opens.append(args)
    row = window.item_model.row_for_path(target)
    assert row >= 0
    index = window.item_model.index(row, 0)
    window.list_view.setCurrentIndex(index)
    window.list_view.setFocus()
    if activation == "double_click":
        QTest.mouseDClick(
            window.list_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=window.list_view.visualRect(index).center(),
        )
        # Qt's offscreen QTest double-click helper can leave the global
        # mouseButtons state pressed after a custom mouseDoubleClickEvent.
        QTest.mouseRelease(
            window.list_view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=window.list_view.visualRect(index).center(),
        )
    else:
        QTest.keyClick(window.list_view, Qt.Key.Key_Return)
    qapp.processEvents()
    assert [Path(call[0]) for call in adapter.default_calls] == [target.absolute()]
    assert internal_opens == []
    _close_browser(window, coordinator, qapp)


def test_supported_file_stays_internal_and_explicit_external_open_is_available(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "files"
    target = folder / "broken.cbz"
    _write_file(target, "broken")
    adapter = FakeSystemOpenAdapter()
    window, coordinator = _make_browser(
        tmp_path,
        qapp,
        folder,
        system_file_opener=SystemFileOpener(adapter),
    )
    internal_opens: list[object] = []
    window._open_path_handler = lambda *args: internal_opens.append(args)
    row = window.item_model.row_for_path(target)
    index = window.item_model.index(row, 0)
    window.open_item(index)
    assert len(internal_opens) == 1
    assert adapter.default_calls == []

    assert window._open_system_file(target)
    assert [Path(call[0]) for call in adapter.default_calls] == [target.absolute()]
    _close_browser(window, coordinator, qapp)


@pytest.mark.parametrize(
    ("reading_direction", "single_first_page"),
    [
        ("rtl", True),
        ("ltr", True),
        ("rtl", False),
        ("ltr", False),
    ],
)
def test_png_spreads_page_5_7_9_finish_both_slots(
    tmp_path: Path,
    qapp: QApplication,
    request: pytest.FixtureRequest,
    reading_direction: str,
    single_first_page: bool,
) -> None:
    folder = tmp_path / f"png-{reading_direction}-{single_first_page}"
    _make_png_pages(folder)
    config = ConfigManager(tmp_path / f"{reading_direction}-{single_first_page}.json")
    config.load()
    config.apply(
        {
            "view_mode": "spread",
            "reading_direction": reading_direction,
            "single_first_page": single_first_page,
        },
        save=False,
    )
    window = ViewerWindow(config_manager=config)
    request.addfinalizer(lambda: _close_viewer(window, qapp))
    window.show()
    qapp.processEvents()
    for page in (5, 7, 9):
        assert window.open_path(folder / f"{page:03}.png")
        _wait_for_viewer_terminal(window, qapp)
        assert len(window.viewer._images) == 2
        assert all(image.pixmap is not None for image in window.viewer._images)
        assert all(
            slot.state is ViewerSlotState.READY
            for slot in window._display_unit.slots
        )
        assert window.model.focused_index == page - 1


def test_png_visible_and_prefetch_reopen_and_rapid_navigation_finish(
    tmp_path: Path,
    qapp: QApplication,
    request: pytest.FixtureRequest,
) -> None:
    folder = tmp_path / "png"
    _make_png_pages(folder)
    config = ConfigManager(tmp_path / "viewer.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    request.addfinalizer(lambda: _close_viewer(window, qapp))
    window.show()
    qapp.processEvents()
    assert window.open_path(folder / "005.png")
    _wait_for_viewer_terminal(window, qapp)
    for page in (7, 9, 5, 9):
        window.model.go_to_index(page - 1)
        window._refresh_view()
    _wait_for_viewer_terminal(window, qapp)
    assert window.model.focused_index == 8
    assert all(
        slot.state is ViewerSlotState.READY for slot in window._display_unit.slots
    )
    window._refresh_view()
    _wait_for_viewer_terminal(window, qapp)
    assert all(not image.loading for image in window.viewer._images)


def test_png_one_side_failure_ends_loading_and_partner_succeeds(
    tmp_path: Path,
    qapp: QApplication,
    request: pytest.FixtureRequest,
) -> None:
    folder = tmp_path / "png"
    _make_png_pages(folder, corrupt_page=5)
    config = ConfigManager(tmp_path / "viewer.json")
    config.load()
    window = ViewerWindow(config_manager=config)
    request.addfinalizer(lambda: _close_viewer(window, qapp))
    window.show()
    qapp.processEvents()
    assert window.open_path(folder / "005.png")
    _wait_for_viewer_terminal(window, qapp)
    states = {slot.page_index: slot.state for slot in window._display_unit.slots}
    assert states[4] is ViewerSlotState.FAILED
    assert ViewerSlotState.READY in states.values()
    assert not window._awaiting_first_frame


def test_display_unit_rejects_stale_generation_and_updates_one_slot_only() -> None:
    unit = ViewerDisplayUnit.create(
        request_id=7,
        generation=4,
        focused_page_identity="page-5",
        pages=((4, "page-5", "005.png"), (3, "page-4", "004.png")),
    )
    stale = unit.transition(
        page_index=4,
        image_id="005.png",
        generation=3,
        state=ViewerSlotState.READY,
    )
    assert stale is unit
    updated = unit.transition(
        page_index=4,
        image_id="005.png",
        generation=4,
        state=ViewerSlotState.READY,
    )
    assert updated.slots[0].state is ViewerSlotState.READY
    assert updated.slots[1].state is ViewerSlotState.LOADING
    cancelled = updated.cancel_loading()
    assert cancelled.slots[0].state is ViewerSlotState.READY
    assert cancelled.slots[1].state is ViewerSlotState.CANCELLED
