"""Review regression: replacing an unstarted decode releases reserved bytes."""
from PIL import Image

from app.folder_raster_book_runtime import FolderRasterBookRuntime
from app.image_source import FolderImageSource
from app.zip_raster_book_runtime import (
    ZipRasterPage, ZipRasterDisplayUnit, ZipRasterRenderSpec, _ZipRasterUnitJob,
)


def test_replaced_unstarted_folder_job_does_not_leave_memory_reserved(tmp_path, qapp, monkeypatch):
    with Image.new('RGB', (40, 60)) as image:
        image.save(tmp_path / '0.png')
    source = FolderImageSource(tmp_path)
    runtime = FolderRasterBookRuntime(source, 1, max_active_jobs=2)
    unit = ZipRasterDisplayUnit(0, (ZipRasterPage(0, source.list_images()[0], (40, 60)),), True)
    key = runtime._key_for(unit, ZipRasterRenderSpec((100, 100)))
    job = _ZipRasterUnitJob(serial=1, key=key, request_id=1, source=source, unit=unit)
    runtime._active_job = job
    runtime._jobs.add(job)
    runtime._inflight_reservations[job] = 12345
    monkeypatch.setattr(runtime, '_try_take', lambda candidate: candidate is job)
    try:
        assert runtime._take_unstarted_job(job)
        assert not runtime.has_unfinished_tasks()
        assert runtime.cache_debug_values()['inflight_reservation_bytes'] == 0
    finally:
        runtime.shutdown()
        source.close()
