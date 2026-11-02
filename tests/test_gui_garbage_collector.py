import gc
from threading import Thread, get_ident
import weakref

import pytest
from PySide6.QtCore import QObject, QTimer
from PySide6.QtTest import QTest

from app.gui_garbage_collector import ensure_gui_garbage_collector


@pytest.mark.parametrize('pending_cleanup', [False, True])
def test_worker_allocations_leave_qobject_cycles_for_gui_timer(qapp, monkeypatch, pending_cleanup):
    collector = ensure_gui_garbage_collector()
    assert not gc.isenabled()
    if pending_cleanup:
        retained = [[] for _ in range(40_000)]
        QTimer.singleShot(0, retained.clear)
    gc.collect()
    destroyed_on = []
    main_thread = get_ident()
    probe = QObject()
    probe.cycle = probe
    probe.destroyed.connect(lambda: destroyed_on.append(get_ident()))
    reference = weakref.ref(probe)
    del probe

    def allocate_cycles():
        for _ in range(10_000):
            cycle = []
            cycle.append(cycle)

    worker = Thread(target=allocate_cycles)
    worker.start()
    worker.join(3)
    assert not worker.is_alive()
    assert reference() is not None and not destroyed_on
    # Unrelated deferred cleanup can lower allocation counts below the GC
    # threshold. This test checks collection thread affinity, so make collection
    # due explicitly while retaining the real timer and real cycle collector.
    thresholds = gc.get_threshold()
    monkeypatch.setattr(gc, 'get_count', lambda: thresholds)
    interval = collector.timer.interval()
    collector.timer.setInterval(1)
    try:
        for _ in range(100):
            qapp.processEvents()
            if destroyed_on:
                break
            QTest.qWait(5)
        assert reference() is None
        assert destroyed_on == [main_thread]
    finally:
        collector.timer.setInterval(interval)


def test_window_owners_share_one_application_collector(qapp):
    first = ensure_gui_garbage_collector()
    assert ensure_gui_garbage_collector() is first
    assert first.parent() is qapp
    assert first.timer.isActive()
