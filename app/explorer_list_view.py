from __future__ import annotations

import logging

from PySide6.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QRect,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QFocusEvent,
    QKeyEvent,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QListView, QRubberBand, QTreeView

from .browser_pointer_controller import (
    BrowserPointerController,
    BrowserPointerState,
)
from .browser_wheel_scroll import BrowserWheelScrollAccumulator
from .drag_drop import (
    FileDragController,
    is_internal_path_mime,
    paths_from_mime_data,
)
from .folder_tree_pointer import FolderTreePointerController, FolderTreePointerState
from .mouse_gesture import MouseGestureRecognizer
from .gesture_trail import draw_gesture_trail


class ExplorerListView(QListView):
    """Path-based Explorer input with no standard drag/rubber-band overlap."""

    paths_dropped = Signal(object, object, object, object)
    enterActivated = Signal(QModelIndex)
    itemPressCaptured = Signal(str)
    itemReleaseConfirmed = Signal(str)
    paintCompleted = Signal()
    folderGestureRecognized = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.pointer_controller = BrowserPointerController()
        # Compatibility name used by BrowserWindow and older integrations.
        self.selection_controller = self.pointer_controller
        self._selection_anchor_path: str | None = None
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._drag_started = False
        self._drop_in_progress = False
        self._notify_after_next_paint = False
        self.browser_folder_gestures_enabled = True
        self.mouse_gesture_show_trail = True
        self.mouse_gesture_min_distance = 36
        self._folder_gesture_recognizer = MouseGestureRecognizer(
            max(
                self.mouse_gesture_min_distance,
                QApplication.startDragDistance(),
            ),
            axis_dominance_ratio=1.2,
        )
        self._folder_gesture_trail: list[QPoint] = []
        self._folder_gesture_right_button_down = False
        self._suppress_folder_gesture_context_menu = False
        self._wheel_scroll = BrowserWheelScrollAccumulator()
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        # All source drags are created by start_path_drag(). Qt's standard
        # startDrag and rubber-band paths stay disabled.
        self.setDragEnabled(False)
        self.setDragDropMode(QListView.DragDropMode.DropOnly)

    def set_wheel_scroll_policy(
        self,
        mode: object,
        custom_rows: object,
    ) -> None:
        self._wheel_scroll.configure(mode, custom_rows)

    @property
    def wheel_scroll_mode(self) -> str:
        return self._wheel_scroll.mode

    @property
    def wheel_scroll_custom_rows(self) -> int:
        return self._wheel_scroll.custom_rows

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        # Pixel deltas are already device/gesture-native. Keep Qt's smooth
        # touchpad path intact and customize only vertical angle-wheel input.
        if (
            self._wheel_scroll.mode == "system"
            or not event.pixelDelta().isNull()
            or event.angleDelta().y() == 0
            or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ):
            super().wheelEvent(event)
            return

        movement = self._wheel_scroll.consume_angle_delta(
            event.angleDelta().y(),
            self.gridSize().height(),
        )
        if movement:
            scrollbar = self.verticalScrollBar()
            scrollbar.setValue(scrollbar.value() + movement)
        event.accept()

    def ensure_viewport_drop_target(self) -> None:
        """Reapply drop routing after QListView replaces/configures its viewport."""
        self.viewport().setAcceptDrops(True)

    def notify_after_next_paint(self) -> None:
        self._notify_after_next_paint = True
        self.viewport().update()

    @property
    def drag_started(self) -> bool:
        return self._drag_started

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if (
            event.button() == Qt.MouseButton.RightButton
            and self.browser_folder_gestures_enabled
        ):
            point = event.position().toPoint()
            self._suppress_folder_gesture_context_menu = False
            self._folder_gesture_right_button_down = True
            self._folder_gesture_recognizer.begin((point.x(), point.y()))
            self._folder_gesture_trail = (
                [point] if self.mouse_gesture_show_trail else []
            )
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        index = self.indexAt(event.position().toPoint())
        path = self._path_for_index(index)
        selected_paths = self._selected_paths()
        current_path = self._path_for_index(self.currentIndex())
        self.pointer_controller.begin(
            position=event.position().toPoint(),
            global_position=event.globalPosition().toPoint(),
            modifiers=event.modifiers(),
            row=index.row() if index.isValid() else -1,
            path=path,
            was_selected=path is not None and path in selected_paths,
            selection_snapshot=selected_paths,
            current_path=current_path,
            anchor_path=self._selection_anchor_path,
        )
        self._drag_started = False
        self._hide_rubber_band()
        if path is not None:
            self.itemPressCaptured.emit(path)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if (
            self._folder_gesture_right_button_down
            and self._folder_gesture_recognizer.active
            and event.buttons() & Qt.MouseButton.RightButton
        ):
            point = event.position().toPoint()
            self._folder_gesture_recognizer.update((point.x(), point.y()))
            if self.mouse_gesture_show_trail:
                self._folder_gesture_trail.append(point)
                self.viewport().update()
            event.accept()
            return
        if not event.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return
        position = event.position().toPoint()
        if not self.pointer_controller.exceeded_drag_distance(
            position,
            QApplication.startDragDistance(),
        ):
            event.accept()
            return
        if self.pointer_controller.begin_file_drag():
            self._drag_started = True
            paths = self._drag_paths_for_press()
            self.start_path_drag(paths)
            # QDrag owns the release event while exec() is running. Keep the
            # sequence cancelled so a late release can never become a click.
            self.pointer_controller.cancel()
            return
        if self.pointer_controller.begin_rubber_band():
            self._rubber_band.show()
        if (
            self.pointer_controller.state
            is BrowserPointerState.RUBBER_BAND_SELECTING
        ):
            self._update_rubber_band(position)
            event.accept()
            return
        if (
            self.pointer_controller.state
            is BrowserPointerState.PRESSED_ON_EMPTY
        ):
            # A plain blank drag is neither selection nor file drag.
            self.pointer_controller.cancel()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if (
            event.button() == Qt.MouseButton.RightButton
            and self._folder_gesture_right_button_down
        ):
            point = event.position().toPoint()
            pattern = self._folder_gesture_recognizer.finish(
                (point.x(), point.y())
            )
            self._folder_gesture_right_button_down = False
            self._folder_gesture_trail.clear()
            self.viewport().update()
            if pattern:
                self._suppress_folder_gesture_context_menu = True
                self.folderGestureRecognized.emit(pattern)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        state = self.pointer_controller.state
        press = self.pointer_controller.press
        release_index = self.indexAt(event.position().toPoint())
        release_path = self._path_for_index(release_index)
        if state is BrowserPointerState.PRESSED_ON_ITEM and press is not None:
            if press.path == release_path:
                current_index = self._index_for_path(press.path)
                if current_index.isValid():
                    self._commit_item_click(current_index, press.modifiers)
                    self.itemReleaseConfirmed.emit(press.path)
                    self.clicked.emit(current_index)
        elif state is BrowserPointerState.PRESSED_ON_EMPTY and press is not None:
            if release_path is None:
                self.clearSelection()
                self.setCurrentIndex(QModelIndex())
                self._selection_anchor_path = None
        elif state is BrowserPointerState.RUBBER_BAND_SELECTING:
            self._update_rubber_band(event.position().toPoint())
        self._hide_rubber_band()
        self.pointer_controller.reset()
        self._drag_started = False
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        if self.pointer_controller.state in {
            BrowserPointerState.FILE_DRAGGING,
            BrowserPointerState.RUBBER_BAND_SELECTING,
            BrowserPointerState.CANCELLED,
        }:
            self.pointer_controller.cancel()
            event.accept()
            return
        index = self.indexAt(event.position().toPoint())
        path = self._path_for_index(index)
        current = self._index_for_path(path)
        if current.isValid():
            self._select_only(current)
            self.doubleClicked.emit(current)
            self.activated.emit(current)
        self.pointer_controller.cancel()
        event.accept()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # type: ignore[override]
        self._cancel_folder_gesture()
        self._hide_rubber_band()
        self.pointer_controller.reset()
        self._drag_started = False
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if (
            event.key() == Qt.Key.Key_Escape
            and self._folder_gesture_recognizer.active
        ):
            self._cancel_folder_gesture()
            event.accept()
            return
        if (
            event.key() == Qt.Key.Key_Escape
            and self.pointer_controller.state is not BrowserPointerState.IDLE
        ):
            self._hide_rubber_band()
            self.pointer_controller.cancel()
            event.accept()
            return
        if (
            event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and self.currentIndex().isValid()
        ):
            self.enterActivated.emit(self.currentIndex())
            self.activated.emit(self.currentIndex())
            event.accept()
            return
        super().keyPressEvent(event)

    def startDrag(self, _supported_actions) -> None:  # noqa: N802
        """Disable QListView's competing source-drag implementation."""

    def start_path_drag(self, paths: tuple[str, ...] | None = None) -> bool:
        current_paths = paths if paths is not None else self._selected_paths()
        if not current_paths:
            return False
        return self._execute_path_drag(current_paths)

    def _execute_path_drag(self, paths: tuple[str, ...]) -> bool:
        drag = QDrag(self)
        drag.setMimeData(FileDragController.mime_data(paths))
        drag.exec(
            Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
            Qt.DropAction.CopyAction,
        )
        return True

    def _commit_item_click(
        self,
        index: QModelIndex,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            selection = self.selectionModel()
            flag = (
                QItemSelectionModel.SelectionFlag.Deselect
                if selection.isSelected(index)
                else QItemSelectionModel.SelectionFlag.Select
            )
            selection.select(index, flag)
            selection.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
            self._selection_anchor_path = self._path_for_index(index)
            return
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            self._select_range_to(index)
            return
        self._select_only(index)
        self._selection_anchor_path = self._path_for_index(index)

    def _select_only(self, index: QModelIndex) -> None:
        selection = self.selectionModel()
        selection.select(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        selection.setCurrentIndex(
            index,
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )

    def _select_range_to(self, index: QModelIndex) -> None:
        anchor = self._index_for_path(self._selection_anchor_path)
        if not anchor.isValid():
            anchor = self.currentIndex() if self.currentIndex().isValid() else index
        first, last = sorted((anchor.row(), index.row()))
        selection = QItemSelection(
            self.model().index(first, 0),
            self.model().index(last, 0),
        )
        selection_model = self.selectionModel()
        selection_model.select(
            selection,
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        selection_model.setCurrentIndex(
            index,
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )
        if self._selection_anchor_path is None:
            self._selection_anchor_path = self._path_for_index(anchor)

    def _drag_paths_for_press(self) -> tuple[str, ...]:
        press = self.pointer_controller.press
        if press is None or press.path is None:
            return ()
        candidates = (
            press.selection_snapshot if press.was_selected else (press.path,)
        )
        resolved: list[tuple[int, str]] = []
        for path in candidates:
            index = self._index_for_path(path)
            if index.isValid():
                resolved.append((index.row(), path))
        resolved.sort()
        paths = tuple(path for _row, path in resolved)
        pressed_index = self._index_for_path(press.path)
        if not press.was_selected and pressed_index.isValid():
            self._select_only(pressed_index)
            self._selection_anchor_path = press.path
        return paths

    def _update_rubber_band(self, position: QPoint) -> None:
        press = self.pointer_controller.press
        model = self.model()
        if press is None or model is None:
            return
        rectangle = QRect(press.position, position).normalized()
        self._rubber_band.setGeometry(rectangle)
        selection = self.selectionModel()
        selection.clearSelection()
        for path in press.selection_snapshot:
            index = self._index_for_path(path)
            if index.isValid():
                selection.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select,
                )
        for row in self._rubber_band_candidate_rows(rectangle):
            index = model.index(row, 0)
            if self.visualRect(index).intersects(rectangle):
                selection.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select,
                )

    def _rubber_band_candidate_rows(self, rectangle: QRect) -> range:
        model = self.model()
        if model is None:
            return range(0)
        row_count = model.rowCount()
        grid = self.gridSize()
        if (
            self.viewMode() is not QListView.ViewMode.IconMode
            or self.flow() is not QListView.Flow.LeftToRight
            or grid.width() <= 0
            or grid.height() <= 0
        ):
            return range(row_count)
        columns = max(1, self.viewport().width() // grid.width())
        content_top = rectangle.top() + self.verticalScrollBar().value()
        content_bottom = rectangle.bottom() + self.verticalScrollBar().value()
        first_grid_row = max(0, content_top // grid.height() - 1)
        last_grid_row = max(first_grid_row, content_bottom // grid.height() + 1)
        first = min(row_count, first_grid_row * columns)
        last = min(row_count, (last_grid_row + 1) * columns)
        return range(first, last)

    def _hide_rubber_band(self) -> None:
        self._rubber_band.hide()
        self._rubber_band.setGeometry(QRect())

    def _selected_paths(self) -> tuple[str, ...]:
        output: list[tuple[int, str]] = []
        for index in self.selectedIndexes():
            path = self._path_for_index(index)
            if path is not None:
                output.append((index.row(), path))
        output.sort()
        return tuple(path for _row, path in output)

    def _path_for_index(self, index: QModelIndex) -> str | None:
        model = self.model()
        if model is None or not index.isValid():
            return None
        value = index.data(getattr(model, "PathRole", Qt.ItemDataRole.UserRole))
        return str(value) if value else None

    def _index_for_path(self, path: str | None) -> QModelIndex:
        model = self.model()
        if model is None or not path:
            return QModelIndex()
        row_for_path = getattr(model, "row_for_path", None)
        if callable(row_for_path):
            row = int(row_for_path(path))
            return model.index(row, 0) if row >= 0 else QModelIndex()
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            if self._path_for_index(index) == path:
                return index
        return QModelIndex()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if paths_from_mime_data(event.mimeData()):
            self._drop_in_progress = True
            self._debug_drop("dragEnter", event, accepted=True)
            event.acceptProposedAction()
            return
        self._debug_drop("dragEnter", event, accepted=False)
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # type: ignore[override]
        self._drop_in_progress = False
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        paths = paths_from_mime_data(event.mimeData())
        self._drop_in_progress = False
        if not paths:
            self._debug_drop("drop", event, accepted=False)
            event.ignore()
            return
        index: QModelIndex = self.indexAt(event.position().toPoint())
        source = event.source()
        internal = bool(
            is_internal_path_mime(event.mimeData())
            and source in {self, self.viewport()}
        )
        self._debug_drop(
            "drop",
            event,
            accepted=True,
            internal=internal,
            path_count=len(paths),
        )
        self.paths_dropped.emit(
            paths,
            index,
            event.modifiers(),
            self if internal else None,
        )
        event.acceptProposedAction()

    @property
    def drop_in_progress(self) -> bool:
        return self._drop_in_progress

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if (
            self.mouse_gesture_show_trail
            and self.browser_folder_gestures_enabled
            and self._folder_gesture_right_button_down
            and self._folder_gesture_recognizer.pattern
            and len(self._folder_gesture_trail) >= 2
        ):
            painter = QPainter(self.viewport())
            draw_gesture_trail(painter, self._folder_gesture_trail)
        if self._notify_after_next_paint:
            self._notify_after_next_paint = False
            self.paintCompleted.emit()

    def set_folder_gesture_options(
        self,
        *,
        enabled: bool,
        show_trail: bool,
        min_distance: int,
    ) -> None:
        policy = (bool(enabled), max(12, min(200, int(min_distance))))
        changed = policy != (self.browser_folder_gestures_enabled, self.mouse_gesture_min_distance)
        if changed:
            self._cancel_folder_gesture()
        self.browser_folder_gestures_enabled = bool(enabled)
        self.mouse_gesture_show_trail = bool(show_trail)
        self.mouse_gesture_min_distance = max(
            12,
            min(200, int(min_distance)),
        )
        if changed:
            self._folder_gesture_recognizer = MouseGestureRecognizer(
                max(self.mouse_gesture_min_distance, QApplication.startDragDistance()),
                axis_dominance_ratio=1.2,
            )
        if not self.mouse_gesture_show_trail:
            self._folder_gesture_trail.clear()
        self.viewport().update()

    @property
    def folder_gesture_in_progress(self) -> bool:
        return (
            self._folder_gesture_right_button_down
            and bool(self._folder_gesture_recognizer.pattern)
        )

    @property
    def folder_gesture_trail(self) -> tuple[QPoint, ...]:
        return tuple(self._folder_gesture_trail)

    def consume_folder_gesture_context_menu_suppression(self) -> bool:
        suppressed = self._suppress_folder_gesture_context_menu
        self._suppress_folder_gesture_context_menu = False
        return suppressed

    def _cancel_folder_gesture(self) -> None:
        changed = (
            self._folder_gesture_right_button_down
            or self._folder_gesture_recognizer.active
            or bool(self._folder_gesture_trail)
        )
        self._folder_gesture_right_button_down = False
        self._folder_gesture_recognizer.cancel()
        self._folder_gesture_trail.clear()
        if changed:
            self.viewport().update()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        return super().eventFilter(watched, event)

    def viewportEvent(self, event) -> bool:  # type: ignore[override]
        event_type = event.type()
        if event_type == QEvent.Type.DragEnter:
            self.dragEnterEvent(event)
            return event.isAccepted()
        if event_type == QEvent.Type.DragMove:
            self.dragMoveEvent(event)
            return event.isAccepted()
        if event_type == QEvent.Type.DragLeave:
            self.dragLeaveEvent(event)
            return True
        if event_type == QEvent.Type.Drop:
            self.dropEvent(event)
            return event.isAccepted()
        return super().viewportEvent(event)

    @staticmethod
    def _debug_drop(
        name: str,
        event,
        *,
        accepted: bool,
        internal: bool | None = None,
        path_count: int | None = None,
    ) -> None:
        logger = logging.getLogger("nivisviewer.browser_drop")
        if not logger.isEnabledFor(logging.DEBUG):
            return
        mime = event.mimeData()
        logger.debug(
            "%s receiver=ExplorerListView.viewport formats=%s internal=%s "
            "paths=%s position=%s accepted=%s",
            name,
            tuple(mime.formats()),
            internal,
            path_count,
            event.position().toPoint(),
            accepted,
        )


class PathDropTreeView(QTreeView):
    paths_dropped = Signal(object, object, object, object)
    navigationConfirmed = Signal(QModelIndex)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.pointer_controller = FolderTreePointerController()
        self._drop_hover_index = QModelIndex()
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setAutoExpandDelay(-1)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        index = self.indexAt(event.position().toPoint())
        path = self._path_for_index(index)
        self.pointer_controller.begin(
            path,
            event.position().toPoint(),
            disclosure=self._is_disclosure_position(index, event.position().toPoint()),
        )
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.pointer_controller.moved(
                event.position().toPoint(),
                QApplication.startDragDistance(),
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        index = self.indexAt(event.position().toPoint())
        path = self._path_for_index(index)
        press = self.pointer_controller.press
        if (
            press is not None
            and press.disclosure
            and press.path == path
            and self.pointer_controller.state is FolderTreePointerState.PRESSED
            and (event.position().toPoint() - press.position).manhattanLength()
            < QApplication.startDragDistance()
        ):
            if index.isValid():
                self.setExpanded(index, not self.isExpanded(index))
            self.pointer_controller.reset()
            event.accept()
            return
        confirmed = self.pointer_controller.confirm_release(
            path,
            event.position().toPoint(),
            QApplication.startDragDistance(),
        )
        if confirmed is not None:
            resolved = self._index_for_path(confirmed)
            if resolved.isValid():
                self.setCurrentIndex(resolved)
                self.clicked.emit(resolved)
                self.navigationConfirmed.emit(resolved)
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if (
            event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and self.currentIndex().isValid()
        ):
            self.navigationConfirmed.emit(self.currentIndex())
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # type: ignore[override]
        self.pointer_controller.reset()
        super().focusOutEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if paths_from_mime_data(event.mimeData()):
            self._drop_hover_index = self.indexAt(event.position().toPoint())
            self.viewport().update()
            event.acceptProposedAction()
        else:
            self._clear_drop_hover()
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # type: ignore[override]
        self._clear_drop_hover()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        paths = paths_from_mime_data(event.mimeData())
        index = self.indexAt(event.position().toPoint())
        self._clear_drop_hover()
        if not paths or not index.isValid():
            event.ignore()
            return
        self.paths_dropped.emit(paths, index, event.modifiers(), event.source())
        event.acceptProposedAction()

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if not self._drop_hover_index.isValid():
            return
        rectangle = self.visualRect(self._drop_hover_index).adjusted(1, 1, -2, -2)
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor("#2b78d0"), 2))
        painter.setBrush(QColor(43, 120, 208, 36))
        painter.drawRect(rectangle)

    def set_programmatic_sync(self, applying: bool) -> None:
        if applying:
            self.pointer_controller.begin_programmatic_sync()
        else:
            self.pointer_controller.end_programmatic_sync()

    def _clear_drop_hover(self) -> None:
        if self._drop_hover_index.isValid():
            self._drop_hover_index = QModelIndex()
            self.viewport().update()

    def _path_for_index(self, index: QModelIndex) -> str:
        model = self.model()
        file_path = getattr(model, "filePath", None)
        if model is None or not index.isValid() or not callable(file_path):
            return ""
        return str(file_path(index))

    def _index_for_path(self, path: str) -> QModelIndex:
        model = self.model()
        index_for_path = getattr(model, "index", None)
        if model is None or not callable(index_for_path):
            return QModelIndex()
        return index_for_path(path)

    def _is_disclosure_position(self, index: QModelIndex, position: QPoint) -> bool:
        if not index.isValid() or not self.model().hasChildren(index):
            return False
        item_rect = self.visualRect(index)
        branch_right = item_rect.left()
        branch_left = max(0, branch_right - self.indentation())
        return branch_left <= position.x() < branch_right
