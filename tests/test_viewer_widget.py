from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication

from app.page_model import DisplaySpread, PageSlot
from app.viewer_widget import ViewerWidget, calculate_spread_layout


def center_gap(layout) -> int:
    return layout.rects[1].left() - layout.rects[0].right() - 1


def send_wheel_event(
    widget: ViewerWidget,
    *,
    angle_x: int = 0,
    angle_y: int = 0,
    pixel_x: int = 0,
    pixel_y: int = 0,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> QWheelEvent:
    event = QWheelEvent(
        QPointF(10, 10),
        QPointF(widget.mapToGlobal(QPoint(10, 10))),
        QPoint(pixel_x, pixel_y),
        QPoint(angle_x, angle_y),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    event.ignore()
    widget.wheelEvent(event)
    return event


@pytest.mark.parametrize(
    ("angle_y", "expected_zoom"),
    [(120, 1.15), (-120, 1 / 1.15)],
)
def test_viewer_ctrl_vertical_angle_zooms_and_accepts(
    qapp: QApplication,
    angle_y: int,
    expected_zoom: float,
) -> None:
    widget = ViewerWidget()
    zooms: list[float] = []
    widget.zoomChanged.connect(zooms.append)

    event = send_wheel_event(
        widget,
        angle_y=angle_y,
        modifiers=Qt.KeyboardModifier.ControlModifier,
    )

    assert event.isAccepted()
    assert widget.manual_zoom == pytest.approx(expected_zoom)
    assert zooms == [pytest.approx(expected_zoom)]
    widget.close()


@pytest.mark.parametrize(
    ("modifiers", "wheel_delta"),
    [
        (Qt.KeyboardModifier.ControlModifier, {}),
        (Qt.KeyboardModifier.ControlModifier, {"angle_x": 120}),
        (Qt.KeyboardModifier.ControlModifier, {"pixel_y": 40}),
        (Qt.KeyboardModifier.NoModifier, {}),
        (Qt.KeyboardModifier.NoModifier, {"angle_x": 120}),
        (Qt.KeyboardModifier.NoModifier, {"pixel_y": 40}),
        (Qt.KeyboardModifier.NoModifier, {"pixel_x": 40}),
    ],
)
def test_viewer_wheel_without_vertical_angle_is_ignored_without_action(
    qapp: QApplication,
    modifiers: Qt.KeyboardModifier,
    wheel_delta: dict[str, int],
) -> None:
    widget = ViewerWidget()
    actions: list[str] = []
    widget.zoomChanged.connect(lambda _zoom: actions.append("zoom"))
    widget.nextRequested.connect(lambda: actions.append("next"))
    widget.previousRequested.connect(lambda: actions.append("previous"))
    initial_pan = QPoint(widget._pan)

    event = send_wheel_event(
        widget,
        modifiers=modifiers,
        **wheel_delta,
    )

    assert not event.isAccepted()
    assert actions == []
    assert widget.fit_mode == "fit_window"
    assert widget.manual_zoom == 1.0
    assert widget._pan == initial_pan
    assert not hasattr(widget, "_angle_remainder")
    assert not hasattr(widget, "_pixel_remainder")
    widget.close()


@pytest.mark.parametrize(
    ("angle_y", "expected_action"),
    [(-120, "next"), (120, "previous")],
)
def test_viewer_vertical_angle_navigates_once_and_accepts(
    qapp: QApplication,
    angle_y: int,
    expected_action: str,
) -> None:
    widget = ViewerWidget()
    actions: list[str] = []
    widget.nextRequested.connect(lambda: actions.append("next"))
    widget.previousRequested.connect(lambda: actions.append("previous"))

    event = send_wheel_event(widget, angle_y=angle_y)

    assert event.isAccepted()
    assert actions == [expected_action]
    widget.close()


def test_clear_releases_images_from_last_draw_layout_and_can_repaint(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(320, 240)
    first_image = QImage(80, 120, QImage.Format.Format_ARGB32)
    first_image.fill(Qt.GlobalColor.red)
    second_image = QImage(90, 130, QImage.Format.Format_ARGB32)
    second_image.fill(Qt.GlobalColor.green)
    first_pages = [
        ViewerWidget.from_qimage(0, "first", first_image, (80, 120)),
        ViewerWidget.from_qimage(1, "second", second_image, (90, 130)),
    ]
    widget.set_rotation_angle(90)
    widget.set_pages(
        DisplaySpread(
            0,
            (PageSlot("first", 0), PageSlot("second", 1)),
            False,
        ),
        first_pages,
    )
    widget.show()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))

    old_pixmap_keys = {
        page.pixmap.cacheKey()
        for page in first_pages
        if page.pixmap is not None
    }
    assert widget._last_draw_layout
    assert all(
        pixmap.cacheKey() not in old_pixmap_keys
        for _rect, pixmap in widget._last_draw_layout
    )

    widget.clear()

    assert widget._images == []
    assert widget._spread.slots == ()
    assert widget._last_draw_layout == []
    assert widget.rotation_angle == 90
    assert not any(
        pixmap.cacheKey() in old_pixmap_keys
        for _rect, pixmap in widget._last_draw_layout
    )
    assert isinstance(widget.sizeHint(), QSize)
    assert isinstance(widget.minimumSizeHint(), QSize)
    widget.render(QPixmap(widget.size()))

    replacement_image = QImage(64, 96, QImage.Format.Format_ARGB32)
    replacement_image.fill(Qt.GlobalColor.blue)
    widget.set_rotation_angle(0)
    replacement = ViewerWidget.from_qimage(
        2,
        "replacement",
        replacement_image,
        (64, 96),
    )
    widget.set_pages(
        DisplaySpread(2, (PageSlot("replacement", 2),), True),
        [replacement],
    )
    widget.render(QPixmap(widget.size()))

    assert len(widget._last_draw_layout) == 1
    assert widget._last_draw_layout[0][1].cacheKey() == replacement.pixmap.cacheKey()
    widget.clear()
    widget.clear()
    assert widget._last_draw_layout == []
    widget.close()


def test_normal_spread_uses_configured_gap() -> None:
    layout = calculate_spread_layout(
        [(800, 1200), (800, 1200)],
        (1200, 800),
        gap=24,
    )

    assert layout.effective_gap == 24
    assert center_gap(layout) == 24


@pytest.mark.parametrize("fit_mode", ["actual_size", "manual_zoom"])
def test_explicit_zoom_modes_keep_shared_scale(fit_mode: str) -> None:
    layout = calculate_spread_layout(
        [(800, 1200), (600, 900)],
        (1400, 900),
        fit_mode=fit_mode,
        manual_zoom=1.35,
        gap=42,
        join_spread_pages=True,
    )

    assert layout.effective_gap == 0
    assert center_gap(layout) == 0
    first_scale = layout.rects[0].width() / 800
    second_scale = layout.rects[1].width() / 600
    assert first_scale == pytest.approx(second_scale, abs=0.002)


def test_fitted_spread_has_no_center_gap_and_independent_scales() -> None:
    layout = calculate_spread_layout(
        [(800, 1200), (600, 900)],
        (1400, 900),
        gap=42,
        join_spread_pages=True,
    )

    assert layout.effective_gap == 0
    assert center_gap(layout) == 0
    assert layout.scales[0] == pytest.approx(0.75)
    assert layout.scales[1] == pytest.approx(1.0)
    assert layout.rects[0].height() == layout.rects[1].height() == 900


def test_joined_spread_centers_different_heights_and_preserves_input_order() -> None:
    rtl_order = [(500, 1000), (700, 700)]
    layout = calculate_spread_layout(
        rtl_order,
        (1200, 900),
        join_spread_pages=True,
    )

    assert center_gap(layout) == 0
    assert layout.rects[0].width() < layout.rects[1].width()
    assert layout.rects[1].top() > layout.rects[0].top()
    assert layout.rects[0].center().y() == pytest.approx(
        layout.rects[1].center().y(),
        abs=1,
    )


def test_join_setting_does_not_change_single_or_split_single_page() -> None:
    single = calculate_spread_layout(
        [(800, 1200)],
        (1000, 800),
        gap=35,
        join_spread_pages=True,
        spread_is_single=True,
    )
    split_single = calculate_spread_layout(
        [(600, 900), (600, 900)],
        (1200, 900),
        gap=35,
        join_spread_pages=True,
        spread_is_single=True,
    )

    assert single.effective_gap == 0
    assert split_single.effective_gap == 35
    assert center_gap(split_single) == 35


@pytest.mark.parametrize(
    "sizes",
    [
        ((3600, 6500), (840, 1200)),
        ((840, 1200), (3600, 6500)),
        ((1600, 2400), (1600, 2400)),
        ((3600, 5400), (800, 1200)),
        ((2400, 3600), (1200, 1200)),
        ((3600, 1800), (1200, 2400)),
    ],
)
def test_spread_fit_scales_each_page_into_its_own_slot(
    sizes: tuple[tuple[int, int], tuple[int, int]],
) -> None:
    viewport = (1400, 900)
    gap = 24
    layout = calculate_spread_layout(sizes, viewport, gap=gap)
    slot_width = (viewport[0] - gap) // 2

    assert len(layout.scales) == 2
    for index, ((width, height), scale, rect) in enumerate(
        zip(sizes, layout.scales, layout.rects)
    ):
        expected_scale = min(slot_width / width, viewport[1] / height)
        assert scale == pytest.approx(expected_scale)
        assert rect.width() == max(1, round(width * expected_scale))
        assert rect.height() == max(1, round(height * expected_scale))
        assert 0 <= rect.top()
        assert rect.bottom() < viewport[1]
        if index == 0:
            assert rect.left() >= 0
            assert rect.right() < slot_width
        else:
            assert rect.left() >= slot_width + gap
            assert rect.right() < viewport[0]
    assert center_gap(layout) == gap


def test_large_resolution_difference_does_not_shrink_small_page() -> None:
    layout = calculate_spread_layout(
        [(3600, 6500), (840, 1200)],
        (1400, 900),
        gap=24,
    )

    assert layout.scales[0] != layout.scales[1]
    assert layout.rects[0].height() == 900
    assert layout.rects[1].height() == 900
    assert layout.rects[1].height() > 800


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_rotated_spread_dimensions_fit_independently(angle: int) -> None:
    original = ((3600, 6500), (840, 1200))
    rotated = (
        tuple(reversed(original[0])) if angle in {90, 270} else original[0],
        tuple(reversed(original[1])) if angle in {90, 270} else original[1],
    )

    layout = calculate_spread_layout(rotated, (1400, 900), gap=24)

    assert all(rect.left() >= 0 for rect in layout.rects)
    assert all(rect.right() < 1400 for rect in layout.rects)
    assert all(rect.top() >= 0 for rect in layout.rects)
    assert all(rect.bottom() < 900 for rect in layout.rects)


def send_mouse_event(
    widget: ViewerWidget,
    event_type: QEvent.Type,
    position: tuple[int, int],
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> None:
    point = QPointF(*position)
    event = QMouseEvent(
        event_type,
        point,
        point,
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def test_normal_right_click_requests_context_menu(qapp: QApplication) -> None:
    widget = ViewerWidget()
    positions = []
    gestures = []
    widget.contextMenuRequested.connect(positions.append)
    widget.gestureRecognized.connect(gestures.append)

    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonPress,
        (20, 20),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
    )
    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonRelease,
        (25, 22),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.NoButton,
    )

    assert len(positions) == 1
    assert gestures == []
    widget.close()


def test_right_drag_emits_gesture_and_suppresses_context_menu(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    positions = []
    gestures = []
    widget.contextMenuRequested.connect(positions.append)
    widget.gestureRecognized.connect(gestures.append)

    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonPress,
        (50, 20),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
    )
    send_mouse_event(
        widget,
        QEvent.Type.MouseMove,
        (50, 80),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.RightButton,
    )
    assert widget.gesture_in_progress
    assert len(widget.gesture_trail) == 2
    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonRelease,
        (50, 85),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.NoButton,
    )

    assert gestures == ["D"]
    assert positions == []
    assert widget.gesture_trail == ()
    assert not widget.gesture_in_progress
    widget.close()


