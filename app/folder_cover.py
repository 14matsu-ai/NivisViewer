"""Portable, folder-local cover snapshots shared by Browser and Viewer."""
from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from .cloud_files import local_image_candidate, require_local
from .supported_formats import CREATIVE_PROJECT_EXTENSIONS, PSD_EXTENSIONS


NIVIS_COVER_NAME = "{NivisViewerCoverFile}.jpg"
ZIPPLA_COVER_NAME = "{ZipPlaCoverFile}.jpg"
_EXIF_SOFTWARE = "NivisViewer folder cover"
_COVER_EDGE = 1024
_write_lock = threading.Lock()


def is_zippla_cover_name(name: str) -> bool:
    return name.casefold() == ZIPPLA_COVER_NAME.casefold()


def is_cover_artifact_name(name: str, *, use_zippla_cover: bool = False) -> bool:
    folded = name.casefold()
    return folded == NIVIS_COVER_NAME.casefold() or (
        folded.startswith(f".{NIVIS_COVER_NAME.casefold()}.")
        and folded.endswith(".tmp")
    ) or (use_zippla_cover and is_zippla_cover_name(name))


def cover_candidates(folder: Path, *, use_zippla_cover: bool = False) -> tuple[Path, ...]:
    nivis = folder / NIVIS_COVER_NAME
    zippla = folder / ZIPPLA_COVER_NAME
    return tuple(
        path for path in ((nivis, zippla) if use_zippla_cover else (nivis,))
        if local_image_candidate(path) and path.is_file()
        and (path != nivis or _is_owned_cover(path))
    )


def resolve_folder_cover(folder: Path, *, use_zippla_cover: bool = False) -> Path | None:
    return next(iter(cover_candidates(folder, use_zippla_cover=use_zippla_cover)), None)


def has_nivis_cover(folder: Path) -> bool:
    path = folder / NIVIS_COVER_NAME
    return local_image_candidate(path) and path.is_file()


def _is_owned_cover(path: Path) -> bool:
    if not local_image_candidate(path) or path.is_symlink():
        return False
    try:
        with Image.open(path) as image:
            return image.format == "JPEG" and image.getexif().get(305) == _EXIF_SOFTWARE
    except (OSError, ValueError):
        return False


def _open_cover_source(path: Path):
    suffix = path.suffix.casefold()
    if suffix in {".svg", ".ai"}:
        from .vector_image_decoder import vector_pil
        return vector_pil(path, suffix, (_COVER_EDGE, _COVER_EDGE))
    if suffix in PSD_EXTENSIONS:
        from .psd_decoder import decode_psd_thumbnail
        return decode_psd_thumbnail(path, minimum_long_edge=_COVER_EDGE)
    if suffix in CREATIVE_PROJECT_EXTENSIONS:
        from .creative_image_decoder import decode_creative_thumbnail
        return decode_creative_thumbnail(path, suffix=suffix, minimum_long_edge=_COVER_EDGE)
    return Image.open(path)


def set_folder_cover(image_path: Path) -> Path:
    """Write a bounded JPEG snapshot atomically, without changing the source image."""
    image_path = Path(image_path)
    folder = image_path.parent
    target = folder / NIVIS_COVER_NAME
    require_local(folder)
    require_local(image_path)
    with _write_lock:
        if (target.exists() or target.is_symlink()) and not _is_owned_cover(target):
            raise FileExistsError(f"Cover filename is already in use: {target}")
        temporary = folder / f".{NIVIS_COVER_NAME}.{uuid.uuid4().hex}.tmp"
        try:
            with _open_cover_source(image_path) as source:
                source.draft("RGB", (_COVER_EDGE, _COVER_EDGE))
                source.thumbnail((_COVER_EDGE, _COVER_EDGE), Image.Resampling.LANCZOS)
                image = ImageOps.exif_transpose(source)
                if image.mode != "RGBA":
                    image = image.convert("RGBA")
                canvas = Image.new("RGBA", image.size, "white")
                canvas.alpha_composite(image)
                rgb = canvas.convert("RGB")
                exif = Image.Exif()
                exif[305] = _EXIF_SOFTWARE
                rgb.save(temporary, format="JPEG", quality=88, exif=exif)
            if (target.exists() or target.is_symlink()) and not _is_owned_cover(target):
                raise FileExistsError(f"Cover filename is already in use: {target}")
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return target


def clear_folder_cover(folder: Path) -> bool:
    target = Path(folder) / NIVIS_COVER_NAME
    with _write_lock:
        if not _is_owned_cover(target):
            return False
        target.unlink()
    return True


@dataclass(frozen=True)
class CoverOperationResult:
    folder: Path
    action: str
    succeeded: bool
    error: str = ""


class _CoverWorkerSignals(QObject):
    finished = Signal(object)


class FolderCoverWorker(QRunnable):
    def __init__(self, action: str, path: Path) -> None:
        super().__init__()
        self.action = action
        self.path = Path(path)
        self.signals = _CoverWorkerSignals()

    @Slot()
    def run(self) -> None:
        folder = self.path.parent if self.action == "set" else self.path
        try:
            if self.action == "set":
                set_folder_cover(self.path)
                succeeded = True
            else:
                succeeded = clear_folder_cover(folder)
            error = ""
        except Exception as exc:
            succeeded = False
            error = str(exc)
        self.signals.finished.emit(CoverOperationResult(folder, self.action, succeeded, error))
