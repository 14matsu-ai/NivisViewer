from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtGui import QImage


class PreviewResultKind(str, Enum):
    """Semantic outcome of a Browser preview request.

    Only ``FAILED`` represents a broken preview that should be surfaced to the
    user.  The other image-less states are normal control-flow outcomes.
    """

    READY = "ready"
    PENDING = "pending"
    NO_CONTENT = "no_content"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PreviewSource(str, Enum):
    EXISTING = "existing"
    FOLDER_COVER = "folder_cover"
    TEXT = "text"
    WINDOWS_SHELL = "windows_shell"
    FFMPEG = "ffmpeg"
    ASSOCIATED_ICON = "associated_icon"
    NONE = "none"


QUIET_PREVIEW_RESULTS = frozenset(
    {
        PreviewResultKind.PENDING,
        PreviewResultKind.NO_CONTENT,
        PreviewResultKind.NOT_APPLICABLE,
        PreviewResultKind.UNAVAILABLE,
        PreviewResultKind.CANCELLED,
    }
)


@dataclass(frozen=True)
class PreviewResult:
    kind: PreviewResultKind
    image: QImage | None = None
    source: PreviewSource = PreviewSource.NONE
    message: str = ""
    persist_to_disk: bool = False
    entry_path: str = ""

    @property
    def ready(self) -> bool:
        return (
            self.kind is PreviewResultKind.READY
            and self.image is not None
            and not self.image.isNull()
        )

    @property
    def quiet(self) -> bool:
        return self.kind in QUIET_PREVIEW_RESULTS

    @classmethod
    def ready_image(
        cls,
        image: QImage,
        *,
        source: PreviewSource,
        persist_to_disk: bool,
        entry_path: str = "",
    ) -> PreviewResult:
        return cls(
            PreviewResultKind.READY,
            image.copy(),
            source,
            persist_to_disk=persist_to_disk,
            entry_path=entry_path,
        )

    @classmethod
    def failed(cls, message: str) -> PreviewResult:
        return cls(PreviewResultKind.FAILED, message=str(message))
