from pathlib import Path
import os
from threading import Event
from time import time_ns

from PIL import Image
from PySide6.QtWidgets import QMessageBox
import pytest
from shiboken6 import isValid

from app import recycle_jobs, recycle_selection
from app.application_controller import ApplicationController, FileOperationCloseChoice
from app.config_manager import ConfigManager
from app.file_operation_service import FileOperationKind, FileOperationRequest, FileOperationService
from tests.test_browser_file_operations import (
    MovingRecycleBin, close_window, finish_operation, make_window,
    select_paths, wait_for_recycle_selection,
)
from tests.test_browser_shell_operations import _Process, _confirmed, _create, _fake_bin
from tests.test_zip_raster_viewer_integration import _wait_until


def _replace_file(path):
    path.rename(path.with_suffix('.original'))
    path.write_text('different file at the same path', encoding='utf-8')


def _finish_controller_shutdown(controller, qapp):
    # Shutdown can return False while a queued completion or retired Viewer
    # still needs Qt events. Let those owners drain before the test gives up
    # the event loop; PDFium is stopped in a later shutdown phase.
    _wait_until(qapp, controller.shutdown, timeout_ms=5000)


def _controller(tmp_path, qapp, service, folder, *, viewer=False):
    config = ConfigManager(tmp_path / 'config.json')
    config.load()
    config.apply({'last_browser_path': str(folder), 'viewer_delete_mode': 'single'}, save=True)
    controller = ApplicationController(qapp, config_manager=config, file_operation_service=service)
    try:
        browser = controller.start(restore=False)
        browser_folder = folder
        if viewer:
            browser_folder = tmp_path / 'empty-browser'
            browser_folder.mkdir()
        assert browser.set_current_folder(browser_folder)
        assert browser.wait_for_scan()
        if not viewer:
            return controller, browser
        window = controller.create_viewer_window()
        opened = window.book_session.open_book(folder)
        assert window._finish_opened_book(opened, modal_on_empty=False)
        _wait_until(qapp, lambda: window.presentation_state.displayed is not None)
        runtime = window.book_session.viewer_runtime
        if runtime is not None:
            _wait_until(qapp, lambda: not runtime.has_unfinished_tasks())
        return controller, window
    except BaseException:
        _finish_controller_shutdown(controller, qapp)
        raise


@pytest.mark.parametrize('viewer', [False, True])
@pytest.mark.parametrize('stage', ['question', 'queue'])
def test_confirmed_identity_survives_dialog_and_delayed_queue(tmp_path, qapp, monkeypatch, viewer, stage):
    folder = tmp_path / 'books'
    folder.mkdir()
    source = folder / ('sample.png' if viewer else 'sample.txt')
    if viewer:
        Image.new('RGB', (24, 36), 'navy').save(source)
    else:
        source.write_text('original file', encoding='utf-8')
    target = tmp_path / 'fake-bin'
    target.mkdir()
    handled = []

    def launch(directory):
        process = _Process()
        handled.append(directory)
        process.code = recycle_jobs.run_job(directory, backend=_fake_bin(target))
        return process

    jobs = recycle_jobs.RecycleJobs(tmp_path / recycle_jobs.SPOOL_NAME, launch=launch)
    controller, window = _controller(tmp_path, qapp, FileOperationService(recycle_jobs=jobs), folder, viewer=viewer)
    queue = controller.file_operation_queue
    ready = Event()
    monkeypatch.setattr(queue, '_execution_ready', ready.is_set)
    original = _confirmed((source,))
    captures = []
    capture = recycle_selection.capture_recycle_sources

    def record(paths, cancelled):
        sources = capture(paths, cancelled)
        captures.append(sources)
        return sources

    def confirm(*_args):
        assert captures == [original]
        if stage == 'question':
            _replace_file(source)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(recycle_selection, 'capture_recycle_sources', record)
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    try:
        if viewer:
            assert window.delete_current_image_to_recycle_bin()
        else:
            select_paths(window, [source])
            assert window.move_selected_to_recycle_bin()
        _wait_until(qapp, lambda: queue.active_operation is not None and not window._recycle_selection.pending)
        assert queue.active_operation.planned_sources == original
        if stage == 'queue':
            _replace_file(source)
        ready.set()
        assert queue.wait_for_done(3000)
        outcome = queue.history[-1]
        assert len(outcome.failures) == 1 and not outcome.successes
        assert len(handled) == 1 and not list(target.iterdir())
        assert source.read_text(encoding='utf-8') == 'different file at the same path'
    finally:
        _finish_controller_shutdown(controller, qapp)


