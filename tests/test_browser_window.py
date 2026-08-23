from __future__ import annotations

import zipfile
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

import pytest
from PIL import Image
from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPoint, QSize, Qt
from PySide6.QtGui import QContextMenuEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QListView,
    QMessageBox,
    QSizePolicy,
    QTabWidget,
    QToolButton,
)

from app.browser_model import BrowserItemKind, BrowserItemModel
from app.browser_filter import BrowserFilterState, RatingFilterMode
from app.browser_window import BrowserWindow
from app.browser_image_detail import BrowserImageDetailResult
from app.config_manager import ConfigManager
from app.metadata_store import MetadataStore


def write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (16, 24), "white") as image:
        image.save(path)


def make_config(tmp_path: Path, folder: Path, *, thumbnail_size: int = 180) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(folder))
    config.set("thumbnail_size", thumbnail_size)
    return config


def finish_scan(window: BrowserWindow, qapp: QApplication) -> None:
    assert window.wait_for_scan()
    qapp.processEvents()


def test_file_detail_bar_shows_size_and_header_dimensions_and_rejects_stale(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "detail"
    first = folder / "first.jpg"
    second = folder / "second.jpg"
    write_image(first)
    with Image.new("RGB", (40, 30), "white") as image:
        image.save(second)
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)

    first_index = window.item_model.index(window.item_model.row_for_path(first), 0)
    window.list_view.selectionModel().select(
        first_index,
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    window.list_view.setCurrentIndex(first_index)
    stale_generation = window._detail_generation

    second_index = window.item_model.index(window.item_model.row_for_path(second), 0)
    window.list_view.selectionModel().select(
        second_index,
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    window.list_view.setCurrentIndex(second_index)
    window._on_image_detail_completed(
        BrowserImageDetailResult(str(first), stale_generation, (999, 999))
    )
    window.image_detail_probe._pool.waitForDone()
    qapp.processEvents()

    detail = window.file_detail_label.text()
    assert "40 × 30" in detail
    assert "999" not in detail
    assert "★" not in detail
    window.close()
    qapp.processEvents()


def test_direct_rating_target_preserves_multiselection_and_thumbnail_cache(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "rating-direct"
    first = folder / "first.jpg"
    second = folder / "second.jpg"
    write_image(first)
    write_image(second)
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)
    assert window.thumbnail_provider.wait_for_done()
    qapp.processEvents()
    selection = window.list_view.selectionModel()
    first_index = window.item_model.index(window.item_model.row_for_path(first), 0)
    second_index = window.item_model.index(window.item_model.row_for_path(second), 0)
    selection.select(first_index, QItemSelectionModel.SelectionFlag.Select)
    selection.select(second_index, QItemSelectionModel.SelectionFlag.Select)
    selection.setCurrentIndex(first_index, QItemSelectionModel.SelectionFlag.NoUpdate)
    seeded_thumbnail = QImage(12, 8, QImage.Format.Format_ARGB32)
    seeded_thumbnail.fill(0xFF224466)
    window.item_model.set_thumbnail_image(first, seeded_thumbnail)
    thumbnail = window.item_model.data(
        first_index,
        BrowserItemModel.ThumbnailImageRole,
    )
    thumbnail_key = thumbnail.cacheKey()

    try:
        with patch.object(
            window.thumbnail_provider,
            "request",
            wraps=window.thumbnail_provider.request,
        ) as thumbnail_request:
            assert window.set_rating_for_paths((str(first),), 4) is True
            qapp.processEvents()
            assert thumbnail_request.call_count == 0

        renamed = folder / "first {zpi$r=4}.jpg"
        assert renamed.exists()
        assert second.exists()
        assert len(selection.selectedIndexes()) == 2
        renamed_index = window.item_model.index(
            window.item_model.row_for_path(renamed),
            0,
        )
        assert window.item_model.data(
            renamed_index,
            BrowserItemModel.ThumbnailImageRole,
        ).cacheKey() == thumbnail_key
    finally:
        window.close()
        qapp.processEvents()


def test_top_rating_sort_controls_live_resort_without_thumbnail_decode(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "rating-sort"
    one = folder / "one {zpi$r=1}.jpg"
    three = folder / "three {zpi$r=3}.jpg"
    unrated = folder / "none.jpg"
    for path in (one, three, unrated):
        write_image(path)
    preserved_mtime = 1_234_567_890_123_456_700
    os.utime(one, ns=(preserved_mtime, preserved_mtime))
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)
    assert window.thumbnail_provider.wait_for_done()
    qapp.processEvents()
    scan_generation = window._scan_generation

    try:
        rating_index = window.browser_sort_key_combo.findData("rating")
        assert rating_index >= 0
        window.browser_sort_key_combo.setCurrentIndex(rating_index)
        window.browser_sort_order_combo.setCurrentIndex(
            window.browser_sort_order_combo.findData("ascending")
        )
        qapp.processEvents()
        assert window.browser_sort_key_combo.currentData() == "rating"
        assert [item.display_name for item in window.items] == [
            "one.jpg",
            "three.jpg",
            "none.jpg",
        ]

        window.browser_sort_order_combo.setCurrentIndex(
            window.browser_sort_order_combo.findData("descending")
        )
        qapp.processEvents()
        assert window.browser_sort_order_combo.currentData() == "descending"
        assert [item.display_name for item in window.items] == [
            "three.jpg",
            "one.jpg",
            "none.jpg",
        ]

        window.browser_sort_order_combo.setCurrentIndex(
            window.browser_sort_order_combo.findData("ascending")
        )
        qapp.processEvents()
        selection = window.list_view.selectionModel()
        one_index = window.item_model.index(window.item_model.row_for_path(one), 0)
        selection.select(
            one_index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        selection.setCurrentIndex(
            one_index,
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )
        seeded_thumbnail = QImage(12, 8, QImage.Format.Format_ARGB32)
        seeded_thumbnail.fill(0xFF335577)
        window.item_model.set_thumbnail_image(one, seeded_thumbnail)
        thumbnail_key = window.item_model.data(
            one_index,
            BrowserItemModel.ThumbnailImageRole,
        ).cacheKey()

        with patch.object(
            window.thumbnail_provider,
            "request",
            wraps=window.thumbnail_provider.request,
        ) as thumbnail_request:
            assert window.set_rating_for_paths((str(one),), 5) is True
            qapp.processEvents()
            assert thumbnail_request.call_count == 0

        renamed = folder / "one {zpi$r=5}.jpg"
        assert [item.display_name for item in window.items] == [
            "three.jpg",
            "one.jpg",
            "none.jpg",
        ]
        assert [
            str(window.item_model.item_at(index).path)
            for index in selection.selectedIndexes()
        ] == [str(renamed)]
        renamed_index = window.item_model.index(
            window.item_model.row_for_path(renamed),
            0,
        )
        assert window.item_model.data(
            renamed_index,
            BrowserItemModel.ThumbnailImageRole,
        ).cacheKey() == thumbnail_key
        assert renamed.stat().st_mtime_ns == preserved_mtime
        assert window._scan_generation == scan_generation
        assert window.config.get("browser_sort_key") == "rating"
        assert window.config.get("browser_sort_order") == "ascending"
    finally:
        window.close()
        qapp.processEvents()


def test_search_and_rating_quick_filter_compose_without_changing_sort(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "search-rating"
    paths = (
        folder / "alpha {zpi$r=5}.jpg",
        folder / "日本語 high {zpi$r=3}.jpg",
        folder / "日本語 low {zpi$r=2}.jpg",
        folder / "日本語 none.jpg",
    )
    for index, path in enumerate(paths, start=1):
        write_image(path)
        os.utime(path, ns=(index * 1_000_000_000,) * 2)
    config = make_config(tmp_path, folder)
    config.apply(
        {
            "browser_sort_key": "modified_time",
            "browser_sort_order": "descending",
        }
    )
    opened = []
    window = BrowserWindow(
        config_manager=config,
        open_path_handler=(
            lambda path, new, snapshot: opened.append((path, new, snapshot))
        ),
    )
    finish_scan(window, qapp)
    window.resize(800, 600)
    window.show()
    qapp.processEvents()

    try:
        assert window.navigation_toolbar.isVisibleTo(window)
        assert window.menuBar().cornerWidget(Qt.Corner.TopRightCorner) is (
            window.rating_filter_container
        )
        assert window.rating_filter_widget.parentWidget() is (
            window.rating_filter_container
        )
        assert window.browser_search_edit.parentWidget() is (
            window.browser_search_container
        )
        assert window.browser_search_container.parentWidget() is (
            window.browser_sort_row
        )
        assert window.browser_sort_row.parentWidget() is (
            window.browser_toolbar_content
        )
        assert window.browser_search_edit.sizePolicy().horizontalPolicy() == (
            QSizePolicy.Policy.Preferred
        )
        assert window.browser_sort_row.layout().indexOf(
            window.browser_search_container
        ) > window.browser_sort_row.layout().indexOf(
            window.browser_sort_order_combo
        )
        assert not hasattr(window, "browser_folders_first_checkbox")
        assert not hasattr(window, "browser_display_density_combo")
        assert window.browser_search_edit.isVisibleTo(window)
        assert isinstance(window.browser_search_container, QComboBox)
        assert window.browser_search_container.drop_down_rect().isValid()
        assert window.browser_search_container.style().metaObject().className() == (
            window.browser_sort_key_combo.style().metaObject().className()
        )
        assert all(
            button.text() not in {"▼", "▽", "˅", "V"}
            for button in window.browser_search_container.findChildren(QToolButton)
        )
        assert window.location_stack.parentWidget() is window.browser_location_control
        assert window.browser_location_control.parentWidget() is (
            window.browser_toolbar_content
        )
        assert isinstance(window.browser_location_control, QComboBox)
        assert window.browser_location_control.drop_down_rect().isValid()
        assert window.browser_location_control.style().metaObject().className() == (
            window.browser_sort_key_combo.style().metaObject().className()
        )
        assert not hasattr(window, "location_history_button")
        assert not hasattr(window, "recent_location_button")
        menu_center = window.menuBar().rect().center().y()
        rating_center = window.rating_filter_container.geometry().center().y()
        assert abs(menu_center - rating_center) <= 1
        assert window.rating_filter_container.geometry().right() >= (
            window.menuBar().width() - 8
        )
        assert not hasattr(window, "rating_filter_mode_label")
        assert not hasattr(window, "rating_filter_clear_button")
        window.browser_search_edit.setText(
            "2026 summer illustration character reference sheet"
        )
        measured_widths: dict[int, tuple[int, int, int, int]] = {}
        for width in (800, 1024, 1280, 1600, 1920, 2560, 3840):
            window.resize(width, 700)
            qapp.processEvents()
            search_outer = window.browser_search_container.width()
            search_editor = window.browser_search_edit.width()
            clear_width = sum(
                button.geometry()
                .intersected(window.browser_search_edit.rect())
                .width()
                for button in window.browser_search_edit.findChildren(
                    QAbstractButton
                )
                if button.isVisible()
            )
            margins = window.browser_search_edit.textMargins()
            usable_width = (
                search_editor
                - clear_width
                - margins.left()
                - margins.right()
            )
            measured_widths[width] = (
                search_outer,
                search_editor,
                usable_width,
                window.location_stack.width(),
            )
            assert 150 <= search_outer <= 230
            assert window.browser_search_edit.geometry() == (
                window.browser_search_container.edit_field_rect()
            )
            assert search_editor <= search_outer
            assert usable_width >= 100
            search_left = window.browser_search_container.mapTo(
                window,
                QPoint(0, 0),
            ).x()
            location_right = window.browser_location_control.mapTo(
                window,
                QPoint(window.browser_location_control.width(), 0),
            ).x()
            assert location_right <= search_left
        assert 180 <= measured_widths[1920][0] <= 200
        assert measured_widths[1920][2] >= 130
        assert measured_widths[3840][0] == measured_widths[1920][0]
        assert measured_widths[3840][3] > measured_widths[1920][3]
        assert window.browser_sort_key_combo.currentData() == "modified_time"
        stars_rect = window.rating_filter_widget._stars_rect()

        def star_point(reference: int) -> QPoint:
            return QPoint(
                stars_rect.left()
                + round((reference - 0.5) * stars_rect.width() / 5),
                stars_rect.center().y(),
            )

        star_one = star_point(1)
        star_three = star_point(3)
        star_four = star_point(4)
        star_five = star_point(5)
        QTest.mouseClick(
            window.rating_filter_widget,
            Qt.MouseButton.LeftButton,
            pos=star_one,
        )
        assert window.browser_filter_state.rating_reference == 1
        QTest.mouseClick(
            window.rating_filter_widget,
            Qt.MouseButton.LeftButton,
            pos=star_one,
        )
        assert window.browser_filter_state.rating_mode is RatingFilterMode.OFF

        assert window.rating_filter_widget.rating_at(star_three) == 3
        QTest.mouseClick(
            window.rating_filter_widget,
            Qt.MouseButton.LeftButton,
            pos=star_three,
        )
        assert window.browser_filter_state.rating_mode is RatingFilterMode.AT_LEAST
        assert window.browser_filter_state.rating_reference == 3
        assert "現在: ★3以上" in window.rating_filter_widget.toolTip()
        QTest.mouseClick(
            window.rating_filter_widget,
            Qt.MouseButton.LeftButton,
            pos=star_five,
        )
        assert window.browser_filter_state.rating_reference == 5
        QTest.mouseClick(
            window.rating_filter_widget,
            Qt.MouseButton.LeftButton,
            pos=star_five,
        )
        assert window.browser_filter_state.rating_mode is RatingFilterMode.OFF

        QTest.mouseClick(
            window.rating_filter_widget,
            Qt.MouseButton.LeftButton,
            pos=star_three,
        )
        QTest.mousePress(
            window.rating_filter_widget,
            Qt.MouseButton.LeftButton,
            pos=star_three,
        )
        QTest.mouseRelease(
            window.rating_filter_widget,
            Qt.MouseButton.LeftButton,
            pos=star_four,
        )
        assert window.browser_filter_state.rating_mode is RatingFilterMode.OFF

        def trigger_rating_menu(text: str) -> None:
            context_event = QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse,
                star_three,
                window.rating_filter_widget.mapToGlobal(star_three),
            )
            QApplication.sendEvent(window.rating_filter_widget, context_event)
            rating_menu = window.rating_filter_widget._context_menu
            assert rating_menu is not None and rating_menu.isVisible()
            action = next(
                action for action in rating_menu.actions() if action.text() == text
            )
            action.trigger()
            rating_menu.close()
            qapp.processEvents()

        trigger_rating_menu("★3のみ")
        assert window.browser_filter_state.rating_mode is RatingFilterMode.EQUAL
        assert window.browser_filter_state.rating_reference == 3
        assert "現在: ★3のみ" in window.rating_filter_widget.toolTip()
        trigger_rating_menu("フィルタ解除")
        assert window.browser_filter_state.rating_mode is RatingFilterMode.OFF
        trigger_rating_menu("未評価")
        assert window.browser_filter_state.rating_mode is RatingFilterMode.UNRATED
        assert "現在: 未評価" in window.rating_filter_widget.toolTip()
        trigger_rating_menu("フィルタ解除")
        assert window.browser_filter_state.rating_mode is RatingFilterMode.OFF

        QTest.mouseClick(
            window.rating_filter_widget,
            Qt.MouseButton.LeftButton,
            pos=star_three,
        )
        QTest.mouseClick(
            window.rating_filter_widget,
            Qt.MouseButton.MiddleButton,
            pos=star_three,
        )
        assert window.browser_filter_state.rating_mode is RatingFilterMode.OFF

        window.browser_search_edit.setText("日本語")
        QTest.mouseClick(
            window.rating_filter_widget,
            Qt.MouseButton.LeftButton,
            pos=star_three,
        )
        qapp.processEvents()

        assert window.browser_filter_state == BrowserFilterState.normalized(
            search_text="日本語",
            rating_mode=RatingFilterMode.AT_LEAST,
            rating_reference=3,
        )
        assert window.browser_sort_key_combo.currentData() == "modified_time"
        assert [item.display_name for item in window.items] == [
            "日本語 high.jpg"
        ]
        row = window.item_model.row_for_path(paths[1])
        window.open_item(window.item_model.index(row, 0))
        assert len(opened) == 1
        snapshot = opened[0][2]
        assert snapshot is not None
        assert snapshot.image_ids == (str(paths[1]),)
        assert "search='日本語'" in snapshot.filter_identity
        assert "rating=at_least" in snapshot.filter_identity

        window.browser_search_edit.clear()
        window._apply_pending_browser_search()
        window.rating_filter_widget.set_filter(RatingFilterMode.UNRATED)
        qapp.processEvents()
        assert [item.display_name for item in window.items] == [
            "日本語 none.jpg"
        ]
        assert window.browser_sort_key_combo.currentData() == "modified_time"

        window.rating_filter_widget.clear_filter()
        window.browser_search_edit.setText("ALPHA")
        QTest.qWait(120)
        qapp.processEvents()
        assert [item.display_name for item in window.items] == ["alpha.jpg"]
    finally:
        window.close()
        qapp.processEvents()


def test_search_history_records_only_commits_persists_and_reuses_popup(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "search-history"
    write_image(folder / "背景 character.jpg")
    config = make_config(tmp_path, folder)
    window = BrowserWindow(config_manager=config)
    finish_scan(window, qapp)
    window.show()
    qapp.processEvents()

    try:
        window.browser_search_edit.setFocus()
        for partial in ("c", "ch", "cha", "char"):
            window.browser_search_edit.setText(partial)
        assert window.search_history.entries == ()

        window.browser_search_edit.setText("character")
        QTest.keyClick(window.browser_search_edit, Qt.Key.Key_Return)
        assert window.search_history.entries == ("character",)
        assert window.browser_filter_state.search_text == "character"

        window.browser_search_edit.setText("背景")
        window.list_view.setFocus()
        qapp.processEvents()
        assert window.search_history.entries == ("character",)

        window.browser_search_edit.setFocus()
        QTest.mouseClick(
            window.browser_search_container,
            Qt.MouseButton.LeftButton,
            pos=window.browser_search_container.drop_down_rect().center(),
        )
        qapp.processEvents()
        popup = window._search_history_popup
        assert popup is not None
        popup.close()
        qapp.processEvents()
        assert window.search_history.entries[:2] == ("背景", "character")

        window.browser_search_edit.setText("Foo")
        QTest.keyClick(window.browser_search_edit, Qt.Key.Key_Return)
        window.browser_search_edit.setText("foo")
        QTest.keyClick(window.browser_search_edit, Qt.Key.Key_Return)
        assert window.search_history.entries[0] == "foo"
        assert sum(
            query.casefold() == "foo"
            for query in window.search_history.entries
        ) == 1

        for index in range(20):
            window.search_history.record(f"履歴-{index:02}")
        window._persist_browser_search_history()
        QTest.mouseClick(
            window.browser_search_container,
            Qt.MouseButton.LeftButton,
            pos=window.browser_search_container.drop_down_rect().center(),
        )
        qapp.processEvents()
        popup = window._search_history_popup
        assert popup is not None and popup.isVisible()
        assert popup.list_widget.verticalScrollBar().maximum() > 0
        background_item = next(
            popup.list_widget.item(row)
            for row in range(popup.entry_count)
            if popup.list_widget.item(row).text() == "背景"
        )
        popup.list_widget.itemClicked.emit(background_item)
        qapp.processEvents()
        assert window.browser_search_edit.text() == "背景"
        assert window.browser_filter_state.search_text == "背景"
        assert window.search_history.entries[0] == "背景"

        persisted = ConfigManager(config.path).load()
        assert persisted["browser_search_history"][0] == "背景"
        assert "character" in persisted["browser_search_history"]
    finally:
        window.close()
        qapp.processEvents()

    reopened_config = ConfigManager(config.path)
    reopened_config.load()
    reopened = BrowserWindow(config_manager=reopened_config)
    finish_scan(reopened, qapp)
    reopened.show()
    qapp.processEvents()
    try:
        assert reopened.browser_search_edit.text() == ""
        assert reopened.browser_filter_state.search_text == ""
        assert reopened.search_history.entries[0] == "背景"
        reopened_config.apply(
            {"browser_search_history_limit": 1},
            save=True,
        )
        assert len(reopened.search_history.entries) == 1
        popup = reopened._show_search_history_popup()
        assert popup is not None
        clear_item = next(
            popup.list_widget.item(row)
            for row in range(popup.entry_count)
            if popup.list_widget.item(row).text() == "検索履歴を消去"
        )
        popup.list_widget.itemClicked.emit(clear_item)
        qapp.processEvents()
        assert reopened.search_history.entries == ()
        assert ConfigManager(config.path).load()["browser_search_history"] == []
    finally:
        reopened.close()
        qapp.processEvents()


def test_rating_change_live_filters_without_rescan_or_thumbnail_decode(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "rating-live-filter"
    high = folder / "high {zpi$r=5}.jpg"
    middle = folder / "middle {zpi$r=3}.jpg"
    low = folder / "low {zpi$r=1}.jpg"
    for path in (high, middle, low):
        write_image(path)
    preserved_mtime = middle.stat().st_mtime_ns
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)
    assert window.thumbnail_provider.wait_for_done()
    qapp.processEvents()
    scan_generation = window._scan_generation
    window.rating_filter_widget.set_filter(RatingFilterMode.AT_LEAST, 3)
    qapp.processEvents()
    middle_index = window.item_model.index(
        window.item_model.row_for_path(middle),
        0,
    )
    selection = window.list_view.selectionModel()
    selection.select(
        middle_index,
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    selection.setCurrentIndex(
        middle_index,
        QItemSelectionModel.SelectionFlag.NoUpdate,
    )

    try:
        with patch.object(
            window.thumbnail_provider,
            "request",
            wraps=window.thumbnail_provider.request,
        ) as thumbnail_request:
            assert window.set_rating_for_paths((str(middle),), 2)
            qapp.processEvents()
            assert thumbnail_request.call_count == 0

        renamed = folder / "middle {zpi$r=2}.jpg"
        assert renamed.exists()
        assert [item.display_name for item in window.items] == ["high.jpg"]
        current = window.item_model.item_at(window.list_view.currentIndex())
        assert current is not None and current.path == high
        assert [
            window.item_model.item_at(index).path
            for index in selection.selectedIndexes()
        ] == [high]
        assert renamed.stat().st_mtime_ns == preserved_mtime
        assert window._scan_generation == scan_generation
        assert window.item_model.source_count == 3
    finally:
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize("item_count", [0, 1, 25])
def test_status_count_uses_model_row_count_without_materializing_items(
    tmp_path: Path,
    qapp: QApplication,
    item_count: int,
) -> None:
    folder = tmp_path / f"件数-{item_count}"
    folder.mkdir()
    for index in range(item_count):
        write_image(folder / f"{index:03}.jpg")
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)
    if item_count > 1:
        selection_model = window.list_view.selectionModel()
        first = window.item_model.index(0, 0)
        second = window.item_model.index(1, 0)
        selection_model.setCurrentIndex(
            first,
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )
        selection_model.select(
            first,
            QItemSelectionModel.SelectionFlag.Select,
        )
        selection_model.select(
            second,
            QItemSelectionModel.SelectionFlag.Select,
        )

    with patch.object(
        BrowserItemModel,
        "items",
        new_callable=PropertyMock,
        side_effect=AssertionError("status must not materialize all model items"),
    ):
        window._update_status(force=True)

    assert f" — {item_count}件 — " in window.statusBar().currentMessage()
    if item_count > 1:
        assert "ほか1件" in window.statusBar().currentMessage()
    window.close()
    qapp.processEvents()


def test_loading_status_uses_committed_row_count_without_materializing_items(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "読み込み中件数"
    for index in range(3):
        write_image(folder / f"{index}.jpg")
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)
    window._pending_scan = SimpleNamespace(
        path=folder,
        refresh=False,
        committed=True,
        buffered_entries=[object(), object()],
    )

    try:
        with patch.object(
            BrowserItemModel,
            "items",
            new_callable=PropertyMock,
            side_effect=AssertionError(
                "loading status must not materialize all model items"
            ),
        ):
            window._update_status(force=True)
        assert window.statusBar().currentMessage().endswith(
            "読み込み中… 5項目"
        )
    finally:
        window._pending_scan = None
        window.close()
        qapp.processEvents()


def test_show_folder_sidebar_and_settings_round_trip(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "漫画"
    write_image(folder / "1.jpg")
    (folder / "次の本").mkdir()
    config = make_config(tmp_path, folder, thumbnail_size=220)
    window = BrowserWindow(config_manager=config)

    window.show_initial()
    finish_scan(window, qapp)

    assert window.isVisible()
    assert window.current_path == folder.resolve()
    assert {item.display_name for item in window.items} == {"1.jpg", "次の本"}
    assert window.list_view.iconSize() == QSize(220, 220)

    window.splitter.setSizes([340, 700])
    qapp.processEvents()
    window.set_sidebar_visible(False)
    assert not window.sidebar.isVisible()
    assert not window.sidebar_action.isChecked()
    window.set_sidebar_visible(True)
    assert window.sidebar.isVisible()
    assert window.sidebar_action.isChecked()

    window.close()
    qapp.processEvents()

    assert config.get("browser_sidebar_visible") is True
    assert int(config.get("browser_sidebar_width")) >= 300
    assert config.get("browser_window_geometry")


def test_browser_defers_thumbnail_disk_cache_open_until_a_worker_requests_it(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "先行表示"
    folder.mkdir()
    config = make_config(tmp_path, folder)

    with patch("app.browser_window.ThumbnailDiskCache") as cache_type:
        cache = cache_type.return_value
        cache.enabled = False
        window = BrowserWindow(config_manager=config)

        assert cache_type.call_args.kwargs["enabled"] is False
        assert window.thumbnail_provider.disk_cache is cache
        assert not cache.set_enabled.called

        window.close()
        qapp.processEvents()


def test_settings_action_is_direct_and_triggers_existing_dialog_path_once(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "本棚"
    folder.mkdir()
    opened: list[BrowserWindow] = []
    monkeypatch.setattr(
        BrowserWindow,
        "open_settings_dialog",
        lambda window: opened.append(window),
    )
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)

    actions = window.menuBar().actions()
    settings_actions = [
        action for action in actions if action.text() == "環境設定…"
    ]
    assert settings_actions == [window.settings_action]
    assert window.settings_action.menu() is None
    assert not any(
        action.text() == "設定" and action.menu() is not None
        for action in actions
    )

    window.settings_action.trigger()

    assert opened == [window]
    window.close()
    qapp.processEvents()


def test_settings_dialog_is_not_created_after_application_shutdown_starts(
    monkeypatch,
) -> None:
    dialog_factory = Mock()
    monkeypatch.setattr("app.browser_window.SettingsDialog", dialog_factory)
    window = SimpleNamespace(
        _shutdown_prepared=False,
        _settings_dialog_open_guard=Mock(return_value=False),
    )

    BrowserWindow.open_settings_dialog(window)

    dialog_factory.assert_not_called()


def test_item_activation_passes_image_and_archive_but_folder_navigates(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "本棚"
    image = folder / "選択画像.jpg"
    write_image(image)
    archive = folder / "書庫.cbz"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(image, "1.jpg")
    child = folder / "子フォルダ"
    child.mkdir()
    opened: list[tuple[str, bool]] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        open_path_handler=lambda path, new: opened.append((path, new)),
    )
    finish_scan(window, qapp)

    image_row = next(
        row for row, item in enumerate(window.items) if item.kind == BrowserItemKind.IMAGE
    )
    archive_row = next(
        row for row, item in enumerate(window.items) if item.kind == BrowserItemKind.ARCHIVE
    )
    folder_row = next(
        row for row, item in enumerate(window.items) if item.kind == BrowserItemKind.FOLDER
    )
    window.open_item(window.item_model.index(image_row, 0))
    window.open_item(
        window.item_model.index(archive_row, 0),
        open_in_new_window=True,
    )

    assert opened == [(str(image.absolute()), False), (str(archive.absolute()), True)]

    window.set_current_folder(folder)
    window.open_item(window.item_model.index(folder_row, 0))
    finish_scan(window, qapp)
    assert window.current_path == child.resolve()
    assert len(opened) == 2
    window.close()
    qapp.processEvents()


def test_controller_selection_sync_does_not_reopen_item(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "同期"
    image = folder / "対象.jpg"
    write_image(image)
    opened: list[str] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        open_path_handler=lambda path, _new: opened.append(path),
    )
    finish_scan(window, qapp)

    window.select_path(image)
    finish_scan(window, qapp)

    assert window.list_view.currentIndex().isValid()
    assert window.item_model.item_at(window.list_view.currentIndex()).path == image.absolute()
    assert opened == []
    window.close()
    qapp.processEvents()


def test_sort_controls_apply_without_opening_viewer_or_changing_history(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "並び替え"
    first = folder / "book2.jpg"
    second = folder / "book10.jpg"
    write_image(first)
    write_image(second)
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    os.utime(second, ns=(2_000_000_000, 2_000_000_000))
    opened: list[str] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        open_path_handler=lambda path, _new: opened.append(path),
    )
    finish_scan(window, qapp)
    initial_history_length = len(window.navigation_history)
    initial_generation = window.thumbnail_provider.generation

    window.browser_sort_key_combo.setCurrentIndex(
        window.browser_sort_key_combo.findData("modified_time")
    )
    window.browser_sort_order_combo.setCurrentIndex(
        window.browser_sort_order_combo.findData("descending")
    )
    qapp.processEvents()

    assert [entry.display_name for entry in window.items] == [
        "book10.jpg",
        "book2.jpg",
    ]
    assert window.config.get("browser_sort_key") == "modified_time"
    assert window.config.get("browser_sort_order") == "descending"
    assert len(window.navigation_history) == initial_history_length
    assert window.thumbnail_provider.generation == initial_generation
    assert opened == []
    assert "更新日時・降順" in window.statusBar().currentMessage()
    window.close()
    qapp.processEvents()


def test_sort_and_density_preserve_multiple_selection_and_thumbnail_cache(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "複数選択"
    for index in range(1, 13):
        write_image(folder / f"book{index}.jpg")
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)
    selection_model = window.list_view.selectionModel()
    selected_names = {"book2.jpg", "book10.jpg"}
    for name in selected_names:
        row = next(
            row
            for row, entry in enumerate(window.items)
            if entry.display_name == name
        )
        index = window.item_model.index(row, 0)
        selection_model.select(index, QItemSelectionModel.SelectionFlag.Select)
        if name == "book10.jpg":
            selection_model.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
    initial_generation = window.thumbnail_provider.generation
    initial_history_length = len(window.navigation_history)

    window.config.apply(
        {
            "browser_sort_order": "descending",
            "browser_display_density": "comfortable",
        }
    )
    qapp.processEvents()

    restored_names = {
        window.item_model.item_at(index).display_name
        for index in window.list_view.selectionModel().selectedIndexes()
    }
    current = window.item_model.item_at(window.list_view.currentIndex())
    assert restored_names == selected_names
    assert current is not None and current.display_name == "book10.jpg"
    assert window.list_view.gridSize() == QSize(199, 193)
    assert window.thumbnail_provider.generation == initial_generation
    assert len(window.navigation_history) == initial_history_length
    window.close()
    qapp.processEvents()


def test_thumbnail_size_change_preserves_selection_and_uses_new_generation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "サイズ変更"
    image = folder / "選択.jpg"
    write_image(image)
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)
    window.select_path(image)
    finish_scan(window, qapp)
    initial_generation = window.thumbnail_provider.generation

    window.config.apply({"thumbnail_size": 260})
    qapp.processEvents()

    selected = window.item_model.item_at(window.list_view.currentIndex())
    assert selected is not None and selected.path == image.absolute()
    assert window.list_view.iconSize() == QSize(260, 260)
    assert window.thumbnail_provider.generation == initial_generation + 1

    window.config.apply({"thumbnail_size": 260})
    qapp.processEvents()
    assert window.thumbnail_provider.generation == initial_generation + 1
    window.close()
    qapp.processEvents()


def test_thumbnail_size_change_preserves_unselected_anchor_offset(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "サイズ変更位置"
    for index in range(1, 81):
        write_image(folder / f"page{index:03}.jpg")
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    window.resize(760, 480)
    window.show()
    finish_scan(window, qapp)
    target = folder / "page055.jpg"
    target_index = window.item_model.index(
        window.item_model.row_for_path(target),
        0,
    )
    window.list_view.scrollTo(target_index, QListView.ScrollHint.PositionAtCenter)
    window.list_view.clearSelection()
    window.list_view.setCurrentIndex(QModelIndex())
    qapp.processEvents()
    state = window._capture_list_view_state()
    assert state.anchor_path is not None
    before_row = window.item_model.row_for_path(state.anchor_path)
    before_rect = window.list_view.visualRect(window.item_model.index(before_row, 0))

    window.config.apply({"thumbnail_size": 260})
    qapp.processEvents()
    qapp.processEvents()

    after_row = window.item_model.row_for_path(state.anchor_path)
    after_rect = window.list_view.visualRect(window.item_model.index(after_row, 0))
    assert abs(after_rect.y() - before_rect.y()) <= 1
    assert window.list_view.selectionModel().selectedIndexes() == []
    assert not window.list_view.currentIndex().isValid()
    window.close()
    qapp.processEvents()


def test_display_density_changes_layout_without_changing_thumbnail_size(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "密度"
    write_image(folder / "1.jpg")
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder, thumbnail_size=180)
    )
    finish_scan(window, qapp)

    expectations = {
        "compact": QSize(147, 191),
        "standard": QSize(171, 192),
        "comfortable": QSize(199, 193),
        "large": QSize(231, 195),
    }
    for density, grid_size in expectations.items():
        window.config.apply({"browser_display_density": density})
        qapp.processEvents()
        assert window.list_view.gridSize() == grid_size
        assert window.list_view.iconSize() == QSize(180, 180)

    window.close()
    qapp.processEvents()


def test_folders_first_setting_keeps_folder_group_at_front(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "フォルダ優先"
    write_image(folder / "a.jpg")
    (folder / "z-folder").mkdir()
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    finish_scan(window, qapp)

    assert [entry.display_name for entry in window.items] == [
        "z-folder",
        "a.jpg",
    ]
    window.config.apply({"browser_folders_first": False})
    qapp.processEvents()

    assert [entry.display_name for entry in window.items] == [
        "a.jpg",
        "z-folder",
    ]
    assert window.config.get("browser_folders_first") is False
    window.close()
    qapp.processEvents()


def test_folders_first_roundtrip_restores_unselected_viewport_anchor_and_offset(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "スクロール"
    for index in range(1, 61):
        write_image(folder / f"book{index:03}.jpg")
    for index in range(1, 21):
        (folder / f"folder{index:03}").mkdir()
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    window.resize(640, 420)
    window.show()
    finish_scan(window, qapp)
    anchor_row = window.item_model.row_for_path(folder / "book040.jpg")
    anchor = window.item_model.index(anchor_row, 0)
    window.list_view.scrollTo(anchor, QListView.ScrollHint.PositionAtCenter)
    window.list_view.clearSelection()
    window.list_view.setCurrentIndex(QModelIndex())
    qapp.processEvents()
    before = window._capture_list_view_state()
    assert before.selected_paths == ()
    assert before.current_path is None
    assert before.anchor_path is not None
    initial_generation = window.thumbnail_provider.generation
    initial_scan_generation = window._scan_generation

    with patch.object(window, "_schedule_thumbnail_requests") as schedule:
        window.config.apply({"browser_folders_first": False})
        qapp.processEvents()
        window.config.apply({"browser_folders_first": True})
        qapp.processEvents()
        qapp.processEvents()

    after = window._capture_list_view_state()
    assert after.anchor_path == before.anchor_path
    assert abs(after.anchor_y - before.anchor_y) <= 1
    assert abs(after.vertical_scroll - before.vertical_scroll) <= 1
    assert window.list_view.selectionModel().selectedIndexes() == []
    assert not window.list_view.currentIndex().isValid()
    assert window.thumbnail_provider.generation == initial_generation
    assert window._scan_generation == initial_scan_generation
    schedule.assert_not_called()
    window.close()
    qapp.processEvents()


def test_sort_moves_explicit_selection_only_enough_to_make_it_visible(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "選択スクロール"
    for index in range(1, 81):
        write_image(folder / f"book{index:03}.jpg")
    window = BrowserWindow(config_manager=make_config(tmp_path, folder))
    window.resize(640, 420)
    window.show()
    finish_scan(window, qapp)
    selected_path = folder / "book060.jpg"
    selected = window.item_model.index(
        window.item_model.row_for_path(selected_path),
        0,
    )
    window.list_view.scrollTo(selected, QListView.ScrollHint.PositionAtCenter)
    window.list_view.selectionModel().select(
        selected,
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    window.list_view.setCurrentIndex(selected)
    qapp.processEvents()

    window.config.apply({"browser_sort_order": "descending"})
    qapp.processEvents()

    restored_row = window.item_model.row_for_path(selected_path)
    restored = window.item_model.index(restored_row, 0)
    restored_rect = window.list_view.visualRect(restored)
    viewport_rect = window.list_view.viewport().rect()
    assert viewport_rect.contains(restored_rect)
    assert min(
        abs(restored_rect.top() - viewport_rect.top()),
        abs(restored_rect.bottom() - viewport_rect.bottom()),
    ) <= window.list_view.gridSize().height()
    assert {
        window.item_model.item_at(index).path
        for index in window.list_view.selectionModel().selectedIndexes()
    } == {selected_path.absolute()}
    window.close()
    qapp.processEvents()


def test_metadata_update_does_not_change_filesystem_mtime_sort(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "メタデータ分離"
    older = folder / "older.jpg"
    newer = folder / "newer.jpg"
    write_image(older)
    write_image(newer)
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
    source_mtimes = (older.stat().st_mtime_ns, newer.stat().st_mtime_ns)
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        metadata_store=store,
    )
    finish_scan(window, qapp)
    window.config.apply(
        {
            "browser_sort_key": "modified_time",
            "browser_sort_order": "descending",
        }
    )
    before = [entry.display_name for entry in window.items]

    store.set_rating(str(older), 5)
    qapp.processEvents()

    assert [entry.display_name for entry in window.items] == before == [
        "newer.jpg",
        "older.jpg",
    ]
    assert (older.stat().st_mtime_ns, newer.stat().st_mtime_ns) == source_mtimes
    window.close()
    qapp.processEvents()
    store.close()


def test_sidebar_has_folder_bookmark_and_history_tabs(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "three-tabs"
    folder.mkdir()
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    config = make_config(tmp_path, folder)
    config.set("browser_sidebar_layout", "tabs")
    window = BrowserWindow(
        config_manager=config,
        metadata_store=store,
    )
    finish_scan(window, qapp)

    tabs = window.sidebar_layout_controller.tabs
    assert tabs is not None
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        "フォルダ",
        "お気に入り",
        "履歴",
    ]
    favorites_panel = tabs.widget(1)
    assert isinstance(favorites_panel, QTabWidget)
    assert [
        favorites_panel.tabText(index)
        for index in range(favorites_panel.count())
    ] == ["フォルダ", "本"]
    window.close()
    store.close()
    qapp.processEvents()


def test_bookmark_model_updates_and_bookmarks_can_be_opened_or_removed(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    shelf = tmp_path / "shelf"
    book = shelf / "book"
    archive = shelf / "book.cbz"
    write_image(book / "1.jpg")
    archive.write_bytes(b"test")
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    opened: list[tuple[str, bool]] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, shelf),
        metadata_store=store,
        open_path_handler=lambda path, new: opened.append((path, new)),
    )
    finish_scan(window, qapp)

    window.add_browser_bookmark(book, item_type="book_folder", label="本")
    window.add_browser_bookmark(archive, item_type="archive")
    assert window.bookmark_model.rowCount() == 2

    book_index = window.bookmark_model.index(0, 0)
    archive_index = window.bookmark_model.index(1, 0)
    window.open_bookmark(book_index, open_in_new_window=True)
    window.open_bookmark(archive_index, open_in_new_window=True)

    assert opened == [(str(book.absolute()), True), (str(archive.absolute()), True)]
    window.remove_browser_bookmark(book)
    assert window.bookmark_model.rowCount() == 1
    window.close()
    store.close()
    qapp.processEvents()


def test_folder_bookmark_navigates_browser_without_opening_viewer(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    shelf = tmp_path / "shelf"
    target = shelf / "target"
    target.mkdir(parents=True)
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    opened: list[str] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, shelf),
        metadata_store=store,
        open_path_handler=lambda path, _new: opened.append(path),
    )
    finish_scan(window, qapp)
    window.add_browser_bookmark(target, item_type="folder")

    window.open_bookmark(window.bookmark_model.index(0, 0))
    finish_scan(window, qapp)

    assert window.current_path == target.absolute()
    assert opened == []
    window.close()
    store.close()
    qapp.processEvents()


def test_current_folder_can_be_added_to_bookmarks(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "current"
    folder.mkdir()
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        metadata_store=store,
    )
    finish_scan(window, qapp)

    window.add_current_folder_bookmark()

    assert window.bookmark_model.rowCount() == 1
    entry = window.bookmark_model.entries[0]
    assert Path(entry.path) == folder.absolute()
    assert entry.item_type == "folder"
    window.close()
    store.close()
    qapp.processEvents()


def test_history_is_recent_first_opens_items_and_selection_does_not(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "shelf"
    first = folder / "first"
    second = folder / "second"
    first.mkdir(parents=True)
    second.mkdir()
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    store.record_book_opened(
        str(first),
        item_type="folder",
        start_page_index=1,
        total_pages=3,
    )
    store.record_book_opened(
        str(second),
        item_type="folder",
        start_page_index=2,
        total_pages=4,
    )
    opened: list[tuple[str, bool]] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        metadata_store=store,
        open_path_handler=lambda path, new: opened.append((path, new)),
    )
    finish_scan(window, qapp)

    assert [Path(entry.path) for entry in window.history_model.entries] == [
        second.absolute(),
        first.absolute(),
    ]
    window.history_view.setCurrentIndex(window.history_model.index(0, 0))
    assert opened == []

    window.open_history(
        window.history_model.index(0, 0),
        open_in_new_window=True,
    )
    assert opened == [(str(second.absolute()), True)]
    window.close()
    store.close()
    qapp.processEvents()


def test_missing_metadata_items_are_nonmodal_and_removable(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "shelf"
    folder.mkdir()
    missing_bookmark = tmp_path / "missing-book"
    missing_history = tmp_path / "missing-history.zip"
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    store.add_browser_bookmark(str(missing_bookmark), item_type="folder")
    store.record_book_opened(
        str(missing_history),
        item_type="archive",
        start_page_index=0,
        total_pages=None,
    )
    opened: list[str] = []
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        metadata_store=store,
        open_path_handler=lambda path, _new: opened.append(path),
    )
    finish_scan(window, qapp)

    window.open_bookmark(window.bookmark_model.index(0, 0))
    assert window.statusBar().currentMessage() == "ブックマーク先が見つかりません"
    window.open_history(window.history_model.index(0, 0))
    assert window.statusBar().currentMessage() == "履歴の項目が見つかりません"
    assert opened == []

    window.remove_browser_bookmark(missing_bookmark)
    window.remove_history_entry(window.history_model.index(0, 0))
    assert window.bookmark_model.rowCount() == 0
    assert window.history_model.rowCount() == 0
    window.close()
    store.close()
    qapp.processEvents()


def test_clear_history_uses_confirmation_path(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    folder = tmp_path / "shelf"
    folder.mkdir()
    store = MetadataStore(tmp_path / "data" / "metadata.sqlite3")
    store.record_book_opened(
        str(folder),
        item_type="folder",
        start_page_index=0,
        total_pages=1,
    )
    window = BrowserWindow(
        config_manager=make_config(tmp_path, folder),
        metadata_store=store,
    )
    finish_scan(window, qapp)
    confirmations: list[bool] = []

    def answer_yes(*_args, **_kwargs):
        confirmations.append(True)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", answer_yes)

    assert window.clear_history() is True
    assert confirmations == [True]
    assert window.history_model.rowCount() == 0
    window.close()
    store.close()
    qapp.processEvents()
