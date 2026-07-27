from __future__ import annotations

from pathlib import Path
from time import monotonic
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from app.application_controller import ApplicationController
from app.config_manager import ConfigManager


def write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (8, 12), "white") as image:
        image.save(path)


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
    selected.clear()

    first.open_path(third_image)
    assert selected == []

    second.open_path(first_image)
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
    assert browser.wait_for_scan()
    after_first = len(browser.navigation_history)

    assert viewer.open_path(same_parent)
    assert browser.wait_for_scan()
    assert len(browser.navigation_history) == after_first
    assert browser.current_path == first.parent.absolute()

    assert viewer.open_path(different)
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

    opened: list[Path] = []
    controller._open_path_in_viewer = (
        lambda _window, path, **_kwargs: opened.append(Path(path)) or True
    )
    assert controller.open_adjacent_book(viewer, 1) == "searching"
    assert wait_until(qapp, lambda: bool(opened))
    assert opened == [tmp_path / "books" / "book2.rar"]
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
    qapp.processEvents()
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
        }
    )
    qapp.processEvents()

    assert viewer.join_spread_pages is True
    assert viewer.viewer.join_spread_pages is True
    assert viewer.gap == 37
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
