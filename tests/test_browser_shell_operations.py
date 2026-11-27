"""Exercise Browser moves and progress with a fake Windows Shell."""
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
import ctypes
from threading import Event

import pytest

from app import windows_file_operations as shell
from app.file_operation_identity import FileIdentity
from app.file_operation_plan import FileOperationPlanner
from app.file_operation_service import (
    FileCollisionPolicy,
    FileOperationKind,
    FileOperationProgress,
    FileOperationRequest,
    FileOperationService,
)
from app.metadata_store import MetadataStore
from tests.test_browser_file_operations import close_window, make_window, select_paths


def _create(directory, *names):
    directory.mkdir(parents=True, exist_ok=True)
    paths = tuple(directory / name for name in names)
    for path in paths:
        path.write_text(path.name, encoding='utf-8')
    return paths


def _confirmed(paths):
    return tuple((str(path), FileIdentity.from_stat(path.lstat())) for path in paths)


def _move_request(paths, destination):
    request = FileOperationRequest(1, FileOperationKind.MOVE, tuple(map(str, paths)), str(destination))
    plan = FileOperationPlanner().prepare(request)
    assert plan.ready
    return replace(request, planned_sources=tuple((row.source_path, row.source_identity) for row in plan.items),
                   planned_destination=plan.destination_identity,
                   planned_total_bytes=plan.total_bytes,
                   planned_item_bytes=tuple((row.source_path, row.size_bytes) for row in plan.items))


@pytest.mark.parametrize('change', ['collision', 'replace_source'])
def test_late_move_conflict_keeps_successes_and_never_replays(tmp_path, change):
    paths = _create(tmp_path / 'from', '1 日本語.txt', '2.txt', '3.txt')
    target = tmp_path / 'to'
    target.mkdir()
    request = _move_request(paths, target)
    submissions = []

    def fake_shell(batch):
        submissions.append(tuple(batch.items))
        first = target / paths[0].name
        assert batch.before(str(paths[0]))
        paths[0].rename(first)
        batch.after(str(paths[0]), str(first), 0)
        batch.after(str(paths[0]), str(first), 0)  # Redundant notification.
        if change == 'collision':
            (target / paths[1].name).write_text('competitor', encoding='utf-8')
        else:
            paths[1].rename(paths[1].with_suffix('.old'))
            paths[1].write_text('replacement', encoding='utf-8')
        assert not batch.before(str(paths[1]))
        # A failing PreMoveItem also aborts every later queued item.
        return True, None

    progress = []
    result = FileOperationService(shell_move=fake_shell).execute(request, progress=progress.append)
    assert len(submissions) == 1 and len(submissions[0]) == 3
    assert result.cancelled and [item.success for item in result.items] == [True, False, False]
    assert result.items[1].error_code == ('collision' if change == 'collision' else 'io_error')
    assert result.items[2].error_code == 'cancelled'
    assert len(progress) == 3 and not result.undo_entries  # Undo remains all-or-nothing.
    assert paths[1].exists() and paths[2].exists()
    assert not (target / paths[2].name).exists()
    if change == 'collision':
        assert (target / paths[1].name).read_text(encoding='utf-8') == 'competitor'
    else:
        assert paths[1].read_text(encoding='utf-8') == 'replacement'


def test_shell_actual_late_rename_and_partial_cross_volume_result(tmp_path):
    paths = _create(tmp_path / 'from', '1.txt', '2.txt')
    target = tmp_path / 'to'
    target.mkdir()
    request = _move_request(paths, target)

    def fake_shell(batch):
        assert batch.before(str(paths[0])) and batch.before(str(paths[1]))
        exact = target / '1.txt'
        exact.write_text('late arrival', encoding='utf-8')
        renamed = target / '1 (2).txt'
        paths[0].rename(renamed)
        batch.after(str(paths[0]), str(renamed), 0)
        partial = target / '2.txt'
        partial.write_bytes(paths[1].read_bytes())
        batch.after(str(paths[1]), str(partial), shell._FAIL)
        return True, 'part of the batch failed'

    result = FileOperationService(shell_move=fake_shell).execute(request)
    assert result.successes[0].destination_path == str(target / '1 (2).txt')
    assert result.failures[0].destination_published and not result.failures[0].source_removed
    assert not result.undo_entries
    assert (target / '1.txt').read_text(encoding='utf-8') == 'late arrival'


