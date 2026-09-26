"""Real directory watcher/scan regressions with synthetic readable growing PNGs."""
from collections import Counter
import json
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QAbstractItemView
from PySide6.QtTest import QTest
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.thumbnail_provider import BrowserThumbnailProvider
from tests.test_browser_download_recovery_qt import write_png,wait_until,close,blue

@pytest.mark.parametrize('outside',[False,True])
def test_readable_growing_file_does_not_restart_directory(tmp_path,qapp,outside):
    folder=tmp_path/'downloads';folder.mkdir()
    target=folder/'000-growing.png';write_png(target)
    for i in range(1,40):write_png(folder/f'{i:03}.png')
    config=ConfigManager(tmp_path/'config.json');config.load()
    config.apply({'last_browser_path':str(folder),'browser_thumbnail_background_screens':0})
    calls=Counter()
    def decode(item,size):
        calls[item.path.name]+=1
        return BrowserThumbnailProvider.load_thumbnail(item,size)
    provider=BrowserThumbnailProvider(loader=decode,disk_cache_enabled=False)
    window=BrowserWindow(config_manager=config,thumbnail_provider=provider)
    window.resize(500,360);window.show()
    timer=QTimer();timer.setInterval(100)
    ticks=[0];scans=[];messages=[]
    added=folder/'001-new.png'
    def append():
        with target.open('ab') as stream:stream.write(b'additional-download-bytes')
        ticks[0]+=1
        if ticks[0]==8:write_png(added)
        if ticks[0]==27:timer.stop()
    timer.timeout.connect(append)
    try:
        assert window.wait_for_scan()
        wait_until(qapp,lambda:blue(window,target) and provider.pending_count==0)
        if outside:
            window.list_view.scrollTo(window.item_model.index(35),QAbstractItemView.ScrollHint.PositionAtTop)
            QTest.qWait(300)
        QTest.qWait(500)
        before=Counter(calls);generation=provider.generation
        window.directory_scan_committed.connect(lambda *_:scans.append(1))
        window.statusBar().messageChanged.connect(lambda message:messages.append(message))
        timer.start()
        wait_until(qapp,lambda:not timer.isActive(),timeout=5000)
        during=Counter(calls)
        assert window.item_model.row_for_path(added)>=0, "new files must appear during writes"
        wait_until(qapp,lambda:window.item_model.row_for_path(added)>=0)
        wait_until(qapp,lambda:not window._download_retry._changing and window._pending_scan is None,timeout=5000)
        if not outside:wait_until(qapp,lambda:calls[target.name]>before[target.name])
        observed={'outside':outside,'scans':len(scans),'generations':provider.generation-generation,
                  'loading_messages':sum('読み込み中' in x for x in messages),
                  'target_during':during[target.name]-before[target.name],
                  'target_total':calls[target.name]-before[target.name],
                  'unrelated':sum(max(0,calls[k]-before[k]) for k in before if k!=target.name)}
        print('GROWING_OBSERVATION',json.dumps(observed))
        assert observed['generations']==0
        assert observed['loading_messages']==0
        assert observed['target_during']<=1
        assert observed['unrelated']==0
        if not outside:assert observed['target_total']>=1
    finally:
        timer.stop();close(window,provider,qapp)


