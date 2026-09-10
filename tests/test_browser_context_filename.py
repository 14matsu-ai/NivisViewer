from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, QPoint, QTimer, Qt
from PySide6.QtGui import QContextMenuEvent, QFocusEvent, QKeyEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QWidgetAction

from app.browser_filter import BrowserFilterState, RatingFilterMode
from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.thumbnail_provider import BrowserThumbnailProvider


@pytest.fixture
def browser(tmp_path, qapp):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    provider = BrowserThumbnailProvider(loader=lambda *_: None, disk_cache_enabled=False)
    window = BrowserWindow(config_manager=config, thumbnail_provider=provider, restore_initial_location=False)
    window.image_detail_probe.request = lambda *_: None
    window.resize(900, 620)
    window.show()
    QTest.keyRelease(window, Qt.Key.Key_Control, Qt.KeyboardModifier.NoModifier)
    qapp.processEvents()
    yield window
    window.close()
    provider.close()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    qapp.clipboard().clear()  # Release offscreen MIME ownership before Qt teardown.


def populate(window, tmp_path, names, kind=BrowserItemKind.IMAGE):
    items = [BrowserItem(name, tmp_path / name, kind, None, rating=3) for name in names]
    window.item_model.set_items(items)
    window.list_view.doItemsLayout()
    window.list_view.setCurrentIndex(window.item_model.index(0, 0))
    return items


def actions(menu):
    return {action.text(): action for action in menu.actions() if not isinstance(action, QWidgetAction)}


def invoke(window, monkeypatch, qapp, callback, row=0, keyboard=False):
    class InteractingMenu(QMenu):
        def exec(menu, point):
            menu.popup(point)
            qapp.processEvents()
            try:
                return callback(menu, menu.findChild(QLineEdit, "browser_context_filename"))
            finally:
                menu.hide()
    monkeypatch.setattr("app.browser_window.QMenu", InteractingMenu)
    point = window.list_view.visualRect(window.item_model.index(row, 0)).center()
    assert window.item_model.rowCount() > row, window.items
    assert window.list_view.indexAt(point).row() == row, (point, window.list_view.visualRect(window.item_model.index(row, 0)))
    window._show_context_menu(point, keyboard=keyboard)


@pytest.mark.parametrize("kind,name", [(BrowserItemKind.IMAGE, "日本語 file.jpg"),
                                      (BrowserItemKind.ARCHIVE, "book.cbz"),
                                      (BrowserItemKind.FOLDER, "Folder.Name")])
