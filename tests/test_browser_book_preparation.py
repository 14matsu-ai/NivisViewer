from collections import Counter
from io import BytesIO
from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest
from PySide6.QtTest import QTest

from app.browser_book_preparation import BrowserBookPreparation
from app.browser_model import BrowserItem, BrowserItemKind
from app.config_manager import ConfigManager
from app.image_source import ZipImageSource
from app.viewer_navigation_policy import NavigationInputKind
from app.viewer_window import ViewerWindow
from PIL import Image
from tests.test_application_controller import wait_until


def make_book(tmp_path, name='選んだ書庫.cbz', *, sizes=None):
    path = tmp_path / name
    with ZipFile(path, 'w') as archive:
        for i, size in enumerate(sizes or [(360, 540)] * 8):
            with Image.new('RGB', size, (i*23, 40, 60)) as image:
                data = BytesIO()
                image.save(data, 'PNG')
            archive.writestr(f'{i:02}.png', data.getvalue())
    stat = path.stat()
    return BrowserItem(path.name, path, BrowserItemKind.ARCHIVE, stat.st_mtime,
        stat.st_size, stat.st_mtime_ns)


@pytest.fixture
def target(tmp_path, qapp):
    config = ConfigManager(tmp_path/'settings.json')
    config.load()
    config.apply({'show_page_list':False, 'viewer_memory_mode':'256',
                  'view_mode':'spread', 'single_first_page':True,
                  'book_open_position':'first_page'})
    viewer = ViewerWindow(config_manager=config)
    viewer.resize(900,650); viewer.show(); qapp.processEvents()
    yield viewer
    viewer.close()
    viewer.book_session.shutdown(wait_msecs=3000)
    qapp.processEvents()


def start(preparation, item):
    preparation.select(item)
    preparation._timer.stop()
    preparation._start()


@pytest.mark.parametrize('single_first', [True, False])
@pytest.mark.parametrize('direction', ['ltr', 'rtl'])
def test_prepared_frames_and_source_move_without_decode_again(tmp_path, qapp, target, monkeypatch, single_first, direction):
    target.apply_settings({'single_first_page':single_first})
    target.set_reading_direction(direction)
    calls = Counter()
    original = ZipImageSource.open_image
    def counted(source, name):
        calls[name] += 1
        return original(source, name)
    monkeypatch.setattr(ZipImageSource, 'open_image', counted)
    item = make_book(tmp_path)
    preparation = BrowserBookPreparation(lambda:target)
    try:
        start(preparation, item)
        assert wait_until(qapp, lambda:preparation.ready, timeout=5)
        runtime = preparation.session.viewer_runtime
        source = preparation.session.source
        before = dict(calls)
        assert len(before) == (3 if single_first else 4)
        # Merely preparing a book changes neither history nor visible source.
        assert target.book_session.source is None
        assert target.settings.get('last_open_path', '') == ''
        prepared = preparation.take(item.path, target)
        assert prepared is not None
        painted = []
        target.first_frame_ready.connect(lambda *_:painted.append(True))
        assert target.open_path(item.path, prepared_book=prepared)
        assert wait_until(qapp, lambda:bool(painted), timeout=5)
        assert target.book_session.viewer_runtime is runtime
        assert target.book_session.source is source
        assert runtime.speculative is False
        target.next_page(input_kind=NavigationInputKind.WHEEL)
        assert tuple(target.viewer.displayed_page_indexes) == tuple(s.page_index for s in target.model.spread_at().slots)
        assert all(calls[name] == count for name, count in before.items())
    finally:
        preparation.shutdown()


def test_open_while_decoding_adopts_same_job(tmp_path, qapp, target, monkeypatch):
    entered, release = Event(), Event()
    calls = Counter()
    original = ZipImageSource.open_image
    def blocked(source, name):
        calls[name] += 1
        if name == '00.png':
            entered.set()
            assert release.wait(5)
        return original(source, name)
    monkeypatch.setattr(ZipImageSource, 'open_image', blocked)
    preparation = BrowserBookPreparation(lambda:target)
    item = make_book(tmp_path)
    try:
        start(preparation,item)
        assert wait_until(qapp, entered.is_set)
        runtime = preparation.session.viewer_runtime
        prepared = preparation.take(item.path,target)
        target.open_path(item.path,prepared_book=prepared)
        qapp.processEvents()
        release.set()
        assert wait_until(qapp, lambda:bool(target.viewer.displayed_page_indexes), timeout=5)
        assert target.book_session.viewer_runtime is runtime
        assert calls['00.png'] == 1
    finally:
        release.set(); preparation.shutdown()


