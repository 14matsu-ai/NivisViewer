"""Prepare one archive image for an external application off the GUI thread."""
from pathlib import Path
import tempfile
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal

from .image_source import create_image_source, ZipImageSource, SevenZipImageSource


class _Signals(QObject):
    finished = Signal(object, object, object)


class ArchiveImageExport(QRunnable):
    def __init__(self, identity, book_path, image_id, directory, registry):
        super().__init__()
        self.identity = identity
        self.book_path = book_path
        self.image_id = image_id
        self.directory = Path(directory)
        self.registry = registry
        self.cancelled = Event()
        self.signals = _Signals()

    def run(self):
        source = None
        target = None
        error = None
        try:
            if self.cancelled.is_set():
                return
            source, _ = create_image_source(self.book_path, archive_backend_registry=self.registry,
                                            cancel_token=self.cancelled)
            if isinstance(source, ZipImageSource):
                with source._read_entry_stream(self.image_id, self.cancelled) as stream:
                    payload = stream.getvalue()
            elif isinstance(source, SevenZipImageSource):
                payload = source._read_payload(self.image_id)
            else:
                raise ValueError("Not an archive image")
            if self.cancelled.is_set():
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            directory = Path(tempfile.mkdtemp(prefix="image-", dir=self.directory))
            # Preserve the original format, but never use archive paths for output.
            suffix = Path(self.image_id).suffix.lower()
            target = directory / ("image" + suffix)
            target.write_bytes(payload)
            if self.cancelled.is_set():
                target.unlink(missing_ok=True)
                directory.rmdir()
                target = None
        except Exception as exc:
            error = str(exc)
        finally:
            if source is not None:
                source.close()
            self.signals.finished.emit(self.identity, str(target) if target else None, error)
