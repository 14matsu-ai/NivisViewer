import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyle, QStyleOptionSlider

from app.gesture_trail import draw_gesture_trail
from app.page_model import DisplaySpread, PageSlot
from app.viewer_page_slider import ViewerPageSlider
from app.viewer_widget import ViewerWidget
from app.viewer_render import ViewerRenderResult, render_qimage
from tests.test_viewer_navigation_feedback import blocked_viewer


def test_live_direction_preserves_pending_feedback_and_shared_slider(blocked_viewer, qapp):
    from tests.test_zip_raster_viewer_integration import _wait_until
    window, source = blocked_viewer
    state = window.presentation_state
    committed, history = state.displayed, state.back_history
    window.slider.setSliderDown(True)
    window.slider.setValue(3)
    for direction in ("ltr", "rtl", "ltr"):
        window.set_reading_direction(direction)
        assert window.slider.invertedAppearance() == (direction == "rtl")
        assert window.slider.value() == 3
        assert state.requested_page == 3
        assert state.displayed == committed
        assert state.back_history == history
        assert window.fullscreen_chrome.slider is window.slider
    window.slider.setSliderDown(False)
    source.release.set()
    _wait_until(qapp, lambda: state.displayed_page == 3)


def test_trail_off_preserves_active_recognition_on_both_surfaces(qapp):
    from app.explorer_list_view import ExplorerListView
    viewer, browser = ViewerWidget(), ExplorerListView()
    try:
        for surface, recognizer, set_options, signal in (
            (viewer, viewer._gesture_recognizer, viewer.set_mouse_gesture_options, viewer.gestureRecognized),
            (browser, browser._folder_gesture_recognizer, browser.set_folder_gesture_options, browser.folderGestureRecognized),
        ):
            recognized = []
            signal.connect(recognized.append)
            recognizer.begin((20, 20))
            recognizer.update((20, 80))
            if surface is viewer:
                viewer._right_button_down = True
                viewer._gesture_trail = [QPoint(20, 20), QPoint(20, 80)]
            else:
                browser._folder_gesture_right_button_down = True
                browser._folder_gesture_trail = [QPoint(20, 20), QPoint(20, 80)]
            set_options(enabled=True, show_trail=False, min_distance=36)
            assert recognizer.active
            event = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(20, 90), QPointF(20, 90), Qt.MouseButton.RightButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
            surface.mouseReleaseEvent(event)
            assert recognized == ["D"]
            assert not recognizer.active
            assert not (viewer.gesture_trail if surface is viewer else browser.folder_gesture_trail)
    finally:
        viewer.close()
        browser.close()


def test_existing_trail_setting_persists_and_applies_to_browser_viewer(tmp_path, qapp):
    from app.browser_window import BrowserWindow
    from app.config_manager import ConfigManager
    from app.settings_dialog import SettingsDialog
    from app.viewer_window import ViewerWindow
    config = ConfigManager(tmp_path / "settings.json")
    config.load()
    config.set("last_browser_path", str(tmp_path))
    browser = BrowserWindow(config_manager=config)
    viewer = ViewerWindow(config_manager=config)
    dialog = SettingsDialog(config)
    try:
        assert browser.wait_for_scan()
        dialog.show()
        dialog.mouse_gestures_checkbox.setChecked(False)
        assert dialog.mouse_gesture_trail_checkbox.isEnabled()
        dialog.mouse_gesture_trail_checkbox.setChecked(False)
        dialog.apply_settings()
        assert viewer.viewer.mouse_gesture_show_trail is False
        assert browser.list_view.mouse_gesture_show_trail is False
        assert ConfigManager(config.path).load()["mouse_gesture_show_trail"] is False
        dialog.mouse_gesture_trail_checkbox.setChecked(True)
        dialog.apply_settings()
        assert viewer.viewer.mouse_gesture_show_trail is True
        assert browser.list_view.mouse_gesture_show_trail is True
    finally:
        dialog.reject()
        browser.close()
        viewer.close()
        qapp.processEvents()


