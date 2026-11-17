from __future__ import annotations

import logging

from app import freeze_diagnostics
from app.app_paths import resolve_app_paths
from app.memory_trace import GIB, MemoryTrace, describe_memory
from app.viewer_memory_policy import PhysicalMemorySnapshot


def _snapshot(commit_gib: float, working_set_gib: float) -> PhysicalMemorySnapshot:
    return PhysicalMemorySnapshot(
        total_physical_bytes=64 * GIB,
        available_physical_bytes=32 * GIB,
        process_working_set_bytes=int(working_set_gib * GIB),
        available_commit_bytes=100 * GIB,
        total_commit_bytes=128 * GIB,
        process_private_commit_bytes=int(commit_gib * GIB),
    )


class _Source:
    def __init__(self) -> None:
        self.value = _snapshot(1.0, 0.5)
        self.now = 0.0

    def read(self):
        return self.value

    def clock(self) -> float:
        return self.now


def _records(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == 'nivisviewer.memory']


def test_memory_trace_logs_only_on_step_or_heartbeat(qapp, caplog):
    caplog.set_level(logging.INFO, logger='nivisviewer.memory')
    source = _Source()
    trace = MemoryTrace(reader=source.read, clock=source.clock,
                        step_bytes=GIB, heartbeat_seconds=600)
    trace.start()
    try:
        assert trace.active
        source.now = 10
        source.value = _snapshot(1.5, 0.9)
        assert not trace.sample()
        source.now = 20
        source.value = _snapshot(2.6, 0.9)
        assert trace.sample()
        source.now = 30
        source.value = _snapshot(1.2, 0.4)
        assert trace.sample()
        source.now = 700
        assert trace.sample()
    finally:
        trace.stop('test_stop')
    messages = _records(caplog)
    assert [m.split()[2] for m in messages] == [
        'reason=start', 'reason=growth', 'reason=release', 'reason=heartbeat', 'reason=test_stop']
    assert 'commit_gib=2.60 (+1.60)' in messages[1]
    assert 'commit_gib=1.20 (-1.40)' in messages[2]
    assert 'peak_commit_gib=2.60' in messages[-1]
    assert not trace.active


def test_memory_trace_working_set_alone_can_trigger(qapp, caplog):
    caplog.set_level(logging.INFO, logger='nivisviewer.memory')
    source = _Source()
    trace = MemoryTrace(reader=source.read, clock=source.clock, step_bytes=GIB)
    trace.record('start')
    source.value = _snapshot(1.1, 2.0)
    assert trace.sample()
    assert 'reason=growth' in _records(caplog)[-1]


def test_memory_trace_tolerates_unavailable_counters(qapp, caplog):
    caplog.set_level(logging.INFO, logger='nivisviewer.memory')

    def failing():
        raise OSError('unavailable')

    trace = MemoryTrace(reader=failing)
    trace.start()
    assert not trace.sample()
    trace.stop()
    assert _records(caplog) == []
    assert describe_memory(_snapshot(3.0, 1.0)) == 'commit_gib=3.00 working_set_gib=1.00'


def test_exit_watchdog_repeats_and_marks_teardown_phases(tmp_path, qapp, monkeypatch):
    from PySide6.QtCore import QObject, Signal

    class Application(QObject):
        aboutToQuit = Signal()

    application = Application()
    arms = []
    registered = []
    monkeypatch.setattr(freeze_diagnostics, '_EXIT_WATCHDOG_FILE', None)
    monkeypatch.setattr(freeze_diagnostics.faulthandler, 'dump_traceback_later',
                        lambda timeout, **kwargs: arms.append((timeout, kwargs['repeat'])))
    monkeypatch.setattr(freeze_diagnostics.atexit, 'register',
                        lambda function, *args: registered.append((function, args)))
    paths = resolve_app_paths(profile_override=str(tmp_path), source_root=tmp_path)
    watchdog = freeze_diagnostics.FreezeDiagnostics(paths, application)
    application.aboutToQuit.emit()
    watchdog.keep_during_exit()
    handle = freeze_diagnostics._EXIT_WATCHDOG_FILE
    try:
        # Runtime heartbeats stay one-shot; only the post-loop watchdog repeats.
        assert arms[:-1] == [(5, False), (5, False)]
        assert arms[-1] == (freeze_diagnostics.EXIT_DUMP_INTERVAL_SECONDS, True)
        assert registered == [(freeze_diagnostics.mark_exit_phase, ('Python atexit',))]
        freeze_diagnostics.mark_exit_phase('main returned')
        function, args = registered[0]
        function(*args)
        log = next(paths.logs_dir.glob('freeze-*.log')).read_text(encoding='utf-8')
        lines = [line for line in log.splitlines() if ' at=' in line]
        assert [line.split(' at=')[0] for line in lines] == [
            'Qt aboutToQuit', 'Qt event loop returned', 'main returned', 'Python atexit']
        assert all('commit_gib=' in line for line in lines)
    finally:
        handle.close()
    # A closed or missing handle is ignored instead of raising at teardown.
    freeze_diagnostics.mark_exit_phase('after close')
