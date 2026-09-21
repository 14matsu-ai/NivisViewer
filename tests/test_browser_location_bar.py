from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from app.browser_location_bar import (
    BrowserLocationBreadcrumb,
    BrowserLocationListPopup,
    LocationPopupEntry,
    list_child_directories,
    split_location_segments,
)
from app.browser_visibility import BrowserVisibilityPolicy


def test_location_segments_preserve_drive_unc_and_japanese_paths() -> None:
    drive = split_location_segments(r"C:\A\日本語\D")
    assert [segment.label for segment in drive] == ["C:", "A", "日本語", "D"]
    assert drive[0].path == "C:\\"
    assert drive[-1].path == r"C:\A\日本語\D"

    unc = split_location_segments(r"\\server\share\漫画\本")
    assert [segment.label for segment in unc] == [
        r"\\server\share",
        "漫画",
        "本",
    ]
    assert unc[-1].path == r"\\server\share\漫画\本"


def test_drive_selector_navigation_current_drive_and_narrow_bar(qapp, monkeypatch):
    from PySide6.QtCore import QFileInfo
    from app.browser_location_bar import QDir

    monkeypatch.setattr(QDir, 'drives', lambda: [QFileInfo('C:/'), QFileInfo('D:/')])
    breadcrumb = BrowserLocationBreadcrumb()
    breadcrumb.resize(180, 30)
    breadcrumb.set_location('C:/one/two/three')
    activated = []
    breadcrumb.locationActivated.connect(activated.append)
    breadcrumb.show()
    qapp.processEvents()
    button = breadcrumb.findChild(QToolButton, 'browser_location_drives')
    assert button is not None and button.isVisibleTo(breadcrumb)
    assert breadcrumb._layout.itemAt(0).widget() is button
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    popup = breadcrumb._drive_popup
    assert popup is not None and popup.entry_count == 2
    assert popup.list_widget.currentRow() == 0
    assert popup.list_widget.item(0).font().bold()
    QTest.keyClick(popup.list_widget, Qt.Key.Key_Down)
    QTest.keyClick(popup.list_widget, Qt.Key.Key_Return)
    assert activated == ['D:/']
    assert breadcrumb._drive_popup is None
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert breadcrumb._drive_popup is not None
    QTest.keyClick(breadcrumb._drive_popup.list_widget, Qt.Key.Key_Escape)
    assert breadcrumb._drive_popup is None
    assert activated == ['D:/']
    monkeypatch.setattr(QDir, 'drives', lambda: [QFileInfo('C:/'), QFileInfo('E:/')])
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert breadcrumb._drive_popup.list_widget.item(1).text() == 'E:'
    breadcrumb.set_location(r'\\server\share\books')
    assert breadcrumb._drive_popup is None
    breadcrumb.close()


def test_child_directory_listing_is_natural_and_respects_visibility(
    tmp_path: Path,
) -> None:
    for name in ("folder10", "folder2", ".hidden"):
        (tmp_path / name).mkdir()
    (tmp_path / "file.jpg").write_bytes(b"not a directory")

    visible = list_child_directories(
        tmp_path,
        BrowserVisibilityPolicy(show_hidden_items=False),
    )
    assert [item.label for item in visible] == ["folder2", "folder10"]

    with_hidden = list_child_directories(
        tmp_path,
        BrowserVisibilityPolicy(show_hidden_items=True),
    )
    assert {item.label for item in with_hidden} == {
        ".hidden",
        "folder2",
        "folder10",
    }


