from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from time import monotonic
from zipfile import ZipFile
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from app.application_controller import ApplicationController
from app.browser_filter import BrowserFilterState, RatingFilterMode
from app.browser_model import BrowserItemKind
from app.config_manager import ConfigManager


def write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (8, 12), "white") as image:
        image.save(path)


def write_archive(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = BytesIO()
    with Image.new("RGB", (8, 12), "white") as image:
        image.save(payload, format="PNG")
    with ZipFile(path, "w") as archive:
        archive.writestr("001.png", payload.getvalue())


def make_controller(tmp_path: Path, qapp: QApplication) -> ApplicationController:
    return ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
    )


def close_controller(controller: ApplicationController, qapp: QApplication) -> None:
    for window in tuple(controller.viewer_windows):
        window.close()
    browser = controller.get_browser_window()
    if browser is not None:
        browser.close()
    qapp.processEvents()
    controller.shutdown()


def wait_until(qapp: QApplication, predicate, timeout: float = 2.0) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        QTest.qWait(5)
    return bool(predicate())


def finish_viewer_open(qapp: QApplication, viewer) -> None:
    assert viewer.book_session.wait_for_async(2000)
    qapp.processEvents()
    deadline = monotonic() + 3
    while (
        viewer._pending_book_open_projection is not None
        and monotonic() < deadline
    ):
        qapp.processEvents()
        QTest.qWait(5)
    qapp.processEvents()
    assert viewer._pending_book_open_projection is None


def activate_browser_search(browser, query: str, *, persist: bool = False) -> None:
    browser.browser_search_edit.setText(query)
    browser._browser_search_timer.stop()
    browser._apply_pending_browser_search()
    if persist:
        browser.search_history.record(query)
        browser._persist_browser_search_history()


