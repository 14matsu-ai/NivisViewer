from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import (
    QCursor,
    QMouseEvent,
    QPalette,
    QWheelEvent,
    QWindow,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.config_manager import ConfigManager
from app.fullscreen_chrome import FullscreenChromeController
from app.image_source import ImageSource
from app.page_model import PageModel
from app.settings_dialog import SettingsDialog
from app.viewer_page_navigation import ViewerPageNavigationController
from app.viewer_page_slider import ViewerPageSlider
from app.viewer_window import ViewerWindow


class _MemorySource(ImageSource):
    def __init__(self, sizes: list[tuple[int, int]]) -> None:
        super().__init__(Path("memory"))
        self.sizes = sizes

    def list_images(self) -> list[str]:
        return [f"{index:02}.jpg" for index in range(len(self.sizes))]

    def open_image(self, image_id: str) -> Image.Image:
        return Image.new("RGB", self.sizes[int(Path(image_id).stem)])

    def display_path(self, image_id: str) -> str:
        return image_id


def _model(
    sizes: list[tuple[int, int]],
    *,
    direction: str = "ltr",
    cover: bool = True,
) -> PageModel:
    model = PageModel()
    model.update_options(
        view_mode="spread",
        reading_direction=direction,
        single_first_page=cover,
        treat_wide_image_as_single=True,
    )
    model.set_source(_MemorySource(sizes))
    return model


def _indexes(model: PageModel) -> list[int]:
    return sorted(slot.page_index for slot in model.spread_at().slots)


def _wheel_event(
    target: QWidget,
    *,
    angle_y: int = 0,
    pixel_y: int = 0,
    angle_x: int = 0,
) -> QWheelEvent:
    return QWheelEvent(
        QPointF(10, 10),
        QPointF(target.mapToGlobal(QPoint(10, 10))),
        QPoint(0, pixel_y),
        QPoint(angle_x, angle_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )


def _wheel(
    target: QWidget,
    *,
    angle_y: int = 0,
    pixel_y: int = 0,
    angle_x: int = 0,
) -> QWheelEvent:
    event = _wheel_event(
        target,
        angle_y=angle_y,
        pixel_y=pixel_y,
        angle_x=angle_x,
    )
    QApplication.sendEvent(target, event)
    return event


def _config(tmp_path: Path) -> ConfigManager:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    return config


def _viewer_with_pages(
    tmp_path: Path,
    qapp: QApplication,
    *,
    page_count: int = 7,
) -> tuple[ViewerWindow, list[Path]]:
    folder = tmp_path / "book"
    folder.mkdir()
    pages: list[Path] = []
    for index in range(page_count):
        page = folder / f"{index:02}.jpg"
        Image.new("RGB", (80, 120), "white").save(page)
        pages.append(page)
    config = _config(tmp_path)
    config.apply(
        {
            "view_mode": "spread",
            "single_first_page": True,
            "viewer_canvas_left_click_action": "next_single_page",
        }
    )
    window = ViewerWindow(config_manager=config)
    assert window.open_path(pages[0])
    assert window.book_session.wait_for_async(2000)
    qapp.processEvents()
    window.resize(640, 480)
    window.show()
    qapp.processEvents()
    return window, pages


@pytest.mark.parametrize("direction", ["ltr", "rtl"])
def test_single_page_navigation_creates_sliding_spreads(direction: str) -> None:
    model = _model([(80, 120)] * 7, direction=direction)
    model.go_to_index(1)

    model.next_single()
    assert model.focused_index == 2
    assert model.logical_page_anchor == 2
    assert model.sliding_spread
    assert _indexes(model) == [2, 3]

    model.next_single()
    assert _indexes(model) == [3, 4]
    model.previous_single()
    assert _indexes(model) == [2, 3]


def test_single_navigation_obeys_cover_wide_and_last_page_rules() -> None:
    portrait = (80, 120)
    wide = (160, 80)
    model = _model([portrait, portrait, portrait, wide, wide, portrait])

    assert _indexes(model) == [0]
    model.next_single()
    assert _indexes(model) == [1, 2]
    model.next_single()
    assert _indexes(model) == [2]
    model.next_single()
    assert _indexes(model) == [3]
    model.next_single()
    assert _indexes(model) == [4]
    model.next_single()
    assert _indexes(model) == [5]
    model.next_single()
    assert _indexes(model) == [5]


def test_delayed_dimensions_do_not_snap_sliding_anchor() -> None:
    model = _model([(80, 120)] * 6)
    model.go_to_index(1)
    model.next_single()

    changed = model.set_image_size(3, (80, 120))

    assert not changed
    assert model.current_index == 2
    assert _indexes(model) == [2, 3]


def test_display_unit_navigation_leaves_sliding_mode() -> None:
    model = _model([(80, 120)] * 7)
    model.go_to_index(1)
    model.next_single()
    assert _indexes(model) == [2, 3]

    model.next()

    assert _indexes(model) == [4, 5]
    assert not model.sliding_spread


def test_page_navigation_controller_never_opens_books() -> None:
    model = _model([(80, 120)] * 3)
    changes: list[int] = []
    controller = ViewerPageNavigationController(model, changes.append)

    assert controller.next_single_page()
    assert changes == [0]
    controller.last_page()
    assert not controller.next_single_page()
    assert model.focused_index == 2


def test_page_list_selection_resolves_focused_index_once(
    tmp_path: Path,
    qapp,
    monkeypatch,
) -> None:
    window, _pages = _viewer_with_pages(tmp_path, qapp, page_count=5)
    last_index = window.model.total_pages - 1
    window.model.go_to_index(last_index)
    resolve_calls = 0
    original_index_for_identity = window.model.index_for_identity

    def counted_index_for_identity(identity: str | None) -> int:
        nonlocal resolve_calls
        resolve_calls += 1
        return original_index_for_identity(identity)

    monkeypatch.setattr(
        window.model,
        "index_for_identity",
        counted_index_for_identity,
    )

    window._sync_page_list_selection()

    assert resolve_calls == 1
    current_item = window.page_list.currentItem()
    assert current_item is not None
    assert current_item.data(Qt.ItemDataRole.UserRole) == last_index

    window.close()
    qapp.processEvents()


def test_page_navigation_does_not_stat_displayed_folder_image(
    tmp_path: Path,
    qapp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, pages = _viewer_with_pages(tmp_path, qapp, page_count=7)
    assert window.image_cache.wait_for_done(2000)
    qapp.processEvents()
    page_keys = {
        str(path.absolute()).casefold()
        for path in pages
    }
    stat_calls: list[str] = []
    original_stat = Path.stat

    def counted_stat(path: Path, *args, **kwargs):
        if str(path.absolute()).casefold() in page_keys:
            stat_calls.append(str(path))
        return original_stat(path, *args, **kwargs)

    previous_index = window.model.focused_index
    with monkeypatch.context() as guard:
        guard.setattr(Path, "stat", counted_stat)
        window.next_page()

    assert window.model.focused_index != previous_index
    assert stat_calls == []
    window.close()
    qapp.processEvents()


def test_slider_angle_wheel_moves_one_page_and_is_accepted(qapp) -> None:
    slider = ViewerPageSlider()
    slider.set_single_page_wheel_enabled(True)
    calls: list[str] = []
    slider.nextSinglePageRequested.connect(lambda: calls.append("next"))
    slider.previousSinglePageRequested.connect(lambda: calls.append("previous"))

    down = _wheel(slider, angle_y=-120)
    up = _wheel(slider, angle_y=120)

    assert down.isAccepted()
    assert up.isAccepted()
    assert calls == ["next", "previous"]


def test_slider_accumulates_high_resolution_pixel_delta(qapp) -> None:
    slider = ViewerPageSlider()
    slider.set_single_page_wheel_enabled(True)
    calls: list[str] = []
    slider.nextSinglePageRequested.connect(lambda: calls.append("next"))

    for _index in range(3):
        _wheel(slider, pixel_y=-10)
    assert calls == []
    _wheel(slider, pixel_y=-10)
    assert calls == ["next"]


def test_slider_consumes_horizontal_wheel_without_navigation(qapp) -> None:
    slider = ViewerPageSlider()
    slider.set_single_page_wheel_enabled(True)
    calls: list[str] = []
    slider.nextSinglePageRequested.connect(lambda: calls.append("next"))

    event = _wheel(slider, angle_x=120)

    assert event.isAccepted()
    assert calls == []


def test_slider_programmatic_sync_does_not_reenter_navigation(qapp) -> None:
    slider = ViewerPageSlider()
    requested: list[int] = []
    slider.focusedPageRequested.connect(requested.append)

    slider.set_page_state(12, 7)

    assert slider.minimum() == 0
    assert slider.maximum() == 11
    assert slider.value() == 7
    assert requested == []


def test_slider_disabled_emits_one_display_unit_without_changing_value(
    qapp,
) -> None:
    slider = ViewerPageSlider()
    slider.set_page_state(10, 5)
    calls: list[str] = []
    slider.nextSinglePageRequested.connect(lambda: calls.append("next"))
    slider.previousSinglePageRequested.connect(lambda: calls.append("previous"))
    slider.nextDisplayUnitRequested.connect(
        lambda: calls.append("next_display")
    )

    event = _wheel(slider, angle_y=-120)

    assert event.isAccepted()
    assert calls == ["next_display"]
    assert slider.value() == 5


def test_slider_enabled_consumes_wheel_even_when_page_cannot_move(qapp) -> None:
    slider = ViewerPageSlider()
    slider.set_page_state(1, 0)
    slider.set_single_page_wheel_enabled(True)
    calls: list[str] = []
    slider.previousSinglePageRequested.connect(lambda: calls.append("previous"))

    event = _wheel(slider, angle_y=120)

    assert event.isAccepted()
    assert calls == ["previous"]
    assert slider.value() == 0


@pytest.mark.parametrize("single_page", [False, True])
def test_slider_two_notches_emit_two_operations(
    qapp,
    single_page: bool,
) -> None:
    slider = ViewerPageSlider()
    slider.set_single_page_wheel_enabled(single_page)
    calls: list[str] = []
    if single_page:
        slider.nextSinglePageRequested.connect(lambda: calls.append("next"))
    else:
        slider.nextDisplayUnitRequested.connect(lambda: calls.append("next"))

    event = _wheel(slider, angle_y=-240)

    assert event.isAccepted()
    assert calls == ["next", "next"]


def test_slider_small_angle_delta_accumulates_one_operation(qapp) -> None:
    slider = ViewerPageSlider()
    calls: list[str] = []
    slider.nextDisplayUnitRequested.connect(lambda: calls.append("next"))

    for _index in range(3):
        _wheel(slider, angle_y=-30)
    assert calls == []
    _wheel(slider, angle_y=-30)
    assert calls == ["next"]


def test_slider_mode_changes_reset_both_delta_accumulators(qapp) -> None:
    slider = ViewerPageSlider()
    display_calls: list[str] = []
    single_calls: list[str] = []
    slider.nextDisplayUnitRequested.connect(
        lambda: display_calls.append("next")
    )
    slider.nextSinglePageRequested.connect(
        lambda: single_calls.append("next")
    )

    _wheel(slider, angle_y=-90)
    slider.set_single_page_wheel_enabled(True)
    _wheel(slider, angle_y=-30)
    assert display_calls == []
    assert single_calls == []

    _wheel(slider, pixel_y=-30)
    slider.set_single_page_wheel_enabled(False)
    _wheel(slider, pixel_y=-10)
    assert display_calls == []
    assert single_calls == []

    slider.set_single_page_wheel_enabled(False)
    _wheel(slider, angle_y=-30)
    assert display_calls == []


def test_slider_wheel_at_book_edges_never_uses_adjacent_navigation(
    tmp_path: Path,
    qapp,
) -> None:
    window, pages = _viewer_with_pages(tmp_path, qapp, page_count=3)
    opened: list[str] = []
    window.auto_open_adjacent_book = True
    window._open_path_handler = (
        lambda *_args: opened.append("path") or object()
    )
    window._adjacent_book_handler = (
        lambda *_args: opened.append("book") or "opened"
    )
    source = window.book_session.source
    source_path = window.book_session.book_key
    window.page_navigation.last_page()
    window.slider.set_single_page_wheel_enabled(True)

    _wheel(window.slider, angle_y=-120)
    qapp.processEvents()

    assert opened == []
    assert window.book_session.source is source
    assert window.book_session.book_key == source_path
    assert window.model.focused_index == len(pages) - 1
    window.close()
    qapp.processEvents()


def test_normal_and_fullscreen_use_the_same_page_slider(
    tmp_path: Path,
    qapp,
    monkeypatch,
) -> None:
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    slider = window.slider
    controller = window.fullscreen_chrome
    monkeypatch.setattr(
        controller,
        "_apply_native_fullscreen_frame",
        lambda _fullscreen: None,
    )

    controller.set_fullscreen_state(
        True,
        hide_ui=window.hide_ui_in_fullscreen,
        hide_cursor=window.hide_cursor_in_fullscreen,
    )
    qapp.processEvents()
    assert controller.slider is slider
    assert slider.parent() is controller.bottom_overlay

    controller.set_fullscreen_state(
        False,
        hide_ui=window.hide_ui_in_fullscreen,
        hide_cursor=window.hide_cursor_in_fullscreen,
    )
    qapp.processEvents()
    assert window.slider is slider
    window.close()
    qapp.processEvents()


def test_bottom_overlay_and_children_reuse_slider_wheel_path_once(
    qapp,
    monkeypatch,
) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = ViewerPageSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
    )
    child_label = QLabel("ページ", controller.fullscreen_status_bar)
    controller.fullscreen_status_bar.addPermanentWidget(child_label)
    slider.set_page_state(10, 5)
    slider.set_single_page_wheel_enabled(True)
    single_calls: list[str] = []
    display_calls: list[str] = []
    slider.nextSinglePageRequested.connect(
        lambda: single_calls.append("next")
    )
    slider.nextDisplayUnitRequested.connect(
        lambda: display_calls.append("next")
    )
    window.resize(640, 480)
    window.show()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    controller.show_bottom()
    qapp.processEvents()

    slider_event = _wheel(slider, angle_y=-120)
    assert slider_event.isAccepted()
    assert single_calls == ["next"]

    def unexpected_wheel_event(_event: QWheelEvent) -> None:
        raise AssertionError("overlay wheel must not call slider.wheelEvent")

    monkeypatch.setattr(slider, "wheelEvent", unexpected_wheel_event)
    for target in (
        controller.bottom_overlay,
        controller.fullscreen_status_bar,
        child_label,
    ):
        event = _wheel(target, angle_y=-120)
        assert event.isAccepted()

    assert single_calls == ["next"] * 4
    assert display_calls == []
    assert slider.value() == 5

    horizontal_event = _wheel_event(
        controller.bottom_overlay,
        angle_x=120,
    )
    assert (
        controller.eventFilter(
            controller.bottom_overlay,
            horizontal_event,
        )
        is True
    )
    assert horizontal_event.isAccepted()
    assert single_calls == ["next"] * 4

    slider_child = QWidget(slider)
    child_event = QWheelEvent(
        QPointF(1, 1),
        QPointF(slider.mapToGlobal(QPoint(1, 1))),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    assert controller.eventFilter(slider_child, child_event) is False
    assert single_calls == ["next"] * 4

    slider.set_single_page_wheel_enabled(False)
    event = _wheel(controller.fullscreen_status_bar, angle_y=-120)
    assert event.isAccepted()
    assert display_calls == ["next"]

    controller.set_active(False)
    controller.shutdown()
    window.close()


def test_fullscreen_wheel_filter_handles_only_current_window_bottom_region(
    qapp,
    monkeypatch,
) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = ViewerPageSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
    )
    window.resize(640, 480)
    window.show()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    controller.show_bottom()
    qapp.processEvents()
    window_handle = window.windowHandle()
    assert window_handle is not None
    region = controller._bottom_wheel_region_global()
    calls: list[str] = []
    slider.nextDisplayUnitRequested.connect(lambda: calls.append("next"))
    process_calls = 0
    original_process = slider.process_wheel_delta

    def counted_process(angle_delta: QPoint, pixel_delta: QPoint) -> bool:
        nonlocal process_calls
        process_calls += 1
        return original_process(angle_delta, pixel_delta)

    monkeypatch.setattr(slider, "process_wheel_delta", counted_process)
    for x in (region.left(), region.center().x(), region.right()):
        event = QWheelEvent(
            QPointF(1, 1),
            QPointF(x, region.bottom()),
            QPoint(),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        assert controller.eventFilter(window_handle, event) is True
        assert event.isAccepted()

    assert calls == ["next"] * 3
    assert process_calls == 3

    above = QWheelEvent(
        QPointF(1, 1),
        QPointF(region.center().x(), region.top() - 1),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    assert controller.eventFilter(window_handle, above) is False
    assert process_calls == 3

    other_window = QWindow()
    other_window.create()
    other_event = QWheelEvent(
        QPointF(1, 1),
        QPointF(region.center().x(), region.bottom()),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    assert controller.eventFilter(other_window, other_event) is False
    assert process_calls == 3

    controller.set_active(False)
    controller.shutdown()
    other_window.destroy()
    window.close()


def test_bottom_wheel_region_uses_only_visible_overlay_geometry(
    qapp,
    monkeypatch,
) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = ViewerPageSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
    )
    monkeypatch.setattr(
        controller,
        "_apply_native_fullscreen_frame",
        lambda _fullscreen: None,
    )
    window.resize(640, 480)
    window.show()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    controller.show_bottom()
    qapp.processEvents()
    window_handle = window.windowHandle()
    assert window_handle is not None
    process_calls = 0
    original_process = slider.process_wheel_delta

    def counted_process(angle_delta: QPoint, pixel_delta: QPoint) -> bool:
        nonlocal process_calls
        process_calls += 1
        return original_process(angle_delta, pixel_delta)

    monkeypatch.setattr(slider, "process_wheel_delta", counted_process)
    visible_region = controller._bottom_wheel_region_global()
    overlay_region = QRect(
        controller.bottom_overlay.mapToGlobal(QPoint(0, 0)),
        controller.bottom_overlay.size(),
    )
    assert not visible_region.isEmpty()
    assert visible_region.top() == overlay_region.top()
    assert visible_region.contains(overlay_region.center())

    viewer_center = viewer.mapToGlobal(viewer.rect().center())
    assert not visible_region.contains(viewer_center)
    viewer_qwindow_event = QWheelEvent(
        QPointF(1, 1),
        QPointF(viewer_center),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    assert (
        controller.eventFilter(window_handle, viewer_qwindow_event)
        is False
    )
    assert process_calls == 0

    physical_bottom = QPoint(
        visible_region.center().x(),
        visible_region.bottom(),
    )
    bottom_qwindow_event = QWheelEvent(
        QPointF(1, 1),
        QPointF(physical_bottom),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    assert (
        controller.eventFilter(window_handle, bottom_qwindow_event)
        is True
    )
    assert bottom_qwindow_event.isAccepted()
    assert process_calls == 1

    hover_region = controller._bottom_hover_region_global()
    controller.schedule_hide()
    assert controller._hide_timer.isActive()
    controller.hide_overlays()
    assert not controller.bottom_overlay.isVisible()
    assert controller._bottom_wheel_region_global().isEmpty()
    assert controller._bottom_hover_region_global() == hover_region

    for point in (hover_region.center(), physical_bottom):
        hidden_qwindow_event = QWheelEvent(
            QPointF(1, 1),
            QPointF(point),
            QPoint(),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        hidden_qwindow_event.ignore()
        assert (
            controller.eventFilter(window_handle, hidden_qwindow_event)
            is False
        )
        assert not hidden_qwindow_event.isAccepted()
    assert process_calls == 1

    hidden_widget_event = _wheel_event(
        controller.bottom_overlay,
        angle_y=-120,
    )
    hidden_widget_event.ignore()
    assert (
        controller.eventFilter(
            controller.bottom_overlay,
            hidden_widget_event,
        )
        is False
    )
    assert not hidden_widget_event.isAccepted()
    assert process_calls == 1

    controller.set_fullscreen_state(
        True,
        hide_ui=False,
        hide_cursor=False,
    )
    qapp.processEvents()
    assert controller.bottom_overlay.isVisible()
    disabled_auto_hide_region = controller._bottom_wheel_region_global()
    assert not disabled_auto_hide_region.isEmpty()
    assert not disabled_auto_hide_region.contains(viewer_center)
    disabled_auto_hide_bottom = QWheelEvent(
        QPointF(1, 1),
        QPointF(
            disabled_auto_hide_region.center().x(),
            disabled_auto_hide_region.bottom(),
        ),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    assert (
        controller.eventFilter(
            window_handle,
            disabled_auto_hide_bottom,
        )
        is True
    )
    assert disabled_auto_hide_bottom.isAccepted()
    assert process_calls == 2

    controller.set_active(False)
    controller.shutdown()
    window.close()


def test_bottom_overlay_wheel_accumulates_once_without_other_ui_capture(
    qapp,
) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = ViewerPageSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
    )
    calls: list[str] = []
    slider.nextDisplayUnitRequested.connect(lambda: calls.append("next"))
    window.resize(640, 480)
    window.show()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    controller.show_bottom()
    qapp.processEvents()

    for _index in range(4):
        event = _wheel(controller.bottom_overlay, pixel_y=-10)
        assert event.isAccepted()
    assert calls == ["next"]

    _wheel(viewer, angle_y=-120)
    _wheel(controller.top_overlay, angle_y=-120)
    assert calls == ["next"]

    controller.set_active(False)
    controller.shutdown()
    window.close()


@pytest.mark.parametrize("single_page", [False, True])
@pytest.mark.parametrize(
    ("delta_name", "partial_delta"),
    [("angle", -60), ("pixel", -20)],
)
def test_bottom_wheel_remainder_resets_when_pointer_leaves_region(
    qapp,
    monkeypatch,
    single_page: bool,
    delta_name: str,
    partial_delta: int,
) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = ViewerPageSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
    )
    monkeypatch.setattr(
        controller,
        "_apply_native_fullscreen_frame",
        lambda _fullscreen: None,
    )
    slider.set_single_page_wheel_enabled(single_page)
    single_calls: list[str] = []
    display_calls: list[str] = []
    slider.nextSinglePageRequested.connect(
        lambda: single_calls.append("next")
    )
    slider.nextDisplayUnitRequested.connect(
        lambda: display_calls.append("next")
    )
    window.resize(640, 480)
    window.show()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    controller.show_bottom()
    qapp.processEvents()

    wheel_kwargs = {f"{delta_name}_y": partial_delta}
    _wheel(controller.bottom_overlay, **wheel_kwargs)
    _wheel(controller.bottom_overlay, **wheel_kwargs)
    assert single_calls == (["next"] if single_page else [])
    assert display_calls == ([] if single_page else ["next"])

    _wheel(controller.bottom_overlay, **wheel_kwargs)
    if delta_name == "angle":
        assert slider._angle_remainder == partial_delta
        assert slider._pixel_remainder == 0
    else:
        assert slider._pixel_remainder == partial_delta
        assert slider._angle_remainder == 0

    region = controller._bottom_hover_region_global()
    outside_global = QPoint(region.center().x(), region.top() - 1)
    outside_local = viewer.mapFromGlobal(outside_global)
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(outside_local),
        QPointF(outside_global),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(viewer, move_event)
    assert slider._angle_remainder == 0
    assert slider._pixel_remainder == 0

    _wheel(viewer, **wheel_kwargs)
    assert single_calls == (["next"] if single_page else [])
    assert display_calls == ([] if single_page else ["next"])
    assert slider._angle_remainder == 0
    assert slider._pixel_remainder == 0

    _wheel(controller.bottom_overlay, **wheel_kwargs)
    assert single_calls == (["next"] if single_page else [])
    assert display_calls == ([] if single_page else ["next"])
    _wheel(controller.bottom_overlay, **wheel_kwargs)
    assert single_calls == (["next"] * 2 if single_page else [])
    assert display_calls == ([] if single_page else ["next"] * 2)

    controller.set_active(False)
    controller.shutdown()
    window.close()


@pytest.mark.parametrize(
    ("delta_name", "partial_delta"),
    [("angle", -60), ("pixel", -20)],
)
def test_bottom_wheel_remainder_tracks_input_region_across_receivers(
    qapp,
    monkeypatch,
    delta_name: str,
    partial_delta: int,
) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = ViewerPageSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
    )
    monkeypatch.setattr(
        controller,
        "_apply_native_fullscreen_frame",
        lambda _fullscreen: None,
    )
    window.resize(640, 480)
    window.show()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    controller.show_bottom()
    qapp.processEvents()
    window_handle = window.windowHandle()
    assert window_handle is not None
    region = controller._bottom_hover_region_global()
    calls: list[str] = []
    slider.nextDisplayUnitRequested.connect(lambda: calls.append("next"))
    process_calls = 0
    original_process = slider.process_wheel_delta

    def counted_process(angle_delta: QPoint, pixel_delta: QPoint) -> bool:
        nonlocal process_calls
        process_calls += 1
        return original_process(angle_delta, pixel_delta)

    monkeypatch.setattr(slider, "process_wheel_delta", counted_process)
    wheel_kwargs = {f"{delta_name}_y": partial_delta}
    _wheel(controller.bottom_overlay, **wheel_kwargs)

    angle_delta = (
        QPoint(0, partial_delta) if delta_name == "angle" else QPoint()
    )
    pixel_delta = (
        QPoint(0, partial_delta) if delta_name == "pixel" else QPoint()
    )
    inside_event = QWheelEvent(
        QPointF(1, 1),
        QPointF(region.center().x(), region.bottom()),
        pixel_delta,
        angle_delta,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    assert controller.eventFilter(window_handle, inside_event) is True
    assert inside_event.isAccepted()
    assert process_calls == 2
    assert calls == ["next"]

    _wheel(controller.bottom_overlay, **wheel_kwargs)
    outside_event = QWheelEvent(
        QPointF(1, 1),
        QPointF(region.center().x(), region.top() - 1),
        pixel_delta,
        angle_delta,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    assert controller.eventFilter(window_handle, outside_event) is False
    assert process_calls == 3
    assert calls == ["next"]
    assert slider._angle_remainder == 0
    assert slider._pixel_remainder == 0

    controller.set_active(False)
    controller.shutdown()
    window.close()


def test_hidden_bottom_overlay_does_not_claim_qwindow_then_viewer_wheel(
    tmp_path: Path,
    qapp,
    monkeypatch,
) -> None:
    next_page_calls = 0
    next_or_scroll_calls = 0
    original_next_page = ViewerWindow.next_page
    original_next_or_scroll = ViewerWindow.next_page_or_scroll

    def counted_next_page(self: ViewerWindow) -> None:
        nonlocal next_page_calls
        next_page_calls += 1
        original_next_page(self)

    def counted_next_or_scroll(self: ViewerWindow) -> None:
        nonlocal next_or_scroll_calls
        next_or_scroll_calls += 1
        original_next_or_scroll(self)

    monkeypatch.setattr(ViewerWindow, "next_page", counted_next_page)
    monkeypatch.setattr(
        ViewerWindow,
        "next_page_or_scroll",
        counted_next_or_scroll,
    )
    window, _pages = _viewer_with_pages(tmp_path, qapp, page_count=5)
    controller = window.fullscreen_chrome
    monkeypatch.setattr(
        controller,
        "_apply_native_fullscreen_frame",
        lambda _fullscreen: None,
    )
    window.slider.set_single_page_wheel_enabled(False)
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    controller.show_bottom()
    qapp.processEvents()
    lower_calls: list[str] = []
    lower_single_calls: list[str] = []
    viewer_calls: list[str] = []
    window.slider.nextDisplayUnitRequested.connect(
        lambda: lower_calls.append("next")
    )
    window.slider.nextSinglePageRequested.connect(
        lambda: lower_single_calls.append("next")
    )
    window.slider.previousSinglePageRequested.connect(
        lambda: lower_single_calls.append("previous")
    )
    window.viewer.nextRequested.connect(
        lambda: viewer_calls.append("next")
    )
    process_calls = 0
    display_move_calls = 0
    original_process = window.slider.process_wheel_delta
    original_display_move = window.page_navigation.next_display_unit

    def counted_process(angle_delta: QPoint, pixel_delta: QPoint) -> bool:
        nonlocal process_calls
        process_calls += 1
        return original_process(angle_delta, pixel_delta)

    def counted_display_move() -> bool:
        nonlocal display_move_calls
        display_move_calls += 1
        return original_display_move()

    monkeypatch.setattr(
        window.slider,
        "process_wheel_delta",
        counted_process,
    )
    monkeypatch.setattr(
        window.page_navigation,
        "next_display_unit",
        counted_display_move,
    )

    lower_event = _wheel(controller.bottom_overlay, angle_y=-120)
    assert lower_event.isAccepted()
    assert lower_calls == ["next"]
    assert lower_single_calls == []
    assert process_calls == 1
    assert next_page_calls == 1
    assert next_or_scroll_calls == 0
    assert display_move_calls == 1

    controller.schedule_hide()
    assert controller._hide_timer.isActive()
    controller.hide_overlays()
    assert not controller.bottom_overlay.isVisible()
    hidden_hover_region = controller._bottom_hover_region_global()
    viewer_global = window.viewer.mapToGlobal(
        window.viewer.rect().center()
    )
    assert not hidden_hover_region.contains(viewer_global)
    viewer_local = window.viewer.mapFromGlobal(viewer_global)
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(viewer_local),
        QPointF(viewer_global),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window.viewer, move_event)
    assert not controller.bottom_overlay.isVisible()

    window_handle = window.windowHandle()
    assert window_handle is not None
    qwindow_event = QWheelEvent(
        QPointF(1, 1),
        QPointF(hidden_hover_region.center()),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    qwindow_event.ignore()
    assert controller.eventFilter(window_handle, qwindow_event) is False
    assert not qwindow_event.isAccepted()

    viewer_event = QWheelEvent(
        QPointF(viewer_local),
        QPointF(viewer_global),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(window.viewer, viewer_event)
    assert viewer_event.isAccepted()
    assert process_calls == 1
    assert lower_calls == ["next"]
    assert lower_single_calls == []
    assert viewer_calls == ["next"]
    assert next_page_calls == 2
    assert next_or_scroll_calls == 1
    assert display_move_calls == 2
    qapp.processEvents()
    assert process_calls == 1
    assert lower_calls == ["next"]
    assert viewer_calls == ["next"]
    assert next_page_calls == 2
    assert next_or_scroll_calls == 1
    assert display_move_calls == 2

    controller.set_active(False)
    window.close()
    qapp.processEvents()


@pytest.mark.parametrize("size", [(640, 480), (641, 479)])
def test_fullscreen_overlays_are_flush_with_parent_edges(
    qapp,
    size: tuple[int, int],
    monkeypatch,
) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = ViewerPageSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
    )
    native_states: list[bool] = []
    monkeypatch.setattr(
        controller,
        "_apply_native_fullscreen_frame",
        native_states.append,
    )
    window.resize(*size)
    window.show()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    controller.show_bottom()
    qapp.processEvents()

    assert controller.top_overlay.frameWidth() == 0
    assert controller.bottom_overlay.frameWidth() == 0
    assert controller.top_overlay.autoFillBackground()
    assert controller.bottom_overlay.autoFillBackground()
    expected_background = window.palette().color(QPalette.ColorRole.Window)
    assert central.autoFillBackground()
    assert (
        central.palette().color(QPalette.ColorRole.Window)
        == expected_background
    )
    assert (
        controller.top_overlay.palette().color(QPalette.ColorRole.Window)
        == expected_background
    )
    assert (
        controller.bottom_overlay.palette().color(QPalette.ColorRole.Window)
        == expected_background
    )
    assert expected_background.alpha() > 0
    assert controller.top_overlay.geometry().top() == central.rect().top()
    assert controller.bottom_overlay.geometry().bottom() == central.rect().bottom()
    assert controller._top_layout.spacing() == 0
    assert controller._bottom_layout.spacing() == 0

    controller.hide_overlays()
    controller.show_bottom()
    window.resize(size[0] + 37, size[1] + 29)
    qapp.processEvents()

    assert controller.top_overlay.geometry().top() == central.rect().top()
    assert controller.bottom_overlay.geometry().bottom() == central.rect().bottom()
    assert controller.top_overlay.autoFillBackground()
    assert controller.bottom_overlay.autoFillBackground()
    assert True in native_states

    controller.set_active(False)
    assert native_states[-1] is False
    controller.shutdown()
    window.close()


def test_fullscreen_has_separate_top_and_bottom_trigger_widths(qapp) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = ViewerPageSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
        top_edge_trigger_px=8,
        bottom_edge_trigger_px=28,
        hide_delay_ms=0,
    )
    window.resize(640, 480)
    window.show()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    qapp.processEvents()

    assert controller.top_edge_trigger_px == 8
    assert controller.bottom_edge_trigger_px == 28
    assert controller.bottom_reveal_strip.width() == central.width()
    assert controller.bottom_reveal_strip.height() == 28
    assert controller.bottom_reveal_strip.isVisible()

    for x in (0, central.width() // 2, central.width() - 1):
        controller.process_pointer(
            central.mapToGlobal(QPoint(x, central.height() - 1))
        )
        assert controller.bottom_overlay.isVisible()
    controller.set_active(False)
    window.close()


def test_bottom_hover_region_extends_from_overlay_top_to_window_bottom(
    qapp,
) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = ViewerPageSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
        hide_delay_ms=0,
    )
    window.resize(640, 480)
    window.show()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    controller.show_bottom()
    qapp.processEvents()

    frame = window.frameGeometry()
    overlay_top = controller.bottom_overlay.mapToGlobal(QPoint(0, 0)).y()
    x = frame.center().x()
    points = (
        QPoint(x, overlay_top),
        QPoint(x, overlay_top + 1),
        QPoint(x, frame.bottom() - 1),
        QPoint(x, frame.bottom()),
    )
    for point in points:
        assert controller._pointer_in_bottom_hover_region(point)
        controller._update_pointer_state(point)
        assert controller.pointer_in_bottom_overlay

    timer = controller._hide_timer
    controller.process_pointer(points[-1])
    controller.process_pointer(points[-2])
    assert controller._hide_timer is timer
    assert controller.bottom_overlay.isVisible()
    controller.set_active(False)
    window.close()


def test_hidden_bottom_overlay_uses_same_region_for_reveal_and_hover(
    qapp,
) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = ViewerPageSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
        hide_delay_ms=0,
    )
    window.resize(640, 480)
    window.show()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    controller.show_bottom()
    qapp.processEvents()

    visible_region = controller._bottom_hover_region_global()
    assert visible_region.bottom() == window.frameGeometry().bottom()
    controller.hide_overlays()
    assert not controller.bottom_overlay.isVisible()
    hidden_region = controller._bottom_hover_region_global()
    assert hidden_region == visible_region

    outside = QPoint(hidden_region.center().x(), hidden_region.top() - 1)
    controller.process_pointer(outside)
    assert not controller.bottom_overlay.isVisible()

    expected_overlay_area = QPoint(
        hidden_region.center().x(),
        hidden_region.top() + 1,
    )
    controller.process_pointer(expected_overlay_area)
    assert controller.bottom_overlay.isVisible()
    assert controller._pointer_in_reveal_area(expected_overlay_area)

    controller.hide_overlays()
    frame_bottom = QPoint(
        hidden_region.center().x(),
        window.frameGeometry().bottom(),
    )
    controller.process_pointer(frame_bottom)
    assert controller.bottom_overlay.isVisible()
    assert controller._pointer_in_reveal_area(frame_bottom)

    timer = controller._hide_timer
    controller.process_pointer(frame_bottom)
    controller.process_pointer(frame_bottom)
    assert controller._hide_timer is timer
    assert controller.bottom_overlay.isVisible()
    controller.set_active(False)
    window.close()


def test_fullscreen_screen_bottom_reveals_through_existing_mouse_event_path(
    qapp,
    monkeypatch,
) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = ViewerPageSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
        hide_delay_ms=20,
    )
    window.show()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    qapp.processEvents()

    screen_geometry = QRect(window.screen().geometry())
    shortened_frame = screen_geometry.adjusted(0, 0, 0, -1)
    monkeypatch.setattr(window, "isFullScreen", lambda: True)
    monkeypatch.setattr(
        window,
        "frameGeometry",
        lambda: QRect(shortened_frame),
    )
    controller.hide_overlays()

    region = controller._bottom_hover_region_global()
    assert region.bottom() == screen_geometry.bottom() + 1
    assert region.contains(
        QPoint(region.center().x(), shortened_frame.bottom())
    )
    assert region.contains(
        QPoint(region.center().x(), screen_geometry.bottom())
    )
    assert not region.contains(
        QPoint(region.center().x(), region.bottom() + 1)
    )

    show_calls = 0
    original_show = controller.bottom_overlay.show

    def counted_show() -> None:
        nonlocal show_calls
        show_calls += 1
        original_show()

    monkeypatch.setattr(controller.bottom_overlay, "show", counted_show)
    timer = controller._hide_timer
    for x in (
        screen_geometry.left(),
        screen_geometry.center().x(),
        screen_geometry.right() + 1,
    ):
        controller.hide_overlays()
        point = QPoint(x, screen_geometry.bottom())
        local = window.mapFromGlobal(point)
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(local),
            QPointF(point),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(window, event)
        assert controller.bottom_overlay.isVisible()
        assert controller._pointer_in_reveal_area(point)
        assert controller._hide_timer is timer

    assert show_calls == 3
    controller.set_active(False)
    controller.shutdown()
    window.close()


def test_fullscreen_bottom_hover_keeps_existing_hide_timer_and_policies(
    qapp,
    monkeypatch,
) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = ViewerPageSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=window.menuBar(),
        slider=slider,
        status_bar=status,
        hide_delay_ms=20,
    )
    window.show()
    controller.set_fullscreen_state(True, hide_ui=True, hide_cursor=False)
    qapp.processEvents()

    region = controller._bottom_hover_region_global()
    edge = QPoint(region.center().x(), region.bottom())
    controller.hide_overlays()
    timer = controller._hide_timer
    controller.process_pointer(edge)
    controller.process_pointer(edge)
    assert controller.bottom_overlay.isVisible()
    assert controller._hide_timer is timer
    assert not timer.isActive()

    outside = QPoint(region.center().x(), region.top() - 1)
    controller.process_pointer(outside)
    assert timer.isActive()
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: QPoint(outside)))
    QTest.qWait(30)
    qapp.processEvents()
    assert not controller.bottom_overlay.isVisible()

    controller.set_fullscreen_state(False, hide_ui=True, hide_cursor=False)
    controller.process_pointer(edge)
    assert not controller.bottom_overlay.isVisible()

    controller.set_fullscreen_state(True, hide_ui=False, hide_cursor=False)
    assert controller.top_overlay.isVisible()
    assert controller.bottom_overlay.isVisible()
    controller.process_pointer(edge)
    assert not timer.isActive()
    controller.set_active(False)
    controller.shutdown()
    window.close()


def test_bottom_trigger_clamps_and_legacy_edge_migrates(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"fullscreen_edge_trigger_px": 20}', encoding="utf-8")

    config = ConfigManager(path)
    settings = config.load()

    assert settings["fullscreen_top_edge_trigger_px"] == 20
    assert settings["fullscreen_bottom_edge_trigger_px"] == 20
    changed = config.apply(
        {
            "fullscreen_top_edge_trigger_px": 100,
            "fullscreen_bottom_edge_trigger_px": 2,
        }
    )
    assert changed["fullscreen_top_edge_trigger_px"] == 32
    assert changed["fullscreen_bottom_edge_trigger_px"] == 12


def test_canvas_click_moves_immediately_on_release_without_pending_timer(
    tmp_path: Path,
    qapp,
) -> None:
    old_interval = QApplication.doubleClickInterval()
    QApplication.setDoubleClickInterval(30)
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    window.next_page()
    assert _indexes(window.model) == [1, 2]

    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(window.viewer.width() - 5, window.viewer.height() // 2),
    )
    assert _indexes(window.model) == [2, 3]
    assert not window.viewer.canvas_pointer.pending
    window.close()
    qapp.processEvents()
    QApplication.setDoubleClickInterval(old_interval)


def test_canvas_double_click_toggles_fullscreen_without_page_move(
    tmp_path: Path,
    qapp,
) -> None:
    old_interval = QApplication.doubleClickInterval()
    QApplication.setDoubleClickInterval(80)
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    before = window.model.focused_index

    QTest.mouseDClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=window.viewer.rect().center(),
    )
    QTest.qWait(100)

    assert window.isFullScreen()
    assert window.model.focused_index == before
    window.close()
    qapp.processEvents()
    QApplication.setDoubleClickInterval(old_interval)