@pytest.mark.parametrize("rtl", [False, True])
def test_runtime_spread_left_page_hydration_does_not_cancel_loupe(tmp_path, qapp, rtl):
    from tests.test_zip_raster_viewer_integration import _window, _wait_until
    window, session, source, archive = _window(tmp_path, pages=4)
    try:
        window.set_view_mode("spread")
        window.set_single_first_page(False)
        window.set_reading_direction("rtl" if rtl else "ltr")
        window.show()
        qapp.processEvents()
        assert window._finish_opened_book(session.open_book(archive), modal_on_empty=False)
        _wait_until(qapp, lambda: len(window.viewer._last_image_layout) == 2)
        left = min(window.viewer._last_image_layout, key=lambda entry: entry[0].x())
        requested = window.model.focused_index
        QTest.mouseClick(window.viewer, Qt.MouseButton.MiddleButton, pos=left[0].center())
        _wait_until(qapp, lambda: window.viewer.magnifier_active)
        _wait_until(qapp, lambda: not window.viewer._render_pending and not session.viewer_runtime.has_unfinished_tasks())
        assert window.viewer.magnifier_active
        assert len(window.viewer._magnifier_spread_pixmaps) == 2
        assert window.model.focused_index == requested
        assert window.viewer.magnifier_source_page == left[1].page_index
        window.next_page()
        assert not window.viewer.magnifier_active
        assert not window.viewer._magnifier_spread_keys
    finally:
        window.close()
        qapp.processEvents()


def finish(widget, qapp):
    assert widget.wait_for_rendering()
    qapp.processEvents()
    widget.render(QPixmap(widget.size()))


@pytest.fixture
def live_spread(qapp):
    widget = ViewerWidget()
    widget.resize(600, 400)
    widget.show()
    qapp.processEvents()
    pages = []
    for index in range(2):
        image = QImage(240, 320, QImage.Format.Format_RGB32)
        image.fill(QColor("red" if index == 0 else "blue"))
        # A sharp asymmetric edge distinguishes actual resampling policies.
        painter = QPainter(image)
        painter.fillRect(0, 0, 71, 113, QColor("white"))
        painter.end()
        pages.append(widget.from_qimage(index, str(index), image, (240, 320)))
    widget.set_pages(DisplaySpread(0, tuple(PageSlot(p.image_id, p.page_index) for p in pages), False), pages)
    finish(widget, qapp)
    try:
        yield widget
    finally:
        widget.close()
        qapp.processEvents()


def test_pixmap_spread_live_algorithm_replaces_both_artifacts_without_pointer_work(live_spread, qapp, monkeypatch):
    widget = live_spread
    for _, image, _ in widget._last_image_layout:
        image.qimage = None
    promotions = []
    widget.magnifierSourceResolutionRequested.connect(lambda page, size: promotions.append(page))
    widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    finish(widget, qapp)
    old_keys = dict(widget._magnifier_spread_keys)
    old_pixmaps = {k: p.cacheKey() for k, p in widget._magnifier_spread_pixmaps.items()}
    geometry = tuple(widget._magnifier_spread_layout)
    crop = QRectF(widget.magnifier_source_rect)
    jobs = []
    submitted_sources = {}
    original = widget._queue_render
    def queue(source, key, **kwargs):
        jobs.append(key)
        submitted_sources[key] = QImage(source)
        return original(source, key, **kwargs)
    monkeypatch.setattr(widget, "_queue_render", queue)
    assert all(image.qimage is None for image in widget._images)
    widget.set_resampling_algorithms(magnifier_upscale="bilinear")
    finish(widget, qapp)
    assert len(jobs) == 2
    assert set(widget._magnifier_spread_keys) == set(old_keys)
    for identity, key in widget._magnifier_spread_keys.items():
        assert key.upscale_algorithm == "bilinear"
        assert key != old_keys[identity]
        assert widget._magnifier_spread_pixmaps[identity].cacheKey() != old_pixmaps[identity]
        expected, _ = render_qimage(submitted_sources[key], key)
        actual = widget._magnifier_spread_pixmaps[identity].toImage()
        actual_rgba = actual.convertToFormat(QImage.Format.Format_RGBA8888)
        expected_rgba = expected.convertToFormat(QImage.Format.Format_RGBA8888)
        assert actual_rgba.size() == expected_rgba.size()
        # QPixmap conversion may change non-pixel QImage metadata.
        assert bytes(actual_rgba.constBits()) == bytes(expected_rgba.constBits())
    assert tuple(widget._magnifier_spread_layout) == geometry
    assert widget.magnifier_source_rect == crop
    for x in range(100, 500, 10):
        widget._update_magnifier_selection(QPoint(x, 200))
    widget.set_resampling_algorithms(magnifier_upscale="bilinear")
    assert len(jobs) == 2
    # Zoom must change both physical targets, while preserving source identity.
    keys = dict(widget._magnifier_spread_keys)
    widget.set_magnifier_options(zoom=3.)
    finish(widget, qapp)
    assert len(jobs) == 4
    for identity, key in widget._magnifier_spread_keys.items():
        assert key.upscale_algorithm == "bilinear"
        assert key.target_width > keys[identity].target_width
        assert key.source_cache_key == keys[identity].source_cache_key
    assert sorted(promotions) == [0, 1]  # Full raster hydration is not target-sized.


