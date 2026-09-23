from __future__ import annotations

from .browser_workflow_policy import decode_preferred_drop_effect

import os
import uuid
import ctypes
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


@dataclass(frozen=True)
class ClipboardPasteReceipt:
    """Identity of the exact clipboard state used to submit a paste."""

    mime_identity: tuple[int | None, int]
    signature: tuple[tuple[str, ...], str]
    internal_request_identity: str | None
    internal_generation: int | None
    internal_paths: tuple[str, ...]
    privately_owned: bool

    @classmethod
    def capture(
        cls,
        state: InternalClipboardState,
        mime: QMimeData | None,
    ) -> ClipboardPasteReceipt:
        snapshot = state.snapshot if state.matches_mime(mime) else None
        return cls(
            InternalClipboardState.mime_identity(mime),
            InternalClipboardState.mime_signature(mime),
            snapshot.request_identity if snapshot is not None else None,
            snapshot.generation if snapshot is not None else None,
            snapshot.paths if snapshot is not None else (),
            state.owns_mime(mime),
        )

    def matches(self, state: InternalClipboardState, mime: QMimeData | None) -> bool:
        return (
            self.mime_identity == state.mime_identity(mime)
            and self.signature == state.mime_signature(mime)
        )

    def matches_internal_snapshot(self, state: InternalClipboardState) -> bool:
        snapshot = state.snapshot
        return bool(
            self.internal_request_identity
            and snapshot is not None
            and snapshot.request_identity == self.internal_request_identity
            and snapshot.generation == self.internal_generation
            and snapshot.is_cut
        )


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

    def owns_mime(self, mime: QMimeData | None) -> bool:
        """Return true only for the private marker created by this window."""
        snapshot = self._snapshot
        if snapshot is None or mime is None:
            return False
        try:
            request_identity, generation, raw_operation, raw_paths = mime.property(
                INTERNAL_CLIPBOARD_MARKER_PROPERTY
            )
            return (
                str(request_identity) == snapshot.request_identity
                and int(generation) == snapshot.generation
                and InternalClipboardOperation(str(raw_operation)) is snapshot.operation
                and tuple(self._absolute(path) for path in raw_paths) == snapshot.paths
            )
        except (TypeError, ValueError):
            return False

    @classmethod
    def mime_identity(cls, mime: QMimeData | None) -> tuple[int | None, int]:
        if mime is None:
            return (None, 0)
        try:
            import shiboken6

            pointer = int(shiboken6.getCppPointer(mime)[0])
        except (ImportError, RuntimeError, TypeError, IndexError):
            pointer = id(mime)
        sequence: int | None = None
        if os.name == "nt":
            try:
                get_sequence = ctypes.WinDLL("user32", use_last_error=True).GetClipboardSequenceNumber
                get_sequence.argtypes = ()
                get_sequence.restype = ctypes.c_uint32
                sequence = int(get_sequence())
            except (AttributeError, OSError):
                sequence = None
        return (sequence, pointer)

    @classmethod
    def mime_signature(cls, mime: QMimeData | None) -> tuple[tuple[str, ...], str]:
        if mime is None:
            return ((), "unknown")
        paths = tuple(
            dict.fromkeys(
                cls.path_key(url.toLocalFile())
                for url in mime.urls()
                if url.isLocalFile() and url.toLocalFile()
            )
        )
        return (paths, cls.preferred_drop_effect(mime))

    @staticmethod
    def preferred_drop_effect(mime: QMimeData | None) -> str:
        if mime is None:
            return "unknown"
        for name in ('application/x-qt-windows-mime;value="Preferred DropEffect"', "Preferred DropEffect"):
            if mime.hasFormat(name):
                return decode_preferred_drop_effect(bytes(mime.data(name)))
        return "unspecified"

    @staticmethod
    def path_key(path: str | Path) -> str:
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path)))
        ).casefold()

    @staticmethod
    def _absolute(path: str | Path) -> str:
        return os.path.abspath(os.path.normpath(os.fspath(path)))
