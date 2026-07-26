from __future__ import annotations

from PySide6.QtCore import QEvent, QMimeData, QObject

from .drag_drop import paths_from_mime_data, viewer_drop_paths


class ExternalDropOpenController(QObject):
    """Validates local Viewer drops and forwards child-widget drop events."""

    _DRAG_EVENT_TYPES = {
        QEvent.Type.DragEnter,
        QEvent.Type.DragMove,
        QEvent.Type.DragLeave,
        QEvent.Type.Drop,
    }

    @staticmethod
    def local_paths(mime: QMimeData) -> tuple[str, ...]:
        return paths_from_mime_data(mime)

    @staticmethod
    def paths(mime: QMimeData) -> tuple[str, ...]:
        return viewer_drop_paths(mime)

    @classmethod
    def is_drag_event(cls, event: QEvent) -> bool:
        return event.type() in cls._DRAG_EVENT_TYPES
