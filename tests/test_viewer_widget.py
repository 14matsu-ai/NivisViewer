from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication

from app.page_model import DisplaySpread, PageSlot
from app.viewer_presentation_state import (
    PresentationSurface,
    PresentationSurfaceMode,
)
from app.viewer_widget import ViewerImage, ViewerWidget, calculate_spread_layout


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


def test_delayed_surface_projection_cannot_clear_a_committed_frame(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(160, 120)
    widget.set_direct_display_mode(True)
    loading = PresentationSurface(PresentationSurfaceMode.LOADING, 1)
    assert widget.apply_presentation_surface(loading)

    pixmap = QPixmap(32, 48)
    pixmap.fill(Qt.GlobalColor.blue)
    spread = DisplaySpread(0, (PageSlot("0.jpg", 0),), True)
    widget.commit_display_ready_single(
        spread,
        0,
        "0.jpg",
        (32, 48),
        pixmap,
        object(),
    )
    displayed = PresentationSurface(PresentationSurfaceMode.DISPLAYED, 2)
    assert widget.apply_presentation_surface(displayed)

    assert not widget.apply_presentation_surface(loading)
    assert widget.presentation_surface == displayed
    assert [image.image_id for image in widget._images] == ["0.jpg"]
    widget.close()


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


def test_manual_zoom_drag_updates_only_clamped_view_transform(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(300, 200)
    widget.show()
    source = QImage(400, 300, QImage.Format.Format_RGB32)
    source.fill(Qt.GlobalColor.white)
    widget.set_pages(
        DisplaySpread(0, (PageSlot("page", 0),), True),
        [ViewerWidget.from_qimage(0, "page", source, (400, 300))],
    )
    widget.set_manual_zoom(2.0)
    assert widget.wait_for_rendering()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))
    dpr = max(1.0, float(widget.devicePixelRatioF()))
    assert widget.view_transform.viewport_physical_size == QSize(
        round(300 * dpr),
        round(200 * dpr),
    )
    assert widget.view_transform.display_unit_physical_bounds == QSize(
        round(800 * dpr),
        round(600 * dpr),
    )
    render_generation = widget._render_generation
    render_keys = set(widget._render_cache)
    clicks: list[str] = []
    page_paint_acks: list[object] = []
    widget.rightSideClicked.connect(lambda: clicks.append("right"))
    widget.contentPainted.connect(page_paint_acks.append)

    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonPress,
        QPoint(150, 100),
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.LeftButton,
    )
    send_mouse_event(
        widget,
        QEvent.Type.MouseMove,
        QPoint(210, 140),
        button=Qt.MouseButton.NoButton,
        buttons=Qt.MouseButton.LeftButton,
    )
    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonRelease,
        QPoint(210, 140),
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.NoButton,
    )

    assert widget._pan == QPoint(60, 40)
    assert widget.view_transform.pan_offset == QPoint(60, 40)
    assert widget._render_generation == render_generation
    assert set(widget._render_cache) == render_keys
    assert not widget._render_tasks
    assert clicks == []
    assert page_paint_acks == []

    # A drag cannot overscroll, and a later sub-threshold click keeps the
    # existing side-click navigation contract.
    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonPress,
        QPoint(0, 0),
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.LeftButton,
    )
    send_mouse_event(
        widget,
        QEvent.Type.MouseMove,
        QPoint(299, 199),
        button=Qt.MouseButton.NoButton,
        buttons=Qt.MouseButton.LeftButton,
    )
    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonRelease,
        QPoint(299, 199),
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.NoButton,
    )
    assert widget._pan == QPoint(250, 200)

    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonPress,
        QPoint(250, 100),
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.LeftButton,
    )
    send_mouse_event(
        widget,
        QEvent.Type.MouseButtonRelease,
        QPoint(250, 100),
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.NoButton,
    )
    assert clicks == ["right"]
    widget.close()


