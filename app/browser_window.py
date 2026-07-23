from __future__ import annotations

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
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QResizeEvent
from PySide6.QtWidgets import (
    QFileSystemModel,
    QListView,
    QMainWindow,
    QMenu,
    QSplitter,
    QStyle,
    QTabWidget,
    QTreeView,
    QWidget,
)

from .browser_model import (
    BrowserItem,
    BrowserItemDiscovery,
    BrowserItemKind,
    BrowserItemModel,
)
from .config_manager import ConfigManager
from .thumbnail_provider import BrowserThumbnailProvider, PageThumbnailProvider


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
    ) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("NivisViewer - ブラウザ")
        self.resize(1100, 760)

        self.config = config_manager
        self.settings = config_manager.data
        self._open_path_handler = open_path_handler
        self.discovery = discovery or BrowserItemDiscovery()
        self.thumbnail_provider = thumbnail_provider or BrowserThumbnailProvider(self)
        self.thumbnail_provider.thumbnail_ready.connect(self._on_thumbnail_ready)
        self.current_path: Path | None = None
        self._generation = self.thumbnail_provider.generation
        self._syncing_tree = False
        self._pending_tree_path: Path | None = None
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
        self._restore_window_state()
        self._restore_initial_folder()

    @property
    def items(self) -> tuple[BrowserItem, ...]:
        return self.item_model.items

    def show_initial(self) -> None:
        self.show()

    def set_current_folder(self, folder: str | Path) -> bool:
        target = Path(folder).expanduser()
        if not target.is_dir():
            self.statusBar().showMessage(f"フォルダが見つかりません: {target}")
            return False

        result = self.discovery.discover(target)
        self.current_path = result.folder
        self.config.set("last_browser_path", str(result.folder))
        self._generation = self.thumbnail_provider.begin_generation()
        self.item_model.set_items(result.items)
        self.list_view.clearSelection()
        self._sync_tree_to_path(result.folder)
        self._update_status(error=result.error)
        QTimer.singleShot(0, self._request_visible_thumbnails)
        return result.error is None

    def select_path(self, path: str | Path) -> None:
        target = Path(path).expanduser()
        if target.is_file():
            folder = target.parent
            selected = target
        elif target.is_dir():
            parent = target.parent
            folder = parent if parent != target else target
            selected = target
        else:
            self.statusBar().showMessage(f"項目が見つかりません: {target}")
            return

        if not self.set_current_folder(folder):
            return
        row = self.item_model.row_for_path(selected)
        if row < 0 and target.is_dir() and self.set_current_folder(target):
            return
        if row >= 0:
            index = self.item_model.index(row, 0)
            self.list_view.setCurrentIndex(index)
            self.list_view.scrollTo(index, QListView.ScrollHint.PositionAtCenter)
            self._update_status()

    def open_item(self, index: QModelIndex, *, open_in_new_window: bool = False) -> None:
        item = self.item_model.item_at(index)
        if item is None:
            return
        if item.kind == BrowserItemKind.FOLDER:
            self.set_current_folder(item.path)
            return
        if self._open_path_handler is not None:
            self._open_path_handler(str(item.path), open_in_new_window)

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

    def event(self, event: QEvent) -> bool:  # type: ignore[override]
        handled = super().event(event)
        if event.type() == QEvent.Type.WindowActivate:
            self.activated.emit(self)
        return handled

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

        view_menu = self.menuBar().addMenu("表示")
        self.sidebar_action = QAction("サイドバーを表示", self)
        self.sidebar_action.setCheckable(True)
        self.sidebar_action.setChecked(
            bool(self.settings.get("browser_sidebar_visible", True))
        )
        self.sidebar_action.toggled.connect(self.set_sidebar_visible)
        view_menu.addAction(self.sidebar_action)

        self.statusBar().showMessage("フォルダを選択してください。")
        self.splitter.setSizes([self._sidebar_width, max(1, self.width() - self._sidebar_width)])
        self.set_sidebar_visible(self.sidebar_action.isChecked())

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
        self._pending_tree_path = path
        self._folder_change_timer.start()

    def _apply_pending_tree_path(self) -> None:
        path = self._pending_tree_path
        self._pending_tree_path = None
        if path is not None:
            self.set_current_folder(path)

    def _sync_tree_to_path(self, path: Path) -> None:
        index = self.file_system_model.index(str(path))
        if not index.isValid():
            self._pending_tree_path = path
            return
        if self._pending_tree_path == path:
            self._pending_tree_path = None
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
        pending = self._pending_tree_path
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

    def _show_context_menu(self, position: QPoint) -> None:
        index = self.list_view.indexAt(position)
        item = self.item_model.item_at(index)
        if item is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction("開く")
        new_action = menu.addAction("新しいViewerWindowで開く")
        location_action = menu.addAction("エクスプローラーで場所を開く")
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