def test_changed_source_fences_running_and_queued_cached_results(tmp_path,qapp):
    from threading import Event
    from PySide6.QtGui import QImage
    from tests.test_browser_download_refresh import item_for
    path=tmp_path/'changing.png';write_png(path)
    original=item_for(path)
    started=Event();release=Event()
    def decode(*args):
        started.set()
        assert release.wait(3)
        image=QImage(16,16,QImage.Format.Format_RGB32);image.fill(0xFF0000FF)
        return image
    provider=BrowserThumbnailProvider(loader=decode,disk_cache_enabled=False)
    ready=[];counts=[]
    provider.thumbnail_ready.connect(lambda *args:ready.append(args))
    provider.page_count_ready.connect(lambda *args:counts.append(args))
    try:
        generation=provider.begin_generation()
        assert provider.request(original,80,generation=generation)
        wait_until(qapp,started.is_set)
        worker=next(iter(provider._pending.values())).worker
        with path.open('ab') as stream:stream.write(b'changed')
        changed=item_for(path)
        provider.reconcile_items([original],[changed])
        worker.signals.page_count_discovered.emit(str(path),generation,99)
        release.set()
        assert provider.wait_for_done(3000);qapp.processEvents()
        assert not ready and not counts
        assert provider.request(changed,80,generation=generation)
        assert provider.wait_for_done(3000);qapp.processEvents()
        assert len(ready)==1
        ready.clear()
        provider.request(changed,80,generation=generation)  # queued memory hit
        provider.reconcile_items([changed],[])
        qapp.processEvents()
        assert not ready
        provider.reconcile_items([],[changed])  # same-name reappearance
        provider.request(changed,80,generation=generation)
        qapp.processEvents()
        assert len(ready)==1
    finally:
        release.set();provider.close()


def test_changed_queued_source_releases_owner_and_preserves_running_neighbor(tmp_path,qapp):
    from threading import Event
    from PySide6.QtGui import QImage
    from tests.test_browser_download_refresh import item_for
    first=tmp_path/'neighbor.png';second=tmp_path/'growing.png'
    write_png(first);write_png(second)
    neighbor=item_for(first);original=item_for(second)
    started=Event();release=Event()
    def decode(*args):
        started.set();assert release.wait(3)
        image=QImage(16,16,QImage.Format.Format_RGB32);image.fill(0xFF0000FF)
        return image
    provider=BrowserThumbnailProvider(loader=decode,disk_cache_enabled=False)
    provider._pool.setMaxThreadCount(1)
    settled=[];ready=[]
    provider.work_settled.connect(lambda *args:settled.append(args))
    provider.thumbnail_ready.connect(lambda *args:ready.append(args))
    try:
        generation=provider.begin_generation()
        assert provider.request(neighbor,80,generation=generation)
        wait_until(qapp,started.is_set)
        assert provider.request(original,80,generation=generation)
        with second.open('ab') as stream:stream.write(b'changed')
        changed=item_for(second)
        provider.reconcile_items([neighbor,original],[neighbor,changed])
        assert len(settled)==1 and settled[0][0]==str(second) and settled[0][-1]=='cancelled'
        assert provider.pending_count==1
        assert not next(iter(provider._pending.values())).worker.cancelled.is_set()
        provider.defer_changing_item(changed)
        assert provider.request_background(changed,80,generation=generation)=='settled'
        release.set();assert provider.wait_for_done(3000);qapp.processEvents()
        assert len(ready)==1 and ready[0][0]==str(first)
    finally:
        release.set();provider.close()


def test_cached_page_count_is_fenced_when_revision_changes_without_generation(tmp_path,qapp):
    from dataclasses import replace
    from tests.test_browser_download_refresh import item_for
    path=tmp_path/'book.zip';path.write_bytes(b'old')
    old=replace(item_for(path),page_count=12)
    provider=BrowserThumbnailProvider(disk_cache_enabled=False)
    counts=[]
    provider.page_count_ready.connect(lambda *args:counts.append(args))
    try:
        generation=provider.begin_generation()
        provider.request_page_count(old,generation=generation)
        path.write_bytes(b'new contents longer')
        current=replace(item_for(path),page_count=None)
        provider.reconcile_items([old],[current])
        qapp.processEvents()
        assert not counts  # previously queued old count must not publish
        assert not provider.request_page_count(old,generation=generation)
        qapp.processEvents()
        assert not counts  # stale callers must not queue another notification
        assert not provider.request_page_count(replace(old,page_count=None),generation=generation)
        assert provider.pending_count==0  # stale callers must not start I/O either
        provider.request_page_count(replace(current,page_count=23),generation=generation)
        qapp.processEvents()
        assert counts==[(str(path),generation,23)]
    finally:
        provider.close()
