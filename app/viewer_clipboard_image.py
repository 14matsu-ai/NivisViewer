"""Decode the displayed page at source resolution for the clipboard off the UI thread."""

from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal

from .image_source import create_image_source
from .raster_execution_budget import source_request_scope
from .gimp_xcf_backend import gimp_cancellation
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
                    with source_request_scope(self.cancelled), gimp_cancellation(self.cancelled):
                        image = source.open_image(self.image_id)
                    if not self.cancelled.is_set():
                        result = pil_to_qimage(image)
        except Exception as exc:
            error = str(exc)
        finally:
            for resource in (image, source):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception as exc:
                        error = error or str(exc)
            self.signals.finished.emit(self.identity, result, error)
