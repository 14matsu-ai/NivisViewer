from pathlib import Path

import pytest
from shiboken6 import isValid
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from app.browser_location_bar import BrowserLocationListPopup, LocationPopupEntry
from app.browser_filter import BrowserFilterState, RatingFilterMode
from app.browser_search_history import BrowserSearchHistory
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.i18n import install_ui_language, tr
from app.metadata_store import MetadataStore
from tests.test_browser_interaction_regressions import send_wheel
from tests.test_browser_navigation_window import write_image


@pytest.fixture
def browser(tmp_path, qapp, monkeypatch):
    folder = tmp_path / "books"
    for name in ("Alpha.png", "Beta.png", "背景.png"):
        write_image(folder / name)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply({"last_browser_path": str(folder), "browser_search_history": ["Alpha", "Beta", "背景"]}, save=True)
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.record_book_opened(str(folder), item_type="folder", start_page_index=0, total_pages=1)
    file_deletes = []
    monkeypatch.setattr(BrowserWindow, "move_selected_to_recycle_bin", lambda self: file_deletes.append(True))
    window = BrowserWindow(config_manager=config, metadata_store=store)
    window.file_delete_calls = file_deletes
    window.resize(640, 420)
    window.show()
    assert window.wait_for_scan()
    qapp.processEvents()
    window.list_view.setCurrentIndex(window.item_model.index(0, 0))
    try:
        yield window
    finally:
        qapp.processEvents()
        if isValid(window):
            window.close()
        qapp.processEvents()
        store.close()


def right_click(popup, row):
    point = popup.list_widget.visualItemRect(popup.list_widget.item(row)).center()
    QTest.mouseClick(popup.list_widget.viewport(), Qt.MouseButton.RightButton, pos=point)


def saved_history(browser):
    return ConfigManager(browser.config.path).load()["browser_search_history"]


@pytest.mark.parametrize("query", [" fOO ", "STRASSE", "背景"])
def test_owner_removal_normalizes_casefold_without_reordering(query):
    history = BrowserSearchHistory(("Foo", "Straße", "背景", "last"))
    expected = tuple(value for value in history.entries if value.casefold() != query.strip().casefold())
    assert history.remove(query)
    assert history.entries == expected
    assert not history.remove(query)
    for invalid in (None, 3, "", "  ", "absent"):
        assert not history.remove(invalid)
    assert history.entries == expected


def test_right_click_delete_repeated_last_persists_without_search_or_file_changes(browser, qapp):
    browser.browser_search_edit.setText("Alpha")
    browser._commit_browser_search_history()
    before_filter = browser.browser_filter_state
    before_selection = browser.list_view.currentIndex().data()
    before_location = browser.navigation_history.recent_unique()
    files = {path: path.read_bytes() for path in browser.current_path.iterdir()}
    popup = browser._show_search_history_popup()
    activated = []
    popup.entryActivated.connect(activated.append)
    right_click(popup, 1)
    assert popup.list_widget.currentItem().text() == "Beta"
    assert popup.isVisible() and not activated
    QTest.keyClick(popup.list_widget, Qt.Key.Key_Delete)
    assert browser.search_history.entries == ("Alpha", "背景")
    assert saved_history(browser) == ["Alpha", "背景"]
    assert popup.isVisible() and popup.list_widget.currentItem().text() == "背景"
    assert "2" in browser.browser_search_container.toolTip()
    QTest.keyClick(popup.list_widget, Qt.Key.Key_Delete)
    assert browser.search_history.entries == ("Alpha",)
    assert popup.list_widget.currentItem().text() == "Alpha"
    QTest.keyClick(popup.list_widget, Qt.Key.Key_Delete)
    assert browser.search_history.entries == () and saved_history(browser) == []
    assert browser._search_history_popup is None
    assert browser._show_search_history_popup() is None
    assert browser.browser_filter_state == before_filter
    assert browser.browser_search_edit.text() == "Alpha"
    assert browser.list_view.currentIndex().data() == before_selection
    assert browser.navigation_history.recent_unique() == before_location
    assert browser.history_model.rowCount() == 1
    assert not activated and not browser.file_delete_calls
    assert {path: path.read_bytes() for path in files} == files


@pytest.mark.parametrize("invalid", ["separator", "clear", "empty", "no_current"])
def test_invalid_row_or_empty_right_click_cannot_delete_previous_selection(browser, invalid):
    popup = browser._show_search_history_popup()
    right_click(popup, 1)
    if invalid == "separator":
        right_click(popup, 3)
        assert popup.list_widget.currentRow() == -1
    elif invalid == "clear":
        right_click(popup, 4)
    elif invalid == "empty":
        popup.resize(popup.width(), popup.height() + 80)
        QTest.mouseClick(popup.list_widget.viewport(), Qt.MouseButton.RightButton,
                         pos=QPoint(8, popup.list_widget.viewport().height() - 5))
        assert popup.list_widget.currentRow() == -1
    else:
        popup.list_widget.clearSelection()
        popup.list_widget.setCurrentRow(-1)
    QTest.keyClick(popup.list_widget, Qt.Key.Key_Delete)
    assert browser.search_history.entries == ("Alpha", "Beta", "背景")
    assert popup.isVisible() and not browser.file_delete_calls


