"""At-most-once handoff of confirmed recycle work to a short-lived process.

Only the GUI launches jobs. A worker claims each request once, journals outcomes
and exits. Neither startup recovery nor missing acknowledgements replay a job.
This module must stay independent of Qt and the GUI profile lock.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID, uuid4

from .file_operation_identity import FileIdentity
from .windows_file_operations import RecycleOnly


WORKER_ARGUMENT = '--recycle-job'
SPOOL_NAME = 'recycle-jobs'
_START_WINDOW_NS = 60 * 10**9
_NO_PROGRESS_NS = 120 * 10**9


def _write(directory, name, value):
    temporary = directory / f'.{name}-{uuid4().hex}.tmp'
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, directory / name)


def _read(directory, name):
    try:
        return json.loads((directory / name).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return None


def _contains_spool(path, directory):
    source = os.path.normcase(os.path.abspath(path))
    job = os.path.normcase(os.path.abspath(directory))
    return job == source or job.startswith(source.rstrip(os.sep) + os.sep)


def _process_stamp(pid):
    """PID plus creation time avoids mistaking a reused PID for our worker."""
    if os.name != 'nt':
        return None
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    process = kernel.OpenProcess(0x101000, False, pid)  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return None
    try:
        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(process, *(ctypes.byref(t) for t in (created, exited, system, user))):
            return None
        if kernel.WaitForSingleObject(process, 0) != 0x102:  # WAIT_TIMEOUT (running)
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel.CloseHandle(process)


def _launch(directory):
    command = [sys.executable]
    environment = None
    if getattr(sys, 'frozen', False):
        environment = {**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'}
    else:
        command.append(str(Path(__file__).resolve().parent.parent / 'main.py'))
    command.extend((WORKER_ARGUMENT, str(directory)))
    return subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True,
        env=environment,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


@dataclass(frozen=True)
class RecycleOutcome:
    index: int
    success: bool
    error: str | None = None
    completed_ns: int | None = None


class RecycleReceipt:
    def __init__(self, directory, process):
        self.directory = directory
        self.process = process
        self._offset = 0
        self._progress_at = time.monotonic_ns()

    def note_progress(self):
        self._progress_at = time.monotonic_ns()

    def unresponsive(self):
        return time.monotonic_ns() - self._progress_at >= _NO_PROGRESS_NS

    def updates(self):
        with (self.directory / 'outcomes.jsonl').open('rb') as stream:
            stream.seek(self._offset)
            while line := stream.readline():
                if not line.endswith(b'\n'):
                    break  # Worker may still be writing this line.
                self._offset = stream.tell()
                yield RecycleOutcome(**json.loads(line))

    def cancel(self):
        if self.revoke_before_acceptance():
            return
        (self.directory / 'cancel').touch()

    def accepted(self):
        return (self.directory / 'accepted.json').is_file()

    def revoke_before_acceptance(self, *, error=None):
        # Race the child's claim using the same atomic operation. Normal exit
        # can withdraw only a request that has never been taken by the child.
        try:
            (self.directory / 'claimed').mkdir()
        except FileExistsError:
            return False
        _write(self.directory, 'terminal.json', {'cancelled': error is None, 'error': error})
        return True

    def terminal(self):
        terminal = _read(self.directory, 'terminal.json')
        if terminal is not None:
            return terminal
        code = self.process.poll() if self.process is not None else 1
        if code is not None:
            # An exit between the first read and poll can have written a result.
            terminal = _read(self.directory, 'terminal.json')
            if terminal is not None:
                return terminal
            terminal = {
                'error': f'recycle_worker_exit ({code})', 'cancelled': False,
            }
            if self.process is not None:
                try:
                    _write(self.directory, 'terminal.json', terminal)
                except OSError:
                    pass
            return terminal
        if not self.accepted():
            request = _read(self.directory, 'request.json')
            if request and time.time_ns() - request['created_ns'] > _START_WINDOW_NS:
                self.revoke_before_acceptance(error='recycle_acceptance_expired')
                return _read(self.directory, 'terminal.json')
        return None

    def observed(self):
        (self.directory / 'observed').touch()


class RecycleJobs:
    def __init__(self, directory, *, launch=_launch):
        self.directory = Path(directory)
        self.launch = launch

    def submit(self, sources):
        directory = self.directory / uuid4().hex
        directory.mkdir(parents=True)
        (directory / 'outcomes.jsonl').touch()
        _write(directory, 'request.json', {
            'id': directory.name, 'version': 1, 'created_ns': time.time_ns(),
            'sources': [[p, asdict(identity) if identity is not None else None] for p, identity in sources],
        })
        try:
            process = self.launch(directory)
        except OSError as exc:
            receipt = RecycleReceipt(directory, None)
            receipt.revoke_before_acceptance(error=f'recycle_launch_failed: {exc}')
            return receipt
        try:
            _write(directory, 'launched.json', {'pid': process.pid, 'stamp': _process_stamp(process.pid)})
        except OSError:
            # The worker can already own the request. Never launch a second one.
            pass
        return RecycleReceipt(directory, process)

    def _pending_owner(self, directory, request):
        owner = _read(directory, 'accepted.json') or _read(directory, 'launched.json')
        if owner and owner['stamp'] is not None:
            return _process_stamp(owner['pid']) == owner['stamp']
        # An accepted owner whose start time is unavailable is uncertain.
        # Keep its conflict fence until a terminal result becomes available.
        return ((directory / 'accepted.json').exists()
                or (directory / 'claimed').exists()
                or time.time_ns() - request['created_ns'] <= _START_WINDOW_NS)

    def owned_paths(self):
        """Executed off the GUI thread before starting another filesystem operation."""
        if not self.directory.is_dir():
            return ()
        paths = []
        for directory in self.directory.iterdir():
            if not directory.is_dir() or _read(directory, 'terminal.json') is not None:
                continue
            request = _read(directory, 'request.json')
            if request is not None and self._pending_owner(directory, request):
                paths.extend(path for path, identity in request['sources'] if identity is not None)
        return tuple(paths)

    def recover(self, *, created_before, include=(), follow=None, unconfirmed=None):
        """Return newly observed (success, failed) counts and whether to poll again."""
        if not self.directory.is_dir():
            return (), False
        recovered = []
        pending = False
        for directory in self.directory.iterdir():
            if not directory.is_dir() or (directory / 'observed').exists():
                continue
            request = _read(directory, 'request.json')
            if request is None or (request['created_ns'] >= created_before and str(directory) not in include):
                continue
            terminal = _read(directory, 'terminal.json')
            if terminal is None:
                expired = time.time_ns() - request['created_ns'] > _START_WINDOW_NS
                if expired:
                    RecycleReceipt(directory, None).revoke_before_acceptance(error='recycle_acceptance_expired')
                    terminal = _read(directory, 'terminal.json')
            if terminal is None and self._pending_owner(directory, request):
                accepted = directory / 'accepted.json'
                if not accepted.exists():
                    accepted = directory / 'claimed'
                newest = max((row.completed_ns or 0 for row in RecycleReceipt(directory, None).updates()), default=0)
                accepted_record = _read(directory, 'accepted.json') or {}
                if accepted.exists():
                    newest = max(newest, accepted_record.get('accepted_ns') or accepted.stat().st_mtime_ns)
                if newest and time.time_ns() - newest >= _NO_PROGRESS_NS:
                    marker = directory / 'unconfirmed'
                    if unconfirmed is not None and not marker.exists():
                        unconfirmed()
                        marker.touch()
                    if follow is not None:
                        follow.add(str(directory))
                else:
                    pending = True
                    if follow is not None and str(directory) in include:
                        follow.add(str(directory))
                continue
            receipt = RecycleReceipt(directory, None)
            successes = {row.index for row in receipt.updates()
                         if row.success and 0 <= row.index < len(request['sources'])}
            receipt.observed()
            recovered.append((len(successes), len(request['sources']) - len(successes)))
        return tuple(recovered), pending


def read_recovery(jobs, created_before, include, mailbox):
    follow = set()
    notices = []
    try:
        completed, pending = jobs.recover(
            created_before=created_before, include=include, follow=follow,
            unconfirmed=lambda: notices.append(True),
        )
        mailbox.put((include, completed, pending, follow, bool(notices), False))
    except (OSError, ValueError, KeyError, TypeError):
        mailbox.put((include, (), False, set(include), False, True))


def run_job(directory, *, backend=RecycleOnly):
    """Worker entry point. Confirm scope, claim once, then work without a parent."""
    directory = Path(directory)
    try:
        if directory.parent.name != SPOOL_NAME or UUID(hex=directory.name).hex != directory.name:
            return 2
        request = _read(directory, 'request.json')
        if (request is None or request['id'] != directory.name or request['version'] != 1
                or (directory / 'terminal.json').exists()):
            return 2
        items = [(path, FileIdentity(**value) if value is not None else None)
                 for path, value in request['sources']]
        if not items or any(not os.path.isabs(path) for path, _ in items):
            return 2
        # Directory creation is the irreversible acceptance boundary. A crash
        # after creation does not make this request eligible for another run.
        (directory / 'claimed').mkdir()
    except (OSError, TypeError, ValueError, KeyError):
        return 2
    cancelled = False
    error = None
    try:
        if time.time_ns() - request['created_ns'] > _START_WINDOW_NS:
            raise OSError('recycle_acceptance_expired')
        _write(directory, 'accepted.json', {
            'pid': os.getpid(), 'stamp': _process_stamp(os.getpid()), 'accepted_ns': time.time_ns(),
        })
        with backend() as shell, (directory / 'outcomes.jsonl').open('a', encoding='utf-8') as journal:
            for index, (path, identity) in enumerate(items):
                if (directory / 'cancel').exists():
                    cancelled = True
                    break
                try:
                    if _contains_spool(path, directory):
                        raise OSError('recycle_job_storage_is_inside_source')
                    if identity is None or not identity.is_normal or not identity.still_at(path):
                        raise OSError('source_changed_or_unsupported')
                    shell.recycle(path, identity)
                    row = RecycleOutcome(index, True)
                except OSError as exc:
                    row = RecycleOutcome(index, False, str(exc))
                record = asdict(row)
                record['completed_ns'] = time.time_ns()
                journal.write(json.dumps(record, ensure_ascii=True) + '\n')
                # Publish to other processes immediately. Per-item fsync is
                # unnecessary for surviving the GUI process exiting.
                journal.flush()
    except Exception as exc:
        error = str(exc)
    try:
        _write(directory, 'terminal.json', {'cancelled': cancelled, 'error': error})
    except OSError:
        # The monitor can report an exit without a terminal record. Do not
        # leave a windowed bootloader traceback dialog running in the helper.
        return 1
    return 1 if error else 0
