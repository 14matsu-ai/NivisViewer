"""Keep Qt codec initialization outside the GIL-releasing pixel read."""

from PySide6.QtGui import QImage, QImageReader


def read_qimage(reader: QImageReader) -> QImage:
    # In the pinned PySide build canRead retains the GIL; read releases it.
    # Initialize the handler first: a Python-created QIODevice's Shiboken
    # virtual wrappers may need the GIL under Qt's handler-discovery mutex.
    # Lazy discovery inside read can otherwise deadlock with GUI image/icon
    # loading. Pixel decoding remains parallel once that mutex is released.
    if not reader.canRead():
        return QImage()
    return reader.read()