def test_compatibility_executor_checks_same_identity_after_dialog(tmp_path, qapp, monkeypatch):
    source, = _create(tmp_path / 'books', 'example.txt')
    adapter = MovingRecycleBin(tmp_path / 'bin')
    window, coordinator = make_window(tmp_path, qapp, source.parent, recycle_bin=adapter)
    select_paths(window, [source])

    def confirm(*_args):
        _replace_file(source)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, 'question', confirm)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: QMessageBox.StandardButton.Ok)
    completed = []
    coordinator.operation_completed.connect(completed.append)
    try:
        assert window.move_selected_to_recycle_bin()
        finish_operation(window, coordinator, qapp)
        assert not adapter.paths and len(completed[-1].failures) == 1
        assert source.exists()
    finally:
        close_window(window, coordinator, qapp)


def test_missing_selection_is_counted_without_confirmation_or_recycle(tmp_path, qapp, monkeypatch):
    source, = _create(tmp_path / 'books', 'example.txt')
    adapter = MovingRecycleBin(tmp_path / 'bin')
    window, coordinator = make_window(tmp_path, qapp, source.parent, recycle_bin=adapter)
    select_paths(window, [source])
    source.unlink()
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: pytest.fail('No identifiable item to confirm'))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: QMessageBox.StandardButton.Ok)
    completed = []
    coordinator.operation_completed.connect(completed.append)
    try:
        assert window.move_selected_to_recycle_bin()
        finish_operation(window, coordinator, qapp)
        assert len(completed[-1].failures) == 1 and adapter.paths == []
    finally:
        close_window(window, coordinator, qapp)


@pytest.mark.parametrize('invalidate', ['close', 'selection', 'cancel'])
def test_capture_blocked_on_io_is_disowned_without_a_prompt(tmp_path, qapp, monkeypatch, invalidate):
    first, second = _create(tmp_path / 'books', '1.txt', '2.txt')
    adapter = MovingRecycleBin(tmp_path / 'bin')
    window, coordinator = make_window(tmp_path, qapp, first.parent, recycle_bin=adapter)
    select_paths(window, [first])
    entered, release, returned = Event(), Event(), Event()
    capture = recycle_selection.capture_recycle_sources
    questions = []

    def blocked(paths, cancelled):
        from threading import current_thread
        assert current_thread().daemon
        entered.set()
        assert release.wait(5)
        try:
            return capture(paths, cancelled)
        finally:
            returned.set()

    monkeypatch.setattr(recycle_selection, 'capture_recycle_sources', blocked)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: questions.append(True) or QMessageBox.StandardButton.Yes)
    try:
        assert window.move_selected_to_recycle_bin() and entered.wait(1)
        if invalidate == 'close':
            assert window.close()
        elif invalidate == 'selection':
            select_paths(window, [second])
        else:
            window.cancel_file_operation()
        assert not window._recycle_selection.pending and not returned.is_set()
        release.set()
        _wait_until(qapp, returned.is_set)
        qapp.processEvents()
        assert not questions and not adapter.paths
    finally:
        release.set()
        if isValid(window):
            close_window(window, coordinator, qapp)
        else:
            coordinator.close()
            coordinator.metadata_store.close()


