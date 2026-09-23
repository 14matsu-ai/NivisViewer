"""New undo policy tested with a typed facade and a no-clobber temp executor.

This is deliberately not evidence that the Windows recycle-bin API or the
complete production service has run. See the separately marked Qt tests.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event
from types import ModuleType
import os
import stat
import sys
import pytest

from app.file_operation_undo import make_undo_entries, run_undo, safe_stamp


class Kind(str, Enum):
    RENAME='rename'; COPY='copy'; MOVE='move'; RECYCLE='recycle'
    CREATE_DIRECTORY='create_directory'; CREATE_ZIP='create_zip'; UNDO='undo'
class State(str,Enum):
    COMPLETED='completed'; SKIPPED='skipped'
class Collision(str,Enum):
    SKIP='skip'
@dataclass(frozen=True)
class Item:
    source_path: str|None
    destination_path: str|None
    success: bool
    error_code: str|None=None
    error_message: str|None=None
    partial_success: bool=False
    state: State=State.COMPLETED
    operation: Kind|None=None
    replaced_existing: bool=False
    destination_existed_before: bool=False
    child_results: tuple=()
    partially_completed: bool=False
    source_removed: bool=False
    source_exists_after: bool|None=None
@dataclass(frozen=True)
class Result:
    operation: Kind
    items: tuple
    cancelled: bool=False
    request_id: int=1
    operation_id: str='1'
@dataclass(frozen=True)
class Request:
    request_id:int
    operation:Kind
    source_paths:tuple=()
    destination_directory:str|None=None
    new_name:str|None=None
    collision_policy:Collision=Collision.SKIP
    operation_id:str='1'
    undo_entries:tuple=()
@dataclass
class Progress:
    request_id:int
    operation:Kind
    completed:int
    total:int
    source_path:str|None=None
    operation_id:str='1'

@pytest.fixture(autouse=True)
def facade(monkeypatch):
    module=ModuleType('app.file_operation_service')
    for name,value in {'FileOperationKind':Kind,'FileOperationItemResult':Item,
                       'FileOperationItemState':State,'FileOperationRequest':Request,
                       'FileOperationResult':Result,'FileOperationProgress':Progress,
                       'FileCollisionPolicy':Collision}.items():
        setattr(module,name,value)
    monkeypatch.setitem(sys.modules,'app.file_operation_service',module)

class TempExecutor:
    def __init__(self,root): self.root=root; self.calls=[]
    def _execute_request(self,request,*,cancelled=None):
        self.calls.append(request)
        src=Path(request.source_paths[0])
        if request.operation is Kind.RECYCLE:
            dst=self.root/'trash'/src.name
            dst.parent.mkdir(exist_ok=True)
        elif request.operation is Kind.RENAME:
            dst=src.with_name(request.new_name)
        else:
            dst=Path(request.destination_directory)/src.name
        if dst.exists():
            return Result(request.operation,(Item(str(src),str(dst),False),))
        src.rename(dst)
        return Result(request.operation,(Item(str(src),str(dst) if request.operation is not Kind.RECYCLE else None,True),))


def receipt(path,kind=Kind.COPY,source=None,**kw):
    return make_undo_entries(Result(kind,(Item(str(source) if source else None,str(path),True,**kw),)))


def undo(path,entries,executor=None,cancelled=None):
    worker=executor or TempExecutor(path)
    result=run_undo(worker,Request(2,Kind.UNDO,undo_entries=entries),cancelled)
    return result,worker


def test_copy_undo_recycles_only_created_file(tmp_path):
    original=tmp_path/'original.txt'; original.write_text('original')
    copied=tmp_path/'copy.txt'; copied.write_text('original')
    entries=receipt(copied,source=original)
    result,worker=undo(tmp_path,entries)
    assert result.items[0].success and not copied.exists()
    assert original.read_text()=='original'
    assert (tmp_path/'trash'/'copy.txt').exists()
    assert worker.calls[0].operation is Kind.RECYCLE

@pytest.mark.parametrize('kind',[Kind.RENAME,Kind.MOVE])
def test_rename_move_reverse(tmp_path,kind):
    before=tmp_path/'before'/'page.jpg'; before.parent.mkdir()
    after=(tmp_path/'after'/'page.jpg') if kind is Kind.MOVE else before.with_name('new.jpg')
    after.parent.mkdir(exist_ok=True); after.write_text('pixels')
    entries=receipt(after,kind,source=before)
    assert entries
    result,_=undo(tmp_path,entries)
    assert result.items[0].success and before.read_text()=='pixels' and not after.exists()
    assert result.items[0].operation is kind

@pytest.mark.parametrize('change',['edit','replace','remove','symlink'])
def test_changed_target_never_touched(tmp_path,change,monkeypatch):
    path=tmp_path/'created.jpg'; path.write_text('old')
    entries=receipt(path)
    other=tmp_path/'unrelated.jpg'; other.write_text('other')
    if change=='edit': path.write_text('new content')
    elif change=='replace':
        replacement=tmp_path/'replacement'; replacement.write_text('new'); os.replace(replacement,path)
    elif change=='remove': path.unlink()
    else:
        path.write_text('new')
        real_lstat=os.lstat
        def reparse_lstat(candidate):
            result=real_lstat(candidate)
            if os.path.normcase(os.path.abspath(candidate))==os.path.normcase(os.path.abspath(path)):
                return type('StatWithReparseMode',(),{
                    'st_mode':stat.S_IFLNK | stat.S_IFREG,
                    'st_ino':result.st_ino,'st_dev':result.st_dev,
                    'st_size':result.st_size,'st_mtime_ns':result.st_mtime_ns,
                    'st_file_attributes':0,
                })()
            return result
        monkeypatch.setattr('app.file_operation_undo.os.lstat',reparse_lstat)
    result,worker=undo(tmp_path,entries)
    assert not worker.calls and not result.items[0].success
    assert other.read_text()=='other'


def test_old_name_occupied_is_not_overwritten(tmp_path):
    old=tmp_path/'old'; new=tmp_path/'new'; new.write_text('keep')
    entries=receipt(new,Kind.RENAME,source=old)
    old.write_text('new unrelated file')
    result,worker=undo(tmp_path,entries)
    assert not worker.calls and not result.items[0].success
    assert old.read_text()=='new unrelated file' and new.read_text()=='keep'


def test_empty_directory_removed_nonrecursively(tmp_path):
    path=tmp_path/'empty'; path.mkdir()
    entries=receipt(path,Kind.CREATE_DIRECTORY)
    result,_=undo(tmp_path,entries)
    assert result.items[0].success and not path.exists()


def test_new_directory_content_protected(tmp_path):
    path=tmp_path/'empty'; path.mkdir()
    entries=receipt(path,Kind.CREATE_DIRECTORY)
    (path/'new.txt').write_text('keep')
    result,worker=undo(tmp_path,entries)
    assert not worker.calls and not result.items[0].success
    assert (path/'new.txt').exists()

@pytest.mark.parametrize('kwargs',[{'replaced_existing':True},{'destination_existed_before':True},
    {'partial_success':True},{'partially_completed':True},{'child_results':(1,)}])
def test_unsafe_batches_have_no_receipt(tmp_path,kwargs):
    path=tmp_path/'x'; path.write_text('x')
    assert not receipt(path,**kwargs)


def test_nonempty_copy_folder_not_recursively_deleted(tmp_path):
    path=tmp_path/'folder'; path.mkdir(); (path/'x').write_text('x')
    assert not receipt(path)


def test_new_keep_both_move_name_not_guessed(tmp_path):
    path=tmp_path/'name (2)'; path.write_text('x')
    assert not receipt(path,Kind.MOVE,source=tmp_path/'name')


def test_cancel_does_nothing(tmp_path):
    path=tmp_path/'copy'; path.write_text('x')
    cancel=Event(); cancel.set()
    result,worker=undo(tmp_path,receipt(path),cancelled=cancel)
    assert result.cancelled and not worker.calls and path.exists()


def test_reparse_parent_is_not_a_safe_receipt(tmp_path,monkeypatch):
    link=tmp_path/'link'; link.mkdir(); (link/'x').write_text('x')
    real_lstat=os.lstat
    def reparse_lstat(candidate):
        result=real_lstat(candidate)
        if os.path.normcase(os.path.abspath(candidate))==os.path.normcase(os.path.abspath(link)):
            return type('StatWithReparseMode',(),{
                'st_mode':result.st_mode,'st_ino':result.st_ino,
                'st_dev':result.st_dev,'st_size':result.st_size,
                'st_mtime_ns':result.st_mtime_ns,'st_file_attributes':0x400,
            })()
        return result
    monkeypatch.setattr('app.file_operation_undo.os.lstat',reparse_lstat)
    assert safe_stamp(str(link/'x')) is None


def test_unsupported_recycle_is_barrier(tmp_path):
    path=tmp_path/'x'; path.write_text('x')
    assert not receipt(path,Kind.RECYCLE)


def test_inverse_plan_checks_all_collisions_before_each_move(tmp_path):
    path=tmp_path/'new'; path.write_text('keep')
    old=tmp_path/'old'; entries=receipt(path,Kind.RENAME,source=old)
    class RacingExecutor(TempExecutor):
        def _execute_request(self, request, **kwargs):
            old.write_text('racer')
            return super()._execute_request(request,**kwargs)
    result,worker=undo(tmp_path,entries,RacingExecutor(tmp_path))
    assert not result.items[0].success and path.exists() and old.read_text()=='racer'
