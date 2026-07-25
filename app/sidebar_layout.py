from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


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
        self.layout_name = "favorites_top_tree_bottom"
        self.splitter: QSplitter | None = None
        self.tabs: QTabWidget | None = None
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
            layout_name if layout_name in SIDEBAR_LAYOUTS else "favorites_top_tree_bottom"
        )
        sizes = [max(40, min(4000, int(value))) for value in splitter_sizes]
        if len(sizes) != 2:
            sizes = [220, 420]
        self._splitter_sizes = sizes
        for widget in self._content_widgets:
            widget.setParent(self.container)
        self._clear_container()
        self.layout_name = normalized
        self.splitter = None
        self.tabs = None

        if normalized == "tabs":
            root = self._tabs(
                show_favorites=show_favorites,
                show_tree=show_tree,
                show_history=show_history,
            )
        elif normalized == "favorites_only":
            root = self._favorites_panel(
                show_favorites,
                show_history=False,
            )
        elif normalized == "tree_only":
            root = (
                self.folder_tree
                if show_tree
                else QWidget(self.container)
            )
        else:
            splitter = QSplitter(Qt.Orientation.Vertical, self.container)
            first, second = (
                (
                    self._favorites_panel(show_favorites, show_history),
                    self.folder_tree,
                )
                if normalized == "favorites_top_tree_bottom"
                else (
                    self.folder_tree,
                    self._favorites_panel(show_favorites, show_history),
                )
            )
            first.setVisible(
                show_tree if first is self.folder_tree else show_favorites or show_history
            )
            second.setVisible(
                show_tree if second is self.folder_tree else show_favorites or show_history
            )
            splitter.addWidget(first)
            splitter.addWidget(second)
            splitter.setCollapsible(0, True)
            splitter.setCollapsible(1, True)
            splitter.setSizes(sizes)
            splitter.splitterMoved.connect(
                lambda _position, _index: self._record_splitter_sizes(splitter)
            )
            self.splitter = splitter
            root = splitter

        self.container.layout().addWidget(root)

    def current_splitter_sizes(self) -> list[int]:
        if self.splitter is not None:
            sizes = self.splitter.sizes()
            if len(sizes) >= 2 and all(value > 0 for value in sizes[:2]):
                self._splitter_sizes = [
                    max(40, min(4000, int(value)))
                    for value in sizes[:2]
                ]
        return list(self._splitter_sizes)

    def _tabs(
        self,
        *,
        show_favorites: bool,
        show_tree: bool,
        show_history: bool,
    ) -> QWidget:
        tabs = QTabWidget(self.container)
        if show_tree:
            tabs.addTab(self.folder_tree, "フォルダ")
        if show_favorites:
            tabs.addTab(
                self._favorites_panel(True, show_history=False),
                "お気に入り",
            )
        if show_history:
            tabs.addTab(self.history_view, "履歴")
        self.tabs = tabs
        return tabs

    def _favorites_panel(
        self,
        show_favorites: bool,
        show_history: bool,
    ) -> QWidget:
        if show_favorites and not show_history:
            if self.bookmarks_view is None:
                return self.favorites_view
        if show_history and not show_favorites:
            return self.history_view
        if not show_favorites and not show_history:
            return QWidget(self.container)
        tabs = QTabWidget(self.container)
        if show_favorites:
            tabs.addTab(self.favorites_view, "フォルダ")
            if self.bookmarks_view is not None:
                tabs.addTab(self.bookmarks_view, "本")
        if show_history:
            tabs.addTab(self.history_view, "履歴")
        return tabs

    def _record_splitter_sizes(self, splitter: QSplitter) -> None:
        sizes = splitter.sizes()
        if len(sizes) >= 2:
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