def test_search_popup_owns_shortcut_override_but_line_edit_delete_remains_editing(browser, qapp):
    popup = browser._show_search_history_popup()
    override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    override.ignore()
    QApplication.sendEvent(popup.list_widget, override)
    assert override.isAccepted()
    # A real application-wide Delete shortcut would expose accidental propagation.
    fired = []
    shortcut = QShortcut(QKeySequence("Delete"), browser)
    shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
    shortcut.activated.connect(lambda: fired.append(True))
    right_click(popup, 1)
    QTest.keyClick(popup.list_widget, Qt.Key.Key_Delete)
    assert browser.search_history.entries == ("Alpha", "背景")
    assert fired == [] and browser.file_delete_calls == []
    popup.close()
    browser.browser_search_edit.setText("abcd")
    browser.browser_search_edit.setCursorPosition(1)
    browser.browser_search_edit.setFocus()
    QTest.keyClick(browser.browser_search_edit, Qt.Key.Key_Delete)
    assert browser.browser_search_edit.text() == "acd"
    assert fired == [] and browser.file_delete_calls == []


@pytest.mark.parametrize("query_state", ["empty", "middle", "selected"])
def test_last_delete_autorepeat_does_not_flow_to_file_delete(browser, qapp, query_state):
    browser.config.apply({"browser_search_history": ["Alpha"]}, save=True)
    if query_state != "empty":
        browser.browser_search_edit.setText("Alpha")
        browser._commit_browser_search_history()
        if query_state == "middle":
            browser.browser_search_edit.setCursorPosition(2)
        else:
            browser.browser_search_edit.selectAll()
    original_query = browser.browser_search_edit.text()
    original_filter = browser.browser_filter_state
    browser.list_view.setFocus()
    qapp.processEvents()
    assert browser.list_view.hasFocus()
    popup = browser._show_search_history_popup()
    QTest.keyPress(popup.list_widget, Qt.Key.Key_Delete)
    qapp.processEvents()
    assert browser._search_history_popup is None
    browser.activateWindow()
    qapp.processEvents()
    target = QApplication.focusWidget()
    assert target is not None
    for _ in range(3):
        repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier, "", True, 1)
        QApplication.sendEvent(target, repeat)
    assert browser.file_delete_calls == []
    assert browser.browser_search_edit.text() == original_query
    assert browser.browser_filter_state == original_filter
    QTest.keyRelease(target, Qt.Key.Key_Delete)
    browser.list_view.setFocus()
    QTest.keyClick(browser.list_view, Qt.Key.Key_Delete)
    assert browser.file_delete_calls == [True]


@pytest.mark.parametrize("explicit", ["enter", "edit"])
@pytest.mark.parametrize("operation", ["single", "menu", "dropdown"])
def test_deleted_current_query_stays_deleted_across_reopen_navigation_roundtrip_and_explicit_research(
    browser, qapp, tmp_path, operation, explicit,
):
    browser.browser_search_edit.setText("Alpha")
    browser._commit_browser_search_history()
    popup = browser._show_search_history_popup()
    if operation == "single":
        right_click(popup, 0)
        QTest.keyClick(popup.list_widget, Qt.Key.Key_Delete)
        popup.close()
    elif operation == "menu":
        popup.close()
        browser.clear_search_history_action.trigger()
    else:
        popup.list_widget.setCurrentRow(4)
        QTest.keyClick(popup.list_widget, Qt.Key.Key_Return)
    assert "Alpha" not in browser.search_history.entries
    for _ in range(2):
        reopened = browser._show_search_history_popup()
        assert "Alpha" not in browser.search_history.entries
        if reopened is not None:
            reopened.close()
    other = tmp_path / "other"
    write_image(other / "other.png")
    assert browser.navigate_to(other)
    assert browser.wait_for_scan()
    assert browser.go_back()
    assert browser.wait_for_scan()
    browser.restore_viewer_roundtrip_search("ALPHA")
    reopened = browser._show_search_history_popup()
    assert "alpha" not in {entry.casefold() for entry in browser.search_history.entries}
    if reopened is not None:
        reopened.close()
    assert browser.browser_search_edit.text() == "ALPHA"
    if explicit == "enter":
        QTest.keyClick(browser.browser_search_edit, Qt.Key.Key_Return)
    else:
        browser.browser_search_edit.selectAll()
        QTest.keyClicks(browser.browser_search_edit, "Alpha")
        reopened = browser._show_search_history_popup()
        if reopened is not None:
            reopened.close()
    assert browser.search_history.entries[0].casefold() == "alpha"
    assert saved_history(browser)[0].casefold() == "alpha"