@pytest.mark.parametrize('change', ['geometry','file','epoch','settings'])
def test_incompatible_preparation_is_not_installed(tmp_path,qapp,target,change):
    item=make_book(tmp_path)
    preparation=BrowserBookPreparation(lambda:target)
    try:
        start(preparation,item)
        assert wait_until(qapp,lambda:preparation.ready,timeout=5)
        if change=='geometry':
            target.resize(1100,750);qapp.processEvents()
        elif change=='file':
            with ZipFile(item.path,'a') as z:z.writestr('09.png',b'changed')
        elif change=='epoch':
            target.book_session.generation += 1
        else:
            target.set_reading_direction('rtl' if target.reading_direction=='ltr' else 'ltr')
        assert preparation.take(item.path,target) is None
        assert target.book_session.source is None
    finally:
        preparation.shutdown()


def test_rapid_selection_drains_old_owner_before_starting_latest(tmp_path,qapp,target,monkeypatch):
    entered,release=Event(),Event()
    original=ZipImageSource.open_image
    def blocked(source,name):
        if source.source_path.name=='A.cbz':
            entered.set();assert release.wait(5)
        return original(source,name)
    monkeypatch.setattr(ZipImageSource,'open_image',blocked)
    a,b,c=(make_book(tmp_path,name+'.cbz') for name in ('A','B','C'))
    preparation=BrowserBookPreparation(lambda:target)
    try:
        start(preparation,a)
        assert wait_until(qapp,entered.is_set)
        start(preparation,b);start(preparation,c)
        assert preparation.session is None
        assert preparation._retiring is not None
        release.set()
        assert wait_until(qapp,lambda:preparation.ready,timeout=5)
        assert preparation.session.source.source_path==c.path
    finally:
        release.set();preparation.shutdown()


def test_low_available_memory_skips_optional_preparation(tmp_path,qapp,target,monkeypatch):
    from app import browser_book_preparation as module
    from app.viewer_memory_policy import PhysicalMemorySnapshot,MIB
    monkeypatch.setattr(module,'read_physical_memory_snapshot',lambda:PhysicalMemorySnapshot(8192*MIB,64*MIB,32*MIB))
    preparation=BrowserBookPreparation(lambda:target)
    start(preparation,make_book(tmp_path))
    assert preparation.session is None
    assert preparation.shutdown()


def test_unrelated_shared_settings_keep_prepared_book(tmp_path,qapp):
    from tests.test_application_controller import make_controller,close_controller
    controller=make_controller(tmp_path,qapp)
    controller.config.apply({'fullscreen':True,'show_page_list':False})
    item=make_book(tmp_path)
    preparation=controller.book_preparation
    try:
        start(preparation,item)
        assert wait_until(qapp,lambda:preparation.ready,timeout=5)
        session=preparation.session
        controller.config.set('browser_sidebar_width',301)
        qapp.processEvents()
        assert preparation.session is session and preparation.ready
        controller.config.set('viewer_downscale_algorithm','fast')
        assert wait_until(qapp,lambda:preparation.session is not session and preparation.ready,timeout=5)
        viewer=controller.open_path(item.path)
        assert wait_until(qapp,lambda:bool(viewer.viewer.displayed_page_indexes),timeout=5)
        assert viewer.viewer_downscale_algorithm=='fast'
    finally:
        close_controller(controller,qapp)


def test_hidden_fullscreen_layout_preserves_normal_window_restore(tmp_path,qapp):
    from PySide6.QtCore import QByteArray
    config=ConfigManager(tmp_path/'fullscreen.json');config.load()
    config.apply({'fullscreen':True,'show_page_list':True})
    viewer=ViewerWindow(config_manager=config)
    try:
        viewer.resize(600,500)
        geometry=QByteArray(viewer.saveGeometry())
        viewer.prepare_initial_render_layout()
        prepared_spec=viewer._raster_render_spec()
        assert not viewer.isVisible()
        assert not viewer.page_list_dock.isVisible()
        assert viewer.fullscreen_chrome.standard_window_geometry()==geometry
        viewer.show_initial();qapp.processEvents()
        assert viewer._raster_render_spec()==prepared_spec
        viewer.exit_fullscreen();qapp.processEvents()
        assert viewer.size().width()==600 and viewer.size().height()==500
        assert not viewer._is_fullscreen_mode()
        assert viewer.page_list_dock.isVisible()
    finally:
        viewer.close();viewer.book_session.shutdown(wait_msecs=3000)


