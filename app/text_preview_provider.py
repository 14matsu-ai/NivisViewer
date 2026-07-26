from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen

from .file_preview import PreviewResult, PreviewResultKind, PreviewSource
from .thumbnail_render import ThumbnailRenderSpec


TEXT_PREVIEW_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".log",
        ".ini",
        ".cfg",
        ".conf",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".xml",
        ".csv",
        ".py",
        ".js",
        ".ts",
        ".css",
        ".html",
        ".htm",
        ".bat",
        ".cmd",
        ".ps1",
    }
)
TEXT_PREVIEW_MAX_BYTES = 64 * 1024
TEXT_PREVIEW_RENDER_VERSION = "text-v1"


@dataclass(frozen=True)
class TextPreviewContent:
    text: str
    encoding: str


class TextPreviewProvider:
    """Bounded, non-executing text preview renderer for Browser workers."""

    def __init__(self, *, max_bytes: int = TEXT_PREVIEW_MAX_BYTES) -> None:
        self.max_bytes = max(1024, min(TEXT_PREVIEW_MAX_BYTES, int(max_bytes)))

    @staticmethod
    def supports(path: str | Path) -> bool:
        return Path(path).suffix.casefold() in TEXT_PREVIEW_EXTENSIONS

    def load_content(
        self,
        path: str | Path,
        cancel_token: Event | None = None,
    ) -> TextPreviewContent | PreviewResult:
        if cancel_token is not None and cancel_token.is_set():
            return PreviewResult(PreviewResultKind.CANCELLED)
        try:
            with Path(path).open("rb") as source:
                data = source.read(self.max_bytes + 1)
        except (OSError, ValueError) as exc:
            return PreviewResult.failed(f"テキストを読み込めません: {exc}")
        if cancel_token is not None and cancel_token.is_set():
            return PreviewResult(PreviewResultKind.CANCELLED)
        if not data:
            return PreviewResult(PreviewResultKind.NO_CONTENT)
        if self._looks_binary(data):
            return PreviewResult(PreviewResultKind.NOT_APPLICABLE)
        decoded = self.decode(data[: self.max_bytes])
        if decoded is None:
            return PreviewResult(PreviewResultKind.NOT_APPLICABLE)
        if not decoded.text.strip():
            return PreviewResult(PreviewResultKind.NO_CONTENT)
        return decoded

    def generate(
        self,
        path: str | Path,
        spec: ThumbnailRenderSpec,
        cancel_token: Event | None = None,
    ) -> PreviewResult:
        content = self.load_content(path, cancel_token)
        if isinstance(content, PreviewResult):
            return content
        if cancel_token is not None and cancel_token.is_set():
            return PreviewResult(PreviewResultKind.CANCELLED)
        image = self.render(content.text, spec)
        if image.isNull():
            return PreviewResult.failed("テキストプレビューを描画できません")
        return PreviewResult.ready_image(
            image,
            source=PreviewSource.TEXT,
            persist_to_disk=True,
            entry_path=TEXT_PREVIEW_RENDER_VERSION,
        )

    @staticmethod
    def decode(data: bytes) -> TextPreviewContent | None:
        candidates: list[tuple[str, str]] = []
        if data.startswith(b"\x00\x00\xfe\xff"):
            candidates.append(("utf-32-be", "UTF-32 BE"))
        elif data.startswith(b"\xff\xfe\x00\x00"):
            candidates.append(("utf-32-le", "UTF-32 LE"))
        elif data.startswith(b"\xef\xbb\xbf"):
            candidates.append(("utf-8-sig", "UTF-8 BOM"))
        elif data.startswith(b"\xfe\xff"):
            candidates.append(("utf-16-be", "UTF-16 BE"))
        elif data.startswith(b"\xff\xfe"):
            candidates.append(("utf-16-le", "UTF-16 LE"))
        else:
            candidates.extend((("utf-8", "UTF-8"), ("cp932", "CP932")))
        for codec, label in candidates:
            try:
                text = data.decode(codec)
            except UnicodeDecodeError:
                continue
            return TextPreviewContent(text.lstrip("\ufeff"), label)
        return None

    @staticmethod
    def render(text: str, spec: ThumbnailRenderSpec) -> QImage:
        width = max(1, int(spec.frame_width))
        height = max(1, int(spec.frame_height))
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#f8f7f2"))
        painter = QPainter(image)
        try:
            border = max(1, round(min(width, height) * 0.008))
            painter.setPen(QPen(QColor("#d5d1c5"), border))
            painter.drawRect(0, 0, width - 1, height - 1)
            margin = max(6, round(min(width, height) * 0.055))
            font = QFont("Consolas")
            font.setStyleHint(QFont.StyleHint.Monospace)
            font.setPixelSize(max(8, min(24, round(height / 18))))
            painter.setFont(font)
            painter.setPen(QColor("#272727"))
            metrics = QFontMetrics(font)
            body = QRect(
                margin,
                margin,
                max(1, width - margin * 2),
                max(1, height - margin * 2),
            )
            line_height = max(1, metrics.lineSpacing())
            max_lines = max(8, min(20, body.height() // line_height))
            y = body.top()
            for raw_line in text.splitlines()[:max_lines]:
                line = raw_line.expandtabs(4).replace("\x00", "")
                line = metrics.elidedText(
                    line,
                    Qt.TextElideMode.ElideRight,
                    body.width(),
                )
                painter.drawText(
                    QRect(body.left(), y, body.width(), line_height),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    line,
                )
                y += line_height
        finally:
            painter.end()
        return image

    @staticmethod
    def _looks_binary(data: bytes) -> bool:
        # UTF-16/32 legitimately contains NULs; their BOM must be considered
        # before the generic binary heuristic.
        if data.startswith(
            (b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")
        ):
            return False
        sample = data[:8192]
        if b"\x00" in sample:
            return True
        controls = sum(
            byte < 32 and byte not in {9, 10, 12, 13}
            for byte in sample
        )
        return bool(sample) and controls / len(sample) > 0.02
