from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListView, QToolButton

from app.browser_window import BrowserWindow
from app.browser_filter import BrowserFilterState, RatingFilterMode
from app.config_manager import ConfigManager
from app.browser_location_bar import LocationPopupEntry
from app.browser_navigation import BrowserLocation


def write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (8, 12), "white") as image:
        image.save(path)


def make_window(
    tmp_path: Path,
    folder: Path,
    qapp: QApplication,
) -> BrowserWindow:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    window = BrowserWindow(config_manager=config)
    window.resize(500, 360)
    window.show()
    finish_scan(window, qapp)
    return window


def finish_scan(window: BrowserWindow, qapp: QApplication) -> None:
    assert window.wait_for_scan()
    qapp.processEvents()


class _ViewportPaintRecorder(QObject):
    def __init__(self, window: BrowserWindow) -> None:
        super().__init__(window)
        self.window = window
        self.states: list[tuple[str, str | None, str | None, int, int]] = []
        window.list_view.viewport().installEventFilter(self)

    def stop(self) -> None:
        self.window.list_view.viewport().removeEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if (
            watched is self.window.list_view.viewport()
            and event.type() == QEvent.Type.Paint
        ):
            current = self.window.item_model.item_at(
                self.window.list_view.currentIndex()
            )
            anchor_index = self.window._visible_anchor_index()
            anchor = self.window.item_model.item_at(anchor_index)
            anchor_rect = self.window.list_view.visualRect(anchor_index)
            self.states.append(
                (
                    str(self.window.current_path or ""),
                    str(current.path) if current is not None else None,
                    str(anchor.path) if anchor is not None else None,
                    anchor_rect.y() if anchor_rect.isValid() else 0,
                    self.window.list_view.verticalScrollBar().value(),
                )
            )
        return False


class _FilteredModelPaintRecorder(QObject):
    def __init__(self, window: BrowserWindow) -> None:
        super().__init__(window)
        self.window = window
        self.states: list[tuple[str, int, str | None]] = []
        window.list_view.viewport().installEventFilter(self)

    def stop(self) -> None:
        self.window.list_view.viewport().removeEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if (
            watched is self.window.list_view.viewport()
            and event.type() == QEvent.Type.Paint
        ):
            anchor_index = self.window._visible_anchor_index()
            anchor = self.window.item_model.item_at(anchor_index)
            self.states.append(
                (
                    str(self.window.current_path or ""),
                    self.window.item_model.rowCount(),
                    str(anchor.path) if anchor is not None else None,
                )
            )
        return False


def _open_child_folder(
    window: BrowserWindow,
    child: Path,
    qapp: QApplication,
) -> None:
    row = window.item_model.row_for_path(child)
    assert row >= 0
    window.open_item(window.item_model.index(row, 0))
    finish_scan(window, qapp)


def send_extra_button(
    widget,
    event_type: QEvent.Type,
    button: Qt.MouseButton,
) -> None:
    point = QPointF(10, 10)
    buttons = (
        button
        if event_type == QEvent.Type.MouseButtonPress
        else Qt.MouseButton.NoButton
    )
    event = QMouseEvent(
        event_type,
        point,
        point,
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def send_right_gesture_event(
    widget,
    event_type: QEvent.Type,
    position: QPointF,
    *,
    pressed: bool,
) -> QMouseEvent:
    event = QMouseEvent(
        event_type,
        position,
        position,
        (
            Qt.MouseButton.NoButton
            if event_type == QEvent.Type.MouseMove
            else Qt.MouseButton.RightButton
        ),
        (
            Qt.MouseButton.RightButton
            if pressed
            else Qt.MouseButton.NoButton
        ),
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)
    return event


def send_context_menu_event(widget, position) -> None:
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse,
        position,
        widget.mapToGlobal(position),
    )
    QApplication.sendEvent(widget, event)


