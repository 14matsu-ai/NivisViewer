from __future__ import annotations

import os
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager


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
    window.address_bar.setFocus()
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
    QTest.mouseClick(address, Qt.MouseButton.LeftButton, pos=center)
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
