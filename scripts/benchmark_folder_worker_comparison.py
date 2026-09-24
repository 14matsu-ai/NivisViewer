"""Small synthetic folder investigation; no production settings or UI input."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter, sleep

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from PySide6.QtWidgets import QApplication
from app.image_source import FolderImageSource
from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.raster_warmup_planner import RasterBookTopology, RasterWarmupPlan
from app.zip_raster_book_runtime import (
    ZipRasterPage, ZipRasterDisplayUnit, ZipRasterRenderSpec, ZipRasterRequest,
    _UnitKey, _ZipRasterUnitJob,
)


def units_for(source):
    return tuple(ZipRasterDisplayUnit(i, (ZipRasterPage(i, name, (750, 1000)),), True)
                 for i, name in enumerate(source.list_images()))


def throughput(root):
    results = []
    for extension in ('jpg', 'png', 'webp'):
        folder = root / extension
        folder.mkdir()
        for i in range(16):
            with Image.effect_noise((750, 1000), 24).convert('RGB') as im:
                im.save(folder / f'{i:03}.{extension}', **({'quality': 65} if extension != 'png' else {}))
        source = FolderImageSource(folder)
        units = units_for(source)
        spec = ZipRasterRenderSpec((960, 720), decoder_maximum_size=(960, 720), decoder_layout_sized=True)
        samples = {1: [], 2: []}
        for workers in (1, 2, 2, 1, 1, 2, 2, 1):
            jobs = [_ZipRasterUnitJob(serial=i, key=_UnitKey(1, id(source), unit.identity, True, spec),
                                     request_id=i, source=source, unit=unit)
                    for i, unit in enumerate(units)]
            def render(job):
                pages = job._render_unit()
                assert pages and all(p.error is None and p.display_qimage is not None for p in pages)
                return sum(p.display_qimage.sizeInBytes() for p in pages)
            begin = perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                rendered_bytes = sum(pool.map(render, jobs))
            samples[workers].append(round((perf_counter() - begin) * 1000, 2))
        results.append({'format': extension, 'pages': len(units),
                        'encoded_mean_bytes': sum(p.stat().st_size for p in folder.iterdir()) // len(units),
                        'display_bytes': rendered_bytes, 'milliseconds': samples,
                        'median_ms': {k: median(v) for k, v in samples.items()}})
        source.close()
    return results


def cancellation(root, app, mode, delay, interval):
    class DelayedSource(FolderImageSource):
        def open_image(self, name):
            sleep(delay)
            return super().open_image(name)
    source = DelayedSource(root / 'png')
    runtime = FolderRasterBookRuntime(source, 1)
    units = units_for(source)
    topology = RasterBookTopology(units, identity_of=lambda u: u.identity,
                                  page_indexes_of=lambda u: (p.page_index for p in u.pages), page_count=len(units))
    spec = ZipRasterRenderSpec((960, 720))
    if mode == 'finish_started':
        original_cancel = runtime._cancel_active_job
        def preserve_compatible_started():
            active = runtime._active_job
            request = runtime._current_request
            if (active is not None and request is not None and active.started.is_set()
                    and not active.cancelled.is_set()
                    and runtime._active_job_is_artifact_compatible(active, request)):
                return
            original_cancel()
        # Experiment-only override. Restore before shutdown.
        runtime._cancel_active_job = preserve_compatible_started
    commits = []
    runtime.frameReady.connect(lambda frame: commits.append(frame.unit.start_index))
    start = perf_counter()
    next_index = 0
    final_sent = None
    while perf_counter() - start < 5:
        now = perf_counter()
        if next_index < 8 and now - start >= next_index * interval:
            unit = units[next_index]
            plan = RasterWarmupPlan(topology, current=unit, identity_of=lambda u: u.identity,
                                    page_indexes_of=lambda u: (p.page_index for p in u.pages),
                                    direction=1, background_enabled=False)
            runtime.request(ZipRasterRequest(1, next_index + 1, unit, plan, spec, navigation_direction=1))
            if next_index == 7:
                final_sent = now
            next_index += 1
        app.processEvents()
        if commits and commits[-1] == 7:
            break
        sleep(.001)
    assert commits and commits[-1] == 7, commits
    result = {'mode': mode, 'added_read_delay_ms': delay * 1000,
              'input_interval_ms': interval * 1000, 'commits': commits,
              'final_tail_ms': round((perf_counter() - final_sent) * 1000, 2),
              'ready_pages': list(runtime.cached_page_indexes),
              'cancel_requests': runtime.metrics.cancel_requests,
              'jobs_submitted': runtime.metrics.jobs_submitted}
    if mode == 'finish_started':
        runtime._cancel_active_job = original_cancel
    runtime.shutdown()
    app.processEvents()
    source.close()
    return result


if __name__ == '__main__':
    app = QApplication([])
    with TemporaryDirectory(prefix='nivis-small-worker-') as tmp:
        root = Path(tmp)
        report = {'scope': 'decode+resize throughput; warm OS cache; no QPixmap upload/native paint; artificial read delays for cancellation',
                  'throughput': throughput(root), 'cancellation': []}
        for interval in (.020, .040):
            for mode in ('cancel', 'finish_started'):
                report['cancellation'].append(cancellation(root, app, mode, .025, interval))
        print(json.dumps(report, indent=2))