def test_viewer_selection_sync_does_not_decode_open_book_again(tmp_path,qapp):
    from tests.test_application_controller import make_controller,close_controller
    controller=make_controller(tmp_path,qapp)
    item=make_book(tmp_path)
    try:
        browser=controller.start(restore=False)
        browser.navigate_to(tmp_path)
        assert wait_until(qapp,lambda:browser.item_model.row_for_path(item.path)>=0,timeout=5)
        viewer=controller.open_path(item.path)
        assert wait_until(qapp,lambda:bool(viewer.viewer.displayed_page_indexes),timeout=5)
        assert wait_until(qapp,lambda:browser.list_view.currentIndex().isValid(),timeout=5)
        qapp.processEvents()
        assert controller.book_preparation.session is None
        assert controller.book_preparation._retiring is None
    finally:
        close_controller(controller,qapp)


@pytest.mark.parametrize('fullscreen',[False,True])
def test_mouse_selection_and_double_click_adopt_prepared_frames(tmp_path,qapp,fullscreen):
    from PySide6.QtCore import Qt
    from tests.test_application_controller import make_controller,close_controller
    controller=make_controller(tmp_path,qapp)
    controller.config.apply({'fullscreen':fullscreen,'show_page_list':False,
        'single_first_page':True,'book_open_position':'first_page'})
    item=make_book(tmp_path)
    try:
        browser=controller.start(restore=False)
        browser.navigate_to(tmp_path)
        assert wait_until(qapp,lambda:browser.item_model.row_for_path(item.path)>=0,timeout=5)
        row=browser.item_model.row_for_path(item.path)
        index=browser.item_model.index(row,0)
        browser.list_view.scrollTo(index);qapp.processEvents()
        point=browser.list_view.visualRect(index).center()
        QTest.mouseClick(browser.list_view.viewport(),Qt.MouseButton.LeftButton,pos=point)
        preparation=controller.book_preparation
        assert wait_until(qapp,lambda:preparation.ready,timeout=5)
        runtime=preparation.session.viewer_runtime
        source=preparation.session.source
        QTest.mouseDClick(browser.list_view.viewport(),Qt.MouseButton.LeftButton,pos=point)
        QTest.mouseRelease(browser.list_view.viewport(),Qt.MouseButton.LeftButton,pos=point)
        assert wait_until(qapp,lambda:controller.get_active_viewer() is not None,timeout=5)
        viewer=controller.get_active_viewer()
        assert wait_until(qapp,lambda:viewer.viewer.displayed_page_indexes==(0,),timeout=5)
        assert viewer.book_session.viewer_runtime is runtime
        assert viewer.book_session.source is source
        viewer.next_page(input_kind=NavigationInputKind.WHEEL)
        assert set(viewer.viewer.displayed_page_indexes)=={1,2}
    finally:
        close_controller(controller,qapp)


@pytest.mark.parametrize('prepared_first',[False,True])
@pytest.mark.parametrize('fullscreen',[False,True])
def test_closing_viewer_prepares_still_selected_book_for_reopen(tmp_path,qapp,fullscreen,prepared_first):
    from tests.test_application_controller import make_controller,close_controller
    controller=make_controller(tmp_path,qapp)
    controller.config.apply({'fullscreen':fullscreen,'show_page_list':False,
        'single_first_page':True,'book_open_position':'first_page'})
    book_dir=tmp_path/'books';book_dir.mkdir()
    item=make_book(book_dir)
    try:
        browser=controller.start(restore=False)
        browser.navigate_to(book_dir)
        assert wait_until(qapp,lambda:browser.item_model.row_for_path(item.path)>=0,timeout=5)
        preparation=controller.book_preparation
        if prepared_first:
            browser.select_path(item.path)
            assert wait_until(qapp,lambda:preparation.ready,timeout=5)
            browser.open_item(browser.list_view.currentIndex())
        else:
            controller.open_path(item.path)
        viewer=controller.get_active_viewer()
        assert wait_until(qapp,lambda:viewer.viewer.displayed_page_indexes==(0,),timeout=5)
        viewer.next_page(input_kind=NavigationInputKind.WHEEL)
        assert wait_until(qapp,lambda:set(viewer.viewer.displayed_page_indexes)=={1,2},timeout=5)
        viewer.close()
        assert wait_until(qapp,lambda:not controller.viewer_windows,timeout=5)
        assert wait_until(qapp,lambda:preparation.ready,timeout=5)
        source=preparation.session.source
        runtime=preparation.session.viewer_runtime
        assert browser.item_model.item_at(browser.list_view.currentIndex()).path==item.path
        browser.open_item(browser.list_view.currentIndex())
        reopened=controller.get_active_viewer()
        assert wait_until(qapp,lambda:reopened.viewer.displayed_page_indexes==(0,),timeout=5)
        assert reopened.book_session.viewer_runtime is runtime
        assert reopened.book_session.source is source
        reopened.next_page(input_kind=NavigationInputKind.WHEEL)
        assert set(reopened.viewer.displayed_page_indexes)=={1,2}
    finally:
        close_controller(controller,qapp)