def test_navigation_actions_and_address_follow_current_folder(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "A"
    second = tmp_path / "B"
    first.mkdir()
    second.mkdir()
    window = make_window(tmp_path, first, qapp)

    assert window.address_bar.text() == str(first.absolute())
    assert window.location_stack.currentWidget() is window.location_breadcrumb
    assert not window.back_action.isEnabled()
    assert not window.forward_action.isEnabled()
    assert window.up_action.isEnabled()
    assert window.refresh_action.isEnabled()

    assert window.navigate_to(second)
    finish_scan(window, qapp)
    assert window.address_bar.text() == str(second.absolute())
    assert window.back_action.isEnabled()
    assert not window.forward_action.isEnabled()

    assert window.go_back()
    finish_scan(window, qapp)
    assert window.current_path == first.absolute()
    assert window.forward_action.isEnabled()
    window.close()
    qapp.processEvents()


def test_back_restores_selection_and_scroll_position(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "A"
    second = tmp_path / "B"
    for number in range(50):
        write_image(first / f"{number:02}.jpg")
    second.mkdir()
    window = make_window(tmp_path, first, qapp)
    selected = first / "35.jpg"
    index = window.item_model.index(window.item_model.row_for_path(selected), 0)
    window.list_view.setCurrentIndex(index)
    window.list_view.scrollTo(index)
    qapp.processEvents()
    saved_scroll = window.list_view.verticalScrollBar().value()

    assert window.navigate_to(second)
    finish_scan(window, qapp)
    assert window.go_back()
    finish_scan(window, qapp)

    restored = window.item_model.item_at(window.list_view.currentIndex())
    assert restored is not None and restored.path == selected.absolute()
    assert abs(window.list_view.verticalScrollBar().value() - saved_scroll) <= (
        window.list_view.gridSize().height()
    )
    window.close()
    qapp.processEvents()


def test_history_restore_publishes_cached_listing_before_reconcile(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "A"
    second = tmp_path / "B"
    second.mkdir()
    for number in range(160):
        write_image(first / f"{number:03}.jpg")

    window = make_window(tmp_path, first, qapp)
    assert window.navigate_to(second)
    finish_scan(window, qapp)

    assert window.go_back()
    pending = window._pending_scan
    assert pending is not None and pending.snapshot_hit
    assert window.current_path == first.absolute()
    assert 0 < window.item_model.rowCount() < 160
    assert window._snapshot_reconcile_pending

    finish_scan(window, qapp)
    assert window.item_model.rowCount() == 160
    assert not window._snapshot_reconcile_pending
    window.close()
    qapp.processEvents()


def test_folder_snapshot_cache_limit_applies_immediately_from_config(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "A"
    write_image(folder / "a.jpg")
    window = make_window(tmp_path, folder, qapp)

    assert window.folder_snapshot_cache.max_entries == 60_000
    window.config.apply(
        {"browser_folder_snapshot_cache_max_entries": 15_000}
    )
    assert window.folder_snapshot_cache.max_entries == 15_000

    window.config.apply({"browser_folder_snapshot_cache_enabled": False})
    assert not window.folder_snapshot_cache.enabled
    assert window.folder_snapshot_cache.entry_count == 0
    window.close()
    qapp.processEvents()


def test_large_back_never_paints_parent_before_saved_selection_is_restored(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    for number in range(180):
        write_image(parent / f"{number:03}.jpg")
    window = make_window(tmp_path, parent, qapp)
    selected = parent / "150.jpg"
    index = window.item_model.index(window.item_model.row_for_path(selected), 0)
    window.list_view.setCurrentIndex(index)
    window.list_view.scrollTo(index, QListView.ScrollHint.PositionAtCenter)
    qapp.processEvents()
    saved_scroll = window.list_view.verticalScrollBar().value()
    _open_child_folder(window, child, qapp)
    recorder = _ViewportPaintRecorder(window)

    try:
        assert window.go_back()
        finish_scan(window, qapp)

        parent_paints = [
            state for state in recorder.states if state[0] == str(parent.absolute())
        ]
        assert parent_paints
        assert all(state[1] == str(selected.absolute()) for state in parent_paints)
        restored = window.item_model.item_at(window.list_view.currentIndex())
        assert restored is not None and restored.path == selected.absolute()
        assert abs(
            window.list_view.verticalScrollBar().value() - saved_scroll
        ) <= window.list_view.gridSize().height()
    finally:
        recorder.stop()
        window.close()
        qapp.processEvents()


def test_large_back_without_selection_first_paints_saved_anchor_and_offset(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    for number in range(180):
        write_image(parent / f"{number:03}.jpg")
    window = make_window(tmp_path, parent, qapp)
    anchor_target = parent / "140.jpg"
    index = window.item_model.index(
        window.item_model.row_for_path(anchor_target),
        0,
    )
    window.list_view.scrollTo(index, QListView.ScrollHint.PositionAtTop)
    window.list_view.clearSelection()
    window.list_view.setCurrentIndex(QModelIndex())
    qapp.processEvents()
    anchor_index = window._visible_anchor_index()
    anchor_item = window.item_model.item_at(anchor_index)
    assert anchor_item is not None
    saved_anchor = str(anchor_item.path)
    saved_offset = window.list_view.visualRect(anchor_index).y()
    _open_child_folder(window, child, qapp)
    recorder = _ViewportPaintRecorder(window)

    try:
        assert window.go_back()
        finish_scan(window, qapp)

        parent_paints = [
            state for state in recorder.states if state[0] == str(parent.absolute())
        ]
        assert parent_paints
        assert all(state[1] is None for state in parent_paints)
        assert all(state[2] == saved_anchor for state in parent_paints), (
            saved_anchor,
            saved_offset,
            parent_paints,
        )
        assert all(state[3] == saved_offset for state in parent_paints), (
            saved_anchor,
            saved_offset,
            parent_paints,
        )
        assert not window.list_view.currentIndex().isValid()
    finally:
        recorder.stop()
        window.close()
        qapp.processEvents()


def test_history_restore_initial_model_is_bounded_but_contains_saved_view(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "large"
    folder.mkdir()
    for number in range(1200):
        write_image(folder / f"{number:04}.jpg")
    window = make_window(tmp_path, folder, qapp)
    try:
        selected = folder / "0900.jpg"
        index = window.item_model.index(window.item_model.row_for_path(selected), 0)
        window.list_view.setCurrentIndex(index)
        window.list_view.scrollTo(index, QListView.ScrollHint.PositionAtCenter)
        qapp.processEvents()
        location = window._current_location()
        items = window.item_model.source_items
        initial = window._initial_restore_scan_item_count(items, location)
        assert initial < len(items)
        assert initial > window._initial_scan_item_count(len(items))
        selected_row = next(row for row, item in enumerate(items) if item.path == selected)
        assert initial > selected_row
        visible = window._visible_row_range()
        assert visible is not None
        assert initial > visible[1]
    finally:
        window.close()
        qapp.processEvents()


def test_filtered_history_restore_keeps_sparse_viewport_on_back_and_forward(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    for folder, prefix in ((parent, "parent"), (child, "child")):
        for number in range(600):
            rating = " {zpi$r=5}" if number % 7 == 0 else ""
            write_image(folder / f"{prefix}-{number:04}{rating}.jpg")

    window = make_window(tmp_path, parent, qapp)
    try:
        window._set_browser_filter(
            BrowserFilterState.normalized(
                rating_mode=RatingFilterMode.AT_LEAST,
                rating_reference=5,
            )
        )
        qapp.processEvents()
        assert window.item_model.rowCount() > 70

        parent_index = window.item_model.index(70, 0)
        window.list_view.scrollTo(parent_index, QListView.ScrollHint.PositionAtTop)
        window.list_view.clearSelection()
        window.list_view.setCurrentIndex(QModelIndex())
        qapp.processEvents()
        parent_anchor = window.item_model.item_at(window._visible_anchor_index())
        assert parent_anchor is not None
        parent_anchor_path = str(parent_anchor.path)

        assert window.navigate_to(child)
        finish_scan(window, qapp)
        child_index = window.item_model.index(70, 0)
        window.list_view.scrollTo(child_index, QListView.ScrollHint.PositionAtTop)
        window.list_view.clearSelection()
        window.list_view.setCurrentIndex(QModelIndex())
        qapp.processEvents()
        child_anchor = window.item_model.item_at(window._visible_anchor_index())
        assert child_anchor is not None
        child_anchor_path = str(child_anchor.path)

        recorder = _FilteredModelPaintRecorder(window)
        assert window.go_back()
        finish_scan(window, qapp)
        assert window.go_forward()
        finish_scan(window, qapp)

        parent_paints = [
            state for state in recorder.states if state[0] == str(parent.absolute())
        ]
        child_paints = [
            state for state in recorder.states if state[0] == str(child.absolute())
        ]
        assert parent_paints
        assert child_paints
        assert parent_paints[0][2] == parent_anchor_path
        assert child_paints[0][2] == child_anchor_path
        assert parent_paints[0][1] < window.item_model.source_count
        assert child_paints[0][1] < window.item_model.source_count
    finally:
        if "recorder" in locals():
            recorder.stop()
        window.close()
        qapp.processEvents()


def test_small_back_keeps_existing_selection_restore_behavior(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    for number in range(6):
        write_image(parent / f"{number:03}.jpg")
    window = make_window(tmp_path, parent, qapp)
    selected = parent / "004.jpg"
    window.list_view.setCurrentIndex(
        window.item_model.index(window.item_model.row_for_path(selected), 0)
    )
    _open_child_folder(window, child, qapp)
    recorder = _ViewportPaintRecorder(window)

    try:
        assert window.go_back()
        finish_scan(window, qapp)
        parent_paints = [
            state for state in recorder.states if state[0] == str(parent.absolute())
        ]
        assert parent_paints
        assert all(state[1] == str(selected.absolute()) for state in parent_paints)
    finally:
        recorder.stop()
        window.close()
        qapp.processEvents()


def test_forward_never_paints_large_folder_before_saved_state_is_restored(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "first"
    second = first / "second"
    second.mkdir(parents=True)
    for number in range(90):
        write_image(first / f"first-{number:03}.jpg")
        write_image(second / f"second-{number:03}.jpg")
    window = make_window(tmp_path, first, qapp)
    _open_child_folder(window, second, qapp)
    selected = second / "second-075.jpg"
    index = window.item_model.index(window.item_model.row_for_path(selected), 0)
    window.list_view.setCurrentIndex(index)
    window.list_view.scrollTo(index, QListView.ScrollHint.PositionAtCenter)
    qapp.processEvents()
    assert window.go_back()
    finish_scan(window, qapp)
    recorder = _ViewportPaintRecorder(window)

    try:
        assert window.go_forward()
        finish_scan(window, qapp)
        second_paints = [
            state for state in recorder.states if state[0] == str(second.absolute())
        ]
        assert second_paints
        assert all(state[1] == str(selected.absolute()) for state in second_paints)
    finally:
        recorder.stop()
        window.close()
        qapp.processEvents()


def test_back_clears_search_but_preserves_sort_and_restores_saved_item(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "keep-child"
    child.mkdir(parents=True)
    for number in range(70):
        write_image(parent / f"keep-{number:03}.jpg")
        write_image(parent / f"drop-{number:03}.jpg")
    window = make_window(tmp_path, parent, qapp)
    window.config.apply({"browser_sort_order": "descending"})
    window._set_browser_filter(
        BrowserFilterState.normalized(search_text="keep")
    )
    qapp.processEvents()
    source_count = window.item_model.source_count
    selected = parent / "keep-010.jpg"
    index = window.item_model.index(window.item_model.row_for_path(selected), 0)
    window.list_view.setCurrentIndex(index)
    window.list_view.scrollTo(index, QListView.ScrollHint.PositionAtCenter)
    qapp.processEvents()
    _open_child_folder(window, child, qapp)

    try:
        assert window.go_back()
        finish_scan(window, qapp)
        assert window.item_model.rowCount() == source_count
        restored = window.item_model.item_at(window.list_view.currentIndex())
        assert restored is not None and restored.path == selected.absolute()
        assert window.browser_sort_order.value == "descending"
        assert window.browser_filter_state.search_text == ""
        assert window.browser_search_edit.text() == ""
    finally:
        window.close()
        qapp.processEvents()


def test_breadcrumb_separator_text_mode_and_filtered_viewer_snapshot(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    a = tmp_path / "A"
    b = a / "B"
    c = b / "C"
    d = c / "D"
    d.mkdir(parents=True)
    write_image(c / "abc {zpi$r=3}.jpg")
    write_image(c / "xyz {zpi$r=5}.jpg")
    window = make_window(tmp_path, d, qapp)
    window.config.apply(
        {
            "browser_sort_key": "name",
            "browser_sort_order": "descending",
        }
    )
    window._set_browser_filter(
        BrowserFilterState.normalized(
            search_text="abc",
            rating_mode=RatingFilterMode.AT_LEAST,
            rating_reference=3,
        )
    )

    try:
        window._navigate_from_breadcrumb(str(b))
        finish_scan(window, qapp)
        assert window.current_path == b.absolute()
        assert window.browser_filter_state.search_text == ""
        assert window.browser_search_edit.text() == ""
        assert (
            window.browser_filter_state.rating_mode
            is RatingFilterMode.AT_LEAST
        )
        assert window.browser_filter_state.rating_reference == 3
        assert window.browser_sort_order.value == "descending"

        current_index = len(window.location_breadcrumb.segments) - 1
        current_button = window.location_breadcrumb.findChild(
            QToolButton,
            f"browser_location_segment_{current_index}",
        )
        assert current_button is not None
        QTest.mouseClick(current_button, Qt.MouseButton.LeftButton)
        assert window.location_stack.currentWidget() is window.address_bar
        assert window.address_bar.selectedText() == str(b.absolute())
        QTest.keyClick(window.address_bar, Qt.Key.Key_Escape)

        QTest.keyClick(
            window,
            Qt.Key.Key_L,
            Qt.KeyboardModifier.ControlModifier,
        )
        assert window.location_stack.currentWidget() is window.address_bar
        assert window.address_bar.selectedText() == str(b.absolute())
        QTest.keyClick(window.address_bar, Qt.Key.Key_Escape)
        assert window.location_stack.currentWidget() is window.location_breadcrumb

        window._show_breadcrumb_children(str(b))
        for _ in range(100):
            qapp.processEvents()
            menu = window._location_directory_menu
            if menu is not None and any(
                menu.list_widget.item(row).text() == "C"
                for row in range(menu.entry_count)
            ):
                break
            QTest.qWait(5)
        assert menu is not None
        child_item = next(
            menu.list_widget.item(row)
            for row in range(menu.entry_count)
            if menu.list_widget.item(row).text() == "C"
        )
        menu.list_widget.itemClicked.emit(child_item)
        finish_scan(window, qapp)
        assert window.current_path == c.absolute()
        assert [item.display_name for item in window.items] == [
            "xyz.jpg",
            "abc.jpg",
        ]

        snapshot = window._folder_snapshot_for_path(c / "abc {zpi$r=3}.jpg")
        assert snapshot is not None
        assert snapshot.image_ids == (
            str(c / "xyz {zpi$r=5}.jpg"),
            str(c / "abc {zpi$r=3}.jpg"),
        )
    finally:
        window.close()
        qapp.processEvents()


def test_breadcrumb_arrow_coalesces_clicks_and_never_shows_an_empty_popup(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    (folder / "child2").mkdir(parents=True)
    (folder / "child10").mkdir()
    window = make_window(tmp_path, folder, qapp)
    current_index = len(window.location_breadcrumb.segments) - 1
    arrow = window.location_breadcrumb.findChild(
        QToolButton,
        f"browser_location_separator_{current_index}",
    )
    assert arrow is not None

    try:
        with patch.object(
            window.location_directory_loader,
            "request",
            wraps=window.location_directory_loader.request,
        ) as request:
            for _ in range(10):
                QTest.mouseClick(arrow, Qt.MouseButton.LeftButton)
            assert request.call_count == 1
            assert window._location_directory_menu is None

            for _ in range(100):
                qapp.processEvents()
                menu = window._location_directory_menu
                if menu is not None:
                    break
                QTest.qWait(5)
            assert menu is not None and menu.isVisible()
            assert [
                menu.list_widget.item(row).text()
                for row in range(menu.entry_count)
            ] == [
                "child2",
                "child10",
            ]
            assert menu.width() > 0
            assert menu.height() > 0

            QTest.keyClick(menu, Qt.Key.Key_Escape)
            qapp.processEvents()
            assert window._location_directory_menu is None

            QTest.mouseClick(arrow, Qt.MouseButton.LeftButton)
            for _ in range(100):
                qapp.processEvents()
                if window._location_directory_menu is not None:
                    break
                QTest.qWait(5)
            assert window._location_directory_menu is not None
            window.resize(800, 420)
            qapp.processEvents()
            assert window._location_directory_menu is None
    finally:
        window.close()
        qapp.processEvents()


def test_history_popup_direct_jump_restores_selection_scroll_and_recent_menu(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    third = tmp_path / "third"
    for number in range(50):
        write_image(first / f"{number:02}.jpg")
    second.mkdir()
    third.mkdir()
    window = make_window(tmp_path, first, qapp)
    selected = first / "35.jpg"
    index = window.item_model.index(window.item_model.row_for_path(selected), 0)
    window.list_view.setCurrentIndex(index)
    window.list_view.scrollTo(index)
    qapp.processEvents()
    saved_scroll = window.list_view.verticalScrollBar().value()
    assert window.navigate_to(second)
    finish_scan(window, qapp)
    assert window.navigate_to(third)
    finish_scan(window, qapp)

    try:
        history_menu = window._show_navigation_history_menu(
            "back",
            QPoint(1, 1),
        )
        assert history_menu is not None
        first_item = next(
            history_menu.list_widget.item(row)
            for row in range(history_menu.entry_count)
            if history_menu.list_widget.item(row).toolTip()
            == str(first.absolute())
        )
        history_menu.list_widget.itemClicked.emit(first_item)
        finish_scan(window, qapp)
        assert window.current_path == first.absolute()
        restored = window.item_model.item_at(window.list_view.currentIndex())
        assert restored is not None and restored.path == selected.absolute()
        assert abs(
            window.list_view.verticalScrollBar().value() - saved_scroll
        ) <= window.list_view.gridSize().height()

        QTest.mouseClick(
            window.browser_location_control,
            Qt.MouseButton.LeftButton,
            pos=window.browser_location_control.drop_down_rect().center(),
        )
        qapp.processEvents()
        recent_menu = window._location_history_popup
        assert recent_menu is not None
        assert {
            recent_menu.list_widget.item(row).toolTip()
            for row in range(recent_menu.entry_count)
        } == {
            str(first.absolute()),
            str(second.absolute()),
            str(third.absolute()),
        }
    finally:
        window.close()
        qapp.processEvents()


def test_failed_direct_history_jump_restores_the_timeline_index(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    window = make_window(tmp_path, first, qapp)
    assert window.navigate_to(second)
    finish_scan(window, qapp)
    first.rmdir()

    try:
        assert window.navigation_history.current_index == 1
        assert window.go_to_history_index(0)
        finish_scan(window, qapp)
        assert window.current_path == second.absolute()
        assert window.navigation_history.current_index == 1
        assert window.statusBar().currentMessage() == "フォルダが見つかりません"

        recent_menu = window._show_location_history_popup()
        assert recent_menu is not None
        missing_item = next(
            recent_menu.list_widget.item(row)
            for row in range(recent_menu.entry_count)
            if recent_menu.list_widget.item(row).toolTip()
            == str(first.absolute())
        )
        recent_menu.list_widget.itemClicked.emit(missing_item)
        finish_scan(window, qapp)
        assert all(
            location.path != str(first.absolute())
            for _index, location in window.navigation_history.recent_unique()
        )
        assert window.navigation_history.current_index == 1
    finally:
        window.close()
        qapp.processEvents()


def test_back_with_deleted_selection_continues_without_selection(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "A"
    second = tmp_path / "B"
    selected = first / "selected.jpg"
    write_image(selected)
    second.mkdir()
    window = make_window(tmp_path, first, qapp)
    index = window.item_model.index(window.item_model.row_for_path(selected), 0)
    window.list_view.setCurrentIndex(index)

    assert window.navigate_to(second)
    finish_scan(window, qapp)
    selected.unlink()
    assert window.go_back()
    finish_scan(window, qapp)

    assert not window.list_view.currentIndex().isValid()
    window.close()
    qapp.processEvents()


def test_up_gesture_selects_previous_child_and_can_go_back(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    window = make_window(tmp_path, child, qapp)

    window._on_browser_folder_gesture("U")
    finish_scan(window, qapp)

    assert window.current_path == parent.absolute()
    selected = window.item_model.item_at(window.list_view.currentIndex())
    assert selected is not None and selected.path == child.absolute()
    assert window.go_back()
    finish_scan(window, qapp)
    assert window.current_path == child.absolute()
    window.close()
    qapp.processEvents()


def test_all_directory_navigation_routes_clear_only_active_search(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    window = make_window(tmp_path, child, qapp)

    def activate(query: str) -> None:
        window.search_history.record(query)
        window.browser_search_edit.setText(query)
        window._browser_search_timer.stop()
        window._apply_pending_browser_search()
        assert window.browser_filter_state.search_text == query

    try:
        activate("up-query")
        assert window.go_up()
        finish_scan(window, qapp)
        assert window.browser_search_edit.text() == ""

        activate("child")
        child_row = window.item_model.row_for_path(child)
        window.open_item(window.item_model.index(child_row, 0))
        finish_scan(window, qapp)
        assert window.browser_filter_state.search_text == ""

        activate("back-query")
        assert window.go_back()
        finish_scan(window, qapp)
        assert window.browser_filter_state.search_text == ""

        activate("forward-query")
        assert window.go_forward()
        finish_scan(window, qapp)
        assert window.browser_filter_state.search_text == ""

        activate("breadcrumb-query")
        window._navigate_from_breadcrumb(str(parent))
        finish_scan(window, qapp)
        assert window.browser_filter_state.search_text == ""

        activate("address-query")
        window.address_bar.setText(str(child))
        window._navigate_from_address_bar()
        finish_scan(window, qapp)
        assert window.browser_filter_state.search_text == ""

        activate("mru-query")
        location = BrowserLocation(str(parent))
        window._activate_location_history_entry(
            LocationPopupEntry("parent", location, str(parent))
        )
        finish_scan(window, qapp)
        assert window.browser_filter_state.search_text == ""
        assert window.search_history.entries == (
            "mru-query",
            "address-query",
            "breadcrumb-query",
            "forward-query",
            "back-query",
            "child",
            "up-query",
        )
    finally:
        window.close()
        qapp.processEvents()


def test_same_location_sort_rating_and_thumbnail_changes_keep_search(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    write_image(folder / "keep {zpi$r=4}.jpg")
    write_image(folder / "drop {zpi$r=2}.jpg")
    window = make_window(tmp_path, folder, qapp)
    window.browser_search_edit.setText("keep")
    window._browser_search_timer.stop()
    window._apply_pending_browser_search()

    try:
        window.config.apply({"browser_sort_order": "descending"})
        window._on_rating_quick_filter_changed(
            RatingFilterMode.AT_LEAST.value,
            3,
        )
        window.config.apply({"thumbnail_size": 224})
        qapp.processEvents()

        assert window.browser_filter_state.search_text == "keep"
        assert window.browser_search_edit.text() == "keep"
        assert window.browser_filter_state.rating_reference == 3
        assert window.thumbnail_size == 224
    finally:
        window.close()
        qapp.processEvents()


def test_refresh_preserves_history_selection_scroll_and_updates_items(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "refresh"
    for number in range(30):
        write_image(folder / f"{number:02}.jpg")
    window = make_window(tmp_path, folder, qapp)
    selected = folder / "20.jpg"
    index = window.item_model.index(window.item_model.row_for_path(selected), 0)
    window.list_view.setCurrentIndex(index)
    window.list_view.scrollTo(index)
    qapp.processEvents()
    saved_scroll = window.list_view.verticalScrollBar().value()
    history_size = len(window.navigation_history)
    added = folder / "added.jpg"
    write_image(added)

    assert window.refresh_current_folder()
    finish_scan(window, qapp)

    assert len(window.navigation_history) == history_size
    assert window.item_model.row_for_path(added) >= 0
    restored = window.item_model.item_at(window.list_view.currentIndex())
    assert restored is not None and restored.path == selected.absolute()
    assert abs(window.list_view.verticalScrollBar().value() - saved_scroll) <= (
        window.list_view.gridSize().height()
    )
    assert window.statusBar().currentMessage() == "フォルダを更新しました"
    window.close()
    qapp.processEvents()


def test_browser_folder_gesture_starts_on_item_and_blank_viewport_only(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "gestures"
    image = folder / "item.jpg"
    write_image(image)
    window = make_window(tmp_path, folder, qapp)
    gestures: list[str] = []
    window.list_view.folderGestureRecognized.disconnect()
    window.list_view.folderGestureRecognized.connect(gestures.append)
    item_index = window.item_model.index(
        window.item_model.row_for_path(image),
        0,
    )
    item_point = window.list_view.visualRect(item_index).center()
    blank_point = window.list_view.viewport().rect().bottomRight() - QPointF(
        12,
        12,
    ).toPoint()
    assert not window.list_view.indexAt(blank_point).isValid()

    for point in (item_point, blank_point):
        start = QPointF(point)
        end = start + QPointF(70, 0)
        send_right_gesture_event(
            window.list_view.viewport(),
            QEvent.Type.MouseButtonPress,
            start,
            pressed=True,
        )
        send_right_gesture_event(
            window.list_view.viewport(),
            QEvent.Type.MouseMove,
            end,
            pressed=True,
        )
        send_right_gesture_event(
            window.list_view.viewport(),
            QEvent.Type.MouseButtonRelease,
            end,
            pressed=False,
        )

    send_right_gesture_event(
        window,
        QEvent.Type.MouseButtonPress,
        QPointF(20, 20),
        pressed=True,
    )
    send_right_gesture_event(
        window,
        QEvent.Type.MouseButtonRelease,
        QPointF(90, 20),
        pressed=False,
    )

    assert gestures == ["R", "R"]
    window.close()
    qapp.processEvents()


def test_browser_gesture_dispatches_only_once_on_release(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "gestures"
    folder.mkdir()
    window = make_window(tmp_path, folder, qapp)
    refreshes: list[bool] = []
    navigations: list[bool] = []
    adjacent_requests: list[int] = []
    window._folder_navigation_handler = (
        lambda _window, direction: adjacent_requests.append(direction)
        or "searching"
    )
    monkeypatch.setattr(
        window,
        "refresh_current_folder",
        lambda: refreshes.append(True) or True,
    )
    monkeypatch.setattr(
        window,
        "go_up",
        lambda: navigations.append(True) or True,
    )
    start = QPointF(60, 40)
    send_right_gesture_event(
        window.list_view.viewport(),
        QEvent.Type.MouseMove,
        start + QPointF(15, 10),
        pressed=False,
    )
    assert adjacent_requests == []

    send_right_gesture_event(
        window.list_view.viewport(),
        QEvent.Type.MouseButtonPress,
        start,
        pressed=True,
    )
    for offset in (45, 70, 95):
        send_right_gesture_event(
            window.list_view.viewport(),
            QEvent.Type.MouseMove,
            start + QPointF(0, offset),
            pressed=True,
        )
        assert refreshes == []
        assert navigations == []
    send_right_gesture_event(
        window.list_view.viewport(),
        QEvent.Type.MouseButtonRelease,
        start + QPointF(0, 95),
        pressed=False,
    )

    assert refreshes == [True]
    assert navigations == []
    window.close()
    qapp.processEvents()


def test_browser_gesture_setting_is_independent_and_keeps_context_menu(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "gestures"
    folder.mkdir()
    window = make_window(tmp_path, folder, qapp)
    menus: list[object] = []
    gestures: list[str] = []
    window.list_view.customContextMenuRequested.disconnect()
    window.list_view.customContextMenuRequested.connect(menus.append)
    window.list_view.folderGestureRecognized.connect(gestures.append)
    window.config.apply({"mouse_gestures_enabled": False})

    assert window.list_view.browser_folder_gestures_enabled

    window.config.apply({"browser_folder_gestures_enabled": False})
    QTest.mouseClick(
        window.list_view.viewport(),
        Qt.MouseButton.RightButton,
        pos=QPointF(40, 40).toPoint(),
    )
    send_context_menu_event(
        window.list_view.viewport(),
        QPointF(40, 40).toPoint(),
    )

    assert not window.list_view.browser_folder_gestures_enabled
    assert gestures == []
    assert len(menus) == 1
    window.close()
    qapp.processEvents()


def test_plain_browser_right_click_keeps_menu_and_gesture_suppresses_once(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "gestures"
    folder.mkdir()
    window = make_window(tmp_path, folder, qapp)
    menus: list[object] = []
    window.list_view.customContextMenuRequested.disconnect()
    window.list_view.customContextMenuRequested.connect(menus.append)
    point = QPointF(40, 40)

    QTest.mouseClick(
        window.list_view.viewport(),
        Qt.MouseButton.RightButton,
        pos=point.toPoint(),
    )
    send_context_menu_event(window.list_view.viewport(), point.toPoint())

    assert len(menus) == 1
    assert not window.list_view.consume_folder_gesture_context_menu_suppression()

    send_right_gesture_event(
        window.list_view.viewport(),
        QEvent.Type.MouseButtonPress,
        point,
        pressed=True,
    )
    send_right_gesture_event(
        window.list_view.viewport(),
        QEvent.Type.MouseMove,
        point + QPointF(70, 0),
        pressed=True,
    )
    send_right_gesture_event(
        window.list_view.viewport(),
        QEvent.Type.MouseButtonRelease,
        point + QPointF(70, 0),
        pressed=False,
    )

    assert window.list_view.consume_folder_gesture_context_menu_suppression()
    assert not window.list_view.consume_folder_gesture_context_menu_suppression()
    window.close()
    qapp.processEvents()


def test_refresh_gesture_clears_missing_selection_without_adding_history(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "refresh"
    selected = folder / "selected.jpg"
    write_image(selected)
    write_image(folder / "remaining.jpg")
    window = make_window(tmp_path, folder, qapp)
    index = window.item_model.index(window.item_model.row_for_path(selected), 0)
    window.list_view.setCurrentIndex(index)
    history_size = len(window.navigation_history)
    selected.unlink()

    window._on_browser_folder_gesture("D")
    finish_scan(window, qapp)

    assert len(window.navigation_history) == history_size
    assert window.item_model.row_for_path(selected) < 0
    assert not window.list_view.currentIndex().isValid()
    window.close()
    qapp.processEvents()


def test_address_input_handles_missing_supported_and_unsupported_files(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    current = tmp_path / "current"
    target = tmp_path / "target"
    current.mkdir()
    image = target / "選択.tiff"
    archive = target / "本.cbz"
    unsupported = target / "note.txt"
    write_image(image)
    archive.write_bytes(b"placeholder")
    unsupported.write_text("text", encoding="utf-8")
    window = make_window(tmp_path, current, qapp)

    window.address_bar.setText(str(tmp_path / "missing"))
    window._navigate_from_address_bar()
    finish_scan(window, qapp)
    assert window.current_path == current.absolute()
    assert window.statusBar().currentMessage() == "フォルダが見つかりません"

    window.address_bar.setText(f'  "{image}"  ')
    window._navigate_from_address_bar()
    finish_scan(window, qapp)
    selected = window.item_model.item_at(window.list_view.currentIndex())
    assert window.current_path == target.absolute()
    assert selected is not None and selected.path == image.absolute()

    window.address_bar.setText(str(archive))
    window._navigate_from_address_bar()
    finish_scan(window, qapp)
    selected = window.item_model.item_at(window.list_view.currentIndex())
    assert selected is not None and selected.path == archive.absolute()
    assert {
        Path(location.path)
        for _index, location in window.navigation_history.recent_unique()
    } == {current.absolute(), target.absolute()}
    assert all(
        Path(location.path).suffix == ""
        for _index, location in window.navigation_history.recent_unique()
    )

    window.address_bar.setText(str(unsupported))
    window._navigate_from_address_bar()
    finish_scan(window, qapp)
    assert window.current_path == target.absolute()
    assert window.statusBar().currentMessage() == "このファイル形式は表示できません"
    window.close()
    qapp.processEvents()


def test_address_relative_path_ctrl_l_escape_and_backspace_editing(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "A"
    child = first / "child"
    child.mkdir(parents=True)
    window = make_window(tmp_path, first, qapp)

    QTest.keyClick(window, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    assert window.address_bar.hasFocus()
    assert window.address_bar.selectedText() == str(first.absolute())

    window.address_bar.setText("temporary")
    QTest.keyClick(window.address_bar, Qt.Key.Key_Escape)
    assert window.address_bar.text() == str(first.absolute())

    window.address_bar.setText("child")
    window._navigate_from_address_bar()
    finish_scan(window, qapp)
    assert window.current_path == child.absolute()

    window.address_bar.setText("abc")
    window.focus_address_bar()
    window.address_bar.setText("abc")
    QTest.keyClick(window.address_bar, Qt.Key.Key_Backspace)
    assert window.address_bar.text() == "ab"
    assert window.current_path == child.absolute()
    window.close()
    qapp.processEvents()


def test_address_bar_first_click_selects_all_then_preserves_normal_editing(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "address"
    child = folder / "child"
    child.mkdir(parents=True)
    window = make_window(tmp_path, folder, qapp)
    address = window.address_bar
    full_path = str(folder.absolute())
    center = address.rect().center()

    window.list_view.setFocus()
    qapp.processEvents()
    assert not address.hasFocus()
    window.focus_address_bar()
    assert address.selectedText() == full_path

    later_click = QPoint(max(2, address.width() // 4), center.y())
    QTest.mouseClick(address, Qt.MouseButton.LeftButton, pos=later_click)
    assert address.selectedText() != full_path

    QTest.keyClick(
        address,
        Qt.Key.Key_A,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert address.selectedText() == full_path
    QTest.keyClick(address, Qt.Key.Key_End)
    assert address.cursorPosition() == len(address.text())
    QTest.keyClick(address, Qt.Key.Key_Home)
    assert address.cursorPosition() == 0
    QTest.keyClick(address, Qt.Key.Key_Right)
    assert address.cursorPosition() == 1

    address.setText(full_path)
    window.list_view.setFocus()
    qapp.processEvents()
    start = QPoint(2, center.y())
    end = QPoint(max(8, address.width() // 2), center.y())
    QTest.mousePress(address, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(address, end)
    QTest.mouseRelease(address, Qt.MouseButton.LeftButton, pos=end)
    assert address.selectedText()
    assert address.selectedText() != full_path

    address.setText("child")
    QTest.keyClick(address, Qt.Key.Key_Return)
    finish_scan(window, qapp)
    assert window.current_path == child.absolute()
    window.focus_address_bar()
    address.setText("temporary")
    QTest.keyClick(address, Qt.Key.Key_Escape)
    assert address.text() == str(child.absolute())
    window.close()
    qapp.processEvents()


def test_inaccessible_discovery_keeps_current_items(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    first = tmp_path / "A"
    blocked = tmp_path / "blocked"
    write_image(first / "1.jpg")
    blocked.mkdir()
    window = make_window(tmp_path, first, qapp)
    original_items = window.items
    original_scandir = os.scandir

    def scandir(path):
        if Path(path) == blocked:
            raise PermissionError("denied")
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)

    assert window.navigate_to(blocked)
    finish_scan(window, qapp)
    assert window.current_path == first.absolute()
    assert window.items == original_items
    assert window.statusBar().currentMessage() == "フォルダへアクセスできません"
    window.close()
    qapp.processEvents()


def test_failed_refresh_keeps_existing_list_and_history(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "folder"
    write_image(folder / "1.jpg")
    window = make_window(tmp_path, folder, qapp)
    original_items = window.items
    history_size = len(window.navigation_history)
    original_scandir = os.scandir

    def scandir(path):
        if Path(path) == folder:
            raise PermissionError("denied")
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)

    assert window.refresh_current_folder()
    finish_scan(window, qapp)
    assert window.items == original_items
    assert len(window.navigation_history) == history_size
    assert window.statusBar().currentMessage() == "フォルダへアクセスできません"
    window.close()
    qapp.processEvents()


def test_shortcuts_back_forward_up_and_refresh(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    root = tmp_path / "root"
    first = root / "A"
    second = root / "B"
    first.mkdir(parents=True)
    second.mkdir()
    window = make_window(tmp_path, first, qapp)
    assert window.navigate_to(second)
    finish_scan(window, qapp)

    QTest.keyClick(window, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    finish_scan(window, qapp)
    assert window.current_path == first.absolute()
    QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    finish_scan(window, qapp)
    assert window.current_path == second.absolute()
    QTest.keyClick(window, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    finish_scan(window, qapp)
    assert window.current_path == root.absolute()

    history_size = len(window.navigation_history)
    QTest.keyClick(window, Qt.Key.Key_F5)
    finish_scan(window, qapp)
    assert len(window.navigation_history) == history_size
    assert window.statusBar().currentMessage() == "フォルダを更新しました"
    window.close()
    qapp.processEvents()


def test_backspace_navigates_when_list_has_focus_and_enter_opens_selection(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "A"
    second = tmp_path / "B"
    image = first / "page.jpg"
    write_image(image)
    second.mkdir()
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(first))
    opened: list[str] = []
    window = BrowserWindow(
        config_manager=config,
        open_path_handler=lambda path, _new: opened.append(path),
    )
    window.show()
    finish_scan(window, qapp)
    assert window.navigate_to(second)
    finish_scan(window, qapp)

    window.list_view.setFocus()
    QTest.keyClick(window.list_view, Qt.Key.Key_Backspace)
    finish_scan(window, qapp)
    assert window.current_path == first.absolute()

    index = window.item_model.index(window.item_model.row_for_path(image), 0)
    window.list_view.setCurrentIndex(index)
    QTest.keyClick(window.list_view, Qt.Key.Key_Return)
    assert opened == [str(image.absolute())]
    window.close()
    qapp.processEvents()


def test_extra_buttons_on_child_fire_once_on_press_and_do_not_mix_actions(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "A"
    second = tmp_path / "B"
    third = tmp_path / "C"
    first.mkdir()
    second.mkdir()
    third.mkdir()
    window = make_window(tmp_path, first, qapp)
    assert window.navigate_to(second)
    finish_scan(window, qapp)
    assert window.navigate_to(third)
    finish_scan(window, qapp)
    viewport = window.list_view.viewport()

    send_extra_button(
        viewport,
        QEvent.Type.MouseButtonPress,
        Qt.MouseButton.BackButton,
    )
    finish_scan(window, qapp)
    send_extra_button(
        viewport,
        QEvent.Type.MouseButtonRelease,
        Qt.MouseButton.BackButton,
    )
    finish_scan(window, qapp)
    assert window.current_path == second.absolute()

    send_extra_button(
        window.folder_tree.viewport(),
        QEvent.Type.MouseButtonPress,
        Qt.MouseButton.ForwardButton,
    )
    send_extra_button(
        window.folder_tree.viewport(),
        QEvent.Type.MouseButtonRelease,
        Qt.MouseButton.ForwardButton,
    )
    finish_scan(window, qapp)
    assert window.current_path == third.absolute()

    send_extra_button(
        window.address_bar,
        QEvent.Type.MouseButtonPress,
        Qt.MouseButton.BackButton,
    )
    send_extra_button(
        window.address_bar,
        QEvent.Type.MouseButtonRelease,
        Qt.MouseButton.BackButton,
    )
    assert window.current_path == third.absolute()
    window.close()
    qapp.processEvents()


def test_extra_buttons_without_history_do_nothing(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "only"
    folder.mkdir()
    window = make_window(tmp_path, folder, qapp)

    send_extra_button(
        window.list_view.viewport(),
        QEvent.Type.MouseButtonPress,
        Qt.MouseButton.BackButton,
    )
    send_extra_button(
        window.list_view.viewport(),
        QEvent.Type.MouseButtonRelease,
        Qt.MouseButton.BackButton,
    )

    assert window.current_path == folder.absolute()
    assert len(window.navigation_history) == 1
    window.close()
    qapp.processEvents()


def test_tree_user_navigation_records_once_and_program_sync_does_not_duplicate(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "A"
    second = tmp_path / "B"
    first.mkdir()
    second.mkdir()
    window = make_window(tmp_path, first, qapp)
    index = window.file_system_model.index(str(second))
    assert index.isValid()

    before = len(window.navigation_history)
    window.folder_tree.setCurrentIndex(index)
    window._apply_pending_tree_path()
    finish_scan(window, qapp)

    assert window.current_path == second.absolute()
    assert len(window.navigation_history) == before + 1
    assert window.folder_tree.currentIndex() == index
    window._sync_tree_to_path(second)
    assert len(window.navigation_history) == before + 1
    window.close()
    qapp.processEvents()
