"""Identity captured before handing a path to a filesystem operation."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    mode: int
    attributes: int
    size: int
    modified_ns: int
    changed_ns: int

    @classmethod
    def from_stat(cls, info: os.stat_result) -> FileIdentity:
        return cls(info.st_dev, info.st_ino, info.st_mode,
                   getattr(info, 'st_file_attributes', 0), info.st_size,
                   info.st_mtime_ns, info.st_ctime_ns)

    @property
    def is_directory(self) -> bool:
        return stat.S_ISDIR(self.mode)

    @property
    def is_normal(self) -> bool:
        return bool(self.inode and not self.attributes & 0x400
                    and (self.is_directory or stat.S_ISREG(self.mode)))

    def still_at(self, path: str) -> bool:
        try:
            return self == self.from_stat(os.lstat(path))
        except OSError:
            return False

    def same_object_at(self, path: str) -> bool:
        try:
            current = self.from_stat(os.lstat(path))
        except OSError:
            return False
        return (current.is_normal and self.is_directory == current.is_directory
                and (self.device, self.inode) == (current.device, current.inode))