def test_canvas_two_fast_side_clicks_move_twice_without_wait(
    tmp_path: Path,
    qapp,
) -> None:
    old_interval = QApplication.doubleClickInterval()
    QApplication.setDoubleClickInterval(500)
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    window.next_page()
    position = QPoint(
        window.viewer.width() - 5,
        window.viewer.height() // 2,
    )

    QTest.mouseClick(window.viewer, Qt.MouseButton.LeftButton, pos=position)
    assert window.model.focused_index == 2
    QTest.mouseClick(window.viewer, Qt.MouseButton.LeftButton, pos=position)

    assert window.model.focused_index == 3
    assert not window.viewer.canvas_pointer.pending
    assert not window.isFullScreen()
    window.close()
    qapp.processEvents()
    QApplication.setDoubleClickInterval(old_interval)


def test_canvas_side_double_click_moves_twice_without_fullscreen(
    tmp_path: Path,
    qapp,
) -> None:
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    window.next_page()
    position = QPoint(
        window.viewer.width() - 5,
        window.viewer.height() // 2,
    )

    # QTest.mouseDClick sends the second Qt double-click event. Send the
    # preceding ordinary click to reproduce the native press/release sequence.
    QTest.mouseClick(window.viewer, Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=position,
    )

    assert window.model.focused_index == 3
    assert not window.viewer.canvas_pointer.pending
    assert not window.isFullScreen()
    window.close()
    qapp.processEvents()


