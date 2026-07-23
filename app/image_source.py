from __future__ import annotations

import io
import threading
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from natsort import natsorted
from PIL import Image, ImageOps


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".ico"}
ARCHIVE_EXTENSIONS = {".zip", ".cbz"}
BOOK_FILE_EXTENSIONS = frozenset(ARCHIVE_EXTENSIONS | SUPPORTED_EXTENSIONS)


class ImageSourceError(RuntimeError):
    pass


class ImageSource(ABC):
    def __init__(self, source_path: str | Path) -> None:
        self.source_path = Path(source_path)

    @abstractmethod
    def list_images(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def open_image(self, image_id: str) -> Image.Image:
        raise NotImplementedError

    @abstractmethod
    def display_path(self, image_id: str) -> str:
        raise NotImplementedError

    def file_size(self, image_id: str) -> int | None:
        return None

    def close(self) -> None:
        pass


class FolderImageSource(ImageSource):
    def __init__(self, folder_path: str | Path, *, recursive: bool = False, sort_descending: bool = False) -> None:
        super().__init__(folder_path)
        self.recursive = recursive
        self.sort_descending = sort_descending
        if not self.source_path.is_dir():
            raise ImageSourceError(f"フォルダが見つかりません: {self.source_path}")

    def list_images(self) -> list[str]:
        try:
            iterator = self.source_path.rglob("*") if self.recursive else self.source_path.iterdir()
            files = [
                path
                for path in iterator
                if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
            ]
        except OSError as exc:
            raise ImageSourceError(f"フォルダを読み込めません: {self.source_path}") from exc

        return [
            str(path)
            for path in natsorted(
                files,
                key=lambda item: str(item.relative_to(self.source_path)),
                reverse=self.sort_descending,
            )
        ]

    def open_image(self, image_id: str) -> Image.Image:
        try:
            with Image.open(image_id) as image:
                return ImageOps.exif_transpose(image).convert("RGBA")
        except Exception as exc:
            raise ImageSourceError(f"画像を読み込めません: {image_id}") from exc

    def display_path(self, image_id: str) -> str:
        return str(Path(image_id))

    def file_size(self, image_id: str) -> int | None:
        try:
            return Path(image_id).stat().st_size
        except OSError:
            return None


class ZipImageSource(ImageSource):
    def __init__(self, archive_path: str | Path, *, sort_descending: bool = False) -> None:
        super().__init__(archive_path)
        self.sort_descending = sort_descending
        if not self.source_path.is_file():
            raise ImageSourceError(f"書庫が見つかりません: {self.source_path}")

        try:
            self._zip = zipfile.ZipFile(self.source_path, "r")
        except zipfile.BadZipFile as exc:
            raise ImageSourceError(f"ZIP/CBZとして開けません: {self.source_path}") from exc
        except OSError as exc:
            raise ImageSourceError(f"書庫を開けません: {self.source_path}") from exc
        self._lock = threading.RLock()

    def list_images(self) -> list[str]:
        self._display_names: dict[str, str] = {}
        names = []
        for info in self._zip.infolist():
            if info.is_dir() or Path(info.filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            names.append(info.filename)
            self._display_names[info.filename] = self._decode_display_name(info)
        return natsorted(
            names,
            key=lambda name: self._display_names.get(name, name),
            reverse=self.sort_descending,
        )

    def open_image(self, image_id: str) -> Image.Image:
        try:
            with self._lock:
                with self._zip.open(image_id, "r") as file:
                    data = file.read()
            stream: BinaryIO = io.BytesIO(data)
            with Image.open(stream) as image:
                return ImageOps.exif_transpose(image).convert("RGBA")
        except Exception as exc:
            raise ImageSourceError(f"書庫内の画像を読み込めません: {image_id}") from exc

    def display_path(self, image_id: str) -> str:
        display_name = getattr(self, "_display_names", {}).get(image_id, image_id)
        return f"{self.source_path}!/{display_name}"

    def file_size(self, image_id: str) -> int | None:
        try:
            return self._zip.getinfo(image_id).file_size
        except KeyError:
            return None

    def close(self) -> None:
        self._zip.close()

    @staticmethod
    def _decode_display_name(info: zipfile.ZipInfo) -> str:
        if info.flag_bits & 0x800:
            return info.filename
        try:
            return info.filename.encode("cp437").decode("cp932")
        except UnicodeError:
            return info.filename


def create_image_source(
    path: str | Path,
    *,
    recursive_folder: bool = False,
    sort_descending: bool = False,
) -> tuple[ImageSource, str | None]:
    target = Path(path)
    selected_image: str | None = None

    if target.is_dir():
        return FolderImageSource(target, recursive=recursive_folder, sort_descending=sort_descending), None

    suffix = target.suffix.lower()
    if suffix in ARCHIVE_EXTENSIONS:
        return ZipImageSource(target, sort_descending=sort_descending), None

    if target.is_file() and suffix in SUPPORTED_EXTENSIONS:
        source = FolderImageSource(target.parent, recursive=recursive_folder, sort_descending=sort_descending)
        selected_image = str(target)
        return source, selected_image

    raise ImageSourceError(f"対応していない形式です: {target}")
