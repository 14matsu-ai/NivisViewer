from pathlib import Path
from copy import deepcopy
from threading import Event
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from app.browser_filter import BrowserFilterState, RatingFilterMode
from app.config_manager import ConfigManager
from app.file_operation_service import FileOperationKind
from app.settings_dialog import SettingsDialog
from tests.test_browser_navigation_window import make_window, finish_scan, write_image
from tests.test_browser_file_operations import (
    make_window as operation_window, close_window, finish_operation, select_paths, write_file,
)


def send_wheel(qapp, widget, *, angle=0, pixel=0, phase=Qt.ScrollPhase.NoScrollPhase,
               global_point=None, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QWheelEvent(QPointF(8, 8), QPointF(global_point or widget.mapToGlobal(QPoint(8, 8))),
                        QPoint(0, pixel), QPoint(0, angle), Qt.MouseButton.NoButton,
                        modifiers, phase, False)
    qapp.sendEvent(widget, event)
    assert event.isAccepted()


@pytest.mark.parametrize('surface', ['edit', 'shell'])
def test_search_history_wheel_uses_stable_mru_order_and_commits_filter(tmp_path, qapp, surface):
    folder = tmp_path / '本棚'
    for name in ('alpha', 'beta', 'gamma'):
        write_image(folder / f'{name}.png')
    window = make_window(tmp_path, folder, qapp)
    try:
        window.config.apply({'browser_search_history': ['alpha', 'beta', 'gamma']})
        target = window.browser_search_edit if surface == 'edit' else window.browser_search_container
        window.browser_search_edit.setText('unfinished')
        scroll = window.list_view.verticalScrollBar().value()
        for expected, angle in (('alpha', -120), ('beta', -120), ('gamma', -120),
                                ('gamma', -120), ('beta', 120), ('alpha', 120), ('', 120)):
            send_wheel(qapp, target, angle=angle)
            assert window.browser_search_edit.text() == expected
            assert window.active_search_query == expected
            assert window.item_model.rowCount() == (1 if expected else 3)
            assert window.search_history.entries == ('alpha', 'beta', 'gamma')
            if expected:
                assert window.browser_search_edit.selectedText() == expected
        assert 'unfinished' not in window.search_history.entries
        assert window.list_view.verticalScrollBar().value() == scroll
        window.config.apply({'browser_search_history': []})
        send_wheel(qapp, target, angle=120)
        assert window.browser_search_edit.text() == ''
    finally:
        window.close()
        qapp.processEvents()


def test_search_wheel_accumulates_precise_packets_and_closes_popup(tmp_path, qapp):
    folder = tmp_path / 'books'
    write_image(folder / 'alpha.png')
    window = make_window(tmp_path, folder, qapp)
    try:
        window.config.apply({'browser_search_history': ['alpha', 'beta', 'gamma']})
        window.browser_search_edit.clear()
        for _ in range(3):
            send_wheel(qapp, window.browser_search_edit, angle=-30)
        assert window.browser_search_edit.text() == ''
        send_wheel(qapp, window.browser_search_edit, angle=-30)
        assert window.active_search_query == 'alpha'
        popup = window._show_search_history_popup()
        assert popup.isVisible()
        send_wheel(qapp, popup.list_widget.viewport(), pixel=-20,
                   global_point=window.browser_search_container.mapToGlobal(QPoint(8, 8)))
        assert window.active_search_query == 'alpha'
        send_wheel(qapp, popup.list_widget.viewport(), pixel=-20,
                   global_point=window.browser_search_container.mapToGlobal(QPoint(8, 8)))
        assert window.active_search_query == 'beta', (window._search_wheel_delta, window._search_wheel_index, window._search_wheel_entries)
        assert not popup.isVisible()
        window.browser_search_edit.setText('editing')
        send_wheel(qapp, window.browser_search_edit, angle=-120)
        assert window.active_search_query == 'alpha'  # Manual editing restarts the unchanged MRU.
        window.config.apply({'browser_search_history_limit': 0})
        send_wheel(qapp, window.browser_search_edit, angle=-240)
        assert window.active_search_query == 'alpha'
    finally:
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize('binding', ['Return', 'Enter', 'Ctrl+Return'])
def test_numeric_enter_opens_only_list_and_honors_configured_modifiers(tmp_path, qapp, binding):
    folder = tmp_path / 'books'
    write_image(folder / 'first.png')
    window = make_window(tmp_path, folder, qapp)
    try:
        bindings = deepcopy(window.config.get('shortcut_bindings'))
        bindings['browser']['browser_open_selection'] = [binding]
        window.config.apply({'shortcut_bindings': bindings})
        window.list_view.setCurrentIndex(window.item_model.index(0, 0))
        window.list_view.setFocus()
        opened = Mock()
        window.open_item = opened
        modifiers = Qt.KeyboardModifier.ControlModifier if binding.startswith('Ctrl') else Qt.KeyboardModifier.NoModifier
        QTest.keyClick(window.list_view, Qt.Key.Key_Enter, modifiers | Qt.KeyboardModifier.KeypadModifier)
        assert opened.call_count == 1
        QTest.keyClick(window.list_view, Qt.Key.Key_Return, modifiers)
        assert opened.call_count == 2
        QTest.keyClick(window.list_view, Qt.Key.Key_Enter, Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier)
        assert opened.call_count == 2
        window.browser_search_edit.setFocus()
        window.browser_search_edit.setText('search')
        QTest.keyClick(window.browser_search_edit, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
        assert opened.call_count == 2
        assert window.active_search_query == 'search'
        editor = QLineEdit(window.list_view)
        editor.show()
        editor.setFocus()
        committed = Mock()
        editor.returnPressed.connect(committed)
        QTest.keyClick(editor, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
        assert committed.call_count == 1
        assert opened.call_count == 2
        bindings['browser']['browser_open_selection'] = []
        window.config.apply({'shortcut_bindings': bindings})
        window.list_view.setFocus()
        QTest.keyClick(window.list_view, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
        assert opened.call_count == 2
    finally:
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize('cancel', ['none', 'selection', 'focus', 'new_folder'])
def test_sidebar_async_focus_first_respects_newer_user_intent(tmp_path, qapp, monkeypatch, cancel):
    first, target, newest = (tmp_path / name for name in ('first', 'target', 'newest'))
    for folder in (first, target, newest):
        for name in ('a', 'b', 'c'):
            write_image(folder / f'{name}.png')
    window = make_window(tmp_path, first, qapp)
    release = Event()
    try:
        from app import browser_scanner
        original = browser_scanner.scan_directory
        def scan(request, *args, **kwargs):
            if Path(request.path) == target:
                assert release.wait(3)
            return original(request, *args, **kwargs)
        monkeypatch.setattr(browser_scanner, 'scan_directory', scan)
        window.folder_tree.setFocus()
        assert window._navigate_from_sidebar(target)
        if cancel == 'selection':
            window.list_view.setCurrentIndex(window.item_model.index(1, 0))
        elif cancel == 'focus':
            window.browser_search_edit.setFocus()
        elif cancel == 'new_folder':
            window._navigate_from_sidebar(newest)
        release.set()
        finish_scan(window, qapp)
        if cancel in ('none', 'new_folder'):
            assert window.list_view.hasFocus()
            assert window.list_view.currentIndex().row() == 0
            assert len(window.list_view.selectionModel().selectedIndexes()) == 1
            assert window.current_path == (newest if cancel == 'new_folder' else target)
        elif cancel == 'focus':
            assert window.browser_search_edit.hasFocus()
        else:
            assert not window.list_view.hasFocus()
        assert window._pending_sidebar_focus is None
    finally:
        release.set()
        window.close()
        qapp.processEvents()


def test_sidebar_focus_option_filtered_empty_off_and_snapshot(tmp_path, qapp):
    first, target, empty = (tmp_path / name for name in ('first', 'target', 'empty'))
    write_image(first / 'first.png')
    write_image(target / 'a.png')
    write_image(target / 'keep {zpi$r=3}.png')
    empty.mkdir()
    window = make_window(tmp_path, first, qapp)
    try:
        window._set_browser_filter(BrowserFilterState.normalized(rating_mode=RatingFilterMode.AT_LEAST, rating_reference=3))
        window.folder_tree.setFocus()
        window._navigate_from_sidebar(target)
        finish_scan(window, qapp)
        assert window.list_view.hasFocus()
        assert window.item_model.item_at(window.list_view.currentIndex()).path.name == 'keep {zpi$r=3}.png'
        window.folder_tree.setFocus()
        window._navigate_from_sidebar(empty)
        finish_scan(window, qapp)
        assert window.list_view.hasFocus()
        assert not window.list_view.currentIndex().isValid()
        QTest.keyClick(window.list_view, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        assert window.folder_tree.hasFocus()
        QTest.keyClick(window.folder_tree, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        assert window.list_view.hasFocus()
        assert not window.list_view.currentIndex().isValid()
        window.config.apply({'browser_sidebar_open_focus_first': False})
        window.folder_tree.setFocus()
        window._navigate_from_sidebar(target)
        finish_scan(window, qapp)
        assert window.folder_tree.hasFocus()
        window.config.apply({'browser_sidebar_open_focus_first': True})
        window._navigate_from_sidebar(target)  # Same-folder path needs no scan.
        assert window.list_view.hasFocus()
    finally:
        window.close()
        qapp.processEvents()


def test_shift_arrows_switch_regions_preserve_selection_and_skip_editors(tmp_path, qapp):
    folder = tmp_path / 'books'
    for name in ('a', 'b', 'c'):
        write_image(folder / f'{name}.png')
    window = make_window(tmp_path, folder, qapp)
    try:
        window.list_view.setCurrentIndex(window.item_model.index(1, 0))
        window.folder_tree.setFocus()
        tree_current = window.folder_tree.currentIndex()
        QTest.keyClick(window.folder_tree, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        assert window.list_view.hasFocus()
        assert window.list_view.currentIndex().row() == 1
        # ON takes precedence over a competing configurable shortcut.
        competing = QShortcut(QKeySequence('Shift+Left'), window)
        activated = Mock()
        competing.activated.connect(activated)
        QTest.keyClick(window.list_view, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        assert window.folder_tree.hasFocus()
        assert window.folder_tree.currentIndex() == tree_current
        assert not activated.called
        window.browser_search_edit.setFocus()
        window.browser_search_edit.setText('abc')
        window.browser_search_edit.setCursorPosition(3)
        QTest.keyClick(window.browser_search_edit, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        assert window.browser_search_edit.hasFocus()
        assert window.browser_search_edit.selectedText() == 'c'
        window.config.apply({'browser_shift_arrows_switch_focus': False})
        window.list_view.setFocus()
        QTest.keyClick(window.list_view, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        assert activated.call_count == 1
        competing.setEnabled(False)
        window.config.apply({'browser_shift_arrows_switch_focus': True})
        window.set_sidebar_visible(False)
        QTest.keyClick(window.list_view, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        assert window.list_view.hasFocus()
    finally:
        window.close()
        qapp.processEvents()


def test_rename_late_restore_cannot_override_new_current_or_multiselection(tmp_path, qapp, monkeypatch):
    folder = tmp_path / 'books'
    for name in ('a.cbz', 'b.cbz', 'c.cbz'):
        write_file(folder / name)
    window, coordinator = operation_window(tmp_path, qapp, folder)
    select_paths(window, [folder / 'a.cbz'])
    captured = []
    original = window._restore_file_operation_selection
    monkeypatch.setattr(window, '_restore_file_operation_selection', lambda token=None: captured.append(token))
    try:
        assert window._start_file_operation(FileOperationKind.RENAME, sources=(str(folder / 'a.cbz'),), new_name='z.cbz')
        finish_operation(window, coordinator, qapp)
        assert captured
        select_paths(window, [folder / 'b.cbz', folder / 'c.cbz'])
        before = set(window.selected_file_operation_paths())
        original(captured[-1])
        qapp.processEvents()
        assert set(window.selected_file_operation_paths()) == before
        assert window.item_model.item_at(window.list_view.currentIndex()).path.name == 'b.cbz'
        # A callback from an earlier folder must not consume newer state.
        stale = captured[-1]
        other = tmp_path / 'other'
        write_file(other / 'other.cbz')
        window.navigate_to(other)
        finish_scan(window, qapp)
        select_paths(window, [other / 'other.cbz'])
        original(stale)
        assert window.item_model.item_at(window.list_view.currentIndex()).path.name == 'other.cbz'
    finally:
        close_window(window, coordinator, qapp)


def test_delayed_layout_restore_does_not_replay_after_keyboard_movement(tmp_path, qapp):
    folder = tmp_path / 'books'
    for name in ('a', 'b', 'c'):
        write_image(folder / f'{name}.png')
    window = make_window(tmp_path, folder, qapp)
    try:
        window.list_view.setFocus()
        window.list_view.setCurrentIndex(window.item_model.index(1, 0))
        window._schedule_list_view_state_restore(window._capture_list_view_state())
        QTest.keyClick(window.list_view, Qt.Key.Key_Left)
        newer = window.list_view.currentIndex().row()
        assert newer == 0
        qapp.processEvents()
        assert window.list_view.currentIndex().row() == newer
    finally:
        window.close()
        qapp.processEvents()


def test_browser_focus_options_defaults_apply_cancel_and_reset(tmp_path, qapp):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    keys = ('browser_sidebar_open_focus_first', 'browser_shift_arrows_switch_focus')
    assert all(config.get(key) is True for key in keys)
    dialog = SettingsDialog(config)
    try:
        dialog.browser_sidebar_open_focus_first_checkbox.setChecked(False)
        dialog.browser_shift_arrows_switch_focus_checkbox.setChecked(False)
        dialog.apply_settings()
        assert all(ConfigManager(config.path).load()[key] is False for key in keys)
        dialog._reset_browser_scope()
        assert dialog.browser_sidebar_open_focus_first_checkbox.isChecked()
        assert dialog.browser_shift_arrows_switch_focus_checkbox.isChecked()
        dialog.reject()
        assert all(config.get(key) is False for key in keys)
        config.apply({key: 'invalid' for key in keys})
        assert all(config.get(key) is True for key in keys)
    finally:
        dialog.close()
        qapp.processEvents()


def test_favorite_mouse_release_opens_folder_then_focuses_first_item(tmp_path, qapp):
    first, target = tmp_path / 'first', tmp_path / 'target'
    for folder in (first, target):
        for name in ('a', 'b', 'c'):
            write_image(folder / f'{name}.png')
    window, coordinator = operation_window(tmp_path, qapp, first)
    try:
        window.metadata_store.add_folder_bookmark(str(target))
        window.folder_bookmark_model.refresh()
        window.list_view.setCurrentIndex(window.item_model.index(1, 0))
        window.config.apply({'browser_sidebar_layout': 'favorites_top_tree_bottom'})
        index = next(window.folder_bookmark_model.index(row, 0)
                     for row, entry in enumerate(window.folder_bookmark_model.entries)
                     if Path(entry.path) == target)
        qapp.processEvents()
        assert window.favorite_view.isVisible()
        QTest.mouseClick(window.favorite_view.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.favorite_view.visualRect(index).center())
        finish_scan(window, qapp)
        assert window.current_path == target
        assert window.list_view.hasFocus()
        assert window.list_view.currentIndex().row() == 0
    finally:
        close_window(window, coordinator, qapp)


def test_rename_worker_completion_respects_selection_during_operation(tmp_path, qapp, monkeypatch):
    folder = tmp_path / 'books'
    for name in ('a.cbz', 'b.cbz', 'c.cbz'):
        write_file(folder / name)
    window, coordinator = operation_window(tmp_path, qapp, folder)
    entered, release = Event(), Event()
    service = coordinator.executor.service
    execute = service.execute
    def blocked(request, **kwargs):
        entered.set()
        assert release.wait(3)
        return execute(request, **kwargs)
    monkeypatch.setattr(service, 'execute', blocked)
    try:
        select_paths(window, [folder / 'a.cbz'])
        assert window._start_file_operation(FileOperationKind.RENAME, sources=(str(folder / 'a.cbz'),), new_name='z.cbz')
        assert entered.wait(2)
        select_paths(window, [folder / 'b.cbz', folder / 'c.cbz'])
        release.set()
        finish_operation(window, coordinator, qapp)
        assert set(window.selected_file_operation_paths()) == {str(folder / 'b.cbz'), str(folder / 'c.cbz')}
        assert window.item_model.item_at(window.list_view.currentIndex()).path.name == 'b.cbz'
    finally:
        release.set()
        close_window(window, coordinator, qapp)


def test_consecutive_rename_does_not_reuse_earlier_restore_callback(tmp_path, qapp, monkeypatch):
    folder = tmp_path / 'books'
    for name in ('a.cbz', 'b.cbz'):
        write_file(folder / name)
    window, coordinator = operation_window(tmp_path, qapp, folder)
    callbacks = []
    restore = window._restore_file_operation_selection
    monkeypatch.setattr(window, '_restore_file_operation_selection', lambda token=None: callbacks.append(token))
    try:
        for source, destination in (('a.cbz', 'z.cbz'), ('b.cbz', 'y.cbz')):
            select_paths(window, [folder / source])
            assert window._start_file_operation(FileOperationKind.RENAME, sources=(str(folder / source),), new_name=destination)
            finish_operation(window, coordinator, qapp)
        assert len(callbacks) == 2
        newest_paths = window._operation_restore_paths
        restore(callbacks[0])
        assert window._operation_restore_paths == newest_paths
        assert set(newest_paths) == {str(folder / 'y.cbz')}
        restore(callbacks[1])
        assert window.item_model.item_at(window.list_view.currentIndex()).path.name == 'y.cbz'
    finally:
        close_window(window, coordinator, qapp)


def test_explicit_keypad_binding_remains_narrow_and_other_actions_are_not_aliased(tmp_path, qapp):
    folder = tmp_path / 'books'
    write_image(folder / 'first.png')
    window = make_window(tmp_path, folder, qapp)
    try:
        bindings = deepcopy(window.config.get('shortcut_bindings'))
        bindings['browser']['browser_open_selection'] = ['Num+Enter']
        window.config.apply({'shortcut_bindings': bindings})
        window.list_view.setFocus()
        window.list_view.setCurrentIndex(window.item_model.index(0, 0))
        opened = Mock()
        window.open_item = opened
        QTest.keyClick(window.list_view, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
        assert opened.call_count == 1
        QTest.keyClick(window.list_view, Qt.Key.Key_Return)
        assert opened.call_count == 1
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F2, Qt.KeyboardModifier.KeypadModifier)
        assert not window._browser_shortcut_has('browser_rename', event)
    finally:
        window.close()
        qapp.processEvents()