def test_canvas_gutter_double_click_toggles_fullscreen_without_page_move(
    tmp_path: Path,
    qapp,
) -> None:
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    window.next_page()
    before = window.model.focused_index
    layout = window.viewer._layout_for_current_images()
    left_rect, right_rect = sorted(layout.rects, key=lambda rect: rect.x())
    gutter_x = left_rect.right() + 1
    assert gutter_x < right_rect.left()

    QTest.mouseDClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(gutter_x, window.viewer.height() // 2),
    )

    assert window.isFullScreen()
    assert window.model.focused_index == before
    window.close()
    qapp.processEvents()


def test_canvas_disabled_side_double_click_keeps_fullscreen_contract(
    tmp_path: Path,
    qapp,
) -> None:
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    window.config.apply({"viewer_canvas_left_click_action": "none"})
    before = window.model.focused_index

    QTest.mouseDClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(window.viewer.width() - 5, window.viewer.height() // 2),
    )

    assert window.isFullScreen()
    assert window.model.focused_index == before
    window.close()
    qapp.processEvents()


def test_canvas_drag_and_modifier_click_do_not_navigate(
    tmp_path: Path,
    qapp,
) -> None:
    old_interval = QApplication.doubleClickInterval()
    QApplication.setDoubleClickInterval(25)
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    start = QPoint(window.viewer.width() - 20, window.viewer.height() // 2)

    QTest.mousePress(window.viewer, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window.viewer, start + QPoint(40, 0), delay=1)
    QTest.mouseRelease(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=start + QPoint(40, 0),
    )
    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
        pos=start,
    )
    QTest.qWait(40)

    assert window.model.focused_index == 0
    window.close()
    qapp.processEvents()
    QApplication.setDoubleClickInterval(old_interval)