def test_viewer_book_switch_discards_capture_that_has_not_returned(tmp_path, qapp, monkeypatch):
    folders = (tmp_path / 'one', tmp_path / 'two')
    for folder in folders:
        folder.mkdir()
        Image.new('RGB', (24, 36), 'navy').save(folder / 'sample.png')
    adapter = MovingRecycleBin(tmp_path / 'bin')
    controller, viewer = _controller(tmp_path, qapp, FileOperationService(adapter), folders[0], viewer=True)
    entered, release, finished = Event(), Event(), Event()
    capture = recycle_selection.capture_recycle_sources
    questions = []

    def blocked(paths, cancelled):
        entered.set()
        assert release.wait(5)
        rows = capture(paths, cancelled)
        finished.set()
        return rows

    monkeypatch.setattr(recycle_selection, 'capture_recycle_sources', blocked)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: questions.append(True) or QMessageBox.StandardButton.Yes)
    try:
        assert viewer.delete_current_image_to_recycle_bin() and entered.wait(1)
        assert viewer.open_path(folders[1])
        assert not viewer._recycle_selection.pending
        release.set()
        _wait_until(qapp, finished.is_set)
        assert viewer.book_session.wait_for_async(5000)
        assert not questions and not adapter.paths and not controller.file_operation_queue.busy
    finally:
        release.set()
        _finish_controller_shutdown(controller, qapp)


def test_inactivity_releases_monitor_keeps_success_and_blocks_parent_until_late_result(tmp_path, qapp, monkeypatch):
    paths = _create(tmp_path / 'books', '1.txt', '2.txt')
    clock = [100]
    alive = [True]
    monkeypatch.setattr(recycle_jobs.time, 'monotonic_ns', lambda: clock[0])
    monkeypatch.setattr(recycle_jobs, '_process_stamp', lambda _: 99 if alive[0] else None)
    receipts = []

    def launch(directory):
        receipts.append(directory)
        (directory / 'claimed').mkdir()
        recycle_jobs._write(directory, 'accepted.json', {'pid': 1234, 'stamp': 99})
        paths[0].rename(tmp_path / 'recycled-first')
        (directory / 'outcomes.jsonl').write_text('{"index":0,"success":true}\n', encoding='ascii')
        return _Process()

    def progress(observation):
        assert observation.removed_sources == (str(paths[0]),)
        # A new file appears after the progress receipt but before completion.
        paths[0].write_text('replacement', encoding='utf-8')
        clock[0] += recycle_jobs._NO_PROGRESS_NS

    jobs = recycle_jobs.RecycleJobs(tmp_path / recycle_jobs.SPOOL_NAME, launch=launch)
    service = FileOperationService(recycle_jobs=jobs)
    request = FileOperationRequest(1, FileOperationKind.RECYCLE, tuple(map(str, paths)), planned_sources=_confirmed(paths))
    lexists = os.path.lexists

    def reject_stalled_path(path):
        if clock[0] > 100 and Path(path) in paths:
            pytest.fail('The monitor probed a filesystem path after the deadline')
        return lexists(path)

    with monkeypatch.context() as patch:
        patch.setattr(os.path, 'lexists', reject_stalled_path)
        result = service.execute_detached_recycle(request, Event(), Event(), Event(), progress)
    directory, = receipts
    assert result.recycle_unconfirmed and len(result.successes) == len(result.failures) == 1
    assert result.failures[0].error_code == 'recycle_result_unconfirmed'
    assert not result.successes[0].source_removed and paths[0].read_text(encoding='utf-8') == 'replacement'
    assert not (directory / 'cancel').exists() and not (directory / 'observed').exists()
    from app.file_operation_queue import FileOperationQueue
    queue = FileOperationQueue(service=service)
    try:
        assert queue.enqueue(FileOperationRequest(2, FileOperationKind.RENAME, (str(paths[0].parent),), new_name='other'))
        assert queue.wait_for_done(2000)
        assert queue.history[-1].failures[0].error_code == 'in_use'
        assert paths[0].parent.is_dir()
    finally:
        queue.close()

    # The same accepted helper finishes later. Recovery reads the receipts once.
    paths[1].rename(tmp_path / 'recycled-second')
    with (directory / 'outcomes.jsonl').open('a', encoding='ascii') as journal:
        journal.write('{"index":1,"success":true}\n')
    recycle_jobs._write(directory, 'terminal.json', {'cancelled': False, 'error': None})
    alive[0] = False
    assert jobs.recover(created_before=0, include={str(directory)}) == (((2, 0),), False)
    assert jobs.recover(created_before=time_ns()) == ((), False)
    assert jobs.owned_paths() == () and len(receipts) == 1


