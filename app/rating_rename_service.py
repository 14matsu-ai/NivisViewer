"""Same-directory filename-metadata rename with timestamp preservation."""

from __future__ import annotations

from .i18n import tr


from dataclasses import dataclass
import logging
import os
from pathlib import Path

from .zippla_filename_metadata import ZipPlaFilenameMetadata


_LOG = logging.getLogger(__name__)


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

    @staticmethod
    def destination_conflict(
        metadata: ZipPlaFilenameMetadata, *, is_directory: bool = False,
    ) -> str | None:
        source = metadata.original_path
        destination = metadata.serialized_path(is_directory=is_directory)
        if (os.path.normcase(os.fspath(source)) != os.path.normcase(os.fspath(destination))
                and os.path.lexists(destination)):
            return tr('同名の項目が既に存在するため変更できません: {p0}', p0=destination.name)
        return None

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
        conflict = self.destination_conflict(metadata, is_directory=directory)
        if conflict:
            return RatingRenameResult(
                source,
                destination,
                normalized_rating,
                False,
                error_message=conflict,
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
        except OSError as exc:
            if getattr(exc, 'winerror', None) in {32, 33}:
                message = tr('ファイルが使用中のため変更できません。Viewerや他のアプリで閉じてから再試行してください。')
            elif isinstance(exc, FileExistsError):
                message = tr('同名の項目が既に存在するため変更できません: {p0}', p0=destination.name)
            else:
                message = str(exc)
            return RatingRenameResult(
                source,
                destination,
                normalized_rating,
                False,
                error_message=message,
                original_mtime_ns=source_stat.st_mtime_ns,
            )

        final_mtime_ns = None
        try:
            destination_stat = destination.stat()
            final_mtime_ns = destination_stat.st_mtime_ns
            if final_mtime_ns != source_stat.st_mtime_ns:
                os.utime(
                    destination,
                    ns=(destination_stat.st_atime_ns, source_stat.st_mtime_ns),
                    follow_symlinks=False,
                )
                final_mtime_ns = destination.stat().st_mtime_ns
        except (OSError, NotImplementedError, ValueError) as exc:
            # Publication already succeeded. Keep the new path authoritative
            # so the Browser, Viewer and history can follow the completed rename.
            final_mtime_ns = None
            _LOG.warning(
                "rating rename mtime restore failed source=%s destination=%s: %s",
                source, destination, exc,
            )
        return RatingRenameResult(
            source,
            destination,
            normalized_rating,
            True,
            changed=True,
            original_mtime_ns=source_stat.st_mtime_ns,
            final_mtime_ns=final_mtime_ns,
        )


__all__ = ["RatingRenameResult", "RatingRenameService"]
