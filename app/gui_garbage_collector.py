"""Collect Python reference cycles on the Qt application thread."""

import gc

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer


class GuiGarbageCollector(QObject):
    def __init__(self, application: QCoreApplication) -> None:
        super().__init__(application)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.collect_if_due)
        self.timer.start()

    def collect_if_due(self) -> None:
        counts = gc.get_count()
        thresholds = gc.get_threshold()
        if thresholds[0] <= 0 or counts[0] < thresholds[0]:
            return
        generation = 0
        if counts[1] >= thresholds[1]:
            generation = 1
            if counts[2] >= thresholds[2]:
                generation = 2
        gc.collect(generation)


def ensure_gui_garbage_collector() -> GuiGarbageCollector:
    application = QCoreApplication.instance()
    if application is None or QThread.currentThread() != application.thread():
        raise RuntimeError('GUI cycle collection must be installed on the application thread')
    collector = getattr(application, '_nivis_gui_garbage_collector', None)
    if collector is None:
        # An allocation on any Python worker can otherwise collect cycles
        # containing GUI-created QObject wrappers. Concurrent parent/child
        # destruction can then invert Qt's QObject locks and the GIL.
        restore_automatic = gc.isenabled()
        gc.disable()
        collector = GuiGarbageCollector(application)
        application._nivis_gui_garbage_collector = collector
        if restore_automatic:
            application.destroyed.connect(_restore_automatic_collection)
    return collector


def _restore_automatic_collection() -> None:
    gc.enable()
