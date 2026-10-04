from __future__ import annotations

from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.adjacent_book_search import (
    AdjacentBookBrowserSnapshot,
    AdjacentBookSearchStatus,
    AdjacentBookSnapshotEntry,
)
from app.application_controller import ApplicationController
from app.config_manager import ConfigManager
from app.viewer_widget import ViewerWidget


def _write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (12, 18), "white") as image:
        image.save(path)


def _snapshot(paths: tuple[Path, ...]) -> AdjacentBookBrowserSnapshot:
    return AdjacentBookBrowserSnapshot(
        parent_folder=str(paths[0].parent),
        scan_generation=17,
        entries=tuple(
            AdjacentBookSnapshotEntry(
                str(path),
                "image",
                path.suffix,
                path.name.casefold(),
                file_size=path.stat().st_size,
                modified_time_ns=path.stat().st_mtime_ns,
            )
            for path in paths
        ),
        sort_identity="rating:descending:folders_first=1",
        filter_identity="search='kept':rating=at_least:reference=3",
    )


def _send_mouse_button(widget: ViewerWidget, button: Qt.MouseButton) -> None:
    local = QPoint(20, 20)
    for event_type, buttons in (
        (QEvent.Type.MouseButtonPress, button),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        QApplication.sendEvent(
            widget,
            QMouseEvent(
                event_type,
                QPointF(local),
                QPointF(widget.mapToGlobal(local)),
                button,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            ),
        )


def _finish_open(qapp: QApplication, viewer) -> None:
    assert viewer.book_session.wait_for_async(2000)
    deadline = monotonic() + 3.0
    while monotonic() < deadline:
        qapp.processEvents()
        if viewer._pending_book_open_projection is None:
            break
        QTest.qWait(5)
    qapp.processEvents()
    assert viewer._pending_book_open_projection is None


def _close_controller(controller: ApplicationController, qapp: QApplication) -> None:
    for window in tuple(controller.viewer_windows):
        window.close()
    qapp.processEvents()
    controller.shutdown()


def _open_snapshot_viewer(
    tmp_path: Path,
    qapp: QApplication,
    order: tuple[str, ...],
    current_index: int,
):
    paths = tuple(tmp_path / name for name in order)
    for path in paths:
        _write_image(path)
    snapshot = _snapshot(paths)
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
    )
    current = paths[current_index]
    viewer = controller.open_path(
        current,
        folder_snapshot=controller._folder_snapshot_from_browser_navigation(
            snapshot,
            str(current),
        ),
        browser_snapshot=snapshot,
    )
    _finish_open(qapp, viewer)
    return controller, viewer, paths, snapshot


