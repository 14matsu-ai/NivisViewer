from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMenu, QPushButton

from app.browser_filter import BrowserFilterState
from app.browser_pointer_controller import BrowserPointerState
from app.browser_window import BROWSER_SHORTCUT_RUNTIME_IDS
from app.config_manager import ConfigManager
from app.shortcut_catalog import SPECS_BY_SCOPE
from app.settings_dialog import SettingsDialog
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


def test_filter_restore_cache_tracks_live_folder_cache_setting(browser_case, qapp):
    controller, browser, _ = browser_case
    controller.config.apply({'browser_folder_snapshot_cache_enabled': True})
    original = browser.item_model.items
    browser._set_browser_filter(BrowserFilterState.normalized(search_text='a'))
    rebuild = Mock(wraps=browser.item_model.visible_items)
    original_method = browser.item_model.visible_items
    browser.item_model.visible_items = rebuild
    try:
        browser.clear_browser_filters()
        rebuild.assert_not_called()
        assert browser.item_model.items == original
        browser._set_browser_filter(BrowserFilterState.normalized(search_text='a'))
        controller.config.apply({'browser_folder_snapshot_cache_enabled': False})
        rebuild.reset_mock()
        browser.clear_browser_filters()
        rebuild.assert_called_once()
        assert browser.item_model.items == original
    finally:
        browser.item_model.visible_items = original_method


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


def test_browser_open_selection_keeps_return_and_keypad_enter_and_supports_remap(
    browser_case, qapp
):
    _, browser, root = browser_case
    index = browser.item_model.index(browser.item_model.row_for_path(root / 'a.png'), 0)
    browser.list_view.setCurrentIndex(index)
    opened = Mock()
    browser.open_item = opened

    assert set(browser.shortcut_bindings["browser_open_selection"]) == {
        "Return", "Enter"
    }
    QTest.keyClick(browser.list_view, Qt.Key.Key_Return)
    QTest.keyClick(browser.list_view, Qt.Key.Key_Enter)
    assert opened.call_count == 2

    browser.shortcut_bindings["browser_open_selection"] = ["O"]
    browser._apply_browser_shortcuts()
    QTest.keyClick(browser.list_view, Qt.Key.Key_Return)
    QTest.keyClick(browser.list_view, Qt.Key.Key_Enter)
    assert opened.call_count == 2
    QTest.keyClick(browser.list_view, Qt.Key.Key_O)
    assert opened.call_count == 3


def test_browser_catalog_actions_have_runtime_owners():
    catalog_ids = {spec.action_id for spec in SPECS_BY_SCOPE['browser']}
    assert catalog_ids == BROWSER_SHORTCUT_RUNTIME_IDS


