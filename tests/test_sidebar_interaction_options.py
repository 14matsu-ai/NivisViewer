from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListView, QToolButton

from app.config_manager import ConfigManager
from app.favorite_tabs import FavoriteTabs
from app.metadata_store import MetadataStore
from app.settings_dialog import SettingsDialog
from app.sidebar_history_view import SidebarHistoryView


def wheel(qapp, bar, delta):
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, delta),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    qapp.sendEvent(bar, event)
    qapp.processEvents()


def test_wheel_limits_overflow_overlay_and_padding(tmp_path, qapp):
    store = MetadataStore(tmp_path / "metadata.db")
    tabs = FavoriteTabs(QListView(), store)
    tabs.resize(220, 300)
    for _ in range(8):
        tabs.add_group()
    tabs.configure({})
    tabs.show()
    qapp.processEvents()
    bar = tabs.tabBar()
    last = tabs.count() - 1
    wheel(qapp, bar, -120)
    assert tabs.currentIndex() == last
    assert bar.tabRect(last).intersects(bar.rect())
    assert not any(button.isVisible() for button in bar.findChildren(QToolButton))
    assert tabs.cornerWidget() is tabs.add_button
    assert not tabs.add_button.geometry().intersects(bar.geometry())
    assert bar.styleSheet() == ""
    tabs.setCurrentIndex(0)
    wheel(qapp, bar, 120)
    assert tabs.currentIndex() == 0
    tabs.configure({"favorite_tabs_wrap_wheel": True})
    wheel(qapp, bar, 120)
    assert tabs.currentIndex() == last
    wheel(qapp, bar, -120)
    assert tabs.currentIndex() == 0
    tabs.configure({f"sidebar_tab_padding_{side}": 12 for side in ("top", "bottom", "left", "right")})
    qapp.processEvents()
    before = bar.tabSizeHint(0)
    tabs.configure({f"sidebar_tab_padding_{side}": 0 for side in ("top", "bottom", "left", "right")})
    qapp.processEvents()
    after = bar.tabSizeHint(0)
    assert after.width() < before.width() and after.height() < before.height()
    tabs.close()
    tabs.deleteLater()
    qapp.processEvents()
    store.close()


def test_history_click_modes_do_not_double_open(qapp):
    view = SidebarHistoryView()
    model = QStandardItemModel(view)
    model.appendRow(QStandardItem("履歴"))
    view.setModel(model)
    opened = []
    view.open_requested.connect(lambda index: opened.append(index.row()))
    view.show()
    qapp.processEvents()
    point = view.visualRect(model.index(0, 0)).center()
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert opened == [0]
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert opened == [0]
    view.double_click_to_open = True
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert opened == [0]
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert opened == [0, 0]
    QTest.keyClick(view, Qt.Key.Key_Return)
    assert opened == [0, 0, 0]
    view.close()
    view.deleteLater()


def test_sidebar_settings_save_and_reset(tmp_path, qapp):
    config = ConfigManager(tmp_path / "config.json")
    config.load()
    dialog = SettingsDialog(config)
    assert not dialog.favorite_tabs_wrap_checkbox.isChecked()
    assert not dialog.history_double_click_checkbox.isChecked()
    dialog.favorite_tabs_wrap_checkbox.setChecked(True)
    dialog.history_double_click_checkbox.setChecked(True)
    for spin in dialog.sidebar_tab_padding_spins.values():
        spin.setValue(0)
    config.apply(dialog.values(), save=True)
    reopened = ConfigManager(config.path)
    reopened.load()
    assert reopened.get("favorite_tabs_wrap_wheel") is True
    assert reopened.get("history_double_click_to_open") is True
    assert reopened.get("sidebar_tab_padding_left") == 0
    dialog._reset_browser_scope()
    assert dialog.values()["favorite_tabs_wrap_wheel"] is False
    assert dialog.values()["history_double_click_to_open"] is False
    assert dialog.values()["sidebar_tab_padding_left"] == -1
    dialog.close()
