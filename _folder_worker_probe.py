from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, perf_counter, sleep
from threading import Event

from PIL import Image
from PySide6.QtWidgets import QApplication

from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.image_source import FolderImageSource
from tests.test_folder_raster_book_runtime import _request, _unit


def until(app, condition, seconds=8):
    deadline = monotonic() + seconds
    while not condition() and monotonic() < deadline:
        app.processEvents()
        sleep(.001)
    assert condition()


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as name:
    root = Path(name)
    for index in range(12):
        Image.effect_noise((750, 1000), 28).convert("RGB").save(
            root / f"{index:03}.jpg", quality=65
        )
    print("encoded_mean", sum(path.stat().st_size for path in root.glob("*.jpg")) // 12)
    for workers in (1, 2, 2, 1):
        source = FolderImageSource(root)
        runtime = FolderRasterBookRuntime(source, 1, max_active_jobs=workers)
        units = tuple(_unit(source, index) for index in range(12))
        frames = []
        runtime.frameReady.connect(frames.append)
        try:
            start = monotonic()
            assert runtime.request(_request(1, units[0], *units, direction=1))
            until(app, lambda: bool(frames))
            first_ms = round((monotonic() - start) * 1000, 2)
            assert runtime.release_continuous_warmup(request_id=1)
            warm_start = monotonic()
            peak_jobs = 0
            def complete():
                nonlocal_peak[0] = max(nonlocal_peak[0], runtime.active_job_count)
                return runtime.cached_unit_count == 12 and not runtime.has_unfinished_tasks()
            nonlocal_peak = [0]
            until(app, complete)
            warm_ms = round((monotonic() - warm_start) * 1000, 2)
            peak_jobs = nonlocal_peak[0]
            ready = runtime.cached_unit_count
            cache_mib = round(runtime.cache_bytes / 1048576, 2)

            patterns = {
                "forward": (1, 2, 3, 4),
                "reverse": (3, 2, 1, 0),
                "pingpong": (1, 2, 1, 2, 1),
                "rapid": (2, 4, 6, 8, 10),
            }
            pattern_ms = {}
            request_id = 2
            for label, targets in patterns.items():
                t = perf_counter()
                for target in targets:
                    assert runtime.request(
                        _request(request_id, units[target], *units, direction=1),
                        preserve_started_compatible=True,
                    )
                    request_id += 1
                pattern_ms[label] = round((perf_counter() - t) * 1000, 2)
            print(workers, first_ms, warm_ms, ready, peak_jobs, cache_mib, pattern_ms, flush=True)
        finally:
            until(app, lambda: not runtime.has_unfinished_tasks())
            assert runtime.shutdown(wait_msecs=3000)

    class DelayedSource(FolderImageSource):
        def __init__(self, path):
            super().__init__(path)
            self.started = {1: Event(), 2: Event()}

        def open_compatible_jpeg_at_most(self, image_id, maximum_size):
            index = int(Path(image_id).stem)
            if index in self.started:
                self.started[index].set()
                sleep(.05)
            return super().open_compatible_jpeg_at_most(image_id, maximum_size)

    for workers in (1, 2, 2, 1):
        source = DelayedSource(root)
        runtime = FolderRasterBookRuntime(source, 1, max_active_jobs=workers)
        units = tuple(_unit(source, index) for index in range(12))
        frames = []
        runtime.frameReady.connect(frames.append)
        try:
            assert runtime.request(_request(1, units[0], *units, direction=1))
            until(app, lambda: bool(frames))
            assert runtime.release_continuous_warmup(request_id=1)
            assert source.started[1].wait(1)
            if workers == 2:
                assert source.started[2].wait(1)
            start = perf_counter()
            assert runtime.request(
                _request(2, units[10], *units, direction=1),
                preserve_started_compatible=True,
            )
            until(app, lambda: frames[-1].request_id == 2)
            print("cold_busy", workers, round((perf_counter() - start) * 1000, 2),
                  "ready", runtime.cached_unit_count,
                  "peak_active", workers, flush=True)
        finally:
            until(app, lambda: not runtime.has_unfinished_tasks())
            assert runtime.shutdown(wait_msecs=3000)