def test_start_creates_browser_and_shares_config(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = make_controller(tmp_path, qapp)

    browser = controller.start()

    assert controller.get_browser_window() is browser
    assert controller.viewer_windows == ()
    assert browser.config is controller.config
    assert browser.settings is controller.settings
    close_controller(controller, qapp)


def test_start_passes_initial_path_to_open_processing(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image = tmp_path / "初期画像.jpg"
    write_image(image)
    controller = make_controller(tmp_path, qapp)

    browser = controller.start(str(image))
    window = controller.get_active_viewer()

    assert controller.get_browser_window() is browser
    assert window is not None
    finish_viewer_open(qapp, window)
    assert window.book_session.current_path == image
    assert str(image) in window.model.image_ids
    close_controller(controller, qapp)


def test_create_and_close_viewer_updates_registration(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = make_controller(tmp_path, qapp)
    window = controller.create_viewer_window()

    assert controller.viewer_windows == (window,)

    controller.close_viewer_window(window)

    assert controller.viewer_windows == ()
    assert controller.get_active_viewer() is None
    qapp.processEvents()
    controller.shutdown()


def test_reuse_active_reuses_active_viewer(tmp_path: Path, qapp: QApplication) -> None:
    image = tmp_path / "book.jpg"
    write_image(image)
    controller = make_controller(tmp_path, qapp)
    controller.settings["open_viewer_behavior"] = "reuse_active"
    existing = controller.create_viewer_window()

    opened = controller.open_path(image)

    assert opened is existing
    assert controller.viewer_windows == (existing,)
    close_controller(controller, qapp)


def test_always_new_creates_a_viewer_for_each_open(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_image = tmp_path / "first" / "1.jpg"
    second_image = tmp_path / "second" / "1.jpg"
    write_image(first_image)
    write_image(second_image)
    controller = make_controller(tmp_path, qapp)
    controller.settings["open_viewer_behavior"] = "always_new"

    first = controller.open_path(first_image)
    second = controller.open_path(second_image)

    assert first is not second
    assert controller.viewer_windows == (first, second)
    close_controller(controller, qapp)


def test_viewer_file_request_reuses_source_window_even_when_always_new(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image = tmp_path / "selected.jpg"
    write_image(image)
    controller = make_controller(tmp_path, qapp)
    controller.settings["open_viewer_behavior"] = "always_new"
    source_window = controller.create_viewer_window()

    source_window._request_open_path(image)

    finish_viewer_open(qapp, source_window)
    assert controller.viewer_windows == (source_window,)
    assert source_window.book_session.current_path == image
    close_controller(controller, qapp)


def test_reuse_or_create_creates_then_reuses(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_image = tmp_path / "first.jpg"
    second_image = tmp_path / "second.jpg"
    write_image(first_image)
    write_image(second_image)
    controller = make_controller(tmp_path, qapp)
    controller.settings["open_viewer_behavior"] = "reuse_or_create"

    first = controller.open_path(first_image)
    second = controller.open_path(second_image)

    finish_viewer_open(qapp, first)
    assert second is first
    assert controller.viewer_windows == (first,)
    assert first.book_session.current_path == second_image
    close_controller(controller, qapp)


def test_invalid_open_behavior_falls_back_to_reuse_or_create(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_image = tmp_path / "first.jpg"
    second_image = tmp_path / "second.jpg"
    write_image(first_image)
    write_image(second_image)
    controller = make_controller(tmp_path, qapp)
    controller.settings["open_viewer_behavior"] = "invalid-value"

    first = controller.open_path(first_image)
    second = controller.open_path(second_image)

    assert second is first
    assert len(controller.viewer_windows) == 1
    close_controller(controller, qapp)


def test_two_viewers_hold_independent_books_and_sessions(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first_image = tmp_path / "first" / "1.jpg"
    second_image = tmp_path / "second" / "1.jpg"
    write_image(first_image)
    write_image(second_image)
    controller = make_controller(tmp_path, qapp)

    first = controller.open_path(first_image, open_in_new_window=True)
    second = controller.open_path(second_image, open_in_new_window=True)

    finish_viewer_open(qapp, first)
    finish_viewer_open(qapp, second)
    assert first.book_session is not second.book_session
    assert first.book_session.current_path == first_image
    assert second.book_session.current_path == second_image

    controller.close_viewer_window(first)

    assert controller.viewer_windows == (second,)
    assert second.book_session.current_path == second_image
    close_controller(controller, qapp)


def test_last_viewer_requests_application_exit_once(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = make_controller(tmp_path, qapp)
    exit_requests: list[bool] = []
    controller.exit_requested.connect(lambda: exit_requests.append(True))
    window = controller.create_viewer_window()

    controller.close_viewer_window(window)
    controller.close_viewer_window(window)

    assert exit_requests == [True]
    assert controller.viewer_windows == ()
    qapp.processEvents()
    controller.shutdown()


def test_browser_is_singleton_and_coexists_with_viewers(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    image = tmp_path / "book.jpg"
    write_image(image)
    controller = make_controller(tmp_path, qapp)

    first_browser = controller.create_browser_window()
    second_browser = controller.create_browser_window()
    viewer = controller.open_path(image)

    assert first_browser is second_browser
    assert controller.get_browser_window() is first_browser
    assert controller.viewer_windows == (viewer,)
    close_controller(controller, qapp)


def test_browser_item_open_delegates_to_controller_open_path(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    image = tmp_path / "selected.jpg"
    write_image(image)
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    browser.set_current_folder(tmp_path)
    assert browser.wait_for_scan()
    opened: list[tuple[str, bool | None]] = []

    def record_open(path, *, open_in_new_window=None):
        opened.append((str(path), open_in_new_window))
        return controller.create_viewer_window()

    monkeypatch.setattr(controller, "open_path", record_open)
    row = browser.item_model.row_for_path(image)

    browser.open_item(browser.item_model.index(row, 0))

    assert opened == [(str(image.absolute()), None)]
    close_controller(controller, qapp)


def test_last_viewer_does_not_exit_while_browser_exists(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    viewer = controller.create_viewer_window()
    exit_requests: list[bool] = []
    controller.exit_requested.connect(lambda: exit_requests.append(True))

    controller.close_viewer_window(viewer)

    assert controller.viewer_windows == ()
    assert controller.get_browser_window() is browser
    assert exit_requests == []
    browser.close()
    qapp.processEvents()
    assert exit_requests == [True]
    controller.shutdown()


def test_browser_close_keeps_application_alive_while_viewer_exists(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    viewer = controller.create_viewer_window()
    exit_requests: list[bool] = []
    controller.exit_requested.connect(lambda: exit_requests.append(True))

    browser.close()
    qapp.processEvents()

    assert controller.get_browser_window() is None
    assert controller.viewer_windows == (viewer,)
    assert exit_requests == []

    viewer.close()
    qapp.processEvents()
    assert exit_requests == [True]
    controller.shutdown()


def test_active_viewer_book_changes_sync_browser_but_inactive_does_not(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    first_image = tmp_path / "first" / "1.jpg"
    second_image = tmp_path / "second" / "1.jpg"
    third_image = tmp_path / "third" / "1.jpg"
    write_image(first_image)
    write_image(second_image)
    write_image(third_image)
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    selected: list[str] = []
    monkeypatch.setattr(browser, "select_path", lambda path: selected.append(str(path)))
    first = controller.open_path(first_image, open_in_new_window=True)
    second = controller.open_path(second_image, open_in_new_window=True)
    finish_viewer_open(qapp, first)
    finish_viewer_open(qapp, second)
    selected.clear()

    first.open_path(third_image)
    finish_viewer_open(qapp, first)
    assert selected == []

    second.open_path(first_image)
    finish_viewer_open(qapp, second)
    assert selected == [str(first_image)]
    close_controller(controller, qapp)


def test_viewer_sync_same_parent_does_not_add_browser_history_but_new_parent_does(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    shelf = tmp_path / "shelf"
    first = shelf / "first" / "1.jpg"
    same_parent = shelf / "first" / "2.jpg"
    different = shelf / "second" / "1.jpg"
    write_image(first)
    write_image(same_parent)
    write_image(different)
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.set("last_browser_path", str(shelf))
    config.save()
    controller = ApplicationController(qapp, config_manager=ConfigManager(config.path))
    browser = controller.create_browser_window()
    assert browser.wait_for_scan()
    viewer = controller.open_path(first)
    finish_viewer_open(qapp, viewer)
    assert browser.wait_for_scan()
    after_first = len(browser.navigation_history)

    assert viewer.open_path(same_parent)
    finish_viewer_open(qapp, viewer)
    assert browser.wait_for_scan()
    assert len(browser.navigation_history) == after_first
    assert browser.current_path == first.parent.absolute()

    assert viewer.open_path(different)
    finish_viewer_open(qapp, viewer)
    assert browser.wait_for_scan()
    assert len(browser.navigation_history) == after_first + 1
    assert browser.current_path == different.parent.absolute()
    close_controller(controller, qapp)


def test_bring_to_front_does_not_enable_always_on_top(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = make_controller(tmp_path, qapp)
    widget = QWidget()
    always_on_top_before = bool(widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    controller.bring_window_to_front_once(widget)
    qapp.processEvents()

    assert bool(widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) == always_on_top_before
    widget.close()
    controller.shutdown()


def test_bring_to_front_setting_can_disable_front_operation(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    image = tmp_path / "page.jpg"
    write_image(image)
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({"bring_viewer_to_front_on_open": False})
    calls: list[bool] = []
    monkeypatch.setattr(
        controller,
        "bring_window_to_front_once",
        lambda _window: calls.append(True),
    )

    controller.open_path(image)

    assert calls == []
    close_controller(controller, qapp)


def test_browser_explicit_new_always_creates_new_viewer(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "first" / "1.jpg"
    second = tmp_path / "second" / "1.jpg"
    write_image(first)
    write_image(second)
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({"open_viewer_behavior": "reuse_active"})

    first_viewer = controller._handle_browser_open_request(str(first), True)
    second_viewer = controller._handle_browser_open_request(str(second), True)

    assert first_viewer is not second_viewer
    assert len(controller.viewer_windows) == 2
    close_controller(controller, qapp)


def test_loop_book_navigation_setting_wraps_to_first_book(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "books" / "1" / "1.jpg"
    last = tmp_path / "books" / "2" / "1.jpg"
    write_image(first)
    write_image(last)
    controller = make_controller(tmp_path, qapp)
    controller.config.apply({"loop_book_navigation": True})
    viewer = controller.open_path(last)
    finish_viewer_open(qapp, viewer)

    result = controller.open_adjacent_book(viewer, 1)

    assert result == "searching"
    assert wait_until(
        qapp,
        lambda: viewer.book_session.current_path == first.parent,
    )
    close_controller(controller, qapp)


def test_book_navigation_stops_at_edge_when_loop_is_disabled(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "books" / "1" / "1.jpg"
    last = tmp_path / "books" / "2" / "1.jpg"
    write_image(first)
    write_image(last)
    controller = make_controller(tmp_path, qapp)
    viewer = controller.open_path(first)
    finish_viewer_open(qapp, viewer)

    result = controller.open_adjacent_book(viewer, -1)

    assert result == "searching"
    assert wait_until(
        qapp,
        lambda: viewer.status.currentMessage() == "前の書庫はありません",
    )
    assert viewer.book_session.current_path == first
    close_controller(controller, qapp)


def test_book_candidates_use_natural_order_and_adjacent_open_stays_in_background(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch,
) -> None:
    first = tmp_path / "books" / "book1" / "1.jpg"
    write_image(first)
    (tmp_path / "books" / "book2.zip").write_bytes(b"not-opened-by-this-test")
    (tmp_path / "books" / "book10.cbz").write_bytes(b"not-opened-by-this-test")
    controller = make_controller(tmp_path, qapp)
    viewer = controller.open_path(first)
    finish_viewer_open(qapp, viewer)
    opened: list[tuple[Path, bool | None]] = []

    def capture_open(_window, path, *, bring_to_front=None):
        opened.append((Path(path), bring_to_front))
        return True

    monkeypatch.setattr(controller, "_open_path_in_viewer", capture_open)

    result = controller.open_adjacent_book(viewer, 1)

    assert result == "searching"
    assert wait_until(qapp, lambda: bool(opened))
    assert opened == [(tmp_path / "books" / "book2.zip", False)]
    close_controller(controller, qapp)


def test_book_candidates_include_external_archives_and_hide_later_rar_volumes(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "books" / "book1" / "1.jpg"
    write_image(first)
    for name in (
        "book2.rar",
        "book3.cbr",
        "book4.7z",
        "book5.cb7",
        "series.part1.rar",
        "series.part2.rar",
        "series.r00",
    ):
        (tmp_path / "books" / name).write_bytes(b"archive")
    controller = make_controller(tmp_path, qapp)
    viewer = controller.open_path(first)
    finish_viewer_open(qapp, viewer)

    opened: list[Path] = []
    controller._open_path_in_viewer = (
        lambda _window, path, **_kwargs: opened.append(Path(path)) or True
    )
    assert controller.open_adjacent_book(viewer, 1) == "searching"
    assert wait_until(qapp, lambda: bool(opened))
    assert opened == [tmp_path / "books" / "book2.rar"]
    close_controller(controller, qapp)


def test_browser_image_open_uses_visible_sort_order_as_viewer_topology(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "browser-order"
    images = [
        folder / "page1 {zpi$r=1}.jpg",
        folder / "page02.jpg",
        folder / "page2 {zpi$r=5}.jpg",
        folder / "page10 {zpi$r=3}.jpg",
    ]
    for index, path in enumerate(images):
        write_image(path)
        with path.open("ab") as stream:
            stream.write(b"x" * (index * 31))
        timestamp = (1, 1, 3, 2)[index] * 1_000_000_000
        path.touch()
        os.utime(path, ns=(timestamp, timestamp))

    controller = make_controller(tmp_path, qapp)
    controller._restore_on_start = False
    controller.config.apply({"view_mode": "single"})
    browser = controller.create_browser_window()
    try:
        assert browser.set_current_folder(folder)
        assert browser.wait_for_scan()
        qapp.processEvents()

        for sort_key, sort_order in (
            ("name", "ascending"),
            ("name", "descending"),
            ("modified_time", "ascending"),
            ("modified_time", "descending"),
            ("file_size", "ascending"),
            ("rating", "ascending"),
            ("rating", "descending"),
        ):
            controller.config.apply(
                {
                    "browser_sort_key": sort_key,
                    "browser_sort_order": sort_order,
                }
            )
            qapp.processEvents()
            ordered = tuple(
                str(item.path)
                for item in browser.items
                if item.kind is BrowserItemKind.IMAGE
            )
            assert len(ordered) == len(images)
            selected = ordered[1]
            row = browser.item_model.row_for_path(selected)
            assert row >= 0

            browser.open_item(browser.item_model.index(row, 0))
            viewer = controller.get_active_viewer()
            assert viewer is not None
            finish_viewer_open(qapp, viewer)

            assert tuple(viewer.model.image_ids) == ordered
            assert viewer.model.focused_index == 1
            assert viewer.book_session.folder_listing_snapshot is not None
            assert (
                viewer.book_session.folder_listing_snapshot.selected_index
                == 1
            )
            runtime = viewer.book_session.page_list_runtime
            assert runtime is not None and runtime.image_ids == ordered

            viewer.next_page()
            assert wait_until(
                qapp,
                lambda: viewer.presentation_state.displayed_page == 2,
            )
            viewer.previous_page()
            assert wait_until(
                qapp,
                lambda: viewer.presentation_state.displayed_page == 1,
            )
    finally:
        close_controller(controller, qapp)


def test_browser_order_snapshot_survives_reload_and_filter_until_reopen(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "browser-snapshot"
    paths = [
        folder / name
        for name in (
            "keep1 {zpi$r=5}.jpg",
            "drop2 {zpi$r=5}.jpg",
            "keep10 {zpi$r=3}.jpg",
            "keep20 {zpi$r=4}.jpg",
        )
    ]
    for path in paths:
        write_image(path)

    controller = make_controller(tmp_path, qapp)
    controller._restore_on_start = False
    controller.config.apply(
        {
            "view_mode": "single",
            "browser_sort_key": "name",
            "browser_sort_order": "descending",
        }
    )
    browser = controller.create_browser_window()
    try:
        assert browser.set_current_folder(folder)
        assert browser.wait_for_scan()
        qapp.processEvents()
        descending = tuple(
            str(item.path)
            for item in browser.items
            if item.kind is BrowserItemKind.IMAGE
        )
        selected = descending[1]
        row = browser.item_model.row_for_path(selected)
        browser.open_item(browser.item_model.index(row, 0))
        viewer = controller.get_active_viewer()
        assert viewer is not None
        finish_viewer_open(qapp, viewer)
        assert tuple(viewer.model.image_ids) == descending

        # Browser changes do not mutate the already-open book, and a plain
        # Viewer reload retains the immutable open-time topology snapshot.
        controller.config.apply({"browser_sort_order": "ascending"})
        qapp.processEvents()
        ascending = tuple(
            str(item.path)
            for item in browser.items
            if item.kind is BrowserItemKind.IMAGE
        )
        assert ascending == tuple(reversed(descending))
        assert tuple(viewer.model.image_ids) == descending
        viewer.reload_current_book()
        finish_viewer_open(qapp, viewer)
        assert tuple(viewer.model.image_ids) == descending

        # A fresh Browser-originated open takes the new visible order.
        row = browser.item_model.row_for_path(selected)
        browser.open_item(browser.item_model.index(row, 0))
        finish_viewer_open(qapp, viewer)
        assert tuple(viewer.model.image_ids) == ascending

        # Search and rating predicates compose before sorting. The Viewer
        # receives this final order and does not rediscover omitted files.
        browser._set_browser_filter(
            BrowserFilterState.normalized(
                search_text="keep",
                rating_mode=RatingFilterMode.AT_LEAST,
                rating_reference=4,
            )
        )
        qapp.processEvents()
        visible = tuple(
            str(item.path)
            for item in browser.items
            if item.kind is BrowserItemKind.IMAGE
        )
        assert visible == (str(paths[0]), str(paths[3]))
        hidden = paths[1]
        selected = visible[0]
        row = browser.item_model.row_for_path(selected)
        browser.open_item(browser.item_model.index(row, 0))
        finish_viewer_open(qapp, viewer)
        assert tuple(viewer.model.image_ids) == visible
        assert str(hidden) not in viewer.model.image_ids
        assert viewer.book_session.page_list_runtime is not None
        assert viewer.book_session.page_list_runtime.image_ids == visible

        # A controller/direct open has no Browser authority and preserves the
        # existing FolderImageSource default listing behavior.
        controller.open_path(hidden)
        finish_viewer_open(qapp, viewer)
        assert viewer.book_session.folder_listing_snapshot is None
        assert set(viewer.model.image_ids) == {str(path) for path in paths}
    finally:
        close_controller(controller, qapp)


def test_random_reshuffle_preserves_viewer_open_time_snapshot(tmp_path, qapp):
    folder = tmp_path / "random-snapshot"
    for number in range(25):
        write_image(folder / f"本{number}.jpg")
    controller = make_controller(tmp_path, qapp)
    controller._restore_on_start = False
    controller.config.apply({"view_mode": "single", "browser_sort_key": "random",
                             "browser_random_seed": 54321})
    browser = controller.create_browser_window()
    try:
        assert browser.set_current_folder(folder)
        assert browser.wait_for_scan()
        qapp.processEvents()
        before = tuple(str(i.path) for i in browser.items)
        browser.open_item(browser.item_model.index(10, 0))
        viewer = controller.get_active_viewer()
        finish_viewer_open(qapp, viewer)
        assert tuple(viewer.model.image_ids) == before
        browser.browser_sort_key_combo.activated.emit(14)
        qapp.processEvents()
        assert browser.browser_random_seed != 54321
        after = tuple(str(i.path) for i in browser.items)
        assert tuple(viewer.model.image_ids) == before
        viewer.reload_current_book()
        finish_viewer_open(qapp, viewer)
        assert tuple(viewer.model.image_ids) == before
        browser.open_item(browser.item_model.index(10, 0))
        finish_viewer_open(qapp, viewer)
        assert tuple(viewer.model.image_ids) == after
        assert viewer.book_session.page_list_runtime.image_ids == after
    finally:
        close_controller(controller, qapp)


def test_viewer_roundtrip_search_is_one_shot_and_never_history_state(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder_a = tmp_path / "A"
    folder_b = tmp_path / "B"
    image = folder_a / "cat.jpg"
    write_image(image)
    folder_b.mkdir()
    controller = make_controller(tmp_path, qapp)
    controller._restore_on_start = False
    browser = controller.create_browser_window()

    try:
        assert browser.set_current_folder(folder_a)
        assert browser.wait_for_scan()
        activate_browser_search(browser, "cat", persist=True)
        row = browser.item_model.row_for_path(image)
        browser.open_item(browser.item_model.index(row, 0))
        viewer = controller.get_active_viewer()
        assert viewer is not None
        finish_viewer_open(qapp, viewer)

        assert browser.active_search_query == "cat"
        controller._on_browser_activated(browser)
        assert browser.active_search_query == "cat"
        assert controller._viewer_search_return_context is None

        assert browser.navigate_to(folder_b)
        assert browser.wait_for_scan()
        assert browser.active_search_query == ""
        assert browser.browser_search_edit.text() == ""
        assert browser.go_back()
        assert browser.wait_for_scan()
        assert browser.current_path == folder_a.absolute()
        assert browser.active_search_query == ""
        restored = ConfigManager(controller.config.path).load()
        assert "cat" in restored["browser_search_history"]
    finally:
        close_controller(controller, qapp)


def test_viewer_roundtrip_context_is_invalidated_by_edit_clear_and_setting_off(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "folder"
    cat = folder / "cat.jpg"
    dog = folder / "dog.jpg"
    write_image(cat)
    write_image(dog)
    controller = make_controller(tmp_path, qapp)
    controller._restore_on_start = False
    browser = controller.create_browser_window()

    try:
        assert browser.set_current_folder(folder)
        assert browser.wait_for_scan()
        activate_browser_search(browser, "cat", persist=True)
        row = browser.item_model.row_for_path(cat)
        browser.open_item(browser.item_model.index(row, 0))
        viewer = controller.get_active_viewer()
        assert viewer is not None
        finish_viewer_open(qapp, viewer)
        assert controller._viewer_search_return_context is not None

        browser.browser_search_edit.setText("dog")
        browser._browser_search_timer.stop()
        browser._apply_pending_browser_search()
        assert controller._viewer_search_return_context is None
        controller._on_browser_activated(browser)
        assert browser.active_search_query == "dog"

        activate_browser_search(browser, "cat")
        row = browser.item_model.row_for_path(cat)
        browser.open_item(browser.item_model.index(row, 0))
        finish_viewer_open(qapp, viewer)
        assert controller._viewer_search_return_context is not None
        browser.browser_search_edit.clear()
        browser._browser_search_timer.stop()
        browser._apply_pending_browser_search()
        assert controller._viewer_search_return_context is None
        assert browser.active_search_query == ""

        activate_browser_search(browser, "cat")
        row = browser.item_model.row_for_path(cat)
        browser.open_item(browser.item_model.index(row, 0))
        finish_viewer_open(qapp, viewer)
        assert controller._viewer_search_return_context is not None
        controller.config.apply(
            {"browser_preserve_search_for_viewer_roundtrip": False}
        )
        assert controller._viewer_search_return_context is None
        assert browser.active_search_query == ""

        activate_browser_search(browser, "cat")
        row = browser.item_model.row_for_path(cat)
        browser.open_item(browser.item_model.index(row, 0))
        finish_viewer_open(qapp, viewer)
        assert controller._viewer_search_return_context is None
        assert browser.active_search_query == ""
        assert "cat" in browser.search_history.entries
    finally:
        close_controller(controller, qapp)


def test_viewer_image_sync_uses_snapshot_identity_and_minimal_scroll(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "images"
    paths = tuple(folder / f"{number:03}.jpg" for number in range(80))
    for path in paths:
        write_image(path)
    controller = make_controller(tmp_path, qapp)
    controller._restore_on_start = False
    browser = controller.create_browser_window()
    browser.resize(500, 360)
    browser.show()

    try:
        assert browser.set_current_folder(folder)
        assert browser.wait_for_scan()
        qapp.processEvents()
        snapshot = browser.adjacent_book_snapshot(folder)
        assert snapshot is not None

        visible_index = browser._visible_anchor_index()
        visible_item = browser.item_model.item_at(visible_index)
        assert visible_item is not None
        visible_target = visible_item.path
        before_scroll = browser.list_view.verticalScrollBar().value()
        assert browser.synchronize_viewer_item(
            visible_target,
            expected_parent=folder,
        )
        assert browser.list_view.verticalScrollBar().value() == before_scroll

        offscreen_target = paths[-1]
        assert browser.synchronize_viewer_item(
            offscreen_target,
            expected_parent=folder,
        )
        assert browser.list_view.verticalScrollBar().value() > before_scroll
        selected = browser.item_model.item_at(browser.list_view.currentIndex())
        assert selected is not None and selected.path == offscreen_target.absolute()

        first = paths[20]
        folder_snapshot = controller._folder_snapshot_from_browser_navigation(
            snapshot,
            str(first),
        )
        viewer = controller.open_path(
            first,
            folder_snapshot=folder_snapshot,
            browser_snapshot=snapshot,
        )
        finish_viewer_open(qapp, viewer)
        selected = browser.item_model.item_at(browser.list_view.currentIndex())
        assert selected is not None and selected.path == first.absolute()

        assert controller.open_adjacent_book(viewer, 1) == "opened"
        finish_viewer_open(qapp, viewer)
        selected = browser.item_model.item_at(browser.list_view.currentIndex())
        assert selected is not None and selected.path == paths[21].absolute()

        for target in paths[22:25]:
            controller._synchronize_browser_to_viewer_item(viewer, target)
        selected = browser.item_model.item_at(browser.list_view.currentIndex())
        assert selected is not None and selected.path == paths[24].absolute()
    finally:
        close_controller(controller, qapp)


def test_viewer_folder_book_sync_selects_parent_item_and_adjacent_folder(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "books"
    first = parent / "01.Folder"
    second = parent / "02.Folder"
    write_image(first / "1.jpg")
    write_image(second / "1.jpg")
    controller = make_controller(tmp_path, qapp)
    controller._restore_on_start = False
    browser = controller.create_browser_window()

    try:
        assert browser.set_current_folder(parent)
        assert browser.wait_for_scan()
        snapshot = browser.adjacent_book_snapshot(parent)
        assert snapshot is not None
        viewer = controller.open_path(first, browser_snapshot=snapshot)
        finish_viewer_open(qapp, viewer)

        assert browser.current_path == parent.absolute()
        selected = browser.item_model.item_at(browser.list_view.currentIndex())
        assert selected is not None and selected.path == first.absolute()

        assert controller.open_adjacent_book(viewer, 1) == "opened"
        finish_viewer_open(qapp, viewer)
        assert browser.current_path == parent.absolute()
        selected = browser.item_model.item_at(browser.list_view.currentIndex())
        assert selected is not None and selected.path == second.absolute()
    finally:
        close_controller(controller, qapp)


def test_existing_archive_sync_uses_the_same_snapshot_selection_path(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    parent = tmp_path / "archives"
    first = parent / "01.zip"
    second = parent / "02.zip"
    write_archive(first)
    write_archive(second)
    controller = make_controller(tmp_path, qapp)
    controller._restore_on_start = False
    browser = controller.create_browser_window()

    try:
        assert browser.set_current_folder(parent)
        assert browser.wait_for_scan()
        snapshot = browser.adjacent_book_snapshot(parent)
        assert snapshot is not None
        viewer = controller.open_path(first, browser_snapshot=snapshot)
        finish_viewer_open(qapp, viewer)
        selected = browser.item_model.item_at(browser.list_view.currentIndex())
        assert selected is not None and selected.path == first.absolute()

        assert controller.open_adjacent_book(viewer, 1) == "opened"
        finish_viewer_open(qapp, viewer)
        selected = browser.item_model.item_at(browser.list_view.currentIndex())
        assert selected is not None and selected.path == second.absolute()
    finally:
        close_controller(controller, qapp)


def test_viewer_sync_leaves_selection_neutral_when_rating_hides_exact_item(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    folder = tmp_path / "rated"
    visible = folder / "visible {zpi$r=5}.jpg"
    hidden = folder / "hidden {zpi$r=1}.jpg"
    write_image(visible)
    write_image(hidden)
    controller = make_controller(tmp_path, qapp)
    controller._restore_on_start = False
    browser = controller.create_browser_window()

    try:
        assert browser.set_current_folder(folder)
        assert browser.wait_for_scan()
        snapshot = browser.adjacent_book_snapshot(folder)
        assert snapshot is not None
        viewer = controller.open_path(
            hidden,
            folder_snapshot=controller._folder_snapshot_from_browser_navigation(
                snapshot,
                str(hidden),
            ),
            browser_snapshot=snapshot,
        )
        finish_viewer_open(qapp, viewer)
        browser._set_browser_filter(
            BrowserFilterState.normalized(
                rating_mode=RatingFilterMode.AT_LEAST,
                rating_reference=4,
            )
        )
        qapp.processEvents()

        controller._on_viewer_book_changed(viewer, str(hidden))

        assert browser.item_model.row_for_path(hidden) < 0
        assert not browser.list_view.currentIndex().isValid()
        assert browser.list_view.selectionModel().selectedIndexes() == []
        assert browser.browser_filter_state.rating_reference == 4
    finally:
        close_controller(controller, qapp)


def test_browser_folder_gesture_uses_browser_path_not_viewer_source(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    viewer_image = tmp_path / "viewer-source" / "1.jpg"
    siblings = [
        tmp_path / "browser-folders" / name
        for name in ("01", "02", "10")
    ]
    write_image(viewer_image)
    for folder in siblings:
        folder.mkdir(parents=True)
    (siblings[0].parent / "03.txt").write_text("file", encoding="utf-8")
    controller = make_controller(tmp_path, qapp)
    controller._restore_on_start = False
    viewer = controller.open_path(viewer_image)
    finish_viewer_open(qapp, viewer)
    browser = controller.create_browser_window()
    browser.set_current_folder(siblings[1])
    assert browser.wait_for_scan()

    browser.list_view.folderGestureRecognized.emit("L")

    assert id(browser) in controller._adjacent_request_by_window
    assert wait_until(qapp, lambda: browser.current_path == siblings[0])
    assert viewer.book_session.current_path == viewer_image

    browser.list_view.folderGestureRecognized.emit("L")
    QTest.qWait(50)
    qapp.processEvents()
    assert browser.current_path == siblings[0]
    assert viewer.book_session.current_path == viewer_image
    close_controller(controller, qapp)


def test_missing_current_book_is_unavailable_without_exception(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    first = tmp_path / "books" / "book1" / "1.jpg"
    write_image(first)
    controller = make_controller(tmp_path, qapp)
    viewer = controller.open_path(first)
    finish_viewer_open(qapp, viewer)
    viewer.book_session.current_path = tmp_path / "books" / "missing.zip"

    assert controller.open_adjacent_book(viewer, 1) == "searching"
    assert wait_until(
        qapp,
        lambda: viewer.status.currentMessage() == "移動できる書庫がありません",
    )
    close_controller(controller, qapp)


def test_settings_changes_apply_to_existing_browser_and_viewer(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller = make_controller(tmp_path, qapp)
    browser = controller.create_browser_window()
    viewer = controller.create_viewer_window()

    controller.config.apply(
        {
            "join_spread_pages": True,
            "gap": 37,
            "thumbnail_size": 230,
            "viewer_prefetch_preset": "memory_saver",
            "viewer_memory_mode": "minimal",
        }
    )
    qapp.processEvents()

    assert viewer.join_spread_pages is True
    assert viewer.viewer.join_spread_pages is True
    assert viewer.gap == 37
    assert viewer.prefetch_preset == "memory_saver"
    assert viewer.image_prefetch_forward_units == 2
    assert viewer.pdf_prefetch_backward_units == 0
    assert viewer.viewer_memory_mode == "minimal"
    assert viewer.image_cache.cache_byte_budget_mib == 128
    assert browser.thumbnail_size == 230
    assert browser.list_view.iconSize().width() == 230
    close_controller(controller, qapp)


def test_shutdown_is_idempotent(tmp_path: Path, qapp: QApplication) -> None:
    controller = make_controller(tmp_path, qapp)
    controller.start()

    controller.shutdown()
    controller.shutdown()

    for window in controller.viewer_windows:
        window.close()
    browser = controller.get_browser_window()
    if browser is not None:
        browser.close()
    qapp.processEvents()