def test_single_filename_is_canonical_readonly_and_partial_copy(browser, tmp_path, qapp, monkeypatch, kind, name):
    items = populate(browser, tmp_path, [name], kind)
    browser.item_model.set_items([replace(items[0], display_name="rendered alias")])
    browser.list_view.setCurrentIndex(browser.item_model.index(0, 0))
    qapp.clipboard().setText("untouched")
    def inspect(menu, edit):
        assert isinstance(menu.actions()[0], QWidgetAction)
        assert edit.text() == name and edit.isReadOnly()
        assert qapp.clipboard().text() == "untouched"
        choices = actions(menu)
        assert not choices["選択文字をコピー"].isEnabled()
        assert not choices["選択文字で検索"].isEnabled()
        edit.setFocus()
        QTest.keyClick(edit, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert qapp.clipboard().text() == "untouched"
        edit.setSelection(1, 3)
        selected = edit.selectedText()
        assert choices["選択文字をコピー"].isEnabled()
        assert choices["選択文字で検索"].isEnabled()
        QTest.keyClick(edit, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert qapp.clipboard().text() == selected
        assert qapp.clipboard().mimeData().formats() == ["text/plain"]
        qapp.clipboard().setText("before explicit action")
        menu.setFocus()
        QApplication.sendEvent(edit, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.PopupFocusReason))
        assert edit.selectedText() == selected
        assert choices["選択文字をコピー"].isEnabled()
        return choices["選択文字をコピー"]
    invoke(browser, monkeypatch, qapp, inspect)
    assert qapp.clipboard().text() == name[1:4]
    assert qapp.clipboard().mimeData().formats() == ["text/plain"]


@pytest.mark.parametrize("literal", ['+r=3', '[abc]*?', '"quoted"', 'A OR B', '名前😀'])
def test_selected_search_uses_literal_existing_pipeline_mru_and_edit_lifecycle(browser, tmp_path, qapp, monkeypatch, literal):
    # Synthetic model names need not be legal physical Windows filenames.
    items = populate(browser, tmp_path, [f"prefix {literal} suffix.zip", "unrelated.zip"])
    browser._set_browser_filter(BrowserFilterState(rating_mode=RatingFilterMode.AT_LEAST, rating_reference=2))
    qapp.processEvents()
    browser.list_view.doItemsLayout()
    browser.list_view.setCurrentIndex(browser.item_model.index(0, 0))
    edited = QSignalSpy(browser.search_query_edited)
    browser.list_view.selectionModel().select(
        browser.item_model.index(0, 0), QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    assert browser.selected_file_operation_paths() == (str(items[0].path),), browser.items
    qapp.clipboard().setText("do not copy on search")
    def search(menu, edit):
        start = len("prefix ".encode("utf-16-le")) // 2
        edit.setSelection(start, len(literal.encode("utf-16-le")) // 2)
        assert edit.selectedText() == literal
        return actions(menu)["選択文字で検索"]
    invoke(browser, monkeypatch, qapp, search)
    assert browser.browser_search_edit.text() == literal
    assert browser.active_search_query == literal
    assert browser.items == (items[0],)
    assert browser.browser_filter_state.rating_mode == RatingFilterMode.AT_LEAST
    assert edited.count() == 1 and edited.at(0)[1] == literal
    assert browser.search_history.entries[0] == literal
    assert ConfigManager(browser.config.path).load()["browser_search_history"][0] == literal
    assert not browser._browser_search_timer.isActive()
    assert qapp.clipboard().text() == "do not copy on search"
    # Existing clear-search behavior preserves rating and MRU.
    browser.clear_active_browser_search()
    assert browser.items == tuple(items)
    assert browser.search_history.entries[0] == literal


def test_edit_shortcuts_cannot_mutate_or_invoke_browser_operations(browser, tmp_path, qapp, monkeypatch):
    populate(browser, tmp_path, ["選択 example.zip", "別の file.zip"])
    row = browser.item_model.row_for_path(tmp_path / "選択 example.zip")
    called = []
    for name in ("copy_shortcut", "cut_shortcut", "paste_shortcut", "recycle_shortcut", "rename_shortcut", "focus_address_shortcut"):
        shortcut = getattr(browser, name)
        shortcut.activated.disconnect()
        shortcut.activated.connect(lambda key=name: called.append(key))
    qapp.clipboard().setText("not a filename replacement")
    def inspect(menu, edit):
        QTest.mouseClick(edit, Qt.MouseButton.LeftButton, pos=QPoint(20, edit.height() // 2))
        edit.setFocus()
        QTest.keyClick(edit, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        assert edit.selectedText() == edit.text()
        assert len(browser.selected_file_operation_paths()) == 1
        original = edit.text()
        for key, modifiers in ((Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier),
                               (Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier),
                               (Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier),
                               (Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier),
                               (Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier),
                               (Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier),
                               (Qt.Key.Key_F2, Qt.KeyboardModifier.NoModifier),
                               (Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)):
            QTest.keyClick(edit, key, modifiers)
        QTest.keyClicks(edit, "replacement")
        assert edit.text() == original and not called
        assert menu.isVisible()
        assert qapp.clipboard().text() == "not a filename replacement"
        edit.setSelection(0, 2)
        QTest.keyClick(edit, Qt.Key.Key_Down)
        assert edit.selectedText() == "選択"
        assert menu.activeAction() == actions(menu)["選択文字をコピー"]
        QTest.keyClick(menu, Qt.Key.Key_Escape)
        assert not menu.isVisible()
    invoke(browser, monkeypatch, qapp, inspect, row=row)
    assert not called and browser.active_search_query == ""


def test_mouse_drag_selects_without_closing_menu_and_deselection_disables_actions(browser, tmp_path, qapp, monkeypatch):
    populate(browser, tmp_path, ["abcdefghijklmnopqrstuvwxyz.zip"])
    def inspect(menu, edit):
        edit.setFocus()
        edit.setCursorPosition(0)
        y = edit.height() // 2
        QTest.mousePress(edit, Qt.MouseButton.LeftButton, pos=QPoint(12, y))
        QTest.mouseMove(edit, QPoint(95, y))
        QTest.mouseRelease(edit, Qt.MouseButton.LeftButton, pos=QPoint(95, y))
        assert edit.selectedText() and menu.isVisible()
        assert actions(menu)["選択文字をコピー"].isEnabled()
        edit.deselect()
        assert not actions(menu)["選択文字をコピー"].isEnabled()
        assert not actions(menu)["選択文字で検索"].isEnabled()
    invoke(browser, monkeypatch, qapp, inspect)


def test_long_unicode_filename_is_bounded_and_fully_selectable(browser, tmp_path, qapp, monkeypatch):
    name = "長い日本語😀" * 100 + ".zip"
    populate(browser, tmp_path, [name])
    def inspect(menu, edit):
        assert edit.text() == name
        assert edit.width() <= 480 and menu.width() <= 540
        edit.setFocus()
        QTest.keyClick(edit, Qt.Key.Key_End)
        qapp.processEvents()  # QLineEdit updates its horizontal offset on paint.
        assert edit.cursorPosition() == len(name.encode("utf-16-le")) // 2
        assert 0 <= edit.cursorRect().center().x() < edit.width()
        QTest.keyClick(edit, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(edit, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert qapp.clipboard().text() == name
    invoke(browser, monkeypatch, qapp, inspect)


def test_rightclick_retargets_but_multi_selection_keeps_whole_name_copy(browser, tmp_path, qapp, monkeypatch):
    items = populate(browser, tmp_path, ["a.zip", "Folder.Name"], BrowserItemKind.FOLDER)
    QTest.keyPress(browser, Qt.Key.Key_Control)
    def single(menu, edit):
        assert edit is not None, (browser.selected_file_operation_paths(), browser.list_view.currentIndex().row(), QApplication.keyboardModifiers())
        assert edit.text() == "Folder.Name"
        assert browser.selected_file_operation_paths() == (str(items[1].path),)
    invoke(browser, monkeypatch, qapp, single, row=1)
    QTest.keyRelease(browser, Qt.Key.Key_Control)
    selection = browser.list_view.selectionModel()
    selection.select(browser.item_model.index(0, 0), QItemSelectionModel.SelectionFlag.Select)
    def multiple(menu, edit):
        assert edit is None
        assert "選択文字をコピー" not in actions(menu)
        assert "選択文字で検索" not in actions(menu)
        return actions(menu)["名前をコピー"]
    invoke(browser, monkeypatch, qapp, multiple, row=1)
    assert qapp.clipboard().text() == "a.zip\nFolder.Name"


def test_background_no_header_and_keyboard_context_targets_current_item(browser, tmp_path, qapp, monkeypatch):
    populate(browser, tmp_path, ["first.zip", "current.zip"])
    seen = []
    class RecordingMenu(QMenu):
        def exec(menu, point):
            edit = menu.findChild(QLineEdit, "browser_context_filename")
            seen.append(None if edit is None else edit.text())
            return None
    monkeypatch.setattr("app.browser_window.QMenu", RecordingMenu)
    browser._show_context_menu(QPoint(-10, -10))
    assert seen == [None]
    row = browser.item_model.row_for_path(tmp_path / "current.zip")
    browser.list_view.setCurrentIndex(browser.item_model.index(row, 0))
    event = QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(0, 0), QPoint(0, 0))
    QApplication.sendEvent(browser.list_view.viewport(), event)
    assert seen == [None, "current.zip"]


@pytest.mark.parametrize("action_label", ["選択文字をコピー", "選択文字で検索"])
def test_real_menu_action_click_preserves_selected_text(browser, tmp_path, qapp, monkeypatch, action_label):
    populate(browser, tmp_path, ["prefix foo.zip"])
    qapp.clipboard().setText("unchanged")
    errors = []

    def create_menu(parent):
        menu = QMenu(parent)
        def interact():
            try:
                edit = menu.findChild(QLineEdit, "browser_context_filename")
                edit.setFocus()
                edit.setSelection(7, 3)
                action = actions(menu)[action_label]
                QTest.mouseClick(menu, Qt.MouseButton.LeftButton,
                                 pos=menu.actionGeometry(action).center())
                assert not menu.isVisible()
            except Exception as error:
                errors.append(error)
            finally:
                menu.close()
        # Enter the real offscreen menu event loop, then interact once it
        # has laid out. This is event ordering, not a timed test sleep.
        QTimer.singleShot(0, interact)
        return menu

    monkeypatch.setattr("app.browser_window.QMenu", create_menu)
    point = browser.list_view.visualRect(browser.item_model.index(0, 0)).center()
    browser._show_context_menu(point)
    assert not errors, errors
    if action_label == "選択文字をコピー":
        assert qapp.clipboard().text() == "foo"
        assert qapp.clipboard().mimeData().formats() == ["text/plain"]
    else:
        assert browser.active_search_query == "foo"
        assert browser.search_history.entries[0] == "foo"
        assert qapp.clipboard().text() == "unchanged"
