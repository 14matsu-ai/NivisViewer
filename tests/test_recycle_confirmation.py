from pathlib import Path
from threading import Event, current_thread

from PIL import Image
from PySide6.QtWidgets import QMessageBox
import pytest
from shiboken6 import isValid

from app import recycle_selection, windows_recycle_bin
from app.application_controller import ApplicationController, FileOperationCloseChoice
from app.config_manager import ConfigManager
from app.file_operation_queue import FileOperationQueue, FileOperationQueueState
from app.file_operation_service import FileOperationKind, FileOperationRequest, FileOperationService
from tests.test_browser_file_operations import (
    MovingRecycleBin, close_window, finish_operation, make_window,
    select_paths,
)
from tests.test_browser_shell_operations import _confirmed, _create
from tests.test_zip_raster_viewer_integration import _wait_until


def _replace_file(path):
    path.rename(path.with_suffix('.original'))
    path.write_text('different file at the same path', encoding='utf-8')


def _finish_controller_shutdown(controller, qapp):
    # Shutdown can return False while a queued completion or retired Viewer
    # still needs Qt events. Let those owners drain before the test gives up
    # the event loop; PDFium is stopped in a later shutdown phase.
    _wait_until(qapp, controller.shutdown, timeout_ms=5000)
    # The offscreen tests shut down the controller without exiting the shared
    # QApplication. Close its windows so they cannot intercept later input.
    controller._exit_evaluation_suspended += 1
    try:
        for window in (*controller.viewer_windows, controller.get_browser_window()):
            if window is not None and isValid(window):
                assert window.close()
    finally:
        controller._exit_evaluation_suspended -= 1
    qapp.processEvents()


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
    adapter = MovingRecycleBin(target)
    controller, window = _controller(tmp_path, qapp, FileOperationService(adapter), folder, viewer=viewer)
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
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: QMessageBox.StandardButton.Ok)
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
        assert not adapter.paths and not list(target.iterdir())
        assert source.read_text(encoding='utf-8') == 'different file at the same path'
        assert not (tmp_path / 'recycle-jobs').exists()
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


class _ControlledRecycleBin(MovingRecycleBin):
    def __init__(self, trash, blocked_source):
        super().__init__(trash)
        self.blocked_source = str(blocked_source)
        self.entered = Event()
        self.release = Event()
        self.thread = None

    def recycle(self, path):
        if path == self.blocked_source:
            self.thread = current_thread()
            self.entered.set()
            assert self.release.wait(5)
        return super().recycle(path)

    def finish(self):
        self.release.set()
        if self.thread is not None:
            self.thread.join(timeout=3)
            assert not self.thread.is_alive()


def _recycle_request(paths):
    return FileOperationRequest(1, FileOperationKind.RECYCLE, tuple(map(str, paths)),
                                planned_sources=_confirmed(paths))


def test_cancel_preserves_progress_and_stops_before_the_next_shell_call(tmp_path, qapp, monkeypatch):
    paths = _create(tmp_path / 'books', '1.txt', '2.txt', '3.txt')
    adapter = _ControlledRecycleBin(tmp_path / 'bin', paths[1])
    controller, browser = _controller(tmp_path, qapp, FileOperationService(adapter), paths[0].parent)
    queue = controller.file_operation_queue
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
    try:
        select_paths(browser, paths)
        assert browser.move_selected_to_recycle_bin()
        _wait_until(qapp, adapter.entered.is_set)
        _wait_until(qapp, lambda: browser.item_model.row_for_path(paths[0]) == -1)
        assert browser.item_model.row_for_path(paths[1]) != -1
        paths[0].write_text('replacement after progress', encoding='utf-8')
        browser.cancel_file_operation()
        assert not queue.wait_for_done(50)
        conflicting_move = FileOperationRequest(
            2, FileOperationKind.MOVE, (str(paths[1]),), str(tmp_path),
        )
        assert not queue.enqueue(conflicting_move)
        adapter.release.set()
        assert queue.wait_for_done(3000)
        result = queue.history[-1]
        assert result.cancelled and len(result.successes) == 2
        assert not result.successes[0].source_removed
        assert paths[0].read_text(encoding='utf-8') == 'replacement after progress'
        assert adapter.paths == list(map(str, paths[:2]))
        assert paths[2].exists()
        assert not (tmp_path / 'recycle-jobs').exists()
    finally:
        adapter.finish()
        _finish_controller_shutdown(controller, qapp)


