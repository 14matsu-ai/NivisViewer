from unittest.mock import Mock

import pytest
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtGui import QPixmap

from app.browser_filter import BrowserFilterState
from tests.test_application_controller import (
    make_controller, write_image, write_archive, close_controller,
    finish_viewer_open, wait_until,
)


def settle_page(qapp, viewer, path):
    def painted():
        viewer.viewer.render(QPixmap(viewer.viewer.size()))
        qapp.processEvents()
        return viewer.displayed_browser_path == str(path)
    assert wait_until(qapp, painted, timeout=5)
    qapp.processEvents()


def selected(browser):
    return {browser.item_model.item_at(index).path for index in
            browser.list_view.selectionModel().selectedIndexes()}


def test_page_sync_preserves_multiselection_filters_and_avoids_open_loop(tmp_path, qapp, monkeypatch):
    paths = [tmp_path / 'images' / f'{i}.png' for i in range(3)]
    for path in paths:
        write_image(path)
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'view_mode': 'single'})
    browser = controller.create_browser_window()
    try:
        browser.set_current_folder(paths[0].parent)
        assert browser.wait_for_scan()
        browser.open_item(browser.item_model.index(browser.item_model.row_for_path(paths[0]), 0))
        viewer = controller.get_active_viewer()
        finish_viewer_open(qapp, viewer)
        settle_page(qapp, viewer, paths[0])
        opens = Mock(wraps=controller.open_path)
        monkeypatch.setattr(controller, 'open_path', opens)
        focus_calls = Mock()
        monkeypatch.setattr(browser, 'raise_', focus_calls)
        monkeypatch.setattr(browser, 'activateWindow', focus_calls)
        monkeypatch.setattr(browser.list_view, 'setFocus', focus_calls)
        selection = browser.list_view.selectionModel()
        for path in paths[:2]:
            selection.select(browser.item_model.index(browser.item_model.row_for_path(path), 0),
                             QItemSelectionModel.SelectionFlag.Select)
        generation = browser._scan_generation
        history = len(browser.navigation_history)
        viewer.next_page()
        settle_page(qapp, viewer, paths[1])
        assert selected(browser) == set(paths[:2])
        assert browser.item_model.item_at(browser.list_view.currentIndex()).path == paths[1]
        assert browser._scan_generation == generation
        assert len(browser.navigation_history) == history
        opens.assert_not_called()
        focus_calls.assert_not_called()
        old_token = viewer.presentation_state.displayed.token
        browser._set_browser_filter(BrowserFilterState.normalized(search_text='1.png'))
        viewer.next_page()
        settle_page(qapp, viewer, paths[2])
        assert selected(browser) == {paths[1]}
        assert browser.browser_filter_state.search_text == '1.png'
        viewer._pending_presentation_side_effect_token = old_token
        viewer._flush_presentation_side_effects()
        controller._on_viewer_displayed_item_changed(viewer, str(paths[0]))
        assert selected(browser) == {paths[1]}
    finally:
        close_controller(controller, qapp)


def test_active_viewer_page_activation_and_closed_results(tmp_path, qapp):
    paths = [tmp_path / 'images' / f'{i}.png' for i in range(3)]
    for path in paths:
        write_image(path)
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'view_mode': 'single'})
    browser = controller.create_browser_window()
    try:
        browser.set_current_folder(paths[0].parent)
        assert browser.wait_for_scan()
        first = controller.open_path(paths[0], open_in_new_window=True)
        finish_viewer_open(qapp, first)
        second = controller.open_path(paths[2], open_in_new_window=True)
        finish_viewer_open(qapp, second)
        settle_page(qapp, second, paths[2])
        controller._on_viewer_activated(second)
        first.next_page()
        settle_page(qapp, first, paths[1])
        assert selected(browser) == {paths[2]}
        controller._on_viewer_activated(first)
        assert selected(browser) == {paths[1]}
        first.close()
        qapp.processEvents()
        controller._on_viewer_displayed_item_changed(first, str(paths[0]))
        assert paths[0] not in selected(browser)
    finally:
        close_controller(controller, qapp)


def test_folder_source_pages_only_sync_when_browser_contains_them(tmp_path, qapp):
    folder = tmp_path / 'images'
    paths = [folder / f'{i}.png' for i in range(3)]
    for path in paths:
        write_image(path)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({'view_mode': 'single'})
    browser = controller.create_browser_window()
    try:
        viewer = controller.open_path(folder)
        finish_viewer_open(qapp, viewer)
        settle_page(qapp, viewer, paths[0])
        browser.set_current_folder(folder)
        assert browser.wait_for_scan()
        viewer.next_page()
        settle_page(qapp, viewer, paths[1])
        assert selected(browser) == {paths[1]}
        browser.set_current_folder(elsewhere)
        assert browser.wait_for_scan()
        history = len(browser.navigation_history)
        viewer.next_page()
        settle_page(qapp, viewer, paths[2])
        assert browser.current_path == elsewhere
        assert len(browser.navigation_history) == history
    finally:
        close_controller(controller, qapp)


@pytest.mark.parametrize('kind', ['archive', 'pdf'])
def test_container_pages_select_container_without_entering_it(tmp_path, qapp, kind):
    path = tmp_path / ('book.cbz' if kind == 'archive' else 'book.pdf')
    if kind == 'archive':
        write_archive(path)
    else:
        from tests.test_pdf_magnifier_integration import _write_pdf
        _write_pdf(path)
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    try:
        browser.set_current_folder(tmp_path)
        assert browser.wait_for_scan()
        viewer = controller.open_path(path)
        finish_viewer_open(qapp, viewer)
        settle_page(qapp, viewer, path)
        viewer.next_page()
        settle_page(qapp, viewer, path)
        assert selected(browser) == {path}
        assert browser.current_path == tmp_path
    finally:
        close_controller(controller, qapp)
