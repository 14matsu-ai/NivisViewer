from __future__ import annotations

from .i18n import tr
from .favorite_tabs import FavoriteTabs, apply_tab_padding

from collections.abc import Iterable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QSplitter, QTabWidget, QVBoxLayout, QWidget


SIDEBAR_LAYOUTS = {
    "favorites_top_tree_bottom",
    "tree_top_favorites_bottom",
    "tabs",
    "favorites_only",
    "tree_only",
}


class SidebarLayoutController(QObject):
    splitter_sizes_changed = Signal(object)

    def __init__(
        self,
        container: QWidget,
        *,
        favorites_view: QWidget,
        folder_tree: QWidget,
        history_view: QWidget,
        bookmarks_view: QWidget | None = None,
    ) -> None:
        super().__init__(container)
        self.container = container
        self.favorites_view = favorites_view
        self.folder_tree = folder_tree
        self.history_view = history_view
        self.bookmarks_view = bookmarks_view
        self.tab_settings = {}
        self.layout_name = "favorites_top_tree_bottom"
        self.splitter: QSplitter | None = None
        self.tabs: QTabWidget | None = None
        self._favorite_tabs: QTabWidget | None = None
        self._splitter_sizes = [220, 420]
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    def apply(
        self,
        layout_name: str,
        *,
        splitter_sizes: Iterable[int] = (220, 420),
        show_favorites: bool = True,
        show_tree: bool = True,
        show_history: bool = True,
    ) -> None:
        normalized = (
            layout_name
            if layout_name in SIDEBAR_LAYOUTS
            else "favorites_top_tree_bottom"
        )
        sizes = [max(40, min(4000, int(value))) for value in splitter_sizes]
        if len(sizes) != 2:
            sizes = [220, 420]
        self._splitter_sizes = sizes

        preferred_favorite = (
            self._favorite_tabs.currentWidget()
            if self._favorite_tabs is not None
            else None
        )
        preferred_area = None
        if self.tabs is not None:
            current = self.tabs.currentWidget()
            if current is self.folder_tree:
                preferred_area = "tree"
            elif current is self.history_view:
                preferred_area = "history"
            else:
                preferred_area = "favorites"

        for widget in self._content_widgets:
            widget.setParent(self.container)
            widget.hide()
        if self.bookmarks_view is not None:
            self.bookmarks_view.hide()
        self._clear_container()
        self.layout_name = normalized
        self.splitter = None
        self.tabs = None
        self._favorite_tabs = None

        if normalized == "tabs":
            root = self._tabs(
                show_favorites=show_favorites,
                show_tree=show_tree,
                show_history=show_history,
                preferred_area=preferred_area,
                preferred_favorite=preferred_favorite,
            )
        elif normalized == "favorites_only":
            root = self._favorites_panel(
                show_favorites, show_history=False,
                preferred_favorite=preferred_favorite,
            )
        elif normalized == "tree_only":
            root = self.folder_tree if show_tree else QWidget(self.container)
        else:
            favorites_panel = self._favorites_panel(
                show_favorites,
                show_history,
                preferred_favorite=preferred_favorite,
            )
            favorites_visible = show_favorites or show_history
            ordered = (
                (
                    (favorites_panel, favorites_visible),
                    (self.folder_tree, show_tree),
                )
                if normalized == "favorites_top_tree_bottom"
                else (
                    (self.folder_tree, show_tree),
                    (favorites_panel, favorites_visible),
                )
            )
            visible = [widget for widget, enabled in ordered if enabled]

            if len(visible) == 2:
                splitter = QSplitter(Qt.Orientation.Vertical, self.container)
                splitter.setChildrenCollapsible(False)
                splitter.addWidget(visible[0])
                splitter.addWidget(visible[1])
                visible[0].show()
                visible[1].show()
                splitter.setSizes(sizes)
                splitter.splitterMoved.connect(
                    lambda _position, _index: self._record_splitter_sizes(
                        splitter
                    )
                )
                self.splitter = splitter
                root = splitter
            elif len(visible) == 1:
                root = visible[0]
            else:
                root = QWidget(self.container)

        self.container.layout().addWidget(root)
        root.show()
        self.apply_tab_settings()

    def apply_tab_settings(self):
        for tabs in (self.tabs, self._favorite_tabs):
            if tabs is not None:
                apply_tab_padding(tabs.tabBar(), self.tab_settings)
        if isinstance(self.favorites_view, FavoriteTabs):
            self.favorites_view.configure(self.tab_settings)

    def current_splitter_sizes(self) -> list[int]:
        return list(self._splitter_sizes)

    def _tabs(
        self,
        *,
        show_favorites: bool,
        show_tree: bool,
        show_history: bool,
        preferred_area: str | None,
        preferred_favorite: QWidget | None,
    ) -> QWidget:
        tabs = QTabWidget(self.container)
        if show_tree:
            tabs.addTab(self.folder_tree, tr("フォルダ"))
        if show_favorites:
            favorites_panel = self._favorites_panel(
                True, show_history=False,
                preferred_favorite=preferred_favorite,
            )
            tabs.addTab(
                favorites_panel,
                tr("お気に入り"),
            )
        if show_history:
            tabs.addTab(self.history_view, tr("履歴"))
        preferred_widget = {
            "tree": self.folder_tree if show_tree else None,
            "favorites": favorites_panel if show_favorites else None,
            "history": self.history_view if show_history else None,
        }.get(preferred_area)
        if preferred_widget is not None:
            tabs.setCurrentWidget(preferred_widget)
        self.tabs = tabs
        return tabs

    def _favorites_panel(
        self,
        show_favorites: bool,
        show_history: bool,
        preferred_favorite: QWidget | None = None,
    ) -> QWidget:
        if isinstance(self.favorites_view, FavoriteTabs):
            self.favorites_view.set_history(self.history_view if show_history and show_favorites else None)
            if show_favorites:
                self._favorite_tabs = self.favorites_view
                if preferred_favorite is self.history_view and show_history:
                    self.favorites_view.setCurrentWidget(self.history_view)
                return self.favorites_view
        if show_favorites and not show_history:
            return self.favorites_view
        if show_history and not show_favorites:
            return self.history_view
        if not show_favorites and not show_history:
            return QWidget(self.container)

        tabs = QTabWidget(self.container)
        if show_favorites:
            tabs.addTab(self.favorites_view, tr("お気に入り"))
        if show_history:
            tabs.addTab(self.history_view, tr("履歴"))
        if preferred_favorite is self.history_view and show_history:
            tabs.setCurrentWidget(self.history_view)
        if show_favorites:
            self._favorite_tabs = tabs
        return tabs

    def _record_splitter_sizes(self, splitter: QSplitter) -> None:
        sizes = splitter.sizes()
        if len(sizes) >= 2 and all(value > 0 for value in sizes[:2]):
            self._splitter_sizes = [
                max(40, min(4000, int(value)))
                for value in sizes[:2]
            ]
            self.splitter_sizes_changed.emit(list(self._splitter_sizes))

    def _clear_container(self) -> None:
        layout = self.container.layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in self._content_widgets:
                widget.hide()
                widget.deleteLater()

    @property
    def _content_widgets(self) -> tuple[QWidget, ...]:
        return tuple(
            widget
            for widget in (
                self.favorites_view,
                self.bookmarks_view,
                self.folder_tree,
                self.history_view,
            )
            if widget is not None
        )
