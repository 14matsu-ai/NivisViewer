from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event

from .browser_model import BrowserItem, BrowserItemKind
from .browser_thumbnail_scheduler import ThumbnailPriority
from .ffmpeg_thumbnail_backend import (
    FFMPEG_PREVIEW_RENDER_VERSION,
    FFmpegLocator,
    FFmpegThumbnailBackend,
    VIDEO_PREVIEW_EXTENSIONS,
)
from .file_preview import PreviewResult, PreviewResultKind
from .text_preview_provider import (
    TEXT_PREVIEW_EXTENSIONS,
    TEXT_PREVIEW_RENDER_VERSION,
    TextPreviewProvider,
)
from .thumbnail_render import ThumbnailRenderSpec
from .windows_shell_preview import WindowsShellPreviewService


class BrowserPreviewKind(str, Enum):
    FOLDER_COVER = "folder_cover"
    IMAGE = "image"
    ARCHIVE = "archive"
    PDF = "pdf"
    TEXT = "text"
    VIDEO = "video"
    WINDOWS_SHELL = "windows_shell"
    NONE = "none"


@dataclass(frozen=True)
class BrowserPreviewCapability:
    can_open: bool
    can_generate_preview: bool
    preview_kind: BrowserPreviewKind
    extension: str
    is_folder: bool
    is_supported: bool
    status: PreviewResultKind = PreviewResultKind.PENDING