def test_manual_zoom_preserves_cursor_anchor_and_keyboard_pan(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(300, 200)
    widget.show()
    source = QImage(400, 300, QImage.Format.Format_RGB32)
    source.fill(Qt.GlobalColor.white)
    widget.set_pages(
        DisplaySpread(0, (PageSlot("page", 0),), True),
        [ViewerWidget.from_qimage(0, "page", source, (400, 300))],
    )
    assert widget.wait_for_rendering()
    qapp.processEvents()
    anchor = QPoint(70, 60)
    widget.set_manual_zoom(1.0, anchor=anchor)

    def anchor_ratio() -> tuple[float, float]:
        bounds = widget._layout_bounds(widget._layout_for_current_images())
        return (
            (anchor.x() - bounds.left()) / bounds.width(),
            (anchor.y() - bounds.top()) / bounds.height(),
        )

    before = anchor_ratio()
    widget.set_manual_zoom(2.0, anchor=anchor)
    assert widget.wait_for_rendering()
    qapp.processEvents()
    after = anchor_ratio()

    assert after == pytest.approx(before, abs=0.005)
    assert widget.view_transform.zoom_anchor == QPointF(anchor)
    pan_before_key = QPoint(widget._pan)
    assert widget.pan_with_key(
        Qt.Key.Key_Right,
        Qt.KeyboardModifier.NoModifier,
    )
    assert widget._pan.x() < pan_before_key.x()
    assert widget._pan.y() == pan_before_key.y()
    assert not widget._render_tasks
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
    widget.show()
    qapp.processEvents()
    widget.set_pages(
        DisplaySpread(
            0,
            (PageSlot("first", 0), PageSlot("second", 1)),
            False,
        ),
        first_pages,
    )
    assert widget.wait_for_rendering()
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
    assert widget.wait_for_rendering()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))

    assert len(widget._last_draw_layout) == 1
    assert widget._last_image_layout[0][1].image_id == "replacement"
    assert widget._last_draw_layout[0][1].cacheKey() not in old_pixmap_keys
    widget.clear()
    widget.clear()
    assert widget._last_draw_layout == []
    widget.close()


def test_direct_display_commit_is_qpixmap_only_atomic_and_paint_acknowledged(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget = ViewerWidget()
    widget.resize(320, 240)
    widget.set_direct_display_mode(True)
    committed: list[tuple[str, ...]] = []
    painted: list[tuple[int, tuple[str, ...]]] = []
    updates: list[None] = []
    original_update = widget.update

    def record_update(*args) -> None:
        updates.append(None)
        original_update(*args)

    monkeypatch.setattr(widget, "update", record_update)
    widget.displayCommitted.connect(committed.append)
    widget.framePainted.connect(
        lambda serial, image_ids: painted.append((serial, image_ids))
    )
    pixmap = QPixmap(160, 220)
    pixmap.fill(Qt.GlobalColor.red)
    spread = DisplaySpread(4, (PageSlot("page-4", 4),), True)
    token = object()

    serial = widget.commit_display_ready_single(
        spread,
        4,
        "page-4",
        (1600, 2200),
        pixmap,
        token,
    )

    assert serial == 1
    assert updates == [None]
    assert committed == [("page-4",)]
    assert widget.displayed_page_indexes == (4,)
    assert len(widget._images) == 1
    direct_image = widget._images[0]
    assert direct_image.qimage is None
    assert direct_image.pixmap is not None
    assert direct_image.display_prepared
    assert direct_image.pre_rotated
    assert direct_image.original_size == (1600, 2200)
    assert widget._direct_current_frame_token is token
    assert widget._pending_display is None
    assert widget._render_tasks == set()

    widget.render(QPixmap(widget.size()))

    assert painted == [(serial, ("page-4",))]
    widget.close()


def test_direct_mode_preserves_completed_frame_and_resize_starts_no_render(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(320, 240)
    source = QImage(120, 180, QImage.Format.Format_RGB32)
    source.fill(Qt.GlobalColor.blue)
    spread = DisplaySpread(0, (PageSlot("existing", 0),), True)
    widget.set_pages(
        spread,
        [
            ViewerWidget.from_qimage(
                0,
                "existing",
                source,
                (120, 180),
                create_pixmap=False,
            )
        ],
    )
    assert widget.wait_for_rendering()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))
    assert widget._last_draw_layout
    old_pixmap = widget._last_draw_layout[0][1]

    widget.set_direct_display_mode(True)

    assert widget.displayed_page_indexes == (0,)
    assert widget._images[0].qimage is None
    assert widget._images[0].pixmap is not None
    assert not widget._images[0].pixmap.isNull()
    assert widget._images[0].pixmap.cacheKey() == old_pixmap.cacheKey()
    assert widget._render_cache == {}
    assert widget._prepared_units == {}
    assert widget._prepared_requests == {}
    assert widget._pending_display is None

    widget.resize(500, 300)
    qapp.processEvents()
    widget._refresh_current_render()

    assert not widget._resize_render_timer.isActive()
    assert widget._deferred_render_target is None
    assert widget._pending_display is None
    assert widget._render_tasks == set()
    widget.render(QPixmap(widget.size()))
    assert widget._last_draw_layout
    assert widget._last_draw_layout[0][1].cacheKey() == old_pixmap.cacheKey()
    widget.close()


