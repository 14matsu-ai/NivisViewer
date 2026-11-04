from threading import Event

import pytest

from app.thumbnail_provider import BrowserThumbnailProvider
from tests.test_thumbnail_admission import pixels, source


@pytest.mark.parametrize('action', ['viewport', 'pause'])
def test_cancel_then_same_identity_requeue_rejects_old_handoff(tmp_path, qapp, action):
    running = source(tmp_path / 'running.png')
    target = source(tmp_path / 'target.png')
    entered, release = Event(), Event()
    loaded = []

    def loader(item, _size, **_kwargs):
        loaded.append(item.path)
        if item.path == running.path:
            entered.set()
            assert release.wait(5)
        return pixels()

    provider = BrowserThumbnailProvider(loader=loader, max_workers=1, disk_cache_enabled=False)
    ready = []
    provider.thumbnail_ready.connect(lambda path, *_: ready.append(path))
    try:
        assert provider.request(running, 64)
        assert entered.wait(2)
        assert provider.request(target, 64)
        key = (provider._path_key(target.path), 64, provider.generation)
        old = provider._pending[key].worker
        receipt = old.handoff_receipt()
        runner = provider._pending[(provider._path_key(running.path), 64, provider.generation)].worker
        if action == 'viewport':
            assert provider.cancel_requests_except(
                {str(running.path)}, size=64, generation=provider.generation,
            ) == 1
        else:
            provider.set_paused(True)
            provider.set_paused(False)
        assert old.cancelled.is_set()
        assert not runner.cancelled.is_set()
        assert provider.request(target, 64)
        replacement = provider._pending[key].worker
        assert replacement is not old
        assert provider._handoff_worker(receipt) is None
        assert provider._requests.remove_owned_request(key, old) is None
        assert provider._pending[key].worker is replacement
        release.set()
        assert provider.wait_for_done(3000)
        qapp.processEvents()
        assert loaded.count(target.path) == 1
        assert ready.count(str(target.path)) == 1
        assert not provider.pending_count
    finally:
        release.set()
        provider.close(wait_msecs=3000)
        qapp.processEvents()