def test_pending_canvas_click_is_cancelled_by_page_change(
    tmp_path: Path,
    qapp,
) -> None:
    old_interval = QApplication.doubleClickInterval()
    QApplication.setDoubleClickInterval(40)
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    window.config.apply({"viewer_canvas_left_click_action": "none"})

    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(window.viewer.width() - 5, window.viewer.height() // 2),
    )
    assert window.viewer.canvas_pointer.pending
    window.next_page()
    QTest.qWait(55)

    assert window.model.focused_index == 1
    window.close()
    qapp.processEvents()
    QApplication.setDoubleClickInterval(old_interval)


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("next_single_page", [2, 3]),
        ("next_display_unit", [3, 4]),
        ("none", [1, 2]),
    ],
)
def test_canvas_click_setting_is_applied_immediately(
    tmp_path: Path,
    qapp,
    action: str,
    expected: list[int],
) -> None:
    old_interval = QApplication.doubleClickInterval()
    QApplication.setDoubleClickInterval(20)
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    window.next_page()
    window.config.apply({"viewer_canvas_left_click_action": action})

    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(window.viewer.width() - 5, window.viewer.height() // 2),
    )
    QTest.qWait(35)

    assert _indexes(window.model) == expected
    window.close()
    qapp.processEvents()
    QApplication.setDoubleClickInterval(old_interval)


