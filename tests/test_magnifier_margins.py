import pytest
from PySide6.QtCore import QEvent, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPixmap

from app.config_manager import ConfigManager
from app.page_model import DisplaySpread, PageSlot
from app.settings_dialog import SettingsDialog
from app.viewer_widget import ViewerWidget
from tests.test_viewer_resampling_magnifier import _finish_display, _image, _mouse


@pytest.mark.parametrize('spread', [False, True])
@pytest.mark.parametrize('rotation', [0, 90, 270])
@pytest.mark.parametrize('rtl', [False, True])
@pytest.mark.parametrize('zoom', [1.5, 3.0])
def test_loupe_tracks_margins_without_new_jobs(qapp, spread, rotation, rtl, zoom):
    widget = ViewerWidget()
    widget.resize(600, 400)
    widget.set_background_color('#123456')
    widget.set_rotation_angle(rotation)
    widget.set_gap(30)
    widget.set_magnifier_options(zoom=zoom)
    pages = []
    for index, color in enumerate(['#ff0000', '#00ff00'] if spread else ['#ff0000']):
        image = _image(160, 200)
        image.fill(QColor(color))
        pages.append(ViewerWidget.from_qimage(index, str(index), image, (160, 200)))
    if rtl:
        pages.reverse()
    widget.set_pages(DisplaySpread(0, tuple(PageSlot(p.image_id, p.page_index) for p in pages),
                                   not spread), pages)
    if zoom == 3.0:
        widget.set_manual_zoom(.75)
    widget.show()
    try:
        _finish_display(widget, qapp)
        rects = [rect for rect, _, _ in widget._last_image_layout]
        assert widget.toggle_magnifier(rects[0].center())
        assert widget.wait_for_rendering()
        qapp.processEvents()
        assert widget.magnifier_active
        key = widget._magnifier_key
        spread_keys = dict(widget._magnifier_spread_keys)
        generation = widget.magnifier_request_generation
        # Move through every page edge, outside margin and the spread gutter.
        positions = [QPoint(x, widget.height() // 2) for x in range(2, widget.width() - 2, 7)]
        positions += [QPoint(widget.width() // 2, y) for y in range(2, widget.height() - 2, 7)]
        positions += [r.center() for r in rects]
        for position in positions:
            _mouse(widget, QEvent.Type.MouseMove, position.x(), position.y(),
                   button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton)
            crop = widget.magnifier_source_rect
            if spread:
                assert crop.center().x() == pytest.approx(position.x())
                assert crop.center().y() == pytest.approx(position.y())
            else:
                normalized = widget._magnifier_source_normalized
                assert normalized.center().x() == pytest.approx((position.x() - rects[0].x()) / rects[0].width())
                assert normalized.center().y() == pytest.approx((position.y() - rects[0].y()) / rects[0].height())
            canvas = QPixmap(widget.size())
            widget.render(canvas)
            hit = next((i for i, rect in enumerate(rects) if rect.adjusted(3, 3, -3, -3).contains(position)), None)
            near_edge = any(rect.adjusted(-3, -3, 3, 3).contains(position) for rect in rects)
            if hit is not None or not near_edge:
                expected = pages[hit].qimage.pixelColor(0, 0) if hit is not None else widget.background_color
                assert canvas.toImage().pixelColor(widget.width() // 2, widget.height() // 2) == expected
        assert widget._magnifier_key == key
        assert widget._magnifier_spread_keys == spread_keys
        assert widget.magnifier_request_generation == generation
        assert not widget._render_tasks
        # Live OFF restores the old restriction; ON returns to the pointer.
        position = QPoint(2, 2)
        widget._mouse_pos = position
        widget._update_magnifier_selection(position)
        outside_crop = QRectF(widget.magnifier_source_rect)
        widget.set_magnifier_options(allow_outside_image=False)
        assert widget.magnifier_source_rect != outside_crop
        widget.set_magnifier_options(allow_outside_image=True)
        assert widget.magnifier_source_rect == outside_crop
    finally:
        widget.cancel_magnifier()
        widget.wait_for_rendering()
        widget.close()


@pytest.mark.parametrize('value, expected', [(False, False), (True, True), ('false', True), (None, True), (0, True)])
def test_outside_option_normalization_and_persistence(tmp_path, value, expected):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    assert config.get('magnifier_allow_outside_image') is True
    config.apply({'magnifier_allow_outside_image': value})
    config.save()
    assert ConfigManager(config.path).load()['magnifier_allow_outside_image'] is expected


def test_outside_option_settings_apply_and_cancel(tmp_path, qapp):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    dialog = SettingsDialog(config)
    assert dialog.magnifier_allow_outside_image_checkbox.isChecked()
    dialog.magnifier_allow_outside_image_checkbox.setChecked(False)
    dialog.reject()
    assert config.get('magnifier_allow_outside_image') is True
    dialog = SettingsDialog(config)
    dialog.magnifier_allow_outside_image_checkbox.setChecked(False)
    dialog.apply_settings()
    assert ConfigManager(config.path).load()['magnifier_allow_outside_image'] is False
    dialog.reject()
