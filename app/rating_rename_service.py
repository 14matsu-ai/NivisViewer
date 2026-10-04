"""Same-directory filename-metadata rename with timestamp preservation."""

from __future__ import annotations

from .i18n import tr


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
        return self.set_metadata(
            ZipPlaFilenameMetadata.parse(path).with_rating(rating), is_directory=is_directory,
        )

    def set_metadata(
        self, metadata: ZipPlaFilenameMetadata, *, is_directory: bool | None = None,
    ) -> RatingRenameResult:
        path = metadata.original_path
        source = Path(path)
        normalized_rating = metadata.rating
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
        destination = metadata.serialized_path(is_directory=directory)
        same_key = os.path.normcase(os.fspath(source)) == os.path.normcase(os.fspath(destination))
        if os.fspath(source) == os.fspath(destination):
            return RatingRenameResult(
                source,
                destination,
                normalized_rating,
                True,
                changed=False,
                original_mtime_ns=source_stat.st_mtime_ns,
                final_mtime_ns=source_stat.st_mtime_ns,
            )
        if destination.exists() and not same_key:
            return RatingRenameResult(
                source,
                destination,
                normalized_rating,
                False,
                error_message=tr('同名の項目が既に存在します。'),
                original_mtime_ns=source_stat.st_mtime_ns,
            )
        try:
            if same_key:
                # Reuse the established staging/rollback case-rename path,
                # which checks directory entries rather than folded identity.
                from .file_operation_service import FileOperationService
                result = FileOperationService().rename(source, destination.name)
                item = result.effective_items[0] if result.effective_items else None
                if item is None or not item.success:
                    raise OSError(item.error_message if item is not None else tr('変更できません'))
            else:
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
