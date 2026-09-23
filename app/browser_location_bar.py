"""Modern, filesystem-only Browser location breadcrumb controls."""

from __future__ import annotations

from .i18n import tr


import os
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from natsort import natsort_keygen, ns
from PySide6.QtCore import (
    QDir,
    QEvent,
    QObject,
    QPoint,
    QRunnable,
    QSize,
    QThreadPool,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QFont, QGuiApplication, QHideEvent, QKeyEvent, QPainter, QPalette, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .browser_visibility import BrowserVisibilityPolicy, filesystem_visibility_flags


_natural_key = natsort_keygen(alg=ns.IGNORECASE)


@dataclass(frozen=True)
class LocationSegment:
    label: str
    path: str


@dataclass(frozen=True)
class LocationDirectoryResult:
    generation: int
    parent_path: str
    directories: tuple[LocationSegment, ...]
    error: str | None = None


@dataclass(frozen=True)
class LocationPopupEntry:
    label: str
    value: object
    tool_tip: str = ""
    enabled: bool = True
    current: bool = False


class _CompactPopupItemDelegate(QStyledItemDelegate):
    """Keep native item painting while overriding oversized row metrics."""

    def __init__(
        self,
        view: QListWidget,
        native_row_ceiling: int,
    ) -> None:
        super().__init__(view)
        self._native_row_ceiling = max(1, int(native_row_ceiling))

    def set_native_row_ceiling(self, height: int) -> None:
        self._native_row_ceiling = max(1, int(height))

    @staticmethod
    def _is_separator(index) -> bool:
        entry = index.data(Qt.ItemDataRole.UserRole)
        return (
            isinstance(entry, LocationPopupEntry)
            and not entry.enabled
            and entry.value is None
        )

    def sizeHint(self, option, index):  # noqa: N802
        native_hint = super().sizeHint(option, index)
        font_height = max(1, int(option.fontMetrics.height()))
        if self._is_separator(index):
            return QSize(int(native_hint.width()), max(4, font_height // 2))
        # The history popup is intentionally denser than a native menu row.
        # The font's own ascent/descent already provides the necessary space.
        compact_target = font_height
        height = min(
            int(native_hint.height()),
            self._native_row_ceiling,
            compact_target,
        )
        return QSize(int(native_hint.width()), max(1, height))

    def paint(self, painter: QPainter, option, index) -> None:
        if not self._is_separator(index):
            super().paint(painter, option, index)
            return
        painter.save()
        # The separator item is disabled; its current palette group would
        # make the rule almost invisible on the native Windows style.
        line_color = option.palette.color(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Text,
        )
        line_color.setAlpha(200)
        painter.setPen(line_color)
        y = option.rect.center().y()
        painter.drawLine(option.rect.left() + 8, y, option.rect.right() - 8, y)
        painter.restore()


class BrowserLocationListPopup(QFrame):
    """Finite-height, non-modal projection for location and history entries."""

    entryActivated = Signal(object)
    closed = Signal(object)
    DEFAULT_VISIBLE_ROWS = 14

    def __init__(
        self,
        entries: tuple[LocationPopupEntry, ...],
        parent: QWidget,
        *,
        maximum_visible_rows: int = DEFAULT_VISIBLE_ROWS,
        compact_rows: bool = False,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("browser_location_list_popup")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.maximum_visible_rows = max(1, int(maximum_visible_rows))
        self.compact_rows = bool(compact_rows)
        self._closed_emitted = False
        self.list_widget = QListWidget(self)
        self._native_item_delegate = self.list_widget.itemDelegate()
        self._compact_item_delegate: _CompactPopupItemDelegate | None = None
        self.list_widget.setObjectName("browser_location_popup_list")
        self.list_widget.setUniformItemSizes(not self.compact_rows)
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.list_widget.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.list_widget.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.list_widget.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.list_widget.installEventFilter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self.list_widget)

        selected_row = -1
        for row, entry in enumerate(entries):
            item = QListWidgetItem(entry.label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(entry.tool_tip)
            if not entry.enabled:
                item.setFlags(
                    item.flags()
                    & ~Qt.ItemFlag.ItemIsEnabled
                    & ~Qt.ItemFlag.ItemIsSelectable
                )
            if entry.current:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
                selected_row = row
            self.list_widget.addItem(item)
        if selected_row < 0:
            selected_row = self._first_enabled_row()
        if selected_row >= 0:
            self.list_widget.setCurrentRow(selected_row)

        self.list_widget.itemClicked.connect(self._activate_item)
        self.list_widget.itemActivated.connect(self._activate_item)

    @property
    def entry_count(self) -> int:
        return self.list_widget.count()

    def show_for(self, anchor: QWidget) -> None:
        self._show_at(
            anchor.mapToGlobal(anchor.rect().bottomLeft()),
            anchor.mapToGlobal(anchor.rect().topLeft()).y(),
            anchor.screen(),
        )

    def show_at(self, position: QPoint) -> None:
        screen = QGuiApplication.screenAt(position)
        self._show_at(position, position.y(), screen)

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if watched is self.list_widget and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if isinstance(key_event, QKeyEvent):
                if key_event.key() == Qt.Key.Key_Escape:
                    self.close()
                    return True
                if key_event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                    item = self.list_widget.currentItem()
                    if item is not None:
                        self._activate_item(item)
                    return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        super().hideEvent(event)
        if not self._closed_emitted:
            self._closed_emitted = True
            self.closed.emit(self)

    def _activate_item(self, item: QListWidgetItem) -> None:
        if not bool(item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, LocationPopupEntry):
            return
        self.entryActivated.emit(entry)
        self.close()

    def _first_enabled_row(self) -> int:
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if bool(item.flags() & Qt.ItemFlag.ItemIsEnabled):
                return row
        return -1

    def _configure_compact_item_delegate(self) -> None:
        # Measure the actual native delegate on each show, then cap the
        # compact hint to it.  This responds to font/style/DPI changes and
        # avoids the platform style's oversized content minimum.
        self.list_widget.setItemDelegate(self._native_item_delegate)
        native_row_height = min(
            (
                self.list_widget.sizeHintForRow(row)
                for row in range(self.entry_count)
            ),
            default=self.list_widget.fontMetrics().height(),
        )
        if self._compact_item_delegate is None:
            self._compact_item_delegate = _CompactPopupItemDelegate(
                self.list_widget,
                native_row_height,
            )
        else:
            self._compact_item_delegate.set_native_row_ceiling(
                native_row_height
            )
        self.list_widget.setItemDelegate(self._compact_item_delegate)

    def _show_at(self, position: QPoint, anchor_top: int, screen) -> None:
        self.ensurePolished()
        if self.compact_rows:
            self.list_widget.ensurePolished()
            self._configure_compact_item_delegate()
        else:
            row_height = max(
                self.fontMetrics().height() + 8,
                self.list_widget.sizeHintForRow(0),
            )
        visible_rows = max(
            1,
            min(self.entry_count, self.maximum_visible_rows),
        )
        scrollbar_width = (
            self.list_widget.verticalScrollBar().sizeHint().width()
            if self.entry_count > self.maximum_visible_rows
            else 0
        )
        text_width = max(
            (
                self.fontMetrics().horizontalAdvance(
                    self.list_widget.item(row).text()
                )
                for row in range(self.entry_count)
            ),
            default=160,
        )
        width = max(220, min(520, text_width + scrollbar_width + 36))
        if self.compact_rows:
            margins = self.layout().contentsMargins()
            vertical_chrome = (
                2 * self.frameWidth()
                + 2 * self.list_widget.frameWidth()
                + margins.top()
                + margins.bottom()
            )
        else:
            vertical_chrome = 2 * self.frameWidth() + 4
        if self.compact_rows:
            content_height = sum(
                self.list_widget.sizeHintForRow(row)
                for row in range(visible_rows)
            )
        else:
            content_height = visible_rows * row_height
        height = content_height + vertical_chrome
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, available.width())
            height = min(height, available.height())
            x = max(
                available.left(),
                min(position.x(), available.right() - width + 1),
            )
            y = position.y()
            if y + height > available.bottom() + 1:
                y = max(available.top(), anchor_top - height)
            position = QPoint(x, y)
        self.setGeometry(position.x(), position.y(), width, height)
        self.show()
        self.raise_()
        self.list_widget.setFocus(Qt.FocusReason.PopupFocusReason)


def split_location_segments(path: str | Path) -> tuple[LocationSegment, ...]:
    """Split a Windows path while retaining an absolute path per component."""

    absolute = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
    parts = absolute.parts
    if not parts:
        return ()
    result: list[LocationSegment] = []
    current = Path(parts[0])
    root_label = parts[0].rstrip("\\/") or parts[0]
    result.append(LocationSegment(root_label, str(current)))
    for part in parts[1:]:
        current /= part
        result.append(LocationSegment(part, str(current)))
    return tuple(result)


def list_child_directories(
    parent_path: str | Path,
    visibility_policy: BrowserVisibilityPolicy,
    *,
    cancelled: Event | None = None,
) -> tuple[LocationSegment, ...]:
    """Enumerate only visible child directories without decoding Browser items."""

    parent = Path(parent_path)
    directories: list[LocationSegment] = []
    with os.scandir(parent) as entries:
        for entry in entries:
            if cancelled is not None and cancelled.is_set():
                return ()
            if entry.name in {".", ".."} or entry.name.startswith("~$"):
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            attributes = getattr(entry_stat, "st_file_attributes", 0)
            hidden, system = filesystem_visibility_flags(entry.name, attributes)
            if not visibility_policy.allows(
                hidden=hidden,
                system=system,
                supported=True,
                is_directory=True,
            ):
                continue
            directories.append(
                LocationSegment(
                    entry.name,
                    str(Path(entry.path).absolute()),
                )
            )
    directories.sort(key=lambda item: (_natural_key(item.label), item.path.casefold()))
    return tuple(directories)


class _DirectoryWorkerSignals(QObject):
    completed = Signal(object)


class _DirectoryWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        parent_path: str,
        visibility_policy: BrowserVisibilityPolicy,
        cancelled: Event,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.parent_path = parent_path
        self.visibility_policy = visibility_policy
        self.cancelled = cancelled
        self.signals = _DirectoryWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            directories = list_child_directories(
                self.parent_path,
                self.visibility_policy,
                cancelled=self.cancelled,
            )
            result = LocationDirectoryResult(
                self.generation,
                self.parent_path,
                directories,
            )
        except OSError as exc:
            result = LocationDirectoryResult(
                self.generation,
                self.parent_path,
                (),
                str(exc),
            )
        self.signals.completed.emit(result)


class LocationDirectoryLoader(QObject):
    """Latest-request worker facade for breadcrumb separator menus."""

    completed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._generation = 0
        self._cancelled = Event()
        self._workers: set[_DirectoryWorker] = set()

    def request(
        self,
        parent_path: str | Path,
        visibility_policy: BrowserVisibilityPolicy,
    ) -> int:
        self._cancelled.set()
        self._generation += 1
        cancelled = Event()
        self._cancelled = cancelled
        worker = _DirectoryWorker(
            self._generation,
            str(parent_path),
            visibility_policy,
            cancelled,
        )
        self._workers.add(worker)

        def completed(result: LocationDirectoryResult) -> None:
            self._workers.discard(worker)
            if result.generation == self._generation and not cancelled.is_set():
                self.completed.emit(result)

        worker.signals.completed.connect(completed)
        QThreadPool.globalInstance().start(worker)
        return self._generation

    def close(self) -> None:
        self.cancel()

    def cancel(self) -> int:
        self._cancelled.set()
        self._generation += 1
        return self._generation


class BrowserLocationBreadcrumb(QWidget):
    """Compact breadcrumb that keeps the current and nearest ancestors visible."""

    locationActivated = Signal(str)
    childrenRequested = Signal(str)
    editRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("browser_location_breadcrumb")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(140)
        self._segments: tuple[LocationSegment, ...] = ()
        self._separator_buttons: dict[str, QToolButton] = {}
        self._last_width = -1
        self._drive_popup: BrowserLocationListPopup | None = None
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 0, 2, 0)
        self._layout.setSpacing(0)

    @property
    def segments(self) -> tuple[LocationSegment, ...]:
        return self._segments

    def set_location(self, path: str | Path | None) -> None:
        segments = split_location_segments(path) if path else ()
        if segments == self._segments:
            return
        self._segments = segments
        self._rebuild()

    def separator_button_for_path(self, path: str | Path) -> QToolButton | None:
        key = os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))
        button = self._separator_buttons.get(key.casefold())
        if button is None or not button.isVisibleTo(self):
            return None
        return button

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if abs(event.size().width() - self._last_width) >= 8:
            self._rebuild()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.editRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.childAt(event.position().toPoint()) is None
        ):
            self.editRequested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def _clear_layout(self) -> None:
        if self._drive_popup is not None:
            self._drive_popup.close()
        self._separator_buttons.clear()
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild(self) -> None:
        self._last_width = self.width()
        self._clear_layout()
        if not self._segments:
            self._layout.addStretch(1)
            return

        metrics = self.fontMetrics()
        separator_width = max(18, metrics.horizontalAdvance("›") + 10)
        drive_button = QToolButton(self)
        drive_button.setObjectName("browser_location_drives")
        drive_button.setText("›")
        drive_button.setToolTip(tr('ドライブを選択'))
        drive_button.clicked.connect(lambda: self._show_drives(drive_button))
        self._layout.addWidget(drive_button)
        available = max(60, self.width() - 8 - separator_width)
        widths = [
            min(230, metrics.horizontalAdvance(item.label) + 22)
            for item in self._segments
        ]
        separator_width = max(18, metrics.horizontalAdvance("›") + 10)
        visible_start = len(self._segments) - 1
        used = min(widths[-1], max(60, available - separator_width)) + separator_width
        while visible_start > 0:
            candidate = widths[visible_start - 1] + separator_width
            ellipsis = 34 if visible_start - 1 > 0 else 0
            if used + candidate + ellipsis > available:
                break
            visible_start -= 1
            used += candidate

        if visible_start > 0:
            omitted = self._segments[:visible_start]
            button = QToolButton(self)
            button.setObjectName("browser_location_ellipsis")
            button.setText("…")
            button.setToolTip(tr('省略した上位階層'))
            menu = QMenu(button)
            for segment in omitted:
                action = menu.addAction(segment.label)
                action.setToolTip(segment.path)
                action.triggered.connect(
                    lambda _checked=False, value=segment.path: (
                        self.locationActivated.emit(value)
                    )
                )
            button.setMenu(menu)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            self._layout.addWidget(button)

        shown = self._segments[visible_start:]
        for index, segment in enumerate(shown):
            absolute_index = visible_start + index
            button = QToolButton(self)
            button.setObjectName(f"browser_location_segment_{absolute_index}")
            max_width = min(
                260 if absolute_index == len(self._segments) - 1 else 190,
                max(60, available),
            )
            button.setText(
                metrics.elidedText(
                    segment.label,
                    Qt.TextElideMode.ElideMiddle,
                    max_width - 18,
                )
            )
            button.setToolTip(segment.path)
            if absolute_index == len(self._segments) - 1:
                font = QFont(button.font())
                font.setBold(True)
                button.setFont(font)
                button.clicked.connect(
                    lambda _checked=False: self.editRequested.emit()
                )
            else:
                button.clicked.connect(
                    lambda _checked=False, value=segment.path: (
                        self.locationActivated.emit(value)
                    )
                )
            self._layout.addWidget(button)

            separator = QToolButton(self)
            separator.setObjectName(f"browser_location_separator_{absolute_index}")
            separator.setText("›")
            separator.setToolTip(tr('{p0} 直下のフォルダ', p0=segment.label))
            key = os.path.normcase(
                os.path.abspath(os.path.normpath(segment.path))
            ).casefold()
            self._separator_buttons[key] = separator
            separator.clicked.connect(
                lambda _checked=False, value=segment.path: (
                    self.childrenRequested.emit(value)
                )
            )
            self._layout.addWidget(separator)

        self._layout.addStretch(1)

    def _show_drives(self, anchor: QToolButton) -> None:
        if self._drive_popup is not None:
            self._drive_popup.close()
            return
        current_root = self._segments[0].path.casefold() if self._segments else ""
        # Enumerate roots only: do not query volume labels, capacity or readiness
        # here, since an unavailable network/removable drive may block those calls.
        entries = tuple(
            LocationPopupEntry(
                drive.absoluteFilePath().rstrip('\\/') or drive.absoluteFilePath(),
                drive.absoluteFilePath(),
                drive.absoluteFilePath(),
                current=os.path.normpath(drive.absoluteFilePath()).casefold()
                == os.path.normpath(current_root).casefold(),
            )
            for drive in QDir.drives()
        )
        if not entries:
            return
        popup = BrowserLocationListPopup(entries, self)
        self._drive_popup = popup
        popup.entryActivated.connect(lambda entry: self.locationActivated.emit(str(entry.value)))
        popup.closed.connect(self._release_drive_popup)
        popup.show_for(anchor)

    def _release_drive_popup(self, popup: BrowserLocationListPopup) -> None:
        if self._drive_popup is popup:
            self._drive_popup = None
        popup.deleteLater()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        if self._drive_popup is not None:
            self._drive_popup.close()
        super().hideEvent(event)


__all__ = [
    "BrowserLocationBreadcrumb",
    "BrowserLocationListPopup",
    "LocationDirectoryLoader",
    "LocationDirectoryResult",
    "LocationPopupEntry",
    "LocationSegment",
    "list_child_directories",
    "split_location_segments",
]