@pytest.mark.parametrize('action',['different','clear','browser_close','shutdown'])
def test_close_repreparation_uses_latest_selection_and_respects_shutdown(tmp_path,qapp,action):
    from PySide6.QtCore import QItemSelectionModel, QModelIndex
    from tests.test_application_controller import make_controller,close_controller
    controller=make_controller(tmp_path,qapp)
    controller.config.apply({'show_page_list':False,'single_first_page':True})
    book_dir=tmp_path/'books';book_dir.mkdir()
    first=make_book(book_dir,'first.cbz')
    other=make_book(book_dir,'other.cbz')
    try:
        browser=controller.start(restore=False)
        browser.navigate_to(book_dir)
        assert wait_until(qapp,lambda:browser.item_model.row_for_path(other.path)>=0,timeout=5)
        viewer=controller.open_path(first.path)
        assert wait_until(qapp,lambda:bool(viewer.viewer.displayed_page_indexes),timeout=5)
        viewer.close()
        selection=browser.list_view.selectionModel()
        if action=='different':
            index=browser.item_model.index(browser.item_model.row_for_path(other.path),0)
            selection.setCurrentIndex(index,QItemSelectionModel.SelectionFlag.ClearAndSelect)
            assert wait_until(qapp,lambda:controller.book_preparation.ready,timeout=5)
            assert controller.book_preparation.session.current_path==other.path
        else:
            if action=='clear':
                selection.clearSelection();browser.list_view.setCurrentIndex(QModelIndex())
            elif action=='browser_close':
                browser.close()
            else:
                assert wait_until(qapp,controller.shutdown,timeout=5)
            qapp.processEvents()
            assert controller.book_preparation.session is None
            assert controller._standby_viewer is None
    finally:
        close_controller(controller,qapp)


@pytest.mark.parametrize('fullscreen', [False, True])
@pytest.mark.parametrize('existing_viewer', [False, True])
def test_browser_selection_and_open_use_prepared_owner(tmp_path,qapp,monkeypatch,existing_viewer,fullscreen):
    from tests.test_application_controller import make_controller,close_controller
    calls=Counter()
    original=ZipImageSource.open_image
    def counted(source,name):
        calls[(id(source),name)] += 1
        return original(source,name)
    monkeypatch.setattr(ZipImageSource,'open_image',counted)
    item=make_book(tmp_path)
    controller=make_controller(tmp_path,qapp)
    controller.config.apply({'single_first_page':True,'show_page_list':False,
        'viewer_memory_mode':'256','book_open_position':'first_page', 'fullscreen':fullscreen})
    try:
        if existing_viewer:
            viewer=controller.create_viewer_window()
            old=make_book(tmp_path,'前の書庫.cbz')
            viewer.show_initial();viewer.open_path(old.path)
            assert wait_until(qapp,lambda:bool(viewer.viewer.displayed_page_indexes),timeout=5)
        browser=controller.start(restore=False)
        browser.navigate_to(tmp_path)
        assert wait_until(qapp,lambda:any(i.path==item.path for i in browser.item_model.items),timeout=5)
        browser.select_path(item.path)
        assert wait_until(qapp,lambda:controller.book_preparation.ready,timeout=5)
        preparation=controller.book_preparation
        runtime=preparation.session.viewer_runtime
        source=preparation.session.source
        if not existing_viewer:
            assert controller.viewer_windows==()
            assert controller._standby_viewer is not None
            assert not controller._standby_viewer.isVisible()
        before={name:count for (source_id,name),count in calls.items() if source_id==id(source)}
        row=next(i for i,item_in_model in enumerate(browser.item_model.items) if item_in_model.path==item.path)
        browser.open_item(browser.item_model.index(row,0))
        viewer=controller.get_active_viewer()
        assert wait_until(qapp,lambda:viewer.viewer.displayed_page_indexes==(0,),timeout=5)
        assert viewer.book_session.viewer_runtime is runtime
        viewer.next_page(input_kind=NavigationInputKind.WHEEL)
        assert set(viewer.viewer.displayed_page_indexes)=={1,2}
        assert all(calls[(id(source),name)]==count for name,count in before.items())
    finally:
        close_controller(controller,qapp)