def test_clear_filters_binding_is_saved_reloaded_and_runs_on_list_focus(
    browser_case, qapp,
):
    controller, browser, root = browser_case
    controller.config.apply({"browser_cancel_clears_filters": False})
    dialog = SettingsDialog(browser.config)
    try:
        editor = dialog.shortcut_editors[('browser', 'browser_clear_filters')][0]
        editor.setKeySequence(QKeySequence('Ctrl+K'))
        changed = dialog.apply_settings()
        assert 'shortcut_bindings' in changed
    finally:
        dialog.reject()

    restored = ConfigManager(browser.config.path)
    restored.load()
    assert restored.get('shortcut_bindings')['browser']['browser_clear_filters'] == ['Ctrl+K']
    browser._set_browser_filter(
        BrowserFilterState.normalized(search_text='a', rating_mode='unrated')
    )
    browser.list_view.setFocus()
    qapp.processEvents()
    QTest.keyClick(browser.list_view, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert browser.browser_filter_state == BrowserFilterState.normalized()
    assert browser.item_model.rowCount() == 2
    assert browser.current_path == root

    browser._set_browser_filter(BrowserFilterState.normalized(search_text='a'))
    edit = QLineEdit(browser)
    edit.show()
    edit.setFocus()
    qapp.processEvents()
    try:
        QTest.keyClick(edit, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
        assert browser.browser_filter_state.search_text == 'a'
    finally:
        edit.close()


def test_clear_filters_binding_toggles_with_cancel_filter_scope(
    browser_case, qapp,
):
    controller, browser, root = browser_case
    controller.config.apply({
        "browser_cancel_clears_filters": True,
        "shortcut_bindings": {
            "browser": {"browser_clear_filters": ["Ctrl+K"]},
        },
    })
    browser._set_browser_filter(BrowserFilterState.normalized(search_text="a"))
    browser.list_view.setFocus()
    qapp.processEvents()
    QTest.keyClick(browser.list_view, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert browser.browser_filter_state.search_text == "a"

    controller.config.apply({"browser_cancel_clears_filters": False})
    browser._set_browser_filter(BrowserFilterState.normalized(search_text="a"))
    browser.list_view.setFocus()
    qapp.processEvents()
    QTest.keyClick(browser.list_view, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert browser.browser_filter_state == BrowserFilterState.normalized()

    controller.config.apply({"browser_cancel_clears_filters": True})
    browser._set_browser_filter(BrowserFilterState.normalized(search_text="a"))
    browser.list_view.setFocus()
    qapp.processEvents()
    QTest.keyClick(browser.list_view, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert browser.browser_filter_state.search_text == "a"
    assert browser.config.get("shortcut_bindings")["browser"]["browser_clear_filters"] == ["Ctrl+K"]

    controller.config.apply({"browser_cancel_clears_filters": False})
    browser._set_browser_filter(BrowserFilterState.normalized(search_text="a"))
    browser._clipboard_paths = (str(root / "a.png"),)
    browser.list_view.setFocus()
    qapp.processEvents()
    QTest.keyClick(browser.list_view, Qt.Key.Key_Escape)
    assert browser.browser_filter_state.search_text == "a"
    assert not browser._clipboard_paths


def test_applied_cancel_filter_option_clears_filters_from_search_focus(
    browser_case, qapp,
):
    controller, browser, _ = browser_case
    controller.config.apply({"browser_cancel_clears_filters": False})
    dialog = SettingsDialog(browser.config)
    try:
        dialog.browser_cancel_clears_filters_checkbox.setChecked(True)
        changed = dialog.apply_settings()
        assert changed["browser_cancel_clears_filters"] is True
    finally:
        dialog.reject()
    assert browser.browser_cancel_clears_filters is True

    browser.browser_search_edit.setText("a")
    browser.browser_search_edit.setFocus()
    qapp.processEvents()
    QTest.keyClick(browser.browser_search_edit, Qt.Key.Key_Escape)
    assert browser.browser_filter_state == BrowserFilterState.normalized()
    assert browser.browser_search_edit.text() == ""


def test_applied_cancel_filter_option_clears_filters_across_browser_focus_surfaces(
    browser_case, qapp,
):
    controller, browser, _ = browser_case
    controller.config.apply({"browser_cancel_clears_filters": False})
    dialog = SettingsDialog(browser.config)
    try:
        dialog.browser_cancel_clears_filters_checkbox.setChecked(True)
        dialog.apply_settings()
    finally:
        dialog.reject()

    def press_cancel(widget, state):
        browser._set_browser_filter(state)
        widget.setFocus()
        qapp.processEvents()
        QTest.keyClick(widget, Qt.Key.Key_Escape)
        assert browser.browser_filter_state == BrowserFilterState.normalized()

    press_cancel(
        browser.list_view,
        BrowserFilterState.normalized(search_text="a"),
    )
    if browser.folder_tree.isVisible():
        press_cancel(
            browser.folder_tree,
            BrowserFilterState.normalized(search_text="a"),
        )
    press_cancel(
        browser.browser_search_edit,
        BrowserFilterState.normalized(search_text="a"),
    )
    press_cancel(
        browser.rating_filter_widget,
        BrowserFilterState.normalized(rating_mode="unrated"),
    )

    controller.config.apply({
        "browser_tag_registry": [{"name": "分類", "color": "#80bfff"}],
        "browser_tag_grouped": False,
    })
    qapp.processEvents()
    tag_button = browser.tag_quick_filter_strip._buttons["分類"]
    press_cancel(
        tag_button,
        BrowserFilterState.normalized(include_tags=("分類",)),
    )


def test_tag_quick_button_escape_clears_without_selection_or_manual_focus(
    browser_case, qapp,
):
    controller, browser, _ = browser_case
    controller.config.apply({
        "browser_tag_registry": [{"name": "分類", "color": "#80bfff"}],
        "browser_tag_grouped": False,
        "browser_cancel_clears_filters": True,
    })
    qapp.processEvents()
    button = browser.tag_quick_filter_strip._buttons["分類"]
    browser.folder_tree.setFocus()
    qapp.processEvents()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert browser.browser_filter_state.include_tags == ("分類",)
    receiver = QApplication.focusWidget()
    assert receiver is button
    QTest.keyClick(receiver, Qt.Key.Key_Escape)
    assert browser.browser_filter_state == BrowserFilterState.normalized()
    assert not browser.list_view.selectionModel().selectedIndexes()

    editor = QLineEdit(browser)
    editor.setGeometry(8, 8, 120, 24)
    editor.show()
    browser._set_browser_filter(BrowserFilterState.normalized())
    QTimer.singleShot(0, editor.setFocus)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    QTest.qWait(80)
    assert QApplication.focusWidget() is editor
    editor.deleteLater()


def test_tag_quick_focus_retry_does_not_steal_new_user_focus(
    browser_case, qapp,
):
    controller, browser, _ = browser_case
    controller.config.apply({
        "browser_tag_registry": [
            {"name": "分類", "color": "#80bfff"},
            {"name": "人物", "color": "#ffbf80"},
        ],
        "browser_tag_grouped": False,
        "browser_cancel_clears_filters": True,
    })
    qapp.processEvents()
    strip = browser.tag_quick_filter_strip
    first = strip._buttons["分類"]
    second = strip._buttons["人物"]

    for widget in (browser.list_view, browser.folder_tree, browser.browser_search_edit):
        browser._set_browser_filter(BrowserFilterState.normalized())
        QTest.mouseClick(first, Qt.MouseButton.LeftButton)
        widget.setFocus()
        qapp.processEvents()
        QTest.qWait(80)
        assert QApplication.focusWidget() is widget

    browser._set_browser_filter(BrowserFilterState.normalized())
    QTest.mouseClick(first, Qt.MouseButton.LeftButton)
    QTest.mouseClick(second, Qt.MouseButton.LeftButton)
    QTest.qWait(80)
    assert QApplication.focusWidget() is second
    assert browser.browser_filter_state.include_tags == ("分類", "人物")

    other = QDialog()
    other_edit = QLineEdit(other)
    other.setWindowModality(Qt.WindowModality.NonModal)
    other.show()
    other.activateWindow()
    other_edit.setFocus()
    qapp.processEvents()
    browser._set_browser_filter(BrowserFilterState.normalized())
    QTest.mouseClick(first, Qt.MouseButton.LeftButton)
    other.activateWindow()
    other_edit.setFocus()
    QTest.qWait(80)
    assert QApplication.focusWidget() is other_edit
    other.close()

    browser._set_browser_filter(BrowserFilterState.normalized())
    QTest.mouseClick(first, Qt.MouseButton.LeftButton)
    modal = QDialog(browser)
    modal.setWindowModality(Qt.WindowModality.ApplicationModal)
    modal_edit = QLineEdit(modal)
    modal.show()
    modal_edit.setFocus()
    qapp.processEvents()
    QTest.qWait(80)
    assert QApplication.focusWidget() is modal_edit
    modal.close()


@pytest.mark.parametrize('open_with_keyboard', [False, True])
def test_grouped_tag_menu_and_filter_dialog_escape_after_apply(
    browser_case, qapp, open_with_keyboard,
):
    controller, browser, _ = browser_case
    controller.config.apply({
        "browser_tag_registry": [{"name": "分類", "color": "#80bfff"}],
        "browser_tag_grouped": True,
        "browser_cancel_clears_filters": True,
    })
    qapp.processEvents()

    menu_errors = []

    def choose_grouped_tag():
        try:
            action = next(
                action
                for action in browser.tag_menu.actions()
                if action.text() == "分類"
            )
            QTest.mouseClick(
                browser.tag_menu,
                Qt.MouseButton.LeftButton,
                pos=browser.tag_menu.actionGeometry(action).center(),
            )
        except Exception as exc:
            menu_errors.append(exc)

    QTimer.singleShot(0, choose_grouped_tag)
    if open_with_keyboard:
        browser.tag_button.setFocus()
        QTest.keyClick(browser.tag_button, Qt.Key.Key_Space)
    else:
        QTest.mouseClick(browser.tag_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not menu_errors, menu_errors
    assert browser.browser_filter_state.include_tags == ("分類",)
    receiver = QApplication.focusWidget()
    assert receiver is browser.tag_button
    QTest.keyClick(receiver, Qt.Key.Key_Escape)
    assert browser.browser_filter_state == BrowserFilterState.normalized()

    dialog_errors = []

    def apply_tag_filter_dialog():
        dialog = QApplication.activeModalWidget()
        try:
            assert dialog is not None
            dialog.controls["分類"].setCurrentIndex(1)
            QTest.keyClick(dialog, Qt.Key.Key_Return)
        except Exception as exc:
            dialog_errors.append(exc)
            if dialog is not None:
                dialog.reject()

    QTimer.singleShot(0, apply_tag_filter_dialog)
    browser.edit_tag_filter()
    qapp.processEvents()
    assert not dialog_errors, dialog_errors
    assert browser.browser_filter_state.include_tags == ("分類",)
    receiver = QApplication.focusWidget()
    assert receiver is browser.tag_button
    QTest.keyClick(receiver, Qt.Key.Key_Escape)
    assert browser.browser_filter_state == BrowserFilterState.normalized()

    # Applying a dialog must not leave a delayed focus restoration behind.
    QTimer.singleShot(0, apply_tag_filter_dialog)
    browser.edit_tag_filter()
    browser.list_view.setFocus()
    QTest.qWait(80)
    assert QApplication.focusWidget() is browser.list_view


@pytest.mark.parametrize('next_focus', ['list_view', 'folder_tree', 'browser_search_edit'])
def test_tag_overflow_menu_restores_focus_for_escape_without_selection(
    browser_case, qapp, next_focus,
):
    controller, browser, _ = browser_case
    registry = [
        {"name": f"分類{i:02d}", "color": "#80bfff"}
        for i in range(12)
    ]
    controller.config.apply({
        "browser_tag_registry": registry,
        "browser_tag_grouped": False,
        "browser_cancel_clears_filters": True,
    })
    browser.resize(640, 600)
    qapp.processEvents()
    strip = browser.tag_quick_filter_strip
    assert strip._overflow.isVisible()
    target_name = strip._overflow_menu.actions()[-1].text()
    menu_errors = []

    def choose_overflow_tag():
        try:
            action = next(
                action
                for action in strip._overflow_menu.actions()
                if action.text() == target_name
            )
            QTest.mouseClick(
                strip._overflow_menu,
                Qt.MouseButton.LeftButton,
                pos=strip._overflow_menu.actionGeometry(action).center(),
            )
        except Exception as exc:
            menu_errors.append(exc)

    browser.folder_tree.setFocus()
    QTimer.singleShot(0, choose_overflow_tag)
    QTest.mouseClick(strip._overflow, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    QTest.qWait(80)
    assert not menu_errors, menu_errors
    assert browser.browser_filter_state.include_tags == (target_name,)
    assert QApplication.focusWidget() is strip._overflow
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    assert browser.browser_filter_state == BrowserFilterState.normalized()
    assert not browser.list_view.selectionModel().selectedIndexes()

    browser._set_browser_filter(BrowserFilterState.normalized())
    browser.folder_tree.setFocus()
    qapp.processEvents()
    QTimer.singleShot(0, choose_overflow_tag)
    QTest.mouseClick(strip._overflow, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    next_widget = getattr(browser, next_focus)
    next_widget.setFocus()
    QTest.qWait(80)
    assert QApplication.focusWidget() is next_widget


def test_tag_updates_preserve_focused_button_and_menu_action_identity(browser_case, qapp):
    controller, browser, _ = browser_case
    controller.config.apply({
        'browser_tag_registry': [
            {'name': f'Tag{i}', 'color': '#80bfff'} for i in range(20)
        ],
        'browser_tag_grouped': False,
    })
    browser.resize(1400, 700)
    qapp.processEvents()
    strip = browser.tag_quick_filter_strip
    button = next(button for button in strip._buttons.values() if button.isVisible())

    class VisibilityProbe(QObject):
        def __init__(self):
            super().__init__()
            self.hidden = 0

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Hide:
                self.hidden += 1
            return False

    probe = VisibilityProbe()
    button.installEventFilter(probe)
    before = tuple(strip._overflow_menu.actions())
    assert before
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert QApplication.focusWidget() is button
    assert probe.hidden == 0
    assert tuple(strip._overflow_menu.actions()) == before
    QTest.keyClick(button, Qt.Key.Key_Escape)
    assert probe.hidden == 0
    assert browser.browser_filter_state == BrowserFilterState.normalized()


def test_cancel_does_not_leak_from_nonmodal_child_dialog(browser_case, qapp):
    _, browser, _ = browser_case
    state = BrowserFilterState.normalized(search_text='a')
    browser._set_browser_filter(state)
    dialog = QDialog(browser)
    button = QPushButton('Close', dialog)
    dialog.show()
    dialog.activateWindow()
    button.setFocus()
    qapp.processEvents()
    QTest.keyClick(button, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    assert browser.browser_filter_state == state


@pytest.mark.parametrize('binding,key,modifiers', [
    ('T', Qt.Key.Key_T, Qt.KeyboardModifier.NoModifier),
    ('Ctrl+K', Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier),
])
def test_remapped_cancel_immediately_after_tag_click(browser_case, qapp, binding, key, modifiers):
    controller, browser, _ = browser_case
    controller.config.apply({
        'browser_tag_registry': [{'name': '分類', 'color': '#80bfff'}],
        'browser_tag_grouped': False,
        'shortcut_bindings': {'browser': {'browser_cancel': [binding]}},
    })
    qapp.processEvents()
    button = browser.tag_quick_filter_strip._buttons['分類']
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert QApplication.focusWidget() is button
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    assert browser.browser_filter_state.include_tags == ('分類',)
    QTest.keyClick(QApplication.focusWidget(), key, modifiers)
    assert browser.browser_filter_state == BrowserFilterState.normalized()


def test_browser_cancel_routes_clipboard_candidates_and_old_escape_is_removed(
    browser_case, qapp
):
    _, browser, root = browser_case
    index = browser.item_model.index(browser.item_model.row_for_path(root / 'a.png'), 0)
    browser.list_view.setCurrentIndex(index)
    browser._clipboard_paths = (str(root / 'a.png'),)
    browser.shortcut_bindings["browser_cancel"] = ["T"]
    browser._apply_browser_shortcuts()

    QTest.keyClick(browser.list_view, Qt.Key.Key_Escape)
    assert browser._clipboard_paths
    QTest.keyClick(browser.list_view, Qt.Key.Key_T)
    assert not browser._clipboard_paths

    dialog = QDialog(browser)
    dialog.setModal(True)
    browser._clipboard_paths = (str(root / 'a.png'),)
    edit = QLineEdit(dialog)
    dialog.show()
    edit.setFocus()
    qapp.processEvents()
    QTest.keyClick(edit, Qt.Key.Key_T)
    assert browser._clipboard_paths
    dialog.close()


def test_browser_cancel_uses_list_interaction_api_and_respects_remap(
    browser_case, qapp, monkeypatch,
):
    _, browser, root = browser_case
    browser._set_browser_filter(
        BrowserFilterState.normalized(search_text='a', rating_mode='unrated')
    )
    browser._clipboard_paths = (str(root / 'a.png'),)
    browser.shortcut_bindings['browser_cancel'] = ['T']
    browser._apply_browser_shortcuts()
    browser.list_view.setFocus()
    qapp.processEvents()
    clear_filters = Mock(wraps=browser.clear_browser_filters)
    clear_clipboard = Mock(wraps=browser.clear_file_clipboard)
    monkeypatch.setattr(browser, 'clear_browser_filters', clear_filters)
    monkeypatch.setattr(browser, 'clear_file_clipboard', clear_clipboard)

    browser.list_view._folder_gesture_right_button_down = True
    browser.list_view._folder_gesture_recognizer.begin((0, 0))
    assert browser.list_view.has_active_interaction()
    QTest.keyClick(browser.list_view, Qt.Key.Key_Escape)
    assert browser.list_view.has_active_interaction()
    assert browser.browser_filter_state.search_text == 'a'
    assert browser._clipboard_paths
    clear_filters.assert_not_called()
    clear_clipboard.assert_not_called()

    QTest.keyClick(browser.list_view, Qt.Key.Key_T)
    assert not browser.list_view.has_active_interaction()
    assert browser.browser_filter_state.search_text == 'a'
    assert browser._clipboard_paths
    clear_filters.assert_not_called()
    clear_clipboard.assert_not_called()

    index = browser.item_model.index(browser.item_model.row_for_path(root / 'a.png'), 0)
    browser.list_view.pointer_controller.begin(
        position=QPoint(1, 1),
        global_position=QPoint(1, 1),
        modifiers=Qt.KeyboardModifier.NoModifier,
        row=index.row(),
        path=str(root / 'a.png'),
        was_selected=False,
        selection_snapshot=(),
        current_path=str(root / 'a.png'),
        anchor_path=None,
    )
    assert browser.list_view.pointer_controller.state is BrowserPointerState.PRESSED_ON_ITEM
    QTest.keyClick(browser.list_view, Qt.Key.Key_T)
    assert browser.list_view.pointer_controller.state is BrowserPointerState.CANCELLED
    assert browser.browser_filter_state.search_text == 'a'
    assert browser._clipboard_paths
    clear_filters.assert_not_called()
    clear_clipboard.assert_not_called()


def test_browser_cancel_clears_filters_before_clipboard_candidates(
    browser_case, qapp,
):
    _, browser, root = browser_case
    browser._set_browser_filter(
        BrowserFilterState.normalized(search_text='a', rating_mode='unrated')
    )
    browser._clipboard_paths = (str(root / 'a.png'),)
    browser.list_view.setFocus()
    qapp.processEvents()
    QTest.keyClick(browser.list_view, Qt.Key.Key_Escape)
    assert browser.browser_filter_state == BrowserFilterState.normalized()
    assert browser._clipboard_paths
    QTest.keyClick(browser.list_view, Qt.Key.Key_Escape)
    assert not browser._clipboard_paths


def test_browser_cancel_reaches_quick_tag_button_after_click_and_remap(
    browser_case, qapp,
):
    controller, browser, _ = browser_case
    controller.config.apply({
        'browser_tag_registry': [{'name': '分類', 'color': '#80bfff'}],
        'browser_tag_grouped': False,
    })
    qapp.processEvents()
    button = browser.tag_quick_filter_strip._buttons['分類']
    assert button.isVisible()

    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    button.setFocus(Qt.FocusReason.MouseFocusReason)
    qapp.processEvents()
    assert QApplication.focusWidget() is button
    assert browser.browser_filter_state.include_tags == ('分類',)
    assert browser.item_model.rowCount() == 0
    QTest.keyClick(button, Qt.Key.Key_Escape)
    assert browser.browser_filter_state == BrowserFilterState.normalized()

    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    browser.shortcut_bindings['browser_cancel'] = ['T']
    browser._apply_browser_shortcuts()
    QTest.keyClick(button, Qt.Key.Key_Escape)
    assert browser.browser_filter_state.include_tags == ('分類',)
    QTest.keyClick(button, Qt.Key.Key_T)
    assert browser.browser_filter_state == BrowserFilterState.normalized()


def test_browser_cancel_reaches_grouped_tag_button_and_popup_is_first(
    browser_case, qapp,
):
    controller, browser, _ = browser_case
    controller.config.apply({
        'browser_tag_registry': [{'name': '分類', 'color': '#80bfff'}],
        'browser_tag_grouped': True,
    })
    qapp.processEvents()
    button = browser.tag_button
    browser._set_browser_filter(
        BrowserFilterState.normalized(include_tags=('分類',))
    )
    popup_seen = []

    def close_popup_from_key() -> None:
        popup_seen.append(QApplication.activePopupWidget() is browser.tag_menu)
        QTest.keyClick(browser.tag_menu, Qt.Key.Key_Escape)

    QTimer.singleShot(50, close_popup_from_key)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert popup_seen == [True]
    assert QApplication.activePopupWidget() is None
    assert browser.browser_filter_state.include_tags == ('分類',)
    button.setFocus(Qt.FocusReason.MouseFocusReason)
    browser.shortcut_bindings['browser_cancel'] = ['T']
    browser._apply_browser_shortcuts()
    QTest.keyClick(button, Qt.Key.Key_Escape)
    assert browser.browser_filter_state.include_tags == ('分類',)
    QTest.keyClick(button, Qt.Key.Key_T)
    assert browser.browser_filter_state == BrowserFilterState.normalized()


def test_browser_cancel_tag_control_does_not_cross_modal_editor(
    browser_case, qapp,
):
    controller, browser, _ = browser_case
    controller.config.apply({
        'browser_tag_registry': [{'name': '分類', 'color': '#80bfff'}],
        'browser_tag_grouped': False,
    })
    qapp.processEvents()
    button = browser.tag_quick_filter_strip._buttons['分類']
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert browser.browser_filter_state.include_tags == ('分類',)
    dialog = QDialog(browser)
    edit = QLineEdit(dialog)
    dialog.setModal(True)
    dialog.show()
    edit.setFocus()
    qapp.processEvents()
    QTest.keyClick(edit, Qt.Key.Key_Escape)
    assert browser.browser_filter_state.include_tags == ('分類',)
    dialog.close()


def test_browser_cancel_key_stays_editable_for_inline_list_editor(
    browser_case, qapp,
):
    controller, browser, _ = browser_case
    controller.config.apply({
        'browser_tag_registry': [{'name': '分類', 'color': '#80bfff'}],
        'browser_tag_grouped': False,
    })
    qapp.processEvents()
    tag_button = browser.tag_quick_filter_strip._buttons['分類']
    QTest.mouseClick(tag_button, Qt.MouseButton.LeftButton)
    assert browser.browser_filter_state.include_tags == ('分類',)
    browser.shortcut_bindings['browser_cancel'] = ['T']
    browser._apply_browser_shortcuts()
    editor = QLineEdit(browser.list_view.viewport())
    editor.setGeometry(4, 4, 120, 24)
    editor.show()
    editor.setFocus()
    qapp.processEvents()
    before_history = tuple(browser.search_history.entries)
    QTest.keyClick(editor, Qt.Key.Key_T)
    assert editor.text()
    assert browser.browser_filter_state.include_tags == ('分類',)
    assert tuple(browser.search_history.entries) == before_history
    editor.deleteLater()
