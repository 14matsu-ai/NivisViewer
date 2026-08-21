"""Same-directory filename-metadata rename with timestamp preservation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .zippla_filename_metadata import ZipPlaFilenameMetadata


@dataclass(frozen=True)
class RatingRenameResult:
    source_path: Path
    destination_path: Path
    rating: int | None
    success: bool
    changed: bool = False
    error_message: str | None = None
    original_mtime_ns: int | None = None
    final_mtime_ns: int | None = None


class RatingRenameService:
    """Rename only the ZipPla rating token; never rewrite file content."""

    def set_rating(
        self,
        path: str | Path,
        rating: int | None,
        *,
        is_directory: bool | None = None,
    ) -> RatingRenameResult:
        source = Path(path)
        normalized_rating = (
            int(rating) if rating is not None and 1 <= int(rating) <= 5 else None
        )
        try:
            source_stat = source.stat()
        except OSError as exc:
            return RatingRenameResult(
                source,
                source,
                normalized_rating,
                False,
                error_message=str(exc),
            )
        directory = source.is_dir() if is_directory is None else bool(is_directory)
        metadata = ZipPlaFilenameMetadata.parse(source).with_rating(
            normalized_rating
        )
        destination = metadata.serialized_path(is_directory=directory)
        if os.path.normcase(os.fspath(source)) == os.path.normcase(
            os.fspath(destination)
        ):
            return RatingRenameResult(
                source,
                destination,
                normalized_rating,
                True,
                changed=False,
                original_mtime_ns=source_stat.st_mtime_ns,
                final_mtime_ns=source_stat.st_mtime_ns,
            )
        if destination.exists():
            return RatingRenameResult(
                source,
                destination,
                normalized_rating,
                False,
                error_message="同名の項目が既に存在します。",
                original_mtime_ns=source_stat.st_mtime_ns,
            )
        try:
            os.rename(source, destination)
            destination_stat = destination.stat()
            if destination_stat.st_mtime_ns != source_stat.st_mtime_ns:
                os.utime(
                    destination,
                    ns=(destination_stat.st_atime_ns, source_stat.st_mtime_ns),
                    follow_symlinks=False,
                )
                destination_stat = destination.stat()
            return RatingRenameResult(
                source,
                destination,
                normalized_rating,
                True,
                changed=True,
                original_mtime_ns=source_stat.st_mtime_ns,
                final_mtime_ns=destination_stat.st_mtime_ns,
            )
        except OSError as exc:
            return RatingRenameResult(
                source,
                destination,
                normalized_rating,
                False,
                error_message=str(exc),
                original_mtime_ns=source_stat.st_mtime_ns,
            )


__all__ = ["RatingRenameResult", "RatingRenameService"]