def test_snapshot_neighbor_includes_folder_books_but_skips_unsupported_items(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.pdf"
    snapshot = AdjacentBookBrowserSnapshot(
        str(tmp_path),
        3,
        (
            AdjacentBookSnapshotEntry(str(first), "archive", ".zip", "first"),
            AdjacentBookSnapshotEntry(str(tmp_path / "folder"), "folder", "", "folder"),
            AdjacentBookSnapshotEntry(
                str(tmp_path / "unsupported.bin"),
                "other",
                ".bin",
                "unsupported",
                openable_by_nivisviewer=False,
            ),
            AdjacentBookSnapshotEntry(str(second), "pdf", ".pdf", "second"),
        ),
    )

    status, candidate = snapshot.adjacent_viewer_path(first, 1)

    assert status is AdjacentBookSearchStatus.FOUND
    assert Path(candidate or "") == tmp_path / "folder"
    status, candidate = snapshot.adjacent_viewer_path(candidate or "", 1)
    assert status is AdjacentBookSearchStatus.FOUND
    assert Path(candidate or "") == second


@pytest.mark.parametrize(
    ("button", "expected_index"),
    [
        (Qt.MouseButton.BackButton, 0),
        (Qt.MouseButton.ForwardButton, 2),
    ],
)
def test_xbutton_opens_snapshot_neighbor_in_non_filesystem_order(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    button: Qt.MouseButton,
    expected_index: int,
) -> None:
    controller, viewer, paths, _snapshot_value = _open_snapshot_viewer(
        tmp_path,
        qapp,
        ("30.jpg", "10.jpg", "20.jpg"),
        1,
    )
    monkeypatch.setattr(
        controller.adjacent_book_search,
        "search",
        lambda _request: pytest.fail("snapshot navigation must not start a scan"),
    )

    _send_mouse_button(viewer.viewer, button)
    _finish_open(qapp, viewer)

    assert viewer.book_session.current_path == paths[expected_index]
    assert controller.viewer_windows == (viewer,)
    _close_controller(controller, qapp)


@pytest.mark.parametrize(
    ("current_index", "button"),
    [
        (0, Qt.MouseButton.BackButton),
        (2, Qt.MouseButton.ForwardButton),
    ],
)
def test_xbutton_at_snapshot_boundary_does_nothing(
    tmp_path: Path,
    qapp: QApplication,
    current_index: int,
    button: Qt.MouseButton,
) -> None:
    controller, viewer, paths, _snapshot_value = _open_snapshot_viewer(
        tmp_path,
        qapp,
        ("30.jpg", "10.jpg", "20.jpg"),
        current_index,
    )
    generation = viewer.book_session.generation

    _send_mouse_button(viewer.viewer, button)
    qapp.processEvents()

    assert viewer.book_session.current_path == paths[current_index]
    assert viewer.book_session.generation == generation
    assert controller.viewer_windows == (viewer,)
    _close_controller(controller, qapp)


def test_rapid_xbuttons_advance_pending_snapshot_cursor_without_stale_book(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    controller, viewer, paths, snapshot = _open_snapshot_viewer(
        tmp_path,
        qapp,
        ("40.jpg", "10.jpg", "30.jpg", "20.jpg"),
        1,
    )

    _send_mouse_button(viewer.viewer, Qt.MouseButton.ForwardButton)
    assert Path(viewer.browser_navigation_path) == paths[2]
    _send_mouse_button(viewer.viewer, Qt.MouseButton.ForwardButton)
    assert Path(viewer.browser_navigation_path) == paths[3]
    _finish_open(qapp, viewer)

    assert controller.viewer_windows == (viewer,)
    assert viewer.book_session.current_path == paths[3]
    assert viewer.browser_navigation_snapshot is snapshot
    displayed = viewer.presentation_state.displayed
    assert displayed is not None
    assert displayed.token.book.epoch == viewer.book_session.generation
    _close_controller(controller, qapp)


def test_snapshot_book_change_does_not_navigate_browser_that_moved_elsewhere(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, viewer, paths, _snapshot_value = _open_snapshot_viewer(
        tmp_path,
        qapp,
        ("30.jpg", "10.jpg", "20.jpg"),
        1,
    )
    selected: list[str] = []
    browser = SimpleNamespace(
        current_path=tmp_path / "somewhere-else",
        select_path=selected.append,
    )
    with monkeypatch.context() as patch:
        patch.setattr(controller, "get_browser_window", lambda: browser)
        controller._on_viewer_book_changed(viewer, str(paths[2]))

    assert selected == []
    _close_controller(controller, qapp)


def test_xbutton_without_browser_snapshot_does_not_start_filesystem_search(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "direct.jpg"
    _write_image(image)
    controller = ApplicationController(
        qapp,
        config_manager=ConfigManager(tmp_path / "config.json"),
    )
    viewer = controller.open_path(image)
    _finish_open(qapp, viewer)
    searches: list[object] = []
    monkeypatch.setattr(
        controller.adjacent_book_search,
        "search",
        lambda request: searches.append(request) or True,
    )

    _send_mouse_button(viewer.viewer, Qt.MouseButton.ForwardButton)

    assert searches == []
    assert viewer.book_session.current_path == image
    _close_controller(controller, qapp)


def test_xbutton_is_consumed_without_page_command_and_wheel_is_unchanged(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, viewer, _paths, _snapshot_value = _open_snapshot_viewer(
        tmp_path,
        qapp,
        ("30.jpg", "10.jpg", "20.jpg"),
        1,
    )
    page_calls: list[str] = []
    monkeypatch.setattr(viewer, "next_page", lambda **_kwargs: page_calls.append("next"))

    _send_mouse_button(viewer.viewer, Qt.MouseButton.ForwardButton)

    assert page_calls == []

    widget = ViewerWidget()
    next_requests: list[bool] = []
    extra_buttons: list[str] = []
    widget.nextRequested.connect(lambda: next_requests.append(True))
    widget.extraMouseButtonPressed.connect(extra_buttons.append)
    wheel = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(widget, wheel)

    assert next_requests == [True]
    assert extra_buttons == []
    widget.close()
    _close_controller(controller, qapp)
