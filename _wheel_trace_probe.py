from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from threading import Lock

from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.image_source import FolderImageSource
from app.viewer_navigation_policy import NavigationInputKind
from app.viewer_window import ViewerWindow


def until(app, condition):
    end = monotonic() + 5
    while not condition() and monotonic() < end:
        app.processEvents()
        sleep(.002)
    assert condition()


class Source(FolderImageSource):
    def __init__(self, path):
        super().__init__(path)
        self.delay = 0
        self.starts = []
        self.ends = []
        self.lock = Lock()

    def open_image(self, image_id):
        with self.lock:
            self.starts.append((round(monotonic() * 1000), int(Path(image_id).stem)))
        sleep(self.delay)
        result = super().open_image(image_id)
        with self.lock:
            self.ends.append((round(monotonic() * 1000), int(Path(image_id).stem)))
        return result


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as d:
    root = Path(d)
    folder = root / "book"
    folder.mkdir()
    for i in range(12):
        Image.new("RGB", (150, 220), (i*18, 90, 140)).save(folder / f"{i:03}.png")
    source = Source(folder)
    config = ConfigManager(root / "config.json")
    config.load()
    session = BookSession(source_factory=lambda *_a, **_k: (source, None))
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    window.set_view_mode("single")
    window.show()
    app.processEvents()
    try:
        assert window._finish_opened_book(session.open_book(folder), modal_on_empty=False)
        runtime = session.viewer_runtime
        until(app, lambda: runtime.cached_unit_count == 12 and not runtime.has_unfinished_tasks())
        for mode, delay in (("cached", 0), ("cold12ms", .012), ("cold25ms", .025)):
            window._go_to_index_with_history(0)
            until(app, lambda: window.presentation_state.displayed_page == 0)
            if mode != "cached":
                for frame in tuple(runtime._frame_store.values()):
                    if frame.unit.pages[0].page_index:
                        runtime._frame_store.take(frame.key)
                runtime._source_store.clear()
            source.delay = delay
            source.starts.clear()
            source.ends.clear()
            t = monotonic()
            events = []
            commits = []
            paints = []
            def on_commit(commit):
                commits.append((round((monotonic()-t)*1000), commit.frame.unit.focused_index))
            def on_paint(_serial, ids):
                paints.append((round((monotonic()-t)*1000), int(Path(ids[0]).stem)))
            window.presentationCommitted.connect(on_commit)
            window.viewer.framePainted.connect(on_paint)
            def step(i):
                events.append((round((monotonic()-t)*1000), i))
                window.viewer.wheelInputObserved.emit(1_000_000+i*20)
                window.next_page(input_kind=NavigationInputKind.WHEEL)
            for i in range(1, 10):
                QTimer.singleShot(i*20, lambda i=i: step(i))
            QTest.qWait(600)
            if mode != "cached":
                try:
                    until(app, lambda: window.presentation_state.displayed_page == 9)
                except AssertionError:
                    print("cold_final_timeout", mode, flush=True)
            print(mode, "events", events, "starts", source.starts, "ends", source.ends,
                  "commits", commits, "paints", paints,
                  "final", window.presentation_state.displayed_page,
                  "requested", window.presentation_state.requested_page,
                  "active", runtime._active_job.key.unit_identity if runtime._active_job else None,
                  "jobs", len(runtime._jobs), "suspended", runtime._dispatch_suspended,
                  "debug", runtime.cache_debug_values(),
                  "cancels", runtime.metrics.cancel_requests, flush=True)
            window.presentationCommitted.disconnect(on_commit)
            window.viewer.framePainted.disconnect(on_paint)
            until(app, lambda: not runtime.has_unfinished_tasks())
    finally:
        window.close()
        app.processEvents()