def test_canvas_physical_sides_move_independently_of_reading_direction(
    tmp_path: Path,
    qapp,
) -> None:
    old_interval = QApplication.doubleClickInterval()
    QApplication.setDoubleClickInterval(20)
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    assert window.reading_direction == "rtl"
    window.next_page()
    assert window.model.focused_index == 1

    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(window.viewer.width() - 5, window.viewer.height() // 2),
    )
    QTest.qWait(35)
    assert window.model.focused_index == 2

    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(5, window.viewer.height() // 2),
    )
    QTest.qWait(35)
    assert window.model.focused_index == 1
    window.close()
    qapp.processEvents()
    QApplication.setDoubleClickInterval(old_interval)


def test_canvas_click_direction_reverses_immediately(
    tmp_path: Path,
    qapp,
) -> None:
    old_interval = QApplication.doubleClickInterval()
    QApplication.setDoubleClickInterval(20)
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    window.next_page()
    window.config.apply({"viewer_canvas_click_direction": "left_next"})

    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(5, window.viewer.height() // 2),
    )
    QTest.qWait(35)
    assert window.model.focused_index == 2

    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(window.viewer.width() - 5, window.viewer.height() // 2),
    )
    QTest.qWait(35)
    assert window.model.focused_index == 1
    window.close()
    qapp.processEvents()
    QApplication.setDoubleClickInterval(old_interval)


