"""Metadata-only admission checks; never open cloud placeholder contents."""
from __future__ import annotations

import os
from pathlib import Path

from .i18n import tr

OFFLINE = 0x1000
RECALL_ON_OPEN = 0x40000
RECALL_ON_DATA_ACCESS = 0x400000


def requires_download(attributes: int) -> bool:
    # UNPINNED and REPARSE_POINT alone do not mean data is missing.
    return bool(attributes & (OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS))


def online_only_message() -> str:
    return tr('オンラインのみの項目です。Dropboxなどの同期アプリでダウンロードしてから、一覧を更新してください。')


class OnlineOnlyError(OSError):
    def __init__(self, path: str | Path):
        super().__init__(online_only_message())
        self.path = path


def is_online_only(path: str | Path) -> bool:
    if os.name != 'nt':
        return False
    # lstat reads attributes, not the data stream, and does not follow links.
    attributes = getattr(os.lstat(path), 'st_file_attributes', 0)
    return requires_download(attributes)


def require_local(path: str | Path) -> None:
    if is_online_only(path):
        raise OnlineOnlyError(path)


def local_image_candidate(path: str | Path) -> bool:
    try:
        return not is_online_only(path)
    except OSError:
        return False


def walk_local_files(folder: Path):
    """Never descend into a cloud-only directory or a symbolic link."""
    require_local(folder)
    with os.scandir(folder) as entries:
        for entry in entries:
            if not local_image_candidate(entry.path) or entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                yield from walk_local_files(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                yield Path(entry.path)