class PreviewProviderRegistry:
    """Routes Browser previews without conflating preview and Viewer support."""

    def __init__(
        self,
        *,
        settings: dict[str, object] | None = None,
        text_provider: TextPreviewProvider | None = None,
        shell_service: WindowsShellPreviewService | None = None,
        ffmpeg_locator: FFmpegLocator | None = None,
        ffmpeg_backend: FFmpegThumbnailBackend | None = None,
    ) -> None:
        self._settings: dict[str, object] = {}
        self.text_provider = text_provider or TextPreviewProvider()
        self.shell_service = shell_service or WindowsShellPreviewService()
        self.ffmpeg_locator = ffmpeg_locator or FFmpegLocator()
        self._injected_ffmpeg_backend = ffmpeg_backend
        self.ffmpeg_backend = ffmpeg_backend
        self.update_settings(settings or {})

    def update_settings(self, settings: dict[str, object]) -> None:
        self._settings.update(settings)
        if self._injected_ffmpeg_backend is not None:
            self.ffmpeg_backend = self._injected_ffmpeg_backend
            return
        # Discovery may touch PATH or a user-provided network path, so it is
        # deferred until generate() is running on the Browser worker lane.
        self.ffmpeg_backend = None

    def capability_for(self, item: BrowserItem) -> BrowserPreviewCapability:
        extension = (item.extension or item.path.suffix).casefold()
        if item.kind is BrowserItemKind.FOLDER:
            kind = BrowserPreviewKind.FOLDER_COVER
        elif item.kind is BrowserItemKind.IMAGE:
            kind = BrowserPreviewKind.IMAGE
        elif item.kind is BrowserItemKind.ARCHIVE:
            kind = BrowserPreviewKind.ARCHIVE
        elif item.kind is BrowserItemKind.PDF:
            kind = BrowserPreviewKind.PDF
        elif extension in TEXT_PREVIEW_EXTENSIONS:
            kind = BrowserPreviewKind.TEXT
        elif extension in VIDEO_PREVIEW_EXTENSIONS:
            kind = BrowserPreviewKind.VIDEO
        else:
            kind = BrowserPreviewKind.WINDOWS_SHELL
        can_generate = True
        if kind is BrowserPreviewKind.TEXT:
            can_generate = bool(
                self._settings.get("text_preview_enabled", True)
            ) or self.shell_service is not None
        elif kind is BrowserPreviewKind.VIDEO:
            can_generate = (
                bool(self._settings.get("video_thumbnail_enabled", True))
                and str(
                    self._settings.get("video_thumbnail_backend", "auto")
                )
                != "disabled"
            )
        return BrowserPreviewCapability(
            can_open=bool(item.openable_by_nivisviewer),
            can_generate_preview=can_generate,
            preview_kind=kind,
            extension=extension,
            is_folder=item.kind is BrowserItemKind.FOLDER,
            is_supported=bool(item.openable_by_nivisviewer),
        )

    def generate(
        self,
        item: BrowserItem,
        spec: ThumbnailRenderSpec,
        *,
        priority: ThumbnailPriority,
        cancel_token: Event | None = None,
    ) -> PreviewResult:
        capability = self.capability_for(item)
        if capability.preview_kind in {
            BrowserPreviewKind.FOLDER_COVER,
            BrowserPreviewKind.IMAGE,
            BrowserPreviewKind.ARCHIVE,
            BrowserPreviewKind.PDF,
        }:
            return PreviewResult(PreviewResultKind.NOT_APPLICABLE)
        if cancel_token is not None and cancel_token.is_set():
            return PreviewResult(PreviewResultKind.CANCELLED)
        if capability.preview_kind is BrowserPreviewKind.TEXT:
            if (
                bool(self._settings.get("text_preview_enabled", True))
                and priority is not ThumbnailPriority.PREFETCH
            ):
                text_result = self.text_provider.generate(
                    item.path,
                    spec,
                    cancel_token,
                )
                if text_result.kind not in {
                    PreviewResultKind.NOT_APPLICABLE,
                    PreviewResultKind.UNAVAILABLE,
                }:
                    return text_result
            return self._shell_preview(
                item,
                spec,
                priority=priority,
                cancel_token=cancel_token,
            )
        if capability.preview_kind is BrowserPreviewKind.VIDEO:
            return self._video_preview(
                item,
                spec,
                priority=priority,
                cancel_token=cancel_token,
            )
        return self._shell_preview(
            item,
            spec,
            priority=priority,
            cancel_token=cancel_token,
        )

    def disk_cache_variant(self, item: BrowserItem) -> str | None:
        """Return the persistent renderer identity expected for this item.

        A non-empty sentinel intentionally prevents an old generated preview
        from bypassing a setting that now disables that provider.
        """

        capability = self.capability_for(item)
        if capability.preview_kind is BrowserPreviewKind.TEXT:
            return (
                TEXT_PREVIEW_RENDER_VERSION
                if bool(self._settings.get("text_preview_enabled", True))
                else "__preview_disabled__"
            )
        if capability.preview_kind is BrowserPreviewKind.VIDEO:
            backend = str(
                self._settings.get("video_thumbnail_backend", "auto")
            ).casefold()
            if (
                bool(self._settings.get("video_thumbnail_enabled", True))
                and backend in {"auto", "ffmpeg"}
            ):
                return FFMPEG_PREVIEW_RENDER_VERSION
            return "__preview_disabled__"
        if capability.preview_kind is BrowserPreviewKind.WINDOWS_SHELL:
            return "__shell_memory_only__"
        return None

    def shutdown(self) -> None:
        self.shell_service.shutdown()

    def _video_preview(
        self,
        item: BrowserItem,
        spec: ThumbnailRenderSpec,
        *,
        priority: ThumbnailPriority,
        cancel_token: Event | None,
    ) -> PreviewResult:
        if not bool(self._settings.get("video_thumbnail_enabled", True)):
            return PreviewResult(PreviewResultKind.NOT_APPLICABLE)
        backend = str(
            self._settings.get("video_thumbnail_backend", "auto")
        ).casefold()
        if backend == "disabled":
            return PreviewResult(PreviewResultKind.NOT_APPLICABLE)
        if backend in {"auto", "windows_shell"}:
            shell_result = self._shell_preview(
                item,
                spec,
                priority=priority,
                cancel_token=cancel_token,
            )
            if shell_result.kind is PreviewResultKind.READY:
                return shell_result
            if (
                backend == "windows_shell"
                or priority is ThumbnailPriority.PREFETCH
                or shell_result.kind is PreviewResultKind.CANCELLED
            ):
                return shell_result
        if priority is ThumbnailPriority.PREFETCH:
            return PreviewResult(PreviewResultKind.UNAVAILABLE)
        if backend not in {"auto", "ffmpeg"}:
            return PreviewResult(PreviewResultKind.UNAVAILABLE)
        ffmpeg = self.ffmpeg_backend
        if ffmpeg is None:
            explicit = str(self._settings.get("ffmpeg_executable", "") or "")
            executable = self.ffmpeg_locator.locate(explicit)
            ffmpeg = FFmpegThumbnailBackend(executable)
            self.ffmpeg_backend = ffmpeg
        return ffmpeg.generate(item.path, spec, cancel_token)

    def _shell_preview(
        self,
        item: BrowserItem,
        spec: ThumbnailRenderSpec,
        *,
        priority: ThumbnailPriority,
        cancel_token: Event | None,
    ) -> PreviewResult:
        return self.shell_service.request_thumbnail(
            item.path,
            spec,
            cache_only=priority is ThumbnailPriority.PREFETCH,
            cancel_token=cancel_token,
            source_size=item.file_size,
            source_mtime_ns=item.modified_time_ns,
        )
