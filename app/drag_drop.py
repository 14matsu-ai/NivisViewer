from __future__ import annotations

import json
import ntpath
import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from PySide6.QtCore import QObject, QRunnable, QMimeData, QUrl, Qt, Signal, Slot

from .image_source import ARCHIVE_EXTENSIONS, PDF_EXTENSIONS, SUPPORTED_EXTENSIONS


NIVIS_PATHS_MIME = "application/x-nivisviewer-paths+json"
MAX_DROP_PATHS = 256
VIEWER_FILE_EXTENSIONS = (
    set(SUPPORTED_EXTENSIONS) | set(ARCHIVE_EXTENSIONS) | set(PDF_EXTENSIONS)
)


def normalize_local_paths(
    paths: list[str] | tuple[str, ...],
    *,
    maximum: int = MAX_DROP_PATHS,
) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in paths[: max(0, int(maximum))]:
        text = str(raw).strip().strip('"')
        if not text or "://" in text:
            continue
        path = Path(text)
        if not path.is_absolute():
            continue
        normalized = os.path.abspath(os.path.normpath(os.fspath(path)))
        key = os.path.normcase(normalized).casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return tuple(output)


def build_path_mime_data(paths: tuple[str, ...], *, source: str = "browser") -> QMimeData:
    normalized = normalize_local_paths(paths)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(path) for path in normalized])
    payload = json.dumps(
        {"version": 1, "paths": list(normalized), "source": source},
        ensure_ascii=False,
    ).encode("utf-8")
    mime.setData(NIVIS_PATHS_MIME, payload)
    return mime


def paths_from_mime_data(mime: QMimeData) -> tuple[str, ...]:
    candidates: list[str] = []
    if mime.hasFormat(NIVIS_PATHS_MIME):
        try:
            payload = json.loads(bytes(mime.data(NIVIS_PATHS_MIME)).decode("utf-8"))
            if payload.get("version") == 1 and isinstance(payload.get("paths"), list):
                candidates.extend(
                    value for value in payload["paths"] if isinstance(value, str)
                )
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            pass
    if mime.hasUrls():
        candidates.extend(
            url.toLocalFile()
            for url in mime.urls()
            if url.isLocalFile() and url.scheme().casefold() == "file"
        )
    return normalize_local_paths(candidates)


def is_lexically_supported_viewer_path(path: str | Path) -> bool:
    suffix = Path(path).suffix.casefold()
    return not suffix or suffix in VIEWER_FILE_EXTENSIONS


def viewer_drop_paths(mime: QMimeData) -> tuple[str, ...]:
    return tuple(
        path for path in paths_from_mime_data(mime)
        if is_lexically_supported_viewer_path(path)
    )


def windows_volume_root(path: str | Path) -> str | None:
    text = os.fspath(path).replace("/", "\\")
    drive, _tail = ntpath.splitdrive(text)
    if drive:
        if drive.startswith("\\\\"):
            parts = [part for part in drive.split("\\") if part]
            return (
                f"\\\\{parts[0].casefold()}\\{parts[1].casefold()}"
                if len(parts) >= 2
                else None
            )
        return drive.casefold()
    try:
        pure = PureWindowsPath(text)
        if pure.drive:
            return pure.drive.casefold()
    except ValueError:
        pass
    return None


def choose_drop_operation(
    sources: tuple[str, ...],
    destination: str | Path,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> str:
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        return "copy"
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        return "move"
    destination_volume = windows_volume_root(destination)
    source_volumes = {windows_volume_root(path) for path in sources}
    if (
        destination_volume is not None
        and source_volumes == {destination_volume}
    ):
        return "move"
    return "copy"


def is_invalid_drop_target(source: str | Path, destination: str | Path) -> bool:
    source_key = os.path.normcase(
        os.path.abspath(os.path.normpath(os.fspath(source)))
    ).casefold()
    destination_key = os.path.normcase(
        os.path.abspath(os.path.normpath(os.fspath(destination)))
    ).casefold()
    if source_key == destination_key:
        return True
    return destination_key.startswith(source_key.rstrip("\\/") + os.sep.casefold())


@dataclass
class ExplorerSelectionController:
    press_row: int = -1
    blank_press: bool = False
    shift_rubber_band: bool = False
    press_modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier

    def begin(self, row: int, modifiers: Qt.KeyboardModifier) -> None:
        self.press_row = int(row)
        self.press_modifiers = modifiers
        self.blank_press = row < 0
        self.shift_rubber_band = self.blank_press and bool(
            modifiers & Qt.KeyboardModifier.ShiftModifier
        )


class FileDragController:
    @staticmethod
    def mime_data(paths: tuple[str, ...]) -> QMimeData:
        return build_path_mime_data(paths, source="browser")


class FolderDropProbeSignals(QObject):
    finished = Signal(object)


class FolderDropProbe(QRunnable):
    """Checks dropped paths off the GUI thread."""

    def __init__(self, paths: tuple[str, ...]) -> None:
        super().__init__()
        self.paths = paths
        self.signals = FolderDropProbeSignals()

    @Slot()
    def run(self) -> None:
        folders: list[str] = []
        for path in self.paths:
            try:
                if Path(path).is_dir():
                    folders.append(path)
            except OSError:
                continue
        self.signals.finished.emit(tuple(folders))