@pytest.mark.parametrize('options', [
    {'view_mode':'single'}, {'rotation':90}, {'split_wide_image':True},
    {'treat_square_image_as_single':True}, {'show_page_list':True},
])
def test_existing_layout_contracts_survive_preparation(tmp_path,qapp,target,options):
    if 'view_mode' in options:target.set_view_mode(options['view_mode'])
    elif 'rotation' in options:target.rotate_right()
    else:target.apply_settings(options)
    item=make_book(tmp_path,sizes=[(800,500),(510,500),(360,540),(360,540),(360,540)])
    preparation=BrowserBookPreparation(lambda:target)
    try:
        start(preparation,item)
        assert wait_until(qapp,lambda:preparation.ready,timeout=5)
        runtime=preparation.session.viewer_runtime
        prepared=preparation.take(item.path,target)
        assert prepared is not None
        target.open_path(item.path,prepared_book=prepared)
        assert wait_until(qapp,lambda:bool(target.viewer.displayed_page_indexes),timeout=5)
        assert target.book_session.viewer_runtime is runtime
        for command in (target.next_page,target.previous_page,target.next_page):
            command()
            assert wait_until(qapp,lambda:tuple(target.viewer.displayed_page_indexes)==tuple(s.page_index for s in target.model.spread_at().slots),timeout=5)
    finally:
        preparation.shutdown()


def test_shutdown_during_queued_handoff_keeps_source_until_worker_drains(tmp_path,qapp,target,monkeypatch):
    from shiboken6 import isValid
    entered,release,closed=Event(),Event(),Event()
    original,close=ZipImageSource.open_image,ZipImageSource.close
    def blocked(source,name):
        entered.set();assert release.wait(5)
        assert not closed.is_set()
        return original(source,name)
    def observed_close(source):
        closed.set();close(source)
    monkeypatch.setattr(ZipImageSource,'open_image',blocked)
    monkeypatch.setattr(ZipImageSource,'close',observed_close)
    preparation=BrowserBookPreparation(lambda:target)
    item=make_book(tmp_path)
    try:
        start(preparation,item)
        assert wait_until(qapp,entered.is_set)
        prepared=preparation.take(item.path,target)
        runtime=prepared.viewer_runtime
        target.open_path(item.path,prepared_book=prepared)
        target.book_session.shutdown(wait_msecs=0)
        assert not closed.is_set()
        release.set()
        assert wait_until(qapp,closed.is_set,timeout=5)
        assert not isValid(runtime) or not runtime.has_unfinished_tasks()
        assert target.book_session.source is None
    finally:
        release.set();preparation.shutdown()


def test_optional_full_raster_over_budget_stops_without_decoding(tmp_path,qapp,target,monkeypatch):
    from app.viewer_memory_policy import MIB
    target.viewer_cache_budget_bytes=32*MIB
    target.fit_mode='actual_size'
    item=make_book(tmp_path,sizes=[(4000,3000)])
    monkeypatch.setattr(ZipImageSource,'open_image',lambda *_:pytest.fail('over-budget speculative pixel decode'))
    preparation=BrowserBookPreparation(lambda:target)
    try:
        start(preparation,item)
        assert wait_until(qapp,lambda:preparation.session is not None and preparation.session.viewer_runtime is not None
            and preparation.session.viewer_runtime._dispatch_suspended,timeout=5)
        runtime=preparation.session.viewer_runtime
        count=runtime.metrics.jobs_submitted
        QTest.qWait(100);qapp.processEvents()
        assert runtime.metrics.jobs_submitted==count
        assert not runtime.has_unfinished_tasks()
    finally:
        preparation.shutdown()


