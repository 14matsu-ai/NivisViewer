import ctypes
import os
import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from app.windows_directory_notifications import (
    WindowsContentNotification, extended_windows_path, CONTENT_CHANGE_FILTER,
    WAIT_TIMEOUT, WAIT_OBJECT_0, WAIT_FAILED,
)


class Function:
    def __init__(self,result): self.result=result; self.calls=[]
    def __call__(self,*args): self.calls.append(args); return self.result
class Api:
    def __init__(self,handle=0x123456789ABC):
        self.FindFirstChangeNotificationW=Function(handle)
        self.FindNextChangeNotification=Function(True)
        self.WaitForSingleObject=Function(WAIT_TIMEOUT)
        self.FindCloseChangeNotification=Function(True)


@pytest.mark.parametrize('path,expected',[
    (r'C:\download',r'\\?\C:\download'),
    (r'\\server\share\download',r'\\?\UNC\server\share\download'),
    (r'\\?\C:\long\download',r'\\?\C:\long\download'),
])
def test_extended_paths(path,expected):
    assert extended_windows_path(path)==expected


@pytest.mark.parametrize('path',['relative',r'C:relative',r'\relative'])
def test_relative_paths_rejected(path):
    with pytest.raises(ValueError): extended_windows_path(path)


def test_filters_and_nonblocking_wait_and_64bit_handle():
    api=Api(); h=WindowsContentNotification(r'C:\download',api=api)
    assert api.FindFirstChangeNotificationW.calls==[(r'\\?\C:\download',False,CONTENT_CHANGE_FILTER)]
    assert CONTENT_CHANGE_FILTER==0x18
    assert api.FindFirstChangeNotificationW.restype==ctypes.c_void_p
    assert not h.poll()
    assert api.WaitForSingleObject.calls==[(0x123456789ABC,0)]
    h.close(); h.close()
    assert len(api.FindCloseChangeNotification.calls)==1


def test_rearms_each_signal():
    api=Api(); h=WindowsContentNotification(r'C:\download',api=api)
    api.WaitForSingleObject.result=WAIT_OBJECT_0
    assert h.poll() and h.poll()
    assert len(api.FindNextChangeNotification.calls)==2
    h.close()


@pytest.mark.parametrize('handle',[None,0,-1,ctypes.c_void_p(-1).value])
def test_invalid_handles_not_closed(handle):
    api=Api(handle)
    with pytest.raises(OSError): WindowsContentNotification(r'C:\download',api=api)
    assert not api.FindCloseChangeNotification.calls


@pytest.mark.parametrize('wait,next_ok',[(WAIT_FAILED,True),(WAIT_OBJECT_0,False)])
def test_wait_and_rearm_failures_surface(wait,next_ok):
    api=Api(); h=WindowsContentNotification(r'C:\download',api=api)
    api.WaitForSingleObject.result=wait; api.FindNextChangeNotification.result=next_ok
    with pytest.raises(OSError): h.poll()
    h.close(); assert len(api.FindCloseChangeNotification.calls)==1


def test_closed_handle_not_polled():
    api=Api(); h=WindowsContentNotification(r'C:\download',api=api); h.close()
    assert not h.poll() and not api.WaitForSingleObject.calls


@pytest.mark.skipif(os.name!='nt',reason='Real Windows content-change HANDLE required')
def test_browser_watcher_releases_handles_on_rewatch_clear_and_destroy(tmp_path,qapp):
    from app.browser_directory_watcher import BrowserDirectoryWatcher

    watcher=BrowserDirectoryWatcher()
    try:
        assert watcher.watch(tmp_path,1)
        first=watcher._content_holder[0]
        assert first is not None and first._handle is not None
        assert watcher.watch(tmp_path,2)
        assert first._handle is None
        second=watcher._content_holder[0]
        assert second is not None and second._handle is not None
        watcher.clear()
        assert second._handle is None
        assert watcher.watch(tmp_path,3)
        third=watcher._content_holder[0]
        assert third is not None and third._handle is not None
        watcher.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        QTest.qWait(0)
        assert third._handle is None
    finally:
        if watcher._content_holder[0] is not None:
            watcher.clear()
