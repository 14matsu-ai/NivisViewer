import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from app.config_manager import ConfigManager
from app.settings_dialog import SettingsDialog
from tests.test_sprint18_navigation_followup import _viewer_with_pages
from tests.test_viewer_navigation_feedback import blocked_viewer


def click(window, side):
    QTest.mouseClick(window.viewer, Qt.MouseButton.LeftButton,
                     pos=QPoint(5 if side == "left" else window.viewer.width() - 5,
                                window.viewer.height() // 2))


@pytest.mark.parametrize("mode", ["single", "spread"])
@pytest.mark.parametrize("fullscreen", [False, True])
@pytest.mark.parametrize("action", ["next_single_page", "next_display_unit"])
def test_auto_click_uses_live_effective_direction(tmp_path, qapp, mode, fullscreen, action):
    window, _ = _viewer_with_pages(tmp_path, qapp, page_count=9)
    try:
        window.set_view_mode(mode)
        window.set_single_first_page(False)
        window.config.apply({"viewer_canvas_click_direction": "auto", "viewer_canvas_left_click_action": action})
        if fullscreen:
            window.toggle_fullscreen()
        qapp.processEvents()
        for direction, next_side in (("rtl", "left"), ("ltr", "right"), ("rtl", "left")):
            window.set_reading_direction(direction)
            window._go_to_index_with_history(2)
            qapp.processEvents()
            step = 2 if mode == "spread" and action == "next_display_unit" else 1
            click(window, next_side)
            assert window.model.focused_index == 2 + step
            click(window, "right" if next_side == "left" else "left")
            assert window.model.focused_index == 2
        window._go_to_index_with_history(0)
        click(window, "right")  # RTL previous at first page: no adjacent-book action.
        assert window.model.focused_index == 0
        window._go_to_index_with_history(8)
        click(window, "left")
        assert window.model.focused_index == 8
    finally:
        window.close()
        qapp.processEvents()


@pytest.mark.parametrize("value", ["right_next", "left_next", "auto", "invalid", None])
def test_click_config_compatibility_and_settings(tmp_path, qapp, value):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    if value is not None:
        config.apply({"viewer_canvas_click_direction": value})
    config.save()
    expected = value if value in {"right_next", "left_next", "auto"} else "right_next"
    assert ConfigManager(config.path).load()["viewer_canvas_click_direction"] == expected
    dialog = SettingsDialog(config)
    try:
        dialog.show()
        combo = dialog.viewer_canvas_click_direction_combo
        assert combo.currentData() == expected
        index = combo.findData("auto")
        assert combo.itemText(index) == "綴じ方向に合わせる（自動）"
        combo.setCurrentIndex(index)
        dialog.reject()
        assert config.get("viewer_canvas_click_direction") == expected
        dialog = SettingsDialog(config)
        dialog.viewer_canvas_click_direction_combo.setCurrentIndex(index)
        dialog.apply_settings()
        assert ConfigManager(config.path).load()["viewer_canvas_click_direction"] == "auto"
    finally:
        dialog.reject()


def test_auto_click_keeps_pending_target_and_committed_history_separate(blocked_viewer, qapp):
    from tests.test_zip_raster_viewer_integration import _wait_until
    window, source = blocked_viewer
    window.config.apply({"viewer_canvas_click_direction": "auto", "viewer_canvas_left_click_action": "next_single_page"})
    window.set_reading_direction("rtl")
    state = window.presentation_state
    history = state.back_history
    click(window, "left")
    click(window, "left")
    assert state.requested_page == 2
    assert state.displayed_page == 0
    assert state.back_history == history
    source.release.set()
    _wait_until(qapp, lambda: state.displayed_page == 2)


@pytest.mark.parametrize("direction,next_side", [("right_next", "right"), ("left_next", "left")])
def test_fixed_click_modes_ignore_live_binding(tmp_path, qapp, direction, next_side):
    window, _ = _viewer_with_pages(tmp_path, qapp, page_count=9)
    try:
        window.config.apply({"viewer_canvas_click_direction": direction,
                             "viewer_canvas_left_click_action": "next_single_page"})
        for binding in ("rtl", "ltr"):
            window.set_reading_direction(binding)
            window._go_to_index_with_history(2)
            click(window, next_side)
            assert window.model.focused_index == 3
    finally:
        window.close()
        qapp.processEvents()


def test_auto_settings_apply_live_and_preserves_input_guards(tmp_path, qapp):
    window, _ = _viewer_with_pages(tmp_path, qapp, page_count=9)
    dialog = SettingsDialog(window.config)
    try:
        combo = dialog.viewer_canvas_click_direction_combo
        combo.setCurrentIndex(combo.findData("auto"))
        dialog.apply_settings()
        assert window.viewer_canvas_click_direction == "auto"
        window.set_reading_direction("rtl")
        window._go_to_index_with_history(2)
        start = QPoint(5, window.viewer.height() // 2)
        QTest.mouseClick(window.viewer, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ControlModifier, pos=start)
        QTest.mousePress(window.viewer, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(window.viewer, start + QPoint(50, 0))
        QTest.mouseRelease(window.viewer, Qt.MouseButton.LeftButton, pos=start + QPoint(50, 0))
        assert window.model.focused_index == 2
        window._drop_active = True
        click(window, "left")
        assert window.model.focused_index == 2
        window._drop_active = False
        window.toggle_fullscreen()
        qapp.processEvents()
        edge = QPoint(5, window.viewer.height() - 1)
        assert not window._canvas_click_allowed(window.viewer.mapToGlobal(edge))
        QTest.mouseClick(window.viewer, Qt.MouseButton.LeftButton, pos=edge)
        assert window.model.focused_index == 2
        click(window, "left")
        assert window.model.focused_index > 2
    finally:
        dialog.reject()
        window.close()
        qapp.processEvents()
