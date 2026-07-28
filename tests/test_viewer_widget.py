from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, QSize, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

from app.page_model import DisplaySpread, PageSlot
from app.viewer_widget import ViewerWidget, calculate_spread_layout


def center_gap(layout) -> int:
    return layout.rects[1].left() - layout.rects[0].right() - 1


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


@pytest.mark.parametrize("fit_mode", ["fit_window", "actual_size", "manual_zoom"])
def test_joined_spread_has_no_center_gap_and_shared_scale(fit_mode: str) -> None:
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
