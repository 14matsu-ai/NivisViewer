from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel, QWheelEvent
from PySide6.QtWidgets import QApplication, QListView, QSizePolicy

from app.browser_thumbnail_scheduler import build_thumbnail_request_plan
from app.browser_wheel_scroll import BrowserWheelScrollAccumulator
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.explorer_list_view import ExplorerListView
from app.settings_dialog import SettingsDialog
from app.thumbnail_provider import BrowserThumbnailProvider
from app.viewer_widget import ViewerWidget


def _wheel_event(
    target,
    *,
    angle_y: int = 0,
    pixel_y: int = 0,
) -> QWheelEvent:
    return QWheelEvent(
        QPointF(10, 10),
        QPointF(target.mapToGlobal(QPoint(10, 10))),
        QPoint(0, pixel_y),
        QPoint(0, angle_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )


def _scroll_view(
    qapp: QApplication,
    *,
    mode: str = "system",
    custom_rows: int = 3,
    row_height: int = 100,
) -> ExplorerListView:
    view = ExplorerListView()
    view.setViewMode(QListView.ViewMode.IconMode)
    view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
    view.setGridSize(QSize(100, row_height))
    model = QStandardItemModel(view)
    for index in range(200):
        model.appendRow(QStandardItem(str(index)))
    view.setModel(model)
    view.resize(320, 240)
    view.show()
    qapp.processEvents()
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum() // 2)
    view.set_wheel_scroll_policy(mode, custom_rows)
    return view


def _plain_system_view(
    qapp: QApplication,
    *,
    row_height: int = 100,
) -> QListView:
    view = QListView()
    view.setViewMode(QListView.ViewMode.IconMode)
    view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
    view.setGridSize(QSize(100, row_height))
    model = QStandardItemModel(view)
    for index in range(200):
        model.appendRow(QStandardItem(str(index)))
    view.setModel(model)
    view.resize(320, 240)
    view.show()
    qapp.processEvents()
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum() // 2)
    return view


def _wheel_distance(view: ExplorerListView, angle_y: int = -120) -> int:
    scrollbar = view.verticalScrollBar()
    before = scrollbar.value()
    view.wheelEvent(_wheel_event(view.viewport(), angle_y=angle_y))
    return scrollbar.value() - before


def test_system_mode_preserves_inherited_qt_wheel_behavior(
    qapp: QApplication,
) -> None:
    default_view = _plain_system_view(qapp)
    explicit_system_view = _scroll_view(qapp, mode="system", custom_rows=12)

    before = default_view.verticalScrollBar().value()
    default_view.wheelEvent(
        _wheel_event(default_view.viewport(), angle_y=-120)
    )
    inherited = default_view.verticalScrollBar().value() - before
    explicit = _wheel_distance(explicit_system_view)

    assert inherited != 0
    assert explicit == inherited
    default_view.close()
    explicit_system_view.close()


def test_custom_mode_preserves_qt_pixel_delta_touchpad_path(
    qapp: QApplication,
) -> None:
    qt_view = _plain_system_view(qapp)
    custom_view = _scroll_view(qapp, mode="large", row_height=100)

    qt_before = qt_view.verticalScrollBar().value()
    qt_event = _wheel_event(qt_view.viewport(), pixel_y=-17)
    qt_view.wheelEvent(qt_event)
    qt_distance = qt_view.verticalScrollBar().value() - qt_before
    custom_before = custom_view.verticalScrollBar().value()
    custom_event = _wheel_event(custom_view.viewport(), pixel_y=-17)
    custom_view.wheelEvent(custom_event)
    custom_distance = custom_view.verticalScrollBar().value() - custom_before

    assert custom_distance == qt_distance
    assert custom_event.isAccepted() == qt_event.isAccepted()
    qt_view.close()
    custom_view.close()


def test_row_presets_increase_and_follow_grid_height(qapp: QApplication) -> None:
    distances: list[int] = []
    for mode in ("small", "medium", "large"):
        view = _scroll_view(qapp, mode=mode, row_height=100)
        distances.append(_wheel_distance(view))
        view.close()

    assert distances == [100, 200, 300]

    for scaled_row_height in (100, 125, 150, 200):
        resized = _scroll_view(
            qapp,
            mode="small",
            row_height=scaled_row_height,
        )
        assert _wheel_distance(resized) == scaled_row_height
        resized.close()


def test_custom_rows_and_partial_angle_deltas_are_accumulated(
    qapp: QApplication,
) -> None:
    view = _scroll_view(qapp, mode="custom", custom_rows=4, row_height=90)
    assert _wheel_distance(view) == 360
    view.close()

    high_resolution = _scroll_view(qapp, mode="small", row_height=101)
    start = high_resolution.verticalScrollBar().value()
    for _ in range(8):
        high_resolution.wheelEvent(
            _wheel_event(high_resolution.viewport(), angle_y=-15)
        )
    assert high_resolution.verticalScrollBar().value() - start == 101
    high_resolution.close()


def test_wheel_config_persists_and_clamps_invalid_custom_rows(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = ConfigManager(path)
    config.load()
    config.apply(
        {
            "browser_wheel_scroll_mode": "custom",
            "browser_wheel_scroll_custom_rows": 7,
        },
        save=True,
    )

    restored = ConfigManager(path)
    restored.load()
    assert restored.get("browser_wheel_scroll_mode") == "custom"
    assert restored.get("browser_wheel_scroll_custom_rows") == 7

    restored.apply({"browser_wheel_scroll_custom_rows": 0})
    assert restored.get("browser_wheel_scroll_custom_rows") == 1
    restored.apply({"browser_wheel_scroll_custom_rows": 10_000})
    assert restored.get("browser_wheel_scroll_custom_rows") == 12
    restored.apply({"browser_wheel_scroll_mode": "unknown"})
    assert restored.get("browser_wheel_scroll_mode") == "system"


def test_settings_dialog_exposes_custom_rows_and_restores_system_default(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(
        {
            "browser_wheel_scroll_mode": "custom",
            "browser_wheel_scroll_custom_rows": 8,
        }
    )
    dialog = SettingsDialog(config)
    dialog._initial_probe_started = True
    dialog.tabs.setCurrentIndex(3)
    dialog.show()
    qapp.processEvents()

    assert dialog.windowTitle() == "設定"
    assert dialog.tabs.tabText(3) == "Mouse"
    assert dialog.browser_wheel_scroll_group.title() == "Browser マウスホイール"
    assert [
        dialog.browser_wheel_scroll_mode_combo.itemText(index)
        for index in range(dialog.browser_wheel_scroll_mode_combo.count())
    ] == ["System / Default", "Small", "Medium", "Large", "Custom"]
    browser_page = dialog.tabs.widget(1).widget()
    mouse_scroll = dialog.tabs.widget(3)
    mouse_page = mouse_scroll.widget()
    assert mouse_page.isAncestorOf(dialog.browser_wheel_scroll_group)
    assert not browser_page.isAncestorOf(dialog.browser_wheel_scroll_group)
    wheel_top = dialog.browser_wheel_scroll_group.mapTo(
        mouse_scroll.viewport(),
        QPoint(0, 0),
    ).y()
    assert 0 <= wheel_top < mouse_scroll.viewport().height()
    assert (
        wheel_top + dialog.browser_wheel_scroll_group.height()
        <= mouse_scroll.viewport().height()
    )
    wheel_form = dialog.browser_wheel_scroll_group.layout()
    assert wheel_form.labelForField(
        dialog.browser_wheel_scroll_mode_combo
    ).text() == "スクロール量:"
    assert wheel_form.labelForField(
        dialog.browser_wheel_scroll_custom_spin
    ).text() == "Customの行数:"
    assert dialog.browser_wheel_scroll_mode_combo.currentData() == "custom"
    assert dialog.browser_wheel_scroll_custom_spin.value() == 8
    assert dialog.browser_wheel_scroll_custom_spin.isEnabled()
    dialog._restore_browser_wheel_scroll_default()
    values = dialog.values()
    assert values["browser_wheel_scroll_mode"] == "system"
    assert values["browser_wheel_scroll_custom_rows"] == 3
    assert not dialog.browser_wheel_scroll_custom_spin.isEnabled()
    default_changed = dialog.apply_settings()
    assert default_changed["browser_wheel_scroll_mode"] == "system"
    assert default_changed["browser_wheel_scroll_custom_rows"] == 3
    custom_index = dialog.browser_wheel_scroll_mode_combo.findData("custom")
    dialog.browser_wheel_scroll_mode_combo.setCurrentIndex(custom_index)
    dialog.browser_wheel_scroll_custom_spin.setValue(6)
    changed = dialog.apply_settings()
    assert changed["browser_wheel_scroll_mode"] == "custom"
    assert changed["browser_wheel_scroll_custom_rows"] == 6
    restored = ConfigManager(config.path)
    restored.load()
    assert restored.get("browser_wheel_scroll_mode") == "custom"
    assert restored.get("browser_wheel_scroll_custom_rows") == 6
    dialog.close()
    qapp.processEvents()


def test_settings_tab_pages_follow_viewport_without_width_inflation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    dialog = SettingsDialog(config)
    dialog._initial_probe_started = True
    dialog.show()
    qapp.processEvents()
    initial_width = dialog.width()
    initial_size_hint_width = dialog.sizeHint().width()

    for scroll in dialog._scroll_areas:
        assert scroll.widgetResizable()
        assert (
            scroll.horizontalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert (
            scroll.widget().sizePolicy().horizontalPolicy()
            == QSizePolicy.Policy.Ignored
        )

    for _cycle in range(4):
        for index in (1, 3, 2, 0, 4, 1):
            dialog.tabs.setCurrentIndex(index)
            qapp.processEvents()
            scroll = dialog.tabs.currentWidget()
            assert scroll.widget().width() <= scroll.viewport().width() + 1
        QApplication.sendEvent(dialog, QEvent(QEvent.Type.WindowDeactivate))
        QApplication.sendEvent(dialog, QEvent(QEvent.Type.WindowActivate))
        qapp.processEvents()
        assert dialog.width() == initial_width
        assert dialog.minimumSizeHint().width() <= initial_width
        assert dialog.sizeHint().width() <= max(700, initial_size_hint_width)

    dialog.hide()
    qapp.processEvents()
    dialog.show()
    qapp.processEvents()
    assert dialog.width() == initial_width

    dialog.resize(900, 700)
    dialog.tabs.setCurrentIndex(3)
    qapp.processEvents()
    dialog.resize(520, 480)
    for index in (1, 3, 0, 1):
        dialog.tabs.setCurrentIndex(index)
        qapp.processEvents()
        scroll = dialog.tabs.currentWidget()
        assert scroll.widget().width() <= scroll.viewport().width() + 1
    assert dialog.width() == 520
    assert dialog.minimumWidth() <= 520

    dialog.resize(initial_width, 680)
    qapp.processEvents()
    assert dialog.width() == initial_width
    assert dialog.sizeHint().width() == initial_size_hint_width
    dialog.close()
    qapp.processEvents()


def test_live_browser_wheel_setting_has_no_scan_decode_or_generation_side_effect(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    provider = BrowserThumbnailProvider(disk_cache_enabled=False)
    window = BrowserWindow(
        config_manager=config,
        thumbnail_provider=provider,
        pdfium_service=object(),
        restore_initial_location=False,
    )
    calls = {"request": 0, "generation": 0, "scan": 0}
    original_request = provider.request
    original_generation = provider.begin_generation
    original_scan_start = window.scanner.start

    def record_request(*args, **kwargs):
        calls["request"] += 1
        return original_request(*args, **kwargs)

    def record_generation(*args, **kwargs):
        calls["generation"] += 1
        return original_generation(*args, **kwargs)

    def record_scan(*args, **kwargs):
        calls["scan"] += 1
        return original_scan_start(*args, **kwargs)

    provider.request = record_request  # type: ignore[method-assign]
    provider.begin_generation = record_generation  # type: ignore[method-assign]
    window.scanner.start = record_scan  # type: ignore[method-assign]
    scrollbar_steps = (
        window.list_view.verticalScrollBar().singleStep(),
        window.list_view.verticalScrollBar().pageStep(),
    )

    config.apply(
        {
            "browser_wheel_scroll_mode": "large",
            "browser_wheel_scroll_custom_rows": 11,
        }
    )

    assert window.list_view.wheel_scroll_mode == "large"
    assert window.list_view.wheel_scroll_custom_rows == 11
    assert (
        window.list_view.verticalScrollBar().singleStep(),
        window.list_view.verticalScrollBar().pageStep(),
    ) == scrollbar_steps
    assert calls == {"request": 0, "generation": 0, "scan": 0}
    window.close()
    qapp.processEvents()


def test_wheel_amount_is_not_an_input_to_bounded_thumbnail_planning() -> None:
    accumulator = BrowserWheelScrollAccumulator("custom", 12)
    plan = build_thumbnail_request_plan(
        row_count=50_000,
        first_visible=100,
        last_visible=119,
        prefetch_screens=1,
        scroll_direction=1,
    )

    assert accumulator.rows_per_notch == 12
    assert len(plan.visible_rows) == 20
    assert len(plan.directional_rows) <= len(plan.visible_rows)
    assert len(plan.safety_rows) <= (len(plan.visible_rows) + 3) // 4
    assert len(plan.requested_rows) <= 45


def test_browser_wheel_setting_does_not_change_viewer_wheel_navigation(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    config.apply(
        {
            "browser_wheel_scroll_mode": "custom",
            "browser_wheel_scroll_custom_rows": 12,
        }
    )
    viewer = ViewerWidget()
    actions: list[str] = []
    viewer.nextRequested.connect(lambda: actions.append("next"))

    viewer.wheelEvent(_wheel_event(viewer, angle_y=-15))

    assert actions == ["next"]
    viewer.close()
    qapp.processEvents()
