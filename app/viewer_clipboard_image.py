"""Decode the displayed page at source resolution for the clipboard off the UI thread."""

from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal

from .image_source import create_image_source
from .thumbnail_render import pil_to_qimage


class _ClipboardSignals(QObject):
    finished = Signal(object, object, object)


class ClipboardImageDecode(QRunnable):
    def __init__(self, identity, source_path, image_id, *, source_options):
        super().__init__()
        self.identity = identity
        self.source_path = source_path
        self.image_id = image_id
        self.source_options = dict(source_options)
        self.cancelled = Event()
        self.signals = _ClipboardSignals()

    def run(self):
        source = None
        image = None
        result = None
        error = None
        try:
            if not self.cancelled.is_set():
                source, _selected = create_image_source(
                    self.source_path,
                    cancel_token=self.cancelled,
                    **self.source_options,
                )
                if not self.cancelled.is_set():
                    image = source.open_image(self.image_id)
                    if not self.cancelled.is_set():
                        result = pil_to_qimage(image)
        except Exception as exc:
            error = str(exc)
        finally:
            if image is not None:
                image.close()
            if source is not None:
                source.close()
            self.signals.finished.emit(self.identity, result, error)