def test_direct_mode_deactivation_restores_production_rendering(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    widget.resize(320, 240)
    widget.set_direct_display_mode(True)
    direct_pixmap = QPixmap(120, 180)
    direct_pixmap.fill(Qt.GlobalColor.red)
    widget.commit_display_ready_single(
        DisplaySpread(0, (PageSlot("direct", 0),), True),
        0,
        "direct",
        (120, 180),
        direct_pixmap,
        "direct-token",
    )
    direct_paints: list[tuple[int, tuple[str, ...]]] = []
    widget.framePainted.connect(
        lambda serial, image_ids: direct_paints.append((serial, image_ids))
    )
    widget.render(QPixmap(widget.size()))
    assert direct_paints == [(1, ("direct",))]

    widget.set_direct_display_mode(False)
    source = QImage(100, 160, QImage.Format.Format_RGB32)
    source.fill(Qt.GlobalColor.green)
    widget.set_pages(
        DisplaySpread(1, (PageSlot("production", 1),), True),
        [
            ViewerWidget.from_qimage(
                1,
                "production",
                source,
                (100, 160),
                create_pixmap=False,
            )
        ],
    )
    assert widget.wait_for_rendering()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))

    assert widget.displayed_page_indexes == (1,)
    assert widget._images[0].image_id == "production"
    assert widget._images[0].qimage is not None
    assert widget._direct_current_frame_serial == 0
    assert direct_paints == [(1, ("direct",))]
    assert widget._render_cache
    widget.close()


def test_direct_display_commit_rejects_inactive_or_mismatched_units(
    qapp: QApplication,
) -> None:
    widget = ViewerWidget()
    pixmap = QPixmap(80, 120)
    pixmap.fill(Qt.GlobalColor.red)
    spread = DisplaySpread(2, (PageSlot("page-2", 2),), True)

    with pytest.raises(RuntimeError):
        widget.commit_display_ready_single(
            spread,
            2,
            "page-2",
            (80, 120),
            pixmap,
            1,
        )

    widget.set_direct_display_mode(True)
    with pytest.raises(ValueError):
        widget.commit_display_ready_single(
            spread,
            3,
            "page-3",
            (80, 120),
            pixmap,
            2,
        )
    incomplete_spread = DisplaySpread(
        2,
        (PageSlot("page-2", 2), PageSlot("page-3", 3)),
        False,
    )
    with pytest.raises(ValueError):
        widget.commit_display_ready_frame(
            incomplete_spread,
            (
                ViewerImage(
                    page_index=2,
                    image_id="page-2",
                    pixmap=pixmap,
                    original_size=(80, 120),
                    display_prepared=True,
                ),
            ),
            3,
        )
    assert widget.displayed_page_indexes == ()
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
    position: tuple[int, int] | QPoint,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> None:
    local = QPoint(position) if isinstance(position, QPoint) else QPoint(*position)
    point = QPointF(local)
    event = QMouseEvent(
        event_type,
        point,
        QPointF(widget.mapToGlobal(local)),
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
