from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLineEdit, QMenu

from app.browser_filter import BrowserFilterState
from tests.test_application_controller import make_controller, write_image, close_controller


@pytest.fixture
def browser_case(tmp_path, qapp):
    root = tmp_path / 'images'
    for name in ('a.png', 'b.png'):
        write_image(root / name)
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    browser.set_current_folder(root)
    assert browser.wait_for_scan()
    browser.show()
    browser.activateWindow()
    qapp.processEvents()
    try:
        yield controller, browser, root
    finally:
        close_controller(controller, qapp)


@pytest.mark.parametrize('query, rating', [('a', 'off'), ('', 'unrated'), ('a', 'unrated'), ('', 'off')])
@pytest.mark.parametrize('focus', ['search', 'list', 'rating'])
def test_escape_clears_filters_preserving_location_and_selection(browser_case, qapp, monkeypatch, query, rating, focus):
    controller, browser, root = browser_case
    browser._set_browser_filter(BrowserFilterState.normalized(search_text=query, rating_mode=rating))
    qapp.processEvents()
    index = browser.item_model.index(browser.item_model.row_for_path(root / 'a.png'), 0)
    browser.list_view.setCurrentIndex(index)
    qapp.processEvents()
    generation = browser._scan_generation
    view_state = browser._capture_list_view_state()
    restore = Mock(wraps=browser._schedule_list_view_state_restore)
    monkeypatch.setattr(browser, '_schedule_list_view_state_restore', restore)
    opened = Mock()
    monkeypatch.setattr(controller, 'open_path', opened)
    widget = {'search': browser.browser_search_edit, 'list': browser.list_view,
              'rating': browser.rating_filter_widget}[focus]
    widget.setFocus()
    qapp.processEvents()
    QTest.keyClick(widget, Qt.Key.Key_Escape)
    QTest.qWait(150)
    assert browser.browser_filter_state == BrowserFilterState.normalized()
    assert browser.browser_search_edit.text() == ''
    assert browser.item_model.rowCount() == 2
    assert browser.current_path == root
    assert browser._scan_generation == generation
    if query or rating != 'off':
        restore.assert_called_once_with(view_state)
    else:
        restore.assert_not_called()
    assert {browser.item_model.item_at(i).path for i in browser.list_view.selectionModel().selectedIndexes()} == {root / 'a.png'}
    opened.assert_not_called()


def test_escape_cancels_pending_query_once(browser_case, qapp, monkeypatch):
    _, browser, _ = browser_case
    configure = Mock(wraps=browser.item_model.configure_filter)
    monkeypatch.setattr(browser.item_model, 'configure_filter', configure)
    browser.browser_search_edit.setText('pending')
    assert browser._browser_search_timer.isActive()
    browser.list_view.setFocus()
    QTest.keyClick(browser.list_view, Qt.Key.Key_Escape)
    QTest.qWait(150)
    assert not browser._browser_search_timer.isActive()
    assert browser.browser_search_edit.text() == ''
    assert browser.item_model.rowCount() == 2
    configure.assert_not_called()  # Pending query never reached the model.


def test_escape_preserves_popup_dialog_and_address_edit_priority(browser_case, qapp):
    _, browser, _ = browser_case
    state = BrowserFilterState.normalized(search_text='a', rating_mode='unrated')
    browser._set_browser_filter(state)
    menu = QMenu(browser)
    menu.addAction('Example')
    menu.popup(browser.mapToGlobal(browser.rect().center()))
    qapp.processEvents()
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    assert browser.browser_filter_state == state
    dialog = QDialog(browser)
    edit = QLineEdit(dialog)
    dialog.setModal(True)
    dialog.show()
    edit.setFocus()
    qapp.processEvents()
    QTest.keyClick(edit, Qt.Key.Key_Escape)
    assert browser.browser_filter_state == state
    dialog.close()
    browser.focus_address_bar()
    browser.address_bar.setText('cancel this edit')
    QTest.keyClick(browser.address_bar, Qt.Key.Key_Escape)
    assert browser.browser_filter_state == state


def test_escape_invalidates_saved_viewer_return_query(browser_case, qapp):
    from tests.test_application_controller import activate_browser_search, finish_viewer_open
    controller, browser, root = browser_case
    activate_browser_search(browser, 'a', persist=True)
    browser.open_item(browser.item_model.index(browser.item_model.row_for_path(root / 'a.png'), 0))
    finish_viewer_open(qapp, controller.get_active_viewer())
    assert controller._viewer_search_return_context is not None
    browser._set_browser_filter(BrowserFilterState.normalized(rating_mode='unrated'))
    # Exercise explicit clearing while the return query is still hidden;
    # activating Browser first would restore it before the Escape key.
    browser.clear_browser_filters()
    assert controller._viewer_search_return_context is None
    controller._on_browser_activated(browser)
    assert browser.browser_filter_state == BrowserFilterState.normalized()
    assert 'a' in browser.search_history.entries
