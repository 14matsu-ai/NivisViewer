"""Exercise the Browser operation boundaries with a fake Shell and helper."""
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
import ctypes
import json
import os
import runpy
import sys
from threading import Event, Thread
from time import time_ns

import pytest

from app import recycle_jobs, windows_file_operations as shell
from app.application_controller import ApplicationController, FileOperationCloseChoice
from app.config_manager import ConfigManager
from app.file_operation_identity import FileIdentity
from app.file_operation_plan import FileOperationPlanner
from app.file_operation_queue import FileOperationQueue
from app.file_operation_service import (
    FileCollisionPolicy,
    FileOperationKind,
    FileOperationProgress,
    FileOperationRequest,
    FileOperationService,
)
from app.metadata_store import MetadataStore
from tests.test_browser_file_operations import close_window, make_window, select_paths
from tests.test_zip_raster_viewer_integration import _wait_until


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


class _Process:
    pid = 1234

    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


def _fake_bin(directory, *, fail=(), entered=None, release=None):
    class Backend:
        def recycle(self, path, _identity):
            if entered is not None:
                entered.set()
                assert release.wait(6)
            item = Path(path)
            if item.name in fail:
                raise OSError('recycle_not_supported')
            item.rename(directory / item.name)
    return lambda: nullcontext(Backend())


def test_unobserved_job_finishes_without_parent_and_cannot_replay(tmp_path):
    paths = _create(tmp_path / 'from', '1 日本語.txt', '2.txt', '3.txt')
    target = tmp_path / 'fake-bin'
    target.mkdir()
    spool = tmp_path / recycle_jobs.SPOOL_NAME
    jobs = recycle_jobs.RecycleJobs(spool, launch=lambda _: _Process())
    directory = jobs.submit(_confirmed(paths)).directory  # The GUI observer is gone.
    paths[1].rename(paths[1].with_suffix('.old'))
    paths[1].write_text('new file', encoding='utf-8')
    fake = _fake_bin(target, fail={'3.txt'})
    assert recycle_jobs.run_job(directory, backend=fake) == 0
    assert (target / paths[0].name).exists() and paths[2].exists()
    paths[0].write_text('new file after successful recycle', encoding='utf-8')
    assert recycle_jobs.run_job(directory, backend=fake) == 2
    assert paths[0].exists() and paths[1].read_text(encoding='utf-8') == 'new file'
    assert jobs.recover(created_before=time_ns()) == (((1, 2),), False)
    assert jobs.recover(created_before=time_ns()) == ((), False)


@pytest.mark.parametrize('fail_launch', [True, False])
def test_recycle_launch_or_helper_exit_reports_failure_without_retry(tmp_path, fail_launch):
    paths = _create(tmp_path / 'from', '1.txt', '2.txt')
    launched = []

    def launch(directory):
        launched.append(directory)
        if fail_launch:
            raise OSError('cannot start process')
        process = _Process()
        process.code = 9
        return process

    service = FileOperationService(recycle_jobs=recycle_jobs.RecycleJobs(tmp_path / recycle_jobs.SPOOL_NAME, launch=launch))
    request = FileOperationRequest(1, FileOperationKind.RECYCLE, tuple(map(str, paths)), planned_sources=_confirmed(paths))
    result = service.execute_detached_recycle(request, Event(), Event(), Event(), None)
    assert len(launched) == 1 and len(result.failures) == 2 and all(path.exists() for path in paths)
    error = 'recycle_launch_failed' if fail_launch else 'recycle_worker_exit'
    assert all(error in item.error_message for item in result.failures)


def test_killed_helper_reports_journalled_success_once_and_unfinished_items(tmp_path):
    paths = _create(tmp_path / 'from', '1.txt', '2.txt')
    target = tmp_path / 'recycled.txt'
    launch_count = []

    def launch(directory):
        launch_count.append(directory)
        (directory / 'claimed').mkdir()
        paths[0].rename(target)
        row = json.dumps({'index': 0, 'success': True}) + '\n'
        (directory / 'outcomes.jsonl').write_text(row * 2 + '{', encoding='ascii')
        process = _Process()
        process.code = 9
        return process

    updates = []
    jobs = recycle_jobs.RecycleJobs(tmp_path / recycle_jobs.SPOOL_NAME, launch=launch)
    request = FileOperationRequest(1, FileOperationKind.RECYCLE, tuple(map(str, paths)), planned_sources=_confirmed(paths))
    result = FileOperationService(recycle_jobs=jobs).execute_detached_recycle(
        request, Event(), Event(), Event(), updates.append,
    )
    assert len(launch_count) == 1 and len(result.successes) == len(result.failures) == 1
    assert len(updates) == 1 and updates[0].removed_sources == (str(paths[0]),)
    assert target.exists() and paths[1].exists()


