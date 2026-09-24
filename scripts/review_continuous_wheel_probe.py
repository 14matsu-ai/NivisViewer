"""Reuse the small synthetic wheel probe with coordinator/capacity snapshots."""
import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
code = (root / '_wheel_trace_probe.py').read_text(encoding='utf-8')
code = code.replace('from app.book_session import BookSession',
    'from app.book_session import BookSession\nfrom app.image_work_coordinator import ImageWorkCoordinator')
code = code.replace('session = BookSession(source_factory=lambda *_a, **_k: (source, None))',
    'coordinator = ImageWorkCoordinator()\n    session = BookSession(source_factory=lambda *_a, **_k: (source, None), image_work_coordinator=coordinator, folder_worker_limit=' + str(int(sys.argv[1])) + ')')
code = code.replace('events = []', 'events = []\n            samples = []')
code = code.replace('QTest.qWait(600)',
    'drain_until = monotonic() + .6\n            while monotonic() < drain_until:\n                app.processEvents()\n                sleep(.001)')
code = code.replace('window.next_page(input_kind=NavigationInputKind.WHEEL)',
    'window.next_page(input_kind=NavigationInputKind.WHEEL)\n                samples.append((i, runtime.cached_unit_count, runtime.active_job_count, len(source.ends), runtime._dispatch_suspended, runtime.cache_debug_values()["inflight_reservation_bytes"]))')
code = code.replace('print(mode, "events", events, "starts", source.starts, "ends", source.ends,',
    'print(mode, "samples(index,ready,active,completed,suspended,reserved)", samples,')
code += '\nassert coordinator.shutdown(wait_msecs=3000)\n'
exec(compile(code, str(root / '_wheel_trace_probe.py'), 'exec'))