@pytest.mark.parametrize("clear", [False, True])
def test_deletion_invalidates_old_wheel_snapshot(browser, qapp, clear):
    send_wheel(qapp, browser.browser_search_edit, angle=-240)
    assert browser.active_search_query == "Beta"
    assert "Beta" in browser._search_wheel_entries
    popup = browser._show_search_history_popup()
    if clear:
        browser.clear_search_history()
    else:
        right_click(popup, 1)
        QTest.keyClick(popup.list_widget, Qt.Key.Key_Delete)
        popup.close()
    assert browser._search_wheel_entries == () and browser._search_wheel_view_state is None
    for _ in range(5):
        send_wheel(qapp, browser.browser_search_edit, angle=-120)
        if not clear:
            assert browser.active_search_query != "Beta"
    assert "Beta" not in browser.search_history.entries
    assert "Beta" not in saved_history(browser)


def test_search_clear_and_browsing_clear_are_independent_and_browsing_cancel_is_preserved(browser, monkeypatch):
    browser.config.apply({"browser_tag_registry": ["keep"]})
    browser._set_browser_filter(BrowserFilterState.normalized(
        search_text="Alpha", rating_mode=RatingFilterMode.AT_LEAST,
        rating_reference=3, include_tags=("keep",),
    ))
    assert browser.browser_filter_state.include_tags == ("keep",)
    search_filter = browser.browser_filter_state
    locations = browser.navigation_history.recent_unique()
    history_menu = next(action.menu() for action in browser.menuBar().actions() if action.text() == tr("履歴"))
    assert [action.text() for action in history_menu.actions()] == [tr("閲覧履歴をすべて消去..."), tr("検索履歴をすべて消去")]
    questions = []

    def no(*args, **kwargs):
        questions.append(True)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", no)
    history_menu.actions()[0].trigger()
    assert browser.history_model.rowCount() == 1
    assert browser.search_history.entries == ("Alpha", "Beta", "背景")
    browser.clear_search_history_action.trigger()
    assert questions == [True]
    assert browser.history_model.rowCount() == 1 and saved_history(browser) == []
    assert browser.browser_filter_state == search_filter
    assert browser.navigation_history.recent_unique() == locations
    QTest.keyClick(browser.browser_search_edit, Qt.Key.Key_Return)
    assert browser.search_history.entries == ("Alpha",)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    history_menu.actions()[0].trigger()
    assert browser.history_model.rowCount() == 0
    assert browser.search_history.entries == ("Alpha",) and saved_history(browser) == ["Alpha"]
    assert browser.browser_filter_state == search_filter


def test_deletion_survives_window_close_and_new_window(browser, qapp):
    browser.browser_search_edit.setText("Beta")
    browser._commit_browser_search_history()
    popup = browser._show_search_history_popup()
    right_click(popup, 0)
    QTest.keyClick(popup.list_widget, Qt.Key.Key_Delete)
    popup.close()
    browser.close()
    qapp.processEvents()
    config = ConfigManager(browser.config.path)
    config.load()
    restored = BrowserWindow(config_manager=config, restore_initial_location=False)
    try:
        assert "Beta" not in restored.search_history.entries
        reopened = restored._show_search_history_popup()
        assert reopened is not None
        assert "Beta" not in restored.search_history.entries
        reopened.close()
    finally:
        restored.close()
        qapp.processEvents()


@pytest.mark.parametrize("removal_enabled", [False, True])
def test_generic_popup_compatibility_and_blank_query(qapp, removal_enabled):
    parent = QWidget()
    parent.show()
    popup = BrowserLocationListPopup((LocationPopupEntry("Alpha", "Alpha", removable=True),
                                      LocationPopupEntry("blank", "  ", removable=True)),
                                     parent, compact_rows=True, allow_removal=removal_enabled)
    removed, activated = [], []
    popup.entryRemoved.connect(removed.append)
    popup.entryActivated.connect(activated.append)
    popup.show_for(parent)
    try:
        right_click(popup, 1)
        QTest.keyClick(popup.list_widget, Qt.Key.Key_Delete)
        assert popup.entry_count == 2 and not removed and not activated
        QTest.keyClick(popup.list_widget, Qt.Key.Key_Up)
        if not removal_enabled:
            QTest.keyClick(popup.list_widget, Qt.Key.Key_Delete)
            assert popup.entry_count == 2
        QTest.keyClick(popup.list_widget, Qt.Key.Key_Return)
        assert activated[0].value == "Alpha"
        assert not popup.isVisible()
    finally:
        popup.close()
        parent.close()


@pytest.mark.parametrize("language,label", [("ja", "検索履歴をすべて消去"), ("en", "Clear all search history"),
                                           ("zh-Hans", "清除全部搜索历史记录"), ("zh-Hant", "清除全部搜尋歷史記錄")])
def test_new_menu_translation(tmp_path, qapp, language, label):
    install_ui_language(language)
    config = ConfigManager(tmp_path / "language.json")
    config.load()
    window = BrowserWindow(config_manager=config, restore_initial_location=False)
    try:
        assert window.clear_search_history_action.text() == label
    finally:
        window.close()
        qapp.processEvents()
        install_ui_language("ja")
