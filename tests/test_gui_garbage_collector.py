import gc
from threading import Thread, get_ident
import weakref

from PySide6.QtCore import QObject
from PySide6.QtTest import QTest

from app.gui_garbage_collector import ensure_gui_garbage_collector


def test_worker_allocations_leave_qobject_cycles_for_gui_timer(qapp):
    collector = ensure_gui_garbage_collector()
    assert not gc.isenabled()
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
