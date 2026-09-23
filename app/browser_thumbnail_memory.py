"""Application-wide Browser RAM grants; never schedules image jobs.

The existing BrowserWorkflowController remains the only background producer.
The existing provider remains the only owner of thumbnail cache pixels.
"""
from __future__ import annotations

from time import monotonic
import weakref

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from .browser_thumbnail_memory_policy import (
    BrowserMemoryGovernor, MemoryDemand, SAMPLE_INTERVAL_SECONDS,
)
from .viewer_memory_policy import read_physical_memory_snapshot


class BrowserThumbnailMemoryBroker(QObject):
    """One pressure sample and one allocation for all Browser windows."""
    def __init__(self, parent=None, *, reader=read_physical_memory_snapshot,
                 clock=monotonic) -> None:
        super().__init__(parent)
        self._reader = reader
        self._clock = clock
        self._clients: dict[int, weakref.ReferenceType] = {}
        self._governor = BrowserMemoryGovernor()
        self._sampled = False
        self._updating = False
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(int(SAMPLE_INTERVAL_SECONDS * 1000))
        self._timer.timeout.connect(self.sample_now)
        quitting = getattr(parent, "aboutToQuit", None)
        if quitting is not None:
            quitting.connect(self.close)

    @classmethod
    def for_application(cls) -> BrowserThumbnailMemoryBroker:
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("Browser thumbnail budgets require QApplication")
        broker = getattr(app, "_nivis_browser_thumbnail_memory_broker", None)
        if broker is None:
            broker = cls(app)
            app._nivis_browser_thumbnail_memory_broker = broker
        return broker

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._clients.clear()

    def register(self, client) -> None:
        if self._closed:
            return
        identity = id(client)
        self._clients[identity] = weakref.ref(client)
        client.destroyed.connect(lambda *_args, key=identity: self.unregister(key))
        if not self._timer.isActive():
            self._timer.start()
        if not self._sampled:
            self.sample_now()
        else:
            self.rebalance()

    def unregister(self, identity: int) -> None:
        self._clients.pop(identity, None)
        if self._closed:
            return
        if not self._clients:
            self._timer.stop()
            # Do not carry a stale allowance into a later Browser session.
            self._sampled = False
            self._governor = BrowserMemoryGovernor()
        else:
            self.rebalance()

    def _live_clients(self) -> dict[int, object]:
        result = {}
        for identity, reference in tuple(self._clients.items()):
            client = reference()
            try:
                retired = client is None or client.window._shutdown_prepared
                if not retired:
                    client.window.isVisible()  # Also detects deleted Qt wrappers.
            except RuntimeError:
                retired = True
            if retired:
                self._clients.pop(identity, None)
            else:
                result[identity] = client
        if not result:
            self._timer.stop()
            self._sampled = False
            self._governor = BrowserMemoryGovernor()
        return result

    def sample_now(self) -> None:
        if self._closed:
            return
        clients = self._live_clients()
        if not clients:
            return
        retained = sum(client.window.thumbnail_provider.memory_cache_bytes
                       for client in clients.values())
        try:
            snapshot = self._reader()
        except Exception:
            snapshot = None
        self._governor.sample(snapshot, retained)
        self._sampled = True
        self.rebalance()

    def rebalance(self) -> None:
        if self._closed or self._updating:
            return
        self._updating = True
        try:
            clients = self._live_clients()
            demands = {}
            for identity, client in clients.items():
                window = client.window
                visible = bool(window.isVisible() and not window.isMinimized())
                demands[identity] = MemoryDemand(
                    str(client.options.get("browser_thumbnail_memory_mode", "auto")),
                    client._memory_near_bytes,
                    visible,
                )
            grants = self._governor.resolve(demands, self._clock())
            # Reclaim first: two clients must not momentarily both spend an
            # allowance that has just been transferred from one to the other.
            for identity in sorted(clients, key=lambda key:
                    grants[key].limit_bytes - clients[key].window.thumbnail_provider.memory_cache_limit_bytes):
                clients[identity]._apply_memory_grant(grants[identity])
        finally:
            self._updating = False
