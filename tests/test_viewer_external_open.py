from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest

from tests.test_viewer_shortcut_audit import book
from tests.test_application_controller import wait_until, finish_viewer_open
from app import viewer_window
from app.system_file_opener import SystemFileOpener


@pytest.mark.parametrize('direction', ['ltr', 'rtl'])
@pytest.mark.parametrize('folder', [False, True])
def test_ctrl_t_on_each_painted_spread_page_exports_that_original(book, qapp, monkeypatch, tmp_path, direction, folder):
    window, _ = book
    if folder:
        images = tmp_path / 'folder'
        images.mkdir()
        with ZipFile(window.book_session.source.source_path) as archive:
            for name in archive.namelist():
                (images / name).write_bytes(archive.read(name))
        window.open_path(images)
        finish_viewer_open(qapp, window)
    window.config.apply({'shortcut_bindings': {'viewer': {'viewer_open_with': ['Ctrl+T']}}})
    window.set_view_mode('spread')
    window.set_reading_direction(direction)
    def painted():
        window.viewer.render(QPixmap(window.viewer.size()))
        qapp.processEvents()
        return len(window.viewer._last_image_layout) == 2 and all(not p.isNull() for _,_,p in window.viewer._last_image_layout)
    assert wait_until(qapp, painted, timeout=5)
    window.menuBar().hide()
    window.activateWindow()
    window.viewer.setFocus()
    qapp.processEvents()
    opened = []
    monkeypatch.setattr(SystemFileOpener, 'open_with_default_application',
                        lambda self, path, **kw: opened.append(Path(path)) or SimpleNamespace(success=True))
    for rect, page, _ in tuple(window.viewer._last_image_layout):
        position = window.viewer.mapToGlobal(rect.center())
        monkeypatch.setattr(viewer_window.QCursor, 'pos', lambda: position)
        count = len(opened)
        QTest.keyClick(window.viewer, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier)
        assert wait_until(qapp, lambda: len(opened) > count, timeout=5)
        if folder:
            assert opened[-1] == Path(page.image_id)
        else:
            with ZipFile(window.book_session.source.source_path) as archive:
                assert opened[-1].read_bytes() == archive.read(page.image_id)
            assert window.config.base_dir in opened[-1].parents
        assert opened[-1].suffix == Path(page.image_id).suffix
    count = len(opened)
    identity = window._external_open_identity
    window._external_open_identity = None
    window._external_image_prepared(identity, str(opened[-1]), None)
    qapp.processEvents()
    assert len(opened) == count


def test_original_qaction_route_is_inactive_when_menu_hidden(book, qapp, monkeypatch):
    """Reproduce the previous menu-owned shortcut independently of image export."""
    window, _ = book
    calls = []
    action = window.viewer_open_with_action
    action.triggered.disconnect()
    action.triggered.connect(lambda: calls.append(True))
    action.setShortcut('Ctrl+T')
    window.menuBar().hide()
    qapp.processEvents()
    QTest.keyClick(window.viewer, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier)
    assert calls == []