def test_expired_unaccepted_job_cannot_execute_after_late_launch(tmp_path, monkeypatch):
    paths = _create(tmp_path / 'from', '1.txt')
    jobs = recycle_jobs.RecycleJobs(tmp_path / recycle_jobs.SPOOL_NAME, launch=lambda _: _Process())
    receipt = jobs.submit(_confirmed(paths))
    monkeypatch.setattr(recycle_jobs, '_START_WINDOW_NS', 0)
    assert receipt.terminal()['error'] == 'recycle_acceptance_expired'
    assert recycle_jobs.run_job(receipt.directory, backend=lambda: pytest.fail('Revoked job was run')) == 2
    assert paths[0].exists()


@pytest.mark.parametrize('accepted', [True, False])
def test_queue_shutdown_does_not_wait_for_helper_and_revokes_only_unaccepted(tmp_path, qapp, accepted):
    paths = _create(tmp_path / 'from', '1.txt', '2.txt')
    target = tmp_path / 'fake-bin'
    target.mkdir()
    entered, release, launch_entered = Event(), Event(), Event()
    process = _Process()
    launched = []
    spawned = []
    backend = _fake_bin(target, entered=entered, release=release)

    def launch(directory):
        launched.append(directory)
        if accepted:
            def run():
                process.code = recycle_jobs.run_job(directory, backend=backend)
            worker = Thread(target=run)
            spawned.append(worker)
            worker.start()
        launch_entered.set()
        return process

    service = FileOperationService(recycle_jobs=recycle_jobs.RecycleJobs(tmp_path / recycle_jobs.SPOOL_NAME, launch=launch))
    queue = FileOperationQueue(service=service)
    try:
        assert queue.enqueue(FileOperationRequest(1, FileOperationKind.RECYCLE, tuple(map(str, paths)),
                                                  planned_sources=_confirmed(paths)))
        _wait_until(qapp, launch_entered.is_set)
        if accepted:
            _wait_until(qapp, lambda: queue.accepted_recycle and entered.is_set())
        assert queue.accepted_recycle is accepted
        queue.begin_shutdown(cancel_active=True)
        assert queue.wait_for_done(1000)
        directory, = launched
        assert not (directory / 'cancel').exists() and process.poll() is None
        if not accepted:
            assert recycle_jobs.run_job(directory, backend=backend) == 2
    finally:
        release.set()
        queue.close()
        for worker in spawned:
            worker.join(6)
            assert not worker.is_alive()
    assert all(path.exists() is not accepted for path in paths)


def test_explicit_recycle_cancel_preserves_completed_item(tmp_path):
    paths = _create(tmp_path / 'from', '1.txt', '2.txt')
    target = tmp_path / 'fake-bin'
    target.mkdir()
    jobs = recycle_jobs.RecycleJobs(tmp_path / recycle_jobs.SPOOL_NAME, launch=lambda _: _Process())
    receipt = jobs.submit(_confirmed(paths))

    class Backend:
        def recycle(self, path, _identity):
            item = Path(path)
            item.rename(target / item.name)
            receipt.cancel()

    assert recycle_jobs.run_job(receipt.directory, backend=lambda: nullcontext(Backend())) == 0
    assert receipt.terminal()['cancelled']
    assert [(item.index, item.success) for item in receipt.updates()] == [(0, True)]
    assert paths[1].exists() and not paths[0].exists()


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


