from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    QByteArray,
    QDir,
    QEvent,
    QModelIndex,
    QPoint,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QFileSystemModel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QToolBar,
    QTreeView,
    QWidget,
)

from .browser_model import (
    BROWSER_ARCHIVE_EXTENSIONS,
    BROWSER_IMAGE_EXTENSIONS,
    BrowserItem,
    BrowserItemDiscovery,
    BrowserItemKind,
    BrowserItemModel,
)
from .browser_navigation import BrowserLocation, BrowserNavigationHistory
from .bookmark_model import BookmarkModel
from .config_manager import ConfigManager
from .history_model import HistoryModel
from .metadata_store import MetadataStore
from .settings_dialog import SettingsDialog
from .thumbnail_provider import BrowserThumbnailProvider, PageThumbnailProvider
from .thumbnail_disk_cache import ThumbnailDiskCache


BrowserOpenHandler = Callable[[str, bool], object]


class BrowserWindow(QMainWindow):
    activated = Signal(object)
    closing = Signal(object)

    def __init__(
        self,
        *,
        config_manager: ConfigManager,
        open_path_handler: BrowserOpenHandler | None = None,
        discovery: BrowserItemDiscovery | None = None,
        thumbnail_provider: BrowserThumbnailProvider | None = None,
        metadata_store: MetadataStore | None = None,
    ) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("NivisViewer - ブラウザ")
        self.resize(1100, 760)

        self.config = config_manager
        self.settings = config_manager.data
        self.metadata_store = metadata_store
        self._open_path_handler = open_path_handler
        self.discovery = discovery or BrowserItemDiscovery()
        if thumbnail_provider is None:
            disk_cache = ThumbnailDiskCache(
                self.config.thumbnail_cache_dir,
                enabled=False,
                limit_mb=int(self.settings.get("thumbnail_cache_limit_mb", 512)),
            )
            thumbnail_provider = BrowserThumbnailProvider(
                self,
                disk_cache=disk_cache,
                disk_cache_enabled=bool(
                    self.settings.get("thumbnail_disk_cache_enabled", True)
                ),
            )
        self.thumbnail_provider = thumbnail_provider
        self.thumbnail_provider.thumbnail_ready.connect(self._on_thumbnail_ready)
        self.current_path: Path | None = None
        self.navigation_history = BrowserNavigationHistory()
        self._generation = self.thumbnail_provider.generation
        self._syncing_tree = False
        self._pending_tree_navigation_path: Path | None = None
        self._pending_tree_sync_path: Path | None = None
        self._location_restore_token = 0
        self._status_message_token = 0
        self._pressed_extra_buttons: set[Qt.MouseButton] = set()
        self._shutdown_prepared = False
        self._sidebar_width = self._safe_sidebar_width(
            self.settings.get("browser_sidebar_width", 280)
        )
        self.thumbnail_size = self._safe_thumbnail_size(
            self.settings.get("thumbnail_size", 180)
        )

        self._folder_change_timer = QTimer(self)
        self._folder_change_timer.setSingleShot(True)
        self._folder_change_timer.setInterval(120)
        self._folder_change_timer.timeout.connect(self._apply_pending_tree_path)

        self._build_ui()
        self.config.settings_changed.connect(self.apply_settings)
        self._restore_window_state()
        self._restore_initial_folder()

    @property
    def items(self) -> tuple[BrowserItem, ...]:
        return self.item_model.items

    def show_initial(self) -> None:
        self.show()

    def navigate_to(
        self,
        path: str | Path,
        *,
        record_history: bool = True,
        restore_location: BrowserLocation | None = None,
        force_reload: bool = False,
        capture_current: bool = True,
    ) -> bool:
        target = self._absolute_browser_path(path)
        try:
            target_stat = target.stat()
        except FileNotFoundError:
            self._show_temporary_status("フォルダが見つかりません")
            self._sync_address_bar()
            return False
        except OSError:
            self._show_temporary_status("フォルダへアクセスできません")
            self._sync_address_bar()
            return False
        if not stat.S_ISDIR(target_stat.st_mode):
            self._show_temporary_status("フォルダが見つかりません")
            self._sync_address_bar()
            return False

        same_path = self._same_path(self.current_path, target)
        if same_path and not force_reload:
            if restore_location is not None:
                self._schedule_location_restore(restore_location)
            self._sync_address_bar()
            self._update_navigation_actions()
            return True

        if capture_current:
            self._update_current_navigation_state()
        result = self.discovery.discover(target)
        if result.error is not None:
            self._show_temporary_status("フォルダへアクセスできません")
            self._sync_address_bar()
            return False

        self.current_path = result.folder
        self.config.set("last_browser_path", str(result.folder))
        self._generation = self.thumbnail_provider.begin_generation()
        self.item_model.set_items(result.items)
        self.list_view.clearSelection()
        self.list_view.setCurrentIndex(QModelIndex())
        if record_history:
            self.navigation_history.visit(BrowserLocation(str(result.folder)))
        self._folder_change_timer.stop()
        self._pending_tree_navigation_path = None
        self._sync_tree_to_path(result.folder)
        self._sync_address_bar()
        self._update_navigation_actions()
        self._update_status()
        self._schedule_location_restore(
            restore_location or BrowserLocation(str(result.folder))
        )
        QTimer.singleShot(0, self._request_visible_thumbnails)
        return True

    def set_current_folder(self, folder: str | Path) -> bool:
        return self.navigate_to(folder)

    def select_path(self, path: str | Path) -> None:
        target = self._absolute_browser_path(path)
        if target.is_file():
            folder = target.parent
            selected = target
        elif target.is_dir():
            parent = target.parent
            folder = parent if parent != target else target
            selected = target
        else:
            self.statusBar().showMessage("項目が見つかりません", 3000)
            return

        location = BrowserLocation(str(folder), selected_path=str(selected))
        if not self.navigate_to(folder, restore_location=location):
            return
        self._restore_location(location)

    def go_back(self) -> bool:
        self._update_current_navigation_state()
        location = self.navigation_history.go_back()
        if location is None:
            self._update_navigation_actions()
            return False
        if self.navigate_to(
            location.path,
            record_history=False,
            restore_location=location,
            capture_current=False,
        ):
            return True
        self.navigation_history.go_forward()
        self._update_navigation_actions()
        return False

    def go_forward(self) -> bool:
        self._update_current_navigation_state()
        location = self.navigation_history.go_forward()
        if location is None:
            self._update_navigation_actions()
            return False
        if self.navigate_to(
            location.path,
            record_history=False,
            restore_location=location,
            capture_current=False,
        ):
            return True
        self.navigation_history.go_back()
        self._update_navigation_actions()
        return False

    def go_up(self) -> bool:
        if self.current_path is None:
            return False
        parent = self.current_path.parent
        if self._same_path(parent, self.current_path):
            self._update_navigation_actions()
            return False
        return self.navigate_to(
            parent,
            restore_location=BrowserLocation(
                str(parent),
                selected_path=str(self.current_path),
            ),
        )

    def refresh_current_folder(self) -> bool:
        if self.current_path is None:
            return False
        location = self._current_location()
        if not self.navigate_to(
            self.current_path,
            record_history=False,
            restore_location=location,
            force_reload=True,
        ):
            return False
        self._show_temporary_status("フォルダを更新しました")
        return True

    def focus_address_bar(self) -> None:
        self.address_bar.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.address_bar.selectAll()

    def show_history_location(self, index: QModelIndex) -> None:
        entry = self.history_model.entry_at(index)
        if entry is not None:
            self._show_path_in_browser(entry.path)

    def open_item(self, index: QModelIndex, *, open_in_new_window: bool = False) -> None:
        item = self.item_model.item_at(index)
        if item is None:
            return
        if item.kind == BrowserItemKind.FOLDER:
            self.navigate_to(item.path)
            return
        if self._open_path_handler is not None:
            self._open_path_handler(str(item.path), open_in_new_window)

    def add_browser_bookmark(
        self,
        path: str | Path,
        *,
        item_type: str,
        label: str | None = None,
    ) -> None:
        if self.metadata_store is None:
            return
        self.metadata_store.add_browser_bookmark(
            str(path),
            item_type=item_type,
            label=label,
        )

    def remove_browser_bookmark(self, path: str | Path) -> None:
        if self.metadata_store is not None:
            self.metadata_store.remove_browser_bookmark(str(path))

    def add_current_folder_bookmark(self) -> None:
        if self.current_path is not None:
            self.add_browser_bookmark(self.current_path, item_type="folder")

    def open_bookmark(
        self,
        index: QModelIndex,
        *,
        open_in_new_window: bool = False,
    ) -> None:
        entry = self.bookmark_model.entry_at(index)
        if entry is None:
            return
        if not entry.exists:
            self.statusBar().showMessage("ブックマーク先が見つかりません", 3000)
            return
        if entry.item_type == "folder":
            self.navigate_to(entry.path)
            return
        if self._open_path_handler is not None:
            self._open_path_handler(entry.path, open_in_new_window)

    def open_history(
        self,
        index: QModelIndex,
        *,
        open_in_new_window: bool = False,
    ) -> None:
        entry = self.history_model.entry_at(index)
        if entry is None:
            return
        if not entry.exists:
            self.statusBar().showMessage("履歴の項目が見つかりません", 3000)
            return
        if self._open_path_handler is not None:
            self._open_path_handler(entry.path, open_in_new_window)

    def remove_history_entry(self, index: QModelIndex) -> None:
        entry = self.history_model.entry_at(index)
        if entry is not None and self.metadata_store is not None:
            self.metadata_store.remove_history(entry.path)

    def clear_history(self, *, confirm: bool = True) -> bool:
        if self.metadata_store is None:
            return False
        if confirm:
            answer = QMessageBox.question(
                self,
                "閲覧履歴を消去",
                "閲覧履歴をすべて消去しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self.metadata_store.clear_history()
        return True

    def set_sidebar_visible(self, visible: bool) -> None:
        if not visible and self.sidebar.isVisible():
            sizes = self.splitter.sizes()
            if sizes and sizes[0] > 0:
                self._sidebar_width = sizes[0]
        self.sidebar.setVisible(visible)
        if visible:
            total = max(self.splitter.width(), self.width(), self._sidebar_width + 1)
            self.splitter.setSizes([self._sidebar_width, max(1, total - self._sidebar_width)])
        self.sidebar_action.blockSignals(True)
        self.sidebar_action.setChecked(visible)
        self.sidebar_action.blockSignals(False)
        self.config.set("browser_sidebar_visible", visible)
        self.config.set("browser_sidebar_width", self._sidebar_width)

    def prepare_shutdown(self) -> None:
        if self._shutdown_prepared:
            return
        self._shutdown_prepared = True
        self._save_window_state()
        self.thumbnail_provider.close()

    def apply_settings(self, changed: dict[str, object]) -> None:
        if "thumbnail_size" in changed:
            self.thumbnail_size = self._safe_thumbnail_size(changed["thumbnail_size"])
            self.list_view.setIconSize(QSize(self.thumbnail_size, self.thumbnail_size))
            self.list_view.setGridSize(
                QSize(self.thumbnail_size + 44, self.thumbnail_size + 58)
            )
            self.item_model.clear_thumbnails()
            self._generation = self.thumbnail_provider.begin_generation()
            QTimer.singleShot(0, self._request_visible_thumbnails)
        if "thumbnail_disk_cache_enabled" in changed:
            self.thumbnail_provider.set_disk_cache_enabled(
                bool(changed["thumbnail_disk_cache_enabled"])
            )
        if "thumbnail_cache_limit_mb" in changed:
            self.thumbnail_provider.set_disk_cache_limit_mb(
                int(changed["thumbnail_cache_limit_mb"])
            )

    def open_settings_dialog(self) -> None:
        dialog = SettingsDialog(
            self.config,
            self,
            cache_usage_getter=self.thumbnail_provider.disk_cache_usage_bytes,
        )
        dialog.cache_clear_requested.connect(
            self.thumbnail_provider.clear_all_caches_async
        )
        self.thumbnail_provider.cache_cleared.connect(dialog.refresh_cache_usage)
        dialog.exec()

    def event(self, event: QEvent) -> bool:  # type: ignore[override]
        handled = super().event(event)
        if event.type() == QEvent.Type.WindowActivate:
            self.activated.emit(self)
        return handled

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # type: ignore[override]
        if watched is self.address_bar and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if isinstance(key_event, QKeyEvent) and key_event.key() == Qt.Key.Key_Escape:
                self._sync_address_bar()
                self.address_bar.selectAll()
                return True
        if (
            watched is self.address_bar
            and event.type()
            in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
            )
            and isinstance(event, QMouseEvent)
            and event.button()
            in (
                Qt.MouseButton.BackButton,
                Qt.MouseButton.ForwardButton,
            )
        ):
            return True

        if watched is not self.address_bar and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
        ):
            mouse_event = event
            if isinstance(mouse_event, QMouseEvent):
                button = mouse_event.button()
                if button in (
                    Qt.MouseButton.BackButton,
                    Qt.MouseButton.ForwardButton,
                ):
                    if event.type() == QEvent.Type.MouseButtonPress:
                        if button not in self._pressed_extra_buttons:
                            self._pressed_extra_buttons.add(button)
                            if button == Qt.MouseButton.BackButton:
                                self.go_back()
                            else:
                                self.go_forward()
                    else:
                        self._pressed_extra_buttons.discard(button)
                    return True

        if (
            watched is not self.address_bar
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Backspace
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            self.go_back()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if (
            event.key() == Qt.Key.Key_Backspace
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and not self.address_bar.hasFocus()
        ):
            self.go_back()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        self.prepare_shutdown()
        self.closing.emit(self)
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        QTimer.singleShot(0, self._request_visible_thumbnails)

    def _build_ui(self) -> None:
        self.file_system_model = QFileSystemModel(self)
        self.file_system_model.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives
        )
        self.file_system_model.setRootPath("")

        self.folder_tree = QTreeView(self)
        self.folder_tree.setModel(self.file_system_model)
        self.folder_tree.setRootIndex(QModelIndex())
        self.folder_tree.setHeaderHidden(True)
        for column in range(1, self.file_system_model.columnCount()):
            self.folder_tree.hideColumn(column)
        self.folder_tree.selectionModel().currentChanged.connect(self._on_tree_current_changed)
        self.file_system_model.directoryLoaded.connect(self._on_tree_directory_loaded)

        self.sidebar = QTabWidget(self)
        self.sidebar.addTab(self.folder_tree, "フォルダ")

        self.bookmark_model = BookmarkModel(self.metadata_store, self)
        self.bookmark_view = QListView(self)
        self.bookmark_view.setModel(self.bookmark_model)
        self.bookmark_view.activated.connect(self.open_bookmark)
        self.bookmark_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.bookmark_view.customContextMenuRequested.connect(
            self._show_bookmark_context_menu
        )
        self.sidebar.addTab(self.bookmark_view, "ブックマーク")

        self.history_model = HistoryModel(self.metadata_store, self)
        self.history_view = QListView(self)
        self.history_view.setModel(self.history_model)
        self.history_view.activated.connect(self.open_history)
        self.history_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.history_view.customContextMenuRequested.connect(
            self._show_history_context_menu
        )
        self.sidebar.addTab(self.history_view, "履歴")

        self.item_model = BrowserItemModel(self)
        style = self.style()
        self.item_model.set_fallback_icons(
            {
                BrowserItemKind.FOLDER: style.standardIcon(QStyle.StandardPixmap.SP_DirIcon),
                BrowserItemKind.ARCHIVE: style.standardIcon(
                    QStyle.StandardPixmap.SP_DialogOpenButton
                ),
                BrowserItemKind.IMAGE: style.standardIcon(QStyle.StandardPixmap.SP_FileIcon),
            }
        )

        self.list_view = QListView(self)
        self.list_view.setModel(self.item_model)
        self.list_view.setViewMode(QListView.ViewMode.IconMode)
        self.list_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.list_view.setMovement(QListView.Movement.Static)
        self.list_view.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.list_view.setUniformItemSizes(True)
        self.list_view.setWordWrap(True)
        self.list_view.setIconSize(QSize(self.thumbnail_size, self.thumbnail_size))
        self.list_view.setGridSize(QSize(self.thumbnail_size + 44, self.thumbnail_size + 58))
        self.list_view.activated.connect(self.open_item)
        self.list_view.selectionModel().currentChanged.connect(
            lambda _current, _previous: self._update_status()
        )
        self.list_view.verticalScrollBar().valueChanged.connect(
            lambda _value: self._request_visible_thumbnails()
        )
        self.list_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(self._show_context_menu)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.list_view)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, True)
        self.setCentralWidget(self.splitter)

        self.navigation_toolbar = QToolBar("ナビゲーション", self)
        self.navigation_toolbar.setObjectName("browser_navigation_toolbar")
        self.navigation_toolbar.setMovable(False)
        self.navigation_toolbar.setIconSize(QSize(24, 24))
        self.navigation_toolbar.setMinimumHeight(36)

        self.back_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowBack),
            "戻る",
            self,
        )
        self.back_action.setToolTip("前に表示していたフォルダへ戻る (Alt+Left)")
        self.back_action.setShortcuts(
            [QKeySequence("Alt+Left")]
        )
        self.back_action.triggered.connect(self.go_back)
        self.navigation_toolbar.addAction(self.back_action)

        self.forward_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowForward),
            "進む",
            self,
        )
        self.forward_action.setToolTip("戻る前のフォルダへ進む (Alt+Right)")
        self.forward_action.setShortcut(QKeySequence("Alt+Right"))
        self.forward_action.triggered.connect(self.go_forward)
        self.navigation_toolbar.addAction(self.forward_action)

        self.up_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_ArrowUp),
            "上へ",
            self,
        )
        self.up_action.setToolTip("ひとつ上の階層へ移動 (Alt+Up)")
        self.up_action.setShortcut(QKeySequence("Alt+Up"))
        self.up_action.triggered.connect(self.go_up)
        self.navigation_toolbar.addAction(self.up_action)

        self.refresh_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
            "更新",
            self,
        )
        self.refresh_action.setToolTip("現在のフォルダを更新 (F5)")
        self.refresh_action.setShortcut(QKeySequence("F5"))
        self.refresh_action.triggered.connect(self.refresh_current_folder)
        self.navigation_toolbar.addAction(self.refresh_action)
        self.navigation_toolbar.addSeparator()

        self.address_bar = QLineEdit(self)
        self.address_bar.setObjectName("browser_address_bar")
        self.address_bar.setClearButtonEnabled(True)
        self.address_bar.setPlaceholderText("フォルダ、画像、ZIP/CBZのパス")
        self.address_bar.setToolTip(
            "パスを入力してEnterで移動。相対パスは現在のフォルダ基準です。"
        )
        self.address_bar.returnPressed.connect(self._navigate_from_address_bar)
        self.address_bar.installEventFilter(self)
        self.navigation_toolbar.addWidget(self.address_bar)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.navigation_toolbar)

        self.focus_address_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        self.focus_address_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.focus_address_shortcut.activated.connect(self.focus_address_bar)

        for target in (
            self,
            self.list_view,
            self.list_view.viewport(),
            self.folder_tree,
            self.folder_tree.viewport(),
            self.bookmark_view,
            self.bookmark_view.viewport(),
            self.history_view,
            self.history_view.viewport(),
            self.sidebar,
            self.splitter,
        ):
            target.installEventFilter(self)

        view_menu = self.menuBar().addMenu("表示")
        self.sidebar_action = QAction("サイドバーを表示", self)
        self.sidebar_action.setCheckable(True)
        self.sidebar_action.setChecked(
            bool(self.settings.get("browser_sidebar_visible", True))
        )
        self.sidebar_action.toggled.connect(self.set_sidebar_visible)
        view_menu.addAction(self.sidebar_action)

        bookmark_menu = self.menuBar().addMenu("ブックマーク")
        add_folder_bookmark_action = QAction("現在のフォルダを追加", self)
        add_folder_bookmark_action.triggered.connect(self.add_current_folder_bookmark)
        bookmark_menu.addAction(add_folder_bookmark_action)

        history_menu = self.menuBar().addMenu("履歴")
        clear_history_action = QAction("閲覧履歴をすべて消去...", self)
        clear_history_action.triggered.connect(
            lambda _checked=False: self.clear_history()
        )
        history_menu.addAction(clear_history_action)

        settings_menu = self.menuBar().addMenu("設定")
        settings_action = QAction("環境設定...", self)
        settings_action.triggered.connect(self.open_settings_dialog)
        settings_menu.addAction(settings_action)

        self.statusBar().showMessage("フォルダを選択してください。")
        self.splitter.setSizes([self._sidebar_width, max(1, self.width() - self._sidebar_width)])
        self.set_sidebar_visible(self.sidebar_action.isChecked())
        self._update_navigation_actions()

    def _restore_initial_folder(self) -> None:
        raw_path = self.settings.get("last_browser_path", "")
        candidate = Path(raw_path) if isinstance(raw_path, str) and raw_path else Path.home()
        if not candidate.is_dir():
            candidate = Path.home()
        self.set_current_folder(candidate)

    def _restore_window_state(self) -> None:
        raw_geometry = self.settings.get("browser_window_geometry", "")
        if not isinstance(raw_geometry, str) or not raw_geometry:
            return
        try:
            self.restoreGeometry(QByteArray.fromBase64(raw_geometry.encode("ascii")))
        except (ValueError, TypeError):
            return

    def _save_window_state(self) -> None:
        if self.sidebar.isVisible():
            sizes = self.splitter.sizes()
            if sizes and sizes[0] > 0:
                self._sidebar_width = sizes[0]
        self.config.set("browser_sidebar_visible", self.sidebar.isVisible())
        self.config.set("browser_sidebar_width", self._sidebar_width)
        self.config.set(
            "browser_window_geometry",
            bytes(self.saveGeometry().toBase64()).decode("ascii"),
        )

    def _on_tree_current_changed(
        self,
        current: QModelIndex,
        _previous: QModelIndex,
    ) -> None:
        if self._syncing_tree or not current.isValid():
            return
        path = Path(self.file_system_model.filePath(current))
        if not path.is_dir():
            return
        self._pending_tree_navigation_path = path
        self._folder_change_timer.start()

    def _apply_pending_tree_path(self) -> None:
        path = self._pending_tree_navigation_path
        self._pending_tree_navigation_path = None
        if path is not None:
            self.navigate_to(path)

    def _sync_tree_to_path(self, path: Path) -> None:
        index = self.file_system_model.index(str(path))
        if not index.isValid():
            self._pending_tree_sync_path = path
            return
        if self._pending_tree_sync_path == path:
            self._pending_tree_sync_path = None
        self._syncing_tree = True
        try:
            self.folder_tree.setCurrentIndex(index)
            self.folder_tree.scrollTo(index, QTreeView.ScrollHint.PositionAtCenter)
            parent = index.parent()
            while parent.isValid():
                self.folder_tree.expand(parent)
                parent = parent.parent()
        finally:
            self._syncing_tree = False

    def _on_tree_directory_loaded(self, _path: str) -> None:
        pending = self._pending_tree_sync_path
        if pending is not None:
            self._sync_tree_to_path(pending)

    def _request_visible_thumbnails(self) -> None:
        if self._shutdown_prepared or not self.items:
            return
        first = self.list_view.indexAt(QPoint(1, 1))
        last = self.list_view.indexAt(
            QPoint(
                max(1, self.list_view.viewport().width() - 2),
                max(1, self.list_view.viewport().height() - 2),
            )
        )
        first_row = first.row() if first.isValid() else 0
        last_row = last.row() if last.isValid() else min(len(self.items) - 1, first_row + 29)
        if last_row < first_row:
            last_row = min(len(self.items) - 1, first_row + 29)
        for row in range(max(0, first_row), min(len(self.items), last_row + 1)):
            self.thumbnail_provider.request(
                self.items[row],
                self.thumbnail_size,
                generation=self._generation,
            )

    def _on_thumbnail_ready(self, path: str, generation: int, qimage) -> None:
        if (
            self._shutdown_prepared
            or generation != self._generation
            or qimage is None
            or qimage.isNull()
        ):
            return
        self.item_model.set_thumbnail(
            path,
            PageThumbnailProvider.create_icon(qimage, self.thumbnail_size),
        )

    def _update_status(self, *, error: str | None = None) -> None:
        self._status_message_token += 1
        if error:
            self.statusBar().showMessage(error)
            return
        count = len(self.items)
        selected = self.item_model.item_at(self.list_view.currentIndex())
        folder = str(self.current_path) if self.current_path is not None else ""
        message = f"{folder} — {count}件"
        if selected is not None:
            message += f" — 選択: {selected.display_name}"
        self.statusBar().showMessage(message)

    def _show_temporary_status(self, message: str, timeout_ms: int = 3000) -> None:
        self._status_message_token += 1
        token = self._status_message_token
        self.statusBar().showMessage(message)

        def restore_status() -> None:
            if token == self._status_message_token:
                self._update_status()

        QTimer.singleShot(timeout_ms, restore_status)

    def _navigate_from_address_bar(self) -> None:
        raw_path = self.address_bar.text().strip()
        if len(raw_path) >= 2 and raw_path[0] == raw_path[-1] and raw_path[0] in {
            '"',
            "'",
        }:
            raw_path = raw_path[1:-1].strip()
        if not raw_path:
            self._sync_address_bar()
            return

        target = self._absolute_browser_path(raw_path)
        try:
            target_stat = target.stat()
        except FileNotFoundError:
            self._show_temporary_status("フォルダが見つかりません")
            self._sync_address_bar()
            return
        except OSError:
            self._show_temporary_status("フォルダへアクセスできません")
            self._sync_address_bar()
            return
        if stat.S_ISDIR(target_stat.st_mode):
            self.navigate_to(target)
            return
        if stat.S_ISREG(target_stat.st_mode):
            if target.suffix.lower() not in (
                BROWSER_IMAGE_EXTENSIONS | BROWSER_ARCHIVE_EXTENSIONS
            ):
                self._show_temporary_status(
                    "このファイル形式は表示できません"
                )
                self._sync_address_bar()
                return
            location = BrowserLocation(
                str(target.parent),
                selected_path=str(target),
            )
            if self.navigate_to(target.parent, restore_location=location):
                self._restore_location(location)
            return
        self._show_temporary_status("このファイル形式は表示できません")
        self._sync_address_bar()

    def _show_path_in_browser(self, path: str | Path) -> None:
        target = self._absolute_browser_path(path)
        if target.exists():
            self.select_path(target)
            return
        parent = target.parent
        if parent.is_dir():
            self.navigate_to(
                parent,
                restore_location=BrowserLocation(
                    str(parent),
                    selected_path=str(target),
                ),
            )
            return
        self._show_temporary_status("フォルダが見つかりません")

    def _current_location(self) -> BrowserLocation:
        selected = self.item_model.item_at(self.list_view.currentIndex())
        return BrowserLocation(
            path=str(self.current_path or ""),
            selected_path=str(selected.path) if selected is not None else None,
            vertical_scroll=self.list_view.verticalScrollBar().value(),
            horizontal_scroll=self.list_view.horizontalScrollBar().value(),
        )

    def _update_current_navigation_state(self) -> None:
        if self.current_path is None:
            return
        location = self._current_location()
        self.navigation_history.update_current_view_state(
            selected_path=location.selected_path,
            vertical_scroll=location.vertical_scroll,
            horizontal_scroll=location.horizontal_scroll,
        )

    def _schedule_location_restore(self, location: BrowserLocation) -> None:
        self._location_restore_token += 1
        token = self._location_restore_token
        self._restore_location(location)

        def restore_after_layout() -> None:
            if token != self._location_restore_token:
                return
            self._restore_location(location, update_status=False)

        QTimer.singleShot(0, restore_after_layout)

    def _restore_location(
        self,
        location: BrowserLocation,
        *,
        update_status: bool = True,
    ) -> None:
        if not self._same_path(self.current_path, Path(location.path)):
            return
        index = QModelIndex()
        if location.selected_path:
            row = self.item_model.row_for_path(location.selected_path)
            if row >= 0:
                index = self.item_model.index(row, 0)
        if index.isValid():
            self.list_view.setCurrentIndex(index)
            self.list_view.scrollTo(index, QListView.ScrollHint.EnsureVisible)
        else:
            self.list_view.clearSelection()
            self.list_view.setCurrentIndex(QModelIndex())

        vertical = self.list_view.verticalScrollBar()
        horizontal = self.list_view.horizontalScrollBar()
        if not index.isValid() or location.vertical_scroll > 0:
            vertical.setValue(
                max(
                    vertical.minimum(),
                    min(location.vertical_scroll, vertical.maximum()),
                )
            )
        if not index.isValid() or location.horizontal_scroll > 0:
            horizontal.setValue(
                max(
                    horizontal.minimum(),
                    min(location.horizontal_scroll, horizontal.maximum()),
                )
            )
        if update_status:
            self._update_status()

    def _sync_address_bar(self) -> None:
        self.address_bar.setText(
            str(self.current_path) if self.current_path is not None else ""
        )

    def _update_navigation_actions(self) -> None:
        self.back_action.setEnabled(self.navigation_history.can_go_back())
        self.forward_action.setEnabled(self.navigation_history.can_go_forward())
        can_go_up = False
        if self.current_path is not None:
            can_go_up = not self._same_path(
                self.current_path,
                self.current_path.parent,
            )
        self.up_action.setEnabled(can_go_up)
        self.refresh_action.setEnabled(self.current_path is not None)

    def _absolute_browser_path(self, path: str | Path) -> Path:
        target = Path(path).expanduser()
        if not target.is_absolute():
            base = self.current_path or Path.cwd()
            target = base / target
        try:
            return target.resolve()
        except OSError:
            return target.absolute()

    @staticmethod
    def _same_path(first: Path | None, second: Path | None) -> bool:
        if first is None or second is None:
            return first is second
        first_key = os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(first)))
        ).casefold()
        second_key = os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(second)))
        ).casefold()
        return first_key == second_key

    def _show_context_menu(self, position: QPoint) -> None:
        index = self.list_view.indexAt(position)
        item = self.item_model.item_at(index)
        if item is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction("開く")
        new_action = menu.addAction("新しいViewerWindowで開く")
        location_action = menu.addAction("エクスプローラーで場所を開く")
        bookmark_action = None
        if self.metadata_store is not None:
            menu.addSeparator()
            if self.metadata_store.is_browser_bookmarked(str(item.path)):
                bookmark_action = menu.addAction("ブックマークから削除")
            else:
                bookmark_action = menu.addAction("ブックマークに追加")
        if item.kind == BrowserItemKind.FOLDER:
            new_action.setEnabled(False)
        selected = menu.exec(self.list_view.viewport().mapToGlobal(position))
        if selected == open_action:
            self.open_item(index)
        elif selected == new_action:
            self.open_item(index, open_in_new_window=True)
        elif selected == location_action:
            target = item.path if item.kind == BrowserItemKind.FOLDER else item.path.parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        elif bookmark_action is not None and selected == bookmark_action:
            if self.metadata_store is None:
                return
            if self.metadata_store.is_browser_bookmarked(str(item.path)):
                self.remove_browser_bookmark(item.path)
            else:
                self.add_browser_bookmark(
                    item.path,
                    item_type=self._bookmark_item_type(item),
                    label=item.display_name,
                )

    def _show_bookmark_context_menu(self, position: QPoint) -> None:
        index = self.bookmark_view.indexAt(position)
        entry = self.bookmark_model.entry_at(index)
        if entry is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction("開く")
        new_action = menu.addAction("新しいViewerWindowで開く")
        if entry.item_type == "folder":
            new_action.setEnabled(False)
        menu.addSeparator()
        remove_action = menu.addAction("ブックマークから削除")
        selected = menu.exec(self.bookmark_view.viewport().mapToGlobal(position))
        if selected == open_action:
            self.open_bookmark(index)
        elif selected == new_action:
            self.open_bookmark(index, open_in_new_window=True)
        elif selected == remove_action:
            self.remove_browser_bookmark(entry.path)

    def _show_history_context_menu(self, position: QPoint) -> None:
        index = self.history_view.indexAt(position)
        entry = self.history_model.entry_at(index)
        if entry is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction("開く")
        new_action = menu.addAction("新しいViewerWindowで開く")
        location_action = menu.addAction("親フォルダを表示")
        menu.addSeparator()
        remove_action = menu.addAction("この履歴を削除")
        clear_action = menu.addAction("閲覧履歴をすべて消去...")
        selected = menu.exec(self.history_view.viewport().mapToGlobal(position))
        if selected == open_action:
            self.open_history(index)
        elif selected == new_action:
            self.open_history(index, open_in_new_window=True)
        elif selected == location_action:
            self.show_history_location(index)
        elif selected == remove_action:
            self.remove_history_entry(index)
        elif selected == clear_action:
            self.clear_history()

    @staticmethod
    def _bookmark_item_type(item: BrowserItem) -> str:
        if item.kind == BrowserItemKind.FOLDER:
            return "folder"
        if item.kind == BrowserItemKind.ARCHIVE:
            return "archive"
        return "image"

    @staticmethod
    def _safe_thumbnail_size(value: object) -> int:
        try:
            size = int(value)
        except (TypeError, ValueError):
            size = 180
        return max(80, min(500, size))

    @staticmethod
    def _safe_sidebar_width(value: object) -> int:
        try:
            width = int(value)
        except (TypeError, ValueError):
            width = 280
        return max(120, min(1200, width))