def test_escape_cancel_clears_trail_and_suppresses_release(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    positions = []
    gestures = []
    widget.contextMenuRequested.connect(positions.append)
    widget.gestureRecognized.connect(gestures.append)
    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonPress,
        (50, 80),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
    )
    send_mouse_event(
        widget,
        QEvent.Type.MouseMove,
        (50, 20),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.RightButton,
    )

    assert widget.cancel_mouse_gesture()
    assert widget.gesture_trail == ()
    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonRelease,
        (50, 20),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.NoButton,
    )

    assert positions == []
    assert gestures == []
    widget.close()


def test_disabled_gesture_keeps_context_menu(qapp: QApplication) -> None:
    widget = ViewerWidget()
    widget.set_mouse_gesture_options(enabled=False)
    positions = []
    gestures = []
    widget.contextMenuRequested.connect(positions.append)
    widget.gestureRecognized.connect(gestures.append)
    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonPress,
        (10, 10),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
    )
    send_mouse_event(
        widget,
        QEvent.Type.MouseMove,
        (10, 100),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.RightButton,
    )
    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonRelease,
        (10, 100),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.NoButton,
    )

    assert len(positions) == 1
    assert gestures == []
    widget.close()


@pytest.mark.parametrize(
    ("button", "name"),
    [
        (Qt.MouseButton.BackButton, "back"),
        (Qt.MouseButton.ForwardButton, "forward"),
    ],
)
def test_extra_button_fires_once_on_press(
    qapp: QApplication,
    button: Qt.MouseButton,
    name: str,
) -> None:
    widget = ViewerWidget()
    pressed = []
    widget.extraMouseButtonPressed.connect(pressed.append)

    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonPress,
        (20, 20),
        button,
        button,
    )
    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonRelease,
        (20, 20),
        button,
        Qt.MouseButton.NoButton,
    )

    assert pressed == [name]
    widget.close()
