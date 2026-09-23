"""Actual Browser/Qt regressions. Run only with QT_QPA_PLATFORM=offscreen.

All content is generated under pytest's temporary directory. No real app entry
point, downloaded user content, Explorer, native input, or external GUI is used.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch
import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PIL import Image
from app.browser_directory_watcher import BrowserDirectoryChange, BrowserDirectoryWatcher
from app.browser_download_policy import DownloadRetryBudget
from app.browser_model import BrowserItemKind
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.browser_workflow_policy import ThumbnailWarmupCursor
from app.thumbnail_provider import BrowserThumbnailProvider


class SilentWatcher(QObject):
    directory_changed=Signal(object)
    def __init__(self): super().__init__(); self.path=None; self.generation=0
    def watch(self,path,generation): self.path=str(path); self.generation=generation; return True
    def clear(self): self.path=None
    def close(self): self.clear()
    def notify(self): self.directory_changed.emit(BrowserDirectoryChange(self.path,self.generation))


def wait_until(qapp, predicate, timeout=5000):
    for _ in range(timeout//10):
        qapp.processEvents()
        if predicate(): return
        QTest.qWait(10)
    assert predicate()


def write_png(path,color='blue',width=32):
    with Image.new('RGB',(width,40),color) as image: image.save(path,format='PNG')


def blue(window,path):
    row=window.item_model.row_for_path(path)
    if row<0: return False
    image=window.item_model.data(window.item_model.index(row),window.item_model.ThumbnailImageRole)
    return isinstance(image,QImage) and not image.isNull() and image.pixelColor(0,0).blue()>200


def make_window(tmp_path,qapp,*,watcher=None,readable=None):
    folder=tmp_path/'downloads'; folder.mkdir()
    path=folder/'完成.png'; write_png(path)
    config=ConfigManager(tmp_path/'config.json'); config.load()
    config.set('last_browser_path',str(folder))
    calls=[]
    state=readable if readable is not None else [True]
    def decode(item,size):
        calls.append(item.path)
        if not state[0] or item.kind is not BrowserItemKind.IMAGE: return None
        return BrowserThumbnailProvider.load_thumbnail(item,size)
    provider=BrowserThumbnailProvider(loader=decode,disk_cache_enabled=False)
    window=BrowserWindow(config_manager=config,thumbnail_provider=provider,
                         directory_watcher=watcher if watcher is not None else SilentWatcher())
    window.resize(500,360)
    window._download_retry._budget=DownloadRetryBudget(delays=(.05,.10,.20),lifetime=2)
    window.show()
    return window,provider,path,calls,state


def close(window,provider,qapp):
    window.close(); provider.close(); qapp.processEvents()


@pytest.mark.parametrize('snapshot_pending',[False,True])
def test_manual_refresh_releases_unchanged_failure(tmp_path,qapp,snapshot_pending):
    window,provider,path,calls,state=make_window(tmp_path,qapp,readable=[False])
    window._download_retry.close()  # Isolate explicit Refresh from automatic retry.
    try:
        assert window.wait_for_scan()
        wait_until(qapp,lambda:provider.has_failed_requests and provider.pending_count==0)
        before=path.stat(); count=len(calls)
        state[0]=True
        window._snapshot_reconcile_pending=snapshot_pending
        assert window.refresh_current_folder()
        assert window._pending_scan.retry_failed_thumbnails
        assert window.wait_for_scan()
        wait_until(qapp,lambda:blue(window,path))
        after=path.stat()
        assert after.st_size==before.st_size and after.st_mtime_ns==before.st_mtime_ns
        assert len(calls)>count
    finally: close(window,provider,qapp)


def test_manual_refresh_retries_after_automatic_budget_exhaustion(tmp_path,qapp):
    window,provider,path,calls,state=make_window(tmp_path,qapp,readable=[False])
    window._download_retry._budget=DownloadRetryBudget(delays=(.05,),lifetime=2)
    try:
        assert window.wait_for_scan()
        wait_until(qapp,lambda:len(calls)>=2 and provider.has_failed_requests)
        QTest.qWait(100); qapp.processEvents()
        assert len(calls)==2
        assert not window._download_retry._timer.isActive()
        before=path.stat()
        state[0]=True
        assert window.refresh_current_folder()
        assert window.wait_for_scan()
        wait_until(qapp,lambda:blue(window,path))
        after=path.stat()
        assert after.st_size==before.st_size and after.st_mtime_ns==before.st_mtime_ns
        assert len(calls)>2
    finally: close(window,provider,qapp)


def test_retry_recovers_without_any_watch_notification_or_rescan(tmp_path,qapp):
    window,provider,path,calls,state=make_window(tmp_path,qapp,readable=[False])
    try:
        assert window.wait_for_scan()
        wait_until(qapp,lambda:provider.has_failed_requests)
        state[0]=True
        with patch.object(window.scanner,'start',wraps=window.scanner.start) as scan:
            wait_until(qapp,lambda:blue(window,path))
            assert scan.call_count==0
    finally: close(window,provider,qapp)


def test_retry_defers_while_hidden_then_resumes_on_show(tmp_path,qapp):
    window,provider,path,calls,state=make_window(tmp_path,qapp,readable=[False])
    window._download_retry._budget=DownloadRetryBudget(delays=(.2,),lifetime=2)
    try:
        assert window.wait_for_scan()
        wait_until(qapp,lambda:provider.has_failed_requests)
        window.hide()
        state[0]=True
        QTest.qWait(300); qapp.processEvents()
        assert not blue(window,path)
        assert len(calls)==1
        window.show()
        wait_until(qapp,lambda:blue(window,path))
        assert len(calls)>1
    finally: close(window,provider,qapp)


@pytest.mark.parametrize('deferred',['fast-scroll','provider-pause'])
def test_retry_waits_for_scroll_or_provider_resume(tmp_path,qapp,deferred):
    window,provider,path,calls,state=make_window(tmp_path,qapp,readable=[False])
    window._download_retry._budget=DownloadRetryBudget(delays=(.05,),lifetime=2)
    try:
        assert window.wait_for_scan()
        wait_until(qapp,lambda:provider.has_failed_requests)
        if deferred=='fast-scroll':
            window._fast_scrolling=True
        else:
            provider.set_paused(True)
        QTest.qWait(200); qapp.processEvents()
        assert len(calls)==1
        assert not window._download_retry._timer.isActive()
        state[0]=True
        if deferred=='fast-scroll':
            window._fast_scrolling=False
            window._request_visible_thumbnails()
        else:
            provider.set_paused(False)
        wait_until(qapp,lambda:blue(window,path))
        assert len(calls)>1
    finally: close(window,provider,qapp)


def test_permanent_failure_stops_automatic_retries(tmp_path,qapp):
    window,provider,path,calls,state=make_window(tmp_path,qapp,readable=[False])
    try:
        assert window.wait_for_scan()
        wait_until(qapp,lambda:len(calls)>=4)
        QTest.qWait(400); qapp.processEvents()
        assert len(calls)==4  # Initial attempt plus three configured opportunities.
        assert not window._download_retry._timer.isActive()
    finally: close(window,provider,qapp)


def test_event_stream_cannot_postpone_first_scan_forever(tmp_path,qapp):
    watcher=SilentWatcher()
    window,provider,path,calls,state=make_window(tmp_path,qapp,watcher=watcher)
    try:
        assert window.wait_for_scan(); wait_until(qapp,lambda:blue(window,path))
        with patch.object(window.scanner,'start',wraps=window.scanner.start) as scan:
            for _ in range(26):
                watcher.notify(); QTest.qWait(100); qapp.processEvents()
            assert scan.call_count>=1  # Notifications have not gone quiet yet.
            QTest.qWait(450)
            assert window.wait_for_scan()
            count=scan.call_count
            QTest.qWait(500); qapp.processEvents()
            assert scan.call_count==count  # No perpetual directory polling.
    finally: close(window,provider,qapp)


def test_retry_is_cancelled_on_navigation_and_shutdown(tmp_path,qapp):
    window,provider,path,calls,state=make_window(tmp_path,qapp,readable=[False])
    window._download_retry._budget=DownloadRetryBudget(delays=(.5,),lifetime=3)
    try:
        assert window.wait_for_scan(); wait_until(qapp,lambda:provider.has_failed_requests)
        other=tmp_path/'other'; other.mkdir(); write_png(other/'new.png')
        old_count=calls.count(path); state[0]=True
        assert window.navigate_to(other); assert window.wait_for_scan()
        QTest.qWait(700); qapp.processEvents()
        assert calls.count(path)==old_count
        window.prepare_shutdown()
        assert not window._download_retry._timer.isActive()
    finally: close(window,provider,qapp)


def test_targeted_failure_release_preserves_other_memos(tmp_path,qapp):
    window,provider,path,calls,state=make_window(tmp_path,qapp,readable=[False])
    window._download_retry.close()
    try:
        assert window.wait_for_scan(); wait_until(qapp,lambda:provider.has_failed_requests)
        item=window.item_model.item_at(window.item_model.row_for_path(path))
        unrelated=('/other',42,('unrelated',))
        with provider._failure_lock: provider._failed.add(unrelated)
        assert not provider.clear_failed_thumbnail(item,window.thumbnail_render_spec,
                                                  generation=window._generation-1)
        assert provider.clear_failed_thumbnail(item,window.thumbnail_render_spec,
                                               generation=window._generation)
        assert unrelated in provider._failed
        row=window.item_model.row_for_path(path)
        workflow=window._browser_workflow
        workflow._cursor=ThumbnailWarmupCursor(window.item_model.rowCount()+1)
        workflow._cursor.recenter(1,1,1,1)
        workflow._cursor.complete(row)
        identity=workflow._memory_identity(
            item,window.thumbnail_render_spec.cache_token
        )
        workflow._memory_attempted.add(identity)
        assert workflow.retry_failed_thumbnail(
            str(path),window.thumbnail_render_spec.cache_token,item.thumbnail_revision
        )
        assert workflow._cursor.take()==row
        assert identity not in workflow._memory_attempted
        workflow._timer.stop()
    finally: close(window,provider,qapp)


@pytest.mark.skipif(os.name!='nt',reason='Real Windows content-change HANDLE required')
def test_windows_same_name_write_works_without_qt_name_signal(tmp_path,qapp):
    watcher=BrowserDirectoryWatcher()
    window,provider,path,calls,state=make_window(tmp_path,qapp,watcher=watcher)
    try:
        assert window.wait_for_scan(); wait_until(qapp,lambda:blue(window,path))
        assert watcher.content_notifications_active, watcher.last_content_error
        watcher._watcher.directoryChanged.disconnect(
            watcher._watcher_connection
        )  # Isolate new content events.
        watcher._watcher_connection = None
        write_png(path,'red',80)
        def red():
            row=window.item_model.row_for_path(path)
            image=window.item_model.data(window.item_model.index(row),window.item_model.ThumbnailImageRole)
            return isinstance(image,QImage) and not image.isNull() and image.pixelColor(0,0).red()>200
        wait_until(qapp,red,timeout=8000)
        assert window.item_model.item_at(window.item_model.row_for_path(path)).file_size==path.stat().st_size
    finally: close(window,provider,qapp)