def test_pdf_spread_zoom_upgrades_both_promotion_targets_once(live_spread, qapp):
    widget = live_spread
    for _, image, _ in widget._last_image_layout:
        image.rendered_size = (240, 320)
    promotions = []
    widget.magnifierPdfResolutionRequested.connect(lambda page, size: promotions.append((page, size)))
    widget.toggle_magnifier(widget._last_image_layout[0][0].center())
    finish(widget, qapp)
    first = dict(promotions)
    assert set(first) == {0, 1}
    widget.set_magnifier_options(zoom=3.)
    finish(widget, qapp)
    assert len(promotions) == 4
    for page, size in promotions[2:]:
        assert size.width() > first[page].width()
        assert size.height() > first[page].height()
    widget.set_magnifier_options(zoom=1.5)
    finish(widget, qapp)
    widget.set_magnifier_options(zoom=3.)
    finish(widget, qapp)
    widget.set_resampling_algorithms(magnifier_upscale="bilinear")
    finish(widget, qapp)
    # Requests now identify exact final artifacts (size and filter). The
    # Window's independent PDF cache deduplicates/reuses completed artifacts.
    final_count = len(promotions)
    widget.resume_magnifier_after_source_render()
    for x in (150, 300, 450):
        widget._update_magnifier_selection(QPoint(x, 200))
    assert len(promotions) == final_count == 10