@pytest.mark.parametrize('cancel_operation',[False,True])
def test_file_mutation_waits_for_preparation_handle_without_blocking_gui(tmp_path,qapp,monkeypatch,cancel_operation):
    from tests.test_application_controller import make_controller,close_controller
    from app.file_operation_service import FileOperationRequest,FileOperationKind
    entered,release=Event(),Event()
    original=ZipImageSource.open_image
    def blocked(source,name):
        entered.set();assert release.wait(5)
        return original(source,name)
    monkeypatch.setattr(ZipImageSource,'open_image',blocked)
    controller=make_controller(tmp_path,qapp)
    item=make_book(tmp_path)
    try:
        start(controller.book_preparation,item)
        assert wait_until(qapp,entered.is_set)
        queue=controller.file_operation_queue
        assert queue.execute(FileOperationRequest(1,FileOperationKind.RENAME,(str(item.path),),new_name='変更後.cbz'))
        assert wait_until(qapp,lambda:queue._gated_entry is not None,timeout=3)
        assert item.path.exists()
        if cancel_operation:queue.cancel()
        release.set()
        assert wait_until(qapp,lambda:not queue.busy,timeout=5)
        assert item.path.exists() is cancel_operation
        assert (item.path.parent/'変更後.cbz').exists() is not cancel_operation
    finally:
        release.set();close_controller(controller,qapp)


def test_header_completion_between_handoff_and_adoption_preserves_wide_layout(tmp_path,qapp,target,monkeypatch):
    entered,release=Event(),Event()
    probe=ZipImageSource.probe_image_size
    decode=ZipImageSource.open_image
    calls=Counter()
    def blocked(source,name):
        entered.set();assert release.wait(5)
        return probe(source,name)
    def counted(source,name):
        calls[name]+=1
        return decode(source,name)
    monkeypatch.setattr(ZipImageSource,'probe_image_size',blocked)
    monkeypatch.setattr(ZipImageSource,'open_image',counted)
    target.apply_settings({'single_first_page':False})
    item=make_book(tmp_path,sizes=[(800,500),(360,540),(360,540)])
    preparation=BrowserBookPreparation(lambda:target)
    try:
        start(preparation,item)
        assert wait_until(qapp,entered.is_set)
        runtime=preparation.session.viewer_runtime
        prepared=preparation.take(item.path,target)
        release.set()
        # Leave the finished header result queued ahead of the owner adoption.
        assert runtime.wait_for_done(3000)
        target.open_path(item.path,prepared_book=prepared)
        assert wait_until(qapp,lambda:target.viewer.displayed_page_indexes==(0,),timeout=5)
        assert target.book_session.viewer_runtime is runtime
        assert calls['00.png']==1
        assert target.model.get_image_size(0)==(800,500)
    finally:
        release.set();preparation.shutdown()


def test_preparing_cover_does_not_override_resume_position(tmp_path,qapp,target):
    item=make_book(tmp_path)
    target.settings['book_open_position']='resume_last'
    target.settings['reading_positions']={str(item.path):4}
    preparation=BrowserBookPreparation(lambda:target)
    try:
        start(preparation,item)
        assert wait_until(qapp,lambda:preparation.ready,timeout=5)
        assert target.settings['reading_positions'][str(item.path)]==4
        prepared=preparation.take(item.path,target)
        target.open_path(item.path,prepared_book=prepared)
        assert wait_until(qapp,lambda:4 in target.viewer.displayed_page_indexes,timeout=5)
        assert target.model.focused_index==4
    finally:
        preparation.shutdown()


def test_application_shutdown_resumes_after_native_preparation_finishes(tmp_path,qapp,monkeypatch):
    from tests.test_application_controller import make_controller,close_controller
    entered,release=Event(),Event()
    original=ZipImageSource.open_image
    def blocked(source,name):
        entered.set();assert release.wait(5)
        return original(source,name)
    monkeypatch.setattr(ZipImageSource,'open_image',blocked)
    controller=make_controller(tmp_path,qapp)
    try:
        start(controller.book_preparation,make_book(tmp_path))
        assert wait_until(qapp,entered.is_set)
        assert not controller.shutdown()
        release.set()
        # No manual shutdown retry: the retired owner notifies the controller.
        assert wait_until(qapp,lambda:controller._shutdown_complete,timeout=5)
    finally:
        release.set();close_controller(controller,qapp)