def test_breadcrumb_clicks_ancestors_and_keeps_current_visible(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    current = tmp_path / "A" / "B" / "日本語の長い現在フォルダ"
    current.mkdir(parents=True)
    breadcrumb = BrowserLocationBreadcrumb()
    breadcrumb.resize(1000, 30)
    breadcrumb.set_location(current)
    breadcrumb.show()
    qapp.processEvents()
    activated: list[str] = []
    children: list[str] = []
    edits: list[bool] = []
    breadcrumb.locationActivated.connect(activated.append)
    breadcrumb.childrenRequested.connect(children.append)
    breadcrumb.editRequested.connect(lambda: edits.append(True))

    segments = breadcrumb.segments
    ancestor_index = next(
        index for index, segment in enumerate(segments) if segment.label == "A"
    )
    ancestor = breadcrumb.findChild(
        QToolButton,
        f"browser_location_segment_{ancestor_index}",
    )
    assert ancestor is not None
    QTest.mouseClick(ancestor, Qt.MouseButton.LeftButton)
    assert activated == [segments[ancestor_index].path]

    current_button = breadcrumb.findChild(
        QToolButton,
        f"browser_location_segment_{len(segments) - 1}",
    )
    assert current_button is not None
    assert current_button.toolTip() == str(current.absolute())
    QTest.mouseClick(current_button, Qt.MouseButton.LeftButton)
    assert len(activated) == 1
    assert edits == [True]

    separator = breadcrumb.findChild(
        QToolButton,
        f"browser_location_separator_{ancestor_index}",
    )
    assert separator is not None
    QTest.mouseClick(separator, Qt.MouseButton.LeftButton)
    assert children == [segments[ancestor_index].path]

    breadcrumb.resize(180, 30)
    qapp.processEvents()
    assert breadcrumb.findChild(QToolButton, "browser_location_ellipsis") is not None
    current_button = breadcrumb.findChild(
        QToolButton,
        f"browser_location_segment_{len(segments) - 1}",
    )
    assert current_button is not None
    assert current_button.toolTip() == str(current.absolute())
    breadcrumb.close()
    qapp.processEvents()


def test_location_popup_is_bounded_scrollable_and_keyboard_operable(
    qapp: QApplication,
) -> None:
    activated: list[int] = []
    for count in (0, 1, 12, 13, 50, 100, 200):
        entries = tuple(
            LocationPopupEntry(f"場所 {index:03}", index)
            for index in range(count)
        )
        popup = BrowserLocationListPopup(entries, None)
        popup.show_at(QPoint(20, 20))
        qapp.processEvents()
        row_height = max(
            popup.fontMetrics().height() + 8,
            popup.list_widget.sizeHintForRow(0),
        )
        assert popup.entry_count == count
        assert popup.height() <= 14 * row_height + 8
        assert (
            popup.list_widget.verticalScrollBar().maximum() > 0
        ) is (count > 14)
        popup.close()
        qapp.processEvents()

    popup = BrowserLocationListPopup(
        tuple(LocationPopupEntry(f"場所 {index:03}", index) for index in range(50)),
        None,
    )
    popup.entryActivated.connect(lambda entry: activated.append(int(entry.value)))
    popup.show_at(QPoint(20, 20))
    qapp.processEvents()
    scroll_bar = popup.list_widget.verticalScrollBar()
    before_wheel = scroll_bar.value()
    viewport = popup.list_widget.viewport()
    wheel_position = QPointF(viewport.rect().center())
    QApplication.sendEvent(
        viewport,
        QWheelEvent(
            wheel_position,
            QPointF(viewport.mapToGlobal(viewport.rect().center())),
            QPoint(),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        ),
    )
    assert scroll_bar.value() > before_wheel
    QTest.keyClick(popup.list_widget, Qt.Key.Key_End)
    assert popup.list_widget.currentRow() == 49
    QTest.keyClick(popup.list_widget, Qt.Key.Key_Home)
    assert popup.list_widget.currentRow() == 0
    QTest.keyClick(popup.list_widget, Qt.Key.Key_PageDown)
    assert popup.list_widget.currentRow() > 0
    QTest.keyClick(popup.list_widget, Qt.Key.Key_PageUp)
    QTest.keyClick(popup.list_widget, Qt.Key.Key_Down)
    assert popup.list_widget.currentRow() == 1
    QTest.keyClick(popup.list_widget, Qt.Key.Key_Return)
    qapp.processEvents()
    assert activated
    assert not popup.isVisible()

    popup = BrowserLocationListPopup(
        tuple(LocationPopupEntry(f"場所 {index:03}", index) for index in range(20)),
        None,
    )
    popup.show_at(QPoint(20, 20))
    qapp.processEvents()
    QTest.keyClick(popup.list_widget, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert not popup.isVisible()
