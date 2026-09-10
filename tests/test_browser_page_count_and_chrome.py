from __future__ import annotations

import os
from pathlib import Path
from threading import get_ident
import zipfile
from unittest.mock import patch

import pytest
from PIL import Image
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSizePolicy

from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.browser_window import (
    BROWSER_CHROME_CONTROL_HEIGHT,
    BROWSER_CHROME_CONTROL_SPACING,
    BROWSER_NAVIGATION_BUTTON_SIZE,
    BROWSER_NAVIGATION_ICON_SIZE,
    BROWSER_NAVIGATION_TOOLBAR_MARGINS,
    BROWSER_NAVIGATION_TOOLBAR_MIN_HEIGHT,
    BROWSER_STATUS_LEFT_SPACING,
    BROWSER_STATUS_DETAIL_SPACING,
    BROWSER_STATUS_BAR_MIN_IDLE_HEIGHT,
    BrowserWindow,
)
from app.config_manager import ConfigManager
from app.thumbnail_disk_cache import ThumbnailDiskCache
from app.thumbnail_provider import BrowserThumbnailProvider, ThumbnailLoadResult


def _config(tmp_path: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return config


def _item(path: Path, kind: BrowserItemKind, *, page_count=None) -> BrowserItem:
    stat = path.stat()
    return BrowserItem(
        display_name=path.name,
        path=path,
        kind=kind,
        modified_at=stat.st_mtime,
        file_size=None if path.is_dir() else stat.st_size,
        modified_time_ns=stat.st_mtime_ns,
        extension=path.suffix.casefold(),
        page_count=page_count,
    )


def _select(window: BrowserWindow, path: Path, qapp: QApplication) -> None:
    index = window.item_model.index(window.item_model.row_for_path(path), 0)
    window.list_view.selectionModel().select(
        index,
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    window.list_view.setCurrentIndex(index)
    qapp.processEvents()


def _close(window: BrowserWindow, qapp: QApplication) -> None:
    window.prepare_shutdown()
    window.close()
    qapp.processEvents()


def test_page_count_lists_only_direct_viewer_images_without_pixel_decode(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    nested = folder / "nested"
    nested.mkdir(parents=True)
    (folder / "01.jpg").write_bytes(b"not decoded")
    (folder / "02.PNG").write_bytes(b"not decoded")
    (folder / "notes.txt").write_text("ignored", encoding="utf-8")
    (nested / "03.jpg").write_bytes(b"not recursive")
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("01.jpg", b"not decoded")
        target.writestr("nested/02.webp", b"not decoded")
        target.writestr("notes.txt", b"ignored")

    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    try:
        with patch(
            "app.thumbnail_provider.Image.open",
            side_effect=AssertionError("page counting must not decode pixels"),
        ):
            folder_result = provider._load_page_count_pipeline(
                _item(folder, BrowserItemKind.FOLDER),
                -1,
            )
            archive_result = provider._load_page_count_pipeline(
                _item(archive, BrowserItemKind.ARCHIVE),
                -1,
            )
        assert folder_result.page_count == 2
        assert archive_result.page_count == 2
    finally:
        provider.close()
        qapp.processEvents()


def test_thumbnail_results_publish_count_from_their_existing_listing(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    with Image.new("RGB", (20, 30), "white") as image:
        image.save(folder / "01.jpg")
        image.save(folder / "02.png")
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.write(folder / "01.jpg", "01.jpg")
        target.write(folder / "02.png", "nested/02.png")
        target.writestr("notes.txt", "ignored")

    folder_result = BrowserThumbnailProvider.load_thumbnail_result(
        _item(folder, BrowserItemKind.FOLDER),
        96,
    )
    archive_result = BrowserThumbnailProvider.load_thumbnail_result(
        _item(archive, BrowserItemKind.ARCHIVE),
        96,
    )

    assert folder_result.page_count == 2
    assert archive_result.page_count == 2
    assert folder_result.image is not None
    assert archive_result.image is not None


def test_visible_folder_and_archive_listing_publish_count_before_pixel_decode(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "01.jpg").write_bytes(b"not decoded")
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("01.jpg", b"not decoded")
        target.writestr("02.png", b"not decoded")

    folder_counts: list[int] = []
    archive_counts: list[int] = []

    def reject_folder_decode(*_args, **_kwargs):
        assert folder_counts == [1]
        raise OSError("pixel decode intentionally rejected")

    with patch("app.thumbnail_provider.Image.open", reject_folder_decode):
        folder_result = BrowserThumbnailProvider.load_thumbnail_result(
            _item(folder, BrowserItemKind.FOLDER),
            96,
            page_count_callback=folder_counts.append,
        )

    def reject_archive_decode(*_args, **_kwargs):
        assert archive_counts == [2]
        raise OSError("pixel decode intentionally rejected")

    with patch("app.thumbnail_provider.Image.open", reject_archive_decode):
        archive_result = BrowserThumbnailProvider.load_thumbnail_result(
            _item(archive, BrowserItemKind.ARCHIVE),
            96,
            page_count_callback=archive_counts.append,
        )

    assert folder_result.page_count == 1
    assert archive_result.page_count == 2


def test_folder_preview_listing_populates_model_before_selection_and_is_reused(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "visible-folder"
    folder.mkdir()
    with Image.new("RGB", (20, 30), "white") as image:
        image.save(folder / "01.jpg")
        image.save(folder / "02.png")
    item = _item(folder, BrowserItemKind.FOLDER)
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.item_model.set_items([item])
    try:
        assert window.thumbnail_provider.request(
            item,
            window.thumbnail_render_spec,
            generation=window._generation,
            priority=ThumbnailPriority.VISIBLE,
        )
        assert window.thumbnail_provider.wait_for_done()
        qapp.processEvents()
        assert window.item_model.page_count(folder) == 2

        with patch.object(
            window.thumbnail_provider,
            "request_page_count",
            wraps=window.thumbnail_provider.request_page_count,
        ) as request_count:
            _select(window, folder, qapp)
            assert window.file_detail_label.text() == "2 ページ"
            assert request_count.call_count == 0
    finally:
        _close(window, qapp)


def test_known_count_is_immediate_and_reselection_reuses_model_metadata(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "known"
    folder.mkdir()
    item = _item(folder, BrowserItemKind.FOLDER, page_count=42)
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.item_model.set_items([item])
    try:
        with patch.object(
            window.thumbnail_provider,
            "request_page_count",
            wraps=window.thumbnail_provider.request_page_count,
        ) as request_count:
            _select(window, folder, qapp)
            window._thumbnail_request_timer.stop()
            assert window.file_detail_label.text() == "42 ページ"
            assert request_count.call_count == 0

            window.list_view.clearSelection()
            _select(window, folder, qapp)
            assert window.file_detail_label.text() == "42 ページ"
            assert request_count.call_count == 0
    finally:
        _close(window, qapp)


def test_visible_archive_metadata_is_immediate_on_later_selection(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    cover = tmp_path / "cover.jpg"
    with Image.new("RGB", (20, 30), "white") as image:
        image.save(cover)
    archive = tmp_path / "cached.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.write(cover, "01.jpg")
    item = _item(archive, BrowserItemKind.ARCHIVE)
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.item_model.set_items([item])
    try:
        assert window.thumbnail_provider.request(
            item,
            window.thumbnail_render_spec,
            generation=window._generation,
            priority=ThumbnailPriority.VISIBLE,
        )
        assert window.thumbnail_provider.wait_for_done()
        qapp.processEvents()
        assert window.item_model.page_count(archive) == 1

        with patch.object(
            window.thumbnail_provider,
            "request_page_count",
            wraps=window.thumbnail_provider.request_page_count,
        ) as request_count:
            _select(window, archive, qapp)
            assert window.file_detail_label.text() == "1 ページ"
            assert request_count.call_count == 0
    finally:
        _close(window, qapp)


def test_unknown_count_updates_asynchronously_without_thumbnail_decode(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "unknown"
    folder.mkdir()
    (folder / "01.jpg").write_bytes(b"header listing only")
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.item_model.set_items([_item(folder, BrowserItemKind.FOLDER)])
    try:
        with patch(
            "app.thumbnail_provider.Image.open",
            side_effect=AssertionError("metadata fallback must not decode"),
        ):
            _select(window, folder, qapp)
            window._thumbnail_request_timer.stop()
            assert window.thumbnail_provider.wait_for_done()
            qapp.processEvents()
        assert window.file_detail_label.text() == "1 ページ"
        assert window.item_model.page_count(folder) == 1
    finally:
        _close(window, qapp)


def test_stale_count_cannot_overwrite_new_selection_and_known_value_is_reused(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.item_model.set_items(
        [
            _item(first, BrowserItemKind.FOLDER),
            _item(second, BrowserItemKind.FOLDER),
        ]
    )
    try:
        _select(window, first, qapp)
        _select(window, second, qapp)
        window._thumbnail_request_timer.stop()

        window._on_page_count_ready(str(first), window._generation, 9)
        assert "9 ページ" not in window.file_detail_label.text()
        assert window.item_model.page_count(first) == 9

        window._on_page_count_ready(str(second), window._generation, 3)
        assert window.file_detail_label.text() == "3 ページ"

        with patch.object(
            window.thumbnail_provider,
            "request_page_count",
            wraps=window.thumbnail_provider.request_page_count,
        ) as request_count:
            _select(window, first, qapp)
            assert window.file_detail_label.text() == "9 ページ"
            assert request_count.call_count == 0
    finally:
        _close(window, qapp)


def test_selected_folder_counts_direct_children_off_the_ui_thread(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "large"
    folder.mkdir()
    (folder / "01.jpg").write_bytes(b"header listing only")
    nested = folder / "nested"
    nested.mkdir()
    (nested / "02.jpg").write_bytes(b"must not be counted recursively")
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.item_model.set_items([_item(folder, BrowserItemKind.FOLDER)])
    try:
        caller_thread = get_ident()
        scan_threads: list[int] = []
        scanned_paths: list[Path] = []
        real_scandir = os.scandir

        def recorded_scandir(path):
            scan_threads.append(get_ident())
            scanned_paths.append(Path(path))
            return real_scandir(path)

        with patch("app.thumbnail_provider.os.scandir", recorded_scandir):
            _select(window, folder, qapp)
            window._thumbnail_request_timer.stop()
            assert window.thumbnail_provider.wait_for_done()
            qapp.processEvents()
        assert scan_threads
        assert caller_thread not in scan_threads
        assert scanned_paths == [folder]
        assert window.file_detail_label.text() == "1 ページ"
    finally:
        _close(window, qapp)


def test_selected_page_count_outranks_speculative_work_but_not_visible(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "priority"
    folder.mkdir()
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    try:
        with patch.object(
            provider,
            "_start_worker",
            return_value=False,
        ) as start_worker:
            assert provider.request_page_count(
                _item(folder, BrowserItemKind.FOLDER)
            ) is False
        assert start_worker.call_args.args[1] is ThumbnailPriority.SELECTED
        assert ThumbnailPriority.READ_AHEAD < ThumbnailPriority.SELECTED
        assert ThumbnailPriority.SELECTED < ThumbnailPriority.VISIBLE
    finally:
        provider.close()
        qapp.processEvents()


def test_unknown_archive_submits_selected_metadata_immediately_without_timer(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "unknown.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("01.jpg", b"header only")
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.item_model.set_items([_item(archive, BrowserItemKind.ARCHIVE)])
    try:
        with patch.object(
            window.thumbnail_provider,
            "request_page_count",
            return_value=False,
        ) as request_count:
            _select(window, archive, qapp)
        assert request_count.call_count >= 1
        assert request_count.call_args.kwargs["priority"] is (
            ThumbnailPriority.SELECTED
        )
        assert not hasattr(window, "_page_count_request_timer")
        assert window.file_detail_label.text() == "— ページ"
    finally:
        _close(window, qapp)


def test_page_count_adopts_existing_visible_thumbnail_work(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    archive = tmp_path / "visible.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("01.jpg", b"header only")
    item = _item(archive, BrowserItemKind.ARCHIVE)
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    try:
        with patch.object(provider, "_start_worker", return_value=True) as start:
            assert provider.request(
                item,
                96,
                priority=ThumbnailPriority.VISIBLE,
            )
            assert not provider.request_page_count(item)
        assert start.call_count == 1
        assert provider.pending_count == 1
        pending = next(iter(provider._pending.values()))
        assert pending.priority is ThumbnailPriority.VISIBLE
        assert pending.worker.size != -1
    finally:
        provider.close()
        qapp.processEvents()


def test_rapid_selected_metadata_is_latest_wins_and_bounded(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folders = []
    for name in ("A", "B", "C"):
        folder = tmp_path / name
        folder.mkdir()
        folders.append(_item(folder, BrowserItemKind.FOLDER))
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    workers = []
    try:
        with (
            patch.object(provider, "_start_worker", return_value=True),
            patch.object(provider, "_try_take", return_value=True),
        ):
            for item in folders:
                assert provider.request_page_count(item)
                workers.append(next(iter(provider._pending.values())).worker)
                assert provider.pending_count == 1
        assert workers[0].cancelled.is_set()
        assert workers[1].cancelled.is_set()
        assert not workers[2].cancelled.is_set()
        only_key = next(iter(provider._pending))
        assert only_key[0] == provider._path_key(folders[2].path)
    finally:
        provider.close()
        qapp.processEvents()


def test_cancelled_running_selected_metadata_never_publishes(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "A"
    second = tmp_path / "B"
    first.mkdir()
    second.mkdir()
    first_item = _item(first, BrowserItemKind.FOLDER)
    second_item = _item(second, BrowserItemKind.FOLDER)
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    published: list[tuple[str, int]] = []
    provider.page_count_ready.connect(
        lambda path, _generation, count: published.append((path, count))
    )
    try:
        with (
            patch.object(provider, "_start_worker", return_value=True),
            patch.object(provider, "_try_take", return_value=False),
        ):
            assert provider.request_page_count(first_item)
            assert provider.request_page_count(second_item)
        provider._on_page_count_finished(
            str(first),
            provider.generation,
            -1,
            first_item.modified_at,
            ThumbnailLoadResult(None, page_count=99),
        )
        qapp.processEvents()
        assert published == []
        assert provider.pending_count == 1
    finally:
        provider.close()
        qapp.processEvents()


def test_queued_selected_metadata_yields_to_visible_thumbnail_work(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    image = tmp_path / "visible.jpg"
    image.write_bytes(b"not run")
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    try:
        with (
            patch.object(provider, "_start_worker", return_value=True) as start,
            patch.object(provider, "_try_take", return_value=True),
        ):
            assert provider.request_page_count(
                _item(folder, BrowserItemKind.FOLDER)
            )
            assert provider.request(
                _item(image, BrowserItemKind.IMAGE),
                96,
                priority=ThumbnailPriority.VISIBLE,
            )
        assert [call.args[1] for call in start.call_args_list] == [
            ThumbnailPriority.SELECTED,
            ThumbnailPriority.VISIBLE,
        ]
        assert provider.pending_count == 1
        only_key = next(iter(provider._pending))
        assert only_key[0] == provider._path_key(image)
    finally:
        provider.close()
        qapp.processEvents()


def test_thumbnail_disk_metadata_reuses_count_and_invalidates_on_change(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "book.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("01.jpg", b"x")
    item = _item(archive, BrowserItemKind.ARCHIVE)
    cache = ThumbnailDiskCache(tmp_path / "cache")
    image = QImage(32, 24, QImage.Format.Format_ARGB32)
    image.fill(0xFF224466)
    try:
        assert cache.put(item, 96, image, page_count=1)
        with patch.object(
            cache,
            "_read_qimage",
            side_effect=AssertionError("metadata lookup must not read pixels"),
        ):
            assert cache.get_page_count(item) == 1

        with zipfile.ZipFile(archive, "a") as target:
            target.writestr("02.png", b"x")
        assert cache.get_page_count(
            _item(archive, BrowserItemKind.ARCHIVE)
        ) is None
    finally:
        cache.close()


def test_browser_chrome_uses_compact_layout_authorities(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.resize(1100, 760)
    window.show()
    qapp.processEvents()
    try:
        margins = window.navigation_toolbar.layout().contentsMargins()
        assert BROWSER_NAVIGATION_TOOLBAR_MARGINS == (1, 0, 1, 0)
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
            BROWSER_NAVIGATION_TOOLBAR_MARGINS
        )
        assert BROWSER_NAVIGATION_TOOLBAR_MIN_HEIGHT == 27
        assert BROWSER_NAVIGATION_BUTTON_SIZE == 26
        assert BROWSER_NAVIGATION_ICON_SIZE == 20
        assert BROWSER_CHROME_CONTROL_HEIGHT == 24
        assert BROWSER_CHROME_CONTROL_SPACING < 4
        assert window.browser_toolbar_content.layout().spacing() == 2
        assert window.browser_sort_row.layout().spacing() == 2
        menu_bar = window.menuBar()
        menu_action = menu_bar.actionGeometry(menu_bar.actions()[0])
        assert menu_action.bottom() == menu_bar.rect().bottom()
        assert window.navigation_toolbar.y() == menu_bar.height()
        assert menu_bar.height() + window.navigation_toolbar.height() == 47
        assert window.navigation_toolbar.height() == 27
        toolbar_content_margins = (
            window.browser_toolbar_content.layout().contentsMargins()
        )
        assert (
            toolbar_content_margins.top(),
            toolbar_content_margins.bottom(),
        ) == (0, 0)
        breadcrumb_margins = window.location_breadcrumb.layout().contentsMargins()
        assert (breadcrumb_margins.top(), breadcrumb_margins.bottom()) == (0, 0)
        assert window.browser_location_control.edit_field_rect().height() == 20
        assert window.statusBar().height() <= BROWSER_STATUS_BAR_MIN_IDLE_HEIGHT
        for button in (
            window.back_button,
            window.forward_button,
            window.up_button,
            window.refresh_button,
        ):
            assert button.size().width() == BROWSER_NAVIGATION_BUTTON_SIZE
            assert button.size().height() == BROWSER_NAVIGATION_BUTTON_SIZE
            assert button.height() >= window.navigation_toolbar.iconSize().height()
        for control in (
            window.browser_location_control,
            window.browser_sort_key_combo,
            window.browser_search_container,
        ):
            assert control.height() == BROWSER_CHROME_CONTROL_HEIGHT
    finally:
        _close(window, qapp)


def test_browser_chrome_controls_are_unclipped_at_process_dpi(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.resize(1100, 760)
    window.show()
    qapp.processEvents()
    try:
        expected_scale = float(os.environ.get("QT_SCALE_FACTOR", "1"))
        assert window.devicePixelRatioF() == pytest.approx(expected_scale)
        assert window.menuBar().height() + window.navigation_toolbar.height() == 47
        assert window.navigation_toolbar.height() == 27
        menu_action = window.menuBar().actionGeometry(
            window.menuBar().actions()[0]
        )
        assert menu_action.bottom() == window.menuBar().rect().bottom()
        assert window.statusBar().height() <= BROWSER_STATUS_BAR_MIN_IDLE_HEIGHT
        for control in (
            window.browser_location_control,
            window.browser_sort_key_combo,
            window.browser_search_container,
        ):
            assert control.height() >= control.minimumSizeHint().height()
            assert (
                control.height() * window.devicePixelRatioF()
                >= control.minimumSizeHint().height()
                * window.devicePixelRatioF()
            )
        for button in (
            window.back_button,
            window.forward_button,
            window.up_button,
            window.refresh_button,
        ):
            assert button.height() >= BROWSER_NAVIGATION_ICON_SIZE + 4
    finally:
        _close(window, qapp)


def test_status_omits_redundant_view_state_and_uses_stable_metadata_slots(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"probe mocked")
    folder = tmp_path / "folder"
    folder.mkdir()
    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.current_path = tmp_path
    window.item_model.set_items(
        [
            _item(image, BrowserItemKind.IMAGE),
            _item(folder, BrowserItemKind.FOLDER, page_count=120),
        ]
    )
    window.resize(1100, 760)
    window.show()
    qapp.processEvents()
    try:
        window._update_status()
        message = window.statusBar().currentMessage()
        assert message == ""
        assert "更新日時" not in message
        assert "降順" not in message
        assert "表示:" not in message
        assert "表示：" not in message
        assert window.browser_item_count_label.text() == "2 個の項目"
        assert window.browser_selected_path_edit.text() == ""

        size_geometry = window.file_size_label.geometry()
        detail_geometry = window.file_detail_label.geometry()
        window._set_selected_metadata("9 MB", "9 ページ")
        qapp.processEvents()
        assert window.file_size_label.geometry() == size_geometry
        assert window.file_detail_label.geometry() == detail_geometry
        window._set_selected_metadata("123.4 MB", "120 ページ")
        qapp.processEvents()
        assert window.file_size_label.geometry() == size_geometry
        assert window.file_detail_label.geometry() == detail_geometry
        assert (
            window.selected_detail_widget.layout().spacing()
            == BROWSER_STATUS_DETAIL_SPACING
        )

        _select(window, folder, qapp)
        assert window.browser_selected_path_edit.text() == str(folder)
        assert window.file_size_label.text() == "—"
        assert window.file_detail_label.text() == "120 ページ"

        _select(window, image, qapp)
        generation = window._detail_generation
        from app.browser_image_detail import BrowserImageDetailResult

        window._on_image_detail_completed(
            BrowserImageDetailResult(str(image), generation, (640, 480))
        )
        assert window.file_size_label.text().endswith("B")
        assert "サイズ" not in window.file_size_label.text()
        assert window.file_detail_label.text() == "640 × 480"
    finally:
        _close(window, qapp)


def test_status_left_fields_follow_visible_filter_and_copy_full_selection_path(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    long_parent = tmp_path / ("長いフォルダ" * 5)
    long_parent.mkdir()
    first = long_parent / (("選択した画像" * 5) + "-keep.jpg")
    second = tmp_path / "second-keep.jpg"
    hidden = tmp_path / "filtered-out.jpg"
    for path in (first, second, hidden):
        path.write_bytes(b"probe mocked")

    window = BrowserWindow(
        config_manager=_config(tmp_path),
        restore_initial_location=False,
    )
    window.current_path = tmp_path
    window.item_model.set_items(
        [
            _item(first, BrowserItemKind.IMAGE),
            _item(second, BrowserItemKind.IMAGE),
            _item(hidden, BrowserItemKind.IMAGE),
        ]
    )
    window.resize(720, 620)
    window.show()
    qapp.processEvents()
    try:
        window._update_status()
        assert window.browser_item_count_label.text() == "3 個の項目"
        assert window.browser_selected_path_edit.text() == ""
        assert window.browser_selected_path_edit.isReadOnly()
        assert not window.browser_selected_path_edit.hasFrame()
        status_style = "".join(window.statusBar().styleSheet().split())
        assert "QStatusBar::item{border:none;}" in status_style
        path_style = "".join(
            window.browser_selected_path_edit.styleSheet().split()
        )
        assert "border:none" in path_style
        assert "background:transparent" in path_style
        rendered_status = QImage(
            window.statusBar().size(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        rendered_status.fill(Qt.GlobalColor.transparent)
        window.statusBar().render(rendered_status)
        path_left = window.browser_selected_path_edit.mapTo(
            window.statusBar(),
            QPoint(),
        ).x()
        path_right = path_left + window.browser_selected_path_edit.width() - 1
        detail_left = window.selected_detail_widget.mapTo(
            window.statusBar(),
            QPoint(),
        ).x()
        vertical_pixels = range(3, rendered_status.height() - 3)
        for edge, neighbor in (
            (path_left, path_left - 1),
            (path_right, path_right + 1),
            (detail_left, detail_left - 1),
        ):
            assert sum(
                rendered_status.pixelColor(edge, y)
                != rendered_status.pixelColor(neighbor, y)
                for y in vertical_pixels
            ) <= 1
        assert (
            window.browser_status_summary_widget.layout().spacing()
            == BROWSER_STATUS_LEFT_SPACING
        )
        assert (
            window.browser_selected_path_edit.sizePolicy().horizontalPolicy()
            == QSizePolicy.Policy.Expanding
        )

        window.browser_search_edit.setText("keep")
        window._apply_pending_browser_search()
        qapp.processEvents()
        assert window.item_model.rowCount() == 2
        assert window.browser_item_count_label.text() == "2 個の項目"

        _select(window, first, qapp)
        full_path = str(first)
        assert window.browser_selected_path_edit.text() == full_path
        assert "選択:" not in window.browser_selected_path_edit.text()
        assert (
            window.browser_selected_path_edit.fontMetrics().horizontalAdvance(
                full_path
            )
            > window.browser_selected_path_edit.width()
        )
        window.browser_selected_path_edit.setFocus()
        window.browser_selected_path_edit.selectAll()
        QApplication.clipboard().clear()
        QTest.keyClick(
            window.browser_selected_path_edit,
            Qt.Key.Key_C,
            Qt.KeyboardModifier.ControlModifier,
        )
        assert QApplication.clipboard().text() == full_path

        right_slot_positions = (
            window.file_size_label.mapTo(window, QPoint()).x(),
            window.file_detail_label.mapTo(window, QPoint()).x(),
        )
        selection = window.list_view.selectionModel()
        second_index = window.item_model.index(
            window.item_model.row_for_path(second),
            0,
        )
        selection.select(
            second_index,
            QItemSelectionModel.SelectionFlag.Select,
        )
        qapp.processEvents()
        assert window.browser_selected_path_edit.text() == "2 個を選択"
        assert (
            window.file_size_label.mapTo(window, QPoint()).x(),
            window.file_detail_label.mapTo(window, QPoint()).x(),
        ) == right_slot_positions

        selection.clearSelection()
        qapp.processEvents()
        assert window.browser_selected_path_edit.text() == ""
        assert window.statusBar().currentMessage() == ""
        assert (
            window.file_size_label.mapTo(window, QPoint()).x(),
            window.file_detail_label.mapTo(window, QPoint()).x(),
        ) == right_slot_positions
    finally:
        _close(window, qapp)
