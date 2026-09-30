from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QInputDialog, QMenu, QMessageBox, QTabBar, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from .i18n import tr


class FavoriteTabBar(QTabBar):
    wrap_wheel = False

    def _hide_scroll_buttons(self):
        # Keep Qt's current-tab scrolling, but expose no arrow controls.
        for button in self.findChildren(QToolButton):
            button.hide()

    def tabLayoutChange(self):
        super().tabLayoutChange()
        self._hide_scroll_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._hide_scroll_buttons()

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta and self.count():
            target = self.currentIndex() + (-1 if delta > 0 else 1)
            if self.wrap_wheel:
                target %= self.count()
            else:
                target = max(0, min(self.count() - 1, target))
            self.setCurrentIndex(target)
            self._hide_scroll_buttons()
        event.accept()


class FavoriteTabs(QTabWidget):
    """Persistent favorite groups sharing the existing compact folder view."""

    group_changed = Signal(int)
    edit_requested = Signal(int)

    def __init__(self, view, store, parent=None):
        super().__init__(parent)
        self.view = view
        self.store = store
        self._group_id = 1
        self._history = None
        self.setTabBar(FavoriteTabBar(self))
        self.tabBar().setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabBar().customContextMenuRequested.connect(self._context_menu)
        self.tabBarDoubleClicked.connect(self._edit_menu)
        button = QToolButton(self)
        button.setText("+")
        button.setToolTip(tr("お気に入りタブを追加"))
        button.setAccessibleName(tr("お気に入りタブを追加"))
        button.clicked.connect(self.add_group)
        button.setEnabled(store is not None and store.enabled)
        self.setCornerWidget(button)
        self.add_button = button
        self._add_button_opacity = QGraphicsOpacityEffect(button)
        self._add_button_opacity.setOpacity(1.0)
        button.setGraphicsEffect(self._add_button_opacity)
        self.currentChanged.connect(self._activate)
        self.reload()

    def configure(self, settings):
        transparency = max(0, min(100, int(settings.get("favorite_add_button_transparency", 0))))
        self._add_button_opacity.setOpacity(1.0 - transparency / 100.0)
        self.tabBar().wrap_wheel = bool(settings.get("favorite_tabs_wrap_wheel", False))
        apply_tab_padding(self.tabBar(), settings)

    @property
    def group_id(self):
        return self._group_id

    def set_history(self, history):
        previous = self._history
        if previous is not None:
            index = self.indexOf(previous)
            if index >= 0:
                self.removeTab(index)
        self._history = history
        if history is not None:
            self.addTab(history, tr("履歴"))

    def reload(self, selected=None):
        # Keep surviving pages and the history view attached. Rebuilding every
        # tab discards the current history selection and creates layout churn.
        keep_history = selected is None and self._history is not None and self.currentWidget() is self._history
        selected = self.group_id if selected is None else selected
        groups = self.store.list_favorite_groups() if self.store else [(1, tr("フォルダ"))]
        group_ids = {group_id for group_id, _ in groups}
        if selected not in group_ids:
            selected = groups[0][0]
        previous_group = self._group_id
        pages = {self.tabBar().tabData(i): self.widget(i) for i in range(self.count())
                 if self.tabBar().tabData(i) is not None}
        blocked = self.blockSignals(True)
        try:
            for group_id, page in pages.items():
                if group_id not in group_ids:
                    if self.view.parentWidget() is page:
                        self.view.setParent(self)
                    self.removeTab(self.indexOf(page))
                    page.hide()
                    page.deleteLater()
            for position, (group_id, name) in enumerate(groups):
                page = pages.get(group_id)
                if page is None:
                    page = QWidget(self)
                    layout = QVBoxLayout(page)
                    layout.setContentsMargins(0, 0, 0, 0)
                    index = self.insertTab(position, page, name)
                    self.tabBar().setTabData(index, group_id)
                    pages[group_id] = page
                else:
                    index = self.indexOf(page)
                    self.setTabText(index, name)
            if self._history is not None and self.indexOf(self._history) < 0:
                self.addTab(self._history, tr("履歴"))
            page = pages[selected]
            if self.view.parentWidget() is not page:
                page.layout().addWidget(self.view)
            self._group_id = selected
            self.setCurrentWidget(self._history if keep_history else page)
            self.view.show()
        finally:
            self.blockSignals(blocked)
        if selected != previous_group:
            self.group_changed.emit(selected)

    def _activate(self, index):
        if index < 0:
            return
        group_id = self.tabBar().tabData(index)
        if group_id is None:
            return
        self._group_id = group_id
        self.widget(index).layout().addWidget(self.view)
        self.view.show()
        self.group_changed.emit(self.group_id)

    def add_group(self):
        if self.store is None:
            return
        names = {name for _, name in self.store.list_favorite_groups()}
        number = 2
        while tr("フォルダ{p0}", p0=number) in names:
            number += 1
        group_id = self.store.create_favorite_group(tr("フォルダ{p0}", p0=number))
        if group_id:
            self.reload(group_id)

    def rename_group(self, index, name):
        if self.store and self.store.rename_favorite_group(self.tabBar().tabData(index), name):
            self.setTabText(index, name.strip())

    def delete_group(self, index):
        if self.store and self.store.delete_favorite_group(self.tabBar().tabData(index)):
            self.reload()

    def _context_menu(self, position):
        self._edit_menu(self.tabBar().tabAt(position))

    def _edit_menu(self, index):
        if index < 0 or self.store is None or self.tabBar().tabData(index) is None:
            return
        menu = QMenu(self)
        edit = menu.addAction(tr("お気に入りを編集…"))
        menu.addSeparator()
        rename = menu.addAction(tr("名前を変更"))
        delete = menu.addAction(tr("タブを削除"))
        delete.setEnabled(len(self.store.list_favorite_groups()) > 1)
        action = menu.exec(self.tabBar().mapToGlobal(self.tabBar().tabRect(index).bottomLeft()))
        if action is edit:
            self.edit_requested.emit(int(self.tabBar().tabData(index)))
        elif action is rename:
            name, accepted = QInputDialog.getText(self, tr("タブの名前"), tr("名前:"), text=self.tabText(index))
            if accepted and name.strip():
                self.rename_group(index, name)
        elif action is delete:
            answer = QMessageBox.question(
                self, tr("タブを削除"),
                tr("このタブとタブ内のお気に入り登録を削除します。実際のファイルやフォルダは削除しません。"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.delete_group(index)


def apply_tab_padding(bar, settings):
    rules = []
    for side in ("top", "right", "bottom", "left"):
        value = max(-1, min(24, int(settings.get(f"sidebar_tab_padding_{side}", -1))))
        if value >= 0:
            rules.append(f"padding-{side}: {value}px;")
    bar.setStyleSheet("QTabBar::tab { " + " ".join(rules) + " }" if rules else "")
