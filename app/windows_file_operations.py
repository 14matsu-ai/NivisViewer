"""The small Windows Shell COM surface used by Browser moves and recycling.

This module is also used by the independent recycle worker. Keep it free of Qt,
the GUI profile and application state.
"""
from __future__ import annotations

import ctypes as C
import os
from contextlib import ExitStack, contextmanager
from ctypes import wintypes as W
from dataclasses import dataclass
from uuid import UUID

from .file_operation_identity import FileIdentity


_P = C.c_void_p
_HRESULT = C.c_int32
_GUID = C.c_ubyte * 16
_IID_ITEM = '43826d1e-e718-42ee-bc55-a1e261c37bfe'
_IID_OPERATION = '947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8'
_CLSID_OPERATION = '3ad05575-8857-4850-9277-11b85bdb8e09'
_IID_PROGRESS = '04b0f1a7-9490-44bc-96e1-4296a31252e2'
_IID_UNKNOWN = '00000000-0000-0000-c000-000000000046'
_BHID_TRANSFER = 'd5e346a1-f753-4932-b403-4574800e2498'
_IID_TRANSFER_SOURCE = '00adb003-bde9-45c6-8e29-d09f9353e108'
_RECYCLE_FOLDER = 'b7534046-3ecb-4c18-be4e-64cd4cb7d6ac'
_CANCELLED = _HRESULT(0x800704C7).value
_FAIL = _HRESULT(0x80004005).value
_FOF_SILENT = 0x0004
_FOF_RENAMEONCOLLISION = 0x0008
_FOF_NOERRORUI = 0x0400
_FOFX_PRESERVEFILEEXTENSIONS = 0x00200000
_MOVE_FLAGS = _FOF_SILENT | _FOF_RENAMEONCOLLISION | _FOF_NOERRORUI | _FOFX_PRESERVEFILEEXTENSIONS


def _guid(value: str):
    return _GUID.from_buffer_copy(UUID(value).bytes_le)


def _method(pointer, slot, returns, *arguments):
    table = C.cast(pointer, C.POINTER(C.POINTER(_P)))[0]
    return C.WINFUNCTYPE(returns, _P, *arguments)(table[slot])


def _call(pointer, slot, types=(), args=(), *, returns=_HRESULT):
    return _method(pointer, slot, returns, *types)(pointer, *args)


def _check(hr, operation):
    if hr < 0:
        raise OSError(f'{operation}: HRESULT 0x{hr & 0xffffffff:08X}')


@contextmanager
def _apartment():
    init = C.windll.ole32.CoInitializeEx
    init.argtypes, init.restype = [_P, W.DWORD], _HRESULT
    _check(init(None, 2), 'CoInitializeEx')  # COINIT_APARTMENTTHREADED
    try:
        yield
    finally:
        C.windll.ole32.CoUninitialize()


def _release(pointer):
    if pointer:
        _call(pointer, 2, returns=W.ULONG)


def _shell_item(path):
    create = C.windll.shell32.SHCreateItemFromParsingName
    create.argtypes = [W.LPCWSTR, _P, C.POINTER(_GUID), C.POINTER(_P)]
    create.restype = _HRESULT
    result = _P()
    _check(create(path, None, _guid(_IID_ITEM), C.byref(result)), 'SHCreateItemFromParsingName')
    return result


def _filesystem_path(item):
    if not item:
        return None
    value = _P()
    try:
        if _call(item, 5, (W.DWORD, C.POINTER(_P)), (0x80058000, C.byref(value))) < 0:
            return None
        return C.wstring_at(value) if value else None
    finally:
        if value:
            free = C.windll.ole32.CoTaskMemFree
            free.argtypes, free.restype = [_P], None
            free(value)


@dataclass(frozen=True)
class ShellMoveResult:
    destination: str | None = None
    error: str | None = None


