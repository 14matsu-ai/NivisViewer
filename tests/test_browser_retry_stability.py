"""Synthetic Browser regressions; all windows use Qt offscreen."""
import io
from pathlib import Path
import zipfile

from PIL import Image
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtTest import QTest
from app.browser_download_policy import DownloadRetryBudget
from app.browser_model import BrowserItemKind
from app.browser_window import BrowserWindow
from app.config_manager import ConfigManager
from app.thumbnail_provider import BrowserThumbnailProvider
from tests.test_browser_download_recovery_qt import SilentWatcher, wait_until, close, blue


def write_zip(path, encrypted=False):
    image_bytes=io.BytesIO()
    with Image.new('RGB',(32,40),'blue') as image:image.save(image_bytes,format='PNG')
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:z.writestr('page.png',image_bytes.getvalue())
    data=bytearray(archive.getvalue())
    if encrypted:
        # Authoritative ZIP encryption bits, without needing an encryption dependency.
        for signature,offset in ((b'PK\x03\x04',6),(b'PK\x01\x02',8)):
            position=data.index(signature)+offset
            flags=int.from_bytes(data[position:position+2],'little')|1
            data[position:position+2]=flags.to_bytes(2,'little')
    path.write_bytes(data)


def setup(tmp_path,qapp,loader):
    config=ConfigManager(tmp_path/'config.json');config.load()
    folder=tmp_path/'downloads';folder.mkdir()
    config.set('last_browser_path',str(folder))
    provider=BrowserThumbnailProvider(loader=loader,disk_cache_enabled=False)
    watcher=SilentWatcher()
    window=BrowserWindow(config_manager=config,thumbnail_provider=provider,directory_watcher=watcher)
    window.resize(500,360)
    window._download_retry._budget=DownloadRetryBudget(delays=(.05,.10),lifetime=2)
    return folder,window,provider


def status(window,path):
    model=window.item_model;index=model.index(model.row_for_path(path))
    return model.data(index,model.PreviewStatusRole),model.data(index,model.ThumbnailErrorRole)


def refresh(window,qapp):
    assert window._refresh_current_folder(navigation_source='filesystem_watch')
    assert window.wait_for_scan()
    qapp.processEvents()


def test_unchanged_encrypted_zip_keeps_badge_and_is_not_reopened(tmp_path,qapp):
    calls=[]
    def decode(item,size):
        if item.kind is BrowserItemKind.ARCHIVE:
            calls.append(item.thumbnail_revision)
            return BrowserThumbnailProvider.load_thumbnail_result(item,size)
        return None
    folder,window,provider=setup(tmp_path,qapp,decode)
    target=folder/'protected.zip';write_zip(target,encrypted=True)
    other=folder/'large.download';other.write_bytes(b'0')
    window.show()
    try:
        assert window.wait_for_scan()
        refresh(window,qapp)
        wait_until(qapp,lambda:bool(calls) and provider.pending_count==0)
        assert status(window,target)[0]=='failed'
        before=status(window,target);attempts=len(calls)
        row=window.item_model.row_for_path(target)
        window.list_view.selectionModel().setCurrentIndex(window.item_model.index(row),QItemSelectionModel.SelectionFlag.ClearAndSelect)
        resets=[];window.item_model.modelReset.connect(lambda:resets.append(True))
        for i in range(4):
            with other.open('ab') as stream:stream.write(b'chunk')
            refresh(window,qapp)
            QTest.qWait(80)
            assert status(window,target)==before
            assert len(calls)==attempts
            assert window.list_view.currentIndex().row()==row
        assert not resets
        # Manual refresh deliberately retries a permanent failure once.
        assert window.refresh_current_folder();assert window.wait_for_scan()
        wait_until(qapp,lambda:len(calls)>attempts and provider.pending_count==0)
        assert len(calls)==attempts+1
        assert status(window,target)==before
        # Actual content replacement reopens eligibility and converges to pixels.
        write_zip(target,encrypted=False)
        refresh(window,qapp)
        wait_until(qapp,lambda:blue(window,target))
        assert status(window,target)[0]=='ready'
    finally:close(window,provider,qapp)