@pytest.mark.parametrize("rtl", [False, True])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("dpr", [1., 1.25, 1.5, 2.])
@pytest.mark.parametrize("tier", ["full", "preview", "pixmap"])
@pytest.mark.parametrize("zoomed", [False, True])
def test_spread_loupe_samples_both_pages_gutter_and_reuses_artifacts(qapp, monkeypatch, rtl, rotation, dpr, tier, zoomed):
    widget = ViewerWidget()
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: dpr)
    widget.resize(600, 400)
    widget.set_gap(20)
    widget.set_join_spread_pages(False)
    widget.set_rotation_angle(rotation)
    widget.show()
    qapp.processEvents()
    pages = []
    for index, (width, height, color) in enumerate([(240, 320, "red"), (180, 280, "blue")]):
        source = QImage(width, height, QImage.Format.Format_RGB32)
        source.fill(QColor(color))
        painter = QPainter(source)
        painter.fillRect(0, 0, width // 3, height // 4, QColor("yellow" if index == 0 else "lime"))
        painter.end()
        pages.append(widget.from_qimage(index, str(index), source, (width, height)))
    if rtl:
        pages.reverse()
    widget.set_pages(DisplaySpread(0, tuple(PageSlot(p.image_id, p.page_index) for p in pages), False), pages)
    finish(widget, qapp)
    if zoomed:
        widget.set_manual_zoom(1.25)
        widget._pan = QPoint(25, -18)
        finish(widget, qapp)
    # Test the rendered per-page mapping, not an assumed left/current identity.
    layout = sorted(widget._last_image_layout, key=lambda entry: entry[0].x())
    assert len(layout) == 2
    for _rect, image, _pixmap in layout:
        if tier == "preview":
            image.source_is_preview = True
        elif tier == "pixmap":
            image.qimage = None
    try:
        QTest.mouseClick(widget, Qt.MouseButton.MiddleButton, pos=layout[0][0].center())
        finish(widget, qapp)
        assert widget.magnifier_active
        assert len(widget._magnifier_spread_pixmaps) == 2
        keys = dict(widget._magnifier_spread_keys)
        queue_render = widget._queue_render
        monkeypatch.setattr(widget, "_queue_render", lambda *a, **k: pytest.fail("pointer move queued a resize"))
        for point in (layout[0][0].center(), QPoint((layout[0][0].right() + layout[1][0].left()) // 2, 200), layout[1][0].center()):
            widget._update_magnifier_selection(point)
            output = QImage(600, 400, QImage.Format.Format_RGB32)
            painter = QPainter(output)
            widget._draw_magnifier(painter)
            painter.end()
            crop = widget.magnifier_source_rect
            zoom = widget._magnifier_spread_zoom
            # Compare numerous asymmetric interior pixels against each actual
            # displayed page. Also check the unchanged background in the gap.
            checked = 0
            for rect, image, pixmap in layout:
                original = pixmap.toImage()
                for fx, fy in ((.15, .15), (.5, .5), (.8, .8)):
                    x = (rect.x() + rect.width() * fx - crop.x()) * zoom
                    y = (rect.y() + rect.height() * fy - crop.y()) * zoom
                    if 2 <= x < 598 and 2 <= y < 398:
                        expected = original.pixelColor(int(original.width() * fx), int(original.height() * fy))
                        assert output.pixelColor(int(x), int(y)) == expected
                        checked += 1
            assert checked
            gap_x = ((layout[0][0].right() + layout[1][0].left()) / 2 - crop.x()) * zoom
            if 1 <= gap_x < 599:
                assert output.pixelColor(int(gap_x), 200) == widget.background_color
        assert widget._magnifier_spread_keys == keys
        widget.cancel_magnifier()
        assert not widget._magnifier_spread_keys
        stale_key = next(iter(keys.values()))
        stale = QImage(2, 2, QImage.Format.Format_RGB32)
        widget._on_render_completed(
            ViewerRenderResult(stale_key, widget._render_generation, stale, True), object()
        )
        assert not widget.magnifier_active
        assert not widget._magnifier_spread_pixmaps
        monkeypatch.setattr(widget, "_queue_render", queue_render)
        QTest.mouseClick(widget, Qt.MouseButton.MiddleButton, pos=layout[1][0].center())
        finish(widget, qapp)
        assert widget.magnifier_active
        assert widget.magnifier_source_page == layout[1][1].page_index
    finally:
        widget.close()


@pytest.mark.parametrize("rtl", [False, True])
def test_slider_direction_pixels_keys_and_drag(qapp, rtl):
    slider = ViewerPageSlider()
    slider.resize(400, 30)
    slider.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    slider.set_reading_direction("rtl" if rtl else "ltr")
    slider.set_page_state(11, 0)
    slider.show()
    qapp.processEvents()

    def handle():
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        return slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, slider).center()

    try:
        first = handle()
        slider.set_page_state(11, 10)
        last = handle()
        assert (first.x() > last.x()) == rtl
        slider.set_page_state(11, 5)
        middle = handle()
        assert min(first.x(), last.x()) < middle.x() < max(first.x(), last.x())
        QTest.keyClick(slider, Qt.Key.Key_Left if rtl else Qt.Key.Key_Right)
        assert slider.value() == 6
        QTest.keyClick(slider, Qt.Key.Key_Home)
        assert slider.value() == 0
        QTest.keyClick(slider, Qt.Key.Key_End)
        assert slider.value() == 10
        slider.set_page_state(11, 5)
        slider.setPageStep(2)
        QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=last)
        assert slider.value() == 7
        slider.set_page_state(11, 5)
        QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=handle())
        QTest.mouseMove(slider, last)
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=last)
        assert slider.value() == 10
        slider.set_reading_direction("ltr" if rtl else "rtl")
        assert slider.value() == 10
        assert handle().x() == first.x()
    finally:
        slider.close()


@pytest.mark.parametrize("dpr", [1., 1.25, 1.5, 2.])
def test_sparse_trail_continuous_and_no_bright_join(dpr):
    image = QImage(round(120 * dpr), round(60 * dpr), QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(dpr)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    draw_gesture_trail(painter, [QPoint(10, 30), QPoint(60, 30), QPoint(110, 30)])
    painter.end()
    assert all(image.pixelColor(x, int(30 * dpr)).alpha() == 150 for x in range(int(12 * dpr), int(108 * dpr)))
    assert image.pixelColor(int(60 * dpr), int(34 * dpr)).alpha() == 0