def test_canvas_background_is_active_but_center_and_positive_gutter_are_not(
    tmp_path: Path,
    qapp,
) -> None:
    old_interval = QApplication.doubleClickInterval()
    QApplication.setDoubleClickInterval(20)
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    window.next_page()
    assert window.model.focused_index == 1

    center = QPoint(
        window.viewer.rect().center().x(),
        window.viewer.height() // 2,
    )
    QTest.mouseClick(window.viewer, Qt.MouseButton.LeftButton, pos=center)
    QTest.qWait(35)
    assert window.model.focused_index == 1

    layout = window.viewer._layout_for_current_images()
    left_rect, right_rect = sorted(layout.rects, key=lambda rect: rect.x())
    assert right_rect.left() - left_rect.right() - 1 > 0
    gutter_x = left_rect.right() + 1
    if gutter_x == center.x():
        gutter_x = right_rect.left() - 1
    assert gutter_x != center.x()
    gutter = QPoint(
        gutter_x,
        window.viewer.height() // 2,
    )
    QTest.mouseClick(window.viewer, Qt.MouseButton.LeftButton, pos=gutter)
    QTest.qWait(35)
    assert window.model.focused_index == 1

    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(1, window.viewer.height() // 2),
    )
    QTest.qWait(35)
    assert window.model.focused_index == 0
    window.close()
    qapp.processEvents()
    QApplication.setDoubleClickInterval(old_interval)


