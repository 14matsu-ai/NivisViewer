from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QListView,
    QMainWindow,
    QMenu,
    QSlider,
    QStatusBar,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from app.fullscreen_chrome import FullscreenChromeController
from app.sidebar_layout import SidebarLayoutController


def test_fullscreen_edge_reveal_is_overlay_and_keeps_viewer_focus(qapp) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    layout.setContentsMargins(0, 0, 0, 0)
    viewer = QWidget(central)
    viewer.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    slider = QSlider(central)
    layout.addWidget(viewer, 1)
    layout.addWidget(slider)
    window.setCentralWidget(central)
    status = QStatusBar(window)
    window.setStatusBar(status)
    menu_bar = window.menuBar()
    menu_bar.addMenu("表示")
    controller = FullscreenChromeController(
        window,
        viewer=viewer,
        menu_bar=menu_bar,
        slider=slider,
        status_bar=status,
        edge_trigger_px=8,
        hide_delay_ms=300,
    )
    window.resize(640, 480)
    window.show()
    qapp.processEvents()
    controller.set_active(True)
    viewer.setFocus()
    qapp.processEvents()
    viewer_size = viewer.size()

    controller.process_pointer(
        central.mapToGlobal(QPoint(central.width() // 2, 0))
    )
    qapp.processEvents()

    assert controller.top_overlay.isVisible()
    assert not controller.bottom_overlay.isVisible()
    assert viewer.size() == viewer_size
    assert viewer.hasFocus()

    controller.process_pointer(
        central.mapToGlobal(
            QPoint(central.width() // 2, central.height() - 1)
        )
    )
    qapp.processEvents()

    assert not controller.top_overlay.isVisible()
    assert controller.bottom_overlay.isVisible()
    assert viewer.size() == viewer_size
    assert viewer.hasFocus()

    controller.set_active(False)
    qapp.processEvents()
    assert menu_bar.parent() is window
    assert status.parent() is window
    window.close()


def test_fullscreen_trigger_and_delay_are_clamped() -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = QSlider(central)
    layout.addWidget(viewer)
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
        edge_trigger_px=1,
        hide_delay_ms=50,
    )

    assert controller.edge_trigger_px == 4
    assert controller.hide_delay_ms == 300
    controller.configure(
        auto_reveal=True,
        edge_trigger_px=100,
        hide_delay_ms=9000,
    )
    assert controller.edge_trigger_px == 32
    assert controller.hide_delay_ms == 3000
    window.close()


def test_fullscreen_chrome_does_not_hide_during_slider_or_popup(qapp) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = QSlider(central)
    layout.addWidget(viewer)
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
    window.show()
    controller.set_active(True)
    controller.show_bottom()
    slider.setSliderDown(True)
    controller._hide_if_idle()
    assert controller.bottom_overlay.isVisible()
    slider.setSliderDown(False)

    controller.show_top()
    popup = QMenu(window)
    popup.addAction("項目")
    popup.popup(window.mapToGlobal(QPoint(10, 10)))
    qapp.processEvents()
    assert QApplication.activePopupWidget() is popup
    controller._hide_if_idle()
    assert controller.top_overlay.isVisible()
    popup.close()
    controller.set_active(False)
    window.close()


def test_fullscreen_chrome_hides_after_configured_delay(qapp) -> None:
    window = QMainWindow()
    central = QWidget(window)
    layout = QVBoxLayout(central)
    viewer = QWidget(central)
    slider = QSlider(central)
    layout.addWidget(viewer)
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
        hide_delay_ms=300,
    )
    window.show()
    controller.set_active(True)
    controller.show_top()
    controller._pointer_in_reveal_area = lambda _position: False

    controller.schedule_hide()
    QTest.qWait(340)
    qapp.processEvents()

    assert not controller.top_overlay.isVisible()
    controller.set_active(False)
    window.close()


def test_sidebar_layout_variants_and_splitter_state(qapp) -> None:
    container = QWidget()
    favorites = QListView()
    tree = QTreeView()
    history = QListView()
    controller = SidebarLayoutController(
        container,
        favorites_view=favorites,
        folder_tree=tree,
        history_view=history,
    )
    changed_sizes: list[list[int]] = []
    controller.splitter_sizes_changed.connect(changed_sizes.append)

    controller.apply(
        "favorites_top_tree_bottom",
        splitter_sizes=[180, 320],
    )
    assert controller.splitter is not None
    assert controller.splitter.indexOf(tree) == 1
    controller._record_splitter_sizes(controller.splitter)
    assert changed_sizes

    controller.apply(
        "tree_top_favorites_bottom",
        splitter_sizes=changed_sizes[-1],
    )
    assert controller.splitter is not None
    assert controller.splitter.indexOf(tree) == 0

    controller.apply("tabs", splitter_sizes=changed_sizes[-1])
    assert controller.tabs is not None
    assert [
        controller.tabs.tabText(index)
        for index in range(controller.tabs.count())
    ] == ["フォルダ", "お気に入り", "履歴"]
    assert controller.current_splitter_sizes() == changed_sizes[-1]

    controller.apply("favorites_only")
    assert container.layout().itemAt(0).widget() is favorites
    controller.apply("tree_only")
    assert container.layout().itemAt(0).widget() is tree
    container.close()


def test_sidebar_component_visibility_removes_hidden_tabs() -> None:
    container = QWidget()
    favorites = QListView()
    tree = QTreeView()
    history = QListView()
    controller = SidebarLayoutController(
        container,
        favorites_view=favorites,
        folder_tree=tree,
        history_view=history,
    )

    controller.apply(
        "tabs",
        show_favorites=False,
        show_tree=True,
        show_history=False,
    )

    assert controller.tabs is not None
    assert controller.tabs.count() == 1
    assert controller.tabs.tabText(0) == "フォルダ"


def test_sidebar_keeps_existing_book_bookmarks_accessible() -> None:
    container = QWidget()
    favorites = QListView()
    bookmarks = QListView()
    tree = QTreeView()
    history = QListView()
    controller = SidebarLayoutController(
        container,
        favorites_view=favorites,
        bookmarks_view=bookmarks,
        folder_tree=tree,
        history_view=history,
    )

    controller.apply("favorites_only")

    panel = container.layout().itemAt(0).widget()
    assert isinstance(panel, QTabWidget)
    assert [
        panel.tabText(index)
        for index in range(panel.count())
    ] == ["フォルダ", "本"]
    assert panel.widget(1) is bookmarks
