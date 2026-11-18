from __future__ import annotations

import logging
from threading import Event
from time import sleep

from PySide6.QtCore import QRunnable

from app import application_shutdown, thumbnail_provider as tp
from app.browser_model import BrowserItem, BrowserItemKind
from app.browser_thumbnail_scheduler import ThumbnailPriority
from app.thumbnail_requests import PendingThumbnail


def _stuck_worker(tmp_path, name='stuck.png', *, cache_probe=True):
    path = tmp_path / name
    item = BrowserItem(path.name, path, BrowserItemKind.IMAGE, 0.0)
    worker = tp._ThumbnailWorker(item, 16, 0, None, ThumbnailPriority.VISIBLE)
    worker.cache_probe = cache_probe
    # Looks started, so close() cannot take it back from a queue; nothing will
    # ever deliver its completion (the state seen in the hours-long hang).
    worker.run_started.set()
    return worker


def _add_pending(provider, worker):
    key = (provider._path_key(worker.item.path), 16, worker.generation)
    with provider._pending_lock:
        provider._pending[key] = PendingThumbnail(worker, worker.priority)
    return key


def _retire_with_stuck_record(tmp_path):
    provider = tp.BrowserThumbnailProvider()
    worker = _stuck_worker(tmp_path)
    _add_pending(provider, worker)
    provider.close(wait_msecs=0)
    assert provider in tp._RETIRED_THUMBNAIL_PROVIDERS
    return provider, worker


def test_retired_provider_releases_stranded_records_after_grace(tmp_path, qapp, monkeypatch, caplog):
    monkeypatch.setattr(tp, 'RETIRED_STRANDED_GRACE_SECONDS', 0.05)
    caplog.set_level(logging.WARNING, logger='nivisviewer.thumbnail')
    provider, worker = _retire_with_stuck_record(tmp_path)
    try:
        assert provider.shutdown_snapshot()['handoff'] == 1
        # First idle observation only starts the grace period for queued signals.
        assert not provider._try_finalize_retired()
        assert provider in tp._RETIRED_THUMBNAIL_PROVIDERS
        sleep(0.08)
        assert provider._try_finalize_retired()
        assert provider not in tp._RETIRED_THUMBNAIL_PROVIDERS
        assert worker.cancelled.is_set()
        assert provider.pending_count == 0
        messages = [r.getMessage() for r in caplog.records
                    if 'released stranded requests' in r.getMessage()]
        assert len(messages) == 1
        assert "'handoff': 1" in messages[0] and "'kind': 'probe'" in messages[0]
        assert str(tmp_path) not in messages[0] and 'stuck.png' not in messages[0]
    finally:
        tp._RETIRED_THUMBNAIL_PROVIDERS.discard(provider)


def test_stranded_release_waits_while_native_work_is_active(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(tp, 'RETIRED_STRANDED_GRACE_SECONDS', 0.0)
    provider = tp.BrowserThumbnailProvider()
    _add_pending(provider, _stuck_worker(tmp_path))
    entered, release = Event(), Event()

    class SlowSave(QRunnable):
        def run(self):
            entered.set()
            release.wait(10)

    provider._save_pool.start(SlowSave())
    try:
        assert entered.wait(3)
        provider.close(wait_msecs=0)
        assert not provider._native_work_idle()
        for _ in range(3):
            assert not provider._try_finalize_retired()
        assert provider in tp._RETIRED_THUMBNAIL_PROVIDERS
        release.set()
        assert provider._save_pool.waitForDone(3000)
        assert not provider._try_finalize_retired()  # starts the grace period
        assert provider._try_finalize_retired()
        assert provider not in tp._RETIRED_THUMBNAIL_PROVIDERS
    finally:
        release.set()
        tp._RETIRED_THUMBNAIL_PROVIDERS.discard(provider)


def test_stranded_release_returns_unstarted_generation_reservation(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(tp, 'RETIRED_STRANDED_GRACE_SECONDS', 0.0)
    released = []
    monkeypatch.setattr(tp.BROWSER_GENERATION_BUDGET, 'release', released.append)
    provider, _stuck = _retire_with_stuck_record(tmp_path)
    waiting = _stuck_worker(tmp_path, 'waiting.png', cache_probe=False)
    waiting.run_started.clear()
    waiting.generation_reserved = True
    waiting.generation_cost = 123
    provider._waiting_generation[waiting] = _add_pending(provider, waiting)
    try:
        state = provider.shutdown_snapshot()
        assert state['waiting_generation'] == 1 and state['native_idle']
        assert not provider._try_finalize_retired()
        assert provider._try_finalize_retired()
        assert released == [123]
        assert not waiting.generation_reserved
        assert not provider._waiting_generation
    finally:
        tp._RETIRED_THUMBNAIL_PROVIDERS.discard(provider)


def test_application_shutdown_wait_log_interval_widens(qapp, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='nivisviewer.application_shutdown')
    clock = [100.0]
    monkeypatch.setattr(application_shutdown, 'monotonic', lambda: clock[0])
    coordinator = application_shutdown.ApplicationShutdownCoordinator()
    coordinator.configure((('stuck', lambda: False),), diagnostics=lambda: {'n': 1})
    for elapsed in range(0, 700):
        clock[0] = 100.0 + elapsed
        assert not coordinator.begin_shutdown()
    waits = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith('Application shutdown waiting')]
    # 0, 5, 35, 335, 635 seconds: five lines instead of one every 5 seconds.
    assert [float(m.split('elapsed_ms=')[1].split()[0]) for m in waits] == [
        0.0, 5000.0, 35000.0, 335000.0, 635000.0]
    failures = [r for r in caplog.records if 'step failed' in r.getMessage()]
    assert len(failures) == 1