def test_joined_spread_adds_no_fixed_center_dead_zone(
    tmp_path: Path,
    qapp,
) -> None:
    old_interval = QApplication.doubleClickInterval()
    QApplication.setDoubleClickInterval(20)
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    window.next_page()
    window.viewer.set_join_spread_pages(True)
    center_x = window.viewer.rect().center().x()
    assert window.viewer._layout_for_current_images().effective_gap == 0

    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(center_x + 1, window.viewer.height() // 2),
    )
    QTest.qWait(35)
    assert window.model.focused_index == 2
    window.close()
    qapp.processEvents()
    QApplication.setDoubleClickInterval(old_interval)


def test_canvas_display_unit_setting_uses_existing_previous_navigation(
    tmp_path: Path,
    qapp,
) -> None:
    old_interval = QApplication.doubleClickInterval()
    QApplication.setDoubleClickInterval(20)
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    window.next_page()
    assert _indexes(window.model) == [1, 2]
    window.config.apply(
        {"viewer_canvas_left_click_action": "next_display_unit"}
    )

    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=QPoint(5, window.viewer.height() // 2),
    )
    QTest.qWait(35)

    assert _indexes(window.model) == [0]
    window.close()
    qapp.processEvents()
    QApplication.setDoubleClickInterval(old_interval)


