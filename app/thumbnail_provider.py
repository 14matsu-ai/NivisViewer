from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QImage, QPixmap


class PageThumbnailProvider:
    @staticmethod
    def create_icon(qimage: QImage, size: int) -> QIcon:
        thumbnail = qimage.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        return QIcon(QPixmap.fromImage(thumbnail))
