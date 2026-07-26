from __future__ import annotations

from io import BytesIO
import json
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
from .video_thumbnail_policy import (
    VideoMetadata,
    VideoThumbnailFrameMode,
    VideoThumbnailPolicy,
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
FFMPEG_PREVIEW_RENDER_VERSION = "ffmpeg-v2"


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
        frame_mode: str = "smart",
    ) -> None:
        self.executable = Path(executable) if executable else None
        self.timeout_seconds = max(0.05, min(60.0, float(timeout_seconds)))
        self._process_factory = process_factory
        self.policy = VideoThumbnailPolicy(frame_mode)
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
        if self.policy.mode is VideoThumbnailFrameMode.WINDOWS_SHELL:
            return PreviewResult(PreviewResultKind.NOT_APPLICABLE)
        metadata = self._probe_metadata(source, cancel_token)
        edge = max(32, min(2048, int(spec.long_edge)))
        filter_graph = (
            "scale=trunc(iw*sar):ih,"
            f"scale={edge}:{edge}:force_original_aspect_ratio=decrease"
        )
        candidates: list[tuple[float, Image.Image]] = []
        for timestamp in self.policy.candidate_timestamps(metadata.duration):
            if cancel_token is not None and cancel_token.is_set():
                return PreviewResult(PreviewResultKind.CANCELLED)
            decoded = self._extract_frame(
                source,
                timestamp,
                filter_graph,
                cancel_token,
            )
            if isinstance(decoded, PreviewResult):
                if decoded.kind is PreviewResultKind.CANCELLED:
                    return decoded
                continue
            candidates.append((timestamp, decoded))
        selected = self.policy.select(candidates)
        if selected is None:
            return PreviewResult(PreviewResultKind.UNAVAILABLE)
        try:
            rendered = self.policy.render(selected.image, spec, metadata)
        except Exception as exc:
            return PreviewResult.failed(f"動画フレームを読み込めません: {exc}")
        if rendered is None or rendered.isNull():
            return PreviewResult.failed("動画フレームを描画できません")
        return PreviewResult.ready_image(
            rendered,
            source=PreviewSource.FFMPEG,
            persist_to_disk=True,
            entry_path=VideoThumbnailPolicy.cache_variant(self.policy.mode),
        )

    def _extract_frame(
        self,
        source: Path,
        timestamp: float,
        filter_graph: str,
        cancel_token: Event | None,
    ) -> Image.Image | PreviewResult:
        command = (
            str(self.executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{max(0.0, float(timestamp)):.3f}",
            "-noautorotate",
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
                return image.convert("RGBA")
        except Exception as exc:
            return PreviewResult.failed(f"動画フレームを読み込めません: {exc}")

    def _probe_metadata(
        self,
        source: Path,
        cancel_token: Event | None,
    ) -> VideoMetadata:
        if self._process_factory is not subprocess.Popen or self.executable is None:
            return VideoMetadata()
        probe = self.executable.with_name("ffprobe.exe")
        executable = probe if probe.is_file() else shutil.which("ffprobe")
        if not executable:
            return VideoMetadata()
        command = [
            str(executable),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,sample_aspect_ratio,display_aspect_ratio:stream_tags=rotate:format=duration",
            "-of",
            "json",
            str(source),
        ]
        try:
            process = subprocess.Popen(
                command,
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
            started = monotonic()
            output = b""
            while True:
                if cancel_token is not None and cancel_token.is_set():
                    self._terminate_process(process)
                    return VideoMetadata()
                remaining = min(4.0, self.timeout_seconds) - (
                    monotonic() - started
                )
                if remaining <= 0:
                    self._terminate_process(process)
                    return VideoMetadata()
                try:
                    output, _error = process.communicate(
                        timeout=min(0.1, remaining)
                    )
                    break
                except subprocess.TimeoutExpired:
                    continue
            data = json.loads(output[: 1024 * 1024] or b"{}")
            stream = (data.get("streams") or [{}])[0]
            duration = (data.get("format") or {}).get("duration")
            return VideoMetadata(
                duration=self._safe_float(duration),
                width=self._safe_int(stream.get("width")),
                height=self._safe_int(stream.get("height")),
                sample_aspect_ratio=self._parse_ratio(
                    stream.get("sample_aspect_ratio")
                ),
                display_aspect_ratio=self._parse_ratio(
                    stream.get("display_aspect_ratio")
                ),
                rotation=self._safe_int((stream.get("tags") or {}).get("rotate")) or 0,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return VideoMetadata()

    @staticmethod
    def _parse_ratio(value: object) -> float | None:
        text = str(value or "")
        separator = ":" if ":" in text else "/"
        try:
            left, right = text.split(separator, 1)
            denominator = float(right)
            result = float(left) / denominator
        except (ValueError, ZeroDivisionError):
            return None
        return result

    @staticmethod
    def _safe_float(value: object) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: object) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

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
