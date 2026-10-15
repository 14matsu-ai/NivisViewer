"""Bounded diagnostics observe real offscreen wheel/paint ordering."""
import json
from threading import Event, get_ident

from PySide6.QtCore import QStringListModel, QCoreApplication, QEvent

from app.browser_scroll_trace import BrowserScrollTrace
from app.explorer_list_view import ExplorerListView
from tests.test_browser_wheel_scroll import _wheel_event


def test_scroll_trace_is_bounded_and_writes_off_gui_thread(tmp_path, qapp, monkeypatch):
    view = ExplorerListView()
    model = QStringListModel([str(row) for row in range(100)], view)
    view.setModel(model)
    view.resize(300, 200)
    view.show(); qapp.processEvents()
    completed, outputs = Event(), []
    gui_thread = get_ident()
    def write(path, payload):
        outputs.append((get_ident(), payload))
        completed.set()
    monkeypatch.setattr(BrowserScrollTrace, '_write', staticmethod(write))
    trace = BrowserScrollTrace(view, lambda: {'offset': view.verticalScrollBar().value()}, tmp_path)
    view._scroll_trace = trace
    try:
        for _ in range(6):
            qapp.sendEvent(view.viewport(), _wheel_event(view.viewport(), angle_y=-120))
            qapp.processEvents()
        trace.flush()
        assert completed.wait(2)
        assert outputs[0][0] != gui_thread
        events = outputs[0][1]['events']
        names = [entry['event'] for entry in events]
        assert names.count('wheel.begin') == names.count('wheel.end') == 6
        assert 'exposure.begin' in names and 'exposure.end' in names
        assert 'paint.begin' in names and 'paint.end' in names
        assert not trace._active and not trace._probe.isActive()
        count = len(trace._events)
        qapp.sendEvent(view.viewport(), _wheel_event(view.viewport(), angle_y=-120))
        assert trace._wheel == 6 and len(trace._events) == count
        assert trace._events.maxlen == 256
    finally:
        view.close()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(view, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def test_scroll_trace_file_contains_counters_and_source_hashes_only(tmp_path):
    path = tmp_path/'trace.json'
    BrowserScrollTrace._write(path, {'schema': 1, 'events': [], 'wheels': 2})
    text = path.read_text(encoding='utf-8')
    data = json.loads(text)
    assert data['wheels'] == 2
    assert all(len(value) == 64 for value in data['source_sha256'].values())
    assert str(tmp_path) not in text
    assert all('/' not in name and '\\' not in name for name in data['source_sha256'])
