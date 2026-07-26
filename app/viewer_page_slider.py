from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QSlider, QWidget


class ViewerPageSlider(QSlider):
    """A focused-page slider whose wheel input never reaches the Viewer."""

    nextSinglePageRequested = Signal()
    previousSinglePageRequested = Signal()
    focusedPageRequested = Signal(int)
    wheelInteraction = Signal()

    _ANGLE_STEP = 120
    _PIXEL_STEP = 40

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._angle_remainder = 0
        self._pixel_remainder = 0
        self.valueChanged.connect(self.focusedPageRequested)

    def set_page_state(self, page_count: int, focused_index: int) -> None:
        count = max(0, int(page_count))
        with QSignalBlocker(self):
            self.setEnabled(count > 0)
            self.setMinimum(0)
            self.setMaximum(max(0, count - 1))
            self.setValue(max(0, min(int(focused_index), max(0, count - 1))))

    def reset_wheel_accumulator(self) -> None:
        self._angle_remainder = 0
        self._pixel_remainder = 0

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        self.wheelInteraction.emit()
        vertical_angle = event.angleDelta().y()
        vertical_pixel = event.pixelDelta().y()
        horizontal_only = (
            vertical_angle == 0
            and vertical_pixel == 0
            and (
                event.angleDelta().x() != 0
                or event.pixelDelta().x() != 0
            )
        )
        if horizontal_only:
            event.accept()
            return

        direction = 0
        if vertical_pixel:
            if self._pixel_remainder and (
                self._pixel_remainder > 0
            ) != (vertical_pixel > 0):
                self._pixel_remainder = 0
            self._pixel_remainder += vertical_pixel
            if abs(self._pixel_remainder) >= self._PIXEL_STEP:
                direction = 1 if self._pixel_remainder > 0 else -1
                self._pixel_remainder -= direction * self._PIXEL_STEP
                self._angle_remainder = 0
        elif vertical_angle:
            if self._angle_remainder and (
                self._angle_remainder > 0
            ) != (vertical_angle > 0):
                self._angle_remainder = 0
            self._angle_remainder += vertical_angle
            if abs(self._angle_remainder) >= self._ANGLE_STEP:
                direction = 1 if self._angle_remainder > 0 else -1
                self._angle_remainder -= direction * self._ANGLE_STEP
                self._pixel_remainder = 0

        if direction > 0:
            self.previousSinglePageRequested.emit()
        elif direction < 0:
            self.nextSinglePageRequested.emit()
        event.accept()