def test_successful_shell_batch_keeps_normal_undo_and_directory_identity(tmp_path):
    source, = _create(tmp_path / 'from', '1.txt')
    folder = source.parent / '2 folder'
    _create(folder, 'child.txt')
    target = tmp_path / 'to'
    target.mkdir()

    def fake_shell(batch):
        for path, _ in batch.items.values():
            assert batch.before(path)
            destination = target / Path(path).name
            Path(path).rename(destination)
            batch.after(path, str(destination), 0)
        return False, None

    service = FileOperationService(shell_move=fake_shell)
    result = service.execute(_move_request((source, folder), target))
    assert [item.source_is_directory for item in result.successes] == [False, True]
    assert len(result.undo_entries) == 2
    undo = service.execute(FileOperationRequest(2, FileOperationKind.UNDO, undo_entries=result.undo_entries))
    assert len(undo.successes) == 2
    assert source.exists() and (folder / 'child.txt').is_file()


def test_com_adapter_registers_all_items_queries_aborted_even_if_perform_fails(tmp_path, monkeypatch):
    paths = _create(tmp_path / 'from', '1.txt', '2.txt')
    target = tmp_path / 'to'
    target.mkdir()
    results = []
    batch = shell.MoveBatch(_confirmed(paths), str(target), Event(), lambda *args: results.append(args))
    calls = []
    item_paths = {100: str(target), 101: str(paths[0]), 102: str(paths[1])}
    item_ids = {value: key for key, value in item_paths.items()}
    sink_pointer = None

    def create(_clsid, _outer, _context, _iid, out):
        out._obj.value = 10
        return 0

    def invoke(pointer, slot, types=(), args=(), **_kwargs):
        nonlocal sink_pointer
        calls.append(slot)
        if slot == 3:
            sink_pointer = args[0]
            args[1]._obj.value = 2
        if slot == 5:
            assert args[0] & shell._FOF_RENAMEONCOLLISION and not args[0] & 0x10
        if slot == 14:
            assert args[1] == 100 and args[3] is None
        if slot == 21:
            pre_move = shell._method(sink_pointer, 7, shell._HRESULT, ctypes.c_ulong,
                                     ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p)
            assert pre_move(sink_pointer, 0, 101, 100, None) == 0
            return shell._FAIL
        if slot == 22:
            args[0]._obj.value = True
        return 0

    monkeypatch.setattr(shell.C.windll.ole32, 'CoCreateInstance', create)
    monkeypatch.setattr(shell, '_apartment', nullcontext)
    monkeypatch.setattr(shell, '_shell_item', item_ids.__getitem__)
    monkeypatch.setattr(shell, '_filesystem_path', item_paths.get)
    monkeypatch.setattr(shell, '_call', invoke)
    aborted, error = shell.perform_moves(batch)
    assert aborted and error and not results
    assert calls[:7] == [5, 3, 14, 14, 21, 22, 2]
    assert calls.count(21) == calls.count(22) == 1 and 4 in calls


