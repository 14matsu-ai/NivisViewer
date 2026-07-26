from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
from threading import Event
from time import monotonic
from typing import Callable

from PIL import Image

from .file_preview import PreviewResult, PreviewResultKind, PreviewSource
from .thumbnail_render import (
    ThumbnailRenderSpec,
    render_pil_thumbnail,
)


VIDEO_PREVIEW_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".m4v",
        ".mkv",
        ".avi",
        ".mov",
        ".wmv",
        ".webm",
        ".mpg",
        ".mpeg",
        ".mts",
        ".m2ts",
        ".ts",
        ".flv",
        ".ogv",
        ".3gp",
    }
)
FFMPEG_PREVIEW_RENDER_VERSION = "ffmpeg-v1"


class FFmpegLocator:
    """Finds an existing FFmpeg executable without downloading anything."""

    def __init__(self, *, application_dir: str | Path | None = None) -> None:
        self.application_dir = (
            Path(application_dir)
            if application_dir is not None
            else Path(__file__).resolve().parents[1]
        )

    def locate(self, explicit_path: str | Path = "") -> Path | None:
        raw = str(explicit_path or "").strip().strip('"')
        if raw:
            candidate = Path(raw)
            return candidate if candidate.is_file() else None
        bundled_candidates = (
            self.application_dir / "ffmpeg.exe",
            self.application_dir / "tools" / "ffmpeg.exe",
            self.application_dir / "bin" / "ffmpeg.exe",
        )
        for candidate in bundled_candidates:
            if candidate.is_file():
                return candidate
        discovered = shutil.which("ffmpeg")
        return Path(discovered) if discovered else None


class FFmpegThumbnailBackend:
    """Optional, bounded FFmpeg video-frame extractor.

    It is called only from Browser workers.  No shell is involved, stdin is
    closed, and cancellation always terminates the exact child process.
    """

    def __init__(
        self,
        executable: str | Path | None,
        *,
        timeout_seconds: float = 12.0,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self.executable = Path(executable) if executable else None
        self.timeout_seconds = max(0.05, min(60.0, float(timeout_seconds)))
        self._process_factory = process_factory
        self.last_command: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.executable is not None and self.executable.is_file()

    @staticmethod
    def supports(path: str | Path) -> bool:
        return Path(path).suffix.casefold() in VIDEO_PREVIEW_EXTENSIONS

    def generate(
        self,
        path: str | Path,
        spec: ThumbnailRenderSpec,
        cancel_token: Event | None = None,
    ) -> PreviewResult:
        if cancel_token is not None and cancel_token.is_set():
            return PreviewResult(PreviewResultKind.CANCELLED)
        if not self.available:
            return PreviewResult(PreviewResultKind.UNAVAILABLE)
        source = Path(path)
        edge = max(32, min(2048, int(spec.long_edge)))
        filter_graph = (
            "thumbnail=100,"
            f"scale={edge}:{edge}:force_original_aspect_ratio=decrease"
        )
        command = (
            str(self.executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(source),
            "-vf",
            filter_graph,
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "pipe:1",
        )
        self.last_command = command
        try:
            process = self._process_factory(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if os.name == "nt"
                    else 0
                ),
            )
        except (OSError, ValueError):
            return PreviewResult(PreviewResultKind.UNAVAILABLE)

        started = monotonic()
        output = b""
        error_output = b""
        cancelled = False
        timed_out = False
        try:
            while True:
                if cancel_token is not None and cancel_token.is_set():
                    cancelled = True
                    break
                remaining = self.timeout_seconds - (monotonic() - started)
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    output, error_output = process.communicate(
                        timeout=min(0.15, remaining)
                    )
                    break
                except subprocess.TimeoutExpired:
                    continue
            if cancelled or timed_out:
                self._terminate_process(process)
                return PreviewResult(
                    PreviewResultKind.CANCELLED
                    if cancelled
                    else PreviewResultKind.UNAVAILABLE
                )
        finally:
            if process.poll() is None:
                self._terminate_process(process)

        if len(output) > 32 * 1024 * 1024:
            return PreviewResult.failed("動画サムネイルの出力が大きすぎます")
        error_output = error_output[: 1024 * 1024]
        if process.returncode != 0 or not output:
            return PreviewResult(PreviewResultKind.UNAVAILABLE)
        try:
            with Image.open(BytesIO(output)) as image:
                rendered, _crop = render_pil_thumbnail(image, spec)
        except Exception as exc:
            return PreviewResult.failed(f"動画フレームを読み込めません: {exc}")
        if rendered is None or rendered.isNull():
            return PreviewResult.failed("動画フレームを描画できません")
        return PreviewResult.ready_image(
            rendered,
            source=PreviewSource.FFMPEG,
            persist_to_disk=True,
            entry_path=FFMPEG_PREVIEW_RENDER_VERSION,
        )

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        try:
            process.terminate()
            process.wait(timeout=0.75)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=0.75)
            except (OSError, subprocess.TimeoutExpired):
                pass
