"""Close ordering and retry contracts across the Viewer shutdown owner."""

from app.config_manager import ConfigManager
from app.viewer_shutdown import ViewerShutdownPhase
from app.viewer_window import ViewerWindow
from tests.test_application_controller import wait_until


def make_window(tmp_path):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    return ViewerWindow(config_manager=config)


def test_close_hides_before_worker_drain_and_uses_nonblocking_retries(tmp_path, qapp, monkeypatch):
    window = make_window(tmp_path)
    window.show()
    qapp.processEvents()
    release = False
    waits = []
    original = window._pdf_loupe_cache.shutdown

    def drain(wait_msecs):
        assert not window.isVisible()
        waits.append(wait_msecs)
        return original(wait_msecs) if release else False

    monkeypatch.setattr(window._pdf_loupe_cache, 'shutdown', drain)
    try:
        window.close()
        assert not window.isVisible()
        assert window._shutdown_prepared
        assert not waits, 'worker drain ran before the deferred close callback'
        assert wait_until(qapp, lambda: len(waits) >= 2)
        assert set(waits) == {0}
        assert window._shutdown_lifecycle.phase is ViewerShutdownPhase.PDF_LOUPE
        release = True
        assert wait_until(qapp, lambda: window._shutdown_cleanup_complete)
    finally:
        release = True
        assert wait_until(qapp, lambda: window._shutdown_cleanup_complete)


def test_reentrant_shutdown_does_not_reenter_active_step(tmp_path, qapp, monkeypatch):
    window = make_window(tmp_path)
    nested = []
    loupe_calls = []
    original = window._pdf_loupe_cache.shutdown

    def drain(wait_msecs):
        loupe_calls.append(wait_msecs)
        if len(loupe_calls) == 1:
            nested.append(window.prepare_shutdown(wait_msecs=0))
        return original(wait_msecs)

    monkeypatch.setattr(window._pdf_loupe_cache, 'shutdown', drain)
    try:
        assert window.prepare_shutdown(wait_msecs=0)
        assert nested == [False]
        assert loupe_calls == [0]
        assert window._shutdown_lifecycle.phase is ViewerShutdownPhase.COMPLETE
        assert window.prepare_shutdown(wait_msecs=0)
        assert loupe_calls == [0]
    finally:
        window.close()
        qapp.processEvents()


def test_book_drain_retry_does_not_repeat_request_cancellation(tmp_path, qapp, monkeypatch):
    window = make_window(tmp_path)
    session = window.book_session
    drained = False
    shutdown_calls = []
    cancellations = []
    original_shutdown = session.shutdown
    original_clear = window._clear_raster_prefetch_pipeline
    original_complete = type(session).shutdown_complete

    def shutdown(wait_msecs=250):
        shutdown_calls.append(wait_msecs)
        original_shutdown(wait_msecs)

    def clear():
        cancellations.append(True)
        original_clear()

    monkeypatch.setattr(session, 'shutdown', shutdown)
    monkeypatch.setattr(window, '_clear_raster_prefetch_pipeline', clear)
    monkeypatch.setattr(type(session), 'shutdown_complete', property(
        lambda owner: drained if owner is session else original_complete.__get__(owner)
    ))
    try:
        window.close()
        assert window.prepare_shutdown(wait_msecs=0) is False
        count = len(cancellations)
        assert count > 0
        assert window._shutdown_lifecycle.phase is ViewerShutdownPhase.BOOK_DRAIN
        assert window.prepare_shutdown(wait_msecs=0) is False
        assert window.prepare_shutdown(wait_msecs=0) is False
        assert len(cancellations) == count
        assert shutdown_calls == [0]
        drained = True
        assert window.prepare_shutdown(wait_msecs=0)
        assert shutdown_calls == [0]
    finally:
        drained = True
        assert window.prepare_shutdown(wait_msecs=0)
        window.close()
        qapp.processEvents()


def test_service_failure_retries_only_failed_step(tmp_path, qapp, monkeypatch):
    window = make_window(tmp_path)
    archive_calls = []
    pdf_calls = []
    original_archive = window.archive_backend_registry.close
    original_pdf = window.pdfium_service.shutdown

    def archive():
        archive_calls.append(True)
        original_archive()

    def pdf():
        pdf_calls.append(True)
        if len(pdf_calls) == 1:
            raise RuntimeError('injected service cleanup failure')
        return original_pdf()

    monkeypatch.setattr(window.archive_backend_registry, 'close', archive)
    monkeypatch.setattr(window.pdfium_service, 'shutdown', pdf)
    try:
        window.close()
        assert wait_until(qapp, lambda: bool(pdf_calls))
        assert not window.isVisible()
        assert window._shutdown_lifecycle.phase is ViewerShutdownPhase.PDF_SERVICE
        assert wait_until(qapp, lambda: window._shutdown_cleanup_complete)
        assert len(archive_calls) == 1
        assert len(pdf_calls) == 2
    finally:
        assert wait_until(qapp, lambda: window._shutdown_cleanup_complete)