class MoveBatch:
    """Correlate per-item Shell callbacks with the preflight snapshot."""

    def __init__(self, items, destination, cancelled, on_result):
        self.items = {self.key(path): (path, identity) for path, identity in items}
        self.destination = destination
        self.cancelled = cancelled
        self.on_result = on_result
        self.completed = set()
        self.entered = set()

    @staticmethod
    def key(path):
        return os.path.normcase(os.path.normpath(path)) if path else None

    def before(self, source):
        if self.cancelled.is_set():
            return False
        key = self.key(source)
        if key is None:
            return False
        if key not in self.items:
            return any(key.startswith(root + os.sep) and self.items[root][1].is_directory
                       for root in self.entered)
        if key in self.completed:
            return False
        path, identity = self.items[key]
        if not identity.still_at(path):
            self.record(key, ShellMoveResult(error='source_changed'))
            return False
        try:
            os.lstat(os.path.join(self.destination, os.path.basename(path)))
        except FileNotFoundError:
            self.entered.add(key)
            return True
        except OSError as exc:
            self.record(key, ShellMoveResult(error=str(exc)))
        else:
            self.record(key, ShellMoveResult(error='collision'))
        return False

    def after(self, source, destination, hr):
        key = self.key(source)
        if key not in self.entered or key in self.completed:
            return
        error = f'Shell move HRESULT 0x{hr & 0xffffffff:08X}'
        if destination and self.key(os.path.dirname(destination)) == self.key(self.destination):
            self.record(key, ShellMoveResult(destination=destination, error=error if hr < 0 else None))
        else:
            self.record(key, ShellMoveResult(error=error))

    def record(self, key, result):
        if key not in self.completed:
            self.completed.add(key)
            self.on_result(self.items[key][0], result)


class _ProgressSink:
    """IFileOperationProgressSink vtable, owned through Unadvise/Release."""

    def __init__(self, batch):
        self.batch = batch
        self.references = 1
        self.functions = []

        def entry(function, *args, returns=_HRESULT):
            def callback(_self, *values):
                try:
                    return function(*values)
                except Exception:
                    return _FAIL if returns is _HRESULT else 0
            self.functions.append(C.WINFUNCTYPE(returns, _P, *args)(callback))

        def noop(*_args):
            return 0

        entry(self.query, C.POINTER(_GUID), C.POINTER(_P))
        entry(self.add_ref, returns=W.ULONG)
        entry(self.release, returns=W.ULONG)
        entry(noop)                                       # StartOperations
        entry(noop, _HRESULT)                             # FinishOperations
        entry(noop, W.DWORD, _P, W.LPCWSTR)                # PreRenameItem
        entry(noop, W.DWORD, _P, W.LPCWSTR, _HRESULT, _P)  # PostRenameItem
        entry(self.pre_move, W.DWORD, _P, _P, W.LPCWSTR)
        entry(self.post_move, W.DWORD, _P, _P, W.LPCWSTR, _HRESULT, _P)
        entry(noop, W.DWORD, _P, _P, W.LPCWSTR)            # PreCopyItem
        entry(noop, W.DWORD, _P, _P, W.LPCWSTR, _HRESULT, _P)
        entry(noop, W.DWORD, _P)                          # PreDeleteItem
        entry(noop, W.DWORD, _P, _HRESULT, _P)
        entry(noop, W.DWORD, _P, W.LPCWSTR)                # PreNewItem
        entry(noop, W.DWORD, _P, W.LPCWSTR, W.LPCWSTR, W.DWORD, _HRESULT, _P)
        entry(lambda *_: _CANCELLED if batch.cancelled.is_set() else 0, W.UINT, W.UINT)
        for _ in range(3):                                # Reset/Pause/ResumeTimer
            entry(noop)
        self.vtable = (_P * len(self.functions))(*(C.cast(f, _P) for f in self.functions))
        self.object = C.pointer(self.vtable)
        self.pointer = C.cast(C.pointer(self.object), _P)

    def query(self, iid, result):
        if bytes(iid.contents) not in (bytes(_guid(_IID_UNKNOWN)), bytes(_guid(_IID_PROGRESS))):
            result[0] = None
            return _HRESULT(0x80004002).value  # E_NOINTERFACE
        result[0] = self.pointer
        self.add_ref()
        return 0

    def add_ref(self):
        self.references += 1
        return self.references

    def release(self):
        self.references -= 1
        return self.references

    def pre_move(self, _flags, item, _parent, _name):
        return 0 if self.batch.before(_filesystem_path(item)) else _CANCELLED

    def post_move(self, _flags, item, parent, name, hr, created):
        destination = _filesystem_path(created)
        if destination is None and hr == 0 and name and name == os.path.basename(name):
            root = _filesystem_path(parent)
            if root:
                destination = os.path.join(root, name)
        self.batch.after(_filesystem_path(item), destination, hr)
        return 0


