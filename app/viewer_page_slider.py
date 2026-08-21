from __future__ import annotations

from PySide6.QtCore import QPoint, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QSlider, QWidget


class ViewerPageSlider(QSlider):
    """A focused-page slider whose wheel input never reaches the Viewer."""

    nextSinglePageRequested = Signal()
    previousSinglePageRequested = Signal()
    nextDisplayUnitRequested = Signal()
    previousDisplayUnitRequested = Signal()
    focusedPageRequested = Signal(int)
    wheelInteraction = Signal()
    # Preserve QInputEvent's quint64 timestamp across long Windows uptimes.
    wheelInputObserved = Signal(object)
    wheelSequenceFinished = Signal()

    _ANGLE_STEP = 120
    _PIXEL_STEP = 40

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._single_page_wheel_enabled = False
        self._angle_remainder = 0
        self._pixel_remainder = 0
        self.valueChanged.connect(self.focusedPageRequested)

    def set_single_page_wheel_enabled(self, enabled: bool) -> None:
        self._single_page_wheel_enabled = bool(enabled)
        self.reset_wheel_accumulator()

    def set_page_state(self, page_count: int, focused_index: int) -> None:
        count = max(0, int(page_count))
        maximum = max(0, count - 1)
        value = max(0, min(int(focused_index), maximum))
        with QSignalBlocker(self):
            if self.isEnabled() != (count > 0):
                self.setEnabled(count > 0)
            if self.minimum() != 0 or self.maximum() != maximum:
                self.setRange(0, maximum)
            if self.value() != value:
                self.setValue(value)

    def reset_wheel_accumulator(self) -> None:
        self._angle_remainder = 0
        self._pixel_remainder = 0

    def process_wheel_delta(
        self,
        angle_delta: QPoint,
        pixel_delta: QPoint,
        *,
        sequence_finished: bool = False,
    ) -> bool:
        vertical_angle = angle_delta.y()
        vertical_pixel = pixel_delta.y()
        horizontal_only = (
            vertical_angle == 0
            and vertical_pixel == 0
            and (
                angle_delta.x() != 0
                or pixel_delta.x() != 0
            )
        )
        if (
            not horizontal_only
            and vertical_angle == 0
            and vertical_pixel == 0
            and not sequence_finished
        ):
            return False

        self.wheelInteraction.emit()
        if sequence_finished and vertical_angle == 0 and vertical_pixel == 0:
            self.wheelSequenceFinished.emit()
            return True
        if horizontal_only:
            if sequence_finished:
                self.wheelSequenceFinished.emit()
            return True

        direction = 0
        steps = 0
        if vertical_pixel:
            if self._pixel_remainder and (
                self._pixel_remainder > 0
            ) != (vertical_pixel > 0):
                self._pixel_remainder = 0
            self._pixel_remainder += vertical_pixel
            if abs(self._pixel_remainder) >= self._PIXEL_STEP:
                direction = 1 if self._pixel_remainder > 0 else -1
                steps = abs(self._pixel_remainder) // self._PIXEL_STEP
                self._pixel_remainder -= (
                    direction * self._PIXEL_STEP * steps
                )
                self._angle_remainder = 0
        elif vertical_angle:
            if self._angle_remainder and (
                self._angle_remainder > 0
            ) != (vertical_angle > 0):
                self._angle_remainder = 0
            self._angle_remainder += vertical_angle
            if abs(self._angle_remainder) >= self._ANGLE_STEP:
                direction = 1 if self._angle_remainder > 0 else -1
                steps = abs(self._angle_remainder) // self._ANGLE_STEP
                self._angle_remainder -= (
                    direction * self._ANGLE_STEP * steps
                )
                self._pixel_remainder = 0

        for _index in range(steps):
            if direction > 0:
                if self._single_page_wheel_enabled:
                    self.previousSinglePageRequested.emit()
                else:
                    self.previousDisplayUnitRequested.emit()
            elif direction < 0:
                if self._single_page_wheel_enabled:
                    self.nextSinglePageRequested.emit()
                else:
                    self.nextDisplayUnitRequested.emit()
        if sequence_finished:
            self.wheelSequenceFinished.emit()
        return True

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        self.wheelInputObserved.emit(int(event.timestamp()))
        handled = self.process_wheel_delta(
            event.angleDelta(),
            event.pixelDelta(),
            sequence_finished=event.isEndEvent(),
        )
        event.setAccepted(handled)
