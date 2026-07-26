from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QStatusBar, QVBoxLayout, QWidget

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


def _wheel(
    target: QWidget,
    *,
    angle_y: int = 0,
    pixel_y: int = 0,
    angle_x: int = 0,
) -> QWheelEvent:
    event = QWheelEvent(
        QPointF(10, 10),
        QPointF(target.mapToGlobal(QPoint(10, 10))),
        QPoint(0, pixel_y),
        QPoint(angle_x, angle_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
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


def test_slider_angle_wheel_moves_one_page_and_is_accepted(qapp) -> None:
    slider = ViewerPageSlider()
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
    calls: list[str] = []
    slider.nextSinglePageRequested.connect(lambda: calls.append("next"))

    for _index in range(3):
        _wheel(slider, pixel_y=-10)
    assert calls == []
    _wheel(slider, pixel_y=-10)
    assert calls == ["next"]


def test_slider_consumes_horizontal_wheel_without_navigation(qapp) -> None:
    slider = ViewerPageSlider()
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
) -> None:
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    slider = window.slider

    window.showFullScreen()
    window._apply_chrome_visibility()
    qapp.processEvents()
    assert window.fullscreen_chrome.slider is slider
    assert slider.parent() is window.fullscreen_chrome.bottom_overlay

    window.showNormal()
    window._apply_chrome_visibility()
    qapp.processEvents()
    assert window.slider is slider
    window.close()
    qapp.processEvents()


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


def test_canvas_click_is_delayed_and_moves_one_logical_page(
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
        pos=window.viewer.rect().center(),
    )
    assert _indexes(window.model) == [1, 2]
    assert window.viewer.canvas_pointer.pending
    QTest.qWait(QApplication.doubleClickInterval() + 15)
    assert _indexes(window.model) == [2, 3]
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


def test_canvas_drag_and_modifier_click_do_not_navigate(
    tmp_path: Path,
    qapp,
) -> None:
    old_interval = QApplication.doubleClickInterval()
    QApplication.setDoubleClickInterval(25)
    window, _pages = _viewer_with_pages(tmp_path, qapp)
    start = window.viewer.rect().center()

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

    QTest.mouseClick(
        window.viewer,
        Qt.MouseButton.LeftButton,
        pos=window.viewer.rect().center(),
    )
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
        pos=window.viewer.rect().center(),
    )
    QTest.qWait(35)

    assert _indexes(window.model) == expected
    window.close()
    qapp.processEvents()
    QApplication.setDoubleClickInterval(old_interval)


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
    changed = dialog.apply_settings()
    assert changed["viewer_canvas_left_click_action"] == "next_display_unit"
    dialog.reject()
    qapp.processEvents()

    restored = ConfigManager(config.path)
    restored.load()
    assert restored.get("viewer_canvas_left_click_action") == "next_display_unit"