@pytest.mark.parametrize('last_window', ['browser', 'viewer'])
def test_last_window_close_waits_at_most_the_shutdown_grace(
    tmp_path, qapp, monkeypatch, last_window,
):
    folder = tmp_path / 'books'
    folder.mkdir()
    first, second = (folder / name for name in ('1.png', '2.png'))
    for path in (first, second):
        Image.new('RGB', (24, 36), 'navy').save(path)
    adapter = _ControlledRecycleBin(tmp_path / 'bin', first)
    viewer = last_window == 'viewer'
    controller, window = _controller(tmp_path, qapp, FileOperationService(adapter), folder, viewer=viewer)
    queue = controller.file_operation_queue
    begin_shutdown = queue.begin_shutdown
    monkeypatch.setattr(queue, 'begin_shutdown', lambda **kw: begin_shutdown(timeout_msecs=60, **kw))
    exit_requested = []
    monkeypatch.setattr(controller, '_commit_application_exit', lambda: exit_requested.append(True))
    choices = []

    def choose(owner, *, allow_finish):
        assert owner is window and not allow_finish
        choices.append(True)
        return (FileOperationCloseChoice.KEEP_OPEN if len(choices) == 1
                else FileOperationCloseChoice.CANCEL_AND_CLOSE)

    monkeypatch.setattr(controller, '_ask_file_operation_close_choice', choose)
    try:
        if viewer:
            assert controller.get_browser_window().close()
        assert queue.enqueue(_recycle_request((first, second)))
        _wait_until(qapp, adapter.entered.is_set)
        assert not window.close()
        assert not controller._quit_requested and queue.lifecycle is FileOperationQueueState.RUNNING
        assert not window.close()
        _wait_until(qapp, lambda: controller._shutdown_complete and exit_requested)
        assert choices == [True, True]
        assert queue.lifecycle is FileOperationQueueState.STOPPED
        assert adapter.thread.daemon and adapter.thread.is_alive()
        assert not queue.enqueue(FileOperationRequest(2, FileOperationKind.RENAME, (str(second),), new_name='other'))
        adapter.finish()
        qapp.processEvents()
        assert adapter.paths == [str(first)] and second.exists()
        assert not queue.history
        assert not (tmp_path / 'recycle-jobs').exists()
    finally:
        adapter.finish()
        _finish_controller_shutdown(controller, qapp)


def test_other_window_keeps_queue_running_and_old_records_untouched(tmp_path, qapp, monkeypatch):
    paths = _create(tmp_path / 'books', '1.txt', '2.txt')
    old_record, = _create(tmp_path / 'recycle-jobs' / 'old', 'request.json')
    adapter = _ControlledRecycleBin(tmp_path / 'bin', paths[0])
    controller, browser = _controller(tmp_path, qapp, FileOperationService(adapter), paths[0].parent)
    queue = controller.file_operation_queue
    monkeypatch.setattr(
        controller, '_ask_file_operation_close_choice',
        lambda *_args, **_kwargs: pytest.fail('An open Viewer can keep the recycle queue active'),
    )
    try:
        viewer = controller.create_viewer_window()
        assert queue.enqueue(_recycle_request(paths))
        _wait_until(qapp, adapter.entered.is_set)
        assert browser.close()
        assert controller.get_active_viewer() is viewer
        assert queue.lifecycle is FileOperationQueueState.RUNNING
        adapter.release.set()
        assert queue.wait_for_done(3000)
        assert len(queue.history[-1].successes) == 2
        assert old_record.read_text(encoding='utf-8') == 'request.json'
        assert list((tmp_path / 'recycle-jobs').glob('**/*')) == [old_record.parent, old_record]
    finally:
        adapter.finish()
        _finish_controller_shutdown(controller, qapp)


@pytest.mark.parametrize('failure', ['unsupported', 'shell_cancelled', 'replacement_after_call'])
def test_queue_uses_recycle_only_and_reports_actual_outcomes(tmp_path, qapp, monkeypatch, failure):
    paths = _create(tmp_path / 'books', '1 日本語.txt', '2.txt')
    trash = tmp_path / 'bin'
    trash.mkdir()
    calls = []

    class WindowsAdapter(windows_recycle_bin.WindowsRecycleBin):
        available = True

        def recycle(self, _path):
            pytest.fail('Confirmed recycle must use the recycle-only adapter')

    class Shell:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        def recycle(self, path, identity, *, cancelled):
            assert identity.still_at(path) and not cancelled.is_set()
            source = Path(path)
            calls.append(source)
            if source == paths[0]:
                if failure == 'unsupported':
                    raise OSError('recycle_not_supported')
                if failure == 'shell_cancelled':
                    raise InterruptedError('recycle_cancelled')
            source.rename(trash / source.name)
            if source == paths[0]:
                source.write_text('replacement', encoding='utf-8')

    monkeypatch.setattr(windows_recycle_bin, 'RecycleOnly', Shell)
    queue = FileOperationQueue(service=FileOperationService(WindowsAdapter()))
    try:
        progress = []
        queue.operation_progress.connect(progress.append)
        assert queue.enqueue(_recycle_request(paths))
        assert queue.wait_for_done()
        result = queue.history[-1]
        assert result.cancelled is (failure == 'shell_cancelled')
        assert paths[0].exists()
        if failure == 'shell_cancelled':
            assert calls == [paths[0]] and paths[1].exists()
        else:
            assert calls == list(paths) and not paths[1].exists()
            assert progress[-1].removed_sources == (str(paths[1]),)
        assert not progress[0].removed_sources
        if failure == 'replacement_after_call':
            assert paths[0].read_text(encoding='utf-8') == 'replacement'
            assert result.items[0].success and not result.items[0].source_removed
        else:
            assert len(result.failures) == 1
        assert not (tmp_path / 'recycle-jobs').exists()
    finally:
        queue.close()
