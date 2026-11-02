from zipfile import ZipFile
import pytest
from tests.test_application_controller import make_controller, write_image, write_archive, close_controller, wait_until
from tests.test_viewer_side_folders import finish_navigation



def test_recursive_book_is_not_skipped(tmp_path, qapp):
    root = tmp_path / 'books'
    write_image(root / '01' / '1.png')
    write_image(root / '02' / 'nested' / '1.png')
    write_image(root / '03' / '1.png')
    c = make_controller(tmp_path, qapp)
    c.config.apply({'mouse_side_buttons_folder_navigation': True, 'recursive_folder': True})
    try:
        v = c.open_path(root / '01')
        finish_navigation(c,qapp,v)
        assert v.recursive_folder
        v._on_extra_mouse_button('forward')
        finish_navigation(c,qapp,v)
        assert v.book_session.current_path == root / '02'
    finally:
        close_controller(c,qapp)

def test_empty_archives_do_not_retry_same_candidate_forever(tmp_path, qapp, monkeypatch):
    root = tmp_path / 'books'
    write_image(root / 'a.png')
    for name in ('b.cbz','c.cbz'):
        with ZipFile(root / name, 'w'):
            pass
    c = make_controller(tmp_path,qapp)
    c.config.apply({'mouse_side_buttons_folder_navigation':True, 'loop_book_navigation':True, 'browser_folders_first':False})
    attempts=[]
    try:
        b=c.create_browser_window()
        b.set_current_folder(root)
        assert b.wait_for_scan()
        v=c.open_path(root/'a.png', browser_snapshot=b.adjacent_book_snapshot(root))
        finish_navigation(c,qapp,v)
        original=c._open_side_folder_candidate
        def bounded(window,candidate,snapshot,**kwargs):
            attempts.append(candidate)
            if len(attempts)>=4:
                c.config.apply({'mouse_side_buttons_folder_navigation':False})
                return 'boundary'
            return original(window,candidate,snapshot,**kwargs)
        monkeypatch.setattr(c,'_open_side_folder_candidate',bounded)
        v._on_extra_mouse_button('forward')
        wait_until(qapp,lambda:len(attempts)>=4,timeout=3)
        assert len(attempts) <= 2, attempts
    finally:
        c.config.apply({'mouse_side_buttons_folder_navigation':False})
        close_controller(c,qapp)

def test_updated_browser_mixed_listing_is_used(tmp_path,qapp):
    root=tmp_path/'books'
    write_image(root/'a.png')
    c=make_controller(tmp_path,qapp)
    c.config.apply({'mouse_side_buttons_folder_navigation':True, 'browser_folders_first':False})
    try:
        b=c.create_browser_window()
        b.set_current_folder(root)
        assert b.wait_for_scan()
        v=c.open_path(root/'a.png',browser_snapshot=b.adjacent_book_snapshot(root))
        finish_navigation(c,qapp,v)
        write_archive(root/'b.cbz')
        b.navigate_to(root,force_reload=True)
        assert b.wait_for_scan()
        assert any(e.item_kind=='archive' for e in b.adjacent_book_snapshot(root).entries)
        v._on_extra_mouse_button('forward')
        finish_navigation(c,qapp,v)
        assert v.book_session.current_path == root/'b.cbz'
    finally:
        close_controller(c,qapp)
