from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QStyleFactory,
    QToolButton,
)

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


def test_compact_popup_rows_match_popup_height_and_remain_scrollable(
    qapp: QApplication,
) -> None:
    entries = tuple(
        LocationPopupEntry(f"検索候補 {index:02}", index)
        for index in range(18)
    ) + (
        LocationPopupEntry("────────", None, enabled=False),
        LocationPopupEntry("検索履歴を消去", "clear"),
    )
    style_names = ("windows11", "windowsvista", "Windows", "Fusion")
    available_styles = set(QStyleFactory.keys())
    assert set(style_names) <= available_styles
    original_style_name = qapp.style().objectName()
    try:
        for style_name in style_names:
            style = QStyleFactory.create(style_name)
            assert style is not None
            qapp.setStyle(style)
            default_popup = BrowserLocationListPopup(entries, None)
            compact_popup = BrowserLocationListPopup(
                entries,
                None,
                compact_rows=True,
            )
            short_popup = BrowserLocationListPopup(
                entries[:8] + entries[-2:],
                None,
                compact_rows=True,
            )
            activated: list[object] = []
            compact_popup.entryActivated.connect(activated.append)
            try:
                default_popup.show_at(QPoint(20, 20))
                compact_popup.show_at(QPoint(20, 20))
                short_popup.show_at(QPoint(20, 20))
                qapp.processEvents()

                compact_row_height = compact_popup.list_widget.sizeHintForRow(0)
                default_item_row_height = (
                    default_popup.list_widget.sizeHintForRow(0)
                )
                assert compact_popup.compact_rows
                assert compact_row_height <= default_item_row_height
                assert compact_popup.height() < default_popup.height()
                compact_first_rect = compact_popup.list_widget.visualItemRect(
                    compact_popup.list_widget.item(0)
                )
                default_first_rect = default_popup.list_widget.visualItemRect(
                    default_popup.list_widget.item(0)
                )
                assert compact_first_rect.height() <= default_first_rect.height()
                if style_name == "windows11":
                    assert compact_row_height < default_item_row_height
                    assert compact_first_rect.height() < default_first_rect.height()
                    assert compact_row_height <= (
                        compact_popup.list_widget.fontMetrics().height()
                    )
                separator_row = compact_popup.entry_count - 2
                separator_height = compact_popup.list_widget.sizeHintForRow(
                    separator_row
                )
                assert 0 < separator_height < compact_row_height
                assert {
                    compact_popup.list_widget.sizeHintForRow(row)
                    for row in range(compact_popup.entry_count)
                    if row != separator_row
                } == {compact_row_height}
                margins = compact_popup.layout().contentsMargins()
                vertical_chrome = (
                    2 * compact_popup.frameWidth()
                    + 2 * compact_popup.list_widget.frameWidth()
                    + margins.top()
                    + margins.bottom()
                )
                assert compact_popup.height() == (
                    compact_popup.maximum_visible_rows * compact_row_height
                    + vertical_chrome
                )
                assert compact_popup.list_widget.viewport().height() >= (
                    compact_popup.maximum_visible_rows * compact_row_height
                )
                assert compact_popup.list_widget.verticalScrollBar().maximum() > 0
                assert short_popup.list_widget.sizeHintForRow(8) == separator_height
                assert short_popup.height() == (
                    9 * compact_row_height + separator_height + vertical_chrome
                )
                separator_rect = short_popup.list_widget.visualItemRect(
                    short_popup.list_widget.item(8)
                )
                viewport_image = short_popup.list_widget.viewport().grab().toImage()
                line_color = viewport_image.pixelColor(
                    20, separator_rect.center().y()
                )
                background_color = viewport_image.pixelColor(
                    20, separator_rect.top()
                )
                assert abs(
                    line_color.lightness() - background_color.lightness()
                ) >= 50

                separator = compact_popup.list_widget.item(
                    compact_popup.entry_count - 2
                )
                assert not bool(separator.flags() & Qt.ItemFlag.ItemIsEnabled)
                assert not bool(separator.flags() & Qt.ItemFlag.ItemIsSelectable)

                last_item = compact_popup.list_widget.item(
                    compact_popup.entry_count - 1
                )
                compact_popup.list_widget.scrollToItem(
                    last_item,
                    QAbstractItemView.ScrollHint.PositionAtBottom,
                )
                qapp.processEvents()
                last_rect = compact_popup.list_widget.visualItemRect(last_item)
                assert last_rect.height() == compact_row_height
                assert last_rect.top() >= 0
                assert last_rect.bottom() < (
                    compact_popup.list_widget.viewport().height()
                )

                screen = QApplication.primaryScreen()
                assert screen is not None
                screen_geometry = screen.availableGeometry()
                anchor_y = screen_geometry.bottom() - 2
                compact_popup.show_at(
                    QPoint(screen_geometry.left() + 20, anchor_y)
                )
                qapp.processEvents()
                assert compact_popup.y() < anchor_y
                assert compact_popup.geometry().bottom() <= (
                    screen_geometry.bottom()
                )

                compact_popup.list_widget.setCurrentRow(
                    compact_popup.entry_count - 1
                )
                QTest.keyClick(compact_popup.list_widget, Qt.Key.Key_Return)
                qapp.processEvents()
                assert activated[-1].value == "clear"
                assert not compact_popup.isVisible()

                compact_popup.show_at(QPoint(20, 20))
                qapp.processEvents()
                last_rect = compact_popup.list_widget.visualItemRect(last_item)
                QTest.mouseClick(
                    compact_popup.list_widget.viewport(),
                    Qt.MouseButton.LeftButton,
                    pos=last_rect.center(),
                )
                qapp.processEvents()
                assert len(activated) == 2 and activated[-1].value == "clear"
                assert not compact_popup.isVisible()
            finally:
                default_popup.close()
                compact_popup.close()
                short_popup.close()
                qapp.processEvents()
    finally:
        qapp.setStyle(original_style_name)
        qapp.processEvents()
