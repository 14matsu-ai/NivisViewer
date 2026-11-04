"""Slow background work must not turn a metadata action into a GUI wait."""
from threading import Event, Timer

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from app.browser_image_detail import _ProbeWorker
from app.zippla_filename_metadata import ZipPlaFilenameMetadata
from tests.test_browser_tags import library, load, wait_until, write_image


@pytest.mark.parametrize('edit', ['rating', 'tag'])
@pytest.mark.parametrize('folder', [False, True])
def test_metadata_edit_rejects_busy_probe_without_waiting_then_retries(
    library, qapp, monkeypatch, edit, folder,
):
    controller, browser, root = library
    source = root / ('画像フォルダ' if folder else '画像.png')
    if folder:
        source.mkdir()
    image_path = source / '画像.png' if folder else source
    write_image(image_path)
    load(browser, root)
    assert wait_until(qapp, lambda: browser.thumbnail_provider.pending_count == 0)
    assert browser.image_detail_probe.close(5000)
    entered, release = Event(), Event()
    original_run = _ProbeWorker.run

    def blocked_probe(worker):
        entered.set()
        release.wait(5)
        original_run(worker)

    monkeypatch.setattr(_ProbeWorker, 'run', blocked_probe)
    browser.image_detail_probe.request(image_path, browser._detail_generation)
    assert entered.wait(2)
    metadata = ZipPlaFilenameMetadata.parse(source)
    if edit == 'rating':
        metadata = metadata.with_rating(3)
        apply = lambda: browser.set_rating_for_paths((str(source),), 3)
    else:
        metadata = metadata.with_tag_changes({'新': True})
        apply = lambda: browser.set_tags_for_paths((str(source),), {'新': True})
    target = metadata.serialized_path(is_directory=folder)
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    # A regression must fail rather than hang the suite indefinitely. The
    # assertion checks ownership, not a fragile wall-clock speed threshold.
    watchdog = Timer(2, release.set)
    watchdog.start()
    try:
        assert not apply()
        assert not release.is_set(), 'Metadata action waited for the probe'
        assert source.exists() and not target.exists()
        assert '待機中' in QMessageBox.warning.call_args.args[2]
        qapp.processEvents()
        assert heartbeat and not release.is_set()
        release.set()
        assert browser.image_detail_probe.close(5000)
        qapp.processEvents()
        assert apply()
        assert wait_until(qapp, lambda: target.exists() and not source.exists())
        assert wait_until(qapp, lambda: browser._rating_batch is None
                          and not controller.file_operation_queue.busy, timeout=5)
    finally:
        release.set()
        watchdog.cancel()
        watchdog.join()
        browser.image_detail_probe.close(5000)