def perform_moves(batch: MoveBatch) -> tuple[bool, str | None]:
    """Queue every item and invoke PerformOperations once. Never retry it."""
    if os.name != 'nt':
        return False, 'api_unavailable'
    with _apartment(), ExitStack() as owned:
        create = C.windll.ole32.CoCreateInstance
        create.argtypes = [C.POINTER(_GUID), _P, W.DWORD, C.POINTER(_GUID), C.POINTER(_P)]
        create.restype = _HRESULT
        operation = _P()
        _check(create(_guid(_CLSID_OPERATION), None, 1, _guid(_IID_OPERATION), C.byref(operation)), 'IFileOperation')
        owned.callback(_release, operation)
        _check(_call(operation, 5, (W.DWORD,), (_MOVE_FLAGS,)), 'SetOperationFlags')
        destination = _shell_item(batch.destination)
        owned.callback(_release, destination)
        sink = _ProgressSink(batch)
        cookie = W.DWORD()
        _check(_call(operation, 3, (_P, C.POINTER(W.DWORD)), (sink.pointer, C.byref(cookie))), 'Advise')
        owned.callback(_call, operation, 4, (W.DWORD,), (cookie,))
        for path, _identity in batch.items.values():
            if batch.cancelled.is_set():
                return True, None
            try:
                item = _shell_item(path)
                owned.callback(_release, item)
                _check(_call(operation, 14, (_P, _P, W.LPCWSTR, _P), (item, destination, None, None)), 'MoveItem')
            except OSError as exc:
                batch.record(batch.key(path), ShellMoveResult(error=str(exc)))
        if len(batch.completed) == len(batch.items) or batch.cancelled.is_set():
            return batch.cancelled.is_set(), None
        hr = _call(operation, 21)
        aborted = W.BOOL()
        query_hr = _call(operation, 22, (C.POINTER(W.BOOL),), (C.byref(aborted),))
        error = None if hr >= 0 and query_hr >= 0 else (
            f'PerformOperations 0x{hr & 0xffffffff:08X}, GetAnyOperationsAborted 0x{query_hr & 0xffffffff:08X}'
        )
        return bool(aborted.value) or hr == _CANCELLED, error


class RecycleOnly:
    """Use the Shell transfer provider's recycle verb. There is no delete verb."""

    def __enter__(self):
        self._owned = ExitStack()
        try:
            self._owned.enter_context(_apartment())
            find = C.windll.shell32.SHGetKnownFolderItem
            find.argtypes = [C.POINTER(_GUID), W.DWORD, W.HANDLE, C.POINTER(_GUID), C.POINTER(_P)]
            find.restype = _HRESULT
            self._bin = _P()
            _check(find(_guid(_RECYCLE_FOLDER), 0, None, _guid(_IID_ITEM), C.byref(self._bin)), 'SHGetKnownFolderItem')
            self._owned.callback(_release, self._bin)
            return self
        except BaseException:
            self._owned.close()
            raise

    def __exit__(self, *_exc):
        self._owned.close()

    def recycle(self, path, identity):
        with ExitStack() as owned:
            root = C.create_unicode_buffer(32768)
            volume = C.windll.kernel32.GetVolumePathNameW
            volume.argtypes, volume.restype = [W.LPCWSTR, W.LPWSTR, W.DWORD], W.BOOL
            drive_type = C.windll.kernel32.GetDriveTypeW
            drive_type.argtypes, drive_type.restype = [W.LPCWSTR], W.UINT
            if not volume(path, root, len(root)) or drive_type(root.value) != 3 or MoveBatch.key(path) == MoveBatch.key(root.value):
                raise OSError('recycle_not_supported')  # fixed local volumes only; never recycle a volume root
            item = _shell_item(path)
            owned.callback(_release, item)
            parent = _shell_item(os.path.dirname(path))
            owned.callback(_release, parent)
            transfer = _P()
            _check(_call(parent, 3, (_P, C.POINTER(_GUID), C.POINTER(_GUID), C.POINTER(_P)),
                         (None, _guid(_BHID_TRANSFER), _guid(_IID_TRANSFER_SOURCE), C.byref(transfer))), 'BHID_Transfer')
            owned.callback(_release, transfer)
            if not identity.still_at(path):
                raise OSError('source_changed')
            recycled = _P()
            try:
                hr = _call(transfer, 8, (_P, _P, W.DWORD, C.POINTER(_P)),
                           (item, self._bin, 0, C.byref(recycled)))
                _check(hr, 'RecycleItem')
                if not recycled:
                    raise OSError('recycle_not_confirmed')
                try:
                    current = FileIdentity.from_stat(os.lstat(path))
                except FileNotFoundError:
                    pass
                else:
                    if (current.device, current.inode) == (identity.device, identity.inode):
                        raise OSError('recycle_not_confirmed')
            finally:
                _release(recycled)