def test_recovery_deadline_uses_persisted_progress_without_stopping_owner(tmp_path, monkeypatch):
    source, = _create(tmp_path / 'books', '1.txt')
    clock = [1000 * 10**9]
    monkeypatch.setattr(recycle_jobs.time, 'time_ns', lambda: clock[0])
    monkeypatch.setattr(recycle_jobs, '_process_stamp', lambda _pid: 123)
    jobs = recycle_jobs.RecycleJobs(tmp_path / recycle_jobs.SPOOL_NAME, launch=lambda _: _Process())
    directory = jobs.submit(_confirmed((source,))).directory
    created_before = clock[0] + 1
    (directory / 'claimed').mkdir()
    recycle_jobs._write(directory, 'accepted.json', {'pid': 1234, 'stamp': 123, 'accepted_ns': clock[0]})
    clock[0] += recycle_jobs._NO_PROGRESS_NS
    follow, notices = set(), []
    assert jobs.recover(created_before=created_before, follow=follow,
                        unconfirmed=lambda: notices.append(True)) == ((), False)
    assert follow == {str(directory)} and notices == [True]
    assert jobs.owned_paths() == (str(source),) and not (directory / 'cancel').exists()
    completed = '{"index":0,"success":true,"completed_ns":%d}\n' % clock[0]
    (directory / 'outcomes.jsonl').write_text(completed, encoding='ascii')
    assert jobs.recover(created_before=0, include=follow, follow=set()) == ((), True)


@pytest.mark.parametrize('choice, with_viewer, closes', [
    (FileOperationCloseChoice.CLOSE_AFTER_OPERATION, False, True),
    (FileOperationCloseChoice.CLOSE_AFTER_OPERATION, True, False),
    (FileOperationCloseChoice.KEEP_OPEN, False, False),
    (FileOperationCloseChoice.CANCEL_AND_CLOSE, False, False),
])
def test_close_choice_rechecks_matching_late_acceptance(tmp_path, qapp, monkeypatch, choice, with_viewer, closes):
    source, = _create(tmp_path / 'books', '1.txt')
    receipt = []

    def launch(directory):
        receipt.append(directory)
        return _Process()

    jobs = recycle_jobs.RecycleJobs(tmp_path / recycle_jobs.SPOOL_NAME, launch=launch)
    controller, browser = _controller(tmp_path, qapp, FileOperationService(recycle_jobs=jobs), source.parent)
    exit_requested = []
    monkeypatch.setattr(controller, '_commit_application_exit', lambda: exit_requested.append(True))
    monkeypatch.setattr(controller, '_ask_file_operation_close_choice', lambda _: choice)
    queue = controller.file_operation_queue
    try:
        if with_viewer:
            controller.create_viewer_window()
        assert browser._start_file_operation(FileOperationKind.RECYCLE, sources=(str(source),))
        _wait_until(qapp, lambda: bool(receipt))
        directory, = receipt
        # The child claimed but has not delivered the acceptance record. This
        # lets explicit cancel request the next safe boundary without revoking it.
        (directory / 'claimed').mkdir()
        assert not browser.close()
        controller._on_recycle_accepted('different-operation')
        qapp.processEvents()
        assert controller.get_browser_window() is browser
        recycle_jobs._write(directory, 'accepted.json', {'pid': 1234, 'stamp': None})
        if closes:
            _wait_until(qapp, lambda: bool(exit_requested), timeout_ms=3000)
            assert controller._shutdown_complete
            assert not (directory / 'cancel').exists()
        else:
            _wait_until(qapp, lambda: queue.accepted_recycle)
            qapp.processEvents()
            assert controller.get_browser_window() is browser and queue.busy
            assert (directory / 'cancel').exists() is (choice is FileOperationCloseChoice.CANCEL_AND_CLOSE)
        assert source.exists()
    finally:
        _finish_controller_shutdown(controller, qapp)