def test_unrelated_changes_do_not_refill_transient_retry_budget(tmp_path,qapp):
    calls=[]
    def decode(item,size):
        if item.kind is BrowserItemKind.IMAGE:calls.append(item.thumbnail_revision)
        return None
    folder,window,provider=setup(tmp_path,qapp,decode)
    target=folder/'incomplete.png';target.write_bytes(b'incomplete')
    other=folder/'large.download';other.write_bytes(b'0')
    window.show()
    try:
        assert window.wait_for_scan();refresh(window,qapp)
        wait_until(qapp,lambda:len(calls)==3 and provider.pending_count==0)
        before=status(window,target)
        for i in range(5):
            with other.open('ab') as stream:stream.write(b'chunk')
            refresh(window,qapp);QTest.qWait(70)
            assert status(window,target)==before
        assert len(calls)==3
        # The shared timer may serve another changed item; inspect this identity.
        entries=[entry for key,entry in window._download_retry._budget._entries.items() if key[0]==str(target)]
        assert len(entries)==1
        assert entries[0].attempts==2 and entries[0].due_at is None
    finally:close(window,provider,qapp)


def test_interrupted_retry_restores_memo_without_new_allowance(tmp_path,qapp):
    from tests.test_browser_download_refresh import item_for,write_png
    path=tmp_path/'image.png';write_png(path,'blue')
    provider=BrowserThumbnailProvider(loader=lambda *args:None,disk_cache_enabled=False)
    item=item_for(path);budget=DownloadRetryBudget(delays=(.1,),lifetime=1)
    try:
        generation=provider.begin_generation()
        assert provider.request(item,80,generation=generation)
        assert provider.wait_for_done(2000);qapp.processEvents()
        key=(str(path),80,item.thumbnail_revision)
        assert budget.failed(key,generation,0)
        assert budget.take_due(.1)
        assert provider.clear_failed_thumbnail(item,80,generation=generation,automatic=True)
        provider.begin_generation()
        assert provider.can_retry_failed_thumbnail(item,80)
        budget.resume_interrupted(provider.generation,.2,lambda _:True)
        assert budget.next_delay_ms(.2) is None
    finally:provider.close()


def test_changing_incomplete_file_waits_for_quiet_then_recovers(tmp_path,qapp):
    from tests.test_browser_download_refresh import write_png
    calls=[]
    def decode(item,size):
        if item.kind is BrowserItemKind.IMAGE:
            calls.append(item.thumbnail_revision)
            return BrowserThumbnailProvider.load_thumbnail(item,size)
        return None
    folder,window,provider=setup(tmp_path,qapp,decode)
    window._download_retry._budget=DownloadRetryBudget(delays=(.3,.4),lifetime=3)
    target=folder/'writing.png';target.write_bytes(b'incomplete')
    window.show()
    try:
        assert window.wait_for_scan();refresh(window,qapp)
        wait_until(qapp,lambda:bool(calls) and provider.pending_count==0)
        before=status(window,target);count=len(calls)
        for i in range(3):
            with target.open('ab') as stream:stream.write(b'chunk')
            refresh(window,qapp);QTest.qWait(30)
            assert status(window,target)==before
            assert len(calls)==count
        write_png(target,'blue')
        refresh(window,qapp)
        assert status(window,target)==before
        wait_until(qapp,lambda:blue(window,target))
        assert len(calls)==count+1
    finally:close(window,provider,qapp)


def test_password_codes_survive_pdf_and_external_archive_adapters(tmp_path):
    from types import SimpleNamespace
    from app.archive_backend import ArchiveBackendError,ArchiveErrorCode
    from app.pdf_backend import PdfBackendError,PdfErrorCode
    from app.thumbnail_provider import ThumbnailFailureKind
    from tests.test_browser_download_refresh import item_for
    def pdf_locked(*args,**kwargs):raise PdfBackendError(PdfErrorCode.PASSWORD_REQUIRED)
    def archive_locked(*args,**kwargs):raise ArchiveBackendError(ArchiveErrorCode.PASSWORD_REQUIRED)
    service=SimpleNamespace(open_document=pdf_locked)
    registry=SimpleNamespace(backend_for_path=lambda _:SimpleNamespace(list_entries=archive_locked))
    for suffix in ('.pdf','.7z'):
        path=tmp_path/('locked'+suffix);path.write_bytes(b'synthetic')
        result=BrowserThumbnailProvider.load_thumbnail_result(item_for(path),80,pdfium_service=service,archive_backend_registry=registry)
        assert result.resolved_kind.value=='failed'
        assert result.failure_kind is ThumbnailFailureKind.PERMANENT, suffix
