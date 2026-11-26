"""Capture the identity of a delete selection without doing UI-thread I/O."""
from __future__ import annotations

import os
from queue import Empty, SimpleQueue
from threading import Event, Thread

from PySide6.QtCore import QObject, QTimer, Signal

from .file_operation_identity import FileIdentity


def capture_recycle_sources(paths, cancelled):
    captured = {}
    for path in paths:
        if cancelled.is_set():
            break
        path = os.path.abspath(path)
        key = os.path.normcase(path)
        if key in captured:
            continue
        try:
            identity = FileIdentity.from_stat(os.lstat(path))
        except (OSError, ValueError):
            identity = None
        captured[key] = (path, identity if identity and identity.is_normal else None)
    return tuple(captured.values())


def _capture(paths, token, mailbox):
    # No Qt objects or GUI ownership in this thread. A stuck filesystem
    # query cannot make Qt's application/thread-pool teardown wait for it.
    mailbox.put(capture_recycle_sources(paths, token))


class RecycleSelection(QObject):
    captured = Signal(object, object)

    def __init__(self, parent):
        super().__init__(parent)
        self._token = None
        self._mailbox = None
        self._poll = QTimer(self)
        self._poll.setInterval(20)
        self._poll.timeout.connect(self._deliver)

    @property
    def pending(self):
        return self._token is not None

    def start(self, paths):
        if not paths or self.pending:
            return False
        self._token = Event()
        self._mailbox = SimpleQueue()
        Thread(target=_capture, args=(paths, self._token, self._mailbox),
               daemon=True, name='recycle-selection').start()
        self._poll.start()
        return True

    def cancel(self):
        if self._token is not None:
            self._token.set()
            self._token = None
            self._mailbox = None
        self._poll.stop()

    def is_current(self, token):
        return token is self._token

    def _deliver(self):
        token, mailbox = self._token, self._mailbox
        if mailbox is None:
            return
        try:
            sources = mailbox.get_nowait()
        except Empty:
            return
        self._poll.stop()
        try:
            self.captured.emit(token, sources)
        finally:
            if self._token is token:
                self._token = None
                self._mailbox = None