@pytest.mark.parametrize('change', ['replacement', 'cancel', 'shell_cancel'])
def test_recycle_only_checks_path_and_cancel_before_the_native_verb(tmp_path, monkeypatch, change):
    source, = _create(tmp_path / 'from', '日本語.txt')
    _, identity = _confirmed((source,))[0]
    cancelled = Event()
    calls = []

    def volume_path(_path, out, _length):
        out.value = source.anchor
        return True

    def drive_type(_root):
        return 3

    def invoke(_pointer, slot, types=(), args=(), **_kw):
        calls.append(slot)
        if slot == 3:
            args[-1]._obj.value = 3
            if change == 'replacement':
                source.rename(source.with_suffix('.original'))
                source.write_text('different item', encoding='utf-8')
            elif change == 'cancel':
                cancelled.set()
        if slot == 8:
            return shell._COPYENGINE_E_USER_CANCELLED
        return 0

    monkeypatch.setattr(shell.C.windll.kernel32, 'GetVolumePathNameW', volume_path)
    monkeypatch.setattr(shell.C.windll.kernel32, 'GetDriveTypeW', drive_type)
    monkeypatch.setattr(shell, '_shell_item', lambda _path: 4)
    monkeypatch.setattr(shell, '_call', invoke)
    adapter = shell.RecycleOnly()
    adapter._bin = 7
    expected = OSError if change == 'replacement' else InterruptedError
    with pytest.raises(expected, match='source_changed' if change == 'replacement' else 'recycle_cancelled'):
        adapter.recycle(str(source), identity, cancelled=cancelled)
    assert calls.count(8) == int(change == 'shell_cancel')
    assert source.exists()


def test_conflict_resolution_stays_with_safe_existing_path(tmp_path):
    source, = _create(tmp_path / 'from', 'same.txt')
    existing, = _create(tmp_path / 'to', 'same.txt')
    existing.write_text('destination', encoding='utf-8')
    request = FileOperationRequest(1, FileOperationKind.MOVE, (str(source),), str(existing.parent),
                                   collision_policy=FileCollisionPolicy.KEEP_BOTH,
                                   planned_sources=_confirmed((source,)),
                                   planned_destination=_confirmed((existing.parent,))[0][1])
    service = FileOperationService(shell_move=lambda _: pytest.fail('Conflict must use the existing route'))
    result = service.execute(request)
    assert result.successes[0].destination_path != str(existing)
    assert existing.read_text(encoding='utf-8') == 'destination'


def test_metadata_batch_uses_exact_path_and_folder_uses_indexed_prefix(tmp_path, qapp):
    store = MetadataStore(tmp_path / 'metadata.sqlite3')
    old = tmp_path / 'book.zip'
    new = tmp_path / 'moved.zip'
    inside = old / 'child.png'
    adjacent = tmp_path / 'book.zip-plus' / 'child.png'
    for path, tag in ((old, 'Book'), (inside, 'Child'), (adjacent, 'Sibling')):
        store.set_tags(str(path), [tag])
    changes = []
    store.bookmarks_changed.connect(lambda: changes.append(True))
    try:
        assert store.relocate_items(((str(old), str(new)),))
        assert store.get_tags(str(new)) == ['Book']
        assert store.get_tags(str(inside)) == ['Child']
        assert changes == [True]
        assert store.relocate_tree(str(old), str(new))
        assert store.get_tags(str(new / 'child.png')) == ['Child']
        assert store.get_tags(str(adjacent)) == ['Sibling']
    finally:
        store.close()


def test_browser_removes_recycled_row_on_progress_without_scan(tmp_path, qapp, monkeypatch):
    first, second = _create(tmp_path / 'books', '1.txt', '2.txt')
    window, coordinator = make_window(tmp_path, qapp, first.parent)
    try:
        select_paths(window, [first, second])
        monkeypatch.setattr(window, 'refresh_current_folder', lambda: pytest.fail('Progress must not rescan'))
        window._active_file_operation_id = 7
        progress = FileOperationProgress(7, FileOperationKind.RECYCLE, 1, 2, removed_sources=(str(first),))
        window._on_file_operation_progress(progress)
        window._on_file_operation_progress(progress)
        assert window.item_model.row_for_path(first) == -1
        assert window.selected_file_operation_paths() == (str(second),)
    finally:
        window._active_file_operation_id = None
        close_window(window, coordinator, qapp)