@pytest.mark.parametrize('with_viewer', [True, False])
def test_real_window_close_route_leaves_accepted_recycle_in_helper(tmp_path, qapp, monkeypatch, with_viewer):
    source, queued = _create(tmp_path / 'books', '1.txt', '2.txt')
    target = tmp_path / 'fake-bin'
    target.mkdir()
    entered, release = Event(), Event()
    process = _Process()
    workers = []
    backend = _fake_bin(target, entered=entered, release=release)

    def launch(directory):
        def run():
            process.code = recycle_jobs.run_job(directory, backend=backend)
        worker = Thread(target=run)
        workers.append(worker)
        worker.start()
        return process

    service = FileOperationService(recycle_jobs=recycle_jobs.RecycleJobs(tmp_path / recycle_jobs.SPOOL_NAME, launch=launch))
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.set('last_browser_path', str(source.parent))
    controller = ApplicationController(qapp, config_manager=config, file_operation_service=service)
    exits = []
    monkeypatch.setattr(controller, '_commit_application_exit', lambda: exits.append(True))
    monkeypatch.setattr(controller, '_ask_file_operation_close_choice',
                        lambda _: pytest.fail('Accepted recycle should not wait for the close dialog'))
    browser = controller.start(restore=False)
    viewer = controller.create_viewer_window() if with_viewer else None
    try:
        assert browser.wait_for_scan()
        assert browser._start_file_operation(FileOperationKind.RECYCLE, sources=(str(source),))
        _wait_until(qapp, lambda: entered.is_set() and controller.file_operation_queue.accepted_recycle)
        assert controller.file_operation_queue.enqueue(FileOperationRequest(
            99, FileOperationKind.RENAME, (str(queued),), new_name='queued.txt',
        ))
        assert browser.close()
        _wait_until(qapp, lambda: controller.get_browser_window() is None)
        if viewer is not None:
            assert not exits and controller.file_operation_queue.busy
            assert controller.file_operation_queue.accepted_recycle
            assert controller.file_operation_queue.queued_requests
            viewer.close()
        _wait_until(qapp, lambda: bool(exits), timeout_ms=5000)
        assert process.poll() is None and source.exists()
    finally:
        release.set()
        for worker in workers:
            worker.join(6)
        controller.shutdown()
    assert process.poll() == 0 and (target / source.name).is_file()
    assert queued.is_file()


def test_browser_close_before_helper_claim_uses_normal_close_choice(tmp_path, qapp, monkeypatch):
    source, = _create(tmp_path / 'books', '1.txt')
    launched = []

    def launch(directory):
        launched.append(directory)
        return _Process()

    service = FileOperationService(recycle_jobs=recycle_jobs.RecycleJobs(tmp_path / recycle_jobs.SPOOL_NAME, launch=launch))
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.set('last_browser_path', str(source.parent))
    controller = ApplicationController(qapp, config_manager=config, file_operation_service=service)
    controller._exit_evaluation_suspended += 1
    browser = controller.start(restore=False)
    monkeypatch.setattr(controller, '_ask_file_operation_close_choice', lambda _: FileOperationCloseChoice.KEEP_OPEN)
    try:
        assert browser.wait_for_scan()
        assert browser._start_file_operation(FileOperationKind.RECYCLE, sources=(str(source),))
        _wait_until(qapp, lambda: bool(launched))
        assert not browser.close()
        assert source.exists() and not controller.file_operation_queue.accepted_recycle
        controller.file_operation_queue.cancel()
        assert controller.file_operation_queue.wait_for_done(1000)
        assert recycle_jobs.run_job(launched[0], backend=lambda: pytest.fail('Revoked job was run')) == 2
    finally:
        controller.shutdown()


def test_frozen_launcher_passes_reset_only_to_detached_child(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.delenv('PYINSTALLER_RESET_ENVIRONMENT', raising=False)
    monkeypatch.setattr(recycle_jobs.subprocess, 'Popen', lambda *a, **k: captured.append((a, k)))
    recycle_jobs._launch(tmp_path)
    (command,), options = captured[0]
    assert command == [sys.executable, recycle_jobs.WORKER_ARGUMENT, str(tmp_path)]
    assert options['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    assert 'PYINSTALLER_RESET_ENVIRONMENT' not in os.environ
    assert options['close_fds'] and options['stdin'] == options['stdout'] == options['stderr'] == -3


def test_worker_entry_terminates_before_application_startup(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(recycle_jobs, 'run_job', lambda directory: calls.append(directory) or 27)
    main = Path(__file__).resolve().parents[1] / 'main.py'
    monkeypatch.setattr(sys, 'argv', [str(main), recycle_jobs.WORKER_ARGUMENT, str(tmp_path)])
    with pytest.raises(SystemExit) as exit:
        runpy.run_path(str(main), run_name='__main__')
    assert exit.value.code == 27 and calls == [str(tmp_path)]