def test_slider_wheel_setting_is_applied_immediately(
    tmp_path: Path,
    qapp,
) -> None:
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    window.next_page()
    assert window.model.focused_index == 1

    _wheel(window.slider, angle_y=-120)
    qapp.processEvents()
    assert window.model.focused_index == 3

    window.page_navigation.go_to_focused_page_index(1)
    window.config.apply({"viewer_slider_wheel_single_page_enabled": True})
    _wheel(window.slider, angle_y=-120)
    qapp.processEvents()
    assert window.model.focused_index == 2
    window.close()
    qapp.processEvents()


def test_viewer_click_setting_round_trip_and_cancel(tmp_path: Path, qapp) -> None:
    config = _config(tmp_path)
    dialog = SettingsDialog(config)
    dialog.viewer_canvas_left_click_combo.setCurrentIndex(
        dialog.viewer_canvas_left_click_combo.findData("none")
    )
    dialog.reject()
    assert config.get("viewer_canvas_left_click_action") == "next_single_page"

    dialog = SettingsDialog(config)
    dialog.viewer_canvas_left_click_combo.setCurrentIndex(
        dialog.viewer_canvas_left_click_combo.findData("next_display_unit")
    )
    dialog.viewer_canvas_click_direction_combo.setCurrentIndex(
        dialog.viewer_canvas_click_direction_combo.findData("left_next")
    )
    dialog.viewer_slider_wheel_single_page_checkbox.setChecked(True)
    changed = dialog.apply_settings()
    assert changed["viewer_canvas_left_click_action"] == "next_display_unit"
    assert changed["viewer_canvas_click_direction"] == "left_next"
    assert changed["viewer_slider_wheel_single_page_enabled"] is True
    dialog.reject()
    qapp.processEvents()

    restored = ConfigManager(config.path)
    restored.load()
    assert restored.get("viewer_canvas_left_click_action") == "next_display_unit"
    assert restored.get("viewer_canvas_click_direction") == "left_next"
    assert restored.get("viewer_slider_wheel_single_page_enabled") is True
