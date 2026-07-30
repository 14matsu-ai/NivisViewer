from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QGuiApplication

from .file_operation_artifact import FileOperationArtifactPolicy

INTERNAL_CLIPBOARD_MARKER_PROPERTY = "_nivisviewer_clipboard_marker"
WINDOWS_PREFERRED_DROP_EFFECT_MIME = (
    'application/x-qt-windows-mime;value="Preferred DropEffect"'
)


class InternalClipboardOperation(str, Enum):
    COPY = "copy"
    CUT = "cut"


@dataclass(frozen=True)
class InternalClipboardSnapshot:
    operation: InternalClipboardOperation
    paths: tuple[str, ...]
    request_identity: str
    generation: int

    @property
    def is_cut(self) -> bool:
        return self.operation is InternalClipboardOperation.CUT


class InternalClipboardState:
    """Path-identity clipboard state owned by NivisViewer.

    The custom MIME identity keeps a delayed Qt clipboard notification from
    turning an internal CUT into an external COPY.
    """

    def __init__(self) -> None:
        self._snapshot: InternalClipboardSnapshot | None = None
        self._generation = 0

    @property
    def snapshot(self) -> InternalClipboardSnapshot | None:
        return self._snapshot

    @property
    def paths(self) -> tuple[str, ...]:
        return self._snapshot.paths if self._snapshot is not None else ()

    @property
    def is_cut(self) -> bool:
        return bool(self._snapshot and self._snapshot.is_cut)

    @property
    def generation(self) -> int:
        return self._generation

    def replace(
        self,
        paths: tuple[str, ...],
        operation: InternalClipboardOperation,
    ) -> InternalClipboardSnapshot:
        self._generation += 1
        normalized = tuple(
            dict.fromkeys(
                absolute
                for path in paths
                if not FileOperationArtifactPolicy.is_internal_operation_artifact(
                    absolute := self._absolute(path)
                )
            )
        )
        snapshot = InternalClipboardSnapshot(
            operation=operation,
            paths=normalized,
            request_identity=uuid.uuid4().hex,
            generation=self._generation,
        )
        self._snapshot = snapshot
        return snapshot

    def clear(self) -> bool:
        if self._snapshot is None:
            return False
        self._generation += 1
        self._snapshot = None
        return True

    def write_marker(self, mime: QMimeData) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        # A QObject dynamic property identifies the QMimeData instance while
        # NivisViewer owns it without publishing a private Windows clipboard
        # format. Publishing a private format is unnecessary for this
        # process-local source-of-truth and has caused teardown instability in
        # headless Qt clipboard backends.
        mime.setProperty(
            INTERNAL_CLIPBOARD_MARKER_PROPERTY,
            (
                snapshot.request_identity,
                snapshot.generation,
                snapshot.operation.value,
                snapshot.paths,
            ),
        )
        if QGuiApplication.platformName().casefold() == "windows":
            mime.setData(
                WINDOWS_PREFERRED_DROP_EFFECT_MIME,
                (
                    2
                    if snapshot.operation is InternalClipboardOperation.CUT
                    else 1
                ).to_bytes(4, byteorder="little", signed=False),
            )

    def matches_mime(self, mime: QMimeData | None) -> bool:
        snapshot = self._snapshot
        if snapshot is None or mime is None:
            return False
        try:
            marker = mime.property(INTERNAL_CLIPBOARD_MARKER_PROPERTY)
            request_identity, generation, raw_operation, raw_paths = marker
            operation = InternalClipboardOperation(str(raw_operation))
            paths = tuple(self._absolute(path) for path in raw_paths)
            return (
                str(request_identity) == snapshot.request_identity
                and int(generation) == snapshot.generation
                and operation is snapshot.operation
                and paths == snapshot.paths
            )
        except (TypeError, ValueError):
            pass
        expected_effect = (
            "move"
            if snapshot.operation is InternalClipboardOperation.CUT
            else "copy"
        )
        if self.preferred_drop_effect(mime) != expected_effect:
            return False
        mime_paths = tuple(
            dict.fromkeys(
                self._absolute(url.toLocalFile())
                for url in mime.urls()
                if (
                    url.isLocalFile()
                    and url.toLocalFile()
                    and not FileOperationArtifactPolicy.is_internal_operation_artifact(
                        url.toLocalFile()
                    )
                )
            )
        )
        return tuple(self.path_key(path) for path in mime_paths) == tuple(
            self.path_key(path) for path in snapshot.paths
        )

    @staticmethod
    def preferred_drop_effect(mime: QMimeData | None) -> str:
        if mime is None:
            return "unknown"
        preferred_formats = (
            WINDOWS_PREFERRED_DROP_EFFECT_MIME,
            "Preferred DropEffect",
        )
        for mime_format in preferred_formats:
            if not mime.hasFormat(mime_format):
                continue
            raw = bytes(mime.data(mime_format))
            if not raw:
                continue
            value = int.from_bytes(raw[:4], byteorder="little", signed=False)
            if value & 2:
                return "move"
            if value & 1:
                return "copy"
            return f"unknown({value})"
        return "unspecified"

    @staticmethod
    def path_key(path: str | Path) -> str:
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path)))
        ).casefold()

    @staticmethod
    def _absolute(path: str | Path) -> str:
        return os.path.abspath(os.path.normpath(os.fspath(path)))
