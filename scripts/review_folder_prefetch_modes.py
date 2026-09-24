"""Synthetic, offscreen diagnostic of opening one file and idle warmup."""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtWidgets import QApplication
from app.book_session import BookSession
from app.config_manager import ConfigManager
from app.viewer_window import ViewerWindow

app = QApplication.instance() or QApplication([])
mode = sys.argv[1]
page_count = int(sys.argv[2]) if len(sys.argv) > 2 else 20
image_width = int(sys.argv[3]) if len(sys.argv) > 3 else 750
image_height = int(sys.argv[4]) if len(sys.argv) > 4 else 1000
with TemporaryDirectory() as directory:
    root = Path(directory)
    folder = root / 'images'
    folder.mkdir()
    for index in range(page_count):
        with Image.new('RGB', (image_width, image_height), (index * 10, 80, 120)) as image:
            image.save(folder / f'{index:03}.jpg')
    config = ConfigManager(root / 'settings.json')
    config.load()
    session = BookSession()
    window = ViewerWindow(config_manager=config, book_session=session)
    window.resize(640, 480)
    window.set_view_mode('single')
    window.fit_mode = mode
    window.show()
    app.processEvents()
    try:
        assert window._finish_opened_book(session.open_book(folder / '005.jpg'), modal_on_empty=False)
        start = monotonic()
        for checkpoint in (0.5, 1.0, 2.0, 4.0):
            while monotonic() - start < checkpoint:
                app.processEvents()
                sleep(.002)
            print('checkpoint', checkpoint, 'ready', session.viewer_runtime.cached_unit_count,
                  'stop', session.viewer_runtime.warmup_stop_reason)
        runtime = session.viewer_runtime
        values = runtime.cache_debug_values()
        print(mode, 'pages', window.model.total_pages, 'displayed', window.presentation_state.displayed_page,
              'background_enabled', runtime._current_request.warmup_plan.background_enabled,
              {key: values[key] for key in ('ready_page_count', 'cache_used_bytes', 'soft_target_bytes', 'warmup_stop_reason', 'active_job_count')})
    finally:
        window.close()
        app.processEvents()
